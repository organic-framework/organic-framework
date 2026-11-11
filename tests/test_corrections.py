"""The founder's corrections as records (RFC 0006 §2.4): judged from his prompts, recorded once, digested daily."""

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
import of_listen  # noqa: E402
import of_store  # noqa: E402

T0 = of_case._ts("2026-11-09T13:00:00.000Z")


def verdict(correction, rule="", kind="other", repeat_of=""):
    return subprocess.CompletedProcess([], 0, stdout=json.dumps(
        {"type": "result", "structured_output": {"correction": correction, "rule": rule, "kind": kind,
                                                 "repeat_of": repeat_of, "reason": "r"},
         "total_cost_usd": 0.002, "modelUsage": {"claude-haiku-5-5": {}}}), stderr="")


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

    def ev(self, kind, payload, position, minutes, org="acme"):
        rec = of_events.record(kind, payload, position=position, session=f"S-{position}", org=org)
        rec["timestamp"] = of_case._iso(T0 + minutes * 60)
        of_store.add_event(self.conn, rec)
        return rec["of.event.id"]

    def said(self, position, text, minutes, channel="herdr", org="acme"):
        return self.ev("human_prompt", {"origin": "human", "channel": channel, "text": text,
                                        "prompt_id": "uuid-" + text[:5]}, position, minutes, org)

    def recorded(self):
        return of_corrections.corrections(self.conn)


