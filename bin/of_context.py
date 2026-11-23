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


# ---- names inside a session's folder ----
# A session can write its own folder from its sandbox, while of apply and the pulse write there from outside it. So
# every name below a session's folder is opened from the folder's descriptor one component at a time: no link
# followed, a file created only as a new one, a file appended to only when it is a regular file of this user with no
# second name (of_agent-server, message 01a20511-834d).

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIR = getattr(os, "O_DIRECTORY", 0)
# no open waits: a name that is a pipe would hold the pulse until something writes it (of_server, 2026-11-22, on the rules writer's first fix)
_NB = getattr(os, "O_NONBLOCK", 0)


def _dir_fd(base, parts, make=False, make_from=0):
    """A descriptor of BASE/PARTS..., each component opened from the one above without following a link; None when one
    is missing (and not MAKE, or it comes before MAKE_FROM) or is not a folder."""
    try:
        fd = os.open(os.path.realpath(base), os.O_RDONLY | _DIR | _NOFOLLOW | _NB)
    except OSError:
        return None
    for i, part in enumerate(parts):
        try:
            nxt = os.open(part, os.O_RDONLY | _DIR | _NOFOLLOW | _NB, dir_fd=fd)
        except FileNotFoundError:
            if not make or i < make_from:
                os.close(fd)
                return None
            try:
                os.mkdir(part, 0o755, dir_fd=fd)
                nxt = os.open(part, os.O_RDONLY | _DIR | _NOFOLLOW | _NB, dir_fd=fd)
            except OSError:
                os.close(fd)
                return None
        except OSError:
            os.close(fd)
            return None
        os.close(fd)
        fd = nxt
    return fd


def _split(rel):
    parts = [x for x in rel.split(os.sep) if x]
    if not parts or any(x in (".", "..") for x in parts):
        raise ValueError(rel)
    return parts[:-1], parts[-1]


def read_in(base, rel, limit=10 ** 7):
    """BASE/REL's text when it is a regular file reached without a link, else None."""
    dirs, name = _split(rel)
    fd = _dir_fd(base, dirs)
    if fd is None:
        return None
    import stat
    try:
        f = os.open(name, os.O_RDONLY | _NOFOLLOW | _NB, dir_fd=fd)
    except OSError:
        return None
    finally:
        os.close(fd)
    if not stat.S_ISREG(os.fstat(f).st_mode):
        os.close(f)
        return None
    with os.fdopen(f, "rb") as fh:
        data = fh.read(limit + 1)
    return data[:limit].decode("utf-8", "replace")


def write_in(base, rel, text):
    """Write BASE/REL whole: a new file beside it, renamed over the name (which replaces a link, never follows it)."""
    dirs, name = _split(rel)
    fd = _dir_fd(base, dirs, make=True)
    if fd is None:
        raise OSError(f"{os.path.join(base, *dirs)}: not a folder reached without a link")
    tmp = f".{name}.{os.getpid()}.{os.urandom(4).hex()}.tmp"
    try:
        f = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _NB, 0o644, dir_fd=fd)
        with os.fdopen(f, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, name, src_dir_fd=fd, dst_dir_fd=fd)
    except OSError:
        try:
            os.unlink(tmp, dir_fd=fd)
        except OSError:
            pass
        raise
    finally:
        os.close(fd)


def remove_in(base, rel):
    """Remove the name BASE/REL (a link is removed, never what it points at); False when it is not there."""
    dirs, name = _split(rel)
    fd = _dir_fd(base, dirs)
    if fd is None:
        return False
    try:
        os.unlink(name, dir_fd=fd)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


def _exclude(folder, run=None):
    """Keep the rules file out of the folder's git repository: one line in its .git/info/exclude, never a commit. Only
    where .git is a folder of its own: a worktree's or submodule's .git file names its git folder, which the session
    could point anywhere, and no git runs here on a repository a session can write (RUN is kept for callers)."""
    import stat
    try:
        st = os.lstat(os.path.join(os.path.realpath(folder), ".git"))
    except OSError:
        return False
    if not stat.S_ISDIR(st.st_mode):
        return False
    fd = _dir_fd(folder, [".git", "info"], make=True, make_from=1)  # .git is never made here (of_secrets-review)
    if fd is None:
        return False
    line = "/.claude/rules/of-*.md"
    try:
        f = os.open("exclude", os.O_RDWR | os.O_APPEND | os.O_CREAT | _NOFOLLOW | _NB, 0o644, dir_fd=fd)
    except OSError:
        os.close(fd)
        return False
    os.close(fd)
    fs = os.fstat(f)
    # a regular file of this user with one name; 0 names is one replaced after the open (PR 541's rule)
    if not stat.S_ISREG(fs.st_mode) or fs.st_nlink > 1 or fs.st_nlink < 1 or fs.st_uid != os.getuid():
        os.close(f)
        return False
    with os.fdopen(f, "r+", encoding="utf-8", errors="replace") as fh:
        have = fh.read(10 ** 6).splitlines()
        if line not in have:
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
        # each session where it started last: a restart in another folder moves it there. 2026-11-22: a position that
        # changed organization was restarted in the old one's folder and then out of it; its earlier start kept that
        # folder "shared" for the whole window, so the other organization's rules file stayed removed (a user
        # organization's structure keeper, message 01a204f6-9bb4)
        last = {}
        for sid, ts, org, cwd in conn.execute("SELECT session_id, ts, org, cwd FROM event WHERE name = 'of.session_start' "
                                              "AND ts >= ? AND cwd IS NOT NULL AND org IS NOT NULL ORDER BY ts", (since,)):
            last[sid or (org, cwd)] = (org, cwd)
        out = {}
        for org, cwd in last.values():
            out.setdefault(os.path.realpath(os.path.expanduser(cwd)), set()).add(org)
        return out
    except Exception:  # noqa: BLE001 - no store: the registry's folders alone
        return {}
    finally:
        if own and conn is not None:
            conn.close()


