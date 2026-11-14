"""One read model for the event log's views (organic-framework-build reports/of-log-readers-2026-11-14.md, 3.1, 3.2
and 3.5, accepted by of_architect with the fold a pure function; hand-over 40a30bc7).

A view is a View: its name, owner, version, the event names it reads, its key and its acceptable lag, and one pure
function, fold(rows, events) -> the rows it changed ({key: row, or None for a row removed}). No connection: the same
function serves the listener, which keeps the view's rows in the store by a cursor, a rebuild, which folds from the
start, and read(), which folds the log's tail after the view's cursor in memory, so no reader keeps a second
projection of the same facts.

  view_fold   (view, key, row JSON)              the rows, one per key
  view_fold_meta (view, version, cursor, at)      what they reflect: the last event folded, and when

read(view) -> Read(rows, cursor, age_s, late): the rows, carried on with at most `tail` events of the view's names
after its cursor, through a read-only connection that never waits on a writer. More than `tail` behind, the view as
it is, late. age_s is 0 when nothing of its names waits after the cursor, else the time since the oldest one waiting.

A view's owner defines it in its own module and calls register(view); the listener runs every registered view (its
module named in VIEW_MODULES). Tests: tests/test_fold.py.
"""

import json
import os
import time
from collections import namedtuple

import of_events

Read = namedtuple("Read", "rows cursor age_s late")
TAIL = 500
VIEW_MODULES = ("of_fold",)  # the modules whose import registers views; an owner adds its own
DDL = """
CREATE TABLE IF NOT EXISTS view_fold (view TEXT, key TEXT, row TEXT, PRIMARY KEY (view, key));
CREATE TABLE IF NOT EXISTS view_fold_meta (view TEXT PRIMARY KEY, version TEXT, cursor INTEGER, at REAL);
"""
_VIEWS = {}


class View:
    """name, owner, version (a new one rebuilds from the start), names (the event names fold reads), lag_ok_s (the
    age past which readers say it is late; docs/health.md's view.age.<name> degrades past it, fails past 5 times).
    fold(rows, events): pure; rows is {key: row} as the view holds it (read-only), events are dicts (rowid, event_id,
    ts, name, session_id, position, source, org, payload) in log order; returns {key: new row, or None to remove}."""
    name = "base"
    owner = "of_platform"
    version = "1"
    names = ()
    lag_ok_s = 120

    def fold(self, rows, events):
        return {}


def register(view):
    _VIEWS[view.name] = view
    return view


def views():
    import importlib
    for m in VIEW_MODULES:
        try:
            importlib.import_module(m)
        except ImportError:
            pass
    return dict(_VIEWS)


def apply_changes(rows, changed):
    for k, v in changed.items():
        if v is None:
            rows.pop(k, None)
        else:
            rows[k] = v
    return rows


# ------------------------------------------------------------------ the store side (the listener)

def load_rows(conn, view):
    return {k: json.loads(r) for k, r in conn.execute("SELECT key, row FROM view_fold WHERE view = ?", (view.name,))}


def meta(conn, view):
    r = conn.execute("SELECT version, cursor, at FROM view_fold_meta WHERE view = ?", (view.name,)).fetchone()
    return {"version": r[0], "cursor": r[1], "at": r[2]} if r else None


def persist(conn, view, changed, cursor, now=None):
    """The changed rows and the cursor in one transaction."""
    own = not conn.in_transaction
    if own:
        conn.execute("BEGIN IMMEDIATE")
    try:
        for k, v in changed.items():
            if v is None:
                conn.execute("DELETE FROM view_fold WHERE view = ? AND key = ?", (view.name, str(k)))
            else:
                conn.execute("INSERT OR REPLACE INTO view_fold VALUES (?, ?, ?)",
                             (view.name, str(k), json.dumps(v, ensure_ascii=False, default=str)))
        conn.execute("INSERT OR REPLACE INTO view_fold_meta VALUES (?, ?, ?, ?)",
                     (view.name, view.version, cursor, now or time.time()))
        if own:
            conn.execute("COMMIT")
    except Exception:
        if own:
            conn.execute("ROLLBACK")
        raise


def reset(conn, view):
    conn.executescript(DDL)
    conn.execute("DELETE FROM view_fold WHERE view = ?", (view.name,))
    conn.execute("DELETE FROM view_fold_meta WHERE view = ?", (view.name,))


class FoldProcessor:
    """The listener's processor for one View. Its cursor is the one committed with its rows (view_fold_meta), never
    the listener's own, which may lag a pass behind: an event at or below it is dropped, so every view takes each
    event once, a counting one included (of_architect on PR 154). The rows in memory change only after the write
    commits; a failed write drops them, to be loaded again. The listener fetches only the view's own names for it
    (`fold_names`), so a new view builds from the events it reads, not the whole log (of_review on PR 154)."""
    tick_s = None
    own_readiness = True  # read() says when its view is not built yet: the listener's heartbeat need not wait for it

    def __init__(self, view):
        self.view = view
        self.name = f"fold:{view.name}"
        self.version = view.version
        self.fold_names = tuple(view.names)
        self.rows, self.cursor = None, 0

    def _load(self, conn):
        conn.executescript(DDL)  # once, before any transaction (executescript commits an open one)
        self.rows = load_rows(conn, self.view)
        m = meta(conn, self.view)
        self.cursor = int(m["cursor"] or 0) if m and m["version"] == self.view.version else 0

    def reset(self, conn):
        reset(conn, self.view)
        self.rows, self.cursor = {}, 0

    def handle(self, conn, events, upto=None):
        """Fold EVENTS (the view's own names, in log order) and move the cursor to UPTO, or past the last event."""
        if self.rows is None:
            self._load(conn)
        mine = [e for e in events if e.get("rowid", 0) > self.cursor and e.get("name") in self.fold_names]
        cursor = max([self.cursor, upto or 0] + [e.get("rowid", 0) for e in events])
        if not mine and cursor == self.cursor:
            return False
        changed = self.view.fold(self.rows, mine) if mine else {}
        try:
            persist(conn, self.view, changed, cursor)
        except Exception:
            self.rows = None  # unknown what the store holds now: loaded again on the next pass
            raise
        apply_changes(self.rows, changed)
        self.cursor = cursor
        return bool(changed)


