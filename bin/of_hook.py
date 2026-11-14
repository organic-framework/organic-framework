"""of hook: the Claude Code collector for the Organic Framework event log (docs/events.md, v1).

Usage from a Claude Code hook:  of hook <HookEvent>   (hook JSON on stdin)

Rules: always exit 0, stay fast, never block the agent (the question judge runs detached, bin/of_judge.py). Light imports only; the
registry (YAML) is read through a small index cache that is rebuilt when a
registry file changes.
"""

import time as _time_mod
_T0 = _time_mod.time()  # as early as the module loads: the hook's own cost
import hashlib
import json
import os
import re
import socket
import sys
import time

import of_classify
import of_events

STATE = of_events.STATE_DIR
TEXT_MAX = 8192
SHORT = 1024
QUESTION_CLASSIFIER = of_classify.ASK_VERSION  # the version recorded on a candidate; any version firing queues it for the judge
OWNER_WORDS_CLASSIFIER = "ow-v1"
HERDR_SOURCE = "of:hook"
RAW_REF = None  # the hook_record of the call being handled

# ------------------------------------------------------------------ masking

_MASKS = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S)),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}")),
    ("openai_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    # 32-byte hex is mostly transaction and block hashes, which are data. Mask it only next to key words.
    ("hex_key", re.compile(r"(?i)((?:private|secret|seed|mnemonic|signer|wallet)[\w -]{0,20}?key\w*[\s\"'`:=]{1,6}|"
                           r"\b(?:pk|priv|privkey|private_key|secret_key|PRIVATE_KEY|SECRET_KEY)\w*[\s\"'`:=]{1,6})(?:0x)?[0-9a-fA-F]{64}\b")),
    ("url_password", re.compile(r"(\b[a-z][a-z0-9+.\-]*://[^/\s:@]+:)[^@\s/]+(@)")),
    ("assignment", re.compile(r"(?i)\b((?:password|passwd|secret|token|api[_-]?key|access[_-]?key)\s*[:=]\s*)(?![\[<])[\"']?[^\s\"',;]{4,}")),
]


def mask(text):
    if not isinstance(text, str) or not text:
        return text
    for kind, rx in _MASKS:
        if kind == "url_password":
            text = rx.sub(lambda m: f"{m.group(1)}[masked:{kind}]{m.group(2)}", text)
        elif kind in ("assignment",) or (kind == "hex_key" and rx.groups):
            text = rx.sub(lambda m: f"{m.group(1)}[masked:{kind}]", text)
        else:
            text = rx.sub(f"[masked:{kind}]", text)
    return text


def clip(text, limit):
    """Mask, then cut to `limit` bytes of UTF-8. Returns (text, truncated)."""
    text = mask(text or "")
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode("utf-8", "ignore"), True


def clip_tail(text, limit):
    """Mask, then keep the last `limit` bytes of UTF-8: a closing question is at the end (clip() kept the start of the
    last 600 characters, and in Cyrillic lost the question itself)."""
    raw = mask(text or "").encode("utf-8")
    return raw[-limit:].decode("utf-8", "ignore") if len(raw) > limit else raw.decode("utf-8")


# --------------------------------------------------------------- classifiers

_ASK_PHRASES = re.compile(
    r"(?i)(?:(?:тво(?:его|ё|й)|ваш(?:его|е|и))\s*[«\"“]?\s*да\b"
    r"|жд(?:у|ём|ем|ут)\s+(?:тво|ваш)\w*\s+(?:ответ|решени|подтвержд|«?да)"
    r"|нужн[аоы]?\s+(?:тво|ваш)\w*\s+(?:решени|ответ|подтвержд|одобрени)"
    r"|без\s+тво\w*\s+[«\"“]?да"
    r"|awaiting your|waiting for your (?:answer|decision|approval|go-ahead)"
    r"|need your (?:decision|approval|go-ahead|answer))")


def asks_question(text):
    """Heuristic q-v1: does an agent's last message ask the owner something?"""
    if not text or not text.strip():
        return False
    head = text.lstrip()[:200]
    if head.startswith("API Error") or "API Error:" in head:
        return False
    tail = text[-250:]
    return "?" in tail or bool(_ASK_PHRASES.search(text[-600:]))


def carries_owner_words(text):
    return of_classify.carries_owner_words(text)


def prompt_origin(prompt, hook):
    p = (prompt or "").lstrip()
    if p.startswith("<cross-session-message") or of_classify.relay_sender(p):
        return "peer"
    if hook.get("source") == "poll_event" or p.startswith(("<task-notification", "<scheduled", "<system-reminder")):
        return "scheduled"
    return "human"


# Claude Code's own word on who started a turn (2026-11-06: a cron prompt recorded as origin human closed the
# founder's ask c54997b8). The prompt's transcript record carries `turnOrigin`, but it is written just after
# UserPromptSubmit; what is on disk by then is a `system/scheduled_task_fire` record holding the prompt. So the hook
# reads the transcript's tail back to the previous turn. Only words a person typed close an ask (of_architect).
TURN_ORIGIN = {"scheduled": "scheduled", "task_notification": "scheduled", "system": "system", "sdk": "system",
               "auto_continuation": "system"}
TAIL_BYTES = 256 * 1024


def transcript_origin(hook, prompt):
    """'scheduled' (a cron or loop fire), another automation origin from the prompt's own record, 'unknown' when
    the transcript cannot be read, else None (nothing in the transcript says anyone but a person sent it)."""
    path = hook.get("transcript_path")
    if not path:
        return None  # not Claude Code's prompt hook (it always names the transcript): nothing to read
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - TAIL_BYTES))
            data = fh.read()
    except OSError:
        return "unknown"
    pid = hook.get("prompt_id")
    for ln in reversed(data.split(b"\n")):
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except ValueError:
            continue  # the cut first line of the tail
        t = o.get("type")
        if t == "user" and pid and o.get("promptId") == pid and o.get("turnOrigin"):
            return TURN_ORIGIN.get(o["turnOrigin"])
        fire = str(o.get("prompt") or "").strip().rstrip("…").strip()[:150] if o.get("subtype") == "scheduled_task_fire" else ""
        if t == "system" and fire and (prompt or "").strip().startswith(fire):  # Claude Code cuts it at 200 chars
            return "scheduled"
        if t in ("user", "assistant"):
            return None  # the previous turn: nothing between it and this prompt marks automation
    return None


def record_origin(path, offset, text, scan=512 * 1024):
    """The `turnOrigin` of the prompt's own transcript record: the first user record at or after the offset the
    hook saw (the file's size then) whose text starts like the prompt. None when the file or the record is gone."""
    try:
        with open(os.path.expanduser(path), "rb") as fh:
            fh.seek(int(offset))
            data = fh.read(scan)
    except (OSError, TypeError, ValueError):
        return None
    head = (text or "").strip()[:80]
    for ln in data.split(b"\n"):
        try:
            o = json.loads(ln)
        except ValueError:
            continue
        if o.get("type") != "user" or not o.get("turnOrigin"):
            continue
        c = (o.get("message") or {}).get("content")
        body = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c or [] if isinstance(x, dict))
        if body.strip()[:80] == head:
            return o["turnOrigin"]
    return None


# A spawn's position brief, typed as the new session's first prompt ('of spawn' declares it; before it existed,
# most spawns did not). Known frame only; the correction pass marks it injected.
BRIEF_FRAME = re.compile(r"(?:<pasted_content[^>]*>\s*)?You hold the position [a-z][a-z0-9_-]{0,31}\b")


