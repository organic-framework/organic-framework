"""A permission card reaches its delegated receiver, who can decline it in the asking pane."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import of_asks
import of_asks_answer
import of_events
import of_hook
import of_listen
import of_pulse
import of_store


SCREEN = ("Bash command\ncheck-service\n1. No options configured\nDo you want to proceed?\n"
          "❯ 1. Yes\n  2. Yes, always allow\n  3. No, cancel\nEsc to cancel")


class PermissionTest(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        state = stack.enter_context(tempfile.TemporaryDirectory())
        stack.enter_context(mock.patch.object(of_events, "STATE_DIR", state))
        stack.enter_context(mock.patch.object(of_events, "_CONN", {}))
        stack.enter_context(mock.patch.dict(os.environ, OF_DB=os.path.join(state, "of.db")))
        index = {"labels": {"worker": ["SID", "acme"]}}
        stack.enter_context(mock.patch.object(of_hook, "_registry_index", return_value=index))
        self.conn = of_events._conn()
        stack.callback(self.conn.close)
        self.now = time.time()
        self.positions = [{"name": "worker", "_org": "acme", "_unit": "ops", "_delegation":
                           {"id": "ops", "reporting": {"escalate_when": ["ask.blocking", "ask_unanswered > 2h"]}}},
                          {"name": "lead", "_org": "acme", "holder": {"session": "LEAD"}}]
        self.aid = self.request()

    def request(self, session="SID", ref="AC-1", **changes):
        payload = dict({"summary": "check-service", "tool_name": "Bash", "to": "acme:lead", "ref": ref}, **changes)
        rec = of_events.record("permission_request", payload, position="worker", org="acme", session=session,
                               source="hook")
        rec["timestamp"] = of_events.now_iso(self.now - 600)
        of_store.add_event(self.conn, rec)
        return rec["of.event.id"]

    def asks(self):
        return of_asks.project(of_asks.load(self.conn))

    def cli(self, *args, caller="LEAD"):
        from tests.test_of import of
        out = io.StringIO()
        with mock.patch.object(of, "_load_quiet", return_value=self.positions), \
                mock.patch.dict(os.environ, CLAUDE_CODE_SESSION_ID=caller, HERDR_PANE_ID="", OF_POSITION="",
                                CLAUDECODE="1" if caller else ""), mock.patch("sys.stdin.isatty", return_value=True), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            status = of.main(["asks", *args])
        return status, out.getvalue()

    def fake_herdr(self, screens, send_status=0):
        reads = iter(screens)
        agents = [{"pane_id": "other", "agent_session": {"value": "LEAD"}},
                  {"pane_id": "asking", "agent_session": {"value": "SID"}}]

        def run(cmd, **kwargs):
            if cmd[1:] == ["agent", "list"]:
                data = json.dumps({"result": {"agents": agents}})
            elif cmd[1:] == ["pane", "read", "asking", "--source", "visible", "--lines", "40"]:
                data = next(reads)
                if data is None:
                    return subprocess.CompletedProcess(cmd, 1, SCREEN, "read failed")
            elif cmd[1:] == ["pane", "send-keys", "asking", "3"]:
                return subprocess.CompletedProcess(cmd, send_status, "", "")
            else:
                self.fail(f"unexpected command {cmd}")
            return subprocess.CompletedProcess(cmd, 0, data, "")
        return mock.patch.object(subprocess, "run", side_effect=run)

    def test_queues_and_escalation(self):
        with self.fake_herdr([]):
            status, queue = self.cli()
            self.assertEqual(status, 0)
            self.assertNotIn("check-service", queue)
            status, queue = self.cli("--to-me")
            self.assertEqual(status, 0)
            self.assertIn("of asks deny AC-1", queue)
            self.assertIn("check-service", queue)
        self.assertEqual(of_asks.for_owner(self.asks()), [])
        blocked = {"agent_status": "blocked", "agent_session": {"value": "SID"}, "pane_id": "asking"}
        waits = of_pulse.project(self.positions, [blocked], [], self.asks(), {}, now=self.now,
                                 bind=lambda ps, agents: ([(ps[0], agents[0])], [], []))
        self.assertEqual([(w["on"], w["kind"]) for w in waits], [("lead", "waiting_peer")])
        self.assertEqual(of_pulse.run_escalations(self.positions, self.asks(), self.now, self.conn), 0)
        self.assertEqual(of_pulse.run_escalations(self.positions, self.asks(), self.now + 7200, self.conn), 1)
        copy, = of_asks.for_owner(self.asks())
        self.assertEqual((copy["escalated_from"], copy["state"]), (self.aid, "open"))
        self.assertIn("ask_unanswered > 2h", copy["text"])
        self.assertIn("check-service", copy["text"])
        self.assertNotIn("blocking", copy["kind"])

    def test_explicit_escalation_conditions(self):
        for rule, fields in (("ask.blocking", {"blocking": True}), ("money", {"tags": [{"tag": "money"}]}),
                             ("ask_due < 7d", {"due": of_events.now_iso(self.now + 86400)})):
            with self.subTest(rule=rule):
                aid = self.request(session=rule, ref="AC-2", **fields)
                positions = [{"name": "worker", "_delegation": {"reporting": {"escalate_when": [rule]}}}]
                self.assertEqual([(a["event_id"], hit) for a, hit, _ in
                                  of_pulse.escalations(positions, self.asks(), self.now)], [(aid, rule)])

    def test_delivery_to_the_receiver_before_and_after_escalation(self):
        listener = of_listen.DeliverProcessor()
        listener.load_state(self.conn)
        self.assertEqual(listener.due["lead"][0]["ask"], self.aid)
        lead = {"agent_status": "idle", "pane_id": "lead-pane", "agent_session": {"value": "LEAD"}}
        send = mock.Mock(return_value=(True, "typed"))
        for escalated in (False, True):
            with self.subTest(escalated=escalated):
                if escalated:
                    of_pulse.run_escalations(self.positions, self.asks(), self.now + 7200, self.conn)
                of_asks_answer.redeliver_pass(self.conn, self.asks(), self.positions, [lead],
                                              lambda ps, agents: ([(ps[1], agents[0])], [], []),
                                              now=self.now + 300, deliver_fn=send)
                send.assert_called_once()
                pane, frame, session, sender, channel, _ = send.call_args.args
                self.assertEqual((pane, session, sender, channel), ("lead-pane", "LEAD", "worker", None))
                self.assertRegex(frame, of_asks.TOOL_FRAME)
                self.assertIn(of_asks_answer.routed_mark(self.aid), frame)
                self.assertIn("worker's Claude Code asks to run \"Bash: check-service\"", frame)
                self.assertIn("of asks deny AC-1", frame)
                self.assertIn('of ask --escalates AC-1 "…"', frame)
                self.assertEqual(of_asks_answer.escalated_mark(self.aid) in frame, escalated)
                of_events.emit("human_prompt", {"text": frame, "origin": "injected"}, position="lead", session="LEAD",
                               org="acme", source="hook")
                self.assertEqual(of_asks_answer.pending(self.conn, self.asks(), self.positions, self.now + 300), [])
                send.reset_mock()

    def test_receiver_denies_by_card_and_records_only_after_the_screen_clears(self):
        cleared = json.dumps({"result": {"read": {"text": "Command declined"}}})
        with self.fake_herdr([SCREEN, cleared]) as herdr, mock.patch("time.sleep"):
            status, msg = self.cli("deny", "AC-1")
        self.assertEqual(status, 0, msg)
        self.assertEqual([c.args[0][2] for c in herdr.call_args_list], ["list", "read", "send-keys", "read"])
        pl, session, pos = self.conn.execute("SELECT payload, session_id, position FROM event "
                                             "WHERE name = 'of.permission_resolved'").fetchone()
        self.assertEqual(json.loads(pl), {"reply_to": self.aid, "outcome": "denied", "by": "lead", "via": "asks-deny"})
        self.assertEqual((session, pos, self.asks()[0]["state"]), ("SID", "worker", "denied"))

    def test_owner_can_deny_by_event_id_after_escalation(self):
        of_pulse.run_escalations(self.positions, self.asks(), self.now + 7200, self.conn)
        with self.fake_herdr([SCREEN, "Command declined"]), mock.patch("time.sleep"):
            status, msg = self.cli("deny", self.aid, caller="")
        self.assertEqual(status, 0, msg)
        self.assertEqual(self.conn.execute("SELECT json_extract(payload, '$.by') FROM event WHERE "
                                           "name = 'of.permission_resolved'").fetchone()[0], "owner")
        self.assertFalse(any(a["state"] in ("open", "escalated") for a in self.asks()))

    def test_unrelated_caller_is_refused_before_reading_the_pane(self):
        self.positions.append({"name": "other", "holder": {"session": "OTHER"}})
        with self.fake_herdr([]) as herdr:
            status, msg = self.cli("deny", self.aid, caller="OTHER")
        self.assertEqual(status, 1)
        self.assertIn("only the receiver or the owner", msg)
        herdr.assert_not_called()

    def test_dry_run_and_unconfirmed_denials_leave_the_card_open(self):
        for screens, dry_run, reason, presses in (
                ([SCREEN], True, "would press 3 (No)", 0),
                ([None], False, "read", 0),
                (["Exit and stop tasks?\n❯ 1. Yes\n  2. No"], False, "no permission prompt", 0),
                ([SCREEN + "\n❯ "], False, "no permission prompt", 0),
                ([SCREEN.replace("3. No,", "3. Notify,")], False, "no No option", 0),
                ([SCREEN.replace("3. No,", "1. No,")], False, "no No option", 0),
                ([SCREEN, SCREEN], False, "still visible", 1),
                ([SCREEN, None], False, "read", 1),
                ([SCREEN, ""], False, "could not read", 1)):
            with self.subTest(reason=reason, presses=presses), self.fake_herdr(screens) as herdr, mock.patch("time.sleep"):
                status, msg = self.cli("deny", self.aid, *(["--dry-run"] if dry_run else []))
                self.assertEqual(status, 0 if dry_run else 1, msg)
                self.assertIn(reason, msg)
                self.assertEqual(sum(c.args[0][2] == "send-keys" for c in herdr.call_args_list), presses)
                self.assertEqual(self.asks()[0]["state"], "open")

    def test_failed_send_does_not_record_a_denial(self):
        with self.fake_herdr([SCREEN], send_status=1):
            status, msg = self.cli("deny", self.aid)
        self.assertEqual(status, 1)
        self.assertIn("could not press No", msg)
        self.assertEqual(self.asks()[0]["state"], "open")

    def test_late_resolution_does_not_close_the_next_prompt(self):
        next_id = self.request(ref="AC-2")
        of_events.emit("permission_resolved", {"reply_to": self.aid, "outcome": "denied", "by": "lead",
                                              "via": "asks-deny"}, position="worker", org="acme", session="SID")
        self.assertEqual({a["event_id"]: a["state"] for a in self.asks()}, {self.aid: "denied", next_id: "open"})

    def test_a_stale_card_cannot_deny_the_next_prompt(self):
        from tests.test_of import of
        stale = self.asks()
        self.request(ref="AC-2")
        with mock.patch.object(of, "_asks_now", return_value=stale), self.fake_herdr([]) as herdr:
            status, msg = self.cli("deny", self.aid)
        self.assertEqual(status, 1)
        self.assertIn("latest permission request", msg)
        herdr.assert_not_called()
