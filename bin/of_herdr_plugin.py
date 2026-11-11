"""The OF herdr plugin's hooks (herdr-plugin/; owner of_herdr).

The founder, 2026-11-11 ~20:17Z (through of_conductor, msg 01a1cc40): the naming practices that keep the herdr
sidebar readable become part of OF's herdr plugin, applied by themselves when a position starts, not only when
someone runs `of apply`. herdr runs these hooks:

- `startup`: once after the herdr server restores its session (the agents it resumes come back unnamed).
- `agent-detected`: whenever herdr detects an agent in a pane (event `pane.agent_detected`): of spawn, a capacity
  move or resume, or a `claude` started by hand. herdr reports the agent's session a moment after it detects the
  agent, and `of apply` binds a position by that session, so the hook waits for it (at most SESSION_WAIT_S).

Both run `of apply`: the agent's herdr name is its canonical position name; its pane label, tab label (role),
workspace label (organization; one workspace per unit), system, role and summary tokens; misplaced panes moved,
except a move that would move the person's focus (bin/of_focus.py). It is idempotent and takes about a second.

Many agents appear together after a restart, so applies are coalesced: one runs at a time, and requests that arrive
meanwhile cause exactly one more run after it. Output goes to <state>/herdr-plugin.log. Every process here runs with
OF_AUTOMATION=1 (AGENTS.md: automation never touches the owner's SSH agent).
"""

import fcntl
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SESSION_WAIT_S = 30
MAX_RUNS = 5  # in one holder of the lock; requests beyond it wait for the next event
LOG_MAX = 256 * 1024


def _state():
    sys.path.insert(0, HERE)
    import of_events
    os.makedirs(of_events.STATE_DIR, mode=0o700, exist_ok=True)
    return of_events.STATE_DIR


def log(line, state=None):
    path = os.path.join(state or _state(), "herdr-plugin.log")
    try:
        if os.path.getsize(path) > LOG_MAX:
            os.replace(path, path + ".1")
    except OSError:
        pass
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a") as fh:
        fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {line}\n")


def event_pane(env=None):
    """The pane of a pane event: from HERDR_PLUGIN_EVENT_JSON (its data, or the envelope), else HERDR_PANE_ID."""
    env = os.environ if env is None else env
    try:
        ev = json.loads(env.get("HERDR_PLUGIN_EVENT_JSON") or "{}")
    except ValueError:
        ev = {}
    for d in (ev.get("data") if isinstance(ev.get("data"), dict) else {}, ev):
        if isinstance(d, dict) and d.get("pane_id"):
            return d["pane_id"]
    return env.get("HERDR_PANE_ID") or None


def _herdr(*args):
    r = subprocess.run([os.environ.get("HERDR_BIN_PATH") or "herdr", *args], capture_output=True, text=True,
                       timeout=15)
    if r.returncode != 0:
        return None
    try:
        out = json.loads(r.stdout or "{}")
    except ValueError:
        return None
    return out.get("result", out)


def session_of(pane):
    """The agent session herdr reports for a pane, or None."""
    res = _herdr("agent", "get", pane) or {}
    a = res.get("agent") if isinstance(res.get("agent"), dict) else res
    return ((a or {}).get("agent_session") or {}).get("value")


def wait_session(pane, wait_s=SESSION_WAIT_S, step=1.0, get=None, clock=None, sleep=None):
    get, clock, sleep = get or session_of, clock or time.monotonic, sleep or time.sleep
    end = clock() + wait_s
    while True:
        s = get(pane)
        if s or clock() >= end:
            return s
        sleep(step)


def coalesced(run, state=None):
    """Run `run()` now, or have the run in progress run once more after it. Returns the number of runs made here."""
    state = state or _state()
    pending = os.path.join(state, "herdr-plugin.pending")
    with open(pending, "a"):
        pass
    runs = 0
    with open(os.open(os.path.join(state, "herdr-plugin.lock"), os.O_WRONLY | os.O_CREAT, 0o600), "w") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return runs  # the holder sees our pending mark after its run
            try:
                while os.path.exists(pending) and runs < MAX_RUNS:
                    os.unlink(pending)
                    run()
                    runs += 1
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
            if not os.path.exists(pending) or runs >= MAX_RUNS:
                return runs


def of_apply():
    r = subprocess.run([sys.executable, os.path.join(HERE, "of"), "apply"], capture_output=True, text=True,
                       timeout=120, env=dict(os.environ, OF_AUTOMATION="1"))
    out = " | ".join(ln for ln in (r.stdout + r.stderr).splitlines()
                     if ln.strip() and not ln.startswith("no live holder:"))
    log(f"of apply rc={r.returncode}: {out[:600]}")


def main(argv):
    os.environ["OF_AUTOMATION"] = "1"
    what = argv[0] if argv else ""
    if what == "startup":
        log("startup: herdr restored its session")
    elif what == "agent-detected":
        pane = event_pane()
        if not pane:
            log("agent detected: the event names no pane")
        else:
            s = wait_session(pane)
            log(f"agent detected in {pane}: session {s or 'not reported within ' + str(SESSION_WAIT_S) + ' s'}")
    else:
        print("usage: of_herdr_plugin.py startup|agent-detected", file=sys.stderr)
        return 2
    coalesced(of_apply)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
