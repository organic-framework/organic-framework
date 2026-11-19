"""docs/guard.md G34: the forward's own key and host pin are its launchd unit's (of_review on organic-framework PR 350:
`ssh -i` is use, which G1 allows, so a laptop session could open its own forward with that key). Nothing here reads
a key: the guard judges by path."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_guard  # noqa: E402

HOME = os.path.realpath(os.path.expanduser("~"))
DIR = "~/.ssh/of-forward"


def bash(cmd):
    return of_guard.critical_deny("Bash", {"command": cmd}, cwd=HOME)


class ForwardKeyTest(unittest.TestCase):

    def test_an_agent_session_never_names_the_forwards_folder(self):
        key = DIR + "/id_" + "ed25519"
        for cmd in (f"ssh -i {key} -N -R /tmp/x.sock:127.0.0.1:1 of-forward@192.0.2.10",
                    f"ssh -o IdentityFile={key} of-forward@192.0.2.10", f"cat {DIR}/known_hosts",
                    f"cp {key} /tmp/k", f'ssh -i "$HOME/.ssh/of-forward/id_ed25519" h', f"ls {DIR}"):
            got = bash(cmd)
            self.assertTrue(got and "G34" in got[0], cmd)
        for tool in ("Read", "Edit", "Write"):
            got = of_guard.critical_deny(tool, {"file_path": os.path.join(HOME, ".ssh", "of-forward", "known_hosts"),
                                                "content": "x", "old_string": "a", "new_string": "b"}, cwd=HOME)
            self.assertTrue(got and "G34" in got[0], tool)

    def test_the_forwards_own_commands_and_other_keys_pass(self):
        for cmd in ("python3 deploy/host/forward.py keygen", "python3 deploy/host/forward.py status",
                    "python3 deploy/host/forward.py pin-host --to of-forward@192.0.2.10 --fingerprint SHA256:x",
                    "python3 deploy/host/forward.py install --to of-forward@192.0.2.10 --dry-run",
                    "ssh -i ~/.ssh/aws-tokyo.pem ubuntu@h hostname", "grep -n of-forward docs/guard.md"):
            self.assertIsNone(bash(cmd), cmd)



class ForwardLineVariables(unittest.TestCase):
    """G34 through a line's variable (of_secrets-review on organic-framework PR 397; hand-over b657e8bf)."""

    def test_a_path_held_in_a_variable(self):
        folder = "~/.ssh/of-" + "forward"
        for cmd in (f"K={folder}/key; ssh -i $K host", "d=of-" + "forward; cat ~/.ssh/$d/key",
                    f"K=$HOME/.ssh/of-{'forward'}; ls $K", f"export F={folder}/key; ssh -i \"$F\" h",
                    f"K={folder}; cp /tmp/k \"${{K}}/key\""):
            self.assertTrue(of_guard.critical_deny("Bash", {"command": cmd}, cwd="/Users/alex/of"), cmd)
        for cmd in ("K=~/.ssh/other_key; ssh -i $K host", "d=of-other; ls ~/.ssh/$d"):
            self.assertIsNone(of_guard.critical_deny("Bash", {"command": cmd}, cwd="/Users/alex/of"), cmd)

if __name__ == "__main__":
    unittest.main()
