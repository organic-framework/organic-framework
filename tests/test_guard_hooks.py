"""The release gate's hooks (docs/guard.md G17): in a repository whose pre-commit hook is the gate, no agent skips
the hooks or moves them; in any other repository nothing changes. No git runs here: the repositories are folders."""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_guard  # noqa: E402


class HooksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.gated = os.path.join(self.tmp.name, "framework")
        self.other = os.path.join(self.tmp.name, "other")
        for repo, hook in ((self.gated, "#!/usr/bin/env bash\n# Installed by organic-framework-build "
                                        "release/install-hooks.sh: the gate.\nexec gate\n"),
                           (self.other, "#!/bin/sh\nexec lint\n")):
            os.makedirs(os.path.join(repo, ".git", "hooks"))
            os.makedirs(os.path.join(repo, "docs"))
            with open(os.path.join(repo, ".git", "hooks", "pre-commit"), "w") as fh:
                fh.write(hook)
        of_guard._GATED.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def deny(self, cmd, cwd=None):
        hit = of_guard.critical_deny("Bash", {"command": cmd}, cwd or self.gated)
        return bool(hit) and "release gate" in hit[0]

    def test_skipping_the_hooks_is_denied_in_a_gated_repository(self):
        for cmd in ("git commit --no-verify -m x", "git commit -q --no-verify", "git commit -nm x", "git commit -an",
                    "git push --no-verify origin main", "git merge --no-verify topic",
                    "git -c core.hooksPath=/dev/null commit -m x", "git config core.hooksPath /tmp/h",
                    "git config --unset core.hooksPath", "git config core.hooksPath=/tmp/h"):
            self.assertTrue(self.deny(cmd), cmd)

    def test_what_skips_nothing_stays_allowed(self):
        for cmd in ("git commit -m 'never use --no-verify'", "git commit -mn", "git commit -am 'a note -n'",
                    "git commit -F msg.txt", "git push -n origin main", "git config core.hooksPath",
                    "git config --get core.hooksPath", "cat .git/hooks/pre-commit", "git log -n 3",
                    "git -C docs commit -m x"):
            self.assertFalse(self.deny(cmd), cmd)

    def test_another_repository_keeps_its_own_hook_rules(self):
        self.assertFalse(self.deny("git commit --no-verify -m x", cwd=self.other))
        self.assertFalse(self.deny("rm .git/hooks/pre-commit", cwd=self.other))

    def test_the_repository_is_where_the_command_runs(self):
        self.assertTrue(self.deny(f"cd {self.gated} && git commit -n -m x", cwd=self.other))
        self.assertTrue(self.deny(f"git -C {self.gated} commit --no-verify", cwd=self.other))
        self.assertTrue(self.deny("cd docs && git commit --no-verify -m x"))           # a folder inside it
        self.assertFalse(self.deny(f"cd {self.other} && git commit --no-verify -m x"))

    def test_the_hook_files_are_not_changed(self):
        for cmd in ("rm .git/hooks/pre-commit", "chmod -x .git/hooks/pre-commit", "echo exit 0 > .git/hooks/pre-commit",
                    f"cp /tmp/x {self.gated}/.git/hooks/commit-msg", "mv .git/hooks/pre-commit /tmp/"):
            self.assertTrue(self.deny(cmd, cwd=self.gated), cmd)
        hit = of_guard.critical_deny("Write", {"file_path": os.path.join(self.gated, ".git", "hooks", "pre-commit"),
                                               "content": "exit 0"}, self.gated)
        self.assertIn("release gate", hit[0])
        self.assertIsNone(of_guard.critical_deny("Write", {"file_path": os.path.join(self.other, ".git", "hooks",
                                                                                      "pre-commit")}, self.other))


if __name__ == "__main__":
    unittest.main()
