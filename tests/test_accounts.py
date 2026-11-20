"""The machine's harness accounts (bin/of_accounts.py, docs/accounts.md): the list, of account, and of claude."""
import os
import re
import stat
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_accounts  # noqa: E402
import of_capacity  # noqa: E402
import of_events  # noqa: E402
import of_locale  # noqa: E402


class Accounts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        for d in (".claude", ".claude-a", ".claude-b.lock"):  # a lock folder is no account
            os.makedirs(os.path.join(self.home, d))
        open(os.path.join(self.home, ".claude.json"), "w").close()
        self.saved = of_events.CONFIG_DIR
        of_events.CONFIG_DIR = os.path.join(self.tmp.name, "config")
        self.h = lambda *p: os.path.join(self.home, *p)

    def tearDown(self):
        of_events.CONFIG_DIR = self.saved
        of_locale.set_language(None)
        self.tmp.cleanup()

    def test_with_no_file_the_folders_found_by_name(self):
        self.assertIsNone(of_accounts.load())
        self.assertEqual(of_accounts.dirs(self.home), [self.h(".claude"), self.h(".claude-a")])
        self.assertEqual(of_capacity.config_dirs(self.home), of_accounts.dirs(self.home))
        self.assertEqual([e["name"] for e in of_accounts.entries(self.home)], ["default", "a"])

    def test_adopt_then_the_file_decides(self):
        of_accounts.adopt(self.home, log=lambda m: None)
        self.assertEqual(of_accounts.load(), [{"name": "default", "config_dir": "~/.claude"},
                                              {"name": "a", "config_dir": "~/.claude-a"}])
        os.makedirs(self.h(".claude-z"))  # a folder made by hand is not used once the file exists
        self.assertEqual(of_capacity.config_dirs(self.home), [self.h(".claude"), self.h(".claude-a")])

    def test_codex_defaults_to_the_current_login_until_seats_are_listed(self):
        with mock.patch.dict(os.environ, CODEX_HOME=""):
            self.assertEqual(of_accounts.codex_seats(self.home), [{"name": "main", "home": self.h(".codex")}])
        of_accounts.save([{"name": "default", "config_dir": "~/.claude"}])
        with mock.patch.dict(os.environ, CODEX_HOME=self.h("custom")):
            self.assertEqual(of_accounts.codex_seats(self.home), [{"name": "main", "home": self.h("custom")}])
        of_accounts.save(seats=[{"name": "a", "home": "~/a"}, {"name": "b", "home": "~/b"}])
        self.assertEqual(of_accounts.codex_seats(self.home), [{"name": n, "home": self.h(n)} for n in ("a", "b")])
        of_accounts.add("claude-work", home=self.home, logged_in=True, log=lambda m: None)
        self.assertEqual([s["name"] for s in of_accounts.codex_seats(self.home)], ["a", "b"])

    def test_codex_cli_add_list_remove_preserve_claude_and_file_the_login_task(self):
        os.makedirs(of_events.CONFIG_DIR)
        claude = of_accounts.HEADER + "accounts: # keep this comment\n  - {name: a, config_dir: ~/.claude-a}\n"
        with open(of_accounts.path(), "w") as fh:
            fh.write(claude)
        with mock.patch.dict(os.environ, HOME=self.home, CODEX_HOME=""), \
                mock.patch.object(of_accounts, "_file_task", return_value=(True, "filed")) as task, \
                mock.patch("builtins.print") as output:
            self.assertEqual(of_accounts.main(["add", "x", "--codex"]), 0)
            self.assertEqual(stat.S_IMODE(os.stat(self.h(".codex-x")).st_mode), 0o700)
            title, steps = task.call_args.args
            self.assertIn("Codex", title)
            self.assertIn("plain terminal tab", steps)
            self.assertIn("CODEX_HOME=~/.codex-x codex login --device-auth", steps)
            with open(of_accounts.path()) as fh:
                saved = fh.read()
            self.assertTrue(saved.startswith(claude))
            self.assertIn("home: ~/.codex-x", saved)
            self.assertEqual([s["name"] for s in of_accounts.codex_seats()], ["main", "x"])
            self.assertEqual(of_accounts.main(["list"]), 0)
            self.assertIn(mock.call("codex:x  ~/.codex-x"), output.call_args_list)
            self.assertEqual(of_accounts.main(["remove", "x"]), 0)
            self.assertTrue(os.path.isdir(self.h(".codex-x")))
            self.assertEqual(of_accounts.main(["remove", "main"]), 0)
            self.assertEqual(of_accounts.codex_seats(), [])
            with open(of_accounts.path()) as fh:
                self.assertTrue(fh.read().startswith(claude))

    def test_codex_only_file_keeps_claude_discovery_and_uses_the_owners_language(self):
        os.makedirs(of_events.CONFIG_DIR)
        with open(of_events.config_path("owner.yaml"), "w") as fh:
            fh.write("language: ru\n")
        with mock.patch.dict(os.environ, HOME=self.home, CODEX_HOME=""), \
                mock.patch.object(of_accounts, "_file_task", return_value=(True, "filed")) as task, \
                mock.patch("builtins.print"):
            self.assertEqual(of_accounts.main(["add", "x", "--codex", "--home", "~/two words"]), 0)
            title, steps = task.call_args.args
            self.assertRegex(title, r"[\u0400-\u04ff]")
            self.assertRegex(steps, r"[\u0400-\u04ff]")
            self.assertIn("CODEX_HOME=~/'two words' codex login --device-auth", steps)
            self.assertEqual(of_accounts.dirs(), [self.h(".claude"), self.h(".claude-a")])
            self.assertIsNone(of_accounts.load())

    def test_add_makes_the_folder_keeps_the_others_and_files_one_login_task(self):
        tasks = []
        of_accounts.add("work", home=self.home, logged_in=False,
                        ask=lambda t, s: tasks.append((t, s)) or (True, "id1"), log=lambda m: None)
        d = self.h(".claude-work")
        self.assertTrue(os.path.isdir(d))
        self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)
        self.assertEqual([e["name"] for e in of_accounts.load()], ["default", "a", "work"])
        self.assertEqual(of_capacity.config_dirs(self.home), [self.h(".claude"), self.h(".claude-a"), d])
        self.assertEqual(len(tasks), 1)
        self.assertIn("work", tasks[0][0])
        self.assertIn("of claude --account work --dangerously-skip-permissions", tasks[0][1])
        of_accounts.add("ready", home=self.home, logged_in=True, ask=lambda t, s: tasks.append(t), log=lambda m: None)
        self.assertEqual(len(tasks), 1)  # logged in already: no task
        with self.assertRaises(of_accounts.AccountError):
            of_accounts.add("work", "~/elsewhere", home=self.home, logged_in=True, log=lambda m: None)
        with self.assertRaises(of_accounts.AccountError):
            of_accounts.add("other", "~/.claude-a", home=self.home, logged_in=True, log=lambda m: None)
        with self.assertRaises(of_accounts.AccountError):
            of_accounts.add("Bad.Name", home=self.home, logged_in=True, log=lambda m: None)

    def test_a_task_that_could_not_be_filed_is_never_reported_as_filed(self):
        """of_platform's review of PR 22: on a machine with no of_capacity position, `of ask --from of_capacity`
        failed, and add reported the task anyway. Now the steps are printed and add fails."""
        for ask in (lambda t, s: (False, "of: no position 'of_capacity'"),
                    lambda t, s: (_ for _ in ()).throw(RuntimeError("relay down"))):
            lines = []
            with self.assertRaises(of_accounts.AccountError):
                of_accounts.add("work", home=self.home, logged_in=False, ask=ask, log=lines.append)
            self.assertIn("could not be filed", lines[-1])
            self.assertIn("of claude --account work --dangerously-skip-permissions", lines[-1])  # the steps themselves
        self.assertIn("work", [e["name"] for e in of_accounts.load()])  # the folder is made and listed all the same

    def test_the_task_is_filed_as_the_caller(self):
        calls = []
        run = lambda argv, **kw: calls.append(argv) or type("R", (), {"returncode": 1, "stdout": "", "stderr": "of: x"})()  # noqa: E731
        self.assertEqual(of_accounts._file_task("t", "s", of_bin="of", run=run), (False, "of: x"))
        self.assertNotIn("--from", calls[0])

    def test_remove_stops_using_it_and_keeps_the_folder(self):
        of_accounts.remove("a", home=self.home, log=lambda m: None)
        self.assertEqual(of_capacity.config_dirs(self.home), [self.h(".claude")])
        self.assertTrue(os.path.isdir(self.h(".claude-a")))
        with self.assertRaises(of_accounts.AccountError):
            of_accounts.remove("nope", home=self.home, log=lambda m: None)

    def test_the_login_task_in_the_owners_language(self):
        en = of_accounts.login_task("work", "~/.claude-work", "en")
        ru = of_accounts.login_task("work", "~/.claude-work", "ru")
        cyr = re.compile("[\\u0400-\\u04ff]")
        for a, b in zip(en, ru):
            self.assertIsNone(cyr.search(a))
            self.assertIsNotNone(cyr.search(b))
            self.assertNotIn("{", a + b)


