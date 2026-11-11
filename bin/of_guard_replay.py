"""Replay the guard over recorded Bash commands (docs/guard.md, "How a rule changes").

A guard rule gets a replay of at least 21 days before it denies. This reads every Bash command
the local store recorded in the window (assistant tool_use blocks in transcript_record),
deduplicates them, runs `of_guard.critical_deny` on each and writes one decision per command
hash to a file. Comparing two such files (before and after a rule change) gives the new and the
removed denies.

Commands are never printed or written: the decision file holds the command's SHA-256 and the
deny reason; `--compare` shows a changed decision with its reason and the command's first word,
masked. Usage:

    of_guard_replay.py run --days 21 --out before.json
    (change the rule)
    of_guard_replay.py run --days 21 --out after.json
    of_guard_replay.py compare before.json after.json

An organization's own rules (docs/guard.md, O) are replayed per position and day over that organization's sessions:

    of_guard_replay.py org --org acme --rules grants.yaml [--days 21] [--since 2026-11-06T14:37]
"""

import argparse
import collections
import datetime
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def commands(days, db=None):
    """{sha256: command} for every Bash tool_use recorded in the last `days` days."""
    import of_store
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    conn = of_store.connect(db)
    out = {}
    rows = conn.execute("SELECT blob_sha256 FROM transcript_record WHERE tool_names LIKE '%Bash%' AND timestamp >= ?",
                        (since,)).fetchall()
    for (sha,) in rows:
        raw = of_store.get_blob(conn, sha)
        try:
            rec = json.loads(raw or b"{}")
        except ValueError:
            continue
        content = ((rec.get("message") or {}).get("content")) if isinstance(rec, dict) else None
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash":
                cmd = (block.get("input") or {}).get("command")
                if isinstance(cmd, str) and cmd:
                    out[hashlib.sha256(cmd.encode()).hexdigest()] = cmd
    return out


