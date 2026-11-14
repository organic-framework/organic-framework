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
        hs["H1"]["blocked_on"] = "acme:c"   # blocked on a third position: the wait is on it
        self.assertEqual([(r["position"], r["kind"], r["on"]) for r in proj()], [("a", "waiting_peer", "c")])

    def test_find_and_ids(self):
        hs = of_handover.project([PROPOSED])
        self.assertIs(of_handover.find(hs, "H1"), hs["H1"])
        self.assertEqual(of_handover.event_id("H1", "accepted"), of_handover.event_id("H1", "accepted"))
        self.assertNotEqual(of_handover.event_id("H1", "accepted", 0), of_handover.event_id("H1", "accepted", 1))
        self.assertIn("of handover accept", of_handover.offer_text(hs["H1"]))


if __name__ == "__main__":
    unittest.main()


class LoadTest(unittest.TestCase):
    def test_load_reads_by_the_name_index_and_only_hand_overs(self):
        """2026-11-14: LIKE 'of.handover_%' scanned all 421,000 events for 769 (2.5 s a call)."""
        import json
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE event (event_id TEXT, ts TEXT, name TEXT, position TEXT, payload TEXT)")
        conn.execute("CREATE INDEX event_name_ts ON event (name, ts)")
        for i, n in enumerate(("of.handover_proposed", "of.handover_accepted", "of.handoverx", "of.handovers",
                               "of.turn_end", "of.handover_delivered")):
            conn.execute("INSERT INTO event VALUES (?, ?, ?, 'p', ?)", (f"e{i}", f"2026-11-14T00:0{i}:00Z", n,
                                                                       json.dumps({"id": "h"})))
        self.assertEqual([r["name"] for r in of_handover.load(conn)],
                         ["of.handover_proposed", "of.handover_accepted", "of.handover_delivered"])
        traced = []
        conn.set_trace_callback(traced.append)
        of_handover.load(conn)
        plan = conn.execute("EXPLAIN QUERY PLAN " + traced[0]).fetchall()
        self.assertIn("USING INDEX event_name_ts", " ".join(str(r[3]) for r in plan))


class ScopesTest(unittest.TestCase):
    def test_the_listener_keeps_the_scoped_hand_overs_for_the_hook(self):
        """The read path's 3.6: the hook's mandate check read and projected every hand-over event on each tool call."""
        import tempfile
        import of_events
        import of_listen
        import of_store
        with tempfile.TemporaryDirectory() as t:
            saved, of_events.STATE_DIR = of_events.STATE_DIR, t
            of_events._CONN.clear()
            try:
                conn = of_store.connect(os.path.join(t, "of.db"))
                procs = [of_listen.HandoverScopesProcessor()]
                cur = of_listen.prepare(conn, procs)
                of_listen.step(conn, procs, cur)
                self.assertIsNone(of_handover.read_scopes())                   # no listener running here: the store
                with open(os.path.join(t, "listen.ticks"), "w"):
                    pass                                                       # its timer thread turns
                self.assertEqual(of_handover.read_scopes(), {})                 # written at start, empty
                hid = "01a1d6a6-0000-7000-8000-00000000aaaa"
                for kind, pl in (("proposed", {"to": "of_b", "title": "t", "done_when": "d", "basis": "inference",
                                               "scope": {"systems": ["feed"]}}), ("accepted", {})):
                    rec = of_events.record(f"handover_{kind}", dict(pl, id=hid), position="of_a")
                    of_store.add_event(conn, rec)
                of_listen.step(conn, procs, cur)
                scoped = of_handover.read_scopes()
                self.assertEqual(list(scoped), [hid])
                self.assertEqual(of_handover.covering(scoped, "of_b", "feed", None), hid)
                self.assertIsNone(of_handover.covering(scoped, "of_b", "other", None))
                ticks = os.path.join(t, "listen.ticks")
                old = os.path.getmtime(of_handover.scopes_path()) - of_handover.SCOPES_LIVE_S - 60
                os.utime(ticks, (old, old))                                    # the listener stopped
                self.assertIsNone(of_handover.read_scopes())                   # its old copy is not read
                conn.close()
            finally:
                of_events.STATE_DIR = saved
                of_events._CONN.clear()


