"""of-asks at work as a Bench package: Bench's first slice, parts 4 and 5 (organic-framework-build
reports/framework/metamethod/11-first-slice.md). Part 4: the setting escalate_after changes when an unanswered routed
card rises. Part 5: a hook before the module "file" keeps a card unfiled, and the record names it. The package itself
is the repository organic-framework/of-asks and Bench is organic-framework/bench (of_bench); the few package files the
tests need are written into a temporary folder, and Bench is imported from BENCH_HOME (default ~/.local/share/bench);
without Bench they are skipped."""
import argparse
import importlib.machinery
import importlib.util
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "bin"))
BENCH_HOME = os.path.expanduser(os.environ.get("BENCH_HOME") or "~/.local/share/bench")
for _d in (BENCH_HOME, os.path.join(BENCH_HOME, "bin")):
    if os.path.isdir(_d) and _d not in sys.path:
        sys.path.append(_d)

import of_asks_package  # noqa: E402
import of_pulse  # noqa: E402

try:
    import bench  # noqa: E402
    import of_method  # noqa: E402
except ImportError:
    bench = of_method = None

PKG_FILES = {
    "Benchfile.spec": 'package { name = "of-asks", version = "1.0.0", main = "main.lua" }\n',
    "main.lua": ('setting "escalate_after" { type = "duration", default = "2h" }\n'
                 'module "file" { on = command "ask" }\n'
                 'extendable { "file" }\n'),
}


def have_bench():
    if bench is None:   # skipped, in CI too, until OF finds Bench there (of_platform's to settle; of_release)
        return False
    found = any(os.path.isfile(os.path.join(d, n)) for n in ("lua5.4", "luajit", "lua5.1")
                for d in bench.RUNTIME_DIRS)
    if not found and os.environ.get("CI"):
        raise AssertionError("no Lua runtime in CI: the unit job installs lua5.4 and luajit")
    return found


def write(root, files):
    for rel, text in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(text)


def project(files):
    """An organization's folder with the package of-asks (its files written beside it) in its Benchfile and FILES
    ({relative path: text}), installed."""
    d = tempfile.mkdtemp()
    write(os.path.join(d, "methods", "of-asks"), PKG_FILES)
    write(d, dict(files, Benchfile='package("of-asks", { path = "methods/of-asks" })\n'))
    bench.install(d, log=lambda *a: None)
    return d


HOOK = """before "file" {
  written_for = "of-asks 1",
  run = function(card, ctx)
    for _, t in ipairs(card.tags or {}) do
      if t == "money" then return { decision = "stop", reason = "money cards go to finance first" } end
    end
    return "pass"
  end,
}
"""


class EscalateAfterTest(unittest.TestCase):
    """Part 4: a value in config/settings/of-asks.lua changes when an unanswered routed card rises; a value of the
    wrong type is refused at install."""

    def setUp(self):
        if not have_bench():
            self.skipTest("no Lua runtime on this machine")
        self.dirs = []

    def tearDown(self):
        for d in self.dirs:
            shutil.rmtree(d, ignore_errors=True)

    def rises(self, root, hours, rule="ask_unanswered"):
        now = 1_800_000_000
        ts = of_pulse.time.strftime("%Y-%m-%dT%H:%M:%SZ", of_pulse.time.gmtime(now - hours * 3600))
        positions = [{"name": "keeper", "_root": root,
                      "_delegation": {"id": "d1", "reporting": {"escalate_when": [rule]}}}]
        ask = {"event_id": "e1", "state": "open", "to": "unit_owner", "position": "keeper", "ts": ts, "kind": "question"}
        return bool(of_pulse.escalations(positions, [ask], now))

    def test_the_setting_moves_the_time_a_card_rises(self):
        four = project({"config/settings/of-asks.lua": 'return { escalate_after = "4h" }\n'})
        self.dirs.append(four)
        self.assertEqual(of_method.settings(four, "of-asks")["escalate_after"], 4 * 3600)
        self.assertFalse(self.rises(four, 3))
        self.assertTrue(self.rises(four, 5))
        plain = project({})                       # installed, nothing set: the package's default, 2h
        self.dirs.append(plain)
        self.assertTrue(self.rises(plain, 3))
        self.assertFalse(self.rises(plain, 1))
        bare = tempfile.mkdtemp()                 # not installed: the same default, no Lua read
        self.dirs.append(bare)
        self.assertEqual(of_asks_package.escalate_after(bare), 2 * 3600)
        self.assertTrue(self.rises(bare, 3))
        self.assertTrue(self.rises(four, 3, rule="ask_unanswered > 2h"))   # a rule with its own time keeps it
        import of_classify
        self.assertTrue(of_classify.ESCALATE.match("ask_unanswered"))

    def test_a_changed_settings_file_is_not_read_until_it_is_installed(self):
        d = project({"config/settings/of-asks.lua": 'return { escalate_after = "4h" }\n'})
        self.dirs.append(d)
        with open(os.path.join(d, "config", "settings", "of-asks.lua"), "w") as f:
            f.write('return { escalate_after = "30m" }\n')
        self.assertEqual(of_asks_package.escalate_after(d), 2 * 3600)   # refused: the default acts

    def test_a_value_of_the_wrong_type_is_refused_at_install(self):
        with self.assertRaisesRegex(bench.BenchError, "escalate_after"):
            d = project({"config/settings/of-asks.lua": 'return { escalate_after = "four hours" }\n'})
            self.dirs.append(d)


