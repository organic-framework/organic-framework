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

import json
import re
import os
import sys
import time

import of_events

POLL_S = 0.5
BATCH = 2000
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

    def reset(self, conn):
        pass

    def handle(self, conn, rows):
        return False

    def tick(self, conn, now):
        return False


# ---------------------------------------------------------------------- asks

class AsksProcessor(Processor):
    """The asks view: every ask with its state (of_asks.project), and the owner's queue grouped as
    the popup shows it. Tables view_asks and view_ask_groups."""
    name = "asks"
    version = "23"  # 23: each row keeps its whole ask (column ask) and the fold its readers carry on (of_asks.FOLD_KEY): of_asks.current_asks; 22: a pane question the judge confirms is an ask OF files (source pane), kept until answered; 21: a heuristic question is a candidate until the judge opens or closes it (of.agent_question_judged); 20: declared_wait on a heuristic ask (never folded); 19: a heuristic ask's text is its last paragraph, its options from the stored text; 18: q-v4 heuristic asks; a declared wait on the founder keeps a heuristic question open; 17: the founder's free text marks declared asks seen and closes none (from 2026-11-07); 16: heuristic asks by q-v3 (a report with a declared task's steps is no question); 15: free text never closes a diff approval; 14: groups never cross recipients or tasks; a task is claimed only by the founder's own answer; 13: a turn_error is neutral (it closed asks as answered); 12: scheduler prompts close nothing (origin scheduled from the transcript; of.prompt_origin corrections); 11: a popup answer to one ask never closes the others (nor the open question); 10: a subagent's agent_question is not an ask; 9: tasks for the owner (claimed, done by the asker); 8: due, n_options; shadow rebuild; 7: of_asks.relevant (READS; pulled messages); 6: a position's reply answers an ask to it; 5: annotations neutral; 4: a popup answer closes only its ask; 3: status never closes
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

    def _relevant(self, r):
        import of_asks
        return of_asks.relevant(r["name"], r["source"], r.get("payload"))

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

    def load_state(self, conn):
        """After a restart: the relevant rows up to the cursor, so the projection continues (a rebuild that
        was interrupted continues from its cursor too; it then settles into the view directly)."""
        import of_asks
        conn.executescript(self.DDL)
        for t in ("view_asks", "view_ask_groups"):
            conn.execute(f"DROP TABLE IF EXISTS {t}{self.SHADOW}")
        self.rows = {r["event_id"]: r for r in of_asks.load(conn)}
        self.defer, self.dirty = True, False  # the stored view is current up to the cursor
        self.state, self.tail, self.written = None, [], {}
        row = conn.execute("SELECT last_rowid FROM listener_cursor WHERE processor = ?", (self.name,)).fetchone()
        self.max_rowid = int(row[0]) if row else 0

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
                    self.fold = None
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            self.suffix = ""
        return True

    def handle(self, conn, rows):
        if rows:
            self.max_rowid = max(self.max_rowid, max(r.get("rowid") or 0 for r in rows))
        new = [r for r in rows if self._relevant(r)]
        if not new:
            return False
        for r in new:
            row = {k: r.get(k) for k in ("event_id", "ts", "name", "session_id", "position", "payload", "org")}
            if r["event_id"] in self.rows or r["name"] == "of.prompt_origin" or (row["ts"] or "") < self.fed:
                # an event again under its id, a correction of an earlier prompt's origin, or a row older than
                # what was projected (imported history): the carried projection no longer holds; all rows again
                self.state, self.tail = None, []
            self.rows[r["event_id"]] = row
            if self.state is not None:
                self.tail.append(row)
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
        import of_asks
        if self.state is None:
            ordered = sorted(self.rows.values(), key=lambda r: r["ts"])  # imported history can arrive late
            self.state = of_asks.new_state()
            of_asks.project(ordered, self.state)
            self.fed = ordered[-1]["ts"] if ordered else ""
        else:  # only the rows since the last write, on the carried projection: the same asks (of_asks.project)
            tail = sorted(self.tail, key=lambda r: r["ts"])
            of_asks.project(tail, self.state)
            if tail:
                self.fed = max(self.fed, tail[-1]["ts"])
        self.tail = []
        asks = self.state["asks"]
        groups = of_asks.group(of_asks.for_owner(asks))
        by_id = self.rows
        rows = []  # built before the write lock is taken
        for a in asks:
            ev = by_id.get(a["event_id"]) or {}
            pl = ev.get("payload") or {}
            closer = by_id.get(a["closed_by"]) or {}
            labels = {k: v for k, v in (pl.get("classifications") or {}).items() if isinstance(v, (bool, str))}
            labels.update({k: pl.get(k) for k in ("detected_by", "classifier_version") if pl.get(k)})
            rows.append((a["event_id"], a["ts"], ev.get("org"), a["session_id"], a["position"], a["kind"],
                         a["text"], a["state"], a["closed_by"], a["closed_at"], closer.get("name"), a.get("to"),
                         pl.get("delegation"), json.dumps(labels, ensure_ascii=False), self.version,
                         json.dumps(a, ensure_ascii=False, default=str)))
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
            fold = json.dumps(of_asks.fold_meta(self.state, self.max_rowid, self.fed), ensure_ascii=False)
            if self.suffix:
                self.fold = fold
            else:
                conn.execute("INSERT OR REPLACE INTO listener_meta VALUES (?, ?)", (of_asks.FOLD_KEY, fold))
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
    return [{"event_id": e, "ts": ts, "session_id": s, "position": p, "kind": k, "text": t, "state": st,
             "closed_by": cb, "closed_at": ca, "to": to}
            for e, ts, s, p, k, t, st, cb, ca, to in conn.execute(
                "SELECT event_id, ts, session_id, position, kind, text, state, closed_by, closed_at, to_ FROM view_asks "
                "ORDER BY ts")]


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
            have = opened.get(key) or opened.get(pane)
            if have and d and d["fingerprint"] == have[1]:
                kept.add(have[0])
                continue
            if d:
                label = re.sub(r"\s*·\s*❓.*$", "", labels.get(pane, "").strip().strip("■").strip()).strip()
                position = (sessions.get(sid) or [None])[0] or a.get("name") or label or None
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

    def handle(self, conn, rows):
        return False

    def tick(self, conn, now):
        import fcntl
        import subprocess
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
        subprocess.Popen([sys.executable, "-c", _GH_RUNNER, lock_path, os.path.join(here, "of")],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                         env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
        log.close()
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
        for e, ts, n, pos, pl in conn.execute(
                "SELECT event_id, ts, name, position, payload FROM event WHERE ts >= ? AND name IN "
                "('of.agent_question_answered', 'of.agent_question', 'of.ask_escalated') ORDER BY ts", (since,)):
            try:
                rows.append({"event_id": e, "ts": ts, "name": n, "position": pos, "payload": json.loads(pl or "{}")})
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
        elif n == "of.agent_question":
            to = p.get("to")
            if to and to != "founder" and to != r.get("position") and not aa.inbox_only(p):
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
              DeliverProcessor, CaseProcessor, CorrectionsProcessor,
              PromptOriginProcessor,
              GithubProcessor, StudioCacheProcessor, HandoverScopesProcessor]
