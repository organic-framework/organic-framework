"""How of ask, the pulse and the listener read an organization's Benchfile.lock before anything of Bench
(bin/of_asks_package.py, _lock_text_names_it): a regular file reached without a link, opened without waiting. The
folder holds what is pushed to the organization's repository, so a link or a FIFO committed under that name must
never hold `of ask` or the listener, nor make them read what it names (of_review on organic-framework PR 574). Needs
no Bench: it runs everywhere."""

import os
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import of_asks_package  # noqa: E402

NAMED = '{ ["of-asks"] = { version = "1.0.0" } }\n'


class LockReadTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.lock = os.path.join(self.d, of_asks_package.LOCK)

    def names_it(self):
        return of_asks_package._lock_text_names_it(self.d)

    def test_a_plain_lock_that_names_it_reads_true(self):
        with open(self.lock, "w") as f:
            f.write(NAMED)
        self.assertTrue(self.names_it())

    def test_no_lock_or_one_without_it_reads_false(self):
        self.assertFalse(self.names_it())
        with open(self.lock, "w") as f:
            f.write('{ ["other"] = { version = "1.0.0" } }\n')
        self.assertFalse(self.names_it())

    def test_a_link_is_not_followed(self):
        target = os.path.join(tempfile.mkdtemp(), "elsewhere")
        with open(target, "w") as f:
            f.write(NAMED)
        os.symlink(target, self.lock)
        self.assertFalse(self.names_it())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "no FIFOs here")
    def test_a_fifo_no_one_writes_is_not_waited_on(self):
        os.mkfifo(self.lock)
        out = []
        t = threading.Thread(target=lambda: out.append(self.names_it()), daemon=True)
        t.start()
        t.join(5)
        self.assertFalse(t.is_alive(), "the read waited on a FIFO")
        self.assertEqual(out, [False])

    def test_a_folder_under_that_name_reads_false(self):
        os.mkdir(self.lock)
        self.assertFalse(self.names_it())

    def test_without_the_package_before_file_passes_without_bench(self):
        target = os.path.join(tempfile.mkdtemp(), "elsewhere")
        with open(target, "w") as f:
            f.write(NAMED)
        os.symlink(target, self.lock)
        self.assertEqual(of_asks_package.before_file(self.d, {"question": "q"}),
                         {"decision": "pass", "by": None})


if __name__ == "__main__":
    unittest.main()
