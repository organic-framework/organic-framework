"""of desk: the desk and its router (docs/desk.md).

The desk is one herdr workspace (label `desk`) with one tab of N slots in splits. It shows the panes the founder put
there and the agents of the case he works on now (bin/of_case.py, `of.case_inferred`). The router places case members
only: it never moves his placements, the focused pane, an agent he typed to in the last minutes, or what it placed a
moment ago, and it never brings an agent in because it finished a turn or has an open ask (2026-11-09: the desk that
followed states evicted the sessions he was reading).

The planner (`plan_desk`) is pure. The watcher subscribes to herdr events over the socket (no polling), batches them,
and reconciles; a slow heartbeat picks up a new case.
"""

import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import time
from of_locale import tr

import of_events

STATE_DIR = of_events.STATE_DIR
CONFIG_FILE = os.environ.get("OF_DESK_CONFIG") or of_events.config_path("desk.yaml")
DEFAULTS = {
    "label": "desk",          # the desk workspace label
    "slots": 6,               # panes on the desk
    "case_min": 0.3,          # a case member below this score is not brought in
    "case_margin": 0.2,       # a newcomer replaces a router placement only when it scores this much higher
    "case_decay": [1.0, 0.6, 0.4],  # the weight of the current case, the second, the third
    "case_max_age_s": 6 * 3600,     # an older case fills nothing
    "touch_s": 900,           # an agent he typed to this recently is never moved and scores at least 1.0
    "min_stay_s": 600,        # what the router placed stays at least this long
    "removed_s": 7200,        # what he removed stays off at least this long (and while the case keeps its title)
    "notify_again_s": 600,    # one dialog notification per agent per this long
    "heartbeat_s": 30,        # re-check without events (a new case arrives this way)
    "batch_s": 1.0,           # wait for this much quiet before acting on events
    "fallback_workspace": "agents",  # for agents with no position
}


# ------------------------------------------------------------------ state

def load_config():
    cfg = dict(DEFAULTS)
    try:
        import yaml
        with open(CONFIG_FILE) as fh:
            cfg.update(yaml.safe_load(fh) or {})
    except FileNotFoundError:
        pass
    return cfg


def _state_path():
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    return os.path.join(STATE_DIR, "desk.json")


class State:
    """desk.json: pinned names, last status and since per pane, notified asks. Locked while open."""

    def __enter__(self):
        fd = os.open(_state_path(), os.O_RDWR | os.O_CREAT, 0o600)
        self.fh = os.fdopen(fd, "r+")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        self.fh.seek(0)
        text = self.fh.read()
        self.data = json.loads(text) if text.strip() else {}
        self.data.setdefault("pinned", [])
        self.data.setdefault("since", {})      # pane -> [status, ts]
        self.data.setdefault("notified", {})   # pane -> ts of the state it was notified for
        return self.data

    def __exit__(self, *exc):
        self.fh.seek(0)
        self.fh.truncate()
        json.dump(self.data, self.fh, indent=1, sort_keys=True)
        self.fh.flush()
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def update_since(since, agents, now):
    """Record when each pane entered its current status. Returns the updated map."""
    live = {}
    for a in agents:
        pane, status = a["pane_id"], a.get("agent_status") or "unknown"
        old = since.get(pane)
        live[pane] = old if old and old[0] == status else [status, now]
    return live


# ---------------------------------------------------------------- planner

def scores(cases, cfg):
    """name -> (score, case title, why): the best of its weight in each case times that case's decay."""
    decay = list(cfg.get("case_decay") or [1.0])
    out = {}
    for i, c in enumerate(cases or []):
        f = decay[i] if i < len(decay) else 0.0
        for m in c.get("members") or []:
            s = round(float(m.get("weight") or 0) * f, 3)
            if s > out.get(m["position"], (-1,))[0]:
                out[m["position"]] = (s, c.get("title"), m.get("why"))
    return out


