"""Mandate fit: does the work a position hands out belong to the position that gets it? (founder, 2026-11-10)

acme_throughput-owner gave storefront_pricing (new-block bids) "what is 1 ms of our lead worth on pending races"; pending
speed is storefront_search's mandate, and the founder caught it by hand ("может, у нас опять роли перепутались"). The
mandate router reads only paths, and no diagnose rule reads what a task is (RFC 0001 §5.7: drift, wrong-task rate;
docs/lab.md phase 5, not started). This module is the judge: a small model reads one message a position sent another
and the organization's roster, and says whether it hands out work, and whose mandate that work is.

Verdicts: in_mandate (the receiver's), other_position (another position's: `owner`), no_position (nobody's: a role
is missing or a mandate must grow), not_work (a reply, a status, a thanks, a question back).

`of fit replay` runs it over recorded messages and records nothing; the listener's processor comes once the replay
and lab-fit-v0 show it is precise enough.
"""

import json
import os
import re
import subprocess
import time

import of_case
import of_events

VERSION = "mf-v5"  # mf-v5: no receiver's last turn: a misrouted task is what the receiver is doing (circular)
# mf-v4: the thread, the PRs named and the receiver's last turn go to the judge
# mf-v3: a reply continues the receiver's own thread
# mf-v2: mandates as of the message; own-deliverable steps and relays are no new work
# mf-v1: data access is not ownership; whose purpose the work serves decides
MODEL = "haiku"
TIMEOUT_S = 90
TEXT_MAX = 3500
VERDICTS = ("in_mandate", "other_position", "no_position", "not_work")

SYSTEM = """You check how work is divided in an organization of AI agents. Each agent holds a position with a \
mandate: a one-line summary, what it contributes, and when it is done. A manager position hands work to the \
positions under it by message.

You get one message one position sent another, the receiver's mandate, and the organization's roster (every \
position with its summary and contribution).

assigns_work: true only when the message asks the receiver to do new work or to change the work it does: measure, \
build, analyse, decide, change something. A reply, a thanks, a status, a review of work the receiver delivered, \
a question about its own result, or a rule for how to report is not new work. Neither is a step on the \
receiver's own deliverable: queueing, merging, proving or fixing its own PR after a yes, editing its own comment, \
answering a review of its own change. A reply that names another position as the doer, or asks the receiver only to \
pass something on, hands the receiver no work: not_work.

Context you may get: the receiver's message this one replies to, and the PRs the message names (a branch or an \
author that names the receiver means the PR is its own). Use them to tell steps on the receiver's own work from new \
work. That the receiver already does some work does not make it the receiver's: judge it by the mandates.

When the message is a reply to the receiver's own message, it continues the receiver's thread: what the receiver \
proposed, asked for or delivered is its work, and the steps that finish it (a yes, a merge, a queue, a proof, a \
fix, a measurement it offered) are in_mandate or not_work. Judge a reply as other_position or no_position only for \
work it adds that is new and plainly someone else's.

verdict:
- not_work: the message hands out no work.
- in_mandate: the work is the receiver's own: its summary or contribution covers it, or it is a step of work its \
mandate covers.
- other_position: the work is plainly another position's mandate, and that position exists in the roster: name it \
in owner. Use it only when another position's summary or contribution names this work more directly than the \
receiver's does.
- no_position: no position's mandate covers the work: a role is missing, or a mandate must grow to take it.

Judge the purpose of the work, not the data it touches. A position that holds the data, or the tool, is not for \
that the owner of every question asked of it: a tax filing, a hire, a contract, a licence, an audit, a client's \
request, a public text or a new line of business is the mandate of whoever's summary or contribution names that \
purpose, and no_position when nobody's does. Ask: would the receiver's mandate, read literally, list this work as \
something it is there to do? A step that serves the receiver's own stated purpose is in_mandate even when it reads \
another position's data. confidence: 0 to 1. reason: one sentence naming the words of both mandates you compared."""

SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["assigns_work", "verdict", "owner", "confidence", "reason"],
          "properties": {"assigns_work": {"type": "boolean"}, "verdict": {"type": "string", "enum": list(VERDICTS)},
                         "owner": {"type": "string"}, "confidence": {"type": "number"},
                         "reason": {"type": "string"}}}


def _one_line(s, n):
    return " ".join(str(s or "").split())[:n]


