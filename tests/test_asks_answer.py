"""Answering an ask from the popup (of_asks_answer): the card and the recorded answer."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_asks  # noqa: E402
import of_asks_answer  # noqa: E402
import of_events  # noqa: E402
import of_store  # noqa: E402


REAL_OWNER_CHECK = of_asks_answer.not_the_owner


class AnswerTest(unittest.TestCase):
    SCREEN_OWNER = True  # these record as the owner's own screen does (of_asks_answer.not_the_owner holds)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = self.tmp.name
        of_events._CONN.clear()
        self.conn = of_store.connect(os.path.join(self.tmp.name, "of.db"))
        if self.SCREEN_OWNER:
            owner = mock.patch.object(of_asks_answer, "not_the_owner", lambda: None)  # the owner's own screen
            owner.start()
            self.addCleanup(owner.stop)

    def tearDown(self):
        of_events._CONN.clear()
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def add(self, kind, payload, ts, session="S1", position="of_a"):
        rec = of_events.record(kind, payload, position=position, session=session, org="ofb")
        rec["timestamp"] = ts
        of_store.add_event(self.conn, rec)
        return rec["of.event.id"]

    def test_card_and_answer_close_the_group(self):
        self.add("turn_end", {"last_message": "\n".join(f"line {i}" for i in range(30)) +
                              "\nSee acme-inc/storefront#2240 and docs/handover.md"}, "2026-11-01T10:00:00Z")
        q = {"detected_by": "declared", "questions": [{"question": "Lift the hold on acme?",
                                                       "options": ["yes, lift", "no, keep"]}]}
        a1 = self.add("agent_question", q, "2026-11-01T10:00:05Z")
        a2 = self.add("agent_question", q, "2026-11-01T10:01:00Z", session="S2", position="of_b")
        groups = of_asks.group(of_asks.project(of_asks.load(self.conn)))
        self.assertEqual(len(groups), 1)
        item = {"who": groups[0]["who"], "text": groups[0]["text"], "since": groups[0]["since"],
                "ids": groups[0]["ask_ids"], "kind": groups[0]["kind"], "first": groups[0]["first"]}
        positions = [{"name": "of_a", "system": "organic-framework", "_org": "ofb"}]
        c = of_asks_answer.card(self.conn, item, positions)
        self.assertEqual(c["options"], ["yes, lift", "no, keep"])
        self.assertEqual((c["position"], c["system"]), ("of_a", "organic-framework"))
        self.assertEqual(len(c["context"].splitlines()), 31)  # the whole message, never cut
        self.assertIn("docs/handover.md", c["links"])
        self.assertIn("acme-inc/storefront#2240", c["links"])
        self.assertTrue(c["answerable"])
        ok, out = of_asks_answer.answer(self.conn, item, "yes, lift", positions, lambda name: None)
        self.assertTrue(ok)
        self.assertIn("no live pane", out)
        self.assertEqual(of_asks_answer.unsent_drafts(), [])  # sent: the draft is gone
        asks = of_asks.project(of_asks.load(self.conn))
        self.assertEqual({a["event_id"]: a["state"] for a in asks}, {a1: "answered", a2: "answered"})
        row = self.conn.execute("SELECT payload FROM event WHERE name = 'of.agent_question_answered'").fetchone()
        p = json.loads(row[0])
        self.assertEqual((p["answer"], p["via"], p["ask_ids"]), ("yes, lift", "asks-ui", [a1, a2]))
        self.assertTrue(of_asks_answer.frame(a1, "x").startswith("Answer from the founder to your question "))

    def test_send_from_another_thread(self):
        import threading
        import of_asks_tui
        q = {"detected_by": "declared", "questions": [{"question": "Rotate the key now?"}]}
        a1 = self.add("agent_question", q, "2026-11-01T10:00:05Z")
        self.conn.execute("SELECT 1")  # the main thread holds its own connection, as the popup does
        of_events.emit("pulse", {})     # and the cached event connection
        item = {"who": ["of_a"], "text": "Rotate the key now?", "since": None, "ids": [a1],
                "kind": "question (declared)", "first": None}
        result = {}
        t = threading.Thread(target=lambda: result.update(
            out=of_asks_tui.threaded_answer([{"name": "of_a"}])(item, "yes, now")))
        tty = mock.Mock()
        tty.isatty.return_value = True
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("CLAUDE_CODE_SESSION_ID", "OF_POSITION", "CLAUDECODE")}
        with mock.patch.dict(os.environ, clean, clear=True), mock.patch.object(sys, "stdin", tty):  # the owner's screen
            t.start()
            t.join()
        self.assertEqual(result["out"][0], True, result)
        states = {a["event_id"]: a["state"] for a in of_asks.project(of_asks.load(self.conn))}
        self.assertEqual(states[a1], "answered")

    def test_failed_send_keeps_the_draft(self):
        import of_asks_answer
        item = {"who": ["x"], "text": "t", "since": None, "ids": [], "kind": "registry", "first": None}
        ok, why = of_asks_answer.answer(self.conn, dict(item, ids=["Z9"]), "my words", [], lambda n: None)
        self.assertFalse(ok)
        self.assertEqual([d["text"] for d in of_asks_answer.unsent_drafts()], ["my words"])

    def test_registry_and_permission_items_are_not_answerable(self):
        for kind in ("registry", "permission"):
            c = of_asks_answer.card(self.conn, {"who": ["x"], "text": "t", "since": None, "ids": [], "kind": kind}, [])
            self.assertFalse(c["answerable"])


if __name__ == "__main__":
    unittest.main()


class CardUiTest(unittest.TestCase):
    def test_card_lines_keep_everything(self):
        import of_asks_tui
        long_q = "What is the smallest part size for the upload to the store? " * 6
        ctx = "\n".join(f"Paragraph {i}: " + "word " * 30 for i in range(20)) + "\nThis is the whole last sentence."
        c = {"who": ["data_ledger"], "position": "data_ledger", "system": "data", "since": None,
             "kind": "question (heuristic)", "question": long_q, "options": ["a", "b"], "context": ctx,
             "links": [], "answerable": True}
        lines = of_asks_tui.card_lines(c, 60)
        text = " ".join(ln for ln, _ in lines)
        self.assertIn("What is the smallest part size", text)
        self.assertIn("This is the whole last sentence.", text)
        self.assertTrue(all(len(ln) <= 60 for ln, _ in lines))

    def test_last_paragraph_is_whole(self):
        import of_asks_answer
        msg = "Did X.\n\nQuestion: which part size to take, 5 MB or 64 MB?\n\nNext: Y."
        self.assertEqual(of_asks_answer.last_paragraph(msg), "Question: which part size to take, 5 MB or 64 MB?")

    def test_keys_after_send_do_not_leak(self):
        import curses
        import of_asks_tui
        saved = {k: getattr(curses, k) for k in ("curs_set", "use_default_colors", "init_pair", "color_pair", "flushinp")}
        flushed = []
        curses.curs_set = curses.use_default_colors = lambda *a: None
        curses.init_pair = lambda *a: None
        curses.color_pair = lambda n: 0
        try:
            class Scr:
                def __init__(self, keys):
                    self.keys = list(keys)
                def getmaxyx(self):
                    return (40, 100)
                def erase(self):
                    pass
                def refresh(self):
                    pass
                def addnstr(self, *a):
                    pass
                def timeout(self, t):
                    pass
                def getch(self):
                    return self.keys.pop(0) if self.keys else ord("q")
            # Enter opens the card, 1 picks, Enter sends; the user mashes 2+Enter while it sends;
            # those are flushed; then Esc goes to the list and q quits.
            scr = Scr([10, ord("1"), 10, ord("2"), 10, 27, ord("q")])
            curses.flushinp = lambda: (flushed.append(1), scr.keys.clear() if scr.keys[:2] == [ord("2"), 10] else None)
            sent = []
            items = [("Waiting on you", [{"who": ["a"], "text": "q?", "since": None, "ids": ["A1"], "kind": "question (declared)", "first": None},
                                         {"who": ["b"], "text": "q2?", "since": None, "ids": ["B1"], "kind": "question (declared)", "first": None}]),
                     ("Registry-declared (no notifications)", [])]
            orig = of_asks_tui.load
            of_asks_tui.load = lambda positions, conn: items
            card = lambda it: {"who": it["who"], "position": it["who"][0], "system": None, "since": None, "kind": it["kind"],
                               "question": it["text"], "options": ["yes", "no"], "context": "", "links": [], "answerable": True}

            def answer(it, text):
                sent.append((it["ids"][0], text))
                return True, "delivered into the pane"
            try:
                of_asks_tui.run(scr, [], None, lambda *a: "", card, answer)
            finally:
                of_asks_tui.load = orig
            import time
            time.sleep(0.05)
            self.assertEqual(sent, [("A1", "yes")])  # one answer, to the first question only
            self.assertTrue(flushed)
        finally:
            for k, v in saved.items():
                setattr(curses, k, v)


class PopupUxTest(unittest.TestCase):
    """The founder's popup (2026-11-06): optimistic answers with undo, refresh by signal, urgency order."""

    def setUp(self):
        import curses
        import of_asks_tui
        self.saved = {k: getattr(curses, k) for k in ("curs_set", "use_default_colors", "init_pair", "color_pair",
                                                      "flushinp")}
        curses.curs_set = curses.use_default_colors = curses.flushinp = lambda *a: None
        curses.init_pair = lambda *a: None
        curses.color_pair = lambda n: 0
        self.undo, of_asks_tui.UNDO_S = of_asks_tui.UNDO_S, 0.2
        self.lines = []

    def tearDown(self):
        import curses
        import of_asks_tui
        for k, v in self.saved.items():
            setattr(curses, k, v)
        of_asks_tui.UNDO_S = self.undo

    def scr(self, keys):
        test = self

        class Scr:
            def __init__(self):
                self.keys = list(keys)
                self.shown = []

            def getmaxyx(self):
                return (40, 120)

            def erase(self):
                self.shown = []

            def refresh(self):
                test.lines.append(list(self.shown))

            def addnstr(self, y, x, text, n, *a):
                self.shown.append(text)

            def timeout(self, t):
                pass

            def getch(self):
                k = self.keys.pop(0) if self.keys else ord("q")
                if isinstance(k, float):  # a pause: time passes, no key
                    import time as _t
                    _t.sleep(k)
                    return -1
                return k
        return Scr()

    def data(self):
        return [("Waiting on you", [{"who": ["a"], "text": "q1?", "since": None, "ids": ["A1"],
                                     "kind": "question (declared)", "first": None, "section": "normal"},
                                    {"who": ["b"], "text": "q2?", "since": None, "ids": ["B1"],
                                     "kind": "question (declared)", "first": None, "section": "normal"}]),
                ("Registry-declared (no notifications)", [])]

    def test_s_opens_studio_for_the_selected_ask_and_reports(self):
        """of_architect: the founder lives in the popup; s brings Studio's Works (or the ask's work card) to him."""
        import of_asks_tui
        opened = []

        def studio(item, done):
            opened.append(item["ids"][0])
            done("Studio opened in the browser (Works)")
        orig = of_asks_tui.load
        of_asks_tui.load = lambda positions, conn: self.data()
        try:
            of_asks_tui.run(self.scr([ord("j"), ord("s"), 0.05, -1, ord("q")]), [], None,
                            lambda what, x: (True, "ok"), None, None, studio=studio)
        finally:
            of_asks_tui.load = orig
        self.assertEqual(opened, ["B1"])
        self.assertTrue(any("s Studio: works" in t for frame in self.lines for t in frame))
        self.assertTrue(any("Studio opened in the browser" in t for frame in self.lines for t in frame))

    def test_studio_target_work_card_by_work_or_tag(self):
        import sqlite3
        import of_asks_tui
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE event (event_id TEXT, org TEXT, payload TEXT)")
        c.executemany("INSERT INTO event VALUES (?,?,?)", [
            ("A", "acme", json.dumps({"work": "acme-talos"})),
            ("B", "acme", json.dumps({"tags": [{"tag": "wallets"}]})),
            ("C", "acme", json.dumps({"tags": ["money"]}))])
        works = lambda org: {"acme-checkout": {"match": {"tags": ["wallets"]}}, "acme-x": {"match": {}}}
        self.assertEqual(of_asks_tui.studio_target(c, "A", works), ("acme", "/studio/works?org=acme&w=acme-talos"))
        self.assertEqual(of_asks_tui.studio_target(c, "B", works)[1], "/studio/works?org=acme&w=acme-checkout")
        self.assertEqual(of_asks_tui.studio_target(c, "C", works)[1], "/studio/works?org=acme")
        self.assertEqual(of_asks_tui.studio_target(c, None, works), (None, "/studio/works"))

        class Client:
            def _authed(self, method, path, body=None):
                self.call = (method, path, body)
                return 200, {"url": "http://x/studio/open?t=T"}
        import of_client
        self.assertIsNone(of_client.studio_requester({}))   # the founder's terminal or the popup
        self.assertEqual(of_client.studio_requester({"OF_POSITION": "of_platform"}), "agent:of_platform")
        self.assertEqual(of_client.studio_requester({"CLAUDE_CODE_SESSION_ID": "S"}, lambda s: "of_lab"), "agent:of_lab")
        self.assertEqual(of_client.studio_requester({"CLAUDE_CODE_SESSION_ID": "S"}), "agent:unbound")
        cl, urls = Client(), []
        saved = {k: os.environ.pop(k, None) for k in ("OF_POSITION", "CLAUDE_CODE_SESSION_ID")}
        try:
            line = of_asks_tui.open_studio("acme", "/studio/works?org=acme&w=acme-talos", lambda o: cl, urls.append)
        finally:
            os.environ.update({k: v for k, v in saved.items() if v is not None})
        self.assertEqual(cl.call, ("POST", "/v1/studio/link", {"next": "/studio/works?org=acme&w=acme-talos"}))
        self.assertEqual((urls, line.endswith("the work's card)")), (["http://x/studio/open?t=T"], True))

    def test_a_draft_whose_ask_closed_goes_to_the_archive(self):
        """of_architect, 2026-11-07: '1 unsent answer kept as drafts' for 2035b5c5, answered in the chat at 12:01Z."""
        import tempfile
        import of_asks_answer
        import of_events
        with tempfile.TemporaryDirectory() as d:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = d
            try:
                of_asks_answer.save_draft({"ids": ["A-open"], "who": ["a"]}, "yes")
                of_asks_answer.save_draft({"ids": ["A-closed"], "who": ["b"]}, "no")
                self.assertEqual(of_asks_answer.archive_drafts({"A-open"}), ["A-closed"])
                self.assertEqual([x["ask_id"] for x in of_asks_answer.unsent_drafts()], ["A-open"])
                self.assertEqual(len(os.listdir(os.path.join(of_asks_answer.drafts_dir(), "archive"))), 1)
            finally:
                of_events.STATE_DIR = saved

    def run_popup(self, keys, act=None, answer=None, seq=None, loads=None):
        import of_asks_tui
        sent = []
        d = self.data()
        orig = of_asks_tui.load

        def load(positions, conn):
            if loads is not None:
                loads.append(1)
            return d
        of_asks_tui.load = load
        try:
            of_asks_tui.run(self.scr(keys), [], None, act or (lambda what, x: (sent.append((what, x["ids"][0])), (True, "ok"))[1]),
                            None, answer, seq=seq)
        finally:
            of_asks_tui.load = orig
        return sent

    def test_d_shows_a_draft_and_sends_it_only_on_a_second_d(self):
        """A draft is a file: d shows its words, to whom and why it waits, and only d again or y sends it (of_review's
        trust notes on PR 398)."""
        import of_asks_tui
        import time as _t
        draft = {"ask_id": "A1", "ask_ids": ["A1"], "who": ["of_a"], "text": "yes, tonight", "saved_at": _t.time(),
                 "why": "store busy", "item": {"ids": ["A1"], "who": ["of_a"], "text": "q1?", "version": 1}}
        for keys, want in (([ord("d"), ord("x"), 0.3, ord("q")], []),
                           ([ord("d"), ord("d"), 0.3, ord("q")], ["yes, tonight"])):
            got, self.lines = [], []
            orig = of_asks_tui.load
            of_asks_tui.load = lambda positions, conn: self.data()
            try:
                of_asks_tui.run(self.scr(keys), [], None, lambda what, x: (True, "ok"), None,
                                lambda it, text: (got.append(text), (True, "ok"))[1], drafts=lambda: [draft])
            finally:
                of_asks_tui.load = orig
            self.assertEqual(got, want)
            self.assertTrue(any("«yes, tonight»" in t and "store busy" in t for f in self.lines for t in f))

    def test_answer_leaves_at_once_and_u_takes_it_back(self):
        sent = self.run_popup([ord("a"), ord("u"), 0.3, ord("q")])
        self.assertEqual(sent, [])  # undone inside the window: never sent
        self.assertTrue(any("sent ✓ · u to undo" in " ".join(f) for f in self.lines))
        self.assertTrue(any("q1?" in " ".join(f) for f in self.lines[-2:]))  # back in the list

    def test_sent_after_the_window_and_q_never_drops(self):
        sent = self.run_popup([ord("a"), 0.3, -1, ord("q")])
        self.assertEqual(sent, [("answered", "A1")])
        sent = self.run_popup([ord("a"), ord("q")])  # q inside the window: sent, not dropped
        self.assertEqual(sent, [("answered", "A1")])
        hidden_frame = next(f for f in self.lines if any("sent ✓" in t for t in f))
        self.assertFalse(any("q1?" in t for t in hidden_frame))  # gone from the list at once

    def test_q_leaves_at_once_and_hands_what_is_pending_over(self):
        """of_architect, URGENT 2026-11-06: q with three answers pending returns in under 200 ms and hands all three
        to `leave`, which records them in the screen's process (LeaveTest)."""
        import tempfile as _tf
        import time as _t
        import of_asks_tui
        import of_events
        saved_dir = of_events.STATE_DIR
        with _tf.TemporaryDirectory() as d:
            of_events.STATE_DIR = d
            try:
                data = [("Waiting on you", [{"who": [w], "text": f"{w}?", "since": None, "ids": [w.upper()],
                                             "kind": "question (declared)", "first": None, "section": "normal"}
                                            for w in ("a", "b", "c")]), ("Registry-declared (no notifications)", [])]
                handed = []
                slow = lambda what, x: (_t.sleep(3), (True, "ok"))[1]  # a slow send must not hold q
                orig = of_asks_tui.load
                of_asks_tui.load = lambda positions, conn: data
                try:
                    t0 = _t.perf_counter()
                    of_asks_tui.run(self.scr([ord("a"), ord("a"), ord("a"), ord("q")]), [], None, slow, None, None,
                                    handoff=handed.extend)
                    took = _t.perf_counter() - t0
                finally:
                    of_asks_tui.load = orig
                self.assertLess(took, 0.2)
                self.assertEqual(sorted(p["item"]["ids"][0] for p in handed), ["A", "B", "C"])
                self.assertTrue(all(p["done"] for p in handed))  # recorded once: a second flush hands nothing over
            finally:
                of_events.STATE_DIR = saved_dir

    def test_a_failed_send_comes_back_red(self):
        sent = self.run_popup([ord("a"), 0.3, -1, -1, ord("q")], act=lambda what, x: (False, "store busy"))
        self.assertEqual(sent, [])
        self.assertTrue(any("q1?" in t and "✗ store busy" in t for f in self.lines[-2:] for t in f))

    def test_a_seq_change_reloads(self):
        loads, ticks = [], iter([1, 1, 2, 2, 2, 2])
        self.run_popup([-1, -1, -1, ord("q")], seq=lambda: next(ticks, 2), loads=loads)
        self.assertEqual(len(loads), 2)  # the first load, then one for the seq change

    def test_a_task_sits_on_top_and_a_asks_done_first(self):
        import of_asks_tui
        data = [("Waiting on you", of_asks_tui.order([
            {"who": ["a"], "text": "q1?", "since": None, "ids": ["A1"], "kind": "question (declared)", "first": None},
            {"who": ["ci_keeper"], "text": "Generate a new GitHub App key", "since": None, "ids": ["T1"], "kind": "task",
             "first": None, "task": True, "steps": "1. …", "command": None}])), ("Registry-declared (no notifications)", [])]
        self.assertEqual(data[0][1][0]["ids"], ["T1"])
        sent = []
        orig = of_asks_tui.load
        of_asks_tui.load = lambda positions, conn: data
        try:
            of_asks_tui.run(self.scr([ord("a"), ord("n"), ord("a"), ord("y"), 0.3, -1, ord("q")]), [], None,
                            lambda what, x: (sent.append((what, x["ids"][0])), (True, "ok"))[1], None, None)
        finally:
            of_asks_tui.load = orig
        self.assertTrue(any("For you to do" in t for f in self.lines for t in f))
        self.assertTrue(any("Done?" in t for f in self.lines for t in f))
        self.assertEqual(sent, [("answered", "T1")])  # n declined; y claimed once

    def test_the_answer_input_keeps_its_own_layout(self):
        """The founder's screenshot: a task's steps ran over the input. Fixed rows, nothing from the card."""
        import of_asks_tui
        steps = "Steps:\n" + "\n".join(f"{i}. Move 90 USD from the main account to the operator's account, check it" for i in range(12))
        title = "Answer to storefront_checkout: Move 90 USD to the operator's account\n\n" + steps
        typed = list("Moved it, the transaction is confirmed in the block explorer, the hash is in the channel ") * 2

        class Scr:
            def __init__(self):
                self.keys = typed + ["\x04"]
                self.rows = {}

            def getmaxyx(self):
                return (20, 60)

            def clear(self):
                self.rows = {}

            def erase(self):
                self.rows = {}

            def addnstr(self, y, x, text, n, *a):
                assert "\n" not in text, text
                assert x + of_asks_tui.dwidth(text[:n]) <= 60, (y, x, text)
                self.rows[y] = text[:n]

            def move(self, y, x):
                assert 0 <= y < 20 and 0 <= x < 60

            def refresh(self):
                pass

            def timeout(self, t):
                pass

            def get_wch(self):
                return self.keys.pop(0)
        scr = Scr()
        got = of_asks_tui.edit(scr, title)
        self.assertEqual(got, "".join(typed).strip())
        self.assertTrue(scr.rows[0].startswith("Answer to storefront_checkout: Move 90 USD"))
        self.assertNotIn(2, scr.rows)  # the empty row between the keys and the input
        self.assertFalse(any("Steps" in t for t in scr.rows.values()))
        self.assertTrue(all(y >= 3 for y in scr.rows if y not in (0, 1)))

    def test_copy_targets(self):
        import of_asks_tui
        task = {"task": True, "command": "gh api -X POST /apps/x/keys", "steps": "1. …"}
        self.assertEqual(of_asks_tui.copy_target({}, task), "gh api -X POST /apps/x/keys")
        c = {"options": ["no, move it", "yes, run it (recommended: the window is short)"], "question": "q"}
        self.assertEqual(of_asks_tui.copy_target(c, {}), "yes, run it (recommended: the window is short)")
        sent = []
        orig = of_asks_tui.subprocess.run
        of_asks_tui.subprocess.run = lambda argv, **k: sent.append((argv, k["input"]))
        saved = os.environ.pop("SSH_TTY", None)
        try:
            self.assertEqual(of_asks_tui.copy_text("text"), "pbcopy")
        finally:
            of_asks_tui.subprocess.run = orig
            if saved is not None:
                os.environ["SSH_TTY"] = saved
        self.assertEqual(sent, [(["pbcopy"], "text".encode())])

    def test_order(self):
        import of_asks_tui
        now = 1_800_000_000
        iso = lambda t: __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime(t))
        xs = [{"ids": ["old"], "kind": "question (declared)", "since": iso(now - 900)},
              {"ids": ["late"], "kind": "question (declared)", "since": iso(now - 60), "due": iso(now + 5 * 3600)},
              {"ids": ["soon"], "kind": "question (declared)", "since": iso(now - 30), "due": iso(now + 600)},
              {"ids": ["block"], "kind": "question (declared) blocking", "since": iso(now - 120)},
              {"ids": ["free"], "kind": "question (heuristic)", "since": iso(now - 9000), "n_options": 0}]
        got = of_asks_tui.order(xs, now=now)
        self.assertEqual([x["ids"][0] for x in got], ["block", "soon", "old", "late", "free"])
        self.assertEqual([x["section"] for x in got], ["urgent", "urgent", "normal", "normal", "panes"])
        self.assertEqual(of_asks_tui.countdown(iso(now + 38 * 60 + 5), now=now), "in 38 min")
        self.assertEqual(of_asks_tui.countdown(iso(now - 12 * 60), now=now), "overdue 12 min")


