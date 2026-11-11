"""Plugin seams of of-server (RFC 0001 §5.2a, deployment profiles).

The client and the API contract are the same in every profile; the server differs by the
plugins chosen in ~/.config/of/server.yaml:

  plugins:
    storage: sqlite        # sqlite (profile 1 default) | clickhouse
    buffer: disk           # disk | (kafka, redpanda: profile 3/4)
    blobs: fs              # fs | (s3: profile 3/4)
    identity: machine_keys # machine_keys | (oidc: profile 3/4)

Each seam is a small class with one implementation per profile; adding one is a new class
plus a name in REGISTRY, never a fork of the server.
"""

import json
import os
import sqlite3
import threading


# ------------------------------------------------------------------ storage

class Storage:
    """Where routed rows land: one database per organization, plus the vault and unassigned."""

    def insert(self, database, table, rows):
        raise NotImplementedError

    def describe(self):
        return type(self).__name__


class ClickHouseStorage(Storage):
    """ClickHouse over HTTP, inserting as `<database>_ingest` (profiles 1-optional, 2, 3, 4)."""

    def __init__(self, cfg, ship_cfg):
        import of_route
        self.ch = of_route.ClickHouse(ship_cfg)

    def insert(self, database, table, rows):
        self.ch.insert(database, table, rows)

    def locate(self, database, table, keys, host=None):
        """The keys (tuples) present in that table: where a redaction's rows were."""
        cols = KEYS[table]
        out = set()
        for i in range(0, len(keys), REDACT_CHUNK):
            where = redact_where(table, keys[i:i + REDACT_CHUNK], host)
            body = self.ch.post(f"SELECT DISTINCT {', '.join(cols)} FROM {database}.{table} WHERE {where}"
                                " FORMAT JSONCompactEachRow", auth=self.ch.redact_auth(database), query_in_body=True)
            for line in body.decode().splitlines():
                if line.strip():
                    out.add(tuple(int(v) if c == "local_id" else str(v) for c, v in zip(cols, json.loads(line))))
        return out

    def delete(self, database, table, keys, host=None):
        """Delete rows by natural key (redaction); never an `of.redacted` event. Returns the count."""
        # Chunks keep each statement under ClickHouse's max_query_size (256 KiB by default): a
        # thousand keys is one mutation per database, not one per session.
        return sum(self.ch.delete(database, table, redact_where(table, keys[i:i + REDACT_CHUNK], host))
                   for i in range(0, len(keys), REDACT_CHUNK))


# Natural keys: a re-insert replaces, as ReplacingMergeTree does in ClickHouse.
KEYS = {
    "event": ("event_id",),
    "transcript_record": ("session_id", "record_uuid"),
    "hook_record": ("host_id", "local_id"),
    "runtime_record": ("host_id", "received_at", "type", "pane_id"),
}
TS = {"event": "ts", "transcript_record": "ts", "hook_record": "received_at", "runtime_record": "received_at"}


REDACT_CHUNK = 1000


def _sql_str(v):
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


def redact_where(table, keys, host=None):
    """The ClickHouse condition for a redaction: the natural keys (tuples, in KEYS order), the
    machine's own rows only when `host` is given, and never an `of.redacted` event."""
    cols = KEYS[table]
    if not keys:
        return "0"
    vals = []
    for k in keys:
        if len(k) != len(cols):
            raise ValueError(f"key {k!r} does not match {cols}")
        vals.append("(" + ", ".join(str(int(v)) if c == "local_id" else _sql_str(v) for c, v in zip(cols, k)) + ")")
    where = f"({', '.join(cols)}) IN ({', '.join(vals)})" if len(cols) > 1 else f"{cols[0]} IN ({', '.join(v[1:-1] for v in vals)})"
    if host is not None:
        where += f" AND host_id = {_sql_str(host)}"
    if table == "event":
        where += " AND name != 'of.redacted'"
    return where


