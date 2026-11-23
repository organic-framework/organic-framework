"""Decisions the organization's server accepts once (event store step 1; bin/of_decisions.py, the asks fold in
bin/of_asks.py, the diff approval in bin/of; organic-framework-build reports/framework/event-store/04-design.md §2 and
step1-accept-contract.md; hand-over d0ef67ce)."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_asks  # noqa: E402
import of_decisions as d  # noqa: E402
import of_events  # noqa: E402
import of_store  # noqa: E402

ASK = "01a20000-0000-7000-8000-00000000a001"
ORG = "acme"


def ask_row(eid=ASK, ts="2026-11-23T10:00:00.000Z", org=ORG, rowid=1):
    return {"rowid": rowid, "event_id": eid, "ts": ts, "name": "of.agent_question", "session_id": "S", "position": "p",
            "org": org, "payload": {"detected_by": "declared", "to": "founder", "questions": [{"question": "go?"}]}}


def answer_row(eid, ts, answer="yes", by="owner", via="asks-ui", org=ORG, rowid=2, **kw):
    return {"rowid": rowid, "event_id": eid, "ts": ts, "name": "of.agent_question_answered", "session_id": None,
            "position": None, "org": org, "payload": dict({"ask_id": ASK, "ask_ids": [ASK], "answer": answer,
                                                           "by": by, "via": via}, **kw)}


def accepted_row(proposal, seq, ts, answer="yes", by="owner", version=1, rowid=3):
    return {"rowid": rowid, "event_id": d.accepted_id(proposal), "ts": ts, "name": "of.decision_accepted",
            "session_id": None, "position": None, "org": ORG,
            "payload": {"proposal": proposal, "org": ORG, "kind": "answer", "stream": d.answer_stream(ASK, version),
                        "stream_version": 1, "seq": seq, "answer": answer, "by": by, "via": "asks-ui"}}


def cutover_row(ts, rowid=0):
    return {"rowid": rowid, "event_id": "cut-" + ts, "ts": ts, "name": "of.decision_cutover", "session_id": None,
            "position": None, "org": ORG, "payload": {"org": ORG, "first_seq": 1}}


def state_of(rows):
    asks = of_asks.project(sorted(rows, key=lambda r: (r["ts"], r["rowid"])))
    return next(a for a in asks if a["event_id"] == ASK)


class StreamTest(unittest.TestCase):
    def test_streams_and_ids(self):
        self.assertEqual(d.answer_stream(ASK, 2), f"{ASK}@v2")
        self.assertEqual(d.parse_stream(f"{ASK}@v2"), (ASK, 2))
        self.assertEqual(d.parse_stream("x@v0"), (None, None))
        self.assertEqual(d.proposal_id("act", ASK), d.proposal_id("act", ASK))
        self.assertNotEqual(d.proposal_id("act", ASK), d.proposal_id("act2", ASK))
        self.assertEqual(d.accepted_id("P"), of_events.stable_id("decision", "P"))


class FoldTest(unittest.TestCase):
    """The race that started this: two machines answering one card; every machine shows the one accepted."""

    def test_only_the_accepted_answer_counts_after_the_cutover_whatever_the_order(self):
        rows = [cutover_row("2026-11-23T09:00:00.000Z"), ask_row(),
                answer_row("plain-A", "2026-11-23T10:01:00.001Z", answer="A"),       # an old client's, after it
                accepted_row("PB", 7, "2026-11-23T10:01:00.002Z", answer="B")]
        for order in (rows, list(reversed(rows))):
            a = of_asks.project(sorted(order, key=lambda r: (r["ts"], r["rowid"])))
            card = next(x for x in a if x["event_id"] == ASK)
            self.assertEqual((card["state"], card["closed_by"]), ("answered", d.accepted_id("PB")))

    def test_before_the_cutover_todays_rule_holds(self):
        card = state_of([ask_row(), answer_row("plain-A", "2026-11-23T10:01:00.000Z"),
                         cutover_row("2026-11-23T11:00:00.000Z")])
        self.assertEqual((card["state"], card["closed_by"]), ("answered", "plain-A"))

    def test_a_decision_before_its_card_is_held_and_applied_when_the_card_comes(self):
        st = of_asks.new_state()
        of_asks.project([cutover_row("2026-11-23T09:00:00.000Z"),
                         accepted_row("PB", 1, "2026-11-23T09:30:00.000Z")], st)
        self.assertIn(ASK, st["held"])
        self.assertEqual(st["seq"], {ORG: 1})
        meta = json.loads(json.dumps(of_asks.fold_meta(st, 5, "x")))        # carried as the listener stores it
        st2 = of_asks.state_from(st["asks"], meta)
        self.assertEqual((st2["cutover"], st2["seq"]), (st["cutover"], st["seq"]))
        asks = of_asks.project([ask_row(ts="2026-11-23T10:00:00.000Z")], st2)
        card = next(x for x in asks if x["event_id"] == ASK)
        self.assertEqual((card["state"], card["closed_by"]), ("answered", d.accepted_id("PB")))
        self.assertEqual(st2["held"], {})

    def test_ofs_own_closures_and_an_answer_the_server_sent_back_as_off_still_count(self):
        """of_asks, message 01a2089e-bc3a: a reconcile, a listener rule's and the pulse's closures are nobody's
        answer; today's answer written on the server's decisions_off counts after a cutover too."""
        card = state_of([cutover_row("2026-11-23T09:00:00.000Z"), ask_row(),
                         answer_row("rec", "2026-11-23T10:02:00.000Z", via="reconcile", reason="orphaned")])
        self.assertEqual(card["state"], "orphaned")
        for via, by in (("rule", "of"), ("pulse", "of_platform")):
            card = state_of([cutover_row("2026-11-23T09:00:00.000Z"), ask_row(),
                             answer_row("own", "2026-11-23T10:02:00.000Z", via=via, by=by)])
            self.assertEqual(card["closed_by"], "own")
        card = state_of([cutover_row("2026-11-23T09:00:00.000Z"), ask_row(),
                         answer_row("off", "2026-11-23T10:02:00.000Z", proposal="P", decisions_off=True)])
        self.assertEqual(card["closed_by"], "off")
        card = state_of([cutover_row("2026-11-23T09:00:00.000Z"), ask_row(),   # written only for want of a server
                         answer_row("down", "2026-11-23T10:02:00.000Z", proposal="P")])
        self.assertEqual(card["state"], "open")


class SendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = self.tmp.name
        of_events._CONN.clear()
        self.env = mock.patch.dict(os.environ, {"OF_DB": os.path.join(self.tmp.name, "of.db")})
        self.env.start()
        self.conn = of_store.connect(os.path.join(self.tmp.name, "of.db"))

    def tearDown(self):
        self.env.stop()
        of_events._CONN.clear()
        of_events.STATE_DIR = self.saved
        self.conn.close()
        self.tmp.cleanup()

    def propose(self, act="act", by="owner", answer="yes"):
        pid = d.proposal_id(act, ASK)
        d.propose(ORG, "answer", d.answer_stream(ASK, 1), {"answer": answer, "via": "asks-ui", "by": by,
                                                          "evidence": {"quote": "OF-1 yes"}}, pid,
                  {"position": None, "session": None, "person": "owner"})
        return pid

    def names(self, pid):
        return {n for (n,) in self.conn.execute(
            "SELECT name FROM event WHERE event_id IN (?, ?, ?, ?)",
            (d.accepted_id(pid), d.refused_id(pid), d.off_id(pid), d.legacy_id(pid)))}

    def test_accepted_refused_and_off_are_recorded_and_sent_once(self):
        a = self.propose("a")
        server = {}

        def post(url, body):
            server.setdefault("calls", []).append(body["id"])
            p = {"proposal": body["id"], "payload": body["payload"], "org": body["org"], "kind": body["kind"],
                 "stream": body["stream"], "proposer": body["proposer"]}
            if "won" not in server:
                server["won"] = d._accepted_record(p, 1, 1, "of-server")
                return 200, {"state": "accepted", "decision": server["won"]}
            return 409, {"type": "refused", "state": "refused", "accepted": server["won"]}
        proposal = json.loads(self.conn.execute("SELECT payload FROM event WHERE event_id = ?", (a,)).fetchone()[0])
        self.assertEqual(d.send_one(self.conn, proposal, post, "https://s"), "accepted")
        self.assertEqual(self.names(a), {"of.decision_accepted"})
        b = self.propose("b", answer="no")
        with mock.patch.object(of_events, "_org_server_url", return_value="https://s"):
            self.assertEqual(d.counts(d.send_pending(self.conn, post)), {"refused": 1})
            self.assertEqual(d.counts(d.send_pending(self.conn, post)), {})               # settled: never sent again
        self.assertEqual(self.names(b), {"of.decision_refused"})
        won = d.accepted(self.conn, ASK, 1)
        self.assertEqual((won["proposal"], won["seq"]), (a, 1))
        c = self.propose("c")
        with mock.patch.object(of_events, "_org_server_url", return_value="https://s"):
            self.assertEqual(d.counts(d.send_pending(self.conn, lambda u, b: (404, {"type": "decisions_off"}))), {"off": 1})
        self.assertEqual(self.names(c), {"of.decision_off", "of.agent_question_answered"})
        legacy = json.loads(self.conn.execute("SELECT payload FROM event WHERE event_id = ?",
                                              (d.legacy_id(c),)).fetchone()[0])
        self.assertEqual(legacy["evidence"], {"quote": "OF-1 yes"})                 # the chat's proof kept
        self.assertTrue(legacy["decisions_off"])
        e = self.propose("e")
        with mock.patch.object(of_events, "_org_server_url", return_value="https://s"):
            got = d.send_pending(self.conn, post, only=[e, a])                    # the popup's own, at once
        self.assertEqual((got[e]["state"], got[e]["record"]["proposal"]), ("refused", a))
        self.assertEqual(got[a]["state"], "accepted")

    def test_unreachable_before_the_cutover_is_todays_answer_and_after_it_only_waits(self):
        a = self.propose("a")
        down = lambda u, b: (0, None)   # noqa: E731
        with mock.patch.object(of_events, "_org_server_url", return_value="https://s"):
            self.assertEqual(d.counts(d.send_pending(self.conn, down)), {"waiting": 1})
            self.assertEqual(self.names(a), {"of.agent_question_answered"})        # as an answer was before
            of_events.emit("decision_cutover", {"org": ORG, "first_seq": 1}, org=ORG)
            b = self.propose("b")
            self.assertEqual(d.counts(d.send_pending(self.conn, down)), {"waiting": 2})
            self.assertEqual(self.names(b), set())                                  # waits for the server

    def test_an_organization_with_no_server_accepts_on_this_machine(self):
        a, b = self.propose("a"), self.propose("b", answer="no")
        with mock.patch.object(of_events, "_org_server_url", return_value=None):
            self.assertEqual(d.counts(d.send_pending(self.conn)), {"accepted": 1, "refused": 1})
        won = d.accepted(self.conn, ASK, 1)
        self.assertEqual((won["proposal"], won["seq"]), (a, 1))
        rec = json.loads(self.conn.execute("SELECT record FROM event WHERE event_id = ?",
                                           (d.accepted_id(a),)).fetchone()[0])
        self.assertEqual(rec["host.id"], d.LOCAL_HOST)
        refusal = json.loads(self.conn.execute("SELECT payload FROM event WHERE event_id = ?",
                                               (d.refused_id(b),)).fetchone()[0])
        self.assertEqual(refusal["accepted"]["proposal"], a)

    def test_the_view_holds_each_proposals_state(self):
        view = d.Decisions()
        evs = [{"name": "of.decision_proposed", "ts": "t1", "payload": {"proposal": "P", "org": ORG,
                                                                         "stream": f"{ASK}@v1",
                                                                         "payload": {"by": "owner"}}},
               {"name": "of.decision_accepted", "ts": "t2", "payload": {"proposal": "P", "org": ORG,
                                                                         "stream": f"{ASK}@v1", "seq": 4}},
               {"name": "of.decision_proposed", "ts": "t3", "payload": {"proposal": "Q", "org": ORG,
                                                                         "stream": f"{ASK}@v1"}},
               {"name": "of.decision_refused", "ts": "t4", "payload": {"proposal": "Q", "org": ORG,
                                                                        "stream": f"{ASK}@v1",
                                                                        "accepted": {"proposal": "P"}}}]
        rows = view.fold({}, evs)
        self.assertEqual((rows["P"]["state"], rows["P"]["seq"]), ("accepted", 4))
        self.assertEqual((rows["Q"]["state"], rows["Q"]["accepted"]), ("refused", "P"))
        late = view.fold(rows, [dict(evs[0], ts="t5")])                           # a proposal again: stays accepted
        self.assertEqual(late["P"]["state"], "accepted")


if __name__ == "__main__":
    unittest.main()