class SideChatTest(unittest.TestCase):
    def test_argv_fork_and_estimate(self):
        import of_side_chat
        a = of_side_chat.argv("S1", "why?", model="claude-opus-5-5")
        self.assertIn("--fork-session", a)
        self.assertEqual(a[a.index("--resume") + 1], "S1")
        self.assertIn("plan", a)
        self.assertIn("Bash", a)
        b = of_side_chat.argv("S1", "and?", fork_id="F1")
        self.assertNotIn("--fork-session", b)
        self.assertEqual(b[b.index("--resume") + 1], "F1")
        est = of_side_chat.estimate_usd("claude-opus-5-5", {"cache_creation_input_tokens": 536993, "output_tokens": 529})
        self.assertAlmostEqual(est, 2.6955, places=3)
        self.assertIsNone(of_side_chat.estimate_usd("unknown", {}))
        env = of_side_chat.env_for("/x/.claude-e", "A1")
        self.assertEqual((env["CLAUDE_CONFIG_DIR"], env["OF_SIDE_CHAT"], env["SSH_AUTH_SOCK"]), ("/x/.claude-e", "A1", ""))
        self.assertFalse(any(k.startswith("HERDR_") for k in env))


class NotAQuestionTest(AnswerTest):
    def test_n_closes_and_labels(self):
        import of_asks_answer
        q = {"detected_by": "heuristic", "classifications": {"q-v2": True}, "questions": [{"question": "Shall I check?"}]}
        a1 = self.add("agent_question", q, "2026-11-01T10:00:05Z")
        item = {"who": ["of_a"], "text": "Shall I check?", "ids": [a1], "kind": "question (heuristic)"}
        self.assertIn("not a question", of_asks_answer.not_a_question(item))
        states = {a["event_id"]: a["state"] for a in of_asks.project(of_asks.load(self.conn))}
        self.assertEqual(states[a1], "not_a_question")
        self.assertEqual(self.conn.execute("SELECT count(*) FROM event WHERE name = 'of.question_labelled'").fetchone()[0], 1)


