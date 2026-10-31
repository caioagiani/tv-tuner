"""Instala um .wgt assinado na TV pelo protocolo SDB (porta 26101), sem Tizen Studio.

Exige Modo Desenvolvedor ligado na TV com o IP deste computador em "Host PC IP".
Protocolo e comandos (`vd_appinstall`, `was_execute`) seguem o que o
free-my-tv/tizen/install.py documentou (MIT) — o `sdb` da Samsung é binário
Intel e não roda no Apple Silicon sem Rosetta.
"""
import re
import socket
import struct
import time
import zipfile

PORT = 26101
MAX_DATA = 4096


class Sdb:
    """Framing do ADB: cabeçalho de 24 bytes, streams abertos com OPEN, cada WRTE confirmado com OKAY."""

    def __init__(self, ip):
        self.sock = socket.create_connection((ip, PORT), timeout=15)
        self.buf = b""
        self.next_id = 1
        self.okay, self.inbox, self.closed = {}, {}, set()
        self._send(b"CNXN", 0x01000000, MAX_DATA, b"host::\0")
        cmd, _, max_data, data = self._read()
        if cmd != b"CNXN":
            raise ConnectionError(f"a TV respondeu {cmd!r} em vez de conectar")
        self.max = min(max_data, MAX_DATA)
        self.banner = data.rstrip(b"\0").decode("utf-8", "replace")

    def _send(self, cmd, arg0, arg1, data=b""):
        c = struct.unpack("<I", cmd)[0]
        self.sock.sendall(struct.pack("<6I", c, arg0, arg1, len(data), sum(data) & 0xFFFFFFFF, c ^ 0xFFFFFFFF) + data)

    def _recv(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("a TV fechou a conexão")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _read(self):
        cmd, arg0, arg1, n, _, _ = struct.unpack("<6I", self._recv(24))
        return struct.pack("<I", cmd), arg0, arg1, self._recv(n) if n else b""

    def _pump(self):
        cmd, remote, local, data = self._read()
        if cmd == b"OKAY":
            self.okay[local] = remote
        elif cmd == b"WRTE":
            self.inbox[local] = self.inbox.get(local, b"") + data
            self._send(b"OKAY", local, remote)
        elif cmd == b"CLSE":
            self.closed.add(local)

    def _open(self, service):
        local, self.next_id = self.next_id, self.next_id + 1
        self._send(b"OPEN", local, 0, service.encode() + b"\0")
        while local not in self.okay:
            if local in self.closed:
                raise ConnectionError("a TV recusou " + service)
            self._pump()
        return local, self.okay.pop(local)

    def _write(self, stream, data):
        local, remote = stream
        self._send(b"WRTE", local, remote, data)
        while local not in self.okay:
            if local in self.closed:
                raise ConnectionError("a TV fechou o stream")
            self._pump()
        del self.okay[local]

    def _read_until(self, stream, done, seconds):
        local = stream[0]
        end = time.time() + seconds
        while time.time() < end and local not in self.closed and not done(self.inbox.get(local, b"")):
            self.sock.settimeout(max(0.1, end - time.time()))
            try:
                self._pump()
            except socket.timeout:
                break
        self.sock.settimeout(15)
        return self.inbox.pop(local, b"")

    def shell(self, command, until=None, seconds=20):
        stream = self._open("shell:" + command)
        pat = re.compile(until) if until else None
        out = self._read_until(stream, lambda t: bool(pat and pat.search(t.decode("utf-8", "replace"))), seconds)
        return out.decode("utf-8", "replace")

    def capabilities(self):
        text = self._read_until(self._open("capability:"), lambda t: False, 5).decode("utf-8", "replace")
        return dict(line.split(":", 1) for line in text.splitlines() if ":" in line)

    def push(self, path, data):
        stream = self._open("sync:")
        spec = f"{path},{0o100644}".encode()
        self._write(stream, b"SEND" + struct.pack("<I", len(spec)) + spec)
        step = self.max - 8
        for i in range(0, len(data), step):
            part = data[i:i + step]
            self._write(stream, b"DATA" + struct.pack("<I", len(part)) + part)
        self._write(stream, b"DONE" + struct.pack("<I", int(time.time())))
        answer = self._read_until(stream, lambda t: len(t) >= 8, 60)
        if answer[:4] != b"OKAY":
            raise ConnectionError("a TV não aceitou o arquivo: " + answer[8:].decode("utf-8", "replace"))
        self._send(b"WRTE", stream[0], stream[1], b"QUIT" + struct.pack("<I", 0))


def manifest_ids(xml):
    tag = re.search(r"<tizen:application[^>]*>", xml).group(0)
    return re.search(r'\bid="([^"]+)"', tag).group(1), re.search(r'\bpackage="([^"]+)"', tag).group(1)


def install(ip, wgt_path, log=print):
    with zipfile.ZipFile(wgt_path) as z:
        if "author-signature.xml" not in z.namelist():
            raise SystemExit("O .wgt não está assinado; a TV só instala pacote assinado. Veja tizen/README.md.")
        app_id, package = manifest_ids(z.read("config.xml").decode("utf-8"))
    sdb = Sdb(ip)
    caps = sdb.capabilities()
    log(f"Conectado: Tizen {caps.get('platform_version', '?')}")
    remote = caps.get("sdk_toolpath", "/home/owner/share/tmp/sdk_tools").rstrip("/") + "/tvtuner.wgt"
    with open(wgt_path, "rb") as f:
        sdb.push(remote, f.read())
    log("Instalando...")
    out = sdb.shell(f"0 vd_appinstall {package} {remote}", r"spend time", 120)
    if "install completed" not in out:
        hint = ""
        if "different certificate" in out:
            hint = "\nJá existe uma versão assinada com outro certificado: desinstale (tv app uninstall) e repita."
        raise SystemExit("A TV não concluiu a instalação:\n" + out.strip() + hint)
    sdb.shell("0 was_execute " + app_id, r"launched|spend time", 20)
    log("Instalado e aberto na TV.")


def uninstall(ip, package):
    out = Sdb(ip).shell("0 vd_appuninstall " + package, r"spend time", 120)
    if "uninstall completed" not in out:
        raise SystemExit("A TV respondeu:\n" + out.strip() + "\nRemova pela TV: Apps > Configurações > app > Excluir.")
