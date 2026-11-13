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
        self.assertIn("--relay", units[H.WATCHDOG_LABEL]["ProgramArguments"])
        for u in units.values():
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


class HostWatchdogTest(unittest.TestCase):
    def test_the_server_and_each_relay_with_system_daemons(self):
        with tempfile.TemporaryDirectory() as t:
            state, of_state = os.path.join(t, "state"), os.path.join(t, "of")
            os.makedirs(state)
            os.makedirs(of_state)
            with open(os.path.join(state, "server.pid"), "w") as fh:
                fh.write(f"{os.getpid()}\n")
            now = 1_800_000_000.0
            with open(os.path.join(of_state, of_relay_client.HEARTBEAT), "w") as fh:
                json.dump({"ts": now - 1000, "pid": os.getpid(), "loops": {"presence": now - 1000}}, fh)
            cfg = dict(of_server.DEFAULTS, state_dir=state)
            kicked = []
            with mock.patch.object(of_server, "_health", lambda c, timeout=None: None), \
                    mock.patch.object(of_relay_client, "launchd_pid", lambda label, domain=None: os.getpid()), \
                    mock.patch.object(of_server.of_events, "emit", lambda *a, **k: None), \
                    mock.patch.object(of_server.subprocess, "run",
                                      lambda argv, **k: kicked.append(argv) or mock.Mock(returncode=0)):
                got = [of_server.host_watchdog(cfg, [("of", "dev.organic-framework.relay.of", of_state)],
                                               now=now + 60 * i, dump=lambda p: None, log=lambda m: None)
                       for i in range(5)]
        self.assertEqual([g["of-server"] for g in got], ["failing"] * 3 + ["restarted", "waiting"])
        self.assertEqual([g["relay of"] for g in got], ["failing"] * 3 + ["restarted", "waiting"])
        self.assertIn(["launchctl", "kickstart", "-k", "system/dev.organic-framework.server"], kicked)
        self.assertIn(["launchctl", "kickstart", "-k", "system/dev.organic-framework.relay.of"], kicked)


if __name__ == "__main__":
    unittest.main()
