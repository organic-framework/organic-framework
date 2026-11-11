"""of secrets: what leaked into harness transcripts, counted without revealing it (RFC 0002 input).

`summarize` reads gitleaks JSON reports made with --redact, re-reads each finding's span from
its source file, and keys it by HMAC under the tenant-only purpose key `of/secret/v1`. Values
are never printed or stored; the summary holds rule, HMAC prefix, counts, files, organizations.
"""

import collections
import hashlib
import hmac
import json
import os

HIGH_SIGNAL = {"private-key", "evm-private-key", "url-password", "aws-access-token", "github-pat", "github-fine-grained-pat", "github-oauth",
               "gitlab-pat", "slack-bot-token", "slack-user-token", "slack-webhook-url", "stripe-access-token",
               "anthropic-api-key", "anthropic-admin-api-key", "openai-api-key", "gcp-api-key", "jwt",
               "curl-auth-header", "curl-auth-user", "hashicorp-tf-api-token", "npm-access-token",
               "telegram-bot-api-token", "discord-api-token", "sendgrid-api-token", "cloudflare-api-key"}


def _span(lines_cache, path, line, c0, c1):
    if path not in lines_cache:
        try:
            with open(path, "rb") as fh:
                lines_cache.clear()  # keep one file in memory at a time
                lines_cache[path] = fh.read().split(b"\n")
        except OSError:
            lines_cache[path] = []
    lines = lines_cache[path]
    if not 1 <= line <= len(lines):
        return None
    return lines[line - 1][max(c0 - 1, 0):c1]


def summarize(reports, key, router=None, own_sessions=(), out=print, min_entropy=4.0):
    findings = []
    for r in reports:
        with open(r) as fh:
            findings += json.load(fh)
    findings.sort(key=lambda f: (f["File"], f["StartLine"]))
    uniq = {}
    cache = {}
    skipped = 0
    for f in findings:
        if any(s in f["File"] for s in own_sessions):  # this session's test fixtures (fake keys)
            skipped += 1
            continue
        span = _span(cache, f["File"], f["StartLine"], f["StartColumn"], f["EndColumn"])
        if span is None:
            continue
        tag = hmac.new(key, span, hashlib.sha256).hexdigest()[:16]
        k = (f["RuleID"], tag)
        u = uniq.setdefault(k, {"rule": f["RuleID"], "hmac": tag, "count": 0, "files": set(), "orgs": set(),
                                "entropy": f.get("Entropy") or 0, "first_file": f["File"]})
        u["count"] += 1
        u["files"].add(f["File"])
        if router:
            u["orgs"].add(router.org_of(None, None, None, f["File"]) or "unassigned")
    by_rule = collections.defaultdict(lambda: {"findings": 0, "unique": 0, "unique_high_entropy": 0,
                                               "orgs": collections.Counter()})
    for u in uniq.values():
        b = by_rule[u["rule"]]
        b["findings"] += u["count"]
        b["unique"] += 1
        b["unique_high_entropy"] += u["entropy"] >= min_entropy
        for o in u["orgs"]:
            b["orgs"][o] += 1
    out(f"{len(findings)} findings in {len(reports)} reports; {skipped} skipped as this session's test fixtures")
    out(f"{'rule':<26} {'findings':>9} {'unique':>7} {'≥%.1f bits' % min_entropy:>10}  organizations (unique)")
    for rule, b in sorted(by_rule.items(), key=lambda kv: (kv[0] not in HIGH_SIGNAL, -kv[1]["unique"])):
        orgs = ", ".join(f"{o} {n}" for o, n in b["orgs"].most_common())
        flag = "*" if rule in HIGH_SIGNAL else " "
        out(f"{flag}{rule:<25} {b['findings']:>9} {b['unique']:>7} {b['unique_high_entropy']:>10}  {orgs}")
    out("* = high-signal rule: review each for rotation")
    return [dict(u, files=len(u["files"]), orgs=sorted(u["orgs"])) for u in uniq.values()]


def write_summary(items, path):
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(sorted(items, key=lambda u: (u["rule"] not in HIGH_SIGNAL, u["rule"], -u["count"])), fh, indent=1)


# ------------------------------------------------------------ contextual scan

