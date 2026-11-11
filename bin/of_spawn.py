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
import time
import uuid


class SpawnError(Exception):
    pass


def brief_text(p, org_root):
    """The brief, typed as the session's first prompt."""
    mandate = p.get("mandate") or f"registry/briefs/{p['name']}.md"
    path = mandate if os.path.isabs(os.path.expanduser(mandate)) else os.path.join(org_root, mandate)
    home = os.path.expanduser("~")
    path = "~" + path[len(home):] if path.startswith(home) else path
    return (f"You hold the position {p['name']}. Read {path} and the organization's AGENTS.md, and follow them. "
            f"At the end of every turn: of status working|waiting --on founder|<position>|idle|done.")


def work_dir(p, systems, org_paths, exists=os.path.isdir):
    """The position's folder: the first repository of its system that exists under the organization's paths (as a
    child of a path, or as a sibling of it), else the organization's first path."""
    repos = ((systems or {}).get(p.get("system")) or {}).get("repos") or []
    roots = [os.path.expanduser(str(x)) for x in org_paths or []]
    for repo in repos:
        for root in roots:
            for cand in (os.path.join(root, repo), os.path.join(os.path.dirname(root), repo)):
                if exists(cand):
                    return cand
    for root in roots:
        if exists(root):
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


def session_settings(memory=None, deny=None, extra=None):
    """The one --settings JSON: the memory directory (bin/of_memory.py, RFC 0006), the credential deny rules, and
    extra keys such as the tool profile's allowedMcpServers and enabledPlugins (of_tools.launch_parts, docs/tools.md
    §4)."""
    s = json.loads(memory) if memory else {}
    if deny:
        s.setdefault("permissions", {}).setdefault("deny", []).extend(deny)
    for k, v in (extra or {}).items():
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
    return path


def launch_command(cwd, config_dir, sid, name, default_dir, model=None, bypass=True, memory=None, deny=None,
                   state_dir=None, tools=None):
    """memory: the --settings JSON naming the position's memory directory (bin/of_memory.py, RFC 0006); deny: the
    credential deny rules (deny_rules); tools: (argv, settings keys) of the position's tool profile
    (of_tools.launch_parts). The settings go into one settings file (settings_file)."""
    env = ["env", "-u", "CLAUDE_CONFIG_DIR"] if os.path.realpath(config_dir) == os.path.realpath(default_dir) \
        else [f"CLAUDE_CONFIG_DIR={shlex.quote(config_dir)}"]
    parts = ["cd", shlex.quote(cwd), "&&", *env, "OF_SPAWNED=1", "claude", "--session-id", sid, "-n", name]
    tool_argv, tool_settings = tools or ([], {})
    settings = session_settings(memory, deny, tool_settings)
    if settings:
        parts += ["--settings", shlex.quote(settings_file(settings, sid, state_dir))]
    parts += [shlex.quote(a) for a in tool_argv]
    if model:
        parts += ["--model", model]
    if bypass:
        parts.append("--dangerously-skip-permissions")
    return " ".join(parts)


def plan(p, positions, agents, bind, systems, org_paths, org_root, want_workspace, config_dir=None, cwd=None,
         capacity_rows=None, best_target=None, trusted=None, default_dir=os.path.expanduser("~/.claude"),
         model=None, bypass=True, sid=None, memory=None, deny=None, accepted=None, tools=None):
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
            "workspace": ws, "tab": tab, "brief": brief_text(p, org_root),
            "trusted": trusted(config_dir, cwd) if trusted else None,
            "bypass_ok": (not bypass) or bool(accepted(config_dir)) if accepted else True,
            "deny": list(deny or []),
            "command": launch_command(cwd, config_dir, sid, p["name"], default_dir, model, bypass, memory, deny,
                                      tools=tools(p["name"], sid, config_dir) if tools else None)}


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
    """The language of the owner's cards on this machine: ~/.config/of/owner.yaml `language` (en or ru; default en).
    The tool's own text stays English; a newcomer's owner may not read Russian (of_docs, 2026-11-11)."""
    import of_events
    try:
        import yaml
        with open(of_events.config_path("owner.yaml")) as fh:
            lang = str((yaml.safe_load(fh) or {}).get("language") or "en").lower()
    except (OSError, ImportError, AttributeError, ValueError):
        lang = "en"
    return lang if lang in ("en", "ru") else "en"


