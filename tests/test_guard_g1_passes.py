"""docs/guard.md G0.12 and G0.13: two ways past G1 on main (of_secrets-review, msg 01a1f4fe-b2ad; organic-framework PR 389
comment 7017583957): a command name the shell fills in, and a git config value git runs as a shell command."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_guard  # noqa: E402

KEY = "~/.ssh/id_" + "ed25519"


def deny(cmd):
    return of_guard.critical_deny("Bash", {"command": cmd}, cwd="/Users/alex/of")


class CommandNameByExpansion(unittest.TestCase):

    def test_a_reader_named_by_a_substitution_or_a_variable(self):
        for cmd in (f"$(echo cat) {KEY}", f"$( $( echo cat ) ) {KEY}", f"`echo cat` {KEY}", f"C=cat; $C {KEY}"):
            self.assertTrue(deny(cmd), cmd)
        for cmd in ("$(which python3) -V", "$(command -v git) status", "C=ls; $C -la",
                    "K=kubectl; $K get pods -o custom-columns=N:.metadata.name,T:.spec.secret"):
            self.assertIsNone(deny(cmd), cmd)


class GitShellValues(unittest.TestCase):

    def test_a_value_git_runs_as_shell(self):
        for cmd in (f"git -c core.pager='cat {KEY}' log", f"git -c pager.log='cat {KEY}' log",
                    f"GIT_PAGER='cat {KEY}' git log", f"PAGER='cat {KEY}' git log",
                    f"git -c core.sshCommand='cat {KEY}' fetch", f"git -c core.editor='cat {KEY}' commit",
                    f"git -c diff.external='cat {KEY}' diff", f"git -c alias.x='!cat {KEY}' x",
                    f"git config core.pager 'cat {KEY}'"):
            self.assertTrue(deny(cmd), cmd)
        for cmd in ("git -c core.pager=less log", "git log", "GIT_PAGER=cat git log", "git config core.pager less",
                    "git -c color.ui=always log", "git -c alias.st=status st"):
            self.assertIsNone(deny(cmd), cmd)


if __name__ == "__main__":
    unittest.main()
