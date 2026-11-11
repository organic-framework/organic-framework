"""Answering an ask from the owner's popup (`of asks --watch`, herdr prefix+a).

The owner reads a question card (the question, its options, who asks, how long it waits, the
asker's last message before it, links) and answers in place: an option (1..9) or free text. The
answer:
- closes the ask and its duplicates from the same group: `of.agent_question_answered`
  {ask_id, ask_ids, answer, via: asks-ui, by: owner} (an answer given in the person's conductor's chat is
  recorded the same way with via: conductor and delivered the same way: bin/of_asks_chat.py);
- goes into the pane of every asker in the group in the frame "Answer from the founder to your question <id>: …",
  declared as the owner's own words (origin human, channel asks-ui), not as a tool's injection.

Delivery is checked, not trusted (2026-11-08: 5 of 61 answers in a day never reached the asker; a detached
waiter gave up after an hour or a full input box, a herdr error was shown red for a second, and a group answer went
to its first asker only). The answer event is the record; the delivery's record is the collector's `of.human_prompt`
carrying the frame in the asker's position. `redeliver_pass` types what has no such record, for 24 h, when the agent is
idle: at the end of the receiver's turn (the listener runs `of asks deliver <position>`) and at every pulse. A question
`of ask` routed to a unit owner is checked the same way.

The founder's answers come first and together (2026-11-09: four answers given at 10:35-10:37Z reached
platform_owner at 10:56 and 11:01: the pulse came every 5 minutes, an automatic nudge took the idle pane at 10:50, and
the answers were typed one at a time, so the second prompt found the agent busy). One pass types one prompt per
position, the founder's answers to it joined oldest first, before any nudge of the same pulse.
"""

import json
import os
import re
import subprocess
import sys
import time

CONTEXT_LINES = 15
REDELIVER_MAX_AGE_H = 24   # an answer or routed question older than this is not typed any more
REDELIVER_MIN_AGE_S = 120  # the first attempt (the popup, `of ask`) and its record come first
REDELIVER_SOON_S = 10      # at the end of a turn: the popup's own attempt (3 s) and its record are over
JOIN_MAX_BYTES = 6000      # one prompt's joined texts (the collector keeps the first 8 KB of a prompt)
REDELIVER_EVERY_MIN = 30   # after a typed attempt that left no record (a session without the collector)
REDELIVER_TRIES = 3
LATE_AFTER_S = 600         # a later delivery says when it was answered


def ask_details(conn, ask_id):
    """(payload of the ask event, session id, position, ts)."""
    row = conn.execute("SELECT payload, session_id, position, ts FROM event WHERE event_id = ?", (ask_id,)).fetchone()
    if not row:
        return {}, None, None, None
    try:
        payload = json.loads(row[0]) if row[0] else {}
    except ValueError:
        payload = {}
    return payload, row[1], row[2], row[3]


def options(payload):
    qs = payload.get("questions") or []
    q = qs[0] if qs and isinstance(qs[0], dict) else {}
    out = []
    for o in q.get("options") or []:
        out.append(o.get("label") if isinstance(o, dict) else str(o))
    return [o for o in out if o]


def full_question(payload, fallback):
    qs = payload.get("questions") or []
    q = qs[0] if qs and isinstance(qs[0], dict) else {}
    return q.get("question") or fallback or ""


def last_message(conn, session, position, ts):
    """The asker's last message before the question (the end of its last turn), at most CONTEXT_LINES lines."""
    if not ts:
        return ""
    if session:
        row = conn.execute("SELECT payload FROM event WHERE name = 'of.turn_end' AND session_id = ? AND ts <= ? "
                           "ORDER BY ts DESC LIMIT 1", (session, ts)).fetchone()
    elif position:
        row = conn.execute("SELECT payload FROM event WHERE name = 'of.turn_end' AND position = ? AND ts <= ? "
                           "ORDER BY ts DESC LIMIT 1", (position, ts)).fetchone()
    else:
        row = None
    if not row:
        return ""
    try:
        text = (json.loads(row[0]) or {}).get("last_message") or ""
    except ValueError:
        return ""
    return text.rstrip()


