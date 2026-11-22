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
        for name, unit in units.items():   # every service logs outside the state folder a sandbox writes (Gaps entry)
            for line in unit.splitlines():
                if line.startswith(("StandardOutput=append:", "StandardError=append:")):
                    self.assertNotIn("/.local/state/of/", line, name)
                    self.assertIn("/.local/log/of/", line, name)
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

    def test_the_backup_records_counts_and_the_check_restores_into_a_scratch_database(self):
        """agent-host_keeper, 2026-11-22: no restore had been exercised on the host."""
        with tempfile.TemporaryDirectory() as d:
            state = os.path.join(d, "state")
            of_server.Store(state)
            rows = {"of_acme": {"events": 5}, "of_unassigned": {"events": 0}, "of_vault": {"raw": 2}}

            def post(q):
                if q.startswith("SELECT name, total_rows"):
                    db = q.split("database = '")[1].split("'")[0]
                    return "".join(f"{t}\t{n}\n" for t, n in rows.get(db, {}).items()).encode()
                return b""
            cfg = {"state_dir": state, "orgs": {"acme": "of_acme"}, "plugins": {"storage": "clickhouse"},
                   "backup": {"clickhouse_disk": "backups"}}
            with mock.patch("of_ship.config", return_value={"url": "http://192.0.2.1:8123"}):
                of_server.backup(cfg, ch_post=post, today="2026-11-20")
            counts = json.load(open(os.path.join(state, "backups", "2026-11-20", "clickhouse-counts.json")))
            self.assertEqual(counts["of_acme"], {"events": [5, 5]})

            class Admin:
                def __init__(self, restored):
                    self.restored, self.seen = restored, []

                def post(self, q, **kw):
                    self.seen.append(q)
                    if q.startswith("SELECT name, total_rows"):
                        db = q.split("database = '")[1].split("'")[0].replace("of_restore_check_", "")
                        return "".join(f"{t}\t{n}\n" for t, n in self.restored.get(db, {}).items()).encode()
                    return b""
            good = Admin(rows)
            out = of_server.backup_check(cfg, admin=good)
            self.assertTrue(any("of_acme restored, 1 tables within" in x for x in out))
            self.assertIn("RESTORE DATABASE of_acme AS of_restore_check_of_acme FROM Disk('backups', '2026-11-20/of_acme.zip')",
                          good.seen)
            self.assertEqual(good.seen[-1], "DROP DATABASE IF EXISTS of_restore_check_of_vault SYNC")   # dropped after
            with self.assertRaisesRegex(SystemExit, "of_acme"):                                          # a short restore
                of_server.backup_check(cfg, admin=Admin(dict(rows, of_acme={"events": 3})))
            # of_review on PR 559: a database never restored is a failure, merges may shrink a count, and the error
            # keeps ClickHouse's name
            cpath = os.path.join(state, "backups", "2026-11-20", "clickhouse-counts.json")
            saved = open(cpath).read()
            json.dump(dict(json.loads(saved), of_vault={}), open(cpath, "w"))         # its count was not read
            with self.assertRaisesRegex(SystemExit, "of_vault"):
                of_server.backup_check(cfg, admin=good)
            c = json.loads(saved)
            del c["of_unassigned"]                                                     # its backup failed that night
            json.dump(c, open(cpath, "w"))
            with self.assertRaisesRegex(SystemExit, "of_unassigned"):
                of_server.backup_check(cfg, admin=good)
            c = json.loads(saved)
            c["of_acme"] = {"events": [7, 5]}                                          # merged between the counts
            json.dump(c, open(cpath, "w"))
            of_server.backup_check(cfg, admin=Admin(dict(rows, of_acme={"events": 6})))

            class Full(Admin):
                def post(self, q, **kw):
                    if q.startswith("RESTORE"):
                        raise RuntimeError("ClickHouse 507: Code: 243. Cannot reserve space. (NOT_ENOUGH_SPACE)")
                    return super().post(q, **kw)
            json.dump(json.loads(saved), open(cpath, "w"))
            with self.assertRaisesRegex(SystemExit, "NOT_ENOUGH_SPACE"):
                of_server.backup_check(cfg, admin=Full(rows))

    def test_a_database_backed_up_today_is_left_and_one_failure_stops_no_other(self):
        """of_agent-server, 2026-11-22: BACKUP refuses a name that exists, and the name is the UTC day, so a run by
        hand before the timer's failed the timer's, and a run that failed half-way could not be retried that day."""
        with tempfile.TemporaryDirectory() as d:
            state = os.path.join(d, "state")
            of_server.Store(state)
            chdir = os.path.join(d, "ch")
            os.makedirs(os.path.join(chdir, "2026-11-20"))
            with open(os.path.join(chdir, "2026-11-20", "of_acme.zip"), "w") as fh:       # made by an earlier run
                fh.write("zip")
            with open(os.path.join(chdir, "2026-11-20", "of_unassigned.zip"), "w") as fh:  # finished: no lock beside it
                fh.write("zip")
            os.makedirs(os.path.join(chdir, "2026-11-01"))                              # good old days, past the cutoff
            os.makedirs(os.path.join(state, "backups", "2026-11-01"))
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
                with self.assertRaisesRegex(RuntimeError, r"failed for of_vault \(RuntimeError\)"):
                    of_server.backup(cfg, ch_post=post, today="2026-11-20")
            self.assertEqual([q.split()[2] for q in sent if q.startswith("BACKUP")], ["of_vault", "of_zeta"])  # acme and unassigned finished,
            # of_review on PR 554: a failed night removes no old day, or each one would leave fewer good backups
            self.assertTrue(os.path.isdir(os.path.join(chdir, "2026-11-01")))
            self.assertTrue(os.path.isdir(os.path.join(state, "backups", "2026-11-01")))
            # a zip with its lock is unfinished, never done; and 'already exists' with no finished zip is a failure
            open(os.path.join(chdir, "2026-11-20", "of_zeta.zip"), "w").close()
            open(os.path.join(chdir, "2026-11-20", "of_zeta.zip.lock"), "w").close()
            os.unlink(os.path.join(chdir, "2026-11-20", "of_unassigned.zip"))
            with mock.patch("of_ship.config", return_value={"url": "http://192.0.2.1:8123"}):
                with self.assertRaises(RuntimeError) as cm:
                    of_server.backup(cfg, ch_post=post, today="2026-11-20")
            self.assertIn("of_zeta (an unfinished backup of the day is there)", str(cm.exception))
            self.assertIn("of_unassigned (BACKUP_ALREADY_EXISTS)", str(cm.exception))   # ClickHouse's name kept
            self.assertTrue(any("server.db" in x for x in cm.exception.lines))          # and what succeeded
            open(os.path.join(chdir, "2026-11-20", "of_acme.zip"), "w").close()         # empty: never done
            self.assertEqual(of_server._day_zip(cfg["backup"], "2026-11-20", "of_acme"), "unfinished")
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
            self.assertEqual([q for q in sent if q.startswith("BACKUP")],
                             [f"BACKUP DATABASE {db} TO Disk('backups', '2026-11-20/{db}.zip')"
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
