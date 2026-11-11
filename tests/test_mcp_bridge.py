"""of_mcp_bridge (bin/of_mcp_bridge.py): stdio to Streamable HTTP, against a local fake server. No network."""
import io
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))

import of_mcp_bridge  # noqa: E402

KEY = "k" * 40
SEEN = []


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append((self.path, self.headers.get("Mcp-Session-Id"), body.get("method")))
        if not self.path.startswith(f"/mcp/{KEY}/"):
            self.send_response(401)
            self.end_headers()
            return
        if "id" not in body:  # a notification
            self.send_response(202)
            self.end_headers()
            return
        if body["method"] == "initialize":
            out = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-06-18"}})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Mcp-Session-Id", "s-1")
            self.end_headers()
            self.wfile.write(out.encode())
            return
        out = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [{"name": "list_invoices"}]}})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(f"event: message\ndata: {out}\n\n".encode())


class BridgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def run_bridge(self, url, lines):
        out = io.StringIO()
        of_mcp_bridge.Bridge(url, timeout=10, out=out).run([json.dumps(x) for x in lines])
        return [json.loads(x) for x in out.getvalue().splitlines()], out.getvalue()

    def test_json_and_sse_answers_with_the_session_id(self):
        SEEN.clear()
        msgs, _ = self.run_bridge(f"{self.base}/mcp/{KEY}/message", [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
        by_id = {m["id"]: m for m in msgs}
        self.assertEqual(by_id[1]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(by_id[2]["result"]["tools"][0]["name"], "list_invoices")
        self.assertEqual(len(msgs), 2)  # the notification's 202 sends nothing back
        self.assertTrue(all(s == "s-1" for _, s, m in SEEN if m != "initialize"))

    def test_a_refused_request_gets_an_error_naming_the_host_only(self):
        msgs, raw = self.run_bridge(f"{self.base}/mcp/wrong{KEY}/message",
                                    [{"jsonrpc": "2.0", "id": 7, "method": "tools/list"}])
        self.assertEqual(msgs[0]["id"], 7)
        self.assertIn("HTTP 401", msgs[0]["error"]["message"])
        self.assertNotIn(KEY, raw)

    def test_sse_parser(self):
        lines = [b'data: {"a":\n', b'data: 1}\n', b"\n", b": comment\n", b'data: {"b":2}\n']
        self.assertEqual(list(of_mcp_bridge.sse_messages(lines)), [{"a": 1}, {"b": 2}])


class OAuthTest(unittest.TestCase):
    def test_refresh_keeps_the_new_refresh_token_and_mode_600(self):
        import tempfile
        import time
        calls = []

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opener(req, timeout=None):
            calls.append(dict(x.split("=", 1) for x in req.data.decode().split("&")))
            return Resp(json.dumps({"access_token": "a2", "expires_in": 3600, "refresh_token": "r2"}).encode())

        with tempfile.TemporaryDirectory() as d:
            o = of_mcp_bridge.OAuth(d, opener=opener)
            o.save({"client_id": "c", "refresh_token": "r1", "token_endpoint": "https://as.example/token",
                    "access_token": "a1", "expires_at": time.time() - 1})
            self.assertEqual(o.token(), "a2")
            self.assertEqual(calls[0]["grant_type"], "refresh_token")
            self.assertEqual(calls[0]["refresh_token"], "r1")
            self.assertEqual(o.load()["refresh_token"], "r2")
            self.assertEqual(os.stat(o.path).st_mode & 0o777, 0o600)
            self.assertEqual(o.token(), "a2")  # still fresh: no second refresh
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
