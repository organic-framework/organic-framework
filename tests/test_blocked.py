"""Blocking screens (of_blocked): each dialog type on a fixture screen, and the asks it makes."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_asks  # noqa: E402
import of_blocked  # noqa: E402

TRUST = """
 Do you trust the files in this folder?

 /Users/alex/acme/new-repo

 Claude Code may read, write, or execute files contained in this directory. This can pose security risks.

 ❯ 1. Yes, proceed
   2. No, exit

 Enter to confirm · Esc to cancel
"""
MCP = """
 MCP server "linear" needs authentication.
 ❯ 1. Authenticate
   2. Skip for now
"""
ENTER = "Something happened.\n\n Press Enter to continue…\n"
LIMIT = "\n  5-hour limit reached ∙ resets 3pm\n  /upgrade to increase your usage limit.\n"
LOGIN = "\n Invalid API key · Please run /login\n"
EXIT = "\n Exit and stop tasks?\n ❯ 1. Yes, exit and stop 2 background tasks\n   2. No, keep running\n"
COMPACT = "\n".join(["─" * 40, " We recommend compacting earlier", "", "   ❯ Autocompact at ‹ 400k › tokens",
                     "     Keep 1m tokens", "", "   ↑/↓ choose · ←/→ change size · enter confirm · esc not now"])
MENU = "\n Which model?\n ❯ 1. Opus\n   2. Sonnet\n   3. Haiku\n"
PROMPT = "\n⏺ Done. Committed 1edb0e8.\n\n╭────────╮\n│ > \n╰────────╯\n  ? for shortcuts\n"


class BlockedTest(unittest.TestCase):
    def test_types(self):
        got = {name: (of_blocked.detect(scr) or {}).get("type") for name, scr in
               (("trust", TRUST), ("mcp", MCP), ("enter", ENTER), ("limit", LIMIT), ("login", LOGIN),
                ("exit", EXIT), ("compact", COMPACT), ("menu", MENU), ("prompt", PROMPT))}
        self.assertEqual(got, {"trust": "trust", "mcp": "mcp_auth", "enter": "press_enter", "limit": "limit",
                               "login": "login", "exit": "exit", "compact": "compact", "menu": "menu", "prompt": None})

    def test_the_compaction_offer(self):
        """of_server, 2026-11-10 (msg 01a1c650): the relay knew this dialog (b9e3ff5), the detector did not. Its rows
        carry no numbers, so the card offers Esc first (nothing changes) and Enter (compacts)."""
        d = of_blocked.detect(COMPACT)
        self.assertEqual([of_blocked.keys_for(o) for o in d["options"]], [["esc"], ["Enter"]])
        self.assertEqual(d["to"], "founder")
        self.assertIn("compact", of_blocked.question(d, "of_x"))

    def test_a_limit_warning_under_an_open_prompt_is_not_a_stop(self):
        """2026-11-09, ask fb158869 (of_capacity): accounting_keeper idle at its prompt became a limit ask."""
        warn = ("\n⏺ Sent to acme_throughput-owner.\n\n╭────────╮\n│ > \n╰────────╯\n"
                "  You've used 90% of your 5-hour limit · resets 3:10am\n  ⏵⏵ bypass permissions on\n")
        self.assertIsNone(of_blocked.detect(warn))
        self.assertEqual(of_blocked.detect(warn + LIMIT)["type"], "limit")  # a real stop on the same screen still is

    def test_a_limit_line_before_a_finished_turn_is_history(self):
        """2026-11-11, ask ce572545 (of_capacity): a scheduled run's old limit line above an idle prompt."""
        old = LIMIT + "\n✻ Worked for 0s · done 6:27 PM\n\n> \n  ⏵⏵ bypass permissions on\n"
        self.assertIsNone(of_blocked.detect(old))
        self.assertEqual(of_blocked.detect(old + LIMIT)["type"], "limit")  # a new stop after it still is one

    def test_the_inbox_note_for_a_limit(self):
        d = of_blocked.detect(LIMIT)
        self.assertIn("limit", of_blocked.FYI)
        self.assertNotIn("login", of_blocked.FYI)  # a login needs of_capacity: typed
        note = of_blocked.fyi(dict(d, snippet=d["snippet"] + "\n see @/Users/alex/x.log"), "obs_keeper", "wF:pM",
                              "01a1c0b1-305c-72e6-a94f-6a17fb158869")
        self.assertTrue(note.startswith("obs_keeper shows a limit screen in pane wF:pM (ask fb158869"))
        self.assertIn("5-hour limit reached", note)
        self.assertNotIn("@/", note)  # no file mention for the receiving session

    def test_trust_details_and_routing(self):
        d = of_blocked.detect(TRUST)
        self.assertEqual(d["folder"], "/Users/alex/acme/new-repo")
        self.assertEqual([o["key"] for o in d["options"]], ["1", "2"])
        self.assertEqual(d["to"], "founder")
        self.assertIn("/Users/alex/acme/new-repo", of_blocked.question(d, "x"))
        self.assertEqual(of_blocked.detect(LIMIT)["to"], "of_capacity")
        self.assertEqual(of_blocked.keys_for({"key": "Enter"}), ["Enter"])
        # the cursor moving between options does not change the dialog's identity
        moved = TRUST.replace("❯ 1. Yes, proceed", "  1. Yes, proceed").replace("   2. No, exit", " ❯ 2. No, exit")
        self.assertEqual(of_blocked.detect(moved)["fingerprint"], d["fingerprint"])

    def test_projection(self):
        rows = [{"event_id": "b1", "ts": "2026-11-06T10:00:00Z", "name": "of.agent_question", "session_id": "S",
                 "position": "k8s_keeper", "payload": {"detected_by": "blocked", "blocked_type": "trust", "pane": "w1:p1",
                                                       "questions": [{"question": "trust?"}]}},
                {"event_id": "h1", "ts": "2026-11-06T10:01:00Z", "name": "of.turn_end", "session_id": "S",
                 "position": "k8s_keeper", "payload": {}}]
        asks = of_asks.project(rows)
        self.assertEqual([(a["kind"], a["state"]) for a in asks], [("blocked:trust", "open")])  # not closed by activity
        rows.append({"event_id": "c1", "ts": "2026-11-06T10:02:00Z", "name": "of.agent_question_answered",
                     "session_id": None, "position": None,
                     "payload": {"ask_ids": ["b1"], "via": "reconcile", "reason": "dialog_closed"}})
        self.assertEqual(of_asks.project(rows)[0]["state"], "moved_on")


if __name__ == "__main__":
    unittest.main()


class ListIsNotMenuTest(unittest.TestCase):
    def test_numbered_text_with_the_prompt_open(self):
        screen = ("  1. Question cards: what, why now.\n  2. AWS. Any change through aws_keeper.\n\n"
                  "✻ Cogitated for 10s\n" + "─" * 40 + "\n❯\n" + "─" * 40 + "\n  ⏵⏵ bypass permissions on · 2 shells\n")
        self.assertIsNone(of_blocked.detect(screen))
        self.assertEqual(of_blocked.detect(MENU)["type"], "menu")
