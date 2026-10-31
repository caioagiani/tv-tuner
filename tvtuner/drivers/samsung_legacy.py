"""Samsung 2010–2013 (séries C, D, E, F): protocolo legado, TCP na porta 55000.

Só teclas: sem lista de apps, sem volume lido, sem ligar pela rede (desligada,
a placa de rede apaga). Na primeira conexão a TV pergunta "Permitir?".
Protocolo conferido com samsungctl (MIT).
"""
import base64
import socket
import threading

from .. import config, discover
from ..remote import PairingError
from .base import HOME_APP, Driver, Unsupported

PORT = 55000
OK, DENIED, WAITING, CANCELLED, KEY_OK = b"\x64\x00\x01\x00", b"\x64\x00\x00\x00", b"\x0a", b"\x65", b"\x00\x00\x00\x00"


def _s(value, raw=False):
    data = value.encode() if isinstance(value, str) else value
    if not raw:
        data = base64.b64encode(data)
    return bytes([len(data)]) + b"\x00" + data


def _recv(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("a TV fechou a conexão")
        data += chunk
    return data


def _response(sock):
    head = _recv(sock, 3)
    _recv(sock, int.from_bytes(head[1:3], "little"))        # nome do app da TV
    return _recv(sock, int.from_bytes(_recv(sock, 2), "little"))


class SamsungLegacy(Driver):
    brand = "samsung_legacy"
    label = "Samsung (2010–2013)"
    caps = frozenset({"keys", "mute", "power", "pair"})

    def __init__(self, ip, emit=None):
        super().__init__(ip, emit)
        self.lock = threading.Lock()
        self.sock = None

    @classmethod
    def probe(cls, ip, timeout=1.0):
        # Samsung nova também abre a 55000 em alguns anos: quem responde na 8001 é do driver novo.
        if not discover.port_open(ip, PORT, timeout) or discover.port_open(ip, 8001, timeout):
            return None
        saved = config.tv(ip)
        return {"brand": cls.brand, "name": saved.get("name") or "Samsung TV", "model": saved.get("model") or "Samsung (legado)",
                "power": "on", "mac": saved.get("mac"), "os": "Samsung legado", "raw": None}

    def paired(self):
        return bool(config.tv(self.ip).get("token"))

    def _open(self, wait):
        s = socket.create_connection((self.ip, PORT), timeout=wait)
        payload = b"\x64\x00" + _s(discover.local_ip()) + _s("tvtuner") + _s("TV Tuner")
        s.sendall(b"\x00" + _s(b"", True) + _s(payload, True))
        while True:
            r = _response(s)
            if r == OK:
                break
            if r[:1] == WAITING:
                continue                    # "Permitir?" na tela; espera a pessoa
            s.close()
            raise PairingError("A TV recusou (Negar, ou o pedido expirou)." if r in (DENIED,) or r[:1] == CANCELLED
                               else f"Resposta inesperada da TV: {r.hex()}")
        s.settimeout(5)
        return s

    def pair(self, timeout=40):
        self._open(timeout).close()
        config.remember(self.ip, token="legacy")

    def _send(self, key):
        for attempt in (1, 2):
            with self.lock:
                try:
                    if self.sock is None:
                        if not self.paired():
                            raise RuntimeError("TV não pareada — toque no nome da TV e em Parear")
                        self.sock = self._open(6)
                    self.sock.sendall(b"\x00" + _s(b"", True) + _s(b"\x00\x00\x00" + _s(key), True))
                    if _response(self.sock) not in (KEY_OK, OK):
                        raise ConnectionError("tecla recusada")
                    return
                except RuntimeError:
                    raise
                except Exception:
                    self.close()
                    if attempt == 2:
                        raise

    def close(self):
        s, self.sock = self.sock, None
        if s:
            try:
                s.close()
            except Exception:
                pass

    def key(self, code):
        if not code.startswith("KEY_"):
            code = "KEY_" + code.upper()
        self._send(code)                    # os nomes KEY_* nasceram neste protocolo

    def launch(self, app_id):
        if app_id == HOME_APP:
            return self._send("KEY_CONTENTS")   # Smart Hub
        raise Unsupported("TV antiga não abre app pela rede")

    def set_mute(self, on):
        self._send("KEY_MUTE")              # só alterna

    def power_on(self):
        if self.info(1):
            return "already"
        raise Unsupported("TV de 2010–2013 desligada não liga pela rede: use o controle")

    def power_off(self):
        self._send("KEY_POWEROFF")
