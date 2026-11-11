"""The grant key moves into the owner's 1Password (the founder, ask 0fd223f2; hand-over 4c7ac78f): `of grant rekey`
re-signs every grant through his SSH agent, pins the new key and removes the old key file; afterwards every grant
is signed through the agent. A throwaway ssh-agent of the test's own stands in for 1Password's."""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import of_grants  # noqa: E402


def keygen(path):
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", path], check=True,
                   env=of_grants.no_agent_env())


class RekeyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ofk", dir="/tmp")    # a short path: a socket's name has a length limit
        t = self.tmp
        self.old, self.new = os.path.join(t, "grant_ed25519"), os.path.join(t, "new_ed25519")
        keygen(self.old)
        keygen(self.new)
        self.sock = os.path.join(t, "a.sock")
        out = subprocess.run(["ssh-agent", "-s", "-a", self.sock], capture_output=True, text=True,
                             env=of_grants.no_agent_env(), check=True).stdout
        self.agent_pid = int(out.split("SSH_AGENT_PID=")[1].split(";")[0])
        subprocess.run(["ssh-add", "-q", self.new], env=dict(os.environ, SSH_AUTH_SOCK=self.sock), check=True,
                       capture_output=True)
        self.staged = os.path.join(t, "grant_1password.pub")
        shutil.copy(self.new + ".pub", self.staged)
        os.remove(self.new)                                            # the private half is only in the agent now
        self.saved = (of_grants.GRANT_KEY, os.environ.get("OF_GRANT_AGENT_SOCK"))
        of_grants.GRANT_KEY = self.old
        os.environ["OF_GRANT_AGENT_SOCK"] = self.sock
        self.orgs = os.path.join(t, "orgs.yaml")
        with open(self.orgs, "w") as fh:
            fh.write("orgs:\n  - {name: a, path: a}\n  - {name: b, path: b}\n")
        self.files = {}
        for org, ids in (("a", ["g-1", "g-2"]), ("b", ["g-3"])):
            os.makedirs(os.path.join(t, org, "registry"))
            path = os.path.join(t, org, "registry", "grants.yaml")
            import yaml
            grants = []
            for gid in ids:
                g = {"id": gid, "subject": "x_keeper", "issuer": "tenant", "tier": "observe", "effect": "allow",
                     "scope": {"repos": ["~/x"]}, "expires": None, "counter": None, "revoked": None}
                g["signature"] = of_grants.sign_ed25519(g, self.old)
                grants.append(g)
            with open(path, "w") as fh:
                yaml.safe_dump({"grants": grants}, fh, sort_keys=False)
            self.files[org] = path

    def tearDown(self):
        of_grants.GRANT_KEY = self.saved[0]
        if self.saved[1] is None:
            os.environ.pop("OF_GRANT_AGENT_SOCK", None)
        else:
            os.environ["OF_GRANT_AGENT_SOCK"] = self.saved[1]
        try:
            os.kill(self.agent_pid, signal.SIGTERM)
        except OSError:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def grants(self):
        import yaml
        return [g for p in self.files.values() for g in (yaml.safe_load(open(p)) or {}).get("grants") or []]

    def keys(self, path):
        with open(path) as fh:
            return of_grants.Keys(ed25519=[fh.read().strip()])

    def test_every_grant_moves_to_the_agents_key_and_the_old_key_is_gone(self):
        old_keys = self.keys(self.old + ".pub")
        self.assertTrue(all(of_grants.verify(g, old_keys) for g in self.grants()))
        new_keys = self.keys(self.staged)
        paths, n, line = of_grants.rekey(self.staged, self.orgs, self.sock)
        self.assertEqual((n, sorted(paths)), (3, sorted(self.files.values())))
        self.assertTrue(all(of_grants.verify(g, new_keys) and not of_grants.verify(g, old_keys) for g in self.grants()))
        self.assertEqual(open(of_grants.pin_path()).read().split()[:2], line.split()[:2])
        self.assertFalse(os.path.exists(self.old))                     # the old private key file is removed
        self.assertFalse(os.path.exists(self.staged))
        self.assertTrue(open(self.files["a"]).read().startswith("# Signed grants"))
        # from now on a new grant is signed through the agent with the pinned key
        self.assertTrue(of_grants.signs_with_agent())
        g = {"id": "g-4", "subject": "y", "issuer": "tenant", "tier": "observe", "scope": {}}
        g["signature"] = of_grants.sign_ed25519(g)
        self.assertTrue(of_grants.verify(g, self.keys(of_grants.pin_path())))

    def test_nothing_changes_when_the_agent_does_not_offer_the_key(self):
        subprocess.run(["ssh-add", "-D"], env=dict(os.environ, SSH_AUTH_SOCK=self.sock), capture_output=True)
        before = {p: open(p).read() for p in self.files.values()}
        pin_before = open(self.old + ".pub").read()
        with self.assertRaises(RuntimeError) as cm:
            of_grants.rekey(self.staged, self.orgs, self.sock)
        self.assertIn("does not offer this key", str(cm.exception))
        self.assertEqual({p: open(p).read() for p in self.files.values()}, before)
        self.assertEqual(open(self.old + ".pub").read(), pin_before)
        self.assertTrue(os.path.exists(self.old))

    def test_a_staged_file_that_is_not_one_ed25519_line_is_refused(self):
        with open(self.staged, "w") as fh:
            fh.write(textwrap.dedent("not a key\n"))
        with self.assertRaises(ValueError):
            of_grants.rekey(self.staged, self.orgs, self.sock)


if __name__ == "__main__":
    unittest.main()
