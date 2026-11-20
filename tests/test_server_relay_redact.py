"""`of_server.py relay-redact` (of_server, 2026-11-20; card OF-170, hand-over 7da5d129): a relay message's text is
replaced by a mark in the relay store and in the relay's own event, which carried it into storage."""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import sqlite3  # noqa: E402
from unittest import mock  # noqa: E402

import of_asks_answer  # noqa: E402
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
        # this machine's event log: the owner's card, answered with its yes (option 1)
        self.log = sqlite3.connect(":memory:")
        self.log.execute("CREATE TABLE event (event_id TEXT PRIMARY KEY, ts TEXT, name TEXT, payload TEXT, "
                         "position TEXT, host_id TEXT)")
        self.log.execute("INSERT INTO event VALUES ('01a1f8dd-1df1-7000-8000-000000000001', '2026-11-20T10:00:00Z', "
                         "'of.agent_question', ?, 'of_server', 'm')",
                         (json.dumps({"ref": "OF-170", "questions": [{"question": "Replace it with a mark?",
                                                                      "options": ["yes", "no"]}]}),))
        self.log.execute("INSERT INTO event VALUES ('01a1f8dd-2222-7000-8000-000000000002', '2026-11-20T10:01:00Z', "
                         "'of.agent_question_answered', ?, 'founder', 'm')",
                         (json.dumps({"ask_id": "01a1f8dd-1df1-7000-8000-000000000001", "answer": "yes",
                                      "by": "owner"}),))
        self.log.execute("INSERT INTO event VALUES ('01a1f8dd-3333-7000-8000-000000000003', '2026-11-20T10:00:00Z', "
                         "'of.agent_question', ?, 'of_server', 'm')",
                         (json.dumps({"ref": "OF-171", "questions": [{"question": "Something else?",
                                                                      "options": ["yes", "no"]}]}),))
        for patch in (mock.patch.object(of_asks_answer, "not_the_owner", lambda: None),):
            patch.start()
            self.addCleanup(patch.stop)
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
        before = self.app.storage.message_events("db_ofb", [self.msg], "2026-01-01T00:00:00Z")
        self.assertEqual([json.loads(r["payload"])["text"] for r in before], [TEXT])
        out = of_server.relay_redact(self.app, [self.msg], MARK, "test", "OF-170", conn=self.log)
        self.assertEqual(out["messages"], [self.msg])
        self.assertEqual(out["events"], [before[0]["event_id"]])
        self.assertTrue(out["redaction"])
        self.app.writer.drain()
        self.assertEqual(self.text_of(self.msg), MARK)
        self.assertEqual(self.text_of(self.other), "another message, kept as it is")
        after = self.app.storage.message_events("db_ofb", [self.msg], "2026-01-01T00:00:00Z")
        self.assertEqual(len(after), 1)
        self.assertEqual(json.loads(after[0]["payload"])["text"], MARK)
        self.assertEqual(json.loads(after[0]["record"])["of.payload"]["text"], MARK)
        self.assertEqual(json.loads(after[0]["payload"])["msg_id"], self.msg)   # the rest of the event stays

    def test_the_relay_store_keeps_no_byte_of_the_old_text(self):
        of_server.relay_redact(self.app, [self.msg], MARK, "test", "OF-170", conn=self.log)
        d = os.path.join(self.tmp.name, "server")
        for name in ("server.db", "server.db-wal"):
            p = os.path.join(d, name)
            if os.path.exists(p):
                self.assertNotIn(TEXT.encode(), open(p, "rb").read(), name)

    def test_a_second_run_and_an_unknown_id_change_nothing(self):
        of_server.relay_redact(self.app, [self.msg], MARK, "test", "OF-170", conn=self.log)
        self.app.writer.drain()
        again = of_server.relay_redact(self.app, [self.msg, "01a1ffff-0000-7000-8000-000000000000"], MARK, "test", "OF-170",
                                         conn=self.log)
        self.assertEqual((again["messages"], again["events"], again["redaction"]), ([], [], None))

    def test_a_reason_and_a_mark_are_required(self):
        with self.assertRaises(ValueError):
            of_server.relay_redact(self.app, [self.msg], MARK, " ", "OF-170", conn=self.log)
        with self.assertRaises(ValueError):
            of_server.relay_redact(self.app, [self.msg], " ", "test", "OF-170", conn=self.log)
        self.assertEqual(self.text_of(self.msg), TEXT)

    def test_it_is_the_owners_with_his_yes_on_a_card(self):
        # of_secrets-review on organic-framework PR 491: a position could have erased the text of a message it sent
        with mock.patch.object(of_asks_answer, "not_the_owner", lambda: "this runs in an agent's session"):
            with self.assertRaises(PermissionError):
                of_server.relay_redact(self.app, [self.msg], MARK, "test", "OF-170", conn=self.log)
        for ask in (None, "the founder said so", "OF-171", "OF-999"):
            with self.assertRaises(ValueError, msg=ask):
                of_server.relay_redact(self.app, [self.msg], MARK, "test", ask, conn=self.log)
        self.assertEqual(self.text_of(self.msg), TEXT)

    def test_a_storage_that_cannot_read_rows_redacts_the_store_and_says_so(self):
        class InsertOnly(of_plugins.Storage):
            def insert(self, database, table, rows):
                pass
        self.app.storage = InsertOnly()
        out = of_server.relay_redact(self.app, [self.msg], MARK, "test", "OF-170", conn=self.log)
        self.assertEqual(out["messages"], [self.msg])
        self.assertIn("cannot read rows back", out["events_skipped"])


if __name__ == "__main__":
    unittest.main()
