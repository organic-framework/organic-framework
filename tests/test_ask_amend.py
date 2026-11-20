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

    def add(self, kind, payload, ts, position="of_a", session="S1", host=None, source="of"):
        rec = of_events.record(kind, payload, position=position, session=session, org="ofb", source=source)
        rec["timestamp"] = ts
        if host:  # an event another machine of the organization wrote, pulled from the coordination feed
            rec["host.id"] = host
        of_store.add_event(self.conn, rec)
        return rec["of.event.id"]

    def ask(self, text="Deploy the relay today?", options=("yes", "no"), ts="2026-11-15T10:00:00Z", **extra):
        return self.add("agent_question", {"detected_by": "declared", "from_position": "of_a", "to": "founder",
                                           "questions": [{"question": text, "options": list(options)}],
                                           "topic_key": "k1", **extra}, ts)

    def amend(self, aid, version, ts, by="of_a", host=None, **changes):
        return self.add("ask_amended", {"ask_id": aid, "version": version, "changed": list(changes), **changes,
                                        "by": by}, ts, position=by, host=host)

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
        self.assertEqual((a["version"], a["topic_key"]), (2, "k2"))
        self.assertEqual(a["amended"]["changed"], ["text", "options", "tags", "due", "blocking"])
        self.amend(aid, 3, "2026-11-15T10:40:00Z", text="Deploy the relay tonight, after the backup?", blocking=False)
        a = self.asks()[aid]
        self.assertEqual((a["version"], a["kind"]), (3, "question (declared)"))
        self.assertEqual((a["amended"]["changed"], a["amended"]["all"]),
                         (["text", "blocking"], ["text", "options", "tags", "due", "blocking"]))
        self.assertEqual(len(of_asks.group([a])), 1)  # one decision, one card

    def test_only_its_asker_while_open_with_no_answer_and_only_newer(self):
        aid = self.ask()
        self.amend(aid, 2, "2026-11-15T10:10:00Z", by="of_b", text="Someone else's words?")
        self.amend(aid, 1, "2026-11-15T10:11:00Z", text="Not a newer version?")
        self.amend(aid, True, "2026-11-15T10:12:00Z", text="A boolean is no version?")
        self.amend(aid, 2, "2026-11-15T10:13:00Z", host="another-machine", text="From another machine?")  # by: the
        # filer, written on a colleague's machine of the organization (of_server on PR 181)
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

    def test_a_tasks_command_and_where_are_amended_with_its_steps(self):
        """of_architect, message 01a1e8ed-cc9e: a task whose Homebrew command changed could only be withdrawn and filed
        again, a replacement card. Its command and where it is done are part of its steps."""
        task = self.ask("Install the runner", options=(), task=True, steps="1. Open a terminal.",
                        command="brew install runner", where="machine")
        self.amend(task, 2, "2026-11-15T10:10:00Z", command="curl -fsSL https://example.org/runner | sh")
        a = self.asks()[task]
        self.assertEqual((a["command"], a["where"], a["amended"]["changed"]),
                         ("curl -fsSL https://example.org/runner | sh", "machine", ["command"]))
        self.amend(task, 3, "2026-11-15T10:20:00Z", where="phone")  # a command stays: typed in a terminal
        self.assertEqual(self.asks()[task]["where"], "machine")
        self.amend(task, 4, "2026-11-15T10:30:00Z", command=None, where="browser", task="1. Open the page.")
        a = self.asks()[task]
        self.assertEqual((a["command"], a["where"], a["steps"], a["amended"]["all"]),
                         (None, "browser", "1. Open the page.", ["task", "command", "where"]))
        p = of_asks_answer.ask_details(self.conn, task)[0]
        self.assertEqual((p["version"], p["command"], p["where"], p["steps"]), (4, None, "browser", "1. Open the page."))
        raw = json.loads(self.conn.execute("SELECT payload FROM event WHERE event_id = ?", (task,)).fetchone()[0])
        v2 = of_asks.at_version(self.conn, task, raw, "2026-11-15T10:00:00Z", "of_a", of_asks.ask_host(self.conn, task),
                                version=2)
        self.assertEqual((v2["command"], v2["where"]), ("curl -fsSL https://example.org/runner | sh", "machine"))
        question = self.ask("Deploy the relay?", ts="2026-11-15T10:35:00Z", topic_key="k2")  # another decision
        self.amend(question, 2, "2026-11-15T10:40:00Z", command="rm -rf /tmp/x")  # a question has no command
        self.assertIsNone(self.asks()[question].get("command"))
        of_locale.set_language("en")
        try:
            self.assertEqual(of_asks_tui.amended_line({"amended": {"version": 4, "at": "2026-11-15T10:30:00Z",
                                                                   "changed": ["task", "command", "where"]}}, None),
                             "Amended (version 4, at 10:30Z): the steps, the command, where to do it")
        finally:
            of_locale.set_language(None)
        self.add("agent_question_answered", {"ask_id": task, "ask_ids": [task], "answer": "done", "via": "asks-ui",
                                             "by": "owner"}, "2026-11-15T10:50:00Z")  # he said done: no more amends
        self.amend(task, 5, "2026-11-15T10:51:00Z", command="brew install runner")
        self.assertIsNone(self.asks()[task]["command"])

    def test_at_version_gives_each_version(self):
        aid = self.ask(yes_option=1, work="w")
        self.amend(aid, 2, "2026-11-15T10:10:00Z", options=["no", "yes"], yes_option=2)
        self.amend(aid, 3, "2026-11-15T10:20:00Z", text="Deploy the relay now?")
        self.amend(aid, 4, "2026-11-15T10:30:00Z", by="of_b", text="Not its asker")
        self.amend(aid, 4, "2026-11-15T10:31:00Z", host="another-machine", text="Not its machine")
        p0 = of_asks_answer.ask_details(self.conn, aid)[0]
        self.assertEqual((p0["version"], of_asks_answer.options(p0), p0["yes_option"],
                          of_asks_answer.full_question(p0, "")), (3, ["no", "yes"], 2, "Deploy the relay now?"))
        raw = json.loads(self.conn.execute("SELECT payload FROM event WHERE event_id = ?", (aid,)).fetchone()[0])
        host = of_asks.ask_host(self.conn, aid)
        self.assertEqual(host, of_events.machine_id())
        v1 = of_asks.at_version(self.conn, aid, raw, "2026-11-15T10:00:00Z", "of_a", host, version=1)
        self.assertEqual((v1["version"], of_asks_answer.options(v1), v1["yes_option"], v1["amended"]),
                         (1, ["yes", "no"], 1, None))
        v2 = of_asks.at_version(self.conn, aid, raw, "2026-11-15T10:00:00Z", "of_a", host, version=2)
        self.assertEqual(of_asks.at_version(self.conn, aid, raw, "2026-11-15T10:00:00Z", "of_a", None)["version"], 1)
        self.assertEqual((of_asks_answer.full_question(v2, ""), v2["yes_option"]), ("Deploy the relay today?", 2))
        self.assertTrue(of_asks.stale(None, 2) and of_asks.stale(1, 2) and of_asks.stale(2, 3))
        self.assertFalse(of_asks.stale(2, 2) or of_asks.stale(3, 2) or of_asks.stale(None, 1))

    def test_an_amend_from_another_machine_is_ignored_everywhere(self):
        """of_server on PR 181: the feed brings every machine's of.ask_amended, and `by` is the sender's word."""
        aid = self.ask(yes_option=1, work="w")
        self.amend(aid, 2, "2026-11-15T10:10:00Z", host="another-machine", options=["no", "yes"], yes_option=2,
                   text="Rewritten elsewhere?")
        a = self.asks()[aid]
        self.assertEqual((a.get("version"), a["text"], a["host_id"]), (None, "Deploy the relay today?",
                                                                       of_events.machine_id()))
        p = of_asks_answer.ask_details(self.conn, aid)[0]
        self.assertEqual((p["version"], of_asks_answer.options(p), p["yes_option"]), (1, ["yes", "no"], 1))
        import of_asks_inbox
        g = of_asks.group([a])[0]
        self.assertIs(of_asks_inbox.amended_group(g, of_asks_inbox._amends(self.conn, of_asks_chat._epoch(
            "2026-11-15T11:00:00Z"))[aid]), g)
        self.amend(aid, 2, "2026-11-15T10:20:00Z", text="Deploy the relay tonight?")  # its own machine's: applies
        self.assertEqual(self.asks()[aid]["text"], "Deploy the relay tonight?")

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
        self.assertEqual((c["options"], c["version"], c["amended"]["changed"]), (["yes, tonight", "no"], 2, ["options"]))
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
        self.assertEqual(self.typed, [("p1", f"Answer from the founder to your question {aid[-8:]} (version 2: "
                                             f"\u00abDeploy the relay today?\u00bb){of_asks_answer._card_paren(aid)}: "
                                             "yes, tonight")])  # never bare; the card id after the version

    def test_a_change_of_text_alone_refuses_it_too(self):
        """of_review on PR 181: a text amend turned staging into production, and the yes given to staging reached the
        asker as a bare yes."""
        aid = self.ask("Drain node 3 of the staging cluster now?")
        self.amend(aid, 2, "2026-11-15T10:10:00Z", text="Drain node 3 of the production cluster now?")
        ok, status = of_asks_answer.answer(self.conn, self.item(aid, 1), "yes", [{"name": "of_a"}], lambda p: "p1")
        self.assertFalse(ok)
        self.assertTrue(status.startswith(of_asks_answer.AMENDED), status)
        self.assertEqual((self.answers(), self.typed), ([], []))
        ok, status = of_asks_answer.answer(self.conn, self.item(aid, 2), "yes", [{"name": "of_a"}], lambda p: "p1")
        self.assertTrue(ok, status)
        self.assertIn(f"(version 2: \u00abDrain node 3 of the production cluster now?\u00bb){of_asks_answer._card_paren(aid)}: "
                      "yes", self.typed[0][1])

    def test_an_answer_to_an_older_version_recorded_anyway_closes_nothing(self):
        """A path that cannot refuse it, or one that raced the amend: the newest version stays open on the card, and the
        asker gets the answer as one to version N, with that version's words (docs/events.md, of.ask_amended)."""
        aid = self.ask("Drain node 3 of the staging cluster now?")
        self.amend(aid, 2, "2026-11-15T10:10:00Z", text="Drain node 3 of the production cluster now?")
        self.add("agent_question_answered", {"ask_id": aid, "ask_ids": [aid], "answer": "yes", "via": "asks-ui",
                                             "by": "owner", "version": 1}, "2026-11-15T10:11:00Z")
        self.assertEqual(self.asks()[aid]["state"], "open")
        out = of_asks_answer.pending(self.conn, list(self.asks().values()), [{"name": "of_a"}],
                                     now=of_asks_chat._epoch("2026-11-15T10:30:00Z"), min_age=0)
        (typed,) = [x["text"] for x in out if x["key"].startswith("answer:")]
        self.assertTrue(typed.startswith(f"Answer from the founder to your question {aid[-8:]} (version 1: \u00abDrain "
                                         f"node 3 of the staging cluster now?\u00bb){of_asks_answer._card_paren(aid)}: yes"),
                        typed)
        self.add("human_prompt", {"origin": "human", "text": typed}, "2026-11-15T10:31:00Z",  # typed into its pane
                 source="hook")
        self.assertEqual(self.asks()[aid]["state"], "open")
        self.add("human_prompt", {"origin": "human", "text": f"Answer from the founder to your question {aid[-8:]} "
                                  "(version 2: \u00abDrain node 3 of the production cluster now?\u00bb): no"},
                 "2026-11-15T10:40:00Z", source="hook")
        self.assertEqual(self.asks()[aid]["state"], "answered")
        other = self.ask("Rotate the token?", ts="2026-11-15T11:00:00Z")
        self.amend(other, 2, "2026-11-15T11:05:00Z", text="Rotate the token today?")
        self.add("agent_question_answered", {"ask_id": other, "ask_ids": [other], "answer": "yes", "via": "asks-ui",
                                             "by": "owner", "version": 2}, "2026-11-15T11:06:00Z")
        self.assertEqual(self.asks()[other]["state"], "answered")  # the newest version's answer closes it

    def test_leaving_with_an_answer_refused_as_amended_keeps_its_text_as_a_draft(self):
        from unittest import mock
        aid = self.ask()
        self.amend(aid, 2, "2026-11-15T10:10:00Z", options=["yes, tonight", "no"])
        conn = self.conn

        class Own:  # the screen's own connection: the test's store, not closed under it
            def __getattr__(self, name):
                return getattr(conn, name)

            def close(self):
                pass
        with mock.patch.object(of_store, "connect", lambda *a, **k: Own()), \
                mock.patch.object(of_asks_tui, "spawn_deliver", lambda *a: None):
            (_, ok, status), = of_asks_tui.leave(
                [{"what": "answer", "text": "yes", "item": dict(self.item(aid, 1), written=True)}], [{"name": "of_a"}],
                "of")
        self.assertEqual((ok, status[:7]), (False, "amended"))
        self.assertEqual(self.answers(), [])
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

    def test_a_change_of_text_alone_needs_the_version_too(self):
        aid = self.ask("Lift the hold?", ["yes", "no"], from_position="of_a")
        self.amend(aid, 2, "2026-11-11T10:02:00Z", text="Lift the hold today?")
        pid = self.prompt("yes", ts="2026-11-11T10:05:00Z")
        self.refused(aid, pid, "--version 2", option=1)
        out = of_asks_chat.answer(self.conn, self.asks(), POSITIONS, aid[-8:], pid, lambda name: "p1", option=1,
                                  version=2, now=now_of("2026-11-11T10:06:00Z"))
        self.assertIn("answered:", out[0])
        self.assertTrue(self.typed[0][1].startswith(f"Answer from the founder to your question {aid[-8:]} (version 2: "
                                                    f"\u00abLift the hold today?\u00bb){of_asks_answer._card_paren(aid)}: "
                                                    "yes"), self.typed[0][1])


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

    def test_a_due_is_dropped_with_none(self):
        """A user organization's unit owner (msg 01a1f8ea-76d5): a due with no source could not be removed."""
        r = self.run_of("ask", "Lift the hold?", "--options", "yes|no", "--due", "+2h", "--from", "of_a")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        short = self.events("of.agent_question")[0][0][-8:]
        r = self.run_of("ask", "Lift the hold?", "--amend", short, "--due", "none", "--from", "of_a")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (_, p), = self.events("of.ask_amended")
        self.assertEqual((p["changed"], p["due"]), (["due"], None))
        r = self.run_of("ask", "Lift the hold?", "--amend", short, "--due", "none", "--from", "of_a")
        self.assertIn("nothing differs", r.stderr)   # already none

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
        import sqlite3
        db = sqlite3.connect(os.path.join(self.t.name, "state", "of.db"))
        db.execute("UPDATE event SET host_id = 'another-machine' WHERE event_id = ?", (aid,))
        db.commit()
        db.close()
        r = self.run_of("ask", "Lift it now?", "--amend", short, "--from", "of_a")
        self.assertIn("was filed on another machine of the organization: amend it there", r.stderr)
        # withdrawn by its asker: with no terminal, no position is the owner (bin/of _asks_actor; of_review on PR 387)
        w = subprocess.run([sys.executable, OF, "asks", "withdraw", short], env=dict(self.env, OF_POSITION="of_a"),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(w.returncode, 0, w.stdout + w.stderr)
        r = self.run_of("ask", "Lift it now?", "--amend", short, "--from", "of_a")
        self.assertIn("is withdrawn: only an open ask with no answer is amended", r.stderr)

    def test_a_tasks_command_and_where_through_the_cli(self):
        r = self.run_of("ask", "Install the runner", "--task", "1. Open a terminal.", "--command", "brew install runner",
                        "--from", "of_a")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        tid = self.events("of.agent_question")[-1][0]
        short = tid[-8:]
        r = self.run_of("ask", "Install the runner", "--amend", short, "--where", "phone", "--from", "of_a")
        self.assertIn("has a command to paste, typed in a terminal", r.stderr)
        r = self.run_of("ask", "Install the runner", "--amend", short, "--command", "port install runner",
                        "--from", "of_a")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"amended ask {short}: version 2 (command changed)", r.stdout)
        r = self.run_of("ask", "Install the runner", "--amend", short, "--command", "none", "--where", "browser",
                        "--from", "of_a")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        p = self.events("of.ask_amended")[-1][1]
        self.assertEqual((p["version"], p["changed"], p["command"], p["where"]),
                         (3, ["command", "where"], None, "browser"))
        r = self.run_of("ask", "Lift the hold on the relay?", "--options", "yes|no", "--from", "of_a")
        qid = self.events("of.agent_question")[-1][0]
        for flag in (("--command", "ls"), ("--where", "phone")):
            r = self.run_of("ask", "Lift the hold on the relay?", "--amend", qid[-8:], *flag, "--from", "of_a")
            self.assertIn("is a question; work for the founder's hands is another decision", r.stderr, flag)
        self.assertEqual(len(self.events("of.ask_amended")), 2)

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


    def test_an_answer_the_late_view_has_not_folded_refuses_the_amend(self):
        """of_conductor, message 01a1ea94-7796: card MT-31 was answered in the popup at version 6 at 17:12:05Z and
        amended to version 7 at 17:18:51Z. The listener's view had stood since 17:11:02Z and still showed it open, and
        the log's last 15 minutes on top of a late view do not hold an ask filed before them."""
        import time
        import of_listen
        r = self.run_of("ask", "Lift the hold on the relay?", "--options", "yes|no", "--from", "of_a")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        aid = self.events("of.agent_question")[0][0]
        saved = of_events.STATE_DIR
        of_events.STATE_DIR = os.path.join(self.t.name, "state")
        of_events._CONN.clear()
        conn = of_store.connect(os.path.join(self.t.name, "state", "of.db"))
        try:
            old = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 3600))
            conn.execute("UPDATE event SET ts = ? WHERE event_id = ?", (old, aid))  # filed an hour ago
            procs = [of_listen.AsksProcessor()]
            of_listen.step(conn, procs, of_listen.prepare(conn, procs))  # the view, before the answer
            of_events.emit("agent_question_answered", {"ask_id": aid, "ask_ids": [aid], "answer": "yes",
                                                       "via": "asks-ui", "by": "owner", "version": 1})
            late = of_events.record("turn_end", {"last_message": "ok"}, position="of_b", session="S-b", org="org",
                                    source="hook")
            late["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 7200))
            of_store.add_event(conn, late)  # older than what the view fed: the view reads late
            self.assertTrue(of_asks.current_asks(conn)["late"])
            self.assertEqual(of_asks.closed_after_view(conn, aid, 1)[0], "answered")
            self.assertIsNone(of_asks.closed_after_view(conn, aid, 2))  # an answer to an older version closes nothing
        finally:
            conn.close()
            of_events._CONN.clear()
            of_events.STATE_DIR = saved
        r = self.run_of("ask", "Lift the hold today?", "--amend", aid[-8:], "--from", "of_a")
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn(f"--amend: ask {aid[-8:]} was answered at ", r.stderr)
        self.assertEqual(self.events("of.ask_amended"), [])

if __name__ == "__main__":
    unittest.main()
