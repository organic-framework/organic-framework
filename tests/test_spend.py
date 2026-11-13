"""Spend per goal (bin/of_spend.py; docs/registry.md, Goals, "Spend per goal"): attribution session -> position -> work ->
goal, and the month's sums."""
import calendar
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_spend  # noqa: E402

T0 = calendar.timegm((2026, 11, 1, 0, 0, 0))
H = 3600


def iso(t):
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


DOCS = {"acme": {
    "organization": "acme",
    "positions": [{"name": "acme_a", "aliases": ["acme_old"], "holder": {"session": "S1"}},
                  {"name": "acme_b", "holder": {"session": "S2"}},
                  {"name": "acme_c"}, {"name": "acme_d"}, {"name": "acme_e"}],
    "goals": {"g-root": {"title": "r"},
              "g-orders": {"parent": "g-root", "title": "o"},
              "g-cost": {"parent": "g-root", "title": "c"}},
    "conditions": {"c-up": {"title": "u"}},
    "works": {"w-retry": {"goal": "g-orders", "doers": ["acme_a", "acme_c"]},
              "w-cache": {"goal": "g-cost", "doers": ["acme_a"]},
              "w-old": {"goal": "g-cost", "doers": ["acme_c"], "state": "done"},
              "w-page": {"condition": "c-up", "doers": ["acme_d"]},
              "w-d2": {"goal": "g-orders", "doers": ["acme_e"]},
              "w-e2": {"goal": "g-orders", "doers": ["acme_e"]},
              "w-x": {"goal": "g-cost", "doers": ["acme_b"]},
              "w-y": {"goal": "g-orders", "doers": ["acme_b"]}}}}


class Attribution(unittest.TestCase):
    def test_spans_merge_aliases_and_the_busier_position_wins_an_overlap(self):
        canon = of_spend.canon_map(DOCS)
        groups = [("S9", "acme", "acme_a", 100, iso(T0), iso(T0 + 10 * H)),
                  ("S9", None, "acme_old", 5, iso(T0 + 2 * H), iso(T0 + 3 * H)),     # an alias: the same position
                  ("S9", "acme", "acme_b", 3, iso(T0 + 4 * H), iso(T0 + 5 * H)),    # a blip inside: acme_a keeps it
                  ("S9", "acme", "acme_c", 50, iso(T0 + 12 * H), iso(T0 + 20 * H)),  # handed over later
                  ("S9", "acme", "ghost", 9, iso(T0), iso(T0 + H))]                 # no registry knows it
        spans = of_spend.owner_spans(groups, canon)["S9"]
        self.assertEqual(len(spans), 3)
        at = lambda h: of_spend.owner_at(spans, T0 + h * H)  # noqa: E731
        self.assertEqual(at(4.5), ("acme", "acme_a"))
        self.assertEqual(at(11), ("acme", "acme_a"))   # between spans: the earlier one
        self.assertEqual(at(15), ("acme", "acme_c"))
        self.assertEqual(at(30), ("acme", "acme_c"))

    def test_the_fallbacks_one_open_work_one_goal_or_none(self):
        doc = DOCS["acme"]
        self.assertEqual(of_spend.fallback_target("acme", "acme_c", doc), ("work", "w-retry"))  # w-old is done
        self.assertEqual(of_spend.fallback_target("acme", "acme_e", doc), ("goal", "g-orders"))  # two works, one goal
        self.assertEqual(of_spend.fallback_target("acme", "acme_b", doc), ("none", None))  # two goals
        self.assertEqual(of_spend.fallback_target("acme", "nobody", doc), ("none", None))

    def test_a_declared_work_holds_until_the_next_declaration(self):
        spans = {"S1": [(float("-inf"), float("inf"), 0, ("acme", "acme_a"))]}
        decl = {("acme", "acme_a"): [(T0 - H, "w-cache"), (T0 + 5 * H, "w-retry")]}
        segs = of_spend.segments(spans, decl, T0, T0 + 10 * H)
        self.assertEqual([(a - T0, b - T0, w) for _, a, b, _, _, w in segs], [(0, 5 * H, "w-cache"), (5 * H, 10 * H, "w-retry")])


