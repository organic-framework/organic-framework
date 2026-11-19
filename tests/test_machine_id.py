"""of_events.machine_id on a fresh state folder, many callers at once (of_review on organic-framework PR 409: 3 of 4
threads failed the O_EXCL create, and a reader could find the file empty)."""

import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_events  # noqa: E402


class MachineId(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = self.tmp.name

    def tearDown(self):
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def test_many_callers_get_one_id(self):
        got, errors = [], []

        def call():
            try:
                got.append(of_events.machine_id())
            except Exception as e:  # noqa: BLE001
                errors.append(e)
        threads = [threading.Thread(target=call) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(got)), 1)
        self.assertTrue(got[0])
        self.assertEqual(sorted(os.listdir(self.tmp.name)), ["machine_id"])   # no temporary file left
        self.assertEqual(of_events.machine_id(), got[0])

    def test_an_empty_file_an_older_writer_left_is_replaced_once(self):
        """of_review's note on PR 414: six processes that each replaced the empty file got two ids."""
        open(os.path.join(self.tmp.name, "machine_id"), "w").close()
        got = []
        threads = [threading.Thread(target=lambda: got.append(of_events.machine_id(wait_s=0.05))) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(set(got)), 1)
        self.assertTrue(got[0])
        self.assertEqual(of_events.machine_id(), got[0])
        self.assertFalse([n for n in os.listdir(self.tmp.name) if n.startswith(".machine_id.") and n != ".machine_id.lock"])


    def test_a_link_to_nothing_is_replaced_and_never_hangs(self):
        """of_review on PR 419 at 10ab82d: a dangling machine_id link made machine_id() spin forever."""
        import time
        os.symlink(os.path.join(self.tmp.name, "nowhere"), os.path.join(self.tmp.name, "machine_id"))
        start = time.monotonic()
        mid = of_events.machine_id(wait_s=0.05)
        self.assertLess(time.monotonic() - start, 2)
        self.assertTrue(mid)
        self.assertEqual(of_events.machine_id(), mid)
        self.assertFalse(os.path.islink(os.path.join(self.tmp.name, "machine_id")))

    def test_a_folder_no_caller_can_fix_gives_one_id_per_process(self):
        """of_review's note after PR 419: the fallback id was made per call; one process's events carry one host.id."""
        os.mkdir(os.path.join(self.tmp.name, "machine_id"))   # a folder in its place: no id can be written there
        of_events._FALLBACK_ID.clear()
        try:
            a, b = of_events.machine_id(wait_s=0.01), of_events.machine_id(wait_s=0.01)
        finally:
            of_events._FALLBACK_ID.clear()
        self.assertTrue(a)
        self.assertEqual(a, b)

if __name__ == "__main__":
    unittest.main()
