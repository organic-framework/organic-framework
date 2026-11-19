"""`of server export` and `of server import`: one organization's records moved from one of-server to another.

Made for the OF team's server moving to the agent host and, after it, a user organization's records moving to its
own server there (2026-11-19, of_server; the founder's prompts 01a1f5e4-0343 and 01a1f5f2-8d26). This module carries
the server store's part. ClickHouse and the blob store come in their own sections of the same bundle.

A bundle is a folder (mode 0700, files 0600):
- store/<table>.jsonl.gz: the organization's rows of each table, one JSON object per line;
- manifest.json: no row content. It holds the format, the organization, the source's version, and for each table the
  row count, a digest of the rows in key order, and per UTC day the count and the first and last key.

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

FORMAT = 1
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


def _rows(conn, table, org):
    where, _, key_cols = STORE[table]
    cur = conn.execute(f"SELECT * FROM {table} WHERE {where} ORDER BY {', '.join(key_cols)}", {"org": org})
    names = [d[0] for d in cur.description]
    for r in cur:
        yield dict(zip(names, r))


def _private_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)


def _open_private(path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")


def _file_sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def export_store(conn, org, out, version=None):
    """Write the store part of ORG's bundle into the new folder OUT; returns the manifest."""
    if os.path.exists(out) and os.listdir(out):
        raise ExportError(f"{out} is not empty")
    _private_dir(out)
    _private_dir(os.path.join(out, "store"))
    manifest = {"format": FORMAT, "org": org, "version": version, "store": {}, "files": {}}
    conn.execute("BEGIN")  # one snapshot for every table
    try:
        for table in STORE:
            rows = list(_rows(conn, table, org))
            rel = f"store/{table}.jsonl.gz"
            path = os.path.join(out, rel)
            with _open_private(path) as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
                for r in rows:
                    gz.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")).encode() + b"\n")
            manifest["store"][table] = summarize(table, rows)
            manifest["files"][rel] = _file_sha(path)
    finally:
        conn.execute("ROLLBACK")
    with _open_private(os.path.join(out, "manifest.json")) as fh:
        fh.write(json.dumps(manifest, indent=1, sort_keys=True).encode() + b"\n")
    return manifest


def read_bundle_rows(bundle, table):
    with gzip.open(os.path.join(bundle, "store", f"{table}.jsonl.gz"), "rb") as fh:
        for line in fh:
            yield json.loads(line)


def load_manifest(bundle):
    with open(os.path.join(bundle, "manifest.json")) as fh:
        m = json.load(fh)
    if m.get("format") != FORMAT:
        raise ExportError(f"bundle format {m.get('format')!r}; this release reads {FORMAT}")
    return m


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


def check_bundle(bundle):
    """The manifest recomputed from the bundle's own files: [] when they match, else the differences."""
    m = load_manifest(bundle)
    problems = [f"{rel}: sha256 differs" for rel, sha in m["files"].items()
                if not os.path.exists(os.path.join(bundle, rel)) or _file_sha(os.path.join(bundle, rel)) != sha]
    if problems:
        return problems
    got = {}
    for table in STORE:
        got[table] = summarize(table, read_bundle_rows(bundle, table))
    return _compare(m["store"], got, "the bundle")


def check_store(conn, bundle):
    """The manifest recomputed from what CONN holds for the bundle's organization: [] when it matches."""
    m = load_manifest(bundle)
    got = {table: summarize(table, list(_rows(conn, table, m["org"]))) for table in STORE}
    return _compare(m["store"], got, "the store")


def import_store(conn, bundle):
    """Replace the bundle organization's rows with the bundle's, table by table, in one transaction, after the bundle
    checks out. A row of another organization holding the same key (a sequence number, a message id) refuses the
    whole import. Returns {table: rows}."""
    problems = check_bundle(bundle)
    if problems:
        raise ExportError("the bundle does not match its manifest: " + "; ".join(problems))
    m = load_manifest(bundle)
    org, counts = m["org"], {}
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    missing = [t for t in STORE if t not in have]
    if missing:  # the new server makes its own tables at its first start: run it once, empty, first
        raise ExportError(f"this store has no {', '.join(missing)}: start the new server once, empty, before importing")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for table, (where, _, _) in STORE.items():
            conn.execute(f"DELETE FROM {table} WHERE {where}", {"org": org})
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            n = 0
            for r in read_bundle_rows(bundle, table):
                names = [c for c in cols if c in r]
                try:
                    conn.execute(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
                                 [r[c] for c in names])
                except Exception as e:  # sqlite3.IntegrityError: a key another organization holds here
                    raise ExportError(f"{table}: a row's key is held by another organization's row here "
                                      f"({type(e).__name__}); nothing imported") from None
                n += 1
            counts[table] = n
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return counts