def _epoch(iso):
    import calendar
    try:
        return calendar.timegm(time.strptime(str(iso)[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return None


def correct_prompt_origins(conn, since, apply=True):
    """Prompts the hook recorded as the owner's that Claude Code's transcript shows a scheduler (cron, loop) or
    another automation sent, or that a tool declared (of.injected_prompt for this session and text, from 10 minutes
    before to 1 minute after; a declaration written just after the typing misses the live check): one `of.prompt_origin` correction each, which of_asks.apply_prompt_origins applies (only
    words a person typed close an ask; of_architect, 2026-11-06). The listener runs it every 15 minutes over the last
    day, for prompts the live check missed (the fire record written after the hook read the transcript). Idempotent:
    deterministic ids. Returns (prompts checked, [(event_id, session, position, org, origin, turnOrigin, text)],
    corrections written)."""
    import uuid
    import of_store
    ns = uuid.UUID("5b1f7c2e-9d3a-4c61-8e0f-2a7b6c5d4e31")
    rows = conn.execute("SELECT event_id, session_id, position, org, transcript, payload FROM event "
                        "WHERE name = 'of.human_prompt' AND ts >= ? ORDER BY ts", (since,)).fetchall()
    fixed_already = {r[0] for r in conn.execute(
        "SELECT json_extract(payload, '$.event_id') FROM event WHERE name = 'of.prompt_origin' AND ts >= ?", (since,))}
    marks = {}  # (session, text sha256) -> [declared at]: a tool that declared its typing, if only just after it
    for ts_, pl_ in conn.execute("SELECT ts, payload FROM event WHERE name = 'of.injected_prompt' AND ts >= ?",
                                 (time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime((_epoch(since) or 0) - 3600)),)):
        j = json.loads(pl_ or "{}")
        marks.setdefault((j.get("target_session"), j.get("text_sha256")), []).append(_epoch(ts_) or 0)
    found, seen = [], 0
    for eid, sid, pos, org, tr, pl in rows:
        if eid in fixed_already:
            continue
        p = json.loads(pl or "{}")
        if p.get("origin") not in (None, "human", "paste"):
            continue
        seen += 1
        text = p.get("text") or ""
        m = re.search(r"<pasted_content[^>]*>(.*?)(?:</pasted_content[^>]*>|$)", text, re.S)
        digests = {hashlib.sha256(text.strip().encode()).hexdigest()} | (
            {hashlib.sha256(m.group(1).strip().encode()).hexdigest()} if m else set())
        at = _epoch(conn.execute("SELECT ts FROM event WHERE event_id = ?", (eid,)).fetchone()[0]) or 0
        if not p.get("truncated") and any(-INJECT_SESSION_S <= x - at <= 60 for d in digests for x in marks.get((sid, d), [])):
            found.append((eid, sid, pos, org, "injected", "of.injected_prompt", text[:70].replace("\n", " ")))
            continue  # declared by its tool for this session, just before or just after the typing
        if BRIEF_FRAME.match(text.lstrip()):
            found.append((eid, sid, pos, org, "injected", "brief frame", text[:70].replace("\n", " ")))
            continue  # a position brief typed by whoever spawned the session (of_architect, 2026-11-06)
        t = json.loads(tr or "{}") if (tr or "").startswith("{") else {}
        if not t.get("path") or t.get("offset") is None:
            continue
        to = record_origin(t["path"], t["offset"], p.get("text"))
        fixed = TURN_ORIGIN.get(to or "")
        if fixed:
            found.append((eid, sid, pos, org, fixed, to, (p.get("text") or "")[:70].replace("\n", " ")))
    n = 0
    for eid, sid, pos, org, fixed, to, _ in found if apply else []:
        rec = of_events.record("prompt_origin", {"event_id": eid, "origin": fixed,
                                                 "evidence": to if to in ("of.injected_prompt", "brief frame") else f"transcript turnOrigin {to}"},
                               position=pos, org=org, session=sid, source="derive")
        rec["of.event.id"] = str(uuid.uuid5(ns, eid))
        if conn.execute("SELECT 1 FROM event WHERE event_id = ?", (rec["of.event.id"],)).fetchone():
            continue
        of_store.add_event(conn, rec)  # its own short transaction
        n += 1
    return seen, found, n


def _peer_from(prompt):
    relayed = of_classify.relay_sender(prompt)
    if relayed:
        return relayed
    m = re.search(r'from-name="([^"]+)"', prompt or "") or re.search(r'from="([^"]+)"', prompt or "")
    return m.group(1) if m else None


def _peer_body(prompt):
    if of_classify.relay_sender(prompt):
        return of_classify.relay_body(prompt)
    m = re.search(r"<cross-session-message[^>]*>(.*?)(?:</cross-session-message>|$)", prompt or "", re.S)
    return (m.group(1) if m else prompt or "").strip()


# ------------------------------------------------------------ position index

_SESSION = None  # the session this hook call reports for (tmux_state); None for a subagent's call
_PRE_TOOL = False  # this process answers a PreToolUse (the guard): it reads the index cache, never the YAML


def resolve_org(position=None, cwd=None):
    """An event's organization: its position's (registry), else the one whose `paths` hold the cwd."""
    try:
        index = _guard_index() if _PRE_TOOL else _registry_index()
    except Exception:
        return None
    if position:
        info = (index.get("labels") or {}).get(position)
        if info and info[1]:
            return info[1]
    if cwd:
        full = os.path.realpath(os.path.expanduser(cwd))
        for prefix, org in index.get("paths") or []:
            if full == prefix or full.startswith(prefix + os.sep):
                return org
    return None


def _registry_index():
    """{session: [name, org]} and {name or alias: name}, cached by registry mtimes."""
    path = os.path.join(STATE, "index.json")
    orgs = os.environ.get("OF_ORGS") or of_events.config_path("orgs.yaml")
    try:  # the router's version is part of the index: its compiled patterns change with it, and a verdict labelled
        import of_router  # with the new version must not come from the old patterns (of_studio, PR 49, 2026-11-12)
        router = of_router.VERSION
    except Exception:  # noqa: BLE001  no router: the index is built without its systems, as before
        router = None

    def fresh(c):
        return isinstance(c, dict) and c.get("router") == router == router and "paths" in c and "systems" in c and "memory" in c and \
            c["mtimes"] and all(os.path.exists(f) and os.path.getmtime(f) == m for f, m in c["mtimes"].items())

    def read():
        try:
            with open(path) as fh:
                return json.load(fh)
        except Exception:
            return None
    cache = read()
    if fresh(cache):
        return cache
    # One rebuild at a time: after a registry change every hook of ~40 sessions rebuilt the index at once, 1 to 2.5 s
    # each at a load average of 30 (of_server, 2026-11-10). The one that takes the lock rebuilds; the others use the
    # index they have, a moment old, and the next hook sees the new one.
    import fcntl
    lk = None
    try:
        lk = open(os.open(os.path.join(STATE, "index.lock"), os.O_WRONLY | os.O_CREAT, 0o600))
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        if isinstance(cache, dict) and cache.get("router") == router and all(k in cache for k in ("sessions", "paths", "systems", "memory")):
            return cache
        fcntl.flock(lk, fcntl.LOCK_EX)  # no index, or one compiled by another router version: wait for the new one
        cache = read()
        if fresh(cache):
            return cache
    except OSError:
        pass
    import importlib.machinery
    import importlib.util
    here = os.path.dirname(os.path.realpath(__file__))
    loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(here, "of"))
    spec = importlib.util.spec_from_loader("of_cli", loader)
    of = importlib.util.module_from_spec(spec)
    try:
        loader.exec_module(of)
        positions, _ = of.load_positions(orgs)
    except (SystemExit, Exception) as e:  # e.g. an interpreter without PyYAML: record without positions
        try:
            with open(os.path.join(STATE, "hook-errors.log"), "a") as fh:
                fh.write(f"{of_events.now_iso()} registry WARN {type(e).__name__}: {e}; "
                         "index unavailable, events recorded without positions\n")
        except Exception:
            pass
        return {"sessions": {}, "names": {}, "labels": {}, "paths": [], "systems": [], "enrolled": [],
                "pos_system": {}}
    files = [orgs] + [r for _, r, _ in of.org_registries(orgs)]
    cache = {"mtimes": {f: os.path.getmtime(f) for f in files if os.path.exists(f)}, "router": router,
             "sessions": {}, "names": {}, "labels": {}, "paths": []}
    try:  # working-directory routing, as the shipper does: [realpath prefix, organization]
        import yaml
        with open(orgs) as fh:
            for o in (yaml.safe_load(fh) or {}).get("orgs") or []:
                for pth in o.get("paths") or []:
                    cache["paths"].append([os.path.realpath(os.path.expanduser(str(pth))), o.get("name")])
        cache["paths"].sort(key=lambda x: -len(x[0]))
    except Exception:
        pass
    for p in positions:
        s = ((p.get("holder") or {}).get("session") or "").strip()
        if s:
            cache["sessions"][s] = [p["name"], p.get("_orgid"), p.get("serves")]
        for k in [p["name"]] + [str(x) for x in p.get("aliases") or []]:
            cache["names"][k.lower()] = p["name"]
        cache["labels"][p["name"]] = [s, p.get("_orgid"), p.get("role"), p.get("system"), p.get("summary")]
        if p.get("enrolled"):
            cache.setdefault("enrolled", []).append(p["name"])
            cache.setdefault("pos_system", {})[p["name"]] = p.get("system")
    cache["memory"] = {}
    try:  # RFC 0006 §2.3: the memory indexes each position keeps under the cut, with its structure keeper
        import of_memory
        import of_pulse
        keepers = {}
        for p in positions:
            d, o = of_memory.directory(p), of_memory.owner(p, positions)
            k = keepers.setdefault(p.get("_org"), of_pulse.structure_keeper(positions, p.get("_org")))
            if [d, k] not in cache["memory"].setdefault(o, []):
                cache["memory"][o].append([d, k])
    except Exception:  # noqa: BLE001  no warning rather than no index
        pass
    try:  # the mandate router's systems index (compiled per organization root)
        import of_router
        org_paths = {}
        with open(orgs) as fh:
            for o in (__import__("yaml").safe_load(fh) or {}).get("orgs") or []:
                org_paths[o.get("name")] = o.get("paths") or []
        cache["systems"] = of_router.build_index(positions, org_paths)
    except Exception:
        cache["systems"] = []
    tmp = f"{path}.{os.getpid()}.{time.time_ns()}.tmp"  # unique: concurrent rebuilds must not collide
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(cache, fh)
    os.replace(tmp, path)
    return cache


# ------------------------------------------------------------ session cache

def _session_path(sid):
    d = os.path.join(STATE, "sessions")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return os.path.join(d, re.sub(r"[^A-Za-z0-9_.-]", "_", sid or "none") + ".json")


def load_session(sid):
    try:
        with open(_session_path(sid)) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_session(sid, data):
    p = _session_path(sid)
    with open(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(data, fh)


# -------------------------------------------------------------------- herdr

def herdr_call(method, params):
    sock_path = os.environ.get("HERDR_SOCKET_PATH")
    if os.environ.get("HERDR_ENV") != "1" or not sock_path:
        return None
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(0.5)
        c.connect(sock_path)
        c.sendall((json.dumps({"id": f"{HERDR_SOURCE}:{time.time_ns()}", "method": method, "params": params}) + "\n").encode())
        try:
            reply = c.recv(65536)
        except Exception:
            reply = b""
        c.close()
        return json.loads(reply.decode() or "{}")
    except Exception:
        return None


def herdr_state(state, message=None):
    pane = os.environ.get("HERDR_PANE_ID")
    if not pane:
        tmux_state(state, message)
        return
    params = {"pane_id": pane, "source": HERDR_SOURCE, "agent": "claude", "state": state, "seq": time.time_ns()}
    if message:
        params["message"] = message[:200]
    resume = resume_argv()
    if resume:
        params["resume_argv"] = resume
    herdr_call("pane.report_agent", params)


def resume_argv(env=None, python=None, of_bin=None):
    """What herdr runs in this pane after a herdr restart, instead of its own bare `claude --resume <id>`: `of claude
    --resume <id>` resumes the session as it ran, on its account with its model, effort and permission mode
    (bin/of_accounts.py), so no shell function is needed (docs/accounts.md A4, hand-over a9c118e8; docs/herdr.md).
    herdr keeps a resume command reported with a state report and prefers it to its own (herdr 0.9.5,
    src/persist/restore.rs). It types the command into a new login shell, whose PATH may put a Python without PyYAML
    first (macOS path_helper: /usr/bin before Homebrew; seen in the lab). So the command names this hook's own Python
    and this checkout's bin/of, behind `env`, because herdr takes only a plain command name as the first word. None for
    a subagent's call or a side chat, or when a path would break herdr's rules."""
    env = os.environ if env is None else env
    if not _SESSION or env.get("OF_SIDE_CHAT") or not re.fullmatch(r"[0-9A-Za-z-]{8,64}", _SESSION):
        return None
    python = python or sys.executable
    of_bin = of_bin or os.path.join(os.path.dirname(os.path.abspath(__file__)), "of")
    if not python or not os.path.isfile(of_bin) or any("'" in x or re.search(r"[\x00-\x1f\x7f]", x)
                                                         for x in (python, of_bin)):
        return None
    return ["env", python, of_bin, "claude", "--resume", _SESSION]


def tmux_state(state, message=None):
    """An agent without herdr runs in tmux (docs/remote.md §3 row 5): the same states go to its pane's report, which
    the relay and `of who` read (bin/of_tmux.py; owner of_herdr). Also 'started', 'compacted' and 'ended'. An agent
    in a screen window (macOS ships screen, not tmux) reports the same way (bin/of_screen.py; owner of_server)."""
    if os.environ.get("TMUX_PANE") and not os.environ.get("HERDR_PANE_ID"):
        import of_tmux
        of_tmux.report(state, _SESSION, message)
    elif os.environ.get("STY") and not os.environ.get("HERDR_PANE_ID"):
        import of_screen
        of_screen.report(state, _SESSION, message)


# -------------------------------------------------------------------- event

def _home(path):
    home = os.path.expanduser("~")
    return ("~" + path[len(home):]) if isinstance(path, str) and path.startswith(home) else path


def _transcript(hook):
    path = hook.get("transcript_path")
    if not path:
        return None
    try:
        return {"path": _home(path), "offset": os.path.getsize(path)}
    except OSError:
        return {"path": _home(path)}


def write(kind, payload, hook, sess, index):
    sid = hook.get("session_id")
    name_org = ((index.get("sessions") or {}).get(sid) or []) + [None, None, None]
    extra = {
        "of.harness.name": "claude-code",
        "of.harness.version": os.path.basename(os.environ.get("CLAUDE_CODE_EXECPATH", "")) or None,
        "of.harness.config_dir": _home(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")),
        "gen_ai.provider.name": "anthropic",
        "gen_ai.request.model": sess.get("model"),
        "of.cwd": _home(hook.get("cwd")),
        "of.transcript": _transcript(hook),
    }
    if os.environ.get("HERDR_PANE_ID"):
        extra["of.workplace"] = {"runtime": "herdr", "pane": os.environ.get("HERDR_PANE_ID"),
                                 "tab": os.environ.get("HERDR_TAB_ID"), "workspace": os.environ.get("HERDR_WORKSPACE_ID")}
    elif os.environ.get("TMUX_PANE"):
        import of_tmux
        where = of_tmux._where(os.environ)
        if where:
            extra["of.workplace"] = {"runtime": "tmux", "pane": of_tmux.pane_id(*where)}
    elif os.environ.get("STY"):
        import of_screen
        where = of_screen._where(os.environ)
        if where:
            extra["of.workplace"] = {"runtime": "screen", "pane": of_screen.pane_id(*where)}
    if name_org[2] and name_org[2] != name_org[1]:
        extra["of.serves"] = name_org[2]
    if hook.get("agent_id"):  # a subagent's call: the hook's session id is the parent's (docs/events.md)
        extra["of.subagent"] = {"id": hook.get("agent_id"), "type": hook.get("agent_type")}
        if isinstance(payload, dict):  # readers that load payload only (Studio, of_asks.relevant) see it too
            payload = dict(payload, subagent=extra["of.subagent"])
    if os.environ.get("OF_SIDE_CHAT"):  # a fork answering the owner's side question: never a position's holder
        extra["of.side_chat"] = os.environ["OF_SIDE_CHAT"]
    rec = of_events.record(kind, payload, position=name_org[0], org=name_org[1], session=sid, source="hook", extra=extra)
    of_events.append(rec, RAW_REF)
    return rec["of.event.id"]


def _summary(tool_input):
    if not isinstance(tool_input, dict):
        return clip(str(tool_input or ""), 200)[0]
    for k in ("command", "file_path", "url", "path", "pattern", "description"):
        if tool_input.get(k):
            return clip(str(tool_input[k]), 200)[0]
    return clip(json.dumps(tool_input, ensure_ascii=False), 200)[0]


INJECT_SESSION_S = 600  # a mark naming this session: of_capacity resumes a moved session with its note as the first
# prompt, and a large session can take minutes to start (2026-11-06: moves recorded as the owner's words)


def _injected(sid, prompt):
    """Was this prompt typed by a tool that declared it (of event injected_prompt)?"""
    path = os.path.join(STATE, "injections.jsonl")
    try:
        with open(path) as fh:
            lines = fh.read().splitlines()[-200:]
    except FileNotFoundError:
        return None
    digests = {hashlib.sha256((prompt or "").strip().encode()).hexdigest()}
    # Claude Code wraps long typed text as <pasted_content …>…</pasted_content>: match its body too
    m = re.search(r"<pasted_content[^>]*>(.*?)(?:</pasted_content[^>]*>|$)", prompt or "", re.S)
    if m:
        digests.add(hashlib.sha256(m.group(1).strip().encode()).hexdigest())
    now = time.time()
    for line in reversed(lines):
        try:
            j = json.loads(line)
        except Exception:
            continue
        window = INJECT_SESSION_S if sid and j.get("session") == sid else 60
        if now - j.get("ts", 0) <= window and j.get("sha256") in digests and j.get("session") in (sid, None):
            return j
    return None


ASK_DIALOG_DENY = ("Organic Framework: a position never asks its owner in a dialog; it stops your whole session "
                   "until he answers, even when he is typing in your pane. File the question with of ask \"<the "
                   "decision in one line, why now, context, options>\" --options \"a (recommended: why)|b\" (AGENTS.md, "
                   "Asking the founder), declare of status waiting --on founder for that step only, and go on with "
                   "everything that does not depend on the answer.")


def handle(event, hook):
    global OUTPUT, _PRE_TOOL, _SESSION
    sid = hook.get("session_id")
    _SESSION = None if hook.get("agent_id") else sid  # a subagent's call never reports its parent's pane state
    sess = load_session(sid)
    subagent = bool(hook.get("agent_id"))
    tool = hook.get("tool_name")
    # the guard answers within Claude Code's 5-second hook timeout: no registry parsing on this path
    _PRE_TOOL = event == "PreToolUse" and tool not in ("AskUserQuestion", "SendMessage")

    # Fast path: tool events that matter for nothing but unblocking (and, for enrolled positions,
    # the mandate router in observe mode).
    if event == "PreToolUse" and tool not in ("AskUserQuestion", "SendMessage"):
        _guard(hook, sess)  # registered for risky tools only with `of hooks install --guard`
    if event in ("PreToolUse", "PostToolUse", "PostToolUseFailure") and tool not in ("AskUserQuestion", "SendMessage"):
        if sess.get("blocked") and not subagent:
            if event != "PreToolUse" and sess.get("open_ask"):  # the asked-for tool ran: the permission is settled
                write("permission_resolved", {"reply_to": sess["open_ask"], "tool_name": tool,
                                              "outcome": "failed" if event == "PostToolUseFailure" else "ran"},
                      hook, sess, _registry_index())
                sess["open_ask"] = None
            sess["blocked"] = False
            save_session(sid, sess)
            herdr_state("working")
        if event == "PostToolUse" and not subagent:
            _mandate_check(hook, sess)
        return

    index = _registry_index()
    changed = False

    if event == "SessionStart":
        sess.update(model=hook.get("model") or sess.get("model"), started=time.time())
        changed = True
        write("session_start", {"source": hook.get("source"), "title": hook.get("session_title")}, hook, sess, index)
        tmux_state("compacted" if hook.get("source") == "compact" else "started")
        _holder_check(hook, index, sess)
        _mandate_context(hook, index, sess)
        _memory_warning(hook, index)
    elif event == "SessionEnd":
        write("session_end", {"reason": hook.get("reason")}, hook, sess, index)
        tmux_state("ended")
    elif event == "UserPromptSubmit":
        prompt = hook.get("prompt") or ""
        if DENY_SECRETS_FLAG:  # gated on --deny-secrets (the whole secrets policy rides one flag)
            _mention_guard(prompt, hook, sess, index)  # block an @mention of a secret path before it is read
        origin = prompt_origin(prompt, hook)
        mark = _injected(sid, prompt) if origin == "human" else None
        if origin == "human" and not mark:  # a declared tool's text is settled by its mark; else ask the transcript
            origin = transcript_origin(hook, prompt) or "human"
        payload = {"origin": origin}
        if origin == "peer":
            body, cut = clip(_peer_body(prompt), SHORT)
            write("agent_message_in", {"from": _peer_from(prompt), "from_kind": of_classify.from_kind(_peer_from(prompt)),
                                       **({"via": "relay", "authority_class": of_classify.relay_header(prompt)[0],
                                           "authority_detail": of_classify.relay_header(prompt)[1],
                                           "reply_to": of_classify.relay_reply_to(prompt)}
                                          if of_classify.relay_sender(prompt) else {}),
                                       "text": body, "chars": len(prompt),
                                       "classifications": {of_classify.FA: of_classify.fa_message_in(prompt), of_classify.RQ: of_classify.rq_v0(prompt)},
                                       **({"truncated": True} if cut else {})}, hook, sess, index)
        else:
            # a tool that carried the owner's own words (the asks popup) declares origin human
            injector = (mark or {}).get("injector") if (mark or {}).get("origin") != "human" else None
            text, cut = clip(prompt, TEXT_MAX)
            payload = {"origin": "injected" if injector else origin,
                       "channel": (mark or {}).get("channel") or ("herdr" if os.environ.get("HERDR_ENV") == "1"
                                                                  else "terminal"),
                       "input_mode": "pasted" if "<pasted_content" in prompt else "typed",
                       "reply_to": sess.get("open_ask"), "text": text, "chars": len(prompt)}
            if hook.get("prompt_id"):
                payload["prompt_id"] = hook["prompt_id"]
            if injector:
                payload["injector"] = injector
            if cut:
                payload["truncated"] = True
            if os.environ.get("CLAUDE_CODE_ENTRYPOINT") == "sdk-cli" and payload["origin"] == "human":
                payload["origin"] = "system"  # claude -p / the SDK: automation, not the owner
            payload["classifications"] = of_classify.prompt_labels(
                prompt, first=not sess.get("prompts"), origin=None if origin == "human" else origin,
                injected=bool(injector),
                project=os.path.basename(os.path.dirname(hook.get("transcript_path") or "")) or None)
            sess["prompts"] = sess.get("prompts", 0) + 1
            write("human_prompt", payload, hook, sess, index)
            if payload["origin"] in ("human", "paste") and OUTPUT is None:
                note = open_asks_note(sid, prompt)
                if note:
                    OUTPUT = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": note}}
        sess.update(open_ask=None, blocked=False, turn_start=of_events.now_iso(),
                    turn_origin="peer" if origin == "peer" else payload["origin"],
                    turn_peer=_peer_from(prompt) if origin == "peer" else None)
        changed = True
        if not subagent:
            herdr_state("working")
    elif event == "PreToolUse" and tool == "AskUserQuestion" and ((index.get("sessions") or {}).get(sid) or [None])[0]:
        # A position asks its owner only with `of ask` (AGENTS.md, "Nobody waits unseen"). The dialog stops the whole
        # session until he answers, in a pane he may not look at (founder, 2026-11-10: of_platform asked him twice
        # this way while he typed in its pane; the rule was text only, and nothing stopped the call).
        OUTPUT = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                         "permissionDecisionReason": ASK_DIALOG_DENY}}
        write("mandate_check", {"verdict": "ask_dialog", "decision": "deny", "mode": "enforce",
                                "tool_name": tool}, hook, sess, index)
    elif event == "PreToolUse" and tool == "AskUserQuestion":
        qs = []
        for q in (hook.get("tool_input") or {}).get("questions") or []:
            qs.append({"header": q.get("header"), "question": clip(q.get("question"), SHORT)[0],
                       "options": [o.get("label") for o in q.get("options") or []]})
        eid = write("agent_question", {"detected_by": "tool", "tool_use_id": hook.get("tool_use_id"), "questions": qs},
                    hook, sess, index)
        sess.update(open_ask=eid, blocked=True)
        changed = True
        if not subagent:
            herdr_state("blocked", "asks the owner")
    elif event in ("PostToolUse", "PostToolUseFailure") and tool == "AskUserQuestion":
        resp = hook.get("tool_response")
        answers = resp.get("answers") if isinstance(resp, dict) else resp
        write("agent_question_answered", {"tool_use_id": hook.get("tool_use_id"), "reply_to": sess.get("open_ask"),
                                          "answers": json.loads(clip(json.dumps(answers, ensure_ascii=False), SHORT * 4)[0])
                                          if _is_json(clip(json.dumps(answers, ensure_ascii=False), SHORT * 4)[0])
                                          else clip(str(answers), SHORT)[0]}, hook, sess, index)
        sess.update(open_ask=None, blocked=False)
        changed = True
        if not subagent:
            herdr_state("working")
    elif event in ("PostToolUse", "PostToolUseFailure") and tool == "SendMessage":
        ti = hook.get("tool_input") or {}
        resp = hook.get("tool_response")
        rtext = json.dumps(resp, ensure_ascii=False) if not isinstance(resp, str) else resp
        to = str(ti.get("to") or "")
        bare = re.sub(r"\s*\[[0-9a-f]+\]$", "", to).strip().lower()
        if event == "PostToolUseFailure" or '"success":false' in rtext.replace(" ", ""):
            delivery = "unresolved" if re.search(r"(?i)no .*(agent|session)|not found|unknown", rtext) else "failed"
        elif re.search(r"(?i)holds? .*for .*approval|held", rtext):
            delivery = "held"
        else:
            delivery = "delivered"
        msg = str(ti.get("message") or "")
        text, cut = clip(msg, SHORT)
        payload = {"to": to, "to_position": (index.get("names") or {}).get(bare), "delivery": delivery,
                   "carries_owner_words": carries_owner_words(msg), "classifier_version": OWNER_WORDS_CLASSIFIER,
                   "heuristic": True, "text": text, "chars": len(msg)}
        if cut:
            payload["truncated"] = True
        write("agent_message_out", payload, hook, sess, index)
        if sess.get("blocked") and not subagent:
            sess["blocked"] = False
            changed = True
            herdr_state("working")
    elif event == "PermissionRequest":
        eid = write("permission_request", {"tool_name": tool, "summary": _summary(hook.get("tool_input"))}, hook, sess, index)
        sess.update(open_ask=eid, blocked=True)
        changed = True
        if not subagent:
            herdr_state("blocked", f"permission: {tool}")
    elif event == "PermissionDenied":
        write("permission_denied", {"tool_name": tool, "summary": _summary(hook.get("tool_input")),
                                    "reply_to": sess.get("open_ask")}, hook, sess, index)
        sess.update(open_ask=None, blocked=False)
        changed = True
    elif event == "Notification":
        ntype = hook.get("notification_type")
        if ntype != "permission_prompt":
            write("attention", {"notification_type": ntype, "message": clip(hook.get("message"), SHORT)[0]}, hook, sess, index)
    elif event == "Stop":
        last = hook.get("last_assistant_message") or ""
        text, cut = clip(last, SHORT)
        teid = write("turn_end", {"last_message": text, "last_message_chars": len(last),
                                  **({"truncated": True} if cut else {})}, hook, sess, index)
        _continuation(hook, sess, index)
        qs = of_classify.questions(last)
        if not subagent and any(qs.values()):
            eid = write("agent_question", {"detected_by": "heuristic", "heuristic": True,
                                           "classifier_version": QUESTION_CLASSIFIER, "classifications": qs,
                                           "tags": of_classify.tags_of(last[-1200:]),
                                           "questions": [{"question": clip_tail(last[-600:], SHORT),
                                                          "options": of_classify.options_of(last)}]}, hook, sess, index)
            if qs.get(of_classify.REPLY_VERSION):  # reply_to pairing stays on the gate's pinned version
                sess["open_ask"] = eid
            _judge_later(eid, last, hook, sess, index)
        elif not subagent and last.strip() and _judge_gate() == "all":  # every turn of a position (judge.yaml gate)
            _judge_later(teid, last, hook, sess, index, turn=True)
        sess["blocked"] = False
        changed = True
        if not subagent:
            herdr_state("idle")
    elif event == "StopFailure":
        write("turn_error", {"error": clip(str(hook.get("error") or ""), SHORT)[0]}, hook, sess, index)
        if not subagent:
            herdr_state("idle")
    elif event == "PreCompact":
        write("compaction", {"trigger": hook.get("trigger")}, hook, sess, index)
    if changed:
        save_session(sid, sess)


def _judge_gate():
    try:
        import of_judge
        return of_judge.gate()
    except Exception:  # noqa: BLE001
        return "detectors"


def _judge_later(eid, last, hook, sess, index, turn=False):
    """A heuristic question is only a candidate: the judge (bin/of_judge.py, a small model) decides in the background
    whether the agent waits on its owner, and files it for the agent when it filed no `of ask` (founder, ask
    ddd6e3a3; hand-over cc2ed950). turn: a turn no detector picked, queued by its turn_end id (judge.yaml gate: all).
    Only a position's session; never waits."""
    sid = hook.get("session_id")
    pos, org = (((index.get("sessions") or {}).get(sid) or []) + [None, None])[:2]
    if not eid or not pos:
        return
    try:
        import of_judge
        of_judge.queue({"ask_id": eid, "session": sid, "position": pos, "org": org, "ts": of_events.now_iso(),
                        "text": mask(last)[-of_judge.TEXT_MAX:], "origin": sess.get("turn_origin"),
                        "peer": sess.get("turn_peer"), "turn_start": sess.get("turn_start"),
                        "pane": os.environ.get("HERDR_PANE_ID"), "config_dir": os.environ.get("CLAUDE_CONFIG_DIR"),
                        **({"turn": True} if turn else {})})
        of_judge.spawn()
    except Exception as e:  # noqa: BLE001  the pulse picks the candidate up from the store
        _log_error("judge", e)


def _continuation(hook, sess, index):
    """The continuation manifest: what will wake this session later (crons, wake-ups, loops)
    and what background work is in flight. Written as `of.continuation` when it changes, so a
    holder move can re-create it on the target machine (RFC 0001 §5.8; lesson R3)."""
    crons = hook.get("session_crons")
    tasks = hook.get("background_tasks")
    if crons is None and tasks is None:
        return
    manifest = {"session_crons": json.loads(mask(json.dumps(crons or [], ensure_ascii=False))),
                "background_tasks": json.loads(mask(json.dumps(tasks or [], ensure_ascii=False)))}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    if sess.get("continuation_sha") == digest:
        return
    sess["continuation_sha"] = digest
    sess["continuation"] = manifest
    write("continuation", dict(manifest, crons=len(manifest["session_crons"]),
                               tasks=len(manifest["background_tasks"])), hook, sess, index)


def _is_json(text):
    try:
        json.loads(text)
        return True
    except Exception:
        return False


# Critical denies (RFC 0002 §4.1): never admitted, whatever the grants say. Secrets do not leave their
# files through an agent: no dumping the environment, no reading secret files (one list with the deny
# rules: of_classify.SECRET_GLOBS), no echoing *_KEY variables, no keychain passwords.
import of_guard  # noqa: E402  (the guard, bin/of_guard.py)
from of_guard import _WARNINGS, critical_deny, prompt_secret_mention  # noqa: E402,F401


def _guard_index(sid=None):
    """The registry index for the guard, which must answer within Claude Code's 5-second hook timeout.
    2026-11-10: the pulse's probe timed out after 10 s at a load average of 30, nearly all of it parsing the
    registries' YAML after a registry changed; a guard hook killed by the timeout loses its deny. So the
    guard reads the cache as it is on disk, even when a registry changed since, and never waits for YAML to
    record a decision. It rebuilds only when `sid` is given and is not in the cache (a new holder must get its
    position's rules), and then only if the cache is stale. Other hooks rebuild the index as before."""
    try:
        with open(os.path.join(STATE, "index.json")) as fh:
            cache = json.load(fh)
        if isinstance(cache.get("sessions"), dict) and (sid is None or sid in cache["sessions"]):
            return cache
    except Exception:
        if sid is None:
            return {"sessions": {}, "names": {}, "labels": {}, "paths": [], "systems": [], "enrolled": [],
                    "pos_system": {}}
    return _registry_index()


def _guard(hook, sess):
    """Enforced before the tool runs: a critical deny answers `deny` to Claude Code and is recorded as
    of.mandate_check {decision: deny, verdict: critical, mode: enforce}; then the organization's rules
    (_org_guard), then the guard's warnings."""
    global OUTPUT
    try:  # G20, G21: the organizations' keychain items and protected code, from the catalogue's cache (no YAML)
        import of_credentials
        of_guard.use_protection(*of_credentials.guard_protection(
            STATE, os.environ.get("OF_ORGS") or of_events.config_path("orgs.yaml")))
    except Exception:  # noqa: BLE001 - no cache yet: the framework's rules still apply; the catalogue step rebuilds it
        pass
    hit = critical_deny(hook.get("tool_name"), hook.get("tool_input"), hook.get("cwd"))
    if not hit and (_authority_guard(hook, sess) or _release_guard(hook, sess)):
        return
    if not hit and _scratch_guard(hook, sess):
        return
    if not hit and _plugin_guard(hook, sess):
        return
    if not hit and _org_guard(hook, sess):
        return
    if not hit and _credential_guard(hook, sess):
        return
    if not hit and _WARNINGS:  # allowed, noted for the agent and logged
        why, token, *note = _WARNINGS[0]  # a rule may carry its own note (G1.9); the bare-op one (G9) does not
        _add_context(f"Organic Framework guard warning: {why}. " + (note[0] + "." if note else
                     "Use the organization's committed script for this (it calls op with OP_SERVICE_ACCOUNT_TOKEN); "
                     "a bare op will be denied once the flows have moved."))
        try:
            write("mandate_check", {"verdict": "warn", "decision": "admit", "mode": "enforce",
                                    "router_version": __import__("of_router").VERSION, "tool_name": hook.get("tool_name"),
                                    "reason": why, "subject": mask(str(token))[:300]}, hook, sess, _guard_index())
        except (SystemExit, Exception):
            pass
        return
    if not hit:
        return
    why, subject = hit
    OUTPUT = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                     "permissionDecisionReason": f"Organic Framework critical deny: {why}. " + (
                                         "" if why == of_guard.GRANT_DENY else
                                         "Secrets stay in their files; ask the owner if you need a value.")}}
    # the decision goes out before anything else can fail (recording needs the store and the registry)
    sys.stdout.write(json.dumps(OUTPUT, ensure_ascii=False))
    sys.stdout.flush()
    OUTPUT = None
    try:
        index = _guard_index()
        write("mandate_check", {"verdict": "critical", "decision": "deny", "mode": "enforce",
                                "router_version": __import__("of_router").VERSION, "tool_name": hook.get("tool_name"),
                                "reason": why, "subject": mask(str(subject))[:300]}, hook, sess, index)
    except (SystemExit, Exception):
        pass


