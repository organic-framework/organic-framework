"""Spend per goal (docs/registry.md, Goals, "Spend per goal"; owned by of_capacity).

Each API response's fresh tokens (input, cache writes and output) and its cost at list prices (cache reads included)
are attributed session -> position -> work -> goal, and summed per calendar month (UTC):

- a response goes to the position whose holder its session was at that moment: the positions the session's events
  name, each over the span of its events (the one with more events wins where two overlap; aliases resolve to the
  canonical name), else the registry's holder.session and of.holder_changed;
- from the position to a work: the work it last declared with `of status --work` until it declares
  another; without one, its one open work when it is a doer of exactly one; or the goal, when all its open works
  serve one goal; else unattributed under the position;
- a goal's spend is its works', what went to it directly, and its sub-goals'; a condition's, its works'.

A position's runs on other providers' harnesses (of.subagent_run, RFC 0008: Codex, Muse) are in no transcript. Each
ended run's tokens go the same way to its position and work (the run's own `work`, else the work the position had
declared then, else the fallback above), as `outside_fresh` (input not read from cache, plus output) and
`outside_runs`; they carry no list cost, since those plans are flat subscriptions with no price per token here.
`outside` sums them per harness and model.

The responses come from the event store's transcript records (each response once, by message id; subagents to their
session; a fork's copies of its parent's records left out), kept in `view_spend_month` from the start of the
previous month. The listener advances it in of_capacity's tick and writes the month's sums to
`~/.local/state/of/capacity/goal-spend.json` at most every 30 minutes; `of works` and Studio read that file
(`load()`); `of capacity spend` prints it. It is a reading: no node carries a budget (goal budgets were withdrawn
on 2026-11-12, docs/registry.md), and nothing warns, files a card or stops work on spend.
"""

import calendar
import datetime as dt
import json
import os
import sqlite3
import time

import of_events

VERSION = "2"                 # bump to rebuild view_spend_month from the first record (2: account, model, per-model cost)
TICK_BUDGET_S = 20            # the listener's share per tick while the view catches up
REFRESH_S = 1800              # the sums are recomputed at most this often by the listener
DECLARING = ("of.state_declared",)  # `of status --work` only: an ask about a work is not the work being done (PR 86)
DONE = {"done", "parked", "park"}

MONTH_DDL = """
CREATE TABLE IF NOT EXISTS view_spend_month (message_id TEXT PRIMARY KEY, session_id TEXT, epoch REAL,
  fresh INTEGER, cost REAL, config_dir TEXT, model TEXT);
CREATE INDEX IF NOT EXISTS view_spend_month_session ON view_spend_month (session_id, epoch);
CREATE INDEX IF NOT EXISTS view_spend_month_epoch ON view_spend_month (epoch);
"""
# a response's model to its list prices (of_capacity.MODEL_PRICES) by family when the exact name is not listed: a
# Fable response costs 2.5 times an Opus one and a Sonnet one half, so one Opus price for all misread a day by model
FAMILIES = ("fable", "opus-5", "opus-4", "sonnet", "haiku")


def response_cost(model, inp, out, cr, cw, cw5, cw1):
    """The list cost of one response at its model's prices; Opus 5.5's (of_capacity.row_cost) for a model with none."""
    import of_capacity
    prices = of_capacity.MODEL_PRICES
    p = prices.get(model or "")
    if p is None:
        fam = next((f for f in FAMILIES if f in (model or "")), None)
        p = next((v for k, v in prices.items() if fam and fam in k), None)
    if p is None:
        return of_capacity.row_cost(inp, out, cr, cw, cw5, cw1)
    if cw5 is None and cw1 is None:
        cw5, cw1 = 0, cw or 0
    return ((inp or 0) * p[0] + (out or 0) * p[1] + (cw5 or 0) * p[2] + (cw1 or 0) * p[3] + (cr or 0) * p[4]) / 1e6
# What the attribution reads of the event log, kept by a cursor over its rowid (the founder, 2026-11-14, through
# of_conductor msg 01a1d68e-8ef9: no reader projects the whole log). view_spend_owner: each (session, organization,
# position) the events name, with their count and first and last time; org '' for none. view_spend_event: the
# holder changes and the declarations of a work, the only payloads the attribution reads.
EVENTS_VERSION = "1"
EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS view_spend_owner (session_id TEXT NOT NULL, org TEXT NOT NULL, position TEXT NOT NULL,
  n INTEGER NOT NULL, first_ts TEXT, last_ts TEXT, PRIMARY KEY (session_id, org, position));
