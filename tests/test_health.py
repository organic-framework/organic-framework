"""OF's own health (docs/health.md; hand-over 6d9a8305): the checks, the state, the events, the smoke check, rollback."""

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_events  # noqa: E402
import of_health  # noqa: E402


class StateDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = self.tmp.name

    def tearDown(self):
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()


class _Body:
    def __init__(self, d):
        import json as _j
        self.data = _j.dumps(d).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.data


class ChecksTest(StateDir):
    def readings(self, swap=0.5, errors=0.0, served=None):
        return of_health.measure(now=1000.0, server=0.1, lags={"asks": 5.0}, views={"asks": (5.0, 120.0)},
                                 hooks=(errors, 0.3, {"PostToolUse": 0.3}, 400), swap=swap, disk=60.0,
                                 smoke={"head": "abc", "ok": True, "detail": "ok"}, served=served or {})

    def test_grades_and_limits(self):
        self.assertEqual([of_health.grade(v, 2, 20) for v in (1, 3, 21, None)], ["ok", "degraded", "failed", "failed"])
        self.assertEqual([of_health.grade(v, 25, 20, "below") for v in (30, 22, 10)], ["ok", "degraded", "failed"])
        r = self.readings(swap=0.96)
        self.assertEqual(r["machine.memory"]["state"], "failed")
        self.assertEqual(r["listener.lag.asks"]["state"], "ok")
        self.assertEqual(r["view.age.asks"]["limit"], [120.0, 600.0])     # one number: degraded past it, 5x fails

    def test_an_event_only_on_a_change_and_the_owner_told_on_fail_and_back(self):
        events, told = [], []
        run = lambda r, now: of_health.run(now=now, readings=r, emit=lambda k, p, **kw: events.append(p),  # noqa: E731
                                           fyi=lambda o, t: told.append((o, t)), log=lambda m: None,
                                           cards=lambda state: None)
        run(self.readings(), 1000.0)
        self.assertEqual(events, [])                                       # all ok from the start: nothing recorded
        run(self.readings(errors=0.2), 1060.0)
        run(self.readings(errors=0.3), 1120.0)
        self.assertEqual([(e["check"], e["state"]) for e in events], [("hooks.errors", "failed")])  # once
        self.assertEqual(told, [])                                         # not yet 5 minutes
        run(self.readings(), 1180.0)                                       # a flap: back before anyone was told
        run(self.readings(errors=0.2), 1240.0)
        self.assertEqual(told, [])
        run(self.readings(errors=0.2), 1240.0 + of_health.TELL_AFTER_S)    # failed for 5 minutes: told once
        run(self.readings(errors=0.2), 1300.0 + of_health.TELL_AFTER_S)
        self.assertEqual(len(told), 1)
        self.assertIn("failed on this machine", told[0][1])
        state = run(self.readings(), 1400.0 + of_health.TELL_AFTER_S)
        self.assertEqual([o for o, _ in told], ["of_platform", "of_platform"])  # and once when it is over
        self.assertIn("is over", told[1][1])
        self.assertEqual([(e["check"], e["state"], e["was"]) for e in events][1], ("hooks.errors", "ok", "failed"))
        self.assertEqual(state["checks"]["hooks.errors"]["since"], of_health.iso(1400.0 + of_health.TELL_AFTER_S))
        with open(of_health.state_path()) as fh:
            self.assertEqual(json.load(fh)["checks"]["machine.disk"]["state"], "ok")
        self.assertIn("hooks.errors", of_health.table(state))

    def test_swap_is_read_by_its_full_path(self):
        seen = []
        out = "total = 14336.00M  used = 13107.20M  free = 1228.80M  (encrypted)"
        run = lambda argv, **k: seen.append(argv) or type("R", (), {"stdout": out})()  # noqa: E731
        if sys.platform == "darwin":
            self.assertEqual(of_health.swap_share(run), 0.914)
            self.assertEqual(seen[0][0], "/usr/sbin/sysctl")  # launchd's PATH has no /usr/sbin
        r = of_health.measure(now=1.0, server="none", lags={}, views={}, hooks=(0, 0, {}, 0), swap=None, disk=50,
                              smoke={"head": "x", "ok": True}, served={})
        self.assertIn("machine.memory", r) if sys.platform == "darwin" else None

    def test_the_servers_checks_are_carried_and_the_cards_called(self):
        """of_studio (hand-over f06e0eb7): of-server's own checks on this machine's page, as it serves them, with no
        event or fyi from here (the server records its own); and the person's cards from the state."""
        served = of_health.server_checks(url="http://x", opener=lambda req, timeout: _Body({"checks": [
            {"check": "relay.oldest", "state": "failed", "value": 4000, "limit_degraded": 600, "limit_failed": 3600,
             "since": "2026-11-14T00:00:00Z", "owner": "of_server"},
            {"check": "server.health", "state": "ok", "value": 0.2, "limit_degraded": 2, "limit_failed": 20}]}))
        self.assertEqual(served["relay.oldest"]["limit"], [600, 3600])
        r = self.readings(served=served)
        self.assertEqual(r["relay.oldest"]["source"], "of-server")
        self.assertNotIn("source", r["server.health"])                       # this machine's own measurement stays
        events, told, carded = [], [], []
        state = of_health.run(now=2000.0, readings=r, emit=lambda k, p, **kw: events.append(p),
                              fyi=lambda o, t: told.append(o), log=lambda m: None, cards=carded.append)
        self.assertEqual([e["check"] for e in events], [])                   # the server records its own
        self.assertEqual(state["checks"]["relay.oldest"]["since"], "2026-11-14T00:00:00Z")
        self.assertEqual(carded, [state])
        self.assertEqual(of_health.server_checks(url="http://x", opener=lambda req, timeout: 1 / 0), {})

    def test_hook_readings_take_the_last_ten_minutes_from_the_end(self):
        now = 1_800_000_000.0
        with open(os.path.join(self.tmp.name, "hook-timing.jsonl"), "w") as fh:
            for i in range(200):  # 100 old calls, 100 within the window
                ts = of_health.iso(now - (700 if i < 100 else 60))
                fh.write(json.dumps({"ts": ts, "event": "PostToolUse", "ms": 2000 if i == 199 else 100}) + "\n")
        with open(os.path.join(self.tmp.name, "hook-errors.log"), "w") as fh:
            fh.write(f"{of_health.iso(now - 900)} Stop spooled: old\n")
            fh.write(f"{of_health.iso(now - 30)} Stop spooled: spooled raw payload (OperationalError)\n")
            fh.write(f"{of_health.iso(now - 20)} store ingest chunk held the write lock 28.8 s\n")
        share, worst, p99, calls = of_health.hook_readings(now)
        self.assertEqual((calls, share), (100, 0.01))
        self.assertEqual(worst, 2.0)


