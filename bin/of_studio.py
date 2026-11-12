"""Org Studio v0: the owner's web page (docs/studio.md §5).

Server-side HTML, no scripts and no inline styles (of-server sends CSP default-src 'self').
of-server mounts everything under /studio/ and calls

  handle(method, path, query, viewer) -> (status, content_type, body: bytes)

viewer = {"id": ..., "tenant": bool, "scope": "org" | "unit"}. Access (the one-time link and the cookie) is of-server's.
`of studio --file PATH` writes the same page as one static file, with the stylesheet embedded.

Views: inbox (open asks, then positions waiting on the owner), owner (L, λ, W and the rest), coverage (system × position),
waits (position × hour heatmap by cause). Every number comes from bin/of_model.py and bin/of_measures.py.
"""

import collections
import html
import os
import re
import time
import urllib.parse

import of_asks
import of_diagnose
import of_measures
import of_model

DAY = 86400
VIEWS = (("inbox", "Inbox"), ("goals", "Goals"), ("works", "Works"), ("owner", "Owner"), ("review", "Review"), ("coverage", "Coverage"), ("structure", "Structure"),
         ("systems", "Systems"), ("cells", "Cells"), ("waits", "Waits"))
CAUSE_CLASS = {"owner": "c-owner", "peer": "c-peer", "capacity": "c-capacity", "suspended": "c-suspended",
               "external": "c-external", "stalled": "c-stalled"}
CAUSE_LABEL = {"owner": "on the owner", "peer": "on a peer", "capacity": "on capacity (runners, CI, limits)",
               "suspended": "suspended (budget governor)", "external": "outside", "stalled": "stalled, no cause"}


# ------------------------------------------------------------------ data

def orgs(orgs_file=None):
    import yaml
    path = orgs_file or os.environ.get("OF_ORGS") or os.path.expanduser("~/.config/of/orgs.yaml")
    with open(path) as fh:
        doc = yaml.safe_load(fh) or {}
    base = os.path.dirname(os.path.abspath(path))
    return [{"name": o.get("name"), "repo": os.path.join(base, os.path.expanduser(str(o["path"]))),
             "paths": [os.path.expanduser(p) for p in o.get("paths") or []]} for o in doc.get("orgs") or []]


def _home_tilde(p):
    h = os.path.expanduser("~")
    return "~" + p[len(h):] if p.startswith(h) else p


def org_rows(rows, org, others):
    """Events of one organization: tagged with it, or (untagged) whose cwd lies under its paths and under no other's."""
    mine = [_home_tilde(p) for p in org["paths"]]
    theirs = [_home_tilde(p) for o in others for p in o["paths"]]

    def under(cwd, roots):
        return any(cwd == r or cwd.startswith(r.rstrip("/") + "/") for r in roots)
    out = []
    for r in rows:
        if r.get("org"):
            if r["org"] in (org["name"],):
                out.append(r)
            continue
        if (r.get("payload") or {}).get("org") == org["name"]:   # tool events that name their organization in the payload
            out.append(r)
            continue
        cwd = r.get("cwd") or ""
        if under(cwd, mine) and not any(under(cwd, [t]) and len(t) > max((len(m) for m in mine if under(cwd, [m])), default=0)
                                        for t in theirs):
            out.append(r)
    # closures `of` writes with no organization and no cwd (reconcile answers, withdrawals) belong where the ask they
    # name is: without them the ask stays open here while the live asks view has it closed (of_field-analyst, 2026-11-06)
    ids = {r.get("event_id") for r in out}
    for r in rows:
        if r.get("org") or r.get("cwd") or r.get("name") not in CLOSURES:
            continue
        pl = r.get("payload") or {}
        named = set(pl.get("ask_ids") or []) | ({pl["ask_id"]} if pl.get("ask_id") else set())
        if named & ids:
            out.append(r)
    return out


CLOSURES = ("of.agent_question_answered", "of.ask_withdrawn")


# The event kinds the views read (asks and what closes them, prompts, messages, work records, states)
STUDIO_KINDS = ("of.agent_question", "of.permission_request", "of.permission_denied", "of.agent_question_answered",
                "of.human_prompt", "of.turn_end", "of.turn_error", "of.session_end", "of.ask_withdrawn",
                "of.agent_message_in", "of.agent_message_out", "of.work_record", "of.artifact_read",
                "of.state_declared", "of.state_changed",
                "of.pulse", "of.stall_detected", "of.nudge_sent", "of.attention", "of.mandate_check", "of.studio_viewed", "of.diff_applied", "of.inbound_queue", "of.pr_state",
                # what else the ask lifecycle (of_asks.project) reads: without these the page's asks differ from the CLI's
                "of.ask_escalated", "of.permission_resolved", "of.question_labelled", "of.injected_prompt", "of.escalation_due")


TOKEN_METHOD = "tok-v0"   # assistant records, deduplicated by (session, usage, minute); fresh = input + cache writes + output


USAGE_TTL_S = 900        # a unit's usage is refreshed at most this often (incremental: only new bytes)
USAGE_BUDGET_S = 25      # one refresh reads for at most this long; the pulse kills its copy at 60 s
USAGE_INDEX_V = 2        # 2: per-day and per-model sums per session, for `of model usage` (a user organization's structure keeper, 2026-11-11); a new version re-reads the month
CORRECTION_WEEKS = 6   # the Owner page's repeat-correction table (RFC 0006 §2.4)


def _usage_cache_path():
    import of_events
    return os.path.join(of_events.STATE_DIR, "studio", "usage.json")


def _usage_index_path():
    import of_events
    return os.path.join(of_events.STATE_DIR, "studio", "usage-index.json")


def _usage_advance(month, start, wanted, deadline):
    """Read the delegated units' transcripts forward from where the last refresh stopped, and add this month's
    responses to per-session sums: `usage-index.json` keeps each file's offset, this month's message ids (one API
    response counts once, across files: a move copies a transcript to another account) and the sums (the month's, per
    UTC day as [fresh, cost_usd], and fresh tokens per model), with `read_at` and `complete` of the last read. Lines are
    streamed, never a whole file in memory (a full read of a user organization's took 50 s and 5.2 GB, 2026-11-11). The same
    count as of_capacity.usage (list prices, subagents to their session). Returns True when every file is read,
    False when `deadline` came first: the progress is saved and the next refresh goes on from it."""
    import json
    import of_capacity
    path = _usage_index_path()
    try:
        with open(path) as fh:
            idx = json.load(fh)
    except (OSError, ValueError):
        idx = {}
    files = idx.get("files") or {}
    if idx.get("v") != USAGE_INDEX_V:   # a new shape: the month is read again from the start
        idx, files = {"v": USAGE_INDEX_V, "month": month, "files": {}, "ids": [], "sessions": {}}, {}
    elif idx.get("month") != month:   # a new month: the offsets go on, the sums and ids start again
        idx = {"v": USAGE_INDEX_V, "month": month, "files": files, "ids": [], "sessions": {}}
    ids, sums = set(idx.get("ids") or []), idx.get("sessions") or {}
    done = True
    for d in of_capacity.config_dirs():
        for f in of_capacity._transcripts(d):
            sid = of_capacity._session_of(f)
            if sid not in wanted:
                continue
            try:
                st = os.stat(f)
            except OSError:
                continue
            if st.st_mtime < start:
                continue
            rec = files.get(f) or {}
            off = rec.get("offset", 0) if rec.get("ino") == st.st_ino and st.st_size >= rec.get("offset", 0) else 0
            if off >= st.st_size:
                continue
            n = 0
            try:
                with open(f, "rb") as fh:
                    fh.seek(off)
                    for ln in fh:
                        if not ln.endswith(b"\n"):
                            break   # a line still being written: the next refresh reads it whole
                        off += len(ln)
                        n += 1
                        if n % 5000 == 0 and time.time() > deadline:
                            done = False
                            break
                        if b'"usage"' not in ln:
                            continue
                        try:
                            j = json.loads(ln)
                        except ValueError:
                            continue
                        m = j.get("message")
                        if not isinstance(m, dict) or not m.get("usage") or not m.get("id"):
                            continue
                        t = of_capacity._ts(j.get("timestamp") or "") or 0
                        if m["id"] in ids or t < start:
                            continue
                        ids.add(m["id"])
                        u = m["usage"]
                        cc = u.get("cache_creation") or {}
                        w1 = cc.get("ephemeral_1h_input_tokens", 0) if cc else u.get("cache_creation_input_tokens", 0)
                        w5 = cc.get("ephemeral_5m_input_tokens", 0)
                        fresh, cost = u.get("input_tokens", 0) + u.get("output_tokens", 0) + w1 + w5, of_capacity.usage_cost(u)
                        o = sums.setdefault(sid, {"fresh": 0, "cost_usd": 0.0})
                        o["fresh"] += fresh
                        o["cost_usd"] += cost
                        day = o.setdefault("days", {}).setdefault(time.strftime("%Y-%m-%d", time.gmtime(t)), [0, 0.0])
                        day[0] += fresh
                        day[1] += cost
                        mod = o.setdefault("models", {})
                        mod[m.get("model") or "?"] = mod.get(m.get("model") or "?", 0) + fresh
            except OSError:
                continue
            files[f] = {"offset": off, "ino": st.st_ino}
            if not done or time.time() > deadline:
                done = False
                break
        if not done:
            break
    idx.update(files=files, ids=sorted(ids), sessions=sums, read_at=time.time(), complete=done)
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(idx, fh)
        os.replace(tmp, path)
    except OSError:
        return False
    return done


def unit_usage(model, rows, now, refresh=False, budget_s=USAGE_BUDGET_S):
    """{unit: {month, computed_at, fresh, cost_usd, sessions}}: the canonical count (one response per message.id, cost
    at list, cache reads included; of_capacity.usage's) for each delegated unit's sessions this month. Only a refresh
    (`of model escalations`, run by of pulse) reads transcripts: incrementally, for at most `budget_s`; a unit's numbers
    change only when a refresh has read every file. Pages read the cache."""
    import json
    path = _usage_cache_path()
    try:
        with open(path) as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        cache = {}
    month = time.strftime("%Y-%m", time.gmtime(now))
    units = {uid: u for uid, u in (model.get("org_units") or {}).items() if u.get("delegation")}
    stale = [uid for uid in units if (cache.get(uid) or {}).get("month") != month
             or now - (cache.get(uid) or {}).get("computed_at", 0) > USAGE_TTL_S]
    if refresh and stale:
        try:
            import of_capacity  # noqa: F401
        except ImportError:
            return {k: v for k, v in cache.items() if k in units and v.get("month") == month}
        sp = of_model.session_positions(model, rows)
        start = of_model.epoch(month + "-01")
        sess = {uid: {x for x, p in sp.items() if p in of_diagnose.unit_members(model, uid)[1]} for uid in units}
        if _usage_advance(month, start, set().union(*sess.values()) if sess else set(), time.time() + budget_s):
            try:
                with open(_usage_index_path()) as fh:
                    sums = json.load(fh).get("sessions") or {}
            except (OSError, ValueError):
                sums = {}
            for uid in stale:
                per = [sums[x] for x in sess[uid] if x in sums]
                cache[uid] = {"month": month, "computed_at": now, "sessions": len(sess[uid]),
                              "fresh": int(sum(v["fresh"] for v in per)),
                              "cost_usd": round(sum(v["cost_usd"] for v in per), 2)}
            try:
                os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
                with open(path, "w") as fh:
                    json.dump(cache, fh)
            except OSError:
                pass
    return {k: v for k, v in cache.items() if k in units and v.get("month") == month}


def usage_by_position(model, rows, now, units=None, refresh=False, budget_s=USAGE_BUDGET_S):
    """`of model usage` (a user organization's structure keeper, 2026-11-11): each delegated unit's use this month per position, per UTC day and per
    model, from the same index and count as the unit's budget row (unit_usage), so a unit owner can plan models, pauses
    and the limit it needs. {month, read_at, complete, units: {unit: {owner, sessions, fresh, cost_usd, budget_tokens,
    budget_usd, positions: {position: {sessions, fresh, cost_usd, days: {day: {fresh, cost_usd}}, models: {model:
    fresh}, also_in: [other delegated units of the position, which count it too]}}}}}. `complete` is False while the index has not read every file: the numbers are then partial and the
    caller prints a gap, never them. `refresh` advances the index first, for at most `budget_s` (the pulse does it)."""
    import json
    month = time.strftime("%Y-%m", time.gmtime(now))
    delegated = {uid: u for uid, u in (model.get("org_units") or {}).items() if u.get("delegation")}
    sp = of_model.session_positions(model, rows)
    members = {uid: of_diagnose.unit_members(model, uid)[1] for uid in delegated}
    if refresh:
        try:
            import of_capacity  # noqa: F401
            _usage_advance(month, of_model.epoch(month + "-01"),
                           {x for x, p in sp.items() if any(p in ps for ps in members.values())}, time.time() + budget_s)
        except ImportError:
            pass
    try:
        with open(_usage_index_path()) as fh:
            idx = json.load(fh)
    except (OSError, ValueError):
        idx = {}
    ok = idx.get("v") == USAGE_INDEX_V and idx.get("month") == month
    sums = (idx.get("sessions") or {}) if ok else {}
    out = {"month": month, "read_at": idx.get("read_at") if ok else None, "complete": bool(ok and idx.get("complete")),
           "units": {}}
    for uid, u in delegated.items():
        if units and uid not in units:
            continue
        rec = u["delegation"]
        env = (rec.get("envelope") or {}).get("budget") or {}
        row = {"owner": rec.get("to_owner"), "sessions": 0, "fresh": 0, "cost_usd": 0.0,
               "budget_tokens": env.get("tokens_month"), "budget_usd": env.get("usd_month"), "positions": {}}
        for sid, pos in sorted(sp.items()):
            if pos not in members[uid]:
                continue
            row["sessions"] += 1
            s = sums.get(sid)
            p = row["positions"].setdefault(pos, {"sessions": 0, "fresh": 0, "cost_usd": 0.0, "days": {}, "models": {},
                                                  "also_in": sorted(x for x, ps in members.items() if x != uid and pos in ps)})
            p["sessions"] += 1
            if not s:
                continue
            p["fresh"] += s["fresh"]
            p["cost_usd"] += s["cost_usd"]
            for day, (f, c) in (s.get("days") or {}).items():
                d = p["days"].setdefault(day, {"fresh": 0, "cost_usd": 0.0})
                d["fresh"] += f
                d["cost_usd"] += c
            for mod, f in (s.get("models") or {}).items():
                p["models"][mod] = p["models"].get(mod, 0) + f
        for p in row["positions"].values():
            p["cost_usd"] = round(p["cost_usd"], 2)
            for d in p["days"].values():
                d["cost_usd"] = round(d["cost_usd"], 2)
            p["days"] = dict(sorted(p["days"].items()))
        row["fresh"] = sum(p["fresh"] for p in row["positions"].values())
        row["cost_usd"] = round(sum(p["cost_usd"] for p in row["positions"].values()), 2)
        out["units"][uid] = row
    return out


def budget_used(model, rows, now, conn=None, refresh_usage=False, refresh_budget_s=USAGE_BUDGET_S):
    """{unit: {used, budget, share, method, usd_used, usd_budget, usd_share}} for delegated units whose envelope sets
    budget.tokens_month or budget.usd_month, this calendar month (UTC). Tokens: the canonical count from the usage cache
    when present, else tok-v0 from the transcript index; dollars: the usage cache only."""
    out = {}
    usage = unit_usage(model, rows, now, refresh=refresh_usage, budget_s=refresh_budget_s)
    units = {uid: u for uid, u in (model.get("org_units") or {}).items() if u.get("delegation")}
    if not units:
        return out
    try:
        import of_store
        conn = conn or of_store.connect()
    except Exception:  # noqa: BLE001 — no store, no budget reading
        return out
    sp = of_model.session_positions(model, rows)
    month = time.strftime("%Y-%m-01T00:00:00Z", time.gmtime(now))
    for uid, u in units.items():
        env_budget = ((u["delegation"].get("envelope") or {}).get("budget") or {})
        usd_budget = env_budget.get("usd_month")
        cached = usage.get(uid) or {}
        if "cost_usd" in cached:   # shown on the unit row with or without a dollar budget
            out.setdefault(uid, {})["usd_used"] = cached["cost_usd"]
        if isinstance(usd_budget, (int, float)) and usd_budget > 0 and "cost_usd" in cached:
            out.setdefault(uid, {}).update(usd_used=cached["cost_usd"], usd_budget=usd_budget,
                                           usd_share=cached["cost_usd"] / usd_budget)
        budget = env_budget.get("tokens_month")
        if not isinstance(budget, (int, float)) or budget <= 0:
            continue
        if "fresh" in cached:
            out.setdefault(uid, {}).update(used=cached["fresh"], budget=int(budget), share=cached["fresh"] / budget,
                                           method="of_capacity.usage")
            continue
        _, positions = of_diagnose.unit_members(model, uid)
        sess = [x for x, p in sp.items() if p in positions]
        if not sess:
            out.setdefault(uid, {}).update(used=0, budget=int(budget), share=0.0, method=TOKEN_METHOD)
            continue
        ph = ",".join("?" * len(sess))
        q = (f"SELECT SUM(t) FROM (SELECT MAX(COALESCE(input_tokens,0)+COALESCE(cache_write_tokens,0)+COALESCE(output_tokens,0)) t "
             f"FROM transcript_record WHERE type='assistant' AND timestamp >= ? AND session_id IN ({ph}) "
             f"GROUP BY session_id, COALESCE(input_tokens,0), COALESCE(cache_write_tokens,0), COALESCE(cache_read_tokens,0), "
             f"substr(timestamp,1,16))")
        try:
            used = conn.execute(q, [month] + sess).fetchone()[0] or 0
        except Exception:  # noqa: BLE001
            continue
        out.setdefault(uid, {}).update(used=int(used), budget=int(budget), share=used / budget, method=TOKEN_METHOD)
    return out


REVIEW_VERSION = 9           # bump when delegation_series changes: the baseline cache is keyed by it (9: blocked apart, detected tags, cut waits; 8: tool templates)
REVIEW_STEP_S = 3600          # the cumulative series' grid
REVIEW_CACHE_S = 3600         # the baseline window is in the past: its series is recomputed at most hourly
REVIEW_EARLY_S = 86400        # before a day of the review window has passed, pace is shown but called early
METRIC_LABEL = {
    "founder.asks": "Asks to you from the unit",
    "founder.unit_share_of_founder_asks": "The unit's share of all asks to you",
    "founder.founder_messages_in_unit": "Your messages into the unit",
    "founder.agent_hours_waited": "Agent-hours waiting on your answers",
    "founder.plumbing_share_in_unit": "Plumbing share of your messages (status, routing, pastes)",
    "unit_owner_side.asks": "Asks the unit owner receives",
    "unit_owner_side.answer_delay_min.p50": "Unit owner's median answer delay, min",
    "founder.escalation_class": "Asks to you by escalation class (heuristic)",
    "founder.escalation_tag": "Asks to you by the asker's declared tag",
    "unit_owner_side.escalation_class": "Asks to the unit owner by escalation class (heuristic)",
    "unit_owner_yes.owner_yes": "The unit owner's own yes to keepers (uy-v1)",
    "unit_owner_yes.standing_yes": "Standing yes for future actions (uy-v1)",
    "unit_owner_yes.reverts_of_yes_merges": "Reverts of merges a yes named, within 14 days",
}


def predictions_file(repo, rec):
    """reports/delegations/<id>.yaml in the organization's repository (of_field-analyst's), as (path, doc) or None."""
    import yaml
    path = os.path.join(repo, "reports", "delegations", f"{rec.get('id')}.yaml") if repo and rec else None
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as fh:
            return path, (yaml.safe_load(fh) or {})
    except (OSError, yaml.YAMLError):
        return None


def _review_cache_path(rec_id):
    import of_events
    return os.path.join(of_events.STATE_DIR, "studio", f"review-{rec_id}.json")


