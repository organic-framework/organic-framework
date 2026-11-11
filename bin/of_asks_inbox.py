"""`of asks --watch`, layout A: the owner's questions as a queue on the left and the selected card on the right.

The founder chose layout A from three mockups on 2026-11-12 ("OK, let's build it, we'll test it here at my place";
the design agreed with him is of_conductor's asks-herdr-plugin-design.md). Why: the earlier popup had no scrolling,
a long queue did not fit the screen, the arrows did not wrap from the first question to the last, and it looked poor
(59 cards were open that day). Prior art he accepted: Gmail's categories and Superhuman's split inbox, Linear's
one-key triage, HEY, lazygit and k9s (list plus detail, a footer of keys, `?` help, Esc back), GitHub notifications.

The screen:
- top: the streams with their counts, Tab and Shift+Tab switch. Holds work (a blocking question, a dialog or a
  permission that stops an agent, or a question due within URGENT_S), Money and production (money, prod_change,
  irreversible or security_incident, declared or found in its words by of_classify.tags_of), For your hands (tasks),
  Everything else. Snoozed and Handed over appear when they hold anything; a search shows its results as a stream.
  Inside a stream: due soonest first, then oldest.
- left: the queue of the current stream; ↑↓ (j/k) select and wrap at both ends, PgUp/PgDn page, g/G first and last.
- right: the selected card in full: the question, who asks and from which organization, how long it waits, why now,
  the context, the options under their numbers with the recommended one marked, the links last.
- bottom: a permanent line of keys; the status line above it.

Keys: 1-9 answer with that option (a task's 1 is "done", which its asker then checks); r write an answer; h snooze
(in an hour, tomorrow at 9:00, next Monday at 9:00; in Snoozed: back now); d hand it to a position, which gets it in
its pane to settle; / search; ? help; u take back the last answer within UNDO_S; J/K or Space scroll the card; o go to
the asking agent's pane; a answered elsewhere, w withdraw, n not a question, b side chat, c/C copy, s Studio, D send
unsent drafts; Esc closes what is open, else the window, like q.

What must hold (the design's "behaviour that must hold"):
- The selection is an ask id: new cards never move it, and a key answers the card on the screen. When the selected
  card closes elsewhere, the selection moves to its neighbour and answer keys wait MOVED_GUARD_S, said on screen.
- An answer is recorded as the popup records it (of_asks_answer.answer through the same undo window and outbox); its
  row stays in the queue, marked, until the record exists, and leaves only then. A failed send brings it back in red.
- A money or irreversible answer needs a second press of the same key (or y); any other key cancels.
- Below NARROW_W columns: one card per screen, ←→ (and ↑↓) page through the stream (layout B), the same keys.
Snoozing and handing over are records (of.ask_snoozed, of.ask_handed): the card stays open and still answerable;
a handed card is typed into the receiver's pane by of_asks_answer.pending. Known limit: the pulse's reminders do not
read a snooze yet.
"""

import curses
import json
import os
import threading
import time

import of_asks_tui as T
from of_locale import tr

NARROW_W = 100       # below this many columns: one card per screen (layout B)
MOVED_GUARD_S = 1.5  # after the selected card closed elsewhere, answer keys wait this long
RISK_TAGS = ("money", "prod_change", "irreversible", "security_incident")
CONFIRM_TAGS = ("money", "irreversible")  # an answer needs a second key press
ORDER = ("holds", "risk", "hands", "rest", "later", "handed")
ALWAYS = ("holds", "risk", "hands", "rest")
COLOR = {"holds": "red", "risk": "amber", "hands": "purple", "rest": "grey", "later": "soft", "handed": "green",
         "search": "blue"}
OVERLAY_DAYS = 30
# The wheel: up is button 4; down is button 5, which an ncurses built with mouse version 1 (macOS's Python) cannot
# name and reports as button 4's double click or as a position report.
WHEEL_UP = curses.BUTTON4_PRESSED
WHEEL_DOWN = getattr(curses, "BUTTON5_PRESSED", 0) | 0x200000 | curses.REPORT_MOUSE_POSITION


def stream_name(s, q=""):
    return {"holds": tr("Holds work"), "risk": tr("Money and production"), "hands": tr("For your hands"),
            "rest": tr("Everything else"), "later": tr("Snoozed"), "handed": tr("Handed over"),
            "search": tr("Search: {q}", q=q)}[s]


# ------------------------------------------------------------------ pure logic (tested without a screen)

def key_of(x):
    return (x.get("ids") or [None])[0] or ("registry", tuple(x.get("who") or ()))


def risk_of(item):
    """The risk tags of the card's first ask: declared, or found in its words (of_asks_chat.held_tags)."""
    import of_asks_chat
    first = item.get("first") or {"text": item.get("text"), "tags": []}
    return [t for t in of_asks_chat.held_tags(first) if t in RISK_TAGS]


def stream_of(item, now=None):
    now = now or time.time()
    kind = item.get("kind") or ""
    if item.get("task"):
        return "hands"
    due = T._ts(item.get("due"))
    if "blocking" in kind or kind == "permission" or kind.startswith("blocked:") or \
            (due is not None and due - now <= T.URGENT_S):
        return "holds"
    if kind != "registry" and risk_of(item):
        return "risk"
    return "rest"


def sort_key(item):
    due = T._ts(item.get("due"))
    return (0, due) if due is not None else (1, T._ts(item.get("since")) or 0)


