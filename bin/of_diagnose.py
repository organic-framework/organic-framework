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


def unlabelled(i):
    """Issue I as a rule raised it, before any label: open, with no label, wrong remedy or uncounted label."""
    for k in ("label", "wrong_remedy", "label_not_counted", "owner_labels_only"):
        i.pop(k, None)
    i["state"] = "open"
    return i


def label_issue(i, history, keeper, now, carry=()):
    """Apply to issue I the latest label of HISTORY that counts (may_label); a newer one that does not is kept on the
    issue as `label_not_counted` {by, label, ts, why}, so the list shows it."""
    lab, skipped = counted_label(history, i, keeper)
    apply_label(i, lab, now, carry)
    if skipped:
        i["label_not_counted"] = {"by": skipped.get("by"), "label": skipped.get("label"), "ts": skipped.get("ts"),
                                  "why": may_label(skipped.get("by"), i, _keeper_of(keeper, skipped))}
    why = owner_only(i)
    if why:
        i["owner_labels_only"] = why
    else:
        i.pop("owner_labels_only", None)
    return i


def label_writer(r, sessions=None):
    """Who wrote label row R, by what the store records about the caller, never by the payload's word alone (of_review
    on organic-framework PR 630): the position holding the event's session (SESSIONS: fn(session id) -> position or
    None), else the event's own position, else, with neither recorded, the owner when the payload says so or names
    none (`of issues label` records no position for the owner). A payload naming a position the event does not record,
    or a session no position holds, is a writer no rule counts."""
    pl = r.get("payload") if isinstance(r.get("payload"), dict) else {}
    sid, pos = r.get("session_id"), r.get("position")
    if sid and sessions:
        who = sessions(sid)
        if who:
            return who
    if pos:
        return pos
    if sid:
        return f"session {str(sid)[:8]}, which no position holds"
    by = pl.get("by")
    return "owner" if by in (None, "owner") else f"{by}, named in the payload only"


def label_history(rows, sessions=None):
    """{issue_id: [of.issue_labelled payload with its ts and `by`, oldest first]}: `by` is label_writer's, and the
    payload's own word, when it differs, is kept as `by_claimed`."""
    out = {}
    for r in sorted(rows, key=lambda r: epoch(r.get("ts")) or 0):
        if r["name"] == "of.issue_labelled":
            pl = r.get("payload") or {}
            if isinstance(pl, dict) and isinstance(pl.get("issue_id"), str) and pl["issue_id"]:
                by = label_writer(r, sessions)
                x = dict(pl, ts=r["ts"], by=by)
                if pl.get("by") not in (None, by):
                    x["by_claimed"] = pl["by"]
                out.setdefault(pl["issue_id"], []).append(x)
    return out


def keeper_then(model, context, keeper):
    """fn(label) -> the structure keeper when the label was written: context's keeper_at(system, t) on the creating
    system, else (history unknown) the keeper now, KEEPER."""
    at = (context or {}).get("keeper_at")
    creating = next((sid for sid, s in (model.get("systems") or {}).items()
                     if s.get("kind") == "creating" and s.get("keeper")), None)
    memo = {}

    def keeper_for(lab):
        if not (at and creating):
            return keeper
        t = epoch(lab.get("ts")) or 0
        if t not in memo:
            memo[t] = at(creating, t) or keeper
        return memo[t]
    return keeper_for


def session_writer(model, rows):
    """fn(session id) -> the position holding it: the registry's holders first, then the sessions the rows name with a
    registered position (of_model.session_positions), read only when a label names a session the holders do not."""
    memo = {}

    def who(sid):
        if "holders" not in memo:
            memo["holders"] = of_model.session_index(model.get("positions") or {})
        if sid in memo["holders"]:
            return memo["holders"][sid]
        if "rows" not in memo:
            memo["rows"] = of_model.session_positions(model, rows)
        return memo["rows"].get(sid)
    return who


def may_label(by, i, keeper):
    """None when BY's label counts on issue I, else why it does not. The owner labels any issue; the organization's
    structure keeper (KEEPER), the analyst the owner delegates labelling to, and the position the issue routes to
    (`decider_position`: the keeper or a unit owner) label it unless the issue names them in `affected`, where only
    the owner does. Since a label holds over all time, one from any other session, the issue's own subject included,
    would take an issue out of the inbox, the keeper digest and the escalations for good (of_review on organic-framework
    PR 630)."""
    if by == "owner":
        return None
    if by in (i.get("affected") or []):
        return f"the issue names {by}, so only the owner labels it"
    if by == keeper or by in deciders(i):
        return None
    return (f"{by} is neither the owner, the structure keeper ({keeper or 'none'}) nor the position the issue routes to"
            f" ({i.get('decider_position') or 'none'})")


def deciders(i):
    """The positions issue I routes to: `decider_position`, which joins a shared work's doers with ", "."""
    return [x for x in str(i.get("decider_position") or "").split(", ") if x]


OWNER_ONLY_WAY_OUT = ("its decider brings the remedy as a diff, or, if the rule misfired, tells the rule's keeper "
                      "(of_field-analyst, docs/diagnose.md; from another organization through OF's support channel, "
                      "docs/support.md), so a fixed rule stops raising it; the owner's label stays the owner's own "
                      "choice, never asked for")


