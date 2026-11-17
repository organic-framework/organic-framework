"""Work booked for later with no basis (bin/of_tempo.py; hand-over 277de578 from of_architect): the cheap filter, the
items judged once, the judge's record, and the pulse's note into the position's pane."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_case  # noqa: E402
import of_corrections  # noqa: E402
import of_events  # noqa: E402
import of_pulse  # noqa: E402
import of_store  # noqa: E402
import of_tempo  # noqa: E402

T0 = of_case._ts("2026-11-17T13:18:00.000Z")
POSITIONS = [
    {"name": "k8s_keeper", "_org": "acme", "_unit_owner": "platform_owner", "role": "keeper"},
    {"name": "platform_owner", "_org": "acme", "_unit_owner": "platform_owner", "role": "owner"},
    {"name": "acme_conductor", "_org": "acme", "role": "conductor"},
    {"name": "acme_org", "_org": "acme", "role": "org"},
    {"name": "obs_keeper", "_org": "acme", "_unit_owner": "platform_owner", "role": "keeper"},
    {"name": "alex", "_org": "acme", "role": "owner", "holder": {"runtime": "human"}},
]


def reply(*items):
    return subprocess.CompletedProcess([], 0, stdout=json.dumps(
        {"type": "result", "structured_output": {"items": [
            {"id": str(i), "booking": v != "not_a_booking", "basis_kind": k, "verdict": v, "basis": b, "reason": "r"}
            for i, (v, k, b) in enumerate(items, 1)]},
         "total_cost_usd": 0.001, "modelUsage": {"claude-haiku-5-5": {}}}), stderr="")


class FilterTest(unittest.TestCase):
    def test_times_ahead_of_the_item_and_later_days_only(self):
        ts = "2026-11-17T13:18:00Z"
        self.assertEqual(of_tempo.times_ahead("next check at 14:25Z, then 18:45 UTC", ts), ["14:25", "18:45"])
        self.assertEqual(of_tempo.times_ahead("it landed at 12:41:08Z and at 13:20Z", ts), [])  # a reading; too near
        self.assertEqual(of_tempo.times_ahead("deadline tomorrow 12:00Z", ts), ["tomorrow"])
        self.assertEqual(of_tempo.times_ahead("сделаю завтра", ts), ["завтра"])
        self.assertEqual(of_tempo.times_ahead("window 14:00–14:55Z", ts), ["14:00", "14:55"])

    def test_a_clock_wait_is_a_long_sleep_or_a_date_comparison(self):
        self.assertTrue(of_tempo.waits_on_clock("sleep 3600; ./run.sh"))
        self.assertTrue(of_tempo.waits_on_clock("sleep 20m && make"))
        self.assertFalse(of_tempo.waits_on_clock("while true; do sleep 45; done"))
        self.assertTrue(of_tempo.waits_on_clock('end=$(( $(date +%s) + 3000 )); while [ "$(date +%s)" -lt "$end" ]; '
                                                'do sleep 45; done'))
        self.assertFalse(of_tempo.waits_on_clock("gh run watch 4409 --exit-status"))
        self.assertTrue(of_tempo.waits_on_clock('until [ "$(date -u +%Y%m%d%H%M%S)" -ge 20261117184500 ]; do sleep 30; '
                                                'done; ./apply.sh'))   # a user organization's "arm item 7 for 18:45Z"
        self.assertTrue(of_tempo.waits_on_clock("sleep $(( 1763400000 - $(date +%s) ))"))
        self.assertTrue(of_tempo.waits_on_clock('[[ "$(date -u +%H%M)" > 1845 ]] && run'))
        self.assertFalse(of_tempo.waits_on_clock("echo $(date) > run.log"))   # a redirection, not a comparison

    def test_items_new_crons_clock_waits_messages_up_and_asks_each_once(self):
        def ev(name, payload, position, ts="2026-11-17T13:18:00Z", eid=None):
            return {"rowid": 1, "event_id": eid or f"e-{name}-{position}", "ts": ts, "name": name,
                    "session_id": f"S-{position}", "position": position, "org": "acme", "payload": payload}
        rows = [
            ev("of.continuation", {"session_crons": [{"id": "c1", "schedule": "25 14 * * *", "prompt": "run step 2"}],
                                   "background_tasks": [
                                       {"id": "b1", "type": "shell", "command": "sleep 4000; ./step2.sh"},
                                       {"id": "b2", "type": "shell", "command": "gh run watch 1"}]}, "k8s_keeper"),
            ev("of.continuation", {"session_crons": [{"id": "c1", "schedule": "25 14 * * *"}]}, "k8s_keeper"),
            ev("of.agent_message_in", {"from": "acme:k8s_keeper", "text": "I'll roll it at 14:25Z."}, "platform_owner",
               eid="m1"),
            ev("of.agent_message_in", {"from": "k8s_keeper", "text": "Again: 14:25Z, and 18:45Z."}, "platform_owner",
               eid="m2"),
            ev("of.agent_message_in", {"from": "k8s_keeper", "text": "at 15:00Z"}, "obs_keeper"),       # sideways
            ev("of.agent_question", {"detected_by": "declared", "to": "founder",
                                     "questions": [{"question": "Ship it tomorrow?"}]}, "obs_keeper", eid="a1"),
        ]
        got = of_tempo.items(rows, POSITIONS, before=lambda sid, rid, ts: set())   # an empty manifest before them
        self.assertEqual([(i["source"], i["position"], i.get("times")) for i in got], [
            ("cron", "k8s_keeper", []), ("clock_wait", "k8s_keeper", []),
            ("message", "k8s_keeper", ["14:25"]), ("message", "k8s_keeper", ["18:45"]),
            ("ask", "obs_keeper", ["tomorrow"])])
        self.assertEqual(got[2]["to"], "platform_owner")
        judged = {k for i in got for k in i.get("keys") or [i["key"]]}
        self.assertEqual(of_tempo.items(rows, POSITIONS, judged, before=lambda sid, rid, ts: set()), [])  # each once
        # a session's standing crons, booked before the judge started, are no new booking (2026-11-17, going live)
        standing = of_tempo.items(rows[:2], POSITIONS, before=lambda sid, rid, ts: {"c1", "b1"})
        self.assertEqual(standing, [])
        self.assertEqual(of_tempo.items(rows[:2], POSITIONS, before=lambda sid, rid, ts: None), [])  # unknown: none

    def test_one_call_per_position_and_a_basis_it_cannot_name_is_none(self):
        group = [{"key": "k1", "source": "cron", "position": "p", "ts": "2026-11-17T13:18:00Z", "ref": "c1",
                  "text": "schedule x"},
                 {"key": "k2", "source": "message", "position": "p", "ts": "2026-11-17T13:18:00Z", "ref": "m1",
                  "to": "o", "text": "at 15:00Z", "times": ["15:00"]}]
        prompts = []

        def runner(argv, **kw):
            prompts.append(argv[2])
            return reply(("basis", "none", ""), ("basis", "runner", "CI run 4409"))
        v = of_tempo.call(of_tempo.user_prompt("p", group), 2, runner=runner)
        self.assertEqual([x["verdict"] for x in v["verdicts"]], ["no_basis", "basis"])
        self.assertIn("Item 2, a message it sent to o", prompts[0])
        v = of_tempo.call("x", 3, runner=lambda argv, **kw: reply(("basis", "runner", "")))
        self.assertIn("no verdict for item 2", v["error"])


class JudgeTest(unittest.TestCase):
    def setUp(self):
        self.live = of_tempo.NOTES_LIVE
        of_tempo.NOTES_LIVE = True
        self.addCleanup(setattr, of_tempo, "NOTES_LIVE", self.live)
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

    def ev(self, kind, payload, position, minutes):
        rec = of_events.record(kind, payload, position=position, session=f"S-{position}", org="acme")
        rec["timestamp"] = of_case._iso(T0 + minutes * 60)
        of_store.add_event(self.conn, rec)
        return rec["of.event.id"]

    def judged(self):
        return [json.loads(p) for (p,) in self.conn.execute(
            "SELECT payload FROM event WHERE name = 'of.booking_judged' ORDER BY rowid")]

    def test_judged_once_recorded_and_typed_into_the_pane_once(self):
        self.ev("agent_message_in", {"from": "k8s_keeper", "text": "Older: at 23:00Z."}, "platform_owner", -1)
        self.assertEqual(of_tempo.judge(self.conn, POSITIONS, now=T0, runner=None, log=lambda m: None), [])  # starts now
        self.ev("agent_message_in", {"from": "k8s_keeper", "text": "Step 2 at 14:25Z, deadline tomorrow."},
                "platform_owner", 0)
        calls = []
        runner = lambda argv, **kw: calls.append(1) or reply(("no_basis", "none", ""))  # noqa: E731
        mem, typed = {}, []
        bind = lambda positions, agents: ([(p, {"pane_id": "w:1", "agent_status": "idle"})  # noqa: E731
                                           for p in positions if p["name"] == "k8s_keeper"], [])
        of_pulse.booking_notes(POSITIONS, [], bind, mem, T0, self.conn, prompt=lambda *a: True)  # from now
        out = of_tempo.judge(self.conn, POSITIONS, now=T0 + 120, runner=runner, log=lambda m: None)
        self.assertEqual(len(out), 1)
        self.assertEqual([x["verdict"] for x in self.judged()[0]["items"]], ["no_basis"])
        self.assertEqual(self.judged()[0]["items"][0]["times"], ["14:25", "tomorrow"])
        of_tempo.judge(self.conn, POSITIONS, now=T0 + 240, runner=runner, log=lambda m: None)
        self.assertEqual(len(calls), 1)                                        # nothing new: no call
        n = of_pulse.booking_notes(POSITIONS, [], bind, mem, T0 + 300, self.conn,
                                   prompt=lambda pane, text, s: typed.append((pane, text)) or True, log=lambda m: None)
        self.assertEqual(n, 1)
        self.assertEqual(typed[0][0], "w:1")
        self.assertIn("your message to platform_owner (later times: 14:25, tomorrow)", typed[0][1])
        self.assertIn("of status waiting --on", typed[0][1])
        of_pulse.booking_notes(POSITIONS, [], bind, mem, T0 + 600, self.conn, prompt=lambda *a: typed.append(a) or True)
        self.assertEqual(len(typed), 1)                                        # once

    def test_a_busy_pane_waits_a_person_gets_nothing_and_a_failed_call_is_judged_again(self):
        mem = {}
        busy = {"pane_id": "w:2", "agent_status": "working"}
        bind = lambda positions, agents: ([(p, busy) for p in positions], [])  # noqa: E731
        of_pulse.booking_notes(POSITIONS, [], bind, mem, T0, self.conn, prompt=lambda *a: True)
        of_tempo.judge(self.conn, POSITIONS, now=T0, runner=None, log=lambda m: None)
        self.ev("agent_question", {"detected_by": "declared", "to": "founder",
                                   "questions": [{"question": "Do it tomorrow?"}]}, "obs_keeper", 0)
        bad = lambda argv, **kw: subprocess.CompletedProcess([], 1, stdout="", stderr="x")  # noqa: E731
        of_tempo.judge(self.conn, POSITIONS, now=T0 + 120, runner=bad, log=lambda m: None)
        self.assertEqual(self.judged(), [])
        self.assertEqual(len(of_tempo.load_state()["retry"]), 1)
        of_tempo.judge(self.conn, POSITIONS, now=T0 + 240, runner=lambda argv, **kw: reply(("no_basis", "none", "")),
                       log=lambda m: None)
        self.assertEqual(len(self.judged()), 1)                                # judged at the next run
        typed = []
        of_pulse.booking_notes(POSITIONS, [], bind, mem, T0 + 300, self.conn,
                               prompt=lambda *a: typed.append(a) or True, log=lambda m: None)
        self.assertEqual(typed, [])                                            # busy: kept
        self.assertEqual(len(mem["tempo"]["pending"]), 1)
        busy["agent_status"] = "idle"
        of_pulse.booking_notes(POSITIONS, [], bind, mem, T0 + 600, self.conn,
                               prompt=lambda *a: typed.append(a) or True, log=lambda m: None)
        self.assertEqual(len(typed), 1)


class OutcomeTest(JudgeTest):
    def test_each_notes_outcome_ran_basis_or_neither(self):
        """of_architect, message 01a1ea20-1fa7: the live precision from each note's outcome."""
        def note(pos, items, minutes):
            j = self.ev("booking_judged", {"position": pos, "items": items}, pos, minutes)
            self.ev("nudge_sent", {"kind": "booking_no_basis", "judgement": j, "to_position": pos}, pos, minutes + 1)
        cron = {"source": "cron", "ref": "c1", "verdict": "no_basis", "ts": "2026-11-17T13:18:00Z",
                "quote": "schedule 45 23 17 11 *, recurring False: item 7"}
        note("k8s_keeper", [cron], 0)
        self.ev("continuation", {"session_crons": [], "background_tasks": []}, "k8s_keeper", 10)    # gone: ran
        note("obs_keeper", [dict(cron, ref="c2")], 0)
        self.ev("state_declared", {"state": "waiting", "waiting_on": "platform_owner"}, "obs_keeper", 20)
        note("acme_org", [{"source": "message", "ref": "m1", "verdict": "no_basis", "quote": "tomorrow"}], 0)
        self.ev("state_declared", {"state": "waiting", "waiting_on": "acme_org"}, "acme_org", 200)  # past the window
        # a wait whose own time came (13:25Z) and then left is no sign it ran early (of_review on PR 294)
        note("alex", [{"source": "clock_wait", "ref": "b1", "verdict": "no_basis", "ts": "2026-11-17T13:18:00Z",
                       "quote": 'Alarm: until [ "$(date -u +%Y%m%d%H%M%S)" -ge 20261117132500 ]; do sleep 30; done'}], 0)
        self.ev("continuation", {"session_crons": [], "background_tasks": []}, "alex", 10)            # 13:28Z
        got = {r["position"]: r["outcome"] for r in of_tempo.outcomes(self.conn, "2026-11-17T00:00:00Z")}
        self.assertEqual(got, {"k8s_keeper": "ran", "obs_keeper": "basis", "acme_org": "neither", "alex": "neither"})

    def test_an_items_own_booked_time(self):
        at, rec = of_tempo.booked_at({"source": "clock_wait", "ts": "2026-11-17T13:18:00Z",
                                      "quote": "until [ $(date -u +%Y%m%d%H%M) -ge 202611171450 ]; do sleep 60; done"})
        self.assertEqual((of_tempo._iso(at), rec), ("2026-11-17T14:50:00Z", False))
        at, _ = of_tempo.booked_at({"source": "clock_wait", "ts": "2026-11-17T13:18:00Z",
                                    "quote": "t=$(date -u -j -f X '2026-11-17T14:03:00Z' +%s); sleep $(( t - 1 ))"})
        self.assertEqual(of_tempo._iso(at), "2026-11-17T14:03:00Z")
        at, _ = of_tempo.booked_at({"source": "clock_wait", "ts": "2026-11-17T13:18:00Z", "quote": "sleep 3600; go"})
        self.assertEqual(of_tempo._iso(at), "2026-11-17T14:18:00Z")
        self.assertEqual(of_tempo.booked_at({"source": "cron", "quote": "schedule 17 * * * *, recurring True: x"}),
                         (None, True))
        at, _ = of_tempo.booked_at({"source": "clock_wait", "ts": "2026-11-17T13:18:00Z",   # of_review on PR 294
                                    "quote": 'while [ "$(date -u +%H%M)" -lt 1450 ]; do sleep 30; done'})
        self.assertEqual(of_tempo._iso(at), "2026-11-17T14:50:00Z")
        at, _ = of_tempo.booked_at({"source": "clock_wait", "ts": "2026-11-17T13:18:00Z",   # the machine's local time
                                    "quote": 'until [ "$(date +%H%M)" -ge 1850 ]; do sleep 30; done'})
        import time as _t
        self.assertEqual(_t.strftime("%H:%M", _t.localtime(at)), "18:50")

    def test_a_new_sessions_first_manifest_is_all_new(self):
        """of_review on PR 294: a session that started after the judge's first run had its first manifest skipped."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        conn = of_store.connect(os.path.join(tmp.name, "of.db"))
        self.addCleanup(conn.close)
        for kind, sid in (("pulse", "S-0"), ("session_start", "S-new")):
            rec = of_events.record(kind, {}, position="p", session=sid)
            of_store.add_event(conn, rec)
        began = conn.execute("SELECT rowid FROM event WHERE name = 'of.pulse'").fetchone()[0]
        self.assertEqual(of_tempo.before(conn, "S-new", 10 ** 9, "9999", began), set())
        self.assertIsNone(of_tempo.before(conn, "S-old", 10 ** 9, "9999", began))


class PassedTest(JudgeTest):
    def test_a_booking_whose_time_passed_is_never_typed(self):
        """2026-11-17: 9 of 17 notes were typed after their booked time, while the listener was down."""
        mem, typed = {}, []
        bind = lambda positions, agents: ([(p, {"pane_id": "w:1", "agent_status": "idle"})  # noqa: E731
                                           for p in positions if p["name"] == "k8s_keeper"], [])
        of_pulse.booking_notes(POSITIONS, [], bind, mem, T0, self.conn, prompt=lambda *a: True)
        wait = {"source": "clock_wait", "ref": "b1", "verdict": "no_basis", "ts": "2026-11-17T13:18:00Z",
                "quote": 'Alarm: until [ "$(date -u +%Y%m%d%H%M)" -ge 202611171330 ]; do sleep 30; done'}
        self.ev("booking_judged", {"position": "k8s_keeper", "items": [wait]}, "k8s_keeper", 1)
        of_pulse.booking_notes(POSITIONS, [], bind, mem, T0 + 20 * 60, self.conn,     # 13:38Z: past 13:30Z
                               prompt=lambda *a: typed.append(a) or True, log=lambda m: None)
        self.assertEqual(typed, [])


class SampleTest(JudgeTest):
    def test_a_blind_sample_hides_the_verdicts_and_scores_against_labels(self):
        """of_architect, message 01a1eb01-4c6f: precision from a labelled sample of each day's judged items, labelled
        before the verdicts are seen."""
        items = [{"key": f"k{i}", "source": "cron", "quote": f"q{i}", "ts": "2026-11-17T13:00:00Z",
                  "verdict": "no_basis" if i < 4 else "basis"} for i in range(8)]
        self.ev("booking_judged", {"position": "p", "items": items}, "p", 0)
        rows = of_tempo.sample(self.conn, "2026-11-17", flagged=3, passed=2, seed=1)
        self.assertEqual(len(rows), 5)
        self.assertTrue(all("verdict" not in r and r["label"] == "" for r in rows))     # blind
        labels = [{"key": k, "label": lab, "ts": "2026-11-17T13:00:00Z"} for k, lab in
                  (("k0", "no_basis"), ("k1", "passes"), ("k5", "no_basis"), ("k6", "passes"))]
        self.assertEqual(of_tempo.score(self.conn, labels), (0.5, 0.5, 2, 4))


class TempoKindTest(unittest.TestCase):
    def test_the_digest_counts_pushes_on_tempo(self):
        rows = [{"position": "k8s_keeper", "ts": "2026-11-17T13:00:00Z", "kind": "tempo", "rule": "Start when ready.",
                 "repeat_of": None, "event_id": "e1"},
                {"position": "k8s_keeper", "ts": "2026-11-17T13:30:00Z", "kind": "output", "rule": "Units.",
                 "repeat_of": None, "event_id": "e2"}]
        self.assertIn("k8s_keeper: 2, 1 on tempo (work put off)", of_corrections.digest_text(rows))
        self.assertIn("tempo", of_corrections.KINDS)



class ProcessorTest(unittest.TestCase):
    def test_a_busy_organization_still_gets_its_runs(self):
        """of_review on organic-framework PR 288: events of these names come at every turn end, so a quiet period never
        came and the judge seldom started."""
        import of_listen
        p, started = of_listen.TempoProcessor(), []
        import subprocess as sp
        saved, state = sp.Popen, of_events.STATE_DIR
        sp.Popen = lambda *a, **k: started.append(a)
        tmp = tempfile.TemporaryDirectory()
        of_events.STATE_DIR = tmp.name
        try:
            for t in range(0, 600, 30):                     # an event every 30 s, a tick every 30 s
                p.handle(None, [{"name": "of.continuation"}])
                p.tick(None, 1000.0 + t)
        finally:
            sp.Popen, of_events.STATE_DIR = saved, state
            tmp.cleanup()
        self.assertEqual(len(started), 5)                   # every GAP_S (120 s) over ten minutes


if __name__ == "__main__":
    unittest.main()
