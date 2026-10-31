"""Painel web de controle: abre no celular (mesma rede) e manda tecla, app, URL, áudio.

Com --pin, toda chamada à API exige o cabeçalho X-Pin — sem ele, qualquer um na
rede controla a TV. A exceção é /media/<token>/…: quem busca é a TV (que não
manda PIN), e o token aleatório é a credencial.
"""
import json
import mimetypes
import os
import re
import secrets
import shutil
import ssl
import subprocess
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote

from . import config, discover, drivers, macros

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
MEDIA_DIR = os.path.join(tempfile.gettempdir(), "tvtuner-media")
TLS_DIR = os.path.join(config.DIR, "tls")
APP_ICONS = {}                  # (ip, appId) -> (bytes, content-type)
WHISPER_DIR = os.path.join(config.DIR, "whisper")


def addresses():
    """Endereços pelos quais o painel é aberto: IPv4 da rede local e da Tailscale
    (100.64/10), mais o nome MagicDNS. Todos entram no certificado — o navegador
    recusa HTTPS por um endereço que não esteja nele."""
    ips, names = {discover.local_ip()}, {"localhost"}
    # macOS tem ifconfig; Linux enxuto (Docker, Raspberry) às vezes só tem o ip.
    for cmd in (["ifconfig"], ["ip", "-4", "-o", "addr"]):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
        except Exception:
            continue
        ips |= {ip for ip in re.findall(r"inet (\d+\.\d+\.\d+\.\d+)", out) if not ip.startswith("127.")}
    ts = tailscale()
    if ts:
        ips |= set(ts["ips"])
        if ts.get("name"):
            names.add(ts["name"])
    return sorted(ips), sorted(names)


def tailscale():
    """IP e nome do Mac na Tailscale, se ela estiver instalada e conectada."""
    exe = shutil.which("tailscale") or "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    if not os.path.exists(exe):
        return None
    try:
        st = json.loads(subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=5).stdout)
    except Exception:
        return None
    me = st.get("Self") or {}
    ips = [ip for ip in me.get("TailscaleIPs") or [] if "." in ip]
    if st.get("BackendState") != "Running" or not ips:
        return None
    return {"ips": ips, "name": (me.get("DNSName") or "").rstrip(".") or None}


def ensure_cert(ips, names):
    """Certificado próprio para o HTTPS do painel.

    O navegador só libera o microfone em contexto seguro (HTTPS ou localhost);
    pelo IP da rede, em HTTP, não há gravação nem ditado. O certificado leva
    todos os endereços do Mac no SAN e é refeito quando eles mudam.
    """
    os.makedirs(TLS_DIR, exist_ok=True)
    cert, key, mark = (os.path.join(TLS_DIR, n) for n in ("cert.pem", "key.pem", "ip"))
    san = ",".join([f"IP:{ip}" for ip in ips] + [f"DNS:{n}" for n in names])
    try:
        if open(mark).read().strip() == san and os.path.isfile(cert) and os.path.isfile(key):
            return cert, key, False
    except OSError:
        pass
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "800",
        "-subj", "/CN=TV Tuner", "-keyout", key, "-out", cert,
        "-addext", f"subjectAltName={san}",
        "-addext", "extendedKeyUsage=serverAuth",
    ], check=True, capture_output=True)
    os.chmod(key, 0o600)
    with open(mark, "w") as f:
        f.write(san)
    return cert, key, True


# O launchd sobe o serviço com PATH mínimo (/usr/bin:/bin), sem o Homebrew.
for _d in ("/opt/homebrew/bin", "/usr/local/bin"):
    if _d not in os.environ.get("PATH", "").split(":"):
        os.environ["PATH"] = os.environ.get("PATH", "") + ":" + _d


def whisper_model():
    """Modelo do whisper.cpp para transcrever no Mac quando o navegador não transcreve sozinho."""
    if not shutil.which("whisper-cli") or not shutil.which("ffmpeg"):
        return None
    for d in (WHISPER_DIR, os.path.expanduser("~/.cache/whisper")):
        try:
            found = sorted(n for n in os.listdir(d) if n.startswith("ggml-") and n.endswith(".bin"))
        except OSError:
            continue
        if found:
            return os.path.join(d, found[-1])
    return None


