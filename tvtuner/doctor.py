"""Diagnóstico da TV e do que roda em volta dela: `tv doctor` e o painel.

Cada verificação devolve um achado com estado (ok, aviso, falha, info), o que
foi medido e — quando há — o que fazer. Só lê; quem corrige é `--fix` (o que
é seguro fazer sozinho) ou a pessoa, no menu da TV.
"""
import datetime
import os
import re
import shutil
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor

from . import catalog, config, discover, upnp

OK, WARN, FAIL, INFO = "ok", "aviso", "falha", "info"
TIZEN_DATA = os.path.expanduser("~/tizen-studio-data")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# O que a TV não deixa ler pela rede: fica como lembrete, não como "ok".
MANUAL = [
    ("Atualização de software", "Configurações > Suporte > Atualização de software > Atualização automática: ligada"),
    ("Ligar pelo celular", "Configurações > Geral > Rede > Configurações especializadas > Ligar com dispositivo móvel: ligado "
                           "(sem isso, desligada de verdade a TV só liga pelo controle)"),
    ("Smart Hub automático", "Configurações > Geral > Recursos inteligentes > Executar Smart Hub automaticamente: desligado"),
    ("Último app", "Configurações > Geral > Recursos inteligentes > Executar último app automaticamente: ligado"),
    ("Anúncios e rastreio", "Configurações > Suporte > Termos e Privacidade: desligar Serviços de anúncios por interesse e "
                            "Serviço de informações de visualização"),
    ("Modo de imagem", "Configurações > Imagem > Modo de imagem: Filme (o Padrão/Dinâmico exagera cor e brilho)"),
    ("Movimento", "Configurações > Imagem > Configurações especializadas > Auto Motion Plus: desligado para filmes"),
    ("Economia de energia", "Configurações > Geral > Solução Ecológica > Detecção de luz ambiente: a gosto (pode escurecer a imagem)"),
]


def _f(status, title, detail, fix=None, key=None):
    return {"status": status, "title": title, "detail": detail, "fix": fix, "key": key}


