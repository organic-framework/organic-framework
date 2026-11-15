"""G26 and G27 (docs/guard.md): a git repository at the home folder, and a variable that may be unset or empty deciding
what `cd`, `rm -r` or git acts on (of_conductor, message 01a1de70-16f5; of_platform, message 01a1de71-d15e: on
2026-11-15 a test command ran `cd $T && git init` with T unset, and git init made a repository at the home folder)."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_events  # noqa: E402
import of_guard  # noqa: E402
import of_hook  # noqa: E402

INCIDENT = ("sed -n 1p notes.txt && grep -q x notes.txt && T=/tmp/of-probe && rm -rf $T && mkdir -p $T/.git/hooks "
            "2>/dev/null; cd $T && git init -q && git commit --allow-empty -m init")


class SplitOpsTest(unittest.TestCase):
    def test_the_separator_before_each_pipeline(self):
        ops = []
        of_guard._split("a && b; c || d & e\nf", ops)
        self.assertEqual(ops, [None, "&&", ";", "||", "&", "\n"])
        ops = []
        of_guard._split("a &&\n  b ||\n c", ops)   # a newline after && or || continues that list
        self.assertEqual(ops, [None, "&&", "||"])
        self.assertEqual(of_guard._split("a | b && c"), [[("cmd", "a"), ("cmd", "b")], [("cmd", "c")]])


class UnsetTargetTest(unittest.TestCase):
    """G27: what may be unset or empty never decides a cd, rm -r or git target."""

    def setUp(self):
        self.home = os.path.expanduser("~")
        self.env = {"HOME": self.home, "PATH": "/usr/bin", "TMPDIR": "/tmp"}

    def deny(self, cmd, cwd="/tmp"):
        of_guard._WARNINGS.clear()
        return of_guard.targets_deny(cmd, cwd, self.env)

    def verdict(self, cmd, cwd="/tmp"):
        hit = self.deny(cmd, cwd)
        if hit:
            return "deny", hit[0]
        return ("warn", of_guard._WARNINGS[0][0]) if of_guard._WARNINGS else None

    def test_the_incident_and_its_cousins(self):
        """rm -r denies at once; cd and git warn first (live flows in the replay); the incident is G26's deny."""
        got = self.verdict(INCIDENT)
        self.assertEqual(got[0], "deny")
        self.assertTrue(got[1].startswith(of_guard.HOME_REPO_DENY), got)
        self.assertIn("when T is unset", got[1])
        for cmd, mode, what in (("mkdir -p d && T=d; cd $T", "warn", "cd"),       # set on a && chain that may break
                                ("X || T=/a && cd $T", "warn", "cd"),             # X succeeds: T=/a is skipped
                                ("T=; cd $T", "warn", "cd"),                      # set empty
                                ("T=$U; cd $T", "warn", "cd"),
                                ("git -C \"$D\" status", "warn", "git"),          # -C "" is the current folder
                                ("git init $D", "warn", "git"),
                                ("git clone https://example.org/r.git $D", "warn", "git"),
                                # rm -r denies when the path, the name empty, is the root, the home folder or above
                                # it, every file in one of them, or everything in the working folder
                                ("rm -rf \"$T\"/*", "deny", "rm -r"),             # quoting does not help
                                ("rm -fr $OUT/", "deny", "rm -r"),
                                ("rm -rf $HOME/$SUB", "deny", "rm -r"),
                                ("rm -rf ~/$SUB/", "deny", "rm -r"),
                                ("rm -rf \"$T\"*", "deny", "rm -r"),
                                ("T=x rm -rf $T/", "deny", "rm -r"),              # the shell expands $T before T=x
                                ("mkdir -p d && T=d; rm -rf $T/*", "deny", "rm -r"),
                                ("mkdir -p d && T=d; rm -rf $T", "warn", "rm -r"),  # no path at all: rm does nothing
                                ("rm -rf $S/build", "warn", "rm -r")):
            got = self.verdict(cmd)
            self.assertEqual(got and got[0], mode, cmd)
            self.assertTrue(got[1].startswith(f"{what} whose target"), (cmd, got))
            self.assertIn("${NAME:?}", got[1])
            self.assertTrue(of_guard.owns_advice(got[1]))
        self.assertEqual(self.verdict("cd $W && git init")[0], "deny")    # a cd that may reach home, then init
        self.assertEqual(self.verdict("cd $W && git log -1")[0], "warn")

    def test_what_is_set_on_every_path_is_allowed(self):
        for cmd in ("T=$(mktemp -d) && cd $T && git init -q",
                    "T=/tmp/x; cd $T && rm -rf $T",
                    "cd \"${T:?}\" && rm -rf \"$T/\"",
                    ": \"${T:?}\"; rm -rf \"$T/build\"",
                    "for d in a b; do rm -rf $d; done",
                    "if [ -n \"$T\" ]; then rm -rf \"$T\"; fi",
                    "[ -d \"$T\" ] && cd \"$T\"",
                    "cat list | while read -r f; do rm -rf \"$f\"; done",
                    "T=x bash -c 'rm -rf $T'",
                    "export T=/tmp/x; bash -c 'cd $T'",
                    "set -u; cd $T",
                    "cd $HOME/src && rm -rf $TMPDIR/y",
                    "rm -rf ${T:-/tmp/zz}",
                    "rm -f $F",                                          # not recursive
                    "( T=/a; cd $T )",
                    "T=/a && ( cd $T && rm -rf $T )",
                    "cd \"$(git rev-parse --show-toplevel)\"",
                    "cd", "cd -", "rm -rf build dist",
                    # found by the replay: where a $( ) stands, a sourced file, a "…" body this shell expands, a
                    # loop after &&, a test that holds through its then block
                    "for d in ~/a ~/b; do echo \"$d $(git -C \"$d\" log -1)\"; done",
                    ". ./env.sh; cd \"$D\"",
                    "L=/tmp/x; sh -c \"cd $L\"",
                    "cd /tmp && for n in a b; do K=$n; ( cd \"$n\" && ls ); done",
                    "if [ -n \"$T\" ]; then echo go; rm -rf \"$T\"; fi"):
            self.assertIsNone(self.deny(cmd), cmd)

    def test_what_holds_ends_where_its_block_or_its_shell_ends(self):
        for cmd in ("if [ -n \"$T\" ]; then rm -rf \"$T\"; fi; rm -rf $T/",   # after fi the test no longer holds
                    "echo \"$(rm -rf $Q/ && pwd)\"",                          # a $( ) is checked too
                    "T=/tmp/x; bash -c 'rm -rf $T/'"):                         # a new shell: only what is exported
            self.assertEqual((self.verdict(cmd) or ("",))[0], "deny", cmd)

    def test_a_new_shell_sees_only_what_is_exported(self):
        self.assertIsNone(self.verdict("T=/tmp/x; T=$T bash -c 'rm -rf $T'"))
        self.assertIsNone(self.verdict("export T=/tmp/x; bash -c 'rm -rf $T'"))

    def test_a_mode_switch_turns_a_warning_into_a_deny(self):
        saved = of_guard.UNSET_TARGET_MODE
        of_guard.UNSET_TARGET_MODE = dict(saved, cd="deny")
        try:
            self.assertEqual(self.verdict("mkdir -p d && T=d; cd $T")[0], "deny")
        finally:
            of_guard.UNSET_TARGET_MODE = saved

    def test_critical_deny_applies_it(self):
        hit = of_guard.critical_deny("Bash", {"command": "rm -rf \"$OF_TEST_NEVER_SET\"/*"}, "/tmp")
        self.assertTrue(hit and hit[0].startswith("rm -r whose target"), hit)


