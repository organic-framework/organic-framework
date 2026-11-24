"""The command that resumes a Claude Code session as it ran: on its own account, in its own mode.

herdr resumes an agent after its own restart as a bare `claude --resume <id>` typed into the pane's shell
(the built-in Claude integration; herdr 0.9.5 keeps no reported resume command for a pane that integration holds,
checked in the oflab herdr session on 2026-11-10). On 2026-11-10 that started 38 sessions on ~/.claude (its week
spent), without --dangerously-skip-permissions, from stale transcript copies left there by account moves.

`of resume-command <id>` prints the right command line; the shell function in bin/of-shell.zsh runs it in place of
the bare one, only for that exact form, in a herdr pane, without CLAUDE_CONFIG_DIR.
"""

import json
import re
import os
import shlex

import of_capacity
import of_spawn


def newest_copy(sid, dirs=None):
    """(config dir, transcript) holding the newest copy of the session's transcript: a move leaves an older copy on
    the account it left, and the account the session runs on now has the newest one."""
    best = None
    for d in dirs if dirs is not None else of_capacity.config_dirs():
        t = of_capacity.find_transcript(d, sid)
        if not t:
            continue
        try:
            key = (os.path.getmtime(t), os.path.getsize(t))
        except OSError:
            continue
        if best is None or key > best[0]:
            best = (key, d, t)
    return (best[1], best[2]) if best else (None, None)


def planned(sid, root=None):
    """The session's entry in the newest restart plan that has it (`of capacity reboot prepare` reads the model and
    effort from the running process's command line, which the transcript and the settings do not carry)."""
    import glob
    import json
    root = root or of_capacity.REBOOT_DIR
    for f in sorted(glob.glob(os.path.join(root, "*", "plan.json")), key=os.path.getmtime, reverse=True):
        try:
            with open(f) as fh:
                e = next((x for x in json.load(fh).get("entries") or [] if x.get("session") == sid), None)
        except (OSError, ValueError):
            continue
        if e:
            return e
    return {}


def codex_home_of(sid, homes=None):
    """The Codex seat (CODEX_HOME) whose sessions hold SID's rollout
    ($CODEX_HOME/sessions/YYYY/MM/DD/rollout-<time>-<thread id>.jsonl, or .jsonl.zst), or None."""
    import glob
    if not sid or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", str(sid)):
        return None
    if homes is None:
        import of_accounts
        homes = [s["home"] for s in of_accounts.codex_seats()]
    for home in homes:
        if glob.glob(os.path.join(os.path.expanduser(home), "sessions", "*", "*", "*", f"rollout-*-{sid}.jsonl*")):
            return os.path.expanduser(home)
    return None


def codex_command(sid, position, cwd=None, homes=None, model=None, deny=None, prepare=None, launch=None, effort=None):
    """The line that resumes Codex session SID as a position's (hand-over 9ee1db9f, W4): its seat, a fresh layer and a
    new launch's door, so it starts with what a start gives it now (its start context, its profile, its tools), in the
    folder the line runs in; `codex resume <id> -C <folder> -p of-<launch>`. None when no seat holds the session."""
    home = codex_home_of(sid, homes)
    if not home:
        return None
    if not of_spawn.codex_seat_private_ok(home):   # as at a start: no private start context into an unread seat
        return None
    ok, _ = of_spawn.codex_requirements_ok()
    if not ok:   # no resume where OF's hooks are not forced
        return None
    cwd = cwd or os.getcwd()
    org = (position or {}).get("_org")
    if org and of_spawn.in_position_folder(cwd, org):
        if any(not of_spawn.clone_ready(c) for c in of_spawn.position_clones(cwd)):
            return None   # as a Claude resume: a clone not ready gives no line
        of_spawn.prepare_folder(cwd, org, of_spawn.repo_names(position or {}, _org_paths(org)))
    import uuid
    pl = {"harness": "codex", "position": (position or {}).get("name"), "org": org, "cwd": cwd, "config_dir": home,
          "launch": launch or str(uuid.uuid4()), "model": model, "effort": effort, "deny": list(deny or [])}
    (prepare or of_spawn.codex_prepare)(pl, log=lambda m: None)
    return of_spawn.codex_command(pl, resume=sid)


