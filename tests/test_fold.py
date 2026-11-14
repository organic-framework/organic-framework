"""One read model (bin/of_fold.py; the read path's 3.1, 3.2, 3.5, with the fold a pure function, of_architect)."""

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_events  # noqa: E402
import of_fold  # noqa: E402
import of_listen  # noqa: E402
import of_store  # noqa: E402


class FoldTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = self.tmp.name
        of_events._CONN.clear()
        self.conn = of_store.connect(os.path.join(self.tmp.name, "of.db"))
        self.view = of_fold.views()["answers"]

    def tearDown(self):
        of_events._CONN.clear()
        of_events.STATE_DIR = self.saved
        self.conn.close()
        self.tmp.cleanup()

    def answer(self, ask, words, by="owner", via="asks-ui"):
        rec = of_events.record("agent_question_answered", {"ask_id": ask, "ask_ids": [ask], "by": by, "via": via,
                                                           "answer": words}, position="p")
        of_store.add_event(self.conn, rec)
        return rec["of.event.id"]

    def reader(self):
        import sqlite3
        c = sqlite3.connect(f"file:{os.path.join(self.tmp.name, 'of.db')}?mode=ro", uri=True, isolation_level=None)
        return c

    def test_the_fold_is_pure_and_one_function_serves_the_listener_and_read(self):
        rows = {}
        ev = {"event_id": "e1", "ts": "2026-11-14T00:00:00Z", "name": "of.agent_question_answered",
              "payload": {"ask_ids": ["A"], "by": "owner", "via": "asks-ui", "answer": "approve"}}
        changed = self.view.fold(rows, [ev])
        self.assertEqual(rows, {})                                             # the input is never changed
        self.assertEqual(changed["A"]["answers"][0]["answer"], "approve")
        self.assertEqual(self.view.fold(changed, [ev]), {})                   # an event again under its id: nothing

        procs = [of_fold.FoldProcessor(self.view)]
        cur = of_listen.prepare(self.conn, procs)
        self.answer("A", "approve")
        self.answer("B", "no")
        of_listen.step(self.conn, procs, cur)
        r = of_fold.read(self.view, conn=self.reader())
        self.assertEqual((sorted(r.rows), r.late, r.age_s), (["A", "B"], False, 0.0))  # current: age 0
        self.answer("A", "reject")                                              # after the view's cursor
        r = of_fold.read(self.view, conn=self.reader())
        self.assertEqual([a["answer"] for a in r.rows["A"]["answers"]], ["approve", "reject"])  # the tail, folded
        self.assertFalse(r.late)
        self.assertGreater(r.cursor, 0)
        late = of_fold.read(self.view, conn=self.reader(), tail=0)
        self.assertTrue(late.late)
        self.assertEqual([a["answer"] for a in late.rows["A"]["answers"]], ["approve"])  # the view as it is
        of_listen.step(self.conn, procs, cur)
        self.assertEqual(of_fold.load_rows(self.conn, self.view), of_fold.read(self.view, conn=self.reader()).rows)

    def test_a_view_never_built_or_of_another_version_is_late_and_empty(self):
        self.assertEqual(of_fold.read(self.view, conn=self.reader()), of_fold.Read({}, 0, None, True))
        procs = [of_fold.FoldProcessor(self.view)]
        cur = of_listen.prepare(self.conn, procs)
        of_listen.step(self.conn, procs, cur)

        class Newer(type(self.view)):
            version = "2"
        self.assertTrue(of_fold.read(Newer(), conn=self.reader()).late)

    def test_read_reads_only_the_tail_after_the_cursor(self):
        procs = [of_fold.FoldProcessor(self.view)]
        cur = of_listen.prepare(self.conn, procs)
        for i in range(5):
            self.answer(f"A{i}", "approve")
        of_listen.step(self.conn, procs, cur)
        self.answer("Z", "approve")
        seen = []
        c = self.reader()
        c.set_trace_callback(seen.append)
        r = of_fold.read(self.view, conn=c)
        q = [s for s in seen if "FROM event" in s]
        self.assertEqual(len(q), 1)
        self.assertIn("rowid >", q[0])
        self.assertIn("LIMIT", q[0])
        self.assertIn("Z", r.rows)

    def test_a_counting_view_takes_each_event_once(self):
        """of_architect on PR 154: (1) the cursor that counts is the one committed with the rows: a batch fed again
        after a restart is dropped; (2) memory changes only after the write commits: a failed write is handled again
        onto the rows the store holds. Either way, the rows equal one fold of the batch."""
        class Count(of_fold.View):
            name, version, names = "count_test", "1", ("of.agent_question_answered",)

            def fold(self, rows, events):
                n = (rows.get("n") or {"n": 0})["n"] + len(events)
                return {"n": {"n": n}}
        view = Count()
        for i in range(3):
            self.answer(f"A{i}", "approve")
        batch = of_listen.fetch(self.conn, 0)
        p = of_fold.FoldProcessor(view)
        of_listen.prepare(self.conn, [p])
        p.handle(self.conn, batch)
        again = of_fold.FoldProcessor(view)                                   # a restart; the listener's cursor lags
        again.handle(self.conn, batch)
        self.assertEqual(of_fold.load_rows(self.conn, view), {"n": {"n": 3}})
        self.answer("B", "approve")
        more = of_listen.fetch(self.conn, batch[-1]["rowid"])
        real = of_fold.persist
        calls = []

        def failing(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                raise __import__("sqlite3").OperationalError("database is locked")
            return real(*a, **k)
        of_fold.persist = failing
        try:
            with self.assertRaises(Exception):
                again.handle(self.conn, more)
            again.handle(self.conn, more)                                     # handled again next pass
        finally:
            of_fold.persist = real
        self.assertEqual(of_fold.load_rows(self.conn, view), {"n": {"n": 4}})
        self.assertEqual(again.rows, {"n": {"n": 4}})

    def test_every_registered_view_is_pure(self):
        """of_architect on PR 154: purity as a test over every view, not a convention."""
        import json
        for name, view in of_fold.views().items():
            events = [{"rowid": i + 1, "event_id": f"e{i}", "ts": f"2026-11-14T00:00:0{i}Z", "name": n,
                       "session_id": "S", "position": "p", "source": "hook", "org": "o",
                       "payload": {"ask_id": "A", "ask_ids": ["A"], "by": "owner", "via": "asks-ui", "answer": "y"}}
                      for i, n in enumerate(view.names * 2)]
            rows = {}
            rows.update({k: v for k, v in view.fold({}, events[:1]).items() if v is not None})
            before = json.dumps(rows, sort_keys=True, default=str)
            view.fold(rows, events[1:])
            self.assertEqual(json.dumps(rows, sort_keys=True, default=str), before, name)

    def test_a_new_view_builds_from_its_own_names(self):
        """of_review on PR 154: a new fold view built through the generic catch-up read every event of the log."""
        for i in range(3):
            self.answer(f"A{i}", "approve")
        of_events.emit("turn_end", {"x": 1})                                  # not the view's
        p = of_fold.FoldProcessor(self.view)
        cur = of_listen.prepare(self.conn, [p])
        fed = []
        real = p.handle
        p.handle = lambda conn, events, upto=None: fed.extend(events) or real(conn, events, upto)
        of_listen.step(self.conn, [p], cur)
        self.assertEqual({e["name"] for e in fed}, {"of.agent_question_answered"})
        top = self.conn.execute("SELECT max(rowid) FROM event").fetchone()[0]
        self.assertEqual(of_fold.meta(self.conn, self.view)["cursor"], top)  # its cursor at the end all the same
        self.assertEqual(of_fold.age(self.view, conn=self.reader()), (0.0, True))

    def test_a_new_fold_view_keeps_the_listeners_heartbeat(self):
        """A fold view says its own readiness (read() is late until built): building one does not take the heartbeat
        from the views readers already trust."""
        self.conn.executescript(of_listen.DDL)
        self.conn.execute("INSERT OR REPLACE INTO listener_meta VALUES ('heartbeat', '1')")
        p = of_fold.FoldProcessor(self.view)
        of_listen.prepare(self.conn, [p])
        self.assertIsNotNone(self.conn.execute("SELECT 1 FROM listener_meta WHERE key = 'heartbeat'").fetchone())
        self.assertFalse(getattr(p, "in_place", False))


if __name__ == "__main__":
    unittest.main()
