"""Development loops (RFC 0005 §5, §7): the loop metrics, derived from events and never reported. Owner: of_studio.

A loop belongs to a work: its spec is the work's `loop:` in the registry (RFC 0005 §4; target, measures, ladder,
checkpoint_days, wip_arms). The register and the log are events (§4.1, of_platform's `of loop`):

  of.loop_hypothesis  {work, id, change, predict, cost: {rung, runner_h}, voi: low|med|high, status}
                      a later event with the same id updates the hypothesis (its status above all)
  of.loop_run         {work, hypothesis, rung, result, value, metric, accepted, observed_at, proxy: {for, valid_when,
                      held}, queued_at, started_at, ended_at, runner_h, tokens, evidence}; ended_at defaults to the
                      event's ts, metric to the target's, observed_at and proxy to its measure's
  of.loop_decision    {work, hypothesis, decision: keep|drop|park|reopen|step_up|step_back|change_objective, rung, metric,
                      to_money (also as money), note}; a decided it carries never counts: only the registry cites
                      the owner's yes
  (of_platform's names, docs/events.md: a run's id is `run`; a hypothesis update carries `status` and `status_at`)
  of.loop_cost        {work, run, session, from, to, usd, fresh_tokens, responses, by_model}: of_capacity, about an hour
                      after a run ends, the recording session's spend in the run's window; usd null = unknown

  project(work_id, spec, rows, now)  the loop card's data: ladder, register, curve, cost, decisions and the §5 metrics

The owner's measure (docs/registry.md, Works; RFC 0005 §4.2, 2026-11-14): a target with `decided`, the id of the yes of
the person who owns the work's goal, is itself what the loop's changes are for. Its money is a reading, a measure may
chain to it by `to_target` instead of `to_money`, and a run on such a measure is tied to the target, not drift. The id
counts only when it resolves in the log as a person's yes (of_platform's of_loop.resolve_decided; "Where it is read,
and how it is checked"): project() takes a resolver for that, and without one, or when the id does not resolve, the
target is one without `decided`, and `decided_unresolved` names the id with `decided_unresolved_why`. A resolved
id carries `decided_words`, what the person said, for the reader to check that it names the measure.
  checkpoint_trend(curve, ...)      the target per checkpoint_days period: a standing work's progress (docs/registry.md)

Where a reading is taken (docs/registry.md, Works; RFC 0005 §4.3, 2026-11-14): a run carries `observed_at`, the point
where its value was read, and `proxy` when that reading stands for an event that happens elsewhere, with `held`, whether
the condition that makes it equal to the reading there held in the data the run used. A run the event does not mark
takes both from its metric's measure (`observed_at`, `proxy_for`), its `held` then unknown. A run whose proxy did not
hold, or is not known to hold, is not observed: it stays out of the curve of the target (and so out of the gains, the
plateau and the checkpoints) and shows as "not observed at <at>", its reading labelled as a proxy (not_observed_text).

Pure functions over typed rows; no herdr, no network. docs/studio.md §5.6.
"""

import statistics

from of_model import epoch, iso

UNREAD = ("unread", None)   # of_loop.UNREAD: a resolver's answer when this machine's log cannot be read

KINDS = ("of.loop_hypothesis", "of.loop_run", "of.loop_decision", "of.loop_cost")
DAY = 86400
VOI = {"low": 1, "med": 2, "medium": 2, "high": 3}
STATUS_OF = {"keep": "kept", "drop": "dropped", "park": "parked", "reopen": "open"}   # of_loop.STATUS_OF: reopen puts a
# parked, dropped or kept hypothesis back to open (RFC 0005 §4.1; of_platform's change of 2026-11-14, hand-over 7b804b09)
PLATEAU_CYCLES, PLATEAU_GAIN = 3, 0.01     # RFC 0005 §6: 3 cycles in a row gain under 1% of the target
DRIFT_RUNS = 3                             # the latest runs that decide whether the loop measures its target
HELD = ("held", "not_held", "unknown")     # of_loop.PROXY_HELD: whether a proxy's condition held in the run's data
HELD_TEXT = {"not_held": "did not hold", "unknown": "is not known to hold"}


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _segment(raw):
    """The target's controlled segment as the registry writes it (docs/registry.md Works, "The target's controlled
    segment", organic-framework PR 58): {start, end, context}, or None when the target names none. `context` is always
    a list of its parts as written, each a text or a mapping {what, name (optional)}; a lone text becomes a list of one,
    and a part of any other shape is left out (`of registry check` calls it a problem)."""
    if not isinstance(raw, dict):
        return None
    seg = dict(raw)
    ctx = raw.get("context")
    ctx = ctx if isinstance(ctx, list) else [ctx] if isinstance(ctx, str) else []
    seg["context"] = [dict(x) if isinstance(x, dict) else x for x in ctx
                      if isinstance(x, str) or (isinstance(x, dict) and isinstance(x.get("what"), str))]
    return seg


