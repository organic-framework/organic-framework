"""The listener (of_listen): cursors, replay, the asks view equal to the projection."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_asks  # noqa: E402
import of_events  # noqa: E402
import of_listen  # noqa: E402
import of_store  # noqa: E402


class ListenTest(unittest.TestCase):
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

    def ask(self, q, position="a", to="founder", ts=None):
        rec = of_events.record("agent_question", {"detected_by": "declared", "to": to, "questions": [{"question": q}]},
                               position=position, session=f"S-{position}", org="ofb")
        if ts:
            rec["timestamp"] = ts
        of_store.add_event(self.conn, rec)
        return rec["of.event.id"]

    def test_a_long_tick_holds_neither_the_views_nor_the_heartbeat(self):
        """of_asks msg 01a1d67e-8752, 2026-11-13: ticks of 442 s, 132 s and 71 s held the loop, the heartbeat went
        stale, and every reader projected the whole log. Ticks run in their own thread; the ticking processor alone
        waits, and catches up after."""
        import threading
        import time as _t
        release, started = threading.Event(), threading.Event()

        class Slow(of_listen.Processor):
            name, version, tick_s = "slow", "1", 0.01

            def __init__(self):
                self.seen = 0

            def handle(self, conn, rows):
                self.seen += len(rows)
                return False

            def tick(self, conn, now):
                started.set()
                release.wait(10)
                return True
        asks, slow_p = of_listen.AsksProcessor(), Slow()
        procs = [asks, slow_p]
        cur = of_listen.prepare(self.conn, procs)
        locks = {p.name: threading.Lock() for p in procs}
        ticks = {p.name: 0.0 for p in procs}
        changed, stop = threading.Event(), threading.Event()
        path = os.path.join(self.tmp.name, "of.db")
        of_listen.tick_thread(procs, ticks, locks, changed, stop=stop, log=lambda m: None,
                              connect=lambda: of_store.connect(path))
        try:
            self.assertTrue(started.wait(5))
            a1 = self.ask("Ship it?")
            t0 = _t.monotonic()
            self.assertTrue(of_listen.step(self.conn, procs, cur, locks=locks))  # the asks view moves on
            self.assertLess(_t.monotonic() - t0, 2)
            self.assertIn(a1, {a["event_id"] for a in of_listen.read_asks(self.conn)})
            self.assertEqual(slow_p.seen, 0)                                     # its tick holds it: skipped
            release.set()
            for _ in range(50):
                if changed.is_set():
                    break
                _t.sleep(0.1)
            self.assertTrue(changed.is_set())
            with locks["slow"]:
                of_listen.step(self.conn, [slow_p], cur)
            self.assertEqual(slow_p.seen, 1)                                     # caught up from its cursor
        finally:
            stop.set()
            release.set()

    def test_a_long_pass_that_works_keeps_the_watchdogs_file_and_a_stuck_one_does_not(self):
        """2026-11-17 17:52Z: a flush of the asks view ran past the watchdog's 180 s at load 65 and the listener was
        restarted, then read the whole asks log again for 17 minutes."""
        import time as _t

        class F:
            def __init__(self, line):
                self.f_code, self.f_lineno, self.f_back = type("C", (), {"co_filename": "x.py"}), line, None
        line = [1]
        path = of_listen.heartbeat_path()
        if os.path.exists(path):
            os.remove(path)
        done = of_listen.working_beat(thread_id=7, every=0.02, frames=lambda: {7: F(line[0])})
        try:
            _t.sleep(0.15)
            self.assertFalse(os.path.exists(path))          # one place: stuck, never touched
            for i in range(10):
                line[0] = i + 2
                _t.sleep(0.02)
            self.assertTrue(os.path.exists(path))           # the place moves: working
        finally:
            done.set()

    def test_the_spool_drains_during_a_start(self):
        """of_asks, message 01a1eaeb-1f42: spool drains up to an hour old came after each long start, when only the
        main loop drained the spool."""
        import time as _t
        drained = []
        saved = of_listen.SPOOL_S
        of_listen.SPOOL_S = 0.01
        done = of_listen.starting_beat(every=0.02, cpu=lambda: 0.0, drain=lambda: drained.append(1))
        try:
            _t.sleep(0.2)
            self.assertGreaterEqual(len(drained), 3)
        finally:
            done.set()
            of_listen.SPOOL_S = saved

    def test_one_stuck_timer_stops_the_timers_file(self):
        """of_review on organic-framework PR 294: with a thread per timer, the others kept the timers' file fresh; the
        listener now computes its stuck timers and touches the file only while none is stuck (the watchdog stays
        stat-only)."""
        class T(of_listen.Processor):
            def __init__(self, name, every):
                self.name, self.tick_s = name, every
        procs = [T("pulse", 300), T("paste", 20), T("archive", 3600), of_listen.Processor()]
        ticks = {"pulse": 1000.0, "paste": 100.0, "archive": 0.0}
        self.assertEqual(of_listen.stuck_timers(ticks, procs, 1300.0, 900), ["paste"])   # 1,200 s > 20 + 900
        ticks["paste"] = 1290.0
        self.assertEqual(of_listen.stuck_timers(ticks, procs, 1300.0, 900), [])

    def test_a_hung_timer_holds_no_other_timer(self):
        """2026-11-17: the paste tick searched its sources for over an hour at load 90, and the pulse, which types the
        notes, never turned: one thread per timer."""
        import threading
        import time as _t
        release, turned = threading.Event(), []

        class Hung(of_listen.Processor):
            name, version, tick_s = "hung", "1", 0.01

            def tick(self, conn, now):
                release.wait(10)

        class Pulse(of_listen.Processor):
            name, version, tick_s = "pulse", "1", 0.01

            def tick(self, conn, now):
                turned.append(now)
        procs = [Hung(), Pulse()]
        locks = {p.name: threading.Lock() for p in procs}
        ticks = {p.name: 0.0 for p in procs}
        stop = threading.Event()
        path = os.path.join(self.tmp.name, "of.db")
        threads = of_listen.tick_thread(procs, ticks, locks, threading.Event(), stop=stop, log=lambda m: None,
                                        connect=lambda: of_store.connect(path))
        try:
            for _ in range(50):
                if len(turned) >= 3:
                    break
                _t.sleep(0.1)
            self.assertGreaterEqual(len(turned), 3)           # the pulse turns while the other timer hangs
        finally:
            stop.set()
            release.set()
            for t in threads:   # none writes into the next test's state folder
                t.join(5)

    def test_current_asks_reads_the_view_and_folds_its_tail_never_the_whole_log(self):
        """of_platform's log-readers design (organic-framework-build reports/of-log-readers-2026-11-14.md, 3.2-3.3):
        the asks view's read returns the asks with the cursor they reflect, their age and a late flag; it folds at most
        `tail` events after the view with the view's own fold, and past that returns the view as it is, late."""
        def states(asks):
            return {a["event_id"]: (a["state"], a.get("claimed_at")) for a in asks}

        def full():
            return states(of_asks.project(of_asks.load(self.conn)))
        self.assertEqual(of_asks.current_asks(self.conn), {"asks": [], "cursor": 0, "age_s": None, "late": True})
        procs = [of_listen.AsksProcessor()]
        procs[0].MIN_FLUSH_S = 0
        cur = of_listen.prepare(self.conn, procs)
        a1 = self.ask("Merge PR 2349 now?", ts="2026-11-13T10:00:00.000Z")
        a2 = self.ask("Rotate the etcd keys?", position="b", ts="2026-11-13T10:01:00.000Z")
        of_listen.step(self.conn, procs, cur)
        got = of_asks.current_asks(self.conn)
        self.assertEqual((states(got["asks"]), got["late"]), (full(), False))
        self.assertEqual(got["asks"][0]["to"], "founder")       # the whole ask, as the projection made it
        # after the view's last write: an answer and a new ask, folded from the log
        of_events.emit("agent_question_answered", {"ask_id": a1, "ask_ids": [a1], "by": "owner"})
        a3 = self.ask("Pay the invoice?", position="c")
        got = of_asks.current_asks(self.conn)
        self.assertEqual((states(got["asks"]), got["late"]), (full(), False))
        self.assertEqual(got["cursor"], self.conn.execute("SELECT max(rowid) FROM event").fetchone()[0])
        self.assertIn(a3, states(got["asks"]))
        late = of_asks.current_asks(self.conn, tail=1)           # more behind than it may fold: the view as it is
        self.assertTrue(late["late"])
        self.assertEqual(states(late["asks"])[a1][0], "open")
        of_listen.step(self.conn, procs, cur)
        of_events.emit("prompt_origin", {"event_id": "E-any", "origin": "scheduled"})
        self.assertTrue(of_asks.current_asks(self.conn)["late"])  # a correction of an earlier prompt: never folded
        of_listen.step(self.conn, procs, cur)
        got = of_asks.current_asks(self.conn)
        self.assertEqual((states(got["asks"]), got["late"]), (full(), False))
        self.assertEqual(states(got["asks"])[a2][0], "open")

    def test_a_rebuild_of_the_asks_view_reads_only_its_names(self):
        """of_asks's PR 181 bumps the asks view's version: a rebuild from rowid 0 through the generic catch-up read
        every event of the log (the founder's read-path rule, 2026-11-14)."""
        a1 = self.ask("Merge PR 2349 now?")
        for i in range(5):
            of_events.emit("tool_use", {"tool": "Bash", "i": i})                 # not the view's
        p = of_listen.AsksProcessor()
        p.MIN_FLUSH_S = 0
        fed = []
        real = p.handle
        p.handle = lambda conn, rows: fed.extend(rows) or real(conn, rows)
        cur = of_listen.prepare(self.conn, [p])
        of_listen.step(self.conn, [p], cur)
        self.assertEqual({r["name"] for r in fed}, {"of.agent_question"})
        top = self.conn.execute("SELECT max(rowid) FROM event").fetchone()[0]
        self.assertEqual(cur[p.name], top)                                       # its cursor at the end all the same
        got = of_asks.current_asks(self.conn)
        self.assertEqual((got["late"], [a["event_id"] for a in got["asks"]]), (False, [a1]))
        self.assertEqual(got["cursor"], top)                                     # nothing left for a reader to fold

    def test_an_in_place_replay_beats_the_watchdogs_file_and_not_the_readers_heartbeat(self):
        """of_review on PR 149: while a view is rebuilt in place, listen.heartbeat (which the watchdog reads) must keep
        beating so a long replay is never restarted, and listener_meta's heartbeat (which readers trust) must not."""
        class InPlace(of_listen.Processor):
            name, version = "in_place_view", "1"

            def handle(self, conn, rows):
                return False

            def reset(self, conn):
                pass
        self.conn.executescript(of_listen.DDL)
        self.conn.execute("INSERT OR REPLACE INTO listener_meta VALUES ('heartbeat', '1')")
        p = InPlace()
        of_listen.prepare(self.conn, [p])
        self.assertTrue(p.in_place)
        meta = lambda: self.conn.execute("SELECT 1 FROM listener_meta WHERE key = 'heartbeat'").fetchone()  # noqa: E731
        self.assertIsNone(meta())                                               # prepare() dropped it
        path = of_listen.heartbeat_path()
        t = [1000.0]
        beat = of_listen.make_beat(self.conn, [p], clock=lambda: t[0])
        beat()
        self.assertTrue(os.path.exists(path))                                   # the watchdog's file beats
        self.assertIsNone(meta())                                               # readers' heartbeat stays down
        os.utime(path, (1, 1))
        t[0] += of_listen.HEARTBEAT_S - 1
        beat()
        self.assertEqual(os.path.getmtime(path), 1)                             # at most every HEARTBEAT_S
        t[0] += 1
        beat()
        self.assertGreater(os.path.getmtime(path), 1)
        self.assertIsNone(meta())
        whole = of_listen.make_beat(self.conn, [of_listen.AsksProcessor()], clock=lambda: t[0])
        whole()                                                                  # every view whole: the full beat
        self.assertIsNotNone(meta())

    def test_current_asks_reads_one_snapshot_even_when_the_listener_writes_between_its_reads(self):
        """of_review on PR 133: the fold mark and the asks were read in two transactions; a listener write between
        them left the mark one write behind and the tail was folded twice, an ask coming back twice."""
        import sqlite3
        procs = [of_listen.AsksProcessor()]
        procs[0].MIN_FLUSH_S = 0
        cur = of_listen.prepare(self.conn, procs)
        self.ask("Merge PR 2349 now?", ts="2026-11-13T10:00:00.000Z")
        of_listen.step(self.conn, procs, cur)
        self.ask("Rotate the etcd keys?", position="b", ts="2026-11-13T10:01:00.000Z")
        rd = sqlite3.connect(os.path.join(self.tmp.name, "of.db"), isolation_level=None)
        fired = []

        def between(sql):  # the listener writes as the reader starts on the asks, after the mark
            if sql.lstrip().startswith("SELECT ask FROM view_asks") and not fired:
                fired.append(True)
                of_listen.step(self.conn, procs, cur)
        rd.set_trace_callback(between)
        try:
            got = of_asks.current_asks(rd)
        finally:
            rd.close()
        self.assertTrue(fired)
        ids = [a["event_id"] for a in got["asks"]]
        self.assertEqual(len(ids), len(set(ids)))  # each ask once
        self.assertEqual(len(ids), 2)

    def test_current_asks_age_a_declared_asks_key_and_no_writer_in_place_of_a_reader(self):
        """of_platform's notes on PR 133: a quiet view is current (age 0) and a late one ages from its oldest unapplied
        event; state_from finds declared asks by topic_key; a store that will not open read-only gives the empty,
        late answer, never a writing connection."""
        import of_store
        procs = [of_listen.AsksProcessor()]
        procs[0].MIN_FLUSH_S = 0
        cur = of_listen.prepare(self.conn, procs)
        self.ask("Merge PR 2349 now?", ts="2026-11-13T10:00:00.000Z")
        rec = of_events.record("agent_question", {"detected_by": "declared", "to": "founder", "task": True,
                                                  "steps": "1. Open the page.", "questions": [{"question": "Log in"}]},
                               position="t", session="S-t", org="ofb")
        of_store.add_event(self.conn, rec)
        of_listen.step(self.conn, procs, cur)
        self.assertEqual(of_asks.current_asks(self.conn)["age_s"], 0.0)
        self.assertTrue(all("topic_key" in a for a in of_asks.current_asks(self.conn)["asks"]))  # declared and task
        self.ask("Rotate the etcd keys?", position="b", ts="2026-11-13T10:01:00.000Z")
        now = of_asks.seconds_since("2026-11-13T10:01:00.000Z", 0) * -1 + 600  # ten minutes after it
        self.assertAlmostEqual(of_asks.current_asks(self.conn, now=now)["age_s"], 600, delta=1)
        saved = of_store.db_path
        of_store.db_path = lambda: os.path.join(self.tmp.name, "no", "such", "of.db")
        try:
            self.assertIsNone(of_asks.reader())
            self.assertEqual(of_asks.current_asks(), {"asks": [], "cursor": 0, "age_s": None, "late": True})
        finally:
            of_store.db_path = saved

    def test_a_view_from_before_whole_asks_is_rebuilt(self):
        self.conn.executescript("CREATE TABLE view_asks (event_id TEXT PRIMARY KEY, ts TEXT, org TEXT, "
                                "session_id TEXT, position TEXT, kind TEXT, text TEXT, state TEXT, closed_by TEXT, "
                                "closed_at TEXT, closed_by_kind TEXT, to_ TEXT, delegation TEXT, labels TEXT, "
                                "view_version TEXT);")
        procs = [of_listen.AsksProcessor()]
        cur = of_listen.prepare(self.conn, procs)
        self.ask("Merge PR 2349 now?")
        of_listen.step(self.conn, procs, cur)
        self.assertEqual(len(of_asks.current_asks(self.conn)["asks"]), 1)

    def test_a_flush_projects_only_the_new_rows_and_rebuilds_when_it_must(self):
        """2026-11-13: re-projecting every row took 179 s a flush at load ~50. The carried projection gives the same
        asks; a correction of a prompt's origin, an older row or an event again under its id projects all rows."""
        procs = [of_listen.AsksProcessor()]
        procs[0].MIN_FLUSH_S = 0
        cur = of_listen.prepare(self.conn, procs)

        def same():
            want = {a["event_id"]: a["state"] for a in of_asks.project(of_asks.load(self.conn))}
            self.assertEqual({a["event_id"]: a["state"] for a in of_listen.read_asks(self.conn)}, want)
        a1 = self.ask("Merge PR 2349 now?", ts="2026-11-13T10:00:00.000Z")
        self.assertTrue(of_listen.step(self.conn, procs, cur))
        carried = procs[0].state
        a2 = self.ask("Rotate the etcd keys?", position="b", ts="2026-11-13T10:05:00.000Z")
        of_events.emit("agent_question_answered", {"ask_id": a1, "ask_ids": [a1], "by": "owner"})
        self.assertTrue(of_listen.step(self.conn, procs, cur))
        self.assertIs(procs[0].state, carried)               # only the new rows, on the same projection
        same()
        self.ask("An old one from imported history?", position="c", ts="2026-11-01T09:00:00.000Z")
        of_listen.step(self.conn, procs, cur)
        self.assertIsNot(procs[0].state, carried)            # older than what was fed: all rows again
        same()
        carried = procs[0].state
        of_events.emit("prompt_origin", {"event_id": "E-any", "origin": "scheduled"})
        of_events.emit("agent_question_answered", {"ask_id": a2, "ask_ids": [a2], "by": "owner"})
        of_listen.step(self.conn, procs, cur)
        self.assertIsNot(procs[0].state, carried)            # a correction: all rows again
        same()

    def test_a_row_late_within_the_hold_folds_in_order_and_one_later_projects_all_rows_counted(self):
        """of_platform, message 01a1eaab-8fbd: every row was projected again in 434 flush windows a day, nearly all for
        hook events a minute or two older than the newest fed; a 600 s hold leaves 4.5 a day (AsksProcessor.HOLD_S)."""
        import contextlib
        import io
        import time as _t

        def at(s):
            return _t.strftime("%Y-%m-%dT%H:%M:%S.000Z", _t.gmtime(_t.time() - s))

        def same():
            want = {a["event_id"]: a["state"] for a in of_asks.project(of_asks.load(self.conn))}
            self.assertEqual({a["event_id"]: a["state"] for a in of_listen.read_asks(self.conn)}, want)
        procs = [of_listen.AsksProcessor()]
        procs[0].MIN_FLUSH_S = 0
        cur = of_listen.prepare(self.conn, procs)
        a1 = self.ask("Merge PR 2349 now?", ts=at(1200))
        self.ask("Rotate the etcd keys?", position="b", ts=at(60))
        of_listen.step(self.conn, procs, cur)
        carried = procs[0].state
        answer = of_events.record("agent_question_answered", {"ask_id": a1, "ask_ids": [a1], "by": "owner"},
                                  position="a", session="S-a", org="ofb")
        answer["timestamp"] = at(300)  # 240 s older than the newest row fed: a hook's event, late
        of_store.add_event(self.conn, answer)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            of_listen.step(self.conn, procs, cur)
        self.assertIs(procs[0].state, carried)               # folded in order, not every row again
        self.assertEqual((procs[0].late, out.getvalue()), (0, ""))
        same()                                               # the view has it at once
        self.assertEqual(procs[0].state["by_id"][a1]["state"], "open")  # held: only the copy for the view has it
        self.assertEqual(of_asks.current_asks(self.conn)["asks"][0]["state"], "answered")  # readers see it too
        # a row older than the hold's mark (a spool drained after a long busy store): every row again, and counted
        old = self.ask("Drain node 3?", position="c", ts=at(3000))
        with contextlib.redirect_stdout(out):
            of_listen.step(self.conn, procs, cur)
        self.assertIsNot(procs[0].state, carried)
        self.assertEqual(procs[0].late, 1)
        self.assertIn("older than the hold's mark", out.getvalue())
        same()
        self.assertIn(old, {a["event_id"] for a in of_listen.read_asks(self.conn)})

    def test_a_start_carries_on_from_the_view_and_its_checkpoint_never_reading_the_whole_log(self):
        """The founder, 2026-11-17 (prompt 01a1eae5-4583, via of_conductor), translated: "all these questions must be
        lightweight and not wait for a start for minutes": a start read every relevant row of the log, 16 to 17
        minutes at load 60. It now carries on from the view and the carried checkpoint beside it."""
        import time as _t

        def at(s):
            return _t.strftime("%Y-%m-%dT%H:%M:%S.000Z", _t.gmtime(_t.time() - s))

        def answer(aid, s):
            rec = of_events.record("agent_question_answered", {"ask_id": aid, "ask_ids": [aid], "by": "owner"},
                                   position="a", session="S-a", org="ofb")
            rec["timestamp"] = at(s)
            of_store.add_event(self.conn, rec)

        def same():
            want = {a["event_id"]: a["state"] for a in of_asks.project(of_asks.load(self.conn))}
            self.assertEqual({a["event_id"]: a["state"] for a in of_listen.read_asks(self.conn)}, want)
            return want
        procs = [of_listen.AsksProcessor()]
        procs[0].MIN_FLUSH_S = 0
        cur = of_listen.prepare(self.conn, procs)
        a1 = self.ask("Merge PR 2349 now?", ts=at(1200))
        a2 = self.ask("Rotate the etcd keys?", position="b", ts=at(900))
        answer(a1, 700)
        a3 = self.ask("Open the gateway window?", position="c", ts=at(30))  # inside the hold
        answer(a2, 100)  # inside the hold too: the view has a2 answered, the carried state still open
        of_listen.step(self.conn, procs, cur)
        self.assertTrue(procs[0].held)
        cols = "event_id, org, state, closed_by_kind, labels, delegation"
        before = {r[0]: r for r in self.conn.execute(f"SELECT {cols} FROM view_asks")}
        # the last run handled one more row and stopped before writing it
        procs[0].MIN_FLUSH_S, procs[0].last_flush = 3600, _t.time()
        a4 = self.ask("Drain node 3?", position="d", ts=at(10))
        of_listen.step(self.conn, procs, cur)
        saved = of_asks.load

        def whole(*a, **k):
            raise AssertionError("a start read the whole log")
        of_asks.load = whole
        try:
            procs2 = [of_listen.AsksProcessor()]
            procs2[0].MIN_FLUSH_S = 0
            cur2 = of_listen.prepare(self.conn, procs2)
            of_listen.step(self.conn, procs2, cur2)
            # late, but inside the hold: its asker withdrew it before the answer came, so in order it is withdrawn
            # (the carried version from the checkpoint, not the view's answered one, takes it)
            w = of_events.record("ask_withdrawn", {"ask_id": a2, "by": "b"}, position="b", session="S-b", org="ofb")
            w["timestamp"] = at(300)
            of_store.add_event(self.conn, w)
            of_listen.step(self.conn, procs2, cur2)
            self.assertEqual(procs2[0].late, 0)
        finally:
            of_asks.load = saved
        want = same()
        self.assertEqual((want[a2], want[a3], want[a4]), ("withdrawn", "open", "open"))
        # the founder's queue is written whole at each write (view rows are only ever replaced): a3 and a4 are in it
        queue = {i for g in of_listen.read_groups(self.conn) for i in g["ask_ids"]}
        self.assertTrue({a3, a4} <= queue, queue)
        after = {r[0]: r for r in self.conn.execute(f"SELECT {cols} FROM view_asks")}
        self.assertEqual(after[a1], before[a1])  # its columns kept as written
        self.assertEqual(after[a2][1:4], ("ofb", "withdrawn", "of.ask_withdrawn"))
        # a row older than the hold's mark after such a start: every row is read, and the view is the same
        self.ask("An old one?", position="e", ts=at(5000))
        of_listen.step(self.conn, procs2, cur2)
        self.assertEqual(procs2[0].late, 1)
        same()
        self.assertEqual(self.conn.execute(f"SELECT {cols} FROM view_asks WHERE event_id = ?", (a1,)).fetchone(),
                         after[a1])

    def test_a_row_older_than_the_mark_is_projected_again_from_a_snapshot_not_from_every_row(self):
        """The founder, 2026-11-17 (prompt 01a1eae5-4583, via of_conductor): the questions must never wait minutes. A
        spool drained after a busy store brings rows up to an hour old; they redo the projection from the newest
        snapshot before them, with the rows since read by ts, not from every row of the log."""
        import contextlib
        import io
        import time as _t

        def at(s):
            return _t.strftime("%Y-%m-%dT%H:%M:%S.000Z", _t.gmtime(_t.time() - s))

        def same():
            want = {a["event_id"]: a["state"] for a in of_asks.project(of_asks.load(self.conn))}
            self.assertEqual({a["event_id"]: a["state"] for a in of_listen.read_asks(self.conn)}, want)
            return want
        procs = [of_listen.AsksProcessor()]
        procs[0].MIN_FLUSH_S, procs[0].SNAP_S = 0, 60
        cur = of_listen.prepare(self.conn, procs)
        a1 = self.ask("Merge PR 2349 now?", ts=at(6000))
        of_listen.step(self.conn, procs, cur)
        a2 = self.ask("Rotate the etcd keys?", position="b", ts=at(4000))
        of_listen.step(self.conn, procs, cur)
        self.ask("Open the gateway window?", position="c", ts=at(1000))
        of_listen.step(self.conn, procs, cur)
        self.assertEqual(len(procs[0].snaps), 3)  # at the marks of 6600, 4600 and 1600 s ago
        saved, out = of_asks.load, io.StringIO()

        def whole(*a, **k):
            raise AssertionError("every row read again")
        of_asks.load = whole
        try:
            # an answer an hour old, drained from a spool: older than the mark, newer than the oldest snapshots
            rec = of_events.record("agent_question_answered", {"ask_id": a2, "ask_ids": [a2], "by": "owner"},
                                   position="b", session="S-b", org="ofb")
            rec["timestamp"] = at(3000)
            of_store.add_event(self.conn, rec)
            with contextlib.redirect_stdout(out):
                of_listen.step(self.conn, procs, cur)
            self.assertEqual((procs[0].rewinds, procs[0].late), (1, 0))
            self.assertIn("projected again from the snapshot at", out.getvalue())
            # a correction of a prompt's origin, for a prompt inside the snapshots' reach
            p = of_events.record("human_prompt", {"origin": "human", "text": "yes"}, position="a", session="S-a",
                                 org="ofb", source="hook")
            p["timestamp"] = at(2000)
            of_store.add_event(self.conn, p)
            of_events.emit("prompt_origin", {"event_id": p["of.event.id"], "origin": "scheduled"})
            with contextlib.redirect_stdout(out):
                of_listen.step(self.conn, procs, cur)
            self.assertEqual((procs[0].rewinds, procs[0].late), (2, 0))
        finally:
            of_asks.load = saved
        want = same()
        self.assertEqual((want[a1], want[a2]), ("open", "answered"))
        # older than every snapshot (an import): every row, counted
        self.ask("From imported history?", position="d", ts=at(20000))
        with contextlib.redirect_stdout(out):
            of_listen.step(self.conn, procs, cur)
        self.assertEqual(procs[0].late, 1)
        self.assertIn("and every snapshot: every row projected again", out.getvalue())
        same()

    def test_a_redaction_of_an_ask_redoes_the_view_from_before_it(self):
        """of_server, msg 01a1fa1e-3ce2: redaction 7da5d129 rewrote three ask events in place, and the view kept their
        text until a rebuild. of_redact.run records of.redacted with table event and the rows' rowids; the asks
        processor redoes its projection from the earliest of them."""
        import contextlib
        import io
        import time as _t

        def at(s):
            return _t.strftime("%Y-%m-%dT%H:%M:%S.000Z", _t.gmtime(_t.time() - s))
        procs = [of_listen.AsksProcessor()]
        procs[0].MIN_FLUSH_S, procs[0].SNAP_S = 0, 60
        cur = of_listen.prepare(self.conn, procs)
        out = io.StringIO()

        def run():
            with contextlib.redirect_stdout(out):
                of_listen.step(self.conn, procs, cur)
        self.ask("Merge PR 2349 now?", ts=at(6000))
        run()
        secret = self.ask("Wire the matter nobody else knows about?", position="b", ts=at(4000))
        run()
        self.ask("Open the gateway window?", position="c", ts=at(1000))
        run()
        text = {a["event_id"]: a["text"] for a in of_listen.read_asks(self.conn)}
        self.assertIn("matter", text[secret] or "")
        (rowid,) = self.conn.execute("SELECT rowid FROM event WHERE event_id = ?", (secret,)).fetchone()
        self.conn.execute("UPDATE event SET payload = ? WHERE event_id = ?", (json.dumps({"of_redacted": "[x]"}), secret))
        of_store.add_event(self.conn, of_events.record("redacted", {"table": "event", "ids": [rowid]}))
        run()
        text = {a["event_id"]: a["text"] for a in of_listen.read_asks(self.conn)}
        self.assertNotIn("matter", text.get(secret) or "")
        self.assertIn("Merge PR 2349", " ".join(t or "" for t in text.values()))     # the others as they were

    def test_a_rewind_reads_no_snapshot_through_a_link_or_a_fifo(self):
        """of_secrets-review on PR 635: the rewind's read was a plain open, which follows a link and waits on a FIFO."""
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            fifo, link = os.path.join(t, "f.json"), os.path.join(t, "l.json")
            os.mkfifo(fifo)
            with open(os.path.join(t, "real.json"), "w") as fh:
                fh.write("{}")
            os.symlink(os.path.join(t, "real.json"), link)
            for path in (fifo, link):
                p = of_listen.AsksProcessor()
                p.snaps, p.rewind, p.max_rowid = [("2026-11-20T00:00:00.000Z", path)], "2026-11-21T00:00:00.000Z", 0
                self.assertFalse(p._rewind_to_snapshot(self.conn))        # refused at once, no wait

    def test_the_start_deletes_only_versions_in_this_users_own_folder(self):
        """of_review on PR 635: a link at asks-snapshots pointed the start's deletion at any folder the user owns, and
        any folder there that was no version was deleted too."""
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as t:
            state, elsewhere = os.path.join(t, "state"), os.path.join(t, "elsewhere")
            os.makedirs(os.path.join(elsewhere, "projects"))
            open(os.path.join(elsewhere, "projects", "f"), "w").close()
            os.makedirs(state)
            os.symlink(elsewhere, os.path.join(state, "asks-snapshots"))
            with mock.patch.object(of_events, "STATE_DIR", state):
                p = of_listen.AsksProcessor()
                p._load_snaps("2026-11-20T00:00:00.000Z")
                self.assertTrue(os.path.exists(os.path.join(elsewhere, "projects", "f")))   # the link's target kept
                os.unlink(os.path.join(state, "asks-snapshots"))
                base = os.path.join(state, "asks-snapshots")
                for v in ("28", "backup", p.version):
                    os.makedirs(os.path.join(base, v))
                p._load_snaps("2026-11-20T00:00:00.000Z")
                self.assertEqual(sorted(os.listdir(base)), sorted(["backup", p.version]))   # only another version

    def test_another_versions_snapshots_are_deleted(self):
        """of_server on redaction 7da5d129 (msg 01a1fa0b-b0b3): the folders of versions 28 and 29 stayed, cut from
        events since rewritten, and no version deleted them."""
        p = of_listen.AsksProcessor()
        base = os.path.dirname(p.snap_dir())
        for v in ("28", "29", p.version):
            os.makedirs(os.path.join(base, v), exist_ok=True)
            with open(os.path.join(base, v, "2026-11-20T00:00:00.000Z.json"), "w") as fh:
                fh.write("{}")
        p._load_snaps("")
        self.assertEqual(sorted(os.listdir(base)), [p.version])

    def test_the_snapshots_outlive_a_restart_and_never_the_history_they_were_cut_from(self):
        """of_review on PR 316: in memory, the snapshots died with a restart, and the first spool drain after it (2026-
        11-17's pattern: a restart, then rows an hour old) read the whole log. They are files now, taken up by a start
        carried on from the checkpoint, and deleted when the history before them changes."""
        import contextlib
        import io
        import time as _t

        def at(s):
            return _t.strftime("%Y-%m-%dT%H:%M:%S.000Z", _t.gmtime(_t.time() - s))

        def same():
            want = {a["event_id"]: a["state"] for a in of_asks.project(of_asks.load(self.conn))}
            self.assertEqual({a["event_id"]: a["state"] for a in of_listen.read_asks(self.conn)}, want)
            return want

        def run(procs, cur):
            with contextlib.redirect_stdout(out):
                of_listen.step(self.conn, procs, cur)
        out = io.StringIO()
        procs = [of_listen.AsksProcessor()]
        procs[0].MIN_FLUSH_S, procs[0].SNAP_S = 0, 60
        cur = of_listen.prepare(self.conn, procs)
        self.ask("Merge PR 2349 now?", ts=at(6000))
        run(procs, cur)
        a2 = self.ask("Rotate the etcd keys?", position="b", ts=at(4000))
        run(procs, cur)
        self.ask("Open the gateway window?", position="c", ts=at(1000))
        run(procs, cur)
        files = sorted(os.listdir(procs[0].snap_dir()))
        self.assertEqual(len(files), 3)

        def a2_in(name):
            with open(os.path.join(procs[0].snap_dir(), name)) as fh:
                return next(a["state"] for a in json.load(fh)["asks"] if a["event_id"] == a2)
        self.assertEqual(a2_in(files[-1]), "open")
        self.assertIn("MB on disk", out.getvalue())  # each snapshot says its size (of_platform on PR 316)
        procs2 = [of_listen.AsksProcessor()]  # a restart, carried on from the checkpoint
        procs2[0].MIN_FLUSH_S, procs2[0].SNAP_S = 0, 60
        cur2 = of_listen.prepare(self.conn, procs2)
        self.assertEqual([os.path.basename(p) for _, p in procs2[0].snaps], files)
        saved = of_asks.load

        def whole(*a, **k):
            raise AssertionError("every row read again after the restart")
        of_asks.load = whole
        try:
            rec = of_events.record("agent_question_answered", {"ask_id": a2, "ask_ids": [a2], "by": "owner"},
                                   position="b", session="S-b", org="ofb")
            rec["timestamp"] = at(3000)  # a spool drained after the restart: older than the mark
            of_store.add_event(self.conn, rec)
            run(procs2, cur2)
            self.assertEqual((procs2[0].rewinds, procs2[0].late), (1, 0))
        finally:
            of_asks.load = saved
        self.assertEqual(same()[a2], "answered")
        # the snapshot after the one it redid from was cut from the history that answer changed: it was deleted, and
        # the one taken again at that mark holds the answer
        self.assertEqual(a2_in(files[-1]), "answered")
        # older than every snapshot: every row; the old files are deleted, and the one snapshot the write takes again
        # holds the new history
        old = self.ask("From imported history?", position="d", ts=at(20000))
        run(procs2, cur2)
        self.assertEqual(procs2[0].late, 1)
        (left,) = os.listdir(procs2[0].snap_dir())
        with open(os.path.join(procs2[0].snap_dir(), left)) as fh:
            self.assertIn(old, {a["event_id"] for a in json.load(fh)["asks"]})
        same()

    def test_view_matches_projection_incrementally_and_after_replay(self):
        procs = [of_listen.AsksProcessor()]
        cur = of_listen.prepare(self.conn, procs)
        a1 = self.ask("Merge PR 2349 now?")
        self.ask("Rotate the etcd keys?", position="b", to="acme_platform-owner")
        self.assertTrue(of_listen.step(self.conn, procs, cur))
        self.assertEqual([g["ask_ids"] for g in of_listen.read_groups(self.conn)], [[a1]])  # routed ask not in the queue
        of_events.emit("agent_question_answered", {"ask_id": a1, "ask_ids": [a1], "by": "owner"})
        self.assertTrue(of_listen.step(self.conn, procs, cur))
        self.assertEqual(of_listen.read_groups(self.conn), [])
        self.assertFalse(of_listen.step(self.conn, procs, cur))  # nothing new: no change, no bump
        want = {a["event_id"]: a["state"] for a in of_asks.project(of_asks.load(self.conn))}
        self.assertEqual({a["event_id"]: a["state"] for a in of_listen.read_asks(self.conn)}, want)
        org, ver = self.conn.execute("SELECT org, view_version FROM view_asks LIMIT 1").fetchone()
        self.assertEqual((org, ver), ("ofb", of_listen.AsksProcessor.version))
        # a restart continues from the cursor; a rebuild replays from zero to the same view
        procs2 = [of_listen.AsksProcessor()]
        cur2 = of_listen.prepare(self.conn, procs2)
        self.assertFalse(of_listen.step(self.conn, procs2, cur2))
        procs3 = [of_listen.AsksProcessor()]
        before = of_listen.read_asks(self.conn)
        cur3 = of_listen.prepare(self.conn, procs3, rebuild=True)
        self.assertEqual(cur3["asks"], 0)
        self.assertEqual(of_listen.read_asks(self.conn), before)  # the old view serves during the rebuild
        of_listen.step(self.conn, procs3, cur3)
        self.assertIsNone(self.conn.execute("SELECT name FROM sqlite_master WHERE name LIKE '%__new'").fetchone())
        self.assertEqual({a["event_id"]: a["state"] for a in of_listen.read_asks(self.conn)}, want)

    def test_a_step_projects_once_and_leaves_what_arrives_meanwhile_to_the_next(self):
        # 2026-11-09: a projection per batch, 5-8 s each, kept the loop in one step for 40 s while events arrived
        procs = [of_listen.AsksProcessor()]
        procs[0].MIN_FLUSH_S = 0  # the interval is the next test's
        cur = of_listen.prepare(self.conn, procs)
        of_listen.step(self.conn, procs, cur)
        writes = []
        saved_write, saved_batch = procs[0].write, of_listen.BATCH
        late = []

        def write(conn):
            writes.append(1)
            if not late:  # an event lands while the projection runs
                late.append(self.ask("Late question?", position="c"))
            saved_write(conn)
        procs[0].write = write
        of_listen.BATCH = 1
        try:
            asked = [self.ask(f"Question {i}?") for i in range(3)]
            self.assertTrue(of_listen.step(self.conn, procs, cur))
            self.assertEqual(len(writes), 1)  # three batches, one projection
            self.assertEqual({a["event_id"] for a in of_listen.read_asks(self.conn)}, set(asked))
            self.assertTrue(of_listen.step(self.conn, procs, cur))  # the late one: the next step's
            self.assertEqual(len(writes), 2)
            self.assertIn(late[0], {a["event_id"] for a in of_listen.read_asks(self.conn)})
        finally:
            of_listen.BATCH = saved_batch

    def test_a_processor_past_its_budget_yields_to_the_next_and_goes_on_next_step(self):
        """organic-framework-build reports/of-log-readers-2026-11-14.md, 3.4: a catch-up past its processor's budget
        commits what it folded, saves its cursor and yields; the first catch-up (no budget) runs to the end."""
        class Count(of_listen.Processor):
            version = "1"

            def __init__(self, name, budget):
                self.name, self.budget_s, self.seen = name, budget, []

            def handle(self, conn, rows):
                self.seen += [r["rowid"] for r in rows]
                return True
        busy, other = Count("busy", 0), Count("other", None)
        procs = [busy, other]
        cur = of_listen.prepare(self.conn, procs)
        saved = of_listen.BATCH
        of_listen.BATCH = 1
        try:
            for i in range(3):
                of_events.emit("tool_use", {"i": i})
            self.assertTrue(of_listen.step(self.conn, procs, cur, budget=True))
            self.assertEqual((len(busy.seen), len(other.seen)), (1, 3))       # one batch, then the next processor
            stored = self.conn.execute("SELECT last_rowid FROM listener_cursor WHERE processor = 'busy'").fetchone()[0]
            self.assertEqual(stored, busy.seen[-1])                           # its cursor saved
            of_listen.step(self.conn, procs, cur, budget=True)
            self.assertEqual(len(busy.seen), 2)                               # goes on from it
            of_listen.step(self.conn, procs, cur)                             # no budget: to the end
            self.assertEqual(busy.seen, other.seen)
        finally:
            of_listen.BATCH = saved

    def test_the_asks_shadow_is_swapped_in_only_once_caught_up_under_a_budget(self):
        a = [self.ask(f"Question {i}?") for i in range(3)]
        p = of_listen.AsksProcessor()
        p.MIN_FLUSH_S, p.budget_s = 0, 0
        cur = of_listen.prepare(self.conn, [p])
        saved = of_listen.BATCH
        of_listen.BATCH = 1
        try:
            of_listen.step(self.conn, [p], cur, budget=True)
            self.assertTrue(p.defer)                                          # not caught up: not settled
            for _ in range(3):
                of_listen.step(self.conn, [p], cur, budget=True)
            self.assertFalse(p.defer)
            self.assertEqual({x["event_id"] for x in of_listen.read_asks(self.conn)}, set(a))
        finally:
            of_listen.BATCH = saved

    def test_a_working_start_keeps_the_watchdogs_file_and_a_waiting_one_does_not(self):
        """2026-11-17: at load 45 to 103 the start (the asks view's state from the log) passed the watchdog's 180 s,
        and each restart began it again. With the real CPU reading: the beat's own wake-ups never count (of_review on
        organic-framework PR 274: the whole process's CPU grew with them, so a hung start was never restarted)."""
        import time as _t
        path, ticks = of_listen.heartbeat_path(), of_listen.ticks_path()
        done = of_listen.starting_beat(every=0.02)
        try:
            _t.sleep(0.4)                                   # the start waits on nothing: twenty beats, no touch
            self.assertFalse(os.path.exists(path))
            self.assertFalse(os.path.exists(ticks))
            end = _t.process_time() + 0.3
            while _t.process_time() < end:                  # the start works
                sum(range(1000))
            _t.sleep(0.05)
            self.assertTrue(os.path.exists(path))
            self.assertTrue(os.path.exists(ticks))          # its timers' file too (they start after)
        finally:
            done.set()

    def test_the_view_is_projected_at_most_once_per_interval(self):
        # 2026-11-11: with ~40 agents the projection (5 to 57 s) ran almost back to back while Ghostty lagged
        procs = [of_listen.AsksProcessor()]
        cur = of_listen.prepare(self.conn, procs)
        of_listen.step(self.conn, procs, cur)
        first = self.ask("First?")
        self.assertTrue(of_listen.step(self.conn, procs, cur))
        second = self.ask("Second?")
        self.assertFalse(of_listen.step(self.conn, procs, cur))  # within the interval: kept dirty
        self.assertNotIn(second, {a["event_id"] for a in of_listen.read_asks(self.conn)})
        procs[0].last_flush -= procs[0].MIN_FLUSH_S
        self.assertTrue(of_listen.step(self.conn, procs, cur))  # after it: written
        self.assertTrue({first, second} <= {a["event_id"] for a in of_listen.read_asks(self.conn)})

    def test_rederived_events_replace_their_old_copies(self):
        procs = [of_listen.AsksProcessor()]
        cur = of_listen.prepare(self.conn, procs)
        a1 = self.ask("Merge PR 2349 now?")
        of_listen.step(self.conn, procs, cur)
        row = self.conn.execute("SELECT record FROM event WHERE event_id = ?", (a1,)).fetchone()[0]
        self.conn.execute("DELETE FROM event WHERE event_id = ?", (a1,))  # what a re-derive does
        import json
        of_store.add_event(self.conn, json.loads(row))
        of_listen.step(self.conn, procs, cur)
        self.assertEqual([a["event_id"] for a in of_listen.read_asks(self.conn)], [a1])

    def test_a_closure_carries_the_asks_org(self):
        """of_field-analyst: reconcile closures had org NULL, so a per-organization model kept the asks open."""
        a = self.ask("Drain node 3?")
        eid = of_events.emit("agent_question_answered", {"ask_id": a, "ask_ids": [a], "via": "reconcile"})
        w = of_events.emit("ask_withdrawn", {"ask_id": a, "by": "owner"}, org="other")
        orgs = dict(self.conn.execute("SELECT event_id, org FROM event WHERE event_id IN (?, ?)", (eid, w)).fetchall())
        self.assertEqual((orgs[eid], orgs[w]), ("ofb", "other"))  # filled from the ask; an explicit org stays

    def test_a_locked_store_never_kills_the_listener(self):
        """of_architect, 2026-11-06: 'database is locked' in heartbeat() crash-looped the listener."""
        import sqlite3
        quick = of_store.connect(os.path.join(self.tmp.name, "of.db"), timeout=0.1)
        quick.executescript(of_listen.DDL)
        self.ask("Drain node 3?")
        blocker = sqlite3.connect(os.path.join(self.tmp.name, "of.db"), isolation_level=None)
        blocker.execute("BEGIN IMMEDIATE")
        try:
            self.assertFalse(of_listen.heartbeat(quick))  # skipped, not raised
            procs = [of_listen.AsksProcessor()]
            with self.assertRaises(sqlite3.OperationalError):
                cur = of_listen.prepare(quick, procs)  # even preparing needs the lock: the caller retries
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()
        cur = of_listen.prepare(quick, procs)
        self.assertTrue(of_listen.step(quick, procs, cur))
        self.assertTrue(of_listen.heartbeat(quick))
        self.assertEqual(len(of_listen.read_asks(quick)), 1)
        quick.close()

    def test_fresh_and_bump(self):
        self.conn.executescript(of_listen.DDL)
        self.assertFalse(of_listen.fresh(self.conn))
        of_listen.heartbeat(self.conn)
        self.assertTrue(of_listen.fresh(self.conn))
        before = of_listen.seq()
        of_listen.bump()
        self.assertNotEqual(of_listen.seq(), before)

    def test_a_restart_is_not_fresh_until_the_views_caught_up(self):
        self.conn.executescript(of_listen.DDL)
        of_listen.heartbeat(self.conn)  # the previous run's beat, still within STALE_S
        seen = []

        class Probe(of_listen.Processor):
            name, version = "probe", "1"

            def reset(self, conn):
                seen.append(of_listen.fresh(conn))  # a reader looking during the replay

        import threading
        stop = threading.Event()
        stop.set()
        saved, saved_path = of_listen.PROCESSORS, of_store.db_path
        of_listen.PROCESSORS = [Probe]
        of_store.db_path = lambda: os.path.join(self.tmp.name, "of.db")
        try:
            of_listen.run(stop=stop, log=lambda m: None)
        finally:
            of_listen.PROCESSORS, of_store.db_path = saved, saved_path
        self.assertEqual(seen, [False])
        self.assertTrue(of_listen.fresh(self.conn))




