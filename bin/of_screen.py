"""Agents in GNU screen, for machines without herdr or tmux (owner of_server, 2026-11-11).

macOS ships screen (4.00.03 on the founder's Mac) and not tmux, so `of init` starts an organization's agents in a
detached screen session named after the position when tmux is missing (bin/of_init.py, detached()). of_onboarding,
through of_platform (msg 01a1cbdf, 2026-11-11): such an agent never learned that a message waited for it, so a new
organization's onboarding lead would sit idle after the conductor's first message. OF treats it like an agent in
tmux (bin/of_tmux.py, owner of_herdr, which this mirrors):
- OF's Claude Code hook reports the agent's state for its screen window (report()), one small file per window under
  <state>/screen-windows/;
- agents() reads them back as rows shaped like herdr's `agent list`, so binding a position, `of who` and the relay
  see the agent, its session and its state;
- screen(), wait(), prompt() and send_enter() are what the relay needs to type a message in: only when the agent is
  idle, declared as an injection first (bin/of_relay_client.py, herdr_deliver).

A screen agent's pane id is 'screen:<pid>.<session name>:<window>', for example 'screen:30334.of_lead:0'. herdr never
sees it, so no herdr call may take it (is_screen()).

How text goes in (docs/guard.md R10). Through screen's paste buffer (readbuf from a private file, then paste), so
screen never parses it: `stuff` would read ^X and backslash escapes in a message as keys. The text is one bracketed
paste: screen 4.00.03 turns each line feed of a paste into a carriage return, which would submit every line on its
own. Control characters are removed first, so a message cannot close the paste early and type keys. Enter follows
after SUBMIT_DELAY_S, as herdr's `agent prompt` does.

A hardcopy keeps no attributes, so a dim suggestion in Claude Code's input box cannot be told from typed text. In a
detached session nobody can type, so there the relay counts only its own held delivery as text in the box; in an
attached one, any text in the box defers the message (a person may be typing).
"""

import os
import re
import subprocess
import tempfile
import time

import of_tmux

PREFIX = "screen:"
SUBMIT_DELAY_S = 0.3  # herdr 0.9.5 waits 300 ms between a prompt's text and its Enter (AGENT_PROMPT_SUBMIT_DELAY)
PASTE_KEEP_S = 120    # a paste file outlives its delivery: `screen -X` returns before the session reads the file
PASTE_START, PASTE_END = b"\x1b[200~", b"\x1b[201~"
_STY = re.compile(r"\d+\.[A-Za-z0-9_.-]+")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")  # every C0 and C1 control but tab and line feed


def _dir():
    import of_events
    return os.path.join(of_events.STATE_DIR, "screen-windows")


def _private():
    """A folder only this user reads, for the paste and hardcopy files."""
    path = os.path.join(_dir(), "io")
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def is_screen(pane):
    return str(pane or "").startswith(PREFIX)


def _where(env):
    """(STY like '30334.of_lead', window like '0') from the screen variables of a process inside a screen window,
    or None."""
    sty, win = env.get("STY") or "", env.get("WINDOW") or ""
    if not _STY.fullmatch(sty) or not win.isdigit():
        return None
    return sty, win


def pane_id(sty, win):
    return f"{PREFIX}{sty}:{win}"


def _file(sty, win):
    return os.path.join(_dir(), f"{sty}_{win}.json")


def report(state, session, message=None, env=None, pid=None, now=None):
    """The hook's report for this process's screen window, as of_tmux.report() for a tmux pane: state is idle |
    working | blocked, or 'started', 'compacted' or 'ended'. Does nothing outside screen, inside herdr or tmux (the
    innermost one reports), or without a session. Never raises."""
    env = os.environ if env is None else env
    where = _where(env)
    if not where or env.get("HERDR_PANE_ID") or env.get("TMUX_PANE") or not session:
        return None
    sty, win = where
    path = _file(sty, win)
    try:
        doc = of_tmux._load(path)
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
            doc = {"session": session, "pid": pid if pid is not None else of_tmux.claude_pid(),
                   "started": now or time.time()}
            state = "idle" if state == "started" else state
        doc.update(sty=sty, window=win, pane_id=pane_id(sty, win), state=state, message=(message or "")[:200],
                   ts=now or time.time(), cwd=env.get("PWD"))
        of_tmux._save(path, doc)
        return doc
    except OSError:
        return None


def _run(*args, timeout=10):
    return subprocess.run(["screen", *args], capture_output=True, text=True, timeout=timeout)


_LS = re.compile(r"^\s+(\d+\.\S+)\s.*\((Attached|Detached|Multi, attached|Multi, detached)\)\s*$", re.I)


def sessions(run=_run):
    """{STY: attached} for this user's live screen sessions, or None when screen is missing or does not answer.
    `screen -ls` exits 1 when it lists sessions (4.00.03), so its code is not read."""
    try:
        r = run("-ls")
    except (OSError, subprocess.SubprocessError):
        return None
    out = {}
    for line in (r.stdout or "").splitlines():
        m = _LS.match(line)
        if m:
            out[m.group(1)] = "attached" in m.group(2).lower() and "detached" not in m.group(2).lower()
    return out


def _records():
    try:
        names = sorted(os.listdir(_dir()))
    except OSError:
        return []
    return [(os.path.join(_dir(), n), of_tmux._load(os.path.join(_dir(), n))) for n in names if n.endswith(".json")]


