#!/usr/bin/env python3
"""Offline pre-check for lab-secrets-v0 (docs/lab.md, section 14), before the server gate.

Runs of_mask's Masker over synthetic canaries, with no server and no real data:
1. For every claimed gitleaks rule, generate a random string from the rule's own regex
   (seeded), keep it only if the rule's regex matches it, and check that the masker
   tags it, and under which kind.
2. Hand-made canaries for the high-signal kinds, in realistic contexts (prompts, .env,
   curl, JSON records, CI logs, cron prompts).
3. Unclaimed kinds (reported, not gated) and look-alikes (false positives by class).

It never prints a canary value: only rule ids, shapes and counts.
Usage: mask_precheck.py [--seed 42] [--json]
"""

import argparse
import json
import os
import random
import re
import string
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..", "bin"))
import of_mask  # noqa: E402

try:
    import re._parser as sre_parse
    import re._constants as sre_c
except ImportError:  # older Pythons
    import sre_parse
    import sre_constants as sre_c

ANY = string.ascii_letters + string.digits


def _gen(tree, rng, depth=0):
    out = []
    for op, av in tree:
        if op is sre_c.LITERAL:
            out.append(chr(av))
        elif op is sre_c.NOT_LITERAL:
            out.append(rng.choice([c for c in ANY if ord(c) != av]))
        elif op is sre_c.ANY:
            out.append("." if rng.random() < 0.5 else rng.choice(ANY))
        elif op is sre_c.IN:
            out.append(_pick_in(av, rng))
        elif op in (sre_c.MAX_REPEAT, sre_c.MIN_REPEAT, getattr(sre_c, "POSSESSIVE_REPEAT", None)):
            lo, hi, sub = av
            hi = lo + 3 if hi is sre_c.MAXREPEAT or hi > lo + 40 else hi
            n = rng.randint(lo, max(lo, hi)) if lo != hi else lo
            for _ in range(n):
                out.append(_gen(sub, rng, depth + 1))
        elif op is sre_c.SUBPATTERN:
            out.append(_gen(av[-1], rng, depth + 1))
        elif op is sre_c.BRANCH:
            out.append(_gen(rng.choice(av[1]), rng, depth + 1))
        elif op in (sre_c.AT, sre_c.ASSERT, sre_c.ASSERT_NOT, sre_c.GROUPREF):
            continue
        elif op is getattr(sre_c, "ATOMIC_GROUP", None):
            out.append(_gen(av, rng, depth + 1))
        else:
            continue
    return "".join(out)


def _pick_in(items, rng):
    chars, negate = [], False
    for op, av in items:
        if op is sre_c.NEGATE:
            negate = True
        elif op is sre_c.LITERAL:
            chars.append(chr(av))
        elif op is sre_c.RANGE:
            chars.extend(chr(c) for c in range(av[0], av[1] + 1))
        elif op is sre_c.CATEGORY:
            chars.extend({sre_c.CATEGORY_DIGIT: string.digits, sre_c.CATEGORY_WORD: ANY + "_",
                          sre_c.CATEGORY_SPACE: " "}.get(av, ""))
    if negate:
        pool = [c for c in ANY if c not in chars]
        return rng.choice(pool or ANY)
    printable = [c for c in chars if c.isprintable() and c not in "\n\r"]
    return rng.choice(printable or chars or ANY)


def tags_in(text):
    return re.findall(r"\[secret:([a-z0-9_.-]+):[0-9a-f]{8}\]", text)


def rule_samples(det, rng, tries=400):
    """{rule_id: sample or None} for every regex rule."""
    out = {}
    for rule in det.rules:
        sample = None
        try:
            tree = sre_parse.parse(rule.rx.pattern, rule.rx.flags)
        except Exception:
            out[rule.id] = None
            continue
        kws = [k.lower() for k in (rule.keywords or ())]
        for _ in range(tries):
            cand = _gen(tree, rng)
            if rule.rx.search(cand) and (not kws or any(k in cand.lower() for k in kws)):
                sample = cand
                break
        out[rule.id] = sample
    return out


