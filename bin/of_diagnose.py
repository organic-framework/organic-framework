"""Diagnose v0, the run: the anti-pattern catalogue as projections (docs/diagnose.md; RFC 0001 §5.4). Owner: of_studio.

Pure functions over MODEL(t) (bin/of_model.py), typed event rows and a window. Thresholds, priors and inputs come from
one data file, docs/diagnose-v0.yaml; the rules' logic is bin/of_diagnose_rules.py. Both are of_field-analyst's; this
module only runs them: it checks each rule's inputs, applies the owner's labels, routes issues to units, merges and ranks.

  catalogue()                      the parsed YAML
  diagnose(model, rows, a, b)      {"issues": [...], "rules": [{rule, version, status, missing, issues}]}

A rule's status: `fired` (raised issues), `quiet` (inputs present, nothing found), `blocked` (an input it needs is
empty in the window; it raises nothing), or `not_built` (inputs present, logic not written yet). Issues are a
projection, never stored; the owner's labels (`of.issue_labelled`) are the only stored part. The issue record
(of_model.issue, issue_id) is the contract between a rule and this run.
"""

import os
import re

import of_diagnose_rules
import of_model
from of_model import MANAGEMENT_FUNCTIONS, epoch, issue, issue_id, structure_keeper  # noqa: F401 (the issues API)

RULES = of_diagnose_rules.RULES

CATALOGUE = os.environ.get("OF_DIAGNOSE") or os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "docs",
                                                          "diagnose-v0.yaml")
_cache = {}


def catalogue(path=None):
    import yaml
    path = path or CATALOGUE
    mtime = os.path.getmtime(path)
    if _cache.get(path, (None,))[0] != mtime:
        with open(path) as fh:
            _cache[path] = (mtime, yaml.safe_load(fh) or {})
    return _cache[path][1]


def kinds_needed(cat=None):
    """Every event kind any rule reads (for the loaders' name filter): its `needs`, and its `reads` (kinds it uses
    when present, never a reason to block it; of_field-analyst, 2026-11-11)."""
    try:
        cat = cat or catalogue()
    except OSError:
        return ()
    out = {"of.issue_labelled", "of.mandate_check"}
    for r in (cat.get("rules") or {}).values():
        needs = r.get("needs") or []
        for n in (sum(needs.values(), []) if isinstance(needs, dict) else list(needs)) + list(r.get("reads") or []):
            if str(n).startswith("of."):
                out.add(n)
    return tuple(sorted(out))


# ------------------------------------------------------------------ inputs present

def present(model, rows, a, b, cat):
    """The names of the inputs present in [a, b): event kinds, registry paths, wr.position, documents."""
    have = {r["name"] for r in rows if of_model.in_window(r, a, b)}
    systems, positions = model["systems"], model["positions"]
    if any(s.get("depends_on") for s in systems.values()):
        have.add("registry.systems.depends_on")
    if any(s.get("keeper") for s in systems.values()):
        have.add("registry.systems.keeper")
    if any(p.get("exit") for p in positions.values()):
        have.update(("registry.mandate_exit", "registry.mandate.exit"))
    if any(p.get("functions") for p in positions.values()):
        have.update(("registry.mandate_functions", "registry.mandate.functions"))
    loops = (model.get("loops") or {}).values()
    if any(x.get("declared") for x in loops):
        have.add("registry.works.loop")
    if any(a <= (r.get("ended_at") or 0) < b for x in loops for r in x.get("runs") or []):
        have.add("of.loop_run")   # a run that ended in the window (RFC 0005; docs/diagnose.md §4.1)
    else:
        have.discard("of.loop_run")
    wr = model.get("work_records") or {}
    share_min = (cat.get("defaults") or {}).get("attributed_share_min", 0.5)
    if wr.get("total") and wr.get("attributed", 0) / wr["total"] >= share_min:
        have.add("wr.position")
    return have


def _needs(spec):
    n = spec.get("needs") or []
    return sorted(set(sum(n.values(), []))) if isinstance(n, dict) else list(n)


# ------------------------------------------------------------------ issues

def rank_group(i):
    """0 critical; 1 ranked by score (ΔT per owner minute known, > 0); 2 risks without a ΔT yet (diagnose.md §3)."""
    return 0 if i.get("critical") else (1 if (i.get("score") or 0) > 0 else 2)


def evidence_age(i):
    ev = i.get("evidence") or {}
    return ev.get("since") or ev.get("first_ts") or ""


def rank_key(i):
    sev = SEVERITY.index(i.get("severity", "medium")) if i.get("severity", "medium") in SEVERITY else 1
    return (rank_group(i), -(i.get("score") or 0) if rank_group(i) == 1 else -sev, evidence_age(i), i["rule"], i["affected"])


# ------------------------------------------------------------------ labels and the run

def labels(rows):
    """{issue_id: latest of.issue_labelled payload}."""
    out = {}
    for r in sorted(rows, key=lambda r: epoch(r.get("ts")) or 0):
        if r["name"] == "of.issue_labelled":
            pl = r.get("payload") or {}
            if pl.get("issue_id"):
                out[pl["issue_id"]] = dict(pl, ts=r["ts"])
    return out