class BeforeFileTest(unittest.TestCase):
    """Part 5: in a test organization a hook returns "stop" for a card with a given tag; the card is not filed, its
    record names the hook, and filing runs on the module as extended for every other card."""

    def setUp(self):
        if not have_bench():
            self.skipTest("no Lua runtime on this machine")
        loader = importlib.machinery.SourceFileLoader("of_cli_bench", os.path.join(ROOT, "bin", "of"))
        self.of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli_bench", loader))
        loader.exec_module(self.of)
        self.org = project({"extensions/of-asks/finance-review.lua": HOOK})
        self.of._load_quiet = lambda: []
        self.of._me = lambda positions, sender: {"name": "k8s_keeper", "system": "k8s", "_root": self.org}
        self.of._asker_session = lambda me, sender: None
        import of_events
        self.emitted = []
        self._saved = of_events.emit, of_events.card_ref
        of_events.emit = lambda kind, payload, **k: self.emitted.append((kind, payload)) or "e1"
        of_events.card_ref = lambda *a, **k: None

    def tearDown(self):
        import of_events
        of_events.emit, of_events.card_ref = self._saved
        shutil.rmtree(getattr(self, "org", ""), ignore_errors=True)

    def ask(self, question, tags=None):
        return self.of.cmd_ask(argparse.Namespace(
            question=question, options="yes|no", task=None, command=None, tags=tags, to=None, escalates=None,
            blocking=False, due=None, work=None, sender=None, where=None))

    def test_a_money_card_is_stopped_and_its_record_names_the_hook(self):
        self.assertEqual(self.ask("Pay the hosting invoice today?", tags="money"), 1)
        self.assertEqual([k for k, _ in self.emitted], ["ask_stopped"])
        p = self.emitted[0][1]
        self.assertEqual(p["decision"], "stop")
        self.assertEqual(p["by"], 'extensions/of-asks/finance-review.lua: before "file"')
        self.assertEqual(p["reason"], "money cards go to finance first")
        self.assertEqual((p["package"], p["module"], p["tags"]), ("of-asks", "file", ["money"]))

    def test_other_cards_are_filed_as_before(self):
        self.assertEqual(self.ask("Restart the relay today?", tags="none"), 0)
        self.assertEqual([k for k, _ in self.emitted], ["agent_question"])
        # a money tag OF only guessed from the words is shown to the hook apart, and never stops a card by itself
        self.emitted.clear()
        self.assertEqual(self.ask("Pay the hosting invoice of $40 today?"), 0)
        self.assertEqual([k for k, _ in self.emitted], ["agent_question"])

    def test_without_the_package_nothing_is_read(self):
        bare = tempfile.mkdtemp()
        try:
            self.assertEqual(of_asks_package.before_file(bare, {"tags": ["money"]}), {"decision": "pass", "by": None})
            self.assertEqual(of_asks_package.before_file(None, {"tags": ["money"]}), {"decision": "pass", "by": None})
        finally:
            shutil.rmtree(bare, ignore_errors=True)

    def test_a_lock_that_names_it_but_cannot_be_read_stops_the_card(self):
        with open(os.path.join(self.org, bench.LOCK), "a") as f:
            f.write("\nthis is not lua (\n")
        g = of_asks_package.before_file(self.org, {"tags": []})
        self.assertEqual(g["decision"], "stop")
        self.assertIn("Benchfile.lock cannot be read", g["reason"])


if __name__ == "__main__":
    unittest.main()
