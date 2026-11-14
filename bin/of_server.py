"""of-server: the sync server in front of ClickHouse (docs/server.md, contract v0).

v0 scope: auth (machine keys, challenge, short-lived tokens), POST /v1/batches with a
durable on-disk queue (202 only after fsync), receipts (queued -> committed), schema
validation, routing at accept time against the machine's organizations, and batch
writers that fan out to the organization databases and the vault. The writers mask every
row bound for an organization (RFC 0002 §4.2, bin/of_mask.py) and record each secret found
as `of.secret_detected` in the vault, which only the tenant reads. The raw_to_org=false gate
holds until lab-secrets-v0 passes.
Also: POST /v1/grants/verify. Not yet: relay and presence, OTLP.
"""

import gzip
import hashlib
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import of_events
import of_mask
import of_route
import of_server_coord
import of_server_finance
import of_server_secrets

try:
    import compression.zstd as zstd
except ImportError:
    zstd = None

VERSION = "0.4"
PLUGINS = {}  # the chosen plugins, for /v1/health
SCHEMAS = (1,)            # accepted: the current version and, once there is one, the previous
NAMESPACE = "of-server"
DEFAULTS = {
    "host": "127.0.0.1",
    "port": 18200,
    "state_dir": "~/.local/state/of/server",
    "token_ttl": 3600,
    "challenge_ttl": 60,
    "max_body": 64 * 1024 * 1024,
    "flush_every": 1.0,
    "max_attempts": 8,
    # Seconds a connection may sit idle in one read or write before the server closes it: a client that went away
    # in a sleep or a lost network no longer holds a thread (of_conductor, msg 01a1d63f-d434).
    "request_timeout": 60,
    # The tenant master key; the masker's HMAC key is derived from it (label of/secret/v1).
    # Profile 1 creates it on first start; on Kubernetes it is mounted from a Secret and
    # tenant_key_create is false, so a missing mount stops the server instead of changing tags.
    "tenant_key_file": "~/.config/of/tenant.key",
    "tenant_key_create": True,
    # A server outside the tenant's machine gets only the derived masking key (hex), never the
    # master: from the master one could derive the legacy grant HMAC key and forge grants.
    "secret_key_file": None,
    # Grant verification: the tenant's pinned Ed25519 grant public keys (OpenSSH lines). The
    # keys listed in an organization's repository are never trusted by themselves.
    "trusted_grant_keys": [],
    "trusted_grant_keys_file": "~/.config/of/grant_ed25519.pub",
    # Legacy HMAC grant signatures, verified with the master key: retired, since no grant carries one.
    "grant_legacy_hmac": False,   # retired 2026-11-11 (hand-over 4c7ac78f): no grant carries an HMAC
    # Where grants come from on a server with no checkout of the organizations' repositories:
    # {org: path to registry/grants.yaml}, kept fresh by a git-sync sidecar. Absent: orgs.yaml.
    "grants_files": None,
    # {org: path to registry/positions.yaml}: lets the relay check that a sender's presence is the
    # position's registered holder session (absent: live presence alone).
    "positions_files": None,
    # /v1/health without authentication: counts and errors on a local server; on a public
    # ingress only {ok, version}.
    "health_detail": True,
    # ClickHouse for the storage plugin: {url, user, password_file}; `<db>_ingest.password`
    # files sit next to password_file. Absent: the client's ship.yaml (the founder's Mac).
    "clickhouse": None,
    # Consent terms people agree to at enrollment (RFC 0001 §5.5). `effect_on_shipped` says what
    # narrowing consent does to rows already shipped; only "remain" (they stay under the terms
    # they were shipped under) is carried out today.
    "terms": {"2026-11-05": {"effect_on_shipped": "remain", "url": ""}},
    "terms_current": "2026-11-05",
    # Studio (docs/studio.md §5.1): the base URL in one-time links (absent: http://<Host>), and
    # the session length.
    "public_url": None,
    # The tenant's own organization: where views of "all organizations" are recorded (absent: the
    # tenant-only unassigned database).
    "tenant_org": None,
    "studio_session_ttl": 12 * 3600,
    "invite_ttl": 72 * 3600,
    "invite_ttl_max": 7 * 24 * 3600,
}
TIERS = ("typed", "raw")
# Routing settings the server owns. server.yaml wins; the client's ship.yaml is the fallback
# while the founder's Mac still keeps them there.
ROUTE_KEYS = ("tenant", "vault_database", "unassigned_database", "raw_to_org")
ROUTE_DEFAULTS = {"vault_database": "of_vault", "unassigned_database": "of_unassigned", "raw_to_org": False}
REQUIRED = {
    "event": ("event_id", "ts", "name", "record"),
    "transcript_record": ("session_id", "record_uuid", "ts", "source_path", "line_offset", "sha256", "raw"),
    "hook_record": ("local_id", "received_at", "hook_event", "sha256", "raw"),
    "runtime_record": ("received_at", "runtime", "type", "raw"),
}

DDL = """
CREATE TABLE IF NOT EXISTS machines (machine_id TEXT PRIMARY KEY, pubkey TEXT NOT NULL, orgs TEXT NOT NULL,
  raw_orgs TEXT NOT NULL, tenant INTEGER NOT NULL DEFAULT 0, enrolled_at TEXT, revoked_at TEXT);
CREATE TABLE IF NOT EXISTS challenges (nonce TEXT PRIMARY KEY, machine_id TEXT, expires REAL);
CREATE TABLE IF NOT EXISTS tokens (token_sha TEXT PRIMARY KEY, machine_id TEXT, expires REAL);
CREATE TABLE IF NOT EXISTS batches (seq INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT UNIQUE, machine_id TEXT,
  body_sha TEXT, state TEXT, accepted INTEGER, rejected TEXT, by_org TEXT, created_at TEXT, committed_at TEXT,
  attempts INTEGER DEFAULT 0, error TEXT);
CREATE TABLE IF NOT EXISTS seen (k BLOB PRIMARY KEY) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS redactions (redaction_id TEXT PRIMARY KEY, batch_id TEXT, machine_id TEXT, tbl TEXT,
  ids TEXT, reason TEXT, state TEXT, counts TEXT, error TEXT, created_at TEXT, done_at TEXT);
CREATE TABLE IF NOT EXISTS invites (code_sha TEXT PRIMARY KEY, orgs TEXT NOT NULL, raw_allowed TEXT NOT NULL,
  person TEXT, note TEXT, created_by TEXT, created_at TEXT, expires REAL, used_at TEXT, used_by TEXT);
CREATE TABLE IF NOT EXISTS studio_tokens (token_sha TEXT PRIMARY KEY, viewer TEXT, expires REAL);
CREATE TABLE IF NOT EXISTS studio_sessions (session_sha TEXT PRIMARY KEY, viewer TEXT, expires REAL);
CREATE TABLE IF NOT EXISTS secret_sightings (batch_id TEXT, kind TEXT, hmac TEXT, org TEXT, tbl TEXT, count INTEGER,
  first_ts TEXT, last_ts TEXT, location TEXT, detected_at TEXT, PRIMARY KEY (batch_id, kind, hmac, org, tbl));
"""


# What this server offers beyond the v1 core, in GET /v1/terms, so a client can refuse to send to a server
# that lacks it (of_platform's `of event finance_fact`, 2026-11-10).
CAPABILITIES = ("coord", "finance_facts", "secrets")


MIGRATIONS = (
    ("machines", "person", "TEXT"),                       # None: a machine of the tenant
    ("machines", "allowed_raw", "TEXT NOT NULL DEFAULT '[]'"),
    ("machines", "consent", "TEXT NOT NULL DEFAULT '{}'"),  # {org: {tiers, terms, at}}
    ("batches", "tiers", "TEXT"),                         # {org: tiers} at accept time
    ("batches", "duplicates", "INTEGER NOT NULL DEFAULT 0"),  # records already accepted in other batches
)


def load_config(path=None):
    cfg = dict(DEFAULTS)
    path = path or os.path.expanduser("~/.config/of/server.yaml")
    if os.path.exists(path):
        import yaml
        with open(path) as fh:
            cfg.update(yaml.safe_load(fh) or {})
    cfg["state_dir"] = os.path.expanduser(cfg["state_dir"])
    cfg["tenant_key_file"] = os.path.expanduser(cfg["tenant_key_file"])
    return cfg


def route_settings(cfg, ship_cfg):
    out = dict(ROUTE_DEFAULTS)
    out.update({k: ship_cfg[k] for k in ROUTE_KEYS if k in (ship_cfg or {})})
    out.update({k: cfg[k] for k in ROUTE_KEYS if k in cfg})
    out.setdefault("tenant", "default")
    return out


def load_grants_files(files):
    """{org: [grant]} from {org: path}; a missing file means no grants yet."""
    import yaml
    out = {}
    for org, path in files.items():
        path = os.path.expanduser(path)
        if os.path.exists(path):
            with open(path) as fh:
                out[org] = (yaml.safe_load(fh) or {}).get("grants") or []
        else:
            out[org] = []
    return out


def grant_keys(cfg):
    import of_grants
    pins = list(cfg.get("trusted_grant_keys") or [])
    pin_file = os.path.expanduser(cfg.get("trusted_grant_keys_file") or "")
    if pin_file and os.path.exists(pin_file):
        with open(pin_file) as fh:
            pins += [ln for ln in fh.read().splitlines() if ln.strip() and not ln.startswith("#")]
    legacy = None
    master = os.path.expanduser(cfg["tenant_key_file"])
    if cfg.get("grant_legacy_hmac") and os.path.exists(master):
        with open(master, "rb") as fh:
            legacy = of_grants.purpose_key("of/grant/v1", master=fh.read().strip())
    return of_grants.Keys(ed25519=pins, hmac_key=legacy)


