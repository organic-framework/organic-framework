"""RFC 0007 step 2: governance facts need the owner's ask id; one decider per cell; known systems and kinds."""

import importlib.machinery
import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_loader = importlib.machinery.SourceFileLoader("of_cli_cells", os.path.join(HERE, "..", "bin", "of"))
of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli_cells", _loader))
_loader.exec_module(of)

DOC = {"methods": {"pricing": "the price of a quote"},
       "systems": {
           "storefront": {"keeper": "op", "confirmed": {"keeper": "1a2b3c4d"}, "needs": ["build", "operate", "pricing"]},
           "storefront.search": {"parent": "storefront", "needs": ["build", "telepathy"]},
           "marketplace-bots": {"governed_by": "external:dana"},     # the retold "(founder, 2026-11-06)" case
           "delivery": {"keeper": "ops"},
           "old": {"lifecycle": "retired", "confirmed": {"lifecycle": "not-an-id"}}}}
POS = [{"name": "search", "owns": [{"system": "storefront.search", "method": "build", "decides": True},
                                   {"system": "storefront.search", "method": "verify"}]},
       {"name": "bidding", "owns": [{"system": "storefront.search", "method": "build", "decides": True},
                                    {"system": "storefront.bidder", "method": "model"},
                                    {"system": "storefront", "method": "vibes"}]}]


class CellsTest(unittest.TestCase):
    def test_every_rule(self):
        out = of.check_cells(DOC, POS)
        text = "\n".join(out)
        self.assertTrue(all(x.startswith(of.WARN) for x in out), out)  # warnings: today's registries keep passing
        self.assertNotIn("storefront: keeper", text)  # confirmed by an ask id
        self.assertIn("marketplace-bots: governed_by 'external:dana' has no owner's ask id", text)
        self.assertIn("delivery: keeper 'ops' has no owner's ask id", text)
        self.assertIn("old: confirmed.lifecycle 'not-an-id' is not an ask id", text)
        self.assertIn("storefront.search: needs storefront.search x telepathy: unknown kind of work in needs", text)
        self.assertNotIn("'pricing'", text)  # the organization's own kind
        self.assertIn("cells: storefront.search x build has 2 deciders (bidding, search)", text)
        self.assertIn("bidding: owns storefront.bidder x model: unknown system", text)
        self.assertIn("bidding: owns storefront x vibes: unknown kind of work", text)

    def test_a_subsystem_id_is_its_parent_dot_a_name(self):
        ok = of.check_systems({"storefront": {}, "storefront.search": {"parent": "storefront"}}, set())
        self.assertEqual(ok, [])
        bad = of.check_systems({"storefront": {}, "storefront.search": {}, "x.y": {"parent": "storefront"}}, set())
        self.assertEqual(len([b for b in bad if "invalid system id" in b]), 2)

    def test_personal_data_names_whose_and_where(self):
        ok = {"crm": {"personal_data": {"whose": "customers, staff", "where": "PostgreSQL; Hetzner, Germany",
                                        "evidence": ["record of processing, row 3"]}},
              "web": {"personal_data": {"whose": "visitors (not checked)", "where": "Cloudflare, EU",
                                        "evidence": "row 4"}},
              "bot": {}}
        self.assertEqual(of.check_systems(ok, set()), [])
        bad = of.check_systems({"a": {"personal_data": "yes"},
                                "b": {"personal_data": {"whose": " ", "evidence": [1]}}}, set())
        self.assertIn("a: personal_data must be a mapping with whose, where and evidence", bad)
        self.assertIn("b: personal_data.where must be a non-empty string", bad)
        self.assertIn("b: personal_data.whose must be a non-empty string", bad)
        self.assertIn("b: personal_data.evidence must be a string or a list of strings", bad)
        self.assertEqual(len(bad), 4)

    def test_nothing_to_say_on_a_registry_without_the_new_fields(self):
        self.assertEqual(of.check_cells({"systems": {"x": {"governed_by": "self"}}}, [{"name": "a"}]), [])


class PeopleTest(unittest.TestCase):
    def test_people_and_their_positions(self):
        doc = {"people": {"alex": {"name": "Alex", "machines": ["m-0a1b2c3d"], "status": "active"},
                          "bob": {"name": "Bob", "status": "left"},
                          "Bad.Id": {"name": "x"}, "nameless": {"status": "away", "machines": "m-1"}}}
        positions = [{"name": "research_keeper", "holder": {"runtime": "human", "person": "alex"}},
                     {"name": "ops_keeper", "status": "active", "holder": {"runtime": "human", "person": "bob"}},
                     {"name": "old_keeper", "status": "finished", "holder": {"runtime": "human", "person": "bob"}},
                     {"name": "x_keeper", "holder": {"runtime": "human"}},
                     {"name": "y_keeper", "holder": {"runtime": "human", "person": "carol"}},
                     {"name": "agent_a", "holder": {"runtime": "claude-code", "session": "s"}}]
        text = "\n".join(of.check_people(doc, positions))
        self.assertIn("people: Bad.Id: invalid id", text)
        self.assertIn("people: nameless: name is required", text)
        self.assertIn("people: nameless: status 'away' is not one of active, left", text)
        self.assertIn("people: nameless: machines must be a list", text)
        self.assertIn("ops_keeper: held by bob, who left", text)
        self.assertNotIn("old_keeper", text)  # finished: nothing to hand over
        self.assertIn("x_keeper: a human holder names its person", text)
        self.assertIn("y_keeper: holder.person 'carol' is not in people:", text)
        self.assertNotIn("research_keeper", text)
        self.assertNotIn("agent_a", text)
        self.assertEqual(of.check_people({}, [positions[-1]]), [])
        legacy = of.check_people({}, [{"name": "acme_owner", "holder": {"runtime": "human", "person": "founder (Alex P)"}}])
        self.assertTrue(legacy and legacy[0].startswith(of.WARN))  # no people: yet: a warning, the check passes


