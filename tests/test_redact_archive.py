"""The raw archive's register of content redactions (of_server, 2026-11-20; hand-over 7da5d129, card OF-170): a
registered session's records are stored with their mark in every raw copy, whatever copy the archive makes."""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_redact  # noqa: E402

S = "0b4c8a1e-1111-4a2b-9c3d-5e6f7a8b9c0d"
OTHER = "7d2e9f30-2222-4b3c-8d4e-6f7a8b9c0d1e"
MARK = "[redacted: test mark]"
U1, U2, U3 = ("1a2b3c4d-0000-4000-8000-00000000000" + str(i) for i in (1, 2, 3))


def line(**rec):
    return json.dumps(rec, separators=(",", ":")).encode()


TARGET = line(parentUuid=None, type="user", uuid=U1, sessionId=S, timestamp="2026-11-20T10:00:00Z",
              message={"role": "user", "content": "the matter's own words"})
KEEP = line(parentUuid=U1, type="assistant", uuid=U2, sessionId=S, timestamp="2026-11-20T10:00:01Z",
            message={"role": "assistant", "id": "msg_1", "content": [{"type": "text", "text": "an unnamed reply"}]})
NOUUID = line(type="queue-operation", operation="enqueue", sessionId=S, content="queued words of the matter")


def register(*entries):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
    return path


class ArchiveRegisterTest(unittest.TestCase):
    def entries(self, keys, session=S):
        p = register({"session": session, "keys": keys, "mark": MARK, "reason": "test", "ask": "OF-170"})
        self.addCleanup(os.unlink, p)
        return of_redact.archive_register(p)

    def test_a_named_record_is_marked_and_keeps_its_shape(self):
        data = TARGET + b"\n" + KEEP + b"\n"
        out = of_redact.archive_filter(f".claude/projects/-x/{S}.jsonl", data, self.entries([U1]))
        lines = out.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertNotIn(b"matter", out)
        rec = json.loads(lines[0])
        self.assertEqual((rec["uuid"], rec["sessionId"], rec["type"], rec["of_redacted"]), (U1, S, "user", MARK))
        self.assertEqual(rec["message"], {"role": "user", "content": [{"type": "text", "text": MARK}]})
        self.assertEqual(lines[1], KEEP)

    def test_a_line_with_no_uuid_is_named_by_its_hash(self):
        data = KEEP + b"\n" + NOUUID + b"\n"
        out = of_redact.archive_filter(f"p/{S}.jsonl", data, self.entries([hashlib.sha256(NOUUID).hexdigest()]))
        self.assertNotIn(b"matter", out)
        self.assertEqual(out.splitlines()[0], KEEP)
        self.assertEqual(json.loads(out.splitlines()[1])["type"], "queue-operation")

    def test_an_unnamed_session_is_returned_as_it_is(self):
        data = TARGET + b"\n"
        self.assertIs(of_redact.archive_filter(f"p/{OTHER}.jsonl", data, self.entries([U1])), data)
        self.assertIs(of_redact.archive_filter(f"p/{S}.jsonl", data, self.entries([U3])), data)

    def test_a_rotated_copy_and_a_subagent_name_the_session(self):
        e = self.entries([U1])
        for rel in (f"p/{S}.jsonl.1795188417", f"p/{S}/subagents/agent-a1.jsonl"):
            self.assertNotIn(b"matter", of_redact.archive_filter(rel, TARGET + b"\n", e), rel)

    def test_an_appended_frame_with_a_cut_last_line_keeps_it(self):
        data = TARGET + b"\n" + KEEP[:40]
        out = of_redact.archive_filter(f"p/{S}.jsonl", data, self.entries([U1]))
        self.assertTrue(out.endswith(KEEP[:40]))
        self.assertNotIn(b"matter", out)

    def test_the_mark_is_stable(self):
        e = self.entries([U1])
        once = of_redact.archive_filter(f"p/{S}.jsonl", TARGET + b"\n", e)
        self.assertEqual(of_redact.archive_filter(f"p/{S}.jsonl", once, e), once)

    def test_a_missing_register_is_empty_and_a_broken_one_refuses(self):
        self.assertEqual(of_redact.archive_register(os.path.join(tempfile.gettempdir(), "no-such-register")), [])
        for bad in ("not json\n", json.dumps({"session": S, "keys": [], "mark": MARK, "ask": "OF-170"}) + "\n",
                    json.dumps({"session": S, "keys": [U1], "ask": "OF-170"}) + "\n",
                    json.dumps({"session": S, "keys": [U1], "mark": MARK}) + "\n",       # no owner's card
                    json.dumps({"session": S, "keys": [U1], "mark": MARK, "ask": "the founder said so"}) + "\n"):
            fd, p = tempfile.mkstemp()
            with os.fdopen(fd, "w") as fh:
                fh.write(bad)
            self.addCleanup(os.unlink, p)
            with self.assertRaises(of_redact.RegisterError):
                of_redact.archive_register(p)
        self.assertTrue(issubclass(of_redact.RegisterError, OSError))   # of_archive skips the file on an OSError

    def test_a_codex_rollout_names_its_session_at_the_stem_end(self):
        rel = f".codex/sessions/2026/11/20/rollout-2026-11-20T10-00-00-{S}.jsonl"
        self.assertNotIn(b"matter", of_redact.archive_filter(rel, TARGET + b"\n", self.entries([U1])))

    def test_a_hashed_line_with_a_nested_id_the_store_masked_is_marked(self):
        # of_review on organic-framework PR 488 (probe archive_register_filter.py): the store hashed the line after
        # redact_keys, and a nested id once kept that hash from being tried
        rec = {"type": "response_item", "payload": {"id": "msg_2", "text": "the matter; db postgres://app:" + "hunter2"
                                                                                + "@db.internal/x"}}
        line = json.dumps(rec).encode()
        stored = of_redact.of_store.redact_keys(line)
        self.assertNotEqual(stored, line)
        out = of_redact.archive_filter(f"p/{S}.jsonl", line + b"\n", self.entries([hashlib.sha256(stored).hexdigest()]))
        self.assertNotIn(b"matter", out)


