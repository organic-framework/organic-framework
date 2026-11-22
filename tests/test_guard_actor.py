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


class ActorLineTest(unittest.TestCase):
    """G31 over the whole line (of_secrets-review's PR 387 comment 7017458194, point 1, and of_review's comment
    7017518350): an act of `of` anywhere on a line with any way to clear or replace its caller's variables."""

    def test_the_reviews_forms(self):
        V, P, A = "CLAUDE_CODE_SESSION_ID", "OF_POSITION", "of asks answered AS-1"
        for cmd in (f"unset {V} {P}; ({A})", f"> /dev/null env -u {V} -u {P} {A}", f"env -P /usr/bin -u {V} -u {P} {A}",
                    f"sh -xc 'unset {V} {P}; {A}'", f"env -i sh -c \"sh -c '{A}'\"",
                    f"env -u {V} -u {P} of access --org demo grant x", f"unset {V} {P}; {V}=\"${V}\" {A}",
                    f"export -n {V} {P}; {A}", f"declare +x {V}; {A}", f"typeset +x {V}; {A}", f"exec -c {A}",
                    f"declare -x {V}=; {A}", f"typeset -x {V}=; {A}", f"{V}=$EMPTY {A}", f"X={V}; unset $X; {A}",
                    f"ssh localhost {A}", f"echo '{A}' | at now", f"osascript -e 'do shell script \"{A}\"'",
                    "python3 -c \"import subprocess; subprocess.run(['of','asks','answered','AS-1'], env={'PATH':'/b'})\"",
                    "launchctl submit -l x -- /Users/alex/organic-framework/bin/of asks answered AS-1",
                    f"'un'set {P}; {A}", f"OF ASKS answered AS-1; unset {P}",
                    # a pseudo-terminal around the act (of_server on hand-over 726902df)
                    f"script -q /dev/null {A}", f"unbuffer {A}", f"expect -c 'spawn {A}; interact'",
                    f"socat - EXEC:'{A}',pty", f"tmux new-session -d '{A}'",
                    "python3 -c \"import pty; pty.spawn(['of','asks','answered','AS-1'])\"",
                    # a reason that begins with --help is a reason (of_review on PR 399 at 7c776fe)
                    f"export -n CLAUDECODE {V} {P}; script -q /dev/null of asks withdraw OF-1 \"--help me\"",
                    f"declare +x CLAUDECODE {V} {P}; script -q /dev/null of asks answered OF-1 \"--help, done\"",
                    f"export -n {V} {P}; script -q /dev/null of asks withdraw OF-1 --help\" me\"",
                    f"export -n {V} {P}; script -q /dev/null of asks withdraw \"OF-1\" -- --help",
                    f"export -n {V} {P}; script -q /dev/null of asks withdraw \"OF-1\" label=--help",
                    # a help call with a clear on the line is refused too (of_review at 45eb458)
                    "false && unset OF_POSITION; of asks withdraw --help",
                    # an option before the action, and an act in a substitution given to text (after PR 399)
                    f"export -n {V} {P}; script -q /dev/null of asks --json withdraw OF-1",
                    # a permission card's No (of_review on PR 443)
                    f"export -n {V} {P}; script -q /dev/null of asks deny AS-1",
                    f"env -u {V} -u {P} script -q /dev/null python3 bin/of asks --dry-run deny AS-1",
                    # an organization's path is the owner's (of_review on PR 427)
                    f"export -n {V} {P}; script -q /dev/null of org set ofb path /tmp/r",
                    f"env -u {V} -u {P} script -q /dev/null python3 bin/of org set ofb path /tmp/r",
                    # and its work clone, writable from every sandbox of the organization (of_review on PR 542)
                    f"export -n {V} {P}; script -q /dev/null of org set ofb work_clone /tmp/r",
                    f"env -u {V} -u {P} script -q /dev/null python3 bin/of org set ofb work_clone /tmp/r",
                    f"export -n {V} {P}; script -q /dev/null of asks --all answered OF-1",
                    f"export -n {V} {P}; printf \"%s\" \"$(script -q /dev/null of asks withdraw OF-1)\"",
                    f"export -n {V} {P}; of send x \"$(script -q /dev/null of asks withdraw OF-1)\"",
                    # a value option before the action (of_review on PR 404 at fef6358)
                    f"export -n {V} {P}; script -q /dev/null of asks --pane p1 withdraw OF-1",
                    f"export -n {V} {P}; script -q /dev/null of asks --text hello answer OF-1",
                    f"export -n {V} {P}; script -q /dev/null of asks --version 2 answer OF-1",
                    f"export -n {V} {P}; script -q /dev/null of asks --pane=p1 withdraw OF-1",
                    # three and four value options before the action (of_review on PR 404 at 86a7b12)
                    f"export -n {V} {P}; script -q /dev/null of asks --pane a --text b --version 2 answered OF-1",
                    f"export -n {V} {P}; script -q /dev/null of asks --pane a --text b --version 2 --prompt p withdraw OF-1"):
            self.assertTrue(deny(cmd), cmd)

    def test_plain_acts_and_help_still_run(self):
        for cmd in ("of asks answered AS-1", "of asks withdraw AS-2 stale", "python3 bin/of asks answered AS-1",
                    "of asks --all",
                    "of status working --answered AS-1", "of asks answered AS-1 && of status working",
                    # the 21-day replay's false denies: a reason that says `at`, a document an edit writes, notes
                    'of asks withdraw AS-3 "superseded: answered at 12:00 by ssh"',
                    "python3 - <<'EOF'\np='AGENTS.md'; s=open(p).read()\nb='run `of asks answered <id>`; ssh host; "
                    "unset OF_POSITION'\nopen(p,'w').write(s+b)\nEOF",
                    "cat > notes.md <<'EOF'\nenv -u OF_POSITION of asks answered AS-1 passed\nEOF",
                    "printf '%s\\n' 'unset OF_POSITION; of asks answered AS-1' > note.txt",
                    'export SSH_AUTH_SOCK= ; ID=$(echo AS-1); of asks withdraw "${ID}" done'):
            self.assertIsNone(deny(cmd), cmd)
        self.assertTrue(deny("echo 'of asks answered AS-1' | at now"))   # text a runner reads still counts