def apply_label(i, lab, now, carry=()):
    """carry: earlier versions whose labels still apply (the rule's labels_carry_from: detection unchanged)."""
    if not lab:
        return i
    kind = lab.get("label")
    valid = {None, i["rule_version"]} | {f"{i['rule']}@{v}" for v in carry}
    if lab.get("rule_version") not in valid:
        return i   # a label on another version: the new detection is judged anew
    if kind == "false_positive":
        i["state"] = "dismissed"
    elif kind == "true_but_not_now" and now - (epoch(lab.get("ts")) or 0) < 7 * 86400:
        i["state"] = "snoozed"
    elif kind == "wrong_remedy":
        i["wrong_remedy"] = True
    i["label"] = kind
    return i


def decider_position(model, i, keeper):
    """Who decides: the structure keeper; for a loop rule (RFC 0005 §6), the work's doers (`doer`, the pulse nudges
    them) or its owner (`work_owner`), from the issue's `work` or `evidence.work`."""
    if i["decider"] == "structure_keeper":
        return keeper
    if i["decider"] in ("doer", "work_owner"):
        lp = (model.get("loops") or {}).get(i.get("work") or (i.get("evidence") or {}).get("work")) or {}
        return (", ".join(lp.get("doers") or []) or None) if i["decider"] == "doer" else lp.get("owner")
    return None


def mark_unconfirmed(model, i):
    """Systems awaiting the owner's confirm pass (registry `confirm`): `registry_unconfirmed` with the doubts, and
    `keeper_unconfirmed` only when `confirm` lists `keeper` and the system has one. The rules use the entry as is."""
    doubts, what, keeper = {}, set(), False
    for x in i["affected"]:
        s = model["systems"].get(x) or {}
        c = s.get("confirm")
        if not c:
            continue
        doubts[x] = s.get("doubt") or ""
        if isinstance(c, list):   # what awaits confirmation: keeper | boundary | live (docs/registry.md)
            what |= {str(v) for v in c}
            if "keeper" in c and s.get("keeper"):
                keeper = True
    if doubts:
        ev = dict(i.get("evidence") or {}, registry_unconfirmed=True, unconfirmed=doubts)
        if what:
            ev["unconfirmed_what"] = sorted(what)
        if keeper:
            ev["keeper_unconfirmed"] = True
        i["evidence"] = ev
    return i


def route_to_unit(model, i):
    """An owner-decided issue that lies wholly inside one delegated unit is the unit owner's to decide (the delegator
    sees it rolled up, and gets it only through the unit's escalate_when)."""
    if i.get("decider") != "owner" or i.get("critical"):
        return i
    units = {uid: unit_members(model, uid) for uid, u in (model.get("org_units") or {}).items() if u.get("delegation")}
    for uid, (systems, positions) in units.items():
        inside = set(systems) | set(positions)
        if i["affected"] and all(x in inside for x in i["affected"]):
            owner = model["org_units"][uid]["owner"]
            i.update(decider="unit_owner", decider_position=owner, unit=uid)
            break
    return i


def diagnose(model, rows, a, b, cat=None, context=None):
    """context: optional {keeper_since: fn(system, keeper) -> epoch | None} from the organization's registry history;
    code_activity: fn() -> of_model.code_activity over the last 30 days (RFC 0007 §2.2), computed on first call."""
    a, b = epoch(a), epoch(b)
    try:
        cat = cat or catalogue()
    except OSError as exc:
        return {"issues": [], "rules": [], "error": f"no catalogue: {exc}"}
    have = present(model, rows, a, b, cat)
    labs = labels(rows)
    issues, status = [], []
    keeper = structure_keeper(model)
    for name, spec in (cat.get("rules") or {}).items():
        needs = spec.get("needs") or []
        if isinstance(needs, dict):   # a rule in parts: run the parts whose inputs are present
            part_missing = {k: [n for n in v if n not in have] for k, v in needs.items()}
            ok = tuple(k for k, v in part_missing.items() if not v)
            missing = [] if ok else sorted({n for v in part_missing.values() for n in v})
        else:
            part_missing, ok = {}, None
            missing = [n for n in needs if n not in have]
        row = {"rule": f"ap.{name}", "version": f"ap.{name}@{spec.get('version', 0)}", "missing": missing, "issues": 0,
               "parts_blocked": {k: v for k, v in part_missing.items() if v}}
        if missing:
            row["status"] = "blocked"
        elif name not in RULES:
            row["status"] = "not_built"
        else:
            fn = RULES[name]
            kw = {"context": context} if "context" in fn.__code__.co_varnames else {}
            res = fn(model, rows, a, b, cat, spec, ok, **kw) if ok is not None else fn(model, rows, a, b, cat, spec, **kw)
            notes = []
            if isinstance(res, tuple):
                res, extra, *rest = res
                notes = rest[0] if rest else []
            else:
                extra = []
            row["notes"] = notes
            carry = spec.get("labels_carry_from") or []
            found = [apply_label(i, labs.get(i["issue_id"]), b, carry) for i in res]
            for i in found:
                i["decider_position"] = decider_position(model, i, keeper)
                route_to_unit(model, i)
                mark_unconfirmed(model, i)
            row["issues"] = sum(i["state"] == "open" for i in found)
            row["status"] = "fired" if row["issues"] else ("blocked" if extra else "quiet")
            row["missing"] = extra
            issues += found
        status.append(row)
    issues = merge_into(cat, issues, status)
    issues.sort(key=rank_key)
    return {"issues": issues, "rules": status, "catalogue": cat.get("catalogue"), "structure_keeper": keeper}


