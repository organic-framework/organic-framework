"""B3 of the agent host move (bin/of_merge_delta.py): one organization's rows of one machine's store merged into
another's by id; a re-run adds nothing; the delta's asks and hand-overs appear in the target's projections
(lab-agent-host-v0 B3-1, B3-2); another organization's rows never cross; acts of a person cross only from a named host
(of_review and of_secrets-review on organic-framework PR 195)."""
import hashlib
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


def rec(kind, payload, ts, position="acme_ops", host="laptop", session="S1", org="ofb"):
    r = of_events.record(kind, payload, position=position, session=session, org=org)
    r["timestamp"] = ts
    r["host.id"] = host
    if kind == "handover_proposed":
        r["of.payload"]["id"] = r["of.event.id"]
    return r


def ask(text, ts, **kw):
    return rec("agent_question", {"detected_by": "declared", "source": "declared", "from_position": "acme_ops",
                                  "to": "founder", "blocking": False, "topic_key": text[:20],
                                  "questions": [{"question": text, "options": ["yes", "no"]}]}, ts, **kw)


def transcript(conn, sid, off, uid, ts, data=b'{"type": "user"}'):
    sha = of_store.put_blob(conn, data)
    conn.execute("INSERT INTO transcript_record (source_path, line_offset, blob_sha256, session_id, record_uuid, "
                 "timestamp) VALUES (?,?,?,?,?,?)", (f"/x/{sid}.jsonl", off, sha, sid, uid, ts))
    return sha


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src_path = os.path.join(self.tmp.name, "laptop", "of.db")
        self.dst_path = os.path.join(self.tmp.name, "host", "of.db")
        src, dst = of_store.connect(self.src_path), of_store.connect(self.dst_path)
        # the target's own organizations list and registries decide whose positions cross
        for org, names in (("ofb", ["acme_ops", "acme_db"]), ("userorg", ["zeta_ops"])):
            os.makedirs(os.path.join(self.tmp.name, org, "registry"))
            with open(os.path.join(self.tmp.name, org, "registry", "positions.yaml"), "w") as fh:
                fh.write("positions:\n" + "".join(f"  - {{name: {n}}}\n" for n in names))
        self.orgs = os.path.join(self.tmp.name, "orgs.yaml")
        with open(self.orgs, "w") as fh:
            fh.write("orgs:\n  - {name: ofb, path: ofb}\n  - {name: userorg, path: userorg}\n")
        self.before = ask("Run the old migration?", "2026-11-15T11:00:00.000Z")   # B1 copied it to the host already
        for c in (src, dst):
            of_store.add_event(c, self.before)
        self.after_ask = ask("Make p50 the target?", "2026-11-15T12:05:00.000Z")
        self.handover = rec("handover_proposed", {"from": "acme_ops", "to": "acme_db", "title": "Rotate the key",
                                                  "done_when": "the new key works"}, "2026-11-15T12:06:00.000Z")
        ref = of_store.add_hook_record(src, "UserPromptSubmit", "S1", b'{"prompt": "go"}')
        self.prompt = rec("human_prompt", {"origin": "human", "text": "go ahead with the rotation"},
                          "2026-11-15T12:07:00.000Z")
        for r in (self.after_ask, self.handover):
            of_store.add_event(src, r)
        of_store.add_event(src, self.prompt, raw_ref=ref)
        for sid, off, uid, ts_ in (("S1", 0, "u-early", None), ("S1", 10, "u-old", "2026-11-15T11:30:00Z"),
                                   ("S1", 20, "u-1", "2026-11-15T12:07:01Z"), ("S1", 30, "u-2", None),
                                   ("S0", 0, "u-0", "2026-11-15T10:00:00Z"), ("S0", 10, "u-00", None)):
            transcript(src, sid, off, uid, ts_)
        of_store.add_event(src, rec("session_start", {}, "2026-11-15T09:00:00.000Z", session="S0"))
        # another organization on the same laptop: its question, its owner's answer, its conversation
        self.other = ask("Rotate the exchange key?", "2026-11-15T12:10:00.000Z", position="zeta_ops", session="Z1",
                         org="userorg")
        self.other_answer = rec("agent_question_answered", {"ask_id": self.other["of.event.id"], "by": "owner",
                                                            "via": "asks-ui", "answer": "yes"},
                                "2026-11-15T12:11:00.000Z", position="zeta_ops", session="Z1", org="userorg")
        for r in (self.other, self.other_answer):
            of_store.add_event(src, r)
        self.other_sha = transcript(src, "Z1", 0, "z-1", "2026-11-15T12:10:30Z", b'{"text": "the other conversation"}')
        for i in range(3):   # the host has hook records of its own, so the laptop's ids collide with them
            of_store.add_hook_record(dst, "PreToolUse", "H1", f'{{"n": {i}}}'.encode())
        src.close()
        dst.close()

    def tearDown(self):
        self.tmp.cleanup()

    def merge(self, **kw):
        kw.setdefault("org", "ofb")
        kw.setdefault("since", CUT)
        kw.setdefault("orgs_file", self.orgs)
        return of_merge_delta.merge(self.src_path, self.dst_path, **kw)

    def ids(self):
        return [self.prompt["of.event.id"]]

    def test_merge_by_id_and_a_rerun_adds_nothing(self):
        first = self.merge(person_act_ids=self.ids())
        self.assertEqual((first["event"]["added"], first["event"]["other_org"]), (3, 2))
        self.assertEqual(first["transcript_record"]["added"], 2)   # u-1 and u-2: not S1's history, none of S0's
        self.assertEqual(first["hook_record"]["added"], 1)
        again = self.merge(person_act_ids=self.ids())
        self.assertEqual(sum(c["added"] for t, c in again.items() if t not in ("person_acts", "held_refs")), 0, again)
        self.assertEqual(again["person_acts"], [])
        everything = self.merge(since=None, person_act_ids=self.ids())   # no --since: the whole store, still idempotent
        self.assertEqual(everything["event"]["added"], 1)   # S0's session start, before the cutover

    def test_the_delta_appears_in_the_projections(self):
        self.merge(person_act_ids=self.ids())
        conn = of_store.connect(self.dst_path)
        try:
            asks = of_asks.project(of_asks.load(conn))
            texts = {a["text"] for a in asks if a["state"] == "open"}
            self.assertEqual(texts, {"Make p50 the target?", "Run the old migration?"})
            hands = of_handover.project(of_handover.load(conn))
            self.assertEqual(hands[self.handover["of.event.id"]]["title"], "Rotate the key")
            ref = conn.execute("SELECT raw_ref FROM event WHERE event_id = ?",
                               (self.prompt["of.event.id"],)).fetchone()[0]
            table, _, rid = ref.partition(":")
            row = conn.execute("SELECT hook_event, session_id FROM hook_record WHERE id = ?", (int(rid),)).fetchone()
            self.assertEqual((table, row), ("hook_record", ("UserPromptSubmit", "S1")))
            hit = conn.execute("SELECT event_id FROM event_fts WHERE event_fts MATCH 'rotation'").fetchall()
            self.assertEqual([h[0] for h in hit], [self.prompt["of.event.id"]])
        finally:
            conn.close()

    def test_another_organizations_rows_never_cross(self):
        """of_review on PR 195: a laptop store holds every organization's rows; the host's store is one's."""
        counts = self.merge(person_act_ids=self.ids())
        self.assertEqual(counts["transcript_record"]["other_org"], 1)
        conn = of_store.connect(self.dst_path)
        try:
            for r in (self.other, self.other_answer):
                self.assertIsNone(conn.execute("SELECT 1 FROM event WHERE event_id = ?", (r["of.event.id"],)).fetchone())
            self.assertIsNone(conn.execute("SELECT 1 FROM transcript_record WHERE session_id = 'Z1'").fetchone())
            self.assertIsNone(of_store.get_blob(conn, self.other_sha))
        finally:
            conn.close()
        with self.assertRaises(of_merge_delta.MergeError):
            of_merge_delta.merge(self.src_path, self.dst_path, org="", orgs_file=self.orgs)

    def test_acts_of_a_person_cross_only_by_their_ids(self):
        """of_secrets-review and of_review on PR 195: the owner's answers and words the target takes as his cross only
        as ids whoever runs the merge read and checked; a host id is the source's own claim."""
        src = of_store.connect(self.src_path)
        answer = rec("agent_question_answered", {"ask_id": self.after_ask["of.event.id"], "by": "owner",
                                                 "via": "asks-ui", "answer": "yes"}, "2026-11-15T12:08:00.000Z")
        forged = rec("agent_question_answered", {"ask_id": self.before["of.event.id"], "by": "owner",
                                                 "via": "asks-ui", "answer": "yes"}, "2026-11-15T12:09:00.000Z",
                     host="laptop")   # stamped with the owner's machine by another process of the source's OS user
        for r in (answer, forged):
            of_store.add_event(src, r)
        src.close()
        dry = self.merge(dry_run=True)
        self.assertEqual(sorted((x["name"], x["held"]) for x in dry["person_acts"]),
                         [("of.agent_question_answered", True), ("of.agent_question_answered", True),
                          ("of.human_prompt", True)])
        held = self.merge()                                         # nothing named: every act of a person held
        self.assertEqual((held["event"]["added"], held["event"]["held"]), (2, 3))
        later = self.merge(person_act_ids=self.ids() + [answer["of.event.id"]])   # checked, one by one
        self.assertEqual((later["event"]["added"], later["event"]["held"]), (2, 1))
        conn = of_store.connect(self.dst_path)
        try:
            self.assertIsNone(conn.execute("SELECT 1 FROM event WHERE event_id = ?",
                                           (forged["of.event.id"],)).fetchone())
            self.assertTrue(conn.execute("SELECT 1 FROM event WHERE event_id = ?", (answer["of.event.id"],)).fetchone())
        finally:
            conn.close()

    def test_the_target_ships_the_merged_rows_itself(self):
        """B3's ClickHouse part: the merged rows come after the target's ship cursor, so its own shipper sends them
        through its own of-server (masked with its key); ClickHouse keeps an event and a transcript record once by its
        key. An event the source had from the coordination feed is never shipped by the target either."""
        import sqlite3
        import of_coord
        import of_ship
        src = of_store.connect(self.src_path)
        fed = rec("state_declared", {"state": "working"}, "2026-11-15T12:20:00.000Z", host="colleague")
        of_store.add_event(src, fed, raw_ref=of_coord.RAW_REF + "77")
        src.close()
        dst = of_store.connect(self.dst_path)   # the target has shipped what it had before the merge
        for table, key in of_ship.TABLES:
            top = dst.execute(f"SELECT max({key}) FROM {table}").fetchone()[0] or 0
            dst.execute("INSERT OR REPLACE INTO ship_cursor VALUES (?, ?)", (table, top))
        dst.close()
        self.merge(person_act_ids=self.ids())
        conn = of_store.connect(self.dst_path)
        conn.row_factory = sqlite3.Row
        try:
            shipped = {}
            for table, _last, batch in of_ship._batches(conn, {"tenant": "t", "batch_rows": 500,
                                                              "batch_bytes": 10 ** 7}, "host"):
                shipped.setdefault(table, []).extend(batch)
        finally:
            conn.close()
        ids = {d.get("event_id") or (d.get("record") and json.loads(d["record"]).get("of.event.id"))
               for d in shipped.get("event", [])}
        self.assertTrue({self.after_ask["of.event.id"], self.handover["of.event.id"],
                         self.prompt["of.event.id"]} <= ids, ids)
        self.assertNotIn(fed["of.event.id"], ids)
        self.assertEqual(sorted(d["record_uuid"] for d in shipped.get("transcript_record", [])), ["u-1", "u-2"])
        self.assertEqual([d["hook_event"] for d in shipped.get("hook_record", [])], ["UserPromptSubmit"])

    def test_the_registry_decides_the_positions_and_foreign_records_hold(self):
        """of_review on PR 195: an event labelled with one organization but naming another's position, and one that
        changes another organization's ask by its id, never cross."""
        dst = of_store.connect(self.dst_path)
        theirs = ask("Their own question", "2026-11-15T11:50:00.000Z", position="zeta_ops", session="Z1", org="userorg")
        of_store.add_event(dst, theirs)
        dst.close()
        src = of_store.connect(self.src_path)
        borrowed = ask("Spoofed question", "2026-11-15T12:12:00.000Z", position="zeta_ops", session="S1")  # org ofb
        withdrawal = rec("ask_withdrawn", {"ask_id": theirs["of.event.id"], "by": "acme_ops"},
                         "2026-11-15T12:13:00.000Z")
        for r in (borrowed, withdrawal):
            of_store.add_event(src, r)
        src.close()
        counts = self.merge(person_act_ids=self.ids())
        self.assertEqual([x["event_id"] for x in counts["held_refs"]], [withdrawal["of.event.id"]])
        conn = of_store.connect(self.dst_path)
        try:
            for r in (borrowed, withdrawal):
                self.assertIsNone(conn.execute("SELECT 1 FROM event WHERE event_id = ?", (r["of.event.id"],)).fetchone())
            open_ = {a["event_id"] for a in of_asks.project(of_asks.load(conn)) if a["state"] == "open"}
            self.assertIn(theirs["of.event.id"], open_)
        finally:
            conn.close()
        with self.assertRaises(of_merge_delta.MergeError):   # an organization the target does not list
            self.merge(org="nobody")

    def test_a_blob_whose_bytes_are_not_its_hash_is_refused(self):
        """of_secrets-review on PR 195: a poisoned blob would land, and keep the real one out for good."""
        src = of_store.connect(self.src_path)
        real = b'{"type": "user", "message": "the real record"}'
        sha = hashlib.sha256(real).hexdigest()
        src.execute("INSERT INTO blob VALUES (?,?,?,?)", (sha, 5, "raw", b"other"))
        src.execute("INSERT INTO transcript_record (source_path, line_offset, blob_sha256, session_id, record_uuid, "
                    "timestamp) VALUES (?,?,?,?,?,?)", ("/x/S1.jsonl", 40, sha, "S1", "u-9", "2026-11-15T12:30:00Z"))
        src.close()
        counts = self.merge(person_act_ids=self.ids())
        self.assertEqual((counts["blob"]["refused"], counts["transcript_record"]["refused"]), (1, 1))
        dst = of_store.connect(self.dst_path)
        try:
            self.assertIsNone(of_store.get_blob(dst, sha))
            self.assertEqual(of_store.put_blob(dst, real), sha)          # the real one can still be stored
            self.assertEqual(of_store.get_blob(dst, sha), real)
        finally:
            dst.close()

    def test_dry_run_writes_nothing(self):
        counts = self.merge(dry_run=True, person_act_ids=self.ids())
        self.assertEqual(counts["event"]["added"], 3)
        conn = of_store.connect(self.dst_path)
        try:
            self.assertEqual(conn.execute("SELECT count(*) FROM event").fetchone()[0], 1)
        finally:
            conn.close()

    def test_the_source_is_read_only_and_refusals(self):
        before = os.path.getmtime(self.src_path)
        self.merge()
        self.assertEqual(os.path.getmtime(self.src_path), before)
        with self.assertRaises(of_merge_delta.MergeError):
            of_merge_delta.merge(self.dst_path, self.dst_path, org="ofb", orgs_file=self.orgs)
        with self.assertRaises(of_merge_delta.MergeError):
            of_merge_delta.merge(os.path.join(self.tmp.name, "nowhere.db"), self.dst_path, org="ofb", orgs_file=self.orgs)

    def test_cli(self):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = of_merge_delta.main(["--source", self.src_path, "--target", self.dst_path, "--org", "ofb",
                                        "--since", CUT, "--orgs", self.orgs, "--person-acts", self.ids()[0], "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["event"]["added"], 3)


if __name__ == "__main__":
    unittest.main()
