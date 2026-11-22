"""The binding rules reach every position whole through a rules file Claude Code loads itself (hand-over 83b08e7b from
of_architect): written by of apply into the folders the positions run in, kept out of git, named by the start context
instead of the rules, and checked by of registry check."""

import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_context  # noqa: E402

AGENTS = "# Org\n\n## Rules\n\n- **Commit locally.** Never push.\n- Ask the owner with of ask.\n\n## Other\nnot binding\n"


class RulesFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = os.path.realpath(self.tmp.name)   # /var is /private/var on macOS: the paths as the files resolve
        self.root = os.path.join(base, "org-repo")
        self.work = os.path.join(base, "work")
        os.makedirs(self.root)
        os.makedirs(self.work)
        with open(os.path.join(self.root, "AGENTS.md"), "w") as fh:
            fh.write(AGENTS)
        subprocess.run(["git", "-c", "commit.gpgsign=false", "init", "-q", self.work], check=True,
                       env=dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1"))
        self.positions = [{"name": "a_keeper", "_org": "acme", "_orgid": "acme", "_root": self.root, "status": "active",
                           "enrolled": True, "system": "s", "_systems": {}},
                          {"name": "alex", "_org": "acme", "_orgid": "acme", "_root": self.root,
                           "holder": {"runtime": "human"}}]
        self.wd = lambda p, systems, paths: self.work

    def test_written_whole_once_kept_out_of_git_and_named_by_the_context(self):
        import of_spawn
        saved = of_spawn.work_dir
        of_spawn.work_dir = self.wd
        try:
            out = of_context.write_rules(self.positions, {"acme": [self.work]}, log=lambda m: None, sessions={})
            self.assertEqual(out, [os.path.join(self.work, ".claude", "rules", "of-acme.md")])
            text = open(out[0]).read()
            self.assertIn("- **Commit locally.** Never push.", text)          # whole, the binding section only
            self.assertNotIn("not binding", text)
            self.assertIn("bind the positions of `acme`", text)                    # whom it binds (of_review, PR 320)
            self.assertIn("a session of another organization ignores this file", text)
            self.assertEqual(of_context.write_rules(self.positions, {"acme": [self.work]}, log=lambda m: None, sessions={}), [])
            exclude = open(os.path.join(self.work, ".git", "info", "exclude")).read()
            self.assertIn("/.claude/rules/of-*.md", exclude)                     # never committed there
            status = subprocess.run(["git", "-C", self.work, "status", "--porcelain"], capture_output=True, text=True,
                                    env=dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")).stdout
            self.assertEqual(status, "")
            self.assertEqual(of_context.rules_loaded_from(self.work, "acme", os.path.join(self.root, "AGENTS.md")),
                             out[0])
            with open(os.path.join(self.root, "AGENTS.md"), "a") as fh:           # the rules changed: stale
                fh.write("\n## Rules for money\n- Ask first.\n")
            self.assertIsNone(of_context.rules_loaded_from(self.work, "acme", os.path.join(self.root, "AGENTS.md")))
        finally:
            of_spawn.work_dir = saved

    def test_the_start_context_names_the_file_instead_of_the_rules(self):
        import of_spawn
        saved = of_spawn.work_dir
        of_spawn.work_dir = self.wd
        try:
            ctx = of_context.build("a_keeper", self.positions, cwd=self.work)
            self.assertIn("Commit locally", ctx)                                  # no file yet: the rules inline
            of_context.write_rules(self.positions, {"acme": [self.work]}, log=lambda m: None, sessions={})
            ctx = of_context.build("a_keeper", self.positions, cwd=self.work)
            self.assertNotIn("Commit locally", ctx)
            self.assertIn("reach you whole from " + os.path.join(self.work, ".claude", "rules", "of-acme.md"), ctx)
        finally:
            of_spawn.work_dir = saved

    def test_a_folder_two_organizations_run_in_gets_no_rules_file(self):
        """of_review and of_architect on organic-framework PR 320: Claude Code loads every rules file of a folder into
        every session there, and a user organization's sessions ran in the framework's folder beside the OF team's."""
        import of_spawn
        saved = of_spawn.work_dir
        of_spawn.work_dir = self.wd
        try:
            old = os.path.join(self.work, ".claude", "rules", "of-acme.md")
            of_context.write_rules(self.positions, {"acme": [self.work]}, log=lambda m: None, sessions={})
            self.assertTrue(os.path.exists(old))
            other = {os.path.realpath(self.work): {"globex"}}                     # another organization's session
            out = of_context.write_rules(self.positions, {"acme": [self.work]}, log=lambda m: None, sessions=other)
            self.assertEqual(out, [])
            self.assertFalse(os.path.exists(old))                                 # the earlier one removed
            import importlib.machinery
            import importlib.util
            loader = importlib.machinery.SourceFileLoader("of_cli_rules2", os.path.join(HERE, "..", "bin", "of"))
            of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli_rules2", loader))
            loader.exec_module(of)
            warn = of.check_rules_files(self.positions, {"acme": [self.work]}, work_dir=self.wd, sessions=other)
            self.assertIn("is shared by acme, globex", warn[0])
        finally:
            of_spawn.work_dir = saved

    def test_a_session_counts_only_where_it_last_started(self):
        """2026-11-22 (a user organization's structure keeper, message 01a204ff-0f63): a position that changed organization was restarted in the old
        one's folder at 19:56Z and out of it at 20:39Z; its earlier start kept the folder shared for the whole window,
        so that organization's rules file stayed removed though no session of the other ran there."""
        import sqlite3
        import time as _t
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE event (name TEXT, ts TEXT, session_id TEXT, org TEXT, cwd TEXT)")
        now = _t.time()
        iso = lambda s: _t.strftime("%Y-%m-%dT%H:%M:%S.000Z", _t.gmtime(now - s))  # noqa: E731
        conn.executemany("INSERT INTO event VALUES ('of.session_start', ?, ?, ?, ?)", [
            (iso(7200), "S1", "beta", "/w/alpha"),       # restarted in the other organization's folder ...
            (iso(3600), "S1", "beta", "/w/beta"),        # ... and out of it
            (iso(3600), "S2", "alpha", "/w/alpha"),
            (iso(60), None, "alpha", "/w/alpha/sub")])  # a start with no session id still counts
        got = of_context.session_orgs(conn)
        self.assertEqual(got[os.path.realpath("/w/alpha")], {"alpha"})
        self.assertEqual(got[os.path.realpath("/w/beta")], {"beta"})
        self.assertEqual(of_context.shared_folders({"alpha": ["/w/alpha"]}, got), {})

    def test_a_folder_below_another_of_the_same_organization_gets_none(self):
        """Claude Code loads a parent folder's rules too (tested on 2.1.326): a repository under the organization's
        folder would load the rules twice."""
        inner = os.path.join(self.work, "repo")
        os.makedirs(inner)
        two = [dict(self.positions[0], name="b_keeper")] + self.positions
        wd = {"a_keeper": self.work, "b_keeper": inner}
        import of_spawn
        saved = of_spawn.work_dir
        of_spawn.work_dir = lambda p, systems, paths: wd[p["name"]]
        try:
            os.makedirs(os.path.join(inner, ".claude", "rules"))
            stale = os.path.join(inner, ".claude", "rules", "of-acme.md")
            open(stale, "w").close()
            out = of_context.write_rules(two, {"acme": [self.work]}, log=lambda m: None, sessions={})
            self.assertEqual(out, [os.path.join(self.work, ".claude", "rules", "of-acme.md")])
            self.assertFalse(os.path.exists(stale))                               # once, from the folder above
            self.assertEqual(of_context.rules_loaded_from(inner, "acme", os.path.join(self.root, "AGENTS.md")), out[0])
        finally:
            of_spawn.work_dir = saved

    def test_registry_check_names_a_position_the_file_does_not_reach(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("of_cli_rules", os.path.join(HERE, "..", "bin", "of"))
        of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli_rules", loader))
        loader.exec_module(of)
        warn = of.check_rules_files(self.positions, {"acme": [self.work]}, work_dir=self.wd, sessions={})
        self.assertEqual(len(warn), 1)
        self.assertIn("a_keeper", warn[0])
        self.assertIn("run of apply", warn[0])


if __name__ == "__main__":
    unittest.main()
