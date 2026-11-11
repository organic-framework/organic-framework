"""Answers and tasks through a person's conductor, for a person who reaches OF only through that chat (docs/remote.md
§3 rows 1 and 4; hand-over f4d4b89f from of_architect, 2026-11-11; the founder, 18:18Z: "we are again sitting through
Remote Control, there is no access to herdr, so the cards, the questions, the things to do are not fully usable").

Row 1, an answer recorded from the chat: `of asks answer <id> --option N | --text "<words>" --prompt <event id>`. The
conductor points to the person's own words; OF records the answer only when that prompt is evidence for it:
- an `of.human_prompt` written by the hook, origin human (after any `of.prompt_origin` correction), and not on the
  popup's channel (asks-ui: a tool typing the owner's words into a pane);
- typed in the session holding the person's conductor (a position with role conductor owned by the person the card
  waits on): the event's position is that conductor, or its session is the conductor's holder.session;
- later than the card (a task: than its last reopen too), and at most PROMPT_MAX_AGE_H old;
- naming the card (its id, or its asker when that asker has no other open card of this person), or quoting one of its
  options that no other open card of this person offers. With --option the prompt may quote no other option of the
  card; with --text the words must be in the prompt as typed.
The answer is the person's (`by: owner`, so a task is claimed, never closed), `via: conductor`, with the prompt's id
and words; the asker sees them under the answer. Delivery is the popup's: typed into the asker's pane now when it is
idle, else at its turn end or the pulse (of_asks_answer.pending), for REDELIVER_MAX_AGE_H.

Row 4, tasks with a place: `of ask --task … --where phone|browser|machine` says where the person can do it. A task
with no place, or with a --command, is `machine` (a terminal, a key, Touch ID). `of asks --json` carries `where` and
`in_chat`, and `of asks --chat` prints the person's queue for the conductor's chat: the cards with their options and
ids, the tasks they can do from a phone or a browser with their steps, and what waits for the machine (machine tasks,
dialogs in panes, permissions) as titles, never as steps.
"""

import datetime as dt
import json
import re
import time

WHERES = ("phone", "browser", "machine")
PROMPT_MAX_AGE_H = 24  # an older prompt is not taken as an answer: the person is asked again
AT_MACHINE_KINDS = ("permission", "registry")  # answered by keys in a pane or in a terminal, never from a chat
GONE = ("left", "closed", "retired")


def where_of(ask):
    return ask.get("where") if ask.get("where") in WHERES else "machine"


def in_chat(ask):
    """How the conductor presents an open ask: `answer` (a card the person answers in the chat), `steps` (a task they
    can do where they are), `waiting` (it waits until they are at the machine)."""
    kind = ask.get("kind") or ""
    if kind in AT_MACHINE_KINDS or kind.startswith("blocked:"):
        return "waiting"
    if ask.get("task"):
        return "steps" if where_of(ask) in ("phone", "browser") else "waiting"
    return "answer"


def check_where(where, task, command):
    """`of ask --where`: None when fine, else the refusal."""
    if where is None:
        return None
    if not task:
        return "--where goes with --task: it says where the person can do the work"
    if where not in WHERES:
        return f"--where {where!r}: phone (a link or an app), browser, or machine (a terminal, a key, Touch ID)"
    if command and where != "machine":
        return "a task with --command is typed in a terminal: --where machine, or drop --command"
    return None


def _epoch(ts):
    return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()


def _hm(ts):
    return str(ts)[11:16] + "Z"


def _held_by_person(p):
    h = p.get("holder") or {}
    return h.get("runtime") == "human" or bool(h.get("person"))


def conductors(positions, to):
    """The conductor positions of the person a card waits on. A card to the founder or the owner: the owner's
    conductor (role conductor, owned by founder or owner, or named <org>_conductor; a colleague's is
    <org>_conductor-<person>, docs/conductor.md §4). A card routed to a position a person holds: the conductors that
    position owns. A card routed to an agent has none: that agent answers it."""
    import of_asks
    target = None if to in of_asks.OWNER_ADDRESSES else str(to).rpartition(":")[2]
    if target:
        t = next((p for p in positions if p.get("name") == target), None)
        if not t or not _held_by_person(t):
            return []
    out = []
    for p in positions:
        if p.get("role") != "conductor" or p.get("status") in GONE:
            continue
        if (p.get("owner") == target) if target else \
                (p.get("owner") in ("founder", "owner") or str(p.get("name")).endswith("_conductor")):
            out.append(p)
    return out