class OfClaude(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        for d in (".claude", ".claude-a"):
            os.makedirs(os.path.join(self.home, d))
        self.saved = of_events.CONFIG_DIR
        of_events.CONFIG_DIR = os.path.join(self.tmp.name, "config")
        self.kw = dict(home=self.home, chooser=lambda cwd, home: os.path.join(self.home, ".claude-a"),
                       resumer=lambda sid: None, holder=lambda sid, home: None)

    def tearDown(self):
        of_events.CONFIG_DIR = self.saved
        self.tmp.cleanup()

    def test_an_account_by_name(self):
        argv, env = of_accounts.launch(["--account", "a", "-c"], env={}, **self.kw)
        self.assertEqual(argv[1:], ["-c"])
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], os.path.join(self.home, ".claude-a"))
        argv, env = of_accounts.launch(["--account=default"], env={"CLAUDE_CONFIG_DIR": "/x"}, **self.kw)
        self.assertNotIn("CLAUDE_CONFIG_DIR", env)  # the default account runs with the variable unset
        self.assertEqual(argv[1:], [])
        with self.assertRaises(of_accounts.AccountError):
            of_accounts.launch(["--account", "nope"], env={}, **self.kw)

    def test_a_resume_runs_the_sessions_own_line_and_otherwise_the_choice(self):
        kw = dict(self.kw, resumer=lambda sid: f"env CLAUDE_CONFIG_DIR=/h/.claude-a claude --resume {sid} --model m")
        argv, _ = of_accounts.launch(["--resume", "s1"], env={}, **kw)
        self.assertEqual(argv, ["/bin/sh", "-c", "exec env CLAUDE_CONFIG_DIR=/h/.claude-a claude --resume s1 --model m"])
        argv, env = of_accounts.launch(["--resume", "s1"], env={}, **self.kw)  # no account holds it: the chooser
        self.assertEqual(argv[1:], ["--resume", "s1"])
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], os.path.join(self.home, ".claude-a"))

    def test_a_resume_without_its_line_still_goes_to_the_account_that_holds_it(self):
        """of_herdr's lab (hand-over a9c118e8): bin/of failed under a python3 without PyYAML, the line came back None,
        and the resume went to the account the chooser picked: 'No conversation found'."""
        held = os.path.join(self.home, ".claude")
        argv, env = of_accounts.launch(["--resume", "s1"], env={}, **dict(self.kw, holder=lambda sid, home: held))
        self.assertEqual(argv[1:], ["--resume", "s1"])
        self.assertNotIn("CLAUDE_CONFIG_DIR", env)  # ~/.claude holds it: the default account, variable unset
        calls = []
        run = lambda argv, **kw: calls.append(argv) or type("R", (), {"returncode": 0, "stdout": "env x claude", "stderr": ""})()  # noqa: E731
        self.assertEqual(of_accounts.resume_line("s1", of_bin="/of", run=run), "env x claude")
        self.assertEqual(calls[0][:2], [sys.executable, "/of"])  # this Python, never PATH's python3

    def test_account_is_ours_only_as_the_first_argument(self):
        argv, env = of_accounts.launch(["-p", "--account", "a"], env={}, **self.kw)  # Claude Code's own arguments
        self.assertEqual(argv[1:], ["-p", "--account", "a"])

    def test_a_set_config_dir_is_kept_without_account(self):
        argv, env = of_accounts.launch(["-p", "hi"], env={"CLAUDE_CONFIG_DIR": "/elsewhere"}, **self.kw)
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/elsewhere")
        self.assertEqual(argv[1:], ["-p", "hi"])


if __name__ == "__main__":
    unittest.main()