def _add_context(text):
    """Add a note for the agent to this PreToolUse answer (the call is allowed)."""
    global OUTPUT
    prev = (((OUTPUT or {}).get("hookSpecificOutput") or {}).get("additionalContext") or "")
    OUTPUT = {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                     "additionalContext": (prev + "\n" if prev else "") + text}}


def _authority_guard(hook, sess):
    """docs/guard.md G16 (the founder through of_conductor, hand-over 630f29e9, 2026-11-11): a call that would
    contact a regulator or an authority (of_guard.authority_contacts) is denied unless a signed allow grant from the
    tenant names that one contact for the session's position (of_grants.contact_grants). It covers every
    organization and fails closed: a session without a position, or grants that cannot be read, get no exception.
    A contact a grant allows is recorded. True when the call was denied."""
    global OUTPUT
    tool = hook.get("tool_name")
    if tool != "Bash" and not str(tool).startswith("mcp__"):
        return False
    try:
        import of_credentials
        cat = of_credentials.cached(STATE, os.environ.get("OF_ORGS") or of_events.config_path("orgs.yaml"))
        of_guard.use_authorities(c for e in cat.values() for c in e.get("authorities") or [])
    except Exception:  # noqa: BLE001 - the framework's list still applies
        pass
    try:
        contacts = of_guard.authority_contacts(tool, hook.get("tool_input"))
    except Exception:  # noqa: BLE001 - the detector itself failed: nothing to name
        return False
    if not contacts:
        return False
    sid, name, org, allowed = hook.get("session_id"), None, None, {}
    try:
        import of_grants
        index = _guard_index(sid)
        name, org = (((index.get("sessions") or {}).get(sid) or []) + [None, None])[:2]
        org = org or resolve_org(None, hook.get("cwd"))
        allowed = of_grants.contact_grants(org, name, os.environ.get("OF_ORGS") or of_events.config_path("orgs.yaml"))
    except Exception:  # noqa: BLE001 - no grant read, no exception
        allowed = {}
    missing = [(c, how) for c, how in contacts if c not in allowed]

    def record(verdict, decision, grants):
        try:
            write("mandate_check", {"verdict": verdict, "decision": decision, "mode": "enforce",
                                    "router_version": __import__("of_router").VERSION, "tool_name": tool,
                                    "rule": "authority_contact", "contacts": [c for c, _ in contacts][:20],
                                    "grants": grants}, hook, sess, _guard_index())
        except (SystemExit, Exception):
            pass

    if not missing:
        record("authority_contact", "admit", sorted({allowed[c] for c, _ in contacts}))
        return False
    contact, how = missing[0]
    OUTPUT = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                     "permissionDecisionReason": (
        f"Organic Framework guard: this call would contact a regulator or an authority ({contact}, by {how}). An "
        "agent never does that without the founder's explicit word. Ask him with `of ask`: who, what you would send "
        "and why. On his yes, a signed grant naming this one contact for your position lets it through; the `of "
        "grant issue` command goes to him as a task (docs/guard.md G16). Reading their public pages (WebFetch, a "
        "plain GET) stays open.")}}
    sys.stdout.write(json.dumps(OUTPUT, ensure_ascii=False))  # the decision goes out before recording
    sys.stdout.flush()
    OUTPUT = None
    record("authority_contact", "deny", [])
    return True


