"""Samsung 2010–2013 de mentira: porta 55000, "Permitir?" e teclas."""
import base64
import socket
import threading

from tvtuner.drivers.samsung_legacy import _recv, _s


class Legacy:
    def __init__(self):
        self.allow = True
        self.keys = []


def _packet(sock):
    _recv(sock, 1)
    _recv(sock, int.from_bytes(_recv(sock, 2), "little"))       # app (vazio)
    return _recv(sock, int.from_bytes(_recv(sock, 2), "little"))


def _reply(sock, body):
    sock.sendall(b"\x00" + _s(b"iapp.samsung", True) + _s(body, True))


def serve(tv, sock):
    _packet(sock)                                   # pedido de acesso
    _reply(sock, b"\x0a\x00\x02\x00\x00\x00")       # "Permitir?" na tela
    if not tv.allow:
        return _reply(sock, b"\x64\x00\x00\x00")
    _reply(sock, b"\x64\x00\x01\x00")
    while True:
        p = _packet(sock)
        n = p[3]
        tv.keys.append(base64.b64decode(p[5:5 + n]).decode())
        _reply(sock, b"\x00\x00\x00\x00")


def start(host="127.0.0.1", port=55000):
    tv = Legacy()
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(8)

    def loop():
        while True:
            try:
                c, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=lambda: _safe(tv, c), daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()
    return tv, srv


def _safe(tv, c):
    try:
        serve(tv, c)
    except Exception:
        pass
    finally:
        c.close()
