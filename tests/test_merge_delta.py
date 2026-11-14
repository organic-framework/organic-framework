"""B3 of the agent host move (bin/of_merge_delta.py): one machine's store merged into another's by id; a re-run adds
nothing; the delta's asks and hand-overs appear in the target's projections (lab-agent-host-v0 B3-1, B3-2)."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_asks  # noqa: E402
import of_events  # noqa: E402
import of_handover  # noqa: E402
import of_merge_delta  # noqa: E402
import of_store  # noqa: E402

CUT = "2026-11-15T12:00:00.000Z"


def rec(kind, payload, ts, position="acme_ops", host="laptop", session="S1"):
    r = of_events.record(kind, payload, position=position, session=session)
    r["timestamp"] = ts
    r["host.id"] = host
    if kind == "handover_proposed":
        r["of.payload"]["id"] = r["of.event.id"]
    return r


def ask(text, ts):
    return rec("agent_question", {"detected_by": "declared", "source": "declared", "from_position": "acme_ops",
                                  "to": "founder", "blocking": False, "topic_key": text[:20],
                                  "questions": [{"question": text, "options": ["yes", "no"]}]}, ts)


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src_path = os.path.join(self.tmp.name, "laptop", "of.db")
        self.dst_path = os.path.join(self.tmp.name, "host", "of.db")
        src, dst = of_store.connect(self.src_path), of_store.connect(self.dst_path)
        # before the cutover: on both machines already (B1 copied the state)
        self.before = ask("Run the old migration?", "2026-11-15T11:00:00.000Z")
        for c in (src, dst):
            of_store.add_event(c, self.before)
        # after it: only on the laptop
        self.after_ask = ask("Make p50 the target?", "2026-11-15T12:05:00.000Z")
        self.handover = rec("handover_proposed", {"from": "acme_ops", "to": "acme_db", "title": "Rotate the key",
                                                  "done_when": "the new key works"}, "2026-11-15T12:06:00.000Z")
        ref = of_store.add_hook_record(src, "UserPromptSubmit", "S1", b'{"prompt": "go"}')
        self.prompt = rec("human_prompt", {"origin": "human", "text": "go ahead with the rotation"},
                          "2026-11-15T12:07:00.000Z")
        for r in (self.after_ask, self.handover):
            of_store.add_event(src, r)
        of_store.add_event(src, self.prompt, raw_ref=ref)
        sha = of_store.put_blob(src, b'{"type": "user"}')
        for sid, off, uid, ts_ in (("S1", 0, "u-early", None),                  # S1's history before the cutover
                                   ("S1", 10, "u-old", "2026-11-15T11:30:00Z"),
                                   ("S1", 20, "u-1", "2026-11-15T12:07:01Z"),   # its first record after it
                                   ("S1", 30, "u-2", None),                     # undated, after it in the file
                                   ("S0", 0, "u-0", "2026-11-15T10:00:00Z"), ("S0", 10, "u-00", None)):  # ended before
            src.execute("INSERT INTO transcript_record (source_path, line_offset, blob_sha256, session_id, record_uuid, "
                        "timestamp) VALUES (?,?,?,?,?,?)", (f"/x/{sid}.jsonl", off, sha, sid, uid, ts_))
        # the host has hook records of its own, so the laptop's ids collide with them
        for i in range(3):
            of_store.add_hook_record(dst, "PreToolUse", "H1", f'{{"n": {i}}}'.encode())
        src.close()
        dst.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_merge_by_id_and_a_rerun_adds_nothing(self):
        first = of_merge_delta.merge(self.src_path, self.dst_path, since=CUT)
        self.assertEqual(first["event"], {"seen": 3, "added": 3})
        self.assertEqual(first["transcript_record"]["added"], 2)   # u-1 and u-2: not S1's history, none of S0's
        self.assertEqual(first["hook_record"]["added"], 1)
        again = of_merge_delta.merge(self.src_path, self.dst_path, since=CUT)
        self.assertEqual(sum(c["added"] for c in again.values()), 0, again)
        everything = of_merge_delta.merge(self.src_path, self.dst_path)   # no --since: the whole store, still idempotent
        self.assertEqual(everything["event"]["added"], 0)

    def test_the_delta_appears_in_the_projections(self):
        of_merge_delta.merge(self.src_path, self.dst_path, since=CUT)
        conn = of_store.connect(self.dst_path)
        try:
            asks = of_asks.project(of_asks.load(conn))
            texts = {a["text"] for a in asks if a["state"] == "open"}
            self.assertIn("Make p50 the target?", texts)
            self.assertIn("Run the old migration?", texts)
            hands = of_handover.project(of_handover.load(conn))
            self.assertEqual(hands[self.handover["of.event.id"]]["title"], "Rotate the key")
            # the prompt's raw_ref names the target's own copy of the hook record, not the laptop's id
            ref = conn.execute("SELECT raw_ref FROM event WHERE event_id = ?",
                               (self.prompt["of.event.id"],)).fetchone()[0]
            table, _, rid = ref.partition(":")
            row = conn.execute("SELECT hook_event, session_id FROM hook_record WHERE id = ?", (int(rid),)).fetchone()
            self.assertEqual((table, row), ("hook_record", ("UserPromptSubmit", "S1")))
            # full-text search finds the merged event
            hit = conn.execute("SELECT event_id FROM event_fts WHERE event_fts MATCH 'rotation'").fetchall()
            self.assertEqual([h[0] for h in hit], [self.prompt["of.event.id"]])
        finally:
            conn.close()

    def test_dry_run_writes_nothing(self):
        counts = of_merge_delta.merge(self.src_path, self.dst_path, since=CUT, dry_run=True)
        self.assertEqual(counts["event"]["added"], 3)
        conn = of_store.connect(self.dst_path)
        try:
            self.assertEqual(conn.execute("SELECT count(*) FROM event").fetchone()[0], 1)
        finally:
            conn.close()

    def test_the_source_is_read_only_and_refusals(self):
        before = os.path.getmtime(self.src_path)
        of_merge_delta.merge(self.src_path, self.dst_path, since=CUT)
        self.assertEqual(os.path.getmtime(self.src_path), before)
        with self.assertRaises(of_merge_delta.MergeError):
            of_merge_delta.merge(self.dst_path, self.dst_path)
        with self.assertRaises(of_merge_delta.MergeError):
            of_merge_delta.merge(os.path.join(self.tmp.name, "nowhere.db"), self.dst_path)

    def test_cli(self):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = of_merge_delta.main(["--source", self.src_path, "--target", self.dst_path, "--since", CUT, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["event"]["added"], 3)


if __name__ == "__main__":
    unittest.main()
