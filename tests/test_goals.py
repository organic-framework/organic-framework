"""The goal tree and the conditions in `of registry check` (docs/registry.md, Goals and Conditions; bin/of_goals.py)."""

import datetime
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_goals  # noqa: E402


def goal(**over):
    g = {"title": "t", "rank": 1, "measure": "m", "state": "active", "decided": "a1b2c3d4"}
    g.update(over)
    return {k: v for k, v in g.items() if v is not None}


GOALS = {"g-revenue": goal(by=datetime.date(2026, 12, 31)),
         "g-orders": goal(parent="g-revenue", target="+10%"),
         "g-aov": goal(parent="g-revenue", rank=2, target="+5%"),
         "g-other": goal(rank=2)}
CONDITIONS = {"c-compliance": {"title": "t", "measure": "m", "level": "no dated duty missed", "state": "active",
                               "decided": "a1b2c3d4", "under": "g-revenue"}}
WORKS = {"w-checkout": {"goal": "g-orders"}, "w-tax": {"condition": "c-compliance"}, "w-done": {"state": "done"}}


def lines(goals=GOALS, conditions=CONDITIONS, works=WORKS):
    return of_goals.check(goals, conditions, works)


class GoalCheckTest(unittest.TestCase):
    def test_the_doc_example_passes(self):
        self.assertEqual(lines(), [])
        self.assertEqual(of_goals.check(None, None, {"w": {}}), [])          # no goals: nothing asked of works

    def test_problems(self):
        cases = {
            "goals: Bad: the id must match": dict(GOALS, Bad=goal(rank=3)),
            "parent 'g-none' is not a goal": dict(GOALS, x1=goal(parent="g-none", target="t")),
            "parent is the goal itself": dict(GOALS, x1=goal(parent="x1", target="t")),
            "rank must be a whole number": dict(GOALS, x1=goal(rank="first")),
            "rank 1 is held by g-other, g-revenue among the root goals": dict(GOALS, **{"g-other": goal()}),
            "rank 1 is held by g-aov, g-orders under g-revenue": dict(GOALS, **{"g-aov": goal(parent="g-revenue",
                                                                                              target="t")}),
            "state 'open' is not one of": dict(GOALS, x1=goal(rank=3, state="open")),
            "decided is required": dict(GOALS, x1=goal(rank=3, decided=None)),
            "measure is required": dict(GOALS, x1=goal(rank=3, measure=" ")),
            "target is required on a sub-goal": dict(GOALS, x1=goal(parent="g-revenue", rank=3)),
            "by is a date": dict(GOALS, x1=goal(rank=3, by="end of year")),
            "its parents form a cycle": dict(GOALS, x1=goal(parent="x2", target="t"), x2=goal(parent="x1", target="t")),
        }
        for want, goals in cases.items():
            out = "\n".join(lines(goals=goals))
            self.assertIn(want, out, want)
        conds = {
            "the id is also a goal's": {"g-other": CONDITIONS["c-compliance"]},
            "level is required": {"c-x": dict(CONDITIONS["c-compliance"], level="")},
            "conditions: c-x: state 'achieved'": {"c-x": dict(CONDITIONS["c-compliance"], state="achieved")},
            "under 'g-none' is not a goal": {"c-x": dict(CONDITIONS["c-compliance"], under="g-none")},
            "a condition has no rank": {"c-x": dict(CONDITIONS["c-compliance"], rank=1)},
        }
        for want, c in conds.items():
            self.assertIn(want, "\n".join(lines(conditions=c)), want)
        works = {
            "names both goal 'g-orders' and condition 'c-compliance'": {"w": {"goal": "g-orders",
                                                                               "condition": "c-compliance"}},
            "goal 'g-none' is not a goal": {"w": {"goal": "g-none"}},
            "condition 'c-none' is not a condition": {"w": {"condition": "c-none"}},
        }
        for want, w in works.items():
            self.assertIn(want, "\n".join(lines(works=w)), want)

    def test_warnings(self):
        many = dict(GOALS, **{f"g-r{i}": goal(rank=10 + i) for i in range(6)})
        out = "\n".join(lines(goals=many))
        self.assertIn("warning: goals: 8 active root goals, more than 7", out)
        kids = dict(GOALS, **{f"g-k{i}": goal(parent="g-other", rank=i, target="t") for i in range(8)})
        self.assertIn("warning: goals: g-other: 8 active sub-goals, more than 7", "\n".join(lines(goals=kids)))
        self.assertIn("warning: works: w: goal g-revenue has sub-goals", "\n".join(lines(works={"w": {"goal": "g-revenue"}})))
        closed_kids = dict(GOALS, **{k: dict(GOALS[k], state="dropped") for k in ("g-orders", "g-aov")})
        self.assertNotIn("has sub-goals", "\n".join(lines(goals=closed_kids, works={"w": {"goal": "g-revenue"}})))
        out = lines(works={"w-open": {}, "w-parked": {"state": "parked"}})
        self.assertEqual(out, ["warning: works: w-open: names no goal or condition while the organization has them "
                               "(a question for the owner)"])
        closed = dict(GOALS, **{"g-revenue": goal(state="achieved")})
        out = "\n".join(lines(goals=closed))
        self.assertIn("warning: goals: g-orders: active under g-revenue, which is achieved", out)
        conds = {f"c-{i}": dict(CONDITIONS["c-compliance"]) for i in range(8)}
        self.assertIn("warning: conditions: more than 7 active conditions", "\n".join(lines(conditions=conds)))

    def test_a_budget_is_no_longer_a_key(self):
        got = lines(dict(GOALS, x2=goal(rank=3, budget={"usd_month": 5})),
                    dict(CONDITIONS, c2=dict(CONDITIONS["c-compliance"], budget={"tokens_month": 5})))
        self.assertEqual(sorted(x.split(": budget")[0] for x in got), ["warning: conditions: c2", "warning: goals: x2"])

    def test_the_tree_is_in_rank_order(self):
        self.assertEqual([(d, g) for d, g, _ in of_goals.tree(GOALS)],
                         [(0, "g-revenue"), (1, "g-orders"), (1, "g-aov"), (0, "g-other")])
        loop = {"x1": goal(parent="x2"), "x2": goal(parent="x1")}
        self.assertEqual(of_goals.tree(loop), [])                          # a cycle has no root: nothing to walk


if __name__ == "__main__":
    unittest.main()
