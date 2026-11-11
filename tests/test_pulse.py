"""of ask / of status / of pulse: nobody waits unseen."""

import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_asks  # noqa: E402
import of_events  # noqa: E402
import of_pulse  # noqa: E402

T0 = time.mktime(time.strptime("2026-11-05T10:00:00", "%Y-%m-%dT%H:%M:%S")) - time.timezone


def iso(minutes):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(T0 + minutes * 60))


def ev(i, name, position, minutes, sid=None, **payload):
    return {"event_id": f"e{i:02d}", "ts": iso(minutes), "name": name, "session_id": sid or position,
            "position": position, "payload": payload}


POS = [{"name": n, "status": s} for n, s in (("a", "active"), ("b", "active"), ("c", "active"), ("d", "active"),
                                              ("e", "parked"), ("f", "active"))]
AGENTS = [{"pane_id": f"p{n}", "agent_status": st, "name": n} for n, st in
          (("a", "idle"), ("b", "idle"), ("c", "working"), ("d", "idle"), ("e", "idle"), ("f", "blocked"))]


def fake_bind(positions, agents):
    by = {a["name"]: a for a in agents}
    return [(p, by[p["name"]]) for p in positions if p["name"] in by], [], []


class AsksTest(unittest.TestCase):
    def test_declared_asks_group_and_close_by_position(self):
        rows = [ev(1, "of.agent_question", "a", 0, "S", detected_by="declared",
                   questions=[{"question": "Merge PR 2349 now or after the CA window?"}]),
                ev(2, "of.agent_question", "b", 1, "S", detected_by="declared",
                   questions=[{"question": "merge PR #2349 now, or after the CA window"}]),
                ev(3, "of.agent_question", "b", 2, "S", detected_by="declared", questions=[{"question": "Invite TTL?"}]),
                ev(4, "of.state_declared", "a", 3, "S", state="working")]
        asks = of_asks.project(rows)
        # of status working never closes a declared ask: the agent carries on while it waits (2026-11-06)
        self.assertEqual({a["event_id"]: a["state"] for a in asks}, {"e01": "open", "e02": "open", "e03": "open"})
        reask = rows[:3] + [ev(6, "of.agent_question", "b", 2.5, "S", detected_by="declared", topic_key="k",
                                questions=[{"question": "Invite TTL, 72h?"}])]
        rows[2]["payload"]["topic_key"] = "k"
        self.assertEqual([a["state"] for a in of_asks.project(reask) if a["position"] == "b"], ["open", "superseded", "open"])
        groups = of_asks.group(of_asks.project(rows[:3]))
        self.assertEqual([g["who"] for g in groups], [["a", "b"], ["b"]])
        relayed = rows + [ev(7, "of.human_prompt", "b", 3.5, "S", origin="injected", text="Message from x")]
        self.assertEqual([a["state"] for a in of_asks.project(relayed) if a["position"] == "b"], ["open", "open"])
        rows.append(ev(5, "of.agent_question_answered", None, 4, "x", ask_ids=["e03"], by="owner"))
        self.assertEqual([a["event_id"] for a in of_asks.project(rows) if a["state"] == "open"], ["e01", "e02"])


class ProjectTest(unittest.TestCase):
    def test_one_row_per_position_most_urgent_first(self):
        rows = [ev(1, "of.agent_question", "a", 0, detected_by="declared", questions=[{"question": "q?"}]),
                ev(2, "of.state_declared", "b", 5, state="waiting", waiting_on="c", note="need the API"),
                ev(3, "of.state_declared", "a", 1, state="waiting", waiting_on="founder")]
        asks = of_asks.project(rows)
        idle = {"pa": T0, "pb": T0, "pd": T0, "pe": T0}
        out = of_pulse.project(POS, AGENTS, rows, asks, idle, now=T0 + 30 * 60, bind=fake_bind)
        got = {r["position"]: (r["kind"], r["on"]) for r in out}
        self.assertEqual(got, {"a": ("waiting_owner", "founder"), "f": ("waiting_owner", "founder"),
                               "b": ("waiting_peer", "c"), "d": ("stalled", None)})  # c works; e is parked
        self.assertEqual([r["kind"] for r in out], ["waiting_owner", "waiting_owner", "waiting_peer", "stalled"])
        self.assertEqual(len([r for r in out if r["position"] == "a"]), 1)  # ask + declared + ... = one row
        fresh = of_pulse.project(POS, AGENTS, [], [], {"pd": T0}, now=T0 + 10 * 60, bind=fake_bind)
        self.assertNotIn("d", {r["position"] for r in fresh if r["kind"] == "stalled"})  # idle only 10 min
        done = [ev(9, "of.state_declared", "d", 1, state="done")]
        later = of_pulse.project(POS, AGENTS, done, [], {"pd": T0}, now=T0 + 30 * 60, bind=fake_bind)
        self.assertNotIn("d", {r["position"] for r in later})  # declared done: a reason, not a stall


class RunTest(unittest.TestCase):
    def test_notifications_are_rate_limited_and_peers_nudged(self):
        with tempfile.TemporaryDirectory() as d:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = d
            try:
                sent, typed = [], []
                rows = [ev(1, "of.agent_question", "a", 0, detected_by="declared", questions=[{"question": "q?"}]),
                        ev(2, "of.state_declared", "c", 0, state="waiting", waiting_on="d", note="review")]
                asks = of_asks.project(rows)
                mem = of_pulse.load_memory()
                mem["panes"] = {"pd": ["idle", T0]}
                of_pulse.save_memory(mem)
                kw = dict(send=lambda t, b, sound="request": sent.append(t), log=lambda m: None,
                          prompt=lambda pane, text, who: typed.append((pane, text)))
                of_pulse.run(POS, AGENTS, rows, asks, fake_bind, now=T0 + 40 * 60, **kw)
                self.assertTrue(any("stalled" in t for t in sent))
                self.assertTrue(any("waiting on you over 30 min" in t for t in sent))
                self.assertEqual([p for p, _ in typed], ["pd"])
                self.assertTrue(typed[0][1].startswith("Message from c (via of pulse, authority: none"))
                import of_store
                c = of_store.connect()
                self.assertEqual(c.execute("SELECT count(*) FROM event WHERE name = 'of.attention' "
                                           "AND payload LIKE '%herdr_blocked%'").fetchone()[0], 1)  # f is blocked
                sent.clear(), typed.clear()
                of_pulse.run(POS, AGENTS, rows, asks, fake_bind, now=T0 + 45 * 60, **kw)
                self.assertEqual(sent, [])   # same stalls, reminder within the hour, nudge within 30 min
                self.assertEqual(typed, [])
                self.assertEqual(c.execute("SELECT count(*) FROM event WHERE name = 'of.attention'").fetchone()[0], 1)
                c.close()
            finally:
                of_events.STATE_DIR = saved



class DiskCheckTest(unittest.TestCase):
    def test_low_disk_warns_once_a_day_per_episode(self):
        sent, mem, now, GB = [], {}, 1_000_000.0, 10 ** 9
        send = lambda t, b, sound="request": sent.append(t)  # noqa: E731
        of_pulse.disk_check(mem, now, send, free=60 * GB, log=lambda m: None)
        self.assertEqual(sent, [])
        of_pulse.disk_check(mem, now, send, free=49 * GB, log=lambda m: None)
        self.assertEqual(sent, ["Disk: 49 GB free (< 50)"])
        of_pulse.disk_check(mem, now + 300, send, free=48 * GB, log=lambda m: None)
        of_pulse.disk_check(mem, now + 600, send, free=52 * GB, log=lambda m: None)  # between the marks: same episode
        of_pulse.disk_check(mem, now + 900, send, free=49 * GB, log=lambda m: None)
        self.assertEqual(len(sent), 1)
        of_pulse.disk_check(mem, now + 86400, send, free=47 * GB, log=lambda m: None)  # still low a day later
        self.assertEqual(len(sent), 2)
        of_pulse.disk_check(mem, now + 86500, send, free=56 * GB, log=lambda m: None)  # recovered: the episode ends
        of_pulse.disk_check(mem, now + 86600, send, free=49 * GB, log=lambda m: None)
        self.assertEqual(len(sent), 3)


class ParkedButWorkingTest(unittest.TestCase):
    """A position parked in the registry while it works: to the structure keeper once a day (of_architect)."""

    def test_keeper_told_once_a_day_never_the_founder(self):
        pos = [{"name": "acme_org", "role": "org", "status": "active", "_org": "mt"},
               {"name": "k8s_keeper", "role": "keeper", "status": "parked", "_org": "mt"},
               {"name": "old_one", "role": "keeper", "status": "parked", "_org": "mt"},
               {"name": "of_architect", "role": "architect", "status": "active", "_org": "ofb"}]
        agents = [{"pane_id": "p0", "agent_status": "working", "name": "acme_org"},
                  {"pane_id": "p1", "agent_status": "working", "name": "k8s_keeper"},
                  {"pane_id": "p2", "agent_status": "idle", "name": "old_one"}]
        with tempfile.TemporaryDirectory() as d:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = d
            try:
                sent, typed = [], []
                kw = dict(send=lambda t, b, sound="request": sent.append(t), log=lambda m: None,
                          prompt=lambda pane, text, who: typed.append((pane, text)))
                for k in range(3):  # k8s_keeper works through three pulses (10 minutes); the keeper is busy
                    of_pulse.run(pos, agents, [], [], fake_bind, now=T0 + 300 * k, **kw)
                self.assertEqual(typed, [])  # the keeper is working: wait for it to go idle
                agents[0]["agent_status"] = "idle"
                agents[1]["agent_status"] = "idle"
                of_pulse.run(pos, agents, [], [], fake_bind, now=T0 + 900, **kw)
                self.assertEqual([p for p, _ in typed], ["p0"])
                self.assertIn("k8s_keeper is parked, but k8s_keeper is working", typed[0][1])
                self.assertTrue(typed[0][1].startswith("Message from of_pulse (via of pulse"))
                self.assertFalse(any("parked" in t for t in sent))  # never the founder
                typed.clear()
                of_pulse.run(pos, agents, [], [], fake_bind, now=T0 + 1200, **kw)
                self.assertEqual(typed, [])  # once a day
                # acme_org, 2026-11-07: a short reply turn that ends in a declared idle is not work on a paused mandate
                mem = {}
                short = [ev(1, "of.state_declared", "old_one", 2, state="idle", note="parked; answered a peer")]
                busy = [dict(a, agent_status="working") if a["name"] == "old_one" else a for a in agents]
                of_pulse.parked_but_working(pos, busy, fake_bind, [], T0, mem)
                self.assertEqual(of_pulse.parked_but_working(pos, agents, fake_bind, short, T0 + 300, mem), [])
                self.assertEqual(of_pulse.structure_keeper(pos, "ofb"), "of_architect")
                rows = [ev(1, "of.state_declared", "old_one", 0, state="working")]
                hits = of_pulse.parked_but_working(pos, agents, fake_bind, rows, T0 + 10 * 60, {})
                self.assertEqual([(h[0], h[2]) for h in hits], [("old_one", "acme_org")])  # declared working 10 min ago
                self.assertEqual(of_pulse.parked_but_working(pos, agents, fake_bind, rows, T0 + 3 * 3600, {}), [])
                # acme_org, 2026-11-09: parked, and declared working to answer what peers typed in: not flagged
                peer = [ev(1, "of.agent_message_in", "old_one", 0, **{"from": "acme:storefront_operator"}),
                        ev(2, "of.state_declared", "old_one", 4, state="working")]
                self.assertEqual(of_pulse.parked_but_working(pos, agents, fake_bind, peer, T0 + 10 * 60, {}), [])
                founder = peer + [ev(3, "of.human_prompt", "old_one", 3, origin="human", text="go on with the nonce work"),
                                  ev(4, "of.state_declared", "old_one", 5, state="working")]
                self.assertEqual(len(of_pulse.parked_but_working(pos, agents, fake_bind, founder, T0 + 10 * 60, {})), 1)
            finally:
                of_events.STATE_DIR = saved

