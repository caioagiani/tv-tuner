"""Apps conhecidos por ID.

A AU7700 (Tizen 6) não responde `ed.installedApp.get` no websocket — medido em
30/10/2026 —, então a lista de instalados sai de perguntar, um por um, à API
REST (`GET /api/v2/applications/<id>`: 200 se instalado, 404 se não).
IDs extras: ~/.config/tv-tuner/apps.json  →  {"3201907018807": "Netflix", ...}
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor

from . import rest

USER_FILE = os.path.expanduser("~/.config/tv-tuner/apps.json")

KNOWN = {
    "3201907018807": "Netflix",
    "111299001912": "YouTube",
    "3201910019365": "Prime Video",
    "3201901017640": "Disney+",
    "3201606009684": "Spotify",
    "3201807016597": "Apple TV",
    "3202203026841": "Twitch",
    "3202010022079": "Navegador",
    "3201601007230": "HBO Max",
    "3201512006963": "Plex",
    "3201808016802": "Pluto TV",
    "3201710015067": "Paramount+",
    "3201608010191": "Deezer",
    "3201611010983": "YouTube Kids",
    "3201905018355": "Crunchyroll",
    "3201908019041": "Globoplay",
}


def candidates():
    ids = dict(KNOWN)
    try:
        with open(USER_FILE, encoding="utf-8") as f:
            ids.update(json.load(f))
    except FileNotFoundError:
        pass
    return ids


def installed(ip):
    def probe(item):
        app_id, fallback = item
        try:
            st = rest.app_status(ip, app_id)
            return {"appId": app_id, "name": st.get("name") or fallback,
                    "running": st.get("running"), "visible": st.get("visible")}
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=16) as ex:
        return [a for a in ex.map(probe, candidates().items()) if a]
