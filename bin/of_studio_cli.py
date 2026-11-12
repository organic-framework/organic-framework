"""`of model …` and `of studio` (docs/studio.md §6). Read-only except `of model snapshot`.

bin/of dispatches here (`of model` → main(["model", …]), `of studio` → main(["studio", …])).
It also runs alone: python3 bin/of_studio_cli.py model coverage --org acme
"""

import argparse
import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

import of_diagnose  # noqa: E402
import of_measures  # noqa: E402
import of_model  # noqa: E402
import of_studio  # noqa: E402


def _org(name):
    orgs = of_studio.orgs()
    for o in orgs:
        if o["name"] == name:
            return o
    if name is None and orgs:
        return orgs[-1]
    raise SystemExit(f"of model: no organization {name!r} in orgs.yaml ({', '.join(o['name'] for o in orgs)})")


ESCALATIONS_BUDGET_S = 45   # `of model escalations` in the pulse: done well inside its 60 s


def _rows(since=None, names=None):
    try:
        import of_store
        return of_model.load_events(of_store.connect(), since=since, names=names)
    except Exception as exc:  # noqa: BLE001 — a missing store means no events, not a crash
        print(f"of model: no local store ({exc}); registry only", file=sys.stderr)
        return []


def _index(since, until):
    try:
        import of_store
        conn = of_store.connect()
        return [{"session_id": s, "type": t, "timestamp": ts, "harness": h} for s, t, ts, h in conn.execute(
            "SELECT session_id, type, timestamp, harness FROM transcript_record WHERE type = 'assistant' AND timestamp >= ? AND timestamp < ?",
            (since, until))]
    except Exception:  # noqa: BLE001
        return []


def _model(args, rows=None):
    org = _org(args.org)
    at = of_model.epoch(args.at) if getattr(args, "at", None) else time.time()
    doc, rev = of_model.registry_at(org["repo"], at)
    all_orgs = of_studio.orgs()
    rows = of_studio.org_rows(rows if rows is not None else _rows(of_model.iso(at - 9 * 86400)), org,
                              [o for o in all_orgs if o["name"] != org["name"]])
    return org, of_model.build(doc, rows, at=at, org=org["name"], rev=rev, window_h=7 * 24), rows


# ------------------------------------------------------------------ printing

def _table(rows, head):
    w = [max(len(str(x)) for x in col) for col in zip(head, *rows)] if rows else [len(h) for h in head]
    print("  ".join(h.upper().ljust(n) for h, n in zip(head, w)))
    for r in rows:
        print("  ".join(str(x).ljust(n) for x, n in zip(r, w)))


def cmd_model(args):
    if args.what == "measures":
        return cmd_measures(args)
    if args.what == "snapshot":
        return cmd_snapshot(args)
    if args.what == "gate":
        return cmd_gate(args)
    if args.what == "issues":
        return cmd_issues(args)
    if args.what == "escalations":
        return cmd_escalations(args)
    if args.what == "senders":
        return cmd_senders(args)
    if args.what == "usage":
        return cmd_usage(args)
    if args.what == "queues":
        return cmd_queues(args)
    if args.what == "works":
        return cmd_works(args)
    if args.what == "polls":
        return cmd_polls(args)
    if args.what == "loops":
        return cmd_loops(args)
    if args.what == "corrections":
        return cmd_corrections(args)
    if args.what == "activity":
        return cmd_activity(args)
    if args.what == "evidence":
        return cmd_evidence(args)
    if args.what == "cells":
        return cmd_cells(args)
    org, m, _ = _model(args)
    if args.json:
        print(of_model.snapshot(m))
        return 0
    if args.what == "summary":
        flagged = {s: c["flags"] for s, c in m["coverage"]["systems"].items() if c["flags"]}
        print(f"{m['org']} at {m['at']} · registry {m['registry_rev']}")
        print(f"positions {len(m['positions'])} ({sum(p['status'] == 'active' for p in m['positions'].values())} active) · "
              f"systems {len(m['systems'])} · declared edges {sum(1 for e in m['system_dsm'].values() if e['declared'])} · "
              f"coupled pairs {sum(1 for e in m['system_dsm'].values() if e['coupling'])} · interaction edges {len(m['position_dsm'])}")
        print("units: " + ", ".join(f"{k} {len(v)}" for k, v in sorted(m["units"].items())))
        print(f"flagged systems {len(flagged)}: " + "; ".join(f"{s} ({', '.join(f)})" for s, f in sorted(flagged.items())))
    elif args.what == "positions":
        _table([(n, p["system"], p["kind"], p["status"], p.get("throughput") or "", p["owner"], p["accountable"])
                for n, p in sorted(m["positions"].items())], ["name", "system", "kind", "status", "class", "owner", "accountable"])
    elif args.what == "systems":
        _table([(s, x.get("kind") or "", x.get("parent") or "", x.get("keeper") or "", ",".join(m["coverage"]["systems"][s]["flags"]))
                for s, x in sorted(m["systems"].items())], ["system", "kind", "parent", "keeper", "flags"])
    elif args.what == "dsm":
        _table([(a, b, e["declared"] or "", e["coupling"]) for (a, b), e in sorted(m["system_dsm"].items())],
               ["from", "to", "declared", "coupling"])
    elif args.what == "dmm":
        _table([(p, s, of_model.cell_code(c)) for (p, s), c in sorted(m["dmm"].items()) if of_model.cell_code(c)],
               ["position", "system", "cell"])
    elif args.what == "coverage":
        _table([(s, c["keeper"] or "-", ",".join(f"{p}:{n}" for p, n in c["writers"].items()) or "-",
                 (",".join(c["homes"]) if len(c["homes"]) <= 3 else f"{len(c['homes'])} positions") or "-", ",".join(c["flags"])) for s, c in sorted(m["coverage"]["systems"].items())],
               ["system", "keeper", "writers", "homes", "flags"])
    elif args.what == "position-dsm":
        _table([(a, b, e["asks"], e["messages"], e["reads"], round(e["weight"], 2))
                for (a, b), e in sorted(m["position_dsm"].items(), key=lambda kv: -kv[1]["weight"])],
               ["from", "to", "asks", "messages", "reads", "weight"])
    return 0