if __name__ == "__main__":
    unittest.main()


class WorkspaceCountTest(unittest.TestCase):
    def test_counts_per_org_workspace_and_desk(self):
        import of_asks_tui
        positions = [{"name": "a", "_org": "ofb"}, {"name": "b", "_org": "mt"}, {"name": "c", "_org": "mt"}]
        agents = [{"pane_id": "p1", "workspace_id": "w6", "name": "a"}, {"pane_id": "p2", "workspace_id": "w4", "name": "b"}]
        bind = lambda ps, ags: ([(p, next(x for x in ags if x["name"] == p["name"])) for p in ps
                                 if any(x["name"] == p["name"] for x in ags)], [], [])
        asks = [{"state": "open", "position": "a"}, {"state": "open", "position": "b"}, {"state": "answered", "position": "c"}]
        ws = [{"workspace_id": "w3", "label": "organic-framework"}, {"workspace_id": "w4", "label": "acme"},
              {"workspace_id": "w6", "label": "desk"}]
        counts = of_asks_tui.workspace_counts(positions, agents, asks, bind, ws, {"ofb": "organic-framework", "mt": "acme"})
        self.assertEqual(counts, {"w3": 1, "w4": 1, "w6": 1})

    def test_tui_items_group_and_registry(self):
        import of_asks_tui
        import sqlite3
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE event (event_id, ts, name, session_id, position, payload, source, org)")
        import json as _j
        c.execute("INSERT INTO event VALUES ('e1','2026-11-05T10:00:00Z','of.agent_question','S','a',?, 'of', 'o')",
                  (_j.dumps({"detected_by": "declared", "questions": [{"question": "Merge now?"}]}),))
        data = of_asks_tui.load([{"name": "a"}, {"name": "z", "waiting_on": "owner", "summary": "needs a yes"}], c)
        self.assertEqual([(lbl, [i["who"] for i in its]) for lbl, its in data],
                         [("Waiting on you", [["a"]]), ("Registry-declared (no notifications)", [["z"]])])

    def test_an_empty_list_is_checked_against_the_log_before_it_is_believed(self):
        import of_asks_tui
        full = [("Waiting on you", [{"who": ["a"]}]), ("Registry-declared (no notifications)", [])]
        empty = [("Waiting on you", []), ("Registry-declared (no notifications)", [])]
        calls = []

        def reproject():
            calls.append(1)
            return full
        self.assertIs(of_asks_tui.checked(empty, full, reproject), full)  # a half-built view: the log wins
        self.assertIs(of_asks_tui.checked(full, empty, reproject), full)
        self.assertIs(of_asks_tui.checked(empty, empty, reproject), empty)  # nothing was open: no recheck
        self.assertEqual(len(calls), 1)


class InboundStallTest(unittest.TestCase):
    def test_a_queue_older_than_30_min_is_reported_once(self):
        import calendar
        import time as _t
        import of_pulse
        now = calendar.timegm(_t.strptime("2026-11-06T13:00:00", "%Y-%m-%dT%H:%M:%S"))
        q = {"mt:platform_owner": {"n": 25, "oldest": "2026-11-06T09:27:00Z", "at": now - 30},
             "mt:fresh": {"n": 2, "oldest": "2026-11-06T12:50:00Z", "at": now - 30},
             "mt:relay_down": {"n": 9, "oldest": "2026-11-06T09:00:00Z", "at": now - 3600}}
        sent, mem = [], {}
        saved = of_pulse.of_events.emit
        of_pulse.of_events.emit = lambda *a, **k: None
        try:
            self.assertEqual(of_pulse.inbound_stalls(mem, now, lambda t, b: sent.append((t, b)), q), ["mt:platform_owner"])
            self.assertEqual(of_pulse.inbound_stalls(mem, now, lambda t, b: sent.append((t, b)), q), [])  # once
        finally:
            of_pulse.of_events.emit = saved
        self.assertEqual(len(sent), 1)
        self.assertIn("platform_owner: 25 waiting 213m", sent[0][1])

class DelegationAskTest(unittest.TestCase):
    def test_route_and_escalate(self):
        deleg = {"id": "del-x", "unit": "platform", "to_owner": "acme_platform-owner",
                 "reporting": {"escalate_when": ["ask.blocking", "ask_unanswered > 2h"]}}
        positions = [{"name": "sec_keeper", "_unit": "platform", "_unit_owner": "acme_platform-owner", "_delegation": deleg},
                     {"name": "acme_platform-owner", "_unit": "platform", "_unit_owner": "acme_platform-owner",
                      "_delegation": deleg},
                     {"name": "of_a", "_unit": None, "_unit_owner": None, "_delegation": None}]
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(os.path.dirname(__file__), "..", "bin", "of"))
        spec = importlib.util.spec_from_loader("of_cli", loader)
        of = importlib.util.module_from_spec(spec)
        loader.exec_module(of)
        self.assertEqual([of.ask_target(p) for p in positions], ["acme_platform-owner", "founder", "founder"])
        # --to founder from a delegated unit (of_architect, 577eb3c5): the unit owner, unless a declared tag matches
        # escalate_when; nothing is refused
        sec = dict(positions[0], _delegation=dict(deleg, reporting={"escalate_when": [
            "ask.blocking", "money", "prod_change{affects: [storefront]}"]}))
        money = [{"tag": "money", "source": "declared"}]
        self.assertEqual(of.route_ask(sec, "founder", []), ("acme_platform-owner", None, True))
        self.assertEqual(of.route_ask(sec, "founder", money), ("founder", "money", False))
        self.assertEqual(of.route_ask(sec, "founder", [{"tag": "money", "source": "guessed"}]),
                         ("acme_platform-owner", None, True))  # a guessed tag never skips the unit owner
        self.assertEqual(of.route_ask(sec, "founder", [{"tag": "prod_change", "source": "declared", "systems": ["hosts"]}]),
                         ("acme_platform-owner", None, True))  # prod_change on another system
        self.assertEqual(of.route_ask(sec, "founder", [{"tag": "prod_change", "source": "declared", "systems": ["storefront"]}])[:2],
                         ("founder", "prod_change{affects: [storefront]}"))
        self.assertEqual(of.route_ask(sec, None, money), ("acme_platform-owner", None, False))  # the default stays
        self.assertEqual(of.route_ask(positions[1], "founder", []), ("founder", None, False))  # the owner asks up
        self.assertEqual(of.route_ask(positions[2], "founder", []), ("founder", None, False))
        rows = [ev(1, "of.agent_question", "sec_keeper", 0, "S", detected_by="declared", to="acme_platform-owner",
                   questions=[{"question": "rotate the etcd keys now?"}]),
                ev(2, "of.agent_question", "sec_keeper", 1, "S", detected_by="declared", to="acme_platform-owner",
                   blocking=True, questions=[{"question": "prod is down, may I roll back?"}])]
        asks = of_asks.project(rows)
        self.assertEqual(of_asks.for_owner(asks), [])  # nothing reaches the founder's queue
        out = of_pulse.project(positions, [], rows, asks, {}, now=T0 + 60, bind=fake_bind)
        self.assertEqual([(r["position"], r["kind"], r["on"]) for r in out],
                         [("sec_keeper", "waiting_peer", "acme_platform-owner")])
        esc = of_pulse.escalations(positions, asks, T0 + 5 * 60)
        self.assertEqual([(a["event_id"], rule) for a, rule, _ in esc], [("e02", "ask.blocking")])
        esc = of_pulse.escalations(positions, asks, T0 + 3 * 3600, already={"e02"})
        self.assertEqual([(a["event_id"], rule) for a, rule, _ in esc], [("e01", "ask_unanswered > 2h")])
        # at filing (of ask builds this dict before typing the frame): a blocking ask escalates at once
        filed = {"event_id": "e9", "ts": "2026-11-05T10:00:00Z", "state": "open", "to": "acme_platform-owner",
                 "position": "sec_keeper", "text": "gw2 down: roll back?", "kind": "question (declared) blocking",
                 "tags": [], "tags_declared": False}
        self.assertEqual([r for _, r, _ in of_pulse.escalations(positions, [filed], T0)], ["ask.blocking"])
        self.assertEqual(of_pulse.escalations(positions, [dict(filed, kind="question (declared)")], T0), [])
        sent = []
        saved = of_pulse.of_events.emit
        of_pulse.of_events.emit = lambda name, payload, **k: sent.append((name, payload)) or f"id-{len(sent)}"
        try:
            self.assertEqual(of_pulse.escalate(filed, "ask.blocking", "del-x"), "id-2")
        finally:
            of_pulse.of_events.emit = saved
        self.assertEqual([n for n, _ in sent], ["ask_escalated", "agent_question"])
        self.assertEqual((sent[1][1]["escalated_from"], sent[1][1]["source"], sent[1][1]["blocking"]), ("e9", "escalated", True))


class AskTokensTest(unittest.TestCase):
    def test_tokens_follow_the_projection_at_once(self):
        import of_asks_tui
        import of_herdr_asks
        calls = []
        saved = (of_herdr_asks._herdr, of_asks_tui.report_workspaces)
        of_herdr_asks._herdr = lambda *a: calls.append(("pane",) + a) or True
        of_asks_tui.report_workspaces = lambda counts: calls.append(("ws", dict(counts)))
        try:
            positions = [{"name": "a", "_org": "o", "_workspace": "org-ws", "_home_ws": "org-ws"}]
            agents = [{"pane_id": "pa", "name": "a", "workspace_id": "w1", "tokens": {}}]
            ws = [{"workspace_id": "w1", "label": "org-ws"}, {"workspace_id": "w2", "label": "desk"}]
            ask = ev(1, "of.agent_question", "a", 0, "S", detected_by="declared", questions=[{"question": "q?"}])
            asks = of_asks.project([ask])
            of_herdr_asks.refresh_ask_tokens(positions, agents, asks, fake_bind, ws)
            self.assertEqual(calls[-1], ("ws", {"w1": 1, "w2": 0}))
            self.assertIn("ask=q?", calls[0])
            agents[0]["tokens"] = {"ask": "q?"}
            calls.clear()
            done = of_asks.project([ask, ev(2, "of.agent_question_answered", None, 1, "x", ask_ids=["e01"], by="owner")])
            of_herdr_asks.refresh_ask_tokens(positions, agents, done, fake_bind, ws)
            self.assertEqual(calls[-1], ("ws", {"w1": 0, "w2": 0}))  # gone at once, not at the next change
            self.assertIn("--clear-token", calls[0])
        finally:
            of_herdr_asks._herdr, of_asks_tui.report_workspaces = saved


class ReplyTest(unittest.TestCase):
    def test_sendmessage_counts_as_a_reply(self):
        rows = [ev(1, "of.state_declared", "of_platform", 0, state="waiting", waiting_on="of_architect"),
                ev(2, "of.agent_message_out", "of_architect", 5, to="of_platform", to_position="of_platform")]
        self.assertTrue(of_pulse.replied(rows, "of_platform", "of_architect", rows[0]["ts"]))
        self.assertFalse(of_pulse.replied(rows[:1], "of_platform", "of_architect", rows[0]["ts"]))
        self.assertFalse(of_pulse.replied(rows, "of_platform", "of_architect", rows[1]["ts"]))  # before the wait
        relay = [dict(rows[1], payload={"to": "organic-framework-build:of_platform"})]
        self.assertTrue(of_pulse.replied(relay, "of_platform", "of_architect", rows[0]["ts"]))