def prompt_row(conn, ref):
    """The event a --prompt names: its full id or an unambiguous end or start of at least 8 characters."""
    ref = (ref or "").strip()
    if len(ref) < 8:
        raise ValueError("--prompt takes the event id of the person's prompt (of prompts --mine prints it)")
    rows = conn.execute("SELECT event_id, ts, name, session_id, position, source, payload FROM event "
                        "WHERE event_id = ? OR event_id LIKE ? OR event_id LIKE ? LIMIT 2",
                        (ref, ref + "%", "%" + ref)).fetchall()
    if len(rows) != 1:
        raise ValueError(f"--prompt {ref}: " + ("several events match: give the whole id" if rows
                                                else "no recorded event has this id"))
    eid, ts, name, sid, pos, src, pl = rows[0]
    try:
        payload = json.loads(pl or "{}")
    except ValueError:
        payload = {}
    fix = conn.execute("SELECT json_extract(payload, '$.origin') FROM event WHERE name = 'of.prompt_origin' "
                       "AND json_extract(payload, '$.event_id') = ? ORDER BY ts DESC LIMIT 1", (eid,)).fetchone()
    return {"event_id": eid, "ts": ts, "name": name, "session_id": sid, "position": pos, "source": src,
            "origin": fix[0] if fix and fix[0] else payload.get("origin"), "channel": payload.get("channel"),
            "text": str(payload.get("text") or "")}


def _norm(s):
    import of_ask_text
    s = of_ask_text.fold(s)  # the patterns of what people type live there
    s = re.sub(r"[\"'`*_«»“”„‘’]", " ", s)
    return " ".join(s.split())


def _label(option):
    """An option without its explanation: the text before " (", " - ", " — " or ": "."""
    o = re.split(r"\s[(\[]|\s[-–—]\s|:\s", _norm(option))[0]
    return o.strip(" .,;!?")


def _has(phrase, text):
    return bool(phrase) and re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) is not None


def quoted_options(text, opts, elsewhere=()):
    """Indices of this card's options the prompt quotes (the whole option or its label), leaving out labels another
    open card of the person also offers (a bare "yes" then names no card) and a match inside a longer one."""
    t, taken = _norm(text), set(elsewhere)
    hits = []
    for i, o in enumerate(opts):
        for form in (_norm(o), _label(o)):
            if form and form not in taken and _has(form, t):
                hits.append((i, form))
                break
    keep = {i for i, f in hits if not any(f != g and f in g for _, g in hits)}
    return sorted(keep)


def names_card(text, ask_ids, who, other_who=()):
    """What in the prompt names the card: an id of one of its asks (the 8 characters OF shows, or the whole id), or
    its asker when that asker has no other open card of this person. None when nothing does."""
    t = str(text or "").lower()
    for aid in ask_ids:
        for frag in (aid, aid[-8:]):
            if re.search(r"(?<![0-9a-f])" + re.escape(frag.lower()) + r"(?![0-9a-f])", t):
                return f"its id {aid[-8:]}"
    for w in who:
        if w and w not in other_who and re.search(r"(?<![\w-])" + re.escape(w.lower()) + r"(?![\w-])", t):
            return f"its asker {w}"
    return None


def _queue(asks, to):
    """The person's open asks: the owner's queue, or those routed to the position `to`."""
    import of_asks
    if to in of_asks.OWNER_ADDRESSES:
        return [a for a in of_asks.for_owner(asks) if a["state"] == "open"]
    return [a for a in asks if a["state"] == "open" and a.get("to") == to]


def _options_of(conn, ask):
    import of_asks_answer
    return of_asks_answer.options(of_asks_answer.ask_details(conn, ask["event_id"])[0]) or list(ask.get("options") or [])