class Sums(unittest.TestCase):
    def s(self, fresh, usd):
        return {"fresh": fresh, "cost_usd": usd, "responses": 1}

    def test_works_roll_up_to_goals_with_their_descendants_and_conditions(self):
        segs = [("acme", "acme_a", "w-retry", self.s(100, 1.0)),
                ("acme", "acme_a", "w-cache", self.s(200, 2.0)),
                ("acme", "acme_e", None, self.s(40, 0.4)),          # to g-orders directly
                ("acme", "acme_d", None, self.s(30, 0.3)),          # its one open work keeps c-up
                ("acme", "acme_b", None, self.s(7, 0.07)),          # unattributed under acme_b
                (None, None, None, self.s(5, 0.05))]               # a session no position held
        out = of_spend.summarize(segs, DOCS, T0, T0 + 30 * 86400, T0 + 15 * 86400)
        o = out["orgs"]["acme"]
        self.assertEqual(o["works"]["w-retry"]["fresh"], 100)
        self.assertEqual(o["goals"]["g-orders"]["total"]["fresh"], 140)
        self.assertEqual(o["goals"]["g-cost"]["total"]["fresh"], 200)
        self.assertEqual(o["goals"]["g-root"]["total"]["fresh"], 340)
        self.assertEqual(o["conditions"]["c-up"]["total"]["fresh"], 30)
        self.assertEqual(o["unattributed"]["acme_b"]["fresh"], 7)
        self.assertEqual(out["unowned"]["fresh"], 5)
        self.assertEqual(out["total"]["fresh"], 382)
        for node in (o["goals"]["g-root"], o["conditions"]["c-up"]):  # a reading: nothing compares it with a budget
            self.assertFalse({"budget", "share", "forecast"} & set(node))
        self.assertEqual(set(o["by_method"]), {"declared", "one_goal", "one_open_work", "unattributed"})

    def test_the_text_shows_the_tree_and_the_unattributed(self):
        segs = [("acme", "acme_a", "w-retry", self.s(600, 6.0)), ("acme", "acme_b", None, self.s(7, 0.07))]
        text = of_spend.render(of_spend.summarize(segs, DOCS, T0, T0 + 30 * 86400, T0 + 15 * 86400))
        self.assertIn("g-root", text)
        self.assertIn("    g-orders", text)                   # a sub-goal under its parent
        self.assertNotIn("forecast", text)
        self.assertIn("w-retry -> g-orders", text)
        self.assertIn("unattributed", text)
        self.assertIn("acme_b", text)
        self.assertIn("no spend computed yet", of_spend.render(None))

    def test_a_parent_cycle_never_hangs(self):
        docs = {"acme": dict(DOCS["acme"], goals={"g-a": {"parent": "g-b"}, "g-b": {"parent": "g-a"}},
                             works={"w": {"goal": "g-a", "doers": ["acme_a"]}})}
        out = of_spend.summarize([("acme", "acme_a", "w", self.s(1, 0.01))], docs, T0, T0 + 86400, T0 + 3600)
        self.assertEqual(out["orgs"]["acme"]["goals"]["g-a"]["works"]["fresh"], 1)