try:  # of_capacity's processor (bin/of_capacity.py, owned there): spend by message id, the balancer detached
    import of_capacity
    PROCESSORS.append(of_capacity.ListenerProcessor)
except Exception:  # noqa: BLE001 - the listener runs without it rather than not at all
    pass


# ---------------------------------------------------------------------- the loop

def fetch(conn, after, limit=None, top=None):
    out = []
    for rowid, e, ts, n, s, pos, src, org, pl in conn.execute(
            "SELECT rowid, event_id, ts, name, session_id, position, source, org, payload FROM event WHERE rowid > ? "
            "AND rowid <= ? ORDER BY rowid LIMIT ?", (after, top if top is not None else 2 ** 62, limit or BATCH)):
        try:
            payload = json.loads(pl) if pl else {}
        except ValueError:
            payload = {}
        out.append({"rowid": rowid, "event_id": e, "ts": ts, "name": n, "session_id": s, "position": pos,
                    "source": src, "org": org, "payload": payload})
    return out


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
            elif not hasattr(p, "settle") and _has_meta(conn):  # a view without a shadow is half-built until caught up
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


def step(conn, processors, cursors, beat=None, locks=None):
    """One pass: every processor catches up from its cursor. Returns True if any view changed.
    beat(): called between batches, so a long catch-up keeps the heartbeat while the views it serves
    stay whole (the asks view rebuilds in shadow tables; without the beat, readers fell back to the
    slow projection for the length of the rebuild, 2026-11-06).
    A step takes the events in the log when it starts; what arrives meanwhile is the next step's, and a processor
    with flush() writes its view once per step (2026-11-09: the asks view was projected again for every batch, 5 to 8
    s each, and while events kept arriving the loop stayed inside one step for 40 s, with no heartbeat)."""
    changed = False
    top = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM event").fetchone()[0]
    for p in processors:
        lock = (locks or {}).get(p.name)
        if lock is not None and not lock.acquire(blocking=False):
            continue  # its tick runs in the tick thread: it catches up from its cursor on a later pass
        try:
            changed |= _catch_up(conn, p, cursors, top, beat)
        finally:
            if lock is not None:
                lock.release()
    return changed


