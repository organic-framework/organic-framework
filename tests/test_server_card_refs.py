"""Card ids on of-server (POST /v1/card-refs; docs/server.md, Card ids): one counter per organization and prefix that
gives each number out once, the same number again for the same event, raised to every machine's floor, and only for
an organization the machine has joined (of_architect's choice (a), msg 01a1faa1-1fa0; of_platform's contract, msg
01a1faa6-8b66). The client side is tests/test_card_refs.py."""

import os
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_client  # noqa: E402
import of_events  # noqa: E402
import of_server  # noqa: E402


class FakeCH:
    def insert(self, database, table, rows):
        pass


class CardRefServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = os.path.join(root, "state")
        ship_cfg = {"unassigned_database": "db_un", "vault_database": "db_vault", "raw_to_org": False, "tenant": "t"}
        cfg = dict(of_server.DEFAULTS, state_dir=os.path.join(root, "server"), port=0, flush_every=3600,
                   orgs={"zeta": "db_zeta", "ofb": "db_ofb"})
        self.app = of_server.App(cfg, ch=FakeCH(), ship_cfg=ship_cfg, mask_key=b"m" * 32)
        self.httpd = self.app.serve(host="127.0.0.1", port=0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.a = self.machine("a", ["zeta"])
        self.b = self.machine("b", ["zeta", "ofb"])

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.app.writer.stop.set()
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def machine(self, name, orgs):
        d = os.path.join(self.tmp.name, name)
        os.makedirs(d)
        c = of_client.Client(self.url, key_path=os.path.join(d, "key"), state_dir=d)
        c.machine_id = f"m-{name}"
        of_server.enroll(self.app.store, c.machine_id, of_client.ensure_key(c.key_path), orgs, [])
        return c

    def post(self, c, **body):
        try:
            # its own timeout: one try, as of_events.card_ref calls it (a 503 is not waited out)
            return c._authed("POST", "/v1/card-refs", body=body, timeout=10)
        except of_client.ServerError as e:
            return e.status, e.body

    def test_each_number_once_and_the_same_one_again_for_the_same_event(self):
        self.assertEqual(self.a.card_ref("zeta", "ZT", "e1", 0), "ZT-1")   # the client reads the server's answer
        self.assertEqual(self.post(self.b, org="zeta", prefix="ZT", event_id="e1", floor=0),
                         (200, {"ref": "ZT-1", "already": True}))   # another machine, a retry: the same id
        self.assertEqual(self.b.card_ref("zeta", "ZT", "e2", 0), "ZT-2")
        rows = self.app.store.conn().c.execute("SELECT event_id, number, machine_id FROM card_refs ORDER BY number")
        self.assertEqual(rows.fetchall(), [("e1", 1, "m-a"), ("e2", 2, "m-b")])

    def test_the_floor_raises_the_counter_and_a_lower_one_never_lowers_it(self):
        self.assertEqual(self.a.card_ref("zeta", "ZT", "e1", 229), "ZT-230")
        self.assertEqual(self.b.card_ref("zeta", "ZT", "e2", 12), "ZT-231")
        self.assertEqual(self.b.card_ref("zeta", "ZT", "e3", 231), "ZT-232")

    def test_one_counter_per_organization_and_prefix(self):
        self.assertEqual(self.b.card_ref("zeta", "ZT", "e1", 0), "ZT-1")
        self.assertEqual(self.b.card_ref("ofb", "OF", "e2", 0), "OF-1")
        self.assertEqual(self.b.card_ref("zeta", "ZX", "e3", 0), "ZX-1")

    def test_two_machines_at_once_never_share_a_number(self):
        got, errors = [], []

        def take(c, k):
            try:
                for i in range(10):
                    got.append(c.card_ref("zeta", "ZT", f"{c.machine_id}-{k}-{i}", 0))
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))
        threads = [threading.Thread(target=take, args=(c, k)) for c in (self.a, self.b) for k in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(got, key=lambda r: int(r.split("-")[1])), [f"ZT-{n}" for n in range(1, 61)])

    def test_an_organization_the_machine_has_not_joined_is_refused(self):
        status, body = self.post(self.a, org="ofb", prefix="OF", event_id="e1", floor=0)
        self.assertEqual(status, 403)
        self.assertEqual(self.app.store.conn().c.execute("SELECT count(*) FROM card_refs").fetchone()[0], 0)

    def test_an_event_numbered_under_another_prefix_or_organization_is_a_conflict(self):
        self.assertEqual(self.b.card_ref("zeta", "ZT", "e1", 0), "ZT-1")
        for org, prefix in (("zeta", "ZX"), ("ofb", "ZT")):
            self.assertEqual(self.post(self.b, org=org, prefix=prefix, event_id="e1", floor=0)[0], 409, (org, prefix))

    def test_a_malformed_request_is_refused_and_gives_out_nothing(self):
        good = {"org": "zeta", "prefix": "ZT", "event_id": "01a1faa6-8b66-7520-b609-a8cc05a7c66c", "floor": 0}
        for bad in ({"prefix": "zt"}, {"prefix": "Z"}, {"prefix": "ZTABCD"}, {"org": "Zeta"}, {"org": ""},
                    {"event_id": ""}, {"event_id": "e 1"}, {"event_id": None}, {"floor": -1}, {"floor": True},
                    {"floor": "3"}, {"floor": 1.5}, {"floor": of_server.CARD_FLOOR_MAX + 1}):
            status, _ = self.post(self.a, **dict(good, **bad))
            self.assertEqual(status, 400, bad)
        self.assertEqual(self.app.store.conn().c.execute("SELECT count(*) FROM card_refs").fetchone()[0], 0)
        self.assertEqual(self.post(self.a, **dict(good, floor=of_server.CARD_FLOOR_MAX))[1]["ref"],
                         f"ZT-{of_server.CARD_FLOOR_MAX + 1}")

    def test_a_moving_organization_waits(self):
        with mock.patch.object(self.app, "moving", lambda: {"zeta"}):
            self.assertEqual(self.post(self.a, org="zeta", prefix="ZT", event_id="e1", floor=0)[0], 503)
        self.assertEqual(self.a.card_ref("zeta", "ZT", "e1", 0, timeout=10), "ZT-1")

    def test_the_terms_name_the_capability_and_a_token_is_required(self):
        terms = of_client.Client(self.url)._request("GET", "/v1/terms")[1]
        self.assertIn("card_refs", terms["capabilities"])
        with self.assertRaises(of_client.ServerError) as e:
            of_client.Client(self.url)._request("POST", "/v1/card-refs",
                                                body={"org": "zeta", "prefix": "ZT", "event_id": "e1", "floor": 0})
        self.assertEqual(e.exception.status, 401)


if __name__ == "__main__":
    unittest.main()
