"""B3 of the agent host move (bin/of_merge_delta.py): one organization's rows of one machine's store merged into
another's by id; a re-run adds nothing; the delta's asks and hand-overs appear in the target's projections
(lab-agent-host-v0 B3-1, B3-2); another organization's rows never cross; acts of a person cross only from a named host
(of_review and of_secrets-review on organic-framework PR 195)."""
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock

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
        for org, names, work in (("ofb", ["acme_ops", "acme_db"], "w-ofb"), ("userorg", ["zeta_ops"], "w-user")):
            os.makedirs(os.path.join(self.tmp.name, org, "registry"))
            with open(os.path.join(self.tmp.name, org, "registry", "positions.yaml"), "w") as fh:
                fh.write("positions:\n" + "".join(f"  - {{name: {n}}}\n" for n in names)
                         + f"works:\n  {work}: {{kind: project}}\n")
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
        self.assertEqual(sum(again[t]["added"] for t in of_merge_delta.TABLES), 0, again)
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

    def test_another_organizations_diff_and_work_hold(self):
        """of_review on PR 195: of.diff_applied labelled with one organization closed another's diff card; a loop's
        step files itself under a work, which must be the organization's."""
        dst = of_store.connect(self.dst_path)
        theirs = rec("diff_proposed", {"id": "d-user-1", "kind": "spawn"}, "2026-11-15T11:40:00.000Z",
                     position="zeta_ops", session="Z1", org="userorg")
        of_store.add_event(dst, theirs)
        dst.close()
        src = of_store.connect(self.src_path)
        applied = rec("diff_applied", {"id": "d-user-1"}, "2026-11-15T12:14:00.000Z")
        mine = rec("diff_proposed", {"id": "d-ofb-1", "kind": "spawn"}, "2026-11-15T12:15:00.000Z")
        mine_applied = rec("diff_applied", {"id": "d-ofb-1"}, "2026-11-15T12:16:00.000Z")
        their_step = rec("loop_hypothesis", {"work": "w-user", "id": "h1"}, "2026-11-15T12:17:00.000Z")
        our_step = rec("loop_hypothesis", {"work": "w-ofb", "id": "h2"}, "2026-11-15T12:18:00.000Z")
        for r in (applied, mine, mine_applied, their_step, our_step):
            of_store.add_event(src, r)
        src.close()
        counts = self.merge(person_act_ids=self.ids())
        self.assertEqual(sorted(x["event_id"] for x in counts["held_refs"]),
                         sorted([applied["of.event.id"], their_step["of.event.id"]]))
        conn = of_store.connect(self.dst_path)
        try:
            got = {r[0] for r in conn.execute("SELECT event_id FROM event")}
            self.assertTrue({mine["of.event.id"], mine_applied["of.event.id"], our_step["of.event.id"]} <= got)
            self.assertFalse({applied["of.event.id"], their_step["of.event.id"]} & got)
        finally:
            conn.close()

    def _states(self):
        conn = of_store.connect(self.dst_path)
        try:
            got = {r[0] for r in conn.execute("SELECT event_id FROM event")}
            return got, {x["event_id"]: x["state"] for x in of_asks.project(of_asks.load(conn))}
        finally:
            conn.close()

    def _add(self, path, *recs):
        conn = of_store.connect(path)
        try:
            for r in recs:
                of_store.add_event(conn, r)
        finally:
            conn.close()

    def test_an_id_the_target_holds_is_decided_by_its_row(self):
        """of_review on PR 195 (comment 6886650610): a delta proposal labelled ORG that reuses another organization's diff
        or ask id made that record ORG's, so its application or answer crossed and closed their card."""
        diff = rec("diff_proposed", {"id": "d-user-1", "kind": "spawn"}, "2026-11-15T11:40:00.000Z",
                   position="zeta_ops", session="Z1", org="userorg")
        card = ask("Apply d-user-1?", "2026-11-15T11:41:00.000Z", position="zeta_ops", session="Z1", org="userorg")
        card["of.payload"]["diff_id"] = "d-user-1"
        theirs = ask("Rotate their key?", "2026-11-15T11:50:00.000Z", position="zeta_ops", session="Z1", org="userorg")
        self._add(self.dst_path, diff, card, theirs)
        forged_diff = rec("diff_proposed", {"id": "d-user-1", "kind": "spawn"}, "2026-11-15T12:13:00.000Z")
        applied = rec("diff_applied", {"id": "d-user-1"}, "2026-11-15T12:14:00.000Z")
        forged_ask = ask("anything", "2026-11-15T12:12:00.000Z")
        forged_ask["of.event.id"] = theirs["of.event.id"]
        closes = rec("agent_question_answered", {"ask_id": theirs["of.event.id"], "by": "self", "answer": "moved on"},
                     "2026-11-15T12:13:30.000Z")
        self._add(self.src_path, forged_diff, applied, forged_ask, closes)
        counts = self.merge(person_act_ids=self.ids())
        got, states = self._states()
        for what, r in (("the forged proposal", forged_diff), ("its application", applied), ("the answer", closes)):
            self.assertNotIn(r["of.event.id"], got, what)
        self.assertEqual((states[card["of.event.id"]], states[theirs["of.event.id"]]), ("open", "open"))
        self.assertEqual(sorted(x["name"] for x in counts["held_refs"]),
                         ["of.agent_question_answered", "of.diff_applied", "of.diff_proposed"])
        self.assertEqual(counts["event"]["added"], 3)   # ORG's own delta still crosses

    def test_an_id_two_organizations_make_in_the_delta_is_nobodys(self):
        """The target lacks both: the other organization's own proposal and an ORG row reusing its id came in one delta.
        Neither makes the id ORG's, so the ORG row and what files under it are held."""
        real = rec("diff_proposed", {"id": "d-user-2", "kind": "spawn"}, "2026-11-15T12:10:10.000Z",
                   position="zeta_ops", session="Z1", org="userorg")
        forged = rec("diff_proposed", {"id": "d-user-2", "kind": "spawn"}, "2026-11-15T12:13:00.000Z")
        applied = rec("diff_applied", {"id": "d-user-2"}, "2026-11-15T12:14:00.000Z")
        mine = rec("diff_proposed", {"id": "d-ofb-1", "kind": "spawn"}, "2026-11-15T12:15:00.000Z")
        mine_applied = rec("diff_applied", {"id": "d-ofb-1"}, "2026-11-15T12:16:00.000Z")
        self._add(self.src_path, real, forged, applied, mine, mine_applied)
        counts = self.merge(person_act_ids=self.ids())
        got, _ = self._states()
        self.assertFalse({real["of.event.id"], forged["of.event.id"], applied["of.event.id"]} & got)
        self.assertTrue({mine["of.event.id"], mine_applied["of.event.id"]} <= got)   # an id only ORG makes crosses
        self.assertEqual(sorted(x["name"] for x in counts["held_refs"]), ["of.diff_applied", "of.diff_proposed"])

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

    def test_the_live_store_is_never_the_source(self):
        with mock.patch.dict(os.environ, {"OF_DB": self.src_path}):
            with self.assertRaisesRegex(of_merge_delta.MergeError, "live store"):
                self.merge()

    def yields(self, on_yield):
        """Every row past the cap: of_merge_delta's clock and sleep, with ON_YIELD run at each yield."""
        fake = mock.Mock(monotonic=time.monotonic, sleep=lambda s: on_yield())
        return mock.patch.multiple(of_merge_delta, TX_CAP_S=-1, time=fake)

    def test_a_hook_takes_the_lock_at_every_yield(self):
        """of_platform, msg 01a1dc95-cee1: a merge held a store's write lock 154.7 s in one transaction while every
        hook waits 0.15 s. Each yield must leave the lock free: a writer that waits 0 s gets it."""
        got = []

        def hook():
            c = sqlite3.connect(self.dst_path, timeout=0, isolation_level=None)
            try:
                c.execute("BEGIN IMMEDIATE")
                c.execute("ROLLBACK")
                got.append(True)
            except sqlite3.OperationalError:
                got.append(False)
            finally:
                c.close()
        with self.yields(hook):
            counts = self.merge(person_act_ids=self.ids())
        self.assertGreaterEqual(len(got), 3, counts)
        self.assertTrue(all(got), got)
        self.assertGreater(counts["transactions"]["count"], len(got))   # a commit before each yield, and at each end
        self.assertEqual(counts["event"]["added"], 3)

    def test_a_merge_cut_short_is_finished_by_running_it_again(self):
        n = {"yields": 0}

        def crash():
            n["yields"] += 1
            if n["yields"] == 3:
                raise KeyboardInterrupt
        with self.yields(crash), self.assertRaises(KeyboardInterrupt):
            self.merge(person_act_ids=self.ids())
        self.merge(person_act_ids=self.ids())
        conn = of_store.connect(self.dst_path)
        try:
            got = [conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                   for t in ("event", "transcript_record", "hook_record")]
        finally:
            conn.close()
        self.assertEqual(got, [1 + 3, 2, 3 + 1])   # what one whole merge leaves
        again = self.merge(person_act_ids=self.ids())
        self.assertEqual(sum(again[t]["added"] for t in of_merge_delta.TABLES), 0, again)

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