def plan_desk(desk, off, cases, mine, placed, removed, touched, now, cfg):
    """Return the actions of one reconcile (docs/desk.md, "The router").

    desk, off: views {pane, name, status, dialog, focused}. cases: the latest inferred cases, current first.
    mine: names on the desk he placed or pinned. placed: name -> ts the router placed it. removed: name -> {case, at}
    he took off the desk. touched: names he typed to in the last touch_s: their panes never move, on the desk or off
    it (he may be typing there right now), and neither does the focused pane.
    Actions: ("add", incoming), ("swap", incoming, evicted), ("notify", agent in a dialog off the desk).
    """
    sc = scores(cases, cfg)
    title = (cases or [{}])[0].get("title")

    def score(d):
        return sc.get(d["name"], (0.0,))[0]

    def kept_off(name):
        r = removed.get(name)
        return bool(r) and (r.get("case") == title or now - r.get("at", 0) < cfg["removed_s"])

    wanted = [d for d in off if score(d) >= cfg["case_min"] and not kept_off(d["name"])
              and not d.get("focused") and d["name"] not in touched]
    wanted.sort(key=lambda d: (-score(d), d["name"]))
    movable = [d for d in desk if d["name"] in placed and d["name"] not in mine and not d.get("focused")
               and d["name"] not in touched and now - placed[d["name"]] >= cfg["min_stay_s"]]
    movable.sort(key=lambda d: (score(d), -(now - placed[d["name"]]), d["name"]))
    free = cfg["slots"] - len(desk)
    actions = []
    for w in wanted:
        if free > 0:
            actions.append(("add", w))
            free -= 1
        elif movable and score(w) >= score(movable[0]) + cfg["case_margin"] - 1e-9:
            actions.append(("swap", w, movable.pop(0)))
        else:
            break  # the rest score lower still
    on = {d["name"] for d in desk} | {a[1]["name"] for a in actions}
    actions += [("notify", d) for d in off if d.get("dialog") and d["name"] not in on]
    return actions


# ----------------------------------------------------------------- herdr

def _herdr(*args):
    out = subprocess.run(["herdr", *args], capture_output=True, text=True, timeout=20)
    if out.returncode != 0:
        raise RuntimeError(f"herdr {' '.join(args)}: {(out.stderr or out.stdout).strip()[:300]}")
    return json.loads(out.stdout)["result"] if out.stdout.strip().startswith("{") else None


class World:
    """One snapshot of herdr plus the registry, enough to plan and act."""

    def __init__(self, positions, bind, cfg):
        self.cfg = cfg
        self.agents = _herdr("agent", "list")["agents"]
        self.workspaces = _herdr("workspace", "list")["workspaces"]
        self.tabs = _herdr("tab", "list")["tabs"]
        pairs, _, _ = bind(positions, self.agents)
        self.pos_by_pane = {a["pane_id"]: p for p, a in pairs}
        desk = [w for w in self.workspaces if w.get("label") == cfg["label"]]
        self.desk_ws = desk[0]["workspace_id"] if desk else None
        desk_tabs = sorted((t for t in self.tabs if t["workspace_id"] == self.desk_ws),
                           key=lambda t: -t.get("pane_count", 0))
        self.desk_tab = desk_tabs[0]["tab_id"] if desk_tabs else None

    def view(self, a):
        """dialog: herdr's `blocked` (a permission or a choice in the pane). An open `of ask` is not one: it is in
        the founder's popup already, and it brought back an agent he had just removed (2026-11-09)."""
        p = self.pos_by_pane.get(a["pane_id"])
        status = a.get("agent_status") or "unknown"
        return {"pane": a["pane_id"], "name": p["name"] if p else (a.get("name") or a["pane_id"]),
                "status": status, "dialog": status == "blocked", "ask": bool((a.get("tokens") or {}).get("ask")),
                "position": p, "tab": a.get("tab_id"), "focused": bool(a.get("focused"))}

    def split(self):
        desk = [self.view(a) for a in self.agents if a.get("tab_id") == self.desk_tab]
        off = [self.view(a) for a in self.agents if a.get("tab_id") != self.desk_tab]
        return desk, off

    def ordered_desk(self):
        """Desk panes in reading order (top to bottom, left to right)."""
        if not self.desk_tab:
            return []
        desk, _ = self.split()
        by_pane = {d["pane"]: d for d in desk}
        if not desk:
            return []
        layout = _herdr("pane", "layout", "--pane", desk[0]["pane"])["layout"]
        rects = {r["pane_id"]: r["rect"] for r in layout["panes"]}
        order = sorted(by_pane, key=lambda p: (rects.get(p, {}).get("y", 0), rects.get(p, {}).get("x", 0)))
        for p in order:
            by_pane[p]["rect"] = rects.get(p)
        return [by_pane[p] for p in order]

    def home_workspace(self, d):
        p = d.get("position")
        return (p or {}).get("_home_ws") or (p or {}).get("_workspace") or self.cfg["fallback_workspace"]


