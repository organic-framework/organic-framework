"""Mandate fit (bin/of_fit.py): the dispatches it reads, the prompt it builds, the verdicts it flags (2026-11-10)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_events  # noqa: E402
import of_fit  # noqa: E402
import of_store  # noqa: E402

POSITIONS = [
    {"name": "acme_throughput-owner", "_orgid": "acme", "_unit": "shop", "summary": "owner of the money units",
     "contribution": "Measures throughput, finds the constraint, ranks and starts the works."},
    {"name": "storefront_search", "_orgid": "acme", "_unit": "shop", "summary": "pending speed, search capture"},
    {"name": "storefront_pricing", "_orgid": "acme", "_unit": "shop", "summary": "New-block bids by model",
     "mandate_exit": {"done_when": "validated bid models"}},
    {"name": "of_lab", "_orgid": "ofb", "summary": "lab"},
]
WORK = ("Price time: what is 1 ms of our lead worth on pending races? Model win probability against our lead "
        "or lag, STOREFRONT per ms per 30 days.")


def frame(sender, text, msg="01a1c5f0-0000-7000-8000-000000000001"):
    return (f"Message from acme:{sender} (via relay, authority: none: no grant; machine m; msg {msg})\n{text}\n"
            f"[reply: of send acme:{sender} --reply-to {msg} \"…\"]")


def verdict(**v):
    return subprocess.CompletedProcess([], 0, stdout=json.dumps(
        {"type": "result", "structured_output": v, "total_cost_usd": 0.002, "modelUsage": {"claude-haiku-5-5": {}}}))


class FitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = self.tmp.name
        of_events._CONN.clear()
        self.conn = of_store.connect(os.path.join(self.tmp.name, "of.db"))

    def tearDown(self):
        self.conn.close()
        of_events._CONN.clear()
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def got(self, receiver, text, ts="2026-11-10T14:21:04.000Z"):
        rec = of_events.record("agent_message_in", {"text": text, "via": "relay"}, position=receiver,
                               session="S-" + receiver, org="acme")
        rec["timestamp"] = ts
        of_store.add_event(self.conn, rec)
        return rec["of.event.id"]

    def test_dispatches_are_the_bodies_positions_sent_each_other(self):
        eid = self.got("storefront_pricing", frame("acme_throughput-owner", WORK))
        self.got("storefront_pricing", "the founder typed this himself")  # no relay frame: not a dispatch
        self.got("storefront_search", "<pasted_content id=\"x\">\n" + frame("storefront_pricing", "rows are in") +
                 "\n</pasted_content id=\"x\">", ts="2026-11-10T14:30:00.000Z")
        ds = of_fit.dispatches(self.conn, "2026-11-10")
        self.assertEqual([(d["sender"], d["receiver"]) for d in ds],
                         [("acme_throughput-owner", "storefront_pricing"), ("storefront_pricing", "storefront_search")])
        self.assertEqual((ds[0]["event_id"], ds[0]["text"]), (eid, WORK))  # no frame, no reply line
        self.assertEqual(ds[1]["text"], "rows are in")
        self.assertEqual(len(of_fit.dispatches(self.conn, "2026-11-10", sender="acme_throughput-owner")), 1)

    def test_the_judge_sees_the_receivers_mandate_and_its_own_organizations_roster(self):
        self.got("storefront_pricing", frame("acme_throughput-owner", WORK))
        d = of_fit.dispatches(self.conn, "2026-11-10")[0]
        seen = []

        def runner(argv, **kw):
            seen.append(argv[argv.index("-p") + 1])
            return verdict(assigns_work=True, verdict="other_position", owner="storefront_search", confidence=0.7,
                           reason="pending speed is search's")
        v = of_fit.judge(d, POSITIONS, runner=runner)
        prompt = seen[0]
        self.assertIn('"name": "storefront_pricing"', prompt)
        self.assertIn("- storefront_search (shop): pending speed, search capture.", prompt)
        self.assertNotIn("of_lab", prompt)  # another organization
        self.assertNotIn("- storefront_pricing", prompt)  # the receiver is not its own alternative
        self.assertTrue(of_fit.flagged(v))
        self.assertEqual((v["owner"], v["cost_usd"]), ("storefront_search", 0.002))

    def test_the_context_is_the_thread_and_the_prs_never_the_receivers_own_turns(self):
        """Founder, 2026-11-10: give the judge more data. acme_org's labels: half the false flags were steps on the
        receiver's own PR or replies in its own thread. The receiver's last turn is left out: a misrouted task is
        exactly what the receiver is doing (mf-v4's recall fell from 0.80 to 0.20 with it)."""
        asked = frame("storefront_pricing", "Ready: PR #2754 needs your yes to queue.", msg="01a1c5aa-0000-7000-8000-00000000abcd")
        rec = of_events.record("agent_message_in", {"text": asked}, position="acme_throughput-owner", session="S-m",
                               org="acme")
        rec["timestamp"] = "2026-11-10T14:00:00.000Z"
        of_store.add_event(self.conn, rec)
        rec = of_events.record("pr_state", {"number": 2754, "title": "feat: joint valuation", "state": "open",
                                            "head_ref": "claude/bidding-joint"}, position=None, session=None,
                               org="acme")
        rec["timestamp"] = "2026-11-10T13:00:00.000Z"
        of_store.add_event(self.conn, rec)
        rec = of_events.record("turn_end", {"last_message": "working on the ms price"}, position="storefront_pricing",
                               session="S-b", org="acme")
        rec["timestamp"] = "2026-11-10T14:10:00.000Z"
        of_store.add_event(self.conn, rec)
        reply = ("Message from acme:acme_throughput-owner (via relay, authority: none: no grant; machine m; msg "
                 "01a1c5ab-0000-7000-8000-000000000001, reply to 01a1c5aa-0000-7000-8000-00000000abcd)\nYes: queue #2754.")
        self.got("storefront_pricing", reply)
        d = of_fit.dispatches(self.conn, "2026-11-10T14:20")[0]
        self.assertTrue(d["reply"])
        self.assertEqual(d["context"]["thread"], "Ready: PR #2754 needs your yes to queue.")
        self.assertEqual(d["context"]["prs"], ["#2754 feat: joint valuation; branch claude/bidding-joint; state open"])
        self.assertNotIn("receiver_last_turn", d["context"])
        prompt = of_fit.user_prompt(d, of_fit.mandate(POSITIONS[2]), [])
        self.assertIn("replies to a message the receiver sent: yes", prompt)
        self.assertIn("The receiver's message this one replies to:", prompt)
        self.assertNotIn("working on the ms price", prompt)

    def test_what_is_flagged(self):
        self.assertFalse(of_fit.flagged({"assigns_work": True, "verdict": "in_mandate", "confidence": 0.9}))
        self.assertFalse(of_fit.flagged({"assigns_work": False, "verdict": "other_position", "confidence": 0.9}))
        self.assertFalse(of_fit.flagged({"assigns_work": True, "verdict": "other_position", "confidence": 0.4}))
        self.assertTrue(of_fit.flagged({"assigns_work": True, "verdict": "no_position", "confidence": 0.8}))

    def test_an_unknown_owner_is_marked_and_a_failed_call_is_an_error(self):
        d = {"sender": "acme_throughput-owner", "receiver": "storefront_pricing", "text": WORK}
        v = of_fit.judge(d, POSITIONS, runner=lambda *a, **k: verdict(
            assigns_work=True, verdict="other_position", owner="storefront_latency", confidence=0.8, reason="r"))
        self.assertTrue(v["owner_unknown"])

        def boom(*a, **k):
            raise subprocess.TimeoutExpired("claude", 1)
        self.assertIn("timed out", of_fit.judge(d, POSITIONS, runner=boom)["error"])
        self.assertEqual(of_fit.judge(dict(d, sender="someone"), POSITIONS)["error"], "not a position pair")


class DelegateTest(unittest.TestCase):
    def test_it_reads_every_mandate_and_keeps_only_known_positions(self):
        seen = []

        def runner(argv, **kw):
            seen.append(argv[argv.index("-p") + 1])
            return verdict(candidates=[{"name": "storefront_search", "fit": "high", "why": "pending speed"},
                                       {"name": "storefront_latency", "fit": "partial", "why": "made up"}],
                           none_fits=False, suggestion="", overlap="")
        v = of_fit.delegate(WORK, POSITIONS, "acme", asker="acme_throughput-owner", runner=runner)
        self.assertEqual([c["name"] for c in v["candidates"]], ["storefront_search"])  # an invented name is dropped
        self.assertIn("Asked by: acme_throughput-owner", seen[0])
        self.assertIn("- storefront_pricing (shop): New-block bids by model.", seen[0])
        self.assertNotIn("of_lab", seen[0])
        self.assertEqual(v["cost_usd"], 0.002)

    def test_a_failed_call_is_an_error(self):
        def boom(*a, **k):
            raise subprocess.TimeoutExpired("claude", 1)
        self.assertIn("TimeoutExpired", of_fit.delegate(WORK, POSITIONS, "acme", runner=boom)["error"])


if __name__ == "__main__":
    unittest.main()
