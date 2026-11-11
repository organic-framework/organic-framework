"""of secrets review on synthetic data: values never appear in any output."""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_secrets  # noqa: E402

KEY = b"k" * 32
PEM = "-----BEGIN OPENSSH " + "PRIVATE KEY-----\\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ\\n-----END OPENSSH PRIVATE KEY-----"
EVM = "ab" * 32
AWS = "AKIAZZZZZZZZZZZZZZZZ"
SECRETS = (PEM, EVM, AWS, "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ")


def finding(rule, path, line_no, line, value, entropy=4.5):
    s = line.index(value)
    return {"RuleID": rule, "File": path, "StartLine": line_no, "EndLine": line_no,
            "StartColumn": len(line[:s].encode()) + 1, "EndColumn": len(line[:s + len(value)].encode()),
            "Match": "REDACTED", "Secret": "REDACTED", "Entropy": entropy}


class ReviewTest(unittest.TestCase):
    def test_review_classifies_orders_and_never_reveals(self):
        with tempfile.TemporaryDirectory() as d:
            tr = os.path.join(d, "s.jsonl")
            lines = [json.dumps({"timestamp": "2026-10-01T10:00:00Z", "text": f"cat ~/.ssh/id_ed25519 -> {PEM}"}),
                     json.dumps({"timestamp": "2026-10-02T10:00:00Z", "text": f"export AWS_KEY={AWS} done"}),
                     json.dumps({"timestamp": "2026-10-03T10:00:00Z", "text": f"wallet private key: 0x{EVM} ok"}),
                     json.dumps({"timestamp": "2026-10-04T10:00:00Z", "text": f"again PRIVATE_KEY=0x{EVM}"})]
            with open(tr, "w") as fh:
                fh.write("\n".join(lines) + "\n")
            ctx = os.path.join(d, "context.json")
            self.assertEqual(of_secrets.scan_contextual([tr], ctx), 2)
            gl = os.path.join(d, "gitleaks.json")
            with open(gl, "w") as fh:
                pem_in_line = lines[0][lines[0].index("-----BEGIN"):lines[0].index("KEY-----\"") + 8]
                json.dump([finding("private-key", tr, 1, lines[0], pem_in_line),
                           finding("aws-access-token", tr, 2, lines[1], AWS),
                           finding("generic-api-key", tr, 2, lines[1], "AWS_KEY", entropy=2.0)], fh)
            items = of_secrets.build_review([gl, ctx], KEY, width=40)
            self.assertEqual([i["kind"] for i in items], ["private-key/ssh", "private-key/evm-wallet", "aws-access-token"])
            evm = items[1]
            self.assertEqual((len(evm["locations"]), evm["first"][:10], evm["last"][:10]), (2, "2026-10-03", "2026-10-04"))
            dump = json.dumps(items)
            for secret in SECRETS:
                self.assertNotIn(secret, dump)
            self.assertIn("[secret:", items[2]["context"])
            self.assertIn("export AWS_KEY=", items[2]["context"])

    def test_reviewed_latest_mark_wins(self):
        import sqlite3
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE event (ts TEXT, name TEXT, payload TEXT)")
        for ts, d in (("1", "rotate"), ("2", "rotated")):
            c.execute("INSERT INTO event VALUES (?, 'of.secret_reviewed', ?)", (ts, json.dumps({"hmac": "h", "decision": d})))
        self.assertEqual(of_secrets.reviewed(c), {"h": "rotated"})


if __name__ == "__main__":
    unittest.main()


class RotationsTest(unittest.TestCase):
    def test_rows_for_rotate_marks_only_without_values(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "storefront"))
            enc = "".join(c if c.isalnum() else "-" for c in os.path.realpath(os.path.join(root, "storefront")))
            loc = f"/x/.claude/projects/{enc}--claude-worktrees-wt/s.jsonl:12"
            items = [{"hmac": "aa" * 8, "kind": "private-key/evm-wallet", "rule": "evm-private-key", "orgs": ["mt"],
                      "locations": [loc], "first": "2026-10-01T00:00:00Z", "last": "2026-10-02T00:00:00Z", "context": "x"},
                     {"hmac": "bb" * 8, "kind": "url-password", "rule": "url-password", "orgs": ["mt"],
                      "locations": [loc], "first": "2026-10-01", "last": "2026-10-01", "context": "y"},
                     {"hmac": "cc" * 8, "kind": "aws-access-token", "rule": "aws-access-token", "orgs": ["other"],
                      "locations": [loc], "first": "", "last": "", "context": "z"}]
            marks = {"aa" * 8: "rotate", "bb" * 8: "false_positive", "cc" * 8: "rotate"}
            rows = of_secrets.rotations(items, marks, "mt", [root], {"storefront": {"repos": ["storefront"]}})
            self.assertEqual(len(rows), 1)
            r = rows[0]
            self.assertEqual((r["priority"], r["class"], r["systems"], r["repos"], r["ref"]),
                             (1, "wallet", ["storefront"], ["storefront"], "aa" * 4))
            self.assertNotIn("context", r)

