"""G25 (docs/guard.md): an agent's command that types into a pane through herdr's CLI is declared, so the receiving
session records the text as injected, never as its person's words (of_server's trust review of organic-framework
PR 168; of_architect's conditions, msg 01a1d9ab-6b08)."""
import json
import os
import tempfile
import textwrap
import time
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import of_events  # noqa: E402
import of_guard  # noqa: E402
import of_hook  # noqa: E402

PANES = {("pane", "w1:p1"): ("w1:p1", "S2"), ("agent", "of_b"): ("w1:p1", "S2"), ("pane", "w1:p5"): ("w1:p5", None)}


class ParseTest(unittest.TestCase):
    def decl(self, cmd):
        return [(d["target"], d["kind"], bool(d["sha256"])) for d in of_guard.typing_declarations(cmd)]

    def test_forms(self):
        self.assertEqual(self.decl("herdr pane send-text w1:p1 'yes, p50 is the target'"),
                         [(("pane", "w1:p1"), "pane send-text", True)])
        self.assertEqual(self.decl("herdr pane send-text w1:p1 yes && herdr pane send-keys w1:p1 Enter"),
                         [(("pane", "w1:p1"), "pane send-text", True), (("pane", "w1:p1"), "pane send-keys", False)])
        self.assertEqual(self.decl("herdr pane run w1:p1 'of status'"), [(("pane", "w1:p1"), "pane run", True)])
        self.assertEqual(self.decl('herdr agent prompt of_b "approve"'), [(("agent", "of_b"), "agent prompt", True)])
        self.assertEqual(self.decl("herdr agent send-keys of_b 1"), [(("agent", "of_b"), "agent send-keys", False)])
        self.assertEqual(self.decl('herdr pane send-text "$HERDR_PANE_ID" yes'), [(("own", None), "pane send-text", True)])
        self.assertEqual(self.decl("herdr --session main pane send-text w1:p1 \"$(cat note.txt)\""),
                         [(("pane", "w1:p1"), "pane send-text", False)])   # a substitution: unread, pane-wide
        self.assertEqual(self.decl("herdr pane run $P 'claude --resume x'"), [(("unknown", None), "pane run", True)])
        self.assertEqual(self.decl("timeout 5 herdr pane send-text w1:p1 hi"), [(("pane", "w1:p1"), "pane send-text", True)])

    def test_what_types_nothing(self):
        for cmd in ("herdr --machine lab pane send-text w1:p1 hi",  # another machine: a named limit
                    "echo 'herdr pane send-text w1:p1 x' > notes.md",
                    "git commit -m 'relay: herdr pane send-text into the pane'",
                    "herdr pane read w1:p2 --lines 20", "herdr agent list", "of send of_b 'herdr pane run'"):
            self.assertEqual(self.decl(cmd), [], cmd)

    def test_digest_is_normalized_on_both_sides(self):
        """of_architect's condition 2: the trailing Enter or newline, CRLF and bracketed-paste markers."""
        d = of_guard.typed_digest("yes")
        for t in ("yes\n", "yes\r\n", "yes\r", "\x1b[200~yes\x1b[201~", "  yes  \n"):
            self.assertEqual(of_guard.typed_digest(t), d, repr(t))
        self.assertNotEqual(of_guard.typed_digest("yes no"), d)


class DeclareTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        os.makedirs(os.path.join(root, "org", "registry"))
        with open(os.path.join(root, "org", "registry", "positions.yaml"), "w") as fh:
            fh.write(textwrap.dedent("""
                organization: test-org
                positions:
                  - {name: of_a, system: of, role: a, holder: {session: S1}}
                  - {name: of_b, system: of, role: conductor, holder: {session: S2}}
                """))
        with open(os.path.join(root, "orgs.yaml"), "w") as fh:
            fh.write("orgs:\n  - {name: t, path: org}\n")
        self.saved = (of_events.STATE_DIR, of_hook.STATE, os.environ.get("OF_ORGS"), os.environ.get("HERDR_ENV"),
                      os.environ.get("HERDR_PANE_ID"), of_hook._herdr_target)
        of_events.STATE_DIR = of_hook.STATE = os.path.join(root, "state")
        os.makedirs(of_hook.STATE)
        os.environ["OF_ORGS"] = os.path.join(root, "orgs.yaml")
        os.environ.pop("HERDR_ENV", None)
        of_hook._herdr_target = lambda kind, ident: PANES.get((kind, ident), (None, None))

    def tearDown(self):
        of_events.STATE_DIR, of_hook.STATE = self.saved[0], self.saved[1]
        for k, v in zip(("OF_ORGS", "HERDR_ENV", "HERDR_PANE_ID"), self.saved[2:5]):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        of_hook._herdr_target = self.saved[5]
        self.tmp.cleanup()

    def type_(self, cmd, sid="S1"):
        of_hook.handle("PreToolUse", {"hook_event_name": "PreToolUse", "session_id": sid, "cwd": "/x",
                                      "tool_name": "Bash", "tool_input": {"command": cmd}})

    def seen(self, prompt, sid="S2", pane="w1:p1"):
        os.environ["HERDR_PANE_ID"] = pane
        return of_hook._injected(sid, prompt)

    def declarations(self):
        with open(os.path.join(of_hook.STATE, "injections.jsonl")) as fh:
            return [json.loads(x) for x in fh]

    def test_send_text_without_enter(self):
        self.type_("herdr pane send-text w1:p1 'yes, p50 is the target'")
        m = self.seen("yes, p50 is the target")
        self.assertEqual((m["match"], m["by"], m["injector"], m["session"]), ("sha256", "guard", "of_a", "S2"))
        self.assertIsNone(self.seen("something the person typed"))      # by its text only
        self.assertIsNone(self.seen("yes, p50 is the target", sid="S9", pane="w9:p9"))  # another pane

    def test_send_text_with_enter(self):
        self.type_("herdr pane send-text w1:p1 'yes' && herdr pane send-keys w1:p1 Enter")
        self.assertEqual(self.seen("yes\n")["match"], "pane")             # the Enter: any prompt in that pane
        self.assertEqual(self.seen("anything else")["match"], "pane")
        self.assertIsNone(self.seen("yes", sid="S9", pane="w9:p9"))

    def test_run_and_agent_prompt(self):
        self.type_("herdr pane run w1:p1 'of status'")
        self.assertEqual(self.seen("of status\r\n")["match"], "sha256")
        self.type_('herdr agent prompt of_b "approve the diff"')
        m = self.seen("\x1b[200~approve the diff\x1b[201~")
        self.assertEqual((m["match"], m["agent"]), ("sha256", "of_b"))

    def test_the_session_matches_after_the_pane_moved(self):
        self.type_("herdr agent prompt of_b go")
        self.assertEqual(self.seen("go", sid="S2", pane="w7:p2")["match"], "sha256")  # its HERDR_PANE_ID is stale

    def test_own_pane(self):
        os.environ["HERDR_PANE_ID"] = "w1:p3"
        self.type_('herdr pane send-text "$HERDR_PANE_ID" yes', sid="S1")
        self.assertEqual(self.seen("yes", sid="S1", pane="w1:p3")["match"], "sha256")

    def test_an_unknown_target(self):
        self.type_("herdr pane send-text $P 'claude --resume x'")
        self.assertEqual(self.seen("claude --resume x", sid="S9", pane="w9:p9")["match"], "sha256")
        self.assertIsNone(self.seen("yes", sid="S9", pane="w9:p9"))

    def test_keys_to_a_variable_only_warn(self):
        """Declared for every session, such typing hid the founder's own prompts in the replay (condition 5)."""
        self.type_("for p in $(herdr pane list | jq -r .id); do herdr pane send-keys $p Enter; done")
        self.assertFalse(os.path.exists(os.path.join(of_hook.STATE, "injections.jsonl")))
        self.assertIsNone(self.seen("yes", sid="S9", pane="w9:p9"))

    def test_the_window(self):
        self.type_("herdr pane send-keys w1:p1 Enter")
        lines = self.declarations()
        for x in lines:
            x["ts"] -= of_hook.TYPING_WINDOW_S + 1
        with open(os.path.join(of_hook.STATE, "injections.jsonl"), "w") as fh:
            fh.write("".join(json.dumps(x) + "\n" for x in lines))
        self.assertIsNone(self.seen("yes"))

    def test_a_tools_old_declaration_still_matches(self):
        import hashlib
        with open(os.path.join(of_hook.STATE, "injections.jsonl"), "a") as fh:
            fh.write(json.dumps({"ts": time.time(), "session": None, "injector": "budget-governor",
                                 "sha256": hashlib.sha256(b"Limit topped up").hexdigest()}) + "\n")
        self.assertEqual(self.seen("Limit topped up", sid="S9", pane="w9:p9")["match"], "sha256")

    def test_the_prompt_records_which_declaration(self):
        """of_architect's condition 4: a consumer can tell why a prompt was downgraded."""
        self.type_("herdr pane send-text w1:p1 yes && herdr pane send-keys w1:p1 Enter")
        os.environ["HERDR_PANE_ID"] = "w1:p1"
        of_hook.handle("UserPromptSubmit", {"hook_event_name": "UserPromptSubmit", "session_id": "S2", "cwd": "/x",
                                            "prompt": "yes"})
        import of_store
        conn = of_store.connect()
        try:
            rec = [json.loads(r[0]) for r in conn.execute("SELECT record FROM event ORDER BY rowid")]
        finally:
            conn.close()
        p = [r for r in rec if r["event.name"] == "of.human_prompt"][-1]["of.payload"]
        self.assertEqual((p["origin"], p["injector"]), ("injected", "of_a"))
        self.assertEqual(p["declaration"], {"match": "pane", "by": "guard", "kind": "pane send-keys"})

    def test_a_denied_call_and_other_tools_declare_nothing(self):
        self.type_("cat ~/.ssh/id_ed25519; herdr pane send-text w1:p1 yes")
        of_hook.handle("PreToolUse", {"hook_event_name": "PreToolUse", "session_id": "S1", "cwd": "/x",
                                      "tool_name": "Read", "tool_input": {"file_path": "/x/herdr"}})
        self.assertFalse(os.path.exists(os.path.join(of_hook.STATE, "injections.jsonl")))


if __name__ == "__main__":
    unittest.main()
