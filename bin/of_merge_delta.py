#!/usr/bin/env python3
"""B3 of the agent host move: merge one organization's rows from one machine's local store into another's, by id
(organic-framework-build reports/agent-server-migration-plan-2026-11-11.md §3; hand-over aba3d171 item 2). At S3 the
host takes the laptop's delta for the OF team, at S4 for a user organization, and B1's reverse brings the host's delta
back. On the host each organization runs as its own OS user with its own store, so a merge is one organization's.

  python3 bin/of_merge_delta.py --source SRC/of.db --target DST/of.db --org ORG [--since ISO]
      [--person-acts-from HOST_ID,…] [--dry-run] [--json]

What it merges, each by the key that names it on every machine, for ORG only:
- events by `event_id` (UUIDv7, or a fixed id from of_events.stable_id) whose `org` is ORG, and those with no org that
  belong to one of ORG's positions or sessions (as the source's own events show them), with their full-text index;
- transcript records of ORG's sessions by (session_id, record_uuid): with --since those dated at or after it and the
  undated ones that lie after the first such record in their file; session forks of those sessions;
- hook records of ORG's sessions by their content (time, kind, session, blob), since their row ids are per machine; an
  event's raw_ref to one (`hook_record:<id>`) is rewritten to the target's id;
- the blobs those rows name, each hashed before it is stored: one whose bytes are not its address is refused with the
  row that names it.
Not merged: another organization's rows (counted as such); machine-level rows that name no organization's session
(runtime records, snapshots, the pulse, capacity and disk records); the ingest and ship cursors; `meta`.

**Acts of a person** (PERSON_ACTS: the owner's answers, human prompts, prompt-origin corrections, diff approvals,
hand-over accepts and verifies, consents) are what the trust paths read as a person's own. The source is a store any
process of its OS user can write, so they are listed by name, id, position and host, and merged only when their host
is one --person-acts-from names: the owner's own machine, as whoever runs the merge knows it (of_secrets-review and
of_review on organic-framework PR 195). The rest are held; a later run that names their host merges them. Every other
event name crosses: the history the projections, Studio and the analyses read, none of which a trust path takes as a
person's act. Grants carry their own signature, which every reader checks again.

With --since, only rows at or after that time; without it, every row of ORG. A re-run adds nothing. The source is
opened read-only; the target is written one table per transaction. Run it where the target is, on a copy of the
source's of.db: selecting by time reads the source's tables whole (a 20-minute delta of a 3.5-million-record store took
about a minute of disk reads), which is no job for the laptop being moved off. The projections read the log, so the
delta's asks and hand-overs appear in `of asks` and `of handover list` at once; `of listen rebuild` re-derives the
listener's views in time order after a large merge. ClickHouse needs no copy of its own: the merged rows come after
the target's ship cursor, so its own `of ship` sends them through its own of-server, and ClickHouse keeps an event and a
transcript record once by its key (docs/storage.md, "Merging another machine's store")."""
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
HOOK_KEY = ("received_at", "hook_event", "session_id", "blob_sha256")
PERSON_ACTS = ("of.agent_question_answered", "of.human_prompt", "of.prompt_origin", "of.diff_approved",
               "of.handover_accepted", "of.handover_verified", "of.consent")
TABLES = ("blob", "hook_record", "transcript_record", "session_fork", "event")


def person_act(name, payload):
    """Is this event an act the target's trust paths would take as a person's own?"""
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
    """Copy one blob the target lacks, after hashing its bytes: a blob whose bytes are not its address is refused, and
    the row that names it is not merged either (the blob table is INSERT OR IGNORE, so a wrong one would keep the real
    one out for good). True when the row may be merged."""
    if not sha or dst.execute("SELECT 1 FROM blob WHERE sha256 = ?", (sha,)).fetchone():
        return True
    try:
        data = of_store.get_blob(src, sha)
    except Exception:  # noqa: BLE001 - an unreadable blob is refused like a wrong one
        return False
    if data is None:
        return True  # the source lacks it too: the row keeps its reference, as the source had it
    if hashlib.sha256(data).hexdigest() != sha:
        counts["blob"]["refused"] += 1
        return False
    counts["blob"]["added"] += 1
    if not dry:
        of_store.put_blob(dst, data)
    return True


