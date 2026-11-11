"""The OF herdr plugin's hooks (herdr-plugin/; owner of_herdr).

The founder, 2026-11-11 ~20:17Z (through of_conductor, msg 01a1cc40): the naming practices that keep the herdr
sidebar readable become part of OF's herdr plugin, applied by themselves when a position starts, not only when
someone runs `of apply`. herdr runs these hooks:

- `startup`: once after the herdr server restores its session (the agents it resumes come back unnamed).
- `asks` (a pane entrypoint, not a hook): the owner's questions screen, opened as a popup by
  `herdr plugin pane open --plugin organic-framework --entrypoint asks` from a key, a panel's button or a script.
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


PLUGIN_ID = "organic-framework"


# The Agents sidebar: whatever needs the owner first (the founder, ~21:25Z via of_conductor, translated: "the sidebar
# view must go into the release too"). First the agents with an open question to him (the `ask` pane token, bin/of_herdr_asks.py; a missing
# token sorts after present ones), then herdr's attention (4 a dialog or permission, 3 a finished turn not yet seen,
# 2 working, 1 idle and seen: src/app/api_helpers.rs tab_attention_priority), then the layout order. A question he
# has seen but not answered still comes first: attention alone would put it below the working agents. No filter:
# every agent stays listed. herdr keeps a view until the server exits, so the startup hook sets it again (herdr 0.9.5
# docs, socket API, "Agent view queries").
VIEW = {
    "label": "OF: needs you first",
    "sort": [{"field": {"token": "ask"}, "order": "asc"},
             {"field": "attention", "order": "desc"},
             {"field": "workspace_order", "order": "asc"},
             {"field": "tab_order", "order": "asc"},
             {"field": "pane_order", "order": "asc"}],
}


def herdr_request(method, params, sock_path=None, timeout=5.0):
    """One request on herdr's API socket (one JSON line each way). Returns the reply, or {"error": ...}."""
    import socket
    sock_path = sock_path or os.environ.get("HERDR_SOCKET_PATH")
    if not sock_path:
        return {"error": {"code": "no_socket", "message": "HERDR_SOCKET_PATH is not set"}}
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(timeout)
        c.connect(sock_path)
        c.sendall((json.dumps({"id": f"of-herdr-plugin:{time.time_ns()}", "method": method, "params": params})
                   + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = c.recv(65536)
            if not chunk:
                break
            buf += chunk
        c.close()
        return json.loads(buf.decode() or "{}")
    except (OSError, ValueError) as e:
        return {"error": {"code": type(e).__name__, "message": str(e)[:200]}}


def set_view(request=None):
    """Install the OF Agents view (VIEW). Returns (True, None) or (False, reason)."""
    r = (request or herdr_request)("agent.view.set", dict(VIEW, source=f"plugin:{PLUGIN_ID}"))
    err = r.get("error")
    return (False, (err or {}).get("code") or str(err)) if err else (True, None)


KEEP_FIRST_S, KEEP_MAX_S, KEEP_STEADY_S = 2, 60, 60


def keep(cmd, name="the panel", run=None, sleep=None, clock=None, say=None, max_runs=None):
    """Run a pinned pane's program again whenever it exits, so a crash or a half-built update never leaves the pane at a
    shell (the founder's questions panel died at 21:23Z when the build it ran from was rebased). It waits
    KEEP_FIRST_S, doubling up to KEEP_MAX_S while the program keeps failing fast, and back to KEEP_FIRST_S after a run
    longer than KEEP_STEADY_S, saying so in the pane. Ctrl-C stops it. Not for a popup: a popup would never close."""
    from of_locale import tr
    run, sleep, clock = run or subprocess.call, sleep or time.sleep, clock or time.monotonic
    say = say or (lambda text: print(text, flush=True))
    delay, runs = KEEP_FIRST_S, 0
    try:
        while max_runs is None or runs < max_runs:
            started = clock()
            code = run(cmd)
            runs += 1
            if clock() - started > KEEP_STEADY_S:
                delay = KEEP_FIRST_S
            say("\n" + tr("{name} stopped (exit {code}); it starts again in {s} s. Ctrl-C stops it.", name=tr(name),
                           code=code, s=delay))
            sleep(delay)
            delay = min(delay * 2, KEEP_MAX_S)
    except KeyboardInterrupt:
        return 130
    return 0


def herdr_config_path(env=None):
    """herdr's config.toml as herdr finds it: $XDG_CONFIG_HOME/herdr, else ~/.config/herdr (herdr 0.9.5,
    src/config/io.rs config_dir; on macOS and Linux)."""
    env = os.environ if env is None else env
    base = env.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "herdr", "config.toml")


QUESTIONS_KEY = "prefix+a"


def configure(of_home=None, python=None, path=None, run=None, backups=None, now=None):
    """Give a person's herdr OF's two config pieces, for `of init` beside install(): the key that opens the owner's
    questions screen (prefix+a, a 90% x 85% popup running `of asks --watch`) and the red per-workspace question counter
    in the Spaces sidebar (the `$asks` token, set by bin/of_herdr_asks.py). A plugin cannot set either: herdr takes keys
    and sidebar rows only from config.toml. Idempotent; it never replaces a binding of prefix+a or sidebar rows the
    person already has, and says so. It backs the file up first, runs `herdr config check`, puts the backup back if the
    check fails, and reloads a running server. Returns (True, [notes]) or (False, reason)."""
    import shutil
    import tomllib
    run = run or subprocess.run
    of_home = os.path.abspath(of_home or os.path.join(HERE, ".."))
    python = python or sys.executable
    path = path or herdr_config_path()
    text = open(path).read() if os.path.exists(path) else ""
    try:
        cfg = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return False, f"{path} is not valid TOML ({e}); left alone"
    notes, add = [], []
    keys = [k for k in ((cfg.get("keys") or {}).get("command") or []) if k.get("key") == QUESTIONS_KEY]
    if not keys:
        log_file = os.path.join(os.path.expanduser("~"), ".local", "state", "of", "asks-popup.err")
        command = (f"sh -c 'env OF_AUTOMATION=1 {python} {of_home}/bin/of asks --watch 2>>{log_file}; rc=$?; "
                   f"if [ $rc -ne 0 ]; then echo; echo \\\"of asks exited with $rc; see {log_file}. Press Enter."
                   f"\\\"; read x; fi'")
        add.append("\n# Organic Framework: the owner's questions screen (of_herdr_plugin.configure, of init).\n"
                   f"[[keys.command]]\nkey = \"{QUESTIONS_KEY}\"\ntype = \"popup\"\ncommand = \"{command}\"\n"
                   "width = \"90%\"\nheight = \"85%\"\n")
        notes.append(f"{QUESTIONS_KEY} opens the questions screen")
    elif "asks --watch" in str(keys[0].get("command")):
        notes.append(f"{QUESTIONS_KEY} already opens the questions screen")
    else:
        notes.append(f"{QUESTIONS_KEY} is bound to something else; left alone (bind the questions screen by hand)")
    spaces = ((cfg.get("ui") or {}).get("sidebar") or {}).get("spaces")
    if spaces is None:
        add.append("\n# Organic Framework: the number of open questions per workspace, red, hidden when none.\n"
                   "[ui.sidebar.spaces]\nrows = [[\"state_icon\", \"workspace\", { token = \"$asks\", fg = \"#f55\", "
                   "bold = true, rules = [{ equals = \"\", hide = true }] }], [\"branch\", \"git_status\"]]\n")
        notes.append("the sidebar shows the open questions per workspace")
    elif "$asks" in json.dumps(spaces):
        notes.append("the sidebar already shows the question counter")
    else:
        notes.append("the sidebar has rows of its own; left alone (add the $asks token by hand)")
    if not add:
        return True, notes
    backup = None
    if text:
        bdir = backups or os.path.join(os.path.expanduser("~"), ".local", "state", "of", "herdr-config-backups")
        os.makedirs(bdir, exist_ok=True)
        backup = os.path.join(bdir, time.strftime("config.toml.%Y%m%dT%H%M%SZ", time.gmtime(now)) + ".before-of-init")
        shutil.copy2(path, backup)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text + ("" if not text or text.endswith("\n") else "\n") + "".join(add))
    herdr = os.environ.get("OF_HERDR", "herdr")
    try:
        chk = run([herdr, "config", "check"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        chk = None
        notes.append(f"herdr config check did not run ({type(e).__name__})")
    if chk is not None and (chk.returncode != 0 or "ok" not in (chk.stdout or "").lower()):
        if backup:
            shutil.copy2(backup, path)
        else:
            os.unlink(path)
        return False, "herdr config check refused the change; the file is as it was: " + \
            (chk.stderr or chk.stdout or "").strip()[:300]
    try:
        run([herdr, "server", "reload-config"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass  # no server running: the next start reads the file
    if backup:
        notes.append(f"backup: {backup}")
    return True, notes


def install(of_home=None, run=None):
    """Link OF's herdr plugin for this user (herdr registers it in every session) and record OF's home in the plugin's
    config dir (`of_home`, which run.sh reads), for `of init` and for a person installing by hand. Idempotent.
    Returns (True, note) or (False, reason). herdr must be installed; no server needs to run."""
    run = run or subprocess.run
    of_home = os.path.abspath(of_home or os.path.join(HERE, ".."))
    folder = os.path.join(of_home, "herdr-plugin")
    if not os.path.isfile(os.path.join(folder, "herdr-plugin.toml")):
        return False, f"no plugin folder at {folder}"
    herdr = os.environ.get("OF_HERDR", "herdr")
    try:
        listed = run([herdr, "plugin", "list"], capture_output=True, text=True, timeout=15)
        if f"[local:{folder}]" not in (listed.stdout or ""):
            r = run([herdr, "plugin", "link", folder], capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                return False, (r.stderr or r.stdout or "herdr plugin link failed").strip()[:300]
        d = run([herdr, "plugin", "config-dir", PLUGIN_ID], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"herdr: {type(e).__name__}: {e}"[:300]
    cfg = (d.stdout or "").strip().splitlines()[-1:] if d.returncode == 0 else []
    if not cfg:
        return False, "herdr gave no config dir for the plugin"
    os.makedirs(cfg[0], exist_ok=True)
    with open(os.path.join(cfg[0], "of_home"), "w") as fh:
        fh.write(of_home + "\n")
    return True, f"linked {folder}; of_home {of_home}"


def open_questions(of_path=None, run=None):
    """Open the owner's questions screen as a popup over everything: the plugin's `asks` entrypoint, for a panel's
    button or a script. of_path: the bin/of to run instead of this checkout's (a trial build). Returns (True, None),
    or (False, reason): herdr's error code, `ui_busy` while another popup, Settings or Copy mode is open (checked in
    asks-screen-v0), or its message."""
    run = run or subprocess.run
    argv = [os.environ.get("OF_HERDR", "herdr"), "plugin", "pane", "open", "--plugin", PLUGIN_ID, "--entrypoint", "asks"]
    if of_path:
        argv += ["--env", f"OF_ASKS_OF={of_path}"]
    try:
        r = run(argv, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"{type(e).__name__}: {e}"[:200]
    err = {}
    for text in (r.stdout, r.stderr):  # herdr prints an error's JSON on stderr
        try:
            err = (json.loads(text or "{}") or {}).get("error") or err
        except ValueError:
            pass
    if r.returncode == 0 and not err:
        return True, None
    return False, err.get("code") or (r.stderr or r.stdout or f"exit {r.returncode}").strip()[:200]


def main(argv):
    os.environ["OF_AUTOMATION"] = "1"
    what = argv[0] if argv else ""
    if what == "startup":
        ok, why = set_view()
        log("startup: herdr restored its session; Agents view " + ("set" if ok else f"not set: {why}"))
    elif what == "agent-detected":
        pane = event_pane()
        if not pane:
            log("agent detected: the event names no pane")
        else:
            s = wait_session(pane)
            log(f"agent detected in {pane}: session {s or 'not reported within ' + str(SESSION_WAIT_S) + ' s'}")
    elif what == "view":
        ok, why = set_view()
        print("Agents view set: " + VIEW["label"] if ok else f"Agents view not set: {why}",
              file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1
    elif what == "keep":  # keep [--name NAME] -- COMMAND...
        rest = argv[1:]
        name = "the panel"
        if rest[:1] == ["--name"] and len(rest) > 1:
            name, rest = rest[1], rest[2:]
        if rest[:1] == ["--"]:
            rest = rest[1:]
        if not rest:
            print("usage: of_herdr_plugin.py keep [--name NAME] -- COMMAND...", file=sys.stderr)
            return 2
        return keep(rest, name=name)
    elif what == "configure":
        ok, notes = configure(argv[1] if len(argv) > 1 else None)
        print("; ".join(notes) if ok else f"not configured: {notes}", file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1
    elif what == "install":
        ok, note = install(argv[1] if len(argv) > 1 else None)
        print(("" if ok else "not installed: ") + note, file=sys.stdout if ok else sys.stderr)
        return 0 if ok else 1
    elif what == "asks":
        # The questions screen as the plugin's popup ([[panes]] asks in herdr-plugin.toml). Any process can open it:
        # herdr plugin pane open --plugin organic-framework --entrypoint asks (the manifest's placement is popup).
        # OF_ASKS_OF names another bin/of to run, for a trial build (passed with --env).
        of = os.environ.get("OF_ASKS_OF") or os.path.join(HERE, "of")
        os.execv(sys.executable, [sys.executable, of, "asks", "--watch"])
    else:
        print("usage: of_herdr_plugin.py startup|agent-detected|asks|view|keep|install|configure [OF_HOME]", file=sys.stderr)
        return 2
    coalesced(of_apply)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
