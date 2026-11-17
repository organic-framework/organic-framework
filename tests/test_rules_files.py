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
        self.root = os.path.join(self.tmp.name, "org-repo")
        self.work = os.path.join(self.tmp.name, "work")
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
            out = of_context.write_rules(self.positions, {"acme": [self.work]}, log=lambda m: None)
            self.assertEqual(out, [os.path.join(self.work, ".claude", "rules", "of-acme.md")])
            text = open(out[0]).read()
            self.assertIn("- **Commit locally.** Never push.", text)          # whole, the binding section only
            self.assertNotIn("not binding", text)
            self.assertEqual(of_context.write_rules(self.positions, {"acme": [self.work]}, log=lambda m: None), [])
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
            of_context.write_rules(self.positions, {"acme": [self.work]}, log=lambda m: None)
            ctx = of_context.build("a_keeper", self.positions, cwd=self.work)
            self.assertNotIn("Commit locally", ctx)
            self.assertIn("reach you whole from " + os.path.join(self.work, ".claude", "rules", "of-acme.md"), ctx)
        finally:
            of_spawn.work_dir = saved

    def test_registry_check_names_a_position_the_file_does_not_reach(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("of_cli_rules", os.path.join(HERE, "..", "bin", "of"))
        of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli_rules", loader))
        loader.exec_module(of)
        warn = of.check_rules_files(self.positions, {"acme": [self.work]}, work_dir=self.wd)
        self.assertEqual(len(warn), 1)
        self.assertIn("a_keeper", warn[0])
        self.assertIn("run of apply", warn[0])


if __name__ == "__main__":
    unittest.main()
