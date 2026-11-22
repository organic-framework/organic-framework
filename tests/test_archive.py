"""of archive, continuous (bin/of_archive.py; docs/storage.md, "Raw tier: every byte"; hand-over b097ba7c from
of_architect: from 2026-11-05 to 11-16 the raw tier took no copy after its first run)."""

import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))

import of_archive  # noqa: E402
import of_events  # noqa: E402


@unittest.skipIf(of_archive.zstd is None, "needs compression.zstd (Python 3.14+)")
class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.state = os.path.join(self.tmp.name, "state")
        self.saved = (of_archive.HOME, of_events.STATE_DIR, of_events.emit)
        of_archive.HOME, of_events.STATE_DIR = self.home, self.state
        self.emitted = []
        of_events.emit = lambda name, payload, **k: self.emitted.append((name, payload))
        self.proj = os.path.join(self.home, ".claude-x", "projects", "p")
        os.makedirs(self.proj)

    def tearDown(self):
        of_archive.HOME, of_events.STATE_DIR, of_events.emit = self.saved
        self.tmp.cleanup()

    def put(self, name, data, mode="w"):
        p = os.path.join(self.proj, name)
        with open(p, mode + "b") as fh:
            fh.write(data)
        os.utime(p, (time.time(), time.time() + len(data)))   # a new mtime each write, even within one second
        return p

    def copy(self, name):
        with open(os.path.join(self.state, "raw", "files", ".claude-x", "projects", "p", name + ".zst"), "rb") as fh:
            return of_archive.zstd.decompress(fh.read())

    def run_(self, **kw):
        out = []
        of_archive.archive(log=out.append, **kw)
        return out

    def test_new_grown_and_rewritten_files_and_nothing_dropped(self):
        a = self.put("a.jsonl", b'{"n": 1}\n')
        self.run_()
        self.assertEqual(self.copy("a.jsonl"), b'{"n": 1}\n')
        self.put("a.jsonl", b'{"n": 2}\n', mode="a")                     # appended: one more frame
        size = os.path.getsize(os.path.join(self.state, "raw", "files", ".claude-x", "projects", "p", "a.jsonl.zst"))
        out = self.run_()
        self.assertIn("1 grown", out[-1])
        self.assertEqual(self.copy("a.jsonl"), b'{"n": 1}\n{"n": 2}\n')   # frames decompress to the whole file
        self.assertGreater(os.path.getsize(os.path.join(self.state, "raw", "files", ".claude-x", "projects", "p",
                                                        "a.jsonl.zst")), size)
        self.assertEqual(self.run_(quiet=True), [])                        # unchanged: nothing, silently
        before = json.load(open(os.path.join(self.state, "raw", "files.json")))[".claude-x/projects/p/a.jsonl"]
        self.put("a.jsonl", b'{"rewritten": true}\n')                       # not an append: copied whole
        out = self.run_()
        self.assertIn("1 changed", out[-1])
        self.assertEqual(self.copy("a.jsonl"), b'{"rewritten": true}\n')
        kept = os.path.join(self.state, "raw", "files", ".claude-x", "projects", "p", f"a.jsonl.{before[1]}.zst")
        with open(kept, "rb") as fh:                                        # the earlier copy is kept
            self.assertEqual(of_archive.zstd.decompress(fh.read()), b'{"n": 1}\n{"n": 2}\n')
        self.assertEqual(self.emitted[-1][0], "raw_archived")
        self.assertTrue(os.path.exists(a))

    def test_a_change_past_the_head_then_an_append_is_copied_whole(self):
        """of_review on PR 246: a change at offset 8000, then an append, was taken for an append by the head alone; the
        copy then matched no version of the file."""
        body = b"a" * 9000 + b"\n"
        self.put("m.jsonl", body)
        self.run_()
        changed = body[:7900] + b"B" * 200 + body[8100:] + b"tail\n"   # within the last 4 KB before the old end
        self.put("m.jsonl", changed)
        out = self.run_()
        self.assertIn("1 changed", out[-1])
        self.assertEqual(self.copy("m.jsonl"), changed)                    # the copy is the file, not a mix

    def test_a_file_whose_time_alone_moved_is_not_copied_again(self):
        """2026-11-21 (of_capacity): live sessions' transcripts got a new mtime every hour with no new byte, and each
        hour kept one more whole copy: 4,273 copies, 23.5 GiB, each the size of the next."""
        d = os.path.join(self.state, "raw", "files", ".claude-x", "projects", "p")
        body = b'{"n": 1}\n' * 2000                                         # past both 4 KB spans
        t = self.put("t.jsonl", body)
        self.run_()
        for hour in (1, 2):
            os.utime(t, (time.time(), time.time() + 3600 * hour))         # the same bytes, a new time
            out = self.run_()
            self.assertIn("archived 0 files", out[-1])
            self.assertIn("1 with only a new time, not copied", out[-1])
            self.assertEqual((self.emitted[-1][0], self.emitted[-1][1]["files"], self.emitted[-1][1]["touched"]),
                             ("raw_archived", 0, 1))                       # recorded with nothing else copied
        self.assertEqual(sorted(os.listdir(d)), ["t.jsonl.zst"])           # no kept copy
        self.assertEqual(self.copy("t.jsonl"), body)
        index = json.load(open(os.path.join(self.state, "raw", "files.json")))[".claude-x/projects/p/t.jsonl"]
        self.assertEqual(index[:2], [len(body), int(os.stat(t).st_mtime)])  # the index took the new time
        self.assertEqual(self.run_(quiet=True), [])
        self.put("t.jsonl", b"X" + body[1:])                               # the same size, its head changed
        self.assertIn("1 changed", self.run_()[-1])
        self.assertEqual(self.copy("t.jsonl"), b"X" + body[1:])
        self.put("t.jsonl", b'{"n": 2}\n', mode="a")                       # then appended to: still an append
        self.assertIn("1 grown", self.run_()[-1])

    def test_a_same_length_edit_in_a_memory_file_is_copied(self):
        """of_review on PR 499: a folder memory (projects/<p>/memory/) can take a same-length edit between its two 4 KB
        spans; only a transcript's same size with a new time counts as touched."""
        body = bytearray(b"".join(b"line %05d: reviewed on 2026-11-20, verdict approve\n" % i for i in range(240)))
        os.makedirs(os.path.join(self.proj, "memory"))
        self.put("memory/notes.md", bytes(body))
        self.run_()
        i = body.index(b"2026-11-20, verdict approve", len(body) // 2)
        body[i:i + 27] = b"2026-11-21, verdict changes"                     # size, first and last 4 KB as they were
        p = self.put("memory/notes.md", bytes(body))
        os.utime(p, (time.time(), time.time() + 3600))                     # a new time, as an edit gives it
        self.assertIn("1 changed", self.run_()[-1])
        self.assertEqual(self.copy("memory/notes.md"), bytes(body))

    def test_kept_copies_identical_to_the_copy_after_them_are_dropped_and_no_other(self):
        """2026-11-21 (of_capacity, message 01a1fc2a-8d57): 4,273 kept copies were the size of the copy after them."""
        d = os.path.join(self.state, "raw", "files", ".claude-x", "projects", "p")
        os.makedirs(d)
        z = of_archive.zstd.compress

        def put(name, data):
            with open(os.path.join(d, name), "wb") as fh:
                fh.write(data)

        put("t.jsonl.100.zst", z(b"one\n"))                       # differs from the next: stays
        put("t.jsonl.200.zst", z(b"two\n"))                       # the same as the next: dropped
        put("t.jsonl.300.zst", z(b"two\n"))                       # the same as the current: dropped
        put("t.jsonl.zst", z(b"two\n"))
        put("u.jsonl.5.zst", z(b"same\n"))                        # a kept copy of a file with no current copy: stays
        put("v.jsonl.7.zst", z(b"v") + z(b"\n"))                  # the same content in other bytes: stays
        put("v.jsonl.6.zst", z(b"v\n"))                           # differs from the next in its bytes: stays
        put("v.jsonl.zst", z(b"v\n"))
        put("w.jsonl.123.zst", z(b"w\n"))                         # a source named so beside w.jsonl: its own copy
        put("w.jsonl.zst", z(b"w\n"))                             # (of_review on PR 502), never a kept one
        out = []
        of_archive.drop_identical(dry_run=True, log=out.append)
        self.assertIn("nothing dropped", out[-1])                  # no index: a kept copy cannot be told, none goes
        rel = ".claude-x/projects/p/"
        with open(os.path.join(self.state, "raw", "files.json"), "w") as fh:
            json.dump({rel + n: [1, 1, "", ""] for n in ("t.jsonl", "u.jsonl", "v.jsonl", "w.jsonl", "w.jsonl.123")}, fh)
        out = []
        of_archive.drop_identical(dry_run=True, log=out.append)
        self.assertIn("would drop 2 kept copies", out[-1])
        self.assertEqual(len(os.listdir(d)), 10)                   # a dry run deletes nothing
        out = []
        of_archive.drop_identical(log=out.append)
        self.assertIn("dropped 2 kept copies", out[-1])
        self.assertEqual(sorted(os.listdir(d)), ["t.jsonl.100.zst", "t.jsonl.zst", "u.jsonl.5.zst", "v.jsonl.6.zst",
                                                 "v.jsonl.7.zst", "v.jsonl.zst", "w.jsonl.123.zst", "w.jsonl.zst"])
        self.assertEqual(self.copy("t.jsonl"), b"two\n")

    def test_new_files_first_within_a_budget_and_one_path(self):
        self.put("old.jsonl", b"x\n")
        self.run_()
        self.put("old.jsonl", b"y\n", mode="a")
        self.put("new.jsonl", b"z\n")
        todo = of_archive.plan(json.load(open(os.path.join(self.state, "raw", "files.json"))), of_archive.sources())
        self.assertEqual([k for *_, k in todo], ["new", "grown"])          # never copied goes first
        out = self.run_(budget_s=-1)                                        # the budget is spent before the first
        self.assertIn("2 left for the next run", out[-1])
        only = self.put("only.jsonl", b"o\n")
        out = self.run_(paths=[only])                                       # the hook's SessionEnd: that file alone
        self.assertIn("archived 1 files (1 new", out[-1])
        st = of_archive.stats()
        self.assertEqual(st["files"], 2)                                    # old.jsonl and only.jsonl
        self.assertTrue(st["newest"].endswith("Z"))

    def test_a_copy_from_before_the_head_hash_is_kept_when_copied_again(self):
        self.put("b.jsonl", b"first\n")
        root = os.path.join(self.state, "raw")
        os.makedirs(os.path.join(root, "files", ".claude-x", "projects", "p"))
        with open(os.path.join(root, "files", ".claude-x", "projects", "p", "b.jsonl.zst"), "wb") as fh:
            fh.write(of_archive.zstd.compress(b"first\n"))
        with open(os.path.join(root, "files.json"), "w") as fh:            # the 2026-11-05 index: size and mtime only
            json.dump({".claude-x/projects/p/b.jsonl": [1, 1]}, fh)   # nor the tail hash: copied whole
        self.put("b.jsonl", b"second\n", mode="a")
        self.run_()
        self.assertEqual(self.copy("b.jsonl"), b"first\nsecond\n")
        self.assertTrue(os.path.exists(os.path.join(root, "files", ".claude-x", "projects", "p", "b.jsonl.1.zst")))

    def test_one_run_at_a_time(self):
        import fcntl
        self.put("c.jsonl", b"c\n")
        os.makedirs(os.path.join(self.state, "raw", "files"), exist_ok=True)
        with open(os.path.join(self.state, "raw", "archive.lock"), "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            self.assertEqual(self.run_(), ["another of archive is running"])


if __name__ == "__main__":
    unittest.main()
