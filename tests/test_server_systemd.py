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
        self.assertEqual(sorted(units), ["of-server-watchdog.service", "of-server-watchdog.timer", "of-server.service"])
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
            self.assertEqual(sorted(os.listdir(os.path.join(d, "units"))),
                             ["of-server-watchdog.service", "of-server-watchdog.timer", "of-server.service"])
            self.assertIn(["enable", "--now", "of-server.service", "of-server-watchdog.timer"], calls)
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
