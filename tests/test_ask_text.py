"""bin/of_ask_text.py: an ask states no estimate of effort or duration (founder, 2026-11-11), and `of ask` refuses one.

The phrases are the forms found on the founder's open cards that day and in 583 earlier asks, with the names taken
out. Pure logic: no herdr, no event store.
"""
import argparse
import ast
import glob
import importlib.machinery
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_ask_text  # noqa: E402

ESTIMATES = [
    "Шаги (около 5 минут, всё на твоём Mac): 1. Открой настройки",
    "Выгрузить отчёт за октябрь (одна команда, около 2 минут)",
    "После «да» придёт задача примерно на 15 минут: создать бакет",
    "Нужны твоё «да» и ~45 минут твоих рук на хосте",
    "Сначала 1–2 дня подготовки, потом выкатка",
    "Сначала нужно (1–2 дня): скрипт и его проверка",
    "На выкладку после твоего «да» нужно около 15–30 минут.",
    "Ты один раз создаёшь служебный аккаунт: задача минут на пять в обычном терминале",
    "Свой список ты знаешь лучше; черновик займёт у тебя минут 10)",
    "Шаги (~3 мин): 1) Открой настройки",
    "Шаги (минуты две): 1. Открой вкладку",
    "Что делаешь ты, один раз, около 10 минут: 1) Открой профиль",
    "не позже чем через 3 часа придёт вторая задача на минуту: убрать бакет",
    "Это займёт около минуты.",
    "It will take about 30 minutes of your time.",
    "Rotate the key (~2 min)",
    "Проверка правила: войти в SSO (~2 мин)?",  # 2026-11-11: this test text passed and reached the founder
]

FACTS = [
    "Окно начинается через 40 минут; без ответа я его пропущу, и следующее — в четверг.",
    "Декларация подаётся в течение 9 месяцев после конца года.",
    "Нужно уведомить людей не позже 5 дней с момента, когда о ней узнали.",
    "Сессия живёт 2 часа.",
    "Токен действует 12 часов и даёт права твоей роли.",
    "Правило блокировки бакета на 90 дней не даёт удалить свежие копии.",
    "Банк получил новую лицензию 08.10, но за 34 дня так и не подтвердил обновление.",
    "Перезапуск базы, около минуты простоя.",
    "Каждый раз ~7 ч без торговли, пока партнёры не перезагрузят сервер.",
    "Словари с обновлением раз в 5–10 минут.",
    "Помощник теста ждёт до 10 секунд.",
    "Лимит сбросится через ~3 часа.",
    "Версия вышла 4 ноября, неделю назад.",
    "Рынок недели 11-02 завышен.",
    "Для модели ему нужны 60 дней данных.",
    "Это противоречит твоему правилу релизов (15–20 минут чистой работы первого сайта).",
    "Данные есть в бэкапе от 11-09 (14 дней).",
    "За 52 живые минуты 0 отправок.",
    "Каждый месяц совпадает, бюджет $2 600 в месяц на три позиции.",
    "Шаги: 1) Открой настройки. 2) Нажми «Создать».",
    "Он занял 43 ГБ памяти, почти весь своп (~10 секунд).",
    "Запускать ли обновление сегодня в 10:00 UTC?",
]


class EstimatesTest(unittest.TestCase):
    def test_an_estimate_of_work_is_found(self):
        for text in ESTIMATES:
            with self.subTest(text=text):
                self.assertTrue(of_ask_text.estimates(text), text)

    def test_a_fact_with_a_duration_passes(self):
        for text in FACTS:
            with self.subTest(text=text):
                self.assertEqual(of_ask_text.estimates(text), [], text)

    def test_the_refusal_names_the_phrase_and_how_to_rewrite(self):
        why = of_ask_text.check("Войти в SSO", ["да (около 2 минут)", "нет"], "Шаги (~3 мин): 1) Открой терминал")
        self.assertIn("«около 2 минут»", why)
        self.assertIn("«~3 мин»", why)
        self.assertIn("--due", why)
        self.assertNotIn("\n", why)
        self.assertIsNone(of_ask_text.check("Запускать ли обновление в 10:00 UTC?", ["да", "нет"], None))


