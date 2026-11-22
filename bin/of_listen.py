"""The listener (RFC 0001 §5.1a): one local service that follows the event store and keeps views.

`of listen run` follows the local store (of.db, SQLite WAL) by rowid from a cursor per processor
and passes every new event, in order, to each registered processor. A processor keeps a
materialized view (tables in the same store, so Studio can read them) and may emit derived
events; time-based rules are its `tick`. After a batch changes a view, the listener bumps
`views.seq` (a small file): surfaces wake on it and read the views, with no recomputation.

Rebuildable: a processor whose version changed (or `of listen rebuild`) is reset and replayed
from rowid 0; the log stays the source of truth (event sourcing with projections). Without the
listener, the CLI computes from the log as before (`fresh()` says whether the views are live).
"""

import bisect
import json
import re
import os
import sys
import time

import of_events

POLL_S = 0.5
BATCH = 2000
SPOOL_S = 10  # the listener drains the hooks' spool this often (2026-11-17: the founder's prompts waited behind a
#              93-minute transcript ingest that held the only drainer's lock)
BUDGET_S = 2  # a processor's share of one step, unless it names its own budget_s (step())
HEARTBEAT_S = 5
STALE_S = 120  # 2026-11-11: 15 s made every reader project the whole log itself whenever one asks projection
# (5 to 57 s) held the loop; with ~40 agents that fallback was most of the Mac's load. A view at most 2 min old serves.

DDL = """
CREATE TABLE IF NOT EXISTS listener_cursor (processor TEXT PRIMARY KEY, version TEXT NOT NULL,
  last_rowid INTEGER NOT NULL, updated_at TEXT);
CREATE TABLE IF NOT EXISTS listener_meta (key TEXT PRIMARY KEY, value TEXT);
"""


def seq_path():
    return os.path.join(of_events.STATE_DIR, "views.seq")


def bump():
    """Tell the surfaces a view changed: one integer in a file (they watch its mtime)."""
    path = seq_path()
    try:
        n = int(open(path).read().strip() or 0) + 1
    except (OSError, ValueError):
        n = 1
    tmp = f"{path}.{os.getpid()}"
    with open(tmp, "w") as fh:
        fh.write(str(n))
    os.replace(tmp, path)
    return n


def seq():
    try:
        return os.stat(seq_path()).st_mtime_ns
    except OSError:
        return 0


def fresh(conn):
    """Are the views kept live right now (a listener heartbeat within STALE_S)?"""
    try:
        row = conn.execute("SELECT value FROM listener_meta WHERE key = 'heartbeat'").fetchone()
        return bool(row) and time.time() - float(row[0]) < STALE_S
    except Exception:
        return False


class Processor:
    """name, version; reset(conn) empties its view; handle(conn, rows) takes a batch of new events
    (dicts: rowid, event_id, ts, name, session_id, position, source, payload) and returns True if
    the view changed; tick(conn, now) runs every `tick_s` (None: no timer)."""
    name = "base"
    version = "0"
    tick_s = None
    budget_s = None  # BUDGET_S; a step's catch-up past it commits what it folded and yields to the next processor

    def reset(self, conn):
        pass

    def handle(self, conn, rows):
        return False

    def tick(self, conn, now):
        return False


def _minus_s(ts, seconds):
    """An event ts (2026-11-17T17:12:05.817Z) SECONDS earlier, in the same form; "" for none."""
    if not ts:
        return ""
    import datetime
    d = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")) - datetime.timedelta(seconds=seconds)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


# ---------------------------------------------------------------------- asks

