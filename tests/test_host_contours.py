"""Linux host contour planning and execution against in-memory account and filesystem state."""

import base64
import io
import os
import stat
import sys
import unittest
from contextlib import redirect_stderr
from functools import partial
from pathlib import Path
from subprocess import CalledProcessError
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "deploy", "host"))
import contours as C


SAMPLE = ("--admin owner --org-user of --org-user acme "
          "--keeper acme-vault --channel acme --no-tailscale-operator").split()
VAULT = "/var/lib/of-keepers/acme-vault"
PROGRAM = "/opt/of-keepers/acme-vault"
CHANNEL = "/srv/of-channel/acme"
PGREP = ["pgrep", "-u", "owner"]
SSHD = ["sshd", "-T", "-C", "user=owner,host=localhost,addr=127.0.0.1"]


def public_key(byte):
    blob = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20" + byte * 32
    return "ssh-ed25519 " + base64.b64encode(blob).decode("ascii")


OWNER = public_key(b"a") + " laptop"
AGENT = public_key(b"b") + " of-agent"
OWNER_ID = "ssh-ed25519 SHA256:TGIk2D1DFZBKmjWssB2+Qdiona/OUK1TSuWAAHMK+n0"
AGENT_ID = "ssh-ed25519 SHA256:tY51GDtdgY+0w0jAi5adr0DjfLY3KB1G5pQLtXMQD38"


