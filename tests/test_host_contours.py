"""Linux host contour planning and execution against in-memory account and filesystem state."""

import base64
import io
import os
import stat
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "deploy", "host"))
import contours as C


SAMPLE = ("--admin owner --rename agents:of --org-user of --org-user acme "
          "--keeper acme-vault --channel acme").split()
VAULT = "/var/lib/of-keepers/acme-vault"
PROGRAM = "/opt/of-keepers/acme-vault"
CHANNEL = "/srv/of-channel/acme"


class State:
    def __init__(self):
        self.users = {"agents": C.User(1000, "agents", "/home/agents", "/bin/bash")}
        self.groups = {"agents": set(), "wheel": {"agents"}}
        self.paths = {"/home/agents": C.Directory("agents", "agents", 0o755)}
        self.status = {}
        self.system_max = 999
        self.tailscale = False

    def directory(self, path):
        return self.paths.get(path)

    def password(self, name):
        return self.status[name]


def install(path, mode, owner="root", group="root"):
    return ["install", "-d", "-o", owner, "-g", group, "-m", mode, path]


def configured():
    s = State()
    s.users.clear()
    s.paths.clear()
    s.groups = {"wheel": {"owner"}, "ch-of-acme": {"of", "acme"}}
    for uid, n in enumerate(("of", "owner", "acme"), 1000):
        home = "/home/" + n
        s.users[n] = C.User(uid, n, home, "/bin/bash")
        s.groups[n] = set()
        s.paths[home] = C.Directory(n, n, 0o700)
    s.users["acme-vault"] = C.User(400, "acme-vault", VAULT, C.NOLOGIN)
    s.groups["acme-vault"] = set()
    s.status = {"owner": "P", "acme-vault": "L"}
    for path in ("/var/lib/of-keepers", "/opt/of-keepers", PROGRAM, "/srv/of-channel", CHANNEL):
        s.paths[path] = C.Directory("root", "root", 0o755)
    s.paths[VAULT] = C.Directory("acme-vault", "acme-vault", 0o700)
    s.paths[CHANNEL + "/to-of"] = C.Directory("acme", "ch-of-acme", 0o2750)
    s.paths[CHANNEL + "/to-acme"] = C.Directory("of", "ch-of-acme", 0o2750)
    return s


class HostContours(unittest.TestCase):
    def test_sample_command_order_and_arguments(self):
        s = State()
        a = C.arguments(["plan", *SAMPLE])
        steps = C.plan(a, s)
        self.assertEqual([argv for _, argv in steps if argv], [
            ["useradd", "-m", "-U", "-d", "/home/owner", "owner"],
            ["usermod", "-aG", "wheel", "owner"], ["passwd", "owner"],
            ["pgrep", "-u", "agents"], ["usermod", "-l", "of", "agents"],
            ["groupmod", "-n", "of", "agents"], ["usermod", "-d", "/home/of", "-m", "of"],
            install("/home/of", "0700", "of", "of"),
            ["useradd", "-m", "-U", "-d", "/home/acme", "acme"],
            install("/home/acme", "0700", "acme", "acme"),
            ["passwd", "-S", "owner"], ["gpasswd", "-d", "of", "wheel"],
            install("/var/lib/of-keepers", "0755"), install("/opt/of-keepers", "0755"),
            ["useradd", "-r", "-M", "-s", C.NOLOGIN, "-U", "-d", VAULT, "acme-vault"],
            install(VAULT, "0700", "acme-vault", "acme-vault"), install(PROGRAM, "0755"),
            install("/srv/of-channel", "0755"), ["groupadd", "ch-of-acme"],
            ["gpasswd", "-M", "of,acme", "ch-of-acme"], install(CHANNEL, "0755"),
            install(CHANNEL + "/to-of", "2750", "acme", "ch-of-acme"),
            install(CHANNEL + "/to-acme", "2750", "of", "ch-of-acme"),
        ])
        self.assertEqual(steps, C.plan(a, s))
        read_only = Mock(side_effect=subprocess.CalledProcessError(1, ["pgrep"]))
        C.execute(a, steps, invoke=read_only, out=Mock())
        read_only.assert_called_once_with(["pgrep", "-u", "agents"], capture_output=True)

    def test_configured_second_apply(self):
        a = C.arguments(["apply", *SAMPLE, "--org-user", "acme", "--channel", "acme"])
        invoke, output = Mock(), []
        C.execute(a, C.plan(a, configured()), invoke=invoke, out=output.append)
        invoke.assert_not_called()
        self.assertTrue(output and all("already" in line for line in output))

    def test_argument_refusals(self):
        for flags in (["--admin", ""], ["--org-user", "acme\n"], ["--keeper", "../vault"],
                      ["--channel=-group"], ["--rename", "old"], ["--rename", "Bad:of"],
                      ["--rename", "of:new:extra"], ["--rename", "old:" + "x" * 33],
                      ["--admin", "of", "--org-user", "of"], ["--keeper", "root"],
                      ["--admin-key-from", "key.pub"]):
            with self.subTest(flags=flags), self.assertRaises(C.Refusal):
                C.arguments(["plan", *flags])

    def test_wheel_requires_admin_and_rechecks_live_status(self):
        s = State()
        with self.assertRaisesRegex(C.Refusal, "requires --admin"):
            C.plan(C.arguments(["apply", "--org-user", "agents"]), s)
        a = C.arguments(["apply", "--admin", "owner", "--org-user", "agents"])
        for member, status in ((False, "P"), (True, "L"), (True, "NP"), (True, "unexpected")):
            invoke = Mock(return_value=Mock(stdout=f"owner {status}"))
            with self.subTest(member=member, status=status), self.assertRaisesRegex(C.Refusal, "Completed: create"):
                C.execute(a, C.plan(a, s), invoke, lambda n: member, guard=Mock(), out=Mock())
            self.assertFalse(any(call.args[0][0] == "gpasswd" for call in invoke.call_args_list))

    def test_rename_conflicts_and_process_probe(self):
        a = C.arguments(["apply", "--rename", "agents:of"])
        s = State()
        steps = C.plan(a, s)
        for effect in (None, subprocess.CalledProcessError(2, ["pgrep"])):
            invoke = Mock(side_effect=effect)
            with self.assertRaisesRegex(C.Refusal, "no running processes.*Completed: none"):
                C.execute(a, steps, invoke, out=Mock())
            self.assertEqual(invoke.call_count, 1)
        s.users["of"] = s.users["agents"]
        with self.assertRaisesRegex(C.Refusal, "already exists"):
            C.plan(a, s)

    def test_keeper_and_channel_drift(self):
        s = configured()
        s.status["acme-vault"] = "P"
        s.groups["ch-of-acme"].add("outsider")
        s.paths[CHANNEL + "/to-of"] = C.Directory("of", "root", 0o777)
        a = C.arguments(["apply", *SAMPLE])
        self.assertEqual([cmd for _, cmd in C.plan(a, s) if cmd], [
            ["usermod", "-L", "acme-vault"], ["gpasswd", "-M", "of,acme", "ch-of-acme"],
            install(CHANNEL + "/to-of", "2750", "acme", "ch-of-acme"),
        ])

    def test_retry_after_login_rename_and_wheel_gate_on_success(self):
        s = State()
        s.users["of"] = s.users.pop("agents")
        s.paths["/home/agents"] = C.Directory("of", "agents", 0o755)
        s.groups["wheel"] = {"of"}
        a = C.arguments(["apply", *SAMPLE])
        calls = []

        def invoke(argv, **kw):
            calls.append(argv)
            if argv[0] == "pgrep":
                raise subprocess.CalledProcessError(1, argv)
            return Mock(stdout="owner P")

        C.execute(a, C.plan(a, s), invoke, member=lambda n: n == "owner", guard=Mock(), out=Mock())
        self.assertNotIn(["usermod", "-l", "of", "agents"], calls)
        self.assertIn(["groupmod", "-n", "of", "agents"], calls)
        remove = calls.index(["gpasswd", "-d", "of", "wheel"])
        self.assertEqual(calls[remove - 1], ["passwd", "-S", "owner"])

    def test_tailscale_report(self):
        s = configured()
        s.tailscale = True
        a = C.arguments(["apply", "--org-user", "of", "--no-tailscale-operator"])
        for operator in ("of", "owner"):
            invoke = Mock(return_value=Mock(stdout='{"OperatorUser":"' + operator + '","secret":"hidden"}'))
            output = []
            if operator == "of":
                with self.assertRaisesRegex(C.Refusal, "of is the Tailscale operator"):
                    C.execute(a, C.plan(a, s), invoke, out=output.append)
            else:
                C.execute(a, C.plan(a, s), invoke, out=output.append)
            invoke.assert_called_once_with(["tailscale", "debug", "prefs"], capture_output=True, text=True)
            self.assertNotIn("hidden", str(output))

    def test_apply_requires_root_before_host_inspection(self):
        with patch.object(C.os, "geteuid", return_value=1000), patch.object(C, "Host") as host:
            error = io.StringIO()
            with redirect_stderr(error):
                self.assertEqual(C.main(["apply", *SAMPLE]), 1)
            self.assertIn("requires root", error.getvalue())
            host.assert_not_called()

    def test_pathname_commands_refuse_links_and_untrusted_ancestors(self):
        for mode, uid in ((stat.S_IFLNK | 0o777, 0), (stat.S_IFDIR | 0o777, 0),
                          (stat.S_IFDIR | 0o755, 1000)):
            with patch.object(C.os, "lstat", return_value=Mock(st_mode=mode, st_uid=uid)) as inspect:
                with self.assertRaises(C.Refusal):
                    C.check_paths(install("/home/acme", "0700", "acme", "acme"))
                inspect.assert_called_once_with("/home")

    def test_ssh_import_selects_only_pinned_owner_lines_with_original_options(self):
        prefix = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20"

        def key(byte):
            return b"ssh-ed25519 " + base64.b64encode(prefix + byte * 32)

        owner, other = key(b"a"), key(b"b")
        selected = b'from="host1,host2",restrict\t' + owner + b" laptop"
        source = b"\n".join([selected, selected, other + b" of-agent", other + b" " + owner, b"# " + owner])
        self.assertEqual(C.selected_lines([owner + b" device\n"], source), [selected])
        with self.assertRaisesRegex(C.Refusal, "agent key"):
            C.selected_lines([owner], source + b"\n" + owner + b" of-agent")
        for bad in (b"-----BEGIN OPENSSH PRIVATE KEY-----", owner + b" AGENT@host", other + b" other"):
            with self.assertRaises(C.Refusal):
                C.selected_lines([bad], selected)
        for unsafe in (b'command="run this" ', b"cert-authority ", b'environment="X=Y" '):
            with self.assertRaises(C.Refusal):
                C.selected_lines([owner], unsafe + owner)


if __name__ == "__main__":
    unittest.main()
