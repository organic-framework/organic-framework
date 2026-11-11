"""The conductor's panel (hand-over a8ee7e96 from of_conductor, 2026-11-10): a small pane in its pinned view that
refreshes itself and shows the owner, in the language of ~/.config/of/owner.yaml (en by default; ru on the founder's
machine), each open request of the conductor's ledger with its owners,
each thread's live state, its last movement and a stuck flag.

The ledger is of_conductor's (`~/.local/state/of/conductor/requests.yaml`, its schema in the file's header); the
panel only reads it. A thread's live state comes from the records, not from the ledger's own `state`:
- via handover: the hand-over's last event (proposed, offered, accepted, delivered, verified, ...);
- via message: whether the owner answered (a message to of_conductor naming the message id);
- the owner's last declared state (`of status`) and its last turn;
- the founder's cards tied to it (`asks:`), still open or answered.
A thread is stuck when it is not finished and nothing moved for STUCK_S, or a hand-over waits for acceptance
longer than ACCEPT_S.

    of conductor panel [--once] [--every 30]
"""

import json
import os
import shutil
import subprocess
import sys
import time

import yaml

import of_events
import of_locale
from of_locale import tr

STUCK_S = 2 * 3600
ACCEPT_S = 3600
CLOSED_SHOWN_S = 6 * 3600

def LANG_NAME():
    """The person's language by name, for the small model's instructions."""
    return {"ru": "Russian"}.get(of_locale.language(), "English")


# shown through the locale layer (of_locale.tr): English here, the person's language from the catalogue
HANDOVER = {"proposed": "handed over, not accepted yet", "offered": "handed over, not accepted yet",
            "accepted": "accepted, in progress", "blocked": "blocked", "delivered": "delivered, awaiting review",
            "verified": "done, checked", "closed": "done", "rejected": "delivery rejected, being redone",
            "returned": "returned", "declined": "declined", "withdrawn": "withdrawn", "expired": "expired"}
DONE = {"verified", "closed", "done", "declined", "withdrawn"}
STATE = {"working": "working", "waiting": "waiting", "idle": "free", "done": "finished"}
LEDGER = {"routed": "handed over", "accepted": "accepted", "working": "in progress", "delivered": "delivered",
          "done": "done", "declined": "declined"}


def _title(r):
    """The request's title: `title` (the onboarding package's conductor brief, 000c95a), else the older `title_ru`."""
    return r.get("title") or r.get("title_ru") or tr("Untitled")


def ledger_path():
    return os.path.join(of_events.STATE_DIR, "conductor", "requests.yaml")


def load_ledger(path=None):
    try:
        with open(path or ledger_path()) as fh:
            return yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _ts(iso):
    if not iso:
        return None
    s = str(iso).replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            import calendar
            return calendar.timegm(time.strptime(s[:26] if "." in s else s, fmt))
        except ValueError:
            continue
    return None


