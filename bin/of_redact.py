"""`of redact`: remove a secret from every stored copy (accepted by of_architect, 2026-11-06).

Locally, each selected row's blob passes through the ingest rules again (of_store.redact_keys: private keys,
credentials in connection strings and environment dumps, Kubernetes Secret values); the row is repointed to
the masked blob, the old blob is deleted when no row refers to it any more, and of.redacted is written with
the row ids, the two shas and the reason, never content. Rows already shipped are replaced on every server
that holds them: POST /v1/redactions (docs/server.md, Redaction) with the masked copies, made exactly as a
batch would make them (of_ship.plan_wire), grouped by session, at most 10,000 rows per request; the
redaction_id is derived from the request, so a retry never doubles it.
"""

import json
import os
import uuid

import of_events
import of_store

NS = uuid.UUID("6f1b8c2e-4b1d-4c39-9a51-0f3d6c2b7e10")
MAX_IDS = 10000
MAX_BYTES = 32 * 1024 * 1024  # the server takes 64 MB per request (of_server max_body); half, to be safe


def select(conn, sessions=(), shas=(), ids=(), table="transcript_record"):
    """Row ids of `table` for the given sessions (or session id prefixes), blob shas or ids."""
    out = set(ids)
    for s in sessions:
        out |= {r[0] for r in conn.execute(f"SELECT id FROM {table} WHERE session_id LIKE ?", (s + "%",))}
    for sha in shas:
        out |= {r[0] for r in conn.execute(f"SELECT id FROM {table} WHERE blob_sha256 = ?", (sha,))}
    return sorted(out)


def would_change(conn, row_ids, table="transcript_record", fn=None):
    fn = fn or of_store.redact_keys
    out = []
    for rid in row_ids:
        row = conn.execute(f"SELECT blob_sha256 FROM {table} WHERE id = ?", (rid,)).fetchone()
        if row:
            b = of_store.get_blob(conn, row[0]) or b""
            if fn(b) != b:
                out.append(rid)
    return out


def _shipped(conn, table):
    row = conn.execute("SELECT last_rowid FROM ship_cursor WHERE tbl = ?", (table,)).fetchone()
    return row[0] if row else 0


def requests(conn, table, changed, reason, cfg, host):
    """[(server, body)] for the rows of `changed` [(id, sha_old, sha_new)] that were shipped: one request per
    server and up to 10,000 rows. transcript_record rows go as [session_id, record_uuid] pairs, so a sweep
    across hundreds of sessions is a few mutations, not hundreds (of_server 68bcc88)."""
    import sqlite3
    import of_ship
    shipped = _shipped(conn, table)
    servers, default = of_ship.org_servers(cfg), cfg["server_url"].rstrip("/")
    # A replacement always carries its masked raw: the server already holds this row's content wherever it put
    # it (the vault keeps raw for every row it got, routed or not), and the server places the replacement where
    # its row is (of_server 00b7a14). Without raw it would empty the stored copy, so it refuses one (4aa01f5).
    router, consent = of_ship.Router(cfg), None
    groups = {}
    for rid, old, new in changed:
        if rid > shipped:
            continue  # never shipped: the masked row ships as usual
        conn.row_factory = sqlite3.Row
        try:
            r = conn.execute(f"SELECT * FROM {table} WHERE {'rowid' if table == 'event' else 'id'} = ?",
                             (rid,)).fetchone()
        finally:
            conn.row_factory = None
        if r is None:
            continue
        if table == "event":
            d, key = of_ship.event_data(r, cfg, host), r["event_id"]
        elif table == "transcript_record":
            d = of_ship.transcript_data(r, cfg, host, conn)
            key = [r["session_id"] or "", r["record_uuid"]]
        else:
            d = of_ship.hook_data(r, cfg, host, conn)
            key = r["id"]
        for server, records in of_ship.plan_wire(table, [d], router, servers, default, consent).items():
            g = groups.setdefault(server, {"ids": [], "records": [], "old": [], "new": []})
            g["ids"].append(key)
            g["records"] += records
            g["old"].append(old or "")
            g["new"].append(new)
    out = []
    for server, g in sorted(groups.items()):
        by_key = {}
        for rec in g["records"]:
            by_key.setdefault(json.dumps(_key(table, rec)), []).append(rec)
        chunks, cur, size = [], [], 0  # at most MAX_IDS rows and MAX_BYTES of records per request
        for k, (key, old, new) in enumerate(zip(g["ids"], g["old"], g["new"])):
            n = sum(len(json.dumps(r)) for r in by_key.get(json.dumps(key), []))
            if cur and (len(cur) >= MAX_IDS or size + n > MAX_BYTES):
                chunks.append(cur)
                cur, size = [], 0
            cur.append((key, old, new))
            size += n
        if cur:
            chunks.append(cur)
        for part in chunks:
            ids = [k for k, _, _ in part]
            body = {"table": table, "ids": ids, "reason": reason,
                    "sha_old": ",".join(o[:16] for _, o, _ in part),
                    "sha_new": ",".join(n_[:16] for _, _, n_ in part),
                    "records": [rec for k in ids for rec in by_key.get(json.dumps(k), [])]}
            body["redaction_id"] = str(uuid.uuid5(NS, json.dumps([table, ids, body["sha_new"]], sort_keys=True)))
            if table == "hook_record":
                body["host_id"] = host
            out.append((server, body))
    return out


