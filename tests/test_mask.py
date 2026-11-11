"""Masking at the boundary (RFC 0002 §4.2). Canaries are generated at run time from a fixed
seed, so no secret-shaped literal lives in this file."""

import json
import os
import random
import re
import string
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_mask  # noqa: E402

R = random.Random(20261105)
KEY = b"k" * 32
DETECTOR = of_mask.Detector()


def rnd(alphabet, n):
    return "".join(R.choice(alphabet) for _ in range(n))


AN = string.ascii_letters + string.digits
HEX = "0123456789abcdef"


def canaries():
    with open(of_mask.BIP39_FILE) as fh:
        words = fh.read().split()
    pem_body = "\n".join(rnd(AN + "+/", 64) for _ in range(12))
    return {
        "private-key": "-----BEGIN RSA PRIVATE KEY-----\n" + pem_body + "\n-----END RSA PRIVATE KEY-----",
        "aws-access-token": "AKIA" + rnd(string.ascii_uppercase + "234567", 16),
        "github-pat": "ghp_" + rnd(AN, 36),
        "slack-bot-token": "xoxb-" + rnd(string.digits, 12) + "-" + rnd(string.digits, 13) + "-" + rnd(AN, 24),
        "anthropic-api-key": "sk-ant-api03-" + rnd(AN + "-_", 93) + "AA",
        "jwt": "eyJ" + rnd(AN, 30) + ".eyJ" + rnd(AN, 40) + "." + rnd(AN + "-_", 43),
        "evm-private-key": rnd(HEX, 64),
        "url-password": rnd(AN, 14),
        "seed-phrase-12": " ".join(R.choice(words) for _ in range(12)),
        "seed-phrase-24": " ".join(R.choice(words) for _ in range(24)),
        "generic-api-key": rnd(AN, 32),
        "curl-user": rnd(AN, 12),
    }


C = canaries()


class TranslateTest(unittest.TestCase):
    def test_mid_pattern_flags(self):
        self.assertEqual(of_mask.re2_to_py(r"\b(p8e-(?i)[a-z0-9]{32})"), r"\b(p8e-(?i:[a-z0-9]{32}))")
        rx = re.compile(of_mask.re2_to_py(r"a(?i)b|c"))
        self.assertTrue(rx.fullmatch("aB") and rx.fullmatch("C"))
        self.assertIsNone(rx.fullmatch("AB"))
        self.assertEqual(of_mask.re2_to_py(r"(?i)abc"), r"(?i)abc")       # a leading flag stays global
        self.assertEqual(of_mask.re2_to_py(r"pat[[:alnum:]]{14}"), r"pat[a-zA-Z0-9]{14}")
        self.assertEqual(of_mask.re2_to_py(r"[(?i)]x"), r"[(?i)]x")         # inside a class it is literal

    def test_whole_ruleset_compiles(self):
        self.assertGreaterEqual(len(DETECTOR.rules), 220)
        self.assertIn("evm-private-key", DETECTOR.kinds())
        self.assertIn("seed-phrase", DETECTOR.kinds())


