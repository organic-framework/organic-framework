"""One YAML parser for every process OF runs on a machine (bin/of_yaml.py; of_review on organic-framework PR 572):
the pure parser and libyaml's, on the same texts, and `of registry check`'s report of a text they read differently."""

import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))

import yaml  # noqa: E402
import of_yaml  # noqa: E402

ALIKE = ['a: 1\n', 'a: "\\U0001F600"\n', 'a: !!binary aGVsbG8=\n', 'a: 1\na: 2\n', 'a: [1, 2\n', 'a: 0o17\n',
         "a: '\\x01'\n", 'a: "\\x01"\n', '? |\n  k\n: v\n', 'positions:\n  - {name: a_b, status: active}\n',
         'x: &a {k: v}\ny: *a\n', 'a: "caf\\u00e9"\n', "a: |\n  line\n\n  more\n"]
APART = ['a: "\\ud800"\n', 'a: \t1\n']


@unittest.skipUnless(of_yaml.FAST, "PyYAML here has no libyaml: one parser only")
class BothParsersTest(unittest.TestCase):
    def read(self, loader, text):
        try:
            return ("ok", yaml.load(text, Loader=loader))
        except Exception as e:  # noqa: BLE001
            return ("error", type(e).__name__)

    def test_both_read_these_alike_and_the_check_says_nothing(self):
        for t in ALIKE:
            with self.subTest(text=t):
                a, b = self.read(yaml.SafeLoader, t), self.read(of_yaml.FAST, t)
                self.assertEqual(a[0], b[0])
                if a[0] == "ok":
                    self.assertEqual(a, b)
                self.assertIsNone(of_yaml.disagreement(t))

    def test_the_check_names_a_text_they_read_differently(self):
        for t in APART:
            with self.subTest(text=t):
                self.assertNotEqual(self.read(yaml.SafeLoader, t)[0], self.read(of_yaml.FAST, t)[0])
                self.assertIn("parser", of_yaml.disagreement(t))

    def test_the_hooks_own_entry_starts_on_the_same_parser(self):
        """of_server on PR 572: the hooks run bin/of-hook, which imports of_hook directly; of_hook must already see
        libyaml's parser when it is imported, before the guard parses anything."""
        import shutil
        import tempfile
        d = tempfile.mkdtemp(prefix="ofyh-")
        self.addCleanup(shutil.rmtree, d, True)
        for name in ("of-hook", "of_yaml.py"):
            shutil.copy(os.path.join(HERE, "..", "bin", name), os.path.join(d, name))
        with open(os.path.join(d, "of_hook.py"), "w") as fh:   # a stand-in that says which parser it found
            fh.write("import yaml\nFAST = getattr(yaml.safe_load, '_of_fast', False)\n"
                     "def main(argv):\n    print('fast' if FAST else 'pure')\n    return 0\n")
        r = subprocess.run([sys.executable, os.path.join(d, "of-hook"), "Stop"], capture_output=True, text=True,
                           timeout=60, stdin=subprocess.DEVNULL)
        self.assertEqual(r.stdout.strip(), "fast", r.stderr[-500:])

    def test_the_hook_starts_on_the_same_parser(self):
        code = ("import sys, runpy; sys.argv = ['of', 'hook', 'Nothing']; import yaml\n"
                "try:\n    runpy.run_path(sys.argv[0] if False else %r, run_name='__main__')\n"
                "except SystemExit:\n    pass\n"
                "print('fast' if getattr(yaml.safe_load, '_of_fast', False) else 'pure')") % os.path.join(
                    HERE, "..", "bin", "of")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                           stdin=subprocess.DEVNULL, env=dict(os.environ))
        self.assertEqual(r.stdout.strip().splitlines()[-1:], ["fast"], r.stderr[-500:])


if __name__ == "__main__":
    unittest.main()