def _key(table, rec):
    d = rec.get("data") or {}
    if table == "event":
        return d.get("event_id")
    return [d.get("session_id") or "", d.get("record_uuid")] if table == "transcript_record" else d.get("local_id")


def redact_events(conn, rowids, fn=None):
    """Re-mask stored events (their payload and record) with the ingest rules, one short write transaction per
    event (a founder's prompt held a capability URL: a user organization's sec_keeper, 2026-11-11). Returns
    [(rowid, old sha, new sha)] of the record text for the events that changed."""
    import hashlib
    fn = fn or of_store.redact_keys
    changed = []
    for rowid in rowids:
        row = conn.execute("SELECT payload, record FROM event WHERE rowid = ?", (rowid,)).fetchone()
        if not row:
            continue
        payload, record = row[0] or "", row[1] or ""
        new_payload, new_record = fn(payload.encode()).decode(), fn(record.encode()).decode()
        if (new_payload, new_record) == (payload, record):
            continue
        with of_store.write_tx(conn):
            cur = conn.execute("SELECT record FROM event WHERE rowid = ?", (rowid,)).fetchone()
            if not cur or cur[0] != record:
                continue  # changed meanwhile
            conn.execute("UPDATE event SET payload = ?, record = ? WHERE rowid = ?", (new_payload, new_record, rowid))
        changed.append((rowid, hashlib.sha256(record.encode()).hexdigest(), hashlib.sha256(new_record.encode()).hexdigest()))
    return changed


def wait_done(client, redaction_id, timeout_s=1800, poll_s=5, sleep=None):
    """GET /v1/redactions/{id} until done or failed (one request at a time on the Mac: of_server)."""
    import time
    sleep = sleep or time.sleep
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        st = (client.redaction(redaction_id) or {}).get("state")
        if st in ("done", "failed"):
            return st
        sleep(poll_s)
    return "timeout"


def run(conn, reason, transcript_ids=(), hook_ids=(), send_to_servers=True, client_for=None, log=print, wait=True,
        event_ids=()):
    """Redact the rows locally, record of.redacted, and replace the shipped ones on their servers. event_ids are
    rowids of the event table. Returns {table: {"local": n, "requests": [(server, redaction_id, state)]}}."""
    import of_ship
    cfg = of_ship.config()
    host = of_events.machine_id()
    summary = {}
    for table, ids in (("transcript_record", transcript_ids), ("hook_record", hook_ids), ("event", event_ids)):
        if not ids:
            continue
        changed = (redact_events(conn, list(ids)) if table == "event"
                   else of_store.redact_rows(conn, list(ids), reason, table=table))
        summary[table] = {"local": len(changed), "requests": []}
        if not changed:
            continue
        of_events.emit("redacted", {"table": table, "ids": [r for r, _, _ in changed],
                                    "shas": [[o[:16], n[:16]] for _, o, n in changed], "count": len(changed),
                                    "reason": reason, "scope": "local"})
        if not send_to_servers:
            continue
        summary[table]["requests"] += send(requests(conn, table, changed, reason, cfg, host), client_for, log, wait)
    return summary


