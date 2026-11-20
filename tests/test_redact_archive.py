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
import of_asks_answer  # noqa: E402
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
        return of_redact._register_lines(p)   # the filter's tests: entries as read; the cards are checked below

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
        self.conn.execute("CREATE TABLE event (event_id TEXT PRIMARY KEY, ts TEXT, name TEXT, payload TEXT, "
                          "position TEXT, host_id TEXT)")
        self.ask = "01a1f8a0-1111-7000-8000-000000000001"
        self.card("OF-170", self.ask)
        self.path = os.path.join(self.dir, "redaction", "archive-register.jsonl")
        self.emitted = []
        for patch in (mock.patch.object(of_redact.of_events, "emit", lambda name, payload, **k: self.emitted.append(
                (name, payload))), mock.patch.object(of_asks_answer, "not_the_owner", lambda: None)):
            patch.start()
            self.addCleanup(patch.stop)

    def card(self, ref, eid, text="Replace the matter's records with a mark?"):
        self.conn.execute("INSERT INTO event VALUES (?, '2026-11-20T10:00:00Z', 'of.agent_question', ?, 'of_server', 'm')",
                          (eid, json.dumps({"ref": ref, "questions": [{"question": text,
                                                                       "options": ["yes, replace them", "no"]}]})))

    def answer(self, grouped=False, answer="yes, replace them", by="owner", ask=None, n=[0]):
        ask = ask or self.ask
        n[0] += 1
        payload = {"ask_ids": ["x", ask]} if grouped else {"ask_id": ask}
        payload.update(answer=answer, by=by, via="asks-ui")
        self.conn.execute("INSERT INTO event VALUES (?, '2026-11-20T10:01:00Z', 'of.agent_question_answered', ?, "
                          "'founder', 'm')", (f"01a1f8a0-2222-7000-8000-{n[0]:012d}", json.dumps(payload)))

    def add(self, ask, **kw):
        return of_redact.archive_register_add(self.conn, kw.get("session", S), kw.get("keys", [U1]), MARK, "test", ask,
                                              path=self.path)

    def test_an_answered_card_adds_an_entry_and_an_event_without_a_record(self):
        self.answer()
        e = self.add("OF-170")
        self.assertEqual((e["ask"], e["ask_id"], e["keys"]), ("OF-170", self.ask, [U1]))
        self.assertEqual(of_redact.archive_register(self.path, conn=self.conn)[0]["session"], S)
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

    def test_a_no_or_someone_elses_answer_is_no_yes(self):
        # of_review and of_release on organic-framework PR 488 (probe archive_register_writers.py): an unrelated card
        # answered no had added an entry
        self.answer(answer="no")
        self.answer(answer="yes, replace them", by="of_conductor")
        with self.assertRaises(ValueError):
            self.add("OF-170")
        self.assertFalse(os.path.exists(self.path))

    def test_only_the_owner_in_a_terminal_writes(self):
        self.answer()
        with mock.patch.object(of_asks_answer, "not_the_owner", lambda: "this runs in an agent's session"):
            with self.assertRaises(PermissionError):
                self.add("OF-170")
        self.assertFalse(os.path.exists(self.path))

    def test_the_reader_keeps_an_approved_entry_and_drops_one_written_by_hand(self):
        self.answer()
        self.add("OF-170")
        self.card("OF-171", "01a1f8a0-3333-7000-8000-000000000003")   # asked, never answered yes
        with open(self.path, "a") as fh:                                 # what G38 keeps agents from: a line by hand
            fh.write(json.dumps({"session": OTHER, "keys": [U2], "mark": MARK, "reason": "x", "ask": "OF-171"}) + "\n")
            fh.write(json.dumps({"session": OTHER, "keys": [U3], "mark": MARK, "reason": "x", "ask": "OF-1"}) + "\n")
        kept = of_redact.archive_register(self.path, conn=self.conn)
        self.assertEqual([e["session"] for e in kept], [S])
        data = KEEP + b"\n"
        self.assertIs(of_redact.archive_filter(f"p/{OTHER}.jsonl", data, kept), data)

    def test_a_store_that_cannot_be_read_refuses_the_register(self):
        self.answer()
        self.add("OF-170")
        bad = os.path.join(self.dir, "not-a-store.db")
        with open(bad, "w") as fh:
            fh.write("this is no database")
        with mock.patch.object(of_redact.of_store, "db_path", lambda: bad):
            with self.assertRaises(of_redact.RegisterError):
                of_redact.archive_register(self.path)

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