LINK = re.compile(r"(https?://\S+|\b[\w.-]+/[\w.-]+#\d+\b|(?<![\w/])#\d{2,6}\b|"
                  r"(?:~|\.{0,2})?/?[\w.-]+(?:/[\w.-]+)+\.(?:md|py|rs|ts|tsx|js|yaml|yml|toml|sh|sql|json))")


def links(*texts):
    seen, out = set(), []
    for t in texts:
        for m in LINK.findall(t or ""):
            m = m.rstrip(".,;:)»\"'`")
            if m not in seen:
                seen.add(m)
                out.append(m)
    return out[:8]


def transcript_context(conn, session, ts, back=400):
    """The full texts from the transcript (not the clipped event copies): the last assistant text
    before `ts`, and the last AskUserQuestion input. (last_text, ask_input or None)."""
    import of_store
    if not session or not ts:
        return "", None
    rows = conn.execute("SELECT blob_sha256 FROM transcript_record WHERE session_id = ? AND type = 'assistant' "
                        "AND timestamp <= ? ORDER BY timestamp DESC, id DESC LIMIT ?",
                        (session, _plus(ts, 3), back)).fetchall()
    text, ask = "", None
    for (sha,) in rows:
        try:
            rec = json.loads(of_store.get_blob(conn, sha) or b"{}")
        except ValueError:
            continue
        for c in reversed((rec.get("message") or {}).get("content") or []):
            if not isinstance(c, dict):
                continue
            if ask is None and c.get("type") == "tool_use" and c.get("name") == "AskUserQuestion":
                ask = c.get("input") or {}
            if not text and c.get("type") == "text" and (c.get("text") or "").strip():
                text = c["text"].strip()
        if text and ask is not None:
            break
    return text, ask


def _plus(ts, seconds):
    import calendar
    import time
    try:
        t = calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")) + seconds
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ts[19:]
    except (TypeError, ValueError):
        return ts