def send(reqs, client_for=None, log=print, wait=True):
    """POST each request, one at a time; with wait, the next goes only when the previous is done."""
    out = []
    for server, body in reqs:
        try:
            client = (client_for or _client)(server)
            r = client.redact(body)
            state = (r or {}).get("state")
            if wait and state not in ("done", "failed"):
                state = wait_done(client, body["redaction_id"])
        except Exception as e:  # noqa: BLE001 - reported; the local copy is masked either way
            state = f"failed: {type(e).__name__}: {str(e)[:160]}"
        out.append((server, body["redaction_id"], len(body["ids"]), state))
        log(f"{body['table']}: {len(body['ids'])} row(s) -> {server}: {state}")
    return out


def resend(conn, reason, transcript_ids=(), hook_ids=(), client_for=None, log=print):
    """Replace on the servers rows already redacted here (a local-only run): the current, masked row is sent."""
    import of_ship
    cfg, host = of_ship.config(), of_events.machine_id()
    out = {}
    for table, ids in (("transcript_record", transcript_ids), ("hook_record", hook_ids)):
        rows = [(rid, "", conn.execute(f"SELECT blob_sha256 FROM {table} WHERE id = ?", (rid,)).fetchone()[0])
                for rid in ids if conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (rid,)).fetchone()]
        if rows:
            out[table] = send(requests(conn, table, rows, reason, cfg, host), client_for, log)
    return out


def _client(server):
    import of_client
    return of_client.Client(server)


# ------------------------------------------------------------------ the harness's own transcripts (option A)

def transcript_files(roots=None):
    """Claude Code transcripts (main and subagents) under every config dir on this machine."""
    import glob
    import os
    roots = roots or glob.glob(os.path.expanduser("~/.claude*/projects"))
    out = []
    for r in roots:
        out += glob.glob(os.path.join(r, "*", "*.jsonl")) + glob.glob(os.path.join(r, "*", "*", "subagents", "*.jsonl"))
    return sorted(out)


def redact_transcripts(files, apply=False, min_idle_s=600, live_sessions=(), fn=None, log=print):
    """The founder's option A (of_architect, 2026-11-06), prepared, run only on his yes: mask secrets in
    the harness's own transcript files with the ingest rules. Default is a dry run (counts only). A file
    is rewritten IN PLACE (same inode: a running Claude Code appends through an open descriptor, and a
    replaced file would lose those writes), and only when it has not changed for min_idle_s and its
    session is not live; otherwise it is reported as skipped. Never prints a value.
    Returns {"files": n, "changed": n, "lines": n, "skipped_live": [...], "skipped_recent": [...]}."""
    import os
    import time
    fn = fn or of_store.redact_keys
    rep = {"files": 0, "changed": 0, "lines": 0, "skipped_live": [], "skipped_recent": [], "credentials": {}}
    for path in files:
        rep["files"] += 1
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        lines = data.split(b"\n")
        new = [fn(ln) if ln else ln for ln in lines]
        n = sum(1 for a, b in zip(lines, new) if a != b)
        if not n:
            continue
        for a, b in zip(lines, new):
            if a != b:
                for kind, name, fp in credentials_in(a):
                    rep["credentials"].setdefault(f"{kind}:{name}", set()).add(fp)
        sid = os.path.basename(path)[:-6]
        parent = os.path.basename(os.path.dirname(os.path.dirname(path))) if "/subagents/" in path else sid
        if parent in live_sessions or sid in live_sessions:
            rep["skipped_live"].append(path)
            continue
        if time.time() - os.path.getmtime(path) < min_idle_s:
            rep["skipped_recent"].append(path)
            continue
        rep["changed"] += 1
        rep["lines"] += n
        if apply:
            backup(path)  # byte for byte, before the rewrite (of_architect): kept 24 h, then purged
            st = os.stat(path)
            with open(path, "r+b") as fh:  # same inode; nothing else writes an idle, not-live session's file
                if os.fstat(fh.fileno()).st_size != st.st_size:
                    rep["skipped_recent"].append(path)  # it grew meanwhile: leave it for the next run
                    continue
                fh.seek(0)
                fh.write(b"\n".join(new))
                fh.truncate()
            os.utime(path, (st.st_atime, st.st_mtime))
    log(f"{'applied' if apply else 'dry run'}: {rep['changed']} of {rep['files']} files, {rep['lines']} lines; "
        f"skipped {len(rep['skipped_live'])} live, {len(rep['skipped_recent'])} changed in the last {min_idle_s // 60} min")
    return rep