def cmd_measures(args):
    b = of_model.epoch(args.to) if args.to else time.time()
    a = of_model.epoch(args.frm) if args.frm else b - 7 * 86400
    args.at = of_model.iso(b)
    _, m, rows = _model(args, _rows(of_model.iso(a - 2 * 86400)))
    o = of_measures.owner(m, rows, a, b, who=args.owner or of_model.HUMAN)
    fl = of_measures.flow(m, rows, a, b, who=o["owner"], own=o)
    if args.json:
        print(json.dumps(dict(o, flow=fl), indent=1, ensure_ascii=False))
        return 0
    d = o["answer_delay_min"]
    print(f"owner {o['owner']} · {o['from']} – {o['to']}")
    print(f"  L {o['L']}  λ {o['lambda_per_h']}/h  W {o['W_h']} h  residual {o['residual']} ({o['residual_rel']})"
          f"  · arrivals {o['arrivals']}, censored {o['censored']}, open now {o['open_at_end']}")
    print(f"  answer delay p50/p75/p90 {d['p50']}/{d['p75']}/{d['p90']} min (n {d['n']}) · agent-hours waited {o['agent_hours_waited']}")
    print(f"  dead-end asks {len(o['dead_end_asks'])} · inbound concentration {o['inbound_concentration']}"
          f" (non-throughput {o['inbound_from_non_throughput']})")
    print(f"  owner messages {o['owner_messages']} · plumbing {o['plumbing_share']} · status polls {o['status_polls']}"
          f" · (a) {o['throughput_share']['share']} of {o['throughput_share']['matched']} matched")
    _print_flow(fl)
    return 0


def _flow_value(s):
    v = s["value"]
    if s["key"] == "agents_at_once":
        return f"{v['mean_working']} in working 5-minute buckets ({v['working_buckets']} of {v['buckets']}; all buckets {v['mean_all']}, peak {v['peak']})"
    if s["key"] == "wait_h":
        return f"{v['head']} / {v['peer']}"
    if s["key"] == "settled_below_share":
        d = s["detail"]
        return f"{v} ({d['asks'] - d['escalated']} of {d['asks']} not escalated; unit owners {', '.join(d['unit_owners'])})"
    d = s.get("detail") or {}
    n = next(iter(d.values()), None)
    return f"{v}" + (f" (n {n}" + (f"; {d['bots_left_out']} by bots left out" if d.get("bots_left_out") else "") + ")"
                     if isinstance(n, int) else "")


def _print_flow(fl):
    """The flow analyst's series (docs/onboarding-practice.md §5): a value or `gap: <the missing input>` each."""
    print(f"\nflow {fl['method']} (docs/onboarding-practice.md §5) · {fl['days']} days"
          + (f" · partial days {', '.join(fl['partial_days'])}" if fl["partial_days"] else "")
          + (f" · days with no event {', '.join(fl['silent_days'])}" if fl["silent_days"] else ""))
    w = max(len(s["label"]) for s in fl["series"])
    for s in fl["series"]:
        print(f"  {s['label'].ljust(w)}  " + (f"gap: {s['gap']}" if s["gap"] else _flow_value(s)))
    if fl["commits_from_git"]:
        print(f"  ({fl['commits_from_git']} commits from git in the window: another method, never counted as pull requests)")
    print("never collected:")
    for x in fl["never_collected"]:
        print(f"  gap: {x['measure']}: {x['why']}")


def _open_issues(args, org_name, rows, b):
    """One organization's diagnose run: its open issues (each tagged with `org`) and the run itself."""
    import of_diagnose
    org, m, rows = _model(argparse.Namespace(org=org_name, at=of_model.iso(b)), rows)
    ctx = {"keeper_since": lambda sysid, k: of_model.keeper_since(org["repo"], sysid, k)}
    d = of_diagnose.diagnose(m, rows, b - 7 * 86400, b, context=ctx)
    issues = [dict(i, org=org["name"]) for i in d["issues"] if i["state"] == "open"
              and (not args.decider or i["decider"] == args.decider)
              and (not getattr(args, "for_", None) or i.get("decider_position") == args.for_)]
    return org["name"], issues, d


def cmd_issues(args):
    """The diagnosed issues (docs/diagnose.md) and each rule's status; --decider structure_keeper for the keeper's own.
    --org all: every organization in orgs.yaml, one diagnose run each (`of issues` lists and labels across them)."""
    b = time.time()
    args.at = of_model.iso(b)
    rows = _rows(of_model.iso(b - 16 * 86400))
    names = [o["name"] for o in of_studio.orgs()] if args.org == "all" else [args.org]
    runs = [_open_issues(args, n, rows, b) for n in names]
    if args.json:
        if args.org != "all":
            _, issues, d = runs[0]
            print(json.dumps({"issues": issues, "rules": d["rules"], "structure_keeper": d.get("structure_keeper")},
                             indent=1, ensure_ascii=False))
        else:
            print(json.dumps({"issues": [i for _, iss, _ in runs for i in iss],
                              "orgs": {n: {"rules": d["rules"], "structure_keeper": d.get("structure_keeper")} for n, _, d in runs}},
                             indent=1, ensure_ascii=False))
        return 0
    several = len(runs) > 1
    _table([((n,) if several else ()) + (i["rule_version"], i.get("part") or "", i["decider_position"] or i["decider"],
                                         i["issue_id"], i["summary"][:90])
            for n, iss, _ in runs for i in iss], (["org"] if several else []) + ["rule", "part", "decider", "issue", "what"])
    for n, _, d in runs:
        print()
        if several:
            print(f"rules for {n}:")
        _table([(r["version"], r["status"], r["issues"] or "", ", ".join(r["missing"]) or
                 ("parts blocked: " + ", ".join(f"{k} ({', '.join(v)})" for k, v in r["parts_blocked"].items()) if r["parts_blocked"] else ""))
                for r in d["rules"]], ["rule", "status", "issues", "missing"])
        for r in d["rules"]:
            for x in r.get("notes") or []:
                print(f"note {r['version']}: {x}")
    return 0


