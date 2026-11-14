#!/usr/bin/env python3
"""B3 of the agent host move: merge one machine's local store into another's, by id (organic-framework-build
reports/agent-server-migration-plan-2026-11-11.md §3; hand-over aba3d171 item 2). At S3 the host takes the laptop's
delta for the OF team, at S4 for a user organization, and B1's reverse brings the host's delta back.

  python3 bin/of_merge_delta.py --source SRC/of.db --target DST/of.db [--since ISO] [--dry-run] [--json]

What it merges, each by the key that names it on every machine:
- events by `event_id` (UUIDv7, or a fixed id from of_events.stable_id): the asks, hand-overs, states and every other
  record, with their full-text index;
- transcript records by (session_id, record_uuid), with --since those dated at or after it and the undated ones that
  lie after the first such record in their file; snapshots by (path, blob); session forks by session_id;
- hook and runtime records by their content (time, kind, session or pane, blob), since their row ids are per machine;
  an event's raw_ref to one of them (`hook_record:<id>`) is rewritten to the target's id;
- the blobs the merged rows name.
Blobs are hashed before they are stored, and one whose bytes are not its address is refused with the row that names
it. The delta's acts of a person (PERSON_ACTS) are listed, and --hold-person-acts keeps them out for a later run, since
the source is a store any process of its OS user can write (of_secrets-review on organic-framework PR 195).
Machine-local state stays: the ingest and ship cursors (each machine reads its own files and ships its own rows) and
`meta`. With --since, only rows at or after that time (an event's ts, a record's time); without it, every row. A
re-run adds nothing. The source is opened read-only; the target is written one table per transaction. Run it where the
target is, on a copy of the source's of.db: selecting by time reads the source's tables whole (a 20-minute delta of a
3.5-million-record store took about a minute of disk reads), which is no job for the laptop being moved off.

The projections read the log, so the delta's asks and hand-overs appear in `of asks` and `of handover list` at once.
The listener's views fold new rows as they come; `of listen rebuild` re-derives them in time order after a large merge.
ClickHouse holds the shipped copy of these rows; its merge is a separate step (the server's records are idempotent on
their ids, so re-shipping the delta adds nothing either)."""
import argparse
import hashlib
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import of_store  # noqa: E402

EVENT_COLS = ("event_id", "ts", "name", "schema", "host_id", "host_name", "session_id", "position", "org", "source",
              "harness_name", "harness_version", "config_dir", "model", "cwd", "workplace", "transcript", "payload",
              "raw_ref", "record")
TRANSCRIPT_COLS = ("source_path", "line_offset", "blob_sha256", "session_id", "record_uuid", "parent_uuid", "type",
                   "timestamp", "model", "tool_names", "input_tokens", "output_tokens", "cache_read_tokens",
                   "cache_write_tokens", "harness", "message_id", "cache_write_5m_tokens", "cache_write_1h_tokens")
# a record table whose row id is per machine: its natural key, and the time column --since reads
CONTENT_KEYED = {"hook_record": (("received_at", "hook_event", "session_id", "blob_sha256"), "received_at"),
                 "runtime_record": (("received_at", "runtime", "type", "pane_id", "blob_sha256"), "received_at")}


# What only a person does (docs/guard.md G24): rows the host takes as the owner's answers, words and approvals. The
# source is a store every process of its one OS user can write, and only grants carry a signature the target checks
# again, so the merge lists these, and --hold-person-acts keeps them out until whoever runs it has looked
# (of_secrets-review on organic-framework PR 195)
PERSON_ACTS = ("of.agent_question_answered", "of.human_prompt", "of.prompt_origin", "of.diff_approved",
               "of.handover_accepted", "of.handover_verified")


def person_act(name, payload):
    """Is this event a person's act the target would take as theirs?"""
    if name not in PERSON_ACTS:
        return False
    if name == "of.agent_question_answered":
        return payload.get("by") == "owner"
    if name == "of.human_prompt":
        return payload.get("origin") in ("human", "paste")
    return True


class MergeError(Exception):
    pass


