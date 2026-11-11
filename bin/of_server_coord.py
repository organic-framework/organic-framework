"""of-server: an organization's coordination feed (docs/server-several-machines.md §4.2, docs/server.md).

Asks, declared states, hand-overs and holder changes (`of_coord.EVENTS`) are projections of each machine's own
event store. When the writer has written a batch's events into an organization's database, it copies that
organization's coordination events (masked: the very rows the organization got) into the `coord` table here.
`GET /v1/coord` hands them, oldest first, to every machine that joined the organization; its relay inserts them
into its own store, so the projections on every machine see the whole organization.
"""
import json
import threading
import time

import of_coord

DDL = """
CREATE TABLE IF NOT EXISTS coord (seq INTEGER PRIMARY KEY AUTOINCREMENT, org TEXT NOT NULL, event_id TEXT NOT NULL,
  name TEXT, ts TEXT, host_id TEXT, record TEXT NOT NULL, added REAL NOT NULL, UNIQUE (org, event_id));
CREATE INDEX IF NOT EXISTS coord_org_seq ON coord (org, seq);
CREATE INDEX IF NOT EXISTS coord_added ON coord (added);
"""
MAX_WAIT = 30      # seconds a feed request may wait for new events
LIMIT = 500        # events per answer
PRUNE_EVERY = 3600

_lock = threading.Lock()
_ready = set()
_pruned = {}


class CoordError(Exception):
    def __init__(self, status, typ, title):
        super().__init__(title)
        self.status, self.typ, self.title = status, typ, title


def ensure(store):
    with _lock:
        if store.dir in _ready:
            return
        with store.conn() as c:
            c.executescript(DDL)
        _ready.add(store.dir)


def copy(store, org, rows, now=None):
    """The writer, after it wrote `rows` (the `event` table's rows for `org`'s database): keep the
    coordination events in the feed. Shape copies of another organization's events are left out: a feed
    carries only the organization's own. Idempotent (a retried batch adds nothing). Returns the count added."""
    keep = []
    for r in rows:
        if not of_coord.shared(r.get("name") or "") or not r.get("event_id"):
            continue
        try:
            rec = json.loads(r.get("record") or "{}")
        except ValueError:
            continue
        if rec.get("of.shape"):
            continue
        keep.append((org, r["event_id"], r["name"], r.get("ts"), r.get("host_id"), r["record"]))
    if not keep:
        return 0
    ensure(store)
    now = time.time() if now is None else now
    with store.tx() as c:
        before = c.total_changes
        c.executemany("INSERT OR IGNORE INTO coord (org, event_id, name, ts, host_id, record, added)"
                      " VALUES (?, ?, ?, ?, ?, ?, ?)", [k + (now,) for k in keep])
        added = c.total_changes - before
    prune(store, now)
    return added


def prune(store, now=None, force=False):
    """Drop events older than the retention (of_coord.RETENTION_S), at most once an hour."""
    now = time.time() if now is None else now
    if not force and now - _pruned.get(store.dir, 0) < PRUNE_EVERY:
        return 0
    _pruned[store.dir] = now
    ensure(store)
    with store.tx() as c:
        return c.execute("DELETE FROM coord WHERE added < ?", (now - of_coord.RETENTION_S,)).rowcount


def feed(store, m, joined, org, after=0, wait=0, clock=time.time, sleep=time.sleep):
    """`GET /v1/coord?org=&after=&wait=` → {"events": [{seq, event_id, name, ts, host_id, record}], "next", "last"}.
    Only for a machine that joined `org` (the tenant reads every organization). Waits up to `wait` seconds
    for an event after `after`. `last` is the newest sequence number the feed holds for `org`: a client
    whose cursor is past it follows a server that started over, and reads again from 0."""
    if not org:
        raise CoordError(400, "bad_request", "org is required")
    if not m["tenant"] and org not in joined:
        raise CoordError(403, "forbidden", f"organization {org} not joined by this machine")
    try:
        after = max(0, int(after or 0))
        wait = max(0.0, min(float(wait or 0), MAX_WAIT))
    except ValueError:
        raise CoordError(400, "bad_request", "after and wait are numbers")
    ensure(store)
    deadline = clock() + wait
    while True:
        with store.conn() as c:
            rows = c.execute("SELECT seq, event_id, name, ts, host_id, record FROM coord WHERE org = ? AND seq > ?"
                             " ORDER BY seq LIMIT ?", (org, after, LIMIT)).fetchall()
            last = c.execute("SELECT max(seq) FROM coord WHERE org = ?", (org,)).fetchone()[0] or 0
        left = deadline - clock()
        if rows or left <= 0 or after > last:
            break
        sleep(min(1.0, left))
    events = [{"seq": s, "event_id": e, "name": n, "ts": t, "host_id": h, "record": json.loads(r)}
              for s, e, n, t, h, r in rows]
    return {"events": events, "next": rows[-1][0] if rows else after, "last": last}
