"""Append events to the machine's Organic Framework event log (docs/events.md, schema v1)."""

import fcntl
import json
import os
import socket
import time
import uuid

STATE_DIR = os.environ.get("OF_STATE") or os.path.expanduser("~/.local/state/of")
CONFIG_DIR = os.environ.get("OF_CONFIG_DIR") or os.path.expanduser("~/.config/of")


def config_path(name):
    """A file in the OF config dir: ~/.config/of, or $OF_CONFIG_DIR (tests, labs, a second profile)."""
    return os.path.join(CONFIG_DIR, name)
SCHEMA = 1


def _state_dir():
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    return STATE_DIR


def machine_id():
    path = os.path.join(_state_dir(), "machine_id")
    try:
        with open(path) as fh:
            return fh.read().strip()
    except FileNotFoundError:
        mid = str(uuid.uuid4())
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(mid + "\n")
        return mid


def _uuid7():
    u7 = getattr(uuid, "uuid7", None)
    return str(u7() if u7 else uuid.uuid4())


def now_iso(ts=None):
    ts = time.time() if ts is None else ts
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + f".{int(ts * 1000) % 1000:03d}Z"


def record(kind, payload, *, position=None, org=None, session=None, source="of", extra=None):
    """Build one event record. `kind` is the bare kind; event.name gets the of. prefix."""
    rec = {
        "of.schema": SCHEMA,
        "of.event.id": _uuid7(),
        "timestamp": now_iso(),
        "event.name": f"of.{kind}",
        "host.id": machine_id(),
        "host.name": socket.gethostname(),
        "gen_ai.conversation.id": session,
        "gen_ai.agent.name": position,
        "of.org": org,
        "of.source": source,
        # JSON-safe: a value JSON cannot hold (a YAML date, a set) is written as text, never dropped
        "of.payload": json.loads(json.dumps(payload, ensure_ascii=False, default=str)),
    }
    rec.update(extra or {})
    if not rec.get("of.org"):  # resolve from the position, else from the working directory
        try:
            import of_hook
            rec["of.org"] = of_hook.resolve_org(position, rec.get("of.cwd") or os.getcwd() if source != "of" else
                                                rec.get("of.cwd"))
        except Exception:
            pass
    run = os.environ.get("OF_LAB_RUN_ID")
    if run:  # a lab run: its own organization of the tenant `lab` (docs/lab.md)
        rec["of.lab.run_id"] = run
        rec["of.org"] = f"lab-{run}"
    return rec


HOOK_TIMEOUT = None  # set by `of hook`: wait at most this long for the database, then spool
_CONN = {}


def _conn():
    import of_store
    import threading
    key = (of_store.db_path(), HOOK_TIMEOUT, threading.get_ident())  # SQLite objects stay in their thread
    if key not in _CONN:
        _CONN[key] = of_store.connect(timeout=HOOK_TIMEOUT or 10.0)
    return _CONN[key]


def append(rec, raw_ref=None):
    """Write one record to the local store (of.db); if it is busy or broken, spool it to disk
    for the ingest worker. Never raises."""
    try:
        import of_store
        of_store.add_event(_conn(), rec, raw_ref)
        return True
    except Exception:
        pass
    try:
        import of_store
        of_store.spool_append("events.jsonl", rec)
        return True
    except Exception:
        ok = append_jsonl(rec)
        if not ok:  # nowhere to write: say so where the owner looks, instead of losing it silently
            try:
                with open(os.path.join(_state_dir(), "hook-errors.log"), "a") as fh:
                    fh.write(f"{now_iso()} ERROR event {rec.get('event.name')} {rec.get('of.event.id')} not written\n")
            except Exception:
                pass
        return ok


def append_jsonl(rec):
    """The fallback spool: one JSON line under an exclusive lock. Never raises."""
    try:
        path = os.path.join(_state_dir(), "events.jsonl")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            fh.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
        return True
    except Exception:
        return False


ASK_CLOSURES = ("agent_question_answered", "ask_withdrawn", "ask_escalated")


def ask_org(ask_id):
    """The organization of an ask, from its own event (None if unknown or the store is busy)."""
    try:
        row = _conn().execute("SELECT org FROM event WHERE event_id = ?", (ask_id,)).fetchone()
        return row[0] if row else None
    except Exception:  # noqa: BLE001
        return None


def emit(kind, payload, event_id=None, **kw):
    """Write one event. Returns its id, or None if it could not be written anywhere.
    A closure of an ask carries the ask's organization when the caller gave none (reconcile, the CLI and
    the popup close asks of any organization; a per-organization model must see the closure).
    event_id: a fixed id (stable_id) for an event that more than one machine may write about the same thing; the
    store keeps the first copy and the coordination feed drops the rest (docs/server-several-machines.md §4.3)."""
    if kind in ASK_CLOSURES and not kw.get("org") and isinstance(payload, dict):
        aid = payload.get("ask_id") or (payload.get("ask_ids") or [None])[0]
        if aid:
            kw["org"] = ask_org(aid)
    rec = record(kind, payload, **kw)
    if event_id:
        rec["of.event.id"] = event_id
    return rec["of.event.id"] if append(rec) else None


STABLE_NS = uuid.UUID("5b0f6a3e-2f1c-4c8e-9a43-0f0c00000000")


def stable_id(*parts):
    """The same id for the same fact on every machine: uuid5 of its parts."""
    return str(uuid.uuid5(STABLE_NS, ":".join(str(p) for p in parts)))
