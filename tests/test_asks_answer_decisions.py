"""The owner's answer as a decision (event store step 1: organic-framework-build
reports/framework/event-store/04-design.md §2, step1-accept-contract.md; hand-over b18eea97): the popup, the
conductor's chat and the commands propose; only an accepted answer, or one its organization still takes the old way,
is typed into a pane; whoever gave a refused answer is told who answered first; one the server has not answered is
shown as sending and typed once it is accepted."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_asks  # noqa: E402
import of_asks_answer  # noqa: E402
import of_decisions  # noqa: E402
import of_events  # noqa: E402
import of_store  # noqa: E402

POSITIONS = [{"name": "of_a", "_org": "ofb"}, {"name": "of_b", "_org": "ofb"}]
AT = "2026-11-01T10:00:05Z"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = self.tmp.name
        of_events._CONN.clear()
        self.conn = of_store.connect(os.path.join(self.tmp.name, "of.db"))
        for p in (mock.patch.object(of_asks_answer, "not_the_owner", lambda: None),  # the owner's own screen
                  mock.patch.object(of_asks_answer, "ship_soon", lambda: None)):
            p.start()
            self.addCleanup(p.stop)
        self.typed = []
        p = mock.patch.object(of_asks_answer, "deliver",
                              lambda pane, text, session, **k: self.typed.append((pane, text)) or "delivered")
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        of_events._CONN.clear()
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def add(self, kind, payload, ts, session="S1", position="of_a", org="ofb", host=None):
        rec = of_events.record(kind, payload, position=position, session=session, org=org)
        rec["timestamp"] = ts
        if host:
            rec["host.id"] = host
        of_store.add_event(self.conn, rec)
        return rec["of.event.id"]

    def card(self, ts=AT):
        aid = self.add("agent_question", {"detected_by": "declared", "to": "founder",
                                          "questions": [{"question": "Window on Monday 15:00?"}]}, ts)
        g = next(g for g in of_asks.group(of_asks.project(of_asks.load(self.conn))) if aid in g["ask_ids"])
        return aid, {"who": g["who"], "text": g["text"], "since": g["since"], "ids": g["ask_ids"], "kind": g["kind"],
                     "first": g["first"], "act": "00000000-0000-4000-8000-0000000000aa"}

    def server(self, answer):
        """The organization has a server; POST /v1/decisions answers with ANSWER(body) -> (status, body). Set after
        the card is filed: its card id would ask that server for a number."""
        for p in (mock.patch.object(of_events, "_org_server_url", lambda org: "https://127.0.0.1:1"),
                  mock.patch.object(of_decisions, "_default_post", lambda url, body: answer(body))):
            p.start()
            self.addCleanup(p.stop)

    def cut_over(self):
        self.add("decision_cutover", {"org": "ofb"}, "2026-11-01T09:00:00Z", session=None, position=None,
                 host=of_decisions.SERVER_HOST)  # the server's switch: a client's own copy turns nothing on

    def fold_decisions(self):
        """Build of_platform's decisions view as the listener does (of_fold.FoldProcessor); a read folds the tail."""
        import of_fold
        proc = of_fold.FoldProcessor(of_fold.views()["decisions"])
        proc.handle(self.conn, [{"rowid": r, "event_id": e, "ts": ts, "name": n, "org": o, "payload": json.loads(p or "{}")}
                                for r, e, ts, n, o, p in self.conn.execute(
                                    "SELECT rowid, event_id, ts, name, org, payload FROM event ORDER BY rowid")])

    def state(self, aid):
        return {a["event_id"]: a for a in of_asks.project(of_asks.load(self.conn))}[aid]