class DropTest(unittest.TestCase):
    def test_an_estimate_is_cut_and_the_rest_reads(self):
        for text, want in [
                ("Ротировать ключ (~5 мин)?", "Ротировать ключ?"),
                ("Выгрузить отчёт (одна команда, около 2 минут)", "Выгрузить отчёт (одна команда)"),
                ("Шаги (около 5 минут, всё на твоём Mac): 1. Открой", "Шаги (всё на твоём Mac): 1. Открой"),
                ("После «да» придёт задача примерно на 15 минут: создать бакет", "После «да» придёт задача: создать бакет"),
                ("да, сделать сейчас (рекомендую; ~10 мин твоих рук)", "да, сделать сейчас (рекомендую)"),
                ("Запускать ли выкатку сегодня в 10:00 UTC?", "Запускать ли выкатку сегодня в 10:00 UTC?")]:
            with self.subTest(text=text):
                self.assertEqual(of_ask_text.drop(text), want)


class PaneCardTest(unittest.TestCase):
    """A question OF files from an agent's pane is never refused: its estimate is cut from the card."""

    def test_the_card_carries_no_estimate(self):
        import of_events
        import of_judge
        sent = []
        saved = of_events.emit, of_judge.route
        of_events.emit = lambda name, payload, **k: sent.append(payload) or "e1"
        of_judge.route = lambda position: ("founder", None, {})
        try:
            of_judge.file_pane_ask({"position": "k8s_keeper", "text": "…", "session": "S"},
                                   {"question": "Ротировать ключ сегодня (~5 мин твоих рук)?",
                                    "options": ["да (около 2 минут)", "нет"]})
        finally:
            of_events.emit, of_judge.route = saved
        q = sent[0]["questions"][0]
        self.assertEqual(q["question"], "Ротировать ключ сегодня?")
        self.assertEqual(q["options"], ["да", "нет", of_judge.not_a_question()])
        self.assertEqual(sent[0]["estimates_cut"], ["~5 мин", "около 2 минут"])


# the modules that file the founder's cards themselves, with OF's own words
CARD_FILERS = ["of", "of_pulse.py", "of_scratch.py", "of_blocked.py", "of_capacity.py", "of_spawn.py", "of_judge.py",
               "of_listen.py", "of_asks_answer.py"]


class OwnCardsTest(unittest.TestCase):
    def test_no_card_text_of_ofs_own_estimates_time(self):
        """Every string in the modules that file cards, except docstrings and --help texts, holds no estimate."""
        hits = []
        for name in CARD_FILERS:
            path = os.path.join(HERE, "..", "bin", name)
            with open(path) as fh:
                tree = ast.parse(fh.read())
            skip = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.body \
                        and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                    skip.add(id(node.body[0].value))
                if isinstance(node, ast.keyword) and node.arg == "help":
                    skip.update(id(n) for n in ast.walk(node.value))
            for node in ast.walk(tree):
                if id(node) in skip:
                    continue
                if isinstance(node, ast.JoinedStr):
                    s = "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    s = node.value
                else:
                    continue
                hits += [f"{name}:{node.lineno}: {e}" for e in of_ask_text.estimates(s)]
        self.assertEqual(hits, [])


class OfAskRefusesTest(unittest.TestCase):
    def setUp(self):
        loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(HERE, "..", "bin", "of"))
        self.of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli", loader))
        loader.exec_module(self.of)
        self.of._load_quiet = lambda: []
        self.of._me = lambda positions, sender: {"name": "k8s_keeper", "system": "k8s"}
        import of_events
        self.emitted = []
        self._emit = of_events.emit
        of_events.emit = lambda *a, **k: self.emitted.append(a) or "e1"

    def tearDown(self):
        import of_events
        of_events.emit = self._emit

    def args(self, question, options=None, task=None):
        return argparse.Namespace(question=question, options=options, task=task, command=None, tags=None, to=None,
                                  escalates=None, blocking=False, due=None, work=None, sender=None)

    def test_an_ask_with_an_estimate_is_refused_and_nothing_is_recorded(self):
        with self.assertRaises(self.of.OfError) as e:
            self.of.cmd_ask(self.args("Ротировать ключ сегодня?", "да (рекомендую; ~5 мин твоих рук)|нет"))
        self.assertIn("«~5 мин»", str(e.exception))
        with self.assertRaises(self.of.OfError):
            self.of.cmd_ask(self.args("Войти в SSO", task="Шаги (около 2 минут): 1) Открой терминал"))
        self.assertEqual(self.emitted, [])


