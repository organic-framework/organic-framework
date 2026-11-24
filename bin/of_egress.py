"""The one way out of a harness's outer sandbox (RFC 0008 §3.1; of_server, message 01a20e82-8a40): a proxy OF runs
outside the sandbox for one run's life, on a loopback port of its own, that takes CONNECT only, to the hosts its run
may reach on port 443, and refuses every other host, port and method, plain HTTP included. The sandbox lets the run's
process connect to that port alone (sandbox-exec filters by address and port, never by name, hence the proxy for the
names). Its log names hosts, ports and verdicts, never a path or a body: past CONNECT it only pipes bytes it cannot
read.

Muse reached api.meta.ai:443 alone on the laptop (2026-11-24, a read through a logging proxy given as HTTPS_PROXY).
"""
import select
import socket
import threading

ALLOW = {"muse": ("api.meta.ai",)}
PORT = 443
HEAD_MAX = 8192
IDLE_S = 300


class Proxy:
    """`with Proxy(("api.meta.ai",)) as p:` listening on 127.0.0.1:p.port until the block ends; p.log is
    [(host, port, "allowed"|"refused"|"failed"|"not CONNECT")]."""

    def __init__(self, allow, connect=socket.create_connection):
        self.allow = {h.lower() for h in allow}
        self.connect = connect
        self.log = []
        self._lock = threading.Lock()
        self._sock = None
        self._stop = threading.Event()

    def __enter__(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        s.listen(64)
        s.settimeout(0.5)
        self._sock = s
        self.port = s.getsockname()[1]
        threading.Thread(target=self._serve, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        return False

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def _note(self, host, port, verdict):
        with self._lock:
            self.log.append((host, port, verdict))

    def _serve(self):
        while not self._stop.is_set():
            try:
                c, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=self._one, args=(c,), daemon=True).start()

    def _one(self, c):
        up = None
        try:
            c.settimeout(30)
            head = b""
            while b"\r\n\r\n" not in head:
                d = c.recv(1024)
                if not d or len(head) + len(d) > HEAD_MAX:
                    return
                head += d
            parts = head.split(b"\r\n", 1)[0].decode("latin-1").split()
            if len(parts) != 3 or parts[0] != "CONNECT" or not parts[2].startswith("HTTP/1."):
                self._note(None, None, "not CONNECT")
                c.sendall(b"HTTP/1.1 405 Method Not Allowed\r\nConnection: close\r\n\r\n")
                return
            host, _, port = parts[1].rpartition(":")
            host = host.strip("[]").lower()
            if host not in self.allow or port != str(PORT):
                self._note(host, port, "refused")
                c.sendall(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
                return
            try:
                up = self.connect((host, PORT), timeout=20)
            except OSError:
                self._note(host, port, "failed")
                c.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
                return
            self._note(host, port, "allowed")
            c.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
            rest = head.split(b"\r\n\r\n", 1)[1]
            if rest:
                up.sendall(rest)
            self._pipe(c, up)
        except OSError:
            pass
        finally:
            for s in (c, up):
                if s is not None:
                    try:
                        s.close()
                    except OSError:
                        pass

    def _pipe(self, a, b):
        a.settimeout(None)
        b.settimeout(None)
        while not self._stop.is_set():
            r, _, _ = select.select([a, b], [], [], IDLE_S)
            if not r:
                return
            for s in r:
                d = s.recv(65536)
                if not d:
                    return
                (b if s is a else a).sendall(d)


def sandbox_rules(port):
    """The SBPL lines that let a run's process reach the proxy's port alone (no other address, no Unix socket)."""
    return ["(deny network-outbound)", f'(allow network-outbound (remote ip "localhost:{int(port)}"))']
