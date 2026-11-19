"""The machine key is made once, also when two processes start at the same time (bin/of_client.py ensure_key).

of_platform, 2026-11-19: on the agent host the listener and the relay started together under systemd, both saw no
key, the first made it and the second's ssh-keygen asked "Overwrite (y/n)?" with no terminal and failed."""

import os
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_client  # noqa: E402


def derived(path):
    r = subprocess.run(["ssh-keygen", "-y", "-f", path], capture_output=True, text=True, check=True,
                       env=of_client.no_agent_env())
    return r.stdout.split()[:2]


class EnsureKeyTest(unittest.TestCase):

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="of-key-")
        self.path = os.path.join(self.d, "state", "machine_key")

    def test_two_callers_at_once_get_one_matching_pair(self):
        got, errors = [], []

        def call():
            try:
                got.append(of_client.ensure_key(self.path))
            except Exception as e:  # noqa: BLE001
                errors.append(e)
        threads = [threading.Thread(target=call) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(got)), 1)                                  # every caller has the same public half
        self.assertEqual(got[0].split()[:2], derived(self.path))            # and it is the private half's pair
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.path))), ["machine_key", "machine_key.pub"])
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_a_private_half_alone_gets_its_public_half_derived(self):
        pub = of_client.ensure_key(self.path)
        os.unlink(self.path + ".pub")
        of_client._KEY_CHECKED.discard(self.path)
        self.assertEqual(of_client.ensure_key(self.path).split()[:2], pub.split()[:2])

    def test_a_stale_public_half_is_replaced_by_the_private_halfs_own(self):
        """of_review on organic-framework PR 409: a public half left from an earlier key, older than the private half,
        was returned as it was (a crash between the links, or a reader before the winner's replace)."""
        pub = of_client.ensure_key(self.path)
        other = os.path.join(self.d, "other")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", other], check=True,
                       env=of_client.no_agent_env())
        with open(other + ".pub") as fh, open(self.path + ".pub", "w") as out:
            out.write(fh.read())
        st = os.stat(self.path)
        os.utime(self.path + ".pub", (st.st_atime, st.st_mtime - 60))   # older than the private half
        of_client._KEY_CHECKED.discard(self.path)
        got = of_client.ensure_key(self.path)
        self.assertEqual(got.split()[:2], pub.split()[:2])
        self.assertEqual(got.split()[:2], derived(self.path))
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.path))), ["machine_key", "machine_key.pub"])

    def test_an_existing_pair_is_checked_once_and_never_made_again(self):
        pub = of_client.ensure_key(self.path)
        of_client._KEY_CHECKED.discard(self.path)          # a new process
        calls, real = [], of_client.subprocess.run

        def spy(argv, *a, **k):
            calls.append(argv[1] if len(argv) > 1 else "")
            return real(argv, *a, **k)
        of_client.subprocess.run = spy
        try:
            self.assertEqual(of_client.ensure_key(self.path), pub)
            self.assertEqual(of_client.ensure_key(self.path), pub)
        finally:
            of_client.subprocess.run = real
        self.assertEqual(calls, ["-y"])                    # one check against the private half, no new key

    def test_a_mismatched_half_written_after_the_private_one_is_replaced(self):
        """of_review on organic-framework PR 412: 'no older' stood for 'the pair', so a mismatched half written later
        was kept."""
        pub = of_client.ensure_key(self.path)
        other = os.path.join(self.d, "other2")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", other], check=True,
                       env=of_client.no_agent_env())
        with open(other + ".pub") as fh, open(self.path + ".pub", "w") as out:
            out.write(fh.read())                              # newer than the private half, and not its pair
        of_client._KEY_CHECKED.discard(self.path)
        self.assertEqual(of_client.ensure_key(self.path).split()[:2], pub.split()[:2])
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.path))), ["machine_key", "machine_key.pub"])

if __name__ == "__main__":
    unittest.main()
