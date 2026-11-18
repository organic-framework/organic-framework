"""deploy/host/forward.py: the laptop's reverse forward to the agent host (docs/server-several-machines.md 4.8). Pure
logic: a fake launchctl, a temporary home; no ssh runs."""

import importlib.util
import os
import plistlib
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("host_forward", os.path.join(HERE, "..", "deploy", "host", "forward.py"))
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)


class Run:
    def __init__(self, code=0):
        self.calls, self.code = [], code

    def __call__(self, argv, **kw):
        self.calls.append(argv)

        class R:
            returncode, stdout, stderr = self.code, "", "boom"
        return R()


class ForwardTest(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.key = os.path.join(self.home, "fwd_ed25519")
        with open(self.key, "w") as fh:
            fh.write("not a key\n")
        with open(self.key + ".pub", "w") as fh:
            fh.write("ssh-ed25519 AAAAexample of-forward\n")

    def test_ssh_never_uses_an_agent_and_ends_on_a_failed_forward(self):
        argv = F.ssh_argv("alex@192.0.2.10", self.key)
        self.assertEqual(argv[:4], ["/usr/bin/ssh", "-N", "-R", "127.0.0.1:18200:127.0.0.1:18201"])  # to TLS
        opts = {argv[i + 1] for i, a in enumerate(argv) if a == "-o"}
        for o in ("IdentityAgent=none", "IdentitiesOnly=yes", "BatchMode=yes", "ForwardAgent=no",
                  "ExitOnForwardFailure=yes", "ServerAliveInterval=30", "ServerAliveCountMax=3"):
            self.assertIn(o, opts)
        self.assertEqual(argv[argv.index("-i") + 1], self.key)
        self.assertIn("StrictHostKeyChecking=yes", opts)
        self.assertTrue(any(o.startswith("UserKnownHostsFile=") and o.endswith("of-forward/known_hosts") for o in opts))
        self.assertEqual(argv[-1], "alex@192.0.2.10")

    def test_the_unit_is_automation_with_no_agent_socket(self):
        u = F.unit("alex@192.0.2.10", self.key, home=self.home)
        self.assertEqual(u["Label"], "dev.organic-framework.forward")
        self.assertEqual(u["EnvironmentVariables"], {"OF_AUTOMATION": "1", "SSH_AUTH_SOCK": ""})
        self.assertTrue(u["KeepAlive"])
        self.assertTrue(u["StandardErrorPath"].startswith(self.home))

    def test_the_hosts_line_and_block_allow_this_forward_only(self):
        self.assertEqual(F.authorized_line("ssh-ed25519 AAAAexample of-forward\n"),
                         'restrict,port-forwarding,permitlisten="127.0.0.1:18200",command="/usr/bin/false" '
                         'ssh-ed25519 AAAAexample of-forward')
        block = F.sshd_block("of-forward")
        for line in ("Match User of-forward", "AllowTcpForwarding remote", "PermitListen 127.0.0.1:18200",
                     "PermitOpen none", "AllowStreamLocalForwarding no", "GatewayPorts no", "PermitTTY no",
                     "MaxSessions 0", "ForceCommand /usr/bin/false"):
            self.assertIn(line, block)
        self.assertTrue(block.rstrip().endswith("Match all"))  # the main config after the Include stays global

    def test_dry_run_writes_nothing_and_install_loads_the_unit(self):
        run, shown = Run(), []
        F.install("alex@192.0.2.10", self.key, dry_run=True, home=self.home, run=run, out=shown.append)
        self.assertEqual(run.calls, [])
        self.assertFalse(os.path.exists(F.agent_path(self.home)))
        self.assertTrue(any('permitlisten="127.0.0.1:18200"' in s for s in shown))
        self.assertTrue(any("Match User alex" in s for s in shown))
        F.install("alex@192.0.2.10", self.key, home=self.home, run=run, out=shown.append)
        with open(F.agent_path(self.home), "rb") as fh:
            self.assertEqual(plistlib.load(fh)["ProgramArguments"][-1], "alex@192.0.2.10")
        self.assertEqual([c[:2] for c in run.calls], [["launchctl", "bootout"], ["launchctl", "bootstrap"]])

    def test_keygen_prints_only_the_public_half_and_pin_host_checks_the_fingerprint(self):
        key = os.path.join(self.home, "k", "id_ed25519")
        shown = []

        def fake_keygen(argv, **kw):
            self.assertEqual(kw["env"]["SSH_AUTH_SOCK"], "")
            with open(argv[argv.index("-f") + 1] + ".pub", "w") as fh:
                fh.write("ssh-ed25519 AAAAnew of-forward\n")
            open(argv[argv.index("-f") + 1], "w").close()
            return Run()(argv)
        pub = F.keygen(key, run=fake_keygen, out=shown.append)
        self.assertEqual(pub, "ssh-ed25519 AAAAnew of-forward")
        self.assertEqual(os.stat(os.path.dirname(key)).st_mode & 0o777, 0o700)
        self.assertFalse(any("PRIVATE" in s for s in shown))

        class R:
            def __init__(self, out):
                self.returncode, self.stdout, self.stderr = 0, out, ""

        def fake(argv, **kw):
            if argv[0] == "ssh-keyscan":
                return R("192.0.2.10 ssh-ed25519 AAAAhost\n")
            return R("256 SHA256:good 192.0.2.10 (ED25519)\n")
        known = os.path.join(self.home, "k", "known_hosts")
        with self.assertRaises(SystemExit):
            F.pin_host("of-forward@192.0.2.10", "SHA256:other", known=known, run=fake, out=shown.append)
        self.assertFalse(os.path.exists(known))
        F.pin_host("of-forward@192.0.2.10", "SHA256:good", known=known, run=fake, out=shown.append)
        with open(known) as fh:
            self.assertEqual(fh.read(), "192.0.2.10 ssh-ed25519 AAAAhost\n")

    def test_it_refuses_a_missing_key_a_bare_host_and_a_failed_load(self):
        with self.assertRaises(SystemExit):
            F.install("alex@192.0.2.10", os.path.join(self.home, "none"), home=self.home, run=Run())
        with self.assertRaises(SystemExit):
            F.install("192.0.2.10", self.key, home=self.home, run=Run())
        with self.assertRaises(SystemExit):
            F.install("alex@192.0.2.10", self.key, home=self.home, run=Run(code=5), out=lambda s: None)


if __name__ == "__main__":
    unittest.main()
