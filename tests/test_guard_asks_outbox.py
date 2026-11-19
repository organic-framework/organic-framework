"""G35: the asks outbox is the owner's popup's (docs/guard.md G35).

of_platform, msg 01a1f4fc-801d, 2026-11-19: the popup's sender replays each file in ~/.local/state/of/asks-outbox as
the owner's answer or mark, so a file an agent wrote there would record an answer by the owner, past G31 and the
positive owner test of `of asks answered|withdraw`."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_guard  # noqa: E402

BOX = "~/.local/state/of/asks-outbox"


def bash(cmd, cwd=None):
    hit = of_guard.critical_deny("Bash", {"command": cmd}, cwd)
    return hit[0] if hit else None


class AsksOutboxTest(unittest.TestCase):

    def test_the_file_tools_never_write_there(self):
        home = os.path.expanduser("~")
        for tool in ("Write", "Edit", "MultiEdit"):
            for path in (f"{home}/.local/state/of/asks-outbox/a.json", f"{home}/.LOCAL/state/of/ASKS-OUTBOX/a.json"):
                with self.subTest(tool=tool, path=path):
                    hit = of_guard.critical_deny(tool, {"file_path": path})
                    self.assertEqual(hit and hit[0], of_guard.ASKS_OUTBOX_DENY)
        self.assertIsNone(of_guard.critical_deny("Read", {"file_path": f"{home}/.local/state/of/asks-outbox/a.json"}))
        self.assertIsNone(of_guard.critical_deny("Write", {"file_path": f"{home}/.local/state/of/asks-outbox-notes.md"}))

    def test_a_shell_write_move_copy_or_removal_into_it_is_refused(self):
        for cmd in (f"echo '{{}}' > {BOX}/a.json", f"echo x >> {BOX}/a.json", f"cp /x/answer.json {BOX}/",
                    f"mv answer.json {BOX}/b.json", f"printf x | tee {BOX}/c.json",
                    f"ln -s /x/answer.json {BOX}/e.json", "rm -r ~/.local/state/of", f"rm {BOX}/a.json",
                    "cp -R ./state ~/.local/state/of", f"echo x > {BOX}/*.json"):
            with self.subTest(cmd=cmd):
                self.assertEqual(bash(cmd), of_guard.ASKS_OUTBOX_DENY)

    def test_reading_it_and_writing_beside_it_stay_open(self):
        for cmd in (f"ls {BOX}", f"cat {BOX}/a.json", "echo x > ~/.local/state/of/notes.txt",
                    "cp a.json /private/tmp/x/asks-outbox-copy.json"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(bash(cmd))


if __name__ == "__main__":
    unittest.main()
