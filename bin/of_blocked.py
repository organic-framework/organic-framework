"""Blocking screens become asks (founder, 2026-11-06): no session sits blocked unseen.

A session can stop on a dialog the owner never sees: the folder trust question, an MCP server that
needs authentication, "Press Enter to continue", a usage limit or a login, the exit dialog, any
numbered menu without an input field, a permission prompt. The listener (BlockedProcessor) reads
the live panes' screens (herdr pane read), plus Claude Code's own state (`claude agents --json`:
waiting, waitingFor) and herdr's `blocked`, and for each dialog it finds opens one ask:

  of.agent_question {detected_by: blocked, blocked_type, pane, session, position, fingerprint,
                     snippet (the last 15 lines, secrets masked), options [{key, label}], folder, to}

Routing: trust, permission, MCP authentication, menus, press-enter and exit go to the founder;
limits and login go to of_capacity. Folder trust is the founder's alone and is never confirmed
automatically; the card shows the full path. An answer in the popup sends the option's keys to that
pane only, and only while the screen still shows the same dialog (the fingerprint is checked right
before sending); otherwise: "the dialog has changed, open the session (g)". The ask closes when the
dialog is gone from the screen.
"""

import hashlib
import re

SNIPPET_LINES = 15
TYPES = ("trust", "mcp_auth", "press_enter", "limit", "login", "exit", "compact", "permission", "menu")
TO = {"limit": "of_capacity", "login": "of_capacity"}
# Sent to the receiver's inbox (of send --fyi), never typed into its pane (of_capacity, 2026-11-09: its balancer acts
# on every limit within 5 minutes, and each typed limit ask woke its large context only to confirm that). A login
# stays typed: it needs the receiver.
FYI = {"limit"}


def fyi(d, position, pane, ask_id):
    """The inbox note for an FYI-kind dialog: who, where, which ask, the screen's last lines."""
    tail = " / ".join(ln.strip(" │") for ln in (d.get("snippet") or "").splitlines()[-3:] if ln.strip(" │"))
    tail = re.sub(r"@(?=[~/.])", "(at)", tail)  # no file mention in a text another session reads (AGENTS.md)
    return (f"{position or 'an agent'} shows a {d['type']} screen in pane {pane} (ask {ask_id[-8:]}, OF's "
            f"blocked-screen detector; it closes itself when the screen clears): {tail[:400]}")

_TRUST = re.compile(r"(?i)(is this a project you created or one you trust|do you trust the files in this folder|"
                    r"trust this folder)")
_MCP = re.compile(r"(?i)(needs authentication|requires authentication|authenticate with|mcp.*(?:auth|login))")
_ENTER = re.compile(r"(?i)press enter to continue")
_LIMIT = re.compile(r"(?i)(usage limit|limit reached|5-hour limit|weekly limit|you've reached your|rate limit reached)")
# Claude Code's warning under an open prompt ("You've used 90% of your 5-hour limit · resets 3:10am") is not a stop
# (2026-11-09, ask fb158869: accounting_keeper idle at its prompt became "stopped on a usage limit"); a stop says
# "limit reached", "You've reached your …" or "usage limit".
_LIMIT_WARNING = re.compile(r"(?i)you've used \d+\s*% of your")
# A turn that ended after the limit line ("✻ Worked for 0s · done 6:27 PM") makes it history, not a stop
# (of_capacity, 2026-11-11, ask ce572545: a scheduled run's old limit line above an idle prompt, the window reopened).
_TURN_DONE = re.compile(r"(?i)\b[a-z]+ed for \d+\s*[smh]\b")


def _limit_stop(lines):
    """A usage-limit line that is still the session's state: no warning-only line, and no finished turn after it."""
    hits = [i for i, ln in enumerate(lines) if _LIMIT.search(ln) and not _LIMIT_WARNING.search(ln)]
    return bool(hits) and not any(_TURN_DONE.search(ln) for ln in lines[hits[-1] + 1:])
