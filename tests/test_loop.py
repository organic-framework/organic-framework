"""Development loops (RFC 0005, steps 1 and 2): the loop schema check and the hypothesis register."""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_loop  # noqa: E402

LOOP = {"target": {"metric": "day pass, STOREFRONT per day", "money": "STOREFRONT per day of bot revenue", "throughput_class": "raise"},
        "measures": [{"name": "latency_p50_ms", "to_money": "STOREFRONT per ms from the race arithmetic"}],
        "ladder": [{"rung": "oracle", "cost": "0", "accept": "0 differing rows"},
                   {"rung": "offline", "cost": "local CPU", "accept": "no candidate lost"},
                   {"rung": "day_pass", "cost": "6 runner-h", "accept": "> baseline + 1%"}],
        "checkpoint_days": 3, "wip_arms": 2, "babysit_model": "sonnet"}


def ev(i, name, minute, **p):
    return {"name": name, "ts": f"2026-11-07T10:{minute:02d}:00Z", "payload": p}


class LoopCheckTest(unittest.TestCase):
    def test_the_rfc_example_passes(self):
        self.assertEqual(of_loop.check_loop(LOOP, "w"), [])
        extended = dict(LOOP, target=dict(LOOP["target"], better="higher"),
                        ladder=LOOP["ladder"] + [{"rung": "shadow", "accept": "no loss", "production": True, "since": "2026-11-08"}])
        self.assertEqual(of_loop.check_loop(extended, "w"), [])
        self.assertTrue(of_loop.check_loop(dict(LOOP, target=dict(LOOP["target"], better="up")), "w"))

    def test_problems_and_warnings(self):
        bad = {"target": {"metric": "x"}, "measures": [{"name": "latency"}],
               "ladder": [{"rung": "Day Pass"}, {"rung": "a", "accept": "ok"}, {"rung": "a", "accept": "ok"}],
               "checkpoint_days": 0, "wip_arms": "two", "babysit_model": "gpt-5", "extra": 1}
        out = "\n".join(of_loop.check_loop(bad, "w"))
        for want in ("target.money is required", "measure 'latency' needs to_money", "ladder[0].rung must match",
                     "rung 'a' appears twice", "checkpoint_days must be a positive", "wip_arms must be a positive",
                     "warning: w: loop: babysit_model", "warning: w: loop: unknown key 'extra'"):
            self.assertIn(want, out)
        self.assertEqual(of_loop.check_loop("x", "w"), ["w: loop must be a mapping"])
        self.assertIn("ladder is required", "\n".join(of_loop.check_loop({"target": LOOP["target"]}, "w")))

    def test_a_loop_yaml_beside_the_doc_is_checked_and_one_place_only(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(HERE, "..", "bin", "of"))
        spec = importlib.util.spec_from_loader("of_cli", loader)
        of = importlib.util.module_from_spec(spec)
        loader.exec_module(of)
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "storefront", "docs", "rfc"))
            with open(os.path.join(root, "storefront", "docs", "rfc", "search.md"), "w") as fh:
                fh.write("# x\n")
            with open(os.path.join(root, "storefront", "docs", "rfc", "loop.yaml"), "w") as fh:
                fh.write("loop:\n  target: {metric: x}\n  ladder: [{rung: offline, accept: ok}]\n")
            works = {"w": {"title": "t", "kind": "rfc", "doc": "storefront:docs/rfc/search.md (PR #1)"}}
            self.assertTrue(of.doc_path(works["w"]["doc"], [root]).endswith("search.md"))
            out = of.loop_files(works, [root])
            self.assertTrue(any("target.money is required" in x for x in out), out)
            works["w"]["loop"] = LOOP
            self.assertTrue(any("both in the registry and in" in x for x in of.loop_files(works, [root])))


class RegisterTest(unittest.TestCase):
    def test_status_is_derived_and_the_next_is_first(self):
        rows = [ev(1, "of.loop_hypothesis", 1, work="w", hypothesis="h1", change="cheap and valuable", cost="oracle, 0",
                   cost_h=0.0, voi="high"),
                ev(2, "of.loop_hypothesis", 2, work="w", hypothesis="h2", change="dear", cost="day_pass, 6 runner-h",
                   cost_h=6.0, voi="high"),
                ev(3, "of.loop_hypothesis", 3, work="w", hypothesis="h3", change="dropped", cost_h=1.0, voi="low"),
                ev(4, "of.loop_run", 4, work="w", rung="day_pass", hypothesis="h2", result="325.8 STOREFRONT"),
                ev(5, "of.loop_decision", 5, work="w", decision="drop", hypothesis="h3"),
                ev(6, "of.loop_hypothesis", 6, work="other", hypothesis="hx", change="x", voi="low")]
        reg = of_loop.register(rows, "w")
        self.assertEqual([(h["id"], h["status"]) for h in reg["hypotheses"]], [("h1", "open"), ("h2", "testing"),
                                                                               ("h3", "dropped")])
        self.assertEqual((len(reg["runs"]), len(reg["decisions"])), (1, 1))
        self.assertEqual(of_loop.cost_hours("day_pass, 6 runner-h"), 6.0)
        self.assertEqual(of_loop.cost_hours("gate, 40 min"), 40 / 60)
        self.assertEqual(of_loop.cost_hours("0"), 0.0)


if __name__ == "__main__":
    unittest.main()