RELEASE_POSITION = "of_release"


def _release_guard(hook, sess):
    """docs/guard.md G18 (AGENTS.md, the founder 2026-11-11: only of_release merges and pushes the public
    repository): a push, a merge or another write to a GitHub repository of the organic-framework owner
    (of_guard.framework_writes) is denied unless the session holds of_release. Every agent's gh acts as the founder's
    account, so this is the only place positions are told apart. Fails closed: a session without a position is
    denied. True when the call was denied."""
    global OUTPUT
    if hook.get("tool_name") != "Bash":
        return False
    try:
        writes = of_guard.framework_writes("Bash", hook.get("tool_input"), hook.get("cwd"))
    except Exception:  # noqa: BLE001 - the detector itself failed: nothing to name
        return False
    if not writes:
        return False
    sid = hook.get("session_id")
    try:
        name = (((_guard_index(sid).get("sessions") or {}).get(sid) or []) + [None])[0]
    except Exception:  # noqa: BLE001 - no index, no position: denied below
        name = None

    def record(decision):
        try:
            write("mandate_check", {"verdict": "release_write", "decision": decision, "mode": "enforce",
                                    "router_version": __import__("of_router").VERSION, "tool_name": "Bash",
                                    "rule": "release_write", "writes": writes[:10]}, hook, sess, _guard_index())
        except (SystemExit, Exception):
            pass
    if name == RELEASE_POSITION:
        record("admit")
        return False
    OUTPUT = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                     "permissionDecisionReason": (
        f"Organic Framework guard: {writes[0]} changes the public repository on GitHub, and only of_release pushes "
        "and merges there (AGENTS.md; docs/guard.md G18). Commit on the working copy's main as before, then name the "
        "feature's commits and a reviewer to of_release with `of send of_release`. Reading, comments and reviews "
        "(gh pr view, gh pr diff, gh pr comment) stay open.")}}
    sys.stdout.write(json.dumps(OUTPUT, ensure_ascii=False))
    sys.stdout.flush()
    OUTPUT = None
    record("deny")
    return True