def last_paragraph(text):
    """The question in a message: its last paragraph that asks something, whole (never cut mid-word)."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    for p in reversed(paras):
        if "?" in p:
            return p
    return paras[-1] if paras else ""


def live_session(position, positions):
    """The session holding a position now: herdr's live agent, else the registry's holder.session."""
    try:
        r = subprocess.run(["herdr", "agent", "list"], capture_output=True, text=True, timeout=5)
        for a in json.loads(r.stdout)["result"]["agents"]:
            if a.get("name") == position and (a.get("agent_session") or {}).get("value"):
                return a["agent_session"]["value"]
    except Exception:
        pass
    p = next((x for x in positions if x.get("name") == position), {})
    return (p.get("holder") or {}).get("session")


def card(conn, item, positions):
    """Everything the popup shows for one item (a group of asks)."""
    first = item.get("first") or {}
    aid = (item.get("ids") or [None])[0]
    payload, session, position, ts = ask_details(conn, aid) if aid else ({}, None, None, None)
    position = position or first.get("position") or (item["who"][0] if item.get("who") else None)
    p = next((x for x in positions if x["name"] == position), {})
    if not session and position:  # a declared ask names the position: its live holder's session
        session = live_session(position, positions)
    q = full_question(payload, item.get("text"))
    opts = options(payload)
    kind = item.get("kind") or ""
    full, ask_input = transcript_context(conn, session, ts) if aid else ("", None)
    if ask_input and (kind == "permission" or payload.get("detected_by") == "tool"):
        qs = ask_input.get("questions") or []
        if qs and isinstance(qs[0], dict):
            q = qs[0].get("question") or q
            opts = [o.get("label") if isinstance(o, dict) else str(o) for o in qs[0].get("options") or []] or opts
    elif kind.startswith("question (heuristic)"):  # asked in the pane, confirmed by the judge (bin/of_judge.py)
        q = (first.get("text") if first.get("judged") else None) or (last_paragraph(full) if full else q)
        opts = list(first.get("options") or []) or opts
        if not opts and full:  # the (а)/(б) options of the closing text: pick one with 1..9
            import of_classify
            opts = of_classify.options_of(full)
    if kind.startswith("blocked:"):  # a blocking screen: its own options (keys), its own snippet
        opts = [f"{o['key']} {o['label']}" for o in payload.get("options") or []]
        full = payload.get("snippet") or ""
        if payload.get("folder"):
            q = q + f"\nFolder: {payload['folder']}"
    ctx = full or (last_message(conn, session, position, ts) if aid else "")
    if ctx.strip() == q.strip():
        ctx = ""
    doing = payload.get("context") or {}
    if not ctx and doing.get("last_message"):
        ctx = doing["last_message"]
    return {"question": q, "options": opts, "position": position, "system": p.get("system") or doing.get("system"),
            "doing": {"mandate": (doing.get("mandate") or {}).get("summary") or p.get("summary"),
                      "brief": (doing.get("mandate") or {}).get("brief") or p.get("mandate"),
                      "turns": doing.get("recent_turns") or []},
            "org": p.get("_org"), "since": item.get("since"), "who": item.get("who") or [], "context": ctx,
            "links": doing.get("links") or links(q, ctx), "session": session, "ids": item.get("ids") or [], "kind": item.get("kind"),
            "answerable": bool(aid) and item.get("kind") not in ("permission", "registry") and
            (not kind.startswith("blocked:") or bool(opts)),
            "blocked": {"pane": payload.get("pane"), "fingerprint": payload.get("fingerprint"),
                        "options": payload.get("options") or []} if kind.startswith("blocked:") else None}


def frame(ask_id, answer):
    return f"Answer from the founder to your question {ask_id[-8:]}: {answer}"


TYPED_VIAS = ("asks-ui", "conductor")  # answers typed into the asker's pane: the popup's, and the conductor's chat's


def typed_answer(p, ask_id):
    """What the asker's pane gets for an answer event's payload: the frame, and for an answer given in the person's
    conductor's chat (of_asks_chat) the person's own words under it, so the asker sees what the answer rests on."""
    text = frame(ask_id, p.get("answer") or "")
    if p.get("via") == "conductor" and p.get("quote"):
        text += f"\n(Given in the conductor's chat at {str(p.get('prompt_ts') or '')[11:16]}Z, in these words: " \
                f"\u00ab{' '.join(str(p['quote']).split())[:600]}\u00bb)"
    return text


def answer_mark(ask_id):
    """What the asker's of.human_prompt carries once the answer reached its pane."""
    return f"Answer from the founder to your question {ask_id[-8:]}"


def routed_frame(asker, unit, ask_id, text, opts, blocking, escalated=None):
    """A question `of ask` routed to a delegated unit's owner, as typed into the owner's pane. `escalated`: the
    escalate_when rule that already sent it to the founder; the owner then rewrites it instead of filing it again."""
    return (f"Question from {asker} (unit {unit}, delegated to you; ask {ask_id[-8:]}"
            f"{'; escalated to the founder' if escalated else ''}{', blocking' if blocking else ''}): {text}"
            + (f"\nOptions: {' | '.join(opts)}" if opts else "")
            + (escalated_note(asker, ask_id, escalated) if escalated else
               f"\n[answer: of send {asker} --reply-to {ask_id[-8:]} \"…\"; then of asks answered {ask_id[-8:]}; "
               "outside your envelope: of ask the founder]"))


def escalated_note(asker, ask_id, rule):
    # 2026-11-08: a unit owner did not know OF had escalated 878829d1 and filed it again, in another card
    # 2026-11-09: a unit owner settled a routed request itself in 20 s and said so in its pane; the
    # founder's copy stayed open, and he does not read panes
    return (f"\n[OF escalated it to the founder ({rule}): it is on his queue in {asker}'s words. Do not file it again. "
            f"Your rewrite for him, in Russian and standing on its own, replaces that copy: "
            f"of ask \"…\" --escalates {ask_id[-8:]}. His answer reaches you and {asker}. "
            f"If you settle it yourself, answer {asker} (of send {asker} --reply-to {ask_id[-8:]} \"…\") and close the "
            f"founder's copy: of asks answered {ask_id[-8:]} \"what you did\".]")


