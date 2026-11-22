"""of asks: the owner's queue of open asks, projected from the event log (read-only).

Ask lifecycle (docs/events.md, backlog 8): an ask (`of.agent_question`, `of.permission_request`)
is open until a later event of the same session closes it. A heuristic question (the Stop hook's guess from the
closing message) starts as a `candidate`, shown to nobody: the judge (bin/of_judge.py, `of.agent_question_judged`)
opens it when the agent waits on its owner, or closes it as not_a_question (founder, ask ddd6e3a3, 2026-11-07).
- answered:   `of.agent_question_answered` (the popup, `of asks answered`, `of status --answered`), the popup's
              answer typed as `of.human_prompt`, or `of.turn_end` / a tool-driven event after a permission
              request (the tool ran, so it was approved). The owner's free text answers a heuristic question
              in its session, but marks declared asks seen and closes none (from 2026-11-07);
- denied:     `of.permission_denied` or a verified denial recorded by `of asks deny`;
- superseded: a newer ask in the same session before an answer; OF's escalated copy of a routed ask once the unit
              owner files its rewrite (`of ask --escalates <id>`, `escalated_from` on both);
- escalated:  `of.ask_escalated`: a routed ask sent on to the founder (by `of pulse` on its delegation's escalate_when,
              or by its unit owner, `of ask --escalates <id>`); it waits on his copy (`escalated_from`);
- answered_upstream: an escalated ask the owner answered: on a founder copy of it (OF's copy or the unit owner's
              rewrite), or a task copy he said done and its asker checked; settled_by `owner` (settle());
- settled_in_unit: an escalated ask its unit owner or its asker settled: closing a founder copy, a task copy done
              with no word from the owner, or, with no copy open, naming the ask itself (`of asks answered <id>`);
              settled_by names that position. Never the owner's decision. In both its escalation stays its closure;
- expired:    `of.session_end` while open;
- withdrawn:  `of.ask_withdrawn` written by the owner (`of asks withdraw <id>`).
- amended:    not a closure: `of.ask_amended` (`of ask --amend <id>`), by the position that filed the ask, while it is
              open with no answer, gives it a new version (text, options, yes, tags, due, blocking, steps); the ask
              keeps its id and filing time (docs/events.md, of.ask_amended).
A pane question the judge confirms is filed by OF as the agent's ask (`source: pane`, bin/of_judge.py): it lives like
a declared ask, and closes also by the owner's words in that session, the agent's own ask from it, or a later pane
card from it; never because the session went on.
Age never closes an ask; it only labels it `stale` (4 h) or `dead-end` (24 h), set in ~/.config/of/asks.yaml.
Agent messages in, attention notices and compactions do not close an ask.
"""

import json
import re
import time

ASK_KINDS = ("of.agent_question", "of.permission_request")
DEFAULTS = {"stale_hours": 4, "dead_end_hours": 24}  # labels only: an ask is never closed by age


def config():
    import os
    cfg = dict(DEFAULTS)
    import of_events
    path = of_events.config_path("asks.yaml")
    if os.path.exists(path):
        import yaml
        with open(path) as fh:
            cfg.update(yaml.safe_load(fh) or {})
    return cfg


def label(ts, cfg, now=None):
    secs = seconds_since(ts, now)
    if secs is None:
        return ""
    if secs >= cfg["dead_end_hours"] * 3600:
        return "dead-end"
    if secs >= cfg["stale_hours"] * 3600:
        return "stale"
    return ""


def seconds_since(ts, now=None):
    try:
        t = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except Exception:
        return None
    return (time.time() if now is None else now) - t


# events that say nothing about the session going on: annotations derived from other events, and the
# platform's own bookkeeping; they never close or move an ask
NEUTRAL = ("of.agent_message_in", "of.attention", "of.compaction", "of.holder_changed", "of.injected_prompt",
           "of.continuation", "of.context_injected", "of.pulse", "of.nudge_sent", "of.artifact_read",
           "of.paste_sourced", "of.question_labelled", "of.issue_labelled", "of.work_record", "of.escalation_due",
           "of.mandate_changed", "of.holder_bound", "of.holder_unbound", "of.stall_detected", "of.attention_routed",
           "of.capacity_snapshot", "of.state_declared",
           "of.turn_error")  # an API error (rate limit) answers nothing and ends no wait (of_field-analyst, 2026-11-06)


# Every kind project() acts on (of_studio loads by kind: READS keeps its page and this projection in step).
# A hook kind not listed here must be in NEUTRAL (tests.test_pulse.ReadsTest checks the hook and derive).
READS = ("of.agent_message_in", "of.agent_message_out", "of.agent_question", "of.agent_question_answered",
         "of.ask_escalated", "of.ask_withdrawn", "of.human_prompt", "of.mandate_check", "of.permission_denied",
         "of.permission_request", "of.permission_resolved", "of.session_end", "of.session_start",
         "of.state_declared", "of.turn_end", "of.turn_error", "of.prompt_origin", "of.diff_approved", "of.diff_applied",
         "of.agent_question_judged", "of.ask_amended", "of.ask_ref")
ANY_SOURCE = ("of.ask_withdrawn", "of.agent_question_answered", "of.agent_question", "of.state_declared",
              "of.ask_escalated", "of.prompt_origin", "of.diff_approved", "of.diff_applied",
              "of.agent_question_judged", "of.ask_amended", "of.ask_ref")  # written by tools (of ask, of status, the popup,
# reconcile), not only by hooks

# What `of ask --amend` may change (docs/events.md, of.ask_amended): the payload carries the new value under each name
AMENDABLE = ("text", "options", "yes_option", "tags", "due", "blocking", "task", "command", "where")


def relevant(name, source, payload=None):
    """Does project() take this event? Hook and imported events of READS, the tools' declarations, and a
    message the agent took itself (`of inbox --mine`: the relay's of.agent_message_in {delivery: pulled})."""
    if name not in READS:
        return False
    if name == "of.agent_question" and (payload or {}).get("subagent"):
        return False  # a subagent's question (e.g. Claude Code's prompt-suggestion side query) is not an ask;
        # its permission requests stay: they block on the owner like any other (of_studio, 2026-11-06)
    if source in ("hook", "import") or name in ANY_SOURCE:
        return True
    if name == "of.permission_resolved":
        p = payload or {}
        return source == "of" and p.get("via") == "asks-deny" and p.get("outcome") == "denied" and bool(p.get("reply_to"))
    return name == "of.agent_message_in" and (payload or {}).get("delivery") == "pulled"