class DetectTest(unittest.TestCase):
    def masked(self, text):
        m = of_mask.Masker(KEY, DETECTOR)
        out = m.text(text)
        return out, m.take()

    def assertMasked(self, text, value, kind=None):
        out, found = self.masked(text)
        self.assertNotIn(value, out, f"{kind or ''} leaked")
        if kind:
            self.assertIn(kind, {k for k, _ in found}, out)
        return out

    def test_claimed_kinds(self):
        self.assertMasked(f"key file:\n{C['private-key']}\n", C["private-key"], "private-key")
        self.assertMasked(f"export AWS_ACCESS_KEY_ID={C['aws-access-token']}", C["aws-access-token"], "aws-access-token")
        self.assertMasked(f"token {C['github-pat']} ok", C["github-pat"], "github-pat")
        self.assertMasked(f"SLACK={C['slack-bot-token']}", C["slack-bot-token"], "slack-bot-token")
        self.assertMasked(f"ANTHROPIC_API_KEY={C['anthropic-api-key']}", C["anthropic-api-key"], "anthropic-api-key")
        self.assertMasked(f"Bearer {C['jwt']}", C["jwt"])
        self.assertMasked(f'api_key = "{C["generic-api-key"]}"', C["generic-api-key"], "generic-api-key")

    def test_evm_keys_next_to_key_words_with_and_without_0x(self):
        for text in (f"PRIVATE_KEY=0x{C['evm-private-key']}", f"private key: {C['evm-private-key']}",
                     f'"signer_key": "0x{C["evm-private-key"]}"', f"pk={C['evm-private-key']}",
                     f'{{"wallet_pk": "0X{C["evm-private-key"].upper()}"}}',            # of_lab pre-check (a)
                     f"check balance with key 0x{C['evm-private-key']}"):                # of_lab pre-check (b)
            self.assertMasked(text, C["evm-private-key"], "evm-private-key")

    def test_vendor_tokens_without_the_vendor_name(self):
        airtable = "pat" + rnd(AN, 14) + "." + rnd(HEX, 64)
        self.assertMasked(f"export AT={airtable}", airtable, "airtable-personnal-access-token")

    def test_url_password_and_curl_user(self):
        self.assertMasked(f"postgres://admin:{C['url-password']}@db.internal:5432/x", C["url-password"], "url-password")
        self.assertMasked(f"curl -u deploy:{C['curl-user']} https://ci.example/api", C["curl-user"])

    def test_seed_phrases(self):
        for k in ("seed-phrase-12", "seed-phrase-24"):
            out = self.assertMasked(f"mnemonic: {C[k]}\n", C[k], "seed-phrase")
            self.assertTrue(out.startswith("mnemonic: [secret:seed-phrase:"))
        numbered = " ".join(f"{i}. {w}" for i, w in enumerate(C["seed-phrase-12"].split(), 1))
        self.assertMasked(numbered, C["seed-phrase-12"].split()[5], "seed-phrase")
        eleven = " ".join(C["seed-phrase-12"].split()[:11])
        self.assertEqual(self.masked(f"words: {eleven}.")[1], {})

    def test_look_alikes_survive(self):
        tx = rnd(HEX, 64)
        sha = rnd(HEX, 40)
        addr = "0x" + rnd(HEX, 40)
        uuid = "8316e52e-4b1a-4c1e-9f0a-2d4e6f8a9b0c"
        for text in (f"tx hash 0x{tx} confirmed in block 0x{rnd(HEX, 64)}", f"commit {sha}", f"to {addr}",
                     f"session {uuid}", f"transactionHash: 0x{tx}",
                     f'{{"keyHash": "0x{tx}"}}', f"launch_request_key: 0x{tx}", f"public key {tx}",
                     f"seeder_init_code_sha256 = \"{tx}\""):
            out, found = self.masked(text)
            self.assertEqual((out, found), (text, {}), text)

    def test_tags_are_stable_and_keyed(self):
        a, _ = self.masked(f"pk={C['evm-private-key']}")
        b, _ = self.masked(f"PRIVATE_KEY: 0x{C['evm-private-key']}")
        tag = re.search(of_mask.TAG_RX, a).group(0)
        self.assertIn(tag, b)
        other = of_mask.Masker(b"other" * 7, DETECTOR).text(f"pk={C['evm-private-key']}")
        self.assertNotEqual(a, other)

    def test_masking_is_idempotent(self):
        out, _ = self.masked(f"api_key = \"{C['generic-api-key']}\" and {C['github-pat']}")
        again, found = self.masked(out)
        self.assertEqual((again, found), (out, {}))


