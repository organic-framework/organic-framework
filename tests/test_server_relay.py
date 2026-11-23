"""of-server relay and presence (bin/of_server_relay.py): addresses, presence, leases, acks,
authority stamps, masking of message text."""

import json
import os
import shutil
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
from tests.test_server import release_store_conns  # noqa: E402
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
        release_store_conns(self.tmp.name)
        self.tmp.cleanup()

    def test_a_moving_organization_gets_503_and_the_rest_go_on(self):
        """The cut's hold (organic-framework-build's server move plan, §4 step 2): orgs.yaml's hold stops neither the
        shipper nor the relay, so the old server answers the moving organization with 503 and Retry-After while its
        records are copied; the client keeps the request and retries."""
        import urllib.error
        a, z = self.machine("a", ["ofb"]), self.machine("z", ["zeta"])
        saved = of_client.RETRIES
        of_client.RETRIES = 1
        try:
            self.assertEqual(of_server.set_moving(self.app.cfg, "ofb", True), ["ofb"])
            self.app._moving_cache = None
            for c, method, path, body in ((a, "POST", "/v1/batches", {"batch_id": "b", "records": []}),
                                          (a, "GET", "/v1/messages?wait=0", None),
                                          (z, "POST", "/v1/messages", {"to": "ofb:ofb_lead", "from": "zeta:zeta_x",
                                                                         "text": "t", "msg_id": "01a1-m"}),
                                          (z, "GET", "/v1/coord?org=ofb&after=0&wait=0", None)):
                with self.subTest(path=path), self.assertRaises(of_client.ServerError) as e:
                    c._authed(method, path, body=body)
                self.assertEqual(e.exception.status, 503)
                self.assertIn("moving", e.exception.body)
            for path, body in (("/v1/presence", []), ("/v1/messages/01a1-x/ack", {})):
                with self.subTest(write=path), self.assertRaises(of_client.ServerError) as e:
                    a._authed("PUT" if path == "/v1/presence" else "POST", path, body=body)
                self.assertEqual(e.exception.status, 503)                         # every write of its machines
            with self.assertRaises(of_client.ServerError) as e:   # an enrolment writes consent events (round 3)
                of_client.Client(self.url)._request("POST", "/v1/enroll", body={"code": "x", "machine_id": "m-new",
                                                                                 "pubkey": "ssh-ed25519 AAAA", "consent": {}})
            self.assertEqual(e.exception.status, 503)
            with self.app.store.conn() as c:
                c.execute("INSERT INTO messages (seq, msg_id, to_org, to_position, from_org, from_position, state) "
                          "VALUES (900, 'm-ofb', 'zeta', 'zeta_x', 'ofb', 'ofb_lead', 'queued')")
            with self.assertRaises(of_client.ServerError) as e:   # another organization's machine, an ofb message
                z._authed("POST", "/v1/messages/m-ofb/ack", body={})
            self.assertEqual(e.exception.status, 503)
            with self.app.store.conn() as c:                                     # z holds the receiver
                c.execute("INSERT OR REPLACE INTO presence (machine_id, org, position, session_id, state, kind, updated)"
                          " VALUES (?, 'zeta', 'zeta_x', 'S-x', 'idle', 'agent', ?)", (z.machine_id, time.time()))
            got = self.call(z, "GET", "/v1/messages?wait=0")                   # another organization goes on,
            self.assertNotIn("m-ofb", [x.get("msg_id") for x in got.get("messages", [])])  # but a moving one's row
            with self.app.store.conn() as c:                                     # is not leased (round 4)
                self.assertEqual(c.execute("SELECT state FROM messages WHERE msg_id = 'm-ofb'").fetchone()[0], "queued")
                c.execute("UPDATE messages SET created = 0 WHERE msg_id = 'm-ofb'")
            self.app.relay.expire()                                              # nor expired while it moves
            with self.app.store.conn() as c:
                self.assertEqual(c.execute("SELECT state FROM messages WHERE msg_id = 'm-ofb'").fetchone()[0], "queued")

            self.assertEqual(of_server.set_moving(self.app.cfg, "ofb", False), [])
            self.app._moving_cache = None
            got = self.call(z, "GET", "/v1/messages?wait=0")                   # unmarked: the same row is delivered
            self.assertIn("m-ofb", [x.get("msg_id") for x in got.get("messages", [])])
            self.assertIsNotNone(self.call(a, "GET", "/v1/messages?wait=0"))
            with self.assertRaises(ValueError):
                of_server.set_moving(self.app.cfg, "../x", True)
        finally:
            of_client.RETRIES = saved

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

    def _parked_registry(self, extra=""):
        """ofb with a structure keeper, a parked position still held whose successor is active, and its unit's owner."""
        with open(os.path.join(self.tmp.name, "positions.yaml"), "w") as fh:
            fh.write("positions:\n"
                     "  - {name: ofb_org, role: org, aliases: [ofb_keeper], holder: {session: S-org}}\n"
                     "  - {name: ofb_lead, holder: {session: S-lead}}\n"
                     "  - {name: ofb_watch, status: parked, successor: ofb_ops, system: watch,"
                     " holder: {session: S-watch}}\n"
                     "  - {name: ofb_ops, holder: {session: S-ops}}\n"
                     "  - {name: ofb_dev, holder: {session: S-dev}}\n"
                     "  - {name: ofb_old, status: finished}\n"
                     "  - {name: ofb_nap, status: parked}\n"
                     "  - {name: ofb_desk, holder: {person: alex}}\n"
                     "  - {name: ofb_typo, status: parkd}\n" + extra +
                     "units:\n  ops: {owner: ofb_lead, systems: [watch]}\n")
        a = self.machine("a", ["ofb"])
        self.call(a, "PUT", "/v1/presence", {"holders": [
            {"org": "ofb", "position": n, "session_id": f"S-{n[4:]}", "kind": "agent"}
            for n in ("ofb_org", "ofb_lead", "ofb_ops", "ofb_dev")]})
        for i, reply in (("w1", None), ("w2", "m0")):
            self.call(a, "POST", "/v1/messages", {"msg_id": i, "to": "ofb:ofb_watch", "from": "ofb:ofb_dev",
                                                  "text": f"a note {i}", "reply_to": reply})
        return a

    def refused(self, c, path, body, status, typ):
        with self.assertRaises(of_client.ServerError) as cm:
            self.call(c, "POST", path, body)
        self.assertEqual(cm.exception.status, status, str(cm.exception))
        self.assertIn(typ, str(cm.exception))

    def test_a_structure_keeper_moves_a_parked_positions_queue_to_its_successor(self):
        """A user organization's structure keeper, msg 01a1f3a8-b16b: mail piled up on parked positions that kept their
        sessions, and nothing moved it to the successor. Each message keeps its id, sender and reply-to, carries no
        authority, and its frame names whom it was sent to and who moved it."""
        a = self._parked_registry()
        move = {"from": "ofb:ofb_watch", "to": "ofb:ofb_ops", "by": "ofb:ofb_org", "session": "S-org"}
        dry = self.call(a, "POST", "/v1/messages/move", dict(move, dry_run=True))
        self.assertTrue(dry["dry_run"])
        self.assertEqual([(i["msg_id"], i["from"], i["chars"]) for i in dry["moved"]],
                         [("w1", "ofb:ofb_dev", 9), ("w2", "ofb:ofb_dev", 9)])
        self.assertNotIn("text", json.dumps(dry))
        self.assertNotIn("authority_was", json.dumps(dry))
        self.assertEqual(self.events("of.message_moved"), [])
        r = self.call(a, "POST", "/v1/messages/move", move)
        self.assertEqual(([i["msg_id"] for i in r["moved"]], r["leased"], r["not_queued"]), (["w1", "w2"], [], []))
        self.call(a, "PUT", "/v1/presence", {"holders": [
            {"org": "ofb", "position": "ofb_ops", "session_id": "S-ops", "kind": "agent"}]})
        got = self.call(a, "GET", "/v1/messages?wait=0")["messages"]
        self.assertEqual([(m["msg_id"], m["to"], m["from"], m["reply_to"], m["authority"], m["moved_from"],
                           m["moved_by"]) for m in got],
                         [("w1", "ofb:ofb_ops", "ofb:ofb_dev", None, "none", "ofb:ofb_watch", "ofb:ofb_org"),
                          ("w2", "ofb:ofb_ops", "ofb:ofb_dev", "m0", "none", "ofb:ofb_watch", "ofb:ofb_org")])
        import of_relay_client
        frame = of_relay_client.header(got[1])
        self.assertIn("msg w2, sent to ofb:ofb_watch and moved here by ofb:ofb_org, reply to m0)", frame)
        import of_classify   # of_review on PR 375: the hook still reads the reply-to of a moved message
        rid = "01a1f3d2-ccd7-7158-baa9-91ca564ec2f5"
        self.assertEqual(of_classify.relay_reply_to(of_relay_client.header(dict(got[1], reply_to=rid))), rid)
        queued = self.call(a, "GET", "/v1/messages/queued?to=ofb:ofb_ops&session=S-ops")["messages"]
        self.assertEqual({m["moved_from"] for m in queued}, {"ofb:ofb_watch"})
        self.assertEqual([(p["msg_id"], p["from_receiver"], p["to"], p["by"]) for _, p in
                          self.events("of.message_moved")],
                         [("w1", "ofb:ofb_watch", "ofb:ofb_ops", "ofb:ofb_org"),
                          ("w2", "ofb:ofb_watch", "ofb:ofb_ops", "ofb:ofb_org")])

    def test_only_the_keeper_or_the_unit_owner_moves_and_only_to_an_active_position(self):
        a = self._parked_registry()
        base = {"from": "ofb:ofb_watch", "to": "ofb:ofb_ops"}
        for body, status, typ in (
                (dict(base, by="ofb:ofb_dev", session="S-dev"), 403, "forbidden"),   # neither, by the registry
                (dict(base, by="ofb:ofb_org", session="S-other"), 403, "not_holder"),
                (dict(base, by="zeta:zeta_x", session="S-x"), 403, "forbidden"),
                (dict(base, to="zeta:zeta_x", by="ofb:ofb_org", session="S-org"), 400, "bad_request"),
                (dict(base, to="ofb:ofb_watch", by="ofb:ofb_org", session="S-org"), 400, "bad_destination"),
                (dict(base, to="ofb:ofb_old", by="ofb:ofb_org", session="S-org"), 400, "bad_destination"),
                (dict(base, to="ofb:ofb_nap", by="ofb:ofb_org", session="S-org"), 400, "bad_destination"),
                (dict(base, to="ofb:ofb_nobody", by="ofb:ofb_org", session="S-org"), 400, "bad_destination"),
                (dict(base, to="ofb:ofb_typo", by="ofb:ofb_org", session="S-org"), 400, "bad_destination")):
            with self.subTest(body=body):
                self.refused(a, "/v1/messages/move", body, status, typ)
        r = self.call(a, "POST", "/v1/messages/move", dict(base, by="ofb:ofb_lead", session="S-lead",   # unit owner
                                                           msg_ids=["w1", "nope"]))
        self.assertEqual(([i["msg_id"] for i in r["moved"]], r["not_queued"]), (["w1"], ["nope"]))
        self.call(a, "PUT", "/v1/presence", {"holders": [
            {"org": "ofb", "position": "ofb_watch", "session_id": "S-watch", "kind": "agent"},
            {"org": "ofb", "position": "ofb_org", "session_id": "S-org", "kind": "agent"}]})
        self.assertEqual([m["msg_id"] for m in self.call(a, "GET", "/v1/messages?wait=0")["messages"]
                          if m["to"] == "ofb:ofb_watch"], ["w2"])               # leased to its pane now
        r = self.call(a, "POST", "/v1/messages/move", dict(base, by="ofb:ofb_org", session="S-org"))
        self.assertEqual((r["moved"], [i["msg_id"] for i in r["leased"]]), ([], ["w2"]))
        self.refused(a, "/v1/messages/w2/drop", {"to": "ofb:ofb_watch", "by": "ofb:ofb_org", "session": "S-org",
                                                 "reason": "x"}, 409, "leased")

    def test_a_persons_queue_is_theirs_and_names_come_from_the_registry(self):
        """of_review on PR 375: a keeper could move a person's queued texts into an agent's pane, or drop them; `by` given
        by an alias only the registry knows was not made canonical."""
        a = self._parked_registry()
        self.call(a, "POST", "/v1/messages", {"msg_id": "p1", "to": "ofb:ofb_desk", "from": "ofb:ofb_dev", "text": "hi"})
        keeper = {"by": "ofb:ofb_org", "session": "S-org"}
        self.refused(a, "/v1/messages/move", dict(keeper, **{"from": "ofb:ofb_desk", "to": "ofb:ofb_ops"}), 403,
                     "person_held")
        self.refused(a, "/v1/messages/p1/drop", dict(keeper, to="ofb:ofb_desk", reason="x"), 403, "person_held")
        self.call(a, "POST", "/v1/messages", {"msg_id": "p2", "to": "ofb:ofb_ops", "from": "ofb:ofb_dev", "text": "hi"})
        self.call(a, "PUT", "/v1/presence", {"holders": [   # a person presents ofb_ops now: their machine's row
            {"org": "ofb", "position": "ofb_org", "session_id": "S-org", "kind": "agent"},
            {"org": "ofb", "position": "ofb_ops", "session_id": None, "kind": "human"}]})
        self.refused(a, "/v1/messages/p2/drop", dict(keeper, to="ofb:ofb_ops", reason="x"), 403, "person_held")
        self.call(a, "PUT", "/v1/presence", {"holders": [   # presented with a session the registry does not name
            {"org": "ofb", "position": "ofb_org", "session_id": "S-elsewhere", "kind": "agent"}]})
        self.refused(a, "/v1/messages/move", {"from": "ofb:ofb_watch", "to": "ofb:ofb_dev", "by": "ofb:ofb_org",
                                              "session": "S-elsewhere"}, 403, "not_holder")
        # the registry found through orgs.yaml (no positions_files): the move checks its holder session too
        repo = os.path.join(self.tmp.name, "ofbrepo", "registry")
        os.makedirs(repo)
        shutil.copy(os.path.join(self.tmp.name, "positions.yaml"), os.path.join(repo, "positions.yaml"))
        orgs = os.path.join(self.tmp.name, "orgs.yaml")
        with open(orgs, "w") as fh:
            fh.write("orgs:\n  - {name: ofb, path: ofbrepo}\n")
        saved_files, saved_env = dict(self.app.cfg["positions_files"]), os.environ.get("OF_ORGS")
        self.app.cfg["positions_files"].pop("ofb")
        os.environ["OF_ORGS"] = orgs
        try:
            self.refused(a, "/v1/messages/move", {"from": "ofb:ofb_watch", "to": "ofb:ofb_dev", "by": "ofb:ofb_org",
                                                  "session": "S-elsewhere"}, 403, "registry names another holder")
        finally:
            self.app.cfg["positions_files"] = saved_files
            if saved_env is None:
                os.environ.pop("OF_ORGS", None)
            else:
                os.environ["OF_ORGS"] = saved_env
        self.call(a, "PUT", "/v1/presence", {"holders": [
            {"org": "ofb", "position": "ofb_org", "session_id": "S-org", "kind": "agent"}]})
        r = self.call(a, "POST", "/v1/messages/move", {"from": "ofb:ofb_watch", "to": "ofb:ofb_dev",
                                                       "by": "ofb:ofb_keeper", "session": "S-org", "dry_run": True})
        self.assertEqual((r["by"], [i["msg_id"] for i in r["moved"]]), ("ofb:ofb_org", ["w1", "w2"]))

    def test_registry_names_and_statuses(self):
        import of_server_relay
        self.assertIsNone(of_server_relay.live_position([{"name": "x", "status": "parkd"}], "x"))
        self.assertEqual(of_server_relay.live_position([{"name": "x", "aliases": ["y"]}], "y"), "x")
        self.assertTrue(of_server_relay.held_by_person({"holder": {"runtime": "human"}}))
        self.assertFalse(of_server_relay.held_by_person({"holder": {"session": "S"}}))
        orgs = os.path.join(self.tmp.name, "orgs.yaml")
        with open(orgs, "w") as fh:
            fh.write("orgs:\n  - {name: q, path: one}\n  - {name: q, path: two}\n")
        self.assertTrue(of_server_relay.registry_file({}, "q", orgs).endswith(os.path.join("two", "registry",
                                                                                          "positions.yaml")))
        self.assertIsNone(of_server_relay.registry_file({"positions_files": {"q": 7}}, "q", orgs))

    def test_the_keeper_is_the_active_one_and_the_unit_owner_must_be_active(self):
        """of_secrets-review on PR 375: the server took a finished position of role org as keeper, the client did not."""
        import of_server_relay
        ps = [{"name": "k_old", "role": "org", "status": "finished"}, {"name": "k_arch", "role": "architect"},
              {"name": "k_lead", "status": "parked"}, {"name": "k_w", "system": "w"}]
        units = {"u": {"owner": "k_lead", "systems": ["w"]}}
        self.assertEqual(of_server_relay.queue_managers(ps, units, "k_w"), {"k_arch"})
        ps[2]["status"] = "active"
        self.assertEqual(of_server_relay.queue_managers(ps, units, "k_w"), {"k_arch", "k_lead"})
        ps.insert(0, {"name": "k_org", "role": "org"})
        self.assertEqual(of_server_relay.queue_managers(ps, units, "k_w"), {"k_org", "k_lead"})

    def test_a_keeper_drops_one_message_and_the_host_can_undo_it(self):
        a = self._parked_registry()
        to = {"to": "ofb:ofb_watch"}
        for body, status, typ in ((dict(to, by="ofb:ofb_org", session="S-org"), 400, "bad_request"),   # no reason
                                  (dict(to, by="ofb:ofb_dev", session="S-dev", reason="x"), 403, "forbidden"),
                                  ({"by": "ofb:ofb_org", "session": "S-org", "reason": "x"}, 400, "address"),
                                  (dict(to, to="ofb:ofb_ops", by="ofb:ofb_org", session="S-org", reason="x"), 404,
                                   "not_found")):
            with self.subTest(body=body):
                self.refused(a, "/v1/messages/w1/drop", body, status, typ)
        self.refused(a, "/v1/messages/nope/drop", dict(to, by="ofb:ofb_org", session="S-org", reason="x"), 404,
                     "not_found")
        self.refused(a, "/v1/messages/w1/drop", dict(to, by="ofb:ofb_org", session="S-org", reason="x", dry_run=True),
                     400, "dry_run is for move")
        key = "ghp_" + "Zq3vX9kLm2Pw7RtY4nB8cD1fGh6Jk5Ls0QaE"
        drop = dict(to, by="ofb:ofb_org", session="S-org", reason="x" * 290 + f" stale: it named {key}")
        self.assertEqual(self.call(a, "POST", "/v1/messages/w1/drop", drop)["state"], "expired")
        self.assertTrue(self.call(a, "POST", "/v1/messages/w1/drop", drop)["already"])
        expired = self.events("of.message_expired")
        self.assertEqual([(p["msg_id"], p["by"]) for _, p in expired], [("w1", "ofb:ofb_org")])   # once
        self.assertNotIn(key, json.dumps(expired))
        self.assertTrue(expired[0][1]["reason"].startswith("dropped by ofb:ofb_org: " + "x" * 290))   # cut after masking
        with self.app.store.conn() as c:
            self.assertNotIn(key, c.execute("SELECT reason FROM messages WHERE msg_id = 'w1'").fetchone()[0])
        self.assertTrue(any(p.get("last_location") == "message:w1:drop_reason"
                            for _, p in self.events("of.secret_detected")))
        self.assertEqual(self.app.relay.requeue(["w1"], state="expired"), ["w1"])
        r = self.call(a, "POST", "/v1/messages/move", {"from": "ofb:ofb_watch", "to": "ofb:ofb_ops",
                                                       "by": "ofb:ofb_org", "session": "S-org"})
        self.assertEqual([i["msg_id"] for i in r["moved"]], ["w1", "w2"])
        self.refused(a, "/v1/messages/w2/drop", dict(drop, reason="x"), 404, "not_found")   # it is ops' now

    def test_without_a_readable_registry_nobody_moves_or_drops(self):
        """of_secrets-review on PR 375: with no positions_files (the default) or a missing file, any holder in the
        organization could move another position's queue to itself. zeta has neither here, and orgs.yaml in the
        tests' HOME lists no organization."""
        b = self.machine("b", ["zeta"])
        self.call(b, "PUT", "/v1/presence", {"holders": [{"org": "zeta", "position": "zeta_x", "session_id": "S-x",
                                                         "kind": "agent"}]})
        self.call(b, "POST", "/v1/messages", {"msg_id": "z1", "to": "zeta:zeta_a", "from": "zeta:zeta_x", "text": "hi"})
        self.refused(b, "/v1/messages/move", {"from": "zeta:zeta_a", "to": "zeta:zeta_b", "by": "zeta:zeta_x",
                                              "session": "S-x"}, 403, "no_registry")
        self.refused(b, "/v1/messages/z1/drop", {"to": "zeta:zeta_a", "by": "zeta:zeta_x", "session": "S-x",
                                                 "reason": "x"}, 403, "no_registry")
        self.app.cfg["positions_files"]["zeta"] = os.path.join(self.tmp.name, "missing.yaml")
        self.refused(b, "/v1/messages/move", {"from": "zeta:zeta_a", "to": "zeta:zeta_b", "by": "zeta:zeta_x",
                                              "session": "S-x"}, 403, "no_registry")
        broken = os.path.join(self.tmp.name, "broken.yaml")
        with open(broken, "w") as fh:
            fh.write("positions:\n  - {name: zeta_x, holder: {session: S-x}\n")   # mid-edit: no closing brace
        self.app.cfg["positions_files"]["zeta"] = broken
        self.refused(b, "/v1/messages/move", {"from": "zeta:zeta_a", "to": "zeta:zeta_b", "by": "zeta:zeta_x",
                                              "session": "S-x"}, 403, "not valid YAML")

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

    def test_a_holder_briefly_away_keeps_its_name(self):
        """of_secrets-review on PR 164: an alias taken while the name's holder was briefly absent redirected its
        messages for 30 days; without the registry any joined machine could take any absent name."""
        a, b = self.machine("a", ["zeta"]), self.machine("b", ["zeta"])
        self.call(b, "PUT", "/v1/presence", {"holders": [{"org": "zeta", "position": "away_keeper", "session_id": "S2",
                                                         "kind": "agent"}]})
        self.call(b, "PUT", "/v1/presence", {"holders": []})                    # away: a restart, a move
        self.call(a, "PUT", "/v1/presence", {"holders": [{"org": "zeta", "position": "zeta_new", "session_id": "S1",
                                                         "kind": "agent", "aliases": ["away_keeper"]}]})
        self.call(a, "POST", "/v1/messages", {"msg_id": "m-away", "to": "zeta:away_keeper", "from": "zeta:x_keeper",
                                              "text": "for the one away"})
        with self.app.store.conn() as c:
            self.assertEqual(c.execute("SELECT to_position FROM messages WHERE msg_id = 'm-away'").fetchone()[0],
                             "away_keeper")                                     # presented within a day: kept
            self.assertIsNone(c.execute("SELECT 1 FROM position_aliases WHERE alias = 'away_keeper'").fetchone())
            c.execute("INSERT INTO position_aliases VALUES ('zeta', 'away_keeper', 'zeta_new', 'm-a', ?)",
                      (time.time(),))                                           # one taken before (an old server)
        self.call(b, "PUT", "/v1/presence", {"holders": [{"org": "zeta", "position": "away_keeper", "session_id": "S2",
                                                         "kind": "agent"}]})   # the holder is back
        with self.app.store.conn() as c:
            self.assertIsNone(c.execute("SELECT 1 FROM position_aliases WHERE alias = 'away_keeper'").fetchone())

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

    def test_a_backlog_past_the_limit_hides_no_urgent_message_and_counts_whole(self):
        """A user organization through its structure keeper (msg 01a203b5-c16a): the listing held the hundred oldest
        and read as the whole queue, so a guard STOP and a runner hold behind them went unseen."""
        a, b = self.machine("a", ["ofb"]), self.machine("b", ["ofb"])
        dev = {"holders": [{"org": "ofb", "position": "ofb_dev", "session_id": "S-dev", "kind": "agent"}]}
        self.call(b, "PUT", "/v1/presence", dev)
        n = of_server_relay.Relay.QUEUED_LIMIT + 5
        for i in range(n):
            self.call(a, "POST", "/v1/messages", {"msg_id": f"m{i:03d}", "to": "ofb:ofb_dev", "from": "ofb:ofb_lead",
                                                  "text": "x", **({"priority": "urgent"} if i == n - 1 else {})})
        got = self.call(b, "GET", "/v1/messages/queued?to=ofb:ofb_dev&session=S-dev")
        self.assertEqual((got["total"], got["urgent"], len(got["messages"])), (n, 1, of_server_relay.Relay.QUEUED_LIMIT))
        self.assertEqual(got["messages"][0]["msg_id"], f"m{n - 1:03d}")                 # the urgent one, newest, first
        self.assertEqual(got["messages"][1]["msg_id"], "m000")                           # then the oldest
        newest = self.call(b, "GET", "/v1/messages/queued?to=ofb:ofb_dev&session=S-dev&newest=1")["messages"]
        self.assertEqual([m["msg_id"] for m in newest[:3]], [f"m{n - 1:03d}", f"m{n - 2:03d}", f"m{n - 3:03d}"])

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
        self.assertEqual([(m["msg_id"], m["priority"]) for m in q], [("p2", "urgent"), ("p1", "fyi"), ("p3", "normal")])
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

    def test_a_merge_put_adds_and_never_drops_or_blanks_a_holder(self):
        """A user organization's structure keeper, msg 01a1e766-e334, and of_review on organic-framework PR 251: `of send`
        from a job that reads no agents PUT the sender alone, which replaced the machine's whole set. A merge adds or
        updates only its rows; a row with no session never replaces one the machine presents now."""
        a = self.machine("a", ["ofb"])
        held = lambda: sorted((x["position"], x["session_id"]) for x in self.call(a, "GET", "/v1/presence?org=ofb")["holders"])
        self.call(a, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_lead", "session_id": "S-lead"},
                                                         {"org": "ofb", "position": "ofb_dev", "session_id": "S-dev"}]})
        self.call(a, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_lead"}], "merge": True})
        self.assertEqual(held(), [("ofb_dev", "S-dev"), ("ofb_lead", "S-lead")])     # nobody dropped, no session blanked
        self.call(a, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_ops"}], "merge": True})
        self.assertEqual(held(), [("ofb_dev", "S-dev"), ("ofb_lead", "S-lead"), ("ofb_ops", "")])   # a job's position
        self.call(a, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_dev", "session_id": "S-dev2"}],
                                             "merge": True})
        self.assertIn(("ofb_dev", "S-dev2"), held())                                   # a row with a session updates
        self.call(a, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_lead", "session_id": "S-lead"}]})
        self.assertEqual(held(), [("ofb_lead", "S-lead")])                             # the relay's PUT still replaces
        with self.assertRaises(of_client.ServerError) as cm:                           # the same joined checks
            self.call(a, "PUT", "/v1/presence", {"holders": [{"org": "zeta", "position": "zeta_x"}], "merge": True})
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
        organization to the replier, at most 7 days old and not expired or failed, goes back to its sender only,
        without a grant and without authority. A queued or leased message counts: its holder reads it with
        `of inbox --mine` before the relay marks it delivered (2026-11-18, a user organization's structure keeper,
        msg 01a1ee56-f4f7: a busy receiver read, took and answered a message the relay held leased, three times
        refused)."""
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
        early = self.call(a, "POST", "/v1/messages", dict(reply, msg_id="r0"))  # queued: read with of inbox --mine
        self.assertEqual(early["authority"], "none")
        self.assertEqual(self.call(a, "GET", "/v1/messages?wait=0")["messages"][0]["msg_id"], "q1")
        leased = self.call(a, "POST", "/v1/messages", dict(reply, msg_id="r0l"))  # leased: the relay waits for idle
        self.assertEqual(leased["authority"], "none")
        self.call(a, "POST", "/v1/messages/q1/ack", {"status": "delivered"})
        r = self.call(a, "POST", "/v1/messages", dict(reply, grant_id="g-claimed"))
        self.assertEqual((r["authority"], r["authority_reason"]),
                         ("none", "a reply to q1 (no grant needed; carries no authority)"))
        got = next(x for x in self.call(b, "GET", "/v1/messages?wait=0")["messages"] if x["msg_id"] == "r1")
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
        for state, why in (("expired", "it expired unread"), ("dead", "its delivery failed")):  # never reached it
            with self.app.store.conn() as c:
                c.execute("UPDATE messages SET state = ? WHERE msg_id = 'q1'", (state,))
            with self.assertRaises(of_client.ServerError) as cm:
                self.call(a, "POST", "/v1/messages", dict(reply, msg_id=f"r-{state}"))
            self.assertEqual(cm.exception.status, 403, state)
            self.assertIn(f"reply_to q1 never reached ofb:ofb_lead: {why}", json.loads(cm.exception.body)["title"])
        with self.app.store.conn() as c:
            c.execute("UPDATE messages SET state = 'delivered' WHERE msg_id = 'q1'")
        self.call(a, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_lead", "session_id": "S-x"}]})
        with self.assertRaises(of_client.ServerError) as cm:              # the replier must hold the position
            self.call(a, "POST", "/v1/messages", dict(reply, msg_id="r6"))
        self.assertEqual(cm.exception.status, 403)

    def test_a_reply_to_a_message_sent_to_another_server_finds_its_header(self):
        """of_architect, msg 01a206a9-0644 (2026-11-23): once two organizations' servers were two, a reply from a
        position with no grant went to its receiver's server, which never saw the message it answered: 403 "names no
        message". The sender's machine keeps the header (no text) on its own organization's server; a reply finds it
        there, at most 7 days after it came, back to that sender only, without authority."""
        a, b = self.machine("a", ["ofb"]), self.machine("b", ["zeta"])
        self.call(b, "PUT", "/v1/presence", {"holders": [{"org": "zeta", "position": "acme_org", "session_id": "S-zeta"}]})
        self.call(a, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_lead", "session_id": "S-lead"}]})
        g = {"id": "g-zeta", "subject": "acme_org", "issuer": "tenant", "tier": "propose", "scope": {"to_org": ["ofb"]}}
        g["signature"] = of_grants.sign_ed25519(g, self.grant_key)
        head = {"msg_id": "x1", "to": "ofb:ofb_lead", "from": "zeta:acme_org", "grants": [g]}  # as the message carried
        reply = {"msg_id": "rx1", "to": "zeta:acme_org", "from": "ofb:ofb_lead", "text": "done", "reply_to": "x1"}
        with self.assertRaises(of_client.ServerError) as cm:                # no header yet: as before
            self.call(a, "POST", "/v1/messages", reply)
        self.assertIn("names no message", json.loads(cm.exception.body)["title"])
        self.assertEqual(self.call(b, "POST", "/v1/messages/sent", head), {"msg_id": "x1", "kept": True})
        self.assertEqual(self.call(b, "POST", "/v1/messages/sent", head)["kept"], True)    # the same again: kept
        with self.app.store.conn() as c:
            row = c.execute("SELECT * FROM sent_across WHERE msg_id = 'x1'").fetchone()
        self.assertEqual(row[:6], ("x1", "zeta", "acme_org", "ofb", "ofb_lead", "m-b"))  # no text column at all
        r = self.call(a, "POST", "/v1/messages", reply)
        self.assertEqual((r["authority"], r["authority_reason"], r["to"]),
                         ("none", "a reply to x1, sent from here to another server (no grant needed; carries no authority)",
                          "zeta:acme_org"))
        got = next(x for x in self.call(b, "GET", "/v1/messages?wait=0")["messages"] if x["msg_id"] == "rx1")
        self.assertEqual(got["reply_to_from"], "zeta:acme_org")    # a reply to its own message: typed at once (R9)
        for bad, why in ((dict(reply, msg_id="rx2", to="zeta:acme_other"), "is not a message from zeta:acme_other"),
                         (dict(reply, msg_id="rx3", **{"from": "ofb:ofb_dev"}), "is not a message from zeta:acme_org")):
            with self.assertRaises(of_client.ServerError) as cm:
                self.call(a, "POST", "/v1/messages", bad)
            self.assertEqual(cm.exception.status, 403, bad["msg_id"])
            self.assertIn(why, json.loads(cm.exception.body)["title"], bad["msg_id"])
        self.call(a, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_lead", "session_id": "S-x"}]})
        with self.assertRaises(of_client.ServerError) as cm:              # the replier must hold the position
            self.call(a, "POST", "/v1/messages", dict(reply, msg_id="rx4"))
        self.assertIn("registered holder", json.loads(cm.exception.body)["title"])
        self.call(a, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_lead", "session_id": "S-lead"}]})
        with self.app.store.conn() as c:                                    # the 7 days count from the header's arrival
            c.execute("UPDATE sent_across SET created = created - 8 * 86400 WHERE msg_id = 'x1'")
        with self.assertRaises(of_client.ServerError) as cm:
            self.call(a, "POST", "/v1/messages", dict(reply, msg_id="rx5"))
        self.assertIn("was sent more than 7 days ago", json.loads(cm.exception.body)["title"])

    def test_a_header_is_kept_only_for_its_holder_and_never_twice_for_other_ends(self):
        a, b = self.machine("a", ["ofb"]), self.machine("b", ["zeta"])
        g = {"id": "g-zeta", "subject": "acme_org", "issuer": "tenant", "tier": "propose", "scope": {"to_org": ["ofb"]}}
        g["signature"] = of_grants.sign_ed25519(g, self.grant_key)
        head = {"msg_id": "x2", "to": "ofb:ofb_lead", "from": "zeta:acme_org", "grants": [g]}
        for c, body, status, why in (
                (b, head, 403, "does not present the position"),                    # no presence yet
                (a, head, 403, "has not joined zeta"),                              # another organization's sender
                (b, dict(head, to="zeta:other"), 400, "another organization"),      # within one organization
                (b, dict(head, msg_id=""), 400, "msg_id is required"),
                (b, dict(head, to="ofb:" + "x" * 40), 400, "breaks the name rules"),  # no store for free text
                (b, dict(head, **{"from": "nowhere:acme_org"}), 404, "not served here"),
                (b, {k: v for k, v in head.items() if k != "grants"}, 403, "needs its message's standing")):
            if c is b and status != 403 or why == "needs its message's standing":
                self.call(b, "PUT", "/v1/presence",
                          {"holders": [{"org": "zeta", "position": "acme_org", "session_id": "S-zeta"}]})
            with self.assertRaises(of_client.ServerError) as cm:
                self.call(c, "POST", "/v1/messages/sent", body)
            self.assertEqual(cm.exception.status, status, why)
            self.assertIn(why, json.loads(cm.exception.body)["title"])
        self.call(b, "POST", "/v1/messages/sent", head)
        with self.assertRaises(of_client.ServerError) as cm:
            self.call(b, "POST", "/v1/messages/sent", dict(head, to="ofb:ofb_dev"))
        self.assertEqual(cm.exception.status, 409)
        self.call(b, "POST", "/v1/messages", {"msg_id": "here1", "to": "zeta:acme_org", "from": "zeta:acme_org",
                                              "text": "a note"})
        with self.assertRaises(of_client.ServerError) as cm:               # a message on this server has no header
            self.call(b, "POST", "/v1/messages/sent", dict(head, msg_id="here1"))
        self.assertEqual(cm.exception.status, 409)

    def test_a_header_without_a_grant_stands_on_the_message_it_answers(self):
        """A message sent as a reply needs no grant, so neither does its header, when the message it answers, from
        its receiver to its sender, is here; any other reply_to is no standing (of_secrets-review on PR 593)."""
        a, b = self.machine("a", ["ofb"]), self.machine("b", ["zeta"])
        self.call(b, "PUT", "/v1/presence", {"holders": [{"org": "zeta", "position": "acme_org", "session_id": "S-zeta"}]})
        self.call(a, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_lead", "session_id": "S-lead"}]})
        g = {"id": "g-ofb", "subject": "ofb_lead", "issuer": "tenant", "tier": "propose", "scope": {"to_org": ["zeta"]}}
        g["signature"] = of_grants.sign_ed25519(g, self.grant_key)
        self.call(a, "POST", "/v1/messages", {"msg_id": "q7", "to": "zeta:acme_org", "from": "ofb:ofb_lead",
                                              "text": "a question", "grants": [g]})
        head = {"msg_id": "x7", "to": "ofb:ofb_lead", "from": "zeta:acme_org"}
        for rid in ("nope", "here-none"):
            with self.assertRaises(of_client.ServerError) as cm:
                self.call(b, "POST", "/v1/messages/sent", dict(head, reply_to=rid))
            self.assertEqual(cm.exception.status, 403)
        with self.assertRaises(of_client.ServerError) as cm:                # q7 is not from ofb:ofb_dev
            self.call(b, "POST", "/v1/messages/sent", dict(head, to="ofb:ofb_dev", reply_to="q7"))
        self.assertEqual(cm.exception.status, 403)
        self.assertEqual(self.call(b, "POST", "/v1/messages/sent", dict(head, reply_to="q7"))["kept"], True)
        self.assertEqual(self.call(b, "POST", "/v1/messages/sent", dict(head, reply_to="q7"))["kept"], True)  # again
        # what a reply could not answer gives no standing either: expired, failed, or past the 7 days (PR 596)
        for i, (col, val) in enumerate((("state", "expired"), ("state", "dead"), ("done_at", "2026-10-01T00:00:00Z"))):
            with self.app.store.conn() as c:
                c.execute("UPDATE messages SET state = 'delivered', done_at = NULL WHERE msg_id = 'q7'")
                c.execute(f"UPDATE messages SET {col} = ? WHERE msg_id = 'q7'", (val,))
            with self.assertRaises(of_client.ServerError) as cm:
                self.call(b, "POST", "/v1/messages/sent", dict(head, msg_id=f"x7-{i}", reply_to="q7"))
            self.assertEqual(cm.exception.status, 403, val)

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

    def test_a_grant_for_something_else_verifies_no_message(self):
        """2026-11-19: a signed grant whose scope leaves to_org and to_positions open (finance data) matched every
        message, so it verified one to any organization."""
        a = self.machine("a", ["ofb", "zeta"])
        self.call(a, "PUT", "/v1/presence", {"holders": [{"org": "ofb", "position": "ofb_lead", "session_id": "S-lead"}]})
        g = {"id": "g-d", "subject": "ofb_lead", "issuer": "tenant", "tier": "act_and_report",
             "scope": {"data": ["finance"]}}
        g["signature"] = of_grants.sign_ed25519(g, self.grant_key)
        with self.assertRaises(of_client.ServerError) as cm:
            self.call(a, "POST", "/v1/messages", {"msg_id": "d1", "to": "zeta:acme_org", "from": "ofb:ofb_lead",
                                                  "text": "hi", "grants": [g]})
        self.assertEqual(cm.exception.status, 403)
        inside = self.call(a, "POST", "/v1/messages", {"msg_id": "d2", "to": "ofb:ofb_dev", "from": "ofb:ofb_lead",
                                                       "text": "hi", "grants": [g]})
        self.assertEqual(inside["authority"], "unverified_claim")
        m = {"id": "g-m", "subject": "ofb_lead", "issuer": "tenant", "tier": "propose", "scope": {"to_org": ["zeta"]}}
        m["signature"] = of_grants.sign_ed25519(m, self.grant_key)
        ok = self.call(a, "POST", "/v1/messages", {"msg_id": "d3", "to": "zeta:acme_org", "from": "ofb:ofb_lead",
                                                   "text": "hi", "grants": [g, m]})
        self.assertEqual(ok["authority"], "verified")

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
