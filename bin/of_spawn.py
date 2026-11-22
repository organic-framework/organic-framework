"""of spawn <position>: start a position's session with its brief, declared by construction (of_architect, 2026-11-06).

Today 4 of 5 spawns typed the position brief undeclared, so it counted as the founder's words. This tool:
- starts the session in a new herdr pane of the position's place (its workspace and a tab named by its system, as
  `of apply` lays them out; `of apply` then tidies the layout), never nested in the caller's pane;
- picks the account (config dir) with the most headroom from of_capacity's status, unless --config-dir is given;
- works in the position's folder: the first repository of its system under the organization's paths, unless --cwd;
- stops before launching when Claude Code would show the folder-trust dialog in that account, and files a task for
  the founder naming the folder (trusting a folder is his act, never done for him);
- starts Claude Code with a fixed session id, then types the brief through the relay's herdr_deliver, which
  declares it as of.injected_prompt for that session and neutralizes at signs;
- names the agent, runs `of apply`, prints the session id and the registry line for the structure keeper (writing
  holder.session stays with the keeper).
The pure parts (plan) take the registry and herdr's state as arguments; run() does the side effects.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import time
import uuid


class SpawnError(Exception):
    pass


# said only where the organization's registry carries its owner's yes (hand-over ff0f6f09; docs/registry.md, Subagents)
SUBAGENTS_LINE = ("Your owner asks you to run the independent parts of your work as subagents, in parallel "
                  "(docs/method.md, Subagents; your organization's registry, subagents:).")


def brief_text(p, org_root, subagents=False):
    """The brief, typed as the session's first prompt. SUBAGENTS: the organization's registry carries its owner's
    standing yes to subagents (bin/of org_subagents; docs/registry.md, "Subagents")."""
    mandate = p.get("mandate") or f"registry/briefs/{p['name']}.md"
    path = mandate if os.path.isabs(os.path.expanduser(mandate)) else os.path.join(org_root, mandate)
    home = os.path.expanduser("~")
    path = "~" + path[len(home):] if path.startswith(home) else path
    return (f"You hold the position {p['name']}. Read {path} and the organization's AGENTS.md, and follow them. "
            + (f"{SUBAGENTS_LINE} " if subagents else "")
            + f"At the end of every turn: of status working|waiting --on founder|<position>|idle|done.")


def clone_guard(orgs_file=None):
    """A predicate: is a path, resolved, a named work clone, or does it hold or lie in one, refused or not? The
    readers outside the sandbox that pick a folder (work_dir, doc_path) never take such a path (of_review on PR 551:
    checked where the folder is picked, not predicted when the clone is named)."""
    clones = [c for _, c, _, _ in work_clones(orgs_file, include_refused=True)]

    def near(q):
        return any(q == c or q.startswith(c + os.sep) or c.startswith(q + os.sep) for c in clones)

    def hit(path):
        # the whole way there, not only where it ends: the path as written, and each step of it resolved
        # absolute but not normalised: a parent step after a link goes where the link leads, as the open goes, so
        # the resolve and the steps take the path as given; its lexical form is one more check (of_review on PR 551)
        raw = os.path.expanduser(str(path))
        if not os.path.isabs(raw):
            raw = os.path.join(os.getcwd(), raw)
        if near(os.path.abspath(raw)) or near(os.path.realpath(raw)):
            return True
        here, todo, links = os.sep, raw.split(os.sep), 0   # link by link, as the open goes: realpath shows only ends
        while todo:
            part = todo.pop(0)
            if part in ("", "."):
                continue
            if part == "..":
                here = os.path.dirname(here)
                continue
            step = os.path.join(here, part)
            if any(step == c or step.startswith(c + os.sep) for c in clones):
                return True
            try:
                target = os.readlink(step) if os.path.islink(step) else None
            except OSError:
                return bool(clones)   # it changed under the check: refused
            if target is None:
                here = step
                continue
            links += 1
            if links > 40:
                return bool(clones)   # no open resolves it: refused
            if os.path.isabs(target):
                here = os.sep
            todo = target.split(os.sep) + todo
        return False
    return hit


def work_dir(p, systems, org_paths, exists=os.path.isdir):
    """The position's folder: the first repository of its system that exists under the organization's paths (as a
    child of a path, or as a sibling of it), else the organization's first path. Never a named work clone."""
    repos = ((systems or {}).get(p.get("system")) or {}).get("repos") or []
    roots = [os.path.expanduser(str(x)) for x in org_paths or []]
    in_clone = clone_guard()
    for repo in repos:
        for root in roots:
            for cand in (os.path.join(root, repo), os.path.join(os.path.dirname(root), repo)):
                if exists(cand) and not in_clone(cand):
                    return cand
    for root in roots:
        if exists(root) and not in_clone(root):
            return root
    raise SpawnError(f"no working folder for {p['name']}: system {p.get('system')!r} names no repository under "
                     f"{', '.join(org_paths or []) or 'the organization paths'}; pass --cwd")


def place(p, want_workspace):
    """(workspace label, tab label): the position's workspace and a tab named by its system (as `of apply` names
    tabs; it moves the pane into its final tab when the layout differs)."""
    return want_workspace(p), str(p.get("system") or p.get("role") or p["name"])


def _stem(pattern):
    """A glob's literal prefix, up to its first wildcard."""
    m = re.search(r"[*?[]", pattern)
    return pattern[:m.start()] if m else pattern