def inbox_only(p):
    """A routed ask that went to the receiver's inbox (of send --fyi) and is never typed into its pane: a usage-limit
    screen for of_capacity (of_capacity, 2026-11-09). Asks filed before the `delivery` field count by their type."""
    import of_blocked
    return p.get("delivery") == "fyi" or (p.get("detected_by") == "blocked" and p.get("blocked_type") in of_blocked.FYI)


def routed_mark(ask_id):
    return f"delegated to you; ask {ask_id[-8:]}"


def escalated_mark(ask_id):
    """In the first line (the collector keeps 8 KB of a prompt), as routed_mark is."""
    return f"ask {ask_id[-8:]}; escalated to the founder"


def record(ask_ids, answer, position=None, session=None):
    import of_events
    import of_hook
    return of_events.emit("agent_question_answered", {
        "ask_id": ask_ids[0], "ask_ids": list(ask_ids), "answer": of_hook.mask(answer), "via": "asks-ui",
        "by": "owner"}, position=position, session=session)


SAME_ANSWER_S = 600  # the same answer to the same asks within this is one answer


def recorded_already(conn, ids, text, now=None):
    """True when the popup recorded this answer to these asks within SAME_ANSWER_S (2026-11-10, of_platform's ask
    8ee7f1bc: the popup closed while its thread was sending; the outbox sender saw the ask still open in the
    listener's view, which lags, and recorded the answer again 0.85 s later, so it was typed twice)."""
    import of_hook
    since = _iso((now or time.time()) - SAME_ANSWER_S)
    want, masked = set(ids), of_hook.mask(text)
    for (payload,) in conn.execute("SELECT payload FROM event WHERE name = 'of.agent_question_answered' AND ts >= ? "
                                   "AND json_extract(payload, '$.via') = 'asks-ui'", (since,)):
        try:
            p = json.loads(payload or "{}")
        except ValueError:
            continue
        if p.get("answer") == masked and set(p.get("ask_ids") or [p.get("ask_id")]) == want:
            return True
    return False


def deliver(pane, text, session, wait_s=3, sender="owner", human_channel="asks-ui"):
    """Type it now if the agent is idle or done. Returns a status line. Whatever is not typed now, the pulse types
    later (`redeliver_pass`): it checks the collector's record, so nothing waits on a process that may give up.
    human_channel None: the text is a tool's (a routed question from `of ask`), declared as an injection,
    never as the owner's words (2026-11-06: a routed question typed as 'human' closed an ask, f7ea7dc8)."""
    import of_relay_client
    ok, why = of_relay_client.herdr_deliver(pane, text, session, sender, wait_s=wait_s, human_channel=human_channel)
    if ok is True:
        return "delivered into the pane"
    if ok == "deferred":
        return "the agent is busy: OF types it in when its turn ends"
    return f"not typed now ({why}); OF tries again every 5 minutes for {REDELIVER_MAX_AGE_H} h"


def drafts_dir():
    import of_events
    d = os.path.join(of_events.STATE_DIR, "asks-drafts")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def save_draft(item, text):
    """Every answer is on disk before it is sent: a failure loses nothing."""
    import time
    aid = (item.get("ids") or ["?"])[0]
    path = os.path.join(drafts_dir(), f"{aid}.json")
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump({"ask_id": aid, "ask_ids": item.get("ids") or [], "who": item.get("who") or [], "text": text,
                   "saved_at": time.time(), "item": {k: item.get(k) for k in ("who", "text", "since", "ids", "kind")}},
                  fh, ensure_ascii=False)
    return path


def drop_draft(item):
    try:
        os.remove(os.path.join(drafts_dir(), f"{(item.get('ids') or ['?'])[0]}.json"))
    except FileNotFoundError:
        pass