def _plugin_guard(hook, sess):
    """G22 (docs/guard.md): an MCP tool of a plugin the session's organization lists under `tools_excluded` is denied
    in a live session too; the list itself only keeps the plugin from a session's next start (of_tools, hand-over
    da7b128e; a user organization's card e2bb5eef, the founder: nobody acts on his behalf). True when the call was denied. A
    session of no known organization, or a broken cache, is let through, as for the organization's other rules."""
    global OUTPUT
    tool = str(hook.get("tool_name") or "")
    connector = tool.lower().startswith("mcp__claude_ai_")  # a claude.ai connector, by its url entry (msg 01a1d6c3-fe78)
    if not (tool.lower().startswith("mcp__plugin_") or connector):
        return False
    sid = hook.get("session_id")
    try:
        import of_credentials
        index = _guard_index(sid)
        name, org = (((index.get("sessions") or {}).get(sid) or []) + [None, None])[:2]
        org = org or resolve_org(None, hook.get("cwd"))
        if not org:
            return False
        orgs_file = os.environ.get("OF_ORGS") or of_events.config_path("orgs.yaml")
        hit = (of_guard.connector_deny(tool, of_credentials.plugins_excluded(org, STATE, orgs_file,
                                                                             key="connectors_excluded"))
               if connector else of_guard.plugin_deny(tool, of_credentials.plugins_excluded(org, STATE, orgs_file)))
    except Exception:  # noqa: BLE001
        return False
    if not hit:
        return False
    pid, basis = hit
    what = f"the connector {pid}" if connector else f"the plugin {pid}"
    why = (f"Organic Framework rule of {org} (docs/guard.md G22): {what} is excluded for this organization's "
           f"positions ({basis or 'tools_excluded'}), so its tools are not used here. Ask its structure keeper.")
    OUTPUT = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                     "permissionDecisionReason": why}}
    sys.stdout.write(json.dumps(OUTPUT, ensure_ascii=False))  # the decision goes out before recording
    sys.stdout.flush()
    OUTPUT = None
    try:
        write("mandate_check", {"verdict": "org_rule", "decision": "deny", "mode": "enforce",
                                "router_version": __import__("of_router").VERSION, "tool_name": tool,
                                "reason": f"G22: {what} excluded ({basis})", "subject": tool[:300]},
              hook, sess, _guard_index())
    except (SystemExit, Exception):
        pass
    return True