def _source(path):
    if not os.path.isfile(path):
        raise MergeError(f"no store at {path}")
    from urllib.parse import quote
    conn = sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True)  # a ? or # in the path never reaches the URI
    if conn.execute("SELECT name FROM sqlite_master WHERE name = 'event'").fetchone() is None:
        raise MergeError(f"{path} is not an OF store (no event table)")
    return conn


def _cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def _has(conn, table):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def _blob(src, dst, sha, dry, counts):
    """Copy one blob the target lacks, after hashing its bytes: a blob whose bytes are not its address is refused, so
    the row that names it is not merged either (of_secrets-review on organic-framework PR 195: a poisoned blob would
    also keep the real one out, the blob table being INSERT OR IGNORE). True when the row may be merged."""
    if not sha or dst.execute("SELECT 1 FROM blob WHERE sha256 = ?", (sha,)).fetchone():
        return True
    try:
        data = of_store.get_blob(src, sha)
    except Exception:  # noqa: BLE001 - an unreadable blob is refused like a wrong one
        data = None
    if data is None:
        return True  # the source lacks it too: the row keeps its reference, as the source had it
    if hashlib.sha256(data).hexdigest() != sha:
        counts["blob"]["refused"] += 1
        return False
    counts["blob"]["added"] += 1
    if not dry:
        of_store.put_blob(dst, data)
    return True


def _content_keyed(src, dst, table, since, dry, counts, idmap, only_ids=None):
    """Hook and runtime records by their content; fills idmap[table] {source id: target id}."""
    key, tcol = CONTENT_KEYED[table]
    cols = ("id",) + tuple(c for c in _cols(dst, table) if c != "id")
    have = [c for c in cols if c in _cols(src, table)]
    if only_ids is not None:
        rows = [src.execute(f"SELECT {', '.join(have)} FROM {table} WHERE id = ?", (i,)).fetchone() for i in only_ids]
        rows = [r for r in rows if r]
    elif since:
        rows = src.execute(f"SELECT {', '.join(have)} FROM {table} WHERE {tcol} >= ? ORDER BY id", (since,)).fetchall()
    else:
        rows = src.execute(f"SELECT {', '.join(have)} FROM {table} ORDER BY id").fetchall()
    m = idmap.setdefault(table, {})
    for row in rows:
        d = dict(zip(have, row))
        if d["id"] in m:
            continue
        counts[table]["seen"] += 1
        where = " AND ".join(f"{k} IS ?" for k in key)
        hit = dst.execute(f"SELECT id FROM {table} WHERE {where}", tuple(d.get(k) for k in key)).fetchone()
        if hit:
            m[d["id"]] = hit[0]
            continue
        if not _blob(src, dst, d.get("blob_sha256"), dry, counts):
            counts[table]["refused"] += 1
            m[d["id"]] = None
            continue
        counts[table]["added"] += 1
        if dry:
            m[d["id"]] = None
            continue
        rest = [c for c in have if c != "id"]
        cur = dst.execute(f"INSERT INTO {table} ({', '.join(rest)}) VALUES ({', '.join('?' * len(rest))})",
                          tuple(d[c] for c in rest))
        m[d["id"]] = cur.lastrowid


def _raw_ref(src, dst, ref, since, dry, counts, idmap):
    """An event's raw_ref (`hook_record:<id>`, `runtime_record:<id>`) as the target names it, or as it was."""
    if not isinstance(ref, str) or ":" not in ref:
        return ref
    table, _, rid = ref.partition(":")
    if table not in CONTENT_KEYED or not rid.isdigit():
        return ref
    m = idmap.setdefault(table, {})
    if int(rid) not in m:  # a record before --since that a later event points at
        _content_keyed(src, dst, table, None, dry, counts, idmap, only_ids=[int(rid)])
    new = m.get(int(rid))
    return f"{table}:{new}" if new else (None if not dry else ref)


