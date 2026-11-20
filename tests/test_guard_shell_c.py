"""A shell's -c in a cluster of short options is read as -c (Codex runs every command as bash -lc): its body is judged
as the line's own (of_platform, 2026-11-20, found while fixing of_server's review of organic-framework PR 475)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_guard  # noqa: E402

KEY = "~/." + "ssh/id_" + "ed25519"


def denied(cmd):
    return bool(of_guard.critical_deny("Bash", {"command": cmd}))


class ShellClusterTest(unittest.TestCase):
    def test_a_body_after_a_cluster_is_read(self):
        for cmd in (f"bash -lc 'cat {KEY}'", f"/bin/bash -lc 'cat {KEY}'", f"zsh -lc 'cat {KEY}'",
                    f"bash -ec 'cat {KEY}'", f"bash -xc 'cat {KEY}'", f"bash -o pipefail -c 'cat {KEY}'",
                    f"bash +o history -lc 'cat {KEY}'", f"timeout 5 bash -lc 'cat {KEY}'", f"sh -c 'cat {KEY}'"):
            with self.subTest(cmd=cmd):
                self.assertTrue(denied(cmd))

    def test_ordinary_bodies_and_scripts_pass(self):
        for cmd in ("bash -lc 'ls -la'", "bash -lc 'echo hi'", "bash script.sh -c x", "bash -l script.sh"):
            with self.subTest(cmd=cmd):
                self.assertFalse(denied(cmd))


if __name__ == "__main__":
    unittest.main()
