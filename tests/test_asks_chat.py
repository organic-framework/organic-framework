"""Answers and tasks through a person's conductor (bin/of_asks_chat.py; docs/remote.md §3 rows 1 and 4, hand-over
f4d4b89f): an answer is recorded only on the person's own prompt in their conductor's session, later than the card,
naming it or quoting its option; a forged or stale prompt is refused. Tasks carry where they can be done."""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_asks  # noqa: E402
import of_asks_answer  # noqa: E402
import of_asks_chat  # noqa: E402
import of_events  # noqa: E402
import of_store  # noqa: E402

POSITIONS = [
    {"name": "of_conductor", "role": "conductor", "owner": "founder", "holder": {"session": "C1"}, "_org": "ofb"},
    {"name": "of_conductor-alex", "role": "conductor", "owner": "of_alex", "holder": {"session": "C2"}, "_org": "ofb"},
    {"name": "of_alex", "role": "keeper", "owner": "founder", "holder": {"runtime": "human", "person": "alex"}},
    {"name": "of_a", "_org": "ofb"}, {"name": "of_b", "_org": "ofb"}, {"name": "of_unit_owner", "_org": "ofb"},
    # another organization on the machine, built by of init: its head owns itself, its conductor is the head's
    {"name": "acme_owner", "owner": "acme_owner", "holder": {"runtime": "human", "person": "dana"}, "_org": "acme"},
    {"name": "acme_conductor", "role": "conductor", "owner": "acme_owner", "holder": {"session": "C3"}, "_org": "acme"},
    {"name": "acme_a", "owner": "acme_owner", "_org": "acme"},
    {"name": "x_conductor", "role": "conductor", "owner": "of_alex", "holder": {"session": "C4"}, "_org": "ofb"},
]


def now_of(iso):
    return of_asks_chat._epoch(iso)


class ChatTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = self.tmp.name
        of_events._CONN.clear()
        self.conn = of_store.connect(os.path.join(self.tmp.name, "of.db"))
        self.typed = []
        self.saved_deliver = of_asks_answer.deliver
        of_asks_answer.deliver = lambda pane, text, session, **k: self.typed.append((pane, text)) or "delivered"

    def tearDown(self):
        of_asks_answer.deliver = self.saved_deliver
        of_events._CONN.clear()
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def add(self, kind, payload, ts, session="S1", position="of_a", source="of"):
        rec = of_events.record(kind, payload, position=position, session=session, org="ofb", source=source)
        rec["timestamp"] = ts
        of_store.add_event(self.conn, rec)
        return rec["of.event.id"]

    def ask(self, question, options=(), ts="2026-11-11T10:00:00Z", position="of_a", session="S1", **extra):
        return self.add("agent_question", {"detected_by": "declared", "questions": [
            {"question": question, "options": list(options)}], **extra}, ts, session=session, position=position)

    def prompt(self, text, ts="2026-11-11T10:05:00Z", session="C1", position="of_conductor", origin="human",
               channel="herdr", source="hook"):
        return self.add("human_prompt", {"origin": origin, "channel": channel, "text": text}, ts, session=session,
                        position=position, source=source)

    def asks(self):
        return of_asks.project(of_asks.load(self.conn))

    def check(self, aid, pid, now="2026-11-11T10:06:00Z", **kw):
        return of_asks_chat.check(self.conn, self.asks(), POSITIONS, aid[-8:], pid, now=now_of(now), **kw)

    def refused(self, aid, pid, needle, **kw):
        with self.assertRaises(ValueError) as e:
            self.check(aid, pid, **kw)
        self.assertIn(needle, str(e.exception))