class StudioEscalationTest(unittest.TestCase):
    def test_escalation_due_gets_one_founder_copy(self):
        import sqlite3
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = d
            of_events._CONN.clear()
            try:
                import of_store
                conn = of_store.connect(os.path.join(d, "of.db"))
                of_events.emit("escalation_due", {"delegation": "del-x", "unit": "platform", "condition": "issue.severity >= high",
                                                  "from_owner": "acme_platform-owner", "to": "acme_owner", "evidence": ["iss1"]},
                               position="acme_platform-owner")
                ran = []
                pos = [{"name": "a", "_org": "mt", "_delegations": [{"id": "del-x"}]}]
                self.assertEqual(of_pulse.studio_escalations(pos, conn, log=lambda m: None, run=lambda *a, **k: ran.append(a)), 1)
                self.assertEqual(of_pulse.studio_escalations(pos, conn, log=lambda m: None, run=lambda *a, **k: None), 0)
                self.assertIn("escalations", ran[0][0])
                q = conn.execute("SELECT payload FROM event WHERE name = 'of.agent_question'").fetchone()[0]
                self.assertIn('"to": "founder"', q)
            finally:
                of_events._CONN.clear()
                of_events.STATE_DIR = saved


class TagEscalationTest(unittest.TestCase):
    def test_tags_and_risk(self):
        import of_classify
        import of_grants
        self.assertEqual(of_classify.parse_tags("prod_change:storefront+storefront-hosts,money"),
                         [{"tag": "prod_change", "source": "declared", "systems": ["storefront", "storefront-hosts"]},
                          {"tag": "money", "source": "declared"}])
        self.assertEqual(of_pulse.matched_tag("money", [{"tag": "money", "source": "guessed", "method": "tg-v0"}])["source"],
                         "guessed")
        with self.assertRaises(ValueError):
            of_classify.parse_tags("vibes")
        self.assertIn({"tag": "irreversible", "source": "guessed", "method": of_classify.TAG_VERSION},
                      of_classify.tags_of("Удаление таблиц — необратимый шаг"))
        self.assertTrue(of_classify.destructive("kubectl delete ns prod"))
        self.assertFalse(of_classify.destructive("kubectl get pods"))
        for rule in ("prod_change{affects:[storefront]}", "money", "window_go{systems:[k8s]}", "envelope_breach",
                     "security_incident", "irreversible"):
            self.assertTrue(of_classify.ESCALATE.match(rule), rule)
        self.assertFalse(of_classify.ESCALATE.match("low-medium"))
        self.assertTrue(of_pulse.tag_match("prod_change{affects:[storefront]}", [{"tag": "prod_change", "systems": ["storefront"]}]))
        self.assertFalse(of_pulse.tag_match("prod_change{affects:[k8s]}", [{"tag": "prod_change", "systems": ["storefront"]}]))
        self.assertTrue(of_pulse.tag_match("money", [{"tag": "money"}]))
        deleg = {"id": "d", "reporting": {"escalate_when": ["money"]}}
        pos = [{"name": "x", "_delegation": deleg}]
        asks = [{"event_id": "e1", "state": "open", "to": "owner_pos", "position": "x", "ts": "2026-11-06T08:00:00Z",
                 "kind": "question (declared)", "tags": [{"tag": "money"}]}]
        self.assertEqual([r for _, r, _ in of_pulse.escalations(pos, asks, T0)], ["money"])
        issuer = {"risk_appetite": "medium", "scope": {"grants": {"depth": 2}}, "tier": "observe"}
        derived = lambda r: {"risk_appetite": r, "scope": {}, "tier": "observe"}  # noqa: E731
        probs = lambda r: "\n".join(of_grants.attenuates(derived(r), issuer) or [])  # noqa: E731
        self.assertIn("above the issuer's medium", probs("high"))
        self.assertNotIn("risk_appetite", probs("low"))
        self.assertIn("is not one of", probs("low-medium"))


class AskMarksTest(unittest.TestCase):
    def test_frame_and_tab_markers(self):
        import of_herdr_asks
        positions = [{"name": "k8s_keeper"}, {"name": "obs_keeper"}]
        agents = [{"pane_id": "p1", "name": "k8s_keeper", "tab_id": "t1"}, {"pane_id": "p2", "name": "obs_keeper", "tab_id": "t2"}]
        ask = ev(1, "of.agent_question", "k8s_keeper", 0, "S", detected_by="declared", questions=[{"question": "go?"}])
        asks = of_asks.project([ask])
        tabs = [{"tab_id": "t1", "label": "k8s"}, {"tab_id": "t2", "label": "obs ❓1"}]
        labels = {"p1": "■ k8s_keeper ■", "p2": "■ obs_keeper · ❓ asks you ■"}
        cmds = of_herdr_asks.marks(positions, agents, asks, fake_bind, tabs, labels)
        self.assertIn(["pane", "rename", "p1", "■ k8s_keeper · ❓ asks you ■"], cmds)
        self.assertIn(["pane", "rename", "p2", "■ obs_keeper ■"], cmds)       # its ask closed: marker gone
        self.assertIn(["tab", "rename", "t1", "k8s ❓1"], cmds)
        self.assertIn(["tab", "rename", "t2", "obs"], cmds)
        done = {"p1": "■ k8s_keeper · ❓ asks you ■", "p2": "■ obs_keeper ■"}
        self.assertEqual(of_herdr_asks.marks(positions, agents, asks, fake_bind,
                                             [{"tab_id": "t1", "label": "k8s ❓1"}, {"tab_id": "t2", "label": "obs"}], done), [])


class AnsweredInPaneTest(unittest.TestCase):
    def test_owner_prompt_in_the_session_answers_its_declared_asks(self):
        rows = [ev(1, "of.agent_question", "a", 0, "S1", detected_by="declared", questions=[{"question": "import?"}]),
                ev(2, "of.state_declared", "a", 1, "S1", state="working"),
                ev(3, "of.agent_question", "a", 2, "S2", detected_by="declared", questions=[{"question": "other?"}]),
                ev(4, "of.human_prompt", "a", 3, "S1", origin="human", text="да")]
        st = {a["event_id"]: a["state"] for a in of_asks.project(rows)}
        self.assertEqual(st, {"e01": "answered", "e03": "open"})  # S2's ask was not answered in S1


class PopupAnswerFrameTest(unittest.TestCase):
    def test_popup_answer_closes_only_its_ask(self):
        rows = [ev(1, "of.agent_question", "a", 0, "S", detected_by="declared", questions=[{"question": "q1?"}]),
                ev(2, "of.agent_question", "a", 1, "S", detected_by="declared", questions=[{"question": "q2?"}])]
        rows[0]["event_id"] = "01a1-aaaa-0000-11111111"
        rows[1]["event_id"] = "01a1-aaaa-0000-22222222"
        rows.append(ev(3, "of.human_prompt", "a", 2, "S", origin="human", channel="asks-ui",
                       text="Answer from the founder to your question 11111111: да"))
        st = {a["event_id"]: a["state"] for a in of_asks.project(rows)}
        self.assertEqual(st, {"01a1-aaaa-0000-11111111": "answered", "01a1-aaaa-0000-22222222": "open"})


class FreeTextAnswerTest(unittest.TestCase):
    def test_free_text_closes_only_a_single_open_ask(self):
        one = [ev(1, "of.agent_question", "a", 0, "S", detected_by="declared", questions=[{"question": "q1?"}]),
               ev(2, "of.human_prompt", "a", 1, "S", origin="human", text="да")]
        self.assertEqual([x["state"] for x in of_asks.project(one)], ["answered"])
        two = [ev(1, "of.agent_question", "a", 0, "S", detected_by="declared", questions=[{"question": "q1?"}]),
               ev(2, "of.agent_question", "a", 1, "S", detected_by="declared", questions=[{"question": "q2?"}]),
               ev(3, "of.human_prompt", "a", 2, "S", origin="human", text="да")]
        self.assertEqual([x["state"] for x in of_asks.project(two)], ["open", "open"])  # ambiguous: --answered <id>
        # the Wazuh case: a popup answer to another ask leaves this one open
        wazuh = [ev(1, "of.agent_question", "a", 0, "S", detected_by="declared", questions=[{"question": "Wazuh?"}]),
                 ev(2, "of.human_prompt", "a", 1, "S", origin="human", channel="asks-ui",
                    text="Answer from the founder to your question b3f94628: да")]
        self.assertEqual([x["state"] for x in of_asks.project(wazuh)], ["open"])


class OtherAskStaysOpenTest(unittest.TestCase):
    """2026-11-06: the popup's answer to 754659b8 closed f7ea7dc8 and d7bab4e4, because the asker also had a
    heuristic question open in the session and that path closed every declared ask of the position."""

    def test_popup_answer_to_one_ask_leaves_the_others_and_the_open_question(self):
        rows = [ev(1, "of.agent_question", "a", 0, "S", detected_by="declared", questions=[{"question": "who?"}]),
                ev(2, "of.agent_question", "a", 1, "S", detected_by="declared", questions=[{"question": "window?"}]),
                ev(3, "of.agent_question", "a", 2, "S", detected_by="heuristic", questions=[{"question": "ok?"}],
                   classifications={"q-v2": True})]
        rows[0]["event_id"], rows[1]["event_id"] = "01a1-0000-f7ea7dc8", "01a1-0000-754659b8"
        rows.append(ev(4, "of.human_prompt", "a", 3, "S", origin="human", channel="asks-ui",
                       text="Answer from the founder to your question 754659b8: да"))
        st = {a["event_id"]: a["state"] for a in of_asks.project(rows)}
        self.assertEqual(st, {"01a1-0000-f7ea7dc8": "open", "01a1-0000-754659b8": "answered", "e03": "candidate"})

    def test_free_text_answers_the_open_question_but_not_the_declared_asks(self):
        rows = [ev(1, "of.agent_question", "a", 0, "S", detected_by="declared", questions=[{"question": "q1?"}]),
                ev(2, "of.agent_question", "a", 1, "S", detected_by="declared", questions=[{"question": "q2?"}]),
                ev(3, "of.agent_question", "a", 2, "S", detected_by="heuristic", questions=[{"question": "ok?"}],
                   classifications={"q-v2": True}),
                ev(4, "of.human_prompt", "a", 3, "S", origin="human", text="да")]
        st = {a["event_id"]: a["state"] for a in of_asks.project(rows)}
        self.assertEqual(st, {"e01": "open", "e02": "open", "e03": "answered"})


class RoutedQuestionIsNoAnswerTest(unittest.TestCase):
    def test_a_routed_question_typed_into_the_pane_answers_nothing(self):
        """2026-11-06: warehouse_keeper's routed question reached platform_owner's pane recorded as the owner's
        words (channel asks-ui) and closed its only open ask f7ea7dc8."""
        rows = [ev(1, "of.agent_question", "a", 0, "S", detected_by="declared", questions=[{"question": "who?"}]),
                ev(2, "of.human_prompt", "a", 1, "S", origin="human", channel="asks-ui",
                   text="Question from warehouse_keeper (unit platform, delegated to you; ask 2d6183ff): run it?"),
                ev(3, "of.human_prompt", "a", 2, "S", origin="human",
                   text="Message from acme:acme_org (via relay, authority: none): hi")]
        self.assertEqual([x["state"] for x in of_asks.project(rows)], ["open"])


class SchedulerPromptTest(unittest.TestCase):
    def test_scheduled_and_unknown_prompts_close_nothing_and_corrections_apply(self):
        q = lambda: ev(1, "of.agent_question", "a", 0, "S", detected_by="declared", questions=[{"question": "delete it?"}])
        for origin in ("scheduled", "unknown", "system"):
            rows = [q(), ev(2, "of.human_prompt", "a", 1, "S", origin=origin, text="Slack-watch tick")]
            self.assertEqual([x["state"] for x in of_asks.project(rows)], ["open"], origin)
        # recorded as human before the fix; `of db prompt-origins` wrote a correction long after
        rows = [q(), ev(2, "of.human_prompt", "a", 1, "S", origin="human", text="Slack-watch tick"),
                ev(3, "of.prompt_origin", "a", 90, "S", event_id="e02", origin="scheduled")]
        self.assertEqual([x["state"] for x in of_asks.project(rows)], ["open"])
        rows[2]["payload"]["event_id"] = "e99"  # a correction of another prompt changes nothing here
        self.assertEqual([x["state"] for x in of_asks.project(rows)], ["answered"])