def _org_guard(hook, sess):
    """An organization's rules (docs/guard.md, O; docs/registry.md, Organization rules): its signed deny grants
    with a kubectl scope, read through of_grants.guard_rules. A critical deny denies; a non-critical one warns: its
    note is shown once per session and every call is recorded as of.mandate_check. True when the call was denied.
    A broken store or registry fails open here (an organization's rule); the critical rules above never do."""
    global OUTPUT
    cmd = str((hook.get("tool_input") or {}).get("command") or "")
    if hook.get("tool_name") != "Bash" or not ("kubectl" in cmd or of_guard._SSH_WORDS.search(cmd)):
        return False  # kubectl (O1) and the ssh family (O3) are the matchers: every other call skips the grants
    sid = hook.get("session_id")
    try:
        import of_grants
        index = _guard_index(sid)
        name, org = (((index.get("sessions") or {}).get(sid) or []) + [None, None])[:2]
        org = org or resolve_org(None, hook.get("cwd"))
        if not org:
            return False
        rules, notes = of_grants.guard_rules(org, STATE, os.environ.get("OF_ORGS") or of_events.config_path("orgs.yaml"))
        hits = of_guard.org_rule_hits("Bash", hook.get("tool_input"), rules, name,
                                      {"KUBECONFIG": os.environ.get("KUBECONFIG")})
    except Exception:
        return False
    if not hits:
        return False
    subject = mask(str((hook.get("tool_input") or {}).get("command") or ""))[:300]

    def record(rule, said, verdict, decision):
        try:
            write("mandate_check", {"verdict": verdict, "decision": decision, "mode": "enforce",
                                    "router_version": __import__("of_router").VERSION, "tool_name": "Bash",
                                    "grant_id": rule["id"], "reason": f"{rule['reason']} Matched: {said}.",
                                    "subject": subject, **({"grant_notes": notes} if notes else {})},
                  hook, sess, _guard_index())
        except (SystemExit, Exception):
            pass

    deny = next(((r, m) for r, m in hits if r["mode"] == "deny"), None)
    if deny:
        r, said = deny
        OUTPUT = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                         "permissionDecisionReason": f"Organic Framework rule of {org} (grant "
                                         f"{r['id']}): {r['reason']} Matched: {said}."}}
        sys.stdout.write(json.dumps(OUTPUT, ensure_ascii=False))  # the decision goes out before recording
        sys.stdout.flush()
        OUTPUT = None
        record(r, said, "org_rule", "deny")
        return True
    warned = sess.setdefault("org_warned", [])
    new = [(r, m) for r, m in hits if r["id"] not in warned]
    if new:
        _add_context(" ".join(f"Organic Framework rule of {org} (grant {r['id']}; a warning now, a deny later): "
                              f"{r['reason']} Matched: {m}." for r, m in new))
        warned.extend(r["id"] for r, _ in new)
        if sid:
            try:
                save_session(sid, sess)
            except OSError:
                pass
    for r, said in hits:
        record(r, said, "warn", "admit")
    return False