class RedeliverTest(AnswerTest):
    """2026-11-08: answers recorded but never typed (a waiter gave up, a herdr error, a group's second asker).
    The answer event is the record; the asker's of.human_prompt carrying the frame is the delivery's record."""

    def now(self, iso):
        import calendar
        return calendar.timegm(__import__("time").strptime(iso, "%Y-%m-%dT%H:%M:%SZ"))

    def group(self):
        q = {"detected_by": "declared", "questions": [{"question": "Window on Monday 15:00?"}]}
        a1 = self.add("agent_question", q, "2026-11-01T10:00:05Z")
        a2 = self.add("agent_question", q, "2026-11-01T10:01:00Z", session="S2", position="of_b")
        g = of_asks.group(of_asks.project(of_asks.load(self.conn)))[0]
        item = {"who": g["who"], "text": g["text"], "since": g["since"], "ids": g["ask_ids"], "kind": g["kind"],
                "first": g["first"]}
        return a1, a2, item

    def positions(self):
        return [{"name": "of_a", "_org": "ofb"}, {"name": "of_b", "_org": "ofb"},
                {"name": "of_owner", "_org": "ofb"}, {"name": "of_unit", "_org": "ofb", "_unit": "money"}]

    def test_a_group_answer_reaches_every_asker(self):
        a1, a2, item = self.group()
        typed = []
        saved = of_asks_answer.deliver
        of_asks_answer.deliver = lambda pane, text, session, **k: typed.append((pane, text)) or "delivered into the pane"
        try:
            ok, out = of_asks_answer.answer(self.conn, item, "yes", self.positions(), lambda name: "pane-" + name)
        finally:
            of_asks_answer.deliver = saved
        self.assertTrue(ok)
        self.assertEqual(typed, [("pane-of_a", of_asks_answer.frame(a1, "yes")),
                                 ("pane-of_b", of_asks_answer.frame(a2, "yes"))])
        self.assertIn("of_b: delivered", out)

    def test_a_recorded_answer_is_ok_even_when_typing_fails(self):
        _, _, item = self.group()
        import of_relay_client
        saved = of_relay_client.herdr_deliver
        of_relay_client.herdr_deliver = lambda *a, **k: (False, "herdr agent wait failed (rc 1)")
        try:
            ok, out = of_asks_answer.answer(self.conn, item, "yes", self.positions(), lambda name: "p")
        finally:
            of_relay_client.herdr_deliver = saved
        self.assertTrue(ok)  # recorded: the popup must not bring it back red to be answered twice
        self.assertIn("not typed now", out)
        self.assertEqual(of_asks_answer.unsent_drafts(), [])

    def answered(self, a1, a2, ts="2026-11-01T11:00:00Z", **extra):
        self.add("agent_question_answered", {"ask_id": a1, "ask_ids": [a1, a2], "answer": "yes", "via": "asks-ui",
                                             "by": "owner", **extra}, ts, session=None, position=None)

    def test_pending_lists_each_asker_until_its_prompt_is_recorded(self):
        a1, a2, _ = self.group()
        self.answered(a1, a2)
        now = self.now("2026-11-01T11:30:00Z")
        got = of_asks_answer.pending(self.conn, [], self.positions(), now)
        self.assertEqual([d["position"] for d in got], ["of_a", "of_b"])
        self.assertTrue(got[0]["text"].startswith(of_asks_answer.frame(a1, "yes")))
        self.assertIn("at 11:00Z", got[0]["text"])  # 30 min after the answer: it says when he answered
        self.add("human_prompt", {"origin": "human", "channel": "asks-ui", "text": of_asks_answer.frame(a1, "yes")},
                 "2026-11-01T11:00:02Z")
        self.assertEqual([d["position"] for d in of_asks_answer.pending(self.conn, [], self.positions(), now)],
                         ["of_b"])
        # younger than REDELIVER_MIN_AGE_S: the first attempt comes first; older than REDELIVER_MAX_AGE_H: dropped
        self.assertEqual(of_asks_answer.pending(self.conn, [], self.positions(), self.now("2026-11-01T11:00:30Z")), [])
        self.assertEqual(of_asks_answer.pending(self.conn, [], self.positions(), self.now("2026-11-02T11:00:01Z")), [])

    def test_late_wording_says_why(self):
        """of_platform, 2026-11-10: answers that waited for the end of its long turn, by design (4c70a3f), came
        marked as a failed first attempt."""
        a1, a2, _ = self.group()
        self.answered(a1, a2)
        self.add("turn_end", {"last_message": "x"}, "2026-11-01T11:10:00Z", session="S2", position="of_b")
        got = {d["position"]: d["text"] for d in
               of_asks_answer.pending(self.conn, [], self.positions(), self.now("2026-11-01T11:30:00Z"))}
        self.assertIn("you were busy, so it waited for the end of your turn", got["of_a"])  # no turn end since
        self.assertIn("the first attempt did not reach your pane", got["of_b"])  # idle at 11:10, and it did not go in

    def test_the_same_answer_recorded_twice_is_typed_once(self):
        """of_platform, 2026-11-10 (ask 8ee7f1bc): the popup closed while its thread was sending, the outbox sender
        recorded the answer again 0.85 s later, and the joined prompt typed it twice."""
        a1, a2, item = self.group()
        self.answered(a1, a2, ts="2026-11-01T11:00:00Z")
        self.answered(a1, a2, ts="2026-11-01T11:00:01Z")
        got = of_asks_answer.pending(self.conn, [], self.positions(), self.now("2026-11-01T11:30:00Z"))
        self.assertEqual([d["position"] for d in got], ["of_a", "of_b"])
        self.assertTrue(of_asks_answer.recorded_already(self.conn, [a1, a2], "yes", now=self.now("2026-11-01T11:05:00Z")))
        self.assertFalse(of_asks_answer.recorded_already(self.conn, [a1, a2], "no", now=self.now("2026-11-01T11:05:00Z")))
        self.assertFalse(of_asks_answer.recorded_already(self.conn, [a1], "yes", now=self.now("2026-11-01T11:05:00Z")))
        self.assertFalse(of_asks_answer.recorded_already(self.conn, [a1, a2], "yes", now=self.now("2026-11-01T11:20:00Z")))

    def test_answering_twice_records_and_types_once(self):
        _, _, item = self.group()
        typed = []
        saved = of_asks_answer.deliver
        of_asks_answer.deliver = lambda pane, text, session, **k: typed.append(pane) or "delivered into the pane"
        try:
            first = of_asks_answer.answer(self.conn, item, "yes", self.positions(), lambda name: "pane-" + name)
            again = of_asks_answer.answer(self.conn, item, "yes", self.positions(), lambda name: "pane-" + name)
        finally:
            of_asks_answer.deliver = saved
        self.assertTrue(first[0] and again[0])
        self.assertIn("recorded already", again[1])
        self.assertEqual(typed, ["pane-of_a", "pane-of_b"])
        n = self.conn.execute("SELECT count(*) FROM event WHERE name = 'of.agent_question_answered'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_dialog_keys_and_verdicts_are_not_typed(self):
        a1, a2, _ = self.group()
        self.answered(a1, a2, keys=["1"])
        self.add("agent_question_answered", {"ask_id": a1, "ask_ids": [a1], "via": "asks-ui",
                                             "verdict": "not_a_question", "by": "owner"}, "2026-11-01T11:00:00Z",
                 session=None, position=None)
        self.assertEqual(of_asks_answer.pending(self.conn, [], self.positions(), self.now("2026-11-01T11:30:00Z")), [])

    def test_a_routed_question_is_pending_while_open_and_unrecorded(self):
        q = {"detected_by": "declared", "to": "of_owner", "blocking": True,
             "questions": [{"question": "Restart the bot at 15:00?", "options": ["yes", "no"]}]}
        aid = self.add("agent_question", q, "2026-11-01T10:00:00Z", session="SU", position="of_unit")
        asks = of_asks.project(of_asks.load(self.conn))
        now = self.now("2026-11-01T10:20:00Z")
        got = of_asks_answer.pending(self.conn, asks, self.positions(), now)
        self.assertEqual([(d["position"], d["sender"], d["channel"]) for d in got], [("of_owner", "of_unit", None)])
        self.assertTrue(got[0]["text"].startswith(of_asks_answer.routed_frame(
            "of_unit", "money", aid, "Restart the bot at 15:00?", ["yes", "no"], True)))
        self.add("human_prompt", {"origin": "injected", "text": "<pasted_content id=\"x\">\n" +
                                  of_asks_answer.routed_frame("of_unit", "money", aid, "Restart?", [], True)},
                 "2026-11-01T10:00:03Z", session="SO", position="of_owner")
        self.assertEqual(of_asks_answer.pending(self.conn, asks, self.positions(), now), [])
        closed = [dict(a, state="answered") for a in asks]
        self.conn.execute("DELETE FROM event WHERE name = 'of.human_prompt'")
        self.assertEqual(of_asks_answer.pending(self.conn, closed, self.positions(), now), [])

    def test_a_routed_question_with_its_organizations_prefix_is_delivered(self):
        """A user organization's structure keeper, 2026-11-15: `of ask --to <org>:<unit owner>` was stored verbatim, and
        none of five such asks was typed in; three escalated to the founder unseen by the unit owner."""
        q = {"detected_by": "declared", "to": "ofb:of_owner", "blocking": False,
             "questions": [{"question": "Pause the follower tonight?", "options": ["yes", "no"]}]}
        self.add("agent_question", q, "2026-11-01T10:00:00Z", session="SU", position="of_unit")
        other = dict(q, to="acme:of_owner")                                        # another organization's: kept
        self.add("agent_question", other, "2026-11-01T10:00:01Z", session="SU", position="of_unit")
        asks = of_asks.project(of_asks.load(self.conn))
        self.assertEqual([a["to"] for a in asks], ["of_owner", "acme:of_owner"])
        got = of_asks_answer.pending(self.conn, asks, self.positions(), self.now("2026-11-01T10:20:00Z"))
        self.assertEqual([(d["position"], d["sender"]) for d in got], [("of_owner", "of_unit"), ("of_unit", "of")])
        self.assertEqual((of_asks.bare_to(None, "ofb"), of_asks.bare_to("founder", "ofb")), ("founder", "founder"))

    def test_an_ask_routed_to_no_position_is_told_to_its_asker_once(self):
        """of_server, message 01a1e0a3-b569: three asks routed to another organization's prefix were skipped on every
        pass for 18 hours, with no record and no word to anyone; only the 2 h escalation reached the founder."""
        def routed(to, at):
            return self.add("agent_question", {"detected_by": "declared", "to": to,
                                               "questions": [{"question": "Pause?"}]}, at, session="SU",
                            position="of_unit")
        routed("owner", "2026-11-01T09:59:58Z")                  # the person's queue, like founder: no note
        routed("ofb:owner", "2026-11-01T09:59:59Z")
        other = routed("acme:of_owner", "2026-11-01T10:00:00Z")
        typo = routed("of_ownr", "2026-11-01T10:00:01Z")
        done = routed("of_old", "2026-11-01T10:00:02Z")
        renamed = routed("of_boss", "2026-11-01T10:00:03Z")
        positions = self.positions() + [{"name": "of_old", "_org": "ofb", "status": "finished",
                                         "successor": "of_owner"}]
        positions[2] = dict(positions[2], aliases=["of_boss"])
        now = self.now("2026-11-01T10:20:00Z")
        pend = lambda: of_asks_answer.pending(self.conn, of_asks.project(of_asks.load(self.conn)), positions, now)  # noqa: E731
        got = pend()
        self.assertEqual([d["key"] for d in got], [f"unrouted:{other}:of_unit", f"unrouted:{typo}:of_unit",
                                                   f"unrouted:{done}:of_unit", f"routed:{renamed}:of_owner"])
        self.assertIn("'acme:of_owner', no position of your organization", got[0]["text"])
        self.assertIn(f"of asks withdraw {of_asks_answer.card_id(other)}", got[0]["text"])   # its card id
        self.assertIn("a finished position (its successor is of_owner)", got[2]["text"])
        self.assertEqual({d["sender"] for d in got[:3]}, {"of"})
        self.assertEqual([d["key"] for d in of_asks_answer.pending(
            self.conn, of_asks.project(of_asks.load(self.conn)), positions, now, only="of_owner")],
            [f"routed:{renamed}:of_owner"])
        for d in got[:3]:  # typed once: the asker's record of it ends the note
            self.add("human_prompt", {"origin": "injected", "text": "Message from of (via of, authority: none)\n"
                                      + d["text"]}, "2026-11-01T10:21:00Z", session="SU", position="of_unit")
        self.assertEqual([d["key"] for d in pend()], [f"routed:{renamed}:of_owner"])

    def test_a_user_organizations_login_screen_still_reaches_of_capacity(self):
        """of_review on PR 226: of_blocked.TO routes a login screen of any organization's position to of_capacity by
        its bare name; it must reach of_capacity, never turn into a note to the stuck asker."""
        rec = of_events.record("agent_question", {"detected_by": "blocked", "blocked_type": "login", "to": "of_capacity",
                                                  "pane": "wE:pP", "questions": [{"question": "login?"}]},
                               position="shop_keeper", session="SK", org="acme")
        rec["timestamp"] = "2026-11-01T10:00:00Z"
        of_store.add_event(self.conn, rec)
        aid = rec["of.event.id"]
        positions = self.positions() + [{"name": "of_capacity", "_org": "ofb"}, {"name": "shop_keeper", "_org": "acme"}]
        got = of_asks_answer.pending(self.conn, of_asks.project(of_asks.load(self.conn)), positions,
                                     self.now("2026-11-01T10:20:00Z"))
        self.assertEqual([d["key"] for d in got], [f"routed:{aid}:of_capacity"])

    def test_a_limit_screen_for_of_capacity_goes_to_its_inbox_and_a_login_is_typed(self):
        """of_capacity, 2026-11-09: its balancer acts on every limit within 5 minutes; a typed limit ask only woke it."""
        def blocked(kind, at, **extra):
            return self.add("agent_question", dict({"detected_by": "blocked", "blocked_type": kind, "to": "of_owner",
                                                    "pane": "wE:pP", "questions": [{"question": f"{kind}?"}]}, **extra),
                            at, session="SU", position="of_unit")
        blocked("limit", "2026-11-01T10:00:00Z", delivery="fyi")
        blocked("limit", "2026-11-01T10:00:01Z")  # filed before the delivery field: by its type
        login = blocked("login", "2026-11-01T10:00:02Z")
        asks = of_asks.project(of_asks.load(self.conn))
        got = of_asks_answer.pending(self.conn, asks, self.positions(), self.now("2026-11-01T10:20:00Z"))
        self.assertEqual([d["key"] for d in got], [f"routed:{login}:of_owner"])

    def test_an_escalated_frame_carries_both_marks_in_its_first_line(self):
        f = of_asks_answer.routed_frame("of_unit", "money", "01a1bd98-55d4-7583-ac83-bc1d878829d1", "Restart?", [], True,
                                        escalated="ask.blocking")
        first = f.splitlines()[0]
        self.assertIn(of_asks_answer.routed_mark("878829d1"), first)
        self.assertIn(of_asks_answer.escalated_mark("878829d1"), first)  # the collector keeps the first 8 KB
        self.assertIn("--escalates 878829d1", f)
        self.assertNotIn("of ask the founder", f)  # the old hint made platform_owner file it again (11-08)
        self.assertIn('of asks answered 878829d1 "what you did"', f)  # settled by the owner: close his copy (11-09)

    def routed(self, at="2026-11-01T10:00:00Z"):
        q = {"detected_by": "declared", "to": "of_owner", "blocking": True,
             "questions": [{"question": "Restart the bot at 15:00?", "options": ["yes", "no"]}]}
        return self.add("agent_question", q, at, session="SU", position="of_unit")

    def escalated(self, aid, at="2026-11-01T12:00:00Z", rule="ask_unanswered > 2h"):
        self.add("ask_escalated", {"ask_id": aid, "from_owner": "of_owner", "to": "founder", "rule": rule}, at,
                 session=None, position="of_unit")
        return self.add("agent_question", {"detected_by": "declared", "source": "escalated", "to": "founder",
                                           "escalated_from": aid, "questions": [{"question": "[escalated] Restart?"}]},
                        at, session="SU", position="of_unit")

    def rewrite(self, aid, at="2026-11-01T12:03:00Z"):
        return self.add("agent_question", {"detected_by": "declared", "source": "declared", "to": "founder",
                                           "escalated_from": aid, "questions": [{"question": "Restart the bot?"}]},
                        at, session="SO", position="of_owner")

    def test_a_rewrite_supersedes_the_copy_and_both_hear_the_answer(self):
        aid = self.routed()
        copy = self.escalated(aid)
        self.assertEqual(of_asks_answer.recipients(self.conn, [copy]), [("of_unit", "SU", [aid]), ("of_owner", None, [aid])])
        rw = self.rewrite(aid)
        state = {a["event_id"]: a["state"] for a in of_asks.project(of_asks.load(self.conn))}
        self.assertEqual((state[aid], state[copy], state[rw]), ("escalated", "superseded", "open"))
        # the asker hears it under its own ask's id, the owner under its rewrite's
        self.assertEqual(of_asks_answer.recipients(self.conn, [rw]), [("of_unit", "SU", [aid]), ("of_owner", "SO", [rw, aid])])

    def test_the_unit_owner_closes_the_founders_copy_by_the_routed_id(self):
        """2026-11-09: a unit owner settled a routed request in 20 s; the founder's copy stayed open."""
        import importlib.machinery
        import importlib.util
        import types
        loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(HERE, "..", "bin", "of"))
        of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli", loader))
        loader.exec_module(of)
        saved = of._asks_actor, of._reconcile_ask_tokens, of._load_quiet, os.environ.get("OF_DB")
        of._asks_actor, of._reconcile_ask_tokens, of._load_quiet = (lambda: "of_owner"), (lambda *a: None), (lambda: [])
        os.environ["OF_DB"] = os.path.join(self.tmp.name, "of.db")
        try:
            aid = self.routed()
            copy = self.escalated(aid)
            other = self.escalated(self.routed("2026-11-01T10:05:00Z"), at="2026-11-01T12:01:00Z")
            args = types.SimpleNamespace(action="answered", id=aid[-8:], reason=["routed it myself"], watch=False)
            self.assertEqual(of.cmd_asks(args), 0)
            state = {a["event_id"]: a["state"] for a in of_asks.project(of_asks.load(self.conn))}
            self.assertEqual((state[aid], state[copy], state[other]), ("settled_in_unit", "answered", "open"))
            a = self.asks()[aid]  # the unit owner closed the founder's card: never read as his answer (of_server, PR 222)
            self.assertEqual((a["settled_by"], a["settled_via"]), ("of_owner", copy))
            self.assertEqual(of_asks.state_label(a), "settled_in_unit by of_owner")
            row = self.conn.execute("SELECT payload FROM event WHERE name = 'of.agent_question_answered'").fetchone()
            self.assertEqual((json.loads(row[0])["by"], json.loads(row[0])["note"]), ("of_owner", "routed it myself"))
            with self.assertRaises(of.OfError):
                of.cmd_asks(args)  # closed: nothing open matches any more
        finally:
            of._asks_actor, of._reconcile_ask_tokens, of._load_quiet = saved[:3]
            os.environ.pop("OF_DB") if saved[3] is None else os.environ.__setitem__("OF_DB", saved[3])

    def asks(self):
        return {a["event_id"]: a for a in of_asks.project(of_asks.load(self.conn))}

    def answer(self, aid, at, **extra):
        return self.add("agent_question_answered", dict({"ask_id": aid, "ask_ids": [aid], "by": "owner"}, **extra), at,
                        session=None, position=None)

    def test_the_founders_answer_to_its_copy_answers_the_routed_ask_upstream(self):
        """A user organization's structure keeper, via of_platform (message 01a1deaf-47e0): of asks --to-me listed 22
        routed asks as escalated for its unit owner, 18 of them answered by the founder on a copy."""
        aid = self.routed()
        copy = self.escalated(aid)
        esc = self.conn.execute("SELECT event_id FROM event WHERE name = 'of.ask_escalated'").fetchone()[0]
        self.assertEqual(self.asks()[aid]["state"], "escalated")
        ans = self.answer(copy, "2026-11-01T12:30:00Z", answer="yes")
        a = self.asks()[aid]
        self.assertEqual((a["state"], a["settled_by"], a["settled_via"], a["settled_event"], a["settled_at"]),
                         ("answered_upstream", "owner", copy, ans, "2026-11-01T12:30:00Z"))
        self.assertEqual(of_asks.state_label(a), "answered_upstream by owner")
        # it left its unit owner's queue when it was escalated: that stays its closure, which the unit owner's
        # measures read (an escalated ask is closed_otherwise on its side, docs/studio.md)
        self.assertEqual((a["closed_by"], a["closed_at"]), (esc, "2026-11-01T12:00:00Z"))

    def test_a_withdrawn_copy_settles_nothing_and_a_later_rewrite_still_can(self):
        """A unit owner withdraws its rewrite and files another seconds later (seen three times in one user
        organization's log): the routed ask stays escalated in between, so of ask --escalates still finds it."""
        aid = self.routed()
        self.escalated(aid)
        rw = self.rewrite(aid)
        self.add("ask_withdrawn", {"ask_id": rw, "by": "of_owner", "reason": "reworded"}, "2026-11-01T12:04:00Z",
                 session="SO", position="of_owner")
        self.assertEqual(self.asks()[rw]["state"], "withdrawn")
        self.assertEqual(self.asks()[aid]["state"], "escalated")
        rw2 = self.rewrite(aid, at="2026-11-01T12:04:05Z")
        self.answer(rw2, "2026-11-01T13:00:00Z")
        a = self.asks()[aid]
        self.assertEqual((a["state"], a["settled_via"]), ("answered_upstream", rw2))

    def test_a_task_copy_answers_it_once_its_asker_checked_the_result(self):
        aid = self.routed()
        self.escalated(aid)
        rw = self.add("agent_question", {"detected_by": "declared", "source": "declared", "to": "founder",
                                         "escalated_from": aid, "task": True, "steps": "Rotate the bot's key",
                                         "questions": [{"question": "Rotate the bot's key"}]},
                      "2026-11-01T12:03:00Z", session="SO", position="of_owner")
        self.answer(rw, "2026-11-01T12:20:00Z", answer="done")
        self.assertEqual((self.asks()[rw]["claim"], self.asks()[aid]["state"]), ("done", "escalated"))  # a claim only
        done = self.add("state_declared", {"state": "working", "done_task": rw}, "2026-11-01T12:25:00Z",
                        session="SO", position="of_owner")
        a = self.asks()[aid]  # he said done, its asker checked it: his answer
        self.assertEqual((a["state"], a["settled_by"], a["settled_via"], a["settled_event"]),
                         ("answered_upstream", "owner", rw, done))

    def test_a_task_copy_done_with_no_word_from_the_founder_is_settled_in_the_unit(self):
        aid = self.routed()
        self.escalated(aid)
        rw = self.add("agent_question", {"detected_by": "declared", "source": "declared", "to": "founder",
                                         "escalated_from": aid, "task": True, "steps": "Rotate the bot's key",
                                         "questions": [{"question": "Rotate the bot's key"}]},
                      "2026-11-01T12:03:00Z", session="SO", position="of_owner")
        self.add("state_declared", {"state": "working", "done_task": rw}, "2026-11-01T12:25:00Z",
                 session="SO", position="of_owner")
        a = self.asks()[aid]
        self.assertEqual((a["state"], a["settled_by"]), ("settled_in_unit", "of_owner"))

    def test_with_no_copy_open_its_unit_owner_closes_it_by_its_own_id(self):
        """A user organization's structure keeper, message 01a1deb0-a318: once the founder's copy was withdrawn, of
        asks answered <routed id> found nothing open, so its unit owner had no command to close it. An answer naming
        the escalated ask itself counts only while no copy of it is open (otherwise of asks answered closes the copy,
        and the founder's card goes)."""
        import importlib.machinery
        import importlib.util
        import types
        loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(HERE, "..", "bin", "of"))
        of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli", loader))
        loader.exec_module(of)
        saved = of._asks_actor, of._reconcile_ask_tokens, of._load_quiet, os.environ.get("OF_DB")
        of._asks_actor, of._reconcile_ask_tokens, of._load_quiet = (lambda: "of_owner"), (lambda *a: None), (lambda: [])
        os.environ["OF_DB"] = os.path.join(self.tmp.name, "of.db")
        try:
            aid = self.routed()
            copy = self.escalated(aid)
            early = self.answer(aid, "2026-11-01T12:01:00Z")  # names the routed ask while its copy is open: nothing
            self.assertEqual((self.asks()[aid]["state"], self.asks()[copy]["state"]), ("escalated", "open"))
            self.add("ask_withdrawn", {"ask_id": copy, "by": "owner", "reason": "decided by the unit owner"},
                     "2026-11-01T12:10:00Z", session="SU", position="of_unit")
            args = types.SimpleNamespace(action="answered", id=aid[-8:], reason=["decided inside the envelope"],
                                         watch=False)
            self.assertEqual(of.cmd_asks(args), 0)
            row = self.conn.execute("SELECT event_id, payload FROM event WHERE name = 'of.agent_question_answered' "
                                    "AND event_id != ?", (early,)).fetchone()
            self.assertEqual((json.loads(row[1])["ask_ids"], json.loads(row[1])["by"]), ([aid], "of_owner"))
            a = self.asks()[aid]
            self.assertEqual((a["state"], a.get("settled_via"), a["settled_event"], a["settled_by"]),
                             ("settled_in_unit", None, row[0], "of_owner"))
            self.assertEqual(a["closed_at"], "2026-11-01T12:00:00Z")  # its escalation stays its closure
            with self.assertRaises(of.OfError):
                of.cmd_asks(args)  # settled: nothing open or escalated matches any more
        finally:
            of._asks_actor, of._reconcile_ask_tokens, of._load_quiet = saved[:3]
            os.environ.pop("OF_DB") if saved[3] is None else os.environ.__setitem__("OF_DB", saved[3])

    def test_the_views_fold_answers_it_too(self):
        """current_asks carries the listener's view on with the log's tail: the copy's answer reaches the routed ask
        there as well."""
        aid = self.routed()
        copy = self.escalated(aid)
        before = json.loads(json.dumps(of_asks.project(of_asks.load(self.conn))))
        self.answer(copy, "2026-11-01T12:30:00Z")
        st = of_asks.state_from(before, {})
        of_asks.project([r for r in of_asks.load(self.conn) if r["name"] == "of.agent_question_answered"], st)
        self.assertEqual((st["by_id"][aid]["state"], st["by_id"][copy]["state"]), ("answered_upstream", "answered"))

    def test_only_its_unit_owner_its_asker_or_the_owner_settle_it(self):
        """of_server on PR 222: an answer by a third position settled an escalated routed ask, which then left its
        unit owner's queue with nothing done there. It still closes a founder copy it answers, as before."""
        import importlib.machinery
        import importlib.util
        import types
        loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(HERE, "..", "bin", "of"))
        of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli", loader))
        loader.exec_module(of)
        actor = ["of_stranger"]
        saved = of._asks_actor, of._reconcile_ask_tokens, of._load_quiet, os.environ.get("OF_DB")
        of._asks_actor, of._reconcile_ask_tokens, of._load_quiet = (lambda: actor[0]), (lambda *a: None), (lambda: [])
        os.environ["OF_DB"] = os.path.join(self.tmp.name, "of.db")
        try:
            aid = self.routed()
            copy = self.escalated(aid)
            self.answer(copy, "2026-11-01T12:05:00Z", by="of_stranger")  # the founder's card goes ...
            self.assertEqual((self.asks()[copy]["state"], self.asks()[aid]["state"]), ("answered", "escalated"))
            self.answer(aid, "2026-11-01T12:06:00Z", by="of_stranger")  # ... and nothing open: still not its to settle
            self.assertEqual(self.asks()[aid]["state"], "escalated")
            args = types.SimpleNamespace(action="answered", id=aid[-8:], reason=[], watch=False)
            with self.assertRaises(of.OfError) as e:
                of.cmd_asks(args)
            self.assertIn("of_owner", str(e.exception))
            self.assertIn("of_unit", str(e.exception))
            self.assertEqual(self.conn.execute("SELECT count(*) FROM event WHERE name = 'of.agent_question_answered'")
                             .fetchone()[0], 2)  # refused: nothing recorded
            actor[0] = "of_unit"  # its asker may
            self.assertEqual(of.cmd_asks(args), 0)
            a = self.asks()[aid]
            self.assertEqual((a["state"], a["settled_by"]), ("settled_in_unit", "of_unit"))
        finally:
            of._asks_actor, of._reconcile_ask_tokens, of._load_quiet = saved[:3]
            os.environ.pop("OF_DB") if saved[3] is None else os.environ.__setitem__("OF_DB", saved[3])

    def test_the_unit_owner_is_told_once_of_a_later_escalation(self):
        aid = self.routed()
        self.add("human_prompt", {"origin": "injected", "text": of_asks_answer.routed_frame(
            "of_unit", "money", aid, "Restart?", [], True)}, "2026-11-01T10:00:03Z", session="SO", position="of_owner")
        self.escalated(aid)
        now = self.now("2026-11-01T12:10:00Z")
        pend = lambda: of_asks_answer.pending(self.conn, of_asks.project(of_asks.load(self.conn)), self.positions(), now)  # noqa: E731
        got = pend()
        self.assertEqual([(d["key"].split(":")[0], d["position"]) for d in got], [("escalated", "of_owner")])
        self.assertIn("the one typed in before", got[0]["text"])  # it saw the question: the note only
        self.assertIn(f"--escalates {of_asks_answer.card_id(aid)}", got[0]["text"])
        self.add("human_prompt", {"origin": "injected", "text": got[0]["text"]}, "2026-11-01T12:05:00Z",
                 session="SO", position="of_owner")
        self.assertEqual(pend(), [])

    def test_an_escalation_with_the_old_prefixed_owner_still_tells_the_unit_owner(self):
        """of_server, message 01a1e0a3-b569: from_owner copies the routed ask's `to`, stored with its organization's
        prefix before 2026-11-15, and such a note was skipped as another organization's."""
        aid = self.routed()
        self.add("ask_escalated", {"ask_id": aid, "from_owner": "ofb:of_owner", "to": "founder",
                                   "rule": "ask_unanswered > 2h"}, "2026-11-01T12:00:00Z", session=None,
                 position="of_unit")
        got = of_asks_answer.pending(self.conn, of_asks.project(of_asks.load(self.conn)), self.positions(),
                                     self.now("2026-11-01T12:10:00Z"))
        self.assertEqual([(d["key"].split(":")[0], d["position"]) for d in got], [("escalated", "of_owner")])

    def test_no_note_after_a_rewrite_or_an_answer_and_the_full_question_when_unseen(self):
        aid = self.routed()
        copy = self.escalated(aid)
        now = self.now("2026-11-01T12:10:00Z")
        pend = lambda: of_asks_answer.pending(self.conn, of_asks.project(of_asks.load(self.conn)), self.positions(), now)  # noqa: E731
        got = pend()
        self.assertTrue(got[0]["text"].startswith(of_asks_answer.routed_frame(
            "of_unit", "money", aid, "Restart the bot at 15:00?", ["yes", "no"], True, escalated="ask_unanswered > 2h")))
        self.add("agent_question_answered", {"ask_id": copy, "ask_ids": [copy], "by": "owner"}, "2026-11-01T12:06:00Z",
                 session=None, position=None)
        self.assertEqual(pend(), [])
        aid2 = self.routed("2026-11-01T10:30:00Z")
        self.escalated(aid2)
        self.rewrite(aid2, "2026-11-01T12:01:00Z")
        self.assertEqual(pend(), [])
        aid3 = self.routed("2026-11-01T10:40:00Z")
        self.add("ask_escalated", {"ask_id": aid3, "from_owner": "of_owner", "to": "founder", "rule": "by_owner"},
                 "2026-11-01T12:00:00Z", session=None, position="of_unit")
        self.assertEqual(pend(), [])  # escalated by the owner's own rewrite: nothing to tell it

    def test_the_pass_types_into_idle_panes_with_backoff_and_a_limit(self):
        a1, a2, _ = self.group()
        self.answered(a1, a2)
        agents = [{"name": "of_a", "pane_id": "w1:pA", "agent_status": "idle", "agent_session": {"value": "S1"}},
                  {"name": "of_b", "pane_id": "w1:pB", "agent_status": "working", "agent_session": {"value": "S2"}}]
        bind = lambda positions, ags: ([(p, a) for p in positions for a in ags if a["name"] == p["name"]], [], [])
        typed = []

        def fake(pane, text, session, sender, channel, skip):
            self.assertFalse(skip())
            typed.append((pane, session, sender, channel))
            return True, None
        t0 = self.now("2026-11-01T11:10:00Z")
        run = lambda t: of_asks_answer.redeliver_pass(self.conn, [], self.positions(), agents, bind, now=t,
                                                      deliver_fn=fake, log=lambda m: None)
        self.assertEqual(run(t0), 1)  # of_b is busy: next pulse
        self.assertEqual(typed, [("w1:pA", "S1", "owner", "asks-ui")])
        self.assertEqual(run(t0 + 300), 0)  # typed, no record yet: not again within REDELIVER_EVERY_MIN
        run(t0 + 1900)
        run(t0 + 3800)
        self.assertEqual(run(t0 + 5700), 0)  # REDELIVER_TRIES typed attempts, then it stops
        self.assertEqual(len([x for x in typed if x[0] == "w1:pA"]), of_asks_answer.REDELIVER_TRIES)
        agents[1]["agent_status"] = "idle"
        self.assertEqual(run(t0 + 5800), 1)
        self.assertEqual(typed[-1][0], "w1:pB")

    def test_the_founders_answers_go_in_one_prompt_oldest_first_before_other_texts(self):
        # 2026-11-09: four answers typed one at a time; the second prompt found the agent busy
        a1, a2, _ = self.group()
        self.answered(a1, a2)
        q = {"detected_by": "declared", "questions": [{"question": "And the p3 window?"}]}
        a3 = self.add("agent_question", q, "2026-11-01T10:30:00Z")
        self.add("agent_question_answered", {"ask_id": a3, "ask_ids": [a3], "answer": "Tue 10:00", "via": "asks-ui",
                                             "by": "owner"}, "2026-11-01T11:01:00Z", session=None, position=None)
        routed = {"detected_by": "declared", "to": "of_a", "questions": [{"question": "Restart?"}]}
        r = self.add("agent_question", routed, "2026-11-01T10:40:00Z", session="SU", position="of_unit")
        asks = of_asks.project(of_asks.load(self.conn))
        todo = of_asks_answer.pending(self.conn, asks, self.positions(), self.now("2026-11-01T11:10:00Z"), only="of_a")
        self.assertEqual({d["position"] for d in todo}, {"of_a"})  # of_b's copy is not of_a's
        self.assertEqual(len(todo), 3)
        first = of_asks_answer.prompts(todo)
        self.assertEqual(len(first), 1)
        self.assertEqual([d["key"].split(":")[0] for d in first[0]], ["answer", "answer"])
        text = of_asks_answer.joined(first[0])
        self.assertLess(text.index(of_asks_answer.answer_mark(a1)), text.index(of_asks_answer.answer_mark(a3)))
        self.assertNotIn(of_asks_answer.routed_mark(r), text)  # another kind (a tool's text): the next prompt
        big = [dict(d, text="x" * 4000) for d in todo if d["sender"] == "owner"]
        self.assertEqual([len(g) for g in of_asks_answer.prompts(big)], [1])  # past JOIN_MAX_BYTES: the next pass

    def test_a_turn_end_pass_types_into_a_pane_herdr_still_calls_working(self):
        a1, a2, _ = self.group()
        self.answered(a1, a2)
        agents = [{"name": "of_a", "pane_id": "w1:pA", "agent_status": "working", "agent_session": {"value": "S1"}},
                  {"name": "of_b", "pane_id": "w1:pB", "agent_status": "idle", "agent_session": {"value": "S2"}}]
        bind = lambda positions, ags: ([(p, a) for p in positions for a in ags if a["name"] == p["name"]], [], [])
        typed = []

        def fake(pane, text, session, sender, channel, skip):
            typed.append(pane)
            return True, None
        t = self.now("2026-11-01T11:00:20Z")  # younger than REDELIVER_MIN_AGE_S, older than REDELIVER_SOON_S
        run = lambda **k: of_asks_answer.redeliver_pass(self.conn, [], self.positions(), agents, bind, now=t,  # noqa: E731
                                                        deliver_fn=fake, log=lambda m: None, **k)
        self.assertEqual(run(), 0)  # the pulse leaves it to the popup's own attempt
        self.assertEqual(run(only="of_a", min_age=of_asks_answer.REDELIVER_SOON_S, need_idle=False), 1)
        self.assertEqual(typed, ["w1:pA"])  # of_b is idle, but this pass is of_a's turn end


