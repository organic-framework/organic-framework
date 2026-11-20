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

    def test_view_image_of_a_relative_path_is_read_from_cwd(self):
        self.assertEqual(of_hook.codex_calls({"tool_name": "view_image", "cwd": "/w", "turn_id": "t",
                                              "tool_input": {"path": "img/a.png"}})[0]["tool_input"]["file_path"],
                         "/w/img/a.png")

    def test_view_image_of_a_secret_path_is_a_read(self):
        got = self.answers(fire("view_image", {"path": f"{os.path.expanduser('~')}/{KEY}"}))
        self.assertEqual(got[0]["hookSpecificOutput"]["permissionDecision"], "deny")


class PatchFormsTest(unittest.TestCase):
    """of_review and of_secrets-review on PR 469: Codex trims header lines, runs apply_patch through the shell, and an
    unreadable patch is denied, never judged as an Edit of no file."""

    def answers(self, printed):
        return CodexPreToolUse.answers(self, printed)

    def test_headers_as_codex_reads_them(self):
        home = os.path.expanduser("~")
        key = f"{home}/{KEY}"
        owner = f"{home}/.config/of/orgs.yaml"
        for p in (patch(f"   *** Add File: {key}", "+x"), patch("*** Add File: notes.md", "+ok", f"  *** Add File: {key}", "+x"),
                  patch(f"\t*** Delete File: {owner}"), patch(f"***Add File:{key}", "+x"),
                  patch("*** Add Fiel: x", "+x"), patch("*** Add File:", "+x"), "no patch markers at all"):
            got = self.answers(fire("apply_patch", {"command": p}))
            self.assertEqual(len(got), 1, p)
            self.assertEqual(got[0]["hookSpecificOutput"]["permissionDecision"], "deny", p)

    def test_a_patch_through_the_shell_is_judged_by_its_files(self):
        home = os.path.expanduser("~")
        for target in (f"{home}/{KEY}", f"{home}/.config/of/orgs.yaml"):
            cmd = "apply_patch <<'EOF'\n" + patch(f"*** Add File: {target}", "+x") + "\nEOF"
            got = self.answers(fire("Bash", {"command": cmd}))
            self.assertEqual(got[0]["hookSpecificOutput"]["permissionDecision"], "deny", target)
        ok = "apply_patch <<'EOF'\n" + patch("*** Add File: notes.md", "+ok") + "\nEOF"
        self.assertEqual(self.answers(fire("Bash", {"command": ok})), [])

    def test_an_argv_command_is_read_as_its_words(self):
        got = self.answers(fire("apply_patch", {"command": ["apply_patch", patch(f"*** Add File: {ENV}", "+A=1")]}))
        self.assertEqual(got[0]["hookSpecificOutput"]["permissionDecision"], "deny")


class ClaudeCallsQuotingAPatch(unittest.TestCase):
    """of_secrets-review on PR 469 at 6c41f7b: a Claude Code call that quotes the patch markers is no patch."""

    def claude(self, tool, ti):
        of_hook.OUTPUT = None
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            of_hook.handle("PreToolUse", {"hook_event_name": "PreToolUse", "session_id": "claude-1", "cwd": "/w",
                                          "transcript_path": "/home/alex/.claude/projects/-w/claude-1.jsonl",
                                          "tool_name": tool, "tool_input": ti})
        return out.getvalue()

    def test_a_markdown_file_quoting_the_markers_is_written(self):
        M = "*" * 3
        doc = f"How Codex patches look:\n\n{M} Begin Patch\n{M} Add Fiel: x\n{M} End Patch\n"
        self.assertEqual(self.claude("Write", {"file_path": "/w/notes.md", "content": doc}), "")
        self.assertEqual(self.claude("Bash", {"command": f"gh pr comment 1 --body '{M} Begin Patch inline'"}), "")
        self.assertEqual(self.claude("Bash", {"command": f"echo '{M} Begin Patch'"}), "")


class CodexSessionToHerdr(unittest.TestCase):
    """W8 gap 3 (of_herdr's report; of_server's decision eb72245): the managed SessionStart hook reports a Codex pane's
    session to herdr, from herdr's official source, with a growing seq and no resume arguments."""
    ROLLOUT = "/home/alex/.codex/sessions/2026/11/20/rollout-2026-11-20T10-00-00-0190a1b2-c3d4.jsonl"
    ENV = {"HERDR_ENV": "1", "HERDR_PANE_ID": "w1:p2", "HERDR_SOCKET_PATH": "/run/herdr.sock", "PATH": "/nonexistent"}

    def report(self, env, **hook):
        ran = []
        got = of_hook.report_codex_session(dict({"session_id": "T1", "transcript_path": self.ROLLOUT,
                                                 "source": "startup"}, **hook), env=env,
                                           run=lambda argv, **k: ran.append(argv))
        return got, ran

    def test_the_report_and_when_it_is_skipped(self):
        got, ran = self.report(self.ENV)
        self.assertEqual(ran, [got])
        self.assertEqual(got[1:8], ["pane", "report-agent-session", "w1:p2", "--source", "herdr:codex", "--agent", "codex"])
        self.assertEqual(got[got.index("--agent-session-id") + 1], "T1")
        self.assertEqual(got[got.index("--session-start-source") + 1], "startup")
        self.assertTrue(int(got[got.index("--seq") + 1]) > 0)
        self.assertNotIn("--", got)                                            # no resume arguments
        for env, hook in ((dict(self.ENV, HERDR_ENV="0"), {}), ({k: v for k, v in self.ENV.items() if k != "HERDR_PANE_ID"}, {}),
                          (dict(self.ENV, CODEX_THREAD_ID="OTHER"), {}), (self.ENV, {"transcript_path": None})):
            self.assertEqual(self.report(env, **hook), (None, []), (env, hook))
        self.assertEqual(self.report(dict(self.ENV, CODEX_THREAD_ID="T1"))[1][0][4], "--source")

    def test_a_codex_payload_is_told_from_a_claude_one(self):
        self.assertTrue(of_hook.is_codex({"transcript_path": self.ROLLOUT}))
        self.assertTrue(of_hook.is_codex({"turn_id": "t1"}))
        self.assertFalse(of_hook.is_codex({"transcript_path": "/home/alex/.claude/projects/-w/abc.jsonl"}))


if __name__ == "__main__":
    unittest.main()
