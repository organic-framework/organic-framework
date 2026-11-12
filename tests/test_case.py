"""The case desk (docs/desk.md): the founder's case from the log, and a router that fills the desk from it without
moving what he placed, what he types to, or the focused pane (founder, 2026-11-09)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_case  # noqa: E402
import of_desk  # noqa: E402
import of_events  # noqa: E402
import of_listen  # noqa: E402
import of_store  # noqa: E402

T0 = of_case._ts("2026-11-09T13:00:00.000Z")


def at(minutes):
    return of_case._iso(T0 + minutes * 60)


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = self.tmp.name
        of_events._CONN.clear()
        self.conn = of_store.connect(os.path.join(self.tmp.name, "of.db"))

    def tearDown(self):
        self.conn.close()
        of_events._CONN.clear()
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def ev(self, kind, payload, position=None, minutes=0):
        rec = of_events.record(kind, payload, position=position, session=f"S-{position}", org="acme")
        rec["timestamp"] = at(minutes)
        of_store.add_event(self.conn, rec)
        return rec["of.event.id"]

    def said(self, position, text, minutes, channel="herdr", origin="human"):
        return self.ev("human_prompt", {"origin": origin, "channel": channel, "text": text}, position, minutes)


POSITIONS = [{"name": n, "summary": s, "_orgid": "acme", "_unit": "shop"} for n, s in (
    ("acme_throughput-owner", "owner of the money units"), ("accounting_keeper", "Accountant Dog/Spy"),
    ("storefront_checkout", "nonces and wallets"), ("forecasting_keeper", "AI factory"),
    ("platform_owner", "owner of the platform unit"))]


def reply(cases, cost=0.004):
    return subprocess.CompletedProcess([], 0, stdout=json.dumps(
        {"type": "result", "structured_output": {"cases": cases}, "total_cost_usd": cost,
         "modelUsage": {"claude-haiku-5-5": {}}}), stderr="")


class DigestTest(Store):
    def test_his_typed_prompts_with_answers_and_the_agents_traffic_make_the_digest(self):
        self.said("acme_throughput-owner", "Откуда этот всплеск? Попроси accounting keeper посчитать.", 1)
        self.ev("turn_end", {"last_message": "Спросил accounting_keeper про P&L конкурентов."}, "acme_throughput-owner", 2)
        self.said("platform_owner", "Answer from the founder to your question 1a2b3c4d: да", 3, channel="asks-ui")
        self.said("forecasting_keeper", "Answer from the founder to your question 5e6f7a8b: да", 3)
        self.said("storefront_checkout", "status", 4, origin="scheduled")
        self.ev("agent_message_out", {"to_position": "accounting_keeper", "chars": 900}, "acme_throughput-owner", 5)
        self.ev("agent_message_out", {"to_position": "acme_throughput-owner", "chars": 2100}, "accounting_keeper", 6)
        self.ev("agent_message_out", {"to_position": "platform_owner", "chars": 300}, "forecasting_keeper", 6)
        batch = ("Message from acme:accounting_keeper (via relay, authority: none: no grant; machine m1; msg "
                 "01a, reply to 01b) accounting_keeper: P&L конкурентов, всплеск №4: $2 169.\n\n"
                 "Message from acme:storefront_checkout (via relay, authority: none; msg 01c) Taken, first in queue.")
        self.ev("agent_message_in", {"text": batch, "delivery": None}, "acme_throughput-owner", 7)
        d = of_case.digest(self.conn, POSITIONS, now=T0 + 10 * 60)
        self.assertEqual([p["position"] for p in d["prompts"]], ["acme_throughput-owner"])  # no popup answers, no robots
        self.assertEqual(d["prompts"][0]["answer"], "Спросил accounting_keeper про P&L конкурентов.")
        self.assertEqual(d["focal"], ["acme_throughput-owner"])
        self.assertEqual(d["traffic"], {("acme_throughput-owner", "accounting_keeper"): {"n": 2, "chars": 3000}})
        self.assertEqual([(m["sender"], m["text"][:15]) for m in d["messages"]],
                         [("accounting_keeper", "P&L конкурентов"), ("storefront_checkout", "Taken, first in")])
        text = of_case.render(d)
        self.assertIn("to acme_throughput-owner: Откуда этот всплеск?", text)
        self.assertIn("acme_throughput-owner <-> accounting_keeper: 2 messages, 3 KB", text)
        self.assertIn("- platform_owner: owner of the platform unit (acme/shop)", text)

    def test_no_prompts_no_inference(self):
        self.said("acme_throughput-owner", "старое", -200)
        ran = []
        self.assertIsNone(of_case.infer(self.conn, POSITIONS, now=T0, runner=lambda *a, **k: ran.append(1)))
        self.assertEqual(ran, [])

    def test_an_inference_is_recorded_and_read_back(self):
        self.said("acme_throughput-owner", "Откуда этот всплеск?", 1)
        seen = {}

        def runner(argv, **kw):
            seen["argv"], seen["env"] = argv, kw["env"]
            return reply([{"title": "Всплеск #4", "summary": "Где деньги.", "members": [
                {"position": "acme_throughput-owner", "weight": 1, "why": "главный"},
                {"position": "accounting_keeper", "weight": 0.8, "why": "P&L"}]}])
        r = of_case.infer(self.conn, POSITIONS, now=T0 + 120, runner=runner, trigger="prompt")
        self.assertEqual(r["cases"][0]["title"], "Всплеск #4")
        self.assertEqual(seen["argv"][seen["argv"].index("--tools") + 1], "")  # no tools for the model
        self.assertFalse(any(k.startswith("HERDR_") for k in seen["env"]))  # never re-binds a pane
        self.assertEqual(seen["env"]["OF_AUTOMATION"], "1")
        got = of_case.latest(self.conn, now=T0 + 180)
        self.assertEqual([m["position"] for m in got["cases"][0]["members"]], ["acme_throughput-owner", "accounting_keeper"])
        self.assertEqual((got["trigger"], got["prompts"], got["version"]), ("prompt", 1, of_case.VERSION))

    def test_a_failed_call_is_reported_not_recorded(self):
        self.said("acme_throughput-owner", "Откуда этот всплеск?", 1)

        def runner(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 1)
        logged = []
        r = of_case.infer(self.conn, POSITIONS, now=T0 + 120, runner=runner, log=logged.append)
        self.assertIn("timed out", r["error"])
        self.assertIsNone(of_case.latest(self.conn, now=T0 + 180))
        self.assertEqual(len(logged), 1)


class CleanTest(unittest.TestCase):
    def test_only_roster_names_bounded_weights_no_duplicates_and_no_empty_cases(self):
        names = {"a", "b", "c"}
        got = of_case.clean([
            {"title": "T1", "summary": "s", "members": [
                {"position": "b", "weight": 0.4, "why": "x"}, {"position": "a", "weight": 7, "why": "y"},
                {"position": "zz", "weight": 1, "why": "not a position"}, {"position": "a", "weight": 0.1, "why": "dup"},
                {"position": "c", "weight": "much", "why": "z"}]},
            {"title": "T2", "summary": "s", "members": [{"position": "nobody", "weight": 1, "why": "-"}]}], names)
        self.assertEqual(len(got), 1)
        self.assertEqual([(m["position"], m["weight"]) for m in got[0]["members"]], [("a", 1.0), ("c", 0.5), ("b", 0.4)])

    def test_parse_reads_structured_output_and_rejects_a_reply_without_cases(self):
        j, meta = of_case.parse(reply([]).stdout)
        self.assertEqual((j["cases"], meta["model"]), ([], "claude-haiku-5-5"))
        with self.assertRaises(ValueError):
            of_case.parse('{"type": "result", "is_error": true, "result": "limit"}')
        with self.assertRaises(ValueError):
            of_case.parse("no json here")


CFG = dict(of_desk.DEFAULTS, slots=3)
NOW = 10_000.0
CASES = [{"title": "Всплеск #4", "members": [{"position": "thr", "weight": 1.0, "why": "главный"},
                                             {"position": "acc", "weight": 0.8, "why": "P&L"},
                                             {"position": "nonce", "weight": 0.7, "why": "кошельки"},
                                             {"position": "minor", "weight": 0.2, "why": "мелочь"}]},
         {"title": "Правила агентов", "members": [{"position": "org", "weight": 1.0, "why": "главный"}]}]


def v(name, status="idle", focused=False, ask=False):
    return {"pane": "p-" + name, "name": name, "status": status, "dialog": status == "blocked", "ask": ask,
            "focused": focused}


def plan(desk, off, mine=(), placed=None, removed=None, touched=(), cases=CASES, cfg=CFG, now=NOW):
    acts = of_desk.plan_desk(desk, off, cases, set(mine), placed or {}, removed or {}, set(touched), now, cfg)
    return [(a[0], a[1]["name"]) + ((a[2]["name"],) if len(a) > 2 else ()) for a in acts]


class DeskPlanTest(unittest.TestCase):
    def test_case_members_fill_free_slots_by_score_and_weak_ones_stay_off(self):
        self.assertEqual(plan([v("mine")], [v("minor"), v("nonce"), v("acc"), v("thr"), v("other", "done")],
                              mine={"mine"}),
                         [("add", "thr"), ("add", "acc")])

    def test_his_placements_the_focused_pane_and_agents_he_typed_to_are_never_replaced(self):
        old = NOW - 3600
        desk = [v("his"), v("focus", focused=True), v("typed")]
        placed = {"focus": old, "typed": old}
        self.assertEqual(plan(desk, [v("thr")], mine={"his"}, placed=placed, touched={"typed"}), [])

    def test_a_router_placement_gives_way_to_a_clearly_stronger_member_after_its_minimum_stay(self):
        desk = [v("his"), v("old"), v("org")]  # org: the second case, 1.0 x 0.6 = 0.6
        placed = {"old": NOW - 3600, "org": NOW - 3600}
        self.assertEqual(plan(desk, [v("thr"), v("acc"), v("nonce")], mine={"his"}, placed=placed),
                         [("swap", "thr", "old"), ("swap", "acc", "org")])  # acc 0.8 beats org 0.6 by 0.2; nonce: none left
        self.assertEqual(plan(desk, [v("nonce")], mine={"his"}, placed={"org": NOW - 3600}), [])  # 0.7 vs 0.6: stays
        placed = {"old": NOW - 60, "org": NOW - 3600}  # old came a minute ago: it stays
        self.assertEqual(plan(desk, [v("thr")], mine={"his"}, placed=placed), [("swap", "thr", "org")])

    def test_what_he_types_to_or_looks_at_off_the_desk_is_not_pulled_onto_it(self):
        self.assertEqual(plan([], [v("thr", focused=True), v("acc"), v("nonce")], touched={"acc"}),
                         [("add", "nonce")])

    def test_what_he_removed_stays_off_for_this_case(self):
        removed = {"thr": {"case": "Всплеск #4", "at": NOW - 5 * 3600}}
        self.assertEqual(plan([], [v("thr"), v("acc")], removed=removed), [("add", "acc")])
        removed = {"thr": {"case": "Другое дело", "at": NOW - 5 * 3600}}
        self.assertEqual(plan([], [v("thr")], removed=removed), [("add", "thr")])  # a new case, hours later
        removed = {"thr": {"case": "Другое дело", "at": NOW - 600}}
        self.assertEqual(plan([], [v("thr")], removed=removed), [])  # minutes ago: still off

    def test_a_dialog_off_the_desk_is_a_notice_and_an_open_ask_or_a_finished_turn_is_nothing(self):
        self.assertEqual(plan([v("a"), v("b"), v("c")], [v("x", "blocked"), v("y", "done"), v("z", ask=True)],
                              mine={"a", "b", "c"}),
                         [("notify", "x")])

    def test_no_case_no_moves(self):
        self.assertEqual(plan([], [v("thr"), v("x", "done")], cases=[]), [])


class TrackTest(Store):
    def test_his_placements_his_removals_and_the_old_routers_placements(self):
        self.ev("attention_routed", {"action": "bring_in", "pane": "p1", "status": "done"}, "k8s", 50)
        self.ev("attention_routed", {"action": "bring_in", "pane": "p2", "status": "blocked", "by": "owner"}, "put", 50)
        self.ev("attention_routed", {"action": "bring_in", "pane": "p3", "status": "done"}, "kept", -3 * 24 * 60)
        st = {"pinned": ["pin"]}
        now = T0 + 3600
        mine, placed, removed = of_desk.track(st, {"k8s", "put", "his", "kept"}, "Всплеск #4", now, self.conn)
        self.assertEqual(set(placed), {"k8s"})  # the old router brought it 10 minutes ago; the rest is his
        self.assertEqual(mine, {"put", "his", "pin", "kept"})  # kept: brought in days ago and kept there
        mine, placed, removed = of_desk.track(st, {"put", "his"}, "Всплеск #4", NOW + 5)
        self.assertEqual((placed, removed), ({}, {"k8s": {"case": "Всплеск #4", "at": NOW + 5}}))  # he took it off
        mine, placed, removed = of_desk.track(st, {"put", "his", "k8s"}, "Всплеск #4", NOW + 9)
        self.assertEqual((removed, "k8s" in mine), ({}, True))  # he put it back: his now


class CaseProcessorTest(Store):
    def test_it_infers_after_his_prompt_once_he_pauses_and_refreshes_while_he_works(self):
        p = of_listen.CaseProcessor()
        started = []
        p.spawn = started.append
        cur = of_listen.prepare(self.conn, [p])
        self.said("platform_owner", "Answer from the founder to your question 1a2b3c4d: да", 0, channel="asks-ui")
        of_listen.step(self.conn, [p], cur)
        self.assertFalse(p.tick(self.conn, T0 + 600))  # a popup answer is not a case
        self.said("acme_throughput-owner", "Откуда этот всплеск?", 1)
        of_listen.step(self.conn, [p], cur)
        t = T0 + 60
        p.tick(self.conn, t + 10)
        self.assertEqual(started, [])  # he may type again
        p.tick(self.conn, t + 31)
        self.assertEqual(started, ["prompt"])
        self.said("acme_throughput-owner", "А конкуренты?", 2)
        of_listen.step(self.conn, [p], cur)
        p.tick(self.conn, T0 + 120 + 31)
        self.assertEqual(started, ["prompt"])  # the last inference started under GAP_S ago
        p.tick(self.conn, t + 31 + p.GAP_S)
        self.assertEqual(started, ["prompt", "prompt"])
        p.tick(self.conn, t + 31 + p.GAP_S + p.REFRESH_S - 1)
        self.assertEqual(len(started), 2)
        p.tick(self.conn, t + 31 + p.GAP_S + p.REFRESH_S)
        self.assertEqual(started[-1], "refresh")  # he typed under ACTIVE_S ago
        n = len(started)
        p.tick(self.conn, T0 + 120 + p.ACTIVE_S + 3 * p.REFRESH_S)
        self.assertEqual(len(started), n)  # he stopped: the last case stays


if __name__ == "__main__":
    unittest.main()