CREATE TABLE IF NOT EXISTS view_spend_event (event_rowid INTEGER PRIMARY KEY, ts TEXT, name TEXT, org TEXT,
  position TEXT, payload TEXT);
"""
EVENTS_BATCH = 20000


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
    if _meta(conn, "spend_month_version") != VERSION:
        # its columns changed: dropped, made again and read from the start, in one transaction under the write lock,
        # the version read again inside it. The tick and the command can both come here: read outside, the second
        # dropped what the first had read and reset nothing, leaving the view empty and marked complete (of_review on
        # PR 558)
        own_tx = not conn.in_transaction
        if own_tx and not _begin(conn, time.monotonic() + budget_s):
            return 0, False  # the store stayed locked: the migration at the next tick
        if _meta(conn, "spend_month_version") != VERSION:
            conn.execute("DROP TABLE IF EXISTS view_spend_month")
            for stmt in [x.strip() for x in MONTH_DDL.split(";") if x.strip()]:
                conn.execute(stmt)
            _meta(conn, "spend_month_cursor", 0)
            _meta(conn, "spend_month_caught_up", 0)
            _meta(conn, "spend_month_version", VERSION)
        if own_tx:
            conn.execute("COMMIT")
    conn.executescript(MONTH_DDL)
    this_start, _ = month_bounds(now=now)
    keep_from = month_bounds(now=this_start - 1)[0]
    cur, n, t0 = int(_meta(conn, "spend_month_cursor") or 0), 0, time.monotonic()
    own = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'transcript_own'").fetchone() is not None
    src = "transcript_own" if own else "transcript_record"
    caught_up = False
    while time.monotonic() - t0 < budget_s:
        rows = conn.execute(
            "SELECT id, source_path, session_id, message_id, timestamp, input_tokens, output_tokens, "
            "cache_read_tokens, cache_write_tokens, cache_write_5m_tokens, cache_write_1h_tokens, model "
            f"FROM {src} WHERE id > ? ORDER BY id LIMIT ?", (cur, batch)).fetchall()
        if not rows:
            caught_up = True
            break
        keep = []
        for rid, path, sid, mid, ts, inp, out, cr, cw, cw5, cw1, model in rows:
            if not mid or inp is None:
                continue
            t = _epoch(ts)
            m = of_capacity._CONFIG_DIR_RE.match(path or "")
            if not t or t < keep_from or not m:
                continue
            if f"{os.sep}subagents{os.sep}" in (path or ""):
                sid = path.split(os.sep)[-3]
            w5, w1 = cw5, cw1
            if w5 is None and w1 is None:
                w5, w1 = 0, cw or 0
            fresh = (inp or 0) + (out or 0) + (w5 or 0) + (w1 or 0)
            # the account is the config dir the record was first written under: the store keeps a record's first
            # source_path, so a move's copy of the history never credits it to the account it moved to
            keep.append((mid, sid, t, fresh, response_cost(model, inp, out, cr, cw, cw5, cw1), m.group(1), model))
        cur, n = rows[-1][0], n + len(rows)
        # one short write per batch, its cursor with it: the store's other writers (hooks, the listener's views)
        # wait milliseconds, never the whole slice, and a stop loses nothing
        own_tx = not conn.in_transaction
        if own_tx and not _begin(conn, t0 + budget_s):
            return n, False  # the store stayed locked: this batch again at the next tick, from the cursor
        conn.executemany("INSERT OR REPLACE INTO view_spend_month VALUES (?,?,?,?,?,?,?)", keep)
        _meta(conn, "spend_month_cursor", cur)
        if own_tx:
            conn.execute("COMMIT")
    began = not conn.in_transaction
    if began and not _begin(conn, time.monotonic() + 5):
        return n, caught_up  # the prune waits for a tick when the store is free
    if caught_up:
        _meta(conn, "spend_month_caught_up", 1)
    conn.execute("DELETE FROM view_spend_month WHERE epoch < ?", (keep_from,))
    if began:
        conn.execute("COMMIT")
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
    return {"fresh": 0, "cost_usd": 0.0, "responses": 0, "outside_fresh": 0, "outside_runs": 0}


def _add(a, b):
    for k, v in _zero().items():
        a[k] = a.get(k, v) + b.get(k, v)
    return a


SUBAGENT_EVENT = "of.subagent_run"


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def outside_runs(conn, canon, decl, start, end):
    """([(org, position, work or None, sums)], {"<harness>/<model>": {runs, fresh, input, cached, output,
    reasoning}}) of the runs on other providers' harnesses that ended in [start, end): of.subagent_run, phase ended
    (RFC 0008 §3.1, "Recorded"). Their tokens are in no transcript, so this is their only count."""
    segs, by = [], {}
    rows = conn.execute("SELECT ts, org, position, payload FROM event WHERE name = ? AND ts >= ? AND ts < ?",
                        (SUBAGENT_EVENT, _iso(start), _iso(end))).fetchall()
    for ts, org, pos, pl in rows:
        try:
            j = json.loads(pl or "{}")
        except ValueError:
            continue
        if j.get("phase") != "ended":
            continue
        u = j.get("usage") if isinstance(j.get("usage"), dict) else {}
        n = {k: int(u.get(k) or 0) for k in ("input", "cached", "output", "reasoning")}
        fresh = max(0, n["input"] - n["cached"]) + n["output"]
        key = f"{j.get('harness') or '?'}/{j.get('model') or '?'}"
        b = by.setdefault(key, {"runs": 0, "fresh": 0, "input": 0, "cached": 0, "output": 0, "reasoning": 0})
        b["runs"] += 1
        b["fresh"] += fresh
        for k, v in n.items():
            b[k] += v
        name = j.get("position") or pos
        hit = (canon.get((org, name)) or canon.get((None, name))) if name else None
        s = dict(_zero(), outside_fresh=fresh, outside_runs=1)
        if not hit:
            segs.append((None, None, None, s))
            continue
        t = _epoch(ts)
        work = j.get("work")
        if not work:
            for x, w in decl.get(hit) or []:
                if x <= t:
                    work = w
                else:
                    break
        segs.append((hit[0], hit[1], work, s))
    return segs, by


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
    total_all = _zero()
    for org, pos, work, s in seg_sums:
        _add(total_all, s)
    return _round({"v": 1, "month": _month_name(start), "from": start, "to": end, "computed_at": now,
                   "elapsed": elapsed, "total": total_all, "unowned": unowned, "orgs": orgs, **(totals or {})})


# ------------------------------------------------------------------ the run


def _begin(conn, deadline):
    """BEGIN IMMEDIATE, tried again while the store is locked by another writer until `deadline` (a monotonic time);
    False when it never came, so the caller stops this tick and goes on from its cursor at the next."""
    while True:
        try:
            conn.execute("BEGIN IMMEDIATE")
            return True
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) or time.monotonic() >= deadline:
                if "locked" in str(e):
                    return False
                raise
            time.sleep(0.5)


def advance_events(conn, budget_s=TICK_BUDGET_S, batch=EVENTS_BATCH):
    """Advance view_spend_owner and view_spend_event over the events after the cursor, within `budget_s` seconds,
    one short write per batch. Returns (events read, caught up)."""
    conn.executescript(EVENTS_DDL)
    if _meta(conn, "spend_events_version") != EVENTS_VERSION:
        conn.execute("DELETE FROM view_spend_owner")
        conn.execute("DELETE FROM view_spend_event")
        _meta(conn, "spend_events_cursor", 0)
        _meta(conn, "spend_events_caught_up", 0)
        _meta(conn, "spend_events_version", EVENTS_VERSION)
    names = ("of.holder_changed",) + DECLARING
    ph = ",".join("?" * len(names))
    cur, n, t0, caught_up = int(_meta(conn, "spend_events_cursor") or 0), 0, time.monotonic(), False
    while time.monotonic() - t0 < budget_s:
        rows = conn.execute(f"SELECT rowid, ts, name, session_id, org, position, CASE WHEN name IN ({ph}) THEN payload "
                            "END FROM event WHERE rowid > ? ORDER BY rowid LIMIT ?", names + (cur, batch)).fetchall()
        if not rows:
            caught_up = True
            break
        groups, keep = {}, []
        for rid, ts, name, sid, org, pos, pl in rows:
            if sid and pos:
                g = groups.setdefault((sid, org or "", pos), [0, ts, ts])
                g[0] += 1
                g[1], g[2] = min(g[1] or ts, ts or g[1]), max(g[2] or ts, ts or g[2])
            if pl is not None and (name == "of.holder_changed" or '"work"' in pl):
                keep.append((rid, ts, name, org, pos, pl))
        own_tx = not conn.in_transaction
        if own_tx and not _begin(conn, t0 + budget_s):
            return n, False  # the store stayed locked: this batch again at the next tick, from the cursor
        cur, n = rows[-1][0], n + len(rows)
        conn.executemany("INSERT INTO view_spend_owner VALUES (?,?,?,?,?,?) ON CONFLICT (session_id, org, position) "
                         "DO UPDATE SET n = n + excluded.n, first_ts = min(first_ts, excluded.first_ts), "
                         "last_ts = max(last_ts, excluded.last_ts)",
                         [(k[0], k[1], k[2], v[0], v[1], v[2]) for k, v in groups.items()])
        conn.executemany("INSERT OR REPLACE INTO view_spend_event VALUES (?,?,?,?,?,?)", keep)
        _meta(conn, "spend_events_cursor", cur)
        if own_tx:
            conn.execute("COMMIT")
    if caught_up and _meta(conn, "spend_events_caught_up") != "1":
        began = not conn.in_transaction
        if not began or _begin(conn, time.monotonic() + 5):
            _meta(conn, "spend_events_caught_up", 1)
            if began:
                conn.execute("COMMIT")
    return n, caught_up


def _events_ready(conn):
    try:
        return _meta(conn, "spend_events_caught_up") == "1" and _meta(conn, "spend_events_version") == EVENTS_VERSION
    except Exception:  # noqa: BLE001 — a store without the views: the log itself
        return False


def owner_groups(conn):
    """(session, org, position, count, first ts, last ts) of every pair the events name: from view_spend_owner once
    it has caught up, else (a store the view never read) from the log itself."""
    if _events_ready(conn):
        return conn.execute("SELECT session_id, NULLIF(org, ''), position, n, first_ts, last_ts FROM view_spend_owner"
                            ).fetchall()
    return conn.execute("SELECT session_id, org, position, count(*), min(ts), max(ts) FROM event "
                        "WHERE session_id IS NOT NULL AND position IS NOT NULL AND position != '' "
                        "GROUP BY session_id, org, position").fetchall()


def _store_inputs(docs, conn):
    """The event store's groups, holder changes and declarations, and the registry's holder.session."""
    groups = [g for g in owner_groups(conn) if g[0] and g[2]]
    ready = _events_ready(conn)
    canon = canon_map(docs)
    fallback = {}
    holder_sql = ("SELECT ts, payload FROM view_spend_event WHERE name = 'of.holder_changed' ORDER BY ts" if ready
                  else "SELECT ts, payload FROM event WHERE name = 'of.holder_changed' ORDER BY ts")
    for ts, pl in conn.execute(holder_sql):
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
    decl_rows = conn.execute(f"SELECT ts, org, position, payload FROM {'view_spend_event' if ready else 'event'} "
                             f"WHERE name IN ({ph}) AND payload LIKE '%\"work\"%'", DECLARING).fetchall()
    return owner_spans(groups, canon, fallback), declarations(decl_rows, canon)


def advance(conn, now, budget_s=TICK_BUDGET_S):
    """Both cursors: the month's responses and the events the attribution reads. Returns whether both caught up."""
    _, a = ingest(conn, now, budget_s=budget_s)
    _, b = advance_events(conn, budget_s=budget_s)
    return a and b


