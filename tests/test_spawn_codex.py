"""of spawn and of resume-command for a Codex position (hand-over 9ee1db9f, W4; bin/of_spawn.py codex_*,
bin/of_resume.py codex_command): its seat, a layer of its own with its start context whole, the launch's door, the
brief as its first prompt, declared for its pane, and Codex's own session id from its SessionStart hook."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_events  # noqa: E402
import of_resume  # noqa: E402
import of_spawn  # noqa: E402

POS = {"name": "of_docs", "_org": "ofb", "system": "s", "status": "active",
       "holder": {"runtime": "codex"}, "brief": "registry/briefs/of_docs.md"}


def bind(positions, agents):
    return [], [], []


class CodexSpawnTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = os.path.realpath(self.tmp.name)
        self.cwd = os.path.join(self.d, "work")
        self.seat = os.path.join(self.d, "seat")
        os.makedirs(self.cwd)
        os.makedirs(self.seat)
        self.state = os.path.join(self.d, "state")
        os.makedirs(self.state)
        saved = of_events.STATE_DIR
        of_events.STATE_DIR = self.state
        self.addCleanup(setattr, of_events, "STATE_DIR", saved)
        for name, value in (("place", lambda p, w: ("ofb", "docs")), ("brief_text", lambda p, r, s=False: "Read your brief."),
                            ("prepare_folder", lambda *a, **k: None)):
            patch = mock.patch.object(of_spawn, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def plan(self, **kw):
        return of_spawn.codex_plan(POS, [POS], [], bind, {}, [self.cwd], self.d, None, seat=self.seat, cwd=self.cwd,
                                   **kw)

    def test_the_plan_names_the_seat_the_layer_the_folder_and_the_brief(self):
        self.assertEqual(of_spawn.harness_of(POS), "codex")
        self.assertEqual(of_spawn.harness_of({"holder": {"runtime": "claude-code"}}), "claude")
        pl = self.plan(launch="L-1", model="gpt-x")
        self.assertEqual((pl["harness"], pl["config_dir"], pl["session"]), ("codex", self.seat, None))
        cmd = pl["command"]
        for part in (f"CODEX_HOME={self.seat}", "OF_LAUNCH=L-1", "OF_SPAWNED=1", f"codex -C {self.cwd} -p of-L-1",
                     "--no-alt-screen", "'Read your brief.'"):
            self.assertIn(part, cmd)
        self.assertIn("runtime: codex", of_spawn.registry_line(dict(pl, session="T-1"), "p1"))
        with self.assertRaisesRegex(of_spawn.SpawnError, "codex_plan starts it"):   # never as Claude Code
            of_spawn.plan(POS, [POS], [], bind, {}, [self.cwd], self.d, None, config_dir=self.seat, cwd=self.cwd)

    def test_with_no_seat_it_says_so(self):
        with self.assertRaisesRegex(of_spawn.SpawnError, "no Codex seat"):
            of_spawn.codex_seat(None, rows=[], choose=lambda rows, public: None)
        self.assertEqual(of_spawn.codex_seat(None, rows=[{"home": self.seat}], choose=lambda rows, public: rows[0]),
                         self.seat)

    def test_prepare_writes_the_door_and_a_layer_with_the_whole_start_context(self):
        pl = self.plan(launch="L-2")
        with mock.patch("of_door.sessions_dir", return_value=os.path.join(self.d, "of-doors")):
            path = of_spawn.codex_prepare(pl, context=lambda name, cwd: "THE START CONTEXT " + "x" * 20000,
                                          tool_layer=lambda *a, **k: {}, log=lambda m: None)
            door = os.path.join(self.d, "of-doors", "L-2")
            with open(os.path.join(door, "door.json")) as fh:
                self.assertEqual(json.load(fh), {"position": "of_docs", "org": "ofb", "cwd": self.cwd, "launch": "L-2"})
            self.assertEqual(oct(os.stat(door).st_mode & 0o777), "0o700")
            with self.assertRaises(FileExistsError):   # a launch's folder is always new
                of_spawn.codex_prepare(pl, context=lambda n, c: "x", tool_layer=lambda *a, **k: {}, log=lambda m: None)
        self.assertEqual(path, os.path.join(self.seat, "of-L-2.config.toml"))
        with open(path) as fh:
            text = fh.read()
        self.assertIn("THE START CONTEXT", text)
        self.assertIn("x" * 20000, text)                                     # whole: no cap on developer_instructions
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")

    def test_run_declares_the_brief_for_its_pane_and_takes_codexs_own_session_id(self):
        pl = self.plan(launch="L-3")
        calls = []

        def herdr(*a):
            calls.append(a)
            if a[:2] == ("workspace", "list"):
                return {"workspaces": []}
            if a[:2] == ("tab", "list"):
                return {"tabs": []}
            if a[:2] == ("pane", "list"):
                return {"panes": []}
            return {"result": {"pane": {"pane_id": "p9"}}}
        with mock.patch("of_capacity.place_command", return_value=(["tab", "create"], None)), \
                mock.patch("of_capacity.first_pane_id", return_value="p9"):
            pane = of_spawn.codex_run(pl, herdr, rename=lambda pane, name, sid: name, log=lambda m: None,
                                      prepare=lambda pl, log=None: "layer", session=lambda launch, t: "thread-9")
        self.assertEqual((pane, pl["session"]), ("p9", "thread-9"))
        self.assertIn(("pane", "run", "p9", pl["command"]), calls)
        with open(os.path.join(self.state, "injections.jsonl")) as fh:
            decl = json.loads(fh.read().splitlines()[-1])
        import hashlib
        self.assertEqual((decl["pane"], decl["sha256"]), ("p9", hashlib.sha256(b"Read your brief.").hexdigest()))
        self.assertGreater(decl["window"], 60)                                # Codex takes a while to start
        with self.assertRaisesRegex(of_spawn.SpawnError, "did not report its id"):
            with mock.patch("of_capacity.place_command", return_value=(["tab", "create"], None)), \
                    mock.patch("of_capacity.first_pane_id", return_value="p9"):
                of_spawn.codex_run(dict(pl, launch="L-4"), herdr, rename=lambda *a: "", log=lambda m: None,
                                   prepare=lambda pl, log=None: "layer", session=lambda launch, t: None)

    def test_resume_finds_the_seat_by_its_rollout_and_starts_a_fresh_layer(self):
        sid = "019a0000-0000-7000-8000-00000000c0de"
        day = os.path.join(self.seat, "sessions", "2026", "11", "24")
        os.makedirs(day)
        open(os.path.join(day, f"rollout-2026-11-24T05-00-00-{sid}.jsonl"), "w").close()
        made = []
        line = of_resume.codex_command(sid, POS, cwd=self.cwd, homes=[os.path.join(self.d, "other"), self.seat],
                                       prepare=lambda pl, log=None: made.append(pl), launch="L-5")
        self.assertIn(f"codex resume {sid} -C {self.cwd} -p of-L-5 --no-alt-screen", line)
        self.assertIn(f"CODEX_HOME={self.seat}", line)
        self.assertEqual(made[0]["launch"], "L-5")                             # a fresh layer and door
        self.assertIsNone(of_resume.codex_command("019a-none", POS, cwd=self.cwd, homes=[self.seat],
                                                  prepare=lambda pl, log=None: None))
        self.assertIsNone(of_resume.codex_home_of("../x", [self.seat]))


if __name__ == "__main__":
    unittest.main()