class AnswerTest(ChatTest):
    def test_a_quoted_option_closes_the_card_and_the_asker_sees_the_words(self):
        a = self.ask("Run the upgrade today?", ["move it to Thursday", "run it today (recommended: the window is open)"])
        p = self.prompt("ok, run it today")
        out = of_asks_chat.answer(self.conn, self.asks(), POSITIONS, a[-8:], p, lambda name: "pane-" + name,
                                  option=2, relayed_by="of_conductor", now=now_of("2026-11-11T10:06:00Z"))
        self.assertIn("quotes option 2", out[0])
        state = {x["event_id"]: x for x in self.asks()}[a]
        self.assertEqual(state["state"], "answered")
        pl = json.loads(self.conn.execute("SELECT payload FROM event WHERE name = 'of.agent_question_answered'")
                        .fetchone()[0])
        self.assertEqual((pl["via"], pl["by"], pl["prompt"], pl["conductor"]), ("conductor", "owner", p, "of_conductor"))
        self.assertEqual(self.typed[0][0], "pane-of_a")
        self.assertTrue(self.typed[0][1].startswith(of_asks_answer.frame(a, "run it today (recommended: the window is open)")))
        self.assertIn("in these words: «ok, run it today»", self.typed[0][1])
        with self.assertRaises(ValueError) as e:  # the card is closed now: a second call records nothing
            of_asks_chat.answer(self.conn, self.asks(), POSITIONS, a[-8:], p, lambda name: None, option=2,
                                now=now_of("2026-11-11T10:06:00Z"))
        self.assertIn("is answered", str(e.exception))

    def test_naming_the_card_lets_the_conductor_map_the_words(self):
        a = self.ask("Which part size?", ["5 MB", "64 MB"])
        p = self.prompt(f"{a[-8:]}: the second one")
        self.assertEqual(self.check(a, p, option=2)["answer"], "64 MB")
        self.assertIn(f"its id {a[-8:]}", self.check(a, p, option=2)["evidence"])

    def test_the_asker_names_the_card_when_it_has_no_other(self):
        a = self.ask("Free question with no options?")
        p = self.prompt("of_a: keep the old bucket")
        self.assertEqual(self.check(a, p, text="keep the old bucket")["answer"], "keep the old bucket")

    def test_the_text_must_be_the_persons_words(self):
        a = self.ask("Free question with no options?")
        p = self.prompt("of_a: keep the old bucket")
        self.refused(a, p, "as typed", text="delete the old bucket")

    def test_an_option_the_prompt_contradicts_is_refused(self):
        a = self.ask("Run the upgrade today?", ["move it to Thursday", "run it today"])
        p = self.prompt(f"{a[-8:]}: move it to thursday")
        self.refused(a, p, "quotes option 1", option=2)

    def test_a_bare_yes_names_no_card_when_two_cards_offer_it(self):
        a = self.ask("Lift the hold?", ["yes", "no"])
        self.ask("Rotate the key?", ["yes", "no"], position="of_b", session="S2", ts="2026-11-11T10:01:00Z")
        p = self.prompt("yes")
        self.refused(a, p, "neither names the card", option=1)
        self.assertEqual(self.check(a, self.prompt(f"yes to {a[-8:]}", ts="2026-11-11T10:05:30Z"), option=1)["answer"],
                         "yes")

    def test_a_bare_yes_answers_the_only_card(self):
        a = self.ask("Lift the hold?", ["yes", "no"])
        self.assertEqual(self.check(a, self.prompt("yes"), option=1)["answer"], "yes")

    def test_a_task_is_claimed_never_closed(self):
        t = self.ask("Approve the transfer in the bank app", task=True, steps="1. Open the app. 2. Approve.",
                     where="phone")
        p = self.prompt("of_a: done, approved")
        of_asks_chat.answer(self.conn, self.asks(), POSITIONS, t[-8:], p, lambda name: None, text="done, approved",
                            now=now_of("2026-11-11T10:06:00Z"))
        a = {x["event_id"]: x for x in self.asks()}[t]
        self.assertEqual(a["state"], "open")
        self.assertEqual(a["claim"], "done, approved")

    def test_redelivery_carries_the_words(self):
        a = self.ask("Lift the hold?", ["yes", "no"])
        p = self.prompt("yes")
        of_asks_chat.answer(self.conn, self.asks(), POSITIONS, a[-8:], p, lambda name: None, option=1,
                            now=now_of("2026-11-11T10:06:00Z"))
        # the answer event carries the clock's time: the pass comes after REDELIVER_MIN_AGE_S
        todo = of_asks_answer.pending(self.conn, self.asks(), POSITIONS,
                                      now=__import__("time").time() + of_asks_answer.REDELIVER_MIN_AGE_S + 5)
        self.assertEqual(len(todo), 1)
        self.assertIn("in these words: «yes»", todo[0]["text"])


