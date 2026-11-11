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

    def test_keep_restarts_a_pinned_program_with_a_back_off(self):
        t = [0.0]
        said, slept, codes = [], [], iter([1, 1, 1, 0, 1])

        def run(cmd):
            t[0] += 70 if len(slept) == 3 else 1  # the fourth run lasts long: the wait goes back to the first
            return next(codes)
        plugin.keep(["x"], name="the questions panel", run=run, sleep=slept.append, clock=lambda: t[0],
                    say=said.append, max_runs=5)
        self.assertEqual(slept, [2, 4, 8, 2, 4])
        self.assertIn("starts again in 2 s", said[0])
        self.assertEqual(plugin.keep(["x"], run=lambda c: (_ for _ in ()).throw(KeyboardInterrupt()),
                                     sleep=lambda s: None, say=lambda x: None), 130)  # Ctrl-C stops it

    def _check(self, ok=True):
        class R:
            def __init__(self, out, rc):
                self.returncode, self.stdout, self.stderr = rc, out, ""
        return lambda argv, **k: R("config: ok" if ok or argv[1:3] != ["config", "check"] else "bad key", 0 if ok else 1)

    def test_configure_adds_the_key_and_the_counter_once(self):
        import tomllib
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.toml")
            with open(path, "w") as fh:
                fh.write('onboarding = false\n[ui]\nstatus_indicators = "symbols"\n')
            ok, notes = plugin.configure("/of", python="/py/python3", path=path, run=self._check(),
                                         backups=os.path.join(d, "b"), now=0)
            self.assertTrue(ok, notes)
            cfg = tomllib.load(open(path, "rb"))
            key = cfg["keys"]["command"][0]
            self.assertEqual((key["key"], key["type"], key["width"]), ("prefix+a", "popup", "90%"))
            self.assertIn("/py/python3 /of/bin/of asks --watch", key["command"])
            self.assertIn("$asks", str(cfg["ui"]["sidebar"]["spaces"]["rows"]))
            self.assertEqual(cfg["ui"]["status_indicators"], "symbols")  # the person's own settings stay
            self.assertEqual(len(os.listdir(os.path.join(d, "b"))), 1)  # backed up first
            before = open(path).read()
            self.assertTrue(plugin.configure("/of", python="/py/python3", path=path, run=self._check())[0])
            self.assertEqual(open(path).read(), before)  # idempotent

    def test_configure_leaves_the_persons_own_key_and_rows_alone(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.toml")
            own = ('[[keys.command]]\nkey = "prefix+a"\ntype = "popup"\ncommand = "lazygit"\n\n'
                   '[ui.sidebar.spaces]\nrows = [["workspace"]]\n')
            with open(path, "w") as fh:
                fh.write(own)
            ok, notes = plugin.configure("/of", path=path, run=self._check())
            self.assertTrue(ok)
            self.assertEqual(open(path).read(), own)
            self.assertTrue(any("bound to something else" in n for n in notes))
            self.assertTrue(any("rows of its own" in n for n in notes))

    def test_configure_puts_the_file_back_when_herdr_refuses_it(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "config.toml")
            with open(path, "w") as fh:
                fh.write("onboarding = false\n")
            ok, why = plugin.configure("/of", path=path, run=self._check(ok=False), backups=os.path.join(d, "b"))
            self.assertFalse(ok)
            self.assertIn("refused", why)
            self.assertEqual(open(path).read(), "onboarding = false\n")

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


class ResumeTest(unittest.TestCase):
    """docs/accounts.md A4 (hand-over a9c118e8): OF's hook reports `of claude --resume <id>` to herdr with its state, so
    a herdr restart resumes the session on its account, model and mode with no shell function."""

    def setUp(self):
        import of_hook
        self.h = of_hook
        self.saved = (of_hook._SESSION, of_hook.herdr_call, os.environ.get("HERDR_PANE_ID"))
        self.calls = []
        of_hook.herdr_call = lambda method, params: self.calls.append((method, params))
        os.environ["HERDR_PANE_ID"] = "w1:p1"

    def tearDown(self):
        self.h._SESSION, self.h.herdr_call, pane = self.saved
        if pane is None:
            os.environ.pop("HERDR_PANE_ID", None)
        else:
            os.environ["HERDR_PANE_ID"] = pane

    def test_the_state_report_carries_ofs_resume_command(self):
        self.h._SESSION = "00000000-0000-4000-8000-00000000a9c1"
        argv = self.h.resume_argv(env={}, python="/opt/py/bin/python3")
        self.assertEqual(argv[0], "env")  # herdr takes a plain command name only; paths may follow
        self.assertEqual(argv[1], "/opt/py/bin/python3")  # the hook's own Python: it has PyYAML
        self.assertTrue(argv[2].endswith(os.path.join("bin", "of")))
        self.assertEqual(argv[3:], ["claude", "--resume", "00000000-0000-4000-8000-00000000a9c1"])
        self.h.herdr_state("idle")
        method, params = self.calls[-1]
        self.assertEqual((method, params["source"], params["state"]), ("pane.report_agent", "of:hook", "idle"))
        self.assertEqual(params["resume_argv"][3:5], ["claude", "--resume"])

    def test_no_resume_command_for_a_subagent_a_side_chat_or_a_bad_path(self):
        self.h._SESSION = "00000000-0000-4000-8000-00000000a9c1"
        self.assertIsNone(self.h.resume_argv(env={"OF_SIDE_CHAT": "1"}))
        self.assertIsNone(self.h.resume_argv(env={}, python="/it's/python3"))  # herdr refuses apostrophes
        self.assertIsNone(self.h.resume_argv(env={}, of_bin="/no/such/of"))
        self.h._SESSION = None  # a subagent's call
        self.assertIsNone(self.h.resume_argv(env={}))
        self.h._SESSION = "x; rm -rf"  # never typed into a shell
        self.assertIsNone(self.h.resume_argv(env={}))


if __name__ == "__main__":
    unittest.main()