def context_label(part):
    """A context part by its text, or its `what` with its name: what the loop card lists beside the target."""
    if isinstance(part, dict):
        return f'{part["what"]} ({part["name"]})' if part.get("name") else part["what"]
    return part


def decided_of(target):
    """The owner's yes on the target's measure (docs/registry.md, Works, "The owner's measure"): the id as written, when
    it is a non-empty text as `of registry check` takes it, else None (the target is then priced in money, as before)."""
    d = (target or {}).get("decided") if isinstance(target, dict) else None
    return d.strip() if isinstance(d, str) and d.strip() else None


def log_resolver(conn=None, conductors=None):
    """(id, work) -> the person's yes it names in this machine's log, as of_platform's of_loop.resolve_decided finds it
    (docs/registry.md, Works, "Where it is read, and how it is checked"): an ask as `<ask id>#<option>`, filed for WORK
    with that option as its yes (`of ask --work W --yes N`) and chosen by the person's answer; or a prompt a person
    typed in the session of a conductor (CONDUCTORS, by default every listed organization's positions with role
    conductor, read when a prompt is first checked). Each lookup once, through a read-only connection opened on the
    first; UNREAD when the store will not open, or this host has no of_loop (of-server's image). for_work binds it to
    one work."""
    import sqlite3
    held, memo = {"conn": conn, "conductors": conductors}, {}

    def typed_by_person(position, host):
        if held["conductors"] is None:
            held["conductors"] = conductor_positions()
        return position in held["conductors"]

    def resolve(did, work=None):
        key = (did, work)
        if key not in memo:
            try:
                import of_loop
                if held["conn"] is None:
                    import of_fold
                    held["conn"] = of_fold.reader() or False
                memo[key] = of_loop.resolve_decided(held["conn"], did, typed_by_person, work) if held["conn"] else UNREAD
            except (ImportError, sqlite3.Error, ValueError):
                memo[key] = UNREAD
        return memo[key]
    return resolve


def for_work(resolve, work):
    """RESOLVE (id, work) bound to one work: id -> its yes. An ask counts only when filed for that work."""
    return (lambda did: resolve(did, work)) if resolve else None


def conductor_positions(orgs_file=None):
    """The positions with role conductor in every organization this machine lists, from their registries' working
    files: the sessions where only a person types (bin/of's _decided_resolver reads the same). Empty when the list
    will not read."""
    try:
        import of_model
        import of_studio
        out = set()
        for o in of_studio.orgs(orgs_file):
            doc = of_model.registry_at(o["repo"])[0] or {}
            out |= {str(x.get("name")) for x in doc.get("positions") or [] if isinstance(x, dict) and x.get("role") == "conductor"}
        return out
    except Exception:  # noqa: BLE001 - no list, or a registry that will not read: no prompt counts
        return set()


def owners_yes(did, resolve):
    """(the id when RESOLVE finds it in the log as a person's yes, else None; why it does not count, else None; the
    cited words, else None): `unread` when the log could not be read, `not_found` when it holds no such yes,
    `not_looked_up` with no resolver."""
    if not did:
        return None, None, None
    found = resolve(did) if resolve else None
    if found and found != UNREAD:
        return did, None, getattr(found, "words", None) or None
    return None, ("unread" if found == UNREAD else "not_found" if resolve else "not_looked_up"), None