def archive_drafts(open_ids):
    """Move the drafts whose asks are all closed into asks-drafts/archive (they would answer a closed ask; the founder
    answered elsewhere, e.g. in the chat). Returns the ask ids archived. open_ids: the ids of the open asks in a FRESH
    list, never a cached one (a cached list could archive a live draft)."""
    import time
    out = []
    arch = os.path.join(drafts_dir(), "archive")
    for d in unsent_drafts():
        if any(i in open_ids for i in d.get("ask_ids") or [d.get("ask_id")]):
            continue
        os.makedirs(arch, mode=0o700, exist_ok=True)
        src = os.path.join(drafts_dir(), f"{d.get('ask_id')}.json")
        try:
            os.replace(src, os.path.join(arch, f"{d.get('ask_id')}-{int(time.time())}.json"))
            out.append(d.get("ask_id"))
        except OSError:
            continue
    return out


def unsent_drafts():
    out = []
    for f in sorted(os.listdir(drafts_dir())):
        if f.endswith(".json"):
            try:
                with open(os.path.join(drafts_dir(), f)) as fh:
                    out.append(json.load(fh))
            except (OSError, ValueError):
                continue
    return out


def recipients(conn, ids, first_position=None, first_session=None):
    """[(position, session, [ask ids])]: every asker in the group once, the card's asker first. An escalated ask (OF's
    copy, or the unit owner's rewrite) also reaches the routed ask's asker and its unit owner, each under the id it
    knows: the routed ask's, or its own rewrite's."""
    out = {}

    def add(position, session, aid):
        if position:
            e = out.setdefault(position, [session, []])
            if aid not in e[1]:
                e[1].append(aid)
    for aid in ids:
        payload, session, position, _ = ask_details(conn, aid)
        if aid == ids[0]:
            position, session = first_position or position, first_session or session
        src = payload.get("escalated_from")
        if not src:
            add(position, session, aid)
            continue
        p0, s0, asker, _ = ask_details(conn, src)
        add(asker, s0, src)
        if position != asker:
            add(position, session, aid)
        add(p0.get("to"), None, src)
    return [(p, s, a) for p, (s, a) in out.items()]


def answer(conn, item, text, positions, pane_of):
    """Record and deliver one answer to every asker in the group. pane_of(position) -> pane id or None.
    Returns (ok, status line): ok once the answer is recorded; what is not typed now, the pulse types later.
    `conn` must belong to the calling thread (SQLite objects stay in the thread that made them)."""
    save_draft(item, text)
    c = card(conn, item, positions)
    if not c["answerable"]:
        return False, "cannot answer this item here"
    if c.get("blocked"):
        return answer_blocked(c, text)
    lock = _lock()  # the check and the record together: the popup's thread and the outbox sender race (see below)
    try:
        again = recorded_already(conn, c["ids"], text)
        if not again:
            record(c["ids"], text, c["position"], c["session"])
    finally:
        lock.close()
    drop_draft(item)
    if again:
        return True, "answer recorded already; OF types it in when the agent's turn ends"
    rec = recipients(conn, c["ids"], c["position"], c["session"])
    out = []
    for position, session, ids in rec:
        pane = pane_of(position)
        session = session or live_session(position, positions)
        status = deliver(pane, frame(ids[0], text), session) if pane else \
            f"no live pane: OF types it when {position} has one (for {REDELIVER_MAX_AGE_H} h)"
        out.append(f"{position}: {status}" if len(rec) > 1 else status)
    return True, "answer recorded; " + ("; ".join(out) or "no asker to deliver it to")


def _tries_path():
    import of_events
    return os.path.join(of_events.STATE_DIR, "deliveries.json")


def _tries_load(now):
    try:
        with open(_tries_path()) as fh:
            d = json.load(fh) or {}
    except (OSError, ValueError):
        return {}
    keep = now - 2 * REDELIVER_MAX_AGE_H * 3600
    return {k: [t for t in v if t >= keep] for k, v in d.items() if any(t >= keep for t in v)}