class TurnErrorTest(unittest.TestCase):
    def test_a_turn_error_neither_answers_nor_ends_an_ask(self):
        """of_field-analyst: obs_keeper's permission request closed as answered by a rate-limit turn_error."""
        rows = [ev(1, "of.agent_question", "a", 0, "S", detected_by="declared", questions=[{"question": "go?"}]),
                ev(2, "of.permission_request", "a", 1, "S", tool="Bash"),
                ev(3, "of.human_prompt", "a", 89, "S", origin="scheduled", text="alerts-watch hourly sweep"),
                ev(4, "of.turn_error", "a", 89, "S", error="rate_limit")]
        self.assertEqual([x["state"] for x in of_asks.project(rows)], ["open", "open"])
        rows.append(ev(5, "of.agent_question", "a", 95, "S", detected_by="heuristic", questions=[{"question": "ok?"}],
                       classifications={"q-v2": True}))
        rows.append(ev(6, "of.turn_error", "a", 96, "S", error="overloaded"))
        self.assertEqual(of_asks.project(rows)[-1]["state"], "candidate")  # still waiting for the judge


class RedeclaredWaitTest(unittest.TestCase):
    def test_a_reply_counts_for_the_whole_run_of_the_same_wait(self):
        rows = [ev(1, "of.state_declared", "w", 0, state="waiting", waiting_on="po", note="drill slot?"),
                ev(2, "of.agent_message_out", "po", 5, to_position="w", delivery="queued"),
                ev(3, "of.state_declared", "w", 8, state="waiting", waiting_on="po", note="founder go filed by po")]
        self.assertEqual(of_pulse.wait_run_start(rows, "w", "po"), iso(0))
        self.assertTrue(of_pulse.replied(rows, "w", "po", of_pulse.wait_run_start(rows, "w", "po")))
        rows.append(ev(4, "of.state_declared", "w", 9, state="working"))
        rows.append(ev(5, "of.state_declared", "w", 10, state="waiting", waiting_on="po"))
        self.assertEqual(of_pulse.wait_run_start(rows, "w", "po"), iso(10))  # a new wait: the old reply is past


class StuckInputTest(unittest.TestCase):
    def test_a_stuck_box_is_reported_once_to_the_keeper_never_typed_into(self):
        import json as _j
        pos = [{"name": "of_architect", "role": "architect", "status": "active", "_org": "ofb"},
               {"name": "of_lab", "role": "lab", "status": "active", "_org": "ofb"}]
        agents = [{"pane_id": "w3:pA", "agent_status": "idle", "name": "of_architect"},
                  {"pane_id": "w3:pS", "agent_status": "done", "name": "of_lab"}]
        with tempfile.TemporaryDirectory() as d:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = d
            try:
                with open(os.path.join(d, "input-stuck.json"), "w") as fh:
                    _j.dump({"w3:pS": {"count": 3, "since": T0, "chars": 900, "reported": False}}, fh)
                typed, sent = [], []
                live = {a["name"]: a for a in agents}
                of_pulse.stuck_panes(pos, agents, fake_bind, live, lambda pane, text, who: typed.append((pane, text)),
                                     lambda t, b, sound="request": sent.append(t), log=lambda m: None)
                self.assertEqual([p for p, _ in typed], ["w3:pA"])  # the keeper's pane, never w3:pS
                self.assertIn("of_lab's input box (pane w3:pS)", typed[0][1])
                self.assertIn("a person's half-typed words", typed[0][1])
                typed.clear()
                of_pulse.stuck_panes(pos, agents, fake_bind, live, lambda pane, text, who: typed.append((pane, text)),
                                     lambda t, b, sound="request": sent.append(t), log=lambda m: None)
                self.assertEqual((typed, sent), ([], []))  # once
            finally:
                of_events.STATE_DIR = saved

    def test_a_held_paste_placeholder_is_named(self):
        """Acme, 2026-11-11: a delivery's Enter was lost during compaction; the box held '[Pasted text #4 +69
        lines]' for three hours. The relay marks such a box (paste: true); the note says to press Enter."""
        import json as _j
        pos = [{"name": "of_architect", "role": "architect", "status": "active", "_org": "ofb"},
               {"name": "of_lab", "role": "lab", "status": "active", "_org": "ofb"}]
        agents = [{"pane_id": "w3:pA", "agent_status": "idle", "name": "of_architect"},
                  {"pane_id": "w3:pS", "agent_status": "idle", "name": "of_lab"}]
        with tempfile.TemporaryDirectory() as d:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = d
            try:
                with open(os.path.join(d, "input-stuck.json"), "w") as fh:
                    _j.dump({"w3:pS": {"count": 3, "since": T0, "chars": 26, "paste": True, "reported": False}}, fh)
                typed = []
                of_pulse.stuck_panes(pos, agents, fake_bind, {a["name"]: a for a in agents},
                                     lambda pane, text, who: typed.append((pane, text)), lambda *a, **k: None,
                                     log=lambda m: None)
                self.assertIn("paste placeholder", typed[0][1])
                self.assertIn("press Enter", typed[0][1])
            finally:
                of_events.STATE_DIR = saved


class LimitAskNotAWaitTest(unittest.TestCase):
    """of_capacity, 2026-11-11: a blocked limit ask routed to of_capacity became a waiting_peer row, so the pulse typed
    "stopped on a usage limit" into its pane, past of_blocked's FYI rule; and the nudge used a projection 68 s old."""

    def test_a_limit_screen_is_no_wait_on_of_capacity(self):
        pos = [{"name": "ai", "status": "active"}, {"name": "of_capacity", "status": "active"}]
        ask = {"state": "open", "position": "ai", "to": "of_capacity", "kind": "blocked:limit", "ts": "2026-11-11T06:25:51Z",
               "text": "stopped on a usage limit", "event_id": "e-limit"}
        self.assertEqual(of_pulse.project(pos, [], [], [ask], {}, now=T0), [])
        login = dict(ask, kind="blocked:login", event_id="e-login")  # a login still needs of_capacity
        self.assertEqual([(r["kind"], r["on"], r["routed_ask"]) for r in of_pulse.project(pos, [], [], [login], {}, now=T0)],
                         [("waiting_peer", "of_capacity", "e-login")])

    def test_a_nudge_rechecks_what_came_after_the_projection(self):
        with tempfile.TemporaryDirectory() as d:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = d
            try:
                r = {"position": "ai", "on": "of_capacity", "routed_ask": "e-login"}
                observed = time.time() - 60
                self.assertTrue(of_pulse.still_waits(r, observed))
                of_events.emit("state_declared", {"state": "waiting", "waiting_on": "of_capacity"}, position="ai")
                self.assertTrue(of_pulse.still_waits(r, observed))  # declared the same wait again
                of_events.emit("state_declared", {"state": "waiting", "waiting_on": "acme_throughput-owner"}, position="ai")
                self.assertFalse(of_pulse.still_waits(r, observed))  # waits on someone else now
                self.assertTrue(of_pulse.still_waits(r, time.time() + 5))  # that came before a newer projection
                of_events.emit("ask_withdrawn", {"ask_id": "e-login"}, position="ai")
                self.assertFalse(of_pulse.still_waits(r, time.time() + 5))  # the routed ask closed
            finally:
                of_events.STATE_DIR = saved


class RemoteQueueTest(unittest.TestCase):
    """docs/remote.md §3 row 3: with surface remote, the person's queue goes to their conductor, once per item."""

    def test_cards_waits_and_stuck_threads_reach_the_conductor_once(self):
        pos = [{"name": "acme_conductor", "role": "conductor", "status": "active", "_org": "acme"},
               {"name": "acme_dev", "role": "dev", "status": "active", "_org": "acme"}]
        asks = [{"event_id": "e-card-1", "state": "open", "to": "founder", "position": "acme_dev", "text": "merge it?"},
                {"event_id": "e-task-2", "state": "open", "to": "founder", "position": "acme_dev", "text": "log in",
                 "task": True},
                {"event_id": "e-done-3", "state": "answered", "to": "founder", "position": "acme_dev", "text": "x"}]
        rows = [{"position": "acme_dev", "kind": "waiting_owner", "since": "t1", "text": "needs a key"}]
        views = [({"id": "r1", "title": "Fix the build"}, "stuck", []), ({"id": "r2"}, "working", [])]
        typed, mem = [], {}
        busy = {"acme_conductor": {"pane_id": "p-c", "agent_status": "working"}}
        with tempfile.TemporaryDirectory() as d:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = d
            try:
                n = of_pulse.remote_notes(pos, busy, asks, rows, mem, lambda p, t, w: typed.append((p, t)), T0,
                                          log=lambda m: None, views=views)
                self.assertEqual((n, typed), (0, []))  # busy: nothing typed, nothing marked
                idle = {"acme_conductor": {"pane_id": "p-c", "agent_status": "idle"}}
                of_pulse.remote_notes(pos, idle, asks, rows, mem, lambda p, t, w: typed.append((p, t)), T0,
                                      log=lambda m: None, views=views)
                self.assertEqual([p for p, _ in typed], ["p-c"])
                text = typed[0][1]
                for part in ("a card from acme_dev", "a task for their hands", "acme_dev waits on them",
                             "their request is stuck: Fix the build"):
                    self.assertIn(part, text)
                self.assertNotIn("e-done-3"[-8:], text)
                of_pulse.remote_notes(pos, idle, asks, rows, mem, lambda p, t, w: typed.append((p, t)), T0,
                                      log=lambda m: None, views=views)
                self.assertEqual(len(typed), 1)  # once per item
            finally:
                of_events.STATE_DIR = saved


class TaskClaimTest(unittest.TestCase):
    """2026-11-07, task bd12d0: platform_owner's 'of asks answered' for a routed question also claimed a founder's task
    that grouped with it by text; the popup showed 'ты сказал: сделал' for a task he never opened."""

    def rows(self, by="owner"):
        q = ev(1, "of.agent_question", "sec", 0, "S1", detected_by="declared", to="platform_owner",
               questions=[{"question": "file the kernel 6.8.0-149 bug in Launchpad?"}])
        t = ev(2, "of.agent_question", "po", 1, "S2", detected_by="declared", task=True, steps="file it",
               questions=[{"question": "file the kernel 6.8.0-149 bug in Launchpad"}])
        a = ev(3, "of.agent_question_answered", None, 2, None, ask_id="e01", ask_ids=["e01", "e02"], by=by)
        return [q, t, a]

    def test_a_closure_naming_another_positions_ask_never_claims_a_task(self):
        asks = {a["event_id"]: a for a in of_asks.project(self.rows())}
        self.assertEqual(asks["e01"]["state"], "answered")
        self.assertEqual((asks["e02"]["state"], asks["e02"].get("claimed_at")), ("open", None))

    def test_groups_never_cross_recipients_or_tasks(self):
        asks = of_asks.project(self.rows()[:2])
        self.assertEqual(len(of_asks.group(asks)), 2)

    def test_the_founders_own_answer_still_claims_his_task(self):
        rows = self.rows()[1:2] + [ev(3, "of.agent_question_answered", None, 2, None, ask_id="e02", ask_ids=["e02"],
                                      by="owner", via="asks-ui", answer="сделал")]
        self.assertIsNotNone(of_asks.project(rows)[0].get("claimed_at"))
        rows[1]["payload"]["by"] = "platform_owner"   # a position's answer never claims it
        self.assertIsNone(of_asks.project(rows)[0].get("claimed_at"))


