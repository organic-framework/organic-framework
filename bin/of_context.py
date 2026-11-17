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

BRIEF_MAX = 10 ** 6   # read whole: build() fits the context to CAP at paragraph and bullet boundaries
RULES_MAX = 10 ** 6
# Claude Code saves a hook's injected output above 10,000 characters to a file and shows the agent a 2 KB preview,
# which holds only the header and the mandate lines (hand-over b460c5ed from of_architect: 76 of 79 start contexts on
# 2026-11-16 were over it). The context OF emits stays under CAP, with room to spare.
CAP = 9500
RULE_SECTIONS = ("Rules", "Working together", "Language", "Agent names", "No heavy local jobs on the Mac")


def _read(path, limit):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        return None
    return text if len(text) <= limit else text[:limit]


# The binding rules reach a position whole through a file Claude Code loads itself, not through the capped start
# context (hand-over 83b08e7b; checked on Claude Code 2.1.326): a project's .claude/rules/*.md without `paths:` loads
# at a session's start, is re-read from disk after compaction, needs no approval when its text is in it, and keeps the
# repository's own AGENTS.md loading (a CLAUDE.local.md would stop that). of apply writes one per organization into
# each folder its positions run in, excluded from git there.
RULES_FILE = os.path.join(".claude", "rules", "of-{org}.md")


def rules_file_text(org, agents_md):
    """The rules file's text for ORG from its AGENTS.md, or None when it has no binding sections."""
    r = rules(agents_md)
    if not r:
        return None
    return (f"# Binding rules of the organization `{org}`\n\n"
            f"These rules bind the positions of `{org}` that run in this folder; a session of another organization "
            f"ignores this file. Written by OF (of apply and the pulse) "
            f"from {agents_md} (hand-over 83b08e7b); edit that file, not this one: OF writes this one again.\n\n{r}\n")


def rules_folders(positions, org_paths, work_dir=None):
    """{org: [folders]}: the folders the organization's active positions run in (of_spawn.work_dir)."""
    import of_spawn
    work_dir = work_dir or of_spawn.work_dir
    out = {}
    for p in positions:
        if p.get("status") not in (None, "active") or (p.get("holder") or {}).get("runtime") == "human":
            continue
        org = p.get("_orgid") or p.get("_org")
        try:
            d = work_dir(p, p.get("_systems") or {}, org_paths.get(p.get("_org")) or [])
        except Exception:  # noqa: BLE001 - no folder for it: of registry check names it
            continue
        if d and d not in out.setdefault(org, []):
            out[org].append(d)
    return out


def _exclude(folder, run=None):
    """Keep the rules file out of the folder's git repository: one line in its info/exclude, never a commit."""
    import subprocess
    run = run or subprocess.run
    r = run(["git", "-C", folder, "rev-parse", "--git-path", "info/exclude"], capture_output=True, text=True,
            env=dict(os.environ, GIT_CEILING_DIRECTORIES=os.path.expanduser("~")))
    if r.returncode != 0 or not r.stdout.strip():
        return False
    path = r.stdout.strip() if os.path.isabs(r.stdout.strip()) else os.path.join(folder, r.stdout.strip())
    line = "/.claude/rules/of-*.md"
    try:
        have = open(path).read().splitlines() if os.path.exists(path) else []
    except OSError:
        return False
    if line not in have:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as fh:
            fh.write(("\n" if have and have[-1] else "") + "# of apply's rules files (hand-over 83b08e7b)\n" + line + "\n")
    return True


def session_orgs(conn=None, days=7):
    """{folder: {organizations}} of the sessions started in the last DAYS (of.session_start's cwd and org): where the
    positions really run, which the registry may not say."""
    import time as _t
    own = conn is None
    try:
        if own:
            import of_fold
            conn = of_fold.reader()
        if conn is None:
            return {}
        since = _t.strftime("%Y-%m-%dT%H:%M:%S", _t.gmtime(_t.time() - days * 86400))
        out = {}
        for org, cwd in conn.execute("SELECT DISTINCT org, cwd FROM event WHERE name = 'of.session_start' AND ts >= ? "
                                     "AND cwd IS NOT NULL AND org IS NOT NULL", (since,)):
            out.setdefault(os.path.realpath(os.path.expanduser(cwd)), set()).add(org)
        return out
    except Exception:  # noqa: BLE001 - no store: the registry's folders alone
        return {}
    finally:
        if own and conn is not None:
            conn.close()


