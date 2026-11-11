"""of-server's coordination feed (bin/of_server_coord.py, GET /v1/coord) and its event list (bin/of_coord.py).
docs/server-several-machines.md §4.2."""

import json
import os
import re
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_client  # noqa: E402
import of_coord  # noqa: E402
import of_events  # noqa: E402
import of_server  # noqa: E402
import of_relay_client  # noqa: E402
import of_server_coord  # noqa: E402
import of_ship  # noqa: E402
import of_store  # noqa: E402

KEY = "ghp_" + "Zq3vX9kLm2Pw7RtY4nB8cD1fGh6Jk5Ls0QaE"


def event(eid, org, name, payload=None, host="m-a", **extra):
    rec = {"of.schema": 1, "of.event.id": eid, "timestamp": "2026-11-10T21:00:00.000Z", "event.name": name,
           "of.org": org, "of.payload": payload or {}, **extra}
    return {"type": "event", "data": {"event_id": eid, "org": org, "org_id": org, "ts": rec["timestamp"], "name": name,
                                      "session_id": "S", "position": "p", "source": "hook", "host_id": host,
                                      "payload": json.dumps(payload or {}), "raw_ref": "", "record": json.dumps(rec)}}


class FakeCH:
    def __init__(self):
        self.inserts = []

    def insert(self, database, table, rows):
        self.inserts.append((database, table, rows))


class CoordFeedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = os.path.join(root, "state")
        ship_cfg = {"unassigned_database": "db_un", "vault_database": "db_vault", "raw_to_org": False, "tenant": "t"}
        cfg = dict(of_server.DEFAULTS, state_dir=os.path.join(root, "server"), port=0, flush_every=3600,
                   orgs={"ofb": "db_ofb", "mt": "db_mt"})
        self.app = of_server.App(cfg, ch=FakeCH(), ship_cfg=ship_cfg, grants_loader=lambda: {}, mask_key=b"m" * 32)
        self.httpd = self.app.serve(host="127.0.0.1", port=0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.app.writer.stop.set()
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def machine(self, name, orgs, tenant=False):
        d = os.path.join(self.tmp.name, name)
        os.makedirs(d)
        c = of_client.Client(self.url, key_path=os.path.join(d, "key"), state_dir=d)
        c.machine_id = f"m-{name}"
        of_server.enroll(self.app.store, c.machine_id, of_client.ensure_key(c.key_path), orgs, [], tenant=tenant)
        return c

    def feed(self, c, org, after=0):
        return c._authed("GET", f"/v1/coord?org={org}&after={after}&wait=0")[1]

    def test_an_organizations_coordination_reaches_its_other_machines_masked(self):
        a, b, c = self.machine("a", ["ofb", "mt"]), self.machine("b", ["ofb"]), self.machine("c", ["mt"])
        records = [event("e1", "ofb", "of.agent_question", {"questions": [{"question": f"deploy with {KEY}?"}]}),
                   event("e2", "ofb", "of.human_prompt", {"text": "a prompt stays on its machine"}),
                   event("e3", "ofb", "of.handover_proposed", {"to": "ofb_dev"}),
                   event("e4", "mt", "of.state_declared", {"state": "working"})]
        a.send_batch(records, batch_id="b1")
        self.app.writer.drain()
        got = self.feed(b, "ofb")
        self.assertEqual([(e["event_id"], e["name"]) for e in got["events"]],
                         [("e1", "of.agent_question"), ("e3", "of.handover_proposed")])
        self.assertNotIn(KEY, json.dumps(got))                       # the rows the organization got: masked
        self.assertIn("[secret:github-pat:", json.dumps(got["events"][0]["record"]))
        self.assertEqual(got["events"][0]["record"]["of.event.id"], "e1")   # a record the client stores as it is
        self.assertEqual((got["next"], got["last"]), (got["events"][-1]["seq"], got["events"][-1]["seq"]))
        self.assertEqual(self.feed(b, "ofb", after=got["next"])["events"], [])
        self.assertEqual([e["event_id"] for e in self.feed(c, "mt")["events"]], ["e4"])
        with self.assertRaises(of_client.ServerError) as cm:          # an organization this machine has not joined
            self.feed(c, "ofb")
        self.assertEqual(cm.exception.status, 403)
        a.send_batch(records + [event("e5", "ofb", "of.state_declared")], batch_id="b2")  # re-sent: nothing twice
        self.app.writer.drain()
        self.assertEqual([e["event_id"] for e in self.feed(b, "ofb", after=got["next"])["events"]], ["e5"])

    def test_a_cursor_past_the_feed_learns_where_it_ends(self):
        b = self.machine("b", ["ofb"])
        got = self.feed(b, "ofb", after=99)   # the server's feed started over
        self.assertEqual((got["events"], got["next"], got["last"]), ([], 99, 0))

    def test_shape_copies_and_other_names_stay_out(self):
        store = self.app.store
        own = event("s1", "ofb", "of.state_declared")["data"]
        shaped = event("s2", "ofb", "of.state_declared", **{"of.shape": True, "of.serves": "mt"})["data"]
        other = event("s3", "ofb", "of.turn_end")["data"]
        self.assertEqual(of_server_coord.copy(store, "ofb", [own, shaped, other]), 1)
        self.assertEqual(of_server_coord.copy(store, "ofb", [own]), 0)   # idempotent

    def test_old_events_leave_the_feed(self):
        store = self.app.store
        of_server_coord.copy(store, "ofb", [event("o1", "ofb", "of.state_declared")["data"]], now=1000.0)
        of_server_coord.copy(store, "ofb", [event("o2", "ofb", "of.state_declared")["data"]],
                             now=1000.0 + of_coord.RETENTION_S - 1)
        self.assertEqual(of_server_coord.prune(store, now=1000.0 + of_coord.RETENTION_S + 1, force=True), 1)
        b = self.machine("b", ["ofb"])
        self.assertEqual([e["event_id"] for e in self.feed(b, "ofb")["events"]], ["o2"])


    def test_a_relay_stores_other_machines_events_once_and_never_ships_them_back(self):
        """Two machines of one organization: b's relay pulls a's ask into b's store; b's own event is not
        stored twice, a second read adds nothing, and b's shipper skips what it pulled."""
        a, b = self.machine("a", ["ofb"]), self.machine("b", ["ofb"])
        a.send_batch([event("e1", "ofb", "of.agent_question", {"q": "deploy?"}),
                      event("e2", "ofb", "of.state_declared", host="m-b")], batch_id="b1")
        self.app.writer.drain()
        conn = of_store.connect()
        cursor = {}
        self.assertEqual(of_relay_client.pull_coord(b, self.url, "ofb", conn, cursor, "m-b"), 1)
        rows = conn.execute("SELECT event_id, name, raw_ref FROM event WHERE event_id IN ('e1', 'e2')").fetchall()
        self.assertEqual([(r[0], r[1]) for r in rows], [("e1", "of.agent_question")])
        self.assertTrue(rows[0][2].startswith("coord:ofb:"))
        self.assertEqual(cursor, {f"{self.url}|ofb": 2})
        self.assertEqual(of_relay_client.pull_coord(b, self.url, "ofb", conn, cursor, "m-b"), 0)
        cfg = {"tenant": "t", "batch_rows": 100, "batch_bytes": 1 << 20}
        conn.row_factory = __import__("sqlite3").Row  # as of ship reads its store
        shipped = [d["event_id"] for t, _, batch in of_ship._batches(conn, cfg, "m-b") if t == "event" for d in batch]
        self.assertNotIn("e1", shipped)
        conn.close()


class EventListTest(unittest.TestCase):
    """docs/server-several-machines.md §4.2: a new event a projection reads cannot silently stay on one machine."""
    PROJECTIONS = ("of_asks.py", "of_asks_tui.py", "of_pulse.py", "of_handover.py", "of_listen.py")

    def test_every_event_a_projection_reads_is_shared_or_local_by_decision(self):
        bin_dir = os.path.join(HERE, "..", "bin")
        undecided = {}
        for f in self.PROJECTIONS:
            with open(os.path.join(bin_dir, f)) as fh:
                for name in set(re.findall(r"[\"'](of\.[a-z_]+)[\"']", fh.read())):
                    if not of_coord.shared(name) and name not in of_coord.LOCAL_ONLY:
                        undecided.setdefault(name, []).append(f)
        self.assertEqual(undecided, {}, "add each name to of_coord.EVENTS (shared) or LOCAL_ONLY (stays here)")

    def test_the_two_lists_do_not_overlap(self):
        self.assertEqual({n for n in of_coord.LOCAL_ONLY if of_coord.shared(n)}, set())


if __name__ == "__main__":
    unittest.main()
