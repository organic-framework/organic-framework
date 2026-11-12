"""Goal and condition budgets: the 80% warning and the 100% card (docs/registry.md, Goals, Budgets; bin/of_budgets.py)."""

import calendar
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_budgets  # noqa: E402
import of_goals  # noqa: E402

NOW = calendar.timegm(time.strptime("2026-11-20T12:00:00", "%Y-%m-%dT%H:%M:%S"))
DOC = {"goals": {"g-revenue": {"title": "Revenue", "owner": "acme_owner", "budget": {"tokens_month": 400_000_000}},
                 "g-speed": {"title": "Speed", "parent": "g-revenue", "budget": {"usd_month": 1000}}},
       "conditions": {"c-up": {"title": "Up", "under": "g-revenue", "budget": {"usd_month": 100}}}}
POSITIONS = [{"name": "acme_org", "role": "org", "_org": "acme", "status": "active"},
             {"name": "acme_owner", "role": "owner", "_org": "acme", "holder": {"runtime": "human", "person": "alex"}}]


def spend(share_speed, share_up=0.1, month="2026-11"):
    node = lambda share, b, t: {"share": share, "budget": b, "total": t, "forecast": {"fresh": 1, "cost_usd": 2}}  # noqa: E731
    return {"month": month, "orgs": {"acme": {
        "goals": {"g-revenue": node({"tokens": 0.5}, {"tokens_month": 400_000_000}, {"fresh": 200_000_000, "cost_usd": 9}),
                  "g-speed": node({"usd": share_speed}, {"usd_month": 1000}, {"fresh": 1, "cost_usd": share_speed * 1000})},
        "conditions": {"c-up": node({"usd": share_up}, {"usd_month": 100}, {"fresh": 1, "cost_usd": share_up * 100})}}}}


class BudgetTest(unittest.TestCase):
    def run_check(self, sp, mem):
        events, fyis = [], []
        done = of_budgets.check(sp, {"acme": DOC}, POSITIONS, mem, NOW, lambda to, t: fyis.append((to, t)),
                                log=lambda m: None, emit=lambda k, p, **kw: events.append((k, p, kw)))
        return done, events, fyis

    def test_80_warns_the_keeper_once_and_100_files_one_card(self):
        mem = {}
        done, events, fyis = self.run_check(spend(0.85), mem)
        self.assertEqual(done, [("acme", "goals", "g-speed", 80)])
        self.assertEqual([k for k, _, _ in events], ["budget_warning"])
        self.assertEqual(events[0][1]["level"], 80)
        self.assertEqual(fyis[0][0], "acme:acme_org")
        self.assertIn("g-speed is at 85% of its monthly budget ($850 of $1,000)", fyis[0][1])
        self.assertEqual(self.run_check(spend(0.9), mem)[0], [])            # told already this month
        done, events, fyis = self.run_check(spend(1.02), mem)
        self.assertEqual(done, [("acme", "goals", "g-speed", 100)])
        self.assertEqual([k for k, _, _ in events], ["budget_warning", "agent_question"])
        card = events[1][1]
        self.assertEqual(card["to"], "acme_owner")                          # a person holds the parent's owner position
        self.assertIn("g-speed (Speed) of acme has used 102% of its monthly budget", card["questions"][0]["question"])
        self.assertEqual(len(card["questions"][0]["options"]), 2)
        self.assertEqual(fyis, [])                                          # the keeper heard at 80%
        self.assertTrue(events[1][2]["event_id"])                           # one card per node and month, everywhere
        self.assertEqual(self.run_check(spend(1.5), mem)[0], [])

    def test_a_new_month_starts_over_and_old_sums_say_nothing(self):
        mem = {"budgets": {"2026-10:acme:goals:g-speed": 100}}
        self.assertEqual(self.run_check(spend(0.85, month="2026-10"), mem)[0], [])
        self.assertEqual(self.run_check(spend(0.85), mem)[0], [("acme", "goals", "g-speed", 80)])
        self.assertEqual(list(mem["budgets"]), ["2026-11:acme:goals:g-speed"])

    def test_a_card_goes_to_the_founder_when_an_agent_holds_the_owner_position(self):
        agent = [dict(POSITIONS[1], holder={"runtime": "claude-code", "session": "s"}), POSITIONS[0]]
        self.assertEqual(of_budgets.card_to(DOC, "goals", "g-speed", agent, "acme"), "founder")
        self.assertEqual(of_budgets.card_to(DOC, "conditions", "c-up", POSITIONS, "acme"), "acme_owner")  # under's owner
        self.assertEqual(of_budgets.card_to({"goals": {"g": {}}}, "goals", "g", POSITIONS, "acme"), "founder")


class BudgetCheckTest(unittest.TestCase):
    def goals(self, **over):
        base = {"g-a": {"rank": 1, "measure": "m", "state": "active", "decided": "x", "budget": {"tokens_month": 100}},
                "g-b": {"parent": "g-a", "rank": 1, "measure": "m", "target": "t", "state": "active", "decided": "x",
                        "budget": {"tokens_month": 70}},
                "g-c": {"parent": "g-a", "rank": 2, "measure": "m", "target": "t", "state": "active", "decided": "x",
                        "budget": {"tokens_month": 20}}}
        base.update(over)
        return "\n".join(of_goals.check(base, None, None))

    def test_budget_shape_and_sub_budgets(self):
        self.assertEqual(self.goals(), "")
        self.assertIn("budget.tokens_month must be a positive number",
                      self.goals(**{"g-c": dict(rank=2, parent="g-a", measure="m", target="t", state="active",
                                                decided="x", budget={"tokens_month": -1})}))
        self.assertIn("unknown key 'tokens'", self.goals(**{"g-d": dict(rank=2, measure="m", state="active",
                                                                         decided="x", budget={"tokens": 5})}))
        self.assertIn("budget is a mapping", self.goals(**{"g-d": dict(rank=2, measure="m", state="active",
                                                                        decided="x", budget=5)}))
        self.assertIn("warning: goals: g-a: its sub-goals' budgets add up to 110 tokens_month, more than its own 100",
                      self.goals(**{"g-c": dict(rank=2, parent="g-a", measure="m", target="t", state="active",
                                                decided="x", budget={"tokens_month": 40})}))


if __name__ == "__main__":
    unittest.main()