def owner_only(i):
    """Why only the owner can label issue I though it routes to a position (the issue names its decider), with the
    decider's ways out, or None. Its routing stays (of_architect, 2026-11-24, message 01a20d5c-f970): the decider
    brings the remedy as a diff, or tells the rule's keeper that the rule misfired, so a false positive about the
    keeper does not wait on the founder; it never asks the owner for the label."""
    named = [x for x in deciders(i) if x in (i.get("affected") or [])]
    return (f"it names {', '.join(named)}, the position it routes to; {OWNER_ONLY_WAY_OUT}"
            if named and i.get("decider") != "owner" else None)


def _keeper_of(keeper, x):
    """KEEPER: a name, or fn(label) -> the structure keeper when label X was written (diagnose's keeper_for)."""
    return keeper(x) if callable(keeper) else keeper


def counted_label(history, i, keeper):
    """(the latest label in HISTORY that counts on issue I, or None; the latest that does not, when newer than it).
    KEEPER: the structure keeper, or fn(label) -> the keeper when it was written."""
    lab = skipped = None
    for x in history or ():
        if may_label(x.get("by"), i, _keeper_of(keeper, x)) is None:
            lab, skipped = x, None
        else:
            skipped = x
    return lab, skipped


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


EITHER = "unit_owner_or_structure_keeper"   # a catalogue decider: the unit owner inside a delegated unit, else the
# structure keeper (of_architect moved six rules' issues from the founder to the keeper, msg 01a1dc76-ed6e, 2026-11-15)


def route_to_unit(model, i, keeper=None):
    """An owner-decided issue that lies wholly inside one delegated unit is the unit owner's to decide (the delegator
    sees it rolled up, and gets it only through the unit's escalate_when). A rule whose decider is
    `unit_owner_or_structure_keeper` routes the same way, a critical one excepted; an issue it does not route becomes
    the structure keeper's (KEEPER, else the model's)."""
    either = i.get("decider") == EITHER
    if either and i.get("critical"):
        return _to_keeper(model, i, keeper)
    if i.get("decider") not in ("owner", EITHER) or i.get("critical"):
        return i
    units = {uid: unit_members(model, uid) for uid, u in (model.get("org_units") or {}).items() if u.get("delegation")}
    for uid, (systems, positions) in units.items():
        inside = set(systems) | set(positions)
        if i["affected"] and all(x in inside for x in i["affected"]):
            owner = model["org_units"][uid]["owner"]
            i.update(decider="unit_owner", decider_position=owner, unit=uid)
            return i
    return _to_keeper(model, i, keeper) if either else i


def _to_keeper(model, i, keeper=None):
    i.update(decider="structure_keeper", decider_position=keeper or structure_keeper(model))
    return i


def diagnose(model, rows, a, b, cat=None, context=None, label_rows=None):
    """context: optional {keeper_since: fn(system, keeper) -> epoch | None} from the organization's registry history;
    code_activity: fn() -> of_model.code_activity over the last 30 days (RFC 0007 §2.2), computed on first call.
    label_rows: every of.issue_labelled of the organization, whatever the window, since a label is the one stored part
    and holds until another replaces it; None: those among ROWS. Until 2026-11-24 the labels came from the rows, so a
    label older than the rows read (9 days in the page render, 16 in `of model issues`) was lost, and the issue it had
    dismissed was open again in the inbox and could be escalated."""
    a, b = epoch(a), epoch(b)
    try:
        cat = cat or catalogue()
    except OSError as exc:
        return {"issues": [], "rules": [], "error": f"no catalogue: {exc}"}
    have = present(model, rows, a, b, cat)
    labs = label_history(rows if label_rows is None else [r for r in label_rows if (epoch(r.get("ts")) or 0) <= b],
                         session_writer(model, rows))
    issues, status = [], []
    keeper = structure_keeper(model)
    keeper_for = keeper_then(model, context, keeper)
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
            try:
                res = (fn(model, rows, a, b, cat, spec, ok, **kw) if ok is not None
                       else fn(model, rows, a, b, cat, spec, **kw))
            except Exception as exc:  # noqa: BLE001 — one rule failing never takes the others' issues with it
                # (2026-11-23: wip_arms may be a map by rung, which a rule comparing a count with one number could not
                # read; the diagnosis of the whole organization, its escalations with it, stopped on such a rule)
                row.update(status="failed", error=f"{type(exc).__name__}: {str(exc)[:200]}")
                status.append(row)
                continue
            notes = []
            if isinstance(res, tuple):
                res, extra, *rest = res
                notes = rest[0] if rest else []
            else:
                extra = []
            row["notes"] = notes
            carry = spec.get("labels_carry_from") or []
            found = res
            for i in found:
                i["decider_position"] = decider_position(model, i, keeper)
                route_to_unit(model, i, keeper)
                mark_unconfirmed(model, i)
                label_issue(i, labs.get(i["issue_id"]), keeper_for, b, carry)   # after routing: who decides it may label
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