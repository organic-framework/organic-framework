"""of-server: the agents' secrets broker (docs/agent-secrets.md §5.3; hand-over 7ae99eac, approved by of_architect).

`POST /v1/secrets/use {"org", "position", "session_id", "credential"}` (machine bearer) -> `200 {"value",
"ref_vault", "cached"}`. The broker answers only when all of these hold, and records every decision in server.db
`secret_uses` (never a value):
1. the machine is enrolled and joined the organization (its bearer token; the tenant's machine joins all);
2. it presents the position now with that session (presence), and that session is the position's registered
   holder (`holder.session` in the organization's registry): a former holder is refused from its next call;
3. the organization's catalogue lets the position use the credential: the position lists it or keeps it, and the
   credential has a secret reference `ref` (`op://<vault>/<item>/<field>`);
4. the position's own service-account token is in the token folder: `<tokens_dir>/<org>/<position>.token`.
Then it runs `op read <ref>` as that service account (the token in op's environment only; no 1Password app, no
Connect, no SSH agent), so 1Password itself enforces the vault boundary even if a check above had a bug. A value is
kept in memory for at most CACHE_S, keyed by session, so a new holder never reads an old holder's cache.

The registries come from server.yaml `positions_files`, else from orgs.yaml (OF_ORGS or ~/.config/of/orgs.yaml).
"""
import json
import os
import re
import subprocess
import threading
import time

CACHE_S = 300
TOKENS_DIR = "~/.config/of/service-accounts"   # a credential root the guard denies to every session (G14 note)
REF_RE = re.compile(r"^op://([A-Za-z0-9_.-][A-Za-z0-9_. -]*)/([A-Za-z0-9_. -]+)/(?:[A-Za-z0-9_. -]+/)?"
                    r"[A-Za-z0-9_. -]+$")   # 1Password: letters, digits, - _ . and spaces; anything else needs IDs
DDL = """
CREATE TABLE IF NOT EXISTS secret_uses (at REAL NOT NULL, org TEXT, position TEXT, session TEXT, machine_id TEXT,
  credential TEXT, decision TEXT NOT NULL, reason TEXT);
CREATE INDEX IF NOT EXISTS secret_uses_at ON secret_uses (at);
"""
_cache, _lock = {}, threading.Lock()


class SecretsError(Exception):
    def __init__(self, status, typ, title):
        super().__init__(title)
        self.status, self.typ, self.title = status, typ, title


def _registry_path(app, org):
    files = app.cfg.get("positions_files") or {}
    if org in files:
        return os.path.expanduser(files[org])
    import of_credentials
    return dict(of_credentials._registries(os.environ.get("OF_ORGS"))).get(org)


def _catalogue(path, org):
    """(the organization's catalogue entry, {position: holder session}) from its registry file."""
    import yaml
    import of_credentials
    with open(path) as fh:
        doc = yaml.safe_load(fh) or {}
    holders = {}
    for p in doc.get("positions") or []:
        if isinstance(p, dict) and p.get("name"):
            s = (p.get("holder") or {}).get("session") if isinstance(p.get("holder"), dict) else None
            for n in [p["name"]] + list(p.get("aliases") or []):
                holders[str(n)] = s
    return of_credentials._org_entry(org, doc), holders


def _m(m, key):
    try:
        return m[key]
    except (KeyError, IndexError, TypeError):
        return None


def _record(app, m, body, decision, reason):
    try:
        with app.store.conn() as c:
            c.executescript(DDL)
            c.execute("INSERT INTO secret_uses VALUES (?,?,?,?,?,?,?,?)",
                      (time.time(), body.get("org"), body.get("position"), body.get("session_id"),
                       _m(m, "machine_id"), body.get("credential"), decision, reason[:300]))
    except Exception:  # noqa: BLE001 - a record that cannot be written never turns a refusal into an answer
        pass


def _op_read(op_bin, token, ref, timeout=20):
    """The value of `ref` read as the service account whose token is given; (value, None) or (None, why)."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/opt/homebrew/bin"), "HOME": os.environ.get("HOME", "/"),
           "OP_SERVICE_ACCOUNT_TOKEN": token, "OP_BIOMETRIC_UNLOCK_ENABLED": "false", "OF_AUTOMATION": "1"}
    try:
        r = subprocess.run([op_bin, "read", "--no-newline", ref], env=env, capture_output=True, text=True,
                           timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"1Password could not be reached ({type(e).__name__})"
    if r.returncode != 0:
        return None, f"1Password refused the read (exit {r.returncode})"  # its message is not passed on
    return r.stdout, None


def use(app, m, body):
    """`POST /v1/secrets/use`. Returns (status, answer); raises SecretsError after recording the refusal."""
    if not isinstance(body, dict):
        raise SecretsError(400, "bad_request", "the body must be an object")
    org, position, session, cred = (body.get(k) for k in ("org", "position", "session_id", "credential"))
    if not all(isinstance(x, str) and x for x in (org, position, session, cred)):
        raise SecretsError(400, "bad_request", "org, position, session_id and credential are required")

    def refuse(status, typ, why):
        _record(app, m, body, "deny", why)
        raise SecretsError(status, typ, why)

    if not _m(m, "tenant") and org not in set(json.loads(_m(m, "orgs") or "[]")):
        refuse(403, "forbidden", f"organization {org} not joined by this machine")
    holds, why = app.relay._holds(m, org, position, session)
    if not holds:
        refuse(403, "not_holder", why.replace("sender not holder", "not holder"))
    path = _registry_path(app, org)
    if not path or not os.path.exists(path):
        refuse(403, "no_registry", f"the server has no registry for {org}, so it cannot check the holder")
    try:
        cat, holders = _catalogue(path, org)
    except Exception as e:  # noqa: BLE001 - a registry mid-edit refuses, never allows
        refuse(503, "registry_unreadable", f"the registry of {org} could not be read ({type(e).__name__})")
    if holders.get(position) != session:
        refuse(403, "not_holder", "the session is not the position's registered holder (registry holder.session)")
    c = (cat.get("credentials") or {}).get(cred)
    if c is None:
        refuse(404, "unknown_credential", f"{org} has no credential {cred}")
    if cred not in (cat.get("positions") or {}).get(position, []) and c.get("keeper") != position:
        refuse(403, "not_listed", f"{position} neither lists nor keeps {cred}")
    ref = c.get("ref")
    if not ref or not REF_RE.match(ref):
        refuse(409, "no_ref", f"{cred} has no secret reference (ref: op://<vault>/<item>/<field>) in the catalogue")
    key = (org, position, session, cred)
    with _lock:
        hit = _cache.get(key)
        if hit and hit[1] > time.time():
            _record(app, m, body, "allow", "cached")
            return 200, {"value": hit[0], "ref_vault": REF_RE.match(ref).group(1), "cached": True}
    cfg = app.cfg.get("secrets") or {}
    tok_path = os.path.join(os.path.expanduser(cfg.get("tokens_dir") or TOKENS_DIR), org, f"{position}.token")
    try:
        with open(tok_path) as fh:
            token = fh.read().strip()
    except OSError:
        refuse(409, "no_service_account", f"no service-account token for {org}:{position} on the broker "
                                          "(of secret plan prints the line that makes it)")
    value, why = _op_read(cfg.get("op") or "op", token, ref)
    if value is None:
        refuse(502, "vault_refused", why)
    with _lock:
        _cache[key] = (value, time.time() + CACHE_S)
    _record(app, m, body, "allow", "read")
    return 200, {"value": value, "ref_vault": REF_RE.match(ref).group(1), "cached": False}
