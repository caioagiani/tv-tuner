"""LG webOS de mentira: SSAP por websocket na 3000 e o socket de teclas, com
o "Permitir?" aceito na hora (ou recusado). Websocket em stdlib, para os
testes não pedirem dependência. python3 tests/fake_lg.py (127.0.0.1:3000)."""
import base64
import hashlib
import json
import socket
import struct
import sys
import threading

KEY = "chave-da-lg"
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82")
APPS = [("netflix", "Netflix"), ("youtube.leanback.v4", "YouTube"), ("com.webos.app.hdmi1", "HDMI 1")]


def _recv_frame(conn):
    head = conn.recv(2)
    if len(head) < 2:
        return None
    op, n = head[0] & 0x0F, head[1] & 0x7F
    if n == 126:
        n = struct.unpack(">H", conn.recv(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", conn.recv(8))[0]
    mask = conn.recv(4) if head[1] & 0x80 else b"\0\0\0\0"
    data = b""
    while len(data) < n:
        data += conn.recv(n - len(data))
    data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return op, data


def _send_frame(conn, text, op=1):
    data = text.encode()
    n = len(data)
    head = bytes([0x80 | op]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n))
    conn.sendall(head + data)


class LG:
    def __init__(self, port):
        self.port = port
        self.power = "Active"
        self.volume, self.mute = 12, False
        self.app = None
        self.text = []
        self.buttons = []
        self.accept = True
        self.ime_subs, self.vol_subs = [], []
        self.opened = None

    def _notify(self, subs, payload):
        for sub in list(subs):
            try:
                _send_frame(sub[0], json.dumps({"type": "response", "id": sub[1], "payload": payload}))
            except OSError:                             # cliente já foi embora
                subs.remove(sub)

    def ime(self, open_):
        self._notify(self.ime_subs, {"currentWidget": {"focus": open_, "contentType": "text"}})

    def _vol(self):
        return {"volumeStatus": {"volume": self.volume, "muteStatus": self.mute}, "returnValue": True}

    def _push_vol(self):
        self._notify(self.vol_subs, self._vol())

    def handle(self, uri, p):
        u = uri.replace("ssap://", "")
        ok = {"returnValue": True}
        if u == "system/getSystemInfo":
            return {"modelName": "OLED55C1PSA", "returnValue": True}
        if u == "com.webos.service.tvpower/power/getPowerState":
            return {"state": self.power, "returnValue": True}
        if u == "com.webos.service.connectionmanager/getinfo":
            return {"wifiInfo": {"macAddress": "11:22:33:44:55:66"}, "returnValue": True}
        if u == "audio/getVolume":
            return self._vol()
        if u == "audio/getStatus":
            return {"mute": self.mute, "volume": self.volume, "returnValue": True}
        if u == "audio/setVolume":
            self.volume = p["volume"]; self._push_vol(); return ok
        if u == "audio/setMute":
            self.mute = p["mute"]; self._push_vol(); return ok
        if u == "com.webos.applicationManager/listLaunchPoints":
            return {"launchPoints": [{"id": i, "title": t, "icon": f"http://127.0.0.1:{self.port}/icon/{i}.png"}
                                     for i, t in APPS], "returnValue": True}
        if u == "com.webos.applicationManager/getForegroundAppInfo":
            return {"appId": self.app or "com.webos.app.home", "returnValue": True}
        if u == "system.launcher/launch":
            self.app = p["id"]; return ok
        if u == "system.launcher/open":
            self.opened = p["target"]; return ok
        if u == "com.webos.service.ime/insertText":
            self.text.append(p["text"]); return ok
        if u == "com.webos.service.ime/sendEnterKey":
            self.text.append("\n"); return ok
        if u == "com.webos.service.networkinput/getPointerInputSocket":
            return {"socketPath": f"ws://127.0.0.1:{self.port}/pointer", "returnValue": True}
        if u in ("system/turnOff",):
            self.power = "Active Standby"; return ok
        if u in ("system/turnOn",):
            self.power = "Active"; return ok
        if u.startswith("media.controls/"):
            return ok
        return None

    def serve(self, conn):
        req = b""
        while b"\r\n\r\n" not in req:
            chunk = conn.recv(4096)
            if not chunk:
                return
            req += chunk
        lines = req.decode().split("\r\n")
        path = lines[0].split()[1]
        hdr = {l.split(":", 1)[0].lower(): l.split(":", 1)[1].strip() for l in lines[1:] if ":" in l}
        if "sec-websocket-key" not in hdr:            # ícone por HTTP comum
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: %d\r\n\r\n" % len(PNG) + PNG)
            return
        acc = base64.b64encode(hashlib.sha1((hdr["sec-websocket-key"] + GUID).encode()).digest()).decode()
        conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {acc}\r\n\r\n").encode())
        registered = False
        while True:
            fr = _recv_frame(conn)
            if not fr or fr[0] == 8:
                return
            op, data = fr
            if op == 9:                                 # ping
                conn.sendall(bytes([0x8A, 0]))
                continue
            if path == "/pointer":
                self.buttons.append(data.decode())
                continue
            msg = json.loads(data)
            t, mid = msg.get("type"), msg.get("id")
            if t == "hello":
                _send_frame(conn, json.dumps({"type": "hello", "id": mid, "payload": {
                    "deviceOS": "webOS", "deviceOSReleaseVersion": "6.0.0", "pairingTypes": ["PROMPT"]}}))
            elif t == "register":
                key = msg["payload"].get("client-key")
                if key != KEY:
                    _send_frame(conn, json.dumps({"type": "response", "id": mid, "payload": {"pairingType": "PROMPT"}}))
                    if not self.accept:
                        _send_frame(conn, json.dumps({"type": "error", "id": mid, "error": "403 User denied access"}))
                        continue
                registered = True
                _send_frame(conn, json.dumps({"type": "registered", "id": mid, "payload": {"client-key": KEY}}))
            elif t in ("request", "subscribe"):
                if not registered and msg["uri"] != "ssap://system/getSystemInfo":
                    _send_frame(conn, json.dumps({"type": "error", "id": mid, "error": "401 insufficient permissions"}))
                    continue
                if t == "subscribe" and "registerRemoteKeyboard" in msg["uri"]:
                    self.ime_subs.append((conn, mid))
                    continue
                if t == "subscribe" and msg["uri"].endswith("audio/getVolume"):
                    self.vol_subs.append((conn, mid))
                out = self.handle(msg["uri"], msg.get("payload") or {})
                if out is None:
                    _send_frame(conn, json.dumps({"type": "error", "id": mid, "error": "404 no such service or method"}))
                else:
                    _send_frame(conn, json.dumps({"type": "response", "id": mid, "payload": out}))


def start(host="127.0.0.1", port=3000):
    tv = LG(port)
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(16)

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=lambda c=conn: _safe(tv.serve, c), daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()
    return tv, srv


def _safe(fn, conn):
    try:
        fn(conn)
    except Exception:
        pass
    finally:
        conn.close()


if __name__ == "__main__":
    tv, srv = start(sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1")
    print("LG falsa em ws://127.0.0.1:3000 — Ctrl+C para parar", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
