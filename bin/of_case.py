"""of desk case: what the founder works on now, and which agents belong to it (founder, 2026-11-09).

The desk used to follow agent states: an agent that finished a turn came in, in place of the one idle longest.
With fifty agents that moved the desk every few minutes and evicted the sessions the founder was reading
(2026-11-09: acme_throughput-owner, five minutes after it answered him; 1 of the day's 27 moves was an agent
answering him). He asked for a desk that follows the case he works on. So:

1. digest(): from the event log, the founder's own typed prompts in the last WINDOW_S (popup answers left out), what
   each agent answered him, the messages the agents he talked to exchanged with others (the text of the batches the
   relay typed in, the counts of the rest), and the roster.
2. A small model (Haiku through `claude -p`, no tools, no hooks, like the asks judge) names the cases in that window,
   the current one first, and the agents that belong to each, with a weight and a reason.
3. The result is `of.case_inferred`; the desk router (bin/of_desk.py) fills the desk from the latest one.

The digest is the evidence; the model only reads it. Nothing here moves a pane.
"""

import json
import os
import re
import subprocess
import time
from collections import Counter, defaultdict

import of_events

VERSION = "c-v1"
MODEL = "haiku"
TIMEOUT_S = 120
WINDOW_S = 90 * 60        # the founder's prompts the model reads
PROMPT_CHARS = 500        # of each prompt
REPLY_CHARS = 700         # of each answer to him (the store keeps the first 1 KB)
REPLIES = 16              # the latest prompts that carry their answer
MESSAGE_CHARS = 300       # of each message between agents
MESSAGES = 30             # the latest messages with text
MAX_CASES = 3
MAX_MEMBERS = 8
ANSWER = "Answer from the founder to your question"
HEADER = re.compile(r"Message from (?:[a-z0-9-]+:)?([a-z][a-z0-9_-]{0,31}) \(via [^)]*\)\s*(?:\1:\s*)?")

SYSTEM = """You help a founder who runs organizations of AI agents. Every agent holds a position with a name such \
as acme_throughput-owner or accounting_keeper. He works on one matter at a time (a case: a question he investigates, \
a problem he solves, a decision he prepares). He talks to a few agents about it, and they pull in others. His \
screen has a desk with a few slots for agent panes. Decide what he works on and which agents belong on the desk \
for it.

You get: his own prompts of the last 90 minutes, oldest first, each with the agent he typed it to and the start of \
what that agent answered; messages the agents he talked to exchanged with others in that time; message counts per \
pair; the roster (every position with a one-line summary); the cases named last time.

cases: the matters he worked on in this window, the current one first: the matter his latest prompts belong to. \
A case is a whole investigation, not each question in it: the money of an incident, the triggers behind it and the \
reports about it are one case. Split only matters with different goals (a trading incident and the design of a \
tool are two). At most 3; one is fine. For each case:
- title: a short Russian name of the matter, at most 60 characters. Keep last time's title when it is the same \
matter.
- summary: one Russian sentence: what he is finding out or deciding.
- members: the agents that belong on the desk while he works on it, the most central first, at most 8:
  - the agents he typed to about this matter;
  - agents he named or asked to involve. He dictates by voice and names agents loosely ("spam agent", "accounting \
keeper", "acme org", "the forecaster", "thruput" for throughput): map each to its roster name;
  - agents doing substantial work on the matter for those agents: they got a question about it and answer with \
analysis or data, or they were given work on it.
  Leave out agents with only routine traffic (status, acknowledgements, notes sent to many), agents working on \
other matters, and agents he only answered through his question popup.
- weight per member: 1.0 for the agent he talks to about it most, 0.7-0.9 for agents doing the core of the work, \
0.4-0.6 for a supporting part. why: a short Russian phrase: the agent's part in the case.
Use only names from the roster."""

SCHEMA = {"type": "object", "additionalProperties": False,
          "properties": {"cases": {"type": "array", "items": {
              "type": "object", "additionalProperties": False,
              "properties": {"title": {"type": "string"}, "summary": {"type": "string"},
                             "members": {"type": "array", "items": {
                                 "type": "object", "additionalProperties": False,
                                 "properties": {"position": {"type": "string"}, "weight": {"type": "number"},
                                                "why": {"type": "string"}},
                                 "required": ["position", "weight", "why"]}}},
              "required": ["title", "summary", "members"]}}},
          "required": ["cases"]}


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t % 1 * 1000):03d}Z"


def _ts(s):
    import calendar
    return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")) + float("0." + (s[20:23] or "0"))


