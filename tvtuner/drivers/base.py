"""O que toda TV sabe fazer, do jeito do painel e da CLI.

Cada marca implementa o que o protocolo dela permite e declara em `caps`; o
celular esconde o que a TV não tem. Teclas usam os nomes da Samsung (KEY_UP,
KEY_ENTER...) como idioma comum — cada driver traduz para o seu.
"""
import time

HOME_APP = "TvTunerHom.TvTuner"     # botão "Início" do painel; quem não tem o app Tizen manda Home

# keys: setas/OK/voltar · text: digitar · apps/launch: listar e abrir · volume: ler e ajustar o número
# mute: ligar/desligar o mudo · media: tocar/pausar · power: ligar/desligar · pair: precisa parear
# ime: avisa quando o teclado da TV abre · cast: tocar um arquivo/URL · browser: abrir site
# icons: a TV entrega os ícones dos apps · events: empurra volume/mudo (UPnP)
ALL_CAPS = ("keys", "text", "apps", "launch", "volume", "mute", "media", "power", "pair",
            "ime", "cast", "browser", "icons", "events")


class Unsupported(RuntimeError):
    pass


class Driver:
    brand = ""
    label = ""
    caps = frozenset()

    def __init__(self, ip, emit=None):
        self.ip = ip
        self.emit = emit or (lambda kind, data: None)

    # -- quem é -------------------------------------------------------------
    @classmethod
    def probe(cls, ip, timeout=1.0):
        """{brand, name, model, power: on|standby, mac, os} ou None se não for desta marca."""
        raise NotImplementedError

    def info(self, timeout=1.5):
        return self.probe(self.ip, timeout)

    def paired(self):
        return True

    def pair(self, timeout=40):
        pass

    # -- controle -------------------------------------------------------------
    def key(self, code):
        raise Unsupported(f"{self.label}: teclas não suportadas")

    def keys(self, seq, delay=0.4):
        # Número na sequência é pausa em segundos (formato das macros).
        for c in seq:
            if isinstance(c, (int, float)):
                time.sleep(c)
                continue
            self.key(c)
            time.sleep(delay)

    def text(self, value):
        raise Unsupported(f"{self.label}: digitar não é suportado")

    def browser(self, url):
        raise Unsupported(f"{self.label}: abrir site não é suportado")

    def apps(self):
        return []

    def launch(self, app_id):
        raise Unsupported(f"{self.label}: abrir app não é suportado")

    def current_app(self, installed):
        """App visível. `installed()` devolve a lista (com cache do painel)."""
        return None

    def icon(self, app_id):
        """(bytes, content-type) do ícone do app, para quem tem "icons"."""
        raise Unsupported("sem ícone")

    # -- áudio e mídia ----------------------------------------------------------
    def volume(self):
        return None

    def set_volume(self, level):
        raise Unsupported(f"{self.label} não informa o volume: use + e −")

    def muted(self):
        return None

    def set_mute(self, on):
        raise Unsupported(f"{self.label}: mudo não suportado")

    def media(self):
        """PLAYING, PAUSED_PLAYBACK, STOPPED, NO_MEDIA_PRESENT ou None."""
        return None

    def media_cmd(self, action):
        raise Unsupported(f"{self.label}: controle de mídia não suportado")

    def play_url(self, url, mime, title):
        raise Unsupported(f"{self.label}: tocar arquivo não é suportado")

    # Serviços UPnP que avisam mudança de volume/mudo: [(nome, url)].
    def event_urls(self):
        return []

    # -- energia ----------------------------------------------------------------
    def power_on(self):
        """Liga. Devolve como: "already", "key" ou "wol"."""
        raise Unsupported(f"{self.label}: ligar pela rede não é suportado")

    def power_off(self):
        self.key("KEY_POWER")

    def close(self):
        pass
