"""Asks in herdr: the owner sees an agent's open question where he works.

herdr decides a Claude pane's state from the screen (its detection manifest), and it ignores an
outside `blocked` report on a live Claude pane (tested 2026-11-05). So the question is shown as a
pane token instead: `ask` = the question's short text (60 characters), which the sidebar renders
in red, set by `of ask` and cleared when the position's open asks are answered, withdrawn or the
agent declares `of status working|done`. `of pulse` reconciles the tokens every run, and the
desk router treats a pane with an open ask like a blocked one.
"""

import subprocess

SOURCE = "of:ask"
MAX = 60


def counted_out(a):
    """A question asked in a pane (heuristic, confirmed by the judge) has no counter and no token: it sits in its own
    section of the popup only (founder, ask ddd6e3a3)."""
    return str(a.get("kind") or "").startswith("question (heuristic)")


def _herdr(*args):
    try:
        r = subprocess.run(["herdr", *args], capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def short(text):
    t = " ".join((text or "").split())
    return t if len(t) <= MAX else t[:MAX - 1] + "…"


def set_ask(pane, text):
    return _herdr("pane", "report-metadata", pane, "--source", SOURCE, "--token", f"ask={short(text)}")


def clear_ask(pane):
    return _herdr("pane", "report-metadata", pane, "--source", SOURCE, "--clear-token", "ask")


def panes_by_position(positions, agents, bind):
    return {p["name"]: a["pane_id"] for p, a in bind(positions, agents)[0]}


def reconcile(positions, agents, asks, bind):
    """Make every live pane's `ask` token match its position's open asks. Returns (set, cleared)."""
    import of_asks
    want = {}
    for a in of_asks.for_owner(asks):  # the same asks the popup and `of asks` show
        if a["state"] == "open" and a.get("position") and not counted_out(a):
            want.setdefault(a["position"], a["text"])  # the oldest open ask
    panes = panes_by_position(positions, agents, bind)
    have = {a["pane_id"]: (a.get("tokens") or {}).get("ask") for a in agents}
    done_set, done_clear = [], []
    for name, pane in panes.items():
        text = want.get(name)
        if text and have.get(pane) != short(text):
            set_ask(pane, text)
            done_set.append(name)
        elif not text and have.get(pane):
            clear_ask(pane)
            done_clear.append(name)
    return done_set, done_clear


def refresh_ask_tokens(positions, agents, asks, bind, workspaces=None, org_workspace=None):
    """The one place the herdr ask display is written from: each pane's `ask` token and each
    workspace's `asks` count, from the same projection the popup, `of asks`, `of who` and Studio
    use (open asks to the owner). Called on every ask change (ask, answer, withdraw, reconcile,
    status) and forced by every pulse, so the counts never drift. Returns {workspace: n}."""
    import json as _j
    import of_asks_tui
    reconcile(positions, agents, asks, bind)
    try:  # where the asks are: the asker's frame and its tab carry a marker
        apply_marks(positions, agents, asks, bind)
    except Exception:
        pass
    if workspaces is None:
        r = subprocess.run(["herdr", "workspace", "list"], capture_output=True, text=True, timeout=10)
        workspaces = _j.loads(r.stdout)["result"]["workspaces"] if r.returncode == 0 else []
    org_ws = org_workspace or {p.get("_org"): p.get("_home_ws") or p.get("_workspace") for p in positions}
    counts = of_asks_tui.workspace_counts(positions, agents, asks, bind, workspaces, org_ws)
    of_asks_tui.report_workspaces(counts)
    return counts


MARK_RX = __import__("re").compile(r"\s*·\s*❓ asks you|\s*❓\d+$")


def marks(positions, agents, asks, bind, tabs, pane_labels):
    """The herdr commands that show where the asks are: the asker's pane frame
    `■ name · ❓ asks you ■`, and its tab `<label> ❓N` (N open asks in that tab). Only labels that
    differ are rewritten; markers go away when the asks close. Pure."""
    import of_asks
    open_by = {}
    for a in of_asks.for_owner(asks):
        if a["state"] == "open" and a.get("position") and not counted_out(a):
            open_by[a["position"]] = open_by.get(a["position"], 0) + 1
    cmds, per_tab = [], {}
    for p, a in bind(positions, agents)[0]:
        n = open_by.get(p["name"], 0)
        want = f"■ {p['name']} · ❓ asks you ■" if n else f"■ {p['name']} ■"
        have = pane_labels.get(a["pane_id"]) or ""
        if have.startswith("■") and have != want:  # only frames of apply has labelled
            cmds.append(["pane", "rename", a["pane_id"], want])
        per_tab[a.get("tab_id")] = per_tab.get(a.get("tab_id"), 0) + n
    for t in tabs:
        base = MARK_RX.sub("", (t.get("label") or "")).strip()
        if not base:
            continue
        n = per_tab.get(t["tab_id"], 0)
        want = f"{base} ❓{n}" if n else base
        if (t.get("label") or "") != want:
            cmds.append(["tab", "rename", t["tab_id"], want])
    return cmds


def apply_marks(positions, agents, asks, bind):
    import json as _j
    tabs = _j.loads(subprocess.run(["herdr", "tab", "list"], capture_output=True, text=True, timeout=10).stdout)["result"]["tabs"]
    panes = _j.loads(subprocess.run(["herdr", "pane", "list"], capture_output=True, text=True, timeout=10).stdout)["result"]["panes"]
    for c in marks(positions, agents, asks, bind, tabs, {p["pane_id"]: p.get("label") for p in panes}):
        _herdr(*c)
