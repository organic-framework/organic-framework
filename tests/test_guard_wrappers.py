"""docs/guard.md G0.6: the wrappers `script`, `timeout` and `xargs`, read every way (of_secrets-review and of_review on
organic-framework PR 332 at c53d6f95, PR comments 6994559305 and 6994609363). Over six passes each new branch of a
wrapper's reader dropped a reading the old one had; the readings are now added to the plain one, a deny by any of them
denies, and an option the reader does not read exactly makes every later word a possible command."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_guard  # noqa: E402

KEY = "~/.ssh/id_" + "ed25519"  # a private key's usual path
OP = "op item --help"            # a denied command (G9), as of_review probed with it


def deny(cmd, cwd="/Users/alex/of"):
    return of_guard.critical_deny("Bash", {"command": cmd}, cwd=cwd)


class WrapperReadings(unittest.TestCase):

    def test_the_forms_the_parent_denied(self):
        """of_secrets-review, item 1: seven forms c53d6f95's parent denied and c53d6f95 passed."""
        for cmd in (f"exec 3<{KEY} cat <&3", f"command cat {KEY}", f"command -p cat {KEY}", f"taskset -c 0,16 cat {KEY}", f"chrt -f 10 cat {KEY}", f"taskset -Z 0,16 x cat {KEY}",
                    f"timeout --kill-a 1 2 cat {KEY}",
                    f"script -t --command='cat {KEY}' /dev/null",
                    f"script -c 'cat {KEY}' -E never /dev/null",
                    f"script </dev/null -c 'cat {KEY}' /dev/null",
                    f"env -S 'script -c \"cat {KEY}\" /dev/null'",
                    f"script -q -c \"script -q -c 'script -q -c \\\"cat {KEY}\\\" /dev/null' /dev/null\" /dev/null",
                    f"script -c 'sh -s' /dev/null <<< 'cat {KEY}'"):
            self.assertTrue(deny(cmd), cmd)

    def test_the_forms_that_passed_before(self):
        """of_secrets-review, item 2: three forms that passed at the parent too."""
        for cmd in (f"script --echo never -qc'cat {KEY}' /dev/null",
                    f"script --comm='cat {KEY}' /dev/null",
                    f"echo cat | xargs --max-a 1 -tJ MARK MARK {KEY}"):
            self.assertTrue(deny(cmd), cmd)

    def test_the_forms_af79fa6_denied(self):
        """of_review, required change 1."""
        for cmd in (f"timeout --kill-a 2 1 {OP}",
                    f"2>/dev/null script -q -c '{OP}' /dev/null",
                    f"script -q -c sh /dev/null <<'EOF'\n{OP}\nEOF",
                    f"script -q -c sh /dev/null <<EOF\n{OP}\nEOF"):
            self.assertTrue(deny(cmd), cmd)

    def test_the_forms_earlier_passes_closed_stay_closed(self):
        """Every form both reviews at c53d6f95 name as denied there keeps its verdict."""
        for cmd in ("script -t --command=x /dev/null cat " + KEY, f"timeout -f ' 2' cat {KEY}",
                    f"timeout -f 'nan(x)' cat {KEY}", f"timeout -f -- ' 1' {OP}", f"echo cat | xargs -tJ MARK MARK {KEY}",
                    f"script -qc'cat {KEY}' /dev/null", f"script -c'cat {KEY}' /dev/null",
                    f"script -q -c 'true && cat {KEY}' /dev/null", f"script -c 'true; ({OP})' /dev/null",
                    f"script -qc'{OP}' /dev/null"):
            self.assertTrue(deny(cmd), cmd)

    def test_the_second_reviews_forms_at_16cfcee(self):
        """of_secrets-review on PR 332 at 16cfcee: chrt's optional priority, and script's input when the redirection
        comes first or script has no other word."""
        for cmd in (f"chrt -o cat {KEY}", f"chrt --other cat {KEY}", f"chrt -b cat {KEY}", f"chrt -i cat {KEY}",
                    f"<<< 'cat {KEY}' script -q", f"2>&1 script -q <<< 'cat {KEY}'", f"script <<'END'\ncat {KEY}\nEND",
                    f"<<'END' script -q\ncat {KEY}\nEND", f"< /dev/null 2>&1 script <<< 'cat {KEY}'"):
            self.assertTrue(deny(cmd), cmd)
        for cmd in ("chrt -f 10 ls", "chrt -o ls -la", "chrt -o 0 ls", "script -q <<'END'\nls\nEND",
                    "cat <<'END' | grep x\nhello\nEND"):
            self.assertIsNone(deny(cmd), cmd)

    def test_the_eighth_pass(self):
        """of_secrets-review on PR 332 at a1842fc: GNU xargs's optional long values, xargs's -a file, chrt's priority
        through a variable, and script's input after other redirections or a list operator."""
        for cmd in (f"xargs --verbose --replace cat {KEY}", f"xargs --verbose --eof cat {KEY}",
                    f"xargs --verbose --max-lines cat {KEY}", f"xargs --verbose -a {KEY} true", f"xargs -a {KEY} true",
                    f"xargs --arg-file={KEY} true", f"p=0; chrt -o \"$p\" cat {KEY}",
                    f"2>&1 <<'END' script -q\ncat {KEY}\nEND", f"true && <<'END' script -q\ncat {KEY}\nEND",
                    f"0<&0 script -q <<< 'cat {KEY}'"):
            self.assertTrue(deny(cmd), cmd)
        for cmd in ("xargs --verbose --replace=X echo X", "printf a | xargs -a list.txt echo", "cat <&3",
                    "ls 2>&1 | wc", "cat <<'EOF' > notes.txt\nhello\nEOF"):
            self.assertIsNone(deny(cmd), cmd)

    def test_the_ninth_pass(self):
        """Both reviews on PR 332 at 228201a: an escaped `\\<` before a background `&` is a word, so the command after
        it runs; chrt's priority by brace expansion; a redirection between nice's -n and its value."""
        for cmd in (f"echo a\\< & {OP}", "echo a\\< & cat .env", f"printf %s x\\< & {OP}", f"echo a\\<& {OP}",
                    f"echo \\< & cat {KEY}", f"echo a\\> & {OP}", f"chrt -o {{0..0}} cat {KEY}",
                    f"p=0; nice -n </dev/null 0 chrt -o \"$p\" cat {KEY}"):
            self.assertTrue(deny(cmd), cmd)
        for cmd in ("sleep 1 & echo done", "exec 3<>/dev/tcp/127.0.0.1/80", "cat <&3 && echo ok"):
            self.assertIsNone(deny(cmd), cmd)

    def test_plain_uses_still_run(self):
        for cmd in ("timeout 30 git status", "timeout --preserve-status 5 python3 -m unittest tests.test_of",
                    "timeout -k 5 60 make test", "printf x | xargs -0 -n1 grep foo", "find . -name '*.py' | xargs wc -l",
                    "xargs -I{} echo {} < list.txt", "script -q /dev/null ls -la", "script -q -c 'ls -la' /dev/null",
                    "2>/dev/null timeout 5 git fetch",
                    # the replay's two new denies: a later path ending in /env read as env under a doubted taskset
                    "taskset -c 0,16 nice -n 19 docker run --rm --env-file /srv/app/deploy/env img run",
                    "chrt -f 10 nice -n 5 docker run --env-file /srv/app/deploy/env img run",
                    # the follow-up's replay: the shell's fd idiom and `command -v`, nested where the depth bound bites
                    "sh -c '( ( for p in 1 2; do (exec 3<>/dev/tcp/127.0.0.1/$p) 2>/dev/null && echo $p; done ) )'",
                    "sh -c 'echo $(readlink -f $(command -v awk))'", "command -v qemu-system-x86_64"):
            self.assertIsNone(deny(cmd), cmd)


