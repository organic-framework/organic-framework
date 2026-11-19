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
                    f"git config core.pager 'cat {KEY}'",
                    f"P='cat {KEY}' git --config-env=core.pager=P log", f"export P='cat {KEY}'; git --config-env core.pager=P log",
                    # of_review on organic-framework PR 400 at 0e8ebb2
                    f"git config --file .git/config core.pager 'cat {KEY}'", f"git config set core.pager 'cat {KEY}'",
                    f"git -c credential.helper='/bin/cat {KEY}' fetch",
                    f"git -c 'credential.https://example.com.helper=!cat {KEY}' fetch",
                    f"git -c filter.a.b.clean='cat {KEY}' add x", f"git -c difftool.x.cmd='cat {KEY}' difftool",
                    f"git -c mergetool.x.cmd='cat {KEY}' mergetool", f"git -c core.alternateRefsCommand='cat {KEY}' fetch",
                    f"git -c tar.tgz.command='cat {KEY}' archive HEAD", f"git config --global --add core.pager 'cat {KEY}'",
                    # Codex on PR 400's review: guitool, gc's hook, trailers, a submodule's update, sendemail
                    f"git -c guitool.x.cmd='cat {KEY}' gui", f"git -c gc.recentObjectsHook='cat {KEY}' gc",
                    f"git -c trailer.x.cmd='cat {KEY}' interpret-trailers", f"git -c submodule.a.update='!cat {KEY}' submodule update",
                    f"git -c sendemail.toCmd='cat {KEY}' send-email x",
                    # of_review's notes on PR 400 after it merged
                    f"git config set -t path core.pager 'cat {KEY}'", f"git -c sendemail.sendmailCmd='cat {KEY}' send-email x",
                    # an option's value is no mode (of_secrets-review on PR 403 at 86494fc)
                    f"git config core.pager 'cat {KEY}' --show-origin", f"git config --comment -e core.pager 'cat {KEY}'",
                    f"git config --comment --list core.pager 'cat {KEY}'", f"git config --comment -l core.pager 'cat {KEY}'",
                    f"git config --file -l core.pager 'cat {KEY}'",
                    # a mode word after KEY VALUE is a value-pattern (of_review on PR 403 at 86494fc)
                    f"git config core.pager 'cat {KEY}' -l", f"git config set --comment --get core.pager 'cat {KEY}'"):
            self.assertTrue(deny(cmd), cmd)
        for cmd in ("git -c core.pager=less log", "git log", "GIT_PAGER=cat git log", "git config core.pager less",
                    "git -c color.ui=always log", "git -c alias.st=status st", "git -c credential.helper=store fetch",
                    "git config --file .git/config core.pager less", "git config get core.pager", "git config --list",
                    "git config --get core.pager printenv", "git config --unset core.pager printenv"):
            self.assertIsNone(deny(cmd), cmd)


if __name__ == "__main__":
    unittest.main()