def cmd_senders(args):
    """Received peer messages whose sender resolves to no position, by from-name: aliases for the structure keeper."""
    import of_diagnose
    b = time.time()
    args.at = of_model.iso(b)
    _, m, rows = _model(args, _rows(of_model.iso(b - 9 * 86400)))
    c = of_model.unresolved_senders(m, rows, b - 7 * 86400, b)
    if args.json:
        print(json.dumps(dict(c.most_common()), indent=1, ensure_ascii=False))
    else:
        _table(c.most_common(), ["from-name", "messages"])
    return 0


def _short_model(name):
    return (name or "?").replace("claude-", "")


def cmd_usage(args):
    """Each delegated unit's use this month per position, per UTC day and per model (of_studio.usage_by_position), the
    same count as the unit's budget row: what a unit owner needs to answer a budget escalation with a plan (a user organization's structure keeper,
    2026-11-11). A partial index prints a gap, never numbers."""
    b = time.time()
    args.at = of_model.iso(b)
    org, m, rows = _model(args, _rows(of_model.iso(b - 9 * 86400), names=of_studio.kinds()))
    u = of_studio.usage_by_position(m, rows, b, units=args.unit, refresh=args.refresh)
    missing = sorted(set(args.unit or ()) - set(u["units"]))
    if missing:
        print(f"not a delegated unit of {org['name']}: {', '.join(missing)}", file=sys.stderr)
        return 2
    read = of_model.iso(u["read_at"])[:16] + "Z" if u["read_at"] else "never"
    if not u["complete"]:
        gap = (f"the usage index for {u['month']} has not read every transcript yet (last read {read}); "
               "the pulse goes on with it, or run again with --refresh")
        print(json.dumps({"month": u["month"], "read_at": u["read_at"], "complete": False, "gap": gap}, indent=1)
              if args.json else f"gap: {gap}")
        return 0
    if args.json:
        print(json.dumps(dict(u, org=org["name"]), indent=1, ensure_ascii=False))
        return 0
    pct = lambda x, of: f"{100 * x / of:.1f}%" if isinstance(of, (int, float)) and of > 0 else "-"
    print(f"use in {u['month']} (UTC), {org['name']}, index read {read}\n"
          "tokens: input + output + cache writes (cache reads left out); dollars: Opus 5.5 list prices for every model "
          "(of_capacity.usage); both as the unit budget counts them")
    for uid, r in u["units"].items():
        bt, bu = r["budget_tokens"], r["budget_usd"]
        print(f"\n{uid} (owner {r['owner'] or '-'}): {r['fresh']:,} tokens"
              + (f" of {bt:,} ({pct(r['fresh'], bt)})" if isinstance(bt, (int, float)) else "")
              + f", ${r['cost_usd']:,.2f}" + (f" of ${bu:,} ({pct(r['cost_usd'], bu)})" if isinstance(bu, (int, float)) else "")
              + f", {r['sessions']} sessions")
        ps = sorted(r["positions"].items(), key=lambda kv: (-kv[1]["fresh"], kv[0]))
        if not ps:
            print("  no session of this unit's positions this month")
            continue
        _table([(p + ("*" if v["also_in"] else ""), v["sessions"], f"{v['fresh']:,}", pct(v["fresh"], r["fresh"]),
                 f"{v['cost_usd']:,.2f}",
                 ", ".join(f"{_short_model(k)} {pct(f, v['fresh'])}" for k, f in sorted(v["models"].items(), key=lambda kv: -kv[1])
                           if f > 0) or "-") for p, v in ps],
               ["position", "sessions", "tokens", "of unit", "usd", "models (by tokens)"])
        if any(v["also_in"] for _, v in ps):
            print("* also in " + "; ".join(f"{p}: {', '.join(v['also_in'])}" for p, v in ps if v["also_in"])
                  + " (counted in each unit)")
        zero = {"fresh": 0, "cost_usd": 0.0}
        days = sorted({d for _, v in ps for d in v["days"]})
        if args.per_day:
            _table([(d[5:], p, f"{v['days'][d]['fresh']:,}", f"{v['days'][d]['cost_usd']:,.2f}")
                    for d in days for p, v in ps if d in v["days"]], ["day", "position", "tokens", "usd"])
        elif days:
            tot = {d: (sum(v["days"].get(d, zero)["fresh"] for _, v in ps), sum(v["days"].get(d, zero)["cost_usd"] for _, v in ps))
                   for d in days}
            top = {d: max(ps, key=lambda kv: kv[1]["days"].get(d, zero)["fresh"])[0] for d in days}
            _table([(d[5:], f"{tot[d][0]:,}", f"{tot[d][1]:,.2f}", top[d]) for d in days],
                   ["day", "tokens", "usd", "most tokens"])
            print("(each position per day: --per-day; everything: --json)")
    return 0


def cmd_queues(args):
    """Inbound load per position: relayed messages waiting on the server to be taken (of.inbound_queue)."""
    b = of_model.epoch(args.to) if args.to else time.time()
    a = of_model.epoch(args.frm) if args.frm else b - 7 * 86400
    args.at = of_model.iso(b)
    _, m, rows = _model(args, _rows(of_model.iso(a - 2 * 86400)))
    q = of_measures.inbound_queues(m, rows, a, b)
    if args.json:
        print(json.dumps(q, indent=1, ensure_ascii=False))
        return 0
    order = sorted(q.items(), key=lambda kv: (-kv[1]["backlog_min"], -kv[1]["depth_now"], kv[0]))
    _table([(p, v["depth_now"], v["oldest_min_now"] if v["oldest_min_now"] is not None else "-", v["depth_max"],
             v["depth_avg"], round(v["backlog_min"] / 60, 1), v["observed_from"][:16]) for p, v in order],
           ["position", "now", "oldest_min", "max", "avg", "over_30m_h", "observed_from"])
    return 0


