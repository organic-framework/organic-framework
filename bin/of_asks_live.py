"""Reconcile open asks with the live state of their sessions (every pulse, and when the popup opens).

The events can miss the moment an ask ends: the owner answered in the pane of a session without
hooks, a permission was granted and the tool's events were not recorded, the process died, or an
ask came from imported history. The authority on whether a session still waits is Claude Code
itself (`claude agents --json`, per config dir: status waiting with waitingFor, busy, idle), plus
herdr's `blocked`. For each open ask tied to a session:

  session gone (no process in any config dir)       -> closed, reason orphaned
  permission ask, session not waiting nor blocked   -> closed, reason resolved_elsewhere
  question ask, session busy                        -> closed, reason resolved_elsewhere
  question ask, session idle or waiting             -> kept (it is still waiting for input)

Declared asks (`of ask`) belong to a position, not a session, and are left to `of status`.
An ask filed on another machine (pulled from the coordination feed) is that machine's to reconcile: its session is
never in this machine's `claude agents` (docs/server-several-machines.md §4.3).
A closure is `of.agent_question_answered` {ask_id, ask_ids, via: reconcile, reason, by: of}.
A session moved between accounts keeps its session id, so its asks follow it.
"""

import glob
import json
import os
import subprocess

TIMEOUT_S = 20


def config_dirs():
    return sorted(d for d in glob.glob(os.path.expanduser("~/.claude*")) if os.path.isdir(d))


def live_sessions(dirs=None, run=subprocess.run):
    """{session id: (status, waitingFor)} across Claude config dirs, or None if any listing failed
    (then nothing is closed as orphaned)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERDR_")}  # never re-bind a pane
    env["SSH_AUTH_SOCK"] = ""
    from concurrent.futures import ThreadPoolExecutor

    def one(d):  # the config dirs are listed in parallel (each call takes 0.15-0.2 s)
        try:
            r = run(["claude", "agents", "--json"], env=dict(env, CLAUDE_CONFIG_DIR=d), capture_output=True,
                    text=True, timeout=TIMEOUT_S)
            return json.loads(r.stdout or "[]") if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
    dirs = list(dirs or config_dirs())
    with ThreadPoolExecutor(max_workers=max(1, len(dirs))) as ex:
        listings = list(ex.map(one, dirs))
    if any(items is None for items in listings):
        return None
    out = {}
    for items in listings:
        for x in items:
            if x.get("sessionId"):
                out[x["sessionId"]] = (x.get("status") or x.get("state"), x.get("waitingFor"))
    return out


def decide(ask, live, blocked_sessions):
    """The reason to close this open ask now, or None to keep it."""
    sid = ask.get("session_id")
    kind = ask.get("kind") or ""
    if not sid or kind.startswith("question (declared") or ask.get("task") or kind == "task":  # a view row: kind task
        return None  # declared asks and the founder's tasks wait on him, whatever the asker's session does
    if sid in blocked_sessions:
        return None
    if sid not in live:
        return "orphaned"
    status, _ = live[sid]
    if ask.get("kind") == "permission":
        return None if status in ("waiting", "blocked") else "resolved_elsewhere"
    return "resolved_elsewhere" if status == "busy" else None


def reconcile(asks, live, blocked_sessions=(), emit=None):
    """Close the open asks whose sessions no longer wait. Returns [(ask_id, reason)]."""
    if live is None:
        return []
    done = []
    for a in asks:
        if a.get("state") != "open":
            continue
        why = decide(a, live, set(blocked_sessions))
        if why:
            if emit:
                emit("agent_question_answered", {"ask_id": a["event_id"], "ask_ids": [a["event_id"]],
                                                 "via": "reconcile", "reason": why, "by": "of"},
                     position=a.get("position"), session=a.get("session_id"))
            done.append((a["event_id"], why))
    return done


def run(conn, positions=None, agents=None, bind=None, log=print, view=False):
    """One reconcile pass over the store (pulse, popup). view: the asks as the listener's view holds them (a few ms),
    and no pass when the listener is not fresh, for the questions screen: its pass projected the whole log, 16 s of
    CPU and 8 s of reads on 2026-11-12, and the cards waited behind it (the founder: the screen opened in 10 to 30 s;
    of_conductor msg 01a1d09c-4562). The pulse reconciles every 5 minutes either way. The view lags the log by up to a
    listener tick, so an ask answered a moment ago may still read open and get a reconcile close after its answer:
    harmless, since of_asks.project changes only an open ask's state (of_review on PR 98)."""
    import of_asks
    import of_events
    if view:
        import of_listen
        if not of_listen.fresh(conn):
            return []
        asks = of_listen.read_asks(conn)
    else:
        asks = of_asks.project(of_asks.load(conn))
    try:
        import of_coord
        remote = {eid for (eid,) in conn.execute("SELECT event_id FROM event WHERE name = 'of.agent_question' "
                                                  "AND raw_ref LIKE ?", (of_coord.RAW_REF + "%",))}
    except Exception:  # noqa: BLE001
        remote = set()
    asks = [a for a in asks if a.get("event_id") not in remote]
    blocked = set()
    if agents is not None:
        blocked = {(a.get("agent_session") or {}).get("value") for a in agents if a.get("agent_status") == "blocked"}
    done = reconcile(asks, live_sessions(), blocked, of_events.emit)
    if done:
        log(f"asks reconciled: {len(done)} closed (" + ", ".join(sorted({w for _, w in done})) + ")")
    return done