WHISPER_PROMPT = ("Comandos para a TV em português: abrir Netflix, abrir YouTube, Globoplay, Disney+, "
                  "Prime Video, Spotify, Apple TV, Twitch. Volume 20. Mudo. Desligar. Buscar.")
WHISPER_PORT = 8791
_whisper = {"proc": None, "model": None}
_whisper_lock = threading.Lock()
ICON_DIR = os.path.join(config.DIR, "icons")
ICON_CDN = "https://cdn.jsdelivr.net/npm/simple-icons@11.15.0/icons/{}.svg"
VOICE_LOG = os.path.expanduser("~/Library/Logs/tvtuner-voice.log")


def _whisper_server(model):
    """whisper-server com o modelo carregado UMA vez: o whisper-cli recarrega
    o modelo a cada frase (~1–2 s só nisso). Saída vai pro log, não pra pipe
    — pipe sem leitor enche e congela o processo."""
    with _whisper_lock:
        p = _whisper["proc"]
        if p and p.poll() is None and _whisper["model"] == model:
            return True
        if p and p.poll() is None:
            p.terminate()
        if not shutil.which("whisper-server"):
            return False
        log = open(os.path.expanduser("~/Library/Logs/tvtuner-whisper.log"), "ab")
        _whisper["proc"] = subprocess.Popen(
            ["whisper-server", "-m", model, "-l", "pt", "--host", "127.0.0.1", "--port", str(WHISPER_PORT),
             "-nt", "-bs", "5", "-t", "6", "--prompt", WHISPER_PROMPT],
            stdout=log, stderr=log, stdin=subprocess.DEVNULL)
        _whisper["model"] = model
        for _ in range(120):                       # modelo grande leva uns segundos para subir
            if discover.port_open("127.0.0.1", WHISPER_PORT, timeout=0.2):
                return True
            if _whisper["proc"].poll() is not None:
                return False
            time.sleep(0.25)
        return False


def _wav(data, d):
    src, wav = os.path.join(d, "in"), os.path.join(d, "in.wav")
    with open(src, "wb") as f:
        f.write(data)
    # Silêncio vira "": com o prompt de comandos, o whisper "ouviria" um
    # "Abrir Netflix" no nada — e abriria a Netflix sozinho.
    vol = subprocess.run(["ffmpeg", "-hide_banner", "-i", src, "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True, timeout=60).stderr
    m = re.search(r"max_volume: (-?[\d.]+) dB", vol)
    if m and float(m.group(1)) < -42:
        return None
    # Normaliza o volume: microfone de celular longe da boca chega baixo.
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", src, "-af", "loudnorm",
                    "-ar", "16000", "-ac", "1", wav], check=True, capture_output=True, timeout=60)
    return wav


