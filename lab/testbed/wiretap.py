#!/usr/bin/env python3
"""A transparent logging proxy for lab clients: what actually leaves the machine.

Listens on 127.0.0.1:<port>, forwards every request to <upstream>, and appends each
request body (decompressed: zstd or gzip) to <log> as one JSON line with the path. Used by
lab-profile2-v0 to check consent on the wire: with typed-only consent, no raw transcript
or hook content may appear in any request.
Usage: wiretap.py <port> <upstream base URL> <log file>
"""
import gzip
import http.client
import http.server
import json
import sys
import urllib.parse

try:
    import compression.zstd as zstd
except ImportError:
    zstd = None

PORT, UP, LOG = int(sys.argv[1]), urllib.parse.urlparse(sys.argv[2]), sys.argv[3]


def decode(body, enc):
    try:
        if enc == "zstd" and zstd:
            return zstd.decompress(body)
        if enc == "gzip":
            return gzip.decompress(body)
    except Exception:
        pass
    return body


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _go(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n) if n else b""
        with open(LOG, "a") as fh:
            fh.write(json.dumps({"method": self.command, "path": self.path,
                                 "body": decode(body, self.headers.get("content-encoding", "")).decode("utf-8", "replace")}) + "\n")
        hdrs = {k: v for k, v in self.headers.items() if k.lower() not in ("host", "connection")}
        c = http.client.HTTPConnection(UP.hostname, UP.port or 80, timeout=60)
        c.request(self.command, self.path, body=body, headers=hdrs)
        r = c.getresponse()
        data = r.read()
        self.send_response(r.status)
        for k, v in r.getheaders():
            if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(k, v)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_DELETE = _go


http.server.ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
