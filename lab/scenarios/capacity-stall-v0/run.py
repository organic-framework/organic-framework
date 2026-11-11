#!/usr/bin/env python3
"""capacity-stall-v0: can the balancer leave a session stopped on a usage limit while another account accepts work?

Loads bin/of_capacity.py from a git revision (default: the working tree), plans each case with it, and checks the
invariant with its own oracle:
  - every holder stopped on a limit, between turns, on an account that does not accept work, is moved, and
  - to an account that accepts work (the case's ground truth);
  - no move goes to an account that does not accept work, and no mid-turn session moves.
A revision without `decide` (before the rescue) is planned with its balance_plan, as its balancer ran.

Run: python3 lab/scenarios/capacity-stall-v0/run.py [--rev REV] [--json]
Expected (2026-11-11): 3b09797 (before 62d6e1b) and 62d6e1b both fail the real case and the four class cases, incl.
the stalled production holder (3 of 8: the two negatives and the cheap-stalled guard pass); the rescue passes all
eight."""
import argparse
import importlib.util
import inspect
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(ROOT, "bin"))
import cases as C  # noqa: E402


def load(rev):
    if not rev:
        path = os.path.join(ROOT, "bin", "of_capacity.py")
    else:
        src = subprocess.run(["git", "-C", ROOT, "show", f"{rev}:bin/of_capacity.py"], capture_output=True,
                             text=True, check=True).stdout
        path = os.path.join(tempfile.mkdtemp(prefix="cap-stall-"), "of_capacity.py")
        with open(path, "w") as fh:
            fh.write(src)
    spec = importlib.util.spec_from_file_location(f"of_capacity_{rev or 'tree'}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def plan(mod, c):
    pol = dict(mod.POLICY, **c["policy"])
    args = (c["rows"], c["sessions"], c["positions"], c["rates"], c["calib"], pol, c["now"])
    if hasattr(mod, "decide"):
        return mod.decide(*args, stopped=c["stopped"], contexts=c["contexts"], wcalib=c["wcalib"])
    params = inspect.signature(mod.balance_plan).parameters
    kw = {k: v for k, v in (("contexts", c["contexts"]), ("wcalib", c["wcalib"])) if k in params}
    return mod.balance_plan(*args, **kw)


def check(c, moves):
    acc = set(c["accepting"])
    where = {s["session"]: d for d, xs in c["sessions"].items() for s in xs}
    status = {s["session"]: s.get("status") for xs in c["sessions"].values() for s in xs}
    must = {s for s in c["stopped"] if where.get(s) not in acc and status.get(s) in ("idle", "shell", "waiting")} \
        if acc else set()
    moved = {m["session"]: m["to"] for m in moves}
    problems = [f"{s} stopped on {where[s]} and not moved" for s in sorted(must - set(moved))]
    problems += [f"{s} moved to {t}, which does not accept work" for s, t in sorted(moved.items()) if t not in acc]
    problems += [f"{s} moved mid-turn" for s in sorted(moved) if status.get(s) == "busy"]
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rev", default=None, help="git revision of bin/of_capacity.py (default: the working tree)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    mod = load(a.rev)
    out = []
    for make in C.CASES:
        c = make()
        moves = plan(mod, c)
        p = check(c, moves)
        out.append({"case": c["name"], "pass": not p, "problems": p,
                    "moves": [(m["position"], m["from"], m["to"], m["reason"].split(":")[0]) for m in moves]})
    if a.json:
        print(json.dumps({"rev": a.rev or "tree", "results": out}, indent=1))
    else:
        for r in out:
            print(("PASS " if r["pass"] else "FAIL ") + r["case"] + ("" if r["pass"] else ": " + "; ".join(r["problems"])))
        print(json.dumps({"rev": a.rev or "tree", "passed": sum(r["pass"] for r in out), "of": len(out)}))
    return 0 if all(r["pass"] for r in out) else 1


if __name__ == "__main__":
    sys.exit(main())
