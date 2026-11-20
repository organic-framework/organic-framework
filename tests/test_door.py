"""The door of a sandboxed session (bin/of_door.py; RFC 0008 §4.5, of_architect's conditions, msg 01a1f9ee-5080)."""

import importlib.machinery
import importlib.util
import json
import os
import socket
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_door  # noqa: E402


def check(argv, org="orgA"):
    try:
        return of_door.check(argv, org)[0]
    except of_door.Refused as e:
        return ("refused", str(e))


class PolicyTest(unittest.TestCase):
    """Condition 1: the arguments as of's parser reads them; condition 2: no option the caller names a path with."""

    def test_the_allowed_commands_pass_as_written(self):
        for argv in (["status", "working", "--note", "x"], ["status", "waiting", "--on", "of_server"],
                     ["ask", "Run it?", "--options", "a|b"], ["send", "of_server", "hi", "--fyi"],
                     ["inbox", "--mine"], ["inbox", "--mine", "--take", "all"], ["prompts", "--mine", "--last", "3"],
                     ["handover", "accept", "a18341af"], ["status", "--help"]):
            with self.subTest(argv=argv):
                self.assertEqual(check(argv), argv)

    def test_every_read_is_limited_to_the_callers_organization(self):
        for argv, want in ((["who"], ["who", "--org", "orgA"]),
                           (["who", "--org", "other"], ["who", "--org", "orgA"]),
                           (["who", "--org=other"], ["who", "--org", "orgA"]),
                           (["asks", "--to-me"], ["asks", "--org", "orgA", "--to-me"]),
                           (["handover", "list"], ["handover", "--org", "orgA", "list"]),
                           (["loop", "show", "w1"], ["loop", "--org", "orgA", "show", "w1"]),
                           (["event", "show", "01a1f9ee-5080"], ["event", "--org", "orgA", "show", "01a1f9ee-5080"])):
            with self.subTest(argv=argv):
                self.assertEqual(check(argv), want)

    def test_another_caller_a_machine_view_or_another_command_is_refused(self):
        for argv in (["status", "working", "--from", "of_x"], ["send", "of_x", "hi", "--from", "of_y"],
                     ["inbox"], ["inbox", "move", "a", "--to", "b"], ["asks", "answered", "AS-1"],
                     ["asks", "--chat"], ["asks", "--panel"], ["event", "injected_prompt", "--text", "x"],
                     ["hook", "PreToolUse"], ["db", "stats"], ["spawn", "of_x"], ["org", "set", "a", "hold", "on"],
                     ["prompts"], ["prompts", "--all"], ["send", "of_x", "hi", "--grant", "g"], ["status", "workin"],
                     ["ask", "q", "--escalates", "AS-1"], ["ask", "q", "--from", "of_x"], []):
            with self.subTest(argv=argv):
                self.assertEqual(check(argv)[0], "refused")

    def test_no_allowed_option_is_read_as_a_path(self):
        """Condition 2, for the review: each allowed option's value is text of stores or matches, never opens."""
        for cmd, rule in of_door.ALLOW.items():
            for dest in rule["options"]:
                self.assertNotRegex(dest, r"file|path_in|schema|prompt_file", f"{cmd} --{dest}")
        self.assertTrue(of_door.NEVER >= {"sender", "session", "pane", "prompt"})


class ServeTest(unittest.TestCase):
    """The listener's side: only its own Codex session's processes, the request checked, the caller forced."""

    def serve(self, argv, door_pid, stdin=""):
        a, b = socket.socketpair()
        got = {}

        def runner(argv, door, stdin):
            got.update(argv=argv, door=door, stdin=stdin)
            return {"stdout": "ran\n", "stderr": "", "code": 0}
        door = {"position": "of_docs", "org": "orgA", "session": "thread-1", "pid": door_pid, "cwd": "/"}
        t = threading.Thread(target=of_door.serve_one, args=(b, door, None, runner))
        t.start()
        of_door._send(a, {"argv": argv, "stdin": stdin})
        out = of_door._recv(a, 1 << 20)
        t.join(10)
        a.close()
        return out, got

    def test_a_process_under_the_session_is_served_as_that_session(self):
        out, got = self.serve(["who", "--org", "other"], os.getpid(), stdin="body")
        self.assertEqual(out, {"stdout": "ran\n", "stderr": "", "code": 0})
        self.assertEqual(got["argv"], ["who", "--org", "orgA"])
        self.assertEqual((got["door"]["session"], got["door"]["position"], got["stdin"]), ("thread-1", "of_docs", "body"))

    def test_a_process_outside_the_session_is_refused(self):
        out, got = self.serve(["who"], 2 ** 22 - 7)
        self.assertEqual(out["code"], 2)
        self.assertIn("its own Codex session", out["stderr"])
        self.assertEqual(got, {})

    def test_a_refused_request_never_runs(self):
        out, got = self.serve(["db", "stats"], os.getpid())
        self.assertEqual(out["code"], 2)
        self.assertEqual(got, {})

    def test_the_run_forces_the_caller(self):
        env = {}

        class R:
            stdout, stderr, returncode = "o", "e", 0
        import subprocess
        from unittest import mock

        def fake(cmd, **kw):
            env.update(kw["env"])
            return R()
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "someone-else", "OF_POSITION": "of_x",
                                          "SSH_AUTH_SOCK": "/tmp/agent"}), \
                mock.patch.object(subprocess, "run", side_effect=fake):
            of_door.run(["who"], {"session": "thread-1", "position": "of_docs", "cwd": "/"})
        self.assertEqual((env["CLAUDE_CODE_SESSION_ID"], env["OF_POSITION"]), ("thread-1", "of_docs"))
        self.assertNotIn("SSH_AUTH_SOCK", env)
        self.assertEqual(env["OF_DOOR_CALLER"], "1")


