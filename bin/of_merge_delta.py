#!/usr/bin/env python3
"""B3 of the agent host move: merge one organization's rows from one machine's local store into another's, by id
(organic-framework-build reports/agent-server-migration-plan-2026-11-11.md §3; hand-over aba3d171 item 2). At S3 the
host takes the laptop's delta for the OF team, at S4 for a user organization, and B1's reverse brings the host's delta
back. On the host each organization runs as its own OS user with its own store, so a merge is one organization's.

  python3 bin/of_merge_delta.py --source SRC/of.db --target DST/of.db --org ORG [--orgs ORGS.yaml] [--since ISO]
      [--person-acts EVENT_ID,…] [--dry-run] [--json]

What it merges, each by the key that names it on every machine, for ORG only:
- events by `event_id` (UUIDv7, or a fixed id from of_events.stable_id): ORG's, whose position, when they name one,
  is one of ORG's in the target's own registry (--orgs), never the source's label; and whose session, when they name
  one, is ORG's: the target knows it as a session of ORG's positions, or does not know it and the source names only
  ORG's positions for it. With their full-text index;
- transcript records of ORG's sessions by (session_id, record_uuid): with --since those dated at or after it and the
  undated ones that lie after the first such record in their file; session forks of those sessions;
- hook records of ORG's sessions by their content (time, kind, session, blob), since their row ids are per machine; an
  event's raw_ref to one (`hook_record:<id>`) is rewritten to the target's id;
- the blobs those rows name, each hashed before it is stored: one whose bytes are not its address is refused with the
  row that names it.
Not merged: another organization's rows (counted as such); machine-level rows that name no organization's session
(runtime records, snapshots, the pulse, capacity and disk records); the ingest and ship cursors; `meta`.

**Every field of a source row is its writer's word**: on B1's reverse the source is a store an organization's OS user
writes (of_secrets-review and of_review on organic-framework PR 195). So:
- **an event that changes a record by its id, or files itself under one** (an answer, a withdrawal, a snooze, an amend
  of an ask; a hand-over step; a diff's approval or application, which closes its card; a loop's step, an ask or a
  record filed for a work) crosses only when that record is ORG's: an ask, hand-over or diff on the target or in this
  delta, a work in ORG's registry; the rest are held and listed;
- **acts of a person** (PERSON_ACTS: the owner's answers, withdrawals, snoozes, hand-offs and labels, human prompts,
  prompt-origin corrections, diff approvals, hand-over accepts and verifies, consents), what the trust paths read as a
  person's own, are listed by name, id, position and host, and merged only by the event ids --person-acts names, which
  whoever runs the merge read in the listing and checked. A host id is the source's own claim and decides nothing.
Every other event name crosses: the history the projections, Studio and the analyses read. Grants carry their own
signature, which every reader checks again.

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
import time

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
               "of.handover_accepted", "of.handover_verified", "of.consent", "of.ask_withdrawn", "of.ask_snoozed",
               "of.ask_handed", "of.question_labelled", "of.issue_labelled")
# events that change an ask's or a hand-over's state by its id, whoever wrote them: they cross only when the record
# they name is ORG's (of_review on organic-framework PR 195: a withdrawal labelled with one organization closed
# another's ask)
ASK_REFS = ("of.agent_question_answered", "of.agent_question_judged", "of.ask_withdrawn", "of.ask_snoozed",
            "of.ask_handed", "of.ask_escalated", "of.ask_amended", "of.question_labelled", "of.escalation_due")
TABLES = ("blob", "hook_record", "transcript_record", "session_fork", "event")


def person_act(name, payload):
    """Is this event an act the target's trust paths would take as a person's own?"""
    if name not in PERSON_ACTS:
        return False
    if name == "of.human_prompt":
        return payload.get("origin") in ("human", "paste")
    if "by" in payload:  # an answer, a withdrawal, a label: the owner's, not an agent's own closure
        return payload.get("by") == "owner"
    return True


def refs(name, payload):
    """[(kind, ids)] of the records an event changes or files itself under by id: an ask, a hand-over, a diff (its card
    closes on `of.diff_applied`; of_review on organic-framework PR 195), or a work of the registry (a loop's
    hypotheses, runs and decisions, an ask filed for a work, a work record)."""
    out = []
    if name in ASK_REFS:
        ids = [payload.get("ask_id")] + list(payload.get("ask_ids") or [])
        out.append(("ask", {i for i in ids if isinstance(i, str) and i}))
    if name.startswith("of.handover_") and name != "of.handover_proposed":
        out.append(("handover", {payload["id"]} if isinstance(payload.get("id"), str) else set()))
    if name.startswith("of.diff_") and name != "of.diff_proposed":
        ids = {payload.get("id"), payload.get("diff_id")}
        out.append(("diff", {i for i in ids if isinstance(i, str) and i}))
    works = ([payload["work"]] if isinstance(payload.get("work"), str) and payload["work"] else []) + \
        [w for w in payload.get("works") or [] if isinstance(w, str) and w]
    if works:
        out.append(("work", set(works)))
    return out


class MergeError(Exception):
    pass


# Ingest's own cap and yield (of_store, of_platform 2026-11-14): a hook waits on the store's lock 0.15 s.
TX_CAP_S = 0.25
TX_YIELD_S = 0.02


class _Writer:
    """Short write transactions, as ingest writes, so a target's hooks never wait on the merge (of_platform, msg
    01a1dc95-cee1: one table in one transaction held a store's lock 154.7 s). The lock is taken just before a write
    (reading the source and checking the target take none), and at each row, written or skipped, a transaction past
    TX_CAP_S commits and sleeps TX_YIELD_S, so a waiting hook takes the lock. A merge is idempotent, so one cut short
    is finished by running it again."""

    def __init__(self, conn, dry):
        self.conn, self.dry, self.t0, self.count, self.longest = conn, dry, None, 0, 0.0

    def begin(self):
        if not self.dry and self.t0 is None:
            self.conn.execute("BEGIN IMMEDIATE")
            self.t0 = time.monotonic()

    def commit(self):
        if self.t0 is not None:
            self.conn.execute("COMMIT")
            self.longest = max(self.longest, time.monotonic() - self.t0)
            self.count += 1
            of_store.warn_long(self.t0)
            self.t0 = None

    def rollback(self):
        if self.t0 is not None:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            self.t0 = None

    def tick(self):
        """Before each row: past the cap, commit and let a waiting writer in."""
        if self.t0 is not None and time.monotonic() - self.t0 > TX_CAP_S:
            self.commit()
            time.sleep(TX_YIELD_S)


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


def _blob(src, dst, sha, dry, counts, w=None):
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
        if w:
            w.begin()
        of_store.put_blob(dst, data)
    return True


def registry_positions(orgs_file, org):
    """(ORG's position names and aliases, ORG's work ids) from the target's own registry (its orgs.yaml): the source's
    labels never decide them (of_review on organic-framework PR 195: an event labelled with one organization named
    another's position, and crossed as that position's)."""
    import yaml
    try:
        with open(orgs_file) as fh:
            doc = yaml.safe_load(fh) or {}
    except OSError as e:
        raise MergeError(f"the target's organizations list {orgs_file}: {e.strerror}")
    base = os.path.dirname(os.path.abspath(orgs_file))
    for o in doc.get("orgs") or []:
        root = os.path.join(base, os.path.expanduser(str(o.get("path") or "")))
        if (o.get("name") or os.path.basename(root)) != org or not o.get("path"):
            continue
        try:
            with open(os.path.join(root, "registry", "positions.yaml")) as fh:
                reg = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError) as e:
            raise MergeError(f"{org}'s registry on the target: {e}")
        names = set()
        for p in reg.get("positions") or []:
            if isinstance(p, dict) and p.get("name"):
                names.add(p["name"])
                names.update(a for a in p.get("aliases") or [] if isinstance(a, str))
        works = reg.get("works") if isinstance(reg.get("works"), dict) else {}
        return names, {str(w) for w in works}
    raise MergeError(f"the target's {orgs_file} does not list {org}: its registry decides which positions cross")


def org_scope(src, dst, org, positions):
    """ORG's sessions: one the target already knows as a session of ORG's positions, or one it does not know at all
    whose every event in the source names one of ORG's positions. A session the target knows as another organization's
    never comes into scope, whatever the source says."""
    cand = {}
    for sid, pos in src.execute("SELECT DISTINCT session_id, position FROM event WHERE session_id IS NOT NULL"):
        cand.setdefault(sid, set()).add(pos or None)
    sessions = set()
    for sid, poss in cand.items():
        named = {p for p in poss if p}
        known = {r[0] for r in dst.execute("SELECT DISTINCT position FROM event WHERE session_id = ? AND position IS NOT "
                                           "NULL AND position != ''", (sid,))}
        if known:
            if known <= positions:
                sessions.add(sid)
        elif named and named <= positions:
            sessions.add(sid)
    return sessions


def merge(source, target, org, since=None, dry_run=False, person_act_ids=(), orgs_file=None):
    """{table: {seen, added, refused, other_org, held}, person_acts: [...], held_refs: [...]} after merging ORG's rows of
    SOURCE's store into TARGET's (paths to of.db). ORG's positions come from the target's ORGS_FILE registry.
    person_acts lists the delta's acts of a person the target lacked, `held` unless its event id is in PERSON_ACT_IDS
    (ids whoever runs it read in the listing: a host id is the source's own claim). held_refs lists events held because
    they change an ask or a hand-over that is not ORG's."""
    if not org:
        raise MergeError("--org names the organization whose rows move: a store holds every organization's")
    if os.path.realpath(source) == os.path.realpath(target):
        raise MergeError("the source and the target are the same store")
    if os.path.realpath(source) == os.path.realpath(of_store.db_path()):
        raise MergeError("the source is this machine's live store: merge from a copy (sqlite3 <its of.db> "
                         "\".backup <copy>\"), never from the store its hooks write (docs/storage.md)")
    positions, works = registry_positions(orgs_file or os.environ.get("OF_ORGS")
                                          or os.path.expanduser("~/.config/of/orgs.yaml"), org)
    src = _source(source)
    dst = of_store.connect(target)  # the target's schema, migrated as every OF store is
    sessions = org_scope(src, dst, org, positions)
    trusted = {i for i in person_act_ids or () if i}
    counts = {t: {"seen": 0, "added": 0, "refused": 0, "other_org": 0, "held": 0} for t in TABLES}
    acts, held_refs, hook_map = [], [], {}

    w = _Writer(dst, dry_run)

    def tx(fn):
        try:
            fn()
            w.commit()
        except BaseException:
            w.rollback()
            raise

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
        w.tick()
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
        if not _blob(src, dst, d.get("blob_sha256"), dry_run, counts, w):
            counts["hook_record"]["refused"] += 1
            hook_map[d["id"]] = None
            return
        counts["hook_record"]["added"] += 1
        if dry_run:
            hook_map[d["id"]] = None
            return
        rest = [c for c in hook_have if c != "id"]
        w.begin()
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
            w.tick()
            d = dict(zip(have, row))
            counts["transcript_record"]["seen"] += 1
            if d.get("session_id") not in sessions:  # another organization's conversation never crosses
                counts["transcript_record"]["other_org"] += 1
                continue
            if dst.execute("SELECT 1 FROM transcript_record WHERE session_id IS ? AND record_uuid = ?",
                           (d.get("session_id"), d["record_uuid"])).fetchone():
                continue
            if not _blob(src, dst, d.get("blob_sha256"), dry_run, counts, w):
                counts["transcript_record"]["refused"] += 1
                continue
            counts["transcript_record"]["added"] += 1
            if not dry_run:
                w.begin()
                dst.execute(f"INSERT OR IGNORE INTO transcript_record ({', '.join(have)}) VALUES "
                            f"({', '.join('?' * len(have))})", row)
    if _has(src, "transcript_record"):
        tx(transcripts)

    def forks():
        for row in src.execute("SELECT session_id, parent_session_id, shared, method, detected_at FROM session_fork"):
            w.tick()
            counts["session_fork"]["seen"] += 1
            if row[0] not in sessions:
                counts["session_fork"]["other_org"] += 1
                continue
            if dst.execute("SELECT 1 FROM session_fork WHERE session_id = ?", (row[0],)).fetchone():
                continue
            counts["session_fork"]["added"] += 1
            if not dry_run:
                w.begin()
                dst.execute("INSERT OR IGNORE INTO session_fork VALUES (?,?,?,?,?)", row)
    if _has(src, "session_fork") and _has(dst, "session_fork"):
        tx(forks)

    def in_scope(d):
        """ORG's event: no position, or one of ORG's (from the target's registry); ORG's label, or none with one of
        ORG's sessions; and a session, when it names one, in ORG's scope."""
        pos, sid = d.get("position") or None, d.get("session_id") or None
        if pos and pos not in positions:
            return False
        if sid and sid not in sessions:
            return False
        return d.get("org") == org or (not d.get("org") and bool(pos or sid))

    have = [c for c in EVENT_COLS if c in _cols(src, "event")]
    rows = src.execute(f"SELECT {', '.join(have)} FROM event" + (" WHERE ts >= ?" if since else "")
                       + " ORDER BY ts, rowid", (since,) if since else ()).fetchall()
    # ORG's asks and hand-overs: those the target holds as ORG's, and those this delta brings in scope
    # ORG's asks, hand-overs and diffs: those the target holds as ORG's, and those this delta brings in scope; ORG's
    # works: its registry's
    own = {"ask": set(), "handover": set(), "diff": set(), "work": set(works)}
    makers = {"of.agent_question": "ask", "of.handover_proposed": "handover", "of.diff_proposed": "diff"}
    for name, kind in makers.items():
        for eid, pos, pl in dst.execute("SELECT event_id, position, payload FROM event WHERE name = ? AND org = ?",
                                        (name, org)):
            if not pos or pos in positions:
                own[kind].add(eid)
                if kind == "diff":  # a diff is named by its own id, which its proposal carries
                    try:
                        did = (json.loads(pl or "{}") or {}).get("id")
                    except ValueError:
                        did = None
                    if isinstance(did, str):
                        own["diff"].add(did)
    for row in rows:
        d = dict(zip(have, row))
        if d.get("name") in makers and in_scope(d):
            kind = makers[d["name"]]
            own[kind].add(d["event_id"])
            if kind == "diff":
                try:
                    did = (json.loads(d.get("payload") or "{}") or {}).get("id")
                except ValueError:
                    did = None
                if isinstance(did, str):
                    own["diff"].add(did)

    def events():
        for row in rows:
            w.tick()
            d = dict(zip(have, row))
            counts["event"]["seen"] += 1
            if not in_scope(d):
                counts["event"]["other_org"] += 1
                continue
            if dst.execute("SELECT 1 FROM event WHERE event_id = ?", (d["event_id"],)).fetchone():
                continue
            try:
                payload = json.loads(d.get("payload") or "{}")
            except ValueError:
                payload = {}
            payload = payload if isinstance(payload, dict) else {}
            foreign = sorted({i for kind, ids in refs(d.get("name") or "", payload) for i in ids - own[kind]})
            if foreign:  # it would change, or file itself under, a record that is not ORG's
                counts["event"]["held"] += 1
                held_refs.append({"name": d["name"], "event_id": d["event_id"], "position": d.get("position"),
                                  "names": foreign})
                continue
            if person_act(d.get("name"), payload):
                held = d["event_id"] not in trusted
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
            w.begin()
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
    counts["held_refs"] = held_refs
    counts["transactions"] = {"count": w.count, "longest_s": round(w.longest, 3)}
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(prog="of_merge_delta", description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", required=True, help="the store to read: another machine's of.db (opened read-only)")
    ap.add_argument("--target", required=True, help="the store to merge into: this machine's of.db")
    ap.add_argument("--org", required=True, help="the organization whose rows move (its id, as the store writes it)")
    ap.add_argument("--since", help="only rows at or after this time (ISO, as the store writes it): the cutover point")
    ap.add_argument("--orgs", help="the target's organizations list (default: OF_ORGS or ~/.config/of/orgs.yaml); "
                    "ORG's registry there decides which positions cross")
    ap.add_argument("--person-acts", default="", help="event ids, comma-separated, of acts of a person to merge, as the "
                    "listing showed them and you checked them; every other act of a person is held")
    ap.add_argument("--dry-run", action="store_true", help="count what would be added, list the acts of a person; "
                    "write nothing")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        counts = merge(a.source, a.target, a.org, a.since, a.dry_run,
                       [i.strip() for i in a.person_acts.split(",") if i.strip()], a.orgs)
    except MergeError as e:
        print(f"of_merge_delta: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(counts))
        return 0
    acts = counts.pop("person_acts")
    refs_held = counts.pop("held_refs")
    txs = counts.pop("transactions")
    for t, c in counts.items():
        print(f"{t:18} seen {c['seen']:>8}  {'would add' if a.dry_run else 'added'} {c['added']:>8}"
              + (f"  another organization's {c['other_org']}" if c.get("other_org") else "")
              + (f"  refused {c['refused']} (bytes not their hash)" if c.get("refused") else "")
              + (f"  held {c['held']} (acts of a person)" if c.get("held") else ""))
    if not a.dry_run:
        print(f"{'transactions':18} {txs['count']:>13}  the longest held the lock {txs['longest_s']} s")
    for x in refs_held[:200]:
        print(f"  held   {x['name']:30} {x['event_id']}  names another organization's or an unknown record: "
              f"{', '.join(str(n)[-8:] for n in x['names'])}")
    held = [x for x in acts if x["held"]]
    print(f"\nacts of a person in the delta: {len(acts)}, held {len(held)}"
          + (" (check each, then name its event id with --person-acts; a host id is the source's own claim)"
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