def agents(alive=None, live=None):
    """Live Claude Code agents in screen windows on this machine, shaped like herdr's `agent list` rows. A window
    counts while `screen -ls` lists its session and the Claude Code process the hook found is alive; a report whose
    process is gone is removed. [] when screen is missing or no agent reported."""
    recs = _records()
    if not recs:
        return []
    alive = alive or of_tmux._alive
    live = sessions() if live is None else live
    if live is None:
        return []
    out = []
    for path, d in recs:
        if d.get("sty") not in live or not alive(d.get("pid")):
            if not alive(d.get("pid")):
                try:
                    os.unlink(path)  # the agent exited without SessionEnd; its window may hold a shell now
                except OSError:
                    pass
            continue
        out.append({"pane_id": d["pane_id"], "agent": "claude", "agent_status": d.get("state") or "unknown",
                    "agent_session": {"value": d.get("session")}, "runtime": "screen", "name": None, "tokens": {},
                    "workspace_id": None, "tab_id": None, "terminal_title_stripped": None, "cwd": d.get("cwd"),
                    "state_message": d.get("message") or None, "state_ts": d.get("ts")})
    return out


def _record(pane):
    for _, d in _records():
        if d.get("pane_id") == pane:
            return d
    return {}


def state(pane):
    d = _record(pane)
    return d.get("state") if d and of_tmux._alive(d.get("pid")) else None


def wait(pane, until, timeout_s, step=0.5, clock=time.monotonic, sleep=time.sleep):
    """Wait until the agent's reported state is one of `until`. Returns (True, state), or (False, last state) at the
    timeout; (False, None) when the window has no live agent."""
    end = clock() + timeout_s
    while True:
        s = state(pane)
        if s is None or s in until:
            return s is not None, s
        if clock() >= end:
            return False, s
        sleep(step)


def attached(pane, run=_run):
    """Is a person's terminal attached to the agent's screen session? None when it cannot be told."""
    d = _record(pane)
    live = sessions(run) if d else None
    return None if live is None or d.get("sty") not in live else live[d["sty"]]


def _target(d):
    return ("-S", d["sty"], "-p", str(d.get("window") or "0"), "-X")


def screen(pane, run=_run, sleep=time.sleep):
    """The window's visible screen as plain text (a hardcopy keeps no attributes), or None when it cannot be read."""
    d = _record(pane)
    if not d:
        return None
    fd, path = tempfile.mkstemp(prefix="hardcopy-", dir=_private())
    os.close(fd)
    os.unlink(path)  # screen writes it; an existing file would be appended to with hardcopy_append on
    try:
        r = run(*_target(d), "hardcopy", path)
        if r.returncode != 0:
            return None
        for _ in range(30):  # -X returns when the command is sent; the session writes the file after
            if os.path.exists(path) and os.path.getsize(path) > 0:
                sleep(0.05)
                break
            sleep(0.1)
        with open(path, "rb") as fh:
            return decode(fh.read())
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def decode(raw):
    """A hardcopy's text. screen 4.00.03 writes the low byte of each character, even in UTF-8 mode: read as Latin-1,
    Claude Code's box rules (U+2500) are NUL lines and its prompt mark (U+276F) an 'o' before a no-break space; both
    are put back so the relay's box parser reads the box. A newer screen writes UTF-8, kept as it is."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    out = []
    for line in text.split("\n"):
        if line.strip(" ") and set(line.strip(" ")) == {"\x00"}:
            line = line.replace("\x00", "\u2500")
        if line.startswith("o\xa0"):
            line = "\u276f" + line[1:]
        out.append(line)
    return "\n".join(out)


def clean(text):
    """The text with every control character removed but tab and line feed (a carriage return becomes a line feed),
    so it cannot close the bracketed paste or press a key."""
    return _CONTROL.sub("", (text or "").replace("\r\n", "\n").replace("\r", "\n"))


def _sweep(folder, now=None):
    """Remove the paste files older than PASTE_KEEP_S."""
    now = now or time.time()
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        try:
            if name.startswith("paste-") and now - os.path.getmtime(path) > PASTE_KEEP_S:
                os.unlink(path)
        except OSError:
            pass


def prompt(pane, text, run=_run, sleep=time.sleep):
    """Type `text` into the agent's input box and submit it: one bracketed paste through screen's paste buffer, then
    Enter after SUBMIT_DELAY_S. Returns (ok, error).

    `screen -X` returns when the command is sent, not when the session has run it (of_release's review, finding #8,
    2026-11-11). The session runs its commands in order, so readbuf comes before paste; but a paste file removed
    before the session reads it made readbuf fail, and paste then typed the previous message again (checked on
    screen 4.00.03). So the paste buffer is emptied first (`register . ""`), a failed read pastes nothing, and the
    file stays until a later delivery sweeps it (PASTE_KEEP_S)."""
    d = _record(pane)
    if not d:
        return False, "no live screen agent in this window"
    folder = _private()
    _sweep(folder)
    fd, path = tempfile.mkstemp(prefix="paste-", dir=folder)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(PASTE_START + clean(text).encode("utf-8") + PASTE_END)
        for args in (("register", ".", ""), ("readbuf", path), ("paste", ".")):
            r = run(*_target(d), *args)
            if r.returncode != 0:
                return False, (r.stderr or r.stdout or f"screen {args[0]} failed").strip()[:200]
        sleep(SUBMIT_DELAY_S)
        return send_enter(pane, d, run=run)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"screen: {type(e).__name__}: {e}"[:200]


def send_enter(pane, d=None, run=_run):
    d = d or _record(pane)
    if not d:
        return False, "no live screen agent in this window"
    r = run(*_target(d), "stuff", "\r")
    return r.returncode == 0, ((r.stderr or "").strip()[:200] or None)
