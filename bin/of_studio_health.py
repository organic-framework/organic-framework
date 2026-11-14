"""Studio's Health tab, and the person's card when their own work is hit (docs/health.md §3; of_architect's hand-over
f06e0eb7, accepted by of_studio on 2026-11-14).

Both read the state that `of health run` writes every minute (`health.json` in OF's state folder; of_platform's
bin/of_health.py): every check of this machine, and of-server's own checks once that job carries them. Neither reads the
event log. This module is kept apart from bin/of_studio.py, so that the minute's job imports only this file.

- body(state, now): the tab's content. Every check with its state, value, limits, owner and how long it has been in
  that state, failed first. A state older than STALE_S says that `of health run` itself has stopped.
- cards(state, now): one card to the person for each episode of a check that hits their own work (PERSON), once it has
  failed for CARD_AFTER_S. The card names the check, its owner and what the person sees meanwhile, and it closes
  itself when the check is back. `of health run` calls it after writing the state.
"""

import calendar
import html
import json
import os
import time

import of_events

CARD_AFTER_S = 300      # docs/health.md §3: a card only after 5 minutes failed
STALE_S = 180           # `of health run` writes every minute: a state this old means the job has stopped
CARD_SOURCE = "health"
CARD_FROM = "of_studio"  # the card is Studio's (docs/health.md §5); an answer to it comes here
JOB_OWNER = "of_platform"

# The checks that hit the person's own work (docs/health.md §3), each with what they see meanwhile.
PERSON = {
    "server.health": "Studio and the messages between agents go through of-server, so they may be slow or not answer.",
    "relay.oldest": "A message to an idle agent has waited over an hour to be typed in, so agents may be waiting on "
                    "each other without knowing it.",
    "view.age.asks": "Your questions screen reads this view, so new questions may show late and answered ones may "
                     "still show.",
    "hooks.errors": "Part of what the agents do is recorded late or not at all, so their questions and states may "
                    "reach you late.",
    "commit.smoke": "The newest commit in this machine's working copy fails OF's smoke check, so agents' hooks and "
                    "`of` commands may fail until it is fixed or rolled back.",
}
RANK = {"failed": 0, "degraded": 1, "ok": 2}
PERCENT = ("hooks.errors", "machine.memory")


def state_path():
    """The file `of health run` writes (of_health.state_path)."""
    return os.path.join(of_events.STATE_DIR, "health.json")


def cards_path():
    return os.path.join(of_events.STATE_DIR, "studio", "health-cards.json")