class DeliverProcessorTest(ListenTest):
    """acme_org, 2026-11-09: the founder's answers given while the agent was busy waited for the pulse; the listener now
    starts the delivery when the receiver ends its turn."""

    def ev(self, kind, payload, position=None, ts=None, session=None):
        rec = of_events.record(kind, payload, position=position, session=session, org="ofb")
        if ts:
            rec["timestamp"] = ts
        of_store.add_event(self.conn, rec)
        return rec["of.event.id"]

    def test_a_turn_end_starts_the_delivery_once_and_only_while_unrecorded(self):
        import time
        self.ev("turn_end", {"last_message": "history"}, "a")  # before the processor: its cursor starts after it
        p = of_listen.DeliverProcessor()
        started = []
        p.spawn = started.append
        cur = of_listen.prepare(self.conn, [p])
        self.assertEqual(cur["deliver"], self.conn.execute("SELECT max(rowid) FROM event").fetchone()[0])  # from now
        aid = self.ask("Window on Tuesday?", position="a")
        self.ev("agent_question_answered", {"ask_id": aid, "ask_ids": [aid], "answer": "да", "via": "asks-ui",
                                            "by": "owner"})
        self.ev("turn_end", {"last_message": "busy turn"}, "b")
        of_listen.step(self.conn, [p], cur)
        self.assertEqual(started, [])  # b has nothing due
        self.ev("turn_end", {"last_message": "done"}, "a")
        self.ev("turn_end", {"last_message": "again"}, "a")
        of_listen.step(self.conn, [p], cur)
        self.assertEqual(started, ["a"])  # one child within GAP_S
        p.started["a"] = time.time() - 60
        self.ev("human_prompt", {"origin": "human", "channel": "asks-ui",
                                 "text": "Answer from the founder to your question " + aid[-8:] + ": да"}, "a")
        self.ev("turn_end", {"last_message": "got it"}, "a")
        of_listen.step(self.conn, [p], cur)
        self.assertEqual(started, ["a"])  # recorded in its pane: nothing to start
        self.assertNotIn("a", p.due)

    def test_a_restart_reads_the_last_day_and_a_routed_ask_closed_is_dropped(self):
        aid = self.ask("Restart the bot?", position="u", to="owner")
        p = of_listen.DeliverProcessor()
        started = []
        p.spawn = started.append
        cur = of_listen.prepare(self.conn, [p])
        self.assertEqual([d["ask"] for d in p.due["owner"]], [aid])  # read back on reset, nothing typed from here
        self.conn.execute("CREATE TABLE view_asks (event_id TEXT PRIMARY KEY, state TEXT)")
        self.conn.execute("INSERT INTO view_asks VALUES (?, 'answered')", (aid,))
        self.ev("turn_end", {"last_message": "x"}, "owner")
        of_listen.step(self.conn, [p], cur)
        self.assertEqual(started, [])
        self.assertNotIn("owner", p.due)


