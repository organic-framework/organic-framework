"""Finance facts on of-server (bin/of_server_finance.py, POST /v1/finance_facts): docs/events.md, Finance facts.
Server-only write, the grant check, the writer's holding, supersedes, and the refusal anywhere else."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_client  # noqa: E402
import of_events  # noqa: E402
import of_grants  # noqa: E402
import of_route  # noqa: E402
import of_server  # noqa: E402
import of_server_finance as fin  # noqa: E402


def payload(**kw):
    p = {"period": "2026-10", "kind": "OE", "category": "servers", "amount": "1234.50", "currency": "USD",
         "basis": "actual", "source": "invoice HZ-2026-10-118", "grant_id": "g-books"}
    p.update(kw)
    return {k: v for k, v in p.items() if v is not None}


def record(eid, p, position="books_keeper", session="S-books", org="zeta"):
    return {"of.schema": 1, "of.event.id": eid, "timestamp": "2026-11-10T21:30:00.000Z", "event.name": fin.FINANCE,
            "host.id": "m-a", "gen_ai.conversation.id": session, "gen_ai.agent.name": position, "of.org": org,
            "of.source": "of", "of.payload": p}


class FakeCH:
    def __init__(self):
        self.inserts = []

    def insert(self, database, table, rows):
        self.inserts.append((database, table, rows))


class PayloadTest(unittest.TestCase):
    def test_a_fact_is_checked_field_by_field(self):
        self.assertEqual(fin.problems(payload()), [])
        self.assertEqual(fin.problems(payload(kind="T", category="storefront", product="liquidations")), [])
        eur = payload(currency="EUR", amount_usd="1350.10", fx={"rate": "1.0937", "source": "ECB", "date": "2026-10-31"})
        self.assertEqual(fin.problems(eur), [])
        cases = {
            "period must be YYYY-MM": payload(period="2026-13"),
            "kind must be T, OE, I or B": payload(kind="X"),
            "an OE category is one of": payload(category="lunch"),
            "product is for kind T only": payload(product="p"),
            "amount must be a decimal string (no floats)": payload(amount=1234.5),
            "amount_usd is required when currency is not USD": payload(currency="EUR"),
            "fx must be": payload(currency="EUR", amount_usd="1"),
            "basis must be actual or estimate": payload(basis="guess"),
            "source must name the document": payload(source="x" * 301),
            "grant_id is required": payload(grant_id=None),
            "unknown field person": payload(person="a name"),   # amounts per person are never recorded
        }
        for want, p in cases.items():
            self.assertTrue(any(x.startswith(want) for x in fin.problems(p)), (want, fin.problems(p)))

    def test_a_balance_has_a_day_and_an_account_instead_of_a_category(self):
        """Kind B (of_platform, msg 01a1cab5; docs/events.md, Finance facts): the amount held at the end of one day
        in one account, named by the treasury, never by its number or address."""
        b = payload(kind="B", period="2026-11-10", category=None, account="ops wallet", amount="52010.00")
        self.assertEqual(fin.problems(b), [])
        cases = {
            "period must be the day YYYY-MM-DD": dict(b, period="2026-11"),
            "category is absent for a balance": dict(b, category="servers"),
            "account must be the treasury's own short name": {k: v for k, v in b.items() if k != "account"},
            "account must be a name, never an account number or an address": dict(b, account="AE070331234567890123456"),
            "account is for a balance (B) only": payload(account="ops wallet"),
        }
        for want, p in cases.items():
            self.assertTrue(any(x.startswith(want) for x in fin.problems(p)), (want, fin.problems(p)))
        for number in ("0x52908400098527886E0F7030069857D2E4169EE7", "main 12345678", "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"):
            self.assertTrue(fin.problems(dict(b, account=number)), number)
        self.assertTrue(fin.problems(dict(b, period="2026-02-30")))


class FinanceServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory()
        cls.gkey = os.path.join(cls.keys.name, "grant_ed25519")
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "", "-f", cls.gkey], check=True,
                       env=of_grants.no_agent_env())
        with open(cls.gkey + ".pub") as fh:
            cls.pub = fh.read().strip()

    @classmethod
    def tearDownClass(cls):
        cls.keys.cleanup()

    def grant(self, gid, subject, tier="act_and_report", data=("finance",), effect="allow"):
        g = {"id": gid, "subject": subject, "issuer": "tenant", "tier": tier, "effect": effect, "critical": False,
             "scope": {"data": list(data)} if data else {}, "expires": None, "counter": None, "revoked": None,
             "provenance": {"kind": "standing", "chain": [], "source": "test"}}
        g["signature"] = of_grants.sign_ed25519(g, self.gkey)
        return g

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = os.path.join(root, "state")
        self.ch = FakeCH()
        ship_cfg = {"unassigned_database": "db_un", "vault_database": "db_vault", "raw_to_org": False, "tenant": "t"}
        positions = os.path.join(root, "positions.yaml")
        with open(positions, "w") as fh:
            fh.write("positions:\n  - {name: books_keeper, holder: {session: S-books}}\n"
                     "  - {name: k8s_keeper, holder: {session: S-k8s}}\n")
        cfg = dict(of_server.DEFAULTS, state_dir=os.path.join(root, "server"), port=0, flush_every=3600,
                   orgs={"zeta": "db_mt", "ofb": "db_ofb"}, positions_files={"zeta": positions})
        self.grants = {"zeta": [self.grant("g-books", "books_keeper"), self.grant("g-read", "k8s_keeper", tier="observe"),
                              self.grant("g-plain", "k8s_keeper", data=())], "ofb": []}
        self.app = of_server.App(cfg, ch=self.ch, ship_cfg=ship_cfg, grants_loader=lambda: self.grants,
                                 grant_key=lambda: of_grants.Keys(ed25519=[self.pub]), mask_key=b"m" * 32)
        self.httpd = self.app.serve(host="127.0.0.1", port=0)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.a = self.machine("a", ["zeta"])
        self.present(self.a, "books_keeper", "S-books")

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

    def present(self, c, position, session):
        c._authed("PUT", "/v1/presence", body={"holders": [{"org": "zeta", "position": position, "session_id": session,
                                                             "kind": "agent", "pane": "w1:p1"}]})

    def post(self, c, rec):
        try:
            return 202, c._authed("POST", "/v1/finance_facts", body={"record": rec})[1]
        except of_client.ServerError as e:
            return e.status, str(e)

    def written(self):
        self.app.writer.drain()
        return [(d, r["event_id"]) for d, t, rows in self.ch.inserts if t == "event" for r in rows
                if r["name"] == fin.FINANCE]

    def test_the_finance_unit_writes_a_fact_and_only_its_organization_gets_it(self):
        status, ans = self.post(self.a, record("f1", payload()))
        self.assertEqual((status, ans["event_id"], ans["accepted"]), (202, "f1", True))
        self.assertEqual(self.written(), [("db_mt", "f1")])
        self.assertEqual(self.post(self.a, record("f1", payload()))[1].get("already"), True)   # a retry: the same answer
        status, why = self.post(self.a, record("f1", payload(amount="1")))
        self.assertEqual(status, 409)
        terms = of_client.Client(self.url)._request("GET", "/v1/terms")[1]
        self.assertIn("finance_facts", terms["capabilities"])

    def test_the_grant_and_the_holder_are_checked(self):
        k = self.machine("k", ["zeta"])
        self.present(k, "k8s_keeper", "S-k8s")
        for gid, want in (("g-read", "it reads finance facts"), ("g-plain", "no data: [finance]"),
                          ("g-books", "grant is for books_keeper, not k8s_keeper"), ("g-none", "unknown grant")):
            status, why = self.post(k, record(f"k-{gid}", payload(grant_id=gid), position="k8s_keeper", session="S-k8s"))
            self.assertEqual(status, 403, gid)
            self.assertIn(want, why)
        status, why = self.post(k, record("k-x", payload()))            # books_keeper's grant from a machine not holding it
        self.assertEqual(status, 403)
        self.assertIn("writer not holder", why)
        self.grants["zeta"].append(self.grant("g-deny", "books_keeper", effect="deny"))
        self.app._grants = None
        status, why = self.post(self.a, record("f-denied", payload()))
        self.assertEqual(status, 403)
        self.assertIn("denied by grant g-deny", why)
        self.assertEqual(self.written(), [])

    def test_a_balance_supersedes_only_the_same_account_and_day(self):
        b = payload(kind="B", period="2026-11-10", category=None, account="ops wallet", amount="52010.00")
        self.assertEqual(self.post(self.a, record("b1", b))[0], 202)
        self.assertEqual(self.post(self.a, record("b2", dict(b, account="cold wallet")))[0], 202)
        status, why = self.post(self.a, record("b3", dict(b, account="cold wallet", supersedes="b1")))
        self.assertEqual(status, 400)
        self.assertIn("for B (org, period, account)", why)
        status, why = self.post(self.a, record("b3", dict(b, period="2026-11-11", supersedes="b1")))
        self.assertEqual(status, 400)
        self.assertEqual(self.post(self.a, record("b3", dict(b, amount="52011.00", supersedes="b1")))[0], 202)

    def test_a_server_db_from_before_balances_gains_the_account_column(self):
        with self.app.store.conn() as c:
            c.execute("DROP TABLE IF EXISTS finance_facts")
            c.execute("CREATE TABLE finance_facts (event_id TEXT PRIMARY KEY, org TEXT NOT NULL, period TEXT NOT NULL, "
                      "kind TEXT NOT NULL, category TEXT NOT NULL, product TEXT NOT NULL DEFAULT '', position TEXT, "
                      "sha TEXT NOT NULL, supersedes TEXT, batch_id TEXT, created_at TEXT)")
        self.assertEqual(self.post(self.a, record("f1", payload()))[0], 202)
        b = payload(kind="B", period="2026-11-10", category=None, account="ops wallet")
        self.assertEqual(self.post(self.a, record("b1", b))[0], 202)

    def test_supersedes_names_the_current_fact_of_the_same_key(self):
        self.assertEqual(self.post(self.a, record("f1", payload()))[0], 202)
        status, why = self.post(self.a, record("f2", payload(category="saas", supersedes="f1")))
        self.assertEqual(status, 400)
        self.assertIn("another (org, period, kind, category, product)", why)
        self.assertEqual(self.post(self.a, record("f2", payload(amount="1300.00", supersedes="f1")))[0], 202)
        status, why = self.post(self.a, record("f3", payload(amount="1290.00", supersedes="f1")))
        self.assertEqual(status, 409)
        self.assertIn("already superseded by f2", why)
        self.assertEqual(self.post(self.a, record("f3", payload(amount="1290.00", supersedes="f2")))[0], 202)
        status, why = self.post(self.a, record("f4", payload(supersedes="nope")))
        self.assertEqual(status, 400)

    def test_a_batch_never_carries_a_fact(self):
        data = {"event_id": "fb", "org": "zeta", "org_id": "zeta", "ts": "2026-11-10T21:30:00.000Z", "name": fin.FINANCE,
                "session_id": "S-books", "position": "books_keeper", "source": "hook", "payload": json.dumps(payload()),
                "raw_ref": "", "record": json.dumps(record("fb", payload()))}
        r = self.a.send_batch([{"type": "event", "data": data}], batch_id="b1")
        self.assertEqual(r["accepted"], 0)
        self.assertIn("only through POST /v1/finance_facts", r["rejected"][0]["reason"])
        self.assertEqual(self.written(), [])


class CopyTest(unittest.TestCase):
    def test_no_shape_or_publication_copy(self):
        class R:
            db_by_org = {"zeta": "db_mt", "ofb": "db_ofb"}
            unassigned = "db_un"

            def database(self, org):
                return self.db_by_org.get(org, "db_un")

            def builder_of(self, *a):
                return "ofb"   # a builder organization that would get shape copies of anything else

        row = {"event_id": "f1", "org_id": "zeta", "name": fin.FINANCE, "session_id": "S", "payload": "{}",
               "record": json.dumps({"of.event.id": "f1"})}
        out = of_route.fanout("event", [row], R(), {"vault_database": "db_vault"}, copies=True)
        self.assertEqual({db: [r["event_id"] for r in rows] for db, rows in out.items()}, {"db_mt": ["f1"]})


class ClickHouseViewTest(unittest.TestCase):
    def test_the_colleagues_view_leaves_finance_facts_out(self):
        """docs/events.md, Finance facts: owner-only rows. `<db>_read` reads only the views, which run as their
        definer, so the filter is in v_event itself, replaced on an existing installation."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("ch_schema", os.path.join(HERE, "..", "deploy", "clickhouse",
                                                                                "schema.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sql = mod.schema(["of_mt"], "of_mt_vault", "of_mt_un")
        self.assertIn("CREATE OR REPLACE VIEW of_mt.v_event SQL SECURITY DEFINER AS SELECT * FROM of_mt.event"
                      " WHERE name != 'of.finance_fact';", sql)
        self.assertNotIn("of_finance_fact", sql.replace("of.finance_fact", ""))
        self.assertEqual(sql.count("finance_fact"), 1)   # only the colleagues' event view filters


if __name__ == "__main__":
    unittest.main()