class UnreadNudgeTest(unittest.TestCase):
    def test_no_repeat_until_the_peer_had_a_turn(self):
        rows = [ev(1, "of.turn_end", "d", 5)]
        self.assertFalse(of_pulse.unread_since(rows, "d", None))
        self.assertTrue(of_pulse.unread_since(rows, "d", T0 + 10 * 60))   # nudged at 10 min, last turn at 5
        self.assertFalse(of_pulse.unread_since(rows, "d", T0 + 2 * 60))   # a turn ended after the nudge


class NudgeBackoffTest(unittest.TestCase):
    """of_architect, 2026-11-07: 30 min, then 2 h, then daily; never to a receiver waiting on the founder; never the
    same text again while unanswered."""

    def test_backoff_and_same_text(self):
        text = "x is waiting on you"
        last = {"at": T0, "n": 1, "sha": of_pulse._sha(text)}
        read = [ev(1, "of.turn_end", "d", 1)]                       # the nudge was read
        answered = read + [ev(2, "of.agent_message_out", "d", 2)]   # and the peer said something since
        self.assertFalse(of_pulse.may_nudge(last, T0 + 20 * 60, "other", answered, "d", {}))   # < 30 min
        self.assertTrue(of_pulse.may_nudge(last, T0 + 31 * 60, "other", answered, "d", {}))
        self.assertFalse(of_pulse.may_nudge(last, T0 + 31 * 60, text, read, "d", {}))         # same text, unanswered
        self.assertTrue(of_pulse.may_nudge(last, T0 + 31 * 60, text, answered, "d", {}))
        two = dict(last, n=2)
        self.assertFalse(of_pulse.may_nudge(two, T0 + 90 * 60, "other", answered, "d", {}))   # 2 h after the second
        three = dict(last, n=5)
        self.assertFalse(of_pulse.may_nudge(three, T0 + 20 * 3600, "other", answered, "d", {}))  # daily after that
        self.assertTrue(of_pulse.may_nudge(three, T0 + 25 * 3600, "other", answered, "d", {}))
        self.assertFalse(of_pulse.may_nudge({"at": 0, "n": 0, "sha": None}, T0, text, [], "d",
                                            {"d": {"state": "waiting", "waiting_on": "founder"}}))

    def test_a_new_wait_starts_the_count_again(self):
        old = {"at": T0, "n": 4, "sha": "x", "since": iso(0)}
        self.assertEqual(of_pulse.nudge_state(old, iso(0))["n"], 4)
        self.assertEqual(of_pulse.nudge_state(old, iso(60))["n"], 0)
        self.assertEqual(of_pulse.nudge_state(T0, None)["n"], 1)   # the old bare-time state


class DeclaredTagsAuthoritativeTest(unittest.TestCase):
    """of_architect, 2026-11-07: declared tags are authoritative; a guessed tag escalates only an untagged ask."""

    def run_esc(self, tags, declared=False):
        deleg = {"id": "d", "reporting": {"escalate_when": ["prod_change"]}}
        pos = [{"name": "x", "_delegation": deleg}]
        asks = [{"event_id": "e1", "state": "open", "to": "throughput_owner", "position": "x", "ts": "2026-11-06T08:00:00Z",
                 "kind": "question (declared)", "tags": tags, "tags_declared": declared}]
        return [r for _, r, _ in of_pulse.escalations(pos, asks, T0)]

    def test_each_case(self):
        guessed = {"tag": "prod_change", "source": "guessed", "method": "tg-v0"}
        self.assertEqual(self.run_esc([guessed]), ["prod_change"])                        # untagged: the guess counts
        self.assertEqual(self.run_esc([guessed], declared=True), [])                       # --tags none: nothing fires
        self.assertEqual(self.run_esc([{"tag": "money", "source": "declared"}, guessed], declared=True), [])
        self.assertEqual(self.run_esc([{"tag": "prod_change", "source": "declared"}], declared=True), ["prod_change"])


class GuardAskCloseTest(unittest.TestCase):
    def test_the_pulse_closes_its_guard_ask_once_the_guard_works(self):
        """of_server, 2026-11-10: ask 574af69d stayed open after the guard's cause was fixed."""
        import of_store
        with tempfile.TemporaryDirectory() as d:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = d
            of_events._CONN.clear()
            try:
                conn = of_store.connect(os.path.join(d, "of.db"))
                rec = of_events.record("agent_question", {"detected_by": "declared", "source": "guard_check",
                                                          "to": "founder", "topic_key": "guard-broken-/h/.claude",
                                                          "questions": [{"question": "guard?"}]}, position="of_platform")
                of_store.add_event(conn, rec)
                self.assertEqual(of_pulse.close_guard_asks("/h/.claude", log=lambda m: None, conn=conn), 1)
                of_events._CONN.clear()
                state = {a["event_id"]: a["state"] for a in of_asks.project(of_asks.load(conn))}
                self.assertEqual(state[rec["of.event.id"]], "answered")
                self.assertEqual(of_pulse.close_guard_asks("/h/.claude", log=lambda m: None, conn=conn), 0)  # once
                self.assertEqual(of_pulse.close_guard_asks("/other", log=lambda m: None, conn=conn), 0)
                conn.close()
            finally:
                of_events._CONN.clear()
                of_events.STATE_DIR = saved


class GuessedTagTest(unittest.TestCase):
    """acme_org, 2026-11-09: "Research only; no production change" from partner-feed_research was tagged prod_change and
    escalated to the founder by an storefront rule; tg-v1 reads the negation and a guessed tag names the asker's system."""

    RULE = "prod_change{affects:[storefront, wholesale, bot-core, storefront-hosts, returns, contracts]}"

    def test_a_negated_mention_is_no_tag(self):
        import of_classify
        tags = lambda t: [x["tag"] for x in of_classify.tags_of(t)]  # noqa: E731
        self.assertEqual(tags("Please route a runner kit and a slot. Research only; no production change."), [])
        self.assertEqual(tags("Это только чтение, не трогает прод"), [])
        self.assertEqual(tags("We can't deploy today; nothing to pay"), [])
        self.assertEqual(tags("Deploy the runner to production at 15:00?"), ["prod_change"])
        self.assertEqual(tags("Можно выкатить в прод?"), ["prod_change"])
        self.assertEqual(tags("The test does not touch production. Then deploy it?"), ["prod_change"])  # one is not

    def test_a_guessed_tag_meets_an_affects_list_only_by_its_system(self):
        import of_classify
        guess = of_classify.tags_of("Deploy it to production?", systems=["partner-feed"])
        self.assertEqual(guess[0]["systems"], ["partner-feed"])
        self.assertFalse(of_pulse.tag_match(self.RULE, guess))
        self.assertTrue(of_pulse.tag_match(self.RULE, of_classify.tags_of("Deploy it?", systems=["storefront"])))
        bare = of_classify.tags_of("Deploy it?")
        self.assertFalse(of_pulse.tag_match(self.RULE, bare))  # tg-v0 asks in the store: no systems, no match
        self.assertTrue(of_pulse.tag_match("prod_change", bare))  # a rule with no list still takes the guess
        self.assertTrue(of_pulse.tag_match(self.RULE, [{"tag": "prod_change", "source": "declared"}]))  # declared

    def test_the_incident_does_not_escalate(self):
        import of_classify
        deleg = {"id": "del-2026-11-07-data-products", "reporting": {"escalate_when": ["ask.blocking", self.RULE]}}
        pos = [{"name": "partner-feed_research", "system": "partner-feed", "_delegation": deleg}]
        text = "Route a runner kit and a slot for the spread study. Research only; no production change."
        for t in (text, "Route a runner kit; we deploy the study to production runners"):
            asks = [{"event_id": "e1", "state": "open", "to": "acme_throughput-owner", "position": "partner-feed_research",
                     "ts": "2026-11-09T20:58:00Z", "kind": "question (declared)", "tags_declared": False,
                     "tags": of_classify.tags_of(t, systems=["partner-feed"])}]
            self.assertEqual(of_pulse.escalations(pos, asks, T0), [], t)


class DiffApprovalTest(unittest.TestCase):
    def test_free_text_never_closes_a_diff_approval(self):
        """acme_org, 2026-11-07: the founder typing about something else closed ask 8f1658a6, a spawn diff's card."""
        rows = [ev(1, "of.agent_question", "acme_org", 0, "S", detected_by="declared", diff_id="sd-x",
                   questions=[{"question": "Spawn the throughput owner?"}]),
                ev(2, "of.human_prompt", "acme_org", 1, "S", origin="human", text="about the agent mailbox: …")]
        self.assertEqual([x["state"] for x in of_asks.project(rows)], ["open"])
        rows[0]["event_id"] = "01a1-0000-8f1658a6"
        rows.append(ev(3, "of.human_prompt", "acme_org", 2, "S", origin="human", channel="asks-ui",
                       text="Answer from the founder to your question 8f1658a6: да"))
        self.assertEqual([x["state"] for x in of_asks.project(rows)], ["answered"])  # the popup answers it

    def test_the_diffs_approval_closes_its_card_and_free_text_still_answers_the_other_ask(self):
        rows = [ev(1, "of.agent_question", "acme_org", 0, "S", detected_by="declared", diff_id="sd-x",
                   questions=[{"question": "Spawn?"}]),
                ev(2, "of.agent_question", "acme_org", 1, "S", detected_by="declared", questions=[{"question": "Goals?"}]),
                ev(3, "of.human_prompt", "acme_org", 2, "S", origin="human", text="three goals, yes"),
                ev(4, "of.diff_applied", None, 3, None, id="sd-x", digest="d")]
        st = {a["event_id"]: a["state"] for a in of_asks.project(rows)}
        self.assertEqual(st, {"e01": "answered", "e02": "answered"})
        self.assertEqual(of_asks.project(rows)[0]["closed_by"], "e04")


class PositionReplyTest(unittest.TestCase):
    """An ask to a unit owner who is an agent closes on that owner's reply (of_field-analyst, 2026-11-06)."""

    def q(self, i, text, to="platform_owner"):
        return ev(i, "of.agent_question", "k8s_keeper", i, "S", detected_by="declared", to=to,
                  questions=[{"question": text}])

    def test_the_asked_position_answers_others_do_not(self):
        rows = [self.q(1, "Drain node 3 now?"),
                ev(2, "of.agent_message_in", "k8s_keeper", 2, "S", **{"from": "acme:acme_org"}, via="relay"),
                ev(3, "of.agent_message_in", "k8s_keeper", 3, "S", **{"from": "acme:platform_owner"}, via="relay")]
        a = of_asks.project(rows)[0]
        self.assertEqual((a["state"], a["closed_by"]), ("answered", "e03"))

    def test_reply_to_names_one_of_several(self):
        rows = [self.q(1, "Drain node 3 now?"), self.q(2, "Rotate the etcd certs?"),
                ev(3, "of.agent_message_in", "k8s_keeper", 3, "S", **{"from": "acme:platform_owner"}, via="relay"),
                ev(4, "of.agent_message_in", "k8s_keeper", 4, "S", **{"from": "platform_owner"}, reply_to="e02")]
        a1, a2 = of_asks.project(rows)
        self.assertEqual(a1["state"], "open")  # two open to the same owner and no reply_to: neither guessed
        self.assertEqual((a2["state"], a2["closed_by"]), ("answered", "e04"))

    def test_an_ask_to_the_founder_is_not_closed_by_a_peer(self):
        rows = [self.q(1, "Merge PR 12?", to="founder"),
                ev(2, "of.agent_message_in", "k8s_keeper", 2, "S", **{"from": "founder"}, via="relay")]
        self.assertEqual(of_asks.project(rows)[0]["state"], "open")