class LeaveTest(RedeliverTest):
    """of_server, msg 01a1f51f-6c9e (2026-11-19): the popup's outbox held files that a detached sender replayed as the
    owner's answers and marks, and a file any session wrote there was replayed too (of_review, msg 01a1f50c-c57a).
    The screen leaving now records the owner's acts in its own process; only the typing leaves it, and that types
    only what the store holds as his answer."""
    def real(self):
        """The owner's real test (of_asks_answer.not_the_owner), decided by each test's environment and terminal."""
        return mock.patch.object(of_asks_answer, "not_the_owner", REAL_OWNER_CHECK)

    def setUp(self):
        super().setUp()
        self.clean = {k: v for k, v in os.environ.items()
                      if k not in ("CLAUDE_CODE_SESSION_ID", "OF_POSITION", "CLAUDECODE")}
        self.tty = mock.Mock()
        self.tty.isatty.return_value = True

    def leave(self, pendings, env=None, tty=None, own_store=False):
        """own_store: the screen opens its own connections to the test's store, with their own waits."""
        import of_asks_tui
        conn, typed, spawned = self.conn, [], []

        class Own:  # the screen's own connection: the test's store, not closed under it
            def __getattr__(self, name):
                return getattr(conn, name)

            def close(self):
                pass
        real = of_store.connect
        with mock.patch.object(of_store, "connect", real if own_store else (lambda *a, **k: Own())), \
                mock.patch.object(of_asks_answer, "deliver", lambda *a, **k: typed.append(a) or "delivered"), \
                mock.patch.object(of_asks_tui, "spawn_deliver", lambda of_path, events: spawned.append(list(events))), \
                mock.patch.dict(os.environ, env if env is not None else self.clean, clear=True), \
                mock.patch.object(sys, "stdin", tty or self.tty), self.real():
            out = of_asks_tui.leave(pendings, self.positions(), "of")
        return out, typed, spawned

    def events(self, name):
        return [(e, json.loads(p)) for e, p in self.conn.execute(
            "SELECT event_id, payload FROM event WHERE name = ? ORDER BY ts", (name,))]

    def test_an_answer_is_recorded_here_and_only_typed_by_the_deliverer(self):
        a1, a2, item = self.group()
        out, typed, spawned = self.leave([{"what": "answer", "text": "yes", "item": item}])
        answers = self.events("of.agent_question_answered")
        self.assertEqual([(p["ask_ids"], p["answer"], p["by"]) for _, p in answers], [([a1, a2], "yes", "owner")])
        self.assertTrue(out[0][1])
        self.assertEqual(typed, [])                      # no pane waited on as the screen leaves
        self.assertEqual(spawned, [[answers[0][0]]])     # the deliverer types exactly that recorded answer
        self.assertEqual(of_asks_answer.unsent_drafts(), [])
        out, typed, spawned = self.leave([{"what": "answer", "text": "yes", "item": item}])  # its thread got there
        self.assertEqual(len(self.events("of.agent_question_answered")), 1)
        self.assertEqual(spawned, [[]])                  # nothing new to type; the ask tokens still follow

    def test_a_mark_is_recorded_here_by_the_owner_and_refused_to_an_agent(self):
        a1, a2, item = self.group()
        no_tty = mock.Mock()
        no_tty.isatty.return_value = False
        for env, tty in ((dict(self.clean, CLAUDECODE="1"), None), (dict(self.clean, OF_POSITION="of_x"), None),
                         (dict(self.clean, CLAUDE_CODE_SESSION_ID="s"), None), (self.clean, no_tty)):
            out, _, spawned = self.leave([{"what": "withdraw", "item": item}], env=env, tty=tty)
            self.assertEqual((out, spawned), ([], []))  # leaving records nothing
            with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(sys, "stdin", tty or self.tty), \
                    self.real():
                self.assertFalse(of_asks_answer.owner_mark(self.conn, item, "withdraw")[0])  # nor the mark itself
        self.assertEqual(self.events("of.ask_withdrawn"), [])
        out, _, spawned = self.leave([{"what": "withdraw", "item": item}, {"what": "answered", "item": item}])
        self.assertEqual([x[1] for x in out], [True, True])
        (_, w), = self.events("of.ask_withdrawn")
        self.assertEqual((w["ask_id"], w["by"]), (a1, "owner"))
        (_, c), = self.events("of.agent_question_answered")
        self.assertEqual((c["ask_ids"], c["by"], c.get("answer")), ([a1, a2], "owner", None))
        self.assertEqual(spawned, [[]])

    def test_an_act_its_thread_recorded_is_one_event_and_never_typed_twice(self):
        """Codex's review of this change: a mark slow in its thread was recorded again as the screen left. Each act's
        id makes it one event. An answer its thread recorded may be being typed by it: the deliverer is not given it
        (of_review on PR 398); the pulse or the turn's end types it if the thread did not."""
        a1, a2, item = self.group()
        mark = dict(item, act="00000000-0000-4000-8000-00000000000a")
        with mock.patch.dict(os.environ, self.clean, clear=True), mock.patch.object(sys, "stdin", self.tty):
            self.assertTrue(of_asks_answer.owner_mark(self.conn, mark, "answered")[0])  # its thread got there
        out, _, _ = self.leave([{"what": "answered", "item": mark, "started": True, "done": False}])
        self.assertTrue(out[0][1])
        self.assertEqual(len(self.events("of.agent_question_answered")), 1)
        self.conn.execute("DELETE FROM event WHERE name = 'of.agent_question_answered'")
        ans = dict(item, act="00000000-0000-4000-8000-00000000000b")
        self.assertTrue(of_asks_answer.answer(self.conn, ans, "yes", self.positions(), lambda name: None)[0])
        out, typed, spawned = self.leave([{"what": "answer", "text": "yes", "item": ans, "started": True}])
        self.assertEqual([e for e, _ in self.events("of.agent_question_answered")], [ans["act"]])
        self.assertEqual((typed, spawned), ([], [[]]))  # never handed to the deliverer: its thread may be typing it

    def test_a_copy_in_the_spool_and_a_second_flush_stay_one_event(self):
        _, _, item = self.group()
        ans = dict(item, act="00000000-0000-4000-8000-00000000000c")
        rec = of_events.record("agent_question_answered", {"ask_id": item["ids"][0], "ask_ids": item["ids"],
                                                           "answer": "yes", "via": "asks-ui", "by": "owner"})
        rec["of.event.id"] = ans["act"]
        of_store.spool_append("events.jsonl", rec)  # its thread wrote while the store was busy
        p = {"what": "answer", "text": "yes", "item": ans}
        self.leave([p])
        self.assertEqual(self.leave([p])[0], [])        # herdr's signal after q: nothing again
        of_store.drain_spool(self.conn)
        self.assertEqual(len(self.events("of.agent_question_answered")), 1)

    def test_leaving_never_waits_on_a_busy_store(self):
        import sqlite3
        import time
        _, _, item = self.group()
        ans = dict(item, act="00000000-0000-4000-8000-00000000000d")
        writer = sqlite3.connect(os.path.join(self.tmp.name, "of.db"), isolation_level=None)
        writer.execute("BEGIN IMMEDIATE")  # the listener in a long write
        try:
            t0 = time.time()
            out, _, spawned = self.leave([{"what": "answer", "text": "yes", "item": ans}], own_store=True)
            took = time.time() - t0
        finally:
            writer.execute("ROLLBACK")
            writer.close()
        self.assertTrue(out[0][1], out)
        self.assertLess(took, 3.0)                       # the store's own wait is 10 s
        self.assertEqual(spawned, [[ans["act"]]])
        self.assertEqual(self.events("of.agent_question_answered"), [])  # in the spool
        of_store.drain_spool(self.conn)                  # the deliverer drains it first
        self.assertEqual([e for e, _ in self.events("of.agent_question_answered")], [ans["act"]])

    def test_an_answer_nothing_took_keeps_its_draft_and_a_dialog_answer_is_its_threads(self):
        a1, _, item = self.group()
        with mock.patch.object(of_events, "append", lambda rec, raw_ref=None: False):
            out, _, spawned = self.leave([{"what": "answer", "text": "yes", "item": item}])
        self.assertFalse(out[0][1])
        self.assertEqual([(d["ask_id"], bool(d.get("failed_at"))) for d in of_asks_answer.unsent_drafts()],
                         [(a1, True)])                   # its card comes back with the text, for D
        self.assertEqual(spawned, [[]])
        out, _, _ = self.leave([{"what": "answer", "text": "1", "item": dict(item, kind="blocked: dialog"),
                                 "started": True}])
        self.assertEqual(out, [])                        # its keys are never pressed twice

    def test_an_agents_session_records_nothing_as_the_owner(self):
        """Every writer checks itself (of_review's trust notes on PR 398): leaving, the sending thread's answer and
        record_answer refuse in an agent's session, whoever calls them."""
        import of_asks_tui
        _, _, item = self.group()
        agent = dict(self.clean, CLAUDECODE="1")
        out, _, spawned = self.leave([{"what": "answer", "text": "yes", "item": item}], env=agent)
        self.assertEqual((out, spawned), ([], []))
        with mock.patch.dict(os.environ, agent, clear=True), mock.patch.object(sys, "stdin", self.tty), self.real():
            self.assertFalse(of_asks_tui.threaded_answer(self.positions())(item, "yes")[0])
            self.assertFalse(of_asks_answer.record_answer(self.conn, item, "yes")[0])
            self.assertFalse(of_asks_answer.answer(self.conn, item, "yes", self.positions(), lambda n: None)[0])
        self.assertEqual((self.events("of.agent_question_answered"), of_asks_answer.unsent_drafts()), ([], []))

    def test_a_delivered_earlier_answer_never_hides_a_later_one(self):
        """Codex's review: `seen` took the earlier answer before its delivery check, so the same words given again
        after a reopen were never typed."""
        a1, a2, _ = self.group()
        self.answered(a1, a2, ts="2026-11-01T11:00:00Z")
        for who, sess in (("of_a", "S1"), ("of_b", "S2")):
            self.add("human_prompt", {"origin": "human", "channel": "asks-ui", "text": of_asks_answer.frame(a1, "yes")},
                     "2026-11-01T11:00:02Z", session=sess, position=who)
        self.answered(a1, a2, ts="2026-11-01T12:00:00Z")
        got = of_asks_answer.pending(self.conn, [], self.positions(), self.now("2026-11-01T12:30:00Z"))
        self.assertEqual(sorted(d["position"] for d in got), ["of_a", "of_b"])

    def test_a_file_in_the_old_outbox_is_shown_as_a_draft_and_never_replayed(self):
        import of_asks_inbox
        import of_asks_tui
        a1, a2, item = self.group()
        d = of_asks_tui.outbox_dir()
        os.makedirs(d)
        for name, body in (("1.json", {"what": "answer", "text": "yes", "item": item}),
                           ("2.json", {"what": "withdraw", "item": item}),
                           ("3.json", {"what": "answer", "text": "no", "item": {"ids": ["../../escape"]}}),
                           ("4.json", "not an entry"),
                           ("5.json", {"what": "answer", "text": "older", "item": item})):  # his first draft kept
            with open(os.path.join(d, name), "w") as fh:
                json.dump(body, fh)
        with open(os.path.join(d, "0.json"), "w") as fh:  # too deep to parse: set aside, the rest still taken
            fh.write("[" * 100000 + "]" * 100000)
        self.assertEqual(len(of_asks_tui.outbox_leftovers()), 6)
        self.assertEqual(self.events("of.agent_question_answered") + self.events("of.ask_withdrawn"), [])
        self.assertEqual([(x["ask_id"], x["text"], bool(x.get("failed_at"))) for x in of_asks_answer.unsent_drafts()],
                         [(a1, "yes", True)])          # his card comes back with the text; only D sends it
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(of_events.STATE_DIR), "escape.json")))
        self.assertEqual([f for f in os.listdir(d) if f.endswith(".json")], [])
        self.assertEqual(of_asks_tui.outbox_leftovers(), [])
        self.assertEqual(of_asks_inbox.on_their_way(), set())

    def test_deliver_now_types_only_the_answers_named(self):
        a1, a2, _ = self.group()
        named = self.add("agent_question_answered", {"ask_id": a1, "ask_ids": [a1, a2], "answer": "yes",
                                                     "via": "asks-ui", "by": "owner"}, "2026-11-01T11:00:00Z",
                         session=None, position=None)
        self.add("agent_question_answered", {"ask_id": a1, "ask_ids": [a1, a2], "answer": "later", "via": "asks-ui",
                                             "by": "owner"}, "2026-11-01T11:00:02Z", session=None, position=None)
        agents = [{"name": "of_a", "pane_id": "w1:pA", "agent_status": "working", "agent_session": {"value": "S1"}},
                  {"name": "of_b", "pane_id": "w1:pB", "agent_status": "idle", "agent_session": {"value": "S2"}}]
        bind = lambda positions, ags: ([(p, a) for p in positions for a in ags if a["name"] == p["name"]], [], [])  # noqa: E731
        typed = []

        def fake(pane, text, session, sender, channel, skip):
            typed.append((pane, text))
            return True, None
        n = of_asks_answer.redeliver_pass(self.conn, [], self.positions(), agents, bind,
                                          now=self.now("2026-11-01T11:00:03Z"), deliver_fn=fake, log=lambda m: None,
                                          events={named}, min_age=0, need_idle=False)
        self.assertEqual(n, 2)                          # both askers, the busy one too: herdr defers it
        self.assertEqual(sorted(p for p, _ in typed), ["w1:pA", "w1:pB"])
        self.assertTrue(all(": yes" in t and "later" not in t for _, t in typed))
        self.assertEqual(of_asks_answer.redeliver_pass(self.conn, [], self.positions(), agents, bind,
                                                       now=self.now("2026-11-01T11:00:03Z"), deliver_fn=fake,
                                                       log=lambda m: None, events=set(), min_age=0), 0)