def _ping(ip, n=10):
    try:
        out = subprocess.run(["ping", "-c", str(n), "-i", "0.2", "-t", "6", ip],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None
    loss = re.search(r"([\d.]+)% packet loss", out)
    rtt = re.search(r"= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms", out)
    return {"loss": float(loss.group(1)) if loss else 100.0,
            "avg": float(rtt.group(2)) if rtt else None, "max": float(rtt.group(3)) if rtt else None,
            "jitter": float(rtt.group(4)) if rtt else None}


def _cert_expiry(p12, pwd_file):
    try:
        pem = subprocess.run(["openssl", "pkcs12", "-in", p12, "-nokeys", "-legacy", "-passin", "file:" + pwd_file],
                             capture_output=True, timeout=10).stdout
        out = subprocess.run(["openssl", "x509", "-noout", "-enddate"], input=pem,
                             capture_output=True, timeout=10).stdout.decode()
        m = re.search(r"notAfter=(.+)", out)
        return datetime.datetime.strptime(m.group(1).strip(), "%b %d %H:%M:%S %Y %Z") if m else None
    except Exception:
        return None


def check_network(ip, dev):
    out = []
    p = _ping(ip)
    if not p or p["avg"] is None:
        out.append(_f(WARN, "Rede", "A TV não responde a ping (algumas bloqueiam ICMP)."))
    else:
        detail = f"latência média {p['avg']:.0f} ms, pico {p['max']:.0f} ms, perda {p['loss']:.0f}%"
        bad = p["loss"] > 0 or p["avg"] > 30 or p["max"] > 120
        wifi = (dev or {}).get("networkType") == "wireless"
        out.append(_f(WARN if bad else OK, "Rede" + (" (Wi-Fi)" if wifi else " (cabo)"), detail,
                      ("Wi-Fi instável deixa controle e streaming lentos: aproxime o roteador, use 5 GHz "
                       "ou ligue a TV por cabo.") if bad else None))
    if dev and dev.get("networkType") == "wireless":
        out.append(_f(INFO, "Ligar pela rede", "No Wi-Fi o Wake-on-LAN não funciona (medido); em espera o painel liga "
                      "pela tecla de energia.", "Para ligar mesmo desligada de verdade: cabo de rede, ou ative "
                      "\"Ligar com dispositivo móvel\" (ver lista manual)."))
    cfg = config.tv(ip)
    if cfg.get("mac") and dev and dev.get("wifiMac") and cfg["mac"].upper() != dev["wifiMac"].upper():
        out.append(_f(WARN, "Endereço", f"MAC salvo {cfg['mac']} difere do atual {dev['wifiMac']}.", "Rode: tv use " + ip))
    out.append(_f(INFO, "IP fixo", f"A TV está em {ip}. Se o roteador trocar o IP, painel e app param de achá-la.",
                  f"No roteador, reserve {ip} para o MAC {(dev or {}).get('wifiMac') or cfg.get('mac') or '?'} (DHCP estático)."))
    return out


def check_tv(ip):
    out, dev = [], None
    try:
        dev = discover.info(ip, timeout=3)["device"]
    except Exception:
        out.append(_f(FAIL, "TV", f"Não responde em {ip}.", "Ligue a TV (ou tire da tomada e recoloque) e confira se "
                      "está na mesma rede. Se o IP mudou: tv discover."))
        return out, None
    power = dev.get("PowerState")
    out.append(_f(OK, "TV", f"{dev.get('modelName')} · Tizen · {('ligada' if power == 'on' else 'em espera')} · "
                  f"{dev.get('resolution', '?')}"))
    ports = {8001: "API", 8002: "controle (TLS)", 9197: "áudio/UPnP", 26101: "SDB (desenvolvedor)"}
    with ThreadPoolExecutor(max_workers=4) as ex:
        opened = dict(zip(ports, ex.map(lambda p: discover.port_open(ip, p, 1.5), ports)))
    closed = [f"{p} {what}" for p, what in ports.items() if not opened[p]]
    if power == "on":
        out.append(_f(OK if not closed else WARN, "Portas", "todas abertas" if not closed else "fechadas: " + ", ".join(closed),
                      None if not closed else "Porta fechada com a TV ligada costuma ser rede/roteador isolando "
                      "aparelhos (isolamento de clientes no Wi-Fi)."))
    return out, dev


def check_pairing(ip, dev):
    token = config.tv(ip).get("token")
    if not token:
        return [_f(WARN, "Pareamento", "Sem token: setas, texto e voz não funcionam.", "Painel > nome da TV > Parear, ou: tv pair",
                   key="pair")]
    if not dev or dev.get("PowerState") != "on":
        return [_f(INFO, "Pareamento", "Token salvo (só dá para testar com a TV ligada).")]
    from .remote import Remote, PairingError
    t = time.time()
    try:
        r = Remote(ip, token, timeout=8).connect()
        r.close()
    except PairingError:
        return [_f(FAIL, "Pareamento", "A TV recusou o token salvo.", "Pareie de novo (a TV pode ter esquecido o "
                   "dispositivo).", key="pair")]
    except Exception as e:
        return [_f(WARN, "Pareamento", f"Não conectou: {e}")]
    ms = (time.time() - t) * 1000
    return [_f(OK if ms < 2500 else WARN, "Pareamento", f"token aceito, conexão em {ms:.0f} ms",
               None if ms < 2500 else "Conexão lenta: sinal Wi-Fi fraco ou TV sobrecarregada (o painel mantém a conexão aberta, então só pesa na 1ª tecla).")]


def check_developer(ip, dev):
    out = []
    if not dev:
        return out
    me = discover.local_ip()
    if dev.get("developerMode") != "1":
        out.append(_f(WARN, "Modo Desenvolvedor", "Desligado: o app TV Tuner não pode ser instalado nem aberto pelo painel.",
                      "Apps > digite 12345 no controle > Developer mode ON > IP " + me + " > reiniciar a TV."))
    elif dev.get("developerIP") != me:
        out.append(_f(FAIL, "Modo Desenvolvedor", f"Aponta para {dev.get('developerIP')}, mas este Mac é {me}: "
                      "instalar/abrir o app falha.", f"Apps > 12345 > troque o Host PC IP para {me} > reiniciar a TV.",
                      key="devip"))
    else:
        out.append(_f(OK, "Modo Desenvolvedor", f"ligado, apontando para este Mac ({me})"))
    return out


def check_app(ip):
    out = []
    p12 = os.path.join(TIZEN_DATA, "keystore", "author", "tvtuner.p12")
    pwd = os.path.join(TIZEN_DATA, "keystore", "author", "tvtuner.pwd")
    if not os.path.exists(pwd):
        pwd = os.path.join(config.DIR, "cert-pass")
    if os.path.exists(p12):
        end = _cert_expiry(p12, pwd)
        if end:
            days = (end - datetime.datetime.utcnow()).days
            st = FAIL if days < 0 else WARN if days < 90 else OK
            out.append(_f(st, "Certificado do app", f"vence em {end:%d/%m/%Y} ({days} dias)",
                          None if st == OK else "Gere um certificado novo (Tizen Studio > Certificate Manager, "
                          "validade longa) e reinstale: tv app install. App assinado com certificado vencido não "
                          "reinstala.", key="cert"))
    else:
        out.append(_f(INFO, "Certificado do app", "Perfil de assinatura não encontrado neste Mac."))
    wgt = os.path.join(ROOT, "tizen", "TVTuner.wgt")
    src = [os.path.join(ROOT, "tizen", n) for n in ("index.html", "app.js", "style.css", "config.xml")]
    if os.path.exists(wgt):
        stale = [os.path.basename(s) for s in src if os.path.exists(s) and os.path.getmtime(s) > os.path.getmtime(wgt)]
        if stale:
            out.append(_f(WARN, "App TV Tuner", "Há mudanças no código que não estão no pacote instalado: " + ", ".join(stale),
                          "tv doctor --fix (recompila, assina e reinstala)", key="app"))
        else:
            out.append(_f(OK, "App TV Tuner", "pacote em dia com o código"))
    return out


def check_apps(ip, dev):
    if not dev or dev.get("PowerState") != "on":
        return []
    found = {a["name"] for a in catalog.installed(ip)}
    want = {"Netflix", "YouTube", "Globoplay", "Disney+", "Prime Video", "Max"}
    missing = sorted(w for w in want if not any(w.lower() in f.lower() for f in found))
    out = [_f(OK, "Apps", f"{len(found)} apps conhecidos instalados")]
    if missing:
        out.append(_f(INFO, "Apps", "Não instalados: " + ", ".join(missing), "Instale pela loja da TV (Apps) se usar."))
    return out


def check_audio(ip, dev):
    if not dev or dev.get("PowerState") != "on":
        return []
    try:
        v, m = upnp.volume(ip), upnp.muted(ip)
    except Exception as e:
        return [_f(WARN, "Áudio", f"UPnP não respondeu: {e}")]
    if m:
        return [_f(INFO, "Áudio", f"Mudo ligado (volume {v}).")]
    if v > 60:
        return [_f(INFO, "Áudio", f"Volume em {v} — alto para uma TV de 50\".")]
    return [_f(OK, "Áudio", f"volume {v}")]


def check_ads(path=None):
    """O bloqueio vale se o DNS da rede (o mesmo que a TV usa) não resolve os domínios."""
    path = path or os.path.join(ROOT, "dns", "blocklist.txt")
    try:
        doms = [l.split()[-1] for l in open(path) if l.strip() and not l.startswith("#")]
    except OSError:
        return []

    def resolves(d):
        try:
            ips = {a[4][0] for a in socket.getaddrinfo(d, 443)}
        except OSError:
            return False
        return not ips <= {"0.0.0.0", "127.0.0.1", "::", "::1"}
    with ThreadPoolExecutor(max_workers=8) as ex:
        live = [d for d, ok in zip(doms, ex.map(resolves, doms)) if ok]
    if not live:
        return [_f(OK, "Anúncios/rastreio", f"{len(doms)} domínios da Samsung bloqueados no DNS da rede")]
    return [_f(WARN, "Anúncios/rastreio", f"{len(live)} de {len(doms)} domínios de anúncio/rastreio liberados "
               f"(ex.: {live[0]})", "Bloqueie no roteador/Pi-hole/NextDNS a lista dns/blocklist.txt "
               "(ou blocklist-agressivo.txt). Vale para a casa toda.")]


def check_panel():
    out = []
    up = discover.port_open("127.0.0.1", 8780, 1)
    tls = discover.port_open("127.0.0.1", 8743, 1)
    if up:
        out.append(_f(OK if tls else WARN, "Painel", "rodando" + (" (HTTP + HTTPS)" if tls else " — sem HTTPS: microfone não funciona"),
                      None if tls else "Reinicie: tv service install"))
    else:
        out.append(_f(WARN, "Painel", "não está rodando", "tv service install (sobe sozinho com o Mac)"))
    from .panel import whisper_model
    out.append(_f(OK if whisper_model() else INFO, "Voz", "transcrição no Mac pronta" if whisper_model()
                  else "sem whisper/modelo: voz depende do navegador"))
    return out


MANUAL_ROKU = [
    ("Controle pela rede", "Configurações > Sistema > Configurações avançadas do sistema > Controle por apps móveis > "
                           "Acesso à rede: Padrão (Limitado recusa teclas e apps)"),
    ("Ligar pela rede", "Configurações > Sistema > Energia > Início rápido da TV: ligado (sem isso a TV dorme fora da rede e só o Wake-on-LAN acorda)"),
    ("Anúncios e rastreio", "Configurações > Privacidade > Publicidade > Limitar rastreamento de anúncios: ligado"),
    ("Reconhecimento de conteúdo", "Configurações > Privacidade > Experiência da Smart TV > Usar informações das "
                                   "entradas da TV: desligado"),
    ("Atualização", "Configurações > Sistema > Atualização do sistema: a Roku atualiza sozinha; confira a versão"),
]


MANUAL_LG = [
    ("Ligar pela rede", "Configurações > Geral > Dispositivos > Dispositivos externos > Ligar a TV pelo celular "
                        "(Mobile TV On / Ligar via Wi-Fi): ligado — o nome muda com o ano do webOS"),
    ("Início rápido", "Configurações > Geral > Dispositivos/Sistema > Quick Start+: ligado (mantém a rede em espera e o "
                      "painel liga a TV na hora)"),
    ("Reconhecimento de conteúdo", "Configurações > Geral > Sistema > Informações adicionais > Live Plus: desligado"),
    ("Anúncios e rastreio", "Configurações > Geral > Sistema > Informações adicionais > Contratos de usuário: recusar "
                            "anúncios por interesse e recomendações"),
    ("Modo de imagem", "Configurações > Imagem > Modo de imagem: Cinema ou Filmmaker (o Vívido exagera cor e brilho)"),
]
MANUAL_ANDROIDTV = [
    ("Controle pela rede", "Vem ligado (Android TV Remote Service). Se o pareamento falhar sempre: Configurações > Apps > "
                           "Apps do sistema > Android TV Remote Service > Limpar dados"),
    ("Ligar pela rede", "Configurações > Sistema > Energia: ligar \"Wake on LAN\" / \"Ligar pela rede\" (nome varia por fabricante)"),
    ("Anúncios", "Configurações > Privacidade > Anúncios: excluir o ID de publicidade e desligar a personalização"),
    ("Tela inicial", "Configurações > Apps > Google TV: modo só apps reduz as recomendações patrocinadas"),
]
MANUAL_BY_BRAND = {"lg": MANUAL_LG, "roku": MANUAL_ROKU, "androidtv": MANUAL_ANDROIDTV}


def check_generic(ip):
    from . import drivers
    drv = drivers.get(ip)
    try:
        d = drv.info(timeout=3)
    except Exception:
        d = None
    if not d:
        return [_f(FAIL, "TV", f"Não responde em {ip}.", "Ligue a TV e confira se está na mesma rede. "
                   "Se o IP mudou: tv discover.")], None
    out = [_f(OK, "TV", f"{d['model']} · {d['os']} · {'ligada' if d['power'] == 'on' else 'em espera'}")]
    try:
        drv.current_app(drv.apps)
        out.append(_f(OK, "Controle", "a TV aceita comandos pela rede"))
    except Exception as e:
        out.append(_f(FAIL, "Controle", str(e), "Toque no nome da TV e em Parear; aceite na TV." if "pareada" in str(e)
                      else None))
    return out, d


def run(ip):
    from . import drivers
    brand = drivers.brand_of(ip)
    if brand != "samsung":
        out, d = check_generic(ip)
        with ThreadPoolExecutor(max_workers=3) as ex:
            for part in ex.map(lambda f: f(), [lambda: check_network(ip, None), check_panel]):
                out += part
        order = {FAIL: 0, WARN: 1, INFO: 2, OK: 3}
        out.sort(key=lambda f: order[f["status"]])
        return {"tv": ip, "at": time.strftime("%d/%m/%Y %H:%M"), "findings": out,
                "manual": [{"title": t, "detail": dd} for t, dd in MANUAL_BY_BRAND.get(brand, [])]}
    out, dev = check_tv(ip)
    with ThreadPoolExecutor(max_workers=6) as ex:
        jobs = [ex.submit(f, *a) for f, a in [
            (check_network, (ip, dev)), (check_pairing, (ip, dev)), (check_developer, (ip, dev)),
            (check_app, (ip,)), (check_apps, (ip, dev)), (check_audio, (ip, dev)), (check_ads, ()),
            (check_panel, ()),
        ]]
        for j in jobs:
            try:
                out += j.result(timeout=30)
            except Exception as e:
                out.append(_f(WARN, "Diagnóstico", f"uma verificação falhou: {e}"))
    order = {FAIL: 0, WARN: 1, INFO: 2, OK: 3}
    out.sort(key=lambda f: order[f["status"]])
    return {"tv": ip, "at": time.strftime("%d/%m/%Y %H:%M"), "findings": out,
            "manual": [{"title": t, "detail": d} for t, d in MANUAL]}


def fix(ip, report):
    """Só o que é seguro fazer sem perguntar: recompilar e reinstalar o app desatualizado (Samsung)."""
    import sys
    done = []
    for f in report["findings"]:
        if f["key"] == "app":
            from . import sdb
            subprocess.run([sys.executable, os.path.join(ROOT, "tizen", "build.py"), "--sign", "tvtuner"],
                           check=True, capture_output=True, timeout=180)
            sdb.install(ip, os.path.join(ROOT, "tizen", "TVTuner.wgt"))
            done.append("app recompilado, assinado e reinstalado")
    return done
