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
    users = set()          # the users this fake ClickHouse already has
    passwords = {}         # their passwords, as ClickHouse knows them
    default_answers = False
    count_answer = None    # what the users count answers instead, when set
    logs_in_as = {}        # a user whose login lands on another user

    def __init__(self, cfg):
        self.cfg = cfg

    def post(self, query, **kw):
        if kw.get("auth"):  # the default user's probe
            if Client.default_answers:
                return b"1\n"
            raise RuntimeError("401")
        u = self.cfg.get("user")
        if u in Client.users and u != "admin":  # a login as an existing user, with its file
            if open(self.cfg["password_file"], "rb").read().strip() != Client.passwords.get(u):
                raise RuntimeError("401")
            return Client.logs_in_as.get(u, u).encode() + b"\n"
        if "system.users" in query:
            if Client.count_answer is not None:
                if isinstance(Client.count_answer, Exception):
                    raise Client.count_answer
                return Client.count_answer
            return b"1\n" if kw["params"]["param_u"] in Client.users else b"0\n"
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
        Client.seen, Client.users, Client.default_answers = [], set(), False
        Client.count_answer, Client.logs_in_as = None, {}

    def init(self):
        return of_server.clickhouse_init(self.cfg, run=lambda argv, **kw: self.runs.append(argv), client=Client,
                                         sleep=lambda s: None)

    def test_the_disk_source_is_granted_for_backups_where_clickhouse_knows_it(self):
        """The agent host, 2026-11-22: BACKUP ... TO Disk(...) failed with ACCESS_DENIED for want of WRITE ON DISK
        (ClickHouse PR 117406, in 26.8 and 26.9). A server that does not know the DISK source is no failure."""
        out = self.init()
        self.assertIn("GRANT WRITE ON DISK TO of_server", Client.seen)   # WRITE alone: READ is a restore's
        self.assertIn("WRITE ON DISK granted", out[-1])
        real_post = Client.post

        def failing(msg):
            def post(self, query, **kw):
                if "ON DISK" in query:
                    raise RuntimeError(msg)
                return real_post(self, query, **kw)
            return post
        try:
            Client.seen, Client.users = [], set()
            Client.post = failing("ClickHouse 400: Code: 62. DB::Exception: Syntax error (SYNTAX_ERROR)")
            out = self.init()
            self.assertIn("knows no DISK source grant", out[-1])            # an older server: not needed
            Client.seen, Client.users = [], set()
            Client.post = failing("ClickHouse 403: Code: 497. DB::Exception: Not enough privileges. (ACCESS_DENIED)")
            with self.assertRaisesRegex(SystemExit, "ACCESS_DENIED"):       # anything else fails, by its name
                self.init()
        finally:
            Client.post = real_post

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

    def test_it_refuses_what_would_promote_or_outlive_a_user(self):
        """of_review on organic-framework PR 435."""
        Client.default_answers = True
        with self.assertRaisesRegex(SystemExit, "default user answers"):
            self.init()
        Client.default_answers = False
        self.cfg["clickhouse"]["user"] = "of_acme_ingest"          # a schema role named as the server's user
        with self.assertRaisesRegex(SystemExit, "role of its own"):
            self.init()
        self.cfg["clickhouse"]["user"] = "of_server"
        Client.users, Client.passwords = {"of_server"}, {"of_server": b"its own"}   # ClickHouse has it already
        os.unlink(self.pw) if os.path.exists(self.pw) else None
        for _ in range(2):                                          # and a rerun meets the same refusal
            with self.assertRaisesRegex(SystemExit, "of_server"):
                self.init()
            self.assertFalse(os.path.exists(self.pw))              # the refused run's file is gone
        with open(self.pw, "w") as fh:                              # a file that does not log it in
            fh.write("another\n")
        os.chmod(self.pw, 0o600)
        with self.assertRaisesRegex(SystemExit, "does not log in"):
            self.init()
        Client.users, Client.passwords = set(), {}
        os.unlink(self.pw)
        self.init()                                                 # a clean run, then
        os.chmod(self.pw, 0o644)                                   # an existing file anyone can read
        with self.assertRaisesRegex(SystemExit, "not this user's own 0600 file"):
            self.init()

    def test_an_unreadable_count_or_a_login_as_another_user_refuses_and_leaves_no_file_made(self):
        """of_review's notes on organic-framework PR 435 at its merge."""
        pwdir = os.path.dirname(self.pw)
        xml = os.path.join(self.tmp, "ch", "users.d", "admin.xml")
        for answer in (b"", b"\n", RuntimeError("timed out"), b"Code: 497"):
            Client.count_answer = answer
            with self.subTest(answer=answer), self.assertRaisesRegex(SystemExit, "could not tell"):
                self.init()
            self.assertEqual(os.listdir(pwdir), [])               # the admin's file too
            self.assertFalse(os.path.lexists(xml))
        Client.count_answer = None
        self.init()                                                # a first run, then the server's user
        Client.users = {"of_server"}                              # exists, and its file logs in, but as another
        Client.passwords = {"of_server": open(self.pw, "rb").read().strip()}
        Client.logs_in_as = {"of_server": "admin"}
        with self.assertRaisesRegex(SystemExit, "does not log in"):
            self.init()
        self.assertTrue(os.path.exists(self.pw))                  # a file this run did not make stays
        Client.logs_in_as = {}
        self.init()                                                # as itself: provisioned

    def test_two_settings_of_the_moving_flag_at_once_keep_one_token(self):
        """The user organization's sec_keeper on a5108c09: the second of two concurrent settings is no error."""
        of_server.set_moving(self.cfg, "acme", True)
        flag = os.path.join(of_server.moving_dir(self.cfg), "acme")
        token = open(flag).read()
        self.assertEqual(of_server.set_moving(self.cfg, "acme", True), ["acme"])   # O_EXCL alone decides: the
        self.assertEqual(open(flag).read(), token)                                 # second keeps the first's

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
