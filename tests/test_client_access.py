"""The Cloudflare Access service token the client sends to an organization's of-server (bin/of_client.py,
access_headers; docs/registry.md, orgs.yaml server_access): only over https, only to that organization's server and
drain_from URLs, from a mode-600 file, and never in an error message. No network: the opener is a stub."""

import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_client  # noqa: E402

FAKE_ID, FAKE_SECRET = "test-client-id.access", "test-client-value-not-real"


class AccessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.token = os.path.join(d, "access.json")
        self.write_token({"client_id": FAKE_ID, "client_secret": FAKE_SECRET})
        self.orgs = os.path.join(d, "orgs.yaml")
        with open(self.orgs, "w") as fh:
            fh.write(f"orgs:\n  - {{name: acme, path: a, server: 'https://of.acme.test/', "
                     f"drain_from: ['https://old.acme.test', local], server_access: '{self.token}'}}\n"
                     f"  - {{name: beta, path: b, server: 'https://of.beta.test'}}\n")
        self.saved = os.environ.get("OF_ORGS")
        os.environ["OF_ORGS"] = self.orgs
        of_client._ACCESS_MAP.clear()
        of_client._ACCESS_CACHE.clear()

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("OF_ORGS", None)
        else:
            os.environ["OF_ORGS"] = self.saved
        self.tmp.cleanup()

    def write_token(self, doc, mode=0o600):
        with open(os.open(self.token, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), "w") as fh:
            json.dump(doc, fh)
        os.chmod(self.token, mode)

    def test_sent_only_to_the_organizations_https_servers(self):
        want = {"CF-Access-Client-Id": FAKE_ID, "CF-Access-Client-Secret": FAKE_SECRET}
        self.assertEqual(of_client.access_headers("https://of.acme.test"), want)
        self.assertEqual(of_client.access_headers("https://old.acme.test/"), want)   # a drain after a rollback
        for url in ("http://of.acme.test", "http://127.0.0.1:18200", "https://of.beta.test", "https://evil.test"):
            self.assertEqual(of_client.access_headers(url), {}, url)

    def test_a_file_others_can_read_or_a_broken_one_is_refused_without_its_value(self):
        self.write_token({"client_id": FAKE_ID, "client_secret": FAKE_SECRET}, mode=0o644)
        with self.assertRaises(of_client.AccessError) as e:
            of_client.access_headers("https://of.acme.test")
        self.assertIn("mode 600", str(e.exception))
        self.write_token({"client_id": FAKE_ID}, mode=0o600)
        with self.assertRaises(of_client.AccessError) as e:
            of_client.access_headers("https://of.acme.test")
        self.assertIn("needs client_id and client_secret", str(e.exception))
        with open(self.token, "w") as fh:
            fh.write(FAKE_SECRET)   # not JSON
        with self.assertRaises(of_client.AccessError) as e:
            of_client.access_headers("https://of.acme.test")
        for err in (str(e.exception),):
            self.assertNotIn(FAKE_SECRET, err)
            self.assertNotIn(FAKE_ID, err)

    def test_every_request_carries_the_headers(self):
        seen = []

        class Resp(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(req, timeout=None, context=None):
            seen.append({k.lower(): v for k, v in req.header_items()})
            return Resp(b"{}")

        c = of_client.Client("https://of.acme.test")
        c.opener = opener
        c._request("GET", "/v1/health")
        self.assertEqual(seen[0].get("cf-access-client-id"), FAKE_ID)
        self.assertEqual(seen[0].get("cf-access-client-secret"), FAKE_SECRET)
        c = of_client.Client("https://of.beta.test")
        c.opener = opener
        c._request("GET", "/v1/health")
        self.assertNotIn("cf-access-client-id", seen[1])

    def test_no_credential_follows_a_redirect(self):
        """urllib copies a request's ordinary headers to a redirect's target, any host, even http."""
        import urllib.request
        reqs = []

        class Resp(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(req, timeout=None, context=None):
            reqs.append(req)
            return Resp(b"{}")

        c = of_client.Client("https://of.acme.test")
        c.opener = opener
        c._request("GET", "/v1/presence", token="tok-not-real")   # urllib redirects a GET; a POST's 307 it refuses
        req = reqs[0]
        sent = {k.lower(): v for k, v in req.header_items()}
        self.assertEqual(sent.get("cf-access-client-secret"), FAKE_SECRET)
        self.assertEqual(sent.get("authorization"), "Bearer tok-not-real")
        moved = urllib.request.HTTPRedirectHandler().redirect_request(
            req, io.BytesIO(), 302, "Found", {}, "http://elsewhere.test/v1/presence")
        carried = {k.lower(): v for k, v in moved.header_items()}
        for h in ("cf-access-client-id", "cf-access-client-secret", "authorization"):
            self.assertNotIn(h, carried, h)


    def test_a_redirect_is_refused_and_never_followed(self):
        """A real local server answers 302 to a second one; the second never sees a request."""
        import http.server
        import threading
        hits = []

        class Elsewhere(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(dict(self.headers))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        class Redirector(Elsewhere):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{far.server_port}/login")
                self.end_headers()

        far = http.server.HTTPServer(("127.0.0.1", 0), Elsewhere)
        near = http.server.HTTPServer(("127.0.0.1", 0), Redirector)
        for srv in (far, near):
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            self.addCleanup(srv.server_close)
            self.addCleanup(srv.shutdown)
        c = of_client.Client(f"http://127.0.0.1:{near.server_port}")
        with self.assertRaises(of_client.ServerError) as e:
            c._request("GET", "/v1/health", token="tok-not-real")
        self.assertIn("redirect", str(e.exception))
        self.assertEqual(hits, [])

if __name__ == "__main__":
    unittest.main()
