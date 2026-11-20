"""Codex's managed requirements file as of codex requirements writes it (bin/of_codex.py; W3 of the Codex positions
plan, of_server's decision)."""

import os
import subprocess
import sys
import tomllib
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_codex  # noqa: E402


class RequirementsTest(unittest.TestCase):
    def test_it_parses_and_pins_what_w3_names(self):
        doc = tomllib.loads(of_codex.requirements_toml("/home/alex/.local/bin/of"))
        self.assertIs(doc["allow_managed_hooks_only"], True)
        self.assertEqual(doc["allowed_approval_policies"], ["never"])
        self.assertNotIn("danger-full-access", doc["allowed_sandbox_modes"])
        self.assertEqual((doc["features"]["unified_exec"], doc["features"]["unified_exec_tty"]), (False, False))
        self.assertIs(doc["network_proxy"]["dangerously_allow_all_unix_sockets"], True)
        self.assertEqual(doc["network_proxy"]["domains"]["github.com"], "allow")
        for ev in of_codex.MANAGED_HOOK_EVENTS:
            (group,) = doc["hooks"][ev]
            self.assertNotIn("matcher", group)                     # the guard sees every tool call
            (h,) = group["hooks"]
            self.assertEqual((h["type"], h["command"], h["timeout"]), ("command", f"/home/alex/.local/bin/of hook {ev}", 30))

    def test_the_command_prints_it_and_names_what_is_not_pinned(self):
        r = subprocess.run([sys.executable, os.path.join(HERE, "..", "bin", "of"), "codex", "requirements",
                            "--of-bin", "/x/of"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('command = "/x/of hook PreToolUse"', r.stdout)
        self.assertIn("/etc/codex/requirements.toml", r.stderr)
        self.assertIn("allowed_web_search_modes", r.stderr)


if __name__ == "__main__":
    unittest.main()
