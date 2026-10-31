"""Samsung Tizen (2016+): websocket 8002 (teclas/texto), REST 8001 (info/apps),
UPnP 9197 (volume/mudo/mídia) e SDB 26101 (app de desenvolvedor)."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .. import catalog, config, discover, rest, upnp, wol
from ..remote import Remote
from .base import HOME_APP, Driver, readable


class Samsung(Driver):
    brand = "samsung"
    label = "Samsung"
    caps = frozenset({"keys", "text", "apps", "launch", "volume", "mute", "media", "power", "pair",
                      "ime", "cast", "browser", "events"})

    IDLE_S = 30          # sem resposta da TV há tanto tempo = conexão morta
    PING_S = 8

    def __init__(self, ip, emit=None):
        super().__init__(ip, emit)
        self.lock = threading.Lock()
        self.io_lock = threading.Lock()   # leitura e escrita no websocket, nunca ao mesmo tempo
        self.remote = None
        self.used = 0.0             # última vez que a TV respondeu (tecla ou pong)
        self.awake = False          # última leitura dizia "ligada"
        self.last_app = None        # app visível na última leitura
        self.pool = ThreadPoolExecutor(max_workers=12)
        threading.Thread(target=self._keepalive, daemon=True).start()

    @classmethod
    def probe(cls, ip, timeout=1.0):
        try:
            dv = discover.info(ip, timeout=timeout)["device"]
        except Exception:
            return None
        return {"brand": cls.brand, "name": dv.get("name"), "model": dv.get("modelName"),
                "power": dv.get("PowerState"), "mac": dv.get("wifiMac"), "os": dv.get("OS"), "raw": dv}

    def info(self, timeout=1.5):
        d = self.probe(self.ip, timeout)
        self.awake = bool(d) and d["power"] == "on"
        return d

    def paired(self):
        return bool(config.tv(self.ip).get("token"))

    def pair(self, timeout=40):
        # Bloqueia até alguém responder ao "Permitir?" na TV (ela desiste em ~30 s).
        Remote(self.ip, None, timeout=timeout).connect().close()

    # -- conexão websocket --------------------------------------------------------
    # Quando a TV desliga, a conexão morre sem avisar e o próximo envio esperava o
    # tempo-limite inteiro (30 s) — com o botão de ligar na fila atrás dele. Por
    # isso: tempo-limite curto, e conexão parada há mais de IDLE_S é refeita.
    def _keepalive(self):
        """Mantém o websocket aberto. Abrir custa 0,2–3,7 s (medido); derrubar
        por ociosidade fazia a 1ª tecla depois de uma pausa demorar isso tudo.
        Aqui só se ENVIA o ping: quem lê a resposta é o leitor da conexão, e
        sem nada lido por IDLE_S a conexão é dada como morta."""
        while True:
            time.sleep(self.PING_S)
            if not self.lock.acquire(blocking=False):
                continue
            try:
                r = self.remote
                if r is None:
                    # Pré-conecta quando a TV está ligada e pareada: o 1º toque já sai rápido.
                    if self.awake and self.paired():
                        try:
                            self._get()
                        except Exception:
                            self._drop()
                    continue
                if getattr(r, "dead", False) or time.time() - self.used > self.IDLE_S:
                    self._drop()
                    continue
                try:
                    with self.io_lock:
                        r.ws.ping()
                except Exception:
                    self._drop()
            finally:
                self.lock.release()

    def _reader(self, r):
        """Lê tudo o que a TV manda nesta conexão: pong (prova de vida) e
        eventos — o que importa é o teclado da TV abrindo (ms.remote.ime*)."""
        import websocket as _ws
        while self.remote is r:
            try:
                if not readable(r.ws.sock):
                    continue
                with self.io_lock:
                    op, frame = r.ws.recv_data_frame(True)
            except (_ws.WebSocketTimeoutException, TimeoutError):
                continue
            except Exception:
                r.dead = True
                return
            self.used = time.time()
            if op == _ws.ABNF.OPCODE_CLOSE:
                r.dead = True
                return
            if op != _ws.ABNF.OPCODE_TEXT:
                continue
            try:
                msg = json.loads(frame.data)
            except ValueError:
                continue
            ev = msg.get("event") or ""
            if ev.startswith("ms.remote.ime"):
                self.emit("ime", {"event": ev, "open": ev != "ms.remote.imeEnd", "data": msg.get("data")})
            elif ev not in ("ms.channel.ready", "ms.channel.clientConnect", "ms.channel.clientDisconnect"):
                print("evento da TV:", ev, str(msg.get("data"))[:200], flush=True)

    def _drop(self):
        if self.remote:
            try:
                self.remote.close()
            except Exception:
                pass
        self.remote = None

    def _get(self):
        if self.remote is not None and (getattr(self.remote, "dead", False) or time.time() - self.used > self.IDLE_S):
            self._drop()
        if self.remote is None:
            token = config.tv(self.ip).get("token")
            if not token:
                # Sem token a conexão abriria o "Permitir?" na TV a cada toque.
                raise RuntimeError("TV não pareada — teclas e texto pedem pareamento (toque no nome da TV)")
            self.remote = Remote(self.ip, token, timeout=6).connect()
            self.used = time.time()
            threading.Thread(target=self._reader, args=(self.remote,), daemon=True).start()
        return self.remote

    def run(self, fn):
        with self.lock:
            for attempt in (1, 2):
                try:
                    r = self._get()
                    with self.io_lock:
                        out = fn(r)
                    self.used = time.time()
                    return out
                except Exception:
                    self._drop()
                    if attempt == 2:
                        raise

    def close(self):
        with self.lock:
            self._drop()

    # -- controle --------------------------------------------------------------------
    def key(self, code):
        self.run(lambda r: r.key(code))

    def keys(self, seq, delay=0.4):
        self.run(lambda r: r.keys(seq, delay))

    def text(self, value):
        self.run(lambda r: r.text(value))

    def browser(self, url):
        self.run(lambda r: r.browser(url))

    def apps(self):
        # Lista vazia é a TV ocupada/abrindo app, não "nenhum app": tenta de novo.
        return catalog.installed(self.ip) or catalog.installed(self.ip)

    def launch(self, app_id):
        # REST abre direto (sem websocket); o app de desenvolvedor só abre pelo SDB.
        if app_id == HOME_APP:
            from ..sdb import Sdb
            out = Sdb(self.ip).shell("0 was_execute " + app_id, r"launched|spend time", 20)
            if "launched" not in out:
                raise RuntimeError("A TV não abriu o TV Tuner (Modo Desenvolvedor desligado?)")
            return
        try:
            rest.app_open(self.ip, app_id)
        except Exception:
            self.run(lambda r: r.launch(app_id))      # websocket como segunda via

    def current_app(self, installed):
        def visible(a):
            try:
                return a if rest.app_status(self.ip, a["appId"]).get("visible") else None
            except Exception:
                return None
        # O app que estava aberto quase sempre continua: 1 pedido à TV em vez de 8.
        if self.last_app and visible(self.last_app):
            return self.last_app
        hit = [a for a in self.pool.map(visible, installed()) if a]
        self.last_app = hit[0] if hit else None
        return self.last_app

    # -- áudio e mídia -----------------------------------------------------------------
    def volume(self):
        return upnp.volume(self.ip)

    def set_volume(self, level):
        return upnp.set_volume(self.ip, level)

    def muted(self):
        return upnp.muted(self.ip)

    def set_mute(self, on):
        upnp.set_mute(self.ip, on)

    def media(self):
        return upnp.transport(self.ip)

    def media_cmd(self, action):
        {"play": upnp.resume, "pause": upnp.pause, "stop": upnp.stop}[action](self.ip)

    def play_url(self, url, mime, title):
        upnp.play_url(self.ip, url, mime, title)

    def event_urls(self):
        return [(svc, f"http://{self.ip}:9197/upnp/event/{svc}") for svc in ("RenderingControl1", "AVTransport1")]

    # -- energia -----------------------------------------------------------------------
    def power_on(self):
        # Fora da fila dos toques e com conexão própria: ligar não espera ninguém.
        self.close()
        return wol.power_on(self.ip, config.tv(self.ip).get("mac"))

    def power_off(self):
        self.key("KEY_POWER")
