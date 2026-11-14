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
