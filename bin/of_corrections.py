"""The founder's corrections as records (RFC 0006 §2.4, step 4; of_architect, 2026-11-09).

The founder asked why acme_throughput-owner repeated mistakes after he had corrected it. Its lessons sat in a memory
index past Claude Code's cut. A lesson that matters to the role belongs in the role's records (the brief, a rules
file), which the structure keeper reviews and every new holder reads. So:

1. judge(): each of his typed prompts in a position's pane (popup answers left out) goes to a small model (Haiku
   through `claude -p`, no tools, no hooks, like the asks judge) with the agent's closing message he answered and the
   position's earlier corrections. Is this a correction, what is the rule in one line, what kind, and does it repeat
   an earlier one? A correction is `of.founder_correction`; a prompt judged is kept in a state file, never judged
   twice.
2. digest(): once a day, each organization's structure keeper gets one `--fyi` message with its positions'
   corrections since the last digest, sent as of_platform (the judge's owner). The keeper decides where each rule
   goes: the brief, a rules file, or nowhere. Nothing is written into a brief automatically.
3. of_studio shows the repeat-correction rate per position and week (of_measures.repeat_corrections).
"""

import json
import os
import re
import subprocess
import sys
import time

import of_events

VERSION = "fc-v1"
MODEL = "haiku"
TIMEOUT_S = 90
TEXT_MAX = 3000        # of his prompt
BEFORE_MAX = 1000      # of the agent's closing message he answered (the store keeps 1 KB)
EARLIER_DAYS = 30      # corrections the model compares with, for repeat_of
LOOKBACK_S = 24 * 3600  # prompts older than this are never judged
KEEP_S = 3 * 24 * 3600  # judged prompt ids kept this long in the state file
DIGEST_HOUR = 5        # UTC: the daily digest goes at the first run at or after this hour
DIGEST_MAX = 6000      # characters of one digest
KINDS = ("output", "method", "scope", "fact", "process", "other")
SENDER = "organic-framework-build:of_platform"

SYSTEM = """You read one message the founder typed to an AI agent that works for him. Decide whether it corrects \
the agent: says the agent did something wrong, or should do it differently from now on.

correction is true for: criticism of how the agent works or reports ("I don't like how you report", \
"an unclear report again", "why do you only analyse Frankfurt?"), a rule it broke or should keep, a wrong fact \
or number it gave, a step it skipped. A question that implies a mistake counts ("why didn't you look at the US?").
correction is false for: new tasks and questions, also questions about the system the agent studies (the bot, \
its triggers, the market, the data: "could we have seen that these triggers would not fire?" asks about the bot, not \
about the agent); approvals; praise, also praise followed by the next request; information; decisions; and requests \
to do more of the same work or to go deeper. A message can hold several things: it is a correction when any part of \
it says the agent's own work or report was wrong or should change.

When it is a correction:
- rule: the lesson as one general English rule the agent's role should follow from now on, imperative, at most 160 \
characters, without this case's numbers ("State every total with its unit and what it covers.", "Analyze every \
region the bot trades in, not one."). Several corrections: the most important one.
- kind: output (how results are presented: format, units, clarity), method (how the work is done: data, analysis, \
checks), scope (what to work on, priorities, initiative), fact (a wrong fact or number), process (coordination, \
asking, the organization's rules), other.
- repeat_of: the id of an earlier correction in the list that states the same rule in substance, else "".
When it is not: rule "", kind "other", repeat_of "".
reason: one short English sentence why."""

SCHEMA = {"type": "object", "additionalProperties": False,
          "properties": {"correction": {"type": "boolean"}, "rule": {"type": "string"},
                         "kind": {"type": "string", "enum": list(KINDS)}, "repeat_of": {"type": "string"},
                         "reason": {"type": "string"}},
          "required": ["correction", "rule", "kind", "repeat_of", "reason"]}


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + "Z"


