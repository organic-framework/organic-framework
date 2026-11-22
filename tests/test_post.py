"""`of post` (bin/of_post.py): a request to the posting unit as its interface v1 reads it (of_server, hand-over
7d270323, message 01a20553-b1bd), the door it goes through, and the result it waits for."""

import json
import os
import stat
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))

import of_post  # noqa: E402
import of_spawn  # noqa: E402

HEAD = "a" * 40


class PostTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = os.path.realpath(self.tmp.name)
        self.door = os.path.join(self.d, "door")
        os.makedirs(os.path.join(self.door, "requests"))
        open(os.path.join(self.door, "signal"), "w").close()
        self.results = os.path.join(self.d, "results")
        os.makedirs(os.path.join(self.results, "u"))

    def test_the_request_is_what_the_unit_reads(self):
        r = of_post.request("review", 12, "Looks right.\n", "of_review", "S1", commit=HEAD)
        self.assertEqual(r, {"v": 1, "kind": "review", "repo": "organic-framework/organic-framework", "pr": 12,
                             "commit": HEAD, "body": "Looks right.\n", "position": "of_review", "session": "S1"})
        for kw, said in ((dict(kind="review", commit=None), "head commit"), (dict(kind="note"), "comment or review"),
                         (dict(kind="comment", body=" "), "empty"), (dict(kind="comment", body="x" * 140000), "KiB"),
                         (dict(kind="comment", commit="abc"), "40 hex")):
            args = dict(kind="comment", pr=12, body="b", position="p", session="s")
            args.update(kw)
            with self.assertRaisesRegex(of_post.PostError, said):
                of_post.request(args.pop("kind"), args.pop("pr"), args.pop("body"), args.pop("position"),
                                args.pop("session"), **args)

    def test_a_request_is_a_new_file_the_signal_is_touched_and_the_result_read(self):
        sig = os.path.join(self.door, "signal")
        os.utime(sig, (0, 0))
        req = of_post.request("comment", 7, "A note.\n", "of_platform", "S2")

        def unit(_):   # the unit answers once the request is there
            [name] = os.listdir(os.path.join(self.door, "requests"))
            p = os.path.join(self.door, "requests", name)
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o640)
            self.assertEqual(json.load(open(p)), req)
            with open(os.path.join(self.results, "u", name), "w") as fh:
                json.dump({"url": "https://example.invalid/c/1"}, fh)
        state, res = of_post.submit(req, self.door, "sessions", wait_s=5, results=self.results, user="u", sleep=unit)
        self.assertEqual((state, res["url"]), ("posted", "https://example.invalid/c/1"))
        self.assertGreater(os.stat(sig).st_mtime, 0)

    def test_a_review_only_through_the_reviewers_door_and_no_result_is_said(self):
        req = of_post.request("review", 7, "Read whole.\n", "of_review", "S3", commit=HEAD)
        with self.assertRaisesRegex(of_post.PostError, "reviewers' door"):
            of_post.submit(req, self.door, "sessions", wait_s=0, results=self.results, user="u")
        state, res = of_post.submit(of_post.request("comment", 7, "x\n", "p", "s"), self.door, "sessions", wait_s=0,
                                    results=self.results, user="u", sleep=lambda s: None)
        self.assertEqual(state, "waiting")
        home = os.path.join(self.d, "home")
        os.makedirs(os.path.join(home, ".local", "state", "of", "gh-post", "requests"))
        import grp
        mine = grp.getgrgid(os.getgid()).gr_name
        self.assertEqual(of_post.door(home, group=mine)[1], "reviewers")          # of the reviewers' group
        self.assertEqual(of_post.door(home, group="no-such-group-here")[1], "sessions")   # a folder by name alone
        self.assertEqual(of_post.door(os.path.join(self.d, "nobody")), (of_post.SESSIONS_DOOR, "sessions"))

    def test_no_signal_no_request_left_behind(self):
        """of_review on PR 576: a door with no unit behind it keeps nothing a later unit would post."""
        os.remove(os.path.join(self.door, "signal"))
        with self.assertRaisesRegex(of_post.PostError, "nothing was written"):
            of_post.submit(of_post.request("comment", 7, "x\n", "p", "s"), self.door, "sessions", wait_s=0,
                           results=self.results, user="u")
        self.assertEqual(os.listdir(os.path.join(self.door, "requests")), [])

    def test_the_sandbox_may_write_the_doors_requests_and_signal_only(self):
        self.assertEqual(of_spawn.post_door(self.door), [os.path.join(self.door, "requests"),
                                                         os.path.join(self.door, "signal")])
        self.assertEqual(of_spawn.post_door(os.path.join(self.d, "absent")), [])


if __name__ == "__main__":
    unittest.main()
