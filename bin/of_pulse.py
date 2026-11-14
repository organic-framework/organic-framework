"""of pulse: nobody waits unseen (founder, 2026-11-05: the organization stalled and nobody saw it).

`project()` is the one model of "who needs attention", used by `of who`, `of pulse` and Studio:
one row per position, the most urgent reason first:
  waiting_owner  an open ask to the founder (declared with `of ask`, or from hooks), herdr blocked,
                 a declared `of status waiting --on founder`, or the registry's waiting_on: owner;
  waiting_peer   a declared `of status waiting --on <position>`;
  stalled        an active mandate, idle for at least STALL_MIN minutes, with no declared reason
                 (waiting or done) since it went idle.
`run()` is the 5-minute job: it projects, writes `of.pulse`, and tells the founder (one summary of
new stalls; a reminder for asks older than 30 minutes, at most hourly), and nudges an idle peer
that someone waits on (at most every 30 minutes per pair), in the relay's message frame.
"""

import json
import os
import re
import subprocess
import time

import of_events
from of_locale import tr

STALL_MIN = 15
TASK_REMIND_MIN = 120  # an open task for the owner: one reminder every 2 h (of_architect, 2026-11-06)
ASK_REMIND_MIN = 30
REMIND_EVERY_MIN = 60
NUDGE_EVERY_MIN = 120  # hand-over reminders: at least 2 hours apart (peer nudges back off by NUDGE_BACKOFF_S)
NOTIFY_EVERY_S = 60


def _ts(iso):
    try:
        return time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except Exception:
        return None


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def latest_status(rows):
    """{position: payload + ts} of the latest `of.state_declared` per position."""
    out = {}
    for r in rows:
        if r["name"] == "of.state_declared" and r.get("position"):
            out[r["position"]] = dict(r["payload"] or {}, ts=r["ts"])
    return out


def project(positions, agents, rows, asks, idle_since, now=None, bind=None, handovers=None):
    """positions: registry entries; agents: herdr live agents; rows: events (name, ts, position,
    session_id, payload); asks: of_asks.project(...) output; idle_since: {pane: epoch seconds}.
    Returns [{position, kind, on, since, ask_ids, text, evidence}] sorted by urgency, then age."""
    now = now or time.time()
    status = latest_status(rows)
    live = {}
    if bind:
        for p, a in bind(positions, agents)[0]:
            live[p["name"]] = a
    import of_blocked
    open_asks, routed = {}, {}
    for a in asks:
        if a["state"] == "open" and a.get("position"):
            if str(a.get("kind") or "").partition("blocked:")[2] in of_blocked.FYI:
                continue  # a limit screen goes to of_capacity's inbox only; its balancer acts (of_capacity, 2026-11-11)
            if a.get("to") in ("founder", "owner", None):
                open_asks.setdefault(a["position"], []).append(a)
            else:  # routed to a delegated unit's owner position: a wait on that peer
                routed.setdefault(a["position"], a)
    handover_waits = {}
    if handovers:
        import of_handover
        for req, to, h in of_handover.waits(handovers):
            handover_waits.setdefault(req, (h["id"], to, h["proposed_at"], h["title"],
                                            h.get("blocked_on") if h["state"] == "blocked" else None))
    out = []
    for p in positions:
        name, a, st = p["name"], live.get(p["name"]), status.get(p["name"]) or {}
        ev, kind, on, since, text, ids, routed_ask = [], None, None, None, None, [], None
        mine = open_asks.get(name, [])
        if mine:
            ev.append(f"{len(mine)} open ask(s)")
            kind, on, since, text, ids = "waiting_owner", "founder", mine[0]["ts"], mine[0]["text"], [x["event_id"] for x in mine]
        if a and a.get("agent_status") == "blocked":
            ev.append("herdr: blocked")
            kind, on = "waiting_owner", "founder"
        if st.get("state") == "waiting" and st.get("waiting_on") in ("founder", "owner"):
            ev.append(f"declared waiting on founder: {st.get('note') or ''}".strip())
            kind, on, since, text = "waiting_owner", "founder", since or st["ts"], text or st.get("note")
        if p.get("waiting_on") == "owner":
            ev.append("registry: waiting_on owner")
            kind, on = kind or "waiting_owner", on or "founder"
        if kind is None and name in routed:
            r_ = routed[name]
            kind, on, since, text = "waiting_peer", r_["to"], r_["ts"], r_["text"]
            ev.append(f"ask routed to the unit owner {r_['to']}")
            routed_ask = r_.get("event_id")
        if kind is None and name in handover_waits:
            hid, on, since, title, blocked_on = handover_waits[name]
            kind, text = "waiting_peer", f"hand-over {hid[-8:]}: {title}"
            ev.append(f"blocking hand-over {hid[-8:]} to {on}")
            if blocked_on:  # the receiver blocked it on someone: the wait is theirs, never a nudge to the receiver
                b = str(blocked_on).rpartition(":")[2]
                kind, on = ("waiting_owner", "founder") if b in ("founder", "owner") else ("waiting_peer", b)
                ev.append(f"blocked on {b}")
        if kind is None and st.get("state") == "waiting" and st.get("waiting_on"):
            kind, on, since, text = "waiting_peer", st["waiting_on"], st["ts"], st.get("note")
            ev.append(f"declared waiting on {on}")
        if kind is None and a and p.get("status", "active") == "active" and a.get("agent_status") in ("idle", "done"):
            idle_t = idle_since.get(a["pane_id"])
            declared_after = st and _ts(st["ts"]) and idle_t and _ts(st["ts"]) >= idle_t - 60 \
                and st.get("state") in ("waiting", "done", "idle")
            if idle_t and now - idle_t >= STALL_MIN * 60 and not declared_after:
                kind, since = "stalled", _iso(idle_t)
                ev.append(f"idle {int((now - idle_t) // 60)} min, active mandate, no declared reason")
        if kind:
            out.append({"position": name, "kind": kind, "on": on, "since": since, "ask_ids": ids, "text": text,
                        "evidence": ev, "pane": (a or {}).get("pane_id"), "routed_ask": routed_ask})
    rank = {"waiting_owner": 0, "waiting_peer": 1, "stalled": 2}
    out.sort(key=lambda r: (rank[r["kind"]], r["since"] or ""))
    return out


# ------------------------------------------------------------------ memory

def _mem_path():
    return os.path.join(of_events.STATE_DIR, "pulse.json")


def load_memory():
    try:
        with open(_mem_path()) as fh:
            return json.load(fh)
    except Exception:
        return {"panes": {}, "notified_stalls": [], "last_remind": 0, "nudged": {}, "last_notify": 0, "blocked": {}}


# The pulse's projection, kept for `of who` (2026-11-13, of_conductor msg 01a1d663-4530: of who projected the whole
# event log for its attention column, 14 s of CPU and 941 MB a call, while ~70 agents run on the founder's laptop).
ATTENTION_MAX_AGE_S = 900  # three pulses: an older copy means the listener is not running, and of who projects


def _attention_path():
    return os.path.join(of_events.STATE_DIR, "attention.json")


