"""The agent host's plane per organization user (deploy/host/plane.py; of_agent-server's hand-over 5de421ee, B6 and B2),
under the rules for root of organic-framework PR 144: root follows no link and runs no code another user can change."""

import importlib.util
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "deploy", "host"))
spec = importlib.util.spec_from_file_location("plane", os.path.join(HERE, "..", "deploy", "host", "plane.py"))
plane = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plane)
import setup as host  # noqa: E402

HOMES = {"of": "/srv/of", "acme": "/srv/acme"}
ORGS = {"of": [("organic-framework-build", "/srv/of/organic-framework-build")],
        "acme": [("acme", "/srv/acme/acme-org")]}


def inner(argv):
    """The command a `sudo -u U -H env K=V ... cmd` runs, and its env."""
    i = argv.index(plane.ENV) + 1
    env = {}
    while "=" in argv[i] and not argv[i].startswith("/"):
        k, _, v = argv[i].partition("=")
        env[k] = v
        i += 1
    return argv[2], argv[i:], env


class FilesTest(unittest.TestCase):
    def test_parsing_and_each_users_own_organizations(self):
        self.assertEqual(plane.parse_org_users(["of:organic-framework-build=/srv/of/ofb", "of:x=/srv/of/x"]),
                         {"of": [("organic-framework-build", "/srv/of/ofb"), ("x", "/srv/of/x")]})
        for bad in ("of:ofb=relative/path", "Bad Name:x=/srv/x"):
            with self.assertRaises(SystemExit):
                plane.parse_org_users([bad])
        self.assertEqual(plane.parse_claude_dirs(["acme:.claude-a"], ORGS), {"of": [".claude"], "acme": [".claude-a"]})
        text = plane.orgs_yaml(ORGS["of"], "/srv/of")
        self.assertIn("name: organic-framework-build", text)
        self.assertNotIn("acme", text)
        with self.assertRaises(SystemExit):  # another user's checkout is not this plane's
            plane.orgs_yaml([("acme", "/srv/acme/acme-org")], "/srv/of")

    def test_zshrc_block_is_added_once_and_replaced_in_place(self):
        once = plane.zshrc("export EDITOR=vim\n")
        self.assertTrue(once.startswith("export EDITOR=vim\n"))
        self.assertIn("source /opt/organic-framework/bin/of-shell.zsh", once)
        self.assertEqual(plane.zshrc(once), once)
        moved = plane.zshrc(once + "alias ll='ls -l'\n")
        self.assertEqual(moved.count(plane.MARK), 1)
        self.assertTrue(moved.endswith("alias ll='ls -l'\n"))
        self.assertIn('exec /opt/homebrew/bin/python3 /opt/organic-framework/bin/of "$@"', plane.wrapper())

    def test_the_user_writes_its_own_files(self):
        with tempfile.TemporaryDirectory() as h:
            with open(os.path.join(h, ".zshrc"), "w") as fh:
                fh.write("export EDITOR=vim\n")
            done = plane.user_write({"files": plane.user_files(ORGS["of"], "/srv/of")}, home_dir=h)
            self.assertEqual(done, [".config/of/orgs.yaml", ".local/bin/of", ".zshrc"])
            self.assertEqual(oct(os.stat(os.path.join(h, ".config/of/orgs.yaml")).st_mode & 0o777), "0o600")
            self.assertTrue(os.access(os.path.join(h, ".local/bin/of"), os.X_OK))
            with open(os.path.join(h, ".zshrc")) as fh:
                self.assertEqual(fh.read().count(plane.MARK), 1)

    def test_the_daemons_run_as_the_user_at_standard_and_the_watchdog_as_root_without_env(self):
        ds = plane.daemons("of", "/srv/of")
        self.assertEqual(sorted(ds), sorted(plane.label(s, "of") for s in plane.SERVICES))
        for svc in ("herdr", "listen", "health"):
            text, owner = ds[plane.label(svc, "of")]
            d = plistlib.loads(text.encode())
            self.assertEqual((d["UserName"], d["EnvironmentVariables"]["HOME"], d["ProcessType"], owner),
                             ("of", "/srv/of", "Standard", "of"))
            self.assertTrue(d["StandardOutPath"].startswith(host.LOGS + "/"))  # root's logs, never the user's home
            self.assertNotIn("Nice", d)
        text, owner = ds[plane.label("listen-watchdog", "of")]
        w = plistlib.loads(text.encode())
        self.assertEqual(owner, "root")
        self.assertNotIn("UserName", w)  # root, so it can restart the user's system daemon
        self.assertEqual(w["ProgramArguments"][1], "-I")  # no env, no user site, no working folder
        self.assertEqual(w["EnvironmentVariables"]["OF_STATE"], "/srv/of/.local/state/of")
        self.assertEqual(w["EnvironmentVariables"]["OF_LISTEN_TARGET"], "system/dev.organic-framework.listen.of")
        self.assertTrue(w["EnvironmentVariables"]["OF_LISTEN_WATCHDOG_STATE"].startswith(plane.WATCH_STATE + "/"))
        self.assertTrue(w["StandardOutPath"].startswith(host.LOGS + "/"))

    def test_apply_writes_root_files_through_files_and_the_users_as_the_users(self):
        with tempfile.TemporaryDirectory() as t:
            homes = {u: os.path.join(t, "home", u) for u in ORGS}
            for h in homes.values():
                os.makedirs(h)
            orgs = {u: [(o, os.path.join(homes[u], os.path.basename(r))) for o, r in v] for u, v in ORGS.items()}
            ran = []

            def run(argv, **kw):
                ran.append(argv)
                if argv[0] == plane.SUDO:
                    user, cmd, _ = inner(argv)
                    if "plane.user_write" in " ".join(cmd):
                        plane.user_write(json.loads(kw["input"]), home_dir=homes[user])
                return subprocess.CompletedProcess(argv, 0, "", "")
            plane.apply(orgs, {"of": [".claude"], "acme": [".claude-a"]}, run=run, log=lambda m: None,
                        homes=homes, files=host.Files(t), checked=True)
            self.assertTrue(os.path.exists(os.path.join(homes["of"], ".config/of/orgs.yaml")))
            self.assertTrue(os.path.isdir(t + plane.WATCH_STATE))
            for u in ORGS:
                for svc in plane.SERVICES:
                    self.assertTrue(os.path.exists(t + os.path.join(plane.DAEMONS, f"{plane.label(svc, u)}.plist")))
            self.assertEqual(len(os.listdir(t + host.LOGS)), 8)               # four logs per user, made beforehand
            sudo = [a for a in ran if a[0] == plane.SUDO]
            self.assertTrue(all(a[1:4] in (["-u", "of", "-H"], ["-u", "acme", "-H"]) for a in sudo))
            hooks = [inner(a)[1] for a in sudo if "hooks" in a]
            self.assertEqual(len(hooks), 2)

    def test_root_follows_no_link_in_its_folders(self):
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(t + "/var/db")
            os.symlink(t + "/elsewhere", t + plane.WATCH_STATE)  # a link planted where root's folder goes
            with self.assertRaises(SystemExit):
                plane.apply({}, {}, run=lambda *a, **k: None, log=lambda m: None, homes={},
                            files=host.Files(t), checked=True)


