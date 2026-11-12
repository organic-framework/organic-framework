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


RETRIES = 4
RETRY_BASE_S = 0.5
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


def ensure_key(path=KEY_PATH):
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"of-machine {of_events.machine_id()}",
                        "-f", path], check=True, env=no_agent_env())
    with open(path + ".pub") as fh:
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


class Client:
    def __init__(self, url, key_path=KEY_PATH, state_dir=None):
        self.url = url.rstrip("/")
        self.opener = urllib.request.urlopen  # tests replace it
        self.key_path = key_path
        import hashlib
        tag = hashlib.sha256(self.url.encode()).hexdigest()[:12]  # one token per server
        self.token_path = os.path.join(state_dir or of_events.STATE_DIR, f"server-token-{tag}.json")
        self.machine_id = of_events.machine_id()

    def _request(self, method, path, body=None, token=None, raw=None, encoding=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        headers = {"Content-Type": "application/json", **access_headers(self.url)}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if encoding:
            headers["Content-Encoding"] = encoding
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        import ssl
        ctx = ssl.create_default_context() if self.url.startswith("https:") else None  # certificates always verified
        # Every endpoint is idempotent on retry (batch_id, msg_id, acks, PUTs), except POST /v1/enroll
        # (a single-use code: 410 on a retry after a lost response means it probably went through)
        # and the minting endpoints (a retry mints another code; only the last one is shown).
        lost = None
        for i in range(RETRIES):
            try:
                with self.opener(req, timeout=120, context=ctx) as r:
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
                if e.code in RETRY_STATUS and i < RETRIES - 1:
                    time.sleep(_backoff(i, retry_after))
                    continue
                raise ServerError(e.code, body)
            except Exception as e:
                if not _transient(e):
                    raise
                lost = e
                if i < RETRIES - 1:
                    time.sleep(_backoff(i))
        raise TransportError(self.url, f"{type(getattr(lost, 'reason', lost)).__name__}: {getattr(lost, 'reason', lost)}",
                             RETRIES)

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

    def put_presence(self, holders):
        return self._authed("PUT", "/v1/presence", body={"holders": holders})[1]

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

    def coord(self, org, after=0, wait=0):
        """The organization's coordination feed after `after` (docs/server-several-machines.md §4.2)."""
        from urllib.parse import quote
        return self._authed("GET", f"/v1/coord?org={quote(org)}&after={int(after)}&wait={int(wait)}")[1]

    def poll_messages(self, wait=30):
        return self._authed("GET", f"/v1/messages?wait={int(wait)}")[1]

    def queued_messages(self, to, session):
        """Messages queued for a position this caller holds now (read-only, no lease)."""
        from urllib.parse import quote
        return self._authed("GET", f"/v1/messages/queued?to={quote(to)}&session={quote(session or '')}")[1]

    def take_message(self, msg_id, to, session):
        """Take one queued message: the server marks it delivered (pulled); idempotent."""
        from urllib.parse import quote
        return self._authed("POST", f"/v1/messages/{quote(msg_id)}/take", body={"to": to, "session": session})[1]

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
