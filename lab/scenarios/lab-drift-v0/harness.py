#!/usr/bin/env python3
"""lab-drift-v0: score of_studio's anti-pattern detectors on synthetic labelled drift with negatives.

For each scenario (a registry doc, a load_events-shape event list, and labels), build the model
(of_model.build), run the detectors (of_diagnose.diagnose) over [a, b), match each FIRED issue to a label by
rule id + sorted affected set, and accumulate per rule: TP (a positive label fired), FP (a fired issue matching
a negative label, or matching no label), FN (a positive label that did not fire). Report precision and recall
per rule; a rule BLOCKED for lack of inputs in a scenario is reported apart, never as a miss.

Run: python3 lab/scenarios/lab-drift-v0/harness.py
"""
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "bin"))
import of_model  # noqa: E402
import of_diagnose  # noqa: E402

B_ISO = "2026-11-08T00:00Z"
A_ISO = "2026-11-01T00:00Z"


def epoch(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def ev(i, ts, name, sid, payload=None, position=None, cwd="~/acme", source="hook"):
    return {"event_id": f"e{i}", "ts": ts, "name": name, "session_id": sid, "position": position,
            "payload": payload or {}, "cwd": cwd, "source": source}


def key(rule, affected):
    """A fired issue / a label, as (bare rule id, sorted affected tuple)."""
    return (rule, tuple(sorted(affected or [])))


def run_scenario(sc):
    doc, events, labels = sc["doc"], sc["events"], sc["labels"]
    a, b = epoch(sc.get("a", A_ISO)), epoch(sc.get("b", B_ISO))
    model = of_model.build(doc, events, at=sc.get("b", B_ISO), org=doc.get("organization"))
    res = of_diagnose.diagnose(model, events, a, b, context=sc.get("context"))
    fired = {key(it.get("rule"), it.get("affected")) for it in res.get("issues", [])}
    status = {r.get("rule"): r.get("status") for r in res.get("rules", [])}
    return fired, status, res


def score(scenarios):
    # per rule: tp, fp, fn; blocked_in (a positive/negative scenario where the rule was blocked for lack of inputs,
    # scored apart); blocked_ok / blocked_miss (a scenario that LABELS the rule "blocked" — the right outcome for a
    # rule whose inputs do not exist yet, e.g. context-overload/missing-verifier without of.correction: ok when the
    # rule reports blocked, miss when it ran or fired instead).
    agg = {}
    def slot(rule):
        return agg.setdefault(rule, {"tp": 0, "fp": 0, "fn": 0, "blocked_in": [], "blocked_ok": 0, "blocked_miss": 0})
    for sc in scenarios:
        fired, status, _ = run_scenario(sc)
        labelled = set()
        for lab in sc["labels"]:
            k = key(lab["rule"], lab["affected"])
            labelled.add(k)
            s = slot(lab["rule"])
            if lab["label"] == "blocked":   # blocked is the expected, correct outcome for this rule here
                s["blocked_ok" if status.get(lab["rule"]) == "blocked" else "blocked_miss"] += 1
                continue
            if status.get(lab["rule"]) == "blocked":
                s["blocked_in"].append(sc["name"])
                continue
            if lab["label"] == "positive":
                s["tp" if k in fired else "fn"] += 1
            else:  # negative: must stay quiet
                if k in fired:
                    s["fp"] += 1
        # a fired issue matching no label in this scenario is an unexpected fire -> FP
        for k in fired:
            if k not in labelled:
                slot(k[0])["fp"] += 1
    return agg


def report(agg):
    print(f"{'rule':24} {'P':>6} {'R':>6}  tp fp fn  blk  blocked_in")
    out = {}
    for rule, s in sorted(agg.items()):
        tp, fp, fn = s["tp"], s["fp"], s["fn"]
        p = tp / (tp + fp) if (tp + fp) else None
        r = tp / (tp + fn) if (tp + fn) else None
        ps = f"{p:.2f}" if p is not None else "  -"
        rs = f"{r:.2f}" if r is not None else "  -"
        blk = f"{s['blocked_ok']}/{s['blocked_ok'] + s['blocked_miss']}" if (s["blocked_ok"] or s["blocked_miss"]) else "-"
        print(f"{rule:24} {ps:>6} {rs:>6}  {tp:2} {fp:2} {fn:2}  {blk:>4}  {','.join(s['blocked_in']) or '-'}")
        out[rule] = {"precision": p, "recall": r, "tp": tp, "fp": fp, "fn": fn, "blocked_in": s["blocked_in"],
                     "blocked_ok": s["blocked_ok"], "blocked_miss": s["blocked_miss"]}
    return out


if __name__ == "__main__":
    from fixtures import SCENARIOS
    agg = score(SCENARIOS)
    out = report(agg)
    print(json.dumps({"rules": out}, indent=1))