def save_attention(rows, now):
    tmp = f"{_attention_path()}.{os.getpid()}.tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump({"ts": now, "rows": rows}, fh, default=str)
    os.replace(tmp, _attention_path())


def load_attention(now=None, max_age=ATTENTION_MAX_AGE_S):
    """(ts, rows) of the last pulse's projection when it is at most max_age old, else None."""
    try:
        with open(_attention_path()) as fh:
            d = json.load(fh)
        ts, rows = float(d["ts"]), d["rows"]
    except Exception:  # noqa: BLE001
        return None
    age = (now or time.time()) - ts
    return (ts, rows) if 0 <= age <= max_age and isinstance(rows, list) else None


def save_memory(m):
    tmp = f"{_mem_path()}.{os.getpid()}.tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(m, fh)
    os.replace(tmp, _mem_path())


def track_idle(mem, agents, now):
    """Remember since when each pane has been idle (herdr gives the state, not its age)."""
    panes = {}
    for a in agents:
        st, old = a.get("agent_status"), mem["panes"].get(a["pane_id"])
        panes[a["pane_id"]] = old if old and old[0] == st else [st, now]
    mem["panes"] = panes
    return {p: v[1] for p, v in panes.items() if v[0] in ("idle", "done")}


# ----------------------------------------------------------- notifications

def herdr_sound(kind, path=None):
    """The sound OF asks herdr to play for a notification: `kind` (request, done), or none when herdr's own sound
    alerts are off (`[ui.sound] enabled = false` in its config.toml; founder, 2026-11-08)."""
    try:
        import tomllib
        with open(path or os.path.expanduser("~/.config/herdr/config.toml"), "rb") as fh:
            cfg = tomllib.load(fh)
        if ((cfg.get("ui") or {}).get("sound") or {}).get("enabled") is False:
            return "none"
    except (OSError, ValueError, ImportError):
        pass
    return kind


def notify(title, body, sound="request"):
    try:
        subprocess.run(["herdr", "notification", "show", title[:120], "--body", body[:400], "--sound", herdr_sound(sound)],
                       capture_output=True, timeout=5, env=dict(os.environ, OF_AUTOMATION="1"))
    except Exception:
        pass


def queue_notice(text):
    """`of ask` queues; flush_notices sends at most one batch a minute."""
    path = os.path.join(of_events.STATE_DIR, "notify-queue.jsonl")
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a") as fh:
        fh.write(json.dumps({"ts": time.time(), "text": text}, ensure_ascii=False) + "\n")


def flush_notices(mem, now, force=False, send=notify):
    path = os.path.join(of_events.STATE_DIR, "notify-queue.jsonl")
    if not os.path.exists(path) or (not force and now - mem.get("last_notify", 0) < NOTIFY_EVERY_S):
        return 0
    work = f"{path}.{os.getpid()}"
    os.replace(path, work)
    with open(work) as fh:
        items = [json.loads(line)["text"] for line in fh if line.strip()]
    os.remove(work)
    if items:
        send(f"{len(items)} new question(s) for you" if len(items) > 1 else "A question for you",
             " · ".join(items[:4]) + (" …" if len(items) > 4 else "") + "  (of asks)")
        mem["last_notify"] = now
    return len(items)


def nudge_text(waiter, note, peer):
    """The relay's frame (RFC 0001 §5.9): one line, then the text, never as the owner's words."""
    return (f"Message from {waiter} (via of pulse, authority: none; automatic nudge)\n"
            f"{waiter} is waiting on you ({peer}){': ' + note if note else ''}.\n"
            f"[reply: of send {waiter} \"…\", or tell it you can't: of status ... ]")


