"""Tests for the Claude Code collector (bin/of_hook.py). No herdr, temp state."""

import json
import re
import os
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_events  # noqa: E402
import of_guard  # noqa: E402
import of_hook  # noqa: E402


class MaskTest(unittest.TestCase):
    def test_masks_known_secret_shapes(self):
        cases = {
            "key sk-ant-api03-abcdefghijklmnopqrstuv end": "sk-ant",
            "token ghp_abcdefghijklmnopqrstuvwxyz0123 end": "ghp_",
            "AKIA" + "ABCDEFGHIJKLMNOP": "AKIA",  # split, so a secret scanner reading this file sees no key
            "PRIVATE_KEY=0x" + "a" * 64: "a" * 64,
            "signer private key: " + "c" * 64: "c" * 64,
            "private_key: " + "b" * 64: "b" * 64,
            "postgres://bot:hunter2@db:5432/x": "hunter2",
            "password=sup3rsecret": "sup3rsecret",
            "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----": "abc",
            "xoxb-1234567890-abcdef": "xoxb-1234567890",
            "jwt eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4": "SflKxwRJ",
        }
        for text, secret in cases.items():
            out = of_hook.mask(text)
            self.assertNotIn(secret, out, text)
            self.assertIn("[masked:", out, text)

    def test_leaves_plain_text_alone(self):
        text = "Коммит abc123, PR #2349, sha 3f2e1d, pane w3:p5, 64 tests"
        self.assertEqual(of_hook.mask(text), text)

    def test_transaction_hashes_are_data_not_secrets(self):
        text = "tx 0x" + "d" * 64 + " in block hash 0x" + "e" * 64
        self.assertEqual(of_hook.mask(text), text)

    def test_clip_counts_bytes(self):
        text, cut = of_hook.clip("я" * 10, 7)
        self.assertTrue(cut)
        self.assertEqual(text, "я" * 3)


class ClassifierTest(unittest.TestCase):
    def test_question_heuristic(self):
        self.assertTrue(of_hook.asks_question("Готово. Коммитить?"))
        self.assertTrue(of_hook.asks_question("Без твоего «да» не запускаю."))
        self.assertTrue(of_hook.asks_question("Два вопроса пока ждут вашего ответа."))
        self.assertTrue(of_hook.asks_question("Plan ready, awaiting your decision."))
        self.assertFalse(of_hook.asks_question("API Error: 529 overloaded? retrying"))
        self.assertFalse(of_hook.asks_question("Done. All tests pass."))

    def test_owner_words(self):
        self.assertTrue(of_hook.carries_owner_words("The founder approved the merge, go ahead"))
        self.assertTrue(of_hook.carries_owner_words("Основатель сказал катить"))  # a name is in classify.json
        self.assertFalse(of_hook.carries_owner_words("tests pass, ready for review"))

    def test_prompt_origin(self):
        self.assertEqual(of_hook.prompt_origin('<cross-session-message from="x">hi', {}), "peer")
        self.assertEqual(of_hook.prompt_origin("<task-notification>done", {}), "scheduled")
        self.assertEqual(of_hook.prompt_origin("go", {"source": "poll_event"}), "scheduled")
        self.assertEqual(of_hook.prompt_origin("коммить", {}), "human")

    def test_question_testset_if_present(self):
        path = os.path.expanduser("~/acme/org/tools/founder-load/question_testset.jsonl")
        if not os.path.exists(path):
            self.skipTest("test set not on this machine")
        with open(path) as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        tp = sum(1 for r in rows if r["question_to_owner"] and (of_hook.asks_question(r["last_message"]) or r.get("tool_calls")))
        fp = sum(1 for r in rows if not r["question_to_owner"] and of_hook.asks_question(r["last_message"]))
        fn = sum(1 for r in rows if r["question_to_owner"]) - tp
        print(f"\nq-v1 on {len(rows)} labelled records: TP {tp} FP {fp} FN {fn}")
        self.assertGreaterEqual(tp / max(1, tp + fn), 0.85)
        self.assertGreaterEqual(tp / max(1, tp + fp), 0.85)


class HandleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        os.makedirs(os.path.join(root, "org", "registry"))
        with open(os.path.join(root, "org", "registry", "positions.yaml"), "w") as fh:
            fh.write(textwrap.dedent("""
                organization: test-org
                positions:
                  - {name: of_a, system: of, role: a, holder: {session: S1}, aliases: [old-a]}
                  - {name: of_b, system: of, role: b, holder: {session: S2}}
                """))
        with open(os.path.join(root, "orgs.yaml"), "w") as fh:
            fh.write("orgs:\n  - {name: t, path: org}\n")
        state = os.path.join(root, "state")
        self.saved = (of_events.STATE_DIR, of_hook.STATE, os.environ.get("OF_ORGS"), os.environ.get("HERDR_ENV"))
        of_events.STATE_DIR = of_hook.STATE = state
        os.environ["OF_ORGS"] = os.path.join(root, "orgs.yaml")
        os.environ.pop("HERDR_ENV", None)
        self.log = os.path.join(state, "of.db")

    def tearDown(self):
        of_events.STATE_DIR, of_hook.STATE = self.saved[0], self.saved[1]
        for k, v in (("OF_ORGS", self.saved[2]), ("HERDR_ENV", self.saved[3])):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def events(self):
        import of_store
        conn = of_store.connect()
        try:
            return [json.loads(r[0]) for r in conn.execute("SELECT record FROM event ORDER BY rowid")]
        finally:
            conn.close()

    def fire(self, name, **kw):
        of_hook.handle(name, dict(hook_event_name=name, session_id="S1", cwd="/x", **kw))

    def test_scheduler_prompts_are_never_the_owners(self):
        """2026-11-06: a cron prompt recorded as human closed the founder's ask c54997b8. Claude Code writes a
        system/scheduled_task_fire record before UserPromptSubmit and the prompt's own record (turnOrigin) after."""
        tr = os.path.join(self.tmp.name, "t.jsonl")
        cron = "Slack-watch (acme_dispatcher). search Slack for new messages"
        loop = "check the deploy and report"
        lines = [{"type": "assistant", "message": {"content": "done"}},
                 {"type": "system", "subtype": "turn_duration"},
                 {"type": "queue-operation", "operation": "enqueue"},
                 {"type": "system", "subtype": "scheduled_task_fire", "taskId": "45b2bbc8", "cron": "13,43 * * * *",
                  "prompt": cron}]
        with open(tr, "w") as fh:
            fh.write("\n".join(json.dumps(x) for x in lines) + "\n")
        self.fire("UserPromptSubmit", prompt=cron, transcript_path=tr, prompt_id="p1")
        with open(tr, "a") as fh:  # the cron prompt's own record, then a loop wake-up (ScheduleWakeup) fire
            fh.write(json.dumps({"type": "user", "promptId": "p1", "turnOrigin": "scheduled", "message": {"content": cron}}) + "\n")
            fh.write(json.dumps({"type": "assistant", "message": {"content": "ok"}}) + "\n")
            fh.write(json.dumps({"type": "system", "subtype": "scheduled_task_fire", "prompt": loop}) + "\n")
        self.fire("UserPromptSubmit", prompt=loop, transcript_path=tr, prompt_id="p2")
        with open(tr, "a") as fh:  # typed by a person: nothing between the last turn and the prompt
            fh.write(json.dumps({"type": "user", "promptId": "p2", "turnOrigin": "scheduled", "message": {"content": loop}}) + "\n")
            fh.write(json.dumps({"type": "assistant", "message": {"content": "ok"}}) + "\n")
        self.fire("UserPromptSubmit", prompt="да, удаляй", transcript_path=tr, prompt_id="p3")
        self.fire("UserPromptSubmit", prompt="да", transcript_path=os.path.join(self.tmp.name, "gone.jsonl"))
        got = [(e["of.payload"]["origin"], e["of.payload"].get("prompt_id")) for e in self.events()
               if e["event.name"] == "of.human_prompt"]
        self.assertEqual(got, [("scheduled", "p1"), ("scheduled", "p2"), ("human", "p3"), ("unknown", None)])
        long_ = "obs_keeper (alerts-watch) hourly sweep: " + "list active unsilenced alerts " * 12
        with open(tr, "a") as fh:  # Claude Code keeps only 200 characters of the prompt in the fire record
            fh.write(json.dumps({"type": "system", "subtype": "scheduled_task_fire", "prompt": long_[:199] + "…"}) + "\n")
        self.fire("UserPromptSubmit", prompt=long_, transcript_path=tr, prompt_id="p4")
        self.assertEqual([e["of.payload"]["origin"] for e in self.events() if e["event.name"] == "of.human_prompt"][-1],
                         "scheduled")
        size = os.path.getsize(tr)
        self.assertEqual(of_hook.record_origin(tr, 0, cron), "scheduled")
        self.assertIsNone(of_hook.record_origin(tr, size, "x"))

    def test_a_session_mark_holds_for_a_slow_resume(self):
        """of_capacity resumes a moved session with its note as the first prompt; a big session can take minutes."""
        import hashlib
        import time as _t
        note = "of_capacity moved this session to another account. Read the note and follow it."
        os.makedirs(of_hook.STATE, exist_ok=True)
        with open(os.path.join(of_hook.STATE, "injections.jsonl"), "a") as fh:
            for sess in ("S1", None):
                fh.write(json.dumps({"ts": _t.time() - 300, "session": sess, "injector": "of_capacity",
                                     "sha256": hashlib.sha256(note.encode()).hexdigest()}) + "\n")
        self.assertEqual((of_hook._injected("S1", note) or {}).get("session"), "S1")
        self.assertIsNone(of_hook._injected("S2", note))  # an unnamed mark keeps the 60 s window

    def test_correction_pass_takes_a_late_declaration(self):
        """A tool that declares its typing just after it (platform_owner's brief, 2026-11-06) is corrected later."""
        import hashlib
        import of_store
        brief = "You hold the position x_keeper. Read the brief."
        self.fire("UserPromptSubmit", prompt=brief)
        of_events.emit("injected_prompt", {"injector": "spawn", "target_session": "S1",
                                           "text_sha256": hashlib.sha256(brief.encode()).hexdigest()})
        conn = of_store.connect()
        seen, found, n = of_hook.correct_prompt_origins(conn, "2026-01-01")
        self.assertEqual([(f[4], f[5]) for f in found], [("injected", "of.injected_prompt")])
        self.assertEqual((n, of_hook.correct_prompt_origins(conn, "2026-01-01")[2]), (1, 0))
        self.fire("UserPromptSubmit", prompt="You hold the position net_keeper. Read the brief.")  # never declared
        self.fire("UserPromptSubmit", prompt="you hold the position, right?")                    # the owner's words
        found = of_hook.correct_prompt_origins(conn, "2026-01-01")[1]
        self.assertEqual([(f[4], f[5]) for f in found], [("injected", "brief frame")])
        conn.close()

    def test_an_open_gate_queues_every_turn_for_the_judge(self):
        # hand-over cc2ed950: "Скажи, если сделать" fired no detector, so the judge never read it
        import of_judge
        saved = (of_judge.gate, of_judge.queue, of_judge.spawn)
        markers = []
        of_judge.queue, of_judge.spawn = markers.append, lambda *a: True
        try:
            of_judge.gate = lambda: "detectors"
            self.fire("Stop", last_assistant_message="Исправил балансировщик. Скажи, если сделать.")
            self.assertEqual(markers, [])
            of_judge.gate = lambda: "all"
            self.fire("Stop", last_assistant_message="Исправил балансировщик. Скажи, если сделать.")
        finally:
            of_judge.gate, of_judge.queue, of_judge.spawn = saved
        turn = [e for e in self.events() if e["event.name"] == "of.turn_end"][-1]
        self.assertEqual([(m["ask_id"], m["turn"], m["position"]) for m in markers],
                         [(turn["of.event.id"], True, "of_a")])

    def test_question_answer_and_prompt_chain(self):
        self.fire("SessionStart", source="startup", model="claude-opus-5-5")
        self.fire("Stop", last_assistant_message="Всё готово. Коммитить?")
        self.fire("UserPromptSubmit", prompt="да, password=hunter22")
        of_hook.OUTPUT = None
        self.fire("PreToolUse", tool_name="AskUserQuestion", tool_use_id="t1",
                  tool_input={"questions": [{"header": "H", "question": "Q?", "options": [{"label": "A"}]}]})
        # founder, 2026-11-10: a position never asks him in the blocking dialog, even while he types in its pane
        out = of_hook.OUTPUT["hookSpecificOutput"]
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("of ask", out["permissionDecisionReason"])
        of_hook.OUTPUT = None
        self.fire("PostToolUse", tool_name="SendMessage", tool_input={"to": "old-a [3fa9c1]", "message": "founder approved it"},
                  tool_response={"success": True})
        ev = self.events()
        names = [e["event.name"] for e in ev]
        self.assertEqual(names, ["of.session_start", "of.turn_end", "of.agent_question", "of.human_prompt",
                                 "of.mandate_check", "of.agent_message_out"])
        self.assertEqual((ev[4]["of.payload"]["verdict"], ev[4]["of.payload"]["decision"]), ("ask_dialog", "deny"))
        self.assertTrue(all(e["gen_ai.agent.name"] == "of_a" and e["of.org"] == "test-org" for e in ev))
        self.assertEqual(ev[3]["gen_ai.request.model"], "claude-opus-5-5")
        heur = ev[2]["of.payload"]
        self.assertEqual((heur["detected_by"], heur["classifier_version"], heur["heuristic"]), ("heuristic", "q-v5", True))
        prompt = ev[3]["of.payload"]
        self.assertEqual(prompt["reply_to"], ev[2]["of.event.id"])
        self.assertEqual(prompt["origin"], "human")
        self.assertNotIn("hunter22", prompt["text"])
        out = ev[5]["of.payload"]
        self.assertEqual((out["to_position"], out["delivery"], out["carries_owner_words"]), ("of_a", "delivered", True))
        self.assertEqual(oct(os.stat(self.log).st_mode & 0o777), "0o600")

    def test_a_session_holding_no_position_may_still_ask_in_the_dialog(self):
        hook = lambda name, **kw: of_hook.handle(name, dict(hook_event_name=name, session_id="S-own", cwd="/x", **kw))  # noqa: E731
        of_hook.OUTPUT = None
        hook("PreToolUse", tool_name="AskUserQuestion", tool_use_id="t1",
             tool_input={"questions": [{"header": "H", "question": "Q?", "options": [{"label": "A"}]}]})
        self.assertIsNone(of_hook.OUTPUT)  # the founder's own sessions: recorded, not denied
        hook("PostToolUse", tool_name="AskUserQuestion", tool_use_id="t1", tool_response={"answers": {"Q?": "A"}})
        ev = [e for e in self.events() if e.get("gen_ai.conversation.id") == "S-own"]
        self.assertEqual([e["event.name"] for e in ev], ["of.agent_question", "of.agent_question_answered"])
        self.assertEqual(ev[1]["of.payload"]["reply_to"], ev[0]["of.event.id"])

    def test_continuation_manifest_written_on_change_only(self):
        crons = [{"id": "c1", "cron": "*/20 * * * *", "prompt": "check CI, token=" + "abcdef123456"}]  # split for scanners
        self.fire("Stop", last_assistant_message="ok", session_crons=crons, background_tasks=[])
        self.fire("Stop", last_assistant_message="ok", session_crons=crons, background_tasks=[])
        self.fire("Stop", last_assistant_message="ok", session_crons=[], background_tasks=[])
        cont = [e for e in self.events() if e["event.name"] == "of.continuation"]
        self.assertEqual([c["of.payload"]["crons"] for c in cont], [1, 0])
        self.assertNotIn("abcdef123456", json.dumps(cont[0]))

    def test_peer_message_and_injected_prompt(self):
        self.fire("UserPromptSubmit", prompt='<cross-session-message from="uds:x" from-name="of_b">hello</cross-session-message>')
        import hashlib
        import time
        os.makedirs(of_hook.STATE, exist_ok=True)
        with open(os.path.join(of_hook.STATE, "injections.jsonl"), "a") as fh:
            fh.write(json.dumps({"ts": time.time(), "session": "S1", "injector": "budget-governor",
                                 "sha256": hashlib.sha256(b"Limit topped up").hexdigest()}) + "\n")
        self.fire("UserPromptSubmit", prompt="Limit topped up")
        ev = self.events()
        self.assertEqual(ev[0]["event.name"], "of.agent_message_in")
        self.assertEqual((ev[0]["of.payload"]["from"], ev[0]["of.payload"]["text"]), ("of_b", "hello"))
        self.assertEqual((ev[1]["of.payload"]["origin"], ev[1]["of.payload"]["injector"]), ("injected", "budget-governor"))
        answer = "Answer from the founder to your question 1234abcd: yes"  # carried by the asks popup
        with open(os.path.join(of_hook.STATE, "injections.jsonl"), "a") as fh:
            fh.write(json.dumps({"ts": time.time(), "session": "S1", "injector": None, "origin": "human",
                                 "channel": "asks-ui", "sha256": hashlib.sha256(answer.encode()).hexdigest()}) + "\n")
        self.fire("UserPromptSubmit", prompt=answer)
        p = self.events()[2]["of.payload"]
        self.assertEqual((p["origin"], p["channel"], p.get("injector")), ("human", "asks-ui", None))
        relayed = "Message from zeta:x (via relay, authority: none)\nlong text"  # typed long: wrapped as a paste
        with open(os.path.join(of_hook.STATE, "injections.jsonl"), "a") as fh:
            fh.write(json.dumps({"ts": time.time(), "session": "S1", "injector": "of-relay:zeta:x",
                                 "sha256": hashlib.sha256(relayed.encode()).hexdigest()}) + "\n")
        self.fire("UserPromptSubmit", prompt=f'<pasted_content id="9">\n{relayed}\n</pasted_content id="9">')
        e = self.events()[3]  # a relay delivery is a peer's message, never the owner's
        self.assertEqual((e["event.name"], e["of.payload"]["from"], e["of.payload"]["via"]),
                         ("of.agent_message_in", "zeta:x", "relay"))
        self.assertTrue(e["of.payload"]["text"].startswith("Message from zeta:x"))

    def test_busy_database_spools_and_the_worker_drains(self):
        import io
        import sqlite3
        import time
        import of_store
        of_store.connect().close()                              # create the schema
        blocker = sqlite3.connect(os.path.join(of_hook.STATE, "of.db"), isolation_level=None)
        blocker.execute("BEGIN IMMEDIATE")                       # another writer holds the lock
        old_stdin, old_ingest = sys.stdin, of_hook._ingest_detached
        of_hook._ingest_detached = lambda p: None
        of_events._CONN.clear()
        try:
            sys.stdin = io.StringIO(json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "S1",
                                                "prompt": "hello", "cwd": "/x"}))
            t0 = time.time()
            of_hook.main(["UserPromptSubmit"])
            self.assertLess(time.time() - t0, 2.0)               # did not wait out a long busy timeout
        finally:
            sys.stdin, of_hook._ingest_detached = old_stdin, old_ingest
            of_events.HOOK_TIMEOUT = None
            of_events._CONN.clear()
            blocker.execute("ROLLBACK")
            blocker.close()
        spool = os.path.join(of_hook.STATE, "spool")
        self.assertTrue(os.path.getsize(os.path.join(spool, "events.jsonl")) > 0)
        self.assertTrue(os.path.getsize(os.path.join(spool, "hook-raw.jsonl")) > 0)
        conn = of_store.connect()
        self.assertEqual(of_store.drain_spool(conn), 2)          # one event, one raw payload
        self.assertEqual([e["event.name"] for e in self.events()], ["of.human_prompt"])
        self.assertEqual(conn.execute("SELECT count(*) FROM hook_record").fetchone()[0], 1)
        self.assertEqual(of_store.drain_spool(conn), 0)
        # a run that stopped midway left a .draining file: the next run drains it (2026-11-06)
        left = of_store.spool_path("events.jsonl") + ".draining.99999"
        rec = of_events.record("pulse", {"n": 1}, position="of_a")
        with open(left, "w") as fh:
            fh.write(json.dumps(rec) + "\n")
        self.assertEqual(of_store.drain_spool(conn), 1)
        self.assertFalse(os.path.exists(left))
        # a busy store keeps the unread rest of the file for the next run
        recs = [of_events.record("pulse", {"n": i}, position="of_a") for i in (2, 3)]
        with open(left, "w") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in recs))
        calls = {"n": 0}
        real = of_store.add_event

        def flaky(c, r, raw_ref=None):
            calls["n"] += 1
            if calls["n"] == 2:
                raise __import__("sqlite3").OperationalError("database is locked")
            return real(c, r, raw_ref)
        of_store.add_event = flaky
        try:
            self.assertEqual(of_store.drain_spool(conn), 1)
        finally:
            of_store.add_event = real
        with open(left) as fh:
            self.assertEqual(len(fh.readlines()), 1)  # only the unread line is kept
        self.assertEqual(of_store.drain_spool(conn), 1)
        conn.close()

    def test_org_resolved_from_position_or_cwd(self):
        with open(os.environ["OF_ORGS"], "w") as fh:
            fh.write("orgs:\n  - {name: t, path: org, paths: ['%s/work']}\n" % self.tmp.name)
        rec = of_events.record("nudge_sent", {}, position="of_b")
        self.assertEqual(rec["of.org"], "test-org")                      # by position
        of_hook.handle("Stop", {"hook_event_name": "Stop", "session_id": "unbound", "cwd": f"{self.tmp.name}/work/x",
                                "last_assistant_message": "ok"})
        self.assertEqual(self.events()[-1]["of.org"], "t")                 # by working directory

    def test_enrolled_position_gets_its_mandate_at_session_start(self):
        import io
        reg = os.path.join(self.tmp.name, "org", "registry", "positions.yaml")
        os.makedirs(os.path.join(self.tmp.name, "org", "registry", "briefs"), exist_ok=True)
        with open(os.path.join(self.tmp.name, "org", "registry", "briefs", "a.md"), "w") as fh:
            fh.write("# Brief: of_a\nMandate 1: build the thing.")
        with open(os.path.join(self.tmp.name, "org", "AGENTS.md"), "w") as fh:
            fh.write("# Org\n## Rules\n- Documents in English.\n## Other\n- ignore me\n")
        with open(reg, "w") as fh:
            fh.write("organization: test-org\npositions:\n"
                     "  - {name: of_a, system: of, role: a, summary: 'build it', status: active, enrolled: true,"
                     " mandate: registry/briefs/a.md, holder: {session: S1}}\n"
                     "  - {name: of_b, system: of, role: b, summary: 'review it', status: active, holder: {session: S2}}\n")
        outs = {}
        for sid in ("S1", "S2"):
            old_in, old_out = sys.stdin, sys.stdout
            sys.stdin, sys.stdout = io.StringIO(json.dumps({"hook_event_name": "SessionStart", "session_id": sid,
                                                            "source": "startup", "cwd": "/x"})), io.StringIO()
            of_hook.OUTPUT = None
            try:
                of_hook.main(["SessionStart"])
                outs[sid] = sys.stdout.getvalue()
            finally:
                sys.stdin, sys.stdout = old_in, old_out
        ctx = json.loads(outs["S1"])["hookSpecificOutput"]["additionalContext"]
        for piece in ("`of_a`", "build it", "Mandate 1: build the thing.", "Documents in English", "`of_b` (b): review it"):
            self.assertIn(piece, ctx)
        self.assertNotIn("ignore me", ctx)
        self.assertEqual(outs["S2"], "")                     # not enrolled: no context
        self.assertIn("of.context_injected", [e["event.name"] for e in self.events()])

    def test_a_guard_warning_carries_its_rule_note(self):
        """docs/guard.md G1.9: an env file outside ~/.config/<org> is allowed with that rule's own note (not the
        bare-op one) and recorded as of.mandate_check {verdict: warn}."""
        of_hook.OUTPUT = None
        self.fire("PreToolUse", tool_name="Bash", tool_input={"command": "grep -c KEY deploy/pins.env"})
        out = of_hook.OUTPUT["hookSpecificOutput"]
        self.assertNotIn("permissionDecision", out)
        self.assertIn("an env file (pins.env)", out["additionalContext"])
        self.assertIn("G1.9", out["additionalContext"])
        self.assertNotIn("bare op", out["additionalContext"])
        checks = [e for e in self.events() if e.get("event.name") == "of.mandate_check"]
        self.assertEqual([(c.get("of.payload") or {}).get("verdict") for c in checks], ["warn"])

    def test_plain_tool_events_write_nothing(self):
        self.fire("PreToolUse", tool_name="Bash", tool_input={"command": "ls"})
        self.fire("PostToolUse", tool_name="Bash", tool_input={"command": "ls"})
        self.assertFalse(os.path.exists(self.log))

    def test_raw_hook_json_and_transcript_ingest(self):
        import io
        import of_store
        tr = os.path.join(self.tmp.name, "S1.jsonl")
        lines = [{"uuid": "u1", "sessionId": "S1", "type": "user", "timestamp": "t1", "message": {"content": "hi"}},
                 {"uuid": "u2", "sessionId": "S1", "type": "assistant", "timestamp": "t2",
                  "message": {"model": "m", "usage": {"input_tokens": 5, "output_tokens": 7},
                              "content": [{"type": "tool_use", "name": "Bash"}]}}]
        with open(tr, "w") as fh:
            fh.write("\n".join(json.dumps(x) for x in lines) + "\n" + '{"uuid": "u3", "partial')
        raw = json.dumps({"hook_event_name": "Stop", "session_id": "S1", "last_assistant_message": "ok",
                          "transcript_path": tr})
        old_stdin, sys.stdin = sys.stdin, io.StringIO(raw)
        old_ingest, of_hook._ingest_detached = of_hook._ingest_detached, lambda p: self.ingested.append(p)
        self.ingested = []
        try:
            of_hook.main(["Stop"])
        finally:
            sys.stdin, of_hook._ingest_detached = old_stdin, old_ingest
        self.assertEqual(self.ingested, [tr])
        conn = of_store.connect()
        sha = conn.execute("SELECT blob_sha256 FROM hook_record").fetchone()[0]
        self.assertEqual(of_store.get_blob(conn, sha).decode(), raw)
        self.assertEqual(conn.execute("SELECT raw_ref FROM event").fetchone()[0], "hook_record:1")
        self.assertEqual(of_store.ingest_file(conn, tr), 2)   # the partial last line waits
        self.assertEqual(of_store.ingest_file(conn, tr), 0)   # idempotent
        with open(tr, "a") as fh:
            fh.write('": 1}\n')
        self.assertEqual(of_store.ingest_file(conn, tr), 1)   # completed later
        row = conn.execute("SELECT model, tool_names, input_tokens, output_tokens FROM transcript_record"
                           " WHERE record_uuid = 'u2'").fetchone()
        self.assertEqual(row, ("m", "Bash", 5, 7))
        copy = os.path.join(self.tmp.name, "copy-S1.jsonl")
        with open(tr) as a, open(copy, "w") as b:
            b.write(a.read())
        self.assertEqual(of_store.ingest_file(conn, copy), 1)  # only u3's twin lacks a uuid... dedupe by session+uuid
        hits = conn.execute("SELECT count(*) FROM event_fts WHERE event_fts MATCH 'ok'").fetchone()[0]
        self.assertEqual(hits, 1)
        conn.close()

    def test_subagent_transcripts_ride_along(self):
        import of_store
        tr = os.path.join(self.tmp.name, "S9.jsonl")
        sub = os.path.join(self.tmp.name, "S9", "subagents")
        os.makedirs(sub)
        line = {"type": "assistant", "sessionId": "S9", "timestamp": "2026-11-06T08:00:00Z"}
        with open(tr, "w") as fh:
            fh.write(json.dumps(dict(line, uuid="m1")) + "\n")
        with open(os.path.join(sub, "agent-a1.jsonl"), "w") as fh:
            fh.write(json.dumps(dict(line, uuid="a1", isSidechain=True)) + "\n")
        paths = of_store.with_subagents(tr)
        self.assertEqual(paths, [tr, os.path.join(sub, "agent-a1.jsonl")])
        self.assertEqual(of_store.with_subagents(paths[1]), [paths[1]])  # a subagent has no subagents dir
        conn = of_store.connect()
        self.assertEqual(sum(of_store.ingest_file(conn, p) for p in paths), 2)
        self.assertEqual(sum(of_store.ingest_file(conn, p) for p in paths), 0)  # cursors keep it cheap
        conn.close()

    def test_unknown_session_has_no_position_and_never_raises(self):
        of_hook.handle("Stop", {"hook_event_name": "Stop", "session_id": "nobody", "last_assistant_message": "ok"})
        self.assertIsNone(self.events()[0]["gen_ai.agent.name"])