UNRESOLVED_WHY = {"unread": "this machine's event log could not be read to look it up",
                  "not_found": "this machine's log holds no such yes (an ask filed for this work with its yes option, "
                               "which the owner chose, or a prompt typed in a conductor's session)",
                  "not_looked_up": "it was not looked up in a log here",
                  "decision": "a loop decision never cites the owner's yes: the structure keeper rewrites the target "
                              "and its decided in the registry"}


def tied(spec):
    """The measures a run may measure without drifting from the target (RFC 0005 §6): those with `to_money`, and on a
    target the owner decided also those with `to_target` (§4.2)."""
    own = bool(spec["target"].get("decided"))
    return {str(m["name"]) for m in spec["measures"] if m.get("to_money") or (own and m.get("to_target"))}


def spec_of(raw, resolve=None):
    """The work's `loop:` normalized, or None. RESOLVE: id -> the person's yes in the log (log_resolver); the target's
    `decided` is its id only when that finds it."""
    if not isinstance(raw, dict):
        return None
    target = raw.get("target") or {}
    ladder = [dict(r) for r in raw.get("ladder") or [] if isinstance(r, dict) and r.get("rung")]
    names = [str(r["rung"]) for r in ladder]
    prod = {str(r["rung"]) for r in ladder if r.get("production")}
    if not prod and "shadow" in names:   # RFC 0005 §4: a production rung is shadow or higher
        prod = set(names[names.index("shadow"):])
    t = {k: target.get(k) for k in ("metric", "money", "stake", "throughput_class", "observed_at")}
    t["better"] = "lower" if str(target.get("better") or "").lower() == "lower" else "higher"
    t["segment"] = _segment(target.get("segment"))
    t["decided"], why, words = owners_yes(decided_of(target), resolve)
    if words:   # the cited words beside the id: the ask's question and chosen option, or the prompt's first words
        t["decided_words"] = words
    if why:   # the loop is one without decided, and says why (docs/registry.md, Works, "Where it is read")
        t["decided_unresolved"], t["decided_unresolved_why"] = decided_of(target), why
    return {"target": t,
            "measures": [dict(m, to_money=m.get("to_money"), to_target=m.get("to_target"))
                         for m in raw.get("measures") or [] if isinstance(m, dict) and m.get("name")],
            "ladder": ladder, "rungs": names, "production": prod,
            "checkpoint_days": _num(raw.get("checkpoint_days")) or 3, "wip_arms": int(_num(raw.get("wip_arms")) or 2),
            "babysit_model": raw.get("babysit_model") or "sonnet"}


def proxy_of(raw, decl=None):
    """A run's proxy (docs/registry.md, Works, "Where a reading is taken"): {for, valid_when, held} from the event,
    each missing part from DECL, its measure's proxy_for {at, valid_when}; held unknown when not given. None when
    neither names one: the reading is taken where its event happens."""
    raw = raw if isinstance(raw, dict) else {}
    decl = decl if isinstance(decl, dict) and decl.get("at") else {}
    if not raw and not decl:
        return None
    held = str(raw.get("held") or "unknown").strip().lower()
    return {"for": raw.get("for") or raw.get("at") or decl.get("at"),
            "valid_when": raw.get("valid_when") or decl.get("valid_when"),
            "held": held if held in HELD else "unknown"}


def observed(run):
    """Whether RUN's reading counts as taken where its event happens: no proxy, or one whose condition held."""
    p = (run or {}).get("proxy")
    return not p or p.get("held") == "held"


def not_observed_text(run):
    """How a run that is not observed reads, as `of loop show` reads it: 'not observed at <at>', then its reading at
    observed_at labelled as a proxy, never the number alone."""
    p = run.get("proxy") or {}
    val = run.get("value")
    reading = f'{run.get("metric") or "?"} {val:g}' if isinstance(val, (int, float)) else f'{run.get("metric") or "?"}'
    return (f'not observed at {p.get("for") or "the point where its event happens (not named)"}; its reading at '
            f'{run.get("observed_at") or "a point not named"} is a proxy ({reading}), valid only when '
            f'{p.get("valid_when") or "a condition not named"}, which {HELD_TEXT.get(p.get("held"), HELD_TEXT["unknown"])}')