def run(positions, agents, rows, asks, bind, now=None, send=notify, prompt=None, log=print, handovers=None,
        offer=None, elsewhere=None, observed=None):
    """One pulse. Returns the projection. handovers: of_handover.project(...); offer(h) types a
    hand-over into its receiver's idle pane and records the offer. elsewhere: {(org, position): holder} held on
    another machine of the organization now (of_presence.elsewhere); this pulse records nothing about them and types
    nothing for them: their own machine sees the same state through the coordination feed and acts
    (docs/server-several-machines.md §4.3). Pane actions are local by construction: they need a local pane."""
    import of_asks
    observed = observed or time.time()  # wall clock when `rows` were read: a nudge re-checks what came after
    now = now or time.time()
    mem = load_memory()
    idle = track_idle(mem, agents, now)
    rows_ = project(positions, agents, rows, asks, idle, now, bind, handovers)
    try:
        save_attention(rows_, now)
    except Exception as e:  # noqa: BLE001
        log(f"attention copy: {e}")
    try:
        handover_windows(positions, agents, bind, handovers or {}, now, mem, prompt, offer, log, elsewhere=elsewhere,
                         send=send)
    except Exception as e:
        log(f"hand-overs: {e}")
    # herdr `blocked` (a dialog waiting on the owner) becomes an event once per episode, so the
    # display projection (of_studio.attention_all) sees it without reading herdr itself.
    blocked_now = {}
    for p, a in bind(positions, agents)[0]:
        if a.get("agent_status") == "blocked":
            blocked_now[a["pane_id"]] = p["name"]
    for pane, name in blocked_now.items():
        if pane not in mem.get("blocked", {}):
            of_events.emit("attention", {"notification_type": "herdr_blocked", "position": name, "pane": pane},
                           position=name)
    mem["blocked"] = blocked_now
    try:  # the model at t: mandate and holder changes accrue every pulse
        import of_lifecycle
        import of_store
        of_lifecycle.record(of_store.connect(), positions, agents, bind, elsewhere=elsewhere)
    except Exception as e:
        log(f"lifecycle: {e}")
    try:
        guard_check(mem, now, log)
    except Exception as e:
        log(f"guard check: {e}")
    try:  # delegated units: routed asks go up to the founder only on escalate_when
        import of_store
        n = run_escalations(positions, asks, now, of_store.connect(), log, elsewhere=elsewhere)
        n += studio_escalations(positions, of_store.connect(), log)
        if n:
            asks = current_asks()
    except Exception as e:
        log(f"escalations: {e}")
    try:  # asks whose sessions no longer wait close here (claude agents --json, herdr blocked)
        import of_asks_live
        import of_store
        if of_asks_live.run(of_store.connect(), positions, agents, bind, log):
            asks = current_asks()
    except Exception as e:
        log(f"asks reconcile: {e}")
    try:  # forced every run, not only on a change: the display can never drift for long
        import of_herdr_asks
        herdr_only = [a for a in agents if not str(a.get("pane_id") or "").startswith(("tmux:", "screen:"))]
        mem["ws_counts"] = of_herdr_asks.refresh_ask_tokens(positions, herdr_only, asks, bind)  # herdr's tokens only
    except Exception as e:
        log(f"ask tokens: {e}")
    of_events.emit("pulse", {"observed_at": _iso(now), "positions": [
        {k: r[k] for k in ("position", "kind", "on", "since", "ask_ids", "evidence")} for r in rows_]})
    flush_notices(mem, now, force=True, send=send)
    stalled = sorted(r["position"] for r in rows_ if r["kind"] == "stalled")
    new = [s for s in stalled if s not in mem.get("notified_stalls", [])]
    for r in rows_:
        if r["kind"] == "stalled" and r["position"] in new:
            of_events.emit("stall_detected", {"position": r["position"], "idle_since": r["since"],
                                              "reason": "; ".join(r["evidence"]), "evidence": r["evidence"]},
                           position=r["position"])
    if new:
        send(f"{len(stalled)} position(s) stalled", ", ".join(stalled) + "  (idle with an active mandate; of who)")
    mem["notified_stalls"] = stalled
    try:
        inbound_stalls(mem, now, send)
    except Exception as e:  # noqa: BLE001
        log(f"inbound queues: {e}")
    tasks = [a for a in of_asks.for_owner(asks) if a.get("task") and a["state"] == "open" and not a.get("claimed_at")]
    if tasks and now - mem.get("last_task_remind", 0) >= TASK_REMIND_MIN * 60:  # work for his hands: every 2 h, no more
        send(tr("{n} task(s) wait for your hands", n=len(tasks)),
             " · ".join(f"{a.get('position')}: {a['text'][:60]}" for a in tasks[:4])
             + "  (prefix+a → " + tr("To do for you") + ")")
        mem["last_task_remind"] = now
    old = [g for g in of_asks.group(of_asks.for_owner(asks)) if (_ts(g["since"]) or now) <= now - ASK_REMIND_MIN * 60
           and not str(g.get("kind") or "").startswith("question (heuristic)")]  # asked in a pane: no notifications
    if old and now - mem.get("last_remind", 0) >= REMIND_EVERY_MIN * 60:
        send(f"{len(old)} question(s) waiting on you over {ASK_REMIND_MIN} min",
             " · ".join(f"{', '.join(g['who'])}: {g['text'][:60]}" for g in old[:4]) + "  (of asks)")
        mem["last_remind"] = now
    live = {}
    for p, a in bind(positions, agents)[0]:
        live[p["name"]] = a
    status = latest_status(rows)
    for r in rows_:
        if r["kind"] != "waiting_peer":
            continue
        peer = live.get(r["on"])
        key = f"{r['position']}->{r['on']}"
        since = max(wait_run_start(rows, r["position"], r["on"]) or r["since"] or "",
                    _iso(now - REPLY_QUIET_H * 3600))
        if replied(rows, r["position"], r["on"], since):
            continue  # the peer answered (of send or SendMessage) within this wait: the waiter carries on
        last = nudge_state(mem["nudged"].get(key), since)
        text = nudge_text(r["position"], r["text"], r["on"])
        if peer and peer.get("agent_status") in ("idle", "done") and may_nudge(last, now, text, rows, r["on"], status) \
                and still_waits(r, observed):
            if prompt:
                prompt(peer["pane_id"], text, r["position"])
            of_events.emit("nudge_sent", {"position": r["position"], "from_position": r["position"],
                                          "to_position": r["on"], "about": r["text"], "nth": last["n"] + 1,
                                          "pane": peer["pane_id"]}, position=r["position"])
            mem["nudged"][key] = {"at": now, "n": last["n"] + 1, "sha": _sha(text), "since": since}
            log(f"nudged {r['on']} for {r['position']} (#{last['n'] + 1})")
    try:  # registry drift: parked in the registry, working in fact; to the structure keeper, never the founder
        for name, org, keeper, evidence in parked_but_working(positions, agents, bind, rows, now, mem):
            pane = (live.get(keeper) or {}).get("pane_id")
            if not prompt or not pane or live[keeper].get("agent_status") not in ("idle", "done"):
                continue  # the keeper is busy or away: the next pulse tries again
            prompt(pane, parked_note(name, evidence), "of_pulse")
            of_events.emit("nudge_sent", {"position": name, "from_position": "of_pulse", "to_position": keeper,
                                          "about": f"registry says parked, but {name} is working: {evidence}",
                                          "pane": pane, "kind": "parked_but_working"}, position=keeper, org=org)
            mem["parked_told"][name] = time.strftime("%Y-%m-%d", time.gmtime(now))
            log(f"told {keeper}: {name} is parked but working")
    except Exception as e:  # noqa: BLE001
        log(f"parked check: {e}")
    try:  # a pane whose input box held text through STUCK_AFTER deliveries: to its structure keeper, never typed into it
        stuck_panes(positions, agents, bind, live, prompt, send, log)
    except Exception as e:  # noqa: BLE001
        log(f"stuck input check: {e}")
    try:  # docs/remote.md §3 row 3: a person on the remote surface hears about their queue from their conductor
        import of_locale
        if of_locale.owner_setting("surface") == "remote":
            remote_notes(positions, live, asks, rows_, mem, prompt, now, log)
    except Exception as e:  # noqa: BLE001
        log(f"remote notes: {e}")
    save_memory(mem)
    return rows_


INBOUND_STALL_MIN = 30
PARKED_WORKING_S = 3600  # working within the last hour while the registry says parked
WORKING_RUN_S = 600      # herdr's working counts as work only for a run this long (a reply takes less)


def structure_keeper(positions, org):
    """The organization's structure keeper: its active position of role `org` (acme_org), else `architect`
    (of_architect)."""
    mine = [p for p in positions if p.get("_org") == org and p.get("status") != "parked"]
    for role in ("org", "architect"):
        hit = next((p["name"] for p in mine if p.get("role") == role), None)
        if hit:
            return hit
    return None


def parked_but_working(positions, agents, bind, rows, now, mem):
    """[(position, org, keeper, evidence)]: the registry says parked while the live holder declared working or herdr
    showed it working in the last hour (docs/registry.md, status: waiting is not parked; of_architect, 2026-11-06).
    Each position at most once per UTC day; only marked told when the note reached the keeper (see run)."""
    seen = mem.setdefault("working_seen", {})
    run = mem.setdefault("working_run", {})  # name -> [first, last] sighting of the current or last working run
    for p, a in bind(positions, agents)[0]:
        n = p["name"]
        if a.get("agent_status") == "working":
            seen[n] = now
            r = run.get(n)
            run[n] = [r[0], now] if r and now - r[1] <= 2 * 300 + 60 else [now, now]  # pulses 5 min apart
    st = latest_status(rows)
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    out = []
    for p in positions:
        if p.get("status") != "parked" or mem.setdefault("parked_told", {}).get(p["name"]) == day:
            continue
        ev = []
        s = st.get(p["name"])
        r = run.get(p["name"]) or [0, 0]
        # a short turn (a reply to a relayed message) is not work on a paused mandate (acme_org, 2026-11-07): herdr's
        # working counts only as a run of WORKING_RUN_S or more, and not when the turn ended in a declared
        # idle, waiting or done
        ended_quiet = bool(s and s.get("state") in ("idle", "waiting", "done") and (_ts(s.get("ts")) or 0) >= r[1] - 120)
        if now - r[1] <= PARKED_WORKING_S and r[1] - r[0] >= WORKING_RUN_S and not ended_quiet \
                and not turn_from_peer(rows, p["name"], r[1]):
            ev.append(f"herdr showed it working {time.strftime('%H:%M', time.gmtime(r[0]))}-"
                      f"{time.strftime('%H:%MZ', time.gmtime(r[1]))}")
        if s and s.get("state") == "working" and now - (_ts(s.get("ts")) or 0) <= PARKED_WORKING_S \
                and not turn_from_peer(rows, p["name"], _ts(s.get("ts")) or 0):
            ev.append(f"it declared working at {str(s.get('ts'))[11:16]}Z")
        keeper = structure_keeper(positions, p.get("_org"))
        if ev and keeper and keeper != p["name"]:
            out.append((p["name"], p.get("_org"), keeper, "; ".join(ev)))
    return out


