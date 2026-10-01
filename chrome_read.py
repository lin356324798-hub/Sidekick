#!/usr/bin/env python3
"""读 Chrome 数据 —— 走 adb forward + DevTools 协议，全程后台，不用屏幕识别。

原理：
    adb forward tcp:9222 localabstract:chrome_devtools_remote
        ↓
    http://127.0.0.1:9222/json/list        列标签页（HTTP 就行）
    ws://127.0.0.1:9222/devtools/page/<id>  读页面内容（要 WebSocket）

能读到：标签页列表、网址、标题、页面文本、Cookie、localStorage、执行任意 JS
读不到：书签/历史/密码的本地数据库（那些在应用私有目录，无 root 拿不到）

用法：
    python3 chrome_read.py tabs              列出所有标签页
    python3 chrome_read.py text [index]      读第 N 个标签页的可见文本（默认 0）
    python3 chrome_read.py js  "表达式"       在页面上执行 JS
    python3 chrome_read.py cookies           读当前页面的 cookie
"""
import base64
import json
import os
import socket
import struct
import sys
import urllib.request

SERIAL = "127.0.0.1:5555"          # 本机 adbd（无线调试）
ADB_SERVER = ("127.0.0.1", 5037)
PORT = 9222


def _adb(payload: bytes, timeout=6) -> bytes:
    """和 adb server 说话。返回状态四个字节（OKAY / FAIL）。"""
    s = socket.create_connection(ADB_SERVER, timeout=timeout)
    try:
        s.sendall(b"%04x" % len(payload) + payload)
        return s.recv(4)
    finally:
        s.close()


def ensure_forward(port: int = PORT) -> bool:
    """把 Chrome 的调试 socket 接到本地 tcp 端口；已存在也算成功。"""
    payload = (f"host-serial:{SERIAL}:forward:tcp:{port}"
               f";localabstract:chrome_devtools_remote").encode()
    try:
        st = _adb(payload)
    except Exception:
        return False
    # 已存在时 adb 会回 FAIL + "cannot bind"，但通道其实可用，所以再探一次
    if st != b"OKAY":
        return _probe(port)
    return True


def _probe(port: int = PORT) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=4):
            return True
    except Exception:
        return False


def targets(port: int = PORT) -> list:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=6) as r:
        return json.load(r)


# ---------------------------------------------------------------- WebSocket

def _ws_open(ws_url: str, timeout=10):
    rest = ws_url[5:]                       # 去掉 'ws://'
    hostport, _, path = rest.partition("/")
    host, _, port = hostport.partition(":")
    s = socket.create_connection((host, int(port or 80)), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET /{path} HTTP/1.1\r\n"
               f"Host: {hostport}\r\n"
               f"Upgrade: websocket\r\n"
               f"Connection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\n"
               f"Sec-WebSocket-Version: 13\r\n\r\n").encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            raise RuntimeError("WebSocket 握手失败（连接被关闭）")
        buf += chunk
    if b"101" not in buf.split(b"\r\n")[0]:
        raise RuntimeError("WebSocket 握手被拒：" + buf.split(b"\r\n")[0].decode("utf-8", "replace"))
    return s


def _ws_send(s, text: str) -> None:
    data = text.encode()
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    head = bytearray([0x81])
    n = len(data)
    if n < 126:
        head.append(0x80 | n)
    elif n < 65536:
        head.append(0x80 | 126)
        head += struct.pack(">H", n)
    else:
        head.append(0x80 | 127)
        head += struct.pack(">Q", n)
    s.sendall(bytes(head) + mask + masked)


def _ws_recv(s) -> str:
    def rd(n):
        b = b""
        while len(b) < n:
            c = s.recv(n - len(b))
            if not c:
                raise RuntimeError("连接中断")
            b += c
        return b

    hdr = rd(2)
    length = hdr[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", rd(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", rd(8))[0]
    if hdr[1] & 0x80:                        # 服务端不该带掩码，带了解掉
        mask = rd(4)
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(rd(length)))
    else:
        data = rd(length)
    return data.decode("utf-8", "replace")


def cdp_call(ws_url: str, method: str, params: dict = None, timeout: int = 15):
    """发一条 CDP 命令，等它的返回。"""
    s = _ws_open(ws_url, timeout=timeout)
    try:
        s.settimeout(timeout)
        _ws_send(s, json.dumps({"id": 1, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(_ws_recv(s))
            if msg.get("id") == 1:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result")
    finally:
        s.close()


# ---------------------------------------------------------------- 对外命令

def cmd_tabs():
    for i, t in enumerate(targets()):
        print(f"[{i}] {t.get('type'):8} {t.get('title','')}")
        print(f"     {t.get('url','')}")


def _page(index: int = 0) -> dict:
    pages = [t for t in targets() if t.get("type") == "page"]
    if not pages:
        raise SystemExit("没有可读的页面")
    return pages[int(index)]


def cmd_text(index: int = 0):
    p = _page(index)
    r = cdp_call(p["webSocketDebuggerUrl"], "Runtime.evaluate",
                 {"expression": "document.body ? document.body.innerText : ''",
                  "returnByValue": True})
    print(f"--- {p.get('title','')} ---")
    print((r or {}).get("result", {}).get("value", ""))


def cmd_js(expr: str, index: int = 0):
    p = _page(index)
    r = cdp_call(p["webSocketDebuggerUrl"], "Runtime.evaluate",
                 {"expression": expr, "returnByValue": True})
    print(json.dumps((r or {}).get("result", {}), ensure_ascii=False, indent=2))


def cmd_cookies(index: int = 0):
    p = _page(index)
    r = cdp_call(p["webSocketDebuggerUrl"], "Network.getCookies", {"urls": [p.get("url", "")]})
    for c in (r or {}).get("cookies", []):
        print(f"  {c.get('domain','')}  {c.get('name')}={str(c.get('value'))[:40]}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        raise SystemExit(0)
    if not ensure_forward():
        raise SystemExit("forward 失败：Chrome 的调试通道连不上")
    c = args[0]
    if c == "tabs":
        cmd_tabs()
    elif c == "text":
        cmd_text(args[1] if len(args) > 1 else 0)
    elif c == "js":
        cmd_js(args[1], args[2] if len(args) > 2 else 0)
    elif c == "cookies":
        cmd_cookies(args[1] if len(args) > 1 else 0)
    else:
        print(__doc__)