class ReadsTest(unittest.TestCase):
    """of_studio loads events by kind: every kind the hook or derive writes is read or neutral."""

    def test_every_written_kind_is_read_or_neutral(self):
        import re as _re
        written = set()
        for f, rx in (("of_hook.py", r'write\("([a-z_]+)"'), ("of_derive.py", r'self\.emit\("([a-z_]+)"')):
            with open(os.path.join(HERE, "..", "bin", f)) as fh:
                written |= {"of." + k for k in _re.findall(rx, fh.read())}
        self.assertTrue(written)
        self.assertEqual(sorted(written - set(of_asks.READS) - set(of_asks.NEUTRAL)), [])

    def test_a_pulled_reply_counts_a_relay_copy_does_not(self):
        self.assertTrue(of_asks.relevant("of.agent_message_in", "of", {"delivery": "pulled"}))
        self.assertFalse(of_asks.relevant("of.agent_message_in", "of", {"delivery": "delivered"}))
        self.assertFalse(of_asks.relevant("of.pulse", "hook", {}))
        sub = {"subagent": {"id": "a27", "type": ""}}
        self.assertFalse(of_asks.relevant("of.agent_question", "hook", sub))  # a prompt-suggestion side query
        self.assertTrue(of_asks.relevant("of.permission_request", "hook", sub))  # blocks on the owner


class TaskTest(unittest.TestCase):
    """of_architect, URGENT 2026-11-06: a task for the owner (a key, a login) never vanishes on an answer."""

    def task(self):
        return ev(1, "of.agent_question", "ci_keeper", 0, "S", detected_by="declared", to="founder", task=True,
                  steps="1. GitHub → App → Generate a private key\n2. paste the command", command="of secrets put …",
                  questions=[{"question": "Generate a new GitHub App key"}])

    def test_answers_and_replies_claim_never_close(self):
        rows = [self.task(),
                ev(2, "of.human_prompt", "ci_keeper", 1, "S", origin="human", text="ok"),
                ev(3, "of.agent_question_answered", "ci_keeper", 2, "S", ask_id="e01", ask_ids=["e01"], by="owner",
                   answer="сделал"),
                ev(4, "of.agent_question_answered", "ci_keeper", 3, "S", ask_id="e01", ask_ids=["e01"], via="reconcile")]
        a = of_asks.project(rows)[0]
        self.assertEqual((a["state"], a["kind"], a["claim"]), ("open", "task", "сделал"))

    def test_the_asker_closes_or_reopens(self):
        rows = [self.task(),
                ev(2, "of.agent_question_answered", "ci_keeper", 1, "S", ask_id="e01", ask_ids=["e01"], by="owner"),
                ev(3, "of.state_declared", "ci_keeper", 2, "S", state="working", reopen_task="e01",
                   note="the old key still works")]
        a = of_asks.project(rows)[0]
        self.assertEqual((a["state"], a["claimed_at"], a["reopened"]), ("open", None, "the old key still works"))
        rows.append(ev(4, "of.state_declared", "ci_keeper", 3, "S", state="working", done_task="e01"))
        self.assertEqual(of_asks.project(rows)[0]["state"], "done")
        w = [self.task(), ev(2, "of.ask_withdrawn", "ci_keeper", 1, "S", ask_id="e01")]
        self.assertEqual(of_asks.project(w)[0]["state"], "withdrawn")

    def test_a_question_closing_does_not_sweep_the_task(self):
        rows = [self.task(),
                ev(2, "of.agent_question", "ci_keeper", 1, "S", detected_by="heuristic", classifications={"q-v2": True},
                   questions=[{"question": "Merge now?"}]),
                ev(3, "of.human_prompt", "ci_keeper", 2, "S", origin="human", text="да")]
        t, q = of_asks.project(rows)
        self.assertEqual((t["state"], q["state"]), ("open", "answered"))


class AnnotationNeutralTest(unittest.TestCase):
    def test_a_paste_annotation_does_not_move_a_question_on(self):
        rows = [ev(1, "of.agent_question", "a", 0, "S", detected_by="heuristic", classifications={"q-v2": True},
                   questions=[{"question": "Merge now?"}]),
                ev(2, "of.paste_sourced", "a", 1, "S", prompt_id="p", session=None, score=0.0),
                ev(3, "of.human_prompt", "a", 2, "S", origin="human", text="да")]
        a = of_asks.project(rows)[0]
        self.assertEqual((a["state"], a["closed_by"]), ("answered", "e03"))


class FreeTextSeenTest(unittest.TestCase):
    """of_architect, 2026-11-07: the founder's words in a session close no declared ask; they mark them seen. An ask
    closes by the popup's answer (it names the ask), `of asks answered`, or the asker's `of status --answered`."""

    def rows(self, *extra):
        day = 2 * 24 * 60  # 2026-11-07, after FREE_TEXT_SEEN_SINCE
        q = lambda i, m, text: ev(i, "of.agent_question", "of_a", day + m, "S", detected_by="declared", to="founder",
                                  questions=[{"question": text}])
        return [q(1, 0, "adopt acc-v0?"), q(2, 1, "a second decision?"), *[dict(r, ts=iso(day + 2 + n))
                                                                         for n, r in enumerate(extra)]]

    def test_free_text_marks_seen_and_closes_nothing(self):
        talk = ev(3, "of.human_prompt", "of_a", 0, "S", origin="human", text="wait, which agent do I talk to about the tax worker?")
        asks = {a["event_id"]: a for a in of_asks.project(self.rows(talk))}
        self.assertEqual([asks["e01"]["state"], asks["e02"]["state"]], ["open", "open"])
        self.assertEqual(asks["e01"]["seen_by"], "e03")
        alone = [r for r in self.rows(talk) if r["event_id"] != "e02"]  # also when it is the only one open
        self.assertEqual(of_asks.project(alone)[0]["state"], "open")

    def test_the_popup_answer_and_answered_still_close(self):
        popup = ev(3, "of.human_prompt", "of_a", 0, "S", origin="human",
                   text="Answer from the founder to your question 00000e01: yes")
        done = ev(4, "of.agent_question_answered", "of_a", 0, "S", ask_ids=["e02"], via="of asks answered")
        a = {x["event_id"]: x["state"] for x in of_asks.project([dict(r, event_id=r["event_id"].replace("e01", "00000e01"))
                                                                  for r in self.rows(popup, done)])}
        self.assertEqual((a["00000e01"], a["e02"]), ("answered", "answered"))

    def test_before_the_cutoff_history_keeps_its_closures(self):
        old = [ev(1, "of.agent_question", "of_a", 0, "S", detected_by="declared", to="founder", questions=[{"question": "x?"}]),
               ev(2, "of.human_prompt", "of_a", 1, "S", origin="human", text="yes")]
        self.assertEqual(of_asks.project(old)[0]["state"], "answered")


class OpenAsksNoteTest(unittest.TestCase):
    def test_the_hook_names_the_open_asks_and_how_to_close_one(self):
        import sqlite3
        import tempfile
        import of_hook
        with tempfile.TemporaryDirectory() as d:
            db = os.path.join(d, "of.db")
            c = sqlite3.connect(db)
            c.execute("CREATE TABLE view_asks (event_id TEXT PRIMARY KEY, ts TEXT, org TEXT, session_id TEXT, position TEXT, "
                      "kind TEXT, text TEXT, state TEXT, closed_by TEXT, closed_at TEXT, closed_by_kind TEXT, to_ TEXT, "
                      "delegation TEXT, labels TEXT, view_version TEXT)")
            for eid, kind, state, to in (("aaaa04c0bc48", "question (declared)", "open", "founder"),
                                         ("bbbb11111111", "question (declared) blocking", "open", "founder"),
                                         ("cccc22222222", "task", "open", "founder"),
                                         ("dddd33333333", "question (declared)", "answered", "founder"),
                                         ("eeee44444444", "question (declared)", "open", "acme_org")):
                c.execute("INSERT INTO view_asks (event_id, ts, session_id, kind, text, state, to_) VALUES (?,?,?,?,?,?,?)",
                          (eid, eid, "S", kind, f"text of {eid}", state, to))
            c.commit()
            c.close()
            note = of_hook.open_asks_note("S", "wait, which agent?", db=db)
            self.assertIn("04c0bc48", note)
            self.assertIn("11111111", note)
            for gone in ("22222222", "33333333", "44444444"):
                self.assertNotIn(gone, note)
            self.assertIn("of status working --answered", note)
            popup = of_hook.open_asks_note("S", "Answer from the founder to your question 04c0bc48: yes", db=db)
            self.assertNotIn("04c0bc48", popup)
            self.assertIsNone(of_hook.open_asks_note("other", "hi", db=db))
            self.assertIsNone(of_hook.open_asks_note("S", "hi", db=os.path.join(d, "missing.db")))


