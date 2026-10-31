"""Android TV / Google TV (Sony, TCL, Philips, Chromecast, Shield...): protocolo
"Android TV Remote v2", o mesmo do app Google TV no celular.

TLS com certificado do cliente nas portas 6467 (parear) e 6466 (controle),
mensagens protobuf com tamanho em varint. Parear: a TV mostra um código de 6
caracteres; o segredo é SHA-256 das chaves RSA dos dois lados + o código.
Não há lista de apps nem volume absoluto no protocolo: apps abrem por link
(market://launch?id=<pacote>) e o volume só sobe/desce, mas a TV avisa o nível.
Protocolo conferido com androidtvremote2 (Home Assistant, Apache-2.0).
"""
import hashlib
import ipaddress
import os
import socket
import ssl
import subprocess
import threading
import time

from .. import config, discover, wol
from ..remote import PairingError
from .base import HOME_APP, Driver, Unsupported

API_PORT, PAIR_PORT = 6466, 6467
CERT_DIR = os.path.join(config.DIR, "androidtv")

# Recursos pedidos à TV (bitmask do RemoteConfigure): ping, tecla, ime (dá o app atual), energia, volume, link.
FEATURES = 1 | 2 | 4 | 32 | 64 | 512

KEYS = {
    "KEY_UP": 19, "KEY_DOWN": 20, "KEY_LEFT": 21, "KEY_RIGHT": 22, "KEY_ENTER": 23, "KEY_RETURN": 4,
    "KEY_HOME": 3, "KEY_SOURCE": 178, "KEY_MENU": 82, "KEY_TOOLS": 176, "KEY_INFO": 165, "KEY_GUIDE": 172,
    "KEY_PLAY": 85, "KEY_PAUSE": 127, "KEY_STOP": 86, "KEY_FF": 90, "KEY_REWIND": 89,
    "KEY_VOLUP": 24, "KEY_VOLDOWN": 25, "KEY_MUTE": 164, "KEY_CHUP": 166, "KEY_CHDOWN": 167,
    "KEY_POWER": 26, "KEY_POWERON": 224, "KEY_POWEROFF": 223, "KEY_SEARCH": 84, "KEY_BACKSPACE": 67,
    "KEY_RED": 183, "KEY_HDMI1": 243, "KEY_HDMI2": 244, "KEY_HDMI3": 245, "KEY_HDMI4": 246,
    **{f"KEY_{n}": 7 + n for n in range(10)},
}

# Sem lista de instalados no protocolo: os comuns, abertos pelo pacote.
APPS = [
    ("com.netflix.ninja", "Netflix"), ("com.google.android.youtube.tv", "YouTube"),
    ("com.amazon.amazonvideo.livingroom", "Prime Video"), ("com.disney.disneyplus", "Disney+"),
    ("com.wbd.stream", "Max"), ("com.globo.globotv", "Globoplay"), ("com.spotify.tv.android", "Spotify"),
    ("com.apple.atve.androidtv.appletv", "Apple TV"), ("tv.twitch.android.app", "Twitch"),
    ("com.plexapp.android", "Plex"), ("com.google.android.youtube.tvkids", "YouTube Kids"),
]


# -- protobuf mínimo ------------------------------------------------------------------
def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def pb(*fields):
    """pb((1, 5), (2, "texto"), (3, pb(...))): int vira varint, str/bytes vira bloco."""
    out = bytearray()
    for num, val in fields:
        if isinstance(val, bool) or isinstance(val, int):
            out += _varint(num << 3) + _varint(int(val))
        else:
            data = val.encode() if isinstance(val, str) else val
            out += _varint(num << 3 | 2) + _varint(len(data)) + data
    return bytes(out)


