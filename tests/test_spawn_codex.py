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

REAL_PRIVATE_OK = of_spawn.codex_seat_private_ok   # the setUp stands in for these two; their own tests read the real
REAL_REQ_OK = of_spawn.codex_requirements_ok
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
                            ("prepare_folder", lambda *a, **k: None),
                            ("codex_requirements_ok", lambda path=None: (True, None)),
                            ("codex_seat_private_ok", lambda home, rows=None, training=None: True)):
            patch = mock.patch.object(of_spawn, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def plan(self, **kw):
        return of_spawn.codex_plan(POS, [POS], [], bind, {}, [self.cwd], self.d, None, seat=self.seat, cwd=self.cwd,
                                   **kw)

    def test_no_start_and_no_resume_where_the_requirements_file_runs_no_guard(self):
        """of_review on PR 634: Codex started with its brief before anything checked the managed requirements file."""
        with mock.patch.object(of_spawn, "codex_requirements_ok", return_value=(False, "no managed requirements file")):
            with self.assertRaisesRegex(of_spawn.SpawnError, "no managed requirements file"):
                self.plan(launch="L-9")
        with tempfile.TemporaryDirectory() as t:
            req = os.path.join(t, "requirements.toml")
            self.assertFalse(REAL_REQ_OK(req)[0])
            import of_codex
            with open(req, "w") as fh:
                fh.write(of_codex.requirements_toml())
            self.assertEqual(REAL_REQ_OK(req), (True, None))
            with open(req, "w") as fh:
                fh.write("allow_managed_hooks_only = true\n")
            self.assertIn("names no OF guard", REAL_REQ_OK(req)[1])

    def test_a_seat_is_chosen_from_the_seats_rows_and_a_named_one_is_read_too(self):
        """of_review on PR 634: codex_status gives one dict, whose keys the choice walked; a seat given with
        --config-dir skipped the training reading."""
        import of_capacity
        rows = [{"home": self.seat, "account": "acct-1", "used_percent": 10}]
        with mock.patch.object(of_capacity, "codex_seat_rows", return_value=rows) as got_rows:
            self.assertEqual(of_spawn.codex_seat(None, choose=lambda r, public: r[0]), self.seat)
            got_rows.assert_called_once()
        ok = REAL_PRIVATE_OK
        with mock.patch.object(of_capacity, "training_verified", return_value="on"):
            self.assertFalse(ok(self.seat, rows=rows, training={}))
        with mock.patch.object(of_capacity, "training_verified", return_value="off"):
            self.assertTrue(ok(self.seat, rows=rows, training={}))
            self.assertFalse(ok(os.path.join(self.d, "other-seat"), rows=rows, training={}))   # no row: unread
        seen = {}
        with mock.patch.object(of_capacity, "codex_seat_for",
                               side_effect=lambda r, public=True, training=None: seen.setdefault("t", training) and r[0]):
            of_spawn.codex_seat(None, rows=rows, training={"acct-1": "off"})
        self.assertEqual(seen["t"], {"acct-1": "off"})                       # the caller's map reaches the choice
        with mock.patch.object(of_spawn, "codex_seat_private_ok", return_value=False):
            with self.assertRaisesRegex(of_spawn.SpawnError, "not verified off"):
                of_spawn.codex_seat(self.seat, rows=rows)

    def test_the_line_is_one_command_and_codex_json_is_read_safely(self):
        """of_review's notes on PR 634: `exec cd …` is no command, and a FIFO or a record with no pid was waited on or
        taken."""
        pl = self.plan(launch="L-7")
        self.assertTrue(pl["command"].startswith("env CODEX_HOME="))
        import of_codex
        d = of_codex.door_dir("L-7")
        os.makedirs(d, exist_ok=True)
        f = os.path.join(d, "codex.json")
        with open(f, "w") as fh:
            json.dump({"session": "s-1"}, fh)                       # no pid: not taken
        self.assertIsNone(of_spawn.codex_session("L-7", 0, sleep=lambda s: None))
        os.unlink(f)
        os.mkfifo(f)                                                 # a FIFO: never waited on
        self.assertIsNone(of_spawn.codex_session("L-7", 0, sleep=lambda s: None))
        os.unlink(f)
        with open(f, "w") as fh:
            json.dump({"session": "s-1", "pid": 42}, fh)
        self.assertEqual(of_spawn.codex_session("L-7", 0, sleep=lambda s: None), "s-1")

    def test_the_positions_folder_is_untrusted_in_its_seat(self):
        """RFC 0008, the table's first row (of_review on PR 634): no project layer loads from the folder's .codex/."""
        import tomllib
        cfg = os.path.join(self.seat, "config.toml")
        with open(cfg, "w") as fh:
            fh.write('model = "x"\n\n[projects."%s"]\ntrust_level = "trusted"\n\n[other]\nk = 1\n' % self.cwd)
        of_spawn.codex_untrust(self.seat, self.cwd)
        with open(cfg, "rb") as fh:
            doc = tomllib.load(fh)
        self.assertEqual(doc["projects"][self.cwd]["trust_level"], "untrusted")
        self.assertEqual((doc["model"], doc["other"]["k"]), ("x", 1))
        self.assertFalse(self.plan(launch="L-8")["trusted"])

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


SCREEN = """  Folder access

  /Users/alex/work/organic-framework

  Config, hooks, and exec policies from
  untrusted folders stay disabled. Trusted
  project folders can still contribute
  settings. Skills still load, and tools
  follow your permission settings. Opening
  will not change saved trust.

\u203a 1. Open restricted
  2. Back to Agent Command Center

  enter continue \u00b7 esc back
"""   # Codex 0.170.0's screen as herdr read it on the agent host, 2026-11-25


class FolderAccessTest(unittest.TestCase):
    """of_architect, message 01a20f55-3dcb, with of_server's conditions (01a20f5a-1acd): of spawn and of resume-command
    answer Codex's folder-access screen with its selected '1. Open restricted', only for its whole text of a version
    read here, read again just before one key and once after it, with no retry; anything else stays a block."""

    def setUp(self):
        self.folder = "/Users/alex/work/organic-framework"
        self.real = mock.patch.object(of_spawn.os.path, "realpath", lambda p: p)
        self.real.start()
        self.addCleanup(self.real.stop)

    def pane(self, screens, after="\u203a Ask Codex\n"):
        """A herdr stand-in: SCREENS read in turn (the last one kept) until a key, then AFTER."""
        state = {"reads": list(screens), "sent": []}

        def herdr(*a):
            if a[:2] == ("pane", "read"):
                if state["sent"]:
                    return {"read": {"text": after}}
                return {"read": {"text": state["reads"].pop(0) if len(state["reads"]) > 1 else state["reads"][0]}}
            state["sent"].append(a)
            return {}
        return herdr, state["sent"]

    def answer(self, herdr):
        with mock.patch("of_events.emit") as emit:
            got = of_spawn.answer_folder_access(herdr, "p9", self.folder, "0.170.0", log=lambda m: None,
                                                sleep=lambda s: None)
        return got, emit

    def test_only_this_text_for_this_folder_and_version_is_the_screen(self):
        self.assertTrue(of_spawn.folder_access_screen(SCREEN, self.folder, "0.170.0"))
        self.assertFalse(of_spawn.folder_access_screen(SCREEN, "/Users/alex/other", "0.170.0"))
        self.assertFalse(of_spawn.folder_access_screen(SCREEN, self.folder, "0.171.0"))      # a version not read
        self.assertFalse(of_spawn.folder_access_screen(SCREEN.replace("\u203a 1.", "  1."), self.folder, "0.170.0"))
        self.assertFalse(of_spawn.folder_access_screen("Do you trust the files in this folder?", self.folder,
                                                       "0.170.0"))

    def test_an_option_one_that_trusts_the_folder_gets_no_key(self):
        trusting = SCREEN.replace("1. Open restricted", "1. Trust this folder")
        herdr, sent = self.pane([trusting])
        got, emit = self.answer(herdr)
        self.assertIsNone(got)
        self.assertEqual(sent, [])
        emit.assert_not_called()

    def test_one_key_on_the_whole_screen_read_twice_and_it_goes(self):
        herdr, sent = self.pane([SCREEN, SCREEN])
        got, emit = self.answer(herdr)
        self.assertEqual(got, "answered")
        self.assertEqual(sent, [("pane", "send-keys", "p9", "enter")])
        self.assertEqual(emit.call_args_list[0][0][0], "injected_prompt")
        self.assertIn("Open restricted", emit.call_args_list[0][0][1]["reason"])

    def test_a_screen_that_changes_before_the_key_gets_none(self):
        herdr, sent = self.pane([SCREEN, SCREEN + "\nsomething new"])
        got, _ = self.answer(herdr)
        self.assertEqual(got, "left")
        self.assertEqual(sent, [])

    def test_a_screen_still_there_after_the_key_is_left_with_no_retry(self):
        herdr, sent = self.pane([SCREEN, SCREEN], after=SCREEN)
        got, _ = self.answer(herdr)
        self.assertEqual(got, "left")
        pl = {"launch": "L-1", "cwd": self.folder}
        t = [0.0]
        with mock.patch("of_events.emit"), mock.patch.object(of_spawn, "codex_session", lambda l, s: None), \
                mock.patch.object(of_spawn, "answer_folder_access",
                                  side_effect=lambda *a, **k: "left") as ans:
            self.assertIsNone(of_spawn.wait_codex_session(herdr, "p9", pl, 30, log=lambda m: None,
                                                          version="0.170.0",
                                                          clock=lambda: t.__setitem__(0, t[0] + 5) or t[0]))
        self.assertEqual(ans.call_count, 1)                                  # tried once, never again
        self.assertEqual(sent, [("pane", "send-keys", "p9", "enter")])

    def test_the_folder_stays_untrusted_after_the_answer(self):
        seat = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(seat, ignore_errors=True))
        self.real.stop()
        folder = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
        of_spawn.codex_untrust(seat, folder)
        screen = SCREEN.replace("/Users/alex/work/organic-framework", os.path.realpath(folder))
        herdr, sent = self.pane([screen, screen])
        with mock.patch("of_events.emit"):
            got = of_spawn.answer_folder_access(herdr, "p9", folder, "0.170.0", log=lambda m: None,
                                                sleep=lambda s: None)
        key = os.path.realpath(folder)
        self.real.start()
        self.assertEqual(got, "answered")
        import tomllib
        with open(os.path.join(seat, "config.toml"), "rb") as fh:
            got = tomllib.load(fh)["projects"][key]["trust_level"]
        self.assertEqual(got, "untrusted")

    def test_a_resume_watcher_waits_for_the_screen_and_answers_it_once(self):
        t = [0.0]
        herdr, sent = self.pane(["starting", "starting", SCREEN, SCREEN])
        with mock.patch("of_events.emit"):
            self.assertTrue(of_spawn.watch_folder_access("p9", self.folder, timeout=60, herdr=herdr,
                                                         version="0.170.0", log=lambda m: None,
                                                         sleep=lambda s: t.__setitem__(0, t[0] + s),
                                                         clock=lambda: t[0]))
            self.assertFalse(of_spawn.watch_folder_access("p9", self.folder, timeout=60, herdr=herdr,
                                                          version="0.999.0", log=lambda m: None))
        self.assertEqual(sent, [("pane", "send-keys", "p9", "enter")])


if __name__ == "__main__":
    unittest.main()