def _one_line(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[:n - 1] + "…"


def state_path():
    return os.path.join(of_events.STATE_DIR, "corrections.json")


def load_state():
    try:
        with open(state_path()) as fh:
            st = json.load(fh)
    except (OSError, ValueError):
        st = {}
    st.setdefault("judged", {})   # prompt key -> [verdict, at]
    st.setdefault("digests", {})  # org -> ts of the last digest's end
    st.setdefault("structure", {})  # org -> issue ids the last digest listed (new against standing)
    return st


def save_state(st, now):
    st["judged"] = {k: v for k, v in st["judged"].items() if now - (v[1] if isinstance(v, list) else 0) < KEEP_S}
    os.makedirs(of_events.STATE_DIR, mode=0o700, exist_ok=True)
    tmp = state_path() + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(st, fh)
    os.replace(tmp, state_path())


# ------------------------------------------------------------------ what to judge

def prompts(conn, since, until):
    """His typed or pasted prompts into positions' panes: [{event_id, ts, position, session, org, key, text}]."""
    import of_case
    out = []
    for e, ts, pos, sid, org, pl in conn.execute(
            "SELECT event_id, ts, position, session_id, org, payload FROM event WHERE name = 'of.human_prompt' "
            "AND ts >= ? AND ts <= ? AND position IS NOT NULL AND json_extract(payload, '$.origin') IN "
            "('human', 'paste') AND coalesce(json_extract(payload, '$.channel'), '') != 'asks-ui' ORDER BY ts",
            (since, until)):
        p = json.loads(pl or "{}")
        text = p.get("text") or ""
        if not text.strip() or text.startswith(of_case.ANSWER):
            continue
        out.append({"event_id": e, "ts": ts, "position": pos, "session": sid, "org": org, "key": e, "text": text})
    return out


def answered(conn, c):
    """The agent's closing message just before his prompt, in that session: what he reacted to."""
    r = conn.execute("SELECT json_extract(payload, '$.last_message') FROM event WHERE name = 'of.turn_end' "
                     "AND session_id = ? AND ts < ? ORDER BY ts DESC LIMIT 1", (c["session"], c["ts"])).fetchone()
    return (r[0] or "") if r else ""


def earlier(conn, position, before, days=EARLIER_DAYS):
    """[(event_id, rule)] of the position's corrections in the last `days`, oldest first."""
    import of_case
    since = _iso(of_case._ts(before) - days * 86400)
    return [(e, r) for e, r in conn.execute(
        "SELECT event_id, json_extract(payload, '$.rule') FROM event WHERE name = 'of.founder_correction' "
        "AND json_extract(payload, '$.position') = ? AND ts >= ? AND ts < ? ORDER BY ts", (position, since, before))]


def user_prompt(c, before, prior, summary=None):
    rules = "\n".join(f"- {e[-8:]}: {r}" for e, r in prior[-40:]) or "none"
    return (f"Agent: {c['position']}" + (f" ({summary})" if summary else "") + "\n"
            f"The agent's last message before his, shortened:\n<<<\n{_one_line(before, BEFORE_MAX) or '(none)'}\n>>>\n\n"
            f"Earlier corrections of this agent (id: rule):\n{rules}\n\n"
            f"The founder's message:\n<<<\n{c['text'][:TEXT_MAX]}\n>>>")


# ------------------------------------------------------------------ the model call

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
    if not isinstance(j, dict) or not isinstance(j.get("correction"), bool):
        raise ValueError(f"no verdict in the reply: {out[:200]}")
    return j, meta


def call(prompt, config_dir=None, runner=subprocess.run, model=MODEL):
    """{correction, rule, kind, repeat_of, reason, model, ms, cost_usd} or {error, ms}."""
    import of_capacity
    import of_case
    d = config_dir or of_case.account()
    argv = [prompt if x is None else x for x in command(model)]
    cwd = os.path.join(of_events.STATE_DIR, "corrections")
    os.makedirs(cwd, mode=0o700, exist_ok=True)
    t0 = time.time()
    try:
        r = runner(argv, capture_output=True, text=True, timeout=TIMEOUT_S, env=of_capacity.probe_env(d), cwd=cwd)
        j, meta = parse(r.stdout)
    except subprocess.TimeoutExpired:
        return {"error": f"timed out after {TIMEOUT_S}s", "ms": int((time.time() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001  the prompt stays unjudged; the next run tries again
        return {"error": f"{type(e).__name__}: {str(e)[:200]}", "ms": int((time.time() - t0) * 1000)}
    return {"correction": j["correction"], "rule": _one_line(j.get("rule"), 200),
            "kind": j.get("kind") if j.get("kind") in KINDS else "other",
            "repeat_of": str(j.get("repeat_of") or "").strip(), "reason": _one_line(j.get("reason"), 300),
            "model": meta.get("model") or model, "ms": int((time.time() - t0) * 1000),
            "cost_usd": meta.get("cost_usd")}


# ------------------------------------------------------------------ the runs

def judge(conn, positions=(), now=None, runner=subprocess.run, emit=True, log=print, limit=40):
    """Judge his prompts of the last LOOKBACK_S not judged yet. Returns [(prompt, verdict)]."""
    now = now or time.time()
    import of_net
    if runner is subprocess.run and not of_net.online():  # a network drop: each call would wait out its timeout
        log("of corrections: the model API is unreachable; the prompts wait for the next run")
        return []
    st = load_state()
    summaries = {p["name"]: p.get("summary") for p in positions or ()}
    todo = [c for c in prompts(conn, _iso(now - LOOKBACK_S), _iso(now)) if c["key"] not in st["judged"]][:limit]
    out = []
    for c in todo:
        prior = earlier(conn, c["position"], c["ts"])
        v = call(user_prompt(c, answered(conn, c), prior, summaries.get(c["position"])), runner=runner)
        out.append((c, v))
        if "error" in v:
            log(f"of corrections: {c['position']} {c['ts']}: {v['error']}")
            continue
        if v["correction"] and v["rule"]:
            rep = next((e for e, _ in prior if v["repeat_of"] and e[-8:] == v["repeat_of"][-8:]), None)
            payload = {"position": c["position"], "prompt_id": c["event_id"],  # the of.human_prompt event id
                       "rule": v["rule"], "kind": v["kind"], "repeat_of": rep, "quote": _one_line(c["text"], 300),
                       "reason": v["reason"], "judge": VERSION, "model": v["model"], "ms": v["ms"],
                       "cost_usd": v["cost_usd"]}
            if emit:
                rec = of_events.record("founder_correction", payload, position=c["position"], session=c["session"],
                                       org=c["org"])
                rec["timestamp"] = c["ts"]  # at his prompt's time: the weeks count when he corrected
                of_events.append(rec)
        if emit:
            st["judged"][c["key"]] = ["correction" if v["correction"] else "no", now]
    if emit:
        save_state(st, now)
    return out


def corrections(conn, after_rowid=0, top=None, since=None):
    """[{rowid, ts, event_id, position, org, rule, kind, repeat_of}] recorded after a row of the log (a digest takes
    what was written since the last one, also corrections of prompts older than it), oldest first."""
    out = []
    for rowid, e, ts, org, pl in conn.execute(
            "SELECT rowid, event_id, ts, org, payload FROM event WHERE name = 'of.founder_correction' AND rowid > ? "
            "AND rowid <= ? AND ts >= ? ORDER BY ts", (after_rowid, top if top is not None else 2 ** 62, since or "")):
        p = json.loads(pl or "{}")
        out.append({"rowid": rowid, "ts": ts, "event_id": e, "position": p.get("position"), "org": org,
                    "rule": p.get("rule"), "kind": p.get("kind"), "repeat_of": p.get("repeat_of")})
    return out


def digest_text(rows):
    by = {}
    for r in rows:
        by.setdefault(r["position"], []).append(r)
    since, until = rows[0]["ts"], rows[-1]["ts"]
    lines = [f"The founder's corrections, {since[:16]}Z to {until[:16]}Z (of_platform's judge, RFC 0006 section 2.4). "
             "For each rule, you decide where it goes: the position's brief, a rules file, or nowhere. A repeat means "
             "an earlier correction of the same rule did not stick. Misjudged items: tell of_platform."]
    for pos in sorted(by, key=lambda x: (-len(by[x]), x)):
        rs = by[pos]
        reps = sum(1 for r in rs if r["repeat_of"])
        lines.append(f"{pos}: {len(rs)}" + (f", {reps} repeated" if reps else ""))
        for r in rs:
            lines.append(f"- {r['ts'][11:16]}Z [{r['kind']}] {r['rule']}"
                         + (f" (repeats {r['repeat_of'][-8:]})" if r["repeat_of"] else "") + f" ({r['event_id'][-8:]})")
    text = "\n".join(lines)
    return text if len(text) <= DIGEST_MAX else text[:DIGEST_MAX - 40].rsplit("\n", 1)[0] + "\n… (more: of corrections)"


STRUCTURE_HEAD = ("Structure issues for you to decide (docs/diagnose.md 2.23-2.26 and the other structure_keeper "
                  "rules): bring a diff, or label one that is wrong: of issues label <id> "
                  "false_positive|true_but_not_now --note '...'")


def structure_issues(org, runner=subprocess.run):
    """The open issues the structure keeper decides, from of_diagnose as `of model issues` computes them (of_studio's
    computation; labelled issues already drop out). None when the computation fails: the digest then says nothing
    of structure rather than "none". of_architect and of_field-analyst, 2026-11-10: structure issues reach the
    keeper in this digest, never as separate messages."""
    here = os.path.dirname(os.path.realpath(__file__))
    try:
        r = runner([sys.executable, os.path.join(here, "of"), "model", "issues", "--org", org, "--decider",
                    "structure_keeper", "--json"],
                   capture_output=True, text=True, timeout=600, env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
        return json.loads(r.stdout or "{}").get("issues") or [] if r.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


STRUCTURE_ORDER = ("unkept-system", "off-map-work", "cell-gap", "cell-overlap", "orphan-work", "over-coupled", "chatty")


def _rule(i):
    """`ap.unkept-system@0` -> `unkept-system`."""
    return str(i.get("rule_version") or "").split("@")[0].removeprefix("ap.")


def structure_text(issues, sent, org, keeper):
    """The digest's structure section, by rule in of_field-analyst's order (the structural gaps before the noisy
    traffic rules; 2026-11-10: chatty would have put 56 lines before the 8 that matter), a line per rule with its
    count, then in full the issues the previous digest did not list, in the run's order; the rest as one line."""
    new = [i for i in issues if i["issue_id"] not in sent]
    if not new and not issues:
        return None
    by = {}
    for i in issues:
        by.setdefault(_rule(i), []).append(i)
    rank = {r: n for n, r in enumerate(STRUCTURE_ORDER)}
    lines = [STRUCTURE_HEAD]
    for r in sorted(by, key=lambda r: (rank.get(r, len(rank)), r)):
        fresh = [i for i in by[r] if i["issue_id"] not in sent]
        lines.append(f"{r}: {len(by[r])}" + (f", {len(fresh)} new" if len(fresh) != len(by[r]) else ""))
        lines += [f"- [{i['rule_version']}{' ' + i['part'] if i.get('part') else ''}] {_one_line(i['summary'], 220)} "
                  f"({i['issue_id']})" for i in fresh]
    if len(issues) > len(new):
        lines.append(f"{len(issues) - len(new)} still open from earlier digests: of model issues --org {org} --for {keeper}")
    return "\n".join(lines)


def due_digests(conn, positions, now=None, force=False, issues_of=structure_issues):
    """[(org, keeper, top rowid, text or None, structure issue ids)] for organizations whose digest is due: once per
    UTC day, at or after DIGEST_HOUR, with the corrections recorded since the last one (at most 7 days back) and the
    open structure issues. An organization with neither gets nothing."""
    import of_pulse
    now = now or time.time()
    st = load_state()
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    if not force and time.gmtime(now).tm_hour < DIGEST_HOUR:
        return []
    orgs = {}
    for p in positions:
        orgs.setdefault(p.get("_orgid") or p.get("_org"), p.get("_org"))
    top = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM event").fetchone()[0]
    out = []
    for orgid, org in sorted(orgs.items()):
        last = st["digests"].get(orgid) or {}
        if not force and last.get("day") == day:
            continue
        rows = [r for r in corrections(conn, last.get("rowid", 0), top, _iso(now - 7 * 86400))
                if r["org"] in (orgid, org)]
        keeper = of_pulse.structure_keeper(positions, org)
        issues = issues_of(org) if keeper else None
        parts = [digest_text(rows) if rows else None,
                 structure_text(issues, set(st["structure"].get(orgid) or []), org, keeper) if issues else None]
        text = "\n\n".join(x for x in parts if x) or None
        out.append((orgid, keeper, top, text, [i["issue_id"] for i in issues] if issues is not None else None))
    return out


def send_digests(conn, positions, now=None, force=False, dry_run=False, log=print, send=None, issues_of=structure_issues):
    """Send each due digest as an --fyi message to the keeper; an organization with nothing recorded gets nothing,
    and its day still counts as done."""
    now = now or time.time()
    st = load_state()
    sent = 0
    for orgid, keeper, top, text, ids in due_digests(conn, positions, now, force, issues_of):
        if text and keeper:
            if dry_run:
                log(f"to {orgid}:{keeper}:\n{text}\n")
                continue
            ok = (send or _send)(f"{orgid}:{keeper}", text, log)
            if not ok:
                continue  # tried again at the next run
            sent += 1
        if not dry_run:
            st["digests"][orgid] = {"day": time.strftime("%Y-%m-%d", time.gmtime(now)), "rowid": top}
            if ids is not None:
                st["structure"][orgid] = ids
    if not dry_run:
        save_state(st, now)
    return sent


def _send(to, text, log):
    here = os.path.dirname(os.path.realpath(__file__))
    r = subprocess.run([sys.executable, os.path.join(here, "of"), "send", to, "--from", SENDER, "--fyi", text],
                       capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
    log(f"of corrections: digest to {to}: {(r.stdout or r.stderr).strip()[-200:]}")
    return r.returncode == 0
