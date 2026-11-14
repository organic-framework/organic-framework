"""of-server relay and presence (bin/of_server_relay.py): addresses, presence, leases, acks,
authority stamps, masking of message text."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_client  # noqa: E402
import of_events  # noqa: E402
import of_grants  # noqa: E402
import of_route  # noqa: E402
import of_server  # noqa: E402
import of_server_relay  # noqa: E402

NOW = "2026-11-05T12:00:00Z"


class FakeCH:
    def __init__(self):
        self.inserts = []

    def insert(self, database, table, rows):
        self.inserts.append((database, table, rows))


class RelayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory()
        cls.grant_key = os.path.join(cls.keys.name, "grant")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", cls.grant_key], check=True,
                       env=of_grants.no_agent_env())
        cls.grant_pub = open(cls.grant_key + ".pub").read().strip()

    @classmethod
    def tearDownClass(cls):
        cls.keys.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = os.path.join(root, "state")
        self.ch = FakeCH()
        ship_cfg = {"unassigned_database": "db_un", "vault_database": "db_vault", "raw_to_org": False, "tenant": "t"}
        positions = os.path.join(root, "positions.yaml")
        with open(positions, "w") as fh:
            fh.write("positions:\n  - {name: ofb_lead, holder: {session: S-lead}}\n")
        cfg = dict(of_server.DEFAULTS, state_dir=os.path.join(root, "server"), port=0, flush_every=3600,
                   orgs={"ofb": "db_ofb", "zeta": "db_zeta"}, positions_files={"ofb": positions})
        self.grants = {"ofb": [], "zeta": []}
        self.app = of_server.App(cfg, ch=self.ch, ship_cfg=ship_cfg, grants_loader=lambda: self.grants,
                                 grant_key=lambda: of_grants.Keys(ed25519=[self.grant_pub]), mask_key=b"m" * 32)
        self.httpd = self.app.serve(host="127.0.0.1", port=0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.app.writer.stop.set()
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def machine(self, name, orgs, person=None):
        d = os.path.join(self.tmp.name, name)
        os.makedirs(d)
        c = of_client.Client(self.url, key_path=os.path.join(d, "key"), state_dir=d)
        c.machine_id = f"m-{name}"
        of_server.enroll(self.app.store, c.machine_id, of_client.ensure_key(c.key_path), orgs, [], person=person)
        return c

    def call(self, c, method, path, body=None):
        return c._authed(method, path, body=body)[1]

    def events(self, name):
        self.app.writer.drain()
        return [(d, json.loads(r["payload"])) for d, t, rows in self.ch.inserts if t == "event" for r in rows
                if r["name"] == name]

    def test_store_and_forward_lease_ack_and_masking(self):
        a, b = self.machine("a", ["ofb"]), self.machine("b", ["ofb"])
        key = "ghp_" + "Zq3vX9kLm2Pw7RtY4nB8cD1fGh6Jk5Ls0QaE"
        r = self.call(a, "POST", "/v1/messages", {"msg_id": "m1", "to": "ofb:ofb_dev", "from": "ofb:ofb_lead",
                                                  "text": f"use {key} for the deploy"})
        self.assertEqual((r["state"], r["authority"]), ("queued", "none"))
        self.assertEqual(self.call(a, "POST", "/v1/messages", {"msg_id": "m1", "to": "ofb:ofb_dev",
                                                              "from": "ofb:ofb_lead",
                                                              "text": f"use {key} for the deploy"})["state"], "queued")
        with self.assertRaises(of_client.ServerError) as cm:              # same id, other body
            self.call(a, "POST", "/v1/messages", {"msg_id": "m1", "to": "ofb:ofb_dev", "from": "ofb:ofb_lead",
                                                  "text": "other"})
        self.assertEqual(cm.exception.status, 409)
        self.assertEqual(self.call(b, "GET", "/v1/messages?wait=0")["messages"], [])   # nobody holds ofb_dev yet
        self.call(b, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_dev", "session_id": "S-dev",
                                                         "kind": "agent", "pane": "w1:p2"}]})
        got = self.call(b, "GET", "/v1/messages?wait=0")["messages"]
        self.assertEqual([(m["msg_id"], m["from"], m["to"], m["attempt"]) for m in got],
                         [("m1", "ofb:ofb_lead", "ofb:ofb_dev", 1)])
        self.assertNotIn(key, got[0]["text"])                               # masked before storage and delivery
        self.assertIn("[secret:github-pat:", got[0]["text"])
        self.assertEqual(self.call(b, "GET", "/v1/messages?wait=0")["messages"][0]["attempt"], 2)   # own lease renews
        self.assertEqual(self.call(b, "POST", "/v1/messages/m1/ack", {"status": "delivered"})["state"], "delivered")
        self.assertEqual(self.call(b, "GET", "/v1/messages?wait=0")["messages"], [])
        sent = self.events("of.message_sent")
        self.assertEqual([(d, p["msg_id"]) for d, p in sent], [("db_ofb", "m1")])
        self.assertNotIn(key, json.dumps(sent))
        self.assertEqual([p["msg_id"] for _, p in self.events("of.message_delivered")], ["m1"])
        found = [p for d, p in self.events("of.secret_detected")]
        self.assertTrue(found and found[0]["last_location"] == "message:m1")
        self.assertTrue(all(d == "db_vault" for d, _ in self.events("of.secret_detected")))

    def test_a_message_to_an_older_name_reaches_the_position(self):
        """A user organization's issue 07ba3788a3c3: a message queued to a name that later became an alias stayed
        queued, and its holder's take was refused not_holder (AGENTS.md: older names live only as aliases)."""
        a = self.machine("a", ["zeta"])
        self.call(a, "POST", "/v1/messages", {"msg_id": "old", "to": "zeta:zeta_old", "from": "zeta:zeta_new",
                                              "text": "a note to the old name"})
        self.call(a, "PUT", "/v1/presence", {"holders": [{"org": "zeta", "position": "zeta_new", "session_id": "S1",
                                                         "kind": "agent", "aliases": ["zeta_old"]}]})
        got = self.call(a, "GET", "/v1/messages?wait=0")["messages"]
        self.assertEqual([(x["msg_id"], x["to"]) for x in got], [("old", "zeta:zeta_new")])   # readdressed
        self.call(a, "POST", "/v1/messages", {"msg_id": "new", "to": "zeta:zeta_old", "from": "zeta:zeta_new",
                                              "text": "sent after the alias"})
        with self.app.store.conn() as c:
            self.assertEqual(c.execute("SELECT to_position FROM messages WHERE msg_id = 'new'").fetchone()[0],
                             "zeta_new")                                    # stored under the position

    def test_an_alias_never_takes_a_presented_name_another_org_or_one_the_registry_lacks(self):
        a, b = self.machine("a", ["zeta"]), self.machine("b", ["zeta", "ofb"])
        self.call(b, "PUT", "/v1/presence", {"holders": [{"org": "zeta", "position": "live_keeper", "session_id": "S2",
                                                         "kind": "agent"}]})
        self.call(a, "POST", "/v1/messages", {"msg_id": "m-live", "to": "zeta:live_keeper", "from": "zeta:x_keeper",
                                              "text": "for the live one"})
        self.call(a, "PUT", "/v1/presence", {"holders": [{"org": "zeta", "position": "thief", "session_id": "S3",
                                                         "kind": "agent", "aliases": ["live_keeper"]}]})
        with self.app.store.conn() as c:
            self.assertEqual(c.execute("SELECT to_position FROM messages WHERE msg_id = 'm-live'").fetchone()[0],
                             "live_keeper")                                     # a presented name is never an alias
        self.call(b, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_lead", "session_id": "S-lead",
                                                         "kind": "agent", "aliases": ["ofb_dev"]}]})
        with self.app.store.conn() as c:                                        # the registry lists no such alias
            self.assertIsNone(c.execute("SELECT 1 FROM position_aliases WHERE alias = 'ofb_dev'").fetchone())

    def test_long_poll_wakes_on_new_message(self):
        a, b = self.machine("a", ["ofb"]), self.machine("b", ["ofb"])
        self.call(b, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_dev"}]})
        out = {}
        t = threading.Thread(target=lambda: out.update(self.call(b, "GET", "/v1/messages?wait=10")))
        t0 = time.time()
        t.start()
        time.sleep(0.3)
        self.call(a, "POST", "/v1/messages", {"msg_id": "w1", "to": "ofb:ofb_dev", "from": "ofb:ofb_lead", "text": "hi"})
        t.join(5)
        self.assertEqual([m["msg_id"] for m in out["messages"]], ["w1"])
        self.assertLess(time.time() - t0, 5)

    def test_failures_requeue_then_dead(self):
        a, b = self.machine("a", ["ofb"]), self.machine("b", ["ofb"])
        self.call(b, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_dev"}]})
        self.call(a, "POST", "/v1/messages", {"msg_id": "f1", "to": "ofb:ofb_dev", "from": "ofb:ofb_lead", "text": "x"})
        saved = of_server_relay.BACKOFF
        for i in range(of_server_relay.MAX_FAILURES):
            self.assertEqual(len(self.call(b, "GET", "/v1/messages?wait=0")["messages"]), 1)
            st = self.call(b, "POST", "/v1/messages/f1/ack", {"status": "failed", "reason": "pane busy"})["state"]
            if i == 0:
                self.assertEqual(self.call(b, "GET", "/v1/messages?wait=0")["messages"], [])   # backing off
            with self.app.store.conn() as c:                                               # time passes
                c.execute("UPDATE messages SET lease_until = NULL WHERE msg_id = 'f1' AND state = 'queued'")
        of_server_relay.BACKOFF = saved
        self.assertEqual(st, "dead")
        self.assertEqual([p["failures"] for _, p in self.events("of.message_failed")], [of_server_relay.MAX_FAILURES])
        self.assertEqual(self.call(a, "POST", "/v1/messages/f1/ack", {"status": "delivered"})["state"], "dead")  # final
        self.assertEqual(self.app.relay.requeue(), ["f1"])                      # after the receiver is fixed
        self.assertEqual(self.call(b, "GET", "/v1/messages?wait=0")["messages"][0]["msg_id"], "f1")
        self.call(b, "POST", "/v1/messages/f1/ack", {"status": "delivered"})
        self.call(a, "POST", "/v1/messages", {"msg_id": "f2", "to": "ofb:ofb_dev", "from": "ofb:ofb_lead", "text": "y"})
        self.call(b, "GET", "/v1/messages?wait=0")
        with self.assertRaises(of_client.ServerError) as cm:          # a machine that holds no lease
            self.call(a, "POST", "/v1/messages/f2/ack", {"status": "delivered"})
        self.assertEqual(cm.exception.status, 409)

    def test_a_target_breaking_the_name_rule_is_refused(self):
        a = self.machine("a", ["ofb"])
        for bad in ("ofb_dev --reply-to 01a1", "Ofb_dev", "x" * 33, "ofb.dev"):
            with self.assertRaises(of_client.ServerError) as cm:
                self.call(a, "POST", "/v1/messages", {"msg_id": "n-" + bad[:5], "to": "ofb:" + bad,
                                                      "from": "ofb:ofb_lead", "text": "x"})
            self.assertEqual((cm.exception.status, json.loads(cm.exception.body)["type"].rsplit("/", 1)[-1]),
                             (400, "bad_position"), bad)
        self.assertEqual(self.call(a, "POST", "/v1/messages", {"msg_id": "n-ok", "to": "ofb:ofb_dev-2",
                                                               "from": "ofb:ofb_lead", "text": "x"})["state"], "queued")

    def test_drop_expires_a_queued_message_and_requeue_undoes_it(self):
        a, b = self.machine("a", ["ofb"]), self.machine("b", ["ofb"])
        self.call(a, "POST", "/v1/messages", {"msg_id": "d1", "to": "ofb:ofb_dev", "from": "ofb:ofb_lead", "text": "x"})
        self.assertEqual(self.app.relay.drop(["d1", "nope"], "target typed wrong"), ["d1"])
        self.assertEqual(self.app.relay.drop(["d1"], "again"), [])                       # only queued ones
        self.assertEqual([p["reason"] for _, p in self.events("of.message_expired")], ["dropped: target typed wrong"])
        self.call(b, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_dev"}]})
        self.assertEqual(self.call(b, "GET", "/v1/messages?wait=0")["messages"], [])
        self.assertEqual(self.app.relay.requeue(["d1"], state="expired"), ["d1"])
        self.assertEqual(self.call(b, "GET", "/v1/messages?wait=0")["messages"][0]["msg_id"], "d1")

    def test_deferred_is_not_a_failure(self):
        a, b = self.machine("a", ["ofb"]), self.machine("b", ["ofb"])
        self.call(b, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_dev"}]})
        self.call(a, "POST", "/v1/messages", {"msg_id": "d1", "to": "ofb:ofb_dev", "from": "ofb:ofb_lead", "text": "x"})
        for _ in range(of_server_relay.MAX_FAILURES + 2):
            self.assertEqual(len(self.call(b, "GET", "/v1/messages?wait=0")["messages"]), 1)
            r = self.call(b, "POST", "/v1/messages/d1/ack", {"status": "deferred", "reason": "busy", "retry_after": 10})
            self.assertEqual(r["state"], "queued")
            self.assertEqual(self.call(b, "GET", "/v1/messages?wait=0")["messages"], [])      # not before retry_after
            with self.app.store.conn() as c:
                c.execute("UPDATE messages SET lease_until = NULL WHERE msg_id = 'd1'")
        with self.app.store.conn() as c:
            self.assertEqual(c.execute("SELECT failures, state FROM messages WHERE msg_id = 'd1'").fetchone(), (0, "queued"))

    def test_a_receivers_queue_goes_as_a_whole(self):
        """2026-11-09, from a user organization: deferred messages each had their own retry time; the first one
        due was typed alone (11:14:15Z) and the 15-minute window held the GO and two older ones for 15 more minutes."""
        a, b = self.machine("a", ["ofb"]), self.machine("b", ["ofb"])
        self.call(b, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_dev", "kind": "agent"}]})
        for i, after in ((1, 600), (2, 599), (3, 30)):
            self.call(a, "POST", "/v1/messages", {"msg_id": f"q{i}", "to": "ofb:ofb_dev", "from": "ofb:ofb_lead",
                                                  "text": f"t{i}"})
        self.assertEqual([m["msg_id"] for m in self.call(b, "GET", "/v1/messages?wait=0")["messages"]],
                         ["q1", "q2", "q3"])
        for i, after in ((1, 600), (2, 599), (3, 30)):
            self.call(b, "POST", f"/v1/messages/q{i}/ack", {"status": "deferred", "reason": "batched", "retry_after": after})
        self.assertEqual(self.call(b, "GET", "/v1/messages?wait=0")["messages"], [])       # none due yet
        with self.app.store.conn() as c:
            c.execute("UPDATE messages SET lease_until = ? WHERE msg_id = 'q3'", (time.time() - 1,))   # the newest is due
        self.assertEqual([m["msg_id"] for m in self.call(b, "GET", "/v1/messages?wait=0")["messages"]],
                         ["q1", "q2", "q3"])                                              # all three, oldest first
        for i in (1, 2, 3):
            self.call(b, "POST", f"/v1/messages/q{i}/ack", {"status": "deferred", "reason": "batched", "retry_after": 600})
        self.call(a, "POST", "/v1/messages", {"msg_id": "q4", "to": "ofb:ofb_dev", "from": "ofb:ofb_lead",
                                              "text": "GO", "priority": "urgent"})
        got = self.call(b, "GET", "/v1/messages?wait=0")["messages"]                      # urgent carries them along
        self.assertEqual([(m["msg_id"], m["priority"]) for m in got],
                         [("q1", "normal"), ("q2", "normal"), ("q3", "normal"), ("q4", "urgent")])
        self.call(b, "POST", "/v1/messages/q1/ack", {"status": "failed", "reason": "pane gone"})
        for i in (2, 3, 4):
            self.call(b, "POST", f"/v1/messages/q{i}/ack", {"status": "deferred", "reason": "busy", "retry_after": 600})
        self.call(a, "POST", "/v1/messages", {"msg_id": "q5", "to": "ofb:ofb_dev", "from": "ofb:ofb_lead", "text": "x"})
        self.assertEqual([m["msg_id"] for m in self.call(b, "GET", "/v1/messages?wait=0")["messages"]],
                         ["q2", "q3", "q4", "q5"])                                        # a failed one keeps its back-off
        self.call(a, "POST", "/v1/messages", {"msg_id": "f1", "to": "ofb:ofb_dev", "from": "ofb:ofb_lead",
                                              "text": "x", "priority": "fyi"})
        with self.app.store.conn() as c:
            c.execute("UPDATE messages SET lease_until = NULL WHERE msg_id = 'q5'")
        self.assertNotIn("f1", [m["msg_id"] for m in self.call(b, "GET", "/v1/messages?wait=0")["messages"]])

    def test_presence_tells_a_sender_what_has_not_landed(self):
        """2026-11-09, from a user organization: a GO waited 24 minutes and its sender could not see it."""
        a, b = self.machine("a", ["ofb"]), self.machine("b", ["ofb"])
        lead = {"holders": [{"org": "ofb", "position": "ofb_lead", "session_id": "S-lead", "kind": "agent"}]}
        self.assertEqual(self.call(a, "PUT", "/v1/presence", lead)["unsent"], {})
        self.call(a, "POST", "/v1/messages", {"msg_id": "u1", "to": "ofb:ofb_dev", "from": "ofb:ofb_lead", "text": "GO"})
        self.call(a, "POST", "/v1/messages", {"msg_id": "u2", "to": "ofb:ofb_dev", "from": "ofb:ofb_lead", "text": "x",
                                              "priority": "fyi"})                          # read when it chooses: left out
        u = self.call(a, "PUT", "/v1/presence", lead)["unsent"]["ofb:ofb_lead"]
        self.assertEqual((u["n"], [(i["msg_id"], i["to"], i["present"], i["next_at"]) for i in u["items"]]),
                         (1, [("u1", "ofb:ofb_dev", False, None)]))
        self.call(b, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_dev", "kind": "agent"}]})
        self.call(b, "GET", "/v1/messages?wait=0")
        self.call(b, "POST", "/v1/messages/u1/ack", {"status": "deferred", "reason": "batched: the receiver's 15-minute window",
                                                     "retry_after": 300})
        i = self.call(a, "PUT", "/v1/presence", lead)["unsent"]["ofb:ofb_lead"]["items"][0]
        self.assertEqual((i["present"], i["reason"], i["state"], bool(i["next_at"])),
                         (True, "batched: the receiver's 15-minute window", "queued", True))
        with self.app.store.conn() as c:
            c.execute("UPDATE messages SET lease_until = NULL WHERE msg_id = 'u1'")
        self.call(b, "GET", "/v1/messages?wait=0")
        self.call(b, "POST", "/v1/messages/u1/ack", {"status": "delivered"})
        self.assertEqual(self.call(a, "PUT", "/v1/presence", lead)["unsent"], {})

    def test_an_agent_takes_its_own_messages_and_the_relay_injects_only_the_rest(self):
        """Acceptance test 5, server side: three queued for a busy pane, the agent takes two, the
        pane goes idle and the relay delivers only the third."""
        a, b = self.machine("a", ["ofb"]), self.machine("b", ["ofb"])
        dev = {"holders": [{"org": "ofb", "position": "ofb_dev", "session_id": "S-dev", "kind": "agent"}]}
        self.call(b, "PUT", "/v1/presence", dev)
        for i in (1, 2, 3):
            self.call(a, "POST", "/v1/messages", {"msg_id": f"t{i}", "to": "ofb:ofb_dev", "from": "ofb:ofb_lead",
                                                  "text": f"n{i}"})
        # The pane is busy: the relay leased them, then deferred them.
        self.assertEqual(len(self.call(b, "GET", "/v1/messages?wait=0")["messages"]), 3)
        for i in (1, 2, 3):
            self.call(b, "POST", f"/v1/messages/t{i}/ack", {"status": "deferred", "retry_after": 5})
        r = self.call(b, "PUT", "/v1/presence", dev)
        self.assertEqual(r["queued"]["ofb:ofb_dev"]["n"], 3)
        self.assertTrue(r["queued"]["ofb:ofb_dev"]["oldest"])
        q = self.call(b, "GET", "/v1/messages/queued?to=ofb:ofb_dev&session=S-dev")["messages"]
        self.assertEqual([(m["msg_id"], m["state"], m["from"]) for m in q],
                         [("t1", "queued", "ofb:ofb_lead"), ("t2", "queued", "ofb:ofb_lead"), ("t3", "queued", "ofb:ofb_lead")])
        for i in (1, 2):
            self.assertEqual(self.call(b, "POST", f"/v1/messages/t{i}/take", {"session": "S-dev"})["state"], "delivered")
        again = self.call(b, "POST", "/v1/messages/t1/take", {"to": "ofb:ofb_dev", "session": "S-dev"})
        self.assertEqual((again["state"], again["already"], again["text"]), ("delivered", True, "n1"))
        self.assertEqual(self.call(b, "PUT", "/v1/presence", dev)["queued"]["ofb:ofb_dev"]["n"], 1)
        with self.app.store.conn() as c:                                   # retry_after passes: the pane is idle
            c.execute("UPDATE messages SET lease_until = NULL WHERE state = 'queued'")
        self.assertEqual([m["msg_id"] for m in self.call(b, "GET", "/v1/messages?wait=0")["messages"]], ["t3"])
        self.call(b, "POST", "/v1/messages/t3/ack", {"status": "delivered"})
        self.assertEqual(self.call(b, "PUT", "/v1/presence", dev)["queued"], {})
        took = [p for _, p in self.events("of.message_delivered") if p.get("via") == "taken"]
        self.assertEqual(sorted((p["msg_id"], p["session_id"]) for p in took), [("t1", "S-dev"), ("t2", "S-dev")])

    def test_fyi_is_never_offered_to_an_agents_relay(self):
        """priority fyi: an agent reads it with `of inbox --mine` (queued, take); its relay never leases
        it. A human's relay still gets it, for `of inbox`."""
        a, b, h = self.machine("a", ["ofb"]), self.machine("b", ["ofb"]), self.machine("h", ["ofb"])
        dev = {"holders": [{"org": "ofb", "position": "ofb_dev", "session_id": "S-dev", "kind": "agent"}]}
        self.call(b, "PUT", "/v1/presence", dev)
        for i, pr in (("p1", "fyi"), ("p2", "urgent"), ("p3", None)):
            r = self.call(a, "POST", "/v1/messages", {"msg_id": i, "to": "ofb:ofb_dev", "from": "ofb:ofb_lead",
                                                      "text": i, **({"priority": pr} if pr else {})})
            self.assertEqual(r["priority"], pr or "normal")
        with self.assertRaises(of_client.ServerError) as cm:
            self.call(a, "POST", "/v1/messages", {"msg_id": "p4", "to": "ofb:ofb_dev", "from": "ofb:ofb_lead",
                                                  "text": "x", "priority": "low"})
        self.assertEqual(cm.exception.status, 400)
        polled = self.call(b, "GET", "/v1/messages?wait=0")["messages"]
        self.assertEqual([(m["msg_id"], m["priority"]) for m in polled], [("p2", "urgent"), ("p3", "normal")])
        self.assertEqual(self.call(b, "PUT", "/v1/presence", dev)["queued"]["ofb:ofb_dev"]["n"], 3)
        q = self.call(b, "GET", "/v1/messages/queued?to=ofb:ofb_dev&session=S-dev")["messages"]
        self.assertEqual([(m["msg_id"], m["priority"]) for m in q], [("p1", "fyi"), ("p2", "urgent"), ("p3", "normal")])
        took = self.call(b, "POST", "/v1/messages/p1/take", {"session": "S-dev"})
        self.assertEqual((took["state"], took["priority"]), ("delivered", "fyi"))
        self.assertEqual([p["priority"] for _, p in self.events("of.message_sent")], ["fyi", "urgent", "normal"])
        self.call(a, "POST", "/v1/messages", {"msg_id": "p5", "to": "ofb:ofb_owner", "from": "ofb:ofb_lead",
                                              "text": "x", "priority": "fyi"})
        self.call(h, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_owner", "kind": "human"}]})
        self.assertEqual([m["msg_id"] for m in self.call(h, "GET", "/v1/messages?wait=0")["messages"]], ["p5"])

    def test_take_refuses_under_a_live_lease_and_checks_the_holder(self):
        a, b = self.machine("a", ["ofb"]), self.machine("b", ["ofb"])
        self.call(b, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_dev", "session_id": "S-dev"},
                                                         {"org": "ofb", "position": "ofb_lead", "session_id": "S-x"}]})
        self.call(a, "POST", "/v1/messages", {"msg_id": "l1", "to": "ofb:ofb_dev", "from": "ofb:ofb_lead", "text": "x"})
        self.call(b, "GET", "/v1/messages?wait=0")                          # the relay is delivering it now
        q = self.call(b, "GET", "/v1/messages/queued?to=ofb:ofb_dev&session=S-dev")["messages"]
        self.assertEqual(q[0]["state"], "leased")
        self.assertGreater(q[0]["lease_s"], 0)
        with self.assertRaises(of_client.ServerError) as cm:
            self.call(b, "POST", "/v1/messages/l1/take", {"session": "S-dev"})
        self.assertEqual((cm.exception.status, json.loads(cm.exception.body)["type"]), (409, "leased"))
        self.assertGreater(json.loads(cm.exception.body)["retry_after"], 0)
        with self.app.store.conn() as c:                                    # the lease runs out
            c.execute("UPDATE messages SET lease_until = ? WHERE msg_id = 'l1'", (time.time() - 1,))
        self.assertEqual(self.call(b, "POST", "/v1/messages/l1/take", {"session": "S-dev"})["state"], "delivered")
        self.assertEqual(self.call(b, "GET", "/v1/messages?wait=0")["messages"], [])   # never offered again
        for who, path, body in [
                (b, "/v1/messages/queued?to=ofb:ofb_dev&session=S-other", None),   # another session
                (a, "/v1/messages/queued?to=ofb:ofb_dev&session=S-dev", None),      # a machine not presenting it
                (b, "/v1/messages/queued?to=ofb:ofb_lead&session=S-x", None)]:      # not the registered holder
            with self.assertRaises(of_client.ServerError) as cm:
                self.call(who, "GET", path, body)
            self.assertEqual((cm.exception.status, json.loads(cm.exception.body)["type"]), (403, "not_holder"))
        self.call(a, "POST", "/v1/messages", {"msg_id": "l2", "to": "ofb:ofb_dev", "from": "ofb:ofb_lead", "text": "y"})
        with self.assertRaises(of_client.ServerError) as cm:
            self.call(a, "POST", "/v1/messages/l2/take", {"session": "S-dev"})
        self.assertEqual(cm.exception.status, 403)
        with self.assertRaises(of_client.ServerError) as cm:
            self.call(b, "POST", "/v1/messages/l2/take", {})
        self.assertEqual(cm.exception.status, 400)
        with self.assertRaises(of_client.ServerError) as cm:
            self.call(b, "POST", "/v1/messages/nope/take", {"session": "S-dev"})
        self.assertEqual(cm.exception.status, 404)
        with self.assertRaises(of_client.ServerError) as cm:                 # addressed elsewhere
            self.call(b, "POST", "/v1/messages/l2/take", {"to": "ofb:ofb_lead", "session": "S-dev"})
        self.assertEqual(cm.exception.status, 400)
        self.call(b, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_dev", "session_id": "S-dev",
                                                         "kind": "human"}]})
        with self.assertRaises(of_client.ServerError) as cm:                 # a person keeps the local inbox
            self.call(b, "GET", "/v1/messages/queued?to=ofb:ofb_dev&session=S-dev")
        self.assertEqual(cm.exception.status, 403)

    def test_presence_is_scoped_to_joined_orgs(self):
        a = self.machine("a", ["ofb"], person="alice")
        self.call(a, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_dev", "kind": "human"}]})
        h = self.call(a, "GET", "/v1/presence?org=ofb")["holders"]
        self.assertEqual([(x["position"], x["kind"], x["person"]) for x in h], [("ofb_dev", "human", "alice")])
        with self.assertRaises(of_client.ServerError) as cm:
            self.call(a, "GET", "/v1/presence?org=zeta")
        self.assertEqual(cm.exception.status, 403)
        with self.assertRaises(of_client.ServerError) as cm:
            self.call(a, "PUT", "/v1/presence", {"holders": [{"org": "zeta", "position": "acme_x"}]})
        self.assertEqual(cm.exception.status, 403)

    def test_cross_org_needs_a_grant_and_the_sender_must_hold_the_position(self):
        a, b = self.machine("a", ["ofb", "zeta"]), self.machine("b", ["zeta"])
        msg = {"msg_id": "x1", "to": "zeta:acme_org", "from": "ofb:ofb_lead", "text": "hello"}
        with self.assertRaises(of_client.ServerError) as cm:              # no grant
            self.call(a, "POST", "/v1/messages", msg)
        self.assertEqual(cm.exception.status, 403)
        g = {"id": "g-x", "subject": "ofb_lead", "issuer": "tenant", "tier": "propose", "scope": {"to_org": ["zeta"]}}
        g["signature"] = of_grants.sign_ed25519(g, self.grant_key)
        with self.assertRaises(of_client.ServerError) as cm:              # grant fine, but a is not the holder
            self.call(a, "POST", "/v1/messages", dict(msg, grants=[g]))
        self.assertIn("sender not holder", str(cm.exception))
        self.call(a, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_lead", "session_id": "S-other"}]})
        with self.assertRaises(of_client.ServerError) as cm:              # presents it, but not the registered session
            self.call(a, "POST", "/v1/messages", dict(msg, grants=[g]))
        self.assertIn("registered holder", str(cm.exception))
        self.call(a, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_lead", "session_id": "S-lead"}]})
        r = self.call(a, "POST", "/v1/messages", dict(msg, grants=[g]))
        self.assertEqual(r["authority"], "verified")
        self.call(b, "PUT", "/v1/presence", {"holders": [{"org": "zeta", "position": "acme_org"}]})
        got = self.call(b, "GET", "/v1/messages?wait=0")["messages"][0]
        self.assertEqual((got["from"], got["authority"], got["sender_machine"]), ("ofb:ofb_lead", "verified", "m-a"))
        forged = dict(g, scope={"to_org": ["zeta", "other"]})              # tampered after signing
        with self.assertRaises(of_client.ServerError):
            self.call(a, "POST", "/v1/messages", dict(msg, msg_id="x2", grants=[forged]))
        sent = self.events("of.message_sent")
        by_db = {d: p for d, p in sent}
        self.assertIn("text", by_db["db_zeta"])
        self.assertNotIn("text", by_db["db_ofb"])                          # the sender's side gets a slim copy

    def test_a_reply_across_organizations_needs_no_grant(self):
        """docs/registry.md, Grants (of_architect 2eeabd5, 2026-11-09): a reply to a message from the other
        organization, delivered to the replier at most 7 days ago, goes back to its sender only, without a grant
        and without authority."""
        a, b = self.machine("a", ["ofb"]), self.machine("b", ["zeta"])
        g = {"id": "g-zeta", "subject": "acme_org", "issuer": "tenant", "tier": "propose", "scope": {"to_org": ["ofb"]}}
        g["signature"] = of_grants.sign_ed25519(g, self.grant_key)
        self.call(b, "PUT", "/v1/presence", {"holders": [{"org": "zeta", "position": "acme_org", "session_id": "S-zeta"}]})
        self.call(a, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_lead", "session_id": "S-lead"}]})
        self.assertEqual(self.call(b, "POST", "/v1/messages",
                                   {"msg_id": "q1", "to": "ofb:ofb_lead", "from": "zeta:acme_org",
                                    "text": "a guard request", "grants": [g]})["authority"],
                         "verified")
        reply = {"msg_id": "r1", "to": "zeta:acme_org", "from": "ofb:ofb_lead", "text": "done", "reply_to": "q1"}
        with self.assertRaises(of_client.ServerError) as cm:              # not delivered yet
            self.call(a, "POST", "/v1/messages", reply)
        self.assertEqual(cm.exception.status, 403)
        self.assertEqual(self.call(a, "GET", "/v1/messages?wait=0")["messages"][0]["msg_id"], "q1")
        self.call(a, "POST", "/v1/messages/q1/ack", {"status": "delivered"})
        r = self.call(a, "POST", "/v1/messages", dict(reply, grant_id="g-claimed"))
        self.assertEqual((r["authority"], r["authority_reason"]),
                         ("none", "a reply to q1 (no grant needed; carries no authority)"))
        got = self.call(b, "GET", "/v1/messages?wait=0")["messages"][0]
        self.assertEqual(got["reply_to_from"], got["to"])  # a reply to its own message: typed at once (R9)
        self.assertEqual((got["msg_id"], got["reply_to"], got["authority"], got["grant_id"]), ("r1", "q1", "none", None))
        for bad, why in ((dict(reply, msg_id="r2", to="zeta:acme_other"),
                          "is not a message from zeta:acme_other"),  # only back
                         (dict(reply, msg_id="r3", reply_to="q"), "names no message (give its full id)"),  # a short id
                         (dict(reply, msg_id="r4", reply_to="r1"), "is not a message from zeta:acme_org")):  # its own
            with self.assertRaises(of_client.ServerError) as cm:
                self.call(a, "POST", "/v1/messages", bad)
            self.assertEqual(cm.exception.status, 403, bad["msg_id"])
            self.assertIn("not a reply: reply_to", json.loads(cm.exception.body)["title"], bad["msg_id"])
            self.assertIn(why, json.loads(cm.exception.body)["title"], bad["msg_id"])
        with self.app.store.conn() as c:                                    # older than 7 days
            c.execute("UPDATE messages SET done_at = '2026-10-01T00:00:00Z' WHERE msg_id = 'q1'")
        with self.assertRaises(of_client.ServerError) as cm:
            self.call(a, "POST", "/v1/messages", dict(reply, msg_id="r5"))
        self.assertEqual(cm.exception.status, 403)
        self.assertIn("delivered more than 7 days ago", json.loads(cm.exception.body)["title"])
        with self.app.store.conn() as c:
            c.execute("UPDATE messages SET done_at = NULL WHERE msg_id = 'q1'")
        self.call(a, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_lead", "session_id": "S-x"}]})
        with self.assertRaises(of_client.ServerError) as cm:              # the replier must hold the position
            self.call(a, "POST", "/v1/messages", dict(reply, msg_id="r6"))
        self.assertEqual(cm.exception.status, 403)

    def test_a_grant_limited_to_positions_admits_no_other(self):
        a = self.machine("a", ["ofb", "zeta"])
        self.call(a, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_lead", "session_id": "S-lead"}]})
        g = {"id": "g-p", "subject": "ofb_lead", "issuer": "tenant", "tier": "propose",
             "scope": {"to_org": ["zeta"], "to_positions": ["zeta:acme_org", "ofb:ofb_dev"]}}
        g["signature"] = of_grants.sign_ed25519(g, self.grant_key)
        ok = self.call(a, "POST", "/v1/messages", {"msg_id": "p1", "to": "zeta:acme_org", "from": "ofb:ofb_lead",
                                                   "text": "hi", "grants": [g]})
        self.assertEqual(ok["authority"], "verified")
        with self.assertRaises(of_client.ServerError):                     # same organization, another position
            self.call(a, "POST", "/v1/messages", {"msg_id": "p2", "to": "zeta:acme_other", "from": "ofb:ofb_lead",
                                                  "text": "hi", "grants": [g]})
        inside = self.call(a, "POST", "/v1/messages", {"msg_id": "p3", "to": "ofb:ofb_dev", "from": "ofb:ofb_lead",
                                                       "text": "hi", "grants": [g]})
        self.assertEqual(inside["authority"], "verified")
        other = self.call(a, "POST", "/v1/messages", {"msg_id": "p4", "to": "ofb:ofb_qa", "from": "ofb:ofb_lead",
                                                      "text": "hi", "grants": [g]})
        self.assertEqual(other["authority"], "unverified_claim")            # within its org: delivered, not verified

    def test_addresses_are_validated(self):
        a = self.machine("a", ["ofb"])
        for bad in ("ofb", ":x", "ofb:"):
            with self.assertRaises(of_client.ServerError) as cm:
                self.call(a, "POST", "/v1/messages", {"msg_id": bad, "to": bad, "from": "ofb:ofb_lead", "text": "x"})
            self.assertEqual(cm.exception.status, 400)
        with self.assertRaises(of_client.ServerError) as cm:
            self.call(a, "POST", "/v1/messages", {"msg_id": "u", "to": "nowhere:x", "from": "ofb:ofb_lead", "text": "x"})
        self.assertEqual(cm.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
