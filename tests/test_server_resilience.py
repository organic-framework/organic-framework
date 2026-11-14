"""of-server through a sleep and a wake, and through the loss and return of the network (of_conductor, msg
01a1d63f-d434, 2026-11-13: after a day asleep the Mac swapped, of-server answered /v1/health only after 25 s and a person
restarted it). The server runs at standard priority under launchd with a watchdog; a connection idle past
`request_timeout` is closed; a reply to a client that had gone is counted, not traced; the writer waits for a storage it
cannot reach instead of spending a batch's attempts."""

import io
import json
import os
import plistlib
import re
import signal
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
sys.path.insert(0, HERE)
import of_server  # noqa: E402
from test_server import ServerBase, event  # noqa: E402


class SleepWakeTest(unittest.TestCase):
    """A real server process, frozen as a sleep freezes it, while its clients time out and give up."""

    def test_a_frozen_server_answers_at_once_after_the_wake(self):
        with tempfile.TemporaryDirectory() as root:
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
            conf = os.path.join(root, "server.yaml")
            with open(conf, "w") as fh:
                fh.write(f"state_dir: {root}/state\nport: {port}\ntenant_key_file: {root}/tenant.key\n"
                         "plugins: {storage: sqlite}\nrequest_timeout: 2\n")
            log = os.path.join(root, "server.log")
            saved = os.environ.get("OF_STATE")
            os.environ["OF_STATE"] = os.path.join(root, "of")
            url = f"http://127.0.0.1:{port}/v1/health"
            pid = None
            try:
                pid, msg = of_server.start_daemon(config=conf, log_path=log, wait=20)
                self.assertTrue(pid, msg + "\n" + open(log).read())
                deadline = time.time() + 20               # the writer process has reported: the whole server is up
                while not (of_server._health({"host": "127.0.0.1", "port": port}, timeout=2) or {}).get("ok"):
                    self.assertLess(time.time(), deadline, open(log).read())
                    time.sleep(0.2)
                idle = [socket.create_connection(("127.0.0.1", port)) for _ in range(3)]  # clients that never send
                os.killpg(os.getpgid(pid), signal.SIGSTOP)                               # the sleep
                gave_up = []

                def client():
                    try:
                        urllib.request.urlopen(url, timeout=0.5).read()
                    except Exception as e:  # noqa: BLE001
                        gave_up.append(type(e).__name__)
                threads = [threading.Thread(target=client) for _ in range(10)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
                self.assertEqual(len(gave_up), 10)
                time.sleep(1.0)
                os.killpg(os.getpgid(pid), signal.SIGCONT)                               # the wake
                t0 = time.time()
                with urllib.request.urlopen(url, timeout=5) as r:
                    self.assertTrue(json.loads(r.read())["ok"])
                self.assertLess(time.time() - t0, 3)
                for c in idle:                    # closed by the server once idle past request_timeout
                    c.settimeout(6)
                    self.assertEqual(c.recv(10), b"")
                    c.close()
                os.kill(pid, signal.SIGUSR1)      # the watchdog's stack dump before a restart
                time.sleep(1.0)
                text = open(log).read()
                self.assertRegex(text, r"(?m)^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ of-server .* listening on")
                self.assertIn("most recent call first", text)
                self.assertNotIn("Traceback", text)   # the clients that gave up are counted, not traced
            finally:
                if pid:
                    try:
                        os.killpg(os.getpgid(pid), signal.SIGCONT)
                    except OSError:
                        pass
                    of_server.stop_daemon(config=conf, wait=20)
                if saved is None:
                    os.environ.pop("OF_STATE", None)
                else:
                    os.environ["OF_STATE"] = saved


class NetworkLossTest(ServerBase):
    """The storage unreachable (no network, a refused or dropped connection), then back."""

    def test_a_batch_waits_for_the_network_and_drains_when_it_returns(self):
        down = urllib.error.URLError(ConnectionRefusedError(61, "Connection refused"))
        calls = []

        def insert(database, table, rows):
            calls.append(table)
            if self.down:
                raise self.down
            self.ch.inserts.append((database, table, rows))
        self.down = down
        self.app.writer.ch.insert = insert
        self.client.send_batch([event("e1", "ofb")], batch_id="bn")
        for _ in range(self.app.cfg["max_attempts"] + 2):  # more failures than a batch may spend
            self.app.writer.retry_at = 0
            self.assertEqual(self.app.writer.drain(), 0)
        h = of_server.health(self.app.store, self.app.writer)
        self.assertEqual((h["queued"], h["dead"]), (1, 0))
        self.assertIn("storage unreachable", h["writer_error"])
        with self.app.store.conn() as c:
            self.assertEqual(c.execute("SELECT attempts FROM batches WHERE batch_id = 'bn'").fetchone()[0], 0)
        n = len(calls)
        self.assertGreater(self.app.writer.retry_at, time.time())   # it waits, doubling up to a minute
        self.assertEqual(self.app.writer.drain(), 0)
        self.assertEqual(len(calls), n)                              # no attempt while it waits
        for e in (ConnectionResetError(54, "reset"), TimeoutError("timed out")):   # a dropped link, a blackhole
            self.down = e
            self.app.writer.retry_at = 0
            self.assertEqual(self.app.writer.drain(), 0)
        self.down = None                                             # the network is back
        self.app.writer.retry_at = 0
        self.assertEqual(self.app.writer.drain(), 1)
        self.assertEqual((self.app.writer.retry_at, self.app.writer.unreachable), (0.0, 0))
        self.assertEqual(self.client.receipts()["receipts"][0]["state"], "committed")

    def test_a_long_outage_alerts_the_owner_once(self):
        # of_review on organic-framework PR 112, note 5: batches no longer die, so a storage that never returns says so
        self.app.writer.ch.insert = lambda *a: (_ for _ in ()).throw(urllib.error.URLError("no route"))
        self.client.send_batch([event("e1", "ofb")], batch_id="ba")
        said = []
        with mock.patch.object(of_server.of_events, "emit", lambda name, data, **k: said.append((name, data["kind"]))):
            self.app.writer.retry_at = 0
            self.app.writer.drain()                                  # the outage starts: no alert yet
            self.assertEqual(said, [])
            self.app.writer.unreachable_since -= of_server.UNREACHABLE_ALERT_S
            for _ in range(3):
                self.app.writer.retry_at = 0
                self.app.writer.drain()
        self.assertEqual(said, [("server_alert", "storage_unreachable")])

    def test_an_error_answer_still_spends_attempts(self):
        self.ch.fail = True                                          # ClickHouse answered with an error
        old = of_server.alert_dead
        of_server.alert_dead = lambda *a: None
        try:
            self.app.cfg["max_attempts"] = 2
            self.client.send_batch([event("e1", "ofb")], batch_id="be")
            self.app.writer.drain()
            self.app.writer.drain()
        finally:
            of_server.alert_dead = old
        self.assertEqual(of_server.health(self.app.store, self.app.writer)["dead"], 1)

    def test_health_answers_while_the_writer_hangs_on_the_storage(self):
        release = threading.Event()
        self.app.writer.ch.insert = lambda *a: release.wait(30)       # a connection into a blackhole
        self.client.send_batch([event("e1", "ofb")], batch_id="bh")
        t = threading.Thread(target=self.app.writer.drain, daemon=True)
        t.start()
        try:
            t0 = time.time()
            with urllib.request.urlopen(self.url + "/v1/health", timeout=5) as r:
                self.assertEqual(json.loads(r.read())["queued"], 1)
            self.assertLess(time.time() - t0, 2)
        finally:
            release.set()
            t.join(5)


class WatchdogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = dict(of_server.DEFAULTS, state_dir=self.tmp.name)
        with open(os.path.join(self.tmp.name, "server.pid"), "w") as fh:
            fh.write(f"{os.getpid()}\n")
        self.answers, self.dumped, self.restarts, self.said = [], [], [], []

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, now, ok):
        with mock.patch.object(of_server.of_events, "emit", lambda *a, **k: None):
            return of_server.watchdog(self.cfg, now=now, probe=lambda: ok, restart=lambda: self.restarts.append(now),
                                      dump=self.dumped.append, log=self.said.append)

    def test_a_server_silent_for_three_minutes_is_dumped_then_restarted_once_in_fifteen(self):
        self.assertEqual(self.check(0, True), "ok")
        self.assertEqual([self.check(t, False) for t in (60, 120, 180)], ["failing"] * 3)
        self.assertEqual(self.check(240, False), "restarted")        # 180 s since the first failed check
        self.assertEqual((self.dumped, self.restarts), ([os.getpid()], [240]))
        self.assertIn("has not answered /v1/health for 180 s", self.said[-1])
        self.assertEqual({self.check(t, False) for t in range(300, 1140, 60)}, {"waiting"})
        self.assertEqual(self.check(1140, False), "restarted")       # 15 minutes after the last restart
        self.assertEqual(len(self.restarts), 2)
        self.assertEqual(self.check(1200, True), "ok")
        self.assertIn("answers again after 1140 s and a restart", self.said[-1])

    def test_a_short_gap_restarts_nothing(self):
        self.assertEqual([self.check(t, ok) for t, ok in ((0, False), (60, False), (120, True), (180, False))],
                         ["failing", "failing", "ok", "failing"])
        self.assertEqual(self.restarts, [])

    def test_a_stopped_server_is_left_alone(self):
        # of_review on organic-framework PR 112: `of server stop` meant it, and a crash is launchd's KeepAlive's
        os.remove(os.path.join(self.tmp.name, "server.pid"))
        self.assertEqual({self.check(t, False) for t in range(0, 900, 60)}, {"stopped"})
        with open(os.path.join(self.tmp.name, "server.pid"), "w") as fh:
            fh.write("999999\n")                                    # a pid that is not running
        self.assertEqual({self.check(t, False) for t in range(900, 1500, 60)}, {"stopped"})
        self.assertEqual((self.restarts, self.dumped), ([], []))

    def test_a_sleep_between_checks_starts_the_count_again(self):
        # one failed check before the lid closed and one after the wake are not 3 minutes of silence
        self.assertEqual([self.check(t, False) for t in (0, 86400, 86460, 86520)], ["failing"] * 4)
        self.assertEqual(self.check(86580, False), "restarted")      # 180 s of checks after the wake
        self.assertEqual(self.restarts, [86580])

    def test_a_restart_that_hangs_still_waits_the_gap(self):
        import subprocess
        tries = []

        def hang():
            tries.append(1)
            raise subprocess.TimeoutExpired(["launchctl"], 30)
        with mock.patch.object(of_server.of_events, "emit", lambda *a, **k: None):
            got = [of_server.watchdog(self.cfg, now=t, probe=lambda: False, restart=hang, dump=self.dumped.append,
                                      log=self.said.append) for t in range(0, 600, 60)]
        self.assertEqual(got[:4], ["failing"] * 3 + ["restart_failed"])
        self.assertEqual(set(got[4:]), {"waiting"})
        self.assertEqual(len(tries), 1)
        self.assertIn("did not complete (TimeoutExpired", self.said[-1])

    def test_a_restart_launchd_refuses_is_recorded_as_failed(self):
        # of_review on organic-framework PR 142: `kickstart -k` after a bootout exits 113 ("Could not find service")
        with mock.patch.object(of_server.of_events, "emit", lambda *a, **k: None):
            got = [of_server.watchdog(self.cfg, now=t, probe=lambda: False, restart=lambda: mock.Mock(returncode=113),
                                      dump=self.dumped.append, log=self.said.append) for t in range(0, 240, 60)]
        self.assertEqual(got[-1], "restart_failed")
        self.assertIn("launchctl exit 113", self.said[-1])

    def test_without_launchd_it_only_says_so(self):
        with mock.patch.object(of_server, "launchd_job", lambda: None), \
                mock.patch.object(of_server.of_events, "emit", lambda *a, **k: None):
            for t in (0, 60, 120, 180):
                r = of_server.watchdog(self.cfg, now=t, probe=lambda: False, dump=self.dumped.append,
                                       log=self.said.append)
        self.assertEqual((r, self.dumped), ("not-under-launchd", []))


