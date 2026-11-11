"""OF's herdr plugin (herdr-plugin/, bin/of_herdr_plugin.py): its manifest, the event's pane, the wait for the agent's
session, and coalesced applies. No herdr runs here."""

import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(ROOT, "bin"))
import of_herdr_plugin as plugin  # noqa: E402

# herdr 0.9.5 runs plugin event hooks only for these (src/api/schema/events.rs, PLUGIN_HOOK_EVENT_KINDS)
HOOK_EVENTS = {"workspace.created", "workspace.updated", "workspace.closed", "workspace.renamed", "workspace.moved",
               "workspace.reordered", "workspace.focused", "worktree.created", "worktree.opened", "worktree.removed",
               "tab.created", "tab.closed", "tab.renamed", "tab.moved", "tab.focused", "pane.created", "pane.closed",
               "pane.focused", "pane.moved", "pane.exited", "pane.agent_detected", "pane.agent_status_changed"}


class ManifestTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "herdr-plugin", "herdr-plugin.toml"), "rb") as fh:
            self.m = tomllib.load(fh)

    def test_the_manifest_is_what_herdr_links(self):
        for k in ("id", "name", "version", "min_herdr_version"):
            self.assertTrue(self.m.get(k), k)
        self.assertRegex(self.m["id"], r"^[A-Za-z0-9.:_-]+$")
        self.assertEqual({e["on"] for e in self.m["events"]}, {"pane.agent_detected"})
        self.assertTrue({e["on"] for e in self.m["events"]} <= HOOK_EVENTS)
        for item in self.m["events"] + self.m["startup"] + self.m["panes"]:
            self.assertEqual(item["command"][:2], ["sh", "run.sh"])  # argv, run from the plugin folder

    def test_the_questions_screen_opens_as_a_popup(self):
        """`herdr plugin pane open --plugin organic-framework --entrypoint asks` without --placement takes the
        manifest's placement; herdr 0.9.5's CLI cannot ask for a popup itself (checked in the lab, asks-screen-v0)."""
        asks = next(p for p in self.m["panes"] if p["id"] == "asks")
        self.assertEqual((asks["placement"], asks["command"][2]), ("popup", "asks"))
        self.assertRegex(asks["id"], r"^[A-Za-z0-9:_-]+$")  # local ids take no dots

    def test_the_runner_is_valid_sh_and_finds_the_module(self):
        run = os.path.join(ROOT, "herdr-plugin", "run.sh")
        self.assertEqual(subprocess.run(["sh", "-n", run]).returncode, 0)
        r = subprocess.run(["sh", run, "nonsense"], capture_output=True, text=True, cwd=os.path.dirname(run),
                           env=dict(os.environ, OF_STATE=tempfile.mkdtemp()), timeout=30)
        self.assertEqual(r.returncode, 2)  # reached of_herdr_plugin.py, which refused the unknown hook
        self.assertIn("usage: of_herdr_plugin.py", r.stderr)  # startup|agent-detected|asks