def positions_at(positions, ts, cache=None):
    """The registry as it was at `ts` (an ISO time): each organization's registry/positions.yaml from the last
    commit at or before it in the organization's repository, so a replay judges a message by the mandates of its
    day (acme_org, 2026-11-10: mf-v1 quoted mandates written after the messages it judged). An organization whose
    history cannot be read keeps today's registry."""
    import subprocess as sp
    import yaml
    cache = {} if cache is None else cache
    out, roots = [], {}
    for p in positions:
        roots.setdefault(p.get("_root"), []).append(p)
    for root, ps in roots.items():
        try:
            rev = sp.run(["git", "-C", root, "log", "-1", f"--before={ts}", "--format=%H", "--",
                          "registry/positions.yaml"], capture_output=True, text=True, timeout=20).stdout.strip()
            if not rev:
                raise ValueError("no commit before the time")
            if rev not in cache:
                text = sp.run(["git", "-C", root, "show", f"{rev}:registry/positions.yaml"], capture_output=True,
                              text=True, timeout=20, check=True).stdout
                cache[rev] = yaml.safe_load(text) or {}
            doc = cache[rev]
        except Exception:  # noqa: BLE001  no history: today's registry
            out += ps
            continue
        base = ps[0]
        for q in doc.get("positions") or []:
            if not isinstance(q, dict) or not q.get("name"):
                continue
            out.append(dict(q, _root=root, _org=base.get("_org"), _orgid=base.get("_orgid"), _unit=q.get("unit")))
    return out


def mandate(p):
    return {"name": p["name"], "unit": p.get("_unit"), "summary": _one_line(p.get("summary"), 160),
            "contribution": _one_line(p.get("contribution"), 500),
            "done_when": _one_line((p.get("mandate_exit") or {}).get("done_when"), 300),
            "functions": p.get("mandate_functions") or []}


def roster(positions, org):
    return [mandate(p) for p in positions if p.get("_orgid", p.get("_org")) == org
            and (p.get("status") or "active") == "active"]


def strip_header(text):
    """The message body without the relay frame and its reply line."""
    t = re.sub(r"^\s*<pasted_content[^>]*>\s*", "", text or "")
    t = re.sub(r"\s*</pasted_content[^>]*>\s*$", "", t)
    first, _, rest = t.partition("\n")
    if of_case.HEADER.match(first):
        t = rest
    t = re.sub(r"\n?\[reply: of send [^\]]*\]\s*$", "", t.strip())
    return t.strip()


def dispatches(conn, since, until=None, sender=None, receiver=None, context=True, only=None):
    """Messages one position sent another, as the receiver got them: {event_id, ts, sender, receiver, org, text}."""
    q = ("SELECT event_id, ts, position, org, payload FROM event WHERE name = 'of.agent_message_in' AND ts >= ?"
         + (" AND ts < ?" if until else "") + (" AND position = ?" if receiver else "") + " ORDER BY ts")
    args = [since] + ([until] if until else []) + ([receiver] if receiver else [])
    out = []
    for eid, ts, pos, org, pl in conn.execute(q, args).fetchall():
        if only is not None and eid not in only:
            continue
        try:
            text = (json.loads(pl or "{}") or {}).get("text") or ""
        except ValueError:
            continue
        m = of_case.HEADER.match(re.sub(r"^\s*<pasted_content[^>]*>\s*", "", text))
        if not m or (sender and m.group(1) != sender) or m.group(1) == pos:
            continue
        body = strip_header(text)
        first = re.sub(r"^\s*<pasted_content[^>]*>\s*", "", text).split("\n", 1)[0]
        if body:
            rid = re.search(r"\breply to ([0-9a-f-]{8,})", first)
            d = {"event_id": eid, "ts": ts, "sender": m.group(1), "receiver": pos, "org": org, "text": body,
                 "reply": bool(rid), "reply_id": rid.group(1) if rid else None}
            if context:
                d["context"] = context_of(conn, d)
            out.append(d)
    return out


PR_REF = re.compile(r"(?:(?<![\w/])#|/pull/)(\d{3,6})\b")


