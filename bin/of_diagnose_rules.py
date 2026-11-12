"""The detection rules of the anti-pattern catalogue (docs/diagnose.md; thresholds and priors in docs/diagnose-v0.yaml).
Owner: of_field-analyst, single writer since 2026-11-07 (sd-2026-11-07-of-team-roles, the founder's ask 909d03a2).

A rule is `fn(model, rows, a, b, cat, spec[, parts][, context=None])` and returns issues, or (issues, missing[, notes]):
  model    MODEL(t) from of_model.build;   rows  the organization's typed events;   [a, b)  the window, epoch seconds
  cat      the parsed catalogue;           spec  the rule's entry in it
  parts    the runnable parts, when the rule's `needs` is keyed by part (its last positional argument)
  context  optional {keeper_since: fn(system, keeper) -> epoch | None}, from the registry history
Every issue is built with of_model.issue(...). RULES maps a catalogue name to its function. The run (bin/of_diagnose.py,
of_studio's) checks the inputs, applies the owner's labels, routes issues to units, merges and ranks them.

The rules read the model and the rows only through of_model.RULE_SURFACE: tests/test_studio.py pins its names and
parameters, and checks that this module imports nothing else of the platform. A reading the surface lacks is added
there first (ask of_studio); it is never computed here from another module's internals.
"""

import collections
import os
import re

import of_model
from of_model import epoch, issue


def is_scratch(area):
    """Temp and scratchpad paths: /tmp, /private/tmp, $TMPDIR, /var/folders, anything under a `scratchpad` dir."""
    a = str(area or "").lstrip("~/").lstrip("/")
    tmpdir = os.environ.get("TMPDIR", "").strip("/")
    return (a.startswith(("tmp", "private/tmp", "private/var/folders", "var/folders")) or "scratchpad" in a.split("/")
            or (tmpdir and a.startswith(tmpdir)))


def rule_orphan_work(model, rows, a, b, cat, spec, parts=("cluster", "unmapped")):
    t = spec.get("thresholds") or {}
    out = []
    for area, by in ((model.get("orphan_work") or {}).items() if "cluster" in parts else ()):
        if is_scratch(area):
            continue   # a temp dir or a harness scratchpad is not work on any system
        n = sum(by.values())
        if n >= t.get("unowned_verdicts_per_cluster_gte", 3):
            out.append(issue(cat, "orphan-work", [area], {"unowned_calls": n, "by": by},
                             f"{n} tool calls in {area}, which no keeper answers for", part="cluster"))
    # keeperless (v0-v2) moved to unkept-system (2.23) in v3: a live system nobody keeps, with its merged PRs as evidence
    wr = model.get("work_records") or {}
    total, unmapped = wr.get("total") or 0, wr.get("unmapped") or 0
    if "unmapped" in parts and total and unmapped / total >= t.get("unmapped_wr_share_gte", 0.05):
        out.append(issue(cat, "orphan-work", ["(unmapped)"], {"unmapped": unmapped, "total": total},
                         f"{unmapped} of {total} merged changes ({round(100 * unmapped / total)}%) touch no path the systems map claims",
                         part="unmapped"))
    return out


def kept_outside(system):
    """registry.md: `governed_by: external:<who>` is kept by a person or party outside the organization; it counts as kept."""
    return str(system.get("governed_by") or "").startswith("external:")


def is_live(system):
    """registry.md `lifecycle`: live (the default) | unsupported | retired. Keeper and owner rules apply to live systems only."""
    return (system.get("lifecycle") or "live") == "live"


def observed_positions(model, rows, a, b, min_days=1):
    """Positions whose own sessions recorded activity (hook or import events, not bookkeeping written by `of` such as
    of.mandate_changed) on at least `min_days` distinct days of the window: only for those is silence evidence."""
    positions = model["positions"]
    sessions = of_model.session_index(positions)
    days = {}
    for r in rows:
        if of_model.in_window(r, a, b) and r.get("source") in ("hook", "import"):
            p = of_model.position_of(r, positions, sessions)
            if p:
                days.setdefault(p, set()).add(str(r["ts"])[:10])
    return {p for p, d in days.items() if len(d) >= min_days}


def rule_coordination_gap(model, rows, a, b, cat, spec):
    t = spec.get("thresholds") or {}
    systems, positions, pdsm = model["systems"], model["positions"], model["position_dsm"]
    days = t.get("observed_days_gte") or (cat.get("defaults") or {}).get("observed_days_gte", 3)
    seen = observed_positions(model, rows, a, b, days)
    out, unobserved = [], 0
    for r in of_model.overlay(model):
        declared = bool(r["ab"] or r["ba"])
        if not declared and r["coupling"] < t.get("co_changed_wr_gte", 3):
            continue
        ka, kb = (systems.get(r["a"]) or {}).get("keeper"), (systems.get(r["b"]) or {}).get("keeper")
        if not ka or not kb or ka == kb:
            continue
        if any((positions.get(k) or {}).get("status") != "active" for k in (ka, kb)):
            continue
        if ka not in seen or kb not in seen:   # no events of a keeper: silence is not evidence
            unobserved += 1
            continue
        met = any((pdsm.get(e) or {}).get(k) for e in ((ka, kb), (kb, ka)) for k in ("messages", "reads", "asks"))
        if met:
            continue
        why = (f"declared {r['ab'] or r['ba']}" if declared else "") + \
              (f"{' and ' if declared else ''}{r['coupling']} PRs changed both" if r["coupling"] else "")
        out.append(issue(cat, "coordination-gap", [r["a"], r["b"], ka, kb],
                         {"systems": [r["a"], r["b"]], "keepers": [ka, kb], "coupling": r["coupling"], "declared": declared},
                         f"{ka} and {kb} keep {r['a']} and {r['b']} ({why}) and exchanged nothing in the window"))
    # severity rises with co-change: rank among themselves by coupling (score stays 0: no owner minutes)
    out.sort(key=lambda i: -i["evidence"]["coupling"])
    if unobserved and not out:
        return out, [f"activity of the keepers on {days}+ days ({unobserved} required edges unobserved)"]
    return out


def rule_function_gap(model, rows, a, b, cat, spec):
    """A management function no active mandate covers. Critical only when a stakeholder map says a right-to-operate
    interest depends on it; no map exists as data yet, so these are ranked issues."""
    active = {n: p for n, p in model["positions"].items() if p.get("status") == "active"}
    covered = {}
    for n, p in active.items():
        for f in p.get("functions") or []:
            covered.setdefault(f, []).append(n)
    out = []
    for f in of_model.MANAGEMENT_FUNCTIONS:
        if f not in covered:
            out.append(issue(cat, "function-gap", [f"function:{f}"], {"function": f, "covered": sorted(covered),
                                                                       "stakeholder_map": None},
                             f"No active mandate covers the management function '{f.replace('_', ' ')}'"))
    return out