def events(rows, work_id):
    return sorted((r for r in rows if r.get("name") in KINDS and (r.get("payload") or {}).get("work") == work_id),
                  key=lambda r: epoch(r.get("ts")) or 0)


def _hypotheses(evs):
    hyp = {}
    for r in evs:
        pl, t = r.get("payload") or {}, epoch(r.get("ts")) or 0
        if r["name"] == "of.loop_hypothesis":
            hid = str(pl.get("id") or pl.get("hypothesis") or str(r.get("event_id") or "")[-8:])
            t = epoch(pl.get("status_at")) or t   # of_platform: a status update carries its own time
            h = hyp.setdefault(hid, {"id": hid, "recorded_at": t, "status": "open"})
            cost = pl.get("cost") if isinstance(pl.get("cost"), dict) else {}
            if pl.get("status") and pl["status"] != h.get("status"):
                h["status_at"] = t
            for k in ("change", "predict", "voi", "status", "evidence"):
                if pl.get(k) is not None:
                    h[k] = pl[k]
            h.setdefault("status_at", t)
            if cost.get("rung") or pl.get("cost_rung"):
                h["cost_rung"] = cost.get("rung") or pl.get("cost_rung")
            if _num(cost.get("runner_h") if cost else pl.get("cost_runner_h")) is not None:
                h["cost_runner_h"] = _num(cost.get("runner_h") if cost else pl.get("cost_runner_h"))
            h["updated_at"] = t
        elif r["name"] == "of.loop_decision" and pl.get("hypothesis") and pl.get("decision") in STATUS_OF:
            h = hyp.setdefault(str(pl["hypothesis"]), {"id": str(pl["hypothesis"]), "recorded_at": t})
            h["status"], h["decided_at"], h["status_at"] = STATUS_OF[pl["decision"]], t, t
            if pl["decision"] == "reopen":   # open again: neither kept nor dropped, and no longer decided
                h.pop("decided_at")
        elif r["name"] == "of.loop_run" and pl.get("hypothesis"):
            h = hyp.get(str(pl["hypothesis"]))
            if h is not None and h.get("status") == "open":
                h["status"], h["status_at"] = "testing", t   # a run started it; a decision ends it
    return hyp


def _runs(evs, spec, hyp):
    out, last_end = [], {}
    costs = {str((r.get("payload") or {}).get("run")): r.get("payload") or {} for r in evs if r["name"] == "of.loop_cost"}
    target = (spec["target"].get("metric") or "").strip()
    measures = {str(m["name"]): m for m in spec["measures"]}
    for r in evs:
        if r["name"] != "of.loop_run":
            continue
        pl = r.get("payload") or {}
        end = epoch(pl.get("ended_at")) or epoch(r.get("ts")) or 0
        start = epoch(pl.get("started_at"))
        hid = str(pl.get("hypothesis") or "")
        # entering the rung: queued_at, else the end of this hypothesis' previous run, else when it was recorded
        entered = epoch(pl.get("queued_at")) or last_end.get(hid) or (hyp.get(hid) or {}).get("recorded_at") or start or end
        metric = str(pl.get("metric") or target)
        decl = {"observed_at": spec["target"].get("observed_at")} if metric == target else measures.get(metric) or {}
        out.append({"observed_at": pl.get("observed_at") or decl.get("observed_at"),
                    "proxy": proxy_of(pl.get("proxy"), decl.get("proxy_for")),
                    "rung": str(pl.get("rung") or "?"), "hypothesis": hid or None, "result": pl.get("result"),
                    "value": _num(pl.get("value")), "metric": metric, "on_target": metric == target,
                    "entered": min(entered, end), "started": start, "ended": end, "runner_h": _num(pl.get("runner_h")) or 0.0,
                    "tokens": _num(pl.get("tokens")), "evidence": pl.get("evidence"), "event_id": pl.get("run") or r.get("event_id"),
                    "accepted": pl.get("accepted") if isinstance(pl.get("accepted"), bool) else None,
                    "queued_at": epoch(pl.get("queued_at")), "position": pl.get("position") or r.get("position"),
                    "session": r.get("session_id"), "model": pl.get("model") or r.get("model")})
        c = costs.get(str(out[-1]["event_id"]))
        out[-1].update(usd=_num((c or {}).get("usd")), fresh_tokens=_num((c or {}).get("fresh_tokens")), costed=c is not None)
        if hid:
            last_end[hid] = end
    return out