def deny_rules(position, org, cat, named=None, roots=None):
    """The session's own Claude Code deny rules from the credentials catalogue (docs/registry.md, Credentials; option
    2 of of_architect's decision, 2026-11-11), each layer switched with the guard's own (G14) so both deny together:
    - named (of_credentials.MODE_NAMED "deny"): Read and Edit of every credential path the position neither lists
      nor keeps;
    - roots (of_credentials.MODE_ROOT "deny"): the whole of each credential root under which it may use nothing
      (another organization's roots, and its own when it lists nothing there). A root it uses a file under stays
      with the guard for good: in Claude Code a deny beats an allow, so a root cannot be denied with the allowed
      files carved out.
    True or False forces a layer (tests). Deny rules hold in every mode, --dangerously-skip-permissions included
    (they are checked before the mode), and cover cat/head/sed in Bash too; a script that opens the file itself is
    not covered, which is what the guard and, later, a user per position are for."""
    import of_credentials
    if named is None:
        named = getattr(of_credentials, "MODE_NAMED", "warn") == "deny"
    if roots is None:
        roots = getattr(of_credentials, "MODE_ROOT", "warn") == "deny"
    v = of_credentials.position_view(position, org, cat)
    paths = set(v["denied"]) if named else set()
    for ent in (cat.values() if roots else ()):
        for r in ent.get("roots") or []:
            r = r.rstrip("/")
            if r and not any(_stem(a) == r or _stem(a).startswith(r + "/") or r.startswith(_stem(a))
                             for a in v["allowed"]):
                paths.add(r + "/**")
    rules = []
    for p in sorted(paths):
        anchored = "/" + p if p.startswith("/") else p  # //abs is absolute; a single / anchors at the settings source
        rules += [f"Read({anchored})", f"Edit({anchored})"]
    return rules


# Every agent session starts with these (spawn, resume-command, a capacity move). feedbackDrafts "off": Claude Code's
# SendFeedback tool queues drafts for a person to review, nobody reviews them in an agent's pane, and a queued draft
# makes /exit open the drafts' review first; on 2026-11-14 22:50Z that held up a production keeper's move off a spent
# account (of_capacity's proposal; the setting is Claude Code's own: notify, quiet or off).
# env GIT_CEILING_DIRECTORIES = the home folder: git never climbs into the home folder looking for a repository, so a
# stray ~/.git (2026-11-15: an agent's cd with an empty variable, then git init) cannot make every git call under home
# walk the whole home folder; a repository below home is found as before.
# env CLAUDE_CODE_AUTO_COMPACT_WINDOW = 1000000, the window every account's settings already hold: Claude Code offers
# "We recommend compacting earlier" (400k or keep 1m) only when the window comes from settings, never from this variable
# or the --autocompact flag (read in Claude Code 2.1.329's own code). Its dialog held of_lab's input box and every
# delivery the relay typed for it on 2026-11-19 22:50Z (of_architect, msg 01a1f605-7981), and an agent's context must
# not be cut to 400k (the founder, 2026-11-18, prompt 01a1eeff-880b).
AGENT_SETTINGS = {"feedbackDrafts": "off"}
AUTO_COMPACT_WINDOW = "1000000"


def agent_env():
    """The environment every agent session starts with (settings `env`): GIT_CEILING_DIRECTORIES, the home folder, and
    CLAUDE_CODE_AUTO_COMPACT_WINDOW, so no compaction offer stops the session's input."""
    return {"GIT_CEILING_DIRECTORIES": os.path.expanduser("~"), "CLAUDE_CODE_AUTO_COMPACT_WINDOW": AUTO_COMPACT_WINDOW}


# Claude Code's sandbox for every agent session on Linux, the agent host (layer 3 of organic-framework-build's
# reports/agent-host-isolation-2026-11-18.md; the founder accepted the plan, of_conductor msg 01a1efaf-43fd). Its keys
# are Claude Code's own (code.claude.com/docs/en/sandboxing): on, failing at start rather than running unsandboxed,
# with no per-command escape. Bash's reads are denied for the session's credential deny paths (the same catalogue as
# its Read deny rules, which bind the file tools only) and for what of_server listed (its message 01a1efa4-fddf:
# sign-ins, keys, OF's own server and ClickHouse files); writes are allowed into OF's state folder, which of status,
# of send and the recorder write; every Unix socket is reachable (of, herdr, the relay through the forward); the
# network only to SANDBOX_DOMAINS, with strictAllowlist on. of_agent-server tried exactly this block on the agent host on 2026-11-20 under of (its msg
# 01a1f88b-5471): the denied reads and writes failed, herdr, of who and of asks worked. macOS keeps running without it
# until it is tried there. Known gap (docs/guard.md, Gaps): on Linux the sandbox opens every Unix socket or none
# (allowUnixSockets is macOS-only), and of, herdr and the relay need theirs, so a command can reach the systemd user
# bus, herdr or of-server and read through them past denyRead. `systemctl --user` failed to connect in one session on
# the host (of_agent-server, message 01a202cd-f226); whether the bus socket answers was not observed.
SANDBOX_DENY_READ = ("~/.ssh", "~/.codex", "~/.config/of/service-accounts", "~/.config/of/grant_ed25519",
                     "~/.config/of/clickhouse", "~/.config/of/clickhouse-passwords", "~/.config/of/move",
                     "~/.local/state/of/server")


# the hosts a sandboxed command may reach (of_review on organic-framework PR 457: agent sessions run in bypass mode,
# where Claude Code lets every host through unless strictAllowlist is on): GitHub for gh and git, and OpenAI for a
# Codex subagent; a proposal to be widened by what the host's trial shows missing
SANDBOX_DOMAINS = ("github.com", "api.github.com", "codeload.github.com", "uploads.github.com",
                   "objects.githubusercontent.com", "raw.githubusercontent.com",
                   "api.openai.com", "chatgpt.com", "auth.openai.com")
PLATFORM = sys.platform   # tests pin it: CI runs on Linux


def _accounts_dirs():
    try:
        import of_accounts
        return of_accounts.dirs()
    except Exception:  # noqa: BLE001 - no list: the folders found by name
        import glob
        return sorted(glob.glob(os.path.expanduser("~/.claude*")))


