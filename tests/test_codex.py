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



class LayerTest(unittest.TestCase):
    """A Codex session's layer (W3 points 2 to 5, W5, W7; hand-over 9ee1db9f)."""

    def doc(self, **kw):
        from unittest import mock
        os.makedirs(os.path.expanduser("~/.ssh"), exist_ok=True)
        with mock.patch.object(of_codex, "seat_homes", return_value=["/home/alex/.codex", "/home/alex/.codex-b"]):
            return of_codex.layer({"name": "of_docs"}, "/home/alex/wt/of_docs", model="gpt-x",
                                  instructions='# You hold `of_docs`\nRules: say "no".\n',
                                  tool_tables={"features": {"apps": False, "unified_exec": True},
                                               "web_search": "disabled",
                                               "plugins": {"_default": {"enabled": False},
                                                           "review@openai-curated": {"enabled": True}}},
                                  checkout_bin="/home/alex/organic-framework/bin", **kw)

    def test_it_round_trips_and_holds_w3(self):
        d = tomllib.loads(of_codex.to_toml(self.doc()))
        self.assertEqual((d["approval_policy"], d["sandbox_mode"], d["default_permissions"]),
                         ("never", "workspace-write", of_codex.PROFILE))
        self.assertEqual(d["developer_instructions"], '# You hold `of_docs`\nRules: say "no".\n')
        self.assertIs(d["features"]["unified_exec"], False)              # a tool table never turns it back on
        self.assertIs(d["features"]["apps"], False)
        self.assertEqual(d["plugins"]["review@openai-curated"], {"enabled": True})   # a quoted key
        self.assertEqual(d["shell_environment_policy"]["inherit"], "all")
        self.assertNotIn("SSH_AUTH_SOCK", d["shell_environment_policy"]["include_only"])
        prof = d["permissions"][of_codex.PROFILE]
        fs = prof["filesystem"]
        self.assertEqual(fs[":minimal"], "read")
        self.assertEqual(fs["/home/alex/organic-framework/bin"], "read")   # its hook's program, never written
        self.assertNotIn("/home/alex/.codex", fs)                          # a seat home that does not exist here
        self.assertIn(os.path.expanduser("~/.ssh"), fs)
        self.assertEqual(fs[os.path.expanduser("~/.ssh")], "deny")
        self.assertIs(prof["network"]["dangerously_allow_all_unix_sockets"], True)
        self.assertEqual(prof["network"]["domains"]["github.com"], "allow")

    def test_only_existing_outermost_paths_are_denied(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "server", "tls"))
            open(os.path.join(d, "server", "tls", "server.key"), "w").close()
            got = of_codex._outermost([os.path.join(d, "server"), os.path.join(d, "server", "tls", "server.key"),
                                       os.path.join(d, "missing")])
        self.assertEqual(got, [os.path.join(d, "server")])

    def test_the_servers_socket_folders_are_read(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "127.0.0.1_18200.socket"), "w") as fh:
                fh.write("/srv/of-server/server.sock\n")
            self.assertEqual(of_codex.server_socket_dirs(d), ["/srv/of-server"])

    def test_the_layer_file_is_private(self):
        import tempfile
        with tempfile.TemporaryDirectory() as h:
            p = of_codex.write_layer(h, "L-1", self.doc())
            self.assertEqual(os.path.basename(p), "of-L-1.config.toml")
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
            with open(p, "rb") as fh:
                self.assertEqual(tomllib.load(fh)["model"], "gpt-x")

if __name__ == "__main__":
    unittest.main()
