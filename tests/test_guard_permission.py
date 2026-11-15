"""G28 (docs/guard.md): a permission dialog in a session its owner started without prompts is answered by the guard
(of_conductor, message 01a1de9f-17ee: such a dialog held a position for 13 hours and reached the founder as a card)."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_events  # noqa: E402
import of_hook  # noqa: E402

LOOP = ('cd /tmp && for i in 1 2 3; do out=$(of inbox --mine 2>&1); case "$out" in *not_holder*) sleep 5;; '
        '*) echo "$out" | head -40; break;; esac; done')


class PermissionAnswerTest(unittest.TestCase):
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
        self.written, self.states = [], []
        real_write = of_hook.write

        def write(kind, payload, *a, **kw):
            self.written.append((kind, dict(payload)))
            return real_write(kind, payload, *a, **kw)
        self.patches = [mock.patch.object(of_hook, "write", write),
                        mock.patch.object(of_hook, "herdr_state", lambda *a, **kw: self.states.append(a))]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        of_events.STATE_DIR, of_hook.STATE = self.saved[0], self.saved[1]
        if self.saved[2] is None:
            os.environ.pop("OF_ORGS", None)
        else:
            os.environ["OF_ORGS"] = self.saved[2]
        of_hook.OUTPUT = None
        self.root.cleanup()

    def ask(self, cmd, mode, launched=False, tool="Bash", tool_input=None):
        out = io.StringIO()
        with mock.patch.object(of_hook, "_launched_bypass", lambda *a, **kw: launched), \
                contextlib.redirect_stdout(out):
            of_hook.handle("PermissionRequest", {"hook_event_name": "PermissionRequest", "session_id": "S1",
                                                 "cwd": self.root.name, "permission_mode": mode, "tool_name": tool,
                                                 "tool_input": tool_input or {"command": cmd}})
        printed = out.getvalue().strip()
        decision = ((json.loads(printed) if printed else {}).get("hookSpecificOutput") or {}).get("decision")
        recorded = [p for k, p in self.written if k == "permission_request"]
        return decision, recorded[-1] if recorded else None

    def test_a_no_prompt_session_gets_the_guards_answer(self):
        decision, rec = self.ask(LOOP, "bypassPermissions")
        self.assertEqual(decision, {"behavior": "allow"})
        self.assertEqual(rec["answered"]["behavior"], "allow")
        resolved = [p for k, p in self.written if k == "permission_resolved"]
        self.assertEqual([p["outcome"] for p in resolved], ["guard_allowed"])   # the queue never shows it
        self.assertEqual(self.states, [])                    # never shown as blocked: nobody has to answer it
        self.assertFalse(of_hook.load_session("S1").get("blocked"))

    def test_what_the_guard_denies_is_denied_with_its_reason(self):
        decision, rec = self.ask("cat ~/.ssh/id_ed25519", "bypassPermissions")
        self.assertEqual(decision["behavior"], "deny")
        self.assertIn("Organic Framework critical deny", decision["message"])
        self.assertEqual(rec["answered"]["behavior"], "deny")
        self.assertEqual([p["outcome"] for k, p in self.written if k == "permission_resolved"], ["guard_denied"])

    def test_a_session_started_without_prompts_keeps_that_choice_after_its_mode_changed(self):
        decision, rec = self.ask(LOOP, "acceptEdits", launched=True)   # resumed after a move, 2026-11-14
        self.assertEqual(decision, {"behavior": "allow"})

    def test_a_session_started_with_prompts_keeps_its_dialog(self):
        for mode, launched in (("default", False), ("acceptEdits", False), ("plan", False),
                               ("default", True), ("plan", True)):   # a person's later choice beats the launch flag
            self.written.clear()
            self.states.clear()
            decision, rec = self.ask(LOOP, mode, launched=launched)
            self.assertIsNone(decision, (mode, launched))
            self.assertNotIn("answered", rec)
            self.assertTrue(of_hook.load_session("S1").get("blocked"), mode)

    def test_what_guards_the_guard_keeps_its_dialog(self):
        """of_secrets-review on PR 221: only Bash is answered, and never a write to Claude Code's settings, a
        repository's git folder or hooks, or a skipped hook."""
        for tool, ti in (("Edit", {"file_path": "/tmp/p/.claude/settings.json", "old_string": "a", "new_string": "b"}),
                         ("Write", {"file_path": "/tmp/p/.git/hooks/pre-commit", "content": "x"}),
                         ("AskUserQuestion", {"questions": []}), ("WebFetch", {"url": "https://example.org"})):
            decision, rec = self.ask(None, "bypassPermissions", tool=tool, tool_input=ti)
            self.assertIsNone(decision, tool)
        for cmd in ("printf x > .git/hooks/pre-commit", "git commit --no-verify -m x", "cp a ~/.claude/settings.json",
                    "git config core.hooksPath /tmp/h", "sed -i s/a/b/ .claude/settings.local.json"):
            decision, rec = self.ask(cmd, "bypassPermissions")
            self.assertIsNone(decision, cmd)

    def test_the_launch_flags_are_read_up_the_process_tree(self):
        tree = {100: "1 /bin/sh -c of-hook PermissionRequest",
                1: "0 claude --resume abc -n x --settings s.json --dangerously-skip-permissions"}

        def ps(argv, **kw):
            return subprocess.CompletedProcess(argv, 0, stdout=tree.get(int(argv[-1]), ""), stderr="")
        with mock.patch.object(subprocess, "run", ps):
            self.assertFalse(of_hook._launched_bypass(100))       # pid 1 is the end of the walk
            tree[100] = "7 /bin/sh -c of-hook PermissionRequest"
            tree[7] = tree.pop(1)
            self.assertTrue(of_hook._launched_bypass(100))
            tree[7] = "0 claude --resume abc --permission-mode bypassPermissions"
            self.assertTrue(of_hook._launched_bypass(100))
            tree[7] = "0 claude --resume abc --permission-mode acceptEdits"
            self.assertFalse(of_hook._launched_bypass(100))
            tree[7] = "0 /usr/local/bin/node /x/node_modules/@anthropic-ai/claude-code/cli.js"
            self.assertFalse(of_hook._launched_bypass(100))


if __name__ == "__main__":
    unittest.main()