class Store(unittest.TestCase):
    """The month view from transcript records and the run end to end."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_spend.of_events.STATE_DIR
        of_spend.of_events.STATE_DIR = self.tmp.name
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript("""
            CREATE TABLE transcript_record (id INTEGER PRIMARY KEY, source_path TEXT, session_id TEXT, message_id TEXT,
              timestamp TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
              cache_write_tokens INTEGER, cache_write_5m_tokens INTEGER, cache_write_1h_tokens INTEGER);
            CREATE TABLE event (event_id TEXT, ts TEXT, name TEXT, session_id TEXT, position TEXT, source TEXT,
              org TEXT, payload TEXT);""")
        self.n = 0

    def tearDown(self):
        of_spend.of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def rec(self, sid, mid, t, inp=1000, out=100, cw1=0, cr=0, sub=False):
        self.n += 1
        path = f"/h/.claude-a/projects/-w/{sid}/subagents/agent-1.jsonl" if sub else f"/h/.claude-a/projects/-w/{sid}.jsonl"
        self.conn.execute("INSERT INTO transcript_record VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (self.n, path, "agent-1" if sub else sid, mid, iso(t), inp, out, cr, None, 0, cw1))

    def ev(self, t, name, sid, pos, payload=None, org="acme"):
        self.conn.execute("INSERT INTO event VALUES (?,?,?,?,?,?,?,?)",
                          (f"e{t}{name}", iso(t), name, sid, pos, "hook", org, json.dumps(payload or {})))

    def test_each_response_once_subagents_to_their_session_and_old_months_dropped(self):
        now = T0 + 10 * 86400
        self.rec("S1", "m1", T0 + H)
        self.rec("S1", "m1", T0 + H)                   # the same response twice (a copy): counted once
        self.rec("S1", "m2", T0 + 2 * H, sub=True)     # a subagent's: to S1
        self.rec("S1", "m3", T0 - 40 * 86400)          # before the previous month: not kept
        n, done = of_spend.ingest(self.conn, now)
        self.assertTrue(done)
        rows = self.conn.execute("SELECT message_id, session_id, fresh FROM view_spend_month ORDER BY 1").fetchall()
        self.assertEqual(rows, [("m1", "S1", 1100), ("m2", "S1", 1100)])
        self.rec("S2", "m4", T0 + 3 * H)
        self.assertEqual(of_spend.ingest(self.conn, now)[0], 1)  # the cursor goes on from where it stopped

    def test_end_to_end_with_a_declaration_and_the_cache(self):
        now = T0 + 2 * 86400
        self.ev(T0 + H, "of.hook", "S1", "acme_a")
        self.ev(T0 + 5 * H, "of.hook", "S1", "acme_a")
        self.ev(T0 + 3 * H, "of.state_declared", "S1", "acme_a", {"work": "w-retry"})
        self.ev(T0 + 3.5 * H, "of.agent_question", "S1", "acme_a", {"work": "w-cache"})  # an ask about another work
        self.rec("S1", "m1", T0 + 2 * H, inp=100, out=0)        # before the declaration: acme_a has two open works
        self.rec("S1", "m2", T0 + 4 * H, inp=200, out=0)        # declared: w-retry
        self.rec("S3", "m3", T0 + 4 * H, inp=7, out=0)          # no position ever held S3
        of_spend.ingest(self.conn, now)
        out = of_spend.compute(DOCS, self.conn, now=now)
        o = out["orgs"]["acme"]
        self.assertEqual(o["unattributed"]["acme_a"]["fresh"], 100)
        self.assertEqual(o["works"]["w-retry"]["fresh"], 200)   # the ask with --work moved nothing (PR 86)
        self.assertNotIn("w-cache", o["works"])
        self.assertEqual(o["goals"]["g-root"]["total"]["fresh"], 200)
        self.assertEqual(out["unowned"]["fresh"], 7)
        self.assertEqual(out["total"]["fresh"], 307)
        self.assertTrue(out["complete"])
        self.assertTrue(of_spend.tick(self.conn, now, docs_fn=lambda: DOCS))
        self.assertEqual(of_spend.load()["orgs"]["acme"]["works"]["w-retry"]["fresh"], 200)
        self.assertFalse(of_spend.tick(self.conn, now + 60, docs_fn=lambda: DOCS))  # within 30 minutes: kept

    def test_the_event_views_match_the_log_and_follow_it_by_a_cursor(self):
        """The founder, 2026-11-14 (of_conductor msg 01a1d68e-8ef9): no reader projects the whole log. The groups
        the attribution reads come from views kept by a cursor, and the sums are the same as from the log."""
        now = T0 + 2 * 86400
        self.ev(T0 + H, "of.hook", "S1", "acme_a")
        self.ev(T0 + 5 * H, "of.hook", "S1", "acme_a")
        self.ev(T0 + 2 * H, "of.hook", "S2", "acme_b", org=None)
        self.ev(T0 + 3 * H, "of.state_declared", "S1", "acme_a", {"work": "w-retry"})
        self.ev(T0 + 3.2 * H, "of.holder_changed", None, "acme_b", {"position": "acme_b", "new_session": "S4"})
        self.rec("S1", "m1", T0 + 2 * H, inp=100, out=0)
        self.rec("S1", "m2", T0 + 4 * H, inp=200, out=0)
        self.rec("S4", "m4", T0 + 4 * H, inp=50, out=0)
        of_spend.ingest(self.conn, now)
        from_log = of_spend.compute(DOCS, self.conn, now=now)
        log_groups = sorted(of_spend.owner_groups(self.conn))
        n, done = of_spend.advance_events(self.conn, batch=2)
        self.assertEqual((n, done), (5, True))
        self.assertEqual(sorted(of_spend.owner_groups(self.conn)), log_groups)
        from_views = of_spend.compute(DOCS, self.conn, now=now)
        for k in ("orgs", "unowned", "total"):
            self.assertEqual(from_views[k], from_log[k])
        self.ev(T0 + 6 * H, "of.hook", "S1", "acme_a")  # only what came since is read
        self.assertEqual(of_spend.advance_events(self.conn)[0], 1)
        self.assertEqual([g for g in of_spend.owner_groups(self.conn) if g[0] == "S1"][0][3], 4)  # two hooks, the declaration, the new one


class Locked(unittest.TestCase):
    def test_a_locked_store_ends_the_tick_without_an_error_and_keeps_the_cursor(self):
        """2026-11-14: a catch-up run stopped on 'database is locked' after the 10 s busy wait."""
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "s.db")
            a = sqlite3.connect(path, isolation_level=None, timeout=0.1)
            a.executescript("""CREATE TABLE event (event_id TEXT, ts TEXT, name TEXT, session_id TEXT, position TEXT,
                source TEXT, org TEXT, payload TEXT);""")
            a.execute("INSERT INTO event VALUES ('e1', ?, 'of.hook', 'S1', 'acme_a', 'hook', 'acme', '{}')", (iso(T0),))
            of_spend.advance_events(a, budget_s=1)  # the views exist
            a.execute("INSERT INTO event VALUES ('e2', ?, 'of.hook', 'S1', 'acme_a', 'hook', 'acme', '{}')", (iso(T0),))
            b = sqlite3.connect(path, isolation_level=None, timeout=0.1)
            b.execute("BEGIN IMMEDIATE")  # another writer holds the store
            self.assertEqual(of_spend.advance_events(a, budget_s=0.6), (0, False))
            b.execute("COMMIT")
            self.assertEqual(of_spend.advance_events(a, budget_s=1), (1, True))  # from where it stopped
            a.close()
            b.close()
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