class SQLiteStorage(Storage):
    """Profile 1 default: one SQLite file per database under <state_dir>/storage/. No daemon, no
    ClickHouse. Rows are kept whole as JSON, with the natural key, time and organization as columns."""

    def __init__(self, cfg, ship_cfg):
        self.dir = os.path.join(cfg["state_dir"], "storage")
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        self.lock = threading.Lock()
        self.conns = {}

    def _conn(self, database):
        if database not in self.conns:
            path = os.path.join(self.dir, f"{database}.db")
            if not os.path.exists(path):
                os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
            c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            c.execute("PRAGMA journal_mode = WAL")
            for table in KEYS:
                c.execute(f"CREATE TABLE IF NOT EXISTS {table} (key TEXT PRIMARY KEY, ts TEXT, org_id TEXT, row TEXT)")
                c.execute(f"CREATE INDEX IF NOT EXISTS {table}_ts ON {table} (ts)")
            self.conns[database] = c
        return self.conns[database]

    def insert(self, database, table, rows):
        if table not in KEYS:
            raise ValueError(f"unknown table {table}")
        with self.lock:
            c = self._conn(database)
            c.execute("BEGIN")
            try:
                for r in rows:
                    key = "\x1f".join(str(r.get(k, "")) for k in KEYS[table])
                    c.execute(f"INSERT OR REPLACE INTO {table} VALUES (?,?,?,?)",
                              (key, r.get(TS[table]), r.get("org_id", ""), json.dumps(r, ensure_ascii=False)))
                c.execute("COMMIT")
            except BaseException:
                if c.in_transaction:
                    c.execute("ROLLBACK")
                raise

    def locate(self, database, table, keys, host=None):
        if table not in KEYS:
            raise ValueError(f"unknown table {table}")
        out = set()
        with self.lock:
            c = self._conn(database)
            for k in keys:
                q, args = f"SELECT row FROM {table} WHERE key = ?", ["\x1f".join(str(v) for v in k)]
                if host is not None:
                    q += " AND json_extract(row, '$.host_id') = ?"
                    args.append(host)
                if c.execute(q, args).fetchone():
                    out.add(tuple(k))
        return out

    def delete(self, database, table, keys, host=None):
        if table not in KEYS:
            raise ValueError(f"unknown table {table}")
        n = 0
        with self.lock:
            c = self._conn(database)
            for k in keys:
                key = "\x1f".join(str(v) for v in k)
                q = f"DELETE FROM {table} WHERE key = ?"
                args = [key]
                if host is not None:
                    q += " AND json_extract(row, '$.host_id') = ?"
                    args.append(host)
                if table == "event":
                    q += " AND coalesce(json_extract(row, '$.name'), '') != 'of.redacted'"
                n += c.execute(q, args).rowcount
        return n

    def count(self, database, table):
        with self.lock:
            return self._conn(database).execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# ------------------------------------------------------------------- buffer

class Buffer:
    """The durable queue between the acknowledgement and the writers."""

    def put(self, batch_id, payload):
        raise NotImplementedError

    def get(self, batch_id):
        raise NotImplementedError

    def done(self, batch_id):
        raise NotImplementedError

    def dead(self, batch_id):
        raise NotImplementedError

    def revive(self, batch_id):
        raise NotImplementedError


class DiskBuffer(Buffer):
    """Profiles 1-2: one file per batch, fsynced (file and directory) before the 202."""

    def __init__(self, cfg):
        self.queue = os.path.join(cfg["state_dir"], "queue")
        self.dead_dir = os.path.join(cfg["state_dir"], "dead")
        for d in (self.queue, self.dead_dir):
            os.makedirs(d, mode=0o700, exist_ok=True)

    def path(self, batch_id, dead=False):
        return os.path.join(self.dead_dir if dead else self.queue, batch_id + ".json")

    def put(self, batch_id, payload):
        path = self.path(batch_id)
        tmp = path + ".tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(payload, fh, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        dfd = os.open(self.queue, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)

    def get(self, batch_id):
        with open(self.path(batch_id)) as fh:
            return json.load(fh)

    def done(self, batch_id):
        if os.path.exists(self.path(batch_id)):
            os.remove(self.path(batch_id))

    def dead(self, batch_id):
        if os.path.exists(self.path(batch_id)):
            os.replace(self.path(batch_id), self.path(batch_id, dead=True))

    def revive(self, batch_id):
        """Back from the dead segment to the queue; False if it is not there."""
        if not os.path.exists(self.path(batch_id, dead=True)):
            return False
        os.replace(self.path(batch_id, dead=True), self.path(batch_id))
        return True