class CorrectionsTest(Store):
    def test_a_correction_is_recorded_once_at_his_prompt_with_its_repeat(self):
        self.ev("turn_end", {"last_message": "Рынок $2 687, наше $518."}, "thr", 0)
        first = self.said("thr", "Мне не нравится, как ты выдаёшь отчёт: где единицы?", 1)
        self.said("thr", "Answer from the founder to your question 1a2b3c4d: да", 2, channel="asks-ui")
        self.said("thr", "А конкуренты сколько заработали?", 3)
        seen = []

        def runner(argv, **kw):
            text = argv[argv.index("-p") + 1]
            seen.append(text)
            if "отчёт" in text and "единицы" in text:
                return verdict(True, "State every total with its unit.", "output")
            return verdict(False)
        out = of_corrections.judge(self.conn, now=T0 + 600, runner=runner)
        self.assertEqual(len(out), 2)  # the popup answer is not judged
        self.assertIn("Рынок $2 687", seen[0])  # what he reacted to
        rows = self.recorded()
        self.assertEqual([(r["position"], r["kind"], r["rule"], r["repeat_of"]) for r in rows],
                         [("thr", "output", "State every total with its unit.", None)])
        rec = self.conn.execute("SELECT ts, org, payload FROM event WHERE name = 'of.founder_correction'").fetchone()
        self.assertEqual(rec[0][:16], of_case._iso(T0 + 60)[:16])  # his prompt's time
        self.assertEqual(rec[1], "acme")
        self.assertEqual(json.loads(rec[2])["prompt_id"], first)  # of_studio counts one per prompt event
        self.assertEqual(of_corrections.judge(self.conn, now=T0 + 700, runner=runner), [])  # judged once
        self.said("thr", "Опять непонятный отчёт, где единицы?", 20)
        rid = rows[0]["event_id"]

        def again(argv, **kw):
            self.assertIn(f"{rid[-8:]}: State every total with its unit.", argv[argv.index("-p") + 1])
            return verdict(True, "Label every total with its unit.", "output", rid[-8:])
        of_corrections.judge(self.conn, now=T0 + 1800, runner=again)
        self.assertEqual(self.recorded()[-1]["repeat_of"], rid)  # the full id of the earlier one

    def test_a_failed_call_is_tried_again(self):
        self.said("thr", "Почему только Франкфурт?", 1)

        def boom(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 1)
        logged = []
        of_corrections.judge(self.conn, now=T0 + 300, runner=boom, log=logged.append)
        self.assertEqual(len(logged), 1)
        out = of_corrections.judge(self.conn, now=T0 + 400, runner=lambda *a, **k: verdict(False))
        self.assertEqual(len(out), 1)

    def test_one_digest_a_day_per_organization_to_its_keeper(self):
        positions = [{"name": "acme_org", "role": "org", "_org": "acme", "_orgid": "acme"},
                     {"name": "thr", "role": "owner", "_org": "acme", "_orgid": "acme"},
                     {"name": "of_architect", "role": "architect", "_org": "ofb", "_orgid": "ofb"}]
        self.said("thr", "Где единицы?", 1)
        self.said("thr", "Опять без единиц!", 2)
        n = iter([verdict(True, "State every total with its unit.", "output"),
                  verdict(True, "Label totals with units.", "output", "")])
        of_corrections.judge(self.conn, now=T0 + 300, runner=lambda *a, **k: next(n))
        sent = []

        def send(to, text, log):
            sent.append((to, text))
            return True
        early = T0 - 13 * 3600 + 4 * 3600  # 04:00Z: before the hour
        self.assertEqual(of_corrections.send_digests(self.conn, positions, now=early, send=send, issues_of=lambda o: []), 0)
        self.assertEqual(of_corrections.send_digests(self.conn, positions, now=T0 + 600, send=send, issues_of=lambda o: []), 1)
        self.assertEqual(sent[0][0], "acme:acme_org")  # ofb had nothing: nothing sent there
        self.assertIn("thr: 2", sent[0][1])
        self.assertIn("[output] State every total with its unit.", sent[0][1])
        self.assertNotIn("@", sent[0][1])
        self.assertEqual(of_corrections.send_digests(self.conn, positions, now=T0 + 3600, send=send, issues_of=lambda o: []), 0)  # once a day
        # judged after the digest, about a prompt before it: in the next day's digest
        self.said("thr", "И снова без единиц.", 5)
        of_corrections.judge(self.conn, now=T0 + 3700, runner=lambda *a, **k: verdict(True, "Units.", "output"))
        self.assertEqual(of_corrections.send_digests(self.conn, positions, now=T0 + 86400, send=send, issues_of=lambda o: []), 1)
        self.assertIn("thr: 1", sent[1][1])
        self.assertIn("Units.", sent[1][1])
        self.assertNotIn("State every total", sent[1][1])

    def test_structure_issues_go_in_the_same_digest_new_in_full_standing_in_one_line(self):
        """of_architect and of_field-analyst, 2026-11-10: structure issues reach the keeper in this digest."""
        positions = [{"name": "acme_org", "role": "org", "_org": "acme", "_orgid": "acme"},
                     {"name": "of_architect", "role": "architect", "_org": "ofb", "_orgid": "ofb"}]
        open_ = {"acme": [{"issue_id": "aaa111", "rule_version": "ap.unkept-system@0", "part": None,
                                 "summary": "delivery is live and has no keeper"},
                                {"issue_id": "bbb222", "rule_version": "ap.off-map-work@0", "part": "no_own_paths",
                                 "summary": "storefront_price-feed: 12 of 12 edits elsewhere"}], "ofb": []}
        sent = []
        send = lambda to, text, log: sent.append((to, text)) or True  # noqa: E731
        of_corrections.send_digests(self.conn, positions, now=T0, send=send, issues_of=lambda o: open_[o])
        self.assertEqual([to for to, _ in sent], ["acme:acme_org"])  # no corrections, no issues: nothing for ofb
        text = sent[0][1]
        self.assertTrue(text.startswith("Structure issues for you to decide"))
        self.assertIn("- [ap.off-map-work@0 no_own_paths] storefront_price-feed: 12 of 12 edits elsewhere (bbb222)", text)
        open_["acme"].append({"issue_id": "ccc333", "rule_version": "ap.cell-gap@0", "part": "nobody",
                                    "summary": "storefront x secure: nobody"})
        self.said("thr", "Где единицы?", 30)
        of_corrections.judge(self.conn, now=T0 + 3600, runner=lambda *a, **k: verdict(True, "Units.", "output"))
        of_corrections.send_digests(self.conn, positions, now=T0 + 86400, send=send, issues_of=lambda o: open_[o])
        text = sent[1][1]
        self.assertIn("thr: 1", text)  # the corrections first
        self.assertIn("(ccc333)", text)  # new: in full
        self.assertNotIn("(aaa111)", text)
        self.assertIn("2 still open from earlier digests: of model issues --org acme --for acme_org", text)
        # by rule, structural gaps first (of_field-analyst): unkept-system before off-map-work before cell-gap
        self.assertLess(text.index("unkept-system: 1, 0 new"), text.index("off-map-work: 1, 0 new"))
        self.assertLess(text.index("off-map-work: 1"), text.index("cell-gap: 1"))
        failed = lambda o: None  # noqa: E731  the computation failed: nothing said about structure
        self.assertEqual(of_corrections.send_digests(self.conn, positions, now=T0 + 2 * 86400, send=send,
                                                     issues_of=failed), 0)


class CorrectionsProcessorTest(Store):
    def test_it_judges_after_a_pause_and_digests_after_the_hour(self):
        p = of_listen.CorrectionsProcessor()
        started = []
        p.spawn = started.append
        cur = of_listen.prepare(self.conn, [p])
        night = T0 - 13 * 3600 + 2 * 3600  # 02:00Z
        self.said("thr", "Где единицы?", -11 * 60)  # 02:00Z
        of_listen.step(self.conn, [p], cur)
        p.tick(self.conn, night + 30)
        self.assertEqual(started, [])  # he may go on
        p.tick(self.conn, night + 61)
        self.assertEqual(started, ["judge"])
        p.tick(self.conn, night + 600)
        self.assertEqual(started, ["judge"])  # before the digest hour
        p.tick(self.conn, T0)
        self.assertEqual(started, ["judge", "digest"])
        p.tick(self.conn, T0 + 600)
        self.assertEqual(started, ["judge", "digest"])  # the next try in an hour (the child sends once a day)


if __name__ == "__main__":
    unittest.main()
