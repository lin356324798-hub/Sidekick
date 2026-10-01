#!/data/data/com.termux/files/usr/bin/python3
"""MCP 客户端 —— 跟 wps_mcp_server.py 对话。

用法：
  mcp_call.py ping                     握手测试
  mcp_call.py list                     列出所有工具
  mcp_call.py call <工具名> '<json>'    调用工具

例：
  mcp_call.py call create_document '{"filename":"周报.docx","title":"周报"}'
  mcp_call.py call write_cell '{"filename":"表.xlsx","cell":"A1","value":"武将名"}'
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SERVER = str(Path(__file__).with_name("wps_mcp_server.py"))


class MCP:
    def __init__(self):
        self.p = subprocess.Popen(
            [sys.executable, SERVER],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.buf = b""
        self.fd = self.p.stdout.fileno()

    # ---- 底层：自己维护缓冲区，避免 readline 与 read(n) 混用的错位 ----
    def _fill(self):
        chunk = os.read(self.fd, 65536)
        if not chunk:
            raise EOFError("server 关闭了连接")
        self.buf += chunk

    def _read_frame(self):
        while b"\r\n\r\n" not in self.buf:
            self._fill()
        head, rest = self.buf.split(b"\r\n\r\n", 1)
        self.buf = rest
        n = None
        for line in head.split(b"\r\n"):
            if line.lower().startswith(b"content-length:"):
                n = int(line.split(b":", 1)[1].strip())
        if n is None:
            raise ValueError("缺少 Content-Length 头: %r" % head[:80])
        while len(self.buf) < n:
            self._fill()
        body, self.buf = self.buf[:n], self.buf[n:]
        return json.loads(body.decode("utf-8"))

    def _send(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.p.stdin.write(b"Content-Length: %d\r\n\r\n" % len(data))
        self.p.stdin.write(data)
        self.p.stdin.flush()

    def handshake(self):
        self._send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "termux-mcp-client", "version": "1.0"}}})
        r = self._read_frame()
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return r

    def call(self, method, params=None, rid=2):
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        return self._read_frame()

    def close(self):
        try:
            self.p.stdin.close()
        except Exception:
            pass
        self.p.terminate()


def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return
    m = MCP()
    try:
        init = m.handshake()
        info = init.get("result", {}).get("serverInfo", {})
        if a[0] == "ping":
            print("✓ 握手成功  server=%s  协议=%s"
                  % (info, init.get("result", {}).get("protocolVersion")))
            return
        if a[0] == "list":
            r = m.call("tools/list")
            tools = r.get("result", {}).get("tools", [])
            print("共 %d 个工具：" % len(tools))
            for t in tools:
                print("  %-22s %s" % (t["name"], t["description"]))
            return
        if a[0] == "call":
            name = a[1]
            args = json.loads(a[2]) if len(a) > 2 else {}
            r = m.call("tools/call", {"name": name, "arguments": args})
            res = r.get("result", {})
            for c in res.get("content", []):
                print(c.get("text", ""))
            if res.get("isError"):
                sys.exit(1)
            return
        print(__doc__)
    finally:
        m.close()


if __name__ == "__main__":
    main()
