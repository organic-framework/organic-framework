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

    def test_the_bypasses_found_in_review(self):
        """of_architect's review of PR 6: an abbreviation, and core.hooksPath set through the environment."""
        cfg = os.path.join(self.tmp.name, "gitconfig")
        with open(cfg, "w") as fh:
            fh.write("[core]\n\thooksPath = /tmp/none\n")
        for cmd in ("git commit --no-veri -m x", "git commit --no-verif -m x", "git push --no-veri origin main",
                    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null git commit -m x",
                    "export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=Core.HooksPath GIT_CONFIG_VALUE_0=/x; git commit -m x",
                    "GIT_CONFIG_PARAMETERS=\"'core.hooksPath'='/x'\" git commit -m x",
                    f"GIT_CONFIG_GLOBAL={cfg} git commit -m x", "GIT_CONFIG_GLOBAL=/no/such/file git commit -m x",
                    "env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/x git push"):
            self.assertTrue(self.deny(cmd), cmd)
        for cmd in ("GIT_CONFIG_GLOBAL=/dev/null git commit -m x", "GIT_CONFIG_NOSYSTEM=1 git commit -m x",
                    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=user.name GIT_CONFIG_VALUE_0=x git commit -m x"):
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


class PluginCodeTest(unittest.TestCase):
    """The code OF loads as plugins (docs/guard.md G19): an installed plugin's package and the shipped folder of an
    install are the owner's; the development copy's bin/plugins is source like the rest of bin/."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dev = os.path.join(self.tmp.name, "framework")
        self.installed = os.path.join(self.tmp.name, "site-packages", "of_vault_plugin")
        self.shipped = os.path.join(self.tmp.name, "install", "bin", "plugins")
        os.makedirs(os.path.join(self.dev, ".git", "hooks"))
        os.makedirs(os.path.join(self.dev, "bin", "plugins"))
        with open(os.path.join(self.dev, ".git", "hooks", "pre-commit"), "w") as fh:
            fh.write("# Installed by organic-framework-build release/install-hooks.sh: the gate.\n")
        for d in (self.installed, self.shipped):
            os.makedirs(d)
        import of_family
        self.saved = of_family.code_paths
        of_family.code_paths = lambda: [os.path.join(self.dev, "bin", "plugins"), self.installed, self.shipped]
        of_guard._PLUGIN_DIRS.clear()
        of_guard._GATED.clear()

    def tearDown(self):
        import of_family
        of_family.code_paths = self.saved
        of_guard._PLUGIN_DIRS.clear()
        self.tmp.cleanup()

    def deny(self, tool, ti, cwd=None):
        hit = of_guard.critical_deny(tool, ti, cwd or self.tmp.name)
        return bool(hit) and "plugins" in hit[0]

    def test_an_installed_or_shipped_plugin_is_not_written(self):
        for target in (os.path.join(self.installed, "store.py"), os.path.join(self.shipped, "secrets", "x.py")):
            self.assertTrue(self.deny("Write", {"file_path": target, "content": "x"}), target)
            self.assertTrue(self.deny("Bash", {"command": f"echo x > {target}"}), target)
            self.assertTrue(self.deny("Bash", {"command": f"rm {target}"}), target)
            self.assertFalse(self.deny("Bash", {"command": f"cat {target}"}), target)   # reading stays open
            self.assertFalse(self.deny("Read", {"file_path": target}), target)

    def test_the_development_copys_plugins_are_source(self):
        target = os.path.join(self.dev, "bin", "plugins", "secrets", "vault.py")
        self.assertFalse(self.deny("Write", {"file_path": target, "content": "x"}))
        self.assertFalse(self.deny("Bash", {"command": f"echo x > {target}"}))


if __name__ == "__main__":
    unittest.main()
