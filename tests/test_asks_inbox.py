"""Layout A of the owner's questions (bin/of_asks_inbox.py; the founder's choice, 2026-11-12, of_conductor's
asks-herdr-plugin-design.md): streams, wrap-around selection that new cards never move, a key that answers the card on
the screen, a second press for money, the narrow fallback, snooze and hand-over records."""

import datetime as dt
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_asks_inbox as I  # noqa: E402
import of_asks_tui as T  # noqa: E402
import of_events  # noqa: E402
import of_locale  # noqa: E402
import of_store  # noqa: E402


def item(aid, text, kind="question (declared)", since="2026-11-11T10:00:00Z", **kw):
    return dict({"who": [kw.pop("who", "of_a")], "text": text, "since": since, "ids": [aid], "kind": kind,
                 "first": {"text": text, "tags": kw.pop("tags", [])}}, **kw)


class EditorTest(unittest.TestCase):
    """The reply field's cursor (the founder, 2026-11-12 18:41Z: up and down did not move in the field)."""

    def test_up_and_down_move_by_wrapped_rows_and_keep_their_column(self):
        self.assertEqual(I.wrap_line("one two three four", 8), [(0, 8), (8, 14), (14, 18)])
        ed = I.Editor("one two three four")
        self.assertEqual((ed.cl, ed.cc), (0, 18))          # it opens at the end
        ed.vertical(-1, 8)
        self.assertEqual(ed.cc, 12)                          # "thre|e": the same column, one row up
        ed.vertical(-1, 8)
        self.assertEqual(ed.cc, 4)                           # "one |two"
        ed.vertical(-1, 8)
        self.assertEqual(ed.cc, 4)                           # the first row: it stays
        ed.vertical(1, 8)
        ed.vertical(1, 8)
        self.assertEqual(ed.cc, 18)                          # back down to the column it started from
        ed.insert("!")
        self.assertEqual(ed.text(), "one two three four!")

    def test_left_right_home_end_and_joins(self):
        ed = I.Editor("abc\nde")
        ed.vertical(-1, 80)
        self.assertEqual((ed.cl, ed.cc), (0, 2))
        ed.left()
        ed.insert("X")
        self.assertEqual(ed.text(), "aXbc\nde")
        ed.end()
        ed.right()
        self.assertEqual((ed.cl, ed.cc), (1, 0))             # past the line's end: the next line's start
        ed.left()
        self.assertEqual((ed.cl, ed.cc), (0, 4))
        ed.home()
        self.assertEqual(ed.cc, 0)
        ed.end()
        ed.delete()                                          # at a line's end: the next line joins it
        self.assertEqual(ed.lines, ["aXbcde"])
        ed.newline()
        ed.backspace()                                       # at a line's start: it joins the line above
        self.assertEqual(ed.lines, ["aXbcde"])


