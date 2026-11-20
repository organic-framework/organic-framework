"""A Codex session's PreToolUse payload, as of_hook judges it (W3 point 1 of organic-framework-build's
reports/codex-guard-w2-w3-2026-11-20.md, of_server's decision): Bash as it is, apply_patch as one Write or Edit per
file it touches, view_image as a Read, and one answer at most."""

import contextlib
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import of_hook  # noqa: E402

ENV = "." + "env"
KEY = "." + "ssh/id_" + "ed25519"


def fire(tool, tool_input, cwd="/w"):
    of_hook.OUTPUT = None
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        of_hook.handle("PreToolUse", {"hook_event_name": "PreToolUse", "session_id": "codex-thread-1", "cwd": cwd,
                                      "turn_id": "t1", "model": "gpt", "permission_mode": "bypassPermissions",
                                      "tool_name": tool, "tool_use_id": "u1", "tool_input": tool_input})
    return out.getvalue()


def patch(*entries):
    return "\n".join(["*** Begin Patch", *entries, "*** End Patch"])


class PatchCalls(unittest.TestCase):
    def test_each_kind_of_entry_is_a_write_or_an_edit(self):
        calls = of_hook.patch_calls(patch("*** Add File: a/new.txt", "+hello", "*** Update File: b.py", "@@", "-x", "+y",
                                          "*** Move to: c.py", "*** Delete File: d.txt"), "/w")
        self.assertEqual([(c["tool_name"], c["tool_input"]["file_path"]) for c in calls],
                         [("Write", "/w/a/new.txt"), ("Edit", "/w/b.py"), ("Write", "/w/c.py"), ("Edit", "/w/d.txt")])
        self.assertEqual(calls[0]["tool_input"]["content"], "hello\n")
        self.assertEqual(calls[1]["tool_input"]["new_string"], "y\n")


class CodexPreToolUse(unittest.TestCase):
    def answers(self, printed):
        """Every JSON object on stdout: Codex reads one."""
        dec = json.JSONDecoder()
        out, i, s = [], 0, printed.strip()
        while i < len(s):
            obj, j = dec.raw_decode(s, i)
            out.append(obj)
            i = j
            while i < len(s) and s[i].isspace():
                i += 1
        return out

    def test_a_patch_writing_a_secret_file_is_denied_once(self):
        printed = fire("apply_patch", {"command": patch("*** Add File: notes.md", "+ok", f"*** Add File: {ENV}", "+A=1",
                                                         f"*** Update File: {os.path.expanduser('~')}/{KEY}", "+x")})
        got = self.answers(printed)
        self.assertEqual(len(got), 1, printed)
        self.assertEqual(got[0]["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_a_plain_patch_and_a_plain_command_pass(self):
        self.assertEqual(self.answers(fire("apply_patch", {"command": patch("*** Add File: notes.md", "+ok")})), [])
        self.assertEqual(self.answers(fire("Bash", {"command": "ls -la"})), [])

    def test_a_shell_command_is_judged_as_claude_codes(self):
        got = self.answers(fire("Bash", {"command": f"cat {ENV}"}))
        self.assertEqual(got[0]["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_view_image_of_a_secret_path_is_a_read(self):
        got = self.answers(fire("view_image", {"path": f"{os.path.expanduser('~')}/{KEY}"}))
        self.assertEqual(got[0]["hookSpecificOutput"]["permissionDecision"], "deny")


if __name__ == "__main__":
    unittest.main()
