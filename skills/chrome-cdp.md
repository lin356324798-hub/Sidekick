# Chrome 调试抓取技能 —— 用 DevTools 协议读网页数据，不碰屏幕

> 建立日期：2026-09-30
> 适用对象：系统里的 Chrome（`com.android.chrome`）
> 核心工具：`~/.termux-agent/chrome_read.py`（CDP 客户端，纯标准库，206 行）
> 前置条件：本机 adb server(127.0.0.1:5037) + adbd(5555) 在跑（平板无线调试常开即可）

---

## 一、这份文档解决什么问题

要「看网页内容」时，**不用截图、不用 uiautomator、不用点屏幕** ——
把 Chrome 的调试通道接出来，用 DevTools 协议读 DOM、跑 JS、取 Cookie、抽数据。

**用户明确要求过：不要屏幕触控、不要屏幕识别。** 这条路正好满足，
而且比看屏幕更准（拿到的是结构化数据，不是像素）。

适用场景：
- 让 Chrome 打开某网址，抓正文 / 列表 / 表格 / 价格
- 从需要登录的页面取数据（Chrome 里的登录态直接用，不需要重登）
- 验证前端改动（配合 ui-debug 技能）
- 批量抓取（列表页 + 逐条详情页）

---

## 二、原理：为什么能连上

Chrome 启动时会在系统里创建一个 **abstract unix socket**：

```
@chrome_devtools_remote            ← 系统 Chrome
@chrome_devtools_remote           ← 出境易沙箱里的 Chrome（名字前缀不同）
@webview_devtools_remote_<pid>    ← WebView
```

这个 socket **只有 adb（shell 身份）能连**，普通应用直连会 `Permission denied`
（实测过）。所以路子是：

```
1) 本机 adb server 已在跑（127.0.0.1:5037），adb 客户端在 $PREFIX/bin/adb
2) adb forward tcp:9222 localabstract:chrome_devtools_remote
3) HTTP  http://127.0.0.1:9222/json/list              → 标签页列表（够用了）
   WS    ws://127.0.0.1:9222/devtools/page/<id>       → 发 CDP 命令（读内容/跑JS）
```

**关键**：`adb forward` 不需要 adb 客户端也能做 —— 直接和 adb server 说协议就行
（`host-serial:127.0.0.1:5555:forward:tcp:9222;localabstract:chrome_devtools_remote`），
`chrome_read.py` 里就是这么实现的，不依赖 adb 二进制。

---

## 三、日常用法

```bash
# 列标签页（标题 + URL）
python3 ~/.termux-agent/chrome_read.py tabs

# 读第 0 个标签页的可见文本
python3 ~/.termux-agent/chrome_read.py text 0

# 在页面上执行 JS（返回 JSON）
python3 ~/.termux-agent/chrome_read.py js "({t: document.title, n: document.querySelectorAll('a').length})"

# 读当前页 cookie
python3 ~/.termux-agent/chrome_read.py cookies 0
```

打开网页（走 sysshell 工具，或 adb）：

```bash
am start -a android.intent.action.VIEW -d "<网址>" -p com.android.chrome
```

> 发完 intent 别急着读，**等 8~12 秒**（华为官网这类重页面加载慢）。
> 判断加载完成：`chrome_read.py tabs` 里标题从 URL 变成中文标题即为已渲染。

---

## 四、能力边界（2026-09-30 实测）

**能读**：

| 数据 | 方式 |
|---|---|
| 标签页列表（标题、URL） | `GET /json/list` |
| 页面可见文本 | `Runtime.evaluate` → `document.body.innerText` |
| 结构化数据 | `Runtime.evaluate` 跑任意 JS，`returnByValue` |
| Cookie / localStorage | `Network.getCookies` / JS |
| 网络请求 | 先 `Network.enable` |
| 页面截图 | `Page.captureScreenshot`（base64，注意这是像素，另有用途） |

**读不到**：

| 数据 | 原因 |
|---|---|
| **书签** | ① 数据在 `/data/data/com.android.chrome/` 私有目录（无 root）；② 安卓 Chrome 的书签是 **原生界面**，`chrome://bookmarks` 会跳成 `chrome-native://bookmarks/folder/0`，body 是空的，`chrome.bookmarks` API 也不存在 |
| 历史记录 | 同上（`chrome://history` 也是原生界面） |
| 密码 | 私有目录 |
| Chrome 设置项 | 私有目录 + 原生界面，改不了也读不到 |

