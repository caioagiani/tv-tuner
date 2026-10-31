#!/usr/bin/env python3
"""Empacota a tela inicial em tizen/TVTuner.wgt.

    python3 tizen/build.py                 # zip sem assinatura (só para conferir)
    python3 tizen/build.py --sign PERFIL   # assinado com o perfil de certificado Samsung

A TV só instala pacote assinado com certificado Samsung que inclua o DUID dela.
Assinar exige o `tizen` CLI (Tizen Studio ou Tizen SDK CLI). Ver tizen/README.md.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
WGT = os.path.join(HERE, "TVTuner.wgt")
FILES = ["config.xml", "index.html", "app.js", "style.css", "icon.png"]


def tizen_cli():
    home = os.path.expanduser("~")
    for base in (os.environ.get("TIZEN_STUDIO", ""), os.path.join(home, "tizen-studio"), os.path.join(home, "tizen-sdk")):
        path = os.path.join(base, "tools", "ide", "bin", "tizen")
        if base and os.path.exists(path):
            return path
    return shutil.which("tizen")


def sign(profile):
    cli = tizen_cli()
    if not cli:
        sys.exit("Sem o `tizen` CLI. Instale o Tizen SDK (ver tizen/README.md) ou defina TIZEN_STUDIO.")
    out = tempfile.mkdtemp()
    try:
        run = subprocess.run([cli, "package", "-t", "wgt", "-s", profile, "-o", out, "--", WGT],
                             capture_output=True, text=True)
        made = [n for n in os.listdir(out) if n.endswith(".wgt")]
        if run.returncode != 0 or not made:
            sys.exit("`tizen package` falhou:\n" + (run.stdout + run.stderr).strip())
        shutil.move(os.path.join(out, made[0]), WGT)
    finally:
        shutil.rmtree(out, ignore_errors=True)
    with zipfile.ZipFile(WGT) as z:
        if "author-signature.xml" not in z.namelist():
            sys.exit("Pacote voltou sem assinatura.")


def main():
    args = sys.argv[1:]
    with zipfile.ZipFile(WGT, "w", zipfile.ZIP_DEFLATED) as z:
        for name in FILES:
            z.write(os.path.join(HERE, name), name)
    profile = args[args.index("--sign") + 1] if "--sign" in args else None
    if profile:
        sign(profile)
    print(f"{WGT} ({os.path.getsize(WGT) // 1024} KB, {'assinado: ' + profile if profile else 'sem assinatura'})")


if __name__ == "__main__":
    main()
