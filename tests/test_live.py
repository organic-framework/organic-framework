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
            with open(os.path.join(a, "settings.json")) as fh:
                sa = json.load(fh)
            sa["hooks"]["PreToolUse"][0]["matcher"] = of_init.GUARD_MATCHER   # the guard's own group
            with open(os.path.join(a, "settings.json"), "w") as fh:
                json.dump(sa, fh)
            settings(b, ["py /wc/bin/of-hook Stop", "py /wc/bin/of-hook PreToolUse"])   # a recorder's, no guard
            settings(c, ["echo other Stop"])
            d = os.path.join(t, "d")
            settings(d, ["/py /my code/bin/of-hook Stop", "/py /my code/bin/of-hook UserPromptSubmit --deny-secrets"])
            self.assertEqual(of_live.hooked_accounts([d])[0][1][:2], ["--replace", "/my code/bin/of"])
            got = dict(of_live.hooked_accounts([a, b, c, os.path.join(t, "none")]))
            self.assertEqual(got, {a: ["--replace", "/wc/bin/of", "--guard", "--deny-secrets"],
                                   b: ["--replace", "/wc/bin/of"]})

    def test_a_switch_runs_lives_own_of_for_every_account_then_the_units(self):
        plan = of_live.switch_plan("/py", [("/acc", ["--guard"])], live="/L", units=of_live.UNITS)
        self.assertEqual(plan[0], ["/py", "/L/bin/of", "hooks", "install", "--config-dir", "/acc", "--yes", "--guard"])
        self.assertEqual([c[2] for c in plan[1:]], ["listen", "relay", "server", "health"])
        self.assertTrue(all(c[1] == "/L/bin/of" for c in plan))

    def test_hooks_installed_from_live_name_live_not_the_working_copy(self):
        """The outage's replay: the hook command an install writes runs the checkout it was run from, so a hook
        installed from LIVE never runs what a session leaves half-applied in the working copy."""
        cmd = of_init.hook_command("PreToolUse", of_path="/L/bin/of")
        self.assertIn("/L/bin/of-hook", cmd)
        self.assertNotIn(ROOT, cmd)


ORGS = """# the owner's file
orgs:
  - name: alpha
    path: ~/alpha   # its repository
    paths:
      - ~/wc
  - name: beta
    path: ~/beta
"""


class FfEntry(unittest.TestCase):
    def test_setup_lists_the_live_checkout_for_of_protected_ff_and_keeps_the_rest(self):
        import yaml
        with tempfile.TemporaryDirectory() as t:
            f = os.path.join(t, "orgs.yaml")
            with open(f, "w") as fh:
                fh.write(ORGS)
            self.assertIsNone(of_live.ff_entry(f, live="/L"))
            of_live.add_ff_entry(f, "alpha", live="/L")
            text = open(f).read()
            self.assertIn("# the owner's file", text)
            self.assertIn("# its repository", text)
            doc = yaml.safe_load(text)
            self.assertEqual(doc["orgs"][0]["protected_code"], [{"path": "/L", "ff": of_live.FF}])
            self.assertNotIn("protected_code", doc["orgs"][1])
            self.assertEqual(of_live.ff_entry(f, live="/L")["ff"]["repo"], "organic-framework/organic-framework")
            with self.assertRaises(of_live.LiveError):          # a second one is the owner's to add by hand
                of_live.add_ff_entry(f, "alpha", live="/L2")

    def test_setup_stops_before_it_turns_a_registrys_ff_entries_off(self):
        """of_review on PR 628: orgs.yaml's first protected_code stops every registry's ff entry from vouching."""
        from unittest import mock
        import yaml
        with tempfile.TemporaryDirectory() as t:
            f = os.path.join(t, "orgs.yaml")
            with open(f, "w") as fh:
                fh.write(ORGS)
            reg = {"beta": [{"path": "~/beta/.wt/main", "ff": {"repo": "b/b", "branch": "main", "checks": ["unit"]}}]}
            with mock.patch.object(of_live, "registry_ff_items", return_value=reg), \
                    mock.patch.object(of_live, "check_live"), mock.patch.object(of_live, "copy_pyyaml", return_value=t), \
                    mock.patch.object(of_live.os.path, "isfile", return_value=True):
                with self.assertRaises(of_live.LiveError) as e:
                    of_live.setup(live=t, orgs_file=f, org="alpha", run=lambda *a, **k: None, log=lambda m: 0)
                self.assertIn("beta: ~/beta/.wt/main", str(e.exception))
                self.assertEqual(open(f).read(), ORGS)            # nothing written
                with mock.patch.object(of_live.plistlib, "dumps", return_value=b""), \
                        mock.patch.object(of_live, "AGENTS", t), mock.patch.object(of_live, "REQUEST", f + ".req"):
                    of_live.setup(live=t, orgs_file=f, org="alpha", keep_registry_ff=True,
                                  run=lambda *a, **k: subprocess.CompletedProcess(a, 0), log=lambda m: 0)
            doc = yaml.safe_load(open(f))
            self.assertEqual(os.path.expanduser(doc["orgs"][0]["protected_code"][0]["path"]), t)
            self.assertEqual(doc["orgs"][1]["protected_code"], reg["beta"])

    def test_ff_true_is_no_entry(self):
        with tempfile.TemporaryDirectory() as t:
            f = os.path.join(t, "orgs.yaml")
            with open(f, "w") as fh:
                fh.write("orgs:\n  - name: a\n    protected_code:\n      - path: /L\n        ff: true\n")
            self.assertIsNone(of_live.ff_entry(f, live="/L"))

    def test_the_org_is_the_one_listing_the_running_checkout(self):
        with tempfile.TemporaryDirectory() as t:
            f = os.path.join(t, "orgs.yaml")
            with open(f, "w") as fh:
                fh.write(ORGS.replace("~/wc", t))
            self.assertEqual(of_live.checkout_org(f, checkout=t), "alpha")
            self.assertIsNone(of_live.checkout_org(f, checkout="/nowhere"))

    def test_the_move_runs_isolated_with_pyyaml_from_the_kept_copy(self):
        """of_secrets-review on PR 629: -E loaded the user site, a folder sessions may write."""
        text = open(os.path.join(ROOT, "deploy", "laptop", "of-checkout-update.sh")).read()
        self.assertIn('"$PY" -I -B', text)
        self.assertNotIn('"$PY" -E', text)
        self.assertIn("SITE=$LIVE/.git/of-site", text)

    def test_setup_copies_pyyaml_and_the_isolated_run_imports_it(self):
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, ".git"))
            site = of_live.copy_pyyaml(live=t)
            self.assertTrue(os.path.isfile(os.path.join(site, "yaml", "__init__.py")))
            self.assertFalse(os.path.exists(os.path.join(site, "yaml", "__pycache__")))
            r = subprocess.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import yaml; "
                                "print(yaml.__file__)", site], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.path.realpath(r.stdout.strip()).startswith(os.path.realpath(site)))


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
                of_live.switch("/py", run=run, live="/L", accounts=[("/a", []), ("/b", []), ("/c", [])], log=lambda m: 0,
                               units=[])
        self.assertEqual(len(ran), 2)
        moved.assert_not_called()          # ~/.local/bin/of is left as it was