# ---------------------------------------------------------------- actions

def _run(args, log):
    log("herdr " + " ".join(args))
    import of_focus
    why = of_focus.block(args)
    if why:
        log(f"  held back: {why}")
        return False
    out = subprocess.run(["herdr", *args], capture_output=True, text=True, timeout=20)
    if out.returncode != 0:
        log("  failed: " + (out.stderr or out.stdout).strip()[:300])
        return False
    return True


def _label(d):
    p = d.get("position")
    return f"■ {p['name']} ■" if p else None


def bring_in(world, incoming, evicted, log):
    """Move `incoming` into the desk tab, in place of `evicted` (or into the largest slot)."""
    if evicted:
        target, split = evicted["pane"], "right"
    else:
        slots = world.ordered_desk()
        if slots:
            big = max(slots, key=lambda s: (s["rect"] or {}).get("width", 0) * (s["rect"] or {}).get("height", 0))
            r = big["rect"] or {"width": 2, "height": 1}
            target, split = big["pane"], ("right" if r["width"] >= 2 * r["height"] else "down")
        else:
            target, split = None, "right"
    if not world.desk_tab:
        ok = _run(["pane", "move", incoming["pane"], "--new-workspace", "--label", world.cfg["label"],
                   "--tab-label", world.cfg["label"], "--no-focus"], log)
    else:
        args = ["pane", "move", incoming["pane"], "--tab", world.desk_tab, "--split", split, "--no-focus"]
        if target:
            args[5:5] = ["--target-pane", target]
        ok = _run(args, log)
    if ok and evicted:
        ok = send_home(world, evicted, log)
    for d in (incoming, evicted):
        if d and _label(d):
            _run(["pane", "rename", d["pane"], _label(d)], log)
    return ok