def merge(source, target, since=None, dry_run=False, hold_person_acts=False):
    """{table: {seen, added, refused}, person_acts: [...]} after merging SOURCE's store into TARGET's (paths to
    of.db). person_acts lists the delta's acts of a person (PERSON_ACTS) the target lacked, each with `held` when
    HOLD_PERSON_ACTS kept it out; a later run without it merges them."""
    if os.path.realpath(source) == os.path.realpath(target):
        raise MergeError("the source and the target are the same store")
    src = _source(source)
    dst = of_store.connect(target)  # the target's schema, migrated as every OF store is
    counts = {t: {"seen": 0, "added": 0, "refused": 0} for t in ("blob", "hook_record", "runtime_record",
                                                                  "transcript_record", "snapshot", "session_fork",
                                                                  "event")}
    acts = []
    idmap = {}

    def tx(fn):
        if dry_run:
            fn()
            return
        with of_store.write_tx(dst):
            fn()

    for table in CONTENT_KEYED:
        if _has(src, table):
            tx(lambda t=table: _content_keyed(src, dst, t, since, dry_run, counts, idmap))

    def transcripts():
        have = [c for c in TRANSCRIPT_COLS if c in _cols(src, "transcript_record")]
        q = f"SELECT {', '.join(have)} FROM transcript_record"
        if not since:
            rows = src.execute(q).fetchall()
        else:
            # 414,000 of 3.5 million records carry no timestamp (api-request-blob, last-prompt, file-history-snapshot).
            # A transcript only grows, so an undated record is in the delta when it lies in its file at or after the
            # file's first dated record at or after the cutover (by line offset), never the session's whole history
            rows = src.execute(q + " WHERE timestamp >= ?", (since,)).fetchall()
            first = {}
            for sid, path, off in src.execute("SELECT session_id, source_path, min(line_offset) FROM transcript_record "
                                              "WHERE timestamp >= ? GROUP BY session_id, source_path", (since,)):
                first[(sid, path)] = off
            for (sid, path), off in first.items():
                rows += src.execute(q + " WHERE session_id IS ? AND timestamp IS NULL AND source_path = ? AND "
                                    "line_offset >= ?", (sid, path, off)).fetchall()
        for row in rows:
            d = dict(zip(have, row))
            counts["transcript_record"]["seen"] += 1
            if dst.execute("SELECT 1 FROM transcript_record WHERE session_id IS ? AND record_uuid = ?",
                           (d.get("session_id"), d["record_uuid"])).fetchone():
                continue
            if not _blob(src, dst, d.get("blob_sha256"), dry_run, counts):
                counts["transcript_record"]["refused"] += 1
                continue
            counts["transcript_record"]["added"] += 1
            if not dry_run:
                dst.execute(f"INSERT OR IGNORE INTO transcript_record ({', '.join(have)}) VALUES "
                            f"({', '.join('?' * len(have))})", row)
    if _has(src, "transcript_record"):
        tx(transcripts)

    def snapshots():
        rows = src.execute("SELECT path, taken_at, blob_sha256 FROM snapshot" + (" WHERE taken_at >= ?" if since else ""),
                           (since,) if since else ()).fetchall()
        for path, taken, sha in rows:
            counts["snapshot"]["seen"] += 1
            if dst.execute("SELECT 1 FROM snapshot WHERE path = ? AND blob_sha256 = ?", (path, sha)).fetchone():
                continue
            if not _blob(src, dst, sha, dry_run, counts):
                counts["snapshot"]["refused"] += 1
                continue
            counts["snapshot"]["added"] += 1
            if not dry_run:
                dst.execute("INSERT OR IGNORE INTO snapshot (path, taken_at, blob_sha256) VALUES (?,?,?)",
                            (path, taken, sha))
    if _has(src, "snapshot"):
        tx(snapshots)

    def forks():
        for row in src.execute("SELECT session_id, parent_session_id, shared, method, detected_at FROM session_fork"):
            counts["session_fork"]["seen"] += 1
            if dst.execute("SELECT 1 FROM session_fork WHERE session_id = ?", (row[0],)).fetchone():
                continue
            counts["session_fork"]["added"] += 1
            if not dry_run:
                dst.execute("INSERT OR IGNORE INTO session_fork VALUES (?,?,?,?,?)", row)
    if _has(src, "session_fork") and _has(dst, "session_fork"):
        tx(forks)

    def events():
        have = [c for c in EVENT_COLS if c in _cols(src, "event")]
        rows = src.execute(f"SELECT {', '.join(have)} FROM event" + (" WHERE ts >= ?" if since else "")
                           + " ORDER BY ts, rowid", (since,) if since else ()).fetchall()
        for row in rows:
            d = dict(zip(have, row))
            counts["event"]["seen"] += 1
            if dst.execute("SELECT 1 FROM event WHERE event_id = ?", (d["event_id"],)).fetchone():
                continue
            try:
                payload = json.loads(d.get("payload") or "{}")
            except ValueError:
                payload = {}
            if person_act(d.get("name"), payload if isinstance(payload, dict) else {}):
                acts.append({"name": d["name"], "event_id": d["event_id"], "ts": d.get("ts"),
                             "position": d.get("position"), "host_id": d.get("host_id"),
                             "via": payload.get("via"), "origin": payload.get("origin"),
                             "ask_id": payload.get("ask_id") or payload.get("id"), "held": bool(hold_person_acts)})
                if hold_person_acts:
                    counts["event"]["held"] = counts["event"].get("held", 0) + 1
                    continue
            counts["event"]["added"] += 1
            if dry_run:
                continue
            d["raw_ref"] = _raw_ref(src, dst, d.get("raw_ref"), since, dry_run, counts, idmap)
            cols = [c for c in EVENT_COLS if c in d]
            cur = dst.execute(f"INSERT OR IGNORE INTO event ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                              tuple(d[c] for c in cols))
            if cur.rowcount and d.get("payload"):
                try:
                    text = "\n".join(of_store._texts(json.loads(d["payload"]), []))
                except ValueError:
                    text = ""
                if text:
                    dst.execute("INSERT INTO event_fts VALUES (?, ?)", (d["event_id"], text))
    tx(events)
    src.close()
    dst.close()
    counts["person_acts"] = acts
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(prog="of_merge_delta", description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", required=True, help="the store to read: another machine's of.db (opened read-only)")
    ap.add_argument("--target", required=True, help="the store to merge into: this machine's of.db")
    ap.add_argument("--since", help="only rows at or after this time (ISO, as the store writes it): the cutover point")
    ap.add_argument("--dry-run", action="store_true", help="count what would be added, list the person's acts; write "
                    "nothing")
    ap.add_argument("--hold-person-acts", action="store_true", help="merge everything but the delta's acts of a person "
                    "(owner answers, human prompts, prompt origins, diff approvals, hand-over accepts and verifies), "
                    "listed for whoever runs it to look at; a later run without it merges them")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        counts = merge(a.source, a.target, a.since, a.dry_run, a.hold_person_acts)
    except MergeError as e:
        print(f"of_merge_delta: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(counts))
    else:
        acts = counts.pop("person_acts")
        for t, c in counts.items():
            print(f"{t:18} seen {c['seen']:>8}  {'would add' if a.dry_run else 'added'} {c['added']:>8}"
                  + (f"  refused {c['refused']} (bytes not their hash)" if c.get("refused") else "")
                  + (f"  held {c['held']}" if c.get("held") else ""))
        print(f"\nacts of a person in the delta: {len(acts)}" + (" (held: not merged)" if a.hold_person_acts and acts
                                                                  else ""))
        for x in acts[:500]:
            print(f"  {x['ts']}  {x['name']:30} {x['event_id']}  position {x['position'] or '-'}  host "
                  f"{x['host_id'] or '-'}" + (f"  via {x['via']}" if x.get("via") else "")
                  + (f"  origin {x['origin']}" if x.get("origin") else "")
                  + (f"  ask {str(x['ask_id'])[-8:]}" if x.get("ask_id") else ""))
        if len(acts) > 500:
            print(f"  … {len(acts) - 500} more (--json lists them all)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