if __name__ == "__main__":
    unittest.main()


class StoreTxTest(unittest.TestCase):
    """The store's write transactions: never left open, never waited on by their own thread."""

    def setUp(self):
        import of_store
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "of.db")
        self.a = of_store.connect(self.path, timeout=5)
        self.b = of_store.connect(self.path, timeout=5)

    def tearDown(self):
        self.a.close()
        self.b.close()
        self.tmp.cleanup()

    def rec(self, eid):
        return {"of.event.id": eid, "timestamp": "2026-11-06T12:00:00Z", "event.name": "of.pulse"}

    def test_the_wal_file_shrinks_after_a_checkpoint(self):
        # 2026-11-08: a 2.9 GB of.db-wal held 651 live frames; SQLite keeps the file at its largest size unless limited
        import of_store
        self.assertEqual(self.a.execute("PRAGMA journal_size_limit").fetchone()[0], of_store.WAL_LIMIT)
        self.a.execute(f"PRAGMA journal_size_limit = {64 * 1024}")  # the same mechanism, at a size a test can reach
        self.a.execute("CREATE TABLE big (x)")
        self.a.execute("BEGIN")
        self.a.executemany("INSERT INTO big VALUES (?)", [("x" * 1000,)] * 2000)
        self.a.execute("COMMIT")
        self.assertGreater(os.path.getsize(self.path + "-wal"), 1024 * 1024)
        self.a.execute("PRAGMA wal_checkpoint(PASSIVE)")
        self.a.execute("INSERT INTO big VALUES ('y')")
        self.assertLessEqual(os.path.getsize(self.path + "-wal"), 64 * 1024)

    def test_a_second_connection_inside_the_transaction_fails_at_once(self):
        import time
        import of_store
        t0 = time.time()
        with self.assertRaises(of_store.SelfDeadlock):
            with of_store.write_tx(self.a):
                of_store.add_event(self.b, self.rec("e1"))
        self.assertLess(time.time() - t0, 1)  # no wait on its own lock
        self.assertFalse(self.a.in_transaction)  # rolled back, the lock is free
        of_store.add_event(self.b, self.rec("e2"))  # and the other connection can write again

    def test_an_error_inside_rolls_back(self):
        import of_store
        with self.assertRaises(ValueError):
            with of_store.write_tx(self.a):
                self.a.execute("INSERT INTO meta VALUES ('x', '1')")
                raise ValueError
        self.assertFalse(self.a.in_transaction)
        self.assertIsNone(self.b.execute("SELECT 1 FROM meta WHERE key = 'x'").fetchone())

    def test_on_record_writing_elsewhere_cannot_hold_the_lock(self):
        import time
        import of_store
        tr = os.path.join(self.tmp.name, "S1.jsonl")
        with open(tr, "w") as fh:
            for i in range(3):
                fh.write(json.dumps({"uuid": f"u{i}", "sessionId": "S1", "timestamp": "2026-11-06T12:00:00Z"}) + "\n")
        errors = []

        def on_record(obj):
            try:
                of_store.add_event(self.b, self.rec(obj["uuid"]))
            except of_store.SelfDeadlock as e:
                errors.append(e)
                raise
        t0 = time.time()
        self.assertEqual(of_store.ingest_file(self.a, tr, on_record=on_record), 3)
        self.assertLess(time.time() - t0, 1)
        # on_record now runs while the lines are prepared, outside the transaction: its writes go through
        self.assertEqual(len(errors), 0)
        self.assertEqual(self.b.execute("SELECT count(*) FROM event").fetchone()[0], 3)
        self.assertFalse(self.a.in_transaction)
        of_store.add_event(self.b, self.rec("after"))  # the lock is free


