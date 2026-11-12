"""The goal tree and the conditions (docs/registry.md, Goals and Conditions; hand-over b1b627f3 from of_architect).

check(goals, conditions, works, warn): the lines `of registry check` reports for an organization's `goals:`,
`conditions:` and each work's `goal` or `condition`; a warning starts with `warn`.
tree(goals): the goals in rank order, depth first, as (depth, id, goal), for `of works` and Studio.
"""

import datetime
import re

ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")  # the same rule as a work's id
GOAL_STATES = ("active", "achieved", "dropped")
CONDITION_STATES = ("active", "dropped")
BUDGET_KEYS = {"tokens_month", "usd_month"}
FEW = 7  # more active ones at one level is a warning: a goal list is a choice, not an inventory
OPEN_WORK = lambda w: (w or {}).get("state") not in ("done", "parked")  # noqa: E731


def _text(v):
    return isinstance(v, str) and bool(v.strip())


def _date(v):
    return isinstance(v, datetime.date) or (isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v.strip()))


def _rank(v):
    return isinstance(v, int) and not isinstance(v, bool)


def children(goals):
    out = {}
    for gid, g in goals.items():
        if isinstance(g, dict) and g.get("parent") is not None:
            out.setdefault(str(g["parent"]), []).append(str(gid))
    return out


