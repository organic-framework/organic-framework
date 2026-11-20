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
        doc = tomllib.loads(of_codex.requirements_toml())
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
            self.assertEqual((h["type"], h["command"], h["timeout"]), ("command", of_codex.hook_command(ev), 30))
            self.assertIn('"$HOME/.local/bin/of"', h["command"])   # each user's own, never one user's path

    def test_the_command_prints_it_and_names_what_is_not_pinned(self):
        r = subprocess.run([sys.executable, os.path.join(HERE, "..", "bin", "of"), "codex", "requirements",
                            "--of-bin", "/x/of"], capture_output=True, text=True, timeout=60)
        bad = subprocess.run([sys.executable, os.path.join(HERE, "..", "bin", "of"), "codex", "requirements",
                              "--of-bin", "bin/of"], capture_output=True, text=True, timeout=60)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("absolute", bad.stderr)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("/x/of", tomllib.loads(r.stdout)["hooks"]["PreToolUse"][0]["hooks"][0]["command"])
        self.assertIn("the owner's own Codex", r.stderr)
        self.assertIn("/etc/codex/requirements.toml", r.stderr)
        self.assertIn("allowed_web_search_modes", r.stderr)



class HookCommandTest(unittest.TestCase):
    """The managed hook's shell line, run as Codex runs it ($SHELL -lc), with a user's home of its own (of_review on
    organic-framework PR 475: under contours another organization's user has no access to one user's of)."""

    def run_line(self, event, of_body=None, payload='{"tool_name": "Bash"}'):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            if of_body is not None:
                os.makedirs(os.path.join(home, ".local", "bin"))
                exe = os.path.join(home, ".local", "bin", "of")
                with open(exe, "w") as fh:
                    fh.write("#!/bin/sh\n" + of_body + "\n")
                os.chmod(exe, 0o755)
            env = {"HOME": home, "PATH": "/usr/bin:/bin", "SHELL": "/bin/sh"}
            return subprocess.run(["/bin/sh", "-lc", of_codex.hook_command(event)], input=payload, env=env,
                                  capture_output=True, text=True, timeout=30)

    def test_a_user_with_no_of_has_the_call_refused(self):
        r = self.run_line("PreToolUse")
        self.assertEqual(r.returncode, 2)
        self.assertIn("refused", r.stderr)
        self.assertEqual(self.run_line("PermissionRequest").returncode, 2)

    def test_a_broken_of_refuses_the_call(self):
        r = self.run_line("PreToolUse", "echo boom >&2; exit 1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("status 1", r.stderr)

    def test_the_users_own_of_decides_and_reads_the_payload(self):
        r = self.run_line("PreToolUse", 'cat; echo " $1 $2"')
        self.assertEqual((r.returncode, r.stdout), (0, '{"tool_name": "Bash"} hook PreToolUse\n'))
        self.assertEqual(self.run_line("PreToolUse", "echo denied >&2; exit 2").returncode, 2)

    def test_the_prompt_hook_carries_the_mention_guard_and_fails_closed(self):
        r = self.run_line("UserPromptSubmit", 'echo "$@"')
        self.assertEqual(r.stdout, "hook UserPromptSubmit --deny-secrets\n")
        self.assertEqual(self.run_line("UserPromptSubmit").returncode, 2)

    def test_one_named_program_is_absolute_and_quoted(self):
        with self.assertRaises(ValueError):
            of_codex.hook_command("PreToolUse", "bin/of")
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "Organic Framework $x \"q\" {}", "of")
            os.makedirs(os.path.dirname(exe))
            with open(exe, "w") as fh:
                fh.write("#!/bin/sh\necho ran \"$1\"\n")
            os.chmod(exe, 0o755)
            r = subprocess.run(["/bin/sh", "-lc", of_codex.hook_command("PreToolUse", exe)], input="{}",
                               capture_output=True, text=True, timeout=30, env={"PATH": "/usr/bin:/bin", "HOME": d})
            self.assertEqual((r.returncode, r.stdout), (0, "ran hook\n"))
            with open(exe, "w") as fh:
                fh.write("#!/bin/sh\nexit 3\n")
            r = subprocess.run(["/bin/sh", "-lc", of_codex.hook_command("PreToolUse", exe)], input="{}",
                               capture_output=True, text=True, timeout=30, env={"PATH": "/usr/bin:/bin", "HOME": d})
            self.assertEqual(r.returncode, 2)
            self.assertIn('status 3), so this is refused', r.stderr)
            self.assertIn('Organic Framework $x "q" {}', r.stderr)
            with open(exe, "w") as fh:
                fh.write("#!/bin/sh\necho ran \"$1\"\n")
            req = os.path.join(d, "requirements.toml")
            with open(req, "w") as fh:
                fh.write(of_codex.requirements_toml(exe))
            self.assertEqual(of_codex.installed_of_bin(req), os.path.realpath(exe))
            with open(req, "w") as fh:
                fh.write(of_codex.requirements_toml())
            self.assertIsNone(of_codex.installed_of_bin(req))

    def test_a_plain_file_in_the_users_bin_is_not_its_hook_folder(self):
        import tempfile
        from unittest import mock
        home = tempfile.mkdtemp(prefix="of-home-")     # never the real launcher, however the test is run (of_server)
        patch = mock.patch.dict(os.environ, {"HOME": home})
        patch.start()
        self.addCleanup(patch.stop)
        os.makedirs(os.path.expanduser("~/.local/bin"), exist_ok=True)
        plain = os.path.expanduser("~/.local/bin/of")
        with open(plain, "w") as fh:
            fh.write("#!/bin/sh\n")
        try:
            self.assertNotEqual(os.path.dirname(of_codex.default_of_bin()), os.path.dirname(plain))
        finally:
            os.unlink(plain)

    def test_a_recording_event_passes_without_of(self):
        self.assertEqual(self.run_line("Stop").returncode, 0)
        self.assertEqual(self.run_line("SessionStart", "exit 1").returncode, 1)   # Codex records the failure


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

    def test_project_layers_under_write_roots_are_read_only(self):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as cwd, \
                mock.patch.object(of_codex, "seat_homes", return_value=[]):
            os.symlink("/etc", os.path.join(cwd, ".agents"))
            fs = of_codex.filesystem(cwd, checkout_bin=cwd)
            real = os.path.realpath(cwd)
            self.assertEqual(fs[os.path.join(real, ".codex")], "read")
            self.assertTrue(os.path.isdir(os.path.join(real, ".codex")))       # made, so bwrap can bind it
            self.assertEqual(fs[os.path.join(real, ".agents")], "deny")        # a link there is never followed
            state = os.path.expanduser("~/.local/state/of")
            self.assertEqual(fs[os.path.join(state, ".codex")], "read")

    def test_the_sessions_own_home_and_the_guard_index_are_never_written(self):
        import tempfile
        from unittest import mock
        patch = mock.patch.dict(os.environ, {"HOME": tempfile.mkdtemp(prefix="of-home-")})
        patch.start()
        self.addCleanup(patch.stop)
        state = os.path.expanduser("~/.local/state/of")
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(state, "index.json"), "w") as fh:
            fh.write("{}")
        with tempfile.TemporaryDirectory() as cwd, tempfile.TemporaryDirectory() as home, \
                mock.patch.object(of_codex, "seat_homes", return_value=["/home/alex/.codex"]):
            fs = of_codex.filesystem(cwd, checkout_bin=cwd, codex_home=home)
            self.assertEqual(fs[os.path.abspath(home)], "deny")                # not a listed seat, denied all the same
            self.assertEqual(fs[os.path.join(state, "index.json")], "read")
            for name in ("guard-credentials.json", "injections.jsonl", "sessions"):   # made when missing
                self.assertEqual(fs[os.path.join(state, name)], "read", name)
                self.assertTrue(os.path.exists(os.path.join(state, name)), name)

    def test_no_deny_list_no_profile(self):
        from unittest import mock
        import of_spawn
        with mock.patch.object(of_spawn, "sandbox_settings", side_effect=AttributeError("no sandbox_settings")):
            with self.assertRaises(AttributeError):
                of_codex.filesystem("/home/alex/wt")

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

    def test_a_tool_table_never_sets_the_positions_protection(self):
        from unittest import mock
        with mock.patch.object(of_codex, "seat_homes", return_value=[]):
            for key in ("permissions", "shell_environment_policy", "sandbox_mode", "approval_policy"):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    of_codex.layer({"name": "of_docs"}, "/home/alex/wt", tool_tables={key: {}},
                                   checkout_bin="/home/alex/organic-framework/bin")

    def test_the_locale_is_named_and_the_module_reads_on_python_3_11(self):
        self.assertNotIn("LC_*", of_codex.ENV_INCLUDE)
        import ast
        with open(of_codex.__file__) as fh:
            ast.parse(fh.read(), feature_version=(3, 11))

    def test_the_layer_file_is_private(self):
        import tempfile
        with tempfile.TemporaryDirectory() as h:
            os.symlink(os.path.join(h, "elsewhere"), os.path.join(h, f"of-L-1.config.toml.{os.getpid()}"))
            p = of_codex.write_layer(h, "L-1", self.doc())
            self.assertFalse(os.path.exists(os.path.join(h, "elsewhere")))       # the left link was not followed
            self.assertEqual(os.path.basename(p), "of-L-1.config.toml")
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
            with open(p, "rb") as fh:
                self.assertEqual(tomllib.load(fh)["model"], "gpt-x")

if __name__ == "__main__":
    unittest.main()
