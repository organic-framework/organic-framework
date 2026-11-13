"""The relay watched like of-server (docs/health.md §5; of_architect's hand-over 154a0cbf): a heartbeat naming every
loop, a watchdog that dumps and restarts a relay whose loops stopped, and its launchd agent at Standard priority."""

import json
import os
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
        self.assertIn("poll http://s", loops)
        self.assertTrue(R.relay_healthy()[0])


class WatchdogTest(StateDir):
    def test_a_relay_whose_loops_stopped_is_dumped_then_restarted(self):
        restarts, dumped, said = [], [], []
        t0 = 1_800_000_000.0
        R.write_heartbeat({"presence": t0, "poll http://s": t0}, now=t0)        # then it stops moving

        def check(t):
            with mock.patch.object(R.of_events, "emit", lambda *a, **k: None):
                return R.watchdog(now=t, restart=lambda: restarts.append(t), dump=dumped.append, log=said.append,
                                  installed=lambda: True)
        got = [check(t0 + R.STALE_S + 60 * i) for i in range(1, 6)]
        self.assertEqual(got, ["failing"] * 3 + ["restarted", "waiting"])
        self.assertEqual(dumped, [os.getpid()])
        self.assertIn("has not moved its loops", said[-1])
        R.write_heartbeat({"presence": t0 + 900, "poll http://s": t0 + 900}, now=t0 + 900)
        self.assertEqual(check(t0 + 910), "ok")

    def test_a_stopped_relay_is_left_alone(self):
        R.write_heartbeat({"presence": 1.0}, now=1.0)
        with open(R.heartbeat_path()) as fh:
            hb = json.load(fh)
        hb["pid"] = 999999
        with open(R.heartbeat_path(), "w") as fh:
            json.dump(hb, fh)
        self.assertEqual(R.watchdog(now=1000.0, restart=lambda: self.fail("restarted"), installed=lambda: True),
                         "stopped")


class UnitTest(unittest.TestCase):
    def test_the_relay_runs_at_standard_priority_and_the_listener_keeps_its_class(self):
        relay = next(u for u in of_init.UNITS if u["name"] == "relay")
        listen = next(u for u in of_init.UNITS if u["name"] == "listen")
        unit = plistlib.loads(of_init.unit_text(relay, "Darwin", py="/py", of="/of", log="/l", path="/p").encode())
        self.assertEqual(unit["ProcessType"], "Standard")
        self.assertFalse({"Nice", "LowPriorityIO"} & set(unit))
        self.assertNotIn("Nice=", of_init.unit_text(relay, "Linux", py="/py", of="/of", path="/p"))
        self.assertEqual(plistlib.loads(of_init.unit_text(listen, "Darwin", log="/l").encode())["ProcessType"],
                         "Background")

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