class AsksProcessor(Processor):
    """The asks view: every ask with its state (of_asks.project), and the owner's queue grouped as
    the popup shows it. Tables view_asks and view_ask_groups."""
    name = "asks"
    version = "30"  # Permission cards keep their receiver and close on a verified denial.
    # 29: each ask keeps its organization, org, and groups never cross organizations (of_asks.group; of_review on PR 359); 28: each ask keeps its card id, ref (AS-412; of_events, of.ask_ref; the founder, prompt 01a1ea59-aeb7); 27: an escalated routed ask is answered_upstream by the owner, settled_in_unit by its unit owner or asker, with settled_by (of_asks.settle; of_server on PR 222); 26: an escalated routed ask is answered_upstream once a founder copy of it is answered or done; 25: an ask's to is a bare name (of_asks.bare_to: `--to <org>:<position>` was stored verbatim and never delivered); 24: a declared ask keeps the host_id of the machine that filed it, the one whose amends apply (of ask --amend; of_server on PR 181); 23: each row keeps its whole ask (column ask) and the fold its readers carry on (of_asks.FOLD_KEY): of_asks.current_asks; 22: a pane question the judge confirms is an ask OF files (source pane), kept until answered; 21: a heuristic question is a candidate until the judge opens or closes it (of.agent_question_judged); 20: declared_wait on a heuristic ask (never folded); 19: a heuristic ask's text is its last paragraph, its options from the stored text; 18: q-v4 heuristic asks; a declared wait on the founder keeps a heuristic question open; 17: the founder's free text marks declared asks seen and closes none (from 2026-11-07); 16: heuristic asks by q-v3 (a report with a declared task's steps is no question); 15: free text never closes a diff approval; 14: groups never cross recipients or tasks; a task is claimed only by the founder's own answer; 13: a turn_error is neutral (it closed asks as answered); 12: scheduler prompts close nothing (origin scheduled from the transcript; of.prompt_origin corrections); 11: a popup answer to one ask never closes the others (nor the open question); 10: a subagent's agent_question is not an ask; 9: tasks for the owner (claimed, done by the asker); 8: due, n_options; shadow rebuild; 7: of_asks.relevant (READS; pulled messages); 6: a position's reply answers an ask to it; 5: annotations neutral; 4: a popup answer closes only its ask; 3: status never closes
    DDL = """
    CREATE TABLE IF NOT EXISTS view_asks (event_id TEXT PRIMARY KEY, ts TEXT, org TEXT, session_id TEXT,
      position TEXT, kind TEXT, text TEXT, state TEXT, closed_by TEXT, closed_at TEXT, closed_by_kind TEXT,
      to_ TEXT, delegation TEXT, labels TEXT, view_version TEXT, ask TEXT);
    CREATE TABLE IF NOT EXISTS view_ask_groups (n INTEGER PRIMARY KEY, since TEXT, kind TEXT, text TEXT,
      who TEXT, ask_ids TEXT, first TEXT);
    """

    def __init__(self):
        self.rows = {}  # event_id -> row: a re-derived event (same id, new rowid) replaces its old copy
        # the projection carried between flushes (of_asks.new_state), the latest ts fed to it, the rows since: a flush
        # projects only what came (2026-11-13: re-projecting every row took 179 s a flush at load ~50); None: the
        # next write projects all rows again
        self.state, self.fed, self.tail = None, "", []
        self.written = {}  # event_id -> the row last written to view_asks: a flush writes only what changed
        self.max_rowid, self.fold = 0, None  # the last event handled; a shadow build's fold_meta, kept for settle()
        # the hold (HOLD_S): rows at or after the mark wait here, projected on a copy for each write; rows before it
        # are in the carried state for good. late: projections of every row a row later than the hold caused
        self.held, self.mark, self.late = [], "", 0
        # a start carried on from the stored view (_carry_on): its rows by event id (the columns a write keeps for asks
        # whose events this run has not read), the checkpoint's rowid (rows up to it are in the view), the
        # checkpoint a write stores beside the fold (CARRIED_KEY), kept for settle() in a shadow build
        self.stored, self.carried_upto, self.carried = {}, 0, None
        # snapshots of the carried state (SNAP_S apart, SNAP_KEEP_S back): [(mark, file)], oldest first; rewind: the
        # earliest ts a late row or a correction needs projected again from; rewinds: how often one was used
        self.snaps, self.rewind, self.rewinds, self.late_rows = [], None, 0, (0, None)
        import of_asks
        # a rebuild or a new version catches up on these names only (relevant() takes nothing else): a rebuild from
        # rowid 0 had read the whole log (the founder's read-path rule, 2026-11-14; seen on organic-framework PR 181)
        self.catch_up_names = tuple(of_asks.READS)

    def _relevant(self, r):
        import of_asks
        return of_asks.relevant(r["name"], r["source"], r.get("payload"))

    # The hold: the carried projection takes a row only once it is HOLD_S older than the newest row fed, so a row that
    # comes in late, by its ts, still folds in order; the rows not yet that old are projected on a copy at each write,
    # so the view and its fold mark cover every row up to the cursor and readers see no delay. Measured over two days
    # from 2026-11-15T18:00Z (83,300 rows, 5.7% late): every row was projected again in 434 flush windows (15 s) a
    # day; 19 with a hold of 120 s, 11 with 300 s, 4.5 with 600 s, 4.0 with 900 s. What a 600 s hold leaves is spool
    # drains after a busy store (670 rows at once, up to an hour old), counted in listen.log (of_platform's design
    # points, message 01a1eaab-8fbd).
    HOLD_S = 600
    # the carried state at the hold's mark, stored beside the fold at each write so a start carries on from it: the
    # mark, the asks the held rows added (absent from it) or changed (their carried versions), and its indexes
    CARRIED_KEY = "asks_carried"
    # A row older than the hold's mark, a correction of a prompt's origin or an event again under its id needs the
    # projection redone from before its ts. The processor keeps a snapshot of the carried state every SNAP_S of the
    # mark, SNAP_KEEP_S back (5.6 MB of JSON each for 7,704 asks, 0.03 s to take, 0.22 s to load), and redoes it from
    # the newest one before that ts with the rows since read again by ts: two hours of rows were 8,199 on 2026-11-17.
    # Only what is older than every snapshot (an import) reads the whole log.
    # The snapshots are files (snap_dir), so a start carried on from the checkpoint keeps them (of_review on PR 316: in
    # memory, a restart emptied them, and the first spool drain after it read the whole log again) and the listener
    # holds no 50 MB of them on a machine in swap (of_platform on PR 316). A rewind deletes the snapshots after the
    # one it takes, a projection of every row deletes them all, so a file never outlives the history it was cut from.
    SNAP_S = 900
    SNAP_KEEP_S = 2 * 3600

    def snap_dir(self):
        return os.path.join(of_events.STATE_DIR, "asks-snapshots", self.version)

    def _drop_snaps(self, keep=lambda m: False):
        """Delete the snapshot files whose mark KEEP refuses, and forget them."""
        for m, path in self.snaps:
            if not keep(m):
                try:
                    os.remove(path)
                except OSError:
                    pass
        self.snaps = [x for x in self.snaps if keep(x[0])]

    def _load_snaps(self, upto):
        """The snapshot files of this version at or before the mark UPTO, within SNAP_KEEP_S of it: a start carried on
        from the checkpoint takes them up; the others are deleted."""
        d, keep, out = self.snap_dir(), _minus_s(upto, self.SNAP_KEEP_S), []
        for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            path = os.path.join(d, name)
            mark = name[:-5].replace("_", ":") if name.endswith(".json") else ""
            if mark and keep <= mark <= upto:
                out.append((mark, path))
            else:
                try:
                    os.remove(path)
                except OSError:
                    pass
        self.snaps = out

    SHADOW = "__new"
    suffix = ""      # "__new" while a new version is built beside the view that keeps serving
    defer = False    # during the first catch-up: collect only, project once at settle()
    dirty = False    # rows arrived that the stored view does not reflect yet

    def reset(self, conn):
        """A new version or a rebuild: built into shadow tables while the old view keeps serving, and
        swapped in by settle() in one transaction (no stale or half-built window for the popup)."""
        conn.executescript(self.DDL)
        if "ask" not in {r[1] for r in conn.execute("PRAGMA table_info(view_asks)")} and not conn.execute(
                "SELECT count(*) FROM view_asks").fetchone()[0]:
            conn.executescript("DROP TABLE view_asks; DROP TABLE view_ask_groups;" + self.DDL)  # an empty old table
        if conn.execute("SELECT count(*) FROM view_asks").fetchone()[0]:
            self.suffix = self.SHADOW
            conn.executescript(self.DDL.replace("view_asks (", f"view_asks{self.SHADOW} (")
                               .replace("view_ask_groups (", f"view_ask_groups{self.SHADOW} ("))
        else:
            self.suffix = ""
        self.rows, self.defer, self.dirty = {}, True, True
        self.state, self.tail, self.written, self.max_rowid = None, [], {}, 0
        self.held, self.mark = [], ""
        self._load_snaps("")

    def load_state(self, conn):
        """After a restart: the projection carries on from the stored view and the carried checkpoint beside it
        (_carry_on), reading only the rows inside the hold and those after the checkpoint. Only where no whole view
        and checkpoint of this version are stored does it read every relevant row up to the cursor (a rebuild that was
        interrupted continues from its cursor too; it then settles into the view directly). The founder, 2026-11-17
        (prompt 01a1eae5-4583, via of_conductor), translated: "it is damn inconvenient to wait; all these questions
        must be lightweight and not wait for a start for minutes": that read took 16 to 17 minutes a start at load 60,
        three times that day, the questions screen stale meanwhile."""
        conn.executescript(self.DDL)
        for t in ("view_asks", "view_ask_groups"):
            conn.execute(f"DROP TABLE IF EXISTS {t}{self.SHADOW}")
        self.defer, self.dirty = True, False  # the stored view is current up to the cursor
        self.state, self.tail, self.written, self.held, self.mark = None, [], {}, [], ""
        self.stored, self.carried_upto, self.snaps, self.rewind = {}, 0, [], None
        row = conn.execute("SELECT last_rowid FROM listener_cursor WHERE processor = ?", (self.name,)).fetchone()
        self.max_rowid = int(row[0]) if row else 0
        if not self._carry_on(conn):
            self._load_snaps("")  # every row read again: no snapshot cut from another history stays
            self.rows = self._read_all(conn)

    @staticmethod
    def _read_all(conn):
        """Every relevant row of the log by event id: a start with no whole view and checkpoint stored, or a
        projection of all rows again after one carried on from them (an event again under its id, a correction of a
        prompt's origin, a row older than the hold's mark)."""
        import of_asks
        return {r["event_id"]: r for r in of_asks.load(conn)}

    def _carry_on(self, conn):
        """The carried state at the hold's mark, from the view's asks and the checkpoint (CARRIED_KEY); the rows inside
        the hold (ts at or after the mark, up to the checkpoint's rowid) held again, and the rows the last run handled
        after the checkpoint and never wrote, handled again. False when no whole view of this version with its fold
        and checkpoint is stored."""
        import of_asks
        try:
            def meta(key):
                return json.loads((conn.execute("SELECT value FROM listener_meta WHERE key = ?", (key,)).fetchone()
                                   or ["{}"])[0])
            fold, ck = meta(of_asks.FOLD_KEY), meta(self.CARRIED_KEY)
            stored = conn.execute("SELECT event_id, ts, org, session_id, position, kind, text, state, closed_by, "
                                  "closed_at, closed_by_kind, to_, delegation, labels, view_version, ask FROM view_asks "
                                  "ORDER BY ts, event_id").fetchall()
        except Exception:  # noqa: BLE001 - no listener tables yet, or an older view: read the rows
            return False
        mark, upto = ck.get("mark"), int(ck.get("rowid") or 0)
        if not (upto and mark and fold.get("rowid") == upto and stored) \
                or any(r[14] != self.version or not r[15] for r in stored):
            return False
        added, changed = set(ck.get("added") or []), ck.get("changed") or {}
        asks = [changed.get(r[0]) or json.loads(r[15]) for r in stored if r[0] not in added]
        self.state = of_asks.state_from(asks, ck.get("indexes") or {})
        self.mark, self.fed, self.carried_upto = mark, ck.get("fed") or "", upto
        self.stored = {r[0]: r for r in stored}
        self.written = dict(self.stored)  # the first write writes only what changed
        self.held = self._rows_since(conn, mark, upto)
        self.rows = {r["event_id"]: r for r in self.held}
        start = upto
        while start < self.max_rowid:  # handled before the restart and not yet in the view
            rows = fetch(conn, start, top=self.max_rowid, names=self.catch_up_names)
            if not rows:
                break
            self.handle(conn, rows)
            start = rows[-1]["rowid"]
        self.max_rowid = max(self.max_rowid, upto)
        self._load_snaps(mark)
        return True

    def _rows_since(self, conn, ts, upto):
        """The relevant rows with a ts at or after TS and a rowid up to UPTO, by ts: by the name index, never the whole
        log."""
        import of_asks
        out = []
        for e, t, n, sid, pos, pl, org, src, host in conn.execute(
                f"SELECT event_id, ts, name, session_id, position, payload, org, source, host_id FROM event "
                f"WHERE ts >= ? AND name IN ({','.join('?' * len(self.catch_up_names))}) AND rowid <= ?",
                (ts,) + tuple(self.catch_up_names) + (upto,)):
            try:
                pl = json.loads(pl) if pl else {}
            except ValueError:
                pl = {}
            if of_asks.relevant(n, src, pl):
                out.append({"event_id": e, "ts": t, "name": n, "session_id": sid, "position": pos, "payload": pl,
                            "org": org, "host_id": host})
        return sorted(out, key=lambda r: r["ts"] or "")

    @staticmethod
    def _origin_ts(conn, row):
        """The ts of the prompt an of.prompt_origin corrects, or "" (unknown: every row again)."""
        eid = (row.get("payload") or {}).get("event_id")
        got = conn.execute("SELECT ts FROM event WHERE event_id = ?", (eid,)).fetchone() if eid else None
        return (got[0] or "") if got else ""

    def _rewind_to_snapshot(self, conn):
        """The carried state from the newest snapshot at or before self.rewind, and every row since it read again (by
        ts, up to the rows handled); False when no snapshot is that old."""
        import of_asks
        snap = next(((m, path) for m, path in reversed(self.snaps) if m <= self.rewind), None)
        if snap is None:
            return False
        m, path = snap
        self._drop_snaps(lambda x: x <= m)  # cut from the history this row changes: gone before it is redone
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            self._drop_snaps(lambda x: x < m)
            return False
        self.state = of_asks.state_from(data["asks"], data["indexes"])
        rows = self._rows_since(conn, m, self.max_rowid)
        self.rows.update({r["event_id"]: r for r in rows})
        self.held, self.tail, self.mark = rows, [], m
        self.rewinds += 1
        print(f"of listen: asks: projected again from the snapshot at {m} ({len(rows)} rows since), for a row at "
              f"{self.rewind} ({self.rewinds} since the start)", flush=True)
        return True

    def settle(self, conn):
        """After the first catch-up: one projection, then the shadow tables replace the view at once."""
        self.defer = False
        if not self.dirty and not self.suffix:
            return False  # a restart with nothing new: the stored view stands, no bump
        self.write(conn)
        self.dirty = False
        if self.suffix:
            conn.execute("BEGIN IMMEDIATE")
            try:
                for t in ("view_asks", "view_ask_groups"):
                    conn.execute(f"DROP TABLE {t}")
                    conn.execute(f"ALTER TABLE {t}{self.suffix} RENAME TO {t}")
                if self.fold:
                    import of_asks
                    conn.execute("INSERT OR REPLACE INTO listener_meta VALUES (?, ?)", (of_asks.FOLD_KEY, self.fold))
                    conn.execute("INSERT OR REPLACE INTO listener_meta VALUES (?, ?)", (self.CARRIED_KEY, self.carried))
                    self.fold = self.carried = None
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            self.suffix = ""
        return True

    def handle(self, conn, rows):
        if rows:
            self.max_rowid = max(self.max_rowid, max(r.get("rowid") or 0 for r in rows))
        # a start carried on from the view (_carry_on): rows up to its checkpoint are in it or held already
        new = [r for r in rows if self._relevant(r) and (r.get("rowid") or 0) > self.carried_upto]
        if not new:
            return False
        late, oldest = self.late_rows
        for r in new:
            row = {k: r.get(k) for k in ("event_id", "ts", "name", "session_id", "position", "payload", "org",
                                         "host_id")}
            behind = (row["ts"] or "") < self.mark
            origin = r["name"] == "of.prompt_origin"
            if r["event_id"] in self.rows or origin or behind:
                # an event again under its id, a correction of an earlier prompt's origin, or a row older than the
                # hold's mark (imported history, a spool drained after a busy store): the carried projection no longer
                # holds from its ts on; the write redoes it from a snapshot before that, or from every row
                if behind:
                    late, oldest = late + 1, min(oldest or row["ts"], row["ts"])
                if self.state is not None:
                    at = self._origin_ts(conn, row) if origin else (row["ts"] or "")
                    self.rewind = at if self.rewind is None else min(self.rewind, at)
            self.rows[r["event_id"]] = row
            if self.state is not None:
                self.tail.append(row)
        self.late_rows = (late, oldest)
        self.dirty = True  # projected once, at the end of the step (flush) or of the first catch-up (settle)
        return False

    MIN_FLUSH_S = 15  # 2026-11-11: with ~40 agents the projection (5 to 57 s each) ran almost back to back, about half
    # a core for the listener alone while the founder's Ghostty lagged; once per 15 s keeps the popup current
    last_flush = 0.0

    def flush(self, conn):
        if not self.dirty or self.defer:
            return False
        if time.time() - self.last_flush < self.MIN_FLUSH_S:
            return False  # still dirty: the next step after the interval writes it
        self.write(conn)
        self.dirty, self.last_flush = False, time.time()
        return True

    def write(self, conn):
        import copy
        import of_asks
        if self.state is not None and self.rewind is not None and not self._rewind_to_snapshot(conn):
            self.state, self.tail, self.held = None, [], []  # older than every snapshot: every row again
            late, oldest = self.late_rows
            if late:
                self.late += 1
                print(f"of listen: asks: {late} event(s) older than the hold's mark {self.mark} (the oldest at "
                      f"{oldest}) and every snapshot: every row projected again ({self.late} since the start)",
                      flush=True)
        self.rewind, self.late_rows = None, (0, None)
        if self.state is None:
            self._drop_snaps()
            if self.stored:  # carried on from the view, with only the rows since: every row, read now
                self.rows = {**self._read_all(conn), **self.rows}
                self.stored, self.carried_upto = {}, 0
            self.state, self.held = of_asks.new_state(), sorted(self.rows.values(), key=lambda r: r["ts"] or "")
            self.fed, self.mark = "", ""  # imported history can arrive late: every row, in order, through the hold
        else:  # only the rows since the last write, on the carried projection: the same asks (of_asks.project)
            self.held = sorted(self.held + self.tail, key=lambda r: r["ts"] or "")
        self.tail = []
        if self.held:
            # never past now: a row stamped ahead (a wrong clock) would move the mark ahead and make every row after
            # it late (of_platform on PR 310)
            self.fed = max(self.fed, min(self.held[-1]["ts"] or "", of_events.now_iso()))
            self.mark = max(self.mark, _minus_s(self.fed, self.HOLD_S))
            k = bisect.bisect_left([r["ts"] or "" for r in self.held], self.mark)
            of_asks.project(self.held[:k], self.state)  # old enough: into the carried projection for good
            self.held = self.held[k:]
        if self.mark and (not self.snaps or _minus_s(self.mark, self.SNAP_S) >= self.snaps[-1][0]):
            indexes = {k: v for k, v in of_asks.fold_meta(self.state, 0, "").items()
                       if k in ("open_by_session", "declared_turn", "waits_founder")}
            d = self.snap_dir()
            os.makedirs(d, mode=0o700, exist_ok=True)
            path = os.path.join(d, self.mark.replace(":", "_") + ".json")
            tmp = path + ".tmp"
            with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
                json.dump({"asks": self.state["asks"], "indexes": indexes}, fh, ensure_ascii=False, default=str)
            os.replace(tmp, path)
            self.snaps.append((self.mark, path))
            keep = _minus_s(self.mark, self.SNAP_KEEP_S)
            self._drop_snaps(lambda m: m >= keep)
            size = sum(os.path.getsize(p) for _, p in self.snaps if os.path.exists(p)) / 1e6
            print(f"of listen: asks: snapshot at {self.mark}, {os.path.getsize(path) / 1e6:.1f} MB "
                  f"({len(self.snaps)} kept, {size:.0f} MB on disk)", flush=True)
        view = self.state
        if self.held:  # the rows inside the hold, on a copy: the view is whole, the carried state waits for them
            t0 = time.time()
            view = copy.deepcopy(self.state)
            of_asks.project(self.held, view)
            if time.time() - t0 >= 1:  # 0.08 s for 7,643 asks when measured (of_platform on PR 310)
                print(f"of listen: slow asks hold copy: {time.time() - t0:.1f} s ({len(self.held)} rows held)",
                      flush=True)
        carried = self.state["by_id"]
        checkpoint = json.dumps({
            "mark": self.mark, "fed": self.fed, "rowid": self.max_rowid,
            "added": [a["event_id"] for a in view["asks"] if a["event_id"] not in carried],
            "changed": {a["event_id"]: carried[a["event_id"]] for a in view["asks"] if view is not self.state
                        and a["event_id"] in carried and carried[a["event_id"]] != a},
            "indexes": {k: v for k, v in of_asks.fold_meta(self.state, 0, "").items()
                        if k in ("open_by_session", "declared_turn", "waits_founder")}},
            ensure_ascii=False, default=str)
        asks = view["asks"]
        groups = of_asks.group(of_asks.for_owner(asks))
        by_id = self.rows
        rows = []  # built before the write lock is taken
        for a in asks:
            ev, old = by_id.get(a["event_id"]), self.stored.get(a["event_id"])
            if ev is None and old is not None:  # carried on from the view: its own columns, as written
                org, delegation, labels = old[2], old[12], old[13]
            else:
                pl = (ev or {}).get("payload") or {}
                lab = {k: v for k, v in (pl.get("classifications") or {}).items() if isinstance(v, (bool, str))}
                lab.update({k: pl.get(k) for k in ("detected_by", "classifier_version") if pl.get(k)})
                org, delegation, labels = (ev or {}).get("org"), pl.get("delegation"), json.dumps(lab, ensure_ascii=False)
            closer = by_id.get(a["closed_by"])
            closer_kind = closer.get("name") if closer else (
                old[10] if old is not None and old[8] == a["closed_by"] else None)
            rows.append((a["event_id"], a["ts"], org, a["session_id"], a["position"], a["kind"],
                         a["text"], a["state"], a["closed_by"], a["closed_at"], closer_kind, a.get("to"),
                         delegation, labels, self.version, json.dumps(a, ensure_ascii=False, default=str)))
        # every row on this run's first write and in a shadow build; after that only the rows that changed (a flush
        # rewrote all 5,700 rows in one transaction, 12 to 179 s behind the store's other writers on 2026-11-13)
        full = not self.written or bool(self.suffix)
        changed = rows if full else [r for r in rows if self.written.get(r[0]) != r]
        conn.execute("BEGIN IMMEDIATE")
        try:
            if full:
                conn.execute(f"DELETE FROM view_asks{self.suffix}")
                conn.executemany(f"INSERT INTO view_asks{self.suffix} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            elif changed:
                conn.executemany("INSERT OR REPLACE INTO view_asks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", changed)
            conn.execute(f"DELETE FROM view_ask_groups{self.suffix}")
            conn.executemany(f"INSERT INTO view_ask_groups{self.suffix} VALUES (?,?,?,?,?,?,?)", [
                (i, g["since"], g["kind"], g["text"], json.dumps(g["who"], ensure_ascii=False),
                 json.dumps(g["ask_ids"]), json.dumps(g.get("first"), ensure_ascii=False))
                for i, g in enumerate(groups)])
            # what the view now reflects, for readers that carry it on (of_asks.current_asks); a shadow build's goes
            # in when settle() swaps it in
            fold = json.dumps(of_asks.fold_meta(view, self.max_rowid, self.fed), ensure_ascii=False)
            if self.suffix:
                self.fold, self.carried = fold, checkpoint
            else:
                conn.execute("INSERT OR REPLACE INTO listener_meta VALUES (?, ?)", (of_asks.FOLD_KEY, fold))
                conn.execute("INSERT OR REPLACE INTO listener_meta VALUES (?, ?)", (self.CARRIED_KEY, checkpoint))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            self.written = {}  # unknown what the table holds now: the next write writes every row
            raise
        self.written = {r[0]: r for r in rows}


def read_groups(conn):
    """The owner's queue from the view, in the shape of_asks.group returns."""
    out = []
    for since, kind, text, who, ids, first in conn.execute(
            "SELECT since, kind, text, who, ask_ids, first FROM view_ask_groups ORDER BY n"):
        out.append({"since": since, "kind": kind, "text": text, "who": json.loads(who), "ask_ids": json.loads(ids),
                    "first": json.loads(first) if first else None})
    return out


def read_asks(conn):
    """The view's asks with the fields their readers use. The card id and the diff come from the whole ask the view
    keeps (`ask`): the judge knows a cited ask by its card id (j-v7) and a diff's card by its diff, and without them a
    machine with a fresh view knew neither (of_review on organic-framework PR 528)."""
    return [{"event_id": e, "ts": ts, "session_id": s, "position": p, "kind": k, "text": t, "state": st,
             "closed_by": cb, "closed_at": ca, "to": to, "ref": ref, "ref_plain": plain, "diff_id": diff}
            for e, ts, s, p, k, t, st, cb, ca, to, ref, plain, diff in conn.execute(
                "SELECT event_id, ts, session_id, position, kind, text, state, closed_by, closed_at, to_, "
                "json_extract(ask, '$.ref'), json_extract(ask, '$.ref_plain'), json_extract(ask, '$.diff_id') "
                "FROM view_asks ORDER BY ts")]


class WaitsProcessor(Processor):
    """view_waits: wait intervals per organization, from of_studio's own rule
    (of_measures.wait_intervals over its model at now), so the rule has one owner and Studio reads
    the rows instead of recomputing. The last WINDOW_D days. An interval still open now has to_ts NULL. Extra fields
    (session_id, source_event) are stored when wait_intervals returns them as dicts.
    Recomputed every 5 minutes when new events arrived, in a detached child under a lock (`refresh_waits`). It took
    1.7 s when written; on 2026-11-09 it read 180,000 events and took 15 to 35 s every 60 s, and the listener's loop,
    its heartbeat and the asks view stood still meanwhile."""
    name = "waits"
    version = "2"  # 2: of_measures.wait_intervals returns dicts with session_id and source_event
    tick_s = 300
    WINDOW_D = 30
    DDL = """
    CREATE TABLE IF NOT EXISTS view_waits (org TEXT, position TEXT, cause TEXT, from_ts TEXT, to_ts TEXT,
      session_id TEXT, source_event TEXT, view_version TEXT);
    """

    def __init__(self):
        self.dirty = True

    def reset(self, conn):
        conn.executescript(self.DDL)
        conn.execute("DELETE FROM view_waits")
        self.dirty = True

    def load_state(self, conn):
        conn.executescript(self.DDL)
        self.dirty = True

    def handle(self, conn, rows):
        self.dirty = self.dirty or bool(rows)
        return False

    def tick(self, conn, now):
        import fcntl
        import subprocess
        if not self.dirty:
            return False
        lock_path = os.path.join(of_events.STATE_DIR, "waits-run.lock")
        fh = open(os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600))
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            return False  # the last refresh is still going; still dirty, so the next tick tries again
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
        self.dirty = False
        here = os.path.dirname(os.path.realpath(__file__))
        log = open(os.open(os.path.join(of_events.STATE_DIR, "listen.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                           0o600), "a")
        subprocess.Popen([sys.executable, "-c", _WAITS_RUNNER, lock_path, here], stdin=subprocess.DEVNULL,
                         stdout=log, stderr=log, start_new_session=True,
                         env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
        log.close()
        return False  # Studio reads view_waits itself; nothing the listener serves changed


def refresh_waits(conn, now):
    """Recompute view_waits (WaitsProcessor) in one transaction."""
    import of_measures
    import of_model
    import of_studio
    w = WaitsProcessor
    conn.executescript(w.DDL)
    rows_all = of_model.load_events(conn, since=of_model.iso(now - w.WINDOW_D * 86400))
    all_orgs = of_studio.orgs()
    out = []
    for org in all_orgs:
        doc, rev = of_model.registry_at(org["repo"], now)
        rows = of_studio.org_rows(rows_all, org, [o for o in all_orgs if o["name"] != org["name"]])
        model = of_model.build(doc, rows, at=now, org=org["name"], rev=rev, window_h=7 * 24)
        for x in of_measures.wait_intervals(model, rows, now - w.WINDOW_D * 86400, now):
            if isinstance(x, dict):
                p, c, t0, t1 = x["position"], x["cause"], x["from"], x["to"]
                sid, src = x.get("session_id"), x.get("source_event")
            else:
                p, c, t0, t1 = x[:4]
                sid, src = None, None
            open_ = t1 is None or t1 >= now - 1
            out.append((org["name"], p, c, of_model.iso(t0), None if open_ else of_model.iso(t1), sid, src, w.version))
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM view_waits")
        conn.executemany("INSERT INTO view_waits VALUES (?,?,?,?,?,?,?,?)", out)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return len(out)


# The detached waits refresh: holds the lock for the whole run, so refreshes never overlap; a failure goes to listen.log.
_WAITS_RUNNER = """
import fcntl, sys, time
fh = open(sys.argv[1], 'w')
try:
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(0)
sys.path.insert(0, sys.argv[2])
import of_listen, of_store
try:
    of_listen.refresh_waits(of_store.connect(), time.time())
except Exception as e:
    print('of listen: waits refresh:', type(e).__name__, e, flush=True)
    sys.exit(1)
"""


class PasteProcessor(Processor):
    """paste_source (ps-v0, bin/of_paste.py) for each new owner prompt with a pasted block, on a 20 s
    timer (the transcript records of the source are ingested by then)."""
    name = "paste"
    version = "1"
    tick_s = 20

    def __init__(self):
        self.pending = False

    def handle(self, conn, rows):
        if any(r["name"] == "of.human_prompt" and "pasted_content" in json.dumps(r.get("payload") or {}) for r in rows):
            self.pending = True
        return False

    def tick(self, conn, now):
        if not self.pending:
            return False
        self.pending = False
        import of_paste
        of_paste.run(conn, since=of_events.now_iso(now - 2 * 86400), log=lambda m: None)
        return False


class BlockedProcessor(Processor):
    """Blocking screens become asks (bin/of_blocked.py). Every tick_s: the screens of the agents that
    are not working (herdr blocked, idle, done, unknown); a new dialog opens an ask, a changed one
    replaces it, a vanished one closes it (reconcile). Permission prompts on Claude Code sessions are
    left to the collector's own permission asks."""
    name = "blocked"
    version = "1"
    tick_s = 15

    def open_asks(self, conn):
        answered = set()
        for (pl,) in conn.execute("SELECT payload FROM event WHERE name = 'of.agent_question_answered' "
                                  "AND payload LIKE '%ask_id%'"):
            p = json.loads(pl or "{}")
            answered |= set(p.get("ask_ids") or [p.get("ask_id")])
        out = {}
        for e, pl in conn.execute("SELECT event_id, payload FROM event WHERE name = 'of.agent_question' "
                                  "AND json_extract(payload, '$.detected_by') = 'blocked' ORDER BY ts"):
            if e not in answered:
                p = json.loads(pl or "{}")
                out[p.get("session") or p.get("pane")] = (e, p.get("fingerprint"))
        return out

    def tick(self, conn, now):
        import subprocess
        import of_blocked
        r = subprocess.run(["herdr", "agent", "list"], capture_output=True, text=True, timeout=10)
        try:
            agents = json.loads(r.stdout)["result"]["agents"]
        except (ValueError, KeyError):
            return False
        index = {}
        try:
            import of_hook
            index = of_hook._registry_index()
        except Exception:  # noqa: BLE001
            pass
        sessions = index.get("sessions") or {}
        live_names = {a.get("name") for a in agents if a.get("agent_status") in ("idle", "done", "working")}
        try:  # a pane's label names its position even before the session is bound (■ name ■)
            pr = subprocess.run(["herdr", "pane", "list"], capture_output=True, text=True, timeout=10)
            labels = {x["pane_id"]: (x.get("label") or "") for x in json.loads(pr.stdout)["result"]["panes"]}
        except (ValueError, KeyError, subprocess.SubprocessError):
            labels = {}
        opened = self.open_asks(conn)
        changed, seen, kept = False, set(), set()
        for a in agents:
            pane = a["pane_id"]
            if a.get("agent_status") == "working":
                continue
            scr = subprocess.run(["herdr", "pane", "read", pane, "--source", "visible", "--lines", "40"],
                                 capture_output=True, text=True, timeout=10)
            d = of_blocked.detect(scr.stdout if scr.returncode == 0 else "")
            if d and d["type"] == "permission" and a.get("agent") == "claude":
                d = None  # the collector's PermissionRequest already asks
            sid = (a.get("agent_session") or {}).get("value")
            key = sid or pane  # one session can show under two pane ids (a stale one): one ask per session
            if key in seen:
                continue
            seen.add(key)
            label = re.sub(r"\s*·\s*❓.*$", "", labels.get(pane, "").strip().strip("■").strip()).strip()
            position = (sessions.get(sid) or [None])[0] or a.get("name") or label or None
            # a parked or finished position stopped on a limit or a login is meant to stay stopped: the balancer
            # neither moves nor wakes it, so it has no ask, and one already open closes below (of_capacity, message
            # 01a1f0a9-d642: such stops of a user organization's parked positions reached the founder as a card)
            if d and d["type"] in ("limit", "login") and position in self.resting_names():
                d = None
            if d and d["type"] == "compact" and a.get("agent") == "claude" and \
                    self.keep_context(pane, sid, position, d, scr.stdout):   # Claude Code's offer only (PR 431 note)
                changed = True
                continue  # answered by the owner's standing rule; an older card for it closes below (dialog gone)
            have = opened.get(key) or opened.get(pane)
            if have and d and d["fingerprint"] == have[1]:
                kept.add(have[0])
                continue
            if d:
                to = d["to"] if d["to"] == "founder" or d["to"] in live_names else "founder"
                fyi = to != "founder" and d["type"] in of_blocked.FYI
                eid = of_events.emit("agent_question", {
                    "detected_by": "blocked", "blocked_type": d["type"], "pane": pane, "session": sid, "agent": a.get("agent"),
                    "fingerprint": d["fingerprint"], "snippet": d["snippet"], "options": d["options"],
                    "folder": d["folder"], "to": to, "since": of_events.now_iso(now),
                    **({"delivery": "fyi"} if fyi else {}),
                    "questions": [{"question": of_blocked.question(d, position),
                                   "options": [f"{o['key']} {o['label']}" for o in d["options"]]}]},
                    position=position, session=sid)
                if fyi and eid:
                    self.send_fyi(to, of_blocked.fyi(d, position, pane, eid))
                changed = True
        # every open blocked ask whose dialog was not seen again this pass closes: gone, changed or false
        for e, _fp in opened.values():
            if e not in kept:
                of_events.emit("agent_question_answered", {"ask_id": e, "ask_ids": [e], "via": "reconcile",
                                                           "reason": "dialog_closed", "by": "of"})
                changed = True
        return changed


    @staticmethod
    def keep_context(pane, sid, position, d, screen):
        """Claude Code's offer of a smaller auto-compact window, answered Keep in this machine's own pane (of_blocked.
        answer_keep), recorded as a blocked ask answered by the owner's standing rule; False leaves it to his card."""
        import subprocess
        import of_blocked
        if of_blocked.compact_rows(of_blocked.tail(screen, 40)) is None:
            return False
        tried = BlockedProcessor._keep_tried
        if d["fingerprint"] in tried:
            return False  # one attempt per dialog: a failed one is the owner's card (of_review on PR 431)
        tried.add(d["fingerprint"])

        def read():
            try:
                r = subprocess.run(["herdr", "pane", "read", pane, "--source", "visible", "--lines", "40"],
                                   capture_output=True, text=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                return None
            return of_blocked.tail(r.stdout, 40) if r.returncode == 0 else None

        def press(k):
            try:
                return subprocess.run(["herdr", "pane", "send-keys", pane, k], capture_output=True, text=True,
                                      timeout=10).returncode == 0
            except (OSError, subprocess.SubprocessError):
                return False
        ok, why = of_blocked.answer_keep(read, press)
        if not ok:
            print(f"of listen: compaction offer in {pane}: {why}; the owner's card is filed", flush=True)
            return False
        eid = of_events.emit("agent_question", {
            "detected_by": "blocked", "blocked_type": "compact", "pane": pane, "session": sid,
            "fingerprint": d["fingerprint"], "snippet": d["snippet"], "options": d["options"], "to": "founder",
            "since": of_events.now_iso(), "questions": [{"question": of_blocked.question(d, position), "options": []}]},
            position=position, session=sid)
        of_events.emit("agent_question_answered", {"ask_id": eid, "ask_ids": [eid], "via": "rule", "by": "of",
                                                   "answer": "Keep (the current window)", "keys": ["Enter"],
                                                   "basis": of_blocked.KEEP_BASIS}, position=position, session=sid)
        return True

    _keep_tried = set()   # the compaction offers' fingerprints this listener tried to answer Keep

    _resting = (None, frozenset())

    def resting_names(self):
        """The positions whose registry status is parked or finished (of_capacity.RESTING), read from the registries
        again only when one of them changed."""
        import importlib.machinery
        import importlib.util
        orgs = os.environ.get("OF_ORGS") or of_events.config_path("orgs.yaml")
        here = os.path.dirname(os.path.realpath(__file__))
        try:
            loader = importlib.machinery.SourceFileLoader("of_cli_blocked", os.path.join(here, "of"))
            spec = importlib.util.spec_from_loader("of_cli_blocked", loader)
            of = importlib.util.module_from_spec(spec)
            loader.exec_module(of)
            files = [orgs] + [r for _, r, _ in of.org_registries(orgs)]
            stamp = tuple((f, os.path.getmtime(f)) for f in files if os.path.exists(f))
            if self._resting[0] == stamp:
                return self._resting[1]
            positions, _ = of.load_positions(orgs)
        except (SystemExit, Exception):  # noqa: BLE001 - no registry read: none counts as resting, asks go as before
            return frozenset()
        rest = ("parked", "finished")
        names = frozenset(p["name"] for p in positions if str(p.get("status") or "").split("#")[0].strip() in rest)
        self._resting = (stamp, names)
        return names

    @staticmethod
    def send_fyi(to, text):
        """Into the receiver's inbox, never its pane: a detached `of send --fyi` from the detector's owner."""
        import subprocess
        here = os.path.dirname(os.path.realpath(__file__))
        log = open(os.open(os.path.join(of_events.STATE_DIR, "listen.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                           0o600), "a")
        subprocess.Popen([sys.executable, os.path.join(here, "of"), "send", to, "--from",
                          "organic-framework-build:of_platform", "--fyi", text],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                         env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
        log.close()


class ViewsProcessor(Processor):
    """Pinned views (bin/of_views.py) are restored when one of their agents was moved away: every
    tick_s, `of view apply <name>` per view (a no-op when the view holds)."""
    name = "views"
    version = "1"
    tick_s = 60

    def tick(self, conn, now):
        import subprocess
        import sys
        import of_views
        of_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "of")
        for v in of_views.load():
            subprocess.run([sys.executable, of_path, "view", "apply", v["name"], "--quiet"], capture_output=True,
                           timeout=120, env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
        return False


class PulseProcessor(Processor):
    """The pulse as a timer inside the listener (RFC 0001 §5.1a): every PULSE_S it runs one pulse
    (reminders, response windows, stalls, escalations, reconcile, the herdr counts) in a child
    process, so a failure there never stops the listener; the 5-minute launchd job goes away.
    Its outputs are events, which the other processors then fold into their views.
    The child is detached and holds a lock (2026-11-09: a pulse took 75 to 240 s, and while the listener waited on it
    the views, the heartbeat and the deliveries at a turn's end stood still); a pulse still running skips the next."""
    name = "pulse"
    version = "1"
    tick_s = 300
    TIMEOUT_S = 280

    def tick(self, conn, now):
        import fcntl
        import subprocess
        lock_path = os.path.join(of_events.STATE_DIR, "pulse-run.lock")
        fh = open(os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600))
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            return False  # the last pulse is still going
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
        of_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "of")
        log = open(os.open(os.path.join(of_events.STATE_DIR, "pulse.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                           0o600), "a")
        subprocess.Popen([sys.executable, "-c", _PULSE_RUNNER, lock_path, of_path, str(self.TIMEOUT_S)],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                         env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
        log.close()
        return False  # its events reach the views through the log


# The detached pulse: holds the lock for the whole run, so pulses never overlap; its output goes to pulse.log only when
# it fails (a quiet pulse prints its errors).
_PULSE_RUNNER = """
import fcntl, subprocess, sys, time
fh = open(sys.argv[1], 'w')
try:
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(0)
stamp = lambda: time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
import os, signal
# its own process group, killed whole on a timeout: a child of the pulse (of model escalations) must not live on
# as an orphan (2026-11-10: four copies of 0.5-0.9 GB each piled up, one per pulse, reported by platform_owner)
p = subprocess.Popen([sys.executable, sys.argv[2], 'pulse', '--quiet'], stdout=subprocess.PIPE,
                     stderr=subprocess.STDOUT, text=True, start_new_session=True)
try:
    out, _ = p.communicate(timeout=float(sys.argv[3]))
except subprocess.TimeoutExpired:
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        pass
    p.wait()
    print(stamp(), 'of pulse timed out after', sys.argv[3], 's; its process group killed', flush=True)
    sys.exit(1)
out = (out or '').strip()
if p.returncode != 0 or out:
    print(stamp(), 'of pulse rc', p.returncode, out[-2000:], flush=True)
sys.exit(p.returncode)
"""


class GithubProcessor(Processor):
    """Every 30 minutes: `of import github --org all` (incremental, since the last imported merge) as a
    detached process with its own lock, so the listener never waits on GitHub (of_architect, 2026-11-06:
    work records stopped at the last hand-run import). gh's own auth; no SSH agent."""
    name = "github"
    version = "1"
    tick_s = 1800
    # A run starts hundreds of gh processes across the organizations' repositories: 975 in one hour on 2026-11-22,
    # 38% of the Mac's gh starts while it paged (a user organization's structure keeper, message 01a20444-9136). While
    # the load stands above BUSY_LOAD x cores a run waits, for at most MAX_WAIT_S, and runs at background priority.
    BUSY_LOAD = 2.0
    MAX_WAIT_S = 7200
    last_run = 0.0

    def handle(self, conn, rows):
        return False

    def busy(self, load=None, cores=None):
        load = os.getloadavg()[0] if load is None else load
        return load > self.BUSY_LOAD * (cores or os.cpu_count() or 1)

    def tick(self, conn, now):
        import fcntl
        import subprocess
        self.last_run = self.last_run or now   # a start under load waits too
        if self.busy() and now - self.last_run < self.MAX_WAIT_S:
            return False   # the next tick looks again
        lock_path = os.path.join(of_events.STATE_DIR, "github-import.lock")
        fh = open(os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600))
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            return False  # the last run is still going
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()
        here = os.path.dirname(os.path.realpath(__file__))
        log = open(os.open(os.path.join(of_events.STATE_DIR, "github-import.log"),
                           os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a")
        subprocess.Popen(["nice", "-n", "15", sys.executable, "-c", _GH_RUNNER, lock_path, os.path.join(here, "of")],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                         env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
        log.close()
        self.last_run = now
        return False


# The detached runner: holds the lock for the whole import, so ticks never overlap.
_GH_RUNNER = """
import fcntl, os, subprocess, sys, time
fh = open(sys.argv[1], 'w')
try:
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    sys.exit(0)
print(time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'of import github --org all', flush=True)
rc = subprocess.run([sys.executable, sys.argv[2], 'import', 'github', '--org', 'all']).returncode
sys.exit(subprocess.run([sys.executable, sys.argv[2], 'import', 'git', '--org', 'all']).returncode or rc)
"""


class ArchiveProcessor(Processor):
    """Every hour: `of archive` as a detached process (bin/of_archive.py; its own lock), so the raw tier keeps every
    transcript as produced (docs/storage.md, "Raw tier: every byte"; hand-over b097ba7c). 10 minutes of work when the
    machine is idle (load below its cores), 1 minute otherwise, at background priority; a backlog goes on next hour."""
    name = "archive"
    version = "1"
    tick_s = 3600

    def handle(self, conn, rows):
        return False

    def tick(self, conn, now):
        import subprocess
        try:
            idle = os.getloadavg()[0] < (os.cpu_count() or 1)
        except OSError:
            idle = False
        here = os.path.dirname(os.path.realpath(__file__))
        log = open(os.open(os.path.join(of_events.STATE_DIR, "archive.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                           0o600), "a")
        subprocess.Popen([sys.executable, os.path.join(here, "of"), "archive", "--quiet", "--budget",
                          "600" if idle else "60"], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         start_new_session=True, env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
        log.close()
        return False


class PromptOriginProcessor(Processor):
    """Every 15 minutes: of_hook.correct_prompt_origins over the last day, for prompts the hook's live check could
    not tell (the scheduler's fire record written after the hook read the transcript)."""
    name = "prompt_origin"
    version = "1"
    tick_s = 900

    def handle(self, conn, rows):
        return False

    def tick(self, conn, now):
        import of_hook
        since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - 26 * 3600))
        return bool(of_hook.correct_prompt_origins(conn, since)[2])


class StudioCacheProcessor(Processor):
    """Every 10 minutes: of_studio's page cache refresh (`of studio --render-cache`, docs/studio.md 5.1a), started
    through of_studio._refresh_in_background in a short child process. It skips while the lock file .refresh names a
    running refresh, and the listener never imports Studio's model (of_studio, 2026-11-07)."""
    name = "studio_cache"
    version = "1"
    tick_s = 600

    def tick(self, conn, now):
        import subprocess
        here = os.path.dirname(os.path.realpath(__file__))
        code = f"import sys; sys.path.insert(0, {here!r}); import of_studio; of_studio._refresh_in_background()"
        subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True,
                         env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
        return False


class DeliverProcessor(Processor):
    """The founder's answers at the end of the receiver's turn (acme_org, 2026-11-09: four answers given at 10:35-10:37Z
    while platform_owner was busy reached it at 10:56 and 11:01, with the pulse as the only retry). It notes who should
    get the founder's answers from the popup, the questions routed to a unit owner and the escalation notes; when one of
    them ends a turn (of.turn_end) with something still unrecorded in its pane, it starts `of asks deliver <position>`
    as a child, which types it (of_asks_answer.redeliver_pass). Nothing is typed from here."""
    name = "deliver"
    version = "1"
    from_now = True    # old turns are history: a new cursor starts at the end of the log, and reset reads the last day
    FRESH_S = 300      # a turn end seen later than this (a paused catch-up) leaves it to the pulse
    GAP_S = 20         # at most one child per position in this time
    SPAWNS = 3         # children per item; after that the pulse alone retries it
    KEEP_S = 24 * 3600

    def __init__(self):
        self.due = {}      # position -> [{marks, since, ask, state, n}]
        self.started = {}  # position -> when its last child started

    def reset(self, conn):
        self.load_state(conn)

    def load_state(self, conn):
        since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - self.KEEP_S))
        self.due = {}
        rows = []
        for e, ts, n, pos, pl, org in conn.execute(
                "SELECT event_id, ts, name, position, payload, org FROM event WHERE ts >= ? AND name IN "
                "('of.agent_question_answered', 'of.agent_question', 'of.permission_request', 'of.ask_escalated') "
                "ORDER BY ts", (since,)):
            try:
                rows.append({"event_id": e, "ts": ts, "name": n, "position": pos, "payload": json.loads(pl or "{}"),
                             "org": org})
            except ValueError:
                continue
        for r in rows:
            self.note(conn, r)

    def note(self, conn, r):
        import of_asks_answer as aa
        n, p = r["name"], r.get("payload") or {}

        def due(pos, marks, ask=None, state=None):
            self.due.setdefault(pos, []).append({"marks": marks, "since": r["ts"], "ask": ask, "state": state, "n": 0})
        if n == "of.agent_question_answered":
            if p.get("via") in aa.TYPED_VIAS and p.get("answer") and not p.get("keys") and not p.get("verdict"):
                for pos, _, ids in aa.recipients(conn, p.get("ask_ids") or [p.get("ask_id")]):
                    due(pos, [aa.answer_mark(a) for a in ids])
        elif n in ("of.agent_question", "of.permission_request"):
            import of_asks
            to = of_asks.bare_to(p.get("to"), r.get("org"))
            if to != "founder" and to != r.get("position") and not aa.inbox_only(p):
                due(to, [aa.routed_mark(r["event_id"])], r["event_id"], "open")
        elif n == "of.ask_escalated":
            if p.get("ask_id") and p.get("from_owner") and p.get("rule") != "by_owner":
                due(p["from_owner"], [aa.escalated_mark(p["ask_id"])], p["ask_id"], "escalated")

    def closed(self, conn, d):
        """A routed ask no longer open (or escalated), by the asks view, which the listener updates first."""
        if not d["ask"]:
            return False
        try:
            row = conn.execute("SELECT state FROM view_asks WHERE event_id = ?", (d["ask"],)).fetchone()
        except Exception:  # noqa: BLE001 - no view yet: the child decides
            return False
        return bool(row) and row[0] != d["state"]

    def handle(self, conn, rows):
        import calendar
        import of_asks_answer as aa
        now = time.time()
        keep = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - self.KEEP_S))
        for r in rows:
            if r["name"] != "of.turn_end":
                self.note(conn, r)
                continue
            pos = r.get("position")
            if pos not in self.due:
                continue
            left = [d for d in self.due[pos] if d["since"] >= keep and d["n"] < self.SPAWNS
                    and not self.closed(conn, d) and not aa.delivered(conn, pos, d["marks"], d["since"])]
            if not left:
                del self.due[pos]
                continue
            self.due[pos] = left
            try:
                age = now - calendar.timegm(time.strptime(r["ts"][:19], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                continue
            if age > self.FRESH_S or now - self.started.get(pos, 0) < self.GAP_S:
                continue
            self.started[pos] = now
            for d in left:
                d["n"] += 1
            self.spawn(pos)
        return False

    def spawn(self, position):
        import subprocess
        here = os.path.dirname(os.path.realpath(__file__))
        log = open(os.open(os.path.join(of_events.STATE_DIR, "deliver.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                           0o600), "a")
        subprocess.Popen([sys.executable, os.path.join(here, "of"), "asks", "deliver", position],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                         env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
        log.close()


class CaseProcessor(Processor):
    """The founder's case for the desk (docs/desk.md). After he types into an agent's pane, once QUIET_S pass without
    another prompt and at most every GAP_S, and every REFRESH_S while his last prompt is under ACTIVE_S old, it starts
    `of desk case --infer` as a detached child (a small model, about 20 s; the child holds a lock). The record,
    of.case_inferred, is what the desk router reads; nothing here moves a pane."""
    name = "case"
    version = "1"
    from_now = True
    tick_s = 10
    QUIET_S = 30
    GAP_S = 120
    REFRESH_S = 600
    ACTIVE_S = 1800

    def __init__(self):
        self.last_prompt = 0.0  # when he last typed into a pane
        self.pending = False    # he typed since the last inference started
        self.started = 0.0

    def reset(self, conn):
        self.load_state(conn)

    def load_state(self, conn):
        import of_case
        ts = of_case.last_prompt_ts(conn)
        self.last_prompt = of_case._ts(ts) if ts else 0.0
        last = of_case.latest(conn, max_age_s=self.REFRESH_S)
        self.started = of_case._ts(last["ts"]) if last else 0.0

    @staticmethod
    def his(r):
        import of_case
        p = r.get("payload") or {}
        return (r["name"] == "of.human_prompt" and r.get("position") and p.get("origin") in ("human", "paste")
                and p.get("channel") != "asks-ui" and not (p.get("text") or "").startswith(of_case.ANSWER))

    def handle(self, conn, rows):
        import of_case
        for r in rows:
            if self.his(r):
                self.last_prompt = max(self.last_prompt, of_case._ts(r["ts"]))
                self.pending = True
        return False

    def tick(self, conn, now):
        if not self.last_prompt or now - self.last_prompt < self.QUIET_S or now - self.started < self.GAP_S:
            return False  # nothing yet, he may still be typing, or one ran a moment ago
        if self.pending:
            trigger = "prompt"
        elif now - self.last_prompt < self.ACTIVE_S and now - self.started >= self.REFRESH_S:
            trigger = "refresh"
        else:
            return False
        self.pending, self.started = False, now
        self.spawn(trigger)
        return False

    def spawn(self, trigger):
        import subprocess
        here = os.path.dirname(os.path.realpath(__file__))
        log = open(os.open(os.path.join(of_events.STATE_DIR, "case.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                           0o600), "a")
        subprocess.Popen([sys.executable, os.path.join(here, "of"), "desk", "case", "--infer", "--trigger", trigger],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                         env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
        log.close()


class CorrectionsProcessor(Processor):
    """The founder's corrections (RFC 0006 §2.4; bin/of_corrections.py). After he types into a position's pane, once
    QUIET_S pass and at most every GAP_S, it starts `of corrections judge` as a detached child (a small model per
    prompt; the child holds a lock and skips what it judged). Every DIGEST_EVERY_S from DIGEST_HOUR UTC it starts
    `of corrections digest`, which judges what is left and sends each structure keeper the day's digest once."""
    name = "corrections"
    version = "1"
    from_now = True
    tick_s = 30
    QUIET_S = 60
    GAP_S = 120
    DIGEST_EVERY_S = 3600

    def __init__(self):
        self.last_prompt, self.pending, self.started, self.digest_at = 0.0, False, 0.0, 0.0

    def handle(self, conn, rows):
        import of_case
        for r in rows:
            if CaseProcessor.his(r):
                self.last_prompt = max(self.last_prompt, of_case._ts(r["ts"]))
                self.pending = True
        return False

    def tick(self, conn, now):
        import of_corrections
        if self.pending and now - self.last_prompt >= self.QUIET_S and now - self.started >= self.GAP_S:
            self.pending, self.started = False, now
            self.spawn("judge")
        elif time.gmtime(now).tm_hour >= of_corrections.DIGEST_HOUR and now - self.digest_at >= self.DIGEST_EVERY_S:
            self.digest_at = now
            self.spawn("digest")
        return False

    def spawn(self, action):
        import subprocess
        here = os.path.dirname(os.path.realpath(__file__))
        log = open(os.open(os.path.join(of_events.STATE_DIR, "corrections.log"),
                           os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a")
        subprocess.Popen([sys.executable, os.path.join(here, "of"), "corrections", action, "--quiet"],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                         env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
        log.close()


class AccessDigestProcessor(Processor):
    """The weekly access review (docs/access.md §5; bin/of_access.py): from DIGEST_HOUR UTC on Mondays, at most every
    EVERY_S, it starts `of access digest --quiet` as a detached child, which sends each organization's structure keeper
    its week's digest once (a state file per ISO week)."""
    name = "access_digest"
    version = "1"
    from_now = True
    tick_s = 300
    EVERY_S = 3600

    def __init__(self):
        self.at = 0.0

    def handle(self, conn, rows):
        return False

    def tick(self, conn, now):
        import of_access
        if of_access.due(now) and now - self.at >= self.EVERY_S:
            self.at = now
            import subprocess
            here = os.path.dirname(os.path.realpath(__file__))
            log = open(os.open(os.path.join(of_events.STATE_DIR, "access-digest.log"),
                               os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a")
            subprocess.Popen([sys.executable, os.path.join(here, "of"), "access", "digest", "--quiet"],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                             env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
            log.close()
        return False


class TempoProcessor(Processor):
    """Work booked for later, judged for a basis (bin/of_tempo.py; hand-over 277de578). It reads only its own names (a
    session's crons and background tasks, messages, asks), by its own cursor and within its time budget; once any of
    them came, at most every GAP_S, it starts `of tempo judge` as a detached child (a small model per position and run;
    the child holds a lock and skips what it judged). No quiet period: these names come at every turn end, so a busy
    organization was never quiet for one (of_review on organic-framework PR 288); an item that comes during a run is
    the next run's. The pulse types a booking with no basis into its position's pane."""
    name = "tempo"
    version = "1"
    from_now = True
    tick_s = 30
    GAP_S = 120

    def __init__(self):
        import of_tempo
        self.catch_up_names = tuple(of_tempo.NAMES)
        self.pending, self.started = False, 0.0

    def handle(self, conn, rows):
        if rows:
            self.pending = True
        return False

    def tick(self, conn, now):
        if self.pending and now - self.started >= self.GAP_S:
            self.pending, self.started = False, now
            import subprocess
            here = os.path.dirname(os.path.realpath(__file__))
            log = open(os.open(os.path.join(of_events.STATE_DIR, "tempo.log"),
                               os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a")
            subprocess.Popen([sys.executable, os.path.join(here, "of"), "tempo", "judge", "--quiet"],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                             env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
            log.close()
        return False


class HandoverScopesProcessor(Processor):
    """handover-scopes.json: the accepted and blocked hand-overs with a scope, rewritten when a hand-over event comes,
    for the hook's mandate check, which reads it instead of the store (the read path's 3.6; of_handover.write_scopes)."""
    name = "handover_scopes"
    version = "1"

    def __init__(self):
        self.dirty = True  # written once at start

    def reset(self, conn):
        self.dirty = True

    def handle(self, conn, rows):
        if any(str(r.get("name") or "").startswith("of.handover_") for r in rows):
            self.dirty = True
        return False

    def flush(self, conn):
        if not self.dirty:
            return False
        import of_handover
        of_handover.write_scopes(of_handover.project(of_handover.load(conn)))
        self.dirty = False
        return False


PROCESSORS = [AsksProcessor, WaitsProcessor, PasteProcessor, BlockedProcessor, ViewsProcessor, PulseProcessor,
              DeliverProcessor, CaseProcessor, CorrectionsProcessor, AccessDigestProcessor, TempoProcessor,
              PromptOriginProcessor,
              GithubProcessor, StudioCacheProcessor, HandoverScopesProcessor, ArchiveProcessor]
try:  # of_capacity's processor (bin/of_capacity.py, owned there): spend by message id, the balancer detached
    import of_capacity
    PROCESSORS.append(of_capacity.ListenerProcessor)
except Exception:  # noqa: BLE001 - the listener runs without it rather than not at all
    pass


# ---------------------------------------------------------------------- the loop

def fetch(conn, after, limit=None, top=None, names=None):
    """The events after rowid AFTER, up to TOP, in log order; NAMES: only those (a fold view's own)."""
    out = []
    only = f"AND name IN ({','.join('?' * len(names))}) " if names else ""
    for rowid, e, ts, n, s, pos, src, org, pl, host in conn.execute(
            "SELECT rowid, event_id, ts, name, session_id, position, source, org, payload, host_id FROM event "
            "WHERE rowid > ? "
            f"AND rowid <= ? {only}ORDER BY rowid LIMIT ?",
            (after, top if top is not None else 2 ** 62, *(names or ()), limit or BATCH)):
        try:
            payload = json.loads(pl) if pl else {}
        except ValueError:
            payload = {}
        out.append({"rowid": rowid, "event_id": e, "ts": ts, "name": n, "session_id": s, "position": pos,
                    "source": src, "org": org, "payload": payload, "host_id": host})
    return out


def prune(conn, processors):
    """Forget the cursors of processors that no longer run, and a removed fold view's rows: a leftover cursor reads as
    a processor days behind (2026-11-17: fold:escalated, removed when the pulse took to reading escalations by their
    fixed ids, showed as failed in Studio's Health tab for three days; of_architect, message 01a1e82c-d448). Returns
    the names forgotten."""
    names = {p.name for p in processors}
    gone = [r[0] for r in conn.execute("SELECT processor FROM listener_cursor").fetchall() if r[0] not in names]
    for name in gone:
        conn.execute("DELETE FROM listener_cursor WHERE processor = ?", (name,))
        if name.startswith("fold:"):
            for table in ("view_fold_meta", "view_fold"):
                try:
                    conn.execute(f"DELETE FROM {table} WHERE view = ?", (name[len("fold:"):],))
                except __import__("sqlite3").OperationalError:
                    pass   # no fold tables yet
    return gone


def prepare(conn, processors, rebuild=False):
    """Cursor per processor; a new version (or rebuild) resets the view and replays from 0."""
    conn.executescript(DDL)
    cursors = {}
    for p in processors:
        row = conn.execute("SELECT version, last_rowid FROM listener_cursor WHERE processor = ?", (p.name,)).fetchone()
        if rebuild or not row or row[0] != p.version:
            start = 0
            if getattr(p, "from_now", False):  # no view to build: a new cursor starts at the end of the log
                start = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM event").fetchone()[0]
            elif not hasattr(p, "settle") and not getattr(p, "own_readiness", False) and _has_meta(conn):
                # a view without a shadow is half-built until caught up (a fold view says its own: of_fold.read)
                conn.execute("DELETE FROM listener_meta WHERE key = 'heartbeat'")
                p.in_place = True
            p.reset(conn)
            conn.execute("INSERT OR REPLACE INTO listener_cursor VALUES (?,?,?,?)",
                         (p.name, p.version, start, of_events.now_iso()))
            cursors[p.name] = start
        else:
            if hasattr(p, "load_state"):
                p.load_state(conn)
            cursors[p.name] = row[1]
    return cursors


def step(conn, processors, cursors, beat=None, locks=None, budget=False):
    """One pass: every processor catches up from its cursor. Returns True if any view changed.
    budget: each processor stops after the batch that takes it past its `budget_s`, its cursor saved and its view
    flushed, and the next processor runs; it goes on from its cursor in the next step (organic-framework-build
    reports/of-log-readers-2026-11-14.md, 3.4). The service's steps pass it; the first catch-up does not, since the
    heartbeat after it tells readers every view is whole.
    beat(): called between batches, so a long catch-up keeps the heartbeat while the views it serves
    stay whole (the asks view rebuilds in shadow tables; without the beat, readers fell back to the
    slow projection for the length of the rebuild, 2026-11-06).
    A step takes the events in the log when it starts; what arrives meanwhile is the next step's, and a processor
    with flush() writes its view once per step (2026-11-09: the asks view was projected again for every batch, 5 to 8
    s each, and while events kept arriving the loop stayed inside one step for 40 s, with no heartbeat)."""
    changed = False
    top = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM event").fetchone()[0]
    for p in processors:
        if not reads_events(p):  # a timer only (views, pulse, ...): nothing to fold, its cursor follows the log
            if cursors[p.name] < top:
                cursors[p.name] = top
                try:
                    conn.execute("UPDATE listener_cursor SET last_rowid = ?, updated_at = ? WHERE processor = ?",
                                 (top, of_events.now_iso(), p.name))
                except __import__("sqlite3").OperationalError:
                    pass
            continue
        lock = (locks or {}).get(p.name)
        if lock is not None and not lock.acquire(blocking=False):
            continue  # its tick runs in the tick thread: it catches up from its cursor on a later pass
        try:
            own = getattr(p, "budget_s", None)
            changed |= _catch_up(conn, p, cursors, top, beat,
                                 budget_s=(BUDGET_S if own is None else own) if budget else None)
        finally:
            if lock is not None:
                lock.release()
    return changed


def reads_events(p):
    """Does the processor fold events, or only run a timer? A timer's cursor need not wait for its tick: its tick held
    its lock most of each minute, so `of health` saw it a day behind (2026-11-14)."""
    cls = type(p)
    return cls.handle is not Processor.handle or hasattr(p, "flush") or hasattr(p, "settle")


def _catch_up(conn, p, cursors, top, beat, budget_s=None):
    changed, began, yielded = False, time.time(), False
    while True:
        if beat:
            beat()
        # a fold view, and the asks view, read only their own names
        names = getattr(p, "fold_names", None) or getattr(p, "catch_up_names", None)
        rows = fetch(conn, cursors[p.name], top=top, names=names)
        if not rows:
            if names and cursors[p.name] < top:  # none of its names up to TOP: its cursor moves there all the same
                if getattr(p, "fold_names", None):
                    changed |= bool(p.handle(conn, [], upto=top))
                elif hasattr(p, "max_rowid"):
                    p.max_rowid = max(p.max_rowid, top)  # what the view reflects: everything up to TOP
                cursors[p.name] = top
                try:
                    conn.execute("UPDATE listener_cursor SET last_rowid = ?, updated_at = ? WHERE processor = ?",
                                 (top, of_events.now_iso(), p.name))
                except __import__("sqlite3").OperationalError:
                    pass
            break
        t0 = time.time()
        changed |= bool(p.handle(conn, rows))  # a busy store raises here: the batch is handled again next pass
        slow(p.name, "handle", t0, len(rows))
        cursors[p.name] = rows[-1]["rowid"]
        try:  # the stored cursor may lag one pass behind on a busy store; the run keeps its own
            conn.execute("UPDATE listener_cursor SET last_rowid = ?, updated_at = ? WHERE processor = ?",
                         (cursors[p.name], of_events.now_iso(), p.name))
        except __import__("sqlite3").OperationalError:
            pass
        if budget_s is not None and time.time() - began >= budget_s:
            yielded = True   # the rest is the next step's, after the other processors had theirs
            break
    if yielded and getattr(p, "defer", False):
        pass   # not caught up: the shadow is swapped in only once it is
    elif getattr(p, "defer", False):  # caught up after a reset or a restart: project once, swap the shadow in
        t0 = time.time()
        changed |= bool(p.settle(conn))
        slow(p.name, "settle", t0)
    elif hasattr(p, "flush"):
        t0 = time.time()
        changed |= bool(p.flush(conn))
        slow(p.name, "flush", t0)
    return changed


# ---------------------------------------------------------------------- the service and its watchdog

LAUNCHD_LABEL = "dev.organic-framework.listen"
WATCHDOG_LABEL = "dev.organic-framework.listen-watchdog"
# One check a minute. A live listener whose heartbeat is older than STALE_S, or whose timers have not turned for
# ticks_stale (a tick may run long, never this long), is restarted after failing_for, at most once in restart_gap.
WATCHDOG = {"every": 60, "failing_for": 180, "restart_gap": 900, "ticks_stale": 900}


def heartbeat_path():
    return os.path.join(of_events.STATE_DIR, "listen.heartbeat")


def ticks_path():
    return os.path.join(of_events.STATE_DIR, "listen.ticks")


def _touch(path):
    try:
        os.utime(path, None)
    except FileNotFoundError:
        try:
            with open(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600), "w"):
                pass
        except OSError:
            pass
    except OSError:
        pass


def _plist(label, argv, log, extra=""):
    from xml.sax.saxutils import escape
    args = "".join(f"<string>{escape(a)}</string>" for a in argv)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>{args}</array>
  <key>EnvironmentVariables</key><dict><key>OF_AUTOMATION</key><string>1</string><key>SSH_AUTH_SOCK</key><string></string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin</string></dict>
  <key>ProcessType</key><string>Standard</string>
{extra}  <key>StandardOutPath</key><string>{escape(log)}</string>
  <key>StandardErrorPath</key><string>{escape(log)}</string>
</dict></plist>
"""


def launchd_plists(python=None, of_bin=None):
    """{label: plist text}: the listener at ProcessType Standard, with no Nice and no low-priority I/O, and its
    watchdog every minute (hand-over 6d9a8305, docs/health.md). Its readers are people and agents waiting: as a
    Background job with Nice 5 it lost the CPU to the very load it was meant to keep up with, as of-server did at Nice
    10 (of_server's 3981069). Its children lower themselves where they are batch work."""
    python = python or sys.executable
    of_bin = of_bin or os.path.join(os.path.dirname(os.path.realpath(__file__)), "of")
    log = os.path.join(of_events.STATE_DIR, "listen.log")
    listener = _plist(LAUNCHD_LABEL, [python, of_bin, "listen", "run"], log,
                      "  <key>RunAtLoad</key><true/>\n  <key>KeepAlive</key><true/>\n"
                      "  <key>ThrottleInterval</key><integer>10</integer>\n")
    watch = _plist(WATCHDOG_LABEL, [python, of_bin, "listen", "watchdog"],
                   os.path.join(of_events.STATE_DIR, "listen-watchdog.log"),
                   f"  <key>StartInterval</key><integer>{WATCHDOG['every']}</integer>\n")
    return {LAUNCHD_LABEL: listener, WATCHDOG_LABEL: watch}


def install_launchd(uninstall=False, run=None, plists=None, agents_dir=None):
    """`of listen install|uninstall`: write (or remove) the two launchd agents and load them; a unit already as
    written and loaded is left running. Returns one line per agent."""
    import subprocess
    run = run or subprocess.run
    domain = f"gui/{os.getuid()}"
    agents_dir = agents_dir or os.path.expanduser("~/Library/LaunchAgents")
    out = []
    for label, text in (plists or launchd_plists()).items():
        path = os.path.join(agents_dir, f"{label}.plist")
        try:
            with open(path) as fh:
                same = fh.read() == text
        except OSError:
            same = False
        if same and not uninstall and run(["launchctl", "print", f"{domain}/{label}"],
                                          capture_output=True).returncode == 0:
            out.append(f"{label}: unchanged")
            continue
        run(["launchctl", "bootout", f"{domain}/{label}"], capture_output=True)
        if uninstall:
            if os.path.exists(path):
                os.remove(path)
            out.append(f"removed {label}")
            continue
        os.makedirs(agents_dir, exist_ok=True)
        with open(path + ".tmp", "w") as fh:
            fh.write(text)
        os.replace(path + ".tmp", path)
        for _ in range(4):  # right after a bootout launchd may answer "Input/output error" for a moment
            r = run(["launchctl", "bootstrap", domain, path], capture_output=True)
            if r.returncode == 0:
                break
            time.sleep(1.5)
        out.append(f"{label}: {'loaded' if r.returncode == 0 else 'not loaded (launchctl ' + str(r.returncode) + ')'}")
    return out


def systemd_units(python=None, of_bin=None):
    """The listener stays running and the timer starts one watchdog check every WATCHDOG['every'] seconds."""
    import of_systemd
    return {
        "of-listen.service": of_systemd.service("listen", python, of_bin),
        "of-listen-watchdog.service": of_systemd.service("listen", python, of_bin, watchdog=True),
        "of-listen-watchdog.timer": (
            "[Unit]\nDescription=Check the Organic Framework listener\n\n[Timer]\n"
            f"OnBootSec=60\nOnUnitActiveSec={WATCHDOG['every']}\n\n[Install]\nWantedBy=timers.target\n"),
    }


def install_systemd(uninstall=False, run=None, units=None, units_dir=None):
    """Enable the listener and its timer together so the watchdog follows the listener across logins."""
    import of_systemd
    return of_systemd.install(systemd_units() if units is None else units,
                              ("of-listen.service", "of-listen-watchdog.timer"), uninstall, run, units_dir)


def watchdog(now=None, probe=None, restart=None, dump=None, state_path=None, log=print, settings=None, pid=None,
             run=None):
    """One check, run every minute (`of listen watchdog`): of_server.watch for the listener (hand-over
    6d9a8305; the same rule as of-server's and the relays'). A running listener whose heartbeat is older
    than STALE_S or whose timers have not turned for `ticks_stale`, through `failing_for` seconds of checks, is
    restarted at most once in `restart_gap`, its threads' stacks first written to listen.log. Its pid is the manager's,
    never a file's: a dead pid left in listen.pid by `of listen rebuild` or a terminal run made a stuck listener look
    stopped (of_review on PR 145). Returns ok, failing, waiting, stopped, restarted or restart_failed.
    On the agent host a root daemon watches each organization user's listener (deploy/host/plane.py): OF_STATE is that
    user's state, OF_LISTEN_TARGET its system daemon (system/<label>), OF_LISTEN_WATCHDOG_STATE a file outside the
    user's home; it then writes no event into the user's store, which would leave root-owned files there."""
    import of_server
    s = dict(WATCHDOG, **(settings or {}))
    now = time.time() if now is None else now
    target = os.environ.get("OF_LISTEN_TARGET") or ""
    domain, _, label = target.rpartition("/") if target else (None, "", LAUNCHD_LABEL)
    as_root_for_user = bool(target) and os.geteuid() == 0
    state_path = state_path or os.environ.get("OF_LISTEN_WATCHDOG_STATE") or os.path.join(of_events.STATE_DIR,
                                                                                         "listen-watchdog.json")
    if probe is None:
        def probe():  # two files' times by stat: never an open, so no store file is made and no FIFO can block it
            def age(path):
                try:
                    return now - os.stat(path).st_mtime
                except OSError:
                    return None
            hb, ticks = age(heartbeat_path()), age(ticks_path())
            return hb is not None and hb <= STALE_S and (ticks is None or ticks <= s["ticks_stale"])
    user_systemd = sys.platform.startswith("linux") and not target

    def pid_of():
        if pid is not None:
            return pid
        if user_systemd:
            import of_systemd
            return of_systemd.main_pid("of-listen.service", run=run)
        import of_relay_client
        return of_relay_client.launchd_pid(label, domain)

    if user_systemd and restart is None:
        import subprocess
        run = run or subprocess.run

        def restart():
            return run(["systemctl", "--user", "restart", "of-listen.service"],
                       capture_output=True, timeout=30, check=True)

    def stamp(m):
        log(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))} {m}")
    import inspect
    kw, params = {}, inspect.signature(of_server.watch).parameters
    if "record" in params:  # of_server's switch for the event (its PR 144); else the emit is silenced below
        kw["record"] = not as_root_for_user
    if "domain" in params:
        kw["domain"] = domain
    elif domain and restart is None:  # an of_server.watch from before its `domain`: the system daemon restarted here
        import subprocess
        restart = lambda: subprocess.run(["launchctl", "kickstart", "-k", f"{domain}/{label}"],  # noqa: E731
                                         capture_output=True, timeout=30)
    emit = of_events.emit
    if as_root_for_user and "record" not in params:
        of_events.emit = lambda *a, **k: None
    try:
        return of_server.watch("the listener", label, probe, pid_of, state_path, now=now, restart=restart,
                               dump=dump, log=stamp, settings=s, event="listener_watchdog",
                               silent="has not kept its heartbeat or turned its timers", **kw)
    finally:
        of_events.emit = emit


SERVES_READERS = ("asks",)  # fresh() vouches for these views: no heartbeat while one of them is held
LAG_OK_S = {"asks": 120}    # a view's acceptable lag: docs/health.md's view.age.<view> degrades past it, fails past 5x


def stuck_timers(ticks, processors, now, slack=None):
    """The timers whose last start is older than their period plus SLACK (the watchdog's ticks_stale), from the
    listener's own memory. With a thread per timer any one of them could keep the timers' file fresh, so a thread
    touches it only while none is stuck, and the watchdog stays stat-only: on the agent host it runs as root in the
    user's state folder and opens nothing there (of_review and of_server on organic-framework PR 294)."""
    slack = WATCHDOG["ticks_stale"] if slack is None else slack
    return sorted(p.name for p in processors if p.tick_s and now - ticks.get(p.name, now) > p.tick_s + slack)


def _save_ticks(ticks, processors):
    """listen-ticks.json {processor: {at, every}}: when each timer last ran, for `of health`'s listener.lag."""
    try:
        path = os.path.join(of_events.STATE_DIR, "listen-ticks.json")
        data = {p.name: {"at": ticks[p.name], "every": p.tick_s} for p in processors if p.tick_s}
        with open(path + ".tmp", "w") as fh:
            json.dump(data, fh)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def tick_thread(processors, ticks, locks, changed, stop=None, log=print, connect=None):
    """Run each processor's timer in a thread of its own, on a connection of its own (of_asks msg 01a1d67e-8752,
    2026-11-13: ticks of 442 s, 132 s and 71 s held the loop, the heartbeat went past STALE_S, and every reader fell
    back to projecting the whole log). One thread per timer, not one for all: on 2026-11-17 the paste tick searched its
    sources for over an hour at load 90 and no other timer turned, the pulse and its notes included
    (organic-framework-build reports/of-log-readers-2026-11-14.md, 3.4). A processor's tick and its catch-up never run
    at once: each holds its lock. Returns the threads."""
    import threading
    saving = threading.Lock()

    def loop(p):
        conn = None
        while not (stop and stop.is_set()):
            if time.time() - ticks[p.name] >= p.tick_s:
                ticks[p.name] = time.time()
                with locks[p.name]:
                    try:
                        if conn is None:
                            conn = (connect or __import__("of_store").connect)()
                        t0 = time.time()
                        if p.tick(conn, t0):
                            changed.set()
                        slow(p.name, "tick", t0)
                    except Exception as e:  # noqa: BLE001 - one timer failing never stops the others
                        log(f"of listen: {p.name} tick: {type(e).__name__}: {e}")
                with saving:
                    _save_ticks(ticks, processors)
                    if not stuck_timers(ticks, processors, time.time()):
                        _touch(ticks_path())  # the watchdog's sign that every timer still turns
            time.sleep(POLL_S)
    threads = [threading.Thread(target=loop, args=(p,), name=f"of-listen-tick-{p.name}", daemon=True)
               for p in processors if p.tick_s]
    for t in threads:
        t.start()
    return threads

SLOW_S = 5


def slow(name, what, t0, rows=None):
    """Name in listen.log a processor that held the loop past SLOW_S: the heartbeat, the views and the deliveries at a
    turn's end wait on it (2026-11-09: the waits view and the pulse held it for minutes, unseen)."""
    took = time.time() - t0
    if took >= SLOW_S:
        print(f"of listen: slow {name} {what}: {took:.1f} s" + (f" ({rows} events)" if rows is not None else ""),
              flush=True)


def _has_meta(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'listener_meta'").fetchone() is not None


def make_beat(conn, processors, clock=time.time):
    """The first catch-up's beat, called between batches at most every HEARTBEAT_S. With every view whole it is the
    full heartbeat. While a view is rebuilt in place (prepare() set in_place and dropped the heartbeat) readers must not
    trust the views yet, but the listener is alive: only the watchdog's file beats, so a long replay is never
    restarted (of_review on PR 145 and PR 149)."""
    whole = not any(getattr(p, "in_place", False) for p in processors)
    last = [0.0]

    def beat():
        if clock() - last[0] >= HEARTBEAT_S:
            if whole:
                heartbeat(conn)
            else:
                _touch(heartbeat_path())
            last[0] = clock()
    return beat


def heartbeat(conn):
    """Never fatal: a busy store skips one beat (a 'database is locked' here crash-looped the listener). The beat goes
    to listener_meta, which readers trust (fresh()), and to the file listen.heartbeat, which the watchdog reads without
    opening the store (of_server on PR 145: a root watchdog opening a user's store left root-owned files there)."""
    import sqlite3
    _touch(heartbeat_path())
    try:
        conn.execute("INSERT OR REPLACE INTO listener_meta VALUES ('heartbeat', ?)", (str(time.time()),))
        return True
    except sqlite3.OperationalError:
        return False


START_CPU_S = 0.01  # the CPU the other threads must spend in a beat's interval for the start to count as working


def starting_beat(every=None, cpu=None, drain=None):
    """Until the returned event is set, touch the watchdog's files every HEARTBEAT_S while the start spends CPU: a start
    that works (the asks view's state loaded from the log, the first catch-up) is not restarted, one that waits on
    nothing is (2026-11-17: at load 45 to 103 a start took more than the watchdog's 180 s, and each restart began it
    again). The CPU counted is the process's less this thread's own, past START_CPU_S: the whole process's grew with
    the beat's own wake-up, so a hung start was never restarted (of_review on organic-framework PR 274). Readers trust
    only listener_meta's beat, which the start does not write."""
    import threading
    done = threading.Event()
    cpu = cpu or (lambda: time.process_time() - time.thread_time())   # called in the beat's thread

    def loop():
        last, drained = cpu(), time.time()
        while not done.wait(every or HEARTBEAT_S):
            if drain and time.time() - drained >= SPOOL_S:   # the hooks' spool lands during a start too (of_asks,
                drained = time.time()                           # 01a1eaeb-1f42: drains up to an hour old came after
                try:                                            # each long start)
                    drain()
                except Exception:  # noqa: BLE001 - the main loop drains it after the start
                    pass
            now = cpu()
            if now - last >= START_CPU_S:   # the files the watchdog reads: the timers turn only after the start
                _touch(heartbeat_path())
                _touch(ticks_path())

            last = now
    threading.Thread(target=loop, name="of-listen-start", daemon=True).start()
    return done


def working_beat(thread_id=None, every=None, frames=None, stop=None):
    """For the listener's whole life: touch the watchdog's heartbeat file every HEARTBEAT_S while the main thread's
    place in the code moves (its innermost frame and line differ from the last look), so a long pass that works is not
    restarted; one that sits in one place (a lock wait, a stuck call) is, as before. Readers trust only listener_meta's
    beat, which this never writes. 2026-11-17 17:52Z: a flush of the asks view ran past the watchdog's 180 s at load
    65, the watchdog restarted the listener, and its start then read every ask event again for 17 minutes. (macOS gives
    no CPU clock per thread, so the place in the code is the sign of work.) Returns the stop event."""
    import sys as _sys
    import threading
    done = threading.Event()
    tid = thread_id or threading.main_thread().ident
    frames = frames or _sys._current_frames

    def where():
        f = frames().get(tid)
        out = []
        while f is not None and len(out) < 6:
            out.append((f.f_code.co_filename, f.f_lineno))
            f = f.f_back
        return tuple(out)

    def loop():
        last = where()
        while not done.wait(every or HEARTBEAT_S) and not (stop and stop.is_set()):
            now = where()
            if now != last:
                _touch(heartbeat_path())
            last = now
    threading.Thread(target=loop, name="of-listen-working", daemon=True).start()
    return done


def run(stop=None, log=print, rebuild=False):
    if stop is None:   # the service: its asks get their card ids from the pulse, never a wait inside a write
        of_events.CARD_REFS_LATER = True
    import threading
    import of_store
    if stop is None:  # the service: the watchdog asks for every thread's stack before it restarts a stuck one
        import of_stacks
        import signal
        of_stacks.install(signal.SIGUSR1, sys.stderr)  # by a Python handler: faulthandler's C dump crashed on 3.15.2
    spool_conn = []

    def drain_during_start():   # its own connection: SQLite's objects stay in the thread that made them
        if not spool_conn:
            spool_conn.append(of_store.connect())
        of_store.drain_spool(spool_conn[0], budget_s=BUDGET_S)
    starting = starting_beat(drain=drain_during_start)
    working_beat(threading.get_ident(), stop=stop)   # the loop's own thread (a test runs it in another)
    conn = of_store.connect()
    processors = [cls() for cls in PROCESSORS]
    folds_ok = False
    try:  # the fold views (bin/of_fold.py): one processor each, by its own cursor
        import of_fold
        processors += [of_fold.FoldProcessor(v) for v in of_fold.views().values()]
        folds_ok = not of_fold.FAILED   # a view module that did not import is no removed view (of_review on PR 256)
    except Exception as e:  # noqa: BLE001 - a broken view never stops the listener
        log(f"of listen: fold views: {type(e).__name__}: {e}")
    # Readers trust the views only while the heartbeat is fresh. A view rebuilt in place is half-built until
    # caught up, so prepare() drops the heartbeat for it (the asks popup showed "0 waiting", 2026-11-06); the
    # asks view builds in shadow tables instead and the old one keeps serving. Beat after the first catch-up.
    while True:  # a busy store at start: wait, never exit (launchd would restart it into the same lock)
        try:
            cursors = prepare(conn, processors, rebuild)
            if folds_ok:  # only with every view loaded: a view that failed to load is not removed
                for name in prune(conn, processors):
                    log(f"of listen: forgot the cursor of {name}, which no longer runs")
            break
        except __import__("sqlite3").OperationalError as e:
            log(f"of listen: start waits for the store ({e})")
            if stop and stop.is_set():
                return
            time.sleep(2)
    log(f"of listen: {', '.join(f'{p.name}@{p.version} from {cursors[p.name]}' for p in processors)}")
    beat = make_beat(conn, processors)
    while True:  # the first catch-up: a busy store waits and continues from where it got (never a crash)
        try:
            if step(conn, processors, cursors, beat=beat):
                bump()
            break
        except __import__("sqlite3").OperationalError as e:
            log(f"of listen: catch-up paused ({e}); continuing from {min(cursors.values())}")
            if stop and stop.is_set():
                return
            time.sleep(2)
    starting.set()
    heartbeat(conn)
    last_beat = last_purge = last_spool = time.time()
    ticks = {p.name: time.time() - (p.tick_s or 0) + 20 for p in processors}  # first tick after 20 s
    import threading
    locks = {p.name: threading.Lock() for p in processors}
    ticked = threading.Event()
    tick_thread(processors, ticks, locks, ticked, stop=stop, log=log)
    while not (stop and stop.is_set()):
        now = time.time()
        try:
            changed = step(conn, processors, cursors, locks=locks, budget=True)
            if ticked.is_set():
                ticked.clear()
                changed = True
            if changed:
                bump()
            if now - last_spool >= SPOOL_S:  # what hooks spooled while the store was busy, apart from the ingest worker
                last_spool = now
                try:   # the next step folds what it moved in
                    of_store.drain_spool(conn, budget_s=BUDGET_S)
                except Exception as e:  # noqa: BLE001
                    log(f"of listen: spool: {type(e).__name__}: {e}")
            if now - last_purge >= 30:
                last_purge = now
                try:  # option A's backups hold secrets: never kept past 24 h
                    import of_redact
                    for d in of_redact.purge_backups():
                        log(f"of listen: purged redaction backup {d}")
                except Exception as e:  # noqa: BLE001
                    log(f"of listen: backup purge: {type(e).__name__}: {e}")
            if now - last_beat >= HEARTBEAT_S and not any(
                    locks[n].locked() for n in SERVES_READERS if n in locks):
                heartbeat(conn)
                last_beat = now
        except Exception as e:  # noqa: BLE001 - the listener keeps running; the log says why
            log(f"of listen: {type(e).__name__}: {e}")
            time.sleep(2)
        time.sleep(POLL_S)
