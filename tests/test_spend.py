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
              cache_write_tokens INTEGER, cache_write_5m_tokens INTEGER, cache_write_1h_tokens INTEGER, model TEXT);
            CREATE TABLE event (event_id TEXT, ts TEXT, name TEXT, session_id TEXT, position TEXT, source TEXT,
              org TEXT, payload TEXT, host_id TEXT, host_name TEXT);""")
        self.n = 0

    def tearDown(self):
        of_spend.of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def rec(self, sid, mid, t, inp=1000, out=100, cw1=0, cr=0, sub=False, model="claude-opus-5-5", acct="a"):
        self.n += 1
        base = f"/h/.claude-{acct}/projects/-w/{sid}"
        path = f"{base}/subagents/agent-1.jsonl" if sub else f"{base}.jsonl"
        self.conn.execute("INSERT INTO transcript_record VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (self.n, path, "agent-1" if sub else sid, mid, iso(t), inp, out, cr, None, 0, cw1, model))

    def ev(self, t, name, sid, pos, payload=None, org="acme", host="H-here", host_name="here"):
        self.conn.execute("INSERT INTO event VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (f"e{t}{name}{host}", iso(t), name, sid, pos, "hook", org, json.dumps(payload or {}), host,
                           host_name))

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

    def test_each_response_keeps_its_account_and_its_models_price(self):
        """The day and hour views need the account; and one Opus price for every model misread a day by model."""
        now = T0 + 10 * 86400
        self.rec("S1", "m1", T0 + H, inp=1_000_000, out=0)                                  # Opus 5.5: $4
        self.rec("S1", "m2", T0 + H, inp=1_000_000, out=0, model="claude-sonnet-5-5")       # $2
        self.rec("S1", "m3", T0 + H, inp=1_000_000, out=0, model="claude-fable-5-2")        # by family: $10
        self.rec("S1", "m4", T0 + H, inp=1_000_000, out=0, model="something-new", acct="b")  # no price: Opus's
        of_spend.ingest(self.conn, now)
        got = dict(((m, (round(c, 2), d)) for m, c, d in self.conn.execute(
            "SELECT message_id, cost, config_dir FROM view_spend_month")))
        self.assertEqual(got, {"m1": (4.0, "/h/.claude-a"), "m2": (2.0, "/h/.claude-a"), "m3": (10.0, "/h/.claude-a"),
                               "m4": (4.0, "/h/.claude-b")})

    def test_a_view_of_the_old_shape_is_made_again_and_read_from_the_start(self):
        now = T0 + 10 * 86400
        self.conn.executescript("CREATE TABLE view_spend_month (message_id TEXT PRIMARY KEY, session_id TEXT, "
                                "epoch REAL, fresh INTEGER, cost REAL);")
        self.conn.execute("CREATE TABLE IF NOT EXISTS listener_meta (key TEXT PRIMARY KEY, value TEXT)")
        self.conn.execute("INSERT INTO listener_meta VALUES ('spend_month_version', '1')")
        self.conn.execute("INSERT INTO listener_meta VALUES ('spend_month_cursor', '99')")
        self.rec("S1", "m1", T0 + H)
        of_spend.ingest(self.conn, now)
        self.assertEqual(self.conn.execute("SELECT message_id, config_dir, model FROM view_spend_month").fetchall(),
                         [("m1", "/h/.claude-a", "claude-opus-5-5")])

    def test_spend_by_day_and_by_hour_by_account_position_work_and_goal(self):
        """The founder, 2026-11-22, prompt 01a204e5-0acb: of capacity spend by day and by hour."""
        now = T0 + 3 * 86400
        self.ev(T0 + H, "of.hook", "S1", "acme_a")
        self.ev(T0 + 2 * 86400, "of.hook", "S1", "acme_a")
        self.ev(T0 + H, "of.state_declared", "S1", "acme_a", {"work": "w-retry"})
        self.ev(T0 + H, "of.hook", "S2", "acme_b")
        self.rec("S1", "m1", T0 + 2 * H, inp=1_000_000, out=0)                     # day 1, hour 2, account a
        self.rec("S1", "m2", T0 + 86400 + 5 * H, inp=1_000_000, out=0, acct="b")   # day 2, account b
        self.rec("S2", "m3", T0 + 86400 + 5 * H, inp=500_000, out=0, model="claude-sonnet-5-5")
        self.rec("S9", "m4", T0 + 86400 + 6 * H, inp=1_000, out=0)                 # no position held S9
        of_spend.ingest(self.conn, now)
        day = of_spend.periods(DOCS, self.conn, T0 + 3 * H, now, by="day", now=now, here="H-here")  # day 1 whole
        self.assertEqual([p["start"] for p in day["periods"]], [T0, T0 + 86400])
        d1, d2 = day["periods"]
        self.assertEqual(d1["total"]["cost_usd"], 4.0)
        self.assertEqual(d1["accounts"], {"/h/.claude-a": {"fresh": 1_000_000, "cost_usd": 4.0, "responses": 1,
                                                          "outside_fresh": 0, "outside_runs": 0}})
        self.assertEqual(d1["machines"]["here"]["accounts"]["/h/.claude-a"]["positions"]["acme/acme_a"]["cost_usd"], 4.0)
        self.assertEqual(d1["works"]["acme/w-retry"]["cost_usd"], 4.0)
        self.assertEqual(d1["goals"]["acme/g-orders"]["cost_usd"], 4.0)          # a work's spend counts to its goal
        self.assertEqual(d2["accounts"]["/h/.claude-b"]["cost_usd"], 4.0)
        self.assertEqual(d2["positions"]["acme/acme_b"]["cost_usd"], 1.0)   # Sonnet's price
        self.assertEqual(d2["positions"]["(no position)"]["responses"], 1)
        self.assertEqual(day["positions"]["acme/acme_a"]["cost_usd"], 8.0)
        self.assertEqual(day["total"]["responses"], 4)
        hour = of_spend.periods(DOCS, self.conn, T0 + 86400, T0 + 86400 + 7 * H, by="hour", now=now, here="H-here")
        self.assertEqual([(p["start"] - T0 - 86400) // H for p in hour["periods"]], [5, 6])
        self.assertEqual(hour["periods"][0]["total"]["responses"], 2)
        only = of_spend.periods(DOCS, self.conn, T0, now, by="day", org="acme", now=now, here="H-here")
        self.assertNotIn("(no position)", only["positions"])                 # one organization's positions only
        text = of_spend.render_periods(day, top=1)
        self.assertIn("2026-11-01 (part)  $4", text)                          # asked from 03:00
        self.assertIn("2026-11-02  $5", text)
        self.assertIn("  here  $5", text)
        self.assertIn("    /h/.claude-b $4: acme_a $4", text)
        self.assertIn("by account, all machines: /h/.claude-a $5, /h/.claude-b $4", text)

    def test_a_carried_sessions_history_counts_on_the_machine_it_was_spent_on(self):
        """2026-11-22: the agent host's store read the moved sessions' laptop turns again. Each response counts on the
        machine its session's last carry restore put it on, and before its first restore on the one it came from."""
        restores = {"S1": [(T0 + 10 * H, "host")],                                   # laptop -> host at 10:00
                    "S2": [(T0 + 10 * H, "host"), (T0 + 12 * H, "laptop")]}           # and back at 12:00
        self.assertEqual(of_spend.away_spans(restores, "laptop"), {"S1": [(T0 + 10 * H, float("inf"))],
                                                                   "S2": [(T0 + 10 * H, T0 + 12 * H)]})
        self.assertEqual(of_spend.away_spans(restores, "host"), {"S1": [(float("-inf"), T0 + 10 * H)],
                                                                 "S2": [(float("-inf"), T0 + 10 * H),
                                                                        (T0 + 12 * H, float("inf"))]})
        self.assertEqual(of_spend.counted_parts(T0, T0 + 24 * H, [(T0 + 10 * H, T0 + 12 * H)]),
                         [(T0, T0 + 10 * H), (T0 + 12 * H, T0 + 24 * H)])
        now = T0 + 86400
        self.ev(T0 + H, "of.hook", "S1", "acme_a")
        self.ev(T0 + 10 * H, "of.holder_changed", "S1", "acme_a",
                {"position": "acme_a", "new_session": "S1", "evidence": "of capacity carry restore"}, host="H-host")
        self.rec("S1", "m1", T0 + 2 * H, inp=1_000_000, out=0)          # spent here, carried along to the host
        self.rec("S1", "m2", T0 + 11 * H, inp=1_000_000, out=0)         # spent on the host
        of_spend.ingest(self.conn, now)
        here = of_spend.periods(DOCS, self.conn, T0, now, by="day", now=now, here="H-here")
        self.assertEqual(here["total"]["cost_usd"], 4.0)
        host = of_spend.periods(DOCS, self.conn, T0, now, by="day", now=now, here="H-host")
        self.assertEqual(host["total"]["cost_usd"], 4.0)                # the host's store: only its own
        self.assertEqual(of_spend.compute(DOCS, self.conn, now=now, here="H-host")["total"]["cost_usd"], 4.0)

    def test_each_machine_reports_its_hours_and_the_others_show_them(self):
        """The founder, 2026-11-22, prompt 01a204f4-e8e2: the day and hour views missed the agent host's spend."""
        now = T0 + 2 * 86400 + 30 * 60
        self.ev(T0 + H, "of.hook", "S1", "acme_a")
        self.rec("S1", "m1", T0 + 2 * 86400 + 60, inp=1_000_000, out=0)
        self.rec("S9", "m2", T0 + 2 * 86400 + 60, inp=1_000, out=0)     # no position: stays on its machine
        of_spend.ingest(self.conn, now)
        sent = []
        emit = lambda kind, payload, **k: sent.append((kind, payload, k))  # noqa: E731
        self.assertEqual(of_spend.emit_hours(DOCS, self.conn, now, here="H-here", emit=emit), 1)
        (kind, pl, kw), = sent
        self.assertEqual((kind, kw, pl["hour"]), ("spend_hours", {"org": "acme"}, T0 + 2 * 86400))
        self.assertEqual(pl["rows"], [["/h/.claude-a", "acme_a", None, None, 1_000_000, 4.0, 1, 0, 0]])
        self.assertEqual(of_spend.emit_hours(DOCS, self.conn, now + 60, here="H-here", emit=emit), 0)  # 15 minutes
        self.assertEqual(of_spend.emit_hours(DOCS, self.conn, now + 1000, here="H-here", emit=emit), 0)  # no change
        # another machine's two reports of the same hour: the latest counts
        for t, cost in ((now, 9.0), (now + 600, 2.0)):
            self.ev(t, "of.spend_hours", None, None, {"v": 1, "hour": T0 + 2 * 86400, "machine": "host-b",
                                                      "rows": [["~/.claude-b", "acme_b", None, None, 10, cost, 3, 0, 0]]},
                    host="H-host", host_name="host-b")
        out = of_spend.periods(DOCS, self.conn, T0 + 2 * 86400, now + 3600, by="hour", now=now + 700, here="H-here")
        self.assertEqual(out["reported"], ["host-b"])
        self.assertEqual(out["machines"]["host-b"]["accounts"]["~/.claude-b"]["positions"]["acme/acme_b"]["cost_usd"], 2.0)
        self.assertEqual(out["machines"]["here"]["accounts"]["/h/.claude-a"]["positions"]["acme/acme_a"]["cost_usd"], 4.0)
        self.assertEqual(out["positions"]["acme/acme_b"]["cost_usd"], 2.0)
        self.assertIn("machines: here (this one), host-b (its reports)", of_spend.render_periods(out))

    def test_an_hour_reported_before_is_reported_again_when_late_records_change_it(self):
        """of_review on PR 567: an hour once reported was never sent again, so a response read late (a transcript read
        after the hour's report, a carried history) never reached the other machines; nor did a carry restore that
        gives an old hour to another machine, nor an hour whose rows all went."""
        now = T0 + 2 * 86400 + 30 * 60
        self.ev(T0 + H, "of.hook", "S1", "acme_a")
        self.rec("S1", "m1", T0 + 2 * 86400 + 60, inp=1_000_000, out=0)
        of_spend.advance(self.conn, now)
        sent = []
        emit = lambda kind, payload, **k: sent.append(payload)  # noqa: E731
        self.assertEqual(of_spend.emit_hours(DOCS, self.conn, now, here="H-here", emit=emit), 1)
        # a day later: a response of the first day, read only now (six hours before the last report's window)
        later = now + 86400
        self.rec("S1", "m2", T0 + 86400 + 2 * H, inp=1_000_000, out=0)
        of_spend.advance(self.conn, later)
        self.assertEqual(of_spend.emit_hours(DOCS, self.conn, later, here="H-here", emit=emit), 1)
        self.assertEqual((sent[-1]["hour"], sent[-1]["rows"][0][5]), (T0 + 86400 + 2 * H, 4.0))
        self.assertEqual(of_spend.emit_hours(DOCS, self.conn, later + 1000, here="H-here", emit=emit), 0)  # nothing new
        # a carry restore brings the session here now: what it spent before counts on the machine it came from, so
        # both hours it was reported in go again, empty
        self.ev(later + 2000, "of.holder_changed", "S1", "acme_a",
                {"position": "acme_a", "new_session": "S1", "evidence": "of capacity carry restore"})
        of_spend.advance(self.conn, later + 2000)
        self.assertEqual(of_spend.emit_hours(DOCS, self.conn, later + 2000, here="H-here", emit=emit), 2)
        self.assertEqual([(p["hour"], p["rows"]) for p in sent[-2:]], [(T0 + 86400 + 2 * H, []), (T0 + 2 * 86400, [])])

    def test_carry_restores_read_the_view_once_it_caught_up(self):
        """tests/test_log_reads.py (of_review on PR 567): the restores come from view_spend_event, each machine by its
        event's rowid, and the same from the log before the view caught up."""
        now = T0 + 86400
        self.ev(T0 + 10 * H, "of.holder_changed", "S1", "acme_a",
                {"position": "acme_a", "new_session": "S1", "evidence": "of capacity carry restore"}, host="H-host")
        self.ev(T0 + 11 * H, "of.holder_changed", "S2", "acme_a",
                {"position": "acme_a", "new_session": "S2", "evidence": "of capacity move"}, host="H-host")
        before = of_spend.carry_restores(self.conn, now)
        of_spend.advance_events(self.conn)
        self.assertTrue(of_spend._events_ready(self.conn))
        self.assertEqual(of_spend.carry_restores(self.conn, now), before)
        self.assertEqual(before, {"S1": [(T0 + 10 * H, "H-host")]})

    def test_two_organizations_rows_of_one_name_stay_apart(self):
        """of_review on PR 558: positions, works and goals were keyed by bare name, so two organizations' rows of one
        name printed as one sum."""
        docs = dict(DOCS, beta={"organization": "beta", "positions": [{"name": "acme_a", "holder": {"session": "S7"}}],
                                "works": {"w-retry": {"doers": ["acme_a"]}}})
        now = T0 + 86400
        self.ev(T0 + H, "of.hook", "S1", "acme_a")
        self.ev(T0 + H, "of.state_declared", "S1", "acme_a", {"work": "w-retry"})
        self.ev(T0 + H, "of.hook", "S7", "acme_a", org="beta")
        self.rec("S1", "m1", T0 + 2 * H, inp=1_000_000, out=0)
        self.rec("S7", "m2", T0 + 2 * H, inp=1_000_000, out=0)
        of_spend.ingest(self.conn, now)
        out = of_spend.periods(docs, self.conn, T0, now, by="day", now=now)
        self.assertEqual({k: v["cost_usd"] for k, v in out["positions"].items()}, {"acme/acme_a": 4.0, "beta/acme_a": 4.0})
        self.assertEqual({k: v["cost_usd"] for k, v in out["periods"][0]["works"].items()},
                         {"acme/w-retry": 4.0, "beta/w-retry": 4.0})
        self.assertIn("acme/acme_a $4", of_spend.render_periods(out))       # one name in two: both named in full

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

    def test_runs_on_other_harnesses_go_to_their_position_and_work_with_no_list_cost(self):
        # RFC 0008: a position's runs on Codex or Muse are in no transcript; of.subagent_run carries their tokens
        now = T0 + 2 * 86400
        self.ev(T0 + H, "of.hook", "S1", "acme_a")
        self.ev(T0 + 3 * H, "of.state_declared", "S1", "acme_a", {"work": "w-retry"})
        run = lambda t, **kw: self.ev(t, "of.subagent_run", "S1", kw.pop("pos", "acme_a"), dict(  # noqa: E731
            {"phase": "ended", "position": kw.pop("position", "acme_a"), "harness": "codex", "model": "gpt-6.2-astra"},
            **kw))
        run(T0 + 4 * H, usage={"input": 1000, "cached": 600, "output": 50, "reasoning": 20})  # the declared work
        run(T0 + 5 * H, work="w-cache", usage={"input": 200, "cached": 0, "output": 10})      # its own work
        run(T0 + 5.5 * H, phase="started", usage={"input": 9999})                               # a start: not counted
        run(T0 + 6 * H, pos="nobody", position="nobody", harness="muse", model="m", usage={"input": 7})
        of_spend.ingest(self.conn, now)
        out = of_spend.compute(DOCS, self.conn, now=now)
        o = out["orgs"]["acme"]
        self.assertEqual((o["works"]["w-retry"]["outside_fresh"], o["works"]["w-retry"]["outside_runs"]), (450, 1))
        self.assertEqual((o["works"]["w-retry"]["fresh"], o["works"]["w-retry"]["cost_usd"]), (0, 0))
        self.assertEqual(o["works"]["w-cache"]["outside_fresh"], 210)
        self.assertEqual(o["goals"]["g-root"]["total"]["outside_fresh"], 660)
        self.assertEqual(out["unowned"]["outside_fresh"], 7)
        self.assertEqual(out["outside"]["codex/gpt-6.2-astra"],
                         {"runs": 2, "fresh": 660, "input": 1200, "cached": 600, "output": 60, "reasoning": 20})
        text = of_spend.render(out)
        self.assertIn("in 1 outside run(s)", text)
        self.assertIn("runs on other harnesses", text)

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
    def test_the_views_migration_waits_for_the_write_lock_and_is_one_transaction(self):
        """of_review on PR 558: the version read, the drop and the reset ran with no lock across them, so the tick and
        the command could leave the view empty and marked complete."""
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "s.db")
            a = sqlite3.connect(path, isolation_level=None, timeout=0.1)
            a.executescript("""
                CREATE TABLE transcript_record (id INTEGER PRIMARY KEY, source_path TEXT, session_id TEXT, message_id TEXT,
                  timestamp TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
                  cache_write_tokens INTEGER, cache_write_5m_tokens INTEGER, cache_write_1h_tokens INTEGER, model TEXT);
                CREATE TABLE view_spend_month (message_id TEXT PRIMARY KEY, session_id TEXT, epoch REAL, fresh INTEGER,
                  cost REAL);
                CREATE TABLE listener_meta (key TEXT PRIMARY KEY, value TEXT);
                INSERT INTO listener_meta VALUES ('spend_month_version', '1'), ('spend_month_cursor', '1'),
                  ('spend_month_caught_up', '1');""")
            a.execute("INSERT INTO transcript_record VALUES (1, '/h/.claude-a/projects/-w/S1.jsonl', 'S1', 'm1', ?, "
                      "1000, 0, 0, NULL, 0, 0, 'claude-opus-5-5')", (iso(T0 + H),))
            b = sqlite3.connect(path, isolation_level=None, timeout=0.1)
            b.execute("BEGIN IMMEDIATE")  # another writer holds the store
            self.assertEqual(of_spend.ingest(a, T0 + 86400, budget_s=0.5), (0, False))
            self.assertEqual([r[1] for r in a.execute("PRAGMA table_info(view_spend_month)")][-1], "cost")  # untouched
            b.execute("COMMIT")
            n, done = of_spend.ingest(a, T0 + 86400, budget_s=2)
            self.assertTrue(done)
            self.assertEqual(a.execute("SELECT message_id, config_dir FROM view_spend_month").fetchall(),
                             [("m1", "/h/.claude-a")])                    # read again from the first record
        finally:
            shutil.rmtree(d)

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