def rule_lingering(model, rows, a, b, cat, spec):
    """A mandate past its review date by `exit_met_hours_gte`, still active, not extended by a later of.mandate_changed.
    `done_when` is prose in v0, so only the date half is checked; the evidence carries the criterion for the owner."""
    t = spec.get("thresholds") or {}
    positions = model["positions"]
    sessions = of_model.session_index(positions)
    changed = {}
    for r in rows:
        if r["name"] == "of.mandate_changed":
            pos = (r.get("payload") or {}).get("position") or of_model.position_of(r, positions, sessions)
            changed[pos] = max(changed.get(pos, 0), epoch(r.get("ts")) or 0)
    mpp = (cat.get("defaults") or {}).get("minutes_per_prompt", 1.5)
    out = []
    for n, p in positions.items():
        ex = p.get("exit") or {}
        review = epoch(str(ex.get("review"))) if isinstance(ex, dict) and ex.get("review") else None
        if p.get("status") != "active" or review is None:
            continue
        due = review + 86400   # the review day ends at midnight UTC
        if b - due < t.get("exit_met_hours_gte", 48) * 3600 or changed.get(n, 0) > due:
            continue
        prompts = sum(1 for r in rows if r["name"] == "of.human_prompt" and of_model.owner_prompt(r) and (epoch(r.get("ts")) or 0) > due
                      and of_model.position_of(r, positions, sessions) == n)
        days = int((b - due) // 86400)
        out.append(issue(cat, "lingering", [n], {"review": ex.get("review"), "done_when": ex.get("done_when"),
                                                 "owner_prompts_since": prompts},
                         f"{n}: mandate review was {ex.get('review')} ({days} d ago), still active, not extended",
                         delta_m=prompts * mpp))
    return out


# ------------------------------------------------------------------ shared signals

def peer_messages(model, rows, a, b):
    """Counter{(p, q)}: messages p sent q in the window (of.agent_message_out with a resolved to_position)."""
    positions = model["positions"]
    sp = of_model.session_positions(model, rows)
    c = collections.Counter()
    for r in rows:
        if r["name"] != "of.agent_message_out" or not of_model.in_window(r, a, b):
            continue
        pl = r.get("payload") or {}
        p, q = r.get("position") if r.get("position") in positions else sp.get(r.get("session_id")), pl.get("to_position")
        if p and q in positions and p != q and pl.get("delivery") not in ("unresolved", "expired"):
            c[(p, q)] += 1
    return c


def work_by_position(model, rows, lo, hi):
    """(Counter{position: merged PRs}, {system: Counter{position: PRs}}) over [lo, hi): non-bot merged PRs attributed
    through the event's position or a transcript pr-link (of_model.pr_attribution)."""
    positions = model["positions"]
    attrib = of_model.pr_attribution(rows, positions, of_model.session_index(positions))
    by_pos, by_sys = collections.Counter(), {}
    for r in rows:
        pl = r.get("payload") or {}
        if r["name"] != "of.work_record" or pl.get("source") == "pr-link" or not (lo <= (epoch(r.get("ts")) or 0) < hi):
            continue
        if of_model.BOT_AUTHOR.search(str(pl.get("author") or "")):
            continue
        who = r.get("position") if r.get("position") in positions else attrib.get(of_model.pr_key(pl))
        if not who:
            continue
        by_pos[who] += 1
        for sid in pl.get("systems") or []:
            by_sys.setdefault(sid, collections.Counter())[who] += 1
    return by_pos, by_sys


def owner_relays(model, rows, a, b, who=of_model.HUMAN):
    """Counter{(p, q)}: the owner's routing or relay-paste prompts to q's session that name another position p."""
    positions = model["positions"]
    sp = of_model.session_positions(model, rows)
    names = {}
    for n, pos in positions.items():
        for alias in [n] + list(pos.get("aliases") or []):
            if len(str(alias)) >= 4:
                names[str(alias).lower()] = n
    rx = re.compile(r"(?<![\w-])(" + "|".join(sorted(map(re.escape, names), key=len, reverse=True)) + r")(?![\w-])", re.I) \
        if names else None
    c = collections.Counter()
    keeper = of_model.structure_keeper(model)   # talking to the structure keeper about positions is its job, not a relay
    msgs = of_model.founder_messages([r for r in rows if of_model.in_window(r, a, b)]) if who == of_model.HUMAN else []
    for m in msgs:
        if not set(of_model.message_categories(m)) & {"routing", "relay_paste"}:
            continue
        q = m.get("position") if m.get("position") in positions else sp.get(m.get("session_id"))
        text = str((m.get("payload") or {}).get("text") or "")
        for p in {names[x.lower()] for x in (rx.findall(text) if rx else [])}:
            if q and p != q and q != keeper:
                c[(p, q)] += 1
    return c


def _trend(values, last_days, tol):
    """(rising, flat) of a daily series: rising = positive least-squares slope and the last N days above the median;
    flat = |slope per week| within tol of the series mean."""
    n = len(values)
    if n < 3:
        return False, False
    xm, ym = (n - 1) / 2, sum(values) / n
    den = sum((i - xm) ** 2 for i in range(n))
    slope = sum((i - xm) * (v - ym) for i, v in enumerate(values)) / den if den else 0.0
    med = sorted(values)[n // 2]
    rising = slope > 0 and all(v > med for v in values[-last_days:])
    flat = abs(slope * 7) <= tol * ym if ym else True
    return rising, flat


# ------------------------------------------------------------------ the owner, the pair, the system

def rule_owner_router(model, rows, a, b, cat, spec):
    t, dflt = spec.get("thresholds") or {}, cat.get("defaults") or {}
    out = []
    humans = [h for h in model["units"] if h not in model["positions"] or model["positions"][h].get("runtime") == "human"]
    for who in humans or [of_model.HUMAN]:
        o = of_model.owner_measures(model, rows, a, b, who=who)
        fired = []
        if (o.get("plumbing_share") or 0) > t.get("plumbing_share_gt", 0.05):
            fired.append(f"plumbing {round(100 * o['plumbing_share'], 1)}% of {o['owner_messages']} messages")
        if (o.get("inbound_concentration") or 0) > t.get("in_degree_share_gt", 0.25):
            fired.append(f"in-degree share {o['inbound_concentration']} ({o['arrivals']} asks, {o.get('peer_requests', 0)} peer requests)")
        if t.get("l_rising_lambda_flat"):
            asks = of_model.asks_of(model, [r for r in rows if (epoch(r.get("ts")) or 0) < b], who)
            days = [of_model.little(asks, d, d + 86400) for d in range(int(a), int(b), 86400)]
            l_rise, _ = _trend([x["L"] for x in days], dflt.get("trend_last_days", 3), dflt.get("flat_tolerance", 0.2))
            _, lam_flat = _trend([x["lambda_per_h"] for x in days], dflt.get("trend_last_days", 3), dflt.get("flat_tolerance", 0.2))
            if l_rise and lam_flat:
                fired.append("decision inventory L rising while arrivals λ stay flat (saturated)")
        if fired:
            plumb = round((o.get("plumbing_share") or 0) * (o.get("owner_messages") or 0))
            out.append(issue(cat, "owner-router", [who], {"signals": fired, "plumbing_messages": plumb,
                                                         "in_degree_share": o.get("inbound_concentration"),
                                                         "status_polls": o.get("status_polls")},
                             f"{who} routes the organization: " + "; ".join(fired),
                             delta_m=plumb * dflt.get("minutes_per_prompt", 1.5) * 0.5))
    return out


def received_pairs(model, rows, a, b, label=None):
    """(Counter{(p, q)} of messages q received from p, Counter{(p, q)} of those carrying `label`)."""
    positions = model["positions"]
    sp = of_model.session_positions(model, rows)
    version, _, key = (label or "").partition(":")
    total, marked = collections.Counter(), collections.Counter()
    for r, p, _, _ in of_model.message_senders(model, rows, a, b):
        q = r.get("position") if r.get("position") in positions else sp.get(r.get("session_id"))
        if not p or not q or p == q:
            continue
        total[(p, q)] += 1
        if label and (of_model.received_labels(r).get(version) or {}).get(key):
            marked[(p, q)] += 1
    return total, marked


def labels_of(r, version):
    return ((r.get("payload") or {}).get("classifications") or {}).get(version)


def rule_owner_relay(model, rows, a, b, cat, spec):
    t, mpp = spec.get("thresholds") or {}, (cat.get("defaults") or {}).get("minutes_per_prompt", 1.5)
    positions = model["positions"]
    skip = set(t.get("exclude_sender_kinds") or [])
    relays = owner_relays(model, rows, a, b)
    total, words = received_pairs(model, rows, a, b, t.get("owner_words_label") or "fa-2026-11-05:relayed")
    out = []
    for pair in set(relays) | set(words):
        p, q = pair
        if positions.get(p, {}).get("kind") in skip:
            continue   # carrying the owner's decisions is a coordinator's job
        if "owner" in (positions.get(p, {}).get("kind"), positions.get(q, {}).get("kind")):
            continue   # handing a unit owner its unit, or naming one's own position, is delegation, not a relay
        n_r, n_w, n_all = relays.get(pair, 0), words.get(pair, 0), total.get(pair, 0)
        share = n_w / n_all if n_all else 0.0
        words_hit = n_w >= t.get("pair_owner_words_gte", 5) and share >= t.get("pair_owner_words_share_gte", 0.0)
        if n_r >= t.get("pair_owner_relays_gte", 3) or words_hit:
            out.append(issue(cat, "owner-relay", [p, q],
                             {"owner_relays": n_r, "messages_with_owner_words": n_w, "pair_messages": n_all,
                              "owner_words_share": round(share, 2)},
                             f"The owner relays between {p} → {q}: {n_r} routing or pasted prompts, "
                             f"{n_w} of {n_all} messages carrying the owner's words", delta_m=n_r * mpp, key=f"{p}->{q}"))
    return sorted(out, key=lambda i: -(i["evidence"]["owner_relays"] + i["evidence"]["messages_with_owner_words"]))


def rule_over_coupled(model, rows, a, b, cat, spec):
    t = spec.get("thresholds") or {}
    positions = model["positions"]
    mpp = (cat.get("defaults") or {}).get("minutes_per_prompt", 1.5)
    msgs = peer_messages(model, rows, a, b)
    wr, _ = work_by_position(model, rows, a, b)
    seen = observed_positions(model, rows, a, b, t.get("observed_days_gte") or (cat.get("defaults") or {}).get("observed_days_gte", 3))
    waits, declared_days = collections.defaultdict(set), collections.defaultdict(set)
    for r in rows:
        if r["name"] == "of.state_declared" and of_model.in_window(r, a, b):
            pl = r.get("payload") or {}
            declared_days[pl.get("position")].add(str(r["ts"])[:10])
            if pl.get("state") == "waiting" and pl.get("waiting_on") in positions:
                waits[pl.get("position")].add(pl["waiting_on"])
    days_needed = t.get("observed_days_gte") or (cat.get("defaults") or {}).get("observed_days_gte", 3)
    per_pair = t.get("waits_recorded") == "per_pair"
    org_recorded = any(waits.values())
    relays = owner_relays(model, rows, a, b)
    skip_kinds = set(t.get("exclude_pair_kinds") or [])
    keeper = of_model.structure_keeper(model) if t.get("exclude_structure_keeper") else None
    out = []
    for p, q in {tuple(sorted(k)) for k in msgs}:
        if p not in seen or q not in seen:
            continue
        if "coordinator" in (positions[p].get("kind"), positions[q].get("kind")):
            continue   # coordination of shared capacity is chatty-coordination's case
        if skip_kinds & {positions[p].get("kind"), positions[q].get("kind")} or keeper in (p, q):
            continue   # a unit owner with its unit's positions, or the structure keeper: their mandate, not a split to merge
        pq, qp = msgs.get((p, q), 0), msgs.get((q, p), 0)
        ratio = (pq + qp) / max(1, wr.get(p, 0) + wr.get(q, 0))
        recip = min(pq, qp) / max(pq, qp) if max(pq, qp) else 0.0
        mutual = int(q in waits.get(p, ()) and p in waits.get(q, ()))
        waits_recorded = (len(declared_days.get(p, ())) >= days_needed and len(declared_days.get(q, ())) >= days_needed) \
            if per_pair else org_recorded
        third = mutual >= t.get("mutual_waits_gte", 1) if waits_recorded else recip >= t.get("reciprocity_without_waits_gte", 0.8)
        if ratio >= t.get("interface_ratio_gte", 3) and recip >= t.get("reciprocity_gte", 0.5) and third:
            n_rel = relays.get((p, q), 0) + relays.get((q, p), 0)
            out.append(issue(cat, "over-coupled", [p, q],
                             {"messages": {f"{p}->{q}": pq, f"{q}->{p}": qp}, "merged_prs": {p: wr.get(p, 0), q: wr.get(q, 0)},
                              "interface_ratio": round(ratio, 1), "reciprocity": round(recip, 2), "mutual_waits": mutual,
                              "waits_recorded": waits_recorded},
                             f"{p} and {q} exchanged {pq + qp} messages for {wr.get(p, 0) + wr.get(q, 0)} merged PRs "
                             f"(ratio {round(ratio, 1)}, reciprocity {round(recip, 2)})", delta_m=n_rel * mpp))
    return sorted(out, key=lambda i: -i["evidence"]["interface_ratio"])


def rule_fragmented_ownership(model, rows, a, b, cat, spec, parts=("runtime", "code"), context=None):
    t = spec.get("thresholds") or {}
    out, notes = [], []
    since_fn = (context or {}).get("keeper_since")
    if "code" in parts:
        width = b - a
        windows = [work_by_position(model, rows, a - k * width, b - k * width)[1] for k in range(t.get("consecutive_windows", 2))]
        for sid, s in model["systems"].items():
            keeper = s.get("keeper")
            if not keeper:
                continue   # no keeper is orphan-work's case
            first_start = a - (t.get("consecutive_windows", 2) - 1) * width
            hits = []
            for w in windows:
                c = w.get(sid) or collections.Counter()
                total = sum(c.values())
                share = c.get(keeper, 0) / total if total else 1.0
                hits.append((len(c), share, total))
            if all(n >= t.get("code_writers_gte", 3) and share < t.get("keeper_share_lt", 0.3) for n, share, _ in hits):
                since = since_fn(sid, keeper) if t.get("keeper_held_at_window_start") and since_fn else None
                if since and since > first_start:   # the windows measure writes from before the keeper existed
                    notes.append(f"{sid}: quiet, keeper {keeper} since {of_model.iso(since)[:10]}")
                    continue
                n, share, total = hits[0]
                cur = windows[0].get(sid) or {}
                out.append(issue(cat, "fragmented-ownership", [sid, keeper],
                                 {"writers": dict(cur), "keeper": keeper, "keeper_share": round(share, 2),
                                  "windows": [{"writers": x[0], "keeper_share": round(x[1], 2), "prs": x[2]} for x in hits]},
                                 f"{n} positions wrote {sid} ({total} merged PRs); its keeper {keeper} wrote "
                                 f"{round(100 * share)}%, for {len(hits)} windows running", part="code"))
    return out, [], notes


def _field(pl, path):
    v = pl
    for part in path.split("."):
        v = v.get(part) if isinstance(v, dict) else None
    return v


def mandate_changes(model, rows, a, b, fields=("summary", "throughput"), ignore_added=False, settle_days=0):
    """{position: [(ts, changed fields)]}: of.mandate_changed snapshots in [a, b) that differ from the position's
    previous snapshot in `fields` (dotted paths; `status_reopened` = done or finished back to active). The first
    snapshot is the baseline; with ignore_added, a field absent before is not a change (schema adoption), and
    `mandate_exit.done_when` changes only from a non-empty value; changes within settle_days of the first snapshot
    don't count."""
    positions = model["positions"]
    sp = of_model.session_positions(model, rows)
    last, first, out = {}, {}, collections.defaultdict(list)
    for r in sorted((r for r in rows if r["name"] == "of.mandate_changed"), key=lambda r: epoch(r.get("ts")) or 0):
        pl = r.get("payload") or {}
        pos = pl.get("position") or (r.get("position") if r.get("position") in positions else sp.get(r.get("session_id")))
        if not pos:
            continue
        t = epoch(r.get("ts")) or 0
        first.setdefault(pos, t)
        prev = last.get(pos)
        if prev is not None and a <= t < b and t - first[pos] >= settle_days * 86400:
            changed = []
            for f in fields:
                if f == "status_reopened":
                    if prev.get("status") in ("done", "finished") and pl.get("status") == "active":
                        changed.append(f)
                    continue
                old, new = _field(prev, f), _field(pl, f)
                if old == new or (ignore_added and old in (None, "", [], {})):
                    continue
                changed.append(f)
            if changed:
                out[pos].append((r["ts"], changed))
        last[pos] = pl
    return out


def rule_unclear_mandate(model, rows, a, b, cat, spec):
    t = spec.get("thresholds") or {}
    out = []
    fields = tuple(t.get("substantive_fields") or ("summary", "throughput"))
    for pos, ch in mandate_changes(model, rows, a, b, fields, bool(t.get("ignore_added_fields")), t.get("settle_days", 0)).items():
        if len(ch) >= t.get("mandate_changes_gte", 2):
            out.append(issue(cat, "unclear-mandate", [pos], {"changes": [{"ts": ts, "fields": f} for ts, f in ch]},
                             f"{pos}: mandate changed {len(ch)} times in the window ("
                             + ", ".join(sorted({x for _, f in ch for x in f})) + ")"))
    return out


def rule_churn(model, rows, a, b, cat, spec):
    t = spec.get("thresholds") or {}
    positions = model["positions"]
    sp = of_model.session_positions(model, rows)
    changes = collections.defaultdict(list)          # position -> [day]
    for pos, ch in mandate_changes(model, rows, a, b).items():
        changes[pos] += [str(ts)[:10] for ts, _ in ch]
    for r in rows:
        if r["name"] == "of.holder_changed" and of_model.in_window(r, a, b):
            pl = r.get("payload") or {}
            pos = pl.get("position") or (r.get("position") if r.get("position") in positions else sp.get(r.get("session_id")))
            if pos:
                changes[pos].append(str(r["ts"])[:10])
    # merged PRs per day, the window and the 7 days before it, for the throughput dip
    per_day, last_wr = collections.Counter(), 0.0
    for r in rows:
        pl = r.get("payload") or {}
        if r["name"] == "of.work_record" and pl.get("source") != "pr-link" and not of_model.BOT_AUTHOR.search(str(pl.get("author") or "")):
            per_day[str(pl.get("merged_at") or r["ts"])[:10]] += 1
            last_wr = max(last_wr, epoch(pl.get("merged_at") or r["ts"]) or 0)
    complete_until = min(b, last_wr)   # a day counts only when it ended and its merged PRs were imported

    def dip(day):
        d0 = epoch(day)
        if d0 + 86400 > complete_until:
            return 0, per_day.get(day, 0), False
        prev = sorted(per_day.get(of_model.iso(d0 - k * 86400)[:10], 0) for k in range(1, 8))
        med = prev[3]
        return med, per_day.get(day, 0), (med > 0 and per_day.get(day, 0) <= (1 - t.get("wr_drop_gte", 0.5)) * med)

    active = [n for n, p in positions.items() if p.get("status") == "active"]
    by_day = collections.Counter(d for pos, days in changes.items() if pos in active for d in set(days))
    out = []
    for pos, days in changes.items():
        if len(days) >= t.get("changes_per_position_gte", 2):
            hits = [d for d in sorted(set(days)) if dip(d)[2]]
            if hits:
                out.append(issue(cat, "churn", [pos], {"changes": sorted(days), "dip_days": hits},
                                 f"{pos}: {len(days)} holder or mandate changes, merged PRs fell on {', '.join(hits)}"))
    for day, n in by_day.items():
        if active and n / len(active) >= t.get("share_of_positions_one_day_gte", 0.5):
            med, got, hit = dip(day)
            if hit:
                out.append(issue(cat, "churn", [f"day:{day}"], {"positions_changed": n, "active": len(active),
                                                                "merged_prs": got, "trailing_median": med},
                                 f"{day}: {n} of {len(active)} active positions changed at once; merged PRs {got} against a median of {med}"))
    return out


def rule_chatty(model, rows, a, b, cat, spec, parts=("default", "receiver_flood", "repeat_nudges")):
    """Parts: default (messages per merged PR, the organization and its pairs), receiver_flood and repeat_nudges (turns a
    receiver started on typed-in text). With needs keyed by part, only the parts whose inputs are present run."""
    t = spec.get("thresholds") or {}
    out = _chatty_injected(model, rows, a, b, cat, t, parts)
    if "default" not in parts:
        return sorted(out, key=lambda i: -(i["evidence"].get("peak_per_hour") or i["evidence"].get("nudges") or 0))
    last_wr = max((epoch((r.get("payload") or {}).get("merged_at") or r.get("ts")) or 0 for r in rows
                   if r["name"] == "of.work_record" and (r.get("payload") or {}).get("source") != "pr-link"), default=b)
    end = min(b, (last_wr // 86400) * 86400)   # the org trend counts only ended days whose merged PRs were imported
    width = max(86400.0, end - a)
    msgs, prev_msgs = peer_messages(model, rows, a, end), peer_messages(model, rows, a - width, a)
    wr, _ = work_by_position(model, rows, a, end)
    wr_prev, _ = work_by_position(model, rows, a - width, a)
    pair_msgs = peer_messages(model, rows, a, b)          # pairs: the whole window
    pair_wr, _ = work_by_position(model, rows, a, b)
    seen = observed_positions(model, rows, a, b, t.get("observed_days_gte") or (cat.get("defaults") or {}).get("observed_days_gte", 3))
    cur = sum(msgs.values()) / max(1, sum(wr.values()))
    before = sum(prev_msgs.values()) / max(1, sum(wr_prev.values()))
    notes = []
    src_now, src_before = _message_sources(rows, a, end), _message_sources(rows, a - width, a)
    if src_now != src_before and sum(prev_msgs.values()):
        # the trend compares like with like: from 2026-11-06 the relay logs every `of send` (source `of`), where the
        # windows before saw only messages found in transcripts (import, hook): about 6 times as many in the first user organization
        notes.append(f"organization part not compared: messages recorded by {', '.join(sorted(map(str, src_before)))} in the "
                     f"window before and by {', '.join(sorted(map(str, src_now)))} in this one")
    elif sum(prev_msgs.values()) and before and (cur - before) / before >= t.get("org_msgs_per_wr_rise_gte", 0.5):
        out.append(issue(cat, "chatty", ["(organization)"], {"msgs_per_wr": round(cur, 2), "before": round(before, 2)},
                         f"Messages per merged PR rose from {round(before, 1)} to {round(cur, 1)} against the previous window"))
    for p, q in {tuple(sorted(k)) for k in pair_msgs}:
        if p not in seen or q not in seen:
            continue
        n = pair_msgs.get((p, q), 0) + pair_msgs.get((q, p), 0)
        w = pair_wr.get(p, 0) + pair_wr.get(q, 0)
        if n >= t.get("pair_msgs_per_wr_gte", 10) and n / max(1, w) >= t.get("pair_msgs_per_wr_gte", 10):
            out.append(issue(cat, "chatty", [p, q], {"messages": n, "merged_prs": w, "msgs_per_wr": round(n / max(1, w), 1)},
                             f"{p} and {q}: {n} messages for {w} merged PRs ({round(n / max(1, w), 1)} per PR)"))
    return sorted(out, key=lambda i: -(i["evidence"].get("msgs_per_wr") or i["evidence"].get("peak_per_hour") or 0)), [], notes


def _message_sources(rows, lo, hi, min_share=0.1):
    """The recorders that wrote at least `min_share` of the window's of.agent_message_out (import, hook, of)."""
    c = collections.Counter(r.get("source") for r in rows if r["name"] == "of.agent_message_out"
                            and lo <= (epoch(r.get("ts")) or 0) < hi)
    n = sum(c.values())
    return {x for x, k in c.items() if n and k / n >= min_share}


NUDGE_FRAME = re.compile(r"^\s*(?:<pasted_content[^>]*>\s*)?Message from (\S+) \(via of pulse")


RELAY_FROM = re.compile(r"^\s*(?:<pasted_content[^>]*>\s*)?Message from (?:[\w-]+:)?([\w-]+) \(via relay")


def injected_turns(model, rows, a, b):
    """[(ts, receiver, kind, sender)]: turns started in a receiver's session by text typed into its pane, not by its owner
    (the founder, 2026-11-07): a relayed message (of.agent_message_in from the hook), a pulse nudge (the hook's prompt in
    of pulse's frame), or another tool's note (a prompt whose corrected origin is injected)."""
    positions = model["positions"]
    sessions = of_model.session_index(positions)
    out = []
    for r in rows:
        if not of_model.in_window(r, a, b):
            continue
        pl = r.get("payload") or {}
        text = (pl.get("text") or "").lstrip()
        recv = of_model.position_of(r, positions, sessions)
        if not recv:
            continue
        if r["name"] == "of.agent_message_in" and r.get("source") == "hook":
            m = RELAY_FROM.match(text)
            out.append((r["ts"], recv, "relay", (m.group(1) if m else None) or pl.get("from_position") or pl.get("from")))
        elif r["name"] == "of.human_prompt":
            m = NUDGE_FRAME.match(text)
            if m:
                out.append((r["ts"], recv, "nudge", m.group(1)))
            elif pl.get("origin") == "injected":
                out.append((r["ts"], recv, "tool", None))
    return out


def _chatty_injected(model, rows, a, b, cat, t, parts=("receiver_flood", "repeat_nudges")):
    """The receiver's side of chatter: hours in which typed-in messages and nudges started at least
    `injected_turns_per_hour_gte` of its turns, on at least `flood_hours_gte` hours (part receiver_flood), and a
    waiter nudging the same peer at least `nudges_per_pair_gte` times (part repeat_nudges)."""
    turns = injected_turns(model, rows, a, b)
    per_hour, kinds, senders = collections.Counter(), collections.defaultdict(collections.Counter), collections.defaultdict(collections.Counter)
    nudges = collections.Counter()
    for ts, recv, kind, sender in turns:
        per_hour[(recv, str(ts)[:13])] += 1
        kinds[recv][kind] += 1
        if sender:
            senders[recv][sender] += 1
        if kind == "nudge" and sender:
            nudges[(sender, recv)] += 1
    out = []
    rate, hours_min = t.get("injected_turns_per_hour_gte", 20), t.get("flood_hours_gte", 3)
    by_recv = collections.defaultdict(list)
    for (recv, h), n in per_hour.items():
        by_recv[recv].append((n, h))
    for recv, hs in (by_recv.items() if "receiver_flood" in parts else ()):
        flood = sorted((x for x in hs if x[0] >= rate), reverse=True)
        if len(flood) >= hours_min:
            total = sum(kinds[recv].values())
            out.append(issue(cat, "chatty", [recv], {"injected_turns": total, "by_kind": dict(kinds[recv]), "flood_hours": len(flood),
                                                     "peak_per_hour": flood[0][0], "peak_hour": flood[0][1] + ":00Z",
                                                     "top_senders": dict(senders[recv].most_common(5))},
                             f"{recv}: typed-in messages and nudges started {total} turns; {len(flood)} hours with "
                             f"{rate} or more (peak {flood[0][0]} at {flood[0][1][11:13]}:00Z)",
                             part="receiver_flood", key=f"flood:{recv}"))
    for (waiter, recv), n in (nudges.items() if "repeat_nudges" in parts else ()):
        if n >= t.get("nudges_per_pair_gte", 10):
            out.append(issue(cat, "chatty", [waiter, recv], {"nudges": n, "waiter": waiter, "nudged": recv},
                             f"of pulse nudged {recv} {n} times for {waiter}", part="repeat_nudges",
                             key=f"nudges:{waiter}->{recv}"))
    return out


def betweenness(edges):
    """Normalized betweenness centrality on an undirected, unweighted graph {node: set(neighbours)} (Brandes)."""
    nodes = list(edges)
    cb = dict.fromkeys(nodes, 0.0)
    for s_ in nodes:
        stack, pred, sigma, dist = [], {v: [] for v in nodes}, dict.fromkeys(nodes, 0), dict.fromkeys(nodes, -1)
        sigma[s_], dist[s_] = 1, 0
        queue = [s_]
        while queue:
            v = queue.pop(0)
            stack.append(v)
            for w in edges[v]:
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    queue.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    pred[w].append(v)
        delta = dict.fromkeys(nodes, 0.0)
        while stack:
            w = stack.pop()
            for v in pred[w]:
                delta[v] += sigma[v] / sigma[w] * (1 + delta[w])
            if w != s_:
                cb[w] += delta[w]
    n = len(nodes)
    norm = (n - 1) * (n - 2) if n > 2 else 1
    return {v: c / norm for v, c in cb.items()}   # undirected: each pair counted twice, (n-1)(n-2) normalizes it


def rule_bottleneck_broker(model, rows, a, b, cat, spec):
    t = spec.get("thresholds") or {}
    mpp = (cat.get("defaults") or {}).get("minutes_per_prompt", 1.5)
    msgs = peer_messages(model, rows, a, b)
    total = sum(msgs.values())
    if not total:
        return []
    inbound = collections.Counter()
    for (p, q), n in msgs.items():
        inbound[q] += n
    pair = collections.Counter()
    for (p, q), n in msgs.items():
        pair[tuple(sorted((p, q)))] += n
    graph = collections.defaultdict(set)   # strong ties only: on every-message edges the graph is nearly complete
    for (p, q), n in pair.items():
        if n >= t.get("strong_tie_msgs_gte", 1):
            graph[p].add(q)
            graph[q].add(p)
    bc = betweenness(dict(graph)) if graph else {}
    skip_kinds = set(t.get("exclude_kinds") or [])
    keeper = of_model.structure_keeper(model) if t.get("exclude_structure_keeper") else None
    seen = observed_positions(model, rows, a, b, t.get("observed_days_gte") or (cat.get("defaults") or {}).get("observed_days_gte", 3))
    relays = owner_relays(model, rows, a, b)
    out = []
    for pos in graph:
        if pos not in seen or pos == keeper or model["positions"].get(pos, {}).get("kind") in skip_kinds:
            continue
        share = inbound.get(pos, 0) / total
        s1, s2 = share >= t.get("inbound_share_gte", 0.25), bc.get(pos, 0) >= t.get("betweenness_gte", 0.2)
        if s1 and s2:   # waits on it during its unavailability are not recorded yet: both structural signals required
            n_rel = sum(n for (p, q), n in relays.items() if pos in (p, q))
            out.append(issue(cat, "bottleneck-broker", [pos], {"inbound_share": round(share, 3), "betweenness": round(bc[pos], 3),
                                                               "inbound_messages": inbound.get(pos, 0), "messages": total},
                             f"{pos} receives {round(100 * share)}% of peer messages and sits on {round(100 * bc[pos])}% "
                             f"of shortest paths between positions", delta_m=n_rel * mpp))
    return out


def live_holder(p):
    """v0: an active position with a holder session, or held by a person (presence is not in the model yet)."""
    p = p or {}
    return p.get("status", "active") == "active" and bool(p.get("session") or p.get("runtime") == "human")


def queue_days(rows, a, b, org=None):
    """Days in [a, b) with any of.inbound_queue event for the organization: the relay was writing."""
    return {str(r["ts"])[:10] for r in rows if r["name"] == "of.inbound_queue" and of_model.in_window(r, a, b)
            and (not org or (r.get("payload") or {}).get("org") in (None, org))}


def rule_inbound_backlog(model, rows, a, b, cat, spec):
    t = spec.get("thresholds") or {}
    days = queue_days(rows, a, b, model.get("org"))
    need = t.get("queue_observed_days_gte", 3)
    if len(days) < need:
        return [], [f"queues observed on {len(days)} of {need} days"]
    old_s = t.get("old_after_min", 30) * 60
    per_day = t.get("backlog_min_per_day_gte", 60)
    skip = set(t.get("exclude_kinds") or [])
    mpp = (cat.get("defaults") or {}).get("minutes_per_prompt", 1.5)
    relays = owner_relays(model, rows, a, b)
    out = []
    for pos, q in of_model.inbound_queues(model, rows, a, b, old_after_s=old_s).items():
        p = model["positions"].get(pos)
        if p and p.get("kind") in skip:
            continue
        hit = sorted(d for d, v in q["by_day"].items() if v["backlog_min"] >= per_day)
        ev = {"backlog_days": hit, "backlog_h": round(q["backlog_min"] / 60, 1), "depth_avg": q["depth_avg"],
              "depth_max": q["depth_max"], "sender_wait_h": q["backlog_msg_h"], "since": q["observed_from"],
              "in_registry": p is not None}
        n_rel = sum(n for (x, y), n in relays.items() if pos in (x, y))
        if live_holder(p):
            if len(hit) >= t.get("overload_days_gte", 3) and q["depth_avg"] >= t.get("depth_avg_gte", 2):
                out.append(issue(cat, "inbound-backlog", [pos], ev,
                                 f"{pos} takes in less than it receives: messages waited over {old_s // 60} min on "
                                 f"{len(hit)} days, {q['depth_avg']} waiting on average", delta_m=n_rel * mpp, part="overload"))
        elif len(hit) >= t.get("unattended_days_gte", 2):
            why = "not in the registry" if p is None else (f"status {p.get('status')}" if p.get("status", "active") != "active"
                                                           else "no holder session")
            out.append(issue(cat, "inbound-backlog", [pos], dict(ev, holder=why),
                             f"messages wait for {pos}, which nobody holds ({why}): over {old_s // 60} min on "
                             f"{len(hit)} days", delta_m=n_rel * mpp, part="unattended"))
    return out


# ------------------------------------------------------------------ development loops (RFC 0005 §6; diagnose.md 2.16-2.22)
# Each reads the loop projection of diagnose.md §4.1, of_model.loops(model) (of_studio), per work; affected [<work>],
# evidence.work routes the issue to the doer or the work's owner (the run). Times in the projection are epoch seconds.

DAY = 86400


def _loops(model, declared=True):
    """The projected loops, the declared ones only by default (a loop with events but no `loop:` has no ladder)."""
    return {w: lp for w, lp in (of_model.loops(model) or {}).items() if lp and (lp.get("declared") or not declared)}


def _ended_in(r, a, b):
    t = r.get("ended_at")
    return t is not None and a <= t < b


def _t(spec, key, default):
    return (spec.get("thresholds") or {}).get(key, default)


def rule_loop_cheapest_rung(model, rows, a, b, cat, spec):
    out = []
    for w, lp in _loops(model).items():
        ladder = lp.get("ladder_spec") or []
        order = {x["rung"]: i for i, x in enumerate(ladder)}
        runs = sorted(lp.get("runs") or [], key=lambda r: r.get("ended_at") or 0)
        skipped, unjudged = [], 0
        for r in runs:
            if not _ended_in(r, a, b) or r.get("rung") not in order or order[r["rung"]] == 0:
                continue
            if not r.get("hypothesis"):
                unjudged += 1
                continue
            at = r.get("started_at") or r["ended_at"]
            done = {p["rung"] for p in runs if p.get("hypothesis") == r["hypothesis"] and p.get("accepted") is not False
                    and (p.get("ended_at") or 0) <= at and p is not r}
            cheaper = [x for x in ladder[:order[r["rung"]]] if (x.get("since") or 0) <= at and x["rung"] not in done]
            if cheaper:
                skipped.append({"run": r.get("id"), "rung": r["rung"], "hypothesis": r["hypothesis"],
                                "skipped": cheaper[0]["rung"], "latency": ladder[order[r["rung"]]].get("latency"),
                                "skipped_latency": cheaper[0].get("latency")})
        if len(skipped) >= _t(spec, "skipped_runs_gte", 2):
            s = skipped[-1]
            out.append(issue(cat, "loop-cheapest-rung", [w],
                             {"work": w, "skipped": skipped[-20:], "runs_skipping": len(skipped), "runs_without_hypothesis": unjudged},
                             f"{len(skipped)} runs of {w} skipped a cheaper rung: run it on {s['skipped']} first "
                             f"({s['skipped_latency'] or '?'} instead of {s['latency'] or '?'})"))
    return out


def rule_loop_checkpoint(model, rows, a, b, cat, spec):
    out, notes = [], []
    for w, lp in _loops(model).items():
        days = lp.get("checkpoint_days") or _t(spec, "checkpoint_days_default", 3)
        if not any(x.get("production") for x in lp.get("ladder_spec") or []):
            notes.append(f"{w}: the ladder marks no production rung")
            continue
        runs = lp.get("runs") or []
        if not any((r.get("ended_at") or 0) >= b - days * DAY and (r.get("ended_at") or 0) < b for r in runs):
            continue   # the loop did not run lately: a parked loop is not overdue
        last = ((lp.get("metrics") or {}).get("production") or {}).get("last_at")
        since = last or lp.get("start")
        if since is None or (b - since) / DAY <= days:
            continue
        n = round((b - since) / DAY, 1)
        out.append(issue(cat, "loop-checkpoint", [w],
                         {"work": w, "days_without_production": n, "checkpoint_days": days, "last_production_at": last,
                          "highest_rung": (lp.get("highest") or {}).get("rung")},
                         f"No production signal on {w} for {n} days (checkpoint {days}): what goes to shadow next?"))
    return out, [], notes


def rule_loop_plateau(model, rows, a, b, cat, spec):
    out = []
    k, share = _t(spec, "cycles_gte", 3), _t(spec, "gain_share_lt", 0.01)
    for w, lp in _loops(model).items():
        target = (lp.get("target") or {}).get("metric")
        sign = -1 if (lp.get("target") or {}).get("better") == "lower" else 1
        pts = sorted(((r["ended_at"], r["value"]) for r in lp.get("runs") or []
                      if r.get("value") is not None and r.get("ended_at") is not None and r["ended_at"] < b
                      and (r.get("metric") or target) == target), key=lambda p: p[0])
        if len(pts) < k + 1 or pts[-1][0] < a:
            continue
        steps = []
        for i in range(len(pts) - k, len(pts)):
            best = max(sign * v for _, v in pts[:i]) * sign
            steps.append(sign * (pts[i][1] - best) / abs(best or 1))
        if all(g < share for g in steps):
            out.append(issue(cat, "loop-plateau", [w],
                             {"work": w, "metric": target, "values": [v for _, v in pts[-(k + 1):]],
                              "gains": [round(g, 4) for g in steps], "from": pts[-k][0], "to": pts[-1][0]},
                             f"{w}: {k} cycles in a row moved {target} by under {share:.0%}: step back, and ask a second "
                             f"position for a reframe review"))
    return out


def rule_loop_wip(model, rows, a, b, cat, spec):
    out = []
    for w, lp in _loops(model).items():
        limit = lp.get("wip_arms") or _t(spec, "wip_arms_default", 2)
        series = sorted(((lp.get("metrics") or {}).get("wip") or []), key=lambda p: p[0])
        over, prev_t, prev_n = 0.0, None, 0   # the count is prev_n from prev_t until the next step
        for t, n in series + [(b, None)]:
            lo, hi = max(prev_t if prev_t is not None else a, a), min(t, b)
            if prev_n > limit and hi > lo:
                over += hi - lo
            prev_t, prev_n = t, n
        hours = round(over / 3600, 1)
        if hours >= _t(spec, "over_hours_gte", 12):
            testing = [h["id"] for h in lp.get("hypotheses") or [] if h.get("status") == "testing"]
            out.append(issue(cat, "loop-wip", [w], {"work": w, "hours_over": hours, "limit": limit, "testing": testing},
                             f"{w} had more than {limit} arms in flight for {hours} h: finish or park one"))
    return out


def _model_rank(model_id, ranks):
    s = str(model_id or "").lower()
    return next((i for i, f in enumerate(ranks) if f in s), None)


def rule_loop_babysit_model(model, rows, a, b, cat, spec):
    out = []
    ranks = [str(x) for x in _t(spec, "model_rank", ["haiku", "sonnet", "opus", "fable"])]
    for w, lp in _loops(model).items():
        cap = _model_rank(lp.get("babysit_model") or _t(spec, "babysit_model_default", "sonnet"), ranks)
        if cap is None:
            continue
        by, total = {}, 0
        for who, kinds in (((lp.get("metrics") or {}).get("babysit")) or {}).items():
            for cls, models in (kinds or {}).items():
                for mid, n in (models or {}).items():
                    r = _model_rank(mid, ranks)
                    if r is not None and r > cap:
                        by.setdefault(who, {}).setdefault(cls, {})[mid] = n
                        total += n
        if total >= _t(spec, "calls_gte", 100):
            out.append(issue(cat, "loop-babysit-model", [w],
                             {"work": w, "calls": total, "by_doer": by, "babysit_model": ranks[cap]},
                             f"{w}: {total} runner commands and monitors ran on a model above {ranks[cap]}: route them to {ranks[cap]}"))
    return out


_PART_STOP = {"the", "a", "an", "of", "after", "before", "to", "in", "on", "at", "by", "for", "from", "its", "our", "their",
              "and", "or", "with", "into", "onto", "over", "under", "per", "this", "that"}


def _part_tokens(text):
    """The content words of a text, for matching a segment's context part: lower case, '_', '-', '/' and punctuation
    as spaces, one-letter words and stop words dropped, a plural 's' cut from words over three letters."""
    words = re.findall(r"[a-z0-9]+", str(text or "").lower().replace("_", " "))
    return {w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
            for w in words if len(w) > 1 and w not in _PART_STOP}


def _names_part(text, part):
    """True when `text` names the context `part`: every content word of the part is among the text's (RFC 0005 §6)."""
    need = _part_tokens(part)
    return bool(need) and need <= _part_tokens(text)


def rule_loop_objective_drift(model, rows, a, b, cat, spec):
    """Part unpriced: runs that measure a metric neither the target's nor priced, or a change_objective without money.
    Part context (v1, 2026-11-12; hand-over 9e2140b6, docs/registry.md Works): a run's metric, a hypothesis's
    predicted effect or change, or a change_objective's metric names a part of the target's segment.context, which is
    reported beside the target and is never a target, a measure or a lever. One issue per work; context wins the part."""
    out = []
    for w, lp in _loops(model).items():
        tgt = lp.get("target") or {}
        target = tgt.get("metric")
        priced = {str(m.get("name")) for m in lp.get("measures") or [] if m.get("to_money")}
        runs = [r for r in lp.get("runs") or [] if _ended_in(r, a, b)]
        unpriced = [r for r in runs if (r.get("metric") or target) != target and r.get("metric") not in priced]
        bad = [d for d in lp.get("decisions_all") or [] if d.get("kind") == "change_objective"
               and a <= (d.get("ts") or 0) < b and not (d.get("metric") and d.get("to_money"))]
        drift = bad or (runs and len(unpriced) >= _t(spec, "unpriced_runs_gte", 3)
                        and len(unpriced) / len(runs) >= _t(spec, "unpriced_share_gte", 0.30))
        seg = tgt.get("segment") if isinstance(tgt.get("segment"), dict) else {}
        parts = [str(x) for x in seg.get("context") or [] if isinstance(x, str) and x.strip()]
        hits = []
        for part in parts:
            for r in runs:
                if r.get("metric") and _names_part(r["metric"], part):
                    hits.append({"part": part, "run": r.get("id"), "metric": r["metric"]})
            for h in lp.get("hypotheses") or []:
                live = a <= (h.get("ts") or 0) < b or h.get("status") in ("open", "testing")
                for f in ("predict", "change"):
                    if live and h.get(f) and _names_part(h[f], part):
                        hits.append({"part": part, "hypothesis": h.get("id"), f: str(h[f])[:160]})
                        break
            for d in lp.get("decisions_all") or []:
                if d.get("kind") == "change_objective" and a <= (d.get("ts") or 0) < b and d.get("metric") \
                        and _names_part(d["metric"], part):
                    hits.append({"part": part, "decision": "change_objective", "metric": d["metric"]})
        if not (drift or hits):
            continue
        metrics = collections.Counter(r.get("metric") for r in unpriced)
        ev = {"work": w, "target": target, "unpriced": dict(metrics), "runs": len(runs),
              "change_objective_without_money": len(bad)}
        if hits:
            top = collections.Counter(x["part"] for x in hits).most_common(1)[0][0]
            n_r = sum(1 for x in hits if x["part"] == top and "run" in x)
            n_h = sum(1 for x in hits if x["part"] == top and "hypothesis" in x)
            ev.update(segment={"start": seg.get("start"), "end": seg.get("end"), "context": parts}, context=hits[:20])
            named = " and ".join(x for x in (n_r and f"{n_r} run{'s' if n_r > 1 else ''}",
                                             n_h and f"{n_h} hypothes{'es' if n_h > 1 else 'is'}") if x)
            named = f"{named} name{'' if n_r + n_h > 1 else 's'} it" if named else "a change of objective names it"
            out.append(issue(cat, "loop-objective-drift", [w], ev,
                             f"{w}: {top[:1].upper() + top[1:]} is outside the segment: report it, and act inside ({named})",
                             part="context"))
            continue
        top = metrics.most_common(1)[0][0] if metrics else (bad[0].get("metric") or "the new objective")
        money = tgt.get("money") or "money"
        out.append(issue(cat, "loop-objective-drift", [w], ev,
                         f"{w}: {len(unpriced)} of {len(runs)} runs measure {top}, which has no price: "
                         f"price it in {money} before more work on it", part="unpriced"))
    return out


def rule_loop_eval_bottleneck(model, rows, a, b, cat, spec):
    """v0 reads of_studio's wait share per rung as the projection gives it (over the rung's runs so far)."""
    out = []
    for w, lp in _loops(model).items():
        if not any(_ended_in(r, a, b) for r in lp.get("runs") or []):
            continue
        rungs = ((lp.get("metrics") or {}).get("rungs")) or {}
        hot = {n: m for n, m in rungs.items() if (m or {}).get("wait_share") is not None
               and m["wait_share"] > _t(spec, "wait_share_gt", 0.40) and (m.get("runs") or 0) >= _t(spec, "rung_runs_gte", 3)}
        if hot:
            n, m = max(hot.items(), key=lambda kv: kv[1]["wait_share"])
            out.append(issue(cat, "loop-eval-bottleneck", [w], {"work": w, "rungs": hot},
                             f"{w}: runs on {n} waited {m['wait_share']:.0%} of their time: elevate the rung or use a cheaper one"))
    return out


# ------------------------------------------------------------------ structure coverage (diagnose.md 2.23-2.26)
# Structure as well as behaviour (of_architect, 2026-11-10): these rules read the registry's coverage as Studio shows it,
# model["coverage"] (studio.md 3.4) and model["cells"] (RFC 0007 §2.3), never a second computation, so Studio and the
# issue never disagree. They go to the organization's structure keeper, who brings a diff; never straight to the owner.

def _covered_by_parts(systems, sid, covered, seen=()):
    """A supersystem whose every part (recursively) is in `covered`: the parts carry it (as coverage's `kept`)."""
    parts = [x for x, v in systems.items() if v.get("parent") == sid and x not in seen]
    return bool(parts) and all(x in covered or _covered_by_parts(systems, x, covered, seen + (sid,)) for x in parts)


def rule_unkept_system(model, rows, a, b, cat, spec):
    """Part keeper: a live system Studio flags no_keeper, keeper_unknown or keeper_<status> (coverage.flags), whether or
    not work passes through it. Part unit: in an organization with units, a live system in no unit that is not kept
    outside and whose parts are not all in units. Studio's `orphan` flag alone marks only a system kept outside that
    no position touches, which is by design: it is evidence (`untouched`) on the keeper part, not an issue."""
    systems, positions = model["systems"], model["positions"]
    cov = (model.get("coverage") or {}).get("systems") or {}
    by_sys = (model.get("work_records") or {}).get("by_system") or {}
    units = model.get("org_units") or {}
    unit_of = {sid: uid for uid, u in units.items() for sid in u.get("systems") or []}
    out, unit_issues = [], []
    if units:
        for sid, s in sorted(systems.items()):
            if sid in unit_of or not is_live(s) or kept_outside(s) or _covered_by_parts(systems, sid, set(unit_of)):
                continue
            unit_issues.append(issue(cat, "unkept-system", [sid], {"system": sid, "name": s.get("name"), "keeper": s.get("keeper"),
                                                                  "units": sorted(units)},
                                     f"{sid} is live and in no unit: no unit owner or delegation covers it",
                                     part="unit", key=f"unit:{sid}"))
    for sid, row in sorted(cov.items()):
        flags = row.get("flags") or []
        why = [f for f in flags if f == "no_keeper" or f.startswith("keeper_")]
        if not why:
            continue
        s = systems.get(sid) or {}
        k = s.get("keeper")
        what = ("nobody keeps it" if why[0] == "no_keeper" else
                f"its keeper {k} is not a position of the organization" if why[0] == "keeper_unknown" else
                f"its keeper {k} is {why[0][7:]}")
        out.append(issue(cat, "unkept-system", [sid], {"system": sid, "name": s.get("name"), "flags": flags, "keeper": k,
                                                      "keeper_status": (positions.get(k) or {}).get("status") if k else None,
                                                      "untouched": "orphan" in flags, "writers": row.get("writers") or {},
                                                      "merged_prs": len(by_sys.get(sid) or []), "unit": unit_of.get(sid)},
                         f"{sid} is live and {what}" + (", and no position works on it" if "orphan" in flags else ""),
                         part="keeper"))
    return unit_issues + out   # keeper part last: an orphan-work cluster on the same system merges into it


EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
_RV = re.compile(r"rt-v(\d+)$")


def rule_off_map_work(model, rows, a, b, cat, spec):
    """A position whose file edits mostly land in systems other positions keep (mandate-router verdicts,
    of.mandate_check): at least `edits_gte` edits in_mandate or owned_by another keeper, with an in_mandate share
    under `in_mandate_share_lt`. Reads are left out (reading others' code is normal), and so are Bash calls (a shell
    at a repository root is unscoped), paths outside the organization, and paths no system claims: those are the map's
    gap, orphan-work's cluster part (shown here as evidence only). One issue per position; part no_own_paths when none
    of its edits falls in its own system (the registry fix: name its subsystem with paths), else mostly_elsewhere.
    An unowned edit is keyed by of_model.area_of, as the model's orphan_work keys it: an edit in a linked worktree
    counts under its repository (of_studio, 2026-11-12: '~/<org>/.worktrees' read as a worktree bug)."""
    t = spec.get("thresholds") or {}
    positions = model["positions"]
    repos = model.get("map_repos") or ()
    sessions = of_model.session_index(positions)
    tools, min_rv = set(t.get("edit_tools") or EDIT_TOOLS), t.get("min_router", 4)
    per = collections.defaultdict(lambda: {"in_mandate": 0, "owned_by": collections.Counter(), "unowned": collections.Counter(),
                                           "systems": collections.Counter()})
    for r in rows:
        if r["name"] != "of.mandate_check" or not of_model.in_window(r, a, b):
            continue
        pl = r.get("payload") or {}
        m = _RV.match(str(pl.get("router_version") or "rt-v1"))
        if pl.get("tool_name") not in tools or (int(m.group(1)) if m else 1) < min_rv:
            continue
        pos = of_model.position_of(r, positions, sessions)
        if not pos:
            continue
        v, x = str(pl.get("verdict") or ""), per[pos]
        if v == "in_mandate":
            x["in_mandate"] += 1
        elif v.startswith("owned_by:"):
            x["owned_by"][v[9:]] += 1
        elif v == "unowned":
            x["unowned"][pl.get("system") or of_model.area_of(pl.get("path"), repos=repos)] += 1
        else:
            continue
        if pl.get("system"):
            x["systems"][pl["system"]] += 1
    out = []
    for pos, x in sorted(per.items()):
        n_own, n_other = x["in_mandate"], sum(x["owned_by"].values())
        n = n_own + n_other
        if n < t.get("edits_gte", 10) or n_own / n >= t.get("in_mandate_share_lt", 0.5):
            continue
        home = (positions.get(pos) or {}).get("system")
        part = "no_own_paths" if not n_own else "mostly_elsewhere"
        where = ", ".join(f"{k} {c}" for k, c in x["systems"].most_common(3))
        out.append(issue(cat, "off-map-work", [pos], {"position": pos, "system": home, "edits": n, "in_mandate": n_own,
                                                     "in_mandate_share": round(n_own / n, 2),
                                                     "owned_by": dict(x["owned_by"].most_common(5)),
                                                     "unowned": dict(x["unowned"].most_common(5)),
                                                     "systems": dict(x["systems"].most_common(5))},
                         f"{pos} (system {home}): {n - n_own} of {n} file edits landed in systems other positions keep ({where})",
                         part=part))
    return out


def rule_cell_gap(model, rows, a, b, cat, spec):
    """A needed cell of a live system without a decider (model cells: gap nobody or no_decider). When no active
    position owns any cell of the system yet, one issue for the system (part system), not one per cell: its positions
    are not mapped onto cells yet. Blocked while no system declares `needs`."""
    c = model.get("cells") or {}
    if not c.get("systems_with_needs"):
        return [], ["registry needs (no system declares the kinds of work it needs; RFC 0007)"]
    mapped = {r["system"] for r in c.get("cells") or [] if r.get("doers")}
    out, whole = [], collections.defaultdict(list)
    for r in c.get("gaps") or []:
        sid, m = r["system"], r["method"]
        if sid not in mapped:
            whole[sid].append(m)
            continue
        out.append(issue(cat, "cell-gap", [sid, f"method:{m}"], {"system": sid, "method": m, "gap": r["gap"],
                                                                 "doers": r.get("doers") or [], "inactive": r.get("inactive") or []},
                         f"{sid} needs {m}, and " + ("no position owns it" if r["gap"] == "nobody" else
                                                     f"no position decides it ({', '.join(r['doers'])} work it)"),
                         part=r["gap"]))
    for sid, ms in sorted(whole.items()):
        out.append(issue(cat, "cell-gap", [sid], {"system": sid, "needed": ms},
                         f"{sid} needs {len(ms)} kinds of work ({', '.join(ms)}), and no position owns any of its cells",
                         part="system"))
    return out


def rule_cell_overlap(model, rows, a, b, cat, spec):
    """A cell with of_model.OVERLAP_DOERS or more active doers (model cells, as Studio shows it). Blocked while no
    position declares `owns`."""
    c = model.get("cells") or {}
    if not c.get("positions_with_owns"):
        return [], ["registry owns (no position declares the cells it owns; RFC 0007)"]
    out = []
    for r in c.get("overlaps") or []:
        sid, m, doers, dec = r["system"], r["method"], r.get("doers") or [], r.get("deciders") or []
        out.append(issue(cat, "cell-overlap", [sid, f"method:{m}"], {"system": sid, "method": m, "doers": doers, "deciders": dec},
                         f"{sid} x {m}: {len(doers)} positions work it ({', '.join(doers)}); "
                         + (f"{', '.join(dec)} decides" if dec else "none decides")))
    return out


# ------------------------------------------------------------------ provenance (diagnose.md 2.27; handover.md §1.1)

# Work stated as owed to an authority: a law, a regulator, a contract, the owner. A plain "must" or "required" is not
# one: in a spec it describes behaviour ("a required TOML key"), which was every match in the first calibration week.
OWED = re.compile(r"\b(?:compliance|regulatory|legal|statutory|contractual|licen[cs]e)\s+(?:requirement|obligation|duty)s?\b"
                  r"|\b(?:required|mandated|mandatory|obliged|obligatory)\s+(?:by|under|per)\s"
                  r"|\b(?:law|laws|regulation|regulator|decree|statute|counsel|founder|owner)\s+(?:requires|mandates|demands|obliges)\b"
                  r"|\brequirement\s+(?-i:[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d+)\b", re.I)
# The requester's own word that its reason is unconfirmed.
HEDGE = re.compile(r"\[\s*confirm|\bconfirm with\b|\bto be confirmed\b|\bTBC\b|\bunconfirmed\b|\bnot (?:yet )?confirmed\b"
                   r"|\binference\b|\binferred\b|\bunverified\b|\bnot (?:yet )?verified\b"
                   r"|\bpending (?:counsel|legal|confirmation)\b|\bassumption\b", re.I)
ASK_REF = re.compile(r"\b(?:ask|asks|card|founder|owner|yes|answer(?:ed|s)?)\b[^.;\n]{0,40}?\b[0-9a-f]{8}\b", re.I)
QUOTE = re.compile(r"\"[^\"]{12,}\"|“[^”]{12,}”|«[^»]{12,}»")
REF_TOKEN = re.compile(r"(?<![\w-])[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d+(?![\w-])")   # a register row: REG-02, OB-009, F-003
CLOSED_UNDONE = ("declined", "returned", "withdrawn", "expired")


def _around(text, m, width=70):
    lo, hi = max(0, m.start() - width), min(len(text), m.end() + width)
    return ("…" if lo else "") + text[lo:hi].strip() + ("…" if hi < len(text) else "")


def _short(name):
    return str(name or "").rsplit(":", 1)[-1]


def handover_basis(p):
    """(kind, text) of a proposed hand-over's basis: its `basis` field (handover.md §1.1: ask, source, evidence,
    inference), else what its context says. kind: ask | source | evidence | inference | context | None."""
    b = str(p.get("basis") or "").strip()
    if b:
        kind = b.split(":", 1)[0].strip().lower()
        return ("inference" if kind.startswith("inference") else kind if kind in ("ask", "source", "evidence") else "context"), b
    items = [str(x) for x in p.get("context") or []]
    ctx = "; ".join(items)
    for x in items:
        if HEDGE.search(x):
            return "inference", x[:240]
    if ASK_REF.search(ctx):
        return "ask", ctx[:160]
    if QUOTE.search(ctx):
        return "source", ctx[:160]
    return ("context", ctx[:160]) if ctx.strip() else (None, "")


def hedge_phrase(text):
    """The words that say a basis is unconfirmed: a bracket such as "[confirm with counsel, Q-REG-05]" whole."""
    m = HEDGE.search(text or "")
    if not m:
        return ""
    if text[m.start()] == "[" and "]" in text[m.start():]:
        return text[m.start():text.index("]", m.start()) + 1]
    return _around(text, m, 30)


def _names_record(text, key, sha):
    """Does a delivered result name this work record: its PR URL, `<repo>#<n>` or `<repo> PR #<n>`, or its commit?"""
    if key:
        repo, n = key
        return bool(re.search(rf"/pull/{n}\b|\b{re.escape(repo)}\s*(?:PR\s*)?#\s*{n}\b", text, re.I))
    return bool(sha) and len(str(sha)) >= 7 and str(sha)[:7] in text


def _record_basis(text, handover_ids):
    """The basis a work record's own text gives: inference (it says so), ask, a hand-over it cites, a quoted source."""
    if HEDGE.search(text):
        return "inference"
    if ASK_REF.search(text):
        return "ask"
    for h in handover_ids:
        if h[-8:] in text:
            return f"handover:{h[-8:]}"
    return "source" if QUOTE.search(text) else None


def rule_unsourced_requirement(model, rows, a, b, cat, spec):
    """A claim that drives work lost its basis on the way, or never had one (handover.md §1.1).
    Part strengthened: a hand-over whose own basis is an inference (its `basis` field, or its context's word:
    "[confirm with counsel]", "unconfirmed") states the work without that word in its title or done_when, so the
    receiver gets a must; the merged PRs and commits that carry it on are its evidence (linked by the hand-over id in
    their text, by its delivered result, or by a register token such as REG-02 shared by the receiver's work). A work
    record that cites an inference-based hand-over which kept its hedge, and states the work as owed, is strengthened
    on its own. Part unsourced: a hand-over or a work record that states the work as owed to an authority (OWED) with no
    basis at all. Declined, returned, withdrawn and expired hand-overs are left out: the receiver did as §1.1 says."""
    hos, state, results = {}, {}, collections.defaultdict(list)
    for r in rows:
        n, pl = r["name"], r.get("payload") or {}
        if not n.startswith("of.handover_"):
            continue
        hid = pl.get("id") or (r.get("event_id") if n == "of.handover_proposed" else None)
        if not hid:
            continue
        kind = n[len("of.handover_"):]
        if kind == "proposed":
            hos.setdefault(hid, dict(pl, id=hid, ts=r["ts"]))
        elif kind != "offered":
            state[hid] = kind
        if kind == "delivered":
            results[hid].append(" ".join(str(pl[k]) for k in ("result", "note") if pl.get(k)))
    hos = {h: p for h, p in hos.items() if state.get(h) not in CLOSED_UNDONE}
    basis = {h: handover_basis(p) for h, p in hos.items()}
    ho_tokens = {h: set(REF_TOKEN.findall(" ".join([str(p.get("title") or ""), str(p.get("done_when") or "")]
                                                   + [str(x) for x in p.get("context") or []]))) for h, p in hos.items()}

    def flagged(p):
        """The hand-over's own step grew the claim. With a `basis: inference` field the offer shows it unconfirmed, so
        only a title or done_when that calls it owed (OWED) grew it; with the hedge buried in the context (before the
        field, 2026-11-11), a title and done_when that say nothing of it did."""
        wd = f"{p.get('title') or ''} {p.get('done_when') or ''}"
        if basis[p["id"]][0] != "inference" or HEDGE.search(wd) or str(p.get("title") or "").rstrip().endswith("?"):
            return False
        return bool(OWED.search(wd)) if p.get("basis") else True

    carried, own = collections.defaultdict(list), []
    for r in rows:
        if r["name"] != "of.work_record" or not of_model.in_window(r, a, b):
            continue
        pl = r.get("payload") or {}
        if of_model.BOT_AUTHOR.search(str(pl.get("author") or "")):
            continue   # a merge-queue copy of a PR counted on its own
        title = str(pl.get("title") or "")
        text = " ".join(str(pl.get(f) or "") for f in ("title", "body", "message"))   # body and message when the record has them
        k = of_model.pr_key(pl)
        ref = f"{k[0]}#{k[1]}" if k else f"{str(pl.get('repo') or '').rsplit('/', 1)[-1]}@{str(pl.get('sha') or '')[:8]}"
        tokens = set(REF_TOKEN.findall(text))
        merged = epoch(pl.get("merged_at") or r["ts"]) or 0
        owed = OWED.search(text)
        rec = {"record": ref, "url": pl.get("url"), "merged_at": pl.get("merged_at") or r["ts"], "position": pl.get("position"),
               "title": title[:200]}
        if "reviews" in pl:
            rec["reviews"] = pl["reviews"]
        if owed:
            rec["states"] = _around(text, owed, 40)
        links = []
        for h, p in hos.items():
            shared = tokens & ho_tokens[h] if _short(p.get("to")) == pl.get("position") and merged >= (epoch(p["ts"]) or 0) else set()
            if h[-8:] in text:
                links.append((h, "its text cites the hand-over"))
            elif any(_names_record(x, k, pl.get("sha")) for x in results.get(h, ())):
                links.append((h, "the hand-over's delivered result names it"))
            elif shared:
                links.append((h, f"the receiver's work cites {', '.join(sorted(shared))}"))
        hedged = [(h, how) for h, how in links if basis[h][0] == "inference"]
        for h, how in hedged:
            if flagged(hos[h]):
                carried[h].append(dict(rec, link=how))
        if any(flagged(hos[h]) for h, _ in hedged):
            continue
        if hedged and owed and not HEDGE.search(text):
            h, how = hedged[0]
            own.append(("strengthened", rec, {"handover": h[-8:], "handover_basis": basis[h][1], "link": how}))
        elif owed and not links and not _record_basis(text, hos):
            own.append(("unsourced", rec, {}))

    out = []
    for h, p in sorted(hos.items(), key=lambda kv: kv[1]["ts"]):
        frm, to, title = _short(p.get("from")), _short(p.get("to")), str(p.get("title") or "")
        recs = sorted(carried.get(h, []), key=lambda x: x["merged_at"])
        if flagged(p) and (of_model.in_window(p, a, b) or recs):
            into = "; carried into " + ", ".join(x["record"] for x in recs) + " without it" if recs else ""
            ev = {"handover": h[-8:], "from": frm, "to": to, "title": title[:200], "state": state.get(h, "proposed"),
                  "basis": "inference", "basis_text": basis[h][1], "carried_into": recs}
            if results.get(h):
                ev["delivered"] = results[h][-1][:240]
            out.append(issue(cat, "unsourced-requirement", [frm, to], ev,
                             f"hand-over {h[-8:]} ({frm} -> {to}) asks '{title[:90]}' as work to do, while its own basis says "
                             f"{hedge_phrase(basis[h][1]) or 'inference'}{into}", part="strengthened", key=f"handover:{h}"))
        elif (of_model.in_window(p, a, b) and basis[h][0] is None
              and OWED.search(f"{title} {p.get('done_when') or ''}")):
            m = OWED.search(f"{title} {p.get('done_when') or ''}")
            out.append(issue(cat, "unsourced-requirement", [frm, to],
                             {"handover": h[-8:], "from": frm, "to": to, "title": title[:200], "states": m.group(0), "basis": None},
                             f"hand-over {h[-8:]} ({frm} -> {to}) states the work as owed ('{m.group(0)}') with no basis",
                             part="unsourced", key=f"handover:{h}"))
    for part, rec, extra in own:
        who = rec["position"] or rec["record"]
        why = (f"cites hand-over {extra['handover']}, whose basis says {hedge_phrase(extra['handover_basis']) or 'inference'}, and states it as owed"
               if part == "strengthened" else "states the work as owed with no basis")
        out.append(issue(cat, "unsourced-requirement", [who], dict(rec, **extra),
                         f"{rec['record']} by {who} {why}: '{rec.get('states', '')}'", part=part, key=f"wr:{rec['record']}"))
    return out


RULES = {"orphan-work": rule_orphan_work, "coordination-gap": rule_coordination_gap,
         "unclear-mandate": rule_unclear_mandate, "churn": rule_churn, "chatty": rule_chatty,
         "bottleneck-broker": rule_bottleneck_broker,
         "owner-router": rule_owner_router, "owner-relay": rule_owner_relay,
         "over-coupled": rule_over_coupled, "fragmented-ownership": rule_fragmented_ownership,
         "function-gap": rule_function_gap, "lingering": rule_lingering, "inbound-backlog": rule_inbound_backlog,
         "loop-cheapest-rung": rule_loop_cheapest_rung, "loop-checkpoint": rule_loop_checkpoint,
         "loop-plateau": rule_loop_plateau, "loop-wip": rule_loop_wip, "loop-babysit-model": rule_loop_babysit_model,
         "loop-objective-drift": rule_loop_objective_drift, "loop-eval-bottleneck": rule_loop_eval_bottleneck,
         "unkept-system": rule_unkept_system, "off-map-work": rule_off_map_work,
         "cell-gap": rule_cell_gap, "cell-overlap": rule_cell_overlap,
         "unsourced-requirement": rule_unsourced_requirement}
# a rule in parts (needs keyed by part) takes the tuple of runnable parts as its last argument
