"""Agents in tmux (bin/of_tmux.py; docs/remote.md §3 row 5): the hook's reports, the agent list, and the relay typing
into a tmux pane as into a herdr pane (idle only, an empty box only, declared first). No tmux or herdr runs here."""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_events  # noqa: E402
import of_relay_client as of_relay  # noqa: E402
import of_store  # noqa: E402
import of_tmux  # noqa: E402

ENV = {"TMUX": "/private/tmp/tmux-501/default,4242,0", "TMUX_PANE": "%3", "PWD": "/work/acme"}
PANE = "tmux:default:%3"
RULE = "─" * 60


def screen(box, combined_dim=False):
    """Claude Code's input box as tmux's capture-pane -e prints it: attributes joined into one SGR."""
    text = (f"\x1b[2;38;5;246m{box}\x1b[0m" if combined_dim else box)
    return f"some output\n\x1b[38;5;240m{RULE}\x1b[0m\n❯\xa0{text}\n\x1b[38;5;240m{RULE}\x1b[0m\n  ⏵⏵ bypass\n"


class R:
    def __init__(self, out="", rc=0):
        self.returncode, self.stdout, self.stderr = rc, out, ""


class TmuxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = self.tmp.name
        self.conn = of_store.connect()
        self.calls, self.box, self.panes = [], screen(""), "%3\t0\n%4\t0\n"
        self.saved_run, self.saved_alive = of_relay.subprocess.run, of_tmux._alive
        of_relay.subprocess.run = self._run
        of_tmux._alive = lambda pid: pid == 777

    def tearDown(self):
        of_relay.subprocess.run, of_tmux._alive = self.saved_run, self.saved_alive
        self.conn.close()
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def _run(self, argv, **k):
        self.calls.append(argv)
        if argv[0] == "herdr":
            raise AssertionError(f"a herdr call for a tmux pane: {argv}")
        if "list-panes" in argv:
            return R(self.panes)
        if "capture-pane" in argv:
            return R(self.box)
        return R()

    def report(self, state, session="S1", env=ENV, **k):
        return of_tmux.report(state, session, env=env, pid=777, now=1000.0, **k)

    def test_reports_only_inside_tmux_and_outside_herdr(self):
        self.assertIsNone(of_tmux.report("started", "S1", env={}))
        self.assertIsNone(of_tmux.report("started", "S1", env=dict(ENV, HERDR_PANE_ID="w1:p1")))
        self.assertIsNone(of_tmux.report("started", None, env=ENV))
        self.assertIsNone(of_tmux.report("started", "S1", env=dict(ENV, TMUX_PANE="3")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "tmux-panes")))

    def test_the_state_follows_the_hook(self):
        d = self.report("started")
        self.assertEqual((d["state"], d["pane_id"], d["pid"]), ("idle", PANE, 777))
        self.assertEqual(self.report("working")["state"], "working")
        self.assertEqual(self.report("compacted")["state"], "working")  # a compaction keeps the turn's state
        self.assertEqual(self.report("blocked", message="permission: Bash")["message"], "permission: Bash")
        self.assertEqual(self.report("idle")["state"], "idle")
        self.report("ended", session="OTHER")  # another session's end never forgets this pane
        self.assertEqual(len(of_tmux.agents()), 1)
        self.report("ended")
        self.assertEqual(of_tmux.agents(), [])

    def test_a_new_session_in_the_pane_replaces_the_old(self):
        self.report("started")
        d = of_tmux.report("working", "S2", env=ENV, pid=777, now=2000.0)  # hook installed mid-session: no start seen
        self.assertEqual((d["session"], d["state"], d["started"]), ("S2", "working", 2000.0))

    def test_agents_are_herdr_shaped_and_only_live_ones_count(self):
        self.report("started")
        of_tmux.report("working", "S4", env=dict(ENV, TMUX_PANE="%4"), pid=778, now=1000.0)   # its process died
        of_tmux.report("idle", "S5", env=dict(ENV, TMUX_PANE="%5"), pid=777, now=1000.0)      # its pane closed
        rows = of_tmux.agents()
        self.assertEqual([(a["pane_id"], a["agent_status"], a["agent_session"]["value"]) for a in rows],
                         [(PANE, "idle", "S1")])
        self.assertEqual(rows[0]["runtime"], "tmux")
        left = sorted(os.listdir(os.path.join(self.tmp.name, "tmux-panes")))
        self.assertEqual(left, ["default_3.json", "default_5.json"])  # a dead process's report is removed

    def test_no_tmux_installed_means_no_agents(self):
        self.report("started")

        def missing(argv, **k):
            raise FileNotFoundError("tmux")
        of_relay.subprocess.run = missing
        self.assertEqual(of_tmux.agents(), [])
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "tmux-panes", "default_3.json")))  # kept

    def test_a_dim_suggestion_in_a_combined_sgr_is_an_empty_box(self):
        self.box = screen("what is the status of the limits?", combined_dim=True)
        self.report("started")
        self.assertEqual(of_relay.pane_input(PANE), "")
        self.box = screen("a person's half-typed words")
        self.assertEqual(of_relay.pane_input(PANE), "a person's half-typed words")
        self.assertEqual(of_tmux._dim_only("\x1b[1;2mx\x1b[22my\x1b[38;2;1;2;3mz"), "\x1b[2mx\x1b[22myz")

    def test_the_relay_types_into_an_idle_tmux_agent(self):
        self.report("started")
        sleeps = []
        saved = of_tmux.time.sleep
        of_tmux.time.sleep = sleeps.append
        try:
            ok, err = of_relay.herdr_deliver(PANE, "hello from @of_platform", "S1", "ofb:of_x")
        finally:
            of_tmux.time.sleep = saved
        self.assertEqual((ok, err), (True, None))
        sock = "/private/tmp/tmux-501/default"
        typed = [c for c in self.calls if c[3] in ("load-buffer", "paste-buffer", "send-keys")]
        self.assertEqual([c[3] for c in typed], ["load-buffer", "paste-buffer", "send-keys"])
        self.assertEqual(typed[1][:4] + typed[1][4:5], ["tmux", "-S", sock, "paste-buffer", "-p"])
        self.assertEqual(typed[2][-3:], ["-t", "%3", "Enter"])
        self.assertEqual(sleeps, [of_tmux.SUBMIT_DELAY_S])
        ev = json.loads(self.conn.execute("SELECT payload FROM event WHERE name = 'of.injected_prompt'").fetchone()[0])
        self.assertEqual((ev["target_session"], ev["mentions_neutralized"]), ("S1", 1))  # declared before typing

    def test_text_goes_through_the_buffer_framed_and_neutralized(self):
        self.report("started")
        texts = []
        saved = of_relay.subprocess.run

        def run(argv, **k):
            if "load-buffer" in argv:
                texts.append(k.get("input"))
            return saved(argv, **k)
        of_relay.subprocess.run = run
        of_tmux.time.sleep, sl = (lambda s: None), of_tmux.time.sleep
        try:
            of_relay.herdr_deliver(PANE, "read @notes", "S1", "ofb:of_x")
        finally:
            of_tmux.time.sleep = sl
        self.assertEqual(texts, ["Message from ofb:of_x (via of, authority: none)\nread ＠notes"])

    def test_control_characters_never_reach_the_pane_as_keys(self):
        """A message holding ESC [201~ would end the paste early; what follows would be keys (Shift+Tab: ESC [Z)."""
        self.report("started")
        texts = []
        saved = of_relay.subprocess.run

        def run(argv, **k):
            if "load-buffer" in argv:
                texts.append(k.get("input"))
            return saved(argv, **k)
        of_relay.subprocess.run = run
        of_tmux.time.sleep, sl = (lambda s: None), of_tmux.time.sleep
        try:
            of_relay.herdr_deliver(PANE, "a\x1b[201~\x1b[Z\rb\x07\tc\r\nd\x9b", "S1", "ofb:of_x")
        finally:
            of_tmux.time.sleep = sl
        self.assertTrue(texts[0].endswith("\na[201~[Z\nb\tc\nd"), repr(texts[0]))
        self.assertNotIn("\x1b", texts[0])

    def test_a_busy_blocked_or_typed_into_agent_defers(self):
        self.report("started")
        self.report("working")
        clock = iter(range(100))
        saved = of_tmux.wait
        of_tmux.wait = lambda pane, until, t: saved(pane, until, t, clock=lambda: next(clock), sleep=lambda s: None)
        try:
            res = of_relay.herdr_deliver(PANE, "hi", "S1", "ofb:of_x", wait_s=3)
            self.assertEqual(res[0], "deferred")
            self.assertIn("working", res[1])
            self.report("blocked", message="permission: Bash")
            res = of_relay.herdr_deliver(PANE, "hi", "S1", "ofb:of_x", wait_s=3, working_ok=True)
            self.assertEqual(res[0], "deferred")  # never into a dialog, even for an old message
            self.report("idle")
            self.box = screen("a person's half-typed words")
            res = of_relay.herdr_deliver(PANE, "hi", "S1", "ofb:of_x", wait_s=3)
            self.assertEqual(res, ("deferred", "the input box holds text (27 chars)"))
            self.box = screen("Do you want to proceed?\n enter to confirm")
            self.assertEqual(of_relay.herdr_deliver(PANE, "hi", "S1", "ofb:of_x", wait_s=3),
                             ("deferred", "a dialog is open in the pane"))
        finally:
            of_tmux.wait = saved
        self.assertFalse([c for c in self.calls if "paste-buffer" in c])

    def test_a_pane_without_a_live_agent_fails_without_typing(self):
        res = of_relay.herdr_deliver("tmux:default:%9", "hi", "S1", "ofb:of_x", wait_s=1)
        self.assertEqual(res, (False, "no live agent in this tmux pane"))
        self.assertFalse([c for c in self.calls if "paste-buffer" in c])

    def test_the_relay_binds_a_tmux_agent_to_its_position(self):
        self.report("started")
        positions = [{"name": "of_x", "_org": "ofb", "holder": {"session": "S1"}}]

        def bind(ps, agents):
            pairs = [(p, a) for p in ps for a in agents
                     if (a.get("agent_session") or {}).get("value") == p["holder"]["session"]]
            return pairs, [], []
        local = of_relay.local_holders(positions, bind, of_tmux.agents())
        self.assertEqual(local["ofb:of_x"]["pane"], PANE)
        self.assertEqual((local["ofb:of_x"]["session_id"], local["ofb:of_x"]["state"]), ("S1", "idle"))


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = self.tmp.name
        self.env = {k: os.environ.get(k) for k in ("TMUX", "TMUX_PANE", "HERDR_PANE_ID")}
        os.environ.update({"TMUX": ENV["TMUX"], "TMUX_PANE": "%3"})
        os.environ.pop("HERDR_PANE_ID", None)
        self.saved_pid = of_tmux.claude_pid
        of_tmux.claude_pid = lambda *a, **k: 777

    def tearDown(self):
        of_tmux.claude_pid = self.saved_pid
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def test_the_hooks_states_reach_the_pane_report(self):
        import of_hook
        path = os.path.join(self.tmp.name, "tmux-panes", "default_3.json")
        of_hook._SESSION = "S1"
        of_hook.tmux_state("started")
        of_hook.herdr_state("working")
        self.assertEqual(json.load(open(path))["state"], "working")
        of_hook.herdr_state("blocked", "permission: Bash")
        self.assertEqual(json.load(open(path))["state"], "blocked")
        of_hook._SESSION = None  # a subagent's call
        of_hook.herdr_state("idle")
        self.assertEqual(json.load(open(path))["state"], "blocked")
        of_hook._SESSION = "S1"
        of_hook.tmux_state("ended")
        self.assertFalse(os.path.exists(path))

    def test_claude_pid_walks_up_to_claude(self):
        table = {10: "9 /bin/sh -c of hook Stop", 9: "8 /opt/homebrew/bin/claude --resume x", 8: "1 -zsh"}
        self.assertEqual(self.saved_pid(10, ps=lambda pid: table.get(pid, "")), 9)
        node = {10: "9 /bin/sh -c x", 9: "1 node /usr/local/lib/node_modules/@anthropic-ai/claude-code/cli.js"}
        self.assertEqual(self.saved_pid(10, ps=lambda pid: node.get(pid, "")), 9)  # an npm install
        other = {10: "9 /bin/sh -c x", 9: "8 node /srv/app.js", 8: "1 launchd"}
        self.assertIsNone(self.saved_pid(10, ps=lambda pid: other.get(pid, "")))
        npm = {10: "9 /bin/sh -c x", 9: "1 node /usr/local/bin/claude"}
        self.assertEqual(self.saved_pid(10, ps=lambda pid: npm.get(pid, "")), 9)
        self.assertIsNone(self.saved_pid(10, ps=lambda pid: ""))


if __name__ == "__main__":
    unittest.main()
