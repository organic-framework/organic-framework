"""Hand-overs between positions and owners (docs/handover.md §1).

A hand-over is work one position asks another to do, with checkable `done_when` and a verifier.
Nothing is stored but events; this module is the projection over them and the transition rules:

  proposed ──► accepted ──► delivered ──► verified ──► closed
      │            │  ▲          │
      ├► declined  ├► blocked    └► rejected (back to accepted)
      ├► withdrawn ├► returned
      └► expired   └► withdrawn

Who may write which event: the receiver (`to`) accepts, declines, blocks, delivers and returns;
the verifier (`verify`: the requester by default, or a named position) verifies and rejects; the
requester (`from`) closes and withdraws; `of pulse` offers (types it into the receiver's idle
pane: `of.handover_offered`, where the response window starts) and expires.
"""

import calendar
import json
import os
import re
import time
import uuid

NS = uuid.UUID("5d0c7a3e-91b2-4f6d-8e4a-2b3c4d5e6f70")
KINDS = ("proposed", "offered", "accepted", "declined", "blocked", "delivered", "verified", "rejected",
         "closed", "returned", "withdrawn", "expired", "noted")
OPEN = ("proposed", "accepted", "blocked", "delivered", "verified")
WINDOW_MIN = 30          # default response window, from the offer into the receiver's pane
EXPIRE_WINDOWS = 4       # unanswered this many windows: expired

# (state, kind) -> (new state, actor role)
MOVES = {
    ("proposed", "accepted"): ("accepted", "to"),
    ("proposed", "declined"): ("declined", "to"),
    ("proposed", "withdrawn"): ("withdrawn", "from"),
    ("proposed", "expired"): ("expired", "pulse"),
    ("accepted", "blocked"): ("blocked", "to"),
    ("accepted", "delivered"): ("delivered", "to"),
    ("accepted", "returned"): ("returned", "to"),
    ("accepted", "withdrawn"): ("withdrawn", "from"),
    ("blocked", "accepted"): ("accepted", "to"),
    ("blocked", "delivered"): ("delivered", "to"),
    ("blocked", "returned"): ("returned", "to"),
    ("blocked", "withdrawn"): ("withdrawn", "from"),
    ("delivered", "verified"): ("verified", "verifier"),
    ("delivered", "rejected"): ("accepted", "verifier"),
    ("verified", "closed"): ("closed", "from"),
}


def event_id(hid, kind, n=0):
    """Deterministic ids, so a relay mirror on another machine writes the same event."""
    return str(uuid.uuid5(NS, f"{hid}:{kind}:{n}"))


def load(conn):
    # a range on the name, which the name index serves; LIKE scanned all 421,000 events for 769 (2.5 s of CPU on
    # each of handover list, the pulse and the hook's mandate check, 2026-11-14). '`' sorts right after '_'.
    rows = conn.execute("SELECT event_id, ts, name, position, payload FROM event WHERE name >= 'of.handover_' "
                        "AND name < 'of.handover`' ORDER BY ts, rowid").fetchall()
    out = []
    for e, ts, n, pos, pl in rows:
        try:
            payload = json.loads(pl) if pl else {}
        except ValueError:
            payload = {}
        out.append({"event_id": e, "ts": ts, "name": n, "position": pos, "payload": payload})
    return out


def project(rows):
    """{handover id: state dict}, from handover events in time order. Invalid moves are ignored
    (and listed under `ignored`), so a stray event cannot corrupt the state."""
    out = {}
    for r in rows:
        kind = r["name"].removeprefix("of.handover_")
        p = r["payload"] or {}
        hid = p.get("id") or (r.get("event_id") if kind == "proposed" else None)  # the proposal's id is its event id
        if kind == "proposed":
            if hid in out:
                continue
            out[hid] = {"id": hid, "state": "proposed", "from": p.get("from"), "to": p.get("to"),
                        "title": p.get("title"), "done_when": p.get("done_when"),
                        "verify": p.get("verify") or "requester", "due": p.get("due"),
                        "priority": p.get("priority") or "normal", "mandate": p.get("mandate"),
                        "scope": p.get("scope"), "context": p.get("context") or [], "basis": p.get("basis"),
                        "proposed_at": r["ts"], "offered_at": None, "since": r["ts"], "history": [],
                        "rejections": 0, "result": None, "ignored": [], "notes": []}
            out[hid]["history"].append((r["ts"], "proposed", p.get("from"), None))
            continue
        h = out.get(hid)
        if not h:
            continue
        if kind == "offered":
            h["offered_at"] = h["offered_at"] or r["ts"]
            continue
        if kind == "noted":  # a note on the record, never a state change (a user organization, 2026-11-11)
            if h["state"] in OPEN and (same(h["from"], p.get("by")) or same(h["to"], p.get("by"))
                                       or p.get("by") in (short(h["from"]), short(h["to"]))):
                h.setdefault("notes", []).append({"ts": r["ts"], "by": p.get("by"), "note": p.get("note")})
            else:
                h["ignored"].append((r["ts"], kind))
            continue
        move = MOVES.get((h["state"], kind))
        if not move:
            h["ignored"].append((r["ts"], kind))
            continue
        h["state"], h["since"] = move[0], r["ts"]
        h["history"].append((r["ts"], kind, p.get("by"), p.get("note")))
        if kind == "rejected":
            h["rejections"] += 1
        if kind == "delivered":
            h["result"] = p.get("result")
        if kind == "blocked":
            h["blocked_on"] = p.get("on")
    return out


