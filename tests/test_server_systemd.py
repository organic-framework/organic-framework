"""of-server under the user's systemd on Linux (`of server install` there), its watchdog's Linux branch, and the health
probe through the plain listener's unix socket (2026-11-19, of_server: the OF team's server moves to the agent host)."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_server  # noqa: E402


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({"ok": self.path == "/v1/health", "version": "t"}).encode()
        self.send_response(200 if self.path == "/v1/health" else 404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class SystemdTest(unittest.TestCase):

    def test_the_units_run_the_server_and_its_watchdog(self):
        units = of_server.systemd_units(python="/usr/bin/python3", of_bin="/home/alex/of/bin/of")
        self.assertEqual(sorted(units), ["of-server-backup.service", "of-server-backup.timer", "of-server-watchdog.service",
                                         "of-server-watchdog.timer", "of-server.service"])
        self.assertIn("server backup", units["of-server-backup.service"])
        self.assertIn("OnCalendar=*-*-* 03:30:00 UTC", units["of-server-backup.timer"])
        srv = units["of-server.service"]
        self.assertIn("ExecStart=/usr/bin/python3 /home/alex/of/bin/of server run\n", srv)
        self.assertIn("Restart=always", srv)
        self.assertIn("Environment=OF_AUTOMATION=1", srv)
        self.assertIn("Environment=SSH_AUTH_SOCK=", srv)
        self.assertIn("After=network.target", srv)
        self.assertNotIn("After=of-server.service", srv)               # never ordered after itself
        self.assertIn("WantedBy=default.target", srv)
        self.assertIn("server watchdog", units["of-server-watchdog.service"])
        self.assertIn("Type=oneshot", units["of-server-watchdog.service"])
        self.assertIn(f"OnUnitActiveSec={of_server.WATCHDOG['every']}", units["of-server-watchdog.timer"])

    def test_install_enables_the_server_and_the_timer(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv[2:])
            return subprocess.CompletedProcess(argv, 3 if argv[2] == "is-active" else 0, "", "")
        with tempfile.TemporaryDirectory() as d:
            lines = of_server.install_systemd({"state_dir": os.path.join(d, "state")}, run=run,
                                              units_dir=os.path.join(d, "units"))
            self.assertEqual(len(os.listdir(os.path.join(d, "units"))), 5)
            self.assertIn(["enable", "--now", "of-server.service", "of-server-watchdog.timer", "of-server-backup.timer"],
                          calls)
            self.assertIn("of-server.service: loaded", lines)
            self.assertTrue(of_server._systemd_installed(os.path.join(d, "units")))
            self.assertIsNone(of_server._systemd_installed(os.path.join(d, "none")))

    def test_the_watchdog_on_linux_restarts_through_systemd(self):
        seen = {}

        def watch(*a, **kw):
            seen.update(kw)
            return "ok"
        with mock.patch.object(of_server, "watch", watch), mock.patch.object(of_server.sys, "platform", "linux"), \
                mock.patch.object(of_server.subprocess, "run") as run:
            self.assertEqual(of_server.watchdog({"state_dir": "/nonexistent", "host": "127.0.0.1", "port": 1}), "ok")
            self.assertIs(seen["installed"], of_server._systemd_installed)
            seen["restart"]()
            self.assertEqual(run.call_args[0][0], ["systemctl", "--user", "restart", "of-server.service"])

    def test_a_database_backed_up_today_is_left_and_one_failure_stops_no_other(self):
        """of_agent-server, 2026-11-22: BACKUP refuses a name that exists, and the name is the UTC day, so a run by
        hand before the timer's failed the timer's, and a run that failed half-way could not be retried that day."""
        with tempfile.TemporaryDirectory() as d:
            state = os.path.join(d, "state")
            of_server.Store(state)
            chdir = os.path.join(d, "ch")
            os.makedirs(os.path.join(chdir, "2026-11-20"))
            open(os.path.join(chdir, "2026-11-20", "of_acme.zip"), "w").close()       # made by an earlier run
            sent = []

            def post(q):
                sent.append(q)
                if "of_unassigned" in q:
                    raise RuntimeError("ClickHouse 500: Code: 598. Backup already exists. (BACKUP_ALREADY_EXISTS)")
                if "of_vault" in q:
                    raise RuntimeError("ClickHouse 500: disk full")
            cfg = {"state_dir": state, "orgs": {"acme": "of_acme", "zeta": "of_zeta"}, "plugins": {"storage": "clickhouse"},
                   "backup": {"clickhouse_disk": "backups", "clickhouse_path": chdir}}
            with mock.patch("of_ship.config", return_value={"url": "http://192.0.2.1:8123"}):
                with self.assertRaisesRegex(RuntimeError, "failed for of_vault"):
                    of_server.backup(cfg, ch_post=post, today="2026-11-20")
            self.assertEqual([q.split()[2] for q in sent], ["of_unassigned", "of_vault", "of_zeta"])  # acme skipped,
            # zeta still tried after vault failed

    def test_the_backup_copies_the_store_backs_up_each_database_and_keeps_seven_days(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            state = os.path.join(d, "state")
            of_server.Store(state)
            c = sqlite3.connect(os.path.join(state, "server.db"))
            c.execute("INSERT INTO machines (machine_id, pubkey, orgs, raw_orgs) VALUES ('m', 'k', '[]', '[]')")
            c.commit()
            c.close()
            chdir = os.path.join(d, "ch")
            os.makedirs(os.path.join(chdir, "2026-11-01"))
            os.makedirs(os.path.join(state, "backups", "2026-11-02"))
            sent = []
            cfg = {"state_dir": state, "orgs": {"acme": "of_acme"}, "plugins": {"storage": "clickhouse"},
                   "backup": {"clickhouse_disk": "backups", "clickhouse_path": chdir}}   # the connection in ship.yaml
            with mock.patch("of_ship.config", return_value={"url": "http://192.0.2.1:8123"}):
                out = of_server.backup(cfg, ch_post=sent.append, today="2026-11-20")
                self.assertEqual(sorted(os.listdir(os.path.join(state, "backups", "2026-11-20"))), ["server.db"])
                with self.assertRaises(FileNotFoundError):   # no store: no empty one made and backed up
                    of_server.backup(dict(cfg, state_dir=os.path.join(d, "none")), ch_post=sent.append,
                                     today="2026-11-20")
                self.assertFalse(os.path.exists(os.path.join(d, "none", "server.db")))
            copy = os.path.join(state, "backups", "2026-11-20", "server.db")
            self.assertEqual(os.stat(copy).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(os.path.dirname(copy)).st_mode & 0o777, 0o700)
            got = sqlite3.connect(copy).execute("SELECT machine_id FROM machines").fetchall()
            self.assertEqual(got, [("m",)])
            self.assertEqual(sent, [f"BACKUP DATABASE {db} TO Disk('backups', '2026-11-20/{db}.zip')"
                                    for db in ("of_acme", "of_unassigned", "of_vault")])
            self.assertFalse(os.path.exists(os.path.join(chdir, "2026-11-01")))           # past seven days
            self.assertFalse(os.path.exists(os.path.join(state, "backups", "2026-11-02")))
            self.assertTrue(any("removed 2026-11-01" in x for x in out))
            real = sqlite3.connect
            calls = []

            def failing(*a, **k):  # the copy fails half-way: its .partial must not stay behind (round 3)
                calls.append(a)
                if len(calls) == 2:
                    raise sqlite3.OperationalError("disk full")
                return real(*a, **k)
            with mock.patch("of_ship.config", return_value={}), mock.patch.object(sqlite3, "connect", failing), \
                    self.assertRaises(sqlite3.OperationalError):
                of_server.backup(dict(cfg, backup={"dir": os.path.join(d, "b2")}), ch_post=sent.append, today="2026-11-21")
            self.assertEqual(os.listdir(os.path.join(d, "b2", "2026-11-21")), [])
            cfg["backup"]["clickhouse_disk"] = "x'); DROP"
            with mock.patch("of_ship.config", return_value={}), self.assertRaises(ValueError):
                of_server.backup(cfg, ch_post=sent.append, today="2026-11-20")

    def test_health_asks_the_plain_unix_socket(self):
        d = tempfile.mkdtemp(prefix="ofs-", dir="/tmp")
        path = os.path.join(d, "plain.sock")
        srv = of_server.UnixServer(path, Health)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            self.assertEqual(of_server._health({"socket": path, "host": "127.0.0.1", "port": 1}),
                             {"ok": True, "version": "t"})
            self.assertIsNone(of_server._health({"socket": os.path.join(d, "none.sock"), "host": "", "port": 1}))
        finally:
            srv.shutdown()
            srv.server_close()
            os.unlink(path)
            os.rmdir(d)


if __name__ == "__main__":
    unittest.main()