def apply_prompt_origins(rows):
    """The rows with every `of.prompt_origin` correction applied to its `of.human_prompt` (the correction rows
    themselves left out). `of db prompt-origins` writes a correction when the transcript shows that a prompt the hook
    recorded as the owner's came from a scheduler or another automation; it may come long after the prompt, so it is
    applied up front. The one reader of corrected origins: the asks projection and of_studio's measures both call it
    (rows need event_id, name, payload; load them with of.prompt_origin among the kinds)."""
    fixed = {(r.get("payload") or {}).get("event_id"): (r.get("payload") or {}).get("origin")
             for r in rows if r.get("name") == "of.prompt_origin"}
    out = []
    for r in rows:
        if r.get("name") == "of.prompt_origin":
            continue
        if fixed and r.get("name") == "of.human_prompt" and r.get("event_id") in fixed:
            r = dict(r, payload=dict(r.get("payload") or {}, origin=fixed[r["event_id"]]))
        out.append(r)
    return out


def new_state():
    """Where project() keeps what it has seen, so a caller can feed it more rows later (the listener's asks view)."""
    return {"asks": [], "open_by_session": {}, "by_id": {}, "declared": {}, "by_position": {}, "declared_turn": {},
            "waits_founder": {}}


def twins(conn, plain):
    """The event ids of the asks whose card id is PLAIN (AS-9), stamped or given by of.ask_ref, in the order of their
    event ids: the first reads AS-9, the next AS-9b, AS-9c. The one set ref_id and card_name read; the projection's
    suffix_refs orders the asks it shows the same way (of_review on PR 300)."""
    ids = [e for (e,) in conn.execute(
        "SELECT event_id FROM event WHERE name IN ('of.agent_question', 'of.permission_request') AND "
        "json_extract(payload, '$.ref') = ? "
        "UNION SELECT json_extract(payload, '$.ask_id') FROM event WHERE name = 'of.ask_ref' AND "
        "json_extract(payload, '$.ref') = ?", (plain, plain))]
    return sorted(set(i for i in ids if i))


def card_name(conn, ask_id):
    """ASK_ID's card id as it is shown and resolved (its ref, or one an of.ask_ref gave it, with its twins' suffix),
    else its event id's last 8 hex digits."""
    r = conn.execute("SELECT json_extract(payload, '$.ref') FROM event WHERE event_id = ?", (ask_id,)).fetchone()
    if not (r and r[0]):
        r = conn.execute("SELECT json_extract(payload, '$.ref') FROM event WHERE name = 'of.ask_ref' AND "
                         "json_extract(payload, '$.ask_id') = ? LIMIT 1", (ask_id,)).fetchone()
    if not (r and r[0]):
        return str(ask_id or "")[-8:]
    plain, same = str(r[0]), twins(conn, str(r[0]))
    k = same.index(ask_id) if ask_id in same else 0
    return plain + (chr(ord("a") + k) if k else "")


def suffix_refs(asks):
    """Asks two machines numbered alike (each from its own counter within one sync): the first by event id keeps the
    plain id, the next ones read AS-412b, AS-412c, as ref_id resolves them (of_review on PR 300: both showed the plain
    id, and acting on the later card's id hit the earlier one). The plain id stays in ref_plain."""
    by = {}
    for a in asks:
        plain = a.get("ref_plain") or a.get("ref")
        if plain:
            a["ref_plain"] = plain
            by.setdefault(plain, []).append(a)
    for plain, same in by.items():
        for k, a in enumerate(sorted(same, key=lambda x: x["event_id"])):
            a["ref"] = plain if k == 0 else plain + chr(ord("a") + k)


def ref_id(conn, ref):
    """The event id of the ask whose card id is REF (AS-412, any case), or None. A suffixed one (AS-412b) is the
    later of two asks two machines numbered alike, by event id."""
    import of_events
    m = of_events.REF_RE.match(str(ref or "").strip())
    if not m:
        return None
    base = f"{m.group(1).upper()}-{int(m.group(2))}"
    ids = twins(conn, base)
    k = ord(m.group(3)) - ord("a") if m.group(3) else 0   # b: the second
    return ids[k] if k < len(ids) else None