def _org_paths(org):
    """ORG's checkouts in this machine's orgs.yaml (`path`, `paths`), or []."""
    import yaml
    try:
        with open(of_spawn._orgs_file()) as fh:
            doc = yaml.safe_load(fh) or {}
    except (OSError, ValueError, yaml.YAMLError):
        return []
    o = next((x for x in (doc.get("orgs") or []) if isinstance(x, dict) and x.get("name") == org), None) or {}
    return [x for x in [o.get("path")] + list(o.get("paths") or []) if x]


def command(sid, dirs=None, memory_dir=None, plan_root=None, deny=None, state_dir=None, tools=None, defaults=None,
            org=None, cwd=None, position=None):
    """The shell line that resumes `sid` as it ran, or None when no account holds it (the caller runs the bare one).
    deny: the position's credential deny rules (of_spawn.deny_rules), so a session herdr resumes keeps what of spawn
    gave it (of_capacity, 2026-11-11); with the memory directory they go in one settings file (of_spawn). tools:
    (argv, settings keys) of the position's tool profile (of_tools.launch_parts, docs/tools.md §4). defaults: the
    organization's {model, effort} (orgs.yaml agents), used only when the session names neither. cwd: the folder the
    line runs in, the current one by default, which is where the session starts (the line has no cd), so its sandbox
    keeps OF's rules files there and above closed, as of spawn's does; and the folder the session started in
    (of_capacity.started_folder) too, where that differs (of_secrets-review on PR 601: the line gave the sandbox no
    folder at all)."""
    d, t = newest_copy(sid, dirs)
    if not d:
        return None
    env = ["env", "-u", "CLAUDE_CONFIG_DIR"] if of_capacity.is_default(d) else \
        ["env", f"CLAUDE_CONFIG_DIR={os.path.expanduser(d)}"]
    argv = env + ["claude", "--resume", sid]
    tool_argv, tool_settings = tools or ([], {})
    cwd = cwd or os.getcwd()
    if org and of_spawn.in_position_folder(cwd, org):
        # a position's own folder: the same checks and the same fresh copy of its instructions as of spawn's start
        # (of_secrets-review on PR 608); a clone not ready gives no line, so the session does not start
        if any(not of_spawn.clone_ready(c) for c in of_spawn.position_clones(cwd)):
            return None
        of_spawn.prepare_folder(cwd, org, of_spawn.repo_names(position or {}, _org_paths(org)))
    try:
        started = of_capacity.started_folder(t, cwd, position)
    except Exception:  # noqa: BLE001 - the folder it runs in is still covered
        started = None
    settings = of_spawn.session_settings(json.dumps({"autoMemoryDirectory": memory_dir}) if memory_dir else None, deny,
                                         tool_settings, scratch=(cwd, sid), org=org,
                                         rules_dirs=[started] if started and started != cwd else None)
    if settings:
        # the line resume-command prints is run as it is printed (the claude shell function, herdr's resume): a launch
        argv += ["--settings", of_spawn.settings_file(settings, sid, state_dir, launch=True)]
    argv += list(tool_argv)
    e = planned(sid, plan_root)
    defaults = defaults or {}
    model = of_capacity.last_model(t) or e.get("model") or defaults.get("model")
    if model:
        argv += ["--model", model]
        # the plan's effort even when the model changed since (of_capacity, 2026-11-10: acme_throughput-owner moved to
        # Fable at xhigh after the plan recorded Opus; the account's setting for Fable is empty, so a resume would
        # fall to Claude Code's default effort)
        effort = e.get("effort") or of_capacity.effort_of(d, model) or defaults.get("effort")
        if effort:
            argv += ["--effort", effort]
    if e.get("bypass") or of_capacity.ran_in_bypass(t):
        argv.append(of_capacity.BYPASS_FLAG)
    return " ".join(shlex.quote(a) for a in argv)
