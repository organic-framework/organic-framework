"""of codex selftest (bin/of_codex_selftest.py; W3 of hand-over 9ee1db9f): each canary check against a sandbox that
refuses or lets through, with codex sandbox stood in by a fake run."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_codex_selftest as st  # noqa: E402

DOOR = {"position": "of_docs", "org": "ofb", "launch": "L-1", "session": "thr-1", "pid": 7,
        "cwd": tempfile.mkdtemp(prefix="selftest-cwd-")}


def sandbox(readable=False, writable=False, runs=True):
    """A stand-in for `codex sandbox …`: the control in the position's folder read when it runs; reads and writes
    elsewhere as the case says."""
    def run(argv, **kw):
        cmd = argv[argv.index("--") + 1:]
        if not runs:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="no sandbox")
        if cmd[0] == "cat" and os.path.dirname(cmd[1]) == DOOR["cwd"]:
            with open(cmd[1]) as fh:
                return subprocess.CompletedProcess(argv, 0, stdout=fh.read(), stderr="")
        if cmd[0] == "cat":
            if readable:
                with open(cmd[1]) as fh:
                    return subprocess.CompletedProcess(argv, 0, stdout=fh.read(), stderr="")
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="Operation not permitted")
        if cmd[0] == "sh":
            if writable:
                open(cmd[4], "w").close()
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="Read-only file system")
        raise AssertionError(cmd)
    return run


class GuardProbeTest(unittest.TestCase):
    def test_the_guard_probe_writes_nothing_of_the_live_session(self):
        """of_review on PR 640: the probe recorded a critical deny against the live position's session."""
        seen = {}

        def run(argv, input=None, env=None, **kw):
            seen.update(session=json.loads(input)["session_id"], state=env.get("OF_STATE"))
            return subprocess.CompletedProcess(argv, 0, stdout='{"decision": "deny"}', stderr="")
        self.assertTrue(st.check_guard(DOOR, "/of", run=run)[0])
        self.assertNotEqual(seen["session"], DOOR["session"])
        self.assertTrue(seen["state"] and not os.path.exists(seen["state"]))          # a private state, removed


class SelftestTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="selftest-")
        self.denied = os.path.join(self.d, "creds")
        os.makedirs(self.denied)
        self.fs = {":minimal": "read", self.denied: "deny", DOOR["cwd"]: "write", os.path.join(self.d, "absent"): "deny"}

    def test_the_canaries_are_unreadable_and_removed_whatever_happened(self):
        self.assertEqual(st.check_credentials("codex", DOOR, self.fs, run=sandbox())[0], True)
        ok, detail = st.check_credentials("codex", DOOR, self.fs, run=sandbox(readable=True))
        self.assertFalse(ok)
        self.assertIn(self.denied, detail)
        self.assertEqual(os.listdir(self.denied), [])                    # removed both times
        ok, detail = st.check_credentials("codex", DOOR, self.fs, run=sandbox(runs=False))
        self.assertFalse(ok)                                             # a sandbox that never ran proves nothing
        self.assertIn("control", detail)
        self.assertEqual(os.listdir(DOOR["cwd"]), [])

    def test_the_sandbox_runs_under_the_positions_own_layer_and_profile(self):
        argv = st.sandbox_argv("codex", "L-1", "/w", ["cat", "x"], seat="/seat")
        self.assertEqual(argv[:11], ["env", "CODEX_HOME=/seat", "codex", "sandbox", "-p", "of-L-1", "-P",
                                     "of_position", "-C", "/w", "--include-managed-config"])   # the seat's, never ours

    def test_the_checkout_takes_no_write(self):
        self.assertTrue(st.check_checkout("codex", DOOR, self.d, run=sandbox())[0])
        self.assertFalse(st.check_checkout("codex", DOOR, self.d, run=sandbox(runs=False))[0])
        self.assertFalse(st.check_checkout("codex", DOOR, self.d, run=sandbox(writable=True))[0])
        self.assertFalse([f for f in os.listdir(self.d) if f.startswith(st.CANARY)])

    def test_the_door_refuses_an_outsider_and_served_the_positions_own_status(self):
        """of_review on PR 640: a run started from the operator's terminal is never served, by the door's own rule."""
        import of_codex
        import of_door
        import of_events
        saved = of_events.STATE_DIR
        of_events.STATE_DIR = os.path.join(tempfile.mkdtemp(prefix="selftest-state-"), "of")   # never the real doors
        self.addCleanup(setattr, of_events, "STATE_DIR", saved)
        sock = os.path.join(of_codex.door_dir("L-1"), of_door.SOCKET)
        os.makedirs(os.path.dirname(sock), exist_ok=True)
        open(sock, "w").close()
        self.addCleanup(os.unlink, sock)
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE event (name TEXT, session_id TEXT, payload TEXT)")
        refuse = lambda argv, **kw: subprocess.CompletedProcess(  # noqa: E731
            argv, 2, stdout="", stderr="of: this door serves only the commands of its own Codex session")
        serve = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout="who", stderr="")  # noqa: E731
        self.assertFalse(st.check_door(conn, DOOR, "/of", run=refuse)[0])             # nothing of its own yet
        conn.execute("INSERT INTO event VALUES ('of.state_declared', 'thr-1', '{}')")
        self.assertTrue(st.check_door(conn, DOOR, "/of", run=refuse)[0])
        self.assertFalse(st.check_door(conn, DOOR, "/of", run=serve)[0])              # an outsider served: wrong

    def test_the_store_needs_a_prompt_a_turn_end_and_a_tool_call(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE event (name TEXT, session_id TEXT, payload TEXT)")
        conn.execute("CREATE TABLE transcript_record (session_id TEXT, harness TEXT, blob_sha256 TEXT)")
        blobs = {}
        import of_store
        saved = of_store.get_blob
        of_store.get_blob = lambda c, sha: blobs[sha]
        self.addCleanup(setattr, of_store, "get_blob", saved)
        ok, detail = st.check_store(conn, DOOR)
        self.assertFalse(ok)
        for n in ("of.session_start", "of.human_prompt", "of.turn_end"):
            conn.execute("INSERT INTO event VALUES (?, 'thr-1', '{}')", (n,))
        blobs["a"] = json.dumps({"type": "response_item", "payload": {"type": "function_call", "name": "exec"}})
        conn.execute("INSERT INTO transcript_record VALUES ('thr-1', 'codex', 'a')")
        self.assertTrue(st.check_store(conn, DOOR)[0])

    def test_the_pane_is_held_by_who_and_the_relay_with_this_session(self):
        rows = [{"position": "of_docs", "pane": "w1:p2"}]
        self.assertTrue(st.check_pane(DOOR, rows, {"ofb:of_docs": {"pane": "w1:p2", "session_id": "thr-1"}})[0])
        self.assertFalse(st.check_pane(DOOR, rows, {"ofb:of_docs": {"pane": "w1:p2", "session_id": "other"}})[0])

    def test_a_start_screen_is_blocked_with_nothing_typed(self):
        self.assertTrue(st.check_start_screen("blocked", "")[0])
        self.assertFalse(st.check_start_screen("blocked", "the brief")[0])
        self.assertTrue(st.check_start_screen("idle", "")[0])

    def test_the_relay_check_waits_for_its_delivery(self):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE event (name TEXT, session_id TEXT, payload TEXT)")
        t = [0]
        self.assertFalse(st.check_relay(conn, DOOR, "m-1", timeout=10, sleep=lambda s: t.__setitem__(0, t[0] + s),
                                        clock=lambda: t[0])[0])
        conn.execute("INSERT INTO event VALUES ('of.agent_message_in', 'thr-1', ?)",
                     (json.dumps({"msg_id": "m-1", "delivery": "delivered"}),))
        self.assertTrue(st.check_relay(conn, DOOR, "m-1", timeout=10)[0])

    def test_the_report_fails_on_any_failed_check(self):
        import io
        out = io.StringIO()
        self.assertEqual(st.report([("a", True, "x"), ("b", False, "y")], out), 1)
        self.assertIn("FAIL  b", out.getvalue())


if __name__ == "__main__":
    unittest.main()
