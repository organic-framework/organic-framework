"""Work booked for later with no basis (hand-over 277de578 from of_architect; organic-framework AGENTS.md and
docs/method.md, "Work starts when it is ready"; the founder, prompt 01a1e9ab-a3ba, 2026-11-17: "Where does this
dragging out of time come from?").

A position books a start for later in three ways this module reads, each an item judged once:
  cron        a new session cron (of.continuation's session_crons), by its id;
  clock_wait  a new background task that waits on the clock (a sleep of 15 minutes or more, or a date comparison),
              by its id; a task that waits on a condition (CI, a PR, a log line) is no booking;
  message     what a position sends up with a clock time at least 15 minutes after the message's own time, or
              tomorrow / tonight / next week: a peer message to its unit's owner, its organization's conductor or its
              structure keeper (of.agent_message_in in the receiver's session), and an of ask to the owner;
              deduplicated by position, day and time.
Turn-end narration stays out: most of its times are windows and rollbacks with a basis (of_architect, message
01a1e9b4-c257: 2,458 of 6,502 turn ends of one day named such a time).

`of tempo judge` takes the items not judged yet, one small-model call per position and run with all its new items,
and records `of.booking_judged` (docs/events.md). A booking with no basis is typed into the position's pane once by the
pulse (of_pulse.booking_notes): run it now, or name what it waits on. `of tempo replay --since --until` judges a past
window without recording. Tests: tests/test_tempo.py; scores: lab/scenarios/lab-tempo-v0.
"""

import json
import os
import re
import subprocess
import time

import of_events

VERSION = "tb-v2"
MODEL = "haiku"
TIMEOUT_S = 90
AHEAD_MIN = 15            # a clock time this many minutes after the item's own time is a booking's candidate
SLEEP_MIN_S = 900         # a background sleep this long or longer waits on the clock
LOOKBACK_S = 24 * 3600    # items older than this are never judged
KEEP_S = 3 * 24 * 3600    # judged keys kept this long in the state file
FETCH_MAX = 5000          # events read per run; the rest is the next run's
TEXT_MAX = 700            # of one item's text in the prompt
NAMES = ("of.continuation", "of.agent_message_in", "of.agent_question")
# The pulse types the notes only once the lab's precision is accepted (lab/scenarios/lab-tempo-v0; of_architect): until
# then the judge records its verdicts, and nothing is typed into a pane (the first replay, 2026-11-17, judged an hourly
# check-in a booking with no basis).
NOTES_LIVE = False
BASIS_KINDS = ("position", "owner_card", "window", "runner", "data_not_yet", "conflict", "owner_limit", "condition",
               "none")
VERDICTS = ("basis", "no_basis", "not_a_booking")

_CLOCK = re.compile(r"(?<![\d:])([01]?\d|2[0-3]):([0-5]\d)(?!:\d)")
_WORDS = re.compile(r"\b(tomorrow|tonight|next (?:week|day|morning)|завтра|послезавтра|на следующей неделе)\b", re.I)
_SLEEP = re.compile(r"\bsleep\s+(\d+(?:\.\d+)?)\s*([smhd]?)\b")
_DATE_CMP = re.compile(r"\$\(date\b[^)]*\)[\"']?\s*(?:-lt|-le|-gt|-ge|-eq)\b"            # [ "$(date ...)" -ge X ]
                       r"|-(?:lt|le|gt|ge|eq)\s+[\"']?\$\(date\b"                       # [ X -lt "$(date ...)" ]
                       r"|\[\[[^\]]*\$\(date\b[^\]]*[<>][^\]]*\]\]"                         # [[ $(date ...) > X ]]
                       r"|\[\[[^\]]*[<>][^\]]*\$\(date\b"
                       r"|\bsleep\s+[\"']?\$\(\((?:(?!\)\)).)*date", re.S)           # sleep $(( T - $(date +%s) ))           # sleep $(( T - $(date +%s) ))


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + "Z"