def transcribe(data):
    model = whisper_model()
    if not model:
        raise RuntimeError("Transcrição no Mac indisponível (falta whisper-cli ou modelo em " + WHISPER_DIR + ")")
    t0 = time.time()
    with tempfile.TemporaryDirectory() as d:
        wav = _wav(data, d)
        text = None if wav else ""
        if wav and _whisper_server(model):
            boundary = secrets.token_hex(8)
            with open(wav, "rb") as f:
                audio = f.read()
            body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\njson\r\n"
                    f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\n"
                    f"Content-Type: audio/wav\r\n\r\n").encode() + audio + f"\r\n--{boundary}--\r\n".encode()
            req = urllib.request.Request(f"http://127.0.0.1:{WHISPER_PORT}/inference", data=body,
                                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    text = json.load(r).get("text", "")
            except Exception:
                text = None
        if text is None and wav:
            out = subprocess.run(["whisper-cli", "-m", model, "-l", "pt", "-nt", "-np", "-f", wav, "--prompt", WHISPER_PROMPT],
                                 check=True, capture_output=True, text=True, timeout=120).stdout
            text = out
    text = " ".join(line.strip() for line in text.splitlines() if line.strip())
    try:
        with open(VOICE_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%F %T')}  {len(data)}B  {time.time() - t0:.1f}s  {text}\n")
    except OSError:
        pass
    return text


class TV:
    """Estado e cache de uma TV, sobre o driver da marca dela (drivers/).

    O driver fala com a TV; aqui fica o que o painel precisa por cima: cache
    curto do estado (vários celulares não multiplicam o custo), lista de apps
    e os valores que o UPnP empurra."""

    def __init__(self, ip, emit=None):
        self.ip = ip
        self.emit = emit or (lambda kind, data: None)
        self.drv = drivers.get(ip, self._from_driver)
        self.live = {}              # volume/mudo/mídia empurrados pelo UPnP (GENA)
        self.live_at = 0.0
        self.apps = None            # catálogo instalado, (quando, lista)
        self.state = (0.0, None)    # cache curto do /api/state
        self.pool = ThreadPoolExecutor(max_workers=6)

    def __getattr__(self, name):
        if name == "drv":
            raise AttributeError(name)
        # key, keys, text, browser, launch, power_on, power_off, set_volume...: direto no driver.
        return getattr(self.drv, name)

    def _from_driver(self, kind, data):
        # Volume/mudo empurrados pelo próprio driver (LG) entram no cache do "vivo".
        if kind == "live":
            self.live.update(data)
            self.live.setdefault("media", None)
            self.live_at = time.time()
            st = self.state[1]
            if st and st.get("power") == "on":
                st.update(data)
                self.emit("state", st)
            return
        self.emit(kind, data)

    def installed(self):
        # Lista vazia é a TV ocupada/abrindo app, não "nenhum app": não guarda.
        if not self.apps or time.time() - self.apps[0] > 300:
            found = self.drv.apps()
            self.apps = (time.time(), found) if found else None
            return found
        return self.apps[1]

    def snapshot(self):
        """Estado para o controle "vivo". Cache de 1 s."""
        at, st = self.state
        if st and time.time() - at < 1.0:
            return st
        drv = self.drv
        base = {"ip": self.ip, "brand": drv.brand, "caps": sorted(drv.caps), "paired": drv.paired()}
        try:
            d = drv.info(timeout=1.5)
        except Exception:
            d = None
        if not d:
            st = {"online": False, "power": "off", "name": config.tv(self.ip).get("name"), **base}
            self.state = (time.time(), st)
            return st
        st = {"online": True, "power": d["power"], "name": d["name"], "model": d["model"], **base}
        if st["power"] == "on":
            # Com assinatura UPnP viva, volume/mudo/mídia já chegam empurrados:
            # só o app aberto ainda precisa de pergunta.
            pushed = self.live if time.time() - self.live_at < 290 and {"volume", "mute", "media"} <= set(self.live) else None
            jobs = {"app": self.pool.submit(drv.current_app, self.installed)}
            if pushed:
                st.update(volume=pushed["volume"], mute=pushed["mute"], media=pushed["media"])
            else:
                jobs.update(volume=self.pool.submit(drv.volume), mute=self.pool.submit(drv.muted),
                            media=self.pool.submit(drv.media))
            for k, f in jobs.items():
                try:
                    st[k] = f.result(timeout=4)
                except Exception:
                    st[k] = None
            if st.get("app"):
                st["app"] = {"appId": st["app"]["appId"], "name": st["app"]["name"]}
        self.state = (time.time(), st)
        return st

    def invalidate(self):
        self.state = (0.0, None)


class Hub:
    """As TVs da casa. `tv.xxx` no handler fala com a escolhida — trocar de TV
    é trocar `ip`, sem reiniciar o painel."""

    def __init__(self, ip):
        self.tvs = {}
        self.ip = ip
        self.found = (0.0, [])
        self.subs = set()           # filas dos celulares conectados em /api/events
        self.subs_lock = threading.Lock()
        self.callback = None        # URL que a TV chama com os eventos UPnP (definida no serve)
        self.gena = {}              # serviço -> (sid, expira_em)
        self.last_pushed = None
        self.pairing = {}           # ip -> driver no meio de um pareamento com código
        threading.Thread(target=self._watch, daemon=True).start()

    def current(self):
        if self.ip not in self.tvs:
            ip = self.ip
            self.tvs[ip] = TV(ip, emit=lambda kind, data: self.publish(kind, data) if self.ip == ip else None)
        return self.tvs[self.ip]

    # Tempo real: o celular abre /api/events e recebe o estado quando ele MUDA,
    # em vez de perguntar a cada 3 s. Sem celular conectado, ninguém consulta a TV.
    def subscribe(self):
        import queue
        q = queue.Queue(maxsize=50)
        with self.subs_lock:
            self.subs.add(q)
        return q

    def unsubscribe(self, q):
        with self.subs_lock:
            self.subs.discard(q)

    def publish(self, kind, data):
        with self.subs_lock:
            subs = list(self.subs)
        for q in subs:
            try:
                q.put_nowait((kind, data))
            except Exception:
                pass

    def push_state(self, force=False):
        st = self.current().snapshot()
        key = json.dumps(st, sort_keys=True)
        if force or key != self.last_pushed:
            self.last_pushed = key
            self.publish("state", st)

    def _watch(self):
        """Com celular conectado: energia e app aberto a cada 1,5 s (volume e
        mudo chegam pelo UPnP), e mudança vira evento na hora."""
        while True:
            time.sleep(1.5)
            if not self.subs:
                continue
            try:
                tv = self.current()
                tv.invalidate()
                st = tv.snapshot()
                if st.get("power") == "on":
                    self._gena_keep()
                else:
                    self.gena = {}
                self.push_state()
            except Exception as e:
                print("vigia:", e, flush=True)

    def _gena_keep(self):
        """Assina (e renova) os eventos UPnP da TV: volume, mudo e mídia chegam
        sozinhos quando mudam — inclusive pelo controle físico."""
        if not self.callback:
            return
        for svc, url in self.current().drv.event_urls():
            sid, until = self.gena.get(svc, (None, 0))
            if time.time() < until - 60:
                continue
            hdr = {"SID": sid, "TIMEOUT": "Second-300"} if sid else \
                  {"CALLBACK": f"<{self.callback}/{self.ip}>", "NT": "upnp:event", "TIMEOUT": "Second-300"}
            try:
                with urllib.request.urlopen(urllib.request.Request(url, method="SUBSCRIBE", headers=hdr), timeout=4) as r:
                    secs = int(re.sub(r"\D", "", r.headers.get("TIMEOUT") or "") or 300)
                    self.gena[svc] = (r.headers.get("SID") or sid, time.time() + secs)
            except Exception:
                self.gena.pop(svc, None)        # renovação recusada: assina do zero na próxima volta

    def upnp_event(self, ip, body):
        import html as _html
        tv = self.tvs.get(ip)
        if not tv:
            return
        m = re.search(r"<LastChange>(.*?)</LastChange>", body, re.S)
        xml = _html.unescape(m.group(1)) if m else body
        got = dict(re.findall(r'<(Volume|Mute|TransportState)\s+(?:channel="Master"\s+)?val="([^"]*)"', xml))
        if "Volume" in got:
            tv.live["volume"] = int(got["Volume"])
        if "Mute" in got:
            tv.live["mute"] = got["Mute"] == "1"
        if "TransportState" in got:
            tv.live["media"] = got["TransportState"]
        if got:
            tv.live_at = time.time()
            st = tv.state[1]
            if st and st.get("power") == "on":
                st.update({k: tv.live[k] for k in ("volume", "mute", "media") if k in tv.live})
                if ip == self.ip:
                    self.push_state()

    def __getattr__(self, name):
        return getattr(self.current(), name)

    def listing(self, rescan=False):
        """Salvas + achadas na rede, com estado de cada uma."""
        if rescan or time.time() - self.found[0] > 120:
            self.found = (time.time(), drivers.scan())
        saved = config.tvs()
        rows = {}
        for ip, d in self.found[1]:
            rows[ip] = {"ip": ip, "brand": d["brand"], "name": d["name"], "model": d["model"],
                        "power": d["power"], "online": True}
        def probe(ip):
            d = drivers.probe(ip, saved[ip].get("brand"), timeout=1.2)
            return ip, {"power": d["power"], "online": True} if d else {"power": "off", "online": False}
        with ThreadPoolExecutor(max_workers=8) as ex:
            live = dict(ex.map(probe, [ip for ip in saved if ip not in rows]))
        for ip, v in saved.items():
            row = rows.setdefault(ip, {"ip": ip, "brand": v.get("brand"), "name": v.get("name"),
                                       "model": v.get("model"), **live.get(ip, {})})
            row["saved"] = True
        for ip, row in rows.items():
            needs = "pair" in drivers.DRIVERS.get(row.get("brand") or "samsung", drivers.Driver).caps
            row["paired"] = not needs or bool(saved.get(ip, {}).get("token"))
            row["current"] = ip == self.ip
        return sorted(rows.values(), key=lambda r: (not r["current"], not r.get("saved"), r["ip"]))

    def _learn(self, ip):
        d = drivers.probe(ip, config.tv(ip).get("brand"), timeout=2)
        if d:
            config.remember(ip, brand=d["brand"], name=d["name"], model=d["model"], mac=d["mac"])

    def select(self, ip):
        self._learn(ip)
        config.select(ip)
        self.ip = ip

    def pair(self, ip, code=None):
        """Samsung/LG: bloqueia até "Permitir" na TV. Android TV: 1ª chamada faz a
        TV mostrar um código (devolve {"code": True}); a 2ª traz o código digitado."""
        self._learn(ip)
        t = self.tvs.get(ip)
        drv = t.drv if t else self.pairing.setdefault(ip, drivers.get(ip))
        if getattr(drv, "pair_with_code", False):
            if code is None:
                drv.pair_start()
                return {"code": True}
            drv.pair_finish(code)
        else:
            drv.pair()
        self.pairing.pop(ip, None)
        if t:
            t.invalidate()

    def forget(self, ip):
        config.forget(ip)
        t = self.tvs.pop(ip, None)
        if t:
            t.drv.close()


def make_handler(tv, pin, port):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _json(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self):
            if not pin or self.headers.get("X-Pin") == pin:
                return True
            # EventSource não manda cabeçalho: o PIN vem na URL.
            m = re.search(r"[?&]pin=([^&]*)", self.path)
            return bool(m) and unquote(m.group(1)) == pin

        def do_NOTIFY(self):
            # Eventos UPnP da TV (volume/mudo/mídia). Só aceita das TVs conhecidas.
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n).decode("utf-8", "replace")
            ip = self.path.rsplit("/", 1)[-1]
            if self.path.startswith("/upnp/event/") and self.client_address[0] == ip:
                tv.upnp_event(ip, body)
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _events(self):
            q = tv.subscribe()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.close_connection = True
            import queue
            try:
                self.wfile.write(f"event: state\ndata: {json.dumps(tv.snapshot(), ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
                while True:
                    try:
                        kind, data = q.get(timeout=15)
                        chunk = f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                    except queue.Empty:
                        chunk = ": vivo\n\n"          # mantém a conexão e detecta celular que sumiu
                    self.wfile.write(chunk.encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                tv.unsubscribe(q)

        def _media(self):
            # /media/<token>/<nome>: a TV pede por faixas (Range) ao tocar.
            m = re.match(r"^/media/([A-Za-z0-9_-]{16,})/", self.path)
            path = os.path.join(MEDIA_DIR, m.group(1)) if m else None
            if not path or not os.path.isfile(path):
                return self._json(404, {"error": "not found"})
            size = os.path.getsize(path)
            start, end = 0, size - 1
            rng = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
            if rng and (rng.group(1) or rng.group(2)):
                if rng.group(1):
                    start = int(rng.group(1))
                    end = int(rng.group(2)) if rng.group(2) else end
                else:
                    start = size - int(rng.group(2))
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            mime = mimetypes.guess_type(unquote(self.path))[0] or "application/octet-stream"
            self.send_header("Content-Type", mime)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()
            if self.command == "HEAD":
                return
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(65536, left))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    left -= len(chunk)

        def _icon(self):
            # /icons/<slug>.svg: logo da marca (simple-icons), baixado uma vez e guardado.
            # Sem PIN: é <img>/máscara CSS, que não manda cabeçalho; só serve logo público.
            m = re.match(r"^/icons/([a-z0-9]{2,40})\.svg$", self.path)
            if not m:
                return self._json(404, {"error": "not found"})
            path = os.path.join(ICON_DIR, m.group(1) + ".svg")
            if not os.path.isfile(path):
                try:
                    with urllib.request.urlopen(ICON_CDN.format(m.group(1)), timeout=6) as r:
                        data = r.read()
                except Exception:
                    return self._json(404, {"error": "sem ícone"})
                os.makedirs(ICON_DIR, exist_ok=True)
                with open(path, "wb") as f:
                    f.write(data)
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=2592000")
            self.end_headers()
            self.wfile.write(body)

        def _appicon(self):
            # /appicon/<id>: ícone que a própria TV entrega (Roku). Sem PIN pelo mesmo
            # motivo do /icons/; guardado em memória, por TV.
            m = re.match(r"^/appicon/([A-Za-z0-9._-]{1,64})$", self.path)
            cur = tv.current()
            if not m or "icons" not in cur.drv.caps:
                return self._json(404, {"error": "not found"})
            key = (cur.ip, m.group(1))
            if key not in APP_ICONS:
                try:
                    APP_ICONS[key] = cur.drv.icon(m.group(1))
                except Exception:
                    return self._json(404, {"error": "sem ícone"})
            body, ctype = APP_ICONS[key]
            self.send_response(200)
            self.send_header("Content-Type", ctype or "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=86400")
            self.end_headers()
            self.wfile.write(body)

        def do_HEAD(self):
            if self.path.startswith("/media/"):
                return self._media()
            self._json(404, {"error": "not found"})

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                with open(os.path.join(STATIC, "index.html"), "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path.startswith("/media/"):
                return self._media()
            if self.path.startswith("/icons/"):
                return self._icon()
            if self.path.startswith("/appicon/"):
                return self._appicon()
            if not self._authorized():
                return self._json(401, {"error": "pin"})
            if self.path.startswith("/api/events"):
                return self._events()
            try:
                if self.path in ("/api/state", "/api/status"):
                    return self._json(200, tv.snapshot())
                if self.path == "/api/apps":
                    return self._json(200, tv.installed())
                if self.path == "/api/macros":
                    return self._json(200, sorted(macros.load()))
                if self.path == "/api/voice":
                    return self._json(200, {"server": bool(whisper_model())})
                if self.path == "/api/access":
                    ts = tailscale()
                    return self._json(200, {"tailscale": ts and f"https://{ts['ips'][0]}:{self.server.server_address[1]}"})
                if self.path == "/api/tvs":
                    return self._json(200, tv.listing())
                if self.path == "/api/doctor":
                    from . import doctor
                    return self._json(200, doctor.run(tv.ip))
            except Exception as e:
                return self._json(502, {"error": str(e)})
            self._json(404, {"error": "not found"})

        def _upload(self):
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                raise ValueError("arquivo vazio")
            name = unquote(self.headers.get("X-Name") or "audio")
            os.makedirs(MEDIA_DIR, exist_ok=True)
            token = secrets.token_urlsafe(18)
            with open(os.path.join(MEDIA_DIR, token), "wb") as f:
                left = n
                while left > 0:
                    chunk = self.rfile.read(min(65536, left))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            mime = self.headers.get("Content-Type") or mimetypes.guess_type(name)[0] or "audio/mpeg"
            url = f"http://{discover.local_ip()}:{port}/media/{token}/{quote(name)}"
            tv.play_url(url, mime, os.path.splitext(name)[0])

        def do_POST(self):
            if not self._authorized():
                return self._json(401, {"error": "pin"})
            try:
                if self.path == "/api/voice":
                    n = int(self.headers.get("Content-Length") or 0)
                    return self._json(200, {"text": transcribe(self.rfile.read(n))})
                if self.path == "/api/media/upload":
                    self._upload()
                    tv.invalidate()
                    return self._json(200, {"ok": True})
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.path == "/api/key":
                    tv.key(body["key"])
                elif self.path == "/api/macro":
                    seq = macros.load()[body["name"]]
                    tv.keys(seq)
                elif self.path == "/api/launch":
                    tv.launch(body["appId"])
                elif self.path == "/api/text":
                    tv.text(body["text"])
                elif self.path == "/api/browser":
                    tv.browser(body["url"])
                elif self.path == "/api/tvs/scan":
                    return self._json(200, tv.listing(rescan=True))
                elif self.path == "/api/tvs/select":
                    tv.select(body["ip"])
                elif self.path == "/api/tvs/pair":
                    out = tv.pair(body["ip"], body.get("code"))
                    if out:
                        return self._json(200, out)
                elif self.path == "/api/doctor/fix":
                    from . import doctor
                    return self._json(200, {"done": doctor.fix(tv.ip, doctor.run(tv.ip))})
                elif self.path == "/api/tvs/forget":
                    tv.forget(body["ip"])
                elif self.path == "/api/wake":
                    how = tv.power_on()
                    tv.invalidate()
                    return self._json(200, {"ok": True, "how": how})
                elif self.path == "/api/volume":
                    level = tv.set_volume(body["level"])
                    tv.invalidate()
                    return self._json(200, {"ok": True, "volume": level})
                elif self.path == "/api/mute":
                    tv.set_mute(bool(body["on"]))
                elif self.path == "/api/media/url":
                    url = body["url"]
                    tv.play_url(url, body.get("mime") or mimetypes.guess_type(url)[0] or "audio/mpeg",
                                body.get("title") or "TV Tuner")
                elif self.path in ("/api/media/pause", "/api/media/play", "/api/media/stop"):
                    tv.media_cmd(self.path.rsplit("/", 1)[1])
                else:
                    return self._json(404, {"error": "not found"})
                tv.invalidate()
                self._json(200, {"ok": True})
                threading.Timer(0.6, lambda: tv.subs and tv.push_state()).start()
            except Exception as e:
                self._json(502, {"error": str(e)})

    return Handler


def serve(ip, host="0.0.0.0", port=8780, pin=None, tls_port=8743):
    tv = Hub(ip)
    tv.callback = f"http://{discover.local_ip()}:{port}/upnp/event"
    handler = make_handler(tv, pin, port)
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    shown = discover.local_ip() if host == "0.0.0.0" else host
    print(f"Painel em http://{shown}:{port}  (Ctrl+C para parar)" + ("  — PIN ligado" if pin else ""), flush=True)
    # Sobe o transcritor já: a 1ª frase não paga os ~3 s de carregar o modelo.
    if whisper_model():
        threading.Thread(target=lambda: _whisper_server(whisper_model()), daemon=True).start()
    if tls_port:
        # Mesmo painel em HTTPS: é o endereço com microfone. A mídia segue no HTTP,
        # porque quem busca é a TV e ela não confia no nosso certificado.
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cert, key, _ = ensure_cert(*addresses())
        ctx.load_cert_chain(cert, key)

        def watch_addresses():
            # A Tailscale pode conectar depois do painel subir: certificado novo,
            # carregado no mesmo contexto (vale para as próximas conexões).
            while True:
                time.sleep(60)
                try:
                    c, k, changed = ensure_cert(*addresses())
                    if changed:
                        ctx.load_cert_chain(c, k)
                        print("Certificado refeito para:", ", ".join(addresses()[0]), flush=True)
                except Exception as e:
                    print("Falha ao refazer certificado:", e, flush=True)
        threading.Thread(target=watch_addresses, daemon=True).start()
        tls = ThreadingHTTPServer((host, tls_port), handler)
        tls.daemon_threads = True
        tls.socket = ctx.wrap_socket(tls.socket, server_side=True)
        threading.Thread(target=tls.serve_forever, daemon=True).start()
        print(f"Com microfone: https://{shown}:{tls_port}  (aceite o certificado uma vez)", flush=True)
        ts = tailscale()
        if ts:
            print(f"Fora de casa (Tailscale): https://{ts['ips'][0]}:{tls_port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
