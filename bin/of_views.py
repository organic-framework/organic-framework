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
