"""What starts each turn (bin/of_turns.py): the labels from OF's frames and Claude Code's origin fields, the records
that open no turn, and each response counted once, to the turn open when it came. Pure logic: no store."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import of_turns  # noqa: E402


def user(text, **kw):
    return dict({"type": "user", "message": {"role": "user", "content": text}}, **kw)


def resp(mid, cw=0, cr=0, out=10):
    return (mid, "claude-opus-5-5", 1, out, cr, cw, 0, cw)


class StarterTest(unittest.TestCase):
    def test_labels(self):
        cases = [
            ("relay", user("Message from organic-framework-build:of_review (via relay, authority: none: no grant; ...)")),
            ("relay", user('<pasted_content id="0f38">\nMessage from acme:acme_ops (via relay, authority: none)\nhi')),
            ("pulse", user("Message from of_lab (via of pulse, authority: none)\nnudge")),
            ("asks", user("Message from of (via of asks, authority: none)\nanswer")),
            ("handover", user("Message from of_architect (via of handover, authority: none)\nHand-over 1234abcd")),
            ("spawn", user("Message from of_spawn (via of spawn, authority: none)\nYou hold the position")),
            ("of_tool", user("Message from of_capacity (via of, authority: none)\nthe weekly limit")),
            ("capacity_move", user("of_capacity moved this session to another account. Read the note")),
            ("capacity_move", user("The Mac restarted, and of_capacity resumed this session")),
            ("founder_answer", user("Answer from the founder to your question 1a2b3c4d: yes")),
            ("unframed_message", user("of_architect → the whole team. Founder decided")),
            ("unframed_message", user("Hand-over 952e35bc from of_capacity: Lab scenario")),
            ("peer", user("Another Claude session sent a message: <cross-session-message from=\"uds:x\">")),
            ("cron", user("check the deploy", turnOrigin="scheduled")),
            ("background_done", user("<task-notification> <task-id>bm5d7mpqi</task-id> <status>completed</status>",
                                     origin={"kind": "task-notification"})),
            ("subagent_done", user("<task-notification> <task-id>a9638d293a13dc630</task-id>",
                                   turnOrigin="task_notification")),
            ("monitor", user("<monitor-event>ERROR in deploy.log</monitor-event>")),
            ("subagent", user("Read-only code review of ...", isSidechain=True)),
            ("compaction", user("This session is being continued from a previous conversation that ran out")),
            ("limit_reset", user("Your claude.ai usage limit has reset. Continue the task",
                                 origin={"kind": "auto-continuation"}, isMeta=True)),
            ("sdk", user("Run exactly this command", turnOrigin="sdk")),
            ("typed", user("where did all the agents go?", origin={"kind": "human"}, turnOrigin="human")),
            ("monitor", user("<task-notification> <event>ERROR in deploy.log</event>", origin={"kind": "task-notification"})),
            ("capacity_move", user("of_capacity resumed this session after a move that did not finish. Read the note")),
            ("asks", user("Question from acme_ops (unit trading, delegated to you; ask 1a2b3c4d): merge it?")),
            ("founder_answer", user("Side question from the founder: why is it slow?")),
            ("typed", user("next_step: run the suite again")),          # a word with an underscore is no position
        ]
        for want, rec in cases:
            self.assertEqual(of_turns.turn_starter(rec), want, rec["message"]["content"][:50])
        self.assertTrue(set(w for w, _ in cases) <= set(of_turns.STARTERS))

    def test_records_that_open_no_turn(self):
        tool = {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}
        # a tool result with a text block beside it, such as a system reminder, continues the turn (of_review, PR 647)
        reminder = {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"},
                                                            {"type": "text", "text": "<system-reminder>x</system-reminder>"}]}}
        for rec in (tool, reminder, user("[Request interrupted by user]"), user("<bash-input>ls</bash-input>"),
                    user("shown only", isVisibleInTranscriptOnly=True),
                    user("[Image: original 2068x1842]", isMeta=True), {"type": "assistant", "message": {}}):
            self.assertIsNone(of_turns.turn_starter(rec))

    def test_frame(self):
        self.assertEqual(of_turns.frame(user("Message from acme:acme_org (via relay, authority: none)")),
                         {"org": "acme", "position": "acme_org", "via": "relay"})
        self.assertIsNone(of_turns.frame(user("reply ok")))
        self.assertEqual(of_turns.frame(user("Message from my_org:my_pos (via relay, authority: none)"))["org"], "my_org")


class TurnsTest(unittest.TestCase):
    def test_each_response_once_to_the_turn_open_then(self):
        recs = [("t0", "s", None, resp("m0")),                                     # before any turn
                ("t1", "s", user("Message from x:of_a (via relay, authority: none)"), None),
                ("t2", "s", None, resp("m1", cw=100)), ("t2", "s", None, resp("m1", cw=100)),   # one response, 2 blocks
                ("t3", "s", {"type": "user", "message": {"content": [{"type": "tool_result"}]}}, None),
                ("t4", "s", None, resp("m2", cr=50)),
                ("t5", "s", user("of_capacity moved this session to another account."), None),
                ("t6", "s", None, resp("m3", cw=900))]
        got = list(of_turns.turns(recs))
        self.assertEqual([(t["starter"], t["responses"], t["cache_write"], t["cache_read"]) for t in got],
                         [("before_first_turn", 1, 0, 0), ("relay", 2, 100, 50), ("capacity_move", 1, 900, 0)])
        self.assertEqual(got[1]["sender"], "x:of_a")   # with its organization: two organizations' senders stay apart
        seen = set()   # a carried copy of the same session repeats its responses: counted once over both
        self.assertEqual(sum(t["responses"] for t in of_turns.turns(recs, seen=seen)), 4)
        self.assertEqual(sum(t["responses"] for t in of_turns.turns(recs, seen=seen)), 0)

    def test_a_subagents_turn_counts_to_what_started_its_parent(self):
        main = [("t1", "s", user("Message from x:of_a (via of handover, authority: none)", promptId="p1"), None),
                ("t2", "s", None, resp("m1"))]
        sub = [("t3", "s", user("Review the diff", isSidechain=True, promptId="p1"), None), ("t4", "s", None, resp("m2")),
               ("t5", "s", user("Read the docs", isSidechain=True, promptId="p9"), None), ("t6", "s", None, resp("m3"))]
        got = of_turns.with_causes(list(of_turns.turns(main)) + list(of_turns.turns(sub)))
        self.assertEqual([(t["starter"], t["cause"]) for t in got],
                         [("handover", "handover"), ("subagent", "handover"), ("subagent", "subagent_unlinked")])
        out = of_turns.count(got, ("cause",), {})
        self.assertEqual((out[("handover",)]["turns"], out[("handover",)]["responses"]), (2, 2))

    def test_a_window_cuts_responses_by_time_and_keeps_a_turns_starter(self):
        recs = [("2026-11-19T23:50:00Z", "s", user("Message from x:of_a (via relay, authority: none)"), None),
                ("2026-11-19T23:55:00Z", "s", None, resp("m1")),            # before the window: not counted
                ("2026-11-20T00:00:00.500Z", "s", None, resp("m2")),        # after it, with milliseconds: counted
                ("2026-11-20T01:00:00Z", "s", user("hello"), None), ("2026-11-20T01:01:00Z", "s", None, resp("m3"))]
        got = list(of_turns.turns(recs, since=of_turns._t("2026-11-20T00:00:00Z")))
        self.assertEqual([(t["starter"], t["counted"], t["responses"]) for t in got], [("relay", False, 1), ("typed", True, 1)])
        out = of_turns.count(got, ("starter",), {})
        self.assertEqual((out[("relay",)]["turns"], out[("relay",)]["responses"]), (0, 1))
        self.assertIn("0 turns · 0 responses · 0 at list prices", of_turns.table({}, ("starter",)))

    def test_a_subagents_file_is_its_parents_session_and_only_regular_files_are_read(self):
        self.assertEqual(of_turns.session_of("/h/.claude/projects/p/s-parent/subagents/agent-a1.jsonl", "agent-a1"), "s-parent")
        self.assertEqual(of_turns.session_of("/h/.claude/projects/p/s1.jsonl", "s1"), "s1")
        with tempfile.TemporaryDirectory() as d:
            sub = os.path.join(d, "s-parent", "subagents")
            os.makedirs(sub)
            p = os.path.join(sub, "agent-a1.jsonl")
            with open(p, "w") as fh:
                fh.write(json.dumps(user("Review it", isSidechain=True, sessionId="agent-a1", timestamp="2026-11-20T10:00:00Z")))
            self.assertEqual(of_turns.file_records(p)[0][1], "s-parent")
            link = os.path.join(d, "link.jsonl")
            os.symlink(p, link)
            os.mkfifo(os.path.join(d, "pipe.jsonl"))
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(of_turns.file_records(link), [])
                self.assertEqual(of_turns.file_records(os.path.join(d, "pipe.jsonl")), [])
            self.assertEqual(err.getvalue().count("not a regular file"), 2)

    def test_count_and_a_transcript_file(self):
        lines = [user("Message from x:of_a (via relay, authority: none)", timestamp="2026-11-20T10:00:00Z",
                      sessionId="s1"),
                 {"type": "assistant", "timestamp": "2026-11-20T10:00:05Z", "sessionId": "s1",
                  "message": {"id": "m1", "model": "claude-opus-5-5",
                              "usage": {"input_tokens": 3, "output_tokens": 7, "cache_read_input_tokens": 11,
                                        "cache_creation_input_tokens": 5,
                                        "cache_creation": {"ephemeral_1h_input_tokens": 5}}}},
                 user("hello", timestamp="2026-11-21T09:00:00Z", sessionId="s1")]
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s1.jsonl")
            with open(p, "w") as fh:
                fh.write("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
            recs = of_turns.file_records(p)
        out = of_turns.count(of_turns.turns(recs, since=of_turns._t("2026-11-20T00:00:00Z")), ("day", "position", "starter"),
                             {"s1": "of_b"})
        self.assertEqual(out[("2026-11-20", "of_b", "relay")]["responses"], 1)
        self.assertEqual(out[("2026-11-20", "of_b", "relay")]["cache_read"], 11)
        self.assertEqual(out[("2026-11-21", "of_b", "typed")]["turns"], 1)
        text = of_turns.table(out, ("day", "position", "starter"))
        self.assertNotIn("hello", text)
        self.assertIn("2 turns · 1 responses", text)


if __name__ == "__main__":
    unittest.main()