# -------------------------------------------------------------------- blobs

class Blobs:
    """Content-addressed large objects (position bundles, attachments). Not used by v0 ingest."""

    def put(self, data):
        raise NotImplementedError

    def get(self, sha256):
        raise NotImplementedError


class FsBlobs(Blobs):
    def __init__(self, cfg):
        self.dir = os.path.join(cfg["state_dir"], "blobs")
        os.makedirs(self.dir, mode=0o700, exist_ok=True)

    def put(self, data):
        import hashlib
        sha = hashlib.sha256(data).hexdigest()
        path = os.path.join(self.dir, sha[:2], sha)
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            with open(os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as fh:
                fh.write(data)
            os.replace(path + ".tmp", path)
        return sha

    def get(self, sha256):
        with open(os.path.join(self.dir, sha256[:2], sha256), "rb") as fh:
            return fh.read()


# ----------------------------------------------------------------- identity

class Identity:
    """Who a caller is: proves a machine (or later a person) and checks a signature."""

    def verify(self, principal_id, public_key, data, signature):
        raise NotImplementedError


class MachineKeyIdentity(Identity):
    """Ed25519 machine keys, signatures in OpenSSH SSHSIG format (`ssh-keygen -Y`)."""

    NAMESPACE = "of-server"

    def verify(self, principal_id, public_key, data, signature):
        import subprocess
        import tempfile
        import of_grants
        with tempfile.TemporaryDirectory() as d:
            allowed, sig, msg = (os.path.join(d, n) for n in ("allowed", "sig", "msg"))
            with open(allowed, "w") as fh:
                fh.write(f"{principal_id} namespaces=\"{self.NAMESPACE}\" {public_key}\n")
            with open(sig, "w") as fh:
                fh.write(signature)
            with open(msg, "wb") as fh:
                fh.write(data)
            with open(msg, "rb") as fh:
                r = subprocess.run(["ssh-keygen", "-Y", "verify", "-f", allowed, "-I", principal_id,
                                    "-n", self.NAMESPACE, "-s", sig], stdin=fh, capture_output=True,
                                   env=of_grants.no_agent_env())
            return r.returncode == 0


# ----------------------------------------------------------------- registry

REGISTRY = {
    "storage": {"sqlite": SQLiteStorage, "clickhouse": ClickHouseStorage},
    "buffer": {"disk": DiskBuffer},
    "blobs": {"fs": FsBlobs},
    "identity": {"machine_keys": MachineKeyIdentity},
}
DEFAULT_PLUGINS = {"storage": "sqlite", "buffer": "disk", "blobs": "fs", "identity": "machine_keys"}

# The four seams are families of the plugin mechanism shared with the client (bin/of_family.py, docs/plugins.md §2):
# the classes above are their built-ins, and a shipped (bin/plugins/<seam>/) or installed (entry point
# organic_framework.<seam>) implementation is found the same way. The choice stays in server.yaml.
import of_family  # noqa: E402

for _seam, _impls in REGISTRY.items():
    for _name, _cls in _impls.items():
        of_family.register(_seam, _name, _cls)


def load(cfg, ship_cfg):
    """Instantiate the configured plugins; an unknown name is a startup error, not a fallback."""
    chosen = dict(DEFAULT_PLUGINS, **(cfg.get("plugins") or {}))
    out = {}
    for seam, name in chosen.items():
        try:
            impl = of_family.implementation(seam, name)
        except of_family.PluginError:
            impl = None
        if impl is None:
            raise SystemExit(f"of-server: unknown {seam} plugin {name!r}; known: "
                             f"{sorted(of_family.implementations(seam)) if seam in of_family.FAMILIES else []}")
        if seam == "storage":
            out[seam] = impl(cfg, ship_cfg)
        elif seam == "identity":
            out[seam] = impl()
        else:
            out[seam] = impl(cfg)
    out["chosen"] = chosen
    return out
