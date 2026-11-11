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
A card tagged money, security_incident, prod_change or irreversible is refused (HELD_TAGS, docs/remote.md §5): the
event store is a file the agents' user can write, so until prompts are recorded out of their reach such a card is
answered at the machine; so is a diff's approval card (of diff apply would take its answer as the approval). The
answer is the person's (`by: owner`, so a task is claimed, never closed), `via: conductor`, with the prompt's id
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
# docs/remote.md §5 (of_architect, 2026-11-11, 4557113): the event store is a file the agents' operating-system user
# can write, so a process could add a forged prompt. Until prompts are recorded by a component the agents cannot write
# to (of-server under its own user), a card with one of these tags is answered at the machine, and every diff too.
HELD_TAGS = ("money", "security_incident", "prod_change", "irreversible")
STORE_LIMIT = ("until the person's prompts are recorded by a component the agents cannot write to (docs/remote.md "
               "§5: a process running as the agents' user could add a forged prompt)")
DIFF_HOLD = (f"diff approvals from the chat are on hold {STORE_LIMIT}: approve it at the machine, with "
             "`of diff approve <id>` in a terminal")


def held_tags(ask, options=()):
    """The names of the ask's tags that keep an answer from the chat away until the store is safe: the stored ones
    ({tag, source, systems} from of_classify.parse_tags or tags_of, or a plain "name[:systems]") and, whatever was
    declared, the ones of_classify.tags_of finds in its words (of_release's review 2026-11-11, finding 2: a pane card
    stores no tags and `--tags none` stores nothing, so a money or production question passed)."""
    import of_classify
    names = [str(t.get("tag") or "") if isinstance(t, dict) else str(t).split(":", 1)[0] for t in ask.get("tags") or []]
    words = " ".join([str(ask.get("text") or ""), str(ask.get("steps") or ""), *[str(o) for o in options]])
    names += [t["tag"] for t in of_classify.tags_of(words)]
    return [n for n in dict.fromkeys(names) if n in HELD_TAGS]


def where_of(ask):
    return ask.get("where") if ask.get("where") in WHERES else "machine"


def in_chat(ask):
    """How the conductor presents an open ask: `answer` (a card the person answers in the chat), `steps` (a task they
    can do where they are), `waiting` (it waits until they are at the machine)."""
    kind = ask.get("kind") or ""
    if kind in AT_MACHINE_KINDS or kind.startswith("blocked:") or ask.get("diff_id") or held_tags(ask):
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


TOP = ("founder", "owner")  # the organization's top owner, as a card's `to` and a position's `owner` name him


def conductors(positions, to, asker=None):
    """The conductors that serve the person a card waits on (of_architect's review of 3962757: a colleague's
    conductor must not answer a card addressed to the founder). A card routed to a position a person holds: the
    conductors that position owns. A card to the top owner (`to` founder, owner or none): a conductor owned by the
    top owner by address (founder or owner; the founder's serves him across his organizations, docs/conductor.md
    option A), or one owned by the head of the asker's organization, a person-held position that owns itself (the
    head `of init` writes). Never by name alone. A card routed to an agent has none: that agent answers it."""
    target = None if to in TOP or to is None else str(to).rpartition(":")[2]
    by_name = {p.get("name"): p for p in positions}
    if target:
        t = by_name.get(target)
        if not t or not _held_by_person(t):
            return []
        serves = {target}
    else:
        org = (by_name.get(asker) or {}).get("_org")
        heads = {p["name"] for p in positions if org and p.get("_org") == org and _held_by_person(p)
                 and p.get("owner") == p.get("name")}
        serves = set(TOP) | heads
    return [p for p in positions if p.get("role") == "conductor" and p.get("status") not in GONE
            and p.get("owner") in serves]


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
    by_id = {a["event_id"]: a for a in asks}
    diff = next((by_id[i].get("diff_id") for i in g["ask_ids"] if (by_id.get(i) or {}).get("diff_id")), None)
    if diff:  # of_release's review 2026-11-11, finding 1: of diff apply would take the answer as the approval
        raise ValueError(f"ask {first['event_id'][-8:]} is the approval card of diff {diff}: {DIFF_HOLD}")
    first_opts = _options_of(conn, first)
    held = sorted({t for i in g["ask_ids"] for t in held_tags(by_id.get(i) or {}, first_opts if i == first["event_id"]
                                                               else ())})
    if held:
        raise ValueError(f"ask {first['event_id'][-8:]} is tagged {', '.join(held)}: it is answered at the machine "
                         f"(the popup), not from the chat, {STORE_LIMIT}")
    who_for = first.get("to")
    mine = conductors(positions, who_for, first.get("position"))
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
    via = p["position"] if p["position"] in names else \
        next(c["name"] for c in mine if (c.get("holder") or {}).get("session") == p["session_id"])
    return {"ids": ids, "answer": answer, "option": option, "prompt": p, "conductor": via,
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
    by_id = {a["event_id"]: a for a in mine}
    for g in of_asks.group(mine):
        a = g["first"]
        members = [by_id[i] for i in g["ask_ids"] if i in by_id]
        tag = f"[{a['event_id'][-8:]}] {', '.join(g['who'])}, waiting {of_asks.age(g['since'])}"
        # the whole group, as check() reads it: one held ask keeps the card at the machine (finding 9)
        how = "waiting" if any(in_chat(m) == "waiting" for m in members) else in_chat(a)
        if how == "answer":
            opts = _options_of(conn, a)
            cards.append(f"  {tag}: {a['text']}" + ("".join(f"\n      {i}) {o}" for i, o in enumerate(opts, 1))))
        elif how == "steps":
            steps.append(f"  {tag} ({where_of(a)}): {a['text']}\n" +
                         "\n".join("      " + ln for ln in str(a.get("steps") or "").splitlines() if ln.strip()))
        else:
            held = list(dict.fromkeys(t for m in members for t in held_tags(m)))
            diff = next((m.get("diff_id") for m in members if m.get("diff_id")), None)
            what = "a task at the machine" if a.get("task") else (
                f"diff {diff} to approve at the machine" if diff else
                f"a card tagged {', '.join(held)}, answered at the machine" if held
                else f"a {(a.get('kind') or '').split(':')[0]} in a pane")
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
