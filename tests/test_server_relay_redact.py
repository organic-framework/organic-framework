"""`of_server.py relay-redact` (of_server, 2026-11-20; card OF-170, hand-over 7da5d129): a relay message's text is
replaced by a mark in the relay store and in the relay's own event, which carried it into storage."""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_events  # noqa: E402
import of_plugins  # noqa: E402
import of_server  # noqa: E402

TEXT = "a confidential matter's own words, never to stay"
MARK = "[redacted: test mark]"


class RelayRedactTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = os.path.join(root, "state")
        ship_cfg = {"unassigned_database": "db_un", "vault_database": "db_vault", "raw_to_org": False, "tenant": "t"}
        cfg = dict(of_server.DEFAULTS, state_dir=os.path.join(root, "server"), port=0, flush_every=3600,
                   orgs={"ofb": "db_ofb"})
        self.app = of_server.App(cfg, ship_cfg=ship_cfg, grants_loader=lambda: {}, grant_key=lambda: b"k",
                                 mask_key=b"m" * 32)
        self.assertIsInstance(self.app.storage, of_plugins.SQLiteStorage)
        self.msg = self.app.relay.system_post("ofb:of_conductor", TEXT)
        self.other = self.app.relay.system_post("ofb:of_conductor", "another message, kept as it is")
        self.app.writer.drain()

    def tearDown(self):
        self.app.writer.stop.set()
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def text_of(self, msg_id):
        with self.app.store.conn() as c:
            return c.execute("SELECT text FROM messages WHERE msg_id = ?", (msg_id,)).fetchone()[0]

    def test_the_store_and_the_event_carry_the_mark_and_nothing_else_changes(self):
        before = self.app.storage.message_events("db_ofb", [self.msg])
        self.assertEqual([json.loads(r["payload"])["text"] for r in before], [TEXT])
        out = of_server.relay_redact(self.app, [self.msg], MARK, "test (card OF-170)")
        self.assertEqual(out["messages"], [self.msg])
        self.assertEqual(out["events"], [before[0]["event_id"]])
        self.assertTrue(out["redaction"])
        self.app.writer.drain()
        self.assertEqual(self.text_of(self.msg), MARK)
        self.assertEqual(self.text_of(self.other), "another message, kept as it is")
        after = self.app.storage.message_events("db_ofb", [self.msg])
        self.assertEqual(len(after), 1)
        self.assertEqual(json.loads(after[0]["payload"])["text"], MARK)
        self.assertEqual(json.loads(after[0]["record"])["of.payload"]["text"], MARK)
        self.assertEqual(json.loads(after[0]["payload"])["msg_id"], self.msg)   # the rest of the event stays

    def test_the_relay_store_keeps_no_byte_of_the_old_text(self):
        of_server.relay_redact(self.app, [self.msg], MARK, "test")
        d = os.path.join(self.tmp.name, "server")
        for name in ("server.db", "server.db-wal"):
            p = os.path.join(d, name)
            if os.path.exists(p):
                self.assertNotIn(TEXT.encode(), open(p, "rb").read(), name)

    def test_a_second_run_and_an_unknown_id_change_nothing(self):
        of_server.relay_redact(self.app, [self.msg], MARK, "test")
        self.app.writer.drain()
        again = of_server.relay_redact(self.app, [self.msg, "01a1ffff-0000-7000-8000-000000000000"], MARK, "test")
        self.assertEqual((again["messages"], again["events"], again["redaction"]), ([], [], None))

    def test_a_reason_and_a_mark_are_required(self):
        with self.assertRaises(ValueError):
            of_server.relay_redact(self.app, [self.msg], MARK, " ")
        with self.assertRaises(ValueError):
            of_server.relay_redact(self.app, [self.msg], " ", "test")
        self.assertEqual(self.text_of(self.msg), TEXT)

    def test_a_storage_that_cannot_read_rows_redacts_the_store_and_says_so(self):
        class InsertOnly(of_plugins.Storage):
            def insert(self, database, table, rows):
                pass
        self.app.storage = InsertOnly()
        out = of_server.relay_redact(self.app, [self.msg], MARK, "test")
        self.assertEqual(out["messages"], [self.msg])
        self.assertIn("cannot read rows back", out["events_skipped"])


if __name__ == "__main__":
    unittest.main()