def turn_from_peer(rows, name, at):
    """Did the turn running at `at` (epoch) start from a message another position or a tool typed in? A parked holder
    answering a peer is not work on its paused mandate (acme_org, 2026-11-09: storefront_checkout, parked by its unit owner,
    declared working at 08:45Z to answer the questions its owner and storefront_operator sent at 08:25Z and 08:40Z).
    The founder's prompt, the holder's own scheduler and a message it took itself start real work."""
    starts = [r for r in rows if r.get("position") == name and (_ts(r["ts"]) or 0) <= at + 1 and (
        r["name"] == "of.human_prompt" or (r["name"] == "of.agent_message_in"
                                           and (r.get("payload") or {}).get("delivery") != "pulled"))]
    if not starts:
        return False
    last = max(starts, key=lambda r: r["ts"])  # a message it took itself (pulled) starts no turn
    by_peer = last["name"] == "of.agent_message_in" or (last.get("payload") or {}).get("origin") in ("peer", "injected")
    return by_peer and at - (_ts(last["ts"]) or 0) <= PARKED_WORKING_S


PASTE_HINT = ("The box holds only a paste placeholder ('[Pasted text #N +M lines]'): most likely a delivery whose "
              "Enter was lost (Acme, 2026-11-11: acme_code-auditor sat three hours behind one, lost while Claude "
              "Code compacted the session). Look at the pane; if no person is typing there, press Enter in it to send "
              "the held text (herdr pane send-keys <pane> Enter).")


def remote_notes(positions, live, asks, rows, mem, prompt, now, log=print, views=None):
    """docs/remote.md §3 row 3 (with `surface: remote` in owner.yaml): the person reaches OF only through their
    conductor's chat, so no herdr notification or popup reaches them. Each pulse tells the conductor, in one message
    typed into its pane when it is idle, what is new for the person: a card or a task that opened, a thread of theirs
    that is stuck (the conductor's ledger), a position that waits on them. Each item once; an item the conductor could
    not be told yet waits for the next pulse. views: [(request, kind, threads)] of the ledger (tests)."""
    import of_asks
    by_name = {p["name"]: p for p in positions}
    conductors = [p for p in positions if (p.get("role") == "conductor" or p["name"].endswith("_conductor"))
                  and p.get("status", "active") == "active"]
    if not conductors:
        return 0
    # The person's own conductor, for what is theirs across organizations (their stuck requests): the one owner.yaml
    # names (`conductor:`), else the only conductor live on this machine. Never another organization's conductor by
    # default (after-the-fact review of 2026-11-11, finding 5: the founder's queue would leak across organizations).
    import of_locale
    named = of_locale.owner_setting("conductor")
    local = [c for c in conductors if (live.get(c["name"]) or {}).get("pane_id")]
    mine = next((c for c in conductors if c["name"] == named), None) if named else \
        (local[0] if len(local) == 1 else None)  # a name that is no active conductor is never typed into

    def conductor_for(org):
        """The conductor of an organization's own item: that organization's; none when it has none."""
        return next((c for c in conductors if c.get("_org") == org), None)

    seen = mem.setdefault("remote_seen", [])
    items = {}
    for a in of_asks.for_owner(asks):
        if a.get("state") != "open":
            continue
        key = f"ask:{a['event_id']}"
        if key not in seen:
            org = (by_name.get(a.get("position")) or {}).get("_org")
            what = "a task for their hands" if a.get("task") else "a card"
            c = conductor_for(org)
            if not c:
                continue  # an organization without a conductor: its owner sees the popup, never another's chat
            items.setdefault(c["name"], []).append(
                (key, f"{what} from {a.get('position')}: {str(a.get('text') or '')[:160]} (ask {a['event_id'][-8:]})"))
    for r in rows:
        if r.get("kind") != "waiting_owner":
            continue
        key = f"wait:{r['position']}:{r.get('since')}"
        if key not in seen:
            org = (by_name.get(r["position"]) or {}).get("_org")
            c = conductor_for(org)
            if not c:
                continue
            items.setdefault(c["name"], []).append(
                (key, f"{r['position']} waits on them: {str(r.get('text') or '; '.join(r.get('evidence') or []))[:160]}"))
    if views is None:
        import of_conductor_panel
        import of_store
        ledger = of_conductor_panel.load_ledger()
        views = [] if ledger.get("error") else of_conductor_panel.views_of(ledger, of_store.connect(), now)
    for req, kind, _ in views:
        if kind != "stuck":
            continue
        key = f"stuck:{req.get('id')}"
        if key not in seen:
            if not mine:
                log("remote notes: no conductor of the person's own (owner.yaml conductor:) for a stuck request")
                continue
            items.setdefault(mine["name"], []).append(
                (key, f"their request is stuck: {req.get('title') or req.get('title_ru') or req.get('id')}"))
    told = 0
    for name, its in items.items():
        pane = live.get(name) or {}
        if not prompt or not pane.get("pane_id") or pane.get("agent_status") not in ("idle", "done"):
            continue  # busy or away: the next pulse tries again
        text = ("Message from of_pulse (via of pulse, authority: none; the person's queue, remote surface)\n"
                "New for the person you serve; tell them in their language and plain words within this turn:\n"
                + "\n".join(f"- {line}" for _, line in its) + "\n[to you as their conductor, once per item]")
        res = prompt(pane["pane_id"], text, "of_pulse")
        ok = res[0] if isinstance(res, tuple) else (res is None or res is True)
        if ok is not True:  # deferred or failed: not told, so the next pulse tries again (review 2026-11-11, finding 4)
            log(f"remote notes: {name} not told ({res[1] if isinstance(res, tuple) and len(res) > 1 else res})")
            continue
        of_events.emit("nudge_sent", {"position": name, "from_position": "of_pulse", "to_position": name,
                                      "about": f"{len(its)} item(s) of the person's queue", "pane": pane["pane_id"],
                                      "kind": "remote_queue"}, position=name, org=by_name.get(name, {}).get("_org"))
        seen.extend(k for k, _ in its)
        told += 1
        log(f"told {name}: {len(its)} item(s) for the person (remote surface)")
    del seen[:-2000]
    return told


