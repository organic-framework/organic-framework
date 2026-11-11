#!/usr/bin/env python3
"""lab-drift-v0: assert each of_studio detector clears a precision/recall floor on the synthetic labelled fixtures.
A catalogue or detector change that regresses a rule below the floor fails CI. The floor is loose (of_studio
tightens it to the owner's chosen point); the fixtures currently score P/R 1.0 on every positively-labelled rule.

EXPECTED is derived from the fixtures: every rule with a "positive" label (of_field-analyst, 2026-11-10: the
fixtures now cover each built catalogue rule, 8f82f5c). A rule that gains a positive fixture is held to the floor
automatically. The unbuilt rules (context-overload, missing-verifier) carry only "blocked" labels and are checked
by test_expected_blocked_rules_report_blocked, not by the floor.

Run: python3 -m unittest tests.test_drift_v0
"""
import os
import sys
import unittest

SC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lab", "scenarios", "lab-drift-v0")
sys.path.insert(0, SC)
import harness   # noqa: E402
import fixtures   # noqa: E402

FLOOR_P, FLOOR_R = 0.90, 0.90
# every rule the fixtures exercise with a positive label (and so expect to fire); held to the floor below.
EXPECTED = {l["rule"] for s in fixtures.SCENARIOS for l in s["labels"] if l["label"] == "positive"}


class DriftV0(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agg = harness.score(fixtures.SCENARIOS)

    def test_every_rule_is_scored(self):
        self.assertEqual(set(self.agg) & EXPECTED, EXPECTED,
                         f"rules missing from the scored fixtures: {EXPECTED - set(self.agg)}")

    def test_no_rule_blocked_in_its_own_fixture(self):
        for rule, s in self.agg.items():
            self.assertFalse(s["blocked_in"], f"{rule} blocked (no inputs) in {s['blocked_in']}")

    def test_expected_blocked_rules_report_blocked(self):
        # a rule a fixture labels "blocked" (an unbuilt rule with no input yet) must report blocked, not run or fire
        for rule, s in self.agg.items():
            self.assertEqual(s.get("blocked_miss", 0), 0,
                             f"{rule} labelled blocked but ran/fired in {s['blocked_miss']} fixture(s)")

    def test_precision_and_recall_floor(self):
        for rule in EXPECTED:
            s = self.agg[rule]
            tp, fp, fn = s["tp"], s["fp"], s["fn"]
            p = tp / (tp + fp) if (tp + fp) else 1.0
            r = tp / (tp + fn) if (tp + fn) else 1.0
            self.assertGreaterEqual(p, FLOOR_P, f"{rule} precision {p:.2f} < {FLOOR_P} (tp {tp} fp {fp})")
            self.assertGreaterEqual(r, FLOOR_R, f"{rule} recall {r:.2f} < {FLOOR_R} (tp {tp} fn {fn})")
            self.assertGreater(tp, 0, f"{rule} never fired on a positive")


if __name__ == "__main__":
    unittest.main()