def _one_line(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


# ------------------------------------------------------------------ the digest

def founder_prompts(conn, since, until):
    """His typed or pasted prompts into agents' panes: [{ts, position, text}], oldest first. Popup answers are left
    out: answering a card is not working on a case."""
    rows = conn.execute(
        "SELECT ts, position, json_extract(payload, '$.text') FROM event WHERE name = 'of.human_prompt' "
        "AND ts >= ? AND ts <= ? AND json_extract(payload, '$.origin') IN ('human', 'paste') "
        "AND coalesce(json_extract(payload, '$.channel'), '') != 'asks-ui' AND position IS NOT NULL ORDER BY ts",
        (since, until)).fetchall()
    return [{"ts": ts, "position": pos, "text": text or ""} for ts, pos, text in rows
            if not (text or "").startswith(ANSWER)]


def answer_to(conn, position, after, before):
    """The agent's closing message of the first turn that ended after his prompt (and before his next one)."""
    r = conn.execute("SELECT json_extract(payload, '$.last_message') FROM event WHERE name = 'of.turn_end' "
                     "AND position = ? AND ts > ? AND ts <= ? ORDER BY ts LIMIT 1",
                     (position, after, before)).fetchone()
    return (r[0] or "") if r else ""


def traffic(conn, focal, since, until):
    """Messages between the focal agents and anyone, counted per pair: {(a, b): {"n": , "chars": }} with a in focal."""
    out = defaultdict(lambda: {"n": 0, "chars": 0})
    for frm, to, chars in conn.execute(
            "SELECT position, json_extract(payload, '$.to_position'), json_extract(payload, '$.chars') FROM event "
            "WHERE name = 'of.agent_message_out' AND ts >= ? AND ts <= ?", (since, until)):
        if not frm or not to or frm == to:
            continue
        for a, b in ((frm, to), (to, frm)):
            if a in focal:
                out[(a, b)]["n"] += 1
                out[(a, b)]["chars"] += int(chars or 0)
                break
    return dict(out)


def messages(conn, focal, since, until):
    """Messages with text that involve a focal agent: the batches the relay typed into panes keep each message's
    text. [{ts, sender, to, text}], newest last."""
    out = []
    for ts, to, text in conn.execute(
            "SELECT ts, position, json_extract(payload, '$.text') FROM event WHERE name = 'of.agent_message_in' "
            "AND ts >= ? AND ts <= ? AND json_extract(payload, '$.text') IS NOT NULL ORDER BY ts", (since, until)):
        parts = HEADER.split(text or "")
        # split() gives [before, sender1, body1, sender2, body2, ...]
        for i in range(1, len(parts) - 1, 2):
            sender, body = parts[i], parts[i + 1]
            if to in focal or sender in focal:
                out.append({"ts": ts, "sender": sender, "to": to, "text": body.strip()})
    return out


def roster(positions):
    out = []
    for p in positions:
        if p.get("status") in ("finished", "retired"):
            continue
        where = "/".join(x for x in (p.get("_orgid") or p.get("_org"), p.get("_unit") or p.get("system")) if x)
        out.append({"name": p["name"], "summary": _one_line(p.get("summary") or p.get("role") or "", 90),
                    "where": where})
    return out


def digest(conn, positions, now=None, window_s=WINDOW_S, previous=None):
    now = now or time.time()
    since, until = _iso(now - window_s), _iso(now)
    prompts = founder_prompts(conn, since, until)
    focal = []
    for p in reversed(prompts):
        if p["position"] not in focal:
            focal.append(p["position"])
    for i, p in enumerate(prompts):
        if i >= len(prompts) - REPLIES:
            nxt = next((q["ts"] for q in prompts[i + 1:] if q["position"] == p["position"]), until)
            p["answer"] = answer_to(conn, p["position"], p["ts"], nxt)
    return {"now": until, "since": since, "window_s": window_s, "prompts": prompts, "focal": focal,
            "traffic": traffic(conn, set(focal), since, until),
            "messages": messages(conn, set(focal), since, until)[-MESSAGES:],
            "roster": roster(positions), "previous": previous or []}


def render(d):
    """The model's user prompt from a digest."""
    lines = [f"Now: {d['now']}. Window: the last {d['window_s'] // 60} minutes.", "",
             "His prompts, oldest first (time UTC, the agent he typed to):"]
    for p in d["prompts"]:
        lines.append(f"- {p['ts'][11:16]} to {p['position']}: {_one_line(p['text'], PROMPT_CHARS)}")
        if p.get("answer"):
            lines.append(f"  {p['position']} answered: {_one_line(p['answer'], REPLY_CHARS)}")
    lines += ["", "Messages involving the agents he talked to (newest last):"]
    for m in d["messages"]:
        lines.append(f"- {m['ts'][11:16]} {m['sender']} -> {m['to']}: {_one_line(m['text'], MESSAGE_CHARS)}")
    if not d["messages"]:
        lines.append("- none with text")
    lines += ["", "Message counts in the window (both directions):"]
    for (a, b), v in sorted(d["traffic"].items(), key=lambda kv: (-kv[1]["n"], kv[0])):
        lines.append(f"- {a} <-> {b}: {v['n']} messages, {v['chars'] // 1000} KB")
    lines += ["", "Roster (name: summary, organization/unit):"]
    lines += [f"- {r['name']}: {r['summary']} ({r['where']})" for r in d["roster"]]
    lines += ["", "Cases named last time:"]
    lines += [f"- {c['title']}: " + ", ".join(m["position"] for m in c.get("members", [])) for c in d["previous"]] \
        or ["- none"]
    return "\n".join(lines)


# ------------------------------------------------------------------ the model call

def command(model=MODEL):
    import of_capacity
    return [of_capacity.claude_bin(), "-p", None, "--model", model, "--tools", "", "--system-prompt", SYSTEM,
            "--json-schema", json.dumps(SCHEMA), "--output-format", "json", "--no-session-persistence",
            "--settings", '{"disableAllHooks": true}', "--strict-mcp-config", "--disable-slash-commands",
            "--max-turns", "3"]


def account():
    """The account with the most headroom in of_capacity's last reading; the default one without a reading."""
    try:
        import of_capacity
        rows = [dict(v, config_dir=k) for k, v in of_capacity.load_cache().items()
                if not str(k).startswith("_") and isinstance(v, dict)]
        best = of_capacity.best_target(rows)
        if best:
            return os.path.expanduser(best["config_dir"])
    except Exception:  # noqa: BLE001  no reading: the default account
        pass
    return os.path.expanduser("~/.claude")


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
    if not isinstance(j, dict) or not isinstance(j.get("cases"), list):
        raise ValueError(f"no cases in the reply: {out[:200]}")
    return j, meta


def clean(cases, names):
    """Cases as the desk reads them: roster names only, weights in 0..1, no duplicates, the caps kept."""
    out = []
    for c in cases[:MAX_CASES]:
        seen, members = set(), []
        for m in c.get("members") or []:
            pos = str(m.get("position") or "").strip()
            if pos not in names or pos in seen:
                continue
            seen.add(pos)
            try:
                w = max(0.0, min(1.0, float(m.get("weight"))))
            except (TypeError, ValueError):
                w = 0.5
            members.append({"position": pos, "weight": round(w, 2), "why": _one_line(m.get("why"), 120)})
        members.sort(key=lambda m: -m["weight"])
        if members:
            out.append({"title": _one_line(c.get("title"), 60), "summary": _one_line(c.get("summary"), 240),
                        "members": members[:MAX_MEMBERS]})
    return out


def call(prompt, names, config_dir=None, runner=subprocess.run, model=MODEL):
    """One inference: {cases, model, ms, cost_usd, account} or {error, ms}."""
    import of_capacity
    d = config_dir or account()
    argv = [prompt if x is None else x for x in command(model)]
    cwd = os.path.join(of_events.STATE_DIR, "case")
    os.makedirs(cwd, mode=0o700, exist_ok=True)
    t0 = time.time()
    try:
        r = runner(argv, capture_output=True, text=True, timeout=TIMEOUT_S, env=of_capacity.probe_env(d), cwd=cwd)
        j, meta = parse(r.stdout)
    except subprocess.TimeoutExpired:
        return {"error": f"timed out after {TIMEOUT_S}s", "ms": int((time.time() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001  recorded; the next trigger tries again
        return {"error": f"{type(e).__name__}: {str(e)[:200]}", "ms": int((time.time() - t0) * 1000)}
    return {"cases": clean(j["cases"], names), "model": meta.get("model") or model,
            "ms": int((time.time() - t0) * 1000), "cost_usd": meta.get("cost_usd"),
            "account": os.path.basename(d.rstrip("/"))}


# ------------------------------------------------------------------ the record

def latest(conn, max_age_s=6 * 3600, now=None):
    """The last inferred cases: {ts, cases, ...} or None when there is none this recent."""
    now = now or time.time()
    r = conn.execute("SELECT ts, payload FROM event WHERE name = 'of.case_inferred' AND ts >= ? "
                     "ORDER BY ts DESC LIMIT 1", (_iso(now - max_age_s),)).fetchone()
    if not r:
        return None
    p = json.loads(r[1] or "{}")
    p["ts"] = r[0]
    return p


def last_prompt_ts(conn, now=None):
    now = now or time.time()
    p = founder_prompts(conn, _iso(now - WINDOW_S), _iso(now))
    return p[-1]["ts"] if p else None


def infer(conn, positions, now=None, runner=subprocess.run, emit=True, trigger="cli", log=print):
    """Digest, model, record. Returns the payload (with `prompt` when not emitted) or None without prompts."""
    prev = latest(conn, now=now)
    d = digest(conn, positions, now=now, previous=(prev or {}).get("cases"))
    if not d["prompts"]:
        return None
    import of_net
    if runner is subprocess.run and not of_net.online():  # a network drop: the next prompt infers again
        log("of desk case: the model API is unreachable; the case stays as it was")
        return None
    prompt = render(d)
    v = call(prompt, {r["name"] for r in d["roster"]}, runner=runner)
    payload = dict(v, version=VERSION, trigger=trigger, window_s=d["window_s"], prompts=len(d["prompts"]),
                   last_prompt=d["prompts"][-1]["ts"], focal=d["focal"][:MAX_MEMBERS], digest_chars=len(prompt))
    if emit and "cases" in payload:
        of_events.emit("case_inferred", payload)
    elif emit:
        log(f"of desk case: {payload['error']}")
    if not emit:
        payload["prompt"] = prompt
    return payload
