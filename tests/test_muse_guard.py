"""Muse's two guards (of_server, message 01a20e82-8a40): the proxy that is its one way out (bin/of_egress.py) and its
keychain under a password of OF's (bin/of_keychain.py), as bin/of_subagent.py uses them. No real keychain is touched:
the Security framework's calls are replaced (2026-11-24: a keychain test on the laptop raised a password dialog)."""
import os
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin"))
import of_egress  # noqa: E402
import of_keychain  # noqa: E402
import of_subagent  # noqa: E402


def ask(port, head):
    c = socket.create_connection(("127.0.0.1", port), timeout=5)
    c.sendall(head)
    got = c.recv(1024)
    return c, got


class Upstream:
    """A loopback echo server standing in for the endpoint."""

    def __enter__(self):
        self.s = socket.socket()
        self.s.bind(("127.0.0.1", 0))
        self.s.listen(4)
        threading.Thread(target=self._serve, daemon=True).start()
        return self

    def _serve(self):
        try:
            c, _ = self.s.accept()
            c.sendall(c.recv(1024).upper())
            c.close()
        except OSError:
            pass

    def __exit__(self, *e):
        self.s.close()


class ProxyTest(unittest.TestCase):
    def test_only_connect_to_a_listed_host_on_443_goes_through(self):
        with Upstream() as up:
            port = up.s.getsockname()[1]
            dialed = []

            def connect(addr, timeout=None):
                dialed.append(addr)
                return socket.create_connection(("127.0.0.1", port), timeout=timeout)
            with of_egress.Proxy(("api.meta.ai",), connect=connect) as p:
                c, got = ask(p.port, b"CONNECT api.meta.ai:443 HTTP/1.1\r\nHost: api.meta.ai:443\r\n\r\n")
                self.assertTrue(got.startswith(b"HTTP/1.1 200"))
                c.sendall(b"hello")
                self.assertEqual(c.recv(1024), b"HELLO")
                c.close()
                for head, code, entry in (
                        (b"CONNECT example.com:443 HTTP/1.1\r\n\r\n", b"403", ("example.com", "443", "refused")),
                        (b"CONNECT api.meta.ai:80 HTTP/1.1\r\n\r\n", b"403", ("api.meta.ai", "80", "refused")),
                        (b"GET http://api.meta.ai/ HTTP/1.1\r\n\r\n", b"405", (None, None, "not CONNECT"))):
                    c, got = ask(p.port, head)
                    self.assertIn(code, got.split(b"\r\n")[0])
                    c.close()
                    self.assertIn(entry, p.log)
        self.assertEqual(dialed, [("api.meta.ai", 443)])                       # nothing else was dialed
        self.assertIn(("api.meta.ai", "443", "allowed"), p.log)

    def test_the_sandbox_lets_the_run_reach_the_proxys_port_alone(self):
        prof = of_subagent.sandbox_profile(tempfile.gettempdir(), net_port=4242)
        self.assertIn("(deny network-outbound)", prof)
        self.assertIn('(allow network-outbound (remote ip "localhost:4242"))', prof)
        self.assertNotIn("network-outbound", of_subagent.sandbox_profile(tempfile.gettempdir()))


class KeychainTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)

    def test_a_password_file_is_new_and_the_users_alone(self):
        pw = os.path.join(self.t.name, "kc.pw")
        got = of_keychain.new_password(pw)
        self.assertEqual(os.stat(pw).st_mode & 0o777, 0o600)
        self.assertEqual(of_keychain.read_password(pw), got)
        with self.assertRaises(FileExistsError):
            of_keychain.new_password(pw)
        os.chmod(pw, 0o644)
        with self.assertRaises(of_keychain.KeychainError):
            of_keychain.read_password(pw)
        link = os.path.join(self.t.name, "link.pw")
        os.symlink(pw, link)
        with self.assertRaises(of_keychain.KeychainError):
            of_keychain.read_password(link)

    def test_the_last_run_out_locks_it_and_none_before(self):
        state = {"unlocked": False, "locks": 0}

        def unlock(path, pw_file):
            state["unlocked"] = True

        def lock(path):
            state["unlocked"], state["locks"] = False, state["locks"] + 1
        hold = os.path.join(self.t.name, "hold")
        with mock.patch.object(of_keychain, "unlock", unlock), mock.patch.object(of_keychain, "lock", lock), \
                mock.patch.object(of_keychain, "unlocked", lambda p: state["unlocked"]):
            a = of_keychain.Held("kc", "pw", hold).__enter__()
            b = of_keychain.Held("kc", "pw", hold).__enter__()
            a.__exit__(None, None, None)
            self.assertTrue(state["unlocked"])                         # b still runs
            self.assertEqual(state["locks"], 0)
            b.__exit__(None, None, None)
            self.assertFalse(state["unlocked"])
            self.assertEqual(state["locks"], 1)
            with self.assertRaises(RuntimeError):                      # a run that fails still locks it
                with of_keychain.Held("kc", "pw", hold):
                    raise RuntimeError("the run failed")
            self.assertEqual(state["locks"], 2)

    def test_an_unlock_that_does_not_hold_stops_the_run(self):
        with mock.patch.object(of_keychain, "unlock"), mock.patch.object(of_keychain, "lock") as lock, \
                mock.patch.object(of_keychain, "unlocked", lambda p: False):
            with self.assertRaises(of_keychain.KeychainError):
                of_keychain.Held("kc", "pw", os.path.join(self.t.name, "hold")).__enter__()
        lock.assert_called_once()


class GateTest(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        own = os.path.join(self.t.name, "muse-login")
        os.makedirs(os.path.join(own, "xdg-config", "muse"))
        self.auth = os.path.join(own, "xdg-config", "muse", "auth.json")
        p = mock.patch.object(of_subagent, "muse_login_dir", lambda: own)
        p.start()
        self.addCleanup(p.stop)

    def write_auth(self, doc):
        import json
        with open(self.auth, "w") as fh:
            json.dump(doc, fh)

    def test_protected_only_with_ofs_password_and_a_secretless_auth(self):
        self.write_auth({"schema_version": 1, "providers": {"meta": {"mechanism": "oauth", "storage": "keychain",
                                                                     "user_email": "a@b"}}})
        self.assertFalse(of_subagent.muse_protected()[0])                    # no password file yet
        of_keychain.new_password(of_subagent.muse_pw_file())
        self.assertEqual(of_subagent.muse_protected(), (True, "-"))
        self.write_auth({"providers": {"meta": {"access_token": "x" * 40}}})
        self.assertFalse(of_subagent.muse_protected()[0])                    # a token in the file: no
        self.write_auth({"providers": {"meta": {"storage": "keychain"}}})
        os.chmod(of_subagent.muse_pw_file(), 0o640)
        self.assertFalse(of_subagent.muse_protected()[0])

    def test_an_outside_tool_is_a_limit_only_with_the_guard_checks_there_and_held(self):
        base = [(c, True, "-") for c in of_subagent.SANDBOX_CHECKS]
        tools = ("no tool outside the kind's is called", False, "asset_generation")
        guard = [(c, True, "-") for c in of_subagent.MUSE_GUARD_CHECKS]
        req = of_subagent.MUSE_GUARD_CHECKS
        got = dict((c, ok) for c, ok, _ in of_subagent.outside_tools_limit(base + guard + [tools], True, req))
        self.assertEqual(got["no tool outside the kind's is called"], "limit")
        got = dict((c, ok) for c, ok, _ in of_subagent.outside_tools_limit(base + [tools], True, req))
        self.assertIs(got["no tool outside the kind's is called"], False)    # the guard checks did not run
        broken = base + [(req[0], False, "x")] + guard[1:] + [tools]
        got = dict((c, ok) for c, ok, _ in of_subagent.outside_tools_limit(broken, True, req))
        self.assertIs(got["no tool outside the kind's is called"], False)


if __name__ == "__main__":
    unittest.main()