BACKUP_KEEP_S = 24 * 3600


def backup_dir():
    import os
    d = os.path.join(of_events.STATE_DIR, "redact-backup")
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def backup(path):
    """A byte-for-byte copy of a transcript before option A rewrites it (mode 600 in a 700 directory). It
    holds the secrets itself, so purge_backups deletes it after BACKUP_KEEP_S."""
    import hashlib
    import os
    import shutil
    import time
    day = os.path.join(backup_dir(), time.strftime("%Y%m%dT%H%M%S"))
    os.makedirs(day, mode=0o700, exist_ok=True)
    name = hashlib.sha256(path.encode()).hexdigest()[:16] + "-" + os.path.basename(path)
    dst = os.path.join(day, name)
    shutil.copyfile(path, dst)
    os.chmod(dst, 0o600)
    with open(os.path.join(day, "manifest.tsv"), "a") as fh:
        fh.write(f"{name}\t{path}\n")
    return dst


def purge_backups(now=None, keep_s=BACKUP_KEEP_S):
    """Delete option A's backups older than keep_s (the listener runs this). Returns the directories removed."""
    import os
    import shutil
    import time
    root = os.path.join(of_events.STATE_DIR, "redact-backup")
    if not os.path.isdir(root):
        return []
    gone = []
    for d in os.listdir(root):
        p = os.path.join(root, d)
        if os.path.isdir(p) and (now or time.time()) - os.path.getmtime(p) > keep_s:
            shutil.rmtree(p, ignore_errors=True)
            gone.append(d)
    return gone


def credentials_in(line):
    """(kind, name, sha256[:12]) of each credential the rules mask in a line; never the value."""
    import hashlib
    import re
    out = []
    for m in of_store._DSN_PW.finditer(line):
        if m.group(2)[:1] not in (b"$", b"*", b"{", b"["):
            out.append(("dsn", m.group(1).split(b"//")[-1].rstrip(b":").decode("utf-8", "replace")[:60],
                        hashlib.sha256(m.group(2)).hexdigest()[:12]))
    for m in of_store._ENV_SECRET.finditer(line):
        out.append(("env", m.group(1).decode()[:-1][:60], hashlib.sha256(m.group(3)).hexdigest()[:12]))
    if b"Secret" in line and of_store._K8S_SECRET.search(line):
        for v in of_store._B64_VALUE.findall(line):
            out.append(("k8s_secret", "data", hashlib.sha256(v).hexdigest()[:12]))
    for m in re.finditer(rb"-----BEGIN ([A-Z0-9 ]*)PRIVATE KEY-----", line):
        out.append(("pem", m.group(1).decode().strip() or "PKCS8", hashlib.sha256(line[m.start():m.start() + 400]).hexdigest()[:12]))
    return out


# ------------------------------------------------------------------ verify: placeholders (2026-11-06)

