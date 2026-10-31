"""Ligar a TV pela rede.

Medido na AU7700 (Wi-Fi, 31/10/2026): em espera ela mantém a rede e responde
`PowerState: standby`; o Wake-on-LAN não a acorda, a tecla Power pelo
websocket acorda em ~2 s. Então: em espera → Power; sem resposta → WoL
(precisa de "Ligar com dispositivo móvel" e costuma exigir cabo).
"""
import ipaddress
import socket


def wake(mac, broadcast="255.255.255.255"):
    raw = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    if len(raw) != 6:
        raise ValueError(f"MAC inválido: {mac}")
    packet = b"\xff" * 6 + raw * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for port in (9, 7):
            for _ in range(3):
                s.sendto(packet, (broadcast, port))


def power_on(ip, mac):
    """Liga. Devolve como ligou: "already", "key" ou "wol"."""
    from . import discover
    from .remote import open_remote
    try:
        state = discover.info(ip, timeout=2)["device"].get("PowerState")
    except Exception:
        state = None
    if state == "on":
        return "already"
    from . import config
    if state == "standby" and config.tv(ip).get("token"):   # sem pareio a tecla não passa
        r = open_remote(ip)
        r.timeout = 6
        with r:
            r.key("KEY_POWER")
        return "key"
    if not mac:
        raise RuntimeError("TV não responde e o MAC é desconhecido")
    wake(mac)
    wake(mac, str(ipaddress.ip_network(discover.local_ip() + "/24", strict=False).broadcast_address))
    return "wol"
