#!/usr/bin/env python3
"""A remote MCP server (Streamable HTTP) as a local stdio server, its credential kept inside this process
(docs/tools.md §4). Owner: of_tools.

Some services put the credential in the server's URL or in a header. Written into an MCP config, it would sit in a
file; expanded from the session's environment, every shell command of the session could read it. The bridge reads it
itself, from a file (mode 600) or a variable that `of secret run` sets for this process alone, and never prints it.

  of_mcp_bridge.py --url-file PATH | --url-env NAME  [--header-file NAME=PATH]… [--oauth-store DIR] [--timeout 120]
      serve: stdio on one side, the remote server on the other.
  of_mcp_bridge.py login --url-file PATH --oauth-store DIR [--scopes "a b"] [--no-open]
      One sign-in by the person whose identity the tool acts as, in a plain terminal: discovers the server's
      authorization server (RFC 9728, RFC 8414), registers a client (RFC 7591), opens the sign-in page with PKCE
      (RFC 7636) and a callback on 127.0.0.1, and writes the client and its refresh token to DIR/oauth.json (mode
      600). Nothing is printed but the sign-in link and the result. The token is the tool's, not a Claude account's,
      so every account and every session of the position uses it.
  of_mcp_bridge.py call --url-file PATH [--oauth-store DIR] <tool> ['<json arguments>']
      One tool call from a shell (for a session started before its profile had the tool); prints the result.

stdin and stdout carry the client's JSON-RPC messages, one per line (MCP stdio transport). Each message is POSTed
to the URL with `Accept: application/json, text/event-stream`; a JSON body or the events of an SSE body go back on
stdout; `Mcp-Session-Id` from the server is sent on every later request (MCP specification 2025-06-18, Transports,
Streamable HTTP). A failed request with an id gets a JSON-RPC error naming the host only, so the client never hangs.
Server-initiated messages on a GET stream are not read (none of the tools this serves sends them).
"""
import argparse
import base64
import io
import fcntl
import hashlib
import json
import secrets
import time
import urllib.parse
import os
import re
import sys
import threading
import urllib.error
import urllib.request

PROTOCOL = "2025-06-18"


def host_of(url):
    m = re.match(r"^([a-z]+://[^/?#]+)", url or "")
    return m.group(1) if m else "(no URL)"


def read_secret(path=None, env=None):
    if path:
        with open(os.path.expanduser(path)) as fh:
            return fh.read().strip()
    v = os.environ.get(env or "")
    if not v:
        raise SystemExit(f"of_mcp_bridge: {env} is not set")
    return v.strip()


def sse_messages(lines):
    """JSON messages from an SSE body (an iterable of byte lines)."""
    data = []
    for raw in lines:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data:
                try:
                    yield json.loads("\n".join(data))
                except ValueError:
                    pass
                data = []
            continue
        if line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
    if data:
        try:
            yield json.loads("\n".join(data))
        except ValueError:
            pass


def _post_form(url, fields, opener=None, timeout=30):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "Accept": "application/json"})
    with (opener or urllib.request.urlopen)(req, timeout=timeout) as r:
        return json.loads(r.read())


def _get_json(url, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}),
                                timeout=timeout) as r:
        return json.loads(r.read())


class OAuth:
    """The tool's own OAuth client and tokens in DIR/oauth.json (mode 600), refreshed under a lock so that several
    sessions of one position share one refresh token."""

    def __init__(self, store, opener=None):
        self.dir = os.path.expanduser(store)
        self.path = os.path.join(self.dir, "oauth.json")
        self.opener = opener

    def load(self):
        with open(self.path) as fh:
            return json.load(fh)

    def save(self, doc):
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        tmp = f"{self.path}.{os.getpid()}"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(doc, fh)
        os.replace(tmp, self.path)

    def token(self, force=False):
        doc = self.load()
        if not force and doc.get("access_token") and doc.get("expires_at", 0) > time.time() + 60:
            return doc["access_token"]
        with open(os.open(self.path + ".lock", os.O_WRONLY | os.O_CREAT, 0o600)) as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            doc = self.load()  # another session may have refreshed meanwhile
            if not force and doc.get("access_token") and doc.get("expires_at", 0) > time.time() + 60:
                return doc["access_token"]
            fields = {"grant_type": "refresh_token", "refresh_token": doc["refresh_token"],
                      "client_id": doc["client_id"]}
            if doc.get("client_secret"):
                fields["client_secret"] = doc["client_secret"]
            if doc.get("resource"):
                fields["resource"] = doc["resource"]
            t = _post_form(doc["token_endpoint"], fields, self.opener)
            doc["access_token"] = t["access_token"]
            doc["expires_at"] = time.time() + int(t.get("expires_in") or 3600)
            if t.get("refresh_token"):
                doc["refresh_token"] = t["refresh_token"]
            self.save(doc)
            return doc["access_token"]


