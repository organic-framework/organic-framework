"""The agent host's server side (deploy/host/setup.py; of_agent-server's hand-over aba3d171, item 1): a service user
holds of-server, ClickHouse and their secrets; LaunchDaemons with UserName, written and not loaded; one root watchdog
for the server and each organization's relay. Run against a test tree: no user is created, nothing is loaded."""

import importlib.util
import io
import json
import os
import plistlib
import stat
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_relay_client  # noqa: E402
import of_server  # noqa: E402

spec = importlib.util.spec_from_file_location("host_setup", os.path.join(HERE, "..", "deploy", "host", "setup.py"))
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.pub = os.path.join(self.root, "grant.pub")
        with open(self.pub, "w") as fh:
            fh.write("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample grant@of\n")

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self, *extra):
        out = io.StringIO()
        with redirect_stdout(out):
            H.main(["apply", "--org-user", "of", "--org-user", "acme", "--org", "organic-framework-build",
                    "--org", "acme=of_acme", "--grant-pub", self.pub, "--tenant", "t1", "--root",
                    self.root, *extra])
        return out.getvalue()

    def p(self, path):
        return self.root + path

    def mode(self, path):
        return stat.S_IMODE(os.stat(self.p(path)).st_mode)

    def test_plan_changes_nothing(self):
        out = io.StringIO()
        with redirect_stdout(out):
            H.main(["plan", "--org-user", "of", "--root", self.root])
        self.assertIn("NOT loaded", out.getvalue())
        self.assertFalse(os.path.exists(self.p("/etc/of")))

    def test_apply_writes_the_files_with_their_modes_and_prints_no_secret(self):
        printed = self.apply()
        self.assertEqual(self.mode("/var/of-server"), 0o700)
        self.assertEqual(self.mode("/var/of-server/secrets"), 0o700)
        self.assertEqual(self.mode("/Users/of"), 0o700)
        self.assertEqual(self.mode("/etc/of/server.yaml"), 0o640)
        self.assertEqual(self.mode("/etc/of/grant_ed25519.pub"), 0o644)
        secrets_dir = self.p("/var/of-server/secrets")
        names = sorted(os.listdir(secrets_dir))
        self.assertIn("of_host_admin.password", names)
        self.assertIn("of_acme_ingest.password", names)
        self.assertIn("of_organic_framework_build_read.password", names)
        self.assertIn("of_host_vault_redact.password", names)
        for n in names:
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(secrets_dir, n)).st_mode), 0o600, n)
            with open(os.path.join(secrets_dir, n)) as fh:
                self.assertNotIn(fh.read(), printed)                         # never printed
        server = open(self.p("/etc/of/server.yaml")).read()
        for want in ("host: 127.0.0.1", "tenant_key_create: false", "trusted_grant_keys_file: /etc/of/grant_ed25519.pub",
                     "acme: of_acme", "organic-framework-build: of_organic_framework_build",
                     "tenant: t1", "user: of_host_unassigned_ingest"):
            self.assertIn(want, server)
        users = open(self.p("/etc/of/clickhouse/users.xml")).read()
        self.assertIn("<access_management>1</access_management>", users)
        self.assertNotIn(open(os.path.join(secrets_dir, "of_host_admin.password")).read(), users)
        admin = open(os.path.join(secrets_dir, "of_host_admin.password")).read()
        self.apply()                                                         # a re-run keeps the passwords
        self.assertEqual(open(os.path.join(secrets_dir, "of_host_admin.password")).read(), admin)

    def test_the_daemons_run_as_their_users_at_standard_priority(self):
        self.apply()
        d = self.p("/Library/LaunchDaemons")
        units = {f[:-6]: plistlib.load(open(os.path.join(d, f), "rb")) for f in os.listdir(d)}
        self.assertEqual(set(units), {H.SERVER_LABEL, H.CLICKHOUSE_LABEL, H.WATCHDOG_LABEL, "dev.organic-framework.relay.of",
                                      "dev.organic-framework.relay.acme"})
        self.assertEqual(units[H.SERVER_LABEL]["UserName"], "_ofserver")
        self.assertEqual(units[H.CLICKHOUSE_LABEL]["UserName"], "_ofserver")
        self.assertEqual(units["dev.organic-framework.relay.acme"]["UserName"], "acme")
        self.assertEqual(units["dev.organic-framework.relay.acme"]["EnvironmentVariables"]["HOME"], "/Users/acme")
        self.assertNotIn("UserName", units[H.WATCHDOG_LABEL])                # root: it kickstarts system daemons
        self.assertEqual(units[H.WATCHDOG_LABEL]["StartInterval"], 60)
        wd = units[H.WATCHDOG_LABEL]["ProgramArguments"]
        self.assertIn("--relay", wd)
        self.assertEqual(wd[wd.index("--state-dir") + 1], H.WATCHDOG_STATE)    # root's folder, not _ofserver's
        self.assertEqual([wd[i + 1] for i, w in enumerate(wd) if w == "--code"],  # looked at again each minute
                         [H.PYTHON, H.CLICKHOUSE, f"{H.OF_ROOT}/bin"])
        self.assertEqual(wd[wd.index("--service-user") + 1], "_ofserver")
        for label in (H.SERVER_LABEL, H.WATCHDOG_LABEL):                     # no PYTHON* variable, no user site
            self.assertEqual(units[label]["ProgramArguments"][1:3], ["-E", "-s"])
        self.assertEqual(self.mode(H.WATCHDOG_STATE), 0o700)
        for u in units.values():
            # launchd may open a log as root: every log is in root's own folder, made before, 600
            self.assertTrue(u["StandardOutPath"].startswith(H.LOGS + "/"), u["Label"])
            self.assertEqual(self.mode(u["StandardOutPath"]), 0o600)
            self.assertEqual(u["ProcessType"], "Standard")
            self.assertFalse({"Nice", "LowPriorityIO"} & set(u))
            self.assertEqual(u["EnvironmentVariables"]["OF_AUTOMATION"], "1")
            self.assertEqual(u["EnvironmentVariables"]["SSH_AUTH_SOCK"], "")

    def test_a_grant_key_that_is_no_public_key_is_refused(self):
        with open(self.pub, "w") as fh:
            fh.write("-----BEGIN OPENSSH PRIVATE KEY-----\n")
        with self.assertRaises(SystemExit):
            self.apply()

    def test_start_loads_storage_first_and_stop_the_watchdog_first(self):
        calls = []
        H.start_stop("start", ["of"], run=lambda argv, **k: calls.append(argv) or mock.Mock(returncode=0),
                     out=lambda m: None)
        self.assertEqual([c[-1].split("/")[-1] for c in calls],
                         [f"{H.CLICKHOUSE_LABEL}.plist", f"{H.SERVER_LABEL}.plist",
                          "dev.organic-framework.relay.of.plist", f"{H.WATCHDOG_LABEL}.plist"])
        calls.clear()
        H.start_stop("stop", ["of"], run=lambda argv, **k: calls.append(argv) or mock.Mock(returncode=0),
                     out=lambda m: None)
        self.assertEqual(calls[0], ["launchctl", "bootout", f"system/{H.WATCHDOG_LABEL}"])


