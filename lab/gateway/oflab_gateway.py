#!/usr/bin/env python3
"""oflab gateway: the only path from lab machines to the model provider (docs/lab.md, section 6).

A small stdlib reverse proxy, v0. It:
- accepts a per-run virtual key (Authorization: Bearer <key> or x-api-key) and refuses
  unknown, expired or over-budget keys;
- swaps in the Lab's real Anthropic key, read from the macOS Keychain (service
  `of-lab-anthropic`), which never enters a lab machine;
- streams the response back unchanged, and records every request/response pair:
  bodies as content-addressed zstd blobs, one row per pair in model_traffic.jsonl
  (the shape agreed with of_platform for the raw tier);
- charges each key by the usage the provider reports, at list prices;
- can inject an error for the next N requests of a key (the testbed's turn_error row).

Listens on 127.0.0.1 only. Keys: <state>/keys.json, managed with `oflab_gateway.py key ...`.
"""

import hashlib
import http.client
import http.server
import json
import os
import secrets
import subprocess
import sys
import threading
import time

try:
    import compression.zstd as zstd
except ImportError:
    zstd = None

STATE = os.path.expanduser(os.environ.get("OFLAB_GATEWAY_STATE", "~/.local/state/of/lab/gateway"))
UPSTREAM = os.environ.get("OFLAB_UPSTREAM", "api.anthropic.com")
PORT = int(os.environ.get("OFLAB_GATEWAY_PORT", "18400"))
# USD per million tokens: input, output, cache read, cache write (5 min = 1.25 x input).
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}
DEFAULT_PRICE = (10.0, 50.0, 1.0, 12.5)  # unknown model: charge high, never low
LOCK = threading.Lock()


def _path(*p):
    d = os.path.join(STATE, *p[:-1])
    os.makedirs(d, mode=0o700, exist_ok=True)
    return os.path.join(d, p[-1])


def load_keys():
    try:
        with open(_path("keys.json")) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


def save_keys(keys):
    tmp = _path("keys.json.tmp")
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(keys, fh, indent=1)
    os.replace(tmp, _path("keys.json"))


def real_key():
    return subprocess.run(["security", "find-generic-password", "-s", "of-lab-anthropic", "-w"],
                          capture_output=True, text=True, check=True).stdout.strip()