_LOGIN = re.compile(r"(?i)(please run /login|login required|not logged in|session expired.*log ?in|/login to)")
_EXIT = re.compile(r"(?i)exit and stop tasks")
# Claude Code's offer to compact (2026-11-10, of_capacity msg 01a1c64d; of_server msg 01a1c650): its rows have no
# numbers, and Enter there compacts the conversation. Esc ("not now") leaves the session as it was.
_COMPACT = re.compile(r"(?i)we recommend compacting earlier")
COMPACT_OPTIONS = [{"key": "esc", "label": "Not now (Esc): the session stays as it is"},
                   {"key": "Enter", "label": "Confirm the selected row (Enter): compacts the conversation"}]
_PERM = re.compile(r"(?i)(do you want to (?:proceed|make this edit|create|run)|allow .* to (?:run|use))")
_OPTION = re.compile(r"^\s*(?:[❯›>]\s*)?(\d)\.\s+(.+?)\s*$")
_INPUT_BOX = re.compile(r"^\s*(?:[│|]\s*>\s|❯\s*$|❯\s+[^\d\s]|.*(?:bypass permissions|\? for shortcuts|esc to interrupt|"
                        r"accept edits on|plan mode on))")  # Claude Code's own prompt line or its status line
_CURSOR_OPTION = re.compile(r"^\s*[│|]?\s*[❯›]\s*\d\.\s")  # a dialog marks its selected option
_PATH = re.compile(r"(/(?:Users|home|private|tmp|opt|var)[^\s'\"`│]*)")


def tail(screen, n=SNIPPET_LINES):
    lines = [ln.rstrip() for ln in (screen or "").splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    return lines[-n:]


def fingerprint(lines):
    """The dialog's identity: its text without the cursor marker and spacing."""
    norm = "\n".join(re.sub(r"[❯›]\s*", "", " ".join(ln.split())) for ln in lines if ln.strip())
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


def options(lines):
    out = []
    for ln in lines:
        m = _OPTION.match(ln.strip("│ "))
        if m and m.group(1) not in [o["key"] for o in out]:
            out.append({"key": m.group(1), "label": m.group(2).strip(" │")})
    return out


def detect(screen):
    """{type, options, fingerprint, snippet, folder} for a blocking dialog on this screen, or None."""
    lines = tail(screen)
    text = "\n".join(lines)
    if not text.strip():
        return None
    opts = options(lines)
    kind = None
    if _TRUST.search(text):
        kind = "trust"
    elif _EXIT.search(text):
        kind = "exit"
    elif _COMPACT.search(text):
        kind = "compact"
        opts = COMPACT_OPTIONS
    elif _limit_stop(lines):
        kind = "limit"
    elif _LOGIN.search(text):
        kind = "login"
    elif _MCP.search(text):
        kind = "mcp_auth"
    elif _ENTER.search(text):
        kind = "press_enter"
        opts = opts or [{"key": "Enter", "label": "Continue (Enter)"}]
    elif _PERM.search(text) and opts:
        kind = "permission"
    elif len(opts) >= 2 and any(_CURSOR_OPTION.match(ln) for ln in lines) and not any(_INPUT_BOX.match(ln) for ln in lines):
        kind = "menu"  # a numbered list in the agent's text has no cursor, and the prompt below it is open
    if not kind:
        return None
    folder = None
    if kind == "trust":
        m = _PATH.search(text)
        folder = m.group(1) if m else None
    return {"type": kind, "options": opts, "fingerprint": fingerprint(lines), "snippet": mask("\n".join(lines)),
            "folder": folder, "to": TO.get(kind, "founder")}


def mask(text):
    try:
        import of_hook
        return of_hook.mask(text)
    except Exception:  # noqa: BLE001
        return text


def question(d, position):
    who = position or "a session"
    if d["type"] == "trust":
        return f"{who} waits on the folder trust dialog for {d.get('folder') or 'a new folder'}. Trust it?"
    return {"mcp_auth": f"{who} waits: an MCP server needs authentication.",
            "press_enter": f"{who} waits on \"Press Enter to continue\".",
            "limit": f"{who} stopped on a usage limit.",
            "login": f"{who} needs a login.",
            "exit": f"{who} shows the exit dialog (exit and stop tasks?).",
            "compact": f"{who} shows Claude Code's offer to compact earlier; messages cannot reach it until it closes.",
            "permission": f"{who} waits on a permission prompt.",
            "menu": f"{who} waits on a menu choice."}.get(d["type"], f"{who} is blocked.")


def keys_for(option):
    k = option.get("key")
    return ["Enter"] if k == "Enter" else [k]