def _claude_line(pl, flag=""):
    acct = pl["config_dir"]
    env = "" if acct.rstrip("/").endswith("/.claude") else f"CLAUDE_CONFIG_DIR={acct} "
    return f"cd {pl['cwd']} && {env}claude{flag}"


def trust_task(pl, lang=None):
    """(title, steps) for the owner's task when the folder is untrusted in the chosen account."""
    acct, lang = pl["config_dir"], lang or owner_language()
    if lang == "ru":
        return (f"Доверить папку {pl['cwd']} в аккаунте {acct}, чтобы запустить {pl['position']}",
                f"of spawn {pl['position']} остановился: Claude Code в аккаунте {acct} ещё не доверяет папке {pl['cwd']} "
                f"и покажет диалог доверия. Доверить папку можешь только ты.\n"
                f"1. В любом терминале: {_claude_line(pl)}\n"
                "2. В диалоге доверия выбери «Yes, I trust this folder», затем выйди (/exit).\n"
                f"3. Скажи «готово»: позиция запустится повторной командой of spawn {pl['position']}.")
    return (f"Trust the folder {pl['cwd']} in the account {acct}, to start {pl['position']}",
            f"of spawn {pl['position']} stopped: Claude Code in the account {acct} does not trust the folder "
            f"{pl['cwd']} yet and would show its trust dialog. Only the organization's owner trusts a folder.\n"
            f"1. In any terminal: {_claude_line(pl)}\n"
            "2. In the trust dialog choose \"Yes, I trust this folder\", then leave with /exit.\n"
            f"3. Run of spawn {pl['position']} again.")


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
    acct, lang = pl["config_dir"], lang or owner_language()
    line = _claude_line(pl, " --dangerously-skip-permissions")
    if lang == "ru":
        return (f"Принять предупреждение Bypass Permissions в аккаунте {acct}, чтобы запустить {pl['position']}",
                f"of spawn {pl['position']} остановился: агенты OF работают в режиме Bypass Permissions, а аккаунт "
                f"{acct} ещё ни разу не принял его предупреждение. Принять его можешь только ты.\n"
                f"1. В любом терминале: {line}\n"
                "2. В предупреждении выбери «Yes, I accept», затем выйди (/exit).\n"
                f"3. Скажи «готово»: позиция запустится повторной командой of spawn {pl['position']}.")
    return (f"Accept the Bypass Permissions warning in the account {acct}, to start {pl['position']}",
            f"of spawn {pl['position']} stopped: OF's agents run in Bypass Permissions mode, and the account {acct} "
            f"has not accepted its warning yet. Only the organization's owner accepts it.\n"
            f"1. In any terminal: {line}\n"
            "2. In the warning choose \"Yes, I accept\", then leave with /exit.\n"
            f"3. Run of spawn {pl['position']} again.")


def run(pl, herdr, deliver, rename, wait_session, log=print, of_bin=None, timeout=180):
    """Side effects: open the pane, launch, wait for the session, type the brief (declared), name the agent, of apply.
    herdr(*args) -> result; deliver(pane, text, session) -> (ok, why); rename(pane, name, sid) -> str;
    wait_session(sid, timeout) -> agent or None. Returns the pane id."""
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
    if ok is not True:
        raise SpawnError(f"the brief was not typed ({why}); the session runs in pane {pane}: type it with of send")
    log(f"  brief typed (declared for session {pl['session']})")
    if bypass:  # the input box is drawn by now: one read of the footer
        of_capacity.check_bypass_after_start(pane, log, wait=0, herdr=herdr)
    log(f"  agent name: {rename(pane, pl['position'], pl['session'])}")
    if of_bin:
        subprocess.run([of_bin, "apply"], capture_output=True, text=True, timeout=120)
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
