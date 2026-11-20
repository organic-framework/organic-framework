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

    def register(self, *entries):
        d = os.path.join(self.state, "redaction")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "archive-register.jsonl"), "w") as fh:
            for e in entries:
                fh.write(json.dumps(e) + "\n")

    def test_a_registered_record_is_stored_with_its_mark_whole_and_appended(self):
        """of_server on redaction 7da5d129 (the founder's yes on card OF-170): a session moved to another account gives
        the archive a new file, copied whole with the matter; and an append is one more frame."""
        mark = "[redacted: test]"
        self.register({"session": "s1", "keys": ["u1", "u3"], "mark": mark, "reason": "test", "basis": "test"})
        self.put("s1.jsonl", b'{"uuid": "u1", "type": "user", "message": {"role": "user", "content": "the matter"}}\n'
                             b'{"uuid": "u2", "type": "user", "message": {"role": "user", "content": "kept"}}\n')
        self.run_()
        got = self.copy("s1.jsonl")
        self.assertNotIn(b"the matter", got)
        self.assertIn(mark.encode(), got)
        self.assertIn(b"kept", got)
        once = got.count(mark.encode())
        self.put("s1.jsonl", b'{"uuid": "u3", "type": "user", "message": {"role": "user", "content": "more matter"}}\n',
                 mode="a")
        out = self.run_()
        self.assertIn("1 grown", out[-1])
        got = self.copy("s1.jsonl")
        self.assertNotIn(b"more matter", got)
        self.assertEqual(got.count(mark.encode()), 2 * once)          # the appended record marked as the first

    def test_a_register_that_does_not_read_skips_the_file(self):
        d = os.path.join(self.state, "redaction")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "archive-register.jsonl"), "w") as fh:
            fh.write("not an entry\n")
        self.put("s2.jsonl", b'{"uuid": "u9"}\n')
        out = self.run_()
        self.assertTrue(any(o.startswith("skip ") for o in out), out)
        self.assertFalse(os.path.exists(os.path.join(self.state, "raw", "files", ".claude-x", "projects", "p",
                                                     "s2.jsonl.zst")))

    def test_one_run_at_a_time(self):
        import fcntl
        self.put("c.jsonl", b"c\n")
        os.makedirs(os.path.join(self.state, "raw", "files"), exist_ok=True)
        with open(os.path.join(self.state, "raw", "archive.lock"), "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            self.assertEqual(self.run_(), ["another of archive is running"])


if __name__ == "__main__":
    unittest.main()