def compute(docs, conn, month=None, now=None, here=None):
    """The month's attribution and sums from view_spend_month and the event store (`conn`: the store): this machine's
    own spend, a carried session's history left to the machine it was spent on (away_spans)."""
    now = now or time.time()
    start, end = month_bounds(month, now)
    conn.executescript(MONTH_DDL)
    spans, decl = _store_inputs(docs, conn)
    away = away_spans(carry_restores(conn), here or of_events.machine_id())
    seen = set(spans)
    seg_sums = []
    for sid, a, b, org, pos, work in segments(spans, decl, start, end):
        for x, y in counted_parts(a, b, away.get(sid)):
            f, c, k = conn.execute("SELECT COALESCE(SUM(fresh), 0), COALESCE(SUM(cost), 0), count(*) FROM "
                                   "view_spend_month WHERE session_id = ? AND epoch >= ? AND epoch < ?",
                                   (sid, x, y)).fetchone()
            if k:
                seg_sums.append((org, pos, work, {"fresh": int(f), "cost_usd": float(c), "responses": int(k)}))
    ph = ",".join("?" * len(seen)) or "''"
    f, c, k = conn.execute(f"SELECT COALESCE(SUM(fresh), 0), COALESCE(SUM(cost), 0), count(*) FROM view_spend_month "
                           f"WHERE epoch >= ? AND epoch < ? AND session_id NOT IN ({ph})",
                           [start, end] + sorted(seen)).fetchone()
    if k:
        seg_sums.append((None, None, None, {"fresh": int(f), "cost_usd": float(c), "responses": int(k)}))
    segs, by = outside_runs(conn, canon_map(docs), decl, start, end)
    out = summarize(seg_sums + segs, docs, start, end, now, totals={"outside": by})
    out["complete"] = _meta(conn, "spend_month_caught_up") == "1"
    return out


