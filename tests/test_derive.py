"""Typed events derived from a synthetic transcript (gap #0)."""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_derive  # noqa: E402
import of_events  # noqa: E402
import of_hook  # noqa: E402
import of_store  # noqa: E402


def rec(i, typ, **kw):
    r = {"type": typ, "uuid": f"u{i}", "sessionId": "S", "timestamp": f"2026-10-30T10:{i:02d}:00Z", "cwd": "/x"}
    r.update(kw)
    return r


LINES = [
    rec(1, "user", message={"content": "Ты — оператор. Прочитай бриф и начни. " + "x" * 450},
        origin={"kind": "human"}, promptSource="typed", turnOrigin="human"),
    rec(2, "assistant", message={"model": "m", "content": [{"type": "text", "text": "Готово. Коммитить?"}]}),
    rec(3, "system", subtype="turn_duration"),
    rec(4, "user", message={"content": "да"}, origin={"kind": "human"}, promptSource="typed", turnOrigin="human"),
    rec(5, "assistant", message={"content": [{"type": "tool_use", "id": "t1", "name": "SendMessage",
                                              "input": {"to": "of_b [3fa9c1]", "message": "the owner's yes: go"}}]}),
    rec(6, "user", message={"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}),
    rec(7, "user", message={"content": "status from peer, with the owner's yes"}, origin={"kind": "peer", "from": "uds:/x"},
        promptSource="system", turnOrigin="peer", isMeta=True),
    rec(8, "user", message={"content": "<task-notification>done</task-notification>"},
        origin={"kind": "task-notification"}, turnOrigin="task_notification"),
    rec(9, "system", subtype="compact_boundary", compactMetadata={"trigger": "auto"}),
    {"type": "pr-link", "sessionId": "S", "prNumber": 42, "prRepository": "org/repo", "prUrl": "u", "timestamp": "2026-10-30T10:10:00Z"},
    rec(11, "assistant", message={"content": [{"type": "text", "text": "Без твоего «да» не запускаю."}]}),
    rec(12, "system", subtype="turn_duration"),
    rec(13, "user", message={"content": "Лимит пополнен, продолжай"}, origin={"kind": "human"}, turnOrigin="human"),
]


class DeriveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (of_events.STATE_DIR, of_hook.STATE)
        of_events.STATE_DIR = of_hook.STATE = os.path.join(self.tmp.name, "state")
        orgs = os.path.join(self.tmp.name, "orgs.yaml")
        os.makedirs(os.path.join(self.tmp.name, "org", "registry"))
        with open(os.path.join(self.tmp.name, "org", "registry", "positions.yaml"), "w") as fh:
            fh.write("organization: o\npositions:\n  - {name: of_a, system: of, role: a, holder: {session: S}}\n"
                     "  - {name: of_b, system: of, role: b}\n")
        with open(orgs, "w") as fh:
            fh.write("orgs:\n  - {name: o, path: org}\n")
        os.environ["OF_ORGS"] = orgs
        self.path = os.path.join(self.tmp.name, "S.jsonl")
        with open(self.path, "w") as fh:
            fh.write("\n".join(json.dumps(x) for x in LINES) + "\n")

    def tearDown(self):
        of_events.STATE_DIR, of_hook.STATE = self.saved
        os.environ.pop("OF_ORGS", None)
        self.tmp.cleanup()

    def test_derive(self):
        conn = of_store.connect()
        of_store.ingest_file(conn, self.path)
        self.assertEqual(of_derive.derive(conn, log=lambda m: None), of_derive.derive(conn, log=lambda m: None))
        ev = [json.loads(r[0]) for r in conn.execute("SELECT record FROM event ORDER BY ts, rowid")]
        names = [e["event.name"] for e in ev]
        for k in ("of.human_prompt", "of.turn_end", "of.agent_question", "of.agent_message_out", "of.agent_message_in",
                  "of.compaction", "of.work_record"):
            self.assertIn(k, names)
        self.assertTrue(all(e["gen_ai.agent.name"] == "of_a" and e["of.source"] == "import" for e in ev))
        prompts = [e["of.payload"] for e in ev if e["event.name"] == "of.human_prompt"]
        fa = [p["classifications"]["fa-2026-11-05"] for p in prompts]
        self.assertEqual(fa[0]["kind"], "brief")                       # first and long
        self.assertEqual(fa[1]["approval"], "short")                   # «да»
        self.assertTrue(fa[0]["extracted"] and fa[1]["extracted"] and not fa[2]["extracted"])  # notification dropped
        n_before = conn.execute("SELECT count(*) FROM event_fts").fetchone()[0]
        of_derive.derive(conn, log=lambda m: None)                     # re-derive: replaced, not duplicated
        self.assertEqual(conn.execute("SELECT count(*) FROM event_fts").fetchone()[0], n_before)
        q = [e for e in ev if e["event.name"] == "of.agent_question"]
        self.assertEqual(prompts[1]["reply_to"], q[0]["of.event.id"])  # the yes answers the question
        # "Без твоего «да» не запускаю." was an implicit ask for q-v1; q-v2 reads it as a condition on a later yes
        self.assertEqual(q[1]["of.payload"]["classifications"], {"q-v0": False, "q-v1": True, "q-v2": False, "q-v3": False, "q-v4": False,
                                                                "q-v5": False})
        self.assertEqual(prompts[2]["origin"], "scheduled")
        self.assertEqual(prompts[3]["classifications"]["of-v1"]["origin"], "injected")
        out = next(e["of.payload"] for e in ev if e["event.name"] == "of.agent_message_out")
        self.assertEqual((out["to_position"], out["delivery"], out["carries_owner_words"]), ("of_b", "delivered", True))
        msg_in = next(e["of.payload"] for e in ev if e["event.name"] == "of.agent_message_in")
        self.assertTrue(msg_in["classifications"]["fa-2026-11-05"]["relayed"])
        conn.close()


if __name__ == "__main__":
    unittest.main()


class CodexTest(unittest.TestCase):
    def test_codex_rollout(self):
        import compression.zstd as zstd
        with tempfile.TemporaryDirectory() as d:
            saved = (of_events.STATE_DIR, of_hook.STATE)
            of_events.STATE_DIR = of_hook.STATE = os.path.join(d, "state")
            orgs = os.path.join(d, "orgs.yaml")
            with open(orgs, "w") as fh:
                fh.write("orgs: []\n")
            os.environ["OF_ORGS"] = orgs
            try:
                sid = "019dd3ac-47bf-7682-aba2-d629a586bdcd"
                path = os.path.join(d, f"rollout-2026-10-30T10-00-00-{sid}.jsonl.zst")
                lines = [
                    {"timestamp": "2026-10-30T10:00:00Z", "type": "session_meta", "payload": {"id": sid}},
                    {"timestamp": "2026-10-30T10:00:01Z", "type": "turn_context", "payload": {"model": "gpt-5"}},
                    {"timestamp": "2026-10-30T10:00:02Z", "type": "response_item",
                     "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>x"}]}},
                    {"timestamp": "2026-10-30T10:00:05Z", "type": "event_msg", "payload": {"type": "user_message", "message": "почини тест"}},
                    {"timestamp": "2026-10-30T10:00:05Z", "type": "response_item",
                     "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "почини тест"}]}},
                    {"timestamp": "2026-10-30T10:01:00Z", "type": "response_item",
                     "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Готово. Мержить?"}]}},
                    {"timestamp": "2026-10-30T10:02:00Z", "type": "event_msg",
                     "payload": {"type": "user_message", "message": "да", "turn_trigger": "remote_ios"}}]
                with open(path, "wb") as fh:
                    fh.write(zstd.compress(("\n".join(json.dumps(x) for x in lines) + "\n").encode()))
                conn = of_store.connect()
                self.assertEqual(of_store.ingest_file(conn, path, "codex"), 7)
                self.assertEqual(of_store.ingest_file(conn, path, "codex"), 0)          # cursor holds
                of_derive.derive_codex(conn, log=lambda m: None)
                ev = [json.loads(r[0]) for r in conn.execute("SELECT record FROM event ORDER BY ts, rowid")]
                prompts = [e for e in ev if e["event.name"] == "of.human_prompt"]
                self.assertEqual([p["of.payload"]["text"] for p in prompts], ["почини тест", "да"])  # deduped, env skipped
                self.assertEqual(prompts[1]["of.payload"]["channel"], "phone")
                self.assertEqual(prompts[0]["gen_ai.conversation.id"], sid)
                self.assertTrue(all(e["of.harness.name"] == "codex" for e in ev))
                q = [e for e in ev if e["event.name"] == "of.agent_question"]
                self.assertEqual(prompts[1]["of.payload"]["reply_to"], q[0]["of.event.id"])
                conn.close()
            finally:
                of_events.STATE_DIR, of_hook.STATE = saved
                os.environ.pop("OF_ORGS", None)


