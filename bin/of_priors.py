#!/usr/bin/env python3
"""The priors tally (docs/diagnose.md §5.2): each rule's p from its deciders' labels in one organization.

An organization runs it on its side and returns what it prints: per rule, counts and p only, never an issue's text,
id, note, affected positions or case, so its labels stay in it (docs/contours.md §2; of_architect, message
01a205f4-c040, 2026-11-23).

    python3 bin/of_priors.py --org ORG [--json]

It reads this machine's store and the organization's registry, as Studio's page does. The rules of §5.2:
- each issue counts by its latest label (`of_diagnose.labels`);
- only the rule's current version's issues, as `of_diagnose.apply_label` reads a label: one given on the current
  version counts, since that version raised the issue it was given on (a version changes whenever what it raises
  does, docs/diagnose.md "Labels and versions"); one given on a version the current one carries (`labels_carry_from`),
  or on none named, counts when the current version still raises its issue, replayed over the 7 days before the
  label, an issue merged into another (`thresholds.merge_into`) included. A label whose rule was blocked or failed in
  that replay is not judged: the store lacks what the rule reads then, so it counts apart (`replay blocked`), never as
  no longer raised;
- distinct judgements are (label, note): a decider's one note on many issues of one cause is one judgement;
- p is the share of distinct judgements that are not `false_positive`, held at 0.95 or under, and set only from 5 of
  them; m_decide is not set, since a label records no decision time.

Its cost (2026-11-23: the first run was stopped at a user organization's cap of 60 CPU seconds, past 3 GiB, with
nothing printed; of_architect, message 01a205fe-0a3d): only labels on a carried or unnamed version are replayed, one
replay per sitting of them (labels less than an hour apart), which ends at the hour after its last label and runs only
the rules labelled in it; the rows are read once, only the kinds Studio's page and those rules read, through a window
of the 9 days a page reads, slid forward from sitting to sitting, so memory holds one window. Each replay's progress
goes to stderr, and the footer gives the CPU seconds and the peak memory.
"""
import argparse
import json
import math
import os
import resource
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import of_diagnose  # noqa: E402
import of_model  # noqa: E402

P_MAX = 0.95
P_FLOOR = 5          # distinct judgements before p is set
WINDOW_D = 7         # the diagnose window, as Studio's page and `of issues` run it
ROWS_D = 9           # the rows a run reads before its end, as Studio's page loads them
SITTING_GAP_S = 3600  # labels less than this apart are one sitting, judged by one replay


def current(cat, rule):
    """(version, [versions whose labels carry]) of RULE (`ap.<name>`) in the catalogue; None when it has none."""
    spec = (cat.get("rules") or {}).get(str(rule)[3:]) if str(rule).startswith("ap.") else None
    if spec is None:
        return None
    return int(spec.get("version", 0)), [int(v) for v in spec.get("labels_carry_from") or []]


def counts_for(lab, rule, cur):
    """A label applies to the current version: given on it, on a version it carries, or on none named."""
    v, carry = cur
    return lab.get("rule_version") in {None, f"{rule}@{v}"} | {f"{rule}@{c}" for c in carry}


def replayed(d):
    """What a diagnose run tells the tally: {ids: the issue ids it raised, those merged into another issue included
    (their id is their rule's over the host's affected positions, as `merge_into` matches them), blocked: the rules
    it could not judge (blocked, failed, not built)}."""
    ids = set()
    for i in d.get("issues") or []:
        ids.add(i["issue_id"])
        for rv in ((i.get("evidence") or {}).get("merged") or {}):
            ids.add(of_model.issue_id(rv.split("@")[0][3:], i["affected"]))
    return {"ids": ids, "blocked": {r["rule"] for r in d.get("rules") or [] if r.get("status") not in ("fired", "quiet")}}


def hour_after(t):
    return math.ceil(t / 3600) * 3600


def rule_of(lab):
    return lab.get("rule") or str(lab.get("rule_version") or "").split("@")[0]


