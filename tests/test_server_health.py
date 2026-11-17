"""of-server's own checks (docs/health.md §2, §3, §5; of_architect's hand-overs 154a0cbf and 09042907): server.health
as answered, relay.oldest, one state each with its limits, an of.health_changed on a change of relay.oldest only (each
machine records server.health), its owner told after 5 minutes failed, when it is over and once a day typed in, and
GET /v1/health/checks."""

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
        self.clock, self.said, self.told = Clock(), [], []
        self.h = H.Health(self.app.store, now=self.clock, emit=lambda name, data: self.said.append((name, data)),
                          tell=lambda pos, text, typed: self.told.append((pos, text, typed)) or "id")

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
        self.assertEqual(self.h.relay_oldest(), 0.0)                     # first seen now: its age before counts nothing
        self.clock.t += 700
        self.presence("idle_a", "idle", self.clock() - 10)
        self.assertEqual(self.h.relay_oldest(), 700)
        self.assertEqual([c["state"] for c in self.h.evaluate()], ["degraded"])

    def test_a_wait_on_a_busy_agent_counts_nothing_and_a_held_message_none(self):
        # of_review on organic-framework PR 142: a message 80 minutes on a busy agent read failed once the turn ended
        now = self.clock()
        self.presence("b", "working", now - 10)
        self.message("b", now - 4800)
        self.assertEqual(self.h.relay_oldest(), 0.0)
        self.clock.t += 60
        self.presence("b", "idle", self.clock() - 5)                     # its turn ended
        self.assertEqual(self.h.relay_oldest(), 0.0)
        self.clock.t += 30
        self.presence("b", "idle", self.clock() - 5)
        self.assertEqual(self.h.relay_oldest(), 30)
        with self.app.store.conn() as c:                                 # the person's text holds it
            c.execute("UPDATE messages SET reason = 'the input box holds text (12 chars)'")
        self.assertEqual(self.h.relay_oldest(), 0.0)

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
        # server.health's changes are each machine's to record (of health run measures it there): recorded once,
        # not by both sides (hand-over 09042907); relay.oldest's are the server's
        self.assertEqual([(n, d["check"]) for n, d in self.said], [("health_changed", "relay.oldest")])
        self.clock.t += H.TELL_AFTER + 60
        self.h.answered(25)
        self.h.evaluate()
        self.assertEqual(self.told, [])                                  # and so are its owner's notes
        self.clock.t += H.WINDOW + 1                                     # no answer since: the state stays
        self.assertEqual(self.h.evaluate(), [])
        states = {s["check"]: s for s in self.h.states()}
        self.assertEqual((states["server.health"]["state"], states["server.health"]["limit_failed"]), ("failed", 20.0))

    def failing(self, minutes):
        """relay.oldest failed: a message seen queued for a live, idle agent for over an hour, then MINUTES more."""
        self.presence("idle_a", "idle", self.clock() - 10)
        self.message("idle_a", self.clock() - 10)
        self.h.evaluate()
        self.clock.t += 3601
        for _ in range(minutes + 1):
            self.presence("idle_a", "idle", self.clock() - 10)
            self.h.evaluate()
            self.clock.t += 60

    def test_relay_oldest_failed_is_told_after_five_minutes_and_when_it_is_over(self):
        self.failing(3)
        self.assertEqual({s["check"]: s["state"] for s in self.h.states()}["relay.oldest"], "failed")
        self.assertEqual(self.told, [])                                  # under 5 minutes failed: nobody yet
        for _ in range(3):
            self.presence("idle_a", "idle", self.clock() - 10)
            self.h.evaluate()
            self.clock.t += 60
        self.assertEqual(len(self.told), 1)
        pos, text, typed = self.told[0]
        self.assertEqual((pos, typed), ("of_server", False))             # one --fyi to the owner
        self.assertIn("relay.oldest failed at", text)
        for _ in range(10):                                              # still failed: no second note this day
            self.presence("idle_a", "idle", self.clock() - 10)
            self.h.evaluate()
            self.clock.t += 60
        self.assertEqual(len(self.told), 1)
        with self.app.store.conn() as c:                                 # typed in at last
            c.execute("UPDATE messages SET state = 'delivered'")
        self.h.evaluate()
        self.assertEqual(len(self.told), 2)
        self.assertEqual(self.told[1][2], False)
        self.assertIn("the failure you were told of is over", self.told[1][1])
        self.clock.t += 60
        self.h.evaluate()
        self.assertEqual(len(self.told), 2)

    def test_a_failure_within_five_minutes_tells_nobody(self):
        self.failing(2)
        with self.app.store.conn() as c:
            c.execute("UPDATE messages SET state = 'delivered'")
        self.h.evaluate()
        self.assertEqual(self.told, [])
        self.assertEqual([d["state"] for n, d in self.said if d["check"] == "relay.oldest"], ["ok", "failed", "ok"])

    def test_a_failure_that_lasts_is_typed_in_once_a_day(self):
        self.failing(6)
        self.assertEqual([t[2] for t in self.told], [False])
        self.clock.t += H.REMIND_EVERY - 7 * 60                          # a day after the failure began
        self.presence("idle_a", "idle", self.clock() - 10)
        self.h.evaluate()
        self.assertEqual([t[2] for t in self.told], [False, True])       # typed into the owner's pane
        self.assertIn("Reminded once a day", self.told[1][1])
        for _ in range(3):
            self.clock.t += 3600
            self.presence("idle_a", "idle", self.clock() - 10)
            self.h.evaluate()
        self.assertEqual(len(self.told), 2)                              # not again the same day
        self.clock.t += H.REMIND_EVERY
        self.presence("idle_a", "idle", self.clock() - 10)
        self.h.evaluate()
        self.assertEqual([t[2] for t in self.told], [False, True, True])

    def test_a_note_the_relay_cannot_take_is_tried_again(self):
        answers = [None, "id"]
        self.h.tell = lambda pos, text, typed: self.told.append(typed) or answers.pop(0)
        self.failing(5)
        self.assertEqual(self.told, [False])
        self.presence("idle_a", "idle", self.clock() - 10)
        self.h.evaluate()
        self.assertEqual(self.told, [False, False])
        self.clock.t += 60
        self.presence("idle_a", "idle", self.clock() - 10)
        self.h.evaluate()
        self.assertEqual(self.told, [False, False])                      # told once it was queued

    def test_the_server_tells_through_its_own_relay(self):
        msg = self.app.relay.system_post("ofb:of_server", "Health: a test note", priority="fyi")
        with self.app.store.conn() as c:
            row = c.execute("SELECT to_org, to_position, from_org, from_position, authority, sender_machine, state,"
                            " priority FROM messages WHERE msg_id = ?", (msg,)).fetchone()
        self.assertEqual(row, ("ofb", "of_server", "ofb", "of_server", "none", "of-server", "queued", "fyi"))
        self.assertIsNone(self.app.relay.system_post("acme:of_server", "x"))          # not served here
        self.assertIsNone(self.app.relay.system_post("ofb:Bad Name", "x"))
        old = H.OWNER_ORG
        try:
            H.OWNER_ORG = "ofb"
            got = self.app._tell_health("of_server", "Health: typed", True)
        finally:
            H.OWNER_ORG = old
        with self.app.store.conn() as c:
            self.assertEqual(c.execute("SELECT priority FROM messages WHERE msg_id = ?", (got,)).fetchone(), ("normal",))

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