class SmokeTest(unittest.TestCase):
    def test_the_working_copys_head_passes(self):
        repo = os.path.join(HERE, "..")
        if subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"], capture_output=True).returncode != 0:
            self.skipTest("not a git checkout")
        ok, detail = of_health.smoke(repo)
        self.assertTrue(ok, detail)
        self.assertIn("modules import", detail)


class RollbackTest(unittest.TestCase):
    def setUp(self):
        loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(HERE, "..", "bin", "of"))
        spec = importlib.util.spec_from_loader("of_cli", loader)
        self.of = importlib.util.module_from_spec(spec)
        loader.exec_module(self.of)
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "r")
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main")
        self.write("a.py", "a = 1\n")
        self.write("b.py", "b = 1\n")
        self.commit("first", "of_a")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *a):
        return subprocess.run(["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@example.com",
                               "-c", "commit.gpgsign=false", *a], capture_output=True, text=True, check=True).stdout

    def write(self, name, text):
        with open(os.path.join(self.repo, name), "w") as fh:
            fh.write(text)

    def commit(self, msg, position):
        self.git("add", "-A")
        self.git("commit", "-qm", f"{msg}\n\nPosition: {position}")
        return self.git("rev-parse", "HEAD").strip()

    def test_one_commits_own_paths_and_only_them(self):
        self.write("a.py", "a = 2  # broken\n")
        self.write("c.py", "c = 1\n")
        bad = self.commit("break a", "of_x")
        self.write("b.py", "b = 2\n")
        self.commit("change b", "of_y")
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@example.com"}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            new = self.of.rollback(self.repo, bad[:8], "of_platform", log=lambda m: None)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), new)
        with open(os.path.join(self.repo, "a.py")) as fh:
            self.assertEqual(fh.read(), "a = 1\n")
        with open(os.path.join(self.repo, "b.py")) as fh:
            self.assertEqual(fh.read(), "b = 2\n")                          # the later commit stays
        self.assertFalse(os.path.exists(os.path.join(self.repo, "c.py")))   # added by the bad commit: gone
        self.assertEqual(self.git("status", "--porcelain"), "")             # working copy and index follow
        msg = self.git("log", "-1", "--format=%B")
        self.assertIn(f"This reverts commit {bad} by of_x", msg)
        self.assertIn("Position: of_platform", msg)

    def test_an_edit_made_while_the_revert_commits_is_never_lost(self):
        """of_review on PR 145: another agent's edit, made while the revert was being committed, was overwritten."""
        self.write("a.py", "a = 2\n")
        bad = self.commit("break a", "of_x")
        before = self.git("rev-parse", "HEAD").strip()

        def run(argv, **kw):
            if "commit" in argv and "-F" in argv:  # the revert's commit, in its worktree: someone edits meanwhile
                self.write("a.py", "a = 3  # someone's edit in the meantime\n")
            return subprocess.run(argv, **kw)
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@example.com"}
        from unittest import mock
        with mock.patch.dict(os.environ, env), self.assertRaises(self.of.OfError) as e:
            self.of.rollback(self.repo, bad, "of_platform", run=run, log=lambda m: None)
        self.assertIn("appeared while the revert was made", str(e.exception))
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), before)         # main did not move
        with open(os.path.join(self.repo, "a.py")) as fh:
            self.assertEqual(fh.read(), "a = 3  # someone's edit in the meantime\n")

    def test_a_path_edited_in_the_last_moment_is_kept_and_staged_as_main(self):
        """of_server and of_review on PR 145: a path left as it was kept its pre-revert content staged, so the next
        plain commit undid the revert for it."""
        self.write("a.py", "a = 2\n")
        bad = self.commit("break a", "of_x")

        def run(argv, **kw):
            r = subprocess.run(argv, **kw)
            if "update-ref" in argv:  # main has moved; the file is edited before it is written
                self.write("a.py", "a = 4  # an edit in the last moment\n")
            return r
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@example.com"}
        from unittest import mock
        said = []
        with mock.patch.dict(os.environ, env):
            new = self.of.rollback(self.repo, bad, "of_platform", run=run, log=said.append)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), new)
        self.assertIn("edited meanwhile: a.py", said[0])
        with open(os.path.join(self.repo, "a.py")) as fh:
            self.assertEqual(fh.read(), "a = 4  # an edit in the last moment\n")  # the edit is kept
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")         # nothing staged undoes the revert
        self.assertEqual(self.git("show", f"{new}:a.py"), "a = 1\n")

    def test_refused_while_someone_edits_a_path(self):
        self.write("a.py", "a = 2\n")
        bad = self.commit("break a", "of_x")
        self.write("a.py", "a = 3  # someone's work in progress\n")
        with self.assertRaises(self.of.OfError) as e:
            self.of.rollback(self.repo, bad, "of_platform", log=lambda m: None)
        self.assertIn("someone is editing", str(e.exception))
        with open(os.path.join(self.repo, "a.py")) as fh:
            self.assertEqual(fh.read(), "a = 3  # someone's work in progress\n")


if __name__ == "__main__":
    unittest.main()
