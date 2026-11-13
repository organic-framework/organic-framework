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


if __name__ == "__main__":
    unittest.main()
