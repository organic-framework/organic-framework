"""`of server enroll-machine` keeps the tenant's machine the tenant's (of_lab's clean-VM walkthrough of the questions
panel over two contours, 2026-11-19, finding 3: re-enrolling it without --tenant made it a plain machine silently, and
`of invite` then refused it as no tenant)."""

import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
OF = os.path.join(HERE, "..", "bin", "of")
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_server  # noqa: E402


class EnrollMachineTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = self.tmp.name
        self.state = os.path.join(home, "server")
        os.makedirs(os.path.join(home, ".config", "of"))
        with open(os.path.join(home, ".config", "of", "server.yaml"), "w") as fh:
            fh.write(f"state_dir: {self.state}\n")
        self.pub = os.path.join(home, "m.pub")
        with open(self.pub, "w") as fh:
            fh.write("ssh-ed25519 AAAAexample m1\n")
        self.env = dict(os.environ, HOME=home, OF_AUTOMATION="1", SSH_AUTH_SOCK="")
        self.store = of_server.Store(self.state)
        of_server.enroll(self.store, "m1", "ssh-ed25519 AAAAexample m1", ["acme"], tenant=True)

    def tearDown(self):
        self.tmp.cleanup()

    def tenant(self):
        with self.store.conn() as c:
            return c.execute("SELECT tenant FROM machines WHERE machine_id = 'm1'").fetchone()[0]

    def of(self, *args):
        return subprocess.run([sys.executable, OF, "server", "enroll-machine", "--machine-id", "m1", "--pubkey",
                               self.pub, *args], env=self.env, capture_output=True, text=True, timeout=60)

    def test_the_tenant_stays_the_tenant_unless_told(self):
        r = self.of("--org", "acme")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("stays it", r.stdout)
        self.assertEqual(self.tenant(), 1)
        r = self.of("--org", "acme", "--no-tenant")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.tenant(), 0)

    def key(self, store=None):
        with (store or self.store).conn() as c:
            row = c.execute("SELECT pubkey FROM machines WHERE machine_id = 'm1'").fetchone()
        return row and row[0]

    def test_a_new_key_for_an_enrolled_machine_needs_replace_key(self):
        # 2026-11-23: a lab test's re-enrolment with a scratch key took this machine's place on its live server
        with open(self.pub, "w") as fh:
            fh.write("ssh-ed25519 AAAAother m1\n")
        r = self.of("--org", "acme")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("another key", r.stderr)
        self.assertEqual(self.key(), "ssh-ed25519 AAAAexample m1")
        r = self.of("--org", "acme", "--replace-key")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.key(), "ssh-ed25519 AAAAother m1")

    def test_enroll_itself_refuses_a_new_key_without_replace_key(self):
        # every caller, `of init` among them, which a scratch OF_CONFIG_DIR gives a fresh key for the real machine id
        with self.assertRaises(of_server.EnrollError) as e:
            of_server.enroll(self.store, "m1", "ssh-ed25519 AAAAscratch x", ["lab"], tenant=True)
        self.assertEqual(e.exception.status, 409)
        self.assertEqual(self.key(), "ssh-ed25519 AAAAexample m1")
        of_server.enroll(self.store, "m1", "ssh-ed25519 AAAAexample renamed", ["acme", "beta"], tenant=True)
        of_server.revoke(self.store, "m1")
        of_server.enroll(self.store, "m1", "ssh-ed25519 AAAAnew m1", ["acme"], tenant=True)  # revoked: a new key is fine
        self.assertEqual(self.key(), "ssh-ed25519 AAAAnew m1")

    def test_one_server_yaml_for_every_reader_and_writer(self):
        """of_secrets-review on PR 590: of init wrote server.yaml in the config folder and read ~/.config/of's."""
        import of_events
        saved, env = of_events.CONFIG_DIR, os.environ.pop("OF_SERVER_CONFIG", None)
        try:
            of_events.CONFIG_DIR = os.path.join(self.tmp.name, "cfg")
            self.assertEqual(of_server.config_file(), os.path.join(self.tmp.name, "cfg", "server.yaml"))
            os.environ["OF_SERVER_CONFIG"] = os.path.join(self.tmp.name, "named.yaml")
            self.assertEqual(of_server.config_file(), os.path.join(self.tmp.name, "named.yaml"))
            with open(os.path.join(self.tmp.name, "named.yaml"), "w") as fh:
                fh.write("state_dir: /srv/elsewhere\n")
            self.assertEqual(of_server.load_config()["state_dir"], "/srv/elsewhere")
        finally:
            of_events.CONFIG_DIR = saved
            os.environ.pop("OF_SERVER_CONFIG", None)
            if env is not None:
                os.environ["OF_SERVER_CONFIG"] = env

    def test_the_servers_own_enroll_needs_replace_key_too(self):
        cfg = os.path.join(self.tmp.name, ".config", "of", "server.yaml")
        run = lambda *extra: subprocess.run(
            [sys.executable, os.path.join(HERE, "..", "bin", "of_server.py"), "--config", cfg, "enroll", "--machine-id",
             "m1", "--pubkey", "ssh-ed25519 AAAAother m1", "--org", "acme", *extra],
            env=self.env, capture_output=True, text=True, timeout=60)
        r = run()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("enrolled with another key", r.stderr)
        self.assertEqual(self.key(), "ssh-ed25519 AAAAexample m1")
        r = run("--replace-key")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.key(), "ssh-ed25519 AAAAother m1")

    def test_the_same_key_with_another_comment_is_no_new_key(self):
        with open(self.pub, "w") as fh:
            fh.write("ssh-ed25519 AAAAexample renamed\n")
        r = self.of("--org", "acme")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_of_server_config_names_the_server_it_writes(self):
        other = os.path.join(self.tmp.name, "scratch-server")
        cfg = os.path.join(self.tmp.name, "scratch.yaml")
        with open(cfg, "w") as fh:
            fh.write(f"state_dir: {other}\n")
        with open(self.pub, "w") as fh:
            fh.write("ssh-ed25519 AAAAscratch m1\n")
        r = subprocess.run([sys.executable, OF, "server", "enroll-machine", "--machine-id", "m1", "--pubkey", self.pub,
                            "--org", "lab"], env=dict(self.env, OF_SERVER_CONFIG=cfg), capture_output=True, text=True,
                           timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.key(of_server.Store(other)), "ssh-ed25519 AAAAscratch m1")
        self.assertEqual(self.key(), "ssh-ed25519 AAAAexample m1")  # the server of ~/.config/of is left as it was


if __name__ == "__main__":
    unittest.main()