def _scratch_guard(hook, sess):
    """docs/guard.md G15: a session the pulse lists over its scratch quota (of_scratch.read_quota; over 6 GiB until
    back under 3 GiB) may read and delete in its scratch folder, never write into it. True when the call was denied.
    A missing or unreadable quota file lists nobody."""
    global OUTPUT
    sid = hook.get("session_id")
    try:
        import of_scratch
        e = of_scratch.read_quota().get(sid) if sid else None
        if not e:
            return False
        what = of_guard.scratch_write(hook.get("tool_name"), hook.get("tool_input"), hook.get("cwd"), e.get("paths"))
        if not what:
            return False
        held, quota, below = (of_scratch.gib(e.get("bytes") or 0), of_scratch.gib(of_scratch.SCRATCH_QUOTA),
                              of_scratch.gib(of_scratch.SCRATCH_CAP))
    except Exception:
        return False
    why = (f"Organic Framework scratch quota: this session's scratch folder holds {held} GiB, over the {quota} GiB "
           f"quota, so nothing new is written into it ({what}). Prune it under {below} GiB first (rm, git worktree "
           f"remove; du -sh to check); the pulse lifts this within 5 minutes (docs/guard.md G15).")
    OUTPUT = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                     "permissionDecisionReason": why}}
    sys.stdout.write(json.dumps(OUTPUT, ensure_ascii=False))  # the decision goes out before recording
    sys.stdout.flush()
    OUTPUT = None
    try:
        write("mandate_check", {"verdict": "scratch_quota", "decision": "deny", "mode": "enforce",
                                "router_version": __import__("of_router").VERSION, "tool_name": hook.get("tool_name"),
                                "rule": "scratch_quota", "reason": why, "scratch_bytes": e.get("bytes")},
              hook, sess, _guard_index())
    except (SystemExit, Exception):
        pass
    return True


def _credential_guard(hook, sess):
    """docs/guard.md G14 (docs/registry.md, Credentials; of_architect, 8ef7bc0): under an organization's credential
    root a position reads or uses only the files a credential it lists, or keeps, names; a path a credential names
    elsewhere likewise. Deny mode denies. Warn mode (of_credentials.mode: MODE_NAMED for a file a credential names,
    MODE_ROOT for an undeclared file under a root) lets the call run, shows the note once per
    session per organization, and records each file once per session. True when the call was denied. A broken
    store or registry fails open here (an organization's rule); the critical rules above never do."""
    global OUTPUT
    tool = hook.get("tool_name")
    if tool not in ("Bash", "Read", "Grep", "Edit", "MultiEdit", "NotebookEdit"):
        return False
    sid = hook.get("session_id")
    try:
        import of_credentials
        cat = of_credentials.cached(STATE, os.environ.get("OF_ORGS") or of_events.config_path("orgs.yaml"))
        refs = of_guard.credential_refs(tool, hook.get("tool_input"), hook.get("cwd"), of_credentials.interesting(cat))
        if not refs:
            return False
        index = _guard_index(sid)
        name, org = (((index.get("sessions") or {}).get(sid) or []) + [None, None])[:2]
        hits = []
        for path, _how in dict.fromkeys(refs):
            d = of_credentials.decide(path, name, org, cat)
            if d and not d[0]:
                hits.append((path, d[1], d[2]))
        if not hits:
            return False
        denying = [h for h in hits if of_credentials.mode(h[2]) == "deny"]
        path, owner, ids = (denying or hits)[0]
        why = of_credentials.reason(path, name, owner, ids, cat)
        mode = "deny" if denying else "warn"
    except Exception:
        return False
    home = os.path.expanduser("~")

    def record(verdict, decision, files):
        try:
            write("mandate_check", {"verdict": verdict, "decision": decision, "mode": "enforce",
                                    "router_version": __import__("of_router").VERSION, "tool_name": tool,
                                    "rule": "credential_root", "credential_org": owner, "credential_ids": ids,
                                    "reason": why, "files": [f.replace(home, "~", 1) for f in files][:20]},
                  hook, sess, _guard_index())
        except (SystemExit, Exception):
            pass

    if mode == "deny":
        OUTPUT = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                         "permissionDecisionReason": f"Organic Framework credential rule of {owner}: "
                                         f"{why}."}}
        sys.stdout.write(json.dumps(OUTPUT, ensure_ascii=False))  # the decision goes out before recording
        sys.stdout.flush()
        OUTPUT = None
        record("credential", "deny", [h[0] for h in hits])
        return True
    warned, seen = sess.setdefault("cred_warned", []), sess.setdefault("cred_seen", [])
    new = [h[0] for h in hits if h[0] not in seen]
    changed = bool(new) or owner not in warned
    if owner not in warned:
        _add_context(f"Organic Framework credential rule of {owner} (a warning now, a deny later): {why}.")
        warned.append(owner)
    if new:
        record("warn", "admit", new)
        sess["cred_seen"] = (seen + new)[-500:]
    if changed and sid:
        try:
            save_session(sid, sess)
        except OSError:
            pass
    return False


def _mention_guard(prompt, hook, sess, index):
    """Enforced at UserPromptSubmit: block a prompt that @-mentions a secret path before Claude Code reads it,
    and record it as of.mandate_check {deny, critical, enforce}. Defence beside the native Read deny rules."""
    global OUTPUT
    hit = prompt_secret_mention(prompt)
    if not hit:
        return False
    OUTPUT = {"decision": "block",
              "reason": f"Organic Framework critical deny: this prompt names a secret file as an @mention "
                        f"({os.path.basename(hit.rstrip('/')) or hit}); Claude Code would read it into the session. "
                        "Remove the @mention and ask the owner for the value if you need it."}
    try:
        write("mandate_check", {"verdict": "critical", "decision": "deny", "mode": "enforce",
                                "router_version": __import__("of_router").VERSION, "tool_name": "UserPromptSubmit",
                                "reason": "a secret file named as an @ mention", "subject": mask(str(hit))[:200]},
              hook, sess, index)
    except (SystemExit, Exception):
        pass
    return True


def _mandate_check(hook, sess):
    import of_router
    index = _registry_index()
    name = ((index.get("sessions") or {}).get(hook.get("session_id")) or [None])[0]
    if not name or name not in (index.get("enrolled") or []):
        return
    path = of_router.target_path(hook.get("tool_name"), hook.get("tool_input"), hook.get("cwd"))
    if not path:
        return
    org, system, keeper = of_router.resolve(path, index.get("systems") or [])
    v = of_router.verdict(name, (index.get("pos_system") or {}).get(name), system, keeper, hook.get("tool_name"),
                          outside=system is None and not of_router.inside(path, index.get("systems") or []),
                          lifecycle=of_router.lifecycle_of(index.get("systems") or [], org, system))
    if v.startswith("owned_by:"):  # work inside an accepted hand-over's scope (docs/handover.md §1.3)
        try:
            import of_handover
            # the listener's copy (handover-scopes.json); the store only where no listener keeps one
            scoped = of_handover.read_scopes()
            hid = of_handover.covering(scoped if scoped is not None else
                                       of_handover.project(of_handover.load(of_events._conn())), name, system, path)
            if hid:
                v = f"in_handover:{hid}"
        except Exception:
            pass
    cmd = (hook.get("tool_input") or {}).get("command") if hook.get("tool_name") == "Bash" else None
    write("mandate_check", {"verdict": v, "decision": "admit", "mode": "observe", "router_version": of_router.VERSION,
                            "tool_name": hook.get("tool_name"), "command_class": of_classify.command_class(hook.get("tool_name"), cmd),
                            **({"tags": [{"tag": "irreversible", "source": "guessed", "method": of_classify.TAG_VERSION,
                                       "basis": "destructive command"}]} if cmd and of_classify.destructive(cmd) else {}),
                            "path": _home(path), "system": system, "system_org": org, "keeper": keeper},
          hook, sess, index)


OUTPUT = None  # the hook's stdout JSON (SessionStart additionalContext for enrolled positions)


def open_asks_note(sid, prompt, db=None):
    """The founder's words in a session with open declared asks close none of them (of_architect, 2026-11-07: a
    prompt about another agent closed 04c0bc48). The agent sees which asks stay open and closes the one the words
    answered itself. Read from the listener's view, read-only and never waiting; None when there is nothing."""
    import sqlite3
    import of_asks
    import of_store
    if not sid:
        return None
    try:
        conn = sqlite3.connect(f"file:{db or of_store.db_path()}?mode=ro", uri=True, timeout=0.3)
        rows = conn.execute("SELECT event_id, text FROM view_asks WHERE session_id = ? AND state = 'open' "
                            "AND kind LIKE 'question (declared)%' AND coalesce(to_, 'founder') IN ('founder', 'owner') "
                            "ORDER BY ts", (sid,)).fetchall()
        conn.close()
    except sqlite3.Error:
        return None
    m = of_asks.ANSWER_FRAME.match((prompt or "").lstrip())
    rows = [(e, t) for e, t in rows if not (m and e.endswith(m.group(1)))]  # the popup's answer closes its own ask
    if not rows:
        return None
    items = "; ".join(f"{e[-8:]} «{(t or '').strip()[:70]}»" for e, t in rows[:5])
    return (f"Your open asks to the founder in this session: {items}. His words here close none of them. If they "
            "answered one, run `of status working --answered <id>`; an ask they did not answer stays in his list.")
DENY_SECRETS_FLAG = False  # set from argv in main(); gates the UserPromptSubmit @mention block


