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


if __name__ == "__main__":
    unittest.main()