def check(goals, conditions, works, warn="warning: "):
    goals = goals if goals is not None else {}
    conditions = conditions if conditions is not None else {}
    out = []
    if not isinstance(goals, dict):
        out.append("goals: must be a mapping of goal id -> goal")
        goals = {}
    if not isinstance(conditions, dict):
        out.append("conditions: must be a mapping of condition id -> condition")
        conditions = {}
    goals = {str(k): v for k, v in goals.items()}
    conditions = {str(k): v for k, v in conditions.items()}
    for cid in sorted(set(goals) & set(conditions)):
        out.append(f"conditions: {cid}: the id is also a goal's; ids are unique across goals and conditions")
    kids = children(goals)
    siblings = {}
    for gid, g in goals.items():
        tag = f"goals: {gid}"
        if not ID.match(gid):
            out.append(f"{tag}: the id must match {ID.pattern}")
        if not isinstance(g, dict):
            out.append(f"{tag}: must be a mapping")
            continue
        parent = g.get("parent")
        if parent is not None:
            parent = str(parent)
            if parent == gid:
                out.append(f"{tag}: parent is the goal itself")
            elif parent not in goals:
                out.append(f"{tag}: parent {parent!r} is not a goal")
        if not _rank(g.get("rank")):
            out.append(f"{tag}: rank must be a whole number, unique among its siblings (1 matters most)")
        else:
            siblings.setdefault(parent if parent in goals else None, []).append((g["rank"], gid))
        if g.get("state") not in GOAL_STATES:
            out.append(f"{tag}: state {g.get('state')!r} is not one of {', '.join(GOAL_STATES)}")
        if not _text(g.get("decided")):
            out.append(f"{tag}: decided is required: the id of the ask whose yes holds this text")
        if not _text(g.get("measure")):
            out.append(f"{tag}: measure is required: how success is seen")
        if parent is not None and not _text(g.get("target")) and not isinstance(g.get("target"), (int, float)):
            out.append(f"{tag}: target is required on a sub-goal: the level that counts as success")
        if g.get("by") is not None and not _date(g["by"]):
            out.append(f"{tag}: by is a date (YYYY-MM-DD)")
        p = goals.get(parent) if parent else None
        if g.get("state") == "active" and isinstance(p, dict) and p.get("state") in ("achieved", "dropped"):
            out.append(f"{warn}{tag}: active under {parent}, which is {p['state']}: a closed parent closes its branch")
    for kind, nodes in (("goals", goals), ("conditions", conditions)):  # docs/registry.md, Budgets
        for nid, n in nodes.items():
            b = n.get("budget") if isinstance(n, dict) else None
            if b is None:
                continue
            tag = f"{kind}: {nid}: budget"
            if not isinstance(b, dict) or not b:
                out.append(f"{tag} is a mapping of tokens_month and/or usd_month")
                continue
            for k in sorted(set(b) - BUDGET_KEYS):
                out.append(f"{tag}: unknown key {k!r} (tokens_month, usd_month)")
            for k in sorted(set(b) & BUDGET_KEYS):
                if not isinstance(b[k], (int, float)) or isinstance(b[k], bool) or b[k] <= 0:
                    out.append(f"{tag}.{k} must be a positive number")
    for parent, ids in sorted(kids.items()):  # sub-budgets are parts of the parent's
        pb = (goals.get(parent) or {}).get("budget") if isinstance(goals.get(parent), dict) else None
        if not isinstance(pb, dict):
            continue
        for k in sorted(BUDGET_KEYS):
            parts = [((goals[i] or {}).get("budget") or {}).get(k) for i in ids if isinstance(goals.get(i), dict)
                     and isinstance((goals[i] or {}).get("budget"), dict)]
            parts = [x for x in parts if isinstance(x, (int, float)) and not isinstance(x, bool)]
            if isinstance(pb.get(k), (int, float)) and parts and sum(parts) > pb[k]:
                out.append(f"{warn}goals: {parent}: its sub-goals' budgets add up to {sum(parts):,} {k}, more than "
                           f"its own {pb[k]:,}")
    for gid in goals:  # a cycle: following parents comes back to the goal
        seen, cur = [], gid
        while isinstance(goals.get(cur), dict) and goals[cur].get("parent") is not None and cur not in seen:
            seen.append(cur)
            cur = str(goals[cur]["parent"])
        if cur in seen and cur == gid:
            out.append(f"goals: {gid}: its parents form a cycle ({' -> '.join(seen + [gid])})")
    for parent, ranks in sorted(siblings.items(), key=lambda x: str(x[0])):
        dup = sorted({r for r, _ in ranks if sum(1 for x, _ in ranks if x == r) > 1})
        for r in dup:
            who = ", ".join(sorted(g for x, g in ranks if x == r))
            out.append(f"goals: rank {r} is held by {who} " + (f"under {parent}" if parent else "among the root goals"))
    active = lambda ids: [i for i in ids if isinstance(goals.get(i), dict) and goals[i].get("state") == "active"]  # noqa: E731
    roots = active([gid for gid, g in goals.items() if isinstance(g, dict) and g.get("parent") is None])
    if len(roots) > FEW:
        out.append(f"{warn}goals: {len(roots)} active root goals, more than {FEW}: a goal list is a choice")
    for parent, ids in sorted(kids.items()):
        if len(active(ids)) > FEW:
            out.append(f"{warn}goals: {parent}: {len(active(ids))} active sub-goals, more than {FEW}")
    for cid, c in conditions.items():
        tag = f"conditions: {cid}"
        if not ID.match(cid):
            out.append(f"{tag}: the id must match {ID.pattern}")
        if not isinstance(c, dict):
            out.append(f"{tag}: must be a mapping")
            continue
        for k, what in (("measure", "how it is seen"), ("level", "the level held"),
                        ("decided", "the id of the ask whose yes holds this text")):
            if not _text(c.get(k)):
                out.append(f"{tag}: {k} is required: {what}")
        if c.get("state") not in CONDITION_STATES:
            out.append(f"{tag}: state {c.get('state')!r} is not one of {', '.join(CONDITION_STATES)}")
        if c.get("under") is not None and str(c["under"]) not in goals:
            out.append(f"{tag}: under {c['under']!r} is not a goal")
        for k in ("rank", "parent"):
            if k in c:
                out.append(f"{tag}: a condition has no {k} (it is held at its level, not ranked or decomposed)")
    if len([c for c in conditions.values() if isinstance(c, dict) and c.get("state") == "active"]) > FEW:
        out.append(f"{warn}conditions: more than {FEW} active conditions")
    for wid, w in (works or {}).items() if isinstance(works, dict) else ():
        if not isinstance(w, dict):
            continue
        tag, g, c = f"works: {wid}", w.get("goal"), w.get("condition")
        if g is not None and c is not None:
            out.append(f"{tag}: names both goal {g!r} and condition {c!r}: a work keeps a condition or moves a goal")
        if g is not None:
            if str(g) not in goals:
                out.append(f"{tag}: goal {g!r} is not a goal")
            elif active(kids.get(str(g)) or []):  # closed sub-goals leave a leaf (of_review on PR 70)
                out.append(f"{warn}{tag}: goal {g} has sub-goals; attach the work to the sub-goal it moves")
        if c is not None and str(c) not in conditions:
            out.append(f"{tag}: condition {c!r} is not a condition")
        if g is None and c is None and (goals or conditions) and OPEN_WORK(w):
            out.append(f"{warn}{tag}: names no goal or condition while the organization has them (a question for "
                       "the owner)")
    return out


def tree(goals):
    """[(depth, id, goal)]: roots by rank, each followed by its sub-goals by rank, depth first."""
    goals = {str(k): v for k, v in (goals or {}).items() if isinstance(v, dict)}
    kids = children(goals)
    order = lambda ids: sorted(ids, key=lambda i: (goals[i].get("rank") if _rank(goals[i].get("rank")) else 1 << 30, i))  # noqa: E731
    out, seen = [], set()

    def walk(gid, depth):
        if gid in seen:  # a cycle is the check's problem; the tree only refuses to loop
            return
        seen.add(gid)
        out.append((depth, gid, goals[gid]))
        for k in order([k for k in kids.get(gid, []) if k in goals]):
            walk(k, depth + 1)
    for r in order([g for g, x in goals.items() if x.get("parent") is None or str(x["parent"]) not in goals]):
        walk(r, 0)
    return out
