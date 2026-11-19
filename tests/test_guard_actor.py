"""docs/guard.md G31: `of` run with the variables that name its caller cleared (of_secrets-review, msg 01a1ef00-6f57,
2026-11-18, on organic-framework PR 334: with CLAUDE_CODE_SESSION_ID and OF_POSITION unset, `of asks answered` and
`of status --done-task` record the act as the owner's, and an agent's shell can unset them)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_guard  # noqa: E402


def deny(cmd):
    return of_guard.critical_deny("Bash", {"command": cmd}, cwd="/Users/alex/of")


class ActorTest(unittest.TestCase):

    def test_clearing_the_caller_before_of_is_denied(self):
        for cmd in ("env -u CLAUDE_CODE_SESSION_ID -u OF_POSITION of asks answered AS-1 --option 1",
                    "env -u OF_POSITION of asks withdraw 0a1b",
                    "env --unset=CLAUDE_CODE_SESSION_ID of asks answered AS-1",
                    "env -uOF_POSITION of asks answered AS-1",
                    "CLAUDE_CODE_SESSION_ID= OF_POSITION= of asks answered AS-1",
                    'CLAUDE_CODE_SESSION_ID="" of asks answered AS-1',
                    "unset CLAUDE_CODE_SESSION_ID OF_POSITION; of asks answered AS-1",
                    "export OF_POSITION=; of asks withdraw 0a1b",
                    "env -i PATH=/usr/bin:/bin of asks answered AS-1",
                    "env -i bash -c 'of asks answered AS-1'",
                    "unset CLAUDE_CODE_SESSION_ID; bash -c \"of asks answered AS-1\"",
                    "env -u CLAUDE_CODE_SESSION_ID python3 bin/of asks answered AS-1",
                    "env -u CLAUDE_CODE_SESSION_ID nice -n 5 of asks answered AS-1",
                    "(unset OF_POSITION; of asks answered AS-1)",
                    # a HOME of its own is no way out: its state can be a link to the real one
                    "env -i HOME=/private/tmp/x/home PATH=/usr/bin:/bin of asks answered AS-1"):
            got = deny(cmd)
            self.assertTrue(got, cmd)
            self.assertIn("G31", got[0], cmd)

    def test_the_keepers_list_writers_keep_their_caller_too(self):
        """of_review on organic-framework PR 357: `of access grant` with the session id cleared passed as the owner."""
        for cmd in ("env -u CLAUDE_CODE_SESSION_ID of access grant k p item:x --tier secret --decided AC-1 --org acme",
                    "CLAUDE_CODE_SESSION_ID= of access revoke k p item:x --why x --org acme",
                    "env -i python3 bin/of access record k p item:x --org acme",
                    "env -u OF_POSITION of access link k p item:x"):
            got = of_guard.critical_deny("Bash", {"command": cmd}, cwd="/Users/alex/of")
            self.assertTrue(got and "G31" in got[0], cmd)
        self.assertIsNone(of_guard.critical_deny("Bash", {"command": "of access map --org acme"}, cwd="/Users/alex/of"))
        self.assertIsNone(of_guard.critical_deny("Bash", {"command": "env -u CLAUDE_CODE_SESSION_ID of access map"},
                                                 cwd="/Users/alex/of"))

    def test_what_names_the_caller_or_runs_no_of_passes(self):
        for cmd in ("of asks answered AS-1 --option 1", "of status working --done-task 0a1b",
                    "env -u HERDR_ENV -u HERDR_PANE_ID of asks", "unset FOO; of status working",
                    "env -u CLAUDE_CODE_SESSION_ID python3 -m unittest discover -s tests",
                    "env -i PATH=/usr/bin:/bin OF_POSITION=of_x CLAUDE_CODE_SESSION_ID=s1 of status working",
                    "of send of_x \"env -u OF_POSITION of asks answered: a gap, written up\"",
                    "unset CLAUDE_CODE_SESSION_ID; echo done",
                    # after the replay: the cleared runs it found were labs and dry runs of acts that record no caller
                    "env -i HOME=/private/tmp/x/home PATH=/usr/bin:/bin of model measures --org acme",
                    "env -i HOME=$HOME PATH=/usr/bin:/bin of hook Stop",
                    "env -u CLAUDE_CODE_SESSION_ID -u OF_POSITION of who",
                    "env -u OF_POSITION of status working --done-task 0a1b"):
            self.assertIsNone(deny(cmd), cmd)


if __name__ == "__main__":
    unittest.main()
