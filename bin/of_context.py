"""The mandate in context (RFC 0001 v3 §5.7, step 1 of steering).

For an ENROLLED position (registry `enrolled: true`), the SessionStart hook (sources startup,
resume, clear, compact) returns `additionalContext` with: who the session is (canonical name,
organization, system, role), the current mandate (summary, status, throughput, serves), the brief
(cut to a limit), the organization's binding rules (AGENTS.md sections), and the team (the other
positions of the organization, with what each does now). Recording is unaffected; this only adds
context, and only for enrolled positions.

A position that owns or does a work whose loop target the owner decided (docs/registry.md, Works, "The owner's
measure") is told so: the target, the id of that yes, and that its money is reported, never asked for as a proof.
"""

import os
import re

BRIEF_MAX = 9000
RULES_MAX = 4000
TEAM_MAX = 40
RULE_SECTIONS = ("Rules", "Working together", "Language", "Agent names", "No heavy local jobs on the Mac")


def _read(path, limit):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    return text if len(text) <= limit else text[:limit] + f"\n…[cut at {limit} characters; read the file: {path}]"


def rules(agents_md, limit=RULES_MAX):
    """The binding sections of an AGENTS.md (by heading), concatenated and cut to a limit."""
    text = _read(agents_md, 10 ** 6)
    if not text:
        return None
    parts, cur, keep = [], [], False
    for line in text.splitlines():
        m = re.match(r"^##\s+(.*)", line)
        if m:
            if keep and cur:
                parts.append("\n".join(cur))
            cur, keep = [line], any(m.group(1).strip().startswith(s) for s in RULE_SECTIONS)
        elif keep:
            cur.append(line)
    if keep and cur:
        parts.append("\n".join(cur))
    out = "\n\n".join(parts).strip()
    return out[:limit] + ("\n…" if len(out) > limit else "") if out else None


def owner_measures(loops, resolve=None):
    """Lines naming each loop whose target names the owner's yes, from [(work id, owner|doer, loop)]; RESOLVE (an id ->
    the yes in the log) tells a real one from an unresolved one, which is named as such (of_review on PR 168)."""
    import of_loop
    out, real = [], False
    for wid, role, loop in loops or []:
        if of_loop.decided(loop):
            out.append(f"- `{wid}` (you are its {role}): {of_loop.target_line(loop, resolve)}.")
            real = real or bool(of_loop.owners_yes(loop, resolve))
    if not out:
        return []
    return (["", "## Loops whose target names the owner's yes"] + out
            + (["Where the owner decided the measure, the measure itself justifies the work's changes. Report cost "
                "and money beside it; never ask the owner, or relay an ask, to price, justify or prove it in money. "
                "The plateau question and every gate on an act stay (docs/registry.md, Works, \"The owner's "
                "measure\")."] if real else []))


def build(position, positions, loops=None, resolve=None):
    """additionalContext for one enrolled position, or None. LOOPS: [(work id, owner|doer, loop)] of the works it owns
    or does (bin/of position_loops); RESOLVE: id -> the owner's yes in the log (bin/of _decided_resolver)."""
    p = next((x for x in positions if x["name"] == position), None)
    if not p or not p.get("enrolled"):
        return None
    root = p.get("_root") or ""
    org = p.get("_orgid") or p.get("_org")
    lines = [f"# Organic Framework: you hold the position `{p['name']}`",
             f"Organization `{org}` · system `{p.get('system')}` · role `{p.get('role')}` · kind `{p.get('kind')}`"
             f" · owner `{p.get('owner')}`.",
             "",
             "## Current mandate",
             f"- summary: {p.get('summary') or '-'}",
             f"- status: {p.get('status') or '-'}; throughput class: {p.get('throughput') or '-'}"
             + (f"; serves `{p['serves']}` (its data stays in that organization)" if p.get("serves") else ""),
             f"- brief: {p.get('mandate') or '-'}"]
    ex = p.get("mandate_exit") if isinstance(p.get("mandate_exit"), dict) else None
    if ex:
        lines.append(f"- exit: done when {ex.get('done_when')}" + (f"; review {ex['review']}" if ex.get("review") else ""))
    if p.get("mandate_functions"):
        lines.append("- management functions covered: " + ", ".join(map(str, p["mandate_functions"])))
    if p.get("waiting_on"):
        lines.append(f"- registry says you wait on: {p['waiting_on']}")
    lines += owner_measures(loops, resolve)
    brief = _read(os.path.join(root, p["mandate"]), BRIEF_MAX) if p.get("mandate") else None
    if brief:
        lines += ["", "## Brief", brief.strip()]
    r = rules(os.path.join(root, "AGENTS.md"))
    if r:
        lines += ["", "## Binding rules (AGENTS.md of the organization)", r]
    team = [x for x in positions if x.get("_org") == p.get("_org") and x["name"] != p["name"]
            and x.get("status") in (None, "active")][:TEAM_MAX]
    if team:
        lines += ["", "## Your team (message them with `of send <name> \"…\"`; ask the founder only with `of ask`)"]
        lines += [f"- `{x['name']}` ({x.get('role')}): {x.get('summary') or ''}" for x in team]
    lines += ["", "At the end of every turn: `of status working|waiting --on founder|<position>|idle|done`."]
    return "\n".join(lines)
