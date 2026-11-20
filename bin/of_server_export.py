"""`of server export` and `of server import`: one organization's records moved from one of-server to another.

Made for the OF team's server moving to the agent host and, after it, a user organization's records moving to its
own server there (2026-11-19, of_server; the founder's prompts 01a1f5e4-0343 and 01a1f5f2-8d26). This module carries
the server store and ClickHouse parts.

A bundle is a folder (mode 0700, files 0600):
- store/<table>.jsonl.gz: the organization's rows of each table, one JSON object per line, or .jsonl.gz.age when the
  export names an age recipient: the gzip stream goes straight into `age`, with no plaintext on disk (the user
  organization's sec_keeper's condition, through of_architect, message 01a1f5ff-f009);
- clickhouse/<org|vault>/<table>/<UTC day>.native.gz (.age): one day's Native blocks, streamed like the store files;
- manifest.json.gz (.age): no row content, but row keys. It holds the format, the organization, the source's version,
  each file's sha256 and its gzip stream's (plain_sha256), and for each table the row count, a digest of the rows in
  key order, and per UTC day the count and the first and last key. Encrypted with the rest, since keys name things;
- index.json, the one plain file: the format, the organization, whether it is encrypted and each file's sha256. The
  export prints index.json's sha256, which the operator carries to the import by another path: age authenticates no
  sender, so only that hash ties a bundle to its export (of_secrets-review on organic-framework PR 425).

The store part is small and its rows change after they are written (a message's state, a lease), so it is exported
whole each time, at the bulk and again at the cut. Import replaces the organization's rows in one transaction.
Presence, passkey windows and links are not carried: relays publish presence again within a minute, and windows and
links expire within minutes. Machines, tokens, challenges and invites are not carried: machines enrol on the new
server. Nothing here prints a row: only counts, keys and digests.
"""

from contextlib import contextmanager
import datetime
import gzip
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from typing import NamedTuple

FORMAT = 2  # 2: index.json plain, the manifest encrypted with the rest (PR 425)
# table: (the organization's rows, the column that dates a row, the key columns in order)
STORE = {
    "messages": ("to_org = :org OR from_org = :org", "created_at", ("msg_id",)),
    "coord": ("org = :org", "added", ("event_id",)),
    "position_aliases": ("org = :org", "updated", ("org", "alias")),
    "presented": ("org = :org", "last_seen", ("org", "position")),
    "finance_facts": ("org = :org", "created_at", ("event_id",)),
    "secret_sightings": ("org = :org", "detected_at", ("batch_id", "kind", "hmac", "org", "tbl")),
    "passkey_enrollments": ("org = :org", "enrolled_at", ("credential_id",)),
}


class ExportError(Exception):
    pass


def _day(v):
    """The UTC day of a stored time: an ISO string's date, or an epoch number's."""
    if v is None or v == "":
        return "none"
    if isinstance(v, (int, float)):
        return datetime.datetime.fromtimestamp(float(v), datetime.timezone.utc).strftime("%Y-%m-%d")
    return str(v)[:10]


def _key(row, cols):
    return "/".join(str(row.get(c)) for c in cols)


