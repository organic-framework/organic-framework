"""`of asks --panel`: a small panel of the owner's questions, always in sight, pinnable in a view.

The founder, 2026-11-12 ~20:45Z (through of_conductor, msg 01a1cc5b): besides the popup, "a small panel with the
questions, so that one sees how many there are and of what, and a hint how to open the popup, or even a button to
open it with the mouse". It shows, read-only:
- the count of each stream, in its colour (the streams of bin/of_asks_inbox.py: Holds work, Money and production,
  For your hands, Everything else; Snoozed and Handed over when they hold anything);
- the longest wait and the top card of the most urgent stream;
- how to open the questions (the key herdr binds to `of asks --watch`, read from its config: "Ctrl-B, then a") and a
  button. A click on the button (also in a pane without the focus), Enter or o opens the popup over everything
  through OF's herdr plugin (of_herdr_plugin.open_questions, with this bin/of, so a trial build opens itself), or
  through `popup_open` in ~/.config/of/asks.yaml when the machine sets another opener; while herdr shows another
  popup, Settings or Copy mode the button's line says so.
It refreshes as the popup does (views.seq, a full reload every REFRESH_S) and adapts to the room it gets (the founder,
~21:08Z: "lots of room, more information; less room, less information"):
- under TALL_H rows: the counts, the longest wait, the first card and the button (one stream per line when narrow);
- from TALL_H rows: every stream that holds anything with its first cards (how long, who, what), as many as fit,
  shared out with the most urgent first; the button at the foot;
- from WIDE_W columns too: a second column with who waits on him (each position, its count and longest wait) and
  what he answered in the last RECENT_H hours. A click on a card row opens the questions too.
"""

import curses
import os
import shlex
import subprocess
import threading
import time

import of_asks_inbox as I
import of_asks_tui as T
from of_locale import tr


def herdr_config():
    try:
        import tomllib
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")  # as herdr reads it (of_herdr)
        with open(os.path.join(base, "herdr", "config.toml"), "rb") as fh:
            return tomllib.load(fh)
    except (OSError, ValueError, ImportError):
        return {}


def key_name(k):
    """ctrl+b -> Ctrl-B, shift+a -> Shift-A, a -> a."""
    parts = [p for p in str(k or "").split("+") if p]
    if len(parts) == 1:
        return parts[0]
    return "-".join(p.capitalize() if len(p) > 1 else p.upper() for p in parts)


def open_keys(cfg=None, of_path=None):
    """How the person opens the questions popup: "Ctrl-B, then a". The binding whose command runs this very bin/of
    first (a trial from a branch has its own key), else the first that runs `of asks --watch`."""
    cfg = herdr_config() if cfg is None else cfg
    prefix = key_name((cfg.get("keys") or {}).get("prefix") or "ctrl+b")
    binds = [b for b in (cfg.get("keys") or {}).get("command") or [] if "asks --watch" in str(b.get("command"))]
    mine = [b for b in binds if of_path and of_path in str(b.get("command"))]
    b = (mine or binds or [None])[0]
    if not b:
        return None
    key = str(b.get("key") or "")
    if key.startswith("prefix+"):
        return tr("{prefix}, then {key}", prefix=prefix, key=key_name(key[len("prefix+"):]))
    return key_name(key)


def opener():
    """The argv that opens the popup over everything (herdr's socket API, of_herdr), from ~/.config/of/asks.yaml
    `popup_open`; None when there is none yet."""
    import of_events
    try:
        import yaml
        with open(of_events.config_path("asks.yaml")) as fh:
            v = (yaml.safe_load(fh) or {}).get("popup_open")
    except (OSError, ImportError, ValueError, AttributeError):
        return None
    if not v:
        return None
    return shlex.split(v) if isinstance(v, str) else [str(x) for x in v]


TALL_H = 8      # from this many rows: the first cards of each stream
WIDE_W = 110    # from this many columns (and TALL_H rows): who waits on him and what he answered lately
RECENT_H = 24   # the answers listed are this many hours old at most


def recent_answers(conn, hours=RECENT_H, limit=12, now=None):
    """[{ts, who, text, answer}] the owner's own answers, newest first: from the popup, the conductor's chat or
    `of asks answered` in his terminal (a task he marked done)."""
    import json
    import of_asks_answer
    now = now or time.time()
    since = I.iso(now - hours * 3600)
    out = []
    for ts, pl in conn.execute("SELECT ts, payload FROM event WHERE name = 'of.agent_question_answered' AND ts >= ? "
                               "ORDER BY ts DESC LIMIT 400", (since,)):
        try:
            p = json.loads(pl or "{}")
        except ValueError:
            continue
        if p.get("by") not in ("owner", "founder") or p.get("verdict") or p.get("via") == "reconcile":
            continue
        q, _, who, _ = of_asks_answer.ask_details(conn, p.get("ask_id") or (p.get("ask_ids") or [None])[0])
        out.append({"ts": ts, "who": who or "?", "text": of_asks_answer.full_question(q, ""),
                    "answer": p.get("answer") or (" ".join(p["keys"]) if p.get("keys") else tr("done"))})
        if len(out) >= limit:
            break
    return out


