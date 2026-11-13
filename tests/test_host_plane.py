"""The agent host's plane per organization user (deploy/host/plane.py; of_agent-server's hand-over 5de421ee, B6 and B2)."""

import importlib.util
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("plane", os.path.join(HERE, "..", "deploy", "host", "plane.py"))
plane = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plane)

HOMES = {"of": "/srv/of", "acme": "/srv/acme"}
ORGS = {"of": [("organic-framework-build", "/srv/of/organic-framework-build")],
        "acme": [("acme", "/srv/acme/acme-org")]}


class FilesTest(unittest.TestCase):
    def test_parsing_and_each_users_own_organizations(self):
        self.assertEqual(plane.parse_org_users(["of:organic-framework-build=/srv/of/ofb", "of:x=/srv/of/x"]),
                         {"of": [("organic-framework-build", "/srv/of/ofb"), ("x", "/srv/of/x")]})
        with self.assertRaises(SystemExit):
            plane.parse_org_users(["of:ofb=relative/path"])
        self.assertEqual(plane.parse_claude_dirs(["acme:.claude-a"], ORGS), {"of": [".claude"], "acme": [".claude-a"]})
        text = plane.orgs_yaml(ORGS["of"], "/srv/of")
        self.assertIn("name: organic-framework-build", text)
        self.assertNotIn("acme", text)
        with self.assertRaises(SystemExit):  # another user's checkout is not this plane's
            plane.orgs_yaml([("acme", "/srv/acme/acme-org")], "/srv/of")

    def test_zshrc_block_is_added_once_and_replaced_in_place(self):
        once = plane.zshrc("export EDITOR=vim\n")
        self.assertTrue(once.startswith("export EDITOR=vim\n"))
        self.assertIn("source /opt/organic-framework/bin/of-shell.zsh", once)
        self.assertEqual(plane.zshrc(once), once)
        moved = plane.zshrc(once + "alias ll='ls -l'\n")
        self.assertEqual(moved.count(plane.MARK), 1)
        self.assertTrue(moved.endswith("alias ll='ls -l'\n"))
        self.assertIn('exec /opt/homebrew/bin/python3 /opt/organic-framework/bin/of "$@"', plane.wrapper())

    def test_the_daemons_run_as_the_user_at_standard_and_the_watchdog_as_root(self):
        ds = plane.daemons("of", "/srv/of")
        self.assertEqual(sorted(ds), sorted(plane.label(s, "of") for s in plane.SERVICES))
        for svc in ("herdr", "listen", "health"):
            d = plistlib.loads(ds[plane.label(svc, "of")].encode())
            self.assertEqual((d["UserName"], d["EnvironmentVariables"]["HOME"], d["ProcessType"]),
                             ("of", "/srv/of", "Standard"))
            self.assertNotIn("Nice", d)
        self.assertEqual(plistlib.loads(ds[plane.label("herdr", "of")].encode())["ProgramArguments"][-1], "server")
        self.assertEqual(plistlib.loads(ds[plane.label("health", "of")].encode())["StartInterval"], 60)
        w = plistlib.loads(ds[plane.label("listen-watchdog", "of")].encode())
        self.assertNotIn("UserName", w)  # root, so it can restart the user's system daemon
        self.assertEqual(w["EnvironmentVariables"]["OF_STATE"], "/srv/of/.local/state/of")
        self.assertEqual(w["EnvironmentVariables"]["OF_LISTEN_TARGET"], "system/dev.organic-framework.listen.of")
        self.assertTrue(w["EnvironmentVariables"]["OF_LISTEN_WATCHDOG_STATE"].startswith(plane.WATCH_STATE))
        self.assertIn("import of_listen", w["ProgramArguments"][-1])  # no PyYAML needed for root

    def test_the_steps_per_user(self):
        got = [(u, what) for u, what, _ in plane.steps(ORGS, {"of": [".claude"], "acme": [".claude-a", ".claude-b"]},
                                                       homes=HOMES)]
        self.assertEqual(sum(1 for u, w in got if u == "acme" and w.startswith("hooks")), 2)
        self.assertIn(("of", "orgs.yaml: its own organizations only"), got)
        self.assertEqual(sum(1 for u, w in got if u is None), 8)  # four daemons per user
        acts = {w: a for _, w, a in plane.steps(ORGS, {"of": [".claude"], "acme": [".claude"]}, homes=HOMES)}
        hooks = acts["hooks, guard and deny rules in /srv/of/.claude"]
        self.assertEqual(hooks[1][-6:], ["install", "--guard", "--deny-secrets", "--config-dir", "/srv/of/.claude",
                                         "--yes"])


class DeployTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "src")
        os.makedirs(os.path.join(self.src, "bin"))
        self.git(self.src, "init", "-q", "-b", "main")
        self.commit("v1")
        self.root = os.path.join(self.tmp.name, "opt")
        self.ran = []

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, repo, *a):
        return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.com",
                               "-c", "commit.gpgsign=false", *a], capture_output=True, text=True, check=True).stdout

    def commit(self, text):
        with open(os.path.join(self.src, "bin", "of"), "w") as fh:
            fh.write(text + "\n")
        self.git(self.src, "add", "-A")
        self.git(self.src, "commit", "-qm", text)
        return self.git(self.src, "rev-parse", "HEAD").strip()

    def run_with(self, smoke_ok):
        def run(argv, **kw):
            if argv[0] == plane.PYTHON and "smoke" in argv:
                return subprocess.CompletedProcess(argv, 0 if smoke_ok else 1, "smoke: ok", "smoke: FAILED: imports")
            if argv[0] == plane.PYTHON:  # compileall: no Homebrew Python needed where the test runs
                self.ran.append(argv)
                return subprocess.CompletedProcess(argv, 0, "", "")
            if argv[0] in ("chown", "chmod"):
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.run(argv, **kw)
        return run

    def test_deploy_checks_the_commit_and_rolls_a_failing_one_back(self):
        v1 = self.git(self.src, "rev-parse", "HEAD").strip()
        self.assertEqual(plane.deploy(self.src, v1, run=self.run_with(True), log=lambda m: None, root=self.root), v1)
        v2 = self.commit("v2 broken")
        with self.assertRaises(SystemExit) as e:
            plane.deploy(self.src, v2, run=self.run_with(False), log=lambda m: None, root=self.root)
        self.assertIn("back on", str(e.exception))
        self.assertEqual(self.git(self.root, "rev-parse", "HEAD").strip(), v1)  # the commit before, still serving
        self.assertIn("compileall", self.ran[0])  # the users cannot write the read-only checkout's bytecode cache
        v3 = self.commit("v3")
        self.assertEqual(plane.deploy(self.src, v3, run=self.run_with(True), log=lambda m: None, root=self.root), v3)


if __name__ == "__main__":
    unittest.main()