PLACEHOLDERS = ["password", "PASSWORD", "Password", "secret", "SECRET", "Secret", "admin", "ADMIN", "[REDACTED]",
                "<REDACTED>", "REDACTED", "redacted", "<PASSWORD>", "<password>", "<SECRET>", "<secret>", "****", "***",
                "**", "*", "*****", "******", "********", "x", "xx", "xxx", "xxxx", "xxxxx", "xxxxxx", "xxxxxxxx", "XXXX",
                "XXXXX", "XXXXXX", "XXXXXXXX", "...", "\u2026", "<set>", "<SET>", "set", "dummy", "changeme", "change_me",
                "CHANGEME", "test", "testing", "example", "example123", "your_api_key_here", "your-api-key", "<YOUR_KEY>",
                "<token>", "<TOKEN>", "token", "TOKEN", "$TOKEN", "${TOKEN}", "$PGPASSWORD", "${PGPASSWORD}", "$PASSWORD",
                "${PASSWORD}", "$SECRET", "$ROLE", "$DB_PASSWORD", "pass", "pass123", "password123", "secret123", "a", "p",
                "pw", "pwd", "<pw>", "<pass>", "<PASS>", "PASS", "hunter2", "foo", "bar", "baz", "sk-...", "sk-xxx", "0x...",
                "0x", "<key>", "<KEY>", "KEY", "key", "private_key", "PRIVATE_KEY", "<PRIVATE_KEY>", "0xYOUR_PRIVATE_KEY",
                "<hidden>", "hidden", "HIDDEN", "masked", "<masked>", "MASKED", "<redacted>", "[redacted]", "[masked]",
                "[hidden]", "none", "None", "null", "NULL", "empty", "***REDACTED***", "<REDACT>", "REPLACE_ME",
                "replace_me", "<secret-value>", "<value>", "value", "VALUE", "?", "secret1", "<your-password>",
                "<password-here>", "***masked***", "%s", "$1", "$2", "xoxb-", "xapp-", "ghp_", "sntrys_", "unset",
                "your_password", "your-password", "YourSecurePassword123!", "SecurePassword123!", "\u0432\u0430\u0448_password",
                "\u0432\u0430\u0448_\u043f\u0430\u0440\u043e\u043b\u044c", "", " ", "''", '""', "<from 1Password>", "op://",
                "${{ secrets.TOKEN }}"]
WEAK_IF_NOT_LOCAL = ["postgres", "clickhouse", "default", "fixture", "s3cr3t"]
# A machine adds the weak words its organizations' passwords were found to use in ~/.config/of/redact.yaml, as
# `weak_fingerprints: [<_fp(word)>, …]`, so the word itself is kept nowhere.
REDACT_CONFIG = os.path.expanduser("~/.config/of/redact.yaml")


def _fp(text):
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()[:12]


PLACEHOLDER_FPS = {_fp(w) for w in PLACEHOLDERS}
WEAK_FPS = {_fp(w): w for w in WEAK_IF_NOT_LOCAL}


def weak_fps(path=None):
    """WEAK_FPS and the machine's own weak fingerprints (REDACT_CONFIG); a missing or unreadable file adds none."""
    try:
        import yaml
        with open(path or REDACT_CONFIG) as fh:
            extra = (yaml.safe_load(fh) or {}).get("weak_fingerprints") or []
    except Exception:  # a missing file, bad YAML, no PyYAML: the built-in words only
        extra = []
    return set(WEAK_FPS) | {str(x) for x in extra}


def classify(fp, host=None, size=None):
    """'(a) placeholder', 'weak real password' (a weak word on a non-local host), '(a) weak, local', or None.
    A local host alone never makes a value (a): only placeholder or weak-word evidence does (platform_owner)."""
    if fp in PLACEHOLDER_FPS:
        return "(a) placeholder"
    if fp in weak_fps():
        local = bool(host) and str(host).split(":")[0] in ("127.0.0.1", "localhost")
        return "(a) weak, local" if local else "weak real password"
    return None