class WireTest(unittest.TestCase):
    def test_forward_prints_what_the_door_answers(self):
        import io
        from unittest import mock
        d = tempfile.mkdtemp(prefix="door-")
        path = os.path.join(d, "of.sock")
        srv = socket.socket(socket.AF_UNIX)
        srv.bind(path)
        srv.listen(1)

        def one():
            c, _ = srv.accept()
            req = of_door._recv(c, 1 << 20)
            of_door._send(c, {"stdout": json.dumps(req), "stderr": "warn\n", "code": 3})
            c.close()
        t = threading.Thread(target=one)
        t.start()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
            code = of_door.forward(path, ["status", "working"], stdin="")
        t.join(10)
        srv.close()
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(out.getvalue())["argv"], ["status", "working"])
        self.assertEqual(err.getvalue(), "warn\n")

    def test_no_door_says_so(self):
        import io
        from unittest import mock
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            self.assertEqual(of_door.forward("/nonexistent/of.sock", ["who"], stdin=""), 2)
        self.assertIn("did not answer", err.getvalue())


class DoorsTest(unittest.TestCase):
    def test_a_folder_whose_pid_is_not_its_codex_opens_no_door(self):
        base = tempfile.mkdtemp(prefix="sessions-")
        os.makedirs(os.path.join(base, "L-1"))
        with open(os.path.join(base, "L-1", of_door.DOOR_FILE), "w") as fh:
            json.dump({"position": "of_docs", "org": "orgA", "cwd": "/", "launch": "L-1"}, fh)
        with open(os.path.join(base, "L-1", of_door.CODEX_FILE), "w") as fh:
            json.dump({"session": "thread-1", "pid": os.getpid()}, fh)    # this test's python, not codex -p of-L-1
        self.assertEqual(of_door.read_doors(base), {})


class EventShowTest(unittest.TestCase):
    """Condition 3: asks, their answers and amendments, hand-overs and a person's prompt, in the caller's org."""

    def setUp(self):
        loader = importlib.machinery.SourceFileLoader("of_cli_door", os.path.join(HERE, "..", "bin", "of"))
        spec = importlib.util.spec_from_loader("of_cli_door", loader)
        self.of = importlib.util.module_from_spec(spec)
        loader.exec_module(self.of)
        import sqlite3
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE event (event_id TEXT PRIMARY KEY, ts TEXT, name TEXT, position TEXT, org TEXT, "
                          "payload TEXT)")
        rows = (("01a1aaaa-0001-7000-8000-000000000001", "of.agent_question", "of_docs", "orgA"),
                ("01a1aaaa-0002-7000-8000-000000000002", "of.turn_end", "of_docs", "orgA"),
                ("01a1aaaa-0003-7000-8000-000000000003", "of.handover_proposed", "b_x", "orgB"))
        for eid, name, pos, org in rows:
            self.conn.execute("INSERT INTO event VALUES (?, '2026-11-20T00:00:00Z', ?, ?, ?, '{\"text\": \"t\"}')",
                              (eid, name, pos, org))
        self.positions = [{"name": "of_docs", "_org": "orgA"}, {"name": "b_x", "_org": "orgB"}]

    def test_an_ask_is_shown_and_other_kinds_or_organizations_are_not(self):
        ev = self.of.event_show(self.conn, "01a1aaaa-0001", "orgA", self.positions)
        self.assertEqual((ev["name"], ev["payload"]), ("of.agent_question", {"text": "t"}))
        with self.assertRaises(self.of.OfError):
            self.of.event_show(self.conn, "01a1aaaa-0002", "orgA", self.positions)     # a hook record
        with self.assertRaises(self.of.OfError):
            self.of.event_show(self.conn, "01a1aaaa-0003", "orgA", self.positions)     # another organization
        with self.assertRaises(self.of.OfError):
            self.of.event_show(self.conn, "01a1aaaa", "orgA", self.positions)          # too short to name one
        with self.assertRaises(self.of.OfError):
            self.of.event_show(self.conn, "01a1aaaa-000", "orgA", self.positions)      # more than one


if __name__ == "__main__":
    unittest.main()
