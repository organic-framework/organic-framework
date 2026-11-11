"""The founder moves his focus himself; Organic Framework never does it for him (hand-over 3c7e6f17, 2026-11-10).

On 2026-11-10 at 19:50:57Z `of view apply conductor` moved of_conductor's pane into a new tab to lay it out with
its panel. The tab the founder was looking at emptied, herdr closed it, and he found himself in
of_onboarding-research's tab. Every herdr pane move OF makes (of apply, of view apply, the desk) passes `block`
first: a move is held back when it would take the pane he works in to another tab, or the last pane out of the tab
he is looking at. It runs on a later pass, once he has moved on.
"""

import json
import subprocess


def panes_now():
    out = subprocess.run(["herdr", "pane", "list"], capture_output=True, text=True, timeout=20)
    return json.loads(out.stdout or "{}").get("result", {}).get("panes") or [] if out.returncode == 0 else None


def block(args, panes=None):
    """Why this herdr command must not run now, or None (any command but a pane move passes)."""
    if list(args[:2]) != ["pane", "move"] or len(args) < 3:
        return None
    try:
        panes = panes if panes is not None else panes_now()
    except Exception:  # noqa: BLE001  herdr unreadable: no guard rather than no move
        return None
    if not panes:
        return None
    pane = args[2]
    focused = next((p for p in panes if p.get("focused")), None)
    src = next((p for p in panes if p.get("pane_id") == pane), None)
    if not focused or not src:
        return None
    dest = args[list(args).index("--tab") + 1] if "--tab" in args else "new"
    if dest == src.get("tab_id"):
        return None  # within its own tab: what he sees stays where it is
    if pane == focused.get("pane_id"):
        return "it is the pane the founder works in"
    if src.get("tab_id") == focused.get("tab_id") and sum(1 for p in panes if p.get("tab_id") == src.get("tab_id")) <= 1:
        return "it would empty the tab the founder is looking at"
    return None
