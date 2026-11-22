"""OF's reading of a Bench package (bin/of_method.py; docs/bench.md, "OF's part"): main.lua's words, an
organization's settings type-checked at install, and a hook called before a module. Bench is organic-framework/bench,
found where OF's configuration names it (bin/of_benchpath.py). These tests take its checkout from OF_TEST_BENCH, a
variable only the tests read, and name it in the test home's owner.yaml as the owner would; without it they skip.
Moved from Bench's own tests by of_bench (organic-framework/bench), whose tests keep the reader, the lock and the fork."""

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))

BENCH = os.environ.get("OF_TEST_BENCH")
bench = of_method = None


def setUpModule():
    """Name the checkout in the test home's owner.yaml (tests/isolate.py) as the owner would, for this module only:
    other modules' tests see no Bench (of_checkouts takes it)."""
    global bench, of_method
    if not (BENCH and os.path.isfile(os.path.join(BENCH, "bin", "bench.py"))):
        raise unittest.SkipTest("no Bench checkout (set OF_TEST_BENCH to one)")
    import importlib
    import of_init
    of_init.write_owner_setting("bench", os.path.realpath(BENCH))
    import of_method as m
    of_method = importlib.reload(m)
    import bench as b
    bench = b
    bench.FRAMEWORK = of_method.on_install


def tearDownModule():
    import of_init
    of_init.write_owner_setting("bench", None)


FAST = {"instructions": 2_000_000}


def runtimes():
    found = []
    for name in ("lua5.4", "luajit", "lua5.1"):
        for d in bench.RUNTIME_DIRS:
            p = os.path.join(d, name)
            if os.path.isfile(p) and p not in found:
                found.append(p)
                break
    if not found and os.environ.get("CI"):
        raise AssertionError("no Lua runtime in CI: the unit job installs lua5.4 and luajit")
    return found



SPEC_10 = '''
package {
  name    = "of-asks",
  version = "1.0.0",
  summary = "How an agent asks the owner a question, and how the owner sees and answers it",
  license = "Apache-2.0",
  source  = "https://github.com/organic-framework/of-asks",
  main    = "main.lua",
}

depends { "organic-framework ~> 1" }      -- as a gem that depends on Rails

needs {                                   -- what the package's code will need; shown to whoever installs it
  store = { records = { read = "question.*", write = "question.*" } },
  model = { "small" },
}
'''

MAIN_10 = '''
local card    = require "lib.card"
local routing = require "lib.routing"
local answer  = require "lib.answer"

roles { "asker", "decider" }

subject "question" {
  states = { "needed", "written", "filed", "delivered", "answered", "applied" },
  exits  = { "decided_by_asker", "withdrawn" },
}

setting "language"       { type = "language", default = "en" }
setting "escalate_after" { type = "duration", default = "2h" }

module "decide"     { by = skill "decide-to-ask" }
module "write-card" { by = skill "write-card", output = card.shape, must = card.checks }

module "file" {
  on  = command "ask",
  run = function(q, ctx)
    q.to = routing.decider(q, ctx.organization)
    return ctx.tool("file", q)
  end,
}

module "amend"   { on = command "ask --amend", by = tool "amend" }

module "deliver" {
  on  = record "question.filed",
  run = function(q, ctx) return ctx.show(q, ctx.interfaces_for(q.to)) end,
}

module "remind" {
  on  = schedule(function(s) return "every " .. s.escalate_after end),
  run = function(ctx)
    for _, q in ipairs(ctx.questions { state = "delivered", older_than = ctx.settings.escalate_after }) do
      ctx.escalate(q)
    end
  end,
}

module "answer"        { on = act "answer", by = role "decider", must = answer.checks }
module "take-answer"   { on = record "question.answered", by = skill "take-answer" }
module "spot-question" { on = turn_end(), by = classifier "question" }
module "read-card"     { on = command "ask", by = classifier "card-reader" }

extendable { "write-card", "file", "deliver", "answer" }
'''

LIB_10 = {
    "lib/routing.lua": '''
local M = {}

function M.decider(q, org)
  local unit = org.unit_of(q.from)
  if unit and unit.delegated and not q.escalated then return unit.head end
  return org.owner
end

return M
''',
    "lib/card.lua": 'return { shape = { "decision", "why_now", "context", "options" }, checks = { "no_time_estimates" } }',
    "lib/answer.lua": 'return { checks = { "from_a_person", "names_the_version" } }',
}

BENCHFILE_10 = '''
source "https://bench.useorganic.ai"

package "organic-framework"                     -- brings of-asks and the rest
package "alerts"
package "cost"                                  -- optional: counts what each package spends
package("onboarding",   { path = "forks/onboarding" })       -- its fork
package("cafe-support", { path = "methods/cafe-support" })   -- its own method
'''