class StudioCacheTimerTest(unittest.TestCase):
    def test_the_timer_starts_a_detached_refresh_through_of_studio(self):
        import of_listen
        calls = []
        saved = of_listen.subprocess.Popen if hasattr(of_listen, "subprocess") else None
        import subprocess
        orig = subprocess.Popen
        subprocess.Popen = lambda argv, **k: calls.append((argv, k))
        try:
            p = of_listen.StudioCacheProcessor()
            self.assertEqual(p.tick_s, 600)
            self.assertFalse(p.tick(None, 0))
        finally:
            subprocess.Popen = orig
        argv, k = calls[0]
        self.assertIn("of_studio._refresh_in_background()", argv[-1])
        self.assertEqual((k["env"]["OF_AUTOMATION"], k["start_new_session"]), ("1", True))
        self.assertIn(of_listen.StudioCacheProcessor, of_listen.PROCESSORS)


class PulseTimerTest(ListenTest):
    def test_the_pulse_runs_detached_and_skips_while_the_last_one_holds_the_lock(self):
        # 2026-11-09: a pulse of 75-240 s stopped the listener's loop, its heartbeat and the views while it ran
        import fcntl
        import subprocess
        calls = []
        orig = subprocess.Popen
        subprocess.Popen = lambda argv, **k: calls.append((argv, k))
        try:
            p = of_listen.PulseProcessor()
            self.assertFalse(p.tick(None, 0))
            held = open(os.path.join(self.tmp.name, "pulse-run.lock"), "w")
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                p.tick(None, 0)
            finally:
                held.close()
        finally:
            subprocess.Popen = orig
        self.assertEqual(len(calls), 1)
        argv, k = calls[0]
        self.assertEqual((argv[2], argv[-1]), (of_listen._PULSE_RUNNER, str(p.TIMEOUT_S)))
        self.assertEqual((k["env"]["OF_AUTOMATION"], k["env"]["SSH_AUTH_SOCK"], k["start_new_session"]), ("1", "", True))