def _priority(h):
    """Value of information per cost (RFC 0005 §4.1), as `of loop` orders its register (of_loop.register): voi
    low/med/high = 1/2/3, an absent one med (docs/registry.md, Works, "The owner's measure"), over the runner hours
    (1 when not given, at least 0.05); money never enters it."""
    rh = h.get("cost_runner_h")
    return VOI.get(str(h.get("voi") or "med").lower(), 1) / max(1.0 if rh is None else rh, 0.05)


def _wip_series(hyp, evs):
    """Hypotheses in test over time, [(ts, n)]: +1 when a run starts an open one, -1 when it is decided or parked."""
    steps, state = [], {}
    for r in evs:
        pl, t = r.get("payload") or {}, epoch(r.get("ts")) or 0
        hid = str((pl.get("id") or pl.get("hypothesis")) if r["name"] == "of.loop_hypothesis" else pl.get("hypothesis") or "")
        if not hid:
            continue
        new = state.get(hid, "open")
        if r["name"] == "of.loop_run" and new == "open":
            new = "testing"
        elif r["name"] == "of.loop_decision" and pl.get("decision") in STATUS_OF:
            new = STATUS_OF[pl["decision"]]
        elif r["name"] == "of.loop_hypothesis" and pl.get("status"):
            new = pl["status"]
        old = state.get(hid, "open")
        if (old == "testing") != (new == "testing"):
            steps.append((t, 1 if new == "testing" else -1))
        state[hid] = new
    out, n = [], 0
    for t, dlt in steps:
        n += dlt
        out.append((t, n))
    return out


def babysit(rows, doers, a, b):
    """Per doer: tool calls classed runner or monitor (of.mandate_check `command_class`, of_platform), by model."""
    out = {}
    for r in rows:
        if r.get("name") != "of.mandate_check":
            continue
        pl, t = r.get("payload") or {}, epoch(r.get("ts")) or 0
        cls, who = pl.get("command_class"), r.get("position") or pl.get("position")
        if cls in ("runner", "monitor") and who in doers and a <= t < b:
            m = str(pl.get("model") or r.get("model") or "unknown")
            out.setdefault(who, {"runner": {}, "monitor": {}})[cls][m] = out.get(who, {}).get(cls, {}).get(m, 0) + 1
    return out


