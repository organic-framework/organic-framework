"""Mandates and holders over time (RFC 0001 v3, phase 2, observe gap 1): the basis of "the model at t".

The registry is the current snapshot; the log holds the history. `record()` compares each position
with the last events about it and writes only what changed:
  of.mandate_changed {position, mandate: {path, revision}, summary, status, throughput, serves,
                      changed: [fields], first: bool}
  of.holder_bound    {position, session, pane}       a live holder appears or changes
  of.holder_unbound  {position, previous_session}    the live holder is gone
Run by `of apply` and by `of pulse`, so the history accrues on its own.
"""

import hashlib
import json
import os

import of_events

FIELDS = ("summary", "status", "throughput", "serves", "mandate_exit", "mandate_functions")


def brief_revision(p):
    """sha256 prefix of the brief's content, or None when the position names no readable brief."""
    path = p.get("mandate")
    if not path:
        return None
    full = path if os.path.isabs(path) else os.path.join(p.get("_root") or "", path)
    try:
        with open(full, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:16]
    except OSError:
        return None


def snapshot(p):
    snap = {k: p.get(k) for k in FIELDS}
    snap["mandate"] = {"path": p.get("mandate"), "revision": brief_revision(p)}
    return snap


def _last(conn, name, position):
    row = conn.execute("SELECT payload FROM event WHERE name = ? AND position = ? ORDER BY ts DESC, rowid DESC LIMIT 1",
                       (name, position)).fetchone()
    return json.loads(row[0]) if row and row[0] else None


def record(conn, positions, agents, bind, elsewhere=None):
    """Write the mandate and holder changes since the last record. Returns how many events.

    Several machines (docs/server-several-machines.md §4.3): each one binds what it holds and unbinds only a holder
    it bound itself (the last of.holder_bound came from this machine), so a holder on another machine, seen through
    the coordination feed, is never unbound here. A position held elsewhere now is left to that machine. A mandate
    snapshot has a fixed id per position and content, so two machines reading one registry write it once."""
    n = 0
    live = {p["name"]: a for p, a in bind(positions, agents)[0]}
    away = {nm for _, nm in (elsewhere or {})}
    me = of_events.machine_id()
    for p in positions:
        name, org = p["name"], p.get("_orgid")
        snap, last = snapshot(p), _last(conn, "of.mandate_changed", name)
        prev = {k: (last or {}).get(k) for k in FIELDS} | {"mandate": (last or {}).get("mandate")}
        changed = [k for k in (*FIELDS, "mandate") if snap.get(k) != prev.get(k)]
        if last is None or changed:
            of_events.emit("mandate_changed", dict(snap, position=name, changed=changed if last else [],
                                                   first=last is None, issued_by=None,
                                                   reason="registry snapshot" if last is None else "registry diff"),
                           position=name, org=org,
                           event_id=of_events.stable_id("mandate", name, json.dumps(prev, sort_keys=True, default=str),
                                                        json.dumps(snap, sort_keys=True, default=str)))
            n += 1
        a = live.get(name)
        if not a and name in away:
            continue
        last_session, bound_here = None, False
        rows = conn.execute("SELECT name, payload, host_id FROM event WHERE name IN ('of.holder_bound', "
                            "'of.holder_unbound') AND position = ? ORDER BY ts DESC, rowid DESC LIMIT 1",
                            (name,)).fetchone()
        if rows and rows[0] == "of.holder_bound":
            last_session = json.loads(rows[1]).get("session")
            bound_here = rows[2] in (None, me)
        session = (a.get("agent_session") or {}).get("value") if a else None
        if a and session != last_session:
            of_events.emit("holder_bound", {"position": name, "session": session, "pane": a["pane_id"],
                                            "previous_session": last_session}, position=name, org=org, session=session)
            n += 1
        elif not a and last_session and bound_here:
            of_events.emit("holder_unbound", {"position": name, "previous_session": last_session},
                           position=name, org=org, session=last_session)
            n += 1
    return n
