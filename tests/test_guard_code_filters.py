"""A word interpreter code only compares or searches for is a filter, not a read of a file by that name (G8).

2026-11-18: a user organization's security keeper ran a python one-liner over its data room's secrets register, a CSV
of names and ids with no value in it, and printed the rows whose text held one credential kind. The guard denied it as
"a command that reads a secret file", because the word it searched for matched a secret file's name; the keeper then
split the word to get past the deny (its structure keeper, msg 01a1ecc1-40a4). Asks: a filter or pattern argument
naming a secret word is no read of that file, and the deny names the string it matched.
"""
import os
import shlex
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import of_guard  # noqa: E402


def deny(cmd):
    return of_guard.critical_deny("Bash", {"command": cmd}, cwd="/Users/alex/register")


def py(code, heredoc=False):
    return f"python3 - <<'EOF'\n{code}\nEOF" if heredoc else "python3 -I -c " + shlex.quote(code)


class CodeFilterTest(unittest.TestCase):
    def test_the_register_search_passes(self):
        code = ("import csv\n"
                "for r in csv.reader(open('secrets-inventory.csv', newline='')):\n"
                "    t = ' '.join(r).lower()\n"
                "    if 'kubeconfig' in t and r[0].startswith(('K8S', 'MAC', 'OFF')):\n"
                "        print(r[0], '|', r[2][:60])")
        self.assertIsNone(deny(py(code)))
        self.assertIsNone(deny(py(code, heredoc=True)))

    def test_compared_or_searched_words_are_filters(self):
        for code in (
                "import sys\nfor l in sys.stdin:\n    if l.rstrip().endswith('.env'): print(l)",
                "import re, sys\nprint(re.findall(r'kubeconfig|token', sys.stdin.read()))",
                "import re, sys\nrx = re.compile('id_ed25519')\nprint(sum(1 for l in sys.stdin if rx.search(l)))",
                "import sys\nn = sum(1 for l in sys.stdin if l.strip() == 'id_ed25519')\nprint(n)",
                "import sys\nfor l in sys.stdin:\n    if l.strip() not in ('.env', 'kubeconfig'): print(l)",
                "import fnmatch, sys\nprint([l for l in sys.stdin if fnmatch.fnmatch(l.strip(), '*.pem')])",
                "import json\nfor r in json.load(open('register.json')):\n"
                "    match r['kind']:\n        case 'kubeconfig': print(r['id'])",
                "import sys\nprint(sys.stdin.read().count('.netrc'))",
                "import pathlib\nfor l in pathlib.Path('register.csv').read_text().splitlines():\n"
                "    if '.env' in l: print(l.split(',')[0])",
        ):
            self.assertIsNone(deny(py(code)), code)

    def test_a_word_the_code_may_open_is_still_a_read(self):
        for code in (
                "print(open('kubeconfig').read())",
                # the same word opened as well as compared
                "import sys\nt = sys.stdin.read()\nif 'kubeconfig' in t: print(open('kubeconfig').read())",
                # held in a name, then compared and opened
                "import sys\np = 'kubeconfig'\nif p in sys.argv: print(open(p).read())",
                # a folder's names filtered by the word: the filter picks the file the code opens
                "import os\nfor f in os.listdir('.'):\n    if 'kubeconfig' in f: print(open(f).read())",
                "import glob\nprint(glob.glob('*.pem'))",
                "import pathlib\nfor p in pathlib.Path('.').rglob('*'):\n    if p.name.endswith('.pem'): print(p)",
                # a name read from data and opened
                "import sys\nfor l in sys.stdin:\n    if 'kubeconfig' in l: print(open(l.strip()).read())",
                "import sys, pathlib\nfor l in sys.stdin:\n"
                "    if l.strip().endswith('.env'): print(pathlib.Path(l.strip()).read_text())",
                # a command run on a name from data
                "import subprocess, sys\nfor l in sys.stdin:\n"
                "    if '.env' in l: subprocess.run(['cat', l.strip()])",
                "import os, sys\nfor l in sys.stdin:\n    if l.strip().endswith('.env'): os.system('cat ' + l)",
                # the opener handed on under another name, or to another function
                "import sys\no = open\nfor l in sys.stdin:\n    if '.env' in l: print(o(l.strip()).read())",
                "import sys\nif '.env' in sys.argv: print([f.read() for f in map(open, sys.argv[1:])])",
                # a literal object asked for a computed member
                "import sys, zipfile\nfor l in sys.stdin:\n"
                "    if l.strip().endswith('.env'): print(zipfile.ZipFile('a.zip').open(l.strip()).read())",
                # a command inside a longer name, a module's own table
                "import asyncio, sys\nif '.env' in sys.argv: asyncio.create_subprocess_exec('cat', sys.argv[1])",
                "import os, sys\nif '.env' in sys.argv: print(os.__dict__['list' + 'dir']('.'))",
                # code made at run time
                "import sys\nif '.env' in sys.argv: exec(sys.stdin.read())",
                # a word the code builds text with, not a filter
                "s = open('notes.md').read().replace('x', 'kubeconfig')\nprint(s)",
                # python 2 does not parse: no carve-out
                "import sys\nfor l in sys.stdin:\n    if 'kubeconfig' in l: print l",
        ):
            self.assertTrue(deny(py(code)), code)

    def test_a_slip_in_the_reading_keeps_the_deny(self):
        from unittest import mock
        code = "import sys\nfor l in sys.stdin:\n    if 'kubeconfig' in l: print(l)"
        self.assertIsNone(deny(py(code)))
        with mock.patch.object(of_guard, "_py_filters_of", side_effect=RecursionError):
            self.assertIn("G8", deny(py(code))[0])

    def test_other_interpreters_are_unchanged(self):
        self.assertTrue(deny("node -e 'process.stdin.on(\"data\", d => d.includes(\"kubeconfig\") && 1)'"))

    def test_the_deny_names_the_string_and_the_rule(self):
        why = deny(py("import sys\nfor l in sys.stdin:\n    if 'kubeconfig' in l: print(open(l.strip()).read())"))[0]
        self.assertIn("'kubeconfig'", why)
        self.assertIn("G8", why)
        self.assertIn("filter", why)
        self.assertTrue(of_guard.owns_advice(why))  # its advice is its own, with the owner's line kept
        self.assertIn("ask the owner", why)
        # a shell reader keeps its own wording
        self.assertIn("reads a secret file (id_rsa)", deny("cat ~/.ssh/id_rsa")[0])


if __name__ == "__main__":
    unittest.main()
