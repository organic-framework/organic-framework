"""lab-routing-v0, the relay regression suite — the fast unit layer (of_architect, 2026-11-07).

The relay types into every agent's pane, and in one day it broke four ways. This file guards each fault
at the level of the pure function the fix lives in, so `python3 -m unittest` catches a regression without a
VM. The end-to-end scenarios against real Claude Code in the lab VM (where the @-expansion and the input box
actually live) are built separately once the Lab's Claude login is available; these unit guards stand regardless.

  Fault 1  at-path mentions typed into a pane      -> of_relay_client.neutralize_mentions (0719c7e)
  Fault 2  tool texts delivered without a frame     -> of_relay_client.frame
  Fault 3  typing into a non-empty input box        -> of_relay_client.input_box_text (9858684, the block loop)
  Fault 4  scheduled prompts read as the owner's    -> of_hook.prompt_origin
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import of_relay_client as rc  # noqa: E402
import of_hook  # noqa: E402

FF = "＠"  # U+FF20 fullwidth at sign: the neutralized form


class Fault1Mentions(unittest.TestCase):
    """An '@' that could start a file mention is neutralized; everything else is left alone."""

    def test_at_before_path_is_neutralized(self):
        out, n = rc.neutralize_mentions("see @config/secrets/x.env now")
        self.assertEqual(n, 1)
        self.assertNotIn("@", out)
        self.assertIn(FF, out)
        self.assertIn("config/secrets/x.env", out)  # the path text is kept, only the sigil changes

    def test_at_at_start_and_after_paren(self):
        self.assertEqual(rc.neutralize_mentions("@/etc/wireguard/wg0.conf")[1], 1)
        self.assertEqual(rc.neutralize_mentions("(@.env)")[1], 1)

    def test_email_is_not_a_mention(self):
        out, n = rc.neutralize_mentions("write to a@b.com please")
        self.assertEqual(n, 0)
        self.assertIn("a@b.com", out)

    def test_idempotent(self):
        once, _ = rc.neutralize_mentions("ping @.env twice")
        twice, n = rc.neutralize_mentions(once)
        self.assertEqual(n, 0)
        self.assertEqual(once, twice)


class Fault2Frame(unittest.TestCase):
    """A tool's text is framed as a peer message; an already-framed text is never framed twice."""

    def test_unframed_gets_a_frame(self):
        out = rc.frame("do the thing", sender="of_platform")
        self.assertTrue(out.startswith("Message from of_platform (via of, authority: none)\n"))
        self.assertIn("do the thing", out)

    def test_already_framed_is_unchanged(self):
        once = rc.frame("hi", sender="org:pos")
        self.assertEqual(rc.frame(once), once)

    def test_pasted_wrapped_frame_not_doubled(self):
        wrapped = '<pasted_content id="1">\nMessage from org:pos (via of, authority: none)\nhi'
        self.assertEqual(rc.frame(wrapped), wrapped)


class Fault3InputBox(unittest.TestCase):
    """A box holding text defers the delivery; a dim suggestion counts as empty; no box -> None."""

    RULE = "─" * 40

    def _screen(self, between):
        return "top line\n" + self.RULE + "\n" + between + "\n" + self.RULE + "\n? for shortcuts"

    def test_text_in_box_is_read(self):
        self.assertEqual(rc.input_box_text(self._screen("❯ half typed words")), "half typed words")

    def test_dim_suggestion_is_empty(self):
        self.assertEqual(rc.input_box_text(self._screen("❯ \x1b[2mTry \"edit\"\x1b[0m")), "")

    def test_no_box_is_none(self):
        self.assertIsNone(rc.input_box_text("a screen with no rules at all"))


class Fault4Origin(unittest.TestCase):
    """A scheduled or peer prompt is never classified as the owner's own words."""

    def test_scheduled_prefixes(self):
        for p in ("<task-notification>x", "<scheduled>x", "<system-reminder>x"):
            self.assertEqual(of_hook.prompt_origin(p, {}), "scheduled", p)

    def test_poll_event_source(self):
        self.assertEqual(of_hook.prompt_origin("do x", {"source": "poll_event"}), "scheduled")

    def test_cross_session_is_peer(self):
        self.assertEqual(of_hook.prompt_origin("<cross-session-message from=x>hi", {}), "peer")

    def test_plain_prompt_is_human(self):
        self.assertEqual(of_hook.prompt_origin("please do the thing", {}), "human")


if __name__ == "__main__":
    unittest.main()