def org_scope(src, org):
    """(positions, sessions) of ORG as the source's own events show them."""
    positions, sessions = set(), set()
    for pos, sid in src.execute("SELECT DISTINCT position, session_id FROM event WHERE org = ?", (org,)):
        if pos:
            positions.add(pos)
        if sid:
            sessions.add(sid)
    return positions, sessions


def merge(source, target, org, since=None, dry_run=False, person_acts_from=()):
    """{table: {seen, added, refused, other_org, held}, person_acts: [...]} after merging ORG's rows of SOURCE's store
    into TARGET's (paths to of.db). person_acts lists the delta's acts of a person the target lacked, `held` unless
    their host is in PERSON_ACTS_FROM."""
    if not org:
        raise MergeError("--org names the organization whose rows move: a store holds every organization's")
    if os.path.realpath(source) == os.path.realpath(target):
        raise MergeError("the source and the target are the same store")
    src = _source(source)
    dst = of_store.connect(target)  # the target's schema, migrated as every OF store is
    positions, sessions = org_scope(src, org)
    trusted = {h for h in person_acts_from or () if h}
    counts = {t: {"seen": 0, "added": 0, "refused": 0, "other_org": 0, "held": 0} for t in TABLES}
    acts, hook_map = [], {}

    def tx(fn):
        if dry_run:
            fn()
            return
        with of_store.write_tx(dst):
            fn()

    hook_cols = [c for c in _cols(dst, "hook_record") if c != "id"]
    hook_have = ["id"] + [c for c in hook_cols if c in _cols(src, "hook_record")]
    target_hooks = None

    def hook_key_map():
        """{natural key: target id}, loaded once (no index serves the key; of_review on PR 195)."""
        nonlocal target_hooks
        if target_hooks is None:
            q = f"SELECT id, {', '.join(HOOK_KEY)} FROM hook_record" + (" WHERE received_at >= ?" if since else "")
            target_hooks = {tuple(r[1:]): r[0] for r in dst.execute(q, (since,) if since else ())}
        return target_hooks

    def hook_row(row):
        d = dict(zip(hook_have, row))
        if d["id"] in hook_map:
            return
        counts["hook_record"]["seen"] += 1
        if d.get("session_id") not in sessions:
            counts["hook_record"]["other_org"] += 1
            hook_map[d["id"]] = None
            return
        key = tuple(d.get(k) for k in HOOK_KEY)
        hit = hook_key_map().get(key) or dst.execute(
            "SELECT id FROM hook_record WHERE " + " AND ".join(f"{k} IS ?" for k in HOOK_KEY), key).fetchone()
        if hit:
            hook_map[d["id"]] = hit if isinstance(hit, int) else hit[0]
            return
        if not _blob(src, dst, d.get("blob_sha256"), dry_run, counts):
            counts["hook_record"]["refused"] += 1
            hook_map[d["id"]] = None
            return
        counts["hook_record"]["added"] += 1
        if dry_run:
            hook_map[d["id"]] = None
            return
        rest = [c for c in hook_have if c != "id"]
        cur = dst.execute(f"INSERT INTO hook_record ({', '.join(rest)}) VALUES ({', '.join('?' * len(rest))})",
                          tuple(d[c] for c in rest))
        hook_map[d["id"]] = cur.lastrowid
        target_hooks[key] = cur.lastrowid

    def hooks():
        q = f"SELECT {', '.join(hook_have)} FROM hook_record" + (" WHERE received_at >= ?" if since else "")
        for row in src.execute(q + " ORDER BY id", (since,) if since else ()):
            hook_row(row)
    if _has(src, "hook_record"):
        tx(hooks)

    def raw_ref(ref):
        if not isinstance(ref, str) or not ref.startswith("hook_record:") or not ref.split(":", 1)[1].isdigit():
            return ref if not (isinstance(ref, str) and ref.startswith("runtime_record:")) else None
        rid = int(ref.split(":", 1)[1])
        if rid not in hook_map:  # a record before --since that a later event points at
            row = src.execute(f"SELECT {', '.join(hook_have)} FROM hook_record WHERE id = ?", (rid,)).fetchone()
            if row:
                hook_row(row)
        new = hook_map.get(rid)
        return f"hook_record:{new}" if new else None

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
                if sid in sessions:
                    first[(sid, path)] = off
            for (sid, path), off in first.items():
                rows += src.execute(q + " WHERE session_id IS ? AND timestamp IS NULL AND source_path = ? AND "
                                    "line_offset >= ?", (sid, path, off)).fetchall()
        for row in rows:
            d = dict(zip(have, row))
            counts["transcript_record"]["seen"] += 1
            if d.get("session_id") not in sessions:  # another organization's conversation never crosses
                counts["transcript_record"]["other_org"] += 1
                continue
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

    def forks():
        for row in src.execute("SELECT session_id, parent_session_id, shared, method, detected_at FROM session_fork"):
            counts["session_fork"]["seen"] += 1
            if row[0] not in sessions:
                counts["session_fork"]["other_org"] += 1
                continue
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
            mine = d.get("org") == org or (not d.get("org") and (d.get("position") in positions
                                                                 or d.get("session_id") in sessions))
            if not mine:
                counts["event"]["other_org"] += 1
                continue
            if dst.execute("SELECT 1 FROM event WHERE event_id = ?", (d["event_id"],)).fetchone():
                continue
            try:
                payload = json.loads(d.get("payload") or "{}")
            except ValueError:
                payload = {}
            if person_act(d.get("name"), payload if isinstance(payload, dict) else {}):
                held = d.get("host_id") not in trusted
                acts.append({"name": d["name"], "event_id": d["event_id"], "ts": d.get("ts"),
                             "position": d.get("position"), "host_id": d.get("host_id"),
                             "via": payload.get("via"), "origin": payload.get("origin"),
                             "ask_id": payload.get("ask_id") or payload.get("id"), "held": held})
                if held:
                    counts["event"]["held"] += 1
                    continue
            counts["event"]["added"] += 1
            if dry_run:
                continue
            d["raw_ref"] = raw_ref(d.get("raw_ref"))
            cols = [c for c in EVENT_COLS if c in d]
            cur = dst.execute(f"INSERT OR IGNORE INTO event ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                              tuple(d[c] for c in cols))
            if cur.rowcount and isinstance(payload, dict):
                text = "\n".join(of_store._texts(payload, []))
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
    ap.add_argument("--org", required=True, help="the organization whose rows move (its id, as the store writes it)")
    ap.add_argument("--since", help="only rows at or after this time (ISO, as the store writes it): the cutover point")
    ap.add_argument("--person-acts-from", default="", help="host ids, comma-separated, whose acts of a person merge: "
                    "the owner's own machine; every other act of a person is listed and held")
    ap.add_argument("--dry-run", action="store_true", help="count what would be added, list the acts of a person; "
                    "write nothing")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        counts = merge(a.source, a.target, a.org, a.since, a.dry_run,
                       [h.strip() for h in a.person_acts_from.split(",") if h.strip()])
    except MergeError as e:
        print(f"of_merge_delta: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(counts))
        return 0
    acts = counts.pop("person_acts")
    for t, c in counts.items():
        print(f"{t:18} seen {c['seen']:>8}  {'would add' if a.dry_run else 'added'} {c['added']:>8}"
              + (f"  another organization's {c['other_org']}" if c.get("other_org") else "")
              + (f"  refused {c['refused']} (bytes not their hash)" if c.get("refused") else "")
              + (f"  held {c['held']} (acts of a person)" if c.get("held") else ""))
    held = [x for x in acts if x["held"]]
    print(f"\nacts of a person in the delta: {len(acts)}, held {len(held)}"
          + (" (name their host with --person-acts-from once you have checked it is the owner's own machine)"
             if held else ""))
    for x in acts[:500]:
        print(f"  {'held ' if x['held'] else 'merge'}  {x['ts']}  {x['name']:30} {x['event_id']}  position "
              f"{x['position'] or '-'}  host {x['host_id'] or '-'}" + (f"  via {x['via']}" if x.get("via") else "")
              + (f"  origin {x['origin']}" if x.get("origin") else "")
              + (f"  ask {str(x['ask_id'])[-8:]}" if x.get("ask_id") else ""))
    if len(acts) > 500:
        print(f"  … {len(acts) - 500} more (--json lists them all)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
