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

    def test_a_git_repository_in_the_home_folder_fails_at_once(self):
        """2026-11-15: an agent's command ran cd with an empty variable, then git init, in the home folder; every git
        call under home outside a repository of its own then walked the whole home folder."""
        r = of_health.measure(now=1.0, server="none", lags={}, views={}, hooks=(0, 0, {}, 0), swap=0.1, disk=50,
                              smoke={"head": "x", "ok": True}, served={}, homegit="/Users/alex/.git")
        self.assertEqual((r["machine.home_git"]["state"], r["machine.home_git"]["value"]), ("failed", "/Users/alex/.git"))
        self.assertIn("move it aside", r["machine.home_git"]["detail"])
        ok = of_health.measure(now=1.0, server="none", lags={}, views={}, hooks=(0, 0, {}, 0), swap=0.1, disk=50,
                               smoke={"head": "x", "ok": True}, served={}, homegit=None)
        self.assertEqual(ok["machine.home_git"]["state"], "ok")
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            self.assertIsNone(of_health.home_git(home))
            os.makedirs(os.path.join(home, ".git"))
            self.assertEqual(of_health.home_git(home), os.path.join(home, ".git"))

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

    def test_one_message_per_owner_per_run(self):
        """2026-11-17: a stopped listener failed 22 checks of one owner, and it got 22 notes; then a reminder a day
        each."""
        told, typed = [], []
        run = lambda r, now: of_health.run(  # noqa: E731
            now=now, readings=r, emit=lambda k, p, **kw: None, fyi=lambda o, t: told.append((o, t)),
            typed=lambda o, t: typed.append((o, t)), log=lambda m: None, cards=lambda state: None)
        t0 = 20_000.0
        run(self.readings(errors=0.2, swap=0.96), t0)
        run(self.readings(errors=0.2, swap=0.96), t0 + of_health.TELL_AFTER_S)
        self.assertEqual(len(told), 1)                                       # two checks, one note
        self.assertIn("2 checks", told[0][1])
        self.assertIn("- hooks.errors failed on this machine", told[0][1])
        self.assertIn("- machine.memory failed on this machine", told[0][1])
        run(self.readings(errors=0.2, swap=0.96), t0 + 86400)
        self.assertEqual(len(typed), 1)                                      # one reminder for both
        self.assertIn("2 checks failed for a day or more", typed[0][1])
        state = run(self.readings(), t0 + 86400 + 60)
        self.assertEqual(len(told), 2)                                       # both over: one note
        self.assertIn("the failure you were told of is over", told[1][1])
        self.assertIsNone(state["checks"]["hooks.errors"]["reminded"])

    def test_a_note_not_sent_is_sent_at_the_next_run_and_never_stops_the_run(self):
        """of_architect, message 01a1e8ee-5b44: a slow `of send` raised TimeoutExpired out of the run before health.json
        was written, and `told` was set although the note was not sent."""
        import subprocess
        told, logged = [], []

        def fyi(o, t, fail=[True]):
            if fail[0]:
                raise subprocess.TimeoutExpired("of send", 60)
            told.append(t)
        run = lambda r, now: of_health.run(  # noqa: E731
            now=now, readings=r, emit=lambda k, p, **kw: None, fyi=fyi, typed=lambda o, t: None,
            log=logged.append, cards=lambda state: None)
        t0 = 30_000.0
        run(self.readings(errors=0.2), t0)
        state = run(self.readings(errors=0.2), t0 + of_health.TELL_AFTER_S)    # the send times out
        self.assertIsNone(state["checks"]["hooks.errors"]["told"])
        with open(of_health.state_path()) as fh:                                # the run still wrote its state
            self.assertEqual(json.load(fh)["at"], of_health.iso(t0 + of_health.TELL_AFTER_S))
        self.assertTrue(any("again at the next run" in m for m in logged))
        fyi.__defaults__[0][0] = False
        state = run(self.readings(errors=0.2), t0 + of_health.TELL_AFTER_S + 60)  # sent now
        self.assertEqual(len(told), 1)
        fyi.__defaults__[0][0] = True
        state = run(self.readings(), t0 + of_health.TELL_AFTER_S + 120)          # "over" lost: kept to send again
        self.assertTrue(state["checks"]["hooks.errors"]["told"])
        fyi.__defaults__[0][0] = False
        state = run(self.readings(), t0 + of_health.TELL_AFTER_S + 180)
        self.assertIn("is over", told[-1])
        self.assertIsNone(state["checks"]["hooks.errors"]["told"])
        run(self.readings(), t0 + of_health.TELL_AFTER_S + 240)
        self.assertEqual(len(told), 2)                                           # once

    def test_a_failure_that_lasts_is_typed_to_its_owner_once_a_day(self):
        """docs/health.md §3, "A failure that lasts is told again" (hand-over 4575c97a): on 2026-11-14 a check failed,
        its owner missed the one --fyi, and it stayed failed three days."""
        told, typed, logged = [], [], []
        run = lambda r, now, ok=True: of_health.run(  # noqa: E731
            now=now, readings=r, emit=lambda k, p, **kw: None, fyi=lambda o, t: told.append(o),
            typed=lambda o, t: typed.append((o, t)) or ok, log=logged.append, cards=lambda state: None)
        t0 = 10_000.0
        run(self.readings(errors=0.2), t0)
        run(self.readings(errors=0.2), t0 + of_health.TELL_AFTER_S)        # the first --fyi, as before
        self.assertEqual((told, typed), (["of_platform"], []))
        run(self.readings(errors=0.2), t0 + 86400 - 60)                     # not yet a day
        self.assertEqual(typed, [])
        run(self.readings(errors=0.2), t0 + 86400, ok=False)                # the relay could not send it: next run
        run(self.readings(errors=0.2), t0 + 86400 + 60)
        self.assertEqual(len(typed), 2)
        self.assertIn("hooks.errors has been failed on this machine since", typed[-1][1])
        self.assertIn("(1.0 days)", typed[-1][1])
        state = run(self.readings(errors=0.2), t0 + 86400 + 3600)           # once a day, not every run
        self.assertEqual(len(typed), 2)
        self.assertEqual(state["checks"]["hooks.errors"]["reminded"], of_health.iso(t0 + 86400 + 60))
        self.assertTrue(any("reminded of_platform of hooks.errors" in m for m in logged))
        run(self.readings(errors=0.2), t0 + 2 * 86400 + 60)                 # the next day
        self.assertEqual(len(typed), 3)
        state = run(self.readings(), t0 + 2 * 86400 + 120)                  # over: told once, no more reminders
        self.assertIsNone(state["checks"]["hooks.errors"]["reminded"])
        self.assertEqual(told, ["of_platform", "of_platform"])

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

    def test_only_a_cached_token_never_a_refresh(self):
        """of_review on PR 159: token() refreshes with four tries of 120 s each, which held the minute's run."""
        import json as _j
        path = os.path.join(self.tmp.name, "token.json")
        self.assertIsNone(of_health.cached_token(path))
        with open(path, "w") as fh:
            _j.dump({"token": "T", "expires": 1000.0}, fh)
        self.assertEqual(of_health.cached_token(path, now=900.0), "T")
        self.assertIsNone(of_health.cached_token(path, now=950.0))           # within a minute of its end: none

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
            fh.write(f"{of_health.iso(now - 10)} PostToolUse ERROR: KeyError 'x'\n")
        share, worst, p99, calls, spooled = of_health.hook_readings(now)
        # a spooled call is its own check, apart from a failed one (of_architect, 2026-11-17, message 01a1ea91-7683)
        self.assertEqual((calls, share, spooled), (100, 0.01, 0.01))
        r = of_health.measure(now=1.0, server="none", lags={}, views={}, hooks=(0.0, 0.1, {}, 100, 0.6), swap=0.1,
                              disk=50, smoke={"head": "x", "ok": True}, served={}, homegit=None)
        self.assertEqual((r["hooks.errors"]["state"], r["hooks.spooled"]["state"]), ("ok", "failed"))
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
