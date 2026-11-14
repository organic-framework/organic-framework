"""of relay, client side: presence and message delivery for this machine (docs/server.md,
"Relay and presence"). Owned by of_platform; the server side is bin/of_relay.py (of_server).

One loop per machine:
- every 60 s, PUT this machine's live holders to each organization's server (only that
  organization's holders; positions held by people are reported with kind "human");
- long-poll each server for messages to positions on this machine;
- deliver each message once (deduplicated by msg_id): into the agent's pane through herdr (or tmux, for an
  agent without herdr: bin/of_tmux.py, owner of_herdr), after the agent is idle, preceded by an
  `injected_prompt` declaration so the collector records the sender; or, for a position held by a
  person, into the local inbox (`of inbox`);
- ack `delivered` only after delivery; `deferred` (retry_after 30 s) when the agent stays busy for a
  few seconds, so one busy receiver never blocks the others or outlives the lease; else `failed`
  with the reason.
"""

import hashlib
import json
import os
import re
import subprocess
import threading
import time

import of_events
import of_screen
import of_store
import of_tmux


def _term(pane):
    """The module that drives an agent outside herdr, of_tmux or of_screen (docs/remote.md §3 row 5), or None for a
    herdr pane."""
    if of_tmux.is_tmux(pane):
        return of_tmux
    if of_screen.is_screen(pane):
        return of_screen
    return None

PRESENCE_EVERY = 60
# Holders are re-read this often; presence is published at once when who holds what changed (a session bound,
# moved to another account, a pane or kind changed), else every PRESENCE_EVERY. After an account move the
# server answered 403 not_holder to `of inbox --mine` for minutes (of_capacity, 2026-11-07).
PRESENCE_CHECK = 10
INBOX_DDL = """
CREATE TABLE IF NOT EXISTS relay_seen (msg_id TEXT PRIMARY KEY, at TEXT, outcome TEXT);
CREATE TABLE IF NOT EXISTS inbox (msg_id TEXT PRIMARY KEY, received_at TEXT, to_position TEXT, sender TEXT,
  authority TEXT, reply_to TEXT, text TEXT, read_at TEXT);
"""


def ensure_tables(conn):
    conn.executescript(INBOX_DDL)


def header(m):
    """Exactly one frame line before the text, never presented as the owner's words (RFC 0001 §5.9)."""
    auth = m.get("authority") or "none"
    if m.get("authority_reason"):
        auth += f": {m['authority_reason']}"
    who = f"machine {m.get('sender_machine') or '?'}" + (f", person {m['sender_person']}" if m.get("sender_person") else "")
    ref = f"msg {m.get('msg_id')}" + (f", reply to {m['reply_to']}" if m.get("reply_to") else "")
    return (f"Message from {m.get('from')} (via relay, authority: {auth}; {who}; {ref})\n"
            f"{m.get('text') or ''}\n"
            f"[reply: of send {m.get('from')} --reply-to {m.get('msg_id')} \"…\"]")


def wait_argv(pane, wait_s, working_ok=False):
    """herdr 0.9.5: `agent wait <pane> --until S [--until S] --timeout MS`. Never `blocked`: a
    blocked agent shows a dialog, and typed text would answer it. `working` only for a message that
    has waited long: Claude Code queues text submitted mid-turn as its next input."""
    argv = ["herdr", "agent", "wait", pane, "--until", "idle", "--until", "done"]
    if working_ok:
        argv += ["--until", "working"]
    return argv + ["--timeout", str(int(wait_s * 1000))]


WORKING_AFTER_S = 5 * 60  # a message older than this goes into a working pane too (never a blocked one)


