"""API HTTP da porta 8001: estado de apps, abrir, fechar.

Firmware mais novo pode exigir o token também aqui; por isso vai pela 8002
(TLS) com o token quando houver.
"""
import json
import urllib.request

from .discover import tls_context


def _req(ip, path, method="GET", token=None):
    if token:
        url = f"https://{ip}:8002/api/v2/{path}?token={token}"
        ctx = tls_context()
    else:
        url = f"http://{ip}:8001/api/v2/{path}"
        ctx = None
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=5, context=ctx) as r:
        body = r.read()
    # O POST de abrir app volta 200 com corpo quebrado ("HTTP/" no lugar de
    # "true", medido na AU7700): o status é o que vale, o corpo é bônus.
    try:
        return json.loads(body) if body else None
    except ValueError:
        return None


def app_status(ip, app_id, token=None):
    return _req(ip, f"applications/{app_id}", token=token)


def app_open(ip, app_id, token=None):
    return _req(ip, f"applications/{app_id}", "POST", token)


def app_close(ip, app_id, token=None):
    return _req(ip, f"applications/{app_id}", "DELETE", token)