class RelayedReplyTest(unittest.TestCase):
    """acme_org, 2026-11-07: the pulse nudged unit owners for keepers they had answered: `of send` replies (source
    `of`) never reached the pulse's rows, and `of asks answered` by the owner did not count."""

    def test_an_of_send_reply_from_the_store_silences_the_nudge(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE event (event_id TEXT, ts TEXT, name TEXT, session_id TEXT, position TEXT, payload TEXT, "
                     "source TEXT)")
        now = T0 + 3 * 3600
        conn.execute("INSERT INTO event VALUES (?,?,?,?,?,?,?)", ("m1", iso(170), "of.agent_message_out", "P",
                     "platform_owner", '{"to": "acme:forecasting_keeper", "via": "relay"}', "of"))
        conn.execute("INSERT INTO event VALUES (?,?,?,?,?,?,?)", ("m0", iso(-4 * 24 * 60), "of.agent_message_out", "P",
                     "platform_owner", '{"to": "acme:forecasting_keeper"}', "of"))  # older than the window
        rows = [ev(1, "of.state_declared", "forecasting_keeper", 160, state="waiting", waiting_on="platform_owner")]
        self.assertFalse(of_pulse.replied(rows, "forecasting_keeper", "platform_owner", iso(160)))
        merged = of_pulse.with_messages(conn, rows, now)
        self.assertEqual([r["event_id"] for r in merged], ["e01", "m1"])
        self.assertTrue(of_pulse.replied(merged, "forecasting_keeper", "platform_owner", iso(160)))
        self.assertEqual(len(of_pulse.with_messages(conn, merged, now)), 2)  # no duplicates

    def test_the_peer_answering_the_waiters_ask_counts(self):
        rows = [ev(1, "of.agent_question", "returns_keeper", 0, detected_by="declared", to="storefront_operator",
                   questions=[{"question": "merge #2650?"}]),
                ev(2, "of.state_declared", "returns_keeper", 1, state="waiting", waiting_on="storefront_operator"),
                ev(3, "of.agent_question_answered", "storefront_operator", 5, ask_ids=["e01"], by="storefront_operator")]
        self.assertTrue(of_pulse.replied(rows, "returns_keeper", "storefront_operator", iso(1)))
        other = [rows[0], rows[1], dict(rows[2], payload={"ask_ids": ["e01"], "by": "owner"})]
        self.assertFalse(of_pulse.replied(other, "returns_keeper", "storefront_operator", iso(1)))  # the founder's popup
        self.assertFalse(of_pulse.replied(rows, "returns_keeper", "storefront_operator", iso(6)))  # before this wait



class JudgedQuestionTest(unittest.TestCase):
    """Founder, ask ddd6e3a3 (2026-11-07): a question guessed from an agent's closing message is a candidate shown to
    nobody; the judge (bin/of_judge.py) opens it when the agent waits on the owner, or closes it."""

    def h(self, i, m, text="Кто делает книгу: (а) мы или (б) Вова?", sid="S"):
        return ev(i, "of.agent_question", "mto", m, sid, detected_by="heuristic", questions=[{"question": text}],
                  classifications={"q-v3": True, "q-v4": True})

    def j(self, i, m, ask, verdict, sid="S", **kw):
        return ev(i, "of.agent_question_judged", "mto", m, sid, ask_id=ask, verdict=verdict, judge="j-v1", **kw)

    def test_a_candidate_is_hidden_until_judged(self):
        rows = [ev(1, "of.agent_message_in", "mto", 0, "S", **{"from": "acme:catalogue_keeper"}),
                ev(2, "of.turn_end", "mto", 1, "S"), self.h(3, 1)]  # a peer's turn: a candidate too
        asks = {a["event_id"]: a for a in of_asks.project(rows)}
        self.assertEqual(asks["e03"]["state"], "candidate")
        self.assertEqual(of_asks.group(of_asks.for_owner(asks.values())), [])
        opened = {a["event_id"]: a for a in of_asks.project(rows + [self.j(4, 1, "e03", "owner_question",
                  question="Кто делает книгу ликвидатора: мы или Вова?", options=["мы", "Вова"])])}["e03"]
        self.assertEqual((opened["state"], opened["judged"], opened["text"], opened["n_options"]),
                         ("open", True, "Кто делает книгу ликвидатора: мы или Вова?", 2))
        for verdict in ("not_a_question", "skipped"):
            closed = {a["event_id"]: a for a in of_asks.project(rows + [self.j(4, 1, "e03", verdict)])}["e03"]
            self.assertEqual((closed["state"], closed["closed_by"]), ("not_a_question", "e04"))
        errored = {a["event_id"]: a for a in of_asks.project(rows + [self.j(4, 1, "e03", "error", error="timeout")])}
        self.assertEqual(errored["e03"]["state"], "candidate")  # the pulse may judge it again

    def test_a_verdict_after_the_session_went_on_changes_nothing(self):
        rows = [self.h(1, 0), ev(2, "of.human_prompt", "mto", 1, "S", origin="injected", text="x"),
                ev(3, "of.turn_end", "mto", 2, "S"), self.j(4, 3, "e01", "owner_question", question="q")]
        self.assertEqual({a["event_id"]: a for a in of_asks.project(rows)}["e01"]["state"], "moved_on")

    def test_a_declared_wait_keeps_the_candidate(self):
        rows = [self.h(1, 0), ev(2, "of.state_declared", "mto", 1, "S", state="waiting", waiting_on="founder"),
                ev(3, "of.turn_end", "mto", 2, "S"), self.j(4, 3, "e01", "owner_question", question="q")]
        self.assertEqual({a["event_id"]: a for a in of_asks.project(rows)}["e01"]["state"], "open")

    def test_the_popup_shows_pane_questions_below_without_counting_them(self):
        import of_asks_tui
        import of_herdr_asks
        item = {"kind": "question (heuristic)", "n_options": 0, "since": iso(1), "first": {}}
        declared = {"kind": "question (declared)", "n_options": 2, "since": iso(5), "first": {}}
        self.assertEqual([x["section"] for x in of_asks_tui.order([item, declared])], ["normal", "panes"])
        self.assertTrue(of_herdr_asks.counted_out({"kind": "question (heuristic)"}))
        self.assertFalse(of_herdr_asks.counted_out({"kind": "question (declared)"}))


class JudgeTest(unittest.TestCase):
    """bin/of_judge.py without the model: the reply's parsing, the call's failure, the hook's marker."""

    def test_parse_and_call(self):
        import json
        import subprocess
        import of_judge
        out = json.dumps({"type": "result", "is_error": False, "total_cost_usd": 0.0004, "duration_ms": 2300,
                          "modelUsage": {"claude-haiku-5-5": {}},
                          "structured_output": {"waits_owner": True, "question": "Катить сегодня?",
                                                "options": ["да", "нет"], "reason": "asks"}})
        seen = {}

        def runner(argv, **kw):
            seen.update(argv=argv, env=kw.get("env"))
            return subprocess.CompletedProcess(argv, 0, out, "")
        v = of_judge.call("prompt text", runner=runner, cfg={"model": "haiku"})
        self.assertEqual((v["verdict"], v["question"], v["options"], v["model"]),
                         ("owner_question", "Катить сегодня?", ["да", "нет"], "claude-haiku-5-5"))
        self.assertEqual(seen["argv"][1:3], ["-p", "prompt text"])  # right after -p, before the variadic --tools
        self.assertFalse(any(k.startswith("HERDR_") for k in seen["env"]))  # never re-binds the agent's pane
        self.assertIn('{"disableAllHooks": true}', seen["argv"])
        self.assertFalse(of_judge.env("~/.claude-x")["CLAUDE_CONFIG_DIR"].startswith("~"))

        def broken(argv, **kw):
            return subprocess.CompletedProcess(argv, 1, "not json", "boom")
        self.assertEqual(of_judge.call("p", runner=broken, cfg={"model": "haiku"})["verdict"], "error")

        def slow(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 1)
        self.assertEqual(of_judge.call("p", runner=slow, cfg={"model": "haiku"})["verdict"], "error")

    def test_prompt_names_the_turn_and_the_filed_asks(self):
        import of_judge
        p = of_judge.user_prompt("…жду ответа", "mto", "peer", "acme:acme_org", [("01ab-12345678", "go?")])
        self.assertIn("a message from another agent (acme:acme_org)", p)
        self.assertIn("- 12345678 (also cited as 01ab-123): go?", p)
        self.assertTrue(p.rstrip().endswith("…жду ответа\n>>>"))

    def test_queue_and_take(self):
        import of_judge
        path = of_judge.queue({"ask_id": "01ab-cdef", "session": "S", "text": "x"})
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        got, c = of_judge.take_pending()
        self.assertEqual(c["ask_id"], "01ab-cdef")
        os.unlink(got)
        self.assertEqual(of_judge.take_pending(), (None, None))

    def test_text_at_the_candidates_offset(self):
        """2026-11-10: from_event passed the transcript column ({"path", "offset"}) as a path, so a retried candidate
        was judged on its stored 600-character tail. The Stop hook runs before the closing text reaches the file, so
        the text is the one nearest the offset, on either side, that ends like the stored tail."""
        import json
        import of_judge
        closing = "Отчёт: " + "длинная строка. " * 60 + "Подключать ли Docker Hub с логином?"
        with tempfile.TemporaryDirectory() as d:
            t = os.path.join(d, "t.jsonl")
            with open(t, "w") as fh:
                fh.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "раньше"}]}}) + "\n")
                offset = fh.tell()  # the file's size when the hook ran
                for j in ({"type": "system"},
                          {"type": "assistant", "message": {"content": [{"type": "text", "text": closing}]}},
                          {"type": "assistant", "message": {"content": [{"type": "text", "text": "следующий ход"}]}}):
                    fh.write(json.dumps(j, ensure_ascii=False) + "\n")
            col = json.dumps({"path": t, "offset": offset})
            self.assertEqual(of_judge.text_at(col, closing[-600:]), closing)
            self.assertEqual(of_judge.text_at(col, "другой текст"), "")   # never another turn's text
            self.assertEqual(of_judge.text_at(t, closing[-600:]), "")      # a bare path is not the column
        self.assertEqual(of_judge.text_at('{"path": "/nonexistent", "offset": 5}', "x"), "")


    def test_a_message_naming_only_filed_asks_is_cleared_without_the_model(self):
        # acme_org, 2026-11-09: its WAITING ON line listed open cards, and the model filed it twice as a new card
        import of_judge
        import of_store
        with tempfile.TemporaryDirectory() as d:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = d
            of_events._CONN.clear()
            try:
                conn = of_store.connect(os.path.join(d, "of.db"))

                def add(kind, payload, position="mto"):
                    rec = of_events.record(kind, payload, position=position, session="S", org="mt")
                    of_store.add_event(conn, rec)
                    return rec["of.event.id"]
                gw2 = add("agent_question", {"detected_by": "declared", "to": "founder",
                                             "questions": [{"question": "gw2?"}]}, "po")
                done = add("agent_question", {"detected_by": "declared", "to": "founder",
                                              "questions": [{"question": "1Password?"}]})
                add("agent_question_answered", {"ask_id": done, "ask_ids": [done], "answer": "да", "by": "owner",
                                                "via": "asks-ui"})
                o, a = gw2[-8:], done[-8:]
                self.assertEqual(of_judge.only_filed_ids(conn, f"Твоё «да» на {a}.\nWAITING ON: {o} (gw2)."),
                                 sorted([o, a]))  # a decision cited by its id, and an open card
                self.assertIsNone(of_judge.only_filed_ids(conn, f"WAITING ON: {a}"))  # nothing open for him
                self.assertIsNone(of_judge.only_filed_ids(conn, f"Катить сегодня? Это рядом с {o}"))  # a new question
                self.assertIsNone(of_judge.only_filed_ids(conn, f"WAITING ON: {o}, коммит 7b3aeaa1"))  # not an ask
                self.assertIsNone(of_judge.only_filed_ids(conn, "WAITING ON: founder"))
                # acme_org, 2026-11-10: a card cited by the first 8 characters of its id, as the event store shows it
                self.assertEqual(of_judge.only_filed_ids(conn, f"WAITING ON: карточка `{gw2[:8]}`"), [gw2[:8]])
                self.assertIn(f"- {o} (also cited as {gw2[:8]}): gw2?",
                              of_judge.user_prompt("x", "po", filed=of_judge.filed(conn, "po")))
                cand = add("agent_question", {"detected_by": "heuristic", "questions": [{"question": "x"}]})
                called = []
                v = of_judge.judge_one(conn, {"ask_id": cand, "position": "mto", "ts": of_events.now_iso(),
                                              "text": f"WAITING ON: {o} (gw2)", "origin": "peer"},
                                       runner=lambda *x, **k: called.append(x), log=lambda m: None)
                self.assertEqual((v["verdict"], v["rule"], called), ("not_a_question", "filed-ids", []))
                conn.close()
            finally:
                of_events._CONN.clear()
                of_events.STATE_DIR = saved

    def test_a_position_the_owner_chats_with_is_never_corrected(self):
        # of_conductor, 2026-11-10: the founder's one window; a correction asked it to file what he reads in its pane
        import json
        import subprocess
        import of_judge
        import of_store
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "org", "registry"))
            with open(os.path.join(d, "org", "registry", "positions.yaml"), "w") as fh:
                fh.write("positions:\n"
                         "  - {name: of_conductor, system: of, role: conductor, kind: coordinator, owner: founder,\n"
                         "     owner_chat: true}\n"
                         "  - {name: of_platform, system: of, role: platform, kind: keeper, owner: founder}\n")
            orgs = os.path.join(d, "orgs.yaml")
            with open(orgs, "w") as fh:
                fh.write(f"orgs:\n  - name: of\n    path: {os.path.join(d, 'org')}\n")
            saved, env = of_events.STATE_DIR, os.environ.get("OF_ORGS")
            of_events.STATE_DIR = d
            of_events._CONN.clear()
            os.environ["OF_ORGS"] = orgs
            try:
                self.assertEqual(of_judge.owner_chat(), {"of_conductor"})
                conn = of_store.connect(os.path.join(d, "of.db"))
                text = "Озвучивать каждый ответ агента или только длинные сводки?"
                calls, typed = [], []

                def runner(*a, **k):
                    calls.append(a)
                    return subprocess.CompletedProcess(a, 0, json.dumps({"structured_output": {
                        "waits_owner": True, "question": text, "options": [], "reason": "asks the owner"}}), "")
                for who in ("of_conductor", "of_platform"):
                    rec = of_events.record("agent_question", {"detected_by": "heuristic",
                                                              "questions": [{"question": text}]},
                                           position=who, session=who, org="of")
                    of_store.add_event(conn, rec)
                    v = of_judge.judge_one(conn, {"ask_id": rec["of.event.id"], "position": who, "session": who,
                                                  "ts": of_events.now_iso(), "text": text, "origin": "scheduled",
                                                  "pane": "p1"},
                                           runner=runner, deliver=lambda p, t: typed.append(p) or (True, ""),
                                           log=lambda m: None)
                    if who == "of_conductor":
                        self.assertEqual((v["verdict"], v["rule"], calls, typed), ("skipped", "owner-chat", [], []))
                    else:  # every other position is judged and corrected as before
                        self.assertEqual((v["verdict"], len(calls), typed), ("owner_question", 1, ["p1"]))
                conn.close()
            finally:
                of_events._CONN.clear()
                of_events.STATE_DIR = saved
                if env is None:
                    os.environ.pop("OF_ORGS", None)
                else:
                    os.environ["OF_ORGS"] = env