def carry_restores(conn):
    """{session: [(epoch, host id)]}: each machine a carry restore put a session on, and when (of.holder_changed with
    the evidence `of capacity carry restore`; a coordination event, so every machine has the others')."""
    out = {}
    for ts, host, pl in conn.execute("SELECT ts, host_id, payload FROM event WHERE name = 'of.holder_changed' "
                                     "AND payload LIKE '%carry restore%'"):
        try:
            j = json.loads(pl or "{}")
        except ValueError:
            continue
        sid, t = j.get("new_session") or j.get("previous_session"), _epoch(ts)
        if sid and host and t is not None and "carry restore" in str(j.get("evidence") or ""):
            out.setdefault(sid, []).append((t, host))
    return {k: sorted(v) for k, v in out.items()}


def away_spans(restores, here):
    """{session: [(a, b)]}: the times a carried session counted on another machine than `here`. A carried transcript
    holds its history from the machine it left, and the store there reads it again: each response counts on the
    machine a carry restore had last put its session on, and before its first restore on the machine it came from
    (2026-11-22: the agent host's store held the moved sessions' laptop turns too)."""
    out = {}
    for sid, rs in restores.items():
        spans = [(float("-inf"), rs[0][0])] if rs[0][1] == here else []
        for i, (t, host) in enumerate(rs):
            if host != here:
                spans.append((t, rs[i + 1][0] if i + 1 < len(rs) else float("inf")))
        if spans:
            out[sid] = spans
    return out


