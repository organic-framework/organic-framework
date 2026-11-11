"""Finance facts, client side (docs/events.md "Finance facts"): validated, sent to the server, a stub kept here."""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_events  # noqa: E402
import of_finance  # noqa: E402
import of_store  # noqa: E402

GOOD = {"period": "2026-10", "kind": "OE", "category": "claude_accounts", "amount": "1200.00", "currency": "USD",
        "basis": "actual", "source": "invoice 2026-10-17", "grant_id": "g-2026-11-10-007"}


class FakeClient:
    def __init__(self, caps=("finance_facts",), fail=None):
        self.caps, self.fail, self.sent = list(caps), fail, []

    def terms(self):
        return {"capabilities": self.caps}

    def _authed(self, method, path, body=None):
        if self.fail:
            raise self.fail
        self.sent.append((method, path, body))
        return 200, {"event_id": body["record"]["of.event.id"], "accepted": True}


class FinanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = self.tmp.name
        of_events._CONN.clear()
        self.conn = of_store.connect(os.path.join(self.tmp.name, "of.db"))

    def tearDown(self):
        self.conn.close()
        of_events._CONN.clear()
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def test_the_payload_rules(self):
        self.assertEqual(of_finance.validate(GOOD), [])
        bad = dict(GOOD, period="2026-13", kind="X", amount=12.5, currency="usd dollars", basis="guess", source="",
                   grant_id="")
        text = "; ".join(of_finance.validate(bad))
        for want in ("period", "kind", "amount must be a decimal string", "currency", "basis", "source", "grant_id"):
            self.assertIn(want, text)
        self.assertIn("an OE category", "; ".join(of_finance.validate(dict(GOOD, category="coffee"))))
        eur = dict(GOOD, currency="EUR")
        self.assertIn("amount_usd", "; ".join(of_finance.validate(eur)))
        self.assertEqual(of_finance.validate(dict(eur, amount_usd="1290.10",
                                                  fx={"rate": "1.0751", "source": "ECB", "date": "2026-10-31"})), [])
        t = dict(GOOD, kind="T", category="storefront", product="arb")
        self.assertEqual(of_finance.validate(t, systems={"storefront"}), [])
        self.assertIn("system id", "; ".join(of_finance.validate(t, systems={"ci"})))
        self.assertIn("product is for kind T only", "; ".join(of_finance.validate(dict(GOOD, product="x"))))

    def test_sent_to_the_server_and_only_a_stub_kept_here(self):
        c = FakeClient()
        eid, answer = of_finance.write(GOOD, "books_keeper", "acme", "https://srv", c, self.conn)
        self.assertTrue(answer["accepted"])
        method, path, body = c.sent[0]
        self.assertEqual((method, path), ("POST", "/v1/finance_facts"))
        self.assertEqual(body["record"]["of.payload"]["amount"], "1200.00")
        row = self.conn.execute("SELECT event_id, org, position, sha256 FROM finance_stub").fetchone()
        self.assertEqual(row[:3], (eid, "acme", "books_keeper"))
        self.assertEqual(row[3], of_finance.payload_sha(GOOD))
        # the amount is nowhere in the local event table or its full-text index
        dump = json.dumps([list(r) for r in self.conn.execute("SELECT * FROM event")])
        self.assertNotIn("1200.00", dump)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM event_fts").fetchone()[0], 0)

    def test_nothing_sent_or_kept_without_the_servers_side_or_on_a_refusal(self):
        with self.assertRaises(ValueError) as e:
            of_finance.write(GOOD, "books_keeper", "acme", "https://srv", FakeClient(caps=()), self.conn)
        self.assertIn("does not accept finance facts yet", str(e.exception))
        with self.assertRaises(RuntimeError):
            of_finance.write(GOOD, "books_keeper", "acme", "https://srv", FakeClient(fail=RuntimeError("403")),
                             self.conn)
        self.conn.execute(of_finance.STUB_DDL)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM finance_stub").fetchone()[0], 0)
        with self.assertRaises(ValueError):
            of_finance.write(dict(GOOD, amount=1.5), "books_keeper", "acme", "https://srv", FakeClient(),
                             self.conn)


class BuildTest(unittest.TestCase):
    def test_a_balance_names_its_account_and_day_and_no_category(self):
        p = of_finance.build("B", "2026-11-10", "1250000.00", "USDC", "actual", "statement 2026-11-10",
                             grant_id="g-1", account="ops-wallet", category="ignored", product="ignored",
                             amount_usd="1250000.00", fx_rate="1", fx_source="peg", fx_date="2026-11-10")
        self.assertEqual(p["account"], "ops-wallet")
        self.assertNotIn("category", p)
        self.assertNotIn("product", p)
        self.assertEqual((p["period"], p["kind"], p["fx"]["rate"]), ("2026-11-10", "B", "1"))

    def test_a_flow_keeps_its_category_and_no_account(self):
        p = of_finance.build("T", "2026-10", "100", "USD", "actual", "report 7", category="storefront",
                             account="x")
        self.assertEqual((p["category"], "account" in p, "fx" in p), ("storefront", False, False))


if __name__ == "__main__":
    unittest.main()
