"""Configuração local: IP, MAC e token da TV.

Fica em ~/.config/tv-tuner/config.json, fora do repositório — o token dá
controle total da TV a quem tiver o arquivo.
"""
import json
import os

PATH = os.path.expanduser(os.environ.get("TVTUNER_CONFIG", "~/.config/tv-tuner/config.json"))
DIR = os.path.dirname(PATH)


def load():
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save(cfg):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, PATH)


def update(**fields):
    cfg = load()
    cfg.update({k: v for k, v in fields.items() if v is not None})
    save(cfg)
    return cfg


# Várias TVs: cada uma guarda o próprio token em "tvs"[ip]. O topo (ip, mac,
# model, token) continua sendo a TV escolhida — a CLI inteira lê de lá.
TV_FIELDS = ("name", "model", "mac", "token")


def tvs(cfg=None):
    cfg = cfg if cfg is not None else load()
    out = {ip: dict(v) for ip, v in (cfg.get("tvs") or {}).items()}
    if cfg.get("ip"):
        cur = out.setdefault(cfg["ip"], {})
        for k in TV_FIELDS:
            if cfg.get(k) and not cur.get(k):
                cur[k] = cfg[k]
    return out


def tv(ip):
    return tvs().get(ip, {})


def remember(ip, **fields):
    """Atualiza uma TV salva (e o topo, se for a escolhida)."""
    cfg = load()
    all_ = tvs(cfg)
    entry = all_.setdefault(ip, {})
    entry.update({k: v for k, v in fields.items() if v is not None})
    cfg["tvs"] = all_
    if cfg.get("ip") == ip:
        cfg.update({k: v for k, v in fields.items() if k in TV_FIELDS and v is not None})
    save(cfg)
    return entry


def forget(ip):
    cfg = load()
    all_ = tvs(cfg)
    all_.pop(ip, None)
    cfg["tvs"] = all_
    if cfg.get("ip") == ip:
        for k in ("ip",) + TV_FIELDS:
            cfg.pop(k, None)
    save(cfg)


def select(ip):
    cfg = load()
    all_ = tvs(cfg)
    entry = all_.setdefault(ip, {})
    cfg["tvs"] = all_
    for k in TV_FIELDS:
        cfg.pop(k, None)
    cfg["ip"] = ip
    cfg.update({k: entry[k] for k in TV_FIELDS if entry.get(k)})
    save(cfg)
    return entry