class JsonTest(unittest.TestCase):
    def test_transcript_line_stays_valid_json_and_loses_every_value(self):
        inner = json.dumps({"env": {"PRIVATE_KEY": "0x" + C["evm-private-key"]}, "note": C["private-key"]})
        line = json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "content": inner},
            {"type": "text", "text": f"db is postgres://u:{C['url-password']}@h/db and {C['seed-phrase-24']}"}]},
            "cwd": "/Users/alex/acme", "PRIVATE_KEY": C["evm-private-key"]})
        m = of_mask.Masker(KEY, DETECTOR)
        row, found = of_mask.mask_row(m, "transcript_record", {"raw": line, "session_id": "S"})
        doc = json.loads(row["raw"])
        self.assertEqual(doc["cwd"], "/Users/alex/acme")
        for k in ("evm-private-key", "url-password", "seed-phrase-24"):
            self.assertNotIn(C[k], row["raw"], k)
        self.assertNotIn(C["private-key"].split("\n")[3], row["raw"])
        self.assertEqual(row["session_id"], "S")
        kinds = {k for k, _ in found}
        self.assertTrue({"evm-private-key", "url-password", "seed-phrase", "private-key"} <= kinds, kinds)

    def test_spans_cut_back_across_strings(self):
        strings = ["abc", "defg", "hi"]                 # joined: "abc\ndefg\nhi"
        out = of_mask._cut(strings, [(1, 6, "k"), (10, 11, "j")], lambda a, b, k: f"<{k}>")
        self.assertEqual(out, ["a<k>", "fg", "h<j>"])
        self.assertEqual(of_mask._cut(strings, [], None), strings)

    def test_event_fields_and_non_json(self):
        m = of_mask.Masker(KEY, DETECTOR)
        ev = {"payload": json.dumps({"text": f"token {C['github-pat']}"}), "record": "{}", "name": "x"}
        row, found = of_mask.mask_row(m, "event", ev)
        self.assertNotIn(C["github-pat"], row["payload"])
        self.assertEqual(sum(found.values()), 1)
        row, _ = of_mask.mask_row(m, "runtime_record", {"raw": f"plain {C['github-pat']} text"})
        self.assertNotIn(C["github-pat"], row["raw"])


class PrefilterTest(unittest.TestCase):
    """The one-scan prefilters give what the plain per-keyword and per-word checks give."""

    @classmethod
    def setUpClass(cls):
        cls.d = of_mask.detector()

    def naive_seeds(self, text):
        out, run, last_end = [], [], None
        for m in of_mask._WORDS.finditer(text):
            w = m.group(0).lower()
            joined = last_end is not None and of_mask._SEP.fullmatch(text, last_end, m.start()) is not None
            if w in self.d.bip39 and (joined or not run):
                run.append(m.span())
            else:
                if len(run) >= 12:
                    out.append((run[0][0], run[-1][1], "seed-phrase"))
                run = [m.span()] if w in self.d.bip39 else []
            last_end = m.end()
        if len(run) >= 12:
            out.append((run[0][0], run[-1][1], "seed-phrase"))
        return out

    def test_keywords_match_the_substring_check(self):
        rnd = random.Random(7)
        kws = sorted(self.d.by_kw)
        for _ in range(300):
            parts = [rnd.choice(kws)[:rnd.randint(1, 12)] if rnd.random() < 0.5 else
                     "".join(rnd.choice(string.ascii_lowercase + "_-:= ") for _ in range(rnd.randint(0, 6)))
                     for _ in range(rnd.randint(1, 30))]
            low = "".join(parts).lower()
            self.assertEqual(self.d.keywords(low), {k for k in kws if k in low}, low)

    def test_seed_runs_match_the_word_by_word_check(self):
        words = sorted(self.d.bip39)
        rnd = random.Random(11)
        found = 0
        seps = [" ", "  ", ", ", "\\n", "\n", "1. ", " 12) ", " is ", " zzzz ", "abc", "\\nn", "; ", "9"]
        for _ in range(400):
            toks = []
            for _ in range(rnd.randint(5, 40)):
                toks.append(rnd.choice(words) if rnd.random() < 0.85 else rnd.choice(["xyzzy", "a", "longwordhere"]))
                toks.append(rnd.choice(seps) if rnd.random() < 0.3 else " ")
            text = "".join(toks)
            got = self.d._seed_phrases(text)
            self.assertEqual(got, self.naive_seeds(text), text)
            found += len(got)
        self.assertGreater(found, 10)  # the cases include real runs, not only rejections

    def test_second_pass_keywords_fall_back_when_escapes_could_hide_one(self):
        m = of_mask.Masker(b"k" * 32, self.d)
        secret = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
        hidden = json.dumps({"t": "token " + secret}).replace("ghp_", "\\u0067hp_")
        self.assertNotIn(secret, m.json_text(hidden))


if __name__ == "__main__":
    unittest.main()