def cmd_works(args):
    """The works of an organization (docs/works.md; v0 derived by of_works), the rows of Studio's Works view."""
    import of_works
    d = of_studio.Data(args.org)
    ws = of_studio.works_of(d) or []
    tree = of_works.goal_tree(d.full, ws) if d.full and (d.full.get("goals") or d.full.get("conditions")) else None
    if getattr(args, "goals", False):
        return _goal_tree_out(d, tree, args.json)
    if args.json:
        keep = ("id", "kind", "title", "state", "stage_summary", "doers", "systems", "doc", "review", "done_when", "status_line",
                "past_review", "progress_7d", "source", "goal", "condition")
        print(json.dumps([dict({k: w.get(k) for k in keep}, last_progress=of_model.iso(w["last_progress"]) if w["last_progress"] else None,
                               waiting_on=[of_studio._wait_text(x, d.now) for x in w["waits"]],
                               prs=[f'{p["repo"]}#{p["number"]}' for p in w["prs"][:20]],
                               open_prs=[f'{p["repo"]}#{p["number"]}' for p in (w.get("open_prs") or [])[:20]]) for w in ws], indent=1, ensure_ascii=False))
        return 0
    if tree:
        _goal_tree_out(d, tree, False)
        print()
    stage = lambda w: (w.get("stage_summary") or "-").replace(" done", "").replace(" · moving: ", " >")[:22]
    _table([(w["state"] + ("*" if w["past_review"] else ""), stage(w), w["id"][:46], ",".join(w["doers"])[:34] or "-",
             "; ".join(of_studio._wait_text(x, d.now) for x in w["waits"][:2])[:48] or "-",
             of_works.age(w["last_progress"], d.now) or "-", w["progress_7d"], w.get("review") or "-")
            for w in ws if args.all or w["doers"] or w["state"] in ("active", "waiting")],
           ["state", "stages", "work", "doers", "waiting on", "last", "7d", "review"])
    if not args.all:
        print(f"({sum(1 for w in ws if not (w['doers'] or w['state'] in ('active', 'waiting')))} more without doers: --all; "
              "* past review; stages: done of all, > moving)")
    return 0


def _goal_tree_out(d, t, as_json):
    """The goal tree as Studio's Goals tab shows it (docs/registry.md, Goals; docs/studio.md §5.5a): goals in rank
    order, each with its measure, target, owner and the rollup of its branch; the conditions beside it, a breach
    first; the open works with no goal."""
    import of_works
    if t is None:
        if as_json:
            print(json.dumps({"org": d.org, "goals": [], "conditions": [], "no_goal": []}, indent=1))
        else:
            print(f"{d.org}: no goals or conditions in the registry (docs/registry.md, Goals: the structure keeper writes them "
                  "with the ask id of the owner's yes)")
        return 0
    for c in t["conditions"]:
        c["reading"] = of_studio.metric_reading(d, c.get("metric"))
        c["breached"] = of_studio.level_breached(c.get("level"), c["reading"])
    live = [w for w in t["loose"] if w.get("state") not in ("done", "parked")]
    roll = lambda r: dict(r, last_progress=of_model.iso(r["last_progress"]) if r["last_progress"] else None)
    if as_json:
        def node(n):
            return {k: n.get(k) for k in ("id", "title", "rank", "depth", "state", "measure", "metric", "target", "by", "owner",
                                          "owner_from", "decided", "drives", "notes")} | {
                "reading": of_studio.metric_reading(d, n.get("metric")), "works": [w["id"] for w in n["works"]],
                "rollup": roll(n["rollup"]), "children": [node(c) for c in n["children"]]}
        print(json.dumps({"org": d.org, "goals": [node(n) for n in t["roots"]],
                          "conditions": [{k: c.get(k) for k in ("id", "title", "measure", "level", "metric", "reading", "breached",
                                                                 "under", "owner", "state", "decided")}
                                         | {"works": [w["id"] for w in c["works"]], "rollup": roll(c["rollup"])} for c in t["conditions"]],
                          "no_goal": [w["id"] for w in live], "unknown": t["unknown"], "notes": t["notes"]},
                         indent=1, ensure_ascii=False, default=str))
        return 0

    def line(r):
        asks = f', {r["asks"]} open ask{"s" if r["asks"] != 1 else ""}' + (f' ({r["on_owner"]} on an owner)' if r["on_owner"] else "")
        doers = ", ".join(r["doers"][:5]) + (f" +{len(r['doers']) - 5}" if len(r["doers"]) > 5 else "")
        return (f'{r["open"]} open of {r["works"]} works; doers {doers or "none"}{asks if r["asks"] else ""}; '
                f'{r["progress_7d"]} progress marks in 7 d')
    for c in [c for c in t["conditions"] if c["breached"]]:
        print(f'BREACHED  {c["id"]}: {c["title"]}: level {c.get("level")}, now {c["reading"]:g} (owner {c["owner"]})')
    print(f"Goals of {d.org}, in rank order (each line adds up its whole branch):")

    def walk(n):
        pad = "  " * (n["depth"] + 1)
        head = f'{str(n["rank"]) + ". " if n["rank"] is not None else ""}{n["id"]}: {n["title"]}'
        print(f'{pad}{head}{"" if n["state"] == "active" else " [" + n["state"] + "]"}  (owner {n["owner"]})')
        print(f'{pad}   measure: {n.get("measure") or "-"}' + (f'; target {n["target"]}' if n.get("target") else "")
              + (f'; by {n["by"]}' if n.get("by") else ""))
        print(f'{pad}   {line(n["rollup"])}')
        if n["works"]:
            print(f'{pad}   works: {", ".join(w["id"] for w in n["works"][:8])}' + (f' +{len(n["works"]) - 8}' if len(n["works"]) > 8 else ""))
        for x in n["notes"]:
            print(f"{pad}   ! {x}")
        for c in n["children"]:
            walk(c)
    for r in t["roots"]:
        walk(r)
    if t["conditions"]:
        print("Conditions, held at their level:")
        for c in t["conditions"]:
            now = f'{c["reading"]:g}' if c["reading"] is not None else ("not a Studio measure" if c.get("metric") else "no metric")
            flag = " BREACHED" if c["breached"] else (" held" if c["breached"] is False else "")
            print(f'  {c["id"]}: {c["title"]}{"" if c["state"] == "active" else " [" + c["state"] + "]"}  (owner {c["owner"]}'
                  f'{", for " + c["under"] if c.get("under") else ""})')
            print(f'     measure: {c.get("measure") or "-"}; level {c.get("level") or "-"}; now {now}{flag}')
            print(f'     {line(c["rollup"])}')
    for x in t["notes"]:
        print(f"! {x}")
    if t["unknown"]:
        print("Works naming a goal or condition the registry lacks: " + ", ".join(f"{w} -> {g}" for w, g in t["unknown"]))
    print(f"Works with no goal: {len(live)} open" + (f' ({", ".join(w["id"] for w in live[:10])}{" …" if len(live) > 10 else ""})' if live else ""))
    return 0


