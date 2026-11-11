"""of asks judge: does an agent's closing message wait on its human owner? (founder, ask ddd6e3a3, 2026-11-07)

The founder's list holds what agents file with `of ask`, his tasks, permissions and dialogs. Guessing questions from
text by rules (q-v0..q-v4) filled it with false items; agents that asked in their own pane were still missed. So:

1. The Stop hook writes a heuristic candidate when any question classifier fires on the closing message, queues it
   here (a marker in ~/.local/state/of/judge/pending) and starts `of asks judge --pending` in the background: the
   hook stays within its 5 s and never blocks the agent. With judge.yaml `gate: all` it queues every turn of a
   position's session (`turn: true`, no candidate written).
2. A small model (Haiku through `claude -p`: no tools, no hooks, no MCP, no session file, the agent's own account)
   reads the message and answers: does the agent wait on the owner, and what is the decision, in one Russian line.
   The verdict is `of.agent_question_judged`; a candidate the judge clears never reaches anyone.
3. When the agent waits on the owner and filed no `of ask`, OF files the question for it at once, where its own
   `of ask` would go (the founder, or its unit owner in a delegated unit): `of.agent_question` {source: pane}, an ask
   like any other, kept until answered or withdrawn, never closed because the session went on (hand-over cc2ed950,
   reports/asks-pane-questions-2026-11-11.md). Its last option is "не вопрос". OF tells the agent once: at once after
   a turn a peer or a tool started; after CORRECT_HUMAN_MIN when the founder started it (he may answer right there,
   and his words in that session answer it).
   A position the owner converses with in its pane (registry `owner_chat: true`) is skipped before the model.
4. Verdicts recorded before 2026-11-11 opened the candidate itself, shown after LIST_AFTER_MIN in a separate section.

The pulse (bin/of_pulse.py) runs pulse_pass(): candidates nobody judged, and the notes not delivered yet.
Other engines (a Codex model) plug in through ~/.config/of/judge.yaml `command:` once installed on the machine.
"""

import fcntl
import json
import os
import re
import subprocess
import sys
import time

import of_events

VERSION = "j-v4"  # j-v4: a proposal that waits on his yes is a question; other positions' named asks are listed; j-v3: an ask is known by its first 8 characters too; j-v2: a message naming only filed asks is cleared
MODEL = "haiku"
TIMEOUT_S = 60
SLOTS = 2                 # judges running at once on this machine (each is a short `claude -p`)
SLOT_WAIT_S = 30          # a judge with no free slot leaves its marker for a running judge or the pulse
TEXT_MAX = 6000           # the closing message's tail the model reads
CORRECT_HUMAN_MIN = 10    # after a turn the founder started: he may be answering in the pane
LIST_AFTER_MIN = 30       # a judged question still open this long reaches his list (separate section)
MAX_AGE_H = 24            # older candidates are never judged
PULSE_MIN_AGE_S = 180     # the pulse takes candidates the hook's judge did not get to within this
CORRECT_EVERY_MIN = 30    # one correction per session in this window
MIN_HEADROOM = 5          # percent: below this in of_capacity's last reading, the judge uses another account

SCHEMA = {"type": "object", "additionalProperties": False,
          "properties": {"waits_owner": {"type": "boolean"},
                         "question": {"type": "string"},
                         "options": {"type": "array", "items": {"type": "string"}},
                         "reason": {"type": "string"}},
          "required": ["waits_owner", "question", "options", "reason"]}

SYSTEM = """You check the last message an AI agent wrote before it stopped and went idle. The agent works for a \
human owner (the founder) in an organization of agents. Decide one thing: does the message leave a decision or an \
answer to the human owner, so that something does not go ahead until he replies?

waits_owner is true when the message asks the owner to decide, choose, confirm, approve, give something \
(a key, a login, access) or answer a question, and a next step depends on that answer. It is also true for a \
proposal that needs his yes before the agent acts and that reaches past the agent's own task: it changes other \
agents or positions, money or limits, production systems, or the organization's rules ("Скажи, если сделать", \
"могу перевести X, делать?", "предлагаю …, да?", "решение за тобой"). The agent's other work may go on; the \
proposal still waits on him. A question to the owner that the message says it waits on (a WAITING ON line naming \
him, "awaiting owner answers", "ждёт твоих ответов") that is in neither list of asks below and names no ask id was \
never filed: waits_owner is true. A wait on another position (its «да», its review, its reply) is not a wait on the \
owner.

waits_owner is false for:
- reports, summaries and status updates, also when they end with "?" or mention a decision already taken;
- plans the agent will carry out by itself, and steps it says it is taking next;
- small optional extras inside the agent's own task that nothing waits on ("если хочешь, могу ещё добавить тест", \
"want me to also…");
- questions to another agent or position (names like acme_org, of_server, platform_owner) or answers to one. When \
another agent started this turn, the message is a reply to it: a question or a request for a «да» in it is \
addressed to that agent, unless the message clearly turns to the human founder (names him or says it waits on the \
founder);
- rhetorical questions, questions it answers itself, questions it quotes from others;
- a reminder of a question already filed with of ask: an 8-character ask id such as 1a2b3c4d is named, or the \
question is in one of the lists of asks already filed (the agent's own, or another position's that the message \
names);
- waiting on something other than the owner: CI, a deploy, a time window, another agent.

question: when waits_owner is true, the decision in one plain Russian line that the owner can answer without \
any other context; otherwise "". options: the choices the agent offered, short, in its words ([] if none). \
reason: one short English sentence why."""