class ForkTest(unittest.TestCase):
    """A fork copies its parent's records under its own session id: counted once (transcript_own)."""

    def test_fork_found_and_its_copies_left_out(self):
        import time
        import of_store
        with tempfile.TemporaryDirectory() as d:
            conn = of_store.connect(os.path.join(d, "of.db"))
            rec = lambda sid, u, t: json.dumps({"type": "assistant", "uuid": u, "sessionId": sid,
                                                "timestamp": f"2026-11-06T10:00:0{t}Z"}) + "\n"
            parent, child = os.path.join(d, "P.jsonl"), os.path.join(d, "C.jsonl")
            with open(parent, "w") as fh:
                fh.write(rec("P", "u1", 1) + rec("P", "u2", 2) + rec("P", "u3", 3))
            time.sleep(0.02)
            with open(child, "w") as fh:  # born later: the first two copied, then its own
                fh.write(rec("C", "u1", 1) + rec("C", "u2", 2) + rec("C", "c1", 4))
            for p in (parent, child):
                of_store.ingest_file(conn, p)
            self.assertEqual(of_store.detect_forks(conn), [])  # no index yet: nothing (and no scan)
            conn.execute("CREATE INDEX transcript_uuid ON transcript_record (record_uuid)")
            child2 = os.path.join(d, "C2.jsonl")  # with the index, ingest marks a fork with its first rows
            with open(child2, "w") as fh:
                fh.write(rec("C2", "u1", 1) + rec("C2", "c2", 5))
            of_store.ingest_file(conn, child2)
            self.assertEqual(conn.execute("SELECT parent_session_id, shared FROM session_fork WHERE session_id = 'C2'")
                             .fetchone(), ("P", 1))
            self.assertEqual(of_store.detect_forks(conn, ["C"]), [("C", "P", 2)])
            self.assertIn(("C", "P", 2), of_store.detect_forks(conn))  # the same from either side
            own = conn.execute("SELECT session_id, record_uuid FROM transcript_own ORDER BY 1, 2").fetchall()
            self.assertEqual(own, [("C", "c1"), ("C2", "c2"), ("P", "u1"), ("P", "u2"), ("P", "u3")])
            conn.close()