def position_starts(conn=None, days=7):
    """{(organization, position): folder} where each position's latest session started in the last DAYS
    (of.session_start); one name in two organizations is two rows (of_review on PR 564)."""
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
        for org, pos, cwd in conn.execute("SELECT org, position, cwd FROM event WHERE name = 'of.session_start' "
                                          "AND ts >= ? AND cwd IS NOT NULL AND position IS NOT NULL ORDER BY ts",
                                          (since,)):
            out[(org, pos)] = cwd
        return out
    except Exception:  # noqa: BLE001 - no store: the registry's folders alone
        return {}
    finally:
        if own and conn is not None:
            conn.close()


def shared_folders(folders, sessions=None):
    """{folder: {organizations}} of the folders where more than one organization's positions run by the registries
    (FOLDERS, from rules_folders). Claude Code loads every rules file of a folder into every session there, so such a
    folder gets none (of_review and of_architect on organic-framework PR 320). A session of another organization that
    only started there takes nothing away: the folder keeps its organization's rules and the session is a stray
    (stray_sessions). 2026-11-22: one restart of another organization's position into a user organization's root
    folder removed that organization's rules file, and eight of its sessions would have started without their
    binding rules (of_architect, message 01a204f7-ef0e). SESSIONS is no longer read; kept for callers."""
    by = folder_owners(folders)
    out = {}
    for d, orgs in by.items():
        # a folder above another organization's registry folder is shared too: the sessions below would load both
        # rules files, its own and this one's (of_review on organic-framework PR 564; PR 320's incident)
        allof = set(orgs) | {o for e, os_ in by.items() if e.startswith(d + os.sep) for o in os_}
        if len(allof) > 1:
            out[d] = allof
    return out


def _nearest(cwd, by):
    """The registry folder nearest above CWD (or CWD itself), or None."""
    best = None
    for d in by:
        if (cwd == d or cwd.startswith(d + os.sep)) and (best is None or len(d) > len(best)):
            best = d
    return best


def folder_owners(folders):
    """{folder: {organizations}} by the registries (FOLDERS, from rules_folders)."""
    by = {}
    for org, dirs in folders.items():
        for d in dirs:
            by.setdefault(os.path.realpath(d), set()).add(org)
    return by


def foreign_orgs(org, cwd, folders):
    """The other organizations whose positions run in CWD or a folder above it by the registries: a session of ORG
    started there would load their rules, and its start would make the folder look shared. of spawn refuses such a
    folder (of_architect, message 01a204f7-ef0e)."""
    if not cwd:
        return set()
    c = os.path.realpath(os.path.expanduser(cwd))
    by = folder_owners(folders)
    near = _nearest(c, by)
    # the nearest registry folder decides, and an organization named there is not foreign (of_review on PR 564)
    if near is None or org in by[near]:
        return set()
    return set(by[near])


def stray_sessions(folders, sessions):
    """{folder: {organizations}}: sessions started in another organization's folder (or below it), where they load
    that organization's rules. Named by of apply, the pulse and of registry check, never acted on."""
    out = {}
    owners = folder_owners(folders)
    for cwd, orgs in sessions.items():
        near = _nearest(cwd, owners)
        extra = set(orgs) - owners[near] if near else set()
        if extra:
            out.setdefault(cwd, set()).update(extra)
    return out