class WaitsTimerTest(ListenTest):
    def test_the_waits_view_refreshes_detached_only_when_dirty_and_not_twice_at_once(self):
        # 2026-11-09: 15-35 s every 60 s inside the loop stopped the heartbeat and the asks view
        import fcntl
        import subprocess
        calls = []
        orig = subprocess.Popen
        subprocess.Popen = lambda argv, **k: calls.append((argv, k))
        try:
            p = of_listen.WaitsProcessor()
            held = open(os.path.join(self.tmp.name, "waits-run.lock"), "w")
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                p.tick(None, 0)
            finally:
                held.close()
            self.assertTrue(p.dirty)  # skipped while the last refresh runs: tried again next tick
            p.tick(None, 0)
            p.tick(None, 0)  # nothing new since: no refresh
        finally:
            subprocess.Popen = orig
        self.assertEqual(len(calls), 1)
        argv, k = calls[0]
        self.assertEqual(argv[2], of_listen._WAITS_RUNNER)
        self.assertEqual((k["env"]["OF_AUTOMATION"], k["start_new_session"]), ("1", True))


class WatchdogTest(unittest.TestCase):
    """Hand-over 6d9a8305 (docs/health.md): the listener at Standard priority, and a watchdog that restarts a stuck
    one, its stacks first, as of-server's (of_server's 3981069)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = self.tmp.name
        self.calls = []

    def tearDown(self):
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def check(self, now, ok):
        return of_listen.watchdog(now=now, probe=lambda: ok, pid=os.getpid(), log=lambda m: self.calls.append(m),
                                  restart=lambda: self.calls.append("restart"),
                                  dump=lambda p: self.calls.append(("dump", p)))

    def test_restart_after_three_minutes_stuck_once_in_fifteen(self):
        from unittest import mock
        with mock.patch.object(of_events, "emit", lambda *a, **k: None):
            self.assertEqual(self.check(0, True), "ok")
            self.assertEqual([self.check(t, False) for t in (60, 120, 180)], ["failing"] * 3)
            self.assertEqual(self.check(240, False), "restarted")
            self.assertEqual(self.calls[0], ("dump", os.getpid()))           # the stacks before the restart
            self.assertIn("restart", self.calls)
            self.assertEqual(self.check(300, False), "waiting")              # at most once in restart_gap
            self.assertEqual(self.check(400, True), "ok")
            self.assertEqual(self.check(10_000, False), "failing")            # a sleep between checks starts over

    def test_no_process_is_launchds_and_the_probe_reads_heartbeat_and_ticks(self):
        self.assertEqual(of_listen.watchdog(now=0, probe=lambda: False, pid=2 ** 22 + 7, log=lambda m: None),
                         "stopped")
        import time as _t
        of_listen._touch(of_listen.heartbeat_path())                        # the main loop's beat, a file
        of_listen._touch(of_listen.ticks_path())
        self.assertEqual(of_listen.watchdog(pid=os.getpid(), log=lambda m: None), "ok")
        old = _t.time() - of_listen.WATCHDOG["ticks_stale"] - 60
        os.utime(of_listen.ticks_path(), (old, old))                        # the timers stopped turning
        self.assertEqual(of_listen.watchdog(pid=os.getpid(), log=lambda m: None), "failing")
        os.utime(of_listen.ticks_path(), None)
        old = _t.time() - of_listen.STALE_S - 60
        os.utime(of_listen.heartbeat_path(), (old, old))                    # the main loop stopped beating
        self.assertEqual(of_listen.watchdog(pid=os.getpid(), log=lambda m: None), "failing")

    def test_the_probe_never_opens_the_store_and_a_fifo_cannot_block_it(self):
        """of_server on PR 145: a root watchdog that opened a user's store read-only left root-owned of.db-wal and
        -shm in the user's folder, and a FIFO in their place blocked it."""
        os.mkfifo(of_listen.heartbeat_path())
        os.mkfifo(of_listen.ticks_path())
        got = of_listen.watchdog(pid=os.getpid(), log=lambda m: None)       # returns: stat only, nothing opened
        self.assertIn(got, ("ok", "failing"))
        self.assertEqual(sorted(f for f in os.listdir(self.tmp.name) if f.startswith("of.db")), [])

    def test_the_pid_is_launchds_never_a_files(self):
        """of_review on PR 145: a dead pid left in listen.pid by `of listen rebuild` or a terminal run made a stuck
        listener look stopped, so it was never restarted."""
        from unittest import mock
        import of_relay_client
        with open(os.path.join(self.tmp.name, "listen.pid"), "w") as fh:
            fh.write("1")                                                       # a stale file: ignored
        asked = []
        with mock.patch.object(sys, "platform", "darwin"), \
                mock.patch.object(of_relay_client, "launchd_pid", lambda label, domain=None: asked.append(
                (label, domain)) or os.getpid()), mock.patch.object(of_events, "emit", lambda *a, **k: None):
            got = [of_listen.watchdog(now=t, probe=lambda: False, log=lambda m: None,
                                      restart=lambda: self.calls.append("restart"),
                                      dump=lambda p: self.calls.append(("dump", p))) for t in (0, 60, 120, 180)]
        self.assertEqual(got[-1], "restarted")
        self.assertEqual(asked[0], (of_listen.LAUNCHD_LABEL, None))            # gui/<uid>, the listener's label
        self.assertIn(("dump", os.getpid()), self.calls)
        with mock.patch.object(sys, "platform", "linux"), \
                mock.patch.dict(os.environ, {"OF_LISTEN_TARGET": "system/dev.organic-framework.listen.of"}), \
                mock.patch.object(of_relay_client, "launchd_pid", lambda label, domain=None: asked.append(
                    (label, domain)) or None):
            self.assertEqual(of_listen.watchdog(now=10_000, probe=lambda: False, log=lambda m: None), "stopped")
        self.assertEqual(asked[-1], ("dev.organic-framework.listen.of", "system"))  # the host's daemon

    def test_linux_watchdog_dumps_the_main_pid_then_restarts_the_user_unit(self):
        from unittest import mock
        show = ["systemctl", "--user", "show", "-p", "MainPID", "--value", "of-listen.service"]
        restart = ["systemctl", "--user", "restart", "of-listen.service"]

        def run(cmd, **kw):
            self.calls.append(cmd)
            self.assertIn(cmd, (show, restart))
            return mock.Mock(returncode=0, stdout=f"{os.getpid()}\n" if cmd == show else "")

        with mock.patch.object(sys, "platform", "linux"), mock.patch.dict(os.environ, {"OF_LISTEN_TARGET": ""}), \
                mock.patch("of_relay_client.launchd_pid", side_effect=AssertionError("unexpected launchd lookup")), \
                mock.patch.object(of_events, "emit"):
            for t in (0, 60, 120, 180):
                result = of_listen.watchdog(now=t, probe=lambda: False, dump=self.calls.append, log=lambda m: None,
                                            run=run)
        self.assertEqual(result, "restarted")
        self.assertEqual(self.calls, [show] * 4 + [os.getpid(), restart])

    def test_linux_watchdog_leaves_a_listener_without_a_main_pid_stopped(self):
        from unittest import mock
        with mock.patch.object(sys, "platform", "linux"), mock.patch.dict(os.environ, {"OF_LISTEN_TARGET": ""}), \
                mock.patch("of_relay_client.launchd_pid", side_effect=AssertionError("unexpected launchd lookup")):
            for code, output in ((0, "0\n"), (0, ""), (0, "invalid\n"), (1, str(os.getpid()))):
                with self.subTest(code=code, output=output):
                    run = mock.Mock(return_value=mock.Mock(stdout=output, returncode=code))
                    self.assertEqual(of_listen.watchdog(now=0, probe=lambda: False, run=run), "stopped")
                    self.assertEqual(run.call_args.args[0], ["systemctl", "--user", "show", "-p", "MainPID",
                                                            "--value", "of-listen.service"])

    def test_the_units_run_at_standard_priority_with_a_watchdog(self):
        units = of_listen.launchd_plists(python="/py", of_bin="/of")
        listener = units[of_listen.LAUNCHD_LABEL]
        self.assertIn("<key>ProcessType</key><string>Standard</string>", listener)
        self.assertNotIn("Nice", listener)
        self.assertNotIn("LowPriorityIO", listener)
        self.assertIn("<string>/py</string><string>/of</string><string>listen</string><string>run</string>", listener)
        self.assertIn("<key>StartInterval</key><integer>60</integer>", units[of_listen.WATCHDOG_LABEL])
        ran = []
        run = lambda argv, **k: ran.append(argv) or type("R", (), {"returncode": 0})()  # noqa: E731
        out = of_listen.install_launchd(run=run, plists=units, agents_dir=self.tmp.name)
        self.assertEqual(out, [f"{of_listen.LAUNCHD_LABEL}: loaded", f"{of_listen.WATCHDOG_LABEL}: loaded"])
        self.assertEqual(of_listen.install_launchd(run=run, plists=units, agents_dir=self.tmp.name),
                         [f"{of_listen.LAUNCHD_LABEL}: unchanged", f"{of_listen.WATCHDOG_LABEL}: unchanged"])

    def test_a_unit_is_never_written_through_a_link(self):
        """of_review on PR 628: a link at the fixed temporary name took the write and the rename moved it over."""
        units = of_listen.launchd_plists(python="/py", of_bin="/of")
        run = lambda argv, **k: type("R", (), {"returncode": 0})()  # noqa: E731
        victim = os.path.join(self.tmp.name, "victim")
        with open(victim, "w") as fh:
            fh.write("mine\n")
        path = os.path.join(self.tmp.name, f"{of_listen.LAUNCHD_LABEL}.plist")
        os.symlink(victim, path + ".tmp")
        of_listen.install_launchd(run=run, plists={of_listen.LAUNCHD_LABEL: units[of_listen.LAUNCHD_LABEL]},
                                  agents_dir=self.tmp.name)
        with open(victim) as fh:
            self.assertEqual(fh.read(), "mine\n")
        self.assertFalse(os.path.islink(path))
        os.remove(path)
        os.symlink(victim, path)
        out = of_listen.install_launchd(run=run, plists={of_listen.LAUNCHD_LABEL: units[of_listen.LAUNCHD_LABEL]},
                                        agents_dir=self.tmp.name)
        self.assertIn("not loaded", out[0])
        with open(victim) as fh:
            self.assertEqual(fh.read(), "mine\n")
        # an uninstall boots a linked unit out and removes the link, a dangling one too (of_review on PR 659)
        ran = []
        run2 = lambda argv, **k: ran.append(argv) or type("R", (), {"returncode": 0})()  # noqa: E731
        for target in (victim, os.path.join(self.tmp.name, "gone")):
            if os.path.lexists(path):
                os.remove(path)
            os.symlink(target, path)
            out = of_listen.install_launchd(uninstall=True, run=run2, agents_dir=self.tmp.name,
                                            plists={of_listen.LAUNCHD_LABEL: units[of_listen.LAUNCHD_LABEL]})
            self.assertEqual(out, [f"removed {of_listen.LAUNCHD_LABEL}"])
            self.assertFalse(os.path.lexists(path))
            self.assertIn("bootout", ran[-1])
        with open(victim) as fh:
            self.assertEqual(fh.read(), "mine\n")