def shared_folders(folders, sessions):
    """{folder: {organizations}} of the folders where more than one organization's positions run, by the registry
    (FOLDERS, from rules_folders) or by the sessions started there or below (SESSIONS, from session_orgs). Claude Code
    loads every rules file of a folder into every session there, so such a folder gets none (of_review and
    of_architect on organic-framework PR 320: a user organization's sessions in the framework's folder loaded the OF
    team's rules)."""
    by = {}
    for org, dirs in folders.items():
        for d in dirs:
            by.setdefault(os.path.realpath(d), set()).add(org)
    for d in list(by):
        for cwd, orgs in sessions.items():
            if cwd == d or cwd.startswith(d + os.sep):
                by[d] |= orgs
    return {d: orgs for d, orgs in by.items() if len(orgs) > 1}


def write_rules(positions, org_paths, dry_run=False, log=print, run=None, sessions=None):
    """Write each organization's rules file into each folder its positions run in, when it changed; in a folder shared
    by two organizations, none, and an earlier one is removed. Returns the paths written (or that would be)."""
    folders = rules_folders(positions, org_paths)
    shared = shared_folders(folders, session_orgs() if sessions is None else sessions)
    roots = {(p.get("_orgid") or p.get("_org")): p.get("_root") for p in positions}
    written = []
    for d, orgs in shared.items():
        for org in orgs:
            old = os.path.join(d, RULES_FILE.format(org=org))
            if os.path.exists(old) and not dry_run:
                os.remove(old)
                log(f"  removed {old}: {d} is shared by {', '.join(sorted(orgs))}")
    for org, dirs in folders.items():
        text = rules_file_text(org, os.path.join(roots.get(org) or "", "AGENTS.md"))
        if not text:
            continue
        mine = [os.path.realpath(d) for d in dirs if os.path.realpath(d) not in shared]
        for d in dirs:
            r = os.path.realpath(d)
            if r in shared:
                continue
            # Claude Code loads a parent folder's rules too: a folder below another of this organization's gets none,
            # or a session there loads the rules twice
            if any(a != r and r.startswith(a + os.sep) for a in mine):
                inner = os.path.join(d, RULES_FILE.format(org=org))
                if os.path.exists(inner) and not dry_run:
                    os.remove(inner)
                    log(f"  removed {inner}: the folder above holds the same rules")
                continue
            path = os.path.join(d, RULES_FILE.format(org=org))
            if _read(path, 10 ** 7) == text:
                continue
            written.append(path)
            if dry_run:
                log(f"  would write {path}")
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".tmp", "w") as fh:
                fh.write(text)
            os.replace(path + ".tmp", path)
            _exclude(d, run)
            log(f"  wrote {path} ({len(text):,} characters of {org}'s binding rules)")
    return written


def rules_loaded_from(cwd, org, agents_md):
    """The rules file Claude Code loads for a session in CWD (in CWD or a folder above it, as Claude Code reads them),
    when it holds ORG's rules as OF would write them now; else None (the start context then carries the rules)."""
    text = rules_file_text(org, agents_md)
    if not text or not cwd:
        return None
    d = os.path.realpath(os.path.expanduser(cwd))
    while True:
        path = os.path.join(d, RULES_FILE.format(org=org))
        if _read(path, 10 ** 7) == text:
            return path
        up = os.path.dirname(d)
        if up == d:
            return None
        d = up


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
    return out[:limit] if out else None


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


def context_path(org, position, state=None):
    """Where the whole start context is kept when the injected one is cut: the position's state folder (mode 600)."""
    import of_events
    return os.path.join(state or of_events.STATE_DIR, "context", str(org), f"{position}.md")


def fit(head, sections, cap=CAP, note=None):
    """(text, cut) where text = HEAD and SECTIONS [(name, text)] in order, within CAP characters, each section whole,
    cut at a line boundary (a paragraph or a bullet: never mid-sentence), or left out; CUT [(name, kept, whole)] for
    each section not kept whole. NOTE(cut) -> the first line naming what was cut, which counts in CAP."""
    def join(parts):
        return "\n".join(x for x in parts if x is not None)
    cut, keep = [], []
    for _ in range(3):   # the note's own length depends on what is cut: settle it
        first = note(cut) if (cut and note) else None
        room = cap - len(join([first, head])) - (40 if first else 0)   # the note's numbers may still grow
        keep, now = [], []
        for name, text in sections:
            if not text:
                continue
            block = "\n" + text
            if len(block) + 1 <= room:
                keep.append(block)
                room -= len(block) + 1
                continue
            lines, taken = block.split("\n"), []
            for ln in lines:
                if len("\n".join(taken + [ln])) + 1 > room:
                    break
                taken.append(ln)
            body = [ln for ln in taken if ln.strip()]
            # a heading alone carries nothing: keep a part only when it holds more than the section's heading
            if len(body) > 1:
                part = "\n".join(taken).rstrip("\n")
                keep.append(part)
                room -= len(part) + 1
                now.append((name, len(part), len(block)))
            else:
                now.append((name, 0, len(block)))
        if [c[0] for c in now] == [c[0] for c in cut] and (not now or first is not None):
            cut = now
            break
        cut = now
    first = note(cut) if (cut and note) else None
    text = join([first, head] + keep)
    if len(text) > cap and cap > len(head):   # the final note grew past its margin: fit again in less room
        return fit(head, sections, cap - (len(text) - cap), note)
    return text, cut