class SedProgramIsText(unittest.TestCase):
    """of_review's probe commit (of_architect msg 01a1f620-bc82): a sed program naming the path act, `python3 -P` and
    `at` as prose was refused, with nothing on the line clearing a variable."""

    def test_a_sed_program_is_prose_unless_it_runs(self):
        import of_guard
        act = "`" + "of org set <org> path`"
        line = ("cp a.py probes/x.py && sed -i '' '1s|.*|\"\"\"G31 forms for " + act + " at PR 427: D = denied."
                "\\n\\nRun: cd <tree> \\&\\& python3 -P <this file> <tree>\\n\"\"\"|' probes/x.py && "
                "release/commit.sh -F m.txt -- probes/x.py")
        self.assertIsNone(of_guard._actor_line_deny(line))
        for runs in ("sed -n 's/x/" + "of org set o path r/e' f; at now",
                     # e after any address, and a program from a file (of_server on 9b5de527)
                     "sed '$e " + "of org set o path r' f; at now",
                     "sed '1,$e " + "of org set o path r' f; at now",
                     "sed '0~2e " + "of org set o path r' f; at now",
                     "sed '/x/e " + "of org set o path r' f; at now",
                     "sed -f prog.sed 'x " + "of org set o path r' f; at now",
                     "sed -nf prog.sed 'x " + "of org set o path r' f; at now",
                     "sed --file=prog.sed 'x " + "of org set o path r' f; at now",
                     "sed 'sxaxbxe' 'x " + "of org set o path r' f; at now",
                     "sed -e 'sxaxbxe' 'x " + "of org set o path r' f; at now",
                     # an e touching a letter (of_review on PR 427 at b0c116f)
                     "sed -n 's/x/" + "of org set o path r/ge' f; at now",
                     "sed -n 's/x/" + "of org set o path r/Ie' f; at now",
                     "sed -n 's/x/" + "of org set o path r/pe' f; at now",
                     "sed -n '$ex " + "of org set o path r' f; at now",   # the e command's own word glued to it
                     "sed '1e " + "of org set o path r' f; at now",
                     "sed 's/x/" + "of org set o path r/' f | sh; at now"):
            self.assertIsNotNone(of_guard._actor_line_deny("export -n OF_POSITION; " + runs), runs)