class ShipTest(unittest.TestCase):
    def test_ships_new_rows_once_and_advances_cursor(self):
        import of_ship
        import of_store
        with tempfile.TemporaryDirectory() as root:
            saved = of_events.STATE_DIR
            of_events.STATE_DIR = root
            try:
                conn = of_store.connect()
                of_store.add_event(conn, of_events.record("turn_end", {"last_message": "ok"}, session="S"))
                tr = os.path.join(root, "S.jsonl")
                with open(tr, "w") as fh:
                    fh.write(json.dumps({"uuid": "u1", "sessionId": "S", "timestamp": "2026-11-05T10:00:00Z"}) + "\n")
                of_store.ingest_file(conn, tr)
                sent = []

                class FakeCH:
                    def __init__(self, cfg):
                        pass

                    def insert(self, database, table, rows):
                        sent.append((database, table, rows))
                old = of_ship.ClickHouse
                of_ship.ClickHouse = FakeCH
                try:
                    cfg = dict(of_ship.DEFAULTS, mode="direct")
                    orgs = os.path.join(root, "orgs.yaml")
                    with open(orgs, "w") as fh:
                        fh.write("orgs: []\n")
                    os.environ["OF_ORGS"] = orgs
                    of_ship.ship(log=lambda m: None, cfg=cfg)
                    of_ship.ship(log=lambda m: None, cfg=cfg)
                finally:
                    of_ship.ClickHouse = old
                    os.environ.pop("OF_ORGS", None)
                self.assertEqual(sorted((d, t, len(r)) for d, t, r in sent),
                                 [("of_unassigned", "event", 1), ("of_unassigned", "transcript_record", 1),
                                  ("of_vault", "transcript_record", 1)])
                by = {(d, t): r for d, t, r in sent}
                self.assertEqual(json.loads(by[("of_vault", "transcript_record")][0]["raw"])["uuid"], "u1")
                self.assertEqual(by[("of_unassigned", "transcript_record")][0]["raw"], "")  # the gate
                conn.close()
            finally:
                of_events.STATE_DIR = saved