def stuck_panes(positions, agents, bind, live, prompt, send, log=print):
    """Report once each pane where the relay deferred STUCK_AFTER deliveries in a row because its input box held text
    (of_architect, 2026-11-07: of_lab's blocked prompt collected ~20 appended deliveries). The note goes to the
    position's structure keeper, typed into the KEEPER's pane; never into the stuck pane. Without a keeper pane,
    a notification to the owner."""
    import of_relay_client
    doc = of_relay_client.read_stuck()
    by_pane = {a["pane_id"]: p for p, a in bind(positions, agents)[0]}
    changed = False
    for pane, e in doc.items():
        if e.get("reported") or e.get("count", 0) < of_relay_client.STUCK_AFTER:
            continue
        p = by_pane.get(pane) or {}
        name = p.get("name") or pane
        keeper = structure_keeper(positions, p.get("_org")) if p else None
        note = (f"Message from of_pulse (via of pulse, authority: none; stuck input box)\n"
                f"{name}'s input box (pane {pane}) has held text ({e.get('chars')} chars) through {e['count']} deliveries "
                f"since {time.strftime('%H:%MZ', time.gmtime(e.get('since') or 0))}; nothing more is typed into it. "
                + (PASTE_HINT if e.get("paste") else
                   f"Look at the pane: a prompt the guard blocked, or a person's half-typed words. Clear it only if it "
                   f"is not a person's draft (Esc twice clears a Claude Code input; never Ctrl+C twice).") + "\n"
                f"[to you as the organization's structure keeper, once per stuck box]")
        kp = (live.get(keeper) or {}) if keeper and keeper != name else {}
        if prompt and kp.get("pane_id") and kp.get("agent_status") in ("idle", "done"):
            prompt(kp["pane_id"], note, "of_pulse")
            log(f"told {keeper}: {name}'s input box is stuck")
        elif not keeper or keeper == name:
            send(f"stuck input box: {name}", f"pane {pane}: {e.get('chars')} chars through {e['count']} deliveries")
        else:
            continue  # the keeper is busy: the next pulse tries again
        of_events.emit("nudge_sent", {"position": name, "from_position": "of_pulse", "to_position": keeper,
                                      "about": f"input box held text through {e['count']} deliveries", "pane": pane,
                                      "kind": "input_stuck"}, position=keeper or name)
        e["reported"] = True
        changed = True
    if changed:
        import json as _j
        tmp = of_relay_client._stuck_path() + f".{os.getpid()}"
        with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
            _j.dump(doc, fh)
        os.replace(tmp, of_relay_client._stuck_path())


def parked_note(name, evidence):
    """In the relay's frame, as nudges are: never the owner's words."""
    return (f"Message from of_pulse (via of pulse, authority: none; registry check)\n"
            f"The registry says {name} is parked, but {name} is working ({evidence}). Unpark it in the registry "
            f"(status: active; waiting is not parked, it declares 'of status waiting'), or stop it.\n"
            f"[to you as the organization's structure keeper, once a day per position]")


