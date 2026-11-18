"""A versioned or alternative interpreter name is read as the name the guard's rules know (docs/guard.md, "Interpreter
names"; of_server, 2026-11-18, found while reviewing organic-framework PR 329: `python3.15 bin/of_tools.py header`
passed G23, and the same name passed G1, G8 and G13)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_guard  # noqa: E402


def deny(cmd):
    return of_guard.critical_deny("Bash", {"command": cmd}, cwd="/Users/alex/of")


class InterpreterNames(unittest.TestCase):

    def test_the_name_the_rules_know(self):
        for name, known in (("python3.15", "python3"), ("python3.12", "python3"), ("python3.13t", "python3"),
                            ("python3.15-intel64", "python3"), ("pypy3", "python3"), ("pypy3.10", "python3"),
                            ("python2.7", "python"), ("python2", "python"), ("pypy", "python"),
                            ("perl5.34", "perl"), ("perl5.30", "perl"), ("ruby3.3", "ruby"), ("php8.2", "php"),
                            ("nodejs", "node"), ("node22", "node"),
                            ("python3", "python3"), ("python", "python"), ("node", "node")):
            self.assertEqual(of_guard._interpreter_name(name), known, name)
        for other in ("python3-config", "python3.15-config", "nodemon", "node-gyp", "perldoc", "phpunit", "rubocop",
                      "pythonic", "cat", ""):
            self.assertEqual(of_guard._interpreter_name(other), other, other)

    def test_a_versioned_name_gets_the_verdict_of_the_plain_one(self):
        cases = (
            ("python3", ("python3.15", "python3.12", "/opt/homebrew/bin/python3.15", "python3.13t", "pypy3",
                         "python3.15-intel64"),
             ("{i} -c \"print(open('.env').read())\"",                      # G8
              "{i} bin/of grant issue x",                                     # G13
              "{i} bin/of_tools.py header acme/g", "{i} -m of_tools header acme/g",  # G23
              "{i} .env", "{i} ~/.ssh/id_rsa",                                # G1
              "sudo {i} -c \"print(open('.env').read())\"",                   # through a wrapper
              "{i} -c 'print(1)'", "{i} -m json.tool notes.json")),           # and what passes still passes
            ("python", ("python2", "python2.7"), ("{i} -c \"print(open('.env').read())\"", "{i} -c 'print(1)'")),
            ("ruby", ("ruby3.3",), ("{i} -e 'puts File.read(\".env\")'", "{i} -e 'puts 1'")),
            ("node", ("nodejs", "node22"), ("{i} -e \"console.log(require('fs').readFileSync('.env','utf8'))\"",
                                            "{i} .env", "{i} -e 'console.log(1)'")),
        )
        for plain, names, commands in cases:
            for cmd in commands:
                want = bool(deny(cmd.format(i=plain)))
                for name in names:
                    self.assertEqual(bool(deny(cmd.format(i=name))), want, cmd.format(i=name))

    def test_a_name_in_other_case_is_the_same_program(self):
        """of_secrets-review on organic-framework PR 332: this Mac's volumes ignore case, so CAT, HEAD and PYTHON3 run
        the real programs, and the guard compared names with their case."""
        for cmd in ("CAT ~/.ssh/id_ed25519", "Cat ~/.ssh/id_ed25519", "HEAD -5 ~/.ssh/id_ed25519",
                    "PYTHON3 -c \"print(open('.env').read())\"", "Python3.15 -c \"print(open('.env').read())\"",
                    "SUDO cat ~/.ssh/id_ed25519", "NICE -n 5 CAT ~/.ssh/id_ed25519", "ENV A=1 cat ~/.ssh/id_ed25519",
                    "/BIN/CAT ~/.ssh/id_ed25519"):
            self.assertTrue(deny(cmd), cmd)
        for cmd in ("CAT notes.txt", "Rscript -e 'print(1)'", "PlistBuddy -c Print x.plist"):
            self.assertIsNone(deny(cmd), cmd)

    def test_the_abi_suffixes(self):
        for name in ("python3.7m", "python3.12d", "python3.7dm", "pythonw3.12"):
            self.assertEqual(of_guard._interpreter_name(name), "python3", name)
            self.assertTrue(deny(name + " -c \"print(open('.env').read())\""), name)

    def test_the_cases_found(self):
        for cmd in ("python3.15 bin/of_tools.py header acme/g", "python3.15 bin/of grant issue x",
                    "python3.12 -c \"print(open('.env').read())\"", "nodejs .env"):
            self.assertTrue(deny(cmd), cmd)


if __name__ == "__main__":
    unittest.main()