def session_commands(days, db=None, org=None, since=None):
    """[(position, day, command)] for every Bash tool_use recorded in the window, once per (session, time, command).
    A session's position and organization are the ones its events name most often; `org` keeps only its sessions."""
    import of_store
    since = since or (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%S")
    conn = of_store.connect(db)
    who = {}
    for sid, pos, o, _n in conn.execute("SELECT session_id, position, org, count(*) FROM event WHERE position IS NOT "
                                        "NULL AND ts >= ? GROUP BY 1, 2, 3 ORDER BY 4", (since,)):
        who[sid] = (pos, o)  # ordered by count: the most frequent wins
    out, seen = [], set()
    for sid, sha, ts in conn.execute("SELECT session_id, blob_sha256, timestamp FROM transcript_record WHERE tool_names "
                                     "LIKE '%Bash%' AND timestamp >= ?", (since,)):
        pos, o = who.get(sid, (None, None))
        if org and o not in (org, None):
            continue
        try:
            rec = json.loads(of_store.get_blob(conn, sha) or b"{}")
        except ValueError:
            continue
        content = ((rec.get("message") or {}).get("content")) if isinstance(rec, dict) else None
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Bash":
                cmd = (block.get("input") or {}).get("command")
                key = (sid, ts, hashlib.sha256((cmd or "").encode()).hexdigest())
                if isinstance(cmd, str) and cmd and key not in seen:
                    seen.add(key)
                    out.append((pos or "(no position)", (ts or "")[:10], cmd))
    return out


def org_replay(rules, rows):
    """{rule id: {"positions": Counter, "days": Counter, "excepted": n, "in_variable": n}} for an organization's rules
    over (position, day, command) rows. `in_variable`: a call of the rule's verbs whose target pod is held in a
    variable or a substitution, which a pods matcher cannot see."""
    import of_guard
    out = {r["id"]: {"positions": collections.Counter(), "days": collections.Counter(), "excepted": 0,
                     "in_variable": 0} for r in rules}
    for pos, day, cmd in rows:
        if "kubectl" not in cmd:
            continue
        for r in rules:
            if r.get("subject") not in (None, "*") and r["subject"] != pos:
                continue
            hit = of_guard.org_rule_hits("Bash", {"command": cmd}, [r], None)
            if hit and pos in (r.get("except") or []):
                out[r["id"]]["excepted"] += 1
            elif hit:
                out[r["id"]]["positions"][pos] += 1
                out[r["id"]]["days"][day] += 1
            elif (r.get("kubectl") or {}).get("pods") is not None:
                verbs = r["kubectl"].get("verbs")
                try:
                    calls = of_guard.kubectl_calls(cmd)
                except Exception:  # noqa: BLE001
                    calls = []
                if any((verbs is None or c["verb"] in verbs) and any("$" in t or "__OFSUB__" in t for t in c["targets"])
                       for c in calls):
                    out[r["id"]]["in_variable"] += 1
    return out


def org_breakdown(rules, rows):
    """{(rule id, position, namespace, verb, kind): calls} for the kubectl calls each rule matched: what a narrow
    Role per position would have to allow (asked for a k8s_keeper, 2026-11-09). Namespace `(all)` is -A, `(default)`
    the kubeconfig's own, `(variable)` one held in a variable; kind `unparsed` when the call names none readably.
    Counts only: no command text and no object names."""
    import of_guard
    out = collections.Counter()
    for pos, _day, cmd in rows:
        if "kubectl" not in cmd:
            continue
        try:
            calls = of_guard.kubectl_calls(cmd)
        except Exception:  # noqa: BLE001
            continue
        for r in rules:
            if pos in (r.get("except") or []) or (r.get("subject") not in (None, "*") and r["subject"] != pos):
                continue
            for c in calls:
                if not of_guard.kubectl_rule_match(r["kubectl"], c):
                    continue
                ns = "(all)" if c["all_namespaces"] else c["namespace"] or "(default)"
                if "$" in ns or "__OFSUB__" in ns:
                    ns = "(variable)"
                for kind in c["kinds"] or ["unparsed"]:
                    out[(r["id"], pos, ns, c["verb_label"] or "unparsed", kind)] += 1
    return out


def decide(cmds):
    """{sha256: reason or None} under the guard as it is on disk now."""
    import of_guard
    out = {}
    for h, cmd in cmds.items():
        try:
            hit = of_guard.critical_deny("Bash", {"command": cmd})
        except Exception as e:  # noqa: BLE001 - a crash is a finding, not a stop
            hit = (f"guard error: {type(e).__name__}", None)
        out[h] = hit[0] if hit else None
    return out


def _first_word(cmd):
    import of_hook
    w = (cmd.strip().split() or [""])[0]
    return of_hook.mask(w)[:40]


def compare(before, after, cmds=None):
    """(new denies, removed denies, changed reasons) as lists of (sha, old reason, new reason)."""
    new, gone, changed = [], [], []
    for h in sorted(set(before) | set(after)):
        a, b = before.get(h), after.get(h)
        if a == b or h not in before or h not in after:
            continue
        (new if a is None else gone if b is None else changed).append((h, a, b))
    return new, gone, changed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--days", type=int, default=21)
    r.add_argument("--out", required=True)
    r.add_argument("--db")
    o = sub.add_parser("org")
    o.add_argument("--org", required=True)
    o.add_argument("--rules", required=True, help="a YAML file with `grants:` (deny grants, issued or proposed; "
                   "signatures are not checked here) or `rules:` (docs/guard.md, O)")
    o.add_argument("--days", type=int, default=21)
    o.add_argument("--since", help="an ISO time instead of --days")
    o.add_argument("--db")
    o.add_argument("--breakdown", help="also write a TSV of matched calls by rule, position, namespace, verb and kind "
                   "(mode 600; counts only)")
    c = sub.add_parser("compare")
    c.add_argument("before")
    c.add_argument("after")
    c.add_argument("--days", type=int, default=21, help="re-read the commands to show their first word")
    a = ap.parse_args(argv)
    if a.cmd == "org":
        import yaml
        import of_grants
        with open(a.rules) as fh:
            doc = yaml.safe_load(fh) or {}
        rules = doc.get("rules") or [of_grants.org_rule(g) for g in doc.get("grants") or []  # proposed or issued
                                     if g.get("effect") == "deny" and isinstance((g.get("scope") or {}).get("kubectl"), dict)
                                     and not g.get("revoked")]
        rows = session_commands(a.days, a.db, a.org, a.since)
        res = org_replay(rules, rows)
        print(f"{len(rows)} Bash calls in {a.org} sessions since {a.since or str(a.days) + ' days ago'}")
        for r in rules:
            x = res[r["id"]]
            n = sum(x["positions"].values())
            print(f"rule {r['id']} ({r.get('mode', 'warn')}): {n} calls by {len(x['positions'])} positions; "
                  f"{x['excepted']} by excepted positions; {x['in_variable']} with the pod in a variable (unseen)")
            print("  per day: " + ", ".join(f"{d} {k}" for d, k in sorted(x["days"].items())))
            for pos, k in x["positions"].most_common(20):
                print(f"  {k:6d}  {pos}")
        if a.breakdown:
            table = org_breakdown(rules, rows)
            with open(os.open(a.breakdown, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
                fh.write(f"# of_guard_replay.py org --org {a.org} --since {a.since or str(a.days) + ' days ago'}: kubectl "
                         "calls each rule matched, counts only (docs/guard.md, O)\n")
                fh.write("rule\tposition\tnamespace\tverb\tkind\tcalls\n")
                for cells, n in sorted(table.items(), key=lambda kv: (kv[0][0], kv[0][1], -kv[1], kv[0][2:])):
                    fh.write("\t".join(cells) + f"\t{n}\n")
            print(f"breakdown: {len(table)} rows, {sum(table.values())} calls -> {a.breakdown}")
        return
    if a.cmd == "run":
        cmds = commands(a.days, a.db)
        dec = decide(cmds)
        with open(os.open(a.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(dec, fh)
        reasons = collections.Counter(v.split(" (")[0] for v in dec.values() if v)
        print(f"{len(dec)} distinct commands over {a.days} days; {sum(reasons.values())} denied")
        for k, n in reasons.most_common():
            print(f"  {n:6d}  {k}")
        return
    with open(a.before) as fh:
        before = json.load(fh)
    with open(a.after) as fh:
        after = json.load(fh)
    new, gone, changed = compare(before, after)
    cmds = commands(a.days) if (new or gone) else {}
    print(f"new denies {len(new)}, removed denies {len(gone)}, changed reasons {len(changed)}")
    for title, rows in (("new", new), ("removed", gone), ("changed", changed)):
        for h, old, newr in rows:
            word = _first_word(cmds[h]) if h in cmds else "?"
            print(f"  {title}: {h[:12]} {word!r}: {old!r} -> {newr!r}")


if __name__ == "__main__":
    main()