def send_home(world, d, log):
    """Back to its place. A unit position goes to its unit's workspace, then `of apply` puts it into
    its system's tabs in the balanced layout (at most 4 per tab); others get a tab named by role."""
    p = d.get("position") or {}
    ws_label = world.home_workspace(d)
    label = f"{p['system']}·home" if p.get("_unit") and p.get("system") else (p.get("role") or d["name"])
    ws = [w for w in world.workspaces if w.get("label") == ws_label]
    if ws:
        ok = _run(["pane", "move", d["pane"], "--new-tab", "--workspace", ws[0]["workspace_id"],
                   "--label", label, "--no-focus"], log)
    else:
        ok = _run(["pane", "move", d["pane"], "--new-workspace", "--label", ws_label,
                   "--tab-label", label, "--no-focus"], log)
    if ok and p.get("_unit"):
        of_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "of")
        subprocess.Popen([sys.executable, of_path, "apply"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True,
                         env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
    return ok


def _emit(action, d, **extra):
    p = d.get("position") or {}
    of_events.emit("attention_routed", {"action": action, "pane": d["pane"], "status": d["status"], **extra},
                   position=p.get("name"), org=p.get("_orgid"))


SEED_S = 2 * 3600  # the first run: what the old router brought in this recently is the router's


def seed_placed(conn, names, now):
    """The first run after the case desk: what the old router brought in within SEED_S and nobody moved since is the
    router's. The rest on the desk is the founder's: he placed it, or kept it there (platform_owner, brought in on
    2026-11-06 and kept for three days)."""
    import of_case
    out = {}
    for n in names:
        r = conn.execute("SELECT ts, json_extract(payload, '$.action'), json_extract(payload, '$.by') FROM event "
                         "WHERE name = 'of.attention_routed' AND position = ? AND json_extract(payload, '$.action') "
                         "IN ('bring_in', 'evict') ORDER BY ts DESC LIMIT 1", (n,)).fetchone()
        if r and r[1] == "bring_in" and r[2] != "owner" and now - of_case._ts(r[0]) <= SEED_S:
            out[n] = of_case._ts(r[0])
    return out


def track(st, on, title, now, conn=None):
    """Update the router's placements against what is on the desk now: a placement that left the desk without the
    router was removed by the founder. Returns (mine, placed, removed)."""
    if "placed" not in st:
        st["placed"] = seed_placed(conn, on, now) if conn is not None else {}
    placed, removed = st["placed"], st.setdefault("removed", {})
    for n in [n for n in placed if n not in on]:
        removed[n] = {"case": title, "at": now}
        del placed[n]
    for n in [n for n in removed if n in on or now - removed[n].get("at", 0) > 86400]:
        del removed[n]  # back on the desk (he put it there), or a day old
    mine = (set(on) - set(placed)) | set(st.get("pinned") or [])
    return mine, placed, removed


def _notify_dialog(d, log):
    try:
        sound = __import__("of_pulse").herdr_sound("request")
    except Exception:  # noqa: BLE001
        sound = None
    _run(["notification", "show", tr("{name} waits for you in a dialog", name=d['name']),
          "--body", tr("A permission or a choice in its pane. Open it: of go {name}", name=d['name'])]
         + (["--sound", sound] if sound else []), log)


def reconcile(positions, bind, log, cfg=None, conn=None):
    import of_case
    import of_store
    cfg = cfg or load_config()
    now = time.time()
    world = World(positions, bind, cfg)
    own = conn is None
    conn = conn or of_store.connect()
    try:
        latest = of_case.latest(conn, max_age_s=cfg["case_max_age_s"], now=now) or {}
        cases = latest.get("cases") or []
        title = cases[0]["title"] if cases else None
        touched = {p["position"] for p in of_case.founder_prompts(conn, of_case._iso(now - cfg["touch_s"]),
                                                                  of_case._iso(now))}
        with State() as st:
            st["since"] = update_since(st["since"], world.agents, now)
            for k in ("done_pending", "done_notified_at"):
                st.pop(k, None)  # the old router's summary of finished agents
            desk, off = world.split()
            try:  # pinned views (of view) are in sight already: never brought onto the desk
                import of_views
                pinned_views = of_views.pinned_positions()
            except Exception:
                pinned_views = set()
            off = [d for d in off if (d.get("position") or {}).get("name") not in pinned_views]
            if not world.desk_tab:
                log("no desk workspace yet")
            mine, placed, removed = track(st, {d["name"] for d in desk}, title, now, conn)
            actions = plan_desk(desk, off, cases, mine, placed, removed, touched, now, cfg)
            sc = scores(cases, cfg)
            for act in actions:
                incoming = act[1]
                if act[0] == "notify":
                    key = f"dialog@{st['since'].get(incoming['pane'], ['', 0])[1]}"
                    if st["notified"].get(incoming["pane"]) == key:
                        continue
                    st["notified"][incoming["pane"]] = key
                    last = st.setdefault("notified_at", {}).get(incoming["name"], 0)
                    if now - last < cfg.get("notify_again_s", 600):
                        continue  # its state flipped again: he was told minutes ago
                    st["notified_at"][incoming["name"]] = now
                    _emit("notify", incoming)
                    _notify_dialog(incoming, log)
                    continue
                evicted = act[2] if act[0] == "swap" else None
                s, _, why = sc.get(incoming["name"], (0, None, None))
                if bring_in(world, incoming, evicted, log):
                    placed[incoming["name"]] = now
                    _emit("bring_in", incoming, replaced=evicted and evicted["name"], case=title, score=s, why=why)
                    if evicted:
                        placed.pop(evicted["name"], None)
                        _emit("evict", evicted, by=incoming["name"], case=title,
                              score=sc.get(evicted["name"], (0,))[0])
                world = World(positions, bind, cfg)  # the layout changed; look again
            if title and world.desk_tab and st.get("tab_title") != title:
                if _run(["tab", "rename", world.desk_tab, title], log):
                    st["tab_title"] = title
            st["notified"] = {k: v for k, v in st["notified"].items() if k in st["since"]}
    finally:
        if own:
            conn.close()
    return actions


# ---------------------------------------------------------------- watcher

def _event_type(ev):
    """herdr sends {"event": "<type>", ...} (a string) or {"event"|"result": {"type": ...}}."""
    if not isinstance(ev, dict):
        return ""
    for k in ("event", "result"):
        v = ev.get(k)
        if isinstance(v, str):
            return v
        if isinstance(v, dict) and isinstance(v.get("type"), str):
            return v["type"]
    return ev.get("type") if isinstance(ev.get("type"), str) else ""


def _subscribe(panes):
    s = socket.socket(socket.AF_UNIX)
    s.connect(os.environ.get("HERDR_SOCKET_PATH") or os.path.expanduser("~/.config/herdr/herdr.sock"))
    subs = [{"type": "pane.agent_status_changed", "pane_id": p} for p in panes]
    subs += [{"type": t} for t in ("pane.agent_detected", "pane.created", "pane.closed", "pane.exited")]
    s.sendall((json.dumps({"id": "of-desk", "method": "events.subscribe",
                           "params": {"subscriptions": subs}}) + "\n").encode())
    return s


def run(positions_loader, bind, log):
    """Foreground watcher. positions_loader() re-reads the registries on each reconcile."""
    cfg = load_config()
    stop = {"now": False}

    def on_term(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, on_term)
    log(f"of desk: watching (slots {cfg['slots']}, label {cfg['label']!r})")
    while not stop["now"]:
        try:
            reconcile(positions_loader(), bind, log, cfg)
            panes = sorted(a["pane_id"] for a in _herdr("agent", "list")["agents"])
            sock = _subscribe(panes)
            buf, dirty, resubscribe = b"", False, False
            last = time.time()
            while not stop["now"] and not resubscribe:
                sock.settimeout(cfg["batch_s"] if dirty else cfg["heartbeat_s"])
                try:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        ev = json.loads(line or b"{}")
                        if "error" in ev:
                            log(f"subscribe error: {ev['error']}")
                            time.sleep(5)
                            resubscribe = True
                        etype = _event_type(ev)
                        if etype in ("pane.agent_detected", "pane.created", "pane.closed", "pane.exited"):
                            resubscribe = True
                        dirty = True
                except socket.timeout:
                    if dirty or time.time() - last >= cfg["heartbeat_s"]:
                        reconcile(positions_loader(), bind, log, cfg)
                        dirty, last = False, time.time()
                        new = sorted(a["pane_id"] for a in _herdr("agent", "list")["agents"])
                        resubscribe = new != panes
            sock.close()
        except Exception as e:  # keep the router alive; herdr may restart
            log(f"error: {e}")
            time.sleep(5)
    log("of desk: stopped")  # reached only when the event loop ends; SIGTERM exits via SystemExit


# --------------------------------------------------------------- daemon

def pidfile():
    return os.path.join(STATE_DIR, "desk.pid")


def running_pid():
    try:
        pid = int(open(pidfile()).read().strip())
        os.kill(pid, 0)
        return pid
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return None


def start(of_path):
    if running_pid():
        return running_pid()
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    log = open(os.path.join(STATE_DIR, "desk.log"), "a")
    proc = subprocess.Popen([sys.executable, of_path, "desk", "run"], stdout=log, stderr=log,
                            stdin=subprocess.DEVNULL, start_new_session=True,
                            env=dict(os.environ, OF_AUTOMATION="1", SSH_AUTH_SOCK=""))
    with open(pidfile(), "w") as fh:
        fh.write(str(proc.pid))
    return proc.pid


def stop():
    pid = running_pid()
    if pid:
        os.kill(pid, signal.SIGTERM)
    try:
        os.remove(pidfile())
    except FileNotFoundError:
        pass
    return pid