# What git or Claude Code runs or reads as configuration in a repository, never written from a session in its work
# clone: hooks, config (a hook path, a filter, an fsmonitor), info (attributes, excludes), submodules, worktrees (their
# config.worktree and commondir), and the folder's own Claude Code settings (of_server's conditions for a work clone,
# message 01a2044e-a1e4; hand-over ab9ed07b)
# .git/commondir is denied too, and must exist: a session that could create it would have git take the repository's
# config and hooks from a folder it wrote, around every other entry here. Holding "." it changes nothing for git, and
# `of org set <org> work_clone` writes it; a clone without it gets no allowWrite (of_review on PR 542, second round:
# absent, the Linux sandbox would cover it with an empty file, on which git dies)
CLONE_CONTROL = (".git/config", ".git/config.worktree", ".git/commondir", ".git/hooks", ".git/info", ".git/modules",
                 ".git/worktrees", ".claude", ".mcp.json")


def clone_ready(clone):
    """Does CLONE hold .git/commondir, a regular file that is no link, holding "."? Only then is it writable."""
    p = os.path.join(clone, ".git", "commondir")
    try:
        if os.path.islink(p) or not os.path.isfile(p):
            return False
        with open(p) as fh:
            return fh.read().strip() == "."
    except OSError:
        return False


def _orgs_file():
    return os.environ.get("OF_ORGS") or os.path.join(os.environ.get("OF_CONFIG_DIR") or
                                                     os.path.expanduser("~/.config/of"), "orgs.yaml")


def clone_refused(clone, entries, base=None):
    """Why CLONE may not be a work clone, or None: it is, holds or lies in any organization's path or checkout (orgs.yaml
    `path`, `paths`), the checkout OF runs from, or the home itself; a sandbox's allowWrite would open those (of_review
    on PR 542, point 4). ENTRIES: orgs.yaml's organization entries; BASE: the folder relative paths start from."""
    home = os.path.realpath(os.path.expanduser("~"))
    if clone == home:
        return "the home folder itself"
    import of_events
    # OF's own checkout, configuration, state and log folders, every account's folder, the SSH folder and the user's
    # systemd units: allowWrite would open orgs.yaml, which this design trusts, or a key (of_review on PR 542)
    guarded = list(of_checkouts()) + [os.path.realpath(os.path.expanduser(x)) for x in
                                      (os.environ.get("OF_CONFIG_DIR") or "~/.config/of", "~/.ssh",
                                       "~/.config/systemd/user", "~/Library/LaunchAgents")]
    guarded += [os.path.realpath(x) for x in (of_events.STATE_DIR, getattr(of_events, "LOG_DIR", None) or
                                              os.path.expanduser("~/.local/log/of"))]
    guarded += [os.path.realpath(x) for x in _accounts_dirs()]
    roots = []
    for o in entries:
        for q in [o.get("path")] + list(o.get("paths") or []):
            if q:
                guarded.append(os.path.realpath(os.path.join(base or "", os.path.expanduser(str(q)))))
        roots += [os.path.realpath(os.path.join(base or "", os.path.expanduser(str(q)))) for q in o.get("paths") or []]
    # a sibling of a checkout root: of spawn's work folder and the document paths look for a repository of the
    # organization beside its checkouts, so a clone there would be adopted outside the sandbox (of_agent-server's
    # check on the agent host, message 01a204aa-94a7, point C)
    parent = os.path.dirname(clone)
    hit = next((r for r in roots if os.path.dirname(r) == parent and r != clone), None)
    if hit:
        return f"it lies beside {hit}, where OF looks for the organization's repositories"
    for g in guarded:
        if clone == g or clone.startswith(g + os.sep) or g.startswith(clone + os.sep):
            return f"it is, holds or lies in {g}"
    return None


def work_clones(orgs_file=None, include_refused=False):
    """[(organization, its work clone, its services' copy)] for each organization whose entry in this machine's
    orgs.yaml names a `work_clone` (card OF-265, the founder's yes; hand-over ab9ed07b): a clone of its repository its
    own sessions may write from their sandbox, while the copy its services and the registry run from (`path`) stays
    denied. orgs.yaml is the owner's file, which no agent writes (docs/guard.md G33); the registry follows agents'
    pushes, so it never names one. A clone that clone_refused refuses is left out, unless INCLUDE_REFUSED (then each
    row ends with the refusal, or None): its copy stays denied all the same. Outside the sandbox only `of diff`, in a
    session, reads it (of_server, message 01a20471-8284)."""
    import yaml
    f = orgs_file or _orgs_file()
    try:
        with open(f) as fh:
            doc = yaml.safe_load(fh) or {}
    except (OSError, ValueError, yaml.YAMLError):
        return []
    base = os.path.dirname(os.path.abspath(f))
    entries = [o for o in ((doc.get("orgs") if isinstance(doc, dict) else None) or []) if isinstance(o, dict)]
    out = []
    for o in entries:
        if not o.get("work_clone") or not o.get("path"):
            continue
        clone = os.path.realpath(os.path.join(base, os.path.expanduser(str(o["work_clone"]))))
        svc = os.path.realpath(os.path.join(base, os.path.expanduser(str(o["path"]))))
        refused = clone_refused(clone, entries, base)
        if include_refused or not refused:
            out.append((o.get("name") or os.path.basename(svc), clone, svc) + ((refused,) if include_refused else ()))
    return out


def _muse_dir():
    try:
        import of_subagent
        return of_subagent.muse_login_dir()
    except Exception:  # noqa: BLE001
        return None


