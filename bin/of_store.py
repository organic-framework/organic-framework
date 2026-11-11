"""The machine's local store: SQLite in WAL mode (docs/storage.md, v1).

Raw tier: content-addressed zstd blobs, transcript records, hook records, runtime
records, snapshots. Typed layer: events (docs/events.md) with full-text search.
Many processes write at once; every write is a short transaction.
"""

import hashlib
import json
import re
import os
import contextlib
import sqlite3
import threading
import time

import of_events

try:
    import compression.zstd as zstd
except ImportError:  # Python < 3.14: store uncompressed, flagged
    zstd = None

SCHEMA_VERSION = 3  # 3: session_fork, transcript_own; 2: message_id and cache writes by TTL (of_capacity)
CHUNK = 150  # lines per write transaction: short locks, so a hook (0.15 s wait) rarely has to spool
CHUNK_BYTES = 1024 * 1024  # content per ingest transaction (the lock is held only for the inserts)
ZLEVEL = 3  # fast enough for hooks; `of compact` may recompress later

DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS blob (
  sha256 TEXT PRIMARY KEY, size INTEGER NOT NULL, codec TEXT NOT NULL, zdata BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS transcript_record (
  id INTEGER PRIMARY KEY, source_path TEXT NOT NULL, line_offset INTEGER NOT NULL,
  blob_sha256 TEXT NOT NULL, session_id TEXT, record_uuid TEXT NOT NULL, parent_uuid TEXT,
  type TEXT, timestamp TEXT, model TEXT, tool_names TEXT, input_tokens INTEGER, output_tokens INTEGER,
  cache_read_tokens INTEGER, cache_write_tokens INTEGER, harness TEXT,
  message_id TEXT, cache_write_5m_tokens INTEGER, cache_write_1h_tokens INTEGER,
  UNIQUE (session_id, record_uuid));
CREATE INDEX IF NOT EXISTS transcript_session_ts ON transcript_record (session_id, timestamp);
CREATE TABLE IF NOT EXISTS hook_record (
  id INTEGER PRIMARY KEY, received_at TEXT NOT NULL, hook_event TEXT, session_id TEXT, blob_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runtime_record (
  id INTEGER PRIMARY KEY, received_at TEXT NOT NULL, runtime TEXT, type TEXT, pane_id TEXT, blob_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshot (
  id INTEGER PRIMARY KEY, path TEXT NOT NULL, taken_at TEXT NOT NULL, blob_sha256 TEXT NOT NULL,
  UNIQUE (path, blob_sha256));
CREATE TABLE IF NOT EXISTS event (
  event_id TEXT PRIMARY KEY, ts TEXT NOT NULL, name TEXT NOT NULL, schema INTEGER,
  host_id TEXT, host_name TEXT, session_id TEXT, position TEXT, org TEXT, source TEXT,
  harness_name TEXT, harness_version TEXT, config_dir TEXT, model TEXT, cwd TEXT,
  workplace TEXT, transcript TEXT, payload TEXT, raw_ref TEXT, record TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS event_name_ts ON event (name, ts);
CREATE INDEX IF NOT EXISTS event_position_ts ON event (position, ts);
CREATE INDEX IF NOT EXISTS event_session_ts ON event (session_id, ts);
CREATE VIRTUAL TABLE IF NOT EXISTS event_fts USING fts5 (event_id UNINDEXED, text);
CREATE TABLE IF NOT EXISTS ingest_cursor (
  source_path TEXT PRIMARY KEY, inode INTEGER, offset INTEGER NOT NULL, updated_at TEXT);
CREATE TABLE IF NOT EXISTS ship_cursor (tbl TEXT PRIMARY KEY, last_rowid INTEGER NOT NULL);
"""

FORK_DDL = """
CREATE TABLE IF NOT EXISTS session_fork (
  session_id TEXT PRIMARY KEY, parent_session_id TEXT NOT NULL, shared INTEGER, method TEXT, detected_at TEXT);
CREATE VIEW IF NOT EXISTS transcript_own AS
  SELECT t.* FROM transcript_record t
  WHERE NOT EXISTS (SELECT 1 FROM session_fork f JOIN transcript_record p
                    ON p.session_id = f.parent_session_id AND p.record_uuid = t.record_uuid
                    WHERE f.session_id = t.session_id);
"""

TEXT_FIELDS = ("text", "last_message", "message", "summary", "error")


def db_path():
    return os.environ.get("OF_DB") or os.path.join(of_events.STATE_DIR, "of.db")


# SQLite never shrinks the WAL file by itself: one large write left a 2.9 GB of.db-wal on 2026-11-08 while only
# 651 frames were in use (reported by acme_org from the founder's disk alert). With a limit, the first write after a
# checkpoint that rewinds the log truncates the file to it.
WAL_LIMIT = 64 * 1024 * 1024


def connect(path=None, timeout=10.0):
    """`timeout` is how long a writer waits for the lock. Hooks pass a short one and spool
    to disk instead of waiting (they must stay within their latency budget)."""
    path = path or db_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    if not os.path.exists(path):
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
    conn = sqlite3.connect(path, timeout=timeout, isolation_level=None)
    conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute(f"PRAGMA journal_size_limit = {WAL_LIMIT}")
    if conn.execute("SELECT name FROM sqlite_master WHERE name = 'meta'").fetchone() is None:
        conn.executescript(DDL)
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    _migrate(conn)  # a new store too: the later tables and the usage columns' flag
    return conn


_MIGRATED = set()


def _migrate(conn):
    """Columns added after a store was created (once per process and path)."""
    key = conn.execute("PRAGMA database_list").fetchone()[2]
    if key in _MIGRATED:
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(transcript_record)")}
    for col, typ in (("message_id", "TEXT"), ("cache_write_5m_tokens", "INTEGER"), ("cache_write_1h_tokens", "INTEGER")):
        if col not in cols:
            try:
                conn.execute(f"ALTER TABLE transcript_record ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass  # another process added it first
    if {"message_id", "cache_write_5m_tokens", "cache_write_1h_tokens"} - {r[1] for r in conn.execute(
            "PRAGMA table_info(transcript_record)")}:
        return  # the store was busy: try again on the next connect; inserts use the old columns meanwhile
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS transcript_message ON transcript_record (session_id, message_id)")
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'transcript_own'").fetchone() is None:
            conn.executescript(FORK_DDL)  # DDL only: no scan, no index build on connect
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    except sqlite3.OperationalError:
        return
    _MIGRATED.add(key)


def has_usage_columns(conn):
    key = conn.execute("PRAGMA database_list").fetchone()[2]
    return key in _MIGRATED


# ------------------------------------------------------------------ blobs

def put_blob(conn, data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    if zstd:
        z, codec = zstd.compress(data, level=ZLEVEL), "zstd"
    else:
        z, codec = data, "raw"
    conn.execute("INSERT OR IGNORE INTO blob VALUES (?, ?, ?, ?)", (sha, len(data), codec, z))
    return sha


def get_blob(conn, sha):
    row = conn.execute("SELECT codec, zdata FROM blob WHERE sha256 = ?", (sha,)).fetchone()
    if not row:
        return None
    return zstd.decompress(row[1]) if row[0] == "zstd" else row[1]


# ----------------------------------------------------------------- events

def _texts(payload, out):
    if isinstance(payload, dict):
        for k, v in payload.items():
            if isinstance(v, str) and (k in TEXT_FIELDS or k == "question"):
                out.append(v)
            elif isinstance(v, (dict, list)):
                _texts(v, out)
    elif isinstance(payload, list):
        for v in payload:
            _texts(v, out)
    return out


_TX = threading.local()  # the connection holding this thread's write transaction, if any


class SelfDeadlock(RuntimeError):
    """A write on a second connection while this thread holds the write lock on another one."""


def _guard(conn):
    held = getattr(_TX, "conn", None)
    if held is not None and held is not conn:
        raise SelfDeadlock("this thread holds the store's write lock on another connection; a write here would "
                           "wait on itself (write on that connection, or after its transaction)")


@contextlib.contextmanager
def write_tx(conn):
    """BEGIN IMMEDIATE ... COMMIT, ROLLBACK on any error, so the write lock is never left held. While it
    is open, a write from this thread through another connection raises SelfDeadlock at once instead
    of waiting out the busy timeout on a lock its own process holds (2026-11-06)."""
    _guard(conn)
    conn.execute("BEGIN IMMEDIATE")
    prev, _TX.conn = getattr(_TX, "conn", None), conn
    t0 = time.monotonic()
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        _TX.conn = prev
        warn_long(t0)


LONG_TX_S = 5.0


def warn_long(t0, what="write transaction"):
    """A write transaction that held the store's lock over LONG_TX_S: one line with its caller in
    hook-errors.log, so the next lock-holder is visible at once (of_architect, 2026-11-06)."""
    took = time.monotonic() - t0
    if took < LONG_TX_S:
        return
    try:
        import traceback
        caller = " <- ".join(f"{os.path.basename(f.filename)}:{f.lineno} {f.name}"
                             for f in traceback.extract_stack()[-6:-2][::-1])
        with open(os.path.join(of_events.STATE_DIR, "hook-errors.log"), "a") as fh:
            fh.write(f"{of_events.now_iso()} store {what} held the write lock {took:.1f} s: {caller}\n")
    except Exception:  # noqa: BLE001
        pass


def add_event(conn, rec, raw_ref=None):
    j = lambda v: None if v is None else json.dumps(v, ensure_ascii=False)
    _guard(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            "INSERT OR IGNORE INTO event VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rec["of.event.id"], rec["timestamp"], rec["event.name"], rec.get("of.schema"),
             rec.get("host.id"), rec.get("host.name"), rec.get("gen_ai.conversation.id"),
             rec.get("gen_ai.agent.name"), rec.get("of.org"), rec.get("of.source"),
             rec.get("of.harness.name"), rec.get("of.harness.version"), rec.get("of.harness.config_dir"),
             rec.get("gen_ai.request.model"), rec.get("of.cwd"), j(rec.get("of.workplace")),
             j(rec.get("of.transcript")), j(rec.get("of.payload")), raw_ref,
             json.dumps(rec, ensure_ascii=False)))
        text = "\n".join(_texts(rec.get("of.payload"), []))
        if text and cur.rowcount:  # a re-delivered event is not indexed twice
            conn.execute("INSERT INTO event_fts VALUES (?, ?)", (rec["of.event.id"], text))
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def add_hook_record(conn, hook_event, session_id, raw):
    sha = put_blob(conn, raw)
    cur = conn.execute("INSERT INTO hook_record (received_at, hook_event, session_id, blob_sha256) VALUES (?,?,?,?)",
                       (of_events.now_iso(), hook_event, session_id, sha))
    return f"hook_record:{cur.lastrowid}"


def add_runtime_record(conn, runtime, typ, pane_id, raw):
    sha = put_blob(conn, raw)
    conn.execute("INSERT INTO runtime_record (received_at, runtime, type, pane_id, blob_sha256) VALUES (?,?,?,?,?)",
                 (of_events.now_iso(), runtime, typ, pane_id, sha))


def add_snapshot(conn, path):
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return False
    sha = put_blob(conn, data)
    cur = conn.execute("INSERT OR IGNORE INTO snapshot (path, taken_at, blob_sha256) VALUES (?,?,?)",
                       (path, of_events.now_iso(), sha))
    return cur.rowcount > 0


# ------------------------------------------------------------ transcripts

def _record_fields(obj):
    msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    usage = msg.get("usage") or {}
    tools = [c.get("name") for c in msg.get("content") or [] if isinstance(c, dict) and c.get("type") == "tool_use"]
    return {
        "session_id": obj.get("sessionId") or obj.get("session_id"),
        "record_uuid": obj.get("uuid") or obj.get("id"),
        "parent_uuid": obj.get("parentUuid"),
        "type": obj.get("type"),
        "timestamp": obj.get("timestamp"),
        "model": msg.get("model"),
        "tool_names": ",".join(t for t in tools if t) or None,
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_tokens": usage.get("cache_read_input_tokens"),
        "cache_write_tokens": usage.get("cache_creation_input_tokens"),
        # one API response is written as one record per content block, all with the same usage: sum by message
        "message_id": msg.get("id"),
        "cache_write_5m_tokens": (usage.get("cache_creation") or {}).get("ephemeral_5m_input_tokens"),
        "cache_write_1h_tokens": (usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens"),
    }


def session_from_path(path):
    """The session id a transcript file stands for: Claude Code `<uuid>.jsonl`; Codex
    `rollout-<date>-<uuid>.jsonl[.zst]` (the last 36 characters of the stem)."""
    stem = os.path.basename(path)
    for ext in (".zst", ".jsonl"):
        if stem.endswith(ext):
            stem = stem[:-len(ext)]
    return stem[-36:] if stem.startswith("rollout-") and len(stem) > 36 else stem


# ------------------------------------------------------------------ forked sessions

def has_uuid_index(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'transcript_uuid'").fetchone() is not None


def _born(conn, sid):
    """When this session's transcript first existed: the earliest birth time of its files, else the
    earliest record timestamp (a file gone since the import)."""
    best = None
    for (path,) in conn.execute("SELECT DISTINCT source_path FROM transcript_record WHERE session_id = ?", (sid,)):
        try:
            st = os.stat(path)
            t = getattr(st, "st_birthtime", None) or st.st_mtime
        except OSError:
            continue
        best = t if best is None else min(best, t)
    if best is not None:
        return best, "birthtime"
    ts = conn.execute("SELECT min(timestamp) FROM transcript_record WHERE session_id = ?", (sid,)).fetchone()[0]
    return (ts or "~"), "first_record"


def detect_forks(conn, sessions=None, log=lambda m: None, in_tx=False):
    """A fork (a side question, a resume with --fork-session) copies its parent's records under its own
    session id. Each pair of sessions sharing records gets one row in session_fork: the session whose
    transcript was born later is the fork, and its parent is the earliest-born session it shares
    records with (the root, never a sibling copy). transcript_own leaves the copies out, so per-session sums
    and of derive count each record once (of_capacity, 2026-11-06). Needs the transcript_uuid index
    (of db forks builds it). Returns the forks found."""
    if not has_uuid_index(conn):
        return []
    if sessions is None:
        pairs = conn.execute(
            "SELECT a.session_id, b.session_id, count(*) FROM transcript_record a JOIN transcript_record b "
            "ON b.record_uuid = a.record_uuid AND b.session_id > a.session_id "
            "WHERE a.type IN ('user', 'assistant') AND a.source_path NOT LIKE '%/subagents/%' "
            "AND b.source_path NOT LIKE '%/subagents/%' GROUP BY 1, 2").fetchall()
    else:
        pairs = []
        for sid in sessions:
            pairs += conn.execute(
                "SELECT ?, b.session_id, count(*) FROM transcript_record a JOIN transcript_record b "
                "ON b.record_uuid = a.record_uuid AND b.session_id != a.session_id "
                "WHERE a.session_id = ? AND a.type IN ('user', 'assistant') "
                "AND b.source_path NOT LIKE '%/subagents/%' GROUP BY 2", (sid, sid)).fetchall()
    partners = {}
    for a, b, n in pairs:
        partners.setdefault(a, {})[b] = n
        partners.setdefault(b, {})[a] = n
    born = {}

    def birth(x):
        if x not in born:
            born[x] = _born(conn, x)
        return born[x]
    found = []
    for s in (sessions if sessions is not None else sorted(partners)):
        others = partners.get(s) or {}
        if not others:
            continue
        group = [s, *others]
        by_file = all(birth(x)[1] == "birthtime" for x in group)  # compare like with like
        key = (lambda x: birth(x)[0]) if by_file else (lambda x: _born_by_record(conn, x)[0])
        earlier = [o for o in others if key(o) < key(s)]
        if not earlier:
            continue  # s is the original (or the earliest of its copies)
        parent = min(earlier, key=key)  # the root, not a sibling copy
        row = (s, parent, others[parent], f"fv-0:{'birthtime' if by_file else 'first_record'}", of_events.now_iso())
        if in_tx:  # inside ingest_file's transaction: the mark lands with the fork's first rows
            conn.execute("INSERT OR REPLACE INTO session_fork VALUES (?,?,?,?,?)", row)
        else:
            with write_tx(conn):
                conn.execute("INSERT OR REPLACE INTO session_fork VALUES (?,?,?,?,?)", row)
        found.append((s, parent, others[parent]))
        log(f"fork {s} of {parent}: {others[parent]} shared records ({row[3]})")
    return found


def _born_by_record(conn, sid):
    ts = conn.execute("SELECT min(timestamp) FROM transcript_record WHERE session_id = ?", (sid,)).fetchone()[0]
    return (ts or "~"), "first_record"


# ------------------------------------------------------------------ private keys never stored

# A PEM private key, in plain text or JSON-escaped inside a transcript line (its newlines as \n, even
# escaped twice inside an annotation): from BEGIN to its END, or, when the END is missing, the key
# characters after BEGIN. And the same block base64-encoded (a Kubernetes Secret's data):
# "-----BEGIN" is "LS0tLS1CRUdJT" in base64. The replacement holds no quote or backslash, so the
# line stays valid JSON (acme_org, 2026-11-06: a GitHub App key printed through kubectl).
_PEM_KEY = re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----(?:(?!-----END).){0,20000}?-----END [A-Z0-9 ]*PRIVATE KEY-----"
                      rb"|-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[A-Za-z0-9+/=\\\s]*", re.S)
_B64_PEM = re.compile(rb"LS0tLS1CRUdJT[A-Za-z0-9+/]{16,}={0,2}")
REDACTED = b"[OF-REDACTED private key]"


def redact_keys(line):
    """The line with every private key block (plain, escaped or base64) replaced by REDACTED, and every
    credential in a connection string or a secret-named environment variable (redact_credentials)."""
    if b"PRIVATE KEY" in line or b"LS0tLS1CRUdJT" in line:
        line = _B64_PEM.sub(REDACTED, _PEM_KEY.sub(REDACTED, line))
    return redact_credentials(line)


# A password in a connection string: scheme://user:PASSWORD@host (postgres, mysql, redis, amqp, https …),
# and the value of a secret-named variable in an environment dump (FOO_TOKEN=…, PGPASSWORD=…, DATABASE_DSN=…),
# as printed from /proc/<pid>/environ or env (acme_org, 2026-11-06). The name and the shape stay, only the value
# goes; a reference ($VAR, ${VAR}) and a mask (***) stay as they are. No quote or backslash is inserted.
_DSN_PW = re.compile(rb"(\b[a-z][a-z0-9+.-]{1,20}://[^:/@\s\"'\\]{1,100}:)([^@/\s\"'\\]{1,200})(@)")
_ENV_SECRET = re.compile(rb"(?:(?<![A-Za-z0-9_])|(?<=\\n)|(?<=\\t))((?:[A-Z][A-Z0-9_]*_)?(?:API_?KEY|SECRET(?:_?ACCESS)?(?:_?KEY)?|TOKEN|PASSWORD|"
                         rb"PASSWD|PASS|PGPASSWORD|DSN|CREDENTIALS?|PRIVATE_?KEY|AUTH)(?:_(?:KEY|B64|BASE64|VALUE|JSON|DATA|SECRET))?=)"
                         rb"((?:\\?[\"'])?)([^\s\"'\\$*{][^\s\"'\\]{3,})")
CRED_REDACTED = b"[OF-REDACTED]"


# A Kubernetes Secret printed whole (kubectl -o json|yaml, a describe, or a failed go-template that dumps the
# object as a Go map: acme_org, 2026-11-06, k8s_keeper's subagent). In a line that holds one, every standalone
# base64 token of 16+ characters is a data value and goes, and so do the plain values under stringData. Over-
# masking inside a Secret's line is accepted; outside one nothing changes.
_K8S_SECRET = re.compile(rb"kind\\?\"?\s*[:=]\s*\\?\"?Secret\b|kind:Secret\b")
_B64_VALUE = re.compile(rb"(?<![A-Za-z0-9+/=_.-])[A-Za-z0-9+/]{16,}={0,2}(?![A-Za-z0-9+/=_.-])")
_STRINGDATA = re.compile(rb"stringData")
_PAIR = re.compile(rb"([A-Za-z0-9_.-]{1,80})(\\?\"\s*:\s*\\?\"|:\s+|:)([^\s\"\\,\]}\[{]{4,})")


def _redact_k8s_secret(line):
    if not _K8S_SECRET.search(line):
        return line
    line = _B64_VALUE.sub(CRED_REDACTED, line)
    out, pos = [], 0
    for m in _STRINGDATA.finditer(line):
        if m.start() < pos:
            continue
        start = m.end()
        window = line[start:start + 4000]
        ends = [i for i in (window.find(b"}"), window.find(b"]")) if i >= 0]
        dedent = re.search(rb"\\n(?! )", window[4:])  # a YAML block ends at the next unindented line
        if dedent:
            ends.append(dedent.start() + 4)
        stop = start + (min(ends) if ends else len(window))
        out += [line[pos:start], _PAIR.sub(lambda p: p.group(1) + p.group(2) + CRED_REDACTED, line[start:stop])]
        pos = stop
    return b"".join(out) + line[pos:] if out else line


# An RPC provider's key in the URL path (acme_org, 2026-11-06: a QuickNode URL went unflagged): only the path token
# goes, the host and the rest of the path stay. QuickNode <sub>.<chain>.quiknode.pro/<token>, Alchemy
# <net>.g.alchemy.com/v2/<key> (and alchemyapi.io), Infura <net>.infura.io/v3/<id>, Ankr rpc.ankr.com/<chain>/<key>,
# Blast API <net>.blastapi.io/<uuid>, dRPC ...drpc.org/...?dkey=<key> (and lb.drpc.org/ogrpc?…&dkey=).
_RPC_KEY = re.compile(
    rb"((?:https?|wss?)://[A-Za-z0-9.-]+\.quiknode\.pro/)([A-Za-z0-9]{20,})"
    rb"|((?:https?|wss?)://[A-Za-z0-9.-]*alchemy(?:api)?\.(?:com|io)/v[23]/)([A-Za-z0-9_-]{16,})"
    rb"|((?:https?|wss?)://[A-Za-z0-9.-]+\.infura\.io/(?:v3|ws/v3)/)([A-Fa-f0-9]{24,})"
    rb"|((?:https?|wss?)://rpc\.ankr\.com/[a-z0-9_]+/)([A-Fa-f0-9]{32,})"
    rb"|((?:https?|wss?)://[A-Za-z0-9.-]+\.blastapi\.io/)([A-Fa-f0-9-]{32,})"
    rb"|((?:https?|wss?)://[A-Za-z0-9.-]*drpc\.org/[^\s\\\"']*?[?&]dkey=)([A-Za-z0-9_-]{16,})")


def _redact_rpc(m):
    for g in (1, 3, 5, 7, 9, 11):
        if m.group(g):
            return m.group(g) + CRED_REDACTED
    return m.group(0)


# Tokens whose prefix names them, wherever they appear (a file read, a relayed '@path' expanded by the harness:
# platform_owner, 2026-11-06, a Grafana SA token). Only the part after the prefix goes.
# A prefix inside a longer run of base64url data ("...x-hf_..." in an encoded blob) is not a token: the character
# before it is not a base64url one, and a token ends within 255 characters (2026-11-11: two runs of 728 and 114,387
# characters were masked whole).
_PREFIXED = re.compile(
    rb"(?<![A-Za-z0-9_-])(glsa_|gh[pousr]_|github_pat_|glpat-|xox[abposr]-|xapp-|sk-ant-(?:api|oat|admin)\d\d-|npm_|hf_)"
    rb"([A-Za-z0-9_-]{20,255})(?![A-Za-z0-9_-])")


# A capability URL of an MCP server, where the token in the path is the credential (Acme's sec_keeper through
# acme_org, 2026-11-11: an accounting service's MCP URL, <name>.zohomcp.com/mcp/<32 hex>/message, in a tool result; the same URL was
# in a prompt and in transcripts from 2026-09-28 on). A token-like segment is 32 or more hex digits, a UUID, or 24 or
# more letters, digits, '_' and '-' with a letter next to a digit somewhere (a slug of words and numbers, as on a
# directory site, stays). It goes in two places: every such segment of a URL whose host names "mcp", with or without
# a scheme, and the value of a key-named query parameter there; and the segment right after /mcp/ in a URL on any
# host (sec_keeper's general rule), never in a file path (work/mcp/<file> is a name: 1,211 rows in the replay). The
# host and the other segments stay. The URL may be percent-encoded (inside another URL's query: %3A%2F%2F, %2F).
# Replayed over 21 days of rows (1.6 million): 6 rows changed, all this one URL.
_CAP = rb"(?:[0-9A-F]{32,}|[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}|(?=[A-Z0-9_-]*?(?:[A-Z][0-9]|[0-9][A-Z]))[A-Z0-9_-]{24,})"
_MCP_URL = re.compile(rb"((?:(?:https?|wss?)(?:://|%3A%2F%2F))?(?:(?<![A-Za-z0-9.-])|(?<=%2F))[A-Za-z0-9.-]*mcp"
                      rb"[A-Za-z0-9.-]*\.[A-Za-z]{2,}(?::\d{1,5}|%3A\d{1,5})?)((?:/|%2F)[^\s\"'\\<>]*)", re.I)
_CAP_SEGMENT = re.compile(rb"(?:(?<=/)|(?<=%2F))" + _CAP + rb"(?![A-Z0-9_-])", re.I)
_MCP_PATH = re.compile(rb"((?:https?|wss?)(?:://|%3A%2F%2F)[A-Za-z0-9.-]+(?::\d{1,5}|%3A\d{1,5})?"
                       rb"(?:(?:/|%2F)[A-Za-z0-9._~-]*?)*?(?:/|%2F)mcp(?:/|%2F))" + _CAP + rb"(?![A-Z0-9_-])", re.I)
_CAP_QUERY = re.compile(rb"([?&](?:api[_-]?key|key|token|access[_-]?token|auth|secret|sig|signature)=)([^&#]{8,})",
                        re.I)


def _redact_mcp(m):
    path = _CAP_SEGMENT.sub(CRED_REDACTED, m.group(2))
    return m.group(1) + (_CAP_QUERY.sub(lambda q: q.group(1) + CRED_REDACTED, path) if b"=" in path else path)


def redact_credentials(line):
    if any(p_ in line for p_ in (b"glsa_", b"gh", b"glpat-", b"xox", b"xapp-", b"sk-ant-", b"npm_", b"hf_")):
        line = _PREFIXED.sub(lambda m: m.group(1) + CRED_REDACTED, line)
    if b"mcp" in line or b"MCP" in line:
        line = _MCP_PATH.sub(lambda m: m.group(1) + CRED_REDACTED, _MCP_URL.sub(_redact_mcp, line))
    if b"quiknode" in line or b"alchemy" in line or b"infura" in line or b"ankr" in line or b"blastapi" in line \
            or b"drpc" in line:
        line = _RPC_KEY.sub(_redact_rpc, line)
    if b"Secret" in line:
        line = _redact_k8s_secret(line)
    if b"://" in line and b"@" in line:
        line = _DSN_PW.sub(lambda m: m.group(1) + (m.group(2) if m.group(2)[:1] in (b"$", b"*", b"{", b"[")
                                                   else CRED_REDACTED) + m.group(3), line)
    if b"=" in line:
        line = _ENV_SECRET.sub(lambda m: m.group(1) + m.group(2) + CRED_REDACTED, line)
    return line


def redact_rows(conn, ids, reason, fn=None, table="transcript_record", pause_s=0.02):
    """Re-redact stored rows (the core of `of redact`): each row's blob is read and masked outside any
    transaction, then ONE short write transaction per row stores the new blob, repoints the row and deletes
    the old blob when no row refers to it; the lock is released between rows (a redaction held it for
    minutes on 2026-11-06). Returns [(id, old sha, new sha)] for the rows that changed. The caller records
    of.redacted {table, ids, shas, reason}, never content."""
    fn = fn or redact_keys
    changed = []
    for rid in ids:
        row = conn.execute(f"SELECT blob_sha256 FROM {table} WHERE id = ?", (rid,)).fetchone()
        if not row:
            continue
        old = row[0]
        data = get_blob(conn, old) or b""
        new_data = fn(data)
        if new_data == data:
            continue
        new = hashlib.sha256(new_data).hexdigest()
        z, codec = (zstd.compress(new_data, level=ZLEVEL), "zstd") if zstd else (new_data, "raw")
        with write_tx(conn):  # milliseconds: no compression, no reads of large blobs inside
            cur = conn.execute(f"SELECT blob_sha256 FROM {table} WHERE id = ?", (rid,)).fetchone()
            if not cur or cur[0] != old:
                continue  # changed meanwhile
            conn.execute("INSERT OR IGNORE INTO blob VALUES (?, ?, ?, ?)", (new, len(new_data), codec, z))
            conn.execute(f"UPDATE {table} SET blob_sha256 = ? WHERE id = ?", (new, rid))
        changed.append((rid, old, new))
        if pause_s:
            time.sleep(pause_s)  # let hooks and the listener in between rows
    drop_unreferenced(conn, [o for _, o, _ in changed])
    return changed


def drop_unreferenced(conn, shas, batch=200):
    """Delete the blobs no row refers to any more. The reference check is one read-only pass over each
    table (blob_sha256 has no index; a check per row inside a write transaction held the lock for
    minutes), then short deletes in batches."""
    shas = sorted(set(shas))
    if not shas:
        return 0
    still = set()
    for t in ("transcript_record", "hook_record"):
        for i in range(0, len(shas), 900):
            part = shas[i:i + 900]
            still |= {r[0] for r in conn.execute(
                f"SELECT DISTINCT blob_sha256 FROM {t} WHERE blob_sha256 IN ({','.join('?' * len(part))})", part)}
    gone = [x for x in shas if x not in still]
    for i in range(0, len(gone), batch):
        with write_tx(conn):
            conn.executemany("DELETE FROM blob WHERE sha256 = ?", [(x,) for x in gone[i:i + batch]])
    return len(gone)


def with_subagents(path):
    """The transcript and its subagents' transcripts: Claude Code writes a session's subagents to
    `<dir>/<session>/subagents/*.jsonl` next to `<dir>/<session>.jsonl`. Live ingest reads them on
    the parent's Stop, SessionEnd and PreCompact; the per-file cursor keeps unchanged ones cheap."""
    out = [path]
    if path.endswith(".jsonl") and f"{os.sep}subagents{os.sep}" not in path:
        import glob
        out += sorted(glob.glob(os.path.join(path[:-len(".jsonl")], "subagents", "*.jsonl")))
    return out


def ingest_file(conn, path, harness="claude-code", max_bytes=None, on_record=None):
    """Read new complete lines of one transcript file since the last cursor. Returns lines added."""
    usage_cols = has_usage_columns(conn)
    try:
        st = os.stat(path)
    except OSError:
        return 0
    row = conn.execute("SELECT inode, offset FROM ingest_cursor WHERE source_path = ?", (path,)).fetchone()
    if path.endswith(".zst"):
        # A compressed rollout (Codex): finished files, read whole; offsets count decompressed bytes.
        if not zstd:
            return 0
        with open(path, "rb") as fh:
            whole = zstd.decompress(fh.read())
        offset = row[1] if row and row[0] == st.st_ino and row[1] <= len(whole) else 0
        if offset >= len(whole):
            return 0
        data = whole[offset:]
    else:
        offset = row[1] if row and row[0] == st.st_ino and row[1] <= st.st_size else 0
        if offset >= st.st_size:
            return 0
        with open(path, "rb") as fh:
            fh.seek(offset)
            data = fh.read(max_bytes) if max_bytes else fh.read()
    added = 0
    end = data.rfind(b"\n")
    if end < 0:
        return 0
    data = data[:end + 1]
    session_hint = session_from_path(path)
    # A new transcript may be a fork: its session_fork mark is written in the same transaction as its
    # first rows, so a reader following transcript_record by id never sees a copy before its mark.
    fork_check = [offset == 0 and f"{os.sep}subagents{os.sep}" not in path and has_uuid_index(conn)]
    last_sid = [None]

    def mark_fork():
        if fork_check[0] and last_sid[0]:
            fork_check[0] = False
            try:
                detect_forks(conn, [last_sid[0]], in_tx=True)
            except sqlite3.Error:
                pass
    lines = data.split(b"\n")[:-1]
    pos, i = offset, 0
    while i < len(lines):
        # Prepare a batch OUTSIDE any transaction: redaction, hashing, compression and parsing take the time
        # (an 8.2 s chunk on 2026-11-06); the transaction below only inserts. At most CHUNK lines or
        # CHUNK_BYTES of content per transaction.
        batch, size = [], 0
        while i < len(lines) and len(batch) < CHUNK and size < CHUNK_BYTES:
            line = lines[i]
            i += 1
            line_offset, pos = pos, pos + len(line) + 1
            if not line.strip():
                continue
            line = redact_keys(line)  # a private key or a credential never reaches the store
            sha = hashlib.sha256(line).hexdigest()
            z, codec = (zstd.compress(line, level=ZLEVEL), "zstd") if zstd else (line, "raw")
            try:
                obj = json.loads(line)
                f = _record_fields(obj)
            except Exception:
                obj, f = None, {}
            if on_record and obj is not None:
                try:
                    on_record(obj)  # outside the transaction; it may still only collect (see _ingest_with_reads)
                except Exception:
                    pass
            sid = f.get("session_id") or session_hint
            last_sid[0] = sid
            base = (path, line_offset, sha, sid, f.get("record_uuid") or sha, f.get("parent_uuid"), f.get("type"),
                    f.get("timestamp"), f.get("model"), f.get("tool_names"), f.get("input_tokens"),
                    f.get("output_tokens"), f.get("cache_read_tokens"), f.get("cache_write_tokens"), harness)
            batch.append((sha, len(line), codec, z, base, f))
            size += len(line)
        _guard(conn)
        conn.execute("BEGIN IMMEDIATE")
        prev, _TX.conn = getattr(_TX, "conn", None), conn
        t0 = time.monotonic()
        try:
            for sha, n, codec, z, base, f in batch:
                conn.execute("INSERT OR IGNORE INTO blob VALUES (?, ?, ?, ?)", (sha, n, codec, z))
                if usage_cols:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO transcript_record (source_path, line_offset, blob_sha256, session_id,"
                        " record_uuid, parent_uuid, type, timestamp, model, tool_names, input_tokens, output_tokens,"
                        " cache_read_tokens, cache_write_tokens, harness, message_id, cache_write_5m_tokens,"
                        " cache_write_1h_tokens) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        base + (f.get("message_id"), f.get("cache_write_5m_tokens"), f.get("cache_write_1h_tokens")))
                else:  # the columns are not there yet (a busy store at migration time): the backfill fills them
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO transcript_record (source_path, line_offset, blob_sha256, session_id,"
                        " record_uuid, parent_uuid, type, timestamp, model, tool_names, input_tokens, output_tokens,"
                        " cache_read_tokens, cache_write_tokens, harness) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", base)
                added += cur.rowcount
            mark_fork()
            conn.execute("INSERT OR REPLACE INTO ingest_cursor VALUES (?,?,?,?)",
                         (path, st.st_ino, pos if i < len(lines) else offset + len(data), of_events.now_iso()))
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            _TX.conn = prev
            warn_long(t0, "ingest chunk")
    return added


def stats(conn):
    q = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "events": q("SELECT count(*) FROM event"),
        "transcript_records": q("SELECT count(*) FROM transcript_record"),
        "sessions": q("SELECT count(DISTINCT session_id) FROM transcript_record"),
        "hook_records": q("SELECT count(*) FROM hook_record"),
        "blobs": q("SELECT count(*) FROM blob"),
        "raw_bytes": q("SELECT coalesce(sum(size), 0) FROM blob"),
        "stored_bytes": q("SELECT coalesce(sum(length(zdata)), 0) FROM blob"),
    }


# ------------------------------------------------------------------ spool
# When the database is busy, hooks append here instead of waiting; the ingest worker drains it.

def spool_path(name):
    d = os.path.join(of_events.STATE_DIR, "spool")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return os.path.join(d, name)


def spool_append(name, obj):
    import fcntl
    fd = os.open(spool_path(name), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")


def drain_spool(conn):
    """Move spooled events and raw hook payloads into the database. Returns the count.
    Leftover .draining files (a run that stopped midway) are drained too, and a busy store keeps the unread
    rest of a file for the next run instead of stranding the whole file (2026-11-06: ~20 files with the
    founder's answers stayed on disk for 20 minutes while a long redaction held the lock). One drainer at
    a time: the ingest worker's flock."""
    import glob
    n = 0
    for name in ("events.jsonl", "hook-raw.jsonl"):
        path = spool_path(name)
        legacy = os.path.join(of_events.STATE_DIR, "events.jsonl")  # the earlier fallback location
        works = []
        for src in ([path, legacy] if name == "events.jsonl" else [path]):
            works += sorted(glob.glob(f"{src}.draining.*"), key=os.path.getmtime)  # leftovers, oldest first
            if os.path.exists(src) and os.path.getsize(src) > 0:
                work = f"{src}.draining.{os.getpid()}"
                if os.path.exists(work):
                    work += f".{int(time.time() * 1000)}"
                os.replace(src, work)  # new appends go to a fresh file
                works.append(work)
        for work in works:
            with open(work) as fh:
                lines = fh.readlines()
            done = 0
            try:
                for line in lines:
                    try:
                        obj = json.loads(line)
                    except Exception:
                        done += 1
                        continue
                    try:
                        if name == "events.jsonl":
                            add_event(conn, obj)
                        else:
                            add_hook_record(conn, obj["hook_event"], obj.get("session_id"), obj["raw"])
                        n += 1
                    except (KeyError, TypeError, ValueError):
                        pass  # a malformed line never holds the rest back
                    done += 1
            except sqlite3.OperationalError:  # busy: keep what is left of this file, try again next run
                with open(work + ".tmp", "w") as fh:
                    fh.writelines(lines[done:])
                os.replace(work + ".tmp", work)
                return n
            os.remove(work)
    return n


def backfill_usage(conn, batch=5000, log=print):
    """Fill message_id and the cache writes by TTL on records ingested before they existed."""
    total = 0
    while True:
        rows = conn.execute("SELECT id, blob_sha256 FROM transcript_record WHERE message_id IS NULL AND type = 'assistant' "
                            "LIMIT ?", (batch,)).fetchall()
        if not rows:
            break
        upd = []
        for rid, sha in rows:
            try:
                f = _record_fields(json.loads(get_blob(conn, sha) or b"{}"))
            except ValueError:
                f = {}
            upd.append((f.get("message_id") or "", f.get("cache_write_5m_tokens"), f.get("cache_write_1h_tokens"), rid))
        with write_tx(conn):
            conn.executemany("UPDATE transcript_record SET message_id = ?, cache_write_5m_tokens = ?, "
                             "cache_write_1h_tokens = ? WHERE id = ?", upd)
        total += len(upd)
    log(f"usage backfill: {total} assistant records")
    return total