def export_secret_key(cfg, path):
    """Write the derived masking key (hex) to a new file of mode 600, for a server that must not
    hold the master key (a Kubernetes Secret). Never prints it."""
    fd = os.open(os.path.expanduser(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(secret_key(dict(cfg, secret_key_file=None, tenant_key_create=False)).hex() + "\n")
    return path


def secret_key(cfg):
    """HMAC key for secret tags: purpose `of/secret/v1` of the tenant master key, or the derived
    key itself from `secret_key_file` (64 hex characters) on a server without the master."""
    import hmac as _hmac
    if cfg.get("secret_key_file"):
        with open(os.path.expanduser(cfg["secret_key_file"])) as fh:
            key = bytes.fromhex(fh.read().strip())
        if len(key) != 32:
            raise SystemExit("of-server: secret_key_file must hold 32 bytes in hex")
        return key
    path = os.path.expanduser(cfg["tenant_key_file"])
    if not os.path.exists(path):
        if not cfg.get("tenant_key_create"):
            raise SystemExit(f"of-server: tenant key {path} is missing and tenant_key_create is false")
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_hex(32) + "\n")
    with open(path, "rb") as fh:
        master = fh.read().strip()
    return _hmac.new(master, b"of/secret/v1", hashlib.sha256).digest()


class Store:
    def __init__(self, state_dir):
        os.makedirs(state_dir, mode=0o700, exist_ok=True)
        self.dir = state_dir
        path = os.path.join(state_dir, "server.db")
        if not os.path.exists(path):
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
        self.local = threading.local()
        with self.conn() as c:
            c.executescript(DDL)
            for table, col, decl in MIGRATIONS:
                if col not in [r[1] for r in c.execute(f"PRAGMA table_info({table})")]:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    def close_thread(self):
        """Close this thread's connection. Request threads live for one request; without this
        each left its database, WAL and shm descriptors open until the process ran out of them."""
        c = getattr(self.local, "c", None)
        if c is not None:
            self.local.c = None
            c.close()

    def conn(self):
        c = getattr(self.local, "c", None)
        if c is None:
            c = sqlite3.connect(os.path.join(self.dir, "server.db"), timeout=10, isolation_level=None)
            c.execute("PRAGMA journal_mode = WAL")
            c.execute("PRAGMA busy_timeout = 10000")
            self.local.c = c
        return _Ctx(c)

    def tx(self):
        """`with store.tx() as c:` BEGIN IMMEDIATE ... COMMIT on this thread's connection, ROLLBACK
        on any error, so no exception leaves the store's write lock held (the local event store
        froze that way on 2026-11-06; of_store.write_tx is the client's twin)."""
        return _Tx(self.conn().c)


class _Ctx:
    """The thread's connection, kept open across uses. It runs in autocommit; a transaction still
    open when the block ends (an exception between BEGIN and COMMIT) is rolled back here, since
    the connection outlives the block and would otherwise keep the write lock."""

    def __init__(self, c):
        self.c = c

    def __enter__(self):
        return self.c

    def __exit__(self, *a):
        if self.c.in_transaction:
            self.c.execute("ROLLBACK")
        return False


class _Tx:
    def __init__(self, c):
        self.c = c

    def __enter__(self):
        self.c.execute("BEGIN IMMEDIATE")
        return self.c

    def __exit__(self, exc_type, *a):
        if exc_type is None:
            self.c.execute("COMMIT")
        elif self.c.in_transaction:
            self.c.execute("ROLLBACK")
        return False


# ------------------------------------------------------------------ machines

def enroll(store, machine_id, pubkey, orgs, raw_orgs=(), tenant=False, person=None, allowed_raw=None, consent=None):
    allowed_raw = sorted(raw_orgs if allowed_raw is None else allowed_raw)
    consent = consent or {o: {"tiers": ["typed", "raw"] if o in raw_orgs else ["typed"], "terms": None,
                              "at": of_events.now_iso()} for o in orgs}
    with store.conn() as c:
        c.execute("INSERT OR REPLACE INTO machines (machine_id, pubkey, orgs, raw_orgs, tenant, enrolled_at, revoked_at,"
                  " person, allowed_raw, consent) VALUES (?,?,?,?,?,?,NULL,?,?,?)",
                  (machine_id, pubkey.strip(), json.dumps(sorted(orgs)), json.dumps(sorted(raw_orgs)),
                   1 if tenant else 0, of_events.now_iso(), person, json.dumps(allowed_raw), json.dumps(consent)))
    of_events.emit("machine_enrolled", {"machine_id": machine_id, "orgs": sorted(orgs), "raw_orgs": sorted(raw_orgs),
                                        "tenant": bool(tenant), "person": person})


def revoke(store, machine_id):
    with store.conn() as c:
        c.execute("UPDATE machines SET revoked_at = ? WHERE machine_id = ?", (of_events.now_iso(), machine_id))
        c.execute("DELETE FROM tokens WHERE machine_id = ?", (machine_id,))


def revoke_machines(store, buffer, tenant, machine_ids, by):
    """Revoke at once (key and tokens) and record of.machine_revoked in each organization the
    machine had joined. Returns the machines revoked."""
    done, rows = [], []
    with store.conn() as c:
        found = c.execute(f"SELECT machine_id, orgs, person FROM machines WHERE revoked_at IS NULL AND machine_id IN "
                          f"({','.join('?' * len(machine_ids))})", list(machine_ids)).fetchall() if machine_ids else []
    for mid, orgs, person in found:
        revoke(store, mid)
        done.append(mid)
        for org in json.loads(orgs):
            rows.append(server_event(tenant, org, "of.machine_revoked",
                                     {"machine_id": mid, "person": person, "by": by}))
    if rows:
        queue_server_events(store, buffer, rows)
    return done


def server_event(tenant, org, name, payload, ts=None):
    """An event the server itself states, in the row shape of the `event` tables."""
    import uuid
    ts = ts or of_events.now_iso()
    eid = str(uuid.uuid4())
    record = {"of.schema": 1, "of.event.id": eid, "timestamp": ts, "event.name": name, "of.source": "of-server",
              "of.org": org, "of.payload": payload}
    return {"tenant_id": tenant, "org_id": org, "host_id": "of-server", "event_id": eid, "ts": ts, "name": name,
            "session_id": "", "position": "", "source": "of-server", "harness_name": "", "harness_version": "",
            "model": "", "payload": json.dumps(payload, ensure_ascii=False), "raw_ref": "",
            "record": json.dumps(record, ensure_ascii=False)}


def queue_server_events(store, buffer, rows, sightings=None):
    """Through the same durable queue and writers as machines' batches (masked, retried).
    `sightings`: secrets already found before the queue, [(kind, hmac, org, table, info)]."""
    import uuid
    bid = "srv-" + str(uuid.uuid4())
    buffer.put(bid, {"batch_id": bid, "machine_id": "of-server", "records": [{"type": "event", "data": r} for r in rows],
                     "sightings": [list(x) for x in sightings or []]})
    by_org = {}
    for r in rows:
        by_org[r["org_id"]] = by_org.get(r["org_id"], 0) + 1
    with store.conn() as c:
        c.execute("INSERT INTO batches (batch_id, machine_id, body_sha, state, accepted, rejected, by_org, created_at)"
                  " VALUES (?,?,?,?,?,?,?,?)", (bid, "of-server", "", "queued", len(rows), "[]", json.dumps(by_org),
                                                of_events.now_iso()))
    return bid


def create_invite(store, cfg, orgs, raw_allowed=(), person=None, note=None, ttl=None, created_by=None):
    import base64
    orgs, raw_allowed = sorted(set(orgs)), sorted(set(raw_allowed))
    if not orgs:
        raise ValueError("an invite names at least one organization")
    if set(raw_allowed) - set(orgs):
        raise ValueError("raw_allowed must be within orgs")
    if cfg.get("orgs") and set(orgs) - set(cfg["orgs"]):
        raise ValueError(f"not served here: {sorted(set(orgs) - set(cfg['orgs']))}")
    ttl = min(int(ttl or cfg["invite_ttl"]), int(cfg["invite_ttl_max"]))
    code = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    expires = time.time() + ttl
    with store.conn() as c:
        c.execute("INSERT INTO invites (code_sha, orgs, raw_allowed, person, note, created_by, created_at, expires)"
                  " VALUES (?,?,?,?,?,?,?,?)", (hashlib.sha256(code.encode()).hexdigest(), json.dumps(orgs),
                                                json.dumps(raw_allowed), person, note, created_by,
                                                of_events.now_iso(), expires))
    return {"code": code, "expires": expires, "expires_at": of_events.now_iso(expires)[:16].replace("T", " ") + " UTC",
            "orgs": orgs, "raw_allowed": raw_allowed, "person": person}


class EnrollError(Exception):
    def __init__(self, status, typ, title):
        super().__init__(title)
        self.status, self.typ, self.title = status, typ, title


def _check_consent(cfg, consent, orgs, allowed_raw):
    """Normalized {org: {tiers, terms}}; raises EnrollError."""
    if not isinstance(consent, dict) or set(consent) != set(orgs):
        raise EnrollError(400, "consent", f"consent must name exactly the organizations {sorted(orgs)}")
    out = {}
    for org, c in consent.items():
        tiers = (c or {}).get("tiers") or []
        terms = (c or {}).get("terms")
        if "typed" not in tiers or set(tiers) - set(TIERS):
            raise EnrollError(400, "consent", f"{org}: tiers must include typed and only {list(TIERS)}")
        if "raw" in tiers and org not in allowed_raw:
            raise EnrollError(403, "consent", f"{org}: the raw tier is not offered by this invite")
        if terms not in cfg["terms"]:
            raise EnrollError(400, "terms", f"{org}: unknown terms version {terms!r}")
        out[org] = {"tiers": [t for t in TIERS if t in tiers], "terms": terms, "at": of_events.now_iso()}
    return out


def enroll_with_invite(store, buffer, cfg, tenant, body):
    code, mid, pubkey = body.get("code") or "", body.get("machine_id") or "", (body.get("pubkey") or "").strip()
    if not (code and mid and pubkey.startswith("ssh-ed25519 ")):
        raise EnrollError(400, "bad_request", "code, machine_id and an ssh-ed25519 pubkey are required")
    sha = hashlib.sha256(code.encode()).hexdigest()
    with store.conn() as c:
        inv = c.execute("SELECT orgs, raw_allowed, person, expires, used_at FROM invites WHERE code_sha = ?",
                        (sha,)).fetchone()
    if not inv:
        raise EnrollError(404, "unknown_code", "unknown invite code")
    orgs, raw_allowed, person, expires, used_at = json.loads(inv[0]), json.loads(inv[1]), inv[2], inv[3], inv[4]
    if used_at or expires < time.time():
        raise EnrollError(410, "used_code", "the invite was used or has expired")
    consent = _check_consent(cfg, body.get("consent"), orgs, raw_allowed)
    with store.conn() as c:
        cur = c.execute("UPDATE invites SET used_at = ?, used_by = ? WHERE code_sha = ? AND used_at IS NULL",
                        (of_events.now_iso(), mid, sha))
        if cur.rowcount != 1:
            raise EnrollError(410, "used_code", "the invite was used or has expired")
        if c.execute("SELECT 1 FROM machines WHERE machine_id = ? AND revoked_at IS NULL", (mid,)).fetchone():
            raise EnrollError(409, "conflict", "this machine is already enrolled; revoke it first")
    raw_orgs = [o for o, v in consent.items() if "raw" in v["tiers"]]
    enroll(store, mid, pubkey, orgs, raw_orgs, person=person, allowed_raw=raw_allowed, consent=consent)
    queue_server_events(store, buffer, [
        server_event(tenant, o, "of.consent", {"machine_id": mid, "person": person, "tiers": v["tiers"],
                                               "terms": v["terms"], "via": "enroll", "effect_on_shipped": None})
        for o, v in consent.items()] + [server_event(tenant, o, "of.machine_enrolled",
                                                     {"machine_id": mid, "person": person}) for o in orgs])
    return {"machine_id": mid, "orgs": orgs, "raw_orgs": sorted(raw_orgs), "person": person}


def update_consent(store, buffer, cfg, tenant, machine, body):
    """Narrow always, widen raw only within the invite. Applies to batches accepted from now on;
    rows already shipped follow the terms' `effect_on_shipped`."""
    mid = machine["machine_id"]
    with store.conn() as c:
        row = c.execute("SELECT orgs, allowed_raw, consent, person FROM machines WHERE machine_id = ?", (mid,)).fetchone()
    orgs, allowed_raw, current, person = json.loads(row[0]), json.loads(row[1]), json.loads(row[2]), row[3]
    if not isinstance(body, dict) or set(body) - set(orgs) or not body:
        raise EnrollError(400, "consent", f"name some of the organizations {orgs}")
    new = dict(current)
    new.update(_check_consent(cfg, body, list(body), allowed_raw))
    raw_orgs = sorted(o for o, v in new.items() if "raw" in v["tiers"])
    with store.conn() as c:
        c.execute("UPDATE machines SET consent = ?, raw_orgs = ? WHERE machine_id = ?",
                  (json.dumps(new), json.dumps(raw_orgs), mid))
    rows = []
    for org in body:
        before = (current.get(org) or {}).get("tiers") or []
        narrowed = sorted(set(before) - set(new[org]["tiers"]))
        effect = cfg["terms"][new[org]["terms"]].get("effect_on_shipped", "remain") if narrowed else None
        rows.append(server_event(tenant, org, "of.consent", {"machine_id": mid, "person": person,
                                                             "tiers": new[org]["tiers"], "terms": new[org]["terms"],
                                                             "via": "update", "withdrawn": narrowed,
                                                             "effect_on_shipped": effect}))
    queue_server_events(store, buffer, rows)
    return {o: {"tiers": v["tiers"], "terms": v["terms"]} for o, v in new.items()}




# ----------------------------------------------------------------- accepting

def validate(rec):
    if not isinstance(rec, dict) or rec.get("type") not in REQUIRED or not isinstance(rec.get("data"), dict):
        return "record must be {type, data} with a known type"
    missing = [f for f in REQUIRED[rec["type"]] if rec["data"].get(f) in (None, "") and f != "raw"]
    missing += [f for f in REQUIRED[rec["type"]] if f == "raw" and "raw" not in rec["data"]]
    if missing:
        return "schema: missing " + ", ".join(missing)
    if rec["type"] == "event":
        try:
            r = json.loads(rec["data"]["record"])
            if not (r.get("of.event.id") and r.get("timestamp") and r.get("event.name")):
                return "schema: record lacks of.event.id, timestamp or event.name"
        except Exception:
            return "schema: record is not JSON"
    return None


NATURAL_KEYS = {"event": ("event_id",), "transcript_record": ("session_id", "record_uuid"),
                "hook_record": ("host_id", "local_id"), "runtime_record": ("host_id", "received_at", "type", "pane_id")}


def record_key(table, org, d, machine_id):
    """A record's identity on this server (docs/server.md: of.event.id; session + record uuid;
    machine + local id). A hook record's local id is per machine, so the machine is part of it."""
    parts = [table, org or ""] + [str(d.get(k, "")) for k in NATURAL_KEYS[table]]
    if table == "hook_record":
        parts.append(machine_id)
    return hashlib.sha256("\x1f".join(parts).encode()).digest()[:16]


def accept(store, buffer, router, machine, batch, body_sha, served=None, redaction=None, finance=False):
    """Validate and route a batch; write it durably; return (status, receipt).

    `served`: the organizations a per-organization server serves (server.yaml `orgs`). There the
    client has routed: each record carries `org_id`, which must be served and joined. Without it
    (the local profile-1 server for every organization) the server routes records itself.

    `redaction`: the batch carries a redaction's replacement rows (docs/server.md, Redaction): they
    reuse old keys, so they pass the duplicate check; one rejected record rejects the redaction
    (400), since deleting without its replacement would hide history; and the batch is queued even
    when it has no records, so the writer runs the delete in queue order.

    `finance`: the one record is a finance fact from POST /v1/finance_facts (of_server_finance), which has
    checked it. Anywhere else an of.finance_fact is refused (docs/events.md, Finance facts: server-only write)."""
    bid = batch.get("batch_id")
    with store.conn() as c:
        row = c.execute("SELECT body_sha, state, accepted, rejected, duplicates FROM batches WHERE batch_id = ?",
                        (bid,)).fetchone()
    if row:
        if row[0] != body_sha:
            return 409, {"type": "conflict", "title": "batch_id reused with a different body"}
        return 202, {"batch_id": bid, "state": row[1], "accepted": row[2], "rejected": json.loads(row[3]),
                     "duplicates": row[4]}
    orgs = set(json.loads(machine["orgs"]))
    raw_orgs = set(json.loads(machine["raw_orgs"]))
    tenant = bool(machine["tenant"])
    replace = bool(batch.get("replace")) and tenant or redaction is not None  # the tenant may re-send on purpose
    kept, rejected, by_org, keys = [], [], {}, []
    for i, rec in enumerate(batch.get("records") or []):
        err = validate(rec)
        if err:
            rejected.append({"index": i, "reason": err})
            continue
        if rec["type"] == "event" and rec["data"].get("name") == of_server_finance.FINANCE and not finance:
            rejected.append({"index": i, "reason": "a finance fact goes only through POST /v1/finance_facts"})
            continue
        d = dict(rec["data"])
        if served is not None:
            org = d.get("org_id") or None
            if org not in served:
                rejected.append({"index": i, "reason": f"organization {org or 'missing'} not served here"})
                continue
        elif d.get("org_id"):
            org = d["org_id"] if d["org_id"] in router.db_by_org else None
        else:
            org = of_route.route_org(rec["type"], d, router)
        d["org_id"] = org or ""
        bad = of_route.copy_violation(rec["type"], d, rec.get("copy"))
        if bad:
            rejected.append({"index": i, "reason": bad})
            continue
        if not tenant and (org is None or org not in orgs):
            rejected.append({"index": i, "reason": f"organization {org or 'unknown'} not joined by this machine"})
            continue
        if "raw" in d and not tenant and org not in raw_orgs:
            d["raw"] = ""  # the person on this machine did not opt in to the raw tier
        d["org_id"] = org or ""
        keys.append(record_key(rec["type"], org, d, machine["machine_id"]))
        kept.append({"type": rec["type"], "data": d})
        by_org[org or "unassigned"] = by_org.get(org or "unassigned", 0) + 1
    if redaction is not None and rejected:
        return 400, {"type": "bad_request", "title": "a replacement record was rejected", "rejected": rejected}
    # A record this server has already accepted, in any batch, is not written again: a re-sent
    # batch with a new id adds nothing (ReplacingMergeTree would only merge it away later).
    duplicates = 0
    if kept and not replace:
        with store.tx() as c:
            fresh = []
            for k, rec in zip(keys, kept):
                if c.execute("INSERT OR IGNORE INTO seen VALUES (?)", (k,)).rowcount:
                    fresh.append(rec)
        duplicates = len(kept) - len(fresh)
        if duplicates:
            by_org = {}
            for rec in fresh:
                o = rec["data"]["org_id"] or "unassigned"
                by_org[o] = by_org.get(o, 0) + 1
        kept = fresh
    payload = {"batch_id": bid, "machine_id": machine["machine_id"], "records": kept}
    if redaction is not None:
        payload["redaction"] = redaction
    buffer.put(bid, payload)
    state = "queued" if kept or redaction is not None else "committed"
    with store.conn() as c:
        tiers = {o: (["typed", "raw"] if tenant or o in raw_orgs else ["typed"]) for o in by_org}
        c.execute("INSERT INTO batches (batch_id, machine_id, body_sha, state, accepted, rejected, by_org, created_at,"
                  " committed_at, tiers, duplicates) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (bid, machine["machine_id"], body_sha, state, len(kept), json.dumps(rejected), json.dumps(by_org),
                   of_events.now_iso(), None if state == "queued" else of_events.now_iso(), json.dumps(tiers), duplicates))
    if state != "queued":
        buffer.done(bid)
    return 202, {"batch_id": bid, "state": state, "accepted": len(kept), "rejected": rejected, "duplicates": duplicates}


# ----------------------------------------------------------------- redaction

REDACTABLE = ("transcript_record", "hook_record", "event")
MAX_REDACT_IDS = 10_000