def write_rules(positions, org_paths, dry_run=False, log=print, run=None, sessions=None):
    """Write each organization's rules file into each folder its positions run in, when it changed; in a folder shared
    by two organizations, none, and an earlier one is removed. Returns the paths written (or that would be)."""
    folders = rules_folders(positions, org_paths)
    shared = shared_folders(folders)
    roots = {(p.get("_orgid") or p.get("_org")): p.get("_root") for p in positions}
    written, keep = [], {}   # keep: {folder: the names OF writes there}, only where it has text to write
    owners = folder_owners(folders)
    for cwd, orgs in sorted(stray_sessions(folders, session_orgs() if sessions is None else sessions).items()):
        own = sorted(owners.get(_nearest(cwd, owners)) or ())
        log(f"  a session of {', '.join(sorted(orgs))} started in {cwd}, a folder of {', '.join(own)}: it loads "
            f"{', '.join(own)}'s rules; restart it in its own organization's folder (the folder keeps its rules)")
    for d, orgs in shared.items():
        for org in orgs:
            old = os.path.join(d, RULES_FILE.format(org=org))
            if not dry_run and remove_in(d, RULES_FILE.format(org=org)):
                log(f"  removed {old}: {d} is shared by {', '.join(sorted(orgs))}")
    for org, dirs in folders.items():
        text = rules_file_text(org, os.path.join(roots.get(org) or "", "AGENTS.md"))
        if not text:
            continue   # no rules text: its name is kept nowhere, so a file under it is removed below
        mine = [os.path.realpath(d) for d in dirs if os.path.realpath(d) not in shared]
        for d in dirs:
            r = os.path.realpath(d)
            if r in shared:
                continue
            # Claude Code loads a parent folder's rules too: a folder below another of this organization's gets none,
            # or a session there loads the rules twice
            if any(a != r and r.startswith(a + os.sep) for a in mine):
                inner = os.path.join(d, RULES_FILE.format(org=org))
                if not dry_run and remove_in(d, RULES_FILE.format(org=org)):
                    log(f"  removed {inner}: the folder above holds the same rules")
                continue
            keep.setdefault(r, set()).add(os.path.basename(RULES_FILE.format(org=org)))
            path = os.path.join(d, RULES_FILE.format(org=org))
            if read_in(d, RULES_FILE.format(org=org)) == text:
                continue
            written.append(path)
            if dry_run:
                log(f"  would write {path}")
                continue
            try:
                write_in(d, RULES_FILE.format(org=org), text)
            except OSError as e:
                log(f"  not written {path}: {e}")
                written.pop()
                continue
            try:
                _exclude(d, run)
            except (OSError, ValueError) as e:   # one folder's exclude never stops the others
                log(f"  no exclude line in {d}: {e}")
            log(f"  wrote {path} ({len(text):,} characters of {org}'s binding rules)")
    # a rules file OF did not write, under the name OF uses: a session can leave one, which the exclude line hides
    # and every session there would load as rules; removed in each folder OF writes rules into (of_server,
    # 2026-11-22, on the rules writer's first fix)
    for r in sorted({os.path.realpath(d) for dirs in folders.values() for d in dirs}):
        for name in _rules_names(r):
            if name.startswith("of-") and name.endswith(".md") and name not in keep.get(r, set()) and not dry_run:
                if remove_in(r, os.path.join(os.path.dirname(RULES_FILE.format(org="x")), name)):
                    log(f"  removed {os.path.join(r, '.claude', 'rules', name)}: OF did not write it")
    return written


def _rules_names(folder):
    fd = _dir_fd(folder, os.path.dirname(RULES_FILE.format(org="x")).split(os.sep))
    if fd is None:
        return []
    try:
        return sorted(os.listdir(fd))
    except OSError:
        return []
    finally:
        os.close(fd)


def rules_loaded_from(cwd, org, agents_md):
    """The rules file Claude Code loads for a session in CWD (in CWD or a folder above it, as Claude Code reads them),
    when it holds ORG's rules as OF would write them now; else None (the start context then carries the rules)."""
    text = rules_file_text(org, agents_md)
    if not text or not cwd:
        return None
    d = os.path.realpath(os.path.expanduser(cwd))
    while True:
        path = os.path.join(d, RULES_FILE.format(org=org))
        if read_in(d, RULES_FILE.format(org=org)) == text:
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
        loud = ""
        if not loaded and any(n == "the binding rules" for n, _, _ in cut):
            # only when the section carries the rules themselves: with the file loaded it is a pointer to it, and its
            # cut is no gap (a user organization's structure keeper, message 01a20519-c22a)
            # said first and loudly: a session that runs on part of its organization's rules (of_architect, message
            # 01a204f7-ef0e: 45,000 characters of a user organization's rules against this context's 9,500)
            loud = (f"OF WARNING: your organization's binding rules do not reach this session whole: no current "
                    f"{RULES_FILE.format(org=org)} in {cwd or 'this folder'} or a folder above it. Read the binding "
                    f"sections of {os.path.join(root, 'AGENTS.md')} in full before anything else, and tell your "
                    f"structure keeper: `of apply` writes the file. ")
        if unwritten:   # the hook could not write the file: point to the sources themselves (of_review on PR 254)
            src = (f"your brief ({os.path.join(root, p['mandate'])}) and " if p.get("mandate") else "") + \
                f"your organization's rules ({os.path.join(root, 'AGENTS.md')})"
            return loud + (f"OF: this start context is cut to fit Claude Code's 10,000-character limit on a hook's "
                           f"output ({what}), and the whole could not be written to a file. Read {src} now, before "
                           f"anything else.")
        return loud + (f"OF: this start context is cut to fit Claude Code's 10,000-character limit on a hook's output ({what}). "
                f"Read the whole context now, before anything else: {path}")
    text, cut = fit(head, sections, cap, note)
    if info is not None:
        info.update(cut=cut, full=full if cut else None, file=path if cut else None, whole=len(full))
    return text