def parse(data):
    """{campo: [valores]}; bloco fica em bytes (mensagem interna: parse de novo)."""
    out, i = {}, 0
    while i < len(data):
        key, i = _read_varint(data, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            val, i = _read_varint(data, i)
        elif wt == 2:
            n, i = _read_varint(data, i)
            val, i = data[i:i + n], i + n
        elif wt == 5:
            val, i = data[i:i + 4], i + 4
        elif wt == 1:
            val, i = data[i:i + 8], i + 8
        else:
            raise ValueError(f"protobuf: tipo {wt}")
        out.setdefault(num, []).append(val)
    return out


def _read_varint(data, i):
    n = shift = 0
    while True:
        b = data[i]
        i += 1
        n |= (b & 0x7F) << shift
        if not b & 0x80:
            return n, i
        shift += 7


def first(msg, num, default=None):
    return msg.get(num, [default])[0]


def send_msg(sock, payload):
    sock.sendall(_varint(len(payload)) + payload)


def recv_msg(sock):
    n = shift = 0
    while True:
        b = sock.recv(1)
        if not b:
            raise ConnectionError("a TV fechou a conexão")
        n |= (b[0] & 0x7F) << shift
        if not b[0] & 0x80:
            break
        shift += 7
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            raise ConnectionError("a TV fechou a conexão")
        data += chunk
    return parse(data)


# -- certificados (DER à mão: só o necessário) -------------------------------------------
def _tlv(d, i):
    tag, n = d[i], d[i + 1]
    i += 2
    if n & 0x80:
        k = n & 0x7F
        n, i = int.from_bytes(d[i:i + k], "big"), i + k
    return tag, d[i:i + n], i + n


def _children(d):
    out, i = [], 0
    while i < len(d):
        tag, val, i = _tlv(d, i)
        out.append((tag, val))
    return out


def cert_info(der):
    """(atributos do subject {oid: texto}, módulo RSA, expoente) de um certificado X.509."""
    tbs = _children(_children(_tlv(der, 0)[1])[0][1])
    if tbs[0][0] == 0xA0:            # versão explícita
        tbs = tbs[1:]
    subject, spki = tbs[4][1], tbs[5][1]
    names = {}
    for _, rdn in _children(subject):
        for _, atv in _children(rdn):
            (_, oid), (_, val) = _children(atv)[:2]
            names[oid.hex()] = val.decode("utf-8", "replace")
    bits = _children(spki)[1][1][1:]        # BIT STRING sem o byte de bits não usados
    n, e = _children(_children(bits)[0][1])
    return names, int.from_bytes(n[1], "big"), int.from_bytes(e[1], "big")


OID_CN, OID_DNQ = "550403", "55042e"


def name_and_mac(der):
    """CN=atvremote/darcy/darcy/SHIELD Android TV/XX:XX... ou dnQualifier=fugu/fugu/Nexus Player."""
    names, _, _ = cert_info(der)
    cn, dnq = names.get(OID_CN, ""), names.get(OID_DNQ, "")
    parts = cn.split("/")
    name = dnq.split("/")[-1] if dnq else (parts[-2] if len(parts) > 1 else cn)
    mac = parts[-1] if len(parts) > 1 and parts[-1].count(":") == 5 else None
    return name, mac


def client_cert():
    """Certificado deste painel perante as TVs Android (um só, gerado uma vez)."""
    cert, key = os.path.join(CERT_DIR, "cert.pem"), os.path.join(CERT_DIR, "key.pem")
    if not (os.path.exists(cert) and os.path.exists(key)):
        os.makedirs(CERT_DIR, exist_ok=True)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
                        "-subj", "/CN=atvremote/O=TV Tuner", "-keyout", key, "-out", cert],
                       check=True, capture_output=True, timeout=30)
        os.chmod(key, 0o600)
    return cert, key


def _context():
    cert, key = client_cert()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.load_cert_chain(cert, key)
    return ctx


def _hex(n):
    s = f"{n:X}"
    return bytes.fromhex(s if len(s) % 2 == 0 else "0" + s)


def secret(client_der, server_der, code):
    """O que prova à TV que a pessoa digitou o código certo. O 1º byte do hash
    é o começo do próprio código: código errado é recusado aqui mesmo."""
    _, cn, ce = cert_info(client_der)
    _, sn, se = cert_info(server_der)
    h = hashlib.sha256()
    for v in (cn, ce, sn, se):
        h.update(_hex(v))
    h.update(bytes.fromhex(code[2:]))
    return h.digest()