def _redaction_keys(table, ids, session, host):
    """The natural-key tuples (of_plugins.KEYS order) a redaction names."""
    keys = []
    for i in ids:
        if table != "transcript_record" and (not isinstance(i, (str, int)) or i in ("", None)):
            raise ValueError("ids must be non-empty strings")
        if table == "transcript_record":
            if isinstance(i, list) and len(i) == 2 and all(isinstance(x, str) and x for x in i):
                keys.append((i[0], i[1]))          # [session_id, record_uuid]: many sessions in one request
            elif isinstance(i, str) and session:
                keys.append((session, i))
            else:
                raise ValueError("transcript_record ids are record_uuid with session, or [session_id, record_uuid]")
        elif table == "hook_record":
            keys.append((host, int(i)))
        else:
            keys.append((str(i),))
    return keys


def _row_key(table, d):
    import of_plugins
    cols = of_plugins.KEYS[table]
    try:
        return tuple(int(d.get(c)) if c == "local_id" else str(d.get(c, "")) for c in cols)
    except (TypeError, ValueError):
        return None


def create_redaction(app, m, body, body_sha):
    """POST /v1/redactions (docs/server.md, Redaction). Returns (status, body)."""
    rid = body.get("redaction_id")
    if not rid or not isinstance(rid, str) or len(rid) > 128:
        return 400, {"type": "bad_request", "title": "redaction_id is required"}
    with app.store.conn() as c:
        row = c.execute("SELECT machine_id, state FROM redactions WHERE redaction_id = ?", (rid,)).fetchone()
    if row:
        if row[0] != m["machine_id"]:
            return 409, {"type": "conflict", "title": "redaction_id used by another machine"}
        return 200, {"redaction_id": rid, "state": redaction_state(app, rid)["state"]}
    table, ids, records = body.get("table"), body.get("ids"), body.get("records") or []
    if table not in REDACTABLE:
        return 400, {"type": "bad_request", "title": f"table must be one of {list(REDACTABLE)}"}
    if not isinstance(ids, list) or not ids or len(ids) > MAX_REDACT_IDS:
        return 400, {"type": "bad_request", "title": f"ids: 1 to {MAX_REDACT_IDS} natural keys"}
    if not (body.get("reason") or "").strip():
        return 400, {"type": "bad_request", "title": "reason is required"}
    if not isinstance(records, list):
        return 400, {"type": "bad_request", "title": "records must be a list"}
    tenant = bool(m["tenant"])
    if not records and not tenant:
        return 403, {"type": "forbidden", "title": "a delete without replacement needs the tenant"}
    # The tenant redacts any row; another machine only the rows it shipped itself.
    host = None if tenant else m["machine_id"]
    key_host = (body.get("host_id") if tenant else None) or m["machine_id"]
    try:
        keys = _redaction_keys(table, ids, body.get("session"), key_host)
    except (ValueError, TypeError) as e:
        return 400, {"type": "bad_request", "title": str(e)}
    wanted = set(keys)
    for i, rec in enumerate(records):
        d = rec.get("data") if isinstance(rec, dict) else None
        if not isinstance(d, dict) or rec.get("type") != table or _row_key(table, d) not in wanted:
            return 400, {"type": "bad_request", "title": f"records[{i}] is not a replacement for one of ids"}
        if host is not None and d.get("host_id") != host:
            return 403, {"type": "forbidden", "title": f"records[{i}]: another machine's row"}
        if table in of_route.RAW_TABLES and not d.get("raw"):
            # A replacement without content would empty the vault's copy: that is a delete, which
            # has its own form (no records, the tenant only). (2026-11-06: a sweep sent these.)
            return 400, {"type": "bad_request", "title": f"records[{i}] has no raw: a replacement carries the redacted content"}
    spec = {"redaction_id": rid, "table": table, "keys": [list(k) for k in keys], "host": host,
            "ids": [i if isinstance(i, list) else str(i) for i in ids], "session": body.get("session") or "", "sha_old": body.get("sha_old") or "",
            "sha_new": body.get("sha_new") or "", "reason": body["reason"].strip()[:500],
            "machine_id": m["machine_id"]}
    bid = f"redaction-{rid}"
    status, receipt = accept(app.store, app.buffer, app.router(), m, {"batch_id": bid, "records": records},
                             body_sha, app.served, redaction=spec)
    if status != 202:
        return status, receipt
    with app.store.conn() as c:
        c.execute("INSERT OR IGNORE INTO redactions (redaction_id, batch_id, machine_id, tbl, ids, reason, state,"
                  " counts, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                  (rid, bid, m["machine_id"], table, json.dumps(spec["ids"]), spec["reason"], "queued", "{}",
                   of_events.now_iso()))
    return 202, {"redaction_id": rid, "state": "queued"}


def redaction_state(app, rid, machine=None):
    with app.store.conn() as c:
        r = c.execute("SELECT r.machine_id, r.state, r.counts, r.error, b.state, b.error FROM redactions r"
                      " LEFT JOIN batches b ON b.batch_id = r.batch_id WHERE r.redaction_id = ?", (rid,)).fetchone()
    if not r or (machine is not None and not machine["tenant"] and r[0] != machine["machine_id"]):
        return None
    state = r[1] if r[1] in ("done", "cancelled") else ("failed" if r[4] == "dead" else "queued")
    return {"redaction_id": rid, "state": state, "counts": json.loads(r[2] or "{}"), "error": r[3] or r[5]}


# -------------------------------------------------------------------- writer

UNREACHABLE_ALERT_S = 1800  # the storage unreachable this long: one of.server_alert to the owner


def alert_unreachable(since, error):
    of_events.emit("server_alert", {"kind": "storage_unreachable", "since": of_events.now_iso() if since is None else
                                    time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since)),
                                    "error": f"{type(error).__name__}: {error}"[:300]})


def unreachable(e):
    """Is E a failure to reach the storage at all (refused, reset, timed out, no route, no name), rather than an answer?
    A storage's own error answer (ClickHouse's HTTP error is a RuntimeError) still counts as an attempt."""
    import urllib.error
    return isinstance(e, (urllib.error.URLError, ConnectionError, TimeoutError)) and not isinstance(e, urllib.error.HTTPError)


