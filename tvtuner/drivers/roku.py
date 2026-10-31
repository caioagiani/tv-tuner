"""Roku (Roku TV e aparelhos Roku): External Control Protocol, HTTP na porta 8060.

Sem pareamento — qualquer um na rede controla, se a TV deixar: em
Configurações > Sistema > Configurações avançadas do sistema > Controle por
apps móveis > Acesso à rede, "Limitado" recusa com 403 e "Desativado" fecha a porta.
O ECP não informa o volume (só sobe/desce) nem diz se está no mudo (só alterna).
Referência: https://developer.roku.com/docs/developer-program/dev-tools/external-control-api.md
"""
import ipaddress
import re
import urllib.error
import urllib.request
from urllib.parse import quote
from xml.etree import ElementTree

from .. import config, discover, wol
from .base import HOME_APP, Driver, Unsupported

PORT = 8060

# Nomes da Samsung (idioma comum do painel) → teclas do ECP.
KEYS = {
    "KEY_UP": "Up", "KEY_DOWN": "Down", "KEY_LEFT": "Left", "KEY_RIGHT": "Right",
    "KEY_ENTER": "Select", "KEY_RETURN": "Back", "KEY_HOME": "Home", "KEY_SOURCE": "Home",
    "KEY_PLAY": "Play", "KEY_PAUSE": "Play", "KEY_PLAY_BACK": "Play", "KEY_FF": "Fwd", "KEY_REWIND": "Rev",
    "KEY_VOLUP": "VolumeUp", "KEY_VOLDOWN": "VolumeDown", "KEY_MUTE": "VolumeMute",
    "KEY_POWER": "Power", "KEY_POWEROFF": "PowerOff", "KEY_POWERON": "PowerOn",
    "KEY_INFO": "Info", "KEY_MENU": "Info", "KEY_TOOLS": "Info", "KEY_SEARCH": "Search",
    "KEY_CHUP": "ChannelUp", "KEY_CHDOWN": "ChannelDown", "KEY_TV": "InputTuner",
    "KEY_HDMI1": "InputHDMI1", "KEY_HDMI2": "InputHDMI2", "KEY_HDMI3": "InputHDMI3", "KEY_HDMI4": "InputHDMI4",
    "KEY_AV1": "InputAV1", "KEY_BACKSPACE": "Backspace", "KEY_REPLAY": "InstantReplay",
}
ECP_KEYS = set(KEYS.values()) | {"Enter", "FindRemote"}

MEDIA = {"play": "PLAYING", "pause": "PAUSED_PLAYBACK", "buffer": "PLAYING", "stop": "STOPPED",
         "close": "NO_MEDIA_PRESENT", "none": "NO_MEDIA_PRESENT"}


def _ecp(ip, path, method="GET", timeout=3.0):
    req = urllib.request.Request(f"http://{ip}:{PORT}/{path}", method=method, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(), r.headers.get("Content-Type") or ""
    except urllib.error.HTTPError as e:
        if e.code == 403:
            raise RuntimeError("A Roku recusou: em Configurações > Sistema > Configurações avançadas do sistema > "
                               "Controle por apps móveis, deixe o acesso à rede em Padrão") from None
        raise


def _xml(ip, path, timeout=3.0):
    return ElementTree.fromstring(_ecp(ip, path, timeout=timeout)[0])


class Roku(Driver):
    brand = "roku"
    label = "Roku"
    caps = frozenset({"keys", "text", "apps", "launch", "mute", "media", "power", "icons"})

    @classmethod
    def probe(cls, ip, timeout=1.0):
        try:
            x = _xml(ip, "query/device-info", timeout)
        except Exception:
            return None
        g = lambda k: (x.findtext(k) or "").strip() or None
        mode = g("power-mode")      # PowerOn, DisplayOff, Ready, Headless
        name = g("user-device-name") or g("friendly-device-name") or g("default-device-name")
        return {"brand": cls.brand, "name": name, "model": g("friendly-model-name") or g("model-name"),
                "power": "on" if mode in (None, "PowerOn") else "standby",
                "mac": g("wifi-mac") if g("network-type") == "wifi" else (g("ethernet-mac") or g("wifi-mac")),
                "os": "Roku OS " + (g("software-version") or "?"), "is_tv": g("is-tv") == "true", "raw": None}

    def _press(self, k):
        _ecp(self.ip, "keypress/" + k, "POST")

    def key(self, code):
        if code in ECP_KEYS:
            return self._press(code)
        k = KEYS.get(code if code.startswith("KEY_") else "KEY_" + code.upper())
        if not k:
            raise Unsupported(f"Roku não tem a tecla {code}")
        self._press(k)

    def text(self, value):
        # O ECP digita uma letra por vez no campo aberto (busca, login...).
        for ch in value:
            self._press("Lit_" + quote(ch, safe=""))

    def apps(self):
        x = _xml(self.ip, "query/apps")
        return [{"appId": a.get("id"), "name": (a.text or "").strip(), "icon": f"/appicon/{a.get('id')}"}
                for a in x.findall("app") if a.get("id")]

    def launch(self, app_id):
        if app_id == HOME_APP:
            return self._press("Home")
        _ecp(self.ip, "launch/" + quote(app_id, safe="."), "POST")

    def current_app(self, installed):
        a = _xml(self.ip, "query/active-app").find("app")
        # Na tela inicial vem <app>Roku</app>, sem id.
        if a is None or not a.get("id"):
            return None
        return {"appId": a.get("id"), "name": (a.text or "").strip()}

    def icon(self, app_id):
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", app_id):
            raise Unsupported("id inválido")
        return _ecp(self.ip, "query/icon/" + app_id)

    def set_mute(self, on):
        self._press("VolumeMute")       # só alterna: o ECP não diz se está no mudo

    def media(self):
        try:
            p = _xml(self.ip, "query/media-player", 2)
        except Exception:
            return None
        return MEDIA.get(p.get("state") or "none", "STOPPED")

    def media_cmd(self, action):
        if action == "stop":
            raise Unsupported("Roku não tem parar: use pausar ou voltar")
        self._press("Play")             # play e pausa são a mesma tecla

    def power_on(self):
        d = self.info(2)
        if d and d["power"] == "on":
            return "already"
        if d:                           # em espera a rede segue no ar
            self._press("PowerOn")
            return "key"
        mac = config.tv(self.ip).get("mac")
        if not mac:
            raise RuntimeError("A Roku não responde e o MAC é desconhecido")
        wol.wake(mac)
        wol.wake(mac, str(ipaddress.ip_network(discover.local_ip() + "/24", strict=False).broadcast_address))
        return "wol"

    def power_off(self):
        self._press("PowerOff")
