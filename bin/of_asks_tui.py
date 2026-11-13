"""`of asks --watch`: the owner's live list of questions, for a herdr popup (prefix+a).

Order (founder, 2026-11-06): blocking questions and those due within 2 h first, soonest due first, with a
countdown (`of ask --due`); then the rest, oldest first. Below them, without counters or notifications: questions an
agent asked in its own pane without `of ask`, confirmed by the judge (bin/of_judge.py) and still open after
of_judge.LIST_AFTER_MIN (founder, ask ddd6e3a3, 2026-11-07); then the registry-declared waits (a position's
`waiting_on: owner`).

The true list first: the first frame comes from the cache, the list from the listener's view replaces it
within milliseconds, and only then the reconcile with live sessions and the herdr counts runs, in the
background. Refresh by signal: views.seq is polled every 250 ms; a full reload every 5 s is the safety net.

List: ↑/↓ or j/k select (kept by ask id across refreshes), Enter open the question card, g go to
the asking agent, a mark answered, w withdraw, r refresh, d send unsent drafts, q quit. Card: the full
question and the agent's full last message from the transcript, wrapped and scrolled (↑/↓, PgUp/PgDn,
Home/End), its options, who asks and how long it waits, links; 1..9 pick an option (Enter sends), r a
free answer, b a side chat through a read-only fork of the session (bin/of_side_chat.py), g go, Esc back.

Answers are optimistic: the item leaves the list at once and the next question opens; the bottom line
says "sent ✓ · u to undo" (in the person's language, bin/of_locale.py) for 5 s, and the answer is sent only after that (u takes it back). A
failed send brings the item back marked red with the reason; q sends whatever is pending, never drops it.
Keys typed meanwhile are flushed, so nothing leaks into the next card. Answers: bin/of_asks_answer.py.
"""

import curses
import json
import re
import os
import subprocess
import sys
import textwrap
import time

from of_locale import tr

REFRESH_S = 5       # the full reload: a safety net; the listener's views.seq signal drives refreshes
SEQ_POLL_MS = 250   # how often the popup looks at views.seq (one stat)
UNDO_S = 5          # an answer waits this long for u (undo) before it is sent
URGENT_S = 2 * 3600  # a question due within this goes first


