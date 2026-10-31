"""Uma interface, várias marcas. Ver base.py para o contrato."""
import ipaddress
from concurrent.futures import ThreadPoolExecutor

from .. import config, discover
from .base import ALL_CAPS, HOME_APP, Driver, Unsupported
from .lg import LG
from .roku import Roku
from .samsung import Samsung

DRIVERS = {d.brand: d for d in (Samsung, LG, Roku)}


def probe(ip, brand=None, timeout=1.0):
    """Quem responde neste IP. Com `brand`, tenta essa marca primeiro."""
    order = sorted(DRIVERS.values(), key=lambda d: d.brand != brand)
    for d in order:
        found = d.probe(ip, timeout)
        if found:
            return found
    return None


def scan(cidr=None):
    """TVs da sub-rede: cada marca pela própria porta, em paralelo."""
    if not cidr:
        cidr = str(ipaddress.ip_network(discover.local_ip() + "/24", strict=False))
    jobs = [(str(h), d) for h in ipaddress.ip_network(cidr).hosts() for d in DRIVERS.values()]
    with ThreadPoolExecutor(max_workers=256) as ex:
        hits = ex.map(lambda j: (j[0], j[1].probe(j[0], 1.0)), jobs)
        found = {}
        for ip, d in hits:
            if d and ip not in found:
                found[ip] = d
    return sorted(found.items(), key=lambda kv: tuple(int(p) for p in kv[0].split(".")))


def brand_of(ip):
    saved = config.tv(ip).get("brand")
    if saved in DRIVERS:
        return saved
    found = probe(ip)
    if found:
        if ip in config.tvs():
            config.remember(ip, brand=found["brand"])
        return found["brand"]
    return "samsung"            # TV desligada e nunca vista: o caso original do projeto


def get(ip, emit=None):
    return DRIVERS[brand_of(ip)](ip, emit)


__all__ = ["ALL_CAPS", "DRIVERS", "HOME_APP", "Driver", "Unsupported", "brand_of", "get", "probe", "scan"]
