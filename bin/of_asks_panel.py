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
It reloads when the listener's views.seq moves (a safety reload every SAFETY_S), draws only on a change, and adapts
to the room it gets (the founder, ~21:08Z: "lots of room, more information; less room, less information"):
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
BURST_S = 0.12  # Enter or o this close to another key is typed text, not a press: it opens nothing
SETTLE_S = 10   # a new build is taken only after its files stood still this long
WIDE_W = 110    # from this many columns (and TALL_H rows): who waits on him and what he answered lately
RECENT_H = 24   # the answers listed are this many hours old at most
SAFETY_S = 60   # a reload without a views.seq signal: the safety net (the founder, 2026-11-13: the panel never rested)
REDRAW_S = 20   # a frame without a change: the ages move
REGISTRY_S = 300  # the registry is read again this often, in the background (it was parsed on every reload)


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


class Restarter:
    """When a long-lived panel takes a new build: its files changed, then stood still SETTLE_S, and the new build's
    CLI starts and knows --panel. The founder's trial panel died at 21:23Z 2026-11-12 restarting in the middle of a
    rebase, on a bin/of that did not know --panel yet (of_conductor, msg 01a1cc81)."""

    def __init__(self, files, of_path, run=None, clock=None):
        self.files, self.of_path = files, of_path
        self.run, self.clock = run or subprocess.run, clock or time.time
        self.born = self.seen = self.mtimes()
        self.since = None

    def mtimes(self):
        out = []
        for f in self.files:
            try:
                out.append(os.stat(f).st_mtime)
            except OSError:
                out.append(None)
        return out

    def due(self):
        now_m = self.mtimes()
        if now_m == self.born:
            return False
        if now_m != self.seen:  # still changing (a checkout or a rebase under way): wait for it to stand still
            self.seen, self.since = now_m, self.clock()
            return False
        if self.clock() - self.since < SETTLE_S:
            return False
        import sys
        try:
            r = self.run([sys.executable, self.of_path, "asks", "--help"], capture_output=True, text=True, timeout=30,
                         env=dict(os.environ, OF_AUTOMATION="1"))
            ok = r.returncode == 0 and "--panel" in (r.stdout or "")
        except Exception:  # noqa: BLE001 - the old build keeps running
            ok = False
        if not ok:
            self.since = self.clock()  # try again later; the old build keeps running meanwhile
        return ok


def summary(items, overlay=None, now=None):
    """{counts: {stream: n}, oldest: item or None, top: item or None} of the owner's queue."""
    g = I.split(items, overlay, now)
    waiting = [x for s in I.ORDER if s != "later" for x in g[s]]
    oldest = min(waiting, key=lambda x: T._ts(x.get("since")) or float("inf"), default=None)
    top = next((g[s][0] for s in I.ORDER if s not in ("later", "handed") and g[s]), None)
    return {"counts": {s: len(g[s]) for s in I.ORDER}, "shown": I.shown_streams(g), "oldest": oldest, "top": top}