def needs_replay(lab, cat):
    """A label on a version the current one carries, or on none named: whether the current version still raises its
    issue is a replay's to say. One on the current version needs none, and one on another version does not count."""
    rule = rule_of(lab)
    cur = current(cat, rule)
    return bool(cur) and counts_for(lab, rule, cur) and lab.get("rule_version") != f"{rule}@{cur[0]}"


def sittings(labels, cat):
    """The replays the labels need: ([(b, {rule names})] in time order, {issue id: its b}), for the labels that need
    one (needs_replay); labels less than SITTING_GAP_S apart are one sitting, whose replay ends at the hour after its
    last label and runs the rules labelled in it."""
    todo = sorted((of_model.epoch(lab.get("ts")) or 0, iid, rule_of(lab)) for iid, lab in labels.items()
                  if needs_replay(lab, cat))
    groups, cur = [], []
    for t, iid, rule in todo:
        if cur and t - cur[-1][0] >= SITTING_GAP_S:
            groups.append(cur)
            cur = []
        cur.append((t, iid, rule))
    groups += [cur] if cur else []
    out, end_of = [], {}
    for g in groups:
        b = hour_after(g[-1][0])
        out.append((b, {rule[3:] for _, _, rule in g}))
        end_of.update({iid: b for _, iid, _ in g})
    return out, end_of


def tally(labels, cat, raised, end_of=None):
    """LABELS: {issue id: its latest label payload, with ts}; RAISED(b): `replayed` of the current rules over the
    WINDOW_D days before b, b being END_OF[issue id] (`sittings`), else the hour after the label. Returns ({rule at its
    current version: counts and p}, {labels, gone})."""
    rows, gone = {}, 0
    for iid, lab in labels.items():
        rule = rule_of(lab)
        cur = current(cat, rule)
        if cur is None:
            gone += 1
            continue
        key = f"{rule}@{cur[0]}"
        r = rows.setdefault(key, {"labels": 0, "older_version": 0, "replay_blocked": 0, "not_raised": 0, "counted": 0,
                                  "_j": set()})
        r["labels"] += 1
        if not counts_for(lab, rule, cur):
            r["older_version"] += 1
            continue
        if lab.get("rule_version") == f"{rule}@{cur[0]}":
            run = {"ids": {iid}, "blocked": set()}   # the current version raised it when it was labelled
        else:
            run = raised((end_of or {}).get(iid) or hour_after(of_model.epoch(lab.get("ts")) or 0))
        if rule in run["blocked"]:
            r["replay_blocked"] += 1
        elif iid not in run["ids"]:
            r["not_raised"] += 1
        else:
            r["counted"] += 1
            r["_j"].add((lab.get("label"), " ".join(str(lab.get("note") or "").split())))
    out = {}
    for key in sorted(rows):
        r = rows[key]
        js = r.pop("_j")
        fp = sum(1 for k, _ in js if k == "false_positive")
        p = round(min(P_MAX, (len(js) - fp) / len(js)), 2) if len(js) >= P_FLOOR else None
        out[key] = dict(r, distinct=len(js), false_positive=fp, p=p,
                        p_now=cat["rules"][key.split("@")[0][3:]].get("p"))
    return out, {"labels": len(labels), "gone": gone}


def table(out, meta):
    """The table to return: per rule, counts and p only."""
    head = ["rule", "labels", "older version", "replay blocked", "not raised now", "counted", "distinct", "false pos.",
            "p", "p now"]
    body = [[k, r["labels"], r["older_version"], r["replay_blocked"], r["not_raised"], r["counted"], r["distinct"],
             r["false_positive"],
             "too few" if r["p"] is None else f'{r["p"]:g}',
             json.dumps(r["p_now"], separators=(",", ":")) if isinstance(r["p_now"], dict) else r["p_now"]]
            for k, r in out.items()]
    w = [max(len(str(x)) for x in col) for col in zip(head, *body)]
    lines = ["  ".join(str(x).ljust(n) for x, n in zip(row, w)).rstrip() for row in [head] + body]
    lines.append(f'org {meta["org"]} · {meta["labels"]} labelled issues · {meta["gone"]} on rules no longer in the '
                 f'catalogue · {meta["replays"]} replays · at {meta["at"]} · p from {P_FLOOR} distinct judgements, '
                 f'at most {P_MAX}' + (f' · {meta["cpu_s"]} CPU s, peak {meta["peak_mib"]} MiB' if "cpu_s" in meta else ""))
    return "\n".join(lines)


