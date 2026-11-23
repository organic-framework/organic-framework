"""Decisions accepted once, in the organization's order (event store step 1; hand-over 2b416487; the contract is
organic-framework-build reports/framework/event-store/step1-accept-contract.md, agreed with of_platform)."""

import json
import os
import sys
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_events  # noqa: E402
import of_server  # noqa: E402
import of_server_decisions as D  # noqa: E402
from tests.test_server import ServerBase  # noqa: E402

ASK = "01a20000-0000-7000-8000-000000000001"
ASK2 = "01a20000-0000-7000-8000-000000000002"


def proposal(stream=f"{ASK}@v1", pid=None, answer="Yes, go", by="owner", org="ofb", **kw):
    p = {"v": 1, "id": pid or str(uuid.uuid4()), "org": org, "kind": "answer", "stream": stream,
         "payload": {"answer": answer, "via": "asks-ui", "by": by, "ask_id": stream.split("@")[0]},
         "proposer": {"position": None, "session": None, "person": "founder"}, "signature": None, "restore": None}
    p.update(kw)
    return p


class DecisionsTest(ServerBase):
    def post(self, body):
        return self.client.decide(body, timeout=30)                        # one try: no backoff

    def test_off_until_the_switch_then_one_answer_per_card_version(self):
        status, out = self.post(proposal())
        self.assertEqual((status, out["type"]), (404, "decisions_off"))     # the client records the answer as before
        status, out = self.post(dict(proposal(), kind="withdraw", extra=1))
        self.assertEqual((status, out["type"]), (404, "decisions_off"))     # off before its form is read: never lost
        rec, turned = D.turn_on(self.app, "ofb")
        self.assertTrue(turned)
        self.assertEqual((rec["event.name"], rec["of.payload"]["first_seq"]), ("of.decision_cutover", 1))
        self.assertFalse(D.turn_on(self.app, "ofb")[1])                     # on already: the same cutover
        key = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"           # a shape the masker knows (tests/test_mask.py)
        first = proposal(answer=f"Yes, and the token is {key}")
        status, out = self.post(first)
        self.assertEqual((status, out["state"]), (200, "accepted"))
        d = out["decision"]
        self.assertEqual(d["of.event.id"], D.decision_id(first["id"]))
        self.assertEqual(d["event.name"], "of.decision_accepted")
        p = d["of.payload"]
        self.assertEqual((p["seq"], p["stream_version"], p["stream"], p["step"], p["signed"], p["restored"]),
                         (1, 1, f"{ASK}@v1", "open→answered", False, False))   # the owner's step, unsigned
        self.assertEqual(p["proposal"], first["id"])
        self.assertNotIn(key, json.dumps(d))                                # masked before it is stored
        self.assertEqual(self.post(first), (200, out))                     # the receipt: the same answer again
        other = proposal(answer="No")
        status, out2 = self.post(other)
        self.assertEqual((status, out2["state"]), (409, "refused"))
        self.assertEqual(out2["accepted"]["of.event.id"], d["of.event.id"])  # the refusal carries the accepted one
        self.assertEqual(self.post(other)[0], 409)                         # refused again, the same answer
        status, out3 = self.post(dict(other, payload=dict(other["payload"], answer="Maybe")))
        self.assertEqual((status, out3["type"]), (409, "conflict"))        # the same id with other content
        self.assertEqual(self.post(proposal(stream=f"{ASK}@v2"))[1]["decision"]["of.payload"]["seq"], 2)  # v2: its own
        self.assertEqual(D.status(self.app.store, "ofb")[0]["refused"], 1)

    def test_the_accepted_record_is_in_the_feed_for_every_machine(self):
        D.turn_on(self.app, "ofb")
        _, out = self.post(proposal())
        feed = self.client.coord("ofb", 0)
        names = [(e["name"], e["host_id"]) for e in feed["events"]]
        self.assertIn(("of.decision_cutover", "of-server"), names)
        self.assertIn(("of.decision_accepted", "of-server"), names)        # the server's: the proposer's pull keeps it
        rec = next(e["record"] for e in feed["events"] if e["name"] == "of.decision_accepted")
        self.assertEqual(rec, out["decision"])

    def test_sequence_per_organization_and_only_a_joined_one(self):
        D.turn_on(self.app, "ofb")
        D.turn_on(self.app, "zeta")
        self.assertEqual(self.post(proposal())[1]["decision"]["of.payload"]["seq"], 1)
        self.assertEqual(self.post(proposal(stream=f"{ASK2}@v1"))[1]["decision"]["of.payload"]["seq"], 2)
        status, out = self.post(proposal(org="zeta"))
        self.assertEqual((status, out["type"]), (403, "forbidden"))        # this machine joined ofb only
        tenant = {"machine_id": "t", "orgs": "[]", "tenant": 1}
        status, out = D.propose(self.app, tenant, proposal(org="zeta"), set(), True)
        self.assertEqual(out["decision"]["of.payload"]["seq"], 1)          # zeta counts from 1 on its own

    def test_bad_proposals_are_refused_before_anything_is_written(self):
        D.turn_on(self.app, "ofb")
        for bad, why in ((dict(proposal(), kind="withdraw"), "only v 1 and kind answer"),
                         (dict(proposal(), stream=ASK), "stream"),
                         (dict(proposal(), signature={"sig": "x"}), "signature"),
                         (dict(proposal(), extra=1), "a proposal is"),
                         (dict(proposal(), payload={"answer": "y", "ask_id": ASK2}), "the stream's ask"),
                         (dict(proposal(), payload={"answer": "y", "by": "Not A Name"}), "payload.by"),
                         (dict(proposal(), payload={"answer": "y", "via": "x" * 80}), "payload.via"),
                         (dict(proposal(), payload={"answer": "y", "seq": 9}), "the server's to set"),
                         (dict(proposal(), payload={"answer": "y", "ask_ids": [ASK]}), "the server's to set"),
                         (dict(proposal(), payload={"answer": "y", "Bad Key": 1}), "lowercase name"),
                         (dict(proposal(), restore={"seq": 0, "stream_version": 1}), "restore")):
            status, out = self.post(bad)
            self.assertEqual(status, 400, why)
            self.assertIn(why, out["title"])
        self.assertEqual(D.status(self.app.store, "ofb")[0]["decisions"], 0)

    def test_the_chats_proof_and_a_status_close_travel_masked(self):
        """of_platform, message 01a2089d-006b: the payload carries the chat's proof that the person answered and a
        status's note; the server keeps every such key, masked, and an asker's own close is a proposal too."""
        D.turn_on(self.app, "ofb")
        key = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
        chat = proposal(payload={"answer": "yes", "via": "conductor", "by": "owner", "ask_id": ASK, "option": 1,
                                 "prompt": "01a20000-0000-7000-8000-0000000000ff",
                                 "quote": f"yes, rotate it, the old one was {key}", "relayed_by": "of_conductor"})
        status, out = self.post(chat)
        p = out["decision"]["of.payload"]
        self.assertEqual((status, p["option"], p["relayed_by"]), (200, 1, "of_conductor"))
        self.assertNotIn(key, json.dumps(out))                              # the proof's text masked too
        mine = proposal(stream=f"{ASK2}@v1", by="k8s_keeper", payload={
            "answer": "done myself", "via": "status", "by": "k8s_keeper", "ask_id": ASK2, "note": "checked the pods"})
        status, out = self.post(mine)
        self.assertEqual((status, out["decision"]["of.payload"]["signed"]), (200, None))   # a position's step

    def test_a_refusal_carries_a_long_accepted_record_whole(self):
        """of_platform, message 01a208aa-8599: an error's body was cut at 1,000 bytes, so a refusal past it lost the
        winner; decide() reads it whole."""
        D.turn_on(self.app, "ofb")
        first = proposal(payload={"answer": "y" * 4000, "via": "relay", "by": "of_conductor", "ask_id": ASK,
                                  "quote": "q" * 3000})
        self.assertEqual(self.post(first)[0], 200)                         # any short via and by pass
        status, out = self.post(proposal(answer="no"))
        self.assertEqual(status, 409)
        self.assertEqual(out["accepted"]["of.payload"]["proposal"], first["id"])
        self.assertEqual(len(out["accepted"]["of.payload"]["answer"]), 4000)

    def test_a_restoration_wins_over_a_later_decision_and_keeps_its_seq(self):
        D.turn_on(self.app, "ofb")
        first = proposal()
        _, out = self.post(first)
        receipt = out["decision"]["of.payload"]
        with self.app.store.tx() as c:                                     # the server's disk lost that decision
            c.execute("DELETE FROM decisions")
            c.execute("DELETE FROM proposals")
            c.execute("UPDATE decision_orgs SET next_seq = 1")
        later = proposal(answer="No")
        _, out2 = self.post(later)
        self.assertEqual(out2["decision"]["of.payload"]["seq"], 1)         # the number is taken again
        status, out3 = self.post(dict(first, restore={"seq": receipt["seq"], "stream_version": 1,
                                                      "accepted_at": receipt["accepted_at"]}))
        self.assertEqual(status, 200)
        p = out3["decision"]["of.payload"]
        self.assertEqual((p["restored"], p["supersedes"], p["seq"]), (True, later["id"], 1))
        with self.app.store.conn() as c:
            self.assertEqual(c.execute("SELECT state FROM decisions WHERE proposal = ?", (later["id"],)).fetchone()[0],
                             "superseded")
            self.assertGreaterEqual(c.execute("SELECT next_seq FROM decision_orgs").fetchone()[0], 2)
        status, out4 = self.post(proposal(answer="Again"))                 # and it holds the stream from then on
        self.assertEqual((status, out4["accepted"]["of.payload"]["proposal"]), (409, first["id"]))

    def test_a_move_holds_it_and_the_commit_is_durable(self):
        D.turn_on(self.app, "ofb")
        of_server.set_moving(self.app.cfg, "ofb", True)
        try:
            self.assertEqual(self.post(proposal())[0], 503)
        finally:
            of_server.set_moving(self.app.cfg, "ofb", False)
        m = {"machine_id": of_events.machine_id(), "orgs": json.dumps(["ofb"]), "tenant": 0}
        D.propose(self.app, m, proposal(), {"ofb"}, True)
        with self.app.store.conn() as c:                                   # this thread's connection did the accept
            self.assertEqual(c.execute("PRAGMA synchronous").fetchone()[0], 2)
            if sys.platform == "darwin":
                self.assertEqual(c.execute("PRAGMA fullfsync").fetchone()[0], 1)
                self.assertEqual(c.execute("PRAGMA checkpoint_fullfsync").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