def cmd_loops(args):
    """The development loops of an organization (RFC 0005 §5): one line per work with a loop, the card's data as JSON."""
    import of_loops
    d = of_studio.Data(args.org)
    cards = [(w, d.loop_of(w)) for w in of_studio.works_of(d) or []]
    cards = [(w, c) for w, c in cards if c]
    if args.json:
        drop = {"spec"}
        print(json.dumps([dict({k: v for k, v in c.items() if k not in drop}, title=w["title"], owner=w.get("owner"),
                               target=c["spec"]["target"]) for w, c in cards], indent=1, ensure_ascii=False, default=str))
        return 0
    if not cards:
        print(f"{d.org}: no work has a loop (RFC 0005: loop: on a work in the registry, or of.loop_* events)")
        return 0
    _table([(w["id"][:40], (c["spec"]["target"].get("metric") or "-")[:40], of_loops.summary(c, d.now) or "-",
             ", ".join(c["drift"]) or "-") for w, c in cards], ["work", "target", "where it stands", "drift"])
    return 0


def cmd_corrections(args):
    """The repeat-correction rate (RFC 0006 §2.4, rc-v0): per position and ISO week, the founder's corrections that
    repeat an earlier one over all of them, from of.founder_correction; the Owner page's table."""
    try:   # only the corrections and the registry: the page's whole computation is not needed here
        import of_store
        conn = of_store.connect()
        try:
            rows = of_model.load_events(conn, names=("of.founder_correction",))
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — a missing store means no corrections, not a crash
        print(f"of model: no local store ({exc}); registry only", file=sys.stderr)
        rows = []
    org, m, rows = _model(args, rows)
    rc = of_measures.repeat_corrections(m, rows, of_model.epoch(m["at"]), max(1, args.weeks))
    if args.json:
        print(json.dumps(dict(rc, org=org["name"]), indent=1, ensure_ascii=False, default=str))
        return 0
    if not rc["recorded"]:
        print(f"{org['name']}: no of.founder_correction recorded yet (RFC 0006 step 4, the judge)")
        return 0

    def cell(c):
        return f"{round(100 * c['rate'])}% {c['repeats']}/{c['n']}" if c["n"] else "-"
    rows = [(p, *(cell(v["weeks"][w]) for w in rc["weeks"]), cell(v))
            for p, v in sorted(rc["positions"].items(), key=lambda kv: (-kv[1]["n"], kv[0]))]
    tot = {"n": sum(c["n"] for c in rc["total"].values()), "repeats": sum(c["repeats"] for c in rc["total"].values())}
    tot["rate"] = tot["repeats"] / tot["n"] if tot["n"] else None
    rows.append(("all", *(cell(rc["total"][w]) for w in rc["weeks"]), cell(tot)))
    print(f"{org['name']}: repeat corrections ({rc['method']}), {rc['from'][:10]} – {rc['to'][:10]}")
    _table(rows, ["position", *(w[5:] for w in rc["weeks"]), "all weeks"])
    return 0


def _code_rows(args):
    """Merged work records and open PRs' states over the last --days before --at, from the local store only."""
    at = of_model.epoch(args.at) if getattr(args, "at", None) else time.time()
    try:
        import of_store
        conn = of_store.connect()
        try:
            return of_model.load_events(conn, since=of_model.iso(at - (max(1, args.days) + 1) * 86400),
                                        names=("of.work_record", "of.pr_state"))
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — a missing store means no code evidence, not a crash
        print(f"of model: no local store ({exc}); registry only", file=sys.stderr)
        return []


def _activity(args):
    org, m, rows = _model(args, _code_rows(args))
    b = of_model.epoch(m["at"])
    return org, m, of_model.code_activity(m, rows, b - max(1, args.days) * 86400, b, areas_depth=args.areas)


def _ask_ids():
    try:
        import of_store
        conn = of_store.connect()
        try:
            return {x for (x,) in conn.execute("SELECT event_id FROM event WHERE name IN (?, ?)", of_model.ASK_KINDS)}
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — no store: ask ids not checked
        return None


def cmd_activity(args):
    """Per-system code activity, writers and co-change over the last --days (RFC 0007 §2.2, ca-v0); with --areas N,
    the same per changed directory N levels deep: the input lab-systems-v0 clusters (RFC 0007 §2.4)."""
    org, m, act = _activity(args)
    if args.json:
        print(json.dumps(dict(act, org=org["name"]), indent=1, ensure_ascii=False, sort_keys=True))
        return 0
    print(f"{org['name']}: code activity ({act['method']}), {act['from'][:10]} – {act['to'][:10]} · {act['records']} merged records, "
          f"{act['unmapped']} unmapped, {act['bots']} by bots left out")
    _table([(sid, x["merged"], x["open"], ", ".join(f"{p}:{n}" for p, n in sorted(x["writers"].items(), key=lambda kv: -kv[1])) or "-",
             x["unattributed"] or "", (x["last"] or "")[:10])
            for sid, x in sorted(act["systems"].items(), key=lambda kv: (-kv[1]["merged"], kv[0]))],
           ["system", "merged", "open", "writers", "no position", "last"])
    if act["co_change"]:
        print()
        _table([(p["a"], p["b"], p["prs"], p["weighted"], p["wide"] or "") for p in act["co_change"][:20]],
               ["system", "system", f"records <= {act['max_systems']}", "weighted", "wider"])
    if args.areas:
        print(f"\n{len(act['areas'])} areas at depth {args.areas}, {len(act['area_co_change'])} area pairs (--json for all)")
    return 0


