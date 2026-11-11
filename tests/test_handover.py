"""Hand-overs (of_handover): projection, who may write what, waits, response windows, scope."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_handover  # noqa: E402


def ev(kind, ts, **p):
    return {"event_id": f"{kind}{ts}", "ts": ts, "name": f"of.handover_{kind}", "position": None, "payload": p}


PROPOSED = ev("proposed", "2026-11-06T10:00:00Z", id="H1", **{"from": "a"}, to="b", title="fix x",
              done_when="tests pass", priority="blocking", scope={"systems": ["bot"], "paths": ["storefront/ops/**"]})


class HandoverTest(unittest.TestCase):
    def test_lifecycle_and_rejection_loop(self):
        rows = [PROPOSED, ev("offered", "2026-11-06T10:01:00Z", id="H1"),
                ev("accepted", "2026-11-06T10:05:00Z", id="H1", by="b"),
                ev("delivered", "2026-11-06T11:00:00Z", id="H1", by="b", result="PR#1"),
                ev("rejected", "2026-11-06T11:10:00Z", id="H1", by="a", note="flaky"),
                ev("closed", "2026-11-06T11:11:00Z", id="H1", by="a"),  # invalid now: ignored
                ev("delivered", "2026-11-06T12:00:00Z", id="H1", by="b", result="PR#2"),
                ev("verified", "2026-11-06T12:10:00Z", id="H1", by="a"),
                ev("closed", "2026-11-06T12:11:00Z", id="H1", by="a")]
        h = of_handover.project(rows)["H1"]
        self.assertEqual(h["state"], "closed")
        self.assertEqual(h["rejections"], 1)
        self.assertEqual(h["result"], "PR#2")
        self.assertEqual(h["offered_at"], "2026-11-06T10:01:00Z")
        self.assertEqual([k for _, k in h["ignored"]], ["closed"])

    def test_who_may_write(self):
        h = of_handover.project([PROPOSED])["H1"]
        self.assertIsNone(of_handover.check(h, "accepted", "b"))
        self.assertIn("only b", of_handover.check(h, "accepted", "a"))
        self.assertIsNone(of_handover.check(h, "withdrawn", "a"))
        self.assertIn("cannot be delivered", of_handover.check(h, "delivered", "b"))
        h["state"] = "delivered"
        h["verify"] = "c"
        self.assertIn("only c", of_handover.check(h, "verified", "a"))
        self.assertIsNone(of_handover.check(h, "verified", "c"))

    def test_a_receiver_stored_with_its_organization_may_accept(self):
        """acme_org, 2026-11-07: f8de73ae was addressed to acme:returns_keeper; the receiver could not accept."""
        h = of_handover.project([dict(PROPOSED, payload=dict(PROPOSED["payload"], to="acme:b"))])["H1"]
        self.assertIsNone(of_handover.check(h, "accepted", "b", "acme"))
        self.assertIn("only acme:b", of_handover.check(h, "accepted", "b", "other-org"))
        self.assertIn("only acme:b", of_handover.check(h, "accepted", "b"))
        self.assertIn("only acme:b", of_handover.check(h, "accepted", "a", "acme"))

    def test_blocking_waits_and_scope(self):
        hs = of_handover.project([PROPOSED])
        self.assertEqual([(r, t) for r, t, _ in of_handover.waits(hs)], [("a", "b")])
        self.assertIsNone(of_handover.covering(hs, "b", "bot", None))  # not accepted yet
        hs = of_handover.project([PROPOSED, ev("accepted", "2026-11-06T10:05:00Z", id="H1", by="b")])
        self.assertEqual(of_handover.covering(hs, "b", "bot", None), "H1")
        self.assertEqual(of_handover.covering(hs, "b", "other", "/r/storefront/ops/x.sh"), "H1")
        self.assertIsNone(of_handover.covering(hs, "b", "other", "/r/storefront/src/x.rs"))
        self.assertIsNone(of_handover.covering(hs, "c", "bot", None))

    def test_response_windows(self):
        t0 = of_handover._ts("2026-11-06T10:00:00Z")
        hs = of_handover.project([PROPOSED])
        act = lambda now, idle=True: [a for a, _ in of_handover.due_actions(hs, now, lambda n: 30, lambda n: idle)]
        self.assertEqual(act(t0 + 60), ["offer"])
        self.assertEqual(act(t0 + 60, idle=False), [])
        hs["H1"]["offered_at"] = "2026-11-06T10:00:00Z"
        self.assertEqual(act(t0 + 10 * 60), [])
        self.assertEqual(act(t0 + 31 * 60), ["nudge"])
        self.assertEqual(act(t0 + 121 * 60), ["expire"])
        hs["H1"]["due"] = "2026-11-05"
        self.assertEqual(act(t0 + 60), ["expire"])

    def test_pulse_counts_blocking_handover_as_waiting_peer(self):
        import of_pulse
        hs = of_handover.project([PROPOSED])
        out = of_pulse.project([{"name": "a"}, {"name": "b"}], [], [], [], {}, now=0,
                               bind=lambda p, a: ([], [], []), handovers=hs)
        self.assertEqual([(r["position"], r["kind"], r["on"]) for r in out], [("a", "waiting_peer", "b")])

    def test_a_handover_blocked_on_the_founder_is_his_wait_not_a_nudge_to_the_receiver(self):
        """acme_org, 2026-11-06: aws_keeper got four nudges for e27e03f9 while it was blocked on the founder."""
        import of_pulse
        hs = of_handover.project([PROPOSED])
        hs["H1"].update(state="blocked", blocked_on="founder")
        proj = lambda: of_pulse.project([{"name": "a"}, {"name": "b"}, {"name": "c"}], [], [], [], {}, now=0,
                                        bind=lambda p, a: ([], [], []), handovers=hs)
        self.assertEqual([(r["position"], r["kind"], r["on"]) for r in proj()], [("a", "waiting_owner", "founder")])
        hs["H1"]["blocked_on"] = "mt:c"   # blocked on a third position: the wait is on it
        self.assertEqual([(r["position"], r["kind"], r["on"]) for r in proj()], [("a", "waiting_peer", "c")])

    def test_find_and_ids(self):
        hs = of_handover.project([PROPOSED])
        self.assertIs(of_handover.find(hs, "H1"), hs["H1"])
        self.assertEqual(of_handover.event_id("H1", "accepted"), of_handover.event_id("H1", "accepted"))
        self.assertNotEqual(of_handover.event_id("H1", "accepted", 0), of_handover.event_id("H1", "accepted", 1))
        self.assertIn("of handover accept", of_handover.offer_text(hs["H1"]))


if __name__ == "__main__":
    unittest.main()


class BasisTest(unittest.TestCase):
    """docs/handover.md §1.1 (the founder's incident of 2026-11-11 16:58Z: an unconfirmed inference travelled as a
    must and merged as a "compliance requirement")."""

    def test_an_obligation_needs_a_basis_in_one_of_four_forms(self):
        p = of_handover.basis_problem
        self.assertIn("states an obligation ('compliance')", p(None, "add the compliance log", "a PR merged"))
        self.assertIn("'must'", p("", "rotate keys", "it must run nightly"))
        self.assertIsNone(p(None, "fix x", "tests pass"))  # no obligation stated: no basis needed
        for ok in ("ask:0fd223f2", 'source:PCI DSS 4.0 §8.3.6 "passwords at least 12 characters"',
                   "evidence:commit 4e3d84a", "inference"):
            self.assertIsNone(p(ok, "a required change", "done"), ok)
        for bad in ("ask:the founder said so", "source:the contract", "because", "evidence:"):
            self.assertIn("is none of the forms", p(bad, "a required change", "done"), bad)

    def test_the_offer_shows_the_basis_before_accepting(self):
        h = of_handover.project([PROPOSED])["H1"]
        self.assertIn("Basis: none given", of_handover.offer_text(h))
        rows = [ev("proposed", "2026-11-06T10:00:00Z", id="H2", **{"from": "a"}, to="b", title="a required log",
                   done_when="merged", basis="inference")]
        h = of_handover.project(rows)["H2"]
        self.assertEqual(h["basis"], "inference")
        self.assertIn("Basis: inference (unconfirmed: the requester's own reasoning)", of_handover.offer_text(h))


class NoteTest(unittest.TestCase):
    """A user organization, 2026-11-11: a correction ("precaution, unconfirmed") to an accepted hand-over could only
    travel as a message, not onto the hand-over's own record."""

    def test_a_note_by_either_party_stays_on_the_record_and_changes_no_state(self):
        rows = [PROPOSED, ev("accepted", "2026-11-06T10:05:00Z", id="H1", by="b"),
                ev("noted", "2026-11-06T10:20:00Z", id="H1", by="a", note="precaution, unconfirmed"),
                ev("noted", "2026-11-06T10:21:00Z", id="H1", by="x", note="not a party")]
        h = of_handover.project(rows)["H1"]
        self.assertEqual(h["state"], "accepted")
        self.assertEqual([(n["by"], n["note"]) for n in h["notes"]], [("a", "precaution, unconfirmed")])
        self.assertEqual([k for _, k in h["ignored"]], ["noted"])
        self.assertIn("Note from a (2026-11-06T10:20Z): precaution, unconfirmed", of_handover.offer_text(h))
        self.assertIsNone(of_handover.check(h, "noted", "a"))
        self.assertIsNone(of_handover.check(h, "noted", "b"))
        self.assertIn("requester", of_handover.check(h, "noted", "x"))
        closed = of_handover.project(rows + [ev("withdrawn", "2026-11-06T11:00:00Z", id="H1", by="a")])["H1"]
        self.assertIn("a note goes on an open one", of_handover.check(closed, "noted", "a") or "")