def project(rows, state=None):
    """rows: dicts with event_id, ts, name, session_id, position, payload (dict), ordered by ts.
    Returns a list of asks with their state and closing event.
    state: a new_state() to carry on from: rows fed later continue where the earlier ones stopped, and the asks
    returned are the same as one projection of all of them, as long as no later row is older than an earlier one, no
    event comes again under its id, and no of.prompt_origin correction comes for a prompt already fed (the listener
    rebuilds then). One pass of the whole log took 16 s of CPU, and 179 s at load ~50 (2026-11-13)."""
    st = state if state is not None else new_state()
    asks, open_by_session, by_id, declared = st["asks"], st["open_by_session"], st["by_id"], st["declared"]
    by_position = st["by_position"]  # position -> its asks in order: a declared status looks back only
    # DECLARE_AFTER_S (of_platform, 2026-11-11: a scan of every ask per status took 17 of 18 s of one projection)
    declared_turn, waits_founder = st["declared_turn"], st["waits_founder"]
    for r in apply_prompt_origins(rows):
        sid, name = r["session_id"], r["name"]
        if name == "of.state_declared":
            pl_ = r["payload"] or {}
            if not (pl_.get("state") == "waiting" and pl_.get("waiting_on") in ("founder", "owner")):
                declared_turn[r.get("position")] = r["ts"]
                waits_founder.pop(r.get("position"), None)
            else:
                declared_turn.pop(r.get("position"), None)
                waits_founder.setdefault(r.get("position"), r["ts"])  # the start of its wait on the founder
            # a status declared just after the turn ended closes that turn's heuristic ask
            cur_ = open_by_session.get(sid) or _last_open(by_position.get(r.get("position")), r["ts"])
            if cur_ and cur_["kind"].startswith("question (heuristic)") and r.get("position") in declared_turn \
                    and _secs(cur_["ts"], r["ts"]) <= DECLARE_AFTER_S:
                cur_.update(state="not_a_question", closed_by=r["event_id"], closed_at=r["ts"])
                if open_by_session.get(cur_["session_id"]) is cur_:
                    open_by_session.pop(cur_["session_id"], None)
        if name == "of.agent_question_answered" and ((r["payload"] or {}).get("ask_ids") or (r["payload"] or {}).get("ask_id")):
            p_ = r["payload"] or {}
            # a reconcile closure ends the wait but is nobody's answer
            st = ("orphaned" if p_.get("reason") == "orphaned" else "moved_on") if p_.get("via") == "reconcile" \
                else ("not_a_question" if p_.get("verdict") == "not_a_question" else "answered")
            named = [by_id.get(x) for x in p_.get("ask_ids") or [p_.get("ask_id")]]
            # a founder's task is claimed only by the founder's own answer: an event that also names an ask to
            # another position is that position's closure, never his "done" (2026-11-07, task bd12d0)
            owners_own = all(x is None or x.get("to") in OWNER_ADDRESSES for x in named)
            for aid in p_.get("ask_ids") or [p_.get("ask_id")]:
                ask = by_id.get(aid)
                if ask and older_version(p_.get("version"), ask):
                    continue  # an answer to a version since amended, recorded anyway (a race): it closes nothing;
                    # the newest version stays open on the card (docs/events.md, of.ask_amended)
                if ask and ask["state"] == "escalated" and st == "answered" and not any(
                        c.get("escalated_from") == ask["event_id"] and c["state"] == "open" for c in asks):
                    # named itself, with no founder copy open (of asks answered by its unit owner or its asker);
                    # with a copy open the founder's card decides
                    settle(ask, p_.get("by"), r)
                    continue
                if ask and ask["state"] == "open" and ask.get("task"):
                    # a task is work for the owner, not a question: an answer is his claim that it is done; it
                    # stays open until the asker checks the result (of status --done-task) or withdraws it
                    if st == "answered" and p_.get("by") in ("owner", "founder") and owners_own:
                        ask.update(claimed_at=r["ts"], claim=(p_.get("answer") or "done")[:200])
                    continue
                if ask and ask["state"] in ("open", "candidate"):  # a candidate too: the popup's n, of asks answered
                    ask.update(state=st, closed_by=r["event_id"], closed_at=r["ts"])
                    if st == "answered":   # who closed it, and the person's prompt it cites: a listing tells an
                        # agent's closure of the owner's question from his answer (message 01a20320-debb)
                        ask.update(closed_by_who=p_.get("by"), closed_cites=p_.get("prompt"))
                    if st == "answered":
                        settle_copy(ask, by_id, r, p_.get("by"))
                    if open_by_session.get(ask["session_id"]) is ask:
                        open_by_session.pop(ask["session_id"], None)
            continue
        if name == "of.state_declared" and ((r["payload"] or {}).get("done_task") or (r["payload"] or {}).get("reopen_task")):
            # the asker closes its task after checking the result, or reopens a claim with what it still sees
            p_ = r["payload"] or {}
            tid = p_.get("done_task") or p_.get("reopen_task")
            for ask in declared.get(r.get("position"), []):
                if ask.get("task") and ask["state"] == "open" and (ask["event_id"] == tid or ask["event_id"].endswith(tid)
                                                                   or ask["event_id"].startswith(tid)):
                    if p_.get("done_task"):
                        ask.update(state="done", closed_by=r["event_id"], closed_at=r["ts"])
                        # the owner's work when he said done (a claim is only ever his), else its asker's word
                        settle_copy(ask, by_id, r, "owner" if ask.get("claimed_at") else r.get("position"))
                    else:
                        ask.update(claimed_at=None, claim=None, reopened=(p_.get("note") or "")[:300],
                                   reopened_at=r["ts"])  # an answer from a chat must be later (of_asks_chat)
        if name == "of.state_declared" and (r["payload"] or {}).get("state") in ("working", "done"):
            # carrying on with other work does not answer an ask (of ask itself suggests `of status working`):
            # declared asks stay open until answered, withdrawn or reconciled; a heuristic one has moved on
            cur = open_by_session.get(sid)
            if cur and not cur["kind"].startswith("question (declared"):
                cur.update(state="moved_on", closed_by=r["event_id"], closed_at=r["ts"])
                open_by_session.pop(sid, None)
            continue
        if name == "of.state_declared":
            continue
        if name in ("of.diff_approved", "of.diff_applied"):  # the diff's own approval closes its card
            did = (r["payload"] or {}).get("id")
            for ask in (a for lst in declared.values() for a in lst):
                if did and ask.get("diff_id") == did and ask["state"] == "open":
                    ask.update(state="answered", closed_by=r["event_id"], closed_at=r["ts"])
            continue
        if name == "of.ask_escalated":  # the routed ask now waits on the founder, as its escalated copy
            ask = by_id.get((r["payload"] or {}).get("ask_id"))
            if ask and ask["state"] == "open":
                ask.update(state="escalated", closed_by=r["event_id"], closed_at=r["ts"])
            continue
        if name == "of.ask_withdrawn":  # by the owner, for one ask by id
            ask = by_id.get((r["payload"] or {}).get("ask_id"))
            if ask and ask["state"] in ("open", "candidate"):
                ask.update(state="withdrawn", closed_by=r["event_id"], closed_at=r["ts"])
                if open_by_session.get(ask["session_id"]) is ask:
                    open_by_session.pop(ask["session_id"], None)
            continue
        if name == "of.ask_ref":  # a card id given to an ask filed before ids existed (of asks refs --backfill)
            ask = by_id.get((r["payload"] or {}).get("ask_id"))
            if ask is not None and not ask.get("ref"):
                ask["ref"] = (r["payload"] or {}).get("ref")
            continue
        if name == "of.ask_amended":  # the asker corrects its open ask in place: a new version, never a closure
            amend(by_id.get((r["payload"] or {}).get("ask_id")), r)
            continue
        if name == "of.agent_question_judged":  # the judge's verdict on a heuristic candidate (bin/of_judge.py)
            p_ = r["payload"] or {}
            ask = by_id.get(p_.get("ask_id"))
            if ask and p_.get("verdict") == "owner_question" and p_.get("filed_as"):
                # from 2026-11-11 the judge files the question as the agent's ask (source pane): the candidate is
                # replaced by it, whatever the session did meanwhile (hand-over cc2ed950)
                if ask["state"] not in ("answered", "withdrawn"):
                    ask.update(state="superseded", closed_by=p_["filed_as"], closed_at=r["ts"])
                if open_by_session.get(ask["session_id"]) is ask:
                    open_by_session.pop(ask["session_id"], None)
            elif ask and ask["state"] == "candidate":
                if p_.get("verdict") == "owner_question":
                    ask.update(state="open", judged=True, text=" ".join((p_.get("question") or ask["text"]).split()),
                               options=p_.get("options") or [], n_options=len(p_.get("options") or []))
                elif p_.get("verdict") in ("not_a_question", "skipped"):
                    ask.update(state="not_a_question", closed_by=r["event_id"], closed_at=r["ts"])
                    if open_by_session.get(ask["session_id"]) is ask:
                        open_by_session.pop(ask["session_id"], None)
            continue
        cur = open_by_session.get(sid)
        if name in ("of.permission_resolved", "of.permission_denied") and (r["payload"] or {}).get("reply_to"):
            # The hook can report the next prompt before asks-deny finishes checking the screen.
            ask = by_id.get(r["payload"]["reply_to"])
            if ask and ask["kind"] == "permission" and ask["session_id"] == sid:
                denied = name == "of.permission_denied" or r["payload"].get("outcome") == "denied"
                if denied or ask["state"] in ("open", "escalated"):
                    state = "denied" if denied else "answered"
                    if ask["state"] == "escalated" or (denied and ask.get("to") not in OWNER_ADDRESSES):
                        for copy in asks:
                            if copy.get("escalated_from") == ask["event_id"] and copy["state"] == "open":
                                copy.update(state=state, closed_by=r["event_id"], closed_at=r["ts"])
                    ask.update(state=state, closed_by=r["event_id"], closed_at=r["ts"])
                if cur is ask:
                    open_by_session.pop(sid, None)
                continue
            if cur is not ask or r["payload"].get("via") == "asks-deny":
                continue  # only a hook event naming the current question can still close that question below
        if name == "of.agent_question" and (r["payload"] or {}).get("detected_by") == "blocked":
            # a blocking screen (bin/of_blocked.py): one ask per dialog, closed by an answer or when it goes
            pl_ = r["payload"] or {}
            ask = {"event_id": r["event_id"], "ts": r["ts"], "session_id": sid, "position": r["position"],
                   "ref": (r.get("payload") or {}).get("ref"),
                   "kind": f"blocked:{pl_.get('blocked_type')}", "text": _text(r), "state": "open", "closed_by": None,
                   "closed_at": None, "to": bare_to(pl_.get("to"), r.get("org")), "tags": [], "pane": pl_.get("pane"),
                   "n_options": len(pl_.get("options") or []), "org": r.get("org")}
            asks.append(ask)
            by_position.setdefault(ask["position"], []).append(ask)
            by_id[ask["event_id"]] = ask
            continue
        if name == "of.agent_question" and (r["payload"] or {}).get("detected_by") == "declared":
            # declared with `of ask`: one position may hold several open asks; they never supersede
            key = (r["payload"] or {}).get("topic_key")
            for old in declared.get(r["position"], []):  # a re-ask on the same topic replaces the open one
                if key and old["state"] == "open" and old.get("topic_key") == key:
                    old.update(state="superseded", closed_by=r["event_id"], closed_at=r["ts"])
            src = (r["payload"] or {}).get("escalated_from")
            if src and (r["payload"] or {}).get("source") != "escalated":  # the unit owner's rewrite (of ask --escalates)
                for old in asks:  # replaces OF's copy (or an earlier rewrite) of the same routed ask, whoever filed it
                    if old.get("escalated_from") == src and old["state"] == "open":
                        old.update(state="superseded", closed_by=r["event_id"], closed_at=r["ts"])
            for old in declared.get(r["position"], []):  # a pane card OF filed is replaced by the agent's own ask
                # from that session, or by a later pane card from it (of_judge.file_pane_ask)
                if old.get("source") == "pane" and old["state"] == "open" and old.get("session_id") == sid:
                    old.update(state="superseded", closed_by=r["event_id"], closed_at=r["ts"])
            ask = {"event_id": r["event_id"], "ts": r["ts"], "session_id": sid, "position": r["position"],
                   "ref": (r.get("payload") or {}).get("ref"),
                   "kind": _kind(r), "text": _text(r), "state": "open", "closed_by": None, "closed_at": None,
                   "topic_key": key, "to": bare_to((r["payload"] or {}).get("to"), r.get("org")),
                   "tags": (r["payload"] or {}).get("tags") or [], "due": (r["payload"] or {}).get("due"),
                   "tags_declared": bool((r["payload"] or {}).get("tags_declared")),
                   "diff_id": (r["payload"] or {}).get("diff_id"),
                   "source": (r["payload"] or {}).get("source"), "escalated_from": src,
                   "n_options": _n_options(r), "host_id": r.get("host_id"),  # the machine that filed it: the one
                   # whose amends apply (of_server on PR 181)
                   "org": r.get("org")}  # its organization: groups never cross one (the owner's panel holds several)
            if (r["payload"] or {}).get("task"):
                pl_ = r["payload"] or {}
                ask.update(task=True, kind="task", steps=(pl_.get("steps") or "")[:4000], command=pl_.get("command"),
                           claimed_at=None, claim=None,
                           # where the person can do it (docs/remote.md §3 row 4): none or a command = at the machine
                           where="machine" if pl_.get("command") or pl_.get("where") not in ("phone", "browser")
                           else pl_["where"])
            asks.append(ask)
            by_position.setdefault(ask["position"], []).append(ask)
            by_id[ask["event_id"]] = ask
            declared.setdefault(r["position"], []).append(ask)
            continue
        if name in ASK_KINDS:
            if cur:
                cur.update(state="superseded", closed_by=r["event_id"], closed_at=r["ts"])
            # a heuristic question (the Stop hook's guess) is a candidate, shown to nobody until the judge confirms it
            heuristic = (r["payload"] or {}).get("detected_by") == "heuristic"
            ask = {"event_id": r["event_id"], "ts": r["ts"], "session_id": sid, "position": r["position"],
                   "ref": (r.get("payload") or {}).get("ref"),
                   "kind": _kind(r), "text": _text(r), "state": "candidate" if heuristic else "open", "closed_by": None,
                   "closed_at": None, "n_options": 2 if name == "of.permission_request" else _n_options(r),
                   "org": r.get("org")}
            if name == "of.permission_request":
                p = r["payload"] or {}
                ask["to"] = bare_to(p.get("to"), r.get("org"))
                ask.update({k: p[k] for k in ("blocking", "due", "tags", "tags_declared") if k in p})
            asks.append(ask)
            by_position.setdefault(ask["position"], []).append(ask)
            by_id[ask["event_id"]] = ask
            open_by_session[sid] = ask
            continue
        if name == "of.human_prompt" and (r["payload"] or {}).get("origin") not in (None, "human", "paste"):
            continue  # a tool's or a peer's text typed into the pane answers nothing the owner was asked
        if name == "of.human_prompt" and TOOL_FRAME.match(((r["payload"] or {}).get("text") or "").lstrip()):
            continue  # a routed question or a relayed message typed by a tool, even if recorded as human
        if name == "of.human_prompt":
            # the owner wrote into the asker's session: its declared asks from that session are answered there;
            # the popup's typed answer names its ask ("Answer from the founder to your question <id>: …")
            # and answers that one only (the answer event itself closes it)
            m = ANSWER_FRAME.match(((r["payload"] or {}).get("text") or "").lstrip())
            open_here = [a for a in declared.get(r.get("position"), [])
                         if a["state"] == "open" and a.get("session_id") in (sid, None) and not a.get("task")]
            if m:  # the popup's answer names its ask (and the version it answered, on an amended one)
                targets = [a for a in open_here if a["event_id"].endswith(m.group(1))
                           and not older_version(int(m.group(2)) if m.group(2) else None, a)]
            elif r["ts"] >= FREE_TEXT_SEEN_SINCE:  # free text marks the session's open asks seen and closes none:
                # the asker judges whether the words answered one (`of status working --answered <id>`); else it stays
                # in the owner's list (of_architect, 2026-11-07: 04c0bc48 was closed by a prompt about another agent)
                for a in open_here:
                    a.update(seen_at=r["ts"], seen_by=r["event_id"])
                # a question asked in this pane, which OF filed (source pane), is answered by his words in this pane
                targets = [a for a in open_here if a.get("source") == "pane" and a.get("session_id") == sid]
            else:  # before FREE_TEXT_SEEN_SINCE: free text answered the session's ask when it was the only one open,
                # never a diff approval (2026-11-07: typing about the agent mailbox closed a spawn diff's card)
                plain = [a for a in open_here if not a.get("diff_id")]
                targets = plain if len(plain) == 1 else []
            for ask in targets:
                ask.update(state="answered", closed_by=r["event_id"], closed_at=r["ts"])
                settle_copy(ask, by_id, r, "owner")  # the person's own words (origin human or paste)
            if m and cur and not cur["event_id"].endswith(m.group(1)):
                continue  # a popup answer names another ask: the session's open question stays open
        if name == "of.agent_message_in":
            # an ask to a position (a unit owner who is an agent) is answered by that position's message into
            # the asker's session: the one ask its reply_to names (ask id or session), else the only open ask
            # to that sender. A message from anyone else answers nothing (of_field-analyst, 2026-11-06).
            pl_ = r["payload"] or {}
            sender = str(pl_.get("from") or "").rpartition(":")[2]
            rt = str(pl_.get("reply_to") or "")
            mine = [a for a in declared.get(r.get("position"), []) if a["state"] == "open" and not a.get("task")
                    and a.get("to") not in OWNER_ADDRESSES and a.get("session_id") in (sid, None)]
            to_sender = [a for a in mine if sender and str(a.get("to") or "").rpartition(":")[2] == sender]
            named = [a for a in to_sender if rt and (rt in (a["event_id"], a.get("session_id")) or len(rt) >= 8 and (
                a["event_id"].endswith(rt) or a["event_id"].startswith(rt)))]
            for ask in named or (to_sender if len(to_sender) == 1 else []):
                ask.update(state="answered", closed_by=r["event_id"], closed_at=r["ts"])
        if not cur or name in NEUTRAL:
            continue
        if name == "of.permission_denied":
            state = "denied"
        elif name == "of.session_end":
            state = "expired"
        elif name in ("of.human_prompt", "of.agent_question_answered"):
            # the session's open question (heuristic or permission) is answered; the position's declared asks
            # were settled above by their own rules. Closing them all here let a popup answer to one ask close
            # every other ask of the asker that had a heuristic question open (2026-11-06: 754659b8's answer
            # closed f7ea7dc8 and d7bab4e4).
            state = "answered"
        elif cur["kind"] == "permission" and name in ("of.turn_end", "of.agent_message_out",
                                                       "of.permission_resolved", "of.mandate_check"):
            state = "answered"  # the tool ran after the request, so it was approved
        elif cur["kind"].startswith("question (heuristic)") and name not in ("of.agent_question", "of.permission_request"):
            if waits_founder.get(cur.get("position")):
                continue  # the agent still declares that it waits on the founder: its question stays (2026-11-07)
            state = "moved_on"  # the session went on (a new turn, a tool call): the wait ended, nobody answered
        else:
            continue
        cur.update(state=state, closed_by=r["event_id"], closed_at=r["ts"])
        open_by_session.pop(sid, None)
    suffix_refs(asks)
    return asks