class Owner(unittest.TestCase):
    def test_setup_and_switch_refuse_a_process_of_automation_started(self):
        env = dict(os.environ, OF_AUTOMATION="1")
        for k in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "OF_POSITION"):
            env.pop(k, None)
        for act in ("setup", "switch"):
            # with a terminal on stdin, which the owner's test takes as his (script gives it one)
            cmd = [sys.executable, os.path.join(ROOT, "bin", "of"), "live", act]
            cmd = (["script", "-q", "/dev/null"] + cmd) if sys.platform == "darwin" else \
                ["script", "-qec", " ".join(cmd), "/dev/null"]
            r = subprocess.run(cmd, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)
            self.assertNotEqual(r.returncode, 0, act)
            self.assertIn("is the owner's", r.stdout + r.stderr, act)


class Request(unittest.TestCase):
    def test_a_request_never_follows_a_link_or_waits_on_a_fifo(self):
        with tempfile.TemporaryDirectory() as t:
            r = os.path.join(t, "req")
            self.assertEqual(of_live.request(r), r)
            os.unlink(r)
            os.symlink(os.path.join(t, "elsewhere"), r)
            with self.assertRaises(of_live.LiveError):
                of_live.request(r)
            self.assertFalse(os.path.exists(os.path.join(t, "elsewhere")))
            os.unlink(r)
            os.mkfifo(r)
            with self.assertRaises(of_live.LiveError):
                of_live.request(r)

    def test_a_switch_moves_only_the_units_this_machine_runs(self):
        with tempfile.TemporaryDirectory() as t:
            open(os.path.join(t, "dev.organic-framework.listen.plist"), "w").close()
            self.assertEqual(of_live.installed_units(t), [("listen", "install")])


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
            subprocess.run(["git", "-C", live, "config", "remote.origin.tagopt", "--no-tags"], check=True)
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

    def test_the_update_restarts_only_the_units_this_machine_runs(self):
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "Library", "LaunchAgents"))
            open(os.path.join(t, "Library", "LaunchAgents", "dev.organic-framework.listen.plist"), "w").close()
            env = dict(os.environ, OF_PYTHON=sys.executable, HOME=t)
            r = subprocess.run(["bash", "-c", 'source "$1"; echo "[$UNITS]"', "u", self.SCRIPT], capture_output=True,
                               text=True, env=env)
            self.assertEqual(r.stdout.strip(), "[ dev.organic-framework.listen]")

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
        if not hasattr(of_guard, "LIVE_DIR"):   # G40 (its own pull request) holds the guard's side
            self.skipTest("no G40 in this tree")
        self.assertEqual(of_live.LIVE_DIR, of_guard.LIVE_DIR)

    def test_a_live_folder_that_is_no_checkout_is_refused(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(of_live.LiveError):
                of_live.check_live(t)


if __name__ == "__main__":
    unittest.main()
