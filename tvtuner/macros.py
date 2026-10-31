"""Sequências de teclas com nome.

As embutidas só usam teclas cujo efeito não depende de onde o cursor está.
Navegar menu "às cegas" (baixo, baixo, OK...) quebra com qualquer
atualização de firmware — essas ficam no arquivo do usuário, por conta dele:
~/.config/tv-tuner/macros.json  →  {"nome": ["KEY_MENU", 1.5, "KEY_DOWN", ...]}
Número na lista é pausa em segundos.
"""
import json
import os

USER_FILE = os.path.expanduser("~/.config/tv-tuner/macros.json")

BUILTIN = {
    "home": ["KEY_HOME"],
    "configuracoes": ["KEY_MENU"],
    "fontes": ["KEY_SOURCE"],
    "hdmi1": ["KEY_HDMI1"],
    "hdmi2": ["KEY_HDMI2"],
    "hdmi3": ["KEY_HDMI3"],
    "mudo": ["KEY_MUTE"],
    "desligar": ["KEY_POWER"],
    # Atalhos de acessibilidade/info que a TV abre direto:
    "info": ["KEY_INFO"],
    "guia": ["KEY_GUIDE"],
}


def load():
    macros = dict(BUILTIN)
    try:
        with open(USER_FILE, encoding="utf-8") as f:
            macros.update(json.load(f))
    except FileNotFoundError:
        pass
    return macros
