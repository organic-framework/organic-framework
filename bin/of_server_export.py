"""`of server export` and `of server import`: one organization's records moved from one of-server to another.

Made for the OF team's server moving to the agent host and, after it, a user organization's records moving to its
own server there (2026-11-19, of_server; the founder's prompts 01a1f5e4-0343 and 01a1f5f2-8d26). This module carries
the server store's part. ClickHouse and the blob store come in their own sections of the same bundle.

A bundle is a folder (mode 0700, files 0600):
- store/<table>.jsonl.gz: the organization's rows of each table, one JSON object per line, or .jsonl.gz.age when the
  export names an age recipient: the gzip stream goes straight into `age`, with no plaintext on disk (the user
  organization's sec_keeper's condition, through of_architect, message 01a1f5ff-f009);
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

import datetime
import gzip
import hashlib
import json
import os
import re

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


def _age():
    import shutil
    path = shutil.which("age")
    if not path:
        raise ExportError("age is not installed here: the bundle is encrypted with it")
    return path


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


def _read_lines(path, identity=None):
    """The lines of the gzip file PATH, decrypted with `age -d -i IDENTITY` when it ends in .age. Yields the lines,
    then the gzip stream's sha256 as the last item (a str)."""
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
            for line in gz:
                yield line
        while src.read(1 << 16):  # whatever follows the gzip stream counts in its hash too
            pass
    finally:
        if proc:
            proc.stdout.close()
            if proc.wait() != 0:
                raise ExportError(f"age could not decrypt {os.path.basename(path)}")
        else:
            src.fh.close()
    yield src.h.hexdigest()


def _file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _belongs(table, row, org):
    """Whether ROW is ORG's by its table's own rule (STORE's predicate), read from the row itself."""
    if table == "messages":
        return org in (row.get("to_org"), row.get("from_org"))
    return row.get("org") == org


def export_store(conn, org, out, version=None, recipient=None):
    """Write the store part of ORG's bundle into the new folder OUT, each file encrypted to the age RECIPIENT when
    given. Returns (manifest, the sha256 of index.json): the operator carries that sha256 to the import by another
    path than the bundle (of_secrets-review on organic-framework PR 425: age authenticates no sender, so the bundle
    alone proves nothing about who made it)."""
    if recipient is not None and not AGE_RECIPIENT.fullmatch(recipient):
        raise ExportError("the recipient is not an age X25519 recipient (age1…)")
    if recipient:
        _age()
    if os.path.exists(out) and os.listdir(out):
        raise ExportError(f"{out} is not empty")
    _private_dir(out)
    _private_dir(os.path.join(out, "store"))
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
    # The manifest names rows (message ids, alias and position names, sightings' hmacs), so it is encrypted with the
    # rest; index.json, the one plain file, holds only the organization and each file's sha256 (PR 425, point 3).
    mrel = f"manifest.json.gz{ext}"
    written = _write_file(os.path.join(out, mrel), [json.dumps(manifest, sort_keys=True).encode()], recipient)
    index = {"format": FORMAT, "org": org, "encrypted": bool(recipient),
             "files": dict({rel: f["sha256"] for rel, f in manifest["files"].items()}, **{mrel: written["sha256"]})}
    with _open_private(os.path.join(out, "index.json")) as fh:
        fh.write(json.dumps(index, indent=1, sort_keys=True).encode() + b"\n")
    return manifest, _file_sha(os.path.join(out, "index.json"))


def load_index(bundle):
    with open(os.path.join(bundle, "index.json")) as fh:
        ix = json.load(fh)
    if ix.get("format") != FORMAT:
        raise ExportError(f"bundle format {ix.get('format')!r}; this release reads {FORMAT}")
    return ix


def load_manifest(bundle, identity=None):
    ix = load_index(bundle)
    rel = "manifest.json.gz" + (".age" if ix.get("encrypted") else "")
    raw = b"".join(x for x in _read_lines(os.path.join(bundle, rel), identity) if isinstance(x, bytes))
    m = json.loads(raw)
    if m.get("org") != ix.get("org") or m.get("format") != FORMAT:
        raise ExportError("the manifest and index.json disagree on the organization or the format")
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
    if index_sha is not None and _file_sha(os.path.join(bundle, "index.json")) != index_sha:
        return ["index.json: its sha256 is not the one the export printed"]
    ix = load_index(bundle)
    problems = [f"{rel}: sha256 differs" for rel, sha in ix["files"].items()
                if not os.path.exists(os.path.join(bundle, rel)) or _file_sha(os.path.join(bundle, rel)) != sha]
    if problems or (ix.get("encrypted") and not identity):
        return problems
    m = load_manifest(bundle, identity)
    got = {}
    for table in STORE:
        sha = []
        got[table] = summarize(table, read_bundle_rows(bundle, table, identity, sha, ix.get("encrypted")))
        rel = os.path.relpath(_store_file(bundle, table, ix.get("encrypted")), bundle)
        if sha != [m["files"][rel]["plain_sha256"]]:
            problems.append(f"{rel}: the decrypted stream's sha256 differs")
    return problems + _compare(m["store"], got, "the bundle")


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


def import_store(conn, bundle, org, index_sha, identity=None):
    """Replace ORG's rows with the bundle's, table by table, in one transaction. Refused, with nothing changed, unless
    the operator's ORG is the bundle's, the INDEX_SHA the export printed matches, the bundle checks out, every row is
    ORG's by its own table's rule, and every column of every row exists here (of_secrets-review on organic-framework
    PR 425). The store is checked against the manifest inside the transaction, which rolls back on any difference.
    A key another organization's row holds here refuses the whole import too. Returns {table: rows}."""
    if not index_sha:
        raise ExportError("give the sha256 the export printed (--index-sha): the bundle alone proves nothing")
    problems = check_bundle(bundle, identity, index_sha)
    if problems:
        raise ExportError("the bundle does not match its index: " + "; ".join(problems))
    ix = load_index(bundle)
    if ix.get("encrypted") and not identity:
        raise ExportError("the bundle is encrypted: name its key with --identity")
    m = load_manifest(bundle, identity)
    if org != m["org"]:
        raise ExportError(f"the bundle is {m['org']}'s, not {org}'s")
    if "machines" not in _tables(conn):  # not a server's store: the new server makes it at its first start
        raise ExportError("this is no server's store: start the new server once, empty, before importing")
    ensure_schema(conn)  # the tables its modules make only when they first serve
    rows = {}
    for table in STORE:  # every row read and judged before anything here changes
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        rows[table] = list(read_bundle_rows(bundle, table, identity, encrypted=ix.get("encrypted")))
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
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return counts
