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
- left: the queue of the current stream; ↑↓ (j/k) select and wrap at both ends, PgUp/PgDn page, Home/End first and
  last.
- right: the selected card in full: the question, who asks and from which organization, how long it waits, why now,
  the context, the options under their numbers with the recommended one marked, the links last.
- bottom: a permanent line of keys; the status line above it.

Keys: 1-9 answer with that option (a task's 1 is "done", which its asker then checks); r write an answer under the
card, which stays in sight and scrolls while he types (Esc keeps a draft); h snooze (in an hour, tomorrow at 9:00,
next Monday at 9:00; in Snoozed: back now); d hand it to a position, which gets it in its pane to settle; / search;
? help; u take back the last answer within UNDO_S; J/K or Space scroll the card; g (or o) go to the asking agent's
pane; a answered elsewhere, w withdraw, n not a question, b side chat, c/C copy, s Studio, D send unsent drafts; Esc
closes what is open, else the window, like q.

What must hold (the design's "behaviour that must hold"):
- The selection is an ask id: new cards never move it, and a key answers the card on the screen. When the selected
  card closes elsewhere, the selection moves to its neighbour and answer keys wait MOVED_GUARD_S, said on screen.
- An answer is recorded as the popup records it (of_asks_answer.answer through the same undo window and outbox); its
  row stays in the queue, marked, until the record exists, and leaves only then. A failed send brings it back in red.
- A money or irreversible answer needs a second press of the same key (or y); any other key cancels.
- Below NARROW_W columns: one card per screen, ↑↓ page through the stream and ←→ switch streams (layout B), the
  same keys otherwise.
Snoozing and handing over are records (of.ask_snoozed, of.ask_handed): the card stays open and still answerable;
a handed card is typed into the receiver's pane by of_asks_answer.pending. Known limit: the pulse's reminders do not
read a snooze yet.
"""

import curses
import functools
import json
import os
import threading
import time

import of_asks_tui as T
from of_locale import tr

PARTIAL_RETRY_S = 0.5  # a card built before the registry loaded asks again after this
NARROW_W = 100       # below this many columns: one card per screen (layout B)
WIDE_W = 180         # from this many columns: a third column with the agent's context (the founder, 2026-11-12 ~21:08Z:
#                      "lots of room, more information; less room, less information")
WHO_W = 48           # from this list width: each row says who asks
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


_RISK = {}


def risk_of(item):
    """The risk tags of the card's first ask: declared, or found in its words (of_asks_chat.held_tags). Kept per card
    and its words: the screen sorts every card into its stream on each frame, and the classifier's patterns over 65
    cards took a quarter of a core while the screen only waited for a key (2026-11-12)."""
    import of_asks_chat
    first = item.get("first") or {"text": item.get("text"), "tags": []}
    k = (key_of(item), item.get("text"), repr(first))
    if k not in _RISK:
        if len(_RISK) > 4000:
            _RISK.clear()
        _RISK[k] = [t for t in of_asks_chat.held_tags(first) if t in RISK_TAGS]
    return list(_RISK[k])


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


@functools.lru_cache(maxsize=8192)
def cell(ch):
    """A character's display cells (T.dwidth), kept: wrapping a pasted answer asked unicodedata twice per character
    on every key (of_herdr's review of PR 104: a 3,000-character paste spent 8 to 17 s in it)."""
    return T.dwidth(ch)


def wrap_line(line, width):
    """[(start, end)] of a line's display rows at `width` cells: a row breaks after its last space that fits, a word
    longer than a row is broken; an empty line is one empty row."""
    if not line:
        return [(0, 0)]
    rows, start, width = [], 0, max(2, width)
    ws = [cell(c) for c in line]
    while start < len(line):
        w, i, space = 0, start, -1
        while i < len(line) and w + ws[i] <= width:
            w += ws[i]
            if line[i] == " ":
                space = i
            i += 1
        if i >= len(line):
            rows.append((start, len(line)))
            break
        end = space + 1 if space >= start else max(i, start + 1)
        rows.append((start, end))
        start = end
    return rows


class Editor:
    """The reply field's text and its cursor (the founder, 2026-11-12 18:41Z: up and down did not move in the field).
    lines: the logical lines; (cl, cc): the cursor's line and character. Up and down move by display rows, wrapped
    rows included, keeping the column they started from; left and right cross line ends; Home and End go to the
    line's start and end."""

    def __init__(self, text=""):
        self.lines = (text or "").split("\n")
        self.cl, self.cc, self.goal = len(self.lines) - 1, len(self.lines[-1]), None
        self._wrapped = {}  # (line, width) -> its rows: a key changes one line, the others keep theirs

    def text(self):
        return "\n".join(self.lines)

    def insert(self, chars):
        ln = self.lines[self.cl]
        self.lines[self.cl] = ln[:self.cc] + chars + ln[self.cc:]
        self.cc, self.goal = self.cc + len(chars), None

    def newline(self):
        ln = self.lines[self.cl]
        self.lines[self.cl:self.cl + 1] = [ln[:self.cc], ln[self.cc:]]
        self.cl, self.cc, self.goal = self.cl + 1, 0, None

    def backspace(self):
        if self.cc:
            ln = self.lines[self.cl]
            self.lines[self.cl] = ln[:self.cc - 1] + ln[self.cc:]
            self.cc -= 1
        elif self.cl:
            self.cc = len(self.lines[self.cl - 1])
            self.lines[self.cl - 1] += self.lines.pop(self.cl)
            self.cl -= 1
        self.goal = None

    def delete(self):
        ln = self.lines[self.cl]
        if self.cc < len(ln):
            self.lines[self.cl] = ln[:self.cc] + ln[self.cc + 1:]
        elif self.cl < len(self.lines) - 1:
            self.lines[self.cl] += self.lines.pop(self.cl + 1)
        self.goal = None

    def left(self):
        if self.cc:
            self.cc -= 1
        elif self.cl:
            self.cl -= 1
            self.cc = len(self.lines[self.cl])
        self.goal = None

    def right(self):
        if self.cc < len(self.lines[self.cl]):
            self.cc += 1
        elif self.cl < len(self.lines) - 1:
            self.cl, self.cc = self.cl + 1, 0
        self.goal = None

    def home(self):
        self.cc, self.goal = 0, None

    def end(self):
        self.cc, self.goal = len(self.lines[self.cl]), None

    def rows(self, width):
        """[(line, start, end)]: every display row of the text."""
        if len(self._wrapped) > 4 * len(self.lines) + 64:
            self._wrapped = {}
        out = []
        for li, ln in enumerate(self.lines):
            k = (ln, width)
            if k not in self._wrapped:
                self._wrapped[k] = wrap_line(ln, width)
            out += [(li, a, b) for a, b in self._wrapped[k]]
        return out

    def cursor(self, rows):
        """(row index, cells from the row's start) of the cursor."""
        ln = self.lines[self.cl]
        for ri, (li, a, b) in enumerate(rows):
            if li == self.cl and (a <= self.cc < b or self.cc == b == len(ln)):
                return ri, sum(cell(c) for c in ln[a:self.cc])
        return len(rows) - 1, 0

    def vertical(self, delta, width):
        """Up (-1) or down (+1) one display row, to the cell nearest the column the moves started from."""
        rows = self.rows(width)
        ri, x = self.cursor(rows)
        goal = x if self.goal is None else self.goal
        t = ri + delta
        if not 0 <= t < len(rows):
            return
        li, a, b = rows[t]
        ln = self.lines[li]
        last = b if b == len(ln) else max(a, b - 1)  # a row that wraps: its end is the next row's start
        c, w = a, 0
        while c < last and w + cell(ln[c]) <= goal:
            w += cell(ln[c])
            c += 1
        self.cl, self.cc, self.goal = li, c, goal


class Writer:
    """The drafts' disk writes off the UI thread, in their order (the founder, 2026-11-13: the screen stuttered; keys
    and drawing never wait on a disk). Inbox takes one; without it, it writes at once (the tests)."""

    def __init__(self):
        import queue
        self.q = queue.Queue()
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self):
        while True:
            fn = self.q.get()
            try:
                fn()
            except Exception:  # noqa: BLE001 - one failed write never stops the others
                pass
            finally:
                self.q.task_done()

    def put(self, fn):
        self.q.put(fn)

    def flush(self, timeout=5.0):
        done = threading.Event()
        self.q.put(done.set)
        done.wait(timeout)


class ReplyDrafts:
    """The answers he put aside with Esc while writing one, kept across openings: one file per ask id under
    asks-reply-drafts in OF's state folder (mode 600). A draft whose ask closed goes to its archive folder, never lost.
    Not the outbox's drafts (of_asks_answer.save_draft): those are answers sent and not yet recorded, which D sends;
    these are unfinished (of_herdr's review of PR 98: Esc's draft lived only while the window was open)."""

    def __init__(self, path=None):
        if path is None:
            import of_events
            path = os.path.join(of_events.STATE_DIR, "asks-reply-drafts")
        self.dir = path

    def _file(self, key):
        return os.path.join(self.dir, f"{os.path.basename(str(key))}.json")

    def load(self):
        out = {}
        try:
            names = sorted(os.listdir(self.dir))
        except OSError:
            return out
        for f in names:
            if f.endswith(".json"):
                try:
                    with open(os.path.join(self.dir, f)) as fh:
                        d = json.load(fh)
                    out[d["ask_id"]] = d["text"]
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        return out

    def save(self, key, text):
        """text: the draft; None or blank removes it."""
        try:
            if not (text or "").strip():
                os.remove(self._file(key))
                return
        except FileNotFoundError:
            return
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        tmp = self._file(key) + f".{os.getpid()}"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            json.dump({"ask_id": key, "text": text, "saved_at": time.time()}, fh, ensure_ascii=False)
        os.replace(tmp, self._file(key))

    def archive(self, key):
        arch = os.path.join(self.dir, "archive")
        try:
            os.makedirs(arch, mode=0o700, exist_ok=True)
            os.replace(self._file(key), os.path.join(arch, f"{os.path.basename(str(key))}-{int(time.time())}.json"))
        except OSError:
            pass


YES = ("y", "Y", "\u043d", "\u041d")  # y, and the letter on its key in the Russian layout (the founder, 2026-11-12)


def char_of(ch):
    """A key as its character ('' for a function key): get_wch gives a str, getch an int."""
    if isinstance(ch, str):
        return ch
    return chr(ch) if isinstance(ch, int) and 0 <= ch < 0x110000 and ch not in (-1,) and ch < curses.KEY_MIN else ""


NEW_H = 6         # the log's tail read for asks the listener's view has not taken yet
CLOSED_H = 48     # and for the closures it may not show yet
SENDING_S = 600   # an answer in the outbox or on its way this long ago hides its card meanwhile


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int((t % 1) * 1000):03d}Z"


def _new_groups(conn, anchor, reopened=()):
    """The asks filed from NEW_H before `anchor` (the listener's last sign of life) that the view has not taken yet
    (view_asks holds every ask it projected, in any state), and the tasks reopened in the tail that the view holds
    closed; each projected alone: a declared ask or task is open from its own event."""
    import of_asks
    cols = "SELECT event_id, ts, name, session_id, position, payload, org, source FROM event "
    rows = conn.execute(cols + "WHERE name = 'of.agent_question' AND ts >= ? ORDER BY ts, rowid",
                        (_iso(anchor - NEW_H * 3600),)).fetchall()
    for tid in reopened:  # a task the view holds done, reopened since (of_herdr's note on PR 119)
        st = conn.execute("SELECT state FROM view_asks WHERE event_id LIKE ?", ("%" + tid,)).fetchone()
        if st and st[0] != "open":
            rows += conn.execute(cols + "WHERE name = 'of.agent_question' AND event_id LIKE ?", ("%" + tid,)).fetchall()
    if not rows:
        return []
    known = set()
    ids = [r[0] for r in rows]
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        known |= {e for (e,) in conn.execute(f"SELECT event_id FROM view_asks WHERE state = 'open' AND event_id IN "
                                             f"({','.join('?' * len(part))})", part)}
    new = []
    seen_closed = set()
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        seen_closed |= {e for (e,) in conn.execute(f"SELECT event_id FROM view_asks WHERE state != 'open' AND event_id "
                                                   f"IN ({','.join('?' * len(part))})", part)}
    again = {e for e in ids if any(e.endswith(t) for t in reopened)}
    for e, ts, n, sid, pos, pl, org, src in rows:
        if e in known or (e in seen_closed and e not in again):
            continue
        try:
            payload = json.loads(pl) if pl else {}
        except ValueError:
            continue
        if of_asks.relevant(n, src, payload):
            new.append({"event_id": e, "ts": ts, "name": n, "session_id": sid, "position": pos, "payload": payload,
                        "org": org})
    if not new:
        return []
    asks = [a for a in of_asks.project(new) if a["state"] == "open" and not (a.get("kind") or "").startswith(
        "question (heuristic)")]
    return of_asks.group(of_asks.for_owner(asks))


def _closures(conn, anchor):
    """{ask id: (what, ts)} from the log's last CLOSED_H: answered (by whom), withdrawn, a task done or reopened. The
    view shows them once the listener has taken them; the screen applies them at once."""
    out = {}
    names = ("of.agent_question_answered", "of.ask_withdrawn")
    for ts, name, pl in conn.execute("SELECT ts, name, payload FROM event WHERE name IN (?, ?) AND ts >= ? "
                                     "ORDER BY ts, rowid", names + (_iso(anchor - CLOSED_H * 3600),)):
        try:
            p = json.loads(pl or "{}")
        except ValueError:
            continue
        what = "withdrawn" if name == "of.ask_withdrawn" else (
            "claimed" if p.get("by") in ("owner", "founder") else "answered")
        for i in p.get("ask_ids") or [p.get("ask_id")]:
            if i:
                out[i] = (what, ts)
    for ts, pl in conn.execute("SELECT ts, payload FROM event WHERE name = 'of.state_declared' AND ts >= ? AND "
                               "(payload LIKE '%done_task%' OR payload LIKE '%reopen_task%') ORDER BY ts, rowid",
                               (_iso(anchor - CLOSED_H * 3600),)):
        try:
            p = json.loads(pl or "{}")
        except ValueError:
            continue
        if p.get("done_task"):
            out[("task", p["done_task"])] = ("done", ts)
        elif p.get("reopen_task"):
            out[("task", p["reopen_task"])] = ("reopened", ts)
    return out


def reader():
    """A read-only connection to the event store for the screen and the side panel. Reads in WAL mode never wait on
    the listener's writes; of_store.connect's first call in a process writes its migration mark, and behind a busy
    listener it waited its 10 s busy timeout on every opening and every card (2026-11-13: the list after 10.5 s at
    load ~40). Falls back to of_store.connect."""
    import sqlite3
    import urllib.parse
    import of_store
    try:
        c = sqlite3.connect("file:" + urllib.parse.quote(of_store.db_path()) + "?mode=ro", uri=True, timeout=2.0,
                            isolation_level=None)
        c.execute("PRAGMA query_only = 1")
        c.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
        return c
    except sqlite3.Error:
        return of_store.connect()


def on_their_way(now=None):
    """The ask ids of answers handed on and not recorded yet: the outbox (the detached sender's) and the drafts an
    answer writes before it is sent, from the last SENDING_S. Older ones failed and are shown again."""
    now = now or time.time()
    out = set()
    try:
        d = T.outbox_dir()
        for f in os.listdir(d):
            if f.endswith(".json"):
                try:
                    with open(os.path.join(d, f)) as fh:
                        b = json.load(fh)
                    if now - float(b.get("queued_at") or 0) < SENDING_S:
                        out |= set((b.get("item") or {}).get("ids") or [])
                except (OSError, ValueError, TypeError):
                    continue
    except OSError:
        pass
    try:
        import of_asks_answer
        for dr in of_asks_answer.unsent_drafts():
            if now - float(dr.get("saved_at") or 0) < SENDING_S:
                out |= set(dr.get("ask_ids") or [dr.get("ask_id")])
    except Exception:  # noqa: BLE001
        pass
    return out


def _last_seen(conn):
    """When the listener last showed life (epoch): its heartbeat, or, once a clean stop removed that, the asks
    cursor's last move, a step behind the view's last flush. None when there is neither."""
    try:
        row = conn.execute("SELECT value FROM listener_meta WHERE key = 'heartbeat'").fetchone()
        if row:
            return float(row[0])
        row = conn.execute("SELECT updated_at FROM listener_cursor WHERE processor = 'asks'").fetchone()
        if row and row[0]:
            return T._ts(row[0]) - 600  # the cursor moves before the flush: ten minutes of margin
    except Exception:  # noqa: BLE001
        pass
    return None


def queue(conn, positions=(), now=None, sending=()):
    """The founder's queue for the screen and the side panel, from one source: the listener's view, whatever its age,
    never a projection of the whole log (2026-11-13: with the listener held minutes in a tick, the screen projected
    the log, its list never came, and the cards he had answered showed again from the last opening's cache; the panel
    differed). The log's tail is applied at once: an answer, a withdrawal or a done task hides its card, an answer on
    its way too (sending), a new declared ask or task shows. Returns (data, behind): data [(label, items)] as
    of_asks_tui.load; behind None, or the listener's last heartbeat (epoch) when it is not fresh."""
    import of_listen
    now = now or time.time()
    try:
        groups = of_listen.read_groups(conn)
    except Exception:  # noqa: BLE001 - no listener has run here: the log, as the CLI computes it
        return T.load(positions, conn, from_log=True), None
    behind, last = None, _last_seen(conn)
    if last is None:  # a view but no sign of when the listener last wrote it: the log, as the CLI computes it
        return T.load(positions, conn, from_log=True), 0.0
    if not of_listen.fresh(conn):
        behind = last
    anchor = min(now, last)  # the tail starts NEW_H and CLOSED_H before the listener's last sign of life (of_herdr on
    # PR 119: anchored at now, an ask filed between the view's last flush and now - NEW_H showed nowhere)
    try:
        closed = _closures(conn, anchor)
    except Exception:  # noqa: BLE001
        closed = {}
    reopened = [k[1] for k, v in closed.items() if isinstance(k, tuple) and v[0] == "reopened"]
    try:
        groups = groups + _new_groups(conn, anchor, reopened)
    except Exception:  # noqa: BLE001 - the view alone
        pass
    data = T.shape(groups, positions)
    sending = set(sending)
    tasks_done = {k[1]: v for k, v in closed.items() if isinstance(k, tuple)}
    out = []
    for label, items in data:
        kept = []
        for x in items:
            ids = x.get("ids") or []
            if ids and all(i in sending for i in ids):
                continue
            if x.get("task") and ids:
                aid = ids[0]
                done = [v for t, v in tasks_done.items() if aid == t or aid.endswith(t)]
                last = max([closed.get(aid)] + done, key=lambda v: v[1] if v else "", default=None) if (
                    closed.get(aid) or done) else None
                if last and last[0] in ("done", "withdrawn"):
                    continue
                if last and last[0] == "claimed" and not x.get("claimed_at"):
                    x = dict(x, claimed_at=last[1])
                kept.append(x)
                continue
            if ids and all(i in closed and closed[i][0] in ("answered", "claimed", "withdrawn") for i in ids):
                continue
            kept.append(x)
        out.append((label, kept))
    return out, behind


def screen_list(conn, positions=(), failed=(), now=None):
    """What the screen's loader hands to its thread: (data, overlay, extra), extra with behind, the drafts archived
    and the unsent ones for D; everything read from disk here, never on the screen's thread."""
    import of_asks_answer
    now = now or time.time()
    failed = set(failed)  # a send that failed in this window is not on its way: its card shows, its draft goes to D
    way = on_their_way(now) - failed
    data, behind = queue(conn, positions, now=now, sending=way)
    # the asks still open, those whose answer is on its way among them: their drafts are never archived (of_herdr on
    # PR 119: a draft on its way went to the archive, and D had nothing to send when the send failed)
    open_ids = {i for _, g in data for x in g for i in x.get("ids") or []} | way
    archived = [] if behind is not None else of_asks_answer.archive_drafts(open_ids)  # a current list only
    drafts = [d for d in of_asks_answer.unsent_drafts()
              if now - float(d.get("saved_at") or 0) >= SENDING_S  # on their way: not unsent
              or set(d.get("ask_ids") or [d.get("ask_id")]) & failed]  # a failed one at once
    return data, overlay(conn, now), {"behind": behind, "archived": archived, "drafts": drafts}


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


def context_rows(c, width):
    """The agent's side of a card, for the third column: what it is doing (its mandate and last turns), its last
    message before the question, the links."""
    wrap = T._wrap
    rows = []
    doing = c.get("doing") or {}
    if doing.get("mandate") or doing.get("turns"):
        rows.append((tr("What the agent is doing:"), "head"))
        if doing.get("mandate"):
            rows += [(ln, "dim") for ln in wrap(doing["mandate"], width)]
        for t in doing.get("turns") or []:
            rows += [(ln, "dim") for ln in wrap(f"· {t}", width)]
        rows.append(("", None))
    if c.get("context"):
        rows += [(tr("The agent's last message before the question:"), "head")]
        rows += [(ln, "dim") for ln in wrap(c["context"], width)]
    if c.get("links"):
        rows += [("", None)] + [(ln, "dim") for ln in wrap(tr("Links: ") + "  ".join(c["links"]), width)]
    return rows


def card_rows(c, item, width, overlay_note=None, context=True):
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
            rows += [("", None), (tr("Answered in its pane: g goes there."), "dim")]
    if context and c.get("context"):
        rows += [("", None), (tr("The agent's last message before the question:"), "head")]
        rows += [(ln, "dim") for ln in wrap(c["context"], width)]
    if context and c.get("links"):
        rows += [("", None)] + [(ln, "dim") for ln in wrap(tr("Links: ") + "  ".join(c["links"]), width)]
    return rows


class Outgoing:
    """Answers and marks on their way: each waits UNDO_S for u, then is sent in a thread; its row stays, marked,
    until it is recorded, and a failure brings it back in red (the design: the card leaves only after the record)."""

    def __init__(self, answer, act, handoff=None):
        self.answer, self.act, self.handoff = answer, act, handoff
        self.pending, self.hidden, self.errors, self.undone = [], set(), {}, None

    def queue(self, item, what, text=None, written=False):
        k = key_of(item)
        self.errors.pop(k, None)
        self.pending.append({"key": k, "item": item, "what": what, "text": text, "at": time.time(), "started": False,
                             "written": written,
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
        """Take back the last answer still in its window: its key; self.undone keeps the whole entry (its text)."""
        p = next((p for p in reversed(self.pending) if not p["started"]), None)
        if not p:
            return None
        self.pending.remove(p)
        self.undone = p
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
                 studio=None, drafts=None, seq=None, initial=None, chat=None, archive=None, timing=None,
                 reply_store=None, writer=None):
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
        self.attr, self.lw, self.cx, self.xtop, self.tab_rects = {}, 0, 0, 0, []
        self.reply, self.reply_drafts, self.cursor = None, {}, None  # the answer being written under its card
        self.store = reply_store  # where Esc's drafts outlive the window (ReplyDrafts)
        self.confirm_reply = None  # the field a written answer's confirm came from: any key but yes goes back to it
        self.writer = writer  # where the drafts' disk writes go (Writer); None: at once
        self.behind = None  # the listener's last heartbeat when it is not fresh (queue's note)
        self.pending_drafts = None  # unsent answers, as the last load found them (no disk read per frame)
        self.started = time.time()
        if reply_store:
            try:
                self.reply_drafts = dict(reply_store.load())
            except Exception:  # noqa: BLE001 - a broken folder never keeps the screen from opening
                self.reply_drafts = {}
        self.timing = timing  # {name: time.time()} of this opening's first frame, true list and full card

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
        data, over = self.bg["data"][:2]
        extra = self.bg["data"][2] if len(self.bg["data"]) > 2 else {}
        self.bg["data"] = None
        self.mark("list")
        self.behind = extra.get("behind")
        if "drafts" in extra:
            self.pending_drafts = extra["drafts"]
        self.items = [x for _, g in data for x in g]
        self.over = over
        open_ids = {i for x in self.items for i in x.get("ids") or []}
        gone = list(extra.get("archived") or []) or (self.archive(open_ids) if self.archive else [])
        # a draft whose ask closed is put aside, only on a list the listener keeps current (a late one may lack it)
        put_aside = [] if self.behind is not None else [k for k in self.reply_drafts if k not in open_ids
                                                        and not (self.reply and self.reply["key"] == k)]
        for k in put_aside:
            self.reply_drafts.pop(k, None)
            if self.store:
                if self.writer:
                    self.writer.put(lambda k=k: self.store.archive(k))
                else:
                    self.store.archive(k)
        gone = list(gone or []) + put_aside
        if gone:
            self.msg = tr("{n} draft(s) archived: the question is closed already ({ids})", n=len(gone),
                          ids=", ".join(str(g)[-8:] for g in gone))

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
            if self.mode == "reply" and self.reply and self.reply["key"] == k:
                self.msg = tr("The card you are answering was closed elsewhere: Esc keeps your text as a draft.")
        else:
            idx = 0
        self.sel[self.cur] = keys[idx] if keys else None
        self.row[("idx", self.cur)] = idx
        return its, idx

    def card(self, item):
        """The full card, loaded in the background; meanwhile a placeholder from the list item."""
        k = key_of(item)
        c = self.cards.get(k)
        partial = c is not None and c.get("partial") and time.time() - c.get("at", 0) > PARTIAL_RETRY_S
        if c is not None and not partial:
            return c
        if k not in self.loading and self.card_of:
            self.loading.add(k)

            def work():
                try:
                    new = self.card_of(item)
                except Exception as e:  # noqa: BLE001
                    new = {"question": item.get("text") or "", "options": [], "answerable": False,
                           "context": tr("could not load: {why}", why=str(e)[:80]), "who": item.get("who")}
                new["at"] = time.time()
                self.cards[k] = new
                self.loading.discard(k)
            threading.Thread(target=work, daemon=True).start()
        if c is not None:  # a card built before the registry loaded: its question and options, until the full one
            return c
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
        tabs = self.tabs(g)
        self.tab_rects = []
        full = {s: f" ● {stream_name(s, self.search or '')} {len(g.get(s) or [])} " for s in tabs}
        short = sum(T.dwidth(v) + 1 for v in full.values()) > w - 1  # narrow: the others as their dot and count
        for s in tabs:
            label = full[s] if s == self.cur or not short else f" ● {len(g.get(s) or [])} "
            a = self.attr.get(COLOR[s], 0)
            if s == self.cur:
                a |= curses.A_REVERSE | curses.A_BOLD
            self.put(0, x, label, a)
            self.tab_rects.append((x, x + T.dwidth(label), s))
            x += T.dwidth(label) + 1
        if self.bg["error"]:
            at = time.strftime("%H:%M", time.localtime(self.bg["good_at"])) if self.bg["good_at"] else "—"
            self.put(1, 0, tr("! {why}: the last good list, from {at}; retrying", why=self.bg["error"], at=at),
                     self.attr.get("red", 0) | curses.A_BOLD)
        elif self.bg["good_at"] is None and time.time() - self.started > 3:
            self.put(1, 0, tr("The list is the last opening's; the current one is loading…"), self.attr.get("amber", 0))
        elif self.behind is not None:
            at = time.strftime("%H:%M", time.localtime(self.behind)) if self.behind else "—"
            self.put(1, 0, tr("The listener is behind (last seen {at}): answers hide their cards at once, a new card "
                              "may come late.", at=at), curses.A_DIM)
        body_top, body_h = 2, max(3, h - 5)
        narrow = w < NARROW_W
        item = its[idx] if its else None
        if self.mode == "reply":
            item = self.reply["item"]
        if narrow:
            if item:
                self.put(1, 0, f"‹ {idx + 1} / {len(its)} ›  " + tr("↑↓ page · ←→ stream"), curses.A_DIM)
            card_x, card_w = 1, max(20, w - 3)
        else:
            lw = max(30, min(60, w * 2 // 5)) if w < WIDE_W else max(48, min(64, w * 28 // 100))
            self.draw_list(its, idx, body_top, body_h, lw)
            for y in range(body_top, body_top + body_h):
                self.put(y, lw + 1, "│", curses.A_DIM)
            above, below = self.row.get(("more", self.cur), (0, 0))
            if above:
                self.put(body_top, lw + 1, "▲", curses.A_BOLD)
            if below:
                self.put(body_top + body_h - 1, lw + 1, "▼", curses.A_BOLD)
            card_x, card_w = lw + 3, max(20, w - lw - 5)
        self.lw, self.cx = (0 if narrow else lw), 0
        ctx = None
        if w >= WIDE_W:  # the third column: the agent's context beside the card
            card_w = (w - card_x - 3) * 55 // 100
            self.cx = card_x + card_w + 2
            ctx = (self.cx, w - self.cx - 1)
            for y in range(body_top, body_top + body_h):
                self.put(y, self.cx - 2, "│", curses.A_DIM)
        if item:
            self.draw_card(item, body_top, body_h, card_x, card_w, ctx)
            if self.mode == "main" and key_of(item) in self.cards and len(self.loading) < 2:
                for j in (idx + 1, idx - 1):  # the next and the previous card load while he reads this one
                    if 0 <= j < len(its) and key_of(its[j]) not in self.cards:
                        self.card(its[j])
        elif not self.bg["busy"] or self.items:
            self.put(body_top + 1, card_x, tr("Nothing here.") if self.cur != "search" else tr("No card matches."),
                     curses.A_DIM)
        else:
            self.put(body_top + 1, card_x, tr("loading…"), curses.A_DIM)
        # bottom: the status line, then the keys
        line = self.out.undo_line() or self.overlay_line() or self.msg
        pend = self.pending_drafts if self.pending_drafts is not None else (self.drafts() if self.drafts else [])
        if not line and pend:
            line = tr("{n} unsent answer(s) kept as drafts: D sends them", n=len(pend))
        if line:
            self.put(h - 3, 0, line, self.attr.get("red", 0) if line.startswith(tr("not sent: {why}", why=""))
                     else curses.A_BOLD)
        self.put(h - 2, 0, keys_line(w, self.mode), curses.A_DIM)
        if self.mode == "reply" and self.cursor and hasattr(self.scr, "move"):
            try:
                self.scr.move(*self.cursor)
            except curses.error:
                pass
        self.scr.refresh()
        self.mark("frame")
        return its, idx

    def draw_list(self, its, idx, top_y, height, width):
        top = self.row.get(self.cur, 0)
        k = key_of(its[idx]) if its else None
        anchor = self.row.get(("anchor", self.cur))
        if anchor and anchor[0] == k:
            # the same card keeps its row when cards arrive above it, also when the queue fits the screen (the design:
            # the list never jumps while he reads; of_herdr's harness, 2026-11-12): what went above is marked ▲
            top = max(0, min(idx - anchor[1], idx))
        else:
            top = max(0, min(top, idx, max(0, len(its) - height)))
        if idx >= top + height:
            top = idx - height + 1
        self.row[self.cur], self.row[("anchor", self.cur)] = top, (k, idx - top)
        self.row[("more", self.cur)] = (top, max(0, len(its) - top - height))
        for i, x in enumerate(its[top:top + height]):
            k = key_of(x)
            st = self.out.state(k)
            age = row_age(x)
            title = " ".join((x.get("text") or "").split())
            if width >= WHO_W:  # room for who asks
                who = T.cut(", ".join(x.get("who") or []), 14)
                title = who + " " * (15 - T.dwidth(who)) + title
            tail = ""
            a = 0
            if st and st[0] in ("pending", "sending"):
                tail, a = " → " + (st[1]["text"] or {"answered": tr("done"), "withdraw": tr("withdrawn")}.get(
                    st[1]["what"], "")), curses.A_DIM
            elif st and st[0] == "failed":
                tail, a = f"  ✗ {st[1]}", self.attr.get("red", 0)
            elif x.get("claimed_at"):
                tail = "  ✓"
            elif k in self.reply_drafts:
                tail = "  ✎"  # an answer he began and put aside: r goes on with it
            room = max(4, width - 3 - T.dwidth(age) - 1)
            tail = T.cut(tail, room // 2) if tail else ""  # the mark stays in sight: a long title is cut, not it
            text = T.cut(title, max(4, room - T.dwidth(tail))) + tail
            y = top_y + i
            sel = top + i == idx
            self.put(y, 0, "●", self.attr.get(COLOR[self.cur if self.cur != "search" else
                                                     stream_of(x)], 0))
            self.put(y, 2, text + " " * max(0, room - T.dwidth(text)) + " " + age,
                     (curses.A_REVERSE if sel else 0) | a)

    def overlay_line(self):
        if self.mode == "confirm" and self.confirm:
            item, n, text, _ = self.confirm
            if self.confirm_reply:
                return tr("Money or irreversible: press y to send «{text}» to {who}; any other key takes you back to "
                          "your answer", text=T.cut(text, 50), who=", ".join(item.get("who") or []))
            return tr("Money or irreversible: press {n} again (or y) to send «{text}» to {who}; any other key cancels",
                      n=n, text=T.cut(text, 50), who=", ".join(item.get("who") or []))
        return None

    def draw_card(self, item, top_y, height, x0, width, ctx=None):
        c = self.card(item)
        k = key_of(item)
        note = None
        o = next((self.over[i] for i in item.get("ids") or [] if i in self.over), None)
        if o and o[0] == "later":
            note = tr("snoozed until {t}", t=time.strftime("%d.%m %H:%M", time.localtime(o[1])))
        elif o and o[0] == "handed":
            note = tr("handed to {p}", p=o[1])
        if not c.get("loading"):
            self.mark("card")
        rows = card_rows(c, item, width, note, context=ctx is None)
        if ctx and self.mode in ("main", "reply"):
            side = context_rows(c, ctx[1])
            if self.note.get("key") != k:
                self.xtop = 0
            self.xtop = max(0, min(self.xtop, max(0, len(side) - height)))
            for i, (ln, kind) in enumerate(side[self.xtop:self.xtop + height]):
                self.put(top_y + i, ctx[0], ln, {"head": curses.A_UNDERLINE, "dim": curses.A_DIM}.get(kind, 0))
        if self.mode == "help":
            rows = [(ln, "text") for ln in help_rows()]
        elif self.mode == "snooze":
            rows = [(tr("Snooze until:"), "title"), ("", None), ("1  " + tr("in an hour"), "option"),
                    ("2  " + tr("tomorrow at 9:00"), "option"), ("3  " + tr("next Monday at 9:00"), "option"),
                    ("", None), (tr("Esc cancels"), "dim")]
        if self.note.get("key") != k or (self.note.get("mode") != self.mode
                                         and "reply" not in (self.mode, self.note.get("mode"))):
            self.ctop = 0  # a new card or a help screen starts at the top; writing an answer keeps the place he read
        self.note = {"key": k, "mode": self.mode}
        style = {"title": curses.A_BOLD, "dim": curses.A_DIM, "why": self.attr.get("amber", 0),
                 "head": curses.A_UNDERLINE, "option": 0, "recommended": self.attr.get("amber", 0) | curses.A_BOLD,
                 "text": 0}
        if self.mode == "reply":
            return self.draw_reply(rows, style, top_y, height, x0, width)
        opts = [r for r in rows if r[1] in ("option", "recommended")]
        body, foot = rows, []
        if self.mode == "main" and opts and len(rows) > height and len(opts) <= max(3, height // 2):
            # the options are what he acts on: kept at the foot of the card while the text above them scrolls
            # (of_conductor, from his first trial: a long card showed only its first option)
            body = [r for r in rows if r[1] not in ("option", "recommended")]
            while body and not body[-1][0]:
                body.pop()
            foot = opts
        bh = height - (len(foot) + 1 if foot else 1 if len(rows) > height else 0)
        self.ctop = max(0, min(self.ctop, max(0, len(body) - bh)))
        for i, (ln, kind) in enumerate(body[self.ctop:self.ctop + bh]):
            self.put(top_y + i, x0, ln, style.get(kind, 0))
        if len(body) > bh:  # where the text is, on the row between it and the options
            self.put(top_y + bh, x0, tr("lines {a}-{b} of {n} · J/K or Space scroll", a=self.ctop + 1,
                                        b=min(len(body), self.ctop + bh), n=len(body)), curses.A_DIM)
        for i, (ln, kind) in enumerate(foot):
            self.put(top_y + bh + 1 + i, x0, ln, style.get(kind, 0))

    def draw_reply(self, rows, style, top_y, height, x0, width):
        """The answer written under the card, which stays in sight and scrolls (↑↓, PgUp PgDn, the wheel) while he
        types (the founder, 2026-11-12: the editor over the whole screen hid a long question he answers with thought).
        The editor grows with the text up to half the card's height; the card takes the rest."""
        ed = self.reply["ed"]
        tw = self.reply["tw"] = max(4, width - 2)
        erows = ed.rows(tw)
        eh = max(3, min(len(erows) + 1, height // 2))
        qh = max(1, height - eh - 1)
        over = len(rows) > qh
        bh = qh - 1 if over else qh
        self.ctop = max(0, min(self.ctop, max(0, len(rows) - bh)))
        for i, (ln, kind) in enumerate(rows[self.ctop:self.ctop + bh]):
            self.put(top_y + i, x0, ln, style.get(kind, 0))
        if over:
            self.put(top_y + bh, x0, tr("lines {a}-{b} of {n} · PgUp PgDn scroll", a=self.ctop + 1,
                                        b=min(len(rows), self.ctop + bh), n=len(rows)), curses.A_DIM)
        hy = top_y + qh
        head = "── " + tr("Your answer to {who}", who=", ".join(self.reply["item"].get("who") or [])) + " "
        head = T.cut(head, width)
        self.put(hy, x0, head + "─" * max(0, width - T.dwidth(head)), curses.A_BOLD)
        ri, cx = ed.cursor(erows)
        top = self.reply.get("top", 0)  # the field's own scroll: the cursor's row stays in sight
        top = min(top, ri) if ri < top else (ri - eh + 1 if ri >= top + eh else top)
        top = max(0, min(top, max(0, len(erows) - eh)))
        self.reply["top"] = top
        for i, (li, a, b) in enumerate(erows[top:top + eh]):
            self.put(hy + 1 + i, x0 + 1, ed.lines[li][a:b])
        if top:
            self.put(hy + 1, x0 + width - 1, "▲", curses.A_BOLD)
        if top + eh < len(erows):
            self.put(hy + eh, x0 + width - 1, "▼", curses.A_BOLD)
        self.cursor = (hy + 1 + ri - top, x0 + 1 + cx)

    def start_reply(self, item):
        """r: the editor opens under the card, with the draft he left on it (Esc keeps one)."""
        k = key_of(item)
        c = self.cards.get(k)
        if not item.get("task") and (c is None or c.get("loading")):
            self.msg = tr("loading…")
            return
        if not item.get("task") and not c.get("answerable"):
            self.msg = tr("Answered in its pane: g goes there.")
            return
        self.mode, self.msg = "reply", ""
        self.reply = {"key": k, "item": item, "ed": Editor(self.reply_drafts.get(k) or ""), "top": 0}
        curses.curs_set(1)

    def keep_draft(self, key, text):
        """Esc's draft, on disk too; None or blank removes it. False when the disk refused it (said on screen)."""
        if (text or "").strip():
            self.reply_drafts[key] = text
        else:
            self.reply_drafts.pop(key, None)
        if self.store:
            def save():
                try:
                    self.store.save(key, text)
                    return True
                except OSError as e:
                    self.msg = tr("not saved: {why}", why=f"{type(e).__name__}: {e}"[:80])
                    return False
            if self.writer:
                self.writer.put(save)
                return True
            return save()
        return True

    def end_reply(self):
        self.mode, self.reply, self.cursor = "main", None, None
        curses.curs_set(0)

    def reply_key(self, ch, h):
        """One key while he writes: the arrows, Home and End move the cursor (wrapped rows included); PgUp, PgDn and the
        wheel scroll the card; Enter (and Shift+Enter, which a terminal gives as Enter) a new line, blank ones too;
        Ctrl+D sends, the one key that does (the founder, 2026-11-12: two Shift+Enters sent); Esc keeps the draft."""
        r = self.reply
        ed = r["ed"]
        if ch in ("\x1b", 27):
            text = ed.text()
            if self.keep_draft(r["key"], text) and text.strip():
                self.msg = tr("The answer is kept as a draft: r goes on with it.")
            self.end_reply()
        elif ch in ("\x04", 4):
            text = ed.text().strip()
            item, c = r["item"], self.cards.get(r["key"])
            # what was typed or pasted after the send never reaches the screen's keys (of_herdr's review of PR 98)
            curses.flushinp()
            if not text:
                return True
            self.end_reply()
            if needs_confirm(item, c):  # the text stays a draft until he confirms, and any other key brings it back
                self.keep_draft(r["key"], ed.text())
                self.mode, self.confirm, self.confirm_reply = "confirm", (item, "y", text, "answer"), r
                curses.curs_set(0)
            else:
                self.keep_draft(r["key"], None)
                self.send(item, "answer", text, written=True)
        elif ch in ("\n", "\r", 10, 13, curses.KEY_ENTER):
            ed.newline()
        elif ch in (curses.KEY_BACKSPACE, "\x7f", "\b", 127, 8):
            ed.backspace()
        elif ch == curses.KEY_DC:
            ed.delete()
        elif ch == curses.KEY_LEFT:
            ed.left()
        elif ch == curses.KEY_RIGHT:
            ed.right()
        elif ch in (curses.KEY_HOME, "\x01"):  # Ctrl+A too
            ed.home()
        elif ch in (curses.KEY_END, "\x05"):  # Ctrl+E too
            ed.end()
        elif ch in (curses.KEY_UP, curses.KEY_DOWN):
            ed.vertical(-1 if ch == curses.KEY_UP else 1, r.get("tw") or 1000)  # the width it was drawn at
        elif ch == curses.KEY_PPAGE:
            self.ctop = max(0, self.ctop - max(1, h // 3))
        elif ch == curses.KEY_NPAGE:
            self.ctop += max(1, h // 3)
        elif isinstance(ch, str) and ch.isprintable():
            ed.insert(ch)
        elif isinstance(ch, int) and 32 <= ch < 127:
            ed.insert(chr(ch))
        return True

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
                self.msg = tr("Answered in its pane: g goes there.")
                return
            opts = c.get("options") or []
            if not 1 <= n <= min(9, len(opts)):
                return
            what, text = "answer", opts[n - 1]
        if needs_confirm(item, self.cards.get(k)):
            self.mode, self.confirm = "confirm", (item, n, text, what)
            return
        self.send(item, what, text)

    def send(self, item, what, text, written=False):
        self.out.queue(item, what, text if what == "answer" else None, written=written)
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
        if self.mode == "reply":
            if (up or down) and self.cx and x >= self.cx:
                self.xtop = max(0, self.xtop + (-3 if up else 3))
            elif up or down:
                self.ctop = max(0, self.ctop + (-3 if up else 3))
            return
        if y == 0 and b & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED):  # a click on a stream's tab
            hit = next((st for x0, x1, st in self.tab_rects if x0 <= x < x1), None)
            if hit:
                self.cur = hit
            return
        on_list = self.lw and x < self.lw
        if (up or down) and on_list and its:
            self.sel[self.cur] = key_of(its[step(len(its), idx, -1 if up else 1, wrap=False)])
        elif (up or down) and self.cx and x >= self.cx:  # over the context column: it scrolls
            self.xtop = max(0, self.xtop + (-3 if up else 3))
        elif up or down:
            self.ctop = max(0, self.ctop + (-3 if up else 3))
        elif on_list and its and b & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED) and y >= 2:  # a press
            row = self.row.get(self.cur, 0) + y - 2
            if 0 <= row < len(its):
                self.sel[self.cur] = key_of(its[row])

    def handle(self, ch):
        """One key. Returns False to leave."""
        h, w = self.scr.getmaxyx()
        if self.mode == "reply" and ch != curses.KEY_MOUSE:  # straight to the editor: no sorting of the streams per key
            return self.reply_key(ch, h)
        g = self.groups()
        its, idx = self.current(g)
        item = its[idx] if its else None
        if ch == curses.KEY_MOUSE:
            if self.mode in ("main", "reply"):
                self.mouse(its, idx)
            return True
        if self.mode == "confirm":
            item_, n, text, what = self.confirm
            back, self.mode, self.confirm, self.confirm_reply = self.confirm_reply, "main", None, None
            c = char_of(ch)
            if c == str(n) or c in YES:
                if back:
                    self.keep_draft(back["key"], None)
                self.send(item_, what, text, written=bool(back))
            elif back:  # a written answer: back in its field, whole, with the cursor where it was (the founder,
                # 2026-11-12: three answers were lost to a key that was not y, a Russian layout or a change of mind)
                self.mode, self.reply = "reply", back
                curses.curs_set(1)
                self.msg = tr("Not sent: your answer is back in the field. Ctrl+D sends it, Esc keeps it as a draft.")
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
        elif ch in (curses.KEY_DOWN, ord("j")) and its:
            self.sel[self.cur] = key_of(its[step(len(its), idx, 1)])
        elif ch in (curses.KEY_UP, ord("k")) and its:
            self.sel[self.cur] = key_of(its[step(len(its), idx, -1)])
        elif ch == curses.KEY_NPAGE and its:
            self.sel[self.cur] = key_of(its[step(len(its), idx, max(1, h - 6), wrap=False)])
        elif ch == curses.KEY_PPAGE and its:
            self.sel[self.cur] = key_of(its[step(len(its), idx, -max(1, h - 6), wrap=False)])
        elif ch == curses.KEY_HOME and its:
            self.sel[self.cur] = key_of(its[0])
        elif ch == curses.KEY_END and its:
            self.sel[self.cur] = key_of(its[-1])
        elif ch in (ord("J"), getattr(curses, "KEY_SF", -2)):
            self.ctop += 1
        elif ch in (ord("K"), getattr(curses, "KEY_SR", -2)):
            self.ctop = max(0, self.ctop - 1)
        elif ch == ord(" "):
            self.ctop += max(1, h - 7)
        elif ch in (9, curses.KEY_BTAB, curses.KEY_RIGHT, curses.KEY_LEFT):
            # ← → and Tab: the streams that hold anything (the founder, ~21:10Z: switch them with the arrows); the empty
            # ones show their 0
            tabs = [s for s in self.tabs(g) if g.get(s) or s == self.cur]
            self.cur = tabs[step(len(tabs), tabs.index(self.cur), 1 if ch in (9, curses.KEY_RIGHT) else -1)]
        elif ord("1") <= ch <= ord("9") and item:
            self.answer(item, ch - ord("0"))
        elif ch == ord("u"):
            k = self.out.undo()
            if k is not None:
                self.msg = tr("cancelled")
                for s in self.tabs(self.groups()):
                    if any(key_of(x) == k for x in self.groups().get(s) or []):
                        self.cur, self.sel[s] = s, k
                p = self.out.undone or {}
                if p.get("written") and p.get("text"):  # a written answer taken back: in its field again, never lost
                    self.keep_draft(k, p["text"])
                    self.mode = "reply"
                    self.reply = {"key": k, "item": p["item"], "ed": Editor(p["text"]), "top": 0}
                    curses.curs_set(1)
                    self.msg = tr("Taken back: your answer is in the field again.")
        elif ch == ord("r") and item and item.get("ids"):
            self.start_reply(item)
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
            if not names:
                self.msg = tr("Positions are still loading: try again in a moment.")
                return True
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
        elif ch in (ord("g"), ord("o")) and item and item.get("who"):  # g: the founder's key (2026-11-12); o too
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
            pend = self.pending_drafts if self.pending_drafts is not None else self.drafts()  # never one on its way
            if pend:
                d = pend[0]
                self.out.queue(dict(d["item"], first=None), "answer", d["text"])
        return True

    def autosave(self):
        """While he writes, the draft on disk follows the field at most a second behind: herdr closing the popup ends
        the process without a goodbye, and what he typed is kept (the founder, 2026-11-12: no path loses typed text)."""
        r = self.reply
        if self.mode != "reply" or not r or not self.store:
            return
        text = r["ed"].text()
        if text != r.get("saved", self.reply_drafts.get(r["key"], "")) and time.time() - r.get("saved_at", 0) >= 1.0:
            r["saved"], r["saved_at"] = text, time.time()
            self.keep_draft(r["key"], text)

    def mark(self, what):
        if self.timing is not None and what not in self.timing:
            self.timing[what] = time.time()

    def key(self):
        """The next key, -1 when none came in time. While he writes and at a confirm, whole characters (get_wch): getch
        gives a Cyrillic letter as its bytes."""
        if self.mode in ("reply", "confirm") and hasattr(self.scr, "get_wch"):
            try:
                return self.scr.get_wch()
            except curses.error:
                return -1
        return self.scr.getch()

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
            self.autosave()
            self.draw()
            ch = self.key()
            if ch == -1:
                continue
            if ch == curses.KEY_RESIZE:
                continue
            if not self.handle(ch):
                if self.writer:
                    self.writer.flush()
                return
            self.drain()

    def drain(self):
        """In the field, every key already waiting is taken before the next frame: herdr pastes as keys, one per
        character, and a frame per key made a long paste cost seconds while herdr's popup held the whole session
        (of_herdr's review of PR 104). Stops when a key ends the field (Ctrl+D flushes the rest itself)."""
        if self.mode != "reply" or not hasattr(self.scr, "get_wch"):
            return
        self.scr.timeout(0)
        try:
            for _ in range(100000):
                if self.mode != "reply":
                    break
                try:
                    ch = self.scr.get_wch()
                except curses.error:
                    break
                if ch != curses.KEY_RESIZE:
                    self.handle(ch)
        finally:
            self.scr.timeout(T.SEQ_POLL_MS)


def keys_line(w, mode="main"):
    """The permanent line of keys, the longest form that fits: Tab, ? and Esc are always there; g goes to the agent
    (the founder, 2026-11-12: "add g, go to the agent"). While he writes: the editor's."""
    if mode == "reply":
        forms = [tr("Ctrl+D send · Enter new line · ↑↓←→ Home End move · PgUp PgDn scroll the card · "
                    "Esc keep as a draft"),
                 tr("Enter new line · Ctrl+D send · ↑↓←→ move · PgUp PgDn card · Esc draft"),
                 "Enter · Ctrl+D · ↑↓←→ · PgUp PgDn · Esc"]
        return next((f for f in forms if T.dwidth(f) <= w - 1), forms[-1])
    forms = [tr("↑↓ select · ←→ stream · 1-9 answer · r write · g go to the agent · h snooze · d hand over · / search · "
                "? help · Esc back"), tr("↑↓ select · ←→ stream · 1-9 answer · r write · g agent · ? help · Esc back"),
             "↑↓ ←→ 1-9 r g h d / ? Esc"]
    return next((f for f in forms if T.dwidth(f) <= w - 1), forms[-1])


def help_rows():
    return [tr("Keys"), "",
            "↑ ↓  j k     " + tr("select; wraps from the first card to the last"),
            "PgUp PgDn    " + tr("a page of the queue"),
            "Home End     " + tr("the first, the last card"),
            "← → Tab      " + tr("the next, the previous stream (a click on its tab too)"),
            "1-9          " + tr("answer with that option (money or irreversible: press it twice)"),
            "r            " + tr("write your own answer under the card: Ctrl+D sends, Esc keeps it as a draft"),
            "u            " + tr("take back the last answer while its seconds run"),
            "h            " + tr("snooze: in an hour, tomorrow at 9:00, next Monday at 9:00 (in Snoozed: back now)"),
            "d            " + tr("hand it to a position, which gets the card in its pane to settle"),
            "/            " + tr("search the questions and who asks"),
            "J K Space    " + tr("scroll the card"),
            "g o          " + tr("go to the asking agent's pane"),
            "a w n        " + tr("answered elsewhere, withdraw, not a question"),
            "b c C s      " + tr("side chat, copy the answer, copy the card, Studio"),
            "Esc q        " + tr("back, close"), "",
            tr("Any key closes this help.")]


def main(positions, conn, of_path, pane_of=None, after=None, refresh=None):
    """The window: the same answer path as the classic popup (of_asks_tui.main), its cache for the first frame."""
    import of_asks_answer
    import of_listen
    timing = {"start": time.time()}
    os.environ.setdefault("ESCDELAY", "25")

    registry, quick = threading.Event(), set()

    def warm():  # the registry, from the start: a card needs it only for who asks (system, mandate, organization)
        _quiet(lambda: T._positions(positions))
        registry.set()
    threading.Thread(target=warm, daemon=True).start()

    def card_of(it):  # in a thread: its own store connection
        """The card; before the registry loaded (a second of YAML, several under load), the question and its options
        at once and the rest when it is there: the options he acts on never wait for it (2026-11-12)."""
        if key_of(it) in quick:
            registry.wait()  # its quick card is on screen: the full one, once the registry is there
        own = reader()
        try:
            ready = registry.is_set()
            if not ready:
                quick.add(key_of(it))
            c = of_asks_answer.card(own, it, T._positions(positions) if ready else [])
            c["risk"] = group_risk(own, c.get("ids") or it.get("ids"))
            if not ready:
                c["partial"] = True
            return c
        finally:
            own.close()

    reconciled = []

    def loader():  # in a thread: the true list, written to the cache for the next first frame
        """The queue from its one source (queue: the view and the log's tail, never the whole log), the registry's
        rows once it is there, and what the screen would otherwise read from disk on its own thread."""
        own = reader()
        try:
            data, over, extra = screen_list(own, T._positions(positions) if registry.is_set() else [],
                                            failed=set(boxes[0].out.errors) if boxes else ())
        finally:
            own.close()
        T.write_cache(data)
        if refresh and not reconciled:  # live sessions and the herdr counts, once, after the first true list
            reconciled.append(True)

            def reconcile():
                _quiet(refresh)
                timing.setdefault("reconcile", time.time())
            threading.Thread(target=reconcile, daemon=True).start()
        return data, over, extra

    def record(kind, payload):
        import of_events
        return of_events.emit(kind, payload)

    def handoff(pendings):
        T.to_outbox(pendings)
        T.spawn_sender(of_path)

    def studio(item, done):
        def work():
            own = reader()
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
    size, boxes, store, writer = [], [], ReplyDrafts(), Writer()

    def run(scr):
        size.extend(scr.getmaxyx())
        box = Inbox(scr, loader, card_of, answer, T.act_cli(of_path),
                    positions=lambda: T._positions(positions) if registry.is_set() else [],
              record=record, handoff=handoff, studio=studio, drafts=of_asks_answer.unsent_drafts, seq=of_listen.seq,
              initial=([x for _, g in first[0] for x in g], first[1]), chat=T.chat_screen,
              timing=timing, reply_store=store, writer=writer)
        boxes.append(box)
        box.run()
    try:
        curses.wrapper(run)
    finally:
        writer.flush()
        if boxes and boxes[0].reply:  # closed while he wrote: what he wrote is a draft
            _quiet(lambda: store.save(boxes[0].reply["key"], boxes[0].reply["ed"].text()))
        log_open(timing, size)
    return 0


def log_open(timing, size, path=None):
    """One line per opening in asks-open.log: the seconds from the start of the screen to its first frame, the true
    list, the first card with its options and the end of the background reconcile. The founder saw the screen open
    in 10 to 30 s in herdr (2026-11-12, of_conductor msg 01a1d09c-4562): this is the measure where he sees it."""
    import of_events
    path = path or os.path.join(of_events.STATE_DIR, "asks-open.log")
    t0 = timing.get("start")
    # where it ran: herdr's popup has HERDR_ENV and no pane id (herdr removes it from a popup), a herdr pane has
    # both, a plain terminal neither (of_herdr's note on PR 98)
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)), "size": size[:2],
           "herdr": os.environ.get("HERDR_ENV") == "1", "pane": os.environ.get("HERDR_PANE_ID")}
    row.update({k: round(v - t0, 2) for k, v in timing.items() if k != "start"})
    try:
        if os.path.exists(path) and os.path.getsize(path) > 512 * 1024:
            os.replace(path, path + ".1")
        with open(path, "a") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError:
        pass


def _quiet(fn):
    try:
        fn()
    except Exception:  # noqa: BLE001
        pass
