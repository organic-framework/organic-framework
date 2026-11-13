"""The listener (of_listen): cursors, replay, the asks view equal to the projection."""

import os
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


if __name__ == "__main__":
    unittest.main()


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
        conn = of_store.connect(os.path.join(self.tmp.name, "of.db"))
        conn.executescript(of_listen.DDL)
        import time as _t
        conn.execute("INSERT OR REPLACE INTO listener_meta VALUES ('heartbeat', ?)", (str(_t.time()),))
        conn.close()
        of_listen._touch(of_listen.ticks_path())
        self.assertEqual(of_listen.watchdog(pid=os.getpid(), log=lambda m: None), "ok")
        old = _t.time() - of_listen.WATCHDOG["ticks_stale"] - 60
        os.utime(of_listen.ticks_path(), (old, old))                        # the timers stopped turning
        self.assertEqual(of_listen.watchdog(pid=os.getpid(), log=lambda m: None), "failing")

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
