"""The relay watched like of-server (docs/health.md §5; of_architect's hand-over 154a0cbf): a heartbeat naming every
loop, a watchdog that dumps and restarts a relay whose loops stopped, and its launchd agent at Standard priority."""

import json
import os
from pathlib import Path
import plistlib
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_events  # noqa: E402
import of_init  # noqa: E402
import of_relay_client as R  # noqa: E402


class StateDir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = self.tmp.name

    def tearDown(self):
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()


class HeartbeatTest(StateDir):
    def test_fresh_stale_and_missing(self):
        self.assertEqual(R.relay_healthy()[0:1], (False,))                       # no heartbeat yet
        now = 1_800_000_000.0
        R.write_heartbeat({"presence": now, "poll http://s": now - 20}, now=now)
        self.assertEqual(R.relay_healthy(now + 30), (True, os.getpid(), "ok"))
        R.write_heartbeat({"presence": now, "poll http://s": now - R.STALE_S - 1}, now=now)
        ok, pid, detail = R.relay_healthy(now)
        self.assertEqual((ok, detail), (False, "loops behind: poll http://s"))
        self.assertFalse(R.relay_healthy(now + R.STALE_S + 1)[0])               # the presence loop stopped too

    def test_run_writes_the_heartbeat_from_its_loops(self):
        class Client:
            def put_presence(self, body):
                return {}

            def poll_messages(self, wait):
                time.sleep(0.05)
                return {"messages": []}
        with mock.patch.object(R, "org_drains", lambda default: {}), \
                mock.patch.object(R, "record_queues", lambda *a, **k: None), \
                mock.patch.object(R, "PRESENCE_CHECK", 0.1):
            stop, threads = R.run(lambda: {}, lambda u: Client(), {}, "http://s", log=lambda m: None)
            try:
                deadline = time.time() + 5
                while time.time() < deadline:
                    try:
                        loops = json.load(open(R.heartbeat_path())).get("loops") or {}
                    except (OSError, ValueError):
                        loops = {}
                    if "presence" in loops and "poll http://s" in loops:
                        break
                    time.sleep(0.1)
            finally:
                stop.set()
                for t in threads:   # every loop ended before tearDown removes the folder they write into
                    t.join(5)
        self.assertFalse([t for t in threads if t.is_alive()])
        self.assertIn("poll http://s", loops)
        self.assertTrue(R.relay_healthy()[0])

    def test_each_organization_is_pulled_in_its_own_long_poll(self):
        """Event store step 1: each organization of a server has its own coordination loop and long-poll, so a slow or
        quiet one holds no other one up, and each loop stamps its own tick (of_review on organic-framework PR 142: a
        loop must be seen moving while a pull is bounded but slow)."""
        seen, waits = {}, []

        class Client:
            def put_presence(self, body):
                return {}

            def poll_messages(self, wait):
                time.sleep(0.05)
                return {"messages": []}

            def coord(self, org, after, wait=0):
                waits.append(wait)
                if org not in seen:
                    seen[org] = time.time()
                    if org == "a":
                        time.sleep(0.6)                                      # a slow pull of the first organization
                else:
                    time.sleep(0.05)
                return {"last": 0, "events": []}
        with mock.patch.object(R, "org_drains", lambda default: {}), \
                mock.patch.object(R, "record_queues", lambda *a, **k: None), \
                mock.patch.object(R, "PRESENCE_CHECK", 0.1):
            stop, threads = R.run(lambda: {}, lambda u: Client(), {"a": "http://s", "b": "http://s"}, "http://s",
                                  log=lambda m: None)
            try:
                deadline, loops = time.time() + 5, {}
                while time.time() < deadline:
                    try:
                        loops = json.load(open(R.heartbeat_path())).get("loops") or {}
                    except (OSError, ValueError):
                        loops = {}
                    if {"coord http://s|a", "coord http://s|b"} <= set(loops) and "b" in seen:
                        break
                    time.sleep(0.1)
            finally:
                stop.set()
                for t in threads:   # every loop ended before tearDown removes the folder they write into
                    t.join(5)
        self.assertFalse([t for t in threads if t.is_alive()])
        self.assertLess(seen["b"] - seen["a"], 0.5)                          # b did not wait for a's slow pull
        self.assertIn("coord http://s|a", loops)
        self.assertIn("coord http://s|b", loops)
        self.assertIn(R.COORD_WAIT, waits)                                   # each read is a long-poll