def verifier(h):
    v = h.get("verify") or "requester"
    return h["from"] if v == "requester" else v


def check(h, kind, actor, org=None):
    """None if `actor` (a position of organization `org`) may write `kind` on hand-over `h` now, else the reason.
    A party may be stored as `org:name` (a requester wrote the receiver's full address): it matches the actor of
    that organization (acme_org, 2026-11-07: returns_keeper could not accept f8de73ae addressed to
    acme:returns_keeper)."""
    if kind not in KINDS or kind in ("proposed", "offered"):
        return f"unknown action {kind!r}"
    if kind == "noted":  # the requester or the receiver adds a note to an open hand-over; its state stays
        if h["state"] not in OPEN:
            return f"hand-over is {h['state']}; a note goes on an open one"
        if not (same(h["from"], actor, org) or same(h["to"], actor, org)):
            return f"only {h['from']} (requester) or {h['to']} (receiver) may add a note"
        return None
    move = MOVES.get((h["state"], kind))
    if not move:
        return f"hand-over is {h['state']}; cannot be {kind}"
    role = move[1]
    who = {"to": h["to"], "from": h["from"], "verifier": verifier(h), "pulse": "of_pulse"}[role]
    if not same(who, actor, org):
        return f"only {who} ({role}) may write {kind}"
    return None


def same(party, actor, org=None):
    """Is `party` (a bare name, or `org:name`) the position `actor` of organization `org`?"""
    if party == actor:
        return True
    o, sep, name = (party or "").partition(":")
    return bool(sep) and name == actor and org is not None and o == org


def short(name):
    """A position address without its organization: `org:name` -> `name`."""
    return (name or "").split(":", 1)[-1]


def waits(handovers):
    """[(requester, receiver, handover)] for blocking hand-overs not yet delivered: the requester waits."""
    return [(short(h["from"]), short(h["to"]), h) for h in handovers.values()
            if h["priority"] == "blocking" and h["state"] in ("proposed", "accepted", "blocked")]


def _ts(iso):
    try:
        return calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return None


def due_actions(handovers, now, window_of, offered_ok):
    """What `of pulse` should do now. window_of(position) -> minutes; offered_ok(position) -> True
    if the receiver's pane is idle and local. Returns [(action, handover)], action in
    offer | nudge | expire."""
    out = []
    for h in handovers.values():
        if h["state"] != "proposed":
            continue
        to = short(h["to"])
        w = window_of(to) * 60
        due = _ts(h["due"] + "T23:59:59") if h.get("due") and len(h["due"]) == 10 else None
        start = _ts(h["offered_at"]) if h["offered_at"] else None
        if (due and now > due) or (start and now - start >= EXPIRE_WINDOWS * w):
            out.append(("expire", h))
        elif not start and offered_ok(to):
            out.append(("offer", h))
        elif start and now - start >= w:
            out.append(("nudge", h))
    return out


# docs/handover.md §1.1 (of_architect cfda02c; the founder's incident of 2026-11-11 16:58Z: an unconfirmed inference
# travelled as a must and merged as a "compliance requirement"): why the work is owed.
# an ask id in full (a hyphenated UUID) or its hex prefix or end (review 2026-11-11, finding 6)
BASIS = re.compile(r'^(?:ask:(?:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}|[0-9a-f]{6,40})'
                   r'|source:\S.*"[^"]+"\s*$|evidence:\S.*|inference)$', re.S)
