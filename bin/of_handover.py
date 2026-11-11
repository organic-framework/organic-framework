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
import time
import uuid

NS = uuid.UUID("5d0c7a3e-91b2-4f6d-8e4a-2b3c4d5e6f70")
KINDS = ("proposed", "offered", "accepted", "declined", "blocked", "delivered", "verified", "rejected",
         "closed", "returned", "withdrawn", "expired")
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
    rows = conn.execute("SELECT event_id, ts, name, position, payload FROM event WHERE name LIKE 'of.handover_%' "
                        "ORDER BY ts, rowid").fetchall()
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
                        "scope": p.get("scope"), "context": p.get("context") or [],
                        "proposed_at": r["ts"], "offered_at": None, "since": r["ts"], "history": [],
                        "rejections": 0, "result": None, "ignored": []}
            out[hid]["history"].append((r["ts"], "proposed", p.get("from"), None))
            continue
        h = out.get(hid)
        if not h:
            continue
        if kind == "offered":
            h["offered_at"] = h["offered_at"] or r["ts"]
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


def offer_text(h):
    lines = [f"Hand-over {h['id'][-8:]} from {h['from']}: {h['title']}",
             f"Done when: {h['done_when']}",
             f"Verifier: {verifier(h)}" + (f" · due {h['due']}" if h.get("due") else "")
             + (" · BLOCKING (the requester waits)" if h["priority"] == "blocking" else "")]
    if h.get("context"):
        lines.append("Context: " + "; ".join(h["context"]))
    lines.append(f"Answer within your response window: `of handover accept {h['id'][-8:]}` or "
                 f"`of handover decline {h['id'][-8:]} --note \"why\"` (or `return`).")
    return "\n".join(lines)


def find(handovers, ref):
    """A hand-over by full id or a unique prefix/suffix."""
    hits = [h for hid, h in handovers.items() if hid and (hid == ref or hid.startswith(ref) or hid.endswith(ref))]
    return hits[0] if len(hits) == 1 else None


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
