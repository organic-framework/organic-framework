"""The raw archive's register of content redactions (of_server, 2026-11-20; hand-over 7da5d129, card OF-170): a
registered session's records are stored with their mark in every raw copy, whatever copy the archive makes."""

import hashlib
import json
import os
import sys
import tempfile
import unittest

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
        p = register({"session": session, "keys": keys, "mark": MARK, "reason": "test", "basis": "test"})
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
        for bad in ("not json\n", json.dumps({"session": S, "keys": [], "mark": MARK}) + "\n",
                    json.dumps({"session": S, "keys": [U1]}) + "\n"):
            fd, p = tempfile.mkstemp()
            with os.fdopen(fd, "w") as fh:
                fh.write(bad)
            self.addCleanup(os.unlink, p)
            with self.assertRaises(of_redact.RegisterError):
                of_redact.archive_register(p)
        self.assertTrue(issubclass(of_redact.RegisterError, OSError))   # of_archive skips the file on an OSError


if __name__ == "__main__":
    unittest.main()
