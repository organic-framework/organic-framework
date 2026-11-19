"""G0.11: a command nested deeper than the guard reads is refused, never allowed unread (docs/guard.md G0.11).

of_review, msg 01a1f412-7113, 2026-11-19 (named as a class on organic-framework PR 332, comment 7011034536): every
reader stopped at its depth cap and answered "nothing found", so any command the guard denies passed when nested five
levels deep, for example five subshells or five command substitutions around a bare `op`. The readers now read
twelve levels and at most READ_BUDGET nested bodies per call; past either, the command is refused."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_guard  # noqa: E402


def subshells(cmd, n):
    for _ in range(n):
        cmd = f"( {cmd} )"
    return cmd


def substitutions(cmd, n):
    for _ in range(n):
        cmd = f"echo $( {cmd} )"
    return cmd


def groups(cmd, n):
    for _ in range(n):
        cmd = "{ " + cmd + "; }"
    return cmd


def deny(cmd):
    hit = of_guard.critical_deny("Bash", {"command": cmd})
    return hit[0] if hit else None


class DepthCapTest(unittest.TestCase):

    def test_a_denied_command_five_levels_down_is_refused(self):
        self.assertIn("bare op", deny(subshells("op item --help", 1)))
        self.assertIn("bare op", deny(groups("op item --help", 4)))
        for wrap in (subshells, substitutions, groups):   # groups: of_secrets-review, msg 01a1f440-6796
            with self.subTest(wrap=wrap.__name__):
                self.assertIn("bare op", deny(wrap("op item --help", 5)))   # read now: twelve levels
                self.assertIn("bare op", deny(wrap("op item --help", of_guard.DEPTH_CAP - 1)))
                self.assertEqual(deny(wrap("op item --help", of_guard.DEPTH_CAP + 2)), of_guard.DEPTH_CAP_DENY)

    def test_the_cap_is_read_and_past_it_is_refused(self):
        for wrap in (subshells, substitutions, groups):
            with self.subTest(wrap=wrap.__name__):
                self.assertIsNone(deny(wrap("echo hi", of_guard.DEPTH_CAP - 1)))
                self.assertIsNone(of_guard._CAPPED[0])
                self.assertEqual(deny(wrap("echo hi", of_guard.DEPTH_CAP + 2)), of_guard.DEPTH_CAP_DENY)
                self.assertEqual(of_guard._CAPPED[0], "_bash_deny")

    def test_more_bodies_than_one_call_reads_is_refused(self):
        """A command built to outlast the hook's timeout, whose kill would lose the deny, is refused instead."""
        wide = "echo " + " ".join("$(echo a)" for _ in range(of_guard.READ_BUDGET + 5))
        self.assertEqual(deny(wide), of_guard.DEPTH_CAP_DENY)
        self.assertEqual(of_guard._CAPPED[0], "_bash_deny (read budget)")
        self.assertIsNone(deny("echo " + " ".join("$(echo a)" for _ in range(50))))

    def test_the_code_reader_marks_its_limit_too(self):
        """of_review on organic-framework PR 389: `_code_runs_protected` stopped at depth 4 without the mark."""
        of_guard._CAPPED[0] = None
        self.assertIsNone(of_guard._code_runs_protected("echo $(true)", None, None, of_guard.DEPTH_CAP))
        self.assertTrue(of_guard._CAPPED[0])        # the first reader to stop keeps the mark
        import inspect
        self.assertIn('_capped(\n                    "_code_runs_protected")', inspect.getsource(of_guard._code_runs_protected))
        of_guard._CAPPED[0] = None
        of_guard._code_runs_protected("echo $(true)", None, None, 0)
        self.assertIsNone(of_guard._CAPPED[0])

    def test_the_mark_is_per_call(self):
        deny(subshells("echo hi", of_guard.DEPTH_CAP + 2))
        self.assertIsNone(deny("echo hi"))
        self.assertIsNone(of_guard._CAPPED[0])

    def test_warn_mode_allows_with_a_warning(self):
        saved = of_guard.DEPTH_CAP_MODE
        of_guard.DEPTH_CAP_MODE = "warn"
        try:
            self.assertIsNone(deny(subshells("echo hi", of_guard.DEPTH_CAP + 2)))
            self.assertIn(of_guard.DEPTH_CAP_DENY, [w[0] for w in of_guard._WARNINGS])
        finally:
            of_guard.DEPTH_CAP_MODE = saved

    def test_every_reader_with_a_depth_cap_marks_it(self):
        """A reader that stops at its cap marks the call, so a later check can refuse it (11 readers at this head)."""
        import inspect
        import re
        src = inspect.getsource(of_guard)
        bodies = re.split(r"\n(?=def )", src)
        capped = [b.split("(", 1)[0][4:] for b in bodies if re.search(r"\n    if [^\n]*\bdepth > DEPTH_CAP\b", b)]
        unmarked = [name for name, b in zip(capped, [b for b in bodies if re.search(r"\n    if [^\n]*\bdepth > DEPTH_CAP\b", b)])
                    if "_CAPPED[0] = _CAPPED[0] or" not in b]
        self.assertEqual(len(capped), 11, capped)
        self.assertEqual(unmarked, [], "a reader whose depth cap answers without marking the call")


if __name__ == "__main__":
    unittest.main()