OF_SPEC_10 = '''
package {
  name    = "organic-framework",
  version = "1.0.0",
  summary = "A method of organizing agents and people",
}

includes {
  "records      ~> 1",
  "structure    ~> 1",
  "of-asks    ~> 1",
  "claude-code  ~> 1",
}
'''


class _EachRuntime(unittest.TestCase):
    def each(self):
        rts = runtimes()
        if not rts:
            self.skipTest("no Lua runtime on this machine")
        saved = bench.RUNTIMES, bench.RUNTIME_DIRS
        try:
            for rt in rts:
                bench.RUNTIMES, bench.RUNTIME_DIRS = (os.path.basename(rt),), (os.path.dirname(rt),)
                with self.subTest(runtime=os.path.basename(rt)):
                    yield rt
        finally:
            bench.RUNTIMES, bench.RUNTIME_DIRS = saved

    def read(self, src, words=None, files=None, **kw):
        words = bench.NO_WORDS if words is None else words
        files = dict(files or {})
        files["x.lua"] = src
        kw["limits"] = dict(FAST, **kw.get("limits", {}))
        return bench.evaluate(files, "x.lua", words, **kw)


class ReaderExamplesTest(_EachRuntime):
    """main.lua as OF reads it: the example of 10-metamethod-v2.md section 3 with its lib files, and an unknown word."""

    def test_the_method_with_its_lib_files(self):
        for _ in self.each():
            r = self.read(MAIN_10, of_method.METHOD_WORDS, files=LIB_10)
            mods = {d["name"]: d["body"] for d in r["decls"] if d["word"] == "module"}
            self.assertEqual(len(mods), 10)                                   # ten modules
            self.assertEqual(mods["file"]["on"], {"@": "command", "args": ["ask"]})
            self.assertEqual(mods["file"]["run"]["@"], "function")             # kept, not called
            self.assertEqual(mods["spot-question"]["on"], {"@": "turn_end", "args": {}})
            self.assertEqual(mods["remind"]["on"]["args"][0]["@"], "function")
            self.assertEqual(mods["write-card"]["output"], ["decision", "why_now", "context", "options"])
            sets = [d for d in r["decls"] if d["word"] == "setting"]
            self.assertEqual([(d["name"], d["body"]["type"]) for d in sets],
                             [("language", "language"), ("escalate_after", "duration")])

    def test_an_unknown_word_is_refused(self):
        for _ in self.each():
            with self.assertRaisesRegex(bench.BenchError, 'unknown name "modul"'):
                self.read('modul "x" {}', of_method.METHOD_WORDS)


# ---- a package and a project on disk ----

def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd, "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false",
                           "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def write(root, files):
    for rel, text in files.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(textwrap.dedent(text).lstrip())


PKG = {
    "Benchfile.spec": '''
        package { name = "demo-asks", version = "1.0.0", main = "main.lua",
                  tests = { "sh", "tests/run.sh" } }
        depends { "organic-framework ~> 1" }
        ''',
    "main.lua": '''
        local routing = require "lib.routing"
        setting "language"       { type = "language", default = "en" }
        setting "escalate_after" { type = "duration", default = "2h" }
        module "write-card" { by = skill "write-card" }
        module "file" { on = command "ask", run = function(q, ctx) return routing.to(q) end }
        module "remind" { on = schedule(function(s) return "every " .. s.escalate_after end) }
        extendable { "write-card", "file" }
        ''',
    "lib/routing.lua": 'return { to = function(q) return q.to or "owner" end }\n',
    "tests/run.sh": '''
        echo "ok 1 - a card has a decision"
        if grep -q "local routing" main.lua; then echo "ok 2 - routing is required"; else echo "not ok 2 - routing is required"; exit 1; fi
        ''',
}


class _Project(unittest.TestCase):
    def setUp(self):
        if not runtimes():
            self.skipTest("no Lua runtime on this machine")
        self.tmp = tempfile.mkdtemp()
        self.state = os.path.join(self.tmp, "state")
        self._saved = bench.STATE_DIR
        bench.STATE_DIR = self.state
        self.upstream = os.path.join(self.tmp, "demo-asks")
        os.makedirs(self.upstream)
        write(self.upstream, PKG)
        git(self.upstream, "init", "-q", "-b", "main")
        git(self.upstream, "add", "-A")
        git(self.upstream, "commit", "-qm", "1.0.0")
        git(self.upstream, "tag", "v1.0.0")
        self.org = os.path.join(self.tmp, "linden")
        os.makedirs(self.org)
        write(self.org, {"Benchfile": f'package("demo-asks", {{ git = "{self.upstream}", ref = "main" }})\n'})
        self.logs = []

    def tearDown(self):
        bench.STATE_DIR = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def install(self, **kw):
        return bench.install(self.org, log=self.logs.append, **kw)