def project(work_id, spec, rows, now, days=7, work=None, resolve=None):
    """The loop card and the rules' projection (docs/diagnose.md §4.1). Every number comes from the events; a missing
    input shows as None, never as zero. work: the registry's entry, for its owner and doers. resolve: id -> the
    person's yes in the log (log_resolver), for the target's `decided` and a change of objective's; without it neither
    counts."""
    spec = spec_of(spec, resolve) if spec and "rungs" not in spec else spec
    evs = events(rows, work_id)
    if not spec and not evs:
        return None
    spec = spec or spec_of({})
    hyp = _hypotheses(evs)
    runs = _runs(evs, spec, hyp)
    week = now - days * DAY
    rungs = list(spec["rungs"]) + sorted({r["rung"] for r in runs} - set(spec["rungs"]))

    ladder = []
    for n in rungs:
        rr = [r for r in runs if r["rung"] == n]
        wk = [r for r in rr if r["ended"] >= week]
        cyc = [(r["ended"] - r["entered"]) / 3600 for r in rr if r["ended"] >= r["entered"]]
        waited = [(r["started"] - r["entered"], r["ended"] - r["entered"]) for r in rr
                  if r["started"] and r["ended"] > r["entered"] and r["started"] >= r["entered"]]
        decl = next((x for x in spec["ladder"] if str(x["rung"]) == n), {})
        ladder.append({"rung": n, "cost": decl.get("cost"), "latency": decl.get("latency"), "accept": decl.get("accept"),
                       "declared": bool(decl), "production": n in spec["production"], "runs": len(rr), "runs_week": len(wk),
                       "cycles_per_day": round(len(wk) / days, 2),
                       "cycle_time_h": round(statistics.median(cyc), 2) if cyc else None,
                       "wait_share": round(sum(w for w, _ in waited) / sum(t for _, t in waited), 3) if waited else None,
                       "last_result": rr[-1]["result"] if rr else None, "last_at": rr[-1]["ended"] if rr else None,
                       "last_run": {k: rr[-1][k] for k in ("metric", "value", "observed_at", "proxy")} if rr else None})
    rank = {n: i for i, n in enumerate(spec["rungs"])}   # only the ladder orders rungs: one it does not name ranks lowest
    reached = max(runs, key=lambda r: (rank.get(r["rung"], -1), -r["ended"])) if runs else None
    highest = None
    if reached:
        first = min(r["ended"] for r in runs if r["rung"] == reached["rung"])
        highest = {"rung": reached["rung"], "at": first}
    start = min([h["recorded_at"] for h in hyp.values()] + [r["entered"] for r in runs] +
                [epoch(r.get("ts")) or now for r in evs]) if evs else None
    prod = [r for r in runs if r["rung"] in spec["production"]]
    last_prod = max((r["ended"] for r in prod), default=None)
    since = last_prod if last_prod else start
    checkpoint = {"last": last_prod, "days_since": round((now - since) / DAY, 1) if since else None,
                  "due_days": spec["checkpoint_days"],
                  "overdue": bool(since) and (now - since) / DAY > spec["checkpoint_days"] and bool(spec["production"])}
    first_money = min((r["ended"] for r in prod), default=None)

    decided = [h for h in hyp.values() if h.get("status") in ("kept", "dropped")]
    kept = [h for h in decided if h["status"] == "kept"]
    curve = sorted(([r["ended"], r["value"], r["rung"], r["hypothesis"]] for r in runs  # a run not observed decides nothing
                    if r["on_target"] and r["value"] is not None and observed(r)), key=lambda p: p[0])
    sign = -1 if spec["target"].get("better") == "lower" else 1   # a gain is a step towards better
    gains = [sign * (curve[i][1] - curve[i - 1][1]) for i in range(1, len(curve))]
    kept_ids = {h["id"] for h in kept}
    kept_gains = [sign * (curve[i][1] - curve[i - 1][1]) for i in range(1, len(curve)) if curve[i][3] in kept_ids]
    plateau = None
    if len(curve) > PLATEAU_CYCLES:
        tail = curve[-(PLATEAU_CYCLES + 1):]
        flat = all(abs(tail[i][1] - tail[i - 1][1]) < PLATEAU_GAIN * abs(tail[i - 1][1] or 1) for i in range(1, len(tail)))
        plateau = {"from": tail[1][0], "to": tail[-1][0], "flat": flat}

    tied_to = tied(spec)
    latest = sorted(runs, key=lambda r: r["ended"])[-DRIFT_RUNS:]
    drift = [r["metric"] for r in latest if not r["on_target"] and r["metric"] not in tied_to]
    wk_runs = [r for r in runs if r["ended"] >= week]
    kept_week = [h for h in kept if (h.get("decided_at") or 0) >= week]
    runner_h = round(sum(r["runner_h"] for r in wk_runs), 2)
    tokens = [r["fresh_tokens"] if r["fresh_tokens"] is not None else r["tokens"] for r in wk_runs
              if r["fresh_tokens"] is not None or r["tokens"] is not None]
    usd = [r["usd"] for r in wk_runs if r["usd"] is not None]
    testing = [h for h in hyp.values() if h.get("status") == "testing"]
    decisions = [dict((r.get("payload") or {}), ts=epoch(r.get("ts"))) for r in evs if r["name"] == "of.loop_decision"]
    for x in decisions:   # only the registry cites the owner's yes: a decision's own decided never counts, and `of loop`
        # refuses a change of objective on a decided loop (docs/registry.md, Works, "The measure changes only ...")
        raw = x.get("decided").strip() if isinstance(x.get("decided"), str) and x["decided"].strip() else None
        x["decided"] = None
        if raw:
            x["decided_unresolved"], x["decided_unresolved_why"] = raw, "decision"
    by_time = lambda hs, k: sorted(hs, key=lambda h: h.get(k) or h.get("updated_at") or 0, reverse=True)
    work = work or {}
    doers = [str(x) for x in work.get("doers") or []]
    loop_start = start or now
    return {
        "work": work_id, "owner": work.get("owner"), "doers": doers,
        "declared": bool(spec["rungs"]) or bool(spec["target"].get("metric")), "spec": spec,
        "target": spec["target"], "measures": spec["measures"],
        "ladder_spec": [{"rung": str(x["rung"]), "cost": x.get("cost"), "latency": x.get("latency"), "accept": x.get("accept"),
                         "production": str(x["rung"]) in spec["production"],
                         "since": epoch(x.get("since")) or loop_start} for x in spec["ladder"]],
        "checkpoint_days": spec["checkpoint_days"], "wip_arms": spec["wip_arms"], "babysit_model": spec["babysit_model"],
        "hypotheses": [{"id": h["id"], "ts": h["recorded_at"], "status": h.get("status"), "status_at": h.get("status_at"),
                        "change": h.get("change"), "predict": h.get("predict")} for h in hyp.values()],
        "runs": [{"id": r["event_id"], "rung": r["rung"], "hypothesis": r["hypothesis"], "metric": r["metric"], "value": r["value"],
                  "accepted": r["accepted"], "on_target": r["on_target"], "observed_at": r["observed_at"], "proxy": r["proxy"],
                  "result": r["result"],
                  "queued_at": r["queued_at"], "started_at": r["started"], "ended_at": r["ended"],
                  "position": r["position"], "session": r["session"], "model": r["model"], "usd": r["usd"],
                  "fresh_tokens": r["fresh_tokens"]} for r in runs],
        "decisions_all": [{"ts": x["ts"], "kind": x.get("decision"), "hypothesis": x.get("hypothesis"), "metric": x.get("metric"),
                           "to_money": x.get("to_money") or x.get("money"), "decided": x["decided"],
                           **{k: x[k] for k in ("decided_unresolved", "decided_unresolved_why") if x.get(k)}}
                          for x in sorted(decisions, key=lambda x: x["ts"] or 0)],
        "metrics": {"rungs": {x["rung"]: {"runs": x["runs"], "cycle_time": x["cycle_time_h"], "wait_share": x["wait_share"]}
                              for x in ladder},
                    "wip": _wip_series(hyp, evs),
                    "production": {"last_at": last_prod, "days_since": checkpoint["days_since"] if last_prod else None},
                    "babysit": babysit(rows, set(doers), week, now)},
        "events": len(evs), "start": start, "ladder": ladder, "highest": highest, "checkpoint": checkpoint,
        "time_to_first_money_days": round((first_money - start) / DAY, 1) if first_money and start else None,
        "hit_rate": round(len(kept) / len(decided), 3) if decided else None, "kept": len(kept), "decided": len(decided),
        "gain_per_cycle": round(statistics.mean(gains), 4) if gains else None,
        "gain_per_kept_cycle": round(statistics.mean(kept_gains), 4) if kept_gains else None,
        "curve": curve, "plateau": plateau,
        "trend": checkpoint_trend(curve, start, now, spec["checkpoint_days"], spec["target"].get("better")),
        "drift": sorted(set(drift)) if drift and len(drift) == len(latest) else [],
        "cost_week": {"runner_h": runner_h, "tokens": sum(tokens) if tokens else None,
                      "usd": round(sum(usd), 2) if usd else None,
                      "runs_without_usd": sum(1 for r in wk_runs if r["usd"] is None),   # pending (an hour) or no start
                      "usd_per_kept_cycle": round(sum(usd) / len(kept_week), 2) if usd and kept_week else None,
                      "runner_h_per_kept_cycle": round(runner_h / len(kept_week), 2) if kept_week else None},
        "wip": {"testing": len(testing), "limit": spec["wip_arms"], "over": len(testing) > spec["wip_arms"]},
        "register": {"open": sorted((h for h in hyp.values() if h.get("status") == "open"), key=_priority, reverse=True),
                     "testing": by_time(testing, "updated_at"),
                     "last_kept": by_time(kept, "decided_at")[:3],
                     "last_dropped": by_time([h for h in decided if h["status"] == "dropped"], "decided_at")[:3],
                     "parked": [h for h in hyp.values() if h.get("status") == "parked"]},
        "decisions": sorted(decisions, key=lambda x: x["ts"] or 0, reverse=True)[:20],
    }