def _catch_up(conn, p, cursors, top, beat):
    changed = False
    while True:
        if beat:
            beat()
        rows = fetch(conn, cursors[p.name], top=top)
        if not rows:
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
    if getattr(p, "defer", False):  # caught up after a reset or a restart: project once, swap the shadow in
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


def pid_path():
    return os.path.join(of_events.STATE_DIR, "listen.pid")


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


def _write_pid():
    try:
        with open(os.open(pid_path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


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


def _heartbeat_age(now):
    import sqlite3
    try:
        import of_store
        conn = sqlite3.connect(f"file:{of_store.db_path()}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute("SELECT value FROM listener_meta WHERE key = 'heartbeat'").fetchone()
        finally:
            conn.close()
        return None if not row else now - float(row[0])
    except Exception:  # noqa: BLE001 - no store, no table: no heartbeat
        return None


def watchdog(now=None, probe=None, restart=None, dump=None, state_path=None, log=print, settings=None, pid=None):
    """One check, run by launchd every minute (`of listen watchdog`). A listener process that is alive, but whose
    heartbeat is older than STALE_S or whose timers have not turned for `ticks_stale`, through `failing_for` seconds
    of checks, is restarted, at most once in `restart_gap`; first `kill -USR1` writes its threads' stacks to
    listen.log. A gap between checks longer than twice `every` (a sleep) starts the count again. A listener with no
    live process is left to launchd's KeepAlive. Returns ok, failing, waiting, stopped, restarted or restart_failed.
    As of-server's watchdog (of_server's 3981069), for the listener (hand-over 6d9a8305)."""
    import signal
    import subprocess
    s = dict(WATCHDOG, **(settings or {}))
    now = time.time() if now is None else now
    state_path = state_path or os.path.join(of_events.STATE_DIR, "listen-watchdog.json")
    try:
        with open(state_path) as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        st = {}
    gap = None if st.get("last_check") is None else now - st["last_check"]
    st["last_check"] = now

    def save(result):
        st["result"] = result
        with open(state_path + ".tmp", "w") as fh:
            json.dump(st, fh)
        os.replace(state_path + ".tmp", state_path)
        return result

    def stamp(m):
        log(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))} {m}")

    if probe is None:
        def probe():
            hb = _heartbeat_age(now)
            try:
                ticks = now - os.path.getmtime(ticks_path())
            except OSError:
                ticks = None
            return hb is not None and hb <= STALE_S and (ticks is None or ticks <= s["ticks_stale"])
    if probe():
        if st.get("failing_since") is not None:
            stamp(f"the listener is live again after {int(now - st['failing_since'])} s")
        st = {"last_ok": now, "last_restart": st.get("last_restart"), "last_check": now}
        return save("ok")
    if pid is None:
        try:
            with open(pid_path()) as fh:
                pid = int(fh.read().strip() or 0)
        except (OSError, ValueError):
            pid = 0
    if not (pid and _alive(pid)):
        st.pop("failing_since", None)
        return save("stopped")
    if st.get("failing_since") is None or gap is None or gap > 2 * s["every"]:
        st["failing_since"], st["fails"] = now, 0
    st["fails"] = st.get("fails", 0) + 1
    failing = now - st["failing_since"]
    if failing < s["failing_for"]:
        return save("failing")
    if st.get("last_restart") is not None and now - st["last_restart"] < s["restart_gap"]:
        return save("waiting")
    try:
        load = round(os.getloadavg()[0], 1)
    except OSError:
        load = None
    dumped = False
    try:
        (dump or (lambda p: (os.kill(p, signal.SIGUSR1), time.sleep(2))))(pid)
        dumped = True
    except OSError:
        pass
    st["last_restart"] = now  # saved first: a launchctl that hangs still waits restart_gap
    save("restarting")
    stamp(f"the listener (pid {pid}) is stuck for {int(failing)} s ({st['fails']} checks; load {load})"
          + ("; its threads' stacks are in listen.log" if dumped else "") + "; restarting it")
    error = None
    try:
        (restart or (lambda: subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
                                            capture_output=True, timeout=30)))()
    except Exception as e:  # noqa: BLE001 - under the very load this is for
        error = f"{type(e).__name__}: {e}"[:200]
        stamp(f"the restart did not complete ({error})")
    try:
        of_events.emit("listener_watchdog", {"action": "restarted" if error is None else "restart_failed", "pid": pid,
                                             "failing_s": int(failing), "checks": st["fails"], "load1": load,
                                             **({"error": error} if error else {})}, position="of_platform")
    except Exception:  # noqa: BLE001 - the restart matters more than its record
        pass
    return save("restarted" if error is None else "restart_failed")


SERVES_READERS = ("asks",)  # fresh() vouches for these views: no heartbeat while one of them is held