class SystemdTest(unittest.TestCase):
    def test_listener_and_watchdog_units(self):
        from unittest import mock
        import tempfile
        logs = tempfile.mkdtemp()
        with mock.patch.dict(of_listen.WATCHDOG, {"every": 37}), mock.patch.object(of_events, "STATE_DIR", "/of state"), \
                mock.patch.object(of_events, "LOG_DIR", logs):
            units = of_listen.systemd_units(python="/my python", of_bin="/my repo/of")
        self.assertEqual(set(units), {"of-listen.service", "of-listen-watchdog.service", "of-listen-watchdog.timer"})
        for name, action in (("of-listen.service", "run"), ("of-listen-watchdog.service", "watchdog")):
            lines = units[name].splitlines()
            self.assertIn(f'ExecStart="/my python" "/my repo/of" listen {action}', lines)
            self.assertIn("Environment=OF_AUTOMATION=1", lines)
            self.assertIn("Environment=SSH_AUTH_SOCK=", lines)
            # the logs live in LOG_DIR, never in the state folder a sandbox may write (hand-over 58681935)
            log = os.path.join(logs, "listen.log" if action == "run" else "listen-watchdog.log")
            self.assertIn(f"StandardOutput=append:{log}", lines)
            self.assertIn(f"StandardError=append:{log}", lines)
        self.assertTrue({"Restart=always", "RestartSec=10", "WantedBy=default.target"}.issubset(
            units["of-listen.service"].splitlines()))
        self.assertIn("Type=oneshot", units["of-listen-watchdog.service"].splitlines())
        self.assertIn("PartOf=of-listen-watchdog.timer", units["of-listen-watchdog.service"].splitlines())
        self.assertNotIn("[Install]", units["of-listen-watchdog.service"])
        self.assertTrue({"OnUnitActiveSec=37", "OnBootSec=60", "WantedBy=timers.target"}.issubset(
            units["of-listen-watchdog.timer"].splitlines()))

    def test_install_reinstall_and_uninstall(self):
        from unittest import mock
        listener, timer = "of-listen.service", "of-listen-watchdog.timer"
        calls = []
        active = set()

        def run(cmd, **kw):
            self.assertEqual(cmd[:2], ["systemctl", "--user"])
            calls.append(cmd[2:])
            return mock.Mock(returncode=3 if cmd[2] == "is-active" and cmd[3] not in active else 0)

        reload = ["daemon-reload"]
        checks = [["is-active", listener], ["is-active", timer]]
        with tempfile.TemporaryDirectory() as root, mock.patch.object(of_events, "STATE_DIR", root):
            directory = Path(root) / "units"
            units = of_listen.systemd_units()

            def install(uninstall=False):
                return of_listen.install_systemd(uninstall=uninstall, run=run, units=units, units_dir=directory)

            self.assertEqual(len(install()), 3)
            self.assertEqual({p.name: p.read_text() for p in directory.iterdir()}, units)
            self.assertEqual(calls, [reload, ["enable", "--now", listener, timer]])
            active.update((listener, timer))
            calls.clear()
            self.assertEqual(install(), [f"{name}: unchanged" for name in units])
            self.assertEqual(calls, checks)

            active.remove(timer)
            calls.clear()
            self.assertIn(f"{listener}: unchanged", install())
            self.assertEqual(calls, checks + [reload, ["enable", "--now", timer]])
            active.add(timer)

            (directory / listener).write_text("old settings\n")
            calls.clear()
            self.assertIn(f"{timer}: unchanged", install())
            self.assertEqual(calls, checks + [["stop", listener], reload, ["enable", "--now", listener]])
            self.assertEqual((directory / listener).read_text(), units[listener])

            calls.clear()
            self.assertEqual(install(uninstall=True), [f"removed {name}" for name in units])
            self.assertEqual(calls, [["disable", "--now", listener, timer], reload])
            self.assertEqual(list(directory.iterdir()), [])