def age(view, conn=None, now=None):
    """(age_s, built) of VIEW from its meta row and one LIMIT 1 query, without loading its rows (of health's reading;
    of_architect on PR 154): 0 when nothing of its names waits after its cursor. (None, False) when never built."""
    own = conn is None
    conn = conn or reader()
    if conn is None:
        return None, False
    try:
        try:
            m = meta(conn, view)
        except Exception:  # noqa: BLE001
            return None, False
        if not m or m["version"] != view.version:
            return None, False
        row = conn.execute(f"SELECT ts FROM event WHERE rowid > ? AND name IN ({','.join('?' * len(view.names))}) "
                           "ORDER BY rowid LIMIT 1", (int(m["cursor"] or 0), *view.names)).fetchone()
        return (max(0.0, (now or time.time()) - (_epoch(row[0]) or time.time())) if row else 0.0), True
    finally:
        if own:
            conn.close()


# ------------------------------------------------------------------ the read side

def reader():
    """A read-only connection: it never writes and never waits on a writer (None when the store will not open so)."""
    import sqlite3
    import urllib.parse
    import of_store
    try:
        c = sqlite3.connect("file:" + urllib.parse.quote(of_store.db_path()) + "?mode=ro", uri=True, timeout=2.0,
                            isolation_level=None)
        c.execute("PRAGMA query_only = 1")
        c.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        return c
    except sqlite3.Error:
        return None


def _epoch(ts):
    import calendar
    try:
        return calendar.timegm(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def read(view, conn=None, tail=TAIL, now=None):
    """Read(rows, cursor, age_s, late) of VIEW (a View or its name): see the module's docstring. Never the whole log."""
    if isinstance(view, str):
        view = views()[view]
    own = conn is None
    conn = conn or reader()
    now = now or time.time()
    if conn is None:
        return Read({}, 0, None, True)
    began = not conn.in_transaction
    if began:
        conn.execute("BEGIN")  # the rows, the cursor and the tail from one snapshot
    try:
        try:
            m = meta(conn, view)
            rows = load_rows(conn, view)
        except Exception:  # noqa: BLE001 - no listener has made the tables yet
            return Read({}, 0, None, True)
        if not m or m["version"] != view.version:  # never built, or built by another version: not this view's rows
            return Read({}, 0, None, True)
        cursor = int(m["cursor"] or 0)
        q = (f"SELECT rowid, event_id, ts, name, session_id, position, source, org, payload FROM event WHERE rowid > ? "
             f"AND name IN ({','.join('?' * len(view.names))}) ORDER BY rowid LIMIT ?")
        events = []
        for rid, e, ts, n, s, pos, src, org, pl in conn.execute(q, (cursor, *view.names, tail + 1)):
            try:
                payload = json.loads(pl) if pl else {}
            except ValueError:
                payload = {}
            events.append({"rowid": rid, "event_id": e, "ts": ts, "name": n, "session_id": s, "position": pos,
                           "source": src, "org": org, "payload": payload})
        age = max(0.0, now - (_epoch(events[0]["ts"]) or now)) if events else 0.0
        if len(events) > tail:
            return Read(rows, cursor, age, True)
        if events:
            apply_changes(rows, view.fold(dict(rows), events))
            cursor = events[-1]["rowid"]
        return Read(rows, cursor, age, False)
    finally:
        if began and conn.in_transaction:
            conn.execute("COMMIT")
        if own:
            conn.close()


# ------------------------------------------------------------------ the first view: the owner's answers to asks

class Answers(View):
    """Each ask's answers in order (who, via, the words, when), from of.agent_question_answered: for the diff approval
    (bin/of _diff_answer), which scanned every answer in the log on each `of diff apply`."""
    name = "answers"
    owner = "of_platform"
    version = "1"
    names = ("of.agent_question_answered",)
    lag_ok_s = 120

    def fold(self, rows, events):
        changed = {}
        for e in events:
            p = e.get("payload") or {}
            item = {"by": p.get("by"), "via": p.get("via"), "answer": p.get("answer"), "ts": e.get("ts"),
                    "event_id": e.get("event_id")}
            for aid in p.get("ask_ids") or [p.get("ask_id")]:
                if not aid:
                    continue
                cur = changed.get(aid) or rows.get(aid) or {"answers": []}
                if any(x.get("event_id") == item["event_id"] for x in cur["answers"]):
                    continue  # an event again under its id
                changed[aid] = {"answers": cur["answers"] + [item]}
        return changed


register(Answers())
