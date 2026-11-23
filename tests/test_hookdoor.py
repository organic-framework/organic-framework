"""The hooks door (bin/of_hookdoor.py): a session asks the listener for its organization's hook decision with data
only, and gets the decision alone (of_server's ruling, message 01a20566-e23c)."""

import os
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))

import of_hookdoor as h  # noqa: E402

CARD = {"question": "Pay the invoice?", "options": ["yes", "no"], "tags": ["money"], "to": "acme_owner",
        "from": "acme_ops", "blocking": False, "due": None, "task": False}


def req(**kw):
    r = {"v": 1, "kind": "hook_before", "org": "acme", "package": "of-asks", "module": "file", "card": dict(CARD)}
    r.update(kw)
    return r



def short_tmp():
    """A temporary folder whose socket paths fit a Unix socket's name (104 bytes on macOS): under /tmp where TMPDIR
    is long, as macOS's per-user one can be (of_server on PR 607)."""
    base = None
    if len(os.path.realpath(tempfile.gettempdir())) > 40 and os.path.isdir("/tmp"):
        base = "/tmp"
    return tempfile.TemporaryDirectory(dir=base)

class ValidateTest(unittest.TestCase):
    def test_data_only_on_the_allowlist(self):
        self.assertEqual(h.validate(req())["card"], CARD)
        bad = [dict(req(), extra=1), req(kind="exec"), req(v=2), req(org="../acme"), req(module="File"),
               req(card=dict(CARD, code="os.execute('x')")), req(card=dict(CARD, tags=["Money!"])),
               req(card=dict(CARD, blocking="yes")), req(card={k: v for k, v in CARD.items() if k != "question"}),
               req(card=dict(CARD, to="../owner")), req(card=dict(CARD, options=["x"] * 21))]
        for r in bad:
            with self.subTest(r=str(r)[:80]):
                with self.assertRaises(h.Refused):
                    h.validate(r)

    def test_the_caller_is_its_nearest_claude_process(self):
        sid = "0123abcd-0000-4000-8000-00000000abcd"
        tree = {10: ["bash", "-c", "of ask"], 9: ["/usr/bin/node", "/opt/claude/bin/claude", "--session-id", sid],
                8: ["/usr/bin/claude", "--resume", "ffffffff-0000-4000-8000-000000000000"]}
        anc = lambda pid: [10, 9, 8]  # noqa: E731
        me = lambda pid: os.getuid()  # noqa: E731
        self.assertEqual(h.caller_session(10, anc, tree.get, me), sid)
        self.assertIsNone(h.caller_session(10, anc, tree.get, lambda pid: os.getuid() + 1))   # another user's
        self.assertIsNone(h.caller_session(10, lambda pid: [10], tree.get, me))           # no Claude above
        self.assertIsNone(h.caller_session(10, anc, {**tree, 9: ["codex", "exec"]}.get, me))   # Codex first
        tree[9] = ["claude", "--session-id", "not-a-uuid"]
        self.assertIsNone(h.caller_session(10, anc, tree.get, me))                         # never a guess further up

    def test_the_organization_comes_from_the_hooks_session_index(self):
        idx = {"sessions": {"S1": ["acme_ops", "acme", None], "S2": "junk"}}
        self.assertEqual(h.org_of_session("S1", idx), "acme")
        self.assertIsNone(h.org_of_session("S2", idx))
        self.assertIsNone(h.org_of_session("S3", idx))

    def test_a_nul_anywhere_is_refused(self):
        with self.assertRaises(h.Refused):
            h.validate(req(card=dict(CARD, question="pay\x00now")))

    def test_only_its_own_organizations_hooks(self):
        ev = lambda p, pk, m, c: {"decision": "stop", "by": "x.lua", "reason": "money", "extra": 1}  # noqa: E731
        self.assertEqual(h.answer(h.validate(req()), "acme", lambda o: "/p", ev),
                         {"decision": "stop", "by": "x.lua", "reason": "money"})
        with self.assertRaisesRegex(h.Refused, "its own organization"):
            h.answer(h.validate(req()), "globex", lambda o: "/p", ev)
        with self.assertRaisesRegex(h.Refused, "no position"):
            h.answer(h.validate(req()), None, lambda o: "/p", ev)


class DeadlineTest(unittest.TestCase):
    def test_the_whole_request_has_one_deadline(self):
        """of_server: a byte now and then never holds the thread past READ_S."""
        import socket
        import time
        a, b = socket.socketpair()
        try:
            def drip():
                for _ in range(6):
                    try:
                        b.sendall(b" ")
                    except OSError:
                        return
                    time.sleep(0.2)
            threading.Thread(target=drip, daemon=True).start()
            t0 = time.monotonic()
            with self.assertRaisesRegex(h.Refused, "did not arrive within"):
                h._recv(a, 1000, 0.5)
            self.assertLess(time.monotonic() - t0, 0.9)
        finally:
            a.close()
            b.close()


