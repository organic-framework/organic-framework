#!/usr/bin/env python3
"""lab-tempo-v0: score the booking judge (bin/of_tempo.py; hand-over 277de578, part 5) on labelled bookings.

Two sources, scored apart (as lab-fit-v0 does):
- synthetic: cases.json, a fictional organization's crons, clock waits, messages up and asks, each labelled
  `no_basis` (work put off with no named reason) or `passes` (a basis, or no booking); the judge is called for real
  (Haiku), so it is a paid run, not a unit test;
- real: real_cases.json names items of a past window in this machine's store by their key, with the label and its
  source; it lives outside the public tree, under lab-private/ (release README §2), replays the window with
  `of_tempo.replay` and prints only scores.

A flag is the verdict `no_basis`. The scores are the flag's precision and recall on the labelled items; the real run
also prints how many items of the window were flagged in all.

    python3 lab/scenarios/lab-tempo-v0/run.py [synthetic|real|both] [--out verdicts.json]
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "..", "bin"))
import of_tempo  # noqa: E402

TS = "2026-11-20T13:00:00Z"


def real_cases():
    base = os.environ.get("OF_LAB_PRIVATE") or os.path.expanduser("~/organic-framework-build/lab-private")
    p = os.path.join(base, "scenarios", "lab-tempo-v0", "real_cases.json")
    return json.load(open(p)) if os.path.exists(p) else None


def scores(pairs):
    """pairs: [(label, verdict or None)] -> (tp, fp, fn, tn, errors)."""
    tp = fp = fn = tn = err = 0
    for label, v in pairs:
        if v is None:
            err += 1
            continue
        want, got = label == "no_basis", v == "no_basis"
        tp, fp, fn, tn = tp + (want and got), fp + (got and not want), fn + (want and not got), tn + (not want and not got)
    return tp, fp, fn, tn, err


def report(name, pairs, cost, extra=""):
    tp, fp, fn, tn, err = scores(pairs)
    p = tp / (tp + fp) if tp + fp else float("nan")
    r = tp / (tp + fn) if tp + fn else float("nan")
    print(f"{name}: n={len(pairs)} precision={p:.2f} recall={r:.2f} (tp {tp}, fp {fp}, fn {fn}, tn {tn}, "
          f"errors {err}) cost ${cost:.3f}{extra}")


def synthetic():
    cases = json.load(open(os.path.join(HERE, "cases.json")))
    todo = [{"key": c["id"], "source": c["source"], "position": c["position"], "ts": TS, "ref": c["id"],
             "to": c.get("to"), "text": c["text"], "times": of_tempo.times_ahead(c["text"], TS)} for c in cases]
    label = {c["id"]: c["label"] for c in cases}
    got, cost = {}, 0.0
    for pos, group, v in of_tempo.judge_items(todo, log=lambda m: print(m, file=sys.stderr)):
        cost += v.get("cost_usd") or 0
        for it, x in zip(group, v.get("verdicts") or [None] * len(group)):
            got[it["key"]] = x and x["verdict"]
            if x and (x["verdict"] == "no_basis") != (label[it["key"]] == "no_basis"):
                print(f"  {it['key']} {label[it['key']]} -> {x['verdict']} ({x['basis_kind']}): {x['reason']}")
    report("synthetic", [(label[k], got.get(k)) for k in label], cost)


def real(out=None):
    """OUT: a list to collect every verdict of the window into (`--out FILE` writes it, to label from)."""
    rc = real_cases()
    if not rc:
        print("real: no real_cases.json under lab-private/: skipped")
        return
    import importlib.machinery
    import importlib.util
    import of_store
    loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(HERE, "..", "..", "..", "bin", "of"))
    of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli", loader))
    loader.exec_module(of)
    conn = of_store.connect()
    got, cost, flagged, n = {}, 0.0, 0, 0
    label = {c["key"]: c["label"] for c in rc["cases"]}
    for pos, group, v in of_tempo.replay(conn, rc["since"], rc["until"], of._load_quiet(), org=rc.get("org"),
                                         log=lambda m: print(m, file=sys.stderr)):
        cost += v.get("cost_usd") or 0
        for it, x in zip(group, v.get("verdicts") or [None] * len(group)):
            n += 1
            flagged += bool(x and x["verdict"] == "no_basis")
            got[it["key"]] = x and x["verdict"]
            if out is not None and x:
                out.append({"key": it["key"], "ts": it["ts"], "source": it["source"], "text": it["text"][:600], **x})
            want = label.get(it["key"])
            if x and want and (x["verdict"] == "no_basis") != (want == "no_basis"):
                print(f"  {it['key']} {want} -> {x['verdict']} ({x['basis_kind']}: {x['basis']}): {x['reason']}")
    missing = [c["key"] for c in rc["cases"] if c["key"] not in got]
    report("real", [(c["label"], got.get(c["key"])) for c in rc["cases"]], cost,
           f"; the window: {n} items, {flagged} flagged" + (f"; {len(missing)} labelled items not found" if missing
                                                              else ""))


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    if which in ("synthetic", "both"):
        synthetic()
    if which in ("real", "both"):
        dump = [] if "--out" in sys.argv else None
        real(dump)
        if dump is not None:
            with open(sys.argv[sys.argv.index("--out") + 1], "w") as fh:
                json.dump(dump, fh, ensure_ascii=False, indent=1)