class ForgedOrStaleTest(ChatTest):
    def setUp(self):
        super().setUp()
        self.a = self.ask("Lift the hold?", ["yes", "no"])

    def test_an_injected_prompt(self):
        self.refused(self.a, self.prompt("yes", origin="injected"), "not typed by the person", option=1)

    def test_an_origin_corrected_by_the_transcript(self):
        p = self.prompt("yes")
        self.add("prompt_origin", {"event_id": p, "origin": "system", "evidence": "turnOrigin"}, "2026-11-11T10:05:01Z")
        self.refused(self.a, p, "origin system", option=1)

    def test_the_popups_typed_answer(self):
        self.refused(self.a, self.prompt("yes", channel="asks-ui"), "popup", option=1)

    def test_a_prompt_in_another_session(self):
        self.refused(self.a, self.prompt("yes", session="S9", position="of_b"), "not in the session", option=1)

    def test_a_colleagues_conductor_does_not_answer_the_owners_card(self):
        self.refused(self.a, self.prompt("yes", session="C2", position="of_conductor-alex"), "not in the session",
                     option=1)

    def test_an_event_that_is_not_a_prompt(self):
        m = self.add("agent_message_in", {"from": "of_b", "text": "yes"}, "2026-11-11T10:05:00Z", session="C1",
                     position="of_conductor", source="hook")
        self.refused(self.a, m, "not a prompt the person typed", option=1)

    def test_a_prompt_not_written_by_the_hook(self):
        self.refused(self.a, self.prompt("yes", source="of"), "not recorded by the hook", option=1)

    def test_an_unknown_id(self):
        self.refused(self.a, "0123456789abcdef", "no recorded event", option=1)

    def test_a_prompt_before_the_card(self):
        self.refused(self.a, self.prompt("yes", ts="2026-11-11T09:59:00Z"), "before the card", option=1)

    def test_a_prompt_older_than_a_day(self):
        self.refused(self.a, self.prompt("yes"), "older than", option=1, now="2026-11-12T11:00:00Z")

    def test_a_prompt_before_the_tasks_reopen(self):
        t = self.ask("Log in to the console", ts="2026-11-11T10:00:30Z", position="of_b", session="S2", task=True,
                     steps="1. Log in.", where="browser")
        p = self.prompt("of_b: done")
        self.add("state_declared", {"state": "working", "reopen_task": t, "note": "still logged out"},
                 "2026-11-11T10:05:30Z", session="S2", position="of_b")
        self.refused(t, p, "reopened", text="done")

    def test_a_card_on_an_agents_queue(self):
        r = self.ask("Routed to the unit owner", ["yes", "no"], to="of_unit_owner")
        self.refused(r, self.prompt(f"{r[-8:]} yes"), "no person's conductor", option=1)

    def test_a_dialog_in_a_pane(self):
        p = self.add("permission_request", {"tool_name": "Bash", "summary": "rm"}, "2026-11-11T10:00:10Z",
                     session="S3", position="of_b", source="hook")
        self.refused(p, self.prompt(f"{p[-8:]} yes"), "answered at the machine", option=1)

    def test_another_organizations_head_does_not_answer_the_founders_card(self):
        """of_architect's review of 3962757: the conductor must serve the person the card is addressed to."""
        self.refused(self.a, self.prompt(f"{self.a[-8:]} yes", session="C3", position="acme_conductor"),
                     "not in the session", option=1)

    def test_a_conductor_is_known_by_its_owner_never_by_its_name(self):
        self.refused(self.a, self.prompt(f"{self.a[-8:]} yes", session="C4", position="x_conductor"),
                     "not in the session", option=1)

    def test_an_organizations_head_answers_its_own_cards(self):
        c = self.ask("Start the onboarding?", ["yes", "no"], position="acme_a", session="S5", ts="2026-11-11T10:02:00Z")
        ok = self.check(c, self.prompt(f"{c[-8:]} yes", session="C3", position="acme_conductor"), option=1)
        self.assertEqual(ok["conductor"], "acme_conductor")
        self.assertEqual(self.check(c, self.prompt(f"{c[-8:]} yes", ts="2026-11-11T10:05:10Z"), option=1)["conductor"],
                         "of_conductor")  # the founder's own conductor, by address, serves him here too

    def test_a_card_tagged_money_or_production_is_answered_at_the_machine(self):
        """docs/remote.md §5: a forged prompt is possible while the store is the agents' file."""
        for tag, stored in (("money", {"tag": "money", "source": "declared"}),
                            ("security_incident", {"tag": "security_incident", "source": "guessed"}),
                            ("prod_change", {"tag": "prod_change", "source": "declared", "systems": ["k8s"]}),
                            ("irreversible", "irreversible")):
            c = self.ask(f"Decide {tag}?", ["yes", "no"], tags=[stored], position="of_b", session="S2",
                         ts="2026-11-11T10:00:20Z")
            self.refused(c, self.prompt(f"{c[-8:]} yes"), f"tagged {tag}", option=1)
            self.assertEqual(of_asks_chat.in_chat({x["event_id"]: x for x in self.asks()}[c]), "waiting")
            self.ask(f"Decide {tag}?", ["yes", "no"], tags=[tag], position="of_b", session="S2")  # supersede nothing
        view = of_asks_chat.chat_view(self.conn, self.asks())
        self.assertIn("a card tagged money, answered at the machine", view)
        ok = self.ask("Rename the channel?", ["yes", "no"], tags=[{"tag": "window_go", "systems": ["slack"]}],
                      position="of_unit_owner",
                      session="S6", ts="2026-11-11T10:00:40Z")
        self.assertEqual(self.check(ok, self.prompt(f"{ok[-8:]} yes"), option=1)["answer"], "yes")

    def test_words_hold_a_card_whatever_was_declared(self):
        """of_release's review 2026-11-11, finding 2: a pane card stores no tags, and --tags none stores nothing."""
        pane = self.ask("Pay 90 USD to the operator?", ["yes", "no"], source="pane", tags=[])
        self.refused(pane, self.prompt(f"{pane[-8:]} yes"), "tagged money", option=1)
        none = self.ask("Restart the production cluster now?", ["yes", "no"], tags=[], tags_declared=True,
                        position="of_b", session="S2", ts="2026-11-11T10:00:30Z")
        self.refused(none, self.prompt(f"{none[-8:]} yes"), "tagged prod_change", option=1)
        self.assertEqual(of_asks_chat.in_chat({x["event_id"]: x for x in self.asks()}[none]), "waiting")
        opt = self.ask("Which way?", ["wait", "pay 90 USD now"], position="of_unit_owner", session="S6",
                       ts="2026-11-11T10:00:40Z")
        self.refused(opt, self.prompt(f"{opt[-8:]} wait"), "tagged money", option=1)  # its options count too

    def test_a_routed_card_to_a_person_takes_that_persons_conductor(self):
        r = self.ask("Merge the release?", ["yes", "no"], to="of_alex")
        self.refused(r, self.prompt(f"{r[-8:]} yes"), "not in the session", option=1)
        ok = self.check(r, self.prompt(f"{r[-8:]} yes", session="C2", position="of_conductor-alex"), option=1)
        self.assertEqual(ok["conductor"], "of_conductor-alex")


