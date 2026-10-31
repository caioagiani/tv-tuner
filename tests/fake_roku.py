"""Roku de mentira: responde o ECP na porta 8060 com o que uma Roku TV real
responde, e guarda cada tecla recebida. Serve aos testes e para mexer no
painel sem ter a TV: python3 tests/fake_roku.py (escuta em 127.0.0.1:8060)."""
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

APPS = [("12", "Netflix"), ("837", "YouTube"), ("13", "Prime Video"), ("291097", "Disney Plus"),
        ("tvinput.hdmi1", "HDMI 1")]
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82")


class Roku:
    def __init__(self):
        self.power = "PowerOn"
        self.active = None
        self.player = "close"
        self.limited = False
        self.pressed = []


def handler(tv):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, ctype="text/xml; charset=utf-8", code=200):
            body = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            p = self.path
            if p == "/query/device-info":
                return self._send(
                    '<?xml version="1.0" encoding="UTF-8" ?><device-info>'
                    "<model-name>7000X</model-name><friendly-model-name>TCL Roku TV</friendly-model-name>"
                    "<user-device-name>Sala Roku</user-device-name><software-version>12.5.0</software-version>"
                    "<network-type>wifi</network-type><wifi-mac>aa:bb:cc:dd:ee:ff</wifi-mac>"
                    f"<is-tv>true</is-tv><power-mode>{tv.power}</power-mode></device-info>")
            if p == "/query/apps":
                return self._send("<apps>" + "".join(
                    f'<app id="{i}" type="{"tvin" if i.startswith("tvinput") else "appl"}" version="1.0">{n}</app>'
                    for i, n in APPS) + "</apps>")
            if p == "/query/active-app":
                if tv.active:
                    name = dict(APPS)[tv.active]
                    return self._send(f'<active-app><app id="{tv.active}" type="appl">{name}</app></active-app>')
                return self._send("<active-app><app>Roku</app></active-app>")
            if p == "/query/media-player":
                return self._send(f'<player error="false" state="{tv.player}"/>')
            if p.startswith("/query/icon/"):
                return self._send(PNG, "image/png")
            self._send("", code=404)

        def do_POST(self):
            p = self.path
            if tv.limited:
                return self._send("", code=403)
            if p.startswith("/keypress/"):
                k = unquote(p[len("/keypress/"):])
                tv.pressed.append(k)
                if k in ("PowerOff",):
                    tv.power = "DisplayOff"
                elif k in ("PowerOn",):
                    tv.power = "PowerOn"
                elif k == "Home":
                    tv.active = None
                elif k == "Play":
                    tv.player = "pause" if tv.player == "play" else "play"
                return self._send("")
            if p.startswith("/launch/"):
                tv.active = unquote(p[len("/launch/"):])
                return self._send("")
            self._send("", code=404)
    return H


def start(host="127.0.0.1", port=8060):
    tv = Roku()
    srv = ThreadingHTTPServer((host, port), handler(tv))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return tv, srv


if __name__ == "__main__":
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    tv, srv = start(host)
    print(f"Roku falsa em http://{host}:8060 — Ctrl+C para parar", flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
