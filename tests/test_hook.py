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


class OrgRuleMaskTest(unittest.TestCase):
    """An organization rule's deny names what matched, the command's own words: masked in the reason shown, in the
    recorded mandate_check and in a warning's context, as its subject is, and masked before its length is cut, so a
    secret the cut would break stays masked (of_server, 2026-11-24, from organic-framework PR 475's review; of_review
    on PR 623). Through the guard's real org_rule_hits."""

    TOK = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"

    def run_rule(self, command, mode="deny"):
        import io
        from unittest import mock
        rule = {"id": "g-demo", "mode": mode, "reason": "demo is not for agents.", "subject": "*", "except": [],
                "cli": {"names": ["demo"]}}
        hook = {"tool_name": "Bash", "session_id": "S", "cwd": "/tmp", "tool_input": {"command": command}}
        out, sess = io.StringIO(), {}
        saved = of_hook.OUTPUT
        try:
            with mock.patch.object(of_hook, "_guard_index", return_value={"sessions": {"S": ["acme_ops", "acme"]}}), \
                    mock.patch("of_grants.guard_rules", return_value=([rule], None)), \
                    mock.patch.object(of_hook, "write") as write, mock.patch("sys.stdout", out), \
                    mock.patch.object(of_hook, "save_session"):
                denied = of_hook._org_guard(hook, sess)
            return denied, out.getvalue() + json.dumps(of_hook.OUTPUT or {}), [c[0][1] for c in write.call_args_list]
        finally:
            of_hook.OUTPUT = saved

    def test_a_token_is_masked_where_it_is_shown_and_recorded(self):
        denied, shown, recs = self.run_rule(f"demo login --token {self.TOK}")
        self.assertTrue(denied)
        self.assertIn("Matched: demo login --token", shown)
        self.assertNotIn(self.TOK, shown)
        self.assertTrue(recs and all(self.TOK not in r["reason"] and self.TOK not in r["subject"] for r in recs))

    def test_a_url_password_past_the_cut_stays_masked(self):
        pw = "Zq8" + "secretpass"   # no shape the masker knows but the URL's own
        # the matched phrase is `demo <filler> https://u:<pw>@…`: a cut at 200 falls between the password and its @
        cmd = "demo " + "x" * (200 - len("demo ") - len(" https://u:") - len(pw)) + f" https://u:{pw}@example.com/a"
        denied, shown, recs = self.run_rule(cmd)
        self.assertTrue(denied)
        self.assertIn("https://u:", shown)
        self.assertNotIn(pw[:8], shown)
        self.assertTrue(recs and all(pw[:8] not in r["reason"] for r in recs))

    def test_a_warning_and_a_record_mask_it_too(self):
        _, shown, recs = self.run_rule(f"demo login --token {self.TOK}", mode="warn")
        self.assertIn("a warning now", shown)
        self.assertNotIn(self.TOK, shown)
        self.assertTrue(recs and all(self.TOK not in r["reason"] for r in recs))
        _, shown, recs = self.run_rule(f"demo login --token {self.TOK}", mode="record")
        self.assertNotIn(self.TOK, shown)
        self.assertTrue(recs and all(self.TOK not in r["reason"] for r in recs))


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
        of_hook._PRE_TOOL = False  # one process per hook in use; here a PreToolUse call's flag outlived its test
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
        if name == "UserPromptSubmit" and "transcript_path" not in kw:  # Claude Code always names its transcript
            kw["transcript_path"] = os.path.join(self.tmp.name, "empty.jsonl")
            open(kw["transcript_path"], "a").close()
        of_hook.handle(name, dict(hook_event_name=name, session_id="S1", cwd="/x", **kw))

    def test_only_unanswered_delegated_permissions_have_a_receiver(self):
        from unittest import mock
        index = {"sessions": {"S1": ["worker", "acme"], "S2": ["lead", "acme"]},
                 "ask_targets": {"worker": "lead", "lead": "founder"}}
        with mock.patch.object(of_hook, "_registry_index", return_value=index), \
                mock.patch.object(of_hook, "herdr_state"), mock.patch.object(of_hook, "write") as write:
            for sid, answer, to in (("S1", None, "lead"), ("S2", None, None), ("unknown", None, None),
                                    ("S1", {"behavior": "allow"}, None)):
                with self.subTest(session=sid, answer=answer), \
                        mock.patch.object(of_hook, "_permission_answer", return_value=answer):
                    write.reset_mock()
                    write.return_value = "request"
                    of_hook.handle("PermissionRequest", {"session_id": sid, "tool_name": "Bash",
                                                         "tool_input": {"command": "check token=example-secret"}})
                    kind, payload = write.call_args_list[0].args[:2]
                    self.assertEqual(kind, "permission_request")
                    self.assertEqual(payload["summary"], "check token=[masked:assignment]")
                    if to:
                        self.assertEqual(payload["to"], to)
                    else:
                        self.assertNotIn("to", payload)

    def test_registry_cache_without_ask_targets_is_rebuilt(self):
        import of_router
        orgs = os.environ["OF_ORGS"]
        registry = os.path.join(self.tmp.name, "org", "registry", "positions.yaml")
        with open(orgs, "w") as fh:
            json.dump({"orgs": [{"name": "acme", "path": "org"}]}, fh)
        with open(registry, "w") as fh:
            json.dump({"units": {"ops": {"owner": "lead"}},
                       "delegations": [{"unit": "ops", "to_owner": "lead"}],
                       "positions": [{"name": "worker", "unit": "ops"}, {"name": "lead", "unit": "ops"},
                                     {"name": "other"}]}, fh)
        os.makedirs(of_hook.STATE, exist_ok=True)
        with open(os.path.join(of_hook.STATE, "index.json"), "w") as fh:
            json.dump({"router": of_router.VERSION, "sessions": {}, "paths": [], "systems": [], "memory": {},
                       "card_prefix": {}, "mtimes": {f: os.path.getmtime(f) for f in (orgs, registry)}}, fh)
        self.assertEqual(of_hook._registry_index()["ask_targets"], {"worker": "lead", "lead": "founder", "other": "founder"})

    def test_a_turn_end_starts_the_delivery_only_while_no_listener_keeps_the_views(self):
        """The founder, 2026-11-17 (prompt 01a1eae5-4583, via of_conductor): an answer he gave a busy agent waited for
        the listener's start. With no fresh listener heartbeat, the agent's own turn end starts `of asks deliver`."""
        import subprocess
        import time as _t
        import of_listen
        import of_store
        self.assertEqual(of_hook.LISTENER_STALE_S, of_listen.STALE_S)
        started = []

        class Child:
            def __init__(self, argv, **kw):
                started.append((argv, kw.get("env") or {}))
        saved, saved_env = subprocess.Popen, dict(os.environ)
        subprocess.Popen = Child
        os.environ.update(HERDR_PANE_ID="p1", CLAUDE_CODE_SESSION_ID="S1", OF_NO_DETACH="")  # Popen is the fake
        try:
            self.fire("Stop", last_assistant_message="ok")
            self.assertEqual(started, [])  # no listener on this machine: nothing to stand in for
            conn = of_store.connect()
            conn.executescript(of_listen.DDL)
            conn.execute("INSERT OR REPLACE INTO listener_meta VALUES ('heartbeat', ?)", (str(_t.time()),))
            conn.commit()
            self.fire("Stop", last_assistant_message="ok")
            self.assertEqual(started, [])  # a listener keeps the views: its DeliverProcessor delivers
            conn.execute("INSERT OR REPLACE INTO listener_meta VALUES ('heartbeat', ?)", (str(_t.time() - 600),))
            conn.commit()
            conn.close()
            self.fire("Stop", last_assistant_message="ok")
            self.fire("Stop", last_assistant_message="ok")  # within DELIVER_GAP_S: not twice
            self.assertEqual(len(started), 1)
            argv, env = started[0]
            self.assertEqual(argv[-3:], ["asks", "deliver", "of_a"])
            self.assertEqual((env.get("OF_AUTOMATION"), env.get("SSH_AUTH_SOCK")), ("1", ""))
            self.assertFalse([k for k in env if k.startswith("HERDR_") or k == "CLAUDE_CODE_SESSION_ID"])
        finally:
            subprocess.Popen = saved
            os.environ.clear()
            os.environ.update(saved_env)

    def test_a_watch_on_a_scratchpad_file_gets_a_note(self):
        """2026-11-15, a user organization's structure keeper: macOS's tmp_cleaner deleted a watch's scripts in the
        scratchpad and the watch printed "none new" for about 11 hours."""
        scratch = "/private/tmp/claude-501/-Users-x-repo/S1/scratchpad/watch.sh"
        for tool, ti in (("Monitor", {"command": f"bash {scratch}", "description": "watch"}),
                         ("CronCreate", {"cron": "*/30 * * * *", "prompt": f"run {scratch} and report"}),
                         ("Bash", {"command": f"while true; do bash {scratch}; sleep 60; done", "run_in_background": True})):
            of_hook.OUTPUT = None
            self.fire("PostToolUse", tool_name=tool, tool_input=ti, tool_response={})
            out = (of_hook.OUTPUT or {}).get("hookSpecificOutput") or {}
            self.assertEqual(out.get("hookEventName"), "PostToolUse", tool)
            self.assertIn("names a file under the session's scratchpad (/private/tmp/claude-501/...)",
                          out.get("additionalContext", ""))
            self.assertIn("print FAIL when it cannot run", out["additionalContext"])
        for tool, ti in (("Bash", {"command": f"bash {scratch}"}),                 # not a watch: a one-off command
                         ("Monitor", {"command": "bash ~/repo/scripts/watch.sh"}),  # a persistent path
                         ("Read", {"file_path": scratch})):
            of_hook.OUTPUT = None
            self.fire("PostToolUse", tool_name=tool, tool_input=ti, tool_response={})
            self.assertIsNone(of_hook.OUTPUT, (tool, ti))

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

    def test_a_prompt_without_a_transcript_is_no_persons(self):
        """of_review on organic-framework PR 170: the hook's own entry, fed a prompt from a shell, named no transcript."""
        of_hook.handle("UserPromptSubmit", {"hook_event_name": "UserPromptSubmit", "session_id": "S1", "cwd": "/x",
                                            "prompt": "yes, make p50 the target"})
        p = [e for e in self.events() if e["event.name"] == "of.human_prompt"][-1]["of.payload"]
        self.assertEqual(p["origin"], "unknown")

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
        # the spool has its own lock: a transcript ingest holding the worker's lock never holds it, and a second
        # drainer waits or returns at once (2026-11-17: the founder's prompts waited 93 minutes behind one ingest)
        import fcntl
        with open(os.path.join(of_hook.STATE, "ingest.lock"), "w") as worker:
            fcntl.flock(worker, fcntl.LOCK_EX)
            of_store.spool_append("events.jsonl", of_events.record("pulse", {"n": 0}, position="of_a"))
            self.assertEqual(of_store.drain_spool(conn), 1)
        with open(of_store.spool_path("drain.lock"), "w") as other:
            fcntl.flock(other, fcntl.LOCK_EX)
            of_store.spool_append("events.jsonl", of_events.record("pulse", {"n": -1}, position="of_a"))
            self.assertEqual(of_store.drain_spool(conn), 0)                # another drainer runs: none here
        self.assertEqual(of_store.drain_spool(conn), 1)
        # a budget spent keeps the rest for the next run (the listener's loop; of_review on PR 290)
        for i in range(3):
            of_store.spool_append("events.jsonl", of_events.record("pulse", {"b": i}, position="of_a"))
        self.assertEqual(of_store.drain_spool(conn, budget_s=-1), 0)
        self.assertEqual(of_store.drain_spool(conn), 3)
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
        # an organization whose registry carries no owner's yes to subagents: nothing is said (of_review on PR 241)
        self.assertNotIn("Subagents", ctx)
        import of_context
        sec = "\n".join(of_context.subagents_section(("The owner, 2026-11-16: \"launch subagents\".", ["P1"],
                                                       ["launch subagents"])))
        self.assertIn("## Subagents: your owner asks you to use them\nYour owner's standing yes for the Agent tool "
                      "(your organization's registry, subagents:; P1): The owner, 2026-11-16: \"launch subagents\". "
                      "As typed in their conductor: \"launch subagents\"\n"
                      "Run the independent parts of your work as subagents, in parallel", sec)
        self.assertEqual(of_context.subagents_section(None), [])
        self.assertEqual(outs["S2"], "")                     # not enrolled: no context
        self.assertIn("of.context_injected", [e["event.name"] for e in self.events()])

    def test_a_large_context_fits_the_cap_and_says_what_was_cut(self):
        """Hand-over b460c5ed from of_architect: above 10,000 characters Claude Code saves a hook's output to a file and
        shows a 2 KB preview; 76 of 79 start contexts on 2026-11-16 were over it, and the rules were cut mid-sentence."""
        import io
        import stat
        import of_context
        reg = os.path.join(self.tmp.name, "org", "registry", "positions.yaml")
        os.makedirs(os.path.join(self.tmp.name, "org", "registry", "briefs"), exist_ok=True)
        paras = [f"Paragraph {i}: " + "the work goes on as the owner said, sentence after sentence. " * 12
                 for i in range(40)]
        with open(os.path.join(self.tmp.name, "org", "registry", "briefs", "a.md"), "w") as fh:
            fh.write("# Brief: of_a\n\n" + "\n\n".join(paras))
        rules = "\n".join(f"- Rule {i}: do what your own tools can do, and check it before you say it is done."
                           for i in range(80))
        with open(os.path.join(self.tmp.name, "org", "AGENTS.md"), "w") as fh:
            fh.write("# Org\n## Rules\n" + rules + "\n")
        with open(reg, "w") as fh:
            fh.write("organization: test-org\npositions:\n"
                     "  - {name: of_a, system: of, role: a, summary: 'build it', status: active, enrolled: true,"
                     " mandate: registry/briefs/a.md, holder: {session: S1}}\n"
                     "  - {name: of_b, system: of, role: b, summary: 'review it', status: active, holder: {session: S2}}\n")
        old_in, old_out = sys.stdin, sys.stdout
        sys.stdin, sys.stdout = io.StringIO(json.dumps({"hook_event_name": "SessionStart", "session_id": "S1",
                                                        "source": "startup", "cwd": "/x"})), io.StringIO()
        of_hook.OUTPUT = None
        try:
            of_hook.main(["SessionStart"])
            out = sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = old_in, old_out
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(ctx), of_context.CAP)
        first = ctx.split("\n")[0]
        # the binding rules are cut here too, so the first line says so loudly (of_architect, msg 01a204f7-ef0e)
        self.assertTrue(first.startswith("OF WARNING: your organization's binding rules do not reach this session"))
        self.assertIn("the brief cut to", first)
        path = first.rsplit(": ", 1)[1]
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path) as fh:
            whole = fh.read()
        self.assertIn(paras[-1].strip(), whole)                          # nothing lost: the whole is in the file
        self.assertIn(rules.split("\n")[-1], whole)
        self.assertIn("- at the end of every turn: `of status", ctx)    # identity and mandate are never cut
        for ln in ctx.split("\n")[1:]:                                  # every line kept whole: never mid-sentence
            self.assertTrue(ln in whole.split("\n"), ln[:80])
        ev = [e for e in self.events() if e["event.name"] == "of.context_injected"][-1]["of.payload"]
        self.assertEqual(ev["chars"], len(ctx))
        self.assertEqual(ev["whole"], len(whole))
        self.assertEqual(ev["cut"][0]["section"], "the brief")
        # of_review on PR 254: an unwritable file is never pointed to; the note names the sources instead
        import unittest.mock as um
        real_open = os.open
        def deny(path, *a, **k):   # the file and the temporary file beside it that replaces it (write_new)
            if os.path.join("context", "test-org", "of_a.md") in path:
                raise PermissionError("denied")
            return real_open(path, *a, **k)
        os.unlink(path)
        sys.stdin, sys.stdout = io.StringIO(json.dumps({"hook_event_name": "SessionStart", "session_id": "S1",
                                                        "source": "startup", "cwd": "/x"})), io.StringIO()
        of_hook.OUTPUT = None
        try:
            with um.patch.object(of_hook.os, "open", deny):
                of_hook.main(["SessionStart"])
            out = sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = old_in, old_out
        ctx2 = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(ctx2), of_context.CAP)
        self.assertIn("and the whole could not be written to a file. Read your brief (", ctx2.split("\n")[0])
        self.assertNotIn(path, ctx2)
        # every fit stays within CAP, the final note included (of_review on PR 254: it overshot by a few characters)
        import random
        rnd = random.Random(7)
        for _ in range(300):
            secs = [(f"s{i}", "\n".join("x" * rnd.randint(1, 900) for _ in range(rnd.randint(1, 30))))
                    for i in range(rnd.randint(1, 8))]
            text, _ = of_context.fit("# head", secs, 2000, lambda c: "N " * (10 + 30 * len(c)))
            self.assertLessEqual(len(text), 2000)
        # a small context is whole, with no note
        small = of_context.fit("# head", [("a", "## A\none line")], 9500, lambda c: "NOTE")
        self.assertEqual(small, ("# head\n\n## A\none line", []))

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

    def test_every_deny_says_not_to_go_round_it_and_reaches_a_subagent(self):
        """A user organization's structure keeper, msg 01a1eedd-f437 (2026-11-18): a drafting subagent wrote a file with
        another tool after a deny. A subagent gets none of OF's binding rules, but it gets the deny's reason."""
        import contextlib
        import io
        # a reason with the secret rules' advice, and one that carries its own (G8): one space before the last sentence
        cases = [({}, "cat ~/.ssh/id_ed25519"), ({"agent_id": "a1", "agent_type": "general-purpose"}, "cat ~/.ssh/id_ed25519"),
                 ({}, "python3 -c \"print(open('.env').read())\"")]
        for extra, cmd in cases:
            of_hook.OUTPUT = None
            out = io.StringIO()
            with contextlib.redirect_stdout(out):  # the decision goes to stdout first (OUTPUT is cleared after)
                self.fire("PreToolUse", tool_name="Bash", tool_input={"command": cmd}, **extra)
            why = json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecisionReason"]
            self.assertIn("another way", why, extra)
            self.assertIn("report the command to your structure keeper", why, extra)
            self.assertNotIn("  ", why, extra)  # one space between the reason's sentences

    def test_the_guard_fails_closed_on_an_error_of_its_own(self):
        """2026-11-11: a NameError in _scratch_guard let every call through unguarded; of_secrets-review's W3 review
        (msg 01a1f8e4-d06c): a hook that raises lets Claude Code and Codex run the call, so the guard denies it."""
        import contextlib
        import io
        from unittest import mock
        for target in ("critical_deny", "_scratch_guard"):
            of_hook.OUTPUT = None
            out = io.StringIO()
            with mock.patch.object(of_hook, target, side_effect=RuntimeError("boom")), contextlib.redirect_stdout(out):
                self.fire("PreToolUse", tool_name="Bash", tool_input={"command": "ls"})
            decision = json.loads(out.getvalue())["hookSpecificOutput"]
            self.assertEqual(decision["permissionDecision"], "deny", target)
            self.assertIn("guard failed (RuntimeError)", decision["permissionDecisionReason"], target)
            self.assertNotIn("boom", decision["permissionDecisionReason"], target)   # only the error's kind
        of_hook.OUTPUT = None
        out = io.StringIO()
        with contextlib.redirect_stdout(out):                                         # and a plain call still runs
            self.fire("PreToolUse", tool_name="Bash", tool_input={"command": "ls"})
        self.assertNotIn("permissionDecision", out.getvalue())

    def test_a_guard_that_does_not_decide_in_time_denies(self):
        """of_server, 2026-11-23: a guard that waits past the hook's limit would leave the call to the harness."""
        import contextlib
        import io
        import time as _t
        from unittest import mock
        of_hook.OUTPUT, of_hook.GUARD_DECIDED = None, False
        out = io.StringIO()
        start = _t.monotonic()
        with mock.patch.object(of_hook, "GUARD_STALL_S", 1), \
                mock.patch.object(of_hook, "critical_deny", side_effect=lambda *a, **k: _t.sleep(5)), \
                contextlib.redirect_stdout(out):
            self.fire("PreToolUse", tool_name="Bash", tool_input={"command": "ls"})
        self.assertLess(_t.monotonic() - start, 4)
        decision = json.loads(out.getvalue())["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("guard failed (GuardStalled)", decision["permissionDecisionReason"])
        import signal
        self.assertEqual(signal.alarm(0), 0)                     # disarmed after the decision

    def test_a_stall_a_handler_takes_or_an_unreadable_file_still_denies(self):
        """of_secrets-review, 2026-11-23: a handler in the guard's work may take the stall, and a file that is there
        but not regular was read as empty."""
        import contextlib
        import io
        import time as _t
        from unittest import mock

        def swallowed(*a, **k):
            try:
                _t.sleep(5)
            except Exception:  # noqa: BLE001 - what a reader in the guard does
                pass
            try:
                _t.sleep(5)
            except BaseException:  # noqa: BLE001 - and a broader one
                pass

        def unreadable(*a, **k):
            of_guard.UNREADABLE.append("/x/.git/config")
        for side, kind in ((swallowed, "GuardStalled"), (unreadable, "GuardUnreadable")):
            of_hook.OUTPUT, of_hook.GUARD_DECIDED = None, False
            out = io.StringIO()
            with mock.patch.object(of_hook, "GUARD_STALL_S", 1), \
                    mock.patch.object(of_hook, "critical_deny", side_effect=side), contextlib.redirect_stdout(out):
                self.fire("PreToolUse", tool_name="Bash", tool_input={"command": "ls"})
            decision = json.loads(out.getvalue())["hookSpecificOutput"]
            self.assertEqual(decision["permissionDecision"], "deny", kind)
            self.assertIn(f"guard failed ({kind})", decision["permissionDecisionReason"])
        of_hook.OUTPUT = None
        out = io.StringIO()
        with contextlib.redirect_stdout(out):                   # the next call starts clean
            self.fire("PreToolUse", tool_name="Bash", tool_input={"command": "ls"})
        self.assertNotIn("permissionDecision", out.getvalue())

    def test_a_registry_never_read_whole_denies(self):
        """of_server, 2026-11-23: unknown protections never read as none."""
        import contextlib
        import io
        from unittest import mock
        import of_credentials
        of_hook.OUTPUT, of_hook.GUARD_DECIDED = None, False
        out = io.StringIO()
        with mock.patch.object(of_credentials, "guard_protection", side_effect=of_credentials.Unreadable("acme")), \
                contextlib.redirect_stdout(out):
            self.fire("PreToolUse", tool_name="Bash", tool_input={"command": "ls"})
        decision = json.loads(out.getvalue())["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("guard failed (Unreadable)", decision["permissionDecisionReason"])
        of_hook.OUTPUT = None
        out = io.StringIO()
        with mock.patch.object(of_credentials, "guard_protection", side_effect=OSError("no cache")), \
                contextlib.redirect_stdout(out):                  # any other failure there: as before
            self.fire("PreToolUse", tool_name="Bash", tool_input={"command": "ls"})
        self.assertNotIn("permissionDecision", out.getvalue())

    def test_a_system_exit_or_a_failure_before_the_guard_answers_denies(self):
        """of_secrets-review on organic-framework PR 469: SystemExit escaped except Exception, and an error before the
        guard ran fell into main's catch-all, both letting the call run."""
        import contextlib
        import io
        from unittest import mock
        of_hook.OUTPUT, of_hook.GUARD_DECIDED = None, False
        out = io.StringIO()
        with mock.patch.object(of_hook, "critical_deny", side_effect=SystemExit(0)), contextlib.redirect_stdout(out):
            self.fire("PreToolUse", tool_name="Bash", tool_input={"command": "ls"})
        decision = json.loads(out.getvalue())["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn("guard failed (SystemExit)", decision["permissionDecisionReason"])
        payload = json.dumps({"hook_event_name": "PreToolUse", "session_id": "S1", "cwd": "/x", "tool_name": "Bash",
                              "tool_input": {"command": "ls"}})
        of_hook.OUTPUT, of_hook.GUARD_DECIDED = None, False
        out = io.StringIO()
        with mock.patch.object(of_hook, "_store_raw", side_effect=RuntimeError("early")), \
                mock.patch("sys.stdin", io.StringIO(payload)), contextlib.redirect_stdout(out):
            self.assertEqual(of_hook.main(["PreToolUse"]), 0)          # main itself never fails the agent
        decision = json.loads(out.getvalue())["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")       # but before the guard answered, it denies
        self.assertIn("guard failed (RuntimeError)", decision["permissionDecisionReason"])

    def test_bin_of_denies_a_pre_tool_use_whose_hook_cannot_load(self):
        """of_secrets-review on PR 469: a failed import of of_hook exited 1, and Claude Code and Codex ran the call."""
        import shutil
        import subprocess
        import tempfile
        d = tempfile.mkdtemp(prefix="ofhk-")
        for name in ("of", "of-hook", "of_yaml.py"):   # of-hook is what the hooks run (of_architect on PR 469)
            shutil.copy(os.path.join(os.path.dirname(of_hook.__file__), name), os.path.join(d, name))
        with open(os.path.join(d, "of_hook.py"), "w") as fh:
            fh.write("raise ImportError('a module the hook needs is broken')\n")
        state = os.path.join(d, "state")
        os.makedirs(state)
        env = dict(os.environ, OF_STATE=state)
        log = os.path.join(state, "hook-errors.log")
        for argv in (["of", "hook", "PreToolUse"], ["of-hook", "PreToolUse"]):
            r = subprocess.run([sys.executable, os.path.join(d, argv[0]), *argv[1:]], input="{}",
                               capture_output=True, text=True, timeout=30, env=env)
            self.assertEqual(r.returncode, 2, argv)
            self.assertIn("guard failed (ImportError)", r.stderr, argv)
            with open(log) as fh:   # of_review, 2026-11-24: the failure leaves a record (it left none that day)
                last = fh.read().splitlines()[-1]
            self.assertIn("PreToolUse launcher ImportError: a module the hook needs is broken", last, argv)
        os.unlink(log)
        os.symlink(os.path.join(d, "elsewhere"), log)   # a link in its place: followed nowhere, the deny still stands
        r = subprocess.run([sys.executable, os.path.join(d, "of-hook"), "PreToolUse"], input="{}",
                           capture_output=True, text=True, timeout=30, env=env)
        self.assertEqual(r.returncode, 2)
        self.assertFalse(os.path.exists(os.path.join(d, "elsewhere")))
        for argv in (["of", "hook", "Stop"], ["of-hook", "Stop"]):
            r = subprocess.run([sys.executable, os.path.join(d, argv[0]), *argv[1:]], input="{}",
                               capture_output=True, text=True, timeout=30)
            self.assertNotEqual(r.returncode, 2, argv)   # another event keeps its old way

    def test_a_guard_reason_is_recorded_masked(self):
        """of_review on organic-framework PR 323: the recorded reason was stored as it was, beside a masked subject."""
        token = "ghp_" + "Ab3" * 12
        import contextlib
        import io
        of_hook.OUTPUT = None
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.fire("PreToolUse", tool_name="Bash", tool_input={"command": f"cat notes/{token}.token"})
        decision = json.loads(out.getvalue())["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn(token, decision["permissionDecisionReason"])  # the agent's own words, in its own session
        checks = [e for e in self.events() if e.get("event.name") == "of.mandate_check"]
        self.assertEqual(len(checks), 1)
        self.assertNotIn(token, json.dumps(checks[0]))

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



class RegistryIndexUnwritableTest(unittest.TestCase):
    """of_review on organic-framework PR 482: in Claude Code's Linux sandbox the index is denyWrite (G36), and the
    commands an agent's shell runs that read it must get it built, unwritten, with no temporary file left behind."""

    def test_an_index_that_cannot_be_replaced_is_returned_unwritten(self):
        from unittest import mock
        real = os.replace

        def refuse(src, dst):
            if dst.endswith("index.json"):
                raise PermissionError(1, "Operation not permitted")
            return real(src, dst)
        os.makedirs(of_hook.STATE, exist_ok=True)
        index = os.path.join(of_hook.STATE, "index.json")
        if os.path.exists(index):
            os.unlink(index)                                    # so this call builds it and tries the write
        with mock.patch.object(of_hook.os, "replace", side_effect=refuse) as rep:
            idx = of_hook._registry_index()
        self.assertTrue(any(c.args[1] == index for c in rep.call_args_list))
        self.assertFalse(os.path.exists(index))
        self.assertIsInstance(idx, dict)
        self.assertIn("sessions", idx)
        left = [n for n in os.listdir(of_hook.STATE) if n.startswith("index.json.") and n.endswith(".tmp")]
        self.assertEqual(left, [])


class ModelCwdTest(unittest.TestCase):
    def test_a_model_call_runs_in_a_fresh_folder_outside_the_state_folder(self):
        """of_agent-server, message 01a20511-834d, the same class: a model call OF makes outside the sandbox never
        runs in a folder a session's sandbox can write."""
        saved = of_events._MODEL_CWD
        of_events._MODEL_CWD = None
        try:
            d = of_events.model_cwd()
            self.assertTrue(os.path.isdir(d) and not os.path.islink(d))
            self.assertFalse(os.path.realpath(d).startswith(os.path.realpath(of_events.STATE_DIR)))
            self.assertEqual(os.listdir(d), [])
            self.assertEqual(os.stat(d).st_mode & 0o777, 0o700)
            self.assertEqual(of_events.model_cwd(), d)                   # one per process
        finally:
            of_events._MODEL_CWD = saved


class NoWriteThroughALinkTest(unittest.TestCase):
    """of_server, hand-over 58681935 (docs/guard.md Gaps, "A process outside the sandbox follows a link"): on the agent
    host a sandboxed session may write OF's state folder; the hook and the store, which run outside the sandbox, never
    write through a link put at one of their names."""

    def test_a_planted_link_at_each_name_leaves_its_target_unchanged(self):
        import tempfile
        import of_events
        import of_hook
        import of_store
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            target = os.path.join(d, "target")
            with open(target, "w") as fh:
                fh.write("TARGET\n")
            state = os.path.join(d, "state")
            os.makedirs(os.path.join(state, "spool"))
            saved = (of_hook.STATE, of_events.STATE_DIR)
            of_hook.STATE = of_events.STATE_DIR = state
            names = ("hook-errors.log", "hook-timing.jsonl", "deliver.log", "injections.jsonl", "index.lock",
                     "ingest.lock", "ship.last", os.path.join("spool", "hooks.jsonl"), os.path.join("spool", "drain.lock"))
            try:
                for n in names:
                    os.symlink(target, os.path.join(state, n))
                of_hook._log_error("x", ValueError("boom"))
                with self.assertRaises(OSError):
                    of_events.append_own(os.path.join(state, "hook-timing.jsonl"), "x\n")
                with self.assertRaises(OSError):
                    of_events.open_own(os.path.join(state, "index.lock"), os.O_WRONLY | os.O_CREAT)
                with self.assertRaises(OSError):
                    of_store.spool_append("hooks.jsonl", {"a": 1})
                self.assertFalse(of_hook.ship_detached_if_due(now=10 ** 10, spawn=lambda *a: None))
                for n in ("index.json", os.path.join("sessions", "s1.json")):   # replaced: the link itself, never its target
                    os.makedirs(os.path.dirname(os.path.join(state, n)), exist_ok=True)
                    os.symlink(target, os.path.join(state, n))
                    of_events.write_new(os.path.join(state, n), "{}")
                    self.assertFalse(os.path.islink(os.path.join(state, n)))
                of_hook.save_session("s2", {"x": 1})
                os.symlink(os.path.join(d, "elsewhere"), os.path.join(state, "pending"))   # a folder replaced by a link
                os.makedirs(os.path.join(d, "elsewhere"))
                with self.assertRaises(OSError):
                    of_events.own_dir(os.path.join(state, "pending"))
                db = os.path.join(d, "db", "of.db")
                os.makedirs(os.path.dirname(db))
                os.symlink(target, db)
                with self.assertRaises(Exception):
                    of_store.connect(db)
                with open(target) as fh:
                    self.assertEqual(fh.read(), "TARGET\n")
                # read_own refuses a link (of_server on PR 545)
                for n in ("ask-refs.json", os.path.join("sessions", "s9.json"), "machine_id", "memory-warned.json"):
                    q = os.path.join(state, n)
                    if os.path.lexists(q):
                        os.remove(q)
                    os.symlink(target, q)
                    with self.assertRaises(OSError):
                        of_events.read_own(q)
                self.assertNotEqual(of_events.machine_id(wait_s=0.1), "TARGET")
                with self.assertRaises(OSError):                                # refused: nothing written, no
                    of_events._with_refs(lambda c: c.update(x=1))               # number given (of_review, PR 545)
                self.assertIsNone(of_events.next_ref("acme"))
                of_events.seen_ref("ACME-42")                                   # a server's number: no raise

                given = of_events.card_prefix("acme") + "-42"

                class _Server:
                    def card_ref(self, *a, **k):
                        return given
                self.assertEqual(of_events.card_ref("acme", "e1", client=_Server()), given)  # the server's, kept
                # an id is always given, whatever lies at the repair lock (of_review on PR 545)
                os.remove(os.path.join(state, "machine_id"))
                with open(os.path.join(state, "machine_id"), "w"):
                    pass                                                        # empty: the repair path
                lockp = os.path.join(state, ".machine_id.lock")
                if os.path.lexists(lockp):
                    os.remove(lockp)
                os.symlink(target, lockp)
                self.assertTrue(of_events.machine_id(wait_s=0.1))
                self.assertTrue(of_events.record("note", {"x": 1})["of.event.id"])  # an event still records
                self.assertEqual(of_hook.load_session("s9"), {"_refused": True})
                of_hook.save_session("s9", of_hook.load_session("s9"))           # never saved over
                self.assertTrue(os.path.islink(os.path.join(state, "sessions", "s9.json")))
                with open(target) as fh:
                    self.assertEqual(fh.read(), "TARGET\n")
                # a file unlinked between the open and the fstat (count 0) is still ours (of_review on PR 545, as
                # organic-framework PR 540's test does it): `!= 1` refused it
                from unittest import mock
                race = os.path.join(state, "race.log")
                with open(race, "w") as fh:
                    fh.write("x")
                real_fstat = os.fstat

                def fstat_after_unlink(fd):
                    if os.path.exists(race):
                        os.unlink(race)
                    return real_fstat(fd)
                with mock.patch.object(of_events.os, "fstat", fstat_after_unlink):
                    os.close(of_events.open_own(race))
                # a second name (a hard link) is refused
                fd = os.open(os.path.join(state, "again.log"), os.O_WRONLY | os.O_CREAT, 0o600)
                os.close(fd)
                os.link(os.path.join(state, "again.log"), os.path.join(d, "hard"))   # a second name: refused
                with self.assertRaises(OSError):
                    of_events.open_own(os.path.join(state, "again.log"))
            finally:
                of_hook.STATE, of_events.STATE_DIR = saved


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
            # of_review on PR 208: under the transaction cap each transaction may hold one record, and a fork's
            # file begins with a title line no session shares; the mark still lands, with every shared record
            from unittest import mock
            title = json.dumps({"type": "custom-title", "customTitle": "a side question", "sessionId": "C3"}) + "\n"
            child3 = os.path.join(d, "C3.jsonl")
            with open(child3, "w") as fh:
                fh.write(title + rec("C3", "u1", 1) + rec("C3", "u2", 2) + rec("C3", "c3", 6))
            with mock.patch.object(of_store, "TX_CAP_S", 0), mock.patch.object(of_store, "TX_YIELD_S", 0):
                of_store.ingest_file(conn, child3)
            self.assertEqual(conn.execute("SELECT parent_session_id, shared FROM session_fork WHERE session_id = 'C3'")
                             .fetchone(), ("P", 2))
            own = conn.execute("SELECT record_uuid FROM transcript_own WHERE session_id = 'C3' AND record_uuid "
                               "LIKE '%' AND type IN ('user', 'assistant')").fetchall()
            self.assertEqual(own, [("c3",)])
            # a moved session: the same session's transcript under another account's folder is no new fork, and its
            # ingest never runs the fork check (2026-11-15: 2 to 9 s a transaction on large sessions, the lock held
            # 14 to 26 s)
            moved = os.path.join(d, "other-account", "P.jsonl")
            os.makedirs(os.path.dirname(moved))
            with open(moved, "w") as fh:
                fh.write(rec("P", "u1", 1) + rec("P", "u2", 2) + rec("P", "u3", 3) + rec("P", "u4", 7))
            with mock.patch.object(of_store, "detect_forks", side_effect=AssertionError("fork check")):
                self.assertEqual(of_store.ingest_file(conn, moved), 1)   # its one new record
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

    def test_deciding_hooks_get_30_seconds_and_the_probe_waits_as_long(self):
        """Ask 5230329b, 2026-11-13: at a load average of 50 the guard took over 5 s, the limit OF had set for every
        hook, so Claude Code would kill it and run the tool unchecked."""
        import subprocess
        from unittest import mock
        import of_init
        old = {"hooks": {"PreToolUse": [{"matcher": of_init.GUARD_MATCHER, "hooks": [
            {"type": "command", "command": f"{of_init.OF} hook PreToolUse", "timeout": 5}]}]}}
        s = of_init.with_hooks(old, of_init.OF, guard=True)
        limits = {(e, g.get("matcher")): h["timeout"] for e, gs in s["hooks"].items() for g in gs for h in g["hooks"]}
        self.assertEqual(limits[("PreToolUse", of_init.GUARD_MATCHER)], 30)    # upgraded in place
        self.assertEqual(limits[("PreToolUse", of_init.GUARD_MCP_MATCHER)], 30)
        self.assertEqual(limits[("PreToolUse", "AskUserQuestion")], 30)
        self.assertEqual(limits[("UserPromptSubmit", None)], 30)                # the mention guard decides too
        self.assertEqual(limits[("PermissionRequest", None)], 30)               # G28 answers a dialog (PR 221)
        self.assertEqual(limits[("Stop", None)], 5)                             # a recording hook keeps 5 s
        waited = []
        def run(*a, **kw):
            waited.append(kw["timeout"])
            raise subprocess.TimeoutExpired(a[0], kw["timeout"])
        with mock.patch.object(of_init.subprocess, "run", run):
            self.assertFalse(of_init.guard_works(s)[0])
            del s["hooks"]["PreToolUse"][0]["hooks"][0]["timeout"]
            of_init.guard_works(s)
        self.assertEqual(waited, [30, 60])                                      # the hook's own limit, else 60 s


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