class WhereTest(ChatTest):
    def test_check_where(self):
        self.assertIsNone(of_asks_chat.check_where(None, None, None))
        self.assertIsNone(of_asks_chat.check_where("phone", "1. Open the app.", None))
        self.assertIn("goes with --task", of_asks_chat.check_where("phone", None, None))
        self.assertIn("--command", of_asks_chat.check_where("browser", "1. Paste it.", "op signin"))

    def test_the_place_and_the_chat_view(self):
        self.ask("Approve the transfer in the bank app", task=True, steps="1. Open the app.\n2. Approve.",
                 where="phone")
        self.ask("Rotate the SSH key", task=True, steps="1. Run ssh-keygen.", position="of_b", session="S2",
                 ts="2026-11-11T10:01:00Z")
        self.ask("Paste the token", task=True, steps="1. Paste it.", command="op read x", where="phone",
                 position="of_unit_owner", session="S4", ts="2026-11-11T10:02:00Z")
        self.ask("Lift the hold?", ["yes", "no"], position="of_b", session="S2", ts="2026-11-11T10:03:00Z")
        by = {a["text"]: a for a in self.asks()}
        self.assertEqual([of_asks_chat.where_of(by[t]) for t in ("Approve the transfer in the bank app",
                                                                 "Rotate the SSH key", "Paste the token")],
                         ["phone", "machine", "machine"])
        self.assertEqual(of_asks_chat.in_chat(by["Lift the hold?"]), "answer")
        view = of_asks_chat.chat_view(self.conn, self.asks())
        machine = view.split("Waits until the person is at the machine")[1]
        self.assertIn("Rotate the SSH key", machine)
        self.assertNotIn("ssh-keygen", view)  # a machine task is never steps in the chat
        self.assertIn("Paste the token", machine)
        self.assertIn("1. Open the app.", view.split("Waits until")[0])
        self.assertIn("1) yes", view)