def cmd_evidence(args):
    """Each system's evidence (RFC 0007 §2.2): where it runs, code over --days, money, governance facts and their
    ask ids (checked against the stored asks); the Systems page's table."""
    org, m, act = _activity(args)
    ev = of_model.evidence(m, act, _ask_ids())
    if args.json:
        print(json.dumps({"org": org["name"], "at": m["at"], "days": args.days, "systems": ev}, indent=1,
                         ensure_ascii=False, sort_keys=True))
        return 0
    _table([(sid, x["lifecycle"], ((x.get("runs") or {}).get("where") or "-")[:40],
             f"{(x.get('code') or {}).get('merged', 0)}/{(x.get('code') or {}).get('open', 0)}",
             "yes" if x.get("money") else "-",
             " ".join(f"{f['fact']}:{f['state']}" for f in x["governance"]) or "-", ",".join(x["needs"]) or "-")
            for sid, x in sorted(ev.items())],
           ["system", "lifecycle", "runs", f"merged/open {args.days}d", "money", "governance", "needs"])
    return 0


def cmd_cells(args):
    """Ownership cells (RFC 0007 §2.3): the needed cells of live systems with no owner, cells with 3 or more doers,
    two deciders, and unknown systems or kinds of work in owns and needs; --json: every cell."""
    org, m, _ = _model(args, [])
    c = m["cells"]
    if args.json:
        print(json.dumps(dict(c, org=org["name"], at=m["at"]), indent=1, ensure_ascii=False, sort_keys=True))
        return 0
    print(f"{org['name']}: {len(c['cells'])} cells · {len(c['systems_with_needs'])} systems list needs · "
          f"{len(c['positions_with_owns'])} positions list owns")
    for title, items, cols in (("needed, no owner", c["gaps"], lambda r: (r["system"], r["method"], r["gap"], ",".join(r["doers"]) or "-")),
                               (f"{of_model.OVERLAP_DOERS}+ doers", c["overlaps"], lambda r: (r["system"], r["method"], ",".join(r["deciders"]) or "-", ",".join(r["doers"]))),
                               ("two deciders", c["two_deciders"], lambda r: (r["system"], r["method"], ",".join(r["deciders"]), ""))):
        print(f"\n{title}: {len(items)}")
        if items:
            _table([cols(r) for r in items], ["system", "method", "", ""])
    if c["unknown"]:
        print(f"\nunknown in owns or needs: {len(c['unknown'])}")
        _table([(u["position"] or "(needs)", u["system"], u["method"], u["why"]) for u in c["unknown"]],
               ["position", "system", "method", "why"])
    return 0


def cmd_polls(args):
    """Status polls, sp-v1 (docs/diagnose.md §7.1): per organization and position, the count and the share of the
    owner's messages, over [--from, --to) (default the last 7 days). --labels FILE checks them against hand labels
    (jsonl: event_id, label); --exclude-unit U judges an organization without unit U's positions."""
    b = of_model.epoch(args.to) if args.to else time.time()
    a = of_model.epoch(args.frm) if args.frm else b - 7 * 86400
    args.at = of_model.iso(b)
    rows_all = _rows(of_model.iso(a - 2 * 86400))
    names = [args.org] if args.org else [o["name"] for o in of_studio.orgs()]
    labelled = {}
    for f in args.labels or []:
        with open(os.path.expanduser(f), encoding="utf-8") as fh:
            labelled.update({x["event_id"]: x.get("label") for x in (json.loads(l) for l in fh if l.strip())})
    out, tot = {}, {"owner_messages": 0, "polls": 0}
    for name in names:
        args.org = name
        _, m, rows = _model(args, rows_all)
        excl = set(of_model.unit_of(m, (m.get("org_units") or {}).get(args.exclude_unit, {}).get("owner"))) \
            if args.exclude_unit and args.exclude_unit in (m.get("org_units") or {}) else None
        if excl is not None:
            excl |= of_diagnose.unit_members(m, args.exclude_unit)[1]
        r = of_measures.status_polls(m, rows, a, b, exclude=excl)
        if labelled:
            r["check"] = of_measures.check_labels(r, {i: l for i, l in labelled.items() if i in r["labels"]})
        out[name] = r
        tot["owner_messages"] += r["owner_messages"]
        tot["polls"] += r["polls"]
    if args.json:
        print(json.dumps({k: {x: y for x, y in v.items() if x != "labels"} for k, v in out.items()}, indent=1, ensure_ascii=False))
        return 0
    print(f"status polls {of_measures.SP} · {of_model.iso(a)[:16]} – {of_model.iso(b)[:16]}"
          + (f" · without unit {args.exclude_unit}" if args.exclude_unit else ""))
    for name, r in out.items():
        print(f"  {name}: {r['polls']} of {r['owner_messages']} owner messages ({r['share']}) · "
              f"instruction with a poll {r['instruction_with_poll']} · system status {r['system_status']}")
        top = ", ".join(f"{p} {v['polls']}" for p, v in list(r["by_position"].items())[:6])
        print(f"    by position: {top or '-'}")
        c = r.get("check")
        if c:
            print(f"    labels: {c['labelled_polls']} polls labelled, sp-v1 {c['sp_polls']} (diff {c['diff']:+d}); "
                  f"agree {c['agree']}, missed {len(c['missed'])}, labelled not a poll {len(c['not_labelled_poll'])}, "
                  f"unlabelled flagged {len(c['unlabelled_flagged'])}")
            for k in ("missed", "not_labelled_poll", "unlabelled_flagged"):
                if c[k]:
                    print(f"      {k}: {' '.join(c[k][:12])}")
    seen = {i for r in out.values() for i in r["labels"]}
    if labelled and any(i not in seen for i in labelled):
        print(f"  labels outside these messages: {' '.join(sorted(i for i in labelled if i not in seen)[:12])}")
    if len(out) > 1:
        print(f"  all: {tot['polls']} of {tot['owner_messages']}"
              f" ({round(tot['polls'] / tot['owner_messages'], 3) if tot['owner_messages'] else None})")
    return 0