class SpooledTest(unittest.TestCase):
    def test_a_hand_over_still_in_the_spool_is_found_by_its_short_id(self):
        """Hand-over 6d9a8305, 2026-11-14: proposed while the store was busy, it sat in the spool, and `of handover
        accept 6d9a8305` said no hand-over matched; the full id worked a minute later."""
        import json
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            state = os.path.join(t, "state")
            os.makedirs(os.path.join(state, "spool"))
            os.makedirs(os.path.join(t, "org", "registry"))
            with open(os.path.join(t, "org", "registry", "positions.yaml"), "w") as fh:
                fh.write("positions:\n  - {name: of_a, system: of, role: a, kind: project, owner: founder, "
                         "summary: a, status: active}\n  - {name: of_b, system: of, role: b, kind: project, "
                         "owner: founder, summary: b, status: active}\n")
            with open(os.path.join(t, "orgs.yaml"), "w") as fh:
                fh.write(f"orgs:\n  - name: ofb\n    path: {t}/org\n    workspace: ofb\n")
            env = dict(os.environ, OF_STATE=state, OF_ORGS=os.path.join(t, "orgs.yaml"), OF_CONFIG_DIR=t,
                       PATH="/usr/bin:/bin", OF_POSITION="of_b")
            env.pop("CLAUDE_CODE_SESSION_ID", None)
            hid = "01a1d6a6-755b-764a-8366-260c6d9a8305"
            code = ("import sys, json; sys.path.insert(0, %r); import of_events, of_store; "
                    "rec = of_events.record('handover_proposed', {'id': %r, 'from': 'of_a', 'to': 'of_b', "
                    "'title': 't', 'done_when': 'd', 'basis': 'inference'}, position='of_a'); "
                    "of_store.connect().close(); of_store.spool_append('events.jsonl', rec)"
                    % (os.path.join(HERE, "..", "bin"), hid))
            subprocess.run([sys.executable, "-c", code], env=env, check=True)
            self.assertTrue(os.path.getsize(os.path.join(state, "spool", "events.jsonl")) > 0)
            r = subprocess.run([sys.executable, os.path.join(HERE, "..", "bin", "of"), "handover", "accept",
                                hid[-8:], "--from", "of_b"], env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("6d9a8305: accepted", r.stdout)


class BasisTest(unittest.TestCase):
    """docs/handover.md §1.1 (the founder's incident of 2026-11-11 16:58Z: an unconfirmed inference travelled as a
    must and merged as a "compliance requirement")."""

    def test_an_obligation_needs_a_basis_in_one_of_four_forms(self):
        p = of_handover.basis_problem
        self.assertIn("states an obligation ('compliance')", p(None, "add the compliance log", "a PR merged"))
        self.assertIn("'must'", p("", "rotate keys", "it must run nightly"))
        self.assertIsNone(p(None, "fix x", "tests pass"))  # no obligation stated: no basis needed
        for ok in ("ask:0fd223f2",
                   "ask:01a1cbd3-0260-700c-8f82-cd4b8b78a4fa",  # a full id (finding 6)
                   'source:PCI DSS 4.0 §8.3.6 "passwords at least 12 characters"',
                   "evidence:commit 4e3d84a", "inference"):
            self.assertIsNone(p(ok, "a required change", "done"), ok)
        for bad in ("ask:the founder said so", "source:the contract", "because", "evidence:", "ask:a----b"):
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
        positions = [{"name": "aws_keeper", "_org": "acme"}, {"name": "warehouse_keeper", "_org": "acme"}]
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
            args = argparse.Namespace(action="propose", ref="warehouse_keeper", title="warehouse offboarding",
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
