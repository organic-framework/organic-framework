"""Pinned views (founder, 2026-11-06): a fixed tab the owner keeps in sight, `~/.config/of/views.yaml`:

    views:
      - name: core
        workspace: organic-framework
        tab: core
        layout: main-left-60          # the first position on the left (60%), the others in a column
        positions: [of_architect, acme_org, of_platform, of_studio]

The desk router never takes a view's positions onto the desk (they are in view already), and
`of apply` neither moves nor relabels them. `of view apply <name>` builds or restores the tab; the
listener restores it when one of its agents was moved away. Layouts:
  main-left-60   main on the left at 60% width, the rest stacked on the right in equal heights
  grid           1, 2, 3 (left + 2) or 4 (2x2) as of apply lays out a system's tab
herdr's `pane move --ratio` sets the share of the EXISTING (target) pane, so the main pane keeps 0.6.
"""

import os
import re
import shlex

import yaml

LAYOUTS = ("main-left-60", "grid")


def path():
    import of_events
    return of_events.config_path("views.yaml")


def load():
    try:
        with open(path()) as fh:
            doc = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return []
    return [v for v in doc.get("views") or [] if isinstance(v, dict) and v.get("name")]


def save(views):
    p = path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as fh:
        fh.write("# Pinned views (of view): tabs the owner keeps in sight; the desk router and of apply leave them alone.\n")
        yaml.safe_dump({"views": views}, fh, sort_keys=False, allow_unicode=True)


def pinned_positions(views=None):
    return {p for v in (views if views is not None else load()) for p in v.get("positions") or []
            if not str(p).startswith("panel:")}


def check(view, position_names):
    out = []
    if view.get("layout", "main-left-60") not in LAYOUTS:
        out.append(f"layout {view.get('layout')!r} is not one of {', '.join(LAYOUTS)}")
    for k in ("workspace", "tab"):
        if not view.get(k):
            out.append(f"{k} is required")
    ps = view.get("positions") or []
    if not ps:
        out.append("positions is required")
    panels = view.get("panels") or {}
    if not isinstance(panels, dict):
        out.append("panels must map a panel name to its command")
        panels = {}
    out += [f"position {p!r} is not in any registry" for p in ps
            if p not in position_names and not (str(p).startswith("panel:") and str(p)[6:] in panels)]
    out += [f"panel {n!r} is not placed: add panel:{n} to positions" for n in panels if f"panel:{n}" not in ps]
    return out


QUESTIONS_PANEL = "questions"
PANEL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def panel_spec(spec, python, bin_dir):
    """(name, command) for `of view add --panel NAME[=COMMAND]`. The questions panel (`of asks --panel`) needs no
    command: it runs under `of_herdr_plugin.py keep`, which starts it again when it exits, with this Python and this
    bin/. Until 2026-11-18 a view's panel could be written only by hand into views.yaml, and `of view add --positions
    …,panel:questions` was refused as an unknown position, so on a machine other than the one it was built on nobody
    could set the panel up (the founder, ~18:16Z via of_conductor, msg 01a1efa8-8574). Raises ValueError with what
    to write instead."""
    name, sep, command = str(spec or "").partition("=")
    name, command = name.strip(), command.strip()
    if not PANEL_NAME.match(name):
        raise ValueError(f"--panel {spec!r}: a panel's name is letters, digits, - or _ (at most 32)")
    if sep and not command:
        raise ValueError(f"--panel {spec!r}: give the command after =, or leave out =")
    if not sep:
        if name != QUESTIONS_PANEL:
            raise ValueError(f"--panel {name}: only the {QUESTIONS_PANEL} panel has a built-in command; "
                             f"give its command as --panel {name}=COMMAND")
        q = shlex.quote
        command = (f"env OF_AUTOMATION=1 {q(python)} {q(os.path.join(bin_dir, 'of_herdr_plugin.py'))} keep --name "
                   f"{q('the questions panel')} -- {q(python)} {q(os.path.join(bin_dir, 'of'))} asks --panel")
    return name, command


def expected(view, area, panes):
    """[(pane, (x, y, w, h))] the view's layout puts its panes at, in `area`."""
    x, y, w, h = area["x"], area["y"], area["width"], area["height"]
    if not panes:
        return []
    if view.get("layout", "main-left-60") == "grid":
        import importlib.machinery
        import importlib.util
        here = os.path.dirname(os.path.realpath(__file__))
        loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(here, "of"))
        spec = importlib.util.spec_from_loader("of_cli", loader)
        of = importlib.util.module_from_spec(spec)
        loader.exec_module(of)
        return list(zip(panes, of.expected_rects(len(panes), area)))
    if len(panes) == 1:
        return [(panes[0], (x, y, w, h))]
    mw = round(w * 0.6)
    rest = panes[1:]
    rh = h / len(rest)
    return [(panes[0], (x, y, mw, h))] + [(p, (x + mw, y + i * rh, w - mw, rh)) for i, p in enumerate(rest)]


