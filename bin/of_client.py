"""Client of of-server (docs/server.md): machine key, token, batches, receipts.

Machine identity: an Ed25519 key made with ssh-keygen; the private key never leaves the
machine. A token (about 1 hour) is obtained by signing a server challenge with
`ssh-keygen -Y sign` (namespace `of-server`). No third-party crypto library is needed.
"""

import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid

import of_events

NAMESPACE = "of-server"


def no_agent_env():
    """Automation never touches an SSH agent (the founder's is 1Password): no SSH_AUTH_SOCK."""
    env = dict(os.environ)
    env["SSH_AUTH_SOCK"] = ""
    return env
KEY_PATH = os.environ.get("OF_MACHINE_KEY") or of_events.config_path("machine_ed25519")


class ServerError(Exception):
    def __init__(self, status, body):
        super().__init__(f"of-server {status}: {body}")
        self.status, self.body = status, body


class TransportError(ServerError):
    """The server could not be reached after the retries (status 0)."""
    def __init__(self, url, cause, tries):
        Exception.__init__(self, f"of-server {url} unreachable after {tries} tries: {cause}")
        self.status, self.body = 0, str(cause)


class AccessError(Exception):
    """The Cloudflare Access token file named for a server cannot be used (its path and the reason, never a value)."""


# Cloudflare Access in front of an organization's of-server (deploy/RUNBOOK.md section 4; docs/registry.md, orgs.yaml
# server_access): a service token per machine, sent as two headers. 2026-11-12: the runbook promised it and the client
# sent none, so a cutover behind Access would have failed at its switch step.
ACCESS_HEADERS = ("CF-Access-Client-Id", "CF-Access-Client-Secret")
_ACCESS_CACHE = {}
_ACCESS_MAP = {}


def access_file(url, orgs_file=None):
    """The server_access file of the organization whose server: or drain_from: is URL, or None. Only over https: the
    token never goes in clear, and never to a host the organization does not name."""
    url = str(url or "").rstrip("/")
    if not url.startswith("https://"):
        return None
    orgs = orgs_file or os.environ.get("OF_ORGS") or of_events.config_path("orgs.yaml")
    try:
        stamp = (orgs, os.stat(orgs).st_mtime_ns)
    except OSError:
        return None
    if _ACCESS_MAP.get("stamp") != stamp:  # one YAML read per change of orgs.yaml, not per request
        try:
            import yaml
            with open(orgs) as fh:
                doc = yaml.safe_load(fh) or {}
        except Exception:  # noqa: BLE001 - a broken orgs.yaml: no Access token to send
            doc = {}
        m = {}
        for o in doc.get("orgs") or []:
            if isinstance(o, dict) and isinstance(o.get("server_access"), str):
                d = o.get("drain_from")
                for u in [o.get("server")] + (d if isinstance(d, list) else [d]):
                    if isinstance(u, str) and u.startswith("https://"):
                        m.setdefault(u.rstrip("/"), os.path.expanduser(o["server_access"]))
        _ACCESS_MAP.clear()
        _ACCESS_MAP.update(stamp=stamp, map=m)
    return _ACCESS_MAP["map"].get(url)


