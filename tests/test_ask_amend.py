"""An open ask corrected in place (`of ask --amend`, docs/events.md of.ask_amended; hand-over 54de6cd9 from of_architect,
on of_field-analyst's review of a user organization's delegated unit, message 01a1dba9-6182): one decision stays one
card, at its newest version, marked amended, with its filing time; an answer records the version it answered, and one
given to a version whose options or yes changed since is refused."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_asks  # noqa: E402
import of_asks_answer  # noqa: E402
import of_asks_chat  # noqa: E402
import of_asks_tui  # noqa: E402
import of_coord  # noqa: E402
import of_events  # noqa: E402
import of_locale  # noqa: E402
import of_store  # noqa: E402
from tests.test_asks_chat import POSITIONS, ChatTest, now_of  # noqa: E402

OF = os.path.join(HERE, "..", "bin", "of")


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = self.tmp.name
        of_events._CONN.clear()
        self.conn = of_store.connect(os.path.join(self.tmp.name, "of.db"))
        self.saved_live = of_asks_answer.live_session
        of_asks_answer.live_session = lambda position, positions: None  # no herdr in a test
        self.typed = []
        self.saved_deliver = of_asks_answer.deliver
        of_asks_answer.deliver = lambda pane, text, session, **k: self.typed.append((pane, text)) or "delivered"

    def tearDown(self):
        of_asks_answer.deliver = self.saved_deliver
        of_asks_answer.live_session = self.saved_live
        self.conn.close()
        of_events._CONN.clear()
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def add(self, kind, payload, ts, position="of_a", session="S1"):
        rec = of_events.record(kind, payload, position=position, session=session, org="ofb")
        rec["timestamp"] = ts
        of_store.add_event(self.conn, rec)
        return rec["of.event.id"]

    def ask(self, text="Deploy the relay today?", options=("yes", "no"), ts="2026-11-15T10:00:00Z", **extra):
        return self.add("agent_question", {"detected_by": "declared", "from_position": "of_a", "to": "founder",
                                           "questions": [{"question": text, "options": list(options)}],
                                           "topic_key": "k1", **extra}, ts)

    def amend(self, aid, version, ts, by="of_a", **changes):
        return self.add("ask_amended", {"ask_id": aid, "version": version, "changed": list(changes), **changes,
                                        "by": by}, ts, position=by)

    def asks(self):
        return {a["event_id"]: a for a in of_asks.project(of_asks.load(self.conn))}


class ProjectionTest(Store):
    def test_a_new_version_keeps_the_id_and_the_filing_time(self):
        aid = self.ask()
        self.amend(aid, 2, "2026-11-15T10:30:00Z", text="Deploy the relay tonight?", options=["yes", "no", "Friday"],
                   tags=["prod_change:relay"], due="2026-11-15T20:00:00Z", blocking=True, topic_key="k2")
        a = self.asks()[aid]
        self.assertEqual((a["ts"], a["state"], a["text"], a["n_options"], a["tags"], a["due"], a["kind"]),
                         ("2026-11-15T10:00:00Z", "open", "Deploy the relay tonight?", 3, ["prod_change:relay"],
                          "2026-11-15T20:00:00Z", "question (declared) blocking"))
        self.assertEqual((a["version"], a["choice_version"], a["topic_key"]), (2, 2, "k2"))
        self.assertEqual(a["amended"]["changed"], ["text", "options", "tags", "due", "blocking"])
        self.amend(aid, 3, "2026-11-15T10:40:00Z", text="Deploy the relay tonight, after the backup?", blocking=False)
        a = self.asks()[aid]
        self.assertEqual((a["version"], a["choice_version"], a["kind"]), (3, 2, "question (declared)"))  # text: no choice
        self.assertEqual((a["amended"]["changed"], a["amended"]["all"]),
                         (["text", "blocking"], ["text", "options", "tags", "due", "blocking"]))
        self.assertEqual(len(of_asks.group([a])), 1)  # one decision, one card

    def test_only_its_asker_while_open_with_no_answer_and_only_newer(self):
        aid = self.ask()
        self.amend(aid, 2, "2026-11-15T10:10:00Z", by="of_b", text="Someone else's words?")
        self.amend(aid, 1, "2026-11-15T10:11:00Z", text="Not a newer version?")
        self.amend(aid, True, "2026-11-15T10:12:00Z", text="A boolean is no version?")
        self.assertNotIn("version", self.asks()[aid])
        self.add("agent_question_answered", {"ask_id": aid, "ask_ids": [aid], "answer": "yes", "via": "asks-ui",
                                             "by": "owner"}, "2026-11-15T10:20:00Z")
        self.amend(aid, 2, "2026-11-15T10:21:00Z", text="After the answer?")
        a = self.asks()[aid]
        self.assertEqual((a["state"], a["text"]), ("answered", "Deploy the relay today?"))
        task = self.ask("Rotate the key", options=(), task=True, steps="1. Open the vault.")
        self.amend(task, 2, "2026-11-15T10:30:00Z", task="1. Open the vault. 2. Rotate.")
        self.assertEqual(self.asks()[task]["steps"], "1. Open the vault. 2. Rotate.")
        self.add("agent_question_answered", {"ask_id": task, "ask_ids": [task], "answer": "done", "via": "asks-ui",
                                             "by": "owner"}, "2026-11-15T10:31:00Z")  # claimed: no more amends
        self.amend(task, 3, "2026-11-15T10:32:00Z", task="Changed after his done?")
        self.assertEqual(self.asks()[task]["version"], 2)

    def test_at_version_gives_each_version(self):
        aid = self.ask(yes_option=1, work="w")
        self.amend(aid, 2, "2026-11-15T10:10:00Z", options=["no", "yes"], yes_option=2)
        self.amend(aid, 3, "2026-11-15T10:20:00Z", text="Deploy the relay now?")
        self.amend(aid, 4, "2026-11-15T10:30:00Z", by="of_b", text="Not its asker")
        p0 = of_asks_answer.ask_details(self.conn, aid)[0]
        self.assertEqual((p0["version"], p0["choice_version"], of_asks_answer.options(p0), p0["yes_option"],
                          of_asks_answer.full_question(p0, "")), (3, 2, ["no", "yes"], 2, "Deploy the relay now?"))
        raw = json.loads(self.conn.execute("SELECT payload FROM event WHERE event_id = ?", (aid,)).fetchone()[0])
        v1 = of_asks.at_version(self.conn, aid, raw, "2026-11-15T10:00:00Z", "of_a", version=1)
        self.assertEqual((v1["version"], of_asks_answer.options(v1), v1["yes_option"], v1["amended"]),
                         (1, ["yes", "no"], 1, None))
        v2 = of_asks.at_version(self.conn, aid, raw, "2026-11-15T10:00:00Z", "of_a", version=2)
        self.assertEqual((of_asks_answer.full_question(v2, ""), v2["yes_option"]), ("Deploy the relay today?", 2))
        self.assertTrue(of_asks.stale_choice(None, 2) and of_asks.stale_choice(1, 2))
        self.assertFalse(of_asks.stale_choice(2, 2) or of_asks.stale_choice(3, 2) or of_asks.stale_choice(None, 1))

    def test_the_view_and_the_other_machines_take_it(self):
        self.assertTrue(of_asks.relevant("of.ask_amended", "of", {}))
        self.assertIn("of.ask_amended", of_asks.READS)
        self.assertIn("of.ask_amended", of_coord.EVENTS)


class AnswerTest(Store):
    def item(self, aid, version=None):
        return {"who": ["of_a"], "text": "q", "since": "2026-11-15T10:00:00Z", "ids": [aid],
                "kind": "question (declared)", **({"version": version} if version else {})}

    def answers(self):
        return [json.loads(p) for (p,) in self.conn.execute(
            "SELECT payload FROM event WHERE name = 'of.agent_question_answered'")]

    def test_the_card_is_the_newest_version_marked_amended(self):
        aid = self.ask()
        self.amend(aid, 2, "2026-11-15T10:10:00Z", options=["yes, tonight", "no"])
        c = of_asks_answer.card(self.conn, self.item(aid), [{"name": "of_a"}])
        self.assertEqual((c["options"], c["version"], c["choice_version"], c["amended"]["changed"]),
                         (["yes, tonight", "no"], 2, 2, ["options"]))
        of_locale.set_language("en")
        try:
            rows = "\n".join(t for t, _ in of_asks_tui.card_lines(dict(c, who=["of_a"], since=None), 100))
            self.assertIn("Amended (version 2, at 10:10Z): the options", rows)
        finally:
            of_locale.set_language(None)

    def test_an_answer_to_a_version_whose_options_changed_is_refused(self):
        aid = self.ask()
        self.amend(aid, 2, "2026-11-15T10:10:00Z", options=["yes, tonight", "no"])
        ok, status = of_asks_answer.answer(self.conn, self.item(aid, 1), "yes", [{"name": "of_a"}], lambda p: "p1")
        self.assertFalse(ok)
        self.assertTrue(status.startswith(of_asks_answer.AMENDED), status)
        self.assertEqual((self.answers(), self.typed), ([], []))  # nothing recorded, nothing typed
        draft = of_asks_answer.unsent_drafts()[0]  # the screen shows the card again, at its new version
        self.assertTrue(draft["failed_at"] and draft["why"].startswith(of_asks_answer.AMENDED))
        ok, status = of_asks_answer.answer(self.conn, self.item(aid), "yes", [{"name": "of_a"}], lambda p: "p1")
        self.assertFalse(ok)  # a card that carried no version showed the first
        ok, status = of_asks_answer.answer(self.conn, self.item(aid, 2), "yes, tonight", [{"name": "of_a"}],
                                           lambda p: "p1")
        self.assertTrue(ok, status)
        self.assertEqual([(a["answer"], a["version"]) for a in self.answers()], [("yes, tonight", 2)])

    def test_a_change_of_text_alone_keeps_the_answer(self):
        aid = self.ask()
        self.amend(aid, 2, "2026-11-15T10:10:00Z", text="Deploy the relay today, after the backup?")
        ok, status = of_asks_answer.answer(self.conn, self.item(aid, 1), "yes", [{"name": "of_a"}], lambda p: "p1")
        self.assertTrue(ok, status)
        self.assertEqual([(a["answer"], a["version"]) for a in self.answers()], [("yes", 1)])

    def test_the_outbox_drops_an_answer_refused_as_amended(self):
        aid = self.ask()
        self.amend(aid, 2, "2026-11-15T10:10:00Z", options=["yes, tonight", "no"])
        of_asks_tui.to_outbox([{"what": "answer", "text": "yes", "item": dict(self.item(aid, 1), written=True)}])
        with open(os.path.join(of_asks_tui.outbox_dir(), os.listdir(of_asks_tui.outbox_dir())[-1])) as fh:
            body = json.load(fh)
        self.assertEqual((body["item"]["version"], body["item"]["written"]), (1, True))

        def answer(it, text):
            return of_asks_answer.answer(self.conn, it, text, [{"name": "of_a"}], lambda p: None)
        of_asks_tui.send_outbox(answer, lambda *a: (True, ""), lambda ids: True)
        self.assertEqual([f for f in os.listdir(of_asks_tui.outbox_dir()) if f.endswith(".json")], [])  # never again
        draft = of_asks_answer.unsent_drafts()[0]  # its text waits as a draft the screen hands to r
        self.assertEqual((draft["text"], draft["item"]["written"], draft["why"][:7]), ("yes", True, "amended"))

    def test_the_unit_owner_gets_the_amend_framed_as_a_routed_question(self):
        frame = of_asks_answer.routed_frame("k8s_keeper", "platform", "01a1-aaaa-bbbbcccc", "Drain node 3 now?",
                                            ["yes", "no"], True, None, (2, ["text", "blocking"]))
        self.assertIn("ask bbbbcccc; amended to version 2 (text, blocking changed), blocking): Drain node 3 now?",
                      frame)
        self.assertTrue(of_asks.TOOL_FRAME.match(frame))  # a tool's text, never the person's words

    def test_a_routed_question_typed_late_is_its_newest_version(self):
        aid = self.add("agent_question", {"detected_by": "declared", "from_position": "k8s_keeper",
                                          "to": "plat_owner", "questions": [{"question": "Drain node 3?",
                                                                              "options": ["yes", "no"]}]},
                       "2026-11-15T10:00:00Z", position="k8s_keeper")
        self.amend(aid, 2, "2026-11-15T10:05:00Z", by="k8s_keeper", text="Drain node 3 and 4?")
        asks = list(self.asks().values())
        out = of_asks_answer.pending(self.conn, asks, [{"name": "k8s_keeper", "_unit": "platform"},
                                                       {"name": "plat_owner"}],
                                     now=of_asks_chat._epoch("2026-11-15T10:20:00Z"), min_age=0)
        routed = [x for x in out if x["key"].startswith("routed:")]
        self.assertEqual(len(routed), 1)
        self.assertIn("Drain node 3 and 4?", routed[0]["text"])


class ChatAmendTest(ChatTest):
    def amend(self, aid, version, ts, by="of_a", **changes):
        return self.add("ask_amended", {"ask_id": aid, "version": version, "changed": list(changes), **changes,
                                        "by": by}, ts, position=by)

    def test_the_chat_names_the_version_it_answers(self):
        aid = self.ask("Lift the hold?", ["yes", "no"], from_position="of_a")
        self.amend(aid, 2, "2026-11-11T10:02:00Z", options=["yes, today", "no"])
        late = self.prompt("yes, today", ts="2026-11-11T10:05:00Z")
        self.refused(aid, late, "--version 2", option=1)              # shown nothing newer than the first
        self.refused(aid, late, "--version 3: ask", option=1, version=3)  # no such version
        self.refused(aid, late, "a card's version is 1 or more", option=1, version=-1)
        c = self.check(aid, late, option=1, version=2)
        self.assertEqual((c["answer"], c["version"]), ("yes, today", 2))
        early = self.prompt("yes, lift it", ts="2026-11-11T10:01:00Z")
        self.refused(aid, early, "before version 2 of the card", option=1, version=2)
        view = of_asks_chat.chat_view(self.conn, self.asks())
        self.assertIn("amended: version 2 (options changed at 10:02Z; answer with --version 2)", view)
        self.assertIn("1) yes, today", view)
        of_asks_chat.record(c)
        p = json.loads(self.conn.execute("SELECT payload FROM event WHERE name = 'of.agent_question_answered'")
                       .fetchone()[0])
        self.assertEqual(p["version"], 2)

    def test_a_change_of_text_alone_needs_no_version(self):
        aid = self.ask("Lift the hold?", ["yes", "no"], from_position="of_a")
        self.amend(aid, 2, "2026-11-11T10:02:00Z", text="Lift the hold today?")
        c = self.check(aid, self.prompt("yes", ts="2026-11-11T10:05:00Z"), option=1)
        self.assertEqual((c["answer"], c["version"]), ("yes", 1))


REG = """units:
  platform:
    owner: plat_owner
    systems: [k8s]