class AndroidTV(Driver):
    brand = "androidtv"
    label = "Android TV"
    caps = frozenset({"keys", "text", "apps", "launch", "mute", "media", "power", "pair"})
    pair_with_code = True

    IDLE_S = 16          # a TV pinga a cada ~5 s; nada em 16 s = conexão morta

    def __init__(self, ip, emit=None):
        super().__init__(ip, emit)
        self.lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.sock = None
        self.seen = 0.0
        self.is_on = None
        self.device = {}
        self.app = None
        self.vol = None                 # {"level", "max", "muted"}
        self.ime = (0, 0)
        self.pairing = None

    # -- quem é -------------------------------------------------------------------------
    @classmethod
    def probe(cls, ip, timeout=1.0):
        if not discover.port_open(ip, PAIR_PORT, timeout):
            return None
        try:
            with socket.create_connection((ip, PAIR_PORT), timeout=timeout + 1) as raw:
                with _context().wrap_socket(raw) as s:
                    der = s.getpeercert(True)
            name, mac = name_and_mac(der)
        except Exception:
            return None
        return {"brand": cls.brand, "name": name or "Android TV", "model": name or "Android TV",
                "power": "on", "mac": mac, "os": "Android TV", "raw": None}

    def info(self, timeout=1.5):
        if not self.paired():
            return self.probe(self.ip, timeout)
        if self.sock is None and not discover.port_open(self.ip, API_PORT, timeout):
            return None
        try:
            with self.lock:
                self._connect()
        except Exception:
            return None
        saved = config.tv(self.ip)
        model = self.device.get("model") or saved.get("model")
        vendor = self.device.get("vendor")
        return {"brand": self.brand, "name": saved.get("name") or " ".join(filter(None, (vendor, model))) or "Android TV",
                "model": model, "power": "on" if self.is_on else "standby", "mac": saved.get("mac"),
                "os": "Android TV", "raw": self.device}

    def paired(self):
        return bool(config.tv(self.ip).get("token"))

    # -- pareamento (dois passos: a TV mostra o código, a pessoa digita) ----------------
    def pair_start(self):
        raw = socket.create_connection((self.ip, PAIR_PORT), timeout=10)
        s = _context().wrap_socket(raw)
        outer = lambda *f: pb((1, 2), (2, 200), *f)       # protocol_version 2, STATUS_OK
        enc = pb((1, 3), (2, 6))                          # hexadecimal, 6 símbolos
        send_msg(s, outer((10, pb((1, "atvremote"), (2, "TV Tuner")))))
        for _ in range(3):
            m = recv_msg(s)
            if first(m, 2, 200) != 200:
                s.close()
                raise PairingError(f"A TV recusou o pareamento (status {first(m, 2)})")
            if 11 in m:
                send_msg(s, outer((20, pb((1, enc), (3, 1)))))     # options: entrada, papel INPUT
            elif 20 in m:
                send_msg(s, outer((30, pb((1, enc), (2, 1)))))     # configuration
            elif 31 in m:
                self.pairing = s                                   # código já está na tela
                return
        s.close()
        raise PairingError("A TV não mostrou o código")

    def pair_finish(self, code):
        code = (code or "").strip().upper()
        s, self.pairing = self.pairing, None
        if not s:
            raise PairingError("Comece o pareamento de novo (o código expirou)")
        try:
            if len(code) != 6 or any(c not in "0123456789ABCDEF" for c in code):
                raise PairingError("O código tem 6 caracteres, de 0–9 e A–F")
            mine = ssl.PEM_cert_to_DER_cert(open(client_cert()[0]).read())
            h = secret(mine, s.getpeercert(True), code)
            if h[0] != int(code[:2], 16):
                raise PairingError("Código errado — confira na tela da TV")
            send_msg(s, pb((1, 2), (2, 200), (40, pb((1, h)))))
            m = recv_msg(s)
            if 41 not in m:
                raise PairingError(f"A TV recusou o código (status {first(m, 2)})")
        finally:
            s.close()
        # O certificado É a credencial; o token só marca "pareada" como nas outras marcas.
        config.remember(self.ip, token="cert:" + hashlib.sha256(mine).hexdigest()[:16])

    def pair(self, timeout=60):
        """CLI: mostra o código na TV e pergunta no terminal."""
        self.pair_start()
        self.pair_finish(input("Código que apareceu na TV: "))

    # -- conexão de controle ------------------------------------------------------------
    def _connect(self):
        if self.sock is not None and time.time() - self.seen < self.IDLE_S:
            return self.sock
        self._drop()
        if not self.paired():
            raise RuntimeError("TV não pareada — toque no nome da TV e em Parear (a TV mostra um código)")
        raw = socket.create_connection((self.ip, API_PORT), timeout=5)
        try:
            s = _context().wrap_socket(raw)
        except ssl.SSLError:
            raw.close()
            raise RuntimeError("A TV não reconhece este painel: pareie de novo") from None
        s.settimeout(5)
        end = time.time() + 6
        while time.time() < end:              # configure → set_active → start
            try:
                m = recv_msg(s)
            except ConnectionError:
                s.close()
                raise RuntimeError("A TV fechou a conexão: pareie de novo") from None
            self._handle(s, m)
            if 40 in m:
                break
        s.settimeout(None)
        self.sock, self.seen = s, time.time()
        threading.Thread(target=self._reader, args=(s,), daemon=True).start()
        return s

    def _handle(self, s, m):
        reply = None
        if 1 in m:                            # remote_configure
            cfg = parse(m[1][0])
            dev = parse(first(cfg, 2, b""))
            self.device = {"model": first(dev, 1, b"").decode(), "vendor": first(dev, 2, b"").decode()}
            reply = pb((1, pb((1, FEATURES & (first(cfg, 1, FEATURES) or FEATURES)),
                              (2, pb((3, 1), (4, "1"), (5, "atvremote"), (6, "1.0.0"))))))
        elif 2 in m:                          # remote_set_active
            reply = pb((2, pb((1, FEATURES))))
        elif 8 in m:                          # ping
            reply = pb((9, pb((1, first(parse(m[8][0]), 1, 0)))))
        elif 20 in m:                         # ime_key_inject: traz o app em primeiro plano
            info = parse(first(parse(m[20][0]), 1, b""))
            pkg = first(info, 12, b"").decode()
            self.app = pkg or None
        elif 21 in m:                         # ime_batch_edit: contadores do campo de texto
            e = parse(m[21][0])
            self.ime = (first(e, 1, 0), first(e, 2, 0))
        elif 40 in m:                         # remote_start
            self.is_on = bool(first(parse(m[40][0]), 1, 0))
        elif 50 in m:                         # volume
            v = parse(m[50][0])
            self.vol = {"level": first(v, 7, 0), "max": first(v, 6, 0), "muted": bool(first(v, 8, 0))}
            self.emit("live", {"volume": self.vol["level"], "mute": self.vol["muted"]})
        if reply:
            with self.send_lock:
                send_msg(s, reply)

    def _reader(self, s):
        while self.sock is s:
            try:
                m = recv_msg(s)
            except Exception:
                if self.sock is s:
                    self.sock = None
                return
            self.seen = time.time()
            try:
                self._handle(s, m)
            except Exception as e:
                print("android tv:", e, flush=True)

    def _send(self, payload):
        for attempt in (1, 2):
            with self.lock:
                s = self._connect()
            try:
                with self.send_lock:
                    send_msg(s, payload)
                return
            except Exception:
                self._drop()
                if attempt == 2:
                    raise

    def _drop(self):
        s, self.sock = self.sock, None
        if s:
            try:
                s.close()
            except Exception:
                pass

    def close(self):
        self._drop()

    # -- controle -----------------------------------------------------------------------
    def _code(self, code):
        self._send(pb((10, pb((1, code), (2, 3)))))          # key_inject, SHORT

    def key(self, code):
        k = KEYS.get(code if code.startswith("KEY_") else "KEY_" + code.upper())
        if k is None:
            raise Unsupported(f"Android TV não tem a tecla {code}")
        self._code(k)

    def text(self, value):
        n = len(value) - 1
        edit = pb((1, 1), (2, pb((1, n), (2, n), (3, value))))
        self._send(pb((21, pb((1, self.ime[0]), (2, self.ime[1]), (3, edit)))))

    def apps(self):
        return [{"appId": pkg, "name": name} for pkg, name in APPS]

    def launch(self, app_id):
        if app_id == HOME_APP:
            return self._code(3)
        link = app_id if "://" in app_id else "market://launch?id=" + app_id
        self._send(pb((90, pb((1, link)))))

    def current_app(self, installed):
        with self.lock:
            self._connect()
        if not self.app:
            return None
        names = dict(APPS)
        return {"appId": self.app, "name": names.get(self.app, self.app.split(".")[-1].capitalize())}

    # -- áudio e mídia ------------------------------------------------------------------
    def volume(self):
        return self.vol["level"] if self.vol else None

    def muted(self):
        return self.vol["muted"] if self.vol else None

    def set_mute(self, on):
        # Só existe a tecla que alterna; com o estado conhecido, só aperta se precisa.
        if self.vol is None or self.vol["muted"] != bool(on):
            self._code(164)

    def media_cmd(self, action):
        self._code({"play": 85, "pause": 85, "stop": 86}[action])

    # -- energia ------------------------------------------------------------------------
    def power_on(self):
        d = self.info(2) if self.paired() else None
        if d and d["power"] == "on":
            return "already"
        if d:
            self._code(224)                    # WAKEUP
            return "key"
        mac = config.tv(self.ip).get("mac")
        if not mac:
            raise RuntimeError("A TV não responde e o MAC é desconhecido")
        wol.wake(mac)
        wol.wake(mac, str(ipaddress.ip_network(discover.local_ip() + "/24", strict=False).broadcast_address))
        return "wol"

    def power_off(self):
        self._code(223)                        # SLEEP