class SettingsTest(_Project):
    """The mechanism of part 4: the install reads config/settings/<package>.lua against the method's types."""

    def test_a_value_changes_what_the_package_reads(self):
        self.install()
        self.assertEqual(of_method.settings(self.org, "demo-asks"), {"language": "en", "escalate_after": 7200})
        write(self.org, {"config/settings/demo-asks.lua": 'return { language = "ru", escalate_after = "4h" }\n'})
        with self.assertRaisesRegex(bench.BenchError, "changed since the install"):
            of_method.settings(self.org, "demo-asks")               # a value nobody checked never acts
        self.install()
        self.assertEqual(of_method.settings(self.org, "demo-asks"), {"language": "ru", "escalate_after": 14400})
        self.assertEqual(of_method.settings(self.org, "demo-asks", parsed=False)["escalate_after"], "4h")

    def test_a_value_of_the_wrong_type_is_refused_at_install(self):
        for text, said in (('return { escalate_after = 4 }', 'escalate_after is 4, not a duration'),
                           ('return { escalate_after = "soon" }', 'escalate_after is "soon", not a duration'),
                           ('return { language = true }', 'language is true, not a language code'),
                           ('return { colour = "red" }', '"colour" is no setting of demo-asks')):
            write(self.org, {"config/settings/demo-asks.lua": text + "\n"})
            with self.assertRaisesRegex(bench.BenchError, said):
                self.install()
            self.assertFalse(os.path.exists(os.path.join(self.org, bench.LOCK)))   # nothing locked

class SettingsTypeTest(_Project):
    def test_only_a_duration_setting_reads_as_seconds(self):
        """of_bench on PR 575: a text setting whose value looks like a duration stays a text."""
        spec = PKG["main.lua"].replace('setting "language"       { type = "language", default = "en" }',
                                       'setting "language"       { type = "language", default = "en" }\n'
                                       '        setting "label" { type = "string", default = "5m" }')
        write(self.upstream, {"main.lua": spec})
        git(self.upstream, "commit", "-qam", "a text setting")
        self.install()
        got = of_method.settings(self.org, "demo-asks")
        self.assertEqual((got["label"], got["escalate_after"]), ("5m", 7200))


class HookTest(_Project):
    """The mechanism of part 5: a hook before a module returns a decision, and the record can name the hook."""

    HOOK = '''
        before "file" {
          written_for = "demo-asks 1",
          run = function(card, ctx)
            for _, t in ipairs(card.tags or {}) do
              if t == "money" then return { decision = "stop", reason = "money cards go to finance first" } end
            end
            return "pass"
          end,
        }
        '''

    def test_a_hook_stops_a_card_with_a_tag_and_passes_the_rest(self):
        write(self.org, {"extensions/demo-asks/finance-review.lua": self.HOOK})
        self.install()
        d = of_method.before(self.org, "demo-asks", "file", {"title": "pay", "tags": ["money"]})
        self.assertEqual(d["decision"], "stop")
        self.assertEqual(d["reason"], "money cards go to finance first")
        self.assertEqual(d["by"], 'extensions/demo-asks/finance-review.lua: before "file"')
        self.assertEqual(of_method.before(self.org, "demo-asks", "file", {"tags": ["infra"]}),
                         {"decision": "pass", "by": None})
        self.assertEqual(of_method.before(self.org, "demo-asks", "write-card", {"tags": ["money"]})["decision"],
                         "pass")

    def test_no_hook_means_no_reader_at_work(self):
        self.install()
        saved, entries = bench.evaluate, []

        def counted(files, entry, *a, **k):
            entries.append(entry)
            return saved(files, entry, *a, **k)
        bench.evaluate = counted
        try:
            self.assertEqual(of_method.before(self.org, "demo-asks", "file", {}), {"decision": "pass", "by": None})
        finally:
            bench.evaluate = saved
        self.assertEqual(entries, ["file.lua"])                    # the lock alone: no hook file evaluated

    def test_a_changed_or_failing_hook_stops_the_module(self):
        p = os.path.join(self.org, "extensions", "demo-asks", "finance-review.lua")
        write(self.org, {"extensions/demo-asks/finance-review.lua": self.HOOK})
        self.install()
        with open(p, "a") as f:
            f.write("-- edited after the install\n")
        d = of_method.before(self.org, "demo-asks", "file", {"tags": []})
        self.assertEqual((d["decision"], "changed since the install" in d["reason"]), ("stop", True))
        self.assertIn(("demo-asks", "extensions/demo-asks/finance-review.lua", "changed since the install"),
                      of_method.check(self.org))
        write(self.org, {"extensions/demo-asks/finance-review.lua":
                         'before "file" { written_for = "demo-asks 1", run = function() while true do end end }\n'})
        self.install()
        d = of_method.before(self.org, "demo-asks", "file", {})
        self.assertEqual(d["decision"], "stop")
        self.assertIn("stopped by the reader", d["reason"])
        write(self.org, {"extensions/demo-asks/finance-review.lua":
                         'before "file" { written_for = "demo-asks 1", run = function() return "maybe" end }\n'})
        self.install()
        self.assertIn("no decision", of_method.before(self.org, "demo-asks", "file", {})["reason"])

    def test_a_hook_is_refused_at_install_where_it_cannot_stand(self):
        for text, said in (('before "remind" { written_for = "demo-asks 1", run = function() end }',
                            'module "remind" may not be extended'),
                           ('before "nothing" { written_for = "demo-asks 1", run = function() end }',
                            'no module "nothing"'),
                           ('before "file" { written_for = "demo-asks 2", run = function() end }',
                            'written_for = "demo-asks 1"'),
                           ('before "file" { written_for = "demo-asks 1" }', "needs run = function")):
            write(self.org, {"extensions/demo-asks/h.lua": text + "\n"})
            with self.assertRaisesRegex(bench.BenchError, said):
                self.install()