class LogicTest(unittest.TestCase):
    def test_the_risk_of_a_card_is_kept_until_its_words_change(self):
        a = item("A", "Pay 90 USD to the operator?")
        self.assertEqual(I.risk_of(a), ["money"])
        self.assertEqual(I.risk_of(dict(a)), ["money"])
        self.assertIn((I.key_of(a), a["text"], repr(a["first"])), I._RISK)
        b = item("A", "Rename the channel?")
        self.assertEqual(I.risk_of(b), [])

    def test_streams_and_their_order(self):
        now = T._ts("2026-11-11T12:00:00Z")
        xs = [item("A", "Rename the channel?", since="2026-11-11T09:00:00Z"),
              item("B", "Rename the other channel?", since="2026-11-11T08:00:00Z"),
              item("C", "Pay 90 USD to the operator?"),
              item("D", "Log in to the console", task=True),
              item("E", "Lift the hold?", kind="question (declared) blocking"),
              item("F", "Choose the window?", due="2026-11-11T12:30:00Z"),
              item("G", "Restart it?", tags=[{"tag": "prod_change", "systems": ["k8s"]}]),
              item("H", "Allow the tool?", kind="permission")]
        g = I.split(xs, {"C": ("handed", "of_b")}, now=now)
        self.assertEqual([x["ids"][0] for x in g["holds"]], ["F", "E", "H"])  # due soonest first, then oldest
        self.assertEqual([x["ids"][0] for x in g["risk"]], ["G"])
        self.assertEqual([x["ids"][0] for x in g["hands"]], ["D"])
        self.assertEqual([x["ids"][0] for x in g["rest"]], ["B", "A"])
        self.assertEqual([x["ids"][0] for x in g["handed"]], ["C"])
        self.assertEqual(I.shown_streams(g), ["holds", "risk", "hands", "rest", "handed"])
        self.assertEqual(I.stream_of(item("C", "Pay 90 USD to the operator?"), now), "risk")  # its words count

    def test_selection_wraps_at_both_ends(self):
        self.assertEqual((I.step(3, 0, -1), I.step(3, 2, 1), I.step(3, 1, 1)), (2, 0, 2))
        self.assertEqual((I.step(3, 0, -5, wrap=False), I.step(3, 2, 5, wrap=False)), (0, 2))

    def test_snooze_presets(self):
        now = time.time()
        self.assertEqual(I.snooze_until("hour", now), now + 3600)
        m = dt.datetime.fromtimestamp(I.snooze_until("morning", now))
        self.assertEqual((m.hour, m.minute, m.date()), (9, 0, dt.date.fromtimestamp(now) + dt.timedelta(days=1)))
        w = dt.datetime.fromtimestamp(I.snooze_until("week", now))
        self.assertEqual((w.weekday(), w.hour), (0, 9))
        self.assertTrue(0 < I.snooze_until("week", now) - now <= 7 * 86400 + 3600)

    def test_money_needs_a_second_press(self):
        self.assertTrue(I.needs_confirm(item("C", "Pay 90 USD to the operator?")))
        self.assertTrue(I.needs_confirm(item("X", "Rename it?"), {"risk": ["irreversible"]}))
        self.assertFalse(I.needs_confirm(item("G", "Restart it?", tags=[{"tag": "prod_change"}])))
        self.assertFalse(I.needs_confirm(item("A", "Rename the channel?")))

    def test_the_card_marks_the_recommended_option_and_says_why_now(self):
        of_locale.set_language("en")
        try:
            c = {"question": "Run the upgrade today?\nThe window is open.", "who": ["of_a"], "org": "ofb",
                 "options": ["move it to Thursday", "run it today (recommended: the window is short)"],
                 "answerable": True, "context": "I checked the nodes.", "links": ["https://x/1"]}
            rows = I.card_rows(c, item("A", "Run the upgrade today?", kind="question (declared) blocking"), 60)
        finally:
            of_locale.set_language(None)
        text = [r for r, _ in rows]
        self.assertEqual(rows[0], ("Run the upgrade today?", "title"))
        self.assertIn("of_a · ofb · waits", text[1])
        self.assertTrue(any(t.startswith("Why now: of_a is stopped until you answer") for t in text))
        self.assertIn(("★ 2  run it today (recommended: the window is short)", "recommended"), rows)
        self.assertEqual(text[-1], "Links: https://x/1")


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = self.tmp.name
        of_events._CONN.clear()
        self.conn = of_store.connect(os.path.join(self.tmp.name, "of.db"))

    def tearDown(self):
        of_events._CONN.clear()
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def add(self, kind, payload, ts, position="of_a", session="S1"):
        rec = of_events.record(kind, payload, position=position, session=session, org="ofb")
        rec["timestamp"] = ts
        of_store.add_event(self.conn, rec)
        return rec["of.event.id"]

    def test_overlay_reads_the_latest_record_per_ask(self):
        now = T._ts("2026-11-11T12:00:00Z")
        self.add("ask_snoozed", {"ask_ids": ["A"], "until": "2026-11-11T13:00:00Z"}, "2026-11-11T11:00:00Z")
        self.add("ask_snoozed", {"ask_ids": ["B"], "until": "2026-11-11T11:30:00Z"}, "2026-11-11T11:00:00Z")
        self.add("ask_handed", {"ask_ids": ["C"], "to": "of_b"}, "2026-11-11T11:00:00Z")
        self.add("ask_handed", {"ask_ids": ["D"], "to": "of_b"}, "2026-11-11T11:00:00Z")
        self.add("ask_handed", {"ask_ids": ["D"], "to": None}, "2026-11-11T11:10:00Z")
        o = I.overlay(self.conn, now)
        self.assertEqual(o, {"A": ("later", T._ts("2026-11-11T13:00:00Z")), "C": ("handed", "of_b")})

    def test_a_handed_card_reaches_the_receivers_pane(self):
        import of_asks
        import of_asks_answer
        a = self.add("agent_question", {"detected_by": "declared", "questions": [
            {"question": "Rename the channel?", "options": ["yes", "no"]}]}, "2026-11-11T10:00:00Z")
        self.add("ask_handed", {"ask_id": a, "ask_ids": [a], "to": "of_b", "by": "owner"}, "2026-11-11T10:05:00Z")
        asks = of_asks.project(of_asks.load(self.conn))
        now = T._ts("2026-11-11T10:10:00Z")
        todo = [x for x in of_asks_answer.pending(self.conn, asks, [{"name": "of_b"}], now=now)
                if x["key"].startswith("handed:")]
        self.assertEqual([x["position"] for x in todo], ["of_b"])
        self.assertIn(f"handed to you; ask {a[-8:]}", todo[0]["text"])
        self.assertIn(f"of asks answered {a[-8:]}", todo[0]["text"])
        self.assertIn("Options: yes | no", todo[0]["text"])
        # no fixed position name (of_herdr's review of PR 26): without a conductor in the registry, the owner himself
        self.assertNotIn("of_conductor", todo[0]["text"])
        self.assertIn("ask the founder yourself (of ask)", todo[0]["text"])
        regs = [{"name": "of_b", "_org": "ofb"},
                {"name": "acme_conductor", "role": "conductor", "owner": "acme_owner", "_org": "acme"},
                {"name": "acme_owner", "owner": "acme_owner", "holder": {"runtime": "human"}, "_org": "acme"},
                {"name": "ofb_conductor", "role": "conductor", "owner": "founder", "_org": "ofb"}]
        named = [x for x in of_asks_answer.pending(self.conn, asks, regs, now=now) if x["key"].startswith("handed:")]
        self.assertIn("If it is not yours to settle, tell ofb_conductor.", named[0]["text"])
        self.add("human_prompt", {"origin": "injected", "text": todo[0]["text"]}, "2026-11-11T10:11:00Z",
                 position="of_b", session="S2")
        later = of_asks_answer.pending(self.conn, asks, [{"name": "of_b"}], now=now + 120)
        self.assertFalse([x for x in later if x["key"].startswith("handed:")])


