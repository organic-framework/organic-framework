"""A Claude Code session that a fork started is reported to herdr as resume (of_herdr, 2026-11-24): herdr 0.9.5 drops a
Claude report whose SessionStart source is fork, and the pane keeps the old session (src/terminal/state.rs,
session_report_allows_session_replacement; the upstream issue waits on the founder's card OF-420)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_hook  # noqa: E402

ENV = {"HERDR_ENV": "1", "HERDR_PANE_ID": "w1:p2", "HERDR_SOCKET_PATH": "/run/herdr.sock"}
HOOK = {"session_id": "S2", "source": "fork", "transcript_path": "/home/alex/.claude/projects/p/S2.jsonl"}


class ClaudeForkToHerdr(unittest.TestCase):
    def run_it(self, hook, env=ENV, panes=("S1", "S2"), fail=False):
        calls, seen = [], list(panes)

        def call(method, params):
            calls.append((method, params))
            if fail:
                raise OSError("no herdr")
            if method == "pane.get":
                value = seen.pop(0) if seen else "S2"
                return {"id": "x", "result": {"type": "pane_info", "pane": {"agent_session": {"value": value}}}}
            return {"id": "x", "result": {}}
        sent = of_hook.report_claude_fork(hook, env=env, call=call, sleep=lambda s: None)
        return sent, calls

    def test_a_fork_is_reported_as_resume_until_herdr_names_it(self):
        sent, calls = self.run_it(HOOK)
        self.assertEqual(sent, 2)   # herdr still named S1 after the first: its own dropped fork took a newer seq
        reports = [p for m, p in calls if m == "pane.report_agent_session"]
        self.assertEqual(len(reports), 2)
        r = reports[0]
        self.assertEqual((r["pane_id"], r["source"], r["agent"], r["agent_session_id"], r["session_start_source"]),
                         ("w1:p2", "herdr:claude", "claude", "S2", "resume"))
        self.assertEqual(r["agent_session_path"], HOOK["transcript_path"])
        self.assertLess(reports[0]["seq"], reports[1]["seq"])

    def test_it_stops_at_its_tries(self):
        sent, _ = self.run_it(HOOK, panes=("S1",) * 9)
        self.assertEqual(sent, 3)

    def test_only_a_fork_in_a_herdr_pane_and_never_a_subagent(self):
        for hook, env in ((dict(HOOK, source="startup"), ENV), (dict(HOOK, source="resume"), ENV),
                          (dict(HOOK, agent_id="a1"), ENV), ({k: v for k, v in HOOK.items() if k != "transcript_path"}, ENV),
                          (HOOK, dict(ENV, HERDR_ENV="0")), (HOOK, {k: v for k, v in ENV.items() if k != "HERDR_PANE_ID"})):
            sent, calls = self.run_it(hook, env=env)
            self.assertEqual((sent, calls), (0, []), (hook, env))

    def test_a_failure_never_stops_the_start(self):
        sent, calls = self.run_it(HOOK, fail=True)
        self.assertEqual(sent, 0)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
