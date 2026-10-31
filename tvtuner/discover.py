"""Acha TVs Samsung na rede e lê o que elas dizem de si.

SSDP falha em vários modelos (não respondem M-SEARCH), então a varredura é
direto na porta 8001 de cada IP da sub-rede.
"""
import ipaddress
import json
import socket
import ssl
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    finally:
        s.close()


def info(ip, timeout=2.0):
    """GET /api/v2/ — modelo, estado de energia, modo desenvolvedor, MAC."""
    with urllib.request.urlopen(f"http://{ip}:8001/api/v2/", timeout=timeout) as r:
        return json.load(r)


def _probe(ip):
    try:
        d = info(ip, timeout=1.0)
        return ip, d if "device" in d else None
    except Exception:
        return ip, None


def scan(cidr=None):
    if not cidr:
        cidr = str(ipaddress.ip_network(local_ip() + "/24", strict=False))
    hosts = [str(h) for h in ipaddress.ip_network(cidr).hosts()]
    with ThreadPoolExecutor(max_workers=64) as ex:
        return [(ip, d) for ip, d in ex.map(_probe, hosts) if d]


def port_open(ip, port, timeout=1.0):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def tls_context():
    # Certificado da TV é autoassinado; a conexão é só na rede local.
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx
