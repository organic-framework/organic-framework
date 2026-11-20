"""`of server clickhouse-setup`: the host's ClickHouse config and unit from a command (of_review on organic-framework PR
435: they were hand-written on the host). No ClickHouse runs here; the files are checked as written."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_server  # noqa: E402


class ClickHouseSetupTest(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="ofcs-")
        self.runs = []

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def setup_(self):
        return of_server.clickhouse_setup(self.home, run=lambda argv, **kw: self.runs.append(argv))

    def test_https_only_no_user_and_the_unit_left_disabled(self):
        tls = os.path.join(self.home, ".config", "of", "clickhouse", "tls")
        os.makedirs(tls, mode=0o700)
        for name in ("server.crt", "server.key"):                # the host's own pair, made there
            with open(os.path.join(tls, name), "w") as fh:
                fh.write("test material\n")
        out = self.setup_()
        base = os.path.join(self.home, ".config", "of", "clickhouse")
        https = open(os.path.join(base, "config.d", "https-only.xml")).read()
        for want in ('<http_port remove="1"/>', '<tcp_port remove="1"/>', "<https_port>18443</https_port>",
                     f"<certificateFile>{tls}/server.crt</certificateFile>", "<listen_host>127.0.0.1</listen_host>"):
            self.assertIn(want, https)
        self.assertIn("<users/>", open(os.path.join(base, "users.xml")).read())   # no user, no default
        config = open(os.path.join(base, "config.xml")).read()
        self.assertIn(f"{self.home}/.local/share/of/clickhouse/", config)
        self.assertNotIn("{HOME}", config + https)
        unit = os.path.join(self.home, ".config", "systemd", "user", "of-clickhouse.service")
        self.assertIn("ExecStart=%h/.local/bin/clickhouse server", open(unit).read())
        for path in (os.path.join(base, "config.xml"), unit):
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.join(base, "users.d")).st_mode & 0o777, 0o700)
        self.assertEqual(self.runs, [["systemctl", "--user", "daemon-reload"]])   # never enabled here
        self.assertTrue(any("clickhouse ca" in line and line.endswith("127.0.0.1:18443") for line in out))
        self.assertTrue(all(line.endswith("unchanged") for line in self.setup_()[:5]))   # a second run: as it is

    def test_another_files_content_refuses_with_nothing_written(self):
        base = os.path.join(self.home, ".config", "of", "clickhouse")
        os.makedirs(os.path.join(base, "tls"), mode=0o700)
        for name in ("server.crt", "server.key"):
            open(os.path.join(base, "tls", name), "w").close()
        with open(os.path.join(base, "users.xml"), "w") as fh:
            fh.write("<clickhouse><users><default/></users></clickhouse>\n")   # a default user someone left
        with self.assertRaisesRegex(SystemExit, "other content"):
            self.setup_()
        self.assertFalse(os.path.exists(os.path.join(base, "config.xml")))
        self.assertEqual(self.runs, [])

    @unittest.skipUnless(shutil.which("openssl"), "openssl makes the certificate")
    def test_a_certificate_is_made_when_the_host_has_none(self):
        out = self.setup_()
        https = open(os.path.join(self.home, ".config", "of", "clickhouse", "config.d", "https-only.xml")).read()
        self.assertIn("server-cert.pem</certificateFile>", https)
        self.assertTrue(any("server-cert.pem" in line for line in out))


if __name__ == "__main__":
    unittest.main()