class WatchdogTest(StateDir):
    def test_a_relay_whose_loops_stopped_is_dumped_then_restarted(self):
        restarts, dumped, said = [], [], []
        t0 = 1_800_000_000.0
        R.write_heartbeat({"presence": t0, "poll http://s": t0}, now=t0)        # then it stops moving

        def check(t):
            with mock.patch.object(R.of_events, "emit", lambda *a, **k: None):
                return R.watchdog(now=t, restart=lambda: restarts.append(t), dump=dumped.append, log=said.append,
                                  installed=lambda: True, pid_of=lambda: os.getpid())
        got = [check(t0 + R.STALE_S + 60 * i) for i in range(1, 6)]
        self.assertEqual(got, ["failing"] * 3 + ["restarted", "waiting"])
        self.assertEqual(dumped, [os.getpid()])
        self.assertIn("has not moved its loops", said[-1])
        t1 = t0 + R.STALE_S + 340
        R.write_heartbeat({"presence": t1, "poll http://s": t1}, now=t1)
        self.assertEqual(check(t1 + 10), "ok")

    def test_a_stale_heartbeat_never_signals_another_process(self):
        # of_review on organic-framework PR 142: after `of relay off` the heartbeat's pid can be any process (a sleep
        # was signalled); the pid is launchd's, and a relay launchd does not run is stopped
        R.write_heartbeat({"presence": 1.0}, now=1.0)                    # names this test's own, live pid
        dumped = []
        with mock.patch.object(R.of_events, "emit", lambda *a, **k: None):
            got = {R.watchdog(now=t, restart=lambda: self.fail("restarted"), dump=dumped.append, log=lambda m: None,
                              installed=lambda: True, pid_of=lambda: None) for t in range(2000, 2600, 60)}
        self.assertEqual((got, dumped), ({"stopped"}, []))
        R.clear_heartbeat()
        self.assertFalse(os.path.exists(R.heartbeat_path()))

    def test_launchd_pid_reads_launchctl(self):
        out = "gui/501/dev.organic-framework.relay = {\n\tstate = running\n\tpid = 4242\n}"
        self.assertEqual(R.launchd_pid("x", run=lambda *a, **k: mock.Mock(returncode=0, stdout=out)), 4242)
        self.assertIsNone(R.launchd_pid("x", run=lambda *a, **k: mock.Mock(returncode=113, stdout="")))

    def test_a_stopped_relay_is_left_alone(self):
        R.write_heartbeat({"presence": 1.0}, now=1.0)
        with open(R.heartbeat_path()) as fh:
            hb = json.load(fh)
        hb["pid"] = 999999
        with open(R.heartbeat_path(), "w") as fh:
            json.dump(hb, fh)
        self.assertEqual(R.watchdog(now=1000.0, restart=lambda: self.fail("restarted"), installed=lambda: True,
                                    pid_of=lambda: None), "stopped")