def access_headers(url, orgs_file=None):
    """{CF-Access-Client-Id, CF-Access-Client-Secret} for URL from its organization's server_access file, or {}. The
    file is JSON {client_id, client_secret}, mode 600 or stricter, owned by this user; any other state raises
    AccessError naming the path and the reason."""
    path = access_file(url, orgs_file)
    if not path:
        return {}
    try:
        st = os.stat(path)
    except OSError as e:
        raise AccessError(f"the Cloudflare Access token file {path} for {url} cannot be read ({type(e).__name__})")
    if st.st_mode & 0o077 or (hasattr(os, "getuid") and st.st_uid != os.getuid()):
        raise AccessError(f"the Cloudflare Access token file {path} must be this user's, mode 600 "
                          f"(it is {oct(st.st_mode & 0o777)}): chmod 600 it")
    key = (path, st.st_mtime_ns, st.st_size)
    if key not in _ACCESS_CACHE:
        try:
            with open(path) as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as e:
            raise AccessError(f"the Cloudflare Access token file {path} is not JSON ({type(e).__name__})") from None
        cid, secret = (doc.get("client_id"), doc.get("client_secret")) if isinstance(doc, dict) else (None, None)
        if not (isinstance(cid, str) and cid.strip() and isinstance(secret, str) and secret.strip()):
            raise AccessError(f"the Cloudflare Access token file {path} needs client_id and client_secret")
        for k in [k for k in _ACCESS_CACHE if k[0] == path]:
            del _ACCESS_CACHE[k]
        _ACCESS_CACHE[key] = dict(zip(ACCESS_HEADERS, (cid.strip(), secret.strip())))
    return dict(_ACCESS_CACHE[key])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """of-server never redirects. A redirect is refused, never followed: urllib would carry the request to another
    host, even over http (of_review's and of_architect's reviews of organic-framework PR 68). Behind Cloudflare Access,
    a refused service token is answered with a redirect to its login page."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        where = str(newurl).split("?")[0]
        raise ServerError(code, f"of-server answered with a redirect to {where}; OF never follows one (behind "
                                "Cloudflare Access this usually means the machine's service token was refused)")


def pin_path(url):
    """Where this machine keeps the certificate it pins for an https server: ~/.config/of/servers/<host>_<port>.pem
    (docs/server-several-machines.md 4.8)."""
    import urllib.parse
    u = urllib.parse.urlsplit(url)
    port = u.port or (443 if u.scheme == "https" else 80)
    return os.path.join(os.path.expanduser("~/.config/of/servers"), f"{u.hostname}_{port}.pem")


def _https(url):
    return str(url or "")[:6].lower() == "https:"


def pin_sha256(url):
    """The SHA-256 of the DER of the certificate this machine pins for URL, or None."""
    import hashlib
    import ssl
    try:
        with open(pin_path(url)) as fh:
            return hashlib.sha256(ssl.PEM_cert_to_DER_cert(fh.read())).hexdigest()
    except (OSError, ValueError):
        return None


def tls_context(url):
    """The TLS context for an https server: only its pinned certificate when this machine pins one (never the
    system's CAs then), else the system's verified default. With a pin, the connection also compares the server's
    certificate with it by the SHA-256 of its DER (urlopen_no_redirect)."""
    import ssl
    pin = pin_path(url)
    if not os.path.exists(pin):
        return ssl.create_default_context()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # verifies the peer and its name
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_verify_locations(cafile=pin)
    ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN  # the pinned certificate itself is the trust anchor
    ctx.of_pin_sha256 = pin_sha256(url)
    return ctx


def _pinned_handler(context):
    """An HTTPS handler whose connections end unless the server's certificate is the pinned one, byte for byte; to a
    host and port with a socket_path, the connection goes through that Unix socket, TLS and its name check alike."""
    import hashlib
    import http.client
    import socket
    import ssl
    want = context.of_pin_sha256

    class Pinned(http.client.HTTPSConnection):
        def connect(self):
            target = connect_target(f"https://{self.host}:{self.port}")
            timeout = self.timeout if isinstance(self.timeout, (int, float)) else None
            if target and target[0] == "unix":
                raw = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                raw.settimeout(timeout)
                try:
                    raw.connect(target[1])
                    self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                except BaseException:
                    raw.close()
                    raise
            elif target:  # another address under the same name: the TLS name and the pin stay this URL's
                raw = socket.create_connection(target[1], timeout=timeout)
                try:
                    self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                except BaseException:
                    raw.close()
                    raise
            else:
                super().connect()
            der = self.sock.getpeercert(binary_form=True) or b""
            if not want or hashlib.sha256(der).hexdigest() != want:
                self.sock.close()
                raise ssl.SSLError("the server's certificate is not the one this machine pins")

    class Handler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(Pinned, req, context=context)
    return Handler(context=context)


_LOOPBACK = ("127.0.0.1", "localhost", "::1")


def urlopen_no_redirect(req, timeout=None, context=None):
    """urllib.request.urlopen with redirects refused. A server this machine reaches through a Unix socket, or on its
    loopback, is never reached through a proxy from the environment: with HTTPS_PROXY set (Codex sets one for its
    commands), urllib sent the request to the proxy over TCP, and socket_path's promise that no connection to that
    address uses TCP broke (of_platform, msg 01a1f997-49af, traced on the agent host)."""
    import urllib.parse
    pinned = context is not None and getattr(context, "of_pin_sha256", None) is not None
    url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
    local = connect_target(url) is not None or (urllib.parse.urlsplit(url).hostname or "").lower() in _LOOPBACK
    handlers = ([urllib.request.ProxyHandler({})] if local else []) + [_NoRedirect()] + (
        [_pinned_handler(context) if pinned else urllib.request.HTTPSHandler(context=context)]
        if context is not None else [])
    return urllib.request.build_opener(*handlers).open(req, timeout=timeout)


RETRIES = 4
RETRY_BASE_S = 0.5
# A refused connection reached no server, so it is tried again for longer: a server restarting on a code change
# (CodeWatch) refuses for seconds, twice in a row on 2026-11-20 at 16:23Z, past the four tries (a user organization's
# platform_owner, through its structure keeper, msg 01a1f9ca-387d)
REFUSED_WINDOW_S = 30
REFUSED_MAX_WAIT_S = 8.0
RETRY_STATUS = (429, 502, 503, 504)


def _transient(e):
    """A network error worth retrying: refused, reset, disconnected, timed out."""
    import http.client
    import socket
    if isinstance(e, urllib.error.HTTPError):
        return False
    if isinstance(e, urllib.error.URLError):
        e = e.reason if isinstance(e.reason, BaseException) else e
    return isinstance(e, (ConnectionRefusedError, ConnectionResetError, ConnectionAbortedError, BrokenPipeError,
                          http.client.RemoteDisconnected, http.client.IncompleteRead, TimeoutError, socket.timeout,
                          urllib.error.URLError))


def _backoff(i, retry_after=None):
    import random
    if retry_after is not None:
        try:
            return min(60.0, max(0.0, float(retry_after)))
        except (TypeError, ValueError):
            pass
    return RETRY_BASE_S * 2 ** i * (0.75 + random.random() / 2)


_KEY_CHECKED = set()  # machine keys whose public half this process has checked against the private half


def ensure_key(path=KEY_PATH):
    """The machine's key, made once. It is made in a temporary folder beside its place and linked in, so two callers at
    once (the listener and the relay started together under systemd on the agent host: of_platform, 2026-11-19) never
    run ssh-keygen on an existing path, which would ask to overwrite it and fail. The caller that links the private
    half links its public half. Any other caller lets the private half decide: once per process it checks the public
    half against `ssh-keygen -y` and, when they differ (a stale half, a crash between the links, another caller's
    half), replaces it with the derived one (of_review on organic-framework PRs 409 and 412)."""
    won = False
    if not os.path.exists(path):
        import shutil
        d = os.path.dirname(path)
        os.makedirs(d, mode=0o700, exist_ok=True)
        tmp = tempfile.mkdtemp(prefix=".machine-key-", dir=d)
        try:
            t = os.path.join(tmp, "key")
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"of-machine {of_events.machine_id()}",
                            "-f", t], check=True, env=no_agent_env())
            try:
                os.link(t, path)  # the first caller's key wins; a second finds it there
                won = True
            except FileExistsError:
                pass
            if won:
                try:
                    os.link(t + ".pub", path + ".pub")
                except FileExistsError:  # a public half left from an earlier key: this one is its pair now
                    os.replace(t + ".pub", path + ".pub")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    pub = path + ".pub"
    if not won and path not in _KEY_CHECKED:
        r = subprocess.run(["ssh-keygen", "-y", "-f", path], capture_output=True, text=True, check=True,
                           env=no_agent_env())
        want = r.stdout.split()[:2]
        try:
            with open(pub) as fh:
                have = fh.read().split()[:2]
        except FileNotFoundError:
            have = None
        if have != want:
            fd, tmp_pub = tempfile.mkstemp(prefix=".machine-pub-", dir=os.path.dirname(path))
            try:
                with os.fdopen(fd, "w") as fh:
                    fh.write(r.stdout.strip() + "\n")
                os.chmod(tmp_pub, 0o644)
                os.replace(tmp_pub, pub)
            finally:
                if os.path.exists(tmp_pub):
                    os.unlink(tmp_pub)
        _KEY_CHECKED.add(path)
    with open(pub) as fh:
        return fh.read().strip()


def sign(data, path=KEY_PATH):
    with tempfile.TemporaryDirectory() as d:
        msg = os.path.join(d, "msg")
        with open(msg, "wb") as fh:
            fh.write(data)
        subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-f", path, "-n", NAMESPACE, msg],
                       check=True, capture_output=True, env=no_agent_env())
        with open(msg + ".sig") as fh:
            return fh.read()


def studio_requester(env=None, position_of_session=None):
    """'agent:<position>' when this process is an agent (OF_POSITION set, or a Claude Code session: its Bash tool
    carries CLAUDE_CODE_SESSION_ID), else None (the founder's own terminal or the asks popup, which herdr starts with
    neither). Studio leaves views an agent requested out of the owner's measures (of_studio, 2026-11-06).
    position_of_session(sid) -> name resolves the session; unresolved it is 'agent:unbound' (of-server accepts only
    agent:<name> by the name rule)."""
    env = os.environ if env is None else env
    if env.get("OF_POSITION"):
        return f"agent:{env['OF_POSITION']}"
    sid = env.get("CLAUDE_CODE_SESSION_ID")
    if not sid:
        return None
    name = position_of_session(sid) if position_of_session else None
    return f"agent:{name}" if name else "agent:unbound"


def _uuid7():
    u7 = getattr(uuid, "uuid7", None)
    return str(u7() if u7 else uuid.uuid4())


def connect_target(url):
    """How this machine reaches URL's host and port, or None for that address itself: ("unix", path) through a Unix
    socket, or ("tcp", (address, port)) to another address under the same name. The file
    ~/.config/of/servers/<host>_<port>.socket names it: an absolute path (`of enroll --socket`; then no connection to
    that address uses TCP, so whoever takes the port gets nothing: docs/server-several-machines.md 4.8; docs/contours.md
    §1), or `tcp:<IP address>:<port>`, an address literal and never a name to look up (the Mac reaching the agent
    host's server over its tailnet port instead of the SSH bridge: the founder through of_conductor, prompt
    01a20389-14b4; hand-over c004dfc6). The connection keeps this URL's name for TLS and its pinned certificate, so the
    file moves the route, not the trust. `of server route` writes it, the owner's command; no guard rule keeps an agent
    from writing it yet (docs/guard.md, Gaps)."""
    import urllib.parse
    u = urllib.parse.urlsplit(url)
    if not u.hostname:
        return None
    port = u.port or (443 if u.scheme.lower() == "https" else 80)
    try:
        with open(os.path.join(os.path.expanduser("~/.config/of/servers"), f"{u.hostname}_{port}.socket")) as fh:
            text = fh.read().strip()
    except OSError:
        return None
    return parse_route(text)


def parse_route(text):
    """("unix", path) for an absolute path, ("tcp", (address, port)) for `tcp:<IP address>:<port>`, else None: what a
    server's route file may hold (connect_target; `of server route`, `of enroll --socket`)."""
    import ipaddress
    text = (text or "").strip()
    if text.startswith("/"):
        return "unix", text
    if text.startswith("tcp:"):
        host, _, tport = text[4:].rpartition(":")
        host = host.strip("[]")
        try:
            ipaddress.ip_address(host)
            tport = int(tport)
        except ValueError:
            return None
        if 0 < tport < 65536:
            return "tcp", (host, tport)
    return None


def socket_path(url):
    """The Unix socket this machine reaches URL's host and port through, or None (connect_target)."""
    t = connect_target(url)
    return t[1] if t and t[0] == "unix" else None


def pinned_url(url):
    """URL as https when it is http to a host and port this machine pins a certificate for: the relay's default
    server, the shipper's fallback and an orgs.yaml entry with no server: all name http://127.0.0.1:18200, which on a
    machine behind the forward any user could answer (of_secrets-review on organic-framework PR 350;
    docs/server-several-machines.md 4.8)."""
    url = (url or "").rstrip("/")
    if url[:7].lower() == "http://":
        import urllib.parse
        u = urllib.parse.urlsplit(url)
        if u.hostname and os.path.exists(pin_path(f"https://{u.hostname}:{u.port or 80}")):
            return "https://" + url[7:]
    return url


class Client:
    def __init__(self, url, key_path=KEY_PATH, state_dir=None):
        self.url = pinned_url(url)
        self.opener = urlopen_no_redirect  # tests replace it
        self.key_path = key_path
        import hashlib
        tag = hashlib.sha256(self.url.encode()).hexdigest()[:12]  # one token per server
        self.token_path = os.path.join(state_dir or of_events.STATE_DIR, f"server-token-{tag}.json")
        self.machine_id = of_events.machine_id()

    def _request(self, method, path, body=None, token=None, raw=None, encoding=None, timeout=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        headers = {"Content-Type": "application/json"}
        if encoding:
            headers["Content-Encoding"] = encoding
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        # credentials never follow a redirect: urllib copies a request's ordinary headers to the redirect target,
        # any host, even http (of_architect's review of organic-framework PR 68)
        secret = dict(access_headers(self.url), **({"Authorization": f"Bearer {token}"} if token else {}))
        for k, v in secret.items():
            req.add_unredirected_header(k, v)
        ctx = tls_context(self.url) if _https(self.url) else None  # certificates always verified
        # Every endpoint is idempotent on retry (batch_id, msg_id, acks, PUTs), except POST /v1/enroll
        # (a single-use code: 410 on a retry after a lost response means it probably went through)
        # and the minting endpoints (a retry mints another code; only the last one is shown).
        lost = None
        deadline = time.monotonic() + REFUSED_WINDOW_S
        i = 0
        while True:
            try:
                with self.opener(req, timeout=timeout or 120, context=ctx) as r:
                    return r.status, json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                try:
                    body = e.read()[:1000].decode(errors="replace")
                    retry_after = e.headers.get("Retry-After") if e.headers else None
                finally:
                    e.close()
                if e.code == 410 and lost is not None and path.startswith("/v1/enroll"):
                    raise ServerError(410, "the code was used, most likely by this machine's first try whose "
                                           "response was lost: check `of shipped` and the receipts before re-enrolling")
                if e.code in RETRY_STATUS and i < RETRIES - 1 and timeout is None:
                    time.sleep(_backoff(i, retry_after))
                    i += 1
                    continue
                raise ServerError(e.code, body)
            except Exception as e:
                if not _transient(e):
                    raise
                lost = e
                refused = isinstance(getattr(e, "reason", e), ConnectionRefusedError)
                if timeout is None and (i < RETRIES - 1 or (refused and time.monotonic() < deadline)):
                    # a call with its own timeout tries once: its caller has a fallback (of_events.card_ref)
                    time.sleep(min(REFUSED_MAX_WAIT_S, _backoff(i)))
                    i += 1
                    continue
                break
        raise TransportError(self.url, f"{type(getattr(lost, 'reason', lost)).__name__}: {getattr(lost, 'reason', lost)}",
                             i + 1)

    def token(self, refresh=False):
        try:
            with open(self.token_path) as fh:
                t = json.load(fh)
            if not refresh and t["expires"] - time.time() > 60:
                return t["token"]
        except Exception:
            pass
        ensure_key(self.key_path)
        _, ch = self._request("POST", "/v1/auth/challenge", {"machine_id": self.machine_id})
        sig = sign(ch["nonce"].encode(), self.key_path)
        _, tok = self._request("POST", "/v1/auth/token", {"machine_id": self.machine_id, "nonce": ch["nonce"],
                                                          "signature": sig})
        with open(os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(tok, fh)
        return tok["token"]

    def _authed(self, method, path, **kw):
        try:
            return self._request(method, path, token=self.token(), **kw)
        except ServerError as e:
            if e.status != 401:
                raise
            return self._request(method, path, token=self.token(refresh=True), **kw)

    def send_batch(self, records, batch_id=None):
        body = json.dumps({"batch_id": batch_id or _uuid7(), "machine_id": self.machine_id, "schema": 1,
                           "records": records}, ensure_ascii=False).encode()
        try:
            import compression.zstd as zstd
            raw, enc = zstd.compress(body, level=3), "zstd"
        except ImportError:
            import gzip
            raw, enc = gzip.compress(body), "gzip"
        _, receipt = self._authed("POST", "/v1/batches", raw=raw, encoding=enc)
        return receipt

    def receipts(self, after=0):
        return self._authed("GET", f"/v1/receipts?after={after}")[1]

    def verify_grant(self, grant_id, subject, action=None):
        return self._authed("POST", "/v1/grants/verify",
                            body={"grant_id": grant_id, "subject": subject, "action": action})[1]

    # ---- enrollment (docs/server.md: invites, enroll, consent, terms) ----

    def terms(self):
        return self._request("GET", "/v1/terms")[1]

    def invite(self, orgs, raw_allowed=(), ttl_s=72 * 3600, note=None, person=None):
        """person: the colleague's id in the organization's registry; None = the tenant's own machine."""
        body = {"orgs": list(orgs), "raw_allowed": list(raw_allowed), "ttl_s": ttl_s, "note": note}
        if person:
            body["person"] = person
        return self._authed("POST", "/v1/invites", body=body)[1]

    def revoke(self, machine_id=None, person=None):
        """Tenant: revoke one machine, or every machine of a person."""
        from urllib.parse import quote
        if bool(machine_id) == bool(person):
            raise ValueError("give exactly one of machine_id, person")
        path = f"/v1/machines/{quote(machine_id)}" if machine_id else f"/v1/machines?person={quote(person)}"
        return self._authed("DELETE", path)[1]

    def enroll(self, code, consent):
        """consent: {org: {"tiers": [...], "terms": version}}. No bearer: the one-time code is the credential."""
        return self._request("POST", "/v1/enroll", body={"code": code, "machine_id": self.machine_id,
                                                         "pubkey": ensure_key(self.key_path), "consent": consent})[1]

    def set_consent(self, consent):
        return self._authed("PUT", "/v1/consent", body=consent)[1]

    # ---- relay and presence (docs/server.md) ----

    def put_presence(self, holders, merge=False):
        """merge: add or update only these rows (docs/server.md, PUT /v1/presence); else replace the machine's set."""
        body = {"holders": holders, **({"merge": True} if merge else {})}
        return self._authed("PUT", "/v1/presence", body=body)[1]

    def card_ref(self, org, prefix, event_id, floor, timeout=None):
        """A card id for the ask EVENT_ID from the server's counter for PREFIX, which gives each number out once and the
        same number again for the same event; FLOOR, this machine's highest number, keeps the counter above it
        (of_architect, msg 01a1faa1-1fa0; of_server's side). Raises on no answer."""
        _, out = self._authed("POST", "/v1/card-refs", body={"org": org, "prefix": prefix, "event_id": event_id,
                                                             "floor": int(floor or 0)}, timeout=timeout)
        return (out or {}).get("ref")   # _authed answers (status, body)

    def presence(self, org):
        from urllib.parse import quote
        return self._authed("GET", f"/v1/presence?org={quote(org)}")[1]

    def send_message(self, to, sender, text, reply_to=None, grant_id=None, grants=None, msg_id=None, priority=None):
        body = {"msg_id": msg_id or _uuid7(), "to": to, "from": sender, "text": text, "reply_to": reply_to}
        if priority in ("fyi", "urgent"):  # fyi: inbox only, never typed in; urgent: past the relay's batching
            body["priority"] = priority
        if grant_id:
            body["grant_id"] = grant_id
        if grants:
            body["grants"] = grants
        return self._authed("POST", "/v1/messages", body=body)[1]

    def sent_header(self, msg_id, to, sender):
        """Keep the header of a message posted to another server on this one, the sender's organization's, so that a
        reply to it is taken here (docs/server.md, `POST /v1/messages/sent`)."""
        return self._authed("POST", "/v1/messages/sent", body={"msg_id": msg_id, "to": to, "from": sender})[1]

    def coord(self, org, after=0, wait=0):
        """The organization's coordination feed after `after` (docs/server-several-machines.md §4.2)."""
        from urllib.parse import quote
        return self._authed("GET", f"/v1/coord?org={quote(org)}&after={int(after)}&wait={int(wait)}")[1]

    def poll_messages(self, wait=30):
        return self._authed("GET", f"/v1/messages?wait={int(wait)}")[1]

    def queued_messages(self, to, session, newest=False):
        """Messages queued for a position this caller holds now (read-only, no lease): the urgent ones first, then the
        oldest (NEWEST: the newest), with the whole queue's `total` and `urgent` counts."""
        from urllib.parse import quote
        return self._authed("GET", f"/v1/messages/queued?to={quote(to)}&session={quote(session or '')}"
                                   + ("&newest=1" if newest else ""))[1]

    def take_message(self, msg_id, to, session):
        """Take one queued message: the server marks it delivered (pulled); idempotent."""
        from urllib.parse import quote
        return self._authed("POST", f"/v1/messages/{quote(msg_id)}/take", body={"to": to, "session": session})[1]

    def move_messages(self, src, dst, by, session, msg_ids=None, dry_run=False):
        """Move SRC's queued messages (all, or MSG_IDS) to DST, as BY (its organization's structure keeper or SRC's
        unit owner, held by SESSION): {moved, leased, not_queued}; DRY_RUN lists them, without their text."""
        body = {"from": src, "to": dst, "by": by, "session": session, "dry_run": bool(dry_run)}
        if msg_ids is not None:
            body["msg_ids"] = list(msg_ids)
        return self._authed("POST", "/v1/messages/move", body=body)[1]

    def drop_message(self, msg_id, to, by, session, reason):
        """Expire one message queued for TO, as BY (TO's structure keeper or unit owner, held by SESSION)."""
        from urllib.parse import quote
        return self._authed("POST", f"/v1/messages/{quote(msg_id)}/drop",
                            body={"to": to, "by": by, "session": session, "reason": reason})[1]

    def redact(self, body):
        """POST /v1/redactions (docs/server.md, Redaction): replace shipped rows with masked copies."""
        return self._authed("POST", "/v1/redactions", body=body)[1]

    def redaction(self, redaction_id):
        from urllib.parse import quote
        return self._authed("GET", f"/v1/redactions/{quote(redaction_id)}")[1]

    def use_secret(self, org, position, session_id, credential):
        """docs/agent-secrets.md §5.3: the value of a credential the calling session's position may use."""
        return self._authed("POST", "/v1/secrets/use", body={"org": org, "position": position,
                                                              "session_id": session_id, "credential": credential})[1]

    def ack(self, msg_id, status, reason=None, retry_after=None):
        from urllib.parse import quote
        body = {"status": status, "reason": reason}
        if retry_after is not None:
            body["retry_after"] = int(retry_after)
        return self._authed("POST", f"/v1/messages/{quote(msg_id)}/ack", body=body)[1]
