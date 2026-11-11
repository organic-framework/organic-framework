"""Client side of remote enrollment against a fake of-server implementing docs/server.md."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
OF = os.path.join(HERE, "..", "bin", "of")


class Fake(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/v1/terms":
            return self._send(200, {"current": "terms-2026-11-05", "text_url": "https://example/terms"})
        if self.path.startswith("/v1/receipts"):
            return self._send(200, {"receipts": [{"seq": 1, "batch_id": "b", "state": "committed",
                                                  "by_org": {"acme": 3}, "tiers": {"acme": ["typed"]}}]})
        self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        Fake.seen.append((self.path, body, self.headers.get("Authorization")))
        if self.path == "/v1/auth/challenge":
            return self._send(200, {"nonce": "n", "namespace": "of-server"})
        if self.path == "/v1/auth/token":
            return self._send(200, {"token": "t", "expires": 9e12})
        if self.path == "/v1/messages":
            return self._send(202, {"msg_id": body["msg_id"], "state": "queued", "authority": "none"})
        if self.path == "/v1/invites":
            return self._send(200, {"code": "INV-123", "expires": "2026-11-08"})
        if self.path == "/v1/enroll":
            if body.get("code") != "INV-123":
                return self._send(410, {"type": "gone"})
            raw = [o for o, c in body["consent"].items() if "raw" in c["tiers"]]
            return self._send(200, {"machine_id": body["machine_id"], "orgs": list(body["consent"]), "raw_orgs": raw})
        self._send(404, {})

    def do_DELETE(self):
        Fake.seen.append((self.path, None, self.headers.get("Authorization")))
        self._send(200, {"revoked": ["m1", "m2"]})

    def do_PUT(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        Fake.seen.append((self.path, body, self.headers.get("Authorization")))
        self._send(200, body)


class EnrollTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.env = dict(os.environ, OF_CONFIG_DIR=os.path.join(self.tmp.name, "config"),
                        OF_STATE=os.path.join(self.tmp.name, "state"), SSH_AUTH_SOCK="")
        self.env.pop("OF_ORGS", None)
        Fake.seen = []

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def of(self, *args):
        return subprocess.run([sys.executable, OF, *args], env=self.env, capture_output=True, text=True, timeout=60)

    def test_enroll_writes_server_per_org_and_consent(self):
        r = self.of("enroll", self.url, "--code", "INV-123", "--org", "acme", "--org", "other", "--raw", "other",
                    "--yes")
        self.assertEqual(r.returncode, 0, r.stderr)
        sent = next(b for p, b, _ in Fake.seen if p == "/v1/enroll")
        self.assertEqual(sent["consent"]["acme"], {"tiers": ["typed"], "terms": "terms-2026-11-05"})
        self.assertEqual(sent["consent"]["other"]["tiers"], ["typed", "raw"])
        self.assertTrue(sent["pubkey"].startswith("ssh-ed25519 "))
        import yaml
        doc = yaml.safe_load(open(os.path.join(self.tmp.name, "config", "orgs.yaml")))
        self.assertEqual({o["name"]: o["server"] for o in doc["orgs"]}, {"acme": self.url, "other": self.url})
        self.assertIn("raw tier: other", r.stdout)

    def test_bad_code_fails_cleanly(self):
        r = self.of("enroll", self.url, "--code", "WRONG", "--org", "x", "--yes")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("410", r.stderr + r.stdout)

    def test_invite_consent_shipped(self):
        os.makedirs(os.path.join(self.tmp.name, "config"))
        with open(os.path.join(self.tmp.name, "config", "orgs.yaml"), "w") as fh:
            fh.write(f"orgs:\n  - {{name: acme, server: '{self.url}'}}\n")
        with open(os.path.join(self.tmp.name, "config", "ship.yaml"), "w") as fh:
            fh.write(f"server_url: '{self.url}'\n")
        r = self.of("invite", "--server", self.url, "--org", "acme", "--ttl", "48h")
        self.assertIn(f"of enroll {self.url} --code INV-123 --org acme", r.stdout)
        inv = next(b for p, b, _ in Fake.seen if p == "/v1/invites")
        self.assertEqual(inv["ttl_s"], 48 * 3600)
        r = self.of("consent", "--raw", "off")
        put = next(b for p, b, _ in Fake.seen if p == "/v1/consent")
        self.assertEqual(put, {"acme": {"tiers": ["typed"], "terms": "terms-2026-11-05"}})
        r = self.of("shipped")
        self.assertIn("acme", r.stdout)
        self.assertIn("3 records", r.stdout)
        self.of("invite", "--server", self.url, "--org", "acme", "--person", "dana")
        self.assertEqual([b for p, b, _ in Fake.seen if p == "/v1/invites"][-1]["person"], "dana")
        r = self.of("machines", "revoke", "--server", self.url, "--person", "dana")
        self.assertIn("revoked: m1, m2", r.stdout)
        self.assertIn(("/v1/machines?person=dana", None), [(p, b) for p, b, _ in Fake.seen])


if __name__ == "__main__":
    unittest.main()

    def test_send_resolves_names_and_posts_to_the_orgs_server(self):
        cfg = os.path.join(self.tmp.name, "config")
        os.makedirs(os.path.join(self.tmp.name, "repo", "registry"))
        with open(os.path.join(self.tmp.name, "repo", "registry", "positions.yaml"), "w") as fh:
            fh.write("organization: mt\npositions:\n"
                     "  - {name: acme_org, system: mt, role: org, aliases: [org-keeper]}\n"
                     "  - {name: acme_owner, system: mt, role: owner}\n")
        os.makedirs(cfg)
        with open(os.path.join(cfg, "orgs.yaml"), "w") as fh:
            fh.write(f"orgs:\n  - {{name: mt, path: '{self.tmp.name}/repo', server: '{self.url}'}}\n")
        r = self.of("send", "org-keeper", "please check", "--from", "acme_owner")
        self.assertEqual(r.returncode, 0, r.stderr)
        sent = next(b for p, b, _ in Fake.seen if p == "/v1/messages")
        self.assertEqual((sent["to"], sent["from"], sent["text"]), ("mt:acme_org", "mt:acme_owner", "please check"))
        self.assertIn("queued:", r.stdout)



sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))


class RetryTest(unittest.TestCase):
    def client(self, outcomes):
        import io
        import urllib.error
        import of_client
        self.addCleanup(setattr, of_client, "_backoff", of_client._backoff)
        of_client._backoff = lambda i, retry_after=None: 0
        c = of_client.Client.__new__(of_client.Client)
        c.url, calls = "http://x", []

        def opener(req, timeout=None, context=None):
            calls.append(req.full_url)
            o = outcomes.pop(0)
            if isinstance(o, int):
                raise urllib.error.HTTPError(req.full_url, o, "x", {"Retry-After": "0"}, io.BytesIO(b"{}"))
            if isinstance(o, BaseException):
                raise o
            class R:
                status = 200
                def read(self):
                    return b'{"ok": 1}'
                def __enter__(self):
                    return self
                def __exit__(self, *a):
                    return False
            return R()
        c.opener = opener
        return c, calls

    def test_transient_errors_retry_then_succeed(self):
        import http.client
        import urllib.error
        c, calls = self.client([http.client.RemoteDisconnected("x"), urllib.error.URLError(ConnectionRefusedError()),
                                503, "ok"])
        self.assertEqual(c._request("GET", "/v1/x"), (200, {"ok": 1}))
        self.assertEqual(len(calls), 4)

    def test_gives_up_with_one_clear_error(self):
        import of_client
        c, calls = self.client([ConnectionResetError()] * 4)
        with self.assertRaises(of_client.TransportError) as cm:
            c._request("GET", "/v1/x")
        self.assertIn("unreachable after 4 tries", str(cm.exception))

    def test_enroll_410_after_lost_response(self):
        import of_client
        c, _ = self.client([ConnectionResetError(), 410])
        with self.assertRaises(of_client.ServerError) as cm:
            c._request("POST", "/v1/enroll", {"code": "c"})
        self.assertIn("of shipped", str(cm.exception))
        c, _ = self.client([404])
        with self.assertRaises(of_client.ServerError) as cm:
            c._request("GET", "/v1/x")
        self.assertEqual(cm.exception.status, 404)
