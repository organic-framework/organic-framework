"""of-server's own checks (docs/health.md §2, §5; of_architect's hand-over 154a0cbf): server.health as answered,
relay.oldest, one state each with its limits, an of.health_changed on a change only, GET /v1/health/checks."""

import os
import sys
import time
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
sys.path.insert(0, HERE)
import of_server_health as H  # noqa: E402
from test_server import ServerBase  # noqa: E402


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class HealthChecksTest(ServerBase):
    def setUp(self):
        super().setUp()
        self.clock, self.said = Clock(), []
        self.h = H.Health(self.app.store, now=self.clock, emit=lambda name, data: self.said.append((name, data)))

    def message(self, to, created, priority="normal", state="queued"):
        with self.app.store.conn() as c:
            c.execute("INSERT INTO messages (msg_id, to_org, to_position, from_org, from_position, text, created_at,"
                      " created, state, priority) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (f"m{created}{to}", "ofb", to, "ofb", "x", "t", H.iso(created), created, state, priority))

    def presence(self, position, state, updated, kind="agent"):
        with self.app.store.conn() as c:
            c.execute("INSERT OR REPLACE INTO presence (machine_id, org, position, session_id, state, pane, kind, updated)"
                      " VALUES ('m1', 'ofb', ?, 's', ?, 'p', ?, ?)", (position, state, kind, updated))

    def test_the_limits(self):
        self.assertEqual([H.state_of("server.health", v) for v in (0.3, 2.0, 2.5, 20.0, 21)],
                         ["ok", "ok", "degraded", "degraded", "failed"])
        self.assertEqual([H.state_of("relay.oldest", v) for v in (0, 600, 601, 3601)],
                         ["ok", "ok", "degraded", "failed"])

    def test_relay_oldest_counts_only_a_live_idle_agent_and_no_fyi(self):
        now = self.clock()
        self.presence("idle_a", "idle", now - 10)
        self.presence("busy_b", "working", now - 10)
        self.presence("gone_c", "idle", now - 600)                       # no live holder
        self.message("busy_b", now - 5000)                                # waits for a busy agent: fine
        self.message("gone_c", now - 6000)
        self.message("idle_a", now - 7000, priority="fyi")               # read when it chooses
        self.assertEqual(self.h.relay_oldest(), 0.0)
        self.message("idle_a", now - 900)
        self.assertEqual(self.h.relay_oldest(), 900)
        self.assertEqual([c["state"] for c in self.h.evaluate()], ["degraded"])

    def test_a_change_writes_one_event_and_a_steady_state_none(self):
        self.h.answered(0.4)
        self.assertEqual([(c["check"], c["state"]) for c in self.h.evaluate()],
                         [("server.health", "ok"), ("relay.oldest", "ok")])
        for _ in range(3):
            self.clock.t += 60
            self.h.answered(0.5)
            self.assertEqual(self.h.evaluate(), [])                     # no event a minute
        self.clock.t += 60
        self.h.answered(25)
        change = self.h.evaluate()
        self.assertEqual([(c["check"], c["state"], c["limit"], c["owner"]) for c in change],
                         [("server.health", "failed", 20.0, "of_server")])
        self.assertEqual([n for n, _ in self.said], ["health_changed"] * 3)
        self.clock.t += H.WINDOW + 1                                     # no answer since: the state stays
        self.assertEqual(self.h.evaluate(), [])
        states = {s["check"]: s for s in self.h.states()}
        self.assertEqual((states["server.health"]["state"], states["server.health"]["limit_failed"]), ("failed", 20.0))

    def test_the_server_measures_its_health_answers_and_serves_the_states(self):
        with urllib.request.urlopen(self.url + "/v1/health", timeout=5) as r:
            r.read()
        time.sleep(0.05)
        v = self.app.health_checks.server_health()
        self.assertIsNotNone(v)
        self.assertLess(v, 2.0)
        self.app.health_checks.evaluate()
        got = self.client._authed("GET", "/v1/health/checks")
        body = got[1] if isinstance(got, tuple) else got
        self.assertEqual({c["check"] for c in body["checks"]}, {"server.health", "relay.oldest"})
        self.assertEqual({c["owner"] for c in body["checks"]}, {"of_server"})
        try:                                                             # machines only
            urllib.request.urlopen(self.url + "/v1/health/checks", timeout=5)
            self.fail("served without a token")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 401)


if __name__ == "__main__":
    unittest.main()