**一句话**：**网页里的数据能读，Chrome 自己管理的本地数据读不到。**

---

## 五、踩过的坑

**1. `file://` 打不开**（安卓 11+ 的限制）
```
am start -d "file:///sdcard/x.html" -p com.android.chrome
→ Error: Activity not started, unable to resolve Intent
```
绕法：起本地 HTTP 服务（`python3 -m http.server 8080 --bind 127.0.0.1`），
Chrome 访问 `http://127.0.0.1:8080/x.html`。这也顺便让"书签页/导航页"能长期用。

**2. `chrome://` 不能用 am start 打开** → `unable to resolve Intent`。
但**可以用 CDP 的 `Target.createTarget` 创建**（要浏览器级 WebSocket，地址在
`/json/version` 的 `webSocketDebuggerUrl` 里）。用完记得 `Target.closeTarget`。

**3. 正文淹没在导航里**
华为官网这类页面的 `innerText` 前 2000 字符全是导航菜单。**过滤技巧**：
按行切分 `innerText`，丢掉长度 < 45 的行 —— 菜单项都很短，正文段落都很长。

**4. `adb forward` 已存在时返回 FAIL**
adb 回 `FAIL` + `cannot bind`，但**通道其实可用**。所以 `ensure_forward()`
在 FAIL 之后要再探一次端口（`_probe`），别直接判死。

**5. adb 协议回包格式不一致**（踩过一次）
- `host:version` → `OKAY` + 4 位十六进制长度 + 内容
- `host-serial:X:forward:...` → **只有 `OKAY`，没有长度前缀**
照搬解析会 `int(b'OKAY', 16)` 抛 ValueError。

**6. 别用 `pkill -f "关键词"` 清理自己的进程**
模式会匹配到执行这条命令的 shell 自身（老坑，见 termux-traps）。

**7. 标签页索引会变**
`text 0` 里的 0 是**当前顺序**，不是固定某个页面。稳妥做法：按 URL 关键字自己找：

```python
t = next(x for x in cr.targets() if 'alpha-antenna' in (x.get('url') or ''))
```

---

## 六、可复用片段

最小 WebSocket 客户端（Chromium CDP 用，纯标准库）：

```python
import socket, base64, os, struct, json

def ws_open(ws_url, timeout=10):
    rest = ws_url[5:]                      # 去掉 'ws://'
    hostport, _, path = rest.partition("/")
    host, _, port = hostport.partition(":")
    s = socket.create_connection((host, int(port or 80)), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(4096)
    if b"101" not in buf.split(b"\r\n")[0]:
        raise RuntimeError("握手被拒")
    return s

def ws_send(s, text):
    data = text.encode(); mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    head = bytearray([0x81]); n = len(data)
    if n < 126: head.append(0x80 | n)
    elif n < 65536: head.append(0x80 | 126); head += struct.pack(">H", n)
    else: head.append(0x80 | 127); head += struct.pack(">Q", n)
    s.sendall(bytes(head) + mask + masked)

def ws_recv(s):
    def rd(n):
        b = b""
        while len(b) < n:
            b += s.recv(n - len(b))
        return b
    hdr = rd(2); ln = hdr[1] & 0x7F
    if ln == 126: ln = struct.unpack(">H", rd(2))[0]
    elif ln == 127: ln = struct.unpack(">Q", rd(8))[0]
    return rd(ln).decode("utf-8", "replace")
```

发命令（等 id 匹配的回包）：

```python
ws_send(s, json.dumps({"id": 1, "method": "Runtime.evaluate",
                       "params": {"expression": js, "returnByValue": True}}))
while True:
    m = json.loads(ws_recv(s))
    if m.get("id") == 1:
        return m.get("result")
```

---

## 七、实战记录

**2026-09-30 · 抓华为官网新闻**

```bash
# 1. 打开新闻中心
am start -d "https://www.huawei.com/cn/news" -p com.android.chrome   # 等 10s

# 2. JS 提取列表（拿到 12 条：标题 + 链接 + 日期）
document.querySelectorAll('a') → 过滤 href 含 /news/ 且文本长度 > 8

# 3. 打开其中一条，提取正文
am start -d ".../news/2026/9/alpha-antenna-6000" -p com.android.chrome
# innerText 按行切分 → 丢弃 < 45 字符的短行 → 得到 12 段正文（2050 字符）

# 4. 存盘
Download/华为新闻-阿尔法6000天线.md
```

结果：标题、日期、链接、12 段正文（含 4 条技术问答）全部拿到，**零屏幕识别**。
