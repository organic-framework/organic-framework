"""The guard replay (bin/of_guard_replay.py): decisions per command hash, compared before and after."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import of_guard_replay  # noqa: E402


class ReplayTest(unittest.TestCase):
    def test_compare_sorts_new_removed_and_changed(self):
        before = {"a": None, "b": "x", "c": "y", "d": None, "gone": None}
        after = {"a": "z", "b": None, "c": "w", "d": None, "fresh": "q"}
        new, removed, changed = of_guard_replay.compare(before, after)
        self.assertEqual(([h for h, *_ in new], [h for h, *_ in removed], [h for h, *_ in changed]),
                         (["a"], ["b"], ["c"]))

    def test_decide_keeps_only_the_reason(self):
        dec = of_guard_replay.decide({"h1": "ls -la", "h2": "print" + "env"})
        self.assertIsNone(dec["h1"])
        self.assertIn("print secrets", dec["h2"])

    def test_org_replay_counts_per_rule_position_and_day(self):
        rules = [{"id": "by-name", "except": ["keeper"],
                  "kubectl": {"verbs": ["exec"], "pods": ["runner-*"]}}]
        rows = [("a", "2026-11-08", "kubectl exec runner-1 -- sh"),
                ("a", "2026-11-09", "kubectl exec runner-2 -- sh"),
                ("keeper", "2026-11-09", "kubectl exec runner-2 -- sh"),
                ("b", "2026-11-09", "kubectl exec $POD -- sh"),
                ("b", "2026-11-09", "kubectl logs runner-1")]
        res = of_guard_replay.org_replay(rules, rows)["by-name"]
        self.assertEqual((dict(res["positions"]), dict(res["days"]), res["excepted"], res["in_variable"]),
                         ({"a": 2}, {"2026-11-08": 1, "2026-11-09": 1}, 1, 1))

    def test_org_breakdown_counts_namespace_verb_and_kind(self):
        rules = [{"id": "admin", "except": ["keeper"], "kubectl": {"admin": {"configs": ["~/.kube/config"]}}}]
        rows = [("a", "d", "kubectl -n evm get pods; kubectl -n evm get pods,svc"),
                ("a", "d", "kubectl --kubeconfig /k/own.yaml -n evm get pods"),   # own credential: not matched
                ("a", "d", "kubectl get $R -A"),
                ("keeper", "d", "kubectl -n evm get pods")]
        self.assertEqual(dict(of_guard_replay.org_breakdown(rules, rows)),
                         {("admin", "a", "evm", "get", "pods"): 2, ("admin", "a", "evm", "get", "services"): 1,
                          ("admin", "a", "(all)", "get", "unparsed"): 1})


    def test_org_replay_names_a_grant_that_would_be_refused(self):
        # a proposed grant `of grant issue` would refuse (admin: true) is named, not replayed; the rest run
        # (a user organization's draft, 2026-11-12)
        import contextlib
        import io
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            rules = os.path.join(t, "rules.yaml")
            with open(rules, "w") as fh:
                fh.write('grants:\n  - {id: bad, subject: "*", effect: deny, scope: {kubectl: {admin: true}}}\n'
                         '  - {id: ok, subject: "*", effect: deny, scope: {kubectl: {verbs: [exec]}}}\n'
                         '  - {id: ok, subject: "*", effect: deny, scope: {kubectl: {pods: true}}}\n'  # a shared id
                         '  - {subject: "*", effect: deny, scope: {kubectl: {verbs: [cp]}}}\n')         # no id
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                of_guard_replay.main(["org", "--org", "x", "--rules", rules, "--db", os.path.join(t, "of.db")])
        self.assertIn("grant bad not replayed: scope kubectl.admin takes lists of strings", out.getvalue())
        self.assertIn("rule ok (warn): 0 calls", out.getvalue())
        self.assertNotIn("rule bad", out.getvalue())
        # each grant its own verdict: the second "ok" is refused, the first replayed; a grant with no id replays
        self.assertIn("grant ok not replayed: scope kubectl.pods is a list of strings", out.getvalue())
        self.assertEqual(out.getvalue().count("rule ok (warn)"), 1)
        self.assertIn("rule None (warn): 0 calls", out.getvalue())


if __name__ == "__main__":
    unittest.main()