class ScreenTest(unittest.TestCase):
    """The window on a fake screen: keys in, frames out."""

    def setUp(self):
        import curses
        self.saved = {k: getattr(curses, k) for k in ("curs_set", "use_default_colors", "init_pair", "color_pair",
                                                      "flushinp", "mousemask", "getmouse", "mouseinterval")}
        curses.curs_set = curses.use_default_colors = curses.flushinp = curses.mousemask = lambda *a: None
        curses.mouseinterval = lambda *a: None
        self.mice = []
        curses.getmouse = lambda: self.mice.pop(0)
        curses.init_pair = lambda *a: None
        curses.color_pair = lambda n: 0
        self.undo, T.UNDO_S = T.UNDO_S, 0.1
        of_locale.set_language("en")
        self.frames, self.sent, self.records = [], [], []

    def tearDown(self):
        import curses
        for k, v in self.saved.items():
            setattr(curses, k, v)
        T.UNDO_S = self.undo
        of_locale.set_language(None)

    def scr(self, keys, size=(40, 140)):
        test = self

        class Scr:
            def __init__(self):
                self.keys, self.cells = list(keys), {}

            def getmaxyx(self):
                return size

            def erase(self):
                self.cells = {}

            def refresh(self):
                test.frames.append(dict(self.cells))

            def addnstr(self, y, x, text, n, *a):
                self.cells[(y, x)] = text[:n]

            def timeout(self, t):
                pass

            def getch(self):
                k = self.keys.pop(0) if self.keys else ord("q")
                if isinstance(k, float):
                    time.sleep(k)
                    return -1
                return k

            def get_wch(self):
                import curses
                k = self.keys.pop(0) if self.keys else "\x1b"
                if isinstance(k, float):
                    time.sleep(k)
                    raise curses.error("no input")
                return k

            def move(self, y, x):
                test.cursor = (y, x)
        return Scr()

    def text(self, frame):
        return "\n".join(t for _, t in sorted(frame.items()))

    def inbox(self, keys, data, cards=None, size=(40, 140), loader=None, seq=None, store=None, flush=False):
        """flush: curses.flushinp drops the keys not read yet, as a terminal drops a paste's rest."""
        import curses
        cards = cards or {}

        def card_of(it):
            c = dict(cards.get(it["ids"][0]) or {"options": ["yes", "no"]})
            return dict({"question": it["text"], "who": it["who"], "answerable": True, "ids": it["ids"]}, **c)

        def answer(it, text):
            self.sent.append((it["ids"][0], text))
            return True, "recorded"
        scr = self.scr(keys, size)
        if flush:
            curses.flushinp = lambda: scr.keys.clear()
        box = I.Inbox(scr, loader or (lambda: ([("Waiting on you", data)], {})), card_of, answer,
                      lambda what, it: (self.sent.append((it["ids"][0], what)) or (True, what)),
                      positions=lambda: [{"name": "of_b"}, {"name": "of_conductor"}],
                      record=lambda kind, payload: self.records.append((kind, payload)) or "E1", seq=seq,
                      initial=(list(data), {}), reply_store=store)
        box.run()
        return box

    def test_two_panes_streams_and_wrap(self):
        data = [item("A", "First plain question?", since="2026-11-11T08:00:00Z"),
                item("B", "Second plain question?", since="2026-11-11T09:00:00Z"),
                item("C", "Pay 90 USD to the operator?")]
        box = self.inbox([0.05, ord("\t"), 0.05, ord("k"), 0.05], data)
        frame = self.text(self.frames[0])
        self.assertIn("● Money and production 1", frame)
        self.assertIn("● Everything else 2", frame)
        self.assertIn("│", frame)
        self.assertIn("↑↓ select · ←→ stream · 1-9 answer", frame)
        # it opens on the most urgent stream that holds anything (Money and production), Tab goes to Everything
        # else (the empty ones are skipped); up from its first card: its last
        self.assertEqual((box.cur, box.sel["rest"]), ("rest", "B"))
        self.assertIn("Second plain question?", self.text(self.frames[-1]))

    def test_a_key_answers_the_card_on_screen_and_its_row_stays_until_recorded(self):
        data = [item("A", "First plain question?", since="2026-11-11T08:00:00Z"),
                item("B", "Second plain question?", since="2026-11-11T09:00:00Z")]
        box = self.inbox([0.05, ord("2"), 0.02, 0.3, 0.05], data)
        self.assertEqual(self.sent, [("A", "no")])
        self.assertEqual(box.sel["rest"], "B")  # the next card that waits
        self.assertTrue(any("First plain question? → no" in self.text(f) for f in self.frames))

    def test_new_cards_never_move_the_selection(self):
        a = item("A", "First plain question?", since="2026-11-11T08:00:00Z")
        b = item("B", "Second plain question?", since="2026-11-11T09:00:00Z")
        new = item("N", "A newer plain question?", since="2026-11-11T07:00:00Z")  # sorts above both
        calls = {"n": 0}

        def loader():
            calls["n"] += 1
            return ([("Waiting on you", [a, b] + ([new] if calls["n"] > 1 else []))], {})
        ticks = {"n": 0}

        def seq():
            ticks["n"] += 1
            return 1 if ticks["n"] > 3 else 0
        box = self.inbox([0.05, ord("j"), 0.05, -1, 0.1, -1, 0.05, ord("1"), 0.3, 0.05], [a, b], loader=loader, seq=seq)
        self.assertEqual(calls["n"] > 1, True)
        self.assertEqual(self.sent, [("B", "yes")])  # still the card he had selected

    def test_the_card_closed_elsewhere_holds_the_answer_keys(self):
        a = item("A", "First plain question?", since="2026-11-11T08:00:00Z")
        b = item("B", "Second plain question?", since="2026-11-11T09:00:00Z")
        calls = {"n": 0}

        def loader():
            calls["n"] += 1
            return ([("Waiting on you", [a, b] if calls["n"] == 1 else [b])], {})
        ticks = {"n": 0}

        def seq():
            ticks["n"] += 1
            return 1 if ticks["n"] > 2 else 0
        self.inbox([0.05, -1, 0.1, -1, ord("1"), 0.05], [a, b], loader=loader, seq=seq)
        self.assertEqual(self.sent, [])
        self.assertTrue(any("closed elsewhere" in self.text(f) for f in self.frames))

    def test_money_takes_a_second_press(self):
        data = [item("C", "Pay 90 USD to the operator?")]
        self.inbox([0.05, ord("1"), ord("x"), 0.2, ord("1"), ord("1"), 0.3, 0.05], data)
        self.assertEqual(self.sent, [("C", "yes")])
        self.assertTrue(any("press 1 again" in self.text(f) for f in self.frames))

    def test_a_task_closes_with_done(self):
        data = [item("D", "Log in to the console", task=True, steps="1. Open the page.")]
        self.inbox([0.05, ord("1"), 0.3, 0.05], data)
        self.assertEqual(self.sent, [("D", "answered")])
        self.assertTrue(any("1  Done (the agent checks the result)" in self.text(f) for f in self.frames))

    def test_snooze_and_hand_over_are_recorded(self):
        data = [item("A", "First plain question?"), item("B", "Second plain question?")]
        box = self.inbox([0.05, ord("h"), ord("2"), 0.1, ord("d")] + [ord(c) for c in "of_b"] + [10, 0.1], data)
        kinds = [(k, p["ask_ids"]) for k, p in self.records]
        self.assertEqual([k for k, _ in kinds], ["ask_snoozed", "ask_handed"])
        self.assertEqual(self.records[0][1]["preset"], "morning")
        self.assertEqual(self.records[1][1]["to"], "of_b")
        self.assertEqual(box.over["A"][0], "later")
        self.assertEqual(box.over[self.records[1][1]["ask_ids"][0]], ("handed", "of_b"))

    def test_narrow_screens_show_one_card(self):
        data = [item("A", "First plain question?"), item("B", "Second plain question?")]
        self.inbox([0.05, ord("j"), 0.05], data, size=(30, 80))
        frame = self.text(self.frames[-1])
        self.assertIn("‹ 2 / 2 ›", frame)
        self.assertNotIn("│", frame)

    def test_the_wheel_scrolls_what_is_under_the_pointer_and_a_click_selects(self):
        import curses
        data = [item(c, f"Plain question {c}?", since=f"2026-11-11T0{i}:00:00Z") for i, c in enumerate("ABC", 1)]
        long = {"A": {"options": ["yes", "no"], "context": "\n".join(f"line {i}" for i in range(80))}}
        self.mice = [(0, 10, 3, 0, I.WHEEL_DOWN & -I.WHEEL_DOWN),  # over the queue: the next card
                     (0, 100, 5, 0, 0x200000),                     # over the card, as macOS's ncurses says it
                     (0, 10, 4, 0, curses.BUTTON1_PRESSED)]        # a press on the third row
        box = self.inbox([0.05, curses.KEY_MOUSE, 0.05, curses.KEY_MOUSE, 0.05, curses.KEY_MOUSE, 0.05], data,
                         cards=long)
        self.assertEqual(box.sel["rest"], "C")
        self.assertTrue(any("line" in self.text(f) for f in self.frames))
        self.assertGreaterEqual(box.ctop, 0)

    def row_of(self, frame, text):
        return next((y for (y, x), t in frame.items() if x == 2 and text in t), None)

    def test_the_selected_row_stays_put_when_a_card_arrives_above_even_if_the_queue_fits(self):
        """of_harness 2026-11-12 (asks-screen-v0): the row moved from screen line 8 to 9 while the queue fit."""
        a = item("A", "First plain question?", since="2026-11-11T08:00:00Z")
        b = item("B", "Second plain question?", since="2026-11-11T09:00:00Z")
        new = item("N", "A newer plain question?", since="2026-11-11T07:00:00Z")
        calls, ticks = {"n": 0}, {"n": 0}

        def loader():
            calls["n"] += 1
            return ([("Waiting on you", [a, b] + ([new] if calls["n"] > 1 else []))], {})

        def seq():
            ticks["n"] += 1
            return 1 if ticks["n"] > 3 else 0
        self.inbox([0.05, ord("j"), 0.05, -1, 0.1, -1, 0.05], [a, b], loader=loader, seq=seq)
        rows = [self.row_of(f, "Second plain question?") for f in self.frames if self.row_of(f, "Second plain")]
        self.assertEqual(len(set(rows)), 1, rows)
        last = self.frames[-1]
        self.assertTrue(any(t == "▲" for t in last.values()))  # the new card went above: marked

    def test_a_narrow_screen_keeps_every_stream_and_the_main_keys(self):
        data = [item("A", "First plain question?"), item("C", "Pay 90 USD to the operator?"),
                item("D", "Log in to the console", task=True), item("E", "Lift it?", kind="question (declared) blocking")]
        self.inbox([0.05], data, size=(30, 77))
        frame = self.frames[-1]
        top = "".join(t for (y, x), t in sorted(frame.items()) if y == 0)
        self.assertEqual(top.count("●"), 4)
        self.assertIn("Holds work 1", top)
        keys = next(t for (y, x), t in frame.items() if y == 28)
        self.assertTrue("←→" in keys and "?" in keys and "Esc" in keys, keys)

    def test_a_wide_screen_adds_the_agents_context_as_a_third_column(self):
        """The founder, ~21:08Z 2026-11-12: lots of room, more information."""
        data = [item("A", "First plain question?", who="of_platform")]
        cards = {"A": {"options": ["yes", "no"], "context": "I checked the nodes.", "links": ["https://x/1"],
                       "doing": {"mandate": "keeps the platform", "turns": ["ran the tests"]}}}
        self.inbox([0.1, 0.05], data, cards=cards, size=(40, 200))
        frame = self.frames[-1]
        card_x = min(x for (y, x), t in frame.items() if t == "First plain question?")
        side = {t for (y, x), t in frame.items() if x > card_x + 40}
        self.assertIn("What the agent is doing:", side)
        self.assertIn("I checked the nodes.", side)
        self.assertNotIn("I checked the nodes.", {t for (y, x), t in frame.items() if x == card_x})
        self.assertTrue(any(t.startswith("of_platform") for (y, x), t in frame.items() if x == 2))  # who, in the row

    def test_left_and_right_switch_streams_and_a_click_on_a_tab_does_too(self):
        """The founder, ~21:10Z 2026-11-12: clickable tabs, and the left and right arrows between them."""
        import curses
        data = [item("A", "First plain question?"), item("C", "Pay 90 USD to the operator?"),
                item("D", "Log in to the console", task=True)]
        box = self.inbox([0.05, curses.KEY_RIGHT, 0.05], data)
        self.assertEqual(box.cur, "hands")  # from Money and production, the next that holds anything
        self.frames = []
        box = self.inbox([0.05, curses.KEY_LEFT, 0.05], data)
        self.assertEqual(box.cur, "rest")  # wraps back to the last
        x0 = next(x0 for x0, x1, st in box.tab_rects if st == "hands")
        self.mice = [(0, x0 + 2, 0, 0, curses.BUTTON1_PRESSED)]
        box = self.inbox([0.05, curses.KEY_MOUSE, 0.05], data)
        self.assertEqual(box.cur, "hands")

    def test_the_options_stay_in_sight_under_a_long_card(self):
        """of_conductor from his first trial: a long card showed only its first option ('lines 1-31 of 40')."""
        long = {"A": {"question": "Back up the data room?\n" + "\n".join(f"paragraph {i}" for i in range(80)),
                      "options": ["yes, nightly (recommended: one evening closes the risk)", "no, not yet"]}}
        self.inbox([0.1, 0.05], [item("A", "Back up the data room?")], cards=long, size=(30, 140))
        frame = self.frames[-1]
        texts = [t for (y, x), t in sorted(frame.items())]
        self.assertTrue(any(t.startswith("★ 1  yes, nightly") for t in texts), texts)
        self.assertTrue(any("2  no, not yet" in t for t in texts))
        self.assertTrue(any(t.startswith("lines 1-") for t in texts))
        status_y = 30 - 3
        self.assertTrue(all(y < status_y for (y, x), t in frame.items() if "no, not yet" in t or t.startswith("lines")))

    def test_help(self):
        self.inbox([0.05, ord("?"), 0.05], [item("A", "First plain question?")])
        self.assertTrue(any("select; wraps from the first card to the last" in self.text(f) for f in self.frames))

    def test_the_answer_is_written_under_the_card_with_the_question_in_sight(self):
        """The founder, 2026-11-12 16:32Z (of_conductor msg 01a1d09c-4562): writing an answer hid the question; he
        wants the reply under the question, which stays readable and scrolls while he types, for long questions."""
        import curses
        long = {"A": {"question": "Back up the data room?\n" + "\n".join(f"paragraph {i}" for i in range(60)),
                      "options": ["yes", "no"]}}
        hello = "\u041f\u0440\u0438\u0432\u0435\u0442"  # a Cyrillic word, written as escapes (text here is English)
        typed = list(hello) + ["\n"] + list("second")
        box = self.inbox([0.1, ord("r")] + typed + [0.05, curses.KEY_NPAGE, 0.05, "\x04", 0.3, 0.05],
                         [item("A", "Back up the data room?", who="of_b")], cards=long, size=(30, 140))
        writing = [self.text(f) for f in self.frames if hello in self.text(f)]
        self.assertTrue(writing)
        self.assertIn("Back up the data room?", writing[0])          # the question, above the answer
        self.assertIn("── Your answer to of_b", writing[0])
        self.assertTrue(any("PgUp PgDn scroll" in t and "paragraph" in t for t in writing))  # it scrolls meanwhile
        self.assertIn("Ctrl+D send", writing[0])                      # the editor's keys on the keys line
        self.assertEqual(self.sent, [("A", hello + "\nsecond")])
        self.assertEqual(box.mode, "main")

    def test_a_pasted_blank_line_on_a_money_card_never_runs_the_rest_of_the_paste_as_keys(self):
        """of_herdr's review of PR 98: herdr pastes as plain text; a blank line sends, the card asks to confirm, and
        the paste's rest ("P", then "a": answered elsewhere) must not reach the screen's keys."""
        # Ctrl+D sends; keys typed ahead of the confirm are dropped, whatever they are
        self.inbox([0.1, ord("r"), "O", "K", "\x04", ord("P"), ord("a")],
                   [item("C", "Pay 90 USD to the operator?")], flush=True)
        self.assertEqual(self.sent, [])
        # of_review's probe: what follows holds a y, which would confirm the answer itself
        self.inbox([0.1, ord("r"), "o", "k", "\x04", ord("y"), ord("e"), ord("s")],
                   [item("C", "Pay 90 USD to the operator?")], flush=True)
        self.assertEqual(self.sent, [])

    def test_a_draft_outlives_the_window_and_one_whose_ask_closed_is_archived(self):
        with tempfile.TemporaryDirectory() as d:
            store = I.ReplyDrafts(d)
            long = "First plain question, with a title much longer than the queue's column has room for?"
            data = [item("A", long), item("B", "Second plain question?")]
            self.inbox([0.1, ord("r"), "a", "b", "\x1b", 0.05, ord("q")], data, store=store)
            self.assertEqual(store.load(), {"A": "ab"})
            self.assertEqual(oct(os.stat(os.path.join(d, "A.json")).st_mode & 0o777), "0o600")
            self.frames = []
            self.inbox([0.1, 0.05, ord("r"), "c", "\x04", 0.3, 0.05], data, store=store)  # a new window
            self.assertTrue(any("✎" in self.text(f) for f in self.frames[:3]))  # the row says it has a draft, even
            # when its title fills the row
            self.assertEqual(self.sent, [("A", "abc")])
            self.assertEqual(store.load(), {})  # sent: the draft is gone
            store.save("B", "half an answer")
            self.inbox([0.1, 0.05], [item("A", "First plain question?")], store=store)  # B closed elsewhere
            self.assertEqual(store.load(), {})
            self.assertEqual(len(os.listdir(os.path.join(d, "archive"))), 1)  # kept aside, never lost
            self.assertTrue(any("archived" in self.text(f) for f in self.frames))

    def test_the_arrows_move_in_the_field_and_the_card_scrolls_only_with_pgup_pgdn(self):
        import curses
        long = {"A": {"question": "Back up the data room?\n" + "\n".join(f"paragraph {i}" for i in range(60)),
                      "options": ["yes", "no"]}}
        box_keys = [0.1, ord("r")] + list("abcdef") + ["\n"] + list("gh") + [curses.KEY_UP, "X", 0.05]
        box = self.inbox(box_keys + [curses.KEY_DOWN, curses.KEY_END, "\x04", 0.3, 0.05],
                         [item("A", "Back up the data room?")], cards=long, size=(30, 140))
        self.assertEqual(self.sent, [("A", "abXcdef\ngh")])
        writing = [self.text(f) for f in self.frames if "abXcdef" in self.text(f)]
        self.assertTrue(writing and "Back up the data room?" in writing[0])  # up moved the cursor, not the card
        self.assertIn("↑↓←→ Home End move", writing[0])
        self.assertIn("PgUp PgDn scroll the card", writing[0])
        self.assertEqual(box.ctop, 0)

    def test_any_key_but_yes_at_the_confirm_goes_back_to_the_answer(self):
        """The founder, 2026-11-12 18:47Z (of_conductor msg 01a1d119-779b): three answers to a money card were lost to
        a key that was not y (a Russian layout, or a change he meant to make). Declining returns to the field with
        the whole text; the Russian layout's letter on y's key confirms."""
        with tempfile.TemporaryDirectory() as d:
            store = I.ReplyDrafts(d)
            money = [item("C", "Pay 90 USD to the operator?")]
            box = self.inbox([0.1, ord("r"), "o", "k", "\x04", "x", 0.05], money, store=store)
            self.assertEqual(self.sent, [])
            self.assertEqual(store.load(), {"C": "ok"})        # kept meanwhile
            self.assertTrue(any("back in the field" in self.text(f) for f in self.frames))
            self.assertTrue(any("ok" in self.text(f) and "Your answer to" in self.text(f) for f in self.frames[-3:]))
            self.inbox([0.1, ord("r"), "!", "\x04", "\u043d", 0.3, 0.05], money, store=store)  # the Russian y
            self.assertEqual(self.sent, [("C", "ok!")])
            self.assertEqual(store.load(), {})                 # confirmed and sent: the draft is gone

    def test_enter_and_shift_enter_only_add_lines_and_ctrl_d_sends(self):
        """The founder, 2026-11-12 18:47Z: two Shift+Enters (a terminal gives Enter) sent the answer."""
        self.inbox([0.1, ord("r"), "a", "\n", "\n", "b", "\n", "\n", 0.05, "\x04", 0.3, 0.05],
                   [item("A", "First plain question?")])
        self.assertEqual(self.sent, [("A", "a\n\nb")])

    def test_u_brings_a_written_answer_back_into_its_field(self):
        self.inbox([0.1, ord("r"), "a", "b", "c", "\x04", ord("u"), 0.05, "d", "\x04", 0.3, 0.05],
                   [item("A", "First plain question?")])
        self.assertEqual(self.sent, [("A", "abcd")])
        self.assertTrue(any("in the field again" in self.text(f) for f in self.frames))

    def test_a_long_paste_is_one_frame_not_one_per_character(self):
        """of_herdr's review of PR 104: herdr pastes as keys, and a frame per key cost seconds on a long paste."""
        with tempfile.TemporaryDirectory() as d:
            store = I.ReplyDrafts(d)
            paste = list(("Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 60)[:3000])
            self.inbox([0.1, ord("r")] + paste + [0.05, "\x1b", 0.05], [item("A", "First plain question?")],
                       store=store)
            self.assertLess(len(self.frames), 15)
            self.assertEqual(store.load(), {"A": "".join(paste)})

    def test_the_field_is_saved_while_he_writes(self):
        """A window herdr closes ends the process without a goodbye: the draft follows the field."""
        with tempfile.TemporaryDirectory() as d:
            store = I.ReplyDrafts(d)
            box = I.Inbox(self.scr([]), lambda: ([("Waiting on you", [])], {}), None, None, None,
                          reply_store=store)
            box.mode, box.reply = "reply", {"key": "A", "item": item("A", "Q?"), "ed": I.Editor("half"), "top": 0}
            box.autosave()
            self.assertEqual(store.load(), {"A": "half"})
            box.reply["ed"].insert(" more")
            box.autosave()                                     # within the second: not yet
            self.assertEqual(store.load(), {"A": "half"})
            box.reply["saved_at"] = 0
            box.autosave()
            self.assertEqual(store.load(), {"A": "half more"})

    def test_esc_keeps_the_draft_and_r_goes_on_with_it(self):
        self.inbox([0.1, ord("r"), "a", "b", "\x1b", 0.05, ord("r"), "c", "\x04", 0.3, 0.05],
                   [item("A", "First plain question?")])
        self.assertTrue(any("kept as a draft" in self.text(f) for f in self.frames))
        self.assertEqual(self.sent, [("A", "abc")])

    def test_g_goes_to_the_agent_and_the_keys_line_says_so(self):
        """The founder, 2026-11-12: g goes to the agent again, shown in the footer; Home and End move to the ends."""
        import curses
        data = [item("A", "First plain question?", since="2026-11-11T08:00:00Z"),
                item("B", "Second plain question?", since="2026-11-11T09:00:00Z")]
        box = self.inbox([0.05, curses.KEY_END, 0.05], data)
        self.assertEqual(box.sel["rest"], "B")
        self.assertIn("g go to the agent", self.text(self.frames[0]))
        self.sent = []
        self.inbox([0.05, ord("g")], data)
        self.assertEqual(self.sent, [("A", "go")])

    def test_a_card_built_before_the_registry_shows_its_options_and_is_completed(self):
        calls = []

        def card_of(it):
            calls.append(1)
            c = {"question": it["text"], "who": it["who"], "answerable": True, "ids": it["ids"],
                 "options": ["yes", "no"]}
            return dict(c, partial=True) if len(calls) == 1 else dict(c, context="I checked the nodes.")
        box = I.Inbox(self.scr([0.1, 0.05, 0.7, 0.1, 0.05]), lambda: ([("Waiting on you", [item("A", "Plain?")])], {}),
                      card_of, lambda it, text: (True, "recorded"), lambda what, it: (True, what),
                      initial=([item("A", "Plain?")], {}))
        box.run()
        self.assertTrue(any("1  yes" in self.text(f) and "I checked" not in self.text(f) for f in self.frames))
        self.assertIn("I checked the nodes.", self.text(self.frames[-1]))
        self.assertEqual(len(calls), 2)

    def test_the_opening_is_timed(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "asks-open.log")
            t0 = time.time()
            I.log_open({"start": t0, "frame": t0 + 0.2, "list": t0 + 0.4, "card": t0 + 1.5}, [40, 140], path)
            with open(path) as fh:
                row = json.loads(fh.read())
            self.assertEqual((row["frame"], row["list"], row["card"], row["size"]), (0.2, 0.4, 1.5, [40, 140]))
            self.assertIn("herdr", row)  # herdr's popup has HERDR_ENV and no pane id


class PanelTest(ScreenTest):
    """The small panel (bin/of_asks_panel.py; the founder ~20:45Z 2026-11-12 via of_conductor, msg 01a1cc5b)."""

    def test_the_key_comes_from_herdr_s_config(self):
        import of_asks_panel as P
        cfg = {"keys": {"command": [{"key": "prefix+a", "command": "sh -c '/x/main/bin/of asks --watch'"},
                                    {"key": "prefix+shift+a", "command": "sh -c '/x/wt/bin/of asks --watch'"}]}}
        self.assertEqual(P.open_keys(cfg), "Ctrl-B, then a")
        self.assertEqual(P.open_keys(cfg, of_path="/x/wt/bin/of"), "Ctrl-B, then Shift-A")
        self.assertEqual(P.open_keys(dict(cfg, keys=dict(cfg["keys"], prefix="ctrl+a"))), "Ctrl-A, then a")
        self.assertIsNone(P.open_keys({}))
        with tempfile.TemporaryDirectory() as d:  # herdr reads XDG_CONFIG_HOME when it is set
            os.makedirs(os.path.join(d, "herdr"))
            with open(os.path.join(d, "herdr", "config.toml"), "w") as fh:
                fh.write('[[keys.command]]\nkey = "prefix+shift+a"\ncommand = "of asks --watch"\n')
            saved = os.environ.get("XDG_CONFIG_HOME")
            os.environ["XDG_CONFIG_HOME"] = d
            try:
                self.assertEqual(P.open_keys(), "Ctrl-B, then Shift-A")
            finally:
                os.environ.pop("XDG_CONFIG_HOME") if saved is None else os.environ.update(XDG_CONFIG_HOME=saved)

    def panel(self, keys, data, size=(6, 140), open_cmd=None):
        import of_asks_panel as P
        ran = []
        box = P.Panel(self.scr(keys, size), lambda: ([("Waiting on you", data)], {}), initial=(list(data), {}),
                      keys="Ctrl-B, then a", open_cmd=open_cmd, run_cmd=ran.append)
        box.run()
        return box, ran

    def test_counts_wait_button_and_a_click(self):
        import curses
        data = [item("A", "First plain question?", since="2026-11-10T08:00:00Z"),
                item("C", "Pay 90 USD to the operator?"), item("D", "Log in to the console", task=True)]
        self.mice = [(0, 3, 3, 0, curses.BUTTON1_PRESSED), (0, 3, 3, 0, curses.BUTTON1_RELEASED)]
        box, ran = self.panel([0.05, curses.KEY_MOUSE, curses.KEY_MOUSE, 0.05], data, open_cmd=["herdr-open", "asks"])
        frame = self.text(self.frames[0])
        self.assertIn("● Money and production 1", frame)
        self.assertIn("● For your hands 1", frame)
        self.assertIn("Longest wait:", frame)
        self.assertIn("First plain question?", frame)
        self.assertIn(" Open the questions ", frame)
        self.assertIn("or Ctrl-B, then a", frame)
        self.assertEqual(box.button[0], 3)
        self.assertEqual(ran, [["herdr-open", "asks"]])

    def test_the_button_opens_the_popup_through_the_plugin(self):
        import of_asks_panel as P
        calls = []

        def popup():
            calls.append(1)
            return (True, None) if len(calls) == 1 else (False, "ui_busy")
        box = P.Panel(self.scr([0.3, 10, 0.3, 0.3, ord("o"), 0.3]), lambda: ([("Waiting on you", [])], {}),
                      initial=([], {}), keys="Ctrl-B, then a", popup=popup)
        box.run()
        self.assertEqual(len(calls), 2)
        text = "\n".join(self.text(f) for f in self.frames)
        self.assertIn("opening the questions…", text)
        self.assertIn("herdr shows another popup, Settings or Copy mode: close it, then try again", text)

    def test_a_new_build_restarts_the_panel(self):
        import of_asks_panel as P
        T.REFRESH_S, saved = 0.05, T.REFRESH_S
        try:
            box = P.Panel(self.scr([0.1, 0.1, 0.1]), lambda: ([("Waiting on you", [])], {}), initial=([], {}),
                          stale=lambda: True)
            self.assertEqual(box.run(), "restart")
        finally:
            T.REFRESH_S = saved

    def test_a_new_build_is_taken_only_when_still_and_whole(self):
        """The founder's trial panel died at 21:23Z restarting mid-rebase on a bin/of without --panel."""
        import of_asks_panel as P
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "of_asks_panel.py")
            open(f, "w").write("1")
            t, helps = [1000.0], []

            def run(argv, **kw):
                helps.append(argv)
                return type("R", (), {"returncode": 0, "stdout": "--watch --panel" if len(helps) > 1 else "--watch"})()
            r = P.Restarter([f], "/x/bin/of", run=run, clock=lambda: t[0])
            self.assertFalse(r.due())
            os.utime(f, (1, 1))                        # a rebase moves the file
            self.assertFalse(r.due())                  # still changing
            t[0] += P.SETTLE_S - 1
            self.assertFalse(r.due())                  # not still long enough
            t[0] += 2
            self.assertFalse(r.due())                  # still, but its CLI does not know --panel yet
            self.assertEqual(len(helps), 1)
            t[0] += P.SETTLE_S + 1
            self.assertTrue(r.due())                   # still and whole: restart
            self.assertEqual(helps[-1][1:], ["/x/bin/of", "asks", "--help"])

    def test_typed_text_opens_nothing(self):
        """of_herdr, 2026-11-12: a restart typed into the panel's pane ('o' and Enter in a command) opened the popup."""
        import of_asks_panel as P
        calls = []
        box = P.Panel(self.scr([0.3] + [ord(c) for c in "do of asks"] + [10, 0.3, 10, ord("o"), 0.3]),
                      lambda: ([("Waiting on you", [])], {}), initial=([], {}),
                      popup=lambda: calls.append(1) or (True, None))
        box.run()
        self.assertEqual(calls, [])

    def test_without_an_opener_the_button_says_the_keys(self):
        box, ran = self.panel([0.3, 10, 0.3], [item("A", "First plain question?")])
        self.assertEqual(ran, [])
        self.assertTrue(any("Press Ctrl-B, then a to open them." in self.text(f) for f in self.frames))

    def test_a_narrow_pane_puts_a_stream_on_each_line(self):
        data = [item("A", "First plain question?"), item("C", "Pay 90 USD to the operator?")]
        self.panel([0.05], data, size=(7, 30))
        rows = [t for (y, x), t in sorted(self.frames[-1].items())]
        self.assertEqual([r for r in rows if r.startswith("●")], ["● 0", "● 1", "● 0", "● 1"])  # too few rows: dots
        self.assertIn("or Ctrl-B, then a", rows)  # under the button when it does not fit beside it

    def test_a_tall_pane_lists_the_first_cards_of_each_stream(self):
        """The founder, ~21:08Z 2026-11-12: lots of room, more information; less room, less information."""
        import curses
        data = [item(c, f"Plain question {c}?", since=f"2026-11-11T0{i}:00:00Z", who=f"of_{c.lower()}")
                for i, c in enumerate("ABCDEF", 1)] + [item("M", "Pay 90 USD to the operator?", who="of_m")]
        self.mice = [(0, 5, 3, 0, curses.BUTTON1_RELEASED)]  # a click on a card row opens the questions too
        box, ran = self.panel([0.05, curses.KEY_MOUSE, 0.05], data, size=(12, 90), open_cmd=["open"])
        rows = [t for (y, x), t in sorted(self.frames[-1].items())]
        self.assertIn("● Money and production 1", rows)
        self.assertTrue(any(r.startswith("● Everything else 6") for r in rows), rows)
        self.assertTrue(any("of_a" in r and "Plain question A?" in r for r in rows))
        self.assertTrue(any("(+" in r for r in rows))  # what did not fit is counted
        self.assertIn("or Ctrl-B, then a", " ".join(rows))
        self.assertEqual(ran, [["open"]])
        self.assertFalse(any("Waiting on you" == r for r in rows))  # under WIDE_W: no second column at this height

    def test_a_wide_tall_pane_adds_who_waits_and_the_latest_answers(self):
        import of_asks_panel as P
        data = [item("A", "Plain question A?", since="2026-11-11T01:00:00Z", who="of_a"),
                item("B", "Plain question B?", since="2026-11-11T02:00:00Z", who="of_a"),
                item("C", "Plain question C?", since="2026-11-11T03:00:00Z", who="of_c")]
        recent = [{"ts": "2026-11-11T20:00:00Z", "who": "of_x", "text": "q", "answer": "approve"}]
        box = P.Panel(self.scr([0.05], (16, 150)), lambda: ([("Waiting on you", data)], {}, recent),
                      initial=(list(data), {}), keys="Ctrl-B, then a")
        box.run()
        frame = self.frames[-1]
        right = {y: t for (y, x), t in frame.items() if x > 80}
        self.assertIn("Waiting on you", right.values())
        self.assertTrue(any("of_a" in t and "×2" in t for t in right.values()), right)
        self.assertIn("Answered lately", right.values())
        self.assertTrue(any("of_x: approve" in t for t in right.values()))


def curses_right():
    import curses
    return curses.KEY_RIGHT


if __name__ == "__main__":
    unittest.main()
