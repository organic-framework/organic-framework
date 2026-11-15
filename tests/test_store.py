"""The local store (bin/of_store.py): connecting stays cheap while another process writes."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
class ConnectBehindAWriterTest(unittest.TestCase):
    """of_asks msg 01a1d67e-8752, 2026-11-13: every process's first connect wrote the schema mark and waited out the
    busy timeout behind the listener's write transaction, 10 s with no CPU."""

    def test_a_migrated_store_connects_without_writing(self):
        import tempfile
        import time as _t
        import of_store as s
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "of.db")
            s.connect(path).close()
            writer = s.connect(path)
            writer.execute("BEGIN IMMEDIATE")  # the listener mid-batch
            try:
                s._MIGRATED.clear()  # a new process
                t0 = _t.monotonic()
                conn = s.connect(path, timeout=2.0)
                self.assertLess(_t.monotonic() - t0, 1.0)
                self.assertTrue(s.has_usage_columns(conn))
                conn.close()
            finally:
                writer.execute("ROLLBACK")
                writer.close()


class IngestCapTest(unittest.TestCase):
    """of health, 2026-11-14: 24 to 27% of hook calls spooled while one ingest transaction held the write lock up to
    29 s. A transaction ends after the record that passes TX_CAP_S, its cursor at that record."""

    def test_every_record_once_and_the_cursor_where_a_stop_left_it(self):
        import json
        import tempfile
        from unittest import mock
        import of_store as s
        with tempfile.TemporaryDirectory() as d:
            conn = s.connect(os.path.join(d, "of.db"))
            path = os.path.join(d, "abc.jsonl")
            with open(path, "w") as fh:
                for i in range(12):
                    fh.write(json.dumps({"type": "user", "uuid": f"u{i}", "sessionId": "S", "n": i}) + "\n")
            commits = []
            real = s._insert_capped

            def capped(conn_, batch, j, usage_cols, t0, cap=None):
                commits.append(j)
                if len(commits) == 4:
                    raise KeyboardInterrupt  # the worker stopped in the 4th transaction
                return real(conn_, batch, j, usage_cols, t0, cap=0)  # one record per transaction
            with mock.patch.object(s, "_insert_capped", capped), mock.patch.object(s, "TX_YIELD_S", 0):
                with self.assertRaises(KeyboardInterrupt):
                    s.ingest_file(conn, path, "claude-code")
            self.assertEqual(commits, [0, 1, 2, 3])
            self.assertEqual(conn.execute("SELECT count(*) FROM transcript_record").fetchone()[0], 3)
            with open(path, "rb") as fh:
                third_end = len(b"".join(fh.readlines()[:3]))
            self.assertEqual(conn.execute("SELECT offset FROM ingest_cursor").fetchone()[0], third_end)
            with mock.patch.object(s, "TX_YIELD_S", 0):
                s.ingest_file(conn, path, "claude-code")  # the rest, from the cursor
            self.assertEqual(conn.execute("SELECT count(*) FROM transcript_record").fetchone()[0], 12)
            self.assertEqual(conn.execute("SELECT offset FROM ingest_cursor").fetchone()[0], os.path.getsize(path))
            conn.close()


if __name__ == "__main__":
    unittest.main()


class LongLockLineTest(unittest.TestCase):
    """of_server, 2026-11-15: a scratch store's 155 s write lock was logged in the live hook-errors.log with no store
    named, and read as the live store's. The line names the database, and marks one that is not this machine's."""

    def test_the_line_names_the_store_it_held(self):
        import tempfile
        import time
        from unittest import mock
        import of_events
        import of_store
        with tempfile.TemporaryDirectory() as root:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = root
            try:
                os.makedirs(os.path.join(root, "scratch"))
                live = of_store.connect(os.path.join(root, "of.db"))
                other = of_store.connect(os.path.join(root, "scratch", "merge.db"))
                real = os.path.realpath(root)                                    # SQLite names the file by its real path
                with mock.patch.dict(os.environ, {"OF_DB": os.path.join(root, "of.db")}):
                    for conn in (live, other):
                        of_store.warn_long(time.monotonic() - of_store.LONG_TX_S - 1, conn=conn)
                    of_store.warn_long(time.monotonic() - of_store.LONG_TX_S - 1)
                with open(os.path.join(root, "hook-errors.log")) as fh:
                    lines = fh.read().splitlines()
                self.assertIn("held the write lock", lines[0])
                self.assertIn(f" in {os.path.join(real, 'of.db')}: ", lines[0])
                self.assertNotIn("not this machine's store", lines[0])
                self.assertIn(f" in {os.path.join(real, 'scratch', 'merge.db')} (not this machine's store): ", lines[1])
                self.assertRegex(lines[2], r"held the write lock [\d.]+ s: ")                # no store given: as before
                live.close()
                other.close()
            finally:
                of_events.STATE_DIR = saved