def scan_contextual(files, out_path, skip=()):
    """Second pass for what gitleaks has no rule for: bare EVM private keys next to key words,
    and passwords in URLs (scheme://user:pass@host). Writes gitleaks-format findings, redacted."""
    import of_hook
    rules = {"hex_key": "evm-private-key", "url_password": "url-password"}
    rx = {k: r for k, r in of_hook._MASKS if k in rules}
    findings = []
    for f in files:
        if any(s in f for s in skip):
            continue
        try:
            with open(f, "rb") as fh:
                lines = fh.read().split(b"\n")
        except OSError:
            continue
        for n, raw in enumerate(lines, 1):
            line = raw.decode("utf-8", "replace")
            for kind, r in rx.items():
                for m in r.finditer(line):
                    s, e = m.span()
                    if kind == "hex_key" and m.groups():
                        s = m.end(1)  # the value only, not the key word before it
                    findings.append({"RuleID": rules[kind], "File": f, "StartLine": n, "EndLine": n,
                                     "StartColumn": len(line[:s].encode()) + 1, "EndColumn": len(line[:e].encode()),
                                     "Match": "REDACTED", "Secret": "REDACTED", "Entropy": 4.0})
    with open(os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(findings, fh)
    return len(findings)


# ------------------------------------------------------------------ review

ORDER = ("private-key", "evm-private-key", "aws-access-token", "curl-auth-user", "curl-auth-header", "url-password")


def _private_kind(span, context):
    head = span[:80].decode("utf-8", "replace").upper()
    ctx = context.lower()
    if b"OPENSSH PRIVATE KEY" in span[:60] or any(w in ctx for w in ("id_rsa", "id_ed25519", "ssh-keygen", ".ssh/")):
        return "ssh"
    if "PRIVATE KEY" in head:
        return "tls-pem"
    return "other"


def _record_ts(line_bytes):
    try:
        return json.loads(line_bytes).get("timestamp")
    except Exception:
        return None


def build_review(reports, key, router=None, skip=(), width=60, min_entropy=4.0):
    """Unique secrets with locations, dates, organizations and masked context. No values."""
    import of_hook
    findings = []
    for r in reports:
        with open(r) as fh:
            findings += json.load(fh)
    items, cache = {}, {}
    for f in sorted(findings, key=lambda f: (f["File"], f["StartLine"])):
        if any(s in f["File"] for s in skip):
            continue
        if f["RuleID"] == "generic-api-key" and (f.get("Entropy") or 0) < min_entropy:
            continue
        span = _span(cache, f["File"], f["StartLine"], f["StartColumn"], f["EndColumn"])
        if not span:
            continue
        line = cache[f["File"]][f["StartLine"] - 1]
        tag = hmac.new(key, span, hashlib.sha256).hexdigest()[:16]
        k = (f["RuleID"], tag)
        it = items.get(k)
        if it is None:
            c0, c1 = max(f["StartColumn"] - 1 - width, 0), min(f["EndColumn"] + width, len(line))
            before = line[c0:f["StartColumn"] - 1].decode("utf-8", "replace")
            after = line[f["EndColumn"]:c1].decode("utf-8", "replace")
            context = of_hook.mask(before) + f"[secret:{f['RuleID']}:{tag[:8]}]" + of_hook.mask(after)
            context = context.replace(span.decode("utf-8", "replace"), f"[secret:{tag[:8]}]")
            kind = f["RuleID"]
            if kind == "private-key":
                kind = "private-key/" + _private_kind(span, before + after)
            elif kind == "evm-private-key":
                kind = "private-key/evm-wallet"
            it = items[k] = {"rule": f["RuleID"], "kind": kind, "hmac": tag, "context": context, "locations": [],
                             "first": None, "last": None, "orgs": set()}
        it["locations"].append(f"{f['File']}:{f['StartLine']}")
        ts = _record_ts(line)
        if ts:
            it["first"] = min(filter(None, (it["first"], ts)))
            it["last"] = max(filter(None, (it["last"], ts)))
        if router:
            it["orgs"].add(router.org_of(None, None, None, f["File"]) or "unassigned")
    rank = {r: i for i, r in enumerate(ORDER)}
    out = [dict(v, orgs=sorted(v["orgs"])) for v in items.values()]
    out.sort(key=lambda v: (rank.get(v["rule"], len(ORDER)), v["first"] or ""))
    return out


DECISIONS = ("rotate", "false_positive", "rotated")


def reviewed(conn):
    """{hmac: decision} from of.secret_reviewed events (the latest mark wins)."""
    out = {}
    for (payload,) in conn.execute("SELECT payload FROM event WHERE name = 'of.secret_reviewed' ORDER BY ts, rowid"):
        p = json.loads(payload or "{}")
        out[p.get("hmac")] = p.get("decision")
    return out


# --------------------------------------------------------------- rotations

PRIORITY = {"private-key/evm-wallet": 1, "aws-access-token": 1, "private-key/ssh": 1, "private-key/tls-pem": 1,
            "private-key/other": 1, "curl-auth-user": 2, "curl-auth-header": 2, "url-password": 2}
CLASS = {"private-key/evm-wallet": "wallet", "aws-access-token": "cloud (AWS)", "private-key/ssh": "ssh key",
         "private-key/tls-pem": "tls key", "private-key/other": "private key", "curl-auth-user": "service password",
         "curl-auth-header": "service token", "url-password": "service password", "generic-api-key": "api key"}


def repo_hint(transcript_path, org_roots):
    """The repository a transcript worked in, from Claude Code's project directory name."""
    project = transcript_path.split("/projects/")[-1].split("/")[0] if "/projects/" in transcript_path else ""
    best = None
    for root in org_roots:
        root = os.path.realpath(os.path.expanduser(root))
        try:
            names = os.listdir(root)
        except OSError:
            continue
        for name in names:
            enc = "".join(c if c.isalnum() else "-" for c in os.path.join(root, name))
            if (project == enc or project.startswith(enc + "-")) and (best is None or len(name) > len(best)):
                best = name
    return best


def rotations(review_items, marks, org, org_roots, systems):
    """Rows for items the owner marked `rotate`, for one organization: no value, no context."""
    rows = []
    for it in review_items:
        if marks.get(it["hmac"]) != "rotate" or org not in it["orgs"]:
            continue
        repos = sorted({r for r in (repo_hint(loc.rsplit(":", 1)[0], org_roots) for loc in it["locations"]) if r})
        owners = sorted({sid for sid, s in (systems or {}).items() if isinstance(s, dict)
                         and set(s.get("repos") or []) & set(repos)})
        rows.append({"ref": it["hmac"][:8], "priority": PRIORITY.get(it["kind"], 3),
                     "class": CLASS.get(it["kind"], it["kind"]), "systems": owners, "repos": repos,
                     "seen": len(it["locations"]), "first": (it["first"] or "")[:10], "last": (it["last"] or "")[:10]})
    rows.sort(key=lambda r: (r["priority"], r["first"]))
    return rows