class LocalAcceptTest(Base):
    """An organization with no server: this machine accepts, once per card and version."""

    def test_the_first_answer_is_typed_and_a_second_one_is_told_who_won(self):
        aid, item = self.card()
        first = of_asks_answer.close_from_command([aid], {"by": "of_owner", "via": "unit", "note": "done in unit"},
                                                  proposer={"position": "of_owner"})
        self.assertEqual(first[0], [aid])
        ok, status = of_asks_answer.answer(self.conn, item, "yes", POSITIONS, lambda name: "pane-" + name)
        self.assertFalse(ok)  # the screen keeps it red with the reason, and archives the draft: the card is closed
        self.assertIn("already answered", status)
        self.assertIn("of_owner", status)
        self.assertIn("your answer was not sent to the asker", status)
        self.assertEqual(self.typed, [])  # a refused answer is never typed
        refused = self.conn.execute("SELECT payload FROM event WHERE name = 'of.decision_refused'").fetchall()
        self.assertEqual(len(refused), 1)  # kept, not lost and not silent

    def test_an_accepted_answer_is_typed_once_and_the_card_closes(self):
        aid, item = self.card()
        ok, status = of_asks_answer.answer(self.conn, item, "yes", POSITIONS, lambda name: "pane-" + name)
        self.assertTrue(ok, status)
        self.assertTrue(status.startswith("answer accepted"), status)
        self.assertEqual([t[0] for t in self.typed], ["pane-of_a"])
        self.assertEqual(self.state(aid)["state"], "answered")

    def test_redelivery_types_an_accepted_answer_the_popup_could_not(self):
        aid, item = self.card()
        ok, _ = of_asks_answer.answer(self.conn, item, "yes", POSITIONS, lambda name: None)  # no live pane
        self.assertTrue(ok)
        now = __import__("time").time() + 600
        todo = of_asks_answer.pending(self.conn, [], POSITIONS, now)
        self.assertEqual([(d["position"], d["sender"]) for d in todo], [("of_a", "owner")])
        self.assertIn("yes", todo[0]["text"])


class ServerTest(Base):
    def test_off_keeps_todays_answer_and_types_it(self):
        aid, item = self.card()
        self.server(lambda body: (404, {"type": "decisions_off"}))
        ok, status = of_asks_answer.answer(self.conn, item, "yes", POSITIONS, lambda name: "pane-" + name)
        self.assertTrue(ok, status)
        self.assertEqual([t[0] for t in self.typed], ["pane-of_a"])
        (pl,), = self.conn.execute("SELECT payload FROM event WHERE name = 'of.agent_question_answered'").fetchall()
        self.assertTrue(json.loads(pl)["decisions_off"])
        self.assertEqual(self.state(aid)["state"], "answered")

    def test_after_the_cutover_an_unsent_answer_is_shown_sending_and_typed_only_once_accepted(self):
        self.cut_over()
        aid, item = self.card()
        self.server(lambda body: (_ for _ in ()).throw(OSError("unreachable")))
        ok, status = of_asks_answer.answer(self.conn, item, "yes", POSITIONS, lambda name: "pane-" + name)
        self.assertTrue(ok, status)
        self.assertIn("sending 1", status)
        self.assertEqual(self.typed, [])  # it may yet be refused
        self.assertEqual(self.state(aid)["state"], "open")
        now = __import__("time").time() + 600
        self.assertEqual(of_asks_answer.pending(self.conn, [], POSITIONS, now), [])
        import of_asks_inbox  # the questions screen marks its row sending, from of_platform's decisions view
        self.fold_decisions()
        self.assertEqual(of_asks_inbox._sending(self.conn, [("Waiting on you", [{"ids": [aid]}])]), {aid})
        # the server takes it later: the background child records the accepted decision
        pid = of_decisions.proposal_id(item["act"], aid)
        (body,), = self.conn.execute("SELECT payload FROM event WHERE event_id = ?", (pid,)).fetchall()
        of_events.append(of_decisions._accepted_record(json.loads(body), 1, 1, "of-server"))
        todo = of_asks_answer.pending(self.conn, [], POSITIONS, now)
        self.assertEqual([d["position"] for d in todo], ["of_a"])
        self.assertEqual(self.state(aid)["state"], "answered")
        self.assertEqual(of_asks_inbox._sending(self.conn, [("Waiting on you", [{"ids": [aid]}])]), set())

    def test_before_the_cutover_an_unsent_answer_counts_as_today(self):
        aid, item = self.card()
        self.server(lambda body: (_ for _ in ()).throw(OSError("unreachable")))
        ok, status = of_asks_answer.answer(self.conn, item, "yes", POSITIONS, lambda name: "pane-" + name)
        self.assertTrue(ok, status)
        self.assertEqual([t[0] for t in self.typed], ["pane-of_a"])
        self.assertEqual(self.state(aid)["state"], "answered")

    def test_a_refusal_from_the_server_names_the_winner(self):
        won = {"proposal": "p-other", "by": "owner", "via": "conductor", "answer": "no, Tuesday",
               "accepted_at": "2026-11-01T10:02:00Z", "seq": 7, "stream_version": 1}
        self.cut_over()
        aid, item = self.card()
        self.server(lambda body: (409, {"type": "refused", "state": "refused", "accepted": won}))
        ok, status = of_asks_answer.answer(self.conn, item, "yes", POSITIONS, lambda name: "pane-" + name)
        self.assertFalse(ok)
        self.assertIn("the founder in the conductor's chat at 10:02Z", status)
        self.assertIn("«no, Tuesday»", status)
        self.assertEqual(self.typed, [])


if __name__ == "__main__":
    unittest.main()
