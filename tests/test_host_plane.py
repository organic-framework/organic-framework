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
    i = argv.index("env") + 1
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
        self.assertEqual(w["ProgramArguments"][1:3], ["-E", "-s"])
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
                if argv[0] == "sudo":
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
            sudo = [a for a in ran if a[0] == "sudo"]
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
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "src")
        os.makedirs(os.path.join(self.src, "bin"))
        self.git(self.src, "init", "-q", "-b", "main")
        self.commit("v1")
        self.root = os.path.join(self.tmp.name, "opt")
        self.files = host.Files(self.tmp.name)
        self.ran = []

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
            if argv[0] == "sudo":
                user, cmd, _ = inner(argv)
                if "smoke" in cmd:  # the source's owner runs it, never root
                    return subprocess.CompletedProcess(argv, 0 if smoke_ok else 1, "smoke: ok", "smoke: FAILED")
                return subprocess.run(cmd, capture_output=True, text=True)  # the owner's git bundle
            if argv[0] in (plane.PYTHON, "chown", "chmod"):
                return subprocess.CompletedProcess(argv, 0, "", "")
            return subprocess.run(argv, **kw)
        return run

    def deploy(self, ok):
        return plane.deploy(self.src, "main", run=self.run_with(ok), log=lambda m: None, root=self.root,
                            files=self.files, owner="of", checked=True)

    def test_deploy_from_the_owners_bundle_and_a_failing_commit_rolled_back(self):
        v1 = self.git(self.src, "rev-parse", "HEAD").strip()
        self.assertEqual(self.deploy(True), v1)
        bundles = [a for a in self.ran if a[0] == "sudo" and "bundle" in a]
        self.assertTrue(bundles and inner(bundles[0])[0] == "of")             # the owner made the bundle
        self.assertFalse(any(a[:2] == ["git", "-C"] and a[2] == self.src for a in self.ran))  # root: no git there
        self.assertTrue(any(a[0] == "sudo" and "smoke" in a for a in self.ran))
        self.assertTrue(any(a[0] == plane.PYTHON and "compileall" in a for a in self.ran))
        self.commit("v2 broken")
        with self.assertRaises(SystemExit) as e:
            self.deploy(False)
        self.assertIn("back on", str(e.exception))
        self.assertEqual(self.git(self.root, "rev-parse", "HEAD").strip(), v1)  # the commit before, still serving
        v3 = self.commit("v3")
        self.assertEqual(self.deploy(True), v3)

    def test_a_link_in_place_of_the_bundle_is_refused(self):
        real = self.run_with(True)

        def run(argv, **kw):
            r = real(argv, **kw)
            if argv[0] == "sudo" and "bundle" in argv:  # the owner leaves a link where its bundle should be
                b = self.files.p(plane.HANDOFF_DIR) + "/release.bundle"
                os.replace(b, b + ".real")
                os.symlink(b + ".real", b)
            return r
        with self.assertRaises(SystemExit) as e:
            plane.deploy(self.src, "main", run=run, log=lambda m: None, root=self.root, files=self.files,
                         owner="of", checked=True)
        self.assertIn("link", str(e.exception))


if __name__ == "__main__":
    unittest.main()
