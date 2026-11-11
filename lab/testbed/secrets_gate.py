#!/usr/bin/env python3
"""lab-secrets-v0, the recorder-to-server part (docs/lab.md, section 14).

make:  write a stub-harness script that carries seeded canaries through prompts,
       assistant replies, a cron prompt and peer-style text, plus the manifest.
check: scan every SQLite database and file under the given roots for the canaries
       (canonical forms too), classify each location as tenant-only or
       organization-visible, and count findings in the vault. It prints shapes and
       counts only, never a value.

Usage:
  secrets_gate.py make  --seed 42 --out DIR
  secrets_gate.py check --manifest DIR/manifest.json --org-root PATH... --tenant-root PATH...
"""

import argparse
import base64
import gzip
import json
import os
import random
import re
import sqlite3
import string
import sys

try:
    import compression.zstd as zstd
except ImportError:
    zstd = None

ANY = string.ascii_letters + string.digits
WORDS = ["abandon", "ability", "able", "about", "above", "absent", "absorb", "abstract", "absurd", "abuse", "access",
         "accident", "account", "accuse", "achieve", "acid", "acoustic", "acquire", "across", "act", "action", "actor",
         "actress", "actual"]


def canaries(rng):
    hx = lambda n: "".join(rng.choice("0123456789abcdef") for _ in range(n))
    al = lambda n: "".join(rng.choice(ANY) for _ in range(n))
    w = WORDS[:]
    rng.shuffle(w)
    pem_body = [al(64) for _ in range(5)]
    evm1, evm2, evm3, evm4 = hx(64), hx(64), hx(64), hx(64)
    c = [
        # shape, claimed, where, text, canonical needles
        ("pem-private-key", True, "prompt", "deploy key:\\n-----BEGIN OPENSSH PRIVATE KEY-----\\n" + "\\n".join(pem_body) + "\\n-----END OPENSSH PRIVATE KEY-----", [pem_body[0], pem_body[2]]),
        ("evm-key-word-0x", True, "reply", f"Set PRIVATE_KEY=0x{evm1} in the env.", [evm1]),
        ("evm-json-pk-upper", True, "prompt", '{"wallet_pk": "0X' + evm2.upper() + '"}', [evm2]),
        ("evm-in-cron", True, "cron", f"check balance with key 0x{evm3}", [evm3]),
        ("url-password", True, "reply", f"psql postgres://admin:{(p1 := al(16))}@db.internal/app", [p1]),
        ("curl-user", True, "prompt", f"curl -u deploy:{(p2 := al(14))} https://ci.example.invalid", [p2]),
        ("auth-header", True, "reply", f"curl -H 'Authorization: Bearer {(p3 := al(40))}' https://api.example.invalid", [p3]),
        ("aws-key", True, "prompt", f"aws_access_key_id = {(p4 := 'AKIA' + ''.join(rng.choice(string.ascii_uppercase + '234567') for _ in range(16)))}", [p4]),
        ("github-pat", True, "reply", f"export GITHUB_TOKEN={(p5 := 'ghp_' + al(36))}", [p5]),
        ("anthropic-key", True, "prompt", f"ANTHROPIC_API_KEY={(p6 := 'sk-ant-api03-' + al(93) + 'AA')}", [p6]),
        ("slack-bot", True, "reply", f"SLACK={(p7 := 'xoxb-' + ''.join(rng.choice(string.digits) for _ in range(11)) + '-' + ''.join(rng.choice(string.digits) for _ in range(12)) + '-' + al(24))}", [p7]),
        ("jwt", True, "prompt", f"token {(p8 := 'eyJ' + al(30) + '.eyJ' + al(60) + '.' + al(43))}", [p8]),
        ("generic-api-key", True, "reply", f'api_key = "{(p9 := al(32))}"', [p9]),
        ("seed-12", True, "prompt", "recovery phrase: " + " ".join(w[:12]), [" ".join(w[:12])]),
        ("seed-24-numbered", True, "reply", "\\n".join(f"{i + 1}. {x}" for i, x in enumerate(w[:24])), [" ".join(w[12:24])]),
        # unclaimed by design: reported, never gating
        ("UNCLAIMED-evm-bare", False, "reply", evm4, [evm4]),
        ("UNCLAIMED-base64-key", False, "prompt", "cfg " + base64.b64encode(f"PRIVATE_KEY=0x{hx(64)}".encode()).decode(), []),
        ("UNCLAIMED-prose-password", False, "prompt", f"the db password is {(p10 := al(12))} for now", [p10]),
    ]
    return c


