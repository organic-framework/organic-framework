"""The laptop's live checkout (bin/of_live.py; hand-over cb134c52)."""
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import of_init  # noqa: E402
import of_live  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def settings(d, commands):
    os.makedirs(d, exist_ok=True)
    hooks = {}
    for c in commands:
        ev = c.split()[2]
        hooks.setdefault(ev, []).append({"hooks": [{"type": "command", "command": c}]})
    with open(os.path.join(d, "settings.json"), "w") as fh:
        json.dump({"hooks": hooks}, fh)


class Accounts(unittest.TestCase):
    def test_each_hooked_account_keeps_its_flags_and_others_are_left(self):
        with tempfile.TemporaryDirectory() as t:
            a, b, c = (os.path.join(t, n) for n in ("a", "b", "c"))
            settings(a, ["py /wc/bin/of-hook PreToolUse", "py /wc/bin/of-hook UserPromptSubmit --deny-secrets"])
            settings(b, ["py /wc/bin/of-hook Stop"])
            settings(c, ["echo other Stop"])
            got = dict(of_live.hooked_accounts([a, b, c, os.path.join(t, "none")]))
            self.assertEqual(got, {a: ["--replace", "/wc/bin/of", "--guard", "--deny-secrets"],
                                   b: ["--replace", "/wc/bin/of"]})

    def test_a_switch_runs_lives_own_of_for_every_account_then_the_units(self):
        plan = of_live.switch_plan("/py", [("/acc", ["--guard"])], live="/L")
        self.assertEqual(plan[0], ["/py", "/L/bin/of", "hooks", "install", "--config-dir", "/acc", "--yes", "--guard"])
        self.assertEqual([c[2] for c in plan[1:]], ["listen", "relay", "server", "health"])
        self.assertTrue(all(c[1] == "/L/bin/of" for c in plan))

    def test_hooks_installed_from_live_name_live_not_the_working_copy(self):
        """The outage's replay: the hook command an install writes runs the checkout it was run from, so a hook
        installed from LIVE never runs what a session leaves half-applied in the working copy."""
        cmd = of_init.hook_command("PreToolUse", of_path="/L/bin/of")
        self.assertIn("/L/bin/of-hook", cmd)
        self.assertNotIn(ROOT, cmd)


class Switch(unittest.TestCase):
    def test_a_switch_leaves_no_hook_of_the_working_copy(self):
        """of_secrets-review on PR 629: the live hooks were added after the working copy's, and both ran."""
        old = {"hooks": {"PreToolUse": [{"matcher": of_init.GUARD_MATCHER, "hooks": [
            {"type": "command", "command": "/py /wc/bin/of-hook PreToolUse"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "/py /wc/bin/of-hook Stop"},
                                {"type": "command", "command": "echo mine"}]}]}}
        new = of_init.with_hooks(old, "/L/bin/of", guard=True, replace=["/wc/bin/of"])
        cmds = [h["command"] for gs in new["hooks"].values() for g in gs for h in g["hooks"]]
        self.assertFalse([c for c in cmds if "/wc/" in c])
        self.assertIn("echo mine", cmds)
        self.assertTrue([c for c in cmds if "/L/bin/of-hook PreToolUse" in c])

    def test_the_first_failed_command_stops_the_switch(self):
        ran = []

        def run(cmd):
            ran.append(cmd)
            return subprocess.CompletedProcess(cmd, 1 if len(ran) == 2 else 0)

        with mock.patch.object(of_live, "check_live"), mock.patch.object(of_live.os, "replace") as moved:
            with self.assertRaises(of_live.LiveError):
                of_live.switch("/py", run=run, live="/L", accounts=[("/a", []), ("/b", []), ("/c", [])], log=lambda m: 0)
        self.assertEqual(len(ran), 2)
        moved.assert_not_called()          # ~/.local/bin/of is left as it was