class LaunchdTest(unittest.TestCase):
    def test_the_server_runs_at_standard_priority_with_a_watchdog_every_minute(self):
        cfg = dict(of_server.DEFAULTS, state_dir="/s")
        units = {k: plistlib.loads(v.encode()) for k, v in
                 of_server.launchd_plists(cfg, python="/py", of_bin="/of").items()}
        server, watch = units[of_server.LAUNCHD_LABEL], units[of_server.WATCHDOG_LABEL]
        self.assertEqual(server["ProcessType"], "Standard")
        self.assertFalse({"Nice", "LowPriorityIO"} & set(server))
        self.assertEqual(server["ProgramArguments"], ["/py", "/of", "server", "run"])
        self.assertEqual(server["KeepAlive"], {"SuccessfulExit": False})
        self.assertEqual((watch["ProgramArguments"][-2:], watch["StartInterval"]), (["server", "watchdog"], 60))
        for u in units.values():
            self.assertEqual(u["EnvironmentVariables"]["OF_AUTOMATION"], "1")
            self.assertEqual(u["EnvironmentVariables"]["SSH_AUTH_SOCK"], "")

    def test_install_writes_and_loads_both_and_uninstall_removes_them(self):
        if sys.platform != "darwin":
            self.skipTest("launchd")
        calls = []

        def run(cmd, **kw):
            calls.append(cmd[:2])
            return mock.Mock(returncode=0)
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"HOME": home}):
            cfg = dict(of_server.DEFAULTS, state_dir=os.path.join(home, "state"))
            msg = of_server.install_launchd(cfg, run=run)
            agents = os.path.join(home, "Library", "LaunchAgents")
            self.assertEqual(sorted(os.listdir(agents)), sorted(f"{lb}.plist" for lb in
                                                                (of_server.LAUNCHD_LABEL, of_server.WATCHDOG_LABEL)))
            self.assertEqual(msg.count("loaded"), 2)
            self.assertEqual(calls, [["launchctl", "bootout"], ["launchctl", "bootstrap"]] * 2)
            calls.clear()                                   # again, unchanged and loaded: nothing restarts
            self.assertEqual(of_server.install_launchd(cfg, run=run).count("unchanged"), 2)
            self.assertEqual(calls, [["launchctl", "print"]] * 2)
            of_server.install_launchd(cfg, uninstall=True, run=run)
            self.assertEqual(os.listdir(agents), [])

    def test_of_init_installs_the_same_agents_and_no_low_priority_unit(self):
        # of_review on organic-framework PR 112: of init had written the Background, Nice 10 unit itself
        import of_init
        with mock.patch.object(of_init.platform, "system", lambda: "Darwin"), \
                mock.patch.object(of_server, "install_launchd", lambda *a, **k: "both loaded"), \
                mock.patch.object(of_init, "_started", lambda m: m):
            self.assertEqual(of_init.install_service(), "service installed: both loaded")
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"HOME": home}), \
                mock.patch.object(of_init.platform, "system", lambda: "Linux"), \
                mock.patch.object(of_init.subprocess, "run", lambda *a, **k: mock.Mock(returncode=0)), \
                mock.patch.object(of_init, "_started", lambda m: m):
            of_init.install_service()
            unit = open(os.path.join(home, ".config", "systemd", "user", "of-server.service")).read()
        self.assertNotIn("Nice", unit)
        self.assertNotIn("IOSchedulingClass", unit)


class ClientGoneTest(unittest.TestCase):
    def test_a_reply_to_a_gone_client_is_counted_once_a_minute_and_other_errors_are_traced_with_a_time(self):
        srv = of_server.Server(("127.0.0.1", 0), of_server.make_handler(mock.Mock(cfg={})))
        try:
            err = io.StringIO()
            with redirect_stderr(err), mock.patch.object(of_server.Server, "gone_said", 0.0):
                for e in (BrokenPipeError(32, "Broken pipe"), ConnectionResetError(54, "reset")):
                    try:
                        raise e
                    except OSError:
                        srv.handle_error(None, ("127.0.0.1", 1))
                try:
                    raise ValueError("bad")
                except ValueError:
                    srv.handle_error(None, ("127.0.0.1", 1))
            text = err.getvalue()
            self.assertEqual(len(re.findall("replies to clients that had gone", text)), 1)
            self.assertRegex(text, r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ error in a request from 127\.0\.0\.1")
            self.assertIn("ValueError: bad", text)
            self.assertNotIn("BrokenPipeError", text.split("error in a request")[1])
        finally:
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
