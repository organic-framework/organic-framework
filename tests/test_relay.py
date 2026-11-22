"""of relay (client side): delivery, dedup, inbox, presence per server."""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_events  # noqa: E402
import of_relay_client as of_relay  # noqa: E402
import of_store  # noqa: E402

LOCAL = {"ofb:of_a": {"org": "ofb", "position": "of_a", "kind": "agent", "pane": "w1:p1", "session_id": "S"},
         "zeta:acme_owner": {"org": "zeta", "position": "acme_owner", "kind": "human", "pane": None, "session_id": None}}


class RelayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = self.tmp.name
        self.conn = of_store.connect()
        self.typed = []
        of_relay._SEEN.clear()

    def tearDown(self):
        self.conn.close()
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def fake(self, ok=True):
        def deliver(pane, text, session, sender, working_ok=False, skip_if=None):
            self.typed.append((pane, text))
            self.working_ok = working_ok
            return ok, None if ok else "pane gone"
        return deliver

    def test_agent_delivery_once_with_header(self):
        m = {"msg_id": "m1", "to": "ofb:of_a", "from": "zeta:acme_org", "text": "please review", "authority": "verified"}
        self.assertEqual(of_relay.deliver(self.conn, m, LOCAL, self.fake()), ("delivered", None))
        self.assertEqual(of_relay.deliver(self.conn, m, LOCAL, self.fake()), ("delivered", "duplicate"))
        self.assertEqual(len(self.typed), 1)
        pane, text = self.typed[0]
        self.assertEqual(pane, "w1:p1")
        self.assertTrue(text.startswith("Message from zeta:acme_org (via relay, authority: verified; machine ?; msg m1)"))
        self.assertIn("of send zeta:acme_org --reply-to m1", text)

    def test_human_goes_to_inbox_and_failures_are_reported(self):
        m = {"msg_id": "m2", "to": "zeta:acme_owner", "from": "ofb:of_a", "text": "hi", "authority": "none"}
        self.assertEqual(of_relay.deliver(self.conn, m, LOCAL, self.fake()), ("delivered", "inbox"))
        self.assertEqual(self.typed, [])
        self.assertEqual(self.conn.execute("SELECT text FROM inbox").fetchone()[0], "hi")
        bad = {"msg_id": "m3", "to": "ofb:of_a", "from": "x:y", "text": "t"}
        self.assertEqual(of_relay.deliver(self.conn, bad, LOCAL, self.fake(ok=False)), ("failed", "pane gone"))
        self.assertEqual(of_relay.deliver(self.conn, dict(bad, msg_id="m4", to="ofb:nobody"), LOCAL, self.fake()),
                         ("failed", "no live holder on this machine"))
        retry = of_relay.deliver(self.conn, bad, LOCAL, self.fake())  # a failed one is retried, not deduped
        self.assertEqual(retry, ("delivered", None))

    def test_busy_receiver_defers_without_dedup(self):
        m = {"msg_id": "m5", "to": "ofb:of_a", "from": "x:y", "text": "t"}
        busy = lambda pane, text, session, sender, working_ok=False, skip_if=None: ("deferred", "agent busy")
        self.assertEqual(of_relay.deliver(self.conn, m, LOCAL, busy), ("deferred", "agent busy"))
        self.assertEqual(of_relay.deliver(self.conn, m, LOCAL, self.fake()), ("delivered", None))

    def test_a_locked_store_never_repeats_an_injection(self):
        import sqlite3
        m = {"msg_id": "m9", "to": "ofb:of_a", "from": "zeta:x", "text": "hi"}
        holder = of_store.connect(timeout=0.1)
        holder.execute("BEGIN IMMEDIATE")  # another process holds the write lock (6.4 GB store, 1.2 GB WAL)
        try:
            busy = of_store.connect(timeout=0.1)
            self.assertEqual(of_relay.deliver(busy, m, LOCAL, self.fake()), ("delivered", None))
            for _ in range(3):  # the server re-offers it after the lease: typed once only
                self.assertEqual(of_relay.deliver(busy, m, LOCAL, self.fake()), ("delivered", "duplicate"))
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        self.assertEqual(len(self.typed), 1)
        of_relay._SEEN.clear()  # a restarted relay reads the file
        self.assertEqual(of_relay.deliver(self.conn, m, LOCAL, self.fake()), ("delivered", "duplicate"))
        self.assertEqual(len(self.typed), 1)
        h = {"msg_id": "m10", "to": "zeta:acme_owner", "from": "ofb:of_a", "text": "for a person"}
        holder = of_store.connect(timeout=0.1)
        holder.execute("BEGIN IMMEDIATE")
        try:
            with self.assertRaises(sqlite3.OperationalError):  # the inbox needs the store: the caller defers
                of_relay.deliver(of_store.connect(timeout=0.1), h, LOCAL, self.fake())
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        self.assertFalse(of_relay.seen("m10"))

    def test_presence_keeps_the_last_good_holders_on_a_registry_error(self):
        import threading
        calls, sent = {"n": 0}, []

        def load():
            calls["n"] += 1
            if calls["n"] > 1:
                raise ValueError("positions.yaml line 972: mapping values are not allowed here")
            return {"ofb:of_a": LOCAL["ofb:of_a"]}

        class C:
            def put_presence(self, body):
                sent.append(body)

            def poll_messages(self, t):
                stop.wait(0.05)
                return {}
        saved = of_relay.PRESENCE_EVERY
        of_relay.PRESENCE_EVERY = 0.01
        stop = threading.Event()
        try:
            _, threads = of_relay.run(load, lambda url: C(), {"ofb": "u"}, "u", log=lambda m: None, stop=stop)
            stop.wait(0.2)
        finally:
            stop.set()
            of_relay.PRESENCE_EVERY = saved
        self.assertGreater(calls["n"], 2)
        self.assertTrue(sent and all(b and b[0]["position"] == "of_a" for b in sent))

    def test_one_server_down_holds_back_no_other_servers_presence(self):
        """of_conductor, msg 01a203da-ade5: one organization's server was killed, and the other's mail waited 15
        minutes, since presence to every server sat in one try and stopped at the first that failed."""
        import threading
        sent = {"a": 0, "b": 0}

        class C:
            def __init__(self, url):
                self.url = url

            def put_presence(self, body):
                if self.url == "a":
                    raise ConnectionRefusedError("down")
                sent[self.url] += 1

            def poll_messages(self, t):
                stop.wait(0.05)
                return {}
        saved = of_relay.PRESENCE_EVERY
        of_relay.PRESENCE_EVERY = 0.01
        stop = threading.Event()
        try:
            of_relay.run(lambda: {"ofb:of_a": LOCAL["ofb:of_a"]}, C, {"ofa": "a", "ofb": "b"}, "a",
                         log=lambda m: None, stop=stop)
            stop.wait(0.3)
        finally:
            stop.set()
            of_relay.PRESENCE_EVERY = saved
        self.assertGreater(sent["b"], 0)                                     # "a" sorts first and is down

    def test_no_write_through_a_link_in_the_state_folder(self):
        """docs/guard.md, Gaps: on the agent host a sandboxed session may write OF's state folder, and the relay, which
        runs outside the sandbox, must not write through a link the session put there."""
        victim = os.path.join(self.tmp.name, "victim")
        with open(victim, "w") as fh:
            fh.write("kept\n")
        path = os.path.join(self.tmp.name, "injections.jsonl")
        os.symlink(victim, path)
        with self.assertRaises(OSError):
            of_relay._open_own(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        tmp = os.path.join(self.tmp.name, "relay-queues.json.123")
        os.symlink(victim, tmp)
        os.close(of_relay._open_new(tmp))                            # the planted link is removed, never followed
        self.assertFalse(os.path.islink(tmp))
        with open(victim) as fh:
            self.assertEqual(fh.read(), "kept\n")
        os.unlink(path)
        os.mkfifo(path)                                               # a FIFO fails at once, never holds the relay
        with self.assertRaises(OSError):
            of_relay._open_own(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        os.unlink(path)
        os.link(victim, path)                                         # a second name of another file is refused
        with self.assertRaises(OSError):
            of_relay._open_own(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        os.unlink(path)
        real_fstat = os.fstat                                         # replaced after it was opened: count 0, kept

        def replaced(fd):
            st = real_fstat(fd)
            return type("S", (), {"st_mode": st.st_mode, "st_uid": st.st_uid, "st_nlink": 0})()
        of_relay.record_queues({"ofb:of_a": {"n": 5, "oldest": "2026-11-22T10:00:00Z"}}, now=1000.0)
        with mock.patch.object(of_relay.os, "fstat", replaced):
            self.assertEqual(of_relay.read_queues()["ofb:of_a"]["n"], 5)
        qpath = of_relay._queues_path()                                # a read never follows a link either
        with open(victim, "w") as fh:
            json.dump({"queues": {"zeta:x": {"n": 7}}}, fh)
        if os.path.exists(qpath):
            os.unlink(qpath)
        os.symlink(victim, qpath)
        self.assertEqual(of_relay.read_queues(), {})
        os.unlink(qpath)
        with open(victim, "w") as fh:
            fh.write("kept\n")
        self.assertNotEqual(of_relay._tmp_suffix(), "")
        names = set()                                                 # each thread its own temporary name
        gate = threading.Barrier(4)                                   # all alive at once: no ident reused
        ths = [threading.Thread(target=lambda: (gate.wait(), names.add(of_relay._tmp_suffix()))) for _ in range(4)]
        [th.start() for th in ths]
        [th.join() for th in ths]
        self.assertEqual(len(names), 4)
        os.symlink(victim, path + ".2")
        of_relay.record_queues({"ofb:of_a": {"n": 1, "oldest": "2026-11-22T10:00:00Z"}}, now=1000.0)  # still works
        self.assertEqual(of_relay.read_queues()["ofb:of_a"]["n"], 1)
        with open(victim) as fh:
            self.assertEqual(fh.read(), "kept\n")

    def test_a_hung_server_holds_back_no_presence_and_a_failed_one_keeps_its_counts(self):
        """of_review on organic-framework PR 530: a server that hangs held the others' presence past the TTL, and a
        failed server's queues were recorded as depth 0."""
        import threading
        sent, rounds = {"b": 0}, {"a": 0}
        release = threading.Event()

        class C:
            def __init__(self, url):
                self.url = url

            def put_presence(self, body):
                if self.url == "a":
                    rounds["a"] += 1
                    if rounds["a"] == 1:
                        return {"queued": {"ofa:of_a": {"n": 4, "oldest": "2026-11-22T10:00:00Z"}},
                                "unsent": {"ofa:of_a": {"n": 1, "items": [{"msg_id": "u1", "to": "ofb:x"}]}}}
                    release.wait(5)                                       # hangs from its second round on
                    raise ConnectionRefusedError("down")
                sent["b"] += 1
                return {}

            def poll_messages(self, t):
                stop.wait(0.05)
                return {}
        saved = (of_relay.PRESENCE_EVERY, of_relay.PRESENCE_WAIT, of_relay.PRESENCE_CHECK)
        of_relay.PRESENCE_EVERY, of_relay.PRESENCE_WAIT, of_relay.PRESENCE_CHECK = 0.01, 0.05, 0.02
        stop = threading.Event()
        local = {"ofa:of_a": dict(LOCAL["ofb:of_a"], org="ofa"), "ofb:of_a": LOCAL["ofb:of_a"]}
        try:
            of_relay.run(lambda: local, C, {"ofa": "a", "ofb": "b"}, "b", log=lambda m: None, stop=stop)
            stop.wait(0.6)
            counts, unsent = of_relay.read_queues(), of_relay.read_unsent()
        finally:
            stop.set()
            release.set()
            of_relay.PRESENCE_EVERY, of_relay.PRESENCE_WAIT, of_relay.PRESENCE_CHECK = saved
        self.assertGreater(sent["b"], 3)                                  # b kept being told while a hung
        self.assertEqual(counts["ofa:of_a"]["n"], 4)                      # a's last count stands, never 0
        self.assertLess(counts["ofa:of_a"]["at"], time.time() - 0.3)      # with its own reading time, not restamped
        self.assertEqual(unsent["ofa:of_a"]["n"], 1)                      # and its holder's undelivered note stays

    def test_a_failed_server_after_a_restart_keeps_the_record_and_declares_no_step(self):
        """of_review on organic-framework PR 530: delivered messages read as waiting again, and the note vanished."""
        of_relay._LAST_QUEUES.clear()
        of_relay.record_queues({"ofa:of_a": {"n": 2, "oldest": "2026-11-22T10:00:00Z"}}, now=1000.0,
                               unsent={"ofa:of_a": {"n": 1, "items": [{"msg_id": "u1", "to": "ofb:x"}]}})
        of_relay._LAST_QUEUES.clear()                                     # the relay restarts; a's server is down
        of_relay.record_queues({"ofb:of_b": {"n": 1, "oldest": "2026-11-22T10:05:00Z"}}, now=2000.0,
                               keep_orgs={"ofa"})
        self.assertEqual(of_relay.read_queues()["ofa:of_a"], {"n": 2, "oldest": "2026-11-22T10:00:00Z", "at": 1000.0})
        self.assertEqual(of_relay.read_unsent()["ofa:of_a"]["n"], 1)
        rows = self.conn.execute("SELECT payload FROM event WHERE name = 'of.inbound_queue' ORDER BY rowid").fetchall()
        self.assertEqual([(json.loads(r[0])["position"], json.loads(r[0])["depth"]) for r in rows],
                         [("of_a", 2), ("of_b", 1)])                      # no step to 0 for a

    def test_a_holder_change_is_published_at_once(self):
        """of_capacity, 2026-11-07: after an account move the server said not_holder for minutes; a changed
        session (or pane, or kind) goes out on the next check, an unchanged set only every PRESENCE_EVERY."""
        import threading
        holders = [{"ofb:of_a": dict(LOCAL["ofb:of_a"], session_id="S1")}]
        sent = []

        class C:
            def put_presence(self, body):
                sent.append([h["session_id"] for h in body])

            def poll_messages(self, t):
                stop.wait(0.05)
                return {}
        saved = (of_relay.PRESENCE_EVERY, of_relay.PRESENCE_CHECK)
        of_relay.PRESENCE_EVERY, of_relay.PRESENCE_CHECK = 3600, 0.02
        stop = threading.Event()
        try:
            of_relay.run(lambda: holders[0], lambda url: C(), {"ofb": "u"}, "u", log=lambda m: None, stop=stop)
            stop.wait(0.15)
            self.assertEqual(sent, [["S1"]])                                   # unchanged: not sent again
            holders[0] = {"ofb:of_a": dict(LOCAL["ofb:of_a"], session_id="S1", state="working")}
            stop.wait(0.1)
            self.assertEqual(sent, [["S1"]])                                   # a busy state alone waits
            holders[0] = {"ofb:of_a": dict(LOCAL["ofb:of_a"], session_id="S2")}
            stop.wait(0.15)
            self.assertEqual(sent, [["S1"], ["S2"]])                           # a new holder goes out at once
        finally:
            stop.set()
            of_relay.PRESENCE_EVERY, of_relay.PRESENCE_CHECK = saved

    def test_taken_messages_are_never_typed_in(self):
        """of_architect's test: 3 queued for a busy pane, the agent takes 2, the pane goes idle: only the third."""
        msgs = [{"msg_id": f"q{i}", "to": "ofb:of_a", "from": "zeta:x", "text": f"t{i}", "created_at": "2026-11-06T12:00:00Z"}
                for i in range(3)]
        taken = []

        class Server:
            def queued_messages(self, to, session):
                assert (to, session) == ("ofb:of_a", "S")
                return {"messages": [m for m in msgs if m["msg_id"] not in taken]}

            def take_message(self, msg_id, to, session):
                taken.append(msg_id)
                return {"msg_id": msg_id, "state": "delivered"}

        busy = lambda pane, text, session, sender, working_ok=False, skip_if=None: ("deferred", "agent busy")
        for m in msgs:
            self.assertEqual(of_relay.deliver(self.conn, m, LOCAL, busy)[0], "deferred")
        listed = of_relay.pull(Server(), "ofb:of_a", "S")  # a plain list takes nothing
        self.assertEqual([t for _, t in listed], [False, False, False])
        got = of_relay.pull(Server(), "ofb:of_a", "S", take="q0")
        got += of_relay.pull(Server(), "ofb:of_a", "S", take="q1")
        self.assertEqual(taken, ["q0", "q1"])
        self.assertEqual(of_relay.read_queues()["ofb:of_a"]["n"], 1)  # of status sees it at once
        for m in msgs:  # the pane is idle now; the server may still offer a taken one (a lease race)
            of_relay.deliver(self.conn, m, LOCAL, self.fake())
        self.assertEqual([t for _, t in self.typed], [of_relay.header(msgs[2])])
        taken.append("q2")  # the server marked it delivered
        of_relay.pull(Server(), "ofb:of_a", "S")
        self.assertNotIn("ofb:of_a", of_relay.read_queues())  # an empty queue leaves the file

    def test_a_message_typed_here_is_not_counted_as_waiting(self):
        """A user organization (msg 01a203ee-9025): the server counts a message the relay already typed into the pane until its
        ack, the listing leaves it out (never shown twice), and the header read "1 message queued" over nothing."""
        class Server:
            def queued_messages(self, to, session):
                return {"messages": [{"msg_id": "T1", "to": to, "from": "zeta:x", "text": "typed", "state": "leased"},
                                     {"msg_id": "T2", "to": to, "from": "zeta:x", "text": "new", "state": "queued"}],
                        "total": 2, "urgent": 0}

            def take_message(self, msg_id, to, session):
                return {}
        of_relay.mark_seen("T1", "typed", to="ofb:of_a")                  # the relay typed it; its ack is on the way
        got = of_relay.pull(Server(), "ofb:of_a", "S")
        self.assertEqual([m["msg_id"] for m, _ in got], ["T2"])
        q = of_relay.LAST_QUEUE["ofb:of_a"]
        self.assertEqual((q["total"], q["listed"], q["typed_here"]), (1, 1, 1))   # the header counts what it shows
        self.assertEqual(of_relay.read_queues()["ofb:of_a"]["n"], 1)

    def test_a_take_finds_what_newest_listed(self):
        """of_review on organic-framework PR 527: a take of an id --newest listed exited 2, since the take read
        only the oldest hundred."""
        class Server:
            def queued_messages(self, to, session, newest=False):
                old = [{"msg_id": f"O{i}", "to": to, "from": "zeta:x", "text": "o", "state": "queued"} for i in range(2)]
                new = [{"msg_id": "N9", "to": to, "from": "zeta:x", "text": "n", "state": "queued"}]
                return {"messages": new if newest else old, "total": 3, "urgent": 0}

            def take_message(self, msg_id, to, session):
                taken.append(msg_id)
                return {}
        taken = []
        of_relay.pull(Server(), "ofb:of_a", "S", newest=True)               # the listing showed N9
        got = of_relay.pull(Server(), "ofb:of_a", "S", take="N9")
        self.assertEqual(([m["msg_id"] for m, t in got if t], taken), (["N9"], ["N9"]))

    def test_take_all_after_newest_takes_the_listed_and_prints_no_backlog_as_new(self):
        """of_review on organic-framework PR 527: with a backlog past the limit, --take all printed up to a hundred
        old queued messages from the other order as new."""
        old_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600))

        class Server:
            def queued_messages(self, to, session, newest=False):
                old = [{"msg_id": f"O{i}", "to": to, "from": "zeta:x", "text": "o", "created_at": old_at}
                       for i in range(3)]
                new = [{"msg_id": "N9", "to": to, "from": "zeta:x", "text": "n", "created_at": old_at}]
                return {"messages": new if newest else old, "total": 4, "urgent": 0}

            def take_message(self, msg_id, to, session):
                taken.append(msg_id)
                return {}
        taken = []
        of_relay.pull(Server(), "ofb:of_a", "S", newest=True)               # the listing showed N9 alone
        got = of_relay.pull(Server(), "ofb:of_a", "S", take="all")
        self.assertEqual(taken, ["N9"])
        self.assertEqual([m["msg_id"] for m, _ in got if m.get("_new")], [])  # old and unlisted: not printed as new

    def test_a_take_under_a_live_lease_leaves_it_to_the_pane(self):
        class Leased(Exception):
            status = 409

        class Server:
            def queued_messages(self, to, session):
                return {"messages": [{"msg_id": "L1", "to": to, "from": "zeta:x", "text": "hi", "state": "leased"}]}

            def take_message(self, msg_id, to, session):
                raise Leased("of-server 409: leased")
        of_relay.pull(Server(), "ofb:of_a", "S")  # listed first: --take all takes only what a listing showed
        (m, taken), = of_relay.pull(Server(), "ofb:of_a", "S", take="all")
        self.assertFalse(taken)
        self.assertEqual(m["_note"], "arriving in your pane now")
        self.assertFalse(of_relay.seen("L1"))  # the relay types it: one delivery, in the pane
        (m, taken), = of_relay.pull(Server(), "ofb:of_a", "S", take="all", pane_busy=True)
        self.assertTrue(taken)  # a busy pane would only defer it: taken here
        self.assertTrue(of_relay.seen("L1"))  # and the relay skips it on its next offer
        self.assertEqual(of_relay.pull(Server(), "ofb:of_a", "S", take="all", pane_busy=True), [])  # never listed twice

    def test_a_message_moved_meanwhile_is_neither_taken_nor_seen(self):
        """of_secrets-review on organic-framework PR 375: take's 409 moved was read as 409 leased, so a busy pane kept
        the moved message as taken and marked it seen, and a successor on this machine never got it."""
        import of_client

        class Server:
            def queued_messages(self, to, session):
                return {"messages": [{"msg_id": "M1", "to": to, "from": "ofb:x", "text": "hi", "state": "queued"}]}

            def take_message(self, msg_id, to, session):
                raise of_client.ServerError(409, '{"type": "moved", "title": "moved", "status": 409}')
        of_relay.pull(Server(), "ofb:of_a", "S")
        self.assertEqual(of_relay.pull(Server(), "ofb:of_a", "S", take="all", pane_busy=True), [])
        self.assertFalse(of_relay.seen("M1"))
        self.assertEqual(of_relay.taken_messages("ofb:of_a"), [])

    def test_take_all_takes_only_what_a_listing_showed_and_keeps_the_text(self):
        """From a user organization, 2026-11-08: `of inbox --take all >/dev/null` took a reply that arrived after the
        agent's listing, and its text was kept nowhere."""
        queue = [{"msg_id": "a1", "to": "ofb:of_a", "from": "zeta:x", "text": "read me", "created_at": "2026-11-08T10:00Z"}]
        taken = []

        class Server:
            def queued_messages(self, to, session):
                return {"messages": [m for m in queue if m["msg_id"] not in taken]}

            def take_message(self, msg_id, to, session):
                taken.append(msg_id)
                return {"msg_id": msg_id, "state": "delivered"}
        of_relay.pull(Server(), "ofb:of_a", "S")                                   # the agent reads a1
        queue.append({"msg_id": "b2", "to": "ofb:of_a", "from": "zeta:y", "text": "delete node-3",   # arrives meanwhile
                      "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 5))})
        got = of_relay.pull(Server(), "ofb:of_a", "S", take="all")                 # output thrown away
        self.assertEqual(taken, ["a1"])
        self.assertEqual([(m["msg_id"], t, m.get("_new")) for m, t in got],
                         [("a1", True, None), ("b2", False, of_relay.NEW_SINCE_LISTING)])
        self.assertEqual(of_relay.pull(Server(), "ofb:of_a", "S", take="all")[0][1], False)  # still not listed
        self.assertEqual(of_relay.pull(Server(), "ofb:of_a", "OTHER")[0][0]["msg_id"], "b2")  # another session's list
        self.assertEqual(of_relay.pull(Server(), "ofb:of_a", "S", take="all")[0][1], False)  # does not count here
        of_relay.pull(Server(), "ofb:of_a", "S", take="b2")                        # named by id: taken
        self.assertEqual(taken, ["a1", "b2"])
        kept = of_relay.taken_messages("ofb:of_a")
        self.assertEqual([(m["msg_id"], m["text"]) for _, m in kept], [("a1", "read me"), ("b2", "delete node-3")])
        self.assertEqual(of_relay.taken_messages("ofb:of_other"), [])
        self.assertEqual(os.stat(of_relay._taken_path()).st_mode & 0o777, 0o600)

    def test_a_take_in_another_process_reaches_the_running_relay(self):
        """2026-11-06: the relay loaded its seen set once, so a take by of inbox --mine was typed in."""
        m = {"msg_id": "x1", "to": "ofb:of_a", "from": "zeta:x", "text": "hi"}
        self.assertFalse(of_relay.seen("x1"))  # the running relay has read the file
        with open(of_relay._seen_path(), "a") as fh:  # another process (the CLI) appends its take
            fh.write(json.dumps({"msg_id": "x1", "outcome": "pulled"}) + "\n")
        self.assertEqual(of_relay.deliver(self.conn, m, LOCAL, self.fake()), ("delivered", "duplicate"))
        self.assertEqual(self.typed, [])

    def test_a_take_during_the_relays_wait_wins(self):
        m = {"msg_id": "r1", "to": "ofb:of_a", "from": "zeta:x", "text": "hi"}

        def slow(pane, text, session, sender, working_ok=False, skip_if=None):
            of_relay.mark_seen("r1", "pulled")  # the agent ran of inbox --mine --take while the relay waited for idle
            if skip_if and skip_if():
                return "skipped", "pulled"
            self.typed.append((pane, text))
            return True, None
        self.assertEqual(of_relay.deliver(self.conn, m, LOCAL, slow), ("delivered", "duplicate"))
        self.assertEqual(self.typed, [])

    def test_queue_file_note_and_events(self):
        import calendar
        import time as _t
        now = calendar.timegm(_t.strptime("2026-11-06T13:00:00", "%Y-%m-%dT%H:%M:%S"))
        of_relay._LAST_QUEUES.clear()
        of_relay.record_queues({"ofb:of_a": {"n": 3, "oldest": "2026-11-06T12:15:00Z"}}, now=now)
        self.assertEqual(of_relay.queue_note("ofb:of_a", now=now),
                         "3 messages waiting for you (oldest 45m): of inbox --mine")
        self.assertIsNone(of_relay.queue_note("ofb:other", now=now))
        of_relay.record_queues({"ofb:of_a": {"n": 3, "oldest_created_at": "2026-11-06T12:15:00Z"}}, now=now)
        of_relay.record_queues({"ofb:of_a": {"n": 3, "oldest": "2026-11-06T12:20:00Z"}}, now=now)  # took one, got one
        of_relay.record_queues({}, now=now)  # drained
        rows = self.conn.execute("SELECT payload FROM event WHERE name = 'of.inbound_queue' ORDER BY rowid").fetchall()
        self.assertEqual([(json.loads(r[0])["depth"], json.loads(r[0])["oldest_created_at"]) for r in rows],
                         [(3, "2026-11-06T12:15:00Z"), (3, "2026-11-06T12:20:00Z"), (0, None)])  # changes only

    def test_status_names_what_the_sender_sent_and_has_not_landed(self):
        """2026-11-09, from a user organization: a GO reached k8s_keeper 24 minutes late; its sender could not
        see it was still queued."""
        import calendar
        import time as _t
        now = calendar.timegm(_t.strptime("2026-11-09T11:14:00", "%Y-%m-%dT%H:%M:%S"))
        item = lambda i, created, **k: dict({"msg_id": i, "to": "zeta:k8s_keeper", "created_at": created,
                                             "priority": "normal", "state": "queued", "present": True,
                                             "reason": "batched: the receiver's 15-minute window",
                                             "next_at": "2026-11-09T11:14:16.000Z"}, **k)
        of_relay._LAST_QUEUES.clear()
        of_relay.record_queues({}, now=now, unsent={"zeta:dpc": {"n": 2, "items": [
            item("g1", "2026-11-09T11:05:39.650Z"), item("g2", "2026-11-09T11:12:00.000Z")]}})
        self.assertEqual(of_relay.queue_note("zeta:dpc", now=now),
                         "1 message you sent is not delivered yet: to zeta:k8s_keeper (8m; batched, next in 1m)")  # g2: <5m
        of_relay.record_queues({}, now=now, unsent={"zeta:dpc": {"n": 4, "items": [
            item("o1", "2026-11-06T11:00:00.000Z", present=False, next_at=None),           # 3 days old: not repeated
            item("g1", "2026-11-09T11:05:39.650Z"), item("g3", "2026-11-09T11:06:00.000Z"),
            item("h1", "2026-11-09T11:07:00.000Z", to="zeta:catalogue_keeper", state="leased")]}})
        self.assertEqual(of_relay.queue_note("zeta:dpc", now=now),
                         "3 messages you sent are not delivered yet: 2 to zeta:k8s_keeper (oldest 8m; batched, next in 1m);"
                         " to zeta:catalogue_keeper (7m; being typed now)")
        of_relay.record_queues({"zeta:dpc": {"n": 1, "oldest": "2026-11-09T11:04:00Z"}}, now=now, unsent={"zeta:dpc": {
            "n": 1, "items": [item("g1", "2026-11-09T11:05:39.650Z", present=False, next_at=None)]}})
        note = of_relay.queue_note("zeta:dpc", now=now)
        self.assertEqual(note.split("\n"), ["1 message waiting for you (oldest 10m): of inbox --mine",
                                            "1 message you sent is not delivered yet: to zeta:k8s_keeper (8m; no live holder)"])
        of_relay.mark_seen("g1", "pane", to="zeta:k8s_keeper")                               # typed in here since
        self.assertEqual(of_relay.queue_note("zeta:dpc", now=now), "1 message waiting for you (oldest 10m): of inbox --mine")
        of_relay.set_queue("zeta:dpc", 0, None, now=now)                                     # of inbox --mine keeps unsent
        self.assertEqual(of_relay.read_unsent()["zeta:dpc"]["n"], 1)

    def test_status_counts_only_what_will_not_arrive_by_itself(self):
        """of_architect: 'of status' said 1 waiting for a message the relay was typing; the take found 0."""
        import time as _t
        now = _t.time()
        of_relay._LAST_QUEUES.clear()
        of_relay.record_queues({"ofb:of_a": {"n": 2, "oldest": "2026-11-06T12:15:00Z"}}, now=now - 30)
        of_relay.set_arriving("ofb:of_a", "m1", True, now=now)          # deferred until the agent is idle
        note = of_relay.queue_note("ofb:of_a", now=now)
        self.assertTrue(note.startswith("1 message waiting for you"), note)
        self.assertIn("1 message arriving in your pane now", note)
        of_relay.mark_seen("m2", "pulled", to="ofb:of_a")                # the agent took the other one
        self.assertTrue(of_relay.queue_note("ofb:of_a", now=now).startswith("1 message arriving in your pane now"))
        of_relay.set_arriving("ofb:of_a", "m1", False, now=now)
        of_relay.mark_seen("m1", "pane", to="ofb:of_a")                   # typed in
        self.assertIsNone(of_relay.queue_note("ofb:of_a", now=now))       # the snapshot is stale: nothing left
        self.assertEqual(of_relay.arriving("ofb:of_a", now=now + 1000), set())

    def test_deliver_marks_arriving_while_deferred(self):
        m = {"msg_id": "d1", "to": "ofb:of_a", "from": "zeta:x", "text": "hi"}
        busy = lambda pane, text, session, sender, working_ok=False, skip_if=None: ("deferred", "busy")
        self.assertEqual(of_relay.deliver(self.conn, m, LOCAL, busy)[0], "deferred")
        self.assertEqual(of_relay.arriving("ofb:of_a"), {"d1"})
        ok = lambda pane, text, session, sender, working_ok=False, skip_if=None: (True, None)
        self.assertEqual(of_relay.deliver(self.conn, m, LOCAL, ok)[0], "delivered")
        self.assertEqual(of_relay.arriving("ofb:of_a"), set())

    def test_a_restart_still_steps_an_emptied_queue_to_zero(self):
        of_relay._LAST_QUEUES.clear()
        of_relay.record_queues({"ofb:of_a": {"n": 3, "oldest": "2026-11-06T12:15:00Z"}})
        of_relay.set_queue("ofb:of_a", 0, None)  # the agent took all (of inbox --mine): the status file says 0
        of_relay._LAST_QUEUES.clear()  # the relay restarts; the queue emptied meanwhile
        of_relay.record_queues({})
        rows = self.conn.execute("SELECT payload FROM event WHERE name = 'of.inbound_queue' ORDER BY rowid").fetchall()
        self.assertEqual([json.loads(r[0])["depth"] for r in rows], [3, 0])

    def test_at_path_mentions_never_reach_a_pane(self):
        """2026-11-06: a relayed '@<path>' made Claude Code read a header file with a token into another session."""
        calls = []

        class R:
            def __init__(self, rc=0):
                self.returncode, self.stdout, self.stderr = rc, "", ""
        saved = of_relay.subprocess.run
        of_relay.subprocess.run = lambda argv, **k: (calls.append(argv), R())[1]
        try:
            ok, _ = of_relay.herdr_deliver("w1:p1", "curl -H @/Users/alex/.config/g/header.txt and mail vp@example.me",
                                           "S", "zeta:x")
        finally:
            of_relay.subprocess.run = saved
        typed = calls[-1][-1]
        self.assertTrue(ok)
        self.assertNotIn("@/Users", typed)
        self.assertIn("\uff20/Users/alex/.config/g/header.txt", typed)
        self.assertIn("vp@example.me", typed)  # e-mail addresses keep their at sign
        ev = self.conn.execute("SELECT payload FROM event WHERE name = 'of.injected_prompt'").fetchone()
        self.assertEqual(json.loads(ev[0])["mentions_neutralized"], 1)

    def test_every_at_sign_outside_a_word_is_neutralized(self):
        """The quoted form, a bare name, a line start, a parenthesis, a double at sign; e-mail keeps its at sign."""
        F = "\uff20"
        for text, want in (('see @"My Docs/key file.txt" now', f'see {F}"My Docs/key file.txt" now'),
                           ("read @credentials and @secrets", f"read {F}credentials and {F}secrets"),
                           ("@of_architect first\n@notes", f"{F}of_architect first\n{F}notes"),
                           ("(@config) [@x] '@y'", f"({F}config) [{F}x] '{F}y'"),
                           ("@@twice", f"{F}{F}twice"),
                           ("mail vp@example.me or a.b+c@d.io", "mail vp@example.me or a.b+c@d.io")):
            self.assertEqual(of_relay.neutralize_mentions(text)[0], want, text)
        self.assertEqual(of_relay.neutralize_mentions("@a @b x@y")[1], 2)

    def _screen(self, box, dim=False):
        rule = "\x1b[2m" + "─" * 60 + "\x1b[0m"
        text = ("\x1b[2m" + box + "\x1b[0m") if dim else box
        return f"some output\n{rule}\n❯\xa0\x1b[0m{text}\n{rule}\n  ⏵⏵ bypass permissions on\n"

    def _deliver_with_box(self, screen, keys=False):
        calls = []

        class R:
            def __init__(self, out=""):
                self.returncode, self.stdout, self.stderr = 0, out, ""
        saved = of_relay.subprocess.run
        of_relay.subprocess.run = lambda argv, **k: (calls.append(argv), R(screen if argv[1:3] == ["pane", "read"] else ""))[1]
        try:
            res = of_relay.herdr_deliver("w3:pS", "of_x: hello", "S", "ofb:of_x")
        finally:
            of_relay.subprocess.run = saved
        return res, [c for c in calls if c[1:3] == (["pane", "send-keys"] if keys else ["agent", "prompt"])]

    def test_a_box_holding_text_defers_and_is_never_appended_to(self):
        """of_architect, 2026-11-07: of_lab's blocked prompt stayed in its box and ~20 deliveries were appended."""
        res, typed = self._deliver_with_box(self._screen("You hold … the blocked prompt\n[reply: of send of_architect]"))
        self.assertEqual((res[0], typed), ("deferred", []))
        for _ in range(2):
            self._deliver_with_box(self._screen("a person's half-typed words"))
        self.assertEqual(of_relay.read_stuck()["w3:pS"]["count"], 3)
        res, typed = self._deliver_with_box(self._screen("what is the status of the limits?", dim=True))  # a suggestion
        self.assertEqual((res[0], len(typed)), (True, 1))
        self.assertNotIn("w3:pS", of_relay.read_stuck())  # an empty box resets the count
        res, typed = self._deliver_with_box(self._screen(""))
        self.assertEqual((res[0], len(typed)), (True, 1))

    def test_a_paste_placeholder_is_marked_and_the_relays_own_is_sent_once(self):
        """of_platform, msg 01a1c8a0 (from a user organization): a code-audit position sat three hours behind
        '[Pasted text #4 +69 lines]', a delivery whose Enter was lost while the session compacted."""
        self._deliver_with_box(self._screen("a person's half-typed words"))
        self.assertFalse(of_relay.read_stuck()["w3:pS"]["paste"])
        res, typed = self._deliver_with_box(self._screen(""))                       # typed: 1 line break (framed)
        self.assertEqual((res[0], len(typed)), (True, 1))
        held = self._screen("[Pasted text #4 +1 lines]")
        res, calls = self._deliver_with_box(held, keys=True)
        self.assertEqual(res[0], "deferred")
        self.assertIn("pressed Enter", res[1])
        self.assertEqual(calls, [["herdr", "pane", "send-keys", "w3:pS", "Enter"]])
        self.assertTrue(of_relay.read_stuck()["w3:pS"]["paste"])
        res, calls = self._deliver_with_box(held, keys=True)                         # once only
        self.assertEqual((res[0], calls), ("deferred", []))
        with open(os.path.join(of_events.STATE_DIR, "injections.jsonl")) as fh:
            self.assertEqual(len(fh.read().splitlines()), 2)                         # declared again when pressed

    def test_the_relays_own_text_held_plain_in_the_box_is_sent_once(self):
        """of_platform, msg 01a1e8d8-5537: a framed text sat 17 minutes in a box as plain text, not a paste placeholder,
        and the relay deferred behind it; the relay's own last delivery shown so is pressed once."""
        res, typed = self._deliver_with_box(self._screen(""))
        self.assertEqual((res[0], len(typed)), (True, 1))
        shown = typed[0][-1].replace("\n", "\n  ")                                  # the box indents continuation lines
        res, calls = self._deliver_with_box(self._screen(shown), keys=True)
        self.assertEqual(res[0], "deferred")
        self.assertIn("pressed Enter", res[1])
        self.assertEqual(calls, [["herdr", "pane", "send-keys", "w3:pS", "Enter"]])
        res, calls = self._deliver_with_box(self._screen(shown), keys=True)          # once only
        self.assertEqual((res[0], calls), ("deferred", []))
        res, typed = self._deliver_with_box(self._screen(""))
        for other in (shown + " and more", "a person's own words"):                  # not ours: left alone
            res, calls = self._deliver_with_box(self._screen(other), keys=True)
            self.assertEqual((res[0], calls), ("deferred", []), other)

    def test_a_placeholder_that_is_not_the_relays_own_is_left_alone(self):
        self._deliver_with_box(self._screen(""))
        for box in ("[Pasted text #2 +40 lines]", "[Pasted text #2 +1 lines] and words"):  # another paste; a draft
            res, calls = self._deliver_with_box(self._screen(box), keys=True)
            self.assertEqual((res[0], calls), ("deferred", []), box)
        with of_store.write_tx(self.conn):
            self.conn.execute("INSERT INTO event (event_id, name, ts, session_id, source, payload, record) VALUES "
                              "('e1', 'of.agent_message_in', '2999-01-01T00:00:00Z', 'S', 'hook', '{}', '{}')")
        res, calls = self._deliver_with_box(self._screen("[Pasted text #2 +1 lines]"), keys=True)
        self.assertEqual((res[0], calls), ("deferred", []))                          # the session took it: not ours

    def test_input_box_text(self):
        self.assertEqual(of_relay.input_box_text(self._screen("")), "")
        self.assertEqual(of_relay.input_box_text(self._screen("abc")), "abc")
        self.assertEqual(of_relay.input_box_text(self._screen("abc", dim=True)), "")
        self.assertIsNone(of_relay.input_box_text("no box here\n"))

    def test_a_side_view_beside_the_conversation_is_not_the_box(self):
        """2026-11-17, a user organization's structure keeper (msg 01a1e907-653e): with a diff view drawn on the right
        half, the rule above the box was the view's, 2,989 characters were read as the box, and three messages waited
        22 minutes behind an empty prompt."""
        left, right = 110, 100
        view_rule = " " * left + "─" * 26
        lines = ["agent reply line " * 3 + " " * 40 + "New file not yet staged", view_rule]
        for k in range(12):
            lines.append(f"  reply text {k} ".ljust(left) + ("diff +line" if k % 3 else "─" * right))
        lines.append("✻ Working… · esc to interrupt".ljust(left) + "─" * right)   # the box's top rule broken by the view
        for box, want in (("", ""), ("abc", "abc"), ("first line\n  second line", "first line\n  second line")):
            screen = "\n".join(lines + ["❯\xa0" + box, "─" * (left + right), "  ⏵⏵ bypass permissions on"])
            self.assertEqual(of_relay.input_box_text(screen), want, box)
            # Claude Code's own status drawn right-aligned to the pane's edge on the prompt line is not typed text
            first, *rest = (box or "").split("\n")
            status = "model · 12% context"
            prompt = ("❯\xa0" + first).ljust(left + right - 2 - len(status)) + "\x1b[38;2;78;186;101m" + status + "\x1b[0m"
            screen = "\n".join(lines + [prompt] + rest + ["─" * (left + right), "  ⏵⏵ bypass permissions on"])
            self.assertEqual(of_relay.input_box_text(screen), want, box)

    def test_a_box_whose_bottom_rule_is_not_drawn(self):
        """2026-11-17, a user organization's structure keeper (msg 01a1e94f-2ad8): beside a side view the box's bottom
        rule was not drawn (the prompt line, an empty line, the footer), the box was read from the conversation above
        the top rule, 4,277 characters, and a message waited 20 minutes; earlier the footer was drawn over the prompt
        line itself."""
        rule = "─" * 120
        convo = ["a reply " * 10, "  more " + " " * 80 + "─" * 30, "✻ Working… · esc to interrupt", " " * 118]
        for tail, want in ((["❯ ", "", "  ⏵⏵ bypass permissions on · 1 shell"], ""),
                           (["❯ ⏵⏵ bypass permissions on · 1 shell", "", "  ⏵⏵ bypass permissions on"], ""),
                           (["❯ half typed", "", "  ⏵⏵ bypass permissions on"], "half typed"),
                           (["❯ first", "  second", "", "  ⏵⏵ bypass permissions on"], "first\n  second"),
                           (["❯ ", "  ⏸ plan mode on"], ""),
                           # of_review on organic-framework PR 285: typed text after an empty line, and typed text
                           # that starts like a footer, are typed
                           (["❯", "", "  the actual request", "", "  ⏵⏵ bypass permissions on"], "the actual request"),
                           (["❯ ? for shortcuts, what are they", "", "  ⏵⏵ bypass permissions on"],
                            "? for shortcuts, what are they"),
                           (["❯ ⏸ pause the deploy", rule, "  ⏵⏵ bypass permissions on"], "⏸ pause the deploy"),
                           (["❯ ", rule, "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← 1 agent"], ""),
                           (["❯ ⏵⏵ bypass permissions on · 20 shells · ← 1 agent · ↓ to manage", "",
                             "     1 agent running"], "")):
            screen = "\n".join(convo + [rule] + tail)
            self.assertEqual(of_relay.input_box_text(screen), want, tail)

    def test_typed_text_after_a_gap_or_an_indented_prompt_mark_is_still_typed(self):
        """of_review on organic-framework PR 280: text after a wide gap that stops short of the pane's edge, and a box
        whose later line is an indented `❯` (a pasted shell prompt), read as empty, so OF would type into them."""
        rule = "─" * 80
        for box, want in (("❯" + " " * 11 + "hello", "hello"), ("❯ a          b", "a          b"),
                          ("❯ first line\n  ❯", "first line\n  ❯"), ("❯ first line\n  ❯ ls -la", "first line\n  ❯ ls -la"),
                          ("❯ fix the build", "fix the build")):
            screen = "\n".join(["a reply", rule, box, rule, "  ⏵⏵ bypass permissions on"])
            self.assertEqual(of_relay.input_box_text(screen), want, box)
        screen = "\n".join(["a reply", rule, "❯".ljust(80 - 2 - 11) + "model · 12%", rule])   # right-aligned to the edge
        self.assertEqual(of_relay.input_box_text(screen), "")

    def test_a_tools_text_is_framed_unless_it_is_the_owners(self):
        """of_architect, 2026-11-07: of_lab took a one-line tool text for the founder's words."""
        f = of_relay.frame
        self.assertEqual(f("hello", "of_platform"), "Message from of_platform (via of, authority: none)\nhello")
        self.assertTrue(f("Reminder: hand-over", "of-pulse:a").startswith("Message from a (via of pulse, authority: none)"))
        self.assertTrue(f("brief", "of_spawn", "of spawn").startswith("Message from of_spawn (via of spawn,"))
        for framed in ("Message from zeta:x (via relay, authority: none)\nhi", "Question from k8s_keeper (unit p, …): x",
                       "Answer from the founder to your question 1234abcd: yes"):
            self.assertEqual(f(framed, "s"), framed)
        res, typed = self._deliver_with_box(self._screen(""))
        self.assertTrue(typed[0][-1].startswith("Message from ofb:of_x (via of, authority: none)\nof_x: hello"))

    def test_the_owners_words_from_the_popup_are_not_framed(self):
        calls = []

        class R:
            def __init__(self):
                self.returncode, self.stdout, self.stderr = 0, "", ""
        saved = of_relay.subprocess.run
        of_relay.subprocess.run = lambda argv, **k: (calls.append(argv), R())[1]
        try:
            of_relay.herdr_deliver("w1:p1", "yes, go ahead", "S", "owner", human_channel="asks-ui")
        finally:
            of_relay.subprocess.run = saved
        self.assertEqual(calls[-1][-1], "yes, go ahead")

    def test_a_hook_record_still_in_the_spool_confirms_the_delivery(self):
        """2026-11-11, of_platform: under load the hook spooled its record, the store got it five minutes later, and
        the relay typed the same message three times."""
        import of_store
        frame = ("Message from ofb:of_capacity (via relay, authority: none: no grant; machine m; "
                 "msg 01a1cc93-d929-71bf-bf67-6f6e5bb9b26e, reply to x)")
        self.assertFalse(of_relay.hook_saw(self.conn, "S-on", "01a1cc93-d929-71bf-bf67-6f6e5bb9b26e"))
        rec = of_events.record("agent_message_in", {"authority_detail": frame, "via": "relay"}, session="S-on",
                               source="hook")
        of_store.spool_append("events.jsonl", rec)
        self.assertTrue(of_relay.hook_saw(self.conn, "S-on", "01a1cc93-d929-71bf-bf67-6f6e5bb9b26e"))
        self.assertFalse(of_relay.hook_saw(self.conn, "S-other", "01a1cc93-d929-71bf-bf67-6f6e5bb9b26e"))
        reads = []
        saved_open = of_relay.open if hasattr(of_relay, "open") else None
        import builtins
        real_open = builtins.open
        of_relay.open = lambda *a, **k: (reads.append(a[0]), real_open(*a, **k))[1]
        try:
            for _ in range(5):                               # one cycle, many checks: one read of an unchanged file
                of_relay.hook_saw(self.conn, "S-on", "01a1cc93-d929-71bf-bf67-6f6e5bb9b26e")
        finally:
            if saved_open is None:
                del of_relay.open
            else:
                of_relay.open = saved_open
        self.assertLessEqual(len([r for r in reads if str(r).endswith("events.jsonl")]), 1)
        own = of_events.record("agent_message_in", {"msg_id": "01a1cc96-66be", "via": "relay"}, session="S-on")
        of_store.spool_append("events.jsonl", own)        # the relay's own record is no confirmation
        self.assertFalse(of_relay.hook_saw(self.conn, "S-on", "01a1cc96-66be"))

    def test_every_message_goes_at_once_fyi_never(self):
        """The founder, 2026-11-11: no 15-minute window. Whatever is queued for a receiver is one joined prompt,
        typed as soon as it is idle, however recently it got the last one; fyi is never typed; a person's inbox
        takes every message one by one."""
        local = dict(LOCAL)
        to = next(k for k, v in local.items() if v.get("kind") != "human")
        m = lambda i, **k: dict({"msg_id": f"b{i}", "to": to, "from": "zeta:x", "text": f"t{i}"}, **k)  # noqa: E731
        plan = of_relay.plan_batch([m(1), m(2), m(3, priority="fyi"), m(4, priority="urgent")], local)
        self.assertEqual(sorted((a, len(ms), r) for a, ms, r in plan),
                         [("deliver", 3, None), ("fyi", 1, of_relay.FYI_RETRY_S)])
        human = next((k for k, v in local.items() if v.get("kind") == "human"), None)
        if human:  # a person's fyi goes to their inbox like any message
            plan = of_relay.plan_batch([dict(m(9, priority="fyi"), to=human)], local)
            self.assertEqual([a for a, _, _ in plan], ["single"])

    def test_a_rule_with_the_sessions_name_is_a_rule(self):
        """2026-11-11, of_platform: '──── shop_auditor ─' in a 72-column pane was 75% rule characters; the box
        was read from the rule above the agent's last reply and 21 deliveries waited over an empty box."""
        named = "─" * 53 + " shop_auditor ─"
        screen = ("the agent's last reply\n" + "─" * 72 + "\nmore reply text\n\u273b Cogitated for 33s\n\n" + named
                  + "\n\u276f\xa0\n" + "─" * 72 + "\n  \u23f5\u23f5 bypass permissions on \u00b7 \u29c9 scope2")
        self.assertTrue(of_relay._is_rule(named))
        self.assertEqual(of_relay.input_box_text(screen), "")
        self.assertFalse(of_relay._is_rule("── a short line of text that only starts with a rule"))
        narrow = "─" * 13 + " shop_throughput-owner ─"      # a 35-column pane (2026-11-12)
        screen = ("a reply\n" + "─" * 35 + "\nmore reply\n\n" + narrow + "\n\u276f\xa0\n" + "─" * 35 + "\n  bypass")
        self.assertTrue(of_relay._is_rule(narrow))
        self.assertEqual(of_relay.input_box_text(screen), "")
        self.assertFalse(of_relay._is_rule("──── two words ─"))       # a label is one name, never prose

    def test_joined_delivery_types_one_prompt_with_each_frame(self):
        local = dict(LOCAL)
        to = next(k for k, v in local.items() if v.get("kind") != "human")
        typed = []
        ok = lambda pane, text, session, sender, working_ok=False, skip_if=None: (typed.append(text), (True, None))[1]
        ms = [{"msg_id": f"j{i}", "to": to, "from": "zeta:x", "text": f"body {i}"} for i in (1, 2)]
        self.assertEqual(of_relay.deliver_joined(self.conn, ms, local, ok), ("delivered", None))
        self.assertEqual(len(typed), 1)
        self.assertEqual(typed[0].count("Message from zeta:x (via relay"), 2)
        self.assertTrue(of_relay.seen("j1") and of_relay.seen("j2"))

    def test_a_dialog_on_screen_defers(self):
        """of_capacity, 2026-11-07: an exit dialog swallowed two relay messages to aws_keeper."""
        screen = "Background work is running\n  1. Exit anyway\n  2. Stay\nEnter to confirm · Esc to cancel\n"
        self.assertTrue(of_relay.dialog_open(screen))
        self.assertFalse(of_relay.dialog_open(self._screen("")))
        res, typed = self._deliver_with_box(screen)
        self.assertEqual((res, typed), (("deferred", "a dialog is open in the pane"), []))

    def test_the_compaction_offer_is_a_dialog(self):
        """of_capacity, 2026-11-10 (msg 01a1c64d): after a 432k session's first Fable turn Claude Code offered to compact;
        its footer is lower case, and Enter there would compact the conversation."""
        compact = "\n".join(["─" * 40, " We recommend compacting earlier", "", "   ❯ Autocompact at ‹ 400k › tokens",
                             "     Keep 1m tokens", "", "   ↑/↓ choose · ←/→ change size · enter confirm · esc not now"])
        self.assertTrue(of_relay.dialog_open(compact))
        self.assertTrue(of_relay.dialog_open("  1. Yes\n  2. No\n enter to select · esc to cancel\n"))
        res, typed = self._deliver_with_box(compact)
        self.assertEqual((res, typed), (("deferred", "a dialog is open in the pane"), []))

    def test_confirmation_by_the_hooks_record_and_a_late_one(self):
        import of_events as ev_
        self.assertFalse(of_relay.hook_saw(self.conn, "S", "m-77"))
        rec = ev_.record("agent_message_in", {"via": "relay", "text": "Message from zeta:x (via relay, authority: none; "
                                              "machine m; msg m-77)\nhi"}, session="S", source="hook")
        of_store.add_event(self.conn, rec)
        self.assertTrue(of_relay.hook_saw(self.conn, "S", "m-77"))
        self.assertTrue(of_relay.confirmed(self.conn, "S", "m-77", wait_s=0))
        self.assertFalse(of_relay.confirmed(self.conn, "S", "m-78", wait_s=0))
        self.assertTrue(of_relay.confirmed(self.conn, "S-nohook", "m-78", wait_s=0))  # no hook here: as before
        # offered again after an unconfirmed typing, but the session had recorded it late: never typed twice
        to = next(k for k, v in LOCAL.items() if v.get("kind") != "human")
        local = {to: dict(LOCAL[to], session_id="S")}
        typed = []
        ok = lambda pane, text, session, sender, working_ok=False, skip_if=None: (typed.append(text), (True, None))[1]
        got = of_relay.deliver(self.conn, {"msg_id": "m-77", "to": to, "from": "zeta:x", "text": "hi"}, local, ok)
        self.assertEqual((got, typed), (("delivered", "confirmed late"), []))

    def test_presence_only_own_org_per_server(self):
        bodies = of_relay.presence_bodies(LOCAL, {"zeta": "https://zeta"}, "http://local")
        self.assertEqual([h["position"] for h in bodies["https://zeta"]], ["acme_owner"])
        self.assertEqual([h["position"] for h in bodies["http://local"]], ["of_a"])
        self.assertEqual(bodies["https://zeta"][0]["kind"], "human")

    def test_a_positions_older_names_go_with_its_presence(self):
        """The registry's `aliases` travel with presence, so the server readdresses a message sent to an older name
        (a user organization's issue 07ba3788a3c3)."""
        positions = [{"_org": "zeta", "name": "zeta_new", "aliases": ["zeta_old"],
                      "holder": {"runtime": "human", "machine": of_events.machine_id()}}]
        local = of_relay.local_holders(positions, lambda ps, live: ([], None, None), [])
        self.assertEqual(local["zeta:zeta_new"]["aliases"], ["zeta_old"])
        body = of_relay.presence_bodies(local, {}, "http://local")["http://local"][0]
        self.assertEqual(body["aliases"], ["zeta_old"])
        changed = dict(local, **{"zeta:zeta_new": dict(local["zeta:zeta_new"], aliases=[])})
        self.assertNotEqual(of_relay.holders_key(local), of_relay.holders_key(changed))   # a change republishes

    def test_after_a_cutover_the_old_server_is_drained(self):
        """docs/server-several-machines.md §4.6: messages still queued on the old server for a moved organization
        are leased here too, while orgs.yaml says drain_from; removing it stops the drain at the next round."""
        with tempfile.TemporaryDirectory() as t:
            orgs = os.path.join(t, "orgs.yaml")
            with open(orgs, "w") as fh:
                fh.write("orgs:\n  - {name: ofb, path: x}\n  - {name: zeta, path: y, server: 'https://zeta/', "
                         "drain_from: local}\n")
            drains = of_relay.org_drains("http://local", orgs)
            self.assertEqual(drains, {"zeta": ["http://local"]})
            bodies = of_relay.presence_bodies(LOCAL, {"zeta": "https://zeta"}, "http://local", drains)
            self.assertEqual([h["position"] for h in bodies["https://zeta"]], ["acme_owner"])
            self.assertEqual(sorted(h["position"] for h in bodies["http://local"]), ["acme_owner", "of_a"])
            with open(orgs, "w") as fh:
                fh.write("orgs:\n  - {name: ofb, path: x}\n  - {name: zeta, path: y, server: 'https://zeta/'}\n")
            self.assertEqual(of_relay.org_drains("http://local", orgs), {})


if __name__ == "__main__":
    unittest.main()


class OldMessageTest(unittest.TestCase):
    def test_old_messages_may_go_into_a_working_pane_never_blocked(self):
        import of_relay_client
        self.assertIn("working", of_relay_client.wait_argv("p", 5, working_ok=True))
        self.assertNotIn("blocked", of_relay_client.wait_argv("p", 5, working_ok=True))
        self.assertGreater(of_relay_client.message_age_s({"created_at": "2026-11-06T07:00:00Z"},
                                                         now=of_relay_client.time.time()), 0)
        self.assertEqual(of_relay_client.message_age_s({}), 0)


class WaitArgvTest(unittest.TestCase):
    def test_idle_or_done_never_blocked(self):
        import of_relay_client
        argv = of_relay_client.wait_argv("w1:p1", 5)
        self.assertEqual(argv[:4], ["herdr", "agent", "wait", "w1:p1"])
        self.assertNotIn("--state", argv)
        self.assertNotIn("blocked", argv)
        self.assertEqual([argv[i + 1] for i, a in enumerate(argv) if a == "--until"], ["idle", "done"])
        self.assertEqual(argv[-1], "5000")


class InboxServersTest(unittest.TestCase):
    """`of inbox --mine` reads the old server a cutover drains (orgs.yaml drain_from), so an --fyi message queued there
    is read and taken, and the old queue can empty."""

    def test_the_organizations_server_then_its_drains(self):
        with tempfile.TemporaryDirectory() as d:
            orgs = os.path.join(d, "orgs.yaml")
            with open(orgs, "w") as fh:
                fh.write("orgs:\n  - {name: acme, path: a, server: 'https://of.acme.test', drain_from: local}\n"
                         "  - {name: beta, path: b, drain_from: ['https://old.beta.test/', 'http://127.0.0.1:18200']}\n"
                         "  - {name: gamma, path: c}\n")
            local = "http://127.0.0.1:18200"
            self.assertEqual(of_relay.inbox_servers("acme", "https://of.acme.test/", local, orgs),
                             ["https://of.acme.test", local])
            self.assertEqual(of_relay.inbox_servers("beta", local, local, orgs), [local, "https://old.beta.test"])
            self.assertEqual(of_relay.inbox_servers("gamma", local, local, orgs), [local])