def context_of(conn, d):
    """What the judge needs beyond the message (founder, 2026-11-10: give it more data): the receiver's message this
    one replies to, the PRs it names with their branch and authoring position, and the receiver's last turn."""
    ctx = {}
    if d.get("reply_id"):
        row = conn.execute("SELECT payload FROM event WHERE name = 'of.agent_message_in' AND position = ? AND ts <= ? "
                           "AND payload LIKE ? ORDER BY ts DESC LIMIT 1",
                           (d["sender"], d["ts"], f"%msg {d['reply_id']}%")).fetchone()
        if row:
            ctx["thread"] = strip_header((json.loads(row[0] or "{}") or {}).get("text") or "")[:1500]
    prs = []
    for n in list(dict.fromkeys(PR_REF.findall(d.get("text") or "")))[:4]:
        st = conn.execute("SELECT payload FROM event WHERE name = 'of.pr_state' AND json_extract(payload, '$.number') = ? "
                          "AND ts <= ? ORDER BY ts DESC LIMIT 1", (int(n), d["ts"])).fetchone()
        wr = conn.execute("SELECT position FROM event WHERE name = 'of.work_record' AND position IS NOT NULL AND "
                          "position != '' AND json_extract(payload, '$.number') = ? LIMIT 1", (int(n),)).fetchone()
        if st:
            p = json.loads(st[0] or "{}")
            prs.append(f"#{n} {_one_line(p.get('title'), 120)}; branch {p.get('head_ref')}; state {p.get('state')}"
                       + (f"; authored by {wr[0]}" if wr else ""))
    if prs:
        ctx["prs"] = prs
    # not the receiver's last turn (mf-v4): a misrouted task is exactly what the receiver is doing, so its turns
    # read as proof that the work is its own (recall on the real labels fell from 0.80 to 0.20)
    return ctx


def user_prompt(d, receiver, others):
    lines = [f"Sender: {d['sender']}", "This message replies to a message the receiver sent: yes" if d.get("reply")
             else "This message replies to a message the receiver sent: no", "", "Receiver's mandate:", json.dumps(receiver, ensure_ascii=False), "",
             "Roster:"]
    lines += [f"- {o['name']} ({o.get('unit') or '-'}): {o['summary']}. {o['contribution']}" for o in others]
    ctx = d.get("context") or {}
    if ctx.get("thread"):
        lines += ["", "The receiver's message this one replies to:", "<<<", ctx["thread"], ">>>"]
    if ctx.get("prs"):
        lines += ["", "PRs the message names:"] + [f"- {x}" for x in ctx["prs"]]
    if ctx.get("receiver_last_turn"):
        lines += ["", "What the receiver said at the end of its last turn before this message:", "<<<",
                  ctx["receiver_last_turn"], ">>>"]
    lines += ["", "The message:", "<<<", d["text"][:TEXT_MAX], ">>>"]
    return "\n".join(lines)


def command(model=MODEL):
    import of_capacity
    return [of_capacity.claude_bin(), "-p", None, "--model", model, "--tools", "", "--system-prompt", SYSTEM,
            "--json-schema", json.dumps(SCHEMA), "--output-format", "json", "--no-session-persistence",
            "--settings", '{"disableAllHooks": true}', "--strict-mcp-config", "--disable-slash-commands",
            "--max-turns", "3"]


def parse(stdout):
    j = json.loads((stdout or "").strip() or "{}")
    if j.get("is_error"):
        raise ValueError(str(j.get("result") or j.get("subtype"))[:200])
    v = j.get("structured_output") or json.loads(j.get("result") or "{}")
    if v.get("verdict") not in VERDICTS:
        raise ValueError(f"no verdict in the reply: {str(v)[:200]}")
    return v, {"cost_usd": j.get("total_cost_usd"), "model": next(iter(j.get("modelUsage") or {}), None)}