def why_missed(rule, sample):
    m = rule.rx.search(sample)
    if not m:
        return "no-match"
    try:
        secret = m.group(rule.group) if rule.group else (m.group(1) if m.re.groups else m.group(0))
    except IndexError:
        secret = m.group(0)
    reasons = []
    if rule.keywords and not any(k.lower() in sample.lower() for k in rule.keywords):
        reasons.append("keyword-prefilter")
    if rule.entropy and of_mask.shannon(secret or "") < rule.entropy:
        reasons.append(f"entropy<{rule.entropy}")
    for al in rule.allow or ():
        try:
            if al.allows(secret, m, sample):
                reasons.append("allowlist")
                break
        except Exception:
            pass
    return "+".join(reasons) or "unexplained"


def hand_canaries(rng):
    hx = lambda n: "".join(rng.choice("0123456789abcdef") for _ in range(n))
    al = lambda n: "".join(rng.choice(ANY) for _ in range(n))
    words = ["abandon", "ability", "able", "about", "above", "absent", "absorb", "abstract", "absurd", "abuse",
             "access", "accident", "account", "accuse", "achieve", "acid", "acoustic", "acquire", "across", "act",
             "action", "actor", "actress", "actual"]
    rng.shuffle(words)
    _pk = "PRIVATE" + " KEY"  # split in source so the release gitleaks check (A4) does not flag this synthetic fixture
    pem = f"-----BEGIN OPENSSH {_pk}-----\n" + "\n".join(al(70) for _ in range(6)) + f"\n-----END OPENSSH {_pk}-----"
    return [
        # (shape, expected claimed?, text)
        ("pem-openssh-in-prompt", True, "here is the deploy key:\n" + pem),
        ("evm-0x-next-to-keyword", True, f"PRIVATE_KEY=0x{hx(64)}"),
        ("evm-no0x-next-to-keyword", True, f"signer private key: {hx(64)}"),
        ("evm-uppercase-in-json", True, json.dumps({"wallet_pk": "0x" + hx(64).upper()})),
        ("url-password", True, f"psql postgres://admin:{al(16)}@db.internal:5432/app"),
        ("curl-user-password", True, f"curl -u deploy:{al(14)} https://ci.example.invalid/api"),
        ("curl-auth-header", True, f"curl -H 'Authorization: Bearer {al(40)}' https://api.example.invalid"),
        ("aws-access-key", True, "aws_access_key_id = AKIA" + "".join(rng.choice(string.ascii_uppercase + "234567") for _ in range(16))),
        ("github-pat", True, "token ghp_" + al(36)),
        ("slack-bot-token", True, "SLACK=xoxb-" + "".join(rng.choice(string.digits) for _ in range(11)) + "-" + "".join(rng.choice(string.digits) for _ in range(12)) + "-" + al(24)),
        ("anthropic-key", True, "ANTHROPIC_API_KEY=sk-ant-api03-" + al(93) + "AA"),
        ("openai-key", True, "OPENAI_API_KEY=sk-proj-" + al(48) + "T3BlbkFJ" + al(48)),
        ("jwt", True, "Authorization: eyJ" + al(30) + ".eyJ" + al(60) + "." + al(43)),
        ("generic-api-key", True, f'api_key = "{al(32)}"'),
        ("seed-12-words", True, "recovery phrase: " + " ".join(words[:12])),
        ("seed-24-numbered", True, "\n".join(f"{i + 1}. {w}" for i, w in enumerate(words[:24]))),
        ("secret-in-cron-prompt", True, json.dumps({"session_crons": [{"prompt": f"check balance with key 0x{hx(64)}"}]})),
        ("secret-in-ci-log", True, f"[ci] export GITHUB_TOKEN=ghp_{al(36)}\n[ci] build ok"),
        # unclaimed by design (reported, not gated)
        ("UNCLAIMED-evm-bare-no-keyword", False, f"{hx(64)}"),
        ("UNCLAIMED-base64-encoded-key", False, "config: " + __import__("base64").b64encode(f"PRIVATE_KEY=0x{hx(64)}".encode()).decode()),
        ("UNCLAIMED-prose-password", False, f"the db password is {al(12)} for now"),
    ]


