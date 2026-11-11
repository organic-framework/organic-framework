#!/usr/bin/env python3
"""lab-onboarding-v0 — C1 and C2 (bench/onboarding/environments/acme.md §4).

The onboarding package's dry run on the fictional company Acme. Runs inside a clean lab VM, as the lab user, with
herdr running and the stub installed as claude. Domain and oracles: of_onboarding (acme.md). Harness: of_lab.

  C1  of init writes the structure and starts nothing
  C2  of spawn starts the conductor first

Run: python3 check.py [--only c1,c2]   (prints one line per assertion, then PASS/FAIL per check; exit 1 on any fail)
"""
import argparse
import json
import os
import subprocess
import sys

HOME = os.path.expanduser("~")
OF = os.path.join(HOME, "organic-framework", "bin", "of")
ACME = os.path.join(HOME, "of", "acme")
POS = os.path.join(ACME, "registry", "positions.yaml")
ENV = dict(os.environ, PATH=f"{HOME}/.local/bin:" + os.environ.get("PATH", ""), OF_AUTOMATION="1")
FIRST7 = ["acme_conductor", "acme_owner", "acme_onboarding", "acme_business-case",
          "acme_system-architect", "acme_org", "acme_flow"]
ROLES = ["conductor", "sponsor", "lead", "business-case", "system-architect", "structure-keeper", "flow-analyst"]
AGENTS = ["storefront_dev", "checkout_dev", "warehouse_dev"]
STAGES = ["charter", "problem", "data", "baselines", "integrate", "decide", "increments", "close"]
results = []


def ok(cond, label, extra=""):
    results.append((bool(cond), label, extra))
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  [{extra}]" if extra and not cond else ""))
    return bool(cond)


def of(*args, **kw):
    return subprocess.run([OF, *args], capture_output=True, text=True, env=ENV, **kw)


def load_yaml(path):
    import yaml
    with open(path) as fh:
        return yaml.safe_load(fh)


def c1():
    print("== C1: of init writes the structure, and starts nothing ==")
    import shutil as _sh; _sh.rmtree(ACME, ignore_errors=True)
    r = of("init", "acme", "--owner", "Alex", "--path", f"{HOME}/code/storefront",
           "--path", f"{HOME}/code/checkout", "--path", f"{HOME}/code/warehouse", "--yes")
    ok(r.returncode == 0, "of init acme exits 0", r.stderr.strip()[:200])
    if not os.path.exists(POS):
        ok(False, "positions.yaml exists", POS)
        return
    reg = load_yaml(POS)
    names = [p["name"] for p in reg.get("positions", [])]
    ok(names[:7] == FIRST7, "the first seven positions in order", str(names[:7]))
    ok([n for n in names if n in AGENTS] == AGENTS, "then the three running agents", str(names[7:]))
    by = {p["name"]: p for p in reg.get("positions", [])}
    ok(all(not (by.get(n, {}).get("holder") or {}).get("session") for n in FIRST7),
       "none of the first seven has a holder.session (herdr on the VM)")
    ok(all((by.get(n, {}).get("package") or {}).get("role") == role for n, role in zip(FIRST7, ROLES)),
       "each of the seven has package onboarding v1 with its role")
    ok((by.get("acme_owner", {}).get("holder") or {}) == {"runtime": "human", "person": "alex"},
       "acme_owner is held by the person alex", str((by.get("acme_owner") or {}).get("holder")))
    cond = by.get("acme_conductor", {})
    ok(cond.get("owner_chat") is True and cond.get("owner") == "acme_owner", "acme_conductor owner_chat + owner")
    ok(all((by.get(a, {}).get("owner") == "acme_owner" and (by.get(a, {}).get("holder") or {}).get("session"))
           for a in AGENTS), "each agent owned by acme_owner with a holder.session")
    ok((reg.get("people") or {}).get("alex") == {"name": "Alex", "machines": [], "status": "active"},
       "people names alex", str((reg.get("people") or {}).get("alex")))
    ok((reg.get("systems") or {}).get("acme", {}).get("keeper") == "acme_org", "systems.acme keeper acme_org")
    ok((reg.get("units") or {}).get("onboarding", {}).get("owner") == "acme_owner", "units.onboarding owner acme_owner")
    work = (reg.get("works") or {}).get("acme-onboarding") or {}
    wstages = [s.get("id") if isinstance(s, dict) else s for s in (work.get("stages") or work.get("steps") or [])]
    ok(wstages == STAGES or set(STAGES) <= set(map(str, _flatten_stage_names(work))),
       "work acme-onboarding has the eight stages", str(wstages))
    ok(_all_open(work), "every stage is open")
    # files + no placeholders
    briefs = os.path.join(ACME, "registry", "briefs")
    ok(all(os.path.exists(os.path.join(briefs, f"{n}.md")) for n in FIRST7), "a brief for each of the seven")
    onb = os.path.join(ACME, "onboarding")
    ok(all(os.path.exists(os.path.join(onb, f)) for f in
           ("README.md", "charter.md", "role-design.md", "recheck.md", "champion.md")), "the onboarding files")
    ok(not _placeholder_in(ACME), "no file contains a template placeholder")
    orgs = load_yaml(os.path.join(HOME, ".config", "of", "orgs.yaml"))
    ae = next((o for o in (orgs.get("orgs") or []) if o.get("name") == "acme"), {})
    ok((ae.get("paths") or [None])[0] in (f"{HOME}/of/acme", "~/of/acme"), "orgs.yaml acme first path is ~/of/acme",
       str(ae.get("paths")))
    rc = of("registry", "check")
    ok(rc.returncode == 0, "of registry check exits 0", rc.stderr.strip()[:200])
    ok("acme_org" in (rc.stdout + rc.stderr) and "no owner" in (rc.stdout + rc.stderr).lower(),
       "its one warning names acme_org has no owner's ask id")
    before = open(POS, "rb").read()
    r2 = of("init", "acme", "--owner", "Alex", "--path", f"{HOME}/code/storefront",
            "--path", f"{HOME}/code/checkout", "--path", f"{HOME}/code/warehouse", "--yes")
    ok(r2.returncode == 0 and open(POS, "rb").read() == before, "idempotent: positions.yaml byte-for-byte the same")


