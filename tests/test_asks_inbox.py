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


class LogicTest(unittest.TestCase):
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
        return Scr()

    def text(self, frame):
        return "\n".join(t for _, t in sorted(frame.items()))

    def inbox(self, keys, data, cards=None, size=(40, 140), loader=None, seq=None):
        cards = cards or {}

        def card_of(it):
            c = dict(cards.get(it["ids"][0]) or {"options": ["yes", "no"]})
            return dict({"question": it["text"], "who": it["who"], "answerable": True, "ids": it["ids"]}, **c)

        def answer(it, text):
            self.sent.append((it["ids"][0], text))
            return True, "recorded"
        box = I.Inbox(self.scr(keys, size), loader or (lambda: ([("Waiting on you", data)], {})), card_of, answer,
                      lambda what, it: (self.sent.append((it["ids"][0], what)) or (True, what)),
                      positions=lambda: [{"name": "of_b"}, {"name": "of_conductor"}],
                      record=lambda kind, payload: self.records.append((kind, payload)) or "E1", seq=seq,
                      initial=(list(data), {}))
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
        self.assertIn("↑↓ select · 1-9 answer", frame)
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
        self.inbox([0.05, curses_right(), 0.05], data, size=(30, 80))
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
        return next((y for (y, x), t in frame.items() if x == 2 and t.startswith(text)), None)

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
        self.assertTrue("Tab" in keys and "?" in keys and "Esc" in keys, keys)

    def test_help(self):
        self.inbox([0.05, ord("?"), 0.05], [item("A", "First plain question?")])
        self.assertTrue(any("select; wraps from the first card to the last" in self.text(f) for f in self.frames))


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
        self.mice = [(0, 3, 3, 0, curses.BUTTON1_PRESSED)]
        box, ran = self.panel([0.05, curses.KEY_MOUSE, 0.05], data, open_cmd=["herdr-open", "asks"])
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
        box = P.Panel(self.scr([0.05, 10, 0.05, ord("o"), 0.05]), lambda: ([("Waiting on you", [])], {}),
                      initial=([], {}), keys="Ctrl-B, then a", popup=popup)
        box.run()
        self.assertEqual(len(calls), 2)
        text = "\n".join(self.text(f) for f in self.frames)
        self.assertIn("opening the questions…", text)
        self.assertIn("herdr shows another popup, Settings or Copy mode: close it, then try again", text)

    def test_without_an_opener_the_button_says_the_keys(self):
        box, ran = self.panel([0.05, 10, 0.05], [item("A", "First plain question?")])
        self.assertEqual(ran, [])
        self.assertTrue(any("Press Ctrl-B, then a to open them." in self.text(f) for f in self.frames))

    def test_a_narrow_pane_puts_a_stream_on_each_line(self):
        data = [item("A", "First plain question?"), item("C", "Pay 90 USD to the operator?")]
        self.panel([0.05], data, size=(10, 30))
        rows = [t for (y, x), t in sorted(self.frames[-1].items())]
        self.assertIn("● Holds work 0", rows)
        self.assertIn("● Everything else 1", rows)
        self.assertIn("or Ctrl-B, then a", rows)  # under the button when it does not fit beside it


def curses_right():
    import curses
    return curses.KEY_RIGHT


if __name__ == "__main__":
    unittest.main()
