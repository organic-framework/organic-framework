"""A private key read through a glob, a loop or a copy with an innocent name (docs/guard.md G0.9, G1.13).

2026-11-17: a user organization's subagent ran `for f in <kit>/*; do [ -f "$f" ] && head -40 "$f"; done` over its
position's runner kit and printed the kit's SSH private key into its tool output. No word on the line named the key:
the shell expanded the glob, and the loop handed each file to head as $f (its security owner through its structure
keeper, msg 01a1eb16-9ebf).
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import of_guard  # noqa: E402

HEADER = "-----BEGIN OPENSSH " + "PRIVATE KEY-----\n"  # spelled apart so no scanner takes this file for a key


def deny(cmd, cwd=None):
    return of_guard.critical_deny("Bash", {"command": cmd}, cwd=cwd)


class KeyReadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = os.path.realpath(self.tmp.name)
        self.kit = os.path.join(self.d, "kit")
        os.makedirs(os.path.join(self.kit, "bin"))
        for name, text in (("runner_ed25519", HEADER + "b3BlbnNzaA==\n"), ("runner_ed25519.pub", "ssh-ed25519 AAAA x\n"),
                           ("ssh_config", "Host runner\n  Port 22\n"), ("env.sh", "export RUNNER_HOST=runner\n"),
                           ("notes.txt", HEADER + "b3BlbnNzaA==\n")):
            with open(os.path.join(self.kit, name), "w") as fh:
                fh.write(text)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_loop_over_a_kit_folder_that_printed_the_key(self):
        cmd = f'for f in {self.kit}/*; do [ -f "$f" ] && {{ echo "== $f"; head -40 "$f"; }}; done 2>/dev/null | head -150'
        self.assertTrue(deny(cmd))
        self.assertTrue(deny(f'for f in {self.kit}/ssh_config {self.kit}/runner_ed25519; do cat "$f"; done'))
        self.assertTrue(deny(f'if true; then for f in {self.kit}/*; do tail -n 5 "${{f}}"; done; fi'))
        self.assertIsNone(deny(f'for f in {self.kit}/*.pub {self.kit}/ssh_config; do cat "$f"; done'))
        self.assertIsNone(deny(f'for f in {self.kit}/*; do ls -la "$f"; done'))         # names only

    def test_a_glob_a_reader_is_given(self):
        self.assertTrue(deny(f"head -40 {self.kit}/*"))
        self.assertTrue(deny(f"cat {self.kit}/runner_*"))
        self.assertTrue(deny(f"K={self.kit}; cat $K/*"))                                  # a variable set on the line
        self.assertTrue(deny("head -3 kit/*", cwd=self.d))                               # relative to the session
        self.assertIsNone(deny(f"cat {self.kit}/*.pub {self.kit}/ssh_config"))
        self.assertIsNone(deny(f"ls -la {self.kit}/*"))
        self.assertIsNone(deny(f"cat {self.kit}/bin/*"))                                 # nothing there

    def test_a_key_by_its_content_whatever_its_name(self):
        notes = os.path.join(self.kit, "notes.txt")                                     # a copy under another name
        self.assertTrue(deny(f"cat {notes}"))
        self.assertTrue(deny(f"cp {notes} /tmp/x"))
        self.assertTrue(deny(f"wc -c < {notes} ; base64 < {notes}"))
        self.assertTrue(of_guard.critical_deny("Read", {"file_path": notes}))
        self.assertTrue(of_guard.critical_deny("Grep", {"pattern": "x", "path": notes}))
        self.assertIsNone(deny(f"ssh -i {notes} -F {self.kit}/ssh_config runner true"))  # use, never shown
        self.assertIsNone(deny(f"ls -la {notes}; shasum -a 256 {self.kit}/ssh_config"))
        self.assertIsNone(of_guard.critical_deny("Read", {"file_path": os.path.join(self.kit, "env.sh")}))
        self.assertIsNone(of_guard.critical_deny("Grep", {"pattern": "x", "path": self.kit}))  # a folder
        fifo = os.path.join(self.d, "pipe")
        os.mkfifo(fifo)
        self.assertIsNone(deny(f"cat {fifo}"))                                          # never opened: no hang

    def test_names_only_through_a_glob_or_a_loop_and_never_on_a_remote_host(self):
        header = os.path.join(self.d, "grafana.header")                              # a secret by name (G1.7)
        with open(header, "w") as fh:
            fh.write("Authorization: Bearer x\n")
        # a variable set on the line is G5's own reading of the word (Known limits), not G0.9's
        self.assertIsNone(deny(f"H={header}; python3 - \"$H\" <<'EOF'\nprint(1)\nEOF"))
        self.assertTrue(deny(f"for f in {self.d}/*.header; do cat \"$f\"; done"))   # a loop's glob: by name too
        self.assertTrue(deny(f"K={self.kit}; for f in $K/*; do cat \"$f\"; done"))
        for name in ("kubeconfig.go", "kubeconfig_test.py"):                           # code found by a glob
            with open(os.path.join(self.d, name), "w") as fh:
                fh.write("package x\n")
        self.assertIsNone(deny(f"grep -n Apply {self.d}/*.go {self.d}/*.py"))
        self.assertIsNone(deny("for c in kube-system/kubeconfig-in-cluster; do grep x \"$c\"; done", cwd=self.d))
        self.assertIsNone(deny(f"ssh runner 'head -40 {self.kit}/*'"))             # the remote host's files
        self.assertIsNone(deny(f"ssh runner 'for f in {self.kit}/*; do cat \"$f\"; done'"))

    def test_braces_a_redirection_and_hidden_or_deeper_files(self):
        """of_secrets-review on organic-framework PR 321: brace expansion, a `<` from a loop's variable, and dotglob or
        globstar each printed a key under an innocuous name."""
        k = self.kit
        self.assertTrue(deny(f"cat {k}/{{ssh_config,notes.txt}}"))
        self.assertTrue(deny(f"head {k}/{{ssh_{{config,x}},notes.{{txt,md}}}}"))       # nested
        self.assertIsNone(deny(f"cat {k}/{{ssh_config,env.sh}}"))
        self.assertIsNone(deny(f"grep -rn needle {k}/*/"))                              # a trailing /: folders only
        for n in (1, 2, 3):
            with open(os.path.join(self.d, f"part{n}"), "w") as fh:
                fh.write(HEADER if n == 2 else "x\n")
        self.assertTrue(deny(f"cat {self.d}/part{{1..3}}"))                            # a range
        self.assertIsNone(deny(f"echo ${{HOME}} ; cat {k}/ssh_config"))                 # ${…} is no brace word
        self.assertTrue(deny(f'for f in {k}/*; do head -5 < "$f"; done'))
        self.assertTrue(deny(f'f={k}/notes.txt; head -5 < "$f"'))
        self.assertIsNone(deny(f'for f in {k}/*.pub; do head -5 < "$f"; done'))
        os.makedirs(os.path.join(self.d, "hid"))
        with open(os.path.join(self.d, "hid", ".deploy"), "w") as fh:
            fh.write(HEADER)
        self.assertTrue(deny(f"shopt -s dotglob; cat {self.d}/hid/*"))
        self.assertTrue(deny(f"shopt -s dotglob; for f in {self.d}/hid/*; do cat \"$f\"; done"))
        os.makedirs(os.path.join(self.d, "deep", "a", "b"))
        with open(os.path.join(self.d, "deep", "a", "b", "k"), "w") as fh:
            fh.write(HEADER)
        self.assertTrue(deny(f"shopt -s globstar; cat {self.d}/deep/**"))
        self.assertTrue(deny(f"cat {k}/[[:alpha:]]otes.txt"))                          # a POSIX class
        self.assertTrue(deny(f"cat {k}/[^s]otes.txt"))
        # of_review on organic-framework PR 321, in zsh's reading too: `**/` recursive, classes, negation
        self.assertTrue(deny(f"head -40 {self.d}/deep/**/*"))
        self.assertTrue(deny(f"head -40 {k}/[^.]*"))
        self.assertTrue(deny(f"head -40 {k}/[[:alnum:]]*"))
        self.assertTrue(deny(f"head -40 {k}/[[:punct:][:alpha:]]otes.txt"))             # a class fnmatch lacks

    def test_a_header_further_down_or_a_public_key_is_not_one(self):
        doc = os.path.join(self.d, "doc.md")
        with open(doc, "w") as fh:
            fh.write("# How a key looks\n\n" + HEADER)
        self.assertIsNone(deny(f"cat {doc}"))
        self.assertFalse(of_guard._key_file(os.path.join(self.kit, "runner_ed25519.pub")))
        for head in ("-----BEGIN RSA " + "PRIVATE KEY-----", "-----BEGIN " + "PRIVATE KEY-----",
                     "-----BEGIN EC " + "PRIVATE KEY-----", "-----BEGIN ENCRYPTED " + "PRIVATE KEY-----",
                     "-----BEGIN PGP " + "PRIVATE KEY BLOCK-----", "PuTTY-User-Key-File-3: ssh-ed25519"):
            with open(doc, "w") as fh:
                fh.write("\n" + head + "\n")
            self.assertTrue(of_guard._key_file(doc), head)


if __name__ == "__main__":
    unittest.main()