def check(conn, asks, positions, ask_ref, prompt_ref, option=None, text=None, now=None):
    """Is the prompt evidence for this answer to this card? Returns {ids, answer, option, prompt, conductor, evidence,
    position, session}; raises ValueError with the reason, said so the conductor can act on it."""
    import of_asks
    now = now or time.time()
    if (option is None) == (text is None):
        raise ValueError("give the answer once: --option N (the card's option) or --text \"<the person's words>\"")
    if text is not None and not str(text).strip():
        raise ValueError("--text is empty")
    ref = str(ask_ref or "").strip()
    if not ref:
        raise ValueError('usage: of asks answer <id> --option N | --text "<words>" --prompt <event id>')
    groups = of_asks.group(asks)
    g = next((x for x in groups if any(i.startswith(ref) or i.endswith(ref) for i in x["ask_ids"])), None)
    if not g:
        hit = next((a for a in asks if a["event_id"].startswith(ref) or a["event_id"].endswith(ref)), None)
        raise ValueError(f"ask {ref} is {hit['state']}: nothing to answer" if hit else f"no open ask matches {ref!r}")
    first = g["first"]
    kind = first.get("kind") or ""
    if kind in AT_MACHINE_KINDS or kind.startswith("blocked:"):
        raise ValueError(f"ask {first['event_id'][-8:]} is a {kind.split(':')[0]} in a pane: it is answered at the "
                         "machine, never from the chat")
    who_for = first.get("to")
    mine = conductors(positions, who_for)
    if not mine:
        raise ValueError(f"ask {first['event_id'][-8:]} waits on {who_for}, which no person's conductor answers "
                         f"(an agent answers it itself: of asks answered)")
    p = prompt_row(conn, prompt_ref)
    if p["name"] != "of.human_prompt":
        raise ValueError(f"--prompt {p['event_id']} is an {p['name']}, not a prompt the person typed")
    if p["source"] != "hook":
        raise ValueError(f"--prompt {p['event_id']} was not recorded by the hook (source {p['source']})")
    if p["origin"] != "human" or p["channel"] == "asks-ui":
        raise ValueError(f"--prompt {p['event_id']} was not typed by the person (origin {p['origin']}"
                         + (", the popup's answer typed into a pane" if p["channel"] == "asks-ui" else "") + ")")
    names = {c["name"] for c in mine}
    sessions = {(c.get("holder") or {}).get("session") for c in mine} - {None}
    if p["position"] not in names and p["session_id"] not in sessions:
        raise ValueError(f"--prompt {p['event_id']} was typed in {p['position'] or (p['session_id'] or '?')[:8]}, "
                         f"not in the session of the person's conductor ({', '.join(sorted(names))})")
    pts = _epoch(p["ts"])
    if pts > now + 60:
        raise ValueError(f"--prompt {p['event_id']} is dated in the future ({p['ts']})")
    if now - pts > PROMPT_MAX_AGE_H * 3600:
        raise ValueError(f"--prompt {p['event_id']} is older than {PROMPT_MAX_AGE_H} h ({p['ts']}): ask the person "
                         "again")
    by_id = {a["event_id"]: a for a in asks}
    members = [by_id[i] for i in g["ask_ids"] if i in by_id]
    ids = [a["event_id"] for a in members if _epoch(a["ts"]) < pts
           and not (a.get("reopened_at") and _epoch(a["reopened_at"]) >= pts)]
    if not ids:
        raise ValueError(f"--prompt {p['event_id']} was typed at {_hm(p['ts'])}, before the card "
                         f"({_hm(g['since'])}" + (f", reopened {_hm(first['reopened_at'])}" if first.get("reopened_at")
                                                  else "") + "): it cannot answer it")
    others = [a for a in _queue(asks, who_for) if a["event_id"] not in g["ask_ids"]]
    other_labels = set()
    for a in others:
        for o in _options_of(conn, a):
            other_labels.update({_norm(o), _label(o)})
    other_who = {a.get("position") for a in others}
    opts = _options_of(conn, first)
    named = names_card(p["text"], g["ask_ids"], g["who"], other_who)
    quoted = quoted_options(p["text"], opts, other_labels)
    if option is not None:
        if not opts:
            raise ValueError(f"ask {first['event_id'][-8:]} has no options: answer it with --text \"<the person's "
                             "words>\"")
        if not 1 <= option <= len(opts):
            raise ValueError(f"--option {option}: the card has options 1..{len(opts)}")
        wrong = [i for i in quoted if i != option - 1]
        if wrong:
            raise ValueError(f"the prompt quotes option {wrong[0] + 1} ({opts[wrong[0]]!r}), not {option}")
        if not named and option - 1 not in quoted:
            raise ValueError(f"the prompt neither names the card (its id {first['event_id'][-8:]}) nor quotes its "
                             f"option {option} ({opts[option - 1]!r})")
        answer, evidence = opts[option - 1], f"names {named}" if named else f"quotes option {option}"
    else:
        if _norm(text) not in _norm(p["text"]):
            raise ValueError("--text must be the person's words as typed in that prompt")
        if not named and len(quoted) != 1:
            raise ValueError(f"the prompt neither names the card (its id {first['event_id'][-8:]}) nor quotes one of "
                             "its options")
        answer, evidence = str(text).strip(), f"names {named}" if named else f"quotes option {quoted[0] + 1}"
    return {"ids": ids, "answer": answer, "option": option, "prompt": p, "conductor": sorted(names)[0],
            "evidence": "the prompt " + evidence,
            "position": first.get("position"), "session": first.get("session_id")}


