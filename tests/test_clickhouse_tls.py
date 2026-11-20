"""ClickHouse over https with its certificate pinned (`clickhouse: ca`): the agent host's ClickHouse serves https
only, with a certificate made there (2026-11-20, of_agent-server), so a process that takes its loopback port while it
is down never reads the Authorization header. A real handshake with certificates made in a temporary folder."""

import http.server
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_route  # noqa: E402
import of_server  # noqa: E402
import of_server_export as ex  # noqa: E402


class Answer(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.auth = self.headers.get("Authorization")
        body = b"1\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@unittest.skipUnless(shutil.which("openssl"), "openssl makes the test certificates")
class PinnedClickHouseTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cert, self.key, _ = of_server.tls_init({"state_dir": os.path.join(self.tmp, "a")})
        self.other, _, _ = of_server.tls_init({"state_dir": os.path.join(self.tmp, "b")})
        self.srv = of_server.TLSServer(("127.0.0.1", 0), Answer, self.cert, self.key)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.pw = os.path.join(self.tmp, "pw")
        with open(self.pw, "w") as fh:
            fh.write("test-password\n")
        self.cfg = {"url": f"https://127.0.0.1:{self.srv.server_address[1]}", "user": "u", "password_file": self.pw}

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_pinned_certificate_alone_is_trusted(self):
        self.assertEqual(of_route.ClickHouse(dict(self.cfg, ca=self.cert)).post("SELECT 1", query_in_body=True),
                         b"1\n")
        self.srv.auth = None
        with self.assertRaises(Exception):
            of_route.ClickHouse(dict(self.cfg, ca=self.other)).post("SELECT 1", query_in_body=True)
        self.assertIsNone(self.srv.auth)                       # no header sent to a certificate not pinned
        ch = ex.ClickHouseHTTP(dict(self.cfg, ca=self.cert))
        self.assertEqual(b"".join(ch.query("SELECT 1", {})), b"1\n")
        with self.assertRaises(ex.ExportError):
            list(ex.ClickHouseHTTP(dict(self.cfg, ca=self.other)).query("SELECT 1", {}))

    def test_a_pin_with_plain_http_is_refused(self):
        with self.assertRaises(ValueError):
            of_route.tls_context({"url": "http://127.0.0.1:8123", "ca": self.cert})
        self.assertIsNone(of_route.tls_context({"url": "https://127.0.0.1:1"}))   # no ca: the system's, as before


if __name__ == "__main__":
    unittest.main()
