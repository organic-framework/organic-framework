"""paste_source ps-v0 (of_paste): matching a pasted body to another session's assistant output."""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_events  # noqa: E402
import of_paste  # noqa: E402
import of_store  # noqa: E402

SOURCE = ("**Готово.** Нашёл отклонённый код в журнале Claude и создал обёртку по нужному пути.\n\n"
          "- Проверил подключение: база `marketplace` доступна, режим чтения включён, таймаут 30 с.\n"
          "- Дальше нужен перезапуск пода, это несколько минут без ноды.")


class PasteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = self.tmp.name
        of_events._CONN.clear()
        self.conn = of_store.connect(os.path.join(self.tmp.name, "of.db"))

    def tearDown(self):
        of_events._CONN.clear()
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def add_record(self, sid, ts, text, uuid_="u1"):
        raw = json.dumps({"type": "assistant", "uuid": uuid_, "sessionId": sid, "timestamp": ts,
                          "message": {"content": [{"type": "text", "text": text}]}}).encode()
        sha = of_store.put_blob(self.conn, raw)
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(transcript_record)")]
        row = {"source_path": "/x", "line_offset": 0, "session_id": sid, "record_uuid": uuid_, "type": "assistant",
               "timestamp": ts, "harness": "claude-code", "blob_sha256": sha}
        keys = [c for c in cols if c in row]
        self.conn.execute(f"INSERT INTO transcript_record ({','.join(keys)}) VALUES ({','.join('?' * len(keys))})",
                          [row[k] for k in keys])

    def test_rendered_paste_finds_its_source(self):
        self.add_record("S-src", "2026-11-06T09:00:00Z", SOURCE)
        self.add_record("S-other", "2026-11-06T09:10:00Z", "Совсем другой текст про бэкапы и окна обслуживания. " * 4, "u2")
        rendered = ("⏺ Готово. Нашёл отклонённый код в журнале Claude и создал обёртку по нужному\n  пути.\n\n"
                    "  • Проверил подключение: база marketplace доступна, режим чтения включён, таймаут 30 с.\n"
                    "  • Дальше нужен перезапуск пода, это несколько минут без ноды.")
        c = of_paste.Corpus(self.conn)
        sid, ruuid, score, n = of_paste.find_source(c, rendered, "2026-11-06T09:30:00Z", "S-recv")
        self.assertEqual((sid, ruuid), ("S-src", "u1"))
        self.assertGreaterEqual(score, of_paste.FLOOR)
        self.assertEqual(of_paste.find_source(c, rendered, "2026-11-06T09:30:00Z", "S-src")[0], None)  # not itself
        self.assertEqual(of_paste.find_source(c, rendered, "2026-11-08T09:30:00Z", "S-recv")[0], None)  # > 24 h

    def test_frames_are_not_pastes(self):
        self.assertTrue(of_paste.framed("Message from mt:x (via relay, authority: none; …)\nhi"))
        self.assertTrue(of_paste.framed("Answer from the founder to your question 1234: yes"))
        self.assertFalse(of_paste.framed("Готово. Нашёл отклонённый код"))
        self.assertEqual(of_paste.pasted_body('<pasted_content id="1">\nbody\n</pasted_content id="1">'), "body")