def amend(ask, r):
    """One of.ask_amended applied to its projected ask: its newest version, marked amended with what changed. Only by
    the position that filed it, from the machine that filed it (its host_id: the coordination feed brings every
    machine's events, and `by` is the sender's word; of_server on PR 181), only while it is open with no answer (a
    task with no claim), and only a newer version; its id and filing time (ts) stay, so its age, order and reminders
    do too."""
    p = r["payload"] or {}
    if not ask or ask["state"] != "open" or ask.get("claimed_at") or not ask.get("position") \
            or (p.get("by") or r.get("position")) != ask["position"]:
        return
    if not ask.get("host_id") or r.get("host_id") != ask["host_id"]:
        return
    v = p.get("version")
    if not isinstance(v, int) or isinstance(v, bool) or v <= (ask.get("version") or 1):
        return
    ch = [c for c in p.get("changed") or [] if c in AMENDABLE]
    if "text" in ch:
        ask["text"] = " ".join(str(p.get("text") or "").split()) or ask["text"]
        if "topic_key" in p:
            ask["topic_key"] = p["topic_key"]
    if "options" in ch:
        ask["n_options"] = len(p.get("options") or [])
    if "tags" in ch:
        ask.update(tags=list(p.get("tags") or []), tags_declared=True)
    if "due" in ch:
        ask["due"] = p.get("due")
    if "blocking" in ch and not ask.get("task"):
        ask["kind"] = "question (declared)" + (" blocking" if p.get("blocking") else "")
    if "task" in ch and ask.get("task"):
        ask["steps"] = str(p.get("task") or "")[:4000]
    if ask.get("task") and {"command", "where"} & set(ch):  # its command to paste and where it is done, as filed
        cmd = p.get("command") if "command" in ch else ask.get("command")
        where = p.get("where") if "where" in ch else ask.get("where")
        ask.update(command=cmd, where="machine" if cmd or where not in ("phone", "browser") else where)
    before = (ask.get("amended") or {}).get("all") or []
    ask["version"] = v
    ask["amended"] = {"version": v, "at": r["ts"], "changed": ch, "all": [c for c in AMENDABLE if c in ch or c in before]}