class EvidenceTest(unittest.TestCase):
    """docs/registry.md, People: a person-held claim carries evidence: beside it (the founder, 2026-11-11: a
    paraphrase put a colleague on systems he does not keep)."""

    def test_person_held_claims_without_evidence_warn(self):
        positions = [
            {"name": "shop_keeper", "holder": {"person": "dana"},
             "owns": [{"system": "shop", "method": "build", "decides": True},
                      {"system": "pricing", "method": "run", "decides": True, "evidence": "ask a76abee5"}]},
            {"name": "ops_keeper", "holder": {"person": "lee", "evidence": "git authors 2026-10-01..11-10: 41 of 47"}},
            {"name": "agent_keeper", "holder": {"session": "s"}, "owns": [{"system": "ci", "method": "run"}]}]
        doc = {"systems": {"shop": {"keeper": "shop_keeper"}, "ops": {"keeper": "ops_keeper"},
                           "ci": {"keeper": "agent_keeper"}, "bank": {"governed_by": "external:bank-team"},
                           "pay": {"governed_by": "external:pay", "evidence": "ask 1234abcd"}},
               "works": {"w1": {"doers": ["external:dana"]}, "w2": {"doers": ["agent_keeper"]}}}
        out = of.check_evidence(doc, positions)
        self.assertTrue(all(x.startswith(of.WARN) for x in out), out)  # warnings: today's registries keep passing
        text = "\n".join(out)
        for expected in ("shop_keeper: holder.person 'dana'", "owns shop/build", "systems: shop: keeper shop_keeper",
                         "systems: bank: governed_by external:bank-team", "works: w1: doers external:dana",
                         "systems: ops: keeper ops_keeper"):  # holding the position is not keeping the system
            self.assertIn(expected, text)
        for absent in ("ops_keeper: holder", "pricing", "systems: ci", "systems: pay", "w2"):  # evidence, or no person
            self.assertNotIn(absent, text)
        self.assertEqual(len(out), 6)


class ProvisionalTest(unittest.TestCase):
    """docs/registry.md, Fields, provisional (of_architect 728ff7a): work that came up in an onboarding starts on a
    position with no powers, on the person's own typed yes."""

    PV = {"asked": "can someone look at the invoices", "at": "2026-11-11T18:05:00Z", "by": "acme_conductor",
          "event": "01a1cbd3-0000-7000-8000-000000000001"}

    def test_complete_and_powerless_or_a_problem(self):
        ok = {"name": "acme_invoices", "kind": "project", "provisional": self.PV}
        self.assertEqual(of.check_provisional({"organization": "acme"}, [ok]), [])
        bad = {"name": "acme_x", "kind": "keeper", "provisional": {"asked": "x"}, "credentials": ["c"],
               "tools": ["slack", "-superpowers"]}
        doc = {"organization": "acme", "units": {"u": {"owner": "acme_x"}},
               "delegations": [{"id": "d", "unit": "u", "to_owner": "acme_x"}]}
        text = "\n".join(of.check_provisional(doc, [bad], [{"subject": "acme_x"}]))
        for expected in ("needs at, by, event", "only a position of kind project",
                         "holds credentials, tools beyond the defaults, a grant, a delegation, a unit"):
            self.assertIn(expected, text)
        self.assertFalse(any(x.startswith(of.WARN) for x in of.check_provisional(doc, [bad])))  # problems, not warnings

    def test_still_provisional_after_the_run_warns(self):
        p = {"name": "acme_invoices", "kind": "project", "provisional": self.PV}
        done = {"organization": "acme", "works": {"acme-onboarding": {"state": "done"}}}
        out = of.check_provisional(done, [p])
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith(of.WARN))
        self.assertEqual(of.check_provisional({"organization": "acme", "works": {"acme-onboarding": {}}}, [p]), [])