class PruneTest(unittest.TestCase):
    def test_a_processor_that_no_longer_runs_loses_its_cursor_and_its_rows(self):
        """2026-11-17 (of_architect, message 01a1e82c-d448): fold:escalated, removed from the code, kept its cursor,
        and the health check read it as three days behind."""
        import sqlite3
        import of_listen
        conn = sqlite3.connect(":memory:")
        conn.executescript(of_listen.DDL)
        conn.execute("CREATE TABLE view_fold_meta (view TEXT PRIMARY KEY, version TEXT, cursor INTEGER, at REAL)")
        conn.execute("CREATE TABLE view_fold (view TEXT, key TEXT, row TEXT, PRIMARY KEY (view, key))")
        for name in ("asks", "fold:answers", "fold:escalated", "gone"):
            conn.execute("INSERT INTO listener_cursor VALUES (?, '1', 5, 'x')", (name,))
        conn.execute("INSERT INTO view_fold_meta VALUES ('escalated', '1', 5, 0), ('answers', '1', 5, 0)")
        conn.execute("INSERT INTO view_fold VALUES ('escalated', 'A1', '{}'), ('answers', 'A2', '{}')")
        running = [type("P", (), {"name": n})() for n in ("asks", "fold:answers")]
        self.assertEqual(sorted(of_listen.prune(conn, running)), ["fold:escalated", "gone"])
        self.assertEqual(sorted(r[0] for r in conn.execute("SELECT processor FROM listener_cursor")),
                         ["asks", "fold:answers"])
        self.assertEqual(conn.execute("SELECT view FROM view_fold_meta").fetchall(), [("answers",)])
        self.assertEqual(conn.execute("SELECT view FROM view_fold").fetchall(), [("answers",)])

    def test_a_view_module_that_does_not_import_is_recorded(self):
        """of_review on PR 256: an ImportError was swallowed, so its views' cursors would be forgotten as removed."""
        import of_fold
        saved = of_fold.VIEW_MODULES
        of_fold.VIEW_MODULES = ("of_fold", "of_no_such_views_module")
        try:
            of_fold.views()
            self.assertEqual(of_fold.FAILED, ["of_no_such_views_module"])
        finally:
            of_fold.VIEW_MODULES = saved
            of_fold.views()
        self.assertEqual(of_fold.FAILED, [])