def discover(url):
    """(resource metadata, authorization server metadata) of a protected MCP server."""
    m = re.match(r"^([a-z]+://[^/?#]+)", url)
    pr = _get_json(m.group(1) + "/.well-known/oauth-protected-resource")
    asv = (pr.get("authorization_servers") or [m.group(1)])[0].rstrip("/")
    for w in ("/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
        try:
            return pr, _get_json(asv + w)
        except (urllib.error.URLError, ValueError):
            continue
    raise SystemExit(f"of_mcp_bridge: {host_of(url)} publishes no authorization server metadata")


def login(url, store, scopes=None, open_browser=True, port=0):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    pr, md = discover(url)
    got = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            from of_locale import tr  # the person's language (owner.yaml), English by default
            self.wfile.write(tr("Done, you may close this tab.").encode())

    srv = HTTPServer(("127.0.0.1", port), Callback)
    redirect = f"http://127.0.0.1:{srv.server_address[1]}/callback"
    reg = json.loads(urllib.request.urlopen(urllib.request.Request(
        md["registration_endpoint"], method="POST", headers={"Content-Type": "application/json"},
        data=json.dumps({"client_name": "Organic Framework tool", "redirect_uris": [redirect],
                         "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
                         "token_endpoint_auth_method": "none"}).encode()), timeout=30).read())
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    scope = scopes or " ".join(pr.get("scopes_supported") or md.get("scopes_supported") or [])
    q = {"response_type": "code", "client_id": reg["client_id"], "redirect_uri": redirect, "state": state,
         "code_challenge": challenge, "code_challenge_method": "S256"}
    if scope:
        q["scope"] = scope
    if pr.get("resource"):
        q["resource"] = pr["resource"]
    link = md["authorization_endpoint"] + ("&" if "?" in md["authorization_endpoint"] else "?") \
        + urllib.parse.urlencode(q)
    print("Sign in and approve in the browser. If no tab opened, open this link:\n" + link, flush=True)
    if open_browser:
        import subprocess
        subprocess.run(["open", link], check=False)
    while "code" not in got and "error" not in got:
        srv.handle_request()
    srv.server_close()
    if got.get("error") or got.get("state") != state:
        raise SystemExit(f"of_mcp_bridge: sign-in failed ({got.get('error') or 'state mismatch'})")
    fields = {"grant_type": "authorization_code", "code": got["code"], "redirect_uri": redirect,
              "client_id": reg["client_id"], "code_verifier": verifier}
    if reg.get("client_secret"):
        fields["client_secret"] = reg["client_secret"]
    if pr.get("resource"):
        fields["resource"] = pr["resource"]
    t = _post_form(md["token_endpoint"], fields)
    if not t.get("refresh_token"):
        raise SystemExit("of_mcp_bridge: the server gave no refresh token; the tool could not renew its access")
    OAuth(store).save({"client_id": reg["client_id"], "client_secret": reg.get("client_secret"),
                       "token_endpoint": md["token_endpoint"], "resource": pr.get("resource"), "scope": scope,
                       "access_token": t["access_token"], "refresh_token": t["refresh_token"],
                       "expires_at": time.time() + int(t.get("expires_in") or 3600), "signed_in": time.time()})
    print(f"Signed in: the tool's token is in {os.path.join(store, 'oauth.json')} (mode 600). Scopes: {scope}")


class Bridge:
    def __init__(self, url, headers=None, timeout=120, out=None, opener=None, oauth=None):
        self.url, self.headers, self.timeout, self.oauth = url, dict(headers or {}), timeout, oauth
        self.session, self.out = None, out or sys.stdout
        self.lock = threading.Lock()
        self.open = opener or urllib.request.urlopen

    def emit(self, msg):
        with self.lock:
            self.out.write(json.dumps(msg, separators=(",", ":")) + "\n")
            self.out.flush()

    def error(self, msg, text):
        if isinstance(msg, dict) and msg.get("id") is not None and "method" in msg:
            self.emit({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32000, "message": text}})

    def post(self, msg, retried=False):
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
             "MCP-Protocol-Version": PROTOCOL, **self.headers}
        if self.session:
            h["Mcp-Session-Id"] = self.session
        if self.oauth:
            h["Authorization"] = "Bearer " + self.oauth.token(force=retried)
        req = urllib.request.Request(self.url, data=json.dumps(msg).encode(), headers=h, method="POST")
        try:
            with self.open(req, timeout=self.timeout) as r:
                sid = r.headers.get("Mcp-Session-Id")
                if sid:
                    self.session = sid
                ctype = (r.headers.get("Content-Type") or "").lower()
                if r.status == 202:
                    return
                if "text/event-stream" in ctype:
                    for m in sse_messages(r):
                        self.emit(m)
                else:
                    body = r.read()
                    if body.strip():
                        m = json.loads(body)
                        for x in (m if isinstance(m, list) else [m]):
                            self.emit(x)
        except urllib.error.HTTPError as e:
            if e.code == 401 and self.oauth and not retried:
                return self.post(msg, retried=True)
            self.error(msg, f"{host_of(self.url)} answered HTTP {e.code}")
        except (urllib.error.URLError, OSError, ValueError) as e:
            self.error(msg, f"{host_of(self.url)}: {type(e).__name__}")

    def run(self, inp=None):
        threads = []
        for line in inp or sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("method") == "initialize":
                self.post(msg)  # the session id must exist before anything else is sent
                continue
            t = threading.Thread(target=self.post, args=(msg,), daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(self.timeout)


def call(bridge, tool, arguments):
    out = io.StringIO()
    bridge.out = out
    bridge.run([json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "of_mcp_bridge", "version": "1"}}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})])
    bridge.run([json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list" if tool == "--list" else "tools/call",
                            "params": {} if tool == "--list" else {"name": tool, "arguments": arguments}})])
    for line in out.getvalue().splitlines():
        m = json.loads(line)
        if m.get("id") == 2:
            return m
    return {"error": {"message": "no answer"}}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = argv.pop(0) if argv and argv[0] in ("login", "call", "serve") else "serve"
    ap = argparse.ArgumentParser(prog=f"of_mcp_bridge.py {mode}")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--url-file")
    g.add_argument("--url-env")
    ap.add_argument("--header-file", action="append", default=[], metavar="NAME=PATH",
                    help="a header whose value is read from a file, e.g. Authorization=~/.config/acme/x.header")
    ap.add_argument("--oauth-store", help="the folder of the tool's own OAuth client and tokens (oauth.json)")
    ap.add_argument("--timeout", type=int, default=120)
    if mode == "login":
        ap.add_argument("--scopes")
        ap.add_argument("--no-open", action="store_true")
    if mode == "call":
        ap.add_argument("tool", help="a tool name, or --list")
        ap.add_argument("arguments", nargs="?", default="{}")
    a, rest = ap.parse_known_args(argv)
    if mode == "call" and rest == ["--list"]:
        a.tool = "--list"
    url = read_secret(a.url_file, a.url_env)
    if mode == "login":
        if not a.oauth_store:
            raise SystemExit("of_mcp_bridge: login needs --oauth-store")
        login(url, a.oauth_store, a.scopes, not a.no_open)
        return 0
    headers = {}
    for hf in a.header_file:
        name, _, path = hf.partition("=")
        headers[name] = read_secret(path)
    b = Bridge(url, headers, a.timeout, oauth=OAuth(a.oauth_store) if a.oauth_store else None)
    if mode == "call":
        m = call(b, a.tool, json.loads(a.arguments))
        if "error" in m:
            print(f"error: {m['error'].get('message')}", file=sys.stderr)
            return 1
        res = m.get("result") or {}
        if a.tool == "--list":
            for t in res.get("tools") or []:
                print(f"{t['name']}: {(t.get('description') or '').splitlines()[0][:150] if t.get('description') else ''}")
            return 0
        for c in res.get("content") or []:
            print(c.get("text") if c.get("type") == "text" else json.dumps(c))
        return 1 if res.get("isError") else 0
    b.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