def make(a):
    rng = random.Random(a.seed)
    os.makedirs(a.out, exist_ok=True)
    cs = canaries(rng)
    script = []
    for shape, claimed, where, text, needles in cs:
        if where == "prompt":
            script.append(f"Please remember this: {text}")
        elif where == "reply":
            script.append(f"!reply Noted. {text}")
            script.append(f"What should I set for {shape}?")
        elif where == "cron":
            script.append(f"!cron {text}")
    script.append("/exit")
    with open(os.path.join(a.out, "script.txt"), "w") as fh:
        fh.write("\n".join(script) + "\n")
    with open(os.path.join(a.out, "manifest.json"), "w") as fh:
        json.dump([{"shape": s, "claimed": c, "where": w, "needles": n} for s, c, w, _, n in cs], fh)
    print(f"{len(cs)} canaries, {len(script)} script lines -> {a.out}")


def _decode(v):
    if isinstance(v, (bytes, bytearray)):
        b = bytes(v)
        for f in ((lambda x: zstd.decompress(x)) if zstd else None, gzip.decompress):
            if f:
                try:
                    b = f(b)
                    break
                except Exception:
                    pass
        return b.decode("utf-8", "ignore")
    return v if isinstance(v, str) else ""


def texts_of(path):
    """Yield (location, text) for every SQLite text/blob cell, or the file's text."""
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        tables = [r[0] for r in c.execute("select name from sqlite_master where type='table'")]
        for t in tables:
            try:
                for row in c.execute(f'select * from "{t}"'):
                    for v in row:
                        s = _decode(v)
                        if s:
                            yield f"{path}:{t}", s
            except sqlite3.DatabaseError:
                continue
        c.close()
        return
    except sqlite3.DatabaseError:
        pass
    try:
        with open(path, "rb") as fh:
            yield path, _decode(fh.read())
    except OSError:
        return


def files_under(roots):
    for r in roots:
        r = os.path.expanduser(r)
        if os.path.isfile(r):
            yield r
            continue
        for d, _, fs in os.walk(r):
            for f in fs:
                if not f.endswith(("-shm", ".lock")):
                    yield os.path.join(d, f)


def norm(s):
    s = s.replace("\\n", " ").replace("\n", " ")
    s = re.sub(r"\b\d+\.\s+", " ", s)
    return re.sub(r"\s+", " ", s).lower()


def check(a):
    man = json.load(open(a.manifest))
    result = {"org_visible": {}, "tenant_only": {}}
    for scope, roots in (("org_visible", a.org_root), ("tenant_only", a.tenant_root)):
        hits = {m["shape"]: 0 for m in man}
        nfiles = 0
        for f in files_under(roots or []):
            nfiles += 1
            for _, text in texts_of(f):
                low = norm(text)
                for m in man:
                    for n in m["needles"]:
                        if n.lower() in low:
                            hits[m["shape"]] += 1
                            break
        result[scope] = {"files": nfiles, "hits": hits}
    claimed_leaks = {s: n for s, n in result["org_visible"]["hits"].items()
                     if n and any(m["shape"] == s and m["claimed"] for m in man)}
    unclaimed = {s: n for s, n in result["org_visible"]["hits"].items()
                 if any(m["shape"] == s and not m["claimed"] for m in man)}
    tenant_seen = sum(1 for n in result["tenant_only"]["hits"].values() if n)
    report = {"claimed_canaries_in_org_stores": claimed_leaks, "unclaimed_in_org_stores": unclaimed,
              "canary_shapes_seen_in_tenant_stores": f"{tenant_seen}/{len(man)}",
              "org_files_scanned": result["org_visible"]["files"],
              "tenant_files_scanned": result["tenant_only"]["files"],
              "gate": "PASS" if not claimed_leaks else "FAIL"}
    print(json.dumps(report, indent=1))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("--seed", type=int, default=42)
    m.add_argument("--out", required=True)
    c = sub.add_parser("check")
    c.add_argument("--manifest", required=True)
    c.add_argument("--org-root", nargs="*")
    c.add_argument("--tenant-root", nargs="*")
    a = ap.parse_args()
    return make(a) if a.cmd == "make" else check(a)


if __name__ == "__main__":
    sys.exit(main())