def _server_keys(path=None):
    """The of-server's own keys under this user, from its server.yaml as bin/of_server.py loads them: the tenant's
    master key (the masking key derives from it), a secret_key_file, and the TLS private key. The guard denies *.key by
    name only, and the sandbox is for the reads it does not see (of_server on organic-framework PR 457). What the
    user's own of commands read stays open: machine_ed25519, the pins under servers, server.yaml itself."""
    cfg = {}
    path = path or os.path.expanduser("~/.config/of/server.yaml")
    try:
        import yaml
        with open(path) as fh:
            cfg = yaml.safe_load(fh) or {}
    except (OSError, Exception):  # noqa: BLE001 - no server here: its default key path still counts
        cfg = {}
    out = [cfg.get("tenant_key_file") or "~/.config/of/tenant.key", cfg.get("secret_key_file")]
    if isinstance(cfg.get("tls"), dict):
        out.append(cfg["tls"].get("key"))
    return [os.path.expanduser(str(x)) for x in out if x]


# the files and folders Claude Code runs or reads as configuration outside the sandbox, in any folder: settings (and the
# hooks in them), hook scripts, skills, agents, commands, workflows and MCP servers
PROTECTED_EDITS = ("**/.claude/settings.json", "**/.claude/settings.local.json", "**/.claude/hooks/**",
                   "**/.claude/skills/**", "**/.claude/agents/**", "**/.claude/commands/**", "**/.claude/workflows/**",
                   "**/.mcp.json")


def _is_checkout(d):
    return os.path.isfile(os.path.join(d, "bin", "of")) and os.path.exists(os.path.join(d, ".git"))


def of_checkouts():
    """The checkouts OF itself runs from: the one `~/.local/bin/of` leads to, where the units (of-server, of-relay,
    of-listen) and bin/of-hook run outside any sandbox, and the one this file is in, when they differ. A session that
    writes one, its .git included, changes code that then runs unsandboxed (of_agent-server's finding on the agent host,
    message 01a202cd-f226; of_server's Gaps entry in docs/guard.md, message 01a202d3-e7c8). `~/.local/bin/of` is a
    symbolic link, or a wrapper script that execs a checkout's bin/of (deploy/host/plane.py); a folder it leads to counts
    only when it holds bin/of and .git, so a wrapper never makes ~/.local the checkout and denies OF's state folder
    (of_secrets-review on PR 509). Spawn's own tree counts whatever it holds: an export has no .git (of_review)."""
    link = os.path.expanduser("~/.local/bin/of")
    own = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))   # the code running now: always, whatever it holds
    found = []
    if os.path.islink(link):
        found.append(os.path.dirname(os.path.dirname(os.path.realpath(link))))
    elif os.path.isfile(link):
        try:
            with open(link, errors="replace") as fh:
                text = fh.read(4096)
        except OSError:
            text = ""
        found += [os.path.realpath(m) for m in re.findall(r"(/[^\s'\"]*)/bin/of(?=[\s'\"]|$)", text, re.M)]
    return list(dict.fromkeys([d for d in found if _is_checkout(d)] + [own]))


def sandbox_settings(deny=None, platform=None, scratch=None, org=None):
    """The session's `sandbox` block on Linux, else None. DENY: its Read/Edit deny rules (`Read(//abs/path/**)`).
    SCRATCH: (its working folder, its session id), so its own scratch folder stays readable while the other sessions'
    are denied."""
    if not (platform or PLATFORM).startswith("linux"):
        return None
    import glob
    import of_scratch
    paths = {os.path.expanduser(x) for x in SANDBOX_DENY_READ}
    # every account's sign-in and every session's transcripts (of_server's list, msg 01a1efa4-fddf: transcripts hold
    # whatever any session read), from OF's own account list: Claude Code reads them itself, outside the sandbox. So
    # an `of` command that reads transcripts (of capacity, of resume-command) does not work from a sandboxed Bash; it
    # runs from the listener or a person's terminal (of_review on organic-framework PR 457)
    for d in _accounts_dirs():
        paths |= {os.path.join(d, ".credentials.json"), os.path.join(d, "projects")}
    if _muse_dir():
        paths.add(_muse_dir())   # Muse's own home and its keychain (of_server's list)
    paths |= set(_server_keys())
    # the other sessions' scratch folders, each by name as they stand when this session starts: the temp root itself
    # must stay writable, since the sandbox writes its bridge sockets there and $TMPDIR points into it, and a writable
    # folder stays readable whatever denyRead says (tried on the agent host under of, 2026-11-20, in bypass mode:
    # the root denied for writes, the sandbox did not start; the root denied for reads, another session's file read).
    # A session that starts later is not in the list (docs/guard.md, Gaps)
    base = of_scratch.base_dir()
    own = set()
    if scratch:
        cwd, sid = scratch
        if cwd and sid:
            own.add(os.path.join(base, of_scratch._encode(cwd), sid))   # the folder as Claude Code names it
        own |= set(of_scratch.folders(sid)) if sid else set()
    paths |= {d for d in glob.glob(os.path.join(base, "*", "*")) if os.path.isdir(d) and d not in own
              and not d.startswith(os.path.join(base, "bundled-skills"))}
    allow = sorted(own)
    for r in deny or []:
        if r.startswith("Read(") and r.endswith(")"):
            q = r[5:-1]
            q = q[1:] if q.startswith("//") else q          # //abs is absolute in a permission rule
            q = re.sub(r"/\*\*$", "", q)                  # a root's whole tree: the sandbox takes the folder
            if not q.startswith("/"):
                continue
            if re.search(r"[*?[]", q):
                # any other glob: the files it matches when the session starts (a sandbox path is no glob; one made
                # later is the deny rule's and the guard's; of_secrets-review on organic-framework PR 457)
                paths |= set(glob.glob(q, recursive=True))
            else:
                paths.add(q)
    denied = sorted(paths)
    # Bash may not write what it may not read: OF's state folder is writable and holds its server folder (the same
    # review); every denied path is denied for writes too, and so is the guard's session index, which only the hook
    # writes, outside the sandbox (docs/guard.md G36; hand-over a18341af), and the checkout OF runs from, whole, since
    # its .git's objects and refs are code too (of_checkouts; a host position works in its own clone, never in it)
    # a work clone the owner named: writable, its control files never, and the services' copy denied (hand-over
    # ab9ed07b); a clone not there gets nothing, its services' copy is denied all the same
    # ORG's clone only, and only with its commondir in place; every copy of an organization that names a clone denied,
    # its clone refused or not (of_review on PR 542)
    writable = [c for o, c, _ in work_clones() if o == org and org and os.path.isdir(c) and clone_ready(c)]
    guarded = [svc for _, _, svc, _ in work_clones(include_refused=True)] + \
        [os.path.join(c, x) for c in writable for x in CLONE_CONTROL]
    return {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
            "autoAllowBashIfSandboxed": True,
            "filesystem": {"denyRead": denied,
                           "denyWrite": denied + [os.path.expanduser("~/.local/state/of/index.json")]
                           + of_checkouts() + [g for g in guarded if g not in of_checkouts()],
                           "allowRead": allow,
                           "allowWrite": [os.path.expanduser("~/.local/state/of")] + writable},
            "network": {"allowAllUnixSockets": True, "strictAllowlist": True,
                        "allowedDomains": list(SANDBOX_DOMAINS)}}