class LoopCliTest(unittest.TestCase):
    def test_hypothesis_run_decision_show(self):
        import subprocess
        import yaml
        with tempfile.TemporaryDirectory() as root:
            org = os.path.join(root, "org")
            os.makedirs(os.path.join(org, "registry"))
            with open(os.path.join(org, "registry", "positions.yaml"), "w") as fh:
                yaml.safe_dump({"organization": "lab", "positions": [{"name": "lab_doer", "system": "s", "role": "doer"}],
                                "systems": {"s": {"name": "S", "kind": "target"}},
                                "works": {"w-search": {"title": "Search", "kind": "rfc", "loop": LOOP}}}, fh)
            orgs = os.path.join(root, "orgs.yaml")
            with open(orgs, "w") as fh:
                yaml.safe_dump({"orgs": [{"name": "lab", "path": org, "paths": [root]}]}, fh)
            env = dict(os.environ, OF_ORGS=orgs, OF_STATE=os.path.join(root, "state"))
            env.pop("CLAUDE_CODE_SESSION_ID", None)
            of = os.path.join(HERE, "..", "bin", "of")
            run = lambda *a: subprocess.run([sys.executable, of, "loop", *a], capture_output=True, text=True, env=env)
            r = run("hypothesis", "w-search", "drop the floor", "--predict", "+1%", "--cost", "day_pass, 6 runner-h",
                    "--voi", "high", "--from", "lab_doer")
            self.assertEqual(r.returncode, 0, r.stderr)
            hid = r.stdout.split()[-1]
            self.assertNotEqual(run("run", "w-search", "--rung", "moon", "--from", "lab_doer").returncode, 0)
            self.assertEqual(run("run", "w-search", "--rung", "day_pass", "--hypothesis", hid, "--result", "325.8 STOREFRONT",
                                 "--value", "325.8", "--accepted", "yes", "--started-at", "2026-11-07T08:00:00Z",
                                 "--from", "lab_doer").returncode, 0)
            self.assertNotEqual(run("run", "w-search", "--rung", "day_pass", "--value", "lots", "--from", "lab_doer").returncode, 0)
            for _ in range(2):  # a runner's job, synced twice, is recorded once
                r = run("run", "w-search", "--rung", "offline", "--run", "cbx-job-1", "--source", "cbx", "--from", "lab_doer")
                self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("already recorded", r.stdout)
            self.assertNotEqual(run("decision", "w-search", "change_objective", "--metric", "latency", "--from", "lab_doer").returncode, 0)
            self.assertEqual(run("decision", "w-search", "change_objective", "--metric", "p50 ms", "--to-money", "STOREFRONT per ms",
                                 "--from", "lab_doer").returncode, 0)
            import json
            import sqlite3
            db = sqlite3.connect(os.path.join(root, "state", "of.db"))
            runp = json.loads(db.execute("SELECT payload FROM event WHERE name = 'of.loop_run'").fetchone()[0])
            self.assertEqual((runp["value"], runp["accepted"], runp["metric"], runp["started_at"]),
                             (325.8, True, "day pass, STOREFRONT per day", "2026-11-07T08:00:00Z"))
            self.assertTrue(runp["ended_at"].endswith("Z") and runp["run"])
            cbx = [json.loads(r[0]) for r in db.execute("SELECT payload FROM event WHERE name = 'of.loop_run' "
                                                         "AND payload LIKE '%cbx-job-1%'")]
            self.assertEqual([(c["run"], c["source"]) for c in cbx], [("cbx-job-1", "cbx")])
            decp = json.loads(db.execute("SELECT payload FROM event WHERE name = 'of.loop_decision' "
                                         "AND payload LIKE '%change_objective%'").fetchone()[0])
            self.assertEqual((decp["metric"], decp["to_money"], decp["money"]), ("p50 ms", "STOREFRONT per ms", "STOREFRONT per ms"))
            hyp = [json.loads(r[0]) for r in db.execute("SELECT payload FROM event WHERE name = 'of.loop_hypothesis' ORDER BY rowid")]
            self.assertEqual(hyp[0]["cost"], {"rung": "day_pass", "runner_h": 6.0, "text": "day_pass, 6 runner-h"})
            self.assertEqual([h.get("status") for h in hyp], ["open", "testing"])  # the first run: testing
            self.assertEqual(run("decision", "w-search", "keep", "--hypothesis", hid, "--from", "lab_doer").returncode, 0)
            out = run("show", "w-search").stdout
            self.assertIn(f"{hid} kept", out)
            self.assertIn("oracle > offline > day_pass", out)


class LoopWorkTest(unittest.TestCase):
    def test_a_work_with_a_loop_needs_owner_and_doers(self):
        """of_architect, 4b5a274: owner and doers are required on a work with a loop."""
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(HERE, "..", "bin", "of"))
        spec = importlib.util.spec_from_loader("of_cli", loader)
        of = importlib.util.module_from_spec(spec)
        loader.exec_module(of)
        w = {"w-s": {"title": "t", "kind": "rfc", "loop": LOOP}}
        out = "\n".join(of.check_works(w, {"o", "d"}, {}))
        self.assertIn("needs an owner", out)
        self.assertIn("needs doers", out)
        w["w-s"].update(owner="o", doers=["d"])
        self.assertEqual([x for x in of.check_works(w, {"o", "d"}, {}) if "loop" in x or "owner" in x], [])
