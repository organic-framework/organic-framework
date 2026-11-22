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
        getattr(self.server, "seen", []).append((self.path, self.headers.get("Authorization")))
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
        self.srv.seen = []
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

    def test_the_scheme_is_read_without_case_and_the_client_compares_the_pin_byte_for_byte(self):
        self.pin(self.cert)
        loud = f"HTTP://127.0.0.1:{self.srv.server_address[1]}"
        self.assertEqual(of_client.pinned_url(loud)[:8], "https://")
        c = of_client.Client(self.url)
        self.assertEqual(c._request("GET", "/v1/x")[0], 200)
        with mock.patch.object(of_client, "pin_sha256", lambda url: "0" * 64):  # a pin that is not this certificate
            with self.assertRaises(Exception):
                of_client.Client(self.url)._request("GET", "/v1/x")

    def test_the_health_checks_send_the_bearer_only_through_the_pin(self):
        """of_secrets-review on PR 350: of_health took the https token and sent it to the plain http address."""
        import of_health
        plain = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.pin(self.cert)
        of_health.server_checks(url=plain, token="tok-1")
        self.assertIsNotNone(of_health.server_health(url=plain, timeout=10))
        self.assertIn(("/v1/health/checks", "Bearer tok-1"), self.srv.seen)  # arrived, over the pinned TLS
        self.assertIn(("/v1/health", None), self.srv.seen)

    def test_a_server_named_with_a_socket_is_reached_through_it_alone(self):
        """docs/contours.md §1, of_agent-server msg 01a1f098-02bb: on the host the forward ends in a Unix socket, and
        no OF client there uses TCP to that address, so whoever holds the port gets nothing."""
        import socketserver

        class UnixTLS(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

            def __init__(self, path, handler, cert, key):
                super().__init__(path, handler)
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(cert, key)
                self.socket = ctx.wrap_socket(self.socket, server_side=True)

        short = tempfile.mkdtemp(dir="/tmp")
        path = os.path.join(short, "s.sock")
        srv = UnixTLS(path, Hello, self.cert, self.key)
        srv.seen = []
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (srv.shutdown(), srv.server_close(), shutil.rmtree(short, ignore_errors=True)))
        url = "https://127.0.0.1:18299"  # nothing listens on that port: a TCP connection would be refused
        dest = of_client.pin_path(url)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy(self.cert, dest)
        with open(dest[:-len(".pem")] + ".socket", "w") as fh:
            fh.write(path + "\n")
        self.assertEqual(of_client.socket_path(url), path)
        status, body = of_client.Client("http://127.0.0.1:18299")._request("GET", "/v1/x", token="tok-s")
        self.assertEqual((status, body["ok"]), (200, True))
        self.assertIn(("/v1/x", "Bearer tok-s"), srv.seen)  # over the socket, with TLS and the pin

    def test_a_server_named_with_a_tcp_target_is_reached_there_under_its_own_name(self):
        """The founder through of_conductor (prompt 01a20389-14b4; hand-over c004dfc6): the Mac reaches the agent
        host's server over its tailnet port, not the SSH bridge. The socket file then names `tcp:<address>:<port>`;
        the TLS name and the pin stay the URL's, and a name to look up is never taken."""
        url = "https://127.0.0.1:18297"  # nothing listens on that port: only the target answers
        dest = of_client.pin_path(url)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy(self.cert, dest)
        side = dest[:-len(".pem")] + ".socket"
        with open(side, "w") as fh:
            fh.write(f"tcp:127.0.0.1:{self.srv.server_address[1]}\n")
        self.assertEqual(of_client.connect_target(url), ("tcp", ("127.0.0.1", self.srv.server_address[1])))
        self.assertIsNone(of_client.socket_path(url))
        status, body = of_client.Client("http://127.0.0.1:18297")._request("GET", "/v1/x", token="tok-t")
        self.assertEqual((status, body["ok"]), (200, True))
        self.assertIn(("/v1/x", "Bearer tok-t"), self.srv.seen)  # at the target, with TLS and the pin
        for bad in ("tcp:example.invalid:443", "tcp:127.0.0.1:0", "tcp:127.0.0.1:x", "tcp:"):
            with open(side, "w") as fh:
                fh.write(bad + "\n")
            self.assertIsNone(of_client.connect_target(url), bad)
        with open(side, "w") as fh:                                      # another certificate there: refused
            fh.write(f"tcp:127.0.0.1:{self.srv.server_address[1]}\n")
        shutil.copy(self.other, dest)
        with self.assertRaises(Exception):
            of_client.Client("http://127.0.0.1:18297")._request("GET", "/v1/x")

    def test_of_server_route_sets_the_route_for_an_enrolled_machine(self):
        """of_review on organic-framework PR 521: a command sets the route, the owner's alone, with no new invite."""
        import argparse
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("of_cli_route", os.path.join(os.path.dirname(
            os.path.abspath(__file__)), "..", "bin", "of"))
        cli = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli_route", loader))
        loader.exec_module(cli)
        import of_asks_answer
        url = "https://127.0.0.1:18296"
        args = lambda via: argparse.Namespace(action="route", path=url, via=via)          # noqa: E731
        with mock.patch.object(of_asks_answer, "not_the_owner", lambda: False):
            with self.assertRaises(cli.OfError):                                          # no pin yet
                cli._server_route(args(f"tcp:127.0.0.1:{self.srv.server_address[1]}"))
            dest = of_client.pin_path(url)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy(self.cert, dest)
            cli._server_route(args(f"tcp:127.0.0.1:{self.srv.server_address[1]}"))
            status, body = of_client.Client("http://127.0.0.1:18296")._request("GET", "/v1/x")
            self.assertEqual((status, body["ok"]), (200, True))                           # through the new route
            side = dest[:-len(".pem")] + ".socket"
            self.assertEqual(oct(os.stat(side).st_mode & 0o777), "0o600")
            for bad in ("tcp:example.invalid:443", "relative/path", ""):
                with self.assertRaises(cli.OfError):
                    cli._server_route(args(bad))
            cli._server_route(args("direct"))
            self.assertFalse(os.path.exists(side))
        with mock.patch.object(of_asks_answer, "not_the_owner", lambda: True):           # an agent's session
            with self.assertRaises(cli.OfError) as cm:
                cli._server_route(args(f"tcp:127.0.0.1:{self.srv.server_address[1]}"))
            self.assertIn("owner's", str(cm.exception))
        self.assertFalse(os.path.exists(side))

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
