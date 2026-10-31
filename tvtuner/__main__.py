"""tv — controle e otimização de TV pela rede (Samsung Tizen, LG webOS, Roku e Android TV).

    tv discover                 acha TVs na rede (salva se houver uma só)
    tv use 192.168.1.50          escolhe a TV
    tv info                     modelo, energia, modo dev, portas
    tv pair                     pede "Permitir" na TV e guarda a chave (Samsung, LG, Android TV)
    tv key HOME VOLUP ...       envia teclas
    tv macro <nome>             roda uma sequência (tv macro sem nome lista)
    tv apps                     lista apps instalados
    tv launch <appId|nome>      abre um app
    tv text "busca"             digita no teclado virtual aberto
    tv browser <url>            abre o navegador da TV numa URL (Samsung, LG)
    tv on | tv off              liga (Wake-on-LAN) / desliga
    tv doctor [--fix]           diagnóstico: o que está certo, o que ajustar
    tv panel [--pin 1234]       painel web para controlar pelo celular
    tv service install --pin 1234   painel sempre no ar (sobe com o login do Mac)
    tv checklist                passo a passo das configurações de privacidade/imagem
    tv app build|install|uninstall   a tela inicial própria (pasta tizen/)
"""
import argparse
import json
import os
import plistlib
import subprocess
import sys

from . import config, discover, drivers, macros
from .remote import PairingError, open_remote

ROOT = __file__.rsplit("/tvtuner/", 1)[0]


def _tv_ip(args):
    ip = args.ip or config.load().get("ip")
    if not ip:
        sys.exit("Sem TV escolhida. Rode: tv discover")
    return ip


def _drv(args):
    return drivers.get(_tv_ip(args))


def _save_tv(ip, d):
    config.remember(ip, brand=d["brand"], mac=d["mac"], model=d["model"], name=d["name"])
    config.select(ip)
    print(f"TV salva: {d['model']} ({d['brand']}) em {ip}")


def cmd_discover(args):
    found = drivers.scan(args.cidr)
    if not found:
        sys.exit("Nenhuma TV respondeu (Samsung na 8001, LG na 3000/3001, Roku na 8060, Android TV na 6467). Ela está ligada e na mesma rede?")
    for ip, d in found:
        print(f"{ip:16} {d['brand']:8} {d['model'] or '?':22} {d['name'] or ''}  [{d['power']}]")
    if len(found) == 1 and not config.load().get("ip"):
        _save_tv(*found[0])
    elif not config.load().get("ip"):
        print("\nMais de uma TV. Escolha com: tv use <ip>")


def cmd_use(args):
    d = drivers.probe(args.target, timeout=3)
    if not d:
        sys.exit(f"Nenhuma TV conhecida responde em {args.target}.")
    _save_tv(args.target, d)


def cmd_info(args):
    ip = _tv_ip(args)
    drv = drivers.get(ip)
    d = drv.info(timeout=3)
    if not d:
        sys.exit(f"A TV não responde em {ip}.")
    for k in ("brand", "model", "name", "os", "power", "mac"):
        if d.get(k):
            print(f"{k:18} {d[k]}")
    print(f"{'suporta':18} {', '.join(sorted(drv.caps))}")
    if drv.brand == "samsung":
        dv = d["raw"]
        for k in ("developerMode", "developerIP", "TokenAuthSupport", "resolution", "networkType"):
            if k in dv:
                print(f"{k:18} {dv[k]}")
        print("\nportas:")
        for port, what in [(8001, "API/remoto"), (8002, "remoto TLS"), (26101, "SDB (modo dev)"), (9197, "DMR/UPnP")]:
            print(f"  {port:6} {'aberta ' if discover.port_open(ip, port) else 'fechada'}  {what}")
        print(f"\ntoken salvo: {'sim' if config.tv(ip).get('token') else 'não (rode tv pair)'}")


def cmd_pair(args):
    drv = _drv(args)
    if "pair" not in drv.caps:
        print(f"{drv.label} não precisa de pareamento.")
        return
    print("Olhe a TV: ela vai mostrar um código." if getattr(drv, "pair_with_code", False)
          else "Olhe a TV e escolha Permitir...")
    try:
        drv.pair(timeout=60)
        print("Pareado. Token salvo em", config.PATH)
    except PairingError as e:
        sys.exit(str(e))


def cmd_key(args):
    _drv(args).keys(args.keys, delay=args.delay)


def cmd_macro(args):
    m = macros.load()
    if not args.name:
        for name, seq in sorted(m.items()):
            print(f"{name:16} {' '.join(map(str, seq))}")
        return
    if args.name not in m:
        sys.exit(f"Macro desconhecida: {args.name}. Lista: tv macro")
    _drv(args).keys(m[args.name], delay=args.delay)


def cmd_apps(args):
    apps = _drv(args).apps()
    if args.json:
        print(json.dumps(apps, indent=2, ensure_ascii=False))
        return
    for a in sorted(apps, key=lambda a: a.get("name", "").lower()):
        print(f"{a.get('appId', ''):28} {a.get('name', '')}")