def _ts(iso):
    import calendar
    try:
        return calendar.timegm(time.strptime(str(iso)[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return None


def order(items, now=None):
    """The founder's order (2026-11-06): blocking or due within 2 h first, soonest due first; then the rest,
    oldest first; questions asked in a pane (heuristic, judged) last, in their own section.
    Each item gets its section: task, urgent, normal or panes."""
    now = now or time.time()
    for x in items:
        due = _ts(x.get("due"))
        if x.get("task"):
            x["section"] = "task"  # work for the owner's own hands: on top, above every question
        elif "blocking" in (x.get("kind") or "") or (due is not None and due - now <= URGENT_S):
            x["section"] = "urgent"
        elif (x.get("kind") or "").startswith("question (heuristic)"):
            x["section"] = "panes"  # asked in the pane, no of ask: below, no counter, no notifications
        else:
            x["section"] = "normal"
    rank = {"task": -1, "urgent": 0, "normal": 1, "panes": 2}
    return sorted(items, key=lambda x: (rank[x["section"]],
                                        (_ts(x.get("due")) or _ts(x.get("since")) or 0) if x["section"] in ("urgent", "task")
                                        else (_ts(x.get("since")) or 0)))


def countdown(due, now=None):
    """'in 38 min' / 'in 2 h 05 min' / 'overdue 12 min', in the person's language."""
    t = _ts(due)
    if t is None:
        return ""
    d = int(t - (now or time.time()))
    m = abs(d) // 60
    span = tr("{h} h {m:02d} min", h=m // 60, m=m % 60) if m >= 60 else tr("{m} min", m=m)
    return tr("in {span}", span=span) if d >= 0 else tr("overdue {span}", span=span)


def load(positions, conn, from_log=False):
    """[(group label, [item])] for the screen. Items carry 'who', 'text', 'since', 'ids', 'kind', 'first'.
    from_log: project from the event log even when the listener's view is fresh."""
    import of_asks
    import of_listen
    if not from_log and of_listen.fresh(conn):  # the listener keeps the queue: read it, no recomputation
        groups = of_listen.read_groups(conn)
    else:
        groups = of_asks.group(of_asks.for_owner(of_asks.project(of_asks.load(conn))))
    return shape(groups, positions)


def shape(groups, positions):
    """The screen's [(group label, [item])] from the queue's groups (of_asks.group's shape) and the registry."""
    import of_judge
    shown_after = time.time() - of_judge.LIST_AFTER_MIN * 60  # the agent had its correction first
    groups = [g for g in groups if not (g.get("kind") or "").startswith("question (heuristic)")
              or (_ts(g.get("since")) or 0) <= shown_after]
    asked = order([{"who": g["who"], "text": g["text"], "since": g["since"], "ids": g["ask_ids"],
                    "kind": "permission" if g["kind"] == "permission" else g["kind"], "first": g.get("first"),
                    "due": (g.get("first") or {}).get("due"), "n_options": (g.get("first") or {}).get("n_options"),
                    "task": bool((g.get("first") or {}).get("task")), "steps": (g.get("first") or {}).get("steps"),
                    "command": (g.get("first") or {}).get("command"), "claimed_at": (g.get("first") or {}).get("claimed_at"),
                    "reopened": (g.get("first") or {}).get("reopened")}
                   for g in groups])
    declared_who = {w for g in groups for w in g["who"]}
    registry = [{"who": [p["name"]], "text": p.get("note") or p.get("summary") or "", "since": None, "ids": [],
                 "kind": "registry", "first": None}
                for p in positions if p.get("waiting_on") == "owner" and p["name"] not in declared_who]
    return [("Waiting on you", asked), ("Registry-declared (no notifications)", registry)]


def cache_path():
    import of_events
    return os.path.join(of_events.STATE_DIR, "asks-cache.json")


def read_cache(with_time=False):
    """The list as the last run saw it: the first frame is drawn from it at once."""
    data, at = [("Waiting on you", []), ("Registry-declared (no notifications)", [])], None
    try:
        with open(cache_path()) as fh:
            doc = json.load(fh)
        data, at = [(g[0], g[1]) for g in doc["data"]], doc.get("at")
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return (data, at) if with_time else data


def waiting(data):
    return len(data[0][1]) if data else 0


def checked(data, cached, reproject):
    """A list that drops to empty while the cache had asks is checked against the event log before it
    is believed: a half-built view must never show the founder "0 waiting" (2026-11-06)."""
    if waiting(data) == 0 and waiting(cached) > 0:
        return reproject()
    return data


def write_cache(data):
    try:
        tmp = cache_path() + f".{os.getpid()}"
        with open(tmp, "w") as fh:
            json.dump({"data": data, "at": time.time()}, fh, ensure_ascii=False)
        os.replace(tmp, cache_path())
    except OSError:
        pass


def _age(since):
    import of_asks
    return of_asks.age(since) if since else ""


def _put(win, y, x, text, attr=0):
    h, w = win.getmaxyx()
    if 0 <= y < h - 1:
        try:
            win.addnstr(y, x, text, max(0, w - 1 - x), attr)
        except curses.error:
            pass


def _wrap(text, width):
    out = []
    for line in (text or "").splitlines() or [""]:
        out += textwrap.wrap(line, width) or [""]
    return out


def dwidth(text):
    """Display width: East Asian wide and full-width characters take two cells, combining marks none."""
    import unicodedata
    return sum(0 if unicodedata.combining(ch) else (2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1)
               for ch in text)


def cut(text, width):
    """The first line of text, cut to `width` display cells."""
    line, out, n = (text or "").split("\n")[0], [], 0
    for ch in line:
        n += dwidth(ch)
        if n > width:
            break
        out.append(ch)
    return "".join(out)


def wrap_rows(lines, width):
    """Display rows for the input: each logical line wrapped by words to `width` (a long word is broken)."""
    rows = []
    for ln in lines:
        parts = textwrap.wrap(ln, width=max(10, width), break_long_words=True, drop_whitespace=False,
                              replace_whitespace=False) if ln else [""]
        rows += parts or [""]
    return rows


def edit(stdscr, title, initial=None):
    """A small multi-line editor in a fixed layout (founder, 2026-11-06: a task's text ran over the input): the
    whole window is cleared; row 0 the header, one line cut to the width; row 1 the keys; row 2 empty; the input
    from row 3, wrapped by words with its own scrolling. Returns the text, or None on Esc. Enter on an empty line
    or Ctrl+D sends."""
    lines = (initial or "").split("\n")
    cur = lines[-1]
    curses.curs_set(1)
    stdscr.timeout(-1)
    stdscr.clear()  # nothing from the card stays on screen
    try:
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            _put(stdscr, 0, 0, cut(title, w - 1), curses.A_BOLD)
            _put(stdscr, 1, 0, cut("Enter: new line · Enter on an empty line or Ctrl+D: send · Esc: cancel", w - 1),
                 curses.A_DIM)
            rows = wrap_rows(lines[:-1] + [cur], w - 4)
            room = max(1, h - 4)  # rows 3 .. h-2 (curses never writes the last row)
            shown = rows[-room:]
            for k, ln in enumerate(shown):
                _put(stdscr, 3 + k, 2, ln)
            last = shown[-1] if shown else ""
            y, x = 3 + len(shown) - 1, 2 + dwidth(last)
            if x >= w - 1:
                y, x = min(y + 1, h - 2), 2
            try:
                stdscr.move(min(y, h - 2), min(x, w - 2))
            except curses.error:
                pass
            stdscr.refresh()
            ch = stdscr.get_wch()
            if ch in ("\x1b",):
                return None
            if ch in ("\x04",):  # Ctrl+D
                lines[-1] = cur
                break
            if ch in ("\n", "\r", curses.KEY_ENTER):
                if cur == "" and any(x_.strip() for x_ in lines[:-1]):
                    break
                lines[-1] = cur
                lines.append("")
                cur = ""
            elif ch in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                if cur:
                    cur = cur[:-1]
                elif len(lines) > 1:
                    lines.pop()
                    cur = lines[-1]
            elif ch == curses.KEY_RESIZE:
                stdscr.clear()
            elif isinstance(ch, str) and ch.isprintable():
                cur += ch
        text = "\n".join(x_ for x_ in lines).strip()
        return text or None
    finally:
        curses.curs_set(0)
        stdscr.timeout(SEQ_POLL_MS)


def copy_text(text):
    """Copy to the clipboard: OSC 52 through the terminal when over SSH (the phone), else pbcopy. Returns how."""
    import base64
    if os.environ.get("SSH_TTY"):
        seq = "\033]52;c;" + base64.b64encode(text.encode()).decode() + "\a"
        with open("/dev/tty", "w") as tty:
            tty.write(seq)
            tty.flush()
        return "OSC 52"
    subprocess.run(["pbcopy"], input=text.encode(), timeout=5, check=True)
    return "pbcopy"


def copy_target(c, item):
    """c: the task's command (else its steps), or a question's recommended option (else its first)."""
    if item.get("task"):
        return item.get("command") or item.get("steps") or item.get("text") or ""
    opts = c.get("options") or []
    import of_ask_text
    rec = next((o for o in opts if of_ask_text.RECOMMENDED.search(o)), None)
    return rec or (opts[0] if opts else c.get("question") or "")


def card_lines(c, width):
    """The whole card as (text, attr kind) lines, wrapped by words: nothing is cut; the card scrolls."""
    out = []
    who = ", ".join(c["who"]) or c["position"] or "?"
    out.append((f"{who}" + (f" · {c['system']}" if c.get("system") else "") +
                (f" · waits {_age(c['since'])}" if c.get("since") else "") + f" · {c.get('kind') or ''}", "head"))
    out.append(("", None))
    doing = c.get("doing") or {}
    if doing.get("mandate") or doing.get("turns"):
        out.append(("What the agent is doing:", "under"))
        if doing.get("mandate"):
            out += [(ln, "dim") for ln in _wrap(f"mandate: {doing['mandate']}" + (f" ({doing['brief']})" if doing.get("brief") else ""), width)]
        for t in doing.get("turns") or []:
            out += [(ln, "dim") for ln in _wrap(f"· {t}", width)]
        out.append(("", None))
    out += [(ln, "question") for ln in _wrap(c["question"], width)]
    if c["options"]:
        out.append(("", None))
        for i, o in enumerate(c["options"][:9], 1):
            out += [(ln, f"option{i}") for ln in _wrap(f"{i}. {o}", width)]
    if c["kind"] == "registry":
        out += [("", None), ("Answering here becomes available after the move to herdr.", "dim")]
    elif c["kind"] == "permission" and not c["options"]:
        out += [("", None), ("A permission dialog: answer it in the session (g).", "dim")]
    elif (c["kind"] or "").startswith("question (heuristic)"):
        out += [("", None)] + [(ln, "dim") for ln in _wrap(
            "Asked in its pane without of ask; the line above is the judge's reading (Haiku). An answer here is typed "
            "into its pane. Its last message is below.", width)]
    if c["context"]:
        out += [("", None), ("The agent's last message before the question:", "under")]
        out += [(ln, "dim") for ln in _wrap(c["context"], width)]
    if c["links"]:
        out += [("", None), ("Links: " + "  ".join(c["links"]), "dim")]
    return out


def keys_line(c, chosen):
    if c["kind"] == "registry":
        return "↑↓ PgUp PgDn scroll · C copy all · g go · Esc back"
    if not c["answerable"]:
        return "↑↓ scroll · c copy · C copy all · b side chat with the session · s Studio · g go to answer · Esc back"
    pick = (f"{chosen + 1} selected: Enter sends · " if chosen is not None else
            (f"1-{min(9, len(c['options']))} pick · " if c["options"] else ""))
    extra = "n not a question · " if (c.get("kind") or "").startswith("question (heuristic)") else ""
    return pick + "r write · c copy · C copy all · b side chat · " + extra + "s Studio · ↑↓ scroll · g go · Esc back"


def draw_card(stdscr, c, lines, top, chosen, status, tail=None):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    _put(stdscr, 0, 0, keys_line(c, chosen), curses.A_DIM)
    body_h = h - 3 - (len(tail) + 1 if tail else 0)
    body_h = max(3, body_h)
    attrs = {"head": curses.A_BOLD, "question": curses.color_pair(1) | curses.A_BOLD, "dim": curses.A_DIM,
             "under": curses.A_UNDERLINE}
    for i, (ln, kind) in enumerate(lines[top:top + body_h]):
        attr = attrs.get(kind, 0)
        if kind and kind.startswith("option") and chosen is not None and kind == f"option{chosen + 1}":
            attr = curses.A_REVERSE
        _put(stdscr, 1 + i, 2, ln, attr)
    if tail:
        y = 1 + body_h
        _put(stdscr, y, 0, "── the session (live) " + "─" * max(0, w - 24), curses.A_DIM)
        for j, ln in enumerate(tail):
            _put(stdscr, y + 1 + j, 2, ln)
    # one footer row above the last (curses never writes the last row): the status, then the position
    counter = f"lines {top + 1}-{min(len(lines), top + body_h)} of {len(lines)}" if len(lines) > body_h else ""
    if status:
        _put(stdscr, h - 2, 0, status + (f"   ·   {counter}" if counter else ""), curses.A_BOLD)
    elif counter:
        _put(stdscr, h - 2, 0, counter, curses.A_DIM)
    stdscr.refresh()


def chat_lines(ask_id, width):
    import of_side_chat
    out = []
    for e in of_side_chat.history(ask_id):
        if e.get("role") == "you":
            out += [(ln, "head") for ln in _wrap("you: " + e["text"], width)]
        else:
            cost = (f" · ~${e['cost_usd']:.2f}" if e.get("cost_usd") is not None else "") + \
                (f" · {e['seconds']}s" if e.get("seconds") is not None else "") + \
                (f" · cache read {e['cache_read_tokens']}" if e.get("cache_read_tokens") else "") + \
                (f" · cache write {e['cache_write_tokens']}" if e.get("cache_write_tokens") else "")
            out += [(ln, None) for ln in _wrap(e.get("text") or "", width)]
            out.append((cost.strip(" ·") or "", "dim"))
        out.append(("", None))
    text, started = of_side_chat.partial(ask_id)
    if text is not None:
        out += [(ln, None) for ln in _wrap(text, width)] if text else []
        out.append((f"thinking… {int(time.time() - (started or time.time()))}s", "dim"))
    return out, text is not None


def chat_screen(stdscr, c):
    """The side chat with the asking session (a read-only fork; of_side_chat): history above,
    Enter or b to ask, Esc back (a reply in progress finishes in the background)."""
    import of_side_chat
    aid = c["ids"][0]
    status = ""
    while True:
        h, w = stdscr.getmaxyx()
        lines, busy = chat_lines(aid, max(20, w - 4))
        stdscr.erase()
        _put(stdscr, 0, 0, f"side chat with {c.get('position')} (a read-only fork of its session; it is not "
             "interrupted) · Enter ask · Esc back", curses.A_BOLD)
        body = h - 3
        for i, (ln, kind) in enumerate(lines[-body:]):
            _put(stdscr, 1 + i, 2, ln, {"head": curses.A_BOLD, "dim": curses.A_DIM}.get(kind, 0))
        if status:
            _put(stdscr, h - 1, 0, status, curses.A_DIM)
        stdscr.refresh()
        stdscr.timeout(400 if busy else -1)
        ch = stdscr.getch()
        if ch == -1:
            continue
        if ch in (27, ord("q")):
            stdscr.timeout(REFRESH_S * 1000)
            return
        if ch in (10, 13, curses.KEY_ENTER, ord("b")) and not busy:
            q = edit(stdscr, f"Ask {c.get('position')} (a fork of its session answers):")
            curses.flushinp()
            if q:
                status = of_side_chat.start(aid, c.get("session"), q)


def confirm_screen(stdscr, who, sending):
    """✓ only once the answer is recorded and delivered or queued; ✗ with the reason otherwise."""
    stdscr.erase()
    if not sending["done"]:
        _put(stdscr, 1, 2, f"Sending to {who}…", curses.A_BOLD)
    elif sending["ok"]:
        _put(stdscr, 1, 2, f"✓ Sent to {who}", curses.A_BOLD)
        _put(stdscr, 3, 2, sending["status"] or "")
        _put(stdscr, 5, 2, "Enter next question · g go to the session · Esc the list", curses.A_DIM)
    else:
        _put(stdscr, 1, 2, f"✗ Not sent to {who}", curses.color_pair(1) | curses.A_BOLD)
        _put(stdscr, 3, 2, sending["status"] or "")
        _put(stdscr, 4, 2, "Your answer is kept as a draft (~/.local/state/of/asks-drafts/).", curses.A_DIM)
        _put(stdscr, 6, 2, "r retry · e edit · Esc the list (the draft stays)", curses.A_DIM)
    stdscr.refresh()


def run(stdscr, positions, conn, act, card_of=None, answer=None, btw=None, tail_of=None, drafts=None,
        loader=None, reconcile=None, seq=None, handoff=None, studio=None, archive=None):
    """loader() -> fresh data from the view (in a thread: a few ms); reconcile() checks live sessions and
    the herdr counts in the background after the first true list, never before it; seq() is the
    listener's views.seq: a change reloads (polled every SEQ_POLL_MS), with a full reload every
    REFRESH_S as a safety net. Without loader, load(positions, conn) in the foreground (tests).
    Answers are optimistic (founder, 2026-11-06): the item leaves the list at once, and the answer is
    sent after UNDO_S unless u takes it back; a failed send brings the item back marked red; q sends
    what is pending, never drops it. studio(item, done): s opens Studio's Works (the ask's work card when it has one)
    in the background; done(status line) reports back (of_architect, 2026-11-06: bring the screen to where he looks)."""
    import threading
    curses.curs_set(0)
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_RED, -1)
    stdscr.timeout(SEQ_POLL_MS)
    bg = {"data": None, "busy": False, "card": None, "error": None, "good_at": None, "reconciled": False}

    def background_load():
        if bg["busy"]:
            return
        bg["busy"] = True

        def work():
            try:
                bg["data"] = loader()
                bg["error"], bg["good_at"] = None, time.time()
            except Exception as e:  # noqa: BLE001 - shown in the header, never hidden
                bg["error"] = "store busy" if "locked" in str(e) else f"{type(e).__name__}: {e}"[:80]
            bg["busy"] = False
            if reconcile and not bg["reconciled"]:  # after the true list is drawn: live sessions, herdr counts
                bg["reconciled"] = True

                def rec():
                    try:
                        reconcile()
                    except Exception:  # noqa: BLE001
                        pass
                    bg["reload"] = True  # without a listener, its changes show on this reload
                threading.Thread(target=rec, daemon=True).start()
        threading.Thread(target=work, daemon=True).start()

    studio_note = {}  # the s key's background result, shown on the next frame
    if loader:
        data, bg["good_at"] = read_cache(with_time=True)
        msg = "updating…"
        background_load()
    else:
        data, msg = load(positions, conn), ""
    last_seq, last_full = (seq() if seq else None), time.time()

    def reload():
        nonlocal data, last_full
        last_full = time.time()
        if loader:
            background_load()
        else:
            data = load(positions, conn)

    sel_id = None  # the selection is an ask id, so a refresh never moves it to another question
    mode, c, lines, top, chosen, it = "list", None, [], 0, None, None
    tail_pane, tail = None, None
    pending, hidden, errors = [], set(), {}  # answers waiting for their undo window; items they hide; failures
    expanded = {"panes": True}
    confirm_task = None  # the task whose "Done?" waits for y
    copied_at = 0.0      # when "copied" was shown (it goes after 2 s)

    def key_of(x):
        return (x["ids"] or [None])[0] or (x.get("kind") or "registry", tuple(x["who"]))

    def items():
        """What the screen shows: answered items hidden; the no-options section collapsed to one line."""
        out = []
        for label, group in data:
            for x in group:
                if key_of(x) in hidden:
                    continue
                out.append(x)
        return out

    def placeholder(x):
        return with_task({"who": x["who"], "position": (x["who"] or [None])[0], "system": None, "since": x.get("since"),
                          "kind": x.get("kind"), "question": x.get("text") or "", "options": [], "context": "loading…",
                          "links": [], "answerable": False, "ids": x.get("ids") or [], "session": None}, x)

    def with_task(card, x):
        """A task's card: the steps and the command to paste, under the title."""
        if x.get("task"):
            card = dict(card, question=(x.get("text") or "") + "\n\n" + tr("Steps:") + "\n" + (x.get("steps") or "")
                        + ("\n\n" + tr("Command:") + f"\n{x['command']}" if x.get("command") else "")
                        + "\n\n" + tr("When done: a in the list → «Done?» → y. The agent checks the result and "
                                       "closes the task."))
        return card

    def open_card(x):
        """Draw the card at once from the list item; the full texts load in the background."""
        nonlocal mode, c, lines, top, chosen, it, tail_pane, tail
        h, w = stdscr.getmaxyx()
        it, c = x, placeholder(x)
        lines, top, chosen, mode, tail_pane, tail = card_lines(c, max(20, w - 4)), 0, None, "card", None, None
        bg["card"] = None

        def work(target=x):
            try:
                bg["card"] = (target, card_of(target))
            except Exception as e:  # noqa: BLE001
                bg["card"] = (target, dict(placeholder(target), context=f"could not load: {e}"))
        threading.Thread(target=work, daemon=True).start()

    def queue(x, what, text=None):
        """An answer (what='answer', text) or a mark (what='answered'|'withdraw'): hidden now, sent after UNDO_S."""
        k = key_of(x)
        hidden.add(k)
        errors.pop(k, None)
        pending.append({"key": k, "item": x, "what": what, "text": text, "at": time.time(), "started": False,
                        "done": False, "ok": None, "status": None})

    def start(p):
        p["started"] = True

        def work():
            try:
                if p["what"] == "answer":
                    ok, status = answer(p["item"], p["text"])
                else:
                    out = act(p["what"], p["item"])
                    ok, status = (out if isinstance(out, tuple) else (True, out))
                p.update(ok=bool(ok), status=status)
            except Exception as e:  # noqa: BLE001
                p.update(ok=False, status=f"{type(e).__name__}: {e}")
            p["done"] = True
        p["thread"] = threading.Thread(target=work, daemon=True)
        p["thread"].start()

    def settle_pending():
        """Start what passed its undo window; bring failures back marked red; forget what is gone."""
        nonlocal msg
        now = time.time()
        for p in list(pending):
            if not p["started"] and now - p["at"] >= UNDO_S:
                start(p)
            if p["done"]:
                pending.remove(p)
                if not p["ok"]:
                    hidden.discard(p["key"])
                    errors[p["key"]] = str(p["status"] or "not sent")[:100]
                    msg = tr("not sent: {why}", why=errors[p['key']])
        present = {key_of(x) for _, g in data for x in g}
        for k in list(hidden):  # a sent answer stays hidden until the list itself drops the item
            if k not in present and not any(p["key"] == k for p in pending):
                hidden.discard(k)

    def undo():
        """u: take back the latest answer still inside its window."""
        nonlocal sel_id, msg
        p = next((p for p in reversed(pending) if not p["started"]), None)
        if not p:
            return False
        pending.remove(p)
        hidden.discard(p["key"])
        sel_id, msg = p["key"], tr("cancelled")
        return True

    def flush():
        """q, Esc or g with answers pending: hand them to a detached sender and leave at once (never wait
        here, never drop an answer: founder, 2026-11-06). One already being sent finishes or is sent again
        by the sender, which skips an ask that is answered by then."""
        left = [p for p in pending if not p["done"]]
        if not left:
            return
        if handoff:
            handoff(left)
            return
        for p in left:  # no handoff (tests of the old path): send here
            if not p["started"]:
                start(p)
        for p in left:
            p["thread"].join(timeout=60)

    def undo_line():
        p = next((p for p in reversed(pending) if not p["started"]), None)
        if not p:
            return None
        left = max(0, int(UNDO_S - (time.time() - p["at"]) + 0.99))
        return tr("sent ✓ · u to undo ({s} s)", s=left)

    def next_question():
        its = items()
        return its[0] if its else None

    while True:
        if bg["data"] is not None:  # a background refresh finished: the selection stays on its ask id
            data, bg["data"] = bg["data"], None
            if msg == "updating…":
                msg = ""
            if archive:  # a draft whose ask closed meanwhile (answered in the chat) leaves the popup, once
                open_ids = {i for _, items in data for it in items for i in it.get("ids") or []}
                gone = archive(open_ids)
                if gone:
                    msg = tr("{n} draft(s) archived: the question is closed already ({ids})", n=len(gone),
                             ids=", ".join(g[-8:] for g in gone))
        if bg.pop("reload", False):
            reload()
        if seq:  # the listener's signal: a view changed
            s_ = seq()
            if s_ != last_seq:
                last_seq = s_
                reload()
        if time.time() - last_full >= REFRESH_S:
            reload()
        settle_pending()
        if bg["card"] is not None and mode == "card" and bg["card"][0] is it:
            c = with_task(bg["card"][1], it)
            lines = card_lines(c, max(20, stdscr.getmaxyx()[1] - 4))
            bg["card"] = None
        its = items()
        if mode == "list":
            # the rows on screen, in order: items, and one collapsed line for the no-options section
            rows = []
            for x in its:
                if x.get("section") == "panes" and not expanded["panes"]:
                    if not rows or rows[-1] != "fold":
                        rows.append("fold")
                    continue
                rows.append(x)
            ids = ["fold" if r == "fold" else key_of(r) for r in rows]
            sel = ids.index(sel_id) if sel_id in ids else 0
            sel_id = ids[sel] if ids else None
            stdscr.timeout(SEQ_POLL_MS)
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            asked = [x for x in its if x["kind"] != "registry" and x.get("section") != "panes"]
            if studio_note.get("m"):
                msg = studio_note.pop("m")
            _put(stdscr, 0, 0, f"of asks · {len(asked)} waiting on you · Enter open · g go · a answered · "
                 "w withdraw · s Studio: works · q quit", curses.A_BOLD)
            if bg["error"]:
                at = time.strftime("%H:%M", time.localtime(bg["good_at"])) if bg["good_at"] else "never"
                _put(stdscr, 1, 0, f"! {bg['error']}: this is the last good list, from {at}; retrying",
                     curses.color_pair(1) | curses.A_BOLD)
            y, idx, section = 2, 0, None
            n_fold = sum(1 for x in its if x.get("section") == "panes")
            labels = {"task": tr("For you to do"), "urgent": tr("Urgent"), "normal": "Waiting on you",
                      "panes": tr("Asked in a pane, without of ask (recognized by Haiku; no notifications)"),
                      "registry": "Registry-declared (no notifications)"}
            for r in rows:
                if y >= h - 3:
                    break
                sec = "panes" if r == "fold" else ("registry" if r["kind"] == "registry" else r.get("section", "normal"))
                if sec != section:
                    section = sec
                    y += 1 if y > 2 else 0
                    if sec != "panes" or expanded["panes"]:
                        _put(stdscr, y, 0, labels[sec], curses.A_UNDERLINE)
                        y += 1
                attr = curses.A_REVERSE if idx == sel else 0
                if r == "fold":
                    _put(stdscr, y, 0, tr(" ▸ {n} more from panes (Enter to expand)", n=n_fold), attr | curses.A_DIM)
                else:
                    if r["kind"] != "registry":
                        attr |= curses.color_pair(1)
                    when = countdown(r.get("due")) if r.get("due") else _age(r["since"])
                    err = f"  ✗ {errors[key_of(r)]}" if key_of(r) in errors else ""
                    if r.get("task"):
                        err += (tr("  ✓ you said: done — waiting for the check") if r.get("claimed_at")
                                else (tr("  ↺ not accepted: ") + r["reopened"][:60] if r.get("reopened")
                                      else tr("  (Enter: steps)")))
                    _put(stdscr, y, 0, f" {when:>14}  {', '.join(r['who'])[:30]:<30}  {r['text']}{err}", attr)
                y += 1
                idx += 1
            pend = drafts() if drafts else []
            if pend:
                _put(stdscr, 1, 0, f"{len(pend)} unsent answer(s) kept as drafts: d sends them", curses.A_BOLD)
            line = undo_line() or msg
            if line:  # _put never writes the last row (curses), so the status line is the one above it
                _put(stdscr, h - 2, 0, line, curses.color_pair(1) if line.startswith(tr("not sent: {why}", why=""))
                     else curses.A_DIM)
            stdscr.refresh()
            ch = stdscr.getch()
            if confirm_task is not None and ch != -1:
                import of_ask_text
                if ch in tuple(ord(k) for k in of_ask_text.YES_KEYS):
                    queue(confirm_task, "answered")  # the claim: the asker confirms or reopens after checking
                    msg = tr("marked: done · the agent checks the result")
                else:
                    msg = ""
                confirm_task = None
                continue
            if ch == ord("u") and undo():
                continue
            if ch == ord("d") and pend:
                d = pend[0]
                queue(dict(d["item"], first=None), "answer", d["text"])
                continue
            if ch == ord("r"):
                reload()
            elif ch in (ord("q"), 27):
                flush()
                return
            elif ch in (curses.KEY_DOWN, ord("j")) and rows:
                sel_id = ids[min(len(ids) - 1, sel + 1)]
            elif ch in (curses.KEY_UP, ord("k")) and rows:
                sel_id = ids[max(0, sel - 1)]
            elif rows and ch in (10, 13, curses.KEY_ENTER):
                if rows[sel] == "fold":
                    expanded["panes"] = True
                else:
                    open_card(rows[sel])
            elif rows and rows[sel] != "fold" and ch == ord("g"):
                flush()
                act("go", rows[sel])
                return
            elif ch == ord("s") and studio:
                studio(rows[sel] if rows and rows[sel] != "fold" else None, lambda m: studio_note.update(m=m))
                msg = tr("Studio: opening…")
            elif rows and rows[sel] != "fold" and ch == ord("a") and rows[sel].get("task") and rows[sel]["ids"]:
                confirm_task = rows[sel]  # a task is not answered by a key: "Done?" first
                msg = tr("Done? y for yes (the agent checks the result), any other key for no")
            elif rows and rows[sel] != "fold" and ch in (ord("a"), ord("w")) and rows[sel]["ids"]:
                nxt = ids[sel + 1] if sel + 1 < len(ids) else (ids[sel - 1] if sel else None)
                queue(rows[sel], "answered" if ch == ord("a") else "withdraw")
                sel_id = nxt
            continue

        # the card: drawn from its own snapshot; the list refresh never redraws it
        if copied_at and time.time() - copied_at > 2 and msg.startswith((tr("copied"), tr("not copied: {why}", why=""))):
            msg, copied_at = "", 0.0
        h, w = stdscr.getmaxyx()
        if tail_pane:
            stdscr.timeout(1000)
            tail = (tail_of(tail_pane) or "").rstrip().splitlines()[-max(5, h // 3):]
        else:
            stdscr.timeout(150 if c.get("context") == "loading…" else SEQ_POLL_MS)
        if studio_note.get("m"):
            msg = studio_note.pop("m")
        draw_card(stdscr, c, lines, top, chosen, undo_line() or msg, tail)
        ch = stdscr.getch()
        if ch == -1:
            continue
        body = max(3, h - 3)

        def answered_go_next(text):
            """Optimistic: the answer is queued, the next question opens at once (founder, 2026-11-06)."""
            nonlocal mode, msg
            queue(it, "answer", text)
            curses.flushinp()  # keys typed meanwhile never leak into the next card
            nxt = next_question()
            if nxt:
                open_card(nxt)
            else:
                mode, msg = "list", tr("no more questions")
        if ch == ord("u") and undo():
            mode = "list"
        elif ch in (27, ord("q")):
            if tail_pane:
                tail_pane, tail, msg = None, None, ""
            else:
                mode, msg = "list", ""
        elif ch in (curses.KEY_DOWN, ord("j")):
            top = min(max(0, len(lines) - body), top + 1)
        elif ch in (curses.KEY_UP, ord("k")):
            top = max(0, top - 1)
        elif ch == curses.KEY_NPAGE:
            top = min(max(0, len(lines) - body), top + body - 1)
        elif ch == curses.KEY_PPAGE:
            top = max(0, top - body + 1)
        elif ch == curses.KEY_HOME:
            top = 0
        elif ch == curses.KEY_END:
            top = max(0, len(lines) - body)
        elif ch == ord("g"):
            flush()
            act("go", it)
            return
        elif c["answerable"] and c["options"] and ord("1") <= ch <= ord(str(min(9, len(c["options"])))):
            chosen = ch - ord("1")
            msg = f"{chosen + 1} selected: {c['options'][chosen][:60]} · Enter sends"
        elif c["answerable"] and chosen is not None and ch in (10, 13, curses.KEY_ENTER):
            msg = ""
            answered_go_next(c["options"][chosen])
        elif c["answerable"] and ch in (ord("r"), ord("t")):
            text = edit(stdscr, f"Answer to {', '.join(c['who'])}: {(it.get('text') or c['question']).splitlines()[0]}")
            curses.flushinp()
            if text:
                answered_go_next(text)
        elif ch == ord("a") and it.get("task") and c.get("ids"):
            mode, confirm_task = "list", it
            msg = tr("Done? y for yes (the agent checks the result), any other key for no")
        elif ch in (ord("a"), ord("w")) and c.get("ids"):
            queue(it, "answered" if ch == ord("a") else "withdraw")
            nxt = next_question()
            if nxt:
                open_card(nxt)
            else:
                mode = "list"
        elif ch == ord("n") and c.get("ids") and (c.get("kind") or "").startswith("question (heuristic)"):
            import of_asks_answer
            msg = of_asks_answer.not_a_question(it, c.get("kind"))
            mode = "list"
            hidden.add(key_of(it))
            reload()
        elif ch == ord("s") and studio:
            studio(it, lambda m: studio_note.update(m=m))
            msg = tr("Studio: opening…")
        elif ch in (ord("c"), ord("C")):
            text = copy_target(c, it) if ch == ord("c") else "\n".join(t for t, _ in card_lines(c, 100000))
            try:
                copy_text(text)
                msg, copied_at = tr("copied"), time.time()
            except Exception as e:  # noqa: BLE001
                msg, copied_at = tr("not copied: {why}", why=type(e).__name__), time.time()
        elif ch == ord("b") and c.get("ids"):
            chat_screen(stdscr, c)
            curses.flushinp()
        elif ch == curses.KEY_RESIZE:
            lines = card_lines(c, max(20, stdscr.getmaxyx()[1] - 4))


# ------------------------------------------------------------------ the outbox: q never waits (2026-11-06)

def outbox_dir():
    import of_events
    return os.path.join(of_events.STATE_DIR, "asks-outbox")


def to_outbox(pendings):
    """Write each pending answer as a file (ask ids, text, the item as the popup saw it). Returns the paths."""
    import uuid
    d = outbox_dir()
    os.makedirs(d, mode=0o700, exist_ok=True)
    out = []
    for p in pendings:
        path = os.path.join(d, f"{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}.json")
        body = {"what": p["what"], "text": p.get("text"), "item": {k: p["item"].get(k) for k in
                ("who", "text", "since", "ids", "kind", "first")}, "queued_at": time.time()}
        tmp = path + ".tmp"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump(body, fh, ensure_ascii=False, default=str)
        os.replace(tmp, path)
        out.append(path)
    return out


def spawn_sender(of_path):
    """One detached sender (`of asks send-outbox`): it outlives the popup and never touches the SSH agent."""
    env = dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK="")
    subprocess.Popen([sys.executable, of_path, "asks", "send-outbox"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, env=env)


def send_outbox(answer, act, still_open, reconcile=None, log=lambda m: None):
    """Send every answer in the outbox once (flock: one sender at a time), oldest first, then ONE reconcile.
    An answer whose asks are all closed already (the popup's thread got there first) is dropped, not sent
    twice. A failed one stays in the outbox for the next run (the listener starts one). Returns sent count."""
    import fcntl
    d = outbox_dir()
    os.makedirs(d, mode=0o700, exist_ok=True)
    lock = open(os.open(os.path.join(d, ".lock"), os.O_WRONLY | os.O_CREAT, 0o600))
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0  # another sender runs
    sent = 0
    try:
        while True:
            files = sorted(f for f in os.listdir(d) if f.endswith(".json"))
            if not files:
                break
            progressed = False
            for f in files:
                path = os.path.join(d, f)
                try:
                    with open(path) as fh:
                        body = json.load(fh)
                except (OSError, ValueError):
                    os.replace(path, path + ".bad")
                    continue
                item = body["item"]
                if not still_open(item.get("ids") or []):
                    os.remove(path)  # answered already
                    progressed = True
                    continue
                try:
                    if body["what"] == "answer":
                        ok, status = answer(item, body["text"])
                    else:
                        out = act(body["what"], item)
                        ok, status = out if isinstance(out, tuple) else (True, out)
                except Exception as e:  # noqa: BLE001
                    ok, status = False, f"{type(e).__name__}: {e}"
                if ok:
                    os.remove(path)
                    sent += 1
                    progressed = True
                else:
                    log(f"outbox {f}: {status}")
            if not progressed:
                break  # what is left failed this time; the next run tries again
    finally:
        lock.close()
    if sent and reconcile:
        try:
            reconcile()
        except Exception:  # noqa: BLE001
            pass
    return sent


def outbox_waiting(older_than_s=60):
    """Answers left in the outbox longer than older_than_s (a sender died): the listener starts one."""
    d = outbox_dir()
    try:
        return [f for f in os.listdir(d) if f.endswith(".json")
                and time.time() - os.path.getmtime(os.path.join(d, f)) > older_than_s]
    except OSError:
        return []


def debounced(fn, wait_s=2.0):
    """fn at most once per wait_s, in a background thread, after the last call (one reconcile for a burst
    of answers, off each answer's critical path)."""
    import threading
    state = {"timer": None}
    lock = threading.Lock()

    def call():
        with lock:
            if state["timer"]:
                state["timer"].cancel()
            t = threading.Timer(wait_s, fn)
            t.daemon = True
            state["timer"] = t
            t.start()
    return call


def studio_target(conn, ask_id, works_of=None):
    """(org, Studio path) for the popup's s key: the work card when the ask names a work (of ask --work) or carries a
    tag a work's match lists, else the organization's Works view. works_of(org) -> the works map (registry)."""
    import urllib.parse
    row = conn.execute("SELECT org, payload FROM event WHERE event_id = ?", (ask_id,)).fetchone() if ask_id else None
    org, pl = (row[0], json.loads(row[1] or "{}")) if row else (None, {})
    wid = pl.get("work")
    if not wid and org and works_of:
        tags = {(t.get("tag") if isinstance(t, dict) else str(t)) for t in pl.get("tags") or []}
        hits = sorted(k for k, w in (works_of(org) or {}).items() if isinstance(w, dict)
                      and tags & set(((w.get("match") or {}).get("tags") or [])))
        wid = hits[0] if len(hits) == 1 else None  # several: the Works view, not a guess
    q = {k: v for k, v in (("org", org), ("w", wid)) if v}
    return org, "/studio/works" + ("?" + urllib.parse.urlencode(q) if q else "")


def open_studio(org, path, client_for=None, opener=None):
    """A one-time link from the organization's of-server (POST /v1/studio/link, so the view is recorded as
    of.studio_viewed), landing on `path`; opened in the browser. Returns a status line."""
    try:
        import of_client
        client = client_for(org) if client_for else _studio_client(org)
        body = {"next": path}
        who = of_client.studio_requester()
        if who:  # an agent pressed it (a test, a check): Studio leaves the view out of the owner's measures
            body["requester"] = who
        url = client._authed("POST", "/v1/studio/link", body=body)[1]["url"]
    except Exception as e:  # noqa: BLE001 - the server may be down
        return f"Studio: no link ({type(e).__name__}: {str(e)[:80]})"
    (opener or (lambda u: subprocess.run(["open", u], check=False)))(url)
    return "Studio opened in the browser (Works" + (", the work's card" if "w=" in path else "") + ")"


def _studio_client(org):
    import of_client
    import of_ship
    cfg = of_ship.config()
    servers = of_ship.org_servers(cfg) if hasattr(of_ship, "org_servers") else {}
    return of_client.Client((servers.get(org) if org else None) or cfg["server_url"])


def works_of(org):
    """The organization's works map from its registry (orgs.yaml), or {}."""
    try:
        import yaml
        cfg = os.environ.get("OF_ORGS") or os.path.expanduser("~/.config/of/orgs.yaml")
        with open(cfg) as fh:
            orgs = (yaml.safe_load(fh) or {}).get("orgs") or []
        o = next((x for x in orgs if x.get("name") == org and x.get("path")), None)
        if not o:
            return {}
        with open(os.path.join(os.path.expanduser(o["path"]), "registry", "positions.yaml")) as fh:
            return (yaml.safe_load(fh) or {}).get("works") or {}
    except Exception:  # noqa: BLE001
        return {}


def _first_org(positions):
    return next((p.get("_orgid") or p.get("_org") for p in positions or [] if p.get("_org")), None)


def act_cli(of_path):
    def act(what, item):
        if what == "go":
            subprocess.run([sys.executable, of_path, "go", item["who"][0]], capture_output=True)
            return ""
        args = ["asks", "answered", item["ids"][0]] if what == "answered" else ["asks", "withdraw", item["ids"][0]]
        r = subprocess.run([sys.executable, of_path, *args], capture_output=True, text=True)
        out = (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr) else what
        return r.returncode == 0, out
    return act


def _positions(positions):
    return positions() if callable(positions) else positions


def threaded_answer(positions, pane_of=None, after=None):
    """The answer function the sending thread calls: it opens its own store connection (SQLite
    objects stay in the thread that made them; the founder lost an answer to sec_keeper on
    2026-11-06 this way). Returns (ok, status)."""
    import of_asks_answer

    def answer(it, text):
        import of_store
        own = of_store.connect()
        try:
            out = of_asks_answer.answer(own, it, text, _positions(positions), pane_of or (lambda name: None))
        finally:
            own.close()
        if after:
            try:
                after()
            except Exception:
                pass
        return out
    return answer


def main(positions, conn, of_path, pane_of=None, after=None, refresh=None):
    import of_asks_answer
    os.environ.setdefault("ESCDELAY", "25")  # a single Esc is Esc, not the start of a key sequence (1 s)

    def card_of(it):  # in a thread: its own store connection
        import of_store
        own = of_store.connect()
        try:
            return of_asks_answer.card(own, it, _positions(positions))
        finally:
            own.close()

    def loader():  # in a thread: the true list first (the view: a few ms), the cache written from it at once
        import of_store
        own = of_store.connect()
        try:
            pos = _positions(positions)  # a callable loads the registry here, after the first frame
            data = checked(load(pos, own), read_cache(), lambda: load(pos, own, from_log=True))
        finally:
            own.close()
        write_cache(data)  # the next open draws this at once
        return data

    answer = threaded_answer(positions, pane_of, debounced(after) if after else None)
    import of_listen

    def handoff(pendings):
        to_outbox(pendings)
        spawn_sender(of_path)

    def studio(item, done):  # in a thread: the store read, the server's link and the browser never hold the popup
        import threading

        def work():
            import of_store
            own = of_store.connect()
            try:
                org, path = studio_target(own, ((item or {}).get("ids") or [None])[0], works_of)
                done(open_studio(org or _first_org(_positions(positions)), path))
            except Exception as e:  # noqa: BLE001
                done(f"Studio: {type(e).__name__}: {str(e)[:80]}")
            finally:
                own.close()
        threading.Thread(target=work, daemon=True).start()

    curses.wrapper(run, [], conn, act_cli(of_path), card_of, answer, None, None,
                   of_asks_answer.unsent_drafts, loader, refresh, of_listen.seq, handoff, studio,
                   of_asks_answer.archive_drafts)
    return 0


def workspace_counts(positions, agents, asks, bind, workspaces, org_workspace, desk_label="desk"):
    """{workspace_id: open asks}: per organization's workspace, and the desk (asks of the agents on it)."""
    import of_asks
    open_by_pos = {}
    for a in of_asks.for_owner(asks):
        if a["state"] == "open" and a.get("position"):
            open_by_pos[a["position"]] = open_by_pos.get(a["position"], 0) + 1
    pos_ws = {p["name"]: a["workspace_id"] for p, a in bind(positions, agents)[0]}
    label_id = {w.get("label"): w["workspace_id"] for w in workspaces}
    out = {w["workspace_id"]: 0 for w in workspaces}
    for p in positions:
        n = open_by_pos.get(p["name"], 0)
        if not n:
            continue
        # its home: the unit's workspace when it has one (ac·platform), else the organization's
        home = label_id.get(p.get("_home_ws") or "") or label_id.get(org_workspace.get(p.get("_org"), ""))
        if home:
            out[home] += n
        live_ws = pos_ws.get(p["name"])
        if live_ws and live_ws != home:
            out[live_ws] = out.get(live_ws, 0) + n  # e.g. the desk, where the agent sits now
    return out


def report_workspaces(counts):
    for ws, n in counts.items():
        subprocess.run(["herdr", "workspace", "report-metadata", ws, "--source", "of:asks",
                        "--token", f"asks={n} ask{'s' if n != 1 else ''}" if n else "asks="],
                       capture_output=True, timeout=10)