def ago(t, now):
    if t is None:
        return "—"
    s = max(0, int(now - t))
    if s < 90:
        return tr("just now")
    if s < 3600:
        return tr("{n} min ago", n=s // 60)
    if s < 86400:
        return tr("{h} h {m:02d} min ago", h=s // 3600, m=s % 3600 // 60)
    return tr("{n} days ago", n=s // 86400)


def _position(owner):
    return str(owner or "").split(":")[-1]


def handover_state(conn, ref):
    """(state, ts, note) of the hand-over's last event, by id or its last 8 characters."""
    if not ref:
        return None, None, None
    row = conn.execute("SELECT name, ts, payload FROM event WHERE name LIKE 'of.handover_%' AND name != "
                       "'of.handover_proposed' AND json_extract(payload, '$.id') LIKE ? ORDER BY ts DESC LIMIT 1",
                       (f"%{ref}",)).fetchone()
    if row:
        p = json.loads(row[2] or "{}")
        return row[0].removeprefix("of.handover_"), _ts(row[1]), p.get("note")
    row = conn.execute("SELECT ts FROM event WHERE name = 'of.handover_proposed' AND event_id LIKE ? LIMIT 1",
                       (f"%{ref}",)).fetchone()
    return ("proposed", _ts(row[0]), None) if row else (None, None, None)


def message_answer(conn, msg, owner):
    """The time the owner answered the conductor's message, or None."""
    if not msg:
        return None
    row = conn.execute("SELECT ts FROM event WHERE name = 'of.agent_message_in' AND position = 'of_conductor' AND "
                       "payload LIKE ? AND payload LIKE ? ORDER BY ts LIMIT 1",
                       (f"%reply to {msg}%", f"%{_position(owner)}%")).fetchone()
    return _ts(row[0]) if row else None


def owner_now(conn, position):
    """(state ru, note, ts) of the owner's last declared state, and its last turn's time."""
    row = conn.execute("SELECT ts, payload FROM event WHERE name = 'of.state_declared' AND position = ? "
                       "ORDER BY ts DESC LIMIT 1", (position,)).fetchone()
    turn = conn.execute("SELECT ts FROM event WHERE name = 'of.turn_end' AND position = ? ORDER BY ts DESC LIMIT 1",
                        (position,)).fetchone()
    st = note = None
    t = None
    if row:
        p = json.loads(row[1] or "{}")
        st = tr(STATE.get(p.get("state"), p.get("state") or ""))
        if p.get("state") == "waiting" and p.get("waiting_on"):
            st += f" ({tr("the founder") if p['waiting_on'] in ('founder', 'owner') else p['waiting_on']})"
        note, t = p.get("reason") or p.get("note"), _ts(row[0])
    return st, note, t, _ts(turn[0]) if turn else None


def card_titles(conn, ids):
    """The founder's open cards as the popup shows them: the first words of each question (the popup shows no ids;
    founder, 2026-11-10: "the questions window has no such card ID, name it some other way")."""
    import sqlite3
    out, asks = [], None
    for i in ids:
        try:
            row = conn.execute("SELECT text, state FROM view_asks WHERE event_id LIKE ?", (f"%{i}",)).fetchone()
        except sqlite3.OperationalError:  # no listener view: project the asks from the log
            import of_asks
            asks = asks if asks is not None else of_asks.project(of_asks.load(conn))
            a = next((x for x in asks if x["event_id"].endswith(i)), None)
            row = (a["text"], a["state"]) if a else None
        if row and row[1] == "open":
            words = " ".join(str(row[0] or "").split())
            out.append(words if len(words) <= 70 else words[:69].rsplit(" ", 1)[0] + "…")
    return out


def open_cards(conn, ids):
    if not ids:
        return 0, 0
    import sqlite3
    states = []
    try:
        for i in ids:
            row = conn.execute("SELECT state FROM view_asks WHERE event_id LIKE ?", (f"%{i}",)).fetchone()
            states.append(row[0] if row else None)
    except sqlite3.OperationalError:  # no listener view: project the asks from the log
        import of_asks
        asks = of_asks.project(of_asks.load(conn))
        states = [next((a["state"] for a in asks if a["event_id"].endswith(i)), None) for i in ids]
    return sum(1 for s in states if s == "open"), len(ids)


# ---- plain words (hand-over de230664: clear to someone who knows nothing of the work) ----

PLAIN_HANDOVER = {"proposed": "handed over, not taken up yet", "offered": "handed over, not taken up yet", "accepted": "taken up", "blocked": "hit an obstacle", "delivered": "done, awaiting review", "verified": "done", "closed": "done", "rejected": "redoing it after review", "returned": "gave the task back", "declined": "declined it", "withdrawn": "the task was withdrawn", "expired": "the time to answer ran out"}
# Apple's system colors in 24-bit (the pane has COLORTERM=truecolor): the look the founder liked, in the text itself
TC = {"red": "38;2;255;69;58", "purple": "38;2;191;90;242", "amber": "38;2;255;214;10", "green": "38;2;48;209;88",
      "grey": "38;2;142;142;147", "dim": "38;2;142;142;147", "text": "38;2;245;245;247", "soft": "38;2;99;99;102"}
KIND_ICON = {"stuck": ("▲", "1;" + TC["red"]), "founder": ("◆", "1;" + TC["purple"]), "working": ("●", TC["amber"]),
             "start": ("○", TC["grey"]), "done": ("●", TC["green"])}
KIND_ORDER = {"stuck": 0, "founder": 1, "working": 2, "start": 3, "done": 4}
TRANSLATE_SYSTEM = ("You write for a busy business owner who knows nothing about software or AI agents. {lang} "
                    "only, plain everyday words, no jargon, no ids, hashes, file names or commit numbers.")


def thread_view(conn, th, now, asked=None):
    """{kind, owner, said, note, last, cards} of one thread from the records. kind: stuck | founder (a card of his
    is open) | working | start (handed over, not taken yet) | done."""
    owner = th.get("owner") or "?"
    pos = _position(owner)
    via, lstate = th.get("via"), th.get("state")
    said, last, note, kind = tr(LEDGER.get(lstate, lstate or "?")), None, None, "working"
    finished = lstate in DONE or bool(th.get("result"))
    waiting_accept = False
    if via == "handover":
        hs, ht, hnote = handover_state(conn, str(th.get("handover") or ""))
        if hs:
            said, last, note = tr(PLAIN_HANDOVER.get(hs, hs)), ht, hnote
            finished = finished or hs in DONE
            waiting_accept = hs in ("proposed", "offered")
    elif via == "message":
        at = message_answer(conn, th.get("message"), owner)
        said, last = (tr("answered"), at) if at else (tr("written to, no answer yet"), None)
        finished = finished or bool(at and lstate in ("done", "delivered"))
        waiting_accept = not at  # unless it moved since the request (below): then it works on it
    elif via == "records":
        said, finished = tr("answered from the records, nobody disturbed"), True
    who = owner_now(conn, pos)
    moves = [x for x in (last, who[2], who[3]) if x]
    last = max(moves) if moves else None
    if via == "message" and waiting_accept and asked and last and last > asked:
        waiting_accept, said = False, tr("working, no answer yet")
    open_n, all_n = open_cards(conn, th.get("asks") or [])
    if finished:
        kind = "done"
    elif open_n:
        kind = "founder"
    elif (last is None or now - last > STUCK_S) or (waiting_accept and last and now - last > ACCEPT_S):
        kind = "stuck"
    elif waiting_accept:
        kind = "start"
    return {"kind": kind, "owner": owner, "pos": pos, "said": said, "note": note or who[1], "who": who[0],
            "last": last, "cards": [c for c in (th.get("asks") or [])], "open_cards": open_n,
            "card_titles": card_titles(conn, th.get("asks") or []) if open_n else []}


def cache_path():
    return os.path.join(of_events.STATE_DIR, "conductor", "panel-ru.json")


def load_cache():
    try:
        with open(cache_path()) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_cache(c):
    os.makedirs(os.path.dirname(cache_path()), exist_ok=True)
    with open(cache_path(), "w") as fh:
        json.dump(c, fh, ensure_ascii=False, indent=1)


def _key(*parts):
    import hashlib
    return hashlib.sha1("\x1f".join(str(p or "") for p in parts).encode()).hexdigest()[:16]


def ask_model(prompt, schema, runner=subprocess.run):
    """One small-model call (Haiku through claude -p, no tools, no hooks); {} on any failure."""
    import of_capacity
    import of_case
    argv = [of_capacity.claude_bin(), "-p", prompt, "--model", "haiku", "--tools", "", "--system-prompt",
            TRANSLATE_SYSTEM.format(lang=LANG_NAME()), "--json-schema", json.dumps(schema), "--output-format", "json",
            "--no-session-persistence", "--settings", '{"disableAllHooks": true}', "--strict-mcp-config",
            "--disable-slash-commands", "--max-turns", "3"]
    try:
        r = runner(argv, capture_output=True, text=True, timeout=90, env=of_capacity.probe_env(of_case.account()),
                   cwd=os.path.dirname(cache_path()))
        j = json.loads(r.stdout or "{}")
        return j.get("structured_output") or {}
    except Exception:  # noqa: BLE001  the panel shows the raw words then, and tries again next round
        return {}


def fill_plain(views, positions, cache, runner=subprocess.run):
    """Ask the model for what the cache lacks: a plain Russian job title per owner (from the registry's summary and
    contribution) and one plain sentence per agent's note. Returns True when it added anything."""
    by = {p["name"]: p for p in positions}
    want_names = {}
    for v in views:
        p = by.get(v["pos"]) or {}
        k = "name:" + _key(of_locale.language(), v["pos"], p.get("summary"), p.get("contribution"))
        if k not in cache:
            want_names[k] = {"id": k, "position": v["pos"], "summary": p.get("summary") or "",
                             "what_it_does": " ".join(str(p.get("contribution") or "").split())[:400]}
    want_notes = {}
    for v in views:
        if v.get("note"):
            k = "note:" + _key(of_locale.language(), v["note"])
            if k not in cache:
                want_notes[k] = {"id": k, "text": " ".join(str(v["note"]).split())[:600]}
    added = False
    if want_names:
        got = ask_model(
            f"For each AI agent below, write a short {LANG_NAME()} job title (2 to 4 words) that a person outside the work "
            "understands, like a role in an ordinary company. Return the id with it.\n"
            + json.dumps(list(want_names.values()), ensure_ascii=False),
            {"type": "object", "required": ["items"], "additionalProperties": False, "properties": {"items": {
                "type": "array", "items": {"type": "object", "required": ["id", "ru"], "additionalProperties": False,
                                           "properties": {"id": {"type": "string"}, "ru": {"type": "string"}}}}}},
            runner)
        for it in got.get("items") or []:
            if it.get("id") in want_names and it.get("ru"):
                cache[it["id"]] = it["ru"].strip()
                added = True
    if want_notes:
        got = ask_model(
            f"Each text below is an AI agent's note on what it is doing. Rewrite each as ONE short {LANG_NAME()} sentence "
            "(at most 90 characters) saying what happens now, in plain words for someone outside the work. Drop "
            "ids, hashes, file names and technical detail. Return the id with it.\n"
            + json.dumps(list(want_notes.values()), ensure_ascii=False),
            {"type": "object", "required": ["items"], "additionalProperties": False, "properties": {"items": {
                "type": "array", "items": {"type": "object", "required": ["id", "ru"], "additionalProperties": False,
                                           "properties": {"id": {"type": "string"}, "ru": {"type": "string"}}}}}},
            runner)
        for it in got.get("items") or []:
            if it.get("id") in want_notes and it.get("ru"):
                cache[it["id"]] = it["ru"].strip()
                added = True
    return added


def plain_name(v, positions, cache):
    p = next((x for x in positions if x["name"] == v["pos"]), {})
    return cache.get("name:" + _key(of_locale.language(), v["pos"], p.get("summary"), p.get("contribution"))) or v["pos"]


def plain_note(v, cache):
    return cache.get("note:" + _key(of_locale.language(), v["note"])) if v.get("note") else None


def views_of(ledger, conn, now):
    reqs = ledger.get("requests") or []
    shown = [r for r in reqs if r.get("state") != "closed" or (now - (_ts(r.get("closed_at")) or 0) < CLOSED_SHOWN_S)]
    out = []
    for r in shown:
        closed = r.get("state") == "closed"
        tv = [] if closed else [thread_view(conn, th, now, _ts(r.get("asked_at"))) for th in r.get("threads") or []]
        kind = "done" if closed or (tv and all(t["kind"] == "done" for t in tv)) else \
            min((t["kind"] for t in tv), key=lambda k: KIND_ORDER[k], default="working")
        out.append((r, kind, tv))
    return sorted(out, key=lambda x: (KIND_ORDER[x[1]], str(x[0].get("asked_at") or "")))


def render(ledger, conn, now=None, width=None, color=True, positions=(), cache=None):
    import textwrap
    now = now or time.time()
    width = max(60, width or shutil.get_terminal_size((100, 40)).columns)
    cache = cache if cache is not None else {}
    c = (lambda code, s: f"\033[{code}m{s}\033[0m") if color else (lambda code, s: s)
    title = c("1;" + TC["text"], tr("Your requests")) + c(TC["dim"], tr("   ·   what the team is doing about them   ·   updated ")
                                                                + time.strftime('%H:%M', time.localtime(now)))
    if ledger.get("error"):
        return "\n".join([title, "", c("33", tr("Cannot read the conductor's list of requests."))])
    rows = views_of(ledger, conn, now)
    if not rows:
        return "\n".join([title, "", tr("No open requests.")])
    n = {k: sum(1 for _, kind, _ in rows if kind == k) for k in KIND_ORDER}
    pill = lambda col, k, lab: c(TC[col] if k else TC["soft"], "●") + " " + c("1;" + TC["text"], str(k)) + " " + \
        c(TC["dim"], lab)  # noqa: E731
    summary = [pill("amber", n["working"] + n["start"], tr("in progress")), pill("purple", n["founder"], tr("waiting on you|pill")),
               pill("red", n["stuck"], tr("stuck|pill")), pill("green", n["done"], tr("done lately"))]
    lines = [title, "", "     ".join(summary), c(TC["soft"], "─" * min(width, 100))]
    wrap = lambda s, ind: textwrap.wrap(s, width=max(30, min(width, 100) - ind), initial_indent=" " * ind,  # noqa: E731
                                        subsequent_indent=" " * ind)
    recent = [r for r, kind, _ in rows if r.get("state") == "closed"]
    for r, kind, tv in rows:
        if r.get("state") == "closed":
            continue
        icon, col = KIND_ICON[kind]
        asked = _ts(r.get("asked_at"))
        when = time.strftime('%H:%M', time.localtime(asked)) if asked else ""
        head = f"{icon} {_title(r)}"
        lines.append("")
        lines.append(c(col, icon) + " " + c("1;" + TC["text"], head[2:]) + c(TC["soft"], f"   {when}" if when else ""))
        for t in tv:
            ticon, tcol = KIND_ICON[t["kind"]]
            who = plain_name(t, positions, cache)
            line = f"{c(tcol, ticon)} {c('1;' + TC['text'], who)} {c(TC['dim'], '— ' + t['said'])}"
            lines.append("    " + line)
            now_ru = plain_note(t, cache)
            if t["kind"] == "founder":
                for q in t.get("card_titles") or [tr("a question in the questions window")]:
                    lines += [c("1;" + TC["purple"], x) for x in wrap(tr("Waits for your answer in the questions window: “{q}”", q=q), 6)]
            if now_ru:
                lines += [c(TC["dim"], x) for x in wrap(now_ru, 6)]
            stamp = tr("last movement ") + ago(t['last'], now)
            if t["kind"] == "stuck":
                stamp += tr(" — STUCK, the conductor will sort it out")
            lines.append(c("1;" + TC["red"] if t["kind"] == "stuck" else TC["soft"], "      " + stamp))
    if recent:
        lines += ["", c(TC["dim"], tr("DONE IN THE LAST HOURS"))]
        lines += [c(TC["green"], "  ●") + " " + c(TC["dim"], _cut(_title(r), min(width, 100) - 6))
                  for r in recent[:5]]
        if len(recent) > 5:
            lines.append(c(TC["soft"], "    " + tr("and {n} more", n=len(recent) - 5)))
    lines += ["", c(TC["red"], "▲") + c(TC["soft"], tr(" stuck   ")) + c(TC["purple"], "◆") + c(TC["soft"], tr(" waiting on you   "))
              + c(TC["amber"], "●") + c(TC["soft"], tr(" in progress   ")) + c(TC["grey"], "○") + c(TC["soft"], tr(" not started   "))
              + c(TC["green"], "●") + c(TC["soft"], tr(" done"))]
    return "\n".join(lines)


def _cut(s, n):
    s = " ".join(str(s or "").split())
    return s if len(s) <= max(20, n) else s[:max(20, n) - 1] + "…"


def _positions():
    """Every organization's positions (name, summary, contribution), for plain job titles."""
    import importlib.machinery
    import importlib.util
    here = os.path.dirname(os.path.realpath(__file__))
    loader = importlib.machinery.SourceFileLoader("of_cli_panel", os.path.join(here, "of"))
    of = importlib.util.module_from_spec(importlib.util.spec_from_loader("of_cli_panel", loader))
    loader.exec_module(of)
    return of._load_quiet()


# ---- the picture mode (de230664, the founder: "in a more Apple format ... a cinematic interface") ----
# herdr carries Ghostty's kitty-graphics support: an image sent from the pane by the kitty protocol shows in it.
# The panel renders an HTML page with headless Chrome only when its content changes, and places the PNG over the
# pane's cells. The text panel stays as the fallback (`--text`).

APPLE = {"bg": "#000000", "card": "#111113", "line": "#232326", "text": "#f5f5f7", "dim": "#8e8e93",
         "green": "#30d158", "amber": "#ffd60a", "red": "#ff453a", "purple": "#bf5af2", "grey": "#636366"}
KIND_DOT = {"stuck": "red", "founder": "purple", "working": "amber", "start": "grey", "done": "green"}
KIND_LABEL = {"stuck": "stuck", "founder": "waiting on you", "working": "in progress", "start": "not started yet", "done": "done"}


def _esc(s):
    import html
    return html.escape(str(s or ""))


def render_html(ledger, conn, now=None, positions=(), cache=None, width=900, height=1200):
    now = now or time.time()
    cache = cache if cache is not None else {}
    a = APPLE
    rows = [] if ledger.get("error") else views_of(ledger, conn, now)
    n = {k: sum(1 for _, kind, _ in rows if kind == k) for k in KIND_ORDER}
    pills = [(tr("in progress"), n["working"] + n["start"], "amber"), (tr("waiting on you|pill"), n["founder"], "purple"),
             (tr("stuck|pill"), n["stuck"], "red"), (tr("done lately"), n["done"], "green")]
    pill_html = "".join(
        f'<div class="pill"><span class="dot" style="background:{a[col] if cnt else a["grey"]}"></span>'
        f'<b>{cnt}</b><span>{_esc(lab)}</span></div>' for lab, cnt, col in pills)
    cards, recent = [], []
    for r, kind, tv in rows:
        if r.get("state") == "closed":
            recent.append(r)
            continue
        asked = _ts(r.get("asked_at"))
        threads = []
        for t in tv:
            now_ru = plain_note(t, cache)
            card_line = ""
            if t["kind"] == "founder":
                card_line = "".join('<div class="ask">' + tr("Waits for your answer in the questions window: “{q}”", q=_esc(q)) + '</div>'
                                    for q in (t.get("card_titles") or [tr("a question")]))
            stuck = '<span class="stuck">' + tr("stuck") + '</span>' if t["kind"] == "stuck" else ""
            threads.append(
                f'<div class="thread"><span class="dot" style="background:{a[KIND_DOT[t["kind"]]]}"></span>'
                f'<div class="tb"><div class="who">{_esc(plain_name(t, positions, cache))}'
                f'<span class="said"> — {_esc(t["said"])}</span>{stuck}</div>'
                + (f'<div class="now">{_esc(now_ru)}</div>' if now_ru else "")
                + card_line + f'<div class="when">{_esc(ago(t["last"], now))}</div></div></div>')
        cards.append(
            f'<section class="card k-{kind}"><div class="head"><span class="dot big" '
            f'style="background:{a[KIND_DOT[kind]]}"></span><h2>{_esc(_title(r))}</h2>'
            f'<span class="asked">{time.strftime("%H:%M", time.localtime(asked)) if asked else ""}</span></div>'
            + "".join(threads) + "</section>")
    done_html = ""
    if recent:
        done_html = ('<div class="done"><div class="label">' + tr("Done in the last hours") + '</div>'
                     + "".join(f'<div class="dl"><span class="dot" style="background:{a["green"]}"></span>'
                               f'{_esc(_title(r))}</div>' for r in recent[:6]) + "</div>")
    empty = '<div class="empty">' + tr("No open requests.") + '</div>' if not cards else ""
    if ledger.get("error"):
        empty = '<div class="empty">' + tr("Cannot read the conductor's list of requests.") + '</div>'
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>
html,body{{margin:0;background:{a['bg']};color:{a['text']};width:{width}px;height:{height}px;overflow:hidden;
font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text","Helvetica Neue",sans-serif;
-webkit-font-smoothing:antialiased}}
.wrap{{padding:40px 40px 32px}}
.top{{display:flex;align-items:baseline;justify-content:space-between;gap:16px}}
h1{{margin:0;font-size:30px;font-weight:700;letter-spacing:-0.02em}}
.time{{color:{a['dim']};font-size:15px}}
.sub{{color:{a['dim']};font-size:15px;margin-top:6px}}
.pills{{display:flex;flex-wrap:wrap;gap:10px;margin:22px 0 26px}}
.pill{{display:flex;align-items:center;gap:8px;background:{a['card']};border:1px solid {a['line']};border-radius:999px;
padding:8px 14px;font-size:15px;color:{a['dim']}}}
.pill b{{color:{a['text']};font-weight:600;font-size:16px}}
.dot{{width:9px;height:9px;border-radius:50%;flex:none;display:inline-block}}
.dot.big{{width:12px;height:12px}}
.card{{background:{a['card']};border:1px solid {a['line']};border-radius:20px;padding:20px 22px;margin-bottom:14px}}
.card.k-stuck{{border-color:#5a1f1c}} .card.k-founder{{border-color:#4a2a5c}}
.head{{display:flex;align-items:center;gap:12px}}
h2{{margin:0;font-size:19px;font-weight:600;letter-spacing:-0.01em;flex:1;line-height:1.3}}
.asked{{color:{a['dim']};font-size:14px}}
.thread{{display:flex;gap:12px;align-items:flex-start;margin:14px 0 0 2px}}
.thread .dot{{margin-top:7px}}
.tb{{flex:1}}
.who{{font-size:16px;font-weight:600}}
.said{{font-weight:400;color:#d1d1d6}}
.stuck{{margin-left:10px;color:{a['red']};font-size:13px;font-weight:600;letter-spacing:0.04em;text-transform:uppercase}}
.now{{color:{a['dim']};font-size:15px;line-height:1.45;margin-top:4px}}
.ask{{display:inline-block;margin-top:8px;padding:6px 12px;border-radius:10px;background:#2a1733;color:#e3b8ff;
font-size:14px;font-weight:600}}
.when{{color:{a['grey']};font-size:13px;margin-top:6px}}
.done{{margin-top:22px}} .label{{color:{a['dim']};font-size:13px;letter-spacing:0.06em;text-transform:uppercase;
margin-bottom:10px}}
.dl{{display:flex;align-items:center;gap:10px;color:#c7c7cc;font-size:15px;padding:5px 0}}
.empty{{color:{a['dim']};font-size:18px;margin-top:40px}}
</style></head><body><div class="wrap">
<div class="top"><h1>{tr("Your requests")}</h1><span class="time">{tr("updated")} {time.strftime('%H:%M', time.localtime(now))}</span></div>
<div class="sub">{tr("What the team is doing about them now")}</div>
<div class="pills">{pill_html}</div>
{empty}{"".join(cards)}{done_html}
</div></body></html>"""


def pane_pixels(out=sys.stdout):
    """(columns, rows, width px, height px) of the pane; the pixel size from the terminal when it tells, else
    estimated from a typical cell (9 x 19 px)."""
    import fcntl
    import struct
    import termios
    try:
        rows, cols, xp, yp = struct.unpack("HHHH", fcntl.ioctl(out.fileno(), termios.TIOCGWINSZ, b"\0" * 8))
    except Exception:  # noqa: BLE001
        cols, rows = shutil.get_terminal_size((100, 40))
        xp = yp = 0
    return cols, rows, xp or cols * 9, yp or rows * 19


PICTURE_MAX_W = 960  # herdr re-sends a pane image as raw RGBA: 960 x 1300 x 4 is about 5 MB, a 2x render was 17 MB
                     # and never finished drawing (lab, 2026-11-11)


def picture_size(cols, rows, wpx, hpx):
    """The page's CSS size: the pane's shape, at most PICTURE_MAX_W wide; the terminal scales it to the cells."""
    w = max(320, min(PICTURE_MAX_W, wpx))
    h = max(240, int(w * hpx / max(1, wpx)))
    return w // 2 * 2, h // 2 * 2


def render_png(html_text, width, height, path):
    """The page to a PNG by headless Chrome (2x for a sharp image); False when Chrome is missing or fails."""
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if not os.path.exists(chrome):
        return False
    d = os.path.dirname(path)
    src = os.path.join(d, "panel.html")
    with open(src, "w") as fh:
        fh.write(html_text)
    try:
        subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=1",
                        f"--window-size={width},{height}", "--virtual-time-budget=3000",
                        f"--user-data-dir={os.path.join(d, 'chrome')}", f"--screenshot={path}", f"file://{src}"],
                       capture_output=True, timeout=60)
    except Exception:  # noqa: BLE001
        return False
    return os.path.exists(path)


def kitty_image(png, cols, rows):
    """The kitty graphics sequence that shows `png` over cols x rows cells at the cursor (chunked base64)."""
    import base64
    data = base64.standard_b64encode(open(png, "rb").read()).decode()
    chunks = [data[i:i + 4096] for i in range(0, len(data), 4096)] or [""]
    out = ["\033_Ga=d,d=A,q=2\033\\"]  # remove the previous image
    for i, ch in enumerate(chunks):
        more = 1 if i < len(chunks) - 1 else 0
        head = f"a=T,f=100,t=d,c={cols},r={rows},q=2,m={more}" if i == 0 else f"m={more},q=2"
        out.append(f"\033_G{head};{ch}\033\\")
    return "".join(out)


# ---- the card view (de230664, the founder ~20:45Z: the picture was blurry and did not scroll) ----
# herdr re-sends a pane image as raw pixels, so a sharp (2x) picture never gets placed and a 1x one is blurry
# (lab/scenarios/herdr-image-v0). The panel draws text instead, at Ghostty's own resolution: rounded cards in
# Apple's colours, and it scrolls by the mouse wheel, the arrows, PgUp/PgDn, j/k, g/G.

def _vlen(s):
    import re
    return len(re.sub(r"\033\[[0-9;]*m", "", s))


def card_lines(ledger, conn, now=None, positions=(), cache=None, width=100, color=True):
    """The panel as lines: a header, the count pills, a rounded card per open request, the done list."""
    import textwrap
    now = now or time.time()
    cache = cache if cache is not None else {}
    c = (lambda code, s: f"\033[{code}m{s}\033[0m") if color else (lambda code, s: s)
    w = max(48, min(width - 2, 104))
    inner = w - 4
    out = ["", "  " + c("1;" + TC["text"], tr("Your requests")) + c(TC["dim"], tr("  ·  updated ") + time.strftime('%H:%M', time.localtime(now))),
           "  " + c(TC["dim"], tr("what the team is doing about them now")), ""]
    if ledger.get("error"):
        return out + ["  " + c(TC["amber"], tr("Cannot read the conductor's list of requests."))]
    rows = views_of(ledger, conn, now)
    n = {k: sum(1 for _, kind, _ in rows if kind == k) for k in KIND_ORDER}
    pill = lambda col, k, lab: c(TC[col] if k else TC["soft"], "●") + " " + c("1;" + TC["text"], str(k)) + " " + \
        c(TC["dim"], lab)  # noqa: E731
    out.append("  " + "    ".join([pill("amber", n["working"] + n["start"], tr("in progress")),
                                    pill("purple", n["founder"], tr("waiting on you|pill")), pill("red", n["stuck"], tr("stuck|pill")),
                                    pill("green", n["done"], tr("done"))]))
    out.append("")
    edge = TC["soft"]

    def box_line(text=""):
        return "  " + c(edge, "│") + " " + text + " " * max(0, inner - _vlen(text)) + " " + c(edge, "│")

    def wrap(text, ind, col):
        return [box_line(" " * ind + c(col, x)) for x in textwrap.wrap(text, width=max(20, inner - ind))]
    recent = []
    for r, kind, tv in rows:
        if r.get("state") == "closed":
            recent.append(r)
            continue
        icon, col = KIND_ICON[kind]
        bcol = TC["red"] if kind == "stuck" else TC["purple"] if kind == "founder" else edge
        asked = _ts(r.get("asked_at"))
        when = time.strftime('%H:%M', time.localtime(asked)) if asked else ""
        out.append("  " + c(bcol, "╭" + "─" * (w - 2) + "╮"))
        title = _cut(_title(r), inner - len(when) - 4)
        head = c(col, icon) + " " + c("1;" + TC["text"], title)
        pad = inner - _vlen(head) - len(when)
        out.append("  " + c(bcol, "│") + " " + head + " " * max(1, pad) + c(TC["soft"], when) + " " + c(bcol, "│"))
        for t in tv:
            out.append("  " + c(bcol, "│") + " " * (w - 2) + c(bcol, "│"))
            ticon, tcol = KIND_ICON[t["kind"]]
            who = plain_name(t, positions, cache)
            first = f"  {c(tcol, ticon)} {c('1;' + TC['text'], who)}"
            out += [x.replace(c(edge, "│"), c(bcol, "│")) for x in [box_line(first)]]
            out += [x.replace(c(edge, "│"), c(bcol, "│")) for x in wrap(t["said"], 4, TC["dim"])]
            if t["kind"] == "founder":
                for q in t.get("card_titles") or [tr("a question in the questions window")]:
                    out += [x.replace(c(edge, "│"), c(bcol, "│"))
                            for x in wrap(tr("Waits for your answer in the questions window: “{q}”", q=q), 4, "1;" + TC["purple"])]
            now_ru = plain_note(t, cache)
            if now_ru:
                out += [x.replace(c(edge, "│"), c(bcol, "│")) for x in wrap(now_ru, 4, TC["text"])]
            stamp = tr("movement ") + ago(t['last'], now) + (tr("  ·  stuck, the conductor will sort it out") if t["kind"] == "stuck" else "")
            out += [x.replace(c(edge, "│"), c(bcol, "│"))
                    for x in wrap(stamp, 4, TC["red"] if t["kind"] == "stuck" else TC["soft"])]
        out.append("  " + c(bcol, "╰" + "─" * (w - 2) + "╯"))
        out.append("")
    if recent:
        out.append("  " + c(TC["dim"], tr("DONE IN THE LAST HOURS")))
        out += ["  " + c(TC["green"], "●") + " " + c(TC["dim"], _cut(_title(r), w - 4))
                for r in recent]
        out.append("")
    out.append("  " + c(TC["red"], "▲") + c(TC["soft"], tr(" stuck   ")) + c(TC["purple"], "◆") + c(TC["soft"], tr(" waiting on you   "))
               + c(TC["amber"], "●") + c(TC["soft"], tr(" in progress   ")) + c(TC["grey"], "○") + c(TC["soft"], tr(" not started   "))
               + c(TC["green"], "●") + c(TC["soft"], tr(" done")))
    return out


def run_cards(every=30, out=sys.stdout, inp=sys.stdin):
    """The card view, live and scrollable: data every `every` seconds, a redraw on change, scroll or resize."""
    import select
    import termios
    import tty
    import of_store
    positions, loaded, cache = [], 0, load_cache()
    fd = inp.fileno()
    old = termios.tcgetattr(fd)
    lines, offset, fetched, drawn = [], 0, 0, None
    out.write("\033[?1049h\033[?25l\033[?1000h\033[?1006h")
    out.flush()
    tty.setcbreak(fd)
    try:
        while True:
            cols, rows = shutil.get_terminal_size((100, 40))
            if time.time() - fetched >= every or not lines:
                if time.time() - loaded > 600:
                    try:
                        positions, loaded = _positions(), time.time()
                    except Exception:  # noqa: BLE001
                        loaded = time.time()
                conn = of_store.connect()
                try:
                    ledger = load_ledger()
                    if not ledger.get("error"):
                        vs = [t for _, _, tv in views_of(ledger, conn, time.time()) for t in tv]
                        if fill_plain(vs, positions, cache):
                            save_cache(cache)
                    lines = card_lines(ledger, conn, positions=positions, cache=cache, width=cols)
                finally:
                    conn.close()
                fetched = time.time()
            view = rows - 1
            offset = max(0, min(offset, max(0, len(lines) - view)))
            frame = (offset, cols, rows, tuple(lines))
            if frame != drawn:
                body = lines[offset:offset + view]
                more = len(lines) > view
                hint = (tr("  ↑↓ mouse wheel · PgUp/PgDn   ") + f"{offset + 1}–{min(len(lines), offset + view)}" + tr(" of ") + f"{len(lines)}"
                        if more else "")
                out.write("\033[H\033[2J" + "\n".join(body) + f"\033[{rows};1H\033[{TC['soft']}m{hint}\033[0m")
                out.flush()
                drawn = frame
            ready, _, _ = select.select([fd], [], [], 1.0)
            if not ready:
                continue
            data = os.read(fd, 256).decode("utf-8", "ignore")
            step = 0
            step -= 3 * data.count("\033[<64;") + data.count("\033[A") + data.count("k")
            step += 3 * data.count("\033[<65;") + data.count("\033[B") + data.count("j")
            step -= view * data.count("\033[5~")
            step += view * data.count("\033[6~")
            if "g" in data:
                offset, step = 0, 0
            if "G" in data:
                offset, step = len(lines), 0
            offset += step
    finally:
        out.write("\033[?1000l\033[?1006l\033[?25h\033[?1049l")
        out.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def run(every=30, once=False, out=sys.stdout, text=True, cards=False):
    import hashlib
    if cards and not once:
        return run_cards(every=every, out=out)
    import of_store
    positions, loaded = [], 0
    cache = load_cache()
    shown = None
    work = os.path.join(of_events.STATE_DIR, "conductor", "panel")
    os.makedirs(work, exist_ok=True)
    while True:
        if time.time() - loaded > 600:
            try:
                positions, loaded = _positions(), time.time()
            except Exception:  # noqa: BLE001  titles fall back to position names
                loaded = time.time()
        conn = of_store.connect()
        try:
            ledger = load_ledger()
            if not ledger.get("error"):
                vs = [t for _, _, tv in views_of(ledger, conn, time.time()) for t in tv]
                if fill_plain(vs, positions, cache):
                    save_cache(cache)
            body = render(ledger, conn, positions=positions, cache=cache) if text or once else None
            page = None if text or once else (ledger, render(ledger, conn, positions=positions, cache=cache,
                                                              color=False))
            if page:
                cols, rows, wpx, hpx = pane_pixels(out)
                pw, ph = picture_size(cols, rows, wpx, hpx)
                html_text = render_html(ledger, conn, positions=positions, cache=cache, width=pw, height=ph)
        finally:
            conn.close()
        if once:
            out.write(body + "\n")
            return 0
        if text:
            out.write("\033[H\033[2J" + body + "\n")
            out.flush()
            time.sleep(every)
            continue
        # redraw only when what it says (minutes aside) or the pane's size changed
        steady = "\n".join(x for x in page[1].split("\n")[1:] if tr("movement") not in x)  # minutes passing redraw nothing
        key = hashlib.sha1((steady + f"{cols}x{rows}").encode()).hexdigest()
        if key != shown:
            png = os.path.join(work, "panel.png")
            if render_png(html_text, pw, ph, png):
                out.write("\033[H\033[2J\033[?25l" + kitty_image(png, cols, rows))
            else:
                out.write("\033[H\033[2J" + page[1] + "\n")
            out.flush()
            shown = key
        time.sleep(every)