def matches(view, layout, panes, tol=3):
    if not layout:
        return False
    got = {p["pane_id"]: p["rect"] for p in layout.get("panes") or []}
    if set(got) != set(panes):
        return False
    for p, (x, y, w, h) in expected(view, layout["area"], panes):
        r = got[p]
        if any(abs(a - b) > tol for a, b in ((r["x"], x), (r["y"], y), (r["width"], w), (r["height"], h))):
            return False
    return True


def panel_held_elsewhere(views, panel, view_name):
    """The name of another view that already places `panel:<panel>`, or None. A panel's pane is found by its label
    (`▤ <name>`), one per machine, so two views holding it would take it from each other every minute (of_onboarding,
    msg 01a1efbe-b153); `of init` skips the pin then, and `of view add --panel` refuses."""
    return next((v["name"] for v in views if v.get("name") != view_name
                 and f"panel:{panel}" in (v.get("positions") or [])), None)


def split_ratio(view):
    """The share a panel's split leaves its anchor, the view's first agent: as the layout gives the main pane. herdr
    0.9.5 moves no pane within its own tab (a `pane move --tab <its tab> --split` keeps its size: lab, 2026-11-18), so
    a panel split at herdr's default half never reached main-left-60's 60%."""
    return "0.5" if view.get("layout", "main-left-60") == "grid" else "0.6"


def adoptable_tab(tabs, pane_tabs, panes, workspace_id):
    """The tab to give the view's tab label instead of moving its panes into a new tab, or None: the tab the view's
    first pane is in, when it lies in the view's workspace and holds nothing but the view's panes. `of init` pins the
    conductor's view before `of spawn` opens the conductor's pane in a tab named by its system. Then every
    `of view apply` moved the conductor to a new tab: held back while the person worked in it (bin/of_focus.py), and
    then it emptied and closed the tab they had been in (hand-over 3c7e6f17). tabs: herdr's tab list; pane_tabs:
    {pane id: tab id} for every pane."""
    if not panes or not workspace_id:
        return None
    tid = pane_tabs.get(panes[0])
    t = next((t for t in tabs if t.get("tab_id") == tid), None)
    if not t or t.get("workspace_id") != workspace_id:
        return None
    inside = {p for p, x in pane_tabs.items() if x == tid}
    return t if inside and inside <= set(panes) else None


def plan_moves(view, panes, tab_id=None, workspace_id=None, in_tab=False):
    """The herdr commands that build the view's tab with its panes in order. in_tab: the tab exists and holds the
    first pane, so the others are laid out around it there; otherwise the first pane opens a new tab."""
    if not panes:
        return []
    first, rest = panes[0], panes[1:]
    cmds = []
    if in_tab:
        pass
    elif workspace_id:
        cmds.append(["pane", "move", first, "--new-tab", "--workspace", workspace_id, "--label", view["tab"],
                     "--no-focus"])
    else:
        cmds.append(["pane", "move", first, "--new-workspace", "--label", view["workspace"], "--tab-label",
                     view["tab"], "--no-focus"])
    if view.get("layout", "main-left-60") == "grid":
        steps = []
        if len(panes) >= 2:
            steps.append((panes[1], "right", panes[0], "0.5"))
        if len(panes) == 3:
            steps.append((panes[2], "down", panes[1], "0.5"))
        elif len(panes) == 4:
            steps += [(panes[2], "down", panes[0], "0.5"), (panes[3], "down", panes[1], "0.5")]
    else:
        steps = []
        if rest:
            steps.append((rest[0], "right", first, "0.6"))  # the target (main) keeps 60%
        n = len(rest)
        for i in range(1, n):  # each split leaves the target 1/(n-i+1) of what remains: equal heights
            steps.append((rest[i], "down", rest[i - 1], f"{1 / (n - i + 1):.4f}"))
    for pane, direction, target, ratio in steps:
        cmds.append(["pane", "move", pane, "--tab", "{TAB}", "--split", direction, "--target-pane", target,
                     "--ratio", ratio, "--no-focus"])
    return cmds
