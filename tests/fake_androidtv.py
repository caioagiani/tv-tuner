"""Android TV de mentira: pareamento por código (6467) e controle (6466) em TLS
com certificado do cliente, protobuf como o da TV. O código "mostrado na
tela" fica em `tv.code`. Usa o mesmo protobuf/DER do driver."""
import os
import secrets
import select
import socket
import ssl
import subprocess
import tempfile
import threading

from tvtuner.drivers.androidtv import first, parse, pb, recv_msg, secret, send_msg

NAME = "atvremote/sabrina/sabrina/Chromecast/AA:BB:CC:DD:EE:01"


class AndroidTV:
    def __init__(self, client_cert):
        self.client_cert = client_cert
        self.paired = set()             # DER dos clientes pareados
        self.code = None
        self.on = True
        self.app = "com.google.android.apps.tv.launcherx"
        self.volume, self.muted = 8, False
        self.keys, self.links, self.text = [], [], []
        self.conns = []

    def push_volume(self):
        # Mesma regra do driver: escrever de outra thread só sob a trava da conexão.
        for c in list(self.conns):
            try:
                with c.io:
                    send_msg(c, pb((50, pb((6, 100), (7, self.volume), (8, self.muted)))))
            except (OSError, ValueError):
                self.conns.remove(c)

    def serve_pair(self, s):
        outer = lambda *f: pb((1, 2), (2, 200), *f)
        client = s.getpeercert(True)
        while True:
            m = recv_msg(s)
            if 10 in m:
                send_msg(s, outer((11, pb((1, "fake")))))
            elif 20 in m:
                send_msg(s, outer((20, pb((3, 1)))))
            elif 30 in m:
                nonce = secrets.token_bytes(2).hex().upper()
                h = secret(client, self.server_der, "00" + nonce)
                self.code = f"{h[0]:02X}{nonce}"
                send_msg(s, outer((31, b"")))
            elif 40 in m:
                got = first(parse(m[40][0]), 1)
                if got == secret(client, self.server_der, self.code):
                    self.paired.add(client)
                    send_msg(s, outer((41, pb((1, got)))))
                else:
                    send_msg(s, pb((1, 2), (2, 402)))
                return

    def serve_api(self, s):
        if s.getpeercert(True) not in self.paired:
            return                                   # TV real derruba o TLS
        send_msg(s, pb((1, pb((1, 0x27F), (2, pb((1, "Chromecast"), (2, "Google")))))))
        recv_msg(s)                                  # nossa configuração
        send_msg(s, pb((2, pb((1, 1)))))
        recv_msg(s)
        send_msg(s, pb((40, pb((1, self.on)))))
        send_msg(s, pb((20, pb((1, pb((12, self.app)))))))
        s.io = threading.Lock()
        self.conns.append(s)
        self.push_volume()
        while True:
            if not s.pending() and not select.select([s], [], [], 0.5)[0]:
                continue
            with s.io:
                m = recv_msg(s)
            if 10 in m:
                k = first(parse(m[10][0]), 1)
                self.keys.append(k)
                if k == 223:
                    self.on = False
                elif k == 224:
                    self.on = True
                elif k == 164:
                    self.muted = not self.muted
                    self.push_volume()
                elif k == 24:
                    self.volume += 1
                    self.push_volume()
                if k in (223, 224):
                    with s.io:
                        send_msg(s, pb((40, pb((1, self.on)))))
            elif 90 in m:
                link = first(parse(m[90][0]), 1).decode()
                self.links.append(link)
                self.app = link.split("id=")[-1]
                with s.io:
                    send_msg(s, pb((20, pb((1, pb((12, self.app)))))))
            elif 21 in m:
                e = parse(m[21][0])
                info = parse(first(parse(first(e, 3)), 2))
                self.text.append(first(info, 3).decode())


def start(client_cert, host="127.0.0.1", api_port=16466, pair_port=16467):
    d = tempfile.mkdtemp()
    cert, key = os.path.join(d, "c.pem"), os.path.join(d, "k.pem")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", "/CN=" + NAME.replace("/", "\\/"), "-keyout", key, "-out", cert], check=True, capture_output=True)
    tv = AndroidTV(client_cert)
    tv.server_der = ssl.PEM_cert_to_DER_cert(open(cert).read())
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(client_cert)          # o cliente é autoassinado: ele é a própria CA
    socks = []
    for port, fn in ((pair_port, tv.serve_pair), (api_port, tv.serve_api)):
        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((host, port))
        srv.listen(8)
        socks.append(srv)

        def loop(srv=srv, fn=fn):
            while True:
                try:
                    raw, _ = srv.accept()
                except OSError:
                    return
                threading.Thread(target=_run, args=(ctx, raw, fn), daemon=True).start()
        threading.Thread(target=loop, daemon=True).start()
    return tv, socks


def _run(ctx, raw, fn):
    try:
        with ctx.wrap_socket(raw, server_side=True) as s:
            fn(s)
    except Exception:
        pass
