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
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin", "rules", "bip39-english.txt")) as _fh:
    _WORDS = [w.strip() for w in _fh if w.strip()]
SEED = [_WORDS[i * 97 % 2048] for i in range(12)]  # twelve list words, as of_review built them


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
        self.assertIsNone(deny("cat " + self.put("token-names.yaml", "- app-db\n- ghcr-pull\n")))  # a YAML list

    def test_a_names_list_that_holds_a_value_stays_secret(self):
        for text in ("evm-signer  Opaque\npassword: hunter2-but-longer\n",          # a credential word with a value
                     f"evm-signer  Opaque\n{KEY}\nAAAA\n",                           # a private key
                     f"r2-writer {TOKEN}\n",                                         # a token
                     "signer: MHgxMjM0NTY3ODlhYmNkZWZBQkNERUY=\n",                   # a base64 value
                     "signer " + "a1" * 32 + "\n",                                    # a long hex value
                     "caf\xe9\x00\x01 binary\n",                                      # not plain text
                     # of_secrets-review on organic-framework PR 332: forms the value patterns missed
                     "app-db\nDB_PASS=abc123\n", "aws_secret_access_key = " + "Q" * 40 + "\n",
                     "app-db=zq8w3k9x7m2p5r4t6y1u\n", "app-db  correcthorsebatterystaple42\n",
                     "- name: app-db\n  value: hunter2\n",
                     # of_review on organic-framework PR 332
                     "evm-signer  " + " ".join(SEED) + "\n", "evm-signer  " + " ".join(SEED[:6]) + "\n"
                     + " ".join(SEED[6:]) + "\n", "db-main  Tr0ub4dor&3x\n",
                     "heroku-api  3f2b8c1e-9a4d-4e7b-b1c2-7d8e9f0a1b2c\n", "r2-writer  k3j9x2m8q7w1e5r4t6y0u8i2o3p4a5s6\n",
                     "data:\n  signer: c2hvcnQxMjM=\n",
                     # of_review on organic-framework PR 332, third pass
                     "registry/0123456789abcdef0123456789abcdef\n", "12345678901234567890123456789012d\n",
                     "\ndemo ABCDEFGHIJKLMNOP\n", "ABCDEFGHIJKLMNOP\n", "abcdef.abcdefghijklmnop\n",
                     # of_review on organic-framework PR 332, fourth pass
                     "NAME DATA AGE\ndemo 1000000 100000d\n", "portal 12345678\n", "-".join(["123456"] * 8) + "\n",
                     # of_review on organic-framework PR 332, fifth pass
                     "billing 4242-4242-4242-4242\n", "name,password\ndemo,public-test-passphrase\n"):
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

    def test_a_remote_command_gets_no_exception(self):
        """of_review on organic-framework PR 332: the file a remote command reads is another machine's."""
        p = self.put("evm-secret-names.txt", "demo\n")
        self.assertIsNone(deny("grep -q demo " + p))
        self.assertTrue(deny("ssh somehost grep -q demo " + p))
        code = "print(open('" + p + "').read())"
        self.assertIsNone(deny("python3 - <<'PY'\n" + code + "\nPY"))                # here: the content is checked
        self.assertTrue(deny("ssh somehost python3 - <<'PY'\n" + code + "\nPY"))     # there: another machine's
        self.assertTrue(deny("'ssh' buildhost true; cat " + p))                       # a remote command in quotes

    def test_the_deny_without_names_is_unchanged(self):
        p = self.put("evm-secret-names.txt", "evm-signer  Opaque\n")
        self.assertTrue(deny("cat " + p.replace("-names", "")))


if __name__ == "__main__":
    unittest.main()