def _flatten_stage_names(work):
    out = []
    for v in (work.values() if isinstance(work, dict) else []):
        if isinstance(v, list):
            for s in v:
                out.append(s.get("name") if isinstance(s, dict) else s)
    return out


def _all_open(work):
    stages = work.get("stages") or work.get("works") or []
    if isinstance(stages, dict):
        stages = list(stages.values())
    return bool(stages) and all((s.get("state") if isinstance(s, dict) else None) == "open" for s in stages)


def _placeholder_in(root):
    for dp, _, fns in os.walk(root):
        for fn in fns:
            try:
                t = open(os.path.join(dp, fn), encoding="utf-8").read()
            except Exception:
                continue
            if any(ph in t for ph in ("{org}", "{Org}", "{owner_id}", "{owner_name}")):
                return True
    return False


def acme_holders():
    r = of("who", "--json")
    try:
        rows = json.loads(r.stdout)
    except ValueError:
        return None, r
    rows = rows.get("positions", rows) if isinstance(rows, dict) else rows
    held = [x for x in rows if str(x.get("name", "")).startswith(("acme_", "storefront_", "checkout_", "warehouse_"))
            and (x.get("session") or x.get("holder") or x.get("state") not in (None, "offline", "idle"))]
    return held, r


def c2():
    print("== C2: the conductor is the first agent started ==")
    held, _ = acme_holders()
    ok(held == [], "before: no acme position has a live holder", str(held))
    import time as _t
    r = of("spawn", "acme_conductor", "--config-dir", os.path.join(HOME, ".claude"))
    ok(r.returncode == 0, "of spawn acme_conductor exits 0", (r.stderr or r.stdout).strip()[:200])
    # the session of spawn started (its declared brief, of.injected_prompt from of_spawn) holds acme_conductor,
    # and it is the only position of spawn has started so far — the conductor is the first agent started.
    _t.sleep(3)
    import glob
    import sqlite3
    ev = sorted(glob.glob(os.path.join(HOME, ".local", "state", "of", "*.db")))
    spawned, first = [], None
    if ev:
        c = sqlite3.connect(ev[0])
        try:
            spawned = [row[0] for row in c.execute(
                "select json_extract(payload,'$.target_session') from event where name='of.injected_prompt' "
                "and json_extract(payload,'$.injector') like '%of_spawn%' order by ts")]
            if spawned:
                row = c.execute("select position from event where name in ('of.holder_bound','of.holder_changed') "
                                "and (session_id=? or json_extract(payload,'$.new_session')=? "
                                "or json_extract(payload,'$.session')=?) and position like 'acme_%' "
                                "order by ts limit 1", (spawned[0], spawned[0], spawned[0])).fetchone()
                first = row[0] if row else None
                if first is None:  # fall back: the conductor holder bound at spawn time
                    row = c.execute("select position from event where name in ('of.holder_bound','of.holder_changed') "
                                    "and position in ('acme_conductor','acme_onboarding','acme_business-case',"
                                    "'acme_system-architect','acme_org','acme_flow') order by ts desc limit 1").fetchone()
                    first = row[0] if row else None
        finally:
            c.close()
    ok(first == "acme_conductor" and len(spawned) == 1,
       "of spawn started acme_conductor first (and only it so far)", f"first={first} spawned={len(spawned)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="c1,c2")
    a = ap.parse_args()
    which = a.only.split(",")
    if "c1" in which:
        c1()
    if "c2" in which:
        c2()
    npass = sum(1 for p, _, _ in results if p)
    print(f"\nlab-onboarding-v0 {a.only}: {npass}/{len(results)} assertions pass")
    return 0 if npass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