def cmd_launch(args):
    drv = _drv(args)
    target = args.app
    if "." not in target and not target.isdigit():
        match = [a for a in drv.apps() if target.lower() in a.get("name", "").lower()]
        if not match:
            sys.exit(f"Nenhum app com '{target}' no nome. Veja: tv apps")
        target = match[0]["appId"]
        print(f"Abrindo {match[0]['name']} ({target})")
    drv.launch(target)


def cmd_text(args):
    _drv(args).text(args.value)


def cmd_browser(args):
    _drv(args).browser(args.url)


def cmd_on(args):
    how = _drv(args).power_on()
    print({"already": "Já estava ligada.", "key": "Ligando (estava em espera).",
           "wol": "TV não respondeu; Wake-on-LAN enviado. Se não ligar: ative ligar pela rede no menu da TV."}[how])


def cmd_off(args):
    _drv(args).power_off()


def cmd_panel(args):
    from . import panel
    panel.serve(_tv_ip(args), args.host, args.port, args.pin, args.tls_port)


CHECKLIST = [
    ("Privacidade — Serviços de informação de visualização (ACR)",
     "Configurações > Geral > Termos e Privacidade > Termos e Políticas > Serviços de Informações de Visualização: Não concordar"),
    ("Privacidade — Anúncios baseados em interesse",
     "Configurações > Geral > Termos e Privacidade > Termos e Políticas > Serviço de anúncios baseados em interesse: Não concordar"),
    ("Privacidade — Reconhecimento de voz / Bixby",
     "Configurações > Geral > Termos e Privacidade > Termos e Políticas > Serviços de reconhecimento de voz: Não concordar (se não usa Bixby/Alexa)"),
    ("Privacidade — Redefinir ID de publicidade",
     "Configurações > Geral > Termos e Privacidade > Privacidade > Redefinir PSID"),
    ("Início — Não abrir a Smart Hub sozinha",
     "Configurações > Geral > Recursos inteligentes > Executar Smart Hub automaticamente: Desligado"),
    ("Início — Voltar ao último app/fonte",
     "Configurações > Geral > Recursos inteligentes > Executar último app automaticamente: Ligado"),
    ("Rede — Permitir 'tv on' pela rede",
     "Configurações > Geral > Rede > Configurações especialistas > Ligar com dispositivo móvel: Ligado"),
    ("Energia — Eco",
     "Configurações > Geral > Solução Eco > Detecção de luz ambiente / Modo de economia: Desligado (imagem não fica escura do nada)"),
    ("Imagem — Modo",
     "Configurações > Imagem > Modo de imagem: Filme (cores mais fiéis; o Padrão/Dinâmico satura e esquenta)"),
    ("Imagem — Movimento",
     "Configurações > Imagem > Configurações especialistas > Configurações do Picture Clarity: Desligado para filmes (tira o efeito novela)"),
    ("Jogos — Game Mode automático",
     "Configurações > Geral > Gerenciador de dispositivos externos > Modo de Jogo: Auto (menor latência com console)"),
    ("Atualização — Auto update",
     "Configurações > Suporte > Atualização de software > Atualização automática: sua escolha (pode reativar termos/anúncios)"),
]


def cmd_checklist(args):
    if drivers.brand_of(_tv_ip(args)) != "samsung":
        sys.exit("O checklist é do menu da Samsung. Para esta TV: tv doctor (traz a lista do menu dela).")
    print("Os nomes podem variar um pouco com o firmware. Enter avança, q sai.\n")
    remote = None
    try:
        remote = open_remote(args.ip).connect()
        remote.key("KEY_MENU")
    except Exception:
        print("(sem conexão com a TV — siga pelo controle físico)\n")
    for i, (title, path) in enumerate(CHECKLIST, 1):
        print(f"[{i}/{len(CHECKLIST)}] {title}\n    {path}")
        if input("    > ").strip().lower() == "q":
            break
    if remote:
        remote.close()
    print("\nComplemento: bloqueie os domínios de dns/blocklist.txt no roteador/NextDNS/Pi-hole.")


PLIST = os.path.expanduser("~/Library/LaunchAgents/com.tvtuner.panel.plist")
LABEL = "com.tvtuner.panel"


