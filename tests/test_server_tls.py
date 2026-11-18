"""The TLS listener for forwarded clients and the client's pinned certificate (docs/server-several-machines.md 4.8;
of_secrets-review on organic-framework PR 350: while the forward is down, any user of the host could take the port and
receive a machine's bearer). A real handshake on 127.0.0.1 with certificates made in a temporary folder."""

import http.server
import json
import os
import shutil
import ssl
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_client  # noqa: E402
import of_server  # noqa: E402


class Hello(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"ok": True, "forwarded": getattr(self.server, "forwarded", False)}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@unittest.skipUnless(shutil.which("openssl"), "openssl makes the test certificates")
class TLSTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cert, self.key, self.sha = of_server.tls_init({"state_dir": os.path.join(self.tmp, "a")})
        self.other, _, _ = of_server.tls_init({"state_dir": os.path.join(self.tmp, "b")})
        self.srv = of_server.TLSServer(("127.0.0.1", 0), Hello, self.cert, self.key)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"https://127.0.0.1:{self.srv.server_address[1]}"
        self.home = os.path.join(self.tmp, "home")
        self.env = mock.patch.dict(os.environ, {"HOME": self.home})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.srv.shutdown()
        self.srv.server_close()

    def get(self):
        with urllib.request.urlopen(self.url + "/", timeout=10, context=of_client.tls_context(self.url)) as r:
            return json.loads(r.read())

    def pin(self, cert):
        dest = of_client.pin_path(self.url)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy(cert, dest)

    def test_the_pinned_certificate_alone_is_trusted(self):
        self.assertTrue(of_client.pin_path(self.url).startswith(self.home))
        with self.assertRaises(urllib.error.URLError):  # no pin: the system's CAs, which never signed it
            self.get()
        self.pin(self.other)
        with self.assertRaises(urllib.error.URLError):  # another server's certificate on the port: refused
            self.get()
        self.pin(self.cert)
        self.assertEqual(self.get(), {"ok": True, "forwarded": True})

    def test_the_pair_is_kept_and_the_key_is_the_owner_s_alone(self):
        again = of_server.tls_init({"state_dir": os.path.join(self.tmp, "a")})
        self.assertEqual(again, (self.cert, self.key, self.sha))
        self.assertEqual(os.stat(self.key).st_mode & 0o777, 0o600)
        self.assertEqual(len(self.sha), 64)

    def test_plain_http_to_a_pinned_address_is_taken_as_https(self):
        port = self.srv.server_address[1]
        plain = f"http://127.0.0.1:{port}"
        self.assertEqual(of_client.Client(plain).url, plain)  # no pin: as given
        self.pin(self.cert)
        self.assertEqual(of_client.Client(plain + "/").url, self.url)  # the relay's default, a fallback, no server:
        self.assertEqual(of_client.Client("http://127.0.0.1:1").url, "http://127.0.0.1:1")  # another port: as given
        c = of_client.Client(plain)
        self.assertEqual(c.url, self.url)
        with urllib.request.urlopen(c.url + "/", timeout=10, context=of_client.tls_context(c.url)) as r:
            self.assertTrue(json.loads(r.read())["ok"])

    def test_the_server_keeps_its_error_handler(self):
        self.assertIn("handle_error", of_server.Server.__dict__)  # PR 350: it had been moved into tls_init
        self.assertTrue(issubclass(of_server.TLSServer, of_server.Server))

    def test_a_silent_client_does_not_hold_the_listener(self):
        import socket
        quiet = socket.create_connection(("127.0.0.1", self.srv.server_address[1]))  # never starts a handshake
        try:
            self.pin(self.cert)
            self.assertTrue(self.get()["ok"])
        finally:
            quiet.close()


if __name__ == "__main__":
    unittest.main()
