#!/usr/bin/env python3
"""lab-fit-v0: score the mandate-fit judge (bin/of_fit.py) on labelled dispatches.

Two sources, scored apart (as lab-drift-v0 does):
- synthetic: cases.json on the fictional roster (roster.json), VM-free and CI-safe; the judge is called for real
  (Haiku, about $0.002 a case), so it is a paid run, not a unit test;
- real: real_cases.json names messages in this machine's tenant store by event id, with the label a person gave;
  it reads the real store and runs on the Mac only (docs/lab.md, Level 2). Only scores are printed.

A flag is other_position or no_position on a message that hands out work (of_fit.flagged). Precision and recall
are of the flag; owner accuracy is over flagged other_position cases with a known owner.

    python3 lab/scenarios/lab-fit-v0/run.py [synthetic|real|both] [--jobs 6] [--out verdicts.jsonl]
"""

import argparse
import concurrent.futures
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def real_cases():
    """The real labelled cases live outside the public tree, under lab-private/ (release README §2). Load them when
    present; without them the scenario runs its synthetic cases only. Override the base with OF_LAB_PRIVATE."""
    base = os.environ.get("OF_LAB_PRIVATE") or os.path.expanduser("~/organic-framework-build/lab-private")
    p = os.path.join(base, "scenarios", "lab-fit-v0", "real_cases.json")
    return json.load(open(p)) if os.path.exists(p) else []
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "bin"))
import of_fit  # noqa: E402


def synthetic():
    roster = json.load(open(os.path.join(HERE, "roster.json")))
    positions = [dict(p, _orgid=roster["org"]) for p in roster["positions"]]
    cases = json.load(open(os.path.join(HERE, "cases.json")))
    return [(c, {"sender": c["sender"], "receiver": c["receiver"], "text": c["text"]}) for c in cases], positions


def real():
    import of_store
    sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "bin"))
    import importlib.machinery
    import importlib.util
    loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(HERE, "..", "..", "..", "bin", "of"))
    of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli", loader))
    loader.exec_module(of)
    positions = of._load_quiet()
    conn = of_store.connect()
    out = []
    for c in real_cases():
        row = conn.execute("SELECT ts, position FROM event WHERE event_id = ?", (c["event_id"],)).fetchone()
        if not row:
            print(f"  {c['id']}: event {c['event_id']} is not in this store", file=sys.stderr)
            continue
        d = next((x for x in of_fit.dispatches(conn, row[0], None, None, row[1], only={c["event_id"]})  # its receiver's, from its time
                  if x["event_id"] == c["event_id"]), None)
        if d:
            out.append((c, d))
    return out, positions


def score(pairs, positions, jobs=6, out=None, model=of_fit.MODEL, as_of=False):
    cache = {}
    with concurrent.futures.ThreadPoolExecutor(jobs) as ex:
        verdicts = list(ex.map(lambda p: of_fit.judge(p[1], positions, model=model, as_of=as_of, cache=cache), pairs))
    tp = fp = fn = tn = err = 0
    owner_ok = owner_n = route_ok = route_n = 0
    confusion, cost, misses = {}, 0.0, []
    for (c, d), v in zip(pairs, verdicts):
        cost += v.get("cost_usd") or 0
        if v.get("error"):
            err += 1
            continue
        want = c["label"] in ("other_position", "no_position")
        got = bool(of_fit.flagged(v))
        tp, fp, fn, tn = tp + (want and got), fp + (got and not want), fn + (want and not got), tn + (not want and not got)
        said = v["verdict"] if v.get("assigns_work") or v["verdict"] == "not_work" else "not_work"
        confusion.setdefault(c["label"], {}).setdefault(said, 0)
        confusion[c["label"]][said] += 1
        if c["label"] in ("in_mandate", "other_position"):  # routing: who should do it, as a manager must decide
            route_n += 1
            picked = d["receiver"] if v["verdict"] == "in_mandate" else v.get("owner")
            route_ok += picked == c["owner"]
        if c["label"] == "other_position" and got and v["verdict"] == "other_position":
            owner_n += 1
            owner_ok += v.get("owner") == c.get("owner")
        if want != got:
            misses.append(f"  {'missed' if want else 'false flag'} {c["id"]}: {d["receiver"]} labelled "
                          f"{c['label']}{' (' + c['owner'] + ')' if c.get('owner') else ''}, judged {v['verdict']} "
                          f"{v.get('owner') or ''} {v.get('confidence')}: {v.get('reason', '')[:160]}")
        if out:
            out.write(json.dumps({"id": c["id"], "label": c["label"], "owner": c.get("owner"), **v},
                                 ensure_ascii=False) + "\n")
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    return {"n": len(pairs), "errors": err, "precision": p, "recall": r, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "owner_accuracy": owner_ok / owner_n if owner_n else None, "routing": route_ok / route_n if route_n else None, "confusion": confusion, "cost_usd": cost,
            "misses": misses}


def show(name, s):
    f = lambda x: "-" if x is None else f"{x:.2f}"  # noqa: E731
    print(f"{name} ({of_fit.VERSION}, {s.get('model')}): n={s['n']} errors={s['errors']} precision={f(s['precision'])} "
          f"recall={f(s['recall'])} (tp {s['tp']}, fp {s['fp']}, fn {s['fn']}, tn {s['tn']}) "
          f"owner={f(s['owner_accuracy'])} routing={f(s['routing'])} ${s['cost_usd']:.3f}")
    for label, row in sorted(s["confusion"].items()):
        print(f"  {label:<15} -> " + ", ".join(f"{k} {v}" for k, v in sorted(row.items())))
    print("\n".join(s["misses"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", nargs="?", default="synthetic", choices=["synthetic", "real", "both"])
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--out")
    ap.add_argument("--model", default=of_fit.MODEL, help="the judge's model (haiku, sonnet, opus, claude-fable-5-2, ...)")
    a = ap.parse_args()
    out = open(a.out, "w") if a.out else None
    if a.source in ("synthetic", "both"):
        show("synthetic", dict(score(*synthetic(), jobs=a.jobs, out=out, model=a.model), model=a.model))
    if a.source in ("real", "both"):
        show("real", dict(score(*real(), jobs=a.jobs, out=out, model=a.model, as_of=True), model=a.model))


if __name__ == "__main__":
    main()