def call(prompt, config_dir=None, runner=subprocess.run, model=MODEL):
    """One verdict: {assigns_work, verdict, owner, confidence, reason, model, ms, cost_usd} or {error, ms}."""
    import of_capacity
    d = config_dir or of_case.account()
    argv = [prompt if x is None else x for x in command(model)]
    cwd = os.path.join(of_events.STATE_DIR, "fit")
    os.makedirs(cwd, mode=0o700, exist_ok=True)
    t0 = time.time()
    try:
        r = runner(argv, capture_output=True, text=True, timeout=TIMEOUT_S, env=of_capacity.probe_env(d), cwd=cwd)
        v, meta = parse(r.stdout)
    except subprocess.TimeoutExpired:
        return {"error": f"timed out after {TIMEOUT_S}s", "ms": int((time.time() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:200]}", "ms": int((time.time() - t0) * 1000)}
    return dict(v, **meta, ms=int((time.time() - t0) * 1000))


def judge(d, positions, runner=subprocess.run, model=MODEL, config_dir=None, as_of=False, cache=None):
    """The verdict on one dispatch, or {error}. A receiver or sender outside the registry is skipped. as_of: judge
    by the registry of the message's time (a replay), not today's."""
    if as_of and d.get("ts"):
        positions = positions_at(positions, d["ts"], cache)
    by = {p["name"]: p for p in positions}
    r = by.get(d["receiver"])
    if not r or d["sender"] not in by:
        return {"error": "not a position pair"}
    org = r.get("_orgid", r.get("_org"))
    others = [m for m in roster(positions, org) if m["name"] != r["name"]]
    v = call(user_prompt(d, mandate(r), others), config_dir=config_dir, runner=runner, model=model)
    if v.get("verdict") == "other_position" and v.get("owner") not in by:
        v["owner_unknown"] = True
    return v


def flagged(v, min_confidence=0.6):
    """A verdict worth a person's look: work outside the receiver's mandate, judged with some confidence."""
    return v.get("assigns_work") and v.get("verdict") in ("other_position", "no_position") \
        and (v.get("confidence") or 0) >= min_confidence


# ---- of delegate: before a hand-out, who should do this? (founder, 2026-11-10) ----

DELEGATE_SYSTEM = """You help a manager in an organization of AI agents hand a task to the right position. Each \
position has a mandate: a one-line summary, what it contributes, and when it is done.

You get the task and the roster (every active position with its summary and contribution). Read every mandate. \
Rank the positions whose mandate covers the task's purpose: the one whose summary or contribution names this work \
most directly first. Judge the purpose of the work, not the data it touches: a position that holds the data is not \
for that the owner of every question asked of it. List at most three, each with fit high (its mandate names this \
work) or partial (it covers a part or a step), and one sentence quoting the words of its mandate that decide.

none_fits: true when no position's mandate names the work's purpose; then say in suggestion whether to grow one \
mandate (which, and by what) or to add a position (its summary in one line). When two mandates both name the work, \
say so in overlap: that is for the structure keeper to settle."""

DELEGATE_SCHEMA = {"type": "object", "additionalProperties": False,
                   "required": ["candidates", "none_fits", "suggestion", "overlap"],
                   "properties": {"candidates": {"type": "array", "items": {
                       "type": "object", "additionalProperties": False, "required": ["name", "fit", "why"],
                       "properties": {"name": {"type": "string"}, "fit": {"type": "string", "enum": ["high", "partial"]},
                                      "why": {"type": "string"}}}},
                       "none_fits": {"type": "boolean"}, "suggestion": {"type": "string"},
                       "overlap": {"type": "string"}}}


def delegate(task, positions, org, asker=None, runner=subprocess.run, model=MODEL, config_dir=None):
    """Who should do `task`: {candidates, none_fits, suggestion, overlap, cost_usd} or {error}. `asker`: the
    manager handing it out; it stays in the roster (its own work is a valid answer), marked."""
    import of_capacity
    others = roster(positions, org)
    prompt = "\n".join(([f"Asked by: {asker} (the manager handing the task out; name it only when the work is its "
                          "own)", ""] if asker else []) + ["Roster:"] +
                        [f"- {o['name']} ({o.get('unit') or '-'}): {o['summary']}. {o['contribution']}"
                         for o in others] + ["", "The task:", "<<<", task[:TEXT_MAX], ">>>"])
    argv = [of_capacity.claude_bin(), "-p", prompt, "--model", model, "--tools", "", "--system-prompt", DELEGATE_SYSTEM,
            "--json-schema", json.dumps(DELEGATE_SCHEMA), "--output-format", "json", "--no-session-persistence",
            "--settings", '{"disableAllHooks": true}', "--strict-mcp-config", "--disable-slash-commands",
            "--max-turns", "3"]
    cwd = os.path.join(of_events.STATE_DIR, "fit")
    os.makedirs(cwd, mode=0o700, exist_ok=True)
    try:
        r = runner(argv, capture_output=True, text=True, timeout=TIMEOUT_S,
                   env=of_capacity.probe_env(config_dir or of_case.account()), cwd=cwd)
        j = json.loads((r.stdout or "").strip() or "{}")
        if j.get("is_error"):
            raise ValueError(str(j.get("result"))[:200])
        v = j.get("structured_output") or json.loads(j.get("result") or "{}")
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    known = {p["name"] for p in positions}
    v["candidates"] = [c for c in v.get("candidates") or [] if c.get("name") in known]
    return dict(v, cost_usd=j.get("total_cost_usd"))