def counted_parts(a, b, spans):
    """[a, b) less the spans when the session counted on another machine."""
    parts = [(a, b)]
    for x, y in spans or ():
        cut = []
        for p, q in parts:
            if y <= p or x >= q:
                cut.append((p, q))
                continue
            if p < x:
                cut.append((p, x))
            if y < q:
                cut.append((y, q))
        parts = cut
    return parts


def _target(docs, o, pos, work):
    """(work or None, goal or None) a response goes to, as the month's sums attribute it."""
    doc = docs.get(o) or {}
    works = doc.get("works") if isinstance(doc.get("works"), dict) else {}
    kind, target = ("work", work) if work else fallback_target(o, pos, doc)
    if kind == "work":
        w = works.get(target) if isinstance(works.get(target), dict) else {}
        return target, w.get("goal")
    return None, (target if kind == "goal" else None)


def _cells(docs, conn, since, until, step, here):
    """{(period, account, org, position, work, goal): sums} of this machine's own responses in [since, until), per
    period of `step` seconds from `since`: the month's attribution, the carried history left to its machine
    (away_spans), the sessions no position held under position None, the runs on other harnesses with account None."""
    import of_capacity
    spans, decl = _store_inputs(docs, conn)
    away = away_spans(carry_restores(conn), here)
    cells = {}

    def put(k, acct, o, pos, work, goal, s):
        _add(cells.setdefault((int(k), acct, o, pos, work, goal), _zero()), s)

    sql = ("SELECT CAST((epoch - ?) / ? AS INTEGER), config_dir, COALESCE(SUM(fresh), 0), COALESCE(SUM(cost), 0), "
           "count(*) FROM view_spend_month WHERE session_id = ? AND epoch >= ? AND epoch < ? GROUP BY 1, 2")
    for sid, a, b, o, pos, work in segments(spans, decl, since, until):
        w, g = _target(docs, o, pos, work)
        for x, y in counted_parts(a, b, away.get(sid)):
            for k, cd, f, c, n in conn.execute(sql, (since, step, sid, x, y)):
                put(k, of_capacity.short(cd) if cd else "?", o, pos, w, g,
                    {"fresh": int(f), "cost_usd": float(c), "responses": int(n)})
    seen = sorted(spans)
    ph = ",".join("?" * len(seen)) or "''"
    for k, cd, f, c, n in conn.execute(
            "SELECT CAST((epoch - ?) / ? AS INTEGER), config_dir, COALESCE(SUM(fresh), 0), COALESCE(SUM(cost), 0), "
            f"count(*) FROM view_spend_month WHERE epoch >= ? AND epoch < ? AND session_id NOT IN ({ph}) GROUP BY 1, 2",
            [since, step, since, until] + seen):
        put(k, of_capacity.short(cd) if cd else "?", None, None, None, None,
            {"fresh": int(f), "cost_usd": float(c), "responses": int(n)})
    canon = canon_map(docs)
    k = 0
    while since + k * step < until:  # the runs on other harnesses: their tokens, no list cost (flat plans)
        segs, _ = outside_runs(conn, canon, decl, since + k * step, min(until, since + (k + 1) * step))
        for o, pos, work, s in segs:
            w, g = _target(docs, o, pos, work) if o else (None, None)
            put(k, None, o, pos, w, g, s)
        k += 1
    return cells