OWNER_BY = ("owner", "founder")  # an answer's `by` when the person gave it (the popup, a chat, his terminal)


def settle(ask, by, r, via=None):
    """Settle an escalated routed ask by event `r`, given by `by` ("owner" or a position), through founder copy `via`
    (None: an answer naming the ask itself). The owner's answer makes it answered_upstream; its unit owner's (its
    `to`) or its asker's makes it settled_in_unit, so a closure by an agent never reads as the owner's decision. Anyone
    else settles nothing: the ask stays in its unit owner's queue (of_server on PR 222). It names who, the event, when
    and the copy (settled_by, settled_event, settled_at, settled_via), so its unit owner's queue (of asks --to-me)
    keeps only real work (a user organization's structure keeper, via of_platform, message 01a1deaf-47e0: 22 routed
    asks listed as escalated, the founder had answered a copy of 18). It keeps its escalation as its closure
    (closed_by, closed_at): the time it left the unit owner's queue, which the unit owner's measures read, so the
    owner's answer never counts as the unit owner's."""
    if not ask or ask["state"] != "escalated" or not may_settle(ask, by):
        return
    ask.update(state="answered_upstream" if by in OWNER_BY else "settled_in_unit", settled_by=by,
               settled_event=r["event_id"], settled_at=r["ts"], **({"settled_via": via} if via else {}))