def checkpoint_trend(curve, start, now, days, better=None):
    """The target by checkpoint (docs/registry.md, a standing work: its loop is its end condition): the loop's time
    from its start cut into periods of checkpoint_days, each with the best on-target value of the runs that ended in it
    (None when none did). `change` compares the last two periods with a value, signed so that a step towards better is
    positive; `pct` is that change over the earlier value."""
    if not start or not days or days <= 0:
        return {"checkpoints": [], "last": None, "previous": None, "change": None, "pct": None}
    span = days * DAY
    n = max(1, int(-(-(now - start) // span)))
    best = min if better == "lower" else max
    out = []
    for i in range(n):
        a, b = start + i * span, start + (i + 1) * span
        vals = [p[1] for p in curve if a <= p[0] < b or (i == n - 1 and p[0] >= b)]
        out.append({"from": a, "to": min(b, now), "best": best(vals) if vals else None, "runs": len(vals)})
    have = [c for c in out if c["best"] is not None]
    last, prev = (have[-1] if have else None), (have[-2] if len(have) > 1 else None)
    sign = -1 if better == "lower" else 1
    change = sign * (last["best"] - prev["best"]) if last and prev else None
    return {"checkpoints": out, "last": last, "previous": prev,
            "change": round(change, 6) if change is not None else None,
            "pct": round(change / abs(prev["best"]), 4) if change is not None and prev["best"] else None}


def trend_text(card, k=4):
    """One line: 'day pass: 211.7 → 290 → 325.8 by 3-day checkpoint (+12.3%)'; None without a measured checkpoint."""
    tr = (card or {}).get("trend") or {}
    cps = tr.get("checkpoints") or []
    if not tr.get("last"):
        return None
    vals = " → ".join("–" if c["best"] is None else f"{c['best']:g}" for c in cps[-k:])
    metric = (card.get("target") or {}).get("metric") or "target"
    pct = f" ({'+' if tr['pct'] >= 0 else ''}{round(100 * tr['pct'], 1)}%)" if tr.get("pct") is not None else ""
    return f"{metric}: {'… ' if len(cps) > k else ''}{vals} by {card.get('checkpoint_days'):g}-day checkpoint{pct}"


def summary(card, now):
    """One line for the Works list: highest rung, days since a production checkpoint, cycles this week, hit rate."""
    if not card:
        return None
    parts = []
    if card["highest"]:
        parts.append(f"highest rung {card['highest']['rung']}")
    ck = card["checkpoint"]
    if ck["days_since"] is not None and card["spec"]["production"]:
        parts.append(f"{'no production checkpoint yet' if not ck['last'] else 'last production checkpoint'} "
                     f"{ck['days_since']:g} d{' ago' if ck['last'] else ''}")
    runs_week = sum(x["runs_week"] for x in card["ladder"])
    parts.append(f"{runs_week} run{'s' if runs_week != 1 else ''} this week")
    if card["hit_rate"] is not None:
        parts.append(f"hit rate {round(100 * card['hit_rate'])}% of {card['decided']}")
    return " · ".join(parts)


def stamp(t):
    return iso(t)[:16].replace("T", " ") if t else "–"