OBLIGATION = re.compile(r"(?i)\b(must|required|requirements?|compliance|compliant|mandated|mandatory)\b")
BASIS_HELP = ("--basis 'ask:<id>' (an owner's decision), 'source:<law, contract, standard or document> \"<the quote>\"', "
              "'evidence:<a commit, metric, log or report>', or 'inference' (your own reasoning, unconfirmed)")


def basis_problem(basis, title, done_when):
    """Why a proposal's basis is not acceptable, or None (docs/handover.md §1.1)."""
    if basis:
        return None if BASIS.match(basis.strip()) else f"--basis {basis!r} is none of the forms: {BASIS_HELP}"
    said = OBLIGATION.search(f"{title or ''}\n{done_when or ''}")
    if said:
        return (f"the hand-over states an obligation ({said.group(0)!r}) without saying why it is owed; add "
                f"{BASIS_HELP}, or reword it without the obligation")
    return None


def basis_kind(basis):
    return (basis or "").split(":", 1)[0] or "none"


def offer_text(h):
    lines = [f"Hand-over {h['id'][-8:]} from {h['from']}: {h['title']}",
             f"Done when: {h['done_when']}",
             f"Basis: {h.get('basis') or 'none given'}" + (" (unconfirmed: the requester's own reasoning)"
                                                           if basis_kind(h.get('basis')) == "inference" else ""),
             f"Verifier: {verifier(h)}" + (f" · due {h['due']}" if h.get("due") else "")
             + (" · BLOCKING (the requester waits)" if h["priority"] == "blocking" else "")]
    if h.get("context"):
        lines.append("Context: " + "; ".join(h["context"]))
    for n in h.get("notes") or []:
        lines.append(f"Note from {n['by']} ({(n['ts'] or '')[:16]}Z): {n['note']}")
    lines.append(f"Answer within your response window: `of handover accept {h['id'][-8:]}` or "
                 f"`of handover decline {h['id'][-8:]} --note \"why\"` (or `return`).")
    return "\n".join(lines)


def find(handovers, ref):
    """A hand-over by full id or a unique prefix/suffix."""
    hits = [h for hid, h in handovers.items() if hid and (hid == ref or hid.startswith(ref) or hid.endswith(ref))]
    return hits[0] if len(hits) == 1 else None


def scopes_path():
    import of_events
    return os.path.join(of_events.STATE_DIR, "handover-scopes.json")


def write_scopes(handovers, path=None):
    """The accepted and blocked hand-overs with a scope, for the hook's mandate check (the listener keeps it): the hook
    reads this small file instead of the store (the read path's 3.6: the recorder hook only appends)."""
    path = path or scopes_path()
    keep = {hid: {"id": h["id"], "to": h["to"], "state": h["state"], "scope": h.get("scope")}
            for hid, h in handovers.items() if h.get("state") in ("accepted", "blocked") and h.get("scope")}
    with open(os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        json.dump(keep, fh)
    os.replace(path + ".tmp", path)
    return len(keep)


SCOPES_LIVE_S = 120  # the listener's main loop, which writes the scopes file, beats every 5 s; older: it is not running


def read_scopes(path=None, now=None):
    """The listener's copy of the scoped hand-overs, or None when there is none or the listener is not running (its
    listen.heartbeat, which the main loop that writes the file touches every 5 s, older than SCOPES_LIVE_S), so a stopped
    listener's old copy is never read (of_review on PRs 140 and 141)."""
    import time
    import of_events
    try:
        if (now or time.time()) - os.path.getmtime(os.path.join(of_events.STATE_DIR, "listen.heartbeat")) > SCOPES_LIVE_S:
            return None
    except OSError:
        return None
    try:
        with open(path or scopes_path()) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def covering(handovers, position, system, path):
    """The id of an accepted (or blocked) hand-over to `position` whose scope covers this system or
    path, else None: the router's verdict is then in_handover:<id> instead of owned_by:<keeper>."""
    import re
    import of_router
    for h in handovers.values():
        if short(h["to"]) != position or h["state"] not in ("accepted", "blocked") or not h.get("scope"):
            continue
        sc = h["scope"]
        if system and system in (sc.get("systems") or []):
            return h["id"]
        if path and any(re.match(of_router.glob_rx("**/" + g.lstrip("/")), path) for g in sc.get("paths") or []):
            return h["id"]
    return None