def session_settings(memory=None, deny=None, extra=None, platform=None, scratch=None, org=None):
    """The one --settings JSON: AGENT_SETTINGS, the memory directory (bin/of_memory.py, RFC 0006), the credential
    deny rules, on Linux the sandbox block (sandbox_settings), and extra keys such as the tool profile's
    allowedMcpServers and enabledPlugins (of_tools.launch_parts, docs/tools.md §4)."""
    s = dict(AGENT_SETTINGS, env=agent_env())
    s.update(json.loads(memory) if memory else {})
    if deny:
        s.setdefault("permissions", {}).setdefault("deny", []).extend(deny)
    box = sandbox_settings(deny, platform, scratch, org)
    if box:
        s["sandbox"] = box
        # the sandbox binds Bash only; the file tools, MCP servers and command hooks run outside it
        # (code.claude.com/docs/en/sandboxing): the file tools write only inside the working folders, and never the
        # checkout OF runs from (of_server, message 01a202d3-e7c8; Claude Code 2.1.314 and later)
        perms = s.setdefault("permissions", {})
        perms["blockWritesOutsideWorkingDirectories"] = True
        rules = perms.setdefault("deny", [])
        rules += [f"Edit(/{c}/**)" for c in of_checkouts()]
        # and never, in any folder, what Claude Code itself runs or reads as configuration outside the sandbox: one
        # Write of a hook into the session's own .claude/settings.local.json would let its next tool call write the
        # checkout (of_secrets-review on PR 509; docs/guard.md, Gaps). The sandbox keeps these from Bash already.
        rules += [f"Edit({x})" for x in PROTECTED_EDITS if f"Edit({x})" not in rules]
        # the Read tool runs outside the sandbox, so it reads what denyRead keeps from Bash: each such path is a Read
        # deny rule too (of_server, message 01a202e4-62f9). An account's projects folder also holds the session's own
        # saved tool results, which Claude Code opens with Read, and a deny cannot carve them out (of_review on PR
        # 507): there the rule takes the transcripts only, and the other sessions' saved results stay open to Read.
        # A folder takes one rule, its tree; a file, or a path not there yet, takes both
        projects = {os.path.join(d, "projects") for d in _accounts_dirs()}
        rules += [r for p in box["filesystem"]["denyRead"]
                  for r in ((f"Read(/{p}/**/*.jsonl)",) if p in projects else
                            (f"Read(/{p}/**)",) if os.path.isdir(p) else (f"Read(/{p})", f"Read(/{p}/**)"))
                  if r not in rules]
        # the session's own scratch folders stay writable to the file tools: the memory directory is exempt, a
        # scratchpad is not (tried by of_server on Claude Code 2.1.330)
        if box["filesystem"]["allowRead"]:
            perms["additionalDirectories"] = list(box["filesystem"]["allowRead"])
        # the work clone for the file tools too, and its control files and the services' copy denied to them
        writable = [c for o, c, _ in work_clones() if o == org and org and os.path.isdir(c) and clone_ready(c)]
        if writable:
            perms["additionalDirectories"] = list(dict.fromkeys(perms.get("additionalDirectories", []) + writable))
        for _, _, svc, _ in work_clones(include_refused=True):
            if f"Edit(/{svc}/**)" not in rules:
                rules.append(f"Edit(/{svc}/**)")
        for c in writable:
            for x in CLONE_CONTROL:
                rules += [r for r in (f"Edit(/{c}/{x})", f"Edit(/{c}/{x}/**)") if r not in rules]
    for k, v in (extra or {}).items():
        if k == "env" and isinstance(v, dict):
            s["env"] = dict(s.get("env") or {}, **v)  # a tool profile's env joins the agents' own
        else:
            s[k] = v
    return json.dumps(s) if s else None


def settings_file(settings, sid, state_dir=None):
    """Write the session's --settings JSON to a file and return its path (mode 600; paths and rules only, never a
    value). The command line then stays short: a fresh pane's shell is still in canonical mode, where the terminal
    takes at most 1,024 bytes a line, and 26 credential paths made a 4.9 KB line that herdr refused
    (of_architect, 2026-11-11: pane_send_failed when spawning of_onboarding)."""
    if state_dir is None:
        import of_events
        state_dir = of_events.STATE_DIR
    d = os.path.join(state_dir, "spawn")
    os.makedirs(d, mode=0o700, exist_ok=True)
    path = os.path.join(d, f"{sid}.settings.json")
    tmp = f"{path}.{os.getpid()}"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as fh:
        fh.write(settings)
    os.replace(tmp, path)
    data = settings.encode()
    WRITTEN[os.path.realpath(path)] = (__import__("hashlib").sha256(data).hexdigest(), len(data))
    return path