def machine_name(conn, host):
    """A machine's name: its latest events' host name, else this one's (socket)."""
    r = conn.execute("SELECT host_name FROM event WHERE host_id = ? AND host_name IS NOT NULL ORDER BY rowid DESC "
                     "LIMIT 1", (host,)).fetchone()
    if r and r[0]:
        return r[0]
    import socket
    return socket.gethostname()


HOURS_EVENT = "of.spend_hours"
HOURS_EVERY_S = 900        # a machine reports its changed hours at most this often
HOURS_LATE = 3             # the last hours are compared again: a long turn's records reach the store at its end
HOURS_BACK_S = 30 * 86400  # a machine's first report goes back as far as the coordination feed keeps (of_coord)


def remote_hours(conn, here, since, until):
    """[(machine, hour, org, rows)] the other machines reported for the hours in [since, until): of.spend_hours from
    the coordination feed, the latest per machine, organization and hour."""
    latest = {}
    for host, name, o, pl in conn.execute(
            "SELECT host_id, host_name, org, payload FROM event WHERE name = ? AND host_id IS NOT NULL AND host_id != ? "
            "AND ts >= ? ORDER BY ts, rowid", (HOURS_EVENT, here, _iso(since))):
        try:
            j = json.loads(pl or "{}")
        except ValueError:
            continue
        hour = j.get("hour")
        if isinstance(hour, (int, float)) and since <= hour < until and isinstance(j.get("rows"), list):
            latest[(host, o, hour)] = (j.get("machine") or name or host[:8], j["rows"])
    return [(m, hour, o, rows) for (_, o, hour), (m, rows) in sorted(latest.items(), key=lambda x: x[0][2])]


def emit_hours(docs, conn, now, here=None, emit=None):
    """Report this machine's hours to the organization's other machines: one of.spend_hours per organization and
    hour that changed since the last report, its rows (account, position, work, goal, fresh, cost, responses,
    outside fresh, outside runs). Shared through the coordination feed, so `of capacity spend --by` on any machine
    shows every machine's spend (the founder, 2026-11-22, prompt 01a204f4-e8e2: "of capacity spend --by hour does not
    show the costs on the second machine"). The sessions no position held stay here: no organization's feed
    is theirs. Returns the events sent."""
    import hashlib
    import socket
    here = here or of_events.machine_id()
    last = float(_meta(conn, "spend_hours_at") or 0)
    if now - last < HOURS_EVERY_S:
        return 0
    hour = now - now % 3600
    final = _meta(conn, "spend_hours_final")
    lo = min(float(final) if final else hour - HOURS_BACK_S, hour - HOURS_LATE * 3600)
    lo = max(lo, month_bounds(now=month_bounds(now=now)[0] - 1)[0])   # nothing before the view keeps
    lo -= lo % 3600
    by = {}
    for (k, acct, o, pos, work, goal), s in _cells(docs, conn, lo, hour + 3600, 3600, here).items():
        if o:
            by.setdefault((lo + k * 3600, o), []).append(
                [acct, pos, work, goal, s["fresh"], round(s["cost_usd"], 4), s["responses"], s["outside_fresh"],
                 s["outside_runs"]])
    conn.execute("CREATE TABLE IF NOT EXISTS view_spend_hours_sent (hour REAL, org TEXT, digest TEXT, "
                 "PRIMARY KEY (hour, org))")
    sent = 0
    for (h, o), rows in sorted(by.items()):
        rows.sort(key=lambda r: [str(x) for x in r])
        digest = hashlib.sha256(json.dumps(rows).encode()).hexdigest()
        was = conn.execute("SELECT digest FROM view_spend_hours_sent WHERE hour = ? AND org = ?", (h, o)).fetchone()
        if was and was[0] == digest:
            continue
        (emit or of_events.emit)("spend_hours", {"v": 1, "hour": h, "machine": socket.gethostname(), "rows": rows},
                                 org=o)
        conn.execute("INSERT OR REPLACE INTO view_spend_hours_sent VALUES (?, ?, ?)", (h, o, digest))
        sent += 1
    conn.execute("DELETE FROM view_spend_hours_sent WHERE hour < ?", (hour - HOURS_BACK_S - 86400,))
    _meta(conn, "spend_hours_final", hour - HOURS_LATE * 3600)
    _meta(conn, "spend_hours_at", now)
    return sent