def lookalikes(rng):
    hx = lambda n: "".join(rng.choice("0123456789abcdef") for _ in range(n))
    return [
        ("tx-hash", f"tx 0x{hx(64)} confirmed in block 51234567"),
        ("tx-hash-json", json.dumps({"txHash": "0x" + hx(64), "status": 1})),
        ("block-hash", f"blockHash: 0x{hx(64)}"),
        ("evm-address", f"sent to 0x{hx(40)}"),
        ("git-sha", f"commit {hx(40)} (HEAD -> main)"),
        ("uuid", f"session {__import__('uuid').UUID(int=rng.getrandbits(128))}"),
        ("sha256-sum", f"sha256: {hx(64)}  release.tar.gz"),
        ("docker-digest", f"image@sha256:{hx(64)}"),
        ("keyHash-identifier", json.dumps({"keyHash": "0x" + hx(64)})),
        ("sha256-identifier", f'seeder_init_code_sha256 = "{hx(64)}"'),
        ("request-key-identifier", f"launch_request_key: 0x{hx(64)}"),
        ("tx-hash-identifier", f"tx_hash=0x{hx(64)}"),
        ("merkle-root", f"merkle_root: 0x{hx(64)}"),
        ("public-key-ed25519", f"public key {hx(64)}"),
        ("commit-id", f"commit_id={hx(40)}"),
        ("solana-signature", "sig " + "".join(rng.choice("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz") for _ in range(88))),
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    det = of_mask.Detector()
    key = bytes(rng.getrandbits(8) for _ in range(32))
    m = of_mask.Masker(key, det)

    per_rule = {"masked_own_kind": [], "masked_other_kind": {}, "missed": [], "no_sample": []}
    for rid, sample in rule_samples(det, rng).items():
        if sample is None:
            per_rule["no_sample"].append(rid)
            continue
        kinds = tags_in(m.text(sample))
        if rid in kinds:
            per_rule["masked_own_kind"].append(rid)
        elif kinds:
            per_rule["masked_other_kind"][rid] = sorted(set(kinds))
        else:
            rule = next(r for r in det.rules if r.id == rid)
            per_rule["missed"].append(f"{rid} ({why_missed(rule, sample)})")

    hand = []
    for shape, claimed, text in hand_canaries(rng):
        masked = m.json_text(text) if text.startswith("{") else m.text(text)
        kinds = tags_in(masked)
        # stability: the same canary in a second record gets the same tag
        again = m.json_text(text) if text.startswith("{") else m.text(text)
        hand.append({"shape": shape, "claimed": claimed, "masked": bool(kinds), "kinds": sorted(set(kinds)),
                     "stable_tag": re.findall(r"\[secret:[^\]]+\]", masked) == re.findall(r"\[secret:[^\]]+\]", again)})

    fp = []
    for cls, text in lookalikes(rng):
        masked = m.json_text(text) if text.startswith("{") else m.text(text)
        fp.append({"class": cls, "masked": bool(tags_in(masked)), "kinds": sorted(set(tags_in(masked)))})

    n_rules = len(per_rule["masked_own_kind"]) + len(per_rule["masked_other_kind"]) + len(per_rule["missed"])
    report = {
        "seed": a.seed, "rules_total": len(det.rules), "rules_with_sample": n_rules,
        "rules_masked_own_kind": len(per_rule["masked_own_kind"]),
        "rules_masked_other_kind": per_rule["masked_other_kind"],
        "rules_missed": per_rule["missed"], "rules_no_sample": per_rule["no_sample"],
        "hand_canaries": hand, "lookalikes": fp,
    }
    if a.json:
        print(json.dumps(report, indent=1))
        return
    print(f"rules: {n_rules}/{report['rules_total']} with a valid sample; "
          f"masked as own kind {report['rules_masked_own_kind']}, as another kind {len(per_rule['masked_other_kind'])}, "
          f"missed {len(per_rule['missed'])}, no sample {len(per_rule['no_sample'])}")
    print("missed rules:", ", ".join(per_rule["missed"]) or "none")
    print("masked as another kind:", json.dumps(per_rule["masked_other_kind"]))
    print("no sample generated:", ", ".join(per_rule["no_sample"]) or "none")
    print("hand canaries:")
    for h in hand:
        flag = "ok " if h["masked"] == h["claimed"] or (not h["claimed"]) else "MISS"
        print(f"  {flag} {h['shape']}: masked={h['masked']} kinds={h['kinds']} stable={h['stable_tag']}")
    print("look-alikes (false positive if masked):")
    for f in fp:
        print(f"  {'FP ' if f['masked'] else 'ok '} {f['class']}: kinds={f['kinds']}")


if __name__ == "__main__":
    sys.exit(main())