def config():
    """~/.config/of/judge.yaml: enabled (default true), model, command (an argv for another engine; the user
    prompt goes to its stdin and it prints the JSON object)."""
    cfg = {"enabled": True, "model": MODEL, "command": None}
    path = of_events.config_path("judge.yaml")
    if os.path.exists(path):
        try:
            import yaml
            with open(path) as fh:
                cfg.update(yaml.safe_load(fh) or {})
        except Exception:  # noqa: BLE001  a broken file leaves the defaults
            pass
    return cfg


def state_dir(*parts):
    d = os.path.join(of_events.STATE_DIR, "judge", *parts)
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


# ------------------------------------------------------------------ the model call

ORIGINS = {"human": "the founder, typing in this pane", "paste": "the founder, pasting into this pane",
           "peer": "a message from another agent", "injected": "a tool (the relay, a scheduler or OF itself)",
           "system": "automation", "scheduled": "a scheduler"}


def user_prompt(text, position=None, origin=None, peer=None, filed=(), named=()):
    """filed: [(ask id, text)] the agent's own open asks; named: [(ask id, text, position)] other positions' open asks
    for the founder that the message names by id (j-v4: "ваше «да» на e0e53b7f" read as new when another filed it)."""
    who = ORIGINS.get(origin or "", "unknown")
    if origin == "peer" and peer:
        who += f" ({peer})"
    asks = "\n".join(f"- {i[-8:]} (also cited as {i[:8]}): {t[:200]}" for i, t in filed) or "none"
    others = "\n".join(f"- {i[-8:]} (also cited as {i[:8]}), filed by {p or 'unknown'}: {t[:200]}"
                       for i, t, p in named) or "none"
    return (f"Agent: {position or 'unknown'}\nThis turn was started by: {who}\n"
            f"Questions this agent already filed with of ask and still open:\n{asks}\n"
            f"Open asks of other positions that the message names:\n{others}\n\n"
            f"The agent's last message:\n<<<\n{text[-TEXT_MAX:]}\n>>>")


PROMPT_SLOT = object()  # where the user prompt goes in the argv


def command(cfg):
    if cfg.get("command"):
        return list(cfg["command"]), True
    import of_capacity
    return [of_capacity.claude_bin(), "-p", PROMPT_SLOT, "--model", str(cfg.get("model") or MODEL), "--tools", "",
            "--system-prompt", SYSTEM, "--json-schema", json.dumps(SCHEMA), "--output-format", "json",
            "--no-session-persistence", "--settings", '{"disableAllHooks": true}', "--strict-mcp-config",
            "--disable-slash-commands", "--max-turns", "3"], False