def build(position, positions, loops=None, resolve=None, runs=None, clocks=None, subagents=None, info=None, cap=CAP,
          unwritten=False, cwd=None):
    """additionalContext for one enrolled position, or None. LOOPS: [(work id, owner|doer, loop)] of the works it owns
    or does (bin/of position_loops); RESOLVE: id -> the owner's yes in the log (bin/of _decided_resolver); RUNS: their
    newest runs that are proxies whose condition did not hold (of_loop.blind_runs). The text is at most CAP characters
    (hand-over b460c5ed): identity and mandate first, then the loop sections, the brief, the organization's binding
    rules, the subagents section and the team, each cut at a paragraph or bullet boundary or left out, and when any is,
    the first line names what and points to the whole context at context_path(), which INFO['full'] carries for the
    hook to write; INFO['cut']: [(section, kept chars, whole chars)]."""
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
    lines += ["- at the end of every turn: `of status working|waiting --on founder|<position>|idle|done`."]
    head = "\n".join(lines)

    def section(parts):
        return "\n".join(parts).lstrip("\n") if parts else None
    brief = _read(os.path.join(root, p["mandate"]), BRIEF_MAX) if p.get("mandate") else None
    loaded = rules_loaded_from(cwd, org, os.path.join(root, "AGENTS.md"))
    r = None if loaded else rules(os.path.join(root, "AGENTS.md"))
    # every active teammate: what does not fit is cut at a line and named in the note, never dropped silently
    team = [x for x in positions if x.get("_org") == p.get("_org") and x["name"] != p["name"]
            and x.get("status") in (None, "active")]
    sections = [
        ("the owner's measure", section(owner_measures(loops, resolve))),
        ("runs not observed", section(not_observed_runs(runs))),
        ("loops in testing", section(loop_clocks(clocks))),
        ("the brief", section(["## Brief", brief.strip()]) if brief else None),
        ("the binding rules", section(["## Binding rules (AGENTS.md of the organization)", r]) if r else
         (f"## Binding rules\nThe organization's binding rules reach you whole from {loaded}, which Claude Code loads "
          "itself and reads again after a compaction." if loaded else None)),
        ("subagents", section(subagents_section(subagents))),
        ("your team", section(["## Your team (message them with `of send <name> \"…\"`; ask the founder only with "
                               "`of ask`)"] + [f"- `{x['name']}` ({x.get('role')}): {x.get('summary') or ''}"
                                             for x in team]) if team else None),
        ("the scratchpad note", "Your scratchpad sits under /private/tmp, which macOS's tmp_cleaner empties every night "
                                "of files not accessed, modified or changed for 3 days: a script a cron, monitor or watch "
                                "runs lives in a path that persists, and a watch that cannot run prints FAIL (a user "
                                "organization, 2026-11-14)."),
    ]
    full = "\n".join([head] + ["\n" + t for _, t in sections if t])
    path = context_path(org, p["name"])

    def note(cut):
        what = "; ".join(f"{n} cut to {k:,} of {w:,} characters" if k else f"{n} left out ({w:,} characters)"
                         for n, k, w in cut)
        if unwritten:   # the hook could not write the file: point to the sources themselves (of_review on PR 254)
            src = (f"your brief ({os.path.join(root, p['mandate'])}) and " if p.get("mandate") else "") + \
                f"your organization's rules ({os.path.join(root, 'AGENTS.md')})"
            return (f"OF: this start context is cut to fit Claude Code's 10,000-character limit on a hook's output "
                    f"({what}), and the whole could not be written to a file. Read {src} now, before anything else.")
        return (f"OF: this start context is cut to fit Claude Code's 10,000-character limit on a hook's output ({what}). "
                f"Read the whole context now, before anything else: {path}")
    text, cut = fit(head, sections, cap, note)
    if info is not None:
        info.update(cut=cut, full=full if cut else None, file=path if cut else None, whole=len(full))
    return text