class SocketTest(unittest.TestCase):
    def test_one_evaluation_at_a_time_per_organization_none_refused(self):
        """of_server: a queue, not a cap; the client's timeout is the only bound."""
        import time
        with short_tmp() as d:
            path = os.path.join(os.path.realpath(d), "door", "hooks.sock")
            stop, ready, busy, overlap = threading.Event(), threading.Event(), [0], [0]
            lock = threading.Lock()

            def evaluate(project, package, module, card):
                with lock:
                    busy[0] += 1
                    overlap[0] = max(overlap[0], busy[0])
                time.sleep(0.05)
                with lock:
                    busy[0] -= 1
                return {"decision": "pass"}
            t = threading.Thread(target=h.serve, args=(lambda sid: "acme", lambda org: "/org/acme", evaluate),
                                 kwargs={"path": path, "stop": stop, "session_of": lambda pid: "S", "ready": ready},
                                 daemon=True)
            t.start()
            self.assertTrue(ready.wait(5))
            got = []
            ts = [threading.Thread(target=lambda: got.append(h.ask("acme", "of-asks", "file", CARD, path=path)))
                  for _ in range(8)]
            try:
                for x in ts:
                    x.start()
                for x in ts:
                    x.join(20)
            finally:
                stop.set()
                t.join(5)
            self.assertEqual([g["decision"] for g in got], ["pass"] * 8)   # none refused
            self.assertEqual(overlap[0], 1)                                  # one at a time for one organization


    def test_a_request_through_the_socket(self):
        with short_tmp() as d:
            path = os.path.join(os.path.realpath(d), "door", "hooks.sock")
            stop, ready = threading.Event(), threading.Event()
            seen = []

            def evaluate(project, package, module, card):
                seen.append((project, package, module, card["tags"]))
                return {"decision": "stop", "by": "extensions/of-asks/finance.lua: before \"file\"",
                        "reason": "money cards go to finance first"} if "money" in card["tags"] else \
                    {"decision": "pass", "by": None}
            t = threading.Thread(target=h.serve, args=(lambda sid: "acme", lambda org: "/org/acme", evaluate),
                                 kwargs={"path": path, "stop": stop, "session_of": lambda pid: "S", "ready": ready},
                                 daemon=True)
            t.start()
            self.assertTrue(ready.wait(5))
            try:
                self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
                got = h.ask("acme", "of-asks", "file", CARD, path=path)
                self.assertEqual(got["decision"], "stop")
                self.assertEqual(seen[-1], ("/org/acme", "of-asks", "file", ["money"]))   # the folder is the listener's
                self.assertEqual(h.ask("acme", "of-asks", "file", dict(CARD, tags=[]), path=path),
                                 {"decision": "pass"})
                other = h.ask("globex", "of-asks", "file", CARD, path=path)
                self.assertEqual((other["decision"], "own organization" in other["reason"]), ("stop", True))
                bad = h.ask("acme", "of-asks", "file", dict(CARD, run="x"), path=path)
                self.assertEqual(bad["decision"], "stop")
            finally:
                stop.set()
                t.join(5)
            gone = h.ask("acme", "of-asks", "file", CARD, path=os.path.join(d, "none.sock"))
            # a folder that is not this user's own of mode 700: no door
            loose = os.path.join(os.path.realpath(d), "loose")
            os.makedirs(loose, mode=0o755)
            os.chmod(loose, 0o755)
            with self.assertRaisesRegex(OSError, "not this user's own folder"):
                h.serve(lambda sid: "acme", lambda o: "/p", lambda *a: {}, path=os.path.join(loose, "hooks.sock"))
            self.assertEqual((gone["decision"], "cannot be reached" in gone["reason"]), ("stop", True))



class OrgFolderTest(unittest.TestCase):
    """of_review on PR 607: a relative orgs.yaml path is orgs.yaml's, as the registry loader reads it, never the
    listener's working folder's (/ under launchd, the home under a systemd user unit)."""

    def test_a_relative_path_is_read_against_orgs_yaml(self):
        import of_listen
        import types
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            conf = os.path.join(d, "home", ".config", "of")
            os.makedirs(conf)
            entries = {"acme": {"path": "../../orgx"}, "globex": {"path": os.path.join(d, "abs")}, "none": {}}
            cli = types.SimpleNamespace(ORGS_FILE=os.path.join(conf, "orgs.yaml"),
                                        _org_paths_entry=lambda org: entries.get(org))
            cwd = os.getcwd()
            try:
                os.chdir("/")
                self.assertEqual(of_listen.org_folder(cli, "acme"), os.path.join(d, "home", "orgx"))
            finally:
                os.chdir(cwd)
            self.assertEqual(of_listen.org_folder(cli, "globex"), os.path.join(d, "abs"))
            self.assertIsNone(of_listen.org_folder(cli, "none"))
            self.assertIsNone(of_listen.org_folder(cli, "absent"))

if __name__ == "__main__":
    unittest.main()