class DeployTest(unittest.TestCase):
    """of_secrets-review on PR 148: only reviewed code (the public main) is deployed, and root runs no Homebrew python
    before the trust check passed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "public")  # stands for the public repository
        os.makedirs(os.path.join(self.src, "bin"))
        self.git(self.src, "init", "-q", "-b", "main")
        self.commit("v1")
        self.root = os.path.join(self.tmp.name, "opt")
        self.ran = []
        self.me = []

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, repo, *a):
        return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@example.com",
                               "-c", "commit.gpgsign=false", *a], capture_output=True, text=True, check=True).stdout

    def commit(self, text):
        with open(os.path.join(self.src, "bin", "of"), "w") as fh:
            fh.write(text + "\n")
        self.git(self.src, "add", "-A")
        self.git(self.src, "commit", "-qm", text)
        return self.git(self.src, "rev-parse", "HEAD").strip()

    def run_with(self, smoke_ok):
        def run(argv, **kw):
            self.ran.append(argv)
            if argv[0] == plane.SUDO:
                user, cmd, _ = inner(argv)
                if "smoke" in cmd:  # an organization user runs it, never root
                    return subprocess.CompletedProcess(argv, 0 if smoke_ok else 1, "smoke: ok", "smoke: FAILED")
            if argv[0] in (plane.SUDO, plane.PYTHON, plane.CHOWN, plane.CHMOD):
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.run(argv, **kw)
        return run

    def deploy(self, commit=None, smoke_ok=True, problems=(), final=()):
        commit = commit or self.git(self.src, "rev-parse", "main").strip()
        return plane.deploy(commit, "of", ["of"], run=self.run_with(smoke_ok), log=lambda m: None, root=self.root,
                            source=self.src, trust=lambda: list(problems), self_check=lambda: list(self.me),
                            final_check=lambda: list(final))

    def head(self):
        return self.git(self.root, "rev-parse", "HEAD").strip()

    def test_each_deploy_a_new_tree_swapped_in_and_a_failing_one_never_in_place(self):
        v1 = self.git(self.src, "rev-parse", "HEAD").strip()
        self.assertEqual(self.deploy(), v1)
        self.assertEqual(self.head(), v1)
        order = [("smoke" if a[0] == plane.SUDO and "smoke" in a else "compile") for a in self.ran
                 if (a[0] == plane.SUDO and "smoke" in a) or (a[0] == plane.PYTHON and "compileall" in a)]
        self.assertEqual(order, ["smoke", "compile"])            # the smoke check first, root compiles after
        self.assertTrue(any(a[0] == plane.PYTHON and a[1] == "-I" for a in self.ran))  # isolated, from /
        self.assertTrue(all(os.path.isabs(a[0]) for a in self.ran))           # every program by its full path
        gits = [a for a in self.ran if a[0] == plane.GIT]
        self.assertTrue(all(a[1:5] == plane.GIT_SAFE for a in gits))           # no hooks, no file system monitor
        self.commit("v2 broken")
        with self.assertRaises(SystemExit) as e:
            self.deploy(smoke_ok=False)
        self.assertIn("is unchanged", str(e.exception))
        self.assertEqual(self.head(), v1)                                    # the tree in place, untouched
        self.assertEqual([d for d in os.listdir(self.tmp.name) if ".new-" in d], [])  # the failed one removed
        v3 = self.commit("v3")
        self.assertEqual(self.deploy(), v3)
        self.assertTrue(os.path.isdir(self.root + ".prev"))                  # the tree before, kept
        with self.assertRaises(SystemExit) as e:
            self.deploy(commit=v1, final=["the checkout is writable by of"])  # setup.check_code fails after the swap
        self.assertIn("the tree before is back", str(e.exception))
        self.assertEqual(self.head(), v3)

    def test_root_runs_no_git_in_the_tree_in_place(self):
        """of_server on PR 148: an existing checkout's .git can redirect the fetch (insteadOf) or run a hook as
        root (post-checkout); root never runs git there."""
        self.deploy()
        marker = os.path.join(self.tmp.name, "hook-ran")
        with open(os.path.join(self.root, ".git", "config"), "a") as fh:
            fh.write(f'[url "{self.tmp.name}/evil"]\n\tinsteadOf = {self.src}\n[core]\n\thooksPath = hooks\n')
        hooks = os.path.join(self.root, ".git", "hooks")
        os.makedirs(hooks, exist_ok=True)
        with open(os.path.join(hooks, "post-checkout"), "w") as fh:
            fh.write(f"#!/bin/sh\ntouch {marker}\n")
        os.chmod(os.path.join(hooks, "post-checkout"), 0o755)
        v2 = self.commit("v2")
        self.ran.clear()
        self.assertEqual(self.deploy(), v2)
        self.assertFalse(os.path.exists(marker))                             # no hook ran
        self.assertFalse(any(a[0] == plane.GIT and self.root in a for a in self.ran))  # no git in the old tree

    def test_a_commit_not_on_the_public_main_is_refused(self):
        self.git(self.src, "checkout", "-q", "-b", "unreviewed")
        side = self.commit("an agent's commit nobody reviewed")
        self.git(self.src, "checkout", "-q", "main")
        with self.assertRaises(SystemExit) as e:
            self.deploy(commit=side)
        self.assertIn("is not on the public main", str(e.exception))
        self.assertFalse(any(a[0] == plane.PYTHON for a in self.ran))      # nothing run as root
        self.assertFalse(os.path.exists(self.root))

    def test_no_python_as_root_before_the_trust_check_passes(self):
        with self.assertRaises(SystemExit) as e:
            self.deploy(problems=["of may write /opt/homebrew/bin"])
        self.assertIn("the trust check failed", str(e.exception))
        self.assertFalse(any(a[0] == plane.PYTHON for a in self.ran))      # no compileall, no smoke
        self.assertFalse(any(a[0] == plane.SUDO and "smoke" in a for a in self.ran))

    def test_the_script_and_its_python_are_checked_first(self):
        self.me = ["of may write the folder of plane.py"]
        with self.assertRaises(SystemExit) as e:
            self.deploy()
        self.assertIn("what runs this script is not root's alone", str(e.exception))
        self.assertEqual(self.ran, [])                                      # nothing ran at all

    def test_root_runs_with_a_fixed_path(self):
        seen = []
        real = self.run_with(True)

        def run(argv, **kw):
            seen.append((argv[0], kw.get("env"), kw.get("cwd")))
            return real(argv, **kw)
        plane.deploy(self.git(self.src, "rev-parse", "main").strip(), "of", ["of"], run=run, log=lambda m: None,
                     root=self.root, source=self.src, trust=lambda: [], self_check=lambda: [], final_check=lambda: [])
        for prog, env, cwd in seen:
            if prog != plane.SUDO:
                self.assertEqual(cwd, "/", prog)
                self.assertEqual(env["PATH"], plane.ROOT_ENV["PATH"], prog)
                if prog == plane.GIT:
                    self.assertEqual((env["GIT_CONFIG_NOSYSTEM"], env["GIT_CONFIG_GLOBAL"]), ("1", "/dev/null"))

    def test_only_a_full_hash(self):
        for bad in ("main", "v1.0", "abc123"):
            with self.assertRaises(SystemExit) as e:
                self.deploy(commit=bad)
            self.assertIn("full commit hash", str(e.exception))

if __name__ == "__main__":
    unittest.main()