def load_state(path=None):
    try:
        with open(path or state_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def epoch(ts):
    """'2026-11-14T06:06:00Z' (as `of health run` writes it) -> seconds; None when it is not such a time."""
    try:
        return float(calendar.timegm(time.strptime(str(ts)[:19], "%Y-%m-%dT%H:%M:%S")))
    except (TypeError, ValueError):
        return None


def span(sec):
    """A duration as a person reads it: 40 s, 12 min, 3 h, 2 d."""
    sec = max(0, int(sec))
    if sec < 90:
        return f"{sec} s"
    if sec < 2 * 3600:
        return f"{sec // 60} min"
    return f"{sec // 3600} h" if sec < 2 * 86400 else f"{sec // 86400} d"


def value_text(check, v):
    """A check's reading with its unit (docs/health.md §2)."""
    if v is None:
        return "no reading"
    if isinstance(v, str):
        return v
    if check in PERCENT:
        return f"{100 * v:.1f}%"
    if check == "machine.disk":
        return f"{v:.1f} GiB free"
    return f"{v:.2f} s" if v < 10 else span(v)   # seconds: answer times, lags, ages, queue waits


def limits(r):
    """(degraded, failed) of a check as `of health run` writes it, or as of-server serves it."""
    lim = r.get("limit")
    if isinstance(lim, (list, tuple)) and len(lim) == 2:
        return lim[0], lim[1]
    if "limit_degraded" in r or "limit_failed" in r:
        return r.get("limit_degraded"), r.get("limit_failed")
    return None, None


def limit_text(check, r):
    d, f = limits(r)
    if d is None and f is None:
        return "–"
    word = "under" if check == "machine.disk" else "over"
    return f"{word} {value_text(check, d)} / {word} {value_text(check, f)}"


def rows(state):
    """[(check, record)] failed first, then degraded, then ok; by name within each."""
    checks = (state or {}).get("checks") or {}
    return sorted(checks.items(), key=lambda kv: (RANK.get(kv[1].get("state"), 0), kv[0]))


def body(state, now=None):
    """The Health tab: every check of this machine and of-server, as `of health run` last wrote them."""
    e = html.escape
    now = time.time() if now is None else now
    if not state:
        return ('<h2>Health</h2><p class="note">No health state on this machine yet: <code>of health install</code> sets '
                f'up the job that checks OF every minute ({e(JOB_OWNER)}; docs/health.md).</p>')
    at = epoch(state.get("at"))
    out = ["<h2>Health <span class=\"sub\">this machine and of-server, checked every minute (docs/health.md §2)</span></h2>"]
    if at is None or now - at > STALE_S:
        out.append(f'<p class="note"><span class="flag">stopped</span> The state is {e(span(now - at)) + " old" if at else "undated"}: '
                   f'<code>of health run</code> has not written it since, so what follows may be out of date. The job is '
                   f'{e(JOB_OWNER)}\'s.</p>')
    rs = rows(state)
    n = {s: sum(1 for _, r in rs if r.get("state") == s) for s in RANK}
    out.append(f'<p class="headline">{n["failed"]} failed · {n["degraded"]} degraded · {n["ok"]} ok · as of '
               f'{e(str(state.get("at") or "?"))}</p>')
    trs = []
    for check, r in rs:
        st = r.get("state") or "?"
        since = epoch(r.get("since"))
        tag = ' <span class="yours">your work</span>' if check in PERSON else ""
        trs.append(
            f'<tr class="{e(st)}"><td>{e(check)}{tag}</td>'
            f'<td class="state {e(st)}">{e(st)}</td><td class="num">{e(value_text(check, r.get("value")))}</td>'
            f'<td class="num">{e(limit_text(check, r))}</td>'
            f'<td class="age">{e(span(now - since)) if since else "–"}</td><td>{e(str(r.get("owner") or "–"))}</td>'
            f'<td class="text">{e(str(r.get("detail") or ""))}</td></tr>')
    out.append('<div class="scroll"><table class="health"><thead><tr><th>Check</th><th>State</th><th>Value</th>'
               '<th>Degraded / failed</th><th>In this state for</th><th>Owner</th><th>Detail</th></tr></thead><tbody>'
               + "".join(trs) + "</tbody></table></div>")
    missing = [c for c in PERSON if c not in dict(rs)]
    if missing:
        out.append(f'<p class="note">Not in this machine\'s state yet: {e(", ".join(missing))}. A check that is not here '
                   'brings no card; of-server\'s own checks reach this page once <code>of health run</code> carries '
                   'them.</p>')
    out.append('<p class="legend">A check\'s owner hears of a failure that lasted 5 minutes, and again when it is over. '
               '<span class="yours">your work</span> marks the checks that also bring you a card after 5 minutes failed; '
               'the card closes itself when the check is back. The limits are docs/health.md §2\'s; each owner tunes '
               'them there.</p>')
    return "".join(out)


def card_text(check, r, now):
    from of_locale import tr
    d, f = limits(r)
    return tr("OF's health check {check} has failed on this machine for {mins} min: {value}, the limit is {limit}. "
              "{meanwhile} Its owner is {owner}; Studio's Health tab shows every check. This card closes itself when "
              "the check is back. What should be done?",
              check=check, mins=int((now - (epoch(r.get("since")) or now)) // 60),
              value=value_text(check, r.get("value")), limit=value_text(check, f) if f is not None else "–",
              meanwhile=tr(PERSON[check]), owner=r.get("owner") or "?")


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save(path, mem):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as fh:
        json.dump(mem, fh, indent=1)
    os.replace(path + ".tmp", path)


def cards(state, now=None, emit=None, path=None):
    """Open a card for each PERSON check failed for CARD_AFTER_S that has none, and close the card of each check that is
    no longer failed (or no longer measured). Each card is kept in health-cards.json by its event id, so closing it reads
    nothing from the log. Returns [(opened|closed, check, event id)]."""
    from of_locale import tr
    now = time.time() if now is None else now
    emit = emit or of_events.emit
    path = path or cards_path()
    mem = _load(path)
    checks = (state or {}).get("checks") or {}
    done = []
    for check in PERSON:
        r, card = checks.get(check), mem.get(check)
        since = epoch((r or {}).get("since"))
        failing = bool(r) and r.get("state") == "failed"
        if failing and card is None and since is not None and now - since >= CARD_AFTER_S:
            owner = r.get("owner") or JOB_OWNER
            eid = emit("agent_question", {
                "detected_by": "declared", "source": CARD_SOURCE, "from_position": CARD_FROM, "to": "owner",
                "topic_key": f"health-{check}", "blocking": False,
                "questions": [{"question": card_text(check, r, now),
                               "options": [tr("leave it to {owner} (recommended: they own the fix)", owner=owner),
                                           tr("I want to look at it now with of_conductor")]}]},
                position=CARD_FROM)
            mem[check] = {"event_id": eid, "opened": iso(now), "since": r.get("since")}
            done.append(("opened", check, eid))
        elif card is not None and not failing:
            note = (f"{check} is {r.get('state')} again since {r.get('since')}" if r else f"{check} is no longer measured")
            emit("agent_question_answered", {"ask_id": card["event_id"], "ask_ids": [card["event_id"]], "by": CARD_FROM,
                                             "via": CARD_SOURCE, "note": note}, position=CARD_FROM)
            mem.pop(check)
            done.append(("closed", check, card["event_id"]))
    if done:
        _save(path, mem)
    return done