class OtherChannelsTest(unittest.TestCase):
    """His rule has no exceptions by channel (of_conductor, 2026-11-11): a diff's question and a reopened task's
    note are refused like an ask."""

    def test_diff_propose_and_reopen_note_are_refused(self):
        import json
        import sqlite3
        import subprocess
        import tempfile
        from tests.test_diff import REG
        of = os.path.join(HERE, "..", "bin", "of")
        with tempfile.TemporaryDirectory() as t:
            os.makedirs(os.path.join(t, "org", "registry"))
            os.makedirs(os.path.join(t, "state"))
            with open(os.path.join(t, "org", "registry", "positions.yaml"), "w") as fh:
                fh.write(REG)
            with open(os.path.join(t, "orgs.yaml"), "w") as fh:
                fh.write(f"orgs:\n  - name: ofb\n    path: {t}/org\n    workspace: ofb\n")
            diff_file = os.path.join(t, "sd.yaml")
            with open(diff_file, "w") as fh:
                fh.write("diff:\n  id: sd-2026-11-11-est\n  kind: mandate_rewrite\n  tier: medium\n"
                         "  author: of_a\n  org: ofb\n  hypothesis: h\n  approvals: [owner]\n"
                         "  predicted: [{metric: m, to: 0}]\n  review: {after: 7 days}\n  reversal: back\n"
                         "  changes:\n    registry:\n      - op: set\n        name: of_b\n        fields:\n"
                         "          mandate_exit: {done_when: done, review: 2026-12-01}\n")
            env = dict(os.environ, OF_STATE=os.path.join(t, "state"), OF_ORGS=os.path.join(t, "orgs.yaml"),
                       OF_CONFIG_DIR=t, PATH="/usr/bin:/bin")
            env.pop("CLAUDE_CODE_SESSION_ID", None)
            run = lambda *a: subprocess.run([sys.executable, of, *a], env=env, capture_output=True, text=True, timeout=60)
            r = run("diff", "propose", diff_file, "--from", "of_a", "--ask", "Утвердить новый срок? Это ~2 мин твоих рук.")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("estimates time", r.stderr)
            r = run("ask", "Войти в SSO", "--task", "1) Открой терминал. 2) Войди.", "--from", "of_a")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            c = sqlite3.connect(os.path.join(t, "state", "of.db"))
            rows = c.execute("SELECT event_id, payload FROM event WHERE name = 'of.agent_question'").fetchall()
            self.assertEqual([json.loads(p).get("diff_id") for _, p in rows], [None])  # the diff filed no card
            tid = rows[0][0][-8:]
            r = run("status", "working", "--reopen-task", tid, "--note", "вход не виден, нужно ещё около 5 минут",
                    "--from", "of_a")
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("the note estimates time", r.stderr)
            r = run("status", "working", "--reopen-task", tid, "--note", "вход не виден: sso login пишет expired",
                    "--from", "of_a")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class PeoplesWordsTest(unittest.TestCase):
    """The patterns of what people type that other modules use (bin/of_asks_tui.py, bin/of_asks_chat.py)."""

    def test_patterns(self):
        import of_asks_chat
        self.assertEqual(of_ask_text.fold("Ещё РАЗ"), "еще раз")
        self.assertEqual(of_asks_chat.quoted_options("ок, еще раз завтра", ["нет", "ещё раз завтра"]), [1])
        self.assertTrue(of_ask_text.RECOMMENDED.search("да (рекомендую: окно короткое)"))
        self.assertIn("д", of_ask_text.YES_KEYS)


class JudgeFiledCardTest(unittest.TestCase):
    """of_architect, 2026-11-12: three cards filed from end-of-turn reports whose status line waited on "the card"
    beside the list that named the open card by its id (bin/of_judge.py says_more, _ON_A_CARD)."""

    def test_a_wait_on_the_card_is_that_card(self):
        import of_judge
        report = ("Готово: правила обновлены, тесты проходят.\n\nМой статус: жду твоего ответа на карточку.\n\n"
                  "**Вопросы команды OF к тебе:**\n- запускать ли независимого рецензента `of_review` (`d8eaf596`).")
        self.assertFalse(of_judge.says_more(report, {"d8eaf596"}))
        self.assertFalse(of_judge.says_more(report.replace("жду твоего ответа на карточку",
                                                           "waiting for your answer on the card"), {"d8eaf596"}))
        # a new wait beside the card is still a question
        self.assertTrue(of_judge.says_more(report.replace("жду твоего ответа на карточку",
                                                          "жду твоего ответа, запускать ли сборку"), {"d8eaf596"}))
        self.assertTrue(of_judge.says_more(report.replace("на карточку.", "на карточку: или сначала тесты?"),
                                           {"d8eaf596"}))


if __name__ == "__main__":
    unittest.main()
