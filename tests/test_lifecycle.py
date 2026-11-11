"""Mandate and holder changes over time (of_lifecycle)."""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_events  # noqa: E402
import of_lifecycle  # noqa: E402
import of_store  # noqa: E402


def bind(positions, agents):
    by = {a["name"]: a for a in agents}
    return [(p, by[p["name"]]) for p in positions if p["name"] in by], [], []


class LifecycleTest(unittest.TestCase):
    def test_snapshot_diff_and_holders(self):
        with tempfile.TemporaryDirectory() as d:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = d
            try:
                os.makedirs(os.path.join(d, "repo", "briefs"))
                brief = os.path.join(d, "repo", "briefs", "a.md")
                with open(brief, "w") as fh:
                    fh.write("v1")
                p = {"name": "of_a", "summary": "s1", "status": "active", "throughput": "raise", "_orgid": "o",
                     "mandate": "briefs/a.md", "_root": os.path.join(d, "repo")}
                agent = {"name": "of_a", "pane_id": "w1:p1", "agent_session": {"value": "S1"}}
                conn = of_store.connect()
                ev = lambda: [(json.loads(r[0]), r[1]) for r in conn.execute(
                    "SELECT payload, name FROM event ORDER BY rowid")]
                self.assertEqual(of_lifecycle.record(conn, [p], [agent], bind), 2)   # baseline + bound
                self.assertEqual(of_lifecycle.record(conn, [p], [agent], bind), 0)   # nothing changed
                p["status"] = "parked"
                with open(brief, "w") as fh:
                    fh.write("v2")
                self.assertEqual(of_lifecycle.record(conn, [p], [agent], bind), 1)
                last = [e for e, n in ev() if n == "of.mandate_changed"][-1]
                self.assertEqual(sorted(last["changed"]), ["mandate", "status"])
                agent["agent_session"]["value"] = "S2"                                # a new holder
                self.assertEqual(of_lifecycle.record(conn, [p], [agent], bind), 1)
                self.assertEqual(of_lifecycle.record(conn, [p], [], bind), 1)         # gone
                kinds = [n for _, n in ev()]
                self.assertEqual(kinds, ["of.mandate_changed", "of.holder_bound", "of.mandate_changed",
                                         "of.holder_bound", "of.holder_unbound"])
                self.assertEqual([e for e, n in ev() if n == "of.holder_bound"][-1]["previous_session"], "S1")
                conn.close()
            finally:
                of_events.STATE_DIR = saved


if __name__ == "__main__":
    unittest.main()