class RemoteApprovalTest(unittest.TestCase):
    """docs/remote.md §3 row 2: a diff approved from the conductor's chat on the person's recorded words."""

    def test_the_persons_yes_in_the_conductors_session_up_to_medium(self):
        import json
        import tempfile
        import of_events
        import of_store
        with tempfile.TemporaryDirectory() as d:
            saved, of_events.STATE_DIR = of_events.STATE_DIR, d
            try:
                conn = of_store.connect()
                pos = [{"name": "acme_conductor", "role": "conductor", "holder": {"session": "S-c"}, "_org": "acme",
                        "owner": "acme_owner"},
                       {"name": "acme_owner", "role": "owner", "_org": "acme"},
                       {"name": "acme_dev", "role": "dev", "holder": {"session": "S-d"}, "_org": "acme"},
                       {"name": "of_conductor", "role": "conductor", "holder": {"session": "S-o"}, "_org": "ofb",
                        "owner": "founder"},
                       {"name": "acme_helper", "role": "conductor", "holder": {"session": "S-h"}, "_org": "acme",
                        "owner": "acme_dev"}]
                e = {"diff": {"id": "sd-1", "tier": "medium", "org": "acme"}, "proposed_at": "2026-11-11T10:00:00Z",
                     "digest": "a1b2c3d4e5f60718"}

                def said(text, session="S-c", origin="human", ts="2026-11-11T11:00:00.000Z"):
                    rec = of_events.record("human_prompt", {"origin": origin, "text": text}, session=session)
                    rec["timestamp"] = ts
                    of_store.add_event(conn, rec)
                    return rec["of.event.id"]
                ok = said("yes, approve sd-1")
                self.assertIsNone(of._remote_approval(conn, e, ok, pos, session="S-c", hold=False)[0])
                cases = {
                    said("yes but first check sd-1"): "not a plain yes",
                    said("yes"): "do not name the diff",
                    said("approve sd-1", ts="2026-11-11T09:00:00.000Z"): "older than the proposal",
                    said("approve sd-1", origin="relay"): "not typed by a person",
                    said("approve sd-1", session="S-d"): "the person's conductor",
                }
                for pid, why in cases.items():
                    got = of._remote_approval(conn, e, pid, pos, session="S-d" if why == "the person's conductor"
                                              else "S-c", hold=False)[0]
                    self.assertIn(why, got or "", pid)
                # of_server's review: origin exactly human; the diff's own organization; its owner's conductor;
                # a digest the person names must be this diff's
                self.assertIn("not typed by a person",
                              of._remote_approval(conn, e, said("approve sd-1", origin=None), pos, session="S-c", hold=False)[0])
                self.assertIn("is acme's", of._remote_approval(conn, e, said("approve sd-1", session="S-o"), pos,
                                                              session="S-o", hold=False)[0])
                self.assertIn("approved by acme_owner", of._remote_approval(conn, e, said("approve sd-1", session="S-h"),
                                                                           pos, session="S-h", hold=False)[0])
                self.assertIsNone(of._remote_approval(conn, e, said("yes sd-1 a1b2c3d4"), pos, session="S-c", hold=False)[0])
                self.assertIn("name a digest", of._remote_approval(conn, e, said("yes sd-1 ffffeeee"), pos,
                                                                   session="S-c", hold=False)[0])
                high = dict(e, diff={"id": "sd-1", "tier": "high", "org": "acme"})
                self.assertIn("approved at the machine",
                              of._remote_approval(conn, high, ok, pos, session="S-c", hold=False)[0])
                self.assertIn("no recorded prompt", of._remote_approval(conn, e, "nope", pos, session="S-c",
                                                                        hold=False)[0])
                # on hold (docs/remote.md §5): the same yes, every tier, is refused and sent to the machine
                for diff in (e, high):
                    why, prompt = of._remote_approval(conn, diff, ok, pos, session="S-c")
                    self.assertIn("on hold", why)
                    self.assertIn("of diff approve <id>` in a terminal", why)
                    self.assertIsNone(prompt)
                conn.close()
            finally:
                of_events.STATE_DIR = saved


class DiffAnswerApprovalTest(unittest.TestCase):
    """After-the-fact review of 2026-11-11, finding 1: only the popup's answer approves a diff's ask."""

    def test_only_the_popups_answer_counts(self):
        import tempfile
        import of_events
        import of_store
        with tempfile.TemporaryDirectory() as d:
            saved, of_events.STATE_DIR = of_events.STATE_DIR, d
            try:
                conn = of_store.connect()
                import of_locale
                yes_ru = of_locale.catalogue("ru")["yes"] + ", go"  # the owner's own language, from the catalogue
                for ask, via, said in (("A1", "asks-ui", "approve"), ("A2", "conductor", "approve"), ("A3", None, "approve"),
                                       ("A4", "asks-ui", yes_ru), ("A5", "asks-ui", "not now")):
                    payload = {"ask_id": ask, "by": "owner", "answer": said}
                    if via:
                        payload["via"] = via
                    of_store.add_event(conn, of_events.record("agent_question_answered", payload))
                self.assertTrue(of._diff_approved_by_answer(conn, "A1"))
                self.assertFalse(of._diff_approved_by_answer(conn, "A2"))  # the conductor's chat never approves
                self.assertFalse(of._diff_approved_by_answer(conn, "A3"))
                self.assertTrue(of._diff_approved_by_answer(conn, "A4"))
                self.assertFalse(of._diff_approved_by_answer(conn, "A5"))
                conn.close()
            finally:
                of_events.STATE_DIR = saved


if __name__ == "__main__":
    unittest.main()