def inbound_stalls(mem, now, send, queues=None):
    """Messages queued on the server for a position for over INBOUND_STALL_MIN minutes: the agent
    receives more than it takes in, or it never goes idle. Reported to the owner once per episode as
    of.stall_detected {cause: inbound_queue}. queues: of_relay_client.read_queues() (refreshed by the
    relay every 60 s; a file older than 10 minutes means the relay is down, and says nothing)."""
    import of_relay_client
    q = queues if queues is not None else of_relay_client.read_queues()
    old = {}
    for key, v in q.items():
        if not v.get("n") or now - (v.get("at") or 0) > 600:
            continue
        t0 = of_relay_client._iso_ts(v.get("oldest"))
        if t0 and now - t0 >= INBOUND_STALL_MIN * 60:
            old[key] = (v["n"], int((now - t0) // 60))
    new = [k for k in sorted(old) if k not in mem.get("inbound_stalls", [])]
    for k in new:
        n, age = old[k]
        pos = k.partition(":")[2]
        of_events.emit("stall_detected", {"position": pos, "cause": "inbound_queue", "depth": n, "oldest_min": age,
                                          "reason": f"{n} relayed message(s) waiting, oldest {age} min",
                                          "evidence": [f"inbound queue {n}, oldest {age} min"]}, position=pos)
    if new:
        send(f"{len(old)} agent(s) not taking their messages",
             ", ".join(f"{k.partition(':')[2]}: {old[k][0]} waiting {old[k][1]}m" for k in sorted(old))
             + "  (of inbox --mine in that session)")
    mem["inbound_stalls"] = sorted(old)
    return new


REPLY_QUIET_H = 2  # a peer's reply within the wait silences nudges for this long at most


def still_waits(r, observed, conn=None):
    """The projection was read at `observed`; a pass can type a nudge a minute later (of_capacity, 2026-11-11: a
    nudge 68 s after the snapshot, after the waiter had declared working and then waiting on someone else). True
    while nothing since says otherwise: no newer declaration of another state or peer, and the routed ask open."""
    import of_store
    c = conn or of_store.connect()
    try:
        at = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(observed))
        row = c.execute("SELECT payload FROM event WHERE name = 'of.state_declared' AND position = ? AND ts >= ? "
                        "ORDER BY ts DESC, rowid DESC LIMIT 1", (r["position"], at)).fetchone()
        if row:
            p = json.loads(row[0] or "{}")
            if not (p.get("state") == "waiting" and p.get("waiting_on") == r["on"]):
                return False
        aid = r.get("routed_ask")
        if aid and c.execute("SELECT 1 FROM event WHERE name IN ('of.agent_question_answered', 'of.ask_withdrawn') "
                             "AND payload LIKE ? LIMIT 1", (f"%{aid}%",)).fetchone():
            return False
        return True
    finally:
        if conn is None:
            c.close()


def wait_run_start(rows, waiter, peer):
    """When the waiter's unbroken run of `waiting --on <peer>` declarations began. Re-declaring the same wait (after
    the peer's reply, with a new note) does not start a new wait, so the reply still counts (acme_org, 2026-11-06:
    platform_owner answered runners_keeper and data_parts-sync, they re-declared, and the pulse nudged it)."""
    start = None
    for r in rows:
        if r["name"] != "of.state_declared" or r.get("position") != waiter:
            continue
        p = r.get("payload") or {}
        same = p.get("state") == "waiting" and str(p.get("waiting_on") or "").rpartition(":")[2] == peer
        start = (start or r["ts"]) if same else None
    return start


# Nudge back-off per waiting pair (of_architect, 2026-11-07: returns_keeper nudged storefront_operator 28 times with
# the same text, every 30 min): the first after the wait is seen, then 30 min, 2 h, and once a day after that.
NUDGE_BACKOFF_S = (30 * 60, 2 * 3600, 24 * 3600)


def _sha(text):
    import hashlib
    return hashlib.sha256((text or "").encode()).hexdigest()[:16]


def nudge_state(v, since):
    """The pair's nudge state {at, n, sha} for the current wait (a new wait starts the count again)."""
    if isinstance(v, (int, float)):  # the state before 2026-11-07: a bare time
        v = {"at": v, "n": 1, "sha": None, "since": None}
    if not v or (since and v.get("since") and since > v["since"] and v.get("at", 0) < (_ts(since) or 0)):
        return {"at": 0, "n": 0, "sha": None}
    return v


def may_nudge(last, now, text, rows, peer, status):
    """The back-off has passed, the last nudge was read, the same text is not repeated unanswered, and the receiver is
    not itself waiting on the founder (the item is with him; a nudge cannot move it)."""
    if last["n"] and now - last["at"] < NUDGE_BACKOFF_S[min(last["n"], len(NUDGE_BACKOFF_S)) - 1]:
        return False
    if unread_since(rows, peer, last["at"] or None):
        return False
    if last["sha"] == _sha(text) and last["n"] and not replied_any(rows, peer, _iso(last["at"])):
        return False
    st = status.get(peer) or {}
    return not (st.get("state") == "waiting" and st.get("waiting_on") in ("founder", "owner"))


def replied_any(rows, peer, since):
    """Did the peer send any message after `since` (an answer to the waiter, or anyone)?"""
    return any(r["name"] == "of.agent_message_out" and r.get("position") == peer and r["ts"] > since for r in rows)


def unread_since(rows, peer, nudged_at):
    """Was the last nudge to `peer` never read: no turn of the peer ended after it? Then no repeat."""
    if not nudged_at:
        return False
    at = _iso(nudged_at)
    return not any(r["name"] == "of.turn_end" and r.get("position") == peer and r["ts"] > at for r in rows)


def replied(rows, waiter, peer, since):
    """Did `peer` answer `waiter` after `since`? Any of.agent_message_out (a SendMessage seen by the hook, or
    `of send`, also with --reply-to) from the peer to the waiter, or the peer's `of asks answered` on one of the
    waiter's asks (acme_org, 2026-11-07: unit owners were nudged for keepers whose asks they had answered)."""
    asks_of_waiter = None
    for r in rows:
        if (r.get("position") != peer and r["name"] != "of.agent_question_answered") or (since and r["ts"] <= since):
            continue
        p = r.get("payload") or {}
        if r["name"] == "of.agent_message_out" and r.get("position") == peer:
            if waiter in (p.get("to_position"), p.get("to")) or str(p.get("to") or "").endswith(f":{waiter}"):
                return True
        elif r["name"] == "of.agent_question_answered" and p.get("by") == peer:
            if asks_of_waiter is None:
                asks_of_waiter = {x["event_id"] for x in rows if x["name"] == "of.agent_question" and x.get("position") == waiter}
            if asks_of_waiter & set(p.get("ask_ids") or [p.get("ask_id")]):
                return True
    return False


MESSAGE_DAYS = 2
PULSE_DAYS = 3  # the pulse's window of the log: waits, replies, turns and nudges are recent facts
# the names its own rules read (the asks come from the view): declarations, turns, prompts, messages, answers
PULSE_NAMES = ("of.state_declared", "of.turn_end", "of.human_prompt", "of.agent_message_out", "of.agent_message_in",
               "of.agent_question", "of.agent_question_answered", "of.ask_withdrawn")


def load_rows(conn, now=None):
    """The events the pulse reads: the asks projection's kinds of the last PULSE_DAYS days, and each position's latest
    declared state however old (latest_status). Never the whole log (the founder's goal of 2026-11-14, prompt
    01a1d68e-1d3e; organic-framework-build reports/of-log-readers-2026-11-14.md: 7 to 10 s of CPU a pulse)."""
    import json as _j
    import of_asks
    rows = []
    for e, ts, n, sid, pos, pl, org, src in conn.execute(
            "SELECT event_id, ts, name, session_id, position, payload, org, source FROM event WHERE name IN "
            f"({','.join('?' * len(PULSE_NAMES))}) AND ts >= ? ORDER BY ts, rowid",
            PULSE_NAMES + (_iso((now or time.time()) - PULSE_DAYS * 86400),)):
        try:
            payload = _j.loads(pl) if pl else {}
        except ValueError:
            payload = {}
        if of_asks.relevant(n, src, payload):  # as the asks projection takes them (sources, subagents, pulls)
            rows.append({"event_id": e, "ts": ts, "name": n, "session_id": sid, "position": pos, "payload": payload,
                         "org": org})
    have = {r["event_id"] for r in rows}
    for e, ts, n, sid, pos, pl, org in conn.execute(
            "SELECT event_id, ts, name, session_id, position, payload, org FROM event WHERE rowid IN "
            "(SELECT max(rowid) FROM event WHERE name = 'of.state_declared' AND position IS NOT NULL GROUP BY position)"):
        if e in have:
            continue
        try:
            payload = _j.loads(pl) if pl else {}
        except ValueError:
            payload = {}
        rows.append({"event_id": e, "ts": ts, "name": n, "session_id": sid, "position": pos, "payload": payload,
                     "org": org})
    return sorted(rows, key=lambda r: r["ts"] or "")


def current_asks(conn=None):
    """The asks from the listener's view and the log's tail (of_asks.current_asks); the log only where no view is."""
    import of_asks
    r = of_asks.current_asks()
    if r["age_s"] is None and not r["asks"]:
        import of_store
        return of_asks.project(of_asks.load(conn or of_store.connect()))
    return r["asks"]


def with_messages(conn, rows, now=None):
    """`rows` (the asks projection's) plus every of.agent_message_out of the last MESSAGE_DAYS days, in time order.
    `of send` writes them with source `of`, which the asks projection leaves out; without them the pulse never saw a
    peer's relayed reply and nudged it again (2026-11-07: 2637 such messages that day, 5 from the hook)."""
    import json as _j
    since = _iso((now or time.time()) - MESSAGE_DAYS * 86400)
    have = {r["event_id"] for r in rows if r["name"] == "of.agent_message_out"}
    extra = []
    for e, ts, n, sid, pos, pl in conn.execute(
            "SELECT event_id, ts, name, session_id, position, payload FROM event WHERE name = 'of.agent_message_out' "
            "AND ts >= ? ORDER BY ts, rowid", (since,)):
        if e in have:
            continue
        try:
            payload = _j.loads(pl) if pl else {}
        except ValueError:
            payload = {}
        extra.append({"event_id": e, "ts": ts, "name": n, "session_id": sid, "position": pos, "payload": payload})
    return sorted(rows + extra, key=lambda r: r["ts"]) if extra else rows


def matched_tag(rule, tags):
    """The tag that fires `rule`, or None. `money` matches an ask tagged money; `prod_change{affects:[a,b]}` (or
    window_go{systems:[...]}) matches a tag whose systems meet the list. A declared tag that names no systems meets
    every list; a guessed one never does (acme_org, 2026-11-09: a guessed prod_change with no systems escalated
    partner-feed's routing request by a storefront rule)."""
    import re
    m = re.match(r"^(\w+)(?:\{\s*(?:affects|systems)\s*:\s*\[([^\]]*)\]\s*\})?$", (rule or "").strip())
    if not m:
        return None
    want = {x.strip() for x in (m.group(2) or "").split(",") if x.strip()}
    for t in tags or []:
        if t.get("tag") != m.group(1):
            continue
        if not want or want & set(t.get("systems") or []) or (not t.get("systems") and t.get("source") != "guessed"):
            return t
    return None


def tag_match(rule, tags):
    """Whether `rule` fires on `tags` (see matched_tag)."""
    return matched_tag(rule, tags) is not None


def effective_tags(ask):
    """The tags escalation rules see: the declared ones are authoritative; guessed tags count only when the asker
    declared none at all (no --tags), never beside declared tags nor after 'of ask --tags none' (of_architect,
    2026-11-07: a guessed prod_change must not escalate a routine release the unit owner may approve)."""
    tags = ask.get("tags") or []
    declared = [t for t in tags if t.get("source") != "guessed"]
    if declared or ask.get("tags_declared"):
        return declared
    return tags


def due_soon(rule, due, now):
    """`ask_due < N d|h`: the ask's own --due is less than N away (docs/registry.md, Delegations; hand-over 2b2289a8,
    a user organization's compliance duties falling due). False for any other rule or an ask with no due."""
    import re
    m = re.match(r"^ask_due\s*<\s*(\d+)\s*([dh])$", rule)
    at = _ts(due) if m and due else None
    return at is not None and at - now < int(m.group(1)) * (86400 if m.group(2) == "d" else 3600)


def escalations(positions, asks, now, already=()):
    """Asks routed to a delegated unit's owner that go up to the founder now, per the delegation's
    reporting.escalate_when: `ask.blocking` (at once), `ask_unanswered > 2h` (after that long), `ask_due < 7d` (its own
    --due less than that away, at filing or later as the date nears).
    Returns [(ask, rule, delegation id)]; `already` = ask ids escalated before."""
    import re
    by_name = {p["name"]: p for p in positions}
    out = []
    for a in asks:
        if a["state"] != "open" or a.get("to") in ("founder", "owner", None) or a["event_id"] in already:
            continue
        d = (by_name.get(a.get("position")) or {}).get("_delegation") or {}
        rules = [str(x).strip() for x in ((d.get("reporting") or {}).get("escalate_when") or [])]
        age = now - (_ts(a["ts"]) or now)
        for rule in rules:
            m = re.match(r"^ask_unanswered\s*>\s*(\d+)\s*([mh])$", rule)
            if (rule == "ask.blocking" and "blocking" in (a.get("kind") or "")) or \
                    (m and age >= int(m.group(1)) * (3600 if m.group(2) == "h" else 60)) or \
                    due_soon(rule, a.get("due"), now) or tag_match(rule, effective_tags(a)):
                out.append((a, rule, d.get("id")))
                break
    return out


def run_escalations(positions, asks, now, conn, log=print, elsewhere=None):
    """Write of.ask_escalated and the founder's copy of the question (once per ask). An ask whose position is held on
    another machine is that machine's to escalate; the ids are fixed per ask, so two machines that both escalate one
    (a position held nowhere) write one escalation."""
    import json as _j
    done = None
    try:  # the escalated view (bin/of_fold.py): a dedupe, never a decision of trust (the ids are fixed per ask)
        import of_fold
        r = of_fold.read("escalated")
        if not r.late:
            done = set(r.rows)
    except Exception:  # noqa: BLE001
        done = None
    if done is None:  # no listener here, or it is behind: the escalations' own events, one name by its index
        done = {(_j.loads(pl or "{}")).get("ask_id") for (pl,) in
                conn.execute("SELECT payload FROM event WHERE name = 'of.ask_escalated'")}
    away = {n for _, n in (elsewhere or {})}
    n = 0
    for a, rule, did in escalations(positions, asks, now, done):
        if a.get("position") in away:
            continue
        escalate(a, rule, did)
        n += 1
    if n:
        log(f"escalated {n} routed ask(s) to the founder")
    return n


def escalate(a, rule, did):
    """of.ask_escalated and the founder's copy of a routed ask `a` (a projected ask). Returns the copy's event id.
    Also called by `of ask` when a rule fires at filing, so the frame typed to the unit owner can say so (acme_org,
    2026-11-08: platform_owner refiled net_keeper's 878829d1 by hand, not knowing OF had escalated it)."""
    t = matched_tag(rule, effective_tags(a))
    guessed = bool(t and t.get("source") == "guessed")
    why = rule + (f", a guessed tag ({t.get('method')})" if guessed else "")
    of_events.emit("ask_escalated", {"ask_id": a["event_id"], "from_owner": a.get("to"), "to": "founder",
                                     "rule": rule, "delegation": did,
                                     **({"tag_source": "guessed", "tag_method": t.get("method")} if guessed else {})},
                   position=a.get("position"), event_id=of_events.stable_id(a["event_id"], "escalated"))
    return of_events.emit("agent_question", {
        "detected_by": "declared", "source": "escalated", "from_position": a.get("position"), "to": "founder",
        "escalated_from": a["event_id"], "delegation": did, "blocking": "blocking" in (a.get("kind") or ""),
        "tag_guessed": guessed,
        "questions": [{"question": f"[escalated from {a.get('to')}: {why}] {a['text']}", "options": []}],
        "classifications": {"q-v0": True, "q-v1": True}}, position=a.get("position"),
        event_id=of_events.stable_id(a["event_id"], "founder_copy"))


STUDIO_ESC_TIMEOUT_S = 60


def _single_run(cmd, lock, timeout, env=None):
    """Run `cmd` as the only copy: a non-blocking lock the child inherits, so it holds while the child lives, even if
    this pulse is killed; the child gets its own process group, killed whole on a timeout (2026-11-10: copies of `of
    model escalations` outlived killed pulses and piled up, one per pulse, 0.5-0.9 GB each). Returns the exit code,
    or None when a copy is already running."""
    import fcntl
    import signal
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True, pass_fds=(fd,), env=env)
        try:
            return p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.wait()
            raise
    finally:
        os.close(fd)