def _tries_save(d):
    path = _tries_path()
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(d, fh)
    os.replace(tmp, path)


def delivered(conn, position, marks, since):
    """True when a prompt carrying one of `marks` reached `position`'s session at or after `since`."""
    for m in marks:
        if conn.execute("SELECT 1 FROM event WHERE name = 'of.human_prompt' AND position = ? AND ts >= ? "
                        "AND payload LIKE ? LIMIT 1", (position, since, f"%{m}%")).fetchone():
            return True
    return False


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t))


TURN_END_GRACE_S = 90  # a turn end this recent is the one this delivery follows


def busy_since(conn, position, ts, now):
    """True when the position ended no turn between ts and the last TURN_END_GRACE_S: it was busy all along, and the
    answer waited for this turn end by design (4c70a3f), not because an attempt failed (of_platform, 2026-11-10)."""
    return not conn.execute("SELECT 1 FROM event WHERE name = 'of.turn_end' AND position = ? AND ts > ? AND ts < ? "
                            "LIMIT 1", (position, ts, _iso(now - TURN_END_GRACE_S))).fetchone()


def _late(ts, now, what, busy=False):
    import of_asks
    age = of_asks.seconds_since(ts, now) or 0
    if age < LATE_AFTER_S:
        return ""
    if busy:
        return f"\n(OF: {what} at {ts[11:16]}Z; you were busy, so it waited for the end of your turn.)"
    return f"\n(OF: {what} at {ts[11:16]}Z; delivered late, the first attempt did not reach your pane.)"


def pending(conn, asks, positions, now=None, min_age=REDELIVER_MIN_AGE_S, only=None):
    """What should be in a pane and has no record there, younger than REDELIVER_MAX_AGE_H and older than `min_age`
    seconds: the founder's answers from the popup (every asker in the group) and the open questions routed to a unit
    owner; `only`: one position's. [{key, position, text, marks, since, sender, channel}]."""
    import of_asks
    now = now or time.time()
    since = _iso(now - REDELIVER_MAX_AGE_H * 3600)
    out, seen = [], set()
    for eid, ts, payload in conn.execute(
            "SELECT event_id, ts, payload FROM event WHERE name = 'of.agent_question_answered' AND ts >= ? "
            "ORDER BY ts", (since,)).fetchall():
        try:
            p = json.loads(payload or "{}")
        except ValueError:
            continue
        if p.get("via") not in TYPED_VIAS or not p.get("answer") or p.get("keys") or p.get("verdict"):
            continue  # a dialog's keys, a not-a-question verdict: nothing to type
        if (of_asks.seconds_since(ts, now) or 0) < min_age:
            continue
        for position, _, ids in recipients(conn, p.get("ask_ids") or [p.get("ask_id")]):
            marks = [answer_mark(a) for a in ids]
            same = (position, tuple(marks), p["answer"])
            if same in seen:
                continue  # the same answer recorded twice (before recorded_already) is typed once
            seen.add(same)
            if (not only or position == only) and not delivered(conn, position, marks, ts):
                out.append({"key": f"answer:{eid}:{position}", "position": position, "marks": marks, "since": ts,
                            "text": typed_answer(p, ids[0]) + _late(ts, now, "the founder answered",
                                                                       busy=busy_since(conn, position, ts, now)),
                            "sender": "owner", "channel": "asks-ui"})
    open_ids = {a["event_id"] for a in asks if a.get("state") == "open"}
    units = {x["name"]: x.get("_unit") for x in positions}
    for eid, ts, asker, payload in conn.execute(
            "SELECT event_id, ts, position, payload FROM event WHERE name = 'of.agent_question' AND ts >= ? "
            "AND json_extract(payload, '$.to') IS NOT NULL AND json_extract(payload, '$.to') != 'founder' "
            "ORDER BY ts", (since,)).fetchall():
        if eid not in open_ids or (of_asks.seconds_since(ts, now) or 0) < min_age:
            continue
        try:
            p = json.loads(payload or "{}")
        except ValueError:
            continue
        to = p.get("to")
        if not asker or to == asker or (only and to != only) or inbox_only(p) or \
                delivered(conn, to, [routed_mark(eid)], ts):
            continue
        text = routed_frame(asker, units.get(asker), eid, full_question(p, ""), options(p), bool(p.get("blocking")))
        out.append({"key": f"routed:{eid}:{to}", "position": to, "marks": [routed_mark(eid)], "since": ts,
                    "text": text + _late(ts, now, f"{asker} asked"), "sender": asker, "channel": None})
    # a routed ask OF escalated: its unit owner is told once, so it rewrites the founder's copy instead of refiling
    by_src = {}
    for a in asks:
        if a.get("escalated_from"):
            by_src.setdefault(a["escalated_from"], []).append(a)
    for eid, ts, payload in conn.execute(
            "SELECT event_id, ts, payload FROM event WHERE name = 'of.ask_escalated' AND ts >= ? ORDER BY ts",
            (since,)).fetchall():
        try:
            p = json.loads(payload or "{}")
        except ValueError:
            continue
        src, owner, rule = p.get("ask_id"), p.get("from_owner"), p.get("rule")
        related = by_src.get(src) or []
        if not src or not owner or rule == "by_owner" or (only and owner != only) \
                or (of_asks.seconds_since(ts, now) or 0) < min_age \
                or any(a.get("source") != "escalated" for a in related) \
                or (related and not any(a.get("state") == "open" for a in related)) \
                or delivered(conn, owner, [escalated_mark(src)], ts):
            continue  # rewritten already, answered or withdrawn, or the owner has seen the note
        q, _, asker, qts = ask_details(conn, src)
        if not asker:
            continue
        unit = units.get(asker)
        text = (f"Question from {asker} (unit {unit}, delegated to you; ask {src[-8:]}; escalated to the founder): "
                "the one typed in before." + escalated_note(asker, src, rule)
                if delivered(conn, owner, [routed_mark(src)], qts or ts) else
                routed_frame(asker, unit, src, full_question(q, ""), options(q), bool(q.get("blocking")), escalated=rule))
        out.append({"key": f"escalated:{eid}:{owner}", "position": owner, "marks": [escalated_mark(src)], "since": ts,
                    "text": text, "sender": asker, "channel": None})
    return out


