"""LG webOS (2014+): SSAP por websocket na 3000 (ws) ou 3001 (wss, obrigatório
nos firmwares novos), e um segundo websocket só para teclas (pointer input).

Pareamento: a TV pergunta "Permitir?" uma vez e devolve uma client-key, que
fica no lugar do token da Samsung. Ligar só por Wake-on-LAN (Configurações >
Geral > Dispositivos > Ligar via Wi-Fi) ou, em espera com rede, pelo SSAP.
Protocolo conferido com aiowebostv (Home Assistant, Apache-2.0).
"""
import ipaddress
import itertools
import json
import ssl
import threading
import time
import urllib.request

import websocket

from .. import config, discover, wol
from ..remote import PairingError
from .base import HOME_APP, Driver, Unsupported

PORTS = ((3000, "ws"), (3001, "wss"))
SSL = {"cert_reqs": ssl.CERT_NONE, "check_hostname": False}

PERMISSIONS = [
    "APP_TO_APP", "CLOSE", "CONTROL_AUDIO", "CONTROL_DISPLAY", "CONTROL_INPUT_JOYSTICK",
    "CONTROL_INPUT_MEDIA_PLAYBACK", "CONTROL_INPUT_MEDIA_RECORDING", "CONTROL_INPUT_TEXT", "CONTROL_INPUT_TV",
    "CONTROL_MOUSE_AND_KEYBOARD", "CONTROL_POWER", "CONTROL_TV_SCREEN", "LAUNCH", "LAUNCH_WEBAPP",
    "READ_APP_STATUS", "READ_COUNTRY_INFO", "READ_CURRENT_CHANNEL", "READ_INPUT_DEVICE_LIST",
    "READ_INSTALLED_APPS", "READ_LGE_SDX", "READ_LGE_TV_INPUT_EVENTS", "READ_NETWORK_STATE",
    "READ_NOTIFICATIONS", "READ_POWER_STATE", "READ_RUNNING_APPS", "READ_SETTINGS", "READ_TV_CHANNEL_LIST",
    "READ_TV_CURRENT_TIME", "READ_UPDATE_INFO", "SEARCH", "TEST_OPEN", "TEST_PROTECTED", "TEST_SECURE",
    "UPDATE_FROM_REMOTE_APP", "WRITE_NOTIFICATION_ALERT", "WRITE_NOTIFICATION_TOAST", "WRITE_SETTINGS",
]

# Nomes da Samsung → botões do pointer input.
KEYS = {
    "KEY_UP": "UP", "KEY_DOWN": "DOWN", "KEY_LEFT": "LEFT", "KEY_RIGHT": "RIGHT", "KEY_ENTER": "ENTER",
    "KEY_RETURN": "BACK", "KEY_EXIT": "EXIT", "KEY_HOME": "HOME", "KEY_SOURCE": "INPUT_HUB", "KEY_MENU": "MENU",
    "KEY_TOOLS": "QMENU", "KEY_INFO": "INFO", "KEY_GUIDE": "GUIDE", "KEY_PLAY": "PLAY", "KEY_PAUSE": "PAUSE",
    "KEY_STOP": "STOP", "KEY_FF": "FASTFORWARD", "KEY_REWIND": "REWIND", "KEY_VOLUP": "VOLUMEUP",
    "KEY_VOLDOWN": "VOLUMEDOWN", "KEY_MUTE": "MUTE", "KEY_CHUP": "CHANNELUP", "KEY_CHDOWN": "CHANNELDOWN",
    "KEY_POWER": "POWER", "KEY_RED": "RED", "KEY_GREEN": "GREEN", "KEY_YELLOW": "YELLOW", "KEY_CYAN": "BLUE",
    **{f"KEY_{n}": str(n) for n in range(10)},
}
BUTTONS = set(KEYS.values()) | {"ASTERISK", "CC", "DASH", "LIST", "RECENT", "MYAPPS", "NETFLIX", "AMAZON"}
STANDBY = ("Active Standby", "Suspend", "Power Off")


def _open(ip, timeout):
    last = None
    for port, scheme in PORTS:
        try:
            return websocket.create_connection(f"{scheme}://{ip}:{port}", timeout=timeout, sslopt=SSL,
                                               suppress_origin=True)
        except Exception as e:      # firmware novo recusa a 3000; antigo não tem a 3001
            last = e
    raise last