def tick_thread(processors, ticks, locks, changed, stop=None, log=print, connect=None):
    """Run the processors' timers in their own thread, on their own connection (of_asks msg 01a1d67e-8752,
    2026-11-13: ticks of 442 s, 132 s and 71 s held the loop, the heartbeat went past STALE_S, and every reader fell
    back to projecting the whole log). A processor's tick and its catch-up never run at once: each holds its lock."""
    import threading
    conn = [None]

    def loop():
        while not (stop and stop.is_set()):
            due = [p for p in processors if p.tick_s and time.time() - ticks[p.name] >= p.tick_s]
            for p in due:
                ticks[p.name] = time.time()
                with locks[p.name]:
                    try:
                        if conn[0] is None:
                            conn[0] = (connect or __import__("of_store").connect)()
                        t0 = time.time()
                        if p.tick(conn[0], t0):
                            changed.set()
                        slow(p.name, "tick", t0)
                    except Exception as e:  # noqa: BLE001 - one timer failing never stops the others
                        log(f"of listen: {p.name} tick: {type(e).__name__}: {e}")
            _touch(ticks_path())  # the watchdog's sign that the timers still turn
            time.sleep(POLL_S)
    t = threading.Thread(target=loop, name="of-listen-ticks", daemon=True)
    t.start()
    return t


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


def heartbeat(conn):
    """Never fatal: a busy store skips one beat (a 'database is locked' here crash-looped the listener)."""
    import sqlite3
    try:
        conn.execute("INSERT OR REPLACE INTO listener_meta VALUES ('heartbeat', ?)", (str(time.time()),))
        return True
    except sqlite3.OperationalError:
        return False


def run(stop=None, log=print, rebuild=False):
    import of_store
    if stop is None:  # the service: the watchdog asks for every thread's stack before it restarts a stuck one
        import faulthandler
        import signal
        faulthandler.register(signal.SIGUSR1, file=sys.stderr, all_threads=True)
        _write_pid()
    conn = of_store.connect()
    processors = [cls() for cls in PROCESSORS]
    # Readers trust the views only while the heartbeat is fresh. A view rebuilt in place is half-built until
    # caught up, so prepare() drops the heartbeat for it (the asks popup showed "0 waiting", 2026-11-06); the
    # asks view builds in shadow tables instead and the old one keeps serving. Beat after the first catch-up.
    while True:  # a busy store at start: wait, never exit (launchd would restart it into the same lock)
        try:
            cursors = prepare(conn, processors, rebuild)
            break
        except __import__("sqlite3").OperationalError as e:
            log(f"of listen: start waits for the store ({e})")
            if stop and stop.is_set():
                return
            time.sleep(2)
    log(f"of listen: {', '.join(f'{p.name}@{p.version} from {cursors[p.name]}' for p in processors)}")
    whole = not any(getattr(p, "in_place", False) for p in processors)
    last = [0.0]

    def beat():
        if whole and time.time() - last[0] >= HEARTBEAT_S:
            heartbeat(conn)
            last[0] = time.time()
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
    heartbeat(conn)
    last_beat = last_outbox = time.time()
    ticks = {p.name: time.time() - (p.tick_s or 0) + 20 for p in processors}  # first tick after 20 s
    import threading
    locks = {p.name: threading.Lock() for p in processors}
    ticked = threading.Event()
    tick_thread(processors, ticks, locks, ticked, stop=stop, log=log)
    while not (stop and stop.is_set()):
        now = time.time()
        try:
            changed = step(conn, processors, cursors, locks=locks)
            if ticked.is_set():
                ticked.clear()
                changed = True
            if changed:
                bump()
            if now - last_outbox >= 30:  # answers a popup handed over whose sender died: send them
                last_outbox = now
                try:  # option A's backups hold secrets: never kept past 24 h
                    import of_redact
                    for d in of_redact.purge_backups():
                        log(f"of listen: purged redaction backup {d}")
                except Exception as e:  # noqa: BLE001
                    log(f"of listen: backup purge: {type(e).__name__}: {e}")
                try:
                    import of_asks_tui
                    if of_asks_tui.outbox_waiting():
                        of_asks_tui.spawn_sender(os.path.join(os.path.dirname(os.path.realpath(__file__)), "of"))
                except Exception as e:  # noqa: BLE001
                    log(f"of listen: outbox: {type(e).__name__}: {e}")
            if now - last_beat >= HEARTBEAT_S and not any(
                    locks[n].locked() for n in SERVES_READERS if n in locks):
                heartbeat(conn)
                last_beat = now
        except Exception as e:  # noqa: BLE001 - the listener keeps running; the log says why
            log(f"of listen: {type(e).__name__}: {e}")
            time.sleep(2)
        time.sleep(POLL_S)
