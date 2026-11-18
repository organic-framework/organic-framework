"""docs/guard.md G1.8: a names list in an organization's config folder is no secret file when the guard finds no value
in it (2026-11-18, a user organization's structure keeper, msg 01a1ee9d-ae5f: a keeper's names-only table of its
Secrets, `<kind>-secret-names.txt`, was denied by G1 and G8 for the word secret in its name, and it blocked a manifest
generator its owner had approved)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_guard  # noqa: E402

KEY = "-----BEGIN OPENSSH " + "PRIVATE KEY-----"  # spelled apart so this file trips no scanner
TOKEN = "ghp_" + "Ab3" * 12


def deny(cmd):
    return of_guard.critical_deny("Bash", {"command": cmd}, cwd="/Users/alex")


class NamesLists(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = os.path.join(os.path.realpath(self.tmp.name), ".config", "acme", "off")
        os.makedirs(self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, text):
        p = os.path.join(self.cfg, name)
        with open(p, "w") as fh:
            fh.write(text)
        return p

    def test_a_names_list_with_no_value_is_read(self):
        table = ("NAME                         TYPE                                  DATA   AGE\n"
                 "app-signer-main              Opaque                                1      12d\n"
                 "ghcr-pull                    kubernetes.io/dockerconfigjson        1      40d\n")
        p = self.put("evm-secret-names.txt", table)
        self.assertIsNone(deny("cat " + p))
        self.assertIsNone(deny(f"python3 - <<'EOF'\nprint(open('{p}').read())\nEOF"))  # G8
        self.assertIsNone(of_guard.critical_deny("Read", {"file_path": p}))
        self.assertIsNone(deny("cat " + self.put("token-names.csv", "name,kind\nr2-writer,api\n")))

    def test_a_names_list_that_holds_a_value_stays_secret(self):
        for text in ("evm-signer  Opaque\npassword: hunter2-but-longer\n",          # a credential word with a value
                     f"evm-signer  Opaque\n{KEY}\nAAAA\n",                           # a private key
                     f"r2-writer {TOKEN}\n",                                         # a token
                     "signer: MHgxMjM0NTY3ODlhYmNkZWZBQkNERUY=\n",                   # a base64 value
                     "signer " + "a1" * 32 + "\n",                                    # a long hex value
                     "caf\xe9\x00\x01 binary\n"):                                      # not plain text
            p = self.put("evm-secret-names.txt", text)
            self.assertTrue(deny("cat " + p), text[:30])
            self.assertTrue(of_guard.critical_deny("Read", {"file_path": p}), text[:30])

    def test_only_a_names_list_that_the_guard_can_read(self):
        missing = os.path.join(self.cfg, "evm-secret-names.txt")
        self.assertTrue(deny("cat " + missing))                                    # nothing to check: secret
        big = self.put("evm-secret-names.txt", "evm-signer  Opaque\n" * 20000)    # over the size it reads
        self.assertTrue(deny("cat " + big))
        self.assertTrue(deny("cat " + self.put("secret-names.env", "evm-signer\n")))  # an env file holds values
        self.assertTrue(deny("cat " + self.put("evm-secret.txt", "evm-signer  Opaque\n")))  # no names part

    def test_the_deny_without_names_is_unchanged(self):
        p = self.put("evm-secret-names.txt", "evm-signer  Opaque\n")
        self.assertTrue(deny("cat " + p.replace("-names", "")))


if __name__ == "__main__":
    unittest.main()