def blob(data):
    digest = hashlib.sha256(data).hexdigest()
    p = _path("blobs", digest[:2], digest + (".zst" if zstd else ""))
    if not os.path.exists(p):
        with open(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as fh:
            fh.write(zstd.compress(data) if zstd else data)
    return digest


def usage_of(body, streamed):
    """(model, input, output, cache_read, cache_write) from a response body."""
    u, model = {}, None
    try:
        if streamed:
            for line in body.decode("utf-8", "ignore").splitlines():
                if not line.startswith("data:"):
                    continue
                ev = json.loads(line[5:].strip() or "{}")
                msg = ev.get("message") or {}
                model = msg.get("model") or model
                for src in (msg.get("usage") or {}, ev.get("usage") or {}):
                    for k, v in src.items():
                        if isinstance(v, int):
                            u[k] = max(u.get(k, 0), v)
        else:
            j = json.loads(body or b"{}")
            model, u = j.get("model"), j.get("usage") or {}
    except Exception:
        pass
    return (model, u.get("input_tokens", 0), u.get("output_tokens", 0),
            u.get("cache_read_input_tokens", 0), u.get("cache_creation_input_tokens", 0))


def cost(model, i, o, cr, cw):
    p = PRICES.get(model or "", DEFAULT_PRICE)
    return (i * p[0] + o * p[1] + cr * p[2] + cw * p[3]) / 1e6


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _deny(self, code, msg):
        body = json.dumps({"type": "error", "error": {"type": "oflab_gateway", "message": msg}}).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self):
        n = int(self.headers.get("content-length") or 0)
        req = self.rfile.read(n) if n else b""
        auth = self.headers.get("authorization", "")
        vkey = auth[7:].strip() if auth.lower().startswith("bearer ") else self.headers.get("x-api-key", "")
        with LOCK:
            keys = load_keys()
            k = keys.get(vkey)
            if not k:
                return self._deny(401, "unknown lab key")
            if time.time() > k["expires"]:
                return self._deny(401, "lab key expired")
            if k.get("spent", 0.0) >= k["usd_cap"]:
                return self._deny(429, "lab run budget exhausted")
            if k.get("inject_errors", 0) > 0:
                k["inject_errors"] -= 1
                save_keys(keys)
                return self._deny(529, "injected by the lab (overloaded)")
        headers = {h: v for h, v in self.headers.items()
                   if h.lower() not in ("host", "authorization", "x-api-key", "content-length", "connection",
                                        "accept-encoding")}
        headers["x-api-key"] = real_key()
        headers["content-length"] = str(len(req))
        t0 = time.time()
        conn = http.client.HTTPSConnection(UPSTREAM, timeout=600)
        conn.request(self.command, self.path, body=req, headers=headers)
        resp = conn.getresponse()
        streamed = "text/event-stream" in (resp.getheader("content-type") or "")
        self.send_response(resp.status)
        for h, v in resp.getheaders():
            if h.lower() not in ("transfer-encoding", "connection", "content-length", "content-encoding"):
                self.send_header(h, v)
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        chunks = []
        while True:
            chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
            if not chunk:
                break
            chunks.append(chunk)
            self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        conn.close()
        body = b"".join(chunks)
        model, i, o, cr, cw = usage_of(body, streamed)
        usd = cost(model, i, o, cr, cw)
        try:
            sid = json.loads(req or b"{}").get("metadata", {}).get("user_id")
        except Exception:
            sid = None
        row = {"received_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)), "run_id": k["run_id"],
               "session_id": sid, "path": self.path, "status": resp.status, "model": model,
               "request_sha256": blob(req), "response_sha256": blob(body),
               "latency_ms": int((time.time() - t0) * 1000), "input_tokens": i, "output_tokens": o,
               "cache_read_tokens": cr, "cache_write_tokens": cw, "usd": round(usd, 6)}
        with LOCK:
            with open(_path("model_traffic.jsonl"), "a") as fh:
                fh.write(json.dumps(row) + "\n")
            keys = load_keys()
            if vkey in keys:
                keys[vkey]["spent"] = round(keys[vkey].get("spent", 0.0) + usd, 6)
                save_keys(keys)

    do_POST = _proxy
    do_GET = _proxy


def cmd_key(argv):
    keys = load_keys()
    if argv[:1] == ["new"]:
        run_id, cap, hours = argv[1], float(argv[2]), float(argv[3] if len(argv) > 3 else 6)
        vkey = "oflab-" + secrets.token_urlsafe(24)
        keys[vkey] = {"run_id": run_id, "usd_cap": cap, "expires": time.time() + hours * 3600, "spent": 0.0}
        save_keys(keys)
        print(vkey)
    elif argv[:1] == ["revoke"]:
        run_id = argv[1]
        for v in [v for v, k in keys.items() if k["run_id"] == run_id]:
            del keys[v]
        save_keys(keys)
    elif argv[:1] == ["inject"]:
        for k in keys.values():
            if k["run_id"] == argv[1]:
                k["inject_errors"] = int(argv[2])
        save_keys(keys)
    else:
        for k in keys.values():
            print(f'{k["run_id"]}: spent ${k.get("spent", 0):.4f} of ${k["usd_cap"]}, '
                  f'expires {time.strftime("%H:%M", time.localtime(k["expires"]))}')


def main(argv):
    if argv[:1] == ["key"]:
        return cmd_key(argv[1:])
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"oflab gateway on 127.0.0.1:{PORT}, upstream {UPSTREAM}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