def settle_copy(copy, by_id, r, by):
    """A founder copy of a routed ask (`escalated_from`: OF's copy or the unit owner's rewrite) was answered, or done
    as a task, by `by`: its routed ask, while escalated, is settled (settle()). A withdrawn copy settles nothing: a unit
    owner withdraws its rewrite to file another."""
    src = by_id.get(copy.get("escalated_from")) if copy.get("escalated_from") else None
    if src is not copy:
        settle(src, by, r, via=copy["event_id"])


def may_settle(ask, by):
    """Whether `by` may settle the escalated routed ask `ask` (settle()): the owner, its unit owner or its asker."""
    return bool(by) and by in OWNER_BY + (ask.get("to"), ask.get("position"))


def closed_by_agent(ask):
    """An answered question to the owner that a named agent closed, not his own answer. Only a closure that names its
    position counts: the owner's own paths record `owner`, `founder`, nothing or `of`, and the rows folded before this
    field have none, so none of those reads as an agent's; an agent whose position did not resolve (`agent`) is an
    agent's closure all the same (of_review on PR 514)."""
    who = ask.get("closed_by_who")
    return ask.get("state") == "answered" and isinstance(who, str) and who not in ("owner", "founder", "of") \
        and ask.get("to") in OWNER_ADDRESSES


def state_label(ask):
    """An ask's state as a listing shows it: a settled escalated ask names who settled it, so a unit's closure is never
    retold as the owner's decision; an agent's closure of the owner's question says so, with the prompt it cites."""
    if closed_by_agent(ask):
        return (f"closed by {ask['closed_by_who']}, citing prompt {str(ask['closed_cites'])[:13]}"
                if ask.get("closed_cites") else f"closed by {ask['closed_by_who']}, no answer of the owner cited")
    return ask["state"] + (f" by {ask['settled_by']}" if ask["state"] in ("answered_upstream", "settled_in_unit")
                           and ask.get("settled_by") else "")


def older_version(answered, ask):
    """True when an answer names a version older than the ask's newest (an int it carries; none names no version)."""
    return isinstance(answered, int) and not isinstance(answered, bool) and answered < (ask.get("version") or 1)


def closed_after_view(conn, ask_id, version=None):
    """(what, ts) when an answer, a withdrawal or an escalation of ASK_ID came after the asks view's fold mark, else
    None. A view that is behind (current_asks returns it late) still shows such an ask open, so a decision that acts
    on an open ask reads the log since that mark for this ask alone (of_conductor, message 01a1ea94-7796: card AS-31,
    answered at version 6 at 17:12:05Z, was amended at 17:18:51Z because the listener's view had stood since
    17:11:02Z). An answer to a version older than VERSION closes nothing, as in project(). None too where no view is:
    the caller projected the log."""
    try:
        meta = json.loads((conn.execute("SELECT value FROM listener_meta WHERE key = ?", (FOLD_KEY,)).fetchone()
                           or ["{}"])[0])
    except Exception:  # noqa: BLE001 - no listener tables on this machine
        return None
    mark = int(meta.get("rowid") or 0)
    if not mark:
        return None
    what = {"of.agent_question_answered": "answered", "of.ask_withdrawn": "withdrawn", "of.ask_escalated": "escalated"}
    for name, ts, pl in conn.execute("SELECT name, ts, payload FROM event WHERE rowid > ? AND name IN "
                                     "('of.agent_question_answered', 'of.ask_withdrawn', 'of.ask_escalated') "
                                     "ORDER BY rowid", (mark,)):
        try:
            p = json.loads(pl or "{}")
        except ValueError:
            continue
        if ask_id not in (p.get("ask_ids") or [p.get("ask_id")]):
            continue
        if name == "of.agent_question_answered" and older_version(p.get("version"), {"version": version}):
            continue
        return what[name], ts
    return None


def amendments(conn, ask_id, ts, position, host, rowids=False):
    """[(ts, payload)] of the of.ask_amended events for one ask, by the position that filed it and from the machine
    that filed it (HOST, the ask's own host_id), in the projection's order (ts, then rowid); ROWIDS: [(ts, rowid,
    payload)]. Reads one name from the ask's filing time (the name index)."""
    out = []
    if not (position and host):
        return out
    for ets, rid, pl, h in conn.execute(
            "SELECT ts, rowid, payload, host_id FROM event WHERE name = 'of.ask_amended' AND ts >= ? "
            "AND json_extract(payload, '$.ask_id') = ? ORDER BY ts, rowid", (ts or "", ask_id)):
        try:
            p = json.loads(pl or "{}")
        except ValueError:
            continue
        if p.get("by") == position and h == host:
            out.append((ets, rid, p) if rowids else (ets, p))
    return out


def ask_host(conn, ask_id):
    """The host_id of the machine that filed the ask (its own event's), or None."""
    row = conn.execute("SELECT host_id FROM event WHERE event_id = ?", (ask_id,)).fetchone()
    return row[0] if row else None