class Panel:
    def __init__(self, scr, loader, seq=None, initial=None, keys=None, open_cmd=None, run_cmd=None, popup=None,
                 stale=None, keys_of=None, safety_s=SAFETY_S):
        self.scr, self.loader, self.seq = scr, loader, seq
        self.items, self.over = initial or ([], {})
        self.keys, self.open_cmd, self.popup, self.stale, self.keys_of = keys, open_cmd, popup, stale, keys_of
        self.run_cmd = run_cmd or (lambda argv: subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                                                 stderr=subprocess.DEVNULL, start_new_session=True))
        self.bg = {"data": None, "busy": False}
        self.button, self.msg, self.msg_at, self.attr = None, "", 0.0, {}
        self.recent, self.cards = [], {}  # the latest answers; screen row -> (x0, x1) of a card row
        self.last_key = 0.0
        self.last_seq, self.last_full = (seq() if seq else None), 0.0
        self.safety_s, self.behind = safety_s, None
        self.drawn, self.drawn_at, self.checked_at, self.keys_at = None, 0.0, 0.0, 0.0

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

    def burst(self):
        """Is this key part of text typed by a tool or pasted (of_herdr: a restart typed into the pane opened the
        popup on his screen)? A key within BURST_S of the one before, or with another right behind it."""
        now, before = time.time(), self.last_key
        self.scr.timeout(int(BURST_S * 1000))
        nxt = self.scr.getch()
        self.scr.timeout(T.SEQ_POLL_MS)
        if nxt != -1 or now - before < BURST_S:
            curses.flushinp()
            self.last_key = time.time()
            return True
        return False

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
            # one click, one open: the release (or a click), never the press too (of_herdr, from his herdr's log:
            # one click called the opener twice in 70 ms, the second got ui_busy)
            pressed = b & (curses.BUTTON1_CLICKED | curses.BUTTON1_RELEASED)
            on_button = self.button and y == self.button[0] and self.button[1] <= x < self.button[1] + self.button[2]
            on_card = y in self.cards and self.cards[y][0] <= x < self.cards[y][1]
            if pressed and (on_button or on_card):
                self.open()
        elif ch in (10, 13, curses.KEY_ENTER, ord("o")):
            if not self.burst():
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
        force = True
        while True:
            now = time.time()
            if self.bg["data"] is not None:
                data, self.over = self.bg["data"][:2]
                if len(self.bg["data"]) > 2:
                    self.recent = self.bg["data"][2]
                if len(self.bg["data"]) > 3:
                    self.behind = self.bg["data"][3]
                self.bg["data"] = None
                self.items = [x for _, grp in data for x in grp]
            if self.seq:
                s_ = self.seq()
                if s_ != self.last_seq:
                    self.last_seq = s_
                    self.refresh()
            if now - self.checked_at >= T.REFRESH_S:
                self.checked_at = now
                if self.stale and self.stale():  # its code changed (a new build): start again on it
                    return "restart"
            if self.keys_of and now - self.keys_at >= 30:  # herdr's bindings may change while the panel lives
                self.keys_at, self.keys = now, self.keys_of()
            if now - self.last_full >= self.safety_s:
                self.refresh()
            # a frame only when something it shows changed, or the ages moved (a pinned panel lives for days)
            sig = (id(self.items), id(self.over), len(self.recent), self.scr.getmaxyx(), self.msg, self.keys,
                   self.behind)
            if force or sig != self.drawn or now - self.drawn_at >= REDRAW_S:
                self.draw()
                self.drawn, self.drawn_at, force = sig, now, False
            ch = self.scr.getch()
            if ch == -1:
                continue
            force = True
            if ch == curses.KEY_RESIZE:
                continue
            if not self.handle(ch):
                return
            if ch != curses.KEY_MOUSE:
                self.last_key = time.time()


def main(of_path, positions=None):
    """positions: the registry, or a callable that loads it (after the first frame), as the popup takes it."""
    import of_listen
    os.environ.setdefault("ESCDELAY", "25")

    reg = {"p": [], "at": 0.0}

    def registry():  # read in the loader's thread, again after REGISTRY_S: never on every reload
        if time.time() - reg["at"] >= REGISTRY_S:
            try:
                reg["p"] = T._positions(positions or [])
            except Exception:  # noqa: BLE001 - the last one stands
                pass
            reg["at"] = time.time()
        return reg["p"]

    def loader():
        """The same queue as the popup (of_asks_inbox.queue: the listener's view and the log's tail), so the two show
        one list (the founder, 2026-11-13: the popup and the panel disagreed)."""
        own = I.reader()  # never waits on the listener's writes
        try:
            data, behind = I.queue(own, registry(), sending=I.on_their_way())
            return data, I.overlay(own), recent_answers(own), behind
        finally:
            own.close()

    here = os.path.dirname(os.path.abspath(__file__))
    restarter = Restarter([os.path.join(here, f) for f in ("of", "of_asks_panel.py", "of_asks_inbox.py", "of_asks_tui.py",
                                                           "of_locale.py", os.path.join("locale", "ru.json"))], of_path)

    def popup():  # OF's herdr plugin opens the questions over everything, with this bin/of (a trial opens itself)
        import of_herdr_plugin
        return of_herdr_plugin.open_questions(of_path)

    def run(scr):
        return Panel(scr, loader, seq=of_listen.seq, initial=([x for _, g in T.read_cache() for x in g], {}),
                     keys=open_keys(of_path=of_path), open_cmd=opener(), popup=popup,
                     stale=restarter.due, keys_of=lambda: open_keys(of_path=of_path)).run()
    if curses.wrapper(run) == "restart":  # a pinned panel lives long: it takes a new build by itself
        import sys
        os.execv(sys.executable, [sys.executable] + sys.argv)
    return 0