def _mandate_context(hook, index, sess):
    """An enrolled position gets its mandate, brief, rules and team as additionalContext."""
    global OUTPUT
    name = ((index.get("sessions") or {}).get(hook.get("session_id")) or [None])[0]
    if not name or name not in (index.get("enrolled") or []) or \
            hook.get("source") not in ("startup", "resume", "clear", "compact", None):
        return
    import importlib.machinery
    import importlib.util
    import of_context
    here = os.path.dirname(os.path.realpath(__file__))
    loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(here, "of"))
    spec = importlib.util.spec_from_loader("of_cli", loader)
    of = importlib.util.module_from_spec(spec)
    loader.exec_module(of)
    positions, _ = of.load_positions(os.environ.get("OF_ORGS") or of_events.config_path("orgs.yaml"))
    me = next((p for p in positions if p.get("name") == name), None)
    try:  # the works it owns or does with a loop: a target the owner decided is named (docs/registry.md, Works)
        loops = of.position_loops(me, os.environ.get("OF_ORGS") or of_events.config_path("orgs.yaml")) if me else []
    except Exception:  # noqa: BLE001 - an unreadable registry or loop.yaml never costs the session its context
        loops = []
    ctx = of_context.build(name, positions, loops)
    if not ctx:
        return
    OUTPUT = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}
    write("context_injected", {"position": name, "source": hook.get("source"), "chars": len(ctx)}, hook, sess, index)


def _memory_warning(hook, index, now=None):
    """RFC 0006 §2.3: a memory index this position keeps (its own, or a unit's or the organization's it owns) past 180
    lines or 22,000 bytes is named at session start, at most once a day per index; Claude Code cuts it at 200 lines
    and 25,000 bytes, the newest entries first. Only file sizes are read here: the hook stays fast."""
    global OUTPUT
    name = ((index.get("sessions") or {}).get(hook.get("session_id")) or [None])[0]
    if not name or hook.get("source") not in ("startup", "resume", "clear", "compact", None):
        return
    entries = (index.get("memory") or {}).get(name) or []
    if not entries:
        return
    import of_memory
    path = os.path.join(STATE, "memory-warned.json")
    try:
        with open(path) as fh:
            warned = json.load(fh)
    except (OSError, ValueError):
        warned = {}
    today = time.strftime("%Y-%m-%d", time.gmtime(now or time.time()))
    notes = []
    for d, keeper in entries:
        lines, size = of_memory.index_size(d)
        if (lines > of_memory.WARN_LINES or size > of_memory.WARN_BYTES) and warned.get(d) != today:
            warned[d] = today
            notes.append(of_memory.warning(d, name, lines, size, keeper))
    if not notes or not _runs_on_own_memory():
        return  # a session still on the folder memory reads none of these (of_capacity seeds them before the switch)
    try:
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(warned, fh)
    except OSError:
        pass
    prev = (((OUTPUT or {}).get("hookSpecificOutput") or {}).get("additionalContext") or "")
    OUTPUT = {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                     "additionalContext": (prev + "\n\n" if prev else "") + "\n".join(notes)}}


def _runs_on_own_memory():
    """Whether the Claude Code process above this hook was started with autoMemoryDirectory (of spawn, of capacity
    move or resume; RFC 0006), as of_capacity tells it: by the process's arguments."""
    import subprocess
    pid = os.getppid()
    for _ in range(5):  # the hook runs under a shell the harness started; only claude's own arguments count
        try:
            out = subprocess.run(["ps", "-o", "ppid=,args=", "-p", str(pid)], capture_output=True, text=True,
                                 timeout=2).stdout.strip()
        except Exception:  # noqa: BLE001
            return False
        if not out:
            return False
        ppid, _, args = out.partition(" ")
        if os.path.basename(args.split(" ", 1)[0]) == "claude":
            return "autoMemoryDirectory" in args
        try:
            pid = int(ppid)
        except ValueError:
            return False
    return False


def _holder_check(hook, index, sess):
    """A new session in a pane whose label names a position held by another session."""
    pane = os.environ.get("HERDR_PANE_ID")
    if not pane:
        return
    reply = herdr_call("pane.get", {"pane_id": pane}) or {}
    label = (((reply.get("result") or {}).get("pane") or {}).get("label") or "").strip().strip("■").strip()
    if not label:
        return
    info = (index.get("labels") or {}).get(label)
    sid = hook.get("session_id")
    if info and info[0] and info[0] != sid and hook.get("source") in ("startup", "resume"):
        write("holder_changed", {"position": label, "previous_session": info[0], "new_session": sid,
                                 "evidence": f"herdr pane {pane} is labelled {label}"}, hook, sess, index)


def _store_raw(event, hook, raw):
    """Keep the full hook JSON in the raw tier. Plain tool events are skipped: the transcript
    holds the same tool input and output, and ingestion stores it. A busy database means the
    payload is spooled to disk for the ingest worker, never lost and never waited for."""
    global RAW_REF
    if event in ("PreToolUse", "PostToolUse", "PostToolUseFailure") and \
            hook.get("tool_name") not in ("AskUserQuestion", "SendMessage"):
        return
    import of_store
    try:
        RAW_REF = of_store.add_hook_record(of_events._conn(), event, hook.get("session_id"), raw)
    except Exception as e:
        try:
            of_store.spool_append("hook-raw.jsonl", {"hook_event": event, "session_id": hook.get("session_id"),
                                                     "received_at": of_events.now_iso(), "raw": raw})
            _log_error(event, None, f"spooled raw payload ({type(e).__name__})")  # expected under load
        except Exception as e2:
            _log_error(event, e2)


def _ingest_detached(path):
    """Queue the transcript for the ingest worker, and start the worker unless one is running.
    One worker per machine: no lock fight between dozens of ingests (lab finding 3)."""
    import fcntl
    import subprocess
    try:
        pending = os.path.join(STATE, "pending")
        os.makedirs(pending, mode=0o700, exist_ok=True)
        marker = os.path.join(pending, hashlib.sha256(path.encode()).hexdigest()[:24])
        with open(os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            fh.write(path)
        with open(os.open(os.path.join(STATE, "ingest.lock"), os.O_WRONLY | os.O_CREAT, 0o600)) as lk:
            try:
                fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return  # a worker is running and will pick the marker up
            fcntl.flock(lk, fcntl.LOCK_UN)
        here = os.path.dirname(os.path.realpath(__file__))
        subprocess.Popen([sys.executable, os.path.join(here, "of"), "ingest", "--pending", "--quiet"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
    except Exception as e:
        _log_error("ingest", e)


def _log_error(event, e, note=None):
    """hook-errors.log: real errors as `ERROR`, expected degradations (spooling) as `spooled`."""
    try:
        with open(os.path.join(STATE, "hook-errors.log"), "a") as fh:
            line = f"spooled: {note}" if note else f"ERROR {type(e).__name__}: {e}"
            fh.write(f"{of_events.now_iso()} {event} {line}\n")
    except Exception:
        pass


HOOK_DB_TIMEOUT = 0.15  # seconds a hook waits for the database before spooling


def _timing(event, hook, started):
    """For enrolled positions only: how long the hook took (steering must stay cheap), one JSON line in
    hook-timing.jsonl {ts, event, tool, position, ms}. Never raises."""
    try:
        index = _guard_index()  # the cache as it is: timing never waits for the registries' YAML
        name = ((index.get("sessions") or {}).get(hook.get("session_id")) or [None])[0]
        if not name or name not in (index.get("enrolled") or []):
            return
        line = {"ts": of_events.now_iso(), "event": event, "tool": hook.get("tool_name"), "position": name,
                "ms": round((time.time() - started) * 1000, 1), "context_chars": len(json.dumps(OUTPUT)) if OUTPUT else 0}
        with open(os.path.join(STATE, "hook-timing.jsonl"), "a") as fh:
            fh.write(json.dumps(line) + "\n")
    except Exception:
        pass


def main(argv):
    global DENY_SECRETS_FLAG
    of_events.HOOK_TIMEOUT = HOOK_DB_TIMEOUT
    event = argv[0] if argv else ""
    DENY_SECRETS_FLAG = "--deny-secrets" in argv  # gates the @mention prompt block (of_architect, 2026-11-06)
    started = _T0
    hook = {}
    try:
        raw = sys.stdin.read(4 * 1024 * 1024)
        hook = json.loads(raw) if raw.strip() else {}
        event = hook.get("hook_event_name") or event
        _store_raw(event, hook, raw)
        handle(event, hook)
        if OUTPUT:
            sys.stdout.write(json.dumps(OUTPUT, ensure_ascii=False))
        if event in ("Stop", "SessionEnd", "PreCompact") and hook.get("transcript_path"):
            _ingest_detached(hook["transcript_path"])
    except (SystemExit, Exception) as e:  # never fail the agent; leave a trace for debugging
        try:
            with open(os.path.join(STATE, "hook-errors.log"), "a") as fh:
                fh.write(f"{of_events.now_iso()} {event} {type(e).__name__}: {e}\n")
        except Exception:
            pass
    if event in ("PostToolUse", "SessionStart", "Stop", "UserPromptSubmit"):
        _timing(event, hook, started)
    return 0
