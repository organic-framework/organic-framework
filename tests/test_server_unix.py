"""of-server on a unix socket in place of a port (cfg `socket`, `tls.socket`): a server on a shared host opens no TCP
port another user could reach, and the forward's user reaches it by the folder's group (2026-11-19, of_server, for a
server moved onto a host other users share). A real TLS handshake through the socket with certificates made in a
temporary folder."""

import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import types
import unittest
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_client  # noqa: E402
import of_server  # noqa: E402
from tests.test_server_tls import Hello  # noqa: E402


@unittest.skipUnless(shutil.which("openssl"), "openssl makes the test certificates")
class UnixSocketTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ofu-", dir="/tmp")   # a unix socket path is short (104 bytes on macOS)
        self.cert, self.key, _ = of_server.tls_init({"state_dir": os.path.join(self.tmp, "a")})
        self.home = os.path.join(self.tmp, "home")
        self.env = mock.patch.dict(os.environ, {"HOME": self.home})
        self.env.start()
        self.servers = []

    def tearDown(self):
        for s in self.servers:
            s.shutdown()
            s.server_close()
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, srv):
        srv.seen = []
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servers.append(srv)
        return srv

    def tls_listener(self, path, mode=None):
        app = types.SimpleNamespace(cfg={"tls": {"socket": path, "cert": self.cert, "key": self.key,
                                                 **({"socket_mode": mode} if mode else {})}})
        with mock.patch.object(of_server, "make_handler", lambda a: Hello):
            return of_server.App.serve_tls(app)

    def test_a_pinned_client_reaches_the_tls_socket_and_its_mode_is_the_groups(self):
        path = os.path.join(self.tmp, "server.sock")
        srv = self.start(self.tls_listener(path))
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o660)
        self.assertEqual(of_server.listener_name(srv), path)
        url = "https://127.0.0.1:18210"
        dest = of_client.pin_path(url)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy(self.cert, dest)
        with open(dest[:-len(".pem")] + ".socket", "w") as fh:
            fh.write(path + "\n")
        with of_client.urlopen_no_redirect(urllib.request.Request(url + "/v1/health"), timeout=10,
                                           context=of_client.tls_context(url)) as r:
            self.assertEqual(json.loads(r.read()), {"ok": True, "forwarded": True})
        self.assertEqual(srv.seen[-1][0], "/v1/health")
        # a proxy in the environment (Codex sets one for its commands) is never used for a socket-reached server
        # (of_platform, msg 01a1f997-49af): the request still reaches the Unix socket, not the proxy's port
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9",
                                          "ALL_PROXY": "http://127.0.0.1:9"}):
            with of_client.urlopen_no_redirect(urllib.request.Request(url + "/v1/health"), timeout=10,
                                               context=of_client.tls_context(url)) as r:
                self.assertEqual(json.loads(r.read()), {"ok": True, "forwarded": True})

    def test_the_plain_listener_is_this_users_alone(self):
        path = os.path.join(self.tmp, "plain.sock")
        srv = self.start(of_server.UnixServer(path, Hello))
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(10)
        c.connect(path)
        c.sendall(b"GET /x HTTP/1.0\r\n\r\n")
        data = b""
        while True:
            chunk = c.recv(4096)
            if not chunk:
                break
            data += chunk
        c.close()
        self.assertIn(b'"ok": true', data)
        self.assertEqual(srv.seen[-1][0], "/x")

    def test_a_live_server_or_another_file_on_the_path_refuses_the_start_and_a_stale_socket_is_taken(self):
        path = os.path.join(self.tmp, "s.sock")
        self.start(of_server.UnixServer(path, Hello))
        with self.assertRaises(OSError) as e:
            of_server.UnixServer(path, Hello)
        self.assertIn("already listens", str(e.exception))
        other = os.path.join(self.tmp, "file")
        open(other, "w").close()
        with self.assertRaises(OSError) as e:
            of_server.UnixServer(other, Hello)
        self.assertIn("not a socket", str(e.exception))
        stale = os.path.join(self.tmp, "stale.sock")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(stale)
        s.close()                                            # a socket file nobody listens on
        self.start(of_server.UnixServer(stale, Hello))
        self.assertEqual(os.stat(stale).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