class SedRunsItsText(unittest.TestCase):
    """of_server on a9d0635c: the act glued to sed's e command, or an s replacement with the e flag and a letter
    delimiter, is read as a command line of its own."""

    def test_what_a_sed_runs_is_read(self):
        import of_guard
        act = "of asks answ" + "ered AS-1 --option 1"
        for form in (f"env -i sed '$e{act}' f", f"env -i sed '1e{act}' f", f"env -i sed -n 'sxaxecho {act}xe' f",
                     f"env -i sed -n 's/.*/{act}/ge' f"):
            self.assertIsNotNone(of_guard._actor_line_deny(form), form)
        self.assertIsNone(of_guard._actor_line_deny("sed -n '$e" + act + "' f"))   # nothing clears a variable
        self.assertEqual(of_guard._sed_run_texts("1s|a|b|g"), [])
        self.assertEqual(of_guard._sed_run_texts("$eecho hi;p"), ["echo hi"])

class OrgPathActByPosition(unittest.TestCase):
    def test_only_the_path_key_is_the_owners(self):
        import of_guard
        self.assertTrue(of_guard._org_path_act(["org", "set", "ofb", "path", "/r"]))
        self.assertFalse(of_guard._org_path_act(["org", "set", "path", "hold", "on"]))   # an organization named path
        self.assertFalse(of_guard._org_path_act(["org", "show", "path"]))
        for words in (["org", "--", "set", "ofb", "path", "/r"], ["org", "set", "--", "ofb", "path", "/r"],
                      ["org", "set", "ofb", "--", "path", "/r"], ["org", "set", "ofb", "path", "--", "/r"]):
            self.assertTrue(of_guard._org_path_act(words), words)     # a double dash is no positional (of_review)
        self.assertFalse(of_guard._org_path_act(["org", "set", "--", "path", "hold", "on"]))


class ServerRouteAct(unittest.TestCase):
    def test_of_server_route_keeps_its_caller(self):
        """of_review on organic-framework PR 521: the route decides where the machine's OF traffic goes."""
        import of_guard
        self.assertTrue(of_guard._server_route_act(["server", "route", "https://h:1", "--via", "direct"]))
        self.assertTrue(of_guard._server_route_act(["server", "--", "route", "https://h:1"]))
        self.assertFalse(of_guard._server_route_act(["server", "status"]))
        self.assertTrue(of_guard._server_route_act(["server", "--via", "direct", "route", "https://h:1"]))
        self.assertTrue(deny("env -u OF_POSITION of server --via direct route https://h:1"))       # option first
        self.assertTrue(deny("unset OF_POSITION; bash -c 'of server --via direct route https://h:1'"))  # a line act
        for cmd in ("env -u CLAUDE_CODE_SESSION_ID -u OF_POSITION of server route https://h:1 --via direct",
                    "OF_POSITION= CLAUDE_CODE_SESSION_ID= python3 bin/of server route https://h:1 --via tcp:192.0.2.1:9"):
            self.assertTrue(deny(cmd), cmd)
        self.assertIsNone(deny("of server route https://h:1 --via direct"))   # the session as it is: of refuses it

if __name__ == "__main__":
    unittest.main()