class HookContainmentTest(_Project):
    """of_secrets-review on PR 575: a hook file from the lock stays under extensions/<package>/, is read with no link
    and no wait, and its digest is the checked text's."""

    HOOK = 'before "file" { written_for = "demo-asks 1", run = function(card) return "pass" end }\n'

    def test_a_lock_path_outside_the_folder_stops(self):
        write(self.org, {"extensions/demo-asks/h.lua": self.HOOK})
        self.install()
        lock = bench.read_lock(self.org)
        for bad in ("../outside.lua", "/etc/hosts", "extensions/other/h.lua", "extensions/demo-asks/../h.lua"):
            with self.subTest(bad=bad):
                lock["demo-asks"]["hooks"][0]["file"] = bad
                bench.write_lock(self.org, lock)
                d = of_method.before(self.org, "demo-asks", "file", {"tags": []})
                self.assertEqual(d["decision"], "stop")

    def test_a_pipe_at_the_hook_never_waits(self):
        import time
        write(self.org, {"extensions/demo-asks/h.lua": self.HOOK})
        self.install()
        p = os.path.join(self.org, "extensions", "demo-asks", "h.lua")
        os.remove(p)
        os.mkfifo(p)
        t0 = time.monotonic()
        d = of_method.before(self.org, "demo-asks", "file", {"tags": []})
        self.assertEqual(d["decision"], "stop")
        self.assertLess(time.monotonic() - t0, 5)

    def test_a_link_at_the_extensions_folder_is_refused_at_install(self):
        outside = os.path.join(self.tmp, "outside")
        write(outside, {"h.lua": self.HOOK})
        os.makedirs(os.path.join(self.org, "extensions"))
        os.symlink(outside, os.path.join(self.org, "extensions", "demo-asks"))
        with self.assertRaisesRegex(bench.BenchError, "not a link"):
            self.install()

    def test_bench_is_the_checked_file(self):
        self.assertEqual(os.path.realpath(sys.modules["bench"].__file__),
                         os.path.realpath(os.path.join(BENCH, "bin", "bench.py")))


class OfBenchCommandTest(_Project):
    """`of bench` runs Bench's commands with OF as the framework, and --org takes the organization's folder from
    orgs.yaml (bin/of cmd_bench)."""

    def test_install_through_of_checks_the_settings(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader("of_cli_bench", os.path.join(HERE, "..", "bin", "of"))
        of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli_bench", loader))
        loader.exec_module(of)
        orgs = os.path.join(self.tmp, "orgs.yaml")
        with open(orgs, "w") as fh:
            fh.write(f"orgs:\n  - name: linden\n    path: {self.org}\n")
        of.ORGS_FILE = orgs
        bench.FRAMEWORK = None
        write(self.org, {"config/settings/demo-asks.lua": 'return { escalate_after = 4 }\n'})
        self.assertEqual(of.main(["bench", "install", "--org", "linden"]), 1)          # the wrong type, refused
        self.assertFalse(os.path.exists(os.path.join(self.org, bench.LOCK)))
        write(self.org, {"config/settings/demo-asks.lua": 'return { escalate_after = "4h" }\n'})
        self.assertEqual(of.main(["bench", "install", "--org", "linden"]), 0)
        self.assertEqual(of_method.settings(self.org, "demo-asks")["escalate_after"], 14400)
        self.assertEqual(bench.FRAMEWORK, of_method.on_install)


if __name__ == "__main__":
    unittest.main()