def recorded(conn, prompt_id, ids):
    """True when this prompt already answered these asks through the conductor."""
    for (pl,) in conn.execute("SELECT payload FROM event WHERE name = 'of.agent_question_answered' "
                              "AND json_extract(payload, '$.via') = 'conductor' "
                              "AND json_extract(payload, '$.prompt') = ?", (prompt_id,)):
        try:
            if set(json.loads(pl or "{}").get("ask_ids") or []) == set(ids):
                return True
        except ValueError:
            continue
    return False


def record(c, relayed_by=None):
    import of_events
    import of_hook
    p = c["prompt"]
    return of_events.emit("agent_question_answered", {
        "ask_id": c["ids"][0], "ask_ids": c["ids"], "answer": of_hook.mask(c["answer"]), "via": "conductor",
        "by": "owner", "prompt": p["event_id"], "prompt_ts": p["ts"], "quote": of_hook.mask(p["text"])[:600],
        "conductor": c["conductor"], "evidence": c["evidence"], "relayed_by": relayed_by,
        **({"option": c["option"]} if c["option"] is not None else {})},
        position=c["position"], session=c["session"])


def answer(conn, asks, positions, ask_ref, prompt_ref, pane_of, option=None, text=None, relayed_by=None, now=None):
    """Check, record and deliver. Returns the status lines; raises ValueError when the prompt is no evidence."""
    import of_asks_answer
    c = check(conn, asks, positions, ask_ref, prompt_ref, option=option, text=text, now=now)
    if recorded(conn, c["prompt"]["event_id"], c["ids"]):
        return [f"recorded already: {len(c['ids'])} ask(s) answered from prompt {c['prompt']['event_id'][-8:]}"]
    eid = record(c, relayed_by)
    if not eid:
        raise ValueError("the answer could not be written to the event store")
    out = [f"answered: {', '.join(i[-8:] for i in c['ids'])} with {c['answer'][:80]!r} ({c['evidence']})"]
    p = {"answer": c["answer"], "via": "conductor", "prompt_ts": c["prompt"]["ts"], "quote": c["prompt"]["text"]}
    for position, session, ids in of_asks_answer.recipients(conn, c["ids"], c["position"], c["session"]):
        pane = pane_of(position)
        session = session or of_asks_answer.live_session(position, positions)
        status = of_asks_answer.deliver(pane, of_asks_answer.typed_answer(p, ids[0]), session) if pane else \
            f"no live pane: OF types it when {position} has one (for {of_asks_answer.REDELIVER_MAX_AGE_H} h)"
        out.append(f"  {position}: {status}")
    return out


def chat_view(conn, asks):
    """The owner's queue as the conductor tells it in the chat: plain text, English (the conductor speaks the person's
    language). Cards first, then what can be done from a phone or a browser, then what waits for the machine."""
    import of_asks
    mine = sorted(_queue(asks, "founder"), key=lambda a: a["ts"])
    cards, steps, waiting = [], [], []
    for g in of_asks.group(mine):
        a = g["first"]
        tag = f"[{a['event_id'][-8:]}] {', '.join(g['who'])}, waiting {of_asks.age(g['since'])}"
        how = in_chat(a)
        if how == "answer":
            opts = _options_of(conn, a)
            cards.append(f"  {tag}: {a['text']}" + ("".join(f"\n      {i}) {o}" for i, o in enumerate(opts, 1))))
        elif how == "steps":
            steps.append(f"  {tag} ({where_of(a)}): {a['text']}\n" +
                         "\n".join("      " + ln for ln in str(a.get("steps") or "").splitlines() if ln.strip()))
        else:
            what = "a task at the machine" if a.get("task") else f"a {(a.get('kind') or '').split(':')[0]} in a pane"
            waiting.append(f"  {tag} ({what}): {a['text'][:160]}")
    out = []
    if cards:
        out += ["Cards to answer here (the conductor records an answer: of asks answer <id> --option N | "
                "--text \"<the person's words>\" --prompt <event id>):"] + cards
    if steps:
        out += ["Tasks the person can do from a phone or a browser (when done, they say so; the asker checks):"] + steps
    if waiting:
        out += ["Waits until the person is at the machine (no steps in the chat):"] + waiting
    return "\n".join(out) or "nothing waits on the person"
