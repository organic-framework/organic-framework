"""of ship: send new rows of the local store (of.db) onward in batches.

Modes (~/.config/of/ship.yaml `mode`):
- `server` (default): POST /v1/batches to of-server (docs/server.md). The local cursor
  advances on 202; receipts are kept so local raw can be deleted once `committed`.
- `direct`: route and insert into ClickHouse from this machine (the pre-server path,
  kept for tests and as a fallback on the tenant's own machine).

One shipper at a time per machine (a non-blocking lock).
"""

import fcntl
import json
import os

import of_coord
import of_events
import of_store
from of_route import (ClickHouse, Router, fanout, published_copy, route_org,  # noqa: F401  (re-exported for tests)
                      shape_event, shape_hook, shape_payload, shape_transcript)

DEFAULTS = {
    "mode": "server",
    "server_url": "http://127.0.0.1:18200",
    "url": "http://127.0.0.1:18123",
    "user": "of",
    "password_file": "~/.config/of/clickhouse/of.password",
    "unassigned_database": "of_unassigned",
    "vault_database": "of_vault",      # tenant-only: unmasked raw records
    "raw_to_org": False,               # until boundary masking exists (RFC 0002), raw stays in the vault
    "tenant": "default",               # each machine sets its own in ship.yaml
    "batch_rows": 2000,
    "batch_bytes": 32 * 1024 * 1024,
}


def config():
    cfg = dict(DEFAULTS)
    path = of_events.config_path("ship.yaml")
    if os.path.exists(path):
        import yaml
        with open(path) as fh:
            cfg.update(yaml.safe_load(fh) or {})
    return cfg


# ------------------------------------------------------------ wire records

def event_data(r, cfg, host):
    rec = json.loads(r["record"])
    return {"tenant_id": cfg["tenant"], "host_id": host, "event_id": r["event_id"], "org": r["org"],
            "ts": r["ts"], "name": r["name"], "session_id": r["session_id"] or "", "position": r["position"] or "",
            "source": r["source"] or "", "harness_name": r["harness_name"] or "",
            "harness_version": r["harness_version"] or "", "model": r["model"] or "",
            "payload": r["payload"] or "", "raw_ref": r["raw_ref"] or "",
            "record": json.dumps(rec, ensure_ascii=False)}


def transcript_data(r, cfg, host, conn):
    raw = of_store.get_blob(conn, r["blob_sha256"]) or b""
    return {"tenant_id": cfg["tenant"], "host_id": host,
            "session_id": r["session_id"] or "", "record_uuid": r["record_uuid"], "parent_uuid": r["parent_uuid"] or "",
            "type": r["type"] or "", "ts": r["timestamp"] or "1970-01-01T00:00:00Z", "model": r["model"] or "",
            "tool_names": r["tool_names"] or "", "input_tokens": r["input_tokens"] or 0,
            "output_tokens": r["output_tokens"] or 0, "cache_read_tokens": r["cache_read_tokens"] or 0,
            "cache_write_tokens": r["cache_write_tokens"] or 0, "harness": r["harness"] or "",
            "source_path": r["source_path"], "line_offset": r["line_offset"], "sha256": r["blob_sha256"],
            "raw": raw.decode("utf-8", "replace")}


def hook_data(r, cfg, host, conn):
    raw = of_store.get_blob(conn, r["blob_sha256"]) or b""
    return {"tenant_id": cfg["tenant"], "host_id": host, "local_id": r["id"], "received_at": r["received_at"],
            "hook_event": r["hook_event"] or "", "session_id": r["session_id"] or "", "sha256": r["blob_sha256"],
            "raw": raw.decode("utf-8", "replace")}


TABLES = [("event", "rowid"), ("transcript_record", "id"), ("hook_record", "id")]