delegations:
  - id: del-1
    unit: platform
    from_owner: founder
    to_owner: plat_owner
    reporting:
      escalate_when: [ask.blocking]
positions:
  - {name: k8s_keeper, system: k8s, role: keeper, kind: keeper, owner: founder}
  - {name: k8s_other, system: k8s, role: other, kind: keeper, owner: founder}
  - {name: plat_owner, system: platform, role: owner, kind: owner, owner: founder, unit: platform}
  - {name: of_a, system: of, role: a, kind: keeper, owner: founder}
  - {name: of_b, system: of, role: b, kind: keeper, owner: founder}
"""


class CliTest(unittest.TestCase):
    """End to end through bin/of: the amend, its refusals, of asks, and a routed ask escalated by its new blocking."""

    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        t = self.t.name
        os.makedirs(os.path.join(t, "org", "registry"))
        os.makedirs(os.path.join(t, "state"))
        with open(os.path.join(t, "org", "registry", "positions.yaml"), "w") as fh:
            fh.write(REG)
        with open(os.path.join(t, "orgs.yaml"), "w") as fh:
            fh.write(f"orgs:\n  - name: org\n    path: {t}/org\n")
        self.env = dict(os.environ, OF_STATE=os.path.join(t, "state"), OF_ORGS=os.path.join(t, "orgs.yaml"),
                        OF_CONFIG_DIR=t, PATH="/usr/bin:/bin")
        self.env.pop("CLAUDE_CODE_SESSION_ID", None)

    def tearDown(self):
        self.t.cleanup()

    def run_of(self, *a):
        return subprocess.run([sys.executable, OF, *a], env=self.env, capture_output=True, text=True, timeout=60)

    def events(self, name):
        import sqlite3
        db = sqlite3.connect(os.path.join(self.t.name, "state", "of.db"))
        try:
            return [(e, json.loads(p)) for e, p in db.execute("SELECT event_id, payload FROM event WHERE name = ? "
                                                               "ORDER BY rowid", (name,))]
        finally:
            db.close()

    def test_amend_and_its_refusals(self):
        r = self.run_of("ask", "Lift the hold on the relay?", "--options", "yes|no", "--from", "of_a")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        aid = self.events("of.agent_question")[0][0]
        short = aid[-8:]
        for args, needle in (
                (("--from", "of_b"), "only the position that filed an ask amends it"),
                (("--from", "of_a"), "nothing differs"),
                (("--from", "of_a", "--work", "w"), "--amend keeps the ask's work"),
                (("--from", "of_a", "--task", "1. Do it."), "is a question; work for the founder's hands"),
                (("--from", "of_a", "--options", "yes|no|later", "--yes", "1"), "--yes <n> marks which option"),
                (("--from", "of_a", "--options", "yes (about 2 hours of work)|no"), "refused: the ask estimates "
                 "time")):  # no estimate on his card, in a new version as in the first
            r = self.run_of("ask", "Lift the hold on the relay?", "--amend", short, *args)
            self.assertNotEqual(r.returncode, 0, (args, r.stdout))
            self.assertIn(needle, r.stderr, args)
        r = self.run_of("ask", "Lift the hold on the relay today?", "--amend", short, "--options", "yes, today|no",
                        "--blocking", "--reason", "the window moved", "--from", "of_a")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"amended ask {short}: version 2 (text, options, blocking changed)", r.stdout)
        (_, p), = self.events("of.ask_amended")
        self.assertEqual((p["ask_id"], p["version"], p["changed"], p["options"], p["blocking"], p["by"], p["reason"]),
                         (aid, 2, ["text", "options", "blocking"], ["yes, today", "no"], True, "of_a",
                          "the window moved"))
        self.assertTrue(p["topic_key"])
        r = self.run_of("ask", "Lift the hold on the relay today?", "--amend", short, "--no-blocking", "--from", "of_a")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.events("of.ask_amended")[-1][1]["version"], 3)
        r = self.run_of("asks")
        self.assertIn("Lift the hold on the relay today?  [amended v3: blocking]", r.stdout)
        self.assertEqual(len(self.events("of.agent_question")), 1)  # one decision, one card
        self.run_of("asks", "withdraw", short)
        r = self.run_of("ask", "Lift it now?", "--amend", short, "--from", "of_a")
        self.assertIn("is withdrawn: only an open ask with no answer is amended", r.stderr)

    def test_a_new_blocking_on_a_routed_ask_escalates_it_once(self):
        r = self.run_of("ask", "Drain node 3?", "--options", "yes|no", "--from", "k8s_keeper")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        aid = self.events("of.agent_question")[0][0]
        self.assertEqual(self.events("of.ask_escalated"), [])
        r = self.run_of("ask", "Drain node 3 now?", "--amend", aid[-8:], "--blocking", "--from", "k8s_keeper")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("escalated to the founder (ask.blocking)", r.stdout)
        self.assertIn("plat_owner has no live pane here: it sees the new version", r.stdout)
        (_, esc), = self.events("of.ask_escalated")
        self.assertEqual(esc["ask_id"], aid)
        copy = [e for e, p in self.events("of.agent_question") if p.get("escalated_from") == aid]
        self.assertEqual(len(copy), 1)
        r = self.run_of("ask", "Drain node 3 and 4 now?", "--amend", aid[-8:], "--tags", "irreversible",
                        "--from", "k8s_keeper")
        self.assertIn(f"was escalated to the founder; his card is its copy {copy[0][-8:]}: amend that one", r.stderr)
        self.assertEqual(len(self.events("of.ask_escalated")), 1)  # never twice


if __name__ == "__main__":
    unittest.main()