def baseline_series(d, model, rec, bw, digest, effective, tag_fixes=None):
    """The baseline window's series on the hourly grid, cached for REVIEW_CACHE_S (keyed by the window and the
    predictions file's digest). The baseline lies before the page's own rows, so its rows are loaded here."""
    import json
    path = _review_cache_path(rec.get("id"))
    key = {"window": bw, "digest": digest, "model": of_model.MODEL_VERSION, "review": REVIEW_VERSION}
    try:
        with open(path) as fh:
            c = json.load(fh)
        if c.get("key") == key and d.now - c.get("computed_at", 0) < REVIEW_CACHE_S:
            return c["series"]
    except (OSError, ValueError):
        pass
    a, b = of_model.epoch(bw[0]), of_model.epoch(bw[1])
    rows = d.rows_between(a - 2 * DAY, b)
    n = int((b - a) // REVIEW_STEP_S)
    series = of_measures.delegation_series(model, rows, a, b, rec, [k * REVIEW_STEP_S for k in range(n + 1)] + [b - a],
                                           effective=effective, tag_fixes=tag_fixes)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + f".{os.getpid()}"
        with open(tmp, "w") as fh:
            json.dump({"key": key, "computed_at": d.now, "series": series}, fh)
        os.replace(tmp, path)
    except OSError:
        pass
    return series


SD_REF = __import__("re").compile(r"\bsd-[0-9]{4}-[0-9]{2}-[0-9]{2}-[A-Za-z0-9_-]+")


def unit_owner_yes_rule(d, model, rec, upto):
    """The unit-owner-yes rule of a delegation, when its envelope's `approvals` names the diff that set it
    (sd-2026-11-06-platform-unit-owner-yes): its measures from that diff's application until `upto`."""
    text = str(((rec.get("envelope") or {}).get("approvals")) or "")
    ids = SD_REF.findall(text)
    if not ids:
        return None
    applied = d.diffs_applied() if hasattr(d, "diffs_applied") else {}
    since = next((applied[i] for i in ids if i in applied), None)
    if since is None:
        return {"diff": ids[0], "since": None, "text": text}
    m = of_measures.unit_owner_yes(model, d.rows, since, upto, rec)
    horizon = max((of_model.epoch((r.get("payload") or {}).get("merged_at")) or 0 for r in d.rows
                   if r["name"] == "of.work_record" and (r.get("payload") or {}).get("source") == "gh"), default=0)
    return dict(m, diff=next(i for i in ids if i in applied), since=of_model.iso(since), text=text,
                prs_imported_until=of_model.iso(horizon) if horizon else None, prs_not_imported=horizon < since)


def _verdict(op, o, v):
    if o is None or v is None:
        return None
    return o <= v if op == "<=" else o >= v if op == ">=" else None


def delegation_review(d, model, rec):
    """Predictions against facts for a delegation (the review view): the predictions file, the baseline window
    recomputed, the review window so far, and the pace against the baseline at the same point of its window.
    None without the file."""
    import hashlib
    found = predictions_file(getattr(d, "repo", None), rec)
    if not found:
        return None
    path, doc = found
    with open(path, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()[:16]
    bw, rw = (doc.get("baseline") or {}).get("window") or [], doc.get("review_window") or []
    if len(bw) != 2 or len(rw) != 2:
        return None
    ra, rb = of_model.epoch(rw[0]), of_model.epoch(rw[1])
    upto = max(ra, min(d.now, rb))
    elapsed, length = upto - ra, rb - ra
    eff = d.delegations_effective()
    tag_fixes = {str(x.get("ask")): x.get("tag") for x in doc.get("tag_corrections") or [] if isinstance(x, dict) and x.get("ask")}
    base = baseline_series(d, model, rec, bw, digest, eff, tag_fixes)
    n = int(elapsed // REVIEW_STEP_S)
    # the moments of changes inside the window (a structural diff the caveats name, a dated practice change): the
    # series is also computed exactly there, so "before" is the value at that time, not at the hour before it
    applied = d.diffs_applied() if hasattr(d, "diffs_applied") else {}
    marks = [(sid, applied.get(sid), None) for sid in dict.fromkeys(SD_REF.findall(" ".join(str(c) for c in doc.get("caveats") or [])))]
    marks += [(x.get("id") or "practice", of_model.epoch(str(x.get("at"))) if x.get("at") else None, x.get("what"))
              for x in doc.get("practice_changes") or [] if isinstance(x, dict)]
    marks = sorted([m for m in marks if m[1] and ra < m[1] <= upto], key=lambda m: m[1])
    offs = sorted(set([k * REVIEW_STEP_S for k in range(n + 1)] + [t - ra for _, t, _ in marks])) + [elapsed]
    obs = of_measures.delegation_series(model, d.rows, ra, upto, rec, offs, effective=eff, tag_fixes=tag_fixes,
                                        cuts=[t for _, t, _ in marks])
    base_end, now = base[-1], obs[-1]
    same = base[min(len(base) - 2, int(elapsed // REVIEW_STEP_S))]   # the baseline at the same point of its window
    ended = d.now >= rb
    rows = []
    for p in doc.get("predictions") or []:
        m, op, v = p.get("metric"), p.get("op"), p.get("value")
        additive = m.split(".")[-1] in of_measures.DELEGATION_ADDITIVE
        o, b_end, b_same = (of_measures.dotted(x, m) for x in (now, base_end, same))
        shape = m
        if additive and not b_end and m.startswith("unit_owner_side."):
            # no baseline curve (the unit owner did not exist): the unit's founder asks of the baseline week give the shape
            shape = "founder." + m.split(".", 1)[1]
        s_end, s_same = of_measures.dotted(base_end, shape), of_measures.dotted(same, shape)
        if additive and v is not None:
            frac = (s_same / s_end) if s_end else (elapsed / length if length else 1.0)
            expected = round(v * frac, 1)
        else:
            expected = v
        ok, decided = _verdict(op, o, v if ended else expected), ended
        if additive and not ended and o is not None and v is not None and (o > v if op == "<=" else o >= v if op == ">=" else False):
            ok, decided = op == ">=", True   # a growing measure past its value is settled for the week, whatever the pace
        md = p.get("min_denominator") or {}
        if md and (of_measures.dotted(now, md.get("metric", "")) or 0) < (md.get("gte") or 0):
            ok = "not_judged"
        unmeasurable = p.get("unmeasurable")
        if unmeasurable:
            ok = "unmeasurable"
        rows.append({"metric": m, "label": METRIC_LABEL.get(m, m), "op": op, "value": v, "additive": additive,
                     "baseline": of_measures.dotted(doc.get("baseline") or {}, m), "baseline_ours": b_end,
                     "baseline_same_point": b_same if additive else None, "observed": o, "expected_now": expected,
                     "on_track": ok, "decided": decided, "shape": shape if shape != m else None,
                     "unmeasurable": unmeasurable, "why": p.get("why"), "min_denominator": md or None,
                     "restated": p.get("restated"), "definition": of_measures.definition_of(m)})
    # a structural change named in the caveats that applied inside the window: its time, and each additive measure
    # before and after it (the verdicts stay on the whole window)
    by_at = {x["at"]: x for x in obs}
    changes = [{"id": sid, "what": what, "at": of_model.iso(t), "offset_h": round((t - ra) / 3600, 1),
                "point": by_at.get(of_model.iso(min(upto, t))) or obs[-1]} for sid, t, what in marks]
    def before(c, metric):
        if metric.endswith("agent_hours_waited"):   # durations: each wait cut at the change
            side_ = metric.split(".", 1)[0]
            return ((now.get(side_) or {}).get("agent_hours_waited_before") or {}).get(c["at"])
        return of_measures.dotted(c["point"], metric)
    for r in rows:
        r["parts"] = [{"id": c["id"], "before": before(c, r["metric"])} for c in changes] \
            if r["additive"] and changes else []
    yes_rule = unit_owner_yes_rule(d, model, rec, upto)
    if yes_rule and yes_rule.get("since"):
        now = dict(now, unit_owner_yes={k: v for k, v in yes_rule.items() if k not in ("text",)})
    reported = []
    for m in doc.get("reported_not_predicted") or []:
        reported.append({"metric": m, "label": METRIC_LABEL.get(m, m), "baseline": of_measures.dotted(doc.get("baseline") or {}, m),
                         "baseline_ours": of_measures.dotted(base_end, m), "observed": of_measures.dotted(now, m)})
    hold = (doc.get("baseline") or {}).get("hold") or doc.get("hold")
    if hold:
        for r in rows:
            r["on_track"] = "not_judged"
    return {"file": os.path.relpath(path, d.repo), "delegation": rec.get("id"), "unit": doc.get("unit"),
            "unit_owner": rec.get("to_owner"), "from_owner": rec.get("from_owner"), "review": rec.get("review"),
            "baseline_window": bw, "review_window": rw, "observed_until": of_model.iso(upto),
            "elapsed_h": round(elapsed / 3600, 1), "length_h": round(length / 3600, 1), "ended": ended,
            "early": elapsed < REVIEW_EARLY_S, "rows": rows, "reported": reported, "hold": hold,
            "caveats": doc.get("caveats") or [], "measured_by": doc.get("measured_by"), "changes": changes,
            "now": now, "baseline_end": base_end, "baseline_same": same, "yes_rule": yes_rule,
            "budget": (getattr(d, "budget", None) or {}).get(rec.get("unit") or doc.get("unit")),
            "series": {"baseline": base[:-1], "review": obs}}


def kinds():
    import of_asks
    return tuple(sorted(set(STUDIO_KINDS) | set(of_diagnose.kinds_needed()) | set(getattr(of_asks, "READS", ()))))


def _normalize(r):
    """A row from of-server's reader (ClickHouse view columns) in load_events() shape."""
    pl = r.get("payload")
    if isinstance(pl, str):
        import json
        try:
            pl = json.loads(pl) if pl else {}
        except ValueError:
            pl = {}
    ts = str(r.get("ts") or "")
    if len(ts) >= 19 and ts[10] == " ":  # ClickHouse 'YYYY-MM-DD HH:MM:SS.mmm' (UTC) -> ISO 8601 with Z
        ts = ts[:10] + "T" + ts[11:] + ("" if ts.endswith("Z") else "Z")
    return {"event_id": r.get("event_id"), "ts": ts, "name": r.get("name"), "session_id": r.get("session_id"),
            "position": r.get("position"), "org": r.get("org") or r.get("org_id"), "cwd": r.get("cwd"),
            "source": r.get("source"), "harness": r.get("harness") or r.get("harness_name"), "payload": pl or {}}


def _enrich(items, model, org):
    for x in items:
        p = model["positions"].get(x["position"], {})
        x.update(org=org, system=p.get("system"), throughput=p.get("throughput"), summary=p.get("summary"))
    return items


ALL = "all"


def attention_all(now=None, conn=None, orgs_file=None, rows=None):
    """The inbox list for every organization from the local store, cheaply (no measures, no heatmap):
    for `of who` and `of status`. Rows: attention() rows plus org, system, throughput, summary."""
    now = now or time.time()
    if rows is None:
        try:
            import of_store
            rows = of_model.load_events(conn or of_store.connect(), since=of_model.iso(now - 9 * DAY), names=STUDIO_KINDS)
        except Exception:  # noqa: BLE001 — no store: registry-declared waits only
            rows = []
    all_orgs = orgs(orgs_file)
    out = []
    for o in all_orgs:
        doc, rev = of_model.registry_at(o["repo"], now)
        model = of_model.build(doc, [], at=now, org=o["name"], rev=rev)
        mine = org_rows(rows, o, [x for x in all_orgs if x is not o])
        out += _enrich(of_measures.attention(model, mine, now), model, o["name"])
    rank = {"waiting_owner": 0, "stalled": 1, "waiting_peer": 2, "waiting_external": 3}
    return sorted(out, key=lambda x: (rank[x["kind"]], x["since"] or "9999", x["org"], x["position"]))


class AllData:
    """The tenant's inbox across every organization it owns: one list, an organization column."""

    def __init__(self, parts):
        self.parts = parts
        self.org = ALL
        self.orgs = [ALL] + [p.org for p in parts]
        self.now = parts[0].now
        self.a, self.b = parts[0].a, parts[0].b
        self.rows = [r for p in parts for r in p.rows]
        self.model = {"registry_rev": ", ".join(f"{p.org} {str(p.model['registry_rev'])[:12]}" for p in parts)}
        self.owner = {"dead_end_asks": [k for p in parts for k in p.owner["dead_end_asks"]]}
        rank = {"waiting_owner": 0, "stalled": 1, "waiting_peer": 2, "waiting_external": 3}
        self.diagnosis = {"issues": [dict(i, org=p.org) for p in parts for i in p.diagnosis["issues"]],
                          "rules": [dict(r, org=p.org) for p in parts for r in p.diagnosis["rules"]]}
        self.attention = sorted((x for p in parts for x in p.attention),
                                key=lambda x: (rank[x["kind"]], x["since"] or "9999", x["org"], x["position"]))


class Data:
    """Everything one page needs, computed once per request from the registry and the local store."""

    def __init__(self, org_name=None, now=None, days=7, orgs_file=None, conn=None, rows=None, source=None, unit=None):
        all_orgs = orgs(orgs_file)
        if not all_orgs:
            raise FileNotFoundError("no organizations listed in orgs.yaml")
        self.orgs = ([ALL] if len(all_orgs) > 1 else []) + [o["name"] for o in all_orgs]
        org = next((o for o in all_orgs if o["name"] == org_name), None) or (all_orgs[-1] if all_orgs else None)
        self.org = org["name"] if org else None
        self.now = now or time.time()
        self.a, self.b = self.now - days * DAY, self.now
        self.repo = org["repo"]
        doc, rev = of_model.registry_at(org["repo"], self.now)
        loaded = rows is None   # rows given (tests, callers with their own rows) are the page's whole input
        since = of_model.iso(self.a - 2 * DAY)  # two more days, for asks opened before the window
        if rows is None and source is not None:
            # profile 2: of-server's reader over the organization's database (rows are that organization's already)
            rows = of_asks.apply_prompt_origins([_normalize(r) for r in source.events(self.org, since_iso=since, names=kinds())])
            self.rows = rows
        else:
            if rows is None:
                try:
                    import of_store
                    conn = conn or of_store.connect()
                    rows = of_model.load_events(conn, since=since, names=kinds())
                except Exception:  # an empty or missing store still shows the registry views
                    rows = []
            self.rows = org_rows(rows, org, [o for o in all_orgs if o is not org])
            if not unit:   # the tenant's own Studio opens count whatever organization the page showed
                have = {r["event_id"] for r in self.rows}
                self.rows += [r for r in rows if r["name"] == "of.studio_viewed" and r["event_id"] not in have]
        self.unit = unit
        self._loaded = loaded
        self._source, self._org_cfg, self._others = source, org, [o for o in all_orgs if o is not org]
        self._rows_from = of_model.epoch(since)
        if loaded:   # a loop runs longer than the window: its whole history (RFC 0005)
            have = {r.get("event_id") for r in self.rows}
            self.rows += [r for r in self.loop_rows() if r.get("event_id") not in have]
        full = of_model.build(doc, self.rows, at=self.now, org=self.org, rev=rev, window_h=days * 24)
        self.compute(full, unit)

    def rows_between(self, a, b):
        """The organization's rows in [a, b): the page's own when they cover it, else loaded (the review's baseline)."""
        if a >= getattr(self, "_rows_from", a):
            return [r for r in self.rows if (of_model.epoch(r.get("ts")) or 0) < b]
        src = getattr(self, "_source", None)
        if src is not None:
            rows = of_asks.apply_prompt_origins([_normalize(r) for r in src.events(self.org, since_iso=of_model.iso(a), names=kinds())])
        else:
            try:
                import of_store
                rows = of_model.load_events(of_store.connect(), since=of_model.iso(a), until=of_model.iso(b), names=kinds())
            except Exception:  # noqa: BLE001 — no store
                return []
            rows = org_rows(rows, self._org_cfg, self._others)
        return [r for r in rows if (of_model.epoch(r.get("ts")) or 0) < b]

    def delegations_effective(self):
        """When each delegation took effect, from every of.diff_applied the store holds (not only the page's rows)."""
        return of_model.delegation_effective(self.diff_rows())

    def diffs_applied(self):
        """{diff id: epoch} for every applied structural diff, of any kind."""
        out = {}
        for r in self.diff_rows():
            i = (r.get("payload") or {}).get("id")
            if i:
                out.setdefault(i, of_model.epoch(r.get("ts")))
        return out

    def pr_state_rows(self):
        """Every of.pr_state the store holds for the organization (one per change of an open PR): the latest state
        of a PR opened long before the page's window."""
        if getattr(self, "_pr_rows", None) is not None:
            return self._pr_rows
        src = getattr(self, "_source", None)
        try:
            if src is not None:
                rows = [_normalize(r) for r in src.events(self.org, since_iso="2000-01-01T00:00:00Z", names=("of.pr_state",))]
            else:
                import of_store
                rows = org_rows(of_model.load_events(of_store.connect(), names=("of.pr_state",)), self._org_cfg, self._others)
        except Exception:  # noqa: BLE001 — no store: the page's own rows
            rows = []
        self._pr_rows = rows
        return rows

    def loop_rows(self):
        """Every development-loop event of the organization (RFC 0005 §4.1): a loop runs longer than the page's window."""
        if getattr(self, "_loop_rows", None) is not None:
            return self._loop_rows
        import of_loops
        src = getattr(self, "_source", None)
        try:
            if src is not None:
                rows = [_normalize(r) for r in src.events(self.org, since_iso="2000-01-01T00:00:00Z", names=of_loops.KINDS)]
            else:
                import of_store
                conn = of_store.connect()
                try:
                    rows = org_rows(of_model.load_events(conn, names=of_loops.KINDS), self._org_cfg, self._others)
                finally:
                    conn.close()
        except Exception:  # noqa: BLE001 — no store: the page's own rows
            rows = [r for r in self.rows if r.get("name") in of_loops.KINDS]
        self._loop_rows = rows
        return rows

    def loop_of(self, w):
        """The loop card's data for a work: the model's projection (computed once, for the card and the rules), else,
        for a work listed outside positions.yaml, of_loops.project on the same rows; None without loop and events."""
        loops = (getattr(self, "full", None) or {}).get("loops") or {}
        if w["id"] in loops and (loops[w["id"]].get("declared") or not w.get("loop")):
            return loops[w["id"]]   # else the model saw the events, but the spec is in a list outside positions.yaml
        if not w.get("loop"):
            return None
        import of_loops
        cache = self.__dict__.setdefault("_loop_cards", {})
        if w["id"] not in cache:
            cache[w["id"]] = of_loops.project(w["id"], w.get("loop"), self.rows, self.now, work=w)
        return cache[w["id"]]

    def history(self, names, since=None):
        """Every event of these kinds the store holds for the organization (from `since`, epoch seconds), whatever
        the page's window; with rows given (tests, callers with their own rows), those rows."""
        lo = of_model.iso(since) if since is not None else None
        mine = lambda: [r for r in self.rows if r.get("name") in names
                        and (since is None or (of_model.epoch(r.get("ts")) or 0) >= since)]
        if not getattr(self, "_loaded", False):
            return mine()
        src = getattr(self, "_source", None)
        try:
            if src is not None:
                return [_normalize(r) for r in src.events(self.org, since_iso=lo or "2000-01-01T00:00:00Z", names=tuple(names))]
            import of_store
            conn = of_store.connect()
            try:
                return org_rows(of_model.load_events(conn, since=lo, names=tuple(names)), self._org_cfg, self._others)
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — no store: the page's own rows
            return mine()

    def code_activity(self, days=of_model.CODE_DAYS):
        """Per-system code activity, writers and co-change over the last `days` (RFC 0007 §2.2), from merged work
        records and open PRs: the Systems page's code evidence, and the diagnose rules' (context["code_activity"])."""
        cache = self.__dict__.setdefault("_code_activity", {})
        if days not in cache:
            a = self.now - days * DAY
            rows = self.history(("of.work_record",), since=a - DAY)
            rows += [r for r in (self.pr_state_rows() if getattr(self, "_loaded", False) else self.history(("of.pr_state",)))
                     if (of_model.epoch(r.get("ts")) or 0) >= a]
            cache[days] = of_model.code_activity(self.full, rows, a, self.now)
        return cache[days]

    def ask_ids(self):
        """Event ids of every ask the store holds (any organization: the system architect's ask may be filed from
        another one), to check the ask ids under `confirmed:`; None when not checkable (rows given, no store)."""
        if "_ask_ids" in self.__dict__:
            return self._ask_ids
        ids = None
        if getattr(self, "_loaded", False):
            src = getattr(self, "_source", None)
            try:
                if src is not None:
                    ids = {r["event_id"] for r in src.events(self.org, since_iso="2000-01-01T00:00:00Z", names=of_model.ASK_KINDS)}
                else:
                    import of_store
                    conn = of_store.connect()
                    try:
                        ids = {x for (x,) in conn.execute("SELECT event_id FROM event WHERE name IN (?, ?)", of_model.ASK_KINDS)}
                    finally:
                        conn.close()
            except Exception:  # noqa: BLE001 — no store: not checked
                ids = None
        self._ask_ids = ids
        return ids

    def evidence(self):
        if "_evidence" not in self.__dict__:
            self._evidence = of_model.evidence(self.model, self.code_activity(), self.ask_ids())
        return self._evidence

    def corrections(self, weeks=CORRECTION_WEEKS):
        """The repeat-correction rate per position and week (RFC 0006 §2.4), from every of.founder_correction the
        organization has: a week's rate needs its whole week, whatever the page's window."""
        cache = self.__dict__.setdefault("_corrections", {})
        if weeks not in cache:
            cache[weeks] = of_measures.repeat_corrections(self.full, self.history(("of.founder_correction",)),
                                                          self.now, weeks)
        return cache[weeks]

    def diff_rows(self):
        if getattr(self, "_diff_rows", None) is not None:
            return self._diff_rows
        src = getattr(self, "_source", None)
        try:
            if src is not None:
                rows = [_normalize(r) for r in src.events(self.org, since_iso="2000-01-01T00:00:00Z", names=("of.diff_applied",))]
            else:
                import of_store
                rows = org_rows(of_model.load_events(of_store.connect(), names=("of.diff_applied",)), self._org_cfg, self._others)
        except Exception:  # noqa: BLE001 — no store: the page's own rows
            rows = []
        have = {r.get("event_id") for r in rows}
        self._diff_rows = sorted(rows + [r for r in self.rows if r["name"] == "of.diff_applied" and r.get("event_id") not in have],
                                 key=lambda r: of_model.epoch(r.get("ts")) or 0)
        return self._diff_rows

    def compute(self, full, unit=None):
        """Measures over the whole organization, then filtered for display (RFC 0001 §5.4): an owner of a unit
        sees its positions, its systems and the asks that reach it, never the rest."""
        who = unit or of_model.HUMAN
        self.full = full
        names = of_model.unit_of(full, unit) if unit else set(full["positions"])
        self.model = of_model.restrict(full, names) if unit else full
        self.owner = of_measures.owner(full, self.rows, self.a, self.b, who=who)
        self.asks = [k for k in of_measures.asks_of(full, self.rows, who) if k["state"] == "open"]
        self.attention = [x for x in _enrich(of_measures.attention(full, self.rows, self.now, who=who), full, self.org)
                          if x["position"] in names or x["position"] == "(unregistered)" and not unit]
        w = of_measures.waits(full, self.rows, self.a, self.b, who=who)
        w["grid"] = {p: v for p, v in w["grid"].items() if p in names or not unit}
        w["declared"] = {p: v for p, v in w["declared"].items() if p in names}
        self.waits = w
        self.queues = {p: v for p, v in of_measures.inbound_queues(full, self.rows, self.a, self.b).items()
                       if p in names or not unit}
        repo = getattr(self, "repo", None)
        ctx = {"keeper_since": lambda sysid, k: of_model.keeper_since(repo, sysid, k),
               "code_activity": self.code_activity} if repo else {"code_activity": self.code_activity}   # RFC 0007
        diag = of_diagnose.diagnose(full, self.rows, self.a, self.b, context=ctx)
        self.diagnosis = of_diagnose.restrict(diag, names, self.model["systems"]) if unit else diag
        # delegated units below this viewer, rolled up as one row each (RFC 0001 §5.8, the reporting line)
        humans = sorted(h for h in full["units"] if h != who and (not unit or h in names))
        self.rollup = []
        self.budget = budget_used(full, self.rows, self.now) if getattr(self, "repo", None) else {}
        self.escalations = of_diagnose.escalations(full, diag, self.budget)
        dels = {u["owner"]: (uid, u["delegation"]) for uid, u in (full.get("org_units") or {}).items() if u.get("delegation")}
        for h in humans:
            o = of_measures.owner(full, self.rows, self.a, self.b, who=h)
            un = of_model.unit_of(full, h)
            att = [x for x in of_measures.attention(full, self.rows, self.now, who=h) if x["position"] in un]
            uid, rec = dels.get(h, (None, None))
            if rec:
                sysset, posset = of_diagnose.unit_members(full, uid)
                un = sorted(set(un) | posset)
            issues_open = sum(1 for i in diag["issues"] if i["state"] == "open" and set(i["affected"]) & set(un))
            bu = self.budget.get(uid) if uid else None
            self.rollup.append({"owner": h, "positions": len(un), "unit": uid, "delegation": (rec or {}).get("id"),
                                "budget": bu, "has_review": bool(rec and predictions_file(getattr(self, "repo", None), rec)),
                                "review": (rec or {}).get("review"), "issues_open": issues_open,
                                "escalations": [x for x in self.escalations if x["to_owner"] == h and x["status"] == "fired"],
                                "throughput_share": o["throughput_share"]["share"],
                                "active": sum(full["positions"][n]["status"] == "active" for n in un if n in full["positions"]),
                                "waiting_on_owner": sum(x["kind"] == "waiting_owner" for x in att),
                                "stalled": sum(x["kind"] == "stalled" for x in att),
                                "open_asks": o["open_at_end"], "L": o["L"], "W_h": o["W_h"],
                                "dead_end": len(o["dead_end_asks"])})


# ------------------------------------------------------------------ html helpers

def e(x):
    return html.escape("" if x is None else str(x), quote=True)


def age(sec):
    sec = max(0, int(sec))
    if sec < 5400:
        return f"{sec // 60} min"
    if sec < 2 * DAY:
        return f"{sec // 3600} h"
    return f"{sec // DAY} d"


def num(x, unit="", nd=1):
    if x is None:
        return '<span class="na">n/a</span>'
    if isinstance(x, float):
        x = round(x, nd)
    return f"{e(x)}{e(unit)}"


def pct(x):
    return '<span class="na">n/a</span>' if x is None else f"{round(100 * x, 1)}%"


def link(view, org, **kw):
    q = {"org": org}
    q.update({k: v for k, v in kw.items() if v is not None})
    return f"/studio/{view}?" + urllib.parse.urlencode(q)


def page(d, view, body, css_href="/studio/static/studio.css", inline_css=None):
    nav = "".join(f'<a class="tab{" on" if v == view else ""}" href="{e(link(v, d.org))}">{e(t)}</a>' for v, t in VIEWS)
    orgs_ = "".join(f'<a class="org{" on" if o == d.org else ""}" href="{e(link(view if o != ALL else "inbox", o))}">'
                    f'{e("all organizations" if o == ALL else o)}</a>' for o in d.orgs)
    style = f"<style>{inline_css}</style>" if inline_css else f'<link rel="stylesheet" href="{e(css_href)}">'
    stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(d.now))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Org Studio · {e(d.org)}</title>{style}</head>
<body><header><div class="brand">Org Studio</div><nav class="orgs">{orgs_}</nav></header>
<nav class="tabs">{nav}</nav>
<main>{body}</main>
<footer>Model at {e(stamp)}<!--of:age--> · registry {e(d.model['registry_rev'])} · window {e(of_model.iso(d.a)[:10])} – {e(of_model.iso(d.b)[:10])} · {len(d.rows)} events</footer>
</body></html>"""


def empty_note(d):
    if d.rows:
        return ""
    return ('<p class="note">No typed events for this organization in the local store yet: asks, waits and messages appear '
            'once the hooks are installed and history is imported. Registry views are live.</p>')


# ------------------------------------------------------------------ views

KIND_LABEL = {"waiting_owner": "Waiting on you", "stalled": "Stalled", "waiting_peer": "Waiting on a peer",
              "waiting_external": "Waiting outside"}


def _since(d, ts):
    t = of_model.epoch(ts) if ts else None
    if t is None:
        return '<span class="na">declared</span>'
    secs = d.now - t
    flag = "dead-end" if secs >= 24 * 3600 else ("stale" if secs >= 4 * 3600 else "")
    return f'<span class="age {flag}">{e(age(secs))}</span>'


def _what(x, n=240):
    """The text to show: a heuristic question sits at the end of the agent's last message, so show its tail."""
    t = " ".join(str(x.get("text") or x.get("summary") or "").split())
    if len(t) <= n:
        return t
    return "…" + t[-n:] if any("heuristic" in s for s in x.get("sources") or []) else t[:n] + "…"


def _att_rows(d, items, with_on=False):
    out = []
    for x in items:
        on = f'<td>{e(x["on"])}</td>' if with_on else ""
        org = f'<td class="src">{e(x.get("org"))}</td>' if d.org == ALL else ""
        out.append(f'<tr><td class="age">{_since(d, x["since"])}</td><td><b>{e(x["position"])}</b></td>{org}{on}'
                   f'<td>{e(x.get("system"))}</td><td>{e(x.get("throughput"))}</td>'
                   f'<td class="text">{e(_what(x))}</td>'
                   f'<td class="src">{e(", ".join(x["sources"]))}</td></tr>')
    return "".join(out)


def view_inbox(d):
    att = d.attention
    mine = [x for x in att if x["kind"] == "waiting_owner"]
    stalled = [x for x in att if x["kind"] == "stalled"]
    others = [x for x in att if x["kind"] in ("waiting_peer", "waiting_external")]
    oc = "<th>Org</th>" if d.org == ALL else ""
    head = f'<thead><tr><th>For</th><th>Position</th>{oc}<th>System</th><th>Class</th><th>What</th><th>Seen by</th></tr></thead>'
    head_on = f'<thead><tr><th>For</th><th>Position</th>{oc}<th>Waits on</th><th>System</th><th>Class</th><th>What</th><th>Seen by</th></tr></thead>'
    has_pulse = any(r["name"] in ("of.pulse", "of.stall_detected") for r in d.rows)
    stall_note = "" if has_pulse else ('<p class="note">Stalls are detected by <code>of pulse</code> (active mandate, idle, '
                                       'no declared reason); no pulse has run for this organization yet.</p>')
    o = d.owner
    sections = [
        f'<h2 class="lead">Waiting on you <span class="sub">one row per position, oldest first</span></h2>'
        + (f'<table class="att">{head}<tbody>{_att_rows(d, mine)}</tbody></table>' if mine else '<p class="quiet">Nobody.</p>'),
        f'<h2 class="lead">Stalled <span class="sub">active mandate, idle, no reason given</span></h2>{stall_note}'
        + (f'<table class="att">{head}<tbody>{_att_rows(d, stalled)}</tbody></table>' if stalled
           else ('<p class="quiet">Nobody.</p>' if has_pulse else "")),
        f'<h2>Waiting on someone else <span class="sub">who waits on whom</span></h2>'
        + (f'<table class="att">{head_on}<tbody>{_att_rows(d, others, with_on=True)}</tbody></table>' if others
           else '<p class="quiet">Nobody.</p>'),
    ]
    issues = [i for i in d.diagnosis["issues"] if i["state"] == "open"]
    critical = [i for i in issues if i["critical"]]
    routed = [i for i in issues if not i["critical"] and i.get("decider") == "structure_keeper"]
    normal = [i for i in issues if not i["critical"] and i.get("decider") != "structure_keeper"]
    keepers = sorted({i.get("decider_position") or "the structure keeper" for i in routed})
    routed_html = (f'<p class="note">{len(routed)} issue{"s" if len(routed) != 1 else ""} routed to '
                   f'{e(", ".join(keepers))} (the structure keeper decides): '
                   f'{e("; ".join(i["summary"] for i in routed[:3]))}{"…" if len(routed) > 3 else ""}</p>') if routed else ""
    crit_html = (f'<h2 class="lead crit">Critical <span class="sub">bypasses the ranking</span></h2>'
                 f'<table class="att">{_issue_head(d)}<tbody>{_issue_rows(d, critical)}</tbody></table>') if critical else ""
    unit_routed = [i for i in normal if i.get("decider") == "unit_owner" and getattr(d, "unit", None) != i.get("decider_position")]
    # a loop rule's issue (RFC 0005 §6): the work's doers act on it (the pulse nudges them); its owner decides it,
    # which leaves the founder's list when that owner owns a delegated unit and is not the viewer
    unit_owners = {u.get("owner") for u in ((getattr(d, "full", None) or {}).get("org_units") or {}).values() if u.get("delegation")}
    doer_routed = [i for i in normal if i.get("decider") == "doer"]
    unit_routed += [i for i in normal if i.get("decider") == "work_owner" and i.get("decider_position") in unit_owners
                    and getattr(d, "unit", None) != i.get("decider_position")]
    normal = [i for i in normal if i not in unit_routed and i not in doer_routed]
    doer_html = (f'<p class="note">{len(doer_routed)} loop issue{"s" if len(doer_routed) != 1 else ""} for the works\' doers '
                 f'({e(", ".join(sorted({i.get("decider_position") or "no doer" for i in doer_routed})))}; the pulse nudges them): '
                 f'{e("; ".join(i["summary"] for i in doer_routed[:3]))}{"…" if len(doer_routed) > 3 else ""}</p>') if doer_routed else ""
    ranked = [i for i in normal if of_diagnose.rank_group(i) == 1]
    risks = [i for i in normal if of_diagnose.rank_group(i) == 2]
    unit_html = (f'<p class="note">{len(unit_routed)} issue{"s" if len(unit_routed) != 1 else ""} inside delegated units, '
                 f'decided by their owners ({e(", ".join(sorted({i["decider_position"] for i in unit_routed})))}); '
                 f'they reach you only through the unit\'s escalation rules.</p>') if unit_routed else ""
    sections.append(
        f'<h2>Issues <span class="sub">ranked by owner minutes saved per minute deciding '
        f'(catalogue {e(d.diagnosis.get("catalogue") or "diagnose-v0")})</span></h2>'
        + (f'<table class="att">{_issue_head(d)}<tbody>{_issue_rows(d, ranked)}</tbody></table>' if ranked
           else '<p class="quiet">None with a known gain.</p>')
        + (f'<h2>Risks without a ΔT yet <span class="sub">by severity, then age of the evidence</span></h2>'
           f'<table class="att">{_issue_head(d)}<tbody>{_issue_rows(d, risks)}</tbody></table>' if risks else "")
        + unit_html
        + doer_html
        + routed_html
        + ('<p class="legend">Label an issue: <code>of issues label &lt;issue&gt; true_positive | false_positive | '
           'true_but_not_now | wrong_remedy [--note …]</code></p>' if normal or critical else "")
        + _rules_status(d))
    return f"""{empty_note(d)}{crit_html}
<section class="tiles">{tile("Waiting on you", num(len(mine)))}{tile("Stalled", num(len(stalled)) if has_pulse else num(None))}
{tile("Waiting on others", num(len(others)))}{tile("Dead-end asks", num(len(o["dead_end_asks"])))}
{tile("Open issues", num(len(normal) + len(critical)), f"{len(routed)} more for the structure keeper" if routed else "")}</section>
{"".join(sections)}"""


def _issue_head(d):
    oc = "<th>Org</th>" if d.org == ALL else ""
    return f'<thead><tr><th>Rule</th>{oc}<th>What</th><th>Default remedy</th><th>Score · severity</th><th>Issue</th></tr></thead>'


GROUPED = ("ap.function-gap",)   # one issue per item in the projection, one expandable row on the page


def _grouped_row(d, rule, items):
    org = f'<td class="src">{e(items[0].get("org"))}</td>' if d.org == ALL else ""
    names = [i["evidence"].get("function") or i["affected"][0] for i in items]
    inner = "".join(f'<li>{e(str(n).replace("_", " "))} <code>of issues label {e(i["issue_id"])} false_positive</code></li>'
                    for n, i in zip(names, items))
    return (f'<tr><td><b>{e(rule[3:])}</b><div class="src">{e(items[0]["rule_version"])}</div></td>{org}'
            f'<td class="text"><details><summary>{e(rule[3:].replace("-", " "))}s: {len(items)} '
            f'({e(", ".join(str(n).replace("_", " ") for n in names))})</summary>'
            f'<p class="src">Dismiss a function the organization does not need with its own label:</p><ul>{inner}</ul></details></td>'
            f'<td>{e((items[0]["remedies"] or [""])[0])}</td><td class="num">risk</td><td class="src">{len(items)} issues</td></tr>')


def _issue_rows(d, items):
    out, groups = [], {}
    for i in items:
        if i["rule"] in GROUPED:
            groups.setdefault((i["rule"], i.get("org")), []).append(i)
    for i in items:
        if i["rule"] in GROUPED:
            key = (i["rule"], i.get("org"))
            if key in groups:
                out.append(_grouped_row(d, i["rule"], groups.pop(key)))
            continue
        org = f'<td class="src">{e(i.get("org"))}</td>' if d.org == ALL else ""
        t = i["score_terms"]
        ev = i.get("evidence") or {}
        if ev.get("registry_unconfirmed"):
            what = ev.get("unconfirmed_what") or []
            label = (", ".join(what) + " not yet confirmed") if what else "registry entry not yet confirmed"
            why = "; ".join(f"{k}: {v}" for k, v in (ev.get("unconfirmed") or {}).items() if v)
            unconfirmed = f' <span class="flag" title="{e(why)}">{label}</span>' + (f'<div class="src">{e(why[:240])}</div>' if why else "")
        else:
            unconfirmed = ""
        out.append(f'<tr><td><b>{e(i["rule"][3:])}</b><div class="src">{e(i["rule_version"])}</div></td>{org}'
                   f'<td class="text">{e(i["summary"])}{unconfirmed}</td><td>{e((i["remedies"] or [""])[0])}</td>'
                   f'<td class="num" title="p {t["p"]} × Δm {t["delta_m"]} min ÷ {t["m_decide"]} min to decide">'
                   f'{i["score"] if t["delta_m"] else "–"} · {e(i.get("severity", "medium"))}</td>'
                   f'<td class="src">{e(i["issue_id"])}{" · wrong remedy" if i.get("wrong_remedy") else ""}</td></tr>')
    return "".join(out)


RULE_RANK = {"fired": 0, "quiet": 1, "not_built": 2, "blocked": 3}


def _rules_status(d):
    """One line per status; a rule counted once, at its best status across organizations (fired > quiet > not built > blocked)."""
    best = {}
    for r in d.diagnosis.get("rules") or []:
        cur = best.get(r["rule"])
        if cur is None or RULE_RANK[r["status"]] < RULE_RANK[cur["status"]]:
            best[r["rule"]] = dict(r)
        elif cur["status"] == r["status"]:
            cur["issues"] += r["issues"]
            if r["status"] == "blocked" and len(r["missing"]) < len(cur["missing"]):
                cur["missing"] = r["missing"]   # the organization closest to running it
    if not best:
        return ""
    parts = []
    for st, label in (("fired", "fired"), ("quiet", "quiet"), ("not_built", "not built yet")):
        rs = sorted(r for r, x in best.items() if x["status"] == st)
        if rs:
            names = ", ".join(r[3:] + (f" ({best[r]['issues']})" if best[r]["issues"] else "")
                              + (f" [blocked: {', '.join(sorted(best[r]['parts_blocked']))}]" if best[r].get("parts_blocked") else "")
                              for r in rs)
            parts.append(f'<div><span class="rs {st}">{e(label)}</span> {e(names)}</div>')
    for r in sorted(r for r, x in best.items() if x["status"] == "blocked"):
        parts.append(f'<div><span class="rs blocked">blocked</span> {e(r[3:])} <span class="src">— missing: '
                     f'{e(", ".join(best[r]["missing"]))}</span></div>')
    return f'<div class="rules">{"".join(parts)}</div>'


def tile(label, value, hint=""):
    return f'<div class="tile"><div class="v">{value}</div><div class="l">{e(label)}</div>{f"<div class=h>{e(hint)}</div>" if hint else ""}</div>'


def view_owner(d):
    o = d.owner
    lt = o
    ts = o["throughput_share"]
    tiles = "".join([
        tile("L · decision inventory", num(lt["L"], "", 2), "time-average open asks"),
        tile("λ · asks per hour", num(lt["lambda_per_h"], "", 2)),
        tile("W · mean wait", num(lt["W_h"], " h", 2), f"{lt['censored']} still open"),
        tile("Little residual L − λW", num(lt["residual"], "", 2),
             f"{lt['carry_in']} asks carried in add {lt['carry_in_L']}" if lt.get("carry_in") else "≈ 0 when stationary"),
        tile("Moved on unanswered", num(o.get("moved_on")), "the agent went on; not in answer delays"),
        tile("Answer p50 / p75 / p90", f'{num(o["answer_delay_min"]["p50"])} / {num(o["answer_delay_min"]["p75"])} / {num(o["answer_delay_min"]["p90"])} min'),
        tile("Agent-hours waited", num(o["agent_hours_waited"], " h"), "gaps over 12 h left out"),
        tile("Dead-end asks", num(len(o["dead_end_asks"]))),
        tile("Inbound concentration", pct(o["inbound_concentration"]), "your share of all requests, peers included"),
        tile("… from non-throughput work", pct(o["inbound_from_non_throughput"])),
        tile("Plumbing share", pct(o["plumbing_share"]), "target under 5%"),
        tile("Status polls", num(o["status_polls"]), "sp-v1; target near zero"),
        tile("Studio opens", num(o["polls_vs_studio"]["views"]) if o["polls_vs_studio"]["recorded"] else num(None),
             "page loads by you" if o["polls_vs_studio"]["recorded"] else "not recorded yet (of-server)"),
        tile("Time on throughput (a)", pct(ts["share"]), "raise + sustain"),
    ])
    me = getattr(d, "unit", None) or of_model.HUMAN
    mine = [n for n, p in d.model["positions"].items() if p["accountable"] == me or n == me]
    units = [f'<tr><td><b>{e(me)}</b></td><td>{len(mine)}</td>'
             f'<td>{sum(d.model["positions"][n]["status"] == "active" for n in mine)}</td>'
             f'<td>{o["open_at_end"]}</td><td>{num(o["L"], "", 2)}</td><td>{num(o["W_h"], " h", 2)}</td>'
             f'<td>{len(o["dead_end_asks"])}</td><td colspan="2" class="src">your own</td></tr>']
    for u in getattr(d, "rollup", []):
        esc = u.get("escalations") or []
        tag = (f'unit {e(u["unit"])} · {e(u["delegation"])}' + (f' · review {e(u["review"])}' if u.get("review") else "")
               + (f' · tokens {round(100 * u["budget"]["share"], 1)}% of the month\'s budget' if (u.get("budget") or {}).get("share") is not None else "")
               + (f' · ${u["budget"]["usd_used"]:,.0f} of ${u["budget"]["usd_budget"]:,.0f} ({round(100 * u["budget"]["usd_share"], 1)}%)'
                  if (u.get("budget") or {}).get("usd_share") is not None else
                  (f' · ${u["budget"]["usd_used"]:,.0f} at list this month' if (u.get("budget") or {}).get("usd_used") is not None else ""))
               if u.get("delegation") else "delegated unit")
        units.append(f'<tr><td>{e(u["owner"])}</td><td>{u["positions"]}</td><td>{u["active"]}</td><td>{u["open_asks"]}</td>'
                     f'<td>{num(u["L"], "", 2)}</td><td>{num(u["W_h"], " h", 2)}</td><td>{u["dead_end"]}</td>'
                     f'<td>{u["waiting_on_owner"]} waiting, {u["stalled"]} stalled, {u.get("issues_open", 0)} issues'
                     f'{"; <b>escalate: " + e(", ".join(x["condition"] for x in esc)) + "</b>" if esc else ""}</td>'
                     f'<td class="src">{tag}</td></tr>')
    cls = "".join(f'<tr><td>{e(k)}</td><td>{v}</td></tr>' for k, v in sorted(ts["by_class"].items(), key=lambda kv: -kv[1]))
    return f"""{empty_note(d)}<h2>{e(o['owner'])} <span class="sub">{e(o['from'][:10])} – {e(o['to'][:10])}</span></h2>
<section class="tiles">{tiles}</section>
{_polls_chart(o["polls_vs_studio"])}
<h2>Units <span class="sub">a delegated unit is one row; its owner answers its asks</span></h2>
<table><thead><tr><th>Owner</th><th>Positions</th><th>Active</th><th>Open asks</th><th>L</th><th>W</th><th>Dead ends</th><th>Inside</th><th></th></tr></thead>
<tbody>{"".join(units)}</tbody></table>
{_corrections_html(d)}
{_review_tables(d)}
<h2>Your messages by throughput class</h2>{f'<table><tbody>{cls}</tbody></table>' if cls else '<p class="quiet">No messages in the window.</p>'}"""


def _corrections_html(d):
    """The repeat-correction rate per position and week (RFC 0006 §2.4): a unit owner sees its own positions."""
    if not hasattr(d, "corrections"):
        return ""
    rc = d.corrections()
    head = (f'<h2>Repeat corrections <span class="sub">the founder\'s corrections that repeat an earlier one to the same position, '
            f'of all, per ISO week ({e(rc["method"])}); splitting rules by role works if it falls</span></h2>')
    if not rc["recorded"]:
        return head + ('<p class="quiet">Not recorded yet: the judge writes <code>of.founder_correction</code> '
                       '(RFC 0006 step 4).</p>')
    unit = getattr(d, "unit", None)
    rows = {p: v for p, v in rc["positions"].items() if not unit or p in d.model["positions"]}
    if not rows:
        return head + '<p class="quiet">No corrections in these weeks.</p>'

    def cell(c):
        if not c["n"]:
            return '<td class="quiet">–</td>'
        return f'<td title="{c["repeats"]} of {c["n"]}">{pct(c["rate"])} <span class="src">{c["repeats"]}/{c["n"]}</span></td>'
    total = {w: {"n": sum(v["weeks"][w]["n"] for v in rows.values()),
                 "repeats": sum(v["weeks"][w]["repeats"] for v in rows.values())} for w in rc["weeks"]}
    for c in total.values():
        c["rate"] = round(c["repeats"] / c["n"], 3) if c["n"] else None
    body = []
    for p, v in sorted(rows.items(), key=lambda kv: (-kv[1]["n"], kv[0])):
        rules = "; ".join(f"{r} ×{k}" for r, k in v["repeated_rules"])
        body.append(f'<tr><td>{e(p)}</td>{"".join(cell(v["weeks"][w]) for w in rc["weeks"])}'
                    f'<td class="src">{e(rules)}</td></tr>')
    body.append(f'<tr><td><b>all</b></td>{"".join(cell(total[w]) for w in rc["weeks"])}<td></td></tr>')
    weeks = "".join(f"<th>{e(w[5:])}</th>" for w in rc["weeks"])
    return (head + f'<table><thead><tr><th>Position</th>{weeks}<th>Repeated most</th></tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table>')


def _polls_chart(pv):
    """Status polls to agents against Studio opens, per day (success.md goal 1: polls near zero once the view answers them)."""
    days = list(pv["days"].items())
    if not days:
        return ""
    peak = max([1] + [max(v["polls"], v["views"]) for _, v in days])
    bw, gap, h, left, top = 14, 22, 90, 34, 8
    width = left + len(days) * (2 * bw + gap) + 10
    out = [f'<svg class="heat" viewBox="0 0 {width} {h + 34}" width="{width}" height="{h + 34}" role="img" '
           f'aria-label="status polls to agents and Studio opens per day">',
           f'<line class="day" x1="{left - 4}" y1="{top + h}" x2="{width - 6}" y2="{top + h}"/>',
           f'<text class="axis" x="{left - 8}" y="{top + 8}" text-anchor="end">{peak}</text>']
    for i, (d, v) in enumerate(days):
        x = left + i * (2 * bw + gap)
        for j, (k, cls) in enumerate((("polls", "c-owner"), ("views", "c-peer"))):
            hh = round(h * v[k] / peak, 1)
            out.append(f'<rect class="{cls}" x="{x + j * bw}" y="{top + h - hh}" width="{bw - 2}" height="{hh}">'
                       f'<title>{e(d)}: {v[k]} {"status polls" if k == "polls" else "Studio opens"}</title></rect>')
        out.append(f'<text class="axis" x="{x}" y="{top + h + 14}">{e(d[5:])}</text>')
    out.append("</svg>")
    note = "" if pv["recorded"] else '<p class="note">Studio opens are recorded by of-server (<code>of.studio_viewed</code>); none yet.</p>'
    return (f'<h2>Status polls vs Studio opens <span class="sub">per day; polls should fall as Studio answers them</span></h2>'
            f'<p class="legend"><span class="sw c-owner"></span>status polls to agents <span class="sw c-peer"></span>Studio opens</p>'
            f'<div class="scroll">{"".join(out)}</div>{note}')


def _review_tables(d):
    out = [f'<p class="note">Delegation <code>{e(u["delegation"])}</code> (unit {e(u["unit"])}) has predictions to review'
           f'{" on " + e(u["review"]) if u.get("review") else ""}: <a href="{e(link("review", d.org))}">predictions against facts</a>.</p>'
           for u in getattr(d, "rollup", []) if u.get("has_review")]
    return "".join(out)


def _pace_word(r, rv):
    ok = r["on_track"]
    if ok == "not_judged":
        md = r.get("min_denominator") or {}
        return ("not judged yet" + (f" (under {md.get('gte')} in {METRIC_LABEL.get(md.get('metric'), md.get('metric'))})"
                                    if md else "")), "na"
    if ok == "unmeasurable":
        return "unmeasurable yet", "na"
    if ok is None:
        return ("no data yet", "na") if r.get("observed") is None else ("–", "na")
    if rv["ended"]:
        return ("met", "ok") if ok else ("missed", "off")
    if r.get("decided"):
        return ("met for the week", "ok") if ok else ("missed for the week", "off")
    if not r["additive"]:
        return ("met so far", "ok") if ok else ("not met so far", "off")
    word = ("on pace", "ok") if ok else ("off pace", "off")
    return (word[0] + (" · day 1, noisy" if rv["early"] and r["additive"] else ""), word[1])


def _review_chart(r, rv):
    """Cumulative over the window: the baseline week, the predicted path (the baseline scaled to the prediction),
    the review window so far, and the predicted value."""
    m, v = r["metric"], r["value"]
    base = [(of_model.epoch(x["at"]), of_measures.dotted(x, m) or 0) for x in rv["series"]["baseline"]]
    obs = [(of_model.epoch(x["at"]), of_measures.dotted(x, m) or 0) for x in rv["series"]["review"]]
    if not base or not obs:
        return ""
    b0, r0, length = base[0][0], obs[0][0], rv["length_h"] * 3600
    shape = [(of_model.epoch(x["at"]), of_measures.dotted(x, r.get("shape") or m) or 0) for x in rv["series"]["baseline"]]
    s_end = shape[-1][1]
    plan = [(t - b0, y * v / s_end) for t, y in shape] if s_end else [(0, 0), (length, v)]
    top = max([y for _, y in base] + [y for _, y in obs] + [v or 0, 1]) * 1.08
    w, h, l, t_, bm = 300, 120, 34, 8, 18

    def pts(xy, t0=0):
        return " ".join(f"{l + (t - t0) / length * (w - l - 6):.1f},{t_ + (1 - y / top) * (h - t_ - bm):.1f}" for t, y in xy)
    gy = t_ + (1 - (v or 0) / top) * (h - t_ - bm)
    days = "".join(f'<text class="axis" x="{l + k / 7 * (w - l - 6):.1f}" y="{h - 4}">{k}</text>' for k in range(0, 8))
    return (f'<figure class="spark"><figcaption>{e(r["label"])}</figcaption>'
            f'<svg viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="{e(r["label"])}: baseline, predicted and observed">'
            f'<text class="axis" x="2" y="{t_ + 8}">{e(round(top))}</text><text class="axis" x="2" y="{h - bm}">0</text>{days}'
            f'<line class="goal" x1="{l}" x2="{w - 6}" y1="{gy:.1f}" y2="{gy:.1f}"><title>predicted {e(r["op"])} {e(v)}</title></line>'
            + "".join(f'<line class="goal" x1="{l + c["offset_h"] * 3600 / length * (w - l - 6):.1f}" x2="{l + c["offset_h"] * 3600 / length * (w - l - 6):.1f}" '
                      f'y1="{t_}" y2="{h - bm}"><title>{e(c["id"])} applied {e(c["at"][:16])}</title></line>' for c in rv.get("changes") or [])
            + f'<polyline class="plan" points="{pts(plan)}"><title>predicted path</title></polyline>'
            f'<polyline class="base" points="{pts(base, b0)}"><title>baseline week: {e(round(base[-1][1], 1))}</title></polyline>'
            f'<polyline class="obs" points="{pts(obs, r0)}"><title>review window so far: {e(round(obs[-1][1], 1))}</title></polyline>'
            f'</svg></figure>')


def _yes_rule_html(rv):
    y, bu = rv.get("yes_rule"), rv.get("budget") or {}
    if not y:
        return ""
    if not y.get("since"):
        return (f'<h2>Unit owner\'s yes <span class="sub">{e(y["diff"])}</span></h2>'
                '<p class="note">The rule is named in the delegation, but its diff has no recorded application yet.</p>')
    owner = rv["unit_owner"]
    prs = ("not imported yet" if y.get("prs_not_imported") else num(y["unit_prs_merged"]))
    rev = ("–" if y.get("prs_not_imported") else f'{num(y["reverts_of_yes_merges"])} <span class="sub">of {y["yes_prs_merged"]} named</span>')
    t = bu.get("budget")
    day = time.gmtime(of_model.epoch(rv["observed_until"]))
    import calendar
    pro = (day.tm_mday - 1 + day.tm_hour / 24) / calendar.monthrange(day.tm_year, day.tm_mon)[1]
    spend = (f'{bu["used"] / 1e6:.0f}M of {t / 1e6:.0f}M' if t and bu.get("used") is not None else "n/a")
    tiles = "".join([
        tile(f"Yes from {owner}", num(y["owner_yes"]), f'{y["classifier"]}; messages with its own yes, of {y["messages_to_keepers"]} to keepers'),
        tile("Standing yes", num(y["standing_yes"]), "permissions for future actions, counted apart"),
        tile("Requests from keepers", num(y["requests_from_keepers"]), "rq-v0, to the unit owner"),
        tile("Unit PRs merged", prs, f'under the rule; PRs imported until {y["prs_imported_until"][:16] if y.get("prs_imported_until") else "–"}'),
        tile("Reverts of yes merges", rev, f'within 14 days; all reverts in the unit: {"–" if y.get("prs_not_imported") else y["reverts_in_unit"]}'),
        tile("Unit spend this month", spend, (f'{pct(bu.get("share"))} of the tokens budget; {pct(round(pro, 3))} of the month gone'
                                              + (f'; ${bu["usd_used"]:,.0f} at list' if bu.get("usd_used") is not None else ""))),
    ])
    by = ", ".join(f"{k} {v}" for k, v in (y.get("owner_yes_by_keeper") or {}).items())
    return (f'<h2>Unit owner\'s yes <span class="sub">{e(y["diff"])}: inside the unit {e(owner)}\'s yes stands for yours on routine merges '
            f'and applies; since {e(y["since"][:16])} UTC</span></h2><section class="tiles">{tiles}</section>'
            + (f'<p class="legend">Yes by keeper: {e(by)}.</p>' if by else "")
            + '<p class="legend">uy-v1 (of_field-analyst\'s definition in the predictions file): a sentence of the unit owner\'s relayed '
              'message to a keeper with yes, approved, go ahead, is fine, ok to merge or "my (routine) yes", that also names a merge, '
              'apply, push or PR; a yes attributed to the founder is a relay, not counted; a standing permission is counted apart; '
              'one per message. Reverts: a PR a yes named, reverted within 14 days (Revert "&lt;title&gt;", or a revert naming its '
              'number). Reported, not judged. Spend is the canonical count of the unit\'s sessions (of_capacity), against '
              '<code>envelope.budget.tokens_month</code>; an escalation fires above 80%.</p>')


def view_review(d):
    full = getattr(d, "full", None)
    if full is None:
        return '<p class="note">Choose an organization above: a delegation is reviewed within its organization.</p>'
    recs = [u["delegation"] for u in (full.get("org_units") or {}).values() if u.get("delegation")]
    if getattr(d, "unit", None):
        recs = [r for r in recs if r.get("to_owner") == d.unit]
    out = []
    for rec in recs:
        rv = delegation_review(d, full, rec)
        if rv is None:
            out.append(f'<p class="note">Delegation <code>{e(rec.get("id"))}</code> has no predictions file '
                       f'(<code>reports/delegations/{e(rec.get("id"))}.yaml</code>).</p>')
            continue
        out.append(_review_html(rv))
    for path, doc in change_prediction_files(d):
        try:
            out.append(_change_html(change_review(d, path, doc)))
        except Exception as exc:  # noqa: BLE001 — one bad file never hides the page
            out.append(f'<p class="note">Prediction <code>{e(os.path.basename(path))}</code> could not be computed: {e(type(exc).__name__)}.</p>')
    return "".join(out) or '<p class="note">No delegation or change prediction in this organization.</p>'


# ------------------------------------------------------------------ change predictions (reports/predictions/*.yaml)

# Measures a change prediction may name, by `measure.name`; each gives {scope: {owner_messages, polls, share, ...}}.
CHANGE_MEASURES = ("status-poll",)


def change_prediction_files(d):
    """(path, doc) for each reports/predictions/*.yaml of the page's organization (of every one, on the all page)."""
    import glob
    import yaml
    cfgs = [o for o in orgs() if d.org in (ALL, None) or o["name"] == d.org]
    out = []
    for o in cfgs:
        for path in sorted(glob.glob(os.path.join(o.get("repo") or "", "reports", "predictions", "*.yaml"))):
            try:
                with open(path) as fh:
                    doc = yaml.safe_load(fh) or {}
            except (OSError, yaml.YAMLError):
                continue
            if isinstance(doc, dict) and doc.get("predictions") and doc.get("review_window"):
                out.append((path, doc))
    return out


def _scope_rows(name, a, b):
    """One organization's model at b and its rows for [a - 2 days, b), with corrected prompt origins."""
    import of_store
    all_orgs = orgs()
    o = next(x for x in all_orgs if x["name"] == name)
    doc, rev = of_model.registry_at(o["repo"], b)
    rows = of_model.load_events(of_store.connect(), since=of_model.iso(a - 2 * DAY), until=of_model.iso(b),
                                names=("of.human_prompt", "of.prompt_origin", "of.state_declared", "of.session_start"))
    rows = org_rows(rows, o, [x for x in all_orgs if x is not o])
    return of_model.build(doc, rows, at=b, org=name, rev=rev, window_h=7 * 24), rows


def poll_scopes(scopes, a, b):
    """Status polls (sp-v1) per declared scope over [a, b): orgs, and units excluded or kept (by unit id)."""
    out = {}
    cache = {}
    for sname, sc in scopes.items():
        tot = {"owner_messages": 0, "polls": 0, "instruction_with_poll": 0, "system_status": 0, "by_position": {}}
        for org in sc.get("orgs") or []:
            if org not in cache:
                cache[org] = _scope_rows(org, a, b)
            m, rows = cache[org]
            units = m.get("org_units") or {}
            def members(uid):
                u = units.get(uid) or {}
                return (set(of_model.unit_of(m, u["owner"])) if u.get("owner") else set()) | of_diagnose.unit_members(m, uid)[1]
            excl = set().union(*[members(u) for u in sc.get("exclude_units") or [] if u in units]) or None
            keep = set().union(*[members(u) for u in sc.get("only_units") or [] if u in units]) if sc.get("only_units") else None
            r = of_measures.status_polls(m, rows, a, b, positions=keep, exclude=excl)
            for k in ("owner_messages", "polls", "instruction_with_poll", "system_status"):
                tot[k] += r[k]
            for p, v in r["by_position"].items():
                tot["by_position"][p] = tot["by_position"].get(p, 0) + v["polls"]
        tot["share"] = round(tot["polls"] / tot["owner_messages"], 3) if tot["owner_messages"] else None
        tot["by_position"] = dict(sorted(tot["by_position"].items(), key=lambda kv: -kv[1]))
        out[sname] = tot
    return out


def _cached(name, key, now, fn):
    """fn()'s JSON result, kept in the state dir under `name` while `key` matches and for REVIEW_CACHE_S."""
    import json
    path = _review_cache_path(name)
    try:
        with open(path) as fh:
            c = json.load(fh)
        if c.get("key") == key and now - c.get("computed_at", 0) < REVIEW_CACHE_S:
            return c["value"]
    except (OSError, ValueError):
        pass
    value = fn()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + f".{os.getpid()}"
        with open(tmp, "w") as fh:
            json.dump({"key": key, "computed_at": now, "value": value}, fh)
        os.replace(tmp, path)
    except OSError:
        pass
    return value


def owner_view(r, excluded=()):
    """An of.studio_viewed the owner made: not one the record excludes by event id (with its reason), and not one
    whose link an agent requested (`requester: agent:<position>`, of-server; the same rule as polls_vs_studio)."""
    pl = r.get("payload") or {}
    return r.get("event_id") not in excluded and not str(pl.get("requester") or "").startswith("agent:")


def works_view_days(rows, a, b, excluded=()):
    """Distinct UTC days in [a, b) with an owner's of.studio_viewed {view: works} (of-server's /studio/ mount)."""
    return len({str(r["ts"])[:10] for r in rows if r.get("name") == "of.studio_viewed"
                and (r.get("payload") or {}).get("view") == "works" and owner_view(r, excluded)
                and a <= (of_model.epoch(r.get("ts")) or 0) < b})


def change_review(d, path, doc):
    """A change prediction against the same measure on the baseline and the review window (or, before it opens, the
    last 7 days for reference). The precondition decides 'not tested' before any verdict."""
    import of_store
    meas = doc.get("measure") if isinstance(doc.get("measure"), dict) else {"name": "status-poll", "version": of_measures.SP}
    if meas.get("name") not in CHANGE_MEASURES:
        return {"file": path, "doc": doc, "unsupported": meas.get("name")}
    scopes = doc.get("scopes")
    if not isinstance(scopes, dict) or not scopes:
        return {"file": path, "doc": doc, "no_scopes": True}   # the file names its scopes; Studio never guesses them
    bw, rw = (doc.get("baseline") or {}).get("window") or [], doc.get("review_window") or []
    ba, bb = of_model.epoch(bw[0]), of_model.epoch(bw[1])
    ra, rb = of_model.epoch(rw[0]), of_model.epoch(rw[1])
    now = d.now
    opened, ended = now >= ra, now >= rb
    wa, wb = (ra, min(now, rb)) if opened else (now - 7 * DAY, now)
    import hashlib
    with open(path, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()[:16]
    key = {"file": digest, "review": REVIEW_VERSION, "sp": of_measures.SP}
    base = _cached(f"change-{os.path.basename(path)[:-5]}-base", dict(key, w=bw), now, lambda: poll_scopes(scopes, ba, bb))
    cur = _cached(f"change-{os.path.basename(path)[:-5]}-now", dict(key, w=[wa // REVIEW_CACHE_S, wb // REVIEW_CACHE_S]), now,
                  lambda: poll_scopes(scopes, wa, wb))
    views = of_model.load_events(of_store.connect(), since=of_model.iso(min(ra, wa)), names=("of.studio_viewed",))
    pre = doc.get("precondition") or {}
    excluded = {str(x.get("event")) for x in doc.get("excluded_views") or [] if isinstance(x, dict) and x.get("event")}
    vdays = works_view_days(views, ra, min(now, rb), excluded) if opened else works_view_days(views, wa, wb, excluded)
    pre_ok = _verdict(pre.get("op"), vdays, pre.get("value")) if pre else True
    rows = []
    for p in doc.get("predictions") or []:
        m, op, v = p.get("metric"), p.get("op"), p.get("value")
        o = of_measures.dotted(cur, m)
        verdict = None
        if ended:
            verdict = "not tested" if not pre_ok else ("met" if _verdict(op, o, v) else "missed")
        rows.append({"metric": m, "op": op, "value": v, "baseline": of_measures.dotted(doc.get("baseline") or {}, m),
                     "baseline_ours": of_measures.dotted(base, m), "observed": o, "verdict": verdict, "why": p.get("why")})
    return {"file": path, "doc": doc, "measure": meas, "scopes": scopes,
            "baseline_window": bw, "review_window": rw, "opened": opened, "ended": ended,
            "window": [of_model.iso(wa), of_model.iso(wb)], "rows": rows, "base": base, "cur": cur,
            "precondition": pre, "view_days": vdays, "precondition_met": pre_ok, "excluded_views": doc.get("excluded_views") or [],
            "reported": doc.get("reported_not_predicted") or []}


def _change_html(cr):
    doc = cr["doc"]
    fx = lambda x: num(x, nd=3) if isinstance(x, float) else num(x)
    title = os.path.basename(cr["file"])[:-5]
    if cr.get("unsupported"):
        return f'<p class="note">Prediction <code>{e(title)}</code>: Studio does not compute the measure <code>{e(cr["unsupported"])}</code> yet.</p>'
    if cr.get("no_scopes"):
        return (f'<p class="note">Prediction <code>{e(title)}</code> declares no <code>scopes</code> '
                '({name: {orgs: [...], only_units | exclude_units: [...]}}): nothing to measure.</p>')
    rw = cr["review_window"]
    when = ("window ended" if cr["ended"] else (f'review window {e(rw[0][:16])} – {e(rw[1][:16])} UTC, open' if cr["opened"]
            else f'review window opens {e(rw[0][:16])} UTC; "now" is the last 7 days, for reference only'))
    trs = "".join(
        f'<tr><td><code>{e(r["metric"])}</code></td><td class="num">{fx(r["baseline"])}'
        f'{"" if r["baseline_ours"] == r["baseline"] else f"<div class=src>recomputed {fx(r["baseline_ours"])}</div>"}</td>'
        f'<td class="num">{e(r["op"])} {fx(r["value"])}</td><td class="num"><b>{fx(r["observed"])}</b></td>'
        f'<td>{e(r["verdict"] or ("–" if not cr["ended"] else ""))}</td><td class="why">{e(r["why"])}</td></tr>' for r in cr["rows"])
    pre = cr["precondition"]
    pre_line = (f'Precondition: <code>{e(pre.get("metric"))}</code> {e(pre.get("op"))} {e(pre.get("value"))}: '
                f'<b>{cr["view_days"]}</b> day(s) with the Works view opened '
                + ("in the review window" if cr["opened"] else "in the last 7 days")
                + ("" if cr["precondition_met"] else (" (not met: the verdict is <b>not tested</b>)" if cr["ended"] else " (not met so far)")) + "."
                + " Views count only through the one-time link (<code>of studio</code>, or <kbd>s</kbd> in the asks popup);"
                + " a link an agent requested does not count"
                + (f'; excluded by the record: {e(", ".join(str(x.get("event"))[:8] + " (" + str(x.get("why") or "") + ")" for x in cr.get("excluded_views") or []))}'
                   if cr.get("excluded_views") else "") + ".") if pre else ""
    rep = []
    for k, sc in cr["cur"].items():
        top = ", ".join(f"{p} {n}" for p, n in list(sc["by_position"].items())[:6]) or "–"
        b = cr["base"].get(k) or {}
        rep.append(f'<tr><td><code>{e(k)}</code></td><td class="num">{b.get("polls")} of {b.get("owner_messages")} ({fx(b.get("share"))})</td>'
                   f'<td class="num">{sc["polls"]} of {sc["owner_messages"]} ({fx(sc["share"])})</td>'
                   f'<td class="num">{sc["instruction_with_poll"]} · {sc["system_status"]}</td><td>{e(top)}</td></tr>')
    return f"""<h2 class="lead">Change prediction: {e(title)} <span class="sub">{when} · review {e(doc.get("review"))}</span></h2>
<p>{e(" ".join(str(doc.get("change") or "").split()))}</p>
<div class="scroll"><table class="review"><thead><tr><th>Prediction</th><th>Baseline week</th><th>Predicted</th><th>Now</th><th>Verdict</th><th>Why predicted</th></tr></thead><tbody>{trs}</tbody></table></div>
<p class="legend">{pre_line}</p>
<h2>By scope <span class="sub">{e(cr["measure"].get("name"))} {e(cr["measure"].get("version") or "")}: polls of owner messages (share); instruction with a poll · system status; top positions now</span></h2>
<div class="scroll"><table><thead><tr><th>Scope</th><th>Baseline week</th><th>Now</th><th>Reported</th><th>By position, now</th></tr></thead><tbody>{"".join(rep)}</tbody></table></div>
<p class="legend">Predictions: <code>{e(os.path.relpath(cr["file"], os.path.dirname(os.path.dirname(os.path.dirname(cr["file"])))))}</code> ({e(doc.get("measured_by"))}).</p>"""


def message_split(rv):
    """The review window's founder messages into the unit, split two ways (of_architect, for the platform review):
    by position (the unit owner, the intended shift, against its keepers) and by topic (the incident, inc-v0, against
    everything else), each with its weekly rate beside the prediction. None before there are messages."""
    sp = ((rv.get("now") or {}).get("founder") or {}).get("founder_messages_split")
    row = next((r for r in rv["rows"] if r["metric"] == "founder.founder_messages_in_unit"), None)
    if not sp or row is None or not rv.get("elapsed_h"):
        return None
    wk = lambda n: round(n * 168 / rv["elapsed_h"]) if not rv["ended"] else n
    parts = {k: {"n": sp[k], "week": wk(sp[k])} for k in ("to_unit_owner", "to_keepers", "incident", "other")}
    return {"parts": parts, "prediction": row["value"], "op": row["op"], "rule": sp.get("rule"),
            "popup_answers": sp.get("popup_answers"), "elapsed_h": rv["elapsed_h"], "ended": rv["ended"],
            "other_above": row["value"] is not None and row["op"] == "<=" and parts["other"]["week"] > row["value"]}


def _parts_html(r):
    if not r.get("parts") or r["observed"] is None:
        return ""
    return "".join(f'<div class="src">before <code>{e(p["id"])}</code>: {num(p["before"])} · after: '
                   f'{num(round(r["observed"] - (p["before"] or 0), 1))}</div>' for p in r["parts"])


def _tags_html(rv, r):
    """Beside the founder-side verdicts: the unit's asks to the founder by the asker's declared tag."""
    if r["metric"] != "founder.asks":
        return ""
    f = (rv.get("now") or {}).get("founder") or {}
    tags = f.get("escalation_tag") or {}
    if not tags:
        return ""
    fixed = f.get("escalation_tag_corrected") or []
    out = (f'<div class="src">by the asker&#x27;s tag: {e(" · ".join(f"{k} {v}" for k, v in tags.items()))}'
           + (f' ({len(fixed)} corrected after filing: {e(", ".join(fixed))})' if fixed else "") + "</div>"
           + (f'<div class="src">blocking screens, reported apart (not counted: the baseline could not record them): '
              f'{f["blocked_asks"]}</div>' if f.get("blocked_asks") else ""))
    for c in rv.get("changes") or []:
        before = ((c["point"].get("founder") or {}).get("escalation_tag") or {}).get("untagged", 0)
        out += (f'<div class="src">untagged before <code>{e(c["id"])}</code> ({e(c["at"][11:16])}Z): {before} · after: '
                f'{tags.get("untagged", 0) - before}</div>')
    return out


def _split_html(rv, r):
    if r["metric"] != "founder.founder_messages_in_unit":
        return ""
    x = message_split(rv)
    if not x:
        return ""
    p = x["parts"]
    rate = "a week at this rate" if not x["ended"] else "in the week"
    cell = lambda k, label: f'{label}{p[k]["n"]} <span class="sub">({p[k]["week"]} {rate})</span>'
    verdict = (f'Not about the incident: {p["other"]["week"]} {rate}, '
               + ("still above" if x["other_above"] else "within") + f' the prediction {x["op"]} {x["prediction"]}.')
    return (f'<div class="src">to {e(rv["unit_owner"])}: {cell("to_unit_owner", "")} · to keepers: {cell("to_keepers", "")}</div>'
            f'<div class="src">incident (<code>{e(x["rule"])}</code>): {cell("incident", "")} · other: {cell("other", "")}</div>'
            f'<div class="src"><b>{e(verdict)}</b> {x["popup_answers"]} of them are answers in the popup.</div>')


def _review_html(rv):
    day = min(7, int(rv["elapsed_h"] // 24) + 1)
    sub = (f'unit {e(rv["unit"])} → {e(rv["unit_owner"])} · review {e(rv["review"])} · '
           + ("window ended" if rv["ended"] else f'day {day} of 7 ({e(rv["elapsed_h"])} h of {e(round(rv["length_h"]))})')
           + f' · observed until {e(rv["observed_until"][:16])} UTC')
    def count(pred):
        return sum(1 for r in rv["rows"] if pred(r))
    wk = "" if rv["ended"] else " for the week"
    parts = [(count(lambda r: r["on_track"] is True and r.get("decided")), "met" + wk),
             (count(lambda r: r["on_track"] is False and r.get("decided")), "missed" + wk),
             (count(lambda r: r["on_track"] is True and not r.get("decided")), "on pace"),
             (count(lambda r: r["on_track"] is False and not r.get("decided")), "off pace"),
             (count(lambda r: r["on_track"] == "not_judged"), "not judged yet"),
             (count(lambda r: r["on_track"] == "unmeasurable"), "unmeasurable yet")]
    head = [f'{len(rv["rows"])} predictions: ' + ", ".join(f"{n} {w}" for n, w in parts if n) + "."]
    f, o, owner = rv["now"]["founder"], rv["now"]["unit_owner_side"], rv["unit_owner"]
    ebp = f.get("escalated_by_position") or {}
    esc = f.get("escalated_copies", 0)
    own = (f.get("by_position") or {}).get(owner, 0) - ebp.get(owner, 0)
    if f.get("asks"):
        head.append(f'Of the {f["asks"]} asks to you from the unit, {own} are {e(owner)}\'s own, {esc} are escalations '
                    f'({ebp.get(owner, 0)} on the unit\'s conditions, {esc - ebp.get(owner, 0)} of keepers\' asks), and '
                    f'{f["asks"] - own - esc} came directly from keepers. {e(owner)} received {o.get("asks", 0)} '
                    f'from the keepers and answered {o.get("answered", 0)} itself'
                    + (f'; {o.get("closed_otherwise")} closed another way (approved in the keeper\'s pane, escalated, withdrawn)'
                       if o.get("closed_otherwise") else "") + ".")
    if rv.get("hold"):
        head.append(f'Baseline on hold: {e(rv["hold"] if isinstance(rv["hold"], str) else "being re-measured")}; nothing is judged until it is restored.')
    fv = lambda r, x: pct(x) if "share" in r["metric"] and isinstance(x, (int, float)) else num(x)
    trs = []
    for r in rv["rows"]:
        w, cls = _pace_word(r, rv)
        trs.append(f'<tr><td>{e(r["label"])}<div class="src"><code>{e(r["metric"])}</code></div></td>'
                   f'<td class="num">{fv(r, r["baseline"])}{"" if r["baseline_ours"] == r["baseline"] else f"<div class=src>recomputed {fv(r, r["baseline_ours"])}</div>"}</td>'
                   f'<td class="num">{e(r["op"])} {fv(r, r["value"])}</td>'
                   f'<td class="num">{num(r["baseline_same_point"]) if r["additive"] else "–"}</td>'
                   f'<td class="num">{(e(r["op"]) + " " + num(r["expected_now"])) if r["additive"] and not rv["ended"] else "–"}</td>'
                   f'<td class="num"><b>{fv(r, r["observed"])}</b></td><td class="pace {cls}">{e(w)}'
                   f'{f"<div class=src>{e(r["unmeasurable"])}</div>" if r.get("unmeasurable") else ""}'
                   f'{f"<div class=src>shape: the baseline week&#x27;s <code>{e(r["shape"])}</code></div>" if r.get("shape") else ""}'
                   f'{_split_html(rv, r)}{_parts_html(r)}{_tags_html(rv, r)}</td>'
                   f'<td class="why">{e(r["why"])}{f"<div class=src>restated: {e(r["restated"])}</div>" if r.get("restated") else ""}'
                   f'{f"<div class=src>definition {e(r["definition"])}</div>" if r.get("definition") else ""}</td></tr>')
    charts = "".join(_review_chart(r, rv) for r in rv["rows"] if r["additive"] and r["value"] is not None)
    base_pos, now_pos = rv["baseline_end"]["founder"].get("by_position") or {}, f.get("by_position") or {}
    to_owner = o.get("by_position") or {}
    names = sorted(set(base_pos) | set(now_pos) | set(to_owner), key=lambda n: (-now_pos.get(n, 0), -to_owner.get(n, 0), -base_pos.get(n, 0), n))
    who = "".join(f'<tr><td>{e(n)}{" <span class=sub>unit owner</span>" if n == owner else ""}</td><td class="num">{base_pos.get(n, 0)}</td>'
                  f'<td class="num">{now_pos.get(n, 0)}</td><td class="num">{to_owner.get(n, 0)}</td></tr>' for n in names)
    def rv_cell(r, x):
        if isinstance(x, dict):
            return e(" · ".join(f"{k} {v}" for k, v in x.items() if k != "version")) + (f' <span class="src">{e(x["version"])}</span>' if x.get("version") else "")
        if x is None and r["metric"].endswith(".escalation_tag"):
            return '<span class="na">– (declared tags exist from 2026-11-06)</span>'
        if x is None and r["metric"].startswith("unit_owner_yes."):
            return '<span class="na">– (the rule did not exist)</span>'
        return fv(r, x) if x is not None else '<span class="na">not computed in Studio</span>'
    rep = "".join(f'<tr><td>{e(r["label"])}<div class="src"><code>{e(r["metric"])}</code></div></td><td>{rv_cell(r, r["baseline"])}</td>'
                  f'<td>{rv_cell(r, r["observed"])}</td></tr>' for r in rv["reported"])
    cav = "".join(f"<li>{e(' '.join(str(c).split()))}</li>" for c in rv["caveats"])
    return f"""<h2 class="lead">Delegation review: {e(rv["delegation"])} <span class="sub">{sub}</span></h2>
<p class="headline">{" ".join(head)}</p>
<div class="scroll"><table class="review"><thead><tr><th>Prediction</th><th>Baseline week</th><th>Predicted for the week</th>
<th>Baseline at this point</th><th>Expected by now</th><th>Now</th><th>Pace</th><th>Why predicted</th></tr></thead><tbody>{"".join(trs)}</tbody></table></div>
<p class="legend">Expected by now = the prediction × the share of the baseline week's total reached at the same point of its week
(same weekday and hour); for a baseline of 0, the shape of the unit's asks to you in the baseline week. A growing measure
already past its value is settled for the week (missed for "&lt;=", met for "&gt;="). Shares and delays are compared as they stand.
At the end of the window, the verdict is met or missed against the prediction itself.</p>
<div class="sparks">{charts}</div>
<p class="legend"><span class="sw k-base"></span>baseline week <span class="sw k-plan"></span>predicted path
<span class="sw k-obs"></span>review window so far <span class="sw k-goal"></span>predicted value · x: days into the window</p>
{_yes_rule_html(rv)}
<h2>Who asks <span class="sub">asks by asking position: to you in the baseline week, to you and to the unit owner since the delegation</span></h2>
<table><thead><tr><th>Position</th><th>To you, baseline week</th><th>To you, now</th><th>To {e(owner)}, now</th></tr></thead><tbody>{who}</tbody></table>
{f'<h2>Reported, not predicted</h2><table><thead><tr><th>Measure</th><th>Baseline week</th><th>Now</th></tr></thead><tbody>{rep}</tbody></table>' if rep else ""}
{f'<details><summary>Caveats from the predictions file ({len(rv["caveats"])})</summary><ul>{cav}</ul></details>' if cav else ""}
<p class="legend">Predictions: <code>{e(rv["file"])}</code> in the organization's repository ({e(rv["measured_by"])}).
Baseline window {e(rv["baseline_window"][0][:16])} – {e(rv["baseline_window"][1][:16])}, recomputed by the same code as the review window.</p>"""


STATE_CLASS = {"waiting": "off", "active": "ok", "quiet": "na", "proposed": "na", "parked": "na", "done": "na"}


def works_of(d):
    """The works rows for the page's organization (of_works v0, derived), restricted for a unit viewer."""
    import of_works
    org = getattr(d, "_org_cfg", None)
    if org is None or getattr(d, "full", None) is None:
        return None
    have = {r.get("event_id") for r in d.rows if r.get("name") == "of.pr_state"}
    rows = d.rows + [r for r in d.pr_state_rows() if r.get("event_id") not in have]  # an open PR quiet for weeks still shows
    ws = of_works.rows_for(d.full, rows, org, d.now, d.attention if not d.unit else
                           of_measures.attention(d.full, d.rows, d.now), d.diagnosis["issues"])
    if d.unit:
        names = set(d.model["positions"])
        ws = [w for w in ws if set(w["doers"]) & names]
    return ws


def _wait_text(x, now):
    import of_works
    on = x.get("on")
    who = "you" if x["kind"] == "waiting_owner" and on in (of_model.HUMAN, None, "owner") else (on or "?")
    return f'{x["position"]} → {who} · {of_works.age(of_model.epoch(x.get("since")), now) or "?"}'


def _work_row(w, d):
    import of_works
    st = w["state"]
    doc = w.get("doc") or {}
    stage = w.get("status_line") or w.get("done_when") or ""   # a registry work's status_line is its `why`
    if w.get("stage_summary"):
        stage = f'{w["stage_summary"]} — {stage}' if stage else w["stage_summary"]
    if w.get("standing"):   # no final end: the loop target's trend by checkpoint, never stage progress
        stage = w.get("trend_summary") or "no measured checkpoint yet"
    waits = "<br>".join(e(_wait_text(x, d.now)) for x in w["waits"][:3]) or '<span class="na">–</span>'
    last = of_works.age(w["last_progress"], d.now)
    return (f'<tr><td><a href="{e(link("works", d.org, w=w["id"]))}">{e(w["title"])}</a>'
            f'<div class="src">{e(w["kind"])} · <code>{e(w["id"])}</code>{" · " + e(doc.get("repo")) if doc.get("repo") else ""}</div></td>'
            f'<td class="pace {STATE_CLASS.get(st, "na")}">{e(st)}{" · standing" if w.get("standing") else ""}'
            f'{" · past review" if w["past_review"] else ""}</td>'
            f'<td class="why">{e(stage[:140] + ("…" if len(stage) > 140 else ""))}</td>'
            f'<td>{e(", ".join(w["doers"])) or "<span class=na>nobody</span>"}</td><td class="src">{waits}</td>'
            f'<td class="age">{e(last) + " ago" if last else "–"}</td><td class="num">{w["progress_7d"]}</td>'
            f'<td class="num">{len(w["issues"]) or ""}</td><td class="age">{e(w.get("review") or "")}</td></tr>')


WORKS_HEAD = ('<thead><tr><th>Work</th><th>State</th><th>Stage / end</th><th>Doers</th><th>Waiting on</th><th>Last progress</th>'
              '<th>7 d</th><th>Issues</th><th>Review</th></tr></thead>')


def view_works(d, wid=None):
    import of_works
    ws = works_of(d)
    if ws is None:
        return '<p class="note">Choose an organization above: works are listed per organization.</p>'
    if wid:
        w = next((x for x in ws if x["id"] == wid), None)
        return _work_card(d, w) if w else f'<p class="note">No work <code>{e(wid)}</code> in this organization.</p>'
    on_you = [w for w in ws if of_works.waits_on_owner(w)]
    past = [w for w in ws if w["past_review"] and w not in on_you]
    live = [w for w in ws if w not in on_you and w not in past and (w["doers"] or w["state"] in ("active", "waiting"))
            and w["state"] not in ("done", "parked")]
    rest = [w for w in ws if w not in on_you and w not in past and w not in live]
    groups = collections.OrderedDict()
    for w in sorted(live, key=lambda w: (of_works.group_of(d.full, w), of_works.ORDER.get(w["state"], 9), -(w["last_progress"] or 0))):
        groups.setdefault(of_works.group_of(d.full, w), []).append(w)
    counts = collections.Counter(w["state"] for w in ws)
    tiles = "".join(tile(lbl, num(n)) for lbl, n in (("Works", len(ws)), ("Waiting on you", len(on_you)), ("Active", counts["active"]),
                                                    ("Waiting", counts["waiting"]), ("Quiet", counts["quiet"]), ("Past review", len(past))))
    out = [f'<section class="tiles">{tiles}</section>',
           _works_source_note(ws)]
    loops = [(w, c) for w in ws for c in [d.loop_of(w)] if c]
    if loops:
        out.append(_loops_table(d, loops))
    if on_you:
        out.append(f'<h2 class="lead crit">Waiting on you</h2><div class="scroll"><table class="works">{WORKS_HEAD}<tbody>'
                   + "".join(_work_row(w, d) for w in on_you) + "</tbody></table></div>")
    if past:
        out.append(f'<h2 class="lead">Past review, no progress since <span class="sub">extend, close or park</span></h2>'
                   f'<div class="scroll"><table class="works">{WORKS_HEAD}<tbody>' + "".join(_work_row(w, d) for w in past) + "</tbody></table></div>")
    for (unit, sysid), items in groups.items():
        label = (f"unit {unit} · " if unit else "") + (sysid or "no system")
        out.append(f'<h2>{e(label)} <span class="sub">{len(items)} work{"s" if len(items) != 1 else ""}</span></h2>'
                   f'<div class="scroll"><table class="works">{WORKS_HEAD}<tbody>' + "".join(_work_row(w, d) for w in items) + "</tbody></table></div>")
    if rest:
        by = collections.Counter(w["state"] for w in rest)
        out.append(f'<details><summary>Other works: {len(rest)} ({", ".join(f"{n} {k}" for k, n in by.most_common())}), '
                   f'RFCs nobody holds and finished projects</summary><div class="scroll"><table class="works">{WORKS_HEAD}<tbody>'
                   + "".join(_work_row(w, d) for w in rest) + "</tbody></table></div></details>")
    return empty_note(d) + "".join(out)


def _loops_table(d, loops):
    import of_loops
    trs = []
    for w, c in loops:
        if not c:
            continue
        t = c["spec"]["target"]
        marks = []
        if c["drift"]:
            marks.append(f'<span class="flag crit">measures {e(", ".join(c["drift"]))}, not the target</span>')
        if c["checkpoint"]["overdue"]:
            marks.append('<span class="flag">production checkpoint overdue</span>')
        if c["plateau"] and c["plateau"]["flat"]:
            marks.append('<span class="flag">plateau</span>')
        if c["wip"]["over"]:
            marks.append('<span class="flag">over the WIP limit</span>')
        trs.append(f'<tr><td><a href="{e(link("works", d.org, w=w["id"]))}">{e(w["title"])}</a>'
                   f'<div class="src"><code>{e(w["id"])}</code> · owner {e(w.get("owner") or "–")}</div></td>'
                   f'<td>{e(t.get("metric") or "no target declared")}<div class="src">{e(t.get("stake") or "")}</div></td>'
                   f'<td>{e(of_loops.summary(c, d.now) or "")} {"".join(marks)}</td></tr>')
    if not trs:
        return ""
    return ('<h2 class="lead">Development loops <span class="sub">one row per work with a loop (RFC 0005); every number is derived '
            'from the loop\'s events, none is reported</span></h2><div class="scroll"><table><thead><tr><th>Work</th><th>Target</th>'
            f'<th>Where it stands</th></tr></thead><tbody>{"".join(trs)}</tbody></table></div>')


def _loop_card(d, w):
    """RFC 0005 §7: the target and the money metric, the ladder, the register, the curve, the cost, the decision log."""
    import of_loops
    c = d.loop_of(w) if hasattr(d, "loop_of") else None
    if not c:
        return ""
    st = of_loops.stamp
    spec, t = c["spec"], c["spec"]["target"]
    h = lambda x: "–" if x is None else f"{x:g}"
    drift = (f'<p class="crit"><b>The latest runs measure {e(", ".join(c["drift"]))}, not the target</b>, and the loop\'s '
             '<code>measures</code> give it no price in money (objective drift, RFC 0005 §6).</p>') if c["drift"] else ""
    facts = [("Target", t.get("metric") or "not declared: the work's loop: in the registry names it"),
             ("Money", t.get("money") or "–"), ("Stake", t.get("stake") or "–"), ("Class", t.get("throughput_class") or "–"),
             ("Started", st(c["start"])), ("Events", str(c["events"]))]
    seg = t.get("segment")
    if seg:   # docs/registry.md: the segment the organization controls, and the parts outside it reported beside it
        facts[1:1] = [("Segment", f'{seg.get("start") or "?"} → {seg.get("end") or "?"}'),
                      ("Context", "; ".join(of_loops.context_label(x) for x in seg["context"])
                       + " (outside the segment: context, never a lever)" if seg["context"] else "–")]
    if not c["declared"]:
        facts.append(("Declared", "no loop: in the registry yet; the events show alone"))
    hi = c["highest"]
    rows = []
    for x in c["ladder"]:
        on = ' class="on"' if hi and x["rung"] == hi["rung"] else ""
        rows.append(f'<tr{on}><td><b>{e(x["rung"])}</b>{" · production" if x["production"] else ""}'
                    f'{"" if x["declared"] else " · <span class=na>not on the ladder</span>"}</td>'
                    f'<td>{e(x["cost"] or "–")}</td><td>{e(x["latency"] or "–")}</td><td class="why">{e(x["accept"] or "–")}</td>'
                    f'<td>{x["runs_week"]} <span class="sub">({x["runs"]} in all)</span></td><td>{h(x["cycle_time_h"])}</td>'
                    f'<td>{"–" if x["wait_share"] is None else str(round(100 * x["wait_share"])) + "%"}</td>'
                    f'<td class="why">{e(str(x["last_result"]) if x["last_result"] is not None else "–")}'
                    f'<div class="src">{e(st(x["last_at"])) if x["last_at"] else ""}</div></td></tr>')
    ck = c["checkpoint"]
    ck_line = ("No production rung on the ladder." if not spec["production"] else
               (f'Last production checkpoint {e(st(ck["last"]))} UTC, {ck["days_since"]:g} d ago' if ck["last"] else
                f'No production checkpoint in the {ck["days_since"]:g} d since the loop started') +
               f' (due every {ck["due_days"]:g} d{": overdue" if ck["overdue"] else ""}).')
    ttfm = c["time_to_first_money_days"]
    reg = c["register"]
    hyp_row = lambda x: (f'<tr><td><code>{e(x["id"])}</code></td><td class="why">{e(x.get("change") or "–")}</td>'
                         f'<td class="why">{e(x.get("predict") or "–")}</td><td>{e(x.get("cost_rung") or "–")}'
                         f'{" · " + h(x.get("cost_runner_h")) + " runner-h" if x.get("cost_runner_h") is not None else ""}</td>'
                         f'<td>{e(x.get("voi") or "–")}</td><td>{e(x.get("status"))}</td></tr>')
    reg_rows = "".join(hyp_row(x) for x in reg["testing"] + reg["open"])
    done_rows = "".join(hyp_row(x) for x in reg["last_kept"] + reg["last_dropped"])
    cw = c["cost_week"]
    decisions = "".join(f'<li>{e(st(x.get("ts")))} <b>{e(x.get("decision") or "?")}</b>'
                        f'{" " + e(x["hypothesis"]) if x.get("hypothesis") else ""}{" → " + e(x["rung"]) if x.get("rung") else ""}'
                        f'{": " + e(x["metric"]) if x.get("metric") else ""}{" · " + e(str(x["note"])[:200]) if x.get("note") else ""}</li>'
                        for x in c["decisions"])
    rate = "–" if c["hit_rate"] is None else f'{round(100 * c["hit_rate"])}% ({c["kept"]} of {c["decided"]} decided)'
    return f"""<h2 class="lead">Development loop <span class="sub">RFC 0005 §7; derived from the loop's events, never reported</span></h2>
{drift}<table class="facts"><tbody>{"".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in facts)}</tbody></table>
<h3>Ladder <span class="sub">cheapest first; the highest rung reached is marked{": " + e(hi["rung"]) + " since " + e(st(hi["at"])) if hi else ""}</span></h3>
<div class="scroll"><table><thead><tr><th>Rung</th><th>Cost</th><th>Latency</th><th>Accept</th><th>Runs this week</th><th>Cycle time, h</th>
<th>Wait share</th><th>Last result</th></tr></thead><tbody>{"".join(rows) or '<tr><td colspan="8" class="quiet">No ladder and no runs.</td></tr>'}</tbody></table></div>
<p class="legend">{ck_line} Time to first money: {"not yet" if ttfm is None else f"{ttfm:g} d"}. Hit rate {e(rate)}.
Gain per cycle {h(c["gain_per_cycle"])}, per kept cycle {h(c["gain_per_kept_cycle"])} (target units).
WIP: {c["wip"]["testing"]} in test, limit {c["wip"]["limit"]}{" (over)" if c["wip"]["over"] else ""}.</p>
{_checkpoints_html(c) if w.get("standing") else ""}<h3>The target per cycle</h3>{_loop_curve(c)}
<h3>Register <span class="sub">in test, then open hypotheses by value of information per runner-hour</span></h3>
{f'<div class="scroll"><table><thead><tr><th>Id</th><th>Change</th><th>Predicted</th><th>Cost</th><th>VoI</th><th>Status</th></tr></thead><tbody>{reg_rows}</tbody></table></div>' if reg_rows else '<p class="quiet">No open hypothesis.</p>'}
{f'<details><summary>Last kept and dropped</summary><table><tbody>{done_rows}</tbody></table></details>' if done_rows else ""}
<h3>Cost this week</h3><p>{"no cost recorded yet" if cw["usd"] is None else f'${cw["usd"]:,.2f}'}{f' ({cw["runs_without_usd"]} run{"s" if cw["runs_without_usd"] != 1 else ""} without a cost yet: of_capacity prices a run an hour after it ends, and a run without a start stays unknown)' if cw["runs_without_usd"] else ""}
 · {h(cw["runner_h"])} runner-h · {"–" if cw["tokens"] is None else f'{cw["tokens"]:,.0f} fresh tokens'}
 · per kept cycle {"–" if cw["usd_per_kept_cycle"] is None else f'${cw["usd_per_kept_cycle"]:,.2f}'} and {h(cw["runner_h_per_kept_cycle"])} runner-h</p>
<h3>Decision log</h3>{f"<ul>{decisions}</ul>" if decisions else '<p class="quiet">No decision recorded.</p>'}"""


def _standing_review(w, d):
    """A standing work's review: the next checkpoint, due checkpoint_days after its last loop decision."""
    import of_works
    c = d.loop_of(w) if hasattr(d, "loop_of") else None
    days = (c or {}).get("checkpoint_days")
    last = w.get("last_decision")
    parts = [w.get("review") or "–"]
    if days:
        parts.append(f"a checkpoint every {days:g} d; " + (f"last loop decision {of_works.age(last, d.now)} ago" if last
                                                           else "no loop decision yet"))
    return " · ".join(parts)


def _checkpoints_html(c):
    """The target by checkpoint (a standing work): the best on-target value per checkpoint_days period."""
    import of_loops
    tr = c.get("trend") or {}
    cps = tr.get("checkpoints") or []
    if not cps:
        return ""
    st = of_loops.stamp
    val = lambda v: "–" if v is None else f"{v:g}"
    rows = "".join(f'<tr><td class="age">{e(st(x["from"]))} – {e(st(x["to"]))}</td>'
                   f'<td class="num">{val(x["best"])}</td><td class="num">{x["runs"]}</td></tr>' for x in cps[-8:])
    better = "lower" if (c.get("target") or {}).get("better") == "lower" else "higher"
    ch = ("" if tr.get("pct") is None else
          f' Last checkpoint against the one before: {"+" if tr["pct"] >= 0 else ""}{round(100 * tr["pct"], 1)}% towards better.')
    return (f'<h3>The target by checkpoint <span class="sub">a standing work: the best on-target run in each '
            f'{c["checkpoint_days"]:g}-day period ({better} is better)</span></h3>'
            f'<table><thead><tr><th>Period (UTC)</th><th>Best</th><th>Runs</th></tr></thead><tbody>{rows}</tbody></table>'
            f'<p class="legend">{e(ch.strip()) if ch else "Fewer than two measured checkpoints yet."}</p>')


def _loop_curve(c, w=560, hgt=120):
    pts = c["curve"]
    if len(pts) < 2:
        return '<p class="quiet">Fewer than two runs measure the target yet.</p>'
    t0, t1 = pts[0][0], pts[-1][0]
    v0, v1 = min(p[1] for p in pts), max(p[1] for p in pts)
    sx = lambda t: 30 + (w - 40) * ((t - t0) / ((t1 - t0) or 1))
    sy = lambda v: hgt - 18 - (hgt - 30) * ((v - v0) / ((v1 - v0) or 1))
    line = " ".join(f"{sx(p[0]):.1f},{sy(p[1]):.1f}" for p in pts)
    pl = c["plateau"]
    band = (f'<rect x="{sx(pl["from"]) - 4:.1f}" y="4" width="{sx(pl["to"]) - sx(pl["from"]) + 8:.1f}" height="{hgt - 20}" '
            f'class="{"plateau" if pl["flat"] else "window"}" fill="currentColor" fill-opacity="0.08"/>') if pl else ""
    dots = "".join(f'<circle cx="{sx(p[0]):.1f}" cy="{sy(p[1]):.1f}" r="3"><title>{e(p[2])} {p[1]:g} · {e(p[3] or "")}</title></circle>'
                   for p in pts)
    label = (f'<text x="{w - 10}" y="14" text-anchor="end" font-size="11">plateau window: '
             f'{"flat, a step back is due" if pl["flat"] else "moving"}</text>') if pl else ""
    return (f'<svg viewBox="0 0 {w} {hgt}" width="{w}" height="{hgt}" role="img" aria-label="the target per cycle">{band}'
            f'<polyline points="{line}" fill="none" stroke="currentColor" stroke-width="1.5"/>{dots}{label}'
            f'<text x="2" y="{sy(v1) + 4:.1f}" font-size="11">{v1:g}</text><text x="2" y="{sy(v0) + 4:.1f}" font-size="11">{v0:g}</text></svg>')


# ------------------------------------------------------------------ goals (docs/registry.md, Goals)

LEVEL = re.compile(r"^\s*(<=|>=|<|>|==|=)\s*(-?\d+(?:\.\d+)?)\s*%?\s*$")


def metric_reading(d, metric):
    """A goal's or a condition's `metric` read as a Studio measure: a dotted name in the owner's measures for the page's
    window (of_measures.owner, e.g. answer_delay_min.p50), or None when it names none (most are prose today)."""
    if not isinstance(metric, str) or not re.fullmatch(r"[a-z_][a-z0-9_]*(\.[a-z0-9_]+)*", metric):
        return None
    v = of_measures.dotted(getattr(d, "owner", None) or {}, metric)
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def level_breached(level, value):
    """True or False when the level is a comparison ('<= 2', '>= 99.5%') and there is a reading; else None. A level in
    percent read against a share (Studio's shares run from 0 to 1) compares the share as a percent: 0.97 is 97%."""
    m = LEVEL.match(str(level or ""))
    if not m or value is None:
        return None
    op, x = m.group(1), float(m.group(2))
    if "%" in str(level) and 0 <= value <= 1:
        value *= 100
    ok = {"<=": value <= x, ">=": value >= x, "<": value < x, ">": value > x, "==": value == x, "=": value == x}[op]
    return not ok


def _names(xs, n=6):
    xs = list(xs)
    return e(", ".join(xs[:n])) + (f' <span class="sub">+{len(xs) - n} more</span>' if len(xs) > n else "") if xs else '<span class="na">nobody</span>'


def goal_spend(d):
    """This month's spend per goal and condition of the page's organization, as a reading (docs/registry.md, Goals,
    "Spend per goal"): {month, computed_at, complete, goals: {id: total}, conditions: {id: total}}, each total
    {fresh, cost_usd, responses} from of_spend.load(), the sums the listener writes (of_capacity). No budget, no share
    and no forecast. None when no sums are written, they are of another month than the page's, or of_spend is absent."""
    try:
        import of_spend
    except ImportError:   # of-server's image carries no of_spend
        return None
    out = of_spend.load()
    month = time.strftime("%Y-%m", time.gmtime(d.now))
    if not out or out.get("month") != month:
        return None
    o = (out.get("orgs") or {}).get(d.org) or {}
    pick = lambda m: {k: x.get("total") for k, x in (m or {}).items() if isinstance(x, dict) and x.get("total")}
    return {"month": month, "computed_at": out.get("computed_at"), "complete": out.get("complete", True),
            "goals": pick(o.get("goals")), "conditions": pick(o.get("conditions"))}


def tokens(n):
    n = n or 0
    return f"{n / 1e9:.2f}B" if n >= 1e9 else f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}k" if n >= 1e3 else str(int(n))


def _spend_cell(sp, kind, nid):
    if sp is None:
        return '<td class="num"><span class="na">–</span></td>'
    t = (sp.get(kind) or {}).get(nid)
    if not t or not t.get("responses"):
        return '<td class="num">$0</td>'
    return (f'<td class="num">${t["cost_usd"]:,.0f}<div class="src">{tokens(t["fresh"])} fresh tokens</div></td>')


def _rollup_cells(r, now):
    import of_works
    asks = (f'{r["asks"]}' + (f' <span class="flag">{r["on_owner"]} on an owner</span>' if r["on_owner"] else "")) if r["asks"] else "–"
    last = of_works.age(r["last_progress"], now)
    states = " · ".join(f"{n} {k}" for k, n in sorted(r["states"].items(), key=lambda kv: of_works.ORDER.get(kv[0], 9)))
    prog = (f'{r["progress_7d"]} progress marks in 7 d' + (f' · last {e(last)} ago' if last else "")
            + (f'<div class="src">{e(states)}{" · " + str(r["past_review"]) + " past review" if r["past_review"] else ""}</div>' if states else ""))
    return (f'<td class="num">{r["open"]}<span class="sub">of {r["works"]}</span></td><td>{_names(r["doers"])}</td>'
            f'<td>{asks}</td><td>{prog if r["works"] else "<span class=na>no work yet</span>"}</td>')


def _goal_rows(d, n, out):
    import of_works
    reading = metric_reading(d, n.get("metric"))
    measure = [e(n.get("measure") or "no measure: every goal needs one")]
    if n.get("target"):
        measure.append(f'target {e(n["target"])}')
    elif n["depth"]:
        measure.append('<span class="flag">no target: a sub-goal needs one</span>')
    if n.get("by"):
        measure.append(f'by {e(n["by"])}')
    if n.get("metric"):
        measure.append(f'<span class="src">{e(n["metric"])}{": now " + num(reading) if reading is not None else ""}</span>')
    head = (f'{str(n["rank"]) + ". " if n["rank"] is not None else ""}<b>{e(n["title"])}</b>'
            f'<div class="src"><code>{e(n["id"])}</code>{"" if n["state"] == "active" else " · " + e(n["state"])}'
            f'{" · decided " + e(n["decided"]) if n.get("decided") else " · <span class=flag>no decided ask id</span>"}</div>'
            + (f'<div class="src">drives: {e(n["drives"])}</div>' if n.get("drives") else "")
            + "".join(f'<div><span class="flag">{e(x)}</span></div>' for x in n["notes"]))
    owner = e(n["owner"]) + ('' if n.get("owner_from") in (n["id"], None) else f' <span class="sub">from {e(n["owner_from"])}</span>')
    out.append(f'<tr class="goal d{min(n["depth"], 5)}{"" if n["state"] == "active" else " closed"}"><td class="g">{head}</td>'
               f'<td class="why">{"<br>".join(measure)}</td><td>{owner}</td>{_rollup_cells(n["rollup"], d.now)}'
               f'{_spend_cell(getattr(d, "_goal_spend", None), "goals", n["id"])}</tr>')
    if n["works"]:
        k = len(n["works"])
        out.append(f'<tr class="goal-works d{min(n["depth"], 5)}"><td colspan="8"><details{" open" if k <= 5 else ""}><summary>'
                   f'{k} work{"s" if k != 1 else ""} on this goal</summary><div class="scroll"><table class="works">{WORKS_HEAD}<tbody>'
                   + "".join(_work_row(w, d) for w in n["works"]) + "</tbody></table></div></details></td></tr>")
    for c in n["children"]:
        _goal_rows(d, c, out)


def _conditions_html(d, conds, compact=False):
    """The conditions table: full width (the breach block), or compact beside the goal tree (three columns, the
    rollup in one cell)."""
    import of_works
    rows = []
    for c in conds:
        reading = metric_reading(d, c.get("metric"))
        c["breached"] = level_breached(c.get("level"), reading)
        flag = ('<span class="flag breach">breached</span>' if c["breached"] else
                '<span class="flag">held</span>' if c["breached"] is False else "")
        read = (num(reading) if reading is not None else
                (f'<span class="na">{e(c["metric"])}: not a Studio measure</span>' if c.get("metric") else '<span class="na">no metric</span>'))
        name = (f'<b>{e(c["title"])}</b> {flag}<div class="src"><code>{e(c["id"])}</code>'
                f'{" · held for " + e(c["under"]) if c.get("under") else " · the whole organization"}'
                f'{"" if c["state"] == "active" else " · " + e(c["state"])}</div>')
        cls = f'{"crit" if c["breached"] else ""}{" closed" if c["state"] != "active" else ""}'.strip()
        measure = f'<td class="why">{e(c.get("measure") or "–")}<br>level {e(c.get("level") or "–")}<br>now {read}</td>'
        if compact:
            r = c["rollup"]
            last = of_works.age(r["last_progress"], d.now)
            works = (f'{r["open"]} open of {r["works"]}<div class="src">{_names(r["doers"], 3)}</div>'
                     + (f'<div class="src">{r["asks"]} open ask{"s" if r["asks"] != 1 else ""}</div>' if r["asks"] else "")
                     + (f'<div class="src">{r["progress_7d"]} progress marks in 7 d{" · last " + e(last) + " ago" if last else ""}</div>'
                        if r["works"] else "")) if r["works"] else '<span class="na">no work yet</span>'
            rows.append(f'<tr class="{cls}"><td>{name}<div class="src">owner {e(c["owner"])}</div></td>{measure}<td>{works}</td>'
                        f'{_spend_cell(getattr(d, "_goal_spend", None), "conditions", c["id"])}</tr>')
        else:
            rows.append(f'<tr class="{cls}"><td>{name}</td>{measure}<td>{e(c["owner"])}</td>{_rollup_cells(c["rollup"], d.now)}'
                        f'{_spend_cell(getattr(d, "_goal_spend", None), "conditions", c["id"])}</tr>')
    head = ('<th>Condition</th><th>Measure · level · now</th><th>Works</th><th>Spend this month</th>' if compact else
            '<th>Condition</th><th>Measure · level · now</th><th>Owner</th><th>Open works</th><th>Doers</th><th>Open asks</th><th>Progress</th>'
            '<th>Spend this month</th>')
    return (f'<div class="scroll"><table class="conditions"><thead><tr>{head}</tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>")


def _spend_note(sp):
    if sp is None:
        return ('<p class="note">Spend this month: no sums for this month yet (the listener writes them; '
                '<code>of capacity spend --fresh</code> computes them now).</p>')
    when = time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(sp.get("computed_at") or 0))
    return (f'<p class="note">Spend this month ({e(sp["month"])}, to {when}): the list cost of the responses of each goal\'s '
            'branch and fresh tokens, a reading with no budget (docs/registry.md, Goals, "Spend per goal").'
            + ("" if sp.get("complete", True) else " The month is still being read, so it is low.") + '</p>')


def view_goals(d):
    """The goal tree in rank order, each goal with its measure, target, owner, the works under it, their doers, open asks
    and progress; the conditions beside it, a breach above everything; the works that name no goal apart."""
    import of_works
    ws = works_of(d)
    if ws is None:
        return '<p class="note">Choose an organization above: goals are kept per organization.</p>'
    keep = set(d.model["positions"]) if d.unit else None
    t = of_works.goal_tree(d.full, ws, keep)
    d._goal_spend = sp = goal_spend(d)
    if not t["roots"] and not t["conditions"]:
        return (empty_note(d) + '<p class="note">No goals in this organization\'s registry yet. Goals are the owner\'s: the '
                'structure keeper writes them, a tree of goals and sub-goals, each with its measure, and records the ask id of the '
                'owner\'s yes on each (docs/registry.md, Goals). Works name the goal they serve; until then they are listed in Works.</p>')
    nodes = []

    def walk(n):
        nodes.append(n)
        for c in n["children"]:
            walk(c)
    for r in t["roots"]:
        walk(r)
    live = lambda w: w.get("state") not in ("done", "parked")
    loose = [w for w in t["loose"] if live(w)]
    cond_html = _conditions_html(d, t["conditions"], compact=bool(t["roots"])) if t["conditions"] else ""
    breached = [c for c in t["conditions"] if c.get("breached")]
    on_goals = sum(n["rollup"]["open"] for n in t["roots"])
    tiles = "".join(tile(lbl, num(v), hint) for lbl, v, hint in (
        ("Goals", sum(1 for n in nodes if n["state"] == "active" and not n["depth"]), "active root goals"),
        ("Sub-goals", sum(1 for n in nodes if n["state"] == "active" and n["depth"]), "active, at every level below"),
        ("Works on goals", on_goals, "open works under the tree"),
        ("No goal", len(loose), "open works naming no goal or condition"),
        ("Conditions", sum(1 for c in t["conditions"] if c["state"] == "active"), f'{len(breached)} breached' if breached else ""),
        ("Open asks", sum(n["rollup"]["asks"] for n in t["roots"]), "from the works on goals")))
    out = [empty_note(d)]
    if breached:   # docs/registry.md: a breach comes before progress
        out.append(f'<div class="breach"><h2 class="lead crit">A condition is breached <span class="sub">it comes before progress on any '
                   f'goal</span></h2>{_conditions_html(d, breached)}</div>')
    out.append(f'<section class="tiles">{tiles}</section>')
    out += [f'<p class="note">{e(x)}</p>' for x in t["notes"]]
    tree = []
    for r in t["roots"]:
        _goal_rows(d, r, tree)
    tree_html = ('<h2 class="lead">Goals <span class="sub">in rank order; each goal adds up the works of its whole branch; '
                 'its doers are derived from its works, never written</span></h2><div class="scroll"><table class="goals"><thead><tr>'
                 '<th>Goal</th><th>Measure · target</th><th>Owner</th><th>Open works</th><th>Doers</th><th>Open asks</th>'
                 '<th>Progress</th><th>Spend this month</th></tr></thead><tbody>' + "".join(tree) + '</tbody></table></div>'
                 + _spend_note(sp)) if tree else \
        '<p class="note">No goals in the registry yet; the conditions stand alone.</p>'
    side = (f'<h2 class="lead">Conditions <span class="sub">held at their level while the goals are pursued; '
            f'never raised past it</span></h2>{cond_html}') if cond_html else ""
    out.append(f'<div class="goalgrid"><div>{tree_html}</div><div>{side}</div></div>' if side else tree_html)
    if t["unknown"]:
        out.append('<p class="note">Works naming a goal or condition the registry does not have: '
                   + ", ".join(f"<code>{e(w)}</code> → <code>{e(g)}</code>" for w, g in t["unknown"]) + "</p>")
    if loose:
        out.append(f'<details{" open" if len(loose) <= 10 else ""}><summary>Works with no goal: {len(loose)} open '
                   f'<span class="sub">a question for the owner: which goal does each serve, or which condition does it keep?</span>'
                   f'</summary><div class="scroll"><table class="works">{WORKS_HEAD}<tbody>'
                   + "".join(_work_row(w, d) for w in loose) + "</tbody></table></div></details>")
    return "".join(out)


def _works_source_note(ws):
    src = ws[0].get("source") if ws else ""
    if src and src.startswith("registry/"):
        return (f'<p class="note">The works are the structure keeper\'s confirmed list (<code>{e(src)}</code>). The state is computed from '
                'evidence (merged PRs by the work\'s match or by a doer with one open work, commits to its RFC, the doers\' turns and waits); '
                'the card shows the state the list declares beside it. Cost and stages with evidence are not shown yet.</p>')
    return ('<p class="note">First version: Studio derives the works itself, from the RFC files in the main checkouts and the positions '
            'of kind project; a brief that names an RFC in its mandate makes its position a doer of that RFC. It switches to the '
            'registry\'s <code>works:</code> map once the structure keeper has confirmed it (docs/works.md, steps 1–2). Cost is not shown yet.</p>')


def _work_card(d, w):
    import of_works
    doc = w.get("doc") or {}
    standing = w.get("standing")
    facts = [("Kind", w["kind"]), ("State", w["state"] + (" · standing" if standing else "") + (" · past review" if w["past_review"] else "")),
             ("Doers", ", ".join(w["doers"]) or "nobody"), ("Systems", ", ".join(w["systems"]) or "–"),
             ("Doc", f'{doc.get("repo") + ": " if doc.get("repo") else ""}{doc.get("path") or "–"}'),
             ("Status line", w.get("status_line") or "–"),
             ("Done when", w.get("done_when") or ("standing: no final end; its loop is the end condition, and only the "
                                                  "owner's decision ends it" if standing else "–")),
             ("Review", _standing_review(w, d) if standing else w.get("review") or "–"), ("Last progress", (of_works.age(w["last_progress"], d.now) or "–") + " ago"),
             ("Declared in the list", " · ".join(str(x) for x in (w.get("declared_state"), w.get("declared_progress")) if x) or "–"),
             ("Match", "; ".join(f"{k}: {', '.join(map(str, v)) if isinstance(v, list) else v}"
                                 for k, v in (w.get("match") or {}).items() if v) or "doers' PRs"),
             ("Source", w.get("source"))]
    if w.get("ambiguous_doers"):
        facts.append(("Not counted", f'PRs of {", ".join(w["ambiguous_doers"])} that match no work: they have several open works, '
                                     'so a PR counts only through a match'))
    dl = "".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in facts)
    prs = "".join(f'<tr><td class="age">{e(str(p["merged_at"])[:16])}</td><td>{e(p["repo"])}#{e(p["number"])}</td>'
                  f'<td>{f"<a href={chr(34)}{e(p[chr(117)+chr(114)+chr(108)])}{chr(34)}>" if p.get("url") else ""}{e(p["title"])}{"</a>" if p.get("url") else ""}</td>'
                  f'<td class="src">{e(p["branch"])}</td><td>{e(p["position"] or "")}</td></tr>' for p in w["prs"][:40])
    ops = "".join(f'<tr><td class="age">{e(of_model.iso(p["activity"])[:16]) if p.get("activity") else "–"}</td>'
                  f'<td>{e(p["repo"])}#{e(p["number"])}{" · draft" if p.get("draft") else ""}</td>'
                  f'<td>{f"<a href={chr(34)}{e(p[chr(117)+chr(114)+chr(108)])}{chr(34)}>" if p.get("url") else ""}{e(p["title"])}{"</a>" if p.get("url") else ""}</td>'
                  f'<td class="src">{e(p["branch"])}</td><td class="age">{e(str(p.get("last_review_at") or "–")[:16])}</td>'
                  f'<td>{e(p["position"] or "")}</td></tr>' for p in (w.get("open_prs") or [])[:40])
    waits = "".join(f"<li>{e(_wait_text(x, d.now))}{': ' + e(x.get('text')[:160]) if x.get('text') else ''}</li>" for x in w["waits"] + w["stalled"])
    iss = "".join(f'<li><code>{e(i["rule"])}</code> {e(i["summary"])}</li>' for i in w["issues"])
    commits = "".join(f"<li>{e(of_model.iso(t)[:16])}</li>" for t in (w.get("doc_commits") or [])[:10])
    brs = "".join(f'<tr><td class="age">{e(of_model.iso(b["last"])[:16]) if b.get("last") else "–"}</td><td>{e(b["repo"])}</td>'
                  f'<td><code>{e(b["branch"])}</code></td></tr>' for b in (w.get("branches") or [])[:15])
    pc = w.get("path_commits") or []
    turns = _doer_turns(d, w["doers"]) if not d.unit else ""
    return f"""<p><a href="{e(link("works", d.org))}">← all works</a></p>
<h2 class="lead">{e(w["title"])} <span class="sub">{e(w["id"])}</span></h2>
<table class="facts"><tbody>{dl}</tbody></table>
{_loop_card(d, w)}
<h2>Waiting on</h2>{f"<ul>{waits}</ul>" if waits else '<p class="quiet">Nothing declared or detected.</p>'}
<h2>Merged PRs <span class="sub">matched to the work at import (its match), or a doer's PR that matches no work; newest first</span></h2>
{f'<div class="scroll"><table><thead><tr><th>Merged</th><th>PR</th><th>Title</th><th>Branch</th><th>By</th></tr></thead><tbody>{prs}</tbody></table></div>' if prs else '<p class="quiet">None in the window.</p>'}
{f'<h2>Open PRs <span class="sub">latest state of each; the newest push or review first; both count as progress</span></h2><div class="scroll"><table><thead><tr><th>Last push or review</th><th>PR</th><th>Title</th><th>Branch</th><th>Last review</th><th>By</th></tr></thead><tbody>{ops}</tbody></table></div>' if ops else ""}
{"" if w.get("standing") else _stages_html(w)}
{f"<h2>Commits to the RFC file <span class=sub>last 60 days</span></h2><ul>{commits}</ul>" if commits else ""}
{f'<h2>Branches its match names <span class="sub">last commit per remote branch: open PRs and long-running work, newest first; {len(w["branches"])} in all</span></h2><table><thead><tr><th>Last commit</th><th>Repo</th><th>Branch</th></tr></thead><tbody>{brs}</tbody></table>' if brs else ""}
{f'<p class="legend">{len(pc)} commits on the default branch touched its match paths in 14 days; the last at {e(of_model.iso(pc[0])[:16])} UTC.</p>' if pc else ""}
{f"<h2>Open issues on its doers or systems</h2><ul>{iss}</ul>" if iss else ""}
{turns}
<p class="legend">Stages with evidence, asks and hand-overs per work come with the registry's <code>works:</code> map (docs/works.md §3).</p>"""


def _stages_html(w):
    import of_works
    st = w.get("stages") or []
    if not st:
        why = ("its RFC has no stage headings or hypothesis table" if w.get("doc_found") else
               "its RFC was not found on the default branch or a matched branch" if w["kind"] == "rfc" else "not an RFC")
        return (f'<h2>Stages</h2><p class="quiet">None found: {e(why)}. The structure keeper can list them under the work\'s '
                '<code>stages:</code> in the registry.</p>') if w.get("state") not in ("done", "parked") else ""
    trs = "".join(
        f'<tr><td><b>{e(of_works.stage_label(x["id"]))}</b></td><td>{e(x["title"])}'
        f'{f"<div class=src>done when: {e(x[chr(100)+chr(111)+chr(110)+chr(101)+chr(95)+chr(119)+chr(104)+chr(101)+chr(110)])}</div>" if x.get("done_when") else ""}'
        f'{f"<div class=src>{e(x[chr(110)+chr(111)+chr(116)+chr(101)])}</div>" if x.get("note") else ""}</td>'
        f'<td class="pace {"ok" if x["state"] == "done" else "off" if x["state"] == "moving" else "na"}">{e(x["state"])}</td>'
        f'<td>{", ".join(f"<a href={chr(34)}{e(p.get(chr(117)+chr(114)+chr(108)) or chr(35))}{chr(34)}>#{e(p[chr(110)+chr(117)+chr(109)+chr(98)+chr(101)+chr(114)])}</a>" for p in x["prs"][:6]) or "–"}</td>'
        f'<td class="src">{e(", ".join(b["branch"] for b in x["branches"][:3])) or "–"}</td><td class="src">{e(x["source"])}</td></tr>'
        for x in st)
    return (f'<h2>Stages <span class="sub">{e(w.get("stage_summary") or "")}; {e(w.get("stages_from") or "")}</span></h2>'
            f'<div class="scroll"><table><thead><tr><th>Stage</th><th>What</th><th>State</th><th>PRs naming it</th><th>Branches</th>'
            f'<th>From</th></tr></thead><tbody>{trs}</tbody></table></div>'
            + ('<p class="legend">Stages and their done or open state are the work\'s list in the registry (from its doers); a stage is '
               'moving when a matched PR or branch names its id (a range such as t2-t5 names each of t2…t5; a numeric id names nothing).</p>'
               if w.get("stages_from") == "registry" else
               '<p class="legend">Stages are read from the RFC (st-v0): stage-like headings (Stage, Phase, Part, Step N; P1, T7, S1) and '
               'hypothesis rows (H1, H4a). A stage is done when its heading, a bold verdict in its row (**Closed …), or the Status line '
               'says so; it is moving when a matched PR or branch names it.</p>'))


def _doer_turns(d, doers, n=6):
    """The doers' last turns, one line each (the tenant only: masked text)."""
    positions = d.full["positions"]
    sessions = of_model.session_index(positions)
    turns = [r for r in d.rows if r["name"] == "of.turn_end" and of_model.position_of(r, positions, sessions) in set(doers)]
    turns.sort(key=lambda r: r["ts"], reverse=True)
    lis = "".join(f'<li><span class="src">{e(r["ts"][5:16])} · {e(of_model.position_of(r, positions, sessions))}</span> '
                  f'{e(" ".join(((r.get("payload") or {}).get("last_message") or "").split())[:200])}</li>' for r in turns[:n])
    return f"<h2>Doers' last turns</h2><ul>{lis}</ul>" if lis else ""


KIND_ORDER = {"creating": 0, "target": 1, "enabling": 2}


def _system_order(systems):
    kids = {}
    for sid, s in systems.items():
        kids.setdefault(s.get("parent") if s.get("parent") in systems else None, []).append(sid)
    out = []

    def walk(sid, depth):
        out.append((sid, depth))
        for k in sorted(kids.get(sid, []), key=lambda x: (KIND_ORDER.get(systems[x].get("kind"), 9), x)):
            walk(k, depth + 1)
    for r in sorted(kids.get(None, []), key=lambda x: (KIND_ORDER.get(systems[x].get("kind"), 9), x)):
        walk(r, 0)
    return out


def view_coverage(d, show_all=False):
    m = d.model
    systems, positions = m["systems"], m["positions"]
    order = _system_order(systems)
    sys_rank = {s: i for i, (s, _) in enumerate(order)}
    cols = sorted((n for n, p in positions.items() if show_all or p["status"] == "active"),
                  key=lambda n: (sys_rank.get(positions[n]["system"], 999), n))
    head = "".join(f'<th class="rot"><div><span>{e(n)}</span></div></th>' for n in cols)
    body = []
    flagged = 0
    for sid, depth in order:
        cov = m["coverage"]["systems"][sid]
        cells = []
        for n in cols:
            c = m["dmm"].get((n, sid))
            code = of_model.cell_code(c) if c else ""
            k = "k" if code.startswith("K") else ("w" if "W" in code else ("h" if code.startswith("h") else ("r" if code else "")))
            cells.append(f'<td class="cell {k}">{e(code)}</td>')
        flags = cov["flags"]
        flagged += bool(flags)
        fl = " ".join(f'<span class="flag {e(f)}">{e(f.replace("_", " "))}</span>' for f in flags)
        fl += "".join(f' <span class="na">{e(x)}</span>' for x in cov.get("notes") or [])
        lc = (systems[sid].get("lifecycle") or "live")
        lc = f'<span class="sub">{e(lc)}</span>' if lc != "live" else ""   # registry.md: shown next to the name
        body.append(f'<tr><th class="sys d{min(depth, 3)}"><span class="kind {e(systems[sid].get("kind"))}"></span>{e(sid)}{lc}</th>'
                    f'{"".join(cells)}<td class="flags">{fl}</td></tr>')
    toggle = link("coverage", d.org, all=None if show_all else "1")
    nosys = m["coverage"]["positions_without_system"]
    return f"""<section class="tiles">{tile("Systems", num(len(systems)))}{tile("Flagged systems", num(flagged))}
{tile("Without keeper", num(sum(1 for s in m['coverage']['systems'].values() if 'no_keeper' in s['flags'])))}
{tile("Positions shown", num(len(cols)))}</section>
<p class="legend"><span class="cell k">K</span> keeper (single writer) · <span class="cell w">W3</span> wrote 3 work records in the window ·
<span class="cell h">h</span> home system · <span class="cell r">m</span> edited (mandate router) · <span class="cell r">r</span> read its files ·
<a href="{e(toggle)}">{"active positions only" if show_all else "show parked and finished"}</a></p>
<div class="scroll"><table class="matrix"><thead><tr><th></th>{head}<th>Flags</th></tr></thead><tbody>{"".join(body)}</tbody></table></div>
{f'<p class="note">Positions with no system in the map: {e(", ".join(nosys))}</p>' if nosys else ''}
<p class="note">{"Writes need work records with touched paths (" + str(m['unmapped_work_records']) + " unmapped in the window)." if (m.get('work_records') or {}).get('total') else "No work records in the window, so the matrix shows no writes: this organization's work is not imported."}</p>
{_mandate_tables(d)}"""


def _mandate_tables(d):
    """Work outside mandates, from of.mandate_check (the mandate router in observe mode)."""
    m = d.model
    mand, orphan = m.get("mandate") or {}, m.get("orphan_work") or {}
    if not mand:
        return ('<h2>Work outside mandates</h2><p class="note">The mandate router (observe mode) writes '
                '<code>of.mandate_check</code> for enrolled positions; none in the window yet. It shows here which work '
                'lands in another position\'s system, and orphan work that no keeper answers for.</p>')
    rows = []
    for n, c in sorted(mand.items(), key=lambda kv: -(sum(kv[1]["owned_by"].values()) + kv[1]["unowned"])):
        total = c["in_mandate"] + sum(c["owned_by"].values()) + c["unowned"] + c["critical"] + c.get("unscoped", 0)
        out = sum(c["owned_by"].values()) + c["unowned"]
        others = ", ".join(f"{k} {v}" for k, v in sorted(c["owned_by"].items(), key=lambda kv: -kv[1]))
        rows.append(f'<tr><td><b>{e(n)}</b></td><td class="num">{total}</td><td class="num">{c["in_mandate"]}</td>'
                    f'<td>{e(others) or "–"}</td><td class="num">{c["unowned"]}</td><td class="num">{c.get("unscoped", 0)}</td>'
                    f'<td class="num">{pct(out / total if total else None)}</td>'
                    f'<td class="num">{c["critical"] or ""}</td></tr>')
    orows = "".join(
        f'<tr><td>{e(area)}</td><td>{"system without keeper" if area in m["systems"] else "no system claims it"}</td>'
        f'<td class="num">{sum(v.values())}</td><td>{e(", ".join(f"{p} {n}" for p, n in sorted(v.items(), key=lambda kv: -kv[1])))}</td></tr>'
        for area, v in sorted(orphan.items(), key=lambda kv: -sum(kv[1].values())))
    return f"""<h2>Work outside mandates <span class="sub">tool calls by enrolled positions, mandate router in observe mode, last 7 days;
unscoped = a shell at a repository root, not a finding</span></h2>
<table><thead><tr><th>Position</th><th>Calls</th><th>In mandate</th><th>In another's system (its keeper)</th><th>Unowned</th><th>Unscoped</th><th>Outside</th><th>Critical</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>
<h2>Orphan work <span class="sub">work that no keeper answers for: a new position, or an extended mandate (RFC 0001 §5.4)</span></h2>
{f'<table><thead><tr><th>Area</th><th>Why</th><th>Calls</th><th>By</th></tr></thead><tbody>{orows}</tbody></table>' if orows else '<p class="quiet">None.</p>'}"""


KIND_ABBR = {"uses": "u", "enabled_by": "e", "interacts": "i"}


def view_structure(d, n=None, min_coupling=1):
    m = d.model
    n = n or m.get("max_systems") or of_model.MAX_SYSTEMS
    systems = m["systems"]
    order = [sid for sid, _ in _system_order(systems)]
    rows = of_model.overlay(m, n)
    wide = sum(r["wide"] for r in rows)
    idx = {(r["a"], r["b"]): r for r in rows}
    undeclared = [r for r in rows if r["status"] == "undeclared" and r["coupling"] >= min_coupling]
    unused = [r for r in rows if r["status"] == "declared_only"]
    runtime = [r for r in rows if r["status"] == "runtime_only"]
    both = [r for r in rows if r["status"] == "both"]
    head = "".join(f'<th class="rot"><div><span>{e(s)}</span></div></th>' for s in order)
    body = []
    for a in order:
        cells = []
        for b in order:
            if a == b:
                cells.append('<td class="cell self"></td>')
                continue
            r = idx.get(tuple(sorted((a, b))))
            if not r or r["status"] == "wide_only":
                cells.append('<td class="cell"></td>')
                continue
            mine = r["ab"] if (a, b) == (r["a"], r["b"]) else r["ba"]   # the edge declared from this row's system
            code = (KIND_ABBR.get(mine, "d") if mine else "") + (str(r["coupling"]) if r["coupling"] else "")
            cls = {"both": "both", "declared_only": "decl", "undeclared": "undecl", "runtime_only": "rt"}[r["status"]]
            tip = (f"{a} {mine} {b}" if mine else f"{a} · {b}: no edge declared from {a}") + \
                  (f" · {r['coupling']} PRs changed both" if r["coupling"] else " · not changed together in the window") + \
                  (f" · observed by {r['observed_by']}" if r["observed_by"] else "") + \
                  (f" · {r['wide']} wider PRs left out; weighted {r['weighted']}" if r["wide"] else "")
            cells.append(f'<td class="cell {cls}" title="{e(tip)}">{e(code)}</td>')
        body.append(f'<tr><th class="sys"><span class="kind {e(systems[a].get("kind"))}"></span>{e(a)}</th>{"".join(cells)}</tr>')
    ulist = "".join(f'<tr><td>{e(r["a"])}</td><td>{e(r["b"])}</td><td class="num">{r["coupling"]}</td>'
                    f'<td class="num">{r["weighted"]}</td><td class="num">{r["wide"] or ""}</td></tr>' for r in undeclared)

    def edges(items):
        return "".join(f'<tr><td>{e(r["a"] if r["ab"] else r["b"])}</td><td>{e(r["ab"] or r["ba"])}</td>'
                       f'<td>{e(r["b"] if r["ab"] else r["a"])}</td><td class="src">{e(r["interface_ab"] or r["interface_ba"] or "")}</td></tr>'
                       for r in items)
    dlist, rlist = edges(unused), edges(runtime)
    choose = " ".join(f'<a class="org{" on" if k == n else ""}" href="{e(link("structure", d.org, n=k))}">≤ {k}</a>' for k in (2, 3, 4, 6, 99))
    return f"""<section class="tiles">{tile("Declared edges", num(sum(bool(r['ab']) + bool(r['ba']) for r in rows)))}
{tile("Declared and observed", num(len(both)))}{tile("Observed, not declared", num(len(undeclared)), "missing edge or path claim")}
{tile("Runtime only", num(len(runtime)), "observed_by: runtime, no co-change expected")}
{tile("Declared, not observed", num(len(unused)), "no co-change, no runtime basis")}
{tile("Work records in the window", num(sum(1 for r in d.rows if r["name"] == "of.work_record" and of_measures.in_window(r, d.a, d.b))), f"{m.get('bot_work_records', 0)} by bots left out")}</section>
<p class="legend"><span class="cell both">u12</span> declared (u uses, e enabled by, i interacts) and changed together in 12 PRs ·
<span class="cell rt">u</span> declared, runtime basis (config references) · <span class="cell decl">u</span> declared only ·
<span class="cell undecl">7</span> changed together, no edge either way · row → column is the declared direction · window: the last 7 days</p>
<p class="legend">Count PRs touching at most <b>{n}</b> systems: {choose} · {wide} pair-counts from wider PRs left out
(cross-system changes such as migration windows); the lists also show the weight Σ 1/(k−1) over all PRs.</p>
<div class="scroll"><table class="matrix dsm"><thead><tr><th></th>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>
<h2>Observed, not declared <span class="sub">system pairs changed in the same PR, most first; ask the structure keeper</span></h2>
{f'<table><thead><tr><th>System</th><th>System</th><th>PRs ≤ {n} systems</th><th>Weighted</th><th>Wider PRs</th></tr></thead><tbody>{ulist}</tbody></table>' if ulist else '<p class="quiet">None.</p>'}
<h2>Runtime only <span class="sub">declared with observed_by: runtime; operational interfaces co-change cannot see</span></h2>
{f'<table><thead><tr><th>System</th><th>Edge</th><th>System</th><th>Interface</th></tr></thead><tbody>{rlist}</tbody></table>' if rlist else '<p class="quiet">None declared with a runtime basis yet.</p>'}
<h2>Declared, not observed in the window <span class="sub">no co-change and no runtime basis: add observed_by, or question the edge</span></h2>
{f'<table><thead><tr><th>System</th><th>Edge</th><th>System</th><th>Interface</th></tr></thead><tbody>{dlist}</tbody></table>' if dlist else '<p class="quiet">None.</p>'}
<p class="note">Coupling counts merged PRs that touched both systems, mapped by changed paths against the systems map
({m['unmapped_work_records']} PRs touched no claimed path). Merge-queue copies and dependency bumps are left out.</p>"""


FACT_WORD = {"confirmed": "confirmed", "ask_not_found": "ask not found", "unconfirmed": "unconfirmed"}


def _fact_html(f):
    v = f" {f['value']}" if f.get("value") else ""
    head = f'<b>{e(f["fact"])}</b>{e(v)}'
    if f["state"] == "confirmed":
        return f'{head} <span class="src">✓ ask {e(f["ask"])}</span>'
    tip = "an ask id under confirmed: that no stored ask has" if f["state"] == "ask_not_found" else \
        "no ask id under confirmed:; a retold date in a comment confirms nothing"
    ask = f' {e(f["ask"])}' if f.get("ask") else ""
    return f'{head} <span class="flag" title="{e(tip)}">{e(FACT_WORD[f["state"]])}{ask}</span>'


def view_systems(d):
    """Each system's evidence (RFC 0007 §2.2): where it runs, code activity and writers over 30 days, money, and
    which governance facts the owner confirmed by an ask id."""
    m = d.model
    ev, act = d.evidence(), d.code_activity()
    days = round((of_model.epoch(act["to"]) - of_model.epoch(act["from"])) / DAY)
    order = _system_order(m["systems"])
    live = [sid for sid, _ in order if ev[sid]["live"]]
    body = []
    for sid, depth in order:
        x = ev[sid]
        code = x.get("code") or {}
        runs = x.get("runs") or {}
        if runs.get("where"):
            rc = e(runs["where"]) + (f'<br><span class="src">{e(runs["evidence"])}</span>' if runs.get("evidence") else
                                     ' <span class="flag" title="where it runs, without evidence from the platform keepers">no evidence</span>')
        else:
            rc = '<span class="flag">not recorded</span>' if x["live"] else '<span class="na">–</span>'
        wr = sorted((code.get("writers") or {}).items(), key=lambda kv: (-kv[1], kv[0]))
        if d.unit:   # a unit's owner sees its own positions; the others' records are counted, never named
            out_n = sum(n for p, n in wr if p not in m["positions"])
            wr = [(p, n) for p, n in wr if p in m["positions"]] + ([("outside the unit", out_n)] if out_n else [])
        wtxt = ", ".join(f"{p} {n}" for p, n in wr[:3]) + (f", +{len(wr) - 3}" if len(wr) > 3 else "")
        if code.get("unattributed"):
            au = sorted(code["authors"].items(), key=lambda kv: -kv[1])
            wtxt += ("; " if wtxt else "") + f'<span class="src" title="{e(", ".join(f"{a} {n}" for a, n in au))}">' \
                    f'{code["unattributed"]} by no position</span>'
        elif wtxt:
            wtxt = e(wtxt)
        ctext = (f'{code.get("merged", 0)} merged' + (f' · {code["open"]} open' if code.get("open") else "") +
                 (f'<br><span class="src">last {e(age(d.now - of_model.epoch(code["last"])))} ago</span>' if code.get("last") else "")) \
            if code.get("merged") or code.get("open") else '<span class="na">none</span>'
        money = e(x["money"]) if x.get("money") else ('<span class="flag">not recorded</span>' if x["live"] else '<span class="na">–</span>')
        gov = "<br>".join(_fact_html(f) for f in x["governance"]) or '<span class="na">–</span>'
        lc = f'<span class="sub">{e(x["lifecycle"])}</span>' if not x["live"] else ""
        name = f'<br><span class="src">{e(x["name"])}</span>' if x.get("name") else ""
        body.append(f'<tr><th class="sys d{min(depth, 3)}"><span class="kind {e(x.get("kind"))}"></span>{e(sid)}{lc}{name}</th>'
                    f'<td>{rc}</td><td class="num">{ctext}</td><td>{wtxt or "–"}</td><td>{money}</td><td>{gov}</td>'
                    f'<td class="src">{e(", ".join(x["needs"])) or "–"}</td></tr>')
    n_runs = sum(1 for sid in live if (ev[sid].get("runs") or {}).get("where"))
    n_money = sum(1 for sid in live if ev[sid].get("money"))
    unconf = sum(ev[sid]["unconfirmed"] for sid in ev)
    active = sum(1 for sid in ev if (ev[sid].get("code") or {}).get("merged"))
    checked = d.ask_ids() is not None
    pairs = [p for p in act["co_change"] if p["a"] in m["systems"] and p["b"] in m["systems"]][:15]
    prow = "".join(f'<tr><td>{e(p["a"])}</td><td>{e(p["b"])}</td><td class="num">{p["prs"]}</td>'
                   f'<td class="num">{p["weighted"]}</td><td class="num">{p["wide"] or ""}</td></tr>' for p in pairs)
    return f"""<section class="tiles">{tile("Live systems", num(len(live)))}
{tile("Where it runs", f"{n_runs}/{len(live)}", "runs: with evidence")}{tile("Money", f"{n_money}/{len(live)}", "money: a P&L or cost line, or a goal")}
{tile("Unconfirmed facts", num(unconf), "keeper, governed_by, lifecycle without an ask id")}
{tile(f"Changed in {days} days", num(active), f"{act['records']} merged records, {act['unmapped']} unmapped")}</section>
<p class="legend">The system architect's evidence (RFC 0007 §2.2). Runtime from <code>runs:</code>, money from <code>money:</code>,
governance from <code>confirmed:</code> (a fact → the owner's ask id; a comment such as "(founder, date)" confirms nothing{"" if checked else "; ask ids not checked against the store here"}).
Code: merged PRs and commits over the last {days} days by changed paths, open PRs from <code>of.pr_state</code>, writers by position
(hover "by no position" for the authors). Needs: the kinds of work the system needs, shown on the <a href="{e(link("cells", d.org))}">Cells</a> page.</p>
<div class="scroll"><table><thead><tr><th>System</th><th>Where it runs</th><th>Code, {days} days</th><th>Writers</th><th>Money</th><th>Governance</th><th>Needs</th></tr></thead>
<tbody>{"".join(body)}</tbody></table></div>
<h2>Changed together, last {days} days <span class="sub">system pairs in one merged record, most first; ≤ {act['max_systems']} systems per record count, the weight Σ 1/(k−1) takes all</span></h2>
{f'<table><thead><tr><th>System</th><th>System</th><th>Records</th><th>Weighted</th><th>Wider</th></tr></thead><tbody>{prow}</tbody></table>' if prow else '<p class="quiet">None.</p>'}
<p class="note">The same data as JSON: <code>of model evidence --json</code>, and per changed directory for lab-systems-v0 <code>of model activity --json --areas 2</code>.</p>"""


def view_cells(d):
    """Ownership cells (RFC 0007 §2.3): system or subsystem x kind of work, one decider; the needed cells of live
    systems that have no owner, and the cells with three or more doers."""
    c = d.model.get("cells") or of_model.cells_of({}, {}, of_model.METHODS)
    methods = list(c["methods"])
    if not c["cells"] and not c["unknown"]:
        return (f'<section class="tiles">{tile("Systems with needs", "0")}{tile("Positions with owns", "0")}</section>'
                '<p class="note">No system lists <code>needs:</code> and no position lists <code>owns:</code> yet. '
                'The system architect writes what each system needs; each position\'s entry lists its cells as '
                '<code>{system, method, decides}</code> (RFC 0007 §2.3, docs/registry.md). Then this page shows the needed '
                'cells with no owner and the cells with three or more doers.</p>')
    idx = {(r["system"], r["method"]): r for r in c["cells"]}
    used = [m for m in methods if any(r["method"] == m for r in c["cells"])]
    systems = d.model["systems"]
    rows_ = [sid for sid, _ in _system_order(systems) if any(k[0] == sid for k in idx)]
    head = "".join(f'<th title="{e(c["methods"][m])}">{e(m)}</th>' for m in used)
    body = []
    for sid in rows_:
        cells = []
        for m in used:
            r = idx.get((sid, m))
            if not r:
                cells.append('<td class="cell"></td>')
                continue
            code = ("D" if r["deciders"] else "") + (str(len(r["doers"])) if r["doers"] else "")
            cls = "undecl" if r["gap"] else "w" if r["overlap"] or len(r["deciders"]) > 1 else "k" if r["needed"] else "decl"
            tip = (f"{sid} · {m}: " + ("needed" if r["needed"] else "not in needs") +
                   (f" · decides: {', '.join(r['deciders'])}" if r["deciders"] else " · no decider") +
                   (f" · doers: {', '.join(r['doers'])}" if r["doers"] else "") +
                   (f" · held by inactive {', '.join(r['inactive'])}" if r["inactive"] else ""))
            cells.append(f'<td class="cell {cls}" title="{e(tip)}">{e(code or "–")}</td>')
        dep = dict(_system_order(systems)).get(sid, 0)
        body.append(f'<tr><th class="sys d{min(dep, 3)}"><span class="kind {e(systems[sid].get("kind"))}"></span>{e(sid)}</th>{"".join(cells)}</tr>')

    def who(r):
        return ", ".join(r["doers"]) or "–"
    gaps = "".join(f'<tr><td>{e(r["system"])}</td><td>{e(r["method"])}</td>'
                   f'<td>{"nobody holds it" if r["gap"] == "nobody" else "doers, no decider"}</td><td>{e(who(r))}</td>'
                   f'<td class="src">{e(", ".join(r["inactive"]))}</td></tr>' for r in c["gaps"])
    over = "".join(f'<tr><td>{e(r["system"])}</td><td>{e(r["method"])}</td><td class="num">{len(r["doers"])}</td>'
                   f'<td>{e(", ".join(r["deciders"]) or "–")}</td><td>{e(who(r))}</td></tr>' for r in c["overlaps"])
    two = "".join(f'<tr><td>{e(r["system"])}</td><td>{e(r["method"])}</td><td>{e(", ".join(r["deciders"]))}</td></tr>'
                  for r in c["two_deciders"])
    unk = "".join(f'<tr><td>{e(u["position"] or "(needs)")}</td><td>{e(u["system"])}</td><td>{e(u["method"])}</td><td>{e(u["why"])}</td></tr>'
                  for u in c["unknown"])
    needed = sum(1 for r in c["cells"] if r["needed"] and r["live"])
    return f"""<section class="tiles">{tile("Needed cells", num(needed), f"{len(c['systems_with_needs'])} systems list needs")}
{tile("Without owner", num(len(c["gaps"])), "needed, live, no decider")}{tile("3+ doers", num(len(c["overlaps"])))}
{tile("Two deciders", num(len(c["two_deciders"])))}{tile("Positions with owns", num(len(c["positions_with_owns"])))}</section>
<p class="legend">A cell is a system or subsystem × a kind of work (RFC 0007 §2.3), from the system's <code>needs:</code> and the positions'
<code>owns:</code>. <span class="cell k">D2</span> a decider and 2 doers · <span class="cell undecl">1</span> needed, no decider ·
<span class="cell w">D3</span> 3 or more doers, or two deciders · <span class="cell decl">D1</span> held, not in the system's needs.
Only active positions hold cells; a cell on a parent system does not cover its subsystems'. Hover a cell for names.</p>
<div class="scroll"><table class="matrix"><thead><tr><th></th>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>
<h2>Needed cells with no owner <span class="sub">live systems; the structure keeper maps a position onto each, or the system architect drops the need</span></h2>
{f'<table><thead><tr><th>System</th><th>Kind of work</th><th>Why</th><th>Doers</th><th>Held by inactive</th></tr></thead><tbody>{gaps}</tbody></table>' if gaps else '<p class="quiet">None.</p>'}
<h2>Cells with {of_model.OVERLAP_DOERS} or more doers</h2>
{f'<table><thead><tr><th>System</th><th>Kind of work</th><th>Doers</th><th>Decides</th><th>Who</th></tr></thead><tbody>{over}</tbody></table>' if over else '<p class="quiet">None.</p>'}
{f'<h2>Two deciders on one cell</h2><table><thead><tr><th>System</th><th>Kind of work</th><th>Decide</th></tr></thead><tbody>{two}</tbody></table>' if two else ''}
{f'<h2>Unknown in owns or needs <span class="sub">a system or kind of work the registry does not define</span></h2><table><thead><tr><th>Position</th><th>System</th><th>Kind of work</th><th>Why</th></tr></thead><tbody>{unk}</tbody></table>' if unk else ''}
<p class="note">The same table: <code>of model cells [--json]</code>.</p>"""


def view_waits(d):
    w = d.waits
    grid, bucket, totals = w["grid"], w["bucket_s"], w.get("totals") or {}
    a, b = of_model.epoch(w["from"]), of_model.epoch(w["to"])
    hours = int((b - a) // bucket)
    first = int(a // bucket) * bucket
    names = sorted(grid, key=lambda n: -sum(totals.get(n, {}).values()))
    cw, ch, left, top, right = 5, 16, 190, 22, 220
    plot = cw * (hours + 1)
    width, height = left + plot + right, top + ch * max(1, len(names)) + 10
    svg = [f'<svg class="heat" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" '
           f'aria-label="agent waits by position and hour, coloured by cause">']
    for i in range(0, hours + 1, 24):
        x = left + i * cw
        svg.append(f'<line class="day" x1="{x}" y1="{top - 4}" x2="{x}" y2="{height}"/>'
                   f'<text class="axis" x="{x + 2}" y="{top - 8}">{e(of_model.iso(first + i * bucket)[5:10])}</text>')
    for r, n in enumerate(names):
        y = top + r * ch
        svg.append(f'<text class="axis name" x="{left - 6}" y="{y + ch - 4}">{e(n[:28])}</text>')
        for slot, causes in grid[n].items():
            i = int((of_model.epoch(slot) - first) // bucket)
            cause, mins = max(causes.items(), key=lambda kv: kv[1])
            level = min(4, 1 + int(sum(causes.values()) // 15))
            cls = CAUSE_CLASS[of_measures.cause_class(cause)]
            detail = ", ".join(f"{c} {round(m)} min" for c, m in sorted(causes.items(), key=lambda kv: -kv[1]))
            svg.append(f'<rect class="{cls} l{level}" x="{left + i * cw}" y="{y + 1}" width="{cw - 1}" height="{ch - 2}">'
                       f'<title>{e(n)} · {e(slot[5:16])} UTC · {e(detail)}</title></rect>')
        # the position's total by cause, as a stacked bar (flow: where its waiting went)
        x, tot = left + plot + 12, totals.get(n, {})
        scale = 150.0 / max(1.0, max((sum(v.values()) for v in totals.values()), default=1.0))
        for c in of_measures.CAUSES:
            m = tot.get(c, 0)
            if m:
                wpx = max(1.0, m * scale)
                svg.append(f'<rect class="{CAUSE_CLASS[c]}" x="{x:.1f}" y="{y + 3}" width="{wpx:.1f}" height="{ch - 6}">'
                           f'<title>{e(n)} · {e(CAUSE_LABEL[c])} · {round(m / 60, 1)} h in the window</title></rect>')
                x += wpx
        svg.append(f'<text class="axis" x="{x + 4:.1f}" y="{y + ch - 4}">{round(sum(tot.values()) / 60, 1)} h</text>')
    svg.append("</svg>")
    legend = " ".join(f'<span class="sw {CAUSE_CLASS[k]}"></span>{e(CAUSE_LABEL[k])}' for k in of_measures.CAUSES)
    sums = {c: sum(t.get(c, 0) for t in totals.values()) / 60 for c in of_measures.CAUSES}
    tiles = "".join(tile(f"Hours {CAUSE_LABEL[c]}", num(round(sums[c], 1))) for c in of_measures.CAUSES if sums[c] or c in ("owner", "peer"))
    dec = "".join(f'<tr><td>{e(n)}</td><td>{e(c)}</td></tr>' for n, c in sorted(w["declared"].items()))
    body = "".join(svg) if names else '<p class="quiet">No measured waits in the window.</p>'
    return f"""{empty_note(d)}<section class="tiles">{tiles}</section>
<h2>Agent waits <span class="sub">position × hour, coloured by the cause; darker = longer; right: the week's total by cause</span></h2>
<p class="legend">{legend}</p><div class="scroll">{body}</div>
<p class="note">Sources: open asks, <code>of status</code> (of.state_declared), <code>of pulse</code> (stalls, herdr-blocked panes,
snapshots between runs). A minute counts once, for the first cause in the legend's order.</p>
<h2>Declared in the registry <span class="sub">waiting_on, for positions with no measured wait</span></h2>
{f'<table><thead><tr><th>Position</th><th>Waiting on</th></tr></thead><tbody>{dec}</tbody></table>' if dec else '<p class="quiet">None.</p>'}
{_queues_table(getattr(d, "queues", None))}"""


def _queues_table(qs):
    """Relayed messages waiting for a position on the server (of.inbound_queue): who receives more than it takes in."""
    head = ('<h2>Inbound queues <span class="sub">relayed messages waiting for a position to take them; '
            f'over {of_measures.QUEUE_OLD_S // 60} min is a stall (of pulse)</span></h2>')
    if not qs:
        return head + ('<p class="quiet">No queue changes recorded in the window (the relay writes '
                       '<code>of.inbound_queue</code> since 2026-11-06).</p>')
    order = sorted(qs.items(), key=lambda kv: (-kv[1]["backlog_min"], -kv[1]["depth_now"], -kv[1]["depth_max"], kv[0]))
    trs = "".join(
        f'<tr><td>{e(p)}</td><td class="num">{v["depth_now"]}</td>'
        f'<td class="num{" flagged" if (v["oldest_min_now"] or 0) * 60 > of_measures.QUEUE_OLD_S else ""}">'
        f'{f'{int(v["oldest_min_now"])} min' if v["oldest_min_now"] is not None else "–"}</td>'
        f'<td class="num">{v["depth_max"]}</td><td class="num">{v["depth_avg"]}</td>'
        f'<td class="num">{num(round(v["backlog_min"] / 60, 1), " h")}</td><td class="src">{e(v["observed_from"][:16])}</td></tr>'
        for p, v in order)
    return (head + '<table><thead><tr><th>Position</th><th>Waiting now</th><th>Oldest now</th><th>Most</th>'
            '<th>Average</th><th>Oldest over 30 min</th><th>Observed from</th></tr></thead>'
            f'<tbody>{trs}</tbody></table><p class="legend">Average is time-weighted over the observed part of the '
            'window. Ages before 2026-11-06 12:53 UTC are upper bounds (the relay then recorded changes of depth only).</p>')


# ------------------------------------------------------------------ the page cache (docs/studio.md §5.1a)
# A page takes 12 to 30 s to compute (the model, the measures and diagnose over a week of events), so of-server serves
# pages computed beforehand: `of studio --render-cache` computes every organization once in its own process and writes
# each default page (every view, and the tenant's inbox across organizations) under the state dir. A request older than
# PAGE_STALE_S starts one refresh in the background and is served the cached page meanwhile, its age in the footer.
# Requests the cache does not hold (a unit owner's view, a work or a structure parameter, profile 2's reader) are
# computed as before.

PAGE_STALE_S = 300
REFRESH_LOCK_S = 600   # at most one start per this many seconds; the render's flock keeps a second one out
REFRESH_MAX_S = 1200   # a render still running after this is stopped (SIGALRM): its lock goes with it
REFRESH_NICE = 10      # renders run below the founder's own work (Ghostty lagged at load ~50, 2026-11-10)


def _pages_dir():
    import of_events
    return os.path.join(of_events.STATE_DIR, "studio", "pages")


def _page_path(org, view, show_all=False):
    return os.path.join(_pages_dir(), org, view + ("__all" if show_all else "") + ".html")


def _private_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    for d in (_pages_dir(), path):   # makedirs gives the mode to the last directory only
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass


def _write_page(path, body):
    _private_dir(os.path.dirname(path))
    tmp = f"{path}.{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.replace(tmp, path)


def _render_lock(path=None):
    """The render's exclusive lock, held for its lifetime and released when it exits, killed or not: an open file
    with an flock, or None when another render holds it (2026-11-10: two renders ran at once, one for 11+ minutes,
    while the Mac's load average was ~50; the old lock was a file's age, and renders took longer than it)."""
    import fcntl
    path = path or os.path.join(_pages_dir(), ".refresh.lock")
    try:
        _private_dir(os.path.dirname(path))
        fh = open(path, "a")
    except OSError:
        return None
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def render_cache(orgs_file=None, log=None):
    """Compute every organization once and write each cacheable page. Returns a one-line summary. One render at a
    time: a second one returns at once."""
    lock = _render_lock()
    if lock is None:
        return "another render is running: nothing done"
    try:
        return _render_cache(orgs_file, log)
    finally:
        lock.close()


def _render_cache(orgs_file=None, log=None):
    t0 = time.time()
    parts, written = [], 0
    for o in orgs(orgs_file):
        try:
            d = Data(o["name"], orgs_file=orgs_file)
        except (KeyError, OSError) as exc:
            if log:
                log(f"{o['name']}: not computed: {exc}")
            continue
        parts.append(d)
        for view, _ in VIEWS:
            _write_page(_page_path(d.org, view), render(view, d, {"org": d.org}))
            written += 1
        _write_page(_page_path(d.org, "coverage", True), render("coverage", d, {"org": d.org, "all": "1"}))
        written += 1
    if parts:
        whole = AllData(parts) if len(parts) > 1 else parts[0]
        _write_page(_page_path(ALL, "inbox"), render("inbox", whole, {}))
        written += 1
    return f"{written} pages for {len(parts)} organizations in {time.time() - t0:.1f} s"


def _refresh_in_background():
    """Start `of studio --render-cache` once: a lock file names the running refresh and when it started.
    The listener calls this name every 10 minutes (bin/of_listen.py, of_platform): renaming it needs a note to them."""
    lock = os.path.join(_pages_dir(), ".refresh")
    try:
        if time.time() - os.path.getmtime(lock) < REFRESH_LOCK_S:
            return False
    except OSError:
        pass
    held = _render_lock()
    if held is None:   # a render is still running: never a second one beside it
        return False
    held.close()
    try:
        _private_dir(_pages_dir())
        with open(lock, "w") as fh:
            fh.write(str(time.time()))
        import subprocess
        import sys
        cli = os.path.join(os.path.dirname(os.path.realpath(__file__)), "of")
        log = os.fdopen(os.open(os.path.join(_pages_dir(), ".refresh.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a")
        env = dict(os.environ, OF_AUTOMATION="1")
        cmd = f'"{sys.executable}" "{cli}" studio --render-cache; rm -f "{lock}"'
        subprocess.Popen(["/bin/sh", "-c", cmd], stdout=log, stderr=log, stdin=subprocess.DEVNULL, env=env,
                         start_new_session=True)
        return True
    except OSError:
        return False


def cached_page(view, q, now=None):
    """(html bytes, age in seconds) of a cached page for this request, or None. Starts a refresh when stale."""
    if set(q) - {"org", "all"} or (q.get("all") and view != "coverage"):
        return None
    org = q.get("org") or None
    if view == "inbox" and org in (None, "", ALL):
        org = ALL
    elif org in (None, "", ALL):
        try:
            names = [o["name"] for o in orgs()]
        except OSError:
            return None
        org = names[-1] if names else None   # Data's default: the last organization listed
    if not org:
        return None
    path = _page_path(org, view, bool(q.get("all")))
    try:
        age = (now or time.time()) - os.path.getmtime(path)
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
    except OSError:
        return None
    note = ""
    if age > PAGE_STALE_S:
        _refresh_in_background()
        note = f" · computed {_age(age)} ago, a newer one is being computed: reload in a minute"
    return body.replace("<!--of:age-->", e(note), 1).encode(), age


def _age(sec):
    sec = int(sec)
    return f"{sec // 60} min" if sec < 2 * 3600 else f"{sec // 3600} h"


# ------------------------------------------------------------------ entry points

def render(view, d, query=None, inline_css=None):
    query = query or {}
    if view == "owner":
        body = view_owner(d)
    elif view == "coverage":
        body = view_coverage(d, show_all=bool(query.get("all")))
    elif view == "waits":
        body = view_waits(d)
    elif view == "systems":
        body = view_systems(d)
    elif view == "cells":
        body = view_cells(d)
    elif view == "review":
        body = view_review(d)
    elif view == "works":
        body = view_works(d, query.get("w"))
    elif view == "goals":
        body = view_goals(d)
    elif view == "structure":
        try:
            n = max(1, min(99, int(query.get("n")))) if query.get("n") else None
        except ValueError:
            n = None
        body = view_structure(d, n)
    else:
        view, body = "inbox", view_inbox(d)
    return page(d, view, body, inline_css=inline_css)


def css():
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "of_studio.css"), encoding="utf-8") as fh:
        return fh.read()


def _q(query):
    if isinstance(query, str):
        query = urllib.parse.parse_qs(query)
    return {k: (v[0] if isinstance(v, list) else v) for k, v in (query or {}).items()}


def unavailable(exc):
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Org Studio · registry not available</title><link rel="stylesheet" href="/studio/static/studio.css"></head>
<body><header><div class="brand">Org Studio</div></header><main>
<h2>Registry not available</h2>
<p class="note">Studio builds every view from the organization's registry (<code>registry/positions.yaml</code>), found through
<code>orgs.yaml</code> (<code>OF_ORGS</code>). On a server, enable the registry checkout (<code>grants.gitSync</code>).</p>
<p class="quiet">{e(type(exc).__name__)}: {e(getattr(exc, "filename", None) or exc)}</p>
</main></body></html>"""


def build(view, org, source=None):
    """The page's data: for the inbox with no organization (or `all`), every organization the reader serves."""
    if view == "inbox" and org in (None, "", ALL):
        names = [o["name"] for o in orgs()]
        if len(names) > 1:
            parts = []
            for n in names:
                try:
                    parts.append(Data(n, source=source))
                except KeyError:  # not served by this server
                    continue
            if len(parts) > 1:
                return AllData(parts)
            if parts:
                return parts[0]
    return Data(None if org == ALL else org, source=source)


def handle(method, path, query=None, viewer=None, source=None, page=None):
    """of-server's mount point for /studio/. Returns (status, content_type, body bytes).
    source: of-server's reader (events(org, since_iso, names), transcript_index(org, since_iso)) in profile 2;
    None in profile 1, where the local store is read. page: a prepared Data, for tests."""
    viewer = viewer or {}
    if method != "GET":
        return 405, "text/plain; charset=utf-8", b"method not allowed"
    unit = None
    if viewer.get("scope") == "unit":
        unit = viewer.get("owner")
        if not unit:
            return 403, "text/plain; charset=utf-8", b"studio: a unit viewer must name its owner"
    elif not viewer.get("tenant") and viewer.get("scope") != "org":
        return 403, "text/plain; charset=utf-8", b"studio: this viewer has no scope yet"
    sub = path.split("/studio", 1)[-1].strip("/")
    if sub == "static/studio.css":
        return 200, "text/css; charset=utf-8", css().encode()
    q = _q(query)
    view = sub or "inbox"
    if view not in dict(VIEWS):
        return 404, "text/plain; charset=utf-8", b"not found"
    if page is None and source is None and not unit:
        hit = cached_page(view, q)
        if hit:
            return 200, "text/html; charset=utf-8", hit[0]
    try:
        if page is None and unit:  # a colleague: their unit in their organization only, never "all"
            org = viewer.get("org") or q.get("org")
            d = Data(None if org in (None, ALL) else org, source=source, unit=unit)
            if unit not in d.full["positions"] and unit not in d.full["units"]:
                return 403, "text/plain; charset=utf-8", b"studio: no such owner in this organization"
            d.orgs = [d.org]
        else:
            d = page or build(view, q.get("org"), source)
    except KeyError:  # of-server's reader does not serve this organization
        return 404, "text/plain; charset=utf-8", b"studio: this server does not serve that organization"
    except OSError as exc:  # no orgs.yaml (OF_ORGS) or no registry checkout: say so, never a 500
        return 503, "text/html; charset=utf-8", unavailable(exc).encode()
    body = render(view, d, q)
    if page is None and source is None and not unit and d.org and not (set(q) - {"org", "all"}):
        try:   # a cold cache: keep this page, and compute the others in the background
            _write_page(_page_path(d.org, view, bool(q.get("all")) and view == "coverage"), body)
            _refresh_in_background()
        except OSError:
            pass
    return 200, "text/html; charset=utf-8", body.encode()


def write_file(path, org=None, view="inbox"):
    """`of studio --file`: one static page with the stylesheet inlined (for a look before the mount exists)."""
    d = build(view, org)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render(view, d, inline_css=css()))
    return path