def cmd_escalations(args):
    """The escalate_when conditions of delegated units that Studio evaluates (docs/handover.md §4); --write records
    each newly fired one as of.escalation_due, once per (delegation, condition, evidence). of pulse may run it."""
    import of_diagnose
    b = time.time()
    args.at = of_model.iso(b)
    org, m, rows = _model(args, _rows(of_model.iso(b - 9 * 86400), names=of_studio.kinds()))
    diag = of_diagnose.diagnose(m, rows, b - 7 * 86400, b,
                                context={"keeper_since": lambda sysid, k: of_model.keeper_since(org["repo"], sysid, k)})
    # the pulse kills its copy at 60 s (of_platform, 88132a8): the usage refresh reads incrementally in what is left
    left = max(5.0, ESCALATIONS_BUDGET_S - (time.time() - b))
    esc = of_diagnose.escalations(m, diag, of_studio.budget_used(m, rows, b, refresh_usage=True, refresh_budget_s=left))
    done = {((r.get("payload") or {}).get("delegation"), (r.get("payload") or {}).get("condition"),
             tuple((r.get("payload") or {}).get("evidence") or ())) for r in rows if r["name"] == "of.escalation_due"}
    written = 0
    for x in esc:
        key = (x["delegation"], x["condition"], tuple(x["evidence"]))
        if args.write and x["status"] == "fired" and key not in done:
            import of_events
            of_events.emit("escalation_due", {"delegation": x["delegation"], "unit": x["unit"], "condition": x["condition"],
                                              "from_owner": x["to_owner"], "to": x["from_owner"], "evidence": x["evidence"]},
                           position=x["to_owner"], org=org["name"])
            written += 1
    if args.json:
        print(json.dumps(esc, indent=1, ensure_ascii=False))
    else:
        _table([(x["delegation"], x["unit"], x["to_owner"], x["condition"], x["status"], ", ".join(x["evidence"]))
                for x in esc], ["delegation", "unit", "owner", "condition", "status", "evidence"])
        if args.write:
            print(f"written: {written}")
    return 0


def cmd_snapshot(args):
    org, m, _ = _model(args)
    path = of_model.snapshot_path(org["repo"], m)
    text = of_model.snapshot(m)
    if args.dry_run:
        print(f"would write {path} ({len(text)} bytes)")
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(path)
    return 0


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def cmd_gate(args):
    import yaml
    org = _org(args.org)
    base = args.baseline or os.path.join(org["repo"], "reports", "baselines", "founder-load-2026-11-05.yaml")
    with open(base) as fh:
        targets = yaml.safe_load(fh)
    problems = []
    cls = targets.get("classifiers") or {}
    try:
        import of_classify
        pin = (of_classify.__doc__ or "") + json.dumps(getattr(of_classify, "PINNED", {}))
        if cls.get("sha256") and cls["sha256"][:16] not in pin:
            problems.append(f"classifier pin {cls['sha256'][:16]} not found in bin/of_classify.py")
    except ImportError:
        problems.append("bin/of_classify.py is missing")
    pinned = (os.path.join(org["repo"], targets["registry_snapshot"]) if targets.get("registry_snapshot")
              else os.path.join(os.path.dirname(base), f"positions-{str(targets.get('registry_sha256'))[:16]}.yaml"))
    reg_path = args.registry or (pinned if os.path.exists(pinned) else os.path.join(org["repo"], "registry", "positions.yaml"))
    reg_ok = _sha(reg_path) == targets.get("registry_sha256")
    if not reg_ok:
        problems.append(f"registry {reg_path} is not the pinned {str(targets.get('registry_sha256'))[:16]}…: "
                        "throughput_share_undated is reported, not gated")
    with open(reg_path) as fh:
        doc = yaml.safe_load(fh) or {}
    windows = targets.get("windows") or {}
    excl = (targets.get("scope") or {}).get("exclude_cwd") or []
    authors = tuple((targets.get("scope") or {}).get("agent_authors") or ())   # the logins the agents merge under
    first = min(of_model.epoch(w[0]) for w in windows.values())
    last = max(of_model.epoch(w[1]) for w in windows.values())
    rows = _rows(of_model.iso(first - 2 * 86400))
    index = _index(of_model.iso(first), of_model.iso(last))
    failed = passed = blocked = 0
    for wname, (a, b) in windows.items():
        if args.window and args.window != wname:
            continue
        m = of_model.build(doc, rows, at=b, org=org["name"], rev="pinned" if reg_ok else "current", window_h=None)
        obs = of_measures.gate(m, rows, index, a, b, exclude_cwd=excl, agent_authors=authors)
        res = of_measures.check(obs, targets, wname)
        have = of_measures.present(rows, index, of_model.epoch(a), of_model.epoch(b), exclude_cwd=excl) | ({"agent_authors"} if authors else set())
        print(f"== {wname} [{a}, {b})")
        out = []
        for key, sub, tgt, ov, tol, ok in res:
            missing = [n for n in of_measures.NEEDS.get(key, ()) if n not in have]
            gated = not (key == "throughput_share_undated" and not reg_ok)
            if missing:
                verdict = "blocked: no " + ", ".join(missing)
                blocked += 1
            elif ok:
                verdict = "pass"
                passed += 1
            else:
                verdict = "FAIL" if gated else "report"
                failed += gated
            out.append((key + (f".{sub}" if sub else ""), tgt, "-" if missing else ov, json.dumps(tol), verdict))
        _table(out, ["observation", "target", "projected", "tolerance", "result"])
    for p in problems:
        print("note:", p)
    print(f"gate: {passed} pass, {failed} fail, {blocked} blocked on missing inputs"
          + (" (the history import: `of import`, then `of derive`, for Claude Code and Codex)" if blocked else ""))
    return 1 if failed or blocked else 0


def _position_of_session(sid):
    """The position whose registry holder is this session, in any listed organization, else None."""
    for o in of_studio.orgs():
        try:
            doc, _ = of_model.registry_at(o["repo"], time.time())
            m = of_model.build(doc, [], at=time.time(), org=o["name"])
        except Exception:  # noqa: BLE001 — an unreadable registry names nobody
            continue
        pos = dict(of_model.session_index(m["positions"])).get(sid)
        if pos:
            return pos
    return None


