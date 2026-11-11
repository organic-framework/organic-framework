"""A position's auto-memory directory (RFC 0006 §2.1, of_architect, 2026-11-09).

Claude Code keeps its auto-memory under the account's config dir and the working folder. Under OF that meant positions
sharing a folder shared one index, the index was cut at 200 lines and 25,000 bytes (the newest lessons last, so lost),
and every account kept its own copy. OF therefore launches every position with
`--settings '{"autoMemoryDirectory": "<dir>"}'` (of spawn here; of capacity move and resume call the same functions).

    <dir> = ~/.local/state/of/memory/<org>/<scope id>/      mode 700, never committed, outside every account

The scope is the registry key `memory:` (position, unit or org), read on the position, then on its unit, then at the
top of the organization's registry; the nearest setting wins and the default is `position`. The scope id is the
position's name, `unit-<unit>`, or `org`. A unit scope for a position in no unit falls back to the position.
A shared index has an owner who keeps it under the cut (owner()): the unit's owner, or the structure keeper.
"""

import json
import os

import of_events

SCOPES = ("position", "unit", "org")
INDEX = "MEMORY.md"
WARN_LINES = 180        # RFC 0006 §2.3: below the CLI's cut of 200 lines
WARN_BYTES = 22000      # and of 25,000 bytes


def root():
    return os.path.join(of_events.STATE_DIR, "memory")


def scope(p):
    unit = ((p.get("_units") or {}).get(p.get("_unit")) or {}) if p.get("_unit") else {}
    for v in (p.get("memory"), unit.get("memory") if isinstance(unit, dict) else None, p.get("_memory_org")):
        if v in SCOPES:
            return "position" if v == "unit" and not p.get("_unit") else v
    return "position"


def scope_id(p):
    s = scope(p)
    return f"unit-{p['_unit']}" if s == "unit" else ("org" if s == "org" else p["name"])


def directory(p, base=None):
    return os.path.join(base or root(), str(p.get("_orgid") or p.get("_org") or "_"), scope_id(p))


def ensure(d):
    """Create the directory and its parents under the memory root, mode 700 (memory can hold what a repository
    must not)."""
    os.makedirs(d, mode=0o700, exist_ok=True)
    top = os.path.dirname(os.path.dirname(d))
    for x in (d, os.path.dirname(d), top):
        if x.startswith(top):
            os.chmod(x, 0o700)
    return d


def settings(p, base=None, make=True):
    """The `--settings` value for a position's launch: {"autoMemoryDirectory": <its dir>}, the directory made."""
    d = directory(p, base)
    if make:
        ensure(d)
    return json.dumps({"autoMemoryDirectory": d})


def owner(p, positions):
    """Who keeps the index under the cut: the position itself; a unit's owner for `unit`; the structure keeper for
    `org` (RFC 0006 §2.1). Falls back to the position."""
    s = scope(p)
    if s == "unit":
        return p.get("_unit_owner") or p["name"]
    if s == "org":
        import of_pulse
        return of_pulse.structure_keeper(positions, p.get("_org")) or p["name"]
    return p["name"]


def index_size(d):
    """(lines, bytes) of the directory's index, (0, 0) without one."""
    try:
        with open(os.path.join(d, INDEX), "rb") as fh:
            data = fh.read()
    except OSError:
        return 0, 0
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0), len(data)


def over(d):
    lines, size = index_size(d)
    return lines > WARN_LINES or size > WARN_BYTES


def warning(d, name, lines, size, keeper=None):
    """The session-start note for an index past the warning line (RFC 0006 §2.3)."""
    where = os.path.join(d, INDEX)
    home = os.path.expanduser("~")
    where = "~" + where[len(home):] if where.startswith(home) else where
    shared = os.path.basename(d.rstrip("/")) != name
    whose = "The shared memory index you keep" if shared else "Your memory index"
    if keeper == name:
        rules = "write the rules that should last into the briefs or a rules file yourself"
    else:
        rules = f"send the rules that should last to {keeper or 'your structure keeper'} for the brief"
    return (f"Memory: {whose}, {where}, has {lines} lines ({size:,} bytes). Claude Code loads only its first 200 lines "
            f"and 25,000 bytes, and new entries go at the end, so the newest lessons are the ones it cuts. Now: merge "
            f"related entries, generalize them into the kind of mistake, and {rules} (RFC 0006, section 2.3)."
            + (" Knowledge every role of the unit needs belongs in a practice file the briefs load." if shared else ""))