class ArchiveRegisterWriterTest(unittest.TestCase):
    def setUp(self):
        import sqlite3
        self.dir = tempfile.mkdtemp()
        self.conn = sqlite3.connect(os.path.join(self.dir, "of.db"))
        self.conn.execute("CREATE TABLE event (event_id TEXT PRIMARY KEY, ts TEXT, name TEXT, payload TEXT)")
        self.ask = "01a1f8a0-1111-7000-8000-000000000001"
        self.conn.execute("INSERT INTO event VALUES (?, '2026-11-20T10:00:00Z', 'of.agent_question', ?)",
                          (self.ask, json.dumps({"ref": "OF-170", "text": "replace it?"})))
        self.path = os.path.join(self.dir, "redaction", "archive-register.jsonl")
        self.emitted = []
        patch = mock.patch.object(of_redact.of_events, "emit", lambda name, payload, **k: self.emitted.append(
            (name, payload)))
        patch.start()
        self.addCleanup(patch.stop)

    def answer(self, grouped=False):
        payload = {"ask_ids": ["x", self.ask]} if grouped else {"ask_id": self.ask}
        self.conn.execute("INSERT INTO event VALUES ('01a1f8a0-2222-7000-8000-000000000002', '2026-11-20T10:01:00Z', "
                          "'of.agent_question_answered', ?)", (json.dumps(payload),))

    def add(self, ask, **kw):
        return of_redact.archive_register_add(self.conn, kw.get("session", S), kw.get("keys", [U1]), MARK, "test", ask,
                                              path=self.path)

    def test_an_answered_card_adds_an_entry_and_an_event_without_a_record(self):
        self.answer()
        e = self.add("OF-170")
        self.assertEqual((e["ask"], e["ask_id"], e["keys"]), ("OF-170", self.ask, [U1]))
        self.assertEqual(of_redact.archive_register(self.path)[0]["session"], S)
        self.assertEqual(oct(os.stat(self.path).st_mode & 0o777), "0o600")
        name, payload = self.emitted[0]
        self.assertEqual((name, payload["table"], payload["count"], payload["ask"]), ("redacted", "archive_register",
                                                                                     1, "OF-170"))
        self.assertNotIn(U1, json.dumps(payload))

    def test_an_ask_answered_in_a_group_counts_and_an_event_id_names_it(self):
        self.answer(grouped=True)
        self.assertEqual(self.add(self.ask[:13])["ask_id"], self.ask)

    def test_no_card_an_unknown_one_or_one_unanswered_adds_nothing(self):
        for ask in (None, "the founder said so", "OF-999", "OF-170"):
            with self.assertRaises(ValueError, msg=ask):
                self.add(ask)
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(self.emitted, [])

    def test_a_register_that_does_not_read_is_never_appended_to(self):
        self.answer()
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as fh:
            fh.write("not json\n")
        with self.assertRaises(of_redact.RegisterError):
            self.add("OF-170")
        self.assertEqual(open(self.path).read(), "not json\n")


if __name__ == "__main__":
    unittest.main()