def _lock():
    """One delivery pass at a time (the pulse's and a turn end's): the second one reads the first one's tries."""
    import fcntl
    import of_events
    fh = open(os.open(os.path.join(of_events.STATE_DIR, "deliveries.lock"), os.O_WRONLY | os.O_CREAT, 0o600))
    fcntl.flock(fh, fcntl.LOCK_EX)
    return fh


def prompts(todo):
    """[[item, ...]]: one prompt per position, the first that pending lists for it. The founder's answers go first and
    together, oldest first; other items join only their own kind (sender and channel), within JOIN_MAX_BYTES."""
    out = {}
    for d in sorted(todo, key=lambda d: d["sender"] != "owner"):  # stable: pending's order within a kind
        cur = out.get(d["position"])
        if cur is None:
            out[d["position"]] = [d]
        elif (cur[0]["sender"], cur[0]["channel"]) == (d["sender"], d["channel"]) and \
                len(joined(cur + [d]).encode()) <= JOIN_MAX_BYTES:
            cur.append(d)
    return list(out.values())


def joined(items):
    return "\n\n".join(d["text"] for d in items)


def redeliver_pass(conn, asks, positions, agents, bind, now=None, deliver_fn=None, log=print, only=None,
                   min_age=REDELIVER_MIN_AGE_S, wait_s=3, need_idle=True):
    """Type what `pending` lists into the position's live pane when its agent is idle or done: one prompt per position
    (`prompts`), each item at most REDELIVER_TRIES typed attempts REDELIVER_EVERY_MIN apart (a typed text the
    collector never recorded). The pulse calls it with the herdr snapshot's idle agents; the end of a turn with
    `only` its position, need_idle False and a longer wait (herdr may still say working for a moment). Returns the
    number of items typed."""
    import of_relay_client
    now = now or time.time()
    todo = pending(conn, asks, positions, now, min_age=min_age, only=only)
    if not todo:
        return 0
    live = {p["name"]: ag for p, ag in (bind(positions, agents)[0] if bind and agents is not None else [])}
    lock = _lock()
    try:
        tries = _tries_load(now)
        ready = []
        for d in todo:
            ag = live.get(d["position"])
            past = tries.get(d["key"]) or []
            if not ag or (need_idle and ag.get("agent_status") not in ("idle", "done")) \
                    or len(past) >= REDELIVER_TRIES or (past and now - past[-1] < REDELIVER_EVERY_MIN * 60):
                continue
            ready.append(d)
        sent = 0
        for group in prompts(ready):
            first = group[0]
            ag = live[first["position"]]
            session = (ag.get("agent_session") or {}).get("value")
            # taken or typed meanwhile (the popup, another pass): the rest goes in the next pass
            skip = (lambda g=group: any(delivered(conn, d["position"], d["marks"], d["since"]) for d in g))
            text = joined(group)
            if deliver_fn:
                ok, why = deliver_fn(ag["pane_id"], text, session, first["sender"], first["channel"], skip)
            else:
                ok, why = of_relay_client.herdr_deliver(ag["pane_id"], text, session, first["sender"], wait_s=wait_s,
                                                        human_channel=first["channel"], skip_if=skip)
            if ok is True:
                for d in group:
                    tries.setdefault(d["key"], []).append(now)
                sent += len(group)
                log(f"redelivered {', '.join(d['key'].split(':')[0] for d in group)} to {first['position']}")
        _tries_save(tries)
    finally:
        lock.close()
    return sent