class TheReadingsAreBounded(unittest.TestCase):
    """of_review on organic-framework PR 332 at 16cfcee: an xargs line of 384 words took 41 s to judge, past the hook's
    30 s, so the hook was killed and its deny lost; four subshells put a doubted timeout's readings past G0's depth, where
    a reading passed. Past either bound the command is denied."""

    def test_a_long_doubted_line_is_denied_fast(self):
        import time
        words = " ".join(["label"] * 384)
        for cmd in (f"xargs --verbos echo {words} ; {OP}", f"xargs --verbos echo {words}"):
            start = time.monotonic()
            self.assertTrue(deny(cmd))
            self.assertLess(time.monotonic() - start, 5)
        self.assertIsNone(deny(f"xargs --verbose echo {words}"))    # a full option name is read exactly
        # the tails are sized before any is built (of_review at a1842fc: 3,000 words took 7.3 s, 20,000 took 320 s)
        for n in (3000, 20000):
            start = time.monotonic()
            self.assertTrue(deny("script -q /dev/null echo " + " ".join(["label"] * n) + f" ; {OP}"))
            self.assertLess(time.monotonic() - start, 5)

    def test_past_the_depth_is_denied(self):
        self.assertTrue(deny(f"( ( ( ( timeout --kill-a 2 1 {OP} ) ) ) )"))
        self.assertTrue(deny(f"( ( ( ( script -q -c '{OP}' /dev/null ) ) ) )"))


class TheFolderAWrapperRunsIn(unittest.TestCase):
    """of_review, required change 1 (from its Codex run): `cd bin && script -c …` judged the string in the session's
    folder, not the one the line had moved to, so an organization's protected code ran through it (G21)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.bin = os.path.join(self.root, "acme-backup", "bin")
        os.makedirs(self.bin)
        of_guard.use_protection(code=[self.bin])

    def tearDown(self):
        of_guard.use_protection()
        self.tmp.cleanup()

    def test_the_string_is_judged_where_the_line_is(self):
        top = os.path.dirname(self.bin)
        for cmd in (f"cd {top} && script -q -c 'bash bin/run.sh' /dev/null",
                    f"cd {self.bin} && script -q -c 'sh run.sh' /dev/null"):
            self.assertIn("protected_code", (deny(cmd, self.root) or [""])[0], cmd)
        self.assertIsNone(deny(f"cd {top} && script -q -c 'cat bin/run.sh' /dev/null", self.root))
        # a folder held in a variable (of_review, non-blocking)
        self.assertIn("protected_code", (deny(f'd={top}; cd "$d" && script -q -c "bash bin/run.sh" /dev/null',
                                              self.root) or [""])[0])


if __name__ == "__main__":
    unittest.main()
