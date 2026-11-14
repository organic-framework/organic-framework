"""of-server's own checks (docs/health.md §2 and §5; of_architect's hand-over 154a0cbf; the founder, prompt
01a1d68a-5ab4: "where else something may fire, because we keep having an unstable problem").

- `server.health`: how long the server took to answer /v1/health, from the accept of the connection to the reply
  written, the longest in the last three minutes. A starved or wedged server shows here before its clients give up.
- `relay.oldest`: how long the oldest message, other than an fyi, has been queued for an agent that is live and idle,
  which the relay should have typed in already: counted from the first evaluation that saw it so, never from its
  creation, so a wait on a busy agent counts nothing (of_review on organic-framework PR 142). A message the person
  holds, with a dialog open in the pane or text in its input box, does not count either.

Each check keeps one state (ok, degraded, failed) with the table's limits and since when, in the server's store. A
change writes one of.health_changed; GET /v1/health/checks serves the states to the machines for `of health`.
"""

import collections
import threading
import time

import of_events

OWNER = "of_server"
EVERY = 60                 # seconds between evaluations
WINDOW = 180               # server.health: the longest answer in this many seconds
PRESENCE_TTL = 120         # as of_server_relay: a presence older than this is no live holder
# check: (degraded over, failed over), in seconds. docs/health.md §2 holds the values; tune them there with evidence.
LIMITS = {"server.health": (2.0, 20.0), "relay.oldest": (600.0, 3600.0)}
IDLE = ("idle", "done")    # herdr's states in which an agent waits for input
HELD = ("a dialog is open%", "the input box holds text%")  # the relay's deferrals the person holds (of_relay_client)

DDL = """CREATE TABLE IF NOT EXISTS health_checks (name TEXT PRIMARY KEY, state TEXT, value REAL, since TEXT,
  updated TEXT);"""


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def state_of(name, value):
    degraded, failed = LIMITS[name]
    return "failed" if value > failed else "degraded" if value > degraded else "ok"


def limit_of(name, state):
    """The limit a state is judged against: the failed limit for failed, else the degraded one."""
    degraded, failed = LIMITS[name]
    return failed if state == "failed" else degraded


class Health:
    def __init__(self, store, now=time.time, emit=None):
        self.store, self.now = store, now
        self.emit = emit or (lambda name, data: of_events.emit(name, data))
        self.answers = collections.deque(maxlen=2000)  # (time, seconds) of each /v1/health answer
        self.waiting = {}  # msg_id: when an evaluation first saw it queued for a live, idle agent
        self.lock = threading.Lock()
        with store.conn() as c:
            c.executescript(DDL)

    def answered(self, seconds):
        with self.lock:
            self.answers.append((self.now(), float(seconds)))

    def server_health(self):
        """The longest /v1/health answer in the last WINDOW seconds, or None when none came."""
        cut = self.now() - WINDOW
        with self.lock:
            vals = [s for t, s in self.answers if t >= cut]
        return max(vals) if vals else None

    def relay_oldest(self):
        """Seconds the oldest message (no fyi, none the person holds) has been seen queued for a live, idle agent, by
        this server's evaluations; 0 when none waits."""
        now = self.now()
        try:
            with self.store.conn() as c:
                rows = c.execute(
                    "SELECT m.msg_id FROM messages m JOIN presence p ON p.org = m.to_org AND p.position = "
                    "m.to_position WHERE m.state IN ('queued', 'leased') AND COALESCE(m.priority, 'normal') != 'fyi'"
                    f" AND p.kind = 'agent' AND p.state IN ({', '.join('?' * len(IDLE))}) AND p.updated > ?"
                    + "".join(" AND COALESCE(m.reason, '') NOT LIKE ?" for _ in HELD),
                    (*IDLE, now - PRESENCE_TTL, *HELD)).fetchall()
        except Exception:  # noqa: BLE001 - no relay tables yet (a server that never relayed)
            return 0.0
        with self.lock:
            self.waiting = {r[0]: self.waiting.get(r[0], now) for r in rows}
            return max((now - t for t in self.waiting.values()), default=0.0)

    def evaluate(self):
        """Judge every check with a value now; write a change as one of.health_changed. Returns the changes."""
        now = self.now()
        changes = []
        for name, value in (("server.health", self.server_health()), ("relay.oldest", self.relay_oldest())):
            if value is None:
                continue  # no answer to judge: the check keeps its state
            state = state_of(name, value)
            with self.store.conn() as c:
                row = c.execute("SELECT state, since FROM health_checks WHERE name = ?", (name,)).fetchone()
                since = row[1] if row and row[0] == state else iso(now)
                c.execute("INSERT OR REPLACE INTO health_checks (name, state, value, since, updated) VALUES (?,?,?,?,?)",
                          (name, state, round(value, 3), since, iso(now)))
            if not row or row[0] != state:
                change = {"check": name, "state": state, "value": round(value, 3), "limit": limit_of(name, state),
                          "since": since, "owner": OWNER}
                changes.append(change)
                try:
                    self.emit("health_changed", change)
                except Exception:  # noqa: BLE001 - the state is kept; the record is best effort
                    pass
        return changes

    def states(self):
        with self.store.conn() as c:
            rows = c.execute("SELECT name, state, value, since, updated FROM health_checks ORDER BY name").fetchall()
        return [{"check": n, "state": s, "value": v, "limit_degraded": LIMITS[n][0], "limit_failed": LIMITS[n][1],
                 "since": since, "updated": upd, "owner": OWNER} for n, s, v, since, upd in rows if n in LIMITS]

    def run(self, stop):
        """Evaluate every EVERY seconds until `stop` is set (the server's own thread)."""
        while not stop.wait(EVERY):
            try:
                self.evaluate()
            except Exception:  # noqa: BLE001 - a busy store skips one round
                pass
