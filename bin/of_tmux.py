"""Agents in tmux, for machines without herdr (docs/remote.md §3 row 5; owner of_herdr).

A person who reaches OF only through the conductor's chat runs the other agents in tmux. OF treats such an agent
like one in a herdr pane:
- OF's Claude Code hook reports the agent's state for its tmux pane (report(), called where the hook reports
  state to herdr), one small file per pane under <state>/tmux-panes/;
- agents() reads them back as rows shaped like herdr's `agent list`, so binding a position, `of who` and the relay
  see the agent, its session and its state;
- screen(), wait(), prompt() and send_enter() are what the relay needs to type a message in: only when the agent is
  idle, only into an empty input box, declared as an injection first (bin/of_relay_client.py, herdr_deliver).

A tmux agent's pane id is 'tmux:<socket name>:%<n>', for example 'tmux:default:%3'. herdr never sees it, so no herdr
call may take it (is_tmux()). Nothing here moves a person's focus or selects a pane: text goes in by a paste buffer.

tmux keeps no agent state of its own; the hook's reports are the only source. A report needs the Claude Code
process's pid, read once at session start, so a pane whose agent exited (a shell prompt again) is never typed into.
"""

import json
import os
import re
import subprocess
import time

PREFIX = "tmux:"
SUBMIT_DELAY_S = 0.3  # herdr 0.9.5 waits 300 ms between a prompt's text and its Enter (AGENT_PROMPT_SUBMIT_DELAY)
# Every C0 and C1 control but tab and line feed. A message holding ESC [201~ would end the bracketed paste early, and
# the rest would reach Claude Code as keys: Enter, or Shift+Tab, which changes the permission mode.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
STATES = ("idle", "working", "blocked", "done")


def _dir():
    import of_events
    return os.path.join(of_events.STATE_DIR, "tmux-panes")


def is_tmux(pane):
    return str(pane or "").startswith(PREFIX)


def _where(env):
    """(socket path, pane like '%3') from the tmux variables of a process inside a tmux pane, or None.
    TMUX is '<socket path>,<server pid>,<session index>'; TMUX_PANE is '%<n>'."""
    sock = (env.get("TMUX") or "").split(",")[0]
    pane = env.get("TMUX_PANE") or ""
    if not sock or not re.fullmatch(r"%\d+", pane):
        return None
    return sock, pane


def pane_id(sock, pane):
    return f"{PREFIX}{os.path.basename(sock)}:{pane}"


def _file(sock, pane):
    return os.path.join(_dir(), f"{os.path.basename(sock)}_{pane.lstrip('%')}.json")


def _load(path):
    try:
        with open(path) as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def _save(path, doc):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = f"{path}.{os.getpid()}"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(doc, fh)
    os.replace(tmp, path)


def claude_pid(start=None, ps=None):
    """The Claude Code process above this one: the hook runs under a shell the harness started. None if not found."""
    ps = ps or (lambda pid: subprocess.run(["ps", "-o", "ppid=,args=", "-p", str(pid)], capture_output=True,
                                           text=True, timeout=2).stdout.strip())
    pid = start or os.getppid()
    for _ in range(6):
        try:
            out = ps(pid)
        except Exception:  # noqa: BLE001 - a hook never fails the agent
            return None
        if not out:
            return None
        ppid, _, args = out.strip().partition(" ")
        argv = args.split()
        first = os.path.basename(argv[0]) if argv else ""
        if first == "claude" or (first == "node" and len(argv) > 1 and "claude" in argv[1]):  # native, or npm's
            return pid
        try:
            pid = int(ppid)
        except ValueError:
            return None
    return None


def report(state, session, message=None, env=None, pid=None, now=None):
    """The hook's report for this process's tmux pane: state is idle | working | blocked, or 'started' (a new or
    resumed session: idle, with the Claude Code pid), 'compacted' (keep the state) or 'ended' (forget the pane).
    Does nothing outside tmux, inside herdr, or without a session. Never raises."""
    env = os.environ if env is None else env
    where = _where(env)
    if not where or env.get("HERDR_PANE_ID") or not session:
        return None
    sock, pane = where
    path = _file(sock, pane)
    try:
        doc = _load(path)
        if state == "ended":
            if doc.get("session") == session:
                os.unlink(path)
            return None
        same = doc.get("session") == session
        if state == "compacted":
            if same:
                return doc
            state = "working"  # a compaction runs inside a turn
        if state == "started" or not same:
            doc = {"session": session, "pid": pid if pid is not None else claude_pid(), "started": now or time.time()}
            state = "idle" if state == "started" else state
        doc.update(socket=sock, pane=pane, pane_id=pane_id(sock, pane), state=state, message=(message or "")[:200],
                   ts=now or time.time(), cwd=env.get("PWD"))
        _save(path, doc)
        return doc
    except OSError:
        return None


def _run(sock, *args, input=None, timeout=10):
    return subprocess.run(["tmux", "-S", sock, *args], capture_output=True, text=True, input=input, timeout=timeout)


def _alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except PermissionError:
        return True
    except (OSError, ValueError):
        return False


def _records():
    try:
        names = sorted(os.listdir(_dir()))
    except OSError:
        return []
    return [(os.path.join(_dir(), n), _load(os.path.join(_dir(), n))) for n in names if n.endswith(".json")]