WRITTEN = {}   # path -> (sha256, bytes) of what settings_file wrote in this process, for record_settings


def record_settings(pl, state_dir=None, emit=None, by="of spawn"):
    """Record the session's settings file as it is launched: of.session_settings with the sha256 and size of what this
    process wrote (settings_file), and the file's name. The event reaches the organization's server, so a session
    that rewrites its own or another session's file under OF's state folder, or backdates it, shows against it
    (of_server, message 01a20337-fe93: the bootstrap's second run tells which sessions run on which settings; a deny
    would stop a structure keeper spawning from its own session). The digest is of the bytes written, never of the
    file read back: what another session wrote between the plan and the launch would read as this writer's own
    (of_review on PR 516); when the file on disk differs at launch, its own digest is recorded beside, as disk_sha256.
    `of resume-command` records it too (BY), for herdr's own resume. It shows a session started on settings other
    than those written for it, not one that forges both the file and the event: the event is shipped from the same
    writable store (of_server, message 01a2033f-5402). A record counts for a session only with a later
    of.session_start of that session: resume-command only prints a line. Returns the digest, or None when the
    session has no settings file."""
    import hashlib
    import of_events
    path = os.path.join(state_dir or of_events.STATE_DIR, "spawn", f"{pl['session']}.settings.json")
    try:
        with open(path, "rb") as fh:
            disk = fh.read()
    except OSError:
        disk = None
    written = WRITTEN.get(os.path.realpath(path))
    if written is None and disk is None:
        return None
    digest, size = written or (hashlib.sha256(disk).hexdigest(), len(disk))
    on_disk = hashlib.sha256(disk).hexdigest() if disk is not None else None
    (emit or of_events.emit)("session_settings", {"session": pl["session"], "position": pl["position"],
                                                  "sha256": digest, "bytes": size, "by": by,
                                                  "file": os.path.basename(path),
                                                  "written": written is not None,
                                                  **({"disk_sha256": on_disk} if on_disk != digest else {})},
                             position=pl["position"], session=pl["session"], org=pl.get("org"))
    return digest


def launch_command(cwd, config_dir, sid, name, default_dir, model=None, bypass=True, memory=None, deny=None,
                   state_dir=None, tools=None, effort=None, org=None):
    """memory: the --settings JSON naming the position's memory directory (bin/of_memory.py, RFC 0006); deny: the
    credential deny rules (deny_rules); tools: (argv, settings keys) of the position's tool profile
    (of_tools.launch_parts). The settings go into one settings file (settings_file)."""
    env = ["env", "-u", "CLAUDE_CONFIG_DIR"] if os.path.realpath(config_dir) == os.path.realpath(default_dir) \
        else [f"CLAUDE_CONFIG_DIR={shlex.quote(config_dir)}"]
    parts = ["cd", shlex.quote(cwd), "&&", *env, "OF_SPAWNED=1", "claude", "--session-id", sid, "-n", name]
    tool_argv, tool_settings = tools or ([], {})
    settings = session_settings(memory, deny, tool_settings, scratch=(cwd, sid), org=org)
    if settings:
        parts += ["--settings", shlex.quote(settings_file(settings, sid, state_dir))]
    parts += [shlex.quote(a) for a in tool_argv]
    if model:
        parts += ["--model", model]
    if effort:
        parts += ["--effort", effort]
    if bypass:
        parts.append("--dangerously-skip-permissions")
    return " ".join(parts)


def plan(p, positions, agents, bind, systems, org_paths, org_root, want_workspace, config_dir=None, cwd=None,
         capacity_rows=None, best_target=None, trusted=None, default_dir=os.path.expanduser("~/.claude"),
         model=None, bypass=True, sid=None, memory=None, deny=None, accepted=None, tools=None, effort=None,
         subagents=False):
    """Everything run() needs, or SpawnError. trusted(config_dir, cwd) -> key or None; accepted(config_dir) -> whether
    the account accepted the Bypass Permissions warning (bypass_accepted); tools(position, sid, config_dir) ->
    (argv, settings keys) of its tool profile (of_tools.launch_parts)."""
    live = {q["name"]: a for q, a in bind(positions, agents)[0]}
    if p["name"] in live:
        a = live[p["name"]]
        raise SpawnError(f"{p['name']} already has a live holder in pane {a.get('pane_id')}; spawn only a position "
                         f"without one (of who)")
    if ((p.get("holder") or {}).get("runtime") or "claude-code") not in ("claude-code", "claude"):
        raise SpawnError(f"{p['name']} is held by {(p.get('holder') or {}).get('runtime')} "
                         f"({(p.get('holder') or {}).get('person') or 'a person'}); of spawn starts Claude Code sessions only")
    if p.get("status") in ("parked", "finished"):
        raise SpawnError(f"{p['name']} is {p['status']} in the registry; its keeper resumes it first")
    cwd = os.path.expanduser(cwd) if cwd else work_dir(p, systems, org_paths)
    if not config_dir:
        rows = capacity_rows or []
        # an account that trusts the folder first: a founder's task to trust it costs more than some headroom
        ok = [r for r in rows if trusted and trusted(os.path.expanduser(r["config_dir"]), cwd)] if trusted else []
        t = (best_target(ok) if ok else None) or (best_target(rows) if best_target else None)
        if not t:
            raise SpawnError("no logged-in account with headroom in of_capacity's status; pass --config-dir")
        config_dir = t["config_dir"]
    config_dir = os.path.expanduser(config_dir)
    sid = sid or str(uuid.uuid4())
    ws, tab = place(p, want_workspace)
    return {"position": p["name"], "org": p.get("_org"), "cwd": cwd, "config_dir": config_dir, "session": sid,
            "workspace": ws, "tab": tab, "brief": brief_text(p, org_root, subagents),
            "trusted": trusted(config_dir, cwd) if trusted else None,
            "bypass_ok": (not bypass) or bool(accepted(config_dir)) if accepted else True,
            "deny": list(deny or []),
            "command": launch_command(cwd, config_dir, sid, p["name"], default_dir, model, bypass, memory, deny,
                                      tools=tools(p["name"], sid, config_dir) if tools else None, effort=effort,
                                      org=p.get("_org"))}


