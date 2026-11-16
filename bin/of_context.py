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
import time

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


# What a position runs as subagents once its owner said yes (docs/method.md, "Subagents: the owner's standing yes"):
# shown only where the organization's registry carries that yes, in the owner's own words (docs/registry.md,
# "Subagents"; hand-over ff0f6f09; of_review on PR 241: an owner's words carry no authority outside their organization)
SUBAGENTS_HOW = ("Run the independent parts of your work as subagents, in parallel, not one after another in your own "
                 "turns: a loop's variants (one hypothesis each, in its own git worktree, within the work's wip_arms), "
                 "searches, a fresh-context review, reading, watching a runner (on the loop's babysit_model), each on a "
                 "model that fits its task. Heavy jobs stay on runners or CI. Decisions, asks to the owner, messages to "
                 "positions, of status and the registry stay yours (docs/method.md, \"Subagents: the owner's standing "
                 "yes\").")


def subagents_section(subagents):
    """The start context's section for (words, ids) from bin/of org_subagents, or nothing."""
    if not subagents:
        return []
    words, ids, typed = (tuple(subagents) + ([],))[:3]
    said = "; ".join(f"\"{t}\"" for t in typed if t)
    return ["", "## Subagents: your owner asks you to use them",
            f"Your owner's standing yes for the Agent tool (your organization's registry, subagents:; {', '.join(ids)}): "
            f"{words}" + (f" As typed in their conductor: {said}" if said else ""), SUBAGENTS_HOW]


def owner_measures(loops, resolve=None):
    """Lines naming each loop whose target names the owner's yes, from [(work id, owner|doer, loop)]; RESOLVE (an id ->
    the yes in the log) tells a real one from an unresolved one, which is named as such (of_review on PR 168)."""
    import of_loop
    out, real = [], False
    for wid, role, loop in loops or []:
        if of_loop.decided(loop):
            mine = (lambda did, w=wid: resolve(did, w)) if resolve else None  # an ask counts for its own work only
            out.append(f"- `{wid}` (you are its {role}): {of_loop.target_line(loop, mine)}.")
            real = real or bool(of_loop.owners_yes(loop, mine))
    if not out:
        return []
    return (["", "## Loops whose target names the owner's yes"] + out
            + (["Where the owner decided the measure, the measure itself justifies the work's changes. Report cost "
                "and money beside it; never ask the owner, or relay an ask, to price, justify or prove it in money. "
                "The plateau question and every gate on an act stay (docs/registry.md, Works, \"The owner's "
                "measure\")."] if real else []))


def not_observed_runs(runs, limit=5):
    """Lines for the newest runs of the position's loops that are proxies whose condition did not hold or is unknown,
    from [(work, run, ts)] (of_loop.blind_runs): shown as not observed, never as the number alone (docs/registry.md,
    Works, "Where a reading is taken")."""
    import of_loop
    if not runs:
        return []
    out = [f"- `{w}` {r.get('hypothesis') or r.get('run') or '?'}, run {str(ts)[:16]} on {r.get('rung') or '?'}: "
           f"{of_loop.not_observed(r)}." for w, r, ts in runs[:limit]]
    return (["", "## Loop runs not observed where their event happens"] + out
            + ["Such a run decides nothing: no keep or drop rests on it, and a conclusion about the event rests only on "
               "readings taken where it happens, or on the data where the condition held (docs/registry.md, Works, "
               "\"Where a reading is taken\")."])


def loop_clocks(clocks, limit=8):
    """Lines for the position's loops with a hypothesis in testing, from {work: of_loop.clocks(...)}: the wait holding
    the stall clock with its time left, or the hours with no run, decision or holding wait, the other waits stated
    since the last progress, and the duty to state a wait (docs/registry.md, Works, "A stated wait")."""
    def until(x):
        return time.strftime('%Y-%m-%d %H:%MZ', time.gmtime(x["expect_at"])) if x.get("expect_at") else "?"
    out = []
    for w, c in sorted((clocks or {}).items())[:limit]:
        if not c.get("testing"):
            continue
        x = c.get("wait")
        if x:
            line = (f"- `{w}` ({c['role']}): its stall clock is held by the wait on {x['on']} {x.get('ref') or ''} until "
                    f"{until(x)} ({c['left_h']:g} h left)").replace("  until", " until")
        else:
            line = f"- `{w}` ({c['role']}): {c['since_h']:g} h with no run, decision or holding wait"
        note = {"calendar": " (holds no clock)", "not_counted": " (a restatement the clock does not count)"}
        rest = [y for y in c.get("stated") or [] if y.get("status") != "holder"]
        if rest:
            line += "; also stated: " + "; ".join(
                f"{y['on']} {y.get('ref') or ''} until {until(y)}".replace("  until", " until")
                + (f" for {y['hypothesis']}" if y.get("hypothesis") else "") + note.get(y.get("status"), "")
                for y in rest[-4:])
        out.append(line + ".")
    if not out:
        return []
    return (["", "## Loops with a hypothesis in testing"] + out
            + ["State a wait when the next run or decision will not come within 4 h: of loop wait <work> --on "
               "runner|window|release|holdout|ask|position|calendar --ref <what> --until <time> (docs/registry.md, "
               "Works, \"A stated wait\"). Of the waits stated since the last progress, the one that ends first holds "
               "the clock; a wait on the calendar or held-out data holds none; restating a wait may shorten it, "
               "never lengthen it."])


def build(position, positions, loops=None, resolve=None, runs=None, clocks=None, subagents=None):
    """additionalContext for one enrolled position, or None. LOOPS: [(work id, owner|doer, loop)] of the works it owns
    or does (bin/of position_loops); RESOLVE: id -> the owner's yes in the log (bin/of _decided_resolver); RUNS: their
    newest runs that are proxies whose condition did not hold (of_loop.blind_runs)."""
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
    lines += not_observed_runs(runs)
    lines += loop_clocks(clocks)
    brief = _read(os.path.join(root, p["mandate"]), BRIEF_MAX) if p.get("mandate") else None
    if brief:
        lines += ["", "## Brief", brief.strip()]
    r = rules(os.path.join(root, "AGENTS.md"))
    if r:
        lines += ["", "## Binding rules (AGENTS.md of the organization)", r]
    lines += subagents_section(subagents)
    team = [x for x in positions if x.get("_org") == p.get("_org") and x["name"] != p["name"]
            and x.get("status") in (None, "active")][:TEAM_MAX]
    if team:
        lines += ["", "## Your team (message them with `of send <name> \"…\"`; ask the founder only with `of ask`)"]
        lines += [f"- `{x['name']}` ({x.get('role')}): {x.get('summary') or ''}" for x in team]
    lines += ["", "Your scratchpad sits under /private/tmp, which macOS's tmp_cleaner empties every night of files not "
                  "accessed, modified or changed for 3 days: a script a cron, monitor or watch runs lives in a path "
                  "that persists, and a watch that cannot run prints FAIL (a user organization, 2026-11-14)."]
    lines += ["", "At the end of every turn: `of status working|waiting --on founder|<position>|idle|done`."]
    return "\n".join(lines)