class State:
    def __init__(self):
        self.users = {"owner": C.User(1000, "owner", "/home/owner", "/bin/bash")}
        self.groups = {"owner": set(), "wheel": {"owner"}}
        self.paths = {"/home/owner": C.Directory("owner", "owner", 0o700)}
        self.status = {"owner": "P"}
        self.keys = {}
        self.check_key_sources = Mock()
        self.system_max = 999
        self.tailscale = False

    def directory(self, path):
        return self.paths.get(path)

    def password(self, name):
        return self.status[name]

    def authorized_keys(self, name):
        return self.keys.get(name, b"")


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
                      ["--admin-key", OWNER]):
            with self.subTest(flags=flags), self.assertRaises(C.Refusal):
                C.arguments(["plan", *flags])

    def test_wheel_requires_admin_and_rechecks_live_status(self):
        s = configured()
        s.groups["wheel"].add("of")
        with self.assertRaisesRegex(C.Refusal, "requires --admin"):
            C.plan(C.arguments(["apply", "--org-user", "of"]), s)
        a = C.arguments(["apply", "--admin", "owner", "--org-user", "of"])
        for member, status in ((False, "P"), (True, "L"), (True, "NP"), (True, "unexpected")):
            invoke = Mock(side_effect=[CalledProcessError(1, PGREP), Mock(stdout=f"owner {status}")])
            with self.subTest(member=member, status=status), self.assertRaisesRegex(C.Refusal, "Completed: none"):
                C.execute(a, C.plan(a, s), invoke, lambda n: member, guard=Mock(), out=Mock(), get_keys=s.authorized_keys)
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
                    invoke = Mock(side_effect=[CalledProcessError(1, PGREP), Mock(stdout="owner P"),
                                               Mock(stdout=listing), Mock()])
                    if rule.endswith(") ALL") or rule.endswith(": ALL"):
                        C.execute(a, steps, invoke, member=lambda n: n == "owner", guard=Mock(), out=Mock(), get_keys=s.authorized_keys)
                        expected = [PGREP, passwd, sudo, change]
                    else:
                        with self.assertRaisesRegex(C.Refusal, "sudo -l"):
                            C.execute(a, steps, invoke, member=lambda n: n == "owner", guard=Mock(), out=Mock(), get_keys=s.authorized_keys)
                        expected = [PGREP, passwd, sudo]
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
        interpreter = "/links/bin/python3"
        python = os.path.realpath(sys.executable)
        resolved = {C.__file__: "/opt/contours.py",
                    "/lib/python": "/usr/lib/python", "/lib64/python": "/usr/lib64/python"}
        with patch.object(C.sys, "executable", interpreter), patch.object(C.sysconfig, "get_paths", return_value=libraries), \
                patch.object(C.os.path, "realpath", side_effect=resolved.__getitem__), \
                patch.object(C, "trust_problems") as check:
            self.assertIs(C.trusted_chain(), check.return_value)
            self.assertCountEqual(check.call_args.args[0], [*resolved.values(), python, "/links/bin"])

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

    def test_key_plan_reports_fingerprints_and_uses_real_helper_path(self):
        s = configured()
        s.keys["owner"] = f"{OWNER}\n{AGENT}\nrestrict {OWNER}\n".encode()
        s.groups["wheel"] = {"of"}
        backup = public_key(b"c") + " backup"
        a = C.arguments(["plan", "--admin", "owner", "--admin-key", backup, "--admin-key", OWNER,
                         "--org-user", "of", "--org-key", "of=" + AGENT])
        script = "/opt/reviewed/contours.py"
        python = os.path.realpath(sys.executable)
        with patch.object(C.sys, "executable", "/links/python3"), \
                patch.object(C.os.path, "realpath", side_effect={C.__file__: script, "/links/python3": python}.__getitem__):
            steps = C.plan(a, s)
        self.assertEqual([argv for _, argv in steps if argv], [
            [python, "-I", script, "_keys", "admin", "owner", backup, OWNER],
            ["usermod", "-aG", "wheel", "owner"], ["sudo", "-l", "-U", "owner"],
            ["gpasswd", "-d", "of", "wheel"],
            [python, "-I", script, "_keys", "org", "of", AGENT],
        ])
        invoke, output = Mock(), []
        C.execute(a, steps, invoke=invoke, out=output.append)
        invoke.assert_not_called()
        self.assertIn(f"keep {OWNER_ID} for owner", output)
        self.assertIn(f"remove {AGENT_ID} from owner", output)
        self.assertIn(f"remove {OWNER_ID} from owner", output)
        self.assertIn(f"keep {AGENT_ID} for of", output)
        for line in (OWNER, AGENT, backup):
            self.assertNotIn(line.split()[1], "\n".join(output))

    def test_admin_pins_must_be_plain_owner_key_lines(self):
        for line in (AGENT, "restrict " + OWNER, 'command="a b" ' + OWNER, OWNER + "\n" + OWNER,
                     "-----BEGIN OPENSSH " + "PRIVATE KEY-----"):
            with self.subTest(line=line), self.assertRaisesRegex(C.Refusal, "--admin-key"):
                C.arguments(["plan", "--admin", "owner", "--admin-key", line])

    def test_org_key_requires_explicit_org_user(self):
        with self.assertRaisesRegex(C.Refusal, "--org-user"):
            C.arguments(["plan", "--org-user", "of", "--channel", "acme", "--org-key", "acme=" + AGENT])

    def test_unpinned_keys_refuse_before_wheel_steps(self):
        a = C.arguments(["apply", "--admin", "owner", "--org-user", "of"])
        for members in ({"of"}, {"of", "owner"}):
            s = configured()
            s.groups["wheel"] = members
            steps = C.plan(a, s)
            for line, fingerprint in ((OWNER, OWNER_ID), (AGENT, AGENT_ID)):
                s.keys["owner"] = line.encode()
                with self.subTest(members=members, line=line), self.assertRaises(C.Refusal) as refusal:
                    C.plan(a, s)
                self.assertIn(fingerprint, str(refusal.exception))
                self.assertIn("--admin-key", str(refusal.exception))
                self.assertNotIn(line.split()[1], str(refusal.exception))
                invoke = Mock(side_effect=CalledProcessError(1, PGREP))
                with self.assertRaisesRegex(C.Refusal, "--admin-key"):
                    C.execute(a, steps, invoke=invoke, get_keys=s.authorized_keys, out=Mock())
                invoke.assert_called_once_with(PGREP, capture_output=True, text=True)

    def test_sshd_key_sources_expand_tokens_and_fall_back_to_defaults(self):
        home = Path("/home/owner")
        config = ("authorizedkeysfile %h/.ssh/authorized_keys /etc/%u %U/%%u/%%%u\n"
                  "authorizedkeyscommand /usr/bin/lookup %u\n")
        invoke = Mock(return_value=Mock(stdout=config))
        with patch.object(C.os, "geteuid", return_value=0):
            self.assertEqual(C.authorized_key_sources("owner", str(home), 1000, invoke),
                             ([home / ".ssh/authorized_keys", Path("/etc/owner"), home / "1000/%u/%owner"],
                              "/usr/bin/lookup %u"))
        invoke.assert_called_once_with(SSHD, capture_output=True, text=True)
        # Without root (plan) the defaults stand in; as root sshd -T must answer.
        invoke = Mock()
        with patch.object(C.os, "geteuid", return_value=1000):
            self.assertEqual(C.authorized_key_sources("owner", str(home), 1000, invoke),
                             ([home / ".ssh/authorized_keys", home / ".ssh/authorized_keys2"], "none"))
        invoke.assert_not_called()
        for failure in (FileNotFoundError(), CalledProcessError(1, SSHD)):
            invoke = Mock(side_effect=failure)
            with self.subTest(failure=failure), patch.object(C.os, "geteuid", return_value=0):
                with self.assertRaisesRegex(C.Refusal, "did not answer"):
                    C.authorized_key_sources("owner", str(home), 1000, invoke)

    def test_a_trusted_user_ca_refuses(self):
        invoke = Mock(return_value=Mock(stdout="authorizedkeysfile .ssh/authorized_keys\ntrustedusercakeys /etc/ssh/user_ca.pub\n"))
        with patch.object(C.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(C.Refusal, "user_ca.pub"):
                C.authorized_key_sources("owner", "/home/alex", 1000, invoke)
        invoke = Mock(return_value=Mock(stdout="authorizedkeysfile .ssh/authorized_keys\ntrustedusercakeys none\n"))
        with patch.object(C.os, "geteuid", return_value=0):
            self.assertEqual(C.authorized_key_sources("owner", "/home/alex", 1000, invoke)[1], "none")

    def test_alternate_key_sources_refuse_plan_live_wheel_and_admin_helper(self):
        s = configured()
        s.groups["wheel"].remove("owner")
        wheel = C.arguments(["apply", "--admin", "owner"])
        steps = C.plan(wheel, s)
        s.groups["wheel"].add("owner")
        a = C.arguments(["apply", "--admin", "owner", "--admin-key", OWNER])
        user = Mock(pw_uid=1000, pw_dir="/home/alex")
        files = "authorizedkeysfile .ssh/authorized_keys .ssh/authorized_keys2\n"
        for command, error in (("none", r"remove \S+/\.ssh/authorized_keys2 for owner"),
                               ("/usr/bin/lookup %u", "AuthorizedKeysCommand")):
            config = Mock(stdout=files + "authorizedkeyscommand " + command)
            invoke = Mock(return_value=config)
            s.check_key_sources = partial(C.check_key_sources, invoke=invoke)
            with self.subTest(command=command), patch.object(C.os, "geteuid", return_value=0), \
                    patch.object(C.pwd, "getpwnam", return_value=user), patch.object(Path, "lstat"), \
                    patch.object(C.os, "setgroups", side_effect=AssertionError) as drop:
                with self.assertRaisesRegex(C.Refusal, error):
                    C.plan(a, s)
                with self.assertRaisesRegex(C.Refusal, error):
                    C.import_keys("admin", "owner", [OWNER], invoke)
                drop.assert_not_called()
                invoke = Mock(side_effect=[CalledProcessError(1, PGREP), config])
                with self.assertRaisesRegex(C.Refusal, error):
                    C.execute(wheel, steps, invoke, out=Mock())
                self.assertEqual([c.args[0] for c in invoke.call_args_list], [PGREP, SSHD])

    def test_wheel_steps_exclude_only_the_scripts_own_process_chain(self):
        a = C.arguments(["apply", "--admin", "owner", "--org-user", "of"])
        parents = {"/proc/30/status": 20, "/proc/20/status": 10, "/proc/10/status": 0}

        def proc_status(path, mode):
            return io.BytesIO(f"Name:\tsh\nPPid:\t{parents[path]}\n".encode())

        for already_member in (False, True):
            s = configured()
            s.groups["wheel"] = {"owner", "of"} if already_member else set()
            steps = C.plan(a, s)
            commands = [argv for _, argv in steps if argv]
            for foreign in (False, True):
                pids = "10\n20\n" + ("11\n" if foreign else "")
                invoke = Mock(side_effect=[Mock(stdout=pids), Mock(stdout="owner P"),
                                           Mock(stdout="(ALL) ALL"), Mock()])
                with self.subTest(commands=commands, foreign=foreign), patch.object(C.os, "getpid", return_value=30), \
                        patch("builtins.open", side_effect=proc_status):
                    if foreign:
                        with self.assertRaisesRegex(C.Refusal, "active PIDs .*11"):
                            C.execute(a, steps, invoke, member=Mock(return_value=True), out=Mock(), get_keys=s.authorized_keys)
                        invoke.assert_called_once_with(PGREP, capture_output=True, text=True)
                    else:
                        C.execute(a, steps, invoke, member=Mock(return_value=True), out=Mock(), get_keys=s.authorized_keys)
                        self.assertEqual(invoke.call_args.args[0], commands[-1])

    def test_key_added_after_admin_joins_wheel_refuses_the_next_wheel_step(self):
        s = configured()
        s.groups["wheel"] = {"of"}
        a = C.arguments(["apply", "--admin", "owner", "--org-user", "of"])
        steps = C.plan(a, s)
        add = ["usermod", "-aG", "wheel", "owner"]

        def run_step(argv, **kw):
            if argv == PGREP:
                raise CalledProcessError(1, argv)
            self.assertEqual(argv, add)
            s.keys["owner"] = AGENT.encode()

        invoke = Mock(side_effect=run_step)
        with self.assertRaises(C.Refusal) as refused:
            C.execute(a, steps, invoke, out=Mock(), get_keys=s.authorized_keys)
        self.assertIn(AGENT_ID, str(refused.exception))
        self.assertEqual([c.args[0] for c in invoke.call_args_list], [PGREP, add, PGREP])

    def test_admin_replaces_in_pin_order_and_org_appends_missing_lines_once(self):
        first, second, agent = OWNER.encode(), public_key(b"c").encode(), AGENT.encode()
        existing = b"\n".join((first, agent, b"# old entry", first))
        self.assertEqual(C.key_contents("admin", existing, [second, first]), second + b"\n" + first + b"\n")
        org = C.key_contents("org", existing, [agent, second, second])
        self.assertEqual(org, existing + b"\n" + second + b"\n")
        self.assertEqual(C.key_contents("org", org, [agent, second]), org)

    def test_main_refuses_indirect_path_for_apply_and_key_helper(self):
        for argv in (["apply", *SAMPLE], ["_keys", "admin", "owner", OWNER]):
            with self.subTest(argv=argv), patch.object(C.os.path, "abspath", return_value="/link/contours.py"), \
                    patch.object(C.os.path, "realpath", return_value="/opt/contours.py"), \
                    patch.object(C, "trusted_chain", return_value=[]), patch.object(C.sys, "flags", isolated=1), \
                    patch.object(C.os, "geteuid", return_value=0), patch.object(C.os, "open") as open_dir, \
                    patch.object(C, "import_keys") as helper, redirect_stderr(io.StringIO()) as error:
                self.assertEqual(C.main(argv), 1)
                self.assertIn("run the script by its real path", error.getvalue())
                open_dir.assert_not_called()
                helper.assert_not_called()


if __name__ == "__main__":
    unittest.main()