def studio_escalations(positions, conn, log=print, run=None):
    """Studio's escalate_when conditions (issue severity, budget, envelope): `of model escalations
    --org O --write` per organization with a delegation records of.escalation_due; each new one gets
    the founder's copy of the question, once (as of.ask_escalated does for routed asks)."""
    import json as _j
    import os as _os
    import sys as _sys
    orgs = sorted({p.get("_org") for p in positions if p.get("_delegations")})
    of_path = _os.path.join(_os.path.dirname(_os.path.realpath(__file__)), "of")
    run = run or _single_run
    for org in orgs:
        try:
            run([_sys.executable, of_path, "model", "escalations", "--org", org, "--write"],
                lock=_os.path.join(of_events.STATE_DIR, f"model-escalations-{org}.lock"), timeout=STUDIO_ESC_TIMEOUT_S,
                env=dict(_os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
        except Exception as e:  # noqa: BLE001
            log(f"studio escalations {org}: {e}")
    copied = {(_j.loads(pl or "{}")).get("escalated_from") for (pl,) in conn.execute(
        "SELECT payload FROM event WHERE name = 'of.agent_question' AND payload LIKE '%escalated_from%'")}
    n = 0
    for eid, pos, pl in conn.execute("SELECT event_id, position, payload FROM event WHERE name = 'of.escalation_due'").fetchall():
        if eid in copied:
            continue
        p = _j.loads(pl or "{}")
        q = (f"[escalated: {p.get('condition')} in unit {p.get('unit')}, owner {p.get('from_owner')}] "
             f"evidence: {', '.join(p.get('evidence') or []) or '-'}. Look at it, or let the unit owner handle it?")
        of_events.emit("agent_question", {
            "detected_by": "declared", "source": "escalated", "from_position": p.get("from_owner") or pos,
            "to": "founder", "escalated_from": eid, "delegation": p.get("delegation"), "blocking": False,
            "questions": [{"question": q, "options": ["I look at it", "the unit owner handles it"]}],
            "classifications": {"q-v0": True, "q-v1": True}}, position=p.get("from_owner") or pos,
            event_id=of_events.stable_id(eid, "founder_copy"))
        n += 1
    if n:
        log(f"escalation_due: {n} copied to the founder")
    return n


DISK_WARN = 50 * 10 ** 9   # docs/storage.md: `of` warns below 50 GB free (of_architect, 2026-11-06)
DISK_CLEAR = 55 * 10 ** 9  # the episode ends above this, so a disk hovering at 50 GB does not warn every pulse


def disk_check(mem, now, send=notify, free=None, log=print):
    """Free space on the Mac below DISK_WARN: one notification per episode, again once a day while it stays low
    (2026-11-08: it fell from 68 to 57 GiB in an hour and only an Acme agent noticed)."""
    import shutil
    free = shutil.disk_usage(os.path.expanduser("~")).free if free is None else free
    if free >= DISK_CLEAR:
        mem.pop("disk_low", None)
    elif free < DISK_WARN and now - mem.get("disk_low", 0) >= 86400:
        send(tr("Disk: {free} GB free (< {warn})", free=f"{free / 1e9:.0f}", warn=f"{DISK_WARN / 1e9:.0f}"),
             tr("of db stats and du -sh ~/.local/state/of/* show what takes OF's space"))
        mem["disk_low"] = now
        log(f"disk: {free / 1e9:.0f} GB free")


def guard_check(mem, now, log=print):
    """Every pulse: in each Claude config dir whose settings install the guard, run it on a harmless probe.
    A guard that does not answer deny opens ONE ask to the founder per broken episode (the deny rules
    still hold; failing closed would stop every agent's tools)."""
    import glob
    import os as _os
    import json as _j
    import of_init
    broken = mem.setdefault("guard_broken", {})
    for d in sorted(glob.glob(_os.path.expanduser("~/.claude*"))):
        try:
            with open(_os.path.join(d, "settings.json")) as fh:
                settings = _j.load(fh)
        except (OSError, ValueError):
            continue
        ok, detail = of_init.guard_works(settings)
        if ok is None:
            continue
        if ok:
            broken.pop(d, None)
            close_guard_asks(d, log)
        elif d not in broken:
            broken[d] = now
            of_events.emit("agent_question", {
                "detected_by": "declared", "source": "guard_check", "from_position": "of_platform", "to": "founder",
                "topic_key": f"guard-broken-{d}", "blocking": False,
                "questions": [{"question": f"The secrets guard in {d} does not run ({detail}). The deny rules still "
                               "hold, but the guard's checks are off until it is fixed (re-run of hooks install "
                               f"--guard --config-dir {d}). Fix it now?", "options": ["fix now", "later"]}]},
                position="of_platform")
            log(f"guard broken in {d}: {detail}")


def close_guard_asks(d, log=print, conn=None):
    """The guard answers deny again in config dir `d`: close the founder's open guard asks for it, by the pulse
    (of_server, 2026-11-10: ask 574af69d stayed open after the cause was fixed). Returns how many it closed."""
    import of_store
    conn = conn or of_store.connect()
    ids = [eid for (eid,) in conn.execute(
        "SELECT event_id FROM event WHERE name = 'of.agent_question' AND json_extract(payload, '$.source') = "
        "'guard_check' AND json_extract(payload, '$.topic_key') = ?", (f"guard-broken-{d}",))]
    if not ids:
        return 0
    # closed = named by an answer or a withdrawal; not a projection of the whole asks log (it ran once per config
    # dir, 10 of a pulse's 20 s on 2026-11-10)
    open_ = {i for i in ids if not conn.execute(
        "SELECT 1 FROM event WHERE name IN ('of.agent_question_answered', 'of.ask_withdrawn') AND payload LIKE ? "
        "LIMIT 1", (f"%{i}%",)).fetchone()}
    if open_:
        of_events.emit("agent_question_answered", {"ask_id": sorted(open_)[0], "ask_ids": sorted(open_),
                                                   "by": "of_platform", "via": "pulse",
                                                   "note": f"the guard in {d} answers deny again"},
                       position="of_platform")
        log(f"guard in {d} works again: closed {len(open_)} ask(s)")
    return len(open_)


def handover_windows(positions, agents, bind, handovers, now, mem, prompt, offer, log=print, elsewhere=None, send=notify):
    """Response windows (docs/handover.md §1.2): offer a proposed hand-over when the receiver's
    pane is idle; nudge once per window when it stays unanswered; expire it after the due date
    or EXPIRE_WINDOWS windows. A receiver held on another machine is left to that machine; the expiry's id is fixed,
    so a hand-over to a position held nowhere expires once even when two machines expire it."""
    import of_handover
    away = {n for _, n in (elsewhere or {})}
    live = {p["name"]: a for p, a in bind(positions, agents)[0]}
    win = {p["name"]: p.get("response_window") or of_handover.WINDOW_MIN for p in positions}

    def idle_local(name):
        a = live.get(name)
        return bool(a and a.get("agent_status") in ("idle", "done"))
    nudged = mem.setdefault("handover_nudged", {})
    for action, h in of_handover.due_actions(handovers, now, lambda n: int(win.get(n, of_handover.WINDOW_MIN)),
                                             idle_local):
        to = of_handover.short(h["to"])
        if to in away:
            continue
        if action == "expire":
            rec = of_events.record("handover_expired", {"id": h["id"], "by": "of_pulse", "from": h["from"],
                                                        "to": h["to"]}, position=to)
            rec["of.event.id"] = of_handover.event_id(h["id"], "expired")
            of_events.append(rec)
            send(f"hand-over expired: {h['from']} -> {h['to']}", h["title"][:120])
            log(f"expired hand-over {h['id'][-8:]}")
        elif action == "offer" and offer:
            if offer(h):
                log(f"offered hand-over {h['id'][-8:]} to {to}")
        elif action == "nudge" and idle_local(to) and now - nudged.get(h["id"], 0) >= max(win.get(to, 30), NUDGE_EVERY_MIN) * 60:
            if prompt:
                prompt(live[to]["pane_id"], f"Reminder: hand-over {h['id'][-8:]} from {h['from']} waits for your "
                       f"answer: {h['title']}. `of handover accept|decline {h['id'][-8:]}`", h["from"])
            of_events.emit("nudge_sent", {"position": h["from"], "from_position": h["from"], "to_position": to,
                                          "about": f"hand-over {h['id']}", "handover_id": h["id"],
                                          "pane": live[to]["pane_id"]}, position=h["from"])
            nudged[h["id"]] = now
            log(f"nudged {to} on hand-over {h['id'][-8:]}")


def herdr_prompt(pane, text, sender):
    """Type a nudge into an idle peer's pane, declared as an injected prompt first."""
    import of_relay_client
    return of_relay_client.herdr_deliver(pane, text, None, f"of-pulse:{sender}", wait_s=5)