def split(items, overlay=None, now=None):
    """{stream: [items]} in ORDER, each sorted: due soonest first, then oldest. overlay {ask id: (stream, value)}
    puts a snoozed or handed card in its own stream."""
    overlay = overlay or {}
    out = {s: [] for s in ORDER}
    for x in items:
        o = next((overlay[i] for i in x.get("ids") or [] if i in overlay), None)
        out[o[0] if o else stream_of(x, now)].append(x)
    for s in out:
        out[s].sort(key=sort_key)
    return out


def shown_streams(groups):
    return [s for s in ORDER if s in ALWAYS or groups.get(s)]


def step(n, idx, delta, wrap=True):
    """The next index in a list of n: wrapping at both ends (the design: up from the first goes to the last)."""
    if n <= 0:
        return 0
    if wrap:
        return (idx + delta) % n
    return max(0, min(n - 1, idx + delta))


def snooze_until(preset, now=None):
    """hour: in an hour; morning: tomorrow at 9:00 local time; week: next Monday at 9:00 local time."""
    import datetime as dt
    now = now or time.time()
    if preset == "hour":
        return now + 3600
    local = dt.datetime.fromtimestamp(now).astimezone()
    if preset == "morning":
        day = local.date() + dt.timedelta(days=1)
    else:
        day = local.date() + dt.timedelta(days=7 - local.weekday())
    return dt.datetime.combine(day, dt.time(9, 0), local.tzinfo).timestamp()


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def overlay(conn, now=None):
    """{ask id: ("later", until epoch) | ("handed", position)} from of.ask_snoozed and of.ask_handed, the latest
    record per ask; a snooze that has run out, or a hand-over taken back (to null), leaves none."""
    now = now or time.time()
    since = iso(now - OVERLAY_DAYS * 86400)
    out = {}
    for name, payload in conn.execute("SELECT name, payload FROM event WHERE name IN ('of.ask_snoozed', "
                                      "'of.ask_handed') AND ts >= ? ORDER BY ts", (since,)):
        try:
            p = json.loads(payload or "{}")
        except ValueError:
            continue
        for aid in p.get("ask_ids") or [p.get("ask_id")]:
            if not aid:
                continue
            if name == "of.ask_snoozed":
                until = T._ts(p.get("until"))
                out[aid] = ("later", until) if until and until > now else None
            else:
                out[aid] = ("handed", p["to"]) if p.get("to") else None
    return {k: v for k, v in out.items() if v}


def needs_confirm(item, card=None):
    return bool(set((card or {}).get("risk") or risk_of(item)) & set(CONFIRM_TAGS))


def group_risk(conn, ids):
    """The risk tags of every ask in a card (finding 9 of 2026-11-11's review: a merged card is as risky as its
    riskiest ask)."""
    import of_asks_answer
    import of_asks_chat
    out = []
    for aid in ids or []:
        p, _, _, _ = of_asks_answer.ask_details(conn, aid)
        a = {"text": of_asks_answer.full_question(p, ""), "tags": p.get("tags") or [], "steps": p.get("steps")}
        out += [t for t in of_asks_chat.held_tags(a, of_asks_answer.options(p)) if t in RISK_TAGS]
    return list(dict.fromkeys(out))


def headline(question, width):
    """(the question in one line, the rest): its first line when short, else its first sentence that ends in a
    question mark or a full stop within two rows, else two rows cut with an ellipsis; the rest is the whole text when
    the headline is not its first line."""
    import re
    first, _, rest = question.partition("\n")
    room = max(40, 2 * width)
    if len(first) <= room:
        return first, rest
    sentences = [x.strip() for x in re.split(r"(?<=[.?!])\s+", first) if x.strip()]
    asked = [x for x in sentences if x.endswith("?") and len(x) <= room]
    if asked:  # the question itself, wherever it stands
        return asked[0], question
    if sentences and len(sentences[0]) <= room:
        return sentences[0], question
    return T.cut(first, room - 1) + "…", question


def tag_name(tag):
    return {"money": tr("money|tag"), "prod_change": tr("production|tag"), "irreversible": tr("irreversible|tag"),
            "security_incident": tr("security|tag")}.get(tag, tag)


def row_age(x, now=None):
    """The queue's right column: how long it has waited, with ! when it is overdue or due within URGENT_S."""
    now = now or time.time()
    due = T._ts(x.get("due"))
    mark = "!" if due is not None and due - now <= T.URGENT_S else ""
    return mark + (T._age(x.get("since")) or "")