def cmd_service(args):
    """Painel sempre no ar: launchd sobe no login e reergue se cair."""
    uid = os.getuid()
    if args.action == "install":
        log = os.path.expanduser("~/Library/Logs/tvtuner-panel.log")
        argv = [sys.executable, "-m", "tvtuner", "panel", "--port", str(args.port)] + (["--pin", args.pin] if args.pin else [])
        plist = {
            "Label": LABEL, "ProgramArguments": argv, "WorkingDirectory": ROOT,
            "EnvironmentVariables": {"PYTHONPATH": ROOT}, "RunAtLoad": True, "KeepAlive": True,
            "StandardOutPath": log, "StandardErrorPath": log,
        }
        os.makedirs(os.path.dirname(PLIST), exist_ok=True)
        subprocess.call(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], stderr=subprocess.DEVNULL)
        with open(PLIST, "wb") as f:
            plistlib.dump(plist, f)
        subprocess.check_call(["launchctl", "bootstrap", f"gui/{uid}", PLIST])
        print(f"Painel no ar e iniciando com o login: http://{discover.local_ip()}:{args.port}  (log: {log})")
    elif args.action == "uninstall":
        subprocess.call(["launchctl", "bootout", f"gui/{uid}/{LABEL}"])
        if os.path.exists(PLIST):
            os.remove(PLIST)
        print("Painel removido do login.")
    else:
        rc = subprocess.call(["launchctl", "print", f"gui/{uid}/{LABEL}"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("rodando" if rc == 0 else "parado")


ICON = {"ok": "\033[32m✓\033[0m", "aviso": "\033[33m!\033[0m", "falha": "\033[31m✗\033[0m", "info": "\033[36mi\033[0m"}


def cmd_doctor(args):
    from . import doctor
    ip = _tv_ip(args)
    rep = doctor.run(ip)
    if args.json:
        print(json.dumps(rep, indent=2, ensure_ascii=False))
        return
    print(f"Diagnóstico de {ip} — {rep['at']}\n")
    for f in rep["findings"]:
        print(f" {ICON[f['status']]} {f['title']}: {f['detail']}")
        if f["fix"]:
            print(f"     → {f['fix']}")
    print("\nConfira no menu da TV (não dá para ler pela rede):")
    for m in rep["manual"]:
        print(f"   · {m['title']}: {m['detail']}")
    if args.fix:
        done = doctor.fix(ip, rep)
        print("\nCorrigido: " + (", ".join(done) if done else "nada a corrigir automaticamente"))
    bad = [f for f in rep["findings"] if f["status"] in ("falha", "aviso")]
    print(f"\n{len(bad)} ponto(s) de atenção." if bad else "\nTudo certo.")


def cmd_app(args):
    if args.action == "build":
        cmd = [sys.executable, f"{ROOT}/tizen/build.py"] + (["--sign", args.profile] if args.profile else [])
        sys.exit(subprocess.call(cmd))
    from . import sdb
    ip = _tv_ip(args)
    if args.action == "install":
        sdb.install(ip, f"{ROOT}/tizen/TVTuner.wgt")
    elif args.action == "uninstall":
        with open(f"{ROOT}/tizen/config.xml", encoding="utf-8") as f:
            _, package = sdb.manifest_ids(f.read())
        sdb.uninstall(ip, package)
        print("Removido.")


def main():
    p = argparse.ArgumentParser(prog="tv", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ip", help="IP da TV (padrão: a salva)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("discover"); s.add_argument("--cidr"); s.set_defaults(fn=cmd_discover)
    s = sub.add_parser("use"); s.add_argument("target"); s.set_defaults(fn=cmd_use)
    sub.add_parser("info").set_defaults(fn=cmd_info)
    sub.add_parser("pair").set_defaults(fn=cmd_pair)
    s = sub.add_parser("key"); s.add_argument("keys", nargs="+"); s.add_argument("--delay", type=float, default=0.4)
    s.set_defaults(fn=cmd_key)
    s = sub.add_parser("macro"); s.add_argument("name", nargs="?"); s.add_argument("--delay", type=float, default=0.4)
    s.set_defaults(fn=cmd_macro)
    s = sub.add_parser("apps"); s.add_argument("--json", action="store_true"); s.set_defaults(fn=cmd_apps)
    s = sub.add_parser("launch"); s.add_argument("app"); s.set_defaults(fn=cmd_launch)
    s = sub.add_parser("text"); s.add_argument("value"); s.set_defaults(fn=cmd_text)
    s = sub.add_parser("browser"); s.add_argument("url"); s.set_defaults(fn=cmd_browser)
    sub.add_parser("on").set_defaults(fn=cmd_on)
    sub.add_parser("off").set_defaults(fn=cmd_off)
    s = sub.add_parser("panel"); s.add_argument("--host", default="0.0.0.0"); s.add_argument("--port", type=int, default=8780)
    s.add_argument("--pin")
    s.add_argument("--tls-port", type=int, default=8743, help="HTTPS com microfone (0 desliga)"); s.set_defaults(fn=cmd_panel)
    sub.add_parser("checklist").set_defaults(fn=cmd_checklist)
    s = sub.add_parser("doctor"); s.add_argument("--fix", action="store_true"); s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_doctor)
    s = sub.add_parser("service"); s.add_argument("action", choices=["install", "uninstall", "status"])
    s.add_argument("--port", type=int, default=8780); s.add_argument("--pin"); s.set_defaults(fn=cmd_service)
    s = sub.add_parser("app"); s.add_argument("action", choices=["build", "install", "uninstall"])
    s.add_argument("--profile", help="perfil de certificado Samsung para assinar"); s.set_defaults(fn=cmd_app)

    args = p.parse_args()
    try:
        args.fn(args)
    except PairingError as e:
        sys.exit(str(e))
    except (ConnectionError, OSError) as e:
        sys.exit(f"Falha de conexão com a TV: {e}")


if __name__ == "__main__":
    main()
