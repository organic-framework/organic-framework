"""Measures of each owner's constraint (docs/studio.md §3) and the founder-load gate (§4).

Pure functions over MODEL(t) (bin/of_model.py), typed event rows and the transcript index.
Labels come from `payload.classifications[<version>]`, set once by bin/of_classify.py (of_platform);
this module never re-runs a classifier.

  owner(model, rows, a, b, who)    L, λ, W, the Little residual, answer delays, dead ends, concentration, plumbing, (a)
  waits(model, rows, a, b)         agent-minutes waited per position × hour × cause (the heatmap)
  repeat_corrections(model, rows, b)  the founder's corrections that repeat an earlier one, per position and week
  gate(model, rows, index, a, b)   the baseline observations under the pinned classifiers
  check(observed, targets)         pass or fail per observation against the baseline file
  flow(model, rows, a, b)          the flow analyst's series before and after onboarding, each a value or a named gap
"""

import collections
import datetime
import json
import re
import time

import of_asks
import of_model
from of_model import epoch

FA = "fa-2026-11-05"        # the baseline's rules, pinned (of_field-analyst)
QV0 = "q-v0"                # the baseline's question rule, pinned
THROUGHPUT_WORK = ("raise", "sustain")
SLEEP_CAP_H = 12            # answer gaps above this are sleep, not waiting (baseline §2.6)
TURN_SLACK_S = 2.0          # a turn_end this close after an ask is the ask's own turn end
PLUMBING = ("status_poll", "routing", "relay_paste", "access", "capacity_tooling")


# ------------------------------------------------------------------ helpers

def quantile(xs, p):
    """The baseline's percentile: the element at floor(p·n) of the sorted list."""
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


def labels(row, version):
    c = (row.get("payload") or {}).get("classifications") or {}
    v = c.get(version)
    return v if isinstance(v, dict) else ({"value": v} if v is not None else None)


in_window = of_model.in_window   # moved to the rule surface (bin/of_model.py)


def excluded_sessions(rows, exclude_cwd=()):
    """Sessions whose working directory falls under an excluded prefix (the baseline's `scope.exclude_cwd`)."""
    out = set()
    if not exclude_cwd:
        return out
    for r in rows:
        cwd = r.get("cwd") or ""
        if any(cwd == p or cwd.startswith(p.rstrip("/") + "/") for p in exclude_cwd):
            out.add(r.get("session_id"))
    return out


is_received = of_model.is_received   # moved to the rule surface (bin/of_model.py)


received_labels = of_model.received_labels   # moved to the rule surface (bin/of_model.py)


# Versions of a measure's definition, newest first: a review compares like with like, so a prediction made on an older
# definition is restated (the predictions file's `restated`) and the review row names the definition it reads.
DEFINITIONS = {
    "founder_messages": [
        ("fm-v2", "2026-11-06T20:00Z", "the owner's prompts with corrected origin human or paste (of.prompt_origin via "
                                       "of_asks.apply_prompt_origins); scheduled, system, injected and unknown never count"),
        ("fm-v1", None, "prompts the fa-2026-11-05 labels call the founder's, whatever their origin"),
    ],
}
DEFINED_BY = {"founder.founder_messages_in_unit": "founder_messages", "founder.plumbing_share_in_unit": "founder_messages"}


def definition_of(metric):
    """The current definition version of a dotted measure, when it has versions."""
    d = DEFINITIONS.get(DEFINED_BY.get(metric) or metric)
    return d[0][0] if d else None


# Templates OF's tools type into a pane whose typer does not declare them yet (of_platform, 2026-11-06): a spawned
# position's brief (its session's first prompt) and of_capacity's move note. Until they declare (of event
# injected_prompt), they are matched here; a hand-typed note of a different wording stays the owner's.
TOOL_TEXT = re.compile(r"(?:<pasted_content[^>]*>\s*)?(?:You hold the position [a-z][a-z0-9_-]*\. Read |"
                       r"of_capacity moved this session to another account\. Read )")


def founder_messages(rows, version=FA, dedupe=True):
    """Human prompts the version labels as the founder's, deduplicated on (minute, first 60 characters) as the baseline does.
    Without labels for the version: origin human or paste, and no exclusions. Either way the origin (corrected by of_asks.apply_prompt_origins,
    which of_model.load_events applies) must be the owner's, human or paste: a cron fire the hook recorded as human
    kept the labels it was given with the wrong origin."""
    seen, out = set(), []
    for r in rows:
        if r.get("name") != "of.human_prompt":
            continue
        pl = r.get("payload") or {}
        if not of_model.owner_prompt(r) or of_asks.TOOL_FRAME.match((pl.get("text") or "").lstrip()) \
                or TOOL_TEXT.match((pl.get("text") or "").lstrip()):
            continue   # a routed question, a relayed message or another tool's template, even if recorded human
        lab = labels(r, version)
        if lab is not None:
            if lab.get("kind") != "founder" or lab.get("extracted") is False:  # extracted: what the baseline's extractor emits
                continue
        elif pl.get("origin") not in of_model.OWNER_ORIGINS or pl.get("exclusions"):
            continue
        if dedupe:
            k = ((r.get("ts") or "")[:16], (pl.get("text") or "")[:60])
            if k in seen:
                continue
            seen.add(k)
        out.append(r)
    return out


def _cats(r, version):
    lab = labels(r, version) or {}
    return [c for c in (lab.get("cats") or lab.get("intent") or []) if c in PLUMBING]


# ------------------------------------------------------------------ owner measures (§3.1, §3.2)

def asks_of(model, rows, who=of_model.HUMAN, effective=None):
    """The ask lifecycle of bin/of_asks.py, restricted to asks whose accountable human is `who`.
    Asks from sessions without a position go to the founder."""
    return [k for k, acc in asks_routed(model, rows, effective) if acc == who]