def not_a_question(item, kind=None):
    """The owner's verdict in the popup (key n): this was not a question. It closes the ask (and its
    group) and stores a label for calibrating the heuristic (the classifier's version goes up after a
    re-calibration). Returns a status line."""
    import of_events
    ids = item.get("ids") or []
    if not ids:
        return "nothing to mark"
    of_events.emit("agent_question_answered", {"ask_id": ids[0], "ask_ids": ids, "via": "asks-ui",
                                               "verdict": "not_a_question", "by": "owner"})
    of_events.emit("question_labelled", {"ask_id": ids[0], "ask_ids": ids, "label": "not_a_question",
                                         "kind": kind or item.get("kind"), "text": (item.get("text") or "")[:600],
                                         "by": "owner"})
    return "marked: not a question (kept as a label for the heuristic)"


def answer_blocked(c, text):
    """Press the option's keys in that pane, only if the screen still shows the same dialog."""
    import of_blocked
    b = c["blocked"]
    opt = next((o for o in b["options"] if text.startswith(o["key"] + " ") or text == o["key"]), None)
    if not opt:
        return False, "pick one of the dialog's options"
    r = subprocess.run(["herdr", "pane", "read", b["pane"], "--source", "visible", "--lines", "40"],
                       capture_output=True, text=True, timeout=10)
    now = of_blocked.detect(r.stdout if r.returncode == 0 else "")
    if not now or now["fingerprint"] != b["fingerprint"]:
        return False, "the dialog has changed: open the session (g)"
    keys = of_blocked.keys_for(opt)
    s = subprocess.run(["herdr", "pane", "send-keys", b["pane"], *keys], capture_output=True, text=True, timeout=10)
    if s.returncode != 0:
        return False, f"keys not sent: {(s.stderr or s.stdout).strip()[:120]}"
    import of_events
    import of_hook
    of_events.emit("agent_question_answered", {"ask_id": c["ids"][0], "ask_ids": c["ids"], "answer": of_hook.mask(text),
                                               "via": "asks-ui", "keys": keys, "by": "owner"},
                   position=c.get("position"), session=c.get("session"))
    return True, f"pressed {' '.join(keys)} in {c.get('position') or b['pane']}"
