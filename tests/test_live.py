"""The laptop's live checkout (bin/of_live.py; hand-over cb134c52)."""
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest

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
            self.assertEqual(got, {a: ["--guard", "--deny-secrets"], b: []})

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


class Agent(unittest.TestCase):
    def test_the_agent_runs_on_a_request_only_with_no_shell(self):
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

    def test_a_live_folder_that_is_no_checkout_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(of_live.LiveError):
                of_live.check_live(t)


if __name__ == "__main__":
    unittest.main()