def at_version(conn, ask_id, payload, ts, position, host, version=None):
    """The ask's payload (its of.agent_question's) as of `version`, the newest when None: its question, options,
    yes_option, tags, due, blocking, and a task's steps, command and where after each amend up to it, with `version` (1 when never amended) and
    `amended` ({version, at, changed, all}). Only the amends of its own position and machine (HOST: the ask's host_id)
    count (docs/events.md, of.ask_amended)."""
    out = dict(payload or {})
    qs = out.get("questions") if isinstance(out.get("questions"), list) else []
    q = dict(qs[0]) if qs and isinstance(qs[0], dict) else {}
    out.update(version=1, amended=None)
    last = 1
    for ets, p in amendments(conn, ask_id, ts, position, host) if conn is not None and ask_id else ():
        v = p.get("version")
        if not isinstance(v, int) or isinstance(v, bool) or v <= last or (version is not None and v > version):
            continue
        ch = [c for c in p.get("changed") or [] if c in AMENDABLE]
        if "text" in ch:
            q["question"] = p.get("text") or q.get("question")
        if "options" in ch:
            q["options"] = list(p.get("options") or [])
        for k in ("yes_option", "tags", "due", "blocking"):
            if k in ch:
                out[k] = p.get(k)
        if "task" in ch and out.get("task"):
            out["steps"] = p.get("task")
        for k in ("command", "where"):
            if k in ch and out.get("task"):
                out[k] = p.get(k)
        before = (out.get("amended") or {}).get("all") or []
        out.update(version=v, amended={"version": v, "at": ets, "changed": ch,
                                       "all": [c for c in AMENDABLE if c in ch or c in before]})
        last = v
    if q:
        out["questions"] = [q] + list(qs[1:])
    return out


def stale(shown, current):
    """True when an answer given to version `shown` (1 when the card carried none) must be refused: the ask has a
    later version. Any change counts, its text included: a sentence can change what a yes means (docs/events.md,
    of.ask_amended; of_review on PR 181: a text amend turned staging into production)."""
    return (current or 1) > (shown if isinstance(shown, int) and not isinstance(shown, bool) else 1)


# Texts OF's own tools type into a pane (bin/of _deliver_ask, the relay); before 2026-11-06 a routed question was
# recorded with origin human, and as free text it closed the receiver's only open ask (f7ea7dc8).
TOOL_FRAME = re.compile(r"(?:<pasted_content[^>]*>\s*)?(?:Question from \S+ \(unit |Message from \S+ \(via )")
FREE_TEXT_SEEN_SINCE = "2026-11-07T00:00:00"  # from here the owner's free text closes no declared ask (history before keeps
# its closures: those asks are days old and their askers moved on)
ANSWER_FRAME = re.compile(r"(?:<pasted_content[^>]*>\s*)?Answer from the founder to your question ([0-9a-f]{8})\b"
                          r"(?: \(version (\d+))?")
DECLARE_AFTER_S = 120  # a status declared this long after the turn's end still speaks for that turn


def _last_open(asks, ts):
    """The position's last open or candidate ask, when it is at most DECLARE_AFTER_S older than ts; else None. The
    asks are in time order, so the scan stops at the first one older than that: an open one further back is older
    still and could not pass the check."""
    for a in reversed(asks or ()):
        if a["state"] in ("open", "candidate"):
            return a if _secs(a["ts"], ts) <= DECLARE_AFTER_S else None
        if _secs(a["ts"], ts) > DECLARE_AFTER_S:
            return None
    return None


def _secs(a, b):
    import calendar
    import time as _t
    try:
        ta = calendar.timegm(_t.strptime(a[:19], "%Y-%m-%dT%H:%M:%S"))
        tb = calendar.timegm(_t.strptime(b[:19], "%Y-%m-%dT%H:%M:%S"))
        return tb - ta
    except (TypeError, ValueError):
        return 10 ** 9


def _kind(r):
    p = r["payload"] or {}
    if r["name"] == "of.permission_request":
        return "permission"
    if p.get("detected_by") == "declared":
        return "question (declared)" + (" blocking" if p.get("blocking") else "")
    return "question (heuristic)" if p.get("detected_by") == "heuristic" else "question"


def _n_options(r):
    """How many options the asker offered (0: a free question, e.g. an agent ending its turn with one). A heuristic
    question written before its options were stored: the lettered options ((a)/(b), Latin or Cyrillic) in its stored
    closing text."""
    qs = (r["payload"] or {}).get("questions") or []
    q0 = qs[0] if qs and isinstance(qs[0], dict) else {}
    if "options" not in q0 and (r["payload"] or {}).get("detected_by") == "heuristic":
        import of_classify
        return len(of_classify.options_of(q0.get("question") or ""))
    return len(q0.get("options") or [])


def _text(r):
    p = r["payload"] or {}
    if r["name"] == "of.permission_request":
        return f"{p.get('tool_name')}: {p.get('summary') or ''}"
    qs = p.get("questions") or []
    q = (qs[0].get("question") if qs and isinstance(qs[0], dict) else "") or ""
    if p.get("detected_by") == "heuristic":  # the stored text is the tail of a message: its last paragraph is the ask
        paras = [x for x in re.split(r"\n\s*\n", q) if x.strip()]
        q = paras[-1] if paras else q
    return " ".join(q.split())


FOLD_KEY = "asks_fold"  # listener_meta: what the asks view reflects and the projection's own indexes (fold_meta)
TAIL = 500             # the most events current_asks folds on the view; past that the view as it is, late


def fold_meta(state, rowid, fed):
    """What a reader needs, beside the view's asks, to carry the listener's projection on exactly: the last event
    the view reflects (rowid, and the latest ts fed) and the indexes that are not in the asks themselves."""
    return {"rowid": rowid, "fed": fed, "at": time.time(),
            "open_by_session": {s: a["event_id"] for s, a in state["open_by_session"].items()},
            "declared_turn": state["declared_turn"], "waits_founder": state["waits_founder"]}


def state_from(asks, meta):
    """A projection state (new_state's shape) rebuilt from the view's asks, in their order, and fold_meta: project()
    carries it on as the listener would."""
    st = new_state()
    st["asks"] = asks
    for a in asks:
        st["by_id"][a["event_id"]] = a
        st["by_position"].setdefault(a.get("position"), []).append(a)
        if "topic_key" in a:  # filed with of ask: project()'s declared path is the one that sets the key, even to
            # None (tests.test_listen checks that every declared ask carries it)
            st["declared"].setdefault(a.get("position"), []).append(a)
    st["open_by_session"].update({s: st["by_id"][e] for s, e in (meta.get("open_by_session") or {}).items()
                                  if e in st["by_id"]})
    st["declared_turn"].update(meta.get("declared_turn") or {})
    st["waits_founder"].update(meta.get("waits_founder") or {})
    return st


def reader():
    """A read-only connection to the event store: reads in WAL mode never wait on a writer and never write
    (of_platform's log-readers design, 3.2). None when the store cannot be opened read-only: never a writing
    connection in its place (of_platform's note on PR 133)."""
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
        return None