def _batches(conn, cfg, host):
    """Yield (table, last_key, [data]) chunks of rows not yet shipped."""
    for table, key in TABLES:
        cur = conn.execute("SELECT last_rowid FROM ship_cursor WHERE tbl = ?", (table,)).fetchone()
        last = cur[0] if cur else 0
        while True:
            rows = conn.execute(f"SELECT {key} AS _k, * FROM {table} WHERE {key} > ? ORDER BY {key} LIMIT ?",
                                (last, cfg["batch_rows"])).fetchall()
            if not rows:
                break
            batch, size = [], 0
            for r in rows:
                if table == "event" and (r["raw_ref"] or "").startswith(of_coord.RAW_REF):
                    last = r["_k"]  # another machine's event from the coordination feed: its own machine ships it
                    continue
                if table == "event":
                    d = event_data(r, cfg, host)
                elif table == "transcript_record":
                    d = transcript_data(r, cfg, host, conn)
                else:
                    d = hook_data(r, cfg, host, conn)
                batch.append(d)
                size += len(d.get("raw", "")) + len(d.get("record", ""))
                last = r["_k"]
                if size >= cfg["batch_bytes"]:
                    break
            yield table, last, batch


def ship(log=print, cfg=None):
    cfg = cfg or config()
    lock_path = os.path.join(of_events.STATE_DIR, "ship.lock")
    with open(os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("another shipper is running")
            return 0
        conn = of_store.connect()
        conn.row_factory = __import__("sqlite3").Row
        try:
            return _ship_server(conn, cfg, log) if cfg["mode"] == "server" else _ship_direct(conn, cfg, log)
        finally:
            conn.close()


def _advance(conn, table, last):
    conn.execute("INSERT OR REPLACE INTO ship_cursor VALUES (?, ?)", (table, last))


def _ship_direct(conn, cfg, log):
    ch = ClickHouse(cfg)
    router = Router(cfg)
    host = of_events.machine_id()
    totals = {}
    for table, last, batch in _batches(conn, cfg, host):
        for d in batch:
            d["org_id"] = route_org(table, d, router) or ""
        for database, rows in fanout(table, batch, router, cfg).items():
            ch.insert(database, table, rows)
        _advance(conn, table, last)
        totals[table] = totals.get(table, 0) + len(batch)
    log("shipped (direct) " + (", ".join(f"{t} {n}" for t, n in totals.items()) or "nothing new"))
    return 0


def org_servers(cfg, orgs_file=None):
    """{org: server url} from orgs.yaml `server:`; organizations without one use the local server."""
    import yaml
    orgs_file = orgs_file or os.environ.get("OF_ORGS") or of_events.config_path("orgs.yaml")
    try:
        with open(orgs_file) as fh:
            doc = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        doc = {}
    return {o["name"]: (o.get("server") or cfg["server_url"]).rstrip("/") for o in doc.get("orgs") or [] if o.get("name")}


def raw_consent(orgs_file=None):
    """{org: True|False}: may this machine send the org's raw tier? An organization on another
    server (orgs.yaml `server:`, joined with `of enroll`) only with `raw: true`, the consent given at
    enrolment or with `of consent --raw on`; an organization on this machine's own server (no
    `server:`) unless `raw: false`. Unknown organizations: see raw_allowed."""
    import yaml
    orgs_file = orgs_file or os.environ.get("OF_ORGS") or of_events.config_path("orgs.yaml")
    try:
        with open(orgs_file) as fh:
            doc = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        doc = {}
    out = {}
    for o in doc.get("orgs") or []:
        if o.get("name"):
            out[o["name"]] = (o.get("raw") is True) if o.get("server") else (o.get("raw") is not False)
    return out


def raw_allowed(org, consent):
    """Raw leaves this machine only with consent; an organization not listed gets nothing raw."""
    return bool(consent.get(org or "", False)) if consent is not None else True


def withhold_raw(d):
    """The record without its raw content (typed-only consent): metadata stays, the content stays here."""
    return dict(d, raw="", raw_withheld=True)


def plan_wire(table, batch, router, servers, default, consent=None):
    """Group one table's records by destination server (RFC 0001 v3 §5.1: routing on the client).
    Content goes to its source organization's server only. When the builder's organization
    (shape) or a publication's receiving organization lives on ANOTHER server, the reduced copy
    is sent there, marked `copy`; on the same server, that server makes the copies itself."""
    out = {}
    for d in batch:
        org = route_org(table, d, router)
        d = dict(d, org_id=org or "")
        if table in ("transcript_record", "hook_record") and not raw_allowed(org, consent):
            d = withhold_raw(d)  # lab-profile2-v0 F1: never rely on the server to drop it
        home = servers.get(org or "", default)
        out.setdefault(home, []).append({"type": table, "data": d})
        if table == "event" and d.get("name") == "of.published":
            p = json.loads(d.get("payload") or "{}")
            to = p.get("to_org")
            if to and servers.get(to, default) != home:
                out.setdefault(servers.get(to, default), []).append(
                    {"type": table, "data": published_copy(d, p), "copy": "published"})
            continue
        own = None
        if table == "event":
            rec = json.loads(d.get("record") or "{}")
            own = router.builder_of(d["org_id"], d.get("session_id"), rec.get("of.org") if rec.get("of.serves") else None)
            maker = shape_event
        elif table == "transcript_record":
            own, maker = router.builder_of(d["org_id"], d.get("session_id")), shape_transcript
        else:
            own, maker = router.builder_of(d["org_id"], d.get("session_id")), shape_hook
        if own and servers.get(own, default) != home:
            out.setdefault(servers.get(own, default), []).append({"type": table, "data": maker(d, own), "copy": "shape"})
    return out


def _ship_server(conn, cfg, log):
    import of_client
    host = of_events.machine_id()
    default = cfg["server_url"].rstrip("/")
    servers = org_servers(cfg)
    router = Router(cfg)
    consent = raw_consent()
    clients = {}
    conn.execute("CREATE TABLE IF NOT EXISTS ship_receipt (batch_id TEXT PRIMARY KEY, tbl TEXT, last_key INTEGER, "
                 "state TEXT, accepted INTEGER, rejected INTEGER, created_at TEXT)")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ship_receipt)")]
    if "server" not in cols:
        conn.execute("ALTER TABLE ship_receipt ADD COLUMN server TEXT")
    totals, rejected = {}, 0
    for table, last, batch in _batches(conn, cfg, host):
        wire = plan_wire(table, batch, router, servers, default, consent)
        for server, records in wire.items():  # all servers must accept before the cursor moves
            client = clients.setdefault(server, of_client.Client(server))
            receipt = client.send_batch(records)
            conn.execute("INSERT OR REPLACE INTO ship_receipt (batch_id, tbl, last_key, state, accepted, rejected, "
                         "created_at, server) VALUES (?,?,?,?,?,?,?,?)",
                         (receipt["batch_id"], table, last, receipt["state"], receipt["accepted"],
                          len(receipt.get("rejected") or []), of_events.now_iso(), server))
            for rj in receipt.get("rejected") or []:
                log(f"rejected {table}[{rj['index']}] at {server}: {rj['reason']}")
            rejected += len(receipt.get("rejected") or [])
            totals[table] = totals.get(table, 0) + receipt["accepted"]
        _advance(conn, table, last)
    committed = sum(sync_receipts(conn, c) for c in (clients.values() or [of_client.Client(default)]))
    log("shipped " + (", ".join(f"{t} {n}" for t, n in totals.items()) or "nothing new")
        + (f"; {rejected} rejected" if rejected else "") + (f"; {committed} batches committed" if committed else ""))
    return 0


def sync_receipts(conn, client):
    """Mark local receipts `committed` once the server has written them (the basis for
    deleting local raw: ship -> confirm -> delete)."""
    srv = client.url
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ship_receipt)")]
    where = "state != 'committed'" + (" AND (server = ? OR server IS NULL)" if "server" in cols else "")
    pending = {r[0] for r in conn.execute(f"SELECT batch_id FROM ship_receipt WHERE {where}",
                                          (srv,) if "server" in cols else ())}
    if not pending:
        return 0
    key = "receipt_seq" if srv.startswith("http://127.0.0.1") else f"receipt_seq:{srv}"
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    after, n = int(row[0]) if row else 0, 0
    while True:
        rs = client.receipts(after)["receipts"]
        if not rs:
            break
        for r in rs:
            if r["batch_id"] in pending and r["state"] in ("committed", "dead"):
                conn.execute("UPDATE ship_receipt SET state = ? WHERE batch_id = ?", (r["state"], r["batch_id"]))
                n += r["state"] == "committed"
            if r["state"] in ("committed", "dead") and all(x["state"] in ("committed", "dead") for x in rs if x["seq"] <= r["seq"]):
                after = r["seq"]
        if len(rs) < 1000:
            break
    conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, str(after)))
    return n