class HookTest(unittest.TestCase):
    def test_the_events_pane(self):
        ev = {"event": "pane.agent_detected", "data": {"pane_id": "w2:p7", "agent": "claude"}}
        self.assertEqual(plugin.event_pane({"HERDR_PLUGIN_EVENT_JSON": json.dumps(ev)}), "w2:p7")
        self.assertEqual(plugin.event_pane({"HERDR_PLUGIN_EVENT_JSON": json.dumps({"pane_id": "w1:p1"})}), "w1:p1")
        self.assertEqual(plugin.event_pane({"HERDR_PLUGIN_EVENT_JSON": "not json", "HERDR_PANE_ID": "w3:p2"}), "w3:p2")
        self.assertIsNone(plugin.event_pane({}))

    def test_it_waits_for_the_session_herdr_reports_after_detecting(self):
        answers = iter([None, None, "S1"])
        t = iter(range(100))
        self.assertEqual(plugin.wait_session("w1:p1", get=lambda p: next(answers), clock=lambda: next(t),
                                             sleep=lambda s: None), "S1")
        t = iter(range(100))
        self.assertIsNone(plugin.wait_session("w1:p1", wait_s=3, get=lambda p: None, clock=lambda: next(t),
                                              sleep=lambda s: None))

    def test_a_panels_button_opens_the_popup(self):
        calls = []

        class R:
            def __init__(self, out, rc=0):
                self.returncode, self.stdout, self.stderr = rc, out, ""
        ok = lambda argv, **k: (calls.append(argv), R('{"id":"cli:plugin","result":{"type":"ok"}}'))[1]
        self.assertEqual(plugin.open_questions("/w/bin/of", run=ok), (True, None))
        self.assertEqual(calls[0][1:7], ["plugin", "pane", "open", "--plugin", "organic-framework", "--entrypoint"])
        self.assertEqual(calls[0][-2:], ["--env", "OF_ASKS_OF=/w/bin/of"])
        self.assertNotIn("--placement", calls[0])  # the manifest's popup applies; the CLI cannot name it
        class E(R):
            def __init__(self, err):
                super().__init__("", 1)
                self.stderr = err
        busy = lambda argv, **k: E('{"error":{"code":"ui_busy","message":"a popup pane is already open"},"id":"cli:plugin"}')
        self.assertEqual(plugin.open_questions(run=busy), (False, "ui_busy"))  # herdr 0.9.5 prints it on stderr

    def test_install_links_once_and_records_ofs_home(self):
        import tempfile as tf
        with tf.TemporaryDirectory() as cfg:
            calls, linked = [], []

            class R:
                def __init__(self, out="", rc=0):
                    self.returncode, self.stdout, self.stderr = rc, out, ""

            def run(argv, **k):
                calls.append(argv[1:3])
                if argv[1:3] == ["plugin", "list"]:
                    return R("".join(f"- organic-framework enabled [local:{x}]\n" for x in linked))
                if argv[1:3] == ["plugin", "link"]:
                    linked.append(argv[3])
                    return R()
                return R(cfg + "\n")
            home = os.path.abspath(ROOT)
            self.assertTrue(plugin.install(home, run=run)[0])
            self.assertTrue(plugin.install(home, run=run)[0])
            self.assertEqual(sum(1 for c in calls if c == ["plugin", "link"]), 1)  # linked once
            with open(os.path.join(cfg, "of_home")) as fh:
                self.assertEqual(fh.read().strip(), home)
            self.assertFalse(plugin.install("/no/such/of", run=run)[0])

    def test_the_agents_view_puts_open_questions_first_and_lists_everyone(self):
        sent = []
        ok = plugin.set_view(request=lambda method, params: (sent.append((method, params)),
                                                             {"result": {"type": "agent_view", "active": True}})[1])
        self.assertEqual(ok, (True, None))
        method, params = sent[0]
        self.assertEqual((method, params["source"]), ("agent.view.set", "plugin:organic-framework"))
        self.assertEqual([x["field"] for x in params["sort"][:2]], [{"token": "ask"}, "attention"])
        self.assertNotIn("filter", params)  # every agent stays listed
        refused = lambda method, params: {"error": {"code": "plugin_not_found", "message": "not linked"}}
        self.assertEqual(plugin.set_view(request=refused), (False, "plugin_not_found"))

    def test_applies_are_coalesced(self):
        """After a herdr restart many agents are detected at once: one apply at a time, and exactly one more for
        everything that arrived during it."""
        with tempfile.TemporaryDirectory() as d:
            runs = []

            def run():
                runs.append(1)
                if len(runs) == 1:  # three more agents detected while the first apply runs
                    for _ in range(3):
                        self.assertEqual(plugin.coalesced(lambda: runs.append("inner"), state=d), 0)
            self.assertEqual(plugin.coalesced(run, state=d), 2)
            self.assertEqual(runs, [1, 1])
            self.assertFalse(os.path.exists(os.path.join(d, "herdr-plugin.pending")))
            self.assertEqual(plugin.coalesced(lambda: runs.append(2), state=d), 1)  # later, a new run

    def test_a_run_storm_stops_at_max_runs(self):
        with tempfile.TemporaryDirectory() as d:
            n = []

            def run():
                n.append(1)
                plugin.coalesced(lambda: None, state=d)  # every run is followed by another request
            self.assertEqual(plugin.coalesced(run, state=d), plugin.MAX_RUNS)
            self.assertTrue(os.path.exists(os.path.join(d, "herdr-plugin.pending")))  # the next event runs it


if __name__ == "__main__":
    unittest.main()