def current_asks(conn=None, tail=TAIL, now=None):
    """The asks view's read (organic-framework-build reports/of-log-readers-2026-11-14.md, 3.2): every ask as the
    listener's view holds it, carried on with the log's events after it, at most `tail` of them, by the view's own fold
    (project() on the state the view and its fold_meta give back). Never a projection of the whole log: past `tail`,
    an event older than what the view folded (imported history) or a correction of a prompt's origin, it returns the
    view as it is, late. Returns {"asks": [...], "cursor": the last event reflected, "age_s": the view's age,
    "late": bool}. conn: a reader() when None."""
    own = conn is None
    conn = conn or reader()
    now = now or time.time()
    if conn is None:  # no read-only store: the empty answer, late
        return {"asks": [], "cursor": 0, "age_s": None, "late": True}
    # the fold mark, the asks and the tail from one snapshot: the listener commits the view and its mark together, and
    # a mark read one write behind the asks folded the tail twice, an ask coming back twice (of_review on PR 133)
    began = not conn.in_transaction
    if began:
        conn.execute("BEGIN")
    try:
        try:
            meta = json.loads((conn.execute("SELECT value FROM listener_meta WHERE key = ?", (FOLD_KEY,)).fetchone()
                               or ["{}"])[0])
            asks = [json.loads(a) for (a,) in conn.execute(
                "SELECT ask FROM view_asks WHERE ask IS NOT NULL ORDER BY ts, event_id")]
        except Exception:  # noqa: BLE001 - no listener here yet, or one before the view kept whole asks
            return {"asks": [], "cursor": 0, "age_s": None, "late": True}
        if not meta.get("rowid") and not asks:
            return {"asks": [], "cursor": 0, "age_s": None, "late": True}
        cursor = int(meta.get("rowid") or 0)
        rows, fetched = [], 0
        for rid, e, ts, n, sid, pos, pl, org, src, host in conn.execute(
                "SELECT rowid, event_id, ts, name, session_id, position, payload, org, source, host_id FROM event "
                f"WHERE rowid > ? AND name IN ({','.join('?' * len(READS))}) ORDER BY rowid LIMIT ?",
                (cursor,) + READS + (tail + 1,)):
            fetched += 1
            try:
                payload = json.loads(pl) if pl else {}
            except ValueError:
                payload = {}
            if relevant(n, src, payload):
                rows.append({"event_id": e, "ts": ts, "name": n, "session_id": sid, "position": pos,
                             "payload": payload, "org": org, "rowid": rid, "host_id": host})
        # its age: 0 with nothing unapplied, else from the oldest event it has not applied (of_architect's point 1 on
        # the design, by of_platform's note on PR 133): a quiet view is current
        age = (seconds_since(rows[0]["ts"], now) or 0.0) if rows else 0.0
        if fetched > tail or any(r["name"] == "of.prompt_origin" or (r["ts"] or "") < (meta.get("fed") or "")
                                   for r in rows):
            return {"asks": asks, "cursor": cursor, "age_s": age, "late": True}
        if rows:
            st = state_from(asks, meta)
            project(sorted(rows, key=lambda r: r["ts"]), st)
            asks, cursor = st["asks"], rows[-1]["rowid"]
        return {"asks": asks, "cursor": cursor, "age_s": age, "late": False}  # its tail folded: current
    finally:
        if began and conn.in_transaction:
            conn.execute("COMMIT")
        if own:
            conn.close()


def load(conn, since_iso=None):
    sql = (f"SELECT event_id, ts, name, session_id, position, payload, org, source, host_id FROM event "
           f"WHERE name IN ({','.join('?' * len(READS))}) " + ("AND ts >= ? " if since_iso else "") + "ORDER BY ts, rowid")
    out = []
    for e, ts, n, s, pos, pl, org, src, host in conn.execute(sql, READS + ((since_iso,) if since_iso else ())):
        try:
            payload = json.loads(pl) if pl else {}
        except Exception:
            payload = {}
        if not relevant(n, src, payload):
            continue
        out.append({"event_id": e, "ts": ts, "name": n, "session_id": s, "position": pos, "payload": payload,
                    "org": org, "host_id": host})
    return out


def age(ts, now=None):
    secs = seconds_since(ts, now)
    if secs is None:
        return "?"
    s = int(secs)
    return f"{s // 60}m" if s < 5400 else (f"{s // 3600}h" if s < 172800 else f"{s // 86400}d")



def _tokens(text):
    return {w for w in re.findall(r"\w+", (text or "").lower()) if len(w) > 2}


def group(asks, threshold=0.6):
    """One decision asked by several agents = one item: open asks merged by similar text
    (Jaccard similarity of their words), oldest first; each item lists who is waiting."""
    import re as _re  # noqa: F401
    items = []
    for a in sorted((x for x in asks if x["state"] == "open"), key=lambda x: x["ts"]):
        t = _tokens(a["text"])
        for it in items:
            u = it["tokens"]
            # never across recipients, nor a task with a question: one answer must not close another person's item
            # (2026-11-07: a routed question to platform_owner and a founder's task grouped; its answer claimed the task);
            # never across organizations: the owner's panel holds several (docs/contours.md §3), and one answer to two
            # organizations' asks would ship under the first one's to its server alone (of_review on PR 359)
            first = it["asks"][0]
            same = first.get("to") == a.get("to") and bool(first.get("task")) == bool(a.get("task")) \
                and first.get("org") == a.get("org")
            if same and t and u and len(t & u) / len(t | u) >= threshold:
                it["asks"].append(a)
                it["tokens"] = u | t
                break
        else:
            items.append({"tokens": t, "asks": [a]})
    out = []
    for it in items:
        first = it["asks"][0]
        out.append({"text": first["text"], "since": first["ts"], "kind": first["kind"],
                    "who": sorted({a["position"] or (a["session_id"] or "?")[:8] for a in it["asks"]}),
                    "ask_ids": [a["event_id"] for a in it["asks"]], "first": first})
    return out


OWNER_ADDRESSES = ("founder", "owner", None)


def bare_to(to, org):
    """An ask's receiver as a bare name: `of ask --to <org>:<position>` was stored verbatim before 2026-11-15, and every
    reader matches the bare name (delivery, of asks --to-me, --escalates); the asker's own organization's prefix is
    dropped. None or empty is the founder."""
    if not to:
        return "founder"
    t = str(to)
    pre, sep, name = t.partition(":")
    return name if sep and org and pre == org else t


def for_owner(asks):
    """The asks that wait on the human owner: not those a delegated unit routes to its own owner
    position (an agent or a colleague), which wait on that position instead."""
    return [a for a in asks if a.get("to") in OWNER_ADDRESSES]
