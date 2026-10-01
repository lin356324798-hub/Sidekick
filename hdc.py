#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HDC 无线调试客户端（Python 实现）
移植自 EriDeLee/harmony-hdc 的 ArkTS 协议实现，逐字节对应。

帧格式: 'HW' + 00 00 + ver + protectLen(2) + payloadLen(4) + protect + payload
认证:   AUTH_NONE -> AUTH_PUBLICKEY(设备弹窗) -> AUTH_SIGNATURE -> AUTH_OK
签名:   RSA-3072 / PSS / SHA512 / saltlen=64, 输出 base64

用法:
  python3 hdc.py keygen                 生成密钥对
  python3 hdc.py connect HOST:PORT      连接并认证
  python3 hdc.py exec HOST:PORT "命令"   执行一条命令
"""
import base64
import json
import os
import random
import socket
import struct
import sys
import time

APP_DIR = os.path.expanduser("~/.termux-agent")
KEY_PATH = os.path.join(APP_DIR, "hdc_key.json")

PACKET_FLAG = b"HW"
PROTOCOL_VER = 1
PAYLOAD_VCODE = 9

CMD_KERNEL_HANDSHAKE = 1
CMD_KERNEL_CHANNEL_CLOSE = 2
CMD_KERNEL_ECHO = 9
CMD_KERNEL_ECHO_RAW = 10
CMD_UNITY_EXECUTE = 1001
CMD_SHELL_INIT = 2000
CMD_SHELL_DATA = 2001

AUTH_NONE = 0
AUTH_TOKEN = 1
AUTH_SIGNATURE = 2
AUTH_PUBLICKEY = 3
AUTH_OK = 4
AUTH_FAIL = 5
AUTH_ENCRYPT = 6

HANDSHAKE_BANNER = "OHOS HDC"
DEFAULT_VERSION = "Ver: 3.0.0b7fdbc1aa8c5fefaa"


def enc_varint(v):
    out = bytearray()
    v &= 0xFFFFFFFF
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def dec_varint(data, pos):
    value = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        pos += 1
        value += (b & 0x7F) << shift
        if not (b & 0x80):
            return value, pos
        shift += 7
        if shift > 70:
            raise ValueError("varint too long")
    raise ValueError("varint truncated")


def field_varint(tag, value):
    return enc_varint((tag << 3) | 0) + enc_varint(value)


def field_bytes(tag, value):
    return enc_varint((tag << 3) | 2) + enc_varint(len(value)) + value


def field_string(tag, value):
    return field_bytes(tag, value.encode("utf-8"))


def parse_message(data):
    result = {}
    pos = 0
    while pos < len(data):
        key, pos = dec_varint(data, pos)
        tag = key >> 3
        wire = key & 7
        if wire == 0:
            v, pos = dec_varint(data, pos)
            result[tag] = v
        elif wire == 2:
            ln, pos = dec_varint(data, pos)
            result[tag] = data[pos:pos + ln]
            pos += ln
        else:
            break
    return result


def _pad16(b):
    return b if len(b) >= 16 else b + (b"\x20" * (16 - len(b)))


def tlv_append(buf, key, value):
    vb = value.encode("utf-8")
    return buf + _pad16(key.encode("utf-8")) + _pad16(str(len(vb)).encode()) + vb


def tlv_parse(data):
    result = {}
    pos = 0
    while pos + 32 <= len(data):
        key = data[pos:pos + 16].decode("utf-8", "ignore").strip()
        pos += 16
        raw_len = data[pos:pos + 16].decode("utf-8", "ignore").strip()
        pos += 16
        try:
            size = int(raw_len)
        except ValueError:
            break
        if size < 0 or pos + size > len(data):
            break
        result[key] = data[pos:pos + size]
        pos += size
    return result


def _ser_protect(channel_id, command_flag):
    return (field_varint(1, channel_id) + field_varint(2, command_flag)
            + field_varint(3, 0) + field_varint(4, PAYLOAD_VCODE))


def build_frame(payload, channel_id, command_flag):
    protect = _ser_protect(channel_id, command_flag)
    return (PACKET_FLAG + b"\x00\x00" + bytes([PROTOCOL_VER])
            + struct.pack(">H", len(protect))
            + struct.pack(">I", len(payload))
            + protect + payload)


def try_parse_frame(data):
    """返回 (frame|None, consumed)。不足一帧时 (None, 0)。"""
    if len(data) < 11:
        return None, 0
    if data[0:2] != PACKET_FLAG:
        raise ValueError("bad HDC header: %r" % data[0:2])
    protocol_ver = data[4]
    protect_size = struct.unpack(">H", data[5:7])[0]
    data_size = struct.unpack(">I", data[7:11])[0]
    total = 11 + protect_size + data_size
    if len(data) < total:
        return None, 0
    protect = data[11:11 + protect_size]
    payload = data[11 + protect_size:total]
    fields = parse_message(protect)
    frame = {
        "protocol_ver": protocol_ver,
        "channel_id": fields.get(1, 0),
        "command_flag": fields.get(2, 0),
        "payload": payload,
    }
    return frame, total


def serialize_handshake(auth_type, session_id, connect_key, buf, version,
                        include_version=True):
    parts = [field_string(1, HANDSHAKE_BANNER),
             field_varint(2, auth_type),
             field_varint(3, session_id),
             field_string(4, connect_key),
             field_bytes(5, buf)]
    if include_version:
        parts.append(field_string(6, version))
    return b"".join(parts)


def parse_handshake(payload):
    f = parse_message(payload)

    def _s(v):
        if isinstance(v, bytes):
            return v.decode("utf-8", "ignore")
        return str(v) if v is not None else ""

    def _n(v):
        return v if isinstance(v, int) else 0

    raw_buf = f.get(5)
    return {
        "banner": _s(f.get(1)),
        "auth_type": _n(f.get(2)),
        "session_id": _n(f.get(3)),
        "connect_key": _s(f.get(4)),
        "buf": raw_buf if isinstance(raw_buf, bytes) else b"",
        "version": _s(f.get(6)),
    }


def build_initial_buf(advertise_encrypt_tcp=False):
    features = "heartbeat,encrypt_tcp" if advertise_encrypt_tcp else "heartbeat"
    buf = b""
    buf = tlv_append(buf, "authtype", "1")
    buf = tlv_append(buf, "supportfeatures", features)
    return buf


def gen_keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    priv_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()).decode()
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv_pem, pub_pem


def load_keypair(regenerate=False):
    if os.path.exists(KEY_PATH) and not regenerate:
        with open(KEY_PATH) as f:
            d = json.load(f)
        return d["private"], d["public"]
    priv, pub = gen_keypair()
    os.makedirs(APP_DIR, exist_ok=True)
    with open(KEY_PATH, "w") as f:
        json.dump({"private": priv, "public": pub}, f, indent=2)
    try:
        os.chmod(KEY_PATH, 0o600)
    except Exception:
        pass
    return priv, pub


def sign_token(priv_pem, token):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    key = serialization.load_pem_private_key(priv_pem.encode(), password=None)
    sig = key.sign(
        token,
        padding.PSS(mgf=padding.MGF1(hashes.SHA512()), salt_length=64),
        hashes.SHA512())
    return base64.b64encode(sig).decode()


class Hdc(object):
    def __init__(self, host, port, timeout=15.0, log=None):
        self.host = host
        self.port = int(port)
        self.timeout = timeout
        self.log = log or (lambda s: None)
        self.sock = None
        self.recv_buf = b""
        self.session_id = 0
        self.next_channel = 1
        self.version = DEFAULT_VERSION

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port),
                                             timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        self.log("[tcp] 已连接 %s:%d" % (self.host, self.port))

    def send(self, payload, command_flag, channel_id=0):
        self.sock.sendall(build_frame(payload, channel_id, command_flag))

    def _fill(self, deadline):
        remaining = deadline - time.time()
        if remaining <= 0:
            raise TimeoutError("等待响应超时")
        self.sock.settimeout(remaining)
        chunk = self.sock.recv(65536)
        if not chunk:
            raise ConnectionError("连接已被对端关闭")
        self.recv_buf += chunk

    def recv_frame(self, timeout=None):
        timeout = self.timeout if timeout is None else timeout
        deadline = time.time() + timeout
        while True:
            frame, consumed = try_parse_frame(self.recv_buf)
            if frame is not None:
                self.recv_buf = self.recv_buf[consumed:]
                return frame
            self._fill(deadline)

    def authenticate(self, host_name=None, auth_wait=90.0):
        """完整握手 + 认证。设备会弹窗，需要你在手机上点「允许」。"""
        self.session_id = random.randint(1, 0xFFFFFFFE)
        connect_key = "%s:%d" % (self.host, self.port)
        host_name = host_name or "termux-hdc"
        priv_pem, pub_pem = load_keypair()

        init_buf = build_initial_buf(False)
        self.send(serialize_handshake(AUTH_NONE, self.session_id, connect_key,
                                      init_buf, self.version),
                  CMD_KERNEL_HANDSHAKE)
        self.log("[hs] 已发初始握手 sid=%d" % self.session_id)

        frame = self.recv_frame()
        hs = parse_handshake(frame["payload"])
        version = hs["version"] or self.version
        auth_type = hs["auth_type"]
        self.log("[hs] 收到#1 authType=%d version=%s" % (auth_type, hs["version"]))

        if auth_type == AUTH_PUBLICKEY:
            info = host_name.encode("utf-8") + b"\x0c" + pub_pem.encode("utf-8")
            self.send(serialize_handshake(AUTH_PUBLICKEY, self.session_id, "",
                                          info, version), CMD_KERNEL_HANDSHAKE)
            self.log("[hs] 已推公钥 —— 请在手机上点「允许」授权弹窗")
            frame = self.recv_frame(timeout=auth_wait)
            hs = parse_handshake(frame["payload"])
            auth_type = hs["auth_type"]
            if hs["version"]:
                version = hs["version"]
            self.log("[hs] 收到#2 authType=%d" % auth_type)

        if auth_type == AUTH_SIGNATURE:
            sig_b64 = sign_token(priv_pem, hs["buf"])
            self.send(serialize_handshake(AUTH_SIGNATURE, self.session_id, "",
                                          sig_b64.encode("utf-8"), version),
                      CMD_KERNEL_HANDSHAKE)
            self.log("[hs] 已发签名（tokenLen=%d）" % len(hs["buf"]))
            frame = self.recv_frame()
            hs = parse_handshake(frame["payload"])
            auth_type = hs["auth_type"]
            self.log("[hs] 收到#3 authType=%d" % auth_type)

        if auth_type == AUTH_ENCRYPT:
            return False, "设备要求 TLS-PSK 加密信道（本实现暂不支持）"
        if auth_type == AUTH_OK:
            tlv = tlv_parse(hs["buf"])
            status = tlv.get("daemonauthstatus", b"").decode("utf-8", "ignore")
            if status == "DAEMON_UNAUTH":
                return False, "设备未授权该公钥（请在手机上点允许后重试）"
            return True, "认证成功（sid=%d）" % self.session_id
        return False, "握手被拒绝 authType=%d buf=%r" % (auth_type, hs["buf"][:80])

    def execute(self, command, timeout=30.0):
        """执行一条命令（CMD_UNITY_EXECUTE 通道），返回输出文本。"""
        ch = self.next_channel
        self.next_channel += 1
        self.send(command.encode("utf-8"), CMD_UNITY_EXECUTE, ch)
        self.log("[exec] ch=%d cmd=%s" % (ch, command))
        out = b""
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                raise TimeoutError("命令执行超时")
            frame = self.recv_frame(timeout=left)
            if frame["channel_id"] != ch:
                continue
            cf = frame["command_flag"]
            if cf in (CMD_KERNEL_ECHO_RAW, CMD_KERNEL_ECHO):
                out += frame["payload"]
            elif cf == CMD_KERNEL_CHANNEL_CLOSE:
                break
        return out.decode("utf-8", "replace")

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    cmd = args[0]

    def log(s):
        print(s, file=sys.stderr)

    if cmd == "keygen":
        import hashlib
        priv, pub = load_keypair(regenerate=True)
        print("✅ 已生成 RSA-3072 密钥对: %s" % KEY_PATH)
        print("   公钥 SHA256 前 32 位: %s" % hashlib.sha256(pub.encode()).hexdigest()[:32])
        return 0

    if cmd in ("connect", "exec"):
        if len(args) < 2:
            print("用法: hdc.py %s HOST:PORT [\"命令\"]" % cmd)
            return 1
        target = args[1]
        if ":" in target:
            host, port = target.rsplit(":", 1)
        else:
            host, port = target, 8888
        try:
            h = Hdc(host, int(port), log=log)
            h.connect()
        except Exception as e:
            print("❌ 连接失败: %s" % e)
            return 2
        try:
            ok, msg = h.authenticate()
        except Exception as e:
            print("❌ 认证异常: %s" % e)
            h.close()
            return 3
        print(("✅ " if ok else "❌ ") + msg)
        if not ok:
            h.close()
            return 3
        if cmd == "exec":
            if len(args) < 3:
                print("用法: hdc.py exec HOST:PORT \"命令\"")
                h.close()
                return 1
            try:
                out = h.execute(args[2])
                print(out)
            except Exception as e:
                print("❌ 执行失败: %s" % e)
                h.close()
                return 4
        h.close()
        return 0

    print("未知命令: %s" % cmd)
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