class GithubBusyTest(unittest.TestCase):
    def test_a_run_waits_out_a_high_load_for_at_most_two_hours(self):
        """A user organization's structure keeper, message 01a20444-9136: 975 gh starts in an hour on a paging Mac."""
        import of_listen
        from unittest import mock
        import tempfile
        p = of_listen.GithubProcessor()
        d = tempfile.mkdtemp()
        saved = of_listen.of_events.STATE_DIR
        of_listen.of_events.STATE_DIR = d
        self.addCleanup(setattr, of_listen.of_events, "STATE_DIR", saved)
        self.assertTrue(p.busy(load=25, cores=10))
        self.assertFalse(p.busy(load=15, cores=10))
        started, T0 = [], 1_800_000_000.0
        with mock.patch.object(p, "busy", lambda: True), mock.patch("subprocess.Popen", lambda *a, **k: started.append(a)):
            p.tick(None, T0)                                         # no run yet: it runs, load or not
            self.assertEqual(len(started), 1)
            self.assertFalse(p.tick(None, T0 + 1800))                # busy within two hours: it waits
            p2 = of_listen.GithubProcessor()                             # a listener restart keeps the bound
            with mock.patch.object(p2, "busy", lambda: True):
                self.assertFalse(p2.tick(None, T0 + 3600))
                self.assertEqual(len(started), 1)
                p2.tick(None, T0 + 7200)                             # two hours since the last: it runs
        self.assertEqual(len(started), 2)
        self.assertEqual(started[0][0][:3], ["nice", "-n", "15"])       # at background priority