def summarize(table, rows):
    """{rows, digest, days: {day: [count, first key, last key]}}, the same whatever order ROWS come in: they are put in
    the order of their keys as text here, so a store and a bundle summarize alike."""
    _, day_col, key_cols = STORE[table]
    h, days = hashlib.sha256(), {}
    for r in sorted(rows, key=lambda r: _key(r, key_cols)):
        h.update(json.dumps(r, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
        k, d = _key(r, key_cols), _day(r.get(day_col))
        e = days.setdefault(d, [0, k, k])
        e[0] += 1
        e[1], e[2] = min(e[1], k), max(e[2], k)
    return {"rows": sum(e[0] for e in days.values()), "digest": h.hexdigest(), "days": dict(sorted(days.items()))}


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _columns(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def ensure_schema(conn):
    """The tables a server's modules make when they first serve (the feed, finance facts, passkeys, sightings): a new
    server that has served nothing yet lacks some of them (of_review on organic-framework PR 425)."""
    import of_server_coord
    import of_server_finance
    import of_server_passkey
    import of_server_relay
    import of_server_secrets
    for ddl in (of_server_relay.DDL, of_server_coord.DDL, of_server_finance.DDL, of_server_passkey.DDL,
                of_server_secrets.DDL):
        conn.executescript(ddl)
    of_server_relay.migrate(conn)
    if "account" not in _columns(conn, "finance_facts"):  # of_server_finance._ensure's migration of an older store
        conn.execute("ALTER TABLE finance_facts ADD COLUMN account TEXT NOT NULL DEFAULT ''")  # (of_review, PR 425)


def _rows(conn, table, org, cols=None):
    where, _, key_cols = STORE[table]
    picked = ", ".join(cols) if cols else "*"
    cur = conn.execute(f"SELECT {picked} FROM {table} WHERE {where} ORDER BY {', '.join(key_cols)}", {"org": org})
    names = [d[0] for d in cur.description]
    for r in cur:
        yield dict(zip(names, r))


def _private_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)


def _open_private(path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")


AGE_RECIPIENT = re.compile(r"age1[0-9a-z]{58}")


AGE_PATHS = ("/usr/bin/age", "/opt/homebrew/bin/age", "/usr/local/bin/age")  # the packages' own places, not PATH


def _age():
    """age at one of its packages' own paths (the user organization's sec_keeper, msg 01a1f638-5862: not PATH)."""
    path = next((p for p in AGE_PATHS if os.path.isfile(p) and os.access(p, os.X_OK)), None)
    if not path:
        raise ExportError("age is not installed at " + " or ".join(AGE_PATHS))
    return path


def _code():
    """This module's sha256: a bulk, its delta and their import run the same release of it."""
    return _file_sha(os.path.realpath(__file__))


class _Hashing:
    """A file object that hashes every byte written to it or read from it on the way through."""

    def __init__(self, fh):
        self.fh, self.h = fh, hashlib.sha256()

    def write(self, b):
        self.h.update(b)
        return self.fh.write(b)

    def read(self, n=-1):
        b = self.fh.read(n)
        self.h.update(b)
        return b

    def flush(self):
        return self.fh.flush()


def _write_file(path, chunks, recipient=None):
    """Write the gzip of CHUNKS to PATH (mode 0600, new), through `age -r RECIPIENT` when given, with no plaintext
    on disk on the way. Returns {"sha256": of the file, "plain_sha256": of the gzip stream}."""
    import subprocess
    with _open_private(path) as out:
        proc = None
        if recipient:
            proc = subprocess.Popen([_age(), "-r", recipient], stdin=subprocess.PIPE, stdout=out,
                                    stderr=subprocess.DEVNULL)
            sink = _Hashing(proc.stdin)
        else:
            sink = _Hashing(out)
        try:
            with gzip.GzipFile(fileobj=sink, mode="wb", mtime=0) as gz:
                for c in chunks:
                    gz.write(c)
        finally:
            if proc:
                proc.stdin.close()
                if proc.wait() != 0:
                    raise ExportError(f"age failed writing {os.path.basename(path)}")
    return {"sha256": _file_sha(path), "plain_sha256": sink.h.hexdigest()}


@contextmanager
def _read_file(path, identity=None):
    """Hash the compressed plaintext on the way to the JSON reader or the Native insert."""
    import subprocess
    if path.endswith(".age"):
        if not identity:
            raise ExportError("the bundle is encrypted: name its key with --identity")
        proc = subprocess.Popen([_age(), "-d", "-i", identity, path], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
        src = _Hashing(proc.stdout)
    else:
        proc, src = None, _Hashing(open(path, "rb"))
    try:
        with gzip.GzipFile(fileobj=src, mode="rb") as gz:
            yield gz, src.h
        while src.read(1 << 16):  # whatever follows the gzip stream counts in its hash too
            pass
    finally:
        if proc:
            proc.stdout.close()
            if proc.wait() != 0:
                raise ExportError(f"age could not decrypt {os.path.basename(path)}")
        else:
            src.fh.close()


def _read_lines(path, identity=None):
    with _read_file(path, identity) as (gz, digest):
        yield from gz
    yield digest.hexdigest()


def _file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


MOVING_SETTLE_S = 1 + 30 + 30  # of_server.App.moving()'s cache, a long poll's MAX_WAIT, and a margin


def _hold(conn, org, config, since, since_sha, snap=None):
    """What must hold for the export to be whole: no batch of ORG queued or dead, no redaction not done, and for the
    final delta the organization marked moving. Checked at the start and again just before index.json is written, so
    a flag cleared mid-export, or a write that slipped in before the flag, is noticed (sec_keeper, round 3). The first
    call returns a snapshot (the newest batch and redaction, the flag's inode and mtime); the second, given it, refuses
    any batch of ORG or redaction newer than it, in any state, and a flag changed since, so a change between the two
    is seen, not only the state at the end. The final delta also waits until the flag is older than the server's
    cache, a long poll and a margin, so no request that read it unset is still writing (sec_keeper, round 4, msg
    01a1f6ea-b786)."""
    queued = _queued_batches(conn, org)
    if queued:  # accepted before the organization was marked moving, not yet written (of_review on PR 433)
        raise ExportError(f"{queued} of {org}'s batches are still queued or dead: wait for the writer, or replay or "
                          "drop the dead ones, with the organization marked moving (of server moving)")
    pending = _pending_redactions(conn, org)
    if pending:  # it would apply on this server only, and the new one would keep the rows it removes (sec_keeper)
        raise ExportError(f"{pending} redactions on this server are not done: finish them before the export")
    if since is not None:
        import of_server
        if not since_sha:
            raise ExportError("--since needs --since-sha, the bulk's index sha256")
        if not (config and os.path.exists(os.path.join(of_server.moving_dir(config), org))):
            raise ExportError(f"the final delta is made with {org} marked moving (of server moving --org {org} on), "
                              "so nothing reaches this server after it")
    flag = os.path.join(_moving_dir(config), org) if config else None
    try:
        st = os.stat(flag) if flag else None
    except FileNotFoundError:
        st = None
    if since is not None and st is not None and time.time() - st.st_mtime < MOVING_SETTLE_S:
        raise ExportError(f"{org} was marked moving {time.time() - st.st_mtime:.0f} s ago: wait until "
                          f"{MOVING_SETTLE_S} s have passed, so no request that read it unset is still writing")
    now = {"batch": _max(conn, "batches", "seq"), "redaction": _max(conn, "redactions", "rowid"),
           "flag": (st.st_ino, st.st_mtime_ns) if st else None}
    if snap is not None:
        if any(_names(by, org) for (by,) in conn.execute(
                "SELECT by_org FROM batches WHERE seq > ?", (snap["batch"],))) if "batches" in _tables(conn) else False:
            raise ExportError(f"a batch of {org} arrived while the export ran: export again")
        if now["redaction"] > snap["redaction"]:
            raise ExportError("a redaction arrived while the export ran: export again")
        if now["flag"] != snap["flag"]:
            raise ExportError(f"{org}'s moving flag changed while the export ran: export again")
    return now


def _moving_dir(config):
    import of_server
    return of_server.moving_dir(config)


def _max(conn, table, col):
    if table not in _tables(conn):
        return 0
    return conn.execute(f"SELECT coalesce(max({col}), 0) FROM {table}").fetchone()[0]


def _names(by_org, org):
    """Whether a batch's by_org may hold ORG's rows: it names ORG, or names no organization at all (NULL or {}, as a
    tenant's delete-only redaction's batch does, which deletes in every database), or cannot be read. An org-less
    batch counts for every organization (the user organization's sec_keeper, round 5, msg 01a1f6fc-399c)."""
    o = _orgs(by_org)
    return o is None or not o or org in o


def _orgs(by_org):
    try:
        return json.loads(by_org or "{}") or {}
    except ValueError:
        return None  # unreadable: counted as every organization's


def _pending_redactions(conn, org):
    """Redactions not done (queued, running or failed) of batches that hold rows of ORG (the user organization's
    sec_keeper, msg 01a1f645-eae4: a redaction at the cut applied on the laptop only)."""
    if "redactions" not in _tables(conn) or "batches" not in _tables(conn):
        return 0
    n = 0
    # every one, whatever its batch names: a tenant's delete-only redaction has by_org {} and deletes in every
    # database (the user organization's sec_keeper, round 3, msg 01a1f657-aeaa); a cancelled one is no work
    return conn.execute("SELECT count(*) FROM redactions WHERE coalesce(state, '') NOT IN ('done', 'cancelled')"
                        ).fetchone()[0]


def _queued_batches(conn, org):
    """The server's queued batches that hold rows of ORG: an export before they are written would leave them behind."""
    if "batches" not in _tables(conn):
        return 0
    n = 0
    for (by_org,) in conn.execute("SELECT by_org FROM batches WHERE state IN ('queued', 'dead')"):  # a dead one
        # may be replayed later, on this server only (sec_keeper, round 3)
        n += _names(by_org, org)
    return n


def _blob_count(cfg):
    """The files in the server's file blob store (of_plugins.FsBlobs), which a bundle does not carry yet."""
    d = os.path.join((cfg or {}).get("state_dir") or "", "blobs")
    return sum(len(files) for _, _, files in os.walk(d)) if cfg and os.path.isdir(d) else 0


def _belongs(table, row, org):
    """Whether ROW is ORG's by its table's own rule (STORE's predicate), read from the row itself."""
    if table == "messages":
        return org in (row.get("to_org"), row.get("from_org"))
    return row.get("org") == org


def export_store(conn, org, out, version=None, recipient=None, *, config=None, since=None, since_sha=None,
                 plaintext=False):
    """Write ORG's store and configured ClickHouse tables into OUT, each file encrypted to the age RECIPIENT when
    given. Returns (manifest, the sha256 of index.json): the operator carries that sha256 to the import by another
    path than the bundle (of_secrets-review on organic-framework PR 425: age authenticates no sender, so the bundle
    alone proves nothing about who made it)."""
    if recipient is not None and not AGE_RECIPIENT.fullmatch(recipient):
        raise ExportError("the recipient is not an age X25519 recipient (age1…)")
    if not recipient and not plaintext:  # records in clear on a machine where every agent runs as one OS user
        raise ExportError("name the receiving key with --to, or say --plaintext")
    if recipient:
        _age()
    if "machines" not in _tables(conn):  # an empty bundle from a file that is no server's store would, imported, wipe
        raise ExportError("this is no server's store: nothing to export")  # the organization's rows (PR 425)
    if since is not None and not (config and _uses_clickhouse(config)):
        raise ExportError("--since needs a ClickHouse export")
    snap = _hold(conn, org, config, since, since_sha)
    blobs = _blob_count(config)
    if blobs:  # the laptop's blob store held none on 2026-11-19; carrying them comes when a server keeps some
        raise ExportError(f"this server's blob store holds {blobs} files, which this release does not carry yet")
    base = _ch_delta_base(since, since_sha, org) if since is not None else None
    try:
        os.mkdir(out, 0o700)  # a new folder, never one that exists or a link to one
    except FileExistsError:
        raise ExportError(f"{out} exists: give a new folder") from None
    os.mkdir(os.path.join(out, "store"), 0o700)
    ext = ".age" if recipient else ""
    manifest = {"format": FORMAT, "org": org, "version": version, "encrypted": bool(recipient), "store": {},
                "columns": {}, "files": {}}
    conn.execute("BEGIN")  # one snapshot for every table
    try:
        have = _tables(conn)
        for table in STORE:  # a table this server never made holds no rows (of_review on PR 425: it stopped half-way)
            manifest["columns"][table] = _columns(conn, table) if table in have else []
            rows = list(_rows(conn, table, org)) if table in have else []
            rel = f"store/{table}.jsonl.gz{ext}"
            manifest["files"][rel] = _write_file(
                os.path.join(out, rel),
                (json.dumps(r, ensure_ascii=False, separators=(",", ":")).encode() + b"\n" for r in rows), recipient)
            manifest["store"][table] = summarize(table, rows)
    finally:
        conn.execute("ROLLBACK")
    if config and _uses_clickhouse(config):
        if not recipient:  # raw vault records in clear (of_secrets-review on organic-framework PR 433)
            raise ExportError("a bundle with ClickHouse rows is always encrypted: name the receiving key with --to")
        ch, databases = clickhouse_connection(config, org)
        if base is not None:
            manifest["delta_of"] = since_sha
        all_days = _export_clickhouse(ch, databases, out, manifest, recipient, base)
    else:
        all_days = None
    # The manifest names rows (message ids, alias and position names, sightings' hmacs), so it is encrypted with the
    # rest; index.json, the one plain file, holds only the organization and each file's sha256 (PR 425, point 3).
    _hold(conn, org, config, since, since_sha, snap)  # again, before anything marks the bundle ready
    mrel = f"manifest.json.gz{ext}"
    written = _write_file(os.path.join(out, mrel), [json.dumps(manifest, sort_keys=True).encode()], recipient)
    index = {"format": FORMAT, "org": org, "encrypted": bool(recipient), "code": _code(), "version": version,
             "files": dict({rel: f["sha256"] for rel, f in manifest["files"].items()}, **{mrel: written["sha256"]})}
    if all_days is not None:  # every current day's fingerprint: no key, no content (the delta and the check read it)
        index["days"] = all_days
        if base is not None:
            index["delta_of"] = since_sha
    raw = json.dumps(index, indent=1, sort_keys=True).encode() + b"\n"
    path = os.path.join(out, "index.json")
    fh = _open_private(path)
    try:
        with fh:
            fh.write(raw)
    except BaseException:
        os.unlink(path)  # an incomplete index must never mark the bundle as ready
        raise
    return manifest, hashlib.sha256(raw).hexdigest()


def load_index(bundle, index_sha=None):
    try:
        with open(os.path.join(bundle, "index.json"), "rb") as fh:
            raw = fh.read()
        if index_sha is not None and hashlib.sha256(raw).hexdigest() != index_sha:
            raise ExportError("index.json: its sha256 is not the one the export printed")
        ix = json.loads(raw)
    except (OSError, ValueError):
        raise ExportError("missing or unreadable index.json") from None
    if not isinstance(ix, dict) or ix.get("format") != FORMAT:
        raise ExportError(f"unsupported bundle format; this release reads {FORMAT}")
    if (not isinstance(ix.get("org"), str) or type(ix.get("encrypted")) is not bool or
            not isinstance(ix.get("files"), dict) or not isinstance(ix.get("code"), str) or
            not isinstance(ix.get("days", {}), dict) or
            any(not isinstance(per, dict) for per in ix.get("days", {}).values())):
        raise ExportError("invalid bundle file inventory")
    return ix


def load_manifest(bundle, identity=None, *, index=None):
    ix = index if index is not None else load_index(bundle)
    rel = "manifest.json.gz" + (".age" if ix.get("encrypted") else "")
    path = os.path.join(bundle, rel)
    try:
        if ix["files"].get(rel) != _file_sha(path):
            raise ExportError("the manifest's sha256 differs from index.json")
        with _read_file(path, identity) as (gz, _):
            m = json.load(gz)
    except (OSError, ValueError, EOFError):
        raise ExportError("cannot read the bundle manifest") from None
    if not isinstance(m, dict) or any(m.get(k) != ix[k] for k in ("org", "format", "encrypted")):
        raise ExportError("the manifest and index.json disagree")
    files = m.get("files")
    if (not isinstance(files, dict) or files.keys() != ix["files"].keys() - {rel} or
            any(not isinstance(f, dict) or f.get("sha256") != ix["files"][name] for name, f in files.items())):
        raise ExportError("the manifest and index.json disagree on the file inventory")
    return m


def _store_file(bundle, table, encrypted):
    return os.path.join(bundle, f"store/{table}.jsonl.gz" + (".age" if encrypted else ""))


def read_bundle_rows(bundle, table, identity=None, sha=None, encrypted=None):
    """The bundle's rows of TABLE; SHA, a list, receives the gzip stream's sha256 once they are all read."""
    if encrypted is None:
        encrypted = load_index(bundle).get("encrypted")
    for item in _read_lines(_store_file(bundle, table, encrypted), identity):
        if isinstance(item, str):
            if sha is not None:
                sha.append(item)
        else:
            yield json.loads(item)


def _compare(want, got, where):
    problems = []
    for table in STORE:
        w, g = want.get(table), got.get(table)
        if w is None:
            problems.append(f"{table}: not in the manifest")
        elif w != g:
            wd, gd = w.get("days", {}), (g or {}).get("days", {})
            bad = sorted(d for d in set(wd) | set(gd) if wd.get(d) != gd.get(d))
            problems.append(f"{table}: {where} differs ({w.get('rows')} rows wanted, {(g or {}).get('rows')} found; "
                            f"days {', '.join(bad[:5]) or 'none'}{' …' if len(bad) > 5 else ''})")
    return problems


def check_bundle(bundle, identity=None, index_sha=None):
    """The bundle against its index and manifest: [] when they match, else the differences. INDEX_SHA, when given, is
    the sha256 the export printed, carried here by another path. An encrypted bundle checks its ciphertext hashes
    alone without IDENTITY, and everything with it."""
    return _checked_bundle(bundle, identity, index_sha)[1]


def _checked_bundle(bundle, identity, index_sha):
    """Return the very manifest whose inventory and day files were checked for this import."""
    try:
        ix = load_index(bundle, index_sha)
    except ExportError as e:
        return None, [str(e)]
    ext = ".age" if ix["encrypted"] else ""
    required = {f"store/{t}.jsonl.gz{ext}" for t in STORE} | {f"manifest.json.gz{ext}"}
    native_pattern = (r"clickhouse/(org|vault)/(" + "|".join(CH_TABLES) +
                      r")/[0-9]{4}-[0-9]{2}-[0-9]{2}\.native\.gz" + re.escape(ext))
    indexed = ix["files"].keys() - required
    if not required <= ix["files"].keys() or any(not re.fullmatch(native_pattern, rel) for rel in indexed):
        return None, ["invalid bundle file inventory"]
    for folder, dirs, names in os.walk(bundle):  # a link would make a check read a file outside the bundle
        if any(os.path.islink(os.path.join(folder, n)) for n in dirs + names):
            return None, ["the bundle holds a symbolic link"]
    for folder, _, names in os.walk(os.path.join(bundle, "clickhouse")):
        for name in names:
            rel = os.path.relpath(os.path.join(folder, name), bundle)
            if name.endswith((".native.gz", ".native.gz.age")) and rel not in indexed:
                return None, ["index.json omits a Native file"]
    problems = [f"{rel}: sha256 differs" for rel, sha in ix["files"].items()
                if not os.path.exists(os.path.join(bundle, rel)) or _file_sha(os.path.join(bundle, rel)) != sha]
    if problems or (ix.get("encrypted") and not identity):
        return None, problems
    try:
        m = load_manifest(bundle, identity, index=ix)
        native = _ch_inventory(m)
    except ExportError as e:
        return None, [str(e)]
    if native != indexed:
        return None, ["the manifest and index.json disagree on the file inventory"]
    got = {}
    for table in STORE:
        sha = []
        got[table] = summarize(table, read_bundle_rows(bundle, table, identity, sha, ix.get("encrypted")))
        rel = os.path.relpath(_store_file(bundle, table, ix.get("encrypted")), bundle)
        if sha != [m["files"][rel]["plain_sha256"]]:
            problems.append(f"{rel}: the decrypted stream's sha256 differs")
    for rel in sorted(native):
        try:
            with _read_file(os.path.join(bundle, rel), identity) as (gz, digest):
                while gz.read(1 << 16):
                    pass
            if digest.hexdigest() != m["files"][rel]["plain_sha256"]:
                problems.append(f"{rel}: the decrypted stream's sha256 differs")
        except Exception:
            problems.append(f"{rel}: cannot read the Native file")
    return m, problems + _compare(m["store"], got, "the bundle")


def check_store(conn, bundle, identity=None, manifest=None):
    """The manifest recomputed from what CONN holds for the bundle's organization, over the source's own columns: a
    store a column ahead still matches (of_review on organic-framework PR 425). [] when it matches."""
    m = manifest or load_manifest(bundle, identity)
    have, got, problems = _tables(conn), {}, []
    for table in STORE:
        cols = m.get("columns", {}).get(table) or []
        if not cols:
            got[table] = summarize(table, [])
            continue
        missing = [c for c in cols if c not in (_columns(conn, table) if table in have else [])]
        if missing:
            problems.append(f"{table}: the store has no column {', '.join(missing)}")
            continue
        got[table] = summarize(table, list(_rows(conn, table, m["org"], cols)))
    return problems + [p for p in _compare(m["store"], got, "the store")
                       if p.split(":", 1)[0] not in {q.split(":", 1)[0] for q in problems}]


def _check_import(bundle, org, index_sha, identity, plaintext=False):
    if not index_sha:
        raise ExportError("give the sha256 the export printed (--index-sha): the bundle alone proves nothing")
    ix = load_index(bundle, index_sha)
    if not ix["encrypted"] and not plaintext:
        raise ExportError("the bundle is not encrypted: refused unless you say --plaintext")
    if ix["code"] != _code():
        raise ExportError("the bundle was made by another release of this code: import it with the same one")
    m, problems = _checked_bundle(bundle, identity, index_sha)
    if not problems:
        if m is None:
            raise ExportError("the bundle is encrypted: name its key with --identity")
        if org != m["org"]:
            problems.append(f"the bundle is {m['org']}'s, not {org}'s")
    return m, problems


def check_import(conn, bundle, org, index_sha, identity=None, *, config=None, plaintext=False):
    """Compare the target only after checking the bundle for the operator's organization and hash."""
    m, problems = _check_import(bundle, org, index_sha, identity, plaintext)
    if not problems:
        problems = check_store(conn, bundle, manifest=m)
    if not problems and "clickhouse" in m:
        ch, databases = clickhouse_connection(config, org)
        problems = _check_clickhouse(ch, databases, m, load_index(bundle, index_sha)["days"])
    return problems


def _serves_another(conn, org):
    """Whether this store holds another organization's own records: then a message between the two, carried in both
    bundles, would replace that organization's copy here (the user organization's sec_keeper, msg 01a1f638-5862)."""
    have = _tables(conn)
    checks = [("messages", "to_org != :org AND from_org != :org")] + \
        [(t, "org != :org") for t in STORE if t != "messages"]   # all seven (sec_keeper, msg 01a1f645-eae4)
    return any(conn.execute(f"SELECT 1 FROM {t} WHERE {w} LIMIT 1", {"org": org}).fetchone()
               for t, w in checks if t in have)


def import_store(conn, bundle, org, index_sha, identity=None, *, config=None, plaintext=False):
    """Replace ORG's rows with the bundle's, table by table, in one transaction. Refused, with nothing changed, unless
    the operator's ORG is the bundle's, the INDEX_SHA the export printed matches, the bundle checks out, every row is
    ORG's by its own table's rule, and every column of every row exists here (of_secrets-review on organic-framework
    PR 425). The store is checked against the manifest inside the transaction, which rolls back on any difference.
    A key another organization's row holds here refuses the whole import too. Once the store commits, replace the
    verified ClickHouse days and compare the target's FINAL summaries. Returns {table: rows}."""
    m, problems = _check_import(bundle, org, index_sha, identity, plaintext)
    if problems:
        raise ExportError("the bundle does not match its index: " + "; ".join(problems))
    if "machines" in _tables(conn) and _serves_another(conn, org):
        raise ExportError("this store holds another organization's records: import into a server of this one alone")
    if "machines" not in _tables(conn):  # not a server's store: the new server makes it at its first start
        raise ExportError("this is no server's store: start the new server once, empty, before importing")
    ensure_schema(conn)  # the tables its modules make only when they first serve
    rows = {}
    for table in STORE:  # every row read and judged before anything here changes
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        rows[table] = list(read_bundle_rows(bundle, table, identity, encrypted=m["encrypted"]))
        for r in rows[table]:
            if not _belongs(table, r, org):
                raise ExportError(f"{table}: a row is not {org}'s by the table's own rule; nothing imported")
            extra = sorted(set(r) - cols)
            if extra:
                raise ExportError(f"{table}: this store has no column {', '.join(extra)}; nothing imported")
    counts = {}
    conn.execute("BEGIN IMMEDIATE")
    try:
        for table, (where, _, _) in STORE.items():
            conn.execute(f"DELETE FROM {table} WHERE {where}", {"org": org})
            for r in rows[table]:
                names = list(r)
                try:
                    conn.execute(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                                 [r[c] for c in names])
                except Exception as e:  # sqlite3.IntegrityError: a key another organization holds here
                    raise ExportError(f"{table}: a row's key is held by another organization's row here "
                                      f"({type(e).__name__}); nothing imported") from None
            counts[table] = len(rows[table])
        problems = check_store(conn, bundle, manifest=m)
        if problems:
            raise ExportError("the store would not match the bundle: " + "; ".join(problems) + "; nothing imported")
        if "clickhouse" in m:
            ch, databases = clickhouse_connection(config, org)  # missing target configuration still rolls back
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    if "clickhouse" in m:
        _import_clickhouse(ch, databases, bundle, identity, m)
        problems = _check_clickhouse(ch, databases, m, load_index(bundle, index_sha)["days"])
        if problems:
            raise ExportError("; ".join(problems))
        counts.update({name: v["rows"] for name, v in m["clickhouse"].items()})
    return counts


# ---------------------------------------------------------------- ClickHouse

# UTC DateTime64 column and complete ReplacingMergeTree sorting tuple in deploy/clickhouse/schema.py.
CH_TABLES = {
    "event": ("ts", "org_id, name, ts, event_id"),
    "transcript_record": ("ts", "session_id, record_uuid"),
    "hook_record": ("received_at", "host_id, local_id"),
    "runtime_record": ("received_at", "host_id, received_at, type, pane_id"),
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):  # a redirect would carry the Authorization header to another address
        return None


def _open(req, context=None):
    """No proxy from the environment and no redirect (the user organization's sec_keeper, msg 01a1f638-5862); with a
    pinned certificate's CONTEXT, that certificate alone is trusted (`clickhouse: ca`)."""
    handlers = [urllib.request.ProxyHandler({}), _NoRedirect()]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers).open(req, timeout=600)


class ClickHouseHTTP:
    """Stream both directions with the tenant credential. Driver errors can contain rows and passwords."""

    def __init__(self, cfg):
        import of_route
        try:
            u = urllib.parse.urlsplit(cfg["url"])
        except Exception:
            raise ExportError("cannot read the ClickHouse connection settings") from None
        if u.scheme != "https" and not (u.scheme == "http" and u.hostname in ("127.0.0.1", "localhost", "::1")):
            raise ExportError("ClickHouse over plain http only on this machine's loopback")
        try:
            self.url = cfg["url"].rstrip("/") + "/?"
            client = of_route.ClickHouse(cfg)
            self.auth, self.context = client.auth, client.context
        except Exception:
            raise ExportError("cannot read the ClickHouse connection settings") from None

    def _post(self, params, body):
        try:
            req = urllib.request.Request(self.url + urllib.parse.urlencode(params), data=body,
                                         headers={"Authorization": self.auth, "Content-Type": "application/octet-stream"})
            with _open(req, self.context) as response:
                yield from iter(lambda: response.read(1 << 16), b"")
        except Exception:
            raise ExportError("ClickHouse request failed") from None

    def query(self, sql, params):
        # ClickHouse HTTP parameters are escaped text, then URL-encoded.
        escapes = str.maketrans({"\\": "\\\\", "\t": "\\t", "\n": "\\n", "\r": "\\r", "\0": "\\0"})
        return self._post({"param_" + k: str(v).translate(escapes) for k, v in params.items()}, sql.encode())

    def insert(self, sql, chunks):
        for _ in self._post({"query": sql}, chunks):
            pass


def _uses_clickhouse(cfg):
    """Whether this server stores in ClickHouse: its storage plugin, as of_plugins.load chooses it. The connection may
    come from ship.yaml alone (the laptop's server has no `clickhouse` in server.yaml)."""
    import of_plugins
    return dict(of_plugins.DEFAULT_PLUGINS, **(cfg.get("plugins") or {})).get("storage") == "clickhouse"


def clickhouse_connection(cfg, org):
    """Use the same database mapping and configuration precedence as of_server.App."""
    import of_route
    import of_server
    import of_ship
    if not (cfg and _uses_clickhouse(cfg)):
        raise ExportError("the bundle includes ClickHouse but this server's storage is not ClickHouse")
    try:
        ship = of_ship.config()
        ship.update(of_server.route_settings(cfg, ship))
        ship.update(cfg.get("clickhouse") or {})  # server.yaml's over ship.yaml's, as of_server.App does
        if cfg.get("orgs"):
            orgs, unassigned = cfg["orgs"], ship["unassigned_database"]
        else:
            router = of_route.Router(ship)
            orgs, unassigned = router.db_by_org, router.unassigned
        if org not in orgs:
            raise ExportError("no ClickHouse database configured for this organization")
        db, vault = orgs[org], ship["vault_database"]
        if not all(isinstance(d, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", d) for d in (db, vault)):
            raise ExportError("invalid ClickHouse database name")
        if db in (vault, unassigned) or list(orgs.values()).count(db) != 1:
            raise ExportError("the organization needs its own ClickHouse database")
        return ClickHouseHTTP(ship), {"org": db, "vault": vault}
    except ExportError:
        raise
    except Exception:
        raise ExportError("cannot read the ClickHouse connection settings") from None


class _CHTable(NamedTuple):
    name: str
    source: str
    date: str
    keys: str
    scope: str
    params: dict

    def where(self, condition=""):
        predicates = [p for p in (self.scope, condition) if p]
        return " WHERE " + " AND ".join(predicates) if predicates else ""


def _org_columns(ch, databases):
    """The vault tables that carry org_id here: hook_record and runtime_record have it only once
    deploy/clickhouse/schema.py's ALTER ran on this ClickHouse (601dacfe, 2026-11-19)."""
    sql = ("SELECT table FROM system.columns WHERE database = {db:String} AND name = 'org_id' "
           "FORMAT TabSeparated")
    try:
        return set(b"".join(ch.query(sql, {"db": databases["vault"]})).decode("ascii").split())
    except Exception:
        raise ExportError("cannot read the vault's columns") from None


# A vault hook row of an older day names no organization; its session's transcript rows do, when they name one alone.
_ONE_ORG_SESSIONS = ("session_id IN (SELECT session_id FROM {db}.transcript_record WHERE notEmpty(session_id) "
                     "GROUP BY session_id HAVING min(org_id) = {{org:String}} AND max(org_id) = {{org:String}})")


def _ch_tables(databases, org, org_cols=frozenset({"event", "transcript_record"})):
    """Each table's rows that are ORG's. The organization's own database holds only its rows. In the vault: event and
    transcript_record by org_id; hook_record by org_id, or, on a row that names none, by its session's one
    organization; runtime_record by org_id alone, so a row that names none stays behind (counted by
    _left_behind). ORG_COLS: the vault tables that have org_id here (_org_columns)."""
    for role, db in databases.items():
        for table, (stamp, keys) in CH_TABLES.items():
            scope = ""
            if role == "vault":
                has = table in org_cols
                if table == "hook_record":
                    legacy = _ONE_ORG_SESSIONS.format(db=db)
                    scope = f"(org_id = {{org:String}} OR (org_id = '' AND {legacy}))" if has else legacy
                elif table == "runtime_record":
                    if not has:
                        continue  # no row here names an organization: every one stays behind
                    scope = "org_id = {org:String}"
                else:
                    scope = "org_id = {org:String}"
            yield _CHTable(f"{role}/{table}", f"{db}.{table}", f"toDate({stamp})", keys, scope,
                           {"org": org} if scope else {})


def _left_behind(ch, databases, org_cols):
    """Per vault table and UTC day, the rows that no organization's export takes: rows naming no organization in
    event, transcript_record and runtime_record, and hook rows naming none whose session names no organization, or
    two (the user organization's keeper, through of_architect, msg 01a1f620-c225, counted per table and day for the
    founder's card on deleting the laptop's copy; its sec_keeper, msg 01a1f638-5862: a session whose transcript rows
    all name no organization is no organization's)."""
    v = databases["vault"]
    queries = {
        "vault/event": f"SELECT toDate(ts), count() FROM {v}.event FINAL WHERE org_id = ''",
        "vault/transcript_record": f"SELECT toDate(ts), count() FROM {v}.transcript_record FINAL WHERE org_id = ''",
        "vault/runtime_record": (f"SELECT toDate(received_at), count() FROM {v}.runtime_record FINAL"
                                 + (" WHERE org_id = ''" if "runtime_record" in org_cols else "")),
        "vault/hook_record": (f"SELECT toDate(received_at), count() FROM {v}.hook_record FINAL WHERE "
                              + ("org_id = '' AND " if "hook_record" in org_cols else "")
                              + f"session_id NOT IN (SELECT session_id FROM {v}.transcript_record WHERE "
                                "notEmpty(session_id) GROUP BY session_id HAVING min(org_id) = max(org_id) "
                                "AND min(org_id) != '')"),
    }
    out = {}
    for name, sql in queries.items():
        try:
            rows = b"".join(ch.query(sql + " GROUP BY 1 ORDER BY 1 FORMAT JSONCompactEachRow", {})).splitlines()
            out[name] = {_ch_day(d): int(n) for d, n in map(json.loads, rows)}
        except Exception:
            raise ExportError(f"{name}: cannot count the rows left behind") from None
    return out


def _ch_day(day):
    if not isinstance(day, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day):
        raise ValueError
    datetime.date.fromisoformat(day)
    return day


def _ch_columns(ch, source):
    """SOURCE's columns in their order: the content hash runs over the source's own columns on both sides, so a
    target a column ahead still matches."""
    db, table = source.split(".", 1)
    sql = ("SELECT name FROM system.columns WHERE database = {db:String} AND table = {t:String} "
           "ORDER BY position FORMAT TabSeparated")
    try:
        cols = b"".join(ch.query(sql, {"db": db, "t": table})).decode("ascii").split()
    except Exception:
        raise ExportError(f"{source}: cannot read its columns") from None
    if not cols or not all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", c) for c in cols):
        raise ExportError(f"{source}: unexpected column names")
    return cols


def _ch_days(ch, table, cols):
    """Per UTC day, with FINAL: [count, first key, last key, content hash], the hash ClickHouse's XOR of each row's
    cityHash64 over COLS. A late row, a redaction or a row replaced in place changes the day's entry (the user
    organization's sec_keeper, msg 01a1f638-5862: a delta by event day missed them)."""
    sql = (f"SELECT {table.date} AS day, count(), toString(min(tuple({table.keys}))), "
           f"toString(max(tuple({table.keys}))), toString(groupBitXor(cityHash64({', '.join(cols)}))) "
           f"FROM {table.source} FINAL{table.where()} GROUP BY day ORDER BY day FORMAT JSONCompactEachRow")
    try:
        rows = map(json.loads, b"".join(ch.query(sql, dict(table.params))).splitlines())
        return {_ch_day(d): [int(n), first, last, str(h)] for d, n, first, last, h in rows}
    except Exception:
        raise ExportError(f"{table.name}: cannot read daily totals") from None


EMPTY_DAY = [0, None, None, "0"]


def _fingerprint(entry):
    """A day's entry as the plain index carries it: no key, no content, only a hash of the entry."""
    return hashlib.sha256(json.dumps(entry).encode()).hexdigest()


def _ch_file(name, day, encrypted):
    return f"clickhouse/{name}/{day}.native.gz" + (".age" if encrypted else "")


def _ch_inventory(m):
    """Derive the allowed paths from complete tables and validated dates before any destructive operation."""
    if "clickhouse" not in m:
        return set()
    names = {f"{role}/{t}" for role in ("org", "vault") for t in CH_TABLES}
    files = set()
    try:
        if set(m["clickhouse"]) != names or set(m.get("clickhouse_columns", {})) - names:
            raise ValueError
        left = m.get("clickhouse_left_behind", {})
        if set(left) - {f"vault/{t}" for t in CH_TABLES} or any(
                type(n) is not int or n < 0 or _ch_day(d) != d for per in left.values() for d, n in per.items()):
            raise ValueError
        for name, summary in m["clickhouse"].items():
            total = 0
            for d, (n, first, last, h) in summary["days"].items():
                _ch_day(d)
                if type(n) is not int or not isinstance(h, str):
                    raise ValueError
                if n == 0:
                    if [n, first, last, h] != EMPTY_DAY or not m.get("delta_of"):
                        raise ValueError  # an emptied day exists only in a delta, to remove the target's copy
                    continue
                if n < 1 or not isinstance(first, str) or not isinstance(last, str):
                    raise ValueError
                total += n
                files.add(_ch_file(name, d, m["encrypted"]))
            if type(summary["rows"]) is not int or summary["rows"] != total:
                raise ValueError
        if files != {rel for rel in m["files"] if rel.startswith("clickhouse/")}:
            raise ValueError
    except (TypeError, ValueError, KeyError, AttributeError):
        raise ExportError("invalid ClickHouse manifest or file inventory") from None
    return files


def _ch_delta_base(bulk, bulk_index_sha, org):
    """The bulk's day fingerprints, from its plain index.json checked against the sha256 its export printed: a delta
    needs no key on the exporting machine (the user organization's sec_keeper, msg 01a1f638-5862)."""
    ix = load_index(bulk, bulk_index_sha)
    if ix["org"] != org or not isinstance(ix.get("days"), dict) or ix.get("delta_of"):
        raise ExportError("expected the ClickHouse bulk bundle of this organization")
    if ix.get("code") != _code():
        raise ExportError("the bulk was made by another release of this code: make the delta with the same one")
    return ix["days"]


def _export_clickhouse(ch, databases, out, m, recipient, base=None):
    """Write the organization's ClickHouse days into OUT. With BASE (a bulk's fingerprints), only the days whose entry
    changed or appeared since, and the days emptied since as empty entries. Returns every current day's fingerprint
    per table, which the plain index carries for the import's check."""
    summaries = m["clickhouse"] = {}
    m["clickhouse_columns"] = {}
    org_cols = _org_columns(ch, databases)
    m["clickhouse_left_behind"] = _left_behind(ch, databases, org_cols)
    summaries["vault/runtime_record"] = {"rows": 0, "days": {}}  # replaced below when its rows name organizations
    current_all = {"vault/runtime_record": {}}
    root = os.path.join(out, "clickhouse")
    _private_dir(root)
    for role in databases:
        _private_dir(os.path.join(root, role))
    for table in _ch_tables(databases, m["org"], org_cols):
        cols = _ch_columns(ch, table.source)
        current = _ch_days(ch, table, cols)
        prints = {d: _fingerprint(v) for d, v in current.items()}
        current_all[table.name] = prints
        if base is None:
            carry = current
        else:
            was = base.get(table.name) or {}
            carry = {d: v for d, v in current.items() if was.get(d) != prints[d]}
            carry.update({d: list(EMPTY_DAY) for d in was if d not in current})
        m["clickhouse_columns"][table.name] = cols
        summaries[table.name] = {"rows": sum(v[0] for v in carry.values()), "days": carry}
        if any(v[0] for v in carry.values()):
            _private_dir(os.path.join(root, table.name))
        sql = f"SELECT * FROM {table.source} FINAL{table.where(f'{table.date} = {{d:Date}}')} FORMAT Native"
        for d, v in carry.items():
            if not v[0]:
                continue
            rel = _ch_file(table.name, d, m["encrypted"])
            try:
                blocks = ch.query(sql, dict(table.params, d=d))
                m["files"][rel] = _write_file(os.path.join(out, rel), blocks, recipient)
            except Exception:
                raise ExportError(f"{table.name} {d}: export failed") from None
    return current_all


def _target_tables(ch, databases, m):
    """The target's tables for the bundle's organization, refusing a bundle that carries rows of a table the target
    cannot scope (a vault runtime_record without org_id: deploy/clickhouse/schema.py's ALTER not run there), which
    would otherwise be skipped while the import reported success (of_review and of_secrets-review on PR 433)."""
    tables = list(_ch_tables(databases, m["org"], _org_columns(ch, databases)))
    names = {t.name for t in tables}
    lost = sorted(n for n, v in m["clickhouse"].items() if n not in names and (v["rows"] or v["days"]))
    if lost:
        raise ExportError(f"the target cannot take {', '.join(lost)}: it has no org_id there; run "
                          "deploy/clickhouse/schema.py's ALTER on it first")
    return tables


def _check_clickhouse(ch, databases, m, all_days):
    """Every current day of the source (ALL_DAYS, from the plain index) against the target's, by fingerprint."""
    problems = []
    for table in _target_tables(ch, databases, m):
        cols = m.get("clickhouse_columns", {}).get(table.name) or _ch_columns(ch, table.source)
        got = {d: _fingerprint(v) for d, v in _ch_days(ch, table, cols).items()}
        want = all_days.get(table.name) or {}
        bad = sorted(d for d in want.keys() | got.keys() if want.get(d) != got.get(d))
        if bad:
            problems.append(f"{table.name}: ClickHouse differs on {', '.join(bad[:5])}{' …' if len(bad) > 5 else ''}")
    return problems


def _foreign_rows(ch, table, stage, org):
    """Staged vault rows that are not ORG's by the table's own rule, as the SQLite side judges each row (of_secrets-
    review on PR 433): org_id, or for a hook row naming none, a session whose transcript rows here or staged name ORG
    alone. The organization's own database holds only its rows by routing: nothing to judge there."""
    if not table.scope:
        return 0
    db = table.source.split(".", 1)[0]
    if table.name == "vault/hook_record":
        sessions = (f"SELECT session_id FROM (SELECT session_id, org_id FROM {db}.transcript_record UNION ALL "
                    f"SELECT session_id, org_id FROM {db}.transcript_record__import) WHERE notEmpty(session_id) "
                    "GROUP BY session_id HAVING min(org_id) = {org:String} AND max(org_id) = {org:String}")
        has = "org_id" in _ch_columns(ch, stage)
        rule = (f"(org_id = {{org:String}} OR (org_id = '' AND session_id IN ({sessions})))" if has
                else f"session_id IN ({sessions})")
    else:
        rule = "org_id = {org:String}"
    sql = f"SELECT count() FROM {stage} WHERE NOT {rule} FORMAT TabSeparated"
    try:
        return int(b"".join(ch.query(sql, {"org": org})) or b"0")
    except Exception:
        raise ExportError(f"{table.name}: cannot judge the staged rows") from None


def _import_clickhouse(ch, databases, bundle, identity, m):
    """Load every carried day into a staging table first, hashing each stream on the way in and checking the staged
    days against the manifest; only then replace the target's days, server-side, from the staging table. A failure
    before the swap changes nothing in the target (the user organization's sec_keeper, msg 01a1f638-5862)."""
    tables = _target_tables(ch, databases, m)
    staged = []
    keep_stage = [False]  # set when a swap failed: the staging tables stay for the retry (sec_keeper)
    try:
        vault_transcript = next((t for t in tables if t.name == "vault/transcript_record"), None)
        if vault_transcript is not None:  # judged against by the hook rows even when no transcript day is carried
            for sql in (f"CREATE TABLE IF NOT EXISTS {vault_transcript.source}__import AS {vault_transcript.source}",
                        f"TRUNCATE TABLE {vault_transcript.source}__import"):
                for _ in ch.query(sql, {}):
                    pass
            staged.append(vault_transcript.source + "__import")
        for table in tables:
            days = m["clickhouse"][table.name]["days"]
            if not days:
                continue
            stage = table.source + "__import"
            for sql in (f"CREATE TABLE IF NOT EXISTS {stage} AS {table.source}", f"TRUNCATE TABLE {stage}"):
                for _ in ch.query(sql, {}):
                    pass
            staged.append(stage)
            for d, (count, _, _, _) in days.items():
                if not count:
                    continue
                rel = _ch_file(table.name, d, m["encrypted"])
                with _read_file(os.path.join(bundle, rel), identity) as (gz, digest):
                    ch.insert(f"INSERT INTO {stage} FORMAT Native", iter(lambda: gz.read(1 << 16), b""))
                    while gz.read(1 << 16):
                        pass
                if digest.hexdigest() != m["files"][rel]["plain_sha256"]:
                    raise ExportError(f"{table.name} {d}: the stream inserted is not the one the manifest names")
            foreign = _foreign_rows(ch, table, stage, m["org"])
            if foreign:
                raise ExportError(f"{table.name}: {foreign} staged rows are not {m['org']}'s; the target is unchanged")
            got = _ch_days(ch, _CHTable(table.name, stage, table.date, table.keys, "", {}),
                           m["clickhouse_columns"][table.name])
            want = {d: v for d, v in days.items() if v[0]}
            if got != want:
                raise ExportError(f"{table.name}: the staged rows differ from the bundle; the target is unchanged")
        carried = [(t, sorted(m["clickhouse"][t.name]["days"].items())) for t in tables]
        # From the first delete until the last insert, a failure, a timeout or a Ctrl-C leaves days empty in the
        # target: the staging tables stay, and every day not yet inserted is named (sec_keeper, round 3). A retry
        # replaces each day again, so it is safe to run as often as needed.
        remaining = [(t.name, d) for t, days in carried for d, v in days if v[0]]
        keep_stage[0] = True
        try:
            for table, days in reversed(carried):  # hook rows go before the transcript rows their scope reads
                for d, _ in days:
                    delete = (f"ALTER TABLE {table.source} DELETE{table.where(f'{table.date} = {{d:Date}}')} "
                              "SETTINGS mutations_sync = 2")
                    for _ in ch.query(delete, dict(table.params, d=d)):
                        pass
            for table, days in carried:  # transcripts before the hooks their scope reads; each row by its table's rule
                stage = table.source + "__import"
                scoped = _CHTable(table.name, stage, table.date, table.keys, table.scope, table.params)
                for d, (count, _, _, _) in days:
                    if not count:
                        continue
                    insert = f"INSERT INTO {table.source} SELECT * FROM {stage}{scoped.where(f'{table.date} = {{d:Date}}')}"
                    for attempt in (1, 2):  # once more from the kept staging table before giving up
                        try:
                            for _ in ch.query(insert, dict(table.params, d=d)):
                                pass
                            break
                        except Exception:
                            if attempt == 2:
                                raise
                    remaining.remove((table.name, d))
        except BaseException:
            shown = ", ".join(f"{n} {d}" for n, d in remaining[:8]) + (" …" if len(remaining) > 8 else "")
            raise ExportError(f"the swap stopped: {len(remaining)} days are not inserted yet ({shown or 'none'}); "
                              "the staging tables are kept: run the import again") from None
        keep_stage[0] = False
    except ExportError:
        raise
    except Exception:
        raise ExportError("ClickHouse import failed; run the import again, it replaces each day") from None
    finally:
        for stage in ([] if keep_stage[0] else staged):
            try:
                for _ in ch.query(f"DROP TABLE IF EXISTS {stage}", {}):
                    pass
            except Exception:
                pass


def delete_bundle(bundle, index_sha):
    """Remove the bundle once its import checked out (the user organization's sec_keeper, through of_architect,
    message 01a1f5ff-f009: nothing left in clear, and the deletion read back). Only a bundle whose index.json has the
    sha256 the export printed is removed, and only the files its index names: any other file refuses it, with nothing
    removed. Returns the number of files removed after reading back that the folder is gone."""
    if os.path.islink(bundle.rstrip("/")):
        raise ExportError("the bundle's path is a symbolic link; nothing removed")
    for folder, dirs, names in os.walk(bundle):  # a link inside it: refused before any file goes (sec_keeper)
        if any(os.path.islink(os.path.join(folder, n)) for n in dirs + names):
            raise ExportError("the bundle holds a symbolic link; nothing removed")
    ix = load_index(bundle, index_sha)
    named = set(ix["files"]) | {"index.json"}
    found = set()
    for folder, _, names in os.walk(bundle):
        found |= {os.path.relpath(os.path.join(folder, n), bundle) for n in names}
    if found - named:
        raise ExportError(f"the bundle holds {len(found - named)} files its index does not name; nothing removed")
    for rel in sorted(found - {"index.json"}) + ["index.json"]:  # the index last: a partial removal stays unimportable
        os.unlink(os.path.join(bundle, rel))
    for folder, _, _ in sorted(os.walk(bundle), key=lambda w: -len(w[0])):
        os.rmdir(folder)
    if os.path.exists(bundle):
        raise ExportError("the bundle's folder is still there after its removal")
    return len(found)
