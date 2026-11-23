"""Two closures of a routed task (a user organization's card, routed by its asker to its unit owner; of_platform,
message 01a20927-8d45): its unit owner's answer is the claim that the task is done, as the founder's is on a task to
him; and `of asks withdraw` takes the card id, as every command that takes an ask id does."""

import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_asks  # noqa: E402
import of_events  # noqa: E402
import of_store  # noqa: E402


def task(eid, to, ts="2026-11-23T10:00:00Z", ref=None):
    return {"event_id": eid, "ts": ts, "name": "of.agent_question", "session_id": "S1", "position": "acme_sec",
            "org": "acme", "payload": {"detected_by": "declared", "source": "declared", "from_position": "acme_sec",
                                     "to": to, "task": True, "steps": "rotate the key", "topic_key": "rotate-" + eid,
                                     "questions": [{"question": "Rotate the key", "options": []}],
                                     **({"ref": ref} if ref else {})}}


def answer(ask, by, ts="2026-11-23T10:05:00Z", eid="ans-1"):
    return {"event_id": eid, "ts": ts, "name": "of.agent_question_answered", "session_id": None, "position": by,
            "org": "acme", "payload": {"ask_id": ask, "ask_ids": [ask], "by": by, "answer": "rotated"}}


class ClaimTest(unittest.TestCase):
    def test_the_unit_owner_answering_a_task_routed_to_it_claims_it(self):
        a = {x["event_id"]: x for x in of_asks.project([task("t1", "acme_platform"), answer("t1", "acme_platform")])}["t1"]
        self.assertEqual((a["state"], a.get("claim")), ("open", "rotated"))  # claimed; its asker checks the result

    def test_another_position_answering_it_claims_nothing(self):
        a = {x["event_id"]: x for x in of_asks.project([task("t1", "acme_platform"), answer("t1", "acme_other")])}["t1"]
        self.assertEqual((a["state"], a.get("claim")), ("open", None))

    def test_the_founder_still_claims_a_task_to_him(self):
        a = {x["event_id"]: x for x in of_asks.project([task("t1", "founder"), answer("t1", "owner")])}["t1"]
        self.assertEqual(a.get("claim"), "rotated")


class WithdrawByCardIdTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved_dir, self.saved_db = of_events.STATE_DIR, os.environ.get("OF_DB")
        of_events.STATE_DIR = self.tmp.name
        of_events._CONN.clear()
        os.environ["OF_DB"] = os.path.join(self.tmp.name, "of.db")
        self.conn = of_store.connect(os.environ["OF_DB"])
        loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(HERE, "..", "bin", "of"))
        self.of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli", loader))
        loader.exec_module(self.of)
        self.of._asks_actor, self.of._reconcile_ask_tokens, self.of._load_quiet = \
            (lambda: "acme_sec"), (lambda *a: None), (lambda: [])

    def tearDown(self):
        of_events._CONN.clear()
        of_events.STATE_DIR = self.saved_dir
        if self.saved_db is None:
            os.environ.pop("OF_DB", None)
        else:
            os.environ["OF_DB"] = self.saved_db
        self.tmp.cleanup()

    def test_withdraw_takes_the_card_id(self):
        rec = of_events.record("agent_question", task("x", "acme_platform", ref="AS-835")["payload"], position="acme_sec",
                               session="S1", org="acme")
        of_store.add_event(self.conn, rec)
        eid = rec["of.event.id"]
        args = types.SimpleNamespace(action="withdraw", id="as-835", reason=["no longer needed"], watch=False)
        self.assertEqual(self.of.cmd_asks(args), 0)
        (pl,), = self.conn.execute("SELECT payload FROM event WHERE name = 'of.ask_withdrawn'").fetchall()
        self.assertEqual(json.loads(pl)["ask_id"], eid)


if __name__ == "__main__":
    unittest.main()
