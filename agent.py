#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Termux Agent —— 跑在安卓平板本地的轻量 AI agent

设计取舍：
  1. 零第三方依赖。只用 Python 标准库，Termux 里 `pkg install python` 即可运行。
     这是刻意的选择：不依赖任何需要编译的原生模块，因此永远不会因为上游升级而失修。
  2. 工具齐全：bash 执行、文件读写改、目录列举、内容检索、网页抓取。
  3. 直连 DeepSeek 官方 API，流式输出，思维链可选展示。
  4. 会话可持久化、可续聊；上下文超限自动裁剪（优先丢老的工具输出）。

用法见同目录 README.md
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import argparse
import base64
import getpass
import hashlib
import json
import os
import queue
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# ---- 只走 IPv4：本机所在网络没有 IPv6 出口 ----
# 不少 API 域名（如 open.bigmodel.cn）同时挂着 AAAA 记录，但这里的网络连不通 IPv6。
# Python 的 urllib 不像 curl 那样做 IPv4/IPv6 并行尝试（Happy Eyeballs），它按
# getaddrinfo 的顺序挨个连：先卡死在不可达的 IPv6 上（每个地址吃满一次 timeout，
# 智谱有 2 条 → 实测白等 50 秒），才退回 IPv4。全局只留 IPv4，一次治好所有请求。
# 只有 IPv6 时原样返回，不把路堵死。
_real_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_v4(host, port, family=0, type=0, proto=0, flags=0):
    res = _real_getaddrinfo(host, port, family, type, proto, flags)
    v4 = [r for r in res if r[0] == socket.AF_INET]
    return v4 or res


socket.getaddrinfo = _getaddrinfo_v4

VERSION = "1.7.4"
# 本进程的启动标识：服务重启后会变，前端据此判断"我手上的页面代码过期了"并自动重载
BOOT_ID = datetime.now().strftime("%Y%m%d%H%M%S")
APP_DIR = Path.home() / ".termux-agent"
CONFIG_PATH = APP_DIR / "config.json"
SESSION_PATH = APP_DIR / "last-session.json"      # 兼容旧版；新会话存 sessions/ 目录
SESSIONS_DIR = APP_DIR / "sessions"
MEMORY_PATH = APP_DIR / "memory.md"
HISTORY_PATH = APP_DIR / "history.txt"

MAX_TOOL_OUTPUT = 30000        # 单个工具输出最大字符数，超出截断

# 单条工具输出的当前生效上限。2026-10-01：由固定 3 万改为按上下文压力收缩 ——
# 3 万字符约 1.5~2 万 token，比 max_tokens 还大，几轮就能把上下文顶满；
# 而超长输出本来就整份落盘（_truncate 会给出路径），缩小预览不丢可查性。
# 见 refresh_tool_cap()。
TOOL_CAP = [MAX_TOOL_OUTPUT]
# 同轮里可以并行跑的只读工具（对照 codex 的 parallel tool calls）：
# 它们不写本地状态、互不依赖，一起发出去能省掉往返等待。
PARALLEL_TOOLS = {"read_file", "list_dir", "grep", "fetch_url", "web_search"}
PARALLEL_TIMEOUT = 180         # 并行工具单个最长等待秒数，超时按错误返回
PARALLEL_MAX_WORKERS = 4
REASONING_KEEP = 4000          # 每轮思考内容最多回传这么多字符（服务端要求回传）
# 命令边跑边把输出尾部推给界面（对照 codex 的实时输出），并让「停止」能真杀掉进程
STREAM_TICK = 0.5              # 至少间隔这么久推一次，避免刷屏
TAIL_MAX_LINES = 12            # 每次推送的尾部行数
TAIL_MAX_CHARS = 1400
MAX_TOOL_ROUNDS = 200          # 单轮任务里「模型→工具」最多来回几次（可在设置页调）
DEFAULT_MAX_TOKENS = 8192      # 输出上限；推理模型会把思维链算在里面，不能给小
PRUNE_AT_CHARS = 180000        # 兜底裁剪阈值（压缩失败时退回裁剪）
CHARS_PER_TOKEN = 1.5          # 粗估：1 token ≈ 1.5 个汉字（偏保守，宁早压不撞上限）
COMPRESS_RATIO = 0.7           # 默认用到模型窗口的 70% 就自动压缩
COMPRESS_AT_CHARS = 60000      # 拿不到窗口信息时的兜底阈值（字符）
# 上下文预算（token）。标称窗口（deepseek 系写着 1M）不等于「该用满的窗口」：
# 每轮塞几十万 token 既慢又贵，检索质量还会掉。压缩阈值按这个预算收口。
# 可用配置项 ctx_budget_tokens 覆盖。
CTX_BUDGET_TOKENS = 200000
PRUNE_RATIO = 1.2              # 兜底裁剪 = 压缩阈值的这个倍数，压缩彻底失败时才动手
DEFAULT_WINDOW_TOKENS = 128000  # 连兜底表都查不到时按 128K 处理
MODEL_CONTEXT_TOKENS = {       # 离线兜底表（真实值优先从服务端 /models 拉，见 refresh_model_windows）
    "deepseek-flash": 1048576,     # 官方在售模型：轻快版
    "deepseek-v4-pro": 1048576,    # 官方在售模型：强力版
    # 智谱 GLM（其 /models 不返回 context_window，只能写死在这里）
    "glm-5.3": 1048576,            # 100 万上下文
    "glm-5.3-flash": 1048576,
    "glm-5.3-flashx": 1048576,
}

# 服务端 /models 不报 context_window 时（llama.cpp、ollama 这类本地后端大多如此），
# 就按模型名里的「家族」推断窗口 —— 比一律按 DEFAULT_WINDOW_TOKENS 兜底准得多。
# 越具体的模式写在越前面；值取各家族官方公布的默认上下文长度（偏保守）。
_FAMILY_WINDOW_HINTS = (
    (r"qwen2\.5.*1m", 1000000),            # Qwen2.5-1M
    (r"qwen2\.5.*72b", 131072),
    (r"qwen3", 32768),
    (r"qwen2\.5", 32768),
    (r"qwen2(?!\.)", 32768),
    (r"llama-?3\.[123]", 131072),          # Llama 3.1 / 3.2 / 3.3
    (r"llama-?3", 8192),
    (r"llama-?2", 4096),
    (r"gemma-?3.*(?:4b|12b|27b)", 131072),
    (r"gemma-?3", 32768),
    (r"gemma", 8192),
    (r"phi-?4.*mini", 131072),
    (r"phi-?4", 16384),
    (r"phi-?3.*4k", 4096),
    (r"phi-?3", 128000),
    (r"mixtral", 32768),
    (r"mistral|magistral|devstral", 32768),
    (r"deepseek-?r1", 131072),
    (r"deepseek-?v3", 131072),
    (r"deepseek-?v2", 65536),
    (r"deepseek-?coder", 16384),
    (r"glm-?4", 131072),
    (r"yi-?1\.5|yi-?34b", 200000),
    (r"yi-", 32768),
    (r"gpt-4\.1", 1047576),
    (r"gpt-4o|o[134]-", 128000),
    (r"gpt-4", 8192),
    (r"gpt-3\.5", 16385),
    (r"claude", 200000),
    (r"kimi|moonshot", 131072),
    (r"grok", 131072),
    (r"internlm", 32768),
    (r"baichuan", 32768),
    (r"chatglm", 32768),
    (r"smollm", 8192),
    (r"tinyllama", 2048),
    (r"granite", 4096),
)
_LOCAL_CTX_CACHE = {"t": 0.0, "n": 0}   # 本机 llama-server 的 n_ctx 缓存
WINDOW_CACHE_TTL = 6 * 3600    # 模型窗口缓存有效期，过期后台刷新


def _norm_model(m: str) -> str:
    """归一化模型名：deepseek-v4-flash 与 deepseek-flash 视作同一个模型。"""
    return re.sub(r"v\d+(?:\.\d+)?[-_]", "", (m or "").strip())


def guess_window_from_name(model: str) -> int:
    """按模型名推断上下文窗口（tokens）；认不出来返回 0。

    只给「服务端 /models 不报 context_window」的后端兜底（llama.cpp / ollama 常见），
    报得出的（DeepSeek 官方就报）走不到这条路。
    """
    m = (model or "").strip().lower()
    if not m:
        return 0
    for pat, win in _FAMILY_WINDOW_HINTS:
        if re.search(pat, m):
            return int(win)
    return 0


def local_context_tokens(timeout: float = 1.0, ttl: float = 30.0) -> int:
    """本机 llama-server 的真实上下文长度（/props 里的 n_ctx）；拿不到返回 0。

    本地模型的窗口是启动时用 -c 决定的，只有 /props 报得准，/v1/models 不给。
    失败时把缓存时间拉长，免得每次刷新页面都白等一秒超时。
    """
    now = time.time()
    if now - _LOCAL_CTX_CACHE["t"] < (ttl if _LOCAL_CTX_CACHE["n"] else ttl * 20):
        return _LOCAL_CTX_CACHE["n"]
    n = 0
    try:
        base = LOCAL_MODEL_URL[:-3] if LOCAL_MODEL_URL.endswith("/v1") else LOCAL_MODEL_URL
        req = urllib.request.Request(base.rstrip("/") + "/props",
                                     headers={"User-Agent": "termux-agent/" + VERSION})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
        if isinstance(d, dict):
            n = int(((d.get("default_generation_settings") or {}).get("n_ctx"))
                    or d.get("n_ctx") or 0)
    except Exception:
        n = 0
    _LOCAL_CTX_CACHE.update({"t": now, "n": n})
    return n


def cfg_compress_ratio(cfg: dict) -> float:
    try:
        r = float(cfg.get("compress_ratio") or COMPRESS_RATIO)
    except Exception:
        r = COMPRESS_RATIO
    return max(0.1, min(0.95, r))


def _save_windows(cfg: dict, out: dict, modal: dict = None, ids: list = None) -> None:
    """只把窗口信息并进磁盘配置再写回，绝不覆盖用户其它设置。

    （踩过坑：直接 save_config(cfg) 时，若传入的 cfg 是个不完整的字典，
      会把用户的名称、进化开关等一并抹掉。）
    """
    disk = {}
    try:
        if CONFIG_PATH.exists():
            disk = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(disk, dict):
            disk = {}
    except Exception:
        disk = {}
    disk["model_windows"] = out
    disk["model_windows_at"] = time.time()
    if modal:
        disk["model_modalities"] = modal
    if ids:
        disk["model_list"] = list(ids)      # 官方在售模型列表，界面下拉用它
    save_config(disk)
    cfg["model_windows"] = out
    cfg["model_windows_at"] = disk["model_windows_at"]
    if modal:
        cfg["model_modalities"] = modal
    if ids:
        cfg["model_list"] = list(ids)


def refresh_model_windows(cfg: dict, force: bool = False) -> dict:
    """从服务端 /models 拉各模型的真实上下文窗口，缓存进 config。失败不抛异常。"""
    cache = cfg.get("model_windows") or {}
    try:
        at = float(cfg.get("model_windows_at") or 0)
    except Exception:
        at = 0
    if cache and not force and (time.time() - at) < WINDOW_CACHE_TTL:
        return cache
    try:
        req = urllib.request.Request(
            cfg["base_url"].rstrip("/") + "/models",
            headers={"Authorization": "Bearer " + (cfg.get("api_key") or ""),
                     "User-Agent": "termux-agent/" + VERSION})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.load(r)
        out, modal, ids = {}, {}, []
        for m in data.get("data") or []:
            mid = m.get("id")
            if not mid:
                continue
            ids.append(mid)
            if m.get("context_window"):
                out[mid] = int(m["context_window"])
            if m.get("input_modalities"):
                modal[mid] = list(m["input_modalities"])
        if ids:
            _save_windows(cfg, out, modal, ids)
            return out
    except Exception:
        pass
    return cache


def _maybe_refresh_windows(cfg: dict) -> None:
    """当前模型不在窗口缓存里（比如换了接口）时，后台补拉一次官方数据。"""
    if (cfg.get("model") or "") in (cfg.get("model_windows") or {}):
        return
    threading.Thread(target=lambda: refresh_model_windows(cfg, force=True),
                     daemon=True).start()


def model_window_tokens(model: str, cfg: dict = None) -> int:
    """模型真实上下文窗口（tokens）。取值顺序见 window_info()。"""
    return window_info(model, cfg)[0]


def window_info(model: str, cfg: dict = None) -> tuple:
    """(窗口 tokens, 来源)。来源取值：官方 / 本地 / 内置 / 推断 / 默认。

    「窗口」取这个模型**真实**的上下文长度，换模型就跟着变：
      1) 服务端 /models 报的 context_window（DeepSeek 官方会报，最准）
      2) 本机 llama-server /props 的 n_ctx（本地模型只有这里准）
      3) 内置表里写死的官方值
      4) 按模型名里的家族推断（小模型就不会被当成 128K 了）
      5) 实在认不出才退回 DEFAULT_WINDOW_TOKENS
    """
    cfg = cfg or {}
    m = (model or "").strip()
    if not m:
        return DEFAULT_WINDOW_TOKENS, "默认"
    n = _norm_model(m)
    cache = cfg.get("model_windows") or {}
    if m in cache:                                  # 1) 服务端上报的
        return int(cache[m]), "官方"
    for k, v in cache.items():
        if _norm_model(k) == n:
            return int(v), "官方"
    if _is_local_url(cfg.get("base_url")) or m.lower().endswith(".gguf"):
        w = local_context_tokens()                  # 2) 本机 llama.cpp
        if w > 0:
            return w, "本地"
    for k, v in MODEL_CONTEXT_TOKENS.items():       # 3) 内置表
        if k == m or _norm_model(k) == n:
            return v, "内置"
    g = guess_window_from_name(m)                   # 4) 按名字推断
    if g > 0:
        return g, "推断"
    return DEFAULT_WINDOW_TOKENS, "默认"             # 5) 兜底


def compress_at_for(cfg: dict, model: str = None, ratio: float = None) -> int:
    """压缩阈值（字符）= 模型窗口 × 比例 ÷ 每 token 字符数。"""
    win = model_window_tokens(model or cfg.get("model"), cfg)
    if win <= 0:                     # 完全拿不到窗口信息：退回固定兜底值
        return COMPRESS_AT_CHARS
    # 2026-10-01：标称窗口 1M 会让阈值算成 110 万字符 ≈ 73 万 token —— 等于永不压缩，
    # 长会话一路膨胀到几十万 token，每轮既慢又贵。按真实预算收口。
    budget = int(cfg.get("ctx_budget_tokens") or CTX_BUDGET_TOKENS)
    win = max(8000, min(win, budget))
    r = cfg_compress_ratio(cfg) if ratio is None else max(0.1, min(0.95, float(ratio)))
    return max(5000, int(win * r * CHARS_PER_TOKEN))
KEEP_RECENT_MESSAGES = 16      # 压缩时无论如何保留的最近消息数
MEMORY_MAX_CHARS = 4000        # 长期记忆上限；超出只裁「流水区」，稳定区不丢（见 merge_memory）
DISTILL_EVERY = 3              # 每 3 条用户消息提炼一次记忆（后台异步）

# ---------------------------------------------------------------- 终端样式

_NO_COLOR = (not sys.stdout.isatty()) or os.environ.get("NO_COLOR")


def c(text: str, code: str) -> str:
    return text if _NO_COLOR else f"\033[{code}m{text}\033[0m"


def dim(t):    return c(t, "2")
def bold(t):   return c(t, "1")
def red(t):    return c(t, "31")
def green(t):  return c(t, "32")
def yellow(t): return c(t, "33")
def blue(t):   return c(t, "34")
def cyan(t):   return c(t, "36")


# ---------------------------------------------------------------- 运行环境

def is_termux() -> bool:
    prefix = os.environ.get("PREFIX", "")
    return prefix.startswith("/data/data/com.termux") or bool(os.environ.get("TERMUX_VERSION"))


def termux_shell() -> str:
    """返回应当用来执行命令的 shell 路径。"""
    if is_termux():
        for p in ("/data/data/com.termux/files/usr/bin/bash",
                  "/data/data/com.termux/files/usr/bin/sh"):
            if os.path.exists(p):
                return p
    for p in ("/bin/bash", "/usr/bin/bash", "/bin/sh"):
        if os.path.exists(p):
            return p
    return shutil.which("bash") or shutil.which("sh") or "/bin/sh"


# ---------------------------------------------------------------- 长期记忆

def load_memory() -> str:
    try:
        return MEMORY_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def save_memory(text: str) -> None:
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        MEMORY_PATH.write_text(text, encoding="utf-8")
    except Exception:
        pass


_DRAFT_PREFIX = re.compile(
    r"^(let me|i must|i should|i'll|i will|hmm\b|the key|new\b|existing memory|"
    r"candidates|what|how|why|which|where|who|here are|looking at|first,|next,|"
    r"比较重要的新增|看对话内容|我挑最重要|新增条目|从对话|分析|梳理|总结一下)",
    re.I,
)


def is_memory_draft(line: str) -> bool:
    """判断一行是不是模型漏出来的思考草稿（而不是一条记忆）。

    记忆提炼偶尔会把模型的推理过程当输出整段返回；若逐行存进 memory.md，
    整份长期记忆会被冲成英文草稿（2026-09-28、2026-09-29 各中过一次，
    第二次把 3234 字里的约 2200 字都污染成了草稿）。
    这里按三种特征挡掉：小标题（以冒号结尾）、草稿常见开头、整行无中文的长英文句。
    """
    if line.endswith((":", "：")):
        return True
    if _DRAFT_PREFIX.match(line):
        return True
    if not re.search(r"[\u4e00-\u9fff]", line) and len(re.findall(r"[A-Za-z]+", line)) >= 4:
        return True
    return False


MEMORY_FLOW_MARK = "【流水】"   # 稳定区 / 流水区的分界线：这行之前是稳定事实，裁剪不碰


def _memory_parts(text: str) -> tuple:
    """把记忆拆成 (稳定区, 流水区)。

    稳定区放设备参数、环境限制、用户关系与偏好、约定这类不该丢的事实；
    流水区放记忆提炼来的过程性进展（「xx 已修」「xx 已生效」之类）。
    分界就是单独一行的 MEMORY_FLOW_MARK；老格式（没有分界）整个算稳定区，
    下次合并时会自动补出一段流水区。
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if MEMORY_FLOW_MARK in lines:
        i = lines.index(MEMORY_FLOW_MARK)
        return lines[:i], lines[i + 1:]
    return lines, []


def merge_memory(new_items: str) -> None:
    """把新提炼的条目追加进记忆，去重并按上限裁剪。

    裁剪只从「流水区」开头丢：那里是提炼来的过程性进展，丢了不心疼。
    流水区丢空了还超限，才动稳定区的开头（保底，正常不该走到）。
    从前的写法是直接砍最旧的行，于是最近记的一堆界面琐事会把设备参数、
    用户关系这类稳定事实挤出记忆 —— 2026-09-30「系统更新被禁用」那条
    就是这么丢的，导致用户点不了更新时谁都说不清原因。
    """
    if not new_items or not new_items.strip():
        return
    stable, flow = _memory_parts(load_memory())
    seen = {l[:24] for l in stable + flow}
    added = []
    for line in new_items.splitlines():
        line = line.strip().lstrip("-•·* ").strip()
        if not line or line.startswith("#") or line == MEMORY_FLOW_MARK:
            continue
        if line[:24] in seen:
            continue
        if is_memory_draft(line):     # 挡掉模型漏出来的思考草稿
            continue
        seen.add(line[:24])
        added.append(line)
    if not added:
        return
    flow = flow + added

    def _size() -> int:
        return (sum(len(l) for l in stable) + sum(len(l) for l in flow)
                + len(MEMORY_FLOW_MARK))

    while flow and _size() > MEMORY_MAX_CHARS:
        flow.pop(0)                   # 先回收流水区最旧的
    while stable and _size() > MEMORY_MAX_CHARS:
        stable.pop(0)                 # 实在放不下，才动稳定区
    save_memory("\n".join(stable + [MEMORY_FLOW_MARK] + flow))


# ---------------------------------------------------------------- 会话存储

def _new_sid() -> str:
    """生成会话 id。

    后缀用随机数，而不是原来的「进程 PID 后 4 位」—— 那样同一个进程在同一秒内
    连开两个会话会撞同一个 id，后一个直接覆盖前一个。
    """
    tail = str(int.from_bytes(os.urandom(2), "big") % 10000).zfill(4)
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + tail


def _sess_path(sid: str) -> Path:
    return SESSIONS_DIR / f"{sid}.json"


# 内部会话（自我进化 / 自愈）不该出现在用户的对话列表里，
# 也不该被「恢复上次会话」选中。
INTERNAL_SID_PREFIX = ("evolve-", "selfheal-", "install-")


def make_session_title(messages: list) -> str:
    """从对话内容里提炼 5~20 字的会话标题。

    取第一条用户消息，去掉系统注入的方括号前缀（如 [系统自动生成的更早对话摘要]）、
    Markdown 记号与多余空白，再截到 20 字以内。用户自定义重命名存在会话的
    title 字段里，有值时优先用它（见 list_sessions）。
    """
    for m in messages:
        if m.get("role") != "user":
            continue
        raw = (m.get("content") or "")
        raw = re.sub(r"^\s*\[[^\]]{0,40}\]\s*", "", raw)      # 去掉开头的方括号前缀
        raw = re.sub(r"[#*`>_~]+", "", raw)                    # 去掉 markdown 记号
        raw = re.sub(r"\s+", " ", raw).strip()
        if not raw:
            continue
        if len(raw) > 20:
            raw = raw[:20].rstrip() + "…"
        return raw
    return ""


def list_sessions(include_internal: bool = False) -> list:
    """返回会话列表（按更新时间倒序）。

    include_internal=False 时过滤掉内部会话（自我进化的会话 id 以 evolve- 开头），
    它们不该出现在用户的历史列表里，也不该在重启时被当成"上次对话"恢复。
    """
    out = []
    if not SESSIONS_DIR.exists():
        return out
    for f in sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if not include_internal and f.stem.startswith(INTERNAL_SID_PREFIX):
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            msgs = d.get("messages") or []
            # 会话自带 title（可能是用户重命名过的）；没有才自动提炼
            title = (d.get("title") or "").strip() or make_session_title(msgs)
            out.append({"id": f.stem, "title": title or "（新对话）",
                        "updated_at": d.get("saved_at", ""), "messages": len(msgs),
                        "model": d.get("model") or "",
                        "pinned": bool(d.get("pinned"))})
        except Exception:
            continue
    # 置顶的排最前；同一组内维持原顺序（按更新时间倒序，sort 是稳定的）
    out.sort(key=lambda x: not x.get("pinned"))
    return out


# ---------------------------------------------------------------- 会话归档
# 归档 = 把上下文交给模型压成一段摘要，同时把**完整原文**一并搬进 archive/（不是删除）。
# 归档件 = archive/<id>.json：{id, title, saved_at, archived_at, summary, messages[完整原文]}
ARCHIVE_DIR = APP_DIR / "archive"


def _archive_path(sid: str) -> Path:
    return ARCHIVE_DIR / f"{sid}.json"


def _safe_sid(sid: str) -> str:
    """挡掉路径穿越，归档/删除只允许操作纯文件名。"""
    return re.sub(r"[^0-9A-Za-z._-]", "_", (sid or "").strip())[:80]


def list_archived() -> list:
    """归档列表（摘要在前，完整原文留在文件里，随时可恢复）。"""
    out = []
    if not ARCHIVE_DIR.exists():
        return out
    for f in sorted(ARCHIVE_DIR.glob("*.json"), key=lambda q: q.stat().st_mtime, reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        msgs = d.get("messages") or []
        out.append({
            "id": d.get("id") or f.stem,
            "title": (d.get("title") or "").strip() or "（无标题）",
            "archived_at": d.get("archived_at") or "",
            "messages": len(msgs),
            "chars": sum(len(str(m.get("content") or "")) for m in msgs),
            "summary": (d.get("summary") or "").strip(),
        })
    return out


def archive_session(sid: str, cfg: dict) -> dict:
    """归档一个会话：先压缩成摘要，再把原文一起存进 archive/，最后从最近列表移走。"""
    sid = _safe_sid(sid)
    src = _sess_path(sid)
    if not src.exists():
        raise RuntimeError("这个会话已经归档过了" if _archive_path(sid).exists() else "找不到该会话")
    d = json.loads(src.read_text(encoding="utf-8"))
    msgs = d.get("messages") or []
    plain = [m for m in msgs if m.get("role") != "system"]
    summary = ""
    if plain:
        try:
            summary = (_chat_once(cfg, [
                {"role": "system", "content": "你是对话归档器。把下面这段对话压缩成要点摘要，保留："
                                              "用户的目标与需求、已完成的事、重要结论与决定、"
                                              "用户的偏好和约束、未完成的待办。用简洁中文，400 字以内。"},
                {"role": "user", "content": _format_transcript(plain, cap=24000)},
            ], 900) or "").strip()
        except Exception:
            summary = ""
    if not summary:
        first = next((str(m.get("content") or "") for m in plain if m.get("role") == "user"), "")
        summary = ("（归档时没连上模型，未生成摘要；原文完整保留）\n"
                   + first.replace("\n", " ")[:200]).strip()
    rec = {"id": sid, "title": d.get("title") or "", "saved_at": d.get("saved_at") or "",
           "archived_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "summary": summary, "messages": msgs}
    if d.get("compress_ratio"):
        rec["compress_ratio"] = d["compress_ratio"]
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    _archive_path(sid).write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
    src.unlink()                       # 已完整搬进 archive/，不是删除
    return {"id": sid, "title": rec["title"], "messages": len(msgs), "summary": summary}


def unarchive_session(sid: str):
    """把归档件放回最近对话；返回新的会话 id，失败返回 None。"""
    sid = _safe_sid(sid)
    p = _archive_path(sid)
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    new_sid = sid
    while _sess_path(new_sid).exists():          # 撞名就加后缀，绝不覆盖任何已有会话
        new_sid = sid + "-" + datetime.now().strftime("%H%M%S")
    data = {"saved_at": d.get("saved_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "title": d.get("title") or "", "messages": d.get("messages") or []}
    if d.get("compress_ratio"):
        data["compress_ratio"] = d["compress_ratio"]
    _sess_path(new_sid).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    p.unlink()
    return new_sid


def delete_session_file(sid: str, archived: bool = False) -> bool:
    """删除会话（archived=True 时删的是归档件）。"""
    p = _archive_path(sid) if archived else _sess_path(sid)
    if not p.exists():
        return False
    p.unlink()
    return True


def sessions_payload(agent, cur: str = "") -> dict:
    return {"current": cur or agent.session_id, "sessions": list_sessions(),
            "archived": list_archived()}


def load_session_messages(sid: str):
    p = _sess_path(sid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("messages") or []
    except Exception:
        return None


def load_session_compress_ratio(sid: str):
    """会话自己记录的压缩比例（0.1~0.95）；老会话没有则返回 None（用配置默认值）。"""
    p = _sess_path(sid)
    if not p.exists():
        return None
    try:
        v = json.loads(p.read_text(encoding="utf-8")).get("compress_ratio")
        return max(0.1, min(0.95, float(v))) if v else None
    except Exception:
        return None


def migrate_session_models(model: str = "", base_url: str = "") -> int:
    """给还没有模型记录的老会话补上默认模型，返回补了几个。

    不补的话，老会话会"跟随"全局设置 —— 你在别的对话切到本地模型后，
    再切回老对话也会变成本地模型，很容易困惑。
    """
    if not (model or base_url) or not SESSIONS_DIR.exists():
        return 0
    n = 0
    for f in SESSIONS_DIR.glob("*.json"):
        if f.stem.startswith("evolve-"):
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if d.get("model") or d.get("base_url"):
                continue
            if model:
                d["model"] = model
            if base_url:
                d["base_url"] = base_url
            f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            n += 1
        except Exception:
            pass
    return n


LAST_SESSION_FILE = APP_DIR / "last_session.txt"


def mark_last_session(sid: str) -> None:
    """记下用户最后在用的会话。

    （踩过坑：重启后按"文件修改时间"挑会话恢复，挑中了一个刚建的空对话，
      用户打开就以为聊天记录全丢了。）

    又踩一次：只认"磁盘上真有这个会话文件"的 id。否则会话被删、文件被写坏之后，
    一个没有文件的"幽灵 id"会被记成"上次会话"，下次重启恢复不了 —— 界面落在一个
    空对话上，侧栏里明明还躺着历史，看着就像记录全没了。
    """
    if not sid or not _sess_path(sid).exists():
        return
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        LAST_SESSION_FILE.write_text(str(sid), encoding="utf-8")
    except Exception:
        pass


def get_last_session() -> str:
    """上次在用的会话 id（只返回真正能打开的）。

    文件被删、或只剩空壳（一条消息都没有）的 id 一律不放行，改成回退到
    "最近一个真有内容的会话"，免得重启后又把用户丢进空对话里。
    """
    sid = ""
    try:
        sid = LAST_SESSION_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    if sid and _sess_path(sid).exists() and load_session_messages(sid):
        return sid
    for x in list_sessions():          # 就近回退（list_sessions 已按更新时间倒序）
        if x.get("messages"):
            return x["id"]
    return ""


def load_session_model(sid: str):
    """读会话自己记录的模型配置；老会话没有则返回 None（沿用全局设置）。"""
    p = _sess_path(sid)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        m, b = d.get("model"), d.get("base_url")
        return {"model": m, "base_url": b} if (m or b) else None
    except Exception:
        return None


def update_session_model(sid: str, model: str = "", base_url: str = "") -> None:
    """只改会话文件里的模型字段，不动消息体（轻量，切换模型时用）。"""
    if not sid:
        return
    p = _sess_path(sid)
    if not p.exists():
        return
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if model:
            d["model"] = model
        if base_url:
            d["base_url"] = base_url
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def rename_session(sid: str, title: str) -> bool:
    """给会话起自定义名字，存进会话文件的 title 字段。

    存进去之后 list_sessions 会优先用它，自动提炼的标题不会覆盖掉。
    """
    try:
        p = _sess_path(sid)
        if not p.exists():
            return False
        d = json.loads(p.read_text(encoding="utf-8"))
        d["title"] = (title or "").strip()[:40]
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception:
        return False


def pin_session(sid: str, pinned: bool = True) -> bool:
    """置顶 / 取消置顶某个会话（存进会话文件的 pinned 字段）。"""
    try:
        p = _sess_path(sid)
        if not p.exists():
            return False
        d = json.loads(p.read_text(encoding="utf-8"))
        if pinned:
            d["pinned"] = True
        else:
            d.pop("pinned", None)          # 取消时删字段，别攒一堆 false
        p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception:
        return False


def save_session_messages(sid: str, messages: list, compress_ratio=None,
                          model: str = "", base_url: str = "") -> None:
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        # 用户重命名的名字、置顶标记都要留住，别被这次覆盖掉；没标题才自动提炼
        title = ""
        pinned = False
        try:
            p_old = _sess_path(sid)
            if p_old.exists():
                _old = json.loads(p_old.read_text(encoding="utf-8"))
                title = (_old.get("title") or "").strip()
                pinned = bool(_old.get("pinned"))
        except Exception:
            title = ""
        if not title:
            title = make_session_title(messages)
        data = {"saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "title": title, "messages": messages}
        if pinned:
            data["pinned"] = True
        if compress_ratio:
            data["compress_ratio"] = round(float(compress_ratio), 3)
        # 这个对话用的是什么模型 —— 切回来时自动还原，不影响别的对话
        if model:
            data["model"] = model
        if base_url:
            data["base_url"] = base_url
        _sess_path(sid).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _migrate_old_session() -> None:
    """把旧版 last-session.json 迁到 sessions/ 目录。"""
    if SESSIONS_DIR.exists() and any(SESSIONS_DIR.glob("*.json")):
        return
    if not SESSION_PATH.exists():
        return
    try:
        d = json.loads(SESSION_PATH.read_text(encoding="utf-8"))
        if d.get("messages"):
            save_session_messages("legacy-" + _new_sid(),
                                  _sanitize_messages(d["messages"]))
    except Exception:
        pass


# ---------------------------------------------------------------- 配置

DEFAULT_CONFIG = {
    "api_key": "",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-flash",
    "max_tokens": DEFAULT_MAX_TOKENS,
    "temperature": 1.0,
    "timeout_sec": 300,
    "workdir": "",
    "compress_ratio": COMPRESS_RATIO,   # 用到模型窗口的百分之多少就自动压缩（0.7 = 70%）
    "max_tool_rounds": MAX_TOOL_ROUNDS,  # 单轮任务最多工具轮次
    "auto_approve": True,     # 设备是用户自己的，命令默认全部放行
    "show_reasoning": True,
    "send_usage": True,
    "local_lite": True,       # 本地模型走轻量模式（不发工具、精简提示词），见 local_lite()
    "language": "中文",
    "web_port": 8765,
    "shell_access": True,     # 允许用 shell(adb) 身份执行命令，权限高于普通应用
    "wb_api_key": "",         # WorkBuddy 平台 API Key（ck_ 开头），用于 imgedit 图像处理
    # ---- 自我进化（默认关闭，需用户在界面里显式开启并写明方向）
    # 默认名 Sidekick：用户可在「身份」页改，改成什么就自称什么；这里只是出厂值
    "assistant_name": "Sidekick",
    "evolve_enabled": False,
    "evolve_direction": "",
    "evolve_per_day": 1,
    # ---- 系统通知（走 shell 的 cmd notification post，不需要装 Termux:API）
    "notify_done": True,     # 长任务跑完提醒
    "notify_error": True,    # 出错中止时提醒
    "notify_ask": True,      # 需要你拍板时提醒
    "notify_stuck": True,    # 卡住 / 反复重试时提醒
    "notify_min_secs": 30,   # 超过多少秒才算"长任务"
    # ---- 多服务商：{名称: {base_url, api_key, models:[...]}}
    # 选中的模型属于哪家，就自动用哪家的 base_url / api_key（见 sync_provider_for_model）
    "providers": {},
}


def provider_of_model(cfg: dict, model: str):
    """模型属于哪个服务商。返回 (名称, 配置字典) 或 (None, None)。"""
    m = str(model or "")
    for pname, p in (cfg.get("providers") or {}).items():
        if not isinstance(p, dict):
            continue
        if m and m in [str(x) for x in (p.get("models") or [])]:
            return pname, p
    return None, None


def sync_provider_for_model(cfg: dict, model: str = "") -> str:
    """把 base_url / api_key 同步成该模型所属服务商的值。

    找不到对应服务商时保持原样（兼容单服务商的老配置）。
    返回切换到的服务商名称，未切换则返回空串。
    """
    pname, p = provider_of_model(cfg, model or cfg.get("model"))
    if not p:
        return ""
    changed = False
    if p.get("base_url") and cfg.get("base_url") != p["base_url"]:
        cfg["base_url"] = p["base_url"]; changed = True
    if p.get("api_key") and cfg.get("api_key") != p["api_key"]:
        cfg["api_key"] = p["api_key"]; changed = True
    if cfg.get("provider") != pname:
        cfg["provider"] = pname
    return pname if changed else ""

# 界面可选的模型（已实测可用的；/v1/models 返回不全，故用固定清单）
# 官方在售模型（兜底用；启动后会从服务端 /models 拉真实列表覆盖，见 refresh_model_windows）
KNOWN_MODELS = [
    "deepseek-flash",
    "deepseek-v4-pro",
]


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as e:
            print(yellow(f"[!] 配置文件读取失败，使用默认值：{e}"))
    # 环境变量优先级高于配置文件
    for env, key in (("TERMUX_AGENT_API_KEY", "api_key"),
                     ("DEEPSEEK_API_KEY", "api_key"),
                     ("TERMUX_AGENT_MODEL", "model"),
                     ("TERMUX_AGENT_BASE_URL", "base_url"),
                     ("TERMUX_AGENT_WORKDIR", "workdir")):
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    if not cfg.get("workdir"):
        cfg["workdir"] = str(Path.home())
    return cfg


def save_config(cfg: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(CONFIG_PATH, 0o600)   # 里面存着 API Key
    except Exception:
        pass


# ---------------------------------------------------------------- 外观（阅读排版）
# 正文的行高/段距/字号等，用户可在控制台「外观」页调。存 config.json 的 ui 字段下。
# 前端把这些值写成 CSS 变量驱动 .bub 样式；这里只负责存取与夹取合法范围。
APPEARANCE_DEFAULTS = {
    "font": 16,        # 正文字号 px
    "line": 23,        # 正文行高 px（绝对值，不是倍数）
    "para": 6,         # 段落间距 px
    "h_gap": 13,       # 标题上方留白 px
    "li": 2,           # 列表项间距 px
    "code": 13,        # 代码块字号 px
}
# 每项：最小值、最大值
APPEARANCE_RANGE = {
    "font": (13, 22), "line": (16, 40), "para": (0, 20),
    "h_gap": (4, 28), "li": (0, 12), "code": (11, 20),
}


def get_appearance(cfg: dict) -> dict:
    """读外观设置，缺项/越界一律回落到默认值。"""
    raw = cfg.get("ui") or {}
    if not isinstance(raw, dict):
        raw = {}
    out = {}
    for k, dflt in APPEARANCE_DEFAULTS.items():
        lo, hi = APPEARANCE_RANGE[k]
        try:
            v = int(round(float(raw.get(k, dflt))))
        except (TypeError, ValueError):
            v = dflt
        out[k] = max(lo, min(hi, v))
    # 行高不该小于字号（否则字会叠在一起），至少比字号大 2px
    if out["line"] < out["font"] + 2:
        out["line"] = out["font"] + 2
    return out


# ---------------------------------------------------------------- 系统提示词

# ══ 每轮易变上下文（2026-10-01 加）══════════════════════════════════════════
# 为什么不能写在系统提示词里：
#   上游（DeepSeek / 同源接口）的上下文缓存要求「从第 0 个 token 起完全一致」才算
#   命中，命中价只有未命中的 1/10，且能把长输入的首 token 延迟从十几秒压到几百毫秒。
#   而系统提示词每轮都会被 _rebuild_system() 重建 —— 只要里面有一个字符变（比如
#   精确到秒的当前时间），它后面的**全部内容**（含整段对话历史）都失去缓存。
#   实测：改前命中率 14.1%，把易变内容挪到对话末尾后 96.1%。
#
# 做法：
#   系统提示词只留不变的部分；易变内容写成一条 system 消息，插在「本轮的 user
#   消息之前」。界面只渲染 user/assistant（见 history_payload），所以用户看不到它。
#   关键：**写一次就不再改动**。历史因此逐字节稳定，缓存才立得住。
#
# 记忆与任务清单只在内容真的变了时重挂一次（没变就沿用历史里那份），否则每轮
# 重复注入会把上下文一路撑大 —— 时间戳很小，每轮都挂不影响。


def _todo_rows(limit: int = 12) -> str:
    """当前任务清单的渲染文本（空串表示没有待办）。"""
    try:
        _td = load_todo(TODO_SID)
        _its = [x for x in (_td.get("items") or []) if x.get("text")]
        if not _its:
            return ""
        return "\n".join(("已完成：" if x.get("done") else "未完成：") + x["text"]
                         for x in _its[:limit])
    except Exception:
        return ""


def build_turn_context(cfg: dict, snap: dict):
    """生成本轮易变上下文。返回 (要插入的文本, 新快照)。文本为空则不插。"""
    snap = snap or {}
    out = ["[本轮上下文 · " + datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "]"]
    new_snap = dict(snap)
    try:
        mem = load_memory()
    except Exception:
        mem = ""
    if mem and mem != snap.get("mem"):
        out.append("[你对这位用户的长期记忆（对话中逐步积累，请默认采信，除非与新事实冲突）]\n" + mem)
        new_snap["mem"] = mem
    rows = _todo_rows()
    if rows and rows != snap.get("todo"):
        out.append(
            "[当前任务清单] 用户交代的待办（来自本机 todo.json，不受对话压缩影响）：\n"
            + rows
            + "\n处理原则：① 接手前先看这份清单，已完成的不重做，未完成的接着做；"
              "② 如果用户提到的旧任务不在这里、你也想不起来，那是被上下文压缩掉了 —— "
              "先用 read_file/grep 去 ~/.termux-agent/archive/ 和 sessions/ 的会话原文里找回来，"
              "③ 但绝不要对用户说「那是在另一个对话里」：对他而言始终是同一个对话，"
              "这样只会让他觉得你在推脱。")
        new_snap["todo"] = rows
    return "\n\n".join(out), new_snap


def trim_old_reasoning(messages: list, keep: int = 1) -> list:
    """只保留最近 keep 条助手消息的思考过程，更早的清空。

    思考过程是「草稿」：模型续写下一步时需要的只是紧接着的那一条，再往前的对
    后续决策没有帮助，却会一直作为输入重复计费（实测占长会话输入的 40%）。
    空串是上游接受的形式 —— _sanitize_messages 里早就在给老会话补空串。
    返回浅拷贝，不改动会话里存的原件（界面/存档仍保留完整思考）。
    """
    out = [dict(m) for m in messages]
    idx = [i for i, m in enumerate(out)
           if m.get("role") == "assistant" and m.get("reasoning_content")]
    if len(idx) <= keep:
        return out
    for i in (idx[:-keep] if keep > 0 else idx):
        out[i]["reasoning_content"] = ""
    return out


def build_system_prompt(cfg: dict) -> str:
    # 界面语言为英文时，整段系统提示词改用英文版（见 build_system_prompt_en）。
    # 模型收到的指令与用户界面语言一致，回复语言也随之一致。
    if str(globals().get("UI_LANG") or "zh").lower().startswith("en"):
        return build_system_prompt_en(cfg)
    if local_lite(cfg):
        name = (cfg.get("assistant_name") or "Sidekick").strip()
        lang = cfg.get("language") or "中文"
        return (f"你是「{name}」，一个跑在用户平板上的本地 AI 助手（离线、速度较慢）。"
                f"请用简洁的{lang}回答，直接给答案，不要啰嗦。"
                "你暂时无法使用工具，只能凭已有知识回答。")
    env_lines = [
        f"工作目录: {cfg['workdir']}",
        f"操作系统: {'Android / Termux' if is_termux() else sys.platform}",
        f"Python: {sys.version.split()[0]}",
        f"shell: {termux_shell()}",
    ]
    if is_termux():
        env_lines += [
            "关于这台设备的重要事实（请牢记，避免犯低级错误）：",
            "- 这是安卓设备上的 Termux 环境，不是常规 Linux 服务器。",
            "- 没有 root 权限，不要尝试 sudo / su，会失败。",
            "- 安装软件用 `pkg install <name>`（不是 apt/yum）。",
            "- 家目录是 Termux 私有目录 $HOME（/data/data/com.termux/files/home），不会被安卓系统清理。",
            "- 访问安卓共享存储（相册、下载等）需要先执行 `termux-setup-storage`，之后路径在 ~/storage/shared/。",
            "- 没有 systemd，后台常驻请用 nohup 或 termux-wake-lock，不要用 systemctl。",
            "- 设备资源有限，避免跑高内存任务；大输出要分页处理。",
            "- 下载安装包/大文件：用 download 工具（流式保存、不截断、自动重试超时），"
            "  保存到 ~/storage/shared/Download/ 下，下载完告诉用户文件在哪。",
            "- 安装 APK：先用 download 下好 .apk，再用 sysshell 执行 `pm install -r 路径`（这是系统级安装，"
            "  不需要确认）。安装失败多半是包不兼容或签名问题，把 pm 的报错原样告诉用户。",
        ]
        env_lines += [
            "关于你自己（你可以修改自己）：",
            f"- 你的源码就是 {Path(__file__).resolve()}，是单文件 Python，可以 read_file 读、edit_file 改。",
            "- 修复自身 bug 或添加功能时，用 `selfupdate` 工具（不是 write_file）：它会自动备份、"
            "  语法检查（失败立即还原），成功后服务自动重启加载新代码；若新代码起不来，看门狗会回滚。",
            "- 自更新的正确姿势：① read_file 读相关代码 ② 想清楚最小改动 ③ 用 selfupdate 传 patch_old/patch_new。"
            "改动要小、要局部，避免大范围重写。",
            "- 自更新后本回合即结束（服务要重启）；重启后可以再验证，但如果它没起来，用户会告诉你。",
            "- 重启后你的对话历史、长期记忆、会话文件都不会丢。",
        ]
        if cfg.get("shell_access"):
            env_lines += [
                "- 你可以用 `sysshell` 工具以 shell（adb）身份执行命令，权限高于普通应用：",
                "  能读系统设置、dumpsys、getprop、pm/am、修改全局设置。普通 bash 遇到权限拒绝时改用 sysshell。",
                "- sysshell 仍不是 root：读不了其它应用私有数据，也写不了系统只读分区。",
                "- 如果 sysshell 报连不上 adb：有电脑就在电脑上执行 adb tcpip 5555；"
                "没电脑就开平板的「无线调试」，用 agent adbpair 配对一次即可，之后自动保持。",
            ]
    # 2026-10-01：长期记忆改由 build_turn_context() 每轮挂在对话末尾。
    # 它会随记忆蒸馏而变化；留在系统提示词里，上游的前缀缓存（要求从第 0 个
    # token 起完全一致）会整段失效 —— 实测命中率 14%，挪走后 96%。
    mem = ""
    if mem:
        env_lines.append("\n[你对这位用户的长期记忆（对话中逐步积累，请默认采信，除非与新事实冲突）]\n" + mem)
    # 技能清单：skills/*.md 是沉淀下来的技能文档（踩过的坑、正确姿势）。
    # 这里只注入「有哪些技能」，具体内容让模型按需 read_file 去读，省上下文。
    try:
        _sk = Path(__file__).resolve().parent / "skills"
        if _sk.is_dir():
            _rows = []
            for _f in sorted(_sk.glob("*.md")):
                _t = ""
                try:
                    for _l in _f.read_text(encoding="utf-8", errors="replace").splitlines():
                        if _l.strip().startswith("#"):
                            _t = _l.lstrip("# ").strip()
                            break
                except Exception:
                    pass
                _rows.append("- " + _f.stem + ("（" + _t + "）" if _t else ""))
            if _rows:
                env_lines.append(
                    "\n[可用技能] 这些是本机沉淀的技能文档，做相关任务前先用 read_file 读它，"
                    "里面记着踩过的坑和正确姿势（路径 ~/.termux-agent/skills/名字.md）：\n"
                    + "\n".join(_rows))
    except Exception:
        pass
    # 当前任务清单：todo.json 是落盘的，不受对话历史压缩影响，每轮都注入。
    # 2026-09-29 加：症状是模型改完代码（会重启）或长对话被压缩后，就"想不起来刚才在做什么"，
    # 甚至跑去翻磁盘、对用户说"那是在另一个对话里"——而用户看到的一直是同一个对话。
    try:
        # 2026-10-01：任务清单同样改由 build_turn_context() 挂到对话末尾。
        # （它每完成一步就变，是所有注入源里变动最频繁的一个。）
        _td = {"items": []}
        _its = []
        if _its:
            _rows = [("已完成：" if x.get("done") else "未完成：") + x["text"] for x in _its[:12]]
            env_lines.append(
                "\n[当前任务清单] 用户交代的待办（来自本机 todo.json，不受对话压缩影响）：\n"
                + "\n".join(_rows)
                + "\n处理原则：① 接手前先看这份清单，已完成的不重做，未完成的接着做；"
                  "② 如果用户提到的旧任务不在这里、你也想不起来，那是被上下文压缩掉了 —— "
                  "先用 read_file/grep 去 ~/.termux-agent/archive/ 和 sessions/ 的会话原文里找回来，"
                  "③ 但绝不要对用户说「那是在另一个对话里」：对他而言始终是同一个对话，"
                  "这样只会让他觉得你在推脱。")
    except Exception:
        pass
    name = (cfg.get("assistant_name") or "Sidekick").strip()
    return (
        f"你是「{name}」，一个运行在用户设备本地的命令行 AI 助手，通过工具真实地操作用户的设备。\n\n"
        "行为准则：\n"
        "1. 需要了解环境（文件、系统状态）时，先用工具去看，不要凭空猜测或让用户手动查。\n"
        "2. 修改文件前先读原文件；不确定路径时先用 list_dir 或 bash 确认。\n"
        "3. 每次只做用户要求的事，不要顺手重构无关代码、不要自作主张安装大体积依赖。\n"
        "4. bash 命令要幂等、可验证。删改类操作先说明再执行。\n"
        "5. 工具报错时，读错误信息并针对性修正，不要盲目重复同一条命令。\n"
        "6. 完成后简明汇报做了什么、结果如何；不要罗列无关细节。\n"
        "7. 遇到 3 步以上的任务（多处改代码、装/配应用、排障、整理资料）先用 todo_write 拆成 2~6 步，"
        "然后每完成一步立刻更新清单（该步 done 置 true），不要攒到最后一次性更新；"
        "用户界面上方会实时显示这份清单。被停止、断线、重连后继续做时，先按清单从没打勾的那步接着做。\n"
        "8. 一次回复里可以同时发出多个工具调用：查多个文件、搜多个关键词这类互不依赖的只读操作"
        "请一次全发出来，系统会并行执行，比一个一个来快得多（写操作仍按顺序执行）。\n"
        "9. 工具输出过长时不会丢：完整内容会落盘并在结果里给出路径，"
        "需要中间部分就用 read_file 分段读（offset 可为负数看结尾）或 grep 检索那个文件。\n"
        "10. 耗时不可预期的长任务（装大包、长时间下载、批量处理）用 bash 的 background=true 丢到后台，"
        "立刻拿到 pid 和日志路径，再用 read_file/grep 查进度，不要用大 timeout 硬等。\n"
        "11. 一处改动用 edit_file；同一文件多处改动、或要同时改多个文件时，用 apply_patch "
        "一次搞定（原子生效，不会改一半）。\n"
        "12. edit_file 若提示片段「不唯一」，错误信息里会列出每处出现的行号与上下文，"
        "照着缩小范围、把前后几行一起写进 old_string 即可。\n"
        f"13. 用{cfg.get('language') or '中文'}回复用户。\n"
        "14. 需要**用户拍板**才能继续时（花钱、删除或覆盖数据、装卸 App、方案取舍、"
        "你拿不到的信息），用 ask_user 弹出选择面板让他挑，可以一次问多个问题；"
        "能从文件/日志/网络查到、或按常理能决定的小事不要问，直接做，"
        "并在汇报里说明你自己的选择。\n\n"
        "15. 回复用 Markdown 排版，界面能渲染：表格、`行内代码`、代码块、**加粗**、"
        "有序/无序列表、引用（> ）、分割线（---）、链接 [文字](https://…) 都支持。"
        "另外还支持两种「富媒体」写法，合适时尽管用：\n"
        "    · 卡片：单独一行写 `:::card 标题`，下面写正文，再单独一行 `:::` 收尾；"
        "第一行的 card 也可换成 tip / warn / danger，卡片会带对应颜色，里面能放列表、表格、代码块。\n"
        "    · HTML 子集：可直接写 details/summary（折叠）、kbd、mark、sub、sup、img、"
        "table、div 等标签；不在白名单里的标签会被转义成普通文字，不会执行。\n"
        "    · 内联 SVG：可整块写 <svg>…</svg>（rect、circle、path、text、linearGradient、"
        "stop 等都支持，viewBox / linearGradient 这类驼峰写法会原样保留），"
        "适合画布局图、示意图、简单图形。\n"
        "    简单问题一句话说清就行，别为了好看硬塞卡片和表格。\n\n"
        "当前运行环境：\n" + "\n".join(env_lines)
    )


def build_system_prompt_en(cfg: dict) -> str:
    """系统提示词的英文版。与中文版内容一一对应，供界面语言为英文时使用。"""
    if local_lite(cfg):
        name = (cfg.get("assistant_name") or "Sidekick").strip()
        return (f"You are “{name}”, a local AI assistant running on the user's tablet "
                "(offline, and on the slow side). Answer concisely in English, get straight "
                "to the point, no waffle. You cannot use tools right now — answer from your "
                "existing knowledge only.")
    env_lines = [
        f"Working directory: {cfg['workdir']}",
        f"OS: {'Android / Termux' if is_termux() else sys.platform}",
        f"Python: {sys.version.split()[0]}",
        f"shell: {termux_shell()}",
    ]
    if is_termux():
        env_lines += [
            "Important facts about this device (memorise them and avoid basic mistakes):",
            "- This is a Termux environment on an Android device, not a regular Linux server.",
            "- No root access. Don't try sudo / su — it will fail.",
            "- Install software with `pkg install <name>` (not apt/yum).",
            "- The home directory is Termux's private $HOME "
            "(/data/data/com.termux/files/home) and won't be cleared by Android.",
            "- To reach Android shared storage (photos, Downloads) run `termux-setup-storage` "
            "first; afterwards the path is ~/storage/shared/.",
            "- There is no systemd. For background daemons use nohup or termux-wake-lock, "
            "never systemctl.",
            "- Resources are limited: avoid high-memory jobs and page large output.",
            "- Downloading installers/large files: use the download tool (streams to disk, "
            "no truncation, retries on timeout), save under ~/storage/shared/Download/ and "
            "tell the user where the file is when done.",
            "- Installing an APK: download the .apk with download first, then run "
            "`pm install -r <path>` via sysshell (a system-level install, no confirmation "
            "needed). Failures are usually incompatibility or a signature issue — relay "
            "pm's error message to the user verbatim.",
        ]
        env_lines += [
            "About yourself (you can modify yourself):",
            f"- Your own source code is {Path(__file__).resolve()}, a single-file Python "
            "program you can inspect with read_file and change with edit_file.",
            "- To fix your own bugs or add features, use the `selfupdate` tool (not "
            "write_file): it auto-backs up, syntax-checks (restoring on failure), and the "
            "service restarts with the new code; if the new code won't start, the watchdog "
            "rolls back.",
            "- The right way to self-update: (1) read_file the relevant code, (2) work out "
            "the minimal change, (3) call selfupdate with patch_old/patch_new. Keep changes "
            "small and local; avoid large rewrites.",
            "- After a self-update the current turn ends (the service restarts). You can "
            "verify afterwards, but if it didn't come up the user will tell you.",
            "- Your conversation history, long-term memory and session files all survive "
            "the restart.",
        ]
        if cfg.get("shell_access"):
            env_lines += [
                "- You can run commands via the `sysshell` tool as the shell (adb) user, "
                "which outranks a normal app: it can read system settings, dumpsys, "
                "getprop, pm/am and change global settings. When plain bash gets a "
                "permission denial, switch to sysshell.",
                "- sysshell is still not root: it can't read other apps' private data or "
                "write system read-only partitions.",
                "- If sysshell reports it can't reach adb: with a computer, run "
                "`adb tcpip 5555` there; without one, enable Wireless debugging on the "
                "tablet and pair once with `agent adbpair` — it stays paired afterwards.",
            ]
    mem = ""
    if mem:
        env_lines.append("\n[Your long-term memory of this user (accumulated during "
                         "conversation; trust it by default unless it conflicts with new "
                         "facts)]\n" + mem)
    try:
        _sk = Path(__file__).resolve().parent / "skills"
        if _sk.is_dir():
            _rows = []
            for _f in sorted(_sk.glob("*.md")):
                _t = ""
                try:
                    for _l in _f.read_text(encoding="utf-8", errors="replace").splitlines():
                        if _l.strip().startswith("#"):
                            _t = _l.lstrip("# ").strip()
                            break
                except Exception:
                    pass
                _rows.append("- " + _f.stem + (" (" + _t + ")" if _t else ""))
            if _rows:
                env_lines.append(
                    "\n[Available skills] These are distilled skill docs (pitfalls and "
                    "correct approaches found the hard way). Read the relevant one with "
                    "read_file before doing that kind of task (path "
                    "~/.termux-agent/skills/<name>.md):\n"
                    + "\n".join(_rows))
    except Exception:
        pass
    try:
        _td = {"items": []}
        _its = []
        if _its:
            _rows = [("Done: " if x.get("done") else "Not done: ") + x["text"] for x in _its[:12]]
            env_lines.append(
                "\n[Current task list] The user's outstanding to-dos (from the local "
                "todo.json, unaffected by conversation compression):\n"
                + "\n".join(_rows)
                + "\nHow to handle it: (1) check this list before taking over — don't redo "
                  "finished items, continue the unfinished ones; (2) if an old task the user "
                  "mentions isn't here and you can't recall it, it was compressed out of "
                  "context — go find it with read_file/grep in ~/.termux-agent/archive/ and "
                  "the session transcripts under sessions/; (3) never tell the user \"that "
                  "was in another conversation\" — to them it is always the same conversation, "
                  "and it just sounds like you're dodging.")
    except Exception:
        pass
    name = (cfg.get("assistant_name") or "Sidekick").strip()
    return (
        f"You are “{name}”, a command-line AI assistant running locally on the user's "
        "device, operating it for real through tools.\n\n"
        "Rules of conduct:\n"
        "1. When you need to know the environment (files, system state), inspect it with "
        "tools first — never guess or ask the user to check for you.\n"
        "2. Read a file before changing it; when unsure of a path, confirm with list_dir "
        "or bash first.\n"
        "3. Do exactly what the user asked — don't refactor unrelated code or install "
        "large dependencies on your own initiative.\n"
        "4. bash commands must be idempotent and verifiable. Explain before executing "
        "anything destructive.\n"
        "5. When a tool errors, read the message and fix it accordingly; don't blindly "
        "repeat the same command.\n"
        "6. Report briefly what you did and how it went; skip irrelevant detail.\n"
        "7. For tasks of 3+ steps (multi-file edits, installing/configuring apps, "
        "troubleshooting, organising material), break them into 2–6 steps with todo_write "
        "and update the list the moment each step finishes (mark that step done=true) "
        "rather than saving it all up; the user sees this list live above the input box. "
        "After being stopped, disconnected or reconnected, pick up from the first "
        "unchecked step.\n"
        "8. You may emit several tool calls in one reply: independent read-only work "
        "(reading several files, searching several keywords) should all go out at once — "
        "the system runs them in parallel, much faster than one at a time (writes still "
        "run in order).\n"
        "9. Long tool output is never lost: the full content is written to disk and its "
        "path given in the result. For the middle part, page through it with read_file "
        "(negative offset reads from the end) or grep that file.\n"
        "10. For long-running jobs of unpredictable duration (installing big packages, "
        "long downloads, batch processing) use bash with background=true to detach them, "
        "get the pid and log path immediately, then poll with read_file/grep — don't burn "
        "a big timeout waiting.\n"
        "11. Use edit_file for a single change; for several changes in one file, or "
        "changes across files, use apply_patch once (atomic — it won't half-apply).\n"
        "12. If edit_file says the snippet isn't unique, the error lists every occurrence "
        "with line numbers and context — narrow it down by including a few surrounding "
        "lines in old_string.\n"
        "13. Reply to the user in English.\n"
        "14. When a decision must be made by the user (spending money, deleting or "
        "overwriting data, installing/removing apps, choosing between approaches, "
        "information you can't obtain), use ask_user to show a choice panel — you can ask "
        "several questions at once. For small things you can find in files/logs/the web, "
        "or decide by common sense, don't ask: just do it and note your choice in the "
        "report.\n\n"
        "15. Format replies in Markdown; the UI renders it: tables, `inline code`, code "
        "blocks, **bold**, ordered/unordered lists, blockquotes (> ), horizontal rules "
        "(---) and links [text](https://…) are all supported. Two kinds of rich media are "
        "also available — use them where fitting:\n"
        "    · Cards: on its own line write `:::card Title`, then the body, then a closing "
        "line `:::`. The first line may also be tip / warn / danger for a coloured card "
        "that can hold lists, tables and code blocks.\n"
        "    · HTML subset: you may write details/summary (collapsible), kbd, mark, sub, "
        "sup, img, table, div and similar tags; tags outside the whitelist are escaped to "
        "plain text and never executed.\n"
        "    · Inline SVG: a whole <svg>…</svg> block works (rect, circle, path, text, "
        "linearGradient, stop etc.), and camelCase attributes such as viewBox / "
        "linearGradient are preserved — good for layout diagrams, schematics and simple "
        "shapes.\n"
        "    Keep simple questions to one sentence; don't pad them with cards and tables "
        "just for looks.\n\n"
        "Current environment:\n" + "\n".join(env_lines)
    )


# ---------------------------------------------------------------- API 客户端

class ApiError(Exception):
    pass


CURRENT_STOP = [None]      # 当前任务的「停止」标志，供深层调用链读取（None = 当前没在跑任务）


class CancelledByUser(Exception):
    """用户点了「停止」—— 用来把阻塞中的请求立刻打断。"""
    pass


def _http_json(url: str, payload: dict, api_key: str, timeout: int, stop_evt=None):
    if stop_evt is None:
        stop_evt = CURRENT_STOP[0]      # 没显式传参就用当前任务的停止标志
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}",
                 "Accept": "text/event-stream"},
    )
    if stop_evt is None:
        return urllib.request.urlopen(req, timeout=timeout)
    # 可打断版：请求丢给子线程去等，主线程每 0.2 秒瞄一眼「停止」标志，
    # 一旦点停就立刻放弃等待退出（子线程是 daemon，随连接自然收尾）。
    box = {}

    def _worker():
        try:
            box["resp"] = urllib.request.urlopen(req, timeout=timeout)
        except BaseException as e:
            box["err"] = e

    th = threading.Thread(target=_worker, daemon=True)
    th.start()
    while th.is_alive():
        if stop_evt.is_set():
            raise CancelledByUser("用户已停止")
        th.join(0.2)
    if "err" in box:
        raise box["err"]
    return box["resp"]


def _http_detail(e: urllib.error.HTTPError) -> str:
    """取服务端返回的错误说明。

    body 只能读一次，读第二次就是空的 —— 所以缓存起来，
    否则重试路径上再调用一次就会显示「服务端返回: 」（啥也看不到）。
    """
    cached = getattr(e, "_detail_cache", None)
    if cached is not None:
        return cached
    try:
        body = e.read().decode("utf-8", "ignore")
    except Exception:
        body = ""
    try:
        obj = json.loads(body)
        out = (obj.get("error") or {}).get("message") or body[:400]
    except Exception:
        out = body[:400]
    try:
        e._detail_cache = out
    except Exception:
        pass
    return out


def _friendly_http_error(e: urllib.error.HTTPError) -> str:
    detail = _http_detail(e)
    hints = {
        401: "API Key 无效或已过期。检查 ~/.termux-agent/config.json 里的 api_key。",
        402: "账户余额不足。",
        429: "请求过快或额度用尽，稍后重试。",
        400: "请求格式被拒绝（可能是模型名不对，或参数不被支持）。",
        503: "服务端繁忙，稍后重试。",
    }
    return f"HTTP {e.code} {e.reason}\n  {hints.get(e.code, '')}\n  服务端返回: {detail}"


# 这些错误码是服务端临时问题，自动重试往往能好
_RETRYABLE_CODES = (429, 500, 502, 503, 504)


def net_reachable(base_url: str, timeout: float = 6.0) -> bool:
    """快速探测模型服务器是否可达。

    存在的意义：请求挂死时默认要等 timeout_sec（300 秒）才报错，用户看到的是
    "一直思考中"。这个前置探测只花几十毫秒，不通就立刻给明确提示。
    """
    try:
        u = urllib.parse.urlparse(base_url or "")
        host = u.hostname or "api.deepseek.com"
        port = u.port or (443 if (u.scheme or "https") == "https" else 80)
        s = socket.create_connection((host, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def try_wake_lock() -> bool:
    """尝试持有唤醒锁（防系统冻结）。

    坑：Termux:API 应用缺失时，`termux-wake-lock` 会 **静默返回 0**，
    光看退出码会误判成成功。所以先用 termux-api 包里的另一个脚本
    （termux-battery-status，只有装了 termux-api 才存在）判定 API 是否可用。
    """
    if not is_termux():
        return False
    exe = shutil.which("termux-wake-lock")
    if not exe:
        return False
    if not shutil.which("termux-battery-status"):
        # termux-api 包没装 → API 通道不可用 → 唤醒锁只是空转
        return False
    try:
        r = subprocess.run([exe], capture_output=True, timeout=20, text=True, errors="replace")
        out = ((r.stdout or "") + (r.stderr or "")).lower()
        if r.returncode != 0:
            return False
        for bad in ("not installed", "not found", "no such", "error", "denied"):
            if bad in out:
                return False
        # 命令存在 + 无报错 → 认为已尝试持有（Termux:API 未装时会静默无效，无法进一步判断）
        return True
    except Exception:
        return False


WAKE_LOCK_STATE = {"ok": None}          # None=未检测 / True=已持有 / False=不可用


def wake_lock_loop() -> None:
    """周期性尝试持有唤醒锁。

    装了 Termux:API 应用后会自动开始生效（无需重启服务）；没装则记为不可用。
    """
    time.sleep(5)
    while True:
        ok = try_wake_lock()
        WAKE_LOCK_STATE["ok"] = ok
        try:
            with open(APP_DIR / "wakelock.log", "a", encoding="utf-8") as f:
                f.write("%s %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                     "已持有" if ok else "不可用（Termux:API 未安装或调用失败）"))
        except Exception:
            pass
        time.sleep(1800 if ok else 600)


_IMG_MARK = re.compile(r"\[我发了一张图片，已保存到 ([^\]\n]+)\]")


def _image_data_url(path: str):
    """把本地图片读成 data: URL；HEIC 先转 JPEG，读不了就返回 None。"""
    try:
        p = Path(path) if os.path.isabs(path) else (APP_DIR / path)
    except Exception:
        return None
    try:
        if not p.is_file():
            return None
        ext = p.suffix.lower()
        if ext in (".heic", ".heif"):
            exe = shutil.which("heif-convert")
            if not exe:
                return None
            dst = APP_DIR / "tmp" / (p.stem + ".jpg")
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                subprocess.run([exe, "-q", "82", "--quiet", str(p), str(dst)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=240, check=True)
                p = dst
            except Exception:
                return None
        if p.stat().st_size > 8 * 1024 * 1024:
            return None
        mime = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif",
                ".bmp": "image/bmp"}.get(p.suffix.lower(), "image/jpeg")
        return "data:%s;base64,%s" % (mime, base64.b64encode(p.read_bytes()).decode())
    except Exception:
        return None


def with_latest_image(messages: list) -> list:
    """最新一条用户消息里带图片路径标注时，把图附上（多模态），让模型直接看到。

    只在「用户刚发来、还没回复」的那一轮生效，历史里的标注保持纯文本，
    避免每轮都重传图片。返回新列表，不改动原 messages。
    """
    if not messages:
        return messages
    idx = len(messages) - 1
    if messages[idx].get("role") != "user":
        return messages
    content = messages[idx].get("content")
    if not isinstance(content, str) or "[我发了一张图片" not in content:
        return messages
    parts = [{"type": "text", "text": content}]
    for path in _IMG_MARK.findall(content)[-3:]:
        url = _image_data_url(path.strip())
        if url:
            parts.append({"type": "image_url", "image_url": {"url": url}})
    if len(parts) == 1:
        return messages
    out = list(messages)
    out[idx] = dict(messages[idx], content=parts)
    return out


def stream_completion(cfg: dict, messages: list, tools: list, max_tokens: int = 0):
    """向 DeepSeek 发起流式请求，逐块 yield 解析后的 chunk。

    产出元组 (kind, data)：
      ("reasoning", str) 思维链增量   ("text", str) 正文增量
      ("tool_call", dict) 累积中的工具调用   ("usage", dict) 用量
      ("done", str) finish_reason
    """
    msgs = messages
    if local_lite(cfg) and len(msgs) > 8:
        # 本地模型：历史也精简（只留 system + 最近几条纯对话），避免每轮都啃几万字符
        keep = [m for m in msgs[1:] if m.get("role") in ("user", "assistant")
                and str(m.get("content") or "").strip()]
        msgs = msgs[:1] + keep[-6:]
    payload = {
        "model": cfg["model"],
        "messages": trim_old_reasoning(with_latest_image(msgs)),
        "stream": True,
        "max_tokens": int(max_tokens or cfg.get("max_tokens") or DEFAULT_MAX_TOKENS),
    }
    if cfg.get("temperature") is not None:
        payload["temperature"] = float(cfg["temperature"])
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    if cfg.get("send_usage", True):
        payload["stream_options"] = {"include_usage": True}

    url = cfg["base_url"].rstrip("/") + "/chat/completions"

    last_err = None
    for attempt in range(3):
        try:
            resp = _http_json(url, payload, cfg["api_key"], int(cfg.get("timeout_sec") or 300))
            break
        except urllib.error.HTTPError as e:
            detail = _http_detail(e).lower()
            # 上下文过长 —— 重试没用，给出可识别、可操作的错误
            if e.code == 400 and ("context" in detail or "length" in detail
                                  or "token" in detail or "maximum" in detail):
                raise ApiError("对话内容太长，超出模型上下文上限。请点「新对话」开始新的一段，"
                               "或换一个上下文更长的模型。\n  服务端返回: " + detail) from e
            # 少数兼容端点不支持 stream_options
            if e.code == 400 and "stream_options" in payload:
                payload.pop("stream_options", None)
                last_err = e
                continue
            # 服务端临时故障 → 自动重试
            if e.code in _RETRYABLE_CODES:
                last_err = e
                time.sleep(1.5 * (attempt + 1))
                continue
            raise ApiError(_friendly_http_error(e)) from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # 网络中断 / 超时（例如进程被系统冻结后连接失效）→ 重试
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    else:
        if isinstance(last_err, urllib.error.HTTPError):
            raise ApiError(_friendly_http_error(last_err))
        raise ApiError(f"网络无法连接 {url}，已重试 3 次仍失败。" + (f"（{last_err}）" if last_err else ""))

    pending_calls = {}
    with resp:
        _stop = CURRENT_STOP[0]
        for raw in resp:
            if _stop is not None and _stop.is_set():     # 用户点了停止 → 立刻收工，别再等模型说完
                return
            line = raw.decode("utf-8", "ignore").strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except Exception:
                continue

            if chunk.get("usage"):
                yield ("usage", chunk["usage"])

            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}

            if delta.get("reasoning_content"):
                yield ("reasoning", delta["reasoning_content"])
            if delta.get("content"):
                yield ("text", delta["content"])

            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = pending_calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] = fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
                yield ("tool_call", dict(slot, index=idx))

            if choice.get("finish_reason"):
                yield ("done", choice["finish_reason"])

    # 收尾：把完整工具调用一次性交出，便于上层组装消息
    if pending_calls:
        yield ("tool_calls_final", [pending_calls[i] for i in sorted(pending_calls)])


# ---------------------------------------------------------------- 工具实现

SPILL_DIR = APP_DIR / "tmp"
SPILL_KEEP = 30                # 落盘的完整输出最多留这么多份，更早的清掉
_SPILL_SEQ = [0]


def _spill_path(kind: str) -> Path:
    SPILL_DIR.mkdir(parents=True, exist_ok=True)
    _SPILL_SEQ[0] += 1
    return SPILL_DIR / f"{kind}-{time.strftime('%m%d-%H%M%S')}-{_SPILL_SEQ[0]}.log"


def _spill(text: str, kind: str = "out") -> str:
    """把超长内容整份写到 tmp/ 下，返回路径（失败返回空串）。

    对照 codex 的做法：长输出不硬截断丢掉，而是落盘留查，模型可以分段读或检索。
    """
    try:
        p = _spill_path(kind)
        p.write_text(text, encoding="utf-8")
        try:                                   # 各留最近若干份，别把磁盘堆满
            for pat, keep in (("out-*.log", SPILL_KEEP), ("bg-*.log", 12)):
                olds = sorted(SPILL_DIR.glob(pat), key=lambda x: x.stat().st_mtime,
                              reverse=True)[keep:]
                for f in olds:
                    if pat.startswith("bg") and time.time() - f.stat().st_mtime < 6 * 3600:
                        continue               # 后台任务可能还在往里写，先别动
                    f.unlink(missing_ok=True)
        except OSError:
            pass
        return str(p)
    except Exception:
        return ""


def refresh_tool_cap(used_chars: int, compress_at: int) -> int:
    """按上下文压力决定单条工具输出的预览上限：越接近压缩阈值越省。

    完整内容照样落盘，需要时用 read_file/grep 去那个文件里查，
    所以压力大时缩小预览只影响「一眼能看多少」，不影响能否查证。
    """
    r = used_chars / max(1, compress_at)
    for edge, cap in ((0.60, 4000), (0.40, 8000), (0.20, 16000)):
        if r >= edge:
            TOOL_CAP[0] = cap
            return cap
    TOOL_CAP[0] = MAX_TOOL_OUTPUT
    return MAX_TOOL_OUTPUT


def _truncate(text: str) -> str:
    cap = TOOL_CAP[0]          # 按上下文压力动态收缩（见 refresh_tool_cap）
    if len(text) <= cap:
        return text
    keep = cap // 2
    full = _spill(text)
    lines = text.count("\n") + 1
    extra = ""
    if full:
        extra = (f"\n[完整输出已存到] {full}（共 {lines} 行 / {len(text)} 字符）"
                 f"\n想看中间被略去的部分：用 read_file 分段读（offset/limit，offset 可用负数从末尾数），"
                 f"或用 grep 在这个文件里检索关键词。")
    return (text[:keep]
            + f"\n\n... [输出过长已截断，共 {len(text)} 字符，此处略去 {len(text) - 2 * keep} 字符] ..."
            + extra
            + "\n\n"
            + text[-keep:])


class ToolRuntime:
    """单轮工具执行的运行时上下文：把进度推给界面、被「停止」时真杀掉子进程。

    注意：只在一次 run_turn 内有效，下一轮会整体替换（旧对象被丢弃即可）。
    """

    def __init__(self, emit=None, cancel=None):
        self.emit = emit
        self.cancel = cancel
        self.key = ""
        self._procs = []
        self._lock = threading.Lock()

    @property
    def stopped(self) -> bool:
        return bool(self.cancel is not None and self.cancel.is_set())

    def begin(self, key: str, label: str, detail: str = "") -> None:
        """告诉界面「这一步开始了」，好让步骤行先亮出来、边跑边看输出。"""
        self.key = key
        if not self.emit:
            return
        try:
            self.emit("step_begin", {"key": key, "label": label, "detail": detail})
        except Exception:
            pass

    def tail(self, text: str) -> None:
        if not (self.emit and self.key):
            return
        try:
            self.emit("step_out", {"key": self.key, "text": text})
        except Exception:
            pass

    def register(self, proc) -> None:
        with self._lock:
            self._procs.append(proc)

    def unregister(self, proc) -> None:
        with self._lock:
            try:
                self._procs.remove(proc)
            except ValueError:
                pass

    def kill_all(self) -> None:
        with self._lock:
            procs = list(self._procs)
        for pr in procs:
            _kill_proc(pr)


CURRENT_RUNTIME = None          # 当前这一轮的工具运行时（run_turn 开头替换）


def _runtime():
    return CURRENT_RUNTIME


def _kill_proc(proc) -> None:
    """结束一个子进程：优先整组杀（命令里 fork 出来的子进程一起收掉）。"""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _tail_text(buf: list) -> str:
    text = "".join(buf[-TAIL_MAX_LINES:])
    if len(text) > TAIL_MAX_CHARS:
        text = "…" + text[-TAIL_MAX_CHARS:]
    return text


def _exec_stream(argv: list, cwd, timeout: int, rt=None) -> dict:
    """跑一条命令：边跑边推输出尾部，能被「停止」打断，超时/被打断都会收掉整个进程组。

    返回 {out, rc, killed, error, elapsed}。error 非空表示连进程都没起来。
    """
    started = time.time()
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, text=True, errors="replace",
            start_new_session=True, bufsize=1,
        )
    except Exception as e:
        return {"out": "", "rc": None, "killed": "", "error": f"[错误] 无法启动命令: {e}",
                "elapsed": 0.0}

    buf = []
    done = threading.Event()

    def _reader():
        try:
            for line in proc.stdout:
                buf.append(line)
        except Exception:
            pass
        finally:
            done.set()

    th = threading.Thread(target=_reader, daemon=True)
    th.start()
    if rt is not None:
        rt.register(proc)

    killed = ""
    last_push = 0.0
    try:
        while True:
            rc = proc.poll()
            if rc is not None and done.is_set():
                break
            if not killed and rt is not None and rt.stopped:
                killed = "user"
                _kill_proc(proc)
            elif not killed and time.time() - started >= timeout:
                killed = "timeout"
                _kill_proc(proc)
            elif rt is not None and time.time() - last_push >= STREAM_TICK:
                last_push = time.time()
                if buf:
                    rt.tail(_tail_text(buf))
            time.sleep(0.12)
        th.join(timeout=3)
    finally:
        if rt is not None:
            rt.unregister(proc)

    out = "".join(buf)
    return {"out": out, "rc": proc.returncode, "killed": killed, "error": None,
            "elapsed": time.time() - started}


def tool_bash(cfg, command: str, timeout_sec: int = 120, background: bool = False) -> str:
    if not command or not command.strip():
        return "[错误] command 为空"
    shell = termux_shell()
    timeout_sec = max(1, min(int(timeout_sec or 120), 900))

    if background:                    # 长时间任务：丢到后台跑，立刻拿到 pid 与日志路径
        try:
            logp = _spill_path("bg").with_suffix(".log")
        except Exception as e:
            return f"[错误] 无法创建日志文件: {e}"
        try:
            with open(logp, "wb") as fh:
                proc = subprocess.Popen(
                    [shell, "-lc", command],
                    cwd=cfg["workdir"], stdout=fh, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, start_new_session=True,
                )
        except Exception as e:
            return f"[错误] 无法在后台启动命令: {e}"
        return (f"[后台] 已在后台启动，pid={proc.pid}\n"
                f"日志：{logp}\n"
                f"用法：用 read_file（offset 传负数看结尾，如 -60）或 grep 查看进度；"
                f"要停止就执行 `kill {proc.pid}`。\n"
                f"注意：后台任务不会随本轮结束而停止，也不再受超时限制。")

    r = _exec_stream([shell, "-lc", command], cfg["workdir"], timeout_sec, _runtime())
    if r["error"]:
        return r["error"]
    out, rc, elapsed = r["out"], r["rc"], r["elapsed"]
    if r["killed"] == "user":
        return (_truncate(out or "")
                + "\n[已停止] 命令被用户中断，进程已终止。"
                  "不要原样重跑；先想想是否还需要继续，或换成更小的步骤。")
    if r["killed"] == "timeout":
        return (_truncate(out or "")
                + f"\n[超时] 命令超过 {timeout_sec}s 被强制终止（整个进程组都已收掉，"
                  f"不会在后台残留）。上面只是它跑出的部分输出，不是完整结果。"
                + "\n[提示] 若这条命令本来就费时，别原样重试：改用 background=true 重跑它，"
                  "会立刻返回 pid 与日志路径、不受超时限制，之后用 read_file（offset 传负数看结尾）"
                  "或 grep 查日志；也可以先把任务拆成更小的步骤，"
                  "或把 timeout_sec 调大（上限 900s）。")
    head = f"[退出码 {rc} | 耗时 {elapsed:.2f}s]\n"
    body = out if (out and out.strip()) else "(无输出)"
    note = ""
    if rc != 0:
        hint = ""
        low = (out or "").lower()
        if rc == 127 or "command not found" in low or "not found" in low:
            hint = "（退出码 127 通常表示命令不存在：先用 `pkg install <包名>` 安装，别猜包名。）"
        elif rc == 126 or "permission denied" in low:
            hint = "（退出码 126 / Permission denied 通常是权限不足：检查 chmod +x，或用 sysshell 工具执行。）"
        elif rc == 137:
            hint = "（退出码 137 表示进程被系统杀掉：多半是内存不足，把任务拆小或分页处理。）"
        brief = " ".join((command or "").split())
        if len(brief) > 300:
            brief = brief[:300] + f"…（原命令共 {len(command)} 字符，已截断显示）"
        note = (f"\n[出错命令] {brief}\n"
                "[提示] 退出码非 0，请根据上面的错误信息调整命令，不要原样重试。"
                + (" " + hint if hint else ""))
    return _truncate(head + body + note)


def _resolve(cfg, path: str) -> Path:
    p = Path(os.path.expanduser(path or "."))
    if not p.is_absolute():
        p = Path(cfg["workdir"]) / p
    return p


def _missing_path_hint(p: Path) -> str:
    """路径不存在时给出可照做的线索：上级目录是否存在、同目录下名字最接近的条目。"""
    import difflib
    notes = []
    parent = p.parent
    if parent.exists():
        scope = parent
    else:
        anc = parent
        while anc.parent != anc and not anc.exists():
            anc = anc.parent
        notes.append(f"上级目录 {parent} 也不存在，最近的已存在目录是 {anc}")
        scope = anc
    try:
        names = sorted(e.name for e in scope.iterdir())[:300]
    except Exception:
        return ("\n提示：" + "；".join(notes)) if notes else ""
    base = p.name or str(p)
    near = difflib.get_close_matches(base, names, n=3, cutoff=0.55)
    if not near:
        low = base.lower()
        near = [n for n in names if n.lower() == low or low in n.lower() or n.lower() in low][:3]
    if near:
        notes.append(f"{scope} 下名字最接近的是 " + "、".join(repr(n) for n in near) + "，确认后重试")
    elif names:
        notes.append(f"{scope} 下没有名字相近的条目（共 {len(names)} 项），可先用 list_dir 查看")
    return ("\n提示：" + "；".join(notes)) if notes else ""


def _explain_os_error(p, e: Exception) -> str:
    """读文件/列目录失败时，把系统 errno 翻成能照做的中文提示。

    此前只回一句英文原始报错（如 PermissionError: [Errno 13] ...），
    Agent 与用户都无从判断该改路径还是改权限。
    """
    import errno as _errno
    s, err = str(p), getattr(e, "errno", None)
    if isinstance(e, PermissionError) or err in (_errno.EACCES, _errno.EPERM):
        if "/storage/" in s or "/sdcard/" in s:
            note = (f"{p} 所在位置无读权限：共享存储要先执行 termux-setup-storage "
                    "并重启 Termux 进程才生效；也可先复制到 ~/ 下再读")
        else:
            note = (f"{p} 无读权限：多半是其它应用的私有目录（Termux 读不了），"
                    "请改读 ~/ 下的文件，或改用 sysshell 试试")
    elif isinstance(e, NotADirectoryError):
        note = f"{p} 中间有一段不是目录（路径层级写错了），用 list_dir 逐层核对"
    elif isinstance(e, IsADirectoryError) or err == _errno.EISDIR:
        note = f"{p} 是目录不是文件，请改用 list_dir"
    elif isinstance(e, FileNotFoundError) or err == _errno.ENOENT:
        note = f"{p} 或它的上级目录已不存在，可能刚被移动/删除，用 list_dir 重新确认"
    elif err == _errno.ELOOP:
        note = "符号链接成环或指向自身（软链接套太多层），请改用真实路径"
    elif err == _errno.ENAMETOOLONG:
        note = "路径太长超过系统上限，请减少目录层级或用更短的文件名"
    elif err == _errno.EIO:
        note = "磁盘 I/O 错误，文件可能已损坏，可先备份再重建"
    else:
        note = "原因见下面的原始报错"
    return f"\n提示：{note}；原始报错：{type(e).__name__}: {e}"


BIG_FILE_BYTES = 6 * 1024 * 1024   # 超过这个大小的文件不再整份读进内存，改流式分段读
MAX_READ_LINES = 2000              # read_file 单次最多返回的行数，limit 超上限就按上限返回，防止一次撑爆上下文


def _tail_lines(p: Path, want: int) -> tuple:
    """只取文件末尾 want 行：从尾部倒着读块，内存里不装整份文件。

    返回 (行列表, 是否读到了文件开头)。行列表始终是文件尾部的内容，所以「倒数第几行」一定准。
    """
    block = 256 * 1024
    cap = 24 * 1024 * 1024
    buf, pos, size = b"", 0, p.stat().st_size
    with p.open("rb") as f:
        while pos < size:
            step = min(block, size - pos)
            pos += step
            f.seek(size - pos)
            buf = f.read(step) + buf
            if buf.count(b"\n") > want or len(buf) >= cap:
                break
    return buf.decode("utf-8", errors="replace").splitlines()[-want:], pos >= size


def _stream_page(p: Path, off: int, limit: int) -> dict:
    """超大文件分段读：一次只在内存里放一页。

    返回 {chunk, first, from_end, more, total}；from_end 为 True 时 first 按「倒数第几行」计
    （超大文件的绝对行号没数过，只有从尾部一路读到文件头才确定）。
    """
    if off < 0:                                   # 负数：只要文件尾部，从后往前读块，不扫全文件
        want = min(abs(off) + limit, 500000)
        tail, whole = _tail_lines(p, want)
        if whole:                                 # 读到了文件开头，总行数已知，行号能精确算
            first = max(1, len(tail) + off + 1)
            chunk = tail[first - 1: first - 1 + limit]
            return {"chunk": chunk, "first": first, "from_end": False,
                    "more": first - 1 + len(chunk) < len(tail), "total": len(tail)}
        idx = max(0, len(tail) - abs(off))         # 本页第一行 = 倒数第 len(tail)-idx 行
        chunk = tail[idx: idx + limit]
        return {"chunk": chunk, "first": len(tail) - idx, "from_end": True,
                "more": idx + limit < len(tail), "total": None}
    first = max(1, off)
    chunk, more = [], False
    with p.open("r", encoding="utf-8", errors="replace") as f:
        for i, ln in enumerate(f):                 # 逐行流式读，跳过的行不留在内存里
            if i < first - 1:
                continue
            if len(chunk) < limit:
                chunk.append(ln.rstrip("\r\n"))
            else:
                more = True
                break
    return {"chunk": chunk, "first": first, "from_end": False, "more": more, "total": None}


def tool_read_file(cfg, path: str, offset: int = 1, limit: int = 800) -> str:
    p = _resolve(cfg, path)
    if not p.exists():
        return f"[错误] 文件不存在: {p}" + _missing_path_hint(p)
    if p.is_dir():
        return f"[错误] 这是目录不是文件: {p}，请用 list_dir"
    try:
        limit = max(1, int(limit or 800))
    except (TypeError, ValueError):
        limit = 800
    capped = limit > MAX_READ_LINES                # 超上限就按上限返回，并如实告知
    if capped:
        limit = MAX_READ_LINES
    try:
        off = int(offset or 1)
    except (TypeError, ValueError):
        off = 1
    try:
        size = p.stat().st_size
    except OSError:
        size = 0
    if size:                                       # 二进制文件按文本读只会刷一屏乱码，直接给出替代查法
        try:
            with p.open("rb") as fh:
                head = fh.read(8192)
        except Exception:
            head = None                            # 读不了就别乱猜，交给下面的分支报真实错误
        if head and b"\x00" in head:
            return (f"[错误] {p} 是二进制文件（{_fmt_size(size)}），按文本读只会是乱码。\n"
                    f"开头 16 字节: {head[:16].hex(' ')}\n"
                    "换这些方式看内容：用 bash 跑 file 看类型、"
                    "strings -n 8 文件 | head -50 抽可读字符串、xxd 文件 | head 看十六进制；"
                    "若是压缩包/安装包，先用 unzip -l / tar -tf 列目录。")
    if size > BIG_FILE_BYTES:                      # 超大文件：流式分段读，不整份进内存
        try:
            r = _stream_page(p, off, limit)
        except Exception as e:
            return "[错误] 读取失败。" + _explain_os_error(p, e)
        scope = f"文件很大（{_fmt_size(size)}，未数总行数）"
    else:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return "[错误] 读取失败。" + _explain_os_error(p, e)
        lines = text.splitlines()
        off = max(1, len(lines) + off + 1) if off < 0 else off   # 负数 = 从末尾往回数
        first = max(1, off)
        chunk = lines[first - 1: first - 1 + limit]
        r = {"chunk": chunk, "first": first, "from_end": False,
             "more": first - 1 + limit < len(lines), "total": len(lines)}
        scope = f"文件共 {len(lines)} 行"
    if not r["chunk"]:                             # offset 越界：把可用范围明确告诉模型
        if r["total"] is None:
            tip = (f"offset={offset} 已在文件末尾之后；这是一份大文件，"
                   f"想读尾部请用负数 offset（如 offset=-200）")
        else:
            tip = f"offset={offset} 超出范围，可用 1~{max(1, r['total'])}"
        return f"[文件] {p}\n（这一页是空的：{tip}；{scope}）"
    if r["from_end"]:                              # 行号按「倒数第几行」标注
        rows = "\n".join(f"{'倒%d' % (r['first'] - i):>7}\t{ln}"
                         for i, ln in enumerate(r["chunk"]))
        shown = f"倒数第 {r['first']} 行起的 {len(r['chunk'])} 行"
    else:
        rows = "\n".join(f"{r['first'] + i:>6}\t{ln}" for i, ln in enumerate(r["chunk"]))
        shown = f"第 {r['first']}-{r['first'] + len(r['chunk']) - 1} 行"
    if r["more"]:                                  # 明确给出下一页该传的 offset，模型不用自己算
        if r["from_end"]:
            nxt = r["first"] - len(r["chunk"])
            more_txt = (f"后面还有更多内容，继续读请用 offset=-{nxt}"
                        f"（负数=从末尾数第 {nxt} 行起）、limit={limit}")
        else:
            nxt = r["first"] + len(r["chunk"])
            more_txt = f"后面还有更多内容，继续读请用 offset={nxt}、limit={limit}"
    else:
        more_txt = "已到文件末尾，没有更多内容了"
    if capped:                                     # 说明被截到上限，模型才知道可以继续往后翻
        more_txt += f"（单次最多 {MAX_READ_LINES} 行，limit 已按上限处理）"
    return _truncate(f"[文件] {p}\n{rows}\n\n[{scope}；已显示{shown}；{more_txt}]")


def _disk_free_note(p: Path) -> str:
    """写失败时顺手报出目标分区还剩多少空间，省得用户再自己跑 df。"""
    import shutil
    try:
        d = p if p.is_dir() else p.parent
        while not d.exists() and d != d.parent:   # 目标还没建出来就往上找到已存在的目录
            d = d.parent
        u = shutil.disk_usage(str(d))
        return (f"\n磁盘剩余空间：{d} 所在分区可用 {_fmt_size(u.free)}"
                f" / 共 {_fmt_size(u.total)}")
    except Exception:
        return ""


def _write_error_hint(p: Path, e: Exception) -> str:
    """写文件/建目录失败时，把系统报错翻成能照做的中文提示。"""
    import errno
    s, err = str(p), getattr(e, "errno", None)
    if isinstance(e, IsADirectoryError):
        note = f"{p} 是目录不是文件，请换个文件名（可先用 list_dir 看一眼）"
    elif isinstance(e, PermissionError) or err == errno.EACCES:
        if "/storage/" in s or "/sdcard/" in s:
            note = (f"{p} 所在位置无写权限：共享存储要先执行 termux-setup-storage "
                    "并重启 Termux 进程才可写；也可先写到 ~/ 下再复制过去")
        else:
            note = f"{p} 无写权限（Termux 没有 root，系统目录写不了），请改写到 ~/ 下"
    elif isinstance(e, FileNotFoundError):
        note = f"上级目录 {p.parent} 不存在，先 mkdir -p 建好再写"
    elif err == errno.ENOSPC:
        note = ("磁盘空间不足，先删掉一些文件再写（可用 bash 跑 df -h ~ 看谁占得多，"
                "或 du -sh ~/* 2>/dev/null | sort -h | tail 找大文件）")
    elif err == errno.EROFS:
        note = "该位置是只读文件系统，请改写到 ~/ 下"
    else:
        note = "原因见下面的原始报错"
    return f"\n提示：{note}；原始报错：{type(e).__name__}: {e}" + _disk_free_note(p)


def tool_write_file(cfg, path: str, content: str) -> str:
    p = _resolve(cfg, path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content or "", encoding="utf-8")
    except Exception as e:
        return "[错误] 写入失败。" + _write_error_hint(p, e)
    n = len((content or "").splitlines())
    return f"[成功] {'覆盖' if existed else '新建'} {p}（{n} 行，{len(content or '')} 字符）"


def _near_miss_hint(old_string: str, text: str) -> str:
    """old_string 没匹配上时，在文件里找最接近的片段，给出可能的目标行号与原文。"""
    import difflib
    f_lines = text.splitlines()
    o_lines = [l.strip() for l in old_string.splitlines() if l.strip()]
    if not f_lines or not o_lines:
        return ""
    f_norm = [l.strip() for l in f_lines]
    sm = difflib.SequenceMatcher(None, o_lines, f_norm, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size > 1]
    if not blocks:
        near = difflib.get_close_matches(o_lines[0], f_norm, n=1, cutoff=0.75)
        if not near:
            return ""
        i = f_norm.index(near[0])
        return (f"\n提示：文件里最接近的是第 {i + 1} 行：{f_lines[i].strip()!r}"
                f"（多半是缩进或个别字符不一致）")
    b = max(blocks, key=lambda x: x.size)
    s = max(0, min(b.b - b.a, len(f_lines) - len(o_lines) - 1))
    e = min(len(f_lines), s + len(o_lines) + 1)
    body = "\n".join(f"{j + 1:>6}\t{f_lines[j]}" for j in range(s, e))
    return (f"\n提示：文件里最接近的位置在第 {s + 1} 行附近"
            f"（其中连续 {b.size} 行与 old_string 相同）：\n{body}")


_PATCH_FILE_RE = re.compile(r"^\s*\*\*\*\s*(Update|Add|Delete)\s+File:\s*(.+?)\s*$")
_PATCH_DIFF_HDR = re.compile(r"^\s*(?:---|\+\+\+)\s+(?:[ab]/)?(\S+)")
_PATCH_HUNK_RE = re.compile(r"^\s*@@")


def _strip_patch_marker(line: str) -> str:
    """补丁行去掉前缀标记：'+' / '-' / ' '（空行视作上下文空行）。"""
    if line[:1] in "+-":
        return line[1:]
    if line[:1] == " ":
        return line[1:]
    return line


def _parse_patch(patch: str) -> tuple:
    """把补丁文本解析成 [{op, path, hunks}]。

    同时认两种写法（都常见）：
      · *** Update File: a.py / *** Add File: b.txt / *** Delete File: c.txt
      · 标准 unified diff：--- a/x +++ b/x @@ -1,3 +1,4 @@
    """
    raw_text = (patch or "").replace("\r\n", "\n").replace("\r", "\n")
    if raw_text.endswith("\n"):          # 末尾换行只是行终止符，不是一行空上下文
        raw_text = raw_text[:-1]
    lines = raw_text.split("\n")
    items = []
    cur = None
    hunk = None
    seen_diff_hdr = False

    def flush_hunk():
        nonlocal hunk
        if cur is not None and hunk is not None and (hunk["old"] or hunk["new"]):
            cur["hunks"].append(hunk)
        hunk = None

    def flush_item():
        nonlocal cur
        flush_hunk()
        if cur is not None:
            items.append(cur)
        cur = None

    for raw in lines:
        m = _PATCH_FILE_RE.match(raw)
        if m:
            flush_item()
            cur = {"op": m.group(1).lower(), "path": m.group(2).strip(), "hunks": []}
            continue
        if raw.startswith("***") and not m:          # *** Begin Patch / *** End Patch
            if re.match(r"^\s*\*\*\*\s*End", raw):
                flush_item()
                continue
            continue
        m2 = _PATCH_DIFF_HDR.match(raw)
        if m2 and not _PATCH_HUNK_RE.match(raw):
            name = m2.group(1)
            if name in ("/dev/null",):
                continue
            if raw.lstrip().startswith("---"):
                if cur is not None and not cur["path"]:
                    cur["path"] = name
                elif cur is None:
                    cur = {"op": "update", "path": name, "hunks": []}
                    seen_diff_hdr = True
                continue
            if cur is None:                          # 只有 +++ 的情况（少见）
                cur = {"op": "update", "path": name, "hunks": []}
            elif not cur["path"]:
                cur["path"] = name
            continue
        if _PATCH_HUNK_RE.match(raw):
            flush_hunk()
            if cur is None:
                return [], "补丁格式不对：@@ 之前没有文件名（需要 *** Update File: 路径 或 ---/+++ 头）"
            hunk = {"old": [], "new": []}
            continue
        if cur is None:
            if not raw.strip():
                continue
            return [], f"补丁格式不对：这行既不是文件头也不是补丁内容 → {raw[:60]!r}"
        if hunk is None:
            if cur["op"] == "add" and raw[:1] == "+":  # 新增文件常不写 @@，直接给 + 内容行
                hunk = {"old": [], "new": []}
            else:
                continue                             # 文件头与第一个 @@ 之间的说明行，忽略
        if raw[:1] == "+":
            hunk["new"].append(raw[1:])
        elif raw[:1] == "-":
            hunk["old"].append(raw[1:])
        elif raw[:1] in (" ", "", "\\"):              # 上下文行（含空行、\ No newline 标记）
            if raw[:1] == "\\":
                continue
            hunk["old"].append(_strip_patch_marker(raw))
            hunk["new"].append(_strip_patch_marker(raw))
    flush_item()

    if not items:
        return [], ("补丁内容为空或无法识别。请用这种格式：\n"
                    "*** Update File: 路径\n@@\n 上下文行\n-要删的行\n+要加的行")
    for it in items:
        if not it["path"]:
            return [], "补丁里有一处没写文件名"
    return items, ""


def _find_block(lines: list, block: list, start: int) -> int:
    """在 lines 里从 start 往后找 block：先严格比对，再忽略首尾空白比对。"""
    if not block:
        return -1
    n, m = len(lines), len(block)
    exact = [b.rstrip() for b in block]
    loose = [b.strip() for b in block]
    for mode in (0, 1):
        wanted = exact if mode == 0 else loose
        i = max(0, start)
        while i + m <= n:
            seg = lines[i:i + m]
            got = [x.rstrip() for x in seg] if mode == 0 else [x.strip() for x in seg]
            if got == wanted:
                return i
            i += 1
    return -1


def _apply_hunks(text: str, hunks: list, path: str) -> tuple:
    """把若干 hunk 依次作用到文本上，返回 (新文本, 错误)。失败时原样不动。"""
    lines = text.splitlines()
    trailing_nl = text.endswith("\n") or text == ""
    cursor = 0
    for hi, h in enumerate(hunks, 1):
        old, new = h["old"], h["new"]
        if not old and not new:
            continue
        if not old:                      # 纯新增：插到光标处
            lines[cursor:cursor] = new
            cursor += len(new)
            continue
        pos = _find_block(lines, old, cursor)
        if pos < 0:
            pos = _find_block(lines, old, 0)     # 再从头找一次（多个 hunk 顺序可能跳着写）
        if pos < 0:
            blob = "\n".join(old)
            return text, (f"第 {hi} 处补丁在 {path} 里找不到对应原文"
                          f"（多半是上下文与文件当前内容不一致，或被前一处补丁改动过）"
                          + _near_miss_hint(blob, text))
        lines[pos:pos + len(old)] = new
        cursor = pos + len(new)
    out = "\n".join(lines)
    if trailing_nl and out and not out.endswith("\n"):
        out += "\n"
    return out, ""


def tool_apply_patch(cfg, patch: str = "") -> str:
    """一次应用多处/多文件的补丁：全部能对上才写盘（原子），任何一处失败就整体不改。"""
    items, err = _parse_patch(patch)
    if err:
        return f"[错误] {err}"

    staged = []          # [(op, path, 新内容 or None, 增, 删)]
    for it in items:
        p = _resolve(cfg, it["path"])
        op = it["op"]
        if op == "add":
            body = "\n".join(_strip_patch_marker(l) for h in it["hunks"] for l in h["new"])
            if body and not body.endswith("\n"):
                body += "\n"
            if p.exists():
                return f"[错误] {p} 已存在，不能用 Add File 覆盖；要改它请用 Update"
            staged.append(("A", p, body, len(body.splitlines()), 0))
            continue
        if op == "delete":
            if not p.exists():
                return f"[错误] {p} 不存在，无法删除"
            staged.append(("D", p, None, 0, 0))
            continue
        if not p.exists():
            return f"[错误] {p} 不存在，无法打补丁" + _missing_path_hint(p)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            return f"[错误] 读取 {p} 失败: {e}"
        new_text, e2 = _apply_hunks(text, it["hunks"], str(p))
        if e2:
            return f"[错误] {e2}\n（补丁未被应用，文件保持原样）"
        adds = sum(1 for h in it["hunks"] for _ in h["new"])
        dels = sum(1 for h in it["hunks"] for _ in h["old"])
        staged.append(("M", p, new_text, adds, dels))

    written = []
    for op, p, body, adds, dels in staged:      # 全部校验通过后才真正落盘
        try:
            if op == "D":
                p.unlink()
                written.append(f"D {p}")
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(body, encoding="utf-8")
                mark = "A" if op == "A" else "M"
                written.append(f"{mark} {p}  (+{adds} -{dels})")
        except Exception as e:
            return (f"[错误] 写入 {p} 失败: {e}\n已成功写入的："
                    + ("、".join(written) if written else "无"))
    return ("[成功] 已应用补丁（%d 个文件）\n" % len(written)) + "\n".join(written)


def _multi_match_hint(old_string: str, text: str, ctx: int = 2, most: int = 4) -> str:
    """old_string 命中多处时，把每一处的行号和上下文列出来，方便一次选对位置。"""
    lines = text.splitlines()
    spots = []
    start = 0
    while len(spots) < most:
        k = text.find(old_string, start)
        if k < 0:
            break
        spots.append(text.count("\n", 0, k) + 1)
        start = k + max(1, len(old_string))
    if not spots:
        return ""
    n_old = max(1, len(old_string.splitlines()))
    out = ["", f"它出现在（共 {len(spots)}{'+' if len(spots) >= most else ''} 处）："]
    printed = set()
    for ln in spots:
        s = max(0, ln - 1 - ctx)
        e = min(len(lines), ln - 1 + n_old + ctx)
        if printed:
            if s <= max(printed) + 1:
                s = max(printed) + 1           # 与上一段窗口挨着，直接续上
            else:
                out.append("   …")
        for j in range(s, e):
            if j in printed:
                continue
            mark = "→" if ln - 1 <= j < ln - 1 + n_old else " "
            out.append(f" {mark}{j + 1:>5}\t{lines[j]}")
            printed.add(j)
    if len(spots) >= most:
        out.append(f"  （只列出前 {most} 处）")
    return "\n".join(out)


def tool_edit_file(cfg, path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    p = _resolve(cfg, path)
    if not p.exists():
        return f"[错误] 文件不存在: {p}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"[错误] 读取失败: {e}"
    if not old_string:
        return "[错误] old_string 不能为空"
    count = text.count(old_string)
    if count == 0:
        return ("[错误] 未找到要替换的内容，请先用 read_file 核对原文"
                "（注意缩进与空格必须完全一致）" + _near_miss_hint(old_string, text))
    if count > 1 and not replace_all:
        return (f"[错误] 该片段在文件里出现 {count} 次，不唯一，无法确定要改哪一处。"
                + _multi_match_hint(old_string, text)
                + "\n请把前后几行一起写进 old_string 以确定位置；"
                  "如果确实要一次性全改，把 replace_all 设为 true。")
    new_text = text.replace(old_string, new_string or "")
    try:
        p.write_text(new_text, encoding="utf-8")
    except Exception as e:
        return f"[错误] 写入失败: {e}"
    return f"[成功] 已修改 {p}（替换 {count if replace_all else 1} 处）"


def _fmt_size(n: float) -> str:
    """把字节数变成人看得懂的大小，如 1.5MB。"""
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}GB"


def _friendly_regex_error(pattern: str, e: Exception, label: str = "pattern") -> str:
    """正则写错时，把 Python 的英文报错翻成中文，并指名常见原因与出错位置。"""
    raw = str(e)
    low = raw.lower()
    if "unterminated subpattern" in low or "unbalanced parenthesis" in low or "missing )" in low:
        cause = "括号 ( ) 没有配对"
    elif "unterminated character set" in low or "missing ]" in low:
        cause = "方括号 [ ] 没有闭合"
    elif "bad character range" in low:
        cause = "方括号里的字符区间写反了（如 [z-a]，应写成 [a-z]）"
    elif "min repeat greater than max repeat" in low:
        cause = "量词区间的上下限写反了（如 {2,1} 应写成 {1,2}）"
    elif "nothing to repeat" in low or "multiple repeat" in low:
        cause = "量词（* + ? {n}）用法不对：它前面必须有可重复的内容，也不能连着写两个"
    elif "bad escape" in low:
        cause = "反斜杠转义无效（要匹配字面反斜杠，用 re.escape 或放进方括号里）"
    elif "end of pattern" in low:
        cause = "表达式不完整（末尾少了内容）"
    else:
        cause = raw
    pos = getattr(e, "pos", None)
    at = f"，出错位置在第 {pos} 个字符附近" if isinstance(pos, int) and pos >= 0 else ""
    return (f"[错误] 正则表达式无效：{cause}{at}。\n"
            f"  {label}={pattern!r}；原始报错：{raw}\n"
            "  提示：先改成普通关键词把结果拿到手，再逐步加特殊符号。")


def tool_list_dir(cfg, path: str = ".", pattern: str = "") -> str:
    p = _resolve(cfg, path)
    if not p.exists():
        return f"[错误] 路径不存在: {p}" + _missing_path_hint(p)
    if not p.is_dir():
        return f"[错误] 不是目录: {p}，要用 read_file 读文件"
    try:
        all_entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    except Exception as e:
        return "[错误] 列举失败。" + _explain_os_error(p, e)
    if not all_entries:
        return f"[目录] {p} 是空目录"
    entries = all_entries
    if pattern:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return _friendly_regex_error(pattern, e)
        entries = [e for e in entries if rx.search(e.name)]
        if not entries:
            return (f"[目录] {p}：没有名称匹配 pattern={pattern!r} 的条目"
                    f"（不加 pattern 时共 {len(all_entries)} 项）")
    now = time.time()
    rows, total = [], 0
    big, recent = [], []
    empty, temps = [], []   # 整理线索用：0 字节空文件 / 疑似临时备份文件
    dirs_seen = []          # 整理线索用：直接子目录（最多 12 个，估算其体量）
    for e in entries[:400]:
        try:
            st = e.stat()
            kind = "d" if e.is_dir() else "-"
            age_days = int((now - st.st_mtime) / 86400)
            if e.is_dir():
                size = ""
                if len(dirs_seen) < 12:
                    dirs_seen.append(e)
            else:
                size = f"{st.st_size:>10}"
                total += st.st_size
                big.append((st.st_size, e.name))
                recent.append((st.st_mtime, e.name))
                if st.st_size == 0:
                    empty.append(e.name)
                elif e.name.lower().endswith(
                        ("~", ".tmp", ".temp", ".bak", ".old", ".orig", ".part", ".crdownload")):
                    temps.append(e.name)
            link = " -> " + os.readlink(e) if e.is_symlink() else ""
            rows.append(f"{kind} {size:>10} {age_days:>5}d  {e.name}{link}")
        except Exception:
            rows.append(f"? {'':>10} {'':>6}  {e.name}")
    n_dir = sum(1 for e in entries if e.is_dir())
    n_file = len(entries) - n_dir
    filt = f"，pattern={pattern!r} 过滤后" if pattern else ""
    head = (f"[目录] {p}{filt}：{n_dir} 个目录 · {n_file} 个文件"
            f"，文件合计 {_fmt_size(total)}（末列=修改距今天数，d=目录 -=文件）")
    more = (f"\n... 另有 {len(entries) - 400} 项未显示，可用 pattern 缩小范围"
            if len(entries) > 400 else "")
    # 整理线索：一眼看出最占空间的几个文件，以及最近动过什么
    tips = []
    big.sort(reverse=True)
    if big and big[0][0] >= 5 * 1024 * 1024:
        tips.append("最大的文件：" + "、".join(
            f"{n}（{_fmt_size(s)}）" for s, n in big[:3] if s >= 1024 * 1024))
    fresh = sorted((t for t in recent if now - t[0] < 86400), reverse=True)
    if fresh:
        tips.append(f"近 24 小时改动 {len(fresh)} 个，最新是 {fresh[0][1]}")
    # 可清理候选：0 字节空文件、tmp/bak 之类临时与备份文件（整理时可以先处理这些）
    junk = []
    if empty:
        junk.append(f"空文件 {len(empty)} 个（如 {', '.join(empty[:3])}）")
    if temps:
        junk.append(f"临时/备份文件 {len(temps)} 个（如 {', '.join(temps[:3])}）")
    if junk:
        tips.append("可清理候选：" + "；".join(junk))
    # 疑似重复副本：名字去掉「(1)/副本/copy/拷贝」等标记后，同目录已存在同名文件
    _copy_rx = re.compile(
        r"(?i)^(.*?)(?:\s*[\(\[]\s*(?:副本|复制|拷贝|copy|\d+)\s*[\)\]]"
        r"|\s*[-_ ]?\s*(?:副本|复制|拷贝|copy))+$")
    names_lower = {e.name.lower() for e in entries[:400] if not e.is_dir()}
    dups = []
    for e in entries[:400]:
        if e.is_dir():
            continue
        stem, ext = os.path.splitext(e.name)
        m = _copy_rx.match(stem)
        base = (m.group(1).rstrip(" -_") + ext) if m else ""
        if base and base.lower() in names_lower:
            dups.append(f"{e.name} ≈ {base}")
    if dups:
        tips.append("疑似重复副本：" + "、".join(dups[:3])
                    + (f" 等 {len(dups)} 个" if len(dups) > 3 else "，可考虑先去重再整理"))
    # 类型分布：散落文件较多时，给出按类型归档的目标目录建议（整理时可直接照做）
    if n_file >= 6:
        _cats = (
            ("图片", "Pictures", (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".heic", ".svg", ".tif", ".tiff")),
            ("视频", "Videos", (".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".3gp")),
            ("音频", "Music", (".mp3", ".flac", ".wav", ".m4a", ".aac", ".ogg", ".opus")),
            ("文档", "Documents", (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md", ".csv", ".epub")),
            ("压缩包", "Archives", (".zip", ".rar", ".7z", ".tar", ".gz", ".xz", ".bz2")),
            ("安装包", "Apk", (".apk", ".apks", ".xapk")),
            ("代码", "Code", (".py", ".js", ".ts", ".java", ".kt", ".c", ".cpp", ".h", ".sh", ".json", ".html", ".css")),
        )
        _stat = {}
        for e in entries[:400]:
            if e.is_dir():
                continue
            try:
                _sz = e.stat().st_size
            except Exception:
                continue
            _v = _stat.setdefault(os.path.splitext(e.name)[1].lower(), [0, 0])
            _v[0] += 1
            _v[1] += _sz
        groups = []
        for _label, _folder, _exts in _cats:
            _cnt = _tot = 0
            for _k, _v in _stat.items():
                if _k in _exts:
                    _cnt += _v[0]
                    _tot += _v[1]
            if _cnt >= 3:
                groups.append((_cnt, f"{_label} {_cnt} 个（{_fmt_size(_tot)}）→ {_folder}/"))
        if groups:
            groups.sort(key=lambda x: -x[0])
            tips.append("按类型归档建议：" + "；".join(g[1] for g in groups[:3]))
    tail = ("\n[整理线索] " + "；".join(tips)) if tips else ""
    return _truncate(head + "\n" + "\n".join(rows) + more + tail)


def _looks_binary(f: Path) -> bool:
    """只看开头一小段有没有 NUL 字节，判断是不是二进制文件（.so/.apk/图片不该当文本搜）。"""
    try:
        with f.open("rb") as fh:
            return b"\x00" in fh.read(8192)
    except Exception:
        return True                                # 连开头都读不了，就当它不可搜


def tool_grep(cfg, pattern: str, path: str = ".", include: str = "", max_results: int = 200) -> str:
    root = _resolve(cfg, path)
    if not root.exists():
        return f"[错误] 路径不存在: {root}" + _missing_path_hint(root)
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return _friendly_regex_error(pattern, e)
    inc_rx = None
    if include:
        try:
            inc_rx = re.compile(include)
        except re.error as e:
            return _friendly_regex_error(include, e, label="include")
    max_results = max(1, min(int(max_results or 200), 1000))
    hits, scanned, skipped = [], 0, 0
    if root.is_file():
        files = [root]
    else:                                          # 按路径排序：命中顺序稳定、可复现
        files = sorted((f for f in root.rglob("*") if f.is_file()), key=lambda x: str(x))
    for f in files:
        if inc_rx and not inc_rx.search(f.name):
            continue
        try:
            too_big = f.stat().st_size > 5 * 1024 * 1024
        except Exception:
            skipped += 1
            continue
        if too_big or _looks_binary(f):            # 二进制/超大文件跳过，不让它们刷出垃圾命中
            skipped += 1
            continue
        scanned += 1
        try:
            with f.open(encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh, 1):
                    if rx.search(line):
                        hits.append(f"{f}:{i}: {line.rstrip()[:300]}")
                        if len(hits) >= max_results:
                            extra = f"，另有 {skipped} 个文件未扫描（二进制或超 5MB）" if skipped else ""
                            return _truncate(f"[检索] 已扫描 {scanned} 个文件，命中上限 {max_results} 条：\n"
                                             + "\n".join(hits) + f"\n[提示] 结果被上限截断{extra}，请缩小范围")
        except Exception:
            continue
    skip_note = f"（另有 {skipped} 个二进制/超 5MB 的文件已跳过）" if skipped else ""
    if not hits:
        return (f"[检索] 扫描 {scanned} 个文件{skip_note}，未命中 /{pattern}/\n"
                "[提示] 换个关键词再查：把正则简化成一段普通文字、去掉 .* 之类符号、"
                "换同义词或只留词根，别在原样重试同一个 pattern。\n"
                "若怀疑是文件本身没找对，先 list_dir 列出目录看有哪些文件、"
                "确认路径和文件名，再回来 grep。"
                + ("\n（若目标确实是二进制/超大文件，请改用 bash 跑 grep -a 或 strings）" if skipped else ""))
    return _truncate(f"[检索] 扫描 {scanned} 个文件{skip_note}，命中 {len(hits)} 条：\n" + "\n".join(hits))


def tool_fetch_url(cfg, url: str, max_chars: int = 20000) -> str:
    if not re.match(r"^https?://", url or ""):
        return "[错误] url 必须以 http:// 或 https:// 开头"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Linux; Android) TermuxAgent/" + VERSION,
        "Accept": "text/html,application/json,text/plain,*/*",
    })
    # 硬性总时间预算：即使服务器慢速滴水式响应也不会无限卡住
    deadline = time.time() + 30
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            ctype = r.headers.get("Content-Type", "")
            chunks, total = [], 0
            while total < 2 * 1024 * 1024:
                if time.time() > deadline:
                    return ("[错误] 抓取超时：这个页面响应过慢或是个下载链接。"
                            "换 web_search 搜资料，或改用 download 工具直接下载。")
                chunk = r.read(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            raw = b"".join(chunks)
    except Exception as e:
        # 把网络异常翻成能照做的中文提示，别只丢一行原始异常名给模型
        import ssl
        if isinstance(e, urllib.error.HTTPError):
            hint = {401: "该页需要登录或鉴权", 403: "网站拒绝了本机访问（多半有反爬或防盗链）",
                    404: "页面不存在或已下架", 429: "请求太频繁，稍后再试",
                    500: "对方服务器内部出错", 502: "对方网关出错", 503: "对方服务器繁忙"}.get(e.code, "")
            return (f"[错误] 抓取失败：HTTP {e.code} {e.reason}。"
                    + (hint + "。" if hint else "")
                    + "可改用 web_search 搜同内容的其它页面。")
        if isinstance(e, urllib.error.URLError):
            r = getattr(e, "reason", e)
            rs = str(r)
            if isinstance(r, socket.gaierror) or "Name or service not known" in rs \
                    or "name resolution" in rs or "nodename nor servname" in rs:
                return (f"[错误] 抓取失败：域名解析不了（{url}）。"
                        "检查域名有没有拼错、网络是否可用。")
            if isinstance(r, (TimeoutError, socket.timeout)):
                return (f"[错误] 抓取失败：连接 {url} 超时（对方很慢或网络不通）。"
                        "稍后重试，或改用 web_search 搜同内容的其它页面。")
            if isinstance(r, ssl.SSLError):
                return (f"[错误] 抓取失败：HTTPS 证书校验不通过（{rs}）。"
                        "该站点证书可能过期或不受信任，可换 web_search 找同内容。")
            return f"[错误] 抓取失败：连不上 {url}（{rs}）。检查网络后重试。"
        if isinstance(e, (TimeoutError, socket.timeout)):
            return (f"[错误] 抓取失败：连接 {url} 超时。"
                    "稍后重试，或改用 web_search 搜同内容的其它页面。")
        if isinstance(e, ssl.SSLError):
            return f"[错误] 抓取失败：HTTPS 证书校验不通过（{e}）。"
        return f"[错误] 抓取失败: {type(e).__name__}: {e}"
    if not raw:
        return "[错误] 抓取到空内容"
    text = raw.decode("utf-8", "replace")
    if "html" in ctype.lower():
        text = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", " ", text)
        text = re.sub(r"&nbsp;?", " ", text)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"&lt;", "<", text)
        text = re.sub(r"&gt;", ">", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    cap = max(1000, min(int(max_chars or 20000), MAX_TOOL_OUTPUT))
    note = f"\n\n[已截断至 {cap} 字符]" if len(text) > cap else ""
    return _truncate(f"[抓取] {url}（{ctype}）\n{text[:cap]}{note}")


def _query_from_url(url: str) -> str:
    """读页失败时，从 URL 推出一段能拿去联网搜的关键词（去掉文件后缀和纯数字 ID 段）。"""
    try:
        u = urllib.parse.urlsplit(url)
    except Exception:
        return ""
    host = u.netloc.split("@")[-1].split(":")[0]
    words = [host[4:] if host.startswith("www.") else host]
    for seg in u.path.split("/"):
        seg = urllib.parse.unquote(seg).strip()
        seg = re.sub(r"\.(html?|php|aspx?|jsp|json|xml)$", "", seg, flags=re.I)
        if not seg or re.fullmatch(r"[\d.\-_]+", seg):
            continue
        words.append(seg.replace("-", " ").replace("_", " "))
    return re.sub(r"\s+", " ", " ".join(w for w in words if w)).strip()[:80]


_fetch_url_raw = tool_fetch_url          # 原始实现：只抓一次，不做兜底


def tool_fetch_url(cfg, url: str, max_chars: int = 20000) -> str:
    """抓网页；读不到时自动改用 web_search 找同内容的其它页面，省掉一轮往返。"""
    out = _fetch_url_raw(cfg, url, max_chars)
    if not out.startswith("[错误]"):
        return out
    # 域名解析不了 / 连接超时 / 连不上 = 网络本身不通，搜索多半也白搭，别再多等一轮超时
    if "域名解析不了" in out or "连不上" in out:
        return out
    if "超时" in out and "抓取超时" not in out:
        return out
    q = _query_from_url(url)
    if not q:
        return out
    try:
        res = tool_web_search(cfg, q, 5)
    except Exception as e:
        return out + f"\n[自动兜底] 改用 web_search 搜索时出错：{type(e).__name__}"
    if res.startswith("[错误]"):
        return out + f"\n[自动兜底] 又用 web_search 搜「{q}」，同样没搜到，换个思路吧。"
    body = res.split("\n", 1)[1] if "\n" in res else ""
    if not body.strip():
        return out
    return _truncate(out + f"\n[自动兜底] 该页读不到，已自动改用 web_search 搜「{q}」，"
                            "下面是同内容的其它候选页面（通常已够用，不必再手动搜索）：\n"
                            + body[:1600])


def tool_download(cfg, url: str, path: str, timeout_sec: int = 600) -> str:
    """把文件流式下载到本地磁盘（不截断），适合 APK、安装包等大文件。"""
    if not re.match(r"^https?://", url or ""):
        return "[错误] url 必须以 http:// 或 https:// 开头"
    if not path or not path.strip():
        return "[错误] 请提供保存路径"
    p = _resolve(cfg, path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return f"[错误] 无法创建目录: {e}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Linux; Android 12; Tablet) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    })
    timeout = max(10, min(int(timeout_sec or 600), 1800))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            with open(p, "wb") as f:
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
    except Exception as e:
        # 失败一律删掉半截文件，避免残留文件被误当成完整安装包
        try:
            if p.exists():
                p.unlink()
        except Exception:
            pass
        if isinstance(e, urllib.error.HTTPError):
            hint = {
                403: "服务器拒绝访问，直链多半带防盗链或已过期",
                404: "链接不存在或文件已下架",
                410: "链接已永久失效",
            }.get(e.code, "")
            return (f"[错误] 下载失败：HTTP {e.code} {e.reason}。"
                    + (hint + "，" if hint else "")
                    + "建议先用 web_search 找到官网下载页再取直链，或优先用华为应用市场安装。")
        if isinstance(e, urllib.error.URLError):
            return (f"[错误] 下载失败：连不上服务器（{e.reason}）。"
                    "检查网络是否可用、URL 是否写对，稍后再试。")
        if isinstance(e, TimeoutError):
            return (f"[错误] 下载超时（超过 {timeout}s）。"
                    "文件较大或网速慢时可加大 timeout_sec 重试，或换个镜像地址。")
        return (f"[错误] 下载失败: {type(e).__name__}: {e}。"
                "请核对 URL 与保存路径后重试。")
    size = p.stat().st_size
    if total and size != total:
        return f"[错误] 下载不完整：应 {total} 字节，实际 {size} 字节，可再次调用 download 重试。"
    msg = f"[成功] 已下载 {size/1024/1024:.1f} MB 到 {p}"
    if p.suffix.lower() == ".apk":
        msg += f"\n[提示] 这是安卓安装包，可用 sysshell 执行：pm install -r {p}"
    return msg


def _chat_once(cfg: dict, messages: list, max_tokens: int) -> str:
    """单轮调用，返回正文（供对话压缩、记忆蒸馏、子代理等场景使用）。

    2026-10-01：原先直接打非流式 /chat/completions，但当前上游（WorkBuddy /
    copilot.tencent.com）明确拒绝非流式：HTTP 400
    "Non-stream chat request is currently not supported"。于是压缩、记忆蒸馏、
    子代理全部**静默失败** —— 压缩退化成「只裁剪不总结」，记忆干脆不再增长，
    界面上完全看不出来。改走流式通道，把正文拼回来。
    """
    text, reasoning = [], []
    for kind, data in stream_completion(cfg, messages, [], max_tokens=max_tokens):
        if kind == "text":
            text.append(data)
        elif kind == "reasoning":
            reasoning.append(data)
    out = "".join(text).strip()
    if out:
        return out
    # 思考模式下若 max_tokens 被思考过程吃光、正文为空，退回思考内容兜底
    return "".join(reasoning).strip()


# ---------------------------------------------------------------- 技能与工具

# ---------------------------------------------------------------- 技能 AI 解释

# 说明存在 skills/.explain/<技能名>.json，和技能文档同生共死：
#   - 不在 skills/*.md 里，所以不会被 list_skills 当成技能
#   - 记下技能文件的「大小+修改时间」指纹，技能一改就自动判定说明过期
SKILL_EXPLAIN_DIRNAME = ".explain"


def _skill_dir() -> Path:
    return Path(__file__).resolve().parent / "skills"


def _explain_path(stem: str) -> Path:
    return _skill_dir() / SKILL_EXPLAIN_DIRNAME / (stem + ".json")


def _skill_sig(stem: str):
    """技能文件指纹；文件不在就返回 None。"""
    try:
        st = (_skill_dir() / (stem + ".md")).stat()
        return [st.st_size, int(st.st_mtime)]
    except OSError:
        return None


def read_skill_explain(stem: str):
    """读已保存的 AI 说明。返回 dict(text, at, stale) 或 None。"""
    try:
        d = json.loads(_explain_path(stem).read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(d, dict) or not (d.get("text") or "").strip():
        return None
    return {"text": d["text"], "at": d.get("at") or "",
            "stale": d.get("src") != _skill_sig(stem)}


def save_skill_explain(stem: str, text: str) -> None:
    try:
        p = _explain_path(stem)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(
            {"text": text, "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
             "src": _skill_sig(stem)}, ensure_ascii=False, indent=1),
            encoding="utf-8")
    except Exception:
        pass


def _clean_explain(t: str) -> str:
    """兜底清洗：模型偶尔会把英文思考过程一起吐出来。

    原因：推理模型把 max_tokens 耗在思考上、正文为空，_extract_text 会退回
    reasoning_content。这里从第一行「中文足够多」的地方开始截取，砍掉前面的推理。
    """
    t = (t or "").strip()
    if not t:
        return t
    lines = t.splitlines()
    for i, l in enumerate(lines):
        cjk = sum(1 for ch in l if "\u4e00" <= ch <= "\u9fff")
        if cjk >= 8:                     # 这一行已经明显是中文正文了
            return "\n".join(lines[i:]).strip()
    return t


def gen_skill_explain(cfg: dict, stem: str) -> str:
    """让模型读一遍技能文档，写一段给用户看的说明。"""
    body = (_skill_dir() / (stem + ".md")).read_text(
        encoding="utf-8", errors="replace")[:12000]
    msgs = [
        {"role": "system", "content":
         "你是给用户写说明的助手。直接输出中文说明正文，"
         "不要输出思考过程、不要输出英文推理、不要复述要求、不要加任何前缀。"},
        {"role": "user", "content":
         "下面是你（AI 助手）自己的一个技能文档。请写一段给用户看的说明，"
         "让他一眼看懂这个技能是干什么的。格式要求：\n"
         "1) 第一句说清「这个技能让助手多会做什么」\n"
         "2) 再写 2~3 条要点：什么场景会用上它、用户可以直接说什么话来调用\n"
         "3) 总共 100~200 字，朴素直白，不要客套话，不要照抄文档原文\n"
         "4) 只输出说明正文，不要加「说明：」「以下是」这类前缀\n\n"
         "技能文档内容：\n" + body},
    ]
    # max_tokens 必须给足：推理模型会先花掉一大截在思考上，
    # 给太小会导致正文为空（实测 700 会被吃光）
    return _clean_explain(_chat_once(cfg, msgs, 2500))


def list_skills() -> list:
    """列出 skills/ 下的技能文档：名字、首行标题、大小、修改时间。

    技能文档是沉淀下来的"踩过的坑和正确姿势"，模型做相关任务前会按需读它。
    这里只是把清单给控制台看，方便用户知道有哪些技能、什么时候加的。
    """
    out = []
    d = Path(__file__).resolve().parent / "skills"
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.md")):
        title = ""
        try:
            for l in f.read_text(encoding="utf-8", errors="replace").splitlines():
                if l.strip().startswith("#"):
                    title = l.lstrip("# ").strip()
                    break
        except Exception:
            pass
        try:
            st = f.stat()
        except OSError:
            continue
        _ex = read_skill_explain(f.stem)
        out.append({
            "name": f.stem,
            "file": f.name,
            "title": title,
            "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M"),
            "explain": bool(_ex),
            "explain_stale": bool(_ex and _ex.get("stale")),
        })
    return out


def list_tools() -> list:
    """列出当前可用的工具：名字 + 说明书里的第一句（够看懂是干嘛的即可）。"""
    out = []
    try:
        for spec in TOOL_SCHEMAS:
            fn = spec.get("function") or {}
            name = fn.get("name") or ""
            if not name:
                continue
            desc = (fn.get("description") or "").strip()
            cut = desc.find("。")
            brief = desc[:cut] if cut > 0 else desc
            out.append({"name": name, "brief": brief[:64], "full": desc[:900]})
    except Exception:
        pass
    return out


# ---------------------------------------------------------------- 运行日志

def list_logs() -> list:
    """列出日志目录下的 .log 文件（名称、大小、修改时间），新的排前面。"""
    out = []
    try:
        for p in APP_DIR.glob("*.log"):
            try:
                st = p.stat()
            except OSError:
                continue
            out.append({
                "name": p.name,
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%m-%d %H:%M"),
                "ts": st.st_mtime,
            })
    except OSError:
        pass
    out.sort(key=lambda x: x["ts"], reverse=True)
    return out


def read_log_tail(name: str, tail: int = 300) -> dict:
    """读某个日志文件的末尾若干行。只允许读 APP_DIR 下的 .log，防路径穿越。"""
    try:
        tail = max(1, min(int(tail or 300), 2000))
    except (TypeError, ValueError):
        tail = 300
    p = (APP_DIR / str(name or "")).resolve()
    if p.parent != APP_DIR.resolve() or p.suffix != ".log":
        return {"ok": False, "error": "只允许读取日志目录下的 .log 文件"}
    if not p.exists():
        return {"ok": False, "error": "没有这个日志：" + str(name)}
    try:
        size = p.stat().st_size
        with open(p, "rb") as f:
            f.seek(max(0, size - 300 * 1024))   # 只读尾部 300KB，避免大日志撑爆内存
            data = f.read().decode("utf-8", "replace")
    except OSError as e:
        return {"ok": False, "error": "读取失败：" + str(e)}
    lines = data.splitlines()[-tail:]
    return {"ok": True, "name": p.name, "lines": lines, "size": size}


SELFCHECK_SYSTEM = (
    "你是本机助手的运维诊断员。用户会给你一段程序运行日志，请按要求分析：\n"
    "1. 【发生了什么】用一两句话概括这段日志期间的主要活动；\n"
    "2. 【异常】逐条列出发现的报错/警告/异常行为，写明大致时间点和关键原文片段；"
    "确实没有异常就明确写「未发现异常」；\n"
    "3. 【可能原因】针对每个异常给出最可能的成因；\n"
    "4. 【建议动作】给出具体可执行的下一步（改哪个文件、跑什么命令、查什么）。\n"
    "要求：中文、简洁、直接给结论；不要复述日志原文；不要客套话；"
    "不确定的地方要明说不确定，不要编造。"
)

SELFCHECK_SYSTEM_EN = (
    "You are the ops diagnostician for this on-device assistant. The user gives you a "
    "program log; analyse it as follows:\n"
    "1. [What happened] Summarise the main activity during this log in one or two sentences;\n"
    "2. [Anomalies] List each error/warning/abnormal behaviour with an approximate "
    "timestamp and the key raw snippet; if there really are none, state plainly "
    "\"no anomalies found\";\n"
    "3. [Likely cause] Give the most probable cause for each anomaly;\n"
    "4. [Suggested action] Give concrete, runnable next steps (which file to edit, which "
    "command to run, what to check).\n"
    "Requirements: English, concise, conclusion first; don't parrot the log; no "
    "pleasantries; say plainly when you're unsure instead of making things up."
)


def selfcheck_system() -> str:
    """自检提示词按界面语言选择。"""
    if str(globals().get("UI_LANG") or "zh").lower().startswith("en"):
        return SELFCHECK_SYSTEM_EN
    return SELFCHECK_SYSTEM


def ai_selfcheck(cfg: dict, name: str, tail: int = 300) -> dict:
    """让接入的模型读日志尾部并给出诊断。失败时返回 ok=False ＋ 原因。"""
    r = read_log_tail(name, tail)
    if not r.get("ok"):
        return r
    text = "\n".join(r["lines"]).strip()
    if not text:
        return {"ok": False, "error": "日志内容是空的"}
    if len(text) > 60000:                        # 再兜一层，防止超长请求
        text = text[-60000:]
    try:
        answer = _chat_once(cfg, [
            {"role": "system", "content": selfcheck_system()},
            {"role": "user", "content": "日志文件：" + r["name"]
             + "（共 " + str(r["size"]) + " 字节，以下是末尾 " + str(len(r["lines"])) + " 行）\n\n" + text},
        ], 1500)
    except Exception as e:
        return {"ok": False, "error": "调用模型失败：" + str(e)}
    return {"ok": True, "name": r["name"], "lines": len(r["lines"]),
            "size": r["size"], "answer": answer}


def _search_bing(query: str, n: int):
    url = "https://cn.bing.com/search?q=" + urllib.parse.quote(query) + "&count=20&setlang=zh-CN"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read(400 * 1024).decode("utf-8", "replace")
    if "b_algo" not in html:
        return None
    items = re.findall(r'<li class="b_algo".*?</li>', html, re.S)
    results = []
    for it in items:
        m = re.search(r'<h2[^>]*>.*?<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', it, re.S)
        if not m:
            continue
        title = re.sub(r"(?s)<[^>]+>", "", m.group(2))
        title = re.sub(r"\s+", " ", title).strip()
        snip = re.search(r'<p[^>]*>(.*?)</p>', it, re.S)
        snippet = ""
        if snip:
            snippet = re.sub(r"(?s)<[^>]+>", "", snip.group(1))
            snippet = re.sub(r"\s+", " ", snippet).strip()
        results.append((title, m.group(1), snippet))
        if len(results) >= n:
            break
    return results


def _search_ddg(query: str, n: int):
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Linux; Android 12; Tablet) AppleWebKit/537.36",
    })
    with urllib.request.urlopen(req, timeout=25) as r:
        html = r.read(400 * 1024).decode("utf-8", "replace")
    if "result__a" not in html:
        return None
    links = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S)
    snips = re.findall(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
    results = []
    for i, (href, title) in enumerate(links[:n]):
        m = re.search(r"[?&]uddg=([^&]+)", href)
        real = urllib.parse.unquote(m.group(1)) if m else href
        t = re.sub(r"(?s)<[^>]+>", "", title)
        t = re.sub(r"\s+", " ", t).strip()
        s = re.sub(r"(?s)<[^>]+>", "", snips[i]) if i < len(snips) else ""
        s = re.sub(r"\s+", " ", s).strip()
        results.append((t, real, s))
    return results


def tool_web_search(cfg, query: str, max_results: int = 8) -> str:
    """联网搜索（优先 Bing，国内可达；回退 DuckDuckGo）。"""
    if not query or not query.strip():
        return "[错误] query 不能为空"
    n = max(1, min(int(max_results or 8), 15))
    results = None
    last_err = ""
    for fn in (_search_bing, _search_ddg):
        try:
            results = fn(query, n)
            if results:
                break
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:60]}"
    if not results:
        return (f"[错误] 搜索失败{('（' + last_err + '）') if last_err else ''}。"
                f"可改用 fetch_url 直接抓取已知网页。")
    rows = [f"{i + 1}. {t}\n   {u}\n   {s}" for i, (t, u, s) in enumerate(results)]
    return _truncate(f"[搜索] {query}\n\n" + "\n\n".join(rows))


# ---- 任务清单：把大任务拆成步骤，界面显示在输入框上方，中断后还能接着做 ----
# 存在 todo.json：{"sid": 会话id, "time": 更新时间, "items": [{text, done}]}
TODO_PATH = APP_DIR / "todo.json"
TODO_SID = ""     # 当前正在干活的会话 id，由 Agent.run_turn 开头写入


def load_todo(sid: str = "") -> dict:
    """读任务清单；老格式（纯列表）自动迁移成带会话号的格式。"""
    try:
        raw = json.loads(TODO_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"sid": "", "time": "", "items": []}
    if isinstance(raw, list):
        raw = {"sid": sid, "time": "", "items": raw}
    if not isinstance(raw, dict):
        return {"sid": "", "time": "", "items": []}
    items = []
    for it in (raw.get("items") or [])[:20]:
        if isinstance(it, dict):
            items.append({"text": str(it.get("text") or ""), "done": bool(it.get("done"))})
        else:
            items.append({"text": str(it), "done": False})
    return {"sid": str(raw.get("sid") or ""), "time": str(raw.get("time") or ""),
            "items": [x for x in items if x["text"]]}


def todo_payload(sid: str = "") -> dict:
    """给前端的清单：只显示属于当前会话的那份，换对话不串台。"""
    d = load_todo(sid)
    if d["sid"] and sid and d["sid"] != sid:
        return {"sid": sid, "time": "", "items": []}
    return {"sid": sid, "time": d["time"], "items": d["items"]}


TODO_HIST = APP_DIR / "todo-history.jsonl"   # 被覆盖的旧清单挪到这里，可回溯
TODO_HIST_MAX = 100                          # 只留最近 100 份


def _archive_todo() -> None:
    """把当前 todo.json 追加进历史 —— 必须在覆盖前调用。

    从前 todo.json 只有一份、写一次盖一次，上一份清单再也找不回来
    （2026-09-29 就因此丢了用户要的那份「skill / 工具 / 日志」清单，
    只剩一句「刚才的任务怎么没了？」）。现在覆盖前先归档；
    与上一条完全相同的不重复记，避免连续更新同一份清单时刷屏。
    """
    try:
        if not TODO_PATH.exists():
            return
        rec = json.loads(TODO_PATH.read_text(encoding="utf-8").strip() or "{}")
        if not isinstance(rec, dict) or not rec.get("items"):
            return
        tail = []
        if TODO_HIST.exists():
            tail = TODO_HIST.read_text(encoding="utf-8").splitlines()
            if tail:
                try:
                    if json.loads(tail[-1]) == rec:      # 与上一条相同，不重复归档
                        return
                except Exception:
                    pass
        tail.append(json.dumps(rec, ensure_ascii=False))
        TODO_HIST.write_text("\n".join(tail[-TODO_HIST_MAX:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def save_todo(items: list, sid: str = "") -> dict:
    d = {"sid": sid, "time": datetime.now().strftime("%Y-%m-%d %H:%M"), "items": items}
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        _archive_todo()          # 覆盖前先归档，别让上一份清单凭空消失
        TODO_PATH.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        try:
            with open(APP_DIR / "todo-error.log", "a", encoding="utf-8") as f:
                f.write("%s 写入 %s 失败: %r\n"
                        % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), TODO_PATH, e))
        except Exception:
            pass
    return d


def _gh_token() -> str:
    """读取 github.json 里的 Token。"""
    import json as _json
    p = os.path.expanduser("~/.termux-agent/github.json")
    try:
        with open(p, encoding="utf-8") as f:
            return (_json.load(f) or {}).get("token", "") or ""
    except Exception:
        return ""


def _gh_api(method: str, path: str, body=None, timeout: int = 40):
    """调 GitHub API，返回 (状态码, 解析后的内容)。"""
    import json as _json
    tok = _gh_token()
    if not tok:
        return 0, {"message": "未配置 GitHub Token（~/.termux-agent/github.json）"}
    url = path if path.startswith("http") else ("https://api.github.com" + path)
    data = _json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method.upper())
    req.add_header("Authorization", "Bearer " + tok)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "pikachu-agent")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, _json.loads(raw)
            except Exception:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, _json.loads(raw)
        except Exception:
            return e.code, raw
    except Exception as e:
        return 0, {"message": "%s: %s" % (type(e).__name__, str(e)[:120])}


def _gh_token_url(repo: str) -> str:
    """把 Token 拼进 clone 地址（仅内存中使用，不落盘）。"""
    return "https://x-access-token:%s@github.com/%s.git" % (_gh_token(), repo.strip("/"))


def _gh_scrub(s: str) -> str:
    """抹掉输出里可能出现的 Token。"""
    try:
        return re.sub(r"github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9_]+|x-access-token:[^@\s]*",
                      "***", str(s))
    except Exception:
        return str(s)


def tool_github(cfg, action: str = "list", repo: str = "", path: str = "",
                ref: str = "", content: str = "", message: str = "",
                dir: str = "", branch: str = "", files=None,
                query: str = "", private: bool = True, **kw) -> str:
    """GitHub 仓库读写：列仓库 / 看文件 / 克隆 / 提交推送 / 建仓库 / 搜代码。"""
    import json as _json
    a = (action or "list").strip().lower()

    # ---- list：列我的仓库 ----
    if a in ("list", "repos", "ls"):
        n = max(1, min(int(kw.get("limit") or 30), 100))
        st, d = _gh_api("GET", "/user/repos?per_page=%d&sort=updated&affiliation=owner" % n)
        if st != 200 or not isinstance(d, list):
            m = d.get("message") if isinstance(d, dict) else d
            return "[错误] 列仓库失败(HTTP %s)：%s" % (st, m)
        if not d:
            return "[GitHub] 该账号还没有任何仓库。"
        rows = []
        for r in d:
            rows.append("  %-42s %s  %s  %s" % (
                r.get("full_name"), "私有" if r.get("private") else "公开",
                (r.get("pushed_at") or "")[:10], r.get("language") or "-"))
        return _truncate("[GitHub] 共 %d 个仓库：" % len(d) + "\n" + "\n".join(rows))

    # ---- repo：查单仓库详情 ----
    if a in ("repo", "info", "detail"):
        if not repo:
            return "[错误] 需要 repo 参数，如 owner/name"
        st, d = _gh_api("GET", "/repos/" + repo.strip("/"))
        if st != 200:
            m = d.get("message") if isinstance(d, dict) else d
            return "[错误] 查询失败(HTTP %s)：%s" % (st, m)
        keys = ("full_name", "description", "private", "default_branch", "language",
                "size", "created_at", "pushed_at", "html_url", "clone_url",
                "stargazers_count", "open_issues_count")
        return _truncate(_json.dumps({k: d.get(k) for k in keys}, ensure_ascii=False, indent=2))

    # ---- read：读文件内容 ----
    if a in ("read", "cat", "file"):
        if not repo:
            return "[错误] 需要 repo 参数"
        q = "/repos/%s/contents/%s" % (repo.strip("/"), path.lstrip("/"))
        if ref:
            q += "?ref=" + ref
        st, d = _gh_api("GET", q)
        if st != 200:
            m = d.get("message") if isinstance(d, dict) else d
            return "[错误] 读取失败(HTTP %s)：%s" % (st, m)
        if isinstance(d, list):
            rows = ["  %s %s" % ("[目录]" if i.get("type") == "dir" else "[文件]",
                                 i.get("path")) for i in d]
            return _truncate("[GitHub] %s/%s 是个目录：" % (repo, path) + "\n" + "\n".join(rows))
        import base64
        try:
            txt = base64.b64decode(d.get("content", "")).decode("utf-8", "replace")
        except Exception as e:
            return "[错误] 解码失败：%s" % e
        head = "[GitHub] %s/%s (%d 字节, sha=%s)" % (
            repo, d.get("path"), d.get("size", 0), (d.get("sha") or "")[:8])
        return _truncate(head + "\n\n" + txt)

    # ---- tree：列目录 ----
    if a in ("tree", "lsdir"):
        if not repo:
            return "[错误] 需要 repo 参数"
        br = ref or branch
        if not br:
            st0, r0 = _gh_api("GET", "/repos/" + repo.strip("/"))
            br = (r0.get("default_branch") if isinstance(r0, dict) else "") or "main"
        st, d = _gh_api("GET", "/repos/%s/git/trees/%s?recursive=0" % (
            repo.strip("/"), br))
        if st != 200:
            m = d.get("message") if isinstance(d, dict) else d
            return "[错误] 失败(HTTP %s)：%s" % (st, m)
        pre = path.strip("/")
        rows = []
        for it in d.get("tree", []):
            p = it.get("path", "")
            if pre and not p.startswith(pre):
                continue
            rows.append("  %s %7s  %s" % ("[目录]" if it.get("type") == "tree" else "[文件]",
                                          it.get("size") or "", p))
        head = "[GitHub] %s@%s 共 %d 项：" % (repo, br, len(rows))
        return _truncate(head + "\n" + "\n".join(rows[:400]))

    # ---- clone / pull：克隆到本地 ----
    if a in ("clone", "pull"):
        if not repo:
            return "[错误] 需要 repo 参数，如 owner/name"
        dest = os.path.expanduser(dir or ("~/github/" + repo.strip("/").split("/")[-1]))
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        url = _gh_token_url(repo)
        if os.path.isdir(os.path.join(dest, ".git")):
            subprocess.run(["git", "-C", dest, "remote", "set-url", "origin", url],
                           capture_output=True, text=True, timeout=60)
            r = subprocess.run(["git", "-C", dest, "pull", "--ff-only"],
                               capture_output=True, text=True, timeout=180)
            tag = "拉取"
        else:
            r = subprocess.run(["git", "clone", url, dest],
                               capture_output=True, text=True, timeout=300)
            subprocess.run(["git", "-C", dest, "remote", "set-url", "origin",
                            "https://github.com/%s.git" % repo.strip("/")],
                           capture_output=True, text=True, timeout=30)
            tag = "克隆"
        if r.returncode != 0:
            return "[错误] %s失败：%s" % (tag, _gh_scrub(r.stderr or r.stdout)[:600])
        return "[GitHub] %s成功 → %s" % (tag, dest)

    # ---- push：提交并推送（files 为 {相对路径: 内容}）----
    if a in ("push", "commit"):
        if not repo:
            return "[错误] 需要 repo 参数"
        dest = os.path.expanduser(dir or ("~/github/" + repo.strip("/").split("/")[-1]))
        if not os.path.isdir(os.path.join(dest, ".git")):
            return "[错误] 本地没有克隆：%s。先 action=clone。" % dest
        written = []
        if isinstance(files, dict):
            for rel, txt in files.items():
                fp = os.path.join(dest, rel)
                os.makedirs(os.path.dirname(fp) or dest, exist_ok=True)
                open(fp, "w", encoding="utf-8").write(txt)
                written.append(rel)
        elif path and content:
            fp = os.path.join(dest, path)
            os.makedirs(os.path.dirname(fp) or dest, exist_ok=True)
            open(fp, "w", encoding="utf-8").write(content)
            written.append(path)
        subprocess.run(["git", "-C", dest, "add", "-A"],
                       capture_output=True, text=True, timeout=60)
        msg = message or "update from pikachu-agent"
        c = subprocess.run(["git", "-C", dest, "-c", "user.name=pikachu-agent",
                            "-c", "user.email=agent@localhost", "commit", "-m", msg],
                           capture_output=True, text=True, timeout=60)
        both = (c.stdout or "") + (c.stderr or "")
        if c.returncode != 0 and "nothing to commit" not in both:
            return "[错误] 提交失败：%s" % _gh_scrub(both)[:400]
        br = branch
        if not br:
            st0, r0 = _gh_api("GET", "/repos/" + repo.strip("/"))
            br = (r0.get("default_branch") if isinstance(r0, dict) else "") or "main"
        subprocess.run(["git", "-C", dest, "remote", "set-url", "origin", _gh_token_url(repo)],
                       capture_output=True, text=True, timeout=30)
        p = subprocess.run(["git", "-C", dest, "push", "origin", "HEAD:" + br],
                           capture_output=True, text=True, timeout=180)
        subprocess.run(["git", "-C", dest, "remote", "set-url", "origin",
                        "https://github.com/%s.git" % repo.strip("/")],
                       capture_output=True, text=True, timeout=30)
        if p.returncode != 0:
            return "[错误] 推送失败：%s" % _gh_scrub(p.stderr or p.stdout)[:600]
        return "[GitHub] 已推送到 %s (%s)，改动文件：%s" % (
            repo, br, ", ".join(written) or "无")

    # ---- create：新建仓库 ----
    if a in ("create", "new"):
        name = repo.strip("/").split("/")[-1] if repo else ""
        if not name:
            return "[错误] 需要 repo 参数作为仓库名"
        st, d = _gh_api("POST", "/user/repos", {
            "name": name, "description": message or "created by pikachu-agent",
            "private": bool(private), "auto_init": True})
        if st not in (200, 201):
            m = d.get("message") if isinstance(d, dict) else d
            return "[错误] 创建失败(HTTP %s)：%s" % (st, m)
        return "[GitHub] 已创建：%s（%s）" % (
            d.get("full_name"), "私有" if d.get("private") else "公开")

    # ---- search：搜代码 ----
    if a in ("search", "find"):
        if not query:
            return "[错误] 需要 query 参数"
        q = query if ":" in query else (("repo:%s %s" % (repo, query)) if repo else query)
        st, d = _gh_api("GET", "/search/code?q=" + urllib.parse.quote(q))
        if st != 200:
            m = d.get("message") if isinstance(d, dict) else d
            return "[错误] 搜索失败(HTTP %s)：%s" % (st, m)
        items = (d.get("items") or [])[:30]
        rows = ["  %s  %s" % (i.get("path"), i.get("html_url")) for i in items]
        return _truncate("[GitHub] 命中 %s 条：" % d.get("total_count") + "\n" + "\n".join(rows))

    return ("[错误] 不支持的 action：%s。可用：list / repo / read / tree / clone / pull / "
            "push / create / search" % a)


def tool_todo_write(cfg, items) -> str:
    """维护任务清单。items 为 [{text, done}] 的列表。"""
    try:
        if isinstance(items, str):
            items = json.loads(items)
        if not isinstance(items, list):
            return "[错误] items 应为列表"
    except Exception:
        return "[错误] items 格式错误"
    clean = []
    for it in items[:20]:
        if isinstance(it, dict):
            t = str(it.get("text") or "").strip()
            if t:
                clean.append({"text": t[:200], "done": bool(it.get("done"))})
        elif str(it).strip():
            clean.append({"text": str(it).strip()[:200], "done": False})
    if not clean:
        return "[错误] 清单为空，请给出至少一步"
    d = save_todo(clean, TODO_SID)
    try:
        ws_broadcast({"ev": "todo", "d": d})     # 界面立刻更新，不必等这一轮结束
    except Exception:
        pass
    done = sum(1 for x in clean if x["done"])
    lines = [f"{i + 1}. [{'x' if x['done'] else ' '}] {x['text']}" for i, x in enumerate(clean)]
    return _truncate(f"任务清单已更新（{done}/{len(clean)} 完成），用户界面已同步：\n" + "\n".join(lines))


# ---- 需要用户拍板时，弹选择面板让他选（可一次问多个问题）

ASK_WAIT = {}          # id -> {"evt": Event, "answers": list|None, "at": ts}
ASK_LOCK = threading.Lock()

INJECT_QUEUE = queue.Queue()   # 「插队」消息：任务进行中插入，下一轮循环时并入对话（不中断任务）


# ---- 系统通知 -----------------------------------------------------------
# 走 shell 的 `cmd notification post`（Android 12 自带），不依赖 Termux:API，
# 也不用装任何 App。通知失败一律静默跳过 —— 绝不能因为发不出提醒而影响正事。

_NOTIFY_SEEN = {}          # tag -> 上次发出的时间戳（节流用）
_NOTIFY_API_OK = None      # Termux:API 插件装没装（探一次就记住，别反复找）


def notify_api_ready() -> bool:
    """装了 Termux:API 插件吗？装了就能用上点击跳转 / 声音 / 震动。"""
    global _NOTIFY_API_OK
    if _NOTIFY_API_OK is None:
        try:
            _NOTIFY_API_OK = bool(shutil.which("termux-notification"))
        except Exception:
            _NOTIFY_API_OK = False
    return bool(_NOTIFY_API_OK)


def _shq(v) -> str:
    """把字符串安全地塞进 shell 单引号里。"""
    return "'" + str(v if v is not None else "").replace("'", "'\\''") + "'"


def page_url(cfg: dict) -> str:
    try:
        port = int(cfg.get("web_port") or 8765)
    except Exception:
        port = 8765
    return "http://127.0.0.1:%d/" % port


def fmt_dur(secs) -> str:
    try:
        t = int(secs)
    except Exception:
        return "?"
    if t < 60:
        return "%d 秒" % t
    if t < 3600:
        return "%d 分 %d 秒" % (t // 60, t % 60)
    return "%d 小时 %d 分" % (t // 3600, (t % 3600) // 60)


def notify_pref(cfg: dict, key: str) -> bool:
    try:
        return bool(cfg.get("notify_" + key, True))
    except Exception:
        return True


def send_notify(cfg, title, content, tag="termux_agent", url="", throttle=0) -> None:
    """发一条系统通知（只走 Termux:API）；任何异常都吞掉。

    用官方插件的 termux-notification：支持点击跳转、声音、震动。
    插件不在就静默跳过 —— 不再走系统接口，免得同一件事发两条、白打扰人。
    """
    try:
        if not cfg:
            return
        tag = tag or "termux_agent"
        if throttle:
            now = time.time()
            if now - _NOTIFY_SEEN.get(tag, 0.0) < throttle:
                return
            _NOTIFY_SEEN[tag] = now
        title = re.sub(r"\s+", " ", str(title or "皮卡丘")).strip()[:50]
        content = re.sub(r"\s+", " ", str(content or "")).strip()[:250] or "（无详情）"

        if not notify_api_ready():
            return          # 只用 Termux:API 发通知；插件不在就静默不发，不做重复打扰
        args = ["termux-notification", "--title", title, "--content", content,
                "--id", tag, "--priority", "high", "--sound",
                "--vibrate", "300,150,300"]
        if url:
            # --action 要的是 shell 命令（由 dash -c 执行），不是 URL；
            # 得用 termux-open-url 才能在点击时把浏览器拉起来。
            args += ["--action", "termux-open-url " + url]
        subprocess.run(args, capture_output=True, timeout=20)
    except Exception:
        pass


def notify_async(cfg, title, content, tag="termux_agent", url="", throttle=0) -> None:
    """后台线程发通知（adb 往返几百毫秒，别拖慢主流程）。"""
    try:
        if not cfg or not cfg.get("shell_access"):
            return
        threading.Thread(target=send_notify, args=(cfg, title, content),
                         kwargs={"tag": tag, "url": url, "throttle": throttle},
                         daemon=True).start()
    except Exception:
        pass


def notify_long_task(cfg, t0, text) -> None:
    """长任务跑完才提醒，短任务不打扰。"""
    try:
        if not notify_pref(cfg, "done") or not t0:
            return
        secs = time.time() - float(t0)
        if secs < float(cfg.get("notify_min_secs") or 30):
            return
        body = re.sub(r"\s+", " ", str(text or "")).strip()[:180]
        if not body:
            return
        notify_async(cfg, "任务完成 · 用时 " + fmt_dur(secs), body,
                     tag="task_done", url=page_url(cfg), throttle=3)
    except Exception:
        pass


def tool_ask_user(cfg, questions=None, timeout_sec: int = 900) -> str:
    """向用户提问并等他选择；问题会以选择面板弹在输入框上方。"""
    if isinstance(questions, dict):
        questions = [questions]
    qs = []
    for q in (questions or [])[:6]:
        if isinstance(q, str):
            q = {"question": q}
        if not isinstance(q, dict):
            continue
        text = str(q.get("question") or q.get("q") or q.get("text") or "").strip()
        if not text:
            continue
        opts = q.get("options") or q.get("choices") or []
        if isinstance(opts, dict):
            opts = list(opts.keys())
        if not isinstance(opts, list):
            opts = []
        opts = [str(o).strip() for o in opts if str(o).strip()][:8]
        qs.append({
            "question": text[:200],
            "options": opts,
            "multi": bool(q.get("multi") or q.get("multiple")),
            "allow_text": bool(q.get("allow_text") or q.get("free_text") or not opts),
        })
    if not qs:
        return "[错误] questions 不能为空，每一项都要有 question 文本。"

    aid = "ask-%d-%d" % (int(time.time() * 1000), os.getpid() % 1000)
    evt = threading.Event()
    with ASK_LOCK:
        ASK_WAIT[aid] = {"evt": evt, "answers": None, "at": time.time()}
    ws_broadcast({"ev": "ask", "d": {"id": aid, "questions": qs}})
    if notify_pref(cfg, "ask"):
        _q1 = str(qs[0].get("question") or "") if qs else ""
        if len(qs) > 1:
            _q1 += "（共 %d 个问题）" % len(qs)
        notify_async(cfg, "需要你拍板", _q1, tag="ask", url=page_url(cfg))
    try:
        limit = max(30, min(int(timeout_sec or 900), 1800))
    except Exception:
        limit = 900
    deadline = time.time() + limit
    while time.time() < deadline and not evt.wait(0.5):
        try:
            rt = _runtime()
            if rt is not None and rt.stopped:     # 用户点了「停止」→ 别继续挂着
                break
        except Exception:
            pass
    with ASK_LOCK:
        item = ASK_WAIT.pop(aid, None) or {}
    answers = item.get("answers")
    ws_broadcast({"ev": "ask_done", "d": {"id": aid}})
    if answers is None:
        _en = str(globals().get("UI_LANG") or "zh").lower().startswith("en")
        return ("[Note] The user did not answer (they may have walked away, or hit stop). "
                "Continue with the safest option according to your own judgement; if you "
                "truly cannot decide, stop and wait for them — don't guess."
                if _en else
                "[提示] 用户没有作答（可能走开了，或点了停止）。"
                "先按你判断里最稳妥的方案继续；实在不能决定就停下来等他，别自己硬猜。")
    _en = str(globals().get("UI_LANG") or "zh").lower().startswith("en")
    lines = []
    for i, q in enumerate(qs):
        a = answers[i] if i < len(answers) else ""
        if isinstance(a, list):
            a = (", " if _en else "、").join(str(x) for x in a)
        lines.append("%d. %s\n   → %s" % (i + 1, q["question"],
                                          a or ("(none selected)" if _en else "（未选）")))
    return ("User's choices:\n" if _en else "用户的选择：\n") + "\n".join(lines)


def subagent_system() -> str:
    """子代理的系统提示：按界面语言选。"""
    if str(globals().get("UI_LANG") or "zh").lower().startswith("en"):
        return ("You are the sub-agent of the main AI assistant. Focus on the single "
                "sub-task you were given and output only the conclusion itself — do not "
                "explain your process, make small talk, or ask questions.")
    return ("你是主 AI 助手的子代理。专注完成分配给你的这一个子任务，"
            "只输出结论本身，不要解释过程、不要寒暄、不要提问。")


def tool_subagent(cfg, prompt: str, max_tokens: int = 2000) -> str:
    """无工具、专注思考的子代理：把复杂问题的某个子问题甩给它单独想。"""
    if not prompt or not prompt.strip():
        return "[错误] prompt 不能为空"
    msgs = [
        {"role": "system", "content": subagent_system()},
        {"role": "user", "content": prompt},
    ]
    try:
        text = _chat_once(cfg, msgs, max(200, min(int(max_tokens or 2000), 4000)))
    except ApiError as e:
        return f"[错误] {e}"
    except Exception as e:
        return f"[错误] {type(e).__name__}: {e}"
    return text or "（子代理没有返回内容）"


_ADB_STATE = {"serial": None, "checked": 0.0}


def _adb_candidates() -> list:
    """本机 adbd 可能暴露的串口候选（无线调试端口是动态的，所以要探测）。"""
    out = []
    adb = shutil.which("adb")
    if not adb:
        return out
    # 1) 已经连上的 127.0.0.1:* 设备
    try:
        r = subprocess.run([adb, "devices"], capture_output=True, text=True,
                           errors="replace", timeout=15)
        for line in (r.stdout or "").splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[0].startswith("127.0.0.1:"):
                out.append(parts[0])
    except Exception:
        pass
    # 2) 无线调试：用 mDNS 发现端口（Android 11+，无需电脑）
    try:
        r = subprocess.run([adb, "mdns", "services"], capture_output=True, text=True,
                           errors="replace", timeout=20)
        for line in (r.stdout or "").splitlines():
            if "_adb-tls-connect._tcp" in line:
                m = re.search(r"(\d+\.\d+\.\d+\.\d+:\d+)", line)
                if m and m.group(1) not in out:
                    out.append(m.group(1))
    except Exception:
        pass
    # 3) 传统 adb tcpip 模式的固定端口
    if "127.0.0.1:5555" not in out:
        out.append("127.0.0.1:5555")
    # 4) 扫端口兜底：Termux 的 android-tools 被裁掉了 mDNS（adb mdns 直接报
    #    「不支持」），无线调试的动态端口只能自己扫。结果缓存 10 分钟。
    #
    # 踩过的坑（都实测过）：
    #   · 缓存不能用「空列表 = 没扫过」来判断 —— 扫不到时 ports 是 []，假值，
    #     于是每轮重扫（实测第二次调用仍要 14.3 秒）。改用独立时间戳。
    #   · 不能只探 127.0.0.1：无线调试的 adbd 常只绑在 WiFi 网卡上
    #     （adb devices 里 192.168.x.x:xxxxx 就是证据），回环扫不到。
    #   · 逐个 connect_ex 太慢（35536 个端口 14 秒），改成非阻塞 connect +
    #     select 批量等待，快一个数量级。
    _now = time.time()
    if not _ADB_STATE.get("scan_ts") or _now - _ADB_STATE["scan_ts"] > 600:
        _ADB_STATE["ports"] = _scan_adbd_ports()
        _ADB_STATE["scan_ts"] = _now
    for _hp in _ADB_STATE.get("ports", []):
        if _hp not in out:
            out.append(_hp)
    return out


def _local_ips() -> list:
    """本机可用的 IPv4（先回环，再补 outbound IP）。"""
    ips = ["127.0.0.1"]
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))       # 只查路由，不发包
        ip = s.getsockname()[0]
        s.close()
        if ip and ip not in ips:
            ips.append(ip)
    except Exception:
        pass
    return ips


def _scan_range(host: str, lo: int = 30000, hi: int = 65535, batch: int = 600,
                wait: float = 0.35) -> list:
    """非阻塞 connect + select 批量扫一段端口，返回在听的端口。"""
    import select
    hits = []
    for start in range(lo, hi + 1, batch):
        socks = []
        for p in range(start, min(start + batch, hi + 1)):
            s = socket.socket()
            s.setblocking(False)
            try:
                s.connect_ex((host, p))       # 非阻塞：立即返回 EINPROGRESS
                socks.append((p, s))
            except Exception:
                try:
                    s.close()
                except Exception:
                    pass
        pend = socks
        deadline = time.time() + wait
        while pend and time.time() < deadline:
            try:
                _, wl, _ = select.select([], [x[1] for x in pend], [], 0.03)
            except Exception:
                break
            ws = set(wl)
            nxt = []
            for p, s in pend:
                if s in ws:
                    try:
                        if s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0:
                            hits.append(p)
                    except Exception:
                        pass
                    try:
                        s.close()
                    except Exception:
                        pass
                else:
                    nxt.append((p, s))
            pend = nxt
        for _, s in pend:
            try:
                s.close()
            except Exception:
                pass
        if hits:
            break                              # 找到一个就够，不再往下扫
    return hits


def _scan_adbd_ports() -> list:
    """扫出本机 adbd 的 TCP 端口，返回可用的 adb 串口列表。"""
    out = []
    for ip in _local_ips():
        for p in _scan_range(ip):
            hp = "%s:%d" % (ip, p)
            if hp not in out:
                out.append(hp)
        if out:
            break                              # 一个网卡上找到就够了
    return out


def _adb_ensure(force: bool = False):
    """确保有一个可用的本机 adbd 串口；返回串口或 None。

    支持三种情形：已连接的 tcpip 端口、无线调试（mDNS 自动发现）、以及 5555。
    这样在**没有电脑**的情况下也能拿到 shell 身份。
    """
    adb = shutil.which("adb")
    if not adb:
        return None
    now = time.time()
    if not force and _ADB_STATE["serial"] and now - _ADB_STATE["checked"] < 60:
        return _ADB_STATE["serial"]
    _ADB_STATE["checked"] = now
    for cand in _adb_candidates():
        try:
            subprocess.run([adb, "connect", cand], capture_output=True,
                           timeout=12, text=True, errors="replace")
            r = subprocess.run([adb, "-s", cand, "shell", "id"], capture_output=True,
                               timeout=15, text=True, errors="replace")
            if "uid=" in (r.stdout or ""):
                _ADB_STATE["serial"] = cand
                return cand
        except Exception:
            continue
    _ADB_STATE["serial"] = None
    return None


def adb_keepalive_loop() -> None:
    """周期性维护本机 adb 通道，让 sysshell 随时可用（脱离电脑也有效）。"""
    time.sleep(20)
    while True:
        try:
            _adb_ensure(force=True)
        except Exception:
            pass
        time.sleep(180)


def tool_sysshell(cfg, command: str, timeout_sec: int = 60) -> str:
    """以 shell（adb）身份执行命令，拥有比普通应用更高的权限。

    能读系统设置、dumpsys、getprop、pm/am、改全局设置等。
    不是 root：/data/data 下的其它应用数据、system 只读分区仍不可写。
    """
    if not cfg.get("shell_access"):
        return "[未启用] 增强权限未开启，请在界面设置里打开「增强权限」。"
    if not command or not command.strip():
        return "[错误] command 不能为空"
    adb = shutil.which("adb")
    if not adb:
        return "[错误] 设备上未安装 adb。请在 Termux 执行 pkg install android-tools 后重试。"
    serial = _adb_ensure()
    if not serial:
        return ("[错误] 连不上本机 adb 调试通道。两条路任选：\n"
                "  · 有电脑时：电脑执行 adb tcpip 5555（平板重启后需重做一次）\n"
                "  · 没电脑时：设置 → 系统 → 开发者选项 → 打开「无线调试」，"
                "用 `agent adbpair <配对端口> <配对码>` 配对一次，之后就能自动连接。")
    secs = max(5, min(int(timeout_sec or 60), 300))
    r = _exec_stream([adb, "-s", serial, "shell", command], cfg["workdir"], secs, _runtime())
    if r["error"]:
        return r["error"]
    if r["killed"] == "user":
        return _truncate(r["out"] or "") + "\n[已停止] 命令被用户中断，进程已终止。"
    if r["killed"] == "timeout":
        return _truncate(r["out"] or "") + f"\n[超时] 命令超过 {secs}s 被终止"
    out = (r["out"] or "").strip() or "(无输出)"
    return _truncate(f"[shell 身份 · 退出码 {r['rc']}]\n{out}")


# ---- HDC：远程控制已配对的华为手机（鸿蒙）。协议实现在同目录 hdc.py ----
HDC_TARGET_FILE = str(APP_DIR / "hdc_target.json")
_HDC_CACHE = {"conn": None, "key": ""}


def _hdc_mod():
    """动态加载同目录下的 hdc.py（HDC 协议实现）。"""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hdc.py")
    if not os.path.exists(path):
        return None
    try:
        spec = importlib.util.spec_from_file_location("hdc_mod", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def _hdc_target(host="", port=0):
    """取目标手机地址：显式给了就用，否则读上次记住的。"""
    if host and port:
        return str(host), int(port)
    try:
        with open(HDC_TARGET_FILE) as f:
            d = json.load(f)
        return str(d.get("host") or ""), int(d.get("port") or 0)
    except Exception:
        return "", 0


def _hdc_save_target(host, port):
    try:
        with open(HDC_TARGET_FILE, "w") as f:
            json.dump({"host": str(host), "port": int(port)}, f)
    except Exception:
        pass


def _hdc_connect(mod, host, port, timeout=40):
    """取一条已认证连接；同目标复用，断了自动重连。"""
    key = "%s:%d" % (host, port)
    conn = _HDC_CACHE.get("conn")
    if conn is not None and _HDC_CACHE.get("key") == key:
        try:
            conn.sock.getpeername()
            return conn, None
        except Exception:
            _HDC_CACHE["conn"] = None
    try:
        conn = mod.Hdc(host, int(port), timeout=min(20, timeout))
        conn.connect()
        ok, msg = conn.authenticate(auth_wait=min(90, timeout))
        if not ok:
            try:
                conn.close()
            except Exception:
                pass
            return None, msg
        _HDC_CACHE["conn"] = conn
        _HDC_CACHE["key"] = key
        return conn, None
    except Exception as e:
        return None, "连接失败: %s" % e


WPS_CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_call.py")


def tool_wps(cfg, action: str = "list", tool: str = "", args: str = "") -> str:
    """操作 WPS/Office 文档（本地 MCP 服务，无需登录账号）。

    action:
      list  列出可用工具（默认）
      call  调用某个工具，配 tool + args
      ping  握手自检
    """
    if not os.path.exists(WPS_CLI):
        return "[错误] 找不到 mcp_call.py（WPS MCP 客户端）"
    act = (action or "list").strip().lower()
    cmd = [sys.executable, WPS_CLI]
    if act in ("ping", "list"):
        cmd.append(act)
    elif act == "call":
        if not tool:
            return "[错误] action=call 时必须给 tool（工具名，先 action=list 看有哪些）"
        cmd += ["call", str(tool).strip()]
        if args:
            if not isinstance(args, str):
                args = json.dumps(args, ensure_ascii=False)
            try:
                json.loads(args)
            except Exception:
                return "[错误] args 不是合法 JSON，请传 JSON 字符串（键和值都用双引号），例：{\"filename\":\"周报.docx\"}"
            cmd.append(args)
    else:
        return "[错误] action 只能是 list / call / ping"
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception as e:
        return "[错误] 调用 WPS MCP 失败：%s" % e
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if err and p.returncode != 0:
        return (out + "\n[stderr] " + err[:500]).strip() or "[错误] 无输出"
    return out or "[完成] 无输出"


def tool_hdcmate(cfg, value: str = "", action: str = "exec", host: str = "",
                 port: int = 0, timeout: int = 40) -> str:
    """远程控制已配对的华为手机（鸿蒙 HDC 协议）。

    action:
      exec   在手机上执行一条 shell 命令（默认），命令写在 value 里
      test   测试连接与认证是否正常（复用已建立的连接）
      target 查看/设置记住的手机地址（给了 host+port 就记住）
    """
    mod = _hdc_mod()
    if mod is None:
        return "[错误] hdc.py 缺失或加载失败（HDC 协议实现文件）"
    act = (action or "exec").strip().lower()
    t_host, t_port = _hdc_target(host, port)
    if t_host and t_port and (host or port):
        _hdc_save_target(t_host, t_port)
        _HDC_CACHE["conn"] = None       # 换了目标必须重连
    if act == "target":
        if not t_host:
            return "[提示] 还没记过目标。用 action=target host=192.168.x.x port=xxxxx 设置一次。"
        return "当前手机目标：%s:%d" % (t_host, t_port)
    if not t_host or not t_port:
        return ("[提示] 还没有配对的手机。请在手机「设置→系统和更新→开发人员选项→无线调试」里"
                "看 IP 和端口，然后调用一次： hdcmate action=target host=192.168.x.x port=xxxxx")
    to = max(5, min(120, int(timeout or 40)))
    conn, err = _hdc_connect(mod, t_host, t_port, to)
    if conn is None:
        return "[HDC 错误] %s" % (err or "未知错误")
    if act in ("test", "status"):
        return "HDC 已连通 %s:%d（认证成功，连接已复用）" % (t_host, t_port)
    if not value:
        return "[提示] action=exec 时请在 value 里写要执行的命令"
    try:
        out = conn.execute(value, timeout=to)
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        _HDC_CACHE["conn"] = None
        return "[HDC 执行失败] %s" % e
    out = (out or "").strip() or "(无输出)"
    return _truncate("[手机 %s:%d]\n%s" % (t_host, t_port, out))


def tool_wx_auto(cfg, action: str = "status") -> str:
    """微信自动陪聊（技能文件 wx_auto.py）：读屏体检 / 启动 / 停止 / 看状态。

    action:
      read   只读当前屏幕（多模态看图），绝不发送任何东西
      once   体检：跑三道闸门并给出判定
      run    后台启动自动回复（对方发新消息才回，已回过的不重复回）
      stop   停止
      status 看状态与最近日志
    """
    py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wx_auto.py")
    if not os.path.exists(py):
        return "[错误] 找不到 wx_auto.py（技能文件缺失）"
    act = (action or "status").strip().lower()
    if act in ("start", "run", "启动"):
        try:
            subprocess.Popen([sys.executable, py, "run"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            time.sleep(1.5)
            return ("微信陪聊已在后台启动。\n"
                    "· 查看状态：wx_auto(action=\"status\")\n"
                    "· 停止：wx_auto(action=\"stop\")\n"
                    "· 注意：需要微信聊天窗口在屏幕上可见；识别不准时只记日志、不会乱发。")
        except Exception as e:
            return "[错误] 启动失败：%s" % e
    if act in ("read", "once", "stop", "status"):
        try:
            r = subprocess.run([sys.executable, py, act], capture_output=True, text=True,
                               errors="replace", timeout=300)
            return (((r.stdout or "") + (r.stderr or "")).strip() or "（无输出）")[:4000]
        except Exception as e:
            return "[错误] 执行失败：%s" % e
    return "[错误] action 只能是 read / once / run / stop / status"


def _static_ref_check(path) -> str:
    """静态体检：模块级字典里有没有「名字还没定义就被引用」（会 NameError）。

    只盯全大写字典字面量（工具注册表这类）里直接引用的 `tool_xxx` 名字——
    这正是「先注册、后定义 / 忘记定义」那种一启动就崩的形态。
    返回空串表示没发现问题，否则返回一句人话说明（最多 5 条）。
    """
    import ast
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return ""                      # 语法问题交给 py_compile 报，这里不重复
    bound = {}                         # 名字 → 最早出现（绑定）的行号
    for node in ast.walk(tree):
        names = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            names = [node.target.id] if isinstance(node.target, ast.Name) else []
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [(a.asname or a.name).split(".")[0] for a in node.names]
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names = [node.id]
        for nm in names:
            if nm and (nm not in bound or node.lineno < bound[nm]):
                bound[nm] = node.lineno
    bad = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id.isupper() for t in node.targets)):
            continue
        for sub in ast.walk(node.value):
            if isinstance(sub, ast.Name) and sub.id.startswith("tool_"):
                ln = getattr(sub, "lineno", 0)
                first = bound.get(sub.id)
                if first is None or first > ln:
                    bad.append("第 %d 行用到 %s，但它%s" % (
                        ln, sub.id,
                        "在文件里根本没定义" if first is None
                        else "要到第 %d 行才定义" % first))
    return "；".join(sorted(set(bad))[:5])


def tool_selfupdate(cfg, summary: str = "", new_code: str = "",
                    patch_old: str = "", patch_new: str = "") -> str:
    """安全地修改自己的源码。

    流程：备份当前版本为 last-good → 应用改动 → 语法检查 →
    失败则立即还原（服务不受影响）→ 成功则几秒后自动重启加载新代码。
    看门狗会盯着重启后的健康状态；若新代码起不来会自动回滚。
    """
    if not is_termux():
        return "[未启用] 自我更新只在平板上可用。"
    src = Path(__file__).resolve()
    try:
        current = src.read_text(encoding="utf-8")
    except Exception as e:
        return f"[错误] 读不到自己的源码：{e}"

    if new_code:
        changed = new_code
    elif patch_old and patch_new is not None and patch_old != patch_new:
        cnt = current.count(patch_old)
        if cnt == 0:
            return "[错误] patch_old 在源码里没找到，改动未应用。"
        if cnt > 1:
            return f"[错误] patch_old 出现 {cnt} 次，不唯一，请给更长的上下文。"
        changed = current.replace(patch_old, patch_new)
    else:
        return "[错误] 请提供 new_code（整文件）或 patch_old + patch_new（局部替换）。"

    if changed == current:
        return "[提示] 改动后内容与原来完全相同，未做任何修改。"
    if len(changed) < len(current) * 0.5:
        return "[错误] 新内容长度只有原来的一半以下，疑似截断，已拒绝应用。"

    # 0) 版本号自动 +1（1.6.0 → 1.6.1，patch 到 10 进位到 minor，再进位到 major）。
    #    取的是 current 里的值，不能用模块级 VERSION —— 本进程可能还是旧代码。
    #    此前 selfupdate 从不改 VERSION，只从摘要开头抠版本号，于是摘要没写版本时
    #    更新记录里全是空版本、界面顶部永远挂着同一个号，看不出「改过」。
    _mv = re.search(r'^VERSION\s*=\s*"([^"]+)"', current, re.M)
    _vp = [int(x) for x in (_mv.group(1) if _mv else "1.0.0").split(".")[:3]]
    while len(_vp) < 3:
        _vp.append(0)
    _vp[2] += 1
    if _vp[2] > 9:
        _vp[2] = 0
        _vp[1] += 1
    if _vp[1] > 9:
        _vp[1] = 0
        _vp[0] += 1
    new_ver = ".".join(str(x) for x in _vp)
    if re.search(r'^VERSION\s*=\s*"[^"]*"', changed, re.M):
        changed = re.sub(r'^VERSION\s*=\s*"[^"]*"', 'VERSION = "%s"' % new_ver,
                         changed, count=1, flags=re.M)

    # 1) 备份当前可用版本（看门狗的回滚目标）
    try:
        VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        (VERSIONS_DIR / f"agent-{stamp}.py").write_text(current, encoding="utf-8")
        LAST_GOOD.write_text(current, encoding="utf-8")
    except Exception as e:
        return f"[错误] 备份失败，为安全起见取消改动：{e}"

    # 2) 写入并做语法检查
    try:
        src.write_text(changed, encoding="utf-8")
        r = subprocess.run([sys.executable, "-m", "py_compile", str(src)],
                           capture_output=True, text=True, timeout=60)
    except Exception as e:
        src.write_text(current, encoding="utf-8")
        return f"[错误] 语法检查异常，已还原：{e}"
    if r.returncode != 0:
        src.write_text(current, encoding="utf-8")
        err = (r.stderr or r.stdout or "").strip().splitlines()
        return ("[错误] 新代码语法有问题，已自动还原（服务未受影响）：\n  "
                + "\n  ".join(err[-6:]))

    # 2.5) 静态体检：语法没问题，但可能存在「用到时还没定义」的名字（一启动就 NameError）
    why = _static_ref_check(src)
    if why:
        src.write_text(current, encoding="utf-8")
        return ("[错误] 静态检查发现「名字还没定义就被引用」（会 NameError），"
                "已自动还原（服务未受影响）：\n  " + why)

    # 3) 成功 → 留一条更新记录，供界面「更新记录」查看
    #    （这一段故意自带实现：本进程可能还是旧代码，不能依赖新加的函数）
    try:
        _cl = APP_DIR / "changelog.json"
        _items = json.loads(_cl.read_text(encoding="utf-8")) if _cl.exists() else []
        if not isinstance(_items, list):
            _items = []
        _raw = (summary or "（未说明改动）").strip()
        _v = re.match(r"\s*[vV]?(\d+\.\d+(?:\.\d+)?)", _raw)
        _items.insert(0, {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "kind": "自我更新",
            "version": _v.group(1) if _v else new_ver,
            "summary": (re.sub(r"^\s*[vV]?\d+\.\d+(?:\.\d+)?\s*[：:]\s*", "", _raw).strip()
                        or _raw).replace("\n", " "),
        })
        _cl.write_text(json.dumps(_items[:200], ensure_ascii=False, indent=1), encoding="utf-8")
        with open(APP_DIR / "evolve.log", "a", encoding="utf-8") as _f:
            _f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [自我更新] "
                     f"v{new_ver} {_raw[:120]}\n")
    except Exception:
        pass

    # 4) 写「接续标记」：重启后自愈读到它就会自动接着跑一轮，
    #    不用用户再敲一句"继续"（清单可能已全打勾，但收尾/验证往往还没做）。
    try:
        (APP_DIR / "_resume.md").write_text(
            "刚刚通过 selfupdate 改了自身代码并重启，本次改动：%s\n"
            "请先确认改动已生效（可读代码或跑一条命令验证），再继续完成用户原本的需求；"
            "如果确实都做完了，就简要汇报结果收尾。" % (summary or "未说明改动"),
            encoding="utf-8")
    except Exception:
        pass

    # 5) 几秒后重启服务加载新代码
    _restart_soon(delay=6, cfg=cfg)
    return (f"[成功] 已更新自身代码（{summary or '未说明改动'}）。"
            "服务将在约 6 秒后自动重启加载新代码；如果新代码起不来，"
            "看门狗会自动回滚到上一个可用版本。"
            "请立刻结束本回合，不要再调用任何工具，直接向用户说明你改了什么。")


# ---------------------------------------------------------------- 图像处理（WorkBuddy 平台服务）

# 实测跑通的接口：提交异步任务 → 轮询 → 下载成品。
# 鉴权用 Bearer（和 DeepSeek 一样的标准形式），密钥是 workbuddy.cn/profile/keys 申请的 ck_ 开头那串。
WB_IMG_BASE = "https://copilot.tencent.com/v2/async/images"
IMG_OPERATIONS = {
    "erase":   "vod-image-erase",              # 擦除文字 / 水印
    "restore": "vod-image-restore",            # 老照片修复
    "enhance": "vod-image-enhance-fidelity",   # 画质增强
    "beauty":  "vod-portrait-beauty",          # 人像美化
    "matting": "vod-image-matting",            # 抠图去背景
}
IMG_OP_DESC = [("erase", "擦除文字/水印"), ("restore", "老照片修复"),
               ("enhance", "画质增强"), ("beauty", "人像美化"),
               ("matting", "抠图去背景")]
IMG_MAX_BYTES = 4 * 1024 * 1024      # 服务端限制 4MB


def _img_post(url: str, body: dict, key: str, timeout: int = 90) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8", "ignore"))
        except Exception:
            raise ApiError("图像服务返回 HTTP %s" % e.code)
    except Exception as e:
        raise ApiError("连不上图像服务：%s" % e)


def tool_imgedit(cfg, operation="", image="", prompt="", out="", timeout_sec=300) -> str:
    """图像处理：擦除文字水印 / 老照片修复 / 画质增强 / 人像美化 / 抠图。

    走 WorkBuddy 平台的图像服务，用配置里的 wb_api_key。
    异步任务：提交 → 轮询（默认最长 300 秒）→ 把成品下载到本地。
    """
    op = (operation or "").strip().lower()
    if op not in IMG_OPERATIONS:
        return ("[错误] operation 必须是以下之一：\n"
                + "\n".join("  · %s —— %s" % (k, d) for k, d in IMG_OP_DESC))
    key = (cfg.get("wb_api_key") or "").strip()
    if not key:
        return ("[未配置] 缺少 WorkBuddy API Key。\n"
                "请到 workbuddy.cn/profile/keys 申请（ck_ 开头），"
                "填进配置项 wb_api_key 后即可使用。")
    src_img = (image or "").strip()
    if not src_img:
        return "[错误] image 不能为空（本地图片路径，或 http(s) 图片地址）"

    body = {"model": IMG_OPERATIONS[op], "response_format": "url"}
    if src_img.startswith("http://") or src_img.startswith("https://"):
        body["image_url"] = [src_img]
    else:
        p = Path(os.path.expanduser(src_img))
        if not p.is_file():
            return "[错误] 找不到图片文件：%s" % src_img
        raw = p.read_bytes()
        if len(raw) >= IMG_MAX_BYTES:
            return "[错误] 图片必须小于 4MB（当前 %.1fMB）" % (len(raw) / 1048576)
        body["image"] = [base64.b64encode(raw).decode("ascii")]
    if prompt:
        body["prompt"] = prompt

    r = _img_post(WB_IMG_BASE + "/edits", body, key)
    if r.get("code") != 0:
        return "[失败] 提交被拒：%s" % (r.get("msg") or json.dumps(r, ensure_ascii=False)[:200])
    task_id = str(((r.get("data") or {}).get("id") or "")).strip()
    if not task_id:
        return "[失败] 服务端未返回任务ID：%s" % json.dumps(r, ensure_ascii=False)[:200]

    deadline = time.time() + max(30, min(int(timeout_sec or 300), 900))
    done = {}
    while time.time() < deadline:
        time.sleep(5)
        q = _img_post(WB_IMG_BASE + "/tasks", {"task_id": task_id}, key, timeout=60)
        d = q.get("data") or {}
        st = str(d.get("status", "")).lower()
        if st == "completed":
            done = d
            break
        if st == "failed":
            msg = (d.get("error") or {}).get("message") or "处理失败"
            return "[失败] %s（任务 %s）" % (msg, task_id)
    if not done:
        return "[超时] 任务 %s 在 %ss 内未完成，可稍后用任务ID查询" % (task_id, timeout_sec)

    urls = [x.get("url") for x in (done.get("data") or []) if x.get("url")]
    if not urls:
        return "[失败] 任务完成但没返回图片地址：%s" % json.dumps(done, ensure_ascii=False)[:200]

    target = out or "~/.termux-agent/outputs"
    dest = Path(os.path.expanduser(target))
    if dest.suffix:                        # 传的是完整文件路径
        outdir = dest.parent
    else:
        outdir = dest
        dest = outdir / ("%s-%s.png" % (op, datetime.now().strftime("%m%d-%H%M%S")))
    outdir.mkdir(parents=True, exist_ok=True)
    saved = []
    for i, u in enumerate(urls):
        d2 = dest if len(urls) == 1 else dest.with_name(
            "%s-%d%s" % (dest.stem, i + 1, dest.suffix or ".png"))
        try:
            with urllib.request.urlopen(u, timeout=120) as r2:
                d2.write_bytes(r2.read())
            saved.append(str(d2))
        except Exception as e:
            saved.append("(下载失败：%s)" % e)

    usage = done.get("usage") or {}
    return _truncate(
        "图像处理完成（%s）\n"
        "成品已保存到：%s\n"
        "耗时/花费：额度 %s\n"
        "任务ID：%s\n"
        "提示：可以接着用 bash 打开或移动这个文件。"
        % (op, "\n".join(saved), usage.get("credit", "?"), task_id))


TOOL_IMPLS = {
    "bash": tool_bash,
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "edit_file": tool_edit_file,
    "apply_patch": tool_apply_patch,
    "list_dir": tool_list_dir,
    "grep": tool_grep,
    "fetch_url": tool_fetch_url,
    "download": tool_download,
    "web_search": tool_web_search,
    "github": tool_github,
    "todo_write": tool_todo_write,
    "ask_user": tool_ask_user,
    "subagent": tool_subagent,
    "sysshell": tool_sysshell,
    "selfupdate": tool_selfupdate,
    "wx_auto": tool_wx_auto,
    "hdcmate": tool_hdcmate,
    "wps": tool_wps,
    "imgedit": tool_imgedit,
}

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "hdcmate",
        "description": ("远程控制已配对的华为手机（鸿蒙，走 HDC 协议，非 adb）。"
                        "完整技能文档见 ~/.termux-agent/hdc_skill.md（含协议说明、鸿蒙常用命令、"
                        "UI 自动化用法、踩过的坑），需要细节时先读它。"
                        "action=exec 在手机上执行 shell 命令（命令写在 value 里）；"
                        "action=target 设定并记住手机地址（host + port，只需一次）；"
                        "action=test 测试连接是否正常。"
                        "初次使用必须先 action=target 设置一次地址；地址从手机「设置→系统和更新→"
                        "开发人员选项→无线调试」里的“IP 地址和端口”获得。"
                        "注意：手机重启或无线路由变化后端口会变，届时需要重新 target。"
                        "权限与 adb shell 相同（uid 2000），不能读应用私有数据、不能 root。"
                        "常用命令：bm dump -a -l 列应用；aa start -b 包名 -a EntryAbility 启动应用；"
                        "uitest dumpLayout -p /data/local/tmp/lay.json 导出界面布局，"
                        "uitest uiInput click X Y 点击坐标（可做完整 UI 自动化）。"),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "description": "exec / target / test，默认 exec"},
            "value": {"type": "string", "description": "action=exec 时要执行的 shell 命令"},
            "host": {"type": "string", "description": "手机 IP，仅 action=target 时给"},
            "port": {"type": "integer", "description": "无线调试端口，仅 action=target 时给"},
            "timeout": {"type": "integer", "description": "超时秒数，默认 40"},
        }, "required": []}}},
    {"type": "function", "function": {
        "name": "wx_auto",
        "description": ("微信自动陪聊：读微信消息 / 自动回复指定联系人（用户已授权，对方知情）。"
                        "action=read 只读屏（多模态看图），绝不发送；"
                        "once 体检：跑三道闸门并给人看判定结果；"
                        "run 后台启动自动回复（对方发新消息才回、已回过的不重复回）；"
                        "stop 停止；status 看状态与日志。"
                        "读屏靠多模态大模型看截图，需要微信聊天窗口在屏幕上可见。"
                        "安全限制：识别不准/含系统词只记日志，找不到发送按钮就放弃，每小时上限 20 条。"),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "description": "read / once / run / stop / status"},
        }, "required": ["action"]}}},
    {"type": "function", "function": {
        "name": "bash",
        "description": ("在当前设备上执行 shell 命令并返回输出。这是主力工具，可用来查看系统状态、"
                        "运行程序、安装 pkg 包、管理文件等。终端类操作请优先用它。"),
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "要执行的完整命令"},
            "timeout_sec": {"type": "integer", "description": "超时秒数，默认 120，上限 900"},
            "background": {"type": "boolean",
                           "description": ("设为 true 表示后台长跑：立刻返回 pid 和日志路径，"
                                           "不占用等待时间。装大包、长时间下载、批量处理等用它，"
                                           "之后再用 read_file/grep 查日志。默认 false（等命令跑完）")},
        }, "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": ("读取文本文件内容，带行号。大文件请配合 offset/limit 分页读取；"
                        "offset 传负数表示从末尾往回数（如 -50 = 最后 50 行），适合看日志结尾。"),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件路径，相对路径基于工作目录"},
            "offset": {"type": "integer", "description": "起始行号，从 1 开始，默认 1；负数表示从末尾数"},
            "limit": {"type": "integer", "description": f"读取行数，默认 800，单次最多 {MAX_READ_LINES} 行"},
        }, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "写入文件（整文件覆盖，不存在则新建，会自动创建父目录）。修改已有文件请优先用 edit_file。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string", "description": "完整文件内容"},
        }, "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "apply_patch",
        "description": ("一次应用多处/多文件的补丁（unified diff 风格），多个文件会原子生效："
                        "全部能对上才写盘，任何一处对不上就整体不改。改多处代码、跨文件改动优先用它，"
                        "比多次 edit_file 更省往返也更安全。格式：\n"
                        "*** Update File: 路径\n@@\n 上下文行\n-要删的行\n+要加的行\n"
                        "（新增文件用 *** Add File: 路径，删除用 *** Delete File: 路径；"
                        "也支持标准的 --- a/x +++ b/x 写法。）"),
        "parameters": {"type": "object", "properties": {
            "patch": {"type": "string", "description": "补丁全文（可含多个文件、多段 @@）"},
        }, "required": ["patch"]}}},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": ("对文件做精确字符串替换。old_string 必须与原文完全一致（含缩进），"
                        "且默认要求唯一匹配。修改已有文件首选此工具。"),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string", "description": "要被替换的原片段，需带足够上下文保证唯一"},
            "new_string": {"type": "string", "description": "替换后的内容；传空字符串表示删除该片段"},
            "replace_all": {"type": "boolean", "description": "是否替换所有匹配项，默认 false"},
        }, "required": ["path", "old_string", "new_string"]}}},
    {"type": "function", "function": {
        "name": "list_dir",
        "description": "列出目录内容，含类型、大小、修改时间。用于了解项目结构和定位文件。",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "目录路径，默认工作目录"},
            "pattern": {"type": "string", "description": "可选的正则，按文件名过滤"},
        }, "required": []}}},
    {"type": "function", "function": {
        "name": "grep",
        "description": "在目录中递归检索文本内容（支持正则），返回 文件:行号: 内容。找代码位置用它，不要用 bash 跑 grep。",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string", "description": "正则表达式"},
            "path": {"type": "string", "description": "检索根路径，默认工作目录"},
            "include": {"type": "string", "description": "仅检索文件名匹配该正则的文件，如 \\.py$"},
            "max_results": {"type": "integer", "description": "最多返回条数，默认 200"},
        }, "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "fetch_url",
        "description": "抓取网页或 API 内容并转为纯文本，用于查阅资料、文档、接口返回。只适合读文本，不要用它下大文件。",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "完整 URL"},
            "max_chars": {"type": "integer", "description": "最多返回字符数，默认 20000"},
        }, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "download",
        "description": "把文件流式下载到本地磁盘（不截断、带超时），用于下载 APK、安装包、图片等大文件。下载微信这类应用安装包时用它，不要用 fetch_url 或 curl。",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string", "description": "下载地址"},
            "path": {"type": "string", "description": "保存路径，例如 ~/storage/shared/Download/wechat.apk"},
            "timeout_sec": {"type": "integer", "description": "超时秒数，默认 600"},
        }, "required": ["url", "path"]}}},
    {"type": "function", "function": {
        "name": "web_search",
        "description": "联网搜索（无需 Key）。当需要了解最新信息、查资料、核实事实时用它，而不是凭记忆回答。",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "max_results": {"type": "integer", "description": "最多返回条数，默认 8，上限 15"},
        }, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "github",
        "description": ("操作 GitHub 仓库（读写自己的仓库，需已配置 Token）。"
                        "clone/pull 把仓库拉到本地，改完用 push 提交并推送。"
                        "action: list 列仓库 / repo 仓库详情 / read 读文件 / tree 列目录 / "
                        "clone 克隆 / pull 拉取 / push 提交推送 / create 新建 / search 搜代码。"),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "description": "list/repo/read/tree/clone/pull/push/create/search"},
            "repo": {"type": "string", "description": "owner/name；create 时传仓库名即可"},
            "path": {"type": "string", "description": "read/tree/push 时的文件相对路径"},
            "ref": {"type": "string", "description": "分支或 commit，默认仓库默认分支"},
            "content": {"type": "string", "description": "push 时写入的文件内容（配合 path）"},
            "files": {"type": "object", "description": "push 时批量写：{相对路径: 内容}"},
            "message": {"type": "string", "description": "提交信息 / 仓库描述"},
            "dir": {"type": "string", "description": "本地目录，默认 ~/github/<仓库名>"},
            "branch": {"type": "string", "description": "推送目标分支，留空则用仓库默认分支"},
            "query": {"type": "string", "description": "search 的搜索词"},
            "private": {"type": "boolean", "description": "create 时是否私有，默认 true"},
        }, "required": ["action"]}}},
    {"type": "function", "function": {
        "name": "todo_write",
        "description": ("维护任务清单：把 3 步以上的大任务拆成 2~6 步写在上面，"
                        "每完成一步就立刻再调一次并把该步 done 置 true（不要攒着）。"
                        "清单会实时显示在用户输入框上方、可点开看进度，中断或重连后据此继续未完成的步骤。"),
        "parameters": {"type": "object", "properties": {
            "items": {"type": "array", "description": "任务列表",
                      "items": {"type": "object", "properties": {
                          "text": {"type": "string", "description": "任务描述"},
                          "done": {"type": "boolean", "description": "是否完成"}}}},
        }, "required": ["items"]}}},
    {"type": "function", "function": {
        "name": "subagent",
        "description": "把某个独立的子问题交给一个专注的「子代理」单独思考（不接触本机文件），返回它的结论。适合拆解复杂任务、让多个难点并行推进。",
        "parameters": {"type": "object", "properties": {
            "prompt": {"type": "string", "description": "给子代理的任务，要写清楚目标与期望的产出"},
            "max_tokens": {"type": "integer", "description": "输出上限，默认 2000"},
        }, "required": ["prompt"]}}},
    {"type": "function", "function": {
        "name": "sysshell",
        "description": ("以 shell（adb）身份执行命令，权限高于普通应用：可读系统设置、dumpsys、getprop、"
                        "pm/am、修改全局设置等。普通 bash 遇到权限拒绝时，改用这个。不是 root。"),
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "要执行的命令"},
            "timeout_sec": {"type": "integer", "description": "超时秒数，默认 60"},
        }, "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "selfupdate",
        "description": ("修改你自己的源码（~/.termux-agent/agent.py），用于修复自身 bug 或给自己加功能。"
                        "会自动备份 + 语法检查，失败立即还原；成功后服务自动重启加载新代码，"
                        "若起不来看门狗会回滚。注意：调用前请先用 read_file 读源码确认，"
                        "改动要小且明确；本回合会在调用后结束。"),
        "parameters": {"type": "object", "properties": {
            "summary": {"type": "string", "description": "一句话说明你改了什么"},
            "new_code": {"type": "string", "description": "整份新源码（与 patch_old 二选一，文件很大，优先用 patch 方式）"},
            "patch_old": {"type": "string", "description": "要被替换的原文片段（必须与源码完全一致且唯一）"},
            "patch_new": {"type": "string", "description": "替换后的内容"},
        }, "required": ["summary"]}}},
    {"type": "function", "function": {
        "name": "ask_user",
        "description": ("向用户提问、让他从你给的选项里挑（会弹出选择面板等他作答，"
                        "可以一次问多个问题）。只在**必须由用户拍板**时才用：花钱、删除或覆盖数据、"
                        "装/卸 App、方案取舍、需要你拿不到的信息（密码、偏好、真实意图）。"
                        "能从文件/日志/网络查到、或按常理能自行决定的小事（起什么名字、先做哪一步、"
                        "要不要重试）不要问，直接做，并在汇报里说明你自己的选择。"),
        "parameters": {"type": "object", "properties": {
            "questions": {"type": "array", "description": "1~6 个问题", "items": {
                "type": "object", "properties": {
                    "question": {"type": "string", "description": "问题本身，写清楚在纠结什么"},
                    "options": {"type": "array", "items": {"type": "string"},
                                "description": "给用户挑的选项（2~6 个，尽量短）"},
                    "multi": {"type": "boolean", "description": "是否可多选，默认单选"},
                    "allow_text": {"type": "boolean", "description": "是否允许用户自由填写"},
                }, "required": ["question"]}},
            "timeout_sec": {"type": "integer", "description": "等多久算超时，默认 900 秒"},
        }, "required": ["questions"]}}},
    {"type": "function", "function": {
        "name": "wps",
        "description": ("生成/编辑 WPS（Word/Excel/PPT）文档，本地 MCP 服务实现，不需要登录账号。"
                        "action=list 先看有哪些工具（默认）；action=call 时用 tool 指定工具名、"
                        "args 传 JSON 字符串参数，如 tool=create_spreadsheet args={\"filename\":\"表.xlsx\"}。"
                        "文件都落在 ~/storage/shared/WPS_AI/，手机上用 WPS 打开即可。"),
        "parameters": {"type": "object", "properties": {
            "action": {"type": "string", "description": "list（默认，列工具）/ call（调用）/ ping"},
            "tool": {"type": "string", "description": "action=call 时的工具名，如 create_document"},
            "args": {"type": "string", "description": "action=call 时的参数，JSON 字符串"},
        }, "required": []}}},
    {"type": "function", "function": {
        "name": "imgedit",
        "description": ("对图片做 AI 处理，五种操作：\n"
                        "erase=擦除图片上的文字/水印；restore=老照片修复；"
                        "enhance=画质增强；beauty=人像美化；matting=抠图去背景。\n"
                        "image 传本地图片路径或 http(s) 地址（本地图需小于 4MB）。\n"
                        "异步处理，一般十几秒完成，成品会下载到本地并返回路径。\n"
                        "注意：这会消耗账号额度（约每张 0.6），别批量乱刷。"),
        "parameters": {"type": "object", "properties": {
            "operation": {"type": "string",
                          "description": "erase / restore / enhance / beauty / matting"},
            "image": {"type": "string", "description": "本地图片路径或 http(s) 图片地址"},
            "prompt": {"type": "string", "description": "可选：额外要求，部分操作支持"},
            "out": {"type": "string", "description": "可选：成品保存路径或目录，默认 ~/.termux-agent/outputs/"},
            "timeout_sec": {"type": "integer", "description": "可选：最长等待秒数，默认 300"},
        }, "required": ["operation", "image"]}}},
]


# 工具的英文说明覆盖表：界面语言为英文时，用它替换 TOOL_SCHEMAS 里的
# description 与参数说明（结构、参数名、required 一律不变，只换文字）。
# 未列出的键沿用中文原值。
TOOL_DESC_EN = {
    "hdcmate": {
        "desc": ("Remote-control a paired Huawei phone (HarmonyOS, over the HDC protocol, "
                 "not adb). The full skill doc is ~/.termux-agent/hdc_skill.md (protocol "
                 "notes, common HarmonyOS commands, UI automation, pitfalls) — read it "
                 "first when you need details. action=exec runs a shell command on the "
                 "phone (the command goes in value); action=target sets and remembers the "
                 "phone address (host + port, once only); action=test checks the connection. "
                 "On first use you must run action=target to set the address; get it from "
                 "the phone's Settings → System & updates → Developer options → Wireless "
                 "debugging under “IP address and port”. Note: the port changes after the "
                 "phone reboots or the router changes, so re-target then. Permissions match "
                 "adb shell (uid 2000): no reading app-private data, no root. Common "
                 "commands: bm dump -a -l lists apps; aa start -b <bundle> -a EntryAbility "
                 "launches an app; uitest dumpLayout -p /data/local/tmp/lay.json exports the "
                 "UI layout; uitest uiInput click X Y taps coordinates (full UI automation)."),
        "props": {
            "action": "exec / target / test, defaults to exec",
            "value": "the shell command to run when action=exec",
            "host": "phone IP, only for action=target",
            "port": "wireless debugging port, only for action=target",
            "timeout": "timeout in seconds, default 40",
        }},
    "wx_auto": {
        "desc": ("Automated WeChat companion: read WeChat messages / auto-reply to a "
                 "chosen contact (the user authorised this, and the other party knows). action=read "
                 "only reads the screen (multimodal vision) and never sends; once runs a "
                 "check-up: three gates whose verdicts are shown to a human; run starts "
                 "auto-reply in the background (replies only to new incoming messages, and "
                 "never twice to the same one); stop stops it; status shows state and logs. "
                 "Reading the screen relies on a multimodal model looking at screenshots, so "
                 "the WeChat chat window must be visible on screen. Safety limits: uncertain "
                 "recognition or system words are only logged, a missing send button means "
                 "give up, and the cap is 20 messages per hour."),
        "props": {"action": "read / once / run / stop / status"}},
    "bash": {
        "desc": ("Run a shell command on this device and return the output. This is the "
                 "workhorse: use it to inspect system state, run programs, install pkg "
                 "packages, manage files and so on. Prefer it for anything terminal-like."),
        "props": {
            "command": "the full command to run",
            "timeout_sec": "timeout in seconds, default 120, max 900",
            "background": ("true = run long in the background: returns the pid and log path "
                           "immediately without blocking. Use it for big installs, long "
                           "downloads, batch processing — then poll the log with "
                           "read_file/grep. Default false (wait for the command to finish)."),
        }},
    "read_file": {
        "desc": ("Read a text file with line numbers. For large files page through it with "
                 "offset/limit; a negative offset counts back from the end (e.g. -50 = last "
                 "50 lines), handy for the tail of a log."),
        "props": {
            "path": "file path; a relative path is resolved against the working directory",
            "offset": "first line number, 1-based, default 1; negative counts from the end",
            "limit": "how many lines to read, default 800, max 2000 per call",
        }},
    "write_file": {
        "desc": ("Write a file (overwrites the whole file, creates it if missing, and "
                 "creates parent directories automatically). To change an existing file, "
                 "prefer edit_file."),
        "props": {"content": "the complete file content"},
    },
    "apply_patch": {
        "desc": ("Apply a patch touching several places/files at once (unified-diff style). "
                 "Multiple files take effect atomically: everything must match or nothing is "
                 "written. Prefer it for multi-site edits and cross-file changes — fewer "
                 "round-trips and safer than repeated edit_file. Format:\n"
                 "*** Update File: path\n@@\n context line\n-line to remove\n+line to add\n"
                 "(For a new file use *** Add File: path, to delete use *** Delete File: "
                 "path; the standard --- a/x +++ b/x form also works.)"),
        "props": {"patch": "the full patch text (may contain several files and @@ hunks)"},
    },
    "edit_file": {
        "desc": ("Do an exact string replacement in a file. old_string must match the "
                 "original exactly (including indentation) and must be unique by default. "
                 "The preferred tool for editing an existing file."),
        "props": {
            "path": "file path",
            "old_string": "the snippet to replace; include enough context to make it unique",
            "new_string": "the replacement; an empty string deletes the snippet",
            "replace_all": "replace every match, default false",
        }},
    "list_dir": {
        "desc": ("List a directory with type, size and modification time. Use it to "
                 "understand project structure and locate files."),
        "props": {
            "path": "directory path, defaults to the working directory",
            "pattern": "optional regex filtering by file name",
        }},
    "grep": {
        "desc": ("Recursively search text content in a directory (regex supported), "
                 "returning file:line: content. Use it to find code; don't shell out to grep."),
        "props": {
            "pattern": "regular expression",
            "path": "root path to search, defaults to the working directory",
            "include": "only search files whose name matches this regex, e.g. \\.py$",
            "max_results": "max number of matches, default 200",
        }},
    "fetch_url": {
        "desc": ("Fetch a web page or API response and convert it to plain text, for "
                 "research, docs and API output. Text only — don't use it to download large "
                 "files."),
        "props": {
            "url": "the full URL",
            "max_chars": "max characters to return, default 20000",
        }},
    "download": {
        "desc": ("Stream a file to local disk (no truncation, with timeouts), for large "
                 "files such as APKs, installers and images. Use it for app installers like "
                 "WeChat — not fetch_url or curl."),
        "props": {
            "url": "download URL",
            "path": "save path, e.g. ~/storage/shared/Download/wechat.apk",
            "timeout_sec": "timeout in seconds, default 600",
        }},
    "web_search": {
        "desc": ("Web search (no key needed). Use it when you need current information, to "
                 "look things up or verify facts, instead of answering from memory."),
        "props": {
            "query": "search keywords",
            "max_results": "max results, default 8, max 15",
        }},
    "github": {
        "desc": ("Operate GitHub repos (read/write your own repos; a token must be "
                 "configured). clone/pull brings a repo down locally, push commits and "
                 "pushes after you edit. action: list repos / repo details / read a file / "
                 "tree a directory / clone / pull / push commit+push / create / search code."),
        "props": {
            "action": "list/repo/read/tree/clone/pull/push/create/search",
            "repo": "owner/name; for create just the repo name",
            "path": "file path relative to the repo, for read/tree/push",
            "ref": "branch or commit, defaults to the repo's default branch",
            "content": "file content to write on push (with path)",
            "files": "batch write on push: {relative path: content}",
            "message": "commit message / repo description",
            "dir": "local directory, default ~/github/<repo name>",
            "branch": "target branch to push to; empty uses the repo default",
            "query": "search term for search",
            "private": "whether create makes it private, default true",
        }},
    "todo_write": {
        "desc": ("Maintain the task list: break a 3+ step task into 2–6 steps and put them "
                 "here; the moment one finishes, call it again with that step done=true "
                 "(don't save it up). The list shows live above the user's input box with "
                 "openable progress; after an interruption or reconnect, continue from the "
                 "unchecked steps."),
        "props": {"items": "the task list"},
    },
    "subagent": {
        "desc": ("Hand an independent sub-problem to a focused “sub-agent” to think about "
                 "on its own (it has no access to this machine's files) and return its "
                 "conclusion. Good for splitting up complex tasks and pushing several hard "
                 "parts forward in parallel."),
        "props": {
            "prompt": "the task for the sub-agent; state the goal and the expected output",
            "max_tokens": "output cap, default 2000",
        }},
    "sysshell": {
        "desc": ("Run a command as the shell (adb) user, which outranks a normal app: can "
                 "read system settings, dumpsys, getprop, pm/am and change global settings. "
                 "When plain bash hits a permission denial, use this instead. Not root."),
        "props": {
            "command": "the command to run",
            "timeout_sec": "timeout in seconds, default 60",
        }},
    "selfupdate": {
        "desc": ("Modify your own source code (~/.termux-agent/agent.py) to fix your own "
                 "bugs or add features. It auto-backs up and syntax-checks, restoring "
                 "immediately on failure; on success the service restarts with the new code, "
                 "and the watchdog rolls back if it won't start. Note: read the source with "
                 "read_file before calling, and keep the change small and precise; the turn "
                 "ends after this call."),
        "props": {
            "summary": "one line describing what you changed",
            "new_code": ("the entire new source (either this or patch_old; the file is huge, "
                         "so prefer the patch form)"),
            "patch_old": "the original snippet to replace (must match the source exactly and be unique)",
            "patch_new": "the replacement content",
        }},
    "ask_user": {
        "desc": ("Ask the user a question and let them pick from your options (a choice "
                 "panel pops up and waits, several questions at once allowed). Use it only "
                 "when a decision must be made by the user: spending money, deleting or "
                 "overwriting data, installing/removing apps, choosing between approaches, "
                 "or information you can't obtain (passwords, preferences, real intent). "
                 "Small things you can find in files/logs/the web or settle by common sense "
                 "(a name, which step first, whether to retry) — just do them and note your "
                 "choice in the report."),
        "props": {
            "questions": "1–6 questions",
            "timeout_sec": "how long before it times out, default 900 seconds",
        }},
    "wps": {
        "desc": ("Create/edit WPS (Word/Excel/PPT) documents via a local MCP service, no "
                 "account login needed. action=list first shows the available tools "
                 "(default); with action=call, use tool to name the tool and args to pass "
                 "JSON-string arguments, e.g. tool=create_spreadsheet "
                 "args={\"filename\":\"table.xlsx\"}. Files land in ~/storage/shared/WPS_AI/; "
                 "open them with WPS on the phone."),
        "props": {
            "action": "list (default, list tools) / call (invoke) / ping",
            "tool": "tool name for action=call, e.g. create_document",
            "args": "arguments for action=call, as a JSON string",
        }},
    "imgedit": {
        "desc": ("Run AI processing on an image, five operations:\n"
                 "erase=remove text/watermarks; restore=repair old photos; enhance=improve "
                 "quality; beauty=portrait retouching; matting=cut out the background.\n"
                 "image takes a local path or an http(s) URL (local images under 4MB).\n"
                 "Processed asynchronously, usually in about ten seconds; the result is "
                 "downloaded locally and its path returned.\n"
                 "Note: this consumes account credit (about 0.6 per image) — don't spam it."),
        "props": {
            "operation": "erase / restore / enhance / beauty / matting",
            "image": "local image path or http(s) image URL",
            "prompt": "optional: extra instructions, supported by some operations",
            "out": "optional: output path or directory, default ~/.termux-agent/outputs/",
            "timeout_sec": "optional: max wait in seconds, default 300",
        }},
}


def localize_tools(tools: list, lang: str | None = None) -> list:
    """按界面语言本地化工具定义：只替换 description 与参数说明文字，
    工具名、参数名、类型、required 一律不动。英文以外的语言原样返回。"""
    lc = str(lang or globals().get("UI_LANG") or "zh").lower()
    if not lc.startswith("en"):
        return tools
    out = []
    for t in tools:
        fn = t.get("function") or {}
        name = fn.get("name") or ""
        ov = TOOL_DESC_EN.get(name)
        if not ov:
            out.append(t)
            continue
        nf = dict(fn)
        if ov.get("desc"):
            nf["description"] = ov["desc"]
        props = ov.get("props") or {}
        if props:
            pa = dict(nf.get("parameters") or {})
            pr = dict(pa.get("properties") or {})
            for pk, pv in pr.items():
                if pk in props:
                    pr[pk] = dict(pv)
                    pr[pk]["description"] = props[pk]
            pa["properties"] = pr
            nf["parameters"] = pa
        nt = dict(t)
        nt["function"] = nf
        out.append(nt)
    return out


# 工具的中文名与摘要 —— 界面只给人看这些，不暴露原始命令

TOOL_LABELS = {
    "bash": "执行命令",
    "read_file": "查看文件",
    "write_file": "写入文件",
    "edit_file": "修改文件",
    "apply_patch": "应用补丁",
    "list_dir": "浏览目录",
    "grep": "搜索内容",
    "fetch_url": "读取网页",
    "download": "下载文件",
    "web_search": "联网搜索",
    "github": "GitHub",
    "todo_write": "任务清单",
    "ask_user": "等你拍板",
    "subagent": "子代理",
    "sysshell": "系统命令",
    "selfupdate": "更新自身",
    "wx_auto": "微信陪聊",
    "hdcmate": "操控手机",
    "wps": "WPS 文档",
}


def _tool_params(name: str) -> tuple:
    """取某个工具参数表里的 (properties, required)。"""
    for s in TOOL_SCHEMAS:
        f = s.get("function") or {}
        if f.get("name") == name:
            pa = f.get("parameters") or {}
            return (pa.get("properties") or {}), (pa.get("required") or [])
    return {}, []


# 参数报错时给的「照抄示例」用值：按参数名填一个真实可用的样子，
# 模型只要把值改掉就能直接重发，不用再对着参数表猜。
_EXAMPLE_VALUES = {
    "path": "/data/data/com.termux/files/home/notes.txt",
    "command": "ls -l ~/storage/shared/Download",
    "pattern": "def main",
    "include": r"\.py$",
    "content": "文件内容",
    "old_string": "要被替换的原片段",
    "new_string": "替换后的内容",
    "patch": "*** Update File: a.txt\n@@\n-旧行\n+新行",
    "url": "https://example.com",
    "query": "关键词",
    "action": "status",
    "prompt": "要单独想清楚的子问题",
    "summary": "一句话说明改了什么",
    "patch_old": "原代码片段",
    "patch_new": "新代码片段",
    "new_code": "",
    "items": [{"text": "第一步", "done": False}],
    "questions": [{"question": "选哪个？", "options": ["选项A", "选项B"]}],
    "text": "任务描述",
    "done": False,
    "question": "选哪个？",
    "options": ["选项A", "选项B"],
    "multi": False,
    "allow_text": False,
    "offset": 1,
    "limit": 200,
    "max_results": 8,
    "max_chars": 20000,
    "max_tokens": 2000,
    "timeout_sec": 120,
    "background": False,
    "replace_all": False,
}


def _schema_example(name: str) -> str:
    """该工具「最小可用调用」的照抄示例：必填参数全填好值，改一下就能重发。"""
    props, req = _tool_params(name)
    if not props:
        return ""
    keys = [k for k in props if k in req] or list(props)[:1]
    obj = {}
    for k in keys:
        sch = props.get(k) or {}
        choices = sch.get("enum") or []
        if choices:
            obj[k] = choices[0]
        elif k in _EXAMPLE_VALUES:
            obj[k] = _EXAMPLE_VALUES[k]
        else:
            obj[k] = {"string": "…", "integer": 0, "number": 0,
                      "boolean": False, "array": [], "object": {}}.get(sch.get("type"), "…")
    try:
        return f" 照抄示例：{json.dumps(obj, ensure_ascii=False)}"
    except Exception:
        return ""


def _schema_hint(name: str) -> str:
    """该工具的参数清单（标注必填/可选）+ 可照抄的调用示例，供报错时附上。"""
    props, req = _tool_params(name)
    if not props:
        return ""
    return " 该工具参数：" + "、".join(
        k + ("（必填）" if k in req else "（可选）") for k in props) + _schema_example(name)


def _coerce_args(name: str, args) -> tuple:
    """按工具参数表，把模型给的字面量纠正成正确类型。

    模型偶尔把整数写成字符串 "120"、把布尔写成 "false"。字符串在 Python 里
    恒为「真」，会让 replace_all 意外变成「全部替换」，所以这里统一纠正；
    纠正不了就返回一句能照着改的说明，而不是抛出难以理解的 ValueError。
    返回 (参数, 错误信息)，错误信息非空表示参数不可用。
    """
    props, _req = _tool_params(name)
    if not isinstance(args, dict):
        return {}, f"参数必须是一个 JSON 对象，收到的是 {type(args).__name__}"
    out = {}
    for k, v in args.items():
        want = (props.get(k) or {}).get("type")
        if isinstance(v, bool):
            out[k] = int(v) if want == "integer" else (str(v).lower() if want == "string" else v)
            continue
        if isinstance(v, str) and want in ("integer", "number"):
            s = v.strip()
            try:
                out[k] = int(s) if want == "integer" else float(s)
            except ValueError:
                return {}, (f"参数 {k} 需要{'整数' if want == 'integer' else '数字'}，"
                            f"收到的是字符串 {v!r}")
        elif want == "boolean" and isinstance(v, str):
            low = v.strip().lower()
            if low in ("true", "1", "yes", "on"):
                out[k] = True
            elif low in ("false", "0", "no", "off", ""):
                out[k] = False
            else:
                return {}, f"参数 {k} 需要布尔值 true 或 false，收到的是 {v!r}"
        elif want in ("array", "object") and isinstance(v, str):
            try:
                nv = json.loads(v)
            except Exception:
                nv = None
            if isinstance(nv, (list if want == "array" else dict)):
                out[k] = nv
            else:
                return {}, (f"参数 {k} 需要 JSON {'数组' if want == 'array' else '对象'}，"
                            f"收到的是字符串 {v[:60]!r}")
        elif want == "string" and isinstance(v, (int, float)):
            out[k] = str(v)
        else:
            out[k] = v
    return out, ""


# ---- 非标准工具调用兜底 ------------------------------------------------
# 模型偶尔不走标准 tool_calls 字段，而是把调用写成 XML 风格的文本，
# 还夹带 ｜｜DSML｜｜ 这类模板噪声。这里兜住，别让整轮调用白白丢掉。

_DSML_NOISE = re.compile(r"[｜|]{0,2}\s*DSML\s*[｜|]{0,2}", re.I)
_XML_INVOKE = re.compile(
    r"<\s*[^<>]*?invoke\s+name\s*=\s*[\"']([^\"']+)[\"'][^<>]*>(.*?)"
    r"<\s*[^<>]*?/\s*[^<>]*?invoke[^<>]*>", re.S | re.I)
_XML_PARAM = re.compile(
    r"<\s*[^<>]*?parameter\s+name\s*=\s*[\"']([^\"']+)[\"'][^<>]*>(.*?)"
    r"<\s*[^<>]*?/\s*[^<>]*?parameter[^<>]*>", re.S | re.I)


def strip_dsml(text: str) -> str:
    return _DSML_NOISE.sub("", text or "")


def parse_xml_tool_calls(text: str) -> list:
    """把 XML 风格的工具调用文本，解析成与标准格式一致的 calls。"""
    t = strip_dsml(text)
    out = []
    for m in _XML_INVOKE.finditer(t):
        name = (m.group(1) or "").strip()
        if not name:
            continue
        args = {}
        for pm in _XML_PARAM.finditer(m.group(2) or ""):
            k = (pm.group(1) or "").strip()
            v = (pm.group(2) or "").strip()
            if not k:
                continue
            try:
                args[k] = json.loads(v)
            except Exception:
                args[k] = v
        if not args:
            body = (m.group(2) or "").strip()
            if body[:1] in ("{", "["):
                try:
                    args = json.loads(body)
                except Exception:
                    args = {}
        out.append({"name": name, "arguments": json.dumps(args, ensure_ascii=False)})
    return out


def looks_like_broken_call(text: str) -> bool:
    """看着像工具调用、却没被解析出来 → 判定为「格式坏了」，该重试。"""
    t = text or ""
    if not t or len(t) > 4000:
        return False
    marks = ("DSML", "<invoke", "invoke name", "<parameter", "parameter name",
             "tool_calls", "<tool_call", "antml:")
    return any(k in t for k in marks)


# ---- 工具禁用名单（控制台可多选禁用：只是不再发给模型，函数本身还在，随时可恢复）----
DISABLED_TOOLS_PATH = APP_DIR / "disabled_tools.json"


def load_disabled_tools() -> list:
    """读被禁用的工具名列表。"""
    try:
        d = json.loads(DISABLED_TOOLS_PATH.read_text(encoding="utf-8"))
        if isinstance(d, list):
            return [str(x) for x in d]
    except Exception:
        pass
    return []


def save_disabled_tools(names) -> list:
    """写禁用名单：去重，且只保留真实存在的工具名（防止写进不存在的东西）。"""
    valid = set()
    try:
        for s in TOOL_SCHEMAS:
            valid.add((s.get("function") or {}).get("name") or "")
    except Exception:
        pass
    clean = sorted({str(n) for n in (names or []) if str(n) in valid})
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        DISABLED_TOOLS_PATH.write_text(json.dumps(clean, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return clean


def delete_skill(name: str) -> str:
    """删掉一个技能文档。删前先备份进 skills/.trash/，删错了还能捞回来。返回备份文件名。"""
    stem = Path(str(name or "")).name
    if stem.endswith(".md"):
        stem = stem[:-3]
    if not stem or "/" in stem or "\\" in stem or stem.startswith("."):
        raise RuntimeError("非法的技能名")
    src = Path(__file__).resolve().parent / "skills" / (stem + ".md")
    if not src.exists():
        raise RuntimeError("没有这个技能：" + stem)
    trash = src.parent / ".trash"
    trash.mkdir(parents=True, exist_ok=True)
    dst = trash / (stem + "-" + datetime.now().strftime("%m%d-%H%M%S") + ".md")
    shutil.copy2(src, dst)
    src.unlink()
    try:                      # 说明和技能同生共死
        _explain_path(stem).unlink()
    except Exception:
        pass
    return dst.name


def build_tools(cfg: dict):
    """根据配置返回当前启用的工具列表。

    过滤条件：增强权限关闭时不给 sysshell；控制台里被禁用的工具也不发 ——
    既省 token，也避免模型误用。禁用只是"不发"，工具函数本身还在，可随时恢复。
    """
    if local_lite(cfg):
        return []          # 本地模型：工具定义占 2858 tokens，直接不发（省 2 分钟）
    off = set(load_disabled_tools())
    out = []
    for t in TOOL_SCHEMAS:
        nm = t["function"]["name"]
        if nm == "sysshell" and not cfg.get("shell_access"):
            continue
        if nm in off:
            continue
        out.append(t)
    return localize_tools(out)


def _tool_detail(name: str, args: dict) -> str:
    """给界面用的简短说明：一眼看出这一步在动什么（命令、路径、关键词…）。"""
    a = args or {}
    if name in ("bash", "sysshell"):
        cmd = " ".join(str(a.get("command", "")).split())
        if a.get("background"):
            cmd = "[后台] " + cmd
        return (cmd[:72] + "…") if len(cmd) > 72 else cmd
    if name == "apply_patch":
        try:
            items, err = _parse_patch(str(a.get("patch", "")))
        except Exception:
            items, err = [], "1"
        if err or not items:
            return "补丁"
        names = []
        for it in items:
            nm = it["path"].rstrip("/").split("/")[-1]
            names.append(("＋" if it["op"] == "add" else "－" if it["op"] == "delete" else "")
                         + nm)
        head = "、".join(names[:3])
        if len(names) > 3:
            head += f" 等 {len(names)} 个文件"
        return head
    if name == "todo_write":
        items = a.get("items") or []
        try:
            done = sum(1 for it in items if isinstance(it, dict) and it.get("done"))
        except Exception:
            done = 0
        return f"{done}/{len(items)} 已完成" if items else ""
    if name == "read_file":
        return str(a.get("path", ""))
    if name in ("write_file", "edit_file", "apply_patch"):
        return str(a.get("path", ""))
    if name == "list_dir":
        return str(a.get("path", "."))
    if name == "grep":
        return str(a.get("pattern", ""))
    if name == "fetch_url":
        return str(a.get("url", ""))
    if name == "download":
        return str(a.get("path", ""))
    if name == "web_search":
        return str(a.get("query", ""))
    if name == "github":
        return ("%s %s" % (a.get("action", ""), a.get("repo", ""))).strip()
    if name == "subagent":
        return str(a.get("prompt", ""))[:40]
    if name == "selfupdate":
        return str(a.get("summary", ""))[:40]
    if name == "wps":
        return ("%s %s" % (a.get("tool", ""), a.get("args", ""))).strip()[:60]
    return ""


SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".cache",
             ".npm", ".gradle", ".cargo", "site-packages"}


# 共享存储里最常用的几个目录（走一遍就好，不再深挖，避免卡住）
EXTRA_REF_DIRS = ["storage/shared/Download", "storage/shared/Documents",
                  "storage/shared/Pictures", "storage/shared/DCIM"]
_FILES_REF_CACHE = {"key": None, "t": 0.0, "list": []}


def files_for_ref(root: str, limit: int = 900, max_depth: int = 3, ttl: float = 8.0) -> list:
    """给界面 @ 引用补全用的文件清单（相对路径，跳过杂项目录和大目录）。"""
    base = Path(root or Path.home())
    if not base.is_dir():
        return []
    ck = (str(base), limit, max_depth)
    now = time.time()
    if _FILES_REF_CACHE["key"] == ck and now - _FILES_REF_CACHE["t"] < ttl:
        return _FILES_REF_CACHE["list"]
    out = []
    try:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            rel = Path(dirpath).relative_to(base)
            if len(rel.parts) >= max_depth:
                dirnames[:] = []
            for fn in sorted(filenames):
                if fn.startswith("."):
                    continue
                r = (rel / fn) if str(rel) != "." else Path(fn)
                out.append(str(r))
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
        if len(out) < limit:                     # 顺手带上共享存储的常用目录（只列一层）
            for rel_dir in EXTRA_REF_DIRS:
                d = base / rel_dir
                if not d.is_dir():
                    continue
                try:
                    names = sorted(os.listdir(d))
                except OSError:
                    continue
                for fn in names:
                    if fn.startswith("."):
                        continue
                    if (d / fn).is_file():
                        out.append(rel_dir + "/" + fn)
                        if len(out) >= limit:
                            break
                if len(out) >= limit:
                    break
    except OSError:
        pass
    _FILES_REF_CACHE.update({"key": ck, "t": now, "list": out})
    return out


def _tool_args_short(name: str, args: dict, limit: int = 4000) -> dict:
    """给界面展示用的工具参数：长文本截断，避免把整份文件塞进前端。"""
    if not isinstance(args, dict):
        return {}
    out = {}
    for k, v in args.items():
        if isinstance(v, str):
            if len(v) > limit:
                v = v[:limit] + f"\n…（已截断，原 {len(v)} 字）"
        elif not (isinstance(v, (int, float, bool)) or v is None):
            try:
                v = json.dumps(v, ensure_ascii=False)
            except (TypeError, ValueError):
                v = str(v)
            if len(v) > limit:
                v = v[:limit] + "…"
        out[k] = v
    return out


def _tool_summary(name: str, result: str) -> str:
    """把工具输出压成一句人话。"""
    r = str(result or "")
    if r.startswith("[未执行]"):
        return "已拦截"
    if r.startswith("[错误]"):
        return "出错"
    if name in ("bash", "sysshell"):
        m = re.match(r"\[(?:shell 身份 · )?退出码 (\d+)", r)
        if m:
            return "完成" if m.group(1) == "0" else f"退出码 {m.group(1)}"
        return "完成"
    if name == "read_file":
        m = re.search(r"文件共 (\d+) 行", r)
        return f"{m.group(1)} 行" if m else "已读取"
    if name in ("write_file", "edit_file"):
        return "已保存"
    if name == "list_dir":
        m = re.search(r"（(\d+) 项", r)
        return f"{m.group(1)} 项" if m else "已列出"
    if name == "grep":
        m = re.search(r"命中 (\d+) 条", r)
        return f"{m.group(1)} 处" if m else "未找到"
    if name == "fetch_url":
        return "已获取"
    if name == "download":
        return "下载完成"
    if name == "web_search":
        return "已搜索"
    if name == "todo_write":
        return "已更新"
    if name == "subagent":
        return "已完成"
    if name == "selfupdate":
        return "已更新代码" if "成功" in r else "已还原"
    return "完成"


# ---------------------------------------------------------------- 权限确认

class Approver:
    """命令闸门。

    默认**全部放行**——设备是用户自己的，每条命令都问一遍没有任何意义。

    只保留一道"安全带"：极少数不可逆的毁灭性操作（格盘、往块设备写、删根目录）
    仍然会被拦下并告知模型。正常使用中它永远不会触发。
    """

    # 只匹配真正会毁掉整台设备/全部数据的写法
    CATASTROPHIC = re.compile(
        r"("
        r"\brm\s+-[A-Za-z]*[rRfF][A-Za-z]*\s+(?:-\S+\s+)*/(?:\s|$|\*)"      # rm -rf /
        r"|\brm\s+-[A-Za-z]*[rRfF][A-Za-z]*\s+(?:-\S+\s+)*(?:~|\$HOME)(?:\s|$|/\s*$|\*)"  # rm -rf ~
        r"|\brm\s+-[A-Za-z]*[rRfF][A-Za-z]*\s+(?:-\S+\s+)*/(?:sdcard|storage|data|system|sdcard/)(?:\s|$|\*|/)"
        r"|\bmkfs(?:\.[a-z0-9]+)?\b"
        r"|\bdd\b[^|;]*\bof=/dev/(?:block|mmcblk|sd[a-z]|nvme)"
        r"|\b(?:fdisk|parted|sgdisk)\b[^|;]*/dev/(?:block|mmcblk|sd[a-z])"
        r"|>\s*/dev/(?:block|mmcblk|sd[a-z])"
        r")"
    )

    def __init__(self, cfg: dict, assume_yes: bool = False, interactive: bool = True):
        self.auto = True if cfg.get("auto_approve", True) else bool(assume_yes)
        self.session_allow = set()
        self.interactive = interactive

    @staticmethod
    def _segments(cmd: str):
        out = []
        for seg in re.split(r"(?:\|\||&&|;|\||\n)", cmd):
            words = seg.strip().split()
            while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
                words = words[1:]
            if words and words[0] in ("sudo", "doas", "env", "command", "nohup"):
                words = words[1:]
            if words:
                out.append((words[0].split("/")[-1], words))
        return out

    @classmethod
    def is_catastrophic(cls, cmd: str) -> bool:
        return bool(cls.CATASTROPHIC.search(cmd))

    # 兼容旧名字
    @classmethod
    def is_risky(cls, cmd: str) -> bool:
        return cls.is_catastrophic(cmd)

    def check(self, name: str, args: dict) -> tuple[bool, str]:
        if name != "bash":
            return True, ""
        cmd = (args.get("command") or "").strip()
        if not cmd:
            return True, ""
        if self.is_catastrophic(cmd):
            return False, ("该命令是不可逆的毁灭性操作（格式化 / 直写块设备 / 删根目录），已自动拦截。"
                           "如果用户确实要求这样做，请先向用户说明风险并让用户手动执行。")
        return True, ""


# ---------------------------------------------------------------- 上下文管理

def estimate_chars(messages: list) -> int:
    total = 0
    for m in messages:
        total += len(str(m.get("content") or ""))
        for tc in m.get("tool_calls") or []:
            total += len(str((tc.get("function") or {}).get("arguments") or ""))
    return total


def _sanitize_messages(messages: list) -> list:
    """保证消息列表满足工具调用协议。

    会话可能在工具循环中途被中断/裁剪/迁移，产生不完整序列（例如一个 assistant
    发了 2 个 tool_calls 却只存下 1 个 tool 结果），导致 API 报 400。
    分两步修干净：
      1) 丢弃"孤儿" tool 消息（前面既不是 assistant 的 tool_calls，也不是另一个 tool）；
      2) 校验每个 assistant 的 tool_calls 是否被足够的、id 匹配的 tool 消息满足，
         不足则剥掉它的 tool_calls 并丢弃这些不完整的 tool 消息。
    """
    cleaned = []
    for m in messages:
        if m.get("role") == "tool":
            if not cleaned:
                continue
            prev = cleaned[-1]
            if prev.get("role") == "tool":
                pass
            elif prev.get("role") == "assistant" and prev.get("tool_calls"):
                pass
            else:
                continue
        cleaned.append(m)

    result = []
    i, n = 0, len(cleaned)
    while i < n:
        m = cleaned[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            tcs = m.get("tool_calls") or []
            ids = [tc.get("id") for tc in tcs if isinstance(tc, dict)]
            # 思考模式要求回传 reasoning_content，老会话里没有这个字段 → 补空串
            if "reasoning_content" not in m:
                m["reasoning_content"] = ""
            j = i + 1
            tool_msgs = []
            while j < n and cleaned[j].get("role") == "tool":
                tool_msgs.append(cleaned[j])
                j += 1
            if len(tool_msgs) > len(tcs):        # 多出来的 tool 结果没人认领
                tool_msgs = tool_msgs[:len(tcs)]
            # 修复历史遗留：一轮里多个工具调用曾共用同一个 id，按顺序重新挂回正确的 id
            for k, tm in enumerate(tool_msgs):
                if k < len(ids) and ids[k]:
                    tm["tool_call_id"] = ids[k]
            have_ids = {tm.get("tool_call_id") for tm in tool_msgs}
            complete = len(tool_msgs) >= len(tcs) and all(
                (tid is None or tid in have_ids) for tid in ids)
            if complete:
                result.append(m)
                result.extend(tool_msgs)
            else:
                nm = dict(m); nm.pop("tool_calls", None); result.append(nm)
            i = j
        else:
            result.append(m)
            i += 1
    return result


def prune_context(messages: list, limit: int = PRUNE_AT_CHARS) -> list:
    """超限时把老的工具输出压成一句摘要，保留对话骨架与最近若干条。

    limit 由调用方按本对话的压缩阈值给出（默认阈值的 1.2 倍），
    阈值调大时兜底裁剪也跟着放宽。

    2026-10-01：原先直接把内容换成「[早期工具输出已裁剪以释放上下文]」，
    等于把「当时查到了什么」整段抹掉，模型只能重新查一遍（或干脆猜）。
    现在留 200 字开头 + 落盘路径 —— 超长输出本来就整份存在 tmp/ 下，
    花 1% 的体积保住线索。
    """
    if estimate_chars(messages) <= limit:
        return messages
    keep_from = max(1, len(messages) - KEEP_RECENT_MESSAGES)
    pruned = 0
    for i in range(1, keep_from):
        m = messages[i]
        if m.get("role") == "tool" and len(str(m.get("content") or "")) > 400:
            c = str(m["content"])
            hit = re.search(r"\[完整输出已存到\]\s*(\S+)", c)
            head = c[:200].replace("\n", " ").strip()
            m["content"] = ("[早期工具输出已裁剪，留开头备查] " + head
                            + ("（完整输出仍在：%s）" % hit.group(1) if hit
                               else "（原文共 %d 字符）" % len(c)))
            pruned += 1
    if pruned:
        print(dim(f"  · 已压缩 {pruned} 条早期工具输出以释放上下文"))
    return messages


# 压缩：分块 + 合并（2026-10-01）
# 原先一次调用、只要 200 字，而且素材只取开头 16000 字符 —— 长任务里中段发生过
# 什么等于全丢（既没进摘要，也没留在历史里）。现在按 3 万字符切段逐段摘要，
# 再把各段合并；工具动作也带上，这样「改过哪些文件 / 跑过什么命令 / 结果如何」
# 能留下来。
COMPRESS_CHUNK_CHARS = 30000     # 每段喂给摘要器的大小
COMPRESS_SUMMARY_CHARS = 3000    # 合并后摘要的字符上限

_COMPRESS_SYS = (
    "你是对话压缩器。把下面这段对话浓缩成要点，务必保留：\n"
    "① 用户的目标与明确要求；② 已经做完的事及其结论（改过哪些文件、跑过什么命令、结果如何）；"
    "③ 关键标识符 —— 文件名、路径、命令、参数、报错原文；④ 用户偏好与约束；⑤ 尚未解决的问题。\n"
    "按「已做 / 结论 / 待办」分段写。宁可具体，不要笼统；不要丢掉标识符。用简洁中文。")

_COMPRESS_MERGE_SYS = (
    "你是对话压缩器。下面是同一段长对话按时间顺序切段后的各段摘要，请合并成一份连贯摘要："
    "按「已做 / 结论 / 待办」分段，去掉重复，保留所有关键标识符"
    "（文件名、路径、命令、参数、报错原文）与用户偏好。用简洁中文，不超过 %d 字。"
    % COMPRESS_SUMMARY_CHARS)


def compress_transcript(cfg: dict, old_msgs: list) -> str:
    """把一段历史压成摘要（分块逐段摘要 → 合并）。失败返回空串。"""
    text = _format_transcript(old_msgs, cap=10 ** 9, with_tools=True)
    if len(text.strip()) < 200:
        return ""
    chunks = [text[i:i + COMPRESS_CHUNK_CHARS]
              for i in range(0, len(text), COMPRESS_CHUNK_CHARS)]
    parts = []
    for i, ck in enumerate(chunks):
        try:
            s = _chat_once(cfg, [
                {"role": "system", "content": _COMPRESS_SYS},
                {"role": "user",
                 "content": "（第 %d/%d 段）\n%s" % (i + 1, len(chunks), ck)},
            ], 1200)
        except Exception:
            s = ""
        if s and s.strip():
            parts.append(s.strip())
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0][:COMPRESS_SUMMARY_CHARS]
    try:
        final = _chat_once(cfg, [
            {"role": "system", "content": _COMPRESS_MERGE_SYS},
            {"role": "user", "content": "\n\n".join(parts)[:80000]},
        ], 2000)
    except Exception:
        final = ""
    return (final or "\n\n".join(parts))[:COMPRESS_SUMMARY_CHARS]


def _format_transcript(messages: list, cap: int = 20000, with_tools: bool = False) -> str:
    """把消息列表转成可读的对话文本（供压缩/提炼用），默认忽略 tool 与 system。

    with_tools=True 时把工具调用与结果也带上（各留开头一段）：
    否则压缩出来的摘要记不住「做过什么、结果如何」，等于把排障过程全抹掉。
    """
    lines = []
    total = 0
    for m in messages:
        if total >= cap:
            break
        role = m.get("role")
        if with_tools and role == "assistant":
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                piece = "调用 %s: %s" % (
                    fn.get("name") or "?",
                    str(fn.get("arguments") or "").replace("\n", " ")[:160])
                lines.append(piece)
                total += len(piece)
        text = str(m.get("content") or "").strip()
        if role == "tool":
            if with_tools and text:
                piece = "结果: " + text.replace("\n", " ")[:240]
                lines.append(piece)
                total += len(piece)
            continue
        if role not in ("user", "assistant") or not text:
            continue
        piece = ("用户: " if role == "user" else "助手: ") + text
        lines.append(piece)
        total += len(piece)
    return "\n".join(lines)


def distill_memory(cfg: dict, messages: list) -> str:
    """从对话里提炼长期记忆（追加式），返回新增条目文本；失败返回空串。"""
    transcript = _format_transcript(messages, cap=20000)
    if len(transcript) < 200:
        return ""
    existing = load_memory()
    try:
        new = _chat_once(cfg, [
            {"role": "system", "content": "你是记忆提炼器。从对话中找出值得长期记住的、关于这位用户或"
                                          "其环境的稳定信息（用户偏好、习惯、正在进行的项目、设备/环境细节、"
                                          "重要结论）。只输出新增条目，每条一行、简洁中文；"
                                          "不要重复已有记忆，不要记一次性琐事，没有就输出「无」。"},
            {"role": "user", "content": "已有记忆：\n" + (existing or "（无）") + "\n\n对话：\n" + transcript},
        ], 800)
    except Exception:
        return ""
    if not new or new.strip() in ("无", "无。", "无。"):
        return ""
    # 清洗提炼结果：模型偶尔会把大段思考当输出返回，若被逐行当成记忆存进去，
    # 会把整份长期记忆冲成草稿（2026-09-28 03:36 就发生过一次），这里提前挡掉。
    items = []
    for raw in new.splitlines():
        line = raw.strip().lstrip("-•·* 0123456789.、)）").strip()
        if not line or line.startswith("#"):
            continue
        if not (4 <= len(line) <= 100):
            continue
        if any(k in line for k in ("已有记忆", "用户消息", "对话：", "我们需要",
                                   "让我们", "从这段对话", "要点摘要")):
            continue
        items.append(line)
    return "\n".join(items)


# ---------------------------------------------------------------- Agent 主循环

class CliRenderer:
    """把 Agent 发来的事件画到终端上。"""

    def __init__(self, show_reasoning: bool = True, verbose: bool = False):
        self.show_reasoning = show_reasoning
        self.verbose = verbose
        self.reasoning_open = False
        self.reasoning_chars = 0

    def __call__(self, kind: str, payload) -> None:
        if kind == "reasoning":
            if not self.show_reasoning:
                return
            if not self.reasoning_open:
                print(dim("  ┌ 思考中…"))
                self.reasoning_open = True
            self.reasoning_chars += len(payload or "")
            if self.verbose:
                print(dim(payload), end="", flush=True)
        elif kind == "text":
            if self.reasoning_open:
                if self.verbose:
                    print()
                else:
                    print(dim(f"  └ 思考 {self.reasoning_chars} 字，开始回答\n"))
                self.reasoning_open = False
            sys.stdout.write(payload or "")
            sys.stdout.flush()
        elif kind == "status":
            print(dim(f"  · {payload}"))
        elif kind == "tool":
            p = payload or {}
            print(f"  {blue('>')} {bold(p.get('label') or p.get('name', ''))}"
                  + (f" {dim(str(p.get('detail', ''))[:110])}" if p.get("detail") else "")
                  + f"  {dim('->')} {_one_line(p.get('result', ''))}")
        elif kind == "notice":
            print(yellow(f"  [!] {payload}"))
        elif kind == "error":
            print(red(f"  [错误] {payload}"))
        elif kind == "message_end":
            if self.reasoning_open:
                print(dim(f"  └ 思考 {self.reasoning_chars} 字"))
                self.reasoning_open = False
            print()
            self.reasoning_chars = 0


class Agent:
    def __init__(self, cfg: dict, approver: Approver, show_reasoning: bool = True,
                 verbose: bool = False, emit=None):
        self.cfg = cfg
        self.approver = approver
        self.show_reasoning = show_reasoning
        self.verbose = verbose
        self.emit = emit if emit is not None else CliRenderer(show_reasoning, verbose)
        self.cancel = threading.Event()
        self.session_id = None
        self.turns = 0
        self.compress_ratio = None      # 本对话专有的压缩比例；None = 用配置默认值
        self.stream_text = ""           # 本轮正在生成的正文（还没落进 messages）
        self.messages = [{"role": "system", "content": build_system_prompt(cfg)}]
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    @property
    def compress_at(self) -> int:
        """本对话的自动压缩阈值（字符），随模型窗口与比例实时算出。"""
        return compress_at_for(self.cfg, model=self.cfg.get("model"),
                               ratio=self.compress_ratio if self.compress_ratio
                               else cfg_compress_ratio(self.cfg))

    def _rebuild_system(self) -> None:
        """记忆/配置变化后重建系统提示词（保留在 messages[0]）。"""
        old = self.messages[0] if self.messages else None
        self.messages = ([{"role": "system", "content": build_system_prompt(self.cfg)}]
                         + [m for m in self.messages if m.get("role") != "system"])

    # ---- 会话持久化
    def load_session(self, sid: str = "") -> bool:
        """恢复会话；sid 为空时取最近一个（--continue / 重启自动恢复用）。"""
        if not sid:
            recent = list_sessions()
            if not recent:
                return False
            sid = recent[0]["id"]
        msgs = load_session_messages(sid)
        if not msgs:
            return False
        self.session_id = sid
        self.messages = [{"role": "system", "content": build_system_prompt(self.cfg)}] + \
            [m for m in msgs if m.get("role") != "system"]
        self.messages = _sanitize_messages(self.messages)
        own = load_session_compress_ratio(sid)
        self.compress_ratio = own
        self.emit("status", f"已恢复会话（{len(self.messages) - 1} 条消息，"
                            f"压缩阈值 {self.compress_at} 字 = 窗口的 "
                            f"{round((own or cfg_compress_ratio(self.cfg)) * 100)}%"
                            f"{'，本对话专有' if own else ''}）")
        return True

    def _autosave(self, force: bool = False) -> None:
        """把当前对话写盘（节流到最快每 1.5 秒一次；force 时无条件写）。

        2026-10-01：以前整轮只在结束时存一次，而重启是 pkill(SIGTERM) 打死的、
        finally 跑不到 —— 「做到一半的那一轮」整轮消失。详见 _LIVE_AGENTS 上的注释。
        """
        if self not in _LIVE_AGENTS and len(_LIVE_AGENTS) < 8:
            _LIVE_AGENTS.append(self)      # 供退出信号回调统一冲刷
        now = time.time()
        if not force and now - getattr(self, "_saved_at", 0.0) < _AUTOSAVE_MIN_GAP:
            return
        self._saved_at = now
        try:
            self.save_session()
        except Exception:
            pass

    def save_session(self) -> None:
        if not self.session_id:
            self.session_id = _new_sid()
        save_session_messages(self.session_id, _sanitize_messages(self.messages),
                              compress_ratio=self.compress_ratio,
                              model=self.cfg.get("model") or "",
                              base_url=self.cfg.get("base_url") or "")

    def reset(self) -> None:
        """开新对话：换会话 id，并把上下文整个清空。

        （曾经只换 id 不清 messages，导致"新建对话"里还是旧内容、
          新会话文件里也带着旧历史。）
        """
        self.session_id = _new_sid()
        self.turns = 0
        self.compress_ratio = None    # 新对话继承设置里的默认比例
        self.messages = [{"role": "system", "content": build_system_prompt(self.cfg)}]
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.emit("status", "已开启新对话")

    def set_compress_ratio(self, percent) -> float:
        """设置本对话的压缩比例（百分比，10~95），立即生效并落盘到该会话。"""
        self.compress_ratio = max(0.1, min(0.95, float(percent) / 100.0))
        self.save_session()
        return self.compress_ratio

    # ---- 上下文压缩：把较早的对话交给模型浓缩成摘要
    def _maybe_compress(self, force: bool = False) -> bool:
        """返回是否真的做了压缩。force=True 时忽略阈值（界面上的"立即压缩"）。"""
        if not force and estimate_chars(self.messages) < self.compress_at:
            return False
        keep = KEEP_RECENT_MESSAGES
        if len(self.messages) <= keep + 4:
            if force:
                self.emit("status", "对话还很短，不需要压缩")
            return False
        old = self.messages[1:len(self.messages) - keep]
        if not old:
            return False
        summary = compress_transcript(self.cfg, old)
        if not summary:
            # 摘要没拿到（网络/上游问题）：退回裁剪，别让上下文无限涨
            self.messages = prune_context(
                self.messages, max(PRUNE_AT_CHARS, int(self.compress_at * PRUNE_RATIO)))
            return False
        self.emit("status", "已压缩较早的上下文")
        self.messages = ([self.messages[0],
                          {"role": "user",
                           "content": "[系统自动生成的更早对话摘要]\n" + summary.strip()}]
                         + self.messages[len(self.messages) - keep:])
        return True

    # ---- 单轮：模型 -> 工具 -> 模型 ...
    def _auto_stop_flag(fn):
        """run_turn 执行期间把本轮的停止标志挂到全局，结束自动清空。
        这样深层的 LLM 请求、工具执行都能读到它（点停止立刻生效），
        又不会误伤任务结束后跑的自动进化 / 子代理等请求。"""
        def wrapper(self, user_input, cancel=None):
            CURRENT_STOP[0] = cancel if cancel is not None else self.cancel
            try:
                return fn(self, user_input, cancel)
            finally:
                CURRENT_STOP[0] = None
        return wrapper

    @_auto_stop_flag
    def run_turn(self, user_input: str, cancel=None) -> str:
        ev = cancel if cancel is not None else self.cancel
        try:
            max_rounds = int(self.cfg.get("max_tool_rounds") or MAX_TOOL_ROUNDS)
        except Exception:
            max_rounds = MAX_TOOL_ROUNDS
        max_rounds = max(10, min(1000, max_rounds))
        global TODO_SID
        TODO_SID = self.session_id       # 任务清单按会话存，切对话不串台
        self._rebuild_system()   # 每轮刷新系统提示词（纳入最新记忆与配置）
        self._maybe_compress()
        self.messages = _sanitize_messages(self.messages)
        # 易变内容（时间/记忆/任务清单）挂在对话末尾，而不是系统提示词里 ——
        # 这样系统提示词与已有历史逐字节不变，上游前缀缓存才能命中。
        _ctx, self.ctx_snap = build_turn_context(self.cfg, getattr(self, "ctx_snap", None) or {})
        if _ctx:
            self.messages.append({"role": "system", "content": _ctx})
        self.messages.append({"role": "user", "content": user_input})
        self.turns += 1
        # 用户说过的话立刻落盘：无论后面被怎么打断，至少不会"我说过它不记得"
        self._autosave(force=True)
        final_text = ""
        _task_t0 = time.time()   # 长任务跑完要不要提醒，看它
        broken_retry = 0         # 工具调用格式坏掉时的重试次数（防死循环）
        seen_calls = {}          # (工具, 参数) -> [上次结果, 重复次数]，用来刹住原地打转（整轮有效）
        # 本轮的工具运行时：让命令边跑边把输出推给界面，并支持「停止」真杀掉进程
        global CURRENT_RUNTIME
        CURRENT_RUNTIME = ToolRuntime(self.emit, ev)

        # 前置连通性探测：不通就立刻说清楚，而不是让用户盯着"思考中"等几分钟。
        # （平板被系统省电冻结时也会命中这里 —— 表现为连不上模型服务器。）
        base = self.cfg.get("base_url") or ""
        if not net_reachable(base, timeout=6.0):
            self.emit("error",
                      "连不上模型服务器。可能是：① 网络断了；② 平板被系统省电冻结"
                      "（拔掉电源线后常见）。请检查网络、把平板插上电源，或在华为"
                      "「应用启动管理」里把 Termux 设为手动管理，然后重试。")
            self.emit("message_end", None)
            if notify_pref(self.cfg, "error"):
                notify_async(self.cfg, "连不上模型服务器",
                             "网络断了，或平板被系统省电冻结，本次任务已中止。",
                             tag="net_fail", url=page_url(self.cfg), throttle=30)
            return final_text or "（网络不通，已中止）"

        for step in range(max_rounds):
            # 每步按上下文压力重算工具输出预览上限（越满越省）
            refresh_tool_cap(estimate_chars(self.messages), self.compress_at)
            if ev.is_set():
                self.emit("notice", "已停止")
                break
            # ---- 插队消息：任务进行中用户塞进来的，每轮开始前并入对话（不中断任务）
            try:
                while True:
                    _im = INJECT_QUEUE.get_nowait()
                    self.messages.append({"role": "user", "content": _im})
                    self.emit("notice", "已插队你的消息：%s" % str(_im)[:60])
            except queue.Empty:
                pass
            except Exception:
                pass
            # 轮次多了给个进度提示，避免用户以为卡死（每 10 轮一次）
            if step and step % 10 == 0:
                self.emit("status", f"已执行 {step} 轮工具调用，继续中…")
            try:
                text, calls, truncated, reasoning = self._stream_once(ev)
            except ApiError as e:
                self.emit("error", str(e))
                if notify_pref(self.cfg, "error"):
                    notify_async(self.cfg, "调用模型出错", str(e)[:180],
                                 tag="api_err", url=page_url(self.cfg), throttle=30)
                return final_text
            except KeyboardInterrupt:
                self.emit("notice", "已中断本次生成")
                break

            # ---- 工具调用兜底 ----
            # 模型偶尔不走标准 tool_calls 字段，而是把调用写成 XML 风格文本
            # （有时还夹带 ｜｜DSML｜｜ 之类模板噪声）。不兜住的话，
            # 这一次工具调用就凭空消失了 —— 表现为"命令没发出去"。
            if not calls and text:
                _fc = parse_xml_tool_calls(text)
                if _fc:
                    calls = _fc
                    text = _XML_INVOKE.sub("", strip_dsml(text)).strip()
                    self.emit("notice", "已自动识别模型输出的工具调用（非标准格式）")
                elif looks_like_broken_call(text) and broken_retry < 2:
                    broken_retry += 1
                    self.emit("notice",
                              f"工具调用格式异常，正在让模型重试（{broken_retry}/2）…")
                    if notify_pref(self.cfg, "stuck"):
                        notify_async(self.cfg, "工具调用格式异常",
                                     "模型这回的调用格式不对，正在自动重试（%d/2）。" % broken_retry,
                                     tag="broken", url=page_url(self.cfg), throttle=20)
                    self.messages.append({
                        "role": "user",
                        "content": "（系统提示）你上一条回复里的工具调用格式无法解析。"
                                   "请重新调用一次，只用标准方式：函数名 + JSON 参数，"
                                   "不要输出 XML 标签、尖括号或其它特殊标记。",
                    })
                    continue

            if text:
                final_text += text
            msg = {"role": "assistant", "content": text or ""}
            # 思考模式下，带工具调用的 assistant 消息必须把 reasoning_content 回传给服务端，
            # 否则会报 400「The reasoning_content in the thinking mode must be passed back」。
            if reasoning or calls:
                msg["reasoning_content"] = reasoning or ""
            if calls:
                msg["tool_calls"] = [{
                    "id": c.get("id") or f"call_{i}",
                    "type": "function",
                    "function": {"name": c.get("name"), "arguments": c.get("arguments") or "{}"},
                } for i, c in enumerate(calls)]
            self.messages.append(msg)
            self.stream_text = ""        # 本轮已写进 messages，不再当"生成中"回放
            self._autosave()             # 助手这步（含要调的工具）先落盘

            if not calls:
                # 收尾瞬间如果有插队消息，别把它落下 —— 并入对话再跑一轮。
                # （否则消息会卡在队列里，直到用户下次发消息才被处理，
                #   表现就是"点了插队没反应，重发一条才冒出来"。）
                try:
                    _im = INJECT_QUEUE.get_nowait()
                except queue.Empty:
                    _im = None
                except Exception:
                    _im = None
                if _im is not None:
                    self.messages.append({"role": "user", "content": _im})
                    self.emit("notice", "已插队你的消息：%s" % str(_im)[:60])
                    continue

                # ---- 空回复保护：模型既没给文字也没调工具，不能就这么静默结束。
                # 否则界面上表现为"任务莫名中断/卡住不动"，用户完全不知道发生了什么。
                # 处理：先自动重试一次；还不行就给一条明确说明，别让界面空着。
                if not (text or "").strip() and not final_text.strip():
                    # 空回复不是"任务结束"，是"这轮没拿到有效输出" —— 换着法子多试几次。
                    # 每次换一种说法，比重复同一句提示更容易让模型开口。
                    _er = int(getattr(self, "_empty_retries", 0) or 0)
                    if _er < 3:
                        self._empty_retries = _er + 1
                        _hints = [
                            "（系统提示）你上一条回复是空的。请直接给出回答，或调用工具继续。",
                            "（系统提示）又空了。请至少先输出一句话说明你的判断，然后再动手。",
                            "（系统提示）仍然没有内容。请换一种方式：先复述你要做什么，再逐步执行。",
                        ]
                        self.messages.append({
                            "role": "user",
                            "content": _hints[min(_er, len(_hints) - 1)],
                        })
                        self.emit("notice", "模型空响应，正在第 %d 次重试…" % (_er + 1))
                        continue
                    self._empty_retries = 0
                    _tip = ("（连续 %d 次都没拿到模型输出，可能是服务端异常或连接不稳。"
                            "请再说一次；若反复出现，可考虑换个模型或检查网络。）" % _er)
                    self.emit("chunk", _tip)
                    final_text = _tip
                    try:
                        self.messages.append({"role": "assistant", "content": _tip})
                    except Exception:
                        pass
                else:
                    self._empty_retries = 0

                # ---- 任务清单自动校对 ----
                # 模型准备收尾了，但清单上还留着没打勾的项：让它先核对一遍再结束
                # （做完的打勾、没做的不许假打勾）。同一轮只打扰一次，不会死循环。
                if getattr(self, "_todo_audit_key", None) != (user_input or ""):
                    try:
                        _undone = [x["text"] for x in load_todo(TODO_SID)["items"]
                                   if not x.get("done")]
                    except Exception:
                        _undone = []
                    if _undone:
                        self._todo_audit_key = user_input or ""
                        self.emit("notice", "清单还有 %d 项没打勾，先让模型核对…" % len(_undone))
                        self.messages.append({
                            "role": "user",
                            "content": ("（系统提示）任务看起来要结束了，但任务清单上还有没打勾的项：\n"
                                        + "\n".join("- " + t for t in _undone)
                                        + "\n请先核对清单：确实做完的，用 todo_write 打勾；"
                                          "没做到的，要么现在补做完，要么直接从清单里去掉；"
                                          "不要为了好看而假打勾。核对完再给最终答复。"),
                        })
                        continue

                if truncated == "length":
                    self.emit("notice", "输出达到长度上限被截断。")
                self.emit("message_end", None)
                notify_long_task(self.cfg, _task_t0, final_text)
                return final_text

            # ---- 先把这一轮的工具调用全部解析、过闸门（都在主线程，顺序与用户交互一致）
            planned = []
            for i, call in enumerate(calls):
                name = call.get("name") or ""
                item = {"i": i, "name": name, "args": {}, "err": None, "quit": False}
                try:
                    raw_args = call.get("arguments") or ""
                    args = json.loads(raw_args or "{}")
                except json.JSONDecodeError as e:
                    pos = e.pos if isinstance(e.pos, int) else 0
                    near = (raw_args[max(0, pos - 40):pos + 40]
                            .replace("\n", "\\n").replace("\t", "\\t"))
                    why = []
                    if re.search(r"'[^']*'", raw_args):
                        why.append("字符串用了单引号（JSON 只认双引号）")
                    if re.search(r",\s*[}\]]", raw_args):
                        why.append("多了一个尾随逗号")
                    if re.search(r"(^|[,{]\s*)[A-Za-z_]\w*\s*:", raw_args):
                        why.append("键名没加双引号")
                    if "\n" in raw_args or "\t" in raw_args:
                        why.append("值里的换行/Tab 要转义成 \\n、\\t")
                    item["err"] = (
                        f"[错误] 工具「{name}」的 arguments 不是合法 JSON：{e.msg}"
                        f"（第 {e.lineno} 行第 {e.colno} 列）。\n"
                        + (f"出错位置附近：…{near}…\n" if near else "")
                        + (f"可能原因：{'；'.join(why)}。\n" if why else "")
                        + f"原始 arguments：{raw_args[:400]}"
                        + ("…（已截断）" if len(raw_args) > 400 else "")
                        + "\n请把 arguments 改成合法 JSON 对象后重新调用："
                          "键与字符串都用双引号、去掉尾随逗号、换行用 \\n 转义，"
                          "不要带 markdown 代码块标记（```）。")
                else:
                    item["args"] = args
                    ok, reason = self.approver.check(name, args)
                    if not ok:
                        if reason == "QUIT":
                            item["quit"] = True
                            item["err"] = "用户要求退出。"
                        else:
                            item["err"] = f"[未执行] {reason}"
                planned.append(item)

            # ---- 同轮里互不依赖的只读工具并行发出（对照 codex 的 parallel tool calls）
            pool = None
            ready = [it for it in planned
                     if not it["err"] and str(it["name"]).strip() in PARALLEL_TOOLS]
            if len(ready) > 1:
                pool = ThreadPoolExecutor(max_workers=min(PARALLEL_MAX_WORKERS, len(ready)))
                for it in ready:
                    it["fut"] = pool.submit(self._dispatch, it["name"], it["args"])
                    it["t0"] = time.time()
                self.emit("status", f"并行执行 {len(ready)} 个只读工具…")

            stopped = False
            try:
                for it in planned:
                    name, args = it["name"], it["args"]
                    ms = 0          # 这一步工具耗时（毫秒），界面用它标出慢步骤
                    key = f"{step}-{it['i']}"
                    it["key"] = key
                    result = it["err"]
                    if it.get("quit"):
                        stopped = True
                    if result is None:
                        fut = it.get("fut")
                        if fut is not None:
                            self.emit("status", f"正在{TOOL_LABELS.get(name, name)}…")
                            try:
                                result = fut.result(timeout=PARALLEL_TIMEOUT)
                            except TimeoutError:
                                result = (f"[错误] 这个工具超过 {PARALLEL_TIMEOUT}s 没返回，已放弃等待。"
                                          "请换用更小的范围重试。")
                            except Exception as e:
                                result = f"[错误] 工具执行异常: {e}"
                            ms = int((time.time() - it.get("t0", time.time())) * 1000)
                        else:
                            self.emit("status", f"正在{TOOL_LABELS.get(name, name)}…")
                            rt = _runtime()
                            if rt is not None:
                                rt.begin(key, TOOL_LABELS.get(name, name),
                                         _tool_detail(name, args))
                            _t0 = time.time()
                            result = self._dispatch(name, args)
                            ms = int((time.time() - _t0) * 1000)
                        if ev.is_set():
                            # 停止期间工具刚好返回 → 不再追加结果，立即收尾
                            self.emit("notice", "已停止")
                            return final_text
                        result = self._repeat_guard(seen_calls, name, args, result)

                    self.emit("tool", {
                        "key": key,
                        "name": name,
                        "label": TOOL_LABELS.get(name, name),
                        "detail": _tool_detail(name, args),
                        "args": _tool_args_short(name, args),
                        "result": result,
                        "summary": _tool_summary(name, result)
                                   + (f" · {ms / 1000:.1f}s" if ms >= 1000 else ""),
                        "blocked": str(result).startswith("[未执行]"),
                    })

                    self.messages.append({
                        "role": "tool",
                        # 必须用本轮自己的下标：早先误用了上个循环残留的 i，
                        # 结果一轮里多个工具调用会挂同一个 id，API 直接报 400。
                        "tool_call_id": (msg["tool_calls"][it["i"]]["id"]
                                         if msg.get("tool_calls") and it["i"] < len(msg["tool_calls"])
                                         else f"call_{it['i']}"),
                        "content": result,
                    })
                    self._autosave()     # 工具结果落盘
            finally:
                if pool is not None:
                    pool.shutdown(wait=False)
            if stopped:
                rt = _runtime()
                if rt is not None:
                    rt.kill_all()
                self.emit("message_end", None)
                return final_text or "（已停止）"

            self.messages = prune_context(self.messages, max(PRUNE_AT_CHARS, int(self.compress_at * PRUNE_RATIO)))

        self.emit("message_end", None)
        notify_long_task(self.cfg, _task_t0, final_text)
        if notify_pref(self.cfg, "stuck"):
            notify_async(self.cfg, "到达轮次上限",
                         "已执行 %d 轮工具调用后停下，任务可能还没做完。" % max_rounds,
                         tag="round_limit", url=page_url(self.cfg), throttle=30)
        return (final_text +
                f"\n[提示] 操作轮次达到上限（{max_rounds} 轮）已停止，任务可能未完成。"
                "可以让我接着做，或在设置页把「单轮最多工具轮次」调大。")

    # 模型偶尔会写口语化的工具名（ls / run / cat）或参数名（filename / cmd）。
    # 表里只放「意思唯一、不会误伤」的说法，命中就顺手纠正，省掉一整轮无效往返。
    _NAME_FIXUPS = {
        "read": "read_file", "cat": "read_file", "open": "read_file",
        "view": "read_file", "show": "read_file", "cat_file": "read_file",
        "readfile": "read_file", "open_file": "read_file", "view_file": "read_file",
        "write": "write_file", "save": "write_file", "create": "write_file",
        "save_file": "write_file", "writefile": "write_file", "create_file": "write_file",
        "edit": "edit_file", "replace": "edit_file", "modify": "edit_file",
        "patch": "apply_patch", "sed": "edit_file", "update_file": "edit_file",
        "apply_diff": "apply_patch", "diff": "apply_patch", "applypatch": "apply_patch",
        "edit_patch": "apply_patch", "multi_edit": "apply_patch",
        "ls": "list_dir", "dir": "list_dir", "ll": "list_dir", "list": "list_dir",
        "listdir": "list_dir", "list_files": "list_dir",
        "search": "grep", "find": "grep", "search_files": "grep",
        "search_content": "grep", "find_in_files": "grep",
        "shell": "bash", "sh": "bash", "run": "bash", "exec": "bash",
        "execute": "bash", "command": "bash", "cmd": "bash", "terminal": "bash",
        "run_command": "bash", "execute_command": "bash", "bash_command": "bash",
        "adb": "sysshell", "adb_shell": "sysshell", "system_shell": "sysshell",
        "su": "sysshell", "root_shell": "sysshell",
        "fetch": "fetch_url", "curl": "fetch_url", "http_get": "fetch_url",
        "get_url": "fetch_url", "urlopen": "fetch_url",
        "wget": "download", "download_file": "download", "download_url": "download",
        "google": "web_search", "bing": "web_search", "websearch": "web_search",
        "search_web": "web_search",
        "todo": "todo_write", "todos": "todo_write", "task_list": "todo_write",
        "agent": "subagent", "sub_agent": "subagent", "task": "subagent",
        "update_self": "selfupdate", "self_update": "selfupdate",
        "update_code": "selfupdate", "edit_self": "selfupdate",
    }
    _ARG_FIXUPS = {
        "file": "path", "filename": "path", "filepath": "path", "file_path": "path",
        "directory": "path", "folder": "path", "dir": "path",
        "cmd": "command", "shell_command": "command",
        "text": "content", "data": "content",
        "old": "old_string", "new": "new_string",
        "old_str": "old_string", "new_str": "new_string",
        "regex": "pattern", "regexp": "pattern", "keyword": "query",
        "timeout": "timeout_sec", "limit": "max_results",
        "diff": "patch", "patch_text": "patch", "content": "patch", "unified_diff": "patch",
    }

    @staticmethod
    def _repeat_guard(seen: dict, name: str, args: dict, result: str) -> str:
        """同一轮里反复用同样参数调同一个工具、结果也一样 → 提醒模型别再原地打转。"""
        try:
            key = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
        except (TypeError, ValueError):
            return result
        prev = seen.get(key)
        if prev is not None and prev[0] == result:
            prev[1] += 1
            if prev[1] >= 2:            # 第三次完全相同 → 明确叫停
                if notify_pref(self.cfg, "stuck"):
                    notify_async(self.cfg, "任务好像卡住了",
                                 "连续用相同参数调用 %s 且结果一样，已叫停。" % name,
                                 tag="stuck", url=page_url(self.cfg), throttle=60)
                return (result + f"\n\n[提示] 这已经是第 {prev[1] + 1} 次用完全相同的参数调用"
                                 f"{name}，结果也一模一样。请不要再重复，换一种做法，"
                                 "或者直接把已有结论告诉用户。")
        else:
            seen[key] = [result, 0]
        return result

    def _dispatch(self, name: str, args: dict) -> str:
        name = str(name or "").strip()
        impl = TOOL_IMPLS.get(name)
        note = ""
        if impl is None:
            real = self._NAME_FIXUPS.get(re.sub(r"[\s\-.]+", "_", name.lower()).strip("_"))
            if real == "apply_patch" and isinstance(args, dict):
                has_patch = any(k in args for k in ("patch", "diff", "patch_text",
                                                    "content", "unified_diff"))
                if not has_patch and ("old_string" in args or "new_string" in args):
                    real = "edit_file"      # 说的是 patch，但给的是片段替换 → 按 edit_file 处理
            if real and real in TOOL_IMPLS:
                note = f"[提示] 没有名为 {name} 的工具，已按 {real} 自动处理。\n"
                name, impl = real, TOOL_IMPLS[real]
        if impl is None:
            try:
                import difflib
                near = difflib.get_close_matches(name, list(TOOL_IMPLS), n=3, cutoff=0.5)
            except Exception:
                near = []
            hint = f"，是否想用：{' / '.join(near)}？" if near else "。"
            return f"[错误] 未知工具 {name}{hint} 可用工具：{', '.join(TOOL_IMPLS)}"
        if isinstance(args, dict):
            props, _req = _tool_params(name)
            args = dict(args)
            fixed = []
            for k in list(args):
                if k in props:
                    continue
                nk = re.sub(r"[\s\-.]+", "_", str(k).strip().lower())
                real = self._ARG_FIXUPS.get(nk)
                if real and real in props:
                    pass                    # 老别名（cmd / file / timeout）照旧静默纠正
                else:
                    # 参数名拼错（commnad / limt / oldstring）→ 就近纠正成合法参数名，
                    # 别当「多余参数」丢掉，否则这次调用多半会因缺必填参数白跑一轮。
                    # 两道防线：名字至少得像到 0.8；它本身就是「别的工具的合法参数」时
                    # 不动它（如给 read_file 传 patch），免得把补丁内容当成路径写出去。
                    real = ""
                    try:
                        import difflib
                        low = {str(x).lower(): x for x in props}
                        known = set()
                        for _sc in TOOL_SCHEMAS:
                            _pa = (_sc.get("function") or {}).get("parameters") or {}
                            known.update(str(x).lower() for x in (_pa.get("properties") or {}))
                        cand = difflib.get_close_matches(nk, list(low), n=1, cutoff=0.8)
                        if cand and nk not in known:
                            real = low[cand[0]]
                    except Exception:
                        real = ""
                    if real:
                        fixed.append(f"{k}→{real}")
                if real and real in props and real not in args:
                    args[real] = args.pop(k)
            if fixed:
                note += f"[提示] 参数名已自动纠正：{'、'.join(fixed)}。\n"
            if props:
                # 工具签名里没有的多余参数就地丢掉（并提示），免得整轮调用因 TypeError 白跑
                dropped = [k for k in list(args) if k not in props]
                for k in dropped:
                    args.pop(k, None)
                if dropped:
                    tip = []
                    for k in dropped:
                        try:
                            import difflib
                            near = difflib.get_close_matches(str(k), list(props), n=1, cutoff=0.6)
                        except Exception:
                            near = []
                        tip.append(str(k) + (f"（是否想用 {near[0]}？）" if near else ""))
                    note += (f"[提示] {name} 已忽略它不支持的多余参数：{'、'.join(tip)}。"
                             f"{_schema_hint(name)} ")
        args, bad = _coerce_args(name, args)
        if bad:
            return (note + f"[错误] 工具参数无法使用：{bad}。请按正确类型重新调用，"
                    f"不要原样重试。{_schema_hint(name)}")
        try:
            return note + impl(self.cfg, **args)
        except TypeError as e:
            req, names = [], []
            for s in TOOL_SCHEMAS:
                f = s.get("function") or {}
                if f.get("name") == name:
                    pa = f.get("parameters") or {}
                    req = pa.get("required") or []
                    names = list(pa.get("properties") or {})
                    break
            detail = str(e)
            lead = ""
            try:
                import difflib
                m = re.search(r"unexpected keyword argument '([^']+)'", detail)
                if m:
                    wrong = m.group(1)
                    near = difflib.get_close_matches(wrong, names, n=1, cutoff=0.5) if names else []
                    lead = (f"参数名 {wrong} 不是本工具的参数，"
                            + (f"是否想用 {near[0]}？" if near else "请对照下面的参数表。"))
                else:
                    m = re.search(r"missing \d+ required positional argument[s]?: '([^']+)'", detail)
                    if m:
                        lead = f"缺少必填参数 {m.group(1)}。"
            except Exception:
                lead = ""
            hint = _schema_hint(name)
            if not hint and names:
                hint = " 该工具参数：" + "、".join(
                    k + ("（必填）" if k in req else "（可选）") for k in names)
            if hint and note and hint.strip() in note:
                hint = ""       # 前面「已忽略多余参数」的提示里已给过同一份参数表，不重复刷
            return note + f"[错误] 工具参数不匹配：{(lead or detail).rstrip('。')}。{hint}"
        except Exception as e:
            return f"[错误] 工具执行异常：{type(e).__name__}: {e}"

    def _stream_once(self, cancel=None):
        """流式拉取一次模型响应。返回 (正文, 工具调用列表, finish_reason, 思考内容)。"""
        ev = cancel if cancel is not None else self.cancel
        text_parts, calls, finish = [], [], None
        reasoning_parts = []
        reasoning_chars = 0
        emitted_reasoning = False
        self.stream_text = ""            # 新的一轮：清掉上一轮残留
        rea_buf, rea_last = [], 0.0      # 思考内容攒一下再发，不然每个 token 一条消息

        for kind, data in stream_completion(self.cfg, self.messages, build_tools(self.cfg)):
            if ev.is_set():
                finish = "cancelled"
                break
            if kind == "reasoning":
                reasoning_parts.append(data)
                if self.show_reasoning:
                    if not emitted_reasoning:
                        self.emit("status", "思考中…")
                        emitted_reasoning = True
                    reasoning_chars += len(data)
                    # 思考过程推给界面（前端可折叠面板流式展示）；攒够一点或过 0.1s 再发
                    rea_buf.append(data)
                    now = time.time()
                    if sum(len(x) for x in rea_buf) >= 24 or (now - rea_last) >= 0.1:
                        self.emit("reasoning", "".join(rea_buf))
                        rea_buf.clear()
                        rea_last = now
            elif kind == "text":
                self.emit("text", data)
                text_parts.append(data)
                self.stream_text += data     # 边生成边攒，切会话/重连时能把这段放出来
            elif kind == "tool_calls_final":
                calls = data
            elif kind == "usage":
                u = data or {}
                self.total_prompt_tokens += int(u.get("prompt_tokens") or 0)
                self.total_completion_tokens += int(u.get("completion_tokens") or 0)
            elif kind == "done":
                finish = data

        if rea_buf:                      # 收尾：把没发完的思考内容补上
            self.emit("reasoning", "".join(rea_buf))
            rea_buf.clear()
        reasoning = "".join(reasoning_parts)
        if len(reasoning) > REASONING_KEEP:      # 太长的思考只留开头，避免每轮都回传一大堆
            reasoning = reasoning[:REASONING_KEEP] + "…（思考过程已截断）"
        return "".join(text_parts), calls, finish, reasoning


class AgentHub:
    """按会话保存独立的 Agent 实例。

    以前全局只有一个 Agent：本地模型一跑，所有对话全被卡住，
    切会话还会串上下文。现在每个对话有自己的实例，各跑各的、互不阻塞。
    """

    def __init__(self, cfg: dict, approver):
        self.base_cfg = cfg
        self.approver = approver
        self._agents = {}
        self._lock = threading.RLock()
        # 最近活跃的会话：自愈续跑要往这个会话里发消息，不能瞎猜。
        # 以前它拿的是 state["agent"]（服务启动时那个实例），换过会话就发错地方 ——
        # 用户在界面上什么也看不到，还以为是"自动继续没生效"。
        self.last_active_sid = ""

    def touch(self, sid: str) -> None:
        """记下"用户当前在用的会话"。每次收到该会话的消息时调用。"""
        if sid:
            with self._lock:
                self.last_active_sid = sid

    def get(self, sid: str):
        """取某个会话的 Agent，没有就按会话记录建一个。"""
        if not sid:
            return None
        with self._lock:
            ag = self._agents.get(sid)
            if ag is not None and ag.session_id != sid:
                # 这个实例已经"换了身份"（新建对话 / 归档后 session_id 变了），
                # 旧会话 id 的键却还指着它 —— 点旧会话会拿到同一个实例，
                # 界面看着像"没切过去"（无法选中另一个会话）。丢掉旧键，按磁盘重建。
                self._agents.pop(sid, None)
                ag = None
            if ag is None:
                try:
                    ag = self._make(sid)
                except Exception:
                    return None
                self._agents[sid] = ag
            return ag

    def _make(self, sid: str):
        c = dict(self.base_cfg)                 # 每个会话一份自己的配置副本
        m = load_session_model(sid) or {}
        if m.get("model"):
            c["model"] = m["model"]
        if m.get("base_url"):
            c["base_url"] = m["base_url"]
        ag = Agent(c, self.approver, show_reasoning=True)
        ag.session_id = sid
        msgs = load_session_messages(sid)
        if msgs:
            ag.messages = _sanitize_messages(msgs)
        r = load_session_compress_ratio(sid)
        if r:
            ag.compress_ratio = r
        return ag

    def adopt(self, ag) -> None:
        """把已有的 agent 收进池子（启动时恢复最近会话用）。"""
        if ag is not None and ag.session_id:
            with self._lock:
                self._agents[ag.session_id] = ag

    def drop(self, sid: str) -> None:
        with self._lock:
            self._agents.pop(sid, None)

    def sync_shared(self, cfg: dict) -> None:
        """全局配置变了：共享字段同步给所有会话（模型/地址跟着会话自己走）。"""
        with self._lock:
            for ag in self._agents.values():
                for k, v in cfg.items():
                    if k in ("model", "base_url"):
                        continue
                    ag.cfg[k] = v

    def others(self, sid: str) -> list:
        with self._lock:
            return [ag for k, ag in self._agents.items() if k != sid]


def _agent_of(state: dict, conn: dict):
    """当前该由哪个 Agent 实例接活。

    给了 conn.sid 就用那个会话（页面进来的请求都带）；
    没给（自愈续跑就是这种，它不知道用户开着哪个页面）就用"最近活跃会话"，
    拿不到再退回默认实例。绝不能只认 state["agent"] —— 那是服务启动时的实例，
    用户切过会话之后就发错地方了，界面上什么都看不到。
    """
    hub = state.get("hub")
    sid = (conn or {}).get("sid")
    if hub and sid:
        ag = hub.get(sid)
        if ag is not None:
            return ag
    if hub:
        last = getattr(hub, "last_active_sid", "")
        if last:
            ag = hub.get(last)
            if ag is not None:
                return ag
    return state["agent"]


def _one_line(s: str, n: int = 100) -> str:
    s = " ".join(str(s).split())
    return (s[:n] + "…") if len(s) > n else s


# ---------------------------------------------------------------- 多语言（i18n）
# 界面文案字典：键名区域_语义。t() 取词，缺失自动回落中文。
# 加语言 = 往 I18N_BUILD 里加一个语言码的字典，不必改其它代码。

I18N_BUILD = {
    # ============ 语言元信息 ============
    "meta": {
        "zh":    {"name": "简体中文", "native": "简体中文", "dir": "ltr"},
        "en":    {"name": "English", "native": "English", "dir": "ltr"},
    },

    # ============ 第一批：约 90 条高频文案 ============
    # 键名规则：区域_语义，如 chat_send / cfg_model / evo_done
    "zh": {
        # --- 顶栏 / 导航 ---
        "nav_back": "‹  返回",
        "nav_settings": "设置",
        "nav_console": "控制台",
        "nav_close": "关闭",
        "nav_help": "查看说明",
        "nav_updates": "查看更新记录",
        "nav_expand_list": "展开对话列表",
        "nav_collapse": "收起侧栏（双击分界线也能切换）",
        "nav_resize": "拖动调整宽度 · 双击收起/展开",
        "nav_lang": "语言",

        # --- 输入区 ---
        "in_placeholder": "说点什么…",
        "in_send": "发送",
        "in_stop": "停止",
        "in_stopping": "停止中…",
        "in_attach": "发送图片或文件",
        "in_hint_full": "Enter 发送 · Shift+Enter 换行 · ↑↓ 翻历史 · Esc 中断 · @ 引用文件 · Ctrl+F 搜索 · / 看命令",

        # --- 对话 ---
        "chat_thinking": "思考中…",
        "chat_thoughts": "思考过程",
        "chat_assistant": "助手",
        "chat_copy_msg": "复制这条消息",
        "chat_copy_reply": "复制这条回答",
        "chat_continue": "继续",
        "chat_find": "在对话里搜索（Ctrl+F）",
        "chat_find_ph": "在对话里搜索…",
        "chat_find_prev": "上一个（Shift+Enter）",
        "chat_find_next": "下一个（Enter）",
        "chat_find_close": "关闭（Esc）",
        "chat_to_latest": "↓ 回到最新",
        "chat_done_goto": "任务已完成，点这里回到最新",
        "chat_empty_reply": "没有返回内容",
        "chat_interrupted": "已中断",

        # --- 最近对话 ---
        "sess_recent": "最近对话",
        "sess_pin": "置顶",
        "sess_unpin": "取消置顶",
        "sess_pin_tip": "置顶后会排在最前面，并用金色高亮",
        "sess_rename": "改名",
        "sess_rename_tip": "给这个对话起个名字（留空则恢复自动标题）",
        "sess_new": "开一个新对话",
        "sess_archive": "归档",
        "sess_archive_tip": "压缩上下文后归档保存（不会丢，可恢复）",
        "sess_delete": "删除（不可恢复，建议先归档）",
        "sess_restore": "恢复",
        "sess_restore_tip": "放回「最近对话」，可以接着聊",

        # --- 待发送队列 ---
        "q_title": "待发送",
        "q_clear": "清空",
        "q_jump": "插队",
        "q_jump_tip": "不中断当前任务，把这条插进当前任务里",
        "q_no_task": "当前没有在跑的任务，直接发就行",
        "q_jumped": "已插队，任务不会中断",
        "q_remove": "移除",

        # --- 文件 / 图片 ---
        "file_remove": "移除",
        "file_remove_img": "移除图片",
        "file_too_big": "文件太大（上限 30MB）",
        "file_too_big_img": "非图片文件上限 8MB",
        "file_pick_fail": "读取文件失败",
        "file_pick_fail_img": "读取图片失败",
        "file_uploading": "正在上传图片…",
        "file_upload_fail": "图片上传失败",

        # --- 上下文 ---
        "ctx_used": "已用 ≈",
        "ctx_compress_at": "% 时压缩）",
        "ctx_ring0": "上下文 0 / 0 token · 0%",

        # --- 设置：模型 ---
        "cfg_brand": "品牌",
        "cfg_base_url": "接口地址",
        "cfg_model": "模型",
        "cfg_balance": "余额",
        "cfg_balance_tip": "点「查询」看剩余额度",
        "cfg_query": "正在查询…",
        "cfg_query_fail": "查询失败",
        "cfg_no_balance_api": "该服务商未提供余额接口",
        "cfg_pulling": "正在拉取模型…",
        "cfg_pull_fail": "拉取失败：",
        "cfg_custom": "自定义…",
        "cfg_local_offline": "本地模型（离线可用）",
        "cfg_local_stopped": "本地模型（服务未启动）",
        "cfg_no_result": "无结果",
        "cfg_saved": "已保存",
        "cfg_key": "API Key",
        "cfg_key_ph": "sk-…（留空则不变）",
        "cfg_base_ph": "https://api.deepseek.com/v1",
        "cfg_shell": "增强权限",
        "cfg_shell_note": "以 shell（adb）身份执行命令，可读系统设置、dumpsys、pm/am。不是 root。",
        "cfg_pull": "拉取",
        "cfg_query_btn": "查询",
        "cfg_save": "保存",
        "cfg_note1": "选好「品牌」，接口地址会自动填上，不必手打。",
        "cfg_note2": "预设覆盖 DeepSeek、智谱、通义等常见品牌，选中即自动填接口地址。用自建或代理服务时选「自定义」，自己填地址。",
        "cfg_note3": "各品牌的 API Key 分开记住：换品牌不会丢、也不会互相覆盖。要改哪个品牌，选中它、重新输入一次即可。",
        "cfg_note4": "Key 存在本机 config.json 里，只用于向对应接口发请求；这个界面只监听 127.0.0.1，不对局域网开放。",
        "cfg_note5": "填好 Key 后点「拉取」获取该接口的可用模型；点「查询」看余额或积分。",
        "cfg_note6": "「拉取」拿到的是接口当前真实提供的模型列表，所以新模型上线不用等更新，拉一次就有。",
        "cfg_local_suffix": "（本地）",
        "cfg_local_online_a": "本地模型在线：",
        "cfg_local_online_b": "，选中会自动指向 ",
        "cfg_lang_untranslated": "  (未翻译)",
        "cfg_key_stored_a": "该品牌已存 Key：",
        "cfg_key_stored_b": "，留空即保持不变",
        "cfg_key_none": "该品牌还没存过 Key，填一次即记住",
        "cfg_need_base": "先填接口地址（或选一个品牌）",
        "cfg_fetched_n": " 个模型，选中后点「保存」生效",
        "cfg_balance_money_a": "（充值 ",
        "cfg_balance_money_b": " + 赠送 ",
        "cfg_balance_money_c": "）",
        "cfg_unknown": "未知原因",

        # --- 进化 ---
        "evo_auto": "自动进化",
        "evo_direction": "进化方向",
        "evo_daily": "每天",
        "evo_run_now": "立即进化一次",
        "evo_on": "· 已开启",
        "evo_off": "· 已关闭",
        "evo_today": "今日",
        "evo_done": "进化完成：",
        "evo_pick_one": "点一次才出来",
        "evo_locked": "已锁定",
        "evo_unlocked": "未锁定 · AI 每天自动换一批",

        # --- 更新记录 ---
        "upd_title": "更新记录",
        "upd_desc": "每次自我更新 / 自动进化 / 手动进化都留一条：版本号 · 时间 · 改了什么。",
        "upd_empty": "还没有记录。自我更新或进化后会显示在这里。",

        # --- 记忆 ---
        "mem_title": "记忆",
        "mem_long": "长期记忆",
        "mem_empty": "还没有积累记忆。多聊几句，它会自动提炼。",

        # --- 任务清单 ---
        "todo_title": "任务清单",
        "todo_clear": "清空清单",

        # --- 工具 / 技能 ---
        "tool_need_pick": "需要你拍板",
        "tool_switched": "已切换到",

        # --- 通用 ---
        "ok_copied": "已复制",
        "ok_saved": "已保存",
        "err_copy_fail": "复制失败，请长按消息手动选择",
        "err_generic": "出错了：",
        "log_pick_a": "这是我从控制台「运行日志」里选的 ",
        "log_pick_b": " 的末尾内容，请先判断有没有问题，能直接修的就动手修，修不了说明原因：\n\n",
        "evo_dir_help": "点一颗胶囊即锁定该方向（再点一下取消），可同时锁定多个；不锁定就每天由 AI 自动换一批。自动进化与手动进化都优先按锁定的方向做。「每天几项」是自动进化每天最多做几项，手动点的不占额度。<br>每项改动都会自动备份 + 语法检查，失败立即还原；服务由看门狗托管，起不来会自动回滚。",
        "look_reset": "恢复默认",
        "chat_more_steps": "点击展开 / 收起更早的步骤",
        "queue_inject_title": "不中断当前任务，把这条插进当前任务里",
        "queue_remove": "移除",
        "file": "文件",
        "file_no_preview": "浏览器无法预览，由服务端转换",
        "img_remove": "移除图片",
        "chat_done_title": "任务已完成，点这里回到最新",
        "skill_gen_fail": "生成失败：",
        "chat_slow": "响应较慢…若平板刚拔掉电源线，可能被系统省电冻结了",
        "look_preset": "预设",
        "evo_ai_picking": "正在让 AI 研究今天的方向…",
        "slash_hint": "↑↓ 选择 · Enter 执行 · Tab 补全 · Esc 关闭",
        "at_hint": "@ 引用文件 · ↑↓ 选择 · Enter 插入 · Esc 关闭",
        "sess_no_match_a": "没有匹配「",
        "sess_no_match_b": "」的对话",
        "evo_per": "每天",
        "evo_items": "项",
        "evo_today_prefix": "今日 ",
        "cfg_no_models": "这个后端没有 /models 接口，列出的是内置清单（",
        "cfg_no_models_mid": " 个），仅供参考",
        "cfg_balance": "余额",
        "cfg_used_local": "本机累计消耗",
        "cfg_points": "点",
        "log_ai_check": "【AI 自检：",
        "log_ai_fail": "自检失败：",
        "log_send_ai": "发给 AI 解决",
        "log_ai_analyzing": "AI 正在分析…",
        "sess_none": "还没有对话",
        "chat_you_inject": "你（插队）",
        "evo_auto": "自动进化",
        "evo_dir": "进化方向",
        "evo_btn_now": "立即进化一次",
        "log_none": "(没有日志文件)",
        "log_selected": "已选 ",
        "chat_back_latest": "↓ 回到最新",
        "chat_thinking": "思考过程",
        "queue_pending": "待发送 ",
        "queue_clear": "清空",
        "queue_inject": "插队",
        "chat_interrupted": "已中断",
        "chat_no_content": "没有返回内容",
        "sess_archived_note": "点归档项可看摘要；原文完整保存在 archive/ 目录里。",
        "log_none_yet": "还没有记录。自我更新或进化后会显示在这里。",
        "cfg_fetching_models": "正在拉取模型…",
        "cfg_querying": "正在查询…",
        "cfg_fetch_fail": "拉取失败：",
        "cfg_fetched": "已拉到 ",
        "evo_dir_hint": "每天由 AI 自动换一批；点一颗即可锁定它",
        "cfg_custom_note": "自定义接口：Key 按接口地址单独记",
        "chat_copied": "已复制",
        "chat_copy_fail": "复制失败，请长按消息手动选择",
        "chat_copy_stale": "这段代码已经不在缓存里了",
        "chat_copy": "复制",
        "ctx_compress_at_prefix": "（",
        "ctx_at_static": "% 时压缩",
        "sess_search_ph": "搜索对话…",
        "sess_empty": "（空）",
        "cfg_local_none": "未检测到 · 先执行 local-model.sh start",
        "find_none": "无结果",
        "chat_search": "搜索",
        "in_stop": "停止",
        "in_stopping": "停止中…",
        "in_hint_queue": "回车排队，这条会在当前任务结束后自动发出 · Esc 中断",
        "in_hint_stop": "Esc 中断当前任务",
        "cfg_name": "名称",
        "cfg_name_ph": "给这个助手起个名字",
        "evo_daily": "每天自动进化",
        "evo_dir": "进化方向",
        "evo_perday": "每天几项",
        "evo_today": "今日进度",
        "evo_manual": "手动进化",
        "evo_btn_now": "立即进化一次",
        "cfg_changelog": "更新记录",
        "cfg_memory": "记忆",
        "cfg_memory_long": "长期记忆",
        "cfg_ctx": "上下文",
        "cfg_ctx_use": "占用",
        "cfg_compact": "压缩时机",
        "cfg_tools": "工具",
        "cfg_all": "全选",
        "th_skill": "技能",
        "th_desc": "说明",
        "th_size": "大小",
        "th_made": "建立",
        "th_tool": "工具",
        "th_use": "用途",
        "sess_new_btn": "新对话",
        "ctl_identity": "身份",
        "evo_sec": "进化",
        "ctl_look": "外观",
        "ctl_log": "日志",
        "ctl_skill": "技能",
        "hello_h1": "有什么可以帮你？",
        "hello_p": "它在本机运行，可以看文件、跑命令、整理资料",
        "reload_for_lang": "正在切换语言，页面将重新加载…",
    },

    "en": {
        # --- top bar / nav ---
        "nav_back": "‹  Back",
        "nav_settings": "Settings",
        "nav_console": "Console",
        "nav_close": "Close",
        "nav_help": "Help",
        "nav_updates": "Release notes",
        "nav_expand_list": "Show conversations",
        "nav_collapse": "Collapse sidebar (or double-tap the divider)",
        "nav_resize": "Drag to resize · double-tap to collapse",
        "nav_lang": "Language",

        # --- input ---
        "in_placeholder": "Type a message…",
        "in_send": "Send",
        "in_stop": "Stop",
        "in_stopping": "Stopping…",
        "in_attach": "Send image or file",
        "in_hint_full": "Enter to send · Shift+Enter for newline · ↑↓ history · Esc to interrupt · @ to reference files · Ctrl+F to search · / for commands",

        # --- chat ---
        "chat_thinking": "Thinking…",
        "chat_thoughts": "Reasoning",
        "chat_assistant": "Assistant",
        "chat_copy_msg": "Copy this message",
        "chat_copy_reply": "Copy this reply",
        "chat_continue": "Continue",
        "chat_find": "Search in conversation (Ctrl+F)",
        "chat_find_ph": "Search in conversation…",
        "chat_find_prev": "Previous (Shift+Enter)",
        "chat_find_next": "Next (Enter)",
        "chat_find_close": "Close (Esc)",
        "chat_to_latest": "↓ Jump to latest",
        "chat_done_goto": "Task finished — tap to jump to latest",
        "chat_empty_reply": "No content returned",
        "chat_interrupted": "Interrupted",

        # --- recent conversations ---
        "sess_recent": "Recent",
        "sess_pin": "Pin",
        "sess_unpin": "Unpin",
        "sess_pin_tip": "Pinned conversations stay on top, highlighted in gold",
        "sess_rename": "Rename",
        "sess_rename_tip": "Name this conversation (leave blank to restore auto title)",
        "sess_new": "New conversation",
        "sess_archive": "Archive",
        "sess_archive_tip": "Compress context and archive — nothing is lost, you can restore it",
        "sess_delete": "Delete (permanent — consider archiving first)",
        "sess_restore": "Restore",
        "sess_restore_tip": "Put it back into Recent so you can continue",

        # --- pending queue ---
        "q_title": "Queued",
        "q_clear": "Clear",
        "q_jump": "Jump queue",
        "q_jump_tip": "Insert this into the current task without interrupting it",
        "q_no_task": "No task running — just send it",
        "q_jumped": "Queued ahead — the task won't be interrupted",
        "q_remove": "Remove",

        # --- files / images ---
        "file_remove": "Remove",
        "file_remove_img": "Remove image",
        "file_too_big": "File too large (30MB max)",
        "file_too_big_img": "Non-image files are limited to 8MB",
        "file_pick_fail": "Failed to read file",
        "file_pick_fail_img": "Failed to read image",
        "file_uploading": "Uploading image…",
        "file_upload_fail": "Image upload failed",

        # --- context ---
        "ctx_used": "Used ≈",
        "ctx_compress_at": "% to compress)",
        "ctx_ring0": "Context 0 / 0 tokens · 0%",

        # --- settings: model ---
        "cfg_brand": "Provider",
        "cfg_base_url": "API endpoint",
        "cfg_model": "Model",
        "cfg_balance": "Balance",
        "cfg_balance_tip": "Click Query to check your remaining quota",
        "cfg_query": "Querying…",
        "cfg_query_fail": "Query failed",
        "cfg_no_balance_api": "This provider has no balance API",
        "cfg_pulling": "Fetching models…",
        "cfg_pull_fail": "Fetch failed: ",
        "cfg_custom": "Custom…",
        "cfg_local_offline": "Local model (works offline)",
        "cfg_local_stopped": "Local model (service not running)",
        "cfg_no_result": "No results",
        "cfg_saved": "Saved",
        "cfg_key": "API Key",
        "cfg_key_ph": "sk-… (leave blank to keep)",
        "cfg_base_ph": "https://api.deepseek.com/v1",
        "cfg_shell": "Shell access",
        "cfg_shell_note": "Run commands as the shell (adb) user: can read system settings, dumpsys, pm/am. Not root.",
        "cfg_pull": "Fetch",
        "cfg_query_btn": "Query",
        "cfg_save": "Save",
        "cfg_note1": "Pick a provider and the endpoint is filled in automatically — no need to type it.",
        "cfg_note2": "Presets cover DeepSeek, Zhipu, Qwen and other common providers; selecting one auto-fills the endpoint. For self-hosted or proxy services, pick “Custom” and enter the address yourself.",
        "cfg_note3": "API keys are remembered per provider: switching providers loses nothing and nothing overwrites another. To change a provider's key, select it and re-enter.",
        "cfg_note4": "Keys live in config.json on this device and are only used to call the matching endpoint; this UI listens on 127.0.0.1 only, never exposed to the LAN.",
        "cfg_note5": "After entering a key, click Fetch to list that endpoint's models; click Query to check balance or credits.",
        "cfg_note6": "Fetch returns the model list the endpoint actually serves right now, so new models show up without waiting for an update.",
        "cfg_local_suffix": " (local)",
        "cfg_local_online_a": "Local models online: ",
        "cfg_local_online_b": ", selecting one points to ",
        "cfg_lang_untranslated": "  (untranslated)",
        "cfg_key_stored_a": "Key stored for this provider: ",
        "cfg_key_stored_b": " — leave blank to keep",
        "cfg_key_none": "No key stored for this provider yet — enter one and it's remembered",
        "cfg_need_base": "Enter an endpoint first (or pick a provider)",
        "cfg_fetched_n": " models — click Save to apply",
        "cfg_balance_money_a": " (topped up ",
        "cfg_balance_money_b": " + granted ",
        "cfg_balance_money_c": ")",
        "cfg_unknown": "Unknown reason",

        # --- evolution ---
        "evo_auto": "Auto-evolve",
        "evo_direction": "Evolution focus",
        "evo_daily": "per day",
        "evo_run_now": "Evolve now",
        "evo_on": "· On",
        "evo_off": "· Off",
        "evo_today": "Today",
        "evo_done": "Evolution complete: ",
        "evo_pick_one": "Tap to reveal",
        "evo_locked": "Locked",
        "evo_unlocked": "Not locked · AI rotates the list daily",

        # --- release notes ---
        "upd_title": "Release notes",
        "upd_desc": "Every self-update, auto-evolution and manual evolution leaves a record: version · time · what changed.",
        "upd_empty": "Nothing here yet. Records appear after a self-update or evolution.",

        # --- memory ---
        "mem_title": "Memory",
        "mem_long": "Long-term memory",
        "mem_empty": "No memories yet. Chat a bit and it will distil them automatically.",

        # --- task list ---
        "todo_title": "Task list",
        "todo_clear": "Clear list",

        # --- tools / skills ---
        "tool_need_pick": "Needs your decision",
        "tool_switched": "Switched to ",

        # --- common ---
        "ok_copied": "Copied",
        "ok_saved": "Saved",
        "err_copy_fail": "Copy failed — long-press the message to select manually",
        "err_generic": "Error: ",
        "log_pick_a": "I picked this from the console run log: ",
        "log_pick_b": " — here is its tail. First judge if anything is wrong; fix what you can, otherwise explain why:\n\n",
        "evo_dir_help": "Click a capsule to lock that direction (click again to unlock); you can lock several at once. If none are locked, the AI picks a new batch each day. Both auto and manual evolution prioritise locked directions. “Per day” is the max auto-evolutions per day; manual ones don't count against it.<br>Every change is auto-backed up + syntax-checked and rolled back on failure; the service is watchdog-managed and self-recovers.",
        "look_reset": "Restore defaults",
        "chat_more_steps": "Expand / collapse earlier steps",
        "queue_inject_title": "Queue this into the current task without interrupting it",
        "queue_remove": "Remove",
        "file": "File",
        "file_no_preview": "Can't preview in browser — converted server-side",
        "img_remove": "Remove image",
        "chat_done_title": "Task finished — click to jump to latest",
        "skill_gen_fail": "Generation failed: ",
        "chat_slow": "Slow response… if the tablet was just unplugged, the system may have frozen it to save power",
        "look_preset": "Preset",
        "evo_ai_picking": "Asking AI to pick today's direction…",
        "slash_hint": "↑↓ choose · Enter run · Tab complete · Esc close",
        "at_hint": "@ reference file · ↑↓ choose · Enter insert · Esc close",
        "sess_no_match_a": "No chats matching “",
        "sess_no_match_b": "”",
        "evo_per": "per day",
        "evo_items": "items",
        "evo_today_prefix": "Today ",
        "cfg_no_models": "This backend has no /models API; showing built-in list (",
        "cfg_no_models_mid": "), for reference only",
        "cfg_balance": "Balance",
        "cfg_used_local": "Local usage",
        "cfg_points": "pts",
        "log_ai_check": "【AI self-check: ",
        "log_ai_fail": "Self-check failed: ",
        "log_send_ai": "Send to AI",
        "log_ai_analyzing": "AI analyzing…",
        "sess_none": "No chats yet",
        "chat_you_inject": "You (injected)",
        "evo_auto": "Auto-evolve",
        "evo_dir": "Direction",
        "evo_btn_now": "Evolve now",
        "log_none": "(no log files)",
        "log_selected": "Selected ",
        "chat_back_latest": "↓ Back to latest",
        "chat_thinking": "Thinking",
        "queue_pending": "Pending ",
        "queue_clear": "Clear",
        "queue_inject": "Inject",
        "chat_interrupted": "Interrupted",
        "chat_no_content": "No content",
        "sess_archived_note": "Click an archived item to view its summary; the original is kept in archive/.",
        "log_none_yet": "Nothing yet. It shows here after a self-update or evolution.",
        "cfg_fetching_models": "Fetching models…",
        "cfg_querying": "Querying…",
        "cfg_fetch_fail": "Fetch failed: ",
        "cfg_fetched": "Fetched ",
        "evo_dir_hint": "Auto-picked by AI daily; click one to lock it in",
        "cfg_custom_note": "Custom API: keys are remembered per endpoint",
        "chat_copied": "Copied",
        "chat_copy_fail": "Copy failed — long-press the message to select it",
        "chat_copy_stale": "This code block is no longer cached",
        "chat_copy": "Copy",
        "ctx_compress_at_prefix": " (",
        "ctx_at_static": "% to compress",
        "sess_search_ph": "Search chats…",
        "sess_empty": "(empty)",
        "cfg_local_none": "Not detected · run local-model.sh start first",
        "find_none": "No results",
        "chat_search": "Search",
        "in_stop": "Stop",
        "in_stopping": "Stopping…",
        "in_hint_queue": "Press Enter to queue — it sends when the current task finishes · Esc to interrupt",
        "in_hint_stop": "Esc to interrupt the current task",
        "cfg_name": "Name",
        "cfg_name_ph": "Give this assistant a name",
        "evo_daily": "Evolve daily",
        "evo_dir": "Direction",
        "evo_perday": "Per day",
        "evo_today": "Today",
        "evo_manual": "Manual",
        "evo_btn_now": "Evolve now",
        "cfg_changelog": "Changelog",
        "cfg_memory": "Memory",
        "cfg_memory_long": "Long-term memory",
        "cfg_ctx": "Context",
        "cfg_ctx_use": "Usage",
        "cfg_compact": "Compaction",
        "cfg_tools": "Tools",
        "cfg_all": "All",
        "th_skill": "Skill",
        "th_desc": "Description",
        "th_size": "Size",
        "th_made": "Created",
        "th_tool": "Tool",
        "th_use": "Purpose",
        "sess_new_btn": "New chat",
        "ctl_identity": "Identity",
        "evo_sec": "Evolution",
        "ctl_look": "Appearance",
        "ctl_log": "Logs",
        "ctl_skill": "Skills",
        "hello_h1": "How can I help?",
        "hello_p": "Runs on this device — it can read files, run commands and organise things",
        "reload_for_lang": "Switching language, reloading…",
    },
}

def t(key: str, lang: str | None = None, **fmt) -> str:
    """取一条界面文案。lang 为空时用全局界面语言；缺失则依次回落 中文 → key 本身。"""
    lc = (lang or globals().get("UI_LANG") or "zh")
    table = I18N_BUILD.get(lc) or {}
    s = table.get(key)
    if s is None:
        s = I18N_BUILD["zh"].get(key)
    if s is None:
        return key
    if fmt:
        try:
            s = s.format(**fmt)
        except Exception:
            pass
    return s


def detect_ui_lang(cfg: dict) -> str:
    """决定界面语言：config.ui_language 优先，其次系统语言猜测，最后中文。"""
    v = (cfg.get("ui_language") or "").strip()
    if v and v in I18N_BUILD:
        return v
    # 从环境变量猜（Termux 里通常是 C 或空，猜不到就中文）
    import os
    for ev in ("LC_ALL", "LC_MESSAGES", "LANG"):
        raw = (os.environ.get(ev) or "").strip()
        if not raw:
            continue
        low = raw.lower().replace("_", "-")
        for code in ("zh", "en"):
            if low.startswith(code):
                return code
    return "zh"


UI_LANG = "zh"



# ---------------------------------------------------------------- 网页界面

WEB_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#ffffff">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<link rel="manifest" href="/manifest.webmanifest">
<title>Sidekick</title>
<style>
/* 设计令牌取自官方 DSH 设计系统（--dsw-static-neutral-bluish-* / brand-primary #4176e6） */
:root{
  /* 浅色（跟随系统亮色）—— 按 iOS 浅色色板重做 */
  --brand:#007aff;
  --brand-soft:rgba(0,122,255,.12);
  --bg:#ffffff; --bg-side:#f2f2f7; --bg-inset:#ffffff; --bg-hover:#e9e9ee; --bg-active:#dedee3;
  --bg2:#f2f2f7;   /* 卡片/预览条底色：附件预览、待发送队列、Agent 提问面板 */
  --bd:rgba(60,60,67,.18); --bd-strong:rgba(60,60,67,.32);
  --t1:#000000; --t2:#3c3c43; --t3:#8e8e93; --t4:#c7c7cc;
  --ok:#34c759; --warn:#ff9500; --err:#ff3b30;
  --violet:#7c5cff;
  --code:#f2f2f7;
  --r:16px; --r-sm:10px;
  --font:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","HarmonyOS Sans SC","Noto Sans SC","Microsoft YaHei",sans-serif;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
@media (prefers-color-scheme: dark){
  :root{
    /* 深色（跟随系统深色）—— 纯黑底 + 层次卡片，按平板上的 App 设计还原 */
    --brand:#0a84ff; --brand-soft:rgba(10,132,255,.16);
    --bg:#000000; --bg-side:#0d0d0f; --bg-inset:#2c2c2e; --bg-hover:#2c2c2e; --bg-active:#3a3a3c;
    --bg2:#1c1c1e;
    --bd:rgba(255,255,255,.10); --bd-strong:rgba(255,255,255,.18);
    --t1:#ffffff; --t2:#e5e5ea; --t3:#8e8e93; --t4:#636366;
    --ok:#30d158; --warn:#ff9f0a; --err:#ff453a;
    --violet:#7c5cff;
    --code:#0b0b0d;
  }
}
/* ================= 视觉升级层 =================
   按参考 App 的设计语言统一外观：层次靠背景明度区分（页面 → 卡片 → 输入框逐层变亮），
   不靠描边；按钮统一胶囊；开关放大到 51×31 并带滑块。
   全部通过覆盖实现，不改动任何 HTML 结构与 id，所以功能零风险。
   注意：选择器统一加 body 前缀提升一级特异性，否则同级会被后面的原有定义反超。 */
body .card{background:var(--bg2);border:none}
body .card-h{background:var(--bg2);border-bottom:1px solid var(--bd)}
body .card-h b{font-weight:700}
body .row .lb{flex:0 0 92px;font-size:15px;color:var(--t3)}
body .row .ctl input[type=text],body .row .ctl input[type=password],body .row .ctl input[type=number],
body .row .ctl select,body .row .ctl textarea{
  background:var(--bg-inset);border:none;border-radius:var(--r-sm);padding:12px 14px;font-size:15px;color:var(--t1)}
body .row .ctl input:focus,body .row .ctl select:focus,body .row .ctl textarea:focus{
  border:none;box-shadow:0 0 0 2px var(--brand)}
body .row .note{font-size:12.5px;color:var(--t3)}
body .btn{border:none;background:var(--bg-inset);border-radius:999px;padding:12px 20px;font-size:15px;font-weight:600;color:var(--t1)}
body .btn:hover{background:var(--bg-hover)}
body .btn.pri{background:var(--brand);color:#fff}
body .sw{
  width:51px;height:31px;border-radius:16px;border:none;appearance:none;
  background-color:var(--bd-strong);
  background-image:radial-gradient(circle at center,#fff 0 13px,rgba(0,0,0,0) 13.6px);
  background-size:27px 27px;background-repeat:no-repeat;background-position:2px center;
  transition:background-position .18s,background-color .18s;cursor:pointer}
body .sw:checked{background-color:var(--brand);background-position:calc(100% - 2px) center}
body .log{background:var(--code);border:none;border-radius:var(--r-sm)}
body .log div{border-bottom:1px solid var(--bd)}
body .cap{background:var(--bg-inset);border:none}
body .sec-t{color:var(--t3);font-size:15px;font-weight:400}
/* 面板顶部栏：改成参考图那种「‹ 返回 + 大标题」左对齐导航。
   用 ::before 生成「‹ 返回」字样、再把原有关闭按钮的字符隐藏（font-size:0），
   order 把它挪到最左，标题放大到 23px 加粗。纯 CSS，不动 HTML。 */
body .sheet .card-h{display:flex;align-items:center;gap:12px;padding:16px}
body .sheet .card-h b{font-size:23px;font-weight:800;flex:1;order:2}
body .sheet .card-h button[data-close]{
  order:1;font-size:0;line-height:1;padding:0;background:none;border:none;width:auto;cursor:pointer}
body .sheet .card-h button[data-close]::before{
  content:"\2039  返回";font-size:17px;font-weight:400;color:var(--brand);line-height:1.2}
/* 侧栏会话列表：每项改成独立卡片（参考图 2 / 图 7 的卡片列表风格）。
   底色用 --bg-inset：深色下比侧栏亮、浅色下比侧栏白，两个主题都能看出"卡片"。 */
body .list{padding:8px 10px}
body .item{background:var(--bg-inset);border-radius:12px;margin-bottom:8px;padding:12px 14px}
body .item:hover{background:var(--bg-hover)}
/* 「新对话」按钮改胶囊，与底栏按钮统一 */
body .newbtn{border:none;background:var(--bg-inset);border-radius:999px;padding:13px 16px;font-size:15px;font-weight:600}
/* 侧栏底部两个入口：改胶囊按钮，去掉上边框 */
body .side-f{border-top:none;gap:8px;padding:10px}
body .side-f button{border-radius:999px;background:var(--bg-inset);font-size:15px;font-weight:600;color:var(--t1);padding:12px 0}
body .side-f button:hover{background:var(--bg-hover)}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{height:100%;margin:0}
body{
  background:var(--bg);color:var(--t1);font:400 16px/24px var(--font);
  display:flex;height:100dvh;overflow:hidden;
}
button{font:inherit;color:inherit;background:none;border:none;cursor:pointer}
input,textarea,select{font:inherit;color:inherit}

/* ---------------- 布局：侧栏 + 主区 ---------------- */
.app{display:flex;width:100%;height:100dvh}
.side{
  flex:0 0 auto;width:264px;min-width:0;display:flex;flex-direction:column;background:var(--bg-side);
  border-right:1px solid var(--bd);padding-top:env(safe-area-inset-top);
}
/* 中间可拖动的分界线（拖动改宽度，双击收起/展开） */
.gutter{
  flex:0 0 7px;margin-left:-4px;position:relative;z-index:6;cursor:col-resize;
  background:transparent;touch-action:none;
}
.gutter::after{
  content:'';position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
  width:3px;height:30px;border-radius:3px;background:var(--bd-strong);opacity:.5;
  transition:opacity .15s,background .15s,height .15s;
}
.gutter:hover::after,.gutter.on::after{opacity:1;background:var(--brand);height:44px}
.gutter.on{background:var(--brand-soft)}
/* 收起后贴左边出现的展开箭头：放在偏上位置，避免挡住正文 */
.sidefab{
  position:fixed;left:0;top:78px;z-index:66;display:none;
  width:26px;height:52px;padding:0 0 0 2px;
  align-items:center;justify-content:center;
  background:var(--bg-side);border:1px solid var(--bd);border-left:none;
  border-radius:0 13px 13px 0;color:var(--t3);font-size:17px;line-height:1;
  box-shadow:1px 0 8px rgba(0,0,0,.06);cursor:pointer;
}
.sidefab:hover{color:var(--brand);border-color:var(--brand)}
/* 收起状态只在宽屏生效（窄屏时侧栏是浮层，走 .side.open） */
@media (min-width: 821px){
  .app.collapsed .side,.app.collapsed .gutter{display:none}
  .app.collapsed .sidefab{display:flex}
  .app.collapsed .top .hamb{display:block}
  /* 收起时给正文与输入区让出左边一点，别被箭头压到字 */
  .app.collapsed .body{padding-left:36px}
  .app.collapsed .comp{padding-left:36px}
}
.side-h{display:flex;align-items:center;gap:8px;padding:14px 14px 10px}
.side-h .dot{width:8px;height:8px;border-radius:50%;background:var(--ok);flex:0 0 auto}
.side-h .dot.busy{background:var(--warn);animation:pulse 1.2s infinite}
.side-h .dot.err{background:var(--err)}
@keyframes pulse{50%{opacity:.35}}
.side-h b{font-weight:500;font-size:16px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.side-h .m{font-size:11px;color:var(--t3);font-family:var(--mono)}
.side-h .side-x{flex:0 0 auto;color:var(--t4);font-size:16px;line-height:1;padding:2px 5px;border-radius:5px}
.side-h .side-x:hover{background:var(--bg-hover);color:var(--t1)}
.newbtn{
  margin:0 12px 10px;padding:9px 12px;border-radius:var(--r-sm);border:1px solid var(--bd);
  background:var(--bg);color:var(--t1);font-size:14px;text-align:left;display:flex;align-items:center;gap:8px
}
.newbtn:active{background:var(--bg-hover)}
.newbtn .plus{color:var(--t3)}
.list{flex:1;overflow-y:auto;padding:4px 8px 8px;overscroll-behavior:contain}
.grp{font-size:11px;color:var(--t4);padding:10px 8px 4px;font-weight:500}
.item{padding:8px 10px;border-radius:var(--r-sm);margin-bottom:2px;cursor:pointer}
.item .r1{display:flex;align-items:center;gap:6px;min-width:0}
.item .r1 .t{flex:1;min-width:0}
.item .iacts{flex:0 0 auto;display:flex;gap:4px;opacity:.5;transition:opacity .12s}
.item:hover .iacts,.item:active .iacts{opacity:1}
.item .iacts button{border:1px solid var(--bd);background:var(--bg);border-radius:6px;height:20px;
  padding:0 6px;color:var(--t3);font-size:11px;line-height:1;cursor:pointer}
.item .iacts button:active{background:var(--bg-hover);color:var(--t1)}
.item .iacts button.del:active{color:var(--err);border-color:var(--err)}
.item .m .mm{color:var(--t3);background:var(--bg-hover);border-radius:4px;padding:0 4px}
.item .ar-s{display:none;margin-top:5px;font-size:11.5px;line-height:1.6;color:var(--t3);
  white-space:pre-wrap;word-break:break-word;max-height:170px;overflow-y:auto;
  border-left:2px solid var(--bd-strong);padding-left:7px}
.item.open .ar-s{display:block}
.grp.ar-head{cursor:pointer;display:flex;align-items:center;gap:6px}
.grp.ar-head:hover{color:var(--t3)}
.item:hover{background:var(--bg-hover)}
.item.cur{background:var(--bg-active)}
.item .t{font-size:13.5px;line-height:20px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.item .m{font-size:11px;color:var(--t4);margin-top:1px}
.side-f{border-top:1px solid var(--bd);padding:8px;display:flex;gap:6px}
.side-f button{flex:1;padding:9px;border-radius:var(--r-sm);font-size:13.5px;color:var(--t2)}
.side-f button:hover{background:var(--bg-hover);color:var(--t1)}

.main{flex:1;display:flex;flex-direction:column;min-width:0}
.top{
  flex:0 0 auto;display:flex;align-items:center;gap:10px;
  padding:calc(env(safe-area-inset-top) + 12px) 18px 12px;
  border-bottom:1px solid var(--bd)
}
.top .hamb{display:none;font-size:18px;color:var(--t2);padding:2px 6px}
.top .t1{font-size:15px;font-weight:500;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#findbtn{font-family:inherit;cursor:pointer}
/* ---- 图标：线性矢量（参照 SF Symbols 的视觉语言：圆角、1.7 描边、跟随文字色）----
   全内联，离线可用；用 <svg class="ic"><use href="#i-xxx"/></svg> 引用 ---- */
svg.ic{width:1.05em;height:1.05em;flex:0 0 auto;display:inline-block;vertical-align:-.14em;
  fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;
  pointer-events:none}
svg.ic.fill{fill:currentColor;stroke:none}
.chip svg.ic,.ph-btn svg.ic,.tbtn svg.ic{margin-right:.32em}
.chip svg.ic:only-child,.tbtn svg.ic:only-child,.ph-orb svg.ic{margin-right:0}
.ph-orb svg.ic{width:40px;height:40px;stroke-width:1.5}

.chip{
  font-size:12px;color:var(--t2);background:var(--bg-inset);border:1px solid var(--bd);
  padding:3px 9px;border-radius:999px;font-family:var(--mono);white-space:nowrap
}
.chip.warn{color:var(--warn);border-color:var(--warn)}
.body{flex:1;overflow-y:auto;overscroll-behavior:contain;padding:22px 18px 10px}
.wrap{max-width:820px;margin:0 auto}

/* ---------------- 消息 ---------------- */
.turn{margin-bottom:22px}
.turn .who{font-size:12px;color:var(--t3);margin-bottom:6px;display:flex;align-items:center;gap:7px}
/* 我发出的消息：整体靠右 */
.turn.me{display:flex;flex-direction:column;align-items:flex-end}
.turn.me .who{justify-content:flex-end;flex-direction:row-reverse}
.bub{font-size:var(--ui-font,16px);line-height:var(--ui-line,23px);word-break:break-word}
.turn.me .bub{background:var(--bg-inset);border:1px solid var(--bd);border-radius:var(--r);padding:10px 14px;display:inline-block;max-width:88%}
.bub p{margin:0 0 var(--ui-para,6px)}.bub>*:last-child{margin-bottom:0}
.bub h2{font-size:17px;font-weight:500;margin:var(--ui-hgap,13px) 0 5px}
.bub h3{font-size:15px;font-weight:500;margin:var(--ui-hgap,13px) 0 3px;color:var(--t2)}
.bub ul{margin:var(--ui-para,6px) 0;padding-left:22px}
.bub li{margin:var(--ui-li,2px) 0}
.bub code{background:var(--code);padding:1px 5px;border-radius:5px;font:calc(var(--ui-code,13px) + .5px) var(--mono);border:1px solid var(--bd)}
.bub pre{background:var(--code);border:1px solid var(--bd);padding:12px 14px;border-radius:var(--r);
  overflow-x:auto;font:var(--ui-code,13px)/1.6 var(--mono);margin:8px 0}
.cbx{position:relative;margin:10px 0}
.cbx pre{margin:0}
.cbx .cbc{position:absolute;right:8px;top:8px;border:1px solid var(--bd);background:var(--bg);
  color:var(--t3);font-size:11px;line-height:1;padding:4px 9px;border-radius:6px;cursor:pointer;
  opacity:.5;transition:opacity .12s}
.cbx:hover .cbc,.cbx .cbc:active{opacity:1}
.cbx .cbc.ok{color:var(--brand);border-color:var(--brand);opacity:1}
.cbx .lg{position:absolute;left:9px;top:-8px;font:10.5px/1 var(--mono);color:var(--t4);
  background:var(--bg);padding:1px 5px}
.bub table{border-collapse:collapse;margin:8px 0;font-size:14px;display:block;overflow-x:auto}
.bub th,.bub td{border:1px solid var(--bd);padding:6px 11px;text-align:left}
.bub th{background:var(--bg-inset);font-weight:500}
.bub ol{margin:6px 0;padding-left:24px}
.bub ol li{margin:3px 0}
.bub hr{border:none;border-top:1px solid var(--bd);margin:14px 0}
.bub blockquote{margin:8px 0;padding:6px 12px;border-left:3px solid var(--brand);
  background:var(--bg-side);border-radius:0 var(--r) var(--r) 0;color:var(--t2)}
.bub a{color:var(--brand);text-decoration:none;border-bottom:1px solid var(--brand-soft)}
.bub img{max-width:100%;border-radius:var(--r);border:1px solid var(--bd);margin:6px 0}
/* 气泡里的附件（2026-10-01）：图片缩略图 / 文件徽标 */
.uatts{display:flex;flex-wrap:wrap;gap:8px;margin:2px 0 6px}
.bub .uthumb{display:block;line-height:0;border:1px solid var(--bd);border-bottom:1px solid var(--bd);border-radius:var(--r);overflow:hidden}
.bub .uthumb img{display:block;width:auto;height:auto;max-width:190px;max-height:190px;margin:0;border:none;border-radius:0}
.bub .uthumb.bad{width:110px;height:74px;background:var(--bg-inset)}
.bub .ufile{display:flex;align-items:center;gap:8px;padding:5px 11px 5px 5px;border:1px solid var(--bd);border-bottom:1px solid var(--bd);border-radius:var(--r);background:var(--bg2);text-decoration:none;max-width:100%}
.bub .ufile .ubadge{flex:0 0 auto;min-width:40px;text-align:center;font:500 11px var(--mono);color:var(--t2);background:var(--bg-inset);border:1px solid var(--bd);border-radius:5px;padding:5px 4px}
.bub .ufile .uname{font-size:13px;color:var(--t2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:230px}
.bub .utext{white-space:pre-wrap}
.bub svg{display:block;width:100%;height:auto;max-width:100%;margin:8px 0;border-radius:var(--r);background:transparent}
.bub .svgwait{display:block;color:var(--t3);font-size:13px;padding:10px 12px;border:1px dashed var(--bd);border-radius:var(--r);margin:8px 0;text-align:center}
.bub mark{background:#fff3bf;color:inherit;padding:0 3px;border-radius:3px}
.bub kbd{font:12px var(--mono);border:1px solid var(--bd);border-bottom-width:2px;
  border-radius:5px;padding:1px 5px;background:var(--bg-inset)}
.bub details{margin:8px 0;border:1px solid var(--bd);border-radius:var(--r);
  background:var(--bg-side);padding:8px 12px}
.bub summary{cursor:pointer;font-weight:500;color:var(--t2)}
.bub sub,.bub sup{font-size:.72em}
.bub .card{margin:10px 0;border:1px solid var(--bd);border-radius:var(--r);
  overflow:hidden;background:var(--bg)}
.bub .card-h{padding:8px 12px;font-size:14px;font-weight:500;background:var(--bg-inset);
  border-bottom:1px solid var(--bd)}
.bub .card-b{padding:10px 12px}
.bub .card-b>*:last-child{margin-bottom:0}
.bub .card.tip{border-color:rgba(48,164,108,.45)}
.bub .card.warn{border-color:rgba(214,158,46,.5)}
.bub .card.danger{border-color:rgba(214,69,69,.45)}
.cursor{display:inline-block;width:2px;height:1.05em;background:var(--brand);vertical-align:-2px;animation:blink 1s steps(2) infinite;margin-left:1px}
@keyframes blink{50%{opacity:0}}
.think{color:var(--t3);font-size:14px;display:flex;align-items:center;gap:8px}
/* 思考过程：流式蹦字、可点开/收起（像 codex 的 reasoning 面板） */
.thinkbox{margin:0 0 9px;border:1px solid var(--bd);border-radius:var(--r);background:var(--bg-side);overflow:hidden}
.thinkhd{display:flex;align-items:center;gap:7px;padding:6px 10px;font-size:12.5px;color:var(--t3);
  cursor:pointer;user-select:none}
.thinkhd:hover{background:var(--bg-hover)}
.thinkhd .arw{font-size:10px;color:var(--t4);transition:transform .18s}
.thinkbox.open .thinkhd .arw{transform:rotate(90deg)}
.thinkhd .cnt{color:var(--t4);font-size:11.5px;margin-left:auto;font-family:var(--mono)}
.thinkbody{display:none;padding:8px 11px;border-top:1px solid var(--bd);font-size:12.5px;
  line-height:1.7;color:var(--t3);white-space:pre-wrap;word-break:break-word;
  max-height:34vh;overflow-y:auto;font-family:var(--mono)}
.thinkbox.open .thinkbody{display:block}
.thinkbody .cur2{color:var(--brand);animation:blink 1s steps(2) infinite}
.thinkbox.done .thinkbody .cur2{display:none}
.think .el{color:var(--t4);font-size:12px;font-variant-numeric:tabular-nums;margin-left:2px}
.sp{width:12px;height:12px;border:2px solid var(--bd-strong);border-top-color:var(--brand);border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
/* 工具步骤：折叠成一张卡片 */
.steps{border:1px solid var(--bd);border-radius:var(--r);background:var(--bg-side);margin:0 0 14px;overflow:hidden}
.steps summary{padding:9px 13px;font-size:13.5px;color:var(--t2);display:flex;align-items:center;gap:8px;cursor:pointer}
.steps summary::-webkit-details-marker{display:none}
.steps summary .arw{transition:transform .18s;color:var(--t4);font-size:11px}
.steps[open] summary .arw{transform:rotate(90deg)}
.steps ol{list-style:none;margin:0;padding:6px 13px 10px;font-size:13px;color:var(--t2)}
.steps li{margin:5px 0}
.steps .hd{display:flex;align-items:baseline;gap:7px;line-height:1.5}
.steps .hd .g{flex:0 0 auto;width:12px;color:var(--t3);font-size:12px;text-align:left}
.steps .hd.err .g{color:var(--err)}
.steps .hd .v{flex:0 0 auto;color:var(--t1);white-space:nowrap}
.steps .hd .a{color:var(--t3);font-family:var(--mono);font-size:11.5px;min-width:0;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.steps .out{margin:3px 0 0 19px;padding:5px 8px;background:var(--code);border-radius:6px;
  font-family:var(--mono);font-size:11.5px;line-height:1.55;color:var(--t2);
  white-space:pre-wrap;word-break:break-word;max-height:5.4em;overflow:hidden}
.steps .out .e{color:var(--err)}
.steps li.more{color:var(--t4);font-size:12px;margin:3px 0 6px}
/* 正在执行的步骤（对照 codex 的实时输出）：转圈 + 边走边看的输出尾巴 */
.steps li.run .hd .g{color:var(--brand);animation:blink .9s infinite}
.steps li.run .hd .el{color:var(--t4);font-size:11px;font-family:var(--mono);margin-left:auto}
.steps .live{margin:4px 0 1px 19px;padding:5px 8px;background:var(--code);border-radius:6px;
  font-family:var(--mono);font-size:11.5px;line-height:1.55;color:var(--t2);
  white-space:pre-wrap;word-break:break-word;max-height:7.5em;overflow:hidden}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.25}}
/* 步骤展开详情：命令原文 / 参数 / 文件差异（对照 codex 的 patch 展示） */
.steps .hd{position:relative}
.steps .hd.clk{cursor:pointer}
.steps .hd .cz{flex:0 0 auto;margin-left:auto;color:var(--t4);font-size:10px;transition:transform .18s}
.steps li.stp.op .hd .cz{transform:rotate(180deg)}
.steps .hd .stat{flex:0 0 auto;font-family:var(--mono);font-size:11px;margin-left:6px}
.steps .hd .stat .a{color:var(--ok)}
.steps .hd .stat .d{color:var(--err)}
.steps .det{margin:5px 0 10px 19px;position:relative}
.steps .det .blk{margin:0 0 8px}
.steps .det .blk .bt{font-size:11px;color:var(--t4);margin:0 0 3px}
.steps .det .blk .bt .a{color:var(--ok)}
.steps .det .blk .bt .d{color:var(--err)}
.steps .det pre{margin:0;padding:6px 9px;background:var(--code);border-radius:6px;
  font-family:var(--mono);font-size:11.5px;line-height:1.6;color:var(--t2);
  white-space:pre-wrap;word-break:break-word;max-height:34vh;overflow:auto}
.steps .det pre.diff span{display:block;white-space:pre-wrap;word-break:break-word}
.steps .det pre.diff .add{color:var(--ok);background:rgba(63,185,80,.10)}
.steps .det pre.diff .del{color:var(--err);background:rgba(248,81,73,.10)}
.steps .det pre.diff .ctx{color:var(--t4)}
.steps .det pre.diff .hdr{color:var(--brand)}
.steps .det .cpbtn{position:absolute;top:-3px;right:0;border:1px solid var(--bd);background:var(--bg-side);
  color:var(--t3);font-size:11px;border-radius:5px;padding:1px 7px;cursor:pointer;z-index:1}
.steps .det .cpbtn:hover{border-color:var(--bd-strong);color:var(--t1)}
/* 更早的步骤：默认藏起来，点提示行可翻回来看（2026-10-01，以前是直接删掉） */
.steps li.oldst{display:none}
.steps ol.showall li.oldst{display:list-item}
.steps li.more{cursor:pointer}
.steps li.more:hover{color:var(--t2)}
.welcome{max-width:820px;margin:8vh auto 0;text-align:center}
.welcome h1{font-size:22px;font-weight:500;margin:0 0 8px}
.welcome p{color:var(--t3);font-size:14px;margin:0 0 24px}
.chips{display:flex;flex-wrap:wrap;gap:8px;justify-content:center}
.chips button{border:1px solid var(--bd);background:var(--bg-side);border-radius:var(--r);
  padding:11px 15px;font-size:14px;text-align:left;line-height:1.5;max-width:340px}
.chips button:hover{border-color:var(--bd-strong);background:var(--bg-hover)}

/* ---------------- 输入区 ---------------- */
.comp{flex:0 0 auto;padding:10px 18px calc(env(safe-area-inset-bottom) + 12px)}
.cwrap{max-width:820px;margin:0 auto;border:1px solid var(--bd-strong);border-radius:var(--r);
  background:var(--bg);display:flex;align-items:flex-end;gap:8px;padding:8px 8px 8px 14px}
.cwrap:focus-within{border-color:var(--brand)}
/* 斜杠命令面板（像 codex 那样输入 / 唤起） */
.cmdp{max-width:820px;margin:0 auto 8px;border:1px solid var(--bd-strong);border-radius:10px;
  background:var(--bg);box-shadow:0 10px 26px rgba(0,0,0,.12);overflow-y:auto;max-height:40vh}
.cmdp .hd{padding:6px 11px;font-size:11px;color:var(--t4);background:var(--bg-side);
  border-bottom:1px solid var(--bd);position:sticky;top:0}
.cmdp .ci{display:flex;align-items:baseline;gap:10px;padding:8px 11px;cursor:pointer}
.cmdp .ci.sel{background:var(--brand-soft)}
.cmdp .ci .c{flex:0 0 auto;min-width:104px;font-family:var(--mono);font-size:13px;color:var(--t1)}
.cmdp .ci.sel .c{color:var(--brand)}
.cmdp .ci .d{color:var(--t3);font-size:12.5px;line-height:1.5}
/* 会话内搜索 */
.findbar{flex:0 0 auto;display:flex;align-items:center;gap:7px;padding:7px 12px;
  border-bottom:1px solid var(--bd);background:var(--bg-side)}
.filep .ci .c{min-width:0;max-width:52%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.filep .ci .d{font-family:var(--mono);font-size:11.5px;overflow:hidden;
  text-overflow:ellipsis;white-space:nowrap}
.findbar input{flex:1 1 auto;min-width:0;border:1px solid var(--bd-strong);border-radius:7px;
  background:var(--bg);color:var(--t1);font-size:13.5px;padding:6px 9px}
.findbar .fi{flex:0 0 auto;color:var(--t3);font-size:12px;font-family:var(--mono);min-width:52px;text-align:right}
.findbar button{flex:0 0 auto;border:1px solid var(--bd);background:var(--bg);color:var(--t2);
  border-radius:6px;font-size:13px;padding:4px 9px;cursor:pointer}
.findbar button:hover{border-color:var(--bd-strong);color:var(--t1)}
.turn.findhit .bub{border-color:var(--brand);box-shadow:0 0 0 3px var(--brand-soft)}
.hint{max-width:820px;margin:6px auto 0;font-size:11.5px;color:var(--t4);text-align:center;
  -webkit-user-select:none;user-select:none;line-height:1.5}
.cwrap textarea{flex:1;resize:none;border:none;outline:none;background:none;font-size:16px;line-height:24px;
  padding:7px 0;max-height:32vh;min-height:38px}
.send{flex:0 0 auto;height:36px;min-width:36px;padding:0 14px;border-radius:var(--r-sm);background:var(--brand);color:#fff;font-size:14px}
.send:disabled{opacity:.4}
.send.stop{background:var(--err)}
.turn .who .act{margin-left:0;padding:1px 7px;border:1px solid var(--bd);border-radius:6px;background:transparent;color:var(--t3);font-size:11px;line-height:1.6;cursor:pointer}
.turn .who .act:active{background:var(--bg2)}
.turn .who .act.ok{color:var(--brand);border-color:var(--brand)}
/* 待发送队列：输入区里其它面板（斜杠命令 / 附件 / 提问 / 任务清单 / 输入框 / 上下文条）
   统一都是 max-width:820px + margin:0 auto，只有 .pq 漏了这一条，
   结果它按 .comp 的整个内宽铺开，比上下的卡片宽出一大截、左边缘也对不齐。
   补上同样的宽度约束即可，纯 CSS、不动任何结构。 */
.pq{max-width:820px;margin:0 auto 8px;display:flex;flex-direction:column;gap:6px;padding:8px 10px;border:1px dashed var(--bd);border-radius:10px;background:var(--bg2)}
.pq-h{display:flex;align-items:center;justify-content:space-between;font-size:12px;color:var(--t3)}
.pq-clr{border:none;background:transparent;color:var(--err);font-size:12px;cursor:pointer}
/* 任务清单（拆步骤、点标题展开） */
.tdl{max-width:820px;margin:0 auto 8px;border:1px solid var(--bd);border-radius:10px;
  background:var(--bg-side);overflow:hidden}
.tdl-h{display:flex;align-items:center;gap:8px;padding:7px 10px;font-size:13px;color:var(--t2);
  cursor:pointer;-webkit-user-select:none;user-select:none}
.tdl-ar{color:var(--t3);font-size:11px;width:9px;transition:transform .15s}
.tdl.open .tdl-ar{transform:rotate(90deg)}
.tdl-p{color:var(--t3);font-size:12px;font-variant-numeric:tabular-nums}
.tdl-bar{flex:1;height:4px;max-width:150px;margin-left:auto;border-radius:2px;
  background:var(--bg-active);overflow:hidden}
.tdl-bar i{display:block;height:100%;width:0;background:var(--brand);transition:width .25s}
.tdl-x{border:none;background:transparent;color:var(--t3);font-size:16px;line-height:1;
  cursor:pointer;padding:0 2px}
.tdl-b{display:none;padding:1px 10px 9px}
.tdl.open .tdl-b{display:block}
.tdl-i{display:flex;gap:7px;align-items:flex-start;padding:3px 0;font-size:13px;color:var(--t2);line-height:1.55}
.tdl-i .m{flex:0 0 auto;width:15px;color:var(--t4);font-size:12px}
.tdl-i .t{flex:1;min-width:0;word-break:break-word}
.tdl-i.ok{color:var(--t3)}
.tdl-i.ok .m{color:var(--ok)}
.tdl-i.ok .t{text-decoration:line-through}
.tdl-i.now{color:var(--t1)}
.tdl-i.now .m{color:var(--brand)}
.ask{max-width:820px;margin:0 auto 8px;border:1px solid var(--brand);border-radius:10px;
  background:var(--bg2,#f7f8fa);overflow:hidden}
.ask-h{display:flex;align-items:center;gap:7px;padding:8px 12px;font-size:13px;font-weight:600;
  color:var(--t1);border-bottom:1px solid var(--bd)}
.ask-h .dot{width:7px;height:7px;border-radius:50%;background:var(--brand);animation:pulse 1.4s infinite}
.ask-b{padding:10px 12px;display:flex;flex-direction:column;gap:12px;max-height:40vh;overflow:auto}
.ask-q{font-size:13.5px;color:var(--t1);line-height:1.6}
.ask-q .n{color:var(--t3);margin-right:5px}
.ask-opts{display:flex;flex-wrap:wrap;gap:6px;margin-top:7px}
.ask-o{border:1px solid var(--bd);border-radius:8px;padding:6px 11px;font-size:13px;
  color:var(--t2);cursor:pointer;background:transparent;transition:.12s;font-family:inherit}
.ask-o:hover{border-color:var(--brand);color:var(--t1)}
.ask-o.on{background:var(--brand);border-color:var(--brand);color:#fff;font-weight:600}
.ask-ta{width:100%;margin-top:7px;border:1px solid var(--bd);border-radius:8px;padding:6px 9px;
  font-size:13px;font-family:inherit;background:var(--bg);color:var(--t1);resize:vertical;min-height:32px}
.ask-f{display:flex;gap:9px;align-items:center;padding:0 12px 11px}
.ask-send{border:none;border-radius:8px;background:var(--brand);color:#fff;font-size:13px;
  font-weight:600;padding:7px 18px;cursor:pointer;font-family:inherit}
.ask-send:active{opacity:.8}
.ask-tip{font-size:12px;color:var(--t3)}
.caps{display:flex;flex-wrap:wrap;gap:6px;margin-top:9px}
.cap{border:1px solid var(--bd);border-radius:999px;padding:5px 13px;font-size:12.5px;
  color:var(--t2);cursor:pointer;background:transparent;font-family:inherit;
  transition:.12s;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.cap:hover{border-color:var(--brand);color:var(--t1);background:var(--brand-soft)}
.cap:active{transform:scale(.97)}
.cap.rnd{border-style:dashed;color:var(--brand);font-weight:600}
.pq-item{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--t2)}
.pq-item .pq-t{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pq-item .pq-x{border:none;background:transparent;color:var(--t3);font-size:15px;line-height:1;cursor:pointer;padding:0 2px}
.hint{display:none}
/* 输入框下方：上下文占用条 */
.tbar{max-width:820px;margin:7px auto 0;display:flex;align-items:center;gap:9px;font-size:11.5px;color:var(--t4)}
.tbar .bar{flex:1;height:4px;border-radius:2px;background:var(--bg-inset);overflow:hidden}
.tbar .bar i{display:block;height:100%;width:0;background:var(--brand);transition:width .3s}
.tbar .pct{min-width:36px;text-align:right;font-variant-numeric:tabular-nums}
/* 图片按钮 + 附件预览 */
.tbtn{flex:0 0 auto;width:36px;height:36px;border-radius:var(--r-sm);border:1px solid var(--bd);
  background:transparent;color:var(--t2);font-size:16px;line-height:1;cursor:pointer;padding:0}
.tbtn:active{background:var(--bg2)}
.chip.on{border-color:var(--brand);color:var(--brand)}
#imgbtn{display:flex;align-items:center;justify-content:center;font-size:24px;font-weight:300;
  line-height:1;padding-bottom:2px}
.att{max-width:820px;margin:0 auto 8px;display:flex;align-items:center;gap:9px;padding:7px 9px;
  border:1px solid var(--bd);border-radius:10px;background:var(--bg2);font-size:13px;color:var(--t2)}
.att img{width:40px;height:40px;object-fit:cover;border-radius:6px;flex:0 0 auto}
.att .ic{width:40px;height:40px;flex:0 0 auto;display:flex;align-items:center;justify-content:center;
  border-radius:6px;background:var(--bg2);font-size:20px}
.att .n{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.att .x{border:none;background:transparent;color:var(--t3);font-size:16px;line-height:1;cursor:pointer;padding:0 2px}

/* ---------------- 面板（设置 / 控制台） ---------------- */
.sheet{position:fixed;inset:0;background:rgba(0,0,0,.38);z-index:60;display:flex;justify-content:center;align-items:flex-start;padding:16px;overflow-y:auto}
/* 卡片内容多时会比屏幕还高（控制台 8 页尤其明显）。此时 .card 上的 margin:auto
   会在 flex 容器里把卡片垂直居中，导致上下两头一起溢出、上半截还滚不回去 ——
   表现就是"卡片下面那些内容全丢了"。这里给卡片限高，把滚动交给内容区自己。 */
/* 卡片内容多时会比屏幕还高（控制台 8 页尤其明显）。此时 .card 上的 margin:auto
   会在 flex 容器里把卡片垂直居中，导致上下两头一起溢出、上半截还滚不回去 ——
   表现就是"卡片下面那些内容全丢了"。这里给卡片限高，把滚动交给内容区自己。
   限高用 dvh（动态视口高）：100vh 在移动浏览器里含工具栏占位，比实际可视区大，
   实测会多出 60px 让底部保存按钮露不出来；dvh 跟随真实可视高度。
   余量给到 48px 而不是 32px：.sheet 自身有 16px×2 的 padding，若卡片刚好占满
   (100dvh-32) 就与外层严丝合缝，一点误差就让 .sheet 也滚起来 —— 外层一滚，
   本该固定的标签栏就被顶上去了，表现为"内容顶到了标签文字"（工具页内容最长，
   最先触发）。留出富余，外层就永远不滚。 */
body .sheet .card{max-height:calc(100vh - 48px);max-height:calc(100dvh - 48px);
  display:flex;flex-direction:column;margin:0 auto}
body .sheet .card-b{overflow-y:auto;flex:1 1 auto;min-height:0;overscroll-behavior:contain;-webkit-overflow-scrolling:touch}
/* 控制台卡片单独放宽（2026-10-01）：标签页涨到 9 个后，600px 装不下整条标签栏
   （标签本身约 596px + 容器左右 padding 24px = 620px），末位标签被挤出可视区。
   只放宽控制台，其他卡片（如技能详情）保持 600px 的舒适阅读宽度。 */
.sheet#ctl .card{max-width:720px}
/* 控制台卡片定高（2026-10-01）：上面那条把 max-height 写成了「最大」高度，卡片实际高度
   是由内容撑的 —— 而 9 个标签页内容长短差得极远（「更新记录」84px、「工具」531px）。
   表现：每切一次标签卡片就明显变高变矮，底部那条常驻操作条（立即压缩/保存）跟着上下乱跳，
   实测同一个「保存」按钮在两张标签之间位移达 447px —— 手指按下去的瞬间它在上面，
   抬起来时已经跑到下面去了，很容易按空或按到别的标签内容上。
   这里让控制台卡片直接占满可用高度（min-height 顶到与 max-height 同一个值），
   高度就固定下来，操作条始终钉在同一个位置；内容短时下方留白，内容长的照旧内部滚动。
   只作用于 #ctl，其他卡片（技能详情等）依旧是跟着内容自适应的高度。 */
.sheet#ctl .card{height:calc(100vh - 48px);height:calc(100dvh - 48px)}
.card{background:var(--bg);border:1px solid var(--bd);border-radius:16px;width:100%;max-width:600px;margin:auto}
.card-h{display:flex;align-items:center;gap:10px;padding:16px 18px;border-bottom:1px solid var(--bd);position:sticky;top:0;background:var(--bg);border-radius:16px 16px 0 0;z-index:1}
.card-h b{font-size:17px;font-weight:500;flex:1}
.card-h button{font-size:22px;color:var(--t3);line-height:1;padding:0 4px}
.card-b{padding:4px 18px 18px}
.row{display:flex;align-items:center;gap:14px;padding:14px 0;border-bottom:1px solid var(--bd)}
.row:last-child{border-bottom:none}
.row .lb{flex:0 0 112px;font-size:14px;color:var(--t2)}
.row .ctl{flex:1;min-width:0}
.row .ctl input[type=text],.row .ctl input[type=password],.row .ctl input[type=number],.row .ctl select,.row .ctl textarea{
  width:100%;border:1px solid var(--bd);border-radius:var(--r-sm);background:var(--bg-side);
  padding:9px 11px;font-size:14px;outline:none}
.row .ctl textarea{resize:vertical;line-height:1.6;font-family:inherit}
.row .ctl input:focus,.row .ctl select:focus,.row .ctl textarea:focus{border-color:var(--brand)}
.row .note{font-size:11.5px;color:var(--t4);margin-top:6px;line-height:1.6}
/* 开关外观统一由上面的 body .sw 负责（51×31，白圆点用背景图 + background-position 位移）。
   这里原本还叠了一套 44×26 + ::after 的写法，两套打架：关掉时两个圆点碰巧重叠看不出问题，
   打开时 .sw:checked 的 background 简写（特异性 0,2,0）会把上面那套的 background-image 重置成
   none，A 的 27px 圆点消失，只剩 20px 且 top:3px 的 ::after —— 表现就是「圆圈变小又偏上」。
   整套删掉，只留 body .sw；flex 这条 body .sw 没写，保留。 */
.sw{flex:0 0 auto}
.sec-t{font-size:12px;color:var(--t4);padding:16px 0 2px;font-weight:500}
.btn{border:1px solid var(--bd);background:var(--bg-side);border-radius:var(--r-sm);padding:9px 15px;font-size:13.5px}
.btn:hover{background:var(--bg-hover)}
.btn.pri{background:var(--brand);color:#fff;border-color:var(--brand)}
.btn:disabled{opacity:.5}
.mini{font-size:11.5px;color:var(--t4);margin-top:8px;line-height:1.6}
.log{margin-top:8px;font-size:11.5px;color:var(--t2);line-height:1.7;max-height:170px;overflow-y:auto;
  border:1px solid var(--bd);border-radius:var(--r-sm);padding:8px 10px;background:var(--bg-side)}
.log div{padding:3px 0;border-bottom:1px dashed var(--bd)}
.log div:last-child{border-bottom:none}
.log.chg{max-height:280px;font-size:12.5px}
.log.chg .ci{margin:4px 0;padding:6px 9px;border-radius:var(--r-sm)}
.log.chg .ci div{border-bottom:none;padding:0}
.log.chg .m{color:var(--t4);font-size:12px}
.log.chg .s{color:var(--t2)}
.log.chg .v{color:var(--brand)}
.log.chg .ci.evo{background:var(--brand-soft);border-left:3px solid var(--brand)}
.log.chg .ci.evo .s{color:var(--t1);font-weight:600}
.log.chg .ci.evo .k{color:var(--brand);font-weight:600}
.bar{height:6px;border-radius:3px;background:var(--bg-inset);overflow:hidden;margin-top:8px}
.bar i{display:block;height:100%;width:0;background:var(--brand);transition:width .3s}

/* ---------------- 提示条 ---------------- */
.toast{position:fixed;left:50%;bottom:calc(env(safe-area-inset-bottom) + 84px);transform:translateX(-50%);
  background:var(--t1);color:var(--bg);padding:10px 16px;border-radius:var(--r-sm);font-size:13.5px;
  max-width:80vw;z-index:80;opacity:0;transition:opacity .2s;pointer-events:none}
.toast.on{opacity:1}

@media (max-width: 820px){
  .gutter{display:none}
  .side{position:fixed;inset:0 auto 0 0;z-index:70;transform:translateX(-100%);transition:transform .22s;box-shadow:0 0 40px rgba(0,0,0,.18);width:294px}
  .side.open{transform:none}
  .top .hamb{display:block}
  .body,.comp{padding-left:14px;padding-right:14px}
  .row .lb{flex-basis:84px}
  #findbtn{padding:2px 7px}
  #findbtn .t{display:none}
}
</style>
</head>
<body>
<!-- 图标库：内联 SVG sprite（线性风格，跟随文字颜色） -->
<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
<symbol id="i-menu" viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h16"/></symbol>
<symbol id="i-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/></symbol>
<symbol id="i-speaker" viewBox="0 0 24 24"><path d="M4 9.5h3.5L12 5.5v13L7.5 14.5H4z"/><path d="M16.5 9.5l4 5M20.5 9.5l-4 5"/></symbol>
<symbol id="i-speaker-wave" viewBox="0 0 24 24"><path d="M4 9.5h3.5L12 5.5v13L7.5 14.5H4z"/><path d="M15.5 9.2a4 4 0 0 1 0 5.6"/><path d="M18.3 6.6a7.6 7.6 0 0 1 0 10.8"/></symbol>
<symbol id="i-phone" viewBox="0 0 24 24"><path d="M6.8 4h3l1.6 4.1-2.1 1.5a11.5 11.5 0 0 0 5.1 5.1l1.5-2.1L20 14.2v3a2.1 2.1 0 0 1-2.3 2.1A16.3 16.3 0 0 1 4.7 6.3 2.1 2.1 0 0 1 6.8 4z"/></symbol>
<symbol id="i-mic" viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0"/><path d="M12 18v3"/></symbol>
<symbol id="i-mic-fill" viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3" fill="currentColor" stroke="none"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0"/><path d="M12 18v3"/></symbol>
<symbol id="i-image" viewBox="0 0 24 24"><rect x="3.5" y="5" width="17" height="14" rx="2.5"/><circle cx="9" cy="10" r="1.6"/><path d="M4.5 16.5l4.5-4 3 2.6 3.5-3.6 4 4.5"/></symbol>
<symbol id="i-xmark" viewBox="0 0 24 24"><path d="M7 7l10 10M17 7L7 17"/></symbol>
<symbol id="i-check" viewBox="0 0 24 24"><path d="M5.5 12.5l4.5 4.5 8.5-9.5"/></symbol>
<symbol id="i-refresh" viewBox="0 0 24 24"><path d="M20.5 12a8.5 8.5 0 1 1-2.7-6.2"/><path d="M20.5 5v4.5H16"/></symbol>
<symbol id="i-sparkles" viewBox="0 0 24 24"><path d="M11 4l1.5 4.1L16.6 9.6l-4.1 1.5L11 15.2 9.5 11.1 5.4 9.6l4.1-1.5z"/><path d="M18 15l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/></symbol>
<symbol id="i-bubble" viewBox="0 0 24 24"><path d="M20 12.2a6.8 6.8 0 0 1-6.8 6.8H10l-4.5 2.6v-3.3A6.8 6.8 0 0 1 10 6h3.2A6.8 6.8 0 0 1 20 12.2z"/></symbol>
<symbol id="i-stop" viewBox="0 0 24 24"><rect x="7" y="7" width="10" height="10" rx="2.6" fill="currentColor" stroke="none"/></symbol>
<symbol id="i-send" viewBox="0 0 24 24"><path d="M4 12l16-7-7 16-2.2-6.8z"/></symbol>
</defs></svg>
<div class="app">
  <aside class="side" id="side">
    <div class="side-h"><span class="dot" id="dot"></span><b id="aname">Sidekick</b><span class="m" id="model"></span>
      <button class="side-x" id="sidex" type="button" title="收起侧栏（双击分界线也能切换）">‹</button></div>
    <button class="newbtn" id="newchat"><span class="plus">＋</span><span data-t="sess_new_btn">新对话</span></button>
    <div class="list" id="list"></div>
    <div class="side-f">
      <button id="openctl" data-t="nav_console">控制台</button>
      <button id="openconf" data-t="nav_settings">设置</button>
    </div>
  </aside>
  <div class="gutter" id="gutter" title="拖动调整宽度 · 双击收起/展开"></div>
  <button class="sidefab" id="sidefab" type="button" title="展开对话列表">›</button>

  <main class="main">
    <div class="top">
      <button class="hamb" id="hamb"><svg class="ic" aria-hidden="true"><use href="#i-menu"/></svg></button>
      <span class="t1" id="topname">Sidekick</span>
      <span class="chip" id="topmodel"></span>
      <span class="chip" id="topver" title="查看更新记录" data-t-title="nav_updates" style="cursor:pointer"></span>
      <span class="chip warn" id="evo" style="display:none">进化中…</span>
      <button class="chip" id="findbtn" type="button" title="在对话里搜索（Ctrl+F）" data-t-title="chat_find"><svg class="ic" aria-hidden="true"><use href="#i-search"/></svg><span class="t" data-t="chat_search"> 搜索</span></button>
    </div>
    <div class="findbar" id="findbar" style="display:none">
      <input id="findq" type="text" placeholder="在对话里搜索…" data-t-ph="chat_find_ph">
      <span class="fi" id="findinfo">0/0</span>
      <button id="findprev" type="button" title="上一个（Shift+Enter）" data-t-title="chat_find_prev">↑</button>
      <button id="findnext" type="button" title="下一个（Enter）" data-t-title="chat_find_next">↓</button>
      <button id="findx" type="button" title="关闭（Esc）" data-t-title="chat_find_close">×</button>
    </div>
    <div class="body" id="body"><div id="root"></div></div>
    <div class="comp">
      <div class="ask" id="ask" style="display:none"></div>
      <div class="tdl" id="tdl" style="display:none">
        <div class="tdl-h" id="tdl-h"><span class="tdl-ar" id="tdl-ar">▸</span><b data-t="todo_title">任务清单</b>
          <span class="tdl-p" id="tdl-p"></span><span class="tdl-bar"><i id="tdl-i"></i></span>
          <button class="tdl-x" id="tdl-x" type="button" title="清空清单" data-t-title="todo_clear">×</button></div>
        <div class="tdl-b" id="tdl-b"></div>
      </div>
      <div class="cmdp" id="cmdp" style="display:none"></div>
      <div class="cmdp filep" id="filep" style="display:none"></div>
      <div class="pq" id="pq" style="display:none"></div>
      <div class="att" id="att" style="display:none"></div>
      <div class="cwrap">
        <button class="tbtn" id="imgbtn" type="button" title="发送图片或文件" data-t-title="in_attach">+</button>
        <textarea id="input" rows="1" placeholder="说点什么…" data-t-ph="in_placeholder"></textarea>
        <button class="send" id="send" data-t="in_send">发送</button>
      </div>
      <div class="tbar">
        <svg class="cring" viewBox="0 0 32 32" aria-hidden="true">
          <circle class="crbg" cx="16" cy="16" r="13"/>
          <circle class="crfg" id="ctxring" cx="16" cy="16" r="13"/>
        </svg>
        <span class="ctxt" id="ctxtxt">上下文 0 / 0 token · 0%</span>
        <span class="cpct" id="ctxpct">0%</span>
        <i id="ctxfill" style="display:none"></i>
      </div>
      <style>
      .tbar{max-width:820px;margin:7px auto 0;display:flex;align-items:center;gap:8px;font-size:11.5px;color:var(--t4)}
      .tbar .cring{width:18px;height:18px;flex:0 0 auto;transform:rotate(-90deg)}
      .tbar .cring circle{fill:none;stroke-width:3.5}
      .tbar .crbg{stroke:var(--bd)}
      .tbar .crfg{stroke:var(--brand);stroke-linecap:round;
        stroke-dasharray:81.68;stroke-dashoffset:81.68;transition:stroke-dashoffset .3s,stroke .3s}
      .tbar .ctxt{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      .tbar .cpct{flex:0 0 auto;min-width:34px;text-align:right;font-weight:600;
        color:var(--t3);font-variant-numeric:tabular-nums}
      </style>
      <div class="hint" id="hint"></div>
      <input type="file" id="filein" style="display:none">
    </div>
  </main>
</div>

<!-- 控制台：名称 / 自我进化 / 记忆 / 上下文 -->
<div class="sheet" id="ctl" style="display:none">
  <div class="card">
    <div class="card-h"><b data-t="nav_console">控制台</b><button data-close="ctl">×</button></div>
    <style>
    /* 控制台重排（2026-09-29）：顶部标签栏 + 进化页仪表盘（方案 A） */
    .ctabs{display:flex;align-items:flex-end;gap:2px;padding:6px 12px 0;border-bottom:1px solid var(--bd);overflow-x:auto;scrollbar-width:none;
      flex:0 0 auto;height:38px;box-sizing:border-box}
    .ctabs::-webkit-scrollbar{display:none}
    .ctabs .tb{flex:0 0 auto;height:31px;box-sizing:border-box;border:none;background:transparent;color:var(--t3);font-family:inherit;font-size:13.5px;padding:9px 12px 0;border-bottom:2px solid transparent;cursor:pointer;white-space:nowrap}
    .ctabs .tb.on{color:var(--brand);border-bottom-color:var(--brand);font-weight:600}
    .ctabs .tb:hover{color:var(--t1)}
    .pane{display:none}.pane.on{display:block}
    .pane-meta{font-size:12px;color:var(--t4);padding:10px 0 0;font-variant-numeric:tabular-nums}
    .st-row{display:flex;align-items:center;gap:9px;padding:16px 0 8px}
    .st-dot{width:8px;height:8px;border-radius:50%;background:var(--t4);flex:0 0 auto}
    .st-dot.on{background:var(--ok,#2ecc71)}
    .st-txt{font-size:14.5px;color:var(--t1);font-weight:600}
    .pbar{height:6px;border-radius:3px;background:var(--bg-inset);overflow:hidden;margin:6px 0}
    .pbar i{display:block;height:100%;width:0;background:var(--brand);transition:width .3s}
    .pbar i.full{background:var(--ok,#2ecc71)}
    .pmeta{font-size:12px;color:var(--t4);font-variant-numeric:tabular-nums}
    .seg{display:inline-flex;border:1px solid var(--bd);border-radius:var(--r-sm);overflow:hidden}
    .seg button{border:none;border-right:1px solid var(--bd);background:transparent;color:var(--t2);font-family:inherit;font-size:13px;padding:8px 13px;cursor:pointer}
    .seg button:last-child{border-right:none}
    .seg button.on{background:var(--brand);color:#fff;font-weight:600}
    .abtn{border:none;border-radius:var(--r-sm);background:var(--brand);color:#fff;font-family:inherit;font-size:13.5px;font-weight:600;padding:10px 18px;cursor:pointer}
    .abtn:active{opacity:.85}
    /* 外观页：滑块 + 数字联动 */
    /* 注意：.row .ctl input[type=number] 那条通用规则权重是 (0,3,3)，且 width:100%。
       下面这条必须带上 .row/.ctl 两级类名（权重 (0,4,4)）才能真正压过它，
       否则数字框会撑满整行、把左边的滑块挤成一条缝。 */
    .ui-sl{display:flex;align-items:center;gap:10px;width:100%}
    .row .ctl .ui-sl input[type=range]{flex:1 1 auto;min-width:0;width:auto;height:22px;
      margin:0;padding:0;border:none;background:none;cursor:pointer;accent-color:var(--brand)}
    .row .ctl .ui-sl input[type=number]{flex:0 0 auto;width:58px;min-width:58px;max-width:58px;
      box-sizing:border-box;padding:6px 4px;text-align:center;font-size:13.5px}
    .ui-sl .u{flex:0 0 auto;font-size:12px;color:var(--t4)}
    .act-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:16px 0 6px;border-top:1px solid var(--bd);margin-top:12px}
    .hrow{display:flex;align-items:center;gap:6px;font-size:13.5px;color:var(--t2);padding:16px 0 0;border-top:1px solid var(--bd);margin-top:14px}
    .help{margin-left:auto;position:relative}
    .help>summary{list-style:none;cursor:pointer;width:18px;height:18px;line-height:16px;text-align:center;border:1px solid var(--bd);border-radius:50%;color:var(--t4);font-size:11px;font-weight:600;display:inline-block;-webkit-user-select:none;user-select:none}
    .help>summary::-webkit-details-marker{display:none}
    .help[open]>summary{border-color:var(--brand);color:var(--brand)}
    .help .hb{position:absolute;right:0;top:24px;z-index:9;width:min(290px,76vw);padding:10px 12px;border:1px solid var(--bd);border-radius:var(--r-sm);background:var(--bg);color:var(--t2);font-size:12px;line-height:1.7;box-shadow:0 8px 24px rgba(0,0,0,.14);text-align:left;font-weight:400;white-space:normal}
    </style>
    <div class="ctabs" id="ctabs"></div>
    <script>
    /* 控制台重排：原 HTML 一字未动，结构在加载后由本脚本重建 —— 所有 id 与数据通道保持不变。
       ① 五个 .sec-t 小节切成真标签页（记住上次选的页）；
       ② 「进化」页改成仪表盘：状态行 + 进度条 + 胶囊主体 + 动作行；
       ③ 重复的三处说明收进右上角「?」，默认收起。 */
    (function(){
      function $(s){ return document.querySelector(s); }
      function go(){
        var ctl = document.getElementById('ctl');
        if(!ctl || ctl.dataset.tabs) return;
        var body = ctl.querySelector('.card-b');
        if(!body) return;
        var secs = [];
        Array.prototype.slice.call(body.children).forEach(function(n){
          if(n.classList && n.classList.contains('sec-t')) secs.push(n);
        });
        if(!secs.length) return;
        ctl.dataset.tabs = '1';

        /* ---------- ① 切分小节 → pane ---------- */
        var panes = [];
        secs.forEach(function(st){
          /* 标题优先走多语言键：data-t 键 → T 取词 → 兜底原文字。
             data-t 扫描会把 .sec-t 文本换成当前语言，但本脚本可能跑在它之前，
             所以直接认 data-t 键最稳，不依赖执行先后。 */
          var nm = (st.getAttribute && st.getAttribute('data-t') && T[st.getAttribute('data-t')] && T[st.getAttribute('data-t')] !== st.getAttribute('data-t'))
                 ? T[st.getAttribute('data-t')]
                 : ((st.firstChild && st.firstChild.nodeType === 3 ? st.firstChild.textContent : st.textContent) || '');
          nm = nm.replace(/[\s\u3000]+/g, ' ').trim() || T.nav_settings;
          var pane = document.createElement('div');
          pane.className = 'pane'; pane.dataset.name = nm;
          body.insertBefore(pane, st);
          var meta = null;
          Array.prototype.slice.call(st.children).forEach(function(ch){
            if(!meta){ meta = document.createElement('div'); meta.className = 'pane-meta'; pane.appendChild(meta); }
            meta.appendChild(ch);          /* 旧标题里的小标记（版本号/字数）搬进页首，id 保留 */
          });
          while(st.nextSibling && !(st.nextSibling.nodeType === 1 && st.nextSibling.classList && st.nextSibling.classList.contains('sec-t'))){
            pane.appendChild(st.nextSibling);
          }
          st.remove();
          panes.push({name: nm, el: pane});
        });
        panes[0].el.classList.add('on');

        /* ---------- ② 标签栏 ---------- */
        var tabs = document.getElementById('ctabs');
        panes.forEach(function(p, i){
          var b = document.createElement('button');
          b.type = 'button'; b.className = 'tb' + (i ? '' : ' on'); b.textContent = p.name;
          b.onclick = function(){
            Array.prototype.slice.call(tabs.children).forEach(function(x){ x.classList.toggle('on', x === b); });
            panes.forEach(function(q){ q.el.classList.toggle('on', q.el === p.el); });
            try{ localStorage.setItem('ctlTab', String(i)); }catch(e){}
          };
          tabs.appendChild(b);
        });
        var keepTab = 0;
        try{ keepTab = parseInt(localStorage.getItem('ctlTab') || '0', 10) || 0; }catch(e){}
        if(keepTab > 0 && tabs.children[keepTab]) tabs.children[keepTab].click();

        /* ---------- ③ 进化页：仪表盘（方案 A） ---------- */
        var ev = null;
        panes.forEach(function(p){ if(!ev && /进化/.test(p.name)) ev = p.el; });
        if(!ev) return;

        var keep = document.createElement('div');       /* 原内容整体收起：元素都还在 DOM 里，只是不显示 */
        keep.id = 'evo-keep'; keep.style.display = 'none';
        Array.prototype.slice.call(ev.children).forEach(function(c){ keep.appendChild(c); });
        ev.appendChild(keep);
        function take(id){ var e = keep.querySelector('#' + id); if(e && e.parentNode) e.parentNode.removeChild(e); return e; }
        var elSw = take('p-enable'), elCap = take('p-caps'), elTip = take('p-caps-tip'),
            elGo = take('p-evolve');
        /* p-perday 只取引用、绝不能 take 出来：take 会把它从 DOM 摘掉，而它必须留在 DOM 里，
           否则 fillPanel 里 $('#p-perday').value 取到 null 直接抛错，函数从那一行中断，
           后面的记忆 / 更新记录 / 上下文全都填不上（界面表现：只有第一页有内容）。
           它留在 #evo-keep（display:none）里，界面上不显示，但保存逻辑照样读得到。 */
        var elPer = keep.querySelector('#p-perday');

        var head = document.createElement('div'); head.className = 'st-row';
        var dot = document.createElement('span'); dot.className = 'st-dot'; dot.id = 'evo-dot';
        var txt = document.createElement('span'); txt.className = 'st-txt'; txt.textContent = T.evo_auto;
        var stx = document.createElement('span'); stx.className = 'pmeta'; stx.id = 'evo-state';
        var sp = document.createElement('span'); sp.style.marginLeft = 'auto'; sp.style.display = 'flex';
        if(elSw){ var swp = document.createElement('label'); swp.className = 'sw-wrap'; swp.appendChild(elSw); sp.appendChild(swp); }
        head.appendChild(dot); head.appendChild(txt); head.appendChild(stx); head.appendChild(sp);
        ev.appendChild(head);

        var bar = document.createElement('div'); bar.className = 'pbar';
        var bi = document.createElement('i'); bi.id = 'evo-bar'; bar.appendChild(bi); ev.appendChild(bar);
        var meta = document.createElement('div'); meta.className = 'pmeta'; meta.id = 'evo-meta'; ev.appendChild(meta);

        var h = document.createElement('div'); h.className = 'hrow';
        var ht = document.createElement('span'); ht.textContent = T.evo_dir;
        h.appendChild(ht);
        if(elTip){ elTip.style.marginLeft = '8px'; elTip.style.fontSize = '12px'; elTip.style.color = 'var(--t4)'; h.appendChild(elTip); }
        var det = document.createElement('details'); det.className = 'help';
        det.innerHTML = '<summary>?</summary><div class="hb">' + T.evo_dir_help + '</div>';
        h.appendChild(det);
        ev.appendChild(h);
        if(elCap) ev.appendChild(elCap);

        var act = document.createElement('div'); act.className = 'act-row';
        var sl = document.createElement('span'); sl.className = 'pmeta'; sl.textContent = T.evo_per;
        var seg = document.createElement('span'); seg.className = 'seg'; seg.id = 'evo-seg';
        [1, 2, 3, 4].forEach(function(v){
          var b = document.createElement('button'); b.type = 'button'; b.dataset.v = v; b.textContent = v + ' 项';
          b.onclick = function(){
            if(elPer) elPer.value = String(v);
            Array.prototype.slice.call(seg.children).forEach(function(x){ x.classList.toggle('on', x === b); });
          };
          seg.appendChild(b);
        });
        var sr = document.createElement('span'); sr.className = 'pmeta'; sr.textContent = T.evo_items;
        var filler = document.createElement('span'); filler.style.flex = '1';
        act.appendChild(sl); act.appendChild(seg); act.appendChild(sr); act.appendChild(filler);
        if(elGo){ elGo.className = 'abtn'; elGo.textContent = T.evo_btn_now; act.appendChild(elGo); }
        ev.appendChild(act);

        /* 进度/状态绘制：挂到 fillPanel 上（面板数据一到就同步） */
        function paintEvo(d){
          if(!d) return;
          var done = d.evolve_done || 0, tgt = d.evolve_target || 1;
          var b = document.getElementById('evo-bar');
          if(b){ b.style.width = (tgt ? Math.min(100, Math.round(done / tgt * 100)) : 0) + '%'; b.className = (done >= tgt ? 'full' : ''); }
          var dt = document.getElementById('evo-dot'); if(dt) dt.className = 'st-dot' + (d.evolve_enabled ? ' on' : '');
          var sx = document.getElementById('evo-state'); if(sx) sx.textContent = d.evolve_enabled ? '· 已开启' : '· 已关闭';
          var em = document.getElementById('evo-meta');
          if(em) em.textContent = T.evo_today_prefix + done + ' / ' + tgt + ' ' + T.evo_items + (d.evolve_date ? '（' + d.evolve_date + '）' : '');
          var sg = document.getElementById('evo-seg');
          if(sg){
            var v = d.evolve_per_day || 1;
            Array.prototype.slice.call(sg.children).forEach(function(x){ x.classList.toggle('on', (+x.dataset.v) === v); });
            if(!sg.dataset.set && elPer){ elPer.value = String(v); sg.dataset.set = '1'; }
          }
        }
        var fp = window.fillPanel;
        if(typeof fp === 'function'){
          window.fillPanel = function(d){ fp(d); try{ paintEvo(d); }catch(e){} };
        }
        if(elPer) paintEvo({evolve_per_day: parseInt(elPer.value, 10) || 1});
      }
      if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go);
      else go();
    })();

    /* ④ 说明收进「?」（2026-09-29 改版）：按卡片收拢，不再逐行挂。
       每个标签页（.pane）右上角一个「?」，把该页所有静态 .note 的说明合并进同一个气泡；
       设置页则在卡片标题栏右侧挂一个。点或长按弹气泡，点 × / 点空白 / Esc 关闭。
       #p-caps-tip（胶囊锁定反馈）与 #c-note（接口提示）是实时状态，保持可见不参与收拢。 */
    (function(){
      var st = document.createElement('style');
      st.textContent =
        '.pane{position:relative;padding-right:36px}' +
        '.q{display:inline-flex;align-items:center;justify-content:center;flex:0 0 auto;box-sizing:border-box;position:absolute;right:0;top:9px;width:22px;height:22px;padding:0;border:1px solid var(--bd);border-radius:50%;background:var(--bg);color:var(--t4);font-family:inherit;font-size:13px;font-weight:600;line-height:1;cursor:pointer;z-index:3}' +
        '.q:hover,.q.on{border-color:var(--brand);color:var(--brand);background:var(--brand-soft)}' +
        '.card-h .q{position:static;margin-left:auto;order:3}' +
        /* 气泡首段给右上角的 × 让位：× 是绝对定位浮着的，否则会盖住第一行右端的字 */
        '.qbox>p:first-of-type{padding-right:34px}' +
        /* 压缩时机那一行：动态提示（如「· 本对话专有（默认 70%）」）自己占剩余宽度并可省略，
           不许把右侧两个按钮挤出容器；按钮固定不被压缩。 */
        '#p-thrsrc{flex:1;min-width:0}' +
        '#p-thr-sess,#p-thr-def{flex:0 0 auto}' +
        '.qpop{position:fixed;inset:0;z-index:80;background:rgba(0,0,0,.34);display:flex;align-items:center;justify-content:center;padding:20px}' +
        '.qbox{position:relative;width:min(400px,88vw);max-height:64vh;overflow:auto;background:var(--bg);border:1px solid var(--bd);border-radius:16px;padding:18px 20px;box-shadow:0 18px 50px rgba(0,0,0,.34);color:var(--t2);font-size:13px;line-height:1.8;text-align:left;-webkit-overflow-scrolling:touch}' +
        '.qbox b{color:var(--t1)}' +
        '.qbox p{margin:0 0 11px} .qbox p:last-child{margin-bottom:0}' +
        '.qx{position:absolute;right:8px;top:8px;width:30px;height:30px;border:none;border-radius:50%;background:var(--bg-inset);color:var(--t3);font-family:inherit;font-size:17px;line-height:1;cursor:pointer;padding:0}' +
        '.qx:hover{background:var(--bg-hover);color:var(--t1)}' +
        '.duo{display:flex;gap:8px;align-items:center}' +
        '.duo>select,.duo>input{flex:1;min-width:0}' +
        '.duo>.btn{flex:0 0 auto;white-space:nowrap;font-size:13px;padding:9px 15px}' +
        '.st{font-size:12px;line-height:1.65;color:var(--t4);margin-top:6px;word-break:break-all}' +
        '.st em{font-style:normal;color:var(--t2);font-weight:600}';
      document.head.appendChild(st);

      var KEEP = {'p-caps-tip':1};   /* 实时状态，一直可见；其余 .note 一律收进「?」 */
      var tip = null;
      function closeTip(){
        if(tip && tip.parentNode) tip.parentNode.removeChild(tip);
        tip = null;
        Array.prototype.slice.call(document.querySelectorAll('.q.on')).forEach(function(b){ b.classList.remove('on'); });
      }
      function openTip(btn, parts){
        closeTip();
        tip = document.createElement('div');
        tip.className = 'qpop';
        tip.innerHTML = '<div class="qbox"><button class="qx" type="button" aria-label="' + T.nav_close + '">×</button>'
                      + parts.map(function(x){ return '<p>' + x + '</p>'; }).join('')
                      + '</div>';
        document.body.appendChild(tip);
        if(btn) btn.classList.add('on');
        tip.querySelector('.qx').onclick = closeTip;
        tip.addEventListener('click', function(e){ if(e.target === tip) closeTip(); });
      }
      document.addEventListener('keydown', function(e){ if(e.key === 'Escape') closeTip(); });

      /* 把一块区域里的静态说明全收起来，返回它们的 HTML 片段数组 */
      function collect(box){
        var parts = [];
        Array.prototype.slice.call(box.querySelectorAll('.note')).forEach(function(n){
          if(n.id && KEEP[n.id]) return;
          var h = (n.innerHTML || '').trim();
          n.style.display = 'none';
          if(h) parts.push(h);
        });
        return parts;
      }
      /* 挂一个「?」；点/长按弹气泡 */
      function mkQ(host, parts, before){
        if(!parts.length) return;
        var q = document.createElement('button');
        q.type = 'button'; q.className = 'q'; q.textContent = '?'; q.title = T.nav_help;
        var t = null, viaLong = false;
        q.addEventListener('click', function(e){
          e.stopPropagation();
          if(viaLong){ viaLong = false; return; }
          if(q.classList.contains('on')) closeTip(); else openTip(q, parts);
        });
        q.addEventListener('touchstart', function(){
          t = setTimeout(function(){ viaLong = true; openTip(q, parts); }, 420);
        }, {passive:true});
        q.addEventListener('touchend', function(){ clearTimeout(t); });
        q.addEventListener('touchmove', function(){ clearTimeout(t); });
        if(before && before.parentNode === host) host.insertBefore(q, before);
        else host.appendChild(q);
      }

      function plant(){
        /* 进化页那个手写的 details.help 与「进化方向」行的说明重复，先移除以免看两遍 */
        Array.prototype.slice.call(document.querySelectorAll('details.help')).forEach(function(d){
          if(d.parentNode) d.parentNode.removeChild(d);
        });
        /* 控制台：每个标签页右上角挂一个，装该页全部说明 */
        var ctl = document.getElementById('ctl');
        if(ctl && !ctl.dataset.qDone){
          ctl.dataset.qDone = '1';
          Array.prototype.slice.call(ctl.querySelectorAll('.pane')).forEach(function(p){
            mkQ(p, collect(p));
          });
        }
        /* 设置：卡片标题栏右侧挂一个 */
        var conf = document.getElementById('conf');
        if(conf && !conf.dataset.qDone){
          conf.dataset.qDone = '1';
          var head = conf.querySelector('.card-h');
          if(head){
            var hb = head.querySelector('b');     /* 让标题撑满，问号自然被顶到最右 */
            if(hb) hb.style.flex = '1';
            mkQ(head, collect(conf), head.querySelector('[data-close]'));
          }
        }
      }
      if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', plant);
      else plant();
    })();
    </script>
    <div class="card-b">
      <div class="sec-t" data-t="ctl_identity">身份</div>
      <div class="row"><span class="lb" data-t="cfg_name">名称</span><div class="ctl"><input type="text" id="p-name" maxlength="20" data-t-ph="cfg_name_ph" placeholder="给这个助手起个名字">
        <div class="note">这个助手在对话里自称的名字，只影响称谓，不影响任何功能。出厂默认叫 <b>Sidekick</b>；这里改成什么，它就自称什么。</div></div></div>

      <div class="sec-t" data-t="evo_sec">进化</div>
      <div class="row"><span class="lb" data-t="evo_daily">每天自动进化</span><div class="ctl"><label class="sw-wrap"><input class="sw" type="checkbox" id="p-enable"></label>
        <div class="note">开启后它每天会自己挑一件事来改进自己（改的是它自己的源码），一天最多按下面设定的项数做。关掉就只在你说「立即进化一次」时动手。</div>
        <div class="note">进化只在它闲着的时候跑，不会打断你正在进行的对话；每次只做一项，做完立刻验证。</div></div></div>
      <div class="row"><span class="lb" data-t="evo_dir">进化方向</span><div class="ctl"><input type="hidden" id="p-dir">
        <div class="caps" id="p-caps"></div>
        <div class="note" id="p-caps-tip"></div>
        <div class="note">点一颗即锁定为进化方向（再点它一下解锁）；不锁定就每天由 AI 自动换一批。</div>
        <div class="note">锁定之后，每天的自动进化都朝这一个方向使劲，不再换来换去 —— 适合你明确知道哪儿不够好、想集中改进的时候。</div></div></div>
      <div class="row"><span class="lb" data-t="evo_perday">每天几项</span><div class="ctl"><input type="number" id="p-perday" min="1" max="10" value="1"></div></div>
      <div class="row"><span class="lb" data-t="evo_today">今日进度</span><div class="ctl"><span id="p-prog" style="font-size:14px"></span>
        <div class="note">今天已经进化了几项、还剩几项。进度写在 evolve.json 里，跨重启保留，第二天自动归零。</div>
        <div class="note" id="p-safe">每项改动都会自动备份 + 语法检查，失败立即还原；服务由看门狗托管，起不来会自动回滚。</div></div></div>
      <div class="row" style="border-bottom:none"><span class="lb" data-t="evo_manual">手动进化</span><div class="ctl">
        <button class="btn" id="p-evolve" data-t="evo_btn_now">立即进化一次</button>
        <div class="note">按上面锁定的那颗方向做一项；没有锁定的就随机挑一颗。</div>
        <div class="note">不用等每天那次，随时可以让它马上进化一次。跑的时候给它点时间，改完会出现在「更新记录」里。</div>
      </div></div>

      <div class="sec-t"><span data-t="cfg_changelog">更新记录</span> <span id="p-ver" style="color:var(--t4)"></span></div>
      <div class="row" style="border-bottom:none"><div class="ctl">
        <div class="log chg" id="p-chg"></div>
        <div class="note">每次自我更新 / 自动进化 / 手动进化都留一条：版本号 · 时间 · 改了什么。<b style="color:var(--brand)">高亮加粗</b>的是进化（它自己改自己）。</div>
        <div class="note">这里只显示最近 12 条，完整的都在 changelog.json 里，不会丢。点开任意一条能看这次到底动了哪里。</div>
        <div class="note">徽章含义：「自我更新」是你说一声让它改的（修 bug、加功能）；「自动进化」是它自己排的班；「手动进化」是你在上面点了「立即进化一次」。</div></div></div>

      <div class="sec-t"><span data-t="cfg_memory">记忆</span> <span id="p-memc" style="color:var(--t4)"></span></div>
      <div class="row" style="border-bottom:none"><span class="lb" data-t="cfg_memory_long">长期记忆</span><div class="ctl">
        <textarea id="p-mem" rows="7" placeholder="还没有积累记忆。多聊几句，它会自动提炼。"></textarea>
        <div class="note">从对话里自动沉淀的长期信息，每次对话都会带上。可以直接编辑或清空。</div>
        <div class="note">存的是稳定事实和你的偏好（设备型号、习惯、约定），不是流水账 —— 每聊一阵它会自己提炼一次，把琐碎过程挤掉。</div>
        <div class="note">上限 3000 字，超了会裁掉最旧的并存一份备份（memory.md.bak-*），所以写得再满也不会丢底稿。改完记得点下面的「保存」。</div></div></div>

      <div class="sec-t" data-t="cfg_ctx">上下文</div>
      <div class="row"><span class="lb" data-t="cfg_ctx_use">占用</span><div class="ctl"><span id="p-ctx" style="font-size:14px"></span>
        <div class="bar"><i id="p-bar"></i></div>
        <div class="note" id="p-win"></div>
        <div class="note">圆环分母是模型真实的窗口大小，不是估算：优先问接口的 /models，其次读本机模型的 n_ctx，再次查内置表，最后按模型名推断。有的接口（如智谱）不返回窗口字段，就只能走后面的兜底。</div></div></div>
      <div class="row"><span class="lb" data-t="cfg_compact">压缩时机</span><div class="ctl">
        <div style="display:flex;align-items:center;gap:7px;flex-wrap:nowrap">
          <input type="number" id="p-thr" min="10" max="95" step="5" style="width:60px;flex:0 0 auto">
          <span style="color:var(--t3);white-space:nowrap" data-t="ctx_at_static">% 时压缩</span>
          <span class="note" id="p-thrsrc"></span>
          <div class="note">输入框里的数字就是本对话当前使用的压缩阈值；点「应用到本对话」把它记在本对话上（不影响之后新开的对话）。<span id="p-thrsrc2"></span></div>
          <button class="btn" id="p-thr-sess" style="margin-left:auto;white-space:nowrap;font-size:13px;padding:8px 12px">应用到本对话</button>
          <button class="btn" id="p-thr-def" style="white-space:nowrap;font-size:13px;padding:8px 12px">设为默认值</button>
        </div>
        <div class="note">阈值 = 模型窗口 × 比例（1 token ≈ 1.5 字）。每个对话各记各的；「设为默认值」只影响之后新开的对话。</div>
        <div class="note">用量涨到阈值时，它会自动把前面的旧对话压缩成摘要，腾出窗口继续聊。调低 = 更早压缩、更省 token 但细节丢得早；调高 = 记得更久但更烧钱。默认 70% 对大多数模型都合适。</div>
        <div class="note">压缩时最近 16 条消息无论如何都原样保留、不进摘要，所以不用怕刚说的话被压没了。</div></div></div>
      <div class="row" style="border-bottom:none"><span class="lb">单轮轮次上限</span><div class="ctl">
        <input type="number" id="p-maxround" min="10" max="1000" step="10" style="width:100px">
        <div class="note">一次任务里模型最多来回调用工具多少轮，不够用就调大（默认 200）。</div>
        <div class="note">「一轮」= 模型想一次 + 调一次工具 + 拿到结果再想。像批量改文件、逐个查资料这种活容易用掉很多轮；如果任务总在中途停住说「达到轮次上限」，就把它调大。</div></div></div>

      <div class="sec-t" data-t="ctl_look">外观</div>
      <div id="ui-box"></div>
      <div class="row" style="border-bottom:none"><span class="lb"></span><div class="ctl">
        <div class="note" id="ui-st"></div>
        <div class="note">改上面任何一项，对话区当场就变，并自动记住（点预设立即落盘、拖滑块停手就落盘；下次打开还是这个样子）。</div>
        <div class="note">这里只管对话正文的排版，不影响侧边栏、按钮等界面本身。</div>
        <div class="note">「预设」是一键套用一整套搭配，套完还能逐项微调，不会锁死。想恢复原样，把各项调回中间值即可。</div></div></div>

      <div class="sec-t" data-t="ctl_log">日志</div>
      <div class="row" style="border-bottom:none"><span class="lb">日志文件</span><div class="ctl">
        <select id="lg-pick" style="width:100%"></select>
        <div class="note">选一个日志看它的末尾；点「AI 自检」会让模型读这段日志并给出：发生了什么 / 异常 / 可能原因 / 建议动作。</div>
        <div class="note">出问题先来这儿：turn.log 记每一轮对话，access.log 记页面请求，agent.log 记服务本身。日志只留末尾一段、且会自动清理最旧的，所以要及时看。</div>
        <div class="note">「AI 自检」是把这段日志直接喂给模型让它分析 —— 用你自己的 API 额度，看长日志前留意一下。</div>
        <div style="display:flex;gap:8px;margin-top:8px;flex-wrap:wrap">
          <button class="btn" id="lg-load" style="font-size:13px;padding:8px 14px">刷新列表</button>
          <button class="btn pri" id="lg-check" style="font-size:13px;padding:8px 14px">AI 自检</button>
        </div>
        <pre id="lg-ans" class="lgbox" style="display:none"></pre>
        <pre id="lg-body" class="lgbox"></pre>
      </div></div>
      <style>
      .lgbox{margin:8px 0 0;padding:10px 12px;background:var(--code);border-radius:10px;
        font-size:11.5px;line-height:1.6;max-height:260px;overflow:auto;white-space:pre-wrap;
        word-break:break-word;color:var(--t2);-webkit-overflow-scrolling:touch}
      #lg-ans{background:var(--brand-soft)}
      </style>
      <script>
      /* 运行日志页：包一层 handleMsg 接住 log_* 事件，再原样转发给原来的处理逻辑 */
      (function(){
        var pick = document.getElementById('lg-pick');
        var body = document.getElementById('lg-body');
        var ans  = document.getElementById('lg-ans');
        if(!pick || !body || !ans) return;

        function curName(){ return pick.value || ''; }
        function esc(s){ return String(s == null ? '' : s)
          .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

        /* 日志名 → 中文说明。用户看英文名不知道是干嘛的，这里在下拉里跟上简短注释。 */
        var LOG_DESC = {
          'access.log':       '页面访问记录',
          'turn.log':         '每轮对话结果',
          'evolve.log':       '自动进化记录',
          'selfheal.log':     '自我修复记录',
          'supervisor.log':   '看门狗·服务启停',
          'wakelock.log':     '唤醒锁·防休眠',
          'crash.log':        '崩溃记录',
          'wx_auto.log':      '微信自动回复',
          'wx_companion.log': '微信陪聊',
          'restart.log':      '服务重启记录',
          '_diag.log':        '诊断输出'
        };

        function fillList(list){
          list = list || [];
          var keep = pick.value;
          pick.innerHTML = list.map(function(x){
            var kb = (x.size / 1024).toFixed(1);
            var desc = LOG_DESC[x.name] ? '（' + LOG_DESC[x.name] + '）' : '';
            return '<option value="' + esc(x.name) + '">' + esc(x.name) + desc
                 + '   ' + kb + ' KB   ' + esc(x.mtime) + '</option>';
          }).join('');
          if(keep){ Array.prototype.slice.call(pick.options).forEach(function(o){ if(o.value === keep) pick.value = keep; }); }
          if(!list.length){ body.textContent = T.log_none; return; }
          if(curName()) wsSend({cmd:'log_tail', name: curName(), tail: 300});
        }

        /* 把原来的 handleMsg 包一层：认识 log_* 就自己处理，否则照旧。
           时序坑：本段脚本排在主逻辑之前，跑到这里时 handleMsg 尚未定义，
           原来写 if(typeof handleMsg === 'function') 会判为 false、**静默跳过包装**，
           于是 log_* 响应被主逻辑当成未知事件丢掉，日志列表永远填不上。
           改成轮询等它出现再包（最多等约 6 秒）。 */
        (function wrapHandle(tries){
          if(typeof handleMsg !== 'function'){
            if(!tries) tries = 0;
            if(tries < 100) setTimeout(function(){ wrapHandle(tries + 1); }, 60);
            return;
          }
          var prev = handleMsg;
          handleMsg = function(m){
            var ev = m && m.ev, d = m && m.d;
            if(ev === 'log_list'){ fillList(d); return; }
            if(ev === 'log_tail'){
              if(d && d.ok){
                body.textContent = (d.lines || []).join('\n');
                body.scrollTop = body.scrollHeight;
              } else { body.textContent = (d && d.error) || '读取失败'; }
              return;
            }
            if(ev === 'log_check'){
              if(d && d.ok){
                ans.style.display = '';
                ans.textContent = T.log_ai_check + (d.name || '') + '，末尾 ' + (d.lines || 0) + ' 行】\n\n'
                  + (d.answer || '(空)');
              } else {
                ans.style.display = '';
                ans.textContent = T.log_ai_fail + ((d && d.error) || '未知原因');
              }
              return;
            }
            return prev(m);
          };
        })();

        pick.onchange = function(){ if(curName()) wsSend({cmd:'log_tail', name: curName(), tail: 300}); };
        document.getElementById('lg-load').onclick = function(){
          wsSend({cmd:'log_list'}); ans.style.display = 'none';
        };
        /* 「发给 AI 解决」：把当前日志末尾塞进输入框并直接发出，
           让对话里的模型真正去分析并动手修 —— 而不是只在控制台里给一段诊断。
           注意本段脚本在主逻辑之前，input/send 那两个 const 还没初始化，
           所以这里一律用 getElementById 现取，不在闭包里引用它们。 */
        (function(){
          var bar = document.getElementById('lg-load');
          if(!bar || !bar.parentNode) return;
          var fx = document.createElement('button');
          fx.type = 'button'; fx.className = 'btn pri';
          fx.textContent = T.log_send_ai;
          fx.style.cssText = 'font-size:13px;padding:8px 14px';
          fx.onclick = function(){
            var nm = curName();
            var ta = document.getElementById('lg-body');
            var txt = ta ? (ta.textContent || '').trim() : '';
            if(!nm || !txt){ toast('先选一个日志，等内容读出来'); return; }
            var inp = document.getElementById('input');
            var snd = document.getElementById('send');
            if(!inp || !snd){ toast('找不到输入框'); return; }
            inp.value = T.log_pick_a + nm + T.log_pick_b
              + '```\n' + txt.slice(-8000) + '\n```';
            try{ inp.dispatchEvent(new Event('input')); }catch(e){}
            document.getElementById('ctl').style.display = 'none';   /* 收掉控制台，回到对话 */
            if(typeof snd.onclick === 'function') snd.onclick();
          };
          bar.parentNode.appendChild(fx);
        })();
        document.getElementById('lg-check').onclick = function(){
          if(!curName()){ toast('先选一个日志文件'); return; }
          ans.style.display = ''; ans.textContent = T.log_ai_analyzing;
          wsSend({cmd:'log_check', name: curName(), tail: 300});
        };
        wsSend({cmd:'log_list'});
      })();
      </script>

      <div class="sec-t" data-t="ctl_skill">技能</div>
      <div class="row" style="border-bottom:none"><div class="ctl">
        <div class="axbar">
          <label><input type="checkbox" id="sk-all"><span data-t="cfg_all">全选</span></label>
          <span class="cnt" id="sk-cnt">已选 0</span>
          <button class="btn" id="sk-del" disabled>删除选中</button>
        </div>
        <div class="tbwrap">
          <table class="tb">
            <thead><tr><th class="ck"></th><th data-t="th_skill">技能</th><th data-t="th_desc">说明</th><th data-t="th_size">大小</th><th data-t="th_made">建立</th></tr></thead>
            <tbody id="sk-list"></tbody>
          </table>
        </div>
        <div class="note">技能文档是沉淀下来的「踩过的坑和正确姿势」，放在 skills/名字.md。做相关任务前会按需读取它，不必占用平时的上下文。删除会先备份到 skills/.trash/，可找回。<br><b>点任意一行即可展开看 AI 写的说明</b>（首次点开会现场生成，之后永久保存；技能改动后会自动提示可重新解释）。</div>
        <div class="note">为什么要有技能：同样的坑踩过一次就够了。这些文档平时不占上下文，只在碰上相关任务时才被读进来，所以放多少都不影响日常对话。</div>
      </div></div>

      <div class="sec-t" data-t="cfg_tools">工具</div>
      <div class="row" style="border-bottom:none"><div class="ctl">
        <div class="axbar">
          <label><input type="checkbox" id="tl-all"><span data-t="cfg_all">全选</span></label>
          <span class="cnt" id="tl-cnt">已选 0</span>
          <button class="btn" id="tl-off" disabled>禁用选中</button>
          <button class="btn" id="tl-on" disabled>恢复选中</button>
        </div>
        <div class="tbwrap">
          <table class="tb">
            <thead><tr><th class="ck"></th><th data-t="th_tool">工具</th><th data-t="th_use">用途</th></tr></thead>
            <tbody id="tl-list"></tbody>
          </table>
        </div>
        <div class="note">当前可调用的全部工具，共 <b id="tl-n">0</b> 个，其中已禁用 <b id="tl-off-n">0</b> 个。禁用只是不再把该工具的定义发给模型，函数本身不动，随时可恢复。</div>
        <div class="note">工具越少，模型挑得越准、也越省 token。用不上的（比如不玩的某个平台）禁掉，反而能让它把该用的用对。</div>
        <div class="note">禁用项记在 disabled_tools.json 里，跨重启保留。想全部恢复，把开关逐个打开即可。</div>
      </div></div>
      <style>
      .axbar{display:flex;align-items:center;gap:10px;padding:2px 0 10px;font-size:12.5px;color:var(--t3)}
      .axbar label{display:flex;align-items:center;gap:5px;cursor:pointer;user-select:none;-webkit-user-select:none}
      .axbar label input{width:15px;height:15px;accent-color:var(--brand);cursor:pointer}
      .axbar .cnt{color:var(--t4);font-variant-numeric:tabular-nums}
      .axbar .btn{padding:6px 13px;font-size:12.5px}
      .axbar .btn:disabled{opacity:.4;cursor:default}
      .tbwrap .tb th.ck,.tbwrap .tb td.ck{width:34px;padding-right:0;text-align:center}
      .tbwrap .tb input[type=checkbox]{width:15px;height:15px;accent-color:var(--brand);cursor:pointer;
        vertical-align:middle;margin:0}
      .tbwrap .tb tr.disabled td:not(.ck){color:var(--t4);opacity:.6}
      .tbwrap .tb tr.disabled td:first-of-type::after{content:'（已禁用）';font-weight:400;color:var(--t4);font-size:11px}
      </style>
      <style>
      .tbwrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
      /* 注意：这几个选择器必须带 .tbwrap 前缀。
         类名 .tb 和控制台标签按钮（.ctabs .tb）撞了 —— 之前写成 .tb{width:100%}，
         那条规则会落到标签按钮上，把每个标签撑成整行宽，8 个标签加起来 4646px，
         右侧 7 个全被挤出可视区，看起来就像"控制台只剩身份一页"。 */
      .tbwrap .tb{width:100%;border-collapse:collapse;font-size:12.5px;line-height:1.5}
      .tbwrap .tb th{text-align:left;font-weight:500;color:var(--t4);font-size:11.5px;
             padding:6px 9px;border-bottom:1px solid var(--bd);white-space:nowrap}
      .tbwrap .tb td{padding:8px 9px;border-bottom:1px solid var(--bd);color:var(--t3);vertical-align:top}
      .tbwrap .tb tbody tr:last-child td{border-bottom:none}
      .tbwrap .tb td:first-child{color:var(--t1);font-weight:600;white-space:nowrap;
                         font-family:ui-monospace,SFMono-Regular,monospace}
      /* 第 3 列在两张表里含义不同：技能表是「说明」、工具表是「用途」。
         原来把第 3、4 列一并写死 nowrap，工具表因此被撑到 745px，
         塞进 528px 的可视区里必须横向滚动才看得到每个工具是干什么的。
         这里让第 3 列能折行、长串也能断；真正要收紧的是「大小 / 建立」这类元信息列
         （技能表的第 4、5 列）。实测：工具表 745→528px，横向滚动消失。 */
      .tbwrap .tb td:nth-child(3){white-space:normal;color:var(--t2);word-break:break-word}
      .tbwrap .tb td:nth-child(4),.tbwrap .tb td:nth-child(5){white-space:nowrap;color:var(--t4);font-size:11.5px}
      /* ---- 技能行：点一下展开 AI 说明，再点收回 ---- */
      .tbwrap tr.skrow{cursor:pointer}
      .tbwrap tr.skrow:hover td{background:var(--bg2)}
      .tbwrap tr.skrow .arw{display:inline-block;width:13px;font-size:9px;color:var(--t4)}
      .tbwrap tr.skrow .sk-dot{color:var(--brand);font-size:10px;margin-left:3px}
      .tbwrap tr.skrow .sk-stale{color:var(--warn);font-size:10px;margin-left:3px}
      /* 展开行：覆盖 first-child 的等宽/不换行，让说明正常排版 */
      .tbwrap tr.skexp td{background:var(--bg2);padding:12px 14px;
                          white-space:normal;font-family:inherit;font-weight:400;color:var(--t1)}
      .tbwrap tr.skexp td:first-child{white-space:normal;font-family:inherit;font-weight:400}
      .tbwrap .skexp-h{display:flex;align-items:center;gap:10px;font-size:12px;color:var(--t3);margin-bottom:7px}
      .tbwrap .skexp-h .btn{margin-left:auto;font-size:12px;padding:3px 10px}
      .tbwrap .skexp-b{font-size:13.5px;line-height:1.75;color:var(--t1);white-space:pre-wrap;word-break:break-word}
      .tbwrap .skexp-wait{color:var(--t3);font-size:13px;display:flex;align-items:center;gap:8px}
      .tbwrap .skexp-err{color:var(--err);font-size:13px}
      </style>
      <script>
      /* 技能与工具页：数据由后端 skills_tools 一次给全。
         包装 handleMsg 要等主逻辑加载完（本段脚本排在它前面），
         否则包装会被静默跳过、事件被当成未知事件丢掉 —— 和运行日志页同样的坑。 */
      (function(){
        var skEl = document.getElementById('sk-list');
        var tlEl = document.getElementById('tl-list');
        if(!skEl || !tlEl) return;

        function esc(s){ return String(s == null ? '' : s)
          .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

        function fill(d){
          d = d || {};
          var sk = d.skills || [], tl = d.tools || [];
          var off = d.disabled || [];              /* 后端给的已禁用工具名 */
          var offSet = {};
          off.forEach(function(n){ offSet[n] = 1; });

          skEl.innerHTML = sk.length ? sk.map(function(x){
            var mark = x.explain ? (x.explain_stale
                        ? '<span class="sk-stale" title="技能改过了，说明可能过期">●</span>'
                        : '<span class="sk-dot" title="已有 AI 说明">●</span>') : '';
            return '<tr class="skrow" data-sk="' + esc(x.name) + '">'
                 + '<td class="ck"><input type="checkbox" data-kind="sk" value="' + esc(x.name) + '"></td>'
                 + '<td><span class="arw">▶</span>' + esc(x.name) + '</td>'
                 + '<td>' + esc(x.title || '') + '</td>'
                 + '<td>' + (x.size / 1024).toFixed(1) + ' KB</td>'
                 + '<td>' + esc(x.mtime) + mark + '</td></tr>';
          }).join('') : '<tr><td colspan="5">（还没有技能文档）</td></tr>';
          tlEl.innerHTML = tl.length ? tl.map(function(x){
            var dis = !!offSet[x.name];
            return '<tr' + (dis ? ' class="disabled"' : '') + '>'
                 + '<td class="ck"><input type="checkbox" data-kind="tl" value="' + esc(x.name) + '"></td>'
                 + '<td>' + esc(x.name) + '</td>'
                 + '<td>' + esc(x.brief || '') + '</td></tr>';
          }).join('') : '<tr><td colspan="3">（读不到工具清单）</td></tr>';

          var n = document.getElementById('tl-n'); if(n) n.textContent = tl.length;
          var dn = document.getElementById('tl-off-n'); if(dn) dn.textContent = off.length;
          syncSel();
          restoreExp();          /* 重绘后把展开行放回去，别让刷新把说明冲掉 */
        }

        /* ---------- 多选：全选 / 计数 / 按钮可用性 ---------- */
        /* ---------- 点开技能 → 展开 AI 说明（点第二次收回） ---------- */
        var expCache = {}, expOpen = null;

        function renderExp(name){
          var c = expCache[name];
          if(!c) return '<div class="skexp-wait"><span class="sp"></span>AI 正在阅读这个技能，并写一段说明…</div>';
          var stale = c.stale ? ' <span class="sk-stale">技能已改动，建议重新解释</span>' : '';
          return '<div class="skexp-h">AI 说明 · ' + esc(c.at || '') + stale
               + '<button class="btn" data-re="' + esc(name) + '">重新解释</button></div>'
               + '<div class="skexp-b">' + esc(c.text || '') + '</div>';
        }

        function closeExp(){
          expOpen = null;
          var tr = document.querySelector('tr.skexp');
          if(tr) tr.parentNode.removeChild(tr);
          var rows = document.querySelectorAll('tr.skrow .arw');
          for(var i = 0; i < rows.length; i++) rows[i].textContent = '▶';
        }

        /* 把展开行插到指定技能行下面。table 重绘后靠 restoreExp() 复用这段逻辑。 */
        function openExp(name){
          var row = document.querySelector('tr.skrow[data-sk="' + name + '"]');
          if(!row) return;
          var old = document.querySelector('tr.skexp');
          if(old) old.parentNode.removeChild(old);
          var tr = document.createElement('tr');
          tr.className = 'skexp';
          var td = document.createElement('td');
          td.colSpan = 5;
          td.innerHTML = renderExp(name);
          tr.appendChild(td);
          row.parentNode.insertBefore(tr, row.nextSibling);
          var a = row.querySelector('.arw'); if(a) a.textContent = '▼';
          expOpen = name;
        }

        /* 表格被 fill() 整体重绘后，把展开行按当前状态放回去 —— 否则会被冲掉。
           （后端生成完说明会推 skills_tools 触发重绘，之前展开行就是这么消失的） */
        function restoreExp(){
          if(!expOpen) return;
          if(!document.querySelector('tr.skrow[data-sk="' + expOpen + '"]')){ expOpen = null; return; }
          openExp(expOpen);
        }

        function toggleExp(name){
          if(expOpen === name){ closeExp(); return; }
          openExp(name);
          if(!expCache[name]) wsSend({cmd:'skill_explain', name: name});
        }

        function onExplain(d){
          if(!d || !d.name) return;
          if(d.loading) return;
          if(d.error){
            var t1 = document.querySelector('tr.skexp > td');
            if(t1) t1.innerHTML = '<div class="skexp-err">' + T.skill_gen_fail + esc(d.error)
                                + '<br>（可点「重新解释」再试一次）</div>';
            return;
          }
          expCache[d.name] = {text: d.text || '', at: d.at || '', stale: false};
          if(expOpen === d.name){
            var t2 = document.querySelector('tr.skexp > td');
            if(t2) t2.innerHTML = renderExp(d.name);
          }
        }

        document.addEventListener('click', function(e){
          var t = e.target;
          if(!t || !t.closest) return;
          var re = t.getAttribute && t.getAttribute('data-re');
          if(re){ wsSend({cmd:'skill_explain', name: re, force:true}); return; }
          var row = t.closest('tr.skrow');
          if(!row) return;
          if(t.tagName === 'INPUT') return;
          toggleExp(row.getAttribute('data-sk'));
        });

        function boxes(kind){
          return Array.prototype.slice.call(
            document.querySelectorAll('.tb input[data-kind="' + kind + '"]'));
        }
        function checked(kind){
          return boxes(kind).filter(function(b){ return b.checked; });
        }
        function syncSel(){
          /* 技能页 */
          var a1 = boxes('sk'), s1 = checked('sk');
          var el1 = document.getElementById('sk-cnt'); if(el1) el1.textContent = T.log_selected + s1.length;
          var bb1 = document.getElementById('sk-all');
          if(bb1){ bb1.checked = a1.length > 0 && s1.length === a1.length;
                   bb1.indeterminate = s1.length > 0 && s1.length < a1.length; }
          var d1 = document.getElementById('sk-del'); if(d1) d1.disabled = !s1.length;
          /* 工具页 */
          var a2 = boxes('tl'), s2 = checked('tl');
          var el2 = document.getElementById('tl-cnt'); if(el2) el2.textContent = T.log_selected + s2.length;
          var bb2 = document.getElementById('tl-all');
          if(bb2){ bb2.checked = a2.length > 0 && s2.length === a2.length;
                   bb2.indeterminate = s2.length > 0 && s2.length < a2.length; }
          var o2 = document.getElementById('tl-off'); if(o2) o2.disabled = !s2.length;
          var r2 = document.getElementById('tl-on');  if(r2) r2.disabled = !s2.length;
        }

        /* 事件委托：表格每次重绘都会换掉行内元素，逐个绑定不现实 */
        document.addEventListener('change', function(e){
          var t = e.target;
          if(!t || !t.tagName) return;
          if(t.getAttribute && t.getAttribute('data-kind')){ syncSel(); return; }
          if(t.id === 'sk-all'){ boxes('sk').forEach(function(b){ b.checked = t.checked; }); syncSel(); return; }
          if(t.id === 'tl-all'){ boxes('tl').forEach(function(b){ b.checked = t.checked; }); syncSel(); return; }
        });

        (function bindBtns(){
          var sd = document.getElementById('sk-del');
          if(sd) sd.onclick = function(){
            var names = checked('sk').map(function(b){ return b.value; });
            if(!names.length) return;
            if(!confirm('删除这 ' + names.length + ' 个技能文档？\n\n' + names.join('\n')
                        + '\n\n（服务端会先备份到 skills/.trash/，需要时可找回）')) return;
            wsSend({cmd:'skills_delete', names: names});
          };
          var to = document.getElementById('tl-off');
          if(to) to.onclick = function(){
            var names = checked('tl').map(function(b){ return b.value; });
            if(!names.length) return;
            if(!confirm('禁用这 ' + names.length + ' 个工具？\n\n' + names.join('\n')
                        + '\n\n（只是不再把它们的定义发给模型，函数本身不动，随时可恢复）')) return;
            wsSend({cmd:'tools_disable', names: names, on: true});
          };
          var tn = document.getElementById('tl-on');
          if(tn) tn.onclick = function(){
            var names = checked('tl').map(function(b){ return b.value; });
            if(!names.length) return;
            wsSend({cmd:'tools_disable', names: names, on: false});
          };
        })();

        (function wrapHandle(tries){
          if(typeof handleMsg !== 'function'){
            if(!tries) tries = 0;
            if(tries < 100) setTimeout(function(){ wrapHandle(tries + 1); }, 60);
            return;
          }
          var prev = handleMsg;
          handleMsg = function(m){
            if(m && m.ev === 'skills_tools'){ fill(m.d); return; }
            if(m && m.ev === 'skill_explain'){ onExplain(m.d); return; }
            return prev(m);
          };
          /* 包好了立刻再要一次：页面加载时发的那条 skills_tools 往往早于本包装就绪，
             响应回来时被当成未知事件丢掉（实测技能区一直是 0 条，手动触发才出来）。
             这里补一刀，保证数据一定能填上。 */
          wsSend({cmd:'skills_tools'});
        })();

        wsSend({cmd:'skills_tools'});
      })();
      </script>
    </div>
    <!-- 常驻操作条：控制台很长（身份/进化/记录/记忆/上下文），原来「保存」跟着内容滚出屏幕、要滑到最后才能按；现把「立即压缩」也并进来，两个按钮贴底常驻、任何标签页下都点得到 -->
    <div class="card-h" style="position:sticky;top:auto;bottom:0;border-top:1px solid var(--bd);border-bottom:none;border-radius:0 0 16px 16px;justify-content:flex-end;gap:10px;flex-wrap:wrap">
      <button class="btn" id="p-compress" style="font-size:14px;padding:10px 18px;color:var(--t1);line-height:normal">立即压缩</button>
      <button class="btn pri" id="p-save" style="font-size:14px;padding:10px 22px">保存</button>
    </div>
  </div>
</div>

<!-- 设置：模型 / Key / 权限 -->
<div class="sheet" id="conf" style="display:none">
  <div class="card">
    <div class="card-h"><b data-t="nav_settings">设置</b><button data-close="conf">×</button></div>
    <div class="card-b">
      <div class="row"><span class="lb" data-t="nav_lang">语言</span><div class="ctl"><select id="c-lang"></select>
        </div></div>
      <div class="row"><span class="lb" data-t="cfg_brand">品牌</span><div class="ctl"><select id="c-brand"></select></div></div>
      <div class="row"><span class="lb" data-t="cfg_key">API Key</span><div class="ctl"><input type="password" id="c-key" autocomplete="off" data-t-ph="cfg_key_ph" placeholder="sk-…（留空则不变）">
        <div class="st" id="c-keyst"></div></div></div>
      <div class="row"><span class="lb" data-t="cfg_base_url">接口地址</span><div class="ctl"><input type="text" id="c-base" data-t-ph="cfg_base_ph" placeholder="https://api.deepseek.com/v1"></div></div>
      <div class="row"><span class="lb" data-t="cfg_model">模型</span><div class="ctl">
        <div class="duo"><select id="c-model"></select><button class="btn" id="c-pull" type="button" data-t="cfg_pull">拉取</button></div>
        <div class="st" id="c-modelst"></div></div></div>
      <div class="row"><span class="lb" data-t="cfg_balance">余额</span><div class="ctl">
        <div class="duo"><div class="st" id="c-bal" style="flex:1;min-width:0;margin-top:0" data-t="cfg_balance_tip">点「查询」看剩余额度</div><button class="btn" id="c-balbtn" type="button" data-t="cfg_query_btn">查询</button></div></div></div>
      <div class="row" style="border-bottom:none"><span class="lb" data-t="cfg_shell">增强权限</span><div class="ctl"><input class="sw" type="checkbox" id="c-shell">
        <div class="note" data-t="cfg_shell_note">以 shell（adb）身份执行命令，可读系统设置、dumpsys、pm/am。不是 root。</div></div></div>
      <div class="note" data-t="cfg_note1">选好「品牌」，接口地址会自动填上，不必手打。</div>
      <div class="note" data-t="cfg_note2">预设覆盖 DeepSeek、智谱、通义等常见品牌，选中即自动填接口地址。用自建或代理服务时选「自定义」，自己填地址。</div>
      <div class="note" data-t="cfg_note3">各品牌的 API Key 分开记住：换品牌不会丢、也不会互相覆盖。要改哪个品牌，选中它、重新输入一次即可。</div>
      <div class="note" data-t="cfg_note4">Key 存在本机 config.json 里，只用于向对应接口发请求；这个界面只监听 127.0.0.1，不对局域网开放。</div>
      <div class="note" data-t="cfg_note5">填好 Key 后点「拉取」获取该接口的可用模型；点「查询」看余额或积分。</div>
      <div class="note" data-t="cfg_note6">「拉取」拿到的是接口当前真实提供的模型列表，所以新模型上线不用等更新，拉一次就有。</div>
    </div>
    <div class="card-h" style="border-top:1px solid var(--bd);border-bottom:none;border-radius:0 0 16px 16px;justify-content:flex-end">
      <button class="btn pri" id="c-save" style="font-size:14px;padding:10px 22px" data-t="cfg_save">保存</button>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
/* ---------------- 多语言：T 取词对象 ---------------- */
/* 服务端在 </head> 前注入了 window.__I18N__ = {lang, dir, dict, meta}。
   用法：T.in_send → "Send"；T.xxx(值) → 带参数的模板。
   缺失的键返回键名本身，便于一眼看出漏翻。 */
const _I18N = window.__I18N__ || {lang:'zh', dir:'ltr', dict:{}, meta:{}};
const T = new Proxy(_I18N.dict, {
  get(d, k){ return (k in d) ? d[k] : String(k); }
});
/* 扫一遍带 data-t 的静态节点，把文案换掉。
   放在 DOMContentLoaded 里跑，早于其它渲染逻辑。 */
function applyI18N(rootEl){
  const scope = rootEl || document;
  scope.querySelectorAll('[data-t]').forEach(el => {
    const k = el.getAttribute('data-t');
    const v = T[k];
    if (v && v !== k) el.textContent = v;
  });
  /* 带属性的：data-t-title / data-t-ph（placeholder） */
  scope.querySelectorAll('[data-t-title]').forEach(el => {
    const v = T[el.getAttribute('data-t-title')];
    if (v) el.setAttribute('title', v);
  });
  scope.querySelectorAll('[data-t-ph]').forEach(el => {
    const v = T[el.getAttribute('data-t-ph')];
    if (v) el.setAttribute('placeholder', v);
  });
  if (_I18N.dir === 'rtl') document.documentElement.setAttribute('dir','rtl');
  document.documentElement.setAttribute('lang', _I18N.lang || 'zh');
}
/* 界面文案是动态生成的（欢迎语、步骤卡片、会话列表…），一次性扫不够。
   盯着 DOM 新增的节点，自动把 data-t 的翻掉 —— 覆盖全站，不必逐个函数改。 */
(function watchI18N(){
  if (!window.MutationObserver) return;
  const obs = new MutationObserver(muts => {
    for (const m of muts){
      for (const n of m.addedNodes){
        if (n.nodeType !== 1) continue;
        if (n.hasAttribute && n.hasAttribute('data-t')) {
          const v = T[n.getAttribute('data-t')];
          if (v && v !== n.getAttribute('data-t')) n.textContent = v;
        }
        if (n.querySelectorAll) applyI18N(n);
      }
    }
  });
  const start = () => obs.observe(document.body, {childList:true, subtree:true});
  if (document.body) start(); else document.addEventListener('DOMContentLoaded', start);
})();

/* 图标引用：<svg class="ic"><use href="#i-名字"/></svg>，用法 ic('mic') */
const ic = (n, cls) => '<svg class="ic' + (cls ? ' ' + cls : '') +
                       '" aria-hidden="true"><use href="#i-' + n + '"/></svg>';
const $ = s => document.querySelector(s);
const body = $('#body'), root = $('#root'), input = $('#input'), send = $('#send'), dot = $('#dot');
let ws = null, wsReady = false, outq = [], reconnectTimer = null, lastEvtAt = Date.now();
let streaming = false, cur = null, curText = '', steps = null, stepsData = [], stepsElided = 0, lastError = false;
let stepsPinned = false;   /* 用户自己点过工具卡片的开合 → 本轮不再自动开合（2026-10-01） */
let cmdSel = 0, cmdShown = [], curSidVal = '';   /* 斜杠命令面板 / 当前会话 id */
let curUiLang = _I18N.lang || 'zh', langChanged = false;   /* 界面语言 / 本次保存是否改了语言 */
let wantSid = '';                                /* 我主动要切过去的会话 id（见 hello 处理） */
let busySid = '';        /* 正在回复中的会话是哪个（不同对话各跑各的，按钮状态别看错） */

/* ---------------- 连接（WebSocket 长连接，与官方同构） ---------------- */
function connect(){
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  try{ ws = new WebSocket(proto + '//' + location.host + '/ws'); }
  catch(e){ return retry(); }
  ws.onopen = () => {
    wsReady = true; dot.className = 'dot';
    const q = outq; outq = [];
    q.forEach(o => { try{ ws.send(JSON.stringify(o)); }catch(e){} });
  };
  ws.onmessage = e => { try{ handleMsg(JSON.parse(e.data)); }catch(err){} };
  ws.onclose = () => { wsReady = false; fileAsked = false; retry(); };
  ws.onerror = () => {};
}
function retry(){
  if(reconnectTimer) return;
  dot.className = 'dot err';
  reconnectTimer = setTimeout(() => { reconnectTimer = null; connect(); }, 1500);
}
function wsSend(obj){
  if(wsReady){ try{ ws.send(JSON.stringify(obj)); }catch(e){ outq.push(obj); } } else outq.push(obj);
  /* 控制台面板只认 WS 推来的数据：WS 没连上时消息只会入队（填不了），刚重连或假连接时
     也可能收不到，两种情况面板都会整片空白。所以 panel 指令一律并行走一次 HTTP
     （服务端 GET /api/panel 只要几毫秒），谁先到谁先填；重复填无害，fillPanel 里有输入框保护。 */
  if(obj && obj.cmd === 'panel')
    fetch('/api/panel', {cache:'no-store'}).then(r => r.json())
      .then(d => { if(d && d.name !== undefined) fillPanel(d); }).catch(() => {});
}

/* ---------------- 提示条 ---------------- */
let toastTimer = null;
function toast(msg, ms){
  const t = $('#toast'); t.textContent = msg; t.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('on'), ms || 3600);
}

/* ---------------- 极简 Markdown ---------------- */
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
let codeMap = new Map(), codeSeq = 0;   /* 代码块内容：渲染时存起来，点「复制」再取 */
/* 消息里允许直出的 HTML 子集（折叠、图片、表格这类呈现用得到）。
   其余标签一律转义成普通文字：不执行，也不会撑坏布局。 */
/* 内联 SVG：单独一套白名单。SVG 对大小写敏感（viewBox / linearGradient），
   所以这里保留原始写法，不跟 HTML 一样往下压小写。 */
const SVG_TAG_OK = new Set(['svg','g','defs','linearGradient','radialGradient','stop','rect','circle',
  'ellipse','line','polyline','polygon','path','text','tspan','use','symbol','clipPath','mask',
  'pattern','image','title','desc','filter','feGaussianBlur','feOffset','feBlend','feColorMatrix',
  'feFlood','feComposite','feMerge','feMergeNode','marker','textPath','view']);
const SVG_ATTR_OK = new Set(['viewBox','xmlns','fill','stroke','stroke-width','stroke-opacity',
  'fill-opacity','fill-rule','stroke-linecap','stroke-linejoin','stroke-dasharray','stroke-dashoffset',
  'x','y','x1','y1','x2','y2','cx','cy','r','rx','ry','d','points','transform','offset','stop-color',
  'stop-opacity','text-anchor','font-size','font-family','font-weight','font-style','opacity',
  'preserveAspectRatio','gradientUnits','gradientTransform','dx','dy','dominant-baseline','clip-path',
  'clipPathUnits','mask','patternUnits','patternTransform','id','vector-effect','width','height',
  'class','result','in','in2','stdDeviation','flood-color','flood-opacity','mode','xlink:href']);
function sanitizeSvg(src){
  return String(src).replace(/<(\/?)([a-zA-Z][\w:-]*)((?:"[^"]*"|'[^']*'|[^>"'])*)>/g,
    function(m, close, tag, attrs){
      if(!SVG_TAG_OK.has(tag)) return '';        /* 不在白名单：丢掉，别漏出来 */
      /* 关键：自闭合的 <rect/> <stop/> <circle/> 必须保住末尾那个斜杠。
         丢了它，标签就变成"未闭合"，后面所有元素会被塞进来，
         整个 SVG 结构就烂了 —— 实测表现就是只剩一片纯色背景。 */
      const selfClose = /\/\s*$/.test(attrs || '');
      let a = '';
      if(!close){
        const re = /([a-zA-Z-]+(?::[a-zA-Z-]+)?)\s*=\s*("([^"]*)"|'([^']*)')/g;
        let mm;
        while((mm = re.exec(attrs || ''))){
          const k = mm[1];
          const v = mm[3] != null ? mm[3] : mm[4];
          if(!SVG_ATTR_OK.has(k)) continue;
          if(/^on/i.test(k)) continue;
          if(/javascript:/i.test(v)) continue;
          a += ' ' + k + '="' + v.replace(/&/g,'&amp;').replace(/</g,'&lt;')
                                     .replace(/"/g,'&quot;').replace(/'/g,'&#39;') + '"';
        }
      }
      return '<' + (close || '') + tag + a + (selfClose ? '/>' : '>');
    });
}

const HTML_OK = new Set(['a','b','strong','i','em','u','s','del','mark','small','code','kbd','br','hr',
  'div','span','p','ul','ol','li','details','summary','table','thead','tbody','tr','th','td',
  'blockquote','img','figure','figcaption','sub','sup','h3','h4','abbr','center']);
const ATTR_OK = new Set(['class','href','src','alt','title','colspan','rowspan','width','height']);
function sanitizeTags(src){
  const keep = [];
  const text = String(src).replace(/<(\/?)([a-zA-Z][\w-]*)((?:"[^"]*"|'[^']*'|[^>"'])*)>/g,
    function(m, close, tag, attrs){
      const t = String(tag).toLowerCase();
      if(!HTML_OK.has(t)) return m;
      let a = '';
      if(!close){
        const re = /([a-zA-Z-]+)\s*=\s*("([^"]*)"|'([^']*)')/g;
        let mm;
        while((mm = re.exec(attrs || ''))){
          const k = mm[1].toLowerCase();
          const v = mm[3] != null ? mm[3] : mm[4];
          if(!ATTR_OK.has(k)) continue;
          if((k === 'href' || k === 'src') && !/^(https?:|mailto:|data:image\/)/i.test(v)) continue;
          a += ' ' + k + '="' + v.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/'/g,'&#39;') + '"';
        }
        if(t === 'a') a += ' target="_blank" rel="noreferrer"';
        if(t === 'img') a += ' loading="lazy"';
      }
      keep.push('<' + (close || '') + t + a + '>');
      return '\u0000H' + (keep.length - 1) + '\u0000';
    });
  return {text: text, keep: keep};
}
function render(src, depth){
  depth = depth || 0;
  const codes = [], langs = [], cards = [];
  let s = String(src).replace(/```([\w#+-]*)\n?([\s\S]*?)```/g, (m,lg,c) => {
    codes.push(c.replace(/\n$/,'')); langs.push(lg || '');
    return '\u0000C' + (codes.length-1) + '\u0000';
  });
  /* 流式生成中：最后一个 <svg> 还没等到 </svg> 时，先别往下渲染 ——
     半截 SVG 会被 esc 转义成一堆乱码/断图。临时换成"图形生成中"占位，
     等收尾标签到了，自然就变成真图（实测流式每 7 字符一块时必现）。 */
  const _lo = s.toLowerCase().lastIndexOf('<svg');
  const _lc = s.toLowerCase().lastIndexOf('</svg>');
  if(_lo > _lc) s = s.slice(0, _lo) + '\u0000P\u0000';
  /* 内联 SVG 整块先拿出来：它不能被逐行插 <br>，还必须保留大小写 */
  const svgs = [];
  s = s.replace(/<svg\b[\s\S]*?<\/svg>/gi, m => {
    svgs.push(sanitizeSvg(m));
    return '\u0000S' + (svgs.length - 1) + '\u0000';
  });
  if(depth < 2){            /* 卡片块：整行 :::kind 标题 … 单独一行 ::: 收尾 */
    s = s.replace(/^[ \t]*:::[ \t]*([a-zA-Z]+)[ \t]*([^\n]*)\n([\s\S]*?)^[ \t]*:::[ \t]*$/gm,
      (m, kind, title, body) => {
        cards.push({kind:String(kind).toLowerCase(), title:(title||'').trim(),
                    body:String(body).replace(/\s+$/,'')});
        return '\u0000K' + (cards.length-1) + '\u0000';
      });
  }
  const st = sanitizeTags(s);
  s = esc(st.text);
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
       .replace(/`([^`\n]+)`/g, '<code>$1</code>')
       .replace(/\[([^\]\n]+)\]\(((?:https?|mailto):[^)\s]+)\)/g,
                '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  const lines = s.split('\n');
  let out = '', list = false, tbl = [], olist = false, quote = false;
  const flushTable = () => {
    if(!tbl.length) return;
    let html = '<table>';
    tbl.forEach((row,i) => {
      if(/^\s*\|[\s:|-]+\|\s*$/.test(row)) return;
      const cells = row.trim().replace(/^\||\|$/g,'').split('|').map(c=>c.trim());
      const tag = i === 0 ? 'th' : 'td';
      html += '<tr>' + cells.map(c=>'<' + tag + '>' + c + '</' + tag + '>').join('') + '</tr>';
    });
    out += html + '</table>'; tbl = [];
  };
  const closeLists = () => {
    if(list){ out += '</ul>'; list = false; }
    if(olist){ out += '</ol>'; olist = false; }
    if(quote){ out += '</blockquote>'; quote = false; }
  };
  for (const ln of lines){
    if(/^\u0000P\u0000$/.test(ln)){            /* 半截 SVG 占位：原样放行 */
      flushTable(); closeLists();
      out += ln;
      continue;
    }
    if(/^\u0000S\d+\u0000$/.test(ln)){          /* SVG 整块：原样放行，不加 <br> */
      flushTable(); closeLists();
      out += ln;
      continue;
    }
    const kc = ln.match(/^\u0000K(\d+)\u0000$/);
    if(kc){
      flushTable(); closeLists();
      const cd = cards[+kc[1]] || {kind:'card', title:'', body:''};
      const cls = /^(card|tip|warn|danger)$/.test(cd.kind) ? cd.kind : 'card';
      out += '<div class="card ' + cls + '">'
           + (cd.title ? '<div class="card-h">' + cd.title + '</div>' : '')
           + '<div class="card-b">' + render(cd.body, depth + 1) + '</div></div>';
      continue;
    }
    if(/^\s*\|.*\|\s*$/.test(ln)){ tbl.push(ln); continue; }
    flushTable();
    if(/^\s*(---+|\*\*\*+)\s*$/.test(ln)){ closeLists(); out += '<hr>'; continue; }
    const qt = ln.match(/^\s*(?:&gt;|>)\s?(.*)$/);
    if(qt){
      if(list){ out += '</ul>'; list = false; }
      if(olist){ out += '</ol>'; olist = false; }
      if(!quote){ out += '<blockquote>'; quote = true; }
      out += qt[1] + '<br>'; continue;
    }
    if(quote){ out += '</blockquote>'; quote = false; }
    const uli = ln.match(/^\s*[-*+]\s+(.*)$/);
    if(uli){
      if(olist){ out += '</ol>'; olist = false; }
      if(!list){ out += '<ul>'; list = true; }
      out += '<li>' + uli[1] + '</li>'; continue;
    }
    const oli = ln.match(/^\s*\d+[.)]\s+(.*)$/);
    if(oli){
      if(list){ out += '</ul>'; list = false; }
      if(!olist){ out += '<ol>'; olist = true; }
      out += '<li>' + oli[1] + '</li>'; continue;
    }
    closeLists();
    const h = ln.match(/^(#{2,4})\s+(.*)$/);
    if(h){
      const lv = h[1].length;
      out += '<h' + lv + '>' + h[2] + '</h' + lv + '>';
      continue;
    }
    out += ln + '<br>';
  }
  flushTable(); closeLists();
  out = out.replace(/\u0000H(\d+)\u0000/g, (m,i) => (st.keep[+i] || ''));
  out = out.replace(/\u0000S(\d+)\u0000/g, (m,i) => (svgs[+i] || ''));
    out = out.replace(/\u0000P\u0000/g, '<span class="svgwait">' + ic('sparkles') + ' 图形生成中…</span>');
  return out.replace(/\u0000C(\d+)\u0000/g, (m,i) => {
    const id = ++codeSeq; codeMap.set(id, codes[i]);
    if(codeMap.size > 300){ codeMap.delete(codeMap.keys().next().value); }
    const lg = langs[+i] ? '<span class="lg">' + esc(langs[+i]) + '</span>' : '';
    return '<div class="cbx">' + lg
      + '<button class="cbc" type="button" data-c="' + id + '">' + T.chat_copy + '</button>'
      + '<pre>' + esc(codes[i]) + '</pre></div>';
  });
}

/* 代码块「复制」用事件委托，不随消息重渲染失效 */
root.addEventListener('click', e => {
  const b = e.target && e.target.closest ? e.target.closest('.cbc') : null;
  if(!b) return;
  const t = codeMap.get(Number(b.dataset.c));
  if(t == null){ toast(T.chat_copy_stale); return; }
  copyText(t, b);
});
/* ---------------- 消息渲染 ---------------- */
function atBottom(){ return body.scrollHeight - body.scrollTop - body.clientHeight < 110; }
/* 「回到最新」：上滑看历史时新内容不打断阅读，给个一键回到底部的入口 */
const jump = document.createElement('button');
jump.type = 'button'; jump.className = 'jump'; jump.textContent = T.chat_back_latest;
jump.onclick = () => { body.scrollTop = body.scrollHeight; syncJump(); };
(function initJump(){
  const st = document.createElement('style');
  st.textContent = '.main{position:relative}'
    + '.jump{position:absolute;left:50%;transform:translateX(-50%);bottom:calc(env(safe-area-inset-bottom) + 88px);'
    + 'z-index:60;background:var(--brand);color:#fff;border:none;border-radius:999px;padding:7px 14px;font-size:13px;'
    + 'box-shadow:0 6px 18px rgba(0,0,0,.22);opacity:0;pointer-events:none;transition:opacity .18s}'
    + '.jump.on{opacity:1;pointer-events:auto}';
  document.head.appendChild(st);
  const main = body.parentNode;
  (main || document.body).appendChild(jump);
  body.addEventListener('scroll', syncJump, {passive:true});
})();
function syncJump(){
  const over = body.scrollHeight - body.clientHeight;
  jump.classList.toggle('on', over > 60 && !atBottom());
}
function toBottom(force){ if(force || atBottom()) body.scrollTop = body.scrollHeight; syncJump(); }
function copyText(text, btn){
  const done = () => {
    if(!btn) return;
    const old = btn.textContent;
    btn.textContent = T.chat_copied; btn.classList.add('ok');
    setTimeout(() => { btn.textContent = old; btn.classList.remove('ok'); }, 1400);
  };
  const fallback = () => {
    const ta = document.createElement('textarea');
    ta.value = text; ta.setAttribute('readonly','');
    ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none';
    document.body.appendChild(ta);
    ta.select(); ta.setSelectionRange(0, text.length);
    let ok = false;
    try{ ok = document.execCommand('copy'); }catch(e){ ok = false; }
    ta.remove();
    if(ok) done(); else toast(T.chat_copy_fail);
  };
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(text).then(done).catch(fallback);
  } else fallback();
}
/* ---------------- 气泡里的附件（图片缩略图 / 文件图标） ----------------
   2026-10-01：以前发完图，气泡里只有一行「[我发了一张图片，已保存到 …]」的原始标记，
   刷新之后尤其难看。现在把这段标记解析出来：图片显示缩略图（点开看原图），
   其他文件显示扩展名徽标 + 文件名。标记本身照旧存在对话里 —— 模型需要那个路径。 */
const ATT_MARK = /\[我发了(一张图片|一个文件)，已保存到 ([^\]\n]+)\]/g;
const ATT_KIND = {'一张图片': 'image', '一个文件': 'file'};
function attBase(p){ return String(p).split('/').pop(); }
function attName(p){
  /* 文件名形如 20261001-160000-8f3a1c-合影.jpg → 去掉时间戳与校验码 */
  return attBase(p).replace(/^\d{8}-\d{6}-[0-9a-f]{6}-?/, '') || attBase(p);
}
function parseAtt(text){
  const out = [];
  const rest = String(text == null ? '' : text).replace(ATT_MARK, (m, kind, p) => {
    out.push({kind: ATT_KIND[kind] || 'file', path: String(p).trim()});
    return '';
  });
  return {items: out, rest: rest.replace(/^\s*\n/, '').trim()};
}
function renderUserBub(bub, text){
  const pr = parseAtt(text);
  if(!pr.items.length){ bub.textContent = text; return; }
  const wrap = document.createElement('div'); wrap.className = 'uatts';
  pr.items.forEach(it => {
    const base = attBase(it.path);
    const url = '/api/file?name=' + encodeURIComponent(base);
    const nm = attName(it.path);
    if(it.kind === 'image'){
      const a = document.createElement('a');
      a.className = 'uthumb'; a.href = url; a.target = '_blank'; a.rel = 'noopener';
      a.title = nm + '（点开看原图）';
      const im = document.createElement('img');
      im.alt = nm; im.loading = 'lazy'; im.src = url;
      im.onerror = () => { a.classList.add('bad'); a.title = nm + '（读不到原图，可能已被清理）'; };
      a.appendChild(im); wrap.appendChild(a);
    } else {
      const ext = (base.split('.').pop() || '?').toUpperCase().slice(0, 4);
      const a = document.createElement('a');
      a.className = 'ufile'; a.href = url + '&dl=1';
      a.title = nm + '（点击下载）';
      a.innerHTML = '<span class="ubadge">' + esc(ext) + '</span>'
                  + '<span class="uname">' + esc(nm) + '</span>';
      wrap.appendChild(a);
    }
  });
  bub.appendChild(wrap);
  if(pr.rest){
    const p = document.createElement('div'); p.className = 'utext'; p.textContent = pr.rest;
    bub.appendChild(p);
  }
}
function addUser(text){
  if($('#welcome')) $('#welcome').remove();
  const d = document.createElement('div');
  d.className = 'turn me';
  d.innerHTML = '<div class="who">'
    + '<button class="act ed" type="button" title="放回输入框，改完再发">编辑</button>'
    + '<button class="act cp" type="button" title="复制这条消息">复制</button><span>你</span></div>'
    + '<div class="bub"></div>';
  renderUserBub(d.querySelector('.bub'), text);
  d.querySelector('.cp').onclick = () => copyText(text, d.querySelector('.cp'));
  d.querySelector('.ed').onclick = () => {
    if(streaming){ toast('先等当前任务结束或按 Esc 中断'); return; }
    input.value = text; autoGrow(); refreshSend(); input.focus();
    try{ input.selectionStart = input.selectionEnd = input.value.length; }catch(e){}
  };
  root.appendChild(d); toBottom(true);
}
/* ---- 思考过程：逐字流式蹦出来，可点开/收起 ---- */
let thoughtBuf = '', thoughtRAF = null, thoughtDone = false, thoughtPinned = false;
function appendThought(t){
  thoughtBuf += t;
  if(thoughtRAF) return;                 /* rAF 节流：每帧最多渲染一次，字多了也不卡 */
  thoughtRAF = requestAnimationFrame(() => {
    thoughtRAF = null;
    if(!cur) return;
    const box = cur.querySelector('.thinkbox'), body = cur.querySelector('.thinkbody');
    if(!box || !body) return;
    box.style.display = '';
    if(!thoughtDone && !thoughtPinned) box.classList.add('open');   /* 思考时自动展开，用户手动收过就不再弹开 */
    body.innerHTML = esc(thoughtBuf) + (thoughtDone ? '' : '<span class="cur2">▍</span>');
    const cn = cur.querySelector('.thinkhd .cnt');
    if(cn) cn.textContent = thoughtBuf.length + ' 字';
    const near = body.scrollHeight - body.scrollTop - body.clientHeight < 80;
    if(near) body.scrollTop = body.scrollHeight;
    toBottom();
  });
}
/* 开始输出正文时收口：标成"思考完成"并自动折叠（想看随时点开） */
function finishThought(collapse){
  if(thoughtDone) return;
  thoughtDone = true;
  if(!cur) return;
  const box = cur.querySelector('.thinkbox'), body = cur.querySelector('.thinkbody');
  if(!box) return;
  box.classList.add('done');
  if(collapse !== false) box.classList.remove('open');
  if(body) body.textContent = thoughtBuf;
  const ttl = box.querySelector('.ttl');
  if(ttl) ttl.textContent = T.chat_thinking;
  const cn = box.querySelector('.cnt');
  if(cn) cn.textContent = thoughtBuf.length + ' 字';
}
function addBot(showThink){
  const d = document.createElement('div');
  d.className = 'turn bot';
  const name = $('#aname').textContent || '助手';
  d.innerHTML = '<div class="who">' + esc(name)
    + '<button class="act" type="button" title="复制这条回答">复制</button></div>'
    + '<div class="thinkbox" style="display:none">'
    +   '<div class="thinkhd"><span class="arw">▶</span><span class="ttl">思考中…</span><span class="cnt"></span></div>'
    +   '<div class="thinkbody"></div></div>'
    + (showThink === false ? '' : '<div class="think"><span class="sp"></span>思考中…</div>')
    + '<div class="bub" style="display:none"></div>';
  const hd = d.querySelector('.thinkhd');
  hd.onclick = () => {
    const box = d.querySelector('.thinkbox');
    box.classList.toggle('open');
    if(!thoughtDone) thoughtPinned = !box.classList.contains('open');
  };
  d.querySelector('.act').onclick = (e) => {
    const t = (d.querySelector('.bub').innerText || '').trim();
    if(!t){ toast('这条回答还没内容，稍等一下'); return; }
    copyText(t, e.currentTarget);
  };
  root.appendChild(d); toBottom(true);
  return d;
}
function showWelcome(){
  root.innerHTML = '';
  const d = document.createElement('div');
  d.className = 'welcome'; d.id = 'welcome';
  d.innerHTML = `<h1 data-t="hello_h1">有什么可以帮你？</h1><p data-t="hello_p">它在本机运行，可以看文件、跑命令、整理资料</p>
    <div class="chips">
      <button>看看现在磁盘还剩多少空间</button>
      <button>列出下载文件夹里最大的几个文件</button>
      <button>帮我写一个备份照片的脚本</button>
      <button>这台平板装了哪些软件</button>
    </div>`;
  d.querySelectorAll('.chips button').forEach(b => b.onclick = () => { input.value = b.textContent; submit(); });
  root.appendChild(d);
  applyI18N(d);                     /* 欢迎语是动态生成的，生成完立刻按当前语言翻译 */
}
function renderHistory(msgs){
  root.innerHTML = ''; liveSteps = {};
  if(!msgs || !msgs.length){ showWelcome(); return; }
  msgs.forEach(m => {
    if(m.role === 'user') addUser(m.content);
    else if(m.role === 'assistant' && (m.content || '').trim()){
      const d = addBot(false);
      const b = d.querySelector('.bub'); b.style.display = ''; b.innerHTML = render(m.content);
      const rc = m.reasoning_content;      /* 这轮想过什么，也留着 */
      if(rc && String(rc).trim()){
        const box = d.querySelector('.thinkbox');
        box.style.display = ''; box.classList.add('done');
        box.querySelector('.ttl').textContent = T.chat_thinking;
        box.querySelector('.thinkbody').textContent = rc;
        box.querySelector('.cnt').textContent = String(rc).length + ' 字';
      }
    }
  });
  toBottom(true);
}
const STEP_SHOW = 12;   /* 折叠面板里最多渲染多少步，更早的收起来，长任务不刷屏 */
let turnStart = 0;      /* 本轮开始时间，用来在收尾时报「用时」 */
function _elapsed(){
  return turnStart ? ((Date.now() - turnStart) / 1000).toFixed(1) + 's' : '';
}
/* 「思考中…」那行带上实时秒数，长任务一眼看到等了多久 */
let thinkMsg = '思考中…', tickTimer = null;
function setThink(th){
  if(!th || th.style.display === 'none') return;
  th.innerHTML = '<span class="sp"></span>' + esc(thinkMsg)
    + (streaming && turnStart ? ` <span class="el">${_elapsed()}</span>` : '');
}
/* 长任务进度行：气泡下面那行实时报「正在做什么 + 已跑了多久」，用户不用点开也知道还在动 */
function stepsTick(){
  if(!streaming || !steps || !steps.isConnected) return;   /* 只在干活时刷新，收尾后由 finishTurn 定格 */
  const tx = steps.querySelector('.txt');
  if(!tx) return;
  const run = steps.querySelector('li.run .v');
  tx.textContent = (run ? `正在${run.textContent}… · ` : '')
    + `已执行 ${stepsData.length} 步`
    + (_elapsed() ? ` · 已用 ${_elapsed()}` : '') + '（点开查看）';
}
function startTick(){
  clearInterval(tickTimer);
  tickTimer = setInterval(() => {
    if(cur) setThink(cur.querySelector('.think'));
    document.querySelectorAll('.steps li.run').forEach(li => {
      const el = li.querySelector('.el');
      if(el) el.textContent = ((Date.now() - Number(li.dataset.t0 || Date.now())) / 1000).toFixed(1) + 's';
    });
    stepsTick();   /* 气泡上的进度行也跟着走秒 */
  }, 500);
}
/* 结果预览：最多 3 行、每行截断，错误行标红（太长的不刷屏） */
function _stepOut(res){
  const raw = String(res == null ? '' : res).trim();
  if(!raw) return '';
  const lines = raw.split('\n').map(x => x.trim()).filter(x => x);
  if(!lines.length) return '';
  const cut = 130;
  const shown = lines.slice(0, 3).map(x => {
    const t = x.length > cut ? x.slice(0, cut) + '…' : x;
    const e = esc(t);
    return /^\[(错误|未执行|失败|error)/i.test(x) ? `<span class="e">${e}</span>` : e;
  });
  if(lines.length > 3) shown.push(`<span style="color:var(--t4)">…（还有 ${lines.length - 3} 行）</span>`);
  return shown.join('\n');
}
/* ---- 步骤详情：命令原文、工具参数、文件差异（对照 codex 的 patch 展示） ---- */
function diffRows(oldS, newS){
  const A = String(oldS == null ? '' : oldS).split('\n');
  const B = String(newS == null ? '' : newS).split('\n');
  const cap = 240, n = Math.min(A.length, cap), m = Math.min(B.length, cap);
  const dp = [];
  for(let i = 0; i <= n; i++) dp.push(new Int32Array(m + 1));
  for(let i = n - 1; i >= 0; i--)
    for(let j = m - 1; j >= 0; j--)
      dp[i][j] = A[i] === B[j] ? dp[i+1][j+1] + 1 : Math.max(dp[i+1][j], dp[i][j+1]);
  const rows = []; let i = 0, j = 0;
  while(i < n && j < m){
    if(A[i] === B[j]){ rows.push([' ', A[i]]); i++; j++; }
    else if(dp[i+1][j] >= dp[i][j+1]){ rows.push(['-', A[i]]); i++; }
    else { rows.push(['+', B[j]]); j++; }
  }
  while(i < n) rows.push(['-', A[i++]]);
  while(j < m) rows.push(['+', B[j++]]);
  if(A.length > n) rows.push(['-', `…（旧内容还有 ${A.length - n} 行）`]);
  if(B.length > m) rows.push(['+', `…（新内容还有 ${B.length - m} 行）`]);
  return rows;
}
function diffBlock(oldS, newS){
  const rows = diffRows(oldS, newS);
  let add = 0, del = 0;
  const body = rows.map(([k, l]) => {
    if(k === '+') add++; else if(k === '-') del++;
    if(k === ' ' && !String(l).trim()) return '';
    const cls = k === '+' ? 'add' : (k === '-' ? 'del' : 'ctx');
    return `<span class="${cls}">${esc(k === ' ' ? '  ' + l : k + l)}</span>`;
  }).join('');
  return {
    html: `<div class="blk"><div class="bt">改动 <span class="a">+${add}</span> <span class="d">-${del}</span></div>`
          + `<pre class="diff">${body}</pre></div>`,
    stat: `<span class="stat"><span class="a">+${add}</span> <span class="d">-${del}</span></span>`,
  };
}
function stepCopy(t){
  const a = (t && t.args) || {};
  if(a.command) return a.command;
  if(a.patch) return a.patch;
  if(t && t.detail) return String(t.detail);
  try{ return JSON.stringify(a, null, 2); }catch(_){ return ''; }
}
function stepDetail(t){
  const a = (t && t.args) || {}, nm = (t && t.name) || '';
  const blks = []; let stat = '';
  const kvBlock = o => {
    const ks = Object.keys(o).filter(k => o[k] !== '' && o[k] != null);
    if(!ks.length) return;
    blks.push('<div class="blk"><div class="bt">参数</div><pre>'
      + esc(ks.map(k => k + ' = ' + String(o[k])).join('\n')) + '</pre></div>');
  };
  if(nm === 'bash' || nm === 'sysshell'){
    if(a.command) blks.push('<div class="blk"><div class="bt">命令</div><pre>$ ' + esc(a.command) + '</pre></div>');
    kvBlock(Object.fromEntries(Object.entries(a).filter(([k]) => k !== 'command')));
  } else if(nm === 'apply_patch'){
    const txt = String(a.patch || '');
    let add = 0, del = 0;
    const body = txt.split('\n').map(l => {
      if(l.startsWith('+++') || l.startsWith('---')) return `<span class="hdr">${esc(l)}</span>`;
      if(l.startsWith('+')){ add++; return `<span class="add">${esc(l)}</span>`; }
      if(l.startsWith('-')){ del++; return `<span class="del">${esc(l)}</span>`; }
      if(l.startsWith('@@') || l.startsWith('***')) return `<span class="hdr">${esc(l)}</span>`;
      return `<span class="ctx">${esc(l)}</span>`;
    }).join('');
    stat = `<span class="stat"><span class="a">+${add}</span> <span class="d">-${del}</span></span>`;
    blks.push('<div class="blk"><div class="bt">补丁 <span class="a">+' + add
      + '</span> <span class="d">-' + del + '</span></div><pre class="diff">' + body + '</pre></div>');
  } else if(nm === 'edit_file' || nm === 'write_file'){
    const isW = nm === 'write_file';
    const r = diffBlock(isW ? '' : (a.old_string || ''), (isW ? a.content : a.new_string) || '');
    stat = r.stat;
    if(a.path) blks.push('<div class="blk"><div class="bt">文件</div><pre>' + esc(a.path)
      + (a.replace_all ? '   （替换全部匹配）' : '') + '</pre></div>');
    blks.push(r.html);
  } else {
    kvBlock(a);
  }
  if(!blks.length) return null;
  return {html: blks.join(''), stat: stat};
}
let liveSteps = {};        /* key -> 正在执行的步骤行，工具结束后原地改成最终形态 */
function ensureSteps(){
  if(!steps || !steps.isConnected){
    steps = document.createElement('details');
    steps.className = 'steps';
    steps.innerHTML = '<summary><span class="arw">▶</span><span class="txt"></span></summary><ol></ol>';
    root.appendChild(steps);
    stepsElided = 0;
    stepsPinned = false;
    /* 干活的当下自动展开：不然实时输出（pre.live）藏在收起的卡片里，推了也看不见。
       用户点一下 summary 就"钉住"，之后不再自动开合 —— 他显然是在仔细看。 */
    steps.querySelector('summary').addEventListener('click', () => { stepsPinned = true; });
    steps.open = true;
  }
  return steps.querySelector('ol');
}
/* 步骤开始：先亮出一行，边跑边把输出尾巴贴上来（长命令不再是黑箱） */
function stepBegin(d){
  if(!cur) return;
  const ol = ensureSteps();
  const li = document.createElement('li');
  li.className = 'stp run';
  li.dataset.t0 = String(Date.now());
  li.innerHTML = `<div class="hd"><span class="g">●</span>`
    + `<span class="v">${esc(d.label || '')}</span>`
    + (d.detail ? `<span class="a">${esc(d.detail)}</span>` : '')
    + `<span class="el">0.0s</span></div><pre class="live"></pre>`;
  ol.appendChild(li);
  if(d.key != null) liveSteps[d.key] = li;
  const txt = steps.querySelector('.txt');
  if(txt) txt.textContent = `正在${d.label || '执行'}… · 已执行 ${stepsData.length} 步`;
  /* 卡片此时是展开的（见 ensureSteps），实时输出直接看得见 */
  toBottom();
}
/* 步骤输出尾巴（节流由服务端控制，这里直接替换文本，保持最后一行可见） */
function stepOut(d){
  const li = d.key != null ? liveSteps[d.key] : null;
  if(!li) return;
  const pre = li.querySelector('pre.live');
  if(!pre) return;
  pre.textContent = String(d.text || '');
  const near = body.scrollHeight - body.scrollTop - body.clientHeight < 140;
  if(near) toBottom();
}
function pushStep(t){
  stepsData.push(t);
  const ol = ensureSteps();
  let li = (t.key != null && liveSteps[t.key]) ? liveSteps[t.key] : null;
  if(li){
    delete liveSteps[t.key];
    li.classList.remove('run');
    const pre = li.querySelector('pre.live');
    if(pre) pre.remove();
    const el = li.querySelector('.el');
    if(el) el.remove();
    li.innerHTML = '';
  } else {
    li = document.createElement('li');
    li.className = 'stp';
  }
  const bad = !!t.blocked || /^\[(错误|未执行|失败|已停止)/.test(String(t.result || ''));
  const out = _stepOut(t.result);
  const det = stepDetail(t);      /* 展开后可看命令原文 / 文件差异 */
  li.innerHTML = `<div class="hd${bad ? ' err' : ''}${det ? ' clk' : ''}">`
    + `<span class="g">${bad ? ic('xmark') : '•'}</span>`
    + `<span class="v">${esc(t.label)}</span>`
    + (t.detail ? `<span class="a">${esc(t.detail)}</span>` : '')
    + (det ? det.stat : '')
    + (det ? '<span class="cz">▼</span>' : '')
    + `</div>` + (out ? `<div class="out">${out}</div>` : '')
    + (det ? `<div class="det" style="display:none">${det.html}</div>` : '');
  if(det){
    const hd = li.querySelector('.hd');
    hd.onclick = () => {
      const b = li.querySelector('.det');
      const open = b.style.display !== 'none';
      b.style.display = open ? 'none' : '';
      li.classList.toggle('op', !open);
    };
    const cp = document.createElement('button');
    cp.className = 'cpbtn'; cp.type = 'button'; cp.textContent = T.chat_copy;
    cp.onclick = e => { e.stopPropagation(); copyText(stepCopy(t), cp); };
    li.querySelector('.det').appendChild(cp);
  }
  /* 只有新元素才挂进列表。并行工具是乱序返回的，复用旧元素时再 appendChild
     会把它挪到末尾 —— 步骤顺序就乱了。 */
  if(!li.parentNode) ol.appendChild(li);
  const moreLi = ol.querySelector('li.more');
  if(moreLi) ol.insertBefore(moreLi, ol.firstChild);   /* 提示行始终排最前 */
  /* 只显示最近 STEP_SHOW 步，更早的**藏起来而不是删掉** —— 点提示行能翻回来 */
  while(ol.querySelectorAll('li.stp:not(.oldst)').length > STEP_SHOW){
    ol.querySelector('li.stp:not(.oldst)').classList.add('oldst');
    stepsElided++;
  }
  if(stepsElided){
    let more = ol.querySelector('li.more');
    if(!more){
      more = document.createElement('li');
      more.className = 'more';
      more.style.color = 'var(--t4)';
      ol.insertBefore(more, ol.firstChild);
    }
    const _mtxt = () => ol.classList.contains('showall')
      ? `收起更早的 ${stepsElided} 步` : `…更早的 ${stepsElided} 步已折叠（点开看）`;
    more.textContent = _mtxt();
    more.title = T.chat_more_steps;
    more.onclick = () => { ol.classList.toggle('showall'); more.textContent = _mtxt(); toBottom(); };
  }
  /* 标题行始终报进度：卡片收起来时也能一眼知道在干什么 */
  steps.querySelector('.txt').textContent =
    `正在${t.label}… · 已执行 ${stepsData.length} 步`;
  toBottom();
}
function doneAll(){
  if(!steps) return false;
  const el = _elapsed();
  steps.querySelector('.txt').textContent =
    (stepsData.length === 1 ? '已完成 1 步操作' : `已完成 ${stepsData.length} 步操作`)
    + (el ? ` · 用时 ${el}` : '')
    + (stepsPinned ? '' : '（点开看细节）');
  /* 干完了就收起来，别让历史被一长串步骤撑开；
     但用户自己点开过就不动它 —— 他显然正在看。 */
  if(!stepsPinned) steps.open = false;
  return true;
}

/* ---------------- 发送 / 停止 / 待发送队列 ---------------- */
function autoGrow(){ input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, window.innerHeight * 0.32) + 'px'; }
let slowTimer = null;
function armSlowHint(){
  clearTimeout(slowTimer);
  slowTimer = setTimeout(() => {
    if(!streaming || !cur) return;
    const th = cur.querySelector('.think');
    if(th && th.style.display !== 'none')
      th.innerHTML = '<span class="sp"></span>' + T.chat_slow;
  }, 25000);
}
let pending = [];   /* 助手正在干活时先排队的消息（存浏览器本地，刷新不丢） */
const PQ_KEY = 'pendingMsgs';
function pqLoad(){
  try{
    const a = JSON.parse(localStorage.getItem(PQ_KEY) || '[]');
    if(Array.isArray(a)) pending = a.map(x => typeof x === 'string' ? {t:x, s:x} : x)
                                  .filter(x => x && typeof x.t === 'string' && x.t);
  }catch(e){ pending = []; }
}
function pqSave(){ try{ localStorage.setItem(PQ_KEY, JSON.stringify(pending)); }catch(e){} }
function renderPending(){
  const box = $('#pq');
  box.innerHTML = '';
  if(!pending.length){ box.style.display = 'none'; return; }
  box.style.display = '';
  const h = document.createElement('div'); h.className = 'pq-h';
  const lb = document.createElement('span'); lb.textContent = T.queue_pending + pending.length + ' 条（当前任务结束后自动发出）';
  const clr = document.createElement('button'); clr.className = 'pq-clr'; clr.type = 'button'; clr.textContent = T.queue_clear;
  clr.onclick = () => { pending = []; pqSave(); renderPending(); refreshSend(); };
  h.appendChild(lb); h.appendChild(clr); box.appendChild(h);
  pending.forEach((it, i) => {
    const c = document.createElement('div'); c.className = 'pq-item';
    const s = document.createElement('span'); s.className = 'pq-t'; s.textContent = it.s || it.t;
    const q = document.createElement('button'); q.className = 'pq-x'; q.type = 'button'; q.textContent = T.queue_inject;
    q.title = T.queue_inject_title;
    q.style.cssText = 'width:auto;padding:0 8px;margin-right:4px;font-size:12px';
    q.onclick = () => {
      if(!streaming){ toast('当前没有在跑的任务，直接发就行'); return; }
      wsSend({cmd:'inject', message: it.t});
      const k = pending.indexOf(it); if(k >= 0) pending.splice(k, 1);
      pqSave(); renderPending(); refreshSend();
      toast('已插队，任务不会中断');
    };
    const x = document.createElement('button'); x.className = 'pq-x'; x.type = 'button'; x.textContent = '×'; x.title = T.queue_remove;
    x.onclick = () => { pending.splice(i, 1); pqSave(); renderPending(); refreshSend(); };
    c.appendChild(s); c.appendChild(q); c.appendChild(x); box.appendChild(c);
  });
}
function queueMsg(text, show){
  pending.push({t: text, s: show || text}); pqSave(); renderPending(); refreshSend();
  toast('已加入待发送（' + pending.length + ' 条）');
}
/* 待发送队列：出队前先确认真的发出去了，发不出去就放回队首 —— 避免"凭空消失" */
let flushing = false;               /* 防重入：任务结束那一瞬间可能和手动发送撞车 */
function flushPending(){
  if(streaming || !pending.length || flushing) return;
  flushing = true;
  const it = pending.shift();
  pqSave(); renderPending(); refreshSend();
  let ok = false;
  try{
    ok = sendText(it.t, it.s) !== false;
  } finally {
    if(!ok){
      pending.unshift(it);        /* 没发出去 → 原样放回，别让它凭空没了 */
      pqSave(); renderPending(); refreshSend();
      toast('刚才那条没发出去，已放回待发送队列');
    }
    flushing = false;
  }
}
function sendText(text, show){
  if(!text){ toast('这条是空的，没发出去'); return false; }
  addUser(text);   /* 附件标记要能被解析成缩略图，所以用真正存进对话的那份 */
  lastError = false; streaming = true; setBusy(true); busySid = curSidVal;
  curText = ''; steps = null; stepsData = []; stepsElided = 0; liveSteps = {}; stepsPinned = false;
  thoughtBuf = ''; thoughtDone = false; thoughtRAF = null; thoughtPinned = false;
  turnStart = Date.now(); thinkMsg = '思考中…'; startTick();
  cur = addBot(true);
  wsSend({cmd:'chat', message: text});
  armSlowHint();
  return true;
}
/* ---------------- 图片附件 ---------------- */
let attach = null;                 /* {name, data(base64), url(本地预览)} */
let uplChain = Promise.resolve();  /* 上传串行，保证先后顺序 */
function renderAtt(){
  const box = $('#att');
  box.innerHTML = '';
  if(!attach){ box.style.display = 'none'; return; }
  box.style.display = '';
  if(attach.url){
    const im = document.createElement('img'); im.alt = ''; im.src = attach.url;
    box.appendChild(im);
  } else if(attach.file){
    const fb = document.createElement('span'); fb.className = 'ic';
    fb.textContent = (String(attach.name).split('.').pop() || '?').toUpperCase().slice(0, 4);
    fb.title = T.file;
    box.appendChild(fb);
  } else {
    const icEl = document.createElement('span'); icEl.className = 'ic';
    icEl.innerHTML = ic('image');
    icEl.title = T.file_no_preview;
    box.appendChild(icEl);
  }
  const n = document.createElement('span'); n.className = 'n'; n.textContent = attach.name;
  if(attach.info) n.title = attach.info;
  const x = document.createElement('button'); x.className = 'x'; x.type = 'button';
  x.textContent = '×'; x.title = T.img_remove;
  x.onclick = () => { attach = null; renderAtt(); refreshSend(); };
  box.appendChild(n); box.appendChild(x);
}
function pickImage(){ $('#filein').click(); }
/* 浏览器里先缩一遍：长边不超过 1600、转 JPEG，省流量也让模型更快 */
function shrinkImage(file, cb){
  const url = URL.createObjectURL(file);
  const img = new Image();
  const done = r => { try{ URL.revokeObjectURL(url); }catch(e){} cb(r); };
  img.onload = () => {
    try{
      const MAX = 1600;
      const ow = img.naturalWidth, oh = img.naturalHeight;
      const sc = Math.min(1, MAX / Math.max(ow, oh));
      const w = Math.max(1, Math.round(ow * sc)), h = Math.max(1, Math.round(oh * sc));
      const c = document.createElement('canvas'); c.width = w; c.height = h;
      const g = c.getContext('2d');
      g.fillStyle = '#fff'; g.fillRect(0, 0, w, h);   /* 透明区铺白，JPEG 没透明通道 */
      g.drawImage(img, 0, 0, w, h);
      const out = c.toDataURL('image/jpeg', 0.85);
      const name = (file.name || 'image').replace(/\.[^.]+$/, '') + '.jpg';
      done({name: name, data: out.split(',')[1] || '', url: out,
            info: ow + '×' + oh + ' → ' + w + '×' + h + '，已压缩'});
    }catch(e){ done(null); }
  };
  img.onerror = () => done(null);   /* 浏览器解不了（如 HEIC）：原样上传，服务端转 */
  img.src = url;
}
function onFilePicked(e){
  const f = (e.target.files || [])[0];
  e.target.value = '';
  if(!f) return;
  const heic = /\.(heic|heif)$/i.test(f.name || '');
  const isImg = (f.type && f.type.indexOf('image/') === 0) || heic;
  if(f.size > 30 * 1024 * 1024){ toast('文件太大（上限 30MB）'); return; }
  if(!isImg){
    /* 2026-10-01：非图片也能发。文件会落到 uploads/ 并把路径写进对话，
       模型需要时用 read_file 去读。base64 传输会膨胀 1/3，所以这里限 8MB。 */
    if(f.size > 8 * 1024 * 1024){ toast('非图片文件上限 8MB'); return; }
    const fr0 = new FileReader();
    fr0.onload = () => {
      const u0 = String(fr0.result || '');
      attach = {name: f.name || 'file.bin', data: u0.split(',')[1] || '', url: '', file: true};
      renderAtt(); refreshSend();
      toast('已选择文件：' + (f.name || ''));
    };
    fr0.onerror = () => toast('读取文件失败');
    fr0.readAsDataURL(f);
    return;
  }
  const fallback = () => {   /* 原样读成 base64，交给服务端处理 */
    const fr = new FileReader();
    fr.onload = () => {
      const u = String(fr.result || '');
      attach = {name: f.name || 'image.heic', data: u.split(',')[1] || '', url: ''};
      renderAtt(); refreshSend();
      toast('已选择图片（服务端将自动转换）');
    };
    fr.onerror = () => toast('读取图片失败');
    fr.readAsDataURL(f);
  };
  shrinkImage(f, r => {
    if(r){ attach = r; renderAtt(); refreshSend(); }
    else { fallback(); }
  });
}
function uploadAndSend(a, text){
  toast('正在上传图片…');
  uplChain = uplChain.then(() => fetch('/api/upload', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: a.name, data: a.data})
    }).then(r => r.json()).then(j => {
      if(!j || !j.path){ toast('图片上传失败'); return; }
      if(j.note) toast(j.note);
      const isImg = !a.file;
      const full = (isImg ? '[我发了一张图片，已保存到 ' : '[我发了一个文件，已保存到 ')
        + j.path + ']' + (text ? '\n' + text : '');
      const show = (isImg ? '图片 ' : '文件 ') + a.name + (text ? '\n' + text : '');
      if(streaming) queueMsg(full, show); else sendText(full, show);
    })).catch(() => { toast('图片上传失败'); });
}
let inputHist = [], histIdx = -1;      /* ↑ / ↓ 翻自己发过的消息 */
function submit(){
  let text = input.value.trim();
  const a = attach;
  /* 空输入 + 没附件 → 直接发「继续」：
     重启 / 中断之后不必再手打一遍，点一下发送就能接着做。 */
  if(!text && !a){
    text = '继续';
    input.value = ''; autoGrow(); hideFilep(); hideCmdp();
    if(streaming){ queueMsg(text); return; }
    sendText(text);
    return;
  }
  if(text){ inputHist.push(text); if(inputHist.length > 50) inputHist.shift(); }
  histIdx = -1;
  input.value = ''; autoGrow(); hideFilep(); hideCmdp();
  if(a){ attach = null; renderAtt(); refreshSend(); uploadAndSend(a, text); return; }
  if(streaming){ queueMsg(text); return; }   /* 正在干活：排队，不打断 */
  sendText(text);
}
/* 上下文占用条 */
function setCtx(p, d){
  /* p = 距阈值百分比（旧的数字调用仍兼容）；d = 完整面板数据（可选，给了就能显示更全的信息）。
     圆环按「占模型窗口的百分比」画 —— 这才是「用了多少」的直觉口径；
     压缩阈值（比如 40%）就是环上应该留意的位置。 */
  var chars = (d && d.context_chars) || 0;
  var winTok = (d && d.model_window) || 0;
  var ratio = (d && d.context_percent_used) || 70;          /* 达到多少 % 会压缩 */
  var winPct = (d && typeof d.window_percent === 'number') ? d.window_percent : null;
  var ringPct = (winPct === null) ? (Number(p) || 0) : winPct;
  ringPct = Math.max(0, Math.min(100, ringPct));

  /* 圆环：周长 2πr = 2π×13 ≈ 81.68 */
  var ringEl = document.getElementById('ctxring');
  if(ringEl){
    ringEl.style.strokeDashoffset = (81.68 * (1 - ringPct / 100)).toFixed(2);
    ringEl.style.stroke = (ringPct >= ratio) ? 'var(--err)'
                         : (ringPct >= ratio * 0.8 ? '#d69e2e' : 'var(--brand)');
  }
  var pc = document.getElementById('ctxpct');
  if(pc) pc.textContent = Math.round(ringPct) + '%';

  var tx = document.getElementById('ctxtxt');
  if(tx){
    var fmt = function(n){
      n = Math.round(Number(n) || 0);
      if(n >= 100000) return (n / 10000).toFixed(1) + ' 万';
      if(n >= 10000)  return (n / 10000).toFixed(2) + ' 万';
      return String(n);
    };
    var used = (d && typeof d.context_tokens === 'number') ? d.context_tokens : 0;
    if(!winTok){
      tx.textContent = T.ctx_ring0;
    } else {
      /* 单位统一用 token：窗口是模型真实值（云端 /models、本地 /props 或按模型名推断），
         已用 token 由字符数换算而来（1 token ≈ 1.5 字），所以带个 ≈。 */
      tx.textContent = T.ctx_used + fmt(used) + ' / ' + fmt(winTok) + ' token · '
                     + Math.round(ringPct) + T.ctx_compress_at_prefix + ratio + T.ctx_compress_at;
    }
  }
  /* 兼容：旧的线性条若还留在页面上，也一起同步 */
  var f = document.getElementById('ctxfill');
  if(f){
    var q = Math.max(0, Math.min(100, Math.round(Number(p) || 0)));
    f.style.width = q + '%';
    f.style.background = q >= 80 ? 'var(--err)' : 'var(--brand)';
  }
}
/* 按输入框里有没有内容，决定按钮是「发送」还是「停止」 */
function refreshSend(){
  const has = input.value.trim().length > 0 || !!attach;
  const hint = $('#hint');
  if(hint){
    hint.textContent = streaming
      ? (has ? T.in_hint_queue : T.in_hint_stop)
      : T.in_hint_full;
  }
  if(streaming && has){
    send.classList.remove('stop'); send.textContent = T.in_send; send.onclick = submit;
  } else if(streaming){
    send.classList.add('stop');
    if(send.dataset.stopping === '1'){
      /* 已经请求过停止：显示明确状态，别让人以为按钮卡死了 */
      send.textContent = T.in_stopping;
      send.onclick = null;
    } else {
      send.textContent = T.in_stop;
      send.onclick = () => {
        send.dataset.stopping = '1';
        wsSend({cmd:'stop'});
        toast('已请求停止，正在中断…');
        refreshSend();
        setTimeout(() => checkRealBusy(false), 1200);   /* 后端已能秒断，很快核对一次 */
        setTimeout(() => checkRealBusy(false), 5000);
        setTimeout(() => {
          /* 20 秒还没复位 → 真卡住了：解锁按钮让他能再点，并说清楚 */
          if(streaming){
            send.dataset.stopping = '';
            toast('任务仍在收尾，可以再点一次「停止」', 4000);
            refreshSend();
          }
        }, 20000);
      };
    }
  } else {
    send.dataset.stopping = '';
    send.classList.remove('stop'); send.textContent = T.in_send; send.onclick = submit;
  }
}
function setBusy(on){
  dot.className = on ? 'dot busy' : (lastError ? 'dot err' : 'dot');
  send.disabled = false;
  refreshSend();
}
/* 一轮结束时，还在跑、或被打断的步骤行就地收口，别让转圈一直转下去 */
function finishLiveSteps(){
  Object.keys(liveSteps).forEach(k => {
    const li = liveSteps[k];
    delete liveSteps[k];
    if(!li || !li.isConnected) return;
    li.classList.remove('run');
    const el = li.querySelector('.el');
    if(el) el.remove();
    const pre = li.querySelector('pre.live');
    if(pre && !pre.textContent.trim()) pre.remove();
    const g = li.querySelector('.hd .g');
    if(g) g.innerHTML = ic('stop');
    if(!li.querySelector('.stopnote')){
      const sp = document.createElement('span');
      sp.className = 'a stopnote';
      sp.textContent = T.chat_interrupted;
      const v = li.querySelector('.hd .v');
      if(v) v.insertAdjacentElement('afterend', sp); else li.querySelector('.hd').appendChild(sp);
    }
  });
}
/* 任务完成提醒：任务跑完时人没在看（页面被切走、或正在往上翻历史），
   就在标题栏（对话名左边）冒个蓝色小圆点；点它、或点屏幕任意处即消失 */
const doneDot = document.createElement('span');
doneDot.id = 'doneDot';
doneDot.title = T.chat_done_title;
doneDot.style.cssText = 'display:none;flex:0 0 auto;width:9px;height:9px;border-radius:50%;'
  + 'background:var(--brand);box-shadow:0 0 0 3px rgba(65,118,230,.25);cursor:pointer';
(function initDoneDot(){
  const t = $('#topname');
  if(t && t.parentNode) t.parentNode.insertBefore(doneDot, t);
})();
function notiOn(){ doneDot.style.display = ''; }
function notiOff(){ doneDot.style.display = 'none'; }
doneDot.onclick = () => { notiOff(); toBottom(true); };
document.addEventListener('pointerdown', notiOff, {passive:true});
function finishTurn(){
  clearTimeout(slowTimer); clearInterval(tickTimer); tickTimer = null;
  const reply = (curText || '').trim();   /* 这一轮的回答正文，自动朗读要用 */
  if(busySid === curSidVal) busySid = '';
  finishThought();          /* 思考面板收口（内容留着，点标题可再展开） */
  finishLiveSteps();
  streaming = false; setBusy(false);
  if(document.hidden || !atBottom()) notiOn(); else notiOff();
  if(cur){
    const b = cur.querySelector('.bub');
    if(b && b.style.display !== 'none') b.innerHTML = render(curText);
    const th = cur.querySelector('.think');
    if(th && th.style.display !== 'none' && !curText && !stepsData.length) th.innerHTML = T.chat_no_content;
  }
  if(stepsData.length){
    doneAll();
    /* 回答结束只更新标题行文字；开合完全交给用户，不替他收起 */
    if(steps && steps.isConnected){
      const tx = steps.querySelector('.txt');
      if(tx) tx.textContent = (stepsData.length === 1 ? '已完成 1 步操作' : `已完成 ${stepsData.length} 步操作`)
        + (_elapsed() ? ` · 用时 ${_elapsed()}` : '') + '（点开查看）';
    }
  }
  cur = null; toBottom();
   /* 有排队的消息，接着发 */
}

/* ---------------- 服务端事件 ---------------- */
function handleMsg(m){
  const ev = m.ev, d = m.d;
  lastEvtAt = Date.now();          /* 服务端还有动静，看门狗据此判断"确实在跑" */
  if(ev === 'hello'){
    /* 页面刚连上：把外观设置要过来（存在 config.json，刷新后要立刻恢复） */
    if(!uiRange) wsSend({cmd:'get_config'});
    /* 服务重启过 → 手上这份页面代码已经过期，自动重载 */
    if(d.boot_id){
      if(!window.__bootId) window.__bootId = d.boot_id;
      else if(window.__bootId !== d.boot_id){ location.reload(); return; }
    }
    /* 任务收尾、别的窗口切会话时会推来「不属于当前对话」的 hello；
       认领它会把视图和侧栏高亮一起拽走 —— 不是我要的那个会话就当作没看见。 */
    if(d.session_id && curSidVal && d.session_id !== curSidVal && d.session_id !== wantSid){
      return;
    }
    wantSid = '';
    $('#aname').textContent = d.name || 'Sidekick';
    $('#topname').textContent = d.name || 'Sidekick';
    document.title = d.name || 'Sidekick';
    curSidVal = d.session_id || curSidVal;
    /* 只有"当前这个对话"在回复时才显示停止；别的对话在跑不影响这里 */
    if(d.busy && busySid !== curSidVal) busySid = curSidVal;
    if(!d.busy && busySid === curSidVal) busySid = '';
    if(busySid !== curSidVal){
      streaming = false; cur = null; setBusy(false);
    } else {
      streaming = true; setBusy(true);
    }
    $('#model').textContent = shortModel(d.model);
    $('#topmodel').textContent = shortModel(d.model);
    $('#topmodel').className = 'chip' + (d.shell_access ? '' : ' warn');
    /* 版本号露出来，并且只在版本变了时提醒一次「更新了什么」 */
    const tv = $('#topver');
    if(tv && d.version){
      tv.textContent = 'v' + d.version;
      const seen = localStorage.getItem('seenVersion');
      if(seen && seen !== d.version){
        const c = d.changelog || [];
        const hit = c.find(x => x.version === d.version) || c[0];
        toast(`已更新到 v${d.version}${hit ? '：' + hit.summary : ''}（设置页「更新记录」看全部）`, 9000);
      }
      localStorage.setItem('seenVersion', d.version);
    }
    setCtx(d.context_percent, d);   /* 把整份面板数据也给过去，圆环与文字才拿得到字数/窗口/阈值 */
    renderTodo(d.todo);      /* 重连/切会话后恢复未完成的任务清单 */
    return;
  }
  if(ev === 'todo'){ renderTodo(d); return; }
  if(ev === 'ask'){ showAsk(d); return; }
  if(ev === 'ask_done'){ closeAsk(); return; }
  if(ev === 'evolve_suggestions'){ renderEvoCaps(d); return; }
  if(ev === 'history'){
    const hm = (d && d.msgs) ? d : {msgs: d || []};
    if(hm.sid && curSidVal && hm.sid !== curSidVal) return;   /* 别的会话的历史，别覆盖这里 */
    renderHistory(hm.msgs || []);
    if(findOpen() && findLastQ) runFind(findLastQ);   /* 历史重绘后恢复搜索高亮 */
    return;
  }
  if(ev === 'sessions'){ renderSessions(d); return; }
  if(ev === 'panel'){ fillPanel(d); return; }
  if(ev === 'config'){
    fillConf(d);
    if(d.ui_defaults) uiDefaults = d.ui_defaults;
    /* 2026-10-01：这里以前无条件 applyAppearance(d.ui) + uiDirty = false，
       而「设置」按钮（#openconf）每次点击都会 wsSend(get_config) —— 于是
       「点了预设还没落盘、又点一下设置」就会把预览悄悄冲回磁盘里的旧值
       （实测：点「宽松」→ 再点设置 → 6 秒后弹回「标准」）。
       现在：服务端那份跟本地一致（也就是保存回执到了）就直接采纳；
       不一致、且用户刚动过手（2 秒内）则保住本地值 —— 别把他正在拖的东西拽回去。 */
    if(uiVals && Date.now() - uiLastEdit < 2000 && !uiSame(d.ui, uiVals)){
      renderAppearance(); syncUI();
    } else {
      applyAppearance(d.ui);
      uiSaved = Object.assign({}, d.ui);
      uiSaving = false;
      uiDirty = !uiSame(uiVals, uiSaved);
      renderAppearance(); syncUI();
    }
    return;
  }
  if(ev === 'models'){ onModels(d); return; }
  if(ev === 'balance'){ onBalance(d); return; }
  if(ev === 'files'){                      /* @ 补全的文件清单到了 */
    fileList = (d && d.list) || []; fileRoot = (d && d.root) || ''; fileAsked = true;
    if(fileQuery()) fileFilter();
    return;
  }
  if(ev === 'panel_saved'){ toast('已保存' + ((d||[]).length ? '：' + d.join('、') : '')); return; }
  if(ev === 'info'){ toast(d); return; }
  if(ev === 'error'){ toast(d, 5200); return; }
  if(ev === 'evolve_state'){ $('#evo').style.display = d.running ? '' : 'none'; return; }
  if(ev === 'evolve_done'){ toast('进化完成：' + String(d).split('\n')[0].slice(0,60), 7000); wsSend({cmd:'panel'}); return; }
  if(ev === 'evt'){
    const sid = (d && d.sid) || '';
    if(sid && curSidVal && sid !== curSidVal) return;   /* 别的对话在说话，这边不动 */
    onAgentEvent(d);
    return;
  }
}
function shortModel(m){ return (m || '').replace(/^deepseek-/, ''); }

/* ---------------- 任务清单：拆步骤、打勾、点标题展开 ---------------- */
let todoList = [], todoOpen = false, todoSig = '', todoManual = false;
function setTodoOpen(o){
  todoOpen = !!o;
  const b = $('#tdl'); if(b) b.classList.toggle('open', todoOpen);
  const ar = $('#tdl-ar'); if(ar) ar.textContent = todoOpen ? '▾' : '▸';
}
function renderTodo(d){
  const box = $('#tdl'); if(!box) return;
  const items = (d && d.items) || [];
  const inSig = items.map(x => (x.done ? '1' : '0') + x.text).join('|');
  if(!items.length){
    box.style.display = 'none'; setTodoOpen(false);
    todoList = []; todoSig = ''; todoManual = false;
    return;
  }
  const prevTexts = todoList.map(x => x.text).join('|');   /* 换清单前先记住旧的条目 */
  todoList = items;
  const total = todoList.length, done = todoList.filter(x => x.done).length;
  box.style.display = '';
  $('#tdl-p').textContent = `${done}/${total}` + (done === total ? ' 已完成' : '');
  const bar = $('#tdl-i');
  bar.style.width = Math.round(done * 100 / total) + '%';
  bar.style.background = done === total ? 'var(--ok)' : 'var(--brand)';
  const now = todoList.findIndex(x => !x.done);
  $('#tdl-b').innerHTML = todoList.map((x, i) =>
    `<div class="tdl-i${x.done ? ' ok' : (i === now ? ' now' : '')}">`
    + `<span class="m">${x.done ? ic('check') : (i === now ? '▶' : '○')}</span>`
    + `<span class="t">${esc(x.text)}</span></div>`).join('');
  const texts = todoList.map(x => x.text).join('|');
  const isNewList = texts !== prevTexts;    /* 条目本身变了 = 来了一份新清单 */
  const sig = todoList.map(x => (x.done ? '1' : '0') + x.text).join('|');
  const changed = sig !== todoSig; todoSig = sig;
  if(isNewList) todoManual = false;         /* 新清单 → 忘掉"用户手动折叠过"，直接换成新的 */
  if(done === total){
    /* 全部做完：只收成一行，清单本体留着 —— 不自动隐藏、不记忆，
       刷新/重连/切回来都还在，点标题随时能展开看结果。 */
    if(!todoManual) setTodoOpen(false);
  }
  else if(isNewList || (changed && !todoManual)){ setTodoOpen(true); }
  else setTodoOpen(todoOpen);
}
function todoBind(){
  const h = $('#tdl-h'), x = $('#tdl-x');
  if(h) h.onclick = () => {
    setTodoOpen(!todoOpen);
    if(!todoOpen && todoList.some(i => !i.done)) todoManual = true;  /* 用户主动折叠，别自动弹开 */
  };
  if(x) x.onclick = (e) => {
    e.stopPropagation();
    todoList = []; todoSig = ''; todoManual = false; renderTodo(null);
    wsSend({cmd:'todo_clear'});
  };
}
todoBind();

/* ---------------- 需要你拍板：选择面板 ---------------- */
let askNow = null;
function closeAsk(){
  askNow = null;
  const b = $('#ask');
  if(b){ b.style.display = 'none'; b.innerHTML = ''; }
}
function showAsk(d){
  const box = $('#ask'); if(!box) return;
  const qs = (d && d.questions) || [];
  if(!qs.length) return;
  askNow = {id: d.id, qs: qs, sel: qs.map(q => q.multi ? [] : ''), txt: qs.map(() => '')};
  box.style.display = '';
  box.innerHTML = '<div class="ask-h"><span class="dot"></span>' + T.tool_need_pick + '</div><div class="ask-b">'
    + qs.map((q, i) =>
        '<div class="ask-q"><div><span class="n">' + (i + 1) + '.</span>' + esc(q.question) + '</div>'
        + ((q.options && q.options.length)
            ? '<div class="ask-opts">' + q.options.map((o, j) =>
                '<button class="ask-o" type="button" data-q="' + i + '" data-o="' + j + '">'
                + esc(o) + '</button>').join('') + '</div>'
            : '')
        + (q.allow_text
            ? '<textarea class="ask-ta" data-t="' + i + '" rows="1" placeholder="也可以直接写在这里…"></textarea>'
            : '')
        + '</div>').join('')
    + '</div><div class="ask-f"><button class="ask-send" id="ask-go" type="button">提交</button>'
    + '<span class="ask-tip">选完点提交；只答其中几个也行</span></div>';
  box.querySelectorAll('.ask-o').forEach(b => {
    b.onclick = () => {
      const i = +b.dataset.q, j = +b.dataset.o, q = askNow.qs[i];
      if(q.multi){
        const at = askNow.sel[i].indexOf(j);
        if(at >= 0) askNow.sel[i].splice(at, 1); else askNow.sel[i].push(j);
      } else {
        askNow.sel[i] = (askNow.sel[i] === j) ? '' : j;
      }
      box.querySelectorAll('.ask-o[data-q="' + i + '"]').forEach(x =>
        x.classList.toggle('on', q.multi ? askNow.sel[i].indexOf(+x.dataset.o) >= 0
                                        : askNow.sel[i] === +x.dataset.o));
    };
  });
  box.querySelectorAll('.ask-ta').forEach(t => {
    t.oninput = () => { askNow.txt[+t.dataset.t] = t.value; };
    t.onkeydown = e2 => { if(e2.key === 'Enter' && !e2.shiftKey){ e2.preventDefault(); sendAsk(); } };
  });
  const go = $('#ask-go'); if(go) go.onclick = sendAsk;
  const ta = box.querySelector('.ask-ta'); if(ta) ta.focus();
}
function sendAsk(){
  if(!askNow) return;
  const answers = askNow.qs.map((q, i) => {
    const parts = [];
    const sel = askNow.sel[i];
    if(Array.isArray(sel)) sel.slice().sort((a, b) => a - b).forEach(j => parts.push(q.options[j]));
    else if(sel !== '' && sel !== undefined) parts.push(q.options[sel]);
    const txt = (askNow.txt[i] || '').trim();
    if(txt) parts.push(txt);
    return parts.join('；');
  });
  const id = askNow.id;
  closeAsk();
  wsSend({cmd: 'ask_reply', id: id, answers: answers});
  toast('已提交你的选择');
}

function onAgentEvent(e){
  if(!cur) return;
  const think = cur.querySelector('.think'), b = cur.querySelector('.bub');
  if(e.k === 'reasoning'){
    appendThought(e.v);
  } else if(e.k === 'text'){
    if(think) think.style.display = 'none';
    finishThought();                       /* 正文开始了，思考段落收口并折叠 */
    b.style.display = ''; curText += e.v;
    b.innerHTML = render(curText) + '<span class="cursor"></span>';
    toBottom();
  } else if(e.k === 'step_begin'){
    if(think) think.style.display = 'none';
    finishThought();
    stepBegin(e.v);
  } else if(e.k === 'step_out'){
    stepOut(e.v);
  } else if(e.k === 'tool'){
    if(think) think.style.display = 'none';
    if(!curText) b.style.display = 'none';
    pushStep(e.v);
  } else if(e.k === 'status'){
    if(think){ thinkMsg = e.v; think.style.display = ''; setThink(think); }
    toBottom();
  } else if(e.k === 'error'){
    lastError = true; finishLiveSteps(); if(think) think.style.display = 'none';
    b.style.display = ''; curText += (curText ? '\n\n' : '') + '出错了：' + e.v;
    b.innerHTML = render(curText); dot.className = 'dot err'; toBottom();
  } else if(e.k === 'notice'){
    if(think) think.style.display = 'none';
    finishLiveSteps();
    if(!curText) b.style.display = 'none';
    const nStyle = 'margin:6px 0 2px;padding:6px 10px;font-size:12.5px;line-height:1.5;'
      + 'color:var(--t3);background:var(--bg-side);border-left:3px solid var(--bd);'
      + 'border-radius:var(--r);white-space:pre-wrap;word-break:break-word';
    /* ---- 插队消息：这是"你这边发出的消息"，要以用户消息的样子独立显示，
       不能混进上面的工具调用列表。位置放在当前命令块之后；
       插完把当前命令块收口（steps=null），后续新命令会另起一块排在它下面。 ---- */
    if(String(e.v || '').indexOf('已插队你的消息：') === 0){
      const txt = String(e.v).replace('已插队你的消息：', '');
      const d = document.createElement('div');
      d.className = 'turn me';
      d.innerHTML = '<div class="who"><span>' + T.chat_you_inject + '</span></div><div class="bub"></div>';
      d.querySelector('.bub').textContent = txt;
      if(steps && steps.isConnected && steps.parentNode){
        steps.parentNode.insertBefore(d, steps.nextSibling);
        steps = null;                     /* 收口：新命令另起一块，排在它下面 */
      } else {
        root.appendChild(d);
      }
      toBottom(true);
      return;
    }
    /* ---- 其它系统提示：轻量提示条（同样不插进工具列表） ---- */
    const n = document.createElement('div');
    n.className = 'notice';
    n.textContent = e.v;
    n.style.cssText = nStyle;
    if(steps && steps.isConnected && steps.parentNode){
      steps.parentNode.insertBefore(n, steps.nextSibling);
    } else {
      root.insertBefore(n, cur);
    }
    toBottom();
  } else if(e.k === 'done'){
    finishTurn();
  }
}

/* ---------------- 侧栏会话列表 ---------------- */
let arOpen = false, lastSess = null;
/* 模型短名：deepseek-flash → flash；本地模型标注「本地」 */
function sessModel(m){
  if(!m) return '';
  const local = /127\.0\.0\.1|localhost/.test(String(m)) || m.indexOf('lfm') === 0;
  let n = shortModel(m);
  if(n.length > 14) n = n.slice(0, 13) + '…';
  return local ? '本地' : n;
}
function sessActions(btns){
  const box = document.createElement('div'); box.className = 'iacts';
  btns.forEach(b => {
    const el = document.createElement('button');
    el.type = 'button';
    if(b.icon) el.innerHTML = ic(b.icon); else el.textContent = b.text;
    if(b.title) el.title = b.title;
    if(b.cls) el.className = b.cls;
    el.onclick = (e) => { e.stopPropagation(); b.act(); };
    box.appendChild(el);
  });
  return box;
}
function renderSessions(d){
  lastSess = d;
  const list = $('#list'); list.innerHTML = '';
  const cur = d.current, ss = d.sessions || [], ar = d.archived || [];
  if(!ss.length && !ar.length){ list.innerHTML = '<div class="grp">' + T.sess_none + '</div>'; return; }
  const grp1 = document.createElement('div'); grp1.className = 'grp';
  grp1.textContent = T.sess_recent; list.appendChild(grp1);
  if(!ss.length){
    const e = document.createElement('div'); e.className = 'grp'; e.textContent = T.sess_empty;
    list.appendChild(e);
  }
  ss.forEach(s => {
    const it = document.createElement('div');
    it.className = 'item' + (s.id === cur ? ' cur' : '') + (s.pinned ? ' pinned' : '');
    if(s.pinned){                      /* 置顶高亮：左侧金条 + 淡金渐变背景 */
      it.style.boxShadow = 'inset 3px 0 0 #ffb300';          /* 内阴影当金条：贴卡片内侧，圆角处不出边 */
      it.style.backgroundColor = 'rgba(255,179,0,.10)';      /* 淡金底，盖掉选中态那层灰 */
      /* 只设背景图，别用 background 简写：简写会把 background-color 重置成
          transparent（内联优先级最高），从而盖掉选中态高亮，也让深色主题下的
          正在聊的那条（.cur 底色是灰的），黄渐变浮在灰底上会显脏，所以这里用内联直接钉死淡金底。 */
      it.style.backgroundImage = 'linear-gradient(90deg, rgba(255,179,0,.16), transparent)';
    }
    it.innerHTML = '<div class="r1"><div class="t">'
      + (s.pinned ? '<svg class="ic" viewBox="0 0 24 24" style="margin-right:4px;color:#ffb300;'
          + 'vertical-align:middle" aria-hidden="true">'
          + '<path d="M9 3.5h6l-1 5 3.2 2.6v1.4h-4.4V20l-.8 1-.8-1v-7.5H6.8v-1.4L10 8.5z"/></svg>' : '')
      + esc(s.title || '（新对话）') + '</div></div>'
      + '<div class="m">' + esc((s.updated_at || '').slice(5,16)) + ' · ' + s.messages + ' 条'
      + (s.model ? ' · <span class="mm">' + esc(sessModel(s.model)) + '</span>' : '') + '</div>';
    it.querySelector('.r1').appendChild(sessActions([
      {text: s.pinned ? '取消置顶' : '置顶',
       title: '置顶后会排在最前面，并用金色高亮',
       act:() => wsSend({cmd:'pin', id: s.id, pinned: !s.pinned})},
      {text:'改名', title:'给这个对话起个名字（留空则恢复自动标题）', act:() => {
        const tEl = it.querySelector('.r1 .t');
        if(!tEl || tEl.dataset.editing) return;
        const oldT = s.title || '';
        tEl.dataset.editing = '1';
        const inp = document.createElement('input');
        inp.type = 'text'; inp.value = oldT; inp.maxLength = 40;
        inp.style.cssText = 'width:100%;font:inherit;font-size:13px;border:1px solid var(--brand);'
          + 'border-radius:6px;padding:2px 6px;background:var(--bg2);color:inherit;outline:none';
        tEl.textContent = ''; tEl.appendChild(inp);
        inp.focus(); try{ inp.select(); }catch(e){}
        let done = false;
        const finish = (save) => {
          if(done) return; done = true;
          const v = inp.value.trim();
          if(save && v !== oldT){
            wsSend({cmd:'rename', id: s.id, title: v});   /* 服务端回推 sessions，界面自动刷新 */
          } else {
            tEl.textContent = oldT || '（新对话）';
          }
          delete tEl.dataset.editing;
        };
        inp.onkeydown = (ev) => {
          ev.stopPropagation();                            /* 别让回车触发发送消息 */
          if(ev.key === 'Enter') finish(true);
          else if(ev.key === 'Escape') finish(false);
        };
        inp.onblur = () => finish(true);
        inp.onclick = (ev) => ev.stopPropagation();        /* 别触发"切换会话" */
      }},
      {text:'归档', title:'压缩上下文后归档保存（不会丢，可恢复）', act:() => {
        if(s.id === curSidVal){
          const others = ss.filter(x => x.id !== s.id);
          if(others.length){
            /* 还有别的对话：先切过去、再归档这个，就不用白白新开一个对话了 */
            wantSid = others[0].id;
            wsSend({cmd:'switch', id: others[0].id});
            setTimeout(() => wsSend({cmd:'archive', id: s.id}), 400);
            return;
          }
          wantSid = '*';               /* 这是最后一个对话了 → 归档后开个新的接上 */
        }
        wsSend({cmd:'archive', id: s.id});
      }},
      {icon:'xmark', cls:'del', title:'删除（不可恢复，建议先归档）', act:() => {
        if(confirm('删除对话「' + (s.title || '新对话') + '」？\n删除后无法恢复；想留着请用「归档」。'))
          wsSend({cmd:'del_session', id: s.id});
      }},
    ]));
    it.onclick = () => {
      if(s.id === cur){ closeSide(); return; }
      steps = null; stepsData = []; stepsElided = 0; curText = ''; stepsPinned = false;
      todoSig = '';
      wantSid = s.id;                    /* 接下来这条 hello 才认领 */
      wsSend({cmd:'switch', id: s.id});
      closeSide();
    };
    list.appendChild(it);
  });
  if(ar.length){
    const h = document.createElement('div'); h.className = 'grp ar-head';
    h.innerHTML = '<span>' + (arOpen ? '▾' : '▸') + '</span> 已归档 · ' + ar.length;
    h.onclick = () => { arOpen = !arOpen; if(lastSess) renderSessions(lastSess); };
    list.appendChild(h);
    if(arOpen) ar.forEach(s => {
      const it = document.createElement('div'); it.className = 'item ar';
      it.innerHTML = '<div class="r1"><div class="t">' + esc(s.title || '（无标题）') + '</div></div>'
        + '<div class="m">' + esc((s.archived_at || '').slice(0,16)) + ' · ' + s.messages + ' 条 · 摘要 '
        + (s.summary || '').length + ' 字</div>';
      if(s.summary){
        const su = document.createElement('div'); su.className = 'ar-s';
        su.textContent = s.summary; it.appendChild(su);
      }
      it.querySelector('.r1').appendChild(sessActions([
        {text:'恢复', title:'放回「最近对话」，可以接着聊', act:() => {
          wsSend({cmd:'unarchive', id: s.id});
        }},
        {icon:'xmark', cls:'del', title:'永久删除归档件', act:() => {
          if(confirm('永久删除归档「' + (s.title || '（无标题）') + '」？\n里面的完整对话记录会一并消失，无法恢复。'))
            wsSend({cmd:'del_session', id: s.id, archived: true});
        }},
      ]));
      it.onclick = () => it.classList.toggle('open');
      list.appendChild(it);
    });
    if(arOpen){
      const n = document.createElement('div'); n.className = 'grp';
      n.style.cssText = 'font-size:11px;line-height:1.6;padding-top:6px';
      n.textContent = T.sess_archived_note;
      list.appendChild(n);
    }
  }
}

/* ---------------- 面板填充 ---------------- */
function fillPanel(d){
  $('#p-name').value = d.name || '';
  $('#p-enable').checked = !!d.evolve_enabled;
  $('#p-dir').value = d.evolve_direction || '';
  $('#p-perday').value = d.evolve_per_day || 1;
  $('#p-prog').textContent = `${d.evolve_done || 0} / ${d.evolve_target || 1} 项` + (d.evolve_date ? `（${d.evolve_date}）` : '');
  $('#p-mem').value = d.memory || '';
  $('#p-memc').textContent = `${d.memory_chars||0} / ${d.memory_max||0} 字`;
  const pct = d.context_percent || 0;
  $('#p-ctx').textContent = `${d.context_messages||0} 条消息 · 约 ${(d.context_chars||0).toLocaleString()} 字`
    + `（阈值 ${(d.context_limit||0).toLocaleString()} 字，已用 ${pct}%）`;
  $('#p-bar').style.width = Math.min(100, pct) + '%';
  $('#p-bar').style.background = pct >= 80 ? 'var(--err)' : 'var(--brand)';
  const thr = $('#p-thr');
  if(thr && document.activeElement !== thr) thr.value = d.context_percent_used || 70;
  const src = $('#p-thrsrc');
  if(src) src.textContent = (d.context_is_custom ? '· 本对话专有' : '')
    + `（默认 ${d.context_default_percent||70}%）`;
  const win = $('#p-win');
  if(win){
    const wk = (d.model_window || 0) >= 1000000
      ? ((d.model_window||0)/1048576).toFixed(0) + 'M'
      : Math.round((d.model_window||0)/1024) + 'K';
    win.textContent = `模型窗口 ${wk} tokens（来源：${d.window_source || '内置'}），`
      + `当前占窗口 ${d.window_percent || 0}%`;
  }
  const mr = $('#p-maxround');
  if(mr && document.activeElement !== mr) mr.value = d.max_tool_rounds || 200;
  const pv = $('#p-ver');
  if(pv) pv.textContent = d.version ? ('v' + d.version) : '';
  const chg = $('#p-chg');
  if(chg){
    const items = d.changelog || [];
    if(!items.length){ chg.innerHTML = '<div>' + T.log_none_yet + '</div>'; }
    else{
      chg.innerHTML = items.map(x => {
        const isEvo = !!x.kind && x.kind !== '自我更新';
        const ver = x.version ? `<b class="v">v${esc(x.version)}</b> ` : '';
        const kind = isEvo ? `<span class="k">${esc(x.kind)}</span> ` : '';
        return `<div class="ci${isEvo ? ' evo' : ''}">`
          + `<div class="m">${esc(x.time||'')}　${ver}${kind}</div>`
          + `<div class="s">${esc(x.summary||'')}</div></div>`;
      }).join('');
    }
  }
}
let localUrl = 'http://127.0.0.1:8080/v1', localModels = [];
let cloudUrl = 'https://api.deepseek.com/v1';   /* 记住云端接口地址，切来切去都不丢 */
/* 判断某个接口地址是不是本机（本地模型） */
function isLocalUrl(u){ return /127\.0\.0\.1|localhost|\[::1\]/.test(String(u || '')); }
$('#c-model') && ($('#c-model').onchange = () => {
  const v = $('#c-model').value;
  if(v.indexOf('local::') === 0){
    $('#c-base').value = localUrl;          /* 本地模型 → 指向本机 */
  } else if(v !== '__custom__'){
    $('#c-base').value = cloudUrl;          /* 云端模型 → 还原云端地址 */
  }
});

/* ═══════════ 外观（阅读排版）═══════════
   正文的行高/段距/字号等存 config.json 的 ui 字段。
   这里只做两件事：① 把值写成 CSS 变量（样式的唯一真相是变量）；
   ② 在控制台「外观」页生成滑块+数字，改一下当场见效、按保存才落盘。 */
let uiVals = null, uiDefaults = null, uiRange = null, uiDirty = false;
let uiSaving = false;   /* 正在等保存回执 */
let uiSaved = null;     /* 服务端已确认的那份，用来判断"到底改了没有" */
let uiLastEdit = 0;     /* 最后一次动外观的时间：躲开"回执把正在拖的值拽回去" */
let uiSaveTimer = null;

/* 2026-10-01：外观改成自动落盘。
   起因是反复收到「设置完外观，重启/刷新又回到标准」。实测根因不是保存坏了
   —— 点保存之后刷新、重启都留得住 —— 而是「点预设只是即时预览，不按右上角
   保存就不落盘」，而面板很长、保存按钮在最上面，那句提示太容易错过。
   现在：点预设立即落盘；拖滑块/改数字停手 0.7 秒后落盘。 */
function uiSame(a, b){
  if(!a || !b) return false;
  return UI_FIELDS.every(f => a[f.k] === b[f.k]);
}
function uiHint(){
  const st = document.getElementById('ui-st');
  if(st) st.textContent = uiDirty ? '已改动，正在自动保存…' : '';
}
function uiFlush(){
  if(!uiVals || !uiDirty) return;
  uiSaving = true;
  wsSend({cmd:'save_config', ui: Object.assign({}, uiVals)});
}
function uiAutoSave(delay){
  uiLastEdit = Date.now();
  uiDirty = !uiSame(uiVals, uiSaved);
  uiHint();
  clearTimeout(uiSaveTimer);
  uiSaveTimer = setTimeout(uiFlush, delay || 700);
}

function applyAppearance(v){
  if(!v) return;
  uiVals = v;
  const r = document.documentElement.style;
  r.setProperty('--ui-font', v.font + 'px');
  r.setProperty('--ui-line', v.line + 'px');
  r.setProperty('--ui-para', v.para + 'px');
  r.setProperty('--ui-hgap', v.h_gap + 'px');
  r.setProperty('--ui-li',   v.li + 'px');
  r.setProperty('--ui-code', v.code + 'px');
}

/* 三档预设：一键切，不用一项项拖 */
const UI_PRESETS = {
  '紧凑': {font:15, line:21, para:3, h_gap:8,  li:0, code:12},
  '标准': {font:16, line:23, para:6, h_gap:13, li:2, code:13},
  '宽松': {font:17, line:30, para:11, h_gap:19, li:5, code:14}
};

const UI_FIELDS = [
  {k:'font',  lb:'正文字号', unit:'px'},
  {k:'line',  lb:'正文行距', unit:'px'},
  {k:'para',  lb:'段落间距', unit:'px'},
  {k:'h_gap', lb:'标题留白', unit:'px'},
  {k:'li',    lb:'列表项间距', unit:'px'},
  {k:'code',  lb:'代码字号', unit:'px'}
];

function renderAppearance(){
  const box = document.getElementById('ui-box');
  if(!box || !uiVals) return;
  if(!box.dataset.built){
    box.dataset.built = '1';
    /* 三档预设按钮 */
    const row = document.createElement('div');
    row.className = 'row';
    row.innerHTML = '<span class="lb">' + T.look_preset + '</span><div class="ctl"><div class="seg" id="ui-preset"></div>' +
                    '<div class="note">一键套用一组搭配，再按需微调下面各项。</div></div>';
    box.appendChild(row);
    const seg = row.querySelector('#ui-preset');
    Object.keys(UI_PRESETS).forEach(nm => {
      const b = document.createElement('button');
      b.type = 'button'; b.textContent = nm;
      b.onclick = () => {
        applyAppearance(Object.assign({}, uiVals, UI_PRESETS[nm]));
        uiAutoSave(0);          /* 预设就是"一键套用"，那就立即落盘，不留"忘了保存"的坑 */
        syncUI(); markPreset();
      };
      seg.appendChild(b);
    });
    /* 六个滑块 + 数字 */
    UI_FIELDS.forEach(f => {
      const r = document.createElement('div');
      r.className = 'row';
      r.innerHTML = '<span class="lb">' + f.lb + '</span><div class="ctl"><div class="ui-sl">' +
        '<input type="range" id="ui-r-' + f.k + '">' +
        '<input type="number" id="ui-n-' + f.k + '"><span class="u">' + f.unit + '</span></div></div>';
      box.appendChild(r);
      const rg = r.querySelector('#ui-r-' + f.k), nb = r.querySelector('#ui-n-' + f.k);
      const [lo, hi] = uiRange[f.k];
      rg.min = lo; rg.max = hi; rg.step = 1; nb.min = lo; nb.max = hi; nb.step = 1;
      const onChange = (val, from) => {
        let v = Math.round(Number(val));
        if(!isFinite(v)) return;
        v = Math.max(lo, Math.min(hi, v));
        /* 行高不能小于字号，否则字会叠在一起 */
        if(f.k === 'font' && uiVals.line < v + 2){ uiVals.line = v + 2; }
        uiVals[f.k] = v;
        if(f.k === 'font' && uiVals.line < v + 2) uiVals.line = v + 2;
        applyAppearance(uiVals); syncUI(f.k, from); markPreset();
        uiAutoSave();           /* 停手 0.7 秒自动落盘，不用再去点「保存」 */
      };
      rg.oninput = () => onChange(rg.value, 'range');
      nb.oninput = () => onChange(nb.value, 'num');
    });
    /* 恢复默认 */
    const fr = document.createElement('div');
    fr.className = 'row';
    fr.style.borderBottom = 'none';
    fr.innerHTML = '<span class="lb"></span><div class="ctl"><button class="btn" type="button" id="ui-reset">' + T.look_reset + '</button></div>';
    box.appendChild(fr);
    fr.querySelector('#ui-reset').onclick = () => {
      applyAppearance(Object.assign({}, uiDefaults)); uiDirty = true; syncUI(); markPreset();
    };
  }
  syncUI();
  markPreset();
}

/* 把 uiVals 同步到滑块/数字（skip = 正在操作的那个控件，别覆盖它的输入） */
function syncUI(skipKey, from){
  if(!uiVals) return;
  UI_FIELDS.forEach(f => {
    const rg = document.getElementById('ui-r-' + f.k), nb = document.getElementById('ui-n-' + f.k);
    if(!rg || !nb) return;
    const v = uiVals[f.k];
    if(!(f.k === skipKey && from === 'range')) rg.value = v;
    if(!(f.k === skipKey && from === 'num'))   nb.value = v;
    const [lo, hi] = uiRange[f.k];
    rg.min = lo; rg.max = hi; nb.min = lo; nb.max = hi;
  });
  const st = document.getElementById('ui-st');
  if(st) st.textContent = uiDirty ? '已改动，正在自动保存…' : '';
}

/* 高亮当前匹配的预设档 */
function markPreset(){
  const seg = document.getElementById('ui-preset');
  if(!seg || !uiVals) return;
  let hit = '';
  Object.keys(UI_PRESETS).forEach(nm => {
    const p = UI_PRESETS[nm];
    if(Object.keys(p).every(k => p[k] === uiVals[k])) hit = nm;
  });
  Array.prototype.slice.call(seg.children).forEach(b => b.classList.toggle('on', b.textContent === hit));
}

function fillConf(d){
  localUrl = d.local_url || localUrl;
  if(d.ui_range) uiRange = d.ui_range;
  if(d.ui_defaults) uiDefaults = d.ui_defaults;
  cloudUrl = d.cloud_base_url || cloudUrl;
  localModels = d.local_models || [];
  const sel = $('#c-model'); sel.innerHTML = '';
  const curIsLocal = isLocalUrl(d.base_url);
  let found = false;
  (d.models || []).forEach(m => {
    const o = document.createElement('option'); o.value = m; o.textContent = m;
    if(m === d.model && !curIsLocal){ o.selected = true; found = true; }
    sel.appendChild(o);
  });
  if(!found && d.model && !curIsLocal){
    const o = document.createElement('option'); o.value = d.model; o.textContent = d.model;
    o.selected = true; sel.appendChild(o); found = true;
  }
  /* —— 本地模型分组（本机 llama-server，离线也能用）—— */
  const g = document.createElement('optgroup');
  g.label = localModels.length ? T.cfg_local_offline : T.cfg_local_stopped;
  localModels.forEach(m => {
    const o = document.createElement('option');
    o.value = 'local::' + m;
    o.textContent = m + T.cfg_local_suffix;
    if(curIsLocal && m === d.model){ o.selected = true; found = true; }
    g.appendChild(o);
  });
  if(!localModels.length){
    const o = document.createElement('option');
    o.textContent = T.cfg_local_none;
    o.disabled = true; g.appendChild(o);
  }
  sel.appendChild(g);
  const o2 = document.createElement('option'); o2.value = '__custom__'; o2.textContent = T.cfg_custom; sel.appendChild(o2);
  $('#c-base').value = d.base_url || '';
  if(curIsLocal) $('#c-base').value = d.base_url || localUrl;
  $('#c-shell').checked = !!d.shell_access;
  /* —— 品牌下拉：预设各家接口地址，选中即自动填 —— */
  /* —— 界面语言下拉 —— */
  const ls = $('#c-lang');
  if(ls && d.languages && !ls.dataset.filled){
    ls.dataset.filled = '1';
    const readySet = new Set(d.ui_language_ready || []);
    d.languages.forEach(L => {
      const o = document.createElement('option');
      o.value = L.code; o.textContent = L.native;
      if(!readySet.has(L.code)) o.textContent += T.cfg_lang_untranslated;
      if(L.code === (d.ui_language || 'zh')) o.selected = true;
      ls.appendChild(o);
    });
  }
  confKeys = d.keys || {};
  const bs = $('#c-brand');
  if(bs && d.brands && !bs.dataset.filled){
    bs.dataset.filled = '1';
    d.brands.forEach(b => {
      const o = document.createElement('option');
      o.value = b.name; o.textContent = b.name; o.dataset.url = b.base_url || '';
      bs.appendChild(o);
    });
    const oc = document.createElement('option');
    oc.value = '__custom__'; oc.textContent = T.cfg_custom; oc.dataset.url = '';
    bs.appendChild(oc);
  }
  if(bs){
    let want = d.brand || '';
    if(!want){                       /* 没标记品牌时按地址反查 */
      const hit = (d.brands || []).filter(b => b.base_url && b.base_url === d.base_url)[0];
      want = hit ? hit.name : '__custom__';
    }
    bs.value = want;
  }
  /* Key 输入框：只说当前品牌存过没有，不预填（留空即不变） */
  $('#c-key').value = '';
  $('#c-key').placeholder = T.cfg_key_ph;
  showKeyState();
  const ms = $('#c-modelst');
  if(ms && !ms.textContent) ms.textContent = localModels.length
    ? (T.cfg_local_online_a + localModels.join('、') + T.cfg_local_online_b + localUrl)
    : '';
}
/* 当前品牌已存的 Key 摘要（后端 keys 是掩码，不是明文） */
let confKeys = {};
function curBrand(){ const b = $('#c-brand'); return b ? b.value : ''; }
function showKeyState(){
  const t = $('#c-keyst'); if(!t) return;
  const nm = curBrand();
  if(nm === '__custom__'){ t.textContent = T.cfg_custom_note; return; }
  const k = confKeys[nm];
  t.innerHTML = k ? (T.cfg_key_stored_a + '<em>' + k + '</em>' + T.cfg_key_stored_b)
                  : T.cfg_key_none;
}
/* 品牌 → 自动填地址 */
$('#c-brand') && ($('#c-brand').onchange = () => {
  const b = $('#c-brand'), o = b.options[b.selectedIndex];
  const u = o && o.dataset.url;
  if(u){ $('#c-base').value = u; }
  showKeyState();
  const ms = $('#c-modelst'); if(ms) ms.textContent = '';
  const bl = $('#c-bal'); if(bl) bl.textContent = T.cfg_balance_tip;
  confModels(curBrand(), []);
});
/* 拉模型 / 查余额 的发起 */
function confAsk(extra){
  const base = ($('#c-base').value || '').trim();
  if(!base){ toast(T.cfg_need_base); return false; }
  wsSend(Object.assign({base_url: base, api_key: ($('#c-key').value || '').trim(),
                        brand: curBrand()}, extra));
  return true;
}
$('#c-pull') && ($('#c-pull').onclick = () => {
  if(!confAsk({cmd:'probe_models'})) return;
  $('#c-modelst').textContent = T.cfg_fetching_models;
});
$('#c-balbtn') && ($('#c-balbtn').onclick = () => {
  if(!confAsk({cmd:'get_balance'})) return;
  $('#c-bal').textContent = T.cfg_querying;
});
/* 后端 events：models / balance */
function confModels(brand, list){
  const sel = $('#c-model'); if(!sel || !list || !list.length) return;
  const keep = sel.value;
  sel.innerHTML = '';                       /* 清空前先记下原选中值，最后补回去 */
  list.forEach(m => {
    const o = document.createElement('option'); o.value = m; o.textContent = m;
    sel.appendChild(o);
  });
  const o2 = document.createElement('option'); o2.value = '__custom__'; o2.textContent = T.cfg_custom;
  sel.appendChild(o2);
  sel.value = (list.indexOf(keep) >= 0) ? keep : list[0];
}
function onModels(d){
  const ms = $('#c-modelst');
  if(!d.ok){ if(ms) ms.textContent = T.cfg_fetch_fail + (d.error || T.cfg_unknown); return; }
  confModels(d.brand || curBrand(), d.models || []);
  if(!ms) return;
  if(d.source === 'builtin'){
    ms.textContent = T.cfg_no_models + (d.models || []).length + T.cfg_no_models_mid;
  } else {
    ms.textContent = T.cfg_fetched + (d.models || []).length + T.cfg_fetched_n;
  }
}
function onBalance(d){
  const el = $('#c-bal'); if(!el) return;
  if(!d.ok){ el.textContent = d.error || T.cfg_query_fail; return; }
  const b = d.balance || {};
  if(b.kind === 'money'){
    const cur = b.currency === 'USD' ? '$' : 'Y';
    el.innerHTML = T.cfg_balance + ' <em>' + cur + ' ' + b.total + '</em>'
      + T.cfg_balance_money_a + b.topped + T.cfg_balance_money_b + b.granted + T.cfg_balance_money_c;
  } else if(b.kind === 'credit'){
    el.innerHTML = T.cfg_used_local + ' <em>' + b.used + '</em> ' + T.cfg_points + (b.note ? ' · ' + b.note : '');
  } else {
    el.textContent = b.note || T.cfg_no_balance_api;
  }
}

/* ---------------- 侧栏宽度：可拖动分界线 ---------------- */
(function(){
  const app = document.querySelector('.app'), side = $('#side'), g = $('#gutter');
  if(!app || !side || !g) return;
  const MIN_W = 176, COLLAPSE_W = 150, DEF_W = 264;   /* 拖到 ~10 个字宽就隐藏 */
  let dragging = false, startX = 0, startW = 0;
  const maxW = () => Math.max(MIN_W + 40, Math.round(window.innerWidth * 0.5));  /* 右侧不小于 50% */
  const applyW = w => { side.style.width = Math.max(0, Math.min(maxW(), Math.round(w))) + 'px'; };
  const collapsed = () => app.classList.contains('collapsed');

  function expand(){
    app.classList.remove('collapsed');
    applyW(Math.max(MIN_W, Number(localStorage.getItem('sideW')) || DEF_W));
    localStorage.setItem('sideCollapsed', '0');
  }
  function collapse(){
    app.classList.add('collapsed');
    localStorage.setItem('sideCollapsed', '1');
  }
  window.agentToggleSide = () => collapsed() ? expand() : collapse();

  g.addEventListener('pointerdown', e => {
    if(e.button !== undefined && e.button !== 0) return;
    dragging = true;
    startX = e.clientX;
    startW = collapsed() ? 0 : side.getBoundingClientRect().width;
    if(collapsed()) app.classList.remove('collapsed');
    applyW(startW);
    g.classList.add('on');
    try{ g.setPointerCapture(e.pointerId); }catch(_){}
    e.preventDefault();
  });
  g.addEventListener('pointermove', e => {
    if(!dragging) return;
    const w = Math.max(0, startW + (e.clientX - startX));
    if(w < COLLAPSE_W){ collapse(); return; }          /* 缩到 ~10 个字宽：直接就隐藏了 */
    if(collapsed()) app.classList.remove('collapsed');
    applyW(w);
  });
  function endDrag(){
    if(!dragging) return;
    dragging = false; g.classList.remove('on');
    if(collapsed()) return;                            /* 已经是收起的，什么都不用做 */
    const w = side.getBoundingClientRect().width;
    const fw = Math.max(MIN_W, Math.min(maxW(), w));   /* 拖到 150~176 之间 → 吸附回 176 */
    applyW(fw);
    localStorage.setItem('sideW', String(fw));
    localStorage.setItem('sideCollapsed', '0');
  }
  g.addEventListener('pointerup', endDrag);
  g.addEventListener('pointercancel', endDrag);
  g.addEventListener('dblclick', e => { e.preventDefault(); window.agentToggleSide(); });

  /* 恢复上次的宽度/收起状态 */
  if(localStorage.getItem('sideCollapsed') === '1'){
    collapse();
  } else {
    const w = Number(localStorage.getItem('sideW'));
    if(w >= MIN_W) applyW(w);
  }
  window.addEventListener('resize', () => {
    if(!collapsed()) applyW(side.getBoundingClientRect().width);
  });
})();

/* ---------------- 交互绑定 ---------------- */
function openSide(){ $('#side').classList.add('open'); }
function closeSide(){ $('#side').classList.remove('open'); }
$('#sidex').onclick = () => { if(window.agentToggleSide) window.agentToggleSide(); };
$('#sidefab').onclick = () => { if(window.agentToggleSide) window.agentToggleSide(); };
$('#hamb').onclick = () => {
  const app = document.querySelector('.app');
  if(app && app.classList.contains('collapsed') && window.innerWidth > 820){
    if(window.agentToggleSide) window.agentToggleSide();
    return;
  }
  $('#side').classList.toggle('open');
};
$('#newchat').onclick = () => {
  if(streaming) wsSend({cmd:'stop'});
  steps = null; stepsData = []; stepsElided = 0; curText = ''; stepsPinned = false;
  pending = []; pqSave(); renderPending(); refreshSend();
  wantSid = '*';                         /* 新对话的 id 由服务端定，认领任意 hello */
  wsSend({cmd:'reset'}); closeSide();
};
$('#openctl').onclick = () => { $('#ctl').style.display = ''; wsSend({cmd:'panel'}); closeSide();
  /* 外观页在控制台里，首次打开时若还没拿到配置，顺带要一份（拿到后自会渲染） */
  if(!uiRange) wsSend({cmd:'get_config'}); else renderAppearance(); };
$('#topver').onclick = () => { $('#ctl').style.display = ''; wsSend({cmd:'panel'}); closeSide(); };
$('#imgbtn').onclick = pickImage;
$('#filein').onchange = onFilePicked;
$('#openconf').onclick = () => { $('#conf').style.display = ''; wsSend({cmd:'get_config'}); closeSide(); };
document.querySelectorAll('[data-close]').forEach(b => b.onclick = () => { $('#' + b.dataset.close).style.display = 'none'; });
document.querySelectorAll('.sheet').forEach(s => s.onclick = e => { if(e.target === s) s.style.display = 'none'; });

$('#p-save').onclick = () => wsSend({cmd:'save_panel',
  name: $('#p-name').value.trim(), evolve_enabled: $('#p-enable').checked,
  evolve_direction: $('#p-dir').value, evolve_per_day: parseInt($('#p-perday').value,10) || 1,
  max_tool_rounds: parseInt($('#p-maxround').value,10) || 200,
  memory: $('#p-mem').value});
/* 手动进化：点一下让 AI 研究几个可升级的方向，摊成胶囊，挑一颗再动手 */
function panelValues(){
  return {evolve_direction: $('#p-dir').value,
          evolve_per_day: parseInt($('#p-perday').value,10) || 1,
          name: $('#p-name').value.trim(),
          max_tool_rounds: parseInt($('#p-maxround').value,10) || 200};
}
let evoSug = null;                         /* 最近一次收到的方向胶囊（含 locked 列表） */
function evoLockedList(d){                 /* locked 可能是数组（新）或字符串（旧），统一成数组 */
  let v = (d && d.locked);
  if(typeof v === 'string') v = v ? [v] : [];
  return Array.isArray(v) ? v : [];
}
function renderEvoCaps(d){
  const box = $('#p-caps'); if(!box) return;
  evoSug = d || null;
  const items = (d && d.items) || [];
  const locked = evoLockedList(d);
  const tip = $('#p-caps-tip');
  box.style.display = '';                  /* 常驻显示：不再"点一次才出来" */
  if(!items.length){
    box.innerHTML = '<span class="cap" style="border-style:dashed">' + T.evo_ai_picking + '</span>';
    if(tip) tip.textContent = T.evo_dir_hint;
    wsSend({cmd:'evolve_suggest'});        /* 空就再要一次（服务端当天已有会秒回） */
    return;
  }
  box.innerHTML = items.slice(0, 8).map((t, i) => {
    const on = locked.indexOf(t) >= 0;                  /* 多选：每颗各自判断 */
    return '<button class="cap' + (on ? ' on' : '') + '" type="button" data-i="' + i + '"'
      + (on ? ' style="border-color:var(--brand);background:var(--brand-soft);color:var(--brand);font-weight:600"'
            : '')
      + '>' + esc(t) + '</button>';
  }).join('');
  if(tip) tip.textContent = locked.length
    ? ('已锁定 ' + locked.length + ' 个方向：' + locked.join('、') + ' · 再点一下可取消该方向')
    : ('未锁定 · AI 每天自动换一批' + (d.at ? '（本批 ' + d.at + '）' : '') + '；点一颗即锁定，可多选');
  box.querySelectorAll('.cap').forEach(b => {
    b.onclick = () => {
      const t = items[+b.dataset.i];
      if(!t) return;
      toast(b.classList.contains('on') ? ('已取消：' + t) : ('已锁定：' + t));
      wsSend({cmd:'evolve_lock', text: t});
    };
  });
}
$('#p-evolve').onclick = () => {                      /* 立即进化：按锁定的方向；没锁就随机挑一颗 */
  const items = (evoSug && evoSug.items) || [];
  const locked = evoLockedList(evoSug);
  const pick = locked[0] || items[Math.floor(Math.random() * items.length)] || '';
  toast(pick ? ('开始进化：' + pick) : '开始进化：按当前方向');
  wsSend({cmd:'evolve', direction: pick});
};
/* 不用手动获取：开场要一次，之后每小时问一次 —— 服务端当天已有就直接回，跨天则自动生成新一批 */
setTimeout(() => wsSend({cmd:'evolve_suggest'}), 3000);
setInterval(() => wsSend({cmd:'evolve_suggest'}), 3600 * 1000);
$('#p-compress').onclick = () => wsSend({cmd:'compress'});
function sendThr(scope){
  const v = parseInt(($('#p-thr').value||'').trim(), 10);
  if(!(v >= 10 && v <= 95)){ toast('填 10 ~ 95 之间的百分比'); return; }
  wsSend({cmd:'set_ratio', percent: v, scope: scope});
}
$('#p-thr-sess').onclick = () => sendThr('session');
$('#p-thr-def').onclick = () => sendThr('default');
$('#c-save').onclick = () => {
  const v = $('#c-model').value;
  const o = {cmd:'save_config', shell_access: $('#c-shell').checked};
  if(uiDirty && uiVals){ o.ui = Object.assign({}, uiVals); uiSaving = true; }   /* 外观改动一并落盘；uiSaving 让回执能正常收口 */
  if(curBrand() && curBrand() !== '__custom__') o.brand = curBrand();
  const langSel = $('#c-lang');
  if(langSel && langSel.value){ o.ui_language = langSel.value; langChanged = (langSel.value !== curUiLang); }
  let model = v, base = $('#c-base').value.trim();
  if(v.indexOf('local::') === 0){          /* 本地模型：地址固定指向本机 */
    model = v.slice(7);
    base = localUrl;
    /* 不写 api_key：本地服务不校验 key，写了反而会把云端 Key 覆盖掉 */
  } else if(v === '__custom__'){
    model = prompt('输入模型名：');
  }
  if(model && model !== '__custom__') o.model = model;
  if($('#c-key').value.trim()) o.api_key = $('#c-key').value.trim();
  if(base) o.base_url = base;
  wsSend(o);
  if(v.indexOf('local::') === 0){
    toast('已切到本地模型 ' + model + '（离线可用，速度较慢；本地服务需保持运行）', 7000);
  } else if(model){
    toast('已切到 ' + model + '（云端接口地址与 Key 已保留，随时可再切回本地）', 5000);
  }
  setTimeout(() => { $('#conf').style.display = 'none'; }, 700);
  /* 语言改了 → 静态界面文案由服务端注入，必须重新拉一次 HTML 才生效。
     这会重连 WS（会话内容不受影响，服务端按 sid 恢复）。 */
  if(langChanged){
    setTimeout(() => {
      toast(T.reload_for_lang === 'reload_for_lang'
              ? '正在切换语言，页面将重新加载…' : T.reload_for_lang, 4000);
      setTimeout(() => location.reload(), 500);
    }, 800);
  }
};

/* ------------- 会话内搜索（Ctrl+F，像 codex 那样在对话里找） ------------- */
let findHits = [], findIdx = -1, findLastQ = '';
function findOpen(){ const b = $('#findbar'); return !!b && b.style.display !== 'none'; }
function openFind(){
  const b = $('#findbar');
  if(b.style.display === 'none'){
    b.style.display = '';
    const q = input.value.trim();
    if(q && q.length <= 40){ $('#findq').value = q; }
    $('#findq').focus(); $('#findq').select();
    runFind($('#findq').value);
  } else { $('#findq').focus(); }
}
function closeFind(){
  $('#findbar').style.display = 'none';
  clearHits(); input.focus();
}
/* 没命中时把 ↑↓ 置灰：一眼看出「没有结果」，而不是点了没反应 */
function setFindNav(on){
  ['#findprev', '#findnext'].forEach(sel => {
    const b = $(sel); if(!b) return;
    b.disabled = !on;
    b.style.opacity = on ? '' : '.4';
    b.style.cursor = on ? '' : 'default';
  });
}
function clearHits(){
  findHits.forEach(t => t.classList.remove('findhit'));
  findHits = []; findIdx = -1;
  setFindNav(false);
  $('#findinfo').textContent = '0/0';   /* 还没输入关键词 */
}
function runFind(q){
  clearHits();
  findLastQ = (q || '').trim();
  if(!findLastQ) return;
  const w = findLastQ.toLowerCase();
  root.querySelectorAll('.turn').forEach(t => {
    const b = t.querySelector('.bub');
    if(b && b.textContent.toLowerCase().indexOf(w) >= 0) findHits.push(t);
  });
  if(!findHits.length){ $('#findinfo').textContent = T.find_none; return; }
  findIdx = 0; gotoHit();
}
function gotoHit(){
  if(findIdx < 0 || !findHits.length) return;
  findHits.forEach((t, i) => t.classList.toggle('findhit', i === findIdx));
  findHits[findIdx].scrollIntoView({block:'center', behavior:'smooth'});
  setFindNav(true);
  $('#findinfo').textContent = (findIdx + 1) + '/' + findHits.length;
}
function stepHit(step){
  if(!findHits.length) return;
  const n = findHits.length;
  findIdx = ((findIdx + step) % n + n) % n;
  gotoHit();
}
function initFind(){
  const bar = $('#findbar'), q = $('#findq');
  if(!bar || !q) return;
  let t = null;
  q.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => runFind(q.value), 160); });
  q.addEventListener('keydown', e => {
    if(e.key === 'Enter'){ e.preventDefault(); stepHit(e.shiftKey ? -1 : 1); }
    else if(e.key === 'Escape'){ e.preventDefault(); closeFind(); }
  });
  $('#findprev').onclick = () => stepHit(-1);
  $('#findnext').onclick = () => stepHit(1);
  $('#findx').onclick = closeFind;
  $('#findbtn').onclick = openFind;
  document.addEventListener('keydown', e => {
    if((e.ctrlKey || e.metaKey) && (e.key === 'f' || e.key === 'F')){
      e.preventDefault(); openFind();
    } else if(e.key === 'Escape' && findOpen() && document.activeElement !== input){
      e.preventDefault(); closeFind();
    }
  });
}

/* ---------------- 斜杠命令（参照 codex：输入 / 弹出，↑↓ 选，Enter 执行） ---------------- */
const CMDS = [
  {c:'/new',      d:'开一个新对话',                        run:() => { wantSid = '*'; wsSend({cmd:'reset'}); }},
  {c:'/model',    d:'切换模型 / API 设置',                 run:() => { $('#conf').style.display=''; wsSend({cmd:'get_config'}); }},
  {c:'/compress', d:'立即把较早的上下文压成摘要',           run:() => wsSend({cmd:'compress'})},
  {c:'/stop',     d:'中断当前任务（同 Esc）',               run:() => { streaming ? wsSend({cmd:'stop'}) : toast('现在没有在跑的任务'); }},
  {c:'/todos',    d:'展开 / 收起任务清单',                  run:() => {
      const b = $('#tdl');
      if(!b || b.style.display === 'none'){ toast('现在没有任务清单'); return; }
      setTodoOpen(!todoOpen);
    }},
  {c:'/archive',  d:'压缩上下文后归档当前对话',              run:() => {
      if(!curSidVal){ toast('还不知道当前对话的编号，稍后再试'); return; }
      const sid0 = curSidVal;
      const others = ((lastSess && lastSess.sessions) || []).filter(x => x.id !== sid0);
      if(others.length){
        /* 还有别的对话：先切过去再归档，不白开一个新对话 */
        wantSid = others[0].id;
        wsSend({cmd:'switch', id: others[0].id});
        setTimeout(() => wsSend({cmd:'archive', id: sid0}), 400);
        return;
      }
      wantSid = '*';                            /* 这是最后一个对话 → 归档后开个新的接上 */
      wsSend({cmd:'archive', id: sid0});
    }},
  {c:'/panel',    d:'打开控制台（记忆 / 进化 / 更新记录）',  run:() => { $('#ctl').style.display=''; wsSend({cmd:'panel'}); }},
  {c:'/find',     d:'在对话里搜索关键词（Ctrl+F）',          run:() => openFind()},
  {c:'/help',     d:'快捷键与用法说明',                     run:() => showHelp()},
];
function cmdpOpen(){ const b = $('#cmdp'); return !!b && b.style.display !== 'none'; }
function hideCmdp(){ const b = $('#cmdp'); if(b) b.style.display = 'none'; }
function renderCmdp(){
  const b = $('#cmdp');
  b.innerHTML = '<div class="hd">' + T.slash_hint + '</div>'
    + cmdShown.map((c, i) =>
        `<div class="ci${i === cmdSel ? ' sel' : ''}" data-i="${i}">`
        + `<span class="c">${esc(c.c)}</span><span class="d">${esc(c.d)}</span></div>`).join('');
  b.style.display = '';
  b.querySelectorAll('.ci').forEach(el => {
    el.onclick = () => { cmdSel = Number(el.dataset.i); runCmd(); };
  });
}
function cmdFilter(){
  const v = input.value;
  if(!/^\/[^\s]*$/.test(v)){ hideCmdp(); return; }
  const q = v.toLowerCase();
  cmdShown = CMDS.filter(c => c.c.indexOf(q) === 0 || c.d.toLowerCase().indexOf(q.slice(1)) >= 0);
  if(!cmdShown.length){ hideCmdp(); return; }
  cmdSel = 0; renderCmdp();
}
function runCmd(){
  const c = cmdShown[cmdSel];
  if(!c) return;
  input.value = ''; autoGrow(); hideCmdp(); hideFilep(); refreshSend(); input.focus();
  c.run();
}
function showHelp(){
  toast('Enter 发送 · Shift+Enter 换行 · ↑↓ 翻历史 · Esc 中断 · @ 引用文件 · Ctrl+F 搜索 · / 看命令', 8000);
}

/* ------------- @ 引用文件（像 codex 那样把文件带进上下文） ------------- */
let fileList = [], fileRoot = '', fileShown = [], fileSel = 0, fileStart = -1, fileAsked = false;
function filepOpen(){ const b = $('#filep'); return !!b && b.style.display !== 'none'; }
function hideFilep(){ const b = $('#filep'); if(b) b.style.display = 'none'; }
function fileQuery(){                     /* 光标前最近的 @xxx，且 @ 前面是行首或空白 */
  const pos = input.selectionStart;
  if(pos == null) return null;
  const before = input.value.slice(0, pos);
  const m = /(?:^|[\s(\[])(@[^\s@]*)$/.exec(before);
  if(!m) return null;
  return {q: m[1].slice(1), start: pos - m[1].length};
}
function renderFilep(){
  const b = $('#filep');
  b.innerHTML = '<div class="hd">' + T.at_hint + '</div>'
    + fileShown.map((f, i) => {
        const cut = f.lastIndexOf('/');
        const name = cut >= 0 ? f.slice(cut + 1) : f;
        const dir = cut >= 0 ? f.slice(0, cut + 1) : '';
        return `<div class="ci${i === fileSel ? ' sel' : ''}" data-i="${i}" title="${esc(f)}">`
          + `<span class="c">${esc(name)}</span><span class="d">${esc(dir || '（当前目录）')}</span></div>`;
      }).join('');
  b.style.display = '';
  b.querySelectorAll('.ci').forEach(el => {
    el.onclick = () => { fileSel = Number(el.dataset.i); filePick(); };
  });
}
function fileFilter(){
  const r = fileQuery();
  if(!r){ hideFilep(); return; }
  if(!fileList.length){                    /* 第一次用到才问后端要清单 */
    if(!fileAsked){ fileAsked = true; wsSend({cmd:'files'}); }
    return;
  }
  const q = r.q.toLowerCase();
  fileShown = (q ? fileList.filter(f => f.toLowerCase().indexOf(q) >= 0) : fileList).slice(0, 60);
  fileStart = r.start;
  if(!fileShown.length){ hideFilep(); return; }
  fileSel = Math.min(Math.max(0, fileSel), fileShown.length - 1);
  renderFilep();
}
function filePick(){
  const f = fileShown[fileSel];
  if(f == null || fileStart < 0) return;
  const v = input.value, pos = input.selectionStart;
  input.value = v.slice(0, fileStart) + '@' + f + ' ' + v.slice(pos);
  const np = fileStart + f.length + 2;
  hideFilep(); autoGrow(); refreshSend();
  requestAnimationFrame(() => { input.selectionStart = input.selectionEnd = np; });
}

input.addEventListener('input', () => { autoGrow(); refreshSend(); cmdFilter(); fileFilter(); });
input.addEventListener('keydown', e => {
  if(filepOpen()){                              /* @ 面板打开时，按键先给面板 */
    if(e.key === 'ArrowDown' || e.key === 'ArrowUp'){
      e.preventDefault();
      fileSel = (fileSel + (e.key === 'ArrowDown' ? 1 : fileShown.length - 1)) % fileShown.length;
      renderFilep(); return;
    }
    if(e.key === 'Enter' || e.key === 'Tab'){ e.preventDefault(); filePick(); return; }
    if(e.key === 'Escape'){ e.preventDefault(); hideFilep(); return; }
  }
  if(cmdpOpen()){                               /* 命令面板打开时，按键先给面板 */
    if(e.key === 'ArrowDown' || e.key === 'ArrowUp'){
      e.preventDefault();
      cmdSel = (cmdSel + (e.key === 'ArrowDown' ? 1 : cmdShown.length - 1)) % cmdShown.length;
      renderCmdp(); return;
    }
    if(e.key === 'Enter' || e.key === 'Tab'){
      e.preventDefault();
      if(e.key === 'Tab'){ input.value = cmdShown[cmdSel].c + ' '; autoGrow(); hideCmdp(); return; }
      runCmd(); return;
    }
    if(e.key === 'Escape'){ e.preventDefault(); hideCmdp(); return; }
  }
  if(e.key === 'Enter' && !e.shiftKey && !e.isComposing){ e.preventDefault(); submit(); return; }
  if(e.key === 'Escape'){                       /* Esc：像 codex 那样中断当前这一轮 */
    if(streaming){ e.preventDefault(); wsSend({cmd:'stop'}); toast('已请求停止');
      setTimeout(() => checkRealBusy(false), 2500); }
    return;
  }
  if((e.key === 'ArrowUp' || e.key === 'ArrowDown') && !input.value.includes('\n')){
    if(!inputHist.length) return;               /* 单行输入时翻历史，多行照常移动光标 */
    if(e.key === 'ArrowUp'){
      histIdx = histIdx < 0 ? inputHist.length - 1 : Math.max(0, histIdx - 1);
    } else {
      if(histIdx < 0) return;
      histIdx = Math.min(inputHist.length, histIdx + 1);
    }
    e.preventDefault();
    input.value = histIdx >= inputHist.length ? '' : inputHist[histIdx];
    autoGrow(); refreshSend();
    requestAnimationFrame(() => { try{ input.selectionStart = input.selectionEnd = input.value.length; }catch(_){} });
  }
});
send.onclick = submit;

/* ---------------- 启动 ---------------- */
applyI18N();                    /* 先把静态文案按当前界面语言换掉，再做其它渲染 */
pqLoad(); renderPending(); refreshSend(); initFind();

/* ---------------- 任务状态看门狗 ----------------
   有时服务端其实已经跑完（或那条消息根本没送到），这一侧却还停在"执行中"：
   按钮显示「停止」、待发送区挂着。这里定期核对一次真实状态，对不上就复位。 */
function checkRealBusy(quiet){
  if(!streaming) return;      /* 界面认为在跑就去核对，不再依赖 ws 状态和会话号 */
  fetch('/api/state', {cache:'no-store'}).then(r => r.json()).then(d => {
    if(!d) return;
    if(d.busy){ lastEvtAt = Date.now(); return; }   /* 服务端确实还在忙，继续等 */
    finishTurn();                                   /* 服务端没在跑 → 无条件复位 */
    if(!quiet) toast('任务其实已经结束，界面状态已自动复位');
  }).catch(() => {});
}
setInterval(() => {
  if(!streaming) return;
  if(Date.now() - lastEvtAt < 20000) return;        /* 20 秒没动静就核对 */
  checkRealBusy(false);
}, 8000);
/* 切回本页面时立刻核对一次，免得在后台待久了状态对不上 */
document.addEventListener('visibilitychange', () => {
  if(document.visibilityState !== 'visible') return;
  lastEvtAt = Date.now();
  if(streaming) setTimeout(() => checkRealBusy(true), 800);
});
/* 进化项目3「交互UI」：侧栏对话搜索——对话多了也能一眼找到。
   纯前端过滤（匹配标题/模型），不发请求、不改服务器数据；无匹配时给出提示，Esc 一键清空。 */
(function initSessFind(){
  const listEl = $('#list');
  if(!listEl) return;
  const st = document.createElement('style');
  st.textContent = '.sfind{padding:0 12px 8px}'
    + '.sfind input{width:100%;padding:7px 10px;border:1px solid var(--bd);border-radius:var(--r-sm);'
    + 'background:var(--bg);color:var(--t1);font-size:13px;outline:none}'
    + '.sfind input:focus{border-color:var(--brand)}';
  document.head.appendChild(st);
  const box = document.createElement('div'); box.className = 'sfind';
  const el = document.createElement('input');
  el.type = 'text'; el.id = 'sfind'; el.autocomplete = 'off'; el.placeholder = T.sess_search_ph;
  box.appendChild(el);
  listEl.parentNode.insertBefore(box, listEl);
  const orig = renderSessions;
  let full = null;                  /* 始终从服务器给的原始列表筛，避免越筛越少 */
  renderSessions = function(d){
    full = d;
    const q = el.value.trim().toLowerCase();
    if(!q){ orig(d); return; }
    const hit = s => String(s.title || '').toLowerCase().includes(q)
      || String(s.model || '').toLowerCase().includes(q);
    const c = Object.assign({}, d);
    c.sessions = (d.sessions || []).filter(hit);
    c.archived = (d.archived || []).filter(hit);
    if(!c.sessions.length && !c.archived.length){
      lastSess = d;
      listEl.innerHTML = '<div class="grp">' + T.sess_no_match_a + esc(q) + T.sess_no_match_b + '</div>';
      return;
    }
    orig(c);
  };
  el.addEventListener('input', () => { if(full) renderSessions(full); });
  el.addEventListener('keydown', e => {
    if(e.key !== 'Escape') return;
    e.preventDefault();
    if(!el.value){ el.blur(); return; }
    el.value = '';
    if(full) renderSessions(full);
  });
})();



connect();
setInterval(() => { if(wsReady) wsSend({cmd:'sessions'}); }, 20000);
if(window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.querySelector('meta[name=theme-color]').setAttribute('content','#0f1115');

/* ---- 调试上报：把浏览器里的 JS 运行时报错发回服务端，记进 access.log ----
   以后排查前端问题不用再 dump 屏幕猜：直接看 access.log 里带 dbg= 的行。
   复用 GET /api/panel 这条已被访问日志记录的通道，服务端无需改动。 */
function dbgReport(tag, msg){
  try{
    var s = String(msg == null ? '' : msg).slice(0, 280);
    fetch('/api/panel?dbg=' + tag + ':' + encodeURIComponent(s), {cache:'no-store'}).catch(function(){});
  }catch(e){}
}
window.addEventListener('error', function(e){
  /* 原来的写法只报 message 和行列号，结果拿到一堆「Script error. | line 0:0」——
     等于没有信息。原因：浏览器对拿不到细节的脚本错误一律只给这句笼统话。
     这里把能拿到的都带上：文件名、堆栈前三行、以及出错的资源标签（<script>/<img> 加载失败）。
     第三个参数 true = 捕获阶段，否则监听不到资源加载错误。 */
  try{
    var err = e.error, t = e.target;
    var bits = [];
    bits.push(e.message || (err && err.message) || '?');
    bits.push('@' + (e.filename || '') + ':' + e.lineno + ':' + e.colno);
    if(err && err.stack) bits.push('stack=' + String(err.stack).split('\n').slice(0, 3).join(' <- '));
    if(t && t !== window && t.tagName) bits.push('res=' + t.tagName + ' ' + (t.src || t.href || ''));
    dbgReport('ERR', bits.join(' | '));
  }catch(_){}
}, true);
window.addEventListener('unhandledrejection', function(e){
  var r = e.reason; dbgReport('REJ', (r && (r.message || r)) || 'unknown');
});

/* ---- 远程求值通道（前端一半）：定期问服务端有没有要我执行的 JS ----
   服务端提供 GET /api/eval（返回一段 JS 源码或空）与 POST /api/eval（收回结果）。
   在服务端实现之前这里只会拿到 404，静默忽略，不影响任何现有功能。
   有了它就不用真的连 DevTools：我可以直接读页面里的 DOM、变量、函数返回值。 */
setInterval(function(){
  fetch('/api/eval', {cache:'no-store'})
    .then(function(r){ return r.ok ? r.text() : ''; })
    .then(function(code){
      if(!code || !code.trim()) return;
      var out;
      try{ out = String(eval(code)); }catch(err){ out = 'ERROR: ' + (err && err.message); }
      /* 回传结果：服务端只实现了 GET（带 r= 参数），所以不能只用 POST */
      fetch('/api/eval?r=' + encodeURIComponent(out.slice(0, 3000)), {cache:'no-store'}).catch(function(){});
    }).catch(function(){});
}, 5000);
</script>
</body>
</html>
"""


def render_web_html(lang: str | None = None) -> str:
    """把语言包注入 WEB_HTML 再返回。

    前端通过全局 T 对象取词：T.in_send → "Send"。
    未翻译的键回落中文，最终回落键名本身（与后端 t() 行为一致）。
    """
    lc = lang or globals().get("UI_LANG") or "zh"
    if lc not in I18N_BUILD:
        lc = "zh"
    # 中文作为兜底一起下发：某条英文缺失时前端能立刻回落，不必再问服务端
    payload = {
        "lang": lc,
        "dir": (I18N_BUILD.get("meta", {}).get(lc) or {}).get("dir", "ltr"),
        "dict": dict(I18N_BUILD.get("zh") or {}),
        "meta": I18N_BUILD.get("meta") or {},
    }
    payload["dict"].update(I18N_BUILD.get(lc) or {})
    js = (
        "<script>window.__I18N__ = "
        + json.dumps(payload, ensure_ascii=False)
        + ";</script>\n</head>"
    )
    return WEB_HTML.replace("</head>", js, 1)


SW_JS = "self.addEventListener('fetch', function(){});"
MANIFEST = {
    "name": "Sidekick",
    "short_name": "助手",
    "start_url": "/",
    "display": "standalone",
    "background_color": "#f5f6f7",
    "theme_color": "#f5f6f7",
    "icons": [{
        "src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any",
    }],
}
ICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">'
    '<rect width="512" height="512" rx="112" fill="#2f6feb"/>'
    '<path d="M140 200h232a28 28 0 0 1 28 28v120a28 28 0 0 1-28 28H140a28 28 0 0 1-28-28V228a28 28 0 0 1 28-28z" '
    'fill="none" stroke="#fff" stroke-width="30" stroke-linejoin="round"/>'
    '<circle cx="196" cy="288" r="22" fill="#fff"/><circle cx="316" cy="288" r="22" fill="#fff"/>'
    '<path d="M256 200V148" stroke="#fff" stroke-width="30" stroke-linecap="round"/>'
    '<circle cx="256" cy="136" r="20" fill="#fff"/></svg>'
)


# ---------------------------------------------------------------- 面板 / 配置数据

def panel_payload(ag, cfg: dict, evolving: bool = False) -> dict:
    """助手设置面板所需的全部数据（HTTP 与 WebSocket 共用）。

    ag = 要看哪个会话；evolving = 是否正在自我进化（这状态在连接上下文里，故显式传入）。
    """
    st = evolve_progress(cfg)
    chars = estimate_chars(ag.messages)
    mem = load_memory()
    msgs = [m for m in ag.messages if m.get("role") in ("user", "assistant", "tool")]
    win_tok, win_src = window_info(cfg.get("model"), cfg)
    return {
        "name": cfg.get("assistant_name") or "Sidekick",
        "version": VERSION,
        "changelog": load_changelog()[:12],
        "model": cfg.get("model"),
        "evolve_enabled": bool(cfg.get("evolve_enabled")),
        "evolve_direction": cfg.get("evolve_direction") or "",
        "evolve_per_day": int(cfg.get("evolve_per_day") or 1),
        "evolve_done": st.get("done", 0),
        "evolve_target": st.get("target", 1),
        "evolve_date": st.get("date", ""),
        "evolve_log": (st.get("log") or [])[-12:],
        "evolving": bool(evolving),
        "memory": mem,
        "memory_chars": len(mem),
        "memory_max": MEMORY_MAX_CHARS,
        "context_chars": chars,
        "context_tokens": round(chars / CHARS_PER_TOKEN),   # 已用 token（按 1 token≈1.5 字换算）
        "context_limit": ag.compress_at,
        "context_default_percent": round(cfg_compress_ratio(cfg) * 100),
        "context_percent_used": round((ag.compress_ratio or cfg_compress_ratio(cfg)) * 100),
        "context_is_custom": bool(load_session_compress_ratio(ag.session_id or "")),
        "model_window": win_tok,
        "window_source": win_src,
        "context_messages": len(msgs),
        "max_tool_rounds": int(cfg.get("max_tool_rounds") or MAX_TOOL_ROUNDS),
        "context_percent": min(100, int(chars * 100 / max(1, ag.compress_at))),
        # 换算成模型真实窗口的占比，让「占用」有意义（窗口 token 数 × 1.5 ≈ 字符数）
        "window_percent": min(100, int(chars * 100 / max(1, win_tok * CHARS_PER_TOKEN))),
    }


LOCAL_MODEL_URL = "http://127.0.0.1:8080/v1"   # 本机 llama-server 的 OpenAI 兼容地址
DEFAULT_CLOUD_URL = "https://api.deepseek.com/v1"


def _is_local_url(u) -> bool:
    """判断接口地址是不是本机（本地模型）。"""
    return bool(re.search(r"127\.0\.0\.1|localhost|\[::1\]", str(u or "")))


def local_lite(cfg: dict) -> bool:
    """本地模型是否走「轻量模式」。

    为什么必须有它：CPU 上 prompt 处理只有 ~23 tok/s，而完整系统提示词（2608 tokens）
    加 14 个工具定义（2858 tokens）就要 5467 tokens —— 光这一块要啃 4 分钟，
    一句话都还没答。轻量模式把它们砍到几百 tokens，本地模型才勉强能用。
    """
    return bool(cfg.get("local_lite", True)) and _is_local_url(cfg.get("base_url"))
_LOCAL_CACHE = {"t": 0.0, "list": []}


def detect_local_models(timeout: float = 1.5, ttl: float = 4.0) -> list:
    """探测本机是否跑着 llama-server（OpenAI 兼容），返回它的模型名列表。

    只在界面打开设置时调用，所以给个短超时 + 几秒缓存，避免拖慢界面。
    """
    now = time.time()
    if now - _LOCAL_CACHE["t"] < ttl:
        return _LOCAL_CACHE["list"]
    out = []
    try:
        req = urllib.request.Request(LOCAL_MODEL_URL + "/models",
                                     headers={"User-Agent": "termux-agent/" + VERSION})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
        for m in (data.get("data") or data.get("models") or []):
            if not isinstance(m, dict):
                continue
            mid = m.get("id") or m.get("model") or m.get("name")
            if not mid:
                continue
            mid = str(mid)
            # llama-server 默认返回模型文件全路径，只留文件名当展示名
            if "/" in mid and mid.endswith(".gguf"):
                mid = Path(mid).stem
            out.append(mid)
    except Exception:
        out = []
    _LOCAL_CACHE.update({"t": now, "list": out})
    return out


# ---------------- 服务商品牌预设（设置页「选品牌 → 自动填接口地址」用）----------------
PROVIDER_PRESETS = [
    ("DeepSeek",      "https://api.deepseek.com/v1"),
    ("WorkBuddy",     "https://copilot.tencent.com/v2"),
    ("智谱 GLM",       "https://open.bigmodel.cn/api/paas/v4"),
    ("月之暗面 Kimi",   "https://api.moonshot.cn/v1"),
    ("MiniMax",       "https://api.minimaxi.com/v1"),
    ("阶跃星辰 StepFun", "https://api.stepfun.com/v1"),
    ("通义千问",        "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    ("硅基流动",        "https://api.siliconflow.cn/v1"),
    ("OpenAI",        "https://api.openai.com/v1"),
]

# 拉不到 /models 的后端，用这份兜底清单（按 base_url 关键字匹配）
PROBE_FALLBACK = {
    "copilot.tencent.com": ["deepseek-v4-pro", "deepseek-v4.1-flash", "glm-5.3",
                            "glm-5.3-flash", "glm-5.3-flashx", "kimi-k3-2",
                            "minimax-m3-pay", "hy3", "step-5-preview", "hunyuan-chat"],
}


def provider_key_of(cfg: dict, brand: str) -> str:
    """取某品牌已保存的 Key —— 换品牌不丢 Key 的关键。"""
    p = (cfg.get("providers") or {}).get(brand)
    if isinstance(p, dict):
        return p.get("api_key") or ""
    return ""


def all_provider_keys(cfg: dict) -> dict:
    """所有品牌已存的 Key（只回前缀，供界面回显）。"""
    out = {}
    for name, p in (cfg.get("providers") or {}).items():
        if isinstance(p, dict) and p.get("api_key"):
            out[name] = _mask_key(p["api_key"])
    return out


def _mask_key(k: str) -> str:
    k = str(k or "")
    return (k[:7] + "…" + k[-4:]) if len(k) > 12 else ("已保存" if k else "")


def probe_models(base_url: str, api_key: str, timeout: int = 25):
    """拉取某接口的可用模型列表。返回 (models, error, source)。

    先试 /models；失败或为空时用兜底清单（WorkBuddy 就没有 /models 接口）。
    source = "api" 真的是从接口拉到的；"builtin" 是内置清单（接口没有或没连上）。
    必须分开报：以前只看列表非空就 ok，结果超时失败也会显示成"已拉到",
    用户根本分不清真拉到还是猜的。
    """
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return [], "接口地址为空", ""
    err = ""
    models = []
    try:
        req = urllib.request.Request(
            base + "/models",
            headers={"Authorization": "Bearer " + str(api_key or ""),
                     "User-Agent": "termux-agent/" + VERSION})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
        for m in (data.get("data") or data.get("models") or []):
            mid = m.get("id") or m.get("model") or m.get("name") if isinstance(m, dict) else m
            if mid:
                models.append(str(mid))
    except Exception as e:
        err = _friendly_http_error(e) if isinstance(e, urllib.error.HTTPError) else str(e)[:120]
    if not models:
        for kw, lst in PROBE_FALLBACK.items():
            if kw in base:
                return list(lst), (err or "该后端无 /models 接口"), "builtin"
    return models, ("" if models else (err or "没拉到模型")), ("api" if models else "")


def query_balance(base_url: str, api_key: str, cfg: dict = None, timeout: int = 20):
    """查余额 / 积分。返回 (dict, error)。

    · DeepSeek  : 官方 /user/balance
    · WorkBuddy : 无余额接口，返回本地累计的消耗点数（每次对话的 usage.credit 累加）
    · 其它      : 明确告知不支持，而不是干瞪眼
    """
    base = str(base_url or "").strip().rstrip("/")
    key = str(api_key or "")
    if not key:
        return {}, "还没填 API Key"

    # ---- DeepSeek ----
    if "api.deepseek.com" in base:
        try:
            req = urllib.request.Request(
                "https://api.deepseek.com/user/balance",
                headers={"Authorization": "Bearer " + key,
                         "Accept": "application/json",
                         "User-Agent": "termux-agent/" + VERSION})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.load(r)
            infos = d.get("balance_infos") or []
            if infos:
                it = infos[0]
                return {"kind": "money",
                        "currency": it.get("currency", "CNY"),
                        "total": it.get("total_balance"),
                        "granted": it.get("granted_balance"),
                        "topped": it.get("topped_up_balance")}, ""
        except Exception as e:
            return {}, _friendly_http_error(e) if isinstance(e, urllib.error.HTTPError) else str(e)[:120]

    # ---- WorkBuddy：本地累计点数 ----
    if "copilot.tencent.com" in base:
        used = float((cfg or {}).get("wb_credit_used") or 0)
        return {"kind": "credit", "used": round(used, 2),
                "note": "WorkBuddy 不提供余额接口，这里显示本机累计消耗"}, ""

    return {}, "该服务商未提供公开的余额接口"


def config_payload(cfg: dict) -> dict:
    key = cfg.get("api_key") or ""
    # 模型下拉 = 各服务商的模型合并（去重，保持稳定顺序）
    models = []
    for p in (cfg.get("providers") or {}).values():
        if isinstance(p, dict):
            for m in (p.get("models") or []):
                if m not in models:
                    models.append(m)
    if not (cfg.get("providers") or {}).items():
        # 没配多服务商（老配置）才用服务端拉到的模型列表；
        # 否则 /models 的全量在售模型会混进下拉（比如智谱会把 glm-4.5 一起带进来）
        for m in (cfg.get("model_list") or KNOWN_MODELS):
            if m not in models:
                models.append(m)
    if cfg.get("model") and cfg["model"] not in models:
        models.insert(0, cfg["model"])
    return {
        "model": cfg.get("model"),
        "base_url": cfg.get("base_url"),
        "api_key": (key[:7] + "…" + key[-4:]) if len(key) > 12 else "",
        "has_key": bool(key),
        "shell_access": bool(cfg.get("shell_access")),
        "models": models,
        "provider": cfg.get("provider") or provider_of_model(cfg, cfg.get("model"))[0] or "",
        "providers": {n: {"base_url": (p or {}).get("base_url", ""),
                          "models": (p or {}).get("models") or []}
                      for n, p in (cfg.get("providers") or {}).items()
                      if isinstance(p, dict)},
        "local_url": LOCAL_MODEL_URL,
        "local_models": detect_local_models(),
        "cloud_base_url": cfg.get("cloud_base_url") or DEFAULT_CLOUD_URL,
        # ---- 设置页重构：品牌 / 各品牌已存 Key / 工作目录 ----
        "brands": [{"name": n, "base_url": u} for n, u in PROVIDER_PRESETS],
        "brand": cfg.get("provider") or (provider_of_model(cfg, cfg.get("model"))[0] or ""),
        # ---- 界面多语言 ----
        "ui_language": detect_ui_lang(cfg),
        "languages": [{"code": c, "native": (m or {}).get("native", c)}
                      for c, m in (I18N_BUILD.get("meta") or {}).items()],
        "ui_language_ready": [c for c, tbl in I18N_BUILD.items() if tbl and c != "meta"],
        "keys": all_provider_keys(cfg),
        "wb_credit_used": float(cfg.get("wb_credit_used") or 0),
        "ui": get_appearance(cfg),
        "ui_defaults": dict(APPEARANCE_DEFAULTS),
        "ui_range": {k: list(v) for k, v in APPEARANCE_RANGE.items()},
    }


def hello_payload(ag, cfg: dict) -> dict:
    # 上下文那几个字段原先只有 panel_payload 有 —— 于是刷新页面时（收到的是 hello）
    # 输入框上方那行拿不到字数/窗口/阈值，只能一直显示 HTML 里的占位「0 / 0 字」。
    # 这里补齐，让上下文指示在页面加载后立刻就有意义。
    _cchars = estimate_chars(ag.messages)
    _cwin, _csrc = window_info(cfg.get("model"), cfg)
    _cratio = ag.compress_ratio or cfg_compress_ratio(cfg)
    return {
        "name": cfg.get("assistant_name") or "Sidekick",
        "version": VERSION,
        "boot_id": BOOT_ID,      # 变了说明服务重启过，前端会自动重载页面
        "changelog": load_changelog()[:3],
        "model": cfg.get("model"),
        "session_id": ag.session_id,
        "shell_access": bool(cfg.get("shell_access")),
        "memory": bool(load_memory()),
        "wakelock": WAKE_LOCK_STATE.get("ok"),
        "context_percent": min(100, int(_cchars * 100 / max(1, ag.compress_at))),
        "context_limit": ag.compress_at,
        "context_chars": _cchars,
        "context_tokens": round(_cchars / CHARS_PER_TOKEN),
        "model_window": _cwin,
        "window_source": _csrc,
        "window_percent": min(100, int(_cchars * 100 / max(1, _cwin * CHARS_PER_TOKEN))),
        "context_percent_used": round(_cratio * 100),
        "context_is_custom": bool(load_session_compress_ratio(ag.session_id or "")),
        "todo": todo_payload(ag.session_id),
        "busy": bool(getattr(ag, "_running", False)),
    }


def history_payload(ag) -> list:
    out = [m for m in ag.messages
           if m.get("role") in ("user", "assistant")
           and not str(m.get("content") or "").startswith("[系统自动生成的更早对话摘要]")]
    # 正在生成、还没写进 messages 的那段正文也带上：
    # 这样切走再切回来不会像是「说到一半被截断」。
    live = getattr(ag, "stream_text", "")
    if live:
        out = out + [{"role": "assistant", "content": live}]
    return out


# ---------------------------------------------------------------- WebSocket（纯标准库）

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
WS_CLIENTS: set = set()          # 活跃连接的发送队列，用于服务端主动推送


def ws_accept_value(key: str) -> str:
    return base64.b64encode(
        hashlib.sha1((key + _WS_GUID).encode("utf-8")).digest()).decode("ascii")


def _ws_read_exact(rfile, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = rfile.read(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed")
        buf += chunk
    return buf


def ws_recv_frame(rfile):
    """读一帧。返回 (opcode, payload)。"""
    b1, b2 = _ws_read_exact(rfile, 2)
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    ln = b2 & 0x7F
    if ln == 126:
        ln = struct.unpack(">H", _ws_read_exact(rfile, 2))[0]
    elif ln == 127:
        ln = struct.unpack(">Q", _ws_read_exact(rfile, 8))[0]
    mask = _ws_read_exact(rfile, 4) if masked else b""
    payload = _ws_read_exact(rfile, ln) if ln else b""
    if masked and payload:
        payload = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
    return opcode, payload


def ws_send_frame(sock, payload: bytes, opcode: int = 1) -> None:
    """发一帧（服务端发出的帧不加掩码）。"""
    header = bytearray()
    header.append(0x80 | opcode)
    n = len(payload)
    if n < 126:
        header.append(n)
    elif n < 65536:
        header.append(126)
        header += struct.pack(">H", n)
    else:
        header.append(127)
        header += struct.pack(">Q", n)
    sock.sendall(bytes(header) + payload)


def ws_broadcast(obj: dict) -> None:
    """向所有已连接的前端推送一条事件（这是 SSE 做不到的：服务端主动推）。"""
    for q in list(WS_CLIENTS):
        try:
            q.put(obj)
        except Exception:
            pass


# 看门狗进程的 pid 文件（agent stop / status 靠它定位看门狗）。
# 历史上叫 web.pid，名字极易被误读成"服务进程的 pid"，现已改名为 supervisor.pid；
# 这里仍用 WEB_PID 这个名字指向同一个文件，其它代码无需改动。
WEB_PID = APP_DIR / "supervisor.pid"
SUPERVISOR = APP_DIR / "supervisor.sh"
VERSIONS_DIR = APP_DIR / "versions"
LAST_GOOD = VERSIONS_DIR / "last-good.py"

# 看门狗脚本：保证服务活着；代码被改坏时自动回滚到上一个可用版本。
# 有了它，agent 才敢改自己的源码 —— 改崩了也能自己爬起来。
SUPERVISOR_SH = r"""#!/data/data/com.termux/files/usr/bin/bash
# Termux Agent 看门狗（由 agent.py 自动生成，请勿手改）
APP_DIR="$HOME/.termux-agent"
SRC="$APP_DIR/agent.py"
VERS="$APP_DIR/versions"
GOOD="$VERS/last-good.py"
LOG="$APP_DIR/supervisor.log"
PIDF="$APP_DIR/supervisor.pid"   # 看门狗专用。web.pid 归服务进程所有（stop/状态查询要用），
                                  # 两边共用一个文件会互相覆盖，导致单实例保护失效、看门狗越堆越多。
PY="$PREFIX/bin/python3"
URL="http://127.0.0.1:8765/api/state"

mkdir -p "$VERS"

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
healthy() { curl -sf -o /dev/null --max-time 8 "$URL" >/dev/null 2>&1; }

# ---- 单实例保护：已经有活着的看门狗就退出，避免多个看门狗互相抢杀服务 ----
OLD=$(cat "$PIDF" 2>/dev/null)
if [ -n "$OLD" ] && [ "$OLD" != "$$" ] && kill -0 "$OLD" 2>/dev/null; then
  if grep -qa "supervisor" "/proc/$OLD/cmdline" 2>/dev/null; then
    log "已有看门狗在运行 (pid $OLD)，本实例退出"
    exit 0
  fi
fi
echo $$ > "$PIDF"

log "看门狗启动 (pid $$)"
fail=0
while true; do
  if healthy; then
    if [ "$fail" -gt 0 ]; then log "服务已恢复（此前连续失败 ${fail} 次）"; fi
    fail=0; sleep 5; continue
  fi

  # 健康检查失败：给服务宽限期，别误杀正在跑长任务的实例
  # （识图/大文件处理这类活儿会让接口短暂变慢，8 秒超时 + 4 次宽限 ≈ 足够跑完）
  # 关键：接口慢 ≠ 服务死了。长任务（大模型续跑 / 大文件处理）会把 GIL 占满，
  # 导致 /api/state 连续超时 —— 这时主进程其实活得好好的，杀掉它等于腰斩任务，
  # 而且重启后自愈又会续跑，形成"重启→续跑→误杀→重启"的死循环。
  # 所以按进程存活来决定耐心：活着给 2 分钟，真没了就 1 次即重启（恢复更快）。
  if pgrep -f "agent.py web --serve" >/dev/null 2>&1; then LIMIT=24; else LIMIT=1; fi
  fail=$((fail+1))
  if [ "$fail" -lt "$LIMIT" ]; then
    log "健康检查失败（第 ${fail}/${LIMIT} 次），再观察"
    sleep 5
    continue
  fi
  log "连续 ${fail} 次失败未恢复，判定服务异常，开始处理"

  # 语法坏了 → 立刻回滚
  if ! "$PY" -m py_compile "$SRC" >>"$LOG" 2>&1; then
    if [ -f "$GOOD" ]; then
      cp "$GOOD" "$SRC"
      log "语法检查失败，已回滚到上一个可用版本"
    else
      log "语法检查失败，但没有可用备份，等待人工修复"
    fi
    sleep 3
    continue
  fi

  # 清掉可能残留的旧实例，再拉一个新的
  pkill -f "agent.py web --serve" >/dev/null 2>&1
  sleep 1
  log "启动服务"
  "$PY" "$SRC" web --serve >>"$LOG" 2>&1 &

  ok=0
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    sleep 1
    if healthy; then ok=1; break; fi
  done

  if [ "$ok" = "1" ]; then
    cp "$SRC" "$GOOD"
    log "服务就绪，已记录可用版本"
    fail=0
    sleep 3
  else
    fail=$((fail+1))
    log "健康检查失败（第 ${fail} 次）"
    pkill -f "agent.py web --serve" >/dev/null 2>&1
    sleep 2
    if [ "$fail" -ge 2 ] && [ -f "$GOOD" ]; then
      cp "$GOOD" "$SRC"
      log "连续失败，已回滚到上一个可用版本"
      fail=0
    fi
    sleep 3
  fi
done
"""


def ensure_supervisor() -> None:
    """把看门狗脚本写到磁盘（内容变化时更新）。"""
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        cur = SUPERVISOR.read_text(encoding="utf-8") if SUPERVISOR.exists() else ""
        if cur != SUPERVISOR_SH:
            SUPERVISOR.write_text(SUPERVISOR_SH, encoding="utf-8")
            os.chmod(SUPERVISOR, 0o700)
        VERSIONS_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _restart_soon(delay: int = 4, cfg=None, why: str = "") -> None:
    """几秒后结束当前服务进程；看门狗会立刻用新代码把它拉起来。

    重启前先跟用户打个招呼（界面提示 + 系统通知）—— 免得他正看着界面突然断连，
    以为任务丢了。重启后 selfheal 会把没做完的清单接着做。
    """
    msg = why or ("服务将在约 %d 秒后重启以加载新代码：界面会短暂断开，"
                  "随后自动回来，没做完的任务会接着做。" % delay)
    try:
        ws_broadcast({"ev": "info", "d": msg})
    except Exception:
        pass
    try:
        if cfg:
            notify_async(cfg, "Agent 即将重启", msg, tag="restart")
    except Exception:
        pass
    try:
        # 不只是杀掉旧进程干等看门狗来救：主动用新代码把服务立刻拉起来，
        # 把"连不上"的窗口从 20+ 秒（看门狗 4 次宽限）缩到约 3 秒。
        #
        # 关键：必须走独立的脚本文件。若把 pkill 和启动命令写在同一条 bash -c 里，
        # 那条命令自己的命令行就会含 "agent.py web --serve" 字样，pkill 会连自己
        # 一起杀掉，后面的启动命令根本跑不到（之前两次都栽在这儿）。
        # 独立脚本在执行时命令行只是 `bash _restart.sh`，绝不会自我匹配。
        _py = os.environ.get("PREFIX", "/data/data/com.termux/files/usr") + "/bin/python3"
        _sh = APP_DIR / "_restart.sh"
        _sh.write_text(
            "#!/data/data/com.termux/files/usr/bin/bash\n"
            "sleep %d\n"
            "pkill -f '[a]gent.py web --serve'\n"
            "sleep 1\n"
            "nohup '%s' '%s' web --serve >>'%s' 2>&1 &\n"
            % (delay, _py, APP_DIR / "agent.py", APP_DIR / "supervisor.log"),
            encoding="utf-8")
        subprocess.Popen(["bash", str(_sh)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except Exception:
        pass


def _server_alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _open_url(url: str) -> None:
    for cmd in (["am", "start", "-a", "android.intent.action.VIEW", "-d", url],
                ["termux-open-url", url]):
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            continue
    print(dim(f"  请手动在浏览器打开： {url}"))


def _stop_server() -> int:
    # 先停看门狗，否则它会把服务再次拉起来
    try:
        pid = int(WEB_PID.read_text().strip())
        os.kill(pid, signal.SIGKILL)
        print(green(f"  已停止看门狗（进程 {pid}）"))
    except Exception:
        print(dim("  看门狗已不在运行"))
    try:
        WEB_PID.unlink()
    except Exception:
        pass
    time.sleep(1)
    try:
        subprocess.run(["pkill", "-9", "-f", "agent.py web --serve"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        subprocess.run(["pkill", "-9", "-f", "supervisor.sh"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    except Exception as e:
        print(red(f"  停止服务时出错：{e}"))

    # 验证是否真的停掉了 —— pkill 只在「同 SELinux 域」时有效。
    # 若服务是早先经 `adb shell run-as com.termux`（runas_app 域）启动的，
    # 从 App 域执行的 pkill 既看不见也杀不掉，会静默失败，表现为「重启没生效」。
    time.sleep(1.5)
    left = []
    try:
        r = subprocess.run(["pgrep", "-f", "agent.py web --serve"],
                           capture_output=True, text=True, timeout=10)
        left = [x for x in (r.stdout or "").split() if x.strip()]
    except Exception:
        pass
    if left:
        print(yellow(f"  [注意] 还有 {len(left)} 个服务进程没停下来（pid {'、'.join(left)}）"))
        print(dim("     多半是它们跑在别的 SELinux 域（例如经 adb run-as 启动的 runas_app 域），"))
        print(dim("     本进程无权杀掉。在电脑上执行这条清掉它们："))
        print(dim("       adb shell run-as com.termux kill -9 " + " ".join(left)))
    else:
        print(green("  服务已停止（已确认无残留进程）"))
    return 0


# ---------------------------------------------------------------- 自我进化

EVOLVE_STATE = APP_DIR / "evolve.json"
EVOLVE_LOG = APP_DIR / "evolve.log"


def load_evolve_state() -> dict:
    try:
        d = json.loads(EVOLVE_STATE.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {}


def save_evolve_state(d: dict) -> None:
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        EVOLVE_STATE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def evolve_progress(cfg: dict) -> dict:
    """返回今日进化进度（跨重启保持；跨天自动重置）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    st = load_evolve_state()
    if st.get("date") != today:
        st = {"date": today, "done": 0, "target": int(cfg.get("evolve_per_day") or 1),
              "log": st.get("log") or []}
        save_evolve_state(st)
    st["target"] = int(cfg.get("evolve_per_day") or 1)
    st.setdefault("log", [])
    return st


def _evolve_log(line: str) -> None:
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        with open(EVOLVE_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
        # 轮转：以前只管追加不管大小，涨到 1MB 也没人清理。超过 512KB 就砍掉前半截、
        # 只留较新的后半截，和 access.log 的 512KB 上限保持一致。
        if EVOLVE_LOG.exists() and EVOLVE_LOG.stat().st_size > 512 * 1024:
            _data = EVOLVE_LOG.read_text(encoding="utf-8", errors="ignore")
            EVOLVE_LOG.write_text(_data[-(256 * 1024):], encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------- 更新记录
# 每次自我更新 / 自动进化 / 手动进化都留一条，界面上「更新记录」直接读这里，
# 免得改了什么只有当时的聊天里看得到、过后就查无此事。

CHANGELOG_PATH = APP_DIR / "changelog.json"
CHANGELOG_MAX = 200


def _parse_version(text: str) -> str:
    """从「v1.2.5：说明…」这类摘要开头抓版本号，抓不到就空着。"""
    m = re.match(r"\s*[vV]?(\d+\.\d+(?:\.\d+)?)", text or "")
    return m.group(1) if m else ""


def load_changelog() -> list:
    try:
        d = json.loads(CHANGELOG_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def log_change(kind: str, summary: str, version: str = "") -> dict:
    """记一条更新/进化记录（新的在最前面）。kind 如：自我更新 / 自动进化 / 手动进化。"""
    raw = (summary or "").strip()
    body = re.sub(r"^\s*[vV]?\d+\.\d+(?:\.\d+)?\s*[：:]\s*", "", raw).strip() or raw
    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "kind": kind,
        "version": version or _parse_version(raw),
        "summary": body.replace("\n", " ").strip(),
    }
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        items = load_changelog()
        items.insert(0, entry)
        CHANGELOG_PATH.write_text(
            json.dumps(items[:CHANGELOG_MAX], ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    try:
        with open(EVOLVE_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [{kind}] "
                    f"{entry['version']} {entry['summary']}\n")
    except Exception:
        pass
    return entry


# ---- 「进化选题器」：让 AI 看项目现状，研究接下来能升级什么

EVO_SUGGEST_FILE = APP_DIR / "evolve_suggestions.json"


def _project_snapshot(cfg: dict) -> str:
    """给选题器看的项目现状：版本、最近做过什么、已有哪些能力。"""
    parts = [f"版本：v{VERSION}"]
    try:
        src = Path(__file__).read_text(encoding="utf-8")
        parts.append(f"形态：单文件 Python（{len(src.splitlines())} 行）+ 内嵌网页界面，无第三方依赖")
    except Exception:
        pass
    try:
        ch = load_changelog()[:14]
        done = [c.get("summary", "") for c in ch if c.get("summary")]
        if done:
            parts.append("最近做过的改动（不要重复这些）：\n  - " + "\n  - ".join(done))
    except Exception:
        pass
    try:
        parts.append("它已有的工具能力：" + "、".join(TOOL_LABELS.get(k, k) for k in TOOL_IMPLS))
    except Exception:
        pass
    try:
        d = (cfg.get("evolve_direction") or "").strip()
        if d:
            parts.append("用户设定的进化方向：" + d.replace("\n", " "))
    except Exception:
        pass
    try:
        mem = load_memory()
        if mem:
            parts.append("已经实现的能力摘要：\n" + mem[:900])
    except Exception:
        pass
    return "\n".join(parts)


def _parse_suggestion_list(raw: str) -> list:
    """从模型回复里抠出升级点列表。

    模型不一定老老实实只给 JSON 数组：可能包在 ```json 代码块里、
    可能用中文引号，也可能干脆忘了 JSON 直接给编号列表。这几种都认，
    总比一次解析失败就白跑一趟强。
    """
    text = (raw or "").strip()
    if not text:
        return []
    items = []

    # ① 先按 JSON 数组试
    for m in re.finditer(r"\[[^\[\]]*\]", text, re.S):
        try:
            arr = json.loads(m.group(0))
        except Exception:
            continue
        if isinstance(arr, list):
            got = [x for x in arr if isinstance(x, str)]
            if got:
                items = got
                break

    # ② JSON 不行 → 按行抠。
    #    但前提是它确实像个列表：得有编号或项目符号。
    #    否则模型只是写了段思考过程，硬抠出来的就是一句废话。
    if not items and re.search(r"(?m)^\s*(?:[-*·•]|\d+\s*[.、)]|\(\d+\))", text):
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("```"):
                continue
            s = re.sub(r"^\s*(?:[-*·•]|\d+\s*[.、)]|\(\d+\))\s*", "", s)
            s = s.strip().strip("\"'“”「」【】 ,，。;；")
            if len(s) >= 2:
                items.append(s)

    out, seen = [], set()
    for x in items:
        t = str(x).strip().strip("\"'“”「」 ,，。;；")
        if 2 <= len(t) <= 44 and t not in seen:
            seen.add(t)
            out.append(t)
    return out[:8]


def _suggest_evolutions(cfg: dict, n: int = 5) -> list:
    """让模型参考当下前沿 agent 的常见能力，给出 n 个「方向」短标签（≤6 字）。"""
    prompt = (
        "你是这个 AI 助手自身的「进化选题器」。下面是它现在的样子，"
        f"请提出 {n} 个**具体、可验收、一次就能做完**的升级方向。\n\n"
        "现状：\n" + _project_snapshot(cfg) + "\n\n"
        "要求：\n"
        "1. 每一条是一个**能力方向（大类）**，不是某件具体要做的事 —— 要写「错误提示」这种方向，"
        "而不是「错误提示：失败时显示原因」这种具体项目\n"
        "2. 每条**最多 6 个汉字**（就是要显示在胶囊按钮上的那几个字），不要标点、不要解释、不要编号，"
        "例如：错误提示 / 文件整理 / 交互界面 / 工具调用 / 运行稳定\n"
        "3. 内容上参考当下主流编程 agent（Claude Code、Codex、Cursor 等）值得长期打磨的能力方向，"
        "但必须贴合这台安卓平板上的单文件 Python 助手；别给「接入新模型」「重写架构」这类大工程\n"
        "4. 方向可以和历史重合（好方向本来就要反复打磨），不必刻意回避「最近做过的」\n"
        f"5. 这 {n} 条尽量覆盖不同方面：稳定性 / 易用性 / 能力 / 使用体验\n"
        "6. 只输出一个 JSON 数组，不要解释、不要代码块，例如：\n"
        '["错误提示", "文件整理", "交互界面", "工具调用", "运行稳定"]'
    )
    try:
        # 思考模式很吃 token：给少了会"思考写完、正文没开始"就被截断，
        # 于是拿到空 content（历史上就是这么连续失败两次的）。这里留足余量。
        raw = _chat_once(cfg, [{"role": "user", "content": prompt}], 4200)
    except Exception as e:
        _evolve_log(f"选题器调用失败：{type(e).__name__}: {e}")
        return []
    out = _parse_suggestion_list(raw)
    if not out:
        _evolve_log("选题器没解析出升级点；原始返回（前 200 字）：%s"
                    % re.sub(r"\s+", " ", (raw or ""))[:200])
    return out


def load_suggestions(only_today: bool = True) -> dict:
    """读缓存的推荐（only_today=True 时只认当天的）。

    结构：{date, at, items:[...], locked:"被锁定的那一颗"}
    locked 非空 = 用户点选并锁定了该方向；此时 items 仍可每日换新，但锁定项一直保留。
    """
    try:
        d = json.loads(EVO_SUGGEST_FILE.read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("items"):
            if not only_today or d.get("date") == datetime.now().strftime("%Y-%m-%d"):
                return d
    except Exception:
        pass
    return {}


def _locked_list(d) -> list:
    """把缓存里的 locked 读成列表（兼容早先写的单值字符串格式）。"""
    v = (d or {}).get("locked")
    if isinstance(v, str):
        v = [v] if v.strip() else []
    out = []
    for x in (v or []):
        s = str(x).strip()
        if s and s not in out:
            out.append(s)
    return out


def save_suggestions(items: list) -> dict:
    """写入新一批方向。**保留已有的锁定项（可多选）**——换一批不该把用户锁的方向弄丢。"""
    d = {"date": datetime.now().strftime("%Y-%m-%d"),
         "at": datetime.now().strftime("%H:%M"),
         "items": list(items or []),
         "locked": []}
    try:
        old = json.loads(EVO_SUGGEST_FILE.read_text(encoding="utf-8"))
        locked = _locked_list(old)
        if locked:
            d["locked"] = locked                  # 锁定的方向跨天保留
            for t in reversed(locked):            # 也要在列表里，前端才高亮得到
                if t not in d["items"]:
                    d["items"] = [t] + d["items"]
    except Exception:
        pass
    try:
        EVO_SUGGEST_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    except Exception:
        pass
    return d


def set_suggest_lock(text: str) -> dict:
    """切换某个方向的锁定状态（**支持多选**）。text 传空串 = 全部解锁。返回最新缓存。"""
    d = load_suggestions(only_today=False) or {}
    items = list(d.get("items") or [])
    locked = _locked_list(d)
    t = (text or "").strip()
    if not t:
        locked = []                               # 传空 = 清空全部锁定
    elif t in locked:
        locked = [x for x in locked if x != t]    # 再点同一颗 = 取消它
    else:
        locked.append(t)
        if t not in items:
            items = [t] + items
    new = {"date": d.get("date") or datetime.now().strftime("%Y-%m-%d"),
           "at": d.get("at") or datetime.now().strftime("%H:%M"),
           "items": items[:8],
           "locked": locked}
    try:
        EVO_SUGGEST_FILE.write_text(json.dumps(new, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    except Exception:
        pass
    return new


def _run_evolution_item(cfg: dict, focus: str = "") -> str:
    """执行**一项**进化改动，返回说明文字。

    刻意一次只做一项：每次改动都要重启服务加载新代码，一次做完多项既不现实也不安全。
    进度**在启动前就先记上**，这样即使自我更新导致服务重启，计数也不会丢。

    focus：本次优先攻克的那个具体方向（用户在胶囊里点的）；留空就按整体方向自由发挥。
    """
    direction = (cfg.get("evolve_direction") or "").strip() or "提升自身的实用性与稳定性"
    if focus:
        direction = f"{focus}（整体方向里优先做这一项）"
    st = evolve_progress(cfg)

    # 先占位记录，防止重启丢失
    st["done"] = int(st.get("done", 0)) + 1
    st.setdefault("log", []).append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"), "summary": "（进行中…）"})
    st["log"] = st["log"][-30:]
    save_evolve_state(st)
    idx = st["done"]

    hint = f"\n本次是今天第 {idx} 项。"
    prompt = (
        "你是这个助手自身的**进化执行器**。请自主完成一项小而明确的改进并实施，然后结束。\n\n"
        f"用户设定的进化方向：{direction}{hint}\n"
        "要求：\n"
        "1. 先用 read_file / grep 看清相关代码，确认真实现状，不要凭猜测。\n"
        "2. 改动必须小、局部、可验证；不要大范围重写，不要改动核心循环的稳定性。\n"
        "3. 用 `selfupdate` 工具实施（它会备份 + 语法检查，失败会自动还原，成功会自动重启）。\n"
        "4. 调用 selfupdate 后本回合即结束，不要再调用任何工具。\n"
        "5. 最后只输出两行：第一行「已改进：<一句话>」，第二行「原因：<一句话>」。\n"
        "6. 如果确实找不到合适的改动，回复「本次跳过：<原因>」，不要硬改。"
    )

    approver = Approver(cfg, assume_yes=True, interactive=False)
    ev_agent = Agent(cfg, approver, show_reasoning=False)
    ev_agent.session_id = f"evolve-{datetime.now().strftime('%Y%m%d')}"
    ev_agent.emit = lambda k, v: None          # 进化过程不打扰用户界面
    out = ""
    try:
        ev_agent.run_turn(prompt, cancel=threading.Event())
        ev_agent.save_session()
        for m in reversed(ev_agent.messages):
            if m.get("role") == "assistant" and (m.get("content") or "").strip():
                out = m["content"].strip()
                break
    except Exception as e:
        out = f"（执行出错：{type(e).__name__}: {e}）"
    out = out[:400] or "（本轮没有产出说明）"

    # 回填结果；若判定跳过则把计数退回去
    st = load_evolve_state() or st
    if st.get("log"):
        st["log"][-1]["summary"] = out[:300]
    if out.startswith("本次跳过"):
        st["done"] = max(0, int(st.get("done", 1)) - 1)
        st["log"][-1]["summary"] = out[:300]
    save_evolve_state(st)
    return out


def _last_active_ts() -> float:
    """最近一次"人机互动"的时间戳 —— 取最近被写过的用户会话文件 mtime。

    发消息、回消息、存会话都会写这个文件，所以它能反映"是否正在被使用"。
    后台进化据此避开用户操作时段，免得它一改代码就重启、把用户的任务打断。
    """
    try:
        best = 0.0
        for p in SESSIONS_DIR.glob("*.json"):
            if p.stem.startswith(INTERNAL_SID_PREFIX):
                continue
            try:
                best = max(best, p.stat().st_mtime)
            except OSError:
                continue
        return best
    except Exception:
        return 0.0


def _evolve_loop(state: dict, cfg: dict) -> None:
    """后台进化调度：每 10 分钟看一次，按天执行设定项数。

    只在"确实空闲"时才动手：进化必然涉及改代码 + 重启，
    而重启会打断用户正在进行的任务，所以宁可等，也不要和人抢。
    """
    time.sleep(30)                              # 启动后缓一下，别和启动流程抢
    idle_need = float(cfg.get("evolve_idle_seconds") or 300)   # 默认闲置 5 分钟
    while True:
        try:
            if not cfg.get("evolve_enabled"):
                time.sleep(300); continue
            if state.get("agent_busy") or state.get("evolving"):
                time.sleep(120); continue
            # 刚跑完一轮时用户往往马上接下一句 —— 再等一会儿，别在人家手底下动手
            _idle = time.time() - _last_active_ts()
            if _idle < idle_need:
                time.sleep(60); continue
            # 每天顺带让 AI 研究一次"接下来能升级什么"，这样用户点开胶囊菜单时已有结果
            try:
                if not load_suggestions(only_today=True):
                    _items = _suggest_evolutions(cfg, 5)
                    if _items:
                        save_suggestions(_items)
                        _evolve_log(f"今日升级建议已生成 {len(_items)} 条")
            except Exception:
                pass

            st = evolve_progress(cfg)
            if st.get("done", 0) >= int(cfg.get("evolve_per_day") or 1):
                time.sleep(600); continue

            state["evolving"] = True
            ws_broadcast({"ev": "evolve_state", "d": {"running": True}})
            _evolve_log(f"自动进化：第 {st.get('done', 0) + 1} 项"
                        f"（方向：{cfg.get('evolve_direction') or '未指定'}）")
            try:
                result = _run_evolution_item(cfg)
                _evolve_log(f"结果：{result[:120]}")
                try:
                    log_change("自动进化", result)      # 同时写进「更新记录」
                except Exception:
                    pass
                ws_broadcast({"ev": "evolve_done", "d": result[:400]})
            finally:
                state["evolving"] = False
                ws_broadcast({"ev": "evolve_state", "d": {"running": False}})
        except Exception as e:
            _evolve_log(f"进化出错：{type(e).__name__}: {e}")
            state["evolving"] = False
        time.sleep(600)


def _schedule_distill(state: dict) -> None:
    """后台异步提炼记忆（不阻塞用户；每 DISTILL_EVERY 条用户消息一次）。"""
    agent = state["agent"]
    if agent.turns % DISTILL_EVERY != 0:
        return
    if state.get("distilling"):
        return
    state["distilling"] = True
    msgs = list(agent.messages)
    cfg = dict(agent.cfg)

    def work():
        try:
            new = distill_memory(cfg, msgs)
            if new:
                merge_memory(new)   # 只写文件；下一轮 run_turn 会自动纳入系统提示词
        except Exception:
            pass
        finally:
            state["distilling"] = False

    threading.Thread(target=work, daemon=True).start()


# ══ 增量落盘与优雅退出（2026-10-01 加）══════════════════════════════════════
# 事故：selfupdate 会 pkill 掉服务进程再拉起，而对话以前**只在整轮结束后**存一次盘
# （web 处理器 finally 里那一句 save_session）。pkill 发的是 SIGTERM，Python 不会
# 为它跑 finally —— 于是「做到一半的那一轮」整轮蒸发：用户消息、工具调用、结果、
# 思考全没了。重启后既不记得做过什么，也不知道下一步该干什么，只能跑偏。
#
# 实据（2026-10-01 15:19~15:22）：磁盘上留着 agent.py.bak-stepscollapse-152143，
# 说明那一轮确实跑了并改了代码；但 turn.log 里那一轮没有任何记录 —— 而写 turn.log
# 和 save_session 就在同一个 finally 里，说明 finally 根本没执行；会话里也搜不到
# "stepscollapse"。三条证据一起指向同一个结论。
#
# 对策：① 边做边存（节流），② 收到 SIGTERM/SIGINT 再冲最后一把。
# SIGKILL（pkill -9）抓不到，所以 ① 才是主力，② 只是补一道。
_LIVE_AGENTS = []
_AUTOSAVE_MIN_GAP = 1.5


def _flush_all_agents(*_a) -> None:
    """收到终止信号：把各会话最后状态写盘，然后才退出。"""
    for ag in list(_LIVE_AGENTS):
        try:
            ag.save_session()
        except Exception:
            pass
    try:
        sys.stderr.write("[退出] 已保存会话\n")
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(0)


def install_exit_hooks() -> None:
    """SIGTERM / SIGINT / SIGHUP → 先存盘再退出。"""
    for sig in ("SIGTERM", "SIGINT", "SIGHUP"):
        s = getattr(signal, sig, None)
        if s is None:
            continue
        try:
            signal.signal(s, _flush_all_agents)
        except Exception:
            pass


def write_resume_note_if_interrupted() -> str:
    """启动时看一眼「上个会话是不是被打断在半路」，是就写接续标记。

    以前只有 selfupdate 会写 _resume.md；可重启还有别的来路（手动 reload.sh、
    看门狗拉起、平板重启、断电…），那些情况下自愈没有任何线索，只能干看着。
    这里改成从**落盘的对话本身**推断，跟谁触发的重启无关。

    返回被打断的说明（空串表示上一轮是正常收尾，不写标记）。
    """
    try:
        sid = get_last_session() or ""
        msgs = load_session_messages(_safe_sid(sid)) if sid else None
        if not msgs or len(msgs) < 2:
            return ""
        last = msgs[-1]
        role = str(last.get("role") or "")
        _en = str(globals().get("UI_LANG") or "zh").lower().startswith("en")
        if role == "tool":
            why = ("The previous turn stopped right after a tool finished, before a reply "
                   "was generated" if _en else "上一轮停在一个工具刚跑完、还没生成回复的地方")
        elif role == "assistant" and last.get("tool_calls"):
            why = ("The previous turn was cut off right after it sent a tool call"
                   if _en else "上一轮刚发出工具调用就被掐断了")
        elif role == "assistant" and not str(last.get("content") or "").strip():
            why = ("The previous turn's reply was empty (most likely interrupted by a restart)"
                   if _en else "上一轮的回复是空的（多半被重启打断）")
        elif role == "user":
            why = ("There is a message that was never processed"
                   if _en else "有一条消息还没被处理")
        else:
            return ""
        if _en:
            (APP_DIR / "_resume.md").write_text(
                why + ", so the service was restarted/interrupted rather than finishing "
                "normally.\n"
                "First check the unchecked items in [the current task list], then review "
                "the last few turns and carry the unfinished work through; if it is in fact "
                "already done, just give a brief wrap-up.\n"
                "(This marker was generated by the startup self-check and is cleared once "
                "used.)",
                encoding="utf-8")
            return (why + "; the service was restarted/interrupted, not finished normally. "
                    "First check the unchecked items in [the current task list], then "
                    "review the last few turns and carry the unfinished work through; if it "
                    "is already done, just give a brief wrap-up. "
                    "(Startup self-check marker; cleared once used.)")
        (APP_DIR / "_resume.md").write_text(
            why + "，说明服务是被重启/打断的，不是正常收尾。\n"
            "请先看【当前任务清单】里没打勾的项，再回看最近几轮对话，"
            "把没做完的接着做完；如果其实已经做完，就简要汇报收尾。\n"
            "（这条标记由启动自检生成，用后即清。）",
            encoding="utf-8")
        return why
    except Exception:
        return ""


def check_unlogged_selfupdate() -> str:
    """启动自检：版本号变了、更新记录里却没有对应条目 → 自动补一条。

    背景（2026-10-01）：从 12:31 起，改自身代码一直绕过 selfupdate 直接写 agent.py
    （自己 cp 一份 bak-xxx 备份 + 手动 reload.sh）。后果是每次自更新都不留痕 ——
    更新记录里 14:48 到 15:38 之间整段空白，连 1.6.8 都查不到；改了什么、为什么改，
    事后完全无从追溯（当天「工具卡片被改成全手动」这个回归就是这么发生的：
    改的人自己都不记得，因为改动所在的那轮对话被重启吞了）。

    这里做个兜底：发现漂移就记一笔，至少能在界面「更新记录」里看到
    「某个时刻代码被改过而没登记」。

    用版本号比对而不是比文件时间 —— 分钟精度加上 copy2 会保留 mtime，时间不可靠；
    版本号是改代码的人自己写下的，最直接。
    """
    try:
        cur = VERSION
        fp = APP_DIR / ".last_version"
        try:
            seen = fp.read_text(encoding="utf-8").strip()
        except Exception:
            seen = ""
        if not seen:
            try:
                fp.write_text(cur, encoding="utf-8")
            except Exception:
                pass
            return ""
        if seen == cur:
            return ""
        items = load_changelog()
        top = str((items[0].get("version") if items else "") or "")
        try:
            fp.write_text(cur, encoding="utf-8")
        except Exception:
            pass
        if top == cur:
            return ""
        log_change("未记录的自更新",
                   "检测到代码版本从 %s 变成 %s，但更新记录里没有对应条目 —— 多半是没走 "
                   "selfupdate、直接改的 agent.py（那样不会自动备份、不做语法检查、也不留痕）。"
                   "这次改了什么无从追溯。下次改自身代码请用 selfupdate 工具。"
                   % (seen or "?", cur), cur)
        return "版本 %s → %s 没有更新记录，已补一条提醒" % (seen or "?", cur)
    except Exception:
        return ""


def cmd_web(cfg: dict, args) -> int:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import socket

    port = int(getattr(args, "port", None) or cfg.get("web_port") or 8765)
    no_open = bool(getattr(args, "no_open", False))
    foreground = bool(getattr(args, "fg", False))
    if getattr(args, "serve", False):      # 看门狗内部使用：前台静默运行
        foreground = True
        no_open = True
    host = "127.0.0.1"

    # 启动时给老会话补上模型记录（只补缺的，不影响已有记录的会话）
    try:
        n = migrate_session_models(cfg.get("model") or "", cfg.get("base_url") or "")
        if n:
            print(f"  已为 {n} 个旧对话补上模型记录：{cfg.get('model')}")
    except Exception:
        pass

    if getattr(args, "stop", False):
        return _stop_server()

    url = f"http://{host}:{port}"

    # 已经在跑 → 直接把窗口叫出来
    if _server_alive(port):
        print(green(f"  已经在运行 → {url}"))
        print(dim("  要重启就执行： agent restart"))
        if not no_open:
            _open_url(url)
        return 0

    # ---- 记录一次服务启动（restart.log）----
    # 专门用来追踪「改代码 → 重启 → 页面会话看着丢了」这件事：
    # 每次服务真正启动都留一条，用户在界面上发现"对话断了"时，可以拿时间点来对账。
    # 顺便记下最近会话 id —— 会话文件其实一直在磁盘上，一条都没少。
    try:
        _rp = APP_DIR / "restart.log"
        _n = 0
        if _rp.exists():
            _n = sum(1 for _ in _rp.open(encoding="utf-8", errors="replace"))
        _sid = get_last_session() or "?"
        with open(_rp, "a", encoding="utf-8") as _rf:
            _rf.write("%s | 第 %d 次启动 | 界面会重连、看着像会话断了（记录都在磁盘） | 最近会话=%s\n"
                      % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), _n + 1, _sid))
    except Exception:
        pass

    if not cfg.get("api_key"):
        print(red("未配置 API Key。请执行： agent config"))
        return 2

    # 固定端口：绝不自动换端口，否则"添加到主屏幕"的书签会失效。
    # 注意要用 SO_REUSEADDR（和真正服务器一致），否则 TIME_WAIT 残留连接会被误判为"端口被占用"。
    free = False
    for attempt in range(6):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            free = True
            break
        except OSError:
            if attempt < 5:
                time.sleep(1)
        finally:
            s.close()
    if not free:
        print(red(f"端口 {port} 被占用。若确定服务没在运行，可执行 agent web --stop 清理后重试。"))
        return 1
    url = f"http://{host}:{port}"

    approver = Approver(cfg, assume_yes=True, interactive=False)
    _migrate_old_session()
    # show_reasoning=True：把模型的思考过程流式发给界面（默认折叠展示，可点开）
    agent = Agent(cfg, approver, show_reasoning=True)
    # 重启后恢复"上次在用的会话"，而不是按文件时间猜（否则可能回到一个空对话）
    recent = list_sessions()
    last = get_last_session()
    # 重启后恢复"上次在用的会话"。踩过的坑：Agent.load_session 失败时返回 False，
    # 并且**根本不动 self.session_id**（只有成功那行才赋值），而这里原先不检查返回值 ——
    # 于是失败就静默沿用构造函数里的新 id，前端一认领，用户看到的就是
    # 「聊天记录没了、冒出一个空对话」。selfupdate 重启时最容易撞上：
    # 上一个进程可能刚写到一半就被拉下来，新进程去读那个文件 JSON 解析失败。
    # 现在：失败先等写盘落定重试一次；仍失败也要把 session_id 钉在原会话上，绝不新开。
    target = last if (last and any(x["id"] == last for x in recent)) else (
        recent[0]["id"] if recent else "")
    if target:
        if not agent.load_session(target):
            time.sleep(0.6)
            if not agent.load_session(target):
                agent.session_id = target      # 消息没读出来，但会话身份必须保住
                print(f"[warn] 会话 {target} 消息读取失败，已保留会话 id，不新开对话",
                      flush=True)
    else:
        agent.session_id = _new_sid()
    hub = AgentHub(cfg, approver)
    hub.adopt(agent)
    state = {"agent": agent, "hub": hub, "distilling": False}

    # 每次启动都同步一遍看门狗脚本：模板可能刚被 selfupdate 改过。
    # 千万别放回下面的 fork 分支里 —— --serve 模式（看门狗拉起服务时用的）
    # 会让 foreground=True，那个分支整个被跳过，脚本就永远更新不了。
    ensure_supervisor()

    # ---- 后台化：fork 出一个脱离会话的子进程，交给看门狗托管
    if not foreground:
        if hasattr(os, "fork"):
            try:
                pid = os.fork()
            except OSError:
                pid = -1
            if pid > 0:
                # 父进程：等服务起来，然后打开窗口就退出
                for _ in range(90):
                    if _server_alive(port):
                        break
                    time.sleep(0.2)
                print(green(f"  Sidekick 已启动 → {url}"))
                print(dim("  关掉这个终端也不影响；要停止请输入：agent web --stop"))
                if not no_open:
                    _open_url(url)
                return 0
            if pid == 0:
                # 子进程：脱离会话、重定向日志，然后用看门狗替换自身
                os.setsid()
                try:
                    APP_DIR.mkdir(parents=True, exist_ok=True)
                    log = open(APP_DIR / "supervisor.log", "a",
                               encoding="utf-8", errors="ignore")
                    os.dup2(log.fileno(), 1)
                    os.dup2(log.fileno(), 2)
                    devnull = open(os.devnull, "rb")
                    os.dup2(devnull.fileno(), 0)
                except Exception:
                    pass
                try:
                    subprocess.Popen(["termux-wake-lock"], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
                except Exception:
                    pass
                try:
                    os.execv("/data/data/com.termux/files/usr/bin/bash",
                             ["bash", str(SUPERVISOR)])
                except Exception:
                    pass
                os._exit(0)
        else:
            foreground = True          # 非 POSIX（比如在电脑上跑）就留在前台

    # ---- 后台进化调度 + 唤醒锁保活 + adb 通道维护（只在提供服务的那层进程里跑）
    threading.Thread(target=_evolve_loop, args=(state, cfg), daemon=True).start()
    threading.Thread(target=wake_lock_loop, daemon=True).start()
    threading.Thread(target=adb_keepalive_loop, daemon=True).start()

    # ---- 自愈与续跑（实现在 selfheal.py）：
    # 捕获崩溃 → 自动开个静默会话定位并 selfupdate 修复（带指纹去重与限流）；
    # 任务中断（服务重启 / 轮次达上限）→ 自动接着做完，每会话有次数上限。
    try:
        import selfheal as _selfheal
        _selfheal.install(
            state, cfg,
            make_agent=lambda: Agent(cfg, Approver(cfg, assume_yes=True, interactive=False),
                                     show_reasoning=False),
            agent_of=lambda st: _agent_of(st, None),
            load_todo=load_todo,
            broadcast=ws_broadcast,
            log_change=log_change,
            logger=_evolve_log,
        )
    except Exception:
        pass

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            # 默认静音；只把「页面加载 / 面板拉取 / WS 连接」记到 access.log，用于排查前端问题
            try:
                line = (fmt % args) if args else str(fmt)
                if ("GET / HTTP" in line) or ("/api/panel" in line) or ("/ws" in line):
                    p = os.path.expanduser("~/.termux-agent/access.log")
                    if os.path.exists(p) and os.path.getsize(p) > 512 * 1024:
                        os.remove(p)                 # 简单滚动，别让它无限长
                    with open(p, "a", encoding="utf-8") as f:
                        f.write(time.strftime("%m-%d %H:%M:%S ") + line + "\n")
            except Exception:
                pass

        def _send(self, code, body, ctype):
            if isinstance(body, str):
                body = body.encode("utf-8")
            try:
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        # ---------------- WebSocket：长连接，双向通信 ----------------
        def _ws_loop(self, state, cfg):
            key = self.headers.get("Sec-WebSocket-Key") or ""
            if not key or "websocket" not in (self.headers.get("Upgrade") or "").lower():
                self._send(400, "expected websocket upgrade", "text/plain")
                return
            self.close_connection = True
            try:
                self.wfile.write(
                    ("HTTP/1.1 101 Switching Protocols\r\n"
                     "Upgrade: websocket\r\n"
                     "Connection: Upgrade\r\n"
                     f"Sec-WebSocket-Accept: {ws_accept_value(key)}\r\n\r\n"
                     ).encode("ascii"))
                self.wfile.flush()
            except Exception:
                return

            outq: "queue.Queue" = queue.Queue()
            sock = self.connection

            def writer():
                while True:
                    try:
                        item = outq.get()
                    except Exception:
                        break
                    if item is None:
                        break
                    try:
                        ws_send_frame(sock, json.dumps(item, ensure_ascii=False).encode("utf-8"), 1)
                    except Exception:
                        break

            wt = threading.Thread(target=writer, daemon=True)
            wt.start()
            WS_CLIENTS.add(outq)
            turn = {"stop": threading.Event(), "busy": False}
            conn = {"sid": state["agent"].session_id}      # 本连接在看哪个会话
            agent = _agent_of(state, conn) or state["agent"]

            def push(ev, data=None):
                outq.put({"ev": ev, "d": data})

            try:
                push("hello", hello_payload(agent, agent.cfg))
                push("history", history_payload(agent))
                push("sessions", sessions_payload(agent))
                _sg = load_suggestions(only_today=True)   # 常驻的进化方向胶囊：连上就推
                if _sg.get("items"):
                    push("evolve_suggestions", _sg)
                while True:
                    op, payload = ws_recv_frame(self.rfile)
                    if op == 0x8:                       # close
                        break
                    if op == 0x9:                       # ping → pong
                        ws_send_frame(sock, payload, 0xA)
                        continue
                    if op != 0x1:                       # 只处理文本帧
                        continue
                    try:
                        msg = json.loads(payload.decode("utf-8", "ignore"))
                    except Exception:
                        continue
                    self._ws_command(msg, state, cfg, push, turn, conn)
            except Exception:
                pass
            finally:
                WS_CLIENTS.discard(outq)
                try:
                    turn["stop"].set()
                except Exception:
                    pass
                outq.put(None)

        def _ws_command(self, msg, state, cfg, push, turn, conn):
            cmd = msg.get("cmd")
            hub = state.get("hub")
            agent = _agent_of(state, conn) or state["agent"]
            scfg = agent.cfg            # 会话级配置（模型/地址跟着会话走）

            if cmd == "chat":
                text = (msg.get("message") or "").strip()
                if not text:
                    return
                # 用户在这个会话说话 → 它就是"当前会话"。自愈续跑（重启后接着做完）
                # 靠这个判断该往哪个会话发，否则会发到启动时的默认实例里去。
                if state.get("hub"):
                    state["hub"].touch(agent.session_id)
                if turn["busy"]:
                    push("error", "这个对话的上一条还没结束，请先停止")
                    return
                if getattr(agent, "_running", False):
                    push("error", "这个对话正在回复中（可能来自另一个窗口），等它结束再发")
                    return
                stop_evt = threading.Event()
                turn["stop"] = stop_evt
                mark_last_session(agent.session_id)

                def work():
                    turn["busy"] = True
                    _t_start = time.time()          # 本轮起点，用于算耗时
                    agent._running = True
                    state["agent_busy"] = True
                    agent.emit = (lambda k, v: push("evt", {"k": k, "v": v,
                                                           "sid": agent.session_id}))
                    try:
                        agent.run_turn(text, cancel=stop_evt)
                    except Exception as e:
                        push("evt", {"k": "error", "v": f"{type(e).__name__}: {e}"})
                    finally:
                        try:
                            agent.save_session()
                        except Exception:
                            pass
                        # 记录这一轮"是怎么结束的"到 turn.log：
                        # 事后排查「任务为什么中断、断在哪一步」就靠它。
                        # 结束原因从会话最后一条消息反推 —— 不用改 run_turn 内部。
                        try:
                            _msgs = agent.messages or []
                            _last = _msgs[-1] if _msgs else {}
                            _lr = str(_last.get("role") or "")
                            _lc = str(_last.get("content") or "")
                            if _lr == "assistant":
                                if _lc.strip():
                                    _why = "轮次上限" if "轮次达到上限" in _lc else "正常完成"
                                elif _last.get("tool_calls"):
                                    _why = "中断-工具已发起但结果没收全"
                                else:
                                    _why = "中断-回复为空(被重启/断连打断)"
                            elif _lr == "tool":
                                _why = "中断-工具跑完后没生成回复"
                            elif _lr == "user":
                                _why = "中断-消息没被处理"
                            else:
                                _why = "未知(" + (_lr or "空") + ")"
                            with open(APP_DIR / "turn.log", "a", encoding="utf-8") as _f:
                                _f.write(
                                    "%s | %-24s | 轮次=%-3d | 耗时=%7.1fs | 会话=%s | %s\n" % (
                                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"), _why,
                                        agent.turns, time.time() - _t_start, agent.session_id,
                                        (text or "")[:40].replace("\n", " ")))
                        except Exception:
                            pass
                        state["agent_busy"] = False
                        turn["busy"] = False
                        agent._running = False
                        agent.stream_text = ""
                        # 只在「这个连接还看着这个会话」时才推会话专属内容；
                        # 否则用户正看着别的对话，这边一收尾就把他的视图拽回去。
                        if conn.get("sid") == agent.session_id:
                            push("evt", {"k": "done", "v": ""})
                            push("hello", hello_payload(agent, agent.cfg))
                            push("history", {"sid": agent.session_id,
                                             "msgs": history_payload(agent)})
                        push("sessions", sessions_payload(
                            agent, conn.get("sid") or agent.session_id))
                        _schedule_distill(state)
                threading.Thread(target=work, daemon=True).start()
                return

            if cmd == "stop":
                turn["stop"].set()
                try:                      # 顺带放掉正在等用户作答的提问，别让它干等
                    with ASK_LOCK:
                        for _it in list(ASK_WAIT.values()):
                            _it["evt"].set()
                except Exception:
                    pass
                if not turn.get("busy"):
                    # 这一侧其实没有在跑的任务 —— 说明前端状态错位了
                    # （多半是上一轮的 done 事件正好在断线/切后台时丢了）。
                    # 直接回一个 done 让它把「停止」按钮和待发送区复位，别让用户干等。
                    push("evt", {"k": "done", "v": ""})
                    push("info", "当前没有在跑的任务，已为你复位界面")
                else:
                    push("info", "已请求停止")
                return

            if cmd == "inject":
                _txt = (msg.get("message") or "").strip()
                if not _txt:
                    push("info", "插队内容为空")
                elif not turn.get("busy"):
                    push("info", "当前没有在跑的任务，这条直接发就行")
                else:
                    INJECT_QUEUE.put(_txt)
                    push("info", "已插队，会在下一步（下一轮）生效，任务不会中断")
                return

            if cmd == "reset":
                agent.reset()
                agent.save_session()
                conn["sid"] = agent.session_id      # 本连接切到新会话
                mark_last_session(agent.session_id)
                if hub:
                    hub.adopt(agent)
                push("hello", hello_payload(agent, agent.cfg))
                push("history", [])
                push("sessions", sessions_payload(agent))
                return

            if cmd == "archive":            # 归档：先压缩上下文，再把原文一起搬进 archive/
                sid = (msg.get("id") or "").strip()
                if not sid:
                    return
                if sid == agent.session_id and turn["busy"]:
                    push("error", "这个对话正在干活，等它停下来再归档")
                    return

                def arch_work(sid=sid):
                    try:
                        rec = archive_session(sid, cfg)
                        if sid == agent.session_id:     # 归档的是当前对话 → 自动开个新对话接着聊
                            agent.reset()
                            agent.save_session()
                            conn["sid"] = agent.session_id      # 本连接跟着走到新对话
                            push("hello", hello_payload(agent, agent.cfg))
                            push("history", [])
                        push("sessions", sessions_payload(agent))
                        push("info", f"已归档「{rec['title'] or '无标题'}」：摘要 + 完整原文都在 archive/，"
                                     f"可随时「恢复」")
                    except Exception as e:
                        push("error", f"归档失败：{e}")
                        push("sessions", sessions_payload(agent))

                push("info", "正在压缩上下文并归档…")
                threading.Thread(target=arch_work, daemon=True).start()
                return

            if cmd == "unarchive":
                new_sid = unarchive_session((msg.get("id") or "").strip())
                push("info", "已恢复到最近对话" if new_sid else "归档里没有这个会话")
                push("sessions", sessions_payload(agent))
                return

            if cmd == "del_session":
                sid = (msg.get("id") or "").strip()
                archived = bool(msg.get("archived"))
                is_current = (not archived and sid == agent.session_id)
                # 删之前先算好"接下来该看哪个"：有置顶的就选置顶，否则选列表里下一个
                nxt = ""
                if is_current:
                    pool = [x for x in list_sessions() if x.get("id") != sid]
                    pool.sort(key=lambda x: not x.get("pinned"))
                    if pool:
                        nxt = pool[0]["id"]
                ok = delete_session_file(sid, archived)
                if hub:
                    hub.drop(sid)               # 从内存池里也清掉
                push("info", "已删除" if ok else "没找到这个会话")
                if is_current:
                    # 删的正是当前在看的对话 → 自动切走，别让界面停在一个已删除的对话上
                    ag2 = hub.get(nxt) if (nxt and hub) else None
                    if ag2 is not None:
                        conn["sid"] = nxt
                        mark_last_session(nxt)
                        agent = ag2
                        push("hello", hello_payload(agent, agent.cfg))
                        push("history", history_payload(agent))
                        push("todo", todo_payload(nxt))
                    else:
                        # 一个都不剩了 → 新开一个空对话接上
                        agent.reset()
                        agent.save_session()
                        if hub:
                            hub.adopt(agent)
                        conn["sid"] = agent.session_id
                        mark_last_session(agent.session_id)
                        push("hello", hello_payload(agent, agent.cfg))
                        push("history", history_payload(agent))
                        push("todo", todo_payload(agent.session_id))
                        push("info", "已经是最后一个对话，已新建一个")
                push("sessions", sessions_payload(agent, conn.get("sid") or agent.session_id))
                return

            if cmd == "pin":
                sid = (msg.get("id") or "").strip()
                want = bool(msg.get("pinned"))
                known = {x.get("id") for x in (list_sessions() + list_archived())}
                if not sid or sid not in known:
                    push("error", "找不到该会话")
                    return
                if pin_session(sid, want):
                    push("sessions", sessions_payload(agent, conn.get("sid") or agent.session_id))
                    push("info", "已置顶，会排在最前面" if want else "已取消置顶")
                else:
                    push("error", "置顶操作失败")
                return

            if cmd == "switch":
                sid = (msg.get("id") or "").strip()
                known = {x.get("id") for x in (list_sessions() + list_archived())}
                if not sid or sid not in known:
                    push("error", "找不到该会话")
                    return
                # 每个会话有自己的 Agent 实例：切过去只是换视图，别的对话照跑不误
                ag2 = hub.get(sid) if hub else None
                if ag2 is None:
                    push("error", "打不开这个会话")
                    return
                conn["sid"] = sid
                mark_last_session(sid)
                agent = ag2
                push("hello", hello_payload(agent, agent.cfg))
                push("history", history_payload(agent))
                push("sessions", sessions_payload(agent))
                push("todo", todo_payload(sid))
                lm = load_session_model(sid) or {}
                if lm.get("model"):
                    push("info", "这个对话用的是 " + str(lm["model"]))
                return

            if cmd == "rename":
                sid = (msg.get("id") or "").strip()
                new_title = (msg.get("title") or "").strip()
                known = {x.get("id") for x in (list_sessions() + list_archived())}
                if not sid or sid not in known:
                    push("error", "找不到该会话")
                    return
                # 传空标题 = 恢复自动提炼
                if rename_session(sid, new_title):
                    push("sessions", sessions_payload(agent))
                    push("info", "已重命名为「%s」" % (new_title or "自动标题"))
                else:
                    push("error", "重命名失败")
                return

            if cmd == "sessions":
                push("sessions", sessions_payload(agent))
                return

            if cmd == "files":                 # 输入框里 @ 引用的文件补全
                root = cfg.get("workdir") or str(Path.home())
                push("files", {"root": root, "list": files_for_ref(root)})
                return

            if cmd == "panel":
                push("panel", panel_payload(agent, cfg, bool(state.get("evolving"))))
                return

            if cmd == "todo":                 # 前端重连后主动拉一次清单
                push("todo", todo_payload(agent.session_id))
                return

            if cmd == "log_list":             # 运行日志：列出所有 .log
                push("log_list", list_logs())
                return

            if cmd == "log_tail":             # 运行日志：读某个日志的尾部
                push("log_tail", read_log_tail(msg.get("name"), msg.get("tail", 300)))
                return

            if cmd == "log_check":            # 运行日志：让 AI 读日志给诊断（要调模型，放后台线程）
                _log_name = (msg.get("name") or "").strip()
                if not _log_name:
                    push("log_check", {"ok": False, "error": "没指定日志文件"})
                    return
                push("info", "AI 正在分析日志，稍等…")

                def _log_work(name=_log_name, tail=msg.get("tail", 300)):
                    try:
                        push("log_check", ai_selfcheck(cfg, name, tail))
                    except Exception as e:
                        push("log_check", {"ok": False, "error": "自检失败：" + str(e)})

                threading.Thread(target=_log_work, daemon=True).start()
                return

            if cmd == "probe_models":         # 设置页：填入 Key 后自动拉可用模型
                _b = str(msg.get("base_url") or cfg.get("base_url") or "").strip()
                _k = str(msg.get("api_key") or "").strip() or provider_key_of(cfg, msg.get("brand") or "") \
                     or (cfg.get("api_key") or "")
                _ms, _err, _src = probe_models(_b, _k)
                push("models", {"ok": bool(_ms), "models": _ms, "error": _err,
                                "source": _src, "base_url": _b})
                return

            if cmd == "get_balance":          # 设置页：查余额 / 积分
                _b = str(msg.get("base_url") or cfg.get("base_url") or "").strip()
                _k = str(msg.get("api_key") or "").strip() or provider_key_of(cfg, msg.get("brand") or "") \
                     or (cfg.get("api_key") or "")
                _bi, _err = query_balance(_b, _k, cfg)
                push("balance", {"ok": bool(_bi), "balance": _bi, "error": _err, "base_url": _b})
                return

            if cmd == "skills_tools":         # 控制台「技能与工具」页：一次把两边都给它
                push("skills_tools", {"skills": list_skills(), "tools": list_tools(),
                                      "disabled": load_disabled_tools()})
                return

            if cmd == "skill_explain":        # 点开技能 → AI 解释（首次生成后永久保存）
                _sn = str(msg.get("name") or "").strip()
                if not _sn:
                    push("error", "技能名不能为空")
                    return
                _force = bool(msg.get("force"))
                if not _force:
                    _c = read_skill_explain(_sn)
                    if _c and not _c.get("stale"):
                        push("skill_explain", {"name": _sn, "text": _c["text"],
                                               "at": _c["at"], "cached": True})
                        return
                push("skill_explain", {"name": _sn, "loading": True, "force": _force})

                def _ex_work(_n=_sn):
                    try:
                        _t = _clean_explain(gen_skill_explain(cfg, _n))
                        if not _t:
                            raise RuntimeError("模型返回为空")
                        save_skill_explain(_n, _t)
                        push("skill_explain", {"name": _n, "text": _t,
                                               "at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                               "cached": False})
                    except Exception as _e:
                        push("skill_explain", {"name": _n, "error": str(_e)})
                    push("skills_tools", {"skills": list_skills(), "tools": list_tools(),
                                          "disabled": load_disabled_tools()})

                threading.Thread(target=_ex_work, daemon=True).start()
                return

            if cmd == "skills_delete":        # 删技能文档（服务端会先备份到 skills/.trash/）
                _names = msg.get("names") or []
                if isinstance(_names, str):
                    _names = [_names]
                _ok, _bad = [], []
                for _n in _names:
                    try:
                        _ok.append(str(_n) + "（已备份 " + delete_skill(_n) + "）")
                    except Exception as _e:
                        _bad.append(str(_n) + "：" + str(_e))
                push("skills_tools", {"skills": list_skills(), "tools": list_tools(),
                                      "disabled": load_disabled_tools()})
                if _ok:
                    push("info", "已删除 " + str(len(_ok)) + " 个技能：" + "；".join(_ok))
                if _bad:
                    push("error", "有 " + str(len(_bad)) + " 个没删成：" + "；".join(_bad))
                return

            if cmd == "tools_disable":        # 禁用 / 恢复工具：只影响发给模型的定义，函数本身不动
                _names = msg.get("names") or []
                if isinstance(_names, str):
                    _names = [_names]
                _on = bool(msg.get("on", True))          # True=禁用，False=恢复
                _cur = set(load_disabled_tools())
                if _on:
                    _cur |= {str(x) for x in _names}
                else:
                    _cur -= {str(x) for x in _names}
                _saved = save_disabled_tools(sorted(_cur))
                push("skills_tools", {"skills": list_skills(), "tools": list_tools(),
                                      "disabled": _saved})
                push("info", ("已禁用 " if _on else "已恢复 ") + str(len(_names)) + " 个工具，"
                             "下一轮对话生效；当前共禁用 " + str(len(_saved)) + " 个")
                return

            if cmd == "ask_reply":            # 用户在提问面板上选好了
                aid = msg.get("id") or ""
                ans = msg.get("answers")
                if not isinstance(ans, list):
                    ans = [ans] if ans is not None else []
                with ASK_LOCK:
                    item = ASK_WAIT.get(aid)
                if item:
                    item["answers"] = ans
                    item["evt"].set()
                return

            if cmd == "todo_clear":
                try:
                    TODO_PATH.unlink()
                except Exception:
                    pass
                ws_broadcast({"ev": "todo",
                              "d": {"sid": agent.session_id, "time": "", "items": []}})
                return

            if cmd == "save_panel":
                changed = []
                if msg.get("name", "").strip():
                    cfg["assistant_name"] = msg["name"].strip()[:20]; changed.append("名称")
                if "evolve_direction" in msg:
                    cfg["evolve_direction"] = str(msg["evolve_direction"])[:2000]; changed.append("进化方向")
                if "evolve_per_day" in msg:
                    try:
                        cfg["evolve_per_day"] = max(1, min(10, int(msg["evolve_per_day"])))
                        changed.append("每日项数")
                    except Exception:
                        pass
                if "evolve_enabled" in msg:
                    cfg["evolve_enabled"] = bool(msg["evolve_enabled"]); changed.append("进化开关")
                if "max_tool_rounds" in msg:
                    try:
                        cfg["max_tool_rounds"] = max(10, min(1000, int(msg["max_tool_rounds"])))
                        changed.append("单轮轮次上限")
                    except Exception:
                        pass
                if "memory" in msg:
                    save_memory(str(msg["memory"])[:MEMORY_MAX_CHARS]); changed.append("记忆")
                save_config(cfg)
                agent._rebuild_system()
                push("panel_saved", changed)
                push("panel", panel_payload(agent, cfg, bool(state.get("evolving"))))
                push("hello", hello_payload(agent, agent.cfg))
                return

            if cmd == "get_config":
                push("config", config_payload(agent.cfg))
                return

            if cmd == "save_config":
                changed = []
                c = agent.cfg                     # 本会话的配置
                new_base = str(msg.get("base_url") or "").strip()
                old_base = c.get("base_url") or ""
                # 云端 → 本地：把云端那套地址记下来，切回去时自动还原
                if new_base and _is_local_url(new_base) and not _is_local_url(old_base):
                    if old_base:
                        cfg["cloud_base_url"] = old_base
                    cfg["cloud_model"] = c.get("model") or ""
                # 本地 → 云端：没给新地址就还原记忆里的云端地址
                if old_base and _is_local_url(old_base) and not _is_local_url(new_base or old_base):
                    if not new_base:
                        new_base = cfg.get("cloud_base_url") or DEFAULT_CLOUD_URL

                def _set(k, v):
                    c[k] = v          # 会话级
                    cfg[k] = v        # 全局（当作新对话的默认值）

                sm = str(msg.get("model") or "").strip()
                if sm:
                    _set("model", sm); changed.append("模型")
                if new_base:
                    _set("base_url", new_base); changed.append("接口")
                # 多服务商：所选模型若属于别家，自动换上那家的地址与 Key
                if sm:
                    _pn = sync_provider_for_model(cfg, sm)
                    if _pn:
                        c["base_url"] = cfg["base_url"]; c["api_key"] = cfg["api_key"]
                        c["provider"] = cfg["provider"]
                        if "接口" not in changed: changed.append("接口")
                        changed.append(_pn + " Key")
                sk = str(msg.get("api_key") or "").strip()
                if sk and not (sk == "local" and _is_local_url(new_base or c.get("base_url"))):
                    _set("api_key", sk); changed.append("Key")   # 本地不写占位 Key
                if "shell_access" in msg:
                    _set("shell_access", bool(msg["shell_access"])); changed.append("权限")
                ul = str(msg.get("ui_language") or "").strip()
                if ul and ul in I18N_BUILD:
                    _set("ui_language", ul)
                    globals()["UI_LANG"] = ul
                    changed.append("语言")
                if isinstance(msg.get("ui"), dict):
                    # 外观：与 config.json 里已存的值合并后夹取合法范围
                    ui = dict(get_appearance(cfg))
                    for k, v in (msg["ui"] or {}).items():
                        if k in APPEARANCE_DEFAULTS:
                            lo, hi = APPEARANCE_RANGE[k]
                            try:
                                ui[k] = max(lo, min(hi, int(round(float(v)))))
                            except (TypeError, ValueError):
                                pass
                    if ui["line"] < ui["font"] + 2:
                        ui["line"] = ui["font"] + 2
                    cfg["ui"] = ui
                    c["ui"] = ui          # 本会话也写：config_payload(c) 是回给前端的那份，
                    # 只写 cfg 靠 hub.sync_shared 兜着太脆 —— hub 为空时会
                    # 把旧值回给前端，面板刚保存完就弹回去（2026-10-01）
                    changed.append("外观")

                save_config(cfg)
                if hub:
                    hub.sync_shared(cfg)          # Key/权限等共享字段同步给别的会话
                agent._rebuild_system()
                update_session_model(agent.session_id, c.get("model") or "",
                                     c.get("base_url") or "")   # 只影响本对话
                _maybe_refresh_windows(cfg)
                push("config", config_payload(c))
                push("hello", hello_payload(agent, c))
                # 只改了外观就不弹提示：外观现在是自动落盘的，拖一下滑块会刷出一串
                # 「已更新：外观」（2026-10-01）
                if changed != ["外观"]:
                    push("info", "已更新：" + ("、".join(changed) or "无变化")
                         + "（只作用于当前对话）")
                return

            if cmd == "set_ratio":
                # percent: 用到模型窗口的百分之多少就压缩；scope: session=本对话，default=写进配置
                try:
                    pct = float(msg.get("percent") or 0)
                except Exception:
                    push("info", "比例要填数字")
                    return
                if not (10 <= pct <= 95):
                    push("info", "比例请填 10 ~ 95 之间的百分数")
                    return
                r = agent.set_compress_ratio(pct)
                if msg.get("scope") == "default":
                    cfg["compress_ratio"] = round(r, 3)
                    save_config(cfg)
                    note = f"已把 {round(r*100)}% 设为默认值（新对话都用它）"
                else:
                    note = f"本对话改为窗口用到 {round(r*100)}% 时压缩"
                push("info", note)
                push("panel", panel_payload(agent, cfg, bool(state.get("evolving"))))
                push("hello", hello_payload(agent, agent.cfg))
                return

            if cmd == "compress":
                try:
                    did = agent._maybe_compress(force=True)
                    push("info", "已压缩较早的对话" if did else "对话还很短，无需压缩")
                except Exception as e:
                    push("info", f"压缩失败：{type(e).__name__}: {e}")
                agent.save_session()
                push("panel", panel_payload(agent, cfg, bool(state.get("evolving"))))
                return

            if cmd == "evolve_lock":         # 胶囊点选/取消（可多选）：切换这颗的锁定状态
                t = (msg.get("text") or "").strip()[:20]
                d = set_suggest_lock(t)      # 已在列表里就移除，否则加进去
                locked = _locked_list(d)
                cfg["evolve_direction"] = "；".join(locked)   # 锁定的方向合起来作为进化方向
                try:
                    save_config(cfg)
                    hub.sync_shared(cfg)
                except Exception:
                    pass
                push("evolve_suggestions", d)
                push("panel", panel_payload(agent, cfg, bool(state.get("evolving"))))
                if not locked:
                    push("info", "已解锁全部方向，之后每天由 AI 自动换一批")
                elif t in locked:
                    push("info", f"已锁定：{t}（当前锁定 {len(locked)} 个方向）")
                else:
                    push("info", f"已取消：{t}（当前锁定 {len(locked)} 个方向）")
                return

            if cmd == "evolve_suggest":      # 让 AI 研究几个可升级的方向（给胶囊菜单用）
                want_fresh = bool(msg.get("fresh"))

                def suggest_work():
                    try:
                        cache = {} if want_fresh else load_suggestions(only_today=True)
                        if cache.get("items"):
                            push("evolve_suggestions", cache)
                            return
                        push("info", "正在让 AI 研究可升级的方向…")
                        items = _suggest_evolutions(agent.cfg, 5)
                        if not items:
                            push("error", "AI 这次没给出建议（可能网络或模型抽风），稍后再试")
                            return
                        push("evolve_suggestions", save_suggestions(items))
                    except Exception as e:
                        push("error", f"生成建议失败：{e}")

                threading.Thread(target=suggest_work, daemon=True).start()
                return

            if cmd == "evolve":
                if state.get("evolving"):
                    push("info", "正在进化中，请稍候")
                    return
                if not (cfg.get("evolve_direction") or "").strip():
                    push("info", "请先填写进化方向")
                    return

                _focus = (msg.get("direction") or "").strip()

                def evolve_now():
                    state["evolving"] = True
                    ws_broadcast({"ev": "evolve_state", "d": {"running": True}})
                    try:
                        _evolve_log("用户手动触发一次进化"
                                    + (f"（方向：{_focus}）" if _focus else ""))
                        result = _run_evolution_item(cfg, focus=_focus)
                        _evolve_log(f"手动进化完成：{result[:120]}")
                        try:
                            log_change("手动进化", result)
                        except Exception:
                            pass
                        ws_broadcast({"ev": "evolve_done", "d": result[:400]})
                    except Exception as e:
                        _evolve_log(f"手动进化出错：{type(e).__name__}: {e}")
                        ws_broadcast({"ev": "evolve_done", "d": f"出错：{e}"})
                    finally:
                        state["evolving"] = False
                        ws_broadcast({"ev": "evolve_state", "d": {"running": False}})
                threading.Thread(target=evolve_now, daemon=True).start()
                push("info", "已开始进化，完成后会通知你")
                return

            push("error", f"未知指令：{cmd}")

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path == "/ws":
                # 页面连上（或重启后重连）—— 和上面 restart.log 里的「服务启动」记录对着看，
                # 就能算出界面上会话断开了多久，也能区分「是重启断的还是网络抖的」。
                try:
                    with open(APP_DIR / "restart.log", "a", encoding="utf-8") as _rf:
                        _rf.write("%s | 页面已连上 | 前端 WebSocket 握手成功\n"
                                  % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                except Exception:
                    pass
                self._ws_loop(state, cfg)
                return
            if path in ("/", "/index.html"):
                self._send(200, render_web_html(), "text/html; charset=utf-8")
            elif path == "/manifest.webmanifest":
                self._send(200, json.dumps(MANIFEST, ensure_ascii=False),
                           "application/manifest+json; charset=utf-8")
            elif path == "/sw.js":
                self._send(200, SW_JS, "application/javascript; charset=utf-8")
            elif path == "/icon.svg":
                self._send(200, ICON_SVG, "image/svg+xml")
            elif path == "/api/state":
                self._send(200, json.dumps(hello_payload(state["agent"], cfg) | {"ready": True},
                                           ensure_ascii=False), "application/json")
            elif path == "/api/sessions":
                self._send(200, json.dumps({
                    "current": state["agent"].session_id,
                    "sessions": list_sessions()}, ensure_ascii=False), "application/json")
            elif path == "/api/history":
                self._send(200, json.dumps({"messages": history_payload(state["agent"])},
                                           ensure_ascii=False), "application/json")
            elif path == "/api/panel":
                self._send(200, json.dumps(panel_payload(state["agent"], cfg, bool(state.get("evolving"))), ensure_ascii=False),
                           "application/json")
            elif path == "/api/config":
                self._send(200, json.dumps(config_payload(cfg), ensure_ascii=False),
                           "application/json")
            elif path == "/api/file":
                # 取回上传过的文件（2026-10-01 加）：气泡里的图片缩略图、文件图标要用。
                # 只认文件名（os.path.basename + 字符白名单），挡掉路径穿越。
                qs2 = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                nm = os.path.basename((qs2.get("name") or [""])[0])
                if not nm or not re.fullmatch(r"[A-Za-z0-9._\u4e00-\u9fff-]{1,140}", nm):
                    self._send(400, "bad name", "text/plain; charset=utf-8")
                    return
                _CT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                       ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
                       ".svg": "image/svg+xml", ".pdf": "application/pdf",
                       ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8",
                       ".json": "application/json", ".csv": "text/csv; charset=utf-8",
                       ".mp3": "audio/mpeg", ".mp4": "video/mp4", ".zip": "application/zip"}
                fp2 = APP_DIR / "uploads" / nm
                if not fp2.is_file():
                    self._send(404, "not found", "text/plain; charset=utf-8")
                    return
                try:
                    blob2 = fp2.read_bytes()
                except Exception as e:
                    self._send(500, str(e), "text/plain; charset=utf-8")
                    return
                self.send_response(200)
                self.send_header("Content-Type",
                                 _CT.get(os.path.splitext(nm)[1].lower(),
                                         "application/octet-stream"))
                self.send_header("Content-Length", str(len(blob2)))
                self.send_header("Cache-Control", "max-age=31536000")
                if qs2.get("dl"):
                    self.send_header("Content-Disposition",
                                     "attachment; filename*=UTF-8''" + urllib.parse.quote(nm))
                self.end_headers()
                self.wfile.write(blob2)
                return
            elif path == "/api/eval":
                # 远程求值通道（服务端一半）—— 用于不碰屏幕地检查前端运行时状态。
                #   不带参数：浏览器来取待执行 JS，取走即删除（保证只执行一次）
                #   带 r=   ：浏览器回传执行结果，落到 eval_r.txt
                # 投递代码：printf '...' > ~/.termux-agent/eval_q.txt
                # 读结果　：cat ~/.termux-agent/eval_r.txt
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if "r" in qs:
                    try:
                        (APP_DIR / "eval_r.txt").write_text(str(qs["r"][0])[:4000], encoding="utf-8")
                    except Exception:
                        pass
                    self._send(200, "ok", "text/plain; charset=utf-8")
                else:
                    try:
                        src = (APP_DIR / "eval_q.txt").read_text(encoding="utf-8")
                        (APP_DIR / "eval_q.txt").unlink()
                    except Exception:
                        src = ""
                    self._send(200, src, "text/plain; charset=utf-8")
            else:
                self._send(404, "not found", "text/plain")

        def do_POST(self):
            path = urllib.parse.urlparse(self.path).path
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                body = {}

            if path == "/api/upload":
                # 前端把图片转成 base64 传来，这里落盘到 ~/.termux-agent/uploads/
                name = str(body.get("name") or "image.jpg")
                data = str(body.get("data") or "")
                if data.startswith("data:"):
                    data = data.split(",", 1)[-1]
                try:
                    blob = base64.b64decode(data, validate=False)
                except Exception:
                    self._send(400, '{"error":"bad base64"}', "application/json")
                    return
                if not blob or len(blob) > 30 * 1024 * 1024:
                    self._send(400, '{"error":"empty or too large"}', "application/json")
                    return
                # 2026-10-01：不再把认不出的扩展名一律写成 .jpg —— 现在也能发普通文件了。
                # 扩展名走格式白名单（不合规就 .bin，既不做路径也不当可执行），
                # 并把原文件名的可读主干并进文件名：气泡里显示「合影.jpg」
                # 而不是「20261001-160000-8f3a1c.jpg」（前端按这个前缀反推显示名）。
                ext = os.path.splitext(name)[1].lower()
                if not re.fullmatch(r"\.[a-z0-9]{1,8}", ext or ""):
                    ext = ".bin"
                stem = os.path.splitext(os.path.basename(str(name)))[0]
                stem = re.sub(r"[\x00-\x1f/\\:*?\"<>|]+", "_", stem).strip(" ._-")[:24]
                up = APP_DIR / "uploads"
                try:
                    up.mkdir(parents=True, exist_ok=True)
                    fn = up / (time.strftime("%Y%m%d-%H%M%S") + "-"
                               + hashlib.md5(blob[:64]).hexdigest()[:6]
                               + ("-" + stem if stem else "") + ext)
                    fn.write_bytes(blob)
                except Exception as e:
                    self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False),
                               "application/json")
                    return
                # HEIC/HEIF（华为相册默认格式）浏览器多半解不了，这里转成 JPEG 再交给模型
                note = ""
                brands = (b"heic", b"heix", b"hevc", b"hevx",
                          b"mif1", b"msf1", b"heim", b"heis")
                if len(blob) > 12 and blob[4:8] == b"ftyp" and blob[8:12] in brands:
                    exe = shutil.which("heif-convert")
                    if exe:
                        dst = fn.with_suffix(".jpg")
                        try:
                            pr = subprocess.run(
                                [exe, "-q", "82", "--quiet", str(fn), str(dst)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                timeout=240)
                            if pr.returncode == 0 and dst.exists():
                                note = "HEIC 已转 JPEG（%dKB → %dKB）" % (
                                    len(blob) // 1024, dst.stat().st_size // 1024)
                                fn = dst
                            else:
                                note = "HEIC 转码失败：" + (
                                    pr.stderr.decode("utf-8", "ignore").strip()[:120]
                                    or "未知错误")
                        except Exception as e:
                            note = "HEIC 转码失败：" + str(e)[:120]
                    else:
                        note = "未装 heif-convert，HEIC 按原样保存（可 pkg install libheif-progs）"
                size = fn.stat().st_size if fn.exists() else len(blob)
                self._send(200, json.dumps({"ok": True, "path": str(fn),
                                            "size": size, "note": note},
                                           ensure_ascii=False),
                           "application/json")
                return

            if path == "/api/stop":
                state["agent"].cancel.set()
                evt = state.get("active_stop")
                if evt:
                    evt.set()
                self._send(200, '{"ok":true}', "application/json")
                return

            if path == "/api/reset":
                state["agent"].reset()
                state["agent"].save_session()
                self._send(200, json.dumps({"ok": True, "session_id": state["agent"].session_id},
                                           ensure_ascii=False), "application/json")
                return

            if path == "/api/switch":
                sid = (body.get("id") or "").strip()
                if not sid:
                    self._send(400, '{"error":"missing id"}', "application/json")
                    return
                if state["agent"].load_session(sid):
                    self._send(200, json.dumps({"ok": True, "session_id": sid},
                                               ensure_ascii=False), "application/json")
                else:
                    self._send(404, '{"error":"not found"}', "application/json")
                return

            if path == "/api/config":
                # 保存配置（模型 / API Key / 基础地址 / 增强权限）
                changed = []
                if "model" in body and body["model"]:
                    cfg["model"] = body["model"]; changed.append("模型")
                if "base_url" in body and body["base_url"]:
                    cfg["base_url"] = body["base_url"].rstrip("/"); changed.append("接口")
                if "api_key" in body and body["api_key"]:
                    cfg["api_key"] = body["api_key"]; changed.append("Key")
                if "shell_access" in body:
                    cfg["shell_access"] = bool(body["shell_access"]); changed.append("权限")
                save_config(cfg)
                # 更新系统提示词（含环境与记忆）
                state["agent"]._rebuild_system()
                _maybe_refresh_windows(cfg)
                self._send(200, json.dumps({"ok": True, "changed": changed},
                                           ensure_ascii=False), "application/json")
                return

            if path == "/api/panel":
                changed = []
                if "name" in body and body["name"].strip():
                    cfg["assistant_name"] = body["name"].strip()[:20]; changed.append("名称")
                if "evolve_direction" in body:
                    cfg["evolve_direction"] = str(body["evolve_direction"])[:2000]; changed.append("进化方向")
                if "evolve_per_day" in body:
                    try:
                        cfg["evolve_per_day"] = max(1, min(10, int(body["evolve_per_day"])))
                        changed.append("每日项数")
                    except Exception:
                        pass
                if "evolve_enabled" in body:
                    cfg["evolve_enabled"] = bool(body["evolve_enabled"]); changed.append("进化开关")
                if "max_tool_rounds" in body:
                    try:
                        cfg["max_tool_rounds"] = max(10, min(1000, int(body["max_tool_rounds"])))
                        changed.append("单轮轮次上限")
                    except Exception:
                        pass
                if "memory" in body:
                    save_memory(str(body["memory"])[:MEMORY_MAX_CHARS]); changed.append("记忆")
                if "compress_ratio" in body:
                    try:
                        r = float(body["compress_ratio"])
                        if r > 1:            # 允许前端直接传百分数
                            r = r / 100.0
                        cfg["compress_ratio"] = round(max(0.1, min(0.95, r)), 3)
                        changed.append("压缩比例默认值")
                    except Exception:
                        pass
                save_config(cfg)
                state["agent"]._rebuild_system()
                self._send(200, json.dumps({"ok": True, "changed": changed},
                                           ensure_ascii=False), "application/json")
                return

            if path == "/api/evolve":
                if state.get("evolving"):
                    self._send(200, json.dumps({"ok": False, "msg": "正在进化中，请稍候"},
                                               ensure_ascii=False), "application/json")
                    return
                if not (cfg.get("evolve_direction") or "").strip():
                    self._send(200, json.dumps({"ok": False, "msg": "请先填写进化方向"},
                                               ensure_ascii=False), "application/json")
                    return

                def evolve_now():
                    state["evolving"] = True
                    try:
                        _evolve_log("用户手动触发一次进化")
                        result = _run_evolution_item(cfg)   # 计数在函数内部完成
                        _evolve_log(f"手动进化完成：{result[:120]}")
                        try:
                            log_change("手动进化", result)
                        except Exception:
                            pass
                    except Exception as e:
                        _evolve_log(f"手动进化出错：{type(e).__name__}: {e}")
                    finally:
                        state["evolving"] = False

                threading.Thread(target=evolve_now, daemon=True).start()
                self._send(200, json.dumps({"ok": True, "msg": "已开始进化，完成后可在面板查看"},
                                           ensure_ascii=False), "application/json")
                return

            if path == "/api/compress":
                try:
                    did = state["agent"]._maybe_compress(force=True)
                    msg = "已压缩较早的对话" if did else "对话还很短，无需压缩"
                except Exception as e:
                    msg = f"压缩失败：{type(e).__name__}: {e}"
                state["agent"].save_session()
                self._send(200, json.dumps({"ok": True, "msg": msg}, ensure_ascii=False),
                           "application/json")
                return

            if path != "/api/chat":
                self._send(404, "not found", "text/plain")
                return

            msg = (body.get("message") or "").strip()
            if not msg:
                self._send(400, '{"error":"empty"}', "application/json")
                return

            import queue as _queue
            q = _queue.Queue()
            stop_evt = threading.Event()
            state["active_stop"] = stop_evt
            agent = state["agent"]

            # 在独立线程里跑 agent，主线程只负责把事件流写给浏览器。
            # 这样「停止」能立刻掐断连接，即使 agent 正阻塞在某个工具调用里。
            # 每个回合用独立的取消标志，避免旧回合的取消状态污染新回合。
            def worker():
                state["agent_busy"] = True
                try:
                    agent.run_turn(msg, cancel=stop_evt)
                except Exception as e:
                    q.put(("error", f"{type(e).__name__}: {e}"))
                finally:
                    try:
                        agent.save_session()
                    except Exception:
                        pass
                    state["agent_busy"] = False
                    q.put(("done", ""))

            agent.emit = lambda k, v: q.put((k, v))
            threading.Thread(target=worker, daemon=True).start()

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()

            def write_obj(obj):
                self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False)
                                  + "\n\n").encode("utf-8"))
                self.wfile.flush()

            try:
                while True:
                    try:
                        item = q.get(timeout=0.25)
                    except _queue.Empty:
                        if stop_evt.is_set():
                            break
                        continue
                    k, v = item
                    if k == "done":
                        _schedule_distill(state)
                        try:
                            write_obj({"k": "done", "v": ""})
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            pass
                        break
                    try:
                        write_obj({"k": k, "v": v})
                    except (BrokenPipeError, ConnectionResetError, OSError):
                        agent.cancel.set()
                        break
            finally:
                if state.get("active_stop") is stop_evt:
                    state.pop("active_stop", None)

    try:
        srv = ThreadingHTTPServer((host, port), Handler)
    except OSError as e:
        print(red(f"启动失败：{e}"))
        return 1
    # 重启不丢任务：信号先落盘 + 从落盘对话推断上次是否被打断（见文件上方注释）
    install_exit_hooks()
    _why = write_resume_note_if_interrupted()
    if _why:
        print(dim("  上次好像被打断了：%s（已写接续标记）" % _why))
    _drift = check_unlogged_selfupdate()
    if _drift:
        print(dim("  " + _drift))
    srv.daemon_threads = True

    # 后台拉一次官方模型窗口（写进 config 缓存），不阻塞启动
    threading.Thread(target=lambda: refresh_model_windows(cfg, force=True),
                     daemon=True).start()

    if foreground:
        print(cyan(f"\n  Sidekick →  {url}"))
        print(dim(f"  模型 {cfg['model']}    工作目录 {cfg['workdir']}"))
        print(dim("  按 Ctrl-C 停止\n"))
        if not no_open:
            _open_url(url)

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print(dim("\n  已停止"))
    finally:
        srv.server_close()
        try:
            if WEB_PID.exists() and WEB_PID.read_text().strip() == str(os.getpid()):
                WEB_PID.unlink()
        except Exception:
            pass
    return 0


# ---------------------------------------------------------------- 命令实现

BANNER = r"""
  _____                    _   _         _                    _
 |_   _|__ _ _ _ _  _ _ _ | | | |_  _ __| |_  _ _  ___ __ _ __| |
   | |/ -_) '_| ' \| ' \ || |_| | || / _` | || | ' \/ -_) _` / _` |
   |_|\___|_| |_||_|_||_\_,_|\___/ \_,_\__,_|\_,_|_||_\___\__,_\__|
"""


def print_banner(cfg: dict) -> None:
    print(cyan(BANNER))
    print(f"  Termux Agent v{VERSION}  ·  在设备本地运行的 AI agent")
    print(f"  {dim('模型')}   {bold(cfg['model'])}")
    print(f"  {dim('工作目录')} {cfg['workdir']}")
    print(f"  {dim('环境')}   {'Android / Termux' if is_termux() else sys.platform}"
          f"   {dim('·')}   工具 {len(build_tools(cfg))} 个")
    if not cfg.get("api_key"):
        print(red("  [!] 尚未配置 API Key，请先执行：agent config"))
    print(dim("  输入 /help 查看命令，/quit 退出\n"))


HELP_TEXT = f"""
{bold('可用命令')}
  /help            显示本帮助
  /new             开启新对话（丢弃当前上下文）
  /model [名称]     查看或切换模型（如 /model deepseek-v4-pro）
  /workdir [路径]   查看或切换工作目录
  /tools           列出工具及其说明
  /save            手动保存当前会话
  /cost            显示本次会话累计 token 用量
  /quit 或 Ctrl-D   退出

{bold('使用技巧')}
  · 直接说人话就行，它会自己决定用哪些工具去了解环境、执行操作。
  · 任务越具体越好：写明路径、目标、验收标准，比"帮我整理一下"有效得多。
  · 平板资源有限，别让它跑高内存任务；大输出会自动分页与截断。
  · 危险命令（rm -rf、往 /dev 写、curl | sh 等）默认每次都要你确认。
"""


def cmd_chat(cfg: dict, args) -> int:
    approver = Approver(cfg, assume_yes=args.yes, interactive=sys.stdin.isatty())
    agent = Agent(cfg, approver, show_reasoning=not args.no_reasoning, verbose=args.verbose)
    if args.continue_:
        agent.load_session()

    if args.prompt:
        out = agent.run_turn(" ".join(args.prompt))
        agent.save_session()
        return 0

    if not cfg.get("api_key"):
        print(red("未配置 API Key。请执行： agent config"))
        return 2
    if not sys.stdin.isatty():
        print(red("当前不是交互终端。单次任务请用： agent run \"你的任务\""))
        return 2

    print_banner(cfg)
    readline_setup()
    while True:
        try:
            line = input(bold(cyan("你 > "))).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.startswith("/"):
            if handle_slash(line, cfg, agent):
                continue
            break
        try:
            agent.run_turn(line)
        except KeyboardInterrupt:
            print(yellow("\n[!] 已中断"))
        print()
    agent.save_session()
    print(dim("会话已保存。下次用 agent -c 续聊。"))
    return 0


def handle_slash(line: str, cfg: dict, agent: "Agent") -> bool:
    """返回 True 表示继续主循环。"""
    parts = line.split(maxsplit=1)
    cmd = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    if cmd in ("/quit", "/exit", "/q"):
        return False
    if cmd == "/help":
        print(HELP_TEXT)
    elif cmd == "/new":
        agent.reset()
    elif cmd == "/model":
        if rest:
            cfg["model"] = rest
            save_config(cfg)
            print(green(f"  · 模型已切换为 {rest}"))
        else:
            print(f"  当前模型：{bold(cfg['model'])}")
    elif cmd == "/workdir":
        if rest:
            p = Path(os.path.expanduser(rest))
            if p.is_dir():
                cfg["workdir"] = str(p.resolve())
                save_config(cfg)
                print(green(f"  · 工作目录已切换到 {cfg['workdir']}"))
            else:
                print(red(f"  目录不存在：{p}"))
        else:
            print(f"  当前工作目录：{cfg['workdir']}")
    elif cmd == "/tools":
        for t in build_tools(cfg):
            fn = t["function"]
            print(f"  {bold(fn['name']):<12} {dim(fn['description'][:90])}")
    elif cmd == "/save":
        agent.save_session()
        print(green("  · 已保存"))
    elif cmd == "/cost":
        print(f"  累计 输入 {agent.total_prompt_tokens} tokens / "
              f"输出 {agent.total_completion_tokens} tokens")
    else:
        print(yellow(f"  未知命令 {cmd}，输入 /help 查看"))
    return True


def readline_setup() -> None:
    try:
        import readline  # noqa: F401  仅用于启用行编辑与历史
        APP_DIR.mkdir(parents=True, exist_ok=True)
        if HISTORY_PATH.exists():
            readline.read_history_file(str(HISTORY_PATH))
        import atexit
        atexit.register(lambda: readline.write_history_file(str(HISTORY_PATH)))
    except Exception:
        pass


def cmd_run(cfg: dict, args) -> int:
    approver = Approver(cfg, assume_yes=args.yes, interactive=sys.stdin.isatty())
    agent = Agent(cfg, approver, show_reasoning=not args.no_reasoning, verbose=args.verbose)
    if args.continue_:
        agent.load_session()
    task = " ".join(args.task)
    if not task.strip():
        print(red("用法： agent run \"任务描述\""))
        return 2
    if not cfg.get("api_key"):
        print(red("未配置 API Key。请执行： agent config"))
        return 2
    print(dim(f"任务：{task}\n"))
    agent.run_turn(task)
    agent.save_session()
    return 0


def cmd_config(cfg: dict, args) -> int:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    print(bold("配置 Termux Agent"))
    print(dim(f"配置文件：{CONFIG_PATH}\n"))

    cur = cfg.get("api_key") or ""
    shown = (cur[:7] + "..." + cur[-4:]) if len(cur) > 12 else ("(未设置)" if not cur else cur)
    print(f"  当前 API Key：{shown}")
    entered = getpass.getpass("  新的 API Key（直接回车保持现状）: ").strip()
    if entered:
        cfg["api_key"] = entered

    print(f"\n  当前模型：{cfg['model']}")
    model = input("  模型名（回车保持现状，推荐 deepseek-flash）: ").strip()
    if model:
        cfg["model"] = model

    print(f"\n  当前工作目录：{cfg['workdir']}")
    wd = input("  工作目录（回车保持现状）: ").strip()
    if wd:
        p = Path(os.path.expanduser(wd))
        if p.is_dir():
            cfg["workdir"] = str(p.resolve())
        else:
            print(yellow(f"  [!] 目录不存在，保持原值"))

    cur_auto = cfg.get("auto_approve", True)
    print(f"\n  命令是否需要逐条确认：", end="")
    print(green("不需要（全部放行）") if cur_auto else yellow("需要（每条都问）"))
    print(dim("  默认不询问 —— 设备是你自己的。格式化、直写块设备、删根目录这类"))
    print(dim("  不可逆操作无论此设置如何都会被自动拦下。"))
    ans = input("  改成需要逐条确认吗？[y/N]: ").strip().lower()
    if ans:
        cfg["auto_approve"] = (ans != "y")

    save_config(cfg)
    print(green(f"\n已保存到 {CONFIG_PATH}"))
    return 0


BOOT_DIR = Path(os.environ.get("HOME", str(Path.home()))) / ".termux" / "boot"
BOOT_SCRIPT = BOOT_DIR / "start-agent.sh"

BOOT_SH = r"""#!/data/data/com.termux/files/usr/bin/bash
# 开机自动拉起 Termux Agent（由 agent autostart 生成）
# 依赖：安装 Termux:Boot 应用后，本目录下的脚本会在开机时自动执行。
sleep 8                                   # 等系统和网络就绪

# 防休眠（需要 Termux:API；没装则静默无效，不影响启动）
if [ -x "$PREFIX/bin/termux-wake-lock" ]; then
  "$PREFIX/bin/termux-wake-lock" >/dev/null 2>&1
fi

cd "$HOME" || exit 1
# 已在运行就不重复启动
if curl -sf -o /dev/null --max-time 3 http://127.0.0.1:8765/api/state; then
  exit 0
fi
exec "$PREFIX/bin/python3" "$HOME/.termux-agent/agent.py" web --no-open
"""


def cmd_adbpair(cfg: dict, args) -> int:
    """与平板自己的「无线调试」配对 —— 让 shell 权限不再依赖电脑。"""
    adb = shutil.which("adb")
    if not adb:
        print(red("  没找到 adb，请先： pkg install android-tools"))
        return 1
    port = getattr(args, "port", None)
    code = getattr(args, "code", None)
    if not port or not code:
        print(bold("与平板自己的无线调试配对（不需要电脑）"))
        print("  1) 打开：设置 → 系统 → 开发者选项 → 无线调试")
        print("  2) 点进去，选「使用配对码配对设备」，屏幕上会显示：")
        print("       · 一个 6 位配对码")
        print("       · 一个「配对端口」（形如 37xxx）")
        print("  3) 回到 Termux 执行：")
        print(green("       agent adbpair <配对端口> <配对码>"))
        print("     例如： agent adbpair 37123 456789")
        print()
        print(dim("  配对成功后，agent 会自动发现并保持连接（看门狗线程每 3 分钟续一次），"))
        print(dim("  以后拔掉电脑也能用 sysshell 的 shell 权限。"))
        return 0
    try:
        r = subprocess.run([adb, "pair", f"127.0.0.1:{port}", str(code)],
                           capture_output=True, text=True, errors="replace", timeout=60)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        print(out or "(无输出)")
        if "successfully paired" in out.lower():
            print(green("\n配对成功！"))
            serial = _adb_ensure(force=True)
            if serial:
                print(green(f"已连上：{serial}"))
                rr = subprocess.run([adb, "-s", serial, "shell", "id"],
                                    capture_output=True, text=True, errors="replace", timeout=20)
                print(dim((rr.stdout or "").strip()[:200]))
            else:
                print(yellow("配对成功但暂时没连上，稍后 agent 会自动重试。"))
        else:
            print(red("配对失败，请确认端口和配对码（配对码每次都会变）。"))
    except Exception as e:
        print(red(f"  配对出错：{e}"))
        return 1
    return 0


def cmd_autostart(cfg: dict, args) -> int:
    """配置开机自启（写 Termux:Boot 脚本）。"""
    print(bold("开机自启配置"))
    try:
        BOOT_DIR.mkdir(parents=True, exist_ok=True)
        BOOT_SCRIPT.write_text(BOOT_SH, encoding="utf-8")
        os.chmod(BOOT_SCRIPT, 0o700)
    except Exception as e:
        print(red(f"  写入失败：{e}"))
        return 1
    print(green(f"  已写入脚本：{BOOT_SCRIPT}"))

    # 检查 Termux:Boot 是否安装（有的话真的能自启；没有则只差这一步）
    has_boot = False
    try:
        r = subprocess.run(["pm", "list", "packages", "com.termux.boot"],
                           capture_output=True, text=True, timeout=15)
        has_boot = "com.termux.boot" in (r.stdout or "")
    except Exception:
        # Termux 里没有 pm 命令，退回探测应用私有目录
        has_boot = Path("/data/data/com.termux.boot").exists()
    if has_boot:
        print(green("  Termux:Boot 已安装 → 下次开机就会自动启动"))
    else:
        print(yellow("  [注意] 还没装 Termux:Boot，脚本暂时不会被执行"))
        print("     装好后不用改任何配置，开机即自动启动。")
        print(dim("     注意：Termux:Boot 的签名必须与主 Termux 一致"
                  "（同源安装），否则无法生效。"))

    print(bold("\n让平板彻底脱离电脑还需要这两步（都是系统设置，一次性）"))
    print("  1) 设置 → 应用 → 应用启动管理 → Termux → 关闭「自动管理」，")
    print("     手动管理里三项全开（自启动 / 关联启动 / 后台活动）")
    print("  2) 最近任务里给 Termux 加锁（下拉卡片 → 点锁图标）")
    print(bold("\n可选：在平板上获得 shell 权限（不需要电脑）"))
    print("  设置 → 系统 → 开发者选项 → 打开「无线调试」")
    print("  之后 agent 会自动发现并连上它，sysshell 工具即可用（无需 adb tcpip）")
    return 0


def cmd_restart(cfg: dict, args) -> int:
    """一步重启：停掉服务与看门狗，再重新拉起来。

    改完代码想立刻生效、或者界面状态卡住想重置时用，不用先 `agent web --stop`。
    """
    print(bold("重启 Agent 服务"))
    _stop_server()
    time.sleep(2)
    print(dim("  正在重新启动…"))
    return cmd_web(cfg, args)


def cmd_selfcheck(cfg: dict, args) -> int:
    """自我体检：源码状态 / 看门狗 / 服务健康 / 可用备份。"""
    print(bold("自我体检"))
    src = Path(__file__).resolve()
    try:
        code = src.read_text(encoding="utf-8")
        size = len(code)
    except Exception as e:
        code, size = "", 0
        print(red(f"  读不到源码：{e}"))
    print(f"  {'源码':<10} {src}")
    print(f"  {'体积':<10} {size/1024:.1f} KB")
    try:
        r = subprocess.run([sys.executable, "-m", "py_compile", str(src)],
                           capture_output=True, timeout=60)
        print(f"  {'语法':<10} " + (green("通过") if r.returncode == 0 else red("有错误")))
    except Exception as e:
        print(f"  {'语法':<10} 检查失败：{e}")

    # 看门狗
    sup = "未运行"
    try:
        if WEB_PID.exists():
            pid = int(WEB_PID.read_text().strip())
            os.kill(pid, 0)
            sup = f"运行中（pid {pid}）"
    except Exception:
        sup = "未运行"
    print(f"  {'看门狗':<10} {sup}")
    print(f"  {'脚本':<10} {SUPERVISOR} " +
          (green("(已生成)") if SUPERVISOR.exists() else yellow("(未生成)")))

    # 服务健康
    port = int(cfg.get("web_port") or 8765)
    alive = _server_alive(port)
    print(f"  {'服务':<10} " + (green(f"健康 → http://127.0.0.1:{port}") if alive
                                else red("无响应")))
    if alive:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=5) as r:
                st = json.loads(r.read().decode("utf-8", "ignore"))
            print(f"  {'模型':<10} {st.get('model')}")
            print(f"  {'会话':<10} {st.get('session_id')}")
            print(f"  {'记忆':<10} {'已积累' if st.get('memory') else '暂无'}")
        except Exception:
            pass

    # 备份
    if VERSIONS_DIR.exists():
        files = sorted(VERSIONS_DIR.glob("*.py"))
        print(f"  {'备份':<10} {len(files)} 个" +
              (f"（回滚目标 last-good.py {'存在' if LAST_GOOD.exists() else '缺失'}）"
               if files else ""))
    else:
        print(f"  {'备份':<10} 暂无（首次自我更新时自动创建）")

    # 看门狗日志尾部
    slog = APP_DIR / "supervisor.log"
    if slog.exists():
        print(bold("\n看门狗日志（最后 8 行）"))
        try:
            lines = slog.read_text(encoding="utf-8", errors="ignore").splitlines()[-8:]
            for l in lines:
                print("  " + dim(l))
        except Exception:
            pass

    print(bold("\n自我更新能力"))
    print("  可用工具：" + (green("selfupdate（改自己）") ) +
          "  ·  流程：备份 → 改 → 语法检查 → 失败立即还原 → 成功自动重启")
    print("  看门狗保障：服务挂掉自动拉起；连续启动失败自动回滚到上一个可用版本")
    return 0


def cmd_doctor(cfg: dict, args) -> int:
    print(bold("环境自检"))
    rows = [
        ("Termux 环境", "是（Android）" if is_termux() else f"否（{sys.platform}）"),
        ("Python", f"{sys.version.split()[0]}  ({sys.executable})"),
        ("shell", termux_shell()),
        ("工作目录", cfg["workdir"] + ("" if os.path.isdir(cfg["workdir"]) else "  [不存在！]")),
        ("API Key", "已配置" if cfg.get("api_key") else "未配置"),
        ("模型", cfg["model"]),
        ("API 地址", cfg["base_url"]),
        ("配置文件", str(CONFIG_PATH)),
    ]
    for k, v in rows:
        print(f"  {k:<12} {v}")

    if is_termux():
        print(bold("\n安卓侧检查"))
        shared = Path.home() / "storage" / "shared"
        print(f"  {'共享存储':<12} "
              + ("已授权" if shared.exists() else "未授权 —— 需要访问相册/下载时执行 termux-setup-storage"))
        wake = shutil.which("termux-wake-lock")
        print(f"  {'wake-lock':<12} "
              + ("可用（长时间运行可先执行 termux-wake-lock 防止被系统冻结）" if wake else "不可用（pkg install termux-api）"))
        print(dim("\n  提示：安卓 12+ 的 phantom process killer 会杀掉后台子进程。"
                  "\n  若 agent 跑到一半被杀，用电脑执行一次："
                  "\n    adb shell settings put global settings_enable_monitor_phantom_procs false"))

    if cfg.get("api_key"):
        print(bold("\n连通性测试"))
        try:
            req = urllib.request.Request(
                cfg["base_url"].rstrip("/") + "/models",
                headers={"Authorization": f"Bearer {cfg['api_key']}"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
            names = [m.get("id") for m in data.get("data", [])]
            print(green(f"  连接成功，可用模型 {len(names)} 个："))
            for n in names:
                mark = "  <- 当前" if n == cfg["model"] else ""
                print(f"    - {n}{mark}")
            if names and cfg["model"] not in names:
                print(yellow(f"  [!] 当前模型 {cfg['model']} 不在列表中，可能无法调用"))
        except urllib.error.HTTPError as e:
            print(red("  " + _friendly_http_error(e)))
        except Exception as e:
            print(red(f"  连接失败：{e}"))
    return 0


# ---------------------------------------------------------------- 入口

COMMON_FLAGS = {
    "-c": "continue_", "--continue": "continue_",
    "--yes": "yes", "-y": "yes",
    "--no-reasoning": "no_reasoning",
    "--verbose": "verbose",
}


def split_common_flags(argv: list[str]) -> tuple[dict, list[str]]:
    """把公共开关从 argv 中摘出来。

    argparse 的 subparser 会用自己的默认值覆盖主 parser 已解析的值，
    导致 `agent --yes run ...` 里的 --yes 丢失。这里自己先摘一遍，
    于是开关写在子命令前面还是后面都有效。
    `--` 之后的内容不再当作开关解析（可按需保留字面参数）。
    """
    flags = {"continue_": False, "yes": False, "no_reasoning": False, "verbose": False}
    rest, literal_mode = [], False
    for a in argv:
        if a == "--":
            literal_mode = True
            rest.append(a)
            continue
        if not literal_mode and a in COMMON_FLAGS:
            flags[COMMON_FLAGS[a]] = True
            continue
        rest.append(a)
    return flags, rest


def main() -> int:
    raw = sys.argv[1:]
    flags, raw = split_common_flags(raw)

    # 容错：直接写一句话就当作单次任务，例如 `agent 看看磁盘占用`。
    # 否则 argparse 会把 "看看磁盘占用" 当成子命令名，报 invalid choice。
    # 注意：新增子命令后必须同步这里，否则会被当成「任务描述」丢给 AI 去执行
    KNOWN_CMDS = {"chat", "run", "web", "config", "doctor", "restart",
                  "selfcheck", "autostart", "adbpair"}
    if raw and not raw[0].startswith("-") and raw[0] not in KNOWN_CMDS:
        raw = ["run"] + raw

    parser = argparse.ArgumentParser(
        prog="agent",
        description="Sidekick —— 跑在安卓平板本地的轻量 AI agent（零第三方依赖）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  agent                            打开网页界面（推荐，像 App 一样用）
  agent web                        同上，显式写法
  agent 看看磁盘占用                 直接提问，在终端里跑一次就退出
  agent -c                          续聊上次会话
  agent config                      配置 API Key 与模型
  agent doctor                      环境自检 + 连通性测试
  agent selfcheck                   自我体检（源码 / 看门狗 / 服务 / 备份）

自我修复：服务由看门狗托管，agent 可用 selfupdate 工具改自己的源码。
改坏了会自动回滚，服务不会起不来。详见 agent selfcheck。

公共开关（放在子命令前后都可以）：
  -c, --continue   恢复上次会话
      --no-reasoning  不显示思维链进度
      --verbose       完整打印模型思维链

默认不再询问是否允许执行命令（设备是你自己的）。只有格式化、直写块设备、
删除根目录这类不可逆操作会被自动拦下。
""")
    parser.add_argument("-v", "--version", action="version", version=f"Termux Agent {VERSION}")
    sub = parser.add_subparsers(dest="cmd")
    p_run = sub.add_parser("run", help="单次执行任务")
    p_run.add_argument("task", nargs="+", help="任务描述")
    p_web = sub.add_parser("web", help="启动网页界面（推荐）")
    p_web.add_argument("--port", type=int, default=None, help="端口，默认 8765")
    p_web.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    p_web.add_argument("--fg", action="store_true", help="留在前台运行（Ctrl-C 停止）")
    p_web.add_argument("--stop", action="store_true", help="停止后台服务")
    p_web.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    sub.add_parser("chat", help="在终端里对话（不推荐，界面简陋）")
    sub.add_parser("config", help="配置 API Key、模型、工作目录")
    sub.add_parser("doctor", help="环境自检与连通性测试")
    sub.add_parser("restart", help="重启服务（一步搞定，不用先 web --stop）")
    sub.add_parser("selfcheck", help="自我体检（源码/看门狗/服务/备份）")
    sub.add_parser("autostart", help="配置开机自启（Termux:Boot 脚本）")
    pa = sub.add_parser("adbpair", help="与平板自己的无线调试配对（脱离电脑用 shell 权限）")
    pa.add_argument("port", nargs="?", help="无线调试的配对端口")
    pa.add_argument("code", nargs="?", help="6 位配对码")

    args = parser.parse_args(raw)
    for k, v in flags.items():
        setattr(args, k, v)
    cfg = load_config()
    # 界面语言：启动时定一次，之后由设置页保存时改（见 save_config 处理）
    globals()["UI_LANG"] = detect_ui_lang(cfg)

    if args.cmd == "config":
        return cmd_config(cfg, args)
    if args.cmd == "doctor":
        return cmd_doctor(cfg, args)
    if args.cmd == "restart":
        return cmd_restart(cfg, args)
    if args.cmd == "selfcheck":
        return cmd_selfcheck(cfg, args)
    if args.cmd == "autostart":
        return cmd_autostart(cfg, args)
    if args.cmd == "adbpair":
        return cmd_adbpair(cfg, args)
    if args.cmd == "run":
        return cmd_run(cfg, args)
    if args.cmd == "chat":
        args.prompt = None
        return cmd_chat(cfg, args)

    # 不带参数 → 直接开网页界面（这是推荐用法）
    return cmd_web(cfg, args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