class RouterTest(unittest.TestCase):
    def test_one_database_per_organization(self):
        import of_ship
        home = os.path.expanduser("~")
        with tempfile.TemporaryDirectory() as root:
            orgs = os.path.join(root, "orgs.yaml")
            with open(orgs, "w") as fh:
                fh.write("orgs:\n  - {name: zeta, database: db_zeta, paths: [~/acme]}\n"
                         "  - {name: ofb, database: db_ofb, paths: [~/organic-framework]}\n")
            r = of_ship.Router(dict(of_ship.DEFAULTS), orgs)
            r.org_by_session = {"S-zeta": "zeta"}
            self.assertEqual(r.org_of("ofb"), "ofb")
            self.assertEqual(r.org_of(None, "S-zeta"), "zeta")
            self.assertEqual(r.org_of(None, None, home + "/acme/storefront/x"), "zeta")
            self.assertEqual(r.org_of(None, None, "~/organic-framework"), "ofb")
            self.assertEqual(r.org_of(None, None, None,
                                      home + "/.claude-x/projects/" + re.sub(r"[^A-Za-z0-9]", "-", home) + "-acme-storefront/abc.jsonl"),
                             "zeta")  # Claude Code's project folder: the cwd, every non-alphanumeric as '-'; any home
            self.assertIsNone(r.org_of(None, None, "/tmp/elsewhere"))
            self.assertEqual(r.database(None), "of_unassigned")
            self.assertEqual(r.database("zeta"), "db_zeta")
            r.org_by_session = {"S-fa": "zeta"}  # an OF position that serves Acme
            self.assertEqual(r.org_of("ofb", "S-fa", "~/organic-framework"), "zeta")
            self.assertEqual(r.org_of("ofb", None, None, serves="zeta"), "zeta")


class PublishedCopyTest(unittest.TestCase):
    def test_receiving_copy_keeps_reference_grant_and_note_only(self):
        import of_ship
        payload = {"from_org": "zeta", "to_org": "ofb", "artifact": "docs/x.md", "grant": "d7", "note": "generic",
                   "tool_use_id": "t", "transcript_record": "u", "edited_at": "ts"}
        rec = of_events.record("published", payload, position="p", org="ofb", session="S")
        out = {"name": "of.published", "org_id": "zeta", "session_id": "S", "raw_ref": "hook_record:1",
               "payload": json.dumps(payload), "record": json.dumps(rec)}
        c = of_ship.published_copy(out, payload)
        self.assertEqual(json.loads(c["payload"]), {k: payload[k] for k in ("from_org", "to_org", "artifact", "grant", "note")})
        self.assertEqual((c["session_id"], c["raw_ref"], c["org_id"]), ("", "", "ofb"))
        self.assertNotIn("transcript_record", c["record"])
        self.assertNotIn("gen_ai.conversation.id", json.loads(c["record"]))