class LG(Driver):
    brand = "lg"
    label = "LG"
    caps = frozenset({"keys", "text", "apps", "launch", "volume", "mute", "media", "power", "pair",
                      "ime", "browser", "icons"})

    def __init__(self, ip, emit=None):
        super().__init__(ip, emit)
        self.lock = threading.Lock()        # abrir/fechar conexão
        self.send_lock = threading.Lock()
        self.ws = None
        self.pointer = None
        self.ids = itertools.count(1)
        self.waiting = {}                   # id -> [Event, resposta]
        self.icons = {}                     # appId -> URL do ícone na TV
        self.names = {}

    # -- quem é -----------------------------------------------------------------------
    @classmethod
    def probe(cls, ip, timeout=1.0):
        # IP vazio da varredura: metade do prazo em cada porta, não o dobro.
        if not any(discover.port_open(ip, p, timeout / len(PORTS)) for p, _ in PORTS):
            return None
        try:
            ws = _open(ip, timeout + 1)
        except Exception:
            return None
        try:
            ws.send(json.dumps({"id": "hello", "type": "hello", "payload": {}}))
            hello = json.loads(ws.recv())
            if hello.get("type") != "hello":
                return None
            # Firmware novo responde o modelo antes do registro; o antigo, erro.
            ws.send(json.dumps({"id": "sys", "type": "request", "uri": "ssap://system/getSystemInfo", "payload": {}}))
            try:
                model = (json.loads(ws.recv()).get("payload") or {}).get("modelName")
            except Exception:
                model = None
        except Exception:
            return None
        finally:
            ws.close()
        p = hello.get("payload") or {}
        return {"brand": cls.brand, "name": "LG " + (model or "webOS TV"), "model": model or "LG webOS",
                "power": "on", "mac": None, "os": f"webOS {p.get('deviceOSReleaseVersion') or '?'}", "raw": p}

    def info(self, timeout=1.5):
        if self.ws is None:
            if not self.paired():
                return self.probe(self.ip, timeout)
            # Desligada: não paga o tempo-limite de abrir o websocket nas duas portas.
            if not any(discover.port_open(self.ip, p, timeout) for p, _ in PORTS):
                return None
        try:
            sysinfo = self.request("system/getSystemInfo", timeout=timeout + 1)
        except Exception:
            self.close()
            return None
        try:
            state = self.request("com.webos.service.tvpower/power/getPowerState", timeout=timeout).get("state")
        except Exception:
            state = None                    # firmware antigo não tem: respondeu = ligada
        saved = config.tv(self.ip)
        if not saved.get("mac"):
            # O MAC só sai depois do pareamento; sem ele não há Wake-on-LAN.
            try:
                net = self.request("com.webos.service.connectionmanager/getinfo", timeout=timeout)
                mac = (net.get("wifiInfo") or {}).get("macAddress") or (net.get("wiredInfo") or {}).get("macAddress")
                if mac and saved:
                    config.remember(self.ip, mac=mac)
            except Exception:
                pass
        return {"brand": self.brand, "name": saved.get("name") or "LG " + (sysinfo.get("modelName") or "TV"),
                "model": sysinfo.get("modelName"), "power": "standby" if state in STANDBY else "on",
                "mac": saved.get("mac"), "os": "webOS", "raw": sysinfo}

    def paired(self):
        return bool(config.tv(self.ip).get("token"))

    # -- conexão ----------------------------------------------------------------------
    def _register(self, ws, key, wait):
        payload = {"forcePairing": False, "pairingType": "PROMPT",
                   "manifest": {"appVersion": "1.1", "manifestVersion": 1, "permissions": PERMISSIONS}}
        if key:
            payload["client-key"] = key
        ws.send(json.dumps({"type": "register", "id": "register_0", "payload": payload}))
        end = time.time() + wait
        while time.time() < end:
            ws.settimeout(max(0.5, end - time.time()))
            try:
                msg = json.loads(ws.recv())
            except websocket.WebSocketTimeoutException:
                break
            if msg.get("type") == "registered":
                new = (msg.get("payload") or {}).get("client-key")
                if new and new != key:
                    config.remember(self.ip, token=new)
                return new or key
            if msg.get("type") == "error":
                raise PairingError("A TV recusou o pareamento: " + str(msg.get("error")))
        raise PairingError("Ninguém aceitou o pedido na TV a tempo.")

    def _connect(self):
        if self.ws is not None and getattr(self.ws, "connected", False):
            return self.ws
        key = config.tv(self.ip).get("token")
        if not key:
            raise RuntimeError("TV não pareada — teclas e texto pedem pareamento (toque no nome da TV)")
        ws = _open(self.ip, 4)
        self._register(ws, key, 6)
        ws.settimeout(None)
        self.ws, self.pointer = ws, None
        threading.Thread(target=self._reader, args=(ws,), daemon=True).start()
        # Teclado da TV abrindo/fechando e volume mudando chegam sozinhos.
        self._subscribe("com.webos.service.ime/registerRemoteKeyboard")
        self._subscribe("audio/getVolume")
        return ws

    def _reader(self, ws):
        while self.ws is ws:
            try:
                msg = json.loads(ws.recv())
            except Exception:
                if self.ws is ws:
                    self.ws = None
                return
            mid, payload = msg.get("id"), msg.get("payload") or {}
            if mid in self.waiting:
                slot = self.waiting[mid]
                slot[1] = msg
                slot[0].set()
            if mid == "sub:com.webos.service.ime/registerRemoteKeyboard" and "currentWidget" in payload:
                w = payload["currentWidget"] or {}
                self.emit("ime", {"event": "lg.ime", "open": bool(w.get("focus")), "data": w.get("contentType")})
            elif mid == "sub:audio/getVolume":
                st = payload.get("volumeStatus") or payload
                if "volume" in st:
                    self.emit("live", {"volume": st["volume"], "mute": bool(st.get("muteStatus", st.get("muted")))})

    def _subscribe(self, uri):
        with self.send_lock:
            self.ws.send(json.dumps({"id": "sub:" + uri, "type": "subscribe", "uri": "ssap://" + uri, "payload": {}}))

    def request(self, uri, payload=None, timeout=5.0):
        for attempt in (1, 2):
            with self.lock:
                ws = self._connect()
            mid = f"r{next(self.ids)}"
            slot = self.waiting[mid] = [threading.Event(), None]
            try:
                with self.send_lock:
                    ws.send(json.dumps({"id": mid, "type": "request", "uri": "ssap://" + uri, "payload": payload or {}}))
                if not slot[0].wait(timeout):
                    raise TimeoutError(f"a TV não respondeu a {uri}")
            except Exception:
                self.close()
                if attempt == 2:
                    raise
                continue
            finally:
                self.waiting.pop(mid, None)
            msg = slot[1]
            if msg.get("type") == "error":
                raise RuntimeError(f"LG recusou {uri}: {msg.get('error')}")
            return msg.get("payload") or {}

    def _button(self, name):
        p = self.pointer
        if p is None or not getattr(p, "connected", False):
            path = self.request("com.webos.service.networkinput/getPointerInputSocket")["socketPath"]
            p = self.pointer = websocket.create_connection(path, timeout=4, sslopt=SSL, suppress_origin=True)
        try:
            p.send(f"type:button\nname:{name}\n\n")
        except Exception:
            self.pointer = None
            raise

    def pair(self, timeout=40):
        ws = _open(self.ip, 6)
        try:
            self._register(ws, None, timeout)
        finally:
            ws.close()

    def close(self):
        for s in (self.ws, self.pointer):
            try:
                s and s.close()
            except Exception:
                pass
        self.ws = self.pointer = None

    # -- controle ---------------------------------------------------------------------
    def key(self, code):
        if code in BUTTONS:
            return self._button(code)
        b = KEYS.get(code if code.startswith("KEY_") else "KEY_" + code.upper())
        if not b:
            raise Unsupported(f"LG não tem a tecla {code}")
        self._button(b)

    def text(self, value):
        self.request("com.webos.service.ime/insertText", {"text": value, "replace": 0})
        self.request("com.webos.service.ime/sendEnterKey")

    def browser(self, url):
        self.request("system.launcher/open", {"target": url})

    def apps(self):
        pts = self.request("com.webos.applicationManager/listLaunchPoints").get("launchPoints") or []
        out = []
        for a in pts:
            if not a.get("id"):
                continue
            self.icons[a["id"]] = a.get("largeIcon") or a.get("icon")
            self.names[a["id"]] = a.get("title") or a["id"]
            out.append({"appId": a["id"], "name": self.names[a["id"]], "icon": f"/appicon/{a['id']}"})
        return out

    def launch(self, app_id):
        if app_id == HOME_APP:
            return self._button("HOME")
        self.request("system.launcher/launch", {"id": app_id})

    def current_app(self, installed):
        app_id = self.request("com.webos.applicationManager/getForegroundAppInfo").get("appId")
        if not app_id or app_id.startswith("com.webos.app.home"):
            return None
        if app_id not in self.names:
            installed()
        return {"appId": app_id, "name": self.names.get(app_id, app_id)}

    def icon(self, app_id):
        if app_id not in self.icons:
            self.apps()
        url = self.icons.get(app_id)
        if not url:
            raise Unsupported("sem ícone")
        if url.startswith("/"):
            url = f"http://{self.ip}:{PORTS[0][0]}{url}"
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        with urllib.request.urlopen(url, timeout=4, context=ctx if url.startswith("https") else None) as r:
            return r.read(), r.headers.get("Content-Type") or "image/png"

    # -- áudio e mídia ----------------------------------------------------------------
    def volume(self):
        p = self.request("audio/getVolume")
        return (p.get("volumeStatus") or p).get("volume")

    def set_volume(self, level):
        level = max(0, min(100, int(level)))
        self.request("audio/setVolume", {"volume": level})
        return level

    def muted(self):
        p = self.request("audio/getStatus")
        return bool(p.get("mute"))

    def set_mute(self, on):
        self.request("audio/setMute", {"mute": bool(on)})

    def media_cmd(self, action):
        self.request("media.controls/" + action)

    # -- energia ----------------------------------------------------------------------
    def power_on(self):
        d = None
        try:
            d = self.info(2) if self.paired() else self.probe(self.ip, 2)
        except Exception:
            pass
        if d and d["power"] == "on":
            return "already"
        if d and self.paired():                 # em espera com rede: o SSAP acorda
            self.request("system/turnOn")
            return "key"
        mac = config.tv(self.ip).get("mac")
        if not mac:
            raise RuntimeError("A LG não responde e o MAC é desconhecido (ative Ligar via Wi-Fi e rode tv use)")
        wol.wake(mac)
        wol.wake(mac, str(ipaddress.ip_network(discover.local_ip() + "/24", strict=False).broadcast_address))
        return "wol"

    def power_off(self):
        self.request("system/turnOff")
