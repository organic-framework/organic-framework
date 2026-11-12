"""Spend per goal (docs/registry.md, Goals, "Budgets"; owned by of_capacity).

Each API response's fresh tokens (input, cache writes and output) and its cost at list prices (cache reads included)
are attributed session -> position -> work -> goal, and summed per calendar month (UTC):

- a response goes to the position whose holder its session was at that moment: the positions the session's events
  name, each over the span of its events (the one with more events wins where two overlap; aliases resolve to the
  canonical name), else the registry's holder.session and of.holder_changed;
- from the position to a work: the work it last declared with `of status --work` until it declares
  another; without one, its one open work when it is a doer of exactly one; or the goal, when all its open works
  serve one goal; else unattributed under the position;
- a goal's spend is its works', what went to it directly, and its sub-goals'; a condition's, its works'.

The responses come from the event store's transcript records (each response once, by message id; subagents to their
session; a fork's copies of its parent's records left out), kept in `view_spend_month` from the start of the
previous month. The listener advances it in of_capacity's tick and writes the month's sums to
`~/.local/state/of/capacity/goal-spend.json` at most every 30 minutes; `of works` and Studio read that file
(`load()`); `of capacity spend` prints it. Nothing here stops work: the warning and the card are of_platform's.
"""

import calendar
import datetime as dt
import json
import os
import time

import of_events

VERSION = "1"                 # bump to rebuild view_spend_month from the first record
TICK_BUDGET_S = 20            # the listener's share per tick while the view catches up
REFRESH_S = 1800              # the sums are recomputed at most this often by the listener
DECLARING = ("of.state_declared",)  # `of status --work` only: an ask about a work is not the work being done (PR 86)
DONE = {"done", "parked", "park"}

MONTH_DDL = """
CREATE TABLE IF NOT EXISTS view_spend_month (message_id TEXT PRIMARY KEY, session_id TEXT, epoch REAL,
  fresh INTEGER, cost REAL);
CREATE INDEX IF NOT EXISTS view_spend_month_session ON view_spend_month (session_id, epoch);
"""


def cache_path():
    return os.path.join(of_events.STATE_DIR, "capacity", "goal-spend.json")