class BaselineRulesTest(unittest.TestCase):
    def test_private_tmp_projects_and_relayed_body(self):
        import of_classify
        self.assertFalse(of_classify.fa_extracted("ok", project="-private-tmp-claude-501-x"))
        self.assertTrue(of_classify.fa_extracted("ok", project="-Users-alex-organic-framework"))
        pre = "owner's yes was given " * 3
        msg = f"{pre}<cross-session-message from=\"x\">hello there</cross-session-message>"
        self.assertFalse(of_classify.fa_message_in(msg)["relayed"])  # the text before the tag is not the body
        self.assertTrue(of_classify.fa_message_in('<cross-session-message from="x">with the owner\'s yes</cross-session-message>')["relayed"])
        far = '<cross-session-message from="x">' + "x" * 320 + " owner's yes"
        self.assertFalse(of_classify.fa_message_in(far)["relayed"])  # beyond clean(body, 300)
        self.assertEqual(of_classify.origin_from_record({"promptSource": "sdk"}), "system")
        self.assertEqual(of_classify.origin_from_record({"entrypoint": "sdk-cli"}), "system")


class CodexSkipTest(unittest.TestCase):
    def test_injected_codex_turns_are_skipped(self):
        import of_derive
        for t in ("<heartbeat>tick</heartbeat>", "  <codex_internal_context>x", "<external_codex_apps_open_page>p"):
            self.assertTrue(t.lstrip().startswith(of_derive.CODEX_SKIP))
        self.assertFalse("<send_user_message_question_reply>yes".startswith(of_derive.CODEX_SKIP))