def message_age_s(m, now=None):
    import calendar
    try:
        t = calendar.timegm(time.strptime(str(m.get("created_at"))[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return 0
    return (now or time.time()) - t


# An at sign directly before a path in a prompt is a file mention: Claude Code reads the file into the session
# (2026-11-06: a relayed note with '@<path of a header file>' put a Grafana token into another agent's session).
# Every text OF types into a pane passes herdr_deliver, which neutralizes such at signs first: '@' becomes the
# fullwidth '＠' (U+FF20) unless a word character, '.', '+' or '-' stands right before it (e-mail addresses keep
# theirs). No lookahead: Claude Code also reads '@"a path with spaces"' and a bare '@name' (a file or folder in the
# receiver's working directory), and any miss leaks, so every other at sign goes (of_architect, 2026-11-06).
_MENTION = re.compile(r"(?<![\w.+\-])@")


def neutralize_mentions(text):
    """(text with every at sign that could start a file mention neutralized, how many)."""
    out, n = _MENTION.subn("\uff20", text or "")
    return out, n


# The pane's input box (of_architect, 2026-11-07): of_lab's prompt was blocked by the guard and went back into its
# input box; every later delivery was appended to it and denied again, about 22 times in 10 hours. Text is typed
# only into an EMPTY box; a box holding text (a blocked prompt, a person's half-typed words) defers the message.
# Claude Code's prompt suggestion is drawn dim (SGR 2) in an empty box and does not count.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_DIM = re.compile(r"\x1b\[2m.*?(?=\x1b\[0m|\x1b\[22m|$)")
STUCK_AFTER = 3  # defers in a row with text in the box: of pulse tells the position's structure keeper, once


_NAMED_RULE = re.compile(r"─{2,}(?: [^─]{1,40} ─*)?")
_NAMED_RULE_NARROW = re.compile(r"─{4,} [^\s─]{1,40} ─+")  # a narrow pane: rule, one name, rule (2026-11-12)


def _is_rule(line):
    """A horizontal rule of Claude Code's input box, also one that carries the session's name ('──── name ─'):
    2026-11-11, of_platform: in a 72-column pane the named rule was 75% rule characters, the box was read from the
    rule above the agent's last reply, and 21 deliveries were deferred over an empty box. In a 35-column pane the
    named rule holds only 14 rule characters (2026-11-12, a user organization's unit owner, 25 deliveries deferred):
    a line of rule characters, one name and rule characters is a rule at any width."""
    t = _ANSI.sub("", line).strip()
    return t.startswith("─") and (t.count("─") > 0.8 * len(t) or (t.count("─") >= 20 and bool(_NAMED_RULE.fullmatch(t)))
                                  or bool(_NAMED_RULE_NARROW.fullmatch(t)))


def input_box_text(screen):
    """The text in Claude Code's input box on a pane screen read with ANSI codes (the lines between the last two
    horizontal rules), without the prompt mark and without a dim suggestion; '' when empty, None when no box."""
    lines = (screen or "").replace("\r", "").split("\n")
    rules = [i for i, l in enumerate(lines) if _is_rule(l)]
    if len(rules) < 2:
        return None
    box = "\n".join(_ANSI.sub("", _DIM.sub("", l)) for l in lines[rules[-2] + 1:rules[-1]])
    return box.replace("\xa0", " ").strip().lstrip("❯>").strip()


# A dialog in the pane (of_capacity, 2026-11-07: Claude Code's 'Background work is running' exit dialog swallowed two
# relay messages; herdr flagged the pane blocked only hours later). Typed text and Enter would answer the dialog.
# The key footers match in any case: the compaction offer (2026-11-10, of_capacity msg 01a1c64d) ends in
# "enter confirm · esc not now", and Enter there compacts the conversation.
_DIALOG = re.compile(r"(?i:enter to confirm|esc to cancel|enter to select|to navigate · enter|press enter to continue"
                     r"|enter confirm · esc)|Background work is running|Do you want to proceed\?"
                     r"|We recommend compacting earlier")


def dialog_open(screen):
    """Is a dialog drawn on the pane's last lines?"""
    tail = "\n".join(_ANSI.sub("", (screen or "").replace("\r", "")).split("\n")[-15:])
    return bool(_DIALOG.search(tail))


def pane_input(pane):
    """The input box's text ('' when empty), '[dialog]' when a dialog is open, None when the pane cannot be read."""
    term = _term(pane)
    if term:
        out = term.screen(pane)
    else:
        r = subprocess.run(["herdr", "pane", "read", pane, "--source", "visible", "--lines", "60", "--format", "ansi"],
                           capture_output=True, text=True, timeout=15)
        out = r.stdout if r.returncode == 0 else None
    if out is None:
        return None
    if dialog_open(out):
        return "[dialog]"
    box = input_box_text(out)
    if box and term is of_screen and of_screen.attached(pane) is False \
            and not (_PASTE_ONLY.fullmatch(box) or _FRAMED.match(box)):
        return ""  # a hardcopy has no dim: in a detached session nobody types, so this is a suggestion (of_screen)
    return box


def _stuck_path():
    return os.path.join(of_events.STATE_DIR, "input-stuck.json")


def read_stuck():
    try:
        with open(_stuck_path()) as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


# Claude Code shows a long paste as one placeholder line until Enter sends it. A box holding only that is most likely
# a delivery whose Enter was lost: on 2026-11-11 a code-audit position sat three hours behind
# '[Pasted text #4 +69 lines]', pasted while the session compacted (of_platform, msg 01a1c8a0, from a user
# organization).
_PASTE_ONLY = re.compile(r"\[Pasted text #\d+(?: \+(\d+) lines)?\]")
HELD_MAX_S = 12 * 3600  # a held delivery older than this is not pressed: a person may have taken the pane over


def note_box(pane, text, now=None):
    """Count defers for a pane whose box holds text (reset when it is empty); of pulse reports STUCK_AFTER. `paste`
    marks a box holding only a paste placeholder (of pulse's keeper note then says to press Enter)."""
    doc = read_stuck()
    if text:
        e = doc.get(pane) or {"count": 0, "since": now or time.time(), "reported": False}
        e.update(count=e["count"] + 1, chars=len(text), last=now or time.time(),
                 head=hashlib.sha256(text[:200].encode()).hexdigest()[:12],
                 paste=bool(_PASTE_ONLY.fullmatch(text.strip())))
        doc[pane] = e
    elif pane in doc:
        doc.pop(pane)
    else:
        return
    try:
        tmp = _stuck_path() + f".{os.getpid()}"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(doc, fh)
        os.replace(tmp, _stuck_path())
    except OSError:
        pass


def _typed_path():
    return os.path.join(of_events.STATE_DIR, "relay-typed.json")


def _read_typed():
    try:
        with open(_typed_path()) as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def _write_typed(doc):
    try:
        tmp = _typed_path() + f".{os.getpid()}"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(doc, fh)
        os.replace(tmp, _typed_path())
    except OSError:
        pass


def note_typed(pane, text, mark):
    """The relay's last delivery typed into a pane: its line count and its injection mark (no text)."""
    doc = _read_typed()
    doc[pane] = {"ts": mark["ts"], "newlines": text.count("\n"), "mark": mark, "enter": False}
    _write_typed(doc)


def held_delivery(pane, box, now=None, conn=None):
    """The relay's own last delivery to this pane when the box holds only its paste placeholder, its Enter lost:
    typed here within HELD_MAX_S, the placeholder's line count matches it, Enter not pressed for it yet, and the
    session's hook has recorded no message since it was typed. Else None. A store that cannot be read says None."""
    m = _PASTE_ONLY.fullmatch((box or "").strip())
    last = _read_typed().get(pane) if m else None
    now = now or time.time()
    if not last or last.get("enter") or now - (last.get("ts") or 0) > HELD_MAX_S:
        return None
    if m.group(1) is not None and abs(int(m.group(1)) - int(last.get("newlines", -9))) > 1:
        return None
    session = (last.get("mark") or {}).get("session")
    if not session:
        return None
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(last["ts"]))
    try:
        c = conn or of_events._conn()
        if c.execute("SELECT 1 FROM event WHERE name = 'of.agent_message_in' AND source = 'hook' AND session_id = ? "
                     "AND ts >= ? LIMIT 1", (session, since)).fetchone():
            return None
    except Exception:  # noqa: BLE001 - unsure: never press a key into a pane
        return None
    return last


def press_held_enter(pane, last):
    """Send the held delivery: declare it again (its first mark is past the hook's window by now), press Enter
    once, and record that it was pressed."""
    mark = dict(last.get("mark") or {}, ts=time.time())
    if mark.get("injector"):
        of_events.emit("injected_prompt", {"injector": mark["injector"], "target_session": mark.get("session"),
                                           "text_sha256": mark.get("sha256"),
                                           "reason": "the relay's own delivery held in the input box, its Enter lost: "
                                                     "Enter pressed once"})
    path = os.path.join(of_events.STATE_DIR, "injections.jsonl")
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a") as fh:
        fh.write(json.dumps(mark) + "\n")
    if _term(pane):
        ok = _term(pane).send_enter(pane)[0]
    else:
        ok = subprocess.run(["herdr", "pane", "send-keys", pane, "Enter"], capture_output=True, text=True,
                            timeout=10).returncode == 0
    doc = _read_typed()
    if pane in doc:
        doc[pane]["enter"] = time.time()
        _write_typed(doc)
    return ok


DELIVER_WAIT_S = 5   # a short wait: a busy receiver defers the message, it never holds the lease
DEFER_S = 30         # retry_after for a deferred message


# Every text a tool types is framed (of_architect, 2026-11-07: of_lab took a tool's one line for the founder's words).
_FRAMED = re.compile(r"^(?:<pasted_content[^>]*>\s*)?(?:Message from \S+ \(via |Question from \S+ \(|Answer from the founder"
                     r"|Side question from the founder)")


def frame(text, sender=None, via=None):
    """The relay's frame for a tool's text, unless it already has one: 'Message from <sender> (via <tool>,
    authority: none)' on its own line, then the text."""
    if _FRAMED.match((text or "").lstrip()):
        return text
    s = str(sender or "of")
    tool, _, rest = s.partition(":")
    if tool.startswith("of-") and rest:  # 'of-pulse:<position>': the tool, then who it speaks for
        via, who = via or tool.replace("-", " "), rest
    else:  # a position ('of_platform', 'org:position')
        via, who = via or "of", s
    return f"Message from {who} (via {via}, authority: none)\n{text}"


def herdr_deliver(pane, text, session=None, sender=None, wait_s=DELIVER_WAIT_S, human_channel=None,
                  working_ok=False, skip_if=None, via=None):
    """Wait briefly until the agent is idle or done, declare the injection, then type the message
    into the pane. Returns (True, None), ("deferred", reason) when the agent stays busy, or
    (False, reason). human_channel (e.g. "asks-ui"): the text is the owner's own words carried by
    a tool, so the collector records origin human on that channel, not an injection."""
    # idle or done only: a blocked agent shows a dialog, and typed text would answer it
    term = _term(pane)
    if term:  # an agent without herdr, in tmux or screen (docs/remote.md §3 row 5): its hook's reported state
        ok, st = term.wait(pane, ("idle", "done") + (("working",) if working_ok else ()), wait_s)
        if not ok:
            return (("deferred", f"agent busy ({st}, not idle or done within {wait_s}s)") if st else
                    (False, f"no live agent in this {term.PREFIX.rstrip(':')} pane"))
        w = None
    else:
        w = subprocess.run(wait_argv(pane, wait_s, working_ok), capture_output=True, text=True, timeout=wait_s + 30)
    if w is not None and w.returncode != 0:
        err = (w.stderr or w.stdout or "").strip()
        if "timeout" in err.lower() or "timed out" in err.lower() or not err:
            return "deferred", f"agent busy (not idle or done within {wait_s}s)"
        return False, f"herdr agent wait failed (rc {w.returncode}): {err[:200]}"
    if skip_if and skip_if():  # taken meanwhile (of inbox --mine --take): never typed twice
        return "skipped", "pulled"
    box = pane_input(pane)
    note_box(pane, box)
    if box == "[dialog]":
        return "deferred", "a dialog is open in the pane"
    last = held_delivery(pane, box) if box else None
    if last:  # its own earlier delivery, Enter lost: sent now; this message waits for the next offer
        ok = press_held_enter(pane, last)
        return "deferred", ("pressed Enter on the relay's own delivery held in the box" if ok else
                            "the relay's own delivery is held in the box; pressing Enter failed")
    if box:  # never appended to a blocked prompt or a person's half-typed words
        return "deferred", f"the input box holds text ({len(box)} chars)"
    if not human_channel:  # a tool's text never reads as the owner's words
        text = frame(text, sender, via)
    text, mentions = neutralize_mentions(text)
    digest = hashlib.sha256(text.strip().encode()).hexdigest()
    mark = {"ts": time.time(), "session": session, "sha256": digest, "injector": f"of-relay:{sender}"}
    if human_channel:
        mark.update(injector=None, origin="human", channel=human_channel)
    else:
        of_events.emit("injected_prompt", {"injector": f"of-relay:{sender}", "target_session": session,
                                           "text_sha256": digest, "reason": "relayed message",
                                           "mentions_neutralized": mentions})
    path = os.path.join(of_events.STATE_DIR, "injections.jsonl")
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a") as fh:
        fh.write(json.dumps(mark) + "\n")
    if term:
        ok, err = term.prompt(pane, text)
    else:
        r = subprocess.run(["herdr", "agent", "prompt", pane, text], capture_output=True, text=True, timeout=60)
        ok, err = r.returncode == 0, (r.stderr.strip()[:200] or None)
    if ok:
        note_typed(pane, text, mark)
    return ok, err


_SEEN = {}  # path -> (ids, bytes read so far, inode)
_SEEN_LOCK = threading.Lock()


def _seen_path():
    return os.path.join(of_events.STATE_DIR, "relay-seen.jsonl")


def seen(msg_id):
    """Was this message delivered or taken here already? An append-only file, never the store: a locked
    of.db must not turn one message into seven injections (a user organization, 2026-11-06). Every check reads the
    lines appended since the last one, so a take by `of inbox --mine` in another process reaches the
    running relay before its next offer (a user organization: a taken message was typed in, 2026-11-06)."""
    p = _seen_path()
    with _SEEN_LOCK:
        ids, done, ino = _SEEN.get(p, (set(), 0, None))
        try:
            st = os.stat(p)
            if st.st_ino != ino or st.st_size < done:  # a new or truncated file: read it again
                ids, done, ino = set(), 0, st.st_ino
            if st.st_size > done:
                with open(p, "rb") as fh:
                    fh.seek(done)
                    chunk = fh.read()
                end = chunk.rfind(b"\n") + 1  # complete lines only
                for ln in chunk[:end].splitlines():
                    try:
                        ids.add(json.loads(ln)["msg_id"])
                    except (ValueError, KeyError, TypeError):
                        continue
                done += end
        except OSError:
            pass
        _SEEN[p] = (ids, done, ino)
        return msg_id in ids


def mark_seen(msg_id, outcome, to=None):
    p = _seen_path()
    seen(msg_id)
    with _SEEN_LOCK:
        _SEEN[p][0].add(msg_id)
        try:
            with open(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a") as fh:
                fh.write(json.dumps({"msg_id": msg_id, "at": of_events.now_iso(), "outcome": outcome,
                                     **({"to": to} if to else {})}) + "\n")
        except OSError:
            pass


# Messages the relay holds for a live pane: being typed, or deferred until the agent is idle. They arrive by
# themselves, so `of status` says "arriving", not "waiting" (of_architect, 2026-11-06: agents spent a call on an
# empty take for a message that came as their next prompt). An entry lapses after ARRIVING_S.
ARRIVING_S = 120


def _arriving_path():
    return os.path.join(of_events.STATE_DIR, "relay-arriving.json")


def _read_arriving():
    try:
        with open(_arriving_path()) as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def set_arriving(key, msg_id, on, now=None):
    now = now or time.time()
    doc = {k: {i: t for i, t in (v or {}).items() if now - t < ARRIVING_S} for k, v in _read_arriving().items()}
    if on:
        doc.setdefault(key, {})[msg_id] = now
    else:
        (doc.get(key) or {}).pop(msg_id, None)
    doc = {k: v for k, v in doc.items() if v}
    try:
        tmp = _arriving_path() + f".{os.getpid()}"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(doc, fh)
        os.replace(tmp, _arriving_path())
    except OSError:
        pass


def arriving(key, now=None):
    now = now or time.time()
    return {i for i, t in (_read_arriving().get(key) or {}).items() if now - t < ARRIVING_S}


def seen_since(key, since):
    """Ids of messages to `key` delivered or taken here after `since` (epoch), from the seen file's tail."""
    try:
        with open(_seen_path(), "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - 256 * 1024))
            lines = fh.read().splitlines()
    except OSError:
        return set()
    out = set()
    for ln in lines:
        try:
            j = json.loads(ln)
        except ValueError:
            continue
        if j.get("to") == key and (_iso_ts(j.get("at")) or 0) >= since - 1:
            out.add(j.get("msg_id"))
    return out


CONFIRM_S = 45  # how long the relay waits for the session's hook to record a typed message


SPOOL_TAIL = 4 << 20  # how much of each spool file's end is read for a hook record


def spooled_hook_record(session, msg_id):
    """Is the hook's record of this message in the spool, not yet in the store? Under load the hook spools its events
    (of_store.spool_append) and the ingest worker moves them in minutes later (2026-11-11, of_platform: a message
    typed into a pane at 21:47:02Z reached the store at 21:52:22Z; the relay read it as lost and typed it three
    times). Only the hook's record carries the frame's text 'msg <id>'; the relay's own carries a msg_id field."""
    import glob
    d = os.path.join(of_events.STATE_DIR, "spool")
    paths = glob.glob(os.path.join(d, "events.jsonl")) + glob.glob(os.path.join(d, "events.jsonl.draining.*")) + \
        glob.glob(os.path.join(of_events.STATE_DIR, "events.jsonl"))
    needle, sess = f"msg {msg_id}".encode(), session.encode()
    for path in paths:
        for line in _spool_tail(path):
            if needle in line and sess in line and b"agent_message_in" in line:
                return True
    return False


_SPOOL_TAILS = {}  # path -> ((size, mtime), [lines]): one read per change, however many messages one cycle confirms


def _spool_tail(path):
    """The lines of the last SPOOL_TAIL bytes of a spool file, read again only when its size or time changed
    (of_architect's note on PR 33: every pending message read up to 4 MB of each file)."""
    try:
        st = os.stat(path)
    except OSError:
        _SPOOL_TAILS.pop(path, None)
        return []
    key = (st.st_size, st.st_mtime_ns)
    hit = _SPOOL_TAILS.get(path)
    if hit and hit[0] == key:
        return hit[1]
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, st.st_size - SPOOL_TAIL))
            lines = [ln for ln in fh.read().split(b"\n") if b"agent_message_in" in ln]
    except OSError:
        return []
    for old in [p for p in _SPOOL_TAILS if not os.path.exists(p)]:
        _SPOOL_TAILS.pop(old, None)
    _SPOOL_TAILS[path] = (key, lines)
    return lines


def hook_saw(conn, session, msg_id):
    """Did the receiving session's hook record this message (its frame carries 'msg <id>')? In the store, or still in
    the spool under load."""
    if not (session and msg_id):
        return False
    if conn is not None:
        try:
            if conn.execute("SELECT 1 FROM event WHERE name = 'of.agent_message_in' AND source = 'hook' AND "
                            "session_id = ? AND payload LIKE ? LIMIT 1", (session, f"%msg {msg_id}%")).fetchone():
                return True
        except Exception:  # noqa: BLE001 - a busy store: the spool below, else unconfirmed (re-offered)
            pass
    return spooled_hook_record(session, msg_id)


def hooked(conn, session):
    """Has this session's hook recorded anything? Only then can a missing record mean a lost message (a session
    without the hook would be offered the same message again and again)."""
    try:
        return conn.execute("SELECT 1 FROM event WHERE session_id = ? AND source = 'hook' LIMIT 1",
                            (session,)).fetchone() is not None
    except Exception:  # noqa: BLE001
        return False


def confirmed(conn, session, msg_id, wait_s=CONFIRM_S, step=2.0):
    """Wait until the hook records the message, up to wait_s. True when seen, or when the session has no hook."""
    if not hooked(conn, session):
        return True
    end = time.time() + wait_s
    while True:
        if hook_saw(conn, session, msg_id):
            return True
        if time.time() >= end:
            return False
        time.sleep(step)


def deliver(conn, m, local, deliverer=herdr_deliver):
    """local: {"<org>:<position>": {"kind", "pane", "session_id", "position"}}. Returns (status, reason).
    Delivery into a pane never depends on the store: once typed, the message is marked seen in a
    file, and the store's copy (relay_seen) is best effort. Only the inbox of a person needs the store;
    if it is busy this raises, and the caller defers the message."""
    import sqlite3
    if seen(m["msg_id"]):
        return "delivered", "duplicate"  # at-least-once from the server; once into the pane
    try:
        ensure_tables(conn)
        if conn.execute("SELECT 1 FROM relay_seen WHERE msg_id = ?", (m["msg_id"],)).fetchone():
            mark_seen(m["msg_id"], "duplicate")
            return "delivered", "duplicate"
    except sqlite3.Error:
        pass  # the file above is the guard; a busy store must not stop delivery
    target = local.get(m.get("to"))
    if target is None:
        outcome = ("failed", "no live holder on this machine")
    elif target.get("kind") == "human":
        conn.execute("INSERT OR IGNORE INTO inbox VALUES (?,?,?,?,?,?,?,NULL)",
                     (m["msg_id"], of_events.now_iso(), m["to"], m.get("from"), m.get("authority"),
                      m.get("reply_to"), m.get("text")))
        outcome = ("delivered", "inbox")
    elif hook_saw(conn, target.get("session_id"), m["msg_id"]):
        outcome = ("delivered", "confirmed late")  # typed before, recorded after the last wait: never typed twice
    else:
        set_arriving(m.get("to"), m["msg_id"], True)  # kept while deferred: it comes in when the agent is idle
        working_ok = message_age_s(m) >= WORKING_AFTER_S
        ok, why = deliverer(target["pane"], header(m), target.get("session_id"), m.get("from"),
                            working_ok=working_ok, skip_if=lambda: seen(m["msg_id"]))
        if ok is True and not working_ok and target.get("session_id") and deliverer is herdr_deliver \
                and not confirmed(conn, target["session_id"], m["msg_id"]):
            # typed, but the session never took it (a dialog swallowed it): offered again, never marked seen
            set_arriving(m.get("to"), m["msg_id"], False)
            return "deferred", "typed but not recorded by the session"
        if ok != "deferred":
            set_arriving(m.get("to"), m["msg_id"], False)
        if ok == "skipped":
            return "delivered", "duplicate"
        outcome = ("delivered", None) if ok is True else ("deferred", why) if ok == "deferred" else \
            ("failed", why or "herdr prompt failed")
    if outcome[0] == "deferred":  # back to the server's queue, offered again after retry_after
        return outcome
    if outcome[0] == "delivered":
        mark_seen(m["msg_id"], outcome[1] or "pane", to=m.get("to"))  # first: the pane has it now
        try:
            conn.execute("INSERT OR IGNORE INTO relay_seen VALUES (?,?,?)",
                         (m["msg_id"], of_events.now_iso(), outcome[1]))
        except sqlite3.Error:
            pass
    of_events.emit("agent_message_in", {"from": m.get("from"), "msg_id": m["msg_id"], "authority": m.get("authority"),
                                        "reply_to": m.get("reply_to"), "delivery": outcome[0], "via": "relay",
                                        "chars": len(m.get("text") or "")},
                   position=(target or {}).get("position"), session=(target or {}).get("session_id"))
    return outcome


# ------------------------------------------------------------------ the inbound queue (of inbox --mine)

def _queues_path():
    return os.path.join(of_events.STATE_DIR, "relay-queues.json")


def _emitted_path():
    return os.path.join(of_events.STATE_DIR, "relay-queues-emitted.json")


def read_queues():
    """{"<org>:<position>": {"n", "oldest", "at"}} as the relay last heard it from the servers."""
    try:
        with open(_queues_path()) as fh:
            return json.load(fh).get("queues") or {}
    except (OSError, ValueError, AttributeError):
        return {}


_LAST_QUEUES = {}


def read_unsent():
    """{"<org>:<position>": {"n", "items", "at"}}: what each local holder sent that the servers had not delivered."""
    try:
        with open(_queues_path()) as fh:
            return json.load(fh).get("unsent") or {}
    except (OSError, ValueError, AttributeError):
        return {}


def record_queues(queued, now=None, unsent=None):
    """Write the servers' queue counts for this machine's holders (the PUT /v1/presence answer) to a
    file the hooks and `of status` read without a network call, and declare each change as
    of.inbound_queue {position, org, depth, oldest_created_at} (the inbound-load measure). `unsent`: the
    answer's messages each holder sent that are not delivered yet, kept beside them for `of status`."""
    now = now or time.time()
    if not _LAST_QUEUES.get("_seeded"):  # after a restart, diff against what the last run emitted, so a
        # queue that emptied meanwhile still gets its step to 0 (of_studio). Its own file: of inbox --mine
        # corrects relay-queues.json for of status, and that must not hide a step from the measure.
        try:
            with open(_emitted_path()) as fh:
                _LAST_QUEUES.update({k: tuple(v) for k, v in json.load(fh).items()})
        except (OSError, ValueError, TypeError):
            pass
    out = {k: {"n": int((v or {}).get("n") or 0), "oldest": (v or {}).get("oldest") or (v or {}).get("oldest_created_at"),
               "at": now}
           for k, v in (queued or {}).items()}
    try:
        tmp = _queues_path() + f".{os.getpid()}"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump({"at": now, "queues": out,
                       "unsent": {k: dict(v, at=now) for k, v in (unsent or {}).items() if isinstance(v, dict)}}, fh)
        os.replace(tmp, _queues_path())
    except OSError:
        pass
    for key in (set(out) | set(_LAST_QUEUES)) - {"_seeded"}:
        cur = ((out.get(key) or {}).get("n", 0), (out.get(key) or {}).get("oldest"))
        if cur != _LAST_QUEUES.get(key, (0, None)):  # depth or the oldest message changed
            org, _, pos = key.partition(":")
            of_events.emit("inbound_queue", {"org": org, "position": pos, "depth": cur[0],
                                             "oldest_created_at": cur[1]}, position=pos)
    _LAST_QUEUES.clear()
    _LAST_QUEUES.update({k: (v["n"], v["oldest"]) for k, v in out.items()}, _seeded=True)
    try:
        tmp = _emitted_path() + f".{os.getpid()}"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump({k: v for k, v in _LAST_QUEUES.items() if k != "_seeded"}, fh)
        os.replace(tmp, _emitted_path())
    except OSError:
        pass
    return out


def set_queue(key, n, oldest, now=None):
    """Correct one position's count in relay-queues.json right after `of inbox --mine` heard the exact
    queue from the server, so `of status` does not repeat a stale count for up to 60 s (of_studio). No
    event: the relay declares changes from its own state."""
    try:
        with open(_queues_path()) as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        doc = {"at": now or time.time(), "queues": {}}
    qs = doc.setdefault("queues", {})
    if n:
        qs[key] = {"n": n, "oldest": oldest, "at": now or time.time()}
    else:
        qs.pop(key, None)
    try:
        tmp = _queues_path() + f".{os.getpid()}"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(doc, fh)
        os.replace(tmp, _queues_path())
    except OSError:
        pass


def queue_note(key, now=None, queues=None, unsent=None):
    """The relay's lines for `of status`: what waits for the position (inbound_note) and what it sent that has not
    landed (unsent_note), joined, or None."""
    lines = [inbound_note(key, now, queues), unsent_note(key, now, unsent)]
    return "\n".join(ln for ln in lines if ln) or None


# A sender sees a message that has not landed (2026-11-09, from a user organization: a project position's
# GO reached k8s_keeper 24 minutes after it was sent, after the step's start, and the founder's window was lost).
UNSENT_NOTE_S = 5 * 60
UNSENT_STALE_S = 24 * 3600  # past a day a note is no longer about timing; it would repeat at every turn end
UNSENT_SHOWN = 3            # receivers named; the rest are counted
BATCHED = "batched: the receiver's 15-minute window"  # an older relay client's reason (the window is gone)


def unsent_note(key, now=None, unsent=None):
    """'N message(s) you sent not delivered yet: 2 to X (oldest 9m; batched, next in 6m); …', per receiver, oldest
    first, for the messages between UNSENT_NOTE_S and UNSENT_STALE_S old that the servers had not delivered when the
    relay last heard (less those delivered here since), or None."""
    now = now or time.time()
    u = (unsent if unsent is not None else read_unsent()).get(key) or {}
    items = [i for i in u.get("items") or []
             if UNSENT_NOTE_S <= now - (_iso_ts(i.get("created_at")) or now) < UNSENT_STALE_S and not seen(i.get("msg_id"))]
    if not items:
        return None
    by_to = {}
    for i in items:
        by_to.setdefault(i.get("to"), []).append(i)
    parts = []
    for to, ms in list(by_to.items())[:UNSENT_SHOWN]:
        i = ms[0]  # the oldest
        why = ("no live holder" if not i.get("present") else "being typed now" if i.get("state") == "leased"
               else "batched" if i.get("reason") == BATCHED else (i.get("reason") or "queued")[:60])
        nxt = _iso_ts(i.get("next_at"))
        when = (f", next in {max(1, int((nxt - now + 59) // 60))}m"
                if nxt and nxt > now and why not in ("no live holder", "being typed now") else "")
        mins = int((now - _iso_ts(i.get("created_at"))) // 60)
        age = f"{'oldest ' if len(ms) > 1 else ''}{mins}m" if mins < 120 else f"{'oldest ' if len(ms) > 1 else ''}{mins // 60}h"
        parts.append(f"{len(ms)} to {to} ({age}; {why}{when})" if len(ms) > 1 else f"to {to} ({age}; {why}{when})")
    rest = sum(len(ms) for ms in list(by_to.values())[UNSENT_SHOWN:])
    n = len(items)
    more = int(u.get("n") or 0) > len(u.get("items") or []) and len(items) == len(u.get("items") or [])
    head = (f"{'at least ' if more else ''}{n} message{'s' if n != 1 else ''} you sent "
            f"{'are' if n != 1 else 'is'} not delivered yet: ")
    tail = f"; {rest} more to other receivers" if rest else ""
    return head + "; ".join(parts) + tail


def inbound_note(key, now=None, queues=None):
    """'N messages waiting for you (oldest Xm): of inbox --mine', or 'N message(s) arriving …', or None. The count is
    the server's as the relay last heard it, less the messages delivered or taken here since, less those the relay
    is typing or holds until the agent is idle (they arrive as the next prompt; taking them is a wasted call)."""
    now = now or time.time()
    q = (queues if queues is not None else read_queues()).get(key) or {}
    if not q.get("n"):
        return None
    coming = arriving(key, now)
    n = q["n"] - len(seen_since(key, q.get("at") or now) - coming) - len(coming)
    plural = lambda k: f"{k} message{'s' if k != 1 else ''}"
    tail = f"; {plural(len(coming))} arriving in your pane now" if coming and n > 0 else ""
    if n <= 0:
        return (f"{plural(len(coming))} arriving in your pane now (the relay types it when you are idle; "
                "no need to take it)") if coming else None
    age = now - (_iso_ts(q.get("oldest")) or now)
    return f"{plural(n)} waiting for you (oldest {int(age // 60)}m): of inbox --mine{tail}"


def _iso_ts(iso):
    import calendar
    try:
        return calendar.timegm(time.strptime(str(iso)[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return None


# What a listing showed to a session, so `--take all` takes only that (from a user organization, 2026-11-08:
# `of inbox --take all >/dev/null` took messages that arrived after the listing, and their text was lost; at
# least ~100 in two days). Kept 14 days, like the server's message TTL.
SHOWN_KEEP_S = 14 * 86400
TAKEN_KEEP_S = 14 * 86400
NEW_SINCE_LISTING = "new since your last listing: shown here, not taken"


def _shown_path():
    return os.path.join(of_events.STATE_DIR, "relay-shown.json")


def _taken_path():
    return os.path.join(of_events.STATE_DIR, "relay-taken.jsonl")


def _shown_load():
    try:
        with open(_shown_path()) as fh:
            return json.load(fh) or {}
    except (OSError, ValueError):
        return {}


def mark_shown(key, session, msg_ids, now=None):
    """Record that a listing showed these messages to this session of this position."""
    now = now or time.time()
    data = _shown_load()
    slot = data.setdefault(f"{key}|{session}", {})
    for i in msg_ids:
        slot.setdefault(i, now)
    for k in list(data):
        data[k] = {i: t for i, t in data[k].items() if now - t < SHOWN_KEEP_S}
        if not data[k]:
            del data[k]
    tmp = _shown_path() + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, _shown_path())


def shown(key, session):
    return set(_shown_load().get(f"{key}|{session}") or {})


def keep_taken(key, session, m, now=None):
    """The taken message's fields as the server gave them (text already masked there), so `of inbox --mine
    --taken` can show it again: a take never loses a text, whatever happened to its output."""
    now = now or time.time()
    rec = {"taken_at": now, "to": key, "session": session,
           "message": {k: v for k, v in m.items() if not k.startswith("_")}}
    path = _taken_path()
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def taken_messages(key, since_s=TAKEN_KEEP_S, now=None):
    """[(taken_at, message)] taken here for `key` in the last `since_s` seconds, oldest first."""
    now = now or time.time()
    out = []
    try:
        with open(_taken_path()) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("to") == key and now - float(r.get("taken_at") or 0) < since_s:
                    out.append((r["taken_at"], r["message"]))
    except OSError:
        pass
    return out


def pull(client, key, session, take=None, pane_busy=False):
    """Messages queued for `key` (this caller's own position). take: None (list only), "all" or a
    msg id prefix. A plain listing records what it showed to this session; "all" takes only those, and a
    message that arrived after the listing is returned untaken with `_note` NEW_SINCE_LISTING (it stays
    queued: the output of a take may be discarded). An id prefix takes what it names. A taken message is
    taken on the server (delivered, so never offered again), kept locally (`taken_messages`), marked seen
    and declared as of.agent_message_in {delivery: pulled}. One under a live relay lease (409) is not
    taken: it is arriving in the pane. Returns [(message, taken: bool)]."""
    msgs = (client.queued_messages(key, session) or {}).get("messages") or []
    if take is None:
        mark_shown(key, session, [m["msg_id"] for m in msgs])
    listed = shown(key, session) if take == "all" else set()
    out = []
    for m in msgs:
        if take == "all" and m["msg_id"] not in listed and not seen(m["msg_id"]):
            out.append((dict(m, _new=NEW_SINCE_LISTING), False))
            continue
        want = take == "all" or (take and m["msg_id"].startswith(take))
        if seen(m["msg_id"]):  # taken here already (a busy pane under a lease): the server learns it from the
            # relay's next ack; never list it twice (2026-11-06). Retry the server's take quietly.
            if want:
                try:
                    client.take_message(m["msg_id"], key, session)
                except Exception:  # noqa: BLE001
                    pass
            continue
        if want:
            try:
                client.take_message(m["msg_id"], key, session)
            except Exception as e:  # noqa: BLE001
                if getattr(e, "status", None) != 409:
                    raise
                if not pane_busy:
                    # 409 leased and the pane is idle: the relay types it in within seconds (the
                    # no-double-delivery rule): not taken here, the agent acts on it there.
                    out.append((dict(m, _note="arriving in your pane now"), False))
                    continue
                # 409 leased but the pane is busy: the relay would only defer it. Taken here: the seen mark
                # makes the relay skip it and ack it delivered on its next offer (of_architect, 2026-11-06).
            keep_taken(key, session, m)
            mark_seen(m["msg_id"], "pulled", to=key)  # the relay re-checks this right before typing
            of_events.emit("agent_message_in", {"from": m.get("from"), "msg_id": m["msg_id"],
                                                "authority": m.get("authority"), "reply_to": m.get("reply_to"),
                                                "delivery": "pulled",
                                                "via": "relay", "chars": len(m.get("text") or "")},
                           position=key.partition(":")[2], session=session)
        out.append((m, bool(want)))
    left = [m for m, taken in out if not taken]
    set_queue(key, len(left), min((m.get("created_at") for m in left if m.get("created_at")), default=None))
    return out


def local_holders(positions, bind, live_agents):
    """{"<org>:<position>": {...}} for this machine: bound agents, plus positions held by people here."""
    out = {}
    pairs, _, _ = bind(positions, live_agents)
    for p, a in pairs:
        out[f"{p.get('_org')}:{p['name']}"] = {
            "org": p.get("_org"), "position": p["name"], "kind": "agent", "pane": a["pane_id"],
            "session_id": (a.get("agent_session") or {}).get("value"), "state": a.get("agent_status")}
    for p in positions:
        h = p.get("holder") or {}
        if h.get("runtime") == "human" and h.get("machine") == of_events.machine_id():
            out[f"{p.get('_org')}:{p['name']}"] = {"org": p.get("_org"), "position": p["name"], "kind": "human",
                                                    "pane": None, "session_id": None, "state": "human"}
    return out


def holders_key(local):
    """Who holds what, without the busy state: a change here publishes presence at once."""
    return sorted((k, h.get("session_id") or "", h.get("pane") or "", h.get("kind") or "") for k, h in local.items())


def presence_bodies(local, servers, default, drains=None):
    """{server: [holder]}: each organization's server gets only that organization's holders. After a cutover, an
    organization's old server (orgs.yaml `drain_from`, see org_drains) gets them too, so the messages still queued
    there are leased and delivered here instead of waiting for a presence that never comes."""
    out = {}
    for h in local.values():
        body = {k: h.get(k) for k in ("org", "position", "session_id", "state", "pane", "kind")}
        main = servers.get(h["org"], default)
        out.setdefault(main, []).append(body)
        for url in (drains or {}).get(h["org"]) or []:
            if url != main:
                out.setdefault(url, []).append(body)
    return out


def org_drains(default, orgs_file=None):
    """{org: [old server url]} from orgs.yaml `drain_from:` (a URL, `local` for this machine's own server, or a list).
    Set at a cutover until the old server holds nothing for the organization, then removed; re-read every presence
    round, so removing it takes effect without a restart."""
    import yaml
    orgs_file = orgs_file or os.environ.get("OF_ORGS") or of_events.config_path("orgs.yaml")
    try:
        with open(orgs_file) as fh:
            doc = yaml.safe_load(fh) or {}
    except (OSError, ValueError):
        return {}
    out = {}
    for o in doc.get("orgs") or []:
        d = o.get("drain_from") if isinstance(o, dict) else None
        urls = d if isinstance(d, list) else [d] if d else []
        urls = [default if u == "local" else str(u).rstrip("/") for u in urls if isinstance(u, str) and u]
        if urls and o.get("name"):
            out[o["name"]] = urls
    return out



def inbox_servers(org, main, default, orgs_file=None):
    """The servers `of inbox --mine` reads for ORG: its own, then the old servers its orgs.yaml `drain_from` names. An
    --fyi message still queued on the old server after a cutover is never typed in, so without this it would stay
    there and the old queue would never empty (2026-11-12, found while checking a user organization's cutover steps)."""
    urls = [str(main).rstrip("/")]
    for u in org_drains(default, orgs_file).get(org) or []:
        if u not in urls:
            urls.append(u)
    return urls


# No window (the founder, 2026-11-11: "remove the 15-minute window entirely"). The relay types whatever is queued
# for a receiver as soon as it is idle, joined into one prompt (deliver_joined); herdr_deliver never types into a
# blocked pane. fyi is never typed: the receiver reads it with of inbox --mine, and `of status` counts it. From
# 2026-11-07 to 2026-11-11 a receiver got at most one prompt per 15 minutes (docs/guard.md R9).
FYI_RETRY_S = 600  # the server's longest back-off: an fyi message is offered again and deferred again


def plan_batch(msgs, local):
    """[(action, [messages], retry_after)] for one poll's messages: 'fyi' (defer, never typed), 'deliver' (one joined
    prompt per receiver, typed as soon as it is idle), 'single' (a person's inbox or no local holder: one by one)."""
    by_to, out = {}, []
    for m in msgs:
        target = local.get(m.get("to"))
        if target is None or target.get("kind") == "human":
            out.append(("single", [m], None))  # a person's inbox takes every message, fyi included
        elif m.get("priority") == "fyi":  # of-server never offers fyi to an agent (dbc07bd): an older server's only
            out.append(("fyi", [m], FYI_RETRY_S))
        else:
            by_to.setdefault(m.get("to"), []).append(m)
    out.extend(("deliver", ms, None) for ms in by_to.values())
    return out


def deliver_joined(conn, msgs, local, deliverer=herdr_deliver):
    """One prompt for several messages to the same receiver (each in its own frame). Returns (status, reason)."""
    if len(msgs) == 1:
        return deliver(conn, msgs[0], local, deliverer)
    target = local.get(msgs[0].get("to"))
    to = msgs[0].get("to")
    for m in msgs:  # typed earlier, recorded after the last wait: delivered, never typed twice
        if not seen(m["msg_id"]) and hook_saw(conn, target.get("session_id"), m["msg_id"]):
            mark_seen(m["msg_id"], "confirmed late", to=to)
    fresh = [m for m in msgs if not seen(m["msg_id"])]
    if not fresh:
        return "delivered", "duplicate"
    set_arriving(to, fresh[0]["msg_id"], True)
    text = "\n\n".join(header(m) for m in fresh)
    working_ok = any(message_age_s(m) >= WORKING_AFTER_S for m in fresh)
    ok, why = deliverer(target["pane"], text, target.get("session_id"), fresh[0].get("from"),
                        working_ok=working_ok, skip_if=lambda: all(seen(m["msg_id"]) for m in fresh))
    if ok is True and not working_ok and target.get("session_id") and deliverer is herdr_deliver \
            and not confirmed(conn, target["session_id"], fresh[0]["msg_id"]):
        set_arriving(to, fresh[0]["msg_id"], False)
        return "deferred", "typed but not recorded by the session"
    if ok != "deferred":
        set_arriving(to, fresh[0]["msg_id"], False)
    if ok == "skipped":
        return "delivered", "duplicate"
    if ok == "deferred":
        return "deferred", why
    if ok is not True:
        return "failed", why or "herdr prompt failed"
    for m in fresh:
        mark_seen(m["msg_id"], "pane", to=to)
        of_events.emit("agent_message_in", {"from": m.get("from"), "msg_id": m["msg_id"], "authority": m.get("authority"),
                                            "reply_to": m.get("reply_to"), "delivery": "delivered", "via": "relay",
                                            "batched": len(fresh), "chars": len(m.get("text") or "")},
                       position=target.get("position"), session=target.get("session_id"))
    return "delivered", None


# ------------------------------------------------------------ coordination across machines

COORD_EVERY = 10  # seconds between reads of a server's coordination feed when the last read brought nothing
COORD_LOG_EVERY = 3600  # one log line per organization and error kind an hour


def _coord_cursor_path():
    return os.path.join(of_events.STATE_DIR, "coord-cursor.json")


def read_coord_cursor():
    """{"<server url>|<org>": seq}: how far this machine has read each organization's coordination feed."""
    try:
        with open(_coord_cursor_path()) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_coord_cursor(cursor):
    try:
        tmp = _coord_cursor_path() + f".{os.getpid()}"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(cursor, fh)
        os.replace(tmp, _coord_cursor_path())
    except OSError:
        pass


def pull_coord(client, url, org, conn, cursor, me):
    """Read one organization's coordination feed (docs/server-several-machines.md §4.2) after this machine's
    cursor and store the events other machines wrote, as they are (masked by the server), with raw_ref
    `coord:<org>:<seq>` so `of ship` never sends them back. The store skips an event id it already holds.
    Moves `cursor` only past what was stored. Returns how many events came from other machines."""
    import of_coord
    key = f"{url}|{org}"
    after = int(cursor.get(key) or 0)
    r = client.coord(org, after)
    if after > int(r.get("last") or 0):  # the server's feed started over: read it again from the start
        cursor[key] = 0
        return 0
    n = 0
    for e in r.get("events") or []:
        rec = e.get("record")
        if e.get("host_id") != me and isinstance(rec, dict) and rec.get("of.event.id"):
            of_store.add_event(conn, rec, raw_ref=f"{of_coord.RAW_REF}{org}:{e.get('seq')}")
            n += 1
    cursor[key] = int(r.get("next") or after)
    return n


# ------------------------------------------------------------ the watchdog (docs/health.md §5)

RELAY_LABEL = "dev.organic-framework.relay"
RELAY_WATCHDOG_LABEL = "dev.organic-framework.relay-watchdog"
HEARTBEAT = "relay-heartbeat.json"
# a loop not seen moving this long: above the longest bounded call, a request to a server that accepts and never
# answers (of_client: 4 tries of 120 s and the back-off), so a hung server is not taken for a stuck relay (of_review
# on organic-framework PR 142)
STALE_S = 900


def heartbeat_path():
    return os.path.join(of_events.STATE_DIR, HEARTBEAT)


def write_heartbeat(ticks, now=None):
    """{ts, pid, loops: {loop: last time it ran}}, written by the presence loop every PRESENCE_CHECK seconds."""
    path = heartbeat_path()
    try:
        with open(path + ".tmp", "w") as fh:
            json.dump({"ts": time.time() if now is None else now, "pid": os.getpid(), "loops": dict(ticks)}, fh)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def clear_heartbeat():
    """A relay that stopped leaves no heartbeat naming its pid (`of relay off`, the end of `of relay run`)."""
    try:
        os.remove(heartbeat_path())
    except OSError:
        pass


def launchd_pid(label, domain=None, run=subprocess.run):
    """The pid launchd has for LABEL in DOMAIN (gui/<uid> by default), else None: a relay booted out has none."""
    try:
        r = run(["launchctl", "print", f"{domain or 'gui/' + str(os.getuid())}/{label}"], capture_output=True,
                text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"^\s*pid = (\d+)", r.stdout or "", re.M) if r.returncode == 0 else None
    return int(m.group(1)) if m else None


def relay_healthy(now=None, stale=STALE_S):
    """(ok, pid, detail): the relay's heartbeat is fresh and every loop ran within `stale` seconds."""
    now = time.time() if now is None else now
    try:
        with open(heartbeat_path()) as fh:
            hb = json.load(fh)
    except (OSError, ValueError):
        return False, None, "no heartbeat"
    late = sorted(k for k, t in (hb.get("loops") or {}).items() if now - t > stale)
    if now - hb.get("ts", 0) > stale:
        return False, hb.get("pid"), f"heartbeat {int(now - hb.get('ts', 0))} s old"
    return (not late), hb.get("pid"), ("loops behind: " + ", ".join(late)) if late else "ok"


def watchdog(now=None, restart=None, dump=None, log=None, installed=None, pid_of=None):
    """One check, run by launchd every minute (`of relay watchdog`): a relay process alive whose loops stopped moving
    for 3 minutes of checks gets its threads' stacks written to relay.log (SIGUSR1), then a restart, at most once in 15
    minutes (of_server.watch; docs/health.md §5; of_architect's hand-over 154a0cbf)."""
    import of_server
    plist = os.path.expanduser(f"~/Library/LaunchAgents/{RELAY_LABEL}.plist")
    # the pid is launchd's, never the heartbeat's: after `of relay off` that pid may belong to any process (of_review
    # on organic-framework PR 142: a stale heartbeat had a sleep signalled)
    return of_server.watch("the relay", RELAY_LABEL, lambda: relay_healthy(now)[0],
                           pid_of or (lambda: launchd_pid(RELAY_LABEL)),
                           os.path.join(of_events.STATE_DIR, "relay-watchdog.json"), now=now, restart=restart,
                           dump=dump, log=log, installed=installed or (lambda: os.path.exists(plist)),
                           event="relay_watchdog", silent="has not moved its loops")


def install_launchd(uninstall=False, run=None, of_bin=None, watchdog_only=False):
    """`of relay install|uninstall`: the relay's launchd agent at Standard priority (of_init.UNITS) and its watchdog,
    one check a minute. `watchdog_only`: the watchdog alone, for `of init`, which installs the relay itself."""
    import subprocess
    import sys
    import of_init
    import of_server
    if sys.platform != "darwin":
        raise SystemExit("of relay install: launchd only (a Linux host runs the relay under systemd)")
    unit = next(u for u in of_init.UNITS if u["name"] == "relay")
    log = os.path.join(of_events.STATE_DIR, "relay.log")
    of_bin = of_bin or of_init.OF
    plists = {RELAY_LABEL: of_init.unit_text(unit, "Darwin", of=of_bin, log=log),
              RELAY_WATCHDOG_LABEL: of_server._plist(
                  RELAY_WATCHDOG_LABEL, [sys.executable, of_bin, "relay", "watchdog"],
                  os.path.join(of_events.STATE_DIR, "relay-watchdog.log"),
                  f"  <key>StartInterval</key><integer>{of_server.WATCHDOG['every']}</integer>\n")}
    if watchdog_only:
        plists.pop(RELAY_LABEL)
    return of_server.load_launchd(plists, uninstall=uninstall, run=run or subprocess.run)


def run(load, client_for, servers, default, log=print, stop=None, deliverer=herdr_deliver):
    """load() -> local holders; client_for(url) -> Client. Runs until `stop` is set."""
    stop = stop or threading.Event()
    conn = of_store.connect()
    ensure_tables(conn)
    try:
        state = {"local": load()}
    except Exception as e:  # noqa: BLE001 - start anyway; presence retries every PRESENCE_EVERY
        log(f"presence: no holders yet: {type(e).__name__}: {str(e)[:200]}")
        state = {"local": {}}

    ticks = {}

    def presence_loop():
        sent_key, sent_at = None, 0.0
        while not stop.is_set():
            ticks["presence"] = time.time()
            write_heartbeat(ticks)  # the watchdog's probe: every loop seen moving (docs/health.md §5)
            try:
                state["local"] = load()
            except Exception as e:  # a registry mid-edit (YAML error): keep the last good holders
                log(f"presence: keeping the last good holders: {type(e).__name__}: {str(e)[:200]}")
            key = holders_key(state["local"])
            if key != sent_key or time.time() - sent_at >= PRESENCE_EVERY:
                try:
                    try:
                        drains = org_drains(default)
                    except Exception:  # noqa: BLE001 - a broken orgs.yaml drains nothing; presence goes on
                        drains = {}
                    bodies = presence_bodies(state["local"], servers, default, drains)
                    queued, unsent = {}, {}
                    for url in set(servers.values()) | {default} | set(bodies):
                        ticks["presence"] = time.time()  # per server: two hung servers outlast STALE_S together
                        write_heartbeat(ticks)
                        r = client_for(url).put_presence(bodies.get(url, []))
                        if isinstance(r, dict):
                            queued.update(r.get("queued") or {})
                            unsent.update(r.get("unsent") or {})
                    record_queues(queued, unsent=unsent)
                    sent_key, sent_at = key, time.time()
                except Exception as e:
                    log(f"presence: {type(e).__name__}: {e}")
            stop.wait(min(PRESENCE_CHECK, PRESENCE_EVERY))

    def poll_loop(url):
        c = client_for(url)
        own = of_store.connect()
        while not stop.is_set():
            ticks[f"poll {url}"] = time.time()
            try:
                msgs = c.poll_messages(30).get("messages") or []
                for action, ms, retry in plan_batch(msgs, state["local"]):
                    if action == "fyi":
                        status, reason = "deferred", "fyi: read with of inbox --mine"
                    else:
                        try:
                            status, reason = (deliver(own, ms[0], state["local"], deliverer) if action == "single"
                                              else deliver_joined(own, ms, state["local"], deliverer))
                        except Exception as e:  # noqa: BLE001 - one message never holds the batch or its lease
                            status, reason = "deferred", f"local store busy: {type(e).__name__}"[:200]
                            log(f"deliver {ms[0].get('msg_id')}: {type(e).__name__}: {e}")
                        retry = DEFER_S
                    for m in ms:
                        try:
                            c.ack(m["msg_id"], status, reason, retry_after=retry if status == "deferred" else None)
                        except Exception as e:  # 409: leased to another machine meanwhile; its outcome stands
                            log(f"ack {m['msg_id']}: {e}")
            except Exception as e:
                log(f"poll {url}: {type(e).__name__}: {e}")
                stop.wait(5)

    def coord_loop(url, orgs):
        c = client_for(url)
        own = of_store.connect()
        me = of_events.machine_id()
        logged = {}
        while not stop.is_set():
            ticks[f"coord {url}"] = time.time()
            cursor, got = read_coord_cursor(), 0
            before = dict(cursor)
            for org in orgs:
                # per organization: each pull is bounded (of_client's tries), but two of them on a hung server
                # take longer than STALE_S together (of_review on organic-framework PR 142)
                ticks[f"coord {url}"] = time.time()
                try:
                    got += pull_coord(c, url, org, own, cursor, me)
                except Exception as e:  # noqa: BLE001 - 403 (not joined there), a busy store, a restart
                    k = (org, type(e).__name__, getattr(e, "status", None))
                    if time.time() - logged.get(k, 0) >= COORD_LOG_EVERY:
                        logged[k] = time.time()
                        log(f"coord {org} at {url}: {type(e).__name__}: {str(e)[:200]}")
            if cursor != before:
                write_coord_cursor(cursor)
            stop.wait(0.2 if got else COORD_EVERY)

    try:
        drained = {u for us in org_drains(default).values() for u in us}
    except Exception:  # noqa: BLE001
        drained = set()
    urls = set(servers.values()) | {default} | drained
    threads = [threading.Thread(target=presence_loop, daemon=True)]
    threads += [threading.Thread(target=poll_loop, args=(u,), daemon=True) for u in urls]
    threads += [threading.Thread(target=coord_loop, args=(u, sorted(o for o, s in servers.items() if s == u)),
                                 daemon=True) for u in urls if any(s == u for s in servers.values())]
    for t in threads:
        t.start()
    return stop, threads
