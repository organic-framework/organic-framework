"""`of server clickhouse-init`: the agent host's ClickHouse provisioned from no user at all (2026-11-20, of_server,
with of_agent-server). No ClickHouse runs here: a fake client takes the statements, and nothing secret may show."""

import hashlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_server  # noqa: E402


class Client:
    seen = []

    def __init__(self, cfg):
        self.cfg = cfg

    def post(self, query, **kw):
        Client.seen.append(query)
        return b"1\n"


class ClickHouseInitTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ofci-")
        os.makedirs(os.path.join(self.tmp, "ch", "users.d"))
        self.pw = os.path.join(self.tmp, "passwords", "of_server.password")
        self.cfg = {"state_dir": os.path.join(self.tmp, "state"), "orgs": {"acme": "of_acme"},
                    "clickhouse_config_dir": os.path.join(self.tmp, "ch"),
                    "clickhouse": {"url": "https://127.0.0.1:18443", "ca": os.path.join(self.tmp, "ca.crt"),
                                   "user": "of_server", "password_file": self.pw}}
        self.runs = []
        Client.seen = []

    def init(self):
        return of_server.clickhouse_init(self.cfg, run=lambda argv, **kw: self.runs.append(argv), client=Client,
                                         sleep=lambda s: None)

    def test_from_no_user_to_the_schema_and_the_servers_user_with_nothing_secret_shown(self):
        out = self.init()
        pwdir = os.path.dirname(self.pw)
        admin = open(os.path.join(pwdir, "admin.password"), "rb").read().strip()
        xml = open(os.path.join(self.tmp, "ch", "users.d", "admin.xml")).read()
        self.assertIn(hashlib.sha256(admin).hexdigest(), xml)
        self.assertNotIn(admin.decode(), xml)
        for name in os.listdir(pwdir):
            self.assertEqual(os.stat(os.path.join(pwdir, name)).st_mode & 0o777, 0o600, name)
        self.assertEqual(os.stat(os.path.join(self.tmp, "ch", "users.d", "admin.xml")).st_mode & 0o777, 0o600)
        self.assertIn(["systemctl", "--user", "enable", "--now", "of-clickhouse.service"], self.runs)
        sql = "\n".join(Client.seen)
        for want in ("CREATE DATABASE IF NOT EXISTS of_acme", "CREATE USER IF NOT EXISTS of_acme_ingest",
                     "CREATE USER IF NOT EXISTS of_server", "GRANT ALL ON of_vault.* TO of_server"):
            self.assertIn(want, sql)
        secrets_here = [open(os.path.join(pwdir, n), "rb").read().strip().decode() for n in os.listdir(pwdir)]
        for value in secrets_here:
            self.assertNotIn(value, sql)                         # only salted sha256 hashes go to ClickHouse
            self.assertNotIn(value, "\n".join(out))              # and nothing secret is printed

        before = {n: open(os.path.join(pwdir, n), "rb").read() for n in os.listdir(pwdir)}
        self.init()                                                # a second run keeps every password
        self.assertEqual(before, {n: open(os.path.join(pwdir, n), "rb").read() for n in os.listdir(pwdir)})

    def test_it_refuses_plain_http_and_a_missing_clickhouse_config(self):
        self.cfg["clickhouse"]["url"] = "http://127.0.0.1:8123"
        with self.assertRaises(SystemExit):
            self.init()
        self.cfg["clickhouse"]["url"] = "https://127.0.0.1:18443"
        self.cfg["clickhouse_config_dir"] = os.path.join(self.tmp, "none")
        with self.assertRaises(SystemExit):
            self.init()
        self.assertEqual(self.runs, [])                           # nothing started


if __name__ == "__main__":
    unittest.main()
