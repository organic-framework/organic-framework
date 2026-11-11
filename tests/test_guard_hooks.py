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

    def test_an_alias_is_read_as_what_it_runs(self):
        """of_architect's second review of PR 6: git runs an alias for a word it does not have."""
        for cmd in ("git -c alias.ci='commit --no-verify' ci -m x",
                    "git config alias.ci 'commit --no-verify' && git ci -m x",
                    "git -c alias.s='!git commit --no-verify -m x' s",
                    "git frobnicate -m x"):                        # neither a built-in nor a readable alias
            self.assertTrue(self.deny(cmd), cmd)
        with open(os.path.join(self.gated, ".git", "config"), "w") as fh:
            fh.write("[alias]\n\tci = commit --no-verify\n\tl = log --oneline\n")
        self.assertTrue(self.deny("git ci -m x"))                    # an alias already in the repository's config
        self.assertFalse(self.deny("git l -3"))
        self.assertFalse(self.deny("git frobnicate -m x", cwd=self.other))

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

    def test_herdrs_plugin_folders_and_acts(self):
        """of_platform's review of PR 24: herdr runs the plugin as the owner; of_home in its config folder decides
        what run.sh starts. Writes there are denied, and so are herdr plugin link and the like, but in of_herdr's
        isolated lab herdr (its XDG_CONFIG_HOME under ~/.cache/of-herdr/)."""
        saved = of_guard.HERDR_PLUGIN_DIRS
        of_guard.HERDR_PLUGIN_DIRS = (os.path.join(self.tmp.name, "herdr-plugins"),)
        of_guard._PLUGIN_DIRS.clear()
        try:
            home = os.path.join(self.tmp.name, "herdr-plugins", "config", "organic-framework", "of_home")
            self.assertTrue(self.deny("Bash", {"command": f"echo /tmp/evil > {home}"}))
            self.assertTrue(self.deny("Write", {"file_path": home, "content": "/tmp/evil"}))
            self.assertFalse(self.deny("Bash", {"command": f"cat {home}"}))
            lab = os.path.expanduser("~/.cache/of-herdr/lab1/xdg")
            owner_sock = "/Users/alex/.config/herdr/herdr.sock"
            saved_env = {k: os.environ.get(k) for k in ("HERDR_SOCKET_PATH", "XDG_CONFIG_HOME")}
            try:
                os.environ["HERDR_SOCKET_PATH"] = owner_sock      # an agent's pane inherits the owner's socket
                os.environ.pop("XDG_CONFIG_HOME", None)
                for cmd in ("herdr plugin link /tmp/x", "XDG_CONFIG_HOME=~/.config herdr plugin unlink of",
                            f"XDG_CONFIG_HOME={lab} herdr plugin link /tmp/x",   # the lab's config, the owner's herdr
                            f"export XDG_CONFIG_HOME={lab}; herdr plugin unlink of"):
                    self.assertTrue(self.deny("Bash", {"command": cmd}), cmd)
                for cmd in (f"HERDR_SOCKET_PATH={lab}/herdr.sock herdr plugin link /tmp/x",
                            f"unset HERDR_SOCKET_PATH; export XDG_CONFIG_HOME={lab}; herdr plugin unlink of",
                            f"HERDR_SOCKET_PATH= XDG_CONFIG_HOME={lab} herdr plugin link /tmp/x",
                            f"env -u HERDR_SOCKET_PATH XDG_CONFIG_HOME={lab} herdr plugin link /tmp/x",
                            "herdr plugin list"):
                    self.assertFalse(self.deny("Bash", {"command": cmd}), cmd)
            finally:
                for k, v in saved_env.items():
                    os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        finally:
            of_guard.HERDR_PLUGIN_DIRS = saved
            of_guard._PLUGIN_DIRS.clear()

    def test_the_development_copys_plugins_are_source(self):
        target = os.path.join(self.dev, "bin", "plugins", "secrets", "vault.py")
        self.assertFalse(self.deny("Write", {"file_path": target, "content": "x"}))
        self.assertFalse(self.deny("Bash", {"command": f"echo x > {target}"}))


if __name__ == "__main__":
    unittest.main()