class UnitTest(unittest.TestCase):
    def test_systemd_relay_unit_and_install(self):
        name = "of-relay.service"
        calls = []

        def run(cmd, **kw):
            self.assertEqual(cmd[:2], ["systemctl", "--user"])
            calls.append(cmd[2:])
            return mock.Mock(returncode=0)

        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"HOME": home}), \
                mock.patch.object(of_events, "STATE_DIR", os.path.join(home, "state")), \
                mock.patch.object(of_events, "LOG_DIR", os.path.join(home, "logs")), \
                mock.patch.object(of_init, "service_path", return_value="/herdr tools:/usr/bin"):
            log = os.path.join(home, "logs", "relay.log")   # never in the state folder a sandbox writes
            units = R.systemd_units(python="/py", of_bin="/of")
            self.assertEqual(list(units), [name])
            for directive in ("ExecStart=/py /of relay run", "Restart=always", "RestartSec=10", "WantedBy=default.target",
                              "Environment=OF_AUTOMATION=1", "Environment=SSH_AUTH_SOCK=",
                              'Environment="PATH=/herdr tools:/usr/bin"',
                              f"StandardOutput=append:{log}", f"StandardError=append:{log}"):
                self.assertIn(directive, units[name].splitlines())
            self.assertEqual(R.install_systemd(run=run, units=units), [f"{name}: loaded"])
            path = Path(home, ".config", "systemd", "user", name)
            self.assertEqual(path.read_text(), units[name])
            self.assertTrue(Path(log).parent.is_dir())
            self.assertEqual(R.install_systemd(run=run, units=units), [f"{name}: unchanged"])
            self.assertEqual(R.install_systemd(uninstall=True, run=run), [f"removed {name}"])
            self.assertFalse(path.exists())
        self.assertEqual(calls, [["daemon-reload"], ["enable", "--now", name], ["is-active", name],
                                 ["disable", "--now", name], ["daemon-reload"]])

    def test_the_relay_and_the_listener_run_at_standard_priority(self):
        relay = next(u for u in of_init.UNITS if u["name"] == "relay")
        listen = next(u for u in of_init.UNITS if u["name"] == "listen")
        unit = plistlib.loads(of_init.unit_text(relay, "Darwin", py="/py", of="/of", log="/l", path="/p").encode())
        self.assertEqual(unit["ProcessType"], "Standard")
        self.assertFalse({"Nice", "LowPriorityIO"} & set(unit))
        self.assertNotIn("Nice=", of_init.unit_text(relay, "Linux", py="/py", of="/of", path="/p"))
        listened = plistlib.loads(of_init.unit_text(listen, "Darwin", log="/l").encode())
        self.assertEqual(listened["ProcessType"], "Standard")  # hand-over 6d9a8305 (of_platform, c16ec92)
        self.assertFalse({"Nice", "LowPriorityIO"} & set(listened))

    def test_install_writes_the_relay_and_its_watchdog(self):
        if sys.platform != "darwin":
            self.skipTest("launchd")
        calls = []

        def run(cmd, **kw):
            calls.append(cmd[:2])
            return mock.Mock(returncode=1 if cmd[1] == "print" else 0)
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"HOME": home}):
            msg = R.install_launchd(run=run, of_bin="/of")
            agents = os.path.join(home, "Library", "LaunchAgents")
            watch = plistlib.load(open(os.path.join(agents, f"{R.RELAY_WATCHDOG_LABEL}.plist"), "rb"))
            relay = plistlib.load(open(os.path.join(agents, f"{R.RELAY_LABEL}.plist"), "rb"))
        self.assertEqual(msg.count("loaded"), 2)
        self.assertEqual((watch["ProgramArguments"][-2:], watch["StartInterval"]), (["relay", "watchdog"], 60))
        self.assertEqual(relay["ProcessType"], "Standard")
        self.assertEqual(relay["EnvironmentVariables"]["OF_AUTOMATION"], "1")

    def test_of_init_installs_the_relay_watchdog_with_the_relay(self):
        relay = next(u for u in of_init.UNITS if u["name"] == "relay")
        self.assertEqual(relay.get("watchdog"), "relay")
        if sys.platform != "darwin":
            self.skipTest("launchd")
        calls = []

        def run(cmd, **kw):
            calls.append(cmd[:2])
            return mock.Mock(returncode=1 if cmd[1] == "print" else 0)
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"HOME": home}):
            msg = R.install_launchd(run=run, of_bin="/of", watchdog_only=True)
            names = os.listdir(os.path.join(home, "Library", "LaunchAgents"))
        self.assertEqual(names, [f"{R.RELAY_WATCHDOG_LABEL}.plist"])
        self.assertIn(R.RELAY_WATCHDOG_LABEL, msg)


if __name__ == "__main__":
    unittest.main()