class HomeRepoTest(unittest.TestCase):
    """G26: no git init, clone or commit makes or uses a repository at the home folder."""

    def setUp(self):
        self.home = os.path.realpath(os.path.expanduser("~"))
        real = os.environ.get("OF_TEST_REAL_HOME")
        if not real or os.path.realpath(real) == self.home:   # it makes a .git in the home folder: tests/isolate's only
            self.skipTest("needs the temporary home folder of tests/isolate.py")
        self.env = {"HOME": self.home, "PATH": "/usr/bin"}
        self.tmp = tempfile.TemporaryDirectory(dir=self.home)   # a folder under the (test's own) home folder

    def tearDown(self):
        self.tmp.cleanup()

    def deny(self, cmd, cwd=None):
        return of_guard.targets_deny(cmd, cwd or self.tmp.name, self.env)

    def test_a_repository_at_the_home_folder_is_denied(self):
        for cmd, cwd in (("cd ~ && git init", None), ("cd && git commit -m x", None), ("git -C ~ init", None),
                         ("git init ~", None), ("git init \"$HOME\"", None), ("git init -q", self.home),
                         ("cd ~ && git clone https://example.org/r.git .", None),
                         ("git clone https://example.org/r.git ~", None), ("git commit -m x", self.home)):
            hit = self.deny(cmd, cwd)
            self.assertIsNotNone(hit, (cmd, cwd))
            self.assertEqual(hit[0], of_guard.HOME_REPO_DENY)
            self.assertTrue(of_guard.owns_advice(hit[0]))

    def test_a_repository_under_the_home_folder_is_allowed(self):
        for cmd in ("git init", "git init -q proj", "cd ~/src && git init", "git clone https://example.org/r.git",
                    "git -C ~/src commit -m x"):
            self.assertIsNone(self.deny(cmd), cmd)
        self.assertIsNone(self.deny("git clone https://example.org/r.git", self.home))   # makes ~/r, not ~/.git

    def test_a_commit_that_would_reach_a_home_repository_is_denied(self):
        stray = os.path.join(self.home, ".git")
        self.assertFalse(os.path.exists(stray))
        os.mkdir(stray)
        try:
            self.assertEqual(self.deny("git commit -m x")[0], of_guard.HOME_REPO_DENY)   # walks up to ~/.git
            own = os.path.join(self.tmp.name, "proj", ".git")
            os.makedirs(own)
            self.assertIsNone(self.deny("cd proj && git commit -m x"))                   # its own repository
        finally:
            os.rmdir(stray)