class ShapeTest(unittest.TestCase):
    def test_shape_carries_process_not_content(self):
        import of_ship
        payload = {"origin": "human", "channel": "herdr", "chars": 120, "reply_to": "e1",
                   "text": "secret Acme detail", "questions": [{"question": "q"}, {"question": "r"}]}
        rec = of_events.record("human_prompt", payload, position="of_field-analyst", org="ofb", session="S",
                               extra={"of.serves": "zeta", "of.cwd": "~/acme/x", "of.transcript": {"path": "p"}})
        out = {"name": "of.human_prompt", "org_id": "zeta", "session_id": "S", "raw_ref": "hook_record:3",
               "payload": json.dumps(payload), "record": json.dumps(rec)}
        sh = of_ship.shape_event(out, "ofb")
        p = json.loads(sh["payload"])
        self.assertEqual(p, {"origin": "human", "channel": "herdr", "chars": 120, "reply_to": "e1", "questions_count": 2})
        self.assertNotIn("Acme", sh["record"])
        self.assertNotIn("of.cwd", json.loads(sh["record"]))
        self.assertEqual((sh["org_id"], sh["raw_ref"], json.loads(sh["record"])["of.shape"]), ("ofb", "", True))
        t = of_ship.shape_transcript({"raw": "content", "source_path": "/x/-Users-alex-acme/y", "tool_names": "Read",
                                      "sha256": "a" * 64, "org_id": "zeta"}, "ofb")
        self.assertEqual((t["raw"], t["source_path"], t["tool_names"], t["org_id"]), ("", "", "Read", "ofb"))

    def test_builder_only_when_routed_elsewhere(self):
        import of_ship
        with tempfile.TemporaryDirectory() as root:
            orgs = os.path.join(root, "orgs.yaml")
            with open(orgs, "w") as fh:
                fh.write("orgs:\n  - {name: zeta, database: db_zeta}\n  - {name: ofb, database: db_ofb}\n")
            r = of_ship.Router(dict(of_ship.DEFAULTS), orgs)
            r.own_org_by_session = {"S-fa": "ofb", "S-zeta": "zeta"}
            self.assertEqual(r.builder_of("zeta", "S-fa"), "ofb")
            self.assertIsNone(r.builder_of("zeta", "S-zeta"))
            self.assertIsNone(r.builder_of("ofb", "S-fa"))



class PerOrgWireTest(unittest.TestCase):
    def test_content_to_source_server_copies_to_other(self):
        import of_ship
        with tempfile.TemporaryDirectory() as root:
            orgs = os.path.join(root, "orgs.yaml")
            with open(orgs, "w") as fh:
                fh.write("orgs:\n  - {name: zeta, database: db_zeta, server: 'https://zeta.example'}\n"
                         "  - {name: ofb, database: db_ofb}\n")
            cfg = dict(of_ship.DEFAULTS)
            r = of_ship.Router(cfg, orgs)
            r.org_by_session, r.own_org_by_session = {"FA": "zeta", "B": "ofb"}, {"FA": "ofb", "B": "ofb"}
            servers = of_ship.org_servers(cfg, orgs)
            default = cfg["server_url"]

            def ev(eid, sid, name="of.turn_end", payload=None, serves=None):
                rec = {"of.event.id": eid, "timestamp": "t", "event.name": name, "of.org": "ofb",
                       **({"of.serves": serves} if serves else {}), "of.payload": payload or {"last_message": "secret"}}
                return {"event_id": eid, "org": "ofb", "ts": "t", "name": name, "session_id": sid,
                        "payload": json.dumps(payload or {"last_message": "secret"}), "raw_ref": "r", "record": json.dumps(rec)}
            batch = [ev("e1", "FA", serves="zeta"), ev("e2", "B"),
                     ev("e3", "FA", "of.published", {"from_org": "zeta", "to_org": "ofb", "artifact": "a", "note": "n",
                                                     "transcript_record": "u"}, serves="zeta")]
            wire = of_ship.plan_wire("event", batch, r, servers, default)
            zeta = wire["https://zeta.example"]
            local = wire[default]
            self.assertEqual([(x["data"]["event_id"], x.get("copy")) for x in zeta], [("e1", None), ("e3", None)])
            self.assertEqual(sorted((x["data"]["event_id"], x.get("copy")) for x in local),
                             [("e1", "shape"), ("e2", None), ("e3", "published")])
            shape = next(x for x in local if x.get("copy") == "shape")["data"]
            self.assertNotIn("secret", shape["payload"] + shape["record"])
            pub = next(x for x in local if x.get("copy") == "published")["data"]
            self.assertNotIn("transcript_record", pub["payload"])
            self.assertTrue(all(x["data"]["org_id"] == "zeta" for x in zeta))


class RawConsentTest(unittest.TestCase):
    def test_typed_only_org_sends_no_raw(self):
        import tempfile
        import of_ship
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "orgs.yaml")
            with open(f, "w") as fh:
                fh.write("orgs:\n  - {name: zeta, server: https://zeta.example}\n"
                         "  - {name: ofb, server: https://ofb.example, raw: true}\n  - {name: home}\n"
                         "  - {name: own, raw: false}\n")
            consent = of_ship.raw_consent(f)
        self.assertEqual(consent, {"zeta": False, "ofb": True, "home": True, "own": False})
        self.assertFalse(of_ship.raw_allowed("unknown", consent))

        class R:
            def org_of(self, *a, **k):
                return None

            def builder_of(self, *a, **k):
                return None
        rec = {"session_id": "S", "raw": "SECRET CONTENT", "record_uuid": "u"}
        for org, sent in (("zeta", ""), ("ofb", "SECRET CONTENT")):
            orig = of_ship.route_org
            of_ship.route_org = lambda table, d, router, _o=org: _o
            try:
                wire = of_ship.plan_wire("transcript_record", [rec], R(), {"zeta": "m", "ofb": "o"}, "x", consent)
            finally:
                of_ship.route_org = orig
            (records,) = wire.values()
            self.assertEqual(records[0]["data"]["raw"], sent)
            self.assertEqual(records[0]["data"].get("raw_withheld", False), not sent)


