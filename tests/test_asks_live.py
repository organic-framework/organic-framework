"""Open asks reconciled with live sessions (of_asks_live) and closed by session progress (of_asks)."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_asks  # noqa: E402
import of_asks_live  # noqa: E402


def ev(i, name, sid, minute, position=None, **payload):
    return {"event_id": f"e{i:02d}", "ts": f"2026-11-06T07:{minute:02d}:00Z", "name": name, "session_id": sid,
            "position": position, "payload": payload}


class LiveTest(unittest.TestCase):
    def test_the_six_cases(self):
        asks = [{"event_id": "obs", "session_id": "S-obs", "kind": "permission", "state": "open"},
                {"event_id": "lab", "session_id": "S-lab", "kind": "permission", "state": "open"},
                {"event_id": "gaps", "session_id": "S-gaps", "kind": "question (heuristic)", "state": "open"},
                {"event_id": "busy", "session_id": "S-busy", "kind": "question (heuristic)", "state": "open"},
                {"event_id": "old", "session_id": "S-2026-06", "kind": "question (heuristic)", "state": "open"},
                {"event_id": "decl", "session_id": "S-gone", "kind": "question (declared)", "state": "open"},
                {"event_id": "herdr", "session_id": "S-h", "kind": "permission", "state": "open"}]
        live = {"S-obs": ("waiting", "input needed"), "S-lab": ("busy", None), "S-gaps": ("idle", None),
                "S-busy": ("busy", None), "S-h": ("idle", None)}
        got = dict(of_asks_live.reconcile(asks, live, blocked_sessions={"S-h"}))
        self.assertEqual(got, {"lab": "resolved_elsewhere", "busy": "resolved_elsewhere", "old": "orphaned"})
        self.assertEqual(of_asks_live.reconcile(asks, None), [])  # a listing failed: close nothing

    def test_a_task_is_never_reconciled(self):
        """of_architect, 2026-11-07: 63 reconcile closures were written for open tasks while their askers were busy."""
        asks = [{"event_id": "t1", "session_id": "S-busy", "kind": "task", "task": True, "state": "open"},
                {"event_id": "t2", "session_id": "S-gone", "kind": "task", "task": True, "state": "open"}]
        emitted = []
        got = of_asks_live.reconcile(asks, {"S-busy": ("busy", None)}, emit=lambda *a, **k: emitted.append(a))
        self.assertEqual((got, emitted), ([], []))

    def test_live_sessions_parse_and_failure(self):
        class R:
            def __init__(self, out, rc=0):
                self.stdout, self.returncode = out, rc
        out = '[{"sessionId": "a", "status": "waiting", "waitingFor": "dialog open"}, {"sessionId": "b", "state": "blocked"}]'
        self.assertEqual(of_asks_live.live_sessions(["d"], run=lambda *a, **k: R(out)),
                         {"a": ("waiting", "dialog open"), "b": ("blocked", None)})
        self.assertIsNone(of_asks_live.live_sessions(["d"], run=lambda *a, **k: R("", 1)))

    def test_progress_closes_and_injected_turns_do_not_ask(self):
        q = [{"question": "Merge #2272 now?"}]
        rows = [ev(1, "of.agent_question", "S1", 1, detected_by="heuristic", questions=q),
                ev(2, "of.continuation", "S1", 1),
                ev(3, "of.attention", "S1", 2),
                ev(4, "of.permission_request", "S2", 3, tool_name="Bash"),
                ev(5, "of.permission_resolved", "S2", 4, outcome="ran"),
                ev(6, "of.human_prompt", "S3", 5, origin="injected", text="moved account; reply in 3 lines"),
                ev(7, "of.turn_end", "S3", 6, last_message="next step?"),
                ev(8, "of.agent_question", "S3", 6, detected_by="heuristic", questions=q)]
        states = {a["event_id"]: a["state"] for a in of_asks.project(rows)}
        # heuristic questions are candidates for the judge (bin/of_judge.py), also after an injected prompt (e08)
        self.assertEqual(states, {"e01": "candidate", "e04": "answered", "e08": "candidate"})
        rows.append(ev(9, "of.turn_end", "S1", 7, last_message="done"))
        self.assertEqual(of_asks.project(rows)[0]["state"], "moved_on")  # S1 went on: the wait ended, no answer


    def test_the_screen_reconciles_from_the_view_and_never_projects_the_log(self):
        """2026-11-12: the screen's pass projected the whole log (16 s of CPU) and its cards waited behind it."""
        import sqlite3
        conn = sqlite3.connect(":memory:")
        saved = of_asks.load

        def boom(*a, **k):
            raise AssertionError("the log was projected")
        of_asks.load = boom
        try:
            self.assertEqual(of_asks_live.run(conn, view=True), [])  # no listener: no pass at all
        finally:
            of_asks.load = saved
            conn.close()
        task = {"event_id": "t", "session_id": "S-gone", "kind": "task", "state": "open"}  # a view row: no task key
        self.assertIsNone(of_asks_live.decide(task, {}, set()))


if __name__ == "__main__":
    unittest.main()