class CliTest(unittest.TestCase):
    """End to end: `of ask --where`, and `of asks answer` refusing a forged prompt and recording the person's own."""

    def test_cli(self):
        import subprocess
        from tests.test_diff import REG
        of = os.path.join(HERE, "..", "bin", "of")
        reg = REG.replace("systems:", "  - name: of_conductor\n    system: organic-framework\n    role: conductor\n"
                                      "    kind: coordinator\n    owner: founder\n    summary: window\n"
                                      "    status: active\n    holder:\n      runtime: claude-code\n"
                                      "      session: c-1\n\nsystems:", 1)
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "org", "registry"))
            os.makedirs(os.path.join(t, "state"))
            with open(os.path.join(t, "org", "registry", "positions.yaml"), "w") as fh:
                fh.write(reg)
            with open(os.path.join(t, "orgs.yaml"), "w") as fh:
                fh.write(f"orgs:\n  - name: ofb\n    path: {t}/org\n    workspace: ofb\n")
            env = dict(os.environ, OF_STATE=os.path.join(t, "state"), OF_ORGS=os.path.join(t, "orgs.yaml"),
                       OF_CONFIG_DIR=t, PATH="/usr/bin:/bin")
            env.pop("CLAUDE_CODE_SESSION_ID", None)
            run = lambda *a: subprocess.run([sys.executable, of, *a], env=env, capture_output=True, text=True,
                                            timeout=60)
            r = run("ask", "Paste the token", "--task", "1. Paste it.", "--command", "op read x", "--where", "phone",
                    "--from", "of_a")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            r = run("ask", "Approve in the bank app", "--task", "1. Open the app. 2. Approve.", "--where", "phone",
                    "--from", "of_a")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = run("ask", "Lift the hold?", "--options", "yes|no", "--from", "of_a")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            r = run("asks", "--json")
            shown = {a["text"]: a for a in json.loads(r.stdout)}
            self.assertEqual((shown["Approve in the bank app"]["where"], shown["Approve in the bank app"]["in_chat"]),
                             ("phone", "steps"))
            aid = shown["Lift the hold?"]["event_id"]
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = os.path.join(t, "state")
            of_events._CONN.clear()
            try:
                conn = of_store.connect(os.path.join(t, "state", "of.db"))
                ids = []
                for sess, pos, text in (("s-9", "of_a", f"{aid[-8:]} yes"), ("c-1", "of_conductor", f"{aid[-8:]} yes")):
                    rec = of_events.record("human_prompt", {"origin": "human", "channel": "herdr", "text": text},
                                           position=pos, session=sess, org="ofb", source="hook")
                    of_store.add_event(conn, rec)
                    ids.append(rec["of.event.id"])
                conn.close()
            finally:
                of_events._CONN.clear()
                of_events.STATE_DIR = saved
            r = run("asks", "answer", aid[-8:], "--option", "1", "--prompt", ids[0])
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("not in the session", r.stderr)
            r = run("asks", "answer", aid[-8:], "--option", "1", "--prompt", ids[1])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("answered", r.stdout)
            r = run("asks", "--json")
            self.assertNotIn(aid, [a["event_id"] for a in json.loads(r.stdout)])
            # a card tagged money waits for the machine, whatever the prompt says (docs/remote.md §5)
            r = run("ask", "Pay the invoice?", "--options", "yes|no", "--tags", "money", "--from", "of_a")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            mid = next(a["event_id"] for a in json.loads(run("asks", "--json").stdout) if a["text"] == "Pay the invoice?")
            r = run("asks", "answer", mid[-8:], "--option", "1", "--prompt", ids[1])
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("tagged money", r.stderr)


if __name__ == "__main__":
    unittest.main()