def env(config_dir=None):
    """The capacity probe's environment (no herdr binding for the nested run, no SSH agent, OF_AUTOMATION): the
    nested harness must never re-bind the agent's pane (AGENTS.md)."""
    import of_capacity
    # the store keeps config dirs as ~/…: a literal "~" in CLAUDE_CONFIG_DIR reads as "not logged in" (2026-11-07)
    d = os.path.expanduser(config_dir or os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude")
    e = of_capacity.probe_env(d)
    e["OF_JUDGE"] = "1"
    return e


def accounts(config_dir=None):
    """The accounts to try, in order: the agent's own, unless of_capacity's last reading says it is spent (then the
    one with the most headroom first). The judgment is one small call; a spent account must not hide a question."""
    d = os.path.expanduser(config_dir or os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude")
    try:
        import of_capacity
        rows = [dict(v, config_dir=k) for k, v in of_capacity.load_cache().items()
                if not str(k).startswith("_") and isinstance(v, dict)]
        own = next((r for r in rows if os.path.normpath(r["config_dir"]) == os.path.normpath(d)), None)
        best = of_capacity.best_target(rows, exclude=d)
        h = of_capacity.headroom(own) if own else None
        if best and (h is None or h < MIN_HEADROOM):
            return [best["config_dir"], d]
        return [d] + ([best["config_dir"]] if best else [])
    except Exception:  # noqa: BLE001  no reading: the agent's own account only
        return [d]


def judge_text(prompt, config_dir=None, runner=subprocess.run):
    """call() on the first account that answers."""
    v = {"verdict": "error", "error": "no account"}
    for d in accounts(config_dir)[:2]:
        v = call(prompt, config_dir=d, runner=runner)
        if v["verdict"] != "error":
            v["account"] = os.path.basename(d.rstrip("/"))
            return v
    return v


def parse(stdout):
    """The verdict from `claude -p --output-format json` (structured_output), or from a bare JSON object."""
    out = (stdout or "").strip()
    try:
        j = json.loads(out)
    except ValueError:
        m = re.search(r"\{.*\}", out, re.S)
        j = json.loads(m.group(0)) if m else {}
    meta = {}
    if isinstance(j, dict) and ("structured_output" in j or j.get("type") == "result"):
        meta = {"cost_usd": j.get("total_cost_usd"), "api_ms": j.get("duration_api_ms") or j.get("duration_ms"),
                "model": next(iter(j.get("modelUsage") or {}), None)}
        if j.get("is_error"):
            raise ValueError(str(j.get("result") or j.get("subtype"))[:200])
        j = j.get("structured_output") or json.loads(j.get("result") or "{}")
    if not isinstance(j, dict) or not isinstance(j.get("waits_owner"), bool):
        raise ValueError(f"no verdict in the reply: {out[:200]}")
    return j, meta


def call(prompt, config_dir=None, runner=subprocess.run, cfg=None):
    """One judgment: {verdict, question, options, reason, model, ms, cost_usd} or {verdict: error, error}."""
    cfg = cfg or config()
    argv, stdin = command(cfg)
    t0 = time.time()
    try:
        argv = [prompt if x is PROMPT_SLOT else x for x in argv]  # right after -p: --tools takes several values
        r = runner(argv, input=prompt if stdin else None, capture_output=True,
                   text=True, timeout=TIMEOUT_S, env=env(config_dir), cwd=state_dir("cwd"))
        j, meta = parse(r.stdout)
    except subprocess.TimeoutExpired:
        return {"verdict": "error", "error": f"timed out after {TIMEOUT_S}s", "ms": int((time.time() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001  any failure is recorded; the pulse may try again
        return {"verdict": "error", "error": f"{type(e).__name__}: {str(e)[:200]}", "ms": int((time.time() - t0) * 1000)}
    return {"verdict": "owner_question" if j["waits_owner"] else "not_a_question",
            "question": " ".join(str(j.get("question") or "").split())[:400],
            "options": [" ".join(str(o).split())[:120] for o in (j.get("options") or [])][:9],
            "reason": str(j.get("reason") or "")[:300], "model": meta.get("model") or str(cfg.get("model") or MODEL),
            "ms": int((time.time() - t0) * 1000), "cost_usd": meta.get("cost_usd")}


# ------------------------------------------------------------------ candidates

def queue(c):
    """The hook's hand-off: one marker per candidate {ask_id, session, position, org, ts, text, origin, peer,
    turn_start, pane, config_dir}. Mode 600: it holds the (masked) closing message."""
    path = os.path.join(state_dir("pending"), re.sub(r"[^0-9a-zA-Z_-]", "", c["ask_id"]) + ".json")
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(c, fh, ensure_ascii=False)
    return path


def spawn(args=("--pending",)):
    """Start a judge in the background, detached from the caller (the hook, the pulse); never waits."""
    here = os.path.dirname(os.path.realpath(__file__))
    try:
        subprocess.Popen([sys.executable, os.path.join(here, "of"), "asks", "judge", *args],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
        return True
    except Exception:  # noqa: BLE001
        return False


def from_event(conn, ask_id):
    """A candidate rebuilt from the store (the pulse's path): the full message from the transcript when it is
    there, else the stored tail."""
    row = conn.execute("SELECT event_id, ts, session_id, position, org, transcript, config_dir, payload FROM event "
                       "WHERE event_id = ? AND name = 'of.agent_question'", (ask_id,)).fetchone()
    if not row:
        return None
    eid, ts, sid, pos, org, tr, cdir, pl = row
    p = json.loads(pl or "{}")
    tail = ((p.get("questions") or [{}])[0] or {}).get("question") or ""
    text = _mask(text_at(tr, tail)) or tail
    origin, peer, start = turn_of(conn, sid, ts)
    return {"ask_id": eid, "session": sid, "position": pos, "org": org, "ts": ts, "text": text, "origin": origin,
            "peer": peer, "turn_start": start, "pane": None, "config_dir": cdir}


def turn_of(conn, sid, ts):
    """Who started the turn that ended at ts: (origin, peer, start ts). A message the agent pulled itself during the
    turn (`of inbox --mine --take`, delivery pulled) starts no turn: of_capacity, 2026-11-10 20:16Z, pulled four of
    of_platform's messages while answering the founder, and the turn read as of_platform's."""
    row = conn.execute("SELECT name, ts, payload FROM event WHERE session_id = ? AND ts <= ? AND (name = "
                       "'of.human_prompt' OR (name = 'of.agent_message_in' AND coalesce(json_extract(payload, "
                       "'$.delivery'), '') NOT IN ('pulled', 'failed'))) ORDER BY ts DESC LIMIT 1", (sid, ts)).fetchone()
    if not row:
        return None, None, None
    n, t, pl = row
    p = json.loads(pl or "{}")
    if n == "of.agent_message_in":
        return "peer", p.get("from"), t
    return p.get("origin") or "human", None, t


def _mask(text):
    """The hook's masking (secrets out of what a model reads), as the hook queues a candidate."""
    try:
        import of_hook
        return of_hook.mask(text)
    except Exception:  # noqa: BLE001
        return text


TEXT_AT_BEFORE = 4 << 20  # bytes read before the offset
TEXT_AT_AFTER = 1 << 20   # and after it: the Stop hook can run before the closing text reaches the file


def text_at(transcript, hint, before=TEXT_AT_BEFORE, after=TEXT_AT_AFTER):
    """The closing text a candidate was written for, from the event's transcript column ({"path", "offset"}: the
    file's size when the hook ran) and the tail the event stored (`hint`): the assistant text nearest the offset,
    on either side, that ends like the hint. '' when there is none, so the caller keeps the stored tail rather
    than another turn's text. The Stop hook often runs before Claude Code writes the closing text, so it is after
    the offset as often as before it (2026-11-10, a replay of 60 candidates).
    (2026-11-10: from_event passed the column itself as a path, so a retried candidate was judged on its last 600
    characters; reading the file's last text instead would have judged a later turn.)"""
    key = " ".join((hint or "").split())[-80:]
    if not key:
        return ""
    try:
        t = json.loads(transcript) if isinstance(transcript, str) else (transcript or {})
        path, offset = os.path.expanduser(t["path"]), int(t["offset"])
        with open(path, "rb") as fh:
            start = max(0, offset - before)
            fh.seek(start)
            chunk = fh.read(offset - start + after)
    except (OSError, ValueError, KeyError, TypeError):
        return ""
    best, best_d, pos = "", None, start
    for line in chunk.split(b"\n"):
        here, pos = pos, pos + len(line) + 1
        if b'"assistant"' not in line:
            continue
        try:
            j = json.loads(line)
        except ValueError:
            continue  # a line cut by the window
        if j.get("type") != "assistant":
            continue
        text = "\n".join(b.get("text") or "" for b in ((j.get("message") or {}).get("content") or [])
                         if isinstance(b, dict) and b.get("type") == "text")
        if text.strip() and " ".join(_mask(text).split()).endswith(key):
            d = abs(here - offset)
            if best_d is None or d < best_d:
                best, best_d = text, d
    return best


def filed(conn, position, since=None):
    """(ask id, text) of the position's open declared asks; with since, only those filed at or after it."""
    import of_asks
    import of_listen
    if of_listen.fresh(conn):
        asks = of_listen.read_asks(conn)
    else:
        asks = of_asks.project(of_asks.load(conn, since_iso=time.strftime("%Y-%m-%dT%H:%M:%S",
                                                                         time.gmtime(time.time() - 7 * 86400))))
    return [(a["event_id"], a["text"]) for a in asks if a.get("position") == position and a["state"] == "open"
            and a["kind"].startswith(("question (declared", "task")) and (not since or a["ts"] >= since)]


ASK_ID = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{8}(?![0-9A-Za-z])")
KNOWN_DAYS = 30


def only_filed_ids(conn, text, now=None):
    """The ask ids a closing message names, when it asks nothing new; else None (2026-11-09: a closing line
    WAITING ON listed asks already on the founder's queue, and the model filed it twice as a new card for him). The
    message has no question mark, names at least one 8-character id, every such id is an ask OF knows (any state, the
    last KNOWN_DAYS days: a decision cited by its id counts), one of them at least is open for the founder, and
    without the lines that name them it asks the owner nothing (j-v4: a keeper's "вопрос про 1Password", never filed,
    stood beside its card ids and was cleared three times)."""
    if "?" in text:
        return None
    ids = set(ASK_ID.findall(text))
    if not ids or says_more(text, ids):
        return None
    now = now or time.time()
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now - KNOWN_DAYS * 86400))
    known = {x for (e,) in conn.execute("SELECT event_id FROM event WHERE name = 'of.agent_question' AND ts >= ?",
                                        (since,)) for x in _short(e)}
    if not ids <= known:
        return None
    return sorted(ids) if ids & founders_open(conn) else None


_UNFILED_ITEM = re.compile(r"(?i)\b(?:вопрос\w*|question\w*|решени\w*|decision\w*|выбор\w*|choice|одобрени\w*|approval)\b")


def says_more(text, ids):
    """Does the message ask the owner something besides these ids: on the lines that name none of them (q-v5's
    requests and owner headings, or a waiting note), or as an item with no id in a list that names them ("WAITING ON:
    от тебя — `0fd223f2`, `2c43429e`, вопрос про 1Password": a question never filed)?"""
    import of_classify
    lines = (text or "").splitlines()
    rest = "\n".join(ln for ln in lines if not any(i in ln for i in ids))
    if of_classify._request_v5(rest) or of_classify._heading_v5(rest) or of_classify._NOTE_WAITS_OWNER_V5.search(rest):
        return True
    for ln in lines:
        if any(i in ln for i in ids):
            items = re.split(r"[,;]", re.sub(r"\([^)]*\)", " ", ln))
            if any(not any(i in it for i in ids) and _UNFILED_ITEM.search(it) for it in items):
                return True
    return False


def open_founder_asks(conn):
    """Every ask or task open for the founder (the listener's view when fresh, else projected from 7 days)."""
    import of_asks
    import of_listen
    if of_listen.fresh(conn):
        asks = of_listen.read_asks(conn)
    else:
        asks = of_asks.project(of_asks.load(conn, since_iso=time.strftime("%Y-%m-%dT%H:%M:%S",
                                                                         time.gmtime(time.time() - 7 * 86400))))
    return [a for a in asks if a["state"] == "open" and (a.get("to") or "founder") in ("founder", "owner")]


def founders_open(conn):
    """The last 8 characters of every ask or task open for the founder."""
    return {x for a in open_founder_asks(conn) for x in _short(a["event_id"])}


def named_asks(asks, text, position=None):
    """[(ask id, text, position)] of the open founder asks in `asks` that the message names by an 8-character id,
    filed by another position."""
    ids = set(ASK_ID.findall(text or ""))
    return [(a["event_id"], a.get("text") or "", a.get("position")) for a in asks
            if a.get("position") != position and ids & _short(a["event_id"])]


def _short(event_id):
    """The 8-character forms an agent cites an ask by: OF's own (the last 8), and the first 8, which an agent reading
    the event store uses (2026-11-10: a keeper named its diff card 01a1c233, and the judge did not know it)."""
    return {event_id[-8:], event_id[:8]}


_CLI = []


def positions(orgs_file=None):
    """Every position of the listed organizations (bin/of load_positions), read on every call: the listener is
    long-lived and registries change. [] when there is no organization list or a registry is broken."""
    try:
        if not _CLI:
            import importlib.machinery
            import importlib.util
            here = os.path.dirname(os.path.realpath(__file__))
            loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(here, "of"))
            mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli", loader))
            loader.exec_module(mod)
            _CLI.append(mod)
        out, _ = _CLI[0].load_positions(orgs_file or os.environ.get("OF_ORGS") or of_events.config_path("orgs.yaml"))
        return out
    except (SystemExit, Exception):  # noqa: BLE001
        return []


def owner_chat(orgs_file=None):
    """Positions the owner converses with in their own pane (registry `owner_chat: true`, set by the structure keeper
    on the owner's word). of_conductor, 2026-11-10: the founder types into its pane and reads its answers there, so a
    question it leaves there for him is seen where it is asked; a correction would make it file a second copy as a
    card. Its decisions that are the owner's still go as asks."""
    return {p.get("name") for p in positions(orgs_file) if p.get("owner_chat") is True}


def route(position, orgs_file=None):
    """(to, delegation id, position dict): where this position's own `of ask` goes by default (bin/of ask_target):
    the owner of its unit when the unit is delegated, else the founder. A pane question OF files goes there too, and
    reaches the founder from a delegated unit only by the delegation's escalate_when (of_architect, 2026-11-11)."""
    p = next((x for x in positions(orgs_file) if x.get("name") == position), None)
    if not p or not _CLI:
        return "founder", None, p or {}
    to = _CLI[0].ask_target(p)
    return to, ((p.get("_delegation") or {}).get("id") if to != "founder" else None), p


def gate():
    """Which closing messages reach the judge: "detectors" (a question classifier fired; the default) or "all"
    (every turn of a position's session). judge.yaml `gate: all` opens it once the pane cards pass the stop rule
    (reports/asks-pane-questions-2026-11-11.md)."""
    return "all" if str(config().get("gate") or "").strip() == "all" else "detectors"


def judged(conn, ask_id):
    """The verdicts already written for this candidate."""
    return [json.loads(p or "{}").get("verdict") for (p,) in conn.execute(
        "SELECT payload FROM event WHERE name = 'of.agent_question_judged' AND json_extract(payload, '$.ask_id') = ?",
        (ask_id,))]


def corrected(conn, session, since_iso):
    return conn.execute("SELECT count(*) FROM event WHERE name = 'of.nudge_sent' AND session_id = ? AND ts >= ? "
                        "AND json_extract(payload, '$.kind') = 'ask_correction'", (session, since_iso)).fetchone()[0] > 0


def noted(conn, ask_id):
    """Was the agent told about this pane card already?"""
    return conn.execute("SELECT count(*) FROM event WHERE name = 'of.nudge_sent' AND json_extract(payload, '$.kind') = "
                        "'ask_correction' AND json_extract(payload, '$.ask_id') = ?", (ask_id,)).fetchone()[0] > 0


def session_moved(conn, session, after):
    """Did the session go on after the candidate (a new prompt, a peer's message, another turn)?"""
    return conn.execute("SELECT count(*) FROM event WHERE session_id = ? AND ts > ? AND name IN ('of.human_prompt', "
                        "'of.agent_message_in', 'of.turn_end')", (session, after)).fetchone()[0] > 0


# ------------------------------------------------------------------ one candidate

NOT_A_QUESTION = "не вопрос"  # the last option of every pane card: the founder's one key for a false card


def file_pane_ask(c, verdict):
    """File the judged question as the agent's own ask would be filed (source pane), where its `of ask` would go:
    the founder, or its unit owner in a delegated unit. It stays until answered or withdrawn; it never closes because
    the session went on (from 2026-11-08 to 11-10, 25 of 43 judged questions closed as moved_on, unfiled). An estimate
    of effort or duration in its words is cut from the card (bin/of_ask_text.py), not refused. Returns (ask id, to)."""
    to, did, p = route(c.get("position"))
    import of_ask_text  # no estimate of effort or duration on his card (founder, 2026-11-11): cut, never refused
    opts = [of_ask_text.drop(o) for o in (verdict.get("options") or []) if o.strip().lower() != NOT_A_QUESTION]
    opts = [o for o in opts if o][:8] + [NOT_A_QUESTION]
    text = c.get("text") or ""
    question = verdict.get("question") or text[-300:]
    cut = of_ask_text.estimates(question) + [e for o in verdict.get("options") or [] for e in of_ask_text.estimates(o)]
    eid = of_events.emit("agent_question", {
        "detected_by": "declared", "source": "pane", "from_position": c.get("position"), "to": to,
        "pane_candidate": c.get("ask_id"), "judge": VERSION, "tags": [], "tags_declared": False,
        **({"delegation": did} if did else {}),
        "context": {"system": p.get("system"), "mandate": {"summary": p.get("summary"), "brief": p.get("mandate")},
                    "last_message": text[-3000:]},
        "questions": [{"question": of_ask_text.drop(question), "options": opts}],
        **({"estimates_cut": cut} if cut else {}),
        "classifications": {"q-v0": True, "q-v1": True}},
        position=c.get("position"), org=c.get("org"), session=c.get("session"))
    return eid, to


def note_text(question, ask_id, to):
    where = "the founder's list" if to in ("founder", "owner", None) else f"the list of {to}, your unit owner"
    a = ask_id[-8:]
    return (f"OF: your last message asks for a decision: «{question}». OF put it on {where} as ask {a}, so you need "
            f"not file it. If it does not need an answer, withdraw it: of asks withdraw {a} \"<why>\". If you were "
            f"answered here, run: of status working --answered {a}. If it needs more context than that line, file it "
            "with of ask (AGENTS.md, \"Asking the founder\"); your ask replaces this one.")


def note(conn, c, ask_id, to, question, deliver=None, log=print):
    """Tell the agent once that OF filed its pane question (idle pane, empty input box), at most once per session per
    CORRECT_EVERY_MIN. The card stands whether or not the note gets through."""
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - CORRECT_EVERY_MIN * 60))
    if not c.get("pane") or noted(conn, ask_id) or corrected(conn, c["session"], since):
        return False
    if deliver is None:
        import of_relay_client

        def deliver(pane, text):
            return of_relay_client.herdr_deliver(pane, text, c["session"], "of-asks:of", wait_s=5)
    ok, why = deliver(c["pane"], note_text(question, ask_id, to))
    if ok is True:
        of_events.emit("nudge_sent", {"kind": "ask_correction", "ask_id": ask_id, "position": c.get("position"),
                                      "from_position": "of", "to_position": c.get("position"), "pane": c["pane"],
                                      "about": question, "to": to},
                       position=c.get("position"), org=c.get("org"), session=c["session"])
        log(f"told {c.get('position')} its question is ask {ask_id[-8:]}: {question}")
        return True
    log(f"note to {c.get('position')} not delivered: {why}")
    return False


def judge_one(conn, c, runner=subprocess.run, deliver=None, log=print, now=None):
    """Judge one closing message and record the verdict. When the agent waits on its owner and filed no ask, OF files
    the question for it at once (file_pane_ask) and tells it so: at once after a turn a peer or a tool started, after
    CORRECT_HUMAN_MIN (the pulse) after one the founder started, who may be answering right there. A marker for a turn
    no detector picked (gate all, `turn: true`) records only a question, a skip or an error, not a "no"."""
    now = now or time.time()
    import of_asks
    age = of_asks.seconds_since(c["ts"], now)
    if age is not None and age > MAX_AGE_H * 3600:
        return None
    if any(v in ("owner_question", "not_a_question", "skipped") for v in judged(conn, c["ask_id"])):
        return None
    if c.get("position") and c["position"] in owner_chat():
        v = {"verdict": "skipped", "rule": "owner-chat",
             "reason": "the owner converses with this position in its pane and reads its questions there"}
    elif c.get("position") and c.get("turn_start") and filed(conn, c["position"], since=c["turn_start"]):
        v = {"verdict": "skipped", "reason": "the agent filed an of ask in this turn"}
    elif (named := only_filed_ids(conn, c.get("text") or "", now)):
        v = {"verdict": "not_a_question", "rule": "filed-ids",
             "reason": f"it names only asks already filed ({', '.join(named)}) and asks nothing new"}
    else:
        named = named_asks(open_founder_asks(conn), c.get("text") or "", c.get("position")) \
            if ASK_ID.search(c.get("text") or "") else []
        v = judge_text(user_prompt(c.get("text") or "", c.get("position"), c.get("origin"), c.get("peer"),
                                   filed(conn, c["position"]) if c.get("position") else (), named),
                       config_dir=c.get("config_dir"), runner=runner)
    filed_as = to = None
    if v["verdict"] == "owner_question" and c.get("position"):
        filed_as, to = file_pane_ask(c, v)
        v = dict(v, filed_as=filed_as, to=to)
    if not (c.get("turn") and v["verdict"] == "not_a_question"):
        of_events.emit("agent_question_judged", dict(v, ask_id=c["ask_id"], judge=VERSION, origin=c.get("origin"),
                                                     **({"turn": True} if c.get("turn") else {})),
                       position=c.get("position"), org=c.get("org"), session=c.get("session"))
    if filed_as and c.get("origin") not in ("human", "paste"):
        note(conn, c, filed_as, to, v.get("question") or "", deliver, log)
    return v


# ------------------------------------------------------------------ runners

def _slot():
    """One of SLOTS judge slots (an flock), or None after SLOT_WAIT_S."""
    end = time.time() + SLOT_WAIT_S
    while True:
        for i in range(SLOTS):
            fh = open(os.open(os.path.join(state_dir(), f"slot-{i}.lock"), os.O_WRONLY | os.O_CREAT, 0o600), "w")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fh
            except BlockingIOError:
                fh.close()
        if time.time() >= end:
            return None
        time.sleep(1)


def take_pending():
    """Claim the oldest marker (renamed, so two judges never take the same one)."""
    d = state_dir("pending")
    for name in sorted(os.listdir(d), key=lambda n: os.path.getmtime(os.path.join(d, n))
                       if os.path.exists(os.path.join(d, n)) else 0):
        if not name.endswith(".json"):
            continue
        src = os.path.join(d, name)
        dst = src[:-5] + f".{os.getpid()}.work"
        try:
            os.rename(src, dst)
        except OSError:
            continue
        try:
            with open(dst) as fh:
                return dst, json.load(fh)
        except ValueError:
            os.unlink(dst)
    return None, None


def pending_count():
    try:
        return sum(1 for n in os.listdir(state_dir("pending")) if n.endswith(".json"))
    except OSError:
        return 0


def run(ids=(), pending=False, pane=None, dry_run=False, log=print):
    """`of asks judge [ids…] [--pending] [--pane P] [--dry-run]`."""
    if not config().get("enabled", True):
        log("the judge is off (judge.yaml enabled: false)")
        return 0
    import of_store
    conn = of_store.connect()
    if dry_run:
        for i in ids:
            c = from_event(conn, _resolve(conn, i))
            if not c:
                log(f"{i}: no such candidate")
                continue
            v = judge_text(user_prompt(c["text"], c["position"], c["origin"], c["peer"], filed(conn, c["position"]),
                                       named_asks(open_founder_asks(conn), c["text"], c["position"])),
                           config_dir=c.get("config_dir"))
            log(json.dumps(dict(v, ask_id=c["ask_id"], position=c["position"]), ensure_ascii=False))
        return 0
    slot = _slot()
    if slot is None:
        log("no free judge slot: the markers stay for a running judge or the pulse")
        return 0
    try:
        for i in ids:
            c = from_event(conn, _resolve(conn, i))
            if c:
                c["pane"] = pane
                v = judge_one(conn, c, log=log)
                log(f"{c['ask_id'][-8:]} {c.get('position')}: {(v or {}).get('verdict', 'not judged')}")
        while pending:
            path, c = take_pending()
            if not path:
                break
            try:
                v = judge_one(conn, c, log=log)
                log(f"{c['ask_id'][-8:]} {c.get('position')}: {(v or {}).get('verdict', 'not judged')}")
                if (v or {}).get("verdict") == "error" and c.get("turn") and c.get("attempts", 0) < 2:
                    queue(dict(c, attempts=c.get("attempts", 0) + 1))  # a turn has no candidate for the pulse to retry
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
    finally:
        slot.close()
    return 0


def _resolve(conn, ref):
    row = conn.execute("SELECT event_id FROM event WHERE name = 'of.agent_question' AND (event_id = ? OR event_id "
                       "LIKE ?) ORDER BY ts DESC LIMIT 1", (ref, f"%{ref}")).fetchone()
    return row[0] if row else ref


# ------------------------------------------------------------------ the pulse's part

def pulse_pass(conn, asks, positions, agents, bind, now=None, deliver=None, log=print, start=spawn):
    """Every pulse: start a judge for candidates nobody judged (the hook's marker was lost, the slots were busy, a
    model error), whatever happened to the session since; and tell agents about the pane cards OF filed for them that
    they were not told about yet (turns the founder started, after CORRECT_HUMAN_MIN; panes that were busy).
    Returns (judges started, notes delivered)."""
    import of_asks
    now = now or time.time()
    started = 0
    todo = []
    for a in asks:
        if not (a.get("kind") or "").startswith("question (heuristic)") or not a.get("position") or \
                a["state"] in ("answered", "withdrawn", "denied"):
            continue
        age = of_asks.seconds_since(a["ts"], now)
        if age is None or age < PULSE_MIN_AGE_S or age > MAX_AGE_H * 3600:
            continue
        vs = judged(conn, a["event_id"])
        if not vs or (all(v == "error" for v in vs) and len(vs) < 3):
            todo.append(a["event_id"])
    if todo or pending_count():
        started = int(start(["--pending", *todo[:20]]))
    live = {}
    for p, ag in bind(positions, agents)[0] if bind and agents is not None else []:
        live[p["name"]] = ag
    sent = 0
    for a in asks:
        if a["state"] != "open" or a.get("source") != "pane" or not a.get("position"):
            continue
        age = of_asks.seconds_since(a["ts"], now)
        if age is None or age < CORRECT_HUMAN_MIN * 60 or age > MAX_AGE_H * 3600:
            continue
        ag = live.get(a["position"])
        if not ag or ag.get("agent_status") not in ("idle", "done") or noted(conn, a["event_id"]):
            continue
        c = {"ask_id": a["event_id"], "session": a["session_id"], "position": a["position"], "ts": a["ts"],
             "pane": ag.get("pane_id"), "org": None}
        sent += bool(note(conn, c, a["event_id"], a.get("to") or "founder", a.get("text") or "", deliver, log))
    return started, sent


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="of asks judge")
    ap.add_argument("ids", nargs="*")
    ap.add_argument("--pending", action="store_true")
    ap.add_argument("--pane")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    return run(a.ids, a.pending, a.pane, a.dry_run)