class Writer(threading.Thread):
    """Drains the queue in order: fan out per table, insert per database, commit the receipt."""

    def __init__(self, store, buffer, router_factory, storage, cfg, ship_cfg, masker=None):
        super().__init__(daemon=True, name="of-server-writer")
        self.store, self.buffer, self.router_factory, self.cfg, self.ship_cfg = store, buffer, router_factory, cfg, ship_cfg
        self.masker = masker
        self.ch = storage  # the storage plugin; the name stays for its single method, insert()
        self.stop = threading.Event()
        self.last_error = None
        self.rate_bps = None  # drain throughput, bytes of queued batches per second (moving average)
        self.retry_at, self.unreachable = 0.0, 0  # the storage could not be reached: wait, doubling, up to a minute
        self.unreachable_since, self.unreachable_said = None, False  # one owner alert per long outage

    def _measure(self, nbytes, seconds):
        if nbytes <= 0 or seconds <= 0:
            return
        r = nbytes / seconds
        self.rate_bps = r if self.rate_bps is None else 0.8 * self.rate_bps + 0.2 * r

    parent_pid = None  # set when running as a child process: exit when the parent is gone

    def orphaned(self):
        if self.parent_pid and os.getppid() != self.parent_pid:
            self.stop.set()
            return True
        return False

    def run(self):
        self.heartbeat()
        while not self.stop.is_set() and not self.orphaned():
            try:
                self.drain()
            except Exception as e:  # keep the writer alive
                self.last_error = f"{type(e).__name__}: {e}"
            self.heartbeat()
            self.stop.wait(self.cfg["flush_every"])

    def heartbeat(self):
        """For a server whose writer runs in another process (ExternalWriter reads it)."""
        path = os.path.join(self.cfg["state_dir"], "writer.json")
        try:
            with open(path + ".tmp", "w") as fh:
                json.dump({"ts": time.time(), "pid": os.getpid(), "last_error": self.last_error,
                           "rate_bps": self.rate_bps}, fh)
            os.replace(path + ".tmp", path)
        except OSError:
            pass


    def drain(self, limit=50):
        if time.time() < self.retry_at:
            return 0
        with self.store.conn() as c:
            todo = c.execute("SELECT seq, batch_id, attempts FROM batches WHERE state = 'queued' ORDER BY seq LIMIT ?",
                             (limit,)).fetchall()
        if not todo:
            return 0
        router = self.router_factory()
        done = 0
        for seq, bid, attempts in todo:
            t0 = time.time()
            try:
                size = os.path.getsize(self.buffer.path(bid)) if hasattr(self.buffer, "path") else 0
            except OSError:
                size = 0
            try:
                batch = self.buffer.get(bid)
                spec = batch.get("redaction")
                counts = self._redact(spec, bid, router, batch, seq) if spec else None
                by_table = {}
                for rec in batch["records"]:
                    by_table.setdefault(rec["type"], []).append(rec["data"])
                sightings = {}
                for kind, tag, org, tbl, s in batch.get("sightings") or []:  # found before the queue (relay)
                    sightings[(kind, tag, org, tbl)] = s
                mask = self._mask_fn(sightings) if self.masker else None
                org_of_db = {db: org for org, db in router.db_by_org.items()}
                for table, rows in by_table.items():
                    for database, out in of_route.fanout(table, rows, router, self.ship_cfg, mask,
                                                         copies=not self.cfg.get("orgs")).items():
                        self.ch.insert(database, table, out)
                        if table == "event" and database in org_of_db:  # the organization's coordination feed
                            of_server_coord.copy(self.store, org_of_db[database], out)
                if sightings:
                    self.ch.insert(self.ship_cfg["vault_database"], "event", self._findings(bid, sightings))
                if spec:
                    self._redacted(spec, counts, router)
            except Exception as e:
                if unreachable(e):
                    # No network (a sleep, a lost link, the storage restarting): the batch waits for it, and no
                    # attempt counts against it (of_conductor, msg 01a1d63f-d434: batches died within seconds).
                    self.unreachable += 1
                    self.retry_at = time.time() + min(60, 2 ** min(self.unreachable, 6))
                    self.last_error = f"{bid}: storage unreachable ({type(e).__name__}: {e})"[:500]
                    self.unreachable_since = self.unreachable_since or time.time()
                    if not self.unreachable_said and time.time() - self.unreachable_since >= UNREACHABLE_ALERT_S:
                        # a wrong host or a storage that never returns says so once (of_review on PR 112, note 5)
                        self.unreachable_said = True
                        alert_unreachable(self.unreachable_since, e)
                    break
                attempts += 1
                dead = attempts >= self.cfg["max_attempts"]
                with self.store.conn() as c:  # never over a batch another writer committed
                    c.execute("UPDATE batches SET attempts = ?, error = ?, state = ? WHERE seq = ? AND state = 'queued'",
                              (attempts, f"{type(e).__name__}: {e}"[:500], "dead" if dead else "queued", seq))
                if dead:
                    self.buffer.dead(bid)
                    alert_dead(bid, attempts, e)
                self.last_error = f"{bid}: {e}"
                break  # keep order: retry this batch first next time
            self.unreachable, self.retry_at, self.unreachable_since, self.unreachable_said = 0, 0.0, None, False
            with self.store.conn() as c:
                c.execute("UPDATE batches SET state = 'committed', committed_at = ?, error = NULL WHERE seq = ?",
                          (of_events.now_iso(), seq))
            self.buffer.done(bid)
            done += 1
            self._measure(size, time.time() - t0)
            self.heartbeat()  # a long drain still reports
            if self.orphaned():
                break
        if done:
            self.last_error = None
        return done


    def _databases(self, router):
        dbs = list(dict.fromkeys([*router.db_by_org.values(), router.unassigned, self.ship_cfg["vault_database"]]))
        return [d for d in dbs if d]

    def _redact(self, spec, bid, router, batch, seq):
        """Delete the named rows everywhere this server writes, and from dead batch files, before
        the replacement is inserted (docs/server.md, Redaction). A replacement goes where its row
        was: before anything is deleted, each replacement's `org_id` becomes the organization whose
        database held the row, saved into this batch's own file so a retry keeps it (2026-11-06:
        replacements routed afresh landed in the unassigned database)."""
        table, host = spec["table"], spec.get("host")
        keys = [tuple(k) for k in spec["keys"]]
        if not spec.get("located"):
            org_of_db = {db: org for org, db in router.db_by_org.items()}
            where = {}
            for db, org in org_of_db.items():
                for k in self.ch.locate(db, table, keys, host):
                    where.setdefault(k, org)
            for r in batch.get("records") or []:
                k = _row_key(table, r["data"])
                if k in where:
                    r["data"]["org_id"] = where[k]
            spec["located"] = len(where)
            self.buffer.put(bid, batch)
        counts = {}
        for db in self._databases(router):
            counts[db] = self.ch.delete(db, table, keys, host)
        counts["disk"] = self._redact_files(table, set(keys), host, bid)
        with self.store.conn() as c:
            old = json.loads((c.execute("SELECT counts FROM redactions WHERE redaction_id = ?",
                                        (spec["redaction_id"],)).fetchone() or ["{}"])[0] or "{}")
            merged = {k: max(v, old.get(k, 0)) for k, v in counts.items()}  # a retry finds less
            c.execute("UPDATE redactions SET counts = ? WHERE redaction_id = ?", (json.dumps(merged), spec["redaction_id"]))
        return merged

    def _redact_files(self, table, keys, host, own_bid):
        """Dead batches only. Every queued batch received before this redaction is committed by now
        (the writer keeps order), and a later one cannot hold the old rows (the duplicate check
        drops them); another redaction's file holds replacements, never touched (2026-11-06: this
        stripped them)."""
        dirs = [getattr(self.buffer, "dead_dir", None)]
        probes = [str(k[-1]) for k in keys]
        dropped = 0
        for d in [x for x in dirs if x]:
            for e in os.scandir(d):
                if not e.name.endswith(".json") or e.name == own_bid + ".json":
                    continue
                try:
                    with open(e.path, encoding="utf-8") as fh:
                        text = fh.read()
                except OSError:
                    continue
                if not any(p in text for p in probes):
                    continue
                payload = json.loads(text)
                if payload.get("redaction"):
                    continue
                keep = [r for r in payload.get("records") or []
                        if not (r.get("type") == table and _row_key(table, r.get("data") or {}) in keys
                                and (host is None or (r.get("data") or {}).get("host_id") == host))]
                n = len(payload.get("records") or []) - len(keep)
                if not n:
                    continue
                payload["records"] = keep
                tmp = e.path + ".tmp"
                with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
                    json.dump(payload, fh, ensure_ascii=False)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, e.path)
                dropped += n
        return dropped

    def _redacted(self, spec, counts, router):
        """`of.redacted`, without content, into the vault and each organization database that held
        the rows; its id is stable per redaction and database, so a retry writes nothing new."""
        import uuid
        org_of_db = {db: org for org, db in router.db_by_org.items()}
        payload = {k: spec[k] for k in ("redaction_id", "table", "ids", "sha_old", "sha_new", "reason", "machine_id")}
        payload["counts"] = {k: v for k, v in counts.items()}
        vault = self.ship_cfg["vault_database"]
        targets = [vault] + [db for db, n in counts.items() if n and db in org_of_db]
        for db in dict.fromkeys(targets):
            row = server_event(self.ship_cfg["tenant"], org_of_db.get(db, ""), "of.redacted", payload)
            eid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"of-redacted:{spec['redaction_id']}:{db}"))
            rec = json.loads(row["record"])
            rec["of.event.id"] = eid
            row.update(event_id=eid, record=json.dumps(rec, ensure_ascii=False))
            self.ch.insert(db, "event", [row])
        with self.store.conn() as c:
            c.execute("UPDATE redactions SET state = 'done', done_at = ?, error = NULL WHERE redaction_id = ?",
                      (of_events.now_iso(), spec["redaction_id"]))

    def _mask_fn(self, sightings):
        def mask(table, row):
            out, found = of_mask.mask_row(self.masker, table, row)
            if found:
                ts = row.get("ts") or row.get("received_at") or ""
                loc = _location(table, row)
                for (kind, tag), n in found.items():
                    k = (kind, tag, row.get("org_id") or "", table)
                    s = sightings.setdefault(k, {"count": 0, "first_ts": ts, "last_ts": ts, "location": loc})
                    s["count"] += n
                    s["first_ts"], s["last_ts"] = min(s["first_ts"], ts), max(s["last_ts"], ts)
            return out
        return mask

    def _findings(self, batch_id, sightings):
        """Record this batch's sightings once (a retried batch adds nothing), then build one
        `of.secret_detected` row per (kind, hmac, organization) with its totals so far. Its
        event id and ts are stable, so ReplacingMergeTree keeps one row per secret."""
        now = of_events.now_iso()
        with self.store.conn() as c:
            for (kind, tag, org, tbl), s in sightings.items():
                c.execute("INSERT OR IGNORE INTO secret_sightings VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (batch_id, kind, tag, org, tbl, s["count"], s["first_ts"], s["last_ts"], s["location"], now))
            keys = sorted({(k, t, o) for k, t, o, _ in sightings})
            rows = []
            for kind, tag, org in keys:
                agg = c.execute("SELECT sum(count), min(first_ts), max(last_ts), min(detected_at), count(DISTINCT batch_id),"
                                " group_concat(DISTINCT tbl) FROM secret_sightings WHERE kind = ? AND hmac = ? AND org = ?",
                                (kind, tag, org)).fetchone()
                last = c.execute("SELECT location FROM secret_sightings WHERE kind = ? AND hmac = ? AND org = ?"
                                 " ORDER BY last_ts DESC LIMIT 1", (kind, tag, org)).fetchone()[0]
                rows.append(_finding_row(self.ship_cfg["tenant"], kind, tag, org, agg, last))
        return rows


class ExternalWriter:
    """The writer as a child process (`of_server.py writer`), so masking, which is CPU-bound and
    holds the GIL in long regex matches, cannot starve the HTTP threads. Supervised: restarted
    if it exits. Its state comes from the heartbeat file it writes every loop."""

    STALE = 60

    def __init__(self, cfg, config_path=None):
        self.cfg, self.config_path = cfg, config_path
        self.stop = threading.Event()
        self.proc = None

    def _hb(self):
        try:
            with open(os.path.join(self.cfg["state_dir"], "writer.json")) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    @property
    def rate_bps(self):
        return (self._hb() or {}).get("rate_bps")

    @property
    def last_error(self):
        hb = self._hb()
        if hb is None:
            return "writer process has not reported yet"
        if time.time() - hb.get("ts", 0) > self.STALE:
            return f"writer process silent for {int(time.time() - hb.get('ts', 0))} s"
        return hb.get("last_error")

    def start(self):
        threading.Thread(target=self._supervise, daemon=True, name="of-server-writer-supervisor").start()

    def _supervise(self):
        import sys
        argv = [sys.executable, os.path.abspath(__file__)]
        if self.config_path:
            argv += ["--config", self.config_path]
        while not self.stop.is_set():
            self.proc = subprocess.Popen(argv + ["writer"], stdin=subprocess.DEVNULL,
                                         env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
            self.proc.wait()
            if not self.stop.wait(2):
                print(f"of-server: writer process exited ({self.proc.returncode}); restarting", flush=True)

    def terminate(self):
        self.stop.set()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(30)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def _location(table, row):
    """Where a secret was seen, by record identity, never by content."""
    if table == "transcript_record":
        return f"transcript_record:{row.get('session_id', '')}/{row.get('record_uuid', '')}"
    if table == "hook_record":
        return f"hook_record:{row.get('host_id', '')}/{row.get('local_id', '')}"
    if table == "event":
        return f"event:{row.get('event_id', '')}"
    return f"{table}:{row.get('host_id', '')}/{row.get('received_at', '')}/{row.get('type', '')}"


def _finding_row(tenant, kind, tag, org, agg, last_location):
    count, first_ts, last_ts, detected_at, batches, tables = agg
    eid = hashlib.sha256(f"of.secret_detected|{kind}|{tag}|{org}".encode()).hexdigest()[:32]
    payload = {"kind": kind, "hmac": tag, "org": org or None, "count": count, "batches": batches,
               "first_seen": first_ts or None, "last_seen": last_ts or None, "tables": sorted((tables or "").split(",")),
               "last_location": last_location}
    record = {"of.schema": 1, "of.event.id": eid, "timestamp": detected_at, "event.name": "of.secret_detected",
              "of.source": "of-server", "of.org": org or None, "of.payload": payload}
    return {"tenant_id": tenant, "org_id": org, "host_id": "of-server", "event_id": eid, "ts": detected_at,
            "name": "of.secret_detected", "session_id": "", "position": "", "source": "of-server",
            "harness_name": "", "harness_version": "", "model": "",
            "payload": json.dumps(payload, ensure_ascii=False), "raw_ref": "",
            "record": json.dumps(record, ensure_ascii=False)}


def replay_dead(store, buffer, batch_ids=None):
    """Put dead batches back in the queue (all, or the given ids) once the cause is fixed. The
    writers retry them in order; natural keys make a partly written batch safe to write again."""
    with store.conn() as c:
        # A cancelled redaction stays dead: replaying it would run what was cancelled.
        rows = c.execute("SELECT seq, batch_id FROM batches WHERE state = 'dead' AND batch_id NOT IN"
                         " (SELECT batch_id FROM redactions WHERE state = 'cancelled') ORDER BY seq").fetchall()
    done = []
    for seq, bid in rows:
        if batch_ids and bid not in batch_ids:
            continue
        if not buffer.revive(bid):
            continue
        with store.conn() as c:
            c.execute("UPDATE batches SET state = 'queued', attempts = 0, error = NULL WHERE seq = ?", (seq,))
        done.append(bid)
    if done:
        of_events.emit("server_alert", {"kind": "dead_replayed", "batches": done})
    return done


def alert_dead(batch_id, attempts, error):
    """A dead batch is data that did not reach ClickHouse: tell the owner at once."""
    of_events.emit("server_alert", {"kind": "dead_batch", "batch_id": batch_id, "attempts": attempts,
                                    "error": f"{type(error).__name__}: {error}"[:300]})
    try:
        subprocess.run(["herdr", "notification", "show", "of-server: a batch failed for good",
                        "--body", f"{batch_id}: {error}"[:200], "--sound", "request"],
                       capture_output=True, timeout=5)
    except Exception:
        pass


def decompress(raw, encoding, limit):
    """Bounded: never inflates more than `limit` bytes (a small body can expand without bound)."""
    if encoding in ("", "identity"):
        return raw
    if encoding == "gzip":
        import zlib
        d = zlib.decompressobj(wbits=31)
        out = d.decompress(raw, limit + 1)
        if len(out) > limit or d.unconsumed_tail:
            raise ValueError("413")
        return out
    if encoding == "zstd" and zstd is not None:
        d = zstd.ZstdDecompressor()
        out = d.decompress(raw, max_length=limit + 1)
        if len(out) > limit or not d.eof:
            raise ValueError("413")
        return out
    raise ValueError("415")


def queue_bytes(state_dir):
    try:
        return sum(e.stat().st_size for e in os.scandir(os.path.join(state_dir, "queue")) if e.name.endswith(".json"))
    except OSError:
        return 0


def health(store, writer):
    """`queue_bytes`, `drain_rate_bps` and `drain_eta_s` show a backlog that is draining (expected
    after a large import) apart from a stall (`writer_error` set, or no rate while batches wait)."""
    with store.conn() as c:
        st = dict(c.execute("SELECT state, count(*) FROM batches GROUP BY state").fetchall())
        oldest = c.execute("SELECT min(created_at) FROM batches WHERE state = 'queued'").fetchone()[0]
    qb = queue_bytes(store.dir) if st.get("queued") else 0
    rate = getattr(writer, "rate_bps", None)
    eta = int(qb / rate) if qb and rate else (0 if not qb else None)
    return {"ok": not st.get("dead") and writer.last_error is None, "version": VERSION, "profile_plugins": PLUGINS,
            "queued": st.get("queued", 0), "committed": st.get("committed", 0), "dead": st.get("dead", 0),
            "oldest_queued": oldest, "writer_error": writer.last_error, "queue_bytes": qb,
            "drain_rate_bps": round(rate) if rate else None, "drain_eta_s": eta}


# ---------------------------------------------------------------------- HTTP

def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"of-server/{VERSION}"
        timeout = app.cfg.get("request_timeout") or None  # StreamRequestHandler sets it on the connection

        def log_message(self, fmt, *args):
            pass

        def finish(self):
            try:
                super().finish()
            finally:
                app.store.close_thread()
                try:
                    self.server.accepted.pop(self.connection.fileno(), None)
                except (AttributeError, OSError, ValueError):
                    pass

        def _send(self, status, body, ctype="application/json"):
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype if status < 400 else "application/problem+json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _raw(self, status, ctype, body, headers=()):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in STUDIO_HEADERS + tuple(headers):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _studio(self, method, u):
            q = parse_qs(u.query)
            if u.path == "/studio/open":
                session, viewer, next_path = studio_open(app, (q.get("t") or [""])[0])
                if not session:
                    return self._raw(401, "text/plain; charset=utf-8", b"studio: the link is unknown, used or expired")
                secure = "; Secure" if (app.cfg.get("public_url") or "").startswith("https://") else ""
                return self._raw(303, "text/plain; charset=utf-8", b"", (
                    ("Set-Cookie", f"of_studio={session}; HttpOnly; SameSite=Strict; Path=/studio/"
                                   f"; Max-Age={int(app.cfg['studio_session_ttl'])}{secure}"),
                    ("Location", next_path)))
            viewer = studio_viewer(app, self.headers.get("Cookie") or "")
            if viewer is None:
                return self._raw(401, "text/plain; charset=utf-8", b"studio: open it with `of studio`")
            try:
                of_studio = studio_module()
            except ImportError:
                return self._raw(404, "text/plain; charset=utf-8", b"studio is not installed on this server")
            if app.studio_source is not None:
                status, ctype, body = of_studio.handle(method, u.path, u.query, viewer, source=app.studio_source)
            else:
                status, ctype, body = of_studio.handle(method, u.path, u.query, viewer)
            if status == 200 and (ctype or "").startswith("text/html"):
                try:
                    studio_viewed(app, viewer, u.path, q)
                except Exception as e:  # recording a view never breaks the page
                    print(f"of-server: studio_viewed not recorded: {type(e).__name__}: {e}", flush=True)
            return self._raw(status, ctype, body if isinstance(body, bytes) else str(body).encode())

        def _relay(self, fn, status=200):
            import of_server_relay as of_relay
            try:
                return self._send(status, fn())
            except of_relay.RelayError as e:
                return self._problem(e.status, e.typ, e.title, e.extra)

        def _passkey(self, fn, status=200):
            import of_server_passkey as P
            try:
                return self._send(status, fn())
            except P.Refused as e:
                return self._problem(e.status, e.typ, e.title)

        def _passkey_open(self, token, page=False):
            """The unauthenticated passkey routes, rate-limited per window id or approval nonce: behind a tunnel every
            phone shares one address, so a per-address limit would let anyone use up everyone's (of_secrets-review on
            organic-framework PR 81). Only an existing token is counted, so random ones keep nothing in memory
            (of_review's probe on the same PR). A token is 24 random bytes, so guessing one is no way in. True: go on."""
            if not app.passkeys.token_known(token):
                if page:
                    body = b"no such window or link (it expired, closed or was used)"
                    self.send_response(404)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self._problem(404, "not_found", "no such window or link (it expired, closed or was used)")
                return False
            key = f"t:{str(token)[:64]}"
            if app.passkey_rate.blocked(key):
                self._problem(429, "too_many", "too many requests for this window or link; wait")
                return False
            app.passkey_rate.add(key)
            return True

        def _passkey_page(self, render):
            import of_server_passkey as P
            nonce = secrets.token_urlsafe(16)
            try:
                body, status, ctype = render(nonce), 200, "text/html; charset=utf-8"
            except P.Refused as e:
                body, status, ctype = e.title.encode(), e.status, "text/plain; charset=utf-8"
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in P.headers(nonce):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _problem(self, status, typ, title, extra=None):
            self._send(status, {"type": typ, "title": title, "status": status, **(extra or {})})

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > app.cfg["max_body"]:
                raise ValueError("413")
            raw = self.rfile.read(n)
            return decompress(raw, (self.headers.get("Content-Encoding") or "").lower(), app.cfg["max_body"])

        def _machine(self):
            auth = self.headers.get("Authorization") or ""
            if not auth.startswith("Bearer "):
                return None
            sha = hashlib.sha256(auth[7:].encode()).hexdigest()
            with app.store.conn() as c:
                row = c.execute("SELECT m.machine_id, m.orgs, m.raw_orgs, m.tenant FROM tokens t JOIN machines m"
                                " ON m.machine_id = t.machine_id WHERE t.token_sha = ? AND t.expires > ?"
                                " AND m.revoked_at IS NULL", (sha, time.time())).fetchone()
            return dict(zip(("machine_id", "orgs", "raw_orgs", "tenant"), row)) if row else None

        def do_GET(self):
            u = urlparse(self.path)
            try:
                if u.path == "/v1/health":
                    try:
                        t0 = self.server.accepted.get(self.connection.fileno()) or time.monotonic()
                    except (AttributeError, OSError, ValueError):
                        t0 = time.monotonic()
                    try:
                        h = health(app.store, app.writer)
                        self._send(200, h if app.cfg.get("health_detail", True) else {"ok": h["ok"], "version": VERSION})
                    finally:  # an answer its client no longer waited for is the slowest kind (of_review, PR 142)
                        app.health_checks.answered(time.monotonic() - t0)  # server.health, as answered
                    return None
                if u.path == "/v1/schema":
                    return self._send(200, {"schemas": list(SCHEMAS), "types": {k: list(v) for k, v in REQUIRED.items()}})
                if u.path == "/studio" or u.path.startswith("/studio/"):
                    return self._studio("GET", u)
                if u.path in ("/passkey/enroll", "/approve/diff") or (
                        u.path.startswith("/v1/passkey/windows/") and u.path.endswith("/options")):
                    import of_server_passkey as P  # docs/remote.md §6: the phone's pages, behind a single-use token
                    q = {k: v[0] for k, v in parse_qs(u.query).items()}
                    if not self._passkey_open(q.get("w") or q.get("n") or u.path.split("/")[-2],
                                              page=not u.path.startswith("/v1/")):
                        return None
                    if u.path == "/passkey/enroll":
                        return self._passkey_page(lambda n: (app.passkeys.window_options(q.get("w")),
                                                             P.enroll_page(q.get("w"), n))[1])
                    if u.path == "/approve/diff":
                        return self._passkey_page(lambda n: P.approve_page(dict(app.passkeys.approval_page(
                            q.get("org"), q.get("id"), q.get("digest"), q.get("n")), nonce=q.get("n")), n))
                    return self._passkey(lambda: app.passkeys.window_options(u.path.split("/")[4]))
                if u.path == "/v1/terms":
                    cur = app.cfg["terms_current"]
                    return self._send(200, {"current": cur, "tiers": list(TIERS), "required": ["typed"],
                                            "capabilities": list(CAPABILITIES),
                                            **{k: v for k, v in app.cfg["terms"][cur].items()}})
                m = self._machine()
                if not m:
                    return self._problem(401, "unauthorized", "missing or expired token")
                if u.path == "/v1/receipts":
                    after = int((parse_qs(u.query).get("after") or ["0"])[0])
                    with app.store.conn() as c:
                        rows = c.execute("SELECT seq, batch_id, state, accepted, rejected, by_org, created_at, committed_at,"
                                         " tiers FROM batches WHERE machine_id = ? AND seq > ? ORDER BY seq LIMIT 1000",
                                         (m["machine_id"], after)).fetchall()
                    keys = ("seq", "batch_id", "state", "accepted", "rejected", "by_org", "created_at", "committed_at",
                            "tiers")
                    out = []
                    for r in rows:
                        d = dict(zip(keys, r))
                        d["rejected"], d["by_org"] = len(json.loads(d["rejected"])), json.loads(d["by_org"])
                        d["tiers"] = json.loads(d["tiers"] or "{}")
                        out.append(d)
                    return self._send(200, {"receipts": out})
                if u.path.startswith("/v1/passkey/windows/"):  # the owner's terminal follows its window
                    return self._passkey(lambda: app.passkeys.window_state(m, u.path.split("/")[4]))
                if u.path == "/v1/presence":
                    return self._relay(lambda: app.relay.get_presence(m, (parse_qs(u.query).get("org") or [""])[0]))
                if u.path == "/v1/health/checks":  # the server's own checks, for `of health` (docs/health.md §3)
                    return self._send(200, {"checks": app.health_checks.states()})
                if u.path == "/v1/messages":
                    return self._relay(lambda: app.relay.poll(m, (parse_qs(u.query).get("wait") or ["0"])[0]))
                if u.path.startswith("/v1/redactions/"):
                    st = redaction_state(app, u.path[len("/v1/redactions/"):], m)
                    return self._send(200, st) if st else self._problem(404, "not_found", "unknown redaction")
                if u.path == "/v1/coord":  # docs/server-several-machines.md §4.2
                    q = parse_qs(u.query)
                    try:
                        return self._send(200, of_server_coord.feed(
                            app.store, m, app.relay._machine_orgs(m), (q.get("org") or [""])[0],
                            (q.get("after") or ["0"])[0], (q.get("wait") or ["0"])[0]))
                    except of_server_coord.CoordError as e:
                        return self._problem(e.status, e.typ, e.title)
                if u.path == "/v1/messages/queued":
                    q = parse_qs(u.query)
                    return self._relay(lambda: app.relay.queued(m, (q.get("to") or [""])[0], (q.get("session") or [""])[0]))
                if u.path == "/v1/metrics":
                    if not m["tenant"]:
                        return self._problem(403, "forbidden", "tenant only")
                    with app.store.conn() as c:
                        st = dict(c.execute("SELECT state, count(*) FROM batches GROUP BY state").fetchall())
                        oldest = c.execute("SELECT min(created_at) FROM batches WHERE state = 'queued'").fetchone()[0]
                    return self._send(200, {"batches": st, "oldest_queued": oldest, "writer_error": app.writer.last_error})
                return self._problem(404, "not_found", u.path)
            except Exception as e:
                return self._problem(500, "internal", f"{type(e).__name__}: {e}")

        def do_PUT(self):
            u = urlparse(self.path)
            try:
                try:
                    raw = self._body()
                except ValueError:
                    return self._problem(413, "too_large", "over the body limit")
                m = self._machine()
                if not m:
                    return self._problem(401, "unauthorized", "missing or expired token")
                if u.path == "/v1/presence":
                    return self._relay(lambda: app.relay.put_presence(m, json.loads(raw or b"{}")))
                if u.path == "/v1/consent":
                    try:
                        return self._send(200, update_consent(app.store, app.buffer, app.cfg, app.ship_cfg["tenant"], m,
                                                              json.loads(raw or b"{}")))
                    except EnrollError as e:
                        return self._problem(e.status, e.typ, e.title)
                return self._problem(404, "not_found", u.path)
            except Exception as e:
                return self._problem(500, "internal", f"{type(e).__name__}: {e}")

        def do_DELETE(self):
            u = urlparse(self.path)
            try:
                m = self._machine()
                if not m:
                    return self._problem(401, "unauthorized", "missing or expired token")
                if not m["tenant"]:
                    return self._problem(403, "forbidden", "tenant only")
                if u.path.startswith("/v1/machines/"):
                    ids = [u.path.rsplit("/", 1)[1]]
                elif u.path == "/v1/machines" and parse_qs(u.query).get("person"):
                    person = parse_qs(u.query)["person"][0]
                    with app.store.conn() as c:
                        ids = [r[0] for r in c.execute("SELECT machine_id FROM machines WHERE person = ? AND revoked_at"
                                                       " IS NULL", (person,))]
                else:
                    return self._problem(404, "not_found", u.path)
                done = revoke_machines(app.store, app.buffer, app.ship_cfg["tenant"], ids, by=m["machine_id"])
                return self._send(200, {"revoked": done})
            except Exception as e:
                return self._problem(500, "internal", f"{type(e).__name__}: {e}")

        def do_POST(self):
            u = urlparse(self.path)
            try:
                try:
                    raw = self._body()
                except ValueError as e:
                    if str(e) == "415":
                        return self._problem(415, "unsupported_encoding", "Content-Encoding not supported")
                    return self._problem(413, "too_large", "over the body limit")
                if u.path == "/v1/auth/challenge":
                    body = json.loads(raw or b"{}")
                    nonce = secrets.token_urlsafe(32)
                    with app.store.conn() as c:
                        c.execute("DELETE FROM challenges WHERE expires < ?", (time.time(),))
                        c.execute("INSERT INTO challenges VALUES (?,?,?)",
                                  (nonce, body.get("machine_id"), time.time() + app.cfg["challenge_ttl"]))
                    return self._send(200, {"nonce": nonce, "namespace": NAMESPACE})
                if u.path == "/v1/auth/token":
                    body = json.loads(raw or b"{}")
                    mid, nonce = body.get("machine_id"), body.get("nonce")
                    with app.store.conn() as c:
                        ch = c.execute("SELECT machine_id FROM challenges WHERE nonce = ? AND expires > ?",
                                       (nonce, time.time())).fetchone()
                        c.execute("DELETE FROM challenges WHERE nonce = ?", (nonce,))
                        m = c.execute("SELECT pubkey FROM machines WHERE machine_id = ? AND revoked_at IS NULL",
                                      (mid,)).fetchone()
                    if not ch or ch[0] != mid or not m or \
                            not app.identity.verify(mid, m[0], (nonce or "").encode(), body.get("signature") or ""):
                        return self._problem(401, "unauthorized", "unknown machine, bad challenge or bad signature")
                    token = secrets.token_urlsafe(32)
                    expires = time.time() + app.cfg["token_ttl"]
                    with app.store.conn() as c:
                        c.execute("DELETE FROM tokens WHERE expires < ?", (time.time(),))
                        c.execute("INSERT INTO tokens VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), mid, expires))
                    return self._send(200, {"token": token, "expires": expires})
                if u.path == "/v1/enroll":
                    ip = self.client_address[0]
                    if app.bad_codes.blocked(ip):
                        return self._problem(429, "too_many", "too many bad invite codes; wait")
                    try:
                        return self._send(200, enroll_with_invite(app.store, app.buffer, app.cfg, app.ship_cfg["tenant"],
                                                                  json.loads(raw or b"{}")))
                    except EnrollError as e:
                        if e.status in (404, 410):
                            app.bad_codes.add(ip)
                        return self._problem(e.status, e.typ, e.title)
                if u.path == "/v1/passkey/approve" or (u.path.startswith("/v1/passkey/windows/") and
                                                       u.path.endswith(("/register", "/test"))):
                    try:
                        b = json.loads(raw or b"{}") or {}
                    except ValueError:
                        return self._problem(400, "bad_request", "the body is not JSON")
                    if not self._passkey_open(b.get("nonce") if u.path == "/v1/passkey/approve" else u.path.split("/")[4]):
                        return None
                    if u.path.endswith("/register"):
                        return self._passkey(lambda: app.passkeys.register(
                            u.path.split("/")[4], b.get("attestation_object"), b.get("client_data_json")))
                    if u.path.endswith("/test"):
                        return self._passkey(lambda: app.passkeys.test(
                            u.path.split("/")[4], b.get("credential"), b.get("authenticator_data"),
                            b.get("client_data_json"), b.get("signature")))

                    def approve():
                        import of_server_passkey as P
                        payload = app.passkeys.approve(b.get("nonce"), b.get("credential"), b.get("authenticator_data"),
                                                       b.get("client_data_json"), b.get("signature"))
                        of_server_coord.copy(app.store, payload["org"], [P.feed_row(payload["org"], payload)])
                        return {"approved": payload["id"], "digest": payload["digest"]}
                    return self._passkey(approve)
                m = self._machine()
                if not m:
                    return self._problem(401, "unauthorized", "missing or expired token")
                if u.path.startswith("/v1/passkey/"):  # docs/remote.md §6: the tenant's terminal and `of diff link`
                    try:
                        b = json.loads(raw or b"{}") or {}
                    except ValueError:
                        return self._problem(400, "bad_request", "the body is not JSON")
                    routes = {
                        "/v1/passkey/windows": lambda: app.passkeys.open_window(m, b.get("org"), b.get("person"),
                                                                                b.get("ttl") or 600),
                        "/v1/passkey/enrollments": lambda: app.passkeys.enroll(m, b.get("record")),
                        "/v1/passkey/links": lambda: app.passkeys.put_link(m, b.get("org"), b.get("id"), b.get("diff"),
                                                                           b.get("digest"), b.get("ttl") or 900),
                        "/v1/passkey/revocations": lambda: app.passkeys.revoke(m, b.get("record")),
                    }
                    if u.path in routes:
                        return self._passkey(routes[u.path])
                    return self._problem(404, "not_found", "no such passkey route")
                if u.path == "/v1/studio/link":
                    if not m["tenant"]:
                        return self._problem(403, "forbidden", "tenant only")
                    try:
                        b = json.loads(raw or b"{}") or {}
                        nxt, who = b.get("next"), b.get("requester")
                    except (ValueError, AttributeError):
                        return self._problem(400, "bad_request", "body must be a JSON object")
                    return self._send(200, {"url": studio_link(app, self.headers.get("Host"), m, next_path=nxt,
                                                               requester=studio_requester(who))})
                if u.path == "/v1/invites":
                    if not m["tenant"]:
                        return self._problem(403, "forbidden", "tenant only")
                    b = json.loads(raw or b"{}")
                    try:
                        return self._send(200, create_invite(app.store, app.cfg, b.get("orgs") or [],
                                                             b.get("raw_allowed") or [], b.get("person"), b.get("note"),
                                                             b.get("ttl_s"), created_by=m["machine_id"]))
                    except ValueError as e:
                        return self._problem(400, "bad_request", str(e))
                if u.path == "/v1/otlp/v1/logs":
                    if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                        return self._problem(415, "unsupported_media_type", "OTLP/JSON only: set the exporter to http/json")
                    import of_server_otlp
                    try:
                        body = json.loads(raw or b"{}")
                    except ValueError:
                        return self._problem(400, "bad_request", "body is not JSON")
                    recs = of_server_otlp.records(body, app.ship_cfg["tenant"], m["machine_id"])
                    batch = {"batch_id": "otlp-" + hashlib.sha256(raw).hexdigest()[:32], "records": recs}
                    status, receipt = accept(app.store, app.buffer, app.router(), m, batch,
                                             hashlib.sha256(raw).hexdigest(), app.served)
                    rej = receipt.get("rejected") or []
                    out = {"partialSuccess": {"rejectedLogRecords": len(rej),
                                              "errorMessage": "; ".join(sorted({r["reason"] for r in rej}))[:500]}} \
                        if rej else {}
                    return self._send(200 if status == 202 else status, out)
                if u.path == "/v1/messages":
                    return self._relay(lambda: app.relay.post(m, json.loads(raw or b"{}")), status=202)
                if u.path.startswith("/v1/messages/") and u.path.endswith("/ack"):
                    msg_id = u.path[len("/v1/messages/"):-len("/ack")]
                    return self._relay(lambda: app.relay.ack(m, msg_id, json.loads(raw or b"{}")))
                if u.path.startswith("/v1/messages/") and u.path.endswith("/take"):
                    msg_id = u.path[len("/v1/messages/"):-len("/take")]
                    return self._relay(lambda: app.relay.take(m, msg_id, json.loads(raw or b"{}")))
                if u.path == "/v1/redactions":
                    try:
                        body = json.loads(raw or b"{}")
                    except ValueError:
                        return self._problem(400, "bad_request", "body is not JSON")
                    status, out = create_redaction(app, m, body if isinstance(body, dict) else {},
                                                   hashlib.sha256(raw).hexdigest())
                    return self._send(status, out)
                if u.path == "/v1/grants/verify":
                    body = json.loads(raw or b"{}")
                    org = (body.get("subject") or "").rpartition(":")[0]
                    if not m["tenant"] and org not in json.loads(m["orgs"]):
                        return self._problem(403, "forbidden", "organization not joined by this machine")
                    return self._send(200, app.verify_grant(body.get("grant_id"), body.get("subject"), body.get("action")))
                if u.path == "/v1/batches":
                    try:
                        batch = json.loads(raw)
                    except Exception:
                        return self._problem(400, "bad_request", "body is not JSON")
                    if not batch.get("batch_id") or not isinstance(batch.get("records"), list):
                        return self._problem(400, "bad_request", "batch_id and records are required")
                    if batch.get("machine_id") not in (None, m["machine_id"]):
                        return self._problem(403, "forbidden", "machine_id does not match the token")
                    if batch.get("schema") not in SCHEMAS:
                        return self._problem(400, "schema", f"schema {batch.get('schema')} not in {list(SCHEMAS)}")
                    status, receipt = accept(app.store, app.buffer, app.router(), m, batch, hashlib.sha256(raw).hexdigest(),
                                             app.served)
                    return self._send(status, receipt)
                if u.path == "/v1/secrets/use":  # docs/agent-secrets.md §5.3
                    try:
                        status, answer = of_server_secrets.use(app, m, json.loads(raw or b"{}"))
                    except ValueError:
                        return self._problem(400, "bad_request", "body is not JSON")
                    except of_server_secrets.SecretsError as e:
                        return self._problem(e.status, e.typ, e.title)
                    return self._send(status, answer)
                if u.path == "/v1/finance_facts":  # docs/events.md, Finance facts
                    try:
                        status, answer = of_server_finance.write(app, m, json.loads(raw or b"{}"), accept)
                    except ValueError:
                        return self._problem(400, "bad_request", "body is not JSON")
                    except of_server_finance.FinanceError as e:
                        return self._problem(e.status, e.typ, e.title)
                    return self._send(status, answer)
                return self._problem(404, "not_found", u.path)
            except Exception as e:
                traceback.print_exc()
                return self._problem(500, "internal", f"{type(e).__name__}: {e}")
    return Handler


STUDIO_HEADERS = (("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; base-uri 'none'"),
                  ("X-Frame-Options", "DENY"), ("X-Content-Type-Options", "nosniff"),
                  ("Referrer-Policy", "no-referrer"), ("Cache-Control", "no-store"))


def studio_module():
    """of_studio as loaded. A changed file under bin/ restarts the whole server (CodeWatch), so Studio
    never runs a new module against an old one (2026-11-07: of_asks called of_classify.q_v3, which the
    server's old of_classify lacked, and Studio answered 500)."""
    import of_studio
    return of_studio


class CodeWatch:
    """The modules this process loaded from its own directory (bin/), with the mtime each had when first
    seen. `changed()` names those whose file changed since, once the files are stable (no write for
    `settle` seconds) and every changed one compiles; a file caught mid-edit or broken keeps the old
    code serving and is tried again on the next check."""

    def __init__(self, here=None, settle=2.0):
        self.here = os.path.realpath(here or os.path.dirname(os.path.abspath(__file__)))
        self.settle, self.seen = settle, {}

    def _loaded(self):
        for m in list(sys.modules.values()):
            f = getattr(m, "__file__", None)
            if f and f.endswith(".py") and os.path.dirname(os.path.realpath(f)) == self.here:
                yield os.path.realpath(f)

    def changed(self):
        now, out = time.time(), []
        for f in self._loaded():
            try:
                mt = os.stat(f).st_mtime
            except OSError:
                continue
            first = self.seen.setdefault(f, mt)
            if mt != first:
                if now - mt < self.settle:
                    return []                    # still being written: wait for the whole deploy
                out.append(f)
        for f in out:
            try:
                with open(f, "rb") as fh:
                    compile(fh.read(), f, "exec")
            except (SyntaxError, ValueError, OSError) as e:
                print(f"of-server: {os.path.basename(f)} changed but does not compile; old code keeps serving: "
                      f"{type(e).__name__}: {str(e)[:200]}", flush=True)
                return []
        return out


def studio_viewed(app, viewer, path, query):
    """`of.studio_viewed`: one successful page load of Studio, no content (events.md). It belongs
    to the organization shown; a view of all organizations goes to `tenant_org` (server.yaml),
    else to the tenant-only unassigned database."""
    sub = path.split("/studio", 1)[-1].strip("/")
    org = (query.get("org") or ["all"])[0] or "all"
    payload = {"viewer": viewer.get("id"), "tenant": bool(viewer.get("tenant")), "scope": viewer.get("scope") or "org",
               "owner": viewer.get("owner"), "view": sub.split("/")[0] or "inbox", "org": org,
               "requester": viewer.get("requester")}
    target = org if org != "all" else app.cfg.get("tenant_org")
    if app.served is None:   # profile 1: the local event log, which Studio reads and the shipper ships
        of_events.emit("studio_viewed", payload, org=target, source="of-server")
    else:
        if target not in app.served:
            target = None
        queue_server_events(app.store, app.buffer, [server_event(app.ship_cfg["tenant"], target or "",
                                                                  "of.studio_viewed", payload)])


def studio_next(path):
    """The page to land on after /studio/open: a path under /studio/ on this server, else /studio/.
    No scheme, host, `//`, backslash, `..` segment or control character, also once percent-decoded."""
    from urllib.parse import unquote
    if not isinstance(path, str) or len(path) > 1024 or not path.startswith("/studio/") \
            or path.startswith("/studio/open"):
        return "/studio/"
    for form in (path, unquote(path)):
        if "//" in form or "\\" in form or any(ord(ch) < 0x21 or ord(ch) == 0x7f for ch in form) \
                or ".." in form.split("?", 1)[0].split("/"):
            return "/studio/"
    return path


STUDIO_REQUESTER = re.compile(r"agent:[A-Za-z0-9_.:-]{1,74}\Z")   # agent:<position>, agent:session:<sid>


def studio_requester(value):
    """The link's requester as stored: `agent:…` (at most 80 printable characters) as given; any
    other value that starts with `agent:` becomes `agent:invalid`, so an agent's load is still never
    counted as the owner's; anything else (the owner's own request) is None."""
    if not isinstance(value, str) or not value.startswith("agent:"):
        return None
    return value if STUDIO_REQUESTER.match(value) else "agent:invalid"


def studio_link(app, host, m, ttl=300, next_path=None, requester=None):
    """A single-use link valid for 5 minutes; only its hash is stored. `next_path` (checked by
    `studio_next`) is kept with the token: /studio/open redirects there. `requester`
    (`agent:<position>` when an agent's process asked) stays with the session and goes into every
    `of.studio_viewed` it produces, so an agent's load is never counted as the owner's."""
    token = secrets.token_urlsafe(32)
    viewer = {"id": m["machine_id"], "tenant": bool(m["tenant"]), "scope": "org"}
    if requester:
        viewer["requester"] = requester
    if next_path:
        viewer["_next"] = studio_next(next_path)
    with app.store.conn() as c:
        c.execute("DELETE FROM studio_tokens WHERE expires < ?", (time.time(),))
        c.execute("INSERT INTO studio_tokens VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(),
                                                               json.dumps(viewer), time.time() + ttl))
    base = (app.cfg.get("public_url") or f"http://{host or '127.0.0.1:%d' % app.cfg['port']}").rstrip("/")
    return f"{base}/studio/open?t={token}"


def studio_open(app, token):
    """Exchange a link token (once) for (session id, viewer, the page to land on);
    (None, None, None) if unknown, used or expired."""
    sha = hashlib.sha256((token or "").encode()).hexdigest()
    with app.store.conn() as c:
        row = c.execute("DELETE FROM studio_tokens WHERE token_sha = ? AND expires > ? RETURNING viewer",
                        (sha, time.time())).fetchone()
        if not row:
            return None, None, None
        viewer = json.loads(row[0])
        next_path = studio_next(viewer.pop("_next", "/studio/"))
        session = secrets.token_urlsafe(32)
        c.execute("DELETE FROM studio_sessions WHERE expires < ?", (time.time(),))
        c.execute("INSERT INTO studio_sessions VALUES (?,?,?)", (hashlib.sha256(session.encode()).hexdigest(),
                                                                 json.dumps(viewer),
                                                                 time.time() + app.cfg["studio_session_ttl"]))
    return session, viewer, next_path


def studio_viewer(app, cookie_header):
    from http.cookies import SimpleCookie
    try:
        jar = SimpleCookie(cookie_header)
    except Exception:
        return None
    if "of_studio" not in jar:
        return None
    sha = hashlib.sha256(jar["of_studio"].value.encode()).hexdigest()
    with app.store.conn() as c:
        row = c.execute("SELECT viewer FROM studio_sessions WHERE session_sha = ? AND expires > ?",
                        (sha, time.time())).fetchone()
    return json.loads(row[0]) if row else None


class StudioData:
    """Studio's events on a per-organization server: the organization's ClickHouse views, read as
    `<db>_read` (SELECT on the views only; content already masked at the boundary)."""

    COLUMNS = "event_id, ts, name, session_id, position, org_id, source, harness_name, payload"

    def __init__(self, cfg, orgs, timeout=60):
        ch = cfg["clickhouse"]
        self.url, self.orgs, self.timeout = ch["url"].rstrip("/"), dict(orgs), timeout
        self.dir = os.path.dirname(os.path.expanduser(ch["password_file"]))

    def _auth(self, db):
        import base64
        with open(os.path.join(self.dir, f"{db}_read.password")) as fh:
            pw = fh.read().strip()
        return "Basic " + base64.b64encode(f"{db}_read:{pw}".encode()).decode()

    def events(self, org, since_iso=None, names=None, after_iso=None):
        """Rows of `<db>.v_event FINAL` as dicts, oldest first. Values travel as query
        parameters, never spliced into SQL; only the database name, from server.yaml, is."""
        import urllib.parse
        import urllib.request
        db = self.orgs.get(org)
        if not db:
            raise KeyError(f"organization {org!r} is not served here")
        where, params = [], {}
        if since_iso:
            where.append("ts >= parseDateTime64BestEffort({since:String}, 3)")
            params["param_since"] = since_iso
        if after_iso:
            where.append("ts > parseDateTime64BestEffort({after:String}, 3)")
            params["param_after"] = after_iso
        if names:
            where.append("name IN {names:Array(String)}")
            params["param_names"] = "[" + ",".join("'" + n.replace("\\", "\\\\").replace("'", "\\'") + "'"
                                                   for n in names) + "]"
        q = (f"SELECT {self.COLUMNS} FROM {db}.v_event FINAL" + (" WHERE " + " AND ".join(where) if where else "")
             + " ORDER BY ts FORMAT JSONEachRow")
        req = urllib.request.Request(f"{self.url}/?" + urllib.parse.urlencode(dict(params, query=q)),
                                     headers={"Authorization": self._auth(db)})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            for line in r:
                if line.strip():
                    yield json.loads(line)


def stamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def log_line(msg, out=None):
    """One line of server.log, with its UTC time (a hang could not be dated from the log, msg 01a1d63f-d434)."""
    print(f"{stamp()} {msg}", file=out or sys.stderr, flush=True)


CLIENT_GONE = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError)


class Server(ThreadingHTTPServer):
    """A deeper accept queue than socketserver's 5, so a burst of shipper batches waits instead
    of being refused; daemon threads, but server_close() still waits for requests in flight."""
    request_queue_size = 128
    daemon_threads = True
    block_on_close = True
    gone, gone_since, gone_said = 0, None, 0.0

    def process_request(self, request, client_address):
        """Note when each connection was accepted, so server.health measures the answer from the accept, queueing
        included (docs/health.md §2)."""
        try:
            self.accepted[request.fileno()] = time.monotonic()
        except (AttributeError, OSError):
            pass
        return super().process_request(request, client_address)

    @property
    def accepted(self):
        if not hasattr(self, "_accepted"):
            self._accepted = {}
        return self._accepted

    def handle_error(self, request, client_address):
        """A reply to a client that had already gone (it timed out, slept, lost its network) is counted and said once
        a minute; any other error is logged with its time and traceback."""
        e = sys.exc_info()[1]
        if isinstance(e, CLIENT_GONE):
            Server.gone += 1
            Server.gone_since = Server.gone_since or stamp()
            if time.time() - Server.gone_said >= 60:
                log_line(f"{Server.gone} replies to clients that had gone (broken pipe, reset, timeout) since "
                         f"{Server.gone_since}; the last: {type(e).__name__}")
                Server.gone, Server.gone_since, Server.gone_said = 0, None, time.time()
            return
        log_line(f"error in a request from {client_address[0] if client_address else '?'}:")
        traceback.print_exc()


class BadCodes:
    """At most `limit` bad invite codes per client address in `window` seconds."""

    def __init__(self, limit=10, window=600):
        self.limit, self.window, self.seen, self.lock = limit, window, {}, threading.Lock()

    def add(self, ip):
        with self.lock:
            self.seen.setdefault(ip, []).append(time.time())

    def blocked(self, ip):
        with self.lock:
            now = time.time()
            recent = [t for t in self.seen.get(ip, []) if now - t < self.window]
            if recent:
                self.seen[ip] = recent
            else:
                self.seen.pop(ip, None)  # an empty key is forgotten, so the map holds only what still counts
            if len(self.seen) > 10000:   # a backstop: drop keys whose every entry has aged out
                for k in [k for k, v in self.seen.items() if not v or now - v[-1] >= self.window]:
                    del self.seen[k]
            return len(recent) >= self.limit


class App:
    def __init__(self, cfg=None, ch=None, router_factory=None, ship_cfg=None, grants_loader=None, grant_key=None,
                 mask_key=None, writer_process=False, config_path=None):
        import of_plugins
        import of_ship
        self.cfg = cfg or load_config()
        self.store = Store(self.cfg["state_dir"])
        ship_cfg = ship_cfg or of_ship.config()
        self.ship_cfg = dict(ship_cfg, **route_settings(self.cfg, ship_cfg))
        if self.cfg.get("clickhouse"):
            self.ship_cfg.update(self.cfg["clickhouse"])
        self.masker = of_mask.Masker(mask_key or secret_key(self.cfg), of_mask.detector())
        # A per-organization server: server.yaml `orgs: {name: database}`.
        self.served = set(self.cfg["orgs"]) if self.cfg.get("orgs") else None
        self.bad_codes = BadCodes()
        self._grants, self._grants_at = None, 0
        if self.cfg["terms_current"] not in self.cfg["terms"]:
            raise SystemExit(f"of-server: terms_current {self.cfg['terms_current']!r} is not in terms")
        for v, t in self.cfg["terms"].items():
            if t.get("effect_on_shipped", "remain") != "remain":
                raise SystemExit(f"of-server: terms {v}: effect_on_shipped {t.get('effect_on_shipped')!r} "
                                 "is not carried out yet (only 'remain')")
        plugins = of_plugins.load(self.cfg, self.ship_cfg) if ch is None else \
            dict(of_plugins.load(dict(self.cfg, plugins={"storage": "sqlite"}), self.ship_cfg), storage=ch)
        self.plugins = plugins["chosen"]
        PLUGINS.clear()
        PLUGINS.update(self.plugins)
        self.storage, self.buffer, self.blobs, self.identity = (plugins[k] for k in ("storage", "buffer", "blobs", "identity"))
        if router_factory is None and self.served is not None:
            orgs, unassigned = dict(self.cfg["orgs"]), self.ship_cfg["unassigned_database"]
            router_factory = lambda: of_route.StaticRouter(orgs, unassigned)  # noqa: E731
        self._router_factory = router_factory or (lambda: of_route.Router(self.ship_cfg))
        self._router, self._router_at = None, 0
        self.writer = ExternalWriter(self.cfg, config_path) if writer_process else \
            Writer(self.store, self.buffer, self.router, self.storage, self.cfg, self.ship_cfg, self.masker)
        import of_server_relay as of_relay
        self.relay = of_relay.Relay(self)
        # Studio reads ClickHouse views on a per-organization server; locally it reads the spool.
        self.studio_source = StudioData(self.cfg, self.cfg["orgs"]) \
            if self.served is not None and self.cfg.get("clickhouse") else None
        import of_grants
        self._grants, self._grants_at = None, 0
        self._grants_loader = grants_loader or (lambda: load_grants_files(self.cfg["grants_files"])
                                                if self.cfg.get("grants_files") else of_grants.load())
        self._grant_key = grant_key or (lambda: grant_keys(self.cfg))
        import of_server_passkey  # docs/remote.md §6: a diff approved with a passkey
        self.passkeys = of_server_passkey.Passkeys(self.store, self.cfg, self._grant_key)
        self.passkey_rate = BadCodes(limit=30, window=600)  # the unauthenticated passkey routes, per token
        import of_server_health  # docs/health.md §2: server.health and relay.oldest, kept and served
        self.health_checks = of_server_health.Health(self.store)

    def router(self):
        if self._router is None or time.time() - self._router_at > 30:  # pick up registry changes
            self._router, self._router_at = self._router_factory(), time.time()
        return self._router

    def verify_grant(self, grant_id, subject, action=None):
        import of_grants
        if self._grants is None or time.time() - self._grants_at > 30:  # pick up grants.yaml changes
            self._grants, self._grants_at = self._grants_loader(), time.time()
        key = self._grant_key()
        return of_grants.verify_request(self._grants, key, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                        grant_id, subject, action)

    def serve(self, host=None, port=None):
        httpd = Server((host or self.cfg["host"], self.cfg["port"] if port is None else port), make_handler(self))
        self.writer.start()
        threading.Thread(target=self._housekeeping, daemon=True, name="of-server-housekeeping").start()
        threading.Thread(target=self.health_checks.run, args=(self.writer.stop,), daemon=True,
                         name="of-server-health").start()
        return httpd

    def _housekeeping(self, every=600):
        """Expire relay messages nobody received in time."""
        while not self.writer.stop.wait(every):
            try:
                self.relay.expire()
            except Exception as e:  # keep the server alive; the writer's health shows real failures
                self.writer.last_error = self.writer.last_error or f"relay expiry: {type(e).__name__}: {e}"


WATCH_S = 2.0  # how often the server looks for a changed module under bin/


def run(log=None, config=None):
    import faulthandler
    import signal
    log = log or (lambda m: log_line(m, sys.stdout))
    # `kill -USR1 <pid>` (the watchdog does it before a restart) writes every thread's stack to server.log, so a hang
    # names where it waits (of_conductor, msg 01a1d63f-d434: a hang that left no trace).
    faulthandler.register(signal.SIGUSR1, file=sys.stderr, all_threads=True)
    app = App(load_config(config) if config else None, writer_process=True, config_path=config)
    httpd = app.serve()
    log(f"of-server {VERSION} listening on {httpd.server_address[0]}:{httpd.server_address[1]}, "
        f"state {app.cfg['state_dir']}")
    # SIGTERM (launchd, Kubernetes): stop accepting, let requests in flight answer, then exit.
    signal.signal(signal.SIGTERM, lambda *a: threading.Thread(target=httpd.shutdown, daemon=True).start())
    pid_file = os.path.join(app.cfg["state_dir"], "server.pid")
    try:
        with open(pid_file, "w") as fh:
            fh.write(f"{os.getpid()}\n")
    except OSError:
        pid_file = None
    restart = threading.Event()

    def watch():
        # A deploy of bin/ restarts the whole server, writer included: one version of every module.
        cw = CodeWatch()
        while not restart.is_set():
            time.sleep(WATCH_S)
            changed = cw.changed()
            if changed:
                log(f"of-server: code changed ({', '.join(sorted(os.path.basename(f) for f in changed))}); restarting")
                restart.set()
                httpd.shutdown()

    threading.Thread(target=watch, daemon=True, name="code-watch").start()
    try:
        httpd.serve_forever()
    finally:
        restart_now = restart.is_set()
        restart.set()
        app.writer.terminate()
        httpd.server_close()
        if pid_file and not restart_now:
            try:
                if _read_pid(pid_file) == os.getpid():
                    os.remove(pid_file)
            except OSError:
                pass
    if restart_now:
        # Same pid, same arguments and environment: launchd, systemd and `of server start` all keep it.
        sys.stdout.flush()
        sys.stderr.flush()
        os.execv(sys.executable, [sys.executable] + sys.argv)


# --------------------------------------------------- start / stop without a service manager

def _read_pid(path):
    try:
        with open(path) as fh:
            return int(fh.read().strip() or 0) or None
    except (OSError, ValueError):
        return None


def _alive(pid):
    try:
        if os.waitpid(pid, os.WNOHANG)[0] == pid:
            return False  # our own child that exited (a zombie answers kill 0)
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _health(cfg, timeout=1.0):
    import urllib.request
    host = cfg["host"] if cfg["host"] not in ("0.0.0.0", "::", "") else "127.0.0.1"
    try:
        with urllib.request.urlopen(f"http://{host}:{cfg['port']}/v1/health", timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except Exception:
        return None


LAUNCHD_LABEL = "dev.organic-framework.server"


def launchd_job():
    """The launchd agent's plist when of-server is installed under launchd on this Mac, else None."""
    if sys.platform != "darwin":
        return None
    path = os.path.expanduser(f"~/Library/LaunchAgents/{LAUNCHD_LABEL}.plist")
    return path if os.path.exists(path) else None


def start_daemon(config=None, log_path=None, wait=15.0):
    """`of server start`: run of-server in the background. Where the launchd agent is installed
    (this Mac) it is kicked there; otherwise (a VM, a container shell) it runs detached. Detached in its own session, OF_AUTOMATION=1
    and no SSH agent, output appended to `log_path`, pid in `<state_dir>/server.pid` (written by
    `run`). Returns (pid, message); pid None when it did not come up."""
    cfg = load_config(config) if config else load_config()
    pid_file = os.path.join(cfg["state_dir"], "server.pid")
    if _health(cfg):
        pid = _read_pid(pid_file)
        return pid, f"of-server already answers on {cfg['host']}:{cfg['port']}" + (f" (pid {pid})" if pid else "")
    if launchd_job() and not config:
        # Installed under launchd: start it there, so it keeps its log and comes back after a crash or
        # a reboot (a detached copy would not). `stop` exits cleanly, which launchd does not restart.
        subprocess.run(["launchctl", "kickstart", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"], check=False)
        deadline = time.time() + wait
        while time.time() < deadline:
            if _health(cfg, timeout=0.5):
                pid = _read_pid(pid_file)
                return pid, f"of-server started under launchd ({LAUNCHD_LABEL}): pid {pid}, {cfg['host']}:{cfg['port']}"
            time.sleep(0.3)
        return None, f"launchd started {LAUNCHD_LABEL} but it does not answer yet; see `launchctl print gui/{os.getuid()}/{LAUNCHD_LABEL}`"
    log_path = os.path.expanduser(log_path or os.path.join(of_events.STATE_DIR, "server.log"))
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    env = {**os.environ, "OF_AUTOMATION": "1", "SSH_AUTH_SOCK": ""}
    cmd = [sys.executable, os.path.abspath(__file__)] + (["--config", config] if config else []) + ["run"]
    with open(log_path, "ab") as log, open(os.devnull, "rb") as null:
        p = subprocess.Popen(cmd, stdin=null, stdout=log, stderr=subprocess.STDOUT, env=env,
                             start_new_session=True, close_fds=True)
    deadline = time.time() + wait
    while time.time() < deadline:
        if p.poll() is not None:
            return None, f"of-server exited at once (code {p.returncode}); see {log_path}"
        if _health(cfg, timeout=0.5):
            return p.pid, f"of-server started: pid {p.pid}, {cfg['host']}:{cfg['port']}, log {log_path}"
        time.sleep(0.3)
    return p.pid, f"of-server started (pid {p.pid}) but does not answer yet on {cfg['host']}:{cfg['port']}; see {log_path}"


def stop_daemon(config=None, wait=30.0):
    """`of server stop`: SIGTERM to the pid in `<state_dir>/server.pid`; the server finishes the
    requests in flight and stops its writer; the queue stays on disk. A server under launchd or
    systemd with restart-on-exit comes back: stop it through that manager instead."""
    import signal
    cfg = load_config(config) if config else load_config()
    pid_file = os.path.join(cfg["state_dir"], "server.pid")
    pid = _read_pid(pid_file)
    if not pid or not _alive(pid):
        try:
            os.remove(pid_file)
        except OSError:
            pass
        return False, "of-server is not running here (no live pid in " + pid_file + ")"
    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + wait
    while time.time() < deadline:
        if not _alive(pid):
            return True, f"of-server stopped (pid {pid})"
        time.sleep(0.3)
    return False, f"of-server (pid {pid}) still running after {int(wait)} s; it may be draining a request"


# ---------------------------------------------------------------- launchd and the watchdog (this Mac)

WATCHDOG_LABEL = "dev.organic-framework.server-watchdog"
# One check a minute; a server that has not answered /v1/health for 3 minutes is restarted, at most once in 15.
WATCHDOG = {"every": 60, "probe_timeout": 20, "failing_for": 180, "restart_gap": 900}


def _plist(label, argv, log, extra=""):
    from xml.sax.saxutils import escape
    args = "".join(f"<string>{escape(a)}</string>" for a in argv)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>{args}</array>
  <key>EnvironmentVariables</key><dict><key>OF_AUTOMATION</key><string>1</string><key>SSH_AUTH_SOCK</key><string></string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin</string></dict>
  <key>ProcessType</key><string>Standard</string>
{extra}  <key>StandardOutPath</key><string>{escape(log)}</string>
  <key>StandardErrorPath</key><string>{escape(log)}</string>
</dict></plist>
"""


def launchd_plists(cfg=None, python=None, of_bin=None):
    """{label: plist text} for of-server and its watchdog on this Mac. Standard, not Background: agents wait on the
    server's answers, and a Background job with Nice 10 and low-priority I/O took 13 s for /v1/health and timed out
    after a wake, while the Mac swapped (of_conductor, msg 01a1d63f-d434). The writer child lowers itself instead."""
    from xml.sax.saxutils import escape
    cfg = cfg or load_config()
    python = python or sys.executable
    of_bin = of_bin or os.path.join(os.path.dirname(os.path.abspath(__file__)), "of")
    log = os.path.join(cfg["state_dir"], "server.log")
    server = _plist(LAUNCHD_LABEL, [python, of_bin, "server", "run"], log,
                    "  <key>RunAtLoad</key><true/>\n  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>\n"
                    "  <key>ThrottleInterval</key><integer>10</integer>\n"
                    "  <key>SoftResourceLimits</key><dict><key>NumberOfFiles</key><integer>4096</integer></dict>\n"
                    f"  <key>WorkingDirectory</key><string>{escape(cfg['state_dir'])}</string>\n")
    watch = _plist(WATCHDOG_LABEL, [python, of_bin, "server", "watchdog"], os.path.join(cfg["state_dir"], "watchdog.log"),
                   f"  <key>StartInterval</key><integer>{WATCHDOG['every']}</integer>\n")
    return {LAUNCHD_LABEL: server, WATCHDOG_LABEL: watch}


def install_launchd(cfg=None, uninstall=False, run=subprocess.run):
    """`of server install|uninstall`: write (or remove) the two launchd agents and load them. Installing restarts a
    running server under the new unit; agents already as they should be are left running."""
    if sys.platform != "darwin":
        raise SystemExit("of server install: launchd only (a Linux host runs of-server under systemd or Kubernetes)")
    cfg = cfg or load_config()
    os.makedirs(cfg["state_dir"], exist_ok=True)
    return load_launchd(launchd_plists(cfg), uninstall=uninstall, run=run)


def load_launchd(plists, uninstall=False, run=subprocess.run):
    """Write (or remove) the launchd agents {label: plist text} and load them; an agent whose file is unchanged and
    that is loaded is left running. Shared by of-server's and the relay's install."""
    domain = f"gui/{os.getuid()}"
    out = []
    for label, text in plists.items():
        path = os.path.expanduser(f"~/Library/LaunchAgents/{label}.plist")
        try:
            with open(path) as fh:
                same = fh.read() == text
        except OSError:
            same = False
        if same and not uninstall and run(["launchctl", "print", f"{domain}/{label}"],
                                          capture_output=True).returncode == 0:
            out.append(f"{label}: unchanged")  # `of init` run again does not restart a server it already set up
            continue
        run(["launchctl", "bootout", f"{domain}/{label}"], capture_output=True)
        if uninstall:
            if os.path.exists(path):
                os.remove(path)
            out.append(f"removed {label}")
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w") as fh:
            fh.write(text)
        os.replace(path + ".tmp", path)
        r = run(["launchctl", "bootstrap", domain, path], capture_output=True)
        out.append(f"{label}: {'loaded' if r.returncode == 0 else 'not loaded (launchctl ' + str(r.returncode) + ')'}")
    return "; ".join(out)


def watchdog(cfg=None, now=None, probe=None, restart=None, dump=None, state_path=None, log=None, settings=None):
    """One check of of-server, run by launchd every minute (`of server watchdog`); see watch(). The probe is
    /v1/health within `probe_timeout` (of_conductor, msg 01a1d63f-d434: a hang after a wake needed a person)."""
    cfg = cfg or load_config()
    s = dict(WATCHDOG, **(settings or {}))
    return watch("of-server", LAUNCHD_LABEL,
                 probe or (lambda: _health(cfg, timeout=s["probe_timeout"]) is not None),
                 lambda: _read_pid(os.path.join(cfg["state_dir"], "server.pid")),
                 state_path or os.path.join(cfg["state_dir"], "watchdog.json"), now=now, restart=restart, dump=dump,
                 log=log, settings=settings, installed=None if restart else launchd_job, event="server_watchdog",
                 silent="has not answered /v1/health")


HOST_WATCHDOG_DIR = "/var/db/of-host-watchdog"  # root's alone (deploy/host/setup.py makes it 700 root:wheel)
_USER = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,31}")


def host_watchdog(cfg, relays, now=None, restart=None, dump=None, log=None, domain="system", state_dir=None,
                  pid_of=None, uid_of=None, code=(), service_user="_ofserver", owner_ok=None):
    """The agent host's one root watchdog (deploy/host/setup.py): of-server, and each organization user's relay, each
    a LaunchDaemon in `domain`. RELAYS: [(user, label, that user's OF state folder)]. Returns {name: result}.

    Root runs it, so it acts through no path anyone else controls (of_secrets-review and of_review on
    organic-framework PR 144): its state is in STATE_DIR, a folder of root's alone; every pid is launchd's; each
    heartbeat is read without following a link or blocking, as a regular file of that user's; one check that fails
    never stops the next. It records no event: root's own store is read by nobody, and its log is the record.

    CODE: the programs it and the daemons it restarts run (the python, ClickHouse, the release's bin/). Each run
    looks at them again, cheaply (`_code_unsafe`: owners and modes, no walk; deploy/host/setup.py's check is the whole
    one): while one is unsafe it restarts nothing and says so in its log (of_secrets-review on PR 144)."""
    import pwd
    import of_relay_client
    state_dir = _private_dir(state_dir or HOST_WATCHDOG_DIR)
    pid_of = pid_of or (lambda label: of_relay_client.launchd_pid(label, domain))
    uid_of = uid_of or (lambda user: pwd.getpwnam(user).pw_uid)
    log = log or (lambda m: log_line(m, sys.stdout))
    unsafe = _code_unsafe(code, [u for u, _, _ in relays] + [service_user], owner_ok) if code else []
    if unsafe:
        log("restarting nothing: the code root and the daemons run changed since deploy/host/setup.py checked it; "
            "run its `plan` again: " + "; ".join(unsafe[:5]))
        return {"code": "unsafe"}
    checks = [("of-server", None, "of-server", LAUNCHD_LABEL, "watchdog.json", "server_watchdog",
               "has not answered /v1/health", lambda: _health(cfg, timeout=WATCHDOG["probe_timeout"]) is not None)]
    for user, label, state in relays:
        hb = os.path.join(state, of_relay_client.HEARTBEAT)
        checks.append((f"relay {user}", user, f"{user}'s relay", label, f"watchdog-relay-{user}.json",
                       "relay_watchdog", "has not moved its loops",
                       lambda hb=hb, user=user: of_relay_client.relay_healthy(now, path=hb, uid=uid_of(user))[0]))
    out = {}
    for key, user, name, label, state_file, event, silent, probe in checks:
        try:
            if user is not None and not _USER.fullmatch(user):
                raise ValueError(f"{user!r} is no user name")
            out[key] = watch(name, label, probe, lambda label=label: pid_of(label), os.path.join(state_dir, state_file),
                             now=now, restart=restart, dump=dump, log=log, event=event, silent=silent, domain=domain,
                             record=False)
        except Exception as e:  # noqa: BLE001 - one relay's failure must not stop the others' or the server's check
            out[key] = "error"
            log(f"{name}: the check failed: {type(e).__name__}: {e}"[:300])
    return out


def _code_unsafe(paths, untrusted, owner_ok=None):
    """What is unsafe now about PATHS and every folder above each (its real path's, and its own): owned by someone
    other than root or an admin who is none of UNTRUSTED, writable by others, or by a group one of them is in. ACLs
    and the trees below are setup.py's whole check."""
    import grp
    import pwd
    import stat
    users = []
    for u in untrusted:
        try:
            users.append(pwd.getpwnam(u))
        except KeyError:
            pass
    groups = {g for pw in users for g in os.getgrouplist(pw.pw_name, pw.pw_gid)}
    names = {pw.pw_name for pw in users} | set(untrusted)
    if owner_ok is None:
        try:
            admin = grp.getgrnam("admin").gr_gid
        except KeyError:
            admin = None

        def owner_ok(uid):
            if uid == 0:
                return True
            try:
                pw = pwd.getpwuid(uid)
            except KeyError:
                return False
            return admin is not None and pw.pw_name not in names and admin in os.getgrouplist(pw.pw_name, pw.pw_gid)
    seen, out = set(), []
    for p in paths:
        for q in (os.path.realpath(p), os.path.abspath(p)):
            while q not in seen:
                seen.add(q)
                try:
                    st = os.lstat(q)
                except OSError as e:
                    out.append(f"{q}: {e.strerror}")
                    break
                if not stat.S_ISLNK(st.st_mode):
                    if not owner_ok(st.st_uid):
                        out.append(f"{q}: owned by uid {st.st_uid}")
                    if st.st_mode & 0o002 or (st.st_mode & 0o020 and st.st_gid in groups):
                        out.append(f"{q}: writable by {'others' if st.st_mode & 0o002 else 'gid ' + str(st.st_gid)}")
                if q == "/":
                    break
                q = os.path.dirname(q)
    return out


def _private_dir(path):
    """PATH as a folder only this user writes: made 700 when absent; refused when it is a link, someone else's, or
    writable by its group or others."""
    import stat
    os.makedirs(path, mode=0o700, exist_ok=True)
    st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.geteuid() or st.st_mode & 0o022:
        raise SystemExit(f"{path}: not a folder of this user's alone; the host watchdog keeps no state there")
    return path


def watch(name, label, probe, pid_of, state_path, now=None, restart=None, dump=None, log=None, settings=None,
          installed=None, event="server_watchdog", silent="has not answered", domain=None, record=True):
    """One watchdog check of a launchd-run process NAME (label LABEL). A process that is alive but fails PROBE
    through `failing_for` seconds of checks is restarted, at most once in `restart_gap`; first `kill -USR1` writes its
    threads' stacks to its log, so the next hang names its cause. A process with no live pid is left alone: a stop
    meant it, and launchd's KeepAlive restarts a crash. A gap between checks longer than twice `every` (a sleep, or
    the watchdog not running) starts the count again; the restart's time is kept before launchctl is called (of_review
    on organic-framework PR 112). Returns ok, failing, waiting, stopped, restarted, restart_failed or
    not-under-launchd. `installed` tells whether the launchd job is there (None: assume it is). Its state file is
    read and written without following a link (the host's root watchdog, organic-framework PR 144); `record` False
    writes no event."""
    import signal
    s = dict(WATCHDOG, **(settings or {}))
    now = time.time() if now is None else now
    log = log or (lambda m: log_line(m, sys.stdout))
    try:
        with os.fdopen(os.open(state_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)) as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        st = {}
    st = st if isinstance(st, dict) else {}
    gap = None if st.get("last_check") is None else now - st["last_check"]
    st["last_check"] = now

    def save(result):
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        tmp = state_path + ".tmp"
        try:
            os.unlink(tmp)  # whatever is there, a link included, is replaced, never written through
        except FileNotFoundError:
            pass
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "w") as fh:
            json.dump(st, fh)
        os.replace(tmp, state_path)
        return result

    if probe():
        if st.get("failing_since") is not None:
            log(f"{name} answers again after {int(now - st['failing_since'])} s"
                + (" and a restart" if st.get("last_restart") is not None
                   and st["last_restart"] >= st["failing_since"] else ""))
        st = {"last_ok": now, "last_restart": st.get("last_restart"), "last_check": now}
        return save("ok")
    pid = pid_of()
    if not (pid and _alive(pid)):
        st.pop("failing_since", None)
        st.pop("fails", None)
        return save("stopped")  # stopped on purpose, or crashed and left to launchd's KeepAlive
    if st.get("failing_since") is None or gap is None or gap > 2 * s["every"]:
        st["failing_since"], st["fails"] = now, 0  # a sleep or a pause between checks is not silence seen
    st["fails"] = st.get("fails", 0) + 1
    failing = now - st["failing_since"]
    if failing < s["failing_for"]:
        return save("failing")
    if st.get("last_restart") is not None and now - st["last_restart"] < s["restart_gap"]:
        return save("waiting")
    if installed is not None and not installed():
        log(f"{name} {silent} for {int(failing)} s; it is not under launchd here, so nothing restarts it")
        return save("not-under-launchd")
    try:
        load = round(os.getloadavg()[0], 1)
    except OSError:
        load = None
    dumped = False
    try:
        (dump or (lambda p: (os.kill(p, signal.SIGUSR1), time.sleep(2))))(pid)
        dumped = True
    except OSError:
        pass
    st["last_restart"] = now  # saved before the call: a launchctl that hangs or fails still waits restart_gap
    save("restarting")
    log(f"{name} (pid {pid}) {silent} for {int(failing)} s ({st['fails']} checks; load {load})"
        + ("; its threads' stacks are in its log" if dumped else "") + "; restarting it")
    error = None
    try:
        target = f"{domain or 'gui/' + str(os.getuid())}/{label}"  # system/<label> for a LaunchDaemon (the host)
        r = (restart or (lambda: subprocess.run(["launchctl", "kickstart", "-k", target], capture_output=True,
                                                timeout=30)))()
        if getattr(r, "returncode", 0) not in (0, None):  # `Could not find service` (113) after a bootout
            error = f"launchctl exit {r.returncode}"
    except Exception as e:  # noqa: BLE001 - a timeout under the very load this is for; the next one waits restart_gap
        error = f"{type(e).__name__}: {e}"[:200]
    if error:
        log(f"the restart did not complete ({error}); the next attempt waits {s['restart_gap']} s")
    try:
        if record:
            of_events.emit(event, {"action": "restarted" if error is None else "restart_failed", "pid": pid,
                                   "failing_s": int(failing), "checks": st["fails"], "load1": load,
                                   **({"error": error} if error else {})})
    except Exception:  # noqa: BLE001 - the restart matters more than its record
        pass
    return save("restarted" if error is None else "restart_failed")


if __name__ == "__main__":  # the container entry point: python3 of_server.py --config /etc/of/server.yaml [run]
    import argparse
    ap = argparse.ArgumentParser(description="of-server")
    ap.add_argument("--config", default=os.environ.get("OF_SERVER_CONFIG"))
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run")
    sub.add_parser("writer", help="the writer loop alone (started by `run` as a child process)")
    en = sub.add_parser("enroll", help="enroll a machine here (until remote enrollment exists)")
    en.add_argument("--machine-id", required=True)
    en.add_argument("--pubkey", required=True, help="the machine's OpenSSH public key line")
    en.add_argument("--org", action="append", default=[], required=True)
    en.add_argument("--raw-org", action="append", default=[], help="an organization whose raw tier the person consented to")
    en.add_argument("--person", help="the colleague's id in the organization's registry; absent: the tenant")
    en.add_argument("--tenant", action="store_true", help="the tenant's own machine (may invite and revoke)")
    rv = sub.add_parser("revoke", help="revoke one machine, or every machine of a person")
    g = rv.add_mutually_exclusive_group(required=True)
    g.add_argument("--machine-id")
    g.add_argument("--person")
    rq = sub.add_parser("relay-requeue", help="offer dead relay messages again (after fixing the receiver)")
    rq.add_argument("--msg-id", action="append", help="only these messages (default: all dead)")
    rq.add_argument("--state", choices=("dead", "expired"), default="dead",
                    help="requeue messages in this state (expired: undo a relay-drop)")
    rd = sub.add_parser("relay-drop", help="drop queued relay messages nobody can take (expired, reason dropped)")
    rd.add_argument("--msg-id", action="append", required=True)
    rd.add_argument("--reason", required=True)
    hw = sub.add_parser("host-watchdog", help="one check of the agent host's server and relays (root, every minute)")
    hw.add_argument("--domain", default="system")
    hw.add_argument("--relay", action="append", default=[], help="USER:LABEL:STATE_DIR (repeat)")
    hw.add_argument("--state-dir", default=HOST_WATCHDOG_DIR, help="the watchdog's own state, a folder of root's alone")
    hw.add_argument("--code", action="append", default=[], help="a program it and its daemons run, looked at each run")
    hw.add_argument("--service-user", default="_ofserver")
    rp = sub.add_parser("replay-dead", help="queue dead batches again (after fixing the cause)")
    rp.add_argument("--batch-id", action="append", help="only these batches (default: all dead)")
    iv = sub.add_parser("invite", help="a one-time enrollment code (printed once)")
    iv.add_argument("--org", action="append", required=True)
    iv.add_argument("--raw-allowed", action="append", default=[])
    iv.add_argument("--person", help="the colleague's id in the organization's registry; absent: the tenant")
    iv.add_argument("--ttl", type=int, help="seconds (default 72 h, at most 7 days)")
    iv.add_argument("--note")
    a = ap.parse_args()
    if a.cmd in (None, "run"):
        run(config=a.config)
    elif a.cmd == "host-watchdog":
        host_watchdog(load_config(a.config), [tuple(r.split(":", 2)) for r in a.relay], domain=a.domain,
                      state_dir=a.state_dir, code=a.code, service_user=a.service_user)
    elif a.cmd == "writer":
        import fcntl
        import signal
        parent = os.getppid()
        try:
            os.nice(10)  # the drain to storage can wait; the requests agents wait on cannot (msg 01a1d63f-d434)
        except OSError:
            pass
        wapp = App(load_config(a.config) if a.config else None)
        signal.signal(signal.SIGTERM, lambda *x: wapp.writer.stop.set())
        # One writer per queue: a writer left over from a killed server must finish (it exits once
        # it sees its parent gone) before this one starts.
        lock = open(os.path.join(wapp.cfg["state_dir"], "writer.lock"), "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(f"of-server writer pid {os.getpid()}: another writer holds the queue; waiting", flush=True)
            fcntl.flock(lock, fcntl.LOCK_EX)
        if os.getppid() != parent:
            raise SystemExit(0)  # our server died while we waited
        wapp.writer.parent_pid = parent
        print(f"of-server writer {VERSION} pid {os.getpid()}", flush=True)
        wapp.writer.run()
    else:
        c = load_config(a.config)
        st = Store(c["state_dir"])
        if a.cmd == "enroll":
            bad = sorted(set(a.org) - set(c["orgs"])) if c.get("orgs") else []
            if bad or set(a.raw_org) - set(a.org):
                raise SystemExit(f"not served here: {bad}" if bad else "--raw-org must also be an --org")
            enroll(st, a.machine_id, a.pubkey, a.org, a.raw_org, tenant=a.tenant, person=a.person)
            print(f"enrolled {a.machine_id}: orgs {sorted(a.org)}, raw {sorted(a.raw_org)}")
        elif a.cmd == "relay-requeue":
            import of_server_relay

            class _A:  # the relay needs only the store here
                store = st
            print(f"queued again: {of_server_relay.Relay(_A).requeue(a.msg_id, a.state) or 'nothing'}")
        elif a.cmd == "relay-drop":
            # the full app (not started): the drop's events go to the disk queue the running server drains
            dropped = App(c, config_path=a.config).relay.drop(a.msg_id, a.reason)
            print(f"dropped: {dropped or 'nothing (no such queued message)'}")
        elif a.cmd == "replay-dead":
            import of_plugins
            done = replay_dead(st, of_plugins.DiskBuffer(c), a.batch_id)
            print(f"queued again: {done or 'nothing'} (the running server's writers pick them up)")
        elif a.cmd == "invite":
            inv = create_invite(st, c, a.org, a.raw_allowed, a.person, a.note, a.ttl, created_by="server-host")
            print(f"invite for {inv['orgs']} (raw allowed: {inv['raw_allowed'] or 'none'}, person: {a.person or 'tenant'}),"
                  f" expires {inv['expires_at']}:")
            print(inv["code"])
        else:
            import of_plugins
            buf = of_plugins.DiskBuffer(c)
            if a.person:
                with st.conn() as conn:
                    ids = [r[0] for r in conn.execute("SELECT machine_id FROM machines WHERE person = ? AND"
                                                      " revoked_at IS NULL", (a.person,))]
            else:
                ids = [a.machine_id]
            done = revoke_machines(st, buf, c.get("tenant") or "default", ids, by="server-host")
            print(f"revoked {done or 'nothing'}")