def git_root(path, exists=os.path.exists, isdir=os.path.isdir):
    """(root, linked): the git repository that holds path (the nearest folder with a .git), and whether its .git is a
    file (a linked worktree or a submodule); (None, False) outside git."""
    d = os.path.abspath(path)
    while True:
        g = os.path.join(d, ".git")
        if exists(g):
            return d, not isdir(g)
        up = os.path.dirname(d)
        if up == d:
            return None, False
        d = up


def trusted_with_parents(config_dir, cwd, trusted_key, root_of=git_root):
    """The trusted folder key for cwd in that account, as Claude Code 2.1.319 decides it (its dy/cy): the folder
    itself; inside a git repository, a trusted folder between it and the repository's root, never above the root;
    in a linked worktree, the root alone; outside git, a trusted parent (here only up to the home folder).
    2026-11-09 (acme_org): ~/acme was trusted in ~/.claude-d and ~/acme/ansible-creek, a repository of
    its own, was not; the old rule (any parent, from acme_org's 2026-11-07 reading) launched net_outage-fix into the
    trust dialog. When in doubt this answers None: of spawn then stops and files the founder's task."""
    k = trusted_key(config_dir, cwd)
    if k:
        return k
    root, linked = root_of(cwd)
    if root and linked:
        return root if root != os.path.abspath(cwd) and trusted_key(config_dir, root, [root]) else None
    d, floor = os.path.dirname(os.path.abspath(cwd)), root or os.path.expanduser("~")
    while d and d != os.path.dirname(d) and len(d) >= len(floor):  # up to the repository's root, else home
        if trusted_key(config_dir, d, [d]):
            return d
        d = os.path.dirname(d)
    return None


def registry_line(pl, pane):
    return (f"  holder: {{runtime: claude-code, session: {pl['session']}, "
            f"workplace: \"herdr local, workspace {pl['workspace']}, tab {pl['tab']}, pane {pane}\"}}")


def owner_language():
    """The person's language (of_locale.language: ~/.config/of/owner.yaml `language`, English by default)."""
    import of_locale
    return of_locale.language()


def _claude_line(pl, flag=""):
    acct = pl["config_dir"]
    env = "" if acct.rstrip("/").endswith("/.claude") else f"CLAUDE_CONFIG_DIR={acct} "
    return f"cd {pl['cwd']} && {env}claude{flag}"


def _host():
    import platform
    return platform.node() or "this machine"


def trust_task(pl, lang=None):
    """(title, steps) for the owner's task when the folder is untrusted in the chosen account. The steps name the
    machine and say what to do there in words, the command only as one way: the owner may drive the conductor from
    another computer (of_onboarding, 2026-11-11), and the conductor runs of spawn again."""
    from of_locale import tr
    kw = dict(position=pl["position"], cwd=pl["cwd"], acct=pl["config_dir"], host=_host(), line=_claude_line(pl))
    return (tr("Trust the folder {cwd} in the account {acct}, to start {position}", lang, **kw),
            tr("of spawn {position} stopped: Claude Code in the account {acct} on {host} does not trust the folder "
               "{cwd} yet and would show its trust dialog. Only the organization's owner trusts a folder.\n"
               "1. On {host}, open Claude Code in the folder {cwd} with the account {acct}: in a terminal there, "
               "{line}; or the Claude app, opening that folder.\n"
               "2. In the trust dialog choose \"Yes, I trust this folder\", then leave Claude Code.\n"
               "3. Tell your conductor it is done; it runs of spawn {position} again.", lang, **kw))


def bypass_accepted(config_dir):
    """Whether the account has accepted Claude Code's Bypass Permissions warning once (skipDangerousModePermissionPrompt
    in its settings.json). Without it, a session started with --dangerously-skip-permissions opens on that warning,
    and the brief would be typed into the dialog (of_docs, 2026-11-11; of_capacity hit it on a resume, 2026-11-07)."""
    try:
        with open(os.path.join(os.path.expanduser(config_dir), "settings.json")) as fh:
            return bool((json.load(fh) or {}).get("skipDangerousModePermissionPrompt"))
    except (OSError, ValueError, AttributeError):
        return False


def bypass_task(pl, lang=None):
    """(title, steps) for the owner's task when the account has not accepted the Bypass Permissions warning."""
    from of_locale import tr
    kw = dict(position=pl["position"], cwd=pl["cwd"], acct=pl["config_dir"], host=_host(),
              line=_claude_line(pl, " --dangerously-skip-permissions"))
    return (tr("Accept the Bypass Permissions warning in the account {acct}, to start {position}", lang, **kw),
            tr("of spawn {position} stopped: OF's agents run in Bypass Permissions mode, and the account {acct} on "
               "{host} has not accepted its warning yet. Only the organization's owner accepts it.\n"
               "1. On {host}, start Claude Code in Bypass Permissions mode with the account {acct}: in a terminal "
               "there, {line}.\n"
               "2. In the warning choose \"Yes, I accept\", then leave Claude Code.\n"
               "3. Tell your conductor it is done; it runs of spawn {position} again.", lang, **kw))


HELD_BRIEF = re.compile(r"^(?:\[Pasted text #\d+(?: \+\d+ lines)?\]|Message from \S+ \(via of spawn)")