def cmd_studio(args):
    """Ask the organization's of-server for a one-time link (POST /v1/studio/link) and open it (docs/studio.md §5.1)."""
    if args.file:
        print(of_studio.write_file(args.file, org=args.org, view=args.view))
        return 0
    if args.render_cache:   # of-server's page cache (docs/studio.md 5.1a); of-server and the listener start it
        import signal
        try:
            os.nice(of_studio.REFRESH_NICE)
        except OSError:
            pass
        signal.alarm(of_studio.REFRESH_MAX_S)   # a hung render dies, and its flock with it
        print(of_studio.render_cache(log=lambda m: print(m, file=sys.stderr)))
        return 0
    import of_client
    import of_ship
    cfg = of_ship.config()
    servers = of_ship.org_servers(cfg) if hasattr(of_ship, "org_servers") else {}
    server = (servers.get(args.org) if args.org else None) or cfg["server_url"]
    try:
        # the page to land on after the one-time open (of-server `next`), so the view asked for is the one recorded
        import urllib.parse
        nxt = f"/studio/{'' if args.view == 'inbox' else args.view}" + (f"?{urllib.parse.urlencode({'org': args.org})}" if args.org else "")
        body = {"next": nxt}
        requester = of_client.studio_requester(position_of_session=_position_of_session)
        if requester:   # an agent asked: its view never counts as the owner's (of-server carries it to of.studio_viewed)
            body["requester"] = requester
        url = of_client.Client(server)._authed("POST", "/v1/studio/link", body=body)[1]["url"]
    except Exception as exc:  # noqa: BLE001 — the server may be down or lack the endpoint
        print(f"of studio: no link from {server}: {exc}. `of studio --file PATH` writes a static page meanwhile.",
              file=sys.stderr)
        return 1
    if args.print:
        print(url)
    else:
        import subprocess
        subprocess.run(["open", url], check=False)
        print("opened a one-time link to Studio (valid 5 minutes, single use)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="of")
    sub = ap.add_subparsers(dest="cmd", required=True)
    mo = sub.add_parser("model", help="the model at time t and the owner measures (read-only; docs/studio.md)")
    mo.add_argument("what", nargs="?", default="summary",
                    choices=["summary", "positions", "systems", "dsm", "dmm", "coverage", "position-dsm",
                             "measures", "snapshot", "gate", "issues", "escalations", "senders", "queues", "works", "polls", "loops", "corrections", "usage",
                             "activity", "evidence", "cells"])
    mo.add_argument("--org")
    mo.add_argument("--at", help="ISO time (UTC); default now")
    mo.add_argument("--json", action="store_true")
    mo.add_argument("--owner", help="measures: the owner (default founder)")
    mo.add_argument("--from", dest="frm", help="measures: window start (default 7 days before --to)")
    mo.add_argument("--to", help="measures: window end (default now)")
    mo.add_argument("--dry-run", action="store_true", help="snapshot: say where it would write")
    mo.add_argument("--baseline", help="gate: the baseline file (default the org's reports/baselines/founder-load-2026-11-05.yaml)")
    mo.add_argument("--registry", help="gate: the pinned registry file, if the working one has moved on")
    mo.add_argument("--window", help="gate: one window, e.g. W7")
    mo.add_argument("--decider", choices=["owner", "structure_keeper", "unit_owner"], help="issues: only those this decider decides")
    mo.add_argument("--for", dest="for_", help="issues: only those this position decides (a structure keeper or a unit owner)")
    mo.add_argument("--all", action="store_true", help="works: also RFCs nobody holds and finished projects")
    mo.add_argument("--goals", action="store_true", help="works: only the goal tree and the conditions (docs/registry.md, Goals)")
    mo.add_argument("--labels", action="append", help="polls: a hand-label file (jsonl: event_id, label); repeatable")
    mo.add_argument("--unit", action="append", help="usage: one delegated unit (repeatable; default every delegated unit)")
    mo.add_argument("--per-day", action="store_true", help="usage: each position's use per day")
    mo.add_argument("--refresh", action="store_true",
                    help=f"usage: read new transcript lines first (for at most {of_studio.USAGE_BUDGET_S} s; the pulse does it too)")
    mo.add_argument("--exclude-unit", help="polls: leave out this unit's positions (a unit judged apart)")
    mo.add_argument("--write", action="store_true", help="escalations: record newly fired ones as of.escalation_due")
    mo.add_argument("--weeks", type=int, default=of_studio.CORRECTION_WEEKS, help="corrections: ISO weeks up to --at")
    mo.add_argument("--days", type=int, default=of_model.CODE_DAYS, help="activity, evidence: days of code up to --at")
    mo.add_argument("--areas", type=int, metavar="DEPTH", help="activity: also per changed directory DEPTH levels deep")
    wk = sub.add_parser("works", prog="of works", help="the works map and each work's state (= of model works; docs/works.md)",
                        description="The works of an organization and each work's state, the rows of Studio's Works view (docs/works.md).")
    wk.add_argument("--org", help="one organization (default: the last one listed in orgs.yaml)")
    wk.add_argument("--json", action="store_true")
    wk.add_argument("--all", action="store_true", help="also RFCs nobody holds and finished projects")
    wk.add_argument("--goals", action="store_true", help="only the goal tree and the conditions (docs/registry.md, Goals; with --json, as JSON)")
    st = sub.add_parser("studio", help="open Org Studio through a one-time link from of-server")
    st.add_argument("--org")
    st.add_argument("--file", help="write one static page instead (stylesheet inlined)")
    st.add_argument("--render-cache", action="store_true",
                    help="compute every organization once and write the pages of-server serves (no link, no view)")
    st.add_argument("--view", default="inbox", choices=[v for v, _ in of_studio.VIEWS])
    st.add_argument("--print", action="store_true", help="print the one-time link instead of opening the browser")
    args = ap.parse_args(argv)
    if args.cmd == "works":  # `of works`: its own prog, so its --help names the command a newcomer types (of_docs, 2026-11-11)
        return cmd_works(args)
    return cmd_model(args) if args.cmd == "model" else cmd_studio(args)


if __name__ == "__main__":
    sys.exit(main())