def _epoch(ts):
    import calendar
    try:
        return calendar.timegm(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return 0.0


def _one_line(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


# ------------------------------------------------------------------ the cheap filter

def times_ahead(text, ts):
    """The clock times in TEXT at least AHEAD_MIN minutes after TS's time of day ('HH:MM'), and the words for a later
    day, in order, each once: a booking's candidates. A time with seconds is a reading, never a booking."""
    try:
        now = int(ts[11:13]) * 60 + int(ts[14:16])
    except (TypeError, ValueError):
        return []
    out = []
    for m in _CLOCK.finditer(text or ""):
        t = int(m.group(1)) * 60 + int(m.group(2))
        if t - now >= AHEAD_MIN:
            out.append(f"{int(m.group(1)):02}:{m.group(2)}")
    out += [m.group(1).lower() for m in _WORDS.finditer(text or "")]
    return list(dict.fromkeys(out))


def waits_on_clock(command):
    """Does a background command wait on the clock: a sleep of SLEEP_MIN_S or more, or a date comparison?"""
    for n, unit in _SLEEP.findall(command or ""):
        if float(n) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[unit] >= SLEEP_MIN_S:
            return True
    return bool(_DATE_CMP.search(command or ""))


def _bare(name):
    return str(name or "").rsplit(":", 1)[-1].strip()


def up_of(position, positions):
    """The positions POSITION sends up to: its unit's owner, its organization's conductor and its structure keeper."""
    import of_pulse
    me = next((p for p in positions if p.get("name") == position), None)
    if not me:
        return set()
    org = me.get("_org")
    up = {me.get("_unit_owner"), of_pulse._conductor_of(positions, org), of_pulse.structure_keeper(positions, org)}
    return {x for x in up if x and x != position}


def items(rows, positions, judged=()):
    """The new items in ROWS (event dicts: rowid, event_id, ts, name, session_id, position, org, payload), not in
    JUDGED (keys). [{key, source, position, session, org, ts, ref, text, times}], in log order."""
    out, seen = [], set(judged)

    def add(it):
        if it["key"] not in seen:
            seen.add(it["key"])
            out.append(it)
    for r in rows:
        p, pos, ts = r.get("payload") or {}, r.get("position"), r.get("ts") or ""
        base = {"session": r.get("session_id"), "org": r.get("org"), "ts": ts}
        if r["name"] == "of.continuation" and pos:
            for c in p.get("session_crons") or []:
                if isinstance(c, dict) and c.get("id"):
                    add(dict(base, key=f"cron:{pos}:{c['id']}", source="cron", position=pos, ref=c["id"],
                             text=f"schedule {c.get('schedule')!s}, recurring {bool(c.get('recurring'))}: "
                                  f"{c.get('prompt') or ''}", times=[]))
            for t in p.get("background_tasks") or []:
                if isinstance(t, dict) and t.get("id") and t.get("type") == "shell" \
                        and waits_on_clock(t.get("command")):
                    add(dict(base, key=f"bg:{pos}:{t['id']}", source="clock_wait", position=pos, ref=t["id"],
                             text=f"{t.get('description') or ''}: {t.get('command') or ''}", times=[]))
        elif r["name"] == "of.agent_message_in" and pos:
            sender = _bare(p.get("from"))
            text = p.get("text") or ""
            if not sender or pos not in up_of(sender, positions):
                continue
            new = [x for x in times_ahead(text, ts) if f"msg:{sender}:{ts[:10]}:{x}" not in seen]
            if new:
                for x in new:
                    seen.add(f"msg:{sender}:{ts[:10]}:{x}")
                out.append(dict(base, key=f"msg:{sender}:{ts[:10]}:{new[0]}", keys=[f"msg:{sender}:{ts[:10]}:{x}"
                                                                                  for x in new],
                                source="message", position=sender, session=None, ref=r.get("event_id"),
                                to=pos, text=text, times=new))
        elif r["name"] == "of.agent_question" and pos and p.get("detected_by") == "declared" \
                and (str(p.get("to") or "founder") in ("founder", "owner") or p.get("asked_to") == "founder"):
            text = " ".join(str(q.get("question") or "") for q in p.get("questions") or [] if isinstance(q, dict))
            new = [x for x in times_ahead(text, ts) if f"msg:{pos}:{ts[:10]}:{x}" not in seen]
            if new:
                for x in new:
                    seen.add(f"msg:{pos}:{ts[:10]}:{x}")
                out.append(dict(base, key=f"msg:{pos}:{ts[:10]}:{new[0]}", keys=[f"msg:{pos}:{ts[:10]}:{x}"
                                                                               for x in new],
                                source="ask", position=pos, ref=r.get("event_id"), to="owner", text=text,
                                times=new))
    return out


# ------------------------------------------------------------------ the model

SYSTEM = """You read what one AI agent, a position in an organization, booked for later: crons it set in its \
session, background tasks that wait on the clock, and messages it sent up (to its manager, its conductor, its \
structure keeper, or a question to the owner) that name a later time. The rule: work starts when it is ready. An \
agent may put a start off only for a named reason (a basis).

For each item decide:
- booking: does it put a start of the agent's own work off to a later time? A watch that checks something every \
few minutes, a report of when something happened, or a time someone else set is not a booking of its own work.
- basis_kind, the reason it waits, when it names one: position (another position must act first, named), \
owner_card (the owner's answer to a card or ask), window (a maintenance or release window or slot, with its \
source), runner (a CI run, a build, a job running now), data_not_yet (data that does not exist yet, named), \
conflict (the same system, host, file or resource is in use, named), owner_limit (a limit the owner set, with its \
ask id), condition (it waits for a named event or condition rather than a time), none (no reason given, or only \
"later", "tomorrow", a round deadline, a guess at how long something takes).
A clock time holds only when it is the basis's own time: a window's start, a booked slot, a limit's reset, a run's \
expected end. A wait on a position, a decision or a run waits for that event, not for a time picked ahead of it: an \
alarm at 16:10Z before a 16:40Z roll that waits on another position's decision is a booking with no basis \
(of_architect, AGENTS.md and docs/method.md, "Work starts when it is ready", 2026-11-17).
Basis's own times: a window's or a booked slot's start (a release slot, a maintenance or host window), the hours a \
named person or operator is on hand, a limit's reset, a run's expected end, and a short lead before such a time (T-5 \
minutes, a prep step shortly before a booked slot). Not a booking: a run on the role's own cadence (a weekly batch, a \
daily note, four runs a month by its role design) at that cadence's time, and a check of data for a period that ends \
then (a weekly check of the week's data). A run the item itself says should start as soon as something ends, booked \
for hours after it, is a booking with no basis.
A fixed clock time needs its own basis: when the item also waits for conditions (a merge, a check, another \
step's end), ask whether the time itself is needed. If the conditions could be met earlier and nothing named requires \
that hour, the time is a booking with no basis ("arm item 7 for 18:45Z with its merge gate": the merge gate does not \
explain 18:45Z). Waiting for a condition with a deadline or timeout (poll CI until done, give up after 50 minutes) \
is a condition, not a booking.
A slot in a plan that this agent or a peer made ("item 7 at 18:45Z", "before k8s_keeper's 18:45-19:10Z slot", "after \
the queue: RISK-003, then Wazuh, then Kafka") is not a window and not a basis by itself: it is one only when the item \
names why the work cannot run now (a conflict on a named system, host or resource, a position that must act first, \
the owner's decision with its id). A recurring watch or health check that runs every few minutes or hours is not a \
booking.
- verdict: basis (a booking with a basis), no_basis (a booking with basis_kind none), not_a_booking.
- basis: the words of the item that name the basis, at most 120 characters; "" when none.
- reason: one short English sentence. Write basis and reason in English whatever language the item is in."""

SCHEMA = {"type": "object", "additionalProperties": False,
          "properties": {"items": {"type": "array", "items": {
              "type": "object", "additionalProperties": False,
              "properties": {"id": {"type": "string"}, "booking": {"type": "boolean"},
                             "basis_kind": {"type": "string", "enum": list(BASIS_KINDS)},
                             "verdict": {"type": "string", "enum": list(VERDICTS)},
                             "basis": {"type": "string"}, "reason": {"type": "string"}},
              "required": ["id", "booking", "basis_kind", "verdict", "basis", "reason"]}}},
          "required": ["items"]}


def user_prompt(position, group, summary=None):
    lines = [f"Agent: {position}" + (f" ({summary})" if summary else "")]
    for i, it in enumerate(group, 1):
        where = {"cron": "a cron it set in its session", "clock_wait": "a background task that waits on the clock",
                 "message": f"a message it sent to {it.get('to')}", "ask": "a question it asked the owner"}[it["source"]]
        times = f"; later times named: {', '.join(it['times'])}" if it.get("times") else ""
        lines.append(f"\nItem {i}, {where}, at {it['ts'][:16]}Z{times}:\n<<<\n{it['text'][:TEXT_MAX]}\n>>>")
    lines.append("\nAnswer one entry per item, its id the item's number.")
    return "\n".join(lines)


def command(model=MODEL):
    import of_capacity
    return [of_capacity.claude_bin(), "-p", None, "--model", model, "--tools", "", "--system-prompt", SYSTEM,
            "--json-schema", json.dumps(SCHEMA), "--output-format", "json", "--no-session-persistence",
            "--settings", '{"disableAllHooks": true}', "--strict-mcp-config", "--disable-slash-commands",
            "--max-turns", "3"]


def parse(stdout):
    out = (stdout or "").strip()
    try:
        j = json.loads(out)
    except ValueError:
        m = re.search(r"\{.*\}", out, re.S)
        j = json.loads(m.group(0)) if m else {}
    meta = {}
    if isinstance(j, dict) and ("structured_output" in j or j.get("type") == "result"):
        meta = {"cost_usd": j.get("total_cost_usd"), "model": next(iter(j.get("modelUsage") or {}), None)}
        if j.get("is_error"):
            raise ValueError(str(j.get("result") or j.get("subtype"))[:200])
        j = j.get("structured_output") or json.loads(j.get("result") or "{}")
    if not isinstance(j, dict) or not isinstance(j.get("items"), list):
        raise ValueError(f"no verdicts in the reply: {out[:200]}")
    return j["items"], meta


def call(prompt, n, config_dir=None, runner=subprocess.run, model=MODEL):
    """{verdicts: [one per item, in order], model, ms, cost_usd} or {error, ms}."""
    import of_capacity
    import of_case
    d = config_dir or of_case.account()
    argv = [prompt if x is None else x for x in command(model)]
    cwd = os.path.join(of_events.STATE_DIR, "tempo")
    os.makedirs(cwd, mode=0o700, exist_ok=True)
    t0 = time.time()
    try:
        r = runner(argv, capture_output=True, text=True, timeout=TIMEOUT_S, env=of_capacity.probe_env(d), cwd=cwd)
        got, meta = parse(r.stdout)
    except subprocess.TimeoutExpired:
        return {"error": f"timed out after {TIMEOUT_S}s", "ms": int((time.time() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001  the items stay unjudged; the next run tries again
        return {"error": f"{type(e).__name__}: {str(e)[:200]}", "ms": int((time.time() - t0) * 1000)}
    by = {str(x.get("id")).strip().lstrip("#"): x for x in got if isinstance(x, dict)}
    verdicts = []
    for i in range(1, n + 1):
        x = by.get(str(i)) or {}
        v = x.get("verdict") if x.get("verdict") in VERDICTS else None
        if v is None:
            return {"error": f"no verdict for item {i}", "ms": int((time.time() - t0) * 1000)}
        k = x.get("basis_kind") if x.get("basis_kind") in BASIS_KINDS else "none"
        if v == "basis" and k == "none":
            v = "no_basis"   # a basis it cannot name is none
        verdicts.append({"verdict": v, "basis_kind": k, "basis": _one_line(x.get("basis"), 160),
                         "reason": _one_line(x.get("reason"), 300)})
    return {"verdicts": verdicts, "model": meta.get("model") or model, "ms": int((time.time() - t0) * 1000),
            "cost_usd": meta.get("cost_usd")}


# ------------------------------------------------------------------ the runs

def state_path():
    return os.path.join(of_events.STATE_DIR, "tempo.json")


def load_state():
    try:
        with open(state_path()) as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        st = {}
    st.setdefault("cursor", None)   # the last rowid read; None: start LOOKBACK_S back
    st.setdefault("judged", {})     # item key -> [verdict, at]
    st.setdefault("retry", [])      # items whose call failed: judged again at the next run
    return st


def save_state(st, now):
    st["judged"] = {k: v for k, v in st["judged"].items() if now - v[1] < KEEP_S}
    os.makedirs(of_events.STATE_DIR, mode=0o700, exist_ok=True)
    tmp = state_path() + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(st, fh)
    os.replace(tmp, state_path())


def fetch(conn, after=None, since=None, until=None, limit=FETCH_MAX):
    """The events of NAMES after rowid AFTER (or from SINCE), up to UNTIL, in log order, at most LIMIT."""
    cond, args = "", list(NAMES)
    if after is not None:
        cond, args = cond + " AND rowid > ?", args + [after]
    if since:
        cond, args = cond + " AND ts >= ?", args + [since]
    if until:
        cond, args = cond + " AND ts < ?", args + [until]
    q = (f"SELECT rowid, event_id, ts, name, session_id, position, org, payload FROM event WHERE name IN "
         f"({','.join('?' * len(NAMES))}){cond} ORDER BY rowid LIMIT ?")
    out = []
    for rid, e, ts, n, s, pos, org, pl in conn.execute(q, args + [limit]):
        try:
            payload = json.loads(pl) if pl else {}
        except ValueError:
            payload = {}
        out.append({"rowid": rid, "event_id": e, "ts": ts, "name": n, "session_id": s, "position": pos, "org": org,
                    "payload": payload})
    return out


PER_CALL = 10   # items in one call: a position with forty new items timed out in one (2026-11-17, lab-tempo-v0)


def groups(todo):
    """[(position, [items])] in first-seen order: one call per position and run with its new items, PER_CALL a call."""
    by = {}
    for it in todo:
        by.setdefault(it["position"], []).append(it)
    return [(pos, its[i:i + PER_CALL]) for pos, its in by.items() for i in range(0, len(its), PER_CALL)]


def judge_items(todo, positions=(), runner=subprocess.run, log=print):
    """[(position, group, result)] for the items, one call per position."""
    summaries = {p["name"]: p.get("summary") for p in positions or ()}
    out = []
    for pos, group in groups(todo):
        v = call(user_prompt(pos, group, summaries.get(pos)), len(group), runner=runner)
        if "error" in v:
            log(f"of tempo: {pos}: {v['error']}")
        out.append((pos, group, v))
    return out


def payload_of(pos, group, v):
    return {"position": pos, "items": [
        {"key": it["key"], "source": it["source"], "ref": it["ref"], "to": it.get("to"), "times": it.get("times") or [],
         "quote": _one_line(it["text"], 300), **x} for it, x in zip(group, v["verdicts"])],
        "judge": VERSION, "model": v["model"], "ms": v["ms"], "cost_usd": v["cost_usd"]}


def judge(conn, positions=(), now=None, runner=subprocess.run, emit=True, log=print):
    """Judge the items that arrived since the last run. Returns [(position, group, result)]."""
    now = now or time.time()
    import of_net
    if runner is subprocess.run and not of_net.online():
        log("of tempo: the model API is unreachable; the items wait for the next run")
        return []
    st = load_state()
    rows = fetch(conn, after=st["cursor"], since=_iso(now - LOOKBACK_S) if st["cursor"] is None else None)
    todo = [it for it in st["retry"] if it["key"] not in st["judged"]] + items(rows, positions, st["judged"])
    out = judge_items(todo, positions, runner, log)
    for pos, group, v in out:
        if "error" in v:
            continue
        if emit:
            first = group[0]
            of_events.emit("booking_judged", payload_of(pos, group, v), position=pos, session=first.get("session"),
                           org=first.get("org"))
        for it, x in zip(group, v["verdicts"]):
            for k in it.get("keys") or [it["key"]]:
                st["judged"][k] = [x["verdict"], now]
    if emit:
        # an item whose call failed is kept to be judged again at the next run, while it is within LOOKBACK_S
        st["retry"] = [it for _, g, v in out if "error" in v for it in g if now - _epoch(it["ts"]) < LOOKBACK_S]
        if rows:
            st["cursor"] = rows[-1]["rowid"]
        save_state(st, now)
    return out


def replay(conn, since, until, positions=(), org=None, runner=subprocess.run, log=print):
    """Judge a past window without recording: [(position, group, result)]."""
    rows = [r for r in fetch(conn, since=since, until=until, limit=10 ** 7) if not org or r.get("org") == org]
    return judge_items(items(rows, positions), positions, runner, log)