def merge_into(cat, issues, status):
    """thresholds.merge_into: an issue of this rule on the same positions as an open issue of that rule becomes
    evidence on it (evidence.merged[<rule>]), not a second issue."""
    out, by = [], {(i["rule"], tuple(i["affected"])): i for i in issues if i["state"] == "open"}
    for i in issues:
        into = ((cat["rules"].get(i["rule"][3:]) or {}).get("thresholds") or {}).get("merge_into")
        host = by.get((f"ap.{into}", tuple(i["affected"]))) if into else None
        if host is None:
            out.append(i)
            continue
        host.setdefault("evidence", {}).setdefault("merged", {})[i["rule_version"]] = dict(i["evidence"], part=i.get("part"))
        for row in status:
            if row["rule"] == i["rule"] and i["state"] == "open":
                row["issues"] -= 1
                row.setdefault("notes", []).append(f"{','.join(i['affected'])}: merged into {host['rule']}")
                if not row["issues"] and row.get("status") == "fired":
                    row["status"] = "quiet"
    return out


# ------------------------------------------------------------------ escalation of delegated units (handover.md §4)

SEVERITY = ("low", "medium", "high", "critical")
STUDIO_CONDITIONS = ("issue.severity", "budget_used", "envelope breach")   # the rest are of pulse's or the tagger's


def severity(i):
    """v0: critical issues are `critical`; others carry the catalogue's `severity` if set, else `medium`."""
    return "critical" if i.get("critical") else (i.get("severity") or "medium")


def unit_members(model, uid):
    u = (model.get("org_units") or {}).get(uid) or {}
    rec = u.get("delegation") or {}
    systems = set(rec.get("systems") or u.get("systems") or [])
    positions = set(rec.get("positions") or []) | {n for n, p in model["positions"].items() if p.get("unit") == uid}
    return systems, positions


def escalations(model, diag, budget_used=None):
    """For each delegated unit, the escalate_when conditions Studio evaluates: issue.severity >= X, budget_used > N%,
    envelope breach. Rows {delegation, unit, from_owner, to_owner, condition, status: fired | quiet | not_measured,
    evidence}. Budgets and envelope use are not recorded yet, so those are not_measured, never quiet."""
    out = []
    for uid, u in (model.get("org_units") or {}).items():
        rec = u.get("delegation")
        if not rec:
            continue
        systems, positions = unit_members(model, uid)
        mine = [i for i in diag.get("issues") or [] if i["state"] == "open"
                and (i.get("unit") == uid or set(i["affected"]) & (systems | positions))]
        for cond in ((rec.get("reporting") or {}).get("escalate_when") or []):
            c = str(cond).strip()
            if not c.startswith(STUDIO_CONDITIONS):
                continue
            row = {"delegation": rec.get("id"), "unit": uid, "from_owner": rec.get("from_owner"),
                   "to_owner": rec.get("to_owner"), "condition": c, "status": "not_measured", "evidence": []}
            mb = re.match(r"budget_used\s*>\s*(\d+(?:\.\d+)?)%", c)
            used = (budget_used or {}).get(uid) or {}
            if mb and (used.get("share") is not None or used.get("usd_share") is not None):
                lim = float(mb.group(1))
                ev = []
                if used.get("share") is not None:
                    ev.append(f"{round(100 * used['share'], 1)}% of {used['budget']:,} tokens this month")
                if used.get("usd_share") is not None:
                    ev.append(f"{round(100 * used['usd_share'], 1)}% of ${used['usd_budget']:,} this month")
                fired = any(x is not None and x * 100 > lim for x in (used.get("share"), used.get("usd_share")))
                row.update(status="fired" if fired else "quiet", evidence=ev)
            m = re.match(r"issue\.severity\s*>=\s*(\w+)", c)
            if m and m.group(1) in SEVERITY:
                floor = SEVERITY.index(m.group(1))
                hit = [i for i in mine if SEVERITY.index(severity(i)) >= floor]
                row.update(status="fired" if hit else "quiet", evidence=sorted(i["issue_id"] for i in hit))
            out.append(row)
    return out


def restrict(diag, names, systems):
    """A unit owner's issues: those that touch its positions or systems."""
    keep = set(names) | set(systems)
    return dict(diag, issues=[i for i in diag["issues"] if keep & set(i["affected"])])