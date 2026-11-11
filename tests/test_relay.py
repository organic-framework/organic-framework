"""of relay (client side): delivery, dedup, inbox, presence per server."""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_events  # noqa: E402
import of_relay_client as of_relay  # noqa: E402
import of_store  # noqa: E402

LOCAL = {"ofb:of_a": {"org": "ofb", "position": "of_a", "kind": "agent", "pane": "w1:p1", "session_id": "S"},
         "mt:acme_owner": {"org": "mt", "position": "acme_owner", "kind": "human", "pane": None, "session_id": None}}


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
        m = {"msg_id": "m1", "to": "ofb:of_a", "from": "mt:acme_org", "text": "please review", "authority": "verified"}
        self.assertEqual(of_relay.deliver(self.conn, m, LOCAL, self.fake()), ("delivered", None))
        self.assertEqual(of_relay.deliver(self.conn, m, LOCAL, self.fake()), ("delivered", "duplicate"))
        self.assertEqual(len(self.typed), 1)
        pane, text = self.typed[0]
        self.assertEqual(pane, "w1:p1")
        self.assertTrue(text.startswith("Message from mt:acme_org (via relay, authority: verified; machine ?; msg m1)"))
        self.assertIn("of send mt:acme_org --reply-to m1", text)

    def test_human_goes_to_inbox_and_failures_are_reported(self):
        m = {"msg_id": "m2", "to": "mt:acme_owner", "from": "ofb:of_a", "text": "hi", "authority": "none"}
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
        m = {"msg_id": "m9", "to": "ofb:of_a", "from": "mt:x", "text": "hi"}
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
        h = {"msg_id": "m10", "to": "mt:acme_owner", "from": "ofb:of_a", "text": "for a person"}
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
        msgs = [{"msg_id": f"q{i}", "to": "ofb:of_a", "from": "mt:x", "text": f"t{i}", "created_at": "2026-11-06T12:00:00Z"}
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

    def test_a_take_under_a_live_lease_leaves_it_to_the_pane(self):
        class Leased(Exception):
            status = 409

        class Server:
            def queued_messages(self, to, session):
                return {"messages": [{"msg_id": "L1", "to": to, "from": "mt:x", "text": "hi", "state": "leased"}]}

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

    def test_take_all_takes_only_what_a_listing_showed_and_keeps_the_text(self):
        """From a user organization, 2026-11-08: `of inbox --take all >/dev/null` took a reply that arrived after the
        agent's listing, and its text was kept nowhere."""
        queue = [{"msg_id": "a1", "to": "ofb:of_a", "from": "mt:x", "text": "read me", "created_at": "2026-11-08T10:00Z"}]
        taken = []

        class Server:
            def queued_messages(self, to, session):
                return {"messages": [m for m in queue if m["msg_id"] not in taken]}

            def take_message(self, msg_id, to, session):
                taken.append(msg_id)
                return {"msg_id": msg_id, "state": "delivered"}
        of_relay.pull(Server(), "ofb:of_a", "S")                                   # the agent reads a1
        queue.append({"msg_id": "b2", "to": "ofb:of_a", "from": "mt:y", "text": "delete node-3",
                      "created_at": "2026-11-08T10:01Z"})                           # arrives meanwhile
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
        m = {"msg_id": "x1", "to": "ofb:of_a", "from": "mt:x", "text": "hi"}
        self.assertFalse(of_relay.seen("x1"))  # the running relay has read the file
        with open(of_relay._seen_path(), "a") as fh:  # another process (the CLI) appends its take
            fh.write(json.dumps({"msg_id": "x1", "outcome": "pulled"}) + "\n")
        self.assertEqual(of_relay.deliver(self.conn, m, LOCAL, self.fake()), ("delivered", "duplicate"))
        self.assertEqual(self.typed, [])

    def test_a_take_during_the_relays_wait_wins(self):
        m = {"msg_id": "r1", "to": "ofb:of_a", "from": "mt:x", "text": "hi"}

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
        item = lambda i, created, **k: dict({"msg_id": i, "to": "mt:k8s_keeper", "created_at": created,
                                             "priority": "normal", "state": "queued", "present": True,
                                             "reason": "batched: the receiver's 15-minute window",
                                             "next_at": "2026-11-09T11:14:16.000Z"}, **k)
        of_relay._LAST_QUEUES.clear()
        of_relay.record_queues({}, now=now, unsent={"mt:dpc": {"n": 2, "items": [
            item("g1", "2026-11-09T11:05:39.650Z"), item("g2", "2026-11-09T11:12:00.000Z")]}})
        self.assertEqual(of_relay.queue_note("mt:dpc", now=now),
                         "1 message you sent is not delivered yet: to mt:k8s_keeper (8m; batched, next in 1m). "
                         "An --urgent message to a receiver takes its queued ones with it, oldest first")  # g2: under 5m
        of_relay.record_queues({}, now=now, unsent={"mt:dpc": {"n": 4, "items": [
            item("o1", "2026-11-06T11:00:00.000Z", present=False, next_at=None),           # 3 days old: not repeated
            item("g1", "2026-11-09T11:05:39.650Z"), item("g3", "2026-11-09T11:06:00.000Z"),
            item("h1", "2026-11-09T11:07:00.000Z", to="mt:catalogue_keeper", state="leased")]}})
        self.assertEqual(of_relay.queue_note("mt:dpc", now=now),
                         "3 messages you sent are not delivered yet: 2 to mt:k8s_keeper (oldest 8m; batched, next in 1m);"
                         " to mt:catalogue_keeper (7m; being typed now). An --urgent message to a receiver takes its"
                         " queued ones with it, oldest first")
        of_relay.record_queues({"mt:dpc": {"n": 1, "oldest": "2026-11-09T11:04:00Z"}}, now=now, unsent={"mt:dpc": {
            "n": 1, "items": [item("g1", "2026-11-09T11:05:39.650Z", present=False, next_at=None)]}})
        note = of_relay.queue_note("mt:dpc", now=now)
        self.assertEqual(note.split("\n"), ["1 message waiting for you (oldest 10m): of inbox --mine",
                                            "1 message you sent is not delivered yet: to mt:k8s_keeper (8m; no live holder)"])
        of_relay.mark_seen("g1", "pane", to="mt:k8s_keeper")                               # typed in here since
        self.assertEqual(of_relay.queue_note("mt:dpc", now=now), "1 message waiting for you (oldest 10m): of inbox --mine")
        of_relay.set_queue("mt:dpc", 0, None, now=now)                                     # of inbox --mine keeps unsent
        self.assertEqual(of_relay.read_unsent()["mt:dpc"]["n"], 1)

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
        m = {"msg_id": "d1", "to": "ofb:of_a", "from": "mt:x", "text": "hi"}
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
                                           "S", "mt:x")
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

    def test_a_tools_text_is_framed_unless_it_is_the_owners(self):
        """of_architect, 2026-11-07: of_lab took a one-line tool text for the founder's words."""
        f = of_relay.frame
        self.assertEqual(f("hello", "of_platform"), "Message from of_platform (via of, authority: none)\nhello")
        self.assertTrue(f("Reminder: hand-over", "of-pulse:a").startswith("Message from a (via of pulse, authority: none)"))
        self.assertTrue(f("brief", "of_spawn", "of spawn").startswith("Message from of_spawn (via of spawn,"))
        for framed in ("Message from mt:x (via relay, authority: none)\nhi", "Question from k8s_keeper (unit p, …): x",
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

    def test_batching_fyi_and_urgent(self):
        """founder, 2026-11-07: one typed prompt per receiver per 15 minutes; fyi never typed; urgent at once."""
        local = dict(LOCAL)
        to = next(k for k, v in local.items() if v.get("kind") != "human")
        m = lambda i, **k: dict({"msg_id": f"b{i}", "to": to, "from": "mt:x", "text": f"t{i}"}, **k)
        now = 10_000.0
        plan = of_relay.plan_batch([m(1), m(2), m(3, priority="fyi")], local, now, last=lambda t: 0)
        self.assertEqual(sorted((a, len(ms)) for a, ms, _ in plan), [("deliver", 2), ("fyi", 1)])
        human = next((k for k, v in local.items() if v.get("kind") == "human"), None)
        if human:  # a person's fyi goes to their inbox like any message
            plan = of_relay.plan_batch([dict(m(9, priority="fyi"), to=human)], local, now, last=lambda t: 0)
            self.assertEqual([a for a, _, _ in plan], ["single"])
        plan = of_relay.plan_batch([m(4)], local, now, last=lambda t: now - 60)   # typed a minute ago
        self.assertEqual([(a, r) for a, _, r in plan], [("wait", 14 * 60 + 1)])
        plan = of_relay.plan_batch([m(5), m(6, priority="urgent")], local, now, last=lambda t: now - 60)
        self.assertEqual([(a, len(ms)) for a, ms, _ in plan], [("deliver", 2)])

    def test_a_message_the_receiver_waits_for_skips_the_window(self):
        """The founder, 2026-11-11, through of_conductor: an answer a receiver waits for is typed in as soon as it is
        idle; only unsolicited messages wait for the 15-minute window (docs/guard.md R9)."""
        local = dict(LOCAL)
        to = next(k for k, v in local.items() if v.get("kind") != "human")
        pos, now = to.split(":")[-1], 10_000.0
        recent = lambda t: now - 60   # noqa: E731 - typed a minute ago
        m = lambda i, **k: dict({"msg_id": f"e{i}", "to": to, "from": "ofb:of_peer", "text": f"t{i}"}, **k)  # noqa
        exp = of_relay.Expected(self.conn)
        self.assertEqual([a for a, _, _ in of_relay.plan_batch([m(1)], local, now, last=recent, expected=exp)],
                         ["wait"])                                       # unsolicited: the window, as before
        plan = of_relay.plan_batch([m(2, reply_to="x", reply_to_from=to), m(3)], local, now, last=recent,
                                   expected=exp)
        self.assertEqual([(a, len(ms)) for a, ms, _ in plan], [("deliver", 2)])   # a reply takes the queue along
        self.assertIsNone(exp(m(4, reply_to="x", reply_to_from="ofb:of_other")))   # a reply to someone else's
        of_events.emit("state_declared", {"position": pos, "state": "waiting", "waiting_on": "ofb:of_peer"},
                       position=pos)
        self.assertEqual(of_relay.Expected(self.conn)(m(5)), "waiting")
        of_events.emit("state_declared", {"position": pos, "state": "working", "waiting_on": None}, position=pos)
        self.assertIsNone(of_relay.Expected(self.conn)(m(6)))
        of_events.emit("handover_proposed", {"from": "of_peer", "to": pos, "title": "t", "done_when": "d"},
                       position="of_peer")
        self.assertEqual(of_relay.Expected(self.conn)(m(7)), "handover")
        self.assertIsNone(of_relay.Expected(self.conn)(dict(m(8), **{"from": "ofb:of_third"})))

    def test_joined_delivery_types_one_prompt_with_each_frame(self):
        local = dict(LOCAL)
        to = next(k for k, v in local.items() if v.get("kind") != "human")
        typed = []
        ok = lambda pane, text, session, sender, working_ok=False, skip_if=None: (typed.append(text), (True, None))[1]
        ms = [{"msg_id": f"j{i}", "to": to, "from": "mt:x", "text": f"body {i}"} for i in (1, 2)]
        self.assertEqual(of_relay.deliver_joined(self.conn, ms, local, ok), ("delivered", None))
        self.assertEqual(len(typed), 1)
        self.assertEqual(typed[0].count("Message from mt:x (via relay"), 2)
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
        rec = ev_.record("agent_message_in", {"via": "relay", "text": "Message from mt:x (via relay, authority: none; "
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
        got = of_relay.deliver(self.conn, {"msg_id": "m-77", "to": to, "from": "mt:x", "text": "hi"}, local, ok)
        self.assertEqual((got, typed), (("delivered", "confirmed late"), []))

    def test_presence_only_own_org_per_server(self):
        bodies = of_relay.presence_bodies(LOCAL, {"mt": "https://mt"}, "http://local")
        self.assertEqual([h["position"] for h in bodies["https://mt"]], ["acme_owner"])
        self.assertEqual([h["position"] for h in bodies["http://local"]], ["of_a"])
        self.assertEqual(bodies["https://mt"][0]["kind"], "human")

    def test_after_a_cutover_the_old_server_is_drained(self):
        """docs/server-several-machines.md §4.6: messages still queued on the old server for a moved organization
        are leased here too, while orgs.yaml says drain_from; removing it stops the drain at the next round."""
        with tempfile.TemporaryDirectory() as t:
            orgs = os.path.join(t, "orgs.yaml")
            with open(orgs, "w") as fh:
                fh.write("orgs:\n  - {name: ofb, path: x}\n  - {name: mt, path: y, server: 'https://mt/', "
                         "drain_from: local}\n")
            drains = of_relay.org_drains("http://local", orgs)
            self.assertEqual(drains, {"mt": ["http://local"]})
            bodies = of_relay.presence_bodies(LOCAL, {"mt": "https://mt"}, "http://local", drains)
            self.assertEqual([h["position"] for h in bodies["https://mt"]], ["acme_owner"])
            self.assertEqual(sorted(h["position"] for h in bodies["http://local"]), ["acme_owner", "of_a"])
            with open(orgs, "w") as fh:
                fh.write("orgs:\n  - {name: ofb, path: x}\n  - {name: mt, path: y, server: 'https://mt/'}\n")
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