def _epoch(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    try:
        return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def month_bounds(month=None, now=None):
    """(start, end) epochs of a calendar month in UTC; `month` is 'YYYY-MM', default the one `now` is in."""
    if month:
        y, m = (int(x) for x in str(month).split("-")[:2])
    else:
        t = time.gmtime(now or time.time())
        y, m = t.tm_year, t.tm_mon
    start = calendar.timegm((y, m, 1, 0, 0, 0))
    end = calendar.timegm((y + (m == 12), m % 12 + 1, 1, 0, 0, 0))
    return start, end


def _month_name(start):
    return time.strftime("%Y-%m", time.gmtime(start))


# ------------------------------------------------------------------ the responses


def _meta(conn, key, value=None):
    conn.execute("CREATE TABLE IF NOT EXISTS listener_meta (key TEXT PRIMARY KEY, value TEXT)")
    if value is None:
        r = conn.execute("SELECT value FROM listener_meta WHERE key = ?", (key,)).fetchone()
        return r[0] if r else None
    conn.execute("INSERT OR REPLACE INTO listener_meta VALUES (?, ?)", (key, str(value)))


def ingest(conn, now, budget_s=TICK_BUDGET_S, batch=5000):
    """Advance view_spend_month over the transcript records by its own cursor, within `budget_s` seconds. Keeps the
    responses from the start of the previous month. Returns (rows read, caught up)."""
    import of_capacity
    conn.executescript(MONTH_DDL)
    if _meta(conn, "spend_month_version") != VERSION:
        conn.execute("DELETE FROM view_spend_month")
        _meta(conn, "spend_month_cursor", 0)
        _meta(conn, "spend_month_caught_up", 0)
        _meta(conn, "spend_month_version", VERSION)
    this_start, _ = month_bounds(now=now)
    keep_from = month_bounds(now=this_start - 1)[0]
    cur, n, t0 = int(_meta(conn, "spend_month_cursor") or 0), 0, time.monotonic()
    own = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'transcript_own'").fetchone() is not None
    src = "transcript_own" if own else "transcript_record"
    caught_up = False
    while time.monotonic() - t0 < budget_s:
        rows = conn.execute(
            "SELECT id, source_path, session_id, message_id, timestamp, input_tokens, output_tokens, "
            "cache_read_tokens, cache_write_tokens, cache_write_5m_tokens, cache_write_1h_tokens "
            f"FROM {src} WHERE id > ? ORDER BY id LIMIT ?", (cur, batch)).fetchall()
        if not rows:
            caught_up = True
            break
        keep = []
        for rid, path, sid, mid, ts, inp, out, cr, cw, cw5, cw1 in rows:
            if not mid or inp is None:
                continue
            t = _epoch(ts)
            if not t or t < keep_from or not of_capacity._CONFIG_DIR_RE.match(path or ""):
                continue
            if f"{os.sep}subagents{os.sep}" in (path or ""):
                sid = path.split(os.sep)[-3]
            w5, w1 = cw5, cw1
            if w5 is None and w1 is None:
                w5, w1 = 0, cw or 0
            fresh = (inp or 0) + (out or 0) + (w5 or 0) + (w1 or 0)
            keep.append((mid, sid, t, fresh, of_capacity.row_cost(inp, out, cr, cw, cw5, cw1)))
        cur, n = rows[-1][0], n + len(rows)
        # one short write per batch, its cursor with it: the store's other writers (hooks, the listener's views)
        # wait milliseconds, never the whole slice, and a stop loses nothing
        own_tx = not conn.in_transaction
        if own_tx:
            conn.execute("BEGIN IMMEDIATE")
        conn.executemany("INSERT OR REPLACE INTO view_spend_month VALUES (?,?,?,?,?)", keep)
        _meta(conn, "spend_month_cursor", cur)
        if own_tx:
            conn.execute("COMMIT")
    if caught_up:
        _meta(conn, "spend_month_caught_up", 1)
    conn.execute("DELETE FROM view_spend_month WHERE epoch < ?", (keep_from,))
    return n, caught_up


# ------------------------------------------------------------------ who held the session, on which work


def canon_map(docs):
    """{(orgid or org or None, name or alias): (org, canonical name)} for every listed registry."""
    out = {}
    for org, doc in docs.items():
        ids = {org, str(doc.get("organization") or org)}
        for p in doc.get("positions") or []:
            if not isinstance(p, dict) or not p.get("name"):
                continue
            for n in [p["name"]] + [str(a) for a in p.get("aliases") or []]:
                for o in ids:
                    out.setdefault((o, n), (org, p["name"]))
                out.setdefault((None, n), (org, p["name"]))
    return out


def owner_spans(groups, canon, fallback=None):
    """{session: [(first, last, events, (org, position))]} from (session, org, position, events, first ts, last ts)
    groups of the event store, canonical names, the same position's groups merged. `fallback`: {session: (org,
    position)} for sessions no event names (holder.session, of.holder_changed), held over all time."""
    merged = {}
    for sid, org, name, k, first, last in groups:
        hit = canon.get((org, name)) or canon.get((None, name))
        a, b = _epoch(first), _epoch(last)
        if not sid or not hit or a is None or b is None:
            continue
        cur = merged.setdefault(sid, {}).get(hit)
        merged[sid][hit] = (min(a, cur[0]), max(b, cur[1]), k + cur[2]) if cur else (a, b, k)
    out = {sid: sorted((a, b, k, who) for who, (a, b, k) in m.items()) for sid, m in merged.items()}
    for sid, who in (fallback or {}).items():
        out.setdefault(sid, [(float("-inf"), float("inf"), 0, who)])
    return out


def owner_at(spans, t):
    """The (org, position) a session's spans give at time t: of those whose span holds t, the one with the most
    events; else the nearest span, an earlier one first."""
    inside = [s for s in spans if s[0] <= t <= s[1]]
    if inside:
        return max(inside, key=lambda s: (s[2], s[0]))[3]
    before = [s for s in spans if s[1] < t]
    if before:
        return max(before, key=lambda s: s[1])[3]
    return min(spans, key=lambda s: s[0])[3] if spans else None


def declarations(rows, canon):
    """{(org, position): [(epoch, work)]} sorted, from (ts, org, position, payload) of declaring events."""
    out = {}
    for ts, org, name, pl in rows:
        try:
            work = (json.loads(pl) if isinstance(pl, str) else (pl or {})).get("work")
        except ValueError:
            continue
        hit = canon.get((org, name)) or canon.get((None, name))
        t = _epoch(ts)
        if work and hit and t is not None:
            out.setdefault(hit, []).append((t, str(work)))
    return {k: sorted(v) for k, v in out.items()}


def fallback_target(org, position, doc):
    """Where an undeclared response goes: ('work', id) for the one open work the position is a doer of; ('goal', id)
    when its open works all serve one goal; ('none', None) otherwise."""
    works = doc.get("works") if isinstance(doc.get("works"), dict) else {}
    mine = [(wid, w) for wid, w in works.items() if isinstance(w, dict)
            and position in [str(x) for x in w.get("doers") or []] and str(w.get("state") or "") not in DONE]
    if len(mine) == 1:
        return ("work", mine[0][0])
    goals = {str(w.get("goal")) if w.get("goal") else None for _, w in mine}
    if mine and len(goals) == 1 and None not in goals:
        return ("goal", goals.pop())
    return ("none", None)


def segments(spans, decl, start, end):
    """[(session, a, b, org, position, work or None)]: the month cut where the session's owner or the owner's
    declared work changes."""
    out = []
    for sid, sp in spans.items():
        cuts = {start, end}
        for a, b, _, _ in sp:
            for x in (a, b):
                if start < x < end:
                    cuts.add(x)
        cuts = sorted(cuts)
        for a, b in zip(cuts, cuts[1:]):
            who = owner_at(sp, (a + b) / 2)
            if not who:
                continue
            ds = decl.get(who) or []
            inner = sorted({a} | {t for t, _ in ds if a < t < b} | {b})
            for x, y in zip(inner, inner[1:]):
                work = None
                for t, w in ds:
                    if t <= x:
                        work = w
                    else:
                        break
                out.append((sid, x, y, who[0], who[1], work))
    return out


# ------------------------------------------------------------------ the sums


def _zero():
    return {"fresh": 0, "cost_usd": 0.0, "responses": 0}


def _add(a, b):
    a["fresh"] += b["fresh"]
    a["cost_usd"] += b["cost_usd"]
    a["responses"] += b["responses"]
    return a


def _round(x):
    if isinstance(x, dict):
        return {k: (round(v, 4) if k == "cost_usd" else _round(v)) for k, v in x.items()}
    return x


def summarize(seg_sums, docs, start, end, now, totals=None):
    """The month's sums. seg_sums: [(org, position, work or None, {fresh, cost_usd, responses})]."""
    orgs = {}
    for org in docs:
        orgs[org] = {"works": {}, "goals": {}, "conditions": {}, "unattributed": {}, "by_method": {}}
    unowned = _zero()
    for org, pos, work, s in seg_sums:
        if org is None:
            _add(unowned, s)
            continue
        doc = docs.get(org) or {}
        o = orgs.setdefault(org, {"works": {}, "goals": {}, "conditions": {}, "unattributed": {}, "by_method": {}})
        works = doc.get("works") if isinstance(doc.get("works"), dict) else {}
        if work:
            kind, target, method = "work", work, "declared"
        else:
            kind, target = fallback_target(org, pos, doc)
            method = {"work": "one_open_work", "goal": "one_goal", "none": "unattributed"}[kind]
        _add(o["by_method"].setdefault(method, _zero()), s)
        if kind == "work":
            w = works.get(target) if isinstance(works.get(target), dict) else {}
            row = o["works"].setdefault(target, dict(_zero(), goal=w.get("goal"), condition=w.get("condition"),
                                                     known=target in works, positions={}))
            _add(row, s)
            _add(row["positions"].setdefault(pos, _zero()), s)
        elif kind == "goal":
            g = o["goals"].setdefault(target, {"direct": _zero()})
            _add(g.setdefault("direct", _zero()), s)
        else:
            _add(o["unattributed"].setdefault(pos, _zero()), s)
    elapsed = max(0.0, min(1.0, (min(now, end) - start) / (end - start)))
    for org, o in orgs.items():
        doc = docs.get(org) or {}
        goals = doc.get("goals") if isinstance(doc.get("goals"), dict) else {}
        conds = doc.get("conditions") if isinstance(doc.get("conditions"), dict) else {}
        for gid in goals:
            o["goals"].setdefault(gid, {"direct": _zero()})
        for gid, g in o["goals"].items():
            spec = goals.get(gid) if isinstance(goals.get(gid), dict) else {}
            g.update(parent=spec.get("parent"), known=gid in goals, works=_zero(), total=_zero())
            g.setdefault("direct", _zero())
        for wid, w in o["works"].items():
            gid = w.get("goal")
            if gid:
                g = o["goals"].setdefault(gid, {"direct": _zero(), "parent": None, "known": False, "works": _zero(),
                                                "total": _zero()})
                _add(g["works"], w)
            cid = w.get("condition")
            if cid:
                o["conditions"].setdefault(cid, {"total": _zero(), "known": cid in conds})
                _add(o["conditions"][cid]["total"], w)
        for cid in conds:
            o["conditions"].setdefault(cid, {"total": _zero(), "known": True})
        children = {}
        for gid, g in o["goals"].items():
            children.setdefault(g.get("parent"), []).append(gid)

        def total(gid, seen=()):
            g = o["goals"][gid]
            t = _add(_add(_zero(), g["direct"]), g["works"])
            for c in children.get(gid, []):
                if c not in seen and c != gid:  # a cycle is of registry check's finding, never a hang here
                    _add(t, total(c, seen + (gid,)))
            g["total"] = t
            return t
        for gid in o["goals"]:
            total(gid)
        for kind, specs in (("goals", goals), ("conditions", conds)):
            for nid, node in o[kind].items():
                spec = specs.get(nid) if isinstance(specs.get(nid), dict) else {}
                b = spec.get("budget") if isinstance(spec.get("budget"), dict) else None
                t = node["total"]
                node["forecast"] = ({"fresh": int(t["fresh"] / elapsed), "cost_usd": t["cost_usd"] / elapsed}
                                    if elapsed > 0 else None)
                if b:
                    node["budget"] = {k: b[k] for k in ("tokens_month", "usd_month") if isinstance(b.get(k), (int, float))}
                    node["share"] = {
                        **({"tokens": t["fresh"] / b["tokens_month"]} if b.get("tokens_month") else {}),
                        **({"usd": t["cost_usd"] / b["usd_month"]} if b.get("usd_month") else {})}
    total_all = _zero()
    for org, pos, work, s in seg_sums:
        _add(total_all, s)
    return _round({"v": 1, "month": _month_name(start), "from": start, "to": end, "computed_at": now,
                   "elapsed": elapsed, "total": total_all, "unowned": unowned, "orgs": orgs, **(totals or {})})


# ------------------------------------------------------------------ the run


def _store_inputs(docs, conn):
    """The event store's groups, holder changes and declarations, and the registry's holder.session."""
    groups = conn.execute("SELECT session_id, org, position, count(*), min(ts), max(ts) FROM event "
                          "WHERE session_id IS NOT NULL AND position IS NOT NULL AND position != '' "
                          "GROUP BY session_id, org, position").fetchall()
    canon = canon_map(docs)
    fallback = {}
    for ts, pl in conn.execute("SELECT ts, payload FROM event WHERE name = 'of.holder_changed' ORDER BY ts"):
        try:
            j = json.loads(pl or "{}")
        except ValueError:
            continue
        hit = canon.get((None, j.get("position"))) if j.get("position") else None
        if hit:
            for k in ("previous_session", "new_session"):
                if j.get(k):
                    fallback[j[k]] = hit
    for org, doc in docs.items():
        for p in doc.get("positions") or []:
            sid = (((p or {}).get("holder") or {}).get("session") or "").strip() if isinstance(p, dict) else ""
            if sid:
                fallback[sid] = (org, p["name"])
    ph = ",".join("?" * len(DECLARING))
    decl_rows = conn.execute(f"SELECT ts, org, position, payload FROM event WHERE name IN ({ph}) "
                             "AND payload LIKE '%\"work\"%'", DECLARING).fetchall()
    return owner_spans(groups, canon, fallback), declarations(decl_rows, canon)


def compute(docs, conn, month=None, now=None):
    """The month's attribution and sums from view_spend_month and the event store (`conn`: the store)."""
    now = now or time.time()
    start, end = month_bounds(month, now)
    conn.executescript(MONTH_DDL)
    spans, decl = _store_inputs(docs, conn)
    seen = set(spans)
    seg_sums = []
    for sid, a, b, org, pos, work in segments(spans, decl, start, end):
        f, c, k = conn.execute("SELECT COALESCE(SUM(fresh), 0), COALESCE(SUM(cost), 0), count(*) FROM view_spend_month "
                               "WHERE session_id = ? AND epoch >= ? AND epoch < ?", (sid, a, b)).fetchone()
        if k:
            seg_sums.append((org, pos, work, {"fresh": int(f), "cost_usd": float(c), "responses": int(k)}))
    ph = ",".join("?" * len(seen)) or "''"
    f, c, k = conn.execute(f"SELECT COALESCE(SUM(fresh), 0), COALESCE(SUM(cost), 0), count(*) FROM view_spend_month "
                           f"WHERE epoch >= ? AND epoch < ? AND session_id NOT IN ({ph})",
                           [start, end] + sorted(seen)).fetchone()
    if k:
        seg_sums.append((None, None, None, {"fresh": int(f), "cost_usd": float(c), "responses": int(k)}))
    out = summarize(seg_sums, docs, start, end, now)
    out["complete"] = _meta(conn, "spend_month_caught_up") == "1"
    return out


def write(out, path=None):
    path = path or cache_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(out, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


def load(path=None):
    """The last sums the listener wrote (this month's), or None."""
    try:
        with open(path or cache_path()) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def tick(conn, now, docs_fn=None):
    """of_capacity's listener tick: advance the view; recompute the sums at most every REFRESH_S."""
    _, caught_up = ingest(conn, now)
    last = load()
    if not caught_up or (last and now - (last.get("computed_at") or 0) < REFRESH_S
                         and last.get("month") == _month_name(month_bounds(now=now)[0])):
        return False
    if docs_fn is None:
        import of_tools
        docs_fn = of_tools.registries
    write(compute(docs_fn(), conn, now=now))
    return True


# ------------------------------------------------------------------ the text


def _num(n):
    n = n or 0
    return f"{n / 1e9:.2f}B" if n >= 1e9 else f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}k" if n >= 1e3 else str(int(n))


def _line(label, s, extra=""):
    return f"  {label:<44} {_num(s['fresh']):>8}  ${s['cost_usd']:>10,.2f}{extra}"


def _budget(node):
    sh, b, fc = node.get("share") or {}, node.get("budget") or {}, node.get("forecast") or {}
    bits = []
    if "tokens" in sh:
        bits.append(f"{100 * sh['tokens']:.0f}% of {_num(b['tokens_month'])}")
    if "usd" in sh:
        bits.append(f"{100 * sh['usd']:.0f}% of ${b['usd_month']:,.0f}")
    if fc and bits:
        bits.append(f"forecast {_num(fc['fresh'])} ${fc['cost_usd']:,.0f}")
    return ("  [" + "; ".join(bits) + "]") if bits else ""


def render(out, org=None):
    """`of capacity spend`: the month's sums as text."""
    if not out:
        return "no spend computed yet (the listener writes it; `of capacity spend --fresh` computes it now)"
    when = time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(out.get("computed_at") or 0))
    lines = [f"Spend in {out['month']}, fresh tokens and list cost, to {when} ({100 * out.get('elapsed', 0):.0f}% of "
             f"the month){'' if out.get('complete', True) else '; the month is still being read, so it is low'}"]
    for name, o in sorted((out.get("orgs") or {}).items()):
        if org and name != org:
            continue
        if not any((o.get(k) or {}) for k in ("works", "unattributed")) and \
                not any(g["total"]["responses"] for g in (o.get("goals") or {}).values()):
            continue
        lines.append(f"\n{name}")
        goals = o.get("goals") or {}
        kids = {}
        for gid, g in goals.items():
            kids.setdefault(g.get("parent") if g.get("parent") in goals else None, []).append(gid)

        def walk(gid, depth):
            g = goals[gid]
            lines.append(_line("  " * depth + gid + ("" if g.get("known", True) else " (not in goals:)"), g["total"],
                               _budget(g)))
            for c in sorted(kids.get(gid, [])):
                walk(c, depth + 1)
        if goals:
            lines.append(" goals (with their sub-goals)")
            for gid in sorted(kids.get(None, [])):
                walk(gid, 1)
        if o.get("conditions"):
            lines.append(" conditions")
            for cid, c in sorted(o["conditions"].items()):
                lines.append(_line("  " + cid, c["total"], _budget(c)))
        if o.get("works"):
            lines.append(" works")
            for wid, w in sorted(o["works"].items(), key=lambda x: -x[1]["cost_usd"]):
                to = w.get("goal") or w.get("condition") or "no goal"
                lines.append(_line(f"  {wid} -> {to}" + ("" if w.get("known", True) else " (not in works:)"), w))
        if o.get("unattributed"):
            lines.append(" unattributed (no --work declared, and no single open work or goal)")
            for pos, s in sorted(o["unattributed"].items(), key=lambda x: -x[1]["cost_usd"]):
                lines.append(_line("  " + pos, s))
        bm = o.get("by_method") or {}
        tot = sum(s["cost_usd"] for s in bm.values()) or 1
        lines.append(" by how: " + ", ".join(f"{k} {100 * s['cost_usd'] / tot:.0f}%" for k, s in sorted(bm.items())))
    if not org:
        lines.append("")
        lines.append(_line("sessions no position held", out.get("unowned") or _zero()))
        lines.append(_line("all", out.get("total") or _zero()))
    return "\n".join(lines)