def card_rows(c, item, width, overlay_note=None):
    """The card as [(text, style)], wrapped to width: title, who and how long, why now, the question, the options
    (the recommended one marked), the agent's last message, the links."""
    import of_ask_text
    wrap = T._wrap
    rows = []
    question = (c.get("question") or item.get("text") or "").strip()
    first, rest = headline(question, width)
    rows += [(ln, "title") for ln in wrap(first, width)]
    who = ", ".join(c.get("who") or item.get("who") or []) or c.get("position") or "?"
    org = c.get("org") or ""
    head = who + (f" · {org}" if org else "") + (f" · {tr('waits {age}', age=T._age(item.get('since')))}"
                                                  if item.get("since") else "")
    rows += [(ln, "dim") for ln in wrap(head, width)]
    why = []
    kind = item.get("kind") or ""
    if "blocking" in kind:
        why.append(tr("{who} is stopped until you answer", who=who))
    if item.get("due"):
        why.append(T.countdown(item.get("due")))
    risk = c.get("risk") or risk_of(item)
    if risk:
        why.append(tr("tagged {tags}", tags=", ".join(tag_name(t) for t in risk)))
    if overlay_note:
        why.append(overlay_note)
    if why:
        rows += [(ln, "why") for ln in wrap(tr("Why now: ") + "; ".join(why), width)]
    if rest.strip():
        rows.append(("", None))
        rows += [(ln, "text") for ln in wrap(rest.strip(), width)]
    if item.get("task"):
        rows.append(("", None))
        rows += [(tr("Steps:"), "head")] + [(ln, "text") for ln in wrap(item.get("steps") or "", width)]
        if item.get("command"):
            rows += [("", None), (tr("Command:"), "head")] + [(ln, "text") for ln in wrap(item["command"], width)]
        if item.get("claimed_at"):
            rows += [("", None), (tr("  ✓ you said: done — waiting for the check"), "dim")]
        elif item.get("reopened"):
            rows += [("", None)] + [(ln, "why") for ln in wrap(tr("  ↺ not accepted: ") + item["reopened"], width)]
        rows += [("", None), (f"1  {tr('Done (the agent checks the result)')}", "option")]
    else:
        opts = c.get("options") or []
        if opts:
            rows.append(("", None))
            for i, o in enumerate(opts[:9], 1):
                rec = bool(of_ask_text.RECOMMENDED.search(o))
                for j, ln in enumerate(wrap(f"{i}  {o}", width - 2)):
                    rows.append((("★ " if rec and j == 0 else "  ") + ln, "recommended" if rec else "option"))
        elif c.get("answerable"):
            rows += [("", None), (tr("No options: r writes an answer."), "dim")]
        elif not c.get("loading"):
            rows += [("", None), (tr("Answered in its pane: o goes there."), "dim")]
    if c.get("context"):
        rows += [("", None), (tr("The agent's last message before the question:"), "head")]
        rows += [(ln, "dim") for ln in wrap(c["context"], width)]
    if c.get("links"):
        rows += [("", None)] + [(ln, "dim") for ln in wrap(tr("Links: ") + "  ".join(c["links"]), width)]
    return rows


class Outgoing:
    """Answers and marks on their way: each waits UNDO_S for u, then is sent in a thread; its row stays, marked,
    until it is recorded, and a failure brings it back in red (the design: the card leaves only after the record)."""

    def __init__(self, answer, act, handoff=None):
        self.answer, self.act, self.handoff = answer, act, handoff
        self.pending, self.hidden, self.errors = [], set(), {}

    def queue(self, item, what, text=None):
        k = key_of(item)
        self.errors.pop(k, None)
        self.pending.append({"key": k, "item": item, "what": what, "text": text, "at": time.time(), "started": False,
                             "done": False, "ok": None, "status": None})

    def _start(self, p):
        p["started"] = True

        def work():
            try:
                if p["what"] == "answer":
                    ok, status = self.answer(p["item"], p["text"])
                else:
                    out = self.act(p["what"], p["item"])
                    ok, status = out if isinstance(out, tuple) else (True, out)
                p.update(ok=bool(ok), status=status)
            except Exception as e:  # noqa: BLE001 - shown on the row
                p.update(ok=False, status=f"{type(e).__name__}: {e}")
            p["done"] = True
        p["thread"] = threading.Thread(target=work, daemon=True)
        p["thread"].start()

    def settle(self, present):
        """Start what passed its undo window; a recorded one hides its row until the list drops it; a failure is
        kept for its row. Returns the failures' lines."""
        failed = []
        for p in list(self.pending):
            if not p["started"] and time.time() - p["at"] >= T.UNDO_S:
                self._start(p)
            if p["done"]:
                self.pending.remove(p)
                if p["ok"]:
                    self.hidden.add(p["key"])
                else:
                    self.errors[p["key"]] = str(p["status"] or "not sent")[:100]
                    failed.append(tr("not sent: {why}", why=self.errors[p["key"]]))
        for k in list(self.hidden):
            if k not in present:
                self.hidden.discard(k)
        return failed

    def state(self, k):
        p = next((p for p in self.pending if p["key"] == k), None)
        if p:
            return ("pending", p) if not p["started"] else ("sending", p)
        return ("failed", self.errors[k]) if k in self.errors else None

    def undo(self):
        p = next((p for p in reversed(self.pending) if not p["started"]), None)
        if not p:
            return None
        self.pending.remove(p)
        return p["key"]

    def undo_line(self):
        p = next((p for p in reversed(self.pending) if not p["started"]), None)
        if not p:
            return None
        return tr("sent ✓ · u to undo ({s} s)", s=max(0, int(T.UNDO_S - (time.time() - p["at"]) + 0.99)))

    def flush(self):
        """Leaving with answers in their window: hand them to the detached sender (never wait, never drop)."""
        left = [p for p in self.pending if not p["done"]]
        if not left:
            return
        if self.handoff:
            self.handoff(left)
            return
        for p in left:
            if not p["started"]:
                self._start(p)
        for p in left:
            p["thread"].join(timeout=60)


# ------------------------------------------------------------------ the screen