def submit_held(pane, read_box, press, wait_s=15, sleep=time.sleep, clock=time.time):
    """After the brief was typed: did it leave the input box? A brief can sit typed and unsent (a user organization's
    structure keeper, message 01a1e8ce-44ee: at a busy moment a spawn's brief waited 17 minutes in the box, a relayed
    message behind it, until Enter was pressed by hand). Reads the box for up to WAIT_S; when it still holds the brief
    (its frame, or Claude Code's paste placeholder), presses Enter once. read_box(pane) -> text, '' or None;
    press(pane) -> ok. Returns 'sent', 'pressed', 'press failed', or 'unknown' (the box holds something else, or
    cannot be read: nothing is pressed)."""
    end = clock() + wait_s
    while True:
        box = read_box(pane)
        if box == "":
            return "sent"
        if clock() >= end:
            break
        sleep(1)
    if box and HELD_BRIEF.match(box.strip()):
        return "pressed" if press(pane) else "press failed"
    return "unknown"


def run(pl, herdr, deliver, rename, wait_session, log=print, of_bin=None, timeout=180, held=None):
    """Side effects: open the pane, launch, wait for the session, type the brief (declared), name the agent, of apply.
    herdr(*args) -> result; deliver(pane, text, session) -> (ok, why); rename(pane, name, sid) -> str;
    wait_session(sid, timeout) -> agent or None; held(pane) -> submit_held's result, after the brief is typed.
    Returns the pane id."""
    import of_capacity
    ws = herdr("workspace", "list")["workspaces"]
    tabs = herdr("tab", "list")["tabs"]
    panes = herdr("pane", "list")["panes"]
    cmd, follow = of_capacity.place_command(pl["workspace"], pl["tab"], pl["cwd"], ws, tabs, panes)
    res = herdr(*cmd)
    pane = of_capacity.first_pane_id(res)
    if not pane:
        raise SpawnError(f"herdr {' '.join(cmd[:2])} returned no pane: {str(res)[:200]}")
    if follow:  # a new workspace: its first tab gets the system's label
        tab_id = next((t["tab_id"] for t in herdr("tab", "list")["tabs"]
                       if any(x.get("pane_id") == pane for x in herdr("pane", "list")["panes"]
                              if x.get("tab_id") == t["tab_id"])), None)
        if tab_id:
            herdr("tab", "rename", tab_id, follow[1])
    log(f"  pane {pane} in {pl['workspace']} / {pl['tab']}")
    bypass = of_capacity.BYPASS_FLAG in pl["command"]
    record_settings(pl)
    herdr("pane", "run", pane, pl["command"])
    if not wait_session(pl["session"], timeout):
        if trust_dialog(herdr, pane):
            raise SpawnError(
                f"Claude Code shows the folder-trust dialog for {pl['cwd']} in {pl['config_dir']} (pane {pane}), "
                f"although the account's records read as trusted: trusting a folder is the founder's act. His popup "
                f"gets a card for this dialog (blocked: trust) within a minute, and his answer there starts the "
                f"session; file no second task. Then type the brief and declare it (of event injected_prompt): "
                f"{pl['brief']} Tell of_platform: the trust rule of Claude Code changed.")
        raise SpawnError(f"session {pl['session']} did not come up in pane {pane} within {timeout}s; look at the pane")
    ok, why = deliver(pane, pl["brief"], pl["session"])
    # herdr may not see the agent in its pane for a moment after the session came up (Claude Code restarting itself
    # after an update, herdr's detection catching up): its wait then fails with agent_not_running at once although
    # the prompt is idle right after (a user organization's structure keeper, message 01a1e0f1-c913: two spawns
    # typed no brief); try again within the same time the session had to come up
    end = time.time() + timeout
    while ok is not True and "agent_not_running" in str(why) and time.time() < end:
        time.sleep(2)
        ok, why = deliver(pane, pl["brief"], pl["session"])
    if ok is not True:
        raise SpawnError(f"the brief was not typed ({why}); the session runs in pane {pane}: type it with of send")
    log(f"  brief typed (declared for session {pl['session']})")
    if held:
        sent = held(pane)
        if sent != "sent":
            log(f"  the brief stayed in the input box: {sent}" + ("" if sent == "pressed" else
                f"; press Enter in pane {pane} if it still holds the brief"))
    if bypass:  # the input box is drawn by now: one read of the footer
        of_capacity.check_bypass_after_start(pane, log, wait=0, herdr=herdr)
    log(f"  agent name: {rename(pane, pl['position'], pl['session'])}")
    if of_bin:
        # the Python OF runs on, not bin/of's shebang: a machine may have OF in its own venv and a python3 without
        # PyYAML on PATH (organic-framework PR 102, review)
        subprocess.run([sys.executable, of_bin, "apply"], capture_output=True, text=True, timeout=120)
    return pane


def trust_dialog(herdr, pane):
    """Whether the pane shows Claude Code's folder-trust dialog (of_blocked's reading of the screen)."""
    import of_blocked
    try:
        r = herdr("pane", "read", pane, "--source", "visible", "--lines", "40")
    except Exception:  # noqa: BLE001  an unreadable pane: the plain error stands
        return False
    text = (r.get("read") or {}).get("text") if isinstance(r, dict) and isinstance(r.get("read"), dict) else r
    if isinstance(r, dict) and not isinstance(text, str):
        text = next((r[k] for k in ("text", "content", "output") if isinstance(r.get(k), str)), "")
    return ((of_blocked.detect(text if isinstance(text, str) else "") or {}).get("type")) == "trust"


def wait_for(fn, timeout, step=2):
    end = time.time() + timeout
    while time.time() < end:
        x = fn()
        if x:
            return x
        time.sleep(step)
    return None
