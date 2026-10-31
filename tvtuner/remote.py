"""Controle remoto pela rede: websocket TLS na porta 8002.

Na primeira conexão a TV mostra "Permitir"; aceito, ela devolve um token que
vai junto nas próximas e dispensa a pergunta.
"""
import base64
import json
import ssl
import time

import websocket

from . import config

APP_NAME = "TV Tuner"


class PairingError(RuntimeError):
    pass


class Remote:
    def __init__(self, ip, token=None, name=APP_NAME, timeout=30):
        self.ip = ip
        self.token = token
        self.name = name
        self.timeout = timeout
        self.ws = None

    def _url(self):
        n = base64.b64encode(self.name.encode()).decode()
        url = f"wss://{self.ip}:8002/api/v2/channels/samsung.remote.control?name={n}"
        if self.token:
            url += f"&token={self.token}"
        return url

    def connect(self):
        self.ws = websocket.create_connection(
            self._url(), timeout=self.timeout, sslopt={"cert_reqs": ssl.CERT_NONE, "check_hostname": False}
        )
        # Sem token, esta leitura espera a pessoa aceitar na TV.
        while True:
            msg = json.loads(self.ws.recv())
            ev = msg.get("event")
            if ev == "ms.channel.connect":
                tok = (msg.get("data") or {}).get("token")
                if tok and tok != self.token:
                    self.token = tok
                    config.remember(self.ip, token=tok)
                return self
            if ev == "ms.channel.unauthorized":
                raise PairingError("A TV recusou (Negar, ou acesso bloqueado em Gerenciador de dispositivos).")
            if ev == "ms.channel.timeOut":
                raise PairingError("Ninguém aceitou o pedido na TV a tempo.")

    def ping(self, timeout=2.5):
        """Ping websocket; volta quando a TV responde qualquer coisa (pong ou evento)."""
        import websocket as _ws
        self.ws.ping()
        old = self.ws.gettimeout()
        self.ws.settimeout(timeout)
        try:
            while True:
                op, _ = self.ws.recv_data_frame(True)
                if op in (_ws.ABNF.OPCODE_PONG, _ws.ABNF.OPCODE_TEXT):
                    return
                if op == _ws.ABNF.OPCODE_CLOSE:
                    raise ConnectionError("TV fechou a conexão")
        finally:
            self.ws.settimeout(old)

    def close(self):
        if self.ws:
            self.ws.close()
            self.ws = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.close()

    def _send(self, method, params):
        self.ws.send(json.dumps({"method": method, "params": params}))

    def key(self, code, cmd="Click"):
        """cmd: Click, Press (segura) ou Release."""
        if not code.startswith("KEY_"):
            code = "KEY_" + code.upper()
        self._send("ms.remote.control", {
            "Cmd": cmd, "DataOfCmd": code, "Option": "false", "TypeOfRemote": "SendRemoteKey",
        })

    def keys(self, codes, delay=0.4):
        for c in codes:
            if isinstance(c, (int, float)):
                time.sleep(c)
                continue
            self.key(c)
            time.sleep(delay)

    def hold(self, code, seconds):
        self.key(code, "Press")
        time.sleep(seconds)
        self.key(code, "Release")

    def text(self, value):
        """Digita num campo de texto aberto na TV (teclado virtual visível)."""
        self._send("ms.remote.control", {
            "Cmd": base64.b64encode(value.encode()).decode(),
            "DataOfCmd": "base64", "TypeOfRemote": "SendInputString",
        })
        self._send("ms.remote.control", {"TypeOfRemote": "SendInputEnd"})

    def _emit(self, event, data=""):
        self._send("ms.channel.emit", {"event": event, "to": "host", "data": data})

    def _wait(self, event, seconds=3):
        # recv() some dentro de ping/pong e não respeita prazo: a TV pinga e o
        # timeout do socket nunca vence. Lê quadro a quadro com prazo próprio.
        end = time.time() + seconds
        try:
            while (left := end - time.time()) > 0:
                self.ws.settimeout(left)
                op, frame = self.ws.recv_data_frame(True)
                if op != websocket.ABNF.OPCODE_TEXT:
                    continue
                msg = json.loads(frame.data)
                if msg.get("event") == event:
                    return msg.get("data")
        except (websocket.WebSocketTimeoutException, TimeoutError):
            pass
        finally:
            self.ws.settimeout(self.timeout)
        return None

    def apps(self):
        """Apps instalados. Tenta o websocket; a AU7700 não responde, então cai no catálogo REST."""
        self._emit("ed.installedApp.get")
        data = self._wait("ed.installedApp.get") or {}
        if data.get("data"):
            return data["data"]
        from . import catalog
        return catalog.installed(self.ip)

    def launch(self, app_id, deep_link=False, meta=None):
        """Abre um app. Na AU7700, `ed.apps.launch` pelo websocket é aceito e
        ignorado (medido 31/10/2026, NATIVE_LAUNCH e DEEP_LINK); o POST da API
        REST abre. O websocket fica para URL (metaTag), que o REST não leva."""
        if app_id.startswith("TvTunerHom."):
            # App de desenvolvedor: REST e websocket não abrem; o SDB abre e confirma.
            from .sdb import Sdb
            out = Sdb(self.ip).shell("0 was_execute " + app_id, r"launched|spend time", 20)
            if "launched" not in out:
                raise RuntimeError("A TV não abriu o TV Tuner (Modo Desenvolvedor desligado?)")
            return
        if not meta:
            from . import rest
            try:
                rest.app_open(self.ip, app_id)
                return
            except Exception:
                pass
        self._emit("ed.apps.launch", {
            "appId": app_id,
            "action_type": "DEEP_LINK" if deep_link else "NATIVE_LAUNCH",
            **({"metaTag": meta} if meta else {}),
        })

    def browser(self, url):
        # Na AU7700 o navegador é 3202010022079; org.tizen.browser é o nome antigo.
        self.launch("3202010022079", meta=url)


def open_remote(ip=None):
    cfg = config.load()
    ip = ip or cfg.get("ip")
    if not ip:
        raise SystemExit("Sem IP. Rode: tv discover  (ou  tv --ip <ip-da-tv> ...)")
    return Remote(ip, config.tv(ip).get("token"))
