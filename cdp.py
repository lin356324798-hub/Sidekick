#!/data/data/com.termux/files/usr/bin/python3
"""CDP 客户端 —— 通过 DevTools 协议直接读写华为浏览器里的页面。

用法：
    cdp.py "JS表达式"                    读/操作第一个匹配页面
    cdp.py "JS表达式" -p 8765            只挑 url 含 8765 的页面
    cdp.py "JS表达式" -n 1               挑第 2 个匹配页面
    cdp.py -l                            列出所有可调试页面
    cdp.py --fix                         重新建立 adb forward（浏览器重启后需要）

原理：adb forward 把浏览器的 localabstract:xxx_webview_devtools_remote_PID
      映射到本机 9222 端口，再用 WebSocket 说 CDP 协议。
      不依赖页面里预先埋好的钩子，任何页面都能读。
"""
import json
import subprocess
import sys
import urllib.request

try:
    import websocket
except ImportError:
    print("缺少 websocket-client：pip install websocket-client")
    sys.exit(1)

PORT = 9222
ADB_SERIAL = "127.0.0.1:5555"


def adb(*args):
    return subprocess.run(["adb", "-s", ADB_SERIAL] + list(args),
                          capture_output=True, text=True, timeout=20)


def fix_forward(port=PORT):
    """重新建立 forward（浏览器重启、PID 变化后必须重建）。
    固定规则：huawei_webview_devtools_remote_*（华为浏览器）优先占 port，
    其余（Termux:Boot 等其他 WebView）往后排，避免顺序随机导致认错目标。"""
    out = adb("shell", "cat /proc/net/unix").stdout or ""
    socks = []
    for line in out.splitlines():
        for tag in ("huawei_webview_devtools_remote_", "webview_devtools_remote_"):
            i = line.find("@" + tag)
            if i >= 0:
                name = line[i + 1:].strip()
                if name not in socks:
                    socks.append(name)
    if not socks:
        print("没有找到 devtools socket（浏览器没开？）")
        return False
    # 华为浏览器的排最前，固定映射到 9222
    socks.sort(key=lambda s: (0 if s.startswith("huawei_") else 1, s))
    adb("forward", "--remove-all")
    for n, name in enumerate(socks):
        p = port + n
        r = adb("forward", "tcp:%d" % p, "localabstract:" + name)
        print("forward tcp:%d -> %s %s" % (p, name, (r.stdout or r.stderr or "").strip()))
    return True


def targets(port=PORT):
    with urllib.request.urlopen("http://127.0.0.1:%d/json" % port, timeout=8) as r:
        return json.load(r)


def pick(port=PORT, match=None, idx=0):
    ts = [t for t in targets(port) if t.get("type") == "page"]
    if match:
        ts = [t for t in ts if match in (t.get("url") or "")]
    if not ts:
        return None
    return ts[idx] if idx < len(ts) else ts[0]


def evaluate(expr, port=PORT, match=None, idx=0, timeout=20):
    t = pick(port, match, idx)
    if not t:
        return "__没有找到匹配的页面__"
    # suppress_origin: Chrome 132+ 的 CDP 会校验 Origin 头，带了就 403，干脆不发
    ws = websocket.create_connection(t["webSocketDebuggerUrl"], timeout=timeout,
                                     suppress_origin=True)
    try:
        ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": expr, "returnByValue": True,
                       "awaitPromise": True, "userGesture": True},
        }))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                if "error" in msg:
                    return "协议错误: " + json.dumps(msg["error"], ensure_ascii=False)[:300]
                res = msg.get("result", {})
                if "exceptionDetails" in res:
                    ex = res["exceptionDetails"]
                    return "JS异常: %s (line %s)" % (
                        (ex.get("exception") or {}).get("description", "?"),
                        ex.get("lineNumber"))
                v = res.get("result", {}).get("value")
                return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    finally:
        ws.close()


def open_url(url, port=PORT):
    """用 CDP 新建一个标签页打开 url（比 am start 可靠，不会被现有标签吞掉）。"""
    with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port, timeout=8) as r:
        v = json.load(r)
    ws = websocket.create_connection(v["webSocketDebuggerUrl"], timeout=20,
                                     suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": "Target.createTarget",
                            "params": {"url": url}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == 1:
                if "error" in m:
                    return "失败: " + json.dumps(m["error"], ensure_ascii=False)[:200]
                return "已新建标签页: " + url
    finally:
        ws.close()


def call(method, params=None, port=PORT, match=None, idx=0, timeout=20):
    """调用任意 CDP 方法（如 Input.insertText、Page.captureScreenshot）。"""
    t = pick(port, match, idx)
    if not t:
        return {"error": "找不到匹配页面"}
    ws = websocket.create_connection(t["webSocketDebuggerUrl"], timeout=timeout,
                                     suppress_origin=True)
    try:
        ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
        while True:
            m = json.loads(ws.recv())
            if m.get("id") == 1:
                return m
    finally:
        ws.close()


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return
    if a[0] == "--fix":
        fix_forward()
        return
    if a[0] == "--open":
        print(open_url(a[1]))
        return
    if a[0] == "--call":
        import argparse
        p = argparse.ArgumentParser()
        p.add_argument("method")
        p.add_argument("--params", default="{}")
        p.add_argument("-p", dest="match", default=None)
        p.add_argument("-n", dest="idx", type=int, default=0)
        o = p.parse_args(a[1:])
        r = call(o.method, json.loads(o.params), match=o.match, idx=o.idx)
        print(json.dumps(r, ensure_ascii=False))
        return
    if a[0] == "-l":
        for n, t in enumerate(targets()):
            if t.get("type") != "page":
                continue
            print("[%d] %s\n    %s" % (n, (t.get("title") or "")[:50], (t.get("url") or "")[:90]))
        return
    expr = a[0]
    match, idx = None, 0
    if "-p" in a:
        match = a[a.index("-p") + 1]
    if "-n" in a:
        idx = int(a[a.index("-n") + 1])
    print(evaluate(expr, match=match, idx=idx))


if __name__ == "__main__":
    main()
