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

    def test_a_name_in_other_unicode_case_and_clustered_options(self):
        """of_secrets-review on organic-framework PR 332 (third pass)."""
        for cmd in ("aw\u212a '{print}' ~/.ssh/id_ed25519", "\u017fed -n p ~/.ssh/id_ed25519",
                    "sudo -nu root cat ~/.ssh/id_ed25519", "/usr/bin/time -lo /dev/null cat ~/.ssh/id_ed25519",
                    "env -P /bin cat ~/.ssh/id_ed25519", "exec -ca label /bin/cat ~/.ssh/id_ed25519",
                    'c$"at" ~/.ssh/id_ed25519', 'C$"AT" ~/.ssh/id_ed25519'):
            self.assertTrue(deny(cmd), cmd)
        for cmd in ("sudo -u root ls", "stdbuf -i0 cat notes.txt", "xargs -I{} echo {}", 'echo $"hello"'):
            self.assertIsNone(deny(cmd), cmd)

    def test_a_cluster_is_read_only_while_every_letter_is_known(self):
        """of_secrets-review on organic-framework PR 332, fourth pass: the cluster reading had taken the command for a
        value where a letter was missing from the table."""
        k = "~/.ssh/id_ed25519"
        for cmd in (f"xargs -tJn cat {k}", f"env -iSX=u cat {k}", f"xargs -J n cat {k}", f"env -iS 'cat {k}'",
                    f"env -iS'cat {k}'", f"env -iScat {k}", f"sudo -nR / cat {k}", f"timeout -- 2 cat {k}",
                    f"sudo -Qu root cat {k}", f"time -lo /dev/null cat {k}", f"timeout -vs TERM -- 2 cat {k}",
                    f"timeout -vs TERM -- .5 cat {k}"):
            self.assertTrue(deny(cmd), cmd)
        for cmd in (f"sudo -Q root cat {k}", f"xargs -Z n cat {k}", f"nice -Zq 5 cat {k}"):  # letters no table knows
            self.assertTrue(deny(cmd), cmd)
        for cmd in ("sudo -nu root ls", "xargs -0 -n1 echo", "env -i PATH=/bin ls", "timeout -- 2 ls",
                    "sudo -Q root ls notes.txt"):
            self.assertIsNone(deny(cmd), cmd)

    def test_the_older_gaps_found_with_the_approval(self):
        """of_secrets-review's approval of organic-framework PR 332: four ways past G1 older than it."""
        k = "~/.ssh/id_ed25519"
        for cmd in (f"taskpolicy -ad default -g default cat {k}", f"</dev/null taskpolicy -ad default cat {k}",
                    f"timeout -- 1e1 cat {k}", f"timeout -- +2 cat {k}", f"timeout +2 cat {k}", f"timeout inf cat {k}",
                    f"echo cat | xargs -J X X {k}", f"echo cat | xargs -I{{}} {{}} {k}",
                    f"echo cat | xargs --replace=R R {k}"):
            self.assertTrue(deny(cmd), cmd)
        for cmd in ("echo hi | xargs -I{} echo {}", "timeout 5 ls", "</dev/null sudo ls", "taskpolicy -b ls",
                    "ls | xargs -J % cp % /tmp/x"):
            self.assertIsNone(deny(cmd), cmd)
        # of_review on organic-framework PR 332, fifth pass: macOS timeout's -f and -p, script's -F, GNU script -c
        for cmd in ("timeout -ps TERM -k 4 2 op item --help", "timeout -p -s TERM 5 timeout -ps TERM 2 op item --help",
                    "< /dev/null timeout -ps TERM 2 op item --help", "env -S 'timeout -ps TERM 2 op item --help'",
                    "script -qF /dev/null op item --help", f"script -qc 'cat {k}' /dev/null"):
            self.assertTrue(deny(cmd), cmd)
        for cmd in ("timeout -fp 5 ls", "script -q /dev/null ls"):
            self.assertIsNone(deny(cmd), cmd)

    def test_the_sixth_pass(self):
        """of_secrets-review and of_review on organic-framework PR 332 at af79fa6: forms its parent denied, and two
        still open."""
        k = "~/.ssh/id_ed25519"
        for cmd in (f"script -q -t --command=x /dev/null cat {k}",          # macOS -t takes --command=x
                    f"timeout -f ' 2' cat {k}", f"timeout -f 'nan(x)' cat {k}", f"timeout 0x1p3 cat {k}",
                    f"echo cat | xargs -tJ MARK MARK {k}", f"echo cat | xargs -0tI% % {k}",
                    f'script -q -c "true && cat {k}" /dev/null', f'script -qc"true && cat {k}" /dev/null',
                    f"script -c'cat {k}' /dev/null", f"sudo script -q -c 'ls; cat {k}' /dev/null"):
            self.assertTrue(deny(cmd), cmd)
        for cmd in ("timeout 5 ls", "timeout -f 2 ls", "script -q /dev/null ls", "script -qc 'ls -la' /dev/null",
                    "echo x | xargs -tJ % echo %", "ls | xargs -0 -n 1 echo"):
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