class RequestLabelTest(unittest.TestCase):
    def test_rq_v0(self):
        import of_classify
        self.assertTrue(of_classify.rq_v0('<cross-session-message from="x">Можешь переразметить PR?</cross-session-message>'))
        self.assertTrue(of_classify.rq_v0('<cross-session-message from="x">please re-derive</cross-session-message>'))
        self.assertFalse(of_classify.rq_v0('<cross-session-message from="x">done, committed 1edb0e8</cross-session-message>'))
        self.assertFalse(of_classify.rq_v0("can you? " + '<cross-session-message from="x">done</cross-session-message>'))


class FromKindTest(unittest.TestCase):
    def test_from_kind(self):
        import of_classify
        self.assertEqual(of_classify.from_kind("a3b3994c9dab0352b"), "subagent")
        self.assertEqual(of_classify.from_kind("of_architect"), "peer")
        self.assertEqual(of_classify.from_kind(None), "peer")


class QuestionV2Test(unittest.TestCase):
    """q-v2 on today's live texts (2026-11-06): plans, conditions and reminders are not questions."""
    FALSE_POSITIVES = [
        "Dry-run бот перезапустился в 07:26Z.\n\nНа следующем обходе проверю, что follower ready и что `wholesale-no-blocks` погас.",
        "2. Как только выйдет, пришлю план.\n\nТогда пришлю план и буду ждать вашего «да» на каждый хост. 3. Статус в `of status`: «waiting» на Canonical.",
        "С моей стороны всё готово, описания пулов Trident больше не меняются. Жду твоего ответа ему; когда боты переедут, проверю, что Trident торгует.",
        "Применить две недостающие миграции.\n\nУдаление таблиц на каждой базе — необратимый шаг. Перед ним покажу проверенный дамп и репетицию и спрошу твоё «да» отдельно по каждой базе.",
        "Новый образ на кластеры выкатывается только после твоего «да» и по одному кластеру за раз. Так записано в мандате.\n\nWAITING ON: of_capacity (остаток волн 3–6)",
        "Master я влил ещё 11-04.\n\nБез твоего «да» в очередь его не поставлю.",
        "Миграции безопасные.\n\nПо окну 1.36.6 в 10:00Z всё ещё жду твоего ответа в `of asks` (d1925ed2).",
    ]
    TRUE_POSITIVES = [
        "Это несколько минут без ноды, бот dry-run, так что ничего не теряем. Сделать надо до заморозки кластера в 10:00Z. Перезапустить?",
        "Перезапуск Nitro — после того как он догонит голову. Делать?",
        "- Делегировать `acme·platform` агенту-владельцу.\n\nС какого начинаем?",
        "Что делать: оставить новый образ и довести дело до перезаписи 43 пулов, откатить на старый `e7c60b65…` или ждать?",
        "Пишет, что разработка выходит очень дорогой. Спрашивает: «ну дорого же нет?» Это вопрос к тебе.",
    ]

    def test_q_v3_a_report_with_a_declared_tasks_steps_is_no_question(self):
        """of_architect, 2026-11-07: of_capacity's report (ask 740038b5) sat in the founder's queue as a question."""
        import of_classify
        report = ("Принято: четыре аккаунта остаются как есть, в Anthropic не пишем. Решение записано в проект брокера.\n\n"
                  "С вас остаётся задача 7e8d779f, около 3 минут:\n1. В обычной вкладке терминала выполнить команду и "
                  "подтвердить Touch ID.\n2. Написать мне «готово».")
        self.assertTrue(of_classify.q_v2(report))    # the old version opened it
        self.assertFalse(of_classify.q_v3(report))
        for t in ("Подтверди, пожалуйста, окно в 10:00 UTC.", "Запускать сейчас?", "Это вопрос к тебе: какой вариант?",
                  "Задача 7e8d779f ждёт тебя. Можно запускать?"):
            self.assertTrue(of_classify.q_v3(t), t)

    def test_q_v4_a_decision_request_without_a_question_mark(self):
        """The founder, 2026-11-07 ("почему этого вопроса нет?"): acme_throughput-owner's closing question had no "?"."""
        import of_classify
        closing = ("**Вопрос к тебе.** Кто делает «книгу ликвидатора в пакете follower» для Refunds и Aave:\n"
                   "- **(а) наши позиции.** returns_keeper пишет дополнение к RFC, inventory_keeper ведёт пакет;\n"
                   "- **(б) идёт Вове** в его программу #2304, и тогда сроки его.\n\n"
                   "Рекомендую (а): цена каждого рестарта видна уже сейчас. Но решать, трогать ли зону Вовы, тебе.")
        self.assertFalse(of_classify.q_v3(closing))
        self.assertTrue(of_classify.q_v4(closing))
        self.assertEqual([o[:13] for o in of_classify.options_of(closing)], ["наши позиции.", "идёт Вове в е"])
        inline = ("Статус `waiting --on founder`: жду твоего ответа на вопрос выше. Кто делает книгу: (а) наши позиции, "
                  "returns_keeper и inventory_keeper, или (б) Вова в своей программе #2304.")
        self.assertTrue(of_classify.q_v4(inline))
        self.assertEqual(of_classify.options_of(inline), ["наши позиции, returns_keeper и inventory_keeper",
                                                          "Вова в своей программе #2304"])
        for t in ("Готово, тесты прошли. Тебе ничего решать не нужно.",
                  "Статус: жду твоего ответа на 8a553833 (смена ключа QuickNode).",  # a reminder of a declared ask
                  "Он впишет это шагом в вопрос тебе. Вопрос будет один.",            # narrative, not a request
                  "Сделал, коммит 6af5364. Дальше проверю логи."):
            self.assertFalse(of_classify.q_v4(t), t)
        for t in self.TRUE_POSITIVES + ["Жду твоего решения по 1.36.6: обновлять сегодня или завтра в 10:00Z.",
                                        "Decision for you: ship tonight or tomorrow.", "- не надо трогать US;\n- вопрос к тебе: окно FRA."]:
            self.assertTrue(of_classify.q_v4(t), t)
        self.assertEqual(of_classify.options_of("Сделал. Всё."), [])

    def test_q_v5_a_question_before_the_closing_status_line(self):
        """The founder, 2026-11-10 ("Вот был вопрос, а он не попал в панель"): ci_keeper asked two questions in its
        pane, then wrote one more sentence and the `of status` line AGENTS.md asks for; no version fired."""
        import of_classify
        closing = ("Раннеры переключены, CVE закрыт.\n\n"
                   "Кэш registry (draft PR #1063) ждёт двух твоих ответов:\n"
                   "1. Подключать ли Docker Hub с логином? Нужен токен в Secret.\n"
                   "2. Годится ли пул classic как первый canary для кэша?\n\n"
                   "После этого понесу apply platform_owner на «да».\n\n"
                   '`of status working --note "CVE closed 15:36Z: pool rev 3 on gh1 (libbrotli1 fixed), END sent; watching '
                   'first pool jobs; TODO this week: Dockerfile cache-URL; registry cache draft #1063 waits for review"`')
        self.assertFalse(any(v for k, v in of_classify.questions(closing).items() if k != "q-v5"))
        self.assertTrue(of_classify.q_v5(closing))
        heading = ("**Кэш registry (draft PR #1063):**\n- **Кэш:** 5 pull-through registry в ci-cache.\n"
                   "- **Что решить тебе:**\n  - Подключать ли Docker Hub под логином. Тогда нужен токен в Secret.\n"
                   "  - Годится ли пул classic как первый canary.\n\n"
                   "**Раннеры (CVE):** жду, когда platform_owner одобрит diff 4ceb691f.\n\n"
                   '`of status waiting --on platform_owner --note "CVE: fix-3 4ceb691f diff sent"`')
        self.assertFalse(of_classify.q_v4(heading))
        self.assertTrue(of_classify.q_v5(heading))       # the heading counts anywhere in the message
        note = ("Наблюдатель за первыми job'ами пула работает.\n\n"
                '`of status working --note "CVE closed; registry cache #1063 awaiting owner answers"`')
        self.assertTrue(of_classify.q_v5(note))          # the agent says it awaits the owner and filed nothing
        for t in ("Готово, коммит 6af5364.\n\n`of status idle`",
                  'Окно прошло.\n\n`of status waiting --on "external:#3093 CI (owner yes in hand: queue at green)"`',
                  "**Вопросы к тебе:** нет.\n\nДальше проверю логи.",
                  "Эта настройка затрагивает пуши ботов, так что решать тебе, и сейчас я ничего не прошу.",
                  "Тебе ничего решать не нужно.\n\n`of status done`"):
            self.assertFalse(of_classify.q_v5(t), t)
        for t in self.TRUE_POSITIVES + ["Жду твоего решения по 1.36.6: обновлять сегодня или завтра в 10:00Z."]:
            self.assertTrue(of_classify.q_v5(t), t)
        self.assertEqual(of_classify.ASK_VERSION, "q-v5")

    def test_q_v2(self):
        import of_classify
        for t in self.FALSE_POSITIVES:
            self.assertFalse(of_classify.q_v2(t), t)
        for t in self.TRUE_POSITIVES:
            self.assertTrue(of_classify.q_v2(t), t)
        self.assertTrue(of_classify.q_v2("x", ask_tool=True))