class GuardTest(unittest.TestCase):
    def test_critical_denies(self):
        deny = [("Bash", {"command": "printenv"}), ("Bash", {"command": "env | grep KEY"}),
                ("Bash", {"command": "cat .env"}), ("Bash", {"command": "cat config/.env.prod"}),
                ("Bash", {"command": "echo $OPENAI_API_KEY"}), ("Bash", {"command": "tail -5 ~/.aws/credentials"}),
                ("Bash", {"command": "security find-generic-password -s x -w"}),
                ("Read", {"file_path": "/repo/.env"}), ("Read", {"file_path": "/repo/certs/server.pem"}),
                ("Read", {"file_path": "/home/alex/.ssh/id_ed25519"})]
        allow = [("Bash", {"command": "cat README.md"}), ("Bash", {"command": "cat .env.example"}),
                 ("Bash", {"command": "echo $HOME"}), ("Bash", {"command": "git status"}),
                 ("Read", {"file_path": "/repo/.env.example"}), ("Read", {"file_path": "/home/alex/.ssh/id_ed25519.pub"}),
                 ("Read", {"file_path": "/repo/src/environment.py"}), ("Bash", {"command": "make env-check"})]
        deny += [("Bash", {"command": "head -20 config/secrets/prod.json"}), ("Bash", {"command": "cat certs/x.p12"}),
                 ("Read", {"file_path": "/repo/config/secrets/prod.json"}), ("Bash", {"command": "jq . ~/.netrc"})]
        for tool, ti in deny:
            self.assertTrue(of_guard.critical_deny(tool, ti), (tool, ti))
        for tool, ti in allow:
            self.assertIsNone(of_guard.critical_deny(tool, ti), (tool, ti))

    def test_installer_flags(self):
        import of_init
        s = of_init.with_hooks({}, "/of", deny_secrets=True, guard=True)
        self.assertIn("Read(**/.env)", s["permissions"]["deny"])
        self.assertIn("Read(**/secrets/**)", s["permissions"]["deny"])
        self.assertNotIn("Bash(cat .env*)", s["permissions"]["deny"])  # it denied .env.example too
        self.assertFalse(any(r in s["permissions"]["deny"] for r in ("Read(**/.env.*)", "Read(**/id_rsa*)")))
        self.assertTrue(any(g.get("matcher") == of_init.GUARD_MATCHER for g in s["hooks"]["PreToolUse"]))
        self.assertTrue(any(g.get("matcher") == of_init.GUARD_MCP_MATCHER for g in s["hooks"]["PreToolUse"]))  # G16
        self.assertEqual(of_init.with_hooks(s, "/of", deny_secrets=True, guard=True), s)  # idempotent
        self.assertNotIn("PreToolUse", of_init.without_hooks(s, "/of").get("hooks") or {})
        self.assertNotIn("permissions", of_init.with_hooks({}, "/of"))
        back = of_init.without_hooks(dict(s, permissions=dict(s["permissions"], deny=s["permissions"]["deny"] + ["Bash(rm:*)"])), "/of")
        self.assertEqual(back["permissions"]["deny"], ["Bash(rm:*)"])  # the owner's own rules stay


class GuardProbeTest(unittest.TestCase):
    def test_install_uses_the_interpreter_and_the_probe_passes(self):
        import of_init
        s = of_init.with_hooks({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": f"{of_init.OF} hook Stop"}]}]}},
                               of_init.OF, deny_secrets=True, guard=True)
        cmd = s["hooks"]["Stop"][0]["hooks"][0]["command"]
        self.assertTrue(cmd.startswith(sys.executable + " "), cmd)   # the old command was upgraded in place
        self.assertEqual(len(s["hooks"]["Stop"]), 1)
        ok, detail = of_init.guard_works(s)
        self.assertTrue(ok, detail)
        self.assertEqual(of_init.guard_works({})[0], None)


class ColdIndexTest(unittest.TestCase):
    def test_hook_with_a_cold_index_records(self):
        """of_lab, 2026-11-06: the hook fast path re-entered itself when the index was rebuilt (RecursionError)."""
        import subprocess
        import tempfile
        of_path = os.path.join(os.path.dirname(__file__), "..", "bin", "of")
        with tempfile.TemporaryDirectory() as t:
            r = subprocess.run([sys.executable, of_path, "hook", "Stop"], input='{"session_id":"x","hook_event_name":"Stop","cwd":"/tmp"}',
                               capture_output=True, text=True, timeout=60, env=dict(os.environ, OF_STATE=t))
            self.assertEqual(r.returncode, 0, r.stderr)
            errors = os.path.join(t, "hook-errors.log")
            self.assertFalse(os.path.exists(errors) and "Error" in open(errors).read(),
                             open(errors).read() if os.path.exists(errors) else "")


class NoPyYAMLTest(unittest.TestCase):
    """The hook under an interpreter without PyYAML, with a cold index: it records and the guard denies."""

    def test_cold_index_without_pyyaml(self):
        import shutil
        import subprocess
        import tempfile
        py = next((p for p in ("/usr/bin/python3", shutil.which("python3")) if p and subprocess.run(
            [p, "-c", "import yaml"], capture_output=True).returncode != 0), None)
        if not py:
            self.skipTest("no interpreter without PyYAML here")
        of_path = os.path.join(os.path.dirname(__file__), "..", "bin", "of")
        with tempfile.TemporaryDirectory() as t:
            r = subprocess.run([py, of_path, "hook", "Stop"], input='{"session_id":"x","hook_event_name":"Stop","cwd":"/tmp"}',
                               capture_output=True, text=True, timeout=60, env=dict(os.environ, OF_STATE=t))
            self.assertEqual(r.returncode, 0, r.stderr)
            import sqlite3
            self.assertEqual(sqlite3.connect(os.path.join(t, "of.db")).execute("SELECT count(*) FROM event").fetchone()[0], 1)
        with tempfile.TemporaryDirectory() as t:
            r = subprocess.run([py, of_path, "hook", "PreToolUse"],
                               input='{"hook_event_name":"PreToolUse","session_id":"t","tool_name":"Bash","tool_input":{"command":"printenv"},"cwd":"/tmp"}',
                               capture_output=True, text=True, timeout=60, env=dict(os.environ, OF_STATE=t))
            self.assertIn('"permissionDecision": "deny"', r.stdout)


class CommandClassTest(unittest.TestCase):
    def test_runner_monitor_other(self):
        """RFC 0005 rule 2.20 needs runner and monitor commands told apart (of_field-analyst, docs/diagnose.md §4.1)."""
        import importlib
        import of_classify
        # an organization's runner commands come from its machine's classify.json (the published code names none)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "classify.json")
            with open(path, "w") as fh:
                json.dump({"runner_commands": [r"\bcargo\s+rbx\b", "runner-w"]}, fh)
            saved = os.environ.get("OF_CLASSIFY_TERMS")
            os.environ["OF_CLASSIFY_TERMS"] = path
            try:
                importlib.reload(of_classify)
                cases = {"cargo rbx run job.json": "runner", "kubectl exec runner-w6 -- ls": "runner",
                         "backtest --days 3": "runner",
                         "until curl -s x; do sleep 30; done": "monitor", "kubectl logs -f p": "monitor",
                         "gh run watch 1": "monitor", "git status": "other", "tail -n 5 f": "other"}
                for cmd, want in cases.items():
                    self.assertEqual(of_classify.command_class("Bash", cmd), want, cmd)
                self.assertEqual(of_classify.command_class("Edit", "cargo rbx"), "other")
            finally:
                if saved is None:
                    os.environ.pop("OF_CLASSIFY_TERMS", None)
                else:
                    os.environ["OF_CLASSIFY_TERMS"] = saved
                importlib.reload(of_classify)
        self.assertEqual(of_classify.command_class("Bash", "cargo rbx run"), "other")  # without the file: generic only
