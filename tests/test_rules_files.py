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
            # another organization's session that only started here takes nothing away; it is named as a stray
            # (of_architect, message 01a204f7-ef0e: one such restart removed a user organization's rules file)
            other = {os.path.realpath(self.work): {"globex"}}
            said = []
            of_context.write_rules(self.positions, {"acme": [self.work]}, log=said.append, sessions=other)
            self.assertTrue(os.path.exists(old))
            self.assertTrue(any("a session of globex started in" in x for x in said))
            # two organizations' registries put positions in one folder: none there, and the earlier one removed
            two = self.positions + [dict(self.positions[0], name="g_keeper", _org="globex", _orgid="globex")]
            out = of_context.write_rules(two, {"acme": [self.work], "globex": [self.work]}, log=lambda m: None,
                                         sessions={})
            self.assertEqual(out, [])
            self.assertFalse(os.path.exists(old))
            import importlib.machinery
            import importlib.util
            loader = importlib.machinery.SourceFileLoader("of_cli_rules2", os.path.join(HERE, "..", "bin", "of"))
            of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli_rules2", loader))
            loader.exec_module(of)
            warn = of.check_rules_files(two, {"acme": [self.work], "globex": [self.work]}, work_dir=self.wd,
                                        sessions={}, starts={})
            self.assertIn("is shared by acme, globex", warn[0])
            self.assertFalse(warn[0].startswith(of.WARN))                          # enrolled: a failure
        finally:
            of_spawn.work_dir = saved

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
        warn = of.check_rules_files(self.positions, {"acme": [self.work]}, work_dir=self.wd, sessions={}, starts={})
        self.assertEqual(len(warn), 1)
        self.assertIn("a_keeper", warn[0])
        self.assertIn("run of apply", warn[0])
        self.assertFalse(warn[0].startswith(of.WARN))     # an enrolled position its rules do not reach: a failure
        # the folder its last session started in is checked too, and named
        other = os.path.join(os.path.dirname(self.work), "elsewhere")
        warn = of.check_rules_files(self.positions, {"acme": [self.work]}, work_dir=self.wd, sessions={},
                                    starts={("acme", "a_keeper"): other})
        self.assertTrue(any(f"its last session started in {other}" in x for x in warn))

    def test_nested_registry_folders(self):
        """of_review on PR 564: a folder above another organization's registry folder is shared (its sessions below
        would load both rules files); the nearest registry folder decides who is foreign; one position name in two
        organizations is two rows."""
        up, low = "/w/alpha", "/w/alpha/beta"
        folders = {"alpha": [up], "beta": [low]}
        self.assertEqual(of_context.shared_folders(folders), {up: {"alpha", "beta"}})
        self.assertEqual(of_context.foreign_orgs("beta", low, folders), set())         # its own folder, below another's
        self.assertEqual(of_context.foreign_orgs("alpha", up + "/x", folders), set())
        self.assertEqual(of_context.foreign_orgs("beta", up + "/x", folders), {"alpha"})
        both = {"alpha": [up], "beta": [up]}
        self.assertEqual(of_context.foreign_orgs("alpha", up, both), set())             # both named there
        self.assertEqual(of_context.foreign_orgs("beta", up, both), set())
        self.assertEqual(of_context.stray_sessions(folders, {low: {"beta"}, up: {"beta"}}), {up: {"beta"}})
        import sqlite3
        import time as _t
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE event (name TEXT, ts TEXT, org TEXT, position TEXT, cwd TEXT)")
        iso = _t.strftime("%Y-%m-%dT%H:%M:%S.000Z", _t.gmtime())
        conn.executemany("INSERT INTO event VALUES ('of.session_start', ?, ?, 'keeper', ?)",
                         [(iso, "alpha", up), (iso, "beta", low)])
        self.assertEqual(of_context.position_starts(conn), {("alpha", "keeper"): up, ("beta", "keeper"): low})

    def test_spawn_refuses_another_organizations_folder(self):
        """of_architect, message 01a204f7-ef0e: a session of one organization must not start where another's
        positions run, where it would load their rules."""
        import of_spawn
        folders = {"acme": [self.work]}
        self.assertEqual(of_context.foreign_orgs("globex", os.path.join(self.work, "sub"), folders), {"acme"})
        self.assertEqual(of_context.foreign_orgs("acme", self.work, folders), set())
        p = {"name": "g_keeper", "_org": "globex", "status": "active"}
        with self.assertRaisesRegex(of_spawn.SpawnError, "a folder of acme's positions"):
            of_spawn.plan(p, [p], [], lambda ps, ag: ([], []), {}, [], "", lambda q: "w", cwd=self.work,
                          config_dir="/nonexistent", folders=folders)

    def test_the_start_context_says_loudly_when_the_rules_are_cut(self):
        long_rules = "# Org\n\n## Rules\n\n" + "".join(f"- Rule {i}: " + "x" * 200 + "\n" for i in range(80))
        with open(os.path.join(self.root, "AGENTS.md"), "w") as fh:
            fh.write(long_rules)
        text = of_context.build("a_keeper", self.positions, cwd=self.work)
        self.assertTrue(text.startswith("OF WARNING: your organization's binding rules do not reach this session"))
        self.assertIn(os.path.join(self.root, "AGENTS.md"), text.split("\n")[0])
        # with the rules file loaded, the section is a pointer: a cut of it is no warning
        import of_spawn
        saved = of_spawn.work_dir
        of_spawn.work_dir = self.wd
        try:
            of_context.write_rules(self.positions, {"acme": [self.work]}, log=lambda m: None, sessions={})
        finally:
            of_spawn.work_dir = saved
        info = {}
        text = of_context.build("a_keeper", self.positions, cwd=self.work, cap=400, info=info)
        self.assertFalse(text.startswith("OF WARNING"))


if __name__ == "__main__":
    unittest.main()