def colors():
    """{name: attr}: the OF palette (the conductor panel's), as 256-colour pairs where the terminal has them. Pair 1
    stays red: the editor and the side chat of of_asks_tui use it."""
    attrs = {k: 0 for k in COLOR.values()}
    try:
        curses.use_default_colors()
        many = getattr(curses, "COLORS", 8) >= 256
        palette = {"red": (203, curses.COLOR_RED), "amber": (220, curses.COLOR_YELLOW),
                   "purple": (141, curses.COLOR_MAGENTA), "green": (78, curses.COLOR_GREEN),
                   "grey": (250, curses.COLOR_WHITE), "soft": (243, curses.COLOR_WHITE), "blue": (75, curses.COLOR_BLUE)}
        for i, (name, (rich, basic)) in enumerate(palette.items(), 1):
            curses.init_pair(i, rich if many else basic, -1)
            attrs[name] = curses.color_pair(i)
    except curses.error:
        pass
    return attrs


class Inbox:
    def __init__(self, scr, loader, card_of, answer, act, positions=lambda: [], record=None, handoff=None,
                 studio=None, drafts=None, seq=None, initial=None, chat=None, archive=None):
        self.scr, self.loader, self.card_of, self.act = scr, loader, card_of, act
        self.positions, self.record, self.studio, self.drafts, self.seq = positions, record, studio, drafts, seq
        self.chat, self.archive = chat, archive
        self.out = Outgoing(answer, act, handoff)
        self.items, self.over = (initial or ([], {}))
        self.bg = {"data": None, "busy": False, "error": None, "good_at": None}
        self.cards, self.loading = {}, set()
        self.cur, self.sel, self.row, self.ctop = "holds", {}, {}, 0
        self.search, self.mode, self.confirm = None, "main", None
        self.msg, self.moved_at, self.note, self.studio_note = "", 0.0, {}, {}
        self.last_seq, self.last_full = (seq() if seq else None), 0.0
        self.attr, self.lw = {}, 0

    # data
    def refresh(self, force=False):
        if self.bg["busy"]:
            return
        self.bg["busy"], self.last_full = True, time.time()

        def work():
            try:
                self.bg["data"] = self.loader()
                self.bg["error"], self.bg["good_at"] = None, time.time()
            except Exception as e:  # noqa: BLE001 - shown on screen, never hidden
                self.bg["error"] = "store busy" if "locked" in str(e) else f"{type(e).__name__}: {e}"[:80]
            self.bg["busy"] = False
        threading.Thread(target=work, daemon=True).start()

    def take_data(self):
        if self.bg["data"] is None:
            return
        data, over = self.bg["data"]
        self.bg["data"] = None
        self.items = [x for _, g in data for x in g]
        self.over = over
        if self.archive:
            gone = self.archive({i for x in self.items for i in x.get("ids") or []})
            if gone:
                self.msg = tr("{n} draft(s) archived: the question is closed already ({ids})", n=len(gone),
                              ids=", ".join(g[-8:] for g in gone))

    def groups(self):
        visible = [x for x in self.items if key_of(x) not in self.out.hidden]
        g = split(visible, self.over)
        if self.search is not None:
            q = self.search.casefold()
            g["search"] = [x for x in visible if q in (x.get("text") or "").casefold()
                           or any(q in w.casefold() for w in x.get("who") or [])]
        return g

    def tabs(self, g):
        return (["search"] if self.search is not None else []) + shown_streams(g)

    def current(self, g):
        """The current stream's items and the selected index; the selection stays on its ask id."""
        tabs = self.tabs(g)
        if self.cur not in tabs or (self.cur != "search" and not g.get(self.cur)):
            # the most urgent stream that holds anything (the design); an emptied one gives way to the next
            self.cur = next((s for s in tabs if g.get(s)), tabs[0])
        its = g.get(self.cur) or []
        keys = [key_of(x) for x in its]
        k = self.sel.get(self.cur)
        if k in keys:
            self.row[("idx", self.cur)] = keys.index(k)
            return its, keys.index(k)
        if k is not None and its:  # it closed elsewhere: its neighbour, and the answer keys wait a moment
            idx = min(self.row.get(("idx", self.cur), 0), len(its) - 1)
            self.moved_at, self.msg = time.time(), tr("The card you were on was closed elsewhere: look again.")
        else:
            idx = 0
        self.sel[self.cur] = keys[idx] if keys else None
        self.row[("idx", self.cur)] = idx
        return its, idx

    def card(self, item):
        """The full card, loaded in the background; meanwhile a placeholder from the list item."""
        k = key_of(item)
        c = self.cards.get(k)
        if c is not None:
            return c
        if k not in self.loading and self.card_of:
            self.loading.add(k)

            def work():
                try:
                    self.cards[k] = self.card_of(item)
                except Exception as e:  # noqa: BLE001
                    self.cards[k] = {"question": item.get("text") or "", "options": [], "answerable": False,
                                     "context": tr("could not load: {why}", why=str(e)[:80]), "who": item.get("who")}
                self.loading.discard(k)
            threading.Thread(target=work, daemon=True).start()
        return {"question": item.get("text") or "", "options": [], "who": item.get("who"), "answerable": False,
                "loading": True, "context": tr("loading…")}

    # drawing
    def put(self, y, x, text, attr=0):
        T._put(self.scr, y, x, text, attr)

    def draw(self):
        g = self.groups()
        its, idx = self.current(g)
        h, w = self.scr.getmaxyx()
        self.scr.erase()
        # top: the streams and their counts
        x = 0
        for s in self.tabs(g):
            label = f" ● {stream_name(s, self.search or '')} {len(g.get(s) or [])} "
            a = self.attr.get(COLOR[s], 0)
            if s == self.cur:
                a |= curses.A_REVERSE | curses.A_BOLD
            self.put(0, x, label, a)
            x += T.dwidth(label) + 1
        if self.bg["error"]:
            at = time.strftime("%H:%M", time.localtime(self.bg["good_at"])) if self.bg["good_at"] else "—"
            self.put(1, 0, tr("! {why}: the last good list, from {at}; retrying", why=self.bg["error"], at=at),
                     self.attr.get("red", 0) | curses.A_BOLD)
        body_top, body_h = 2, max(3, h - 5)
        narrow = w < NARROW_W
        item = its[idx] if its else None
        if narrow:
            if item:
                self.put(1, 0, f"‹ {idx + 1} / {len(its)} ›  " + tr("←→ page"), curses.A_DIM)
            card_x, card_w = 1, max(20, w - 3)
        else:
            lw = max(30, min(60, w * 2 // 5))
            self.draw_list(its, idx, body_top, body_h, lw)
            for y in range(body_top, body_top + body_h):
                self.put(y, lw + 1, "│", curses.A_DIM)
            card_x, card_w = lw + 3, max(20, w - lw - 5)
        self.lw = 0 if narrow else lw
        if item:
            self.draw_card(item, body_top, body_h, card_x, card_w)
        elif not self.bg["busy"] or self.items:
            self.put(body_top + 1, card_x, tr("Nothing here.") if self.cur != "search" else tr("No card matches."),
                     curses.A_DIM)
        else:
            self.put(body_top + 1, card_x, tr("loading…"), curses.A_DIM)
        # bottom: the status line, then the keys
        line = self.out.undo_line() or self.overlay_line() or self.msg
        pend = self.drafts() if self.drafts else []
        if not line and pend:
            line = tr("{n} unsent answer(s) kept as drafts: D sends them", n=len(pend))
        if line:
            self.put(h - 3, 0, line, self.attr.get("red", 0) if line.startswith(tr("not sent: {why}", why=""))
                     else curses.A_BOLD)
        self.put(h - 2, 0, tr("↑↓ select · 1-9 answer · r write · h snooze · d hand over · / search · Tab stream · "
                              "? help · Esc back"), curses.A_DIM)
        self.scr.refresh()
        return its, idx

    def draw_list(self, its, idx, top_y, height, width):
        top = self.row.get(self.cur, 0)
        k = key_of(its[idx]) if its else None
        anchor = self.row.get(("anchor", self.cur))
        if anchor and anchor[0] == k:  # the same card: it keeps its row when cards arrive above it (the design: the
            top = idx - anchor[1]      # list never jumps while he reads)
        top = max(0, min(top, idx, max(0, len(its) - height)))
        if idx >= top + height:
            top = idx - height + 1
        self.row[self.cur], self.row[("anchor", self.cur)] = top, (k, idx - top)
        for i, x in enumerate(its[top:top + height]):
            k = key_of(x)
            st = self.out.state(k)
            age = row_age(x)
            title = " ".join((x.get("text") or "").split())
            tail = ""
            a = 0
            if st and st[0] in ("pending", "sending"):
                tail, a = " → " + (st[1]["text"] or {"answered": tr("done"), "withdraw": tr("withdrawn")}.get(
                    st[1]["what"], "")), curses.A_DIM
            elif st and st[0] == "failed":
                tail, a = f"  ✗ {st[1]}", self.attr.get("red", 0)
            elif x.get("claimed_at"):
                tail = "  ✓"
            room = max(4, width - 3 - T.dwidth(age) - 1)
            text = T.cut(title + tail, room)
            y = top_y + i
            sel = top + i == idx
            self.put(y, 0, "●", self.attr.get(COLOR[self.cur if self.cur != "search" else
                                                     stream_of(x)], 0))
            self.put(y, 2, text + " " * max(0, room - T.dwidth(text)) + " " + age,
                     (curses.A_REVERSE if sel else 0) | a)

    def overlay_line(self):
        if self.mode == "confirm" and self.confirm:
            item, n, text, _ = self.confirm
            return tr("Money or irreversible: press {n} again (or y) to send «{text}» to {who}; any other key cancels",
                      n=n, text=T.cut(text, 50), who=", ".join(item.get("who") or []))
        return None

    def draw_card(self, item, top_y, height, x0, width):
        c = self.card(item)
        k = key_of(item)
        note = None
        o = next((self.over[i] for i in item.get("ids") or [] if i in self.over), None)
        if o and o[0] == "later":
            note = tr("snoozed until {t}", t=time.strftime("%d.%m %H:%M", time.localtime(o[1])))
        elif o and o[0] == "handed":
            note = tr("handed to {p}", p=o[1])
        rows = card_rows(c, item, width, note)
        if self.mode == "help":
            rows = [(ln, "text") for ln in help_rows()]
        elif self.mode == "snooze":
            rows = [(tr("Snooze until:"), "title"), ("", None), ("1  " + tr("in an hour"), "option"),
                    ("2  " + tr("tomorrow at 9:00"), "option"), ("3  " + tr("next Monday at 9:00"), "option"),
                    ("", None), (tr("Esc cancels"), "dim")]
        if self.note.get("key") != k or self.note.get("mode") != self.mode:
            self.ctop, self.note = 0, {"key": k, "mode": self.mode}
        self.ctop = max(0, min(self.ctop, max(0, len(rows) - height)))
        style = {"title": curses.A_BOLD, "dim": curses.A_DIM, "why": self.attr.get("amber", 0),
                 "head": curses.A_UNDERLINE, "option": 0, "recommended": self.attr.get("amber", 0) | curses.A_BOLD,
                 "text": 0}
        for i, (ln, kind) in enumerate(rows[self.ctop:self.ctop + height]):
            self.put(top_y + i, x0, ln, style.get(kind, 0))
        if len(rows) > height:
            self.put(top_y + height, x0, tr("lines {a}-{b} of {n} · J/K or Space scroll", a=self.ctop + 1,
                                             b=min(len(rows), self.ctop + height), n=len(rows)), curses.A_DIM)

    # input
    def line_input(self, prompt, suggest=None):
        """One line of input on the status rows; Enter returns it, Esc None. suggest(text) -> names shown below."""
        text = ""
        curses.curs_set(1)
        self.scr.timeout(-1)
        try:
            while True:
                h, w = self.scr.getmaxyx()
                self.draw()
                self.put(h - 3, 0, " " * (w - 1))
                self.put(h - 3, 0, prompt + " " + text, curses.A_BOLD)
                if suggest:
                    self.put(h - 2, 0, " " * (w - 1))
                    self.put(h - 2, 0, "  ".join(suggest(text)[:8]) or tr("no match"), curses.A_DIM)
                self.scr.refresh()
                ch = self.scr.get_wch() if hasattr(self.scr, "get_wch") else self.scr.getch()
                if ch in ("\x1b", 27):
                    return None
                if ch in ("\n", "\r", 10, 13, curses.KEY_ENTER):
                    return text.strip()
                if ch in (curses.KEY_BACKSPACE, "\x7f", "\b", 127, 8):
                    text = text[:-1]
                elif isinstance(ch, str) and ch.isprintable():
                    text += ch
                elif isinstance(ch, int) and 32 <= ch < 127:
                    text += chr(ch)
        finally:
            curses.curs_set(0)
            self.scr.timeout(T.SEQ_POLL_MS)

    def position_names(self):
        try:
            return sorted({p["name"] for p in self.positions() or [] if p.get("name")
                           and p.get("status") not in ("left", "closed", "retired")})
        except Exception:  # noqa: BLE001
            return []

    def save(self, kind, payload, done):
        """A snooze or a hand-over: recorded in the background; done() updates the screen once it exists."""
        def work():
            try:
                ok = self.record(kind, payload) if self.record else None
                if ok:
                    done()
                else:
                    self.msg = tr("not saved: the event store did not take it")
            except Exception as e:  # noqa: BLE001
                self.msg = tr("not saved: {why}", why=f"{type(e).__name__}: {e}"[:80])
        threading.Thread(target=work, daemon=True).start()

    def answer(self, item, n):
        """Key n on the card on screen."""
        k = key_of(item)
        if time.time() - self.moved_at < MOVED_GUARD_S:
            self.msg = tr("The card you were on was closed elsewhere: look again.")
            return
        st = self.out.state(k)
        if st and st[0] in ("pending", "sending"):
            self.msg = tr("Already answered · u takes it back")
            return
        if item.get("task"):
            if n != 1 or not item.get("ids"):
                return
            what, text = "answered", tr("done")
        else:
            c = self.cards.get(k)
            if c is None or c.get("loading"):
                self.msg = tr("loading…")
                return
            if not c.get("answerable"):
                self.msg = tr("Answered in its pane: o goes there.")
                return
            opts = c.get("options") or []
            if not 1 <= n <= min(9, len(opts)):
                return
            what, text = "answer", opts[n - 1]
        if needs_confirm(item, self.cards.get(k)):
            self.mode, self.confirm = "confirm", (item, n, text, what)
            return
        self.send(item, what, text)

    def send(self, item, what, text):
        self.out.queue(item, what, text if what == "answer" else None)
        self.msg = ""
        curses.flushinp()  # keys typed meanwhile never reach the next card
        its, idx = self.current(self.groups())
        rest = [x for x in its if key_of(x) != key_of(item) and not self.out.state(key_of(x))]
        if rest:  # the next card that waits, below the answered one first
            after = [x for x in its[idx + 1:] if x in rest]
            self.sel[self.cur] = key_of((after or rest)[0])

    def mouse(self, its, idx):
        """The wheel scrolls what is under the pointer: the queue moves the selection (no wrap), the card scrolls;
        a click on a row of the queue selects it (herdr sends wheel reports once mouse reporting is on: of_herdr)."""
        try:
            _, x, y, _, b = curses.getmouse()
        except curses.error:
            return
        down = b & WHEEL_DOWN
        up = b & WHEEL_UP and not down
        on_list = self.lw and x < self.lw
        if (up or down) and on_list and its:
            self.sel[self.cur] = key_of(its[step(len(its), idx, -1 if up else 1, wrap=False)])
        elif up or down:
            self.ctop = max(0, self.ctop + (-3 if up else 3))
        elif on_list and its and b & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED) and y >= 2:  # a press
            row = self.row.get(self.cur, 0) + y - 2
            if 0 <= row < len(its):
                self.sel[self.cur] = key_of(its[row])

    def handle(self, ch):
        """One key. Returns False to leave."""
        g = self.groups()
        its, idx = self.current(g)
        item = its[idx] if its else None
        h, w = self.scr.getmaxyx()
        if ch == curses.KEY_MOUSE:
            if self.mode == "main":
                self.mouse(its, idx)
            return True
        if self.mode == "confirm":
            item_, n, text, what = self.confirm
            self.mode, self.confirm = "main", None
            if ch in (ord(str(n)), ord("y"), ord("Y")):
                self.send(item_, what, text)
            else:
                self.msg = tr("cancelled")
            return True
        if self.mode == "help":
            self.mode = "main"
            return True
        if self.mode == "snooze":
            self.mode = "main"
            presets = {ord("1"): "hour", ord("2"): "morning", ord("3"): "week"}
            if item and ch in presets and item.get("ids"):
                until = snooze_until(presets[ch])
                ids = list(item["ids"])

                def done():
                    for i in ids:
                        self.over[i] = ("later", until)
                    self.msg = tr("snoozed until {t}", t=time.strftime("%d.%m %H:%M", time.localtime(until)))
                self.save("ask_snoozed", {"ask_id": ids[0], "ask_ids": ids, "until": iso(until),
                                          "preset": presets[ch], "by": "owner"}, done)
            return True
        if ch in (ord("q"),) or (ch == 27 and self.search is None):
            self.out.flush()
            return False
        if ch == 27:
            self.search, self.cur = None, "holds"
        elif ch in (curses.KEY_DOWN, ord("j"), curses.KEY_RIGHT) and its:
            self.sel[self.cur] = key_of(its[step(len(its), idx, 1)])
        elif ch in (curses.KEY_UP, ord("k"), curses.KEY_LEFT) and its:
            self.sel[self.cur] = key_of(its[step(len(its), idx, -1)])
        elif ch == curses.KEY_NPAGE and its:
            self.sel[self.cur] = key_of(its[step(len(its), idx, max(1, h - 6), wrap=False)])
        elif ch == curses.KEY_PPAGE and its:
            self.sel[self.cur] = key_of(its[step(len(its), idx, -max(1, h - 6), wrap=False)])
        elif ch in (ord("g"), curses.KEY_HOME) and its:
            self.sel[self.cur] = key_of(its[0])
        elif ch in (ord("G"), curses.KEY_END) and its:
            self.sel[self.cur] = key_of(its[-1])
        elif ch in (ord("J"), getattr(curses, "KEY_SF", -2)):
            self.ctop += 1
        elif ch in (ord("K"), getattr(curses, "KEY_SR", -2)):
            self.ctop = max(0, self.ctop - 1)
        elif ch == ord(" "):
            self.ctop += max(1, h - 7)
        elif ch in (9, curses.KEY_BTAB):  # the streams that hold anything; the empty ones show their 0
            tabs = [s for s in self.tabs(g) if g.get(s) or s == self.cur]
            self.cur = tabs[step(len(tabs), tabs.index(self.cur), 1 if ch == 9 else -1)]
        elif ord("1") <= ch <= ord("9") and item:
            self.answer(item, ch - ord("0"))
        elif ch == ord("u"):
            k = self.out.undo()
            if k is not None:
                self.msg = tr("cancelled")
                for s in self.tabs(self.groups()):
                    if any(key_of(x) == k for x in self.groups().get(s) or []):
                        self.cur, self.sel[s] = s, k
        elif ch == ord("r") and item and item.get("ids"):
            c = self.cards.get(key_of(item)) or {}
            if item.get("task") or c.get("answerable"):
                text = T.edit(self.scr, tr("Answer to {who}: {q}", who=", ".join(item.get("who") or []),
                                           q=(item.get("text") or "").splitlines()[0] if item.get("text") else ""))
                curses.flushinp()
                if text:
                    if needs_confirm(item, c):
                        self.mode, self.confirm = "confirm", (item, "y", text, "answer")
                    else:
                        self.send(item, "answer", text)
        elif ch == ord("h") and item and item.get("ids"):
            if self.cur == "later":  # back now
                ids = list(item["ids"])

                def woke():
                    for i in ids:
                        self.over.pop(i, None)
                    self.msg = tr("back in the queue")
                self.save("ask_snoozed", {"ask_id": ids[0], "ask_ids": ids, "until": iso(time.time()),
                                          "preset": "now", "by": "owner"}, woke)
            else:
                self.mode = "snooze"
        elif ch == ord("d") and item and item.get("ids"):
            names = self.position_names()
            to = self.line_input(tr("Hand over to (a position):"),
                                 lambda t: [n for n in names if t.casefold() in n.casefold()])
            if to:
                hits = [n for n in names if n == to] or [n for n in names if to.casefold() in n.casefold()]
                if len(hits) != 1:
                    self.msg = tr("no single position matches «{t}»", t=to)
                else:
                    ids, who = list(item["ids"]), hits[0]

                    def handed():
                        for i in ids:
                            self.over[i] = ("handed", who)
                        self.msg = tr("handed to {p}: it gets the card in its pane", p=who)
                    self.save("ask_handed", {"ask_id": ids[0], "ask_ids": ids, "to": who, "by": "owner"}, handed)
        elif ch == ord("/"):
            q = self.line_input(tr("Search:"))
            if q:
                self.search, self.cur = q, "search"
        elif ch == ord("?"):
            self.mode = "help"
        elif ch in (ord("o"),) and item and item.get("who"):
            self.out.flush()
            self.act("go", item)
            return False
        elif ch in (ord("a"), ord("w")) and item and item.get("ids"):
            if ch == ord("a") and item.get("task"):
                self.answer(item, 1)
            else:
                self.send(item, "answered" if ch == ord("a") else "withdraw", None)
        elif ch == ord("n") and item and (item.get("kind") or "").startswith("question (heuristic)"):
            import of_asks_answer
            self.msg = of_asks_answer.not_a_question(item, item.get("kind"))
            self.out.hidden.add(key_of(item))
        elif ch == ord("b") and item and self.chat:
            c = self.cards.get(key_of(item))
            if c and c.get("ids"):
                self.chat(self.scr, c)
                curses.flushinp()
        elif ch in (ord("c"), ord("C")) and item:
            c = self.cards.get(key_of(item)) or {"question": item.get("text") or "", "options": []}
            text = T.copy_target(c, item) if ch == ord("c") else "\n".join(t for t, _ in card_rows(c, item, 100000))
            try:
                T.copy_text(text)
                self.msg = tr("copied")
            except Exception as e:  # noqa: BLE001
                self.msg = tr("not copied: {why}", why=type(e).__name__)
        elif ch == ord("s") and self.studio:
            self.studio(item, lambda m: self.studio_note.update(m=m))
            self.msg = tr("Studio: opening…")
        elif ch == ord("D") and self.drafts:
            pend = self.drafts()
            if pend:
                d = pend[0]
                self.out.queue(dict(d["item"], first=None), "answer", d["text"])
        return True

    def run(self):
        curses.curs_set(0)
        self.attr = colors()
        try:  # the wheel and clicks (text selection then takes Shift in most terminals; C copies the card)
            curses.mousemask(WHEEL_UP | WHEEL_DOWN | curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED)
            curses.mouseinterval(0)  # a press is delivered at once, no wait to tell a click
        except curses.error:
            pass
        self.scr.timeout(T.SEQ_POLL_MS)
        self.refresh()
        while True:
            self.take_data()
            if self.seq:
                s_ = self.seq()
                if s_ != self.last_seq:
                    self.last_seq = s_
                    self.refresh()
            if time.time() - self.last_full >= T.REFRESH_S:
                self.refresh()
            present = {key_of(x) for x in self.items}
            for line in self.out.settle(present):
                self.msg = line
            if self.studio_note.get("m"):
                self.msg = self.studio_note.pop("m")
            self.draw()
            ch = self.scr.getch()
            if ch == -1:
                continue
            if ch == curses.KEY_RESIZE:
                continue
            if not self.handle(ch):
                return


def help_rows():
    return [tr("Keys"), "",
            "↑ ↓  j k     " + tr("select; wraps from the first card to the last"),
            "PgUp PgDn    " + tr("a page of the queue"),
            "g G          " + tr("the first, the last card"),
            "Tab ⇧Tab     " + tr("the next, the previous stream"),
            "1-9          " + tr("answer with that option (money or irreversible: press it twice)"),
            "r            " + tr("write your own answer"),
            "u            " + tr("take back the last answer while its seconds run"),
            "h            " + tr("snooze: in an hour, tomorrow at 9:00, next Monday at 9:00 (in Snoozed: back now)"),
            "d            " + tr("hand it to a position, which gets the card in its pane to settle"),
            "/            " + tr("search the questions and who asks"),
            "J K Space    " + tr("scroll the card"),
            "o            " + tr("go to the asking agent's pane"),
            "a w n        " + tr("answered elsewhere, withdraw, not a question"),
            "b c C s      " + tr("side chat, copy the answer, copy the card, Studio"),
            "Esc q        " + tr("back, close"), "",
            tr("Any key closes this help.")]


def main(positions, conn, of_path, pane_of=None, after=None, refresh=None):
    """The window: the same answer path as the classic popup (of_asks_tui.main), its cache for the first frame."""
    import of_asks_answer
    import of_listen
    os.environ.setdefault("ESCDELAY", "25")

    def card_of(it):  # in a thread: its own store connection
        import of_store
        own = of_store.connect()
        try:
            c = of_asks_answer.card(own, it, T._positions(positions))
            c["risk"] = group_risk(own, c.get("ids") or it.get("ids"))
            return c
        finally:
            own.close()

    reconciled = []

    def loader():  # in a thread: the true list, written to the cache for the next first frame
        import of_store
        own = of_store.connect()
        try:
            pos = T._positions(positions)
            data = T.checked(T.load(pos, own), T.read_cache(), lambda: T.load(pos, own, from_log=True))
            over = overlay(own)
        finally:
            own.close()
        T.write_cache(data)
        if refresh and not reconciled:  # live sessions and the herdr counts, once, after the first true list
            reconciled.append(True)
            threading.Thread(target=lambda: _quiet(refresh), daemon=True).start()
        return data, over

    def record(kind, payload):
        import of_events
        return of_events.emit(kind, payload)

    def handoff(pendings):
        T.to_outbox(pendings)
        T.spawn_sender(of_path)

    def studio(item, done):
        def work():
            import of_store
            own = of_store.connect()
            try:
                org, path = T.studio_target(own, ((item or {}).get("ids") or [None])[0], T.works_of)
                done(T.open_studio(org or T._first_org(T._positions(positions)), path))
            except Exception as e:  # noqa: BLE001
                done(f"Studio: {type(e).__name__}: {str(e)[:80]}")
            finally:
                own.close()
        threading.Thread(target=work, daemon=True).start()

    answer = T.threaded_answer(positions, pane_of, T.debounced(after) if after else None)
    first = (T.read_cache(), {})

    def run(scr):
        Inbox(scr, loader, card_of, answer, T.act_cli(of_path), positions=lambda: T._positions(positions),
              record=record, handoff=handoff, studio=studio, drafts=of_asks_answer.unsent_drafts, seq=of_listen.seq,
              initial=([x for _, g in first[0] for x in g], first[1]), chat=T.chat_screen,
              archive=of_asks_answer.archive_drafts).run()
    curses.wrapper(run)
    return 0


def _quiet(fn):
    try:
        fn()
    except Exception:  # noqa: BLE001
        pass
