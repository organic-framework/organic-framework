"""of-server's own checks (docs/health.md §2 and §5; of_architect's hand-over 154a0cbf; the founder, prompt
01a1d68a-5ab4: "where else something may fire, because we keep having an unstable problem").

- `server.health`: how long the server took to answer /v1/health, from the accept of the connection to the reply
  written, the longest in the last three minutes. A starved or wedged server shows here before its clients give up.
- `relay.oldest`: how long the oldest message, other than an fyi, has been queued for an agent that is live and idle,
  which the relay should have typed in already: counted from the first evaluation that saw it so, never from its
  creation, so a wait on a busy agent counts nothing (of_review on organic-framework PR 142). A message the person
  holds, with a dialog open in the pane or text in its input box, does not count either.

Each check keeps one state (ok, degraded, failed) with the table's limits and since when, in the server's store;
GET /v1/health/checks serves the states to the machines for `of health`. A change of relay.oldest writes one
of.health_changed, and its owner is told as docs/health.md §3 says: one --fyi once it has failed for 5 minutes, one
when that failure is over, and once a day while it stays failed, typed into the owner's pane (of_architect's hand-over
09042907). server.health's changes are recorded and told by each machine's `of health run`, which measures the
server's answer from there and so also sees a server that does not answer; the server's own reading is kept and served
only, so a change is recorded once, not by both sides.
"""

import calendar
import collections
import threading
import time

import of_events

OWNER = "of_server"
OWNER_ORG = "organic-framework-build"  # the OF team's organization: the checks' owner is its position
RECORDED = ("relay.oldest",)  # the checks whose changes this server records and tells (server.health: the machines')
TELL_AFTER = 300           # the owner hears of a failure that lasted 5 minutes, not of each flap around a limit
REMIND_EVERY = 86400       # and again once a day while it stays failed, typed into its pane
EVERY = 60                 # seconds between evaluations
WINDOW = 180               # server.health: the longest answer in this many seconds
PRESENCE_TTL = 120         # as of_server_relay: a presence older than this is no live holder
# check: (degraded over, failed over), in seconds. docs/health.md §2 holds the values; tune them there with evidence.
LIMITS = {"server.health": (2.0, 20.0), "relay.oldest": (600.0, 3600.0)}
IDLE = ("idle", "done")    # herdr's states in which an agent waits for input
HELD = ("a dialog is open%", "the input box holds text%")  # the relay's deferrals the person holds (of_relay_client)

DDL = """CREATE TABLE IF NOT EXISTS health_checks (name TEXT PRIMARY KEY, state TEXT, value REAL, since TEXT,
  updated TEXT, told TEXT, reminded TEXT);"""


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def state_of(name, value):
    degraded, failed = LIMITS[name]
    return "failed" if value > failed else "degraded" if value > degraded else "ok"


def limit_of(name, state):
    """The limit a state is judged against: the failed limit for failed, else the degraded one."""
    degraded, failed = LIMITS[name]
    return failed if state == "failed" else degraded


def epoch(ts):
    try:
        return float(calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")))
    except (TypeError, ValueError):
        return 0.0


class Health:
    def __init__(self, store, now=time.time, emit=None, tell=None):
        """TELL(position, text, typed) sends the owner a message, typed into its pane when TYPED, else --fyi; it
        returns a false value when the message could not be queued (tried again at the next evaluation). None: nobody
        is told (a server with no relay)."""
        self.store, self.now, self.tell = store, now, tell
        self.emit = emit or (lambda name, data: of_events.emit(name, data))
        self.answers = collections.deque(maxlen=2000)  # (time, seconds) of each /v1/health answer
        self.waiting = {}  # msg_id: when an evaluation first saw it queued for a live, idle agent
        self.lock = threading.Lock()
        with store.conn() as c:
            c.executescript(DDL)
            have = [r[1] for r in c.execute("PRAGMA table_info(health_checks)")]
            for col in ("told", "reminded"):
                if col not in have:
                    c.execute(f"ALTER TABLE health_checks ADD COLUMN {col} TEXT")

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
        """Judge every check with a value now. A change of a RECORDED check is written as one of.health_changed, and
        its owner is told (_tell). Returns the changes of every check."""
        now = self.now()
        changes = []
        for name, value in (("server.health", self.server_health()), ("relay.oldest", self.relay_oldest())):
            if value is None:
                continue  # no answer to judge: the check keeps its state
            state = state_of(name, value)
            with self.store.conn() as c:
                row = c.execute("SELECT state, since, told, reminded FROM health_checks WHERE name = ?",
                                (name,)).fetchone()
            since = row[1] if row and row[0] == state else iso(now)
            told, reminded = (row[2], row[3]) if row and row[0] == "failed" and state == "failed" else (None, None)
            change = None
            if not row or row[0] != state:
                change = {"check": name, "state": state, "value": round(value, 3), "limit": limit_of(name, state),
                          "since": since, "owner": OWNER}
                changes.append(change)
            if name in RECORDED:
                if change:
                    try:
                        self.emit("health_changed", change)
                    except Exception:  # noqa: BLE001 - the state is kept; the record is best effort
                        pass
                told, reminded = self._tell(name, state, round(value, 3), since, now, told, reminded,
                                            was_told=bool(row and row[0] == "failed" and row[2]))
            with self.store.conn() as c:
                c.execute("INSERT OR REPLACE INTO health_checks (name, state, value, since, updated, told, reminded)"
                          " VALUES (?,?,?,?,?,?,?)", (name, state, round(value, 3), since, iso(now), told, reminded))
        return changes

    def _tell(self, name, state, value, since, now, told, reminded, was_told):
        """docs/health.md §3: one --fyi once a failure has lasted TELL_AFTER (a check that fails and recovers within it
        tells nobody), one when a told failure is over, and from a day after it began, once a day while it stays
        failed, typed into the owner's pane. Returns (told, reminded) as they stand after it."""
        if self.tell is None:
            return told, reminded
        limit = limit_of(name, state)
        try:
            if state == "failed" and not told and now - epoch(since) >= TELL_AFTER:
                if self.tell(OWNER, f"Health: of-server's {name} failed at {since}: value {value} s, limit {limit} s "
                                    f"(docs/health.md §2; `of health`).", False):
                    told = iso(now)
            elif state == "failed" and told and now - epoch(since) >= REMIND_EVERY and \
                    now - epoch(reminded or since) >= REMIND_EVERY:
                days = (now - epoch(since)) / 86400
                if self.tell(OWNER, f"Health: of-server's {name} has been failed since {since} ({days:.1f} days): "
                                    f"value {value} s, limit {limit} s. You are its owner: fix it, or say why it stays "
                                    f"(docs/health.md §3; `of health`). Reminded once a day while it stays failed.",
                             True):
                    reminded = iso(now)
            elif state != "failed" and was_told:
                self.tell(OWNER, f"Health: of-server's {name} is {state} since {since} (value {value} s); the failure "
                                 f"you were told of is over.", False)
        except Exception:  # noqa: BLE001 - a relay that cannot take it now is tried at the next evaluation
            pass
        return told, reminded

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
