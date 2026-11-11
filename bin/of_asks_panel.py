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
It refreshes as the popup does (views.seq, a full reload every REFRESH_S) and fits its pane: one line per part when
wide, one stream per line when narrow.
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
        with open(os.path.expanduser("~/.config/herdr/config.toml"), "rb") as fh:
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


def summary(items, overlay=None, now=None):
    """{counts: {stream: n}, oldest: item or None, top: item or None} of the owner's queue."""
    g = I.split(items, overlay, now)
    waiting = [x for s in I.ORDER if s != "later" for x in g[s]]
    oldest = min(waiting, key=lambda x: T._ts(x.get("since")) or float("inf"), default=None)
    top = next((g[s][0] for s in I.ORDER if s not in ("later", "handed") and g[s]), None)
    return {"counts": {s: len(g[s]) for s in I.ORDER}, "shown": I.shown_streams(g), "oldest": oldest, "top": top}


class Panel:
    def __init__(self, scr, loader, seq=None, initial=None, keys=None, open_cmd=None, run_cmd=None, popup=None):
        self.scr, self.loader, self.seq = scr, loader, seq
        self.items, self.over = initial or ([], {})
        self.keys, self.open_cmd, self.popup = keys, open_cmd, popup
        self.run_cmd = run_cmd or (lambda argv: subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                                                 stderr=subprocess.DEVNULL, start_new_session=True))
        self.bg = {"data": None, "busy": False}
        self.button, self.msg, self.msg_at, self.attr = None, "", 0.0, {}
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

    def draw(self):
        h, w = self.scr.getmaxyx()
        s = summary(self.items, self.over)
        self.scr.erase()
        y = 0
        streams = [(st, s["counts"][st]) for st in s["shown"]]
        line = [(f"● {I.stream_name(st)} {n}", st) for st, n in streams]
        if sum(T.dwidth(t) + 3 for t, _ in line) <= w - 1:  # wide: the streams on one line
            x = 0
            self.put(y, x, tr("Questions") + "  ", curses.A_BOLD)
            x += T.dwidth(tr("Questions")) + 2
            for t, st in line:
                self.put(y, x, t, self.attr.get(I.COLOR[st], 0) | (curses.A_BOLD if s["counts"][st] else 0))
                x += T.dwidth(t) + 3
            y += 1
        else:  # narrow: one stream per line
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
        label = " " + tr("Open the questions") + " "
        hint = tr("or {keys}", keys=self.keys) if self.keys else ""
        by = min(y, h - 2)
        self.put(by, 0, label, curses.A_REVERSE | curses.A_BOLD)
        self.button = (by, 0, T.dwidth(label))
        below = by + 1
        if hint and T.dwidth(label) + 2 + T.dwidth(hint) <= w - 1:
            self.put(by, T.dwidth(label) + 2, hint, curses.A_DIM)
        elif hint and below < h - 1:  # narrow: the keys under the button
            self.put(below, 0, T.cut(hint, w - 1), curses.A_DIM)
            below += 1
        if self.msg and time.time() - self.msg_at < 6 and below < h - 1:
            self.put(below, 0, T.cut(self.msg, w - 1), curses.A_DIM)
        self.scr.refresh()

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
            if self.button and y == self.button[0] and self.button[1] <= x < self.button[1] + self.button[2] \
                    and b & (curses.BUTTON1_PRESSED | curses.BUTTON1_CLICKED | curses.BUTTON1_RELEASED):
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
                data, self.over = self.bg["data"]
                self.bg["data"] = None
                self.items = [x for _, grp in data for x in grp]
            if self.seq:
                s_ = self.seq()
                if s_ != self.last_seq:
                    self.last_seq = s_
                    self.refresh()
            if time.time() - self.last_full >= T.REFRESH_S:
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
            return T.load(T._positions(positions or []), own), I.overlay(own)
        finally:
            own.close()

    def popup():  # OF's herdr plugin opens the questions over everything, with this bin/of (a trial opens itself)
        import of_herdr_plugin
        return of_herdr_plugin.open_questions(of_path)

    def run(scr):
        Panel(scr, loader, seq=of_listen.seq, initial=([x for _, g in T.read_cache() for x in g], {}),
              keys=open_keys(of_path=of_path), open_cmd=opener(), popup=popup).run()
    curses.wrapper(run)
    return 0
