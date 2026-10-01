#!/data/data/com.termux/files/usr/bin/bash
# ============================================================
#  Sidekick 一键安装脚本（安卓 + Termux）
#  用法： bash install.sh
# ============================================================
set -e

SRC="$(cd "$(dirname "$0")" && pwd)"
DST="$HOME/.termux-agent"

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }
ok()   { printf '    \033[32mOK\033[0m  %s\n' "$1"; }
warn() { printf '    \033[33m!!\033[0m  %s\n' "$1"; }
die()  { printf '\n\033[1;31m[失败] %s\033[0m\n' "$1"; exit 1; }

# ---------- 1. 环境检查 ----------
say "1/6 检查环境"

[ -n "$TERMUX_VERSION" ] || warn "看起来不是 Termux 环境，可能装不上"

command -v python3 >/dev/null 2>&1 || die "没找到 python3，请先执行： pkg install python"

PYV=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)
ok "Python $PYV"

command -v curl >/dev/null 2>&1 && ok "curl 可用" || warn "没有 curl，服务自检会跳过（pkg install curl 可补）"

# ---------- 2. 目录 ----------
say "2/6 准备目录 $DST"

if [ -d "$DST" ]; then
  if [ -f "$DST/agent.py" ]; then
    BK="$DST.bak-$(date +%Y%m%d-%H%M%S)"
    warn "已存在旧安装，先备份到 $BK"
    cp -r "$DST" "$BK"
    ok "备份完成"
  fi
else
  mkdir -p "$DST"
fi

mkdir -p "$DST/skills" "$DST/logs" "$DST/versions" "$DST/uploads" "$DST/outputs"
ok "目录就绪"

# ---------- 3. 拷贝文件 ----------
say "3/6 安装文件"

[ -f "$SRC/agent.py" ] || die "发布包里没有 agent.py，请确认在解压后的目录里运行"

cp "$SRC/agent.py" "$DST/agent.py"
ok "agent.py"

for f in mcp_call.py wps.py wps_mcp_server.py; do
  if [ -f "$SRC/$f" ]; then cp "$SRC/$f" "$DST/$f"; ok "$f"; fi
done

n=0
if [ -d "$SRC/skills" ]; then
  for s in "$SRC"/skills/*.md; do
    [ -f "$s" ] || continue
    cp "$s" "$DST/skills/$(basename "$s")"
    n=$((n+1))
  done
fi
ok "技能文档 $n 个"

# 启动脚本
cp "$SRC/scripts/start.sh" "$DST/start.sh" 2>/dev/null && chmod +x "$DST/start.sh" && ok "start.sh"

# 语法校验
python3 -m py_compile "$DST/agent.py" 2>/dev/null || die "agent.py 语法检查没通过，安装中止"
ok "源码语法检查通过"

# ---------- 4. 初始化配置 ----------
say "4/6 初始化配置"

CFG="$DST/config.json"
if [ -f "$CFG" ]; then
  ok "已有配置，保留不动（不会覆盖你的 API Key）"
else
  cat > "$CFG" <<'JSON'
{
  "api_key": "",
  "base_url": "https://api.deepseek.com",
  "model": "deepseek-chat",
  "assistant_name": "Sidekick",
  "work_dir": "",
  "web_port": 8765,
  "auto_approve": true,
  "auto_evolve": true
}
JSON
  chmod 600 "$CFG"
  ok "已生成 config.json（待填 API Key）"
fi

# 关闭历史遗留的语音配置（如果有）
if grep -q '"tts"\|"voice"' "$CFG" 2>/dev/null; then
  warn "配置里有语音相关字段（已废弃，不影响运行）"
fi

# ---------- 5. 启动 ----------
say "5/6 启动服务"

cd "$DST" || die "进不去 $DST"
pkill -f "agent.py web --serve" >/dev/null 2>&1 || true
pkill -f "supervisor.sh" >/dev/null 2>&1 || true
sleep 1

nohup bash supervisor.sh >/dev/null 2>&1 &
ok "看门狗已拉起（会自动重启服务、出错自动回滚）"

for i in $(seq 1 15); do
  sleep 1
  if curl -sf --max-time 3 http://127.0.0.1:8765/api/state >/dev/null 2>&1; then
    ok "服务已响应（等了 ${i} 秒）"
    break
  fi
  [ "$i" -eq 15 ] && warn "服务还没起来，稍后手动执行： bash $DST/start.sh"
done

# ---------- 6. 收尾 ----------
say "6/6 完成"

cat <<TIPS

  界面地址：  http://127.0.0.1:8765/
  启动脚本：  bash $DST/start.sh
  开机自启：  python3 $DST/agent.py autostart
  环境自检：  python3 $DST/agent.py doctor

TIPS

if grep -q '"api_key": ""' "$CFG" 2>/dev/null; then
  printf '  \033[33m下一步：填 API Key —— 打开界面右上角「设置」，或执行 python3 %s/agent.py config\033[0m\n\n' "$DST"
fi
