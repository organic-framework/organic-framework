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
        saved = of_client.time.sleep
        of_client.time.sleep = lambda s: None
        try:
            self.assertEqual(of_client.ensure_key(self.path).split()[:2], pub.split()[:2])
        finally:
            of_client.time.sleep = saved

    def test_an_existing_pair_runs_no_keygen(self):
        pub = of_client.ensure_key(self.path)
        saved = of_client.subprocess.run
        of_client.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(AssertionError("ssh-keygen ran"))
        try:
            self.assertEqual(of_client.ensure_key(self.path), pub)
        finally:
            of_client.subprocess.run = saved


if __name__ == "__main__":
    unittest.main()