class ProposeSpooledTest(unittest.TestCase):
    """acme_org, 2026-11-06: of handover propose crashed (KeyError) when the store was busy and the event spooled."""

    def test_a_spooled_proposal_is_offered_not_a_crash(self):
        import importlib.machinery
        import importlib.util
        import io
        import argparse
        import contextlib
        loader = importlib.machinery.SourceFileLoader("of_cli_ho", os.path.join(HERE, "..", "bin", "of"))
        spec = importlib.util.spec_from_loader("of_cli_ho", loader)
        of = importlib.util.module_from_spec(spec)
        loader.exec_module(of)
        positions = [{"name": "aws_keeper", "_org": "mt"}, {"name": "warehouse_keeper", "_org": "mt"}]
        saved = (of._load_quiet, of._me, of_handover.load, of._handover_offer, of.of_hook_mask)
        appended = []
        of._load_quiet = lambda *a, **k: positions
        of._me = lambda ps, sender=None: positions[0]
        of_handover.load = lambda conn: []  # the store does not have it yet: spooled
        of._handover_offer = lambda h, ps, wait_s=2: False  # no live holder: the pulse offers it later
        of.of_hook_mask = lambda t: t
        import of_events
        saved_append = of_events.append
        of_events.append = lambda rec, raw_ref=None: (appended.append(rec), True)[1]
        try:
            args = argparse.Namespace(action="propose", ref="warehouse_keeper", title="Snowflake offboarding",
                                      done_when="#6 merged", verify=None, systems=None, paths=None, due=None,
                                      blocking=False, mandate=None, context=None, sender="aws_keeper", id=None)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                of.cmd_handover(args)
        finally:
            of._load_quiet, of._me, of_handover.load, of._handover_offer, of.of_hook_mask = saved
            of_events.append = saved_append
        self.assertEqual(len(appended), 1)
        self.assertIn("spooled", out.getvalue())
        self.assertIn("to warehouse_keeper", out.getvalue())



class ResolveTest(unittest.TestCase):
    def test_own_organization_address_is_the_bare_name(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(HERE, "..", "bin", "of"))
        spec = importlib.util.spec_from_loader("of_cli", loader)
        of = importlib.util.module_from_spec(spec)
        loader.exec_module(of)
        positions = [{"name": "returns_keeper", "_org": "acme", "_orgid": "Acme"},
                     {"name": "acme_throughput-owner", "_org": "acme", "_orgid": "Acme"}]
        me = positions[1]
        self.assertEqual(of._resolve_position("acme:returns_keeper", positions, me), "returns_keeper")
        self.assertEqual(of._resolve_position("organic-framework-build:of_platform", positions, me),
                         "organic-framework-build:of_platform")  # another organization stays qualified
        with self.assertRaises(of.OfError):
            of._resolve_position("acme:nobody", positions, me)