def agents(alive=None):
    """Live Claude Code agents in tmux panes on this machine, shaped like herdr's `agent list` rows. A pane counts
    while its tmux server lists it and the Claude Code process the hook found is alive; a report whose process
    is gone is removed. [] when tmux is not installed or no agent reported."""
    alive = alive or _alive
    recs = _records()
    if not recs:
        return []
    panes = {}
    for sock in {d.get("socket") for _, d in recs if d.get("socket")}:
        try:
            r = _run(sock, "list-panes", "-a", "-F", "#{pane_id}\t#{pane_dead}")
        except (OSError, subprocess.SubprocessError):
            panes[sock] = None  # no tmux here, or its server is not answering
            continue
        panes[sock] = {ln.split("\t")[0] for ln in r.stdout.splitlines() if ln.endswith("\t0")} if r.returncode == 0 \
            else set()
    out = []
    for path, d in recs:
        live = panes.get(d.get("socket"))
        if live is None or d.get("pane") not in live or not alive(d.get("pid")):
            if live is not None and not alive(d.get("pid")):
                try:
                    os.unlink(path)  # the agent exited without SessionEnd; its pane may hold a shell now
                except OSError:
                    pass
            continue
        out.append({"pane_id": d["pane_id"], "agent": "claude", "agent_status": d.get("state") or "unknown",
                    "agent_session": {"value": d.get("session")}, "runtime": "tmux", "name": None, "tokens": {},
                    "workspace_id": None, "tab_id": None, "terminal_title_stripped": None, "cwd": d.get("cwd"),
                    "state_message": d.get("message") or None, "state_ts": d.get("ts")})
    return out


def _record(pane):
    """The report behind a tmux pane id, or {}."""
    for _, d in _records():
        if d.get("pane_id") == pane:
            return d
    return {}


def state(pane):
    d = _record(pane)
    return d.get("state") if d and _alive(d.get("pid")) else None


def wait(pane, until, timeout_s, step=0.5, clock=None, sleep=None):
    """Wait until the agent's reported state is one of `until`. Returns (True, state), or (False, last state) at the
    timeout; (False, None) when the pane has no live agent."""
    clock, sleep = clock or time.monotonic, sleep or time.sleep
    end = clock() + timeout_s
    while True:
        s = state(pane)
        if s is None or s in until:
            return s is not None, s
        if clock() >= end:
            return False, s
        sleep(step)


_SGR = re.compile(r"\x1b\[([0-9;:]*)m")


def _dim_only(line):
    """tmux's capture joins attributes into one SGR (for example '\\x1b[2;38;5;8m'); the relay's box parser reads
    dim as '\\x1b[2m' ... '\\x1b[22m'. Keep only those two, in that form, and drop every other SGR."""
    out, pos, dim = [], 0, False
    for m in _SGR.finditer(line):
        out.append(line[pos:m.start()])
        pos = m.end()
        params = [p for p in re.split(r"[;:]", m.group(1))] or [""]
        i, now = 0, dim
        while i < len(params):
            p = params[i]
            if p in ("", "0"):
                now = False
            elif p == "2":
                now = True
            elif p == "22":
                now = False
            elif p in ("38", "48", "58"):  # a colour: 5;n or 2;r;g;b follow, never attributes
                i += 2 if i + 1 < len(params) and params[i + 1] == "5" else 4
            i += 1
        if now != dim:
            out.append("\x1b[2m" if now else "\x1b[22m")
            dim = now
    out.append(line[pos:])
    return "".join(out) + ("\x1b[22m" if dim else "")


def screen(pane):
    """The pane's visible screen with dim marked as the relay's parser expects, or None when it cannot be read."""
    d = _record(pane)
    if not d:
        return None
    try:
        r = _run(d["socket"], "capture-pane", "-p", "-e", "-t", d["pane"])
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return "\n".join(_dim_only(ln) for ln in r.stdout.split("\n"))


def clean(text):
    """The text with every control character removed but tab and line feed (a carriage return becomes a line feed)."""
    return _CONTROL.sub("", (text or "").replace("\r\n", "\n").replace("\r", "\n"))


def prompt(pane, text, sleep=None):
    """Type `text` into the agent's input box and submit it, as herdr's `agent prompt` does: the text, its control
    characters removed (clean()), as one bracketed paste (tmux adds the brackets when the program asked for them),
    then Enter after SUBMIT_DELAY_S. Returns (ok, error)."""
    d = _record(pane)
    if not d:
        return False, "no live tmux agent in this pane"
    buf = f"of-relay-{os.getpid()}"
    try:
        r = _run(d["socket"], "load-buffer", "-b", buf, "-", input=clean(text))
        if r.returncode != 0:
            return False, (r.stderr or "tmux load-buffer failed").strip()[:200]
        r = _run(d["socket"], "paste-buffer", "-p", "-d", "-b", buf, "-t", d["pane"])
        if r.returncode != 0:
            return False, (r.stderr or "tmux paste-buffer failed").strip()[:200]
        (sleep or time.sleep)(SUBMIT_DELAY_S)
        return send_enter(pane, d)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"tmux: {type(e).__name__}: {e}"[:200]


def send_enter(pane, d=None):
    d = d or _record(pane)
    if not d:
        return False, "no live tmux agent in this pane"
    r = _run(d["socket"], "send-keys", "-t", d["pane"], "Enter")
    return r.returncode == 0, (r.stderr.strip()[:200] or None)