def model_kinds(of_studio):
    """The kinds Studio's model reads beside the rules' (of_studio.kinds() without of_diagnose.kinds_needed())."""
    import of_asks
    try:
        import of_loops
        loop = set(of_loops.KINDS)
    except ImportError:
        loop = set()
    return set(of_studio.STUDIO_KINDS) | set(getattr(of_asks, "READS", ())) | loop


def main(argv=None):
    ap = argparse.ArgumentParser(prog="of_priors.py", description="docs/diagnose.md §5.2: each rule's p from its "
                                 "labels; prints per-rule counts and p only")
    ap.add_argument("--org", required=True, help="the organization, as ~/.config/of/orgs.yaml names it")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    import of_store
    import of_studio
    import of_studio_cli
    all_orgs = of_studio.orgs()
    org = next((o for o in all_orgs if o["name"] == a.org), None)
    if org is None:
        print(f"of_priors.py: {a.org} is not in this machine's orgs.yaml", file=sys.stderr)
        return 2
    others = [o for o in all_orgs if o["name"] != org["name"]]
    conn = of_store.connect()
    cat = of_diagnose.catalogue()
    labels = of_diagnose.labels(of_studio.org_rows(of_model.load_events(conn, names=["of.issue_labelled"]), org, others))
    plan, end_of = sittings(labels, cat)
    labelled = set().union(*(r for _, r in plan)) if plan else set()
    kinds = model_kinds(of_studio) | set(of_diagnose.kinds_needed(dict(cat, rules={n: cat["rules"][n] for n in labelled})))
    results, window, seen, loaded_to = {}, [], set(), None
    for b, rules in plan:   # in time order: the window slides forward, each row read once
        lo = b - ROWS_D * 86400
        fresh = of_model.load_events(conn, since=of_model.iso(max(lo, loaded_to or lo)), until=of_model.iso(b),
                                     names=sorted(kinds))
        loaded_to = b
        # load_events adds every of.prompt_origin since the window's start, whatever its end: kept once each
        window = [r for r in window if (r.get("_t") or 0) >= lo]
        for r in of_studio.org_rows(fresh, org, others):
            if r["event_id"] not in seen:
                seen.add(r["event_id"])
                window.append(r)
        seen = {r["event_id"] for r in window}
        window.sort(key=lambda r: (r.get("_t") or 0))
        org_cfg, m, rows = of_studio_cli._model(argparse.Namespace(org=a.org, at=of_model.iso(b)), window)
        ctx = {"keeper_since": lambda sysid, k, repo=org_cfg["repo"]: of_model.keeper_since(repo, sysid, k)}
        d = of_diagnose.diagnose(m, rows, b - WINDOW_D * 86400, b, cat=dict(cat, rules={n: cat["rules"][n] for n in rules}),
                                 context=ctx)
        results[b] = replayed(d)
        del m, rows, d
        ru = resource.getrusage(resource.RUSAGE_SELF)
        print(f"replay {len(results)}/{len(plan)}: {len(window)} rows, {len(rules)} rules, "
              f"{round(ru.ru_utime + ru.ru_stime, 1)} CPU s, peak {ru.ru_maxrss // 1024} MiB", file=sys.stderr, flush=True)
    out, meta = tally(labels, cat, results.__getitem__, end_of)
    ru = resource.getrusage(resource.RUSAGE_SELF)
    meta.update(org=a.org, replays=len(results), at=of_model.iso(time.time()),
                cpu_s=round(ru.ru_utime + ru.ru_stime, 1), peak_mib=ru.ru_maxrss // 1024)
    print(json.dumps({"rules": out, **meta}, indent=1) if a.json else table(out, meta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