class HookOutputTest(unittest.TestCase):
    """The hook's deny carries the rule's own advice, not the secret rules' "secrets stay in their files"."""

    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        state = os.path.join(self.root.name, "state")
        os.makedirs(state)
        with open(os.path.join(self.root.name, "orgs.yaml"), "w") as fh:
            fh.write("orgs: []\n")
        self.saved = (of_events.STATE_DIR, of_hook.STATE, os.environ.get("OF_ORGS"))
        of_events.STATE_DIR = of_hook.STATE = state
        os.environ["OF_ORGS"] = os.path.join(self.root.name, "orgs.yaml")
        of_hook.OUTPUT = None

    def tearDown(self):
        of_events.STATE_DIR, of_hook.STATE = self.saved[0], self.saved[1]
        if self.saved[2] is None:
            os.environ.pop("OF_ORGS", None)
        else:
            os.environ["OF_ORGS"] = self.saved[2]
        of_hook.OUTPUT = None
        self.root.cleanup()

    def test_the_deny_says_what_to_write(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            of_hook.handle("PreToolUse", {"hook_event_name": "PreToolUse", "session_id": "S1", "cwd": self.root.name,
                                          "tool_name": "Bash", "tool_input": {"command": INCIDENT}})
        printed = out.getvalue()
        got = (json.loads(printed) if printed.strip() else (of_hook.OUTPUT or {})).get("hookSpecificOutput") or {}
        self.assertEqual(got.get("permissionDecision"), "deny")
        self.assertIn("${NAME:?}", got["permissionDecisionReason"])
        self.assertIn("G26", got["permissionDecisionReason"])
        self.assertNotIn("Secrets stay", got["permissionDecisionReason"])


if __name__ == "__main__":
    unittest.main()