class Script(unittest.TestCase):
    SCRIPT = os.path.join(ROOT, "deploy", "laptop", "of-checkout-update.sh")

    def check(self, live):
        env = dict(os.environ, OF_PYTHON=sys.executable, HOME=os.path.dirname(live), GIT_CONFIG_GLOBAL="/dev/null",
                   GIT_CONFIG_NOSYSTEM="1")
        return subprocess.run(["bash", "-c", 'source "$1"; LIVE="$2"; check_checkout', "check", self.SCRIPT, live],
                              capture_output=True, text=True, env=env)

    def test_a_clone_made_by_git_here_passes_the_config_check(self):
        """of_secrets-review on PR 629: git on macOS writes core.ignorecase and core.precomposeunicode into any
        clone, and the check refused them, so the live checkout never moved."""
        with tempfile.TemporaryDirectory() as t:
            live = os.path.join(t, "live")
            g = ["git", "-c", "init.defaultBranch=main"]
            subprocess.run(g + ["init", "-q", live], check=True)
            subprocess.run(["git", "-C", live, "remote", "add", "origin", of_live.URL], check=True)
            subprocess.run(["git", "-C", live, "config", "branch.main.remote", "origin"], check=True)
            subprocess.run(["git", "-C", live, "config", "branch.main.merge", "refs/heads/main"], check=True)
            r = self.check(live)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_refused_key_is_logged_by_its_section_alone(self):
        with tempfile.TemporaryDirectory() as t:
            live = os.path.join(t, "live")
            subprocess.run(["git", "init", "-q", live], check=True)
            subprocess.run(["git", "-C", live, "remote", "add", "origin", of_live.URL], check=True)
            subprocess.run(["git", "-C", live, "config", "url.https://u:" + "s3cr3t@h/.insteadof", "x"], check=True)
            r = self.check(live)
            self.assertNotEqual(r.returncode, 0)
            self.assertNotIn("s3cr3t", r.stdout + r.stderr)
            self.assertIn("section url", r.stdout)

    def test_the_tree_is_checked_before_a_restart_at_the_tip(self):
        text = open(self.SCRIPT).read()
        self.assertLess(text.index("private diff-files --quiet"), text.index('if [ "$old" = "$tip" ]'))

    def test_the_private_store_lies_in_the_kept_checkout(self):
        self.assertIn('mktemp -d "$LIVE/.git/of-update.', open(self.SCRIPT).read())


class Agent(unittest.TestCase):
    def test_the_agent_runs_on_a_request_only_with_no_shell(self):
        self.assertEqual(plistlib.loads(of_live.agent_plist("/py", live="/L"))["ProgramArguments"],
                         ["/bin/bash", "/L/deploy/laptop/of-checkout-update.sh"])
        p = plistlib.loads(of_live.agent_plist("/py", script="/s", request="/r"))
        self.assertEqual(p["ProgramArguments"], ["/bin/bash", "/s"])
        self.assertEqual(p["WatchPaths"], ["/r"])
        self.assertFalse(p["RunAtLoad"])
        self.assertEqual(p["EnvironmentVariables"]["OF_AUTOMATION"], "1")
        self.assertEqual(p["EnvironmentVariables"]["OF_PYTHON"], "/py")

    def test_the_update_script_is_bash_clean_and_moves_only_through_of_protected_ff(self):
        s = os.path.join(ROOT, "deploy", "laptop", "of-checkout-update.sh")
        self.assertEqual(subprocess.run(["bash", "-n", s]).returncode, 0)
        text = open(s).read()
        self.assertIn("protected ff", text)
        self.assertIn("LIVE=$HOME/.local/share/of/live", text)
        for linux in ("systemctl", "journalctl", "stat -c"):
            self.assertNotIn(linux, text)

    def test_the_unexpanded_folder_is_the_guards(self):
        import of_guard
        self.assertEqual(of_live.LIVE_DIR, of_guard.LIVE_DIR)

    def test_a_live_folder_that_is_no_checkout_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(of_live.LiveError):
                of_live.check_live(t)


if __name__ == "__main__":
    unittest.main()