class PaneCardTest(unittest.TestCase):
    """A pane question the judge confirms is an ask OF files at once, kept until answered (hand-over cc2ed950)."""

    REG = """units:
  platform:
    owner: plat_owner
    systems: [k8s]
delegations:
  - id: del-1
    unit: platform
    from_owner: founder
    to_owner: plat_owner
    reporting:
      escalate_when: [ask.blocking]
positions:
  - {name: k8s_keeper, system: k8s, role: keeper, kind: keeper, owner: founder}
  - {name: plat_owner, system: platform, role: owner, kind: owner, owner: founder, unit: platform}
  - {name: of_capacity, system: of, role: capacity, kind: keeper, owner: founder}
"""

    def setUp(self):
        import json
        import subprocess
        import of_judge
        import of_store
        self.json, self.oj = json, of_judge
        self.d = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.d, "org", "registry"))
        with open(os.path.join(self.d, "org", "registry", "positions.yaml"), "w") as fh:
            fh.write(self.REG)
        orgs = os.path.join(self.d, "orgs.yaml")
        with open(orgs, "w") as fh:
            fh.write(f"orgs:\n  - name: org\n    path: {os.path.join(self.d, 'org')}\n")
        self.saved, self.env = of_events.STATE_DIR, os.environ.get("OF_ORGS")
        of_events.STATE_DIR = self.d
        of_events._CONN.clear()
        os.environ["OF_ORGS"] = orgs
        self.conn = of_store.connect(os.path.join(self.d, "of.db"))
        self.typed = []
        self.q = "Перевести часть агентов с xhigh на high?"

        def runner(*a, **k):
            return subprocess.CompletedProcess(a, 0, json.dumps({"structured_output": {
                "waits_owner": True, "question": self.q, "options": ["да", "нет"], "reason": "a proposal"}}), "")
        self.runner = runner

    def tearDown(self):
        self.conn.close()
        of_events._CONN.clear()
        of_events.STATE_DIR = self.saved
        if self.env is None:
            os.environ.pop("OF_ORGS", None)
        else:
            os.environ["OF_ORGS"] = self.env
        import shutil
        shutil.rmtree(self.d, ignore_errors=True)

    def add(self, kind, payload, position="of_capacity", session="S"):
        import of_store
        tool = kind in ("agent_question", "state_declared")  # written by tools; the rest by the hook
        rec = of_events.record(kind, payload, position=position, session=session, org="org",
                               source="of" if tool else "hook")
        of_store.add_event(self.conn, rec)
        return rec["of.event.id"]

    def judge(self, position="of_capacity", origin="human", session="S"):
        cand = self.add("agent_question", {"detected_by": "heuristic", "questions": [{"question": "…"}]},
                        position, session)
        v = self.oj.judge_one(self.conn, {"ask_id": cand, "position": position, "session": session, "org": "org",
                                             "ts": of_events.now_iso(), "text": "… Скажи, если сделать.",
                                             "origin": origin, "pane": "p1"},
                                 runner=self.runner, deliver=lambda p, t: self.typed.append(t) or (True, ""),
                                 log=lambda m: None)
        return cand, v

    def asks(self):
        return {a["event_id"]: a for a in of_asks.project(of_asks.load(self.conn))}

    def test_filed_at_once_kept_when_the_session_goes_on_and_answered_in_the_pane(self):
        cand, v = self.judge()
        card = v["filed_as"]
        a = self.asks()
        self.assertEqual((a[card]["state"], a[card]["to"], a[card]["source"]), ("open", "founder", "pane"))
        self.assertEqual(a[cand]["state"], "superseded")  # the candidate is replaced by the card
        row = self.conn.execute("SELECT payload FROM event WHERE event_id = ?", (card,)).fetchone()[0]
        self.assertEqual(self.json.loads(row)["questions"][0]["options"], ["да", "нет", self.oj.not_a_question()])
        self.assertEqual(self.typed, [])  # the founder started the turn: the note waits for the pulse
        # the session goes on: a hand-over, a turn, a declared status, a peer's message
        self.add("human_prompt", {"origin": "injected", "text": "Hand-over 2759a172 from of_conductor"})
        self.add("turn_end", {"last_message": "accepted"})
        self.add("state_declared", {"state": "working"})
        self.add("agent_message_in", {"from": "of_platform", "delivery": "pulled"})
        self.assertEqual(self.asks()[card]["state"], "open")
        self.add("human_prompt", {"origin": "human", "text": "да, переводи"})  # his words in that pane
        self.assertEqual(self.asks()[card]["state"], "answered")

    def test_a_peers_turn_is_noted_at_once_and_the_agents_own_ask_replaces_the_card(self):
        cand, v = self.judge(origin="peer")
        card = v["filed_as"]
        self.assertEqual(len(self.typed), 1)
        self.assertIn(f"as ask {card[-8:]}", self.typed[0])
        self.assertIn(f"of asks withdraw {card[-8:]}", self.typed[0])
        own = self.add("agent_question", {"detected_by": "declared", "to": "founder",
                                          "questions": [{"question": "Перевести 30 сессий на high до 12:00?"}]})
        a = self.asks()
        self.assertEqual((a[card]["state"], a[own]["state"]), ("superseded", "open"))

    def test_a_delegated_units_question_goes_to_its_owner(self):
        _, v = self.judge(position="k8s_keeper", origin="peer", session="K")
        self.assertEqual(v["to"], "plat_owner")
        self.assertEqual(self.asks()[v["filed_as"]]["to"], "plat_owner")
        self.assertIn("the list of plat_owner, your unit owner", self.typed[0])

    def test_the_pulse_notes_a_founders_turn_later_once(self):
        _, v = self.judge()
        card = v["filed_as"]
        asks = list(self.asks().values())
        later = time.time() + self.oj.CORRECT_HUMAN_MIN * 60 + 5
        bind = lambda positions, agents: ([({"name": "of_capacity"}, {"agent_status": "idle", "pane_id": "p1"})], [])
        typed = []
        deliver = lambda p, t: typed.append(t) or (True, "")
        _, sent = self.oj.pulse_pass(self.conn, asks, [], [], bind, now=later, deliver=deliver, log=lambda m: None,
                                        start=lambda a: False, online=lambda: True)
        _, again = self.oj.pulse_pass(self.conn, asks, [], [], bind, now=later + 3600, deliver=deliver,
                                         log=lambda m: None, start=lambda a: False, online=lambda: True)
        self.assertEqual((sent, again, len(typed)), (1, 0, 1))
        self.assertIn(card[-8:], typed[0])

    def test_a_message_pulled_mid_turn_does_not_start_the_turn(self):
        self.add("human_prompt", {"origin": "human", "text": "почему никто не перенёс сессию?"})
        self.add("agent_message_in", {"from": "of_platform", "delivery": "pulled"})
        ts = of_events.now_iso()
        self.assertEqual(self.oj.turn_of(self.conn, "S", ts)[:2], ("human", None))
        self.add("agent_message_in", {"from": "of_platform", "delivery": "delivered"})
        self.assertEqual(self.oj.turn_of(self.conn, "S", of_events.now_iso())[:2], ("peer", "of_platform"))


class FounderWaitTest(unittest.TestCase):
    """`of status waiting --on founder` needs a question he can see (founder, ask ddd6e3a3)."""

    def test_needs_an_open_ask(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(os.path.dirname(__file__), "..", "bin", "of"))
        spec = importlib.util.spec_from_loader("of_cli", loader)
        of = importlib.util.module_from_spec(spec)
        loader.exec_module(of)
        me = {"name": "k"}
        mine = [{"event_id": "01-aaaaaaaa", "state": "open", "position": "k", "to": "founder", "kind": "question (declared)"}]
        task = [dict(mine[0], kind="task")]
        other = [{"event_id": "02-bbbbbbbb", "state": "open", "position": "po", "to": "founder", "kind": "question (declared)"}]
        self.assertIs(of._founder_wait_ask(me, asks=mine), True)
        self.assertIs(of._founder_wait_ask(me, asks=task), True)
        self.assertEqual(of._founder_wait_ask(me, "bbbbbbbb", asks=other), "02-bbbbbbbb")
        with self.assertRaises(of.OfError):
            of._founder_wait_ask(me, asks=other)  # someone else's ask, not named
        with self.assertRaises(of.OfError):
            of._founder_wait_ask(me, asks=[dict(mine[0], state="answered")])
        with self.assertRaises(of.OfError):
            of._founder_wait_ask(me, "cccccccc", asks=other)
        with self.assertRaises(of.OfError):  # a candidate (a guessed question) is no ask he can see
            of._founder_wait_ask(me, asks=[dict(mine[0], kind="question (heuristic)", state="candidate")])


class ClipTailTest(unittest.TestCase):
    def test_the_end_is_kept(self):
        import of_hook
        t = "а" * 700 + " Решать тебе."
        self.assertTrue(of_hook.clip_tail(t[-600:], 1024).endswith("Решать тебе."))
        self.assertFalse(of_hook.clip(t[-600:], 1024)[0].endswith("Решать тебе."))



class HerdrSoundTest(unittest.TestCase):
    def test_off_in_herdr_means_none_from_of(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "config.toml")
            with open(cfg, "w") as fh:
                fh.write("[ui]\nstatus_indicators = \"symbols\"\n\n[ui.sound]\nenabled = false\n")
            self.assertEqual(of_pulse.herdr_sound("request", cfg), "none")
            with open(cfg, "w") as fh:
                fh.write("[ui]\nstatus_indicators = \"symbols\"\n")
            self.assertEqual(of_pulse.herdr_sound("done", cfg), "done")
            self.assertEqual(of_pulse.herdr_sound("done", os.path.join(d, "missing.toml")), "done")


class SingleRunTest(unittest.TestCase):
    """2026-11-10: copies of `of model escalations` outlived killed pulses and piled up, one per pulse."""

    def test_one_copy_and_the_whole_group_dies_on_timeout(self):
        import fcntl
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            lock, pidf = os.path.join(d, "x.lock"), os.path.join(d, "pid")
            fd = os.open(lock, os.O_WRONLY | os.O_CREAT, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertIsNone(of_pulse._single_run([sys.executable, "-c", "pass"], lock, 5))  # a copy runs
            os.close(fd)
            self.assertEqual(of_pulse._single_run([sys.executable, "-c", "pass"], lock, 5), 0)
            grandchild = ("import subprocess, sys, time; p = subprocess.Popen([sys.executable, '-c', "
                          "'import time; time.sleep(30)']); open(sys.argv[1], 'w').write(str(p.pid)); time.sleep(30)")
            with self.assertRaises(subprocess.TimeoutExpired):
                of_pulse._single_run([sys.executable, "-c", grandchild, pidf], lock, 1.5)
            time.sleep(0.2)
            pid = int(open(pidf).read())
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            except PermissionError:
                alive = True
            if alive:  # a zombie of an exited group member counts as gone
                import subprocess as sp
                st = sp.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
                alive = bool(st) and not st.startswith("Z")
            self.assertFalse(alive)