def waiting_agents(items):
    """[(position, open cards, the longest wait's start)], the longest wait first."""
    by = {}
    for x in items:
        for w in x.get("who") or []:
            n, since = by.get(w, (0, None))
            s = x.get("since")
            by[w] = (n + 1, s if since is None or (s and s < since) else since)
    return sorted(((w, n, s) for w, (n, s) in by.items()), key=lambda t: t[2] or "~")


def summary(items, overlay=None, now=None):
    """{counts: {stream: n}, oldest: item or None, top: item or None} of the owner's queue."""
    g = I.split(items, overlay, now)
    waiting = [x for s in I.ORDER if s != "later" for x in g[s]]
    oldest = min(waiting, key=lambda x: T._ts(x.get("since")) or float("inf"), default=None)
    top = next((g[s][0] for s in I.ORDER if s not in ("later", "handed") and g[s]), None)
    return {"counts": {s: len(g[s]) for s in I.ORDER}, "shown": I.shown_streams(g), "oldest": oldest, "top": top}


class Panel:
    def __init__(self, scr, loader, seq=None, initial=None, keys=None, open_cmd=None, run_cmd=None, popup=None,
                 stale=None):
        self.scr, self.loader, self.seq = scr, loader, seq
        self.items, self.over = initial or ([], {})
        self.keys, self.open_cmd, self.popup, self.stale = keys, open_cmd, popup, stale
        self.run_cmd = run_cmd or (lambda argv: subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                                                 stderr=subprocess.DEVNULL, start_new_session=True))
        self.bg = {"data": None, "busy": False}
        self.button, self.msg, self.msg_at, self.attr = None, "", 0.0, {}
        self.recent, self.cards = [], {}  # the latest answers; screen row -> (x0, x1) of a card row
        self.last_seq, self.last_full = (seq() if seq else None), 0.0

    def refresh(self):
        if self.bg["busy"]:
            return
        self.bg["busy"], self.last_full = True, time.time()

        def work():
            try:
                self.bg["data"] = self.loader()
            except Exception:  # noqa: BLE001 - the last list stays on screen
                pass
            self.bg["busy"] = False
        threading.Thread(target=work, daemon=True).start()

    def put(self, y, x, text, attr=0):
        T._put(self.scr, y, x, text, attr)

    def header(self, s, w):
        """Row 0: the streams and their counts, full labels when they fit, else each as its dot and count."""
        title, gap = tr("Questions"), 3
        line = [(f"● {I.stream_name(st)} {s['counts'][st]}", st) for st in s["shown"]]
        if sum(T.dwidth(t) + gap for t, _ in line) + T.dwidth(title) + 2 > w - 1:
            line, gap = [(f"● {s['counts'][st]}", st) for st in s["shown"]], 2
            if sum(T.dwidth(t) + gap for t, _ in line) + T.dwidth(title) + 2 > w - 1:
                title = ""  # the narrowest: the dots and counts alone
        x = 0
        if title:
            self.put(0, x, title + "  ", curses.A_BOLD)
            x += T.dwidth(title) + 2
        for t, st in line:
            self.put(0, x, t, self.attr.get(I.COLOR[st], 0) | (curses.A_BOLD if s["counts"][st] else 0))
            x += T.dwidth(t) + gap

    def draw(self):
        h, w = self.scr.getmaxyx()
        s = summary(self.items, self.over)
        self.scr.erase()
        self.cards = {}
        if h >= TALL_H:
            self.draw_tall(s, h, w)
            by = h - 2
        else:
            by = min(self.draw_small(s, h, w), h - 2)
        self.draw_button(by, h, w)
        self.scr.refresh()

    def draw_small(self, s, h, w):
        y = 0
        streams = [(st, s["counts"][st]) for st in s["shown"]]
        line = [(f"● {I.stream_name(st)} {n}", st) for st, n in streams]
        fits_lines = 1 + len(line) + (3 if self.keys else 2) <= h - 1
        if sum(T.dwidth(t) + 3 for t, _ in line) <= w - 1 or not fits_lines:
            self.header(s, w)  # the streams on one line (each as its dot and count when the labels do not fit)
            y += 1
        else:  # narrow with rows to spare: one stream per line
            self.put(y, 0, tr("Questions"), curses.A_BOLD)
            y += 1
            for t, st in line:
                if y < h - 2:
                    self.put(y, 1, t, self.attr.get(I.COLOR[st], 0) | (curses.A_BOLD if s["counts"][st] else 0))
                    y += 1
        if s["oldest"] and y < h - 2:
            o = s["oldest"]
            self.put(y, 0, T.cut(tr("Longest wait: {age} · {who}: {text}", age=T._age(o.get("since")),
                                    who=", ".join(o.get("who") or []), text=" ".join((o.get("text") or "").split())),
                                 w - 1), curses.A_DIM)
            y += 1
        if s["top"] and s["top"] is not s["oldest"] and y < h - 2:
            t = s["top"]
            self.put(y, 0, T.cut(tr("First: {text}", text=" ".join((t.get("text") or "").split())), w - 1))
            y += 1
        if not s["oldest"] and y < h - 2:
            self.put(y, 0, tr("Nothing waits on you."), curses.A_DIM)
            y += 1
        return y

    def draw_tall(self, s, h, w):
        """Every stream with its first cards on the left; who waits and the latest answers on the right when wide,
        else below when rows are left."""
        self.header(s, w)
        bottom = h - 4  # the content's last row: the button sits on h - 2 with a row of air above it
        two = w >= WIDE_W
        lw = w * 3 // 5 if two else w
        g = I.split(self.items, self.over)
        streams = [st for st in I.ORDER if g[st]]
        y = 2
        room = bottom - y + 1 - len(streams)  # one label row per stream
        quota = {st: 0 for st in streams}
        while room > 0 and any(quota[st] < len(g[st]) for st in streams):  # the most urgent first, then round
            for st in streams:
                if room > 0 and quota[st] < len(g[st]):
                    quota[st] += 1
                    room -= 1
        who_w = max(10, min(20, lw // 6))
        for st in streams:
            if y > bottom:
                break
            more = len(g[st]) - quota[st]
            self.put(y, 0, f"● {I.stream_name(st)} {len(g[st])}" + (f"  (+{more})" if more > 0 else ""),
                     self.attr.get(I.COLOR[st], 0) | curses.A_BOLD)
            y += 1
            for x in g[st][:quota[st]]:
                if y > bottom:
                    break
                who = T.cut(", ".join(x.get("who") or []), who_w)
                row = f"  {I.row_age(x):>4}  {who}{' ' * (who_w - T.dwidth(who))}  " + " ".join((x.get("text") or "").split())
                self.put(y, 0, T.cut(row, lw - 1))
                self.cards[y] = (0, lw)
                y += 1
        if not streams:
            self.put(y, 0, tr("Nothing waits on you."), curses.A_DIM)
            y += 1
        if two:
            self.side(2, lw + 2, bottom, w - lw - 3)
        elif y + 3 <= bottom:
            self.side(y + 1, 0, bottom, w - 1)

    def side(self, y, x0, bottom, width):
        """Who waits on him, then what he answered lately, from row y down to bottom."""
        if width < 20 or y > bottom:
            return
        agents = waiting_agents(self.items)
        half = max(2, (bottom - y + 1) // 2)
        if agents:
            self.put(y, x0, tr("Waiting on you"), curses.A_BOLD)
            y += 1
            for who, n, since in agents[:max(1, half - 1)]:
                if y > bottom:
                    return
                self.put(y, x0, T.cut(f"{T._age(since):>4}  {who}" + (f"  ×{n}" if n > 1 else ""), width))
                y += 1
            y += 1
        if self.recent and y < bottom:
            self.put(y, x0, tr("Answered lately"), curses.A_BOLD)
            y += 1
            for a in self.recent:
                if y > bottom:
                    return
                t = time.strftime("%H:%M", time.localtime(T._ts(a["ts"]) or 0))
                self.put(y, x0, T.cut(f"{t}  {a['who']}: {' '.join(str(a['answer']).split())}", width), curses.A_DIM)
                y += 1

    def draw_button(self, by, h, w):
        label = " " + tr("Open the questions") + " "
        hint = tr("or {keys}", keys=self.keys) if self.keys else ""
        self.put(by, 0, label, curses.A_REVERSE | curses.A_BOLD)
        self.button = (by, 0, T.dwidth(label))
        below = by + 1
        x = T.dwidth(label) + 2
        if hint and x + T.dwidth(hint) <= w - 1:
            self.put(by, x, hint, curses.A_DIM)
            x += T.dwidth(hint) + 3
        elif hint and below < h - 1:  # narrow: the keys under the button
            self.put(below, 0, T.cut(hint, w - 1), curses.A_DIM)
            below += 1
        elif hint and h >= TALL_H:  # tall and narrow: the keys on the row of air above the button
            self.put(by - 1, 0, T.cut(hint, w - 1), curses.A_DIM)
        if self.msg and time.time() - self.msg_at < 6:
            if below < h - 1:
                self.put(below, 0, T.cut(self.msg, w - 1), curses.A_DIM)
            elif x < w - 10:  # tall: no row below the button, the note beside it
                self.put(by, x, T.cut(self.msg, w - 1 - x), curses.A_BOLD)

    def open(self):
        if self.open_cmd:
            try:
                self.run_cmd(self.open_cmd)
                self.msg = tr("opening the questions…")
            except Exception as e:  # noqa: BLE001
                self.msg = tr("could not open: {why}", why=f"{type(e).__name__}: {e}"[:60])
        elif self.popup:
            try:
                ok, why = self.popup()
            except Exception as e:  # noqa: BLE001
                ok, why = False, f"{type(e).__name__}: {e}"[:60]
            self.msg = tr("opening the questions…") if ok else (
                tr("herdr shows another popup, Settings or Copy mode: close it, then try again") if why == "ui_busy"
                else tr("could not open: {why}", why=why))
        else:
            self.msg = tr("Press {keys} to open them.", keys=self.keys) if self.keys else \
                tr("The questions open with of asks --watch.")
        self.msg_at = time.time()

    def handle(self, ch):
        if ch == curses.KEY_MOUSE:
            try:
                _, x, y, _, b = curses.getmouse()
            except curses.error:
                return True
            pressed = b & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED | curses.BUTTON1_RELEASED)
            on_button = self.button and y == self.button[0] and self.button[1] <= x < self.button[1] + self.button[2]
            on_card = y in self.cards and self.cards[y][0] <= x < self.cards[y][1]
            if pressed and (on_button or on_card):
                self.open()
        elif ch in (10, 13, curses.KEY_ENTER, ord("o")):
            self.open()
        elif ch == ord("q"):
            return False
        return True

    def run(self):
        curses.curs_set(0)
        self.attr = I.colors()
        try:
            curses.mousemask(curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED | curses.BUTTON1_RELEASED)
            curses.mouseinterval(0)
        except curses.error:
            pass
        self.scr.timeout(T.SEQ_POLL_MS)
        self.refresh()
        while True:
            if self.bg["data"] is not None:
                data, self.over = self.bg["data"][:2]
                if len(self.bg["data"]) > 2:
                    self.recent = self.bg["data"][2]
                self.bg["data"] = None
                self.items = [x for _, grp in data for x in grp]
            if self.seq:
                s_ = self.seq()
                if s_ != self.last_seq:
                    self.last_seq = s_
                    self.refresh()
            if time.time() - self.last_full >= T.REFRESH_S:
                if self.stale and self.stale():  # its code changed (a new build): start again on it
                    return "restart"
                self.refresh()
            self.draw()
            ch = self.scr.getch()
            if ch in (-1, curses.KEY_RESIZE):
                continue
            if not self.handle(ch):
                return


def main(of_path, positions=None):
    """positions: the registry, or a callable that loads it (after the first frame), as the popup takes it."""
    import of_listen
    os.environ.setdefault("ESCDELAY", "25")

    def loader():
        import of_store
        own = of_store.connect()
        try:
            return T.load(T._positions(positions or []), own), I.overlay(own), recent_answers(own)
        finally:
            own.close()

    here = os.path.dirname(os.path.abspath(__file__))
    files = [os.path.join(here, f) for f in ("of_asks_panel.py", "of_asks_inbox.py", "of_asks_tui.py", "of_locale.py",
                                             os.path.join("locale", "ru.json"))]

    def mtimes():
        out = []
        for f in files:
            try:
                out.append(os.stat(f).st_mtime)
            except OSError:
                out.append(None)
        return out
    born = mtimes()

    def popup():  # OF's herdr plugin opens the questions over everything, with this bin/of (a trial opens itself)
        import of_herdr_plugin
        return of_herdr_plugin.open_questions(of_path)

    def run(scr):
        return Panel(scr, loader, seq=of_listen.seq, initial=([x for _, g in T.read_cache() for x in g], {}),
                     keys=open_keys(of_path=of_path), open_cmd=opener(), popup=popup,
                     stale=lambda: mtimes() != born).run()
    if curses.wrapper(run) == "restart":  # a pinned panel lives long: it takes a new build by itself
        import sys
        os.execv(sys.executable, [sys.executable] + sys.argv)
    return 0