def rows_key(rows):
    """A cheap key for a list of store rows in time order: its length and five event ids spread over it (a page's callers
    pass the same rows filtered anew, so the list's identity says nothing). None when a sampled row has no id."""
    n = len(rows)
    ids = tuple(rows[i].get("event_id") for i in sorted({0, n // 4, n // 2, 3 * n // 4, n - 1}) if 0 <= i < n)
    return None if None in ids else (n, ids)


_ROUTED = collections.OrderedDict()   # the last few projections: one page asks for the same one up to 16 times
ROUTED_KEEP = 4


def view_asks(conn=None):
    """Every ask of the listener's asks view, carried on with the log's tail (of_asks.current_asks, the read of
    organic-framework-build reports/of-log-readers-2026-11-14.md 3.2), each with its organization (`org`) and, on an
    escalated copy, the asker it names (`from_position`): {"asks", "cursor", "age_s", "late"}. None when this host
    keeps no such view (no listener, a test's store, of-server's reader): its callers then project their rows.
    A late view is returned as it is, with late set: a reader shows its age, it never projects the log instead (3.3)."""
    own = conn is None
    conn = conn or of_asks.reader()
    if conn is None:
        return None
    try:
        r = of_asks.current_asks(conn)
        if r["age_s"] is None and not r["asks"]:
            return None
        try:
            orgs = dict(conn.execute("SELECT event_id, org FROM view_asks"))
        except Exception:  # noqa: BLE001 - a view older than its org column
            orgs = {}
        # asks the tail folded are not in the view's table yet; escalated copies name their asker only in the payload
        want = [a["event_id"] for a in r["asks"] if a["event_id"] not in orgs or a.get("source") == "escalated"]
        found = {}
        for i in range(0, len(want), 500):
            part = want[i:i + 500]
            for e, org, pl in conn.execute("SELECT event_id, org, payload FROM event WHERE event_id IN "
                                           f"({','.join('?' * len(part))})", part):
                found[e] = (org, pl)
        asks = []
        for a in r["asks"]:
            a = dict(a, org=orgs.get(a["event_id"], (found.get(a["event_id"]) or (None, None))[0]))
            if a.get("source") == "escalated":
                try:
                    a["from_position"] = (json.loads((found.get(a["event_id"]) or (None, None))[1] or "{}")
                                          .get("from_position"))
                except ValueError:
                    pass
            asks.append(a)
        return {"asks": asks, "cursor": r["cursor"], "age_s": r["age_s"], "late": r["late"]}
    finally:
        if own:
            conn.close()


def attach_asks_view(model, rows, view):
    """Give the model its organization's asks from `view` (view_asks()), so asks_routed reads them instead of
    projecting `rows`. An ask belongs to the model's organization by its own org; an untagged one (an event with no
    org, placed by its cwd) when its event is among the organization's rows. No view: the model is left as it was."""
    if view is None:
        return model
    org, ids = model.get("org"), None
    mine = []
    for a in view["asks"]:
        if a.get("org") is None:
            ids = {r.get("event_id") for r in rows} if ids is None else ids
            if a["event_id"] not in ids:
                continue
        elif a["org"] != org:
            continue
        mine.append(a)
    model["asks_view"] = dict(view, asks=mine)
    return model


_VIEW_ROUTED = collections.OrderedDict()


def _view_routed(model, rows, effective):
    """[(t0, t1, ask, recipient)] over the model's asks view, routed as _asks_routed routes a projection's: t0 the ask's
    epoch, t1 its closing epoch or None. Kept per model, view and delegations, like asks_routed's projections."""
    view = model["asks_view"]
    positions = model["positions"]
    key = (model.get("org"), model.get("at"), view.get("cursor"), len(view["asks"]),
           hash(tuple(sorted((n, p.get("accountable"), p.get("accountable_before"), p.get("delegation_diff"), p.get("session"))
                             for n, p in positions.items()))),
           None if effective is None else tuple(sorted(effective.items(), key=str)))
    hit = _VIEW_ROUTED.get(key)
    if hit is not None:
        _VIEW_ROUTED.move_to_end(key)
        return hit
    sessions = of_model.session_index(positions)
    hit = []
    for ask in view["asks"]:
        t0 = epoch(ask.get("ts"))
        if ask.get("state") == "not_a_question" or t0 is None:
            continue   # the judge's no: never in the owner's queue, at any time
        p = of_model.position_of(ask, positions, sessions)
        ask = dict(ask, position=p)
        if ask.get("source") == "escalated":
            to = ask.get("to")
            acc = of_model.HUMAN if to in of_asks.OWNER_ADDRESSES else to
            ask.update(escalated_by=p, position=ask.get("from_position") or p)
        else:
            acc = of_model.accountable_at(positions[p], t0, effective) if p in positions else of_model.HUMAN
        hit.append((t0, epoch(ask.get("closed_at")) if ask.get("closed_at") else None, ask, acc))
    _VIEW_ROUTED[key] = hit
    while len(_VIEW_ROUTED) > ROUTED_KEEP:
        _VIEW_ROUTED.popitem(last=False)
    return hit


def rows_end(rows):
    """The epoch of the latest row: the time `rows` reach, which callers cut at their window's end."""
    last = max((r.get("ts") or "" for r in rows), default="")
    return epoch(last) if last else None


def asks_routed(model, rows, effective=None):
    """[(ask, recipient)]: every ask of bin/of_asks.py's lifecycle with whom it waits on: the position's accountable
    owner at the ask's time (a delegated unit's owner once its delegation took effect), an escalated copy whom it names,
    the human owner for a session without a position. Asks the judge called no question are left out.
    With the listener's asks view on the model (attach_asks_view), the asks are the view's up to the time `rows` reach,
    each as it stood then (closed later: open), and none is projected. Without it, the lifecycle is projected over
    `rows`, and the same model and rows give the same projection, kept for the next caller (2026-11-14: one user
    organization's page projected it 16 times, 40 s of its 154). Each caller gets its own copies of the ask dicts."""
    if model.get("asks_view") is not None:
        effective = of_model.delegation_effective(rows) if effective is None else effective
        end = rows_end(rows)
        if end is None:
            return []
        out = []
        for t0, t1, ask, acc in _view_routed(model, rows, effective):
            if t0 > end:
                continue
            k = dict(ask)
            if t1 is not None and t1 > end:
                k.update(state="open", closed_at=None, closed_by=None)
            out.append((k, acc))
        return out
    ids = rows_key(rows)
    if ids is None:   # rows without ids (hand-made): nothing to key on
        return _asks_routed(model, rows, effective)
    effective = of_model.delegation_effective(rows) if effective is None else effective   # as _asks_routed would
    key = (model.get("org"), model.get("at"), ids,
           hash(tuple(sorted((n, p.get("accountable"), p.get("accountable_before"), p.get("delegation_diff"), p.get("session"))
                             for n, p in model["positions"].items()))),
           None if effective is None else tuple(sorted(effective.items(), key=str)))
    hit = _ROUTED.get(key)
    if hit is None:
        hit = _ROUTED[key] = _asks_routed(model, rows, effective)
        while len(_ROUTED) > ROUTED_KEEP:
            _ROUTED.popitem(last=False)
    else:
        _ROUTED.move_to_end(key)
    return [(dict(k), acc) for k, acc in hit]


def _asks_routed(model, rows, effective=None):
    positions = model["positions"]
    sessions = of_model.session_index(positions)
    named = []
    keep = getattr(of_asks, "relevant", None)   # the one filter of the live asks view (of_asks.load, the listener)
    for r in rows:
        if keep is not None and not keep(r.get("name"), r.get("source"), r.get("payload")):
            continue
        r = dict(r)
        r["position"] = of_model.position_of(r, positions, sessions)
        named.append(r)
    # at equal timestamps annotations written by `of` go after primary events; otherwise the stable sort keeps the
    # store's order (a question emitted at a turn's end must stay after its own of.turn_end)
    named.sort(key=lambda r: (epoch(r.get("ts")) or 0, r.get("source") == "of"))
    out = []
    effective = of_model.delegation_effective(rows) if effective is None else effective   # pass it from untruncated rows
    asked = {r["event_id"]: r.get("payload") or {} for r in named if r.get("name") == "of.agent_question"}
    for ask in of_asks.project(named):
        if ask["state"] == "not_a_question":
            continue   # the judge's no (of.agent_question_judged, from 2026-11-07T22:20Z): never in the owner's queue
        p, pl = ask.get("position"), asked.get(ask.get("event_id")) or {}
        if pl.get("source") == "escalated":
            # the delegator's copy of an escalated ask (of pulse): it reaches whom it names, whatever the position's line
            to = pl.get("to")
            acc = of_model.HUMAN if to in of_asks.OWNER_ADDRESSES else to
            ask = dict(ask, source="escalated", escalated_from=pl.get("escalated_from"), escalated_by=p,
                       position=pl.get("from_position") or p)   # the copy is written in the escalating session
        else:
            acc = of_model.accountable_at(positions[p], epoch(ask.get("ts")), effective) if p in positions else of_model.HUMAN
        out.append((ask, acc))
    return out


RQ = "rq-v0"


def is_request(r):
    """A received peer message that asks for something: the rq-v0 label (bin/of_classify.py)."""
    v = received_labels(r).get(RQ)
    return bool(v.get("value", v.get("request")) if isinstance(v, dict) else v)


def little(asks, a, b):
    """L (time-average open asks), λ (arrivals per hour), W (mean hours open, censored at b) and the residual L − λW."""
    hours = (b - a) / 3600.0
    area, arrivals, waits, censored = 0.0, 0, [], 0
    for k in asks:
        t0 = epoch(k["ts"])
        t1 = epoch(k.get("closed_at")) if k.get("closed_at") else None
        end = min(t1, b) if t1 is not None else b
        start = max(t0, a)
        if end > start:
            area += end - start
        if a <= t0 < b:
            arrivals += 1
            stop = t1 if t1 is not None and t1 <= b else b
            if t1 is None or t1 > b:
                censored += 1
            waits.append((stop - t0) / 3600.0)
    L = area / 3600.0 / hours if hours else 0.0
    lam = arrivals / hours if hours else 0.0
    W = sum(waits) / len(waits) if waits else 0.0
    res = L - lam * W
    carry = [k for k in asks if epoch(k["ts"]) < a and (not k.get("closed_at") or epoch(k["closed_at"]) > a)]
    carry_area = sum(min(epoch(k["closed_at"]) if k.get("closed_at") else b, b) - a for k in carry)
    return {"L": round(L, 3), "lambda_per_h": round(lam, 3), "W_h": round(W, 3), "residual": round(res, 3),
            "residual_rel": round(res / L, 3) if L else None, "arrivals": arrivals, "censored": censored,
            "carry_in": len(carry), "carry_in_L": round(carry_area / 3600.0 / hours, 3) if hours else 0.0,
            "open_at_end": sum(1 for k in asks if epoch(k["ts"]) < b and (not k.get("closed_at") or epoch(k["closed_at"]) > b))}


_WINDOW_ASKS = collections.OrderedDict()


def _window_asks(model, rows, a, b):
    """Every ask raised in [a, b) by the lifecycle over the window's rows alone (the owner's inbound concentration).
    The same for every owner of one page: projected once and kept like asks_routed's. With the asks view on the model,
    the view's asks raised in [a, b) instead."""
    if model.get("asks_view") is not None:
        a, b = epoch(a), epoch(b)
        return [dict(k) for t0, _, k, _ in _view_routed(model, rows, of_model.delegation_effective(rows)) if a <= t0 < b]
    positions = model["positions"]
    sessions = of_model.session_index(positions)
    win = [r for r in rows if in_window(r, a, b)]
    ids = rows_key(win)
    key = None if ids is None else (model.get("org"), ids, hash(tuple(sorted(sessions.items()))))
    hit = _WINDOW_ASKS.get(key) if key else None
    if hit is None:
        hit = [k for k in of_asks.project(sorted([dict(r, position=of_model.position_of(r, positions, sessions)) for r in win],
                                                 key=lambda r: epoch(r["ts"]))) if k["state"] != "not_a_question"]
        if key:
            _WINDOW_ASKS[key] = hit
            while len(_WINDOW_ASKS) > ROUTED_KEEP:
                _WINDOW_ASKS.popitem(last=False)
    return [dict(k) for k in hit]


def owner(model, rows, a, b, who=of_model.HUMAN, version=FA, dead_end_hours=24):
    """All measures of §3.1–3.2 for one owner over [a, b). a, b: ISO strings or epoch seconds."""
    a, b = epoch(a), epoch(b)
    positions = model["positions"]
    sessions = of_model.session_index(positions)
    eff = of_model.delegation_effective(rows)
    asks = [k for k in asks_of(model, [r for r in rows if epoch(r.get("ts")) is not None and epoch(r["ts"]) < b], who, eff)]
    out = {"owner": who, "from": of_model.iso(a), "to": of_model.iso(b)}
    out.update(little(asks, a, b))
    answered = [k for k in asks if k["state"] == "answered" and a <= epoch(k["ts"]) < b]
    mins = [(epoch(k["closed_at"]) - epoch(k["ts"])) / 60.0 for k in answered]
    out["answer_delay_min"] = {"p50": _r(quantile(mins, .5)), "p75": _r(quantile(mins, .75)), "p90": _r(quantile(mins, .9)),
                               "n": len(mins)}
    out["agent_hours_waited"] = round(sum(m for m in mins if m <= SLEEP_CAP_H * 60) / 60.0, 1)
    out["dead_end_asks"] = sorted(
        [k["event_id"] for k in asks if epoch(k["ts"]) < b and (not k.get("closed_at") or epoch(k["closed_at"]) > b)
         and b - epoch(k["ts"]) >= dead_end_hours * 3600]
        + [k["event_id"] for k in asks if k["state"] == "expired" and a <= epoch(k["closed_at"]) < b])
    # inbound concentration: this owner's share of all asks in the organization
    all_asks = _window_asks(model, rows, a, b)
    mine = [k for k in asks if a <= epoch(k["ts"]) < b]
    non_t = [k for k in mine if positions.get(k.get("position"), {}).get("throughput") not in THROUGHPUT_WORK]
    # in-degree share: requests reaching the owner against all requests between positions (asks to the owner plus peer
    # requests received, rq-v0); asks reach only humans, so asks alone would make this 1 with one owner
    peer = sum(1 for r in rows if in_window(r, a, b) and r["name"] == "of.agent_message_in" and is_received(r) and is_request(r))
    out["inbound_concentration"] = round(len(mine) / (len(mine) + peer), 3) if (mine or peer) else None
    out["peer_requests"] = peer
    out["owner_share_of_human_asks"] = round(len(mine) / len(all_asks), 3) if all_asks else None
    out["moved_on"] = sum(1 for k in asks if k["state"] == "moved_on" and a <= epoch(k["ts"]) < b)
    out["inbound_from_non_throughput"] = round(len(non_t) / len(mine), 3) if mine else None
    # the owner's messages (the tenant's human prompts are the founder's until people are mapped to machines)
    msgs = founder_messages([r for r in rows if in_window(r, a, b)], version) if who == of_model.HUMAN else []
    plumb = [m for m in msgs if _cats(m, version)]
    out["owner_messages"] = len(msgs)
    out["plumbing_share"] = round(len(plumb) / len(msgs), 3) if msgs else None
    out["status_polls"] = sum(1 for m in msgs if is_status_poll(m))           # sp-v1 (docs/diagnose.md §7.1)
    out["status_polls_fa"] = sum(1 for m in msgs if "status_poll" in _cats(m, version))   # the pinned phrase list, for reference
    out["throughput_share"] = throughput_share(model, msgs)
    out["polls_vs_studio"] = polls_vs_studio(msgs, rows, a, b, who, version)
    return out


# ------------------------------------------------------------------ repeat corrections (RFC 0006 §2.4)

RC = "rc-v0"
WEEK_S = 7 * 86400


def week_start(t):
    """Monday 00:00 UTC of t's ISO week, as epoch seconds."""
    return int(t // 86400) * 86400 - time.gmtime(t).tm_wday * 86400


def iso_week(t):
    y, w, _ = datetime.date(*time.gmtime(t)[:3]).isocalendar()
    return f"{y}-W{w:02d}"


def repeat_corrections(model, rows, b, weeks=6):
    """The founder's corrections per position and ISO week (rc-v0), over the `weeks` weeks that end with b's own:
    n, the `of.founder_correction` events (of_platform's judge, RFC 0006 step 4); repeats, those whose `repeat_of`
    names an earlier correction; rate = repeats / n. The position is the payload's (an alias counts as its canonical
    name), else its session's. One correction per `prompt_id` counts, so a judge retried on a prompt counts once.
    Splitting an organization's rules by role works if the rate falls (the founder's test, 2026-11-09)."""
    b = epoch(b)
    positions = model["positions"]
    sessions = of_model.session_index(positions)
    canon = {str(x): n for n, p in positions.items() for x in [n] + list(p.get("aliases") or [])}
    a = week_start(b) - (weeks - 1) * WEEK_S
    labels = [iso_week(a + i * WEEK_S) for i in range(weeks)]
    per, rules, seen, first = {}, collections.defaultdict(collections.Counter), set(), None
    total = {w: {"n": 0, "repeats": 0} for w in labels}
    for r in sorted((r for r in rows if r.get("name") == "of.founder_correction"), key=lambda r: epoch(r.get("ts")) or 0):
        t, pl = epoch(r.get("ts")), r.get("payload") or {}
        key = pl.get("prompt_id") or r.get("event_id")
        if t is None or t >= b or key in seen:
            continue
        seen.add(key)
        first = first or r["ts"]
        if t < a:
            continue
        p = pl.get("position")
        pos = canon.get(str(p), p) if p else of_model.position_of(r, positions, sessions)
        pos = pos or "(unregistered)"
        w = labels[int((t - a) // WEEK_S)]
        rep = bool(pl.get("repeat_of"))
        for c in (per.setdefault(pos, {x: {"n": 0, "repeats": 0} for x in labels})[w], total[w]):
            c["n"] += 1
            c["repeats"] += rep
        if rep and pl.get("rule"):
            rules[pos][" ".join(str(pl["rule"]).split())[:160]] += 1

    def rated(c):
        return dict(c, rate=round(c["repeats"] / c["n"], 3) if c["n"] else None)
    out = {}
    for pos, ws in per.items():
        n, rp = sum(c["n"] for c in ws.values()), sum(c["repeats"] for c in ws.values())
        out[pos] = {"weeks": {w: rated(c) for w, c in ws.items()}, "n": n, "repeats": rp,
                    "rate": round(rp / n, 3) if n else None, "repeated_rules": rules[pos].most_common(3)}
    return {"method": RC, "weeks": labels, "from": of_model.iso(a), "to": of_model.iso(b), "positions": out,
            "total": {w: rated(c) for w, c in total.items()}, "recorded": first is not None, "first": first}


def polls_vs_studio(msgs, rows, a, b, who=of_model.HUMAN, version=FA):
    """Per UTC day of [a, b): the owner's status polls to agents against the owner's Studio page loads
    (`of.studio_viewed`, written by of-server). The founder's views are the tenant's; a colleague's carry `owner`."""
    days = {}
    t = int(a // 86400) * 86400
    while t < b:
        days[of_model.iso(t)[:10]] = {"polls": 0, "views": 0}
        t += 86400
    for m in msgs:
        if is_status_poll(m):   # sp-v1
            d = str(m["ts"])[:10]
            if d in days:
                days[d]["polls"] += 1
    for r in rows:
        if r["name"] != "of.studio_viewed" or not in_window(r, a, b):
            continue
        pl = r.get("payload") or {}
        if str(pl.get("requester") or "").startswith("agent:"):
            continue   # a link an agent requested (of-server 10525a8): not the owner opening Studio
        mine = pl.get("owner") == who if who != of_model.HUMAN else (pl.get("tenant") or pl.get("owner") in (None, who))
        d = str(r["ts"])[:10]
        if mine and d in days:
            days[d]["views"] += 1
    return {"days": days, "poll_version": SP, "polls": sum(v["polls"] for v in days.values()), "views": sum(v["views"] for v in days.values()),
            "recorded": any(r["name"] == "of.studio_viewed" for r in rows)}


def _tag_fix(ask, fixes):
    """A correction of the review record's `tag_corrections` for this ask (by its id or the id's last 8 characters)."""
    if not fixes or not ask.get("event_id"):
        return None
    return fixes.get(ask["event_id"]) or fixes.get(ask["event_id"][-8:])


def declared_tag(ask, fixes=None):
    """The asker's first declared tag (`of ask --tags`, source declared), else 'untagged'. Guessed tags (tg-v0) do not
    count (of_field-analyst, 2026-11-07: founder.escalation_tag; esc-v0 stays the class comparable across windows).
    `fixes` ({ask id or its last 8 characters: tag}, the record's tag_corrections) win: an open ask cannot be retagged."""
    fixed = _tag_fix(ask, fixes)
    if fixed:
        return str(fixed).split(":", 1)[0]
    for t in ask.get("tags") or []:
        if isinstance(t, dict):
            if t.get("source") in (None, "declared") and t.get("tag"):
                return str(t["tag"]).split(":", 1)[0]
        elif t:
            return str(t).split(":", 1)[0]
    if ask.get("source") == "escalated":   # an escalated copy: its escalate_when rule's kind, from the copy's own frame
        m = ESCALATED_RULE.match(ask.get("text") or "")
        return m.group(1) if m else "escalated"
    kind = str(ask.get("kind") or "")
    if kind.startswith(("blocked", "question (heuristic)", "permission")):
        return "detected"   # found by a detector (a screen, a heuristic, a permission prompt): no agent tagged it
    return "untagged"


ESCALATED_RULE = re.compile(r"\[escalated(?: from [^:\]]+)?: ([a-z_.]+)")


def delegation_review(model, rows, a, b, record):
    """The measures a delegation's predictions name (docs/studio.md §2.4; of_field-analyst's predictions file), over
    [a, b) and the record's positions plus its to_owner: founder.* for asks accountable to the founder from those
    positions, unit_owner_side.* for asks accountable to the unit owner."""
    a, b = epoch(a), epoch(b)
    return delegation_series(model, rows, a, b, record, [b - a])[0]


DELEGATION_ADDITIVE = ("asks", "answered", "moved_on", "agent_hours_waited", "founder_messages_in_unit",
                       "founder_asks_total")   # these grow over a window; shares and delays do not


def delegation_series(model, rows, a, b, record, offsets, effective=None, tag_fixes=None, cuts=()):
    """delegation_review's measures at a + each offset (capped at b), from one projection of the asks up to b: an
    ask counts from its opening and its closure from closed_at, as the lifecycle only closes. The point at b is
    delegation_review itself. Also by_position (asks by asking position) and plumbing_share_in_unit. effective: when
    each delegation took effect (of_model.delegation_effective over every of.diff_applied); pass it when `rows` may
    end before the delegation was applied, as a baseline window's do."""
    a, b = epoch(a), epoch(b)
    positions = model["positions"]
    owner = record.get("to_owner")
    unit = set(record.get("positions") or []) | ({owner} if owner else set())
    sp = dict(of_model.session_index(positions))
    for r in rows:
        if r.get("position") in positions and r.get("session_id"):
            sp.setdefault(r["session_id"], r["position"])
    upto = [r for r in rows if (epoch(r.get("ts")) or 0) < b]
    eff = of_model.delegation_effective(rows) if effective is None else effective
    f_any = [k for k in asks_of(model, upto, of_model.HUMAN, eff) if a <= epoch(k["ts"]) < b]
    # blocking screens (blocked:*, recorded from 2026-11-06T13:00Z) are reported apart: a baseline before them could not
    # count them, so the founder's asks leave them out to compare like with like (of_field-analyst, 2026-11-07)
    f_all = [k for k in f_any if not str(k.get("kind") or "").startswith("blocked")]
    f_unit = [k for k in f_all if k.get("position") in unit]
    f_blocked = [k for k in f_any if k.get("position") in unit and str(k.get("kind") or "").startswith("blocked")]
    o_unit = ([k for k in asks_of(model, upto, owner, eff) if k.get("position") in unit and a <= epoch(k["ts"]) < b]
              if owner else [])
    pos_of = lambda m: m.get("position") if m.get("position") in positions else sp.get(m.get("session_id"))
    fm = [m for m in founder_messages([r for r in rows if in_window(r, a, b)]) if pos_of(m) in unit]
    msgs = [(epoch(m["ts"]), bool(_cats(m, FA))) for m in fm]
    topic = incident_topics(fm, asks_of(model, upto, of_model.HUMAN, eff))

    byid = {r.get("event_id"): r for r in upto}

    def owners_answer(k):
        """The unit owner answered: its own reply into the asker's session (of_asks v6) or its own `of asks answered`.
        A permission approved in the asker's pane, or the founder's answer to an escalated copy, is not its answer
        (of_field-analyst, 2026-11-06)."""
        c = byid.get(k.get("closed_by")) or {}
        if c.get("name") == "of.agent_message_in":
            return str((c.get("payload") or {}).get("from") or "").rpartition(":")[2] == owner
        return c.get("position") == owner or sp.get(c.get("session_id")) == owner

    def side(asks, t, is_answer=None):
        asks = [k for k in asks if epoch(k["ts"]) < t]
        done = [k for k in asks if k.get("closed_at") and epoch(k["closed_at"]) <= t]
        ans = [k for k in done if k["state"] == "answered" and (is_answer is None or is_answer(k))]
        mins = [(epoch(k["closed_at"]) - epoch(k["ts"])) / 60.0 for k in ans]
        # a sum of durations splits at a change by cutting each wait there: its part before goes before
        split = {of_model.iso(c): round(sum(max(0.0, min(epoch(k["closed_at"]), c) - epoch(k["ts"])) / 3600.0 for k, m in zip(ans, mins)
                                            if m <= SLEEP_CAP_H * 60), 1) for c in cuts}
        return {"asks": len(asks), "answered": len(ans), "moved_on": sum(k["state"] == "moved_on" for k in done),
                "open_at_end": len(asks) - len(done),
                "closed_otherwise": len(done) - len(ans) - sum(k["state"] == "moved_on" for k in done),
                "escalated_copies": sum(1 for k in asks if k.get("source") == "escalated"),
                "escalated_by_position": dict(collections.Counter(k["position"] for k in asks if k.get("source") == "escalated")),
                "answer_delay_min": {"p50": _r(quantile(mins, .5)), "p75": _r(quantile(mins, .75)), "p90": _r(quantile(mins, .9))},
                "agent_hours_waited": round(sum(m for m in mins if m <= SLEEP_CAP_H * 60) / 60.0, 1),
                "by_position": dict(collections.Counter(k["position"] for k in asks).most_common()),
                "agent_hours_waited_before": split,
                "escalation_tag": dict(collections.Counter(declared_tag(k, tag_fixes) for k in asks).most_common()),
                "escalation_tag_corrected": sorted(k["event_id"][-8:] for k in asks if _tag_fix(k, tag_fixes))}
    out = []
    for off in offsets:
        t = min(b, a + off)
        founder = side(f_unit, t)
        founder["blocked_asks"] = sum(1 for k in f_blocked if epoch(k["ts"]) < t)
        n_all = sum(1 for k in f_all if epoch(k["ts"]) < t)
        founder["unit_share_of_founder_asks"] = round(founder["asks"] / n_all, 3) if n_all else None
        founder["founder_asks_total"] = n_all
        mt = [pl for ts, pl in msgs if ts < t]
        founder["founder_messages_in_unit"] = len(mt)
        founder["plumbing_share_in_unit"] = round(sum(mt) / len(mt), 3) if mt else None
        upto_t = [m for m in fm if epoch(m["ts"]) < t]
        founder["founder_messages_split"] = {
            "to_unit_owner": sum(1 for m in upto_t if pos_of(m) == owner),
            "to_keepers": sum(1 for m in upto_t if pos_of(m) != owner),
            "incident": sum(1 for m in upto_t if topic.get(m["event_id"])),
            "other": sum(1 for m in upto_t if not topic.get(m["event_id"])),
            "popup_answers": sum(1 for m in upto_t if of_asks.ANSWER_FRAME.match((m["payload"].get("text") or "").lstrip())),
            "rule": INC}
        out.append({"at": of_model.iso(t), "founder": founder,
                    "unit_owner_side": side(o_unit, t, owners_answer) if owner else {"asks": 0}})
    return out


INC = "inc-v0"   # the leaked-credentials incident of 2026-11-06 (of_architect, for the platform review); reported, never judged
INCIDENT_WORDS = re.compile(r"(ротир|ротац|rotat|перевыпуст|reissue|revok|отозв|утечк|утек|\bleak|credential|секрет|secret|"
                            r"уволен|уволил|увольнен|offboard|токен|\btokens?\b|парол|password|"
                            r"ключ\w* (?:доступа|подписи|приложения|github)|отключ\w* (?:\w+ )?(?:ключ|учётк|учетк|логин))", re.I)
DECISION_CHARS = 200   # an ask's decision line: the words in its long context name other systems' keys in passing


def incident_topics(msgs, asks):
    """{message event id: True} for the owner's messages about the incident (inc-v0): its own text names rotation, a
    reissue or revocation, leaks, secrets, tokens, passwords, access or signing keys, or the offboarding; or it
    answers an ask (the popup's frame names the ask's last 8 characters; else the ask it closed) tagged
    security_incident or whose decision line names them."""
    by_suffix = {k["event_id"][-8:]: k for k in asks if k.get("event_id")}
    by_close = {k.get("closed_by"): k for k in asks if k.get("closed_by")}

    def about(k):
        tags = " ".join(str(t.get("tag") if isinstance(t, dict) else t) for t in (k.get("tags") or []))
        return "security_incident" in tags or bool(INCIDENT_WORDS.search((k.get("text") or "")[:DECISION_CHARS]))
    out = {}
    for m in msgs:
        t = (m.get("payload") or {}).get("text") or ""
        f = of_asks.ANSWER_FRAME.match(t.lstrip())
        k = by_suffix.get(f.group(1)) if f else by_close.get(m.get("event_id"))
        own = t[f.end():] if f else t
        out[m["event_id"]] = bool(INCIDENT_WORDS.search(own) or (k and about(k)))
    return out


# ------------------------------------------------------------------ status polls, sp-v1 (docs/diagnose.md §7.1)

SP = "sp-v1"
_sp_norm = lambda t: re.sub(r"\s+", " ", (t or "").lower().replace("ё", "е")).strip()
SP_POLL = re.compile(r"(" + "|".join([
    r"как (?:у (?:нас|тебя) |тут |там |же |вообще |сейчас )*(?:тут |там )?дела",
    r"как (?:у нас |тут |там )*(?:прошл[аио]|прошел|идет|идут|продвигается|продвигаемся|успехи)",
    r"какие (?:у нас |тут |там )*новости",
    r"что (?:у нас |у тебя |тут |там |еще |в итоге )*(?:еще )?осталось",
    r"(?:какой|какая|какие) (?:у нас |тут |там |сейчас |вообще )*(?:прогресс|прогноз\w*|статус)",
    r"^\W*(?:ну |так,? )?(?:что|че|чо)\W*$",
    r"чего (?:мы |ты )?(?:ждем|ждешь|так долго)",
    r"сколько (?:еще )?ждать",
    r"(?:что|кто) (?:нас |тебя )?(?:сейчас )?останавливает",
    r"(?:зачем|почему) (?:ты )?меня ждешь|на что ты ждешь|что ты ждешь|ты (?:это )?(?:от меня )?ждешь|ждешь (?:от меня|мое)",
    r"когда (?:у нас )?(?:мы |вообще |уже )*(?:будем|будет|будут|сможем)",
    r"что случилось(?: с| пока|,? пока)|(?:расскажи|скажи),? (?:мне,? )?что случилось",
    r"(?:ты )?уже (?:смержил|задеплоил|выкатил|сделал|закончил|применил)",
    r"еще не (?:вышло|вышел|готово|готов|смержил)",
    r"(?:все ли|готово ли|готов ли) .{0,40}(?:готово|закрыли|к переключению|сделано)|все ли (?:готово|мы закрыли)",
    r"выкачаны ли|ты отправил .{0,40} или нет",
    r"какие (?:у нас |тут |там |ко мне |есть )*(?:ко мне )?(?:есть )?вопросы",
    r"что с (?:ней|ним|ними|этим|ротацией|релиз\w*)\b(?! делать)",
    r"какая (?:у нас )?(?:сейчас )?очередь",
    r"\bhow(?:'s| is| are) (?:it|things|we) (?:going|doing)|what(?:'s| is) left|any progress|what(?:'s| is) the status",
]) + r")")
# a request to report or gather the state is still a poll; any other imperative directs work
SP_REPORT = re.compile(r"\b(?:расскажи|скажи|узнай|проверь|посмотри|проанализируй|доложи|покажи|подскажи|напомни)\b")
SP_WORK = re.compile(r"\b(?:делай|сделай|сделаем|давай(?:те)?|добавь|исправь|пофикси|поправь|настрой|донастро\w*|переделай|построим|построй|"
                  r"запусти|напиши|создай|убери|удали|перенеси|обнови|поставь|отдай|залогин\w*|надо|нужно бы|опиши|запиши|"
                  r"включи|выключи|отключи|перезапусти|смержи|мержи|задеплой|выкати|раскатай|примени|откати)\b")
SP_SYSTEM = re.compile(r"(?:лимит|алерт|\bлог(?:и|ов|ах)?\b|loki|локи|аккаунт\w* по|диск\w*|нагрузк)")
SP_SYSTEM_Q = re.compile(r"(?:что,? (?:собственно,? )?с (?:архивом )?логов|(?:терять|теряем) логи|статус\w* (?:у нас )?по (?:лимит|алерт))")
SP_QUESTION = re.compile(r"(?:что делает|как ты видишь|как работает|что значит|что (?:нам|мне) нужно,? чтобы|зачем|почему|опиши)")
SP_ACK = re.compile(r"^(?:привет|ок(?:ей)?|окей|отлично|хорошо|супер|так|ну|да|все верно|ну отлично)\b")

def status_poll_label(text):
    """sp-v1 (of_field-analyst, docs/diagnose.md §7.1): status_poll, instruction_with_poll, system_status, question, or None.
    A poll's main act asks for the state of work in progress or of the agent doing it. The test: remove the poll
    clauses; if what is left still directs work (an imperative other than report or gather), it is an instruction
    with a poll. A poll on a system or a resource (limits, alerts, logs) is system_status. Rule-based, written
    against the labelled week, so it is checked there (`of model polls --labels`) and on later labels."""
    raw = text or ""
    if "<pasted_content" in raw or re.search(r"^\s*\d[.)]\s", raw, re.M) and len(re.findall(r"^\s*\d[.)]\s", raw, re.M)) >= 2:
        return None
    t = _sp_norm(raw)
    if not SP_POLL.search(t):
        return "system_status" if SP_SYSTEM_Q.search(t) else None
    # the test: remove the poll clauses; what is left must not direct work, decide, or ask about design
    rest = SP_POLL.sub(" ", t)
    if SP_WORK.search(rest):
        return "instruction_with_poll"
    if SP_SYSTEM.search(t):
        return "system_status"
    if SP_QUESTION.search(t) and not re.search(r"что (?:у нас )?(?:еще )?осталось|как .{0,20}дела", t):
        return "question"
    if SP_WORK.search(rest):
        return "instruction_with_poll"
    if len(t) > 320 and not SP_REPORT.search(rest):
        return "instruction_with_poll"
    return "status_poll"


def is_status_poll(msg):
    return status_poll_label((msg.get("payload") or {}).get("text")) == "status_poll"


def status_polls(model, rows, a, b, positions=None, exclude=None, version=SP):
    """Status polls (sp-v1) over [a, b): the count and the share of the owner's messages (fm-v2), for the
    organization and per position. `positions` keeps only those; `exclude` drops those (a unit judged apart)."""
    pos, sp = model["positions"], dict(of_model.session_index(model["positions"]))
    for r in rows:
        if r.get("position") in pos and r.get("session_id"):
            sp.setdefault(r["session_id"], r["position"])
    who = lambda m: m.get("position") if m.get("position") in pos else sp.get(m.get("session_id"))
    msgs = [m for m in founder_messages([r for r in rows if in_window(r, a, b)])
            if (positions is None or who(m) in positions) and not (exclude and who(m) in exclude)]
    labels = {m["event_id"]: status_poll_label((m.get("payload") or {}).get("text")) for m in msgs}
    per = collections.defaultdict(lambda: {"owner_messages": 0, "polls": 0})
    for m in msgs:
        p = per[who(m) or "(no position)"]
        p["owner_messages"] += 1
        p["polls"] += labels[m["event_id"]] == "status_poll"
    share = lambda n, d: round(n / d, 3) if d else None
    by_label = collections.Counter(v for v in labels.values() if v)
    n = by_label.get("status_poll", 0)
    return {"version": version, "from": of_model.iso(a), "to": of_model.iso(b), "owner_messages": len(msgs), "polls": n,
            "share": share(n, len(msgs)), "instruction_with_poll": by_label.get("instruction_with_poll", 0),
            "system_status": by_label.get("system_status", 0),
            "by_position": {k: dict(v, share=share(v["polls"], v["owner_messages"]))
                            for k, v in sorted(per.items(), key=lambda kv: (-kv[1]["polls"], kv[0])) if v["polls"]},
            "poll_ids": [m["event_id"] for m in msgs if labels[m["event_id"]] == "status_poll"],
            "labels": labels}


def check_labels(result, labelled):
    """sp-v1 against hand labels ({event id: label}): the counts, and per message the agreement on status_poll."""
    got = result["labels"]
    lp = {i for i, l in labelled.items() if l == "status_poll"}
    sp_ = {i for i, l in got.items() if l == "status_poll"}
    return {"labelled_polls": len(lp), "sp_polls": len(sp_), "diff": len(sp_) - len(lp), "agree": len(lp & sp_),
            "missed": sorted(lp - sp_), "not_labelled_poll": sorted(i for i in sp_ - lp if i in labelled),
            "unlabelled_flagged": sorted(i for i in sp_ - lp if i not in labelled),
            "not_in_window": sorted(i for i in labelled if i not in got)}


UY = "uy-v1"   # of_field-analyst's definition (unit_owner_yes in the organization's reports/delegations/<id>.yaml); reported, never judged
SENTENCE = re.compile(r"(?<=[.!?;])\s+|\n+")
LEAD = re.compile(r"^\s*(?:[-*\u2022]\s*|\(?[0-9A-Za-z]{1,2}[).]\s+)?")   # a list marker; the words are searched anywhere
YES_WORD = re.compile(r"\b(?:yes|approved(?! (?:for|by|in)\b)|go ahead|is fine|ok to merge)\b|\bmy (?:routine )?yes\b|"
                      r"\ba routine yes from me\b", re.I)
ACTION = re.compile(r"\b(?:merg(?:e|es|ed|ing)|appl(?:y|ies|ied|ying)|push(?:es|ed|ing)?|force-push\w*)\b|#\d+", re.I)
FOUNDER_YES = re.compile(r"\bfounder\b[^.]{0,40}\b(?:yes|approved?|approval)\b|\bfounder(?:'s)? yes\b|\bstanding founder yes\b", re.I)
STANDING = re.compile(r"\bstanding (?:routine )?yes\b", re.I)
ASK_ID = re.compile(r"\b[0-9a-f]{8}\b")          # an ask id: asks go to the founder, so a yes carrying one is its answer
FOUNDER_ANSWERS = re.compile(r"\bfounder(?:'s)?\s+(?:answers?|decisions?)\b|\banswers? (?:from|of) the founder\b", re.I)
PERMISSION = re.compile(r"\bstanding\b|\byou may (?:also )?(?:force-)?push\b|\borg-wide rule\b", re.I)
NOT_YES = re.compile(r"\bwithout (?:a |an |my )?(?:yes|approval)\b|\bneed(?:s|ing|ed)? (?:a |an |my )?(?:yes|approval)\b|"
                     r"\bstill need|\bnot covered\b", re.I)
PR_REF = re.compile(r"(?:\b([A-Za-z][\w.-]*)\s+)?#(\d+)\b")
REVERT = re.compile(r'^\s*Revert\s+"(.+)"\s*$')


def _relay_text(text):
    """A relayed message's body: the text after the relay header's closing parenthesis."""
    t = re.sub(r"^\s*<pasted_content[^>]*>\s*", "", text or "")
    t = re.sub(r"\s*</pasted_content[^>]*>\s*$", "", t)
    if t.lstrip().startswith("Message from ") and ")" in t:
        t = t.split(")", 1)[1]
    return t.strip()


def yes_sentences(text):
    """uy-v1 on one message body: (owner yes sentences, standing: bool). A yes sentence names a yes word and a merge,
    apply, push or PR; one that gives the yes to the founder is a relay; a standing permission is counted apart."""
    body = _relay_text(text)
    raw = [x for x in SENTENCE.split(body) if x and x.strip()]
    listed = [bool(re.match(r"^\s*(?:[-*\u2022]|\(?[0-9A-Za-z]{1,2}[).])\s", x)) for x in raw]
    sents = [LEAD.sub("", x, count=1).strip() for x in raw]
    # a numbered list under a lead naming the founder's answers relays them, until the list ends
    founders, under = set(), False
    for i, x in enumerate(sents):
        if FOUNDER_ANSWERS.search(x):
            under = True
            founders.add(i)
        elif under and (listed[i] or re.match(r"^\(?[0-9]{1,2}[).]", x)):
            founders.add(i)
        elif under and not listed[i] and not re.search(r"\(?[0-9]{1,2}\)", x[:6]):
            under = False
    # the unit owner's own standing permission; a standing yes or org-wide rule of the founder is a relay
    standing = any(STANDING.search(x) and "founder" not in x.lower() for x in sents)
    permission = bool(PERMISSION.search(body))
    yes = []
    for i, s in enumerate(sents):
        if not s or i in founders or FOUNDER_YES.search(s) or NOT_YES.search(s) or PERMISSION.search(s) or ASK_ID.search(s):
            continue
        if YES_WORD.search(s) and ACTION.search(s) and (not permission or re.search(r"#\d+", s)):
            yes.append(s)   # in a message about standing permissions, only a yes that names a PR is a yes to a merge
    return yes, standing


def is_unit_owner_yes(text):
    return bool(yes_sentences(text)[0])


def unit_owner_yes(model, rows, a, b, record, revert_days=14):
    """The unit-owner-yes rule (the unit owner's yes stands for the founder's on routine merges and applies) over
    [a, b), by uy-v1: the owner's messages into its keepers' sessions, those with its own yes (one per message), those
    with a standing yes (apart), the keepers' requests to it (rq-v0), the PRs its yeses name joined to work records,
    the reverts of those within revert_days, and all reverts of unit PRs merged under the rule."""
    a, b = epoch(a), epoch(b)
    positions = model["positions"]
    sessions = of_model.session_index(positions)
    owner = record.get("to_owner")
    keepers = set(record.get("positions") or []) - {owner}
    systems = set(record.get("systems") or [])
    messages, yes, standing, requests, named = 0, collections.Counter(), 0, 0, []
    for r in rows:
        if r.get("name") != "of.agent_message_in" or r.get("source") == "of" or not in_window(r, a, b):
            continue
        pl = r.get("payload") or {}
        sender = str(pl.get("from") or "").rpartition(":")[2]
        to = of_model.position_of(r, positions, sessions)
        if sender == owner and to in keepers:
            messages += 1
            ys, st = yes_sentences(pl.get("text"))
            standing += st
            if ys:
                yes[to] += 1
                for sent in ys:
                    named += [(m.group(1), int(m.group(2))) for m in PR_REF.finditer(sent)]
        elif to == owner and sender in keepers and is_request(r):
            requests += 1
    merged, reverts = [], []
    for r in rows:
        pl = r.get("payload") or {}
        if r.get("name") != "of.work_record" or pl.get("source") == "pr-link" or of_model.BOT_AUTHOR.search(str(pl.get("author") or "")):
            continue
        t = epoch(pl.get("merged_at") or r.get("ts"))
        if t is None or not (set(pl.get("systems") or []) & systems):
            continue
        title = str(pl.get("title") or "")
        if title.lower().startswith("revert") and t >= a:
            reverts.append((t, pl))
        elif a <= t < b:
            merged.append((t, pl))
    key = lambda pl: f'{pl.get("repo")}#{pl.get("number")}'
    repo_of = lambda pl: str(pl.get("repo") or "").rpartition("/")[2]
    yes_prs = {}
    for repo, num in named:
        for t, pl in merged:
            if pl.get("number") == num and (not repo or repo == repo_of(pl) or not any(repo == repo_of(q) for _, q in merged)):
                yes_prs[key(pl)] = (t, pl)

    def reverts_of(t, pl):
        title = str(pl.get("title") or "").strip()
        for rt, rp in reverts:
            if rt < t or rt > t + revert_days * DAY_S or repo_of(rp) != repo_of(pl):
                continue
            m = REVERT.match(rp.get("title") or "")
            if (m and m.group(1).strip() == title) or re.search(rf"#{pl.get('number')}\b", rp.get("title") or ""):
                return key(rp)
        return None
    of_yes = [{"pr": k, "revert": rv} for k, (t, pl) in yes_prs.items() for rv in [reverts_of(t, pl)] if rv]
    in_unit = [{"pr": key(pl), "revert": rv} for t, pl in merged for rv in [reverts_of(t, pl)] if rv]
    return {"messages_to_keepers": messages, "owner_yes": sum(yes.values()), "owner_yes_by_keeper": dict(yes.most_common()),
            "standing_yes": standing, "requests_from_keepers": requests, "yes_prs": sorted(yes_prs),
            "yes_prs_merged": len(yes_prs), "unit_prs_merged": len(merged),
            "reverts_of_yes_merges": len(of_yes), "reverts_in_unit": len(in_unit),
            "reverted": {"of_yes_merges": of_yes, "in_unit": in_unit}, "classifier": UY}


DAY_S = 86400.0


def dotted(d, path):
    for part in path.split("."):
        d = d.get(part) if isinstance(d, dict) else None
    return d


def throughput_share(model, msgs):
    """(a): messages to positions whose class is raise or sustain ÷ messages matched to a position (undated: the model's classes)."""
    positions = model["positions"]
    sessions = of_model.session_index(positions)
    c, matched = collections.Counter(), 0
    for m in msgs:
        p = of_model.position_of(m, positions, sessions)
        if p is None:
            c["unregistered"] += 1
            continue
        matched += 1
        c[positions[p].get("throughput") or "unclassified"] += 1
    work = sum(c[k] for k in THROUGHPUT_WORK)
    return {"share": round(work / matched, 3) if matched else None, "matched": matched, "by_class": dict(c)}


def _r(x):
    return round(x, 1) if x is not None else None


# ------------------------------------------------------------------ attention: who needs whom now (§5.2, inbox top)

STALL_AFTER_S = 30 * 60   # an active position idle this long, with no declared reason, has stalled


def attention(model, rows, now, who=of_model.HUMAN, stall_after_s=STALL_AFTER_S):
    """One row per position, deduplicated across sources, most urgent first:
    {position, kind: waiting_owner | stalled | waiting_peer | waiting_external, on, since, sources, text, ask_id}.
    Sources, strongest first: declared asks and statuses (of.ask_declared, of.status_declared), open asks from the
    hooks (agent_question, permission_request), of.pulse verdicts, then the registry's declared waiting_on."""
    positions = model["positions"]
    sessions = of_model.session_index(positions)
    now = epoch(now)
    out = {}

    def put(pos, kind, on, since, source, text=None, ask_id=None):
        if pos is None:
            return
        cur = out.get(pos)
        rank = {"waiting_owner": 0, "stalled": 1, "waiting_peer": 2, "waiting_external": 3}
        if cur is None or rank[kind] < rank[cur["kind"]]:
            out[pos] = cur = {"position": pos, "kind": kind, "on": on, "since": since, "sources": [], "text": text,
                              "ask_id": ask_id}
        elif cur["kind"] == kind:
            if since and (not cur["since"] or since < cur["since"]):
                cur["since"] = since
            cur["text"] = cur["text"] or text
            cur["ask_id"] = cur["ask_id"] or ask_id
        if source not in cur["sources"]:
            cur["sources"].append(source)

    def owner_of(pos):
        return positions[pos]["accountable"] if pos in positions else of_model.HUMAN

    def classify(pos, on):
        if not on or on in ("owner", "founder", who) or on == owner_of(pos):
            return "waiting_owner", owner_of(pos)
        if str(on).startswith(("external:", "resource:")):
            return "waiting_external", on
        return "waiting_peer", on

    ordered = sorted(rows, key=lambda r: epoch(r.get("ts")) or 0)
    # 1. declared asks (`of ask`: of.agent_question with source declared), keyed by topic_key; closed by
    #    of.agent_question_answered or of.ask_withdrawn naming the ask (event id) or its topic
    declared = {}
    for r in ordered:
        pl = r.get("payload") or {}
        name = r["name"]
        if name == "of.agent_question" and "declared" in (pl.get("source"), pl.get("detected_by")):
            pos = pl.get("from_position") or of_model.position_of(r, positions, sessions)
            declared[(pos, pl.get("topic_key") or r["event_id"])] = r   # a re-ask on the topic supersedes
        elif name in ("of.agent_question_answered", "of.ask_withdrawn"):
            refs = {x for x in [pl.get("ask_id"), pl.get("topic_key")] + list(pl.get("ask_ids") or []) if x}
            for k in [k for k, q in declared.items() if q["event_id"] in refs or k[1] in refs]:
                declared.pop(k)
        elif name == "of.state_declared" and pl.get("state") in ("working", "done"):
            pos = pl.get("position") or of_model.position_of(r, positions, sessions)
            for k in [k for k in declared if k[0] == pos]:
                declared.pop(k)
        elif name == "of.human_prompt" and r.get("session_id") and of_model.owner_prompt(r):  # a cron fire answers nothing
            for k in [k for k, q in declared.items() if q.get("session_id") == r["session_id"]]:
                declared.pop(k)
    for (pos, _), r in declared.items():
        pl = r.get("payload") or {}
        q = pl.get("question") or ((pl.get("questions") or [{}])[0] or {}).get("question")
        kind, on = classify(pos, pl.get("to"))
        put(pos, kind, on, r["ts"], "of ask", q, r["event_id"])
    # 2. declared states (`of status`: of.state_declared working | waiting | done | idle, with waiting_on); latest per position
    status = {}
    for r in ordered:
        if r["name"] == "of.state_declared":
            pl = r.get("payload") or {}
            pos = pl.get("position") or of_model.position_of(r, positions, sessions)
            if pl.get("state") not in ("suspended", "resumed"):  # the governor's suspensions are waits by cause, not asks
                status[pos] = r
    for pos, r in status.items():
        pl = r.get("payload") or {}
        if pl.get("state") == "waiting" or (pl.get("waiting_on") and pl.get("state") not in ("working", "done")):
            kind, on = classify(pos, pl.get("waiting_on"))
            put(pos, kind, on, r["ts"], "of status", pl.get("reason"))
    # 3. open asks seen by the hooks or derived from history (declared ones are already in step 1)
    hook_rows = [r for r in ordered if (epoch(r.get("ts")) or 0) <= now
                 and not (r["name"] == "of.agent_question"
                          and "declared" in ((r.get("payload") or {}).get("source"), (r.get("payload") or {}).get("detected_by")))]
    for k in asks_of(model, hook_rows, who):
        if k["state"] == "open" and "topic_key" not in k:   # topic_key: filed with `of ask` (step 1), from the asks view
            put(k.get("position") or "(unregistered)", "waiting_owner", who, k["ts"], k["kind"], k.get("text"), k["event_id"])
    # 4. stalls from `of pulse`: of.stall_detected, until the position shows life again (any later event of it
    #    other than pulse bookkeeping); of.nudge_sent counts the nudges since
    last_seen, stalls, nudges, blocked = {}, {}, collections.Counter(), {}
    for r in ordered:
        pl = r.get("payload") or {}
        herdr_blocked = r["name"] == "of.attention" and pl.get("notification_type") == "herdr_blocked"
        pos = (pl.get("to_position") or pl.get("position")) if r["name"] == "of.nudge_sent" else \
            pl.get("position") if r["name"] == "of.stall_detected" or herdr_blocked else \
            of_model.position_of(r, positions, sessions)
        if r["name"] == "of.stall_detected":
            stalls[pos] = r
            nudges[pos] = 0
        elif r["name"] == "of.nudge_sent":
            nudges[pos] += 1
        elif herdr_blocked:
            blocked[pos] = r   # a permission dialog seen by of pulse, with no hooks: waiting on the owner
        elif r["name"] != "of.pulse" and pos:
            last_seen[pos] = r["ts"]
    for pos, r in blocked.items():
        if pos and (epoch(last_seen.get(pos)) or 0) <= (epoch(r["ts"]) or 0):
            put(pos, "waiting_owner", owner_of(pos), r["ts"], "herdr blocked", (r.get("payload") or {}).get("message"))
    for pos, r in stalls.items():
        if (epoch(last_seen.get(pos)) or 0) > (epoch(r["ts"]) or 0):
            continue
        pl = r.get("payload") or {}
        why = pl.get("reason") or ""
        if nudges[pos]:
            why = (why + "; " if why else "") + f"nudged {nudges[pos]}×"
        put(pos, "stalled", None, pl.get("idle_since") or r["ts"], "of pulse", why or None)
    # 5. the registry's declared waiting_on, for active positions
    for n, p in positions.items():
        w = p.get("waiting_on")
        if w and p.get("status") == "active":
            kind, on = classify(n, w)
            put(n, kind, on, None, "registry")
    # a stall is only a stall without a declared reason
    for pos, s in list(status.items()):
        if pos in out and out[pos]["kind"] == "stalled" and (s.get("payload") or {}).get("reason"):
            out.pop(pos)
    rank = {"waiting_owner": 0, "stalled": 1, "waiting_peer": 2, "waiting_external": 3}
    return sorted(out.values(), key=lambda x: (rank[x["kind"]], x["since"] or "9999", x["position"]))


# ------------------------------------------------------------------ waits by cause (§3.3)

CAUSES = ("owner", "peer", "capacity", "suspended", "external", "stalled")   # priority when sources overlap


def cause_class(cause):
    """'owner' | 'peer:x' | 'capacity:x' | 'suspended' | 'external:x' | 'stalled' -> its class (the colour)."""
    c = str(cause or "").split(":", 1)[0]
    return c if c in CAUSES else "external"


def norm_cause(on, who=of_model.HUMAN, owner=None):
    """A declared waiting_on / blocked_on as a cause."""
    on = str(on or "")
    if not on or on in ("owner", "founder", who) or (owner and on == owner):
        return "owner"
    if on in ("resource:governor", "suspended"):
        return "suspended"
    if on.startswith("resource:"):
        return "capacity:" + on.split(":", 1)[1]
    if on.startswith("external:"):
        return on
    if on.startswith("peer:"):
        return on
    return "peer:" + on


def wait_intervals(model, rows, a, b, who=of_model.HUMAN):
    """[{position, cause, from, to (None while open), session_id, source_event}] from every source, unmerged
    (the input of of_platform's view_waits; the asks' owner intervals name the ask as source_event):
    asks (owner); herdr-blocked episodes (owner); of.state_declared waiting (by waiting_on) and suspended;
    of.state_changed blocked_on; of.stall_detected (stalled, no cause); of.pulse snapshots between runs."""
    positions = model["positions"]
    sessions = of_model.session_index(positions)
    out = []   # (position, cause, t0, t1 | None while open, session_id, source_event)

    def owner_of(pos):
        return positions[pos]["accountable"] if pos in positions else of_model.HUMAN

    for k in asks_of(model, [r for r in rows if epoch(r.get("ts")) is not None and epoch(r["ts"]) < b], who,
                     of_model.delegation_effective(rows)):
        closed = epoch(k["closed_at"]) if k.get("closed_at") else None
        out.append((k.get("position") or "(unregistered)", "owner", epoch(k["ts"]),
                    closed if closed is not None and closed <= b else None, k.get("session_id"), k.get("event_id")))
    open_ = {}          # pos -> (pos, t0, cause, session, source); one open declared state per position
    pending = {}        # pos -> (t0, cause, session, source): episodes that end at the position's next sign of life
    last_pulse = {}     # pos -> ts of the previous pulse that listed it
    prev_pulse_ts = None
    for r in sorted(rows, key=lambda r: epoch(r.get("ts")) or 0):
        t = epoch(r.get("ts"))
        if t is None or t >= b:
            continue
        pl, name, sid, eid = r.get("payload") or {}, r["name"], r.get("session_id"), r.get("event_id")
        pos = pl.get("position") if name in ("of.state_declared", "of.stall_detected") or \
            (name == "of.attention" and pl.get("notification_type") == "herdr_blocked") else \
            of_model.position_of(r, positions, sessions)
        if name in ("of.state_declared", "of.state_changed"):
            pos = pos or of_model.position_of(r, positions, sessions)
            if pos in open_:
                p, t0, c, s0, e0 = open_.pop(pos)
                out.append((p, c, t0, t, s0, e0))
            state, on = pl.get("state"), pl.get("waiting_on") or pl.get("blocked_on")
            if state == "suspended":
                open_[pos] = (pos, t, "suspended", sid, eid)
            elif (state in ("waiting", "blocked") or (name == "of.state_changed" and on)) and on:
                c = norm_cause(on, who, owner_of(pos))
                if c != "owner":            # owner waits come from asks, which carry their own close
                    open_[pos] = (pos, t, c, sid, eid)
        elif name == "of.stall_detected":
            pending[pos] = (epoch(pl.get("idle_since")) or t, "stalled", sid, eid)
        elif name == "of.attention" and pl.get("notification_type") == "herdr_blocked":
            pending[pos] = (t, "owner", sid, eid)
        elif name == "of.pulse":
            for x in pl.get("positions") or []:
                p, kind = x.get("position"), x.get("kind")
                c = {"waiting_owner": "owner", "stalled": "stalled"}.get(kind) or \
                    (norm_cause(x.get("on"), who, owner_of(p)) if kind in ("waiting_peer", "waiting_external") else None)
                if p and c:
                    since = epoch(x.get("since")) or t
                    start = max(since, last_pulse.get(p, since)) if prev_pulse_ts else since
                    out.append((p, c, start, t, None, eid))
                    last_pulse[p] = t
            prev_pulse_ts = t
        elif name != "of.nudge_sent" and pos in pending:     # a sign of life ends a stall or a blocked episode
            t0, c, s0, e0 = pending.pop(pos)
            out.append((pos, c, t0, t, s0, e0))
    for p, t0, c, s0, e0 in open_.values():
        out.append((p, c, t0, None, s0, e0))
    for p, (t0, c, s0, e0) in pending.items():
        out.append((p, c, t0, None, s0, e0))
    res = []
    for p, c, t0, t1, s0, e0 in out:
        end = b if t1 is None else t1
        if p and end > a and t0 < b and end > t0:
            res.append({"position": p, "cause": c, "from": max(t0, a), "to": None if t1 is None else min(t1, b),
                        "session_id": s0, "source_event": e0})
    return res


def waits(model, rows, a, b, bucket_s=3600, who=of_model.HUMAN):
    """{grid: {position: {bucket iso: {cause: minutes}}}, declared, totals}. Every minute of a position counts once,
    for the highest-priority cause among the overlapping sources (CAUSES order), so asks, declared states and
    pulse snapshots of the same wait never add up twice."""
    a, b = epoch(a), epoch(b)
    positions = model["positions"]
    by_pos = collections.defaultdict(list)
    for iv in wait_intervals(model, rows, a, b, who):   # an open interval (to None) runs to the window's end
        by_pos[iv["position"]].append((iv["from"], b if iv["to"] is None else iv["to"], iv["cause"]))
    grid = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    totals = collections.defaultdict(collections.Counter)
    for p, iv in by_pos.items():
        cuts = sorted({t for t0, t1, _ in iv for t in (t0, t1)} |
                      {k * bucket_s for k in range(int(a // bucket_s) + 1, int(b // bucket_s) + 1)})
        for lo, hi in zip(cuts, cuts[1:]):
            live = [c for t0, t1, c in iv if t0 <= lo and t1 >= hi]
            if not live:
                continue
            c = min(live, key=lambda c: CAUSES.index(cause_class(c)))
            mins = (hi - lo) / 60.0
            grid[p][of_model.iso(lo // bucket_s * bucket_s)][c] += mins
            totals[p][cause_class(c)] += mins
    declared = {}
    for n, p in positions.items():
        w = p.get("waiting_on")
        if w and w != "owner" and p.get("status") == "active" and n not in grid:
            declared[n] = w if str(w).startswith("external:") else f"peer:{w}"
    return {"grid": {p: dict(v) for p, v in grid.items()}, "declared": declared,
            "totals": {p: {k: round(v, 1) for k, v in c.items()} for p, c in totals.items()},
            "bucket_s": bucket_s, "from": of_model.iso(a), "to": of_model.iso(b)}


QUEUE_OLD_S = 30 * 60   # of_pulse's threshold for a stall with cause inbound_queue


def inbound_queues(model, rows, a, b, old_after_s=QUEUE_OLD_S):
    """Inbound load per position, from of.inbound_queue (the relay writes one whenever a position's queue on the
    server changes depth, 0 included). The depth is a step function; its last value before a carries into the window.
    {position: {depth_now, oldest_min_now, depth_max, depth_avg, backlog_min, backlog_msg_h, by_day, observed_from, steps}}:
    depth_avg is time-weighted over the observed part of the window; backlog_min counts the minutes the oldest
    waiting message was older than old_after_s. The relay writes only on a change of depth, so an oldest message
    that changes at the same depth (one taken, one arrived) shows at the next change: ages are upper bounds."""
    a, b = epoch(a), epoch(b)
    org = model.get("org")
    ev = collections.defaultdict(list)
    for r in rows:
        if r.get("name") != "of.inbound_queue":
            continue
        pl = r.get("payload") or {}
        if org and pl.get("org") and pl["org"] != org:
            continue
        t, pos = epoch(r.get("ts")), pl.get("position") or r.get("position")
        if t is None or t > b or not pos:
            continue
        ev[pos].append((t, int(pl.get("depth") or 0), epoch(pl.get("oldest_created_at"))))
    out = {}
    for pos, xs in ev.items():
        xs.sort(key=lambda x: x[0])
        before = [x for x in xs if x[0] <= a]
        steps = ([(a, before[-1][1], before[-1][2])] if before else []) + [x for x in xs if x[0] > a]
        if not steps:
            continue
        area = backlog = backlog_msgs = 0.0
        days = collections.defaultdict(lambda: {"observed_min": 0.0, "depth_min": 0.0, "backlog_min": 0.0})
        for (t0, d, o), (t1, _, _) in zip(steps, steps[1:] + [(b, 0, None)]):
            area += d * (t1 - t0)
            old = max(t0, o + old_after_s) if d and o is not None else None
            if old is not None and t1 > old:
                backlog += t1 - old
                backlog_msgs += d * (t1 - old)
            lo = t0
            while lo < t1:   # the same interval, cut at midnight UTC
                hi = min(t1, (lo // 86400 + 1) * 86400)
                day = days[of_model.iso(lo)[:10]]
                day["observed_min"] += (hi - lo) / 60
                day["depth_min"] += d * (hi - lo) / 60
                if old is not None and hi > old:
                    day["backlog_min"] += (hi - max(lo, old)) / 60
                lo = hi
        t_last, d_last, o_last = steps[-1]
        span = b - steps[0][0]
        out[pos] = {"depth_now": d_last,
                    "oldest_min_now": round((b - o_last) / 60, 1) if d_last and o_last is not None else None,
                    "depth_max": max(d for _, d, _ in steps),
                    "depth_avg": round(area / span, 2) if span > 0 else float(d_last),
                    "backlog_min": round(backlog / 60, 1),
                    "backlog_msg_h": round(backlog_msgs / 3600, 2),   # message-hours waited past old_after_s: sender wait, upper bound
                    "by_day": {k: {"observed_min": round(v["observed_min"], 1),
                                   "depth_avg": round(v["depth_min"] / v["observed_min"], 2) if v["observed_min"] else 0.0,
                                   "backlog_min": round(v["backlog_min"], 1)} for k, v in sorted(days.items())},
                    "observed_from": of_model.iso(steps[0][0]),
                    "steps": [(of_model.iso(t), d) for t, d, _ in steps]}
    return out


# ------------------------------------------------------------------ the gate (§4)

def is_codex(r):
    return r.get("harness") == "codex" or r.get("harness_name") == "codex"


def gate(model, rows, index, a, b, exclude_cwd=(), version=FA, qversion=QV0, agent_authors=()):
    """The baseline observations over [a, b) under the pinned classifiers.
    rows: typed events (Claude Code and Codex, told apart by harness); index: transcript index rows
    {session_id, type, timestamp, harness}. As in the baseline, every measure except the Codex count reads Claude only.
    agent_authors: the GitHub logins the agents merge under (the baseline's `scope.agent_authors`)."""
    a, b = epoch(a), epoch(b)
    drop = excluded_sessions(rows, exclude_cwd)
    rows = [r for r in rows if r.get("session_id") not in drop]
    codex = [r for r in rows if is_codex(r) and in_window(r, a, b)]
    rows = [r for r in rows if not is_codex(r)]
    index = [x for x in index if x.get("harness") != "codex"]
    win = [r for r in rows if in_window(r, a, b)]
    F = founder_messages(win, version)
    by_id = {r["event_id"]: r for r in rows}
    o = collections.OrderedDict()
    o["founder_messages_claude"] = len(F)
    o["founder_messages_codex"] = len(founder_messages(codex, version))
    o["status_polls"] = sum(1 for p in F if "status_poll" in _cats(p, version))
    appr = [p for p in F if (labels(p, version) or {}).get("approval")]
    o["short_approvals"] = sum(1 for p in appr if labels(p, version)["approval"] in ("short", "both"))
    o["explicit_permission_wording"] = sum(1 for p in appr if labels(p, version)["approval"] in ("explicit", "both"))
    peers = [r for r in win if r["name"] == "of.agent_message_in"]
    o["peer_messages"] = len(peers)
    o["relayed_owner_words"] = sum(1 for r in peers if (labels(r, version) or {}).get("relayed"))
    plumb = [p for p in F if _cats(p, version)]
    o["plumbing_messages"] = len(plumb)
    o["plumbing_share"] = round(len(plumb) / len(F), 3) if F else None

    # The baseline pairs a founder message with the assistant entry right before it: an ask counts only if the agent
    # took no other turn between the ask and the message (a turn on a peer message, say, makes it a different reply).
    turns = collections.defaultdict(list)
    for r in rows:
        if r["name"] == "of.turn_end" and r.get("session_id"):
            turns[r["session_id"]].append(epoch(r["ts"]) or 0)
    for v in turns.values():
        v.sort()

    def asked(p):
        q = by_id.get((p.get("payload") or {}).get("reply_to"))
        if not q or q["name"] != "of.agent_question":
            return None
        lab = labels(q, qversion)
        ok = lab.get("value") if lab and "value" in lab else bool(lab)
        if not ok:
            return None
        tq, tp = epoch(q["ts"]) or 0, epoch(p["ts"]) or 0
        if any(tq + TURN_SLACK_S < t < tp for t in turns.get(p.get("session_id"), ())):
            return None
        return q
    pairs = [(p, asked(p)) for p in F]
    gaps = [(epoch(p["ts"]) - epoch(q["ts"])) / 60.0 for p, q in pairs if q]
    o["answers_to_agent_asks"] = len(gaps)
    o["answer_delay_min"] = {"p50": _r(quantile(gaps, .5)), "p75": _r(quantile(gaps, .75)), "p90": _r(quantile(gaps, .9))}
    o["answer_delay_over_60min"] = sum(1 for g in gaps if g > 60)
    o["agent_hours_waiting_on_asks"] = round(sum(g for g in gaps if g <= SLEEP_CAP_H * 60) / 60.0, 1)
    o["bare_yes_answers"] = sum(1 for p, q in pairs if q and (labels(p, version) or {}).get("approval") in ("short", "both"))
    o["concurrency_5min"] = concurrency([x for x in index if x.get("session_id") not in drop], a, b)
    o["throughput_share_undated"] = throughput_share(model, F)["share"]
    prs = {(r["payload"].get("repo"), r["payload"].get("number")) for r in rows
           if r["name"] == "of.work_record" and r["payload"].get("author") in agent_authors
           and r["payload"].get("merged_at") and a <= (epoch(r["payload"]["merged_at"][:16]) or 0) < b}
    o["agent_prs_merged"] = len(prs)
    denom = len(F) + o["founder_messages_codex"]
    o["merged_prs_per_message"] = round(len(prs) / denom, 3) if denom else None
    o["merged_prs_per_message_claude_only"] = round(len(prs) / len(F), 3) if F else None
    return o


# What each gated observation reads; a row whose inputs are empty in the window is `blocked`, not failed.
NEEDS = {
    "founder_messages_claude": ("of.human_prompt",), "status_polls": ("of.human_prompt",),
    "short_approvals": ("of.human_prompt",), "explicit_permission_wording": ("of.human_prompt",),
    "plumbing_messages": ("of.human_prompt",), "plumbing_share": ("of.human_prompt",),
    "throughput_share_undated": ("of.human_prompt",),
    "bare_yes_answers": ("of.human_prompt", "of.agent_question"),
    "answers_to_agent_asks": ("of.human_prompt", "of.agent_question"),
    "answer_delay_min": ("of.human_prompt", "of.agent_question"),
    "answer_delay_over_60min": ("of.human_prompt", "of.agent_question"),
    "agent_hours_waiting_on_asks": ("of.human_prompt", "of.agent_question"),
    "peer_messages": ("of.agent_message_in",), "relayed_owner_words": ("of.agent_message_in",),
    "concurrency_5min": ("transcript_index",),
    "agent_prs_merged": ("of.work_record", "agent_authors"),
    "merged_prs_per_message": ("of.work_record", "of.human_prompt", "codex", "agent_authors"),
    "merged_prs_per_message_claude_only": ("of.work_record", "of.human_prompt", "agent_authors"),
    "founder_messages_codex": ("codex",),
}


def present(rows, index, a, b, exclude_cwd=()):
    """The inputs present in [a, b) after the scope: Claude event kinds, plus 'transcript_index' and 'codex'."""
    drop = excluded_sessions(rows, exclude_cwd)
    scoped = [r for r in rows if r.get("session_id") not in drop and in_window(r, a, b)]
    have = {r["name"] for r in scoped if not is_codex(r)}
    if any(x.get("session_id") not in drop and x.get("harness") != "codex" and a <= (epoch(x.get("timestamp")) or 0) < b
           for x in index):
        have.add("transcript_index")
    if any(is_codex(r) and r["name"] == "of.human_prompt" for r in scoped):
        have.add("codex")
    return have


def concurrency(index, a, b, bucket_min=5):
    """Sessions with at least one assistant entry per 5-minute bucket: mean, p90 (baseline percentile), peak."""
    a_m, b_m = int(a // 60), int(b // 60)
    per = collections.defaultdict(set)
    for x in index:
        if x.get("type") != "assistant":
            continue
        t = epoch(x.get("timestamp"))
        if t is None:
            continue
        m = int(t // 60)
        if a_m <= m < b_m:
            per[m // bucket_min].add(x.get("session_id"))
    allb = [len(per.get(k, ())) for k in range(a_m // bucket_min, b_m // bucket_min)]
    return {"mean": round(sum(allb) / max(1, len(allb)), 1), "p90": quantile(allb, .9), "peak": max(allb) if allb else 0}


def _pass(obs, target, tol):
    if obs is None or target is None:
        return False
    lim = max(tol.get("rel", 0) * abs(target), tol.get("abs", 0))
    return abs(obs - target) <= lim + 1e-9


def check(observed, targets, window):
    """[(key, sub, target, observed, tolerance, pass)] for every gated observation of the baseline file in `window`."""
    out = []
    for key, spec in (targets.get("observations") or {}).items():
        tol, tgt, obs = spec.get("tol") or {}, spec.get(window), observed.get(key)
        if isinstance(tgt, dict):
            for sub, tv in tgt.items():
                st = tol.get(sub, tol) if isinstance(tol.get(sub), dict) else tol
                ov = obs.get(sub) if isinstance(obs, dict) else None
                out.append((key, sub, tv, ov, st, _pass(ov, tv, st)))
        else:
            out.append((key, None, tgt, obs, tol, _pass(obs, tgt, tol)))
    return out


# ------------------------------------------------------------------ the flow measure (docs/onboarding-practice.md §5)

FL = "fl-v0"
FLOW_BUCKET_S = 300          # agents at once: 5-minute buckets
FLOW_TURN_CAP_S = SLEEP_CAP_H * 3600   # a turn longer than this lost its end: it counts from its end only
GH, GIT = "of.work_record:gh", "of.work_record:git"   # inputs by source: a work record from GitHub is not a commit

# What each series reads (as NEEDS for the gate): a series whose input is absent in the window is a gap naming it.
FLOW_NEEDS = {
    "merged_prs_per_day": (GH,),
    "pr_lead_time_h": (GH,),
    "agents_at_once": ("of.turn_end",),
    "head_prompts_per_day": ("of.human_prompt",),
    "merged_prs_per_prompt": (GH, "of.human_prompt"),
    "head_asks_per_day": ("of.agent_question",),
    "answer_median_min": ("of.agent_question",),
    "settled_below_share": ("unit_owner", "of.agent_question"),
    "wait_h": ("of.state_declared",),
}
FLOW_LABELS = {
    "merged_prs_per_day": "merged pull requests per day",
    "pr_lead_time_h": "pull request lead time, median hours",
    "agents_at_once": "agents working at once",
    "head_prompts_per_day": "the head's prompts per day",
    "merged_prs_per_prompt": "merged pull requests per prompt",
    "head_asks_per_day": "the head's asks per day",
    "answer_median_min": "the head's answer, median minutes",
    "settled_below_share": "asks settled below the head",
    "wait_h": "position-hours waiting on the head / on a peer",
}
INPUT_WORDS = {GH: "no of.work_record from GitHub (of import github)", GIT: "no of.work_record from git",
               "unit_owner": "no unit owner below the head (no delegated unit)"}
# Never collected from a company of people (§5 "Gaps, never estimated"): listed, never estimated.
FLOW_NEVER = (
    ("process time, %C&A, activity ratio, rolled %C&A",
     "needs each person's time on each item and the receiving step's judgement of what it got (§1.4); OF collects neither"),
    ("work outside GitHub and git", "OF imports only merged pull requests and commits on each repository's default branch"),
    ("the share of people who record their sessions", "OF sees only the sessions it records"),
)


def _gh_prs(rows, a, b):
    """({(repo, number): payload}, bots) of pull requests from GitHub merged in [a, b): pr-link records and commits
    left out, and bots' (a merge queue's own pull requests wrap others' work) counted apart, as code activity does."""
    out, bots = {}, set()
    for r in rows:
        if r.get("name") != "of.work_record":
            continue
        pl = r.get("payload") or {}
        if pl.get("source") == "gh" and pl.get("number") is not None and a <= (epoch(pl.get("merged_at")) or -1) < b:
            k = (pl.get("repo"), pl.get("number"))
            if of_model.BOT_AUTHOR.search(str(pl.get("author") or "")):
                bots.add(k)
            else:
                out[k] = pl
    return out, len(bots)


def agents_at_once(rows, a, b, bucket_s=FLOW_BUCKET_S):
    """Sessions in a turn per 5-minute bucket. A turn runs from the session's first prompt after its previous turn
    end to its of.turn_end; a turn end with no recorded prompt counts in its own bucket. Working buckets are those with
    at least one session in a turn (the organization at work; no calendar is assumed)."""
    by_s = collections.defaultdict(list)
    for r in rows:
        if r.get("name") in ("of.human_prompt", "of.turn_end") and r.get("session_id"):
            t = epoch(r.get("ts"))
            if t is not None and a - FLOW_TURN_CAP_S <= t < b + FLOW_TURN_CAP_S:
                by_s[r["session_id"]].append((t, r["name"] == "of.turn_end"))
    per = collections.defaultdict(set)
    k0, k1 = int(a // bucket_s), int((b - 1) // bucket_s)
    for sid, ev in by_s.items():
        start = None
        for t, end in sorted(ev):
            if not end:
                start = t if start is None else start
                continue
            t0 = start if start is not None and t - start <= FLOW_TURN_CAP_S else t
            start = None
            for k in range(max(k0, int(t0 // bucket_s)), min(k1, int(t // bucket_s)) + 1):
                per[k].add(sid)
    counts = [len(per.get(k, ())) for k in range(k0, k1 + 1)]
    working = [c for c in counts if c]
    return {"mean_working": round(sum(working) / len(working), 2) if working else None,
            "mean_all": round(sum(counts) / len(counts), 2) if counts else None,
            "peak": max(counts) if counts else 0, "working_buckets": len(working), "buckets": len(counts)}


def settled_below(model, rows, a, b, who=of_model.HUMAN):
    """Asks that reached a unit owner below the head in [a, b), and how many of them were escalated to the head."""
    positions = model["positions"]
    owners = sorted({p.get("accountable") for p in positions.values()} - {who, None, ""})
    upto = [r for r in rows if (epoch(r.get("ts")) or 0) < b]
    eff = of_model.delegation_effective(rows)
    asks = [k for o in owners for k in asks_of(model, upto, o, eff)
            if a <= (epoch(k["ts"]) or 0) < b and k.get("source") != "escalated"]
    up = {k.get("escalated_from") for k in asks_of(model, upto, who, eff) if k.get("source") == "escalated"}
    n_up = sum(1 for k in asks if k.get("event_id") in up)
    return {"unit_owners": owners, "asks": len(asks), "escalated": n_up,
            "share": round((len(asks) - n_up) / len(asks), 3) if asks else None}


def flow(model, rows, a, b, who=of_model.HUMAN, own=None):
    """The flow analyst's series over [a, b) (docs/onboarding-practice.md §5): each with its value, or a gap naming the
    input the organization's events lack (never an estimate), then the measures OF never collects.
    own: owner(model, rows, a, b, who) when the caller has it already."""
    a, b = epoch(a), epoch(b)
    own = own or owner(model, rows, a, b, who)
    have = present(rows, [], a, b)
    prs, bots = _gh_prs(rows, a, b)
    if any((r.get("payload") or {}).get("source") == "gh" for r in rows if r.get("name") == "of.work_record" and in_window(r, a, b)):
        have.add(GH)
    commits = {(pl.get("repo"), pl.get("sha")) for pl in (r.get("payload") or {} for r in rows if r.get("name") == "of.work_record")
               if pl.get("source") == "git" and a <= (epoch(pl.get("merged_at")) or -1) < b}
    if commits:
        have.add(GIT)
    below = settled_below(model, rows, a, b, who)
    if below["unit_owners"]:
        have.add("unit_owner")
    days = (b - a) / 86400.0
    prompts = own["owner_messages"]
    lead = [(epoch(p["merged_at"]) - epoch(p["created_at"])) / 3600.0 for p in prs.values()
            if epoch(p.get("created_at")) is not None and epoch(p.get("merged_at")) is not None]
    wt = waits(model, rows, a, b, who=who)["totals"]
    value = {
        "merged_prs_per_day": round(len(prs) / days, 2) if days else None,
        "pr_lead_time_h": _r(quantile(lead, .5)),
        "agents_at_once": agents_at_once(rows, a, b),
        "head_prompts_per_day": round(prompts / days, 2) if days else None,
        "merged_prs_per_prompt": round(len(prs) / prompts, 3) if prompts else None,
        "head_asks_per_day": round(own["arrivals"] / days, 2) if days else None,
        "answer_median_min": own["answer_delay_min"]["p50"],
        "settled_below_share": below["share"],
        "wait_h": {"head": round(sum(t.get("owner", 0) for t in wt.values()) / 60.0, 1),
                   "peer": round(sum(t.get("peer", 0) for t in wt.values()) / 60.0, 1)},
    }
    detail = {"merged_prs_per_day": {"merged": len(prs), "bots_left_out": bots}, "pr_lead_time_h": {"n": len(lead)},
              "head_prompts_per_day": {"prompts": prompts}, "head_asks_per_day": {"asks": own["arrivals"]},
              "answer_median_min": {"n": own["answer_delay_min"]["n"]}, "settled_below_share": below}
    empty = {"pr_lead_time_h": "no merged pull request with its opening time",
             "merged_prs_per_prompt": "no prompt of the head", "answer_median_min": "no answered ask",
             "settled_below_share": "no ask reached a unit owner"}
    series = []
    for key, needs in FLOW_NEEDS.items():
        lack = [n for n in needs if n not in have]
        v = value[key]
        gap = "; ".join(INPUT_WORDS.get(n, f"no {n}") for n in lack) if lack else (empty.get(key) if v is None else None)
        series.append({"key": key, "label": FLOW_LABELS[key], "value": None if lack else v, "gap": gap,
                       "needs": list(needs), **({"detail": detail[key]} if key in detail else {})})
    seen_days = {(epoch(r.get("ts")) or 0) // 86400 for r in rows if in_window(r, a, b)}
    all_days = range(int(a // 86400), int((b - 1) // 86400) + 1)
    return {"method": FL, "who": who, "from": of_model.iso(a), "to": of_model.iso(b), "days": round(days, 2),
            "partial_days": [of_model.iso(d * 86400)[:10] for d in all_days if d * 86400 < a or (d + 1) * 86400 > b],
            "silent_days": [of_model.iso(d * 86400)[:10] for d in all_days if d not in seen_days],
            "commits_from_git": len(commits), "series": series,
            "never_collected": [{"measure": m, "why": why} for m, why in FLOW_NEVER]}
