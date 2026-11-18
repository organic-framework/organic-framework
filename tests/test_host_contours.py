"""Linux host contour planning and execution against in-memory account and filesystem state."""

import base64
import io
import os
import stat
import sys
import unittest
from contextlib import redirect_stderr
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "deploy", "host"))
import contours as C


SAMPLE = ("--admin owner --org-user of --org-user acme "
          "--keeper acme-vault --channel acme --no-tailscale-operator").split()
VAULT = "/var/lib/of-keepers/acme-vault"
PROGRAM = "/opt/of-keepers/acme-vault"
CHANNEL = "/srv/of-channel/acme"


class State:
    def __init__(self):
        self.users = {"owner": C.User(1000, "owner", "/home/owner", "/bin/bash")}
        self.groups = {"owner": set(), "wheel": {"owner"}}
        self.paths = {"/home/owner": C.Directory("owner", "owner", 0o700)}
        self.status = {"owner": "P"}
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
    s.groups["ch-of-acme"] = {"of", "acme"}
    for uid, n in enumerate(("of", "acme"), 1001):
        home = "/home/" + n
        s.users[n] = C.User(uid, n, home, "/bin/bash")
        s.groups[n] = set()
        s.paths[home] = C.Directory(n, n, 0o700)
    s.users["acme-vault"] = C.User(400, "acme-vault", VAULT, C.NOLOGIN)
    s.groups["acme-vault"] = set()
    s.status["acme-vault"] = "L"
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
            ["useradd", "-m", "-U", "-d", "/home/of", "of"],
            install("/home/of", "0700", "of", "of"),
            ["useradd", "-m", "-U", "-d", "/home/acme", "acme"],
            install("/home/acme", "0700", "acme", "acme"),
            install("/var/lib/of-keepers", "0755"), install("/opt/of-keepers", "0755"),
            ["useradd", "-r", "-M", "-s", C.NOLOGIN, "-U", "-d", VAULT, "acme-vault"],
            install(VAULT, "0700", "acme-vault", "acme-vault"), install(PROGRAM, "0755"),
            install("/srv/of-channel", "0755"), ["groupadd", "ch-of-acme"],
            ["gpasswd", "-M", "of,acme", "ch-of-acme"], install(CHANNEL, "0755"),
            install(CHANNEL + "/to-of", "2750", "acme", "ch-of-acme"),
            install(CHANNEL + "/to-acme", "2750", "of", "ch-of-acme"),
        ])
        self.assertEqual(steps, C.plan(a, s))
        read_only = Mock()
        C.execute(a, steps, invoke=read_only, out=Mock())
        read_only.assert_not_called()

    def test_configured_second_apply(self):
        a = C.arguments(["apply", *SAMPLE, "--org-user", "acme", "--channel", "acme"])
        invoke, output = Mock(), []
        C.execute(a, C.plan(a, configured()), invoke=invoke, out=output.append)
        invoke.assert_not_called()
        self.assertTrue(output and all("already" in line for line in output))

    def test_argument_refusals(self):
        for flags in (["--admin", ""], ["--org-user", "acme\n"], ["--keeper", "../vault"],
                      ["--channel=-group"],
                      ["--admin", "of", "--org-user", "of"], ["--keeper", "root"],
                      ["--admin-key-from", "key.pub"]):
            with self.subTest(flags=flags), self.assertRaises(C.Refusal):
                C.arguments(["plan", *flags])

    def test_wheel_requires_admin_and_rechecks_live_status(self):
        s = configured()
        s.groups["wheel"].add("of")
        with self.assertRaisesRegex(C.Refusal, "requires --admin"):
            C.plan(C.arguments(["apply", "--org-user", "of"]), s)
        a = C.arguments(["apply", "--admin", "owner", "--org-user", "of"])
        for member, status in ((False, "P"), (True, "L"), (True, "NP"), (True, "unexpected")):
            invoke = Mock(return_value=Mock(stdout=f"owner {status}"))
            with self.subTest(member=member, status=status), self.assertRaisesRegex(C.Refusal, "Completed: none"):
                C.execute(a, C.plan(a, s), invoke, lambda n: member, guard=Mock(), out=Mock())
            self.assertFalse(any(call.args[0][0] == "gpasswd" for call in invoke.call_args_list))

    def test_sudo_rule_required_before_each_wheel_change(self):
        a = C.arguments(["apply", "--admin", "owner", "--org-user", "of"])
        sudo = ["sudo", "-l", "-U", "owner"]
        passwd = ["passwd", "-S", "owner"]
        for primary in (False, True):
            s = configured()
            if primary:
                s.users["of"] = s.users["of"]._replace(group="wheel")
                change = ["usermod", "-g", "of", "of"]
            else:
                s.groups["wheel"].add("of")
                change = ["gpasswd", "-d", "of", "wheel"]
            steps = C.plan(a, s)
            self.assertEqual([argv for _, argv in steps if argv], [sudo, change])
            for rule in ("(root) /bin/true", "(ALL) NOPASSWD: /usr/bin/systemctl restart sshd", "(ALL : ALL) /bin/sh, /usr/bin/id", "(ALL : ALL) ALL", "(ALL) NOPASSWD: ALL"):
                with self.subTest(primary=primary, rule=rule):
                    listing = f"User owner may run the following commands on host:\n    {rule}\n"
                    invoke = Mock(side_effect=[Mock(stdout="owner P"), Mock(stdout=listing), Mock()])
                    if rule.endswith(") ALL") or rule.endswith(": ALL"):
                        C.execute(a, steps, invoke, member=lambda n: n == "owner", guard=Mock(), out=Mock())
                        expected = [passwd, sudo, change]
                    else:
                        with self.assertRaisesRegex(C.Refusal, "sudo -l"):
                            C.execute(a, steps, invoke, member=lambda n: n == "owner", guard=Mock(), out=Mock())
                        expected = [passwd, sudo]
                    self.assertEqual([call.args[0] for call in invoke.call_args_list], expected)

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
        with patch.object(C.os, "geteuid", return_value=1000), patch.object(C, "trusted_chain", return_value=[]), \
                patch.object(C, "Host") as host:
            error = io.StringIO()
            with redirect_stderr(error):
                self.assertEqual(C.main(["apply", *SAMPLE]), 1)
            self.assertIn("requires root", error.getvalue())
            host.assert_not_called()

    def test_trust_problems_check_every_component(self):
        script = "/opt/checkout/contours.py"
        directory = stat.S_IFDIR | 0o755
        regular = stat.S_IFREG | 0o644
        chain = {path: Mock(st_uid=0, st_mode=directory) for path in ("/", "/opt", "/opt/checkout")}
        chain[script] = Mock(st_uid=0, st_mode=regular)
        inspect = Mock(side_effect=chain.__getitem__)
        self.assertEqual(C.trust_problems([script], lstat=inspect), [])
        self.assertCountEqual([call.args[0] for call in inspect.call_args_list], chain)
        for path, uid, mode in ((script, 1000, regular), ("/opt/checkout", 0, directory | 0o020),
                                ("/opt", 0, stat.S_IFDIR | 0o1777)):
            with self.subTest(path=path):
                unsafe = {**chain, path: Mock(st_uid=uid, st_mode=mode)}
                problems = C.trust_problems([script], lstat=unsafe.__getitem__)
                self.assertEqual(len(problems), 1)
                self.assertIn(path, problems[0])

    def test_trusted_chain_uses_resolved_script_interpreter_and_libraries(self):
        libraries = {"stdlib": "/lib/python", "platstdlib": "/lib64/python"}
        resolved = {C.__file__: "/opt/contours.py", sys.executable: "/bin/python",
                    "/lib/python": "/usr/lib/python", "/lib64/python": "/usr/lib64/python"}
        with patch.object(C.sysconfig, "get_paths", return_value=libraries), \
                patch.object(C.os.path, "realpath", side_effect=resolved.__getitem__), \
                patch.object(C, "trust_problems") as check:
            self.assertIs(C.trusted_chain(), check.return_value)
            self.assertCountEqual(check.call_args.args[0], resolved.values())

    def test_apply_requires_isolation_and_trusted_paths_before_host_inspection(self):
        for isolated, problems in ((0, []), (1, ["unsafe checkout"]), (0, ["unsafe checkout", "unsafe stdlib"])):
            with self.subTest(isolated=isolated, problems=problems), patch.object(C.sys, "flags", isolated=isolated), \
                    patch.object(C, "trusted_chain", return_value=problems.copy()), \
                    patch.object(C.os, "geteuid", return_value=0), patch.object(C.os, "open") as open_dir:
                error = io.StringIO()
                with redirect_stderr(error):
                    self.assertEqual(C.main(["apply", *SAMPLE]), 1)
                open_dir.assert_not_called()
                for problem in problems + ([] if isolated else ["python3 -I"]):
                    self.assertIn(problem, error.getvalue())

    def test_plan_warns_and_continues(self):
        with patch.object(C.sys, "flags", isolated=0), patch.object(C, "trusted_chain", return_value=["unsafe checkout"]), \
                patch.object(C.os, "open"), patch.object(C.os, "close"), \
                patch.object(C, "Host", return_value=State()), patch.object(C, "execute") as execute:
            error = io.StringIO()
            with redirect_stderr(error):
                self.assertEqual(C.main(["plan", *SAMPLE]), 0)
            for diagnostic in ("warning", "python3 -I", "unsafe checkout"):
                self.assertIn(diagnostic, error.getvalue())
            execute.assert_called_once()

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
        for bad in (b"-----BEGIN OPENSSH " + b"PRIVATE KEY-----", owner + b" AGENT@host", other + b" other"):
            with self.assertRaises(C.Refusal):
                C.selected_lines([bad], selected)
        for unsafe in (b'command="run this" ', b"cert-authority ", b'environment="X=Y" '):
            with self.assertRaises(C.Refusal):
                C.selected_lines([owner], unsafe + owner)


if __name__ == "__main__":
    unittest.main()