class LinkTest(unittest.TestCase):
    """A re-run as root follows no link that _ofserver or an organization user put in its folders (of_secrets-review
    and of_review on organic-framework PR 144)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.outside = os.path.join(self.root, "outside")
        os.makedirs(self.outside)
        os.chmod(self.outside, 0o755)
        self.victim = os.path.join(self.root, "victim.txt")
        with open(self.victim, "w") as fh:
            fh.write("root's own file\n")
        os.chmod(self.victim, 0o644)

    def tearDown(self):
        self.tmp.cleanup()

    def apply(self):
        with redirect_stdout(io.StringIO()):
            return H.main(["apply", "--org-user", "of", "--root", self.root])

    def p(self, path):
        return self.root + path

    def victim_untouched(self):
        self.assertEqual(open(self.victim).read(), "root's own file\n")
        self.assertEqual(stat.S_IMODE(os.stat(self.victim).st_mode), 0o644)

    def test_a_folder_replaced_by_a_link_is_refused_and_its_target_keeps_its_mode(self):
        self.apply()
        state = self.p("/var/of-server/state")
        os.rmdir(state)
        os.symlink(self.outside, state)
        with self.assertRaises(SystemExit) as e:
            self.apply()
        self.assertIn("link", str(e.exception))
        self.assertEqual(stat.S_IMODE(os.stat(self.outside).st_mode), 0o755)

    def test_a_link_above_the_folder_is_refused(self):
        self.apply()
        home = self.p("/var/of-server")
        os.rename(home, home + ".old")
        os.symlink(self.outside, home)
        with self.assertRaises(SystemExit):
            self.apply()
        self.assertEqual(os.listdir(self.outside), [])                      # nothing made through the link

    def test_a_password_that_is_a_link_or_a_hard_link_is_refused(self):
        self.apply()
        pw = self.p(f"/var/of-server/secrets/{H.ADMIN}.password")
        for plant in (os.symlink, os.link):
            os.remove(pw)
            plant(self.victim, pw)
            with self.assertRaises(SystemExit):
                self.apply()
            self.victim_untouched()
            users = open(self.p("/etc/of/clickhouse/users.xml")).read()
            import hashlib
            self.assertNotIn(hashlib.sha256(open(self.victim, "rb").read()).hexdigest(), users)

    def test_a_link_where_a_new_file_is_written_is_replaced_never_written_through(self):
        self.apply()
        tmp = self.p("/etc/of/server.yaml.tmp")
        os.symlink(self.victim, tmp)
        self.apply()
        self.victim_untouched()
        self.assertFalse(os.path.islink(self.p("/etc/of/server.yaml")))

    def test_schema_makes_its_client_file_new_and_never_writes_through_a_link(self):
        self.apply()
        cfg = self.p("/var/of-server/secrets/client.xml")
        os.symlink(self.victim, cfg)
        seen = {}

        def run(argv, **k):
            path = argv[argv.index("--config-file") + 1]
            seen["link"], seen["mode"] = os.path.islink(path), stat.S_IMODE(os.lstat(path).st_mode)
            seen["text"] = open(path).read()
            return mock.Mock(returncode=0, stderr="")
        H.load_schema(["of"], run=run, out=lambda m: None, root=self.root)
        self.victim_untouched()
        self.assertEqual((seen["link"], seen["mode"]), (False, 0o600))
        self.assertIn(f"<user>{H.ADMIN}</user>", seen["text"])
        self.assertFalse(os.path.lexists(cfg))                               # removed after

    def test_a_log_that_is_a_link_is_refused(self):
        self.apply()
        log = self.p(f"{H.LOGS}/relay-of.log")
        os.remove(log)
        os.symlink(self.victim, log)
        with self.assertRaises(SystemExit):
            self.apply()
        self.victim_untouched()


class TrustTest(unittest.TestCase):
    """root and _ofserver run no code an organization user or _ofserver may change (of_secrets-review on
    organic-framework PR 144, finding 1)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = os.path.realpath(self.tmp.name)
        self.brew = os.path.join(t, "brew")
        cellar = os.path.join(self.brew, "Cellar", "python", "3", "bin")
        os.makedirs(cellar)
        os.makedirs(os.path.join(self.brew, "bin"))
        self.real_py = os.path.join(cellar, "python3")
        open(self.real_py, "w").close()
        self.py = os.path.join(self.brew, "bin", "python3")
        os.symlink("../Cellar/python/3/bin/python3", self.py)
        site = os.path.join(self.brew, "lib", "site-packages")
        os.makedirs(site)
        self.extra = os.path.join(t, "extra")
        os.makedirs(self.extra)
        self.extra_mod = os.path.join(self.extra, "mod.py")
        open(self.extra_mod, "w").close()
        with open(os.path.join(site, "extra.pth"), "w") as fh:
            fh.write("# a comment\nimport os\n../../../extra\n")
        self.lists = []

    def tearDown(self):
        self.tmp.cleanup()

    def run_as(self, writable=()):
        def run(argv, input="", **k):
            self.lists.append((argv[3], input.splitlines()))
            return mock.Mock(returncode=0, stdout="".join(p + "\n" for p in writable if p in input.splitlines()),
                             stderr="")
        return run

    def problems(self, writable=(), owner_ok=lambda uid: True, users=None):
        me = __import__("pwd").getpwuid(os.getuid()).pw_name
        return H.trust_problems([self.brew, self.py], users or [me], run=self.run_as(writable), owner_ok=owner_ok)

    def test_a_clean_tree_passes_and_every_folder_and_file_on_the_way_is_asked(self):
        self.assertEqual(self.problems(), [])
        asked = set(self.lists[0][1])
        for p in (os.path.dirname(self.py), self.real_py, os.path.dirname(self.real_py), "/",
                  self.extra_mod, os.path.dirname(self.brew)):
            self.assertIn(p, asked)
        self.assertNotIn(self.py, asked)                                     # a link: its folder is what counts

    def test_a_path_an_untrusted_user_may_write_is_refused(self):
        got = self.problems(writable=[self.extra_mod])
        self.assertEqual(len(got), 1)
        self.assertIn(self.extra_mod, got[0])

    def test_a_path_owned_by_someone_untrusted_is_refused(self):
        got = self.problems(owner_ok=lambda uid: False)
        self.assertTrue(any("owned by neither root nor an admin" in g for g in got), got)

    def test_a_missing_path_and_a_missing_organization_user_are_refused(self):
        os.remove(self.real_py)
        got = self.problems(users=["no-such-user-of-test"])
        self.assertTrue(any(self.real_py in g for g in got), got)
        self.assertTrue(any("no user no-such-user-of-test" in g for g in got), got)

    def test_the_service_user_not_made_yet_is_skipped(self):
        with mock.patch.object(H, "SERVICE_USER", "_ofserver_not_here"):
            self.assertEqual(H.trust_problems([self.brew, self.py], ["_ofserver_not_here"], run=self.run_as(),
                                              owner_ok=lambda uid: True), [])
        self.assertEqual(self.lists, [])                                     # nobody to ask

    def test_main_refuses_and_changes_nothing_when_the_code_is_not_trusted(self):
        out = io.StringIO()
        with mock.patch.object(H.os, "geteuid", lambda: 0), \
                mock.patch.object(H, "check_code", lambda users, run=None: ["x may write 1 of them: /opt/y"]), \
                mock.patch.object(H.Plan, "build", side_effect=AssertionError("nothing is built")), \
                redirect_stdout(out):
            self.assertEqual(H.main(["apply", "--org-user", "of"]), 2)
        self.assertIn("refused", out.getvalue())
        self.assertIn("/opt/y", out.getvalue())

    def test_the_python_running_the_script_is_checked_too_and_pyyaml_must_import_under_E_s(self):
        """of_review on PR 144: the interpreter running setup.py was not checked, and a user install of PyYAML is not
        seen by the daemons' -E -s."""
        roots, asked = [], []
        here = os.path.join(H.OF_ROOT, "deploy", "host", "setup.py")

        def run(argv, **k):
            asked.append(argv)
            return mock.Mock(returncode=1, stdout="", stderr="ModuleNotFoundError: No module named 'yaml'")
        with mock.patch.object(H, "trust_problems", lambda r, *a, **k: roots.extend(r) or []), \
                mock.patch.object(H.os.path, "realpath", lambda p: p):
            got = H.check_code(["of"], run=run, here=here)
        self.assertIn(sys.prefix, roots)
        self.assertEqual(roots[-1], sys.executable)                         # after the prefix whose tree it is in
        self.assertEqual(asked, [["sudo", "-n", "-u", "nobody", "--", H.PYTHON, "-E", "-s", "-c", "import yaml"]])
        self.assertEqual(len(got), 1)
        self.assertIn("PyYAML", got[0])
        asked.clear()
        with mock.patch.object(H, "trust_problems", lambda *a, **k: ["x may write 1 of them: /opt/y"]), \
                mock.patch.object(H.os.path, "realpath", lambda p: p):
            H.check_code(["of"], run=run, here=here)
        self.assertEqual(asked, [])                                          # an unsafe python is never run

    def test_the_script_must_be_the_release_checkouts(self):
        with mock.patch.object(H, "trust_problems", lambda *a, **k: []):
            got = H.check_code(["of"], here="/Users/alex/checkout/deploy/host/setup.py")
        self.assertTrue(any("not from the release checkout" in g for g in got), got)


class HostWatchdogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.state, self.of_state = os.path.join(t, "wd"), os.path.join(t, "of")
        os.makedirs(self.of_state)
        self.hb = os.path.join(self.of_state, of_relay_client.HEARTBEAT)
        self.now = 1_800_000_000.0
        self.cfg = dict(of_server.DEFAULTS, state_dir=os.path.join(t, "ofserver-state"))

    def tearDown(self):
        self.tmp.cleanup()

    def beat(self, ts):
        with open(self.hb, "w") as fh:
            json.dump({"ts": ts, "pid": os.getpid(), "loops": {"presence": ts}}, fh)

    def check(self, relays=None, **kw):
        kicked, emitted = [], []
        with mock.patch.object(of_server, "_health", lambda c, timeout=None: None), \
                mock.patch.object(of_server.of_events, "emit", lambda *a, **k: emitted.append(a)), \
                mock.patch.object(of_server.subprocess, "run",
                                  lambda argv, **k: kicked.append(argv) or mock.Mock(returncode=0)):
            got = [of_server.host_watchdog(self.cfg, relays or [("of", "dev.organic-framework.relay.of",
                                                                   self.of_state)],
                                           now=self.now + 60 * i, dump=lambda p: None, log=lambda m: None,
                                           state_dir=self.state, pid_of=lambda label: os.getpid(),
                                           uid_of=lambda user: os.getuid(), **kw)
                   for i in range(5)]
        return got, kicked, emitted

    def test_the_server_and_each_relay_with_system_daemons(self):
        self.beat(self.now - 1000)
        got, kicked, emitted = self.check()
        self.assertEqual([g["of-server"] for g in got], ["failing"] * 3 + ["restarted", "waiting"])
        self.assertEqual([g["relay of"] for g in got], ["failing"] * 3 + ["restarted", "waiting"])
        self.assertIn(["launchctl", "kickstart", "-k", "system/dev.organic-framework.server"], kicked)
        self.assertIn(["launchctl", "kickstart", "-k", "system/dev.organic-framework.relay.of"], kicked)
        self.assertEqual(emitted, [])                                        # root's own store is read by nobody
        self.assertEqual(sorted(os.listdir(self.state)), ["watchdog-relay-of.json", "watchdog.json"])
        self.assertFalse(os.path.exists(self.cfg["state_dir"]))              # nothing in _ofserver's folder

    def test_the_servers_pid_is_launchds_never_its_pid_file(self):
        os.makedirs(self.cfg["state_dir"])
        with open(os.path.join(self.cfg["state_dir"], "server.pid"), "w") as fh:
            fh.write("1\n")                                                  # _ofserver may write any pid here
        asked = []
        with mock.patch.object(of_relay_client, "launchd_pid", lambda label, domain=None: asked.append(
                (label, domain))), \
                mock.patch.object(of_server, "_health", lambda c, timeout=None: None):
            for i in range(5):
                of_server.host_watchdog(self.cfg, [], now=self.now + 60 * i, dump=lambda p: self.fail("signalled"),
                                        restart=lambda: self.fail("restarted"), log=lambda m: None,
                                        state_dir=self.state)
        self.assertIn(("dev.organic-framework.server", "system"), asked)

    def test_a_fifo_a_link_or_a_crafted_heartbeat_is_no_heartbeat_and_never_blocks(self):
        fresh = os.path.join(self.tmp.name, "fresh.json")
        with open(fresh, "w") as fh:
            json.dump({"ts": self.now, "pid": 1, "loops": {"presence": self.now}}, fh)
        os.symlink(fresh, self.hb)
        self.assertEqual(of_relay_client.relay_healthy(self.now, path=self.hb), (False, None, "no heartbeat"))
        os.remove(self.hb)
        os.mkfifo(self.hb)
        done = []
        th = threading.Thread(target=lambda: done.append(of_relay_client.relay_healthy(self.now, path=self.hb)),
                              daemon=True)
        th.start()
        th.join(5)
        self.assertEqual(done, [(False, None, "no heartbeat")])              # returned at once, not blocked
        os.remove(self.hb)
        for crafted in ('{"ts": "x", "loops": {}}', '{"ts": 1, "loops": {"a": "b"}}', '[1, 2]', '"x"',
                        '{"ts": 1e999, "loops": {}}', '{"ts": 1, "loops": [1]}', "[" * 50000, "not json",
                        '{"ts": 1, "loops": {}, "pad": "' + "x" * 70000 + '"}'):
            with open(self.hb, "w") as fh:
                fh.write(crafted)
            self.assertEqual(of_relay_client.relay_healthy(self.now, path=self.hb), (False, None, "no heartbeat"),
                             crafted[:40])
        self.beat(self.now)
        self.assertTrue(of_relay_client.relay_healthy(self.now, path=self.hb, uid=os.getuid())[0])
        self.assertFalse(of_relay_client.relay_healthy(self.now, path=self.hb, uid=os.getuid() + 1)[0])

    def test_a_link_in_a_folder_on_the_way_is_not_followed_for_another_users_heartbeat(self):
        """of_review and of_secrets-review on PR 144: only the last component was O_NOFOLLOW."""
        real = os.path.join(self.tmp.name, "real-state")
        os.makedirs(real)
        with open(os.path.join(real, of_relay_client.HEARTBEAT), "w") as fh:
            json.dump({"ts": self.now, "pid": 1, "loops": {"presence": self.now}}, fh)
        linked = os.path.join(self.tmp.name, "linked")
        os.symlink(real, linked)
        hb = os.path.join(linked, of_relay_client.HEARTBEAT)
        self.assertTrue(of_relay_client.relay_healthy(self.now, path=hb)[0])  # its own: as before
        self.assertEqual(of_relay_client.relay_healthy(self.now, path=hb, uid=os.getuid()),
                         (False, None, "no heartbeat"))
        self.assertTrue(of_relay_client.relay_healthy(self.now, path=os.path.join(real, of_relay_client.HEARTBEAT),
                                                      uid=os.getuid())[0])

    def test_unsafe_code_restarts_nothing_and_says_why(self):
        prog = os.path.join(self.tmp.name, "python3")
        open(prog, "w").close()
        os.chmod(prog, 0o755)
        self.beat(self.now - 1000)
        logged = []
        ok = dict(code=[prog], owner_ok=lambda uid: True)
        got, kicked, _ = self.check(**ok)
        self.assertEqual(got[3]["of-server"], "restarted")                  # safe code: as before
        os.chmod(prog, 0o777)
        with mock.patch.object(of_server, "_health", lambda c, timeout=None: None):
            got = of_server.host_watchdog(self.cfg, [], now=self.now + 900, log=logged.append, state_dir=self.state,
                                          pid_of=lambda label: os.getpid(), restart=lambda: self.fail("restarted"),
                                          dump=lambda p: self.fail("signalled"), **ok)
        self.assertEqual(got, {"code": "unsafe"})
        self.assertIn(f"{prog}: writable by others", logged[0])
        got = of_server.host_watchdog(self.cfg, [], now=self.now, log=logged.append, state_dir=self.state,
                                      code=[os.path.join(self.tmp.name, "gone")], owner_ok=lambda uid: True)
        self.assertEqual(got, {"code": "unsafe"})

    def test_one_relays_failure_does_not_stop_the_others(self):
        self.beat(self.now)
        other = os.path.join(self.tmp.name, "other")
        os.makedirs(other)

        def uid_of(user):
            if user == "gone":
                raise KeyError(user)
            return os.getuid()
        logged = []
        with mock.patch.object(of_server, "_health", lambda c, timeout=None: {}):
            got = of_server.host_watchdog(self.cfg, [("gone", "x.gone", other), ("../x", "x.bad", other),
                                                     ("of", "dev.organic-framework.relay.of", self.of_state)],
                                          now=self.now, log=logged.append, state_dir=self.state,
                                          pid_of=lambda label: None, uid_of=uid_of)
        self.assertEqual(got, {"of-server": "ok", "relay gone": "error", "relay ../x": "error", "relay of": "ok"})
        self.assertEqual(len(logged), 2)
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.state), "watchdog-relay-..")))

    def test_its_state_folder_must_be_its_own_and_a_planted_link_is_never_written_through(self):
        os.makedirs(self.state)
        os.chmod(self.state, 0o777)
        with self.assertRaises(SystemExit):
            self.check()
        os.chmod(self.state, 0o700)
        victim = os.path.join(self.tmp.name, "victim")
        with open(victim, "w") as fh:
            fh.write("keep")
        os.symlink(victim, os.path.join(self.state, "watchdog.json.tmp"))
        os.symlink(victim, os.path.join(self.state, "watchdog-relay-of.json"))
        self.beat(self.now)
        self.check()
        self.assertEqual(open(victim).read(), "keep")
        linked = os.path.join(self.tmp.name, "linked-state")
        os.rename(self.state, linked)
        os.symlink(linked, self.state)
        with self.assertRaises(SystemExit):
            self.check()


if __name__ == "__main__":
    unittest.main()
