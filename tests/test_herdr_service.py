"""`of herdr service`: this user's herdr server as a systemd user unit on Linux (of_architect's hand-over 763bfda4).
Pure logic: systemctl, loginctl and herdr are fakes, /proc is a folder."""

import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_herdr_service as hs  # noqa: E402

SOCK = "/home/alex/.config/herdr/herdr.sock"


class Host:
    """systemctl, loginctl and `herdr status server`, as one user's machine answers them."""

    def __init__(self, running=False, starts=True, linger="yes"):
        self.running, self.starts, self.linger, self.calls = running, starts, linger, []

    def __call__(self, cmd, capture_output=True, text=True, check=False, timeout=None):
        self.calls.append(cmd)
        out = ""
        if cmd[:2] == ["systemctl", "--user"]:
            if cmd[2] == "start" and self.starts:
                self.running = True
            if cmd[2] == "is-active":
                out = "active\n" if self.running else "inactive\n"
            if cmd[2] == "is-enabled":
                out = "enabled\n"
        elif cmd[0] == "loginctl":
            out = self.linger + "\n"
        elif cmd[1:] == ["status", "server"]:
            out = ("status: running\n" if self.running else "status: not running\n") + f"socket: {SOCK}\n"
        return subprocess.CompletedProcess(cmd, 0, out, "")

    def ctl(self):
        return [c[2] for c in self.calls if c[:2] == ["systemctl", "--user"]]


def fake_proc(root, cgroup, sock=SOCK, pid=4321, inode=777):
    """A /proc with one process holding the listening socket `sock`, in `cgroup`."""
    os.makedirs(os.path.join(root, "net"))
    with open(os.path.join(root, "net", "unix"), "w") as fh:
        fh.write("Num       RefCount Protocol Flags    Type St Inode Path\n")
        fh.write(f"0000000000000000: 00000002 00000000 00010000 0001 01 {inode} {sock}\n")
    os.makedirs(os.path.join(root, str(pid), "fd"))
    os.symlink(f"socket:[{inode}]", os.path.join(root, str(pid), "fd", "3"))
    with open(os.path.join(root, str(pid), "cgroup"), "w") as fh:
        fh.write(f"0::{cgroup}\n")
    return pid


UNDER = "/user.slice/user-1001.slice/user@1001.service/app.slice/of-herdr.service"
OUTSIDE = "/user.slice/user-1001.slice/session-161.scope"


class HerdrServiceTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.units = os.path.join(self.tmp.name, "units")
        self.proc = os.path.join(self.tmp.name, "proc")
        for patch in (mock.patch.object(sys, "platform", "linux"),
                      mock.patch.object(hs.of_events, "STATE_DIR", os.path.join(self.tmp.name, "state"))):
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def install(self, host):
        return hs.install(run=host, units_dir=self.units, herdr="/usr/bin/herdr", proc=self.proc, wait_s=2,
                          sleep=lambda s: None)

    def test_the_unit_runs_herdr_server_and_its_panes_keep_of_automation(self):
        unit = hs.unit_text("/usr/bin/herdr", path="/home/alex/.local/bin:/usr/bin",
                            log="/home/alex/.local/state/of/herdr-server.log")
        self.assertIn("ExecStart=/usr/bin/herdr server\n", unit)  # nothing removed: the panes inherit OF_AUTOMATION
        self.assertNotIn("-u OF_AUTOMATION", unit)
        self.assertIn("ExecStop=/usr/bin/herdr server stop\n", unit)
        self.assertIn("Restart=on-failure\n", unit)
        self.assertIn("RestartPreventExitStatus=1\n", unit)  # another server holds the socket
        self.assertIn("Environment=OF_AUTOMATION=1\n", unit)
        self.assertIn("Environment=SSH_AUTH_SOCK=\n", unit)
        self.assertIn("Environment=PATH=/home/alex/.local/bin:/usr/bin\n", unit)
        self.assertIn("WorkingDirectory=%h\n", unit)
        self.assertTrue(unit.endswith("[Install]\nWantedBy=default.target\n"))

    def test_the_path_does_not_depend_on_the_callers_path(self):
        """A setup script writes this text before the install runs; both must write the same file."""
        texts = set()
        for caller in ("/home/alex/.local/share/mise/shims:/usr/bin:/home/alex/.local/bin", "/usr/bin:/bin"):
            with mock.patch.dict(os.environ, {"PATH": caller, "HOME": "/home/alex"}):
                texts.add(hs.unit_text("/usr/bin/herdr", log="/home/alex/.local/state/of/herdr-server.log"))
        self.assertEqual(len(texts), 1)
        self.assertIn("Environment=PATH=/usr/bin:/home/alex/.local/bin:/usr/local/bin:/bin\n", texts.pop())
        self.assertEqual(hs.unit_path("/home/alex/.local/bin/herdr", home="/home/alex"),
                         "/home/alex/.local/bin:/usr/local/bin:/usr/bin:/bin")

    def test_no_server_running_writes_enables_and_starts_the_unit(self):
        fake_proc(self.proc, UNDER)
        host = Host(running=False)
        lines, ok = self.install(host)
        self.assertTrue(ok)
        self.assertTrue(os.path.exists(os.path.join(self.units, hs.UNIT)))
        self.assertEqual(host.ctl(), ["daemon-reload", "enable", "start"])
        self.assertIn("herdr's server started under the unit (pid 4321)", lines)
        self.assertIn("linger is on", " ".join(lines))

    def test_a_server_outside_the_unit_is_never_stopped(self):
        fake_proc(self.proc, OUTSIDE)
        host = Host(running=True)
        lines, ok = self.install(host)
        self.assertTrue(ok)
        self.assertEqual(host.ctl(), ["daemon-reload", "enable"])  # no start, no stop: every pane would end
        self.assertNotIn(["/usr/bin/herdr", "server", "stop"], host.calls)
        said = " ".join(lines)
        self.assertIn("(pid 4321) runs outside the unit, in " + OUTSIDE, said)
        self.assertIn("`herdr server stop` and then `systemctl --user start of-herdr.service`", said)

    def test_a_changed_unit_never_restarts_a_server_under_it(self):
        fake_proc(self.proc, UNDER)
        os.makedirs(self.units)
        with open(os.path.join(self.units, hs.UNIT), "w") as fh:
            fh.write("[Service]\nExecStart=/usr/bin/herdr server\n")
        host = Host(running=True)
        lines, ok = self.install(host)
        self.assertTrue(ok)
        self.assertEqual(host.ctl(), ["daemon-reload", "enable"])
        self.assertIn(f"applies at its next restart: `{hs.RESTART}`", " ".join(lines))

    def test_installing_again_changes_nothing(self):
        fake_proc(self.proc, UNDER)
        self.install(Host(running=False))
        host = Host(running=True)
        lines, ok = self.install(host)
        self.assertTrue(ok)
        self.assertEqual(host.ctl(), ["enable"])
        self.assertIn("herdr's server runs under the unit (pid 4321)", lines)

    def test_a_server_that_does_not_start_fails_the_install(self):
        fake_proc(self.proc, UNDER)
        lines, ok = self.install(Host(running=False, starts=False))
        self.assertFalse(ok)
        self.assertIn("did not start", lines[-1])

    def test_another_socket_is_not_the_default_server(self):
        fake_proc(self.proc, OUTSIDE, sock="/home/alex/.cache/lab/herdr/herdr.sock")
        self.assertIsNone(hs.socket_holder(SOCK, self.proc))
        self.assertEqual(hs.socket_holder("/home/alex/.cache/lab/herdr/herdr.sock", self.proc)[0], 4321)

    def test_linger_off_names_the_admins_command(self):
        fake_proc(self.proc, UNDER)
        lines, _ = self.install(Host(running=False, linger="no"))
        self.assertIn("`sudo loginctl enable-linger ", " ".join(lines))

    def test_uninstall_disables_and_removes_but_never_stops(self):
        fake_proc(self.proc, UNDER)
        self.install(Host(running=False))
        host = Host(running=True)
        lines, ok = hs.uninstall(run=host, units_dir=self.units)
        self.assertTrue(ok)
        self.assertFalse(os.path.exists(os.path.join(self.units, hs.UNIT)))
        self.assertEqual(host.ctl(), ["disable", "daemon-reload", "is-active"])
        self.assertIn("keeps running until it stops", " ".join(lines))

    def test_status_is_ok_only_under_the_unit(self):
        fake_proc(self.proc, OUTSIDE)
        self.install(Host(running=True))
        lines, ok = hs.status(run=Host(running=True), units_dir=self.units, herdr="/usr/bin/herdr", proc=self.proc)
        self.assertFalse(ok)
        self.assertIn("outside the unit", lines[-1])

    def test_not_on_linux(self):
        with mock.patch.object(sys, "platform", "darwin"):
            with self.assertRaises(SystemExit):
                hs.install(run=Host(), units_dir=self.units, herdr="/usr/bin/herdr", proc=self.proc)


if __name__ == "__main__":
    unittest.main()