class RunnerStringsStartTest(unittest.TestCase):
    """of_release, 2026-11-22: two runner strings called os before importing it, and the pulse's runner died at start on
    the founder's Mac. Each runner string is started here, with a stand-in for `of` and for the listener's folder."""

    def test_each_runner_string_starts_takes_its_lock_and_runs(self):
        import subprocess
        import tempfile
        import of_listen
        with tempfile.TemporaryDirectory() as d:
            stub = os.path.join(d, "of")
            with open(stub, "w") as fh:
                fh.write("import sys\nprint('stub', *sys.argv[1:])\n")
            mods = os.path.join(d, "mods")   # a stand-in for the listener's folder (the waits runner imports from it)
            os.makedirs(mods)
            with open(os.path.join(mods, "of_listen.py"), "w") as fh:
                fh.write("def refresh_waits(conn, now):\n    print('refreshed')\n")
            with open(os.path.join(mods, "of_store.py"), "w") as fh:
                fh.write("def connect():\n    return None\n")
            runs = {"_WAITS_RUNNER": [mods], "_PULSE_RUNNER": [stub, "30"], "_GH_RUNNER": [stub]}
            for name, args in runs.items():
                lock = os.path.join(d, name + ".lock")
                r = subprocess.run([sys.executable, "-c", getattr(of_listen, name), lock, *args],
                                   capture_output=True, text=True, timeout=60, env=dict(os.environ, OF_STATE=d))
                self.assertNotIn("Traceback", r.stdout + r.stderr, name)
                self.assertEqual(r.returncode, 0, name + ": " + r.stdout + r.stderr)
                self.assertTrue(os.path.isfile(lock), name)


if __name__ == "__main__":
    unittest.main()