STEPS = {"day": 86400, "hour": 3600}


def periods(docs, conn, since, until, by="day", org=None, now=None, here=None):
    """Spend per day or hour (UTC) in [since, until), from the same attributed responses as the month's sums: each
    period's total, by machine, then account, then position, and by account, position, work and goal over all machines
    (a work's spend counts to its goal; no sub-goal roll-up here). This machine's own responses come from its store,
    its carried history left to the machine it was spent on; the other machines' from their of.spend_hours reports.
    The sessions no position held show as "(no position)" for this machine only. `org` keeps that organization's
    positions. The view keeps the responses from the start of the previous month (the founder, 2026-11-22, prompts
    01a204e5-0acb, "by day and by hour", and 01a204f4-e8e2, the second machine)."""
    now = now or time.time()
    step = STEPS[by]
    asked, since = since, since - since % step
    here = here or of_events.machine_id()
    conn.executescript(MONTH_DDL)
    cells = {}
    mine = machine_name(conn, here)
    for (k, acct, o, pos, work, goal), s in _cells(docs, conn, since, until, step, here).items():
        if not org or o == org:
            _add(cells.setdefault((k, mine, acct, o, pos, work, goal), _zero()), s)
    reported = set()
    for m, hour, o, rows in remote_hours(conn, here, since, until):
        if org and o != org:
            continue
        reported.add(m)
        for r in rows:
            acct, pos, work, goal, f, c, n, of_, orun = (list(r) + [0] * 9)[:9]
            _add(cells.setdefault((int((hour - since) // step), m, acct, o, pos, work, goal), _zero()),
                 {"fresh": f, "cost_usd": c, "responses": n, "outside_fresh": of_, "outside_runs": orun})

    def tree():
        return {"total": _zero(), "machines": {}, "accounts": {}, "positions": {}, "works": {}, "goals": {}}

    def key(o, name):  # two organizations' rows never add up: a position, work or goal is its organization's
        return f"{o}/{name}" if o else name

    def put(t, m, acct, o, pos, work, goal, s):
        _add(t["total"], s)
        mm = t["machines"].setdefault(m, {"total": _zero(), "accounts": {}})
        _add(mm["total"], s)
        aa = mm["accounts"].setdefault(acct or "other harnesses", {"total": _zero(), "positions": {}})
        _add(aa["total"], s)
        p = key(o, pos) if pos else "(no position)"
        _add(aa["positions"].setdefault(p, dict(_zero(), org=o)), s)
        if acct:
            _add(t["accounts"].setdefault(acct, _zero()), s)
        _add(t["positions"].setdefault(p, dict(_zero(), org=o)), s)
        if work:
            _add(t["works"].setdefault(key(o, work), dict(_zero(), org=o)), s)
        if goal:
            _add(t["goals"].setdefault(key(o, goal), dict(_zero(), org=o)), s)

    whole, rows = tree(), {}
    for (k, m, acct, o, pos, work, goal), s in cells.items():
        put(rows.setdefault(k, tree()), m, acct, o, pos, work, goal, s)
        put(whole, m, acct, o, pos, work, goal, s)
    keep_from = month_bounds(now=month_bounds(now=now)[0] - 1)[0]
    return _round(dict(whole, v=2, by=by, org=org, computed_at=now, kept_from=keep_from, machine=mine,
                       reported=sorted(reported), complete=_meta(conn, "spend_month_caught_up") == "1",
                       periods=[dict(rows[k], start=since + k * step) for k in sorted(rows)], to=until,
                       asked_from=asked, **{"from": since}))


def render_periods(out, top=10):
    """`of capacity spend --by day|hour`: each period's total, then each machine, its accounts and their top
    positions, as text."""
    fmt = "%Y-%m-%d" if out["by"] == "day" else "%Y-%m-%d %H:00Z"
    span = (f"{time.strftime('%Y-%m-%d %H:%MZ', time.gmtime(out['from']))} to "
            f"{time.strftime('%Y-%m-%d %H:%MZ', time.gmtime(min(out['to'], out['computed_at'])))}")
    lines = [f"Spend by {out['by']}, {span}{', ' + out['org'] + ' only' if out.get('org') else ''}: list cost at each "
             f"model's prices and fresh tokens (input, cache writes, output; cache reads are in the cost only)",
             f"  machines: {out['machine']} (this one)"
             + "".join(f", {m} (its reports)" for m in out.get("reported") or [])]
    if not out.get("complete", True):
        lines.append("  the store is still being read into the view, so recent periods are low")
    if out["from"] < out.get("kept_from", 0):
        lines.append(f"  nothing before {time.strftime('%Y-%m-%d', time.gmtime(out['kept_from']))}: the view keeps "
                     f"this and the previous month")

    bare = {}
    for k in out["positions"]:
        bare.setdefault(k.split("/", 1)[-1], set()).add(k)

    def label(k):  # a position's bare name, unless two organizations hold one of that name
        b = k.split("/", 1)[-1]
        return b if len(bare.get(b, ())) <= 1 else k

    def tops(d, n, name=label):
        items = sorted(d.items(), key=lambda x: -x[1]["cost_usd"])
        shown = ", ".join(f"{name(k)} ${v['cost_usd']:,.0f}" for k, v in items[:n])
        rest = sum(v["cost_usd"] for _, v in items[n:])
        return shown + (f", {len(items) - n} more ${rest:,.0f}" if len(items) > n else "")

    def sums(t):
        return (f"${t['cost_usd']:,.0f}  fresh {_num(t['fresh'])}  {t['responses']:,} responses"
                + (f"  + {_num(t.get('outside_fresh'))} in {t['outside_runs']} outside run(s)"
                   if t.get("outside_runs") else ""))

    def machines(t):
        for m, mm in sorted(t["machines"].items(), key=lambda x: -x[1]["total"]["cost_usd"]):
            lines.append(f"  {m}  {sums(mm['total'])}")
            for a, aa in sorted(mm["accounts"].items(), key=lambda x: -x[1]["total"]["cost_usd"]):
                lines.append(f"    {a} ${aa['total']['cost_usd']:,.0f}: {tops(aa['positions'], top)}")
    step = STEPS[out["by"]]
    for p in out["periods"]:
        part = p["start"] < out.get("asked_from", out["from"]) or p["start"] + step > min(out["to"], out["computed_at"])
        lines.append(f"\n{time.strftime(fmt, time.gmtime(p['start']))}{' (part)' if part else ''}  {sums(p['total'])}")
        machines(p)
    lines.append(f"\nall  {sums(out['total'])}")
    machines(out)
    if out["accounts"]:
        lines.append("  by account, all machines: " + tops(out["accounts"], 99, name=str))
    return "\n".join(lines)


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


def tick(conn, now, docs_fn=None, emit=None):
    """of_capacity's listener tick: advance the views; report this machine's changed hours to the others at most every
    HOURS_EVERY_S (emit_hours); recompute the sums at most every REFRESH_S."""
    caught_up = advance(conn, now)
    if docs_fn is None:
        import of_tools
        docs_fn = of_tools.registries
    if not caught_up:
        return False
    docs = None
    if now - float(_meta(conn, "spend_hours_at") or 0) >= HOURS_EVERY_S:
        docs = docs_fn()
        emit_hours(docs, conn, now, emit=emit)
    last = load()
    if last and now - (last.get("computed_at") or 0) < REFRESH_S \
            and last.get("month") == _month_name(month_bounds(now=now)[0]):
        return False
    write(compute(docs if docs is not None else docs_fn(), conn, now=now))
    return True


# ------------------------------------------------------------------ the text


def _num(n):
    n = n or 0
    return f"{n / 1e9:.2f}B" if n >= 1e9 else f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.0f}k" if n >= 1e3 else str(int(n))


def _line(label, s):
    out = f"  {label:<44} {_num(s['fresh']):>8}  ${s['cost_usd']:>10,.2f}"
    if s.get("outside_runs"):
        out += f"  + {_num(s.get('outside_fresh'))} in {s['outside_runs']} outside run(s)"
    return out


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
            lines.append(_line("  " * depth + gid + ("" if g.get("known", True) else " (not in goals:)"), g["total"]))
            for c in sorted(kids.get(gid, [])):
                walk(c, depth + 1)
        if goals:
            lines.append(" goals (with their sub-goals)")
            for gid in sorted(kids.get(None, [])):
                walk(gid, 1)
        if o.get("conditions"):
            lines.append(" conditions")
            for cid, c in sorted(o["conditions"].items()):
                lines.append(_line("  " + cid, c["total"]))
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
        if out.get("outside"):
            lines.append("\nruns on other harnesses (of subagent run; fresh tokens, no list cost: flat plans)")
            for key, b in sorted(out["outside"].items()):
                lines.append(f"  {key:<44} {_num(b['fresh']):>8}  {b['runs']} run(s); input {_num(b['input'])}, "
                             f"cached {_num(b['cached'])}, output {_num(b['output'])}")
    return "\n".join(lines)
