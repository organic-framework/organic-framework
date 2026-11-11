"""The command that resumes a Claude Code session as it ran: on its own account, in its own mode.

herdr resumes an agent after its own restart as a bare `claude --resume <id>` typed into the pane's shell
(the built-in Claude integration; herdr 0.9.5 keeps no reported resume command for a pane that integration holds,
checked in the oflab herdr session on 2026-11-10). On 2026-11-10 that started 38 sessions on ~/.claude (its week
spent), without --dangerously-skip-permissions, from stale transcript copies left there by account moves.

`of resume-command <id>` prints the right command line; the shell function in bin/of-shell.zsh runs it in place of
the bare one, only for that exact form, in a herdr pane, without CLAUDE_CONFIG_DIR.
"""

import json
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


def command(sid, dirs=None, memory_dir=None, plan_root=None, deny=None, state_dir=None, tools=None, defaults=None):
    """The shell line that resumes `sid` as it ran, or None when no account holds it (the caller runs the bare one).
    deny: the position's credential deny rules (of_spawn.deny_rules), so a session herdr resumes keeps what of spawn
    gave it (of_capacity, 2026-11-11); with the memory directory they go in one settings file (of_spawn). tools:
    (argv, settings keys) of the position's tool profile (of_tools.launch_parts, docs/tools.md §4). defaults: the
    organization's {model, effort} (orgs.yaml agents), used only when the session names neither."""
    d, t = newest_copy(sid, dirs)
    if not d:
        return None
    env = ["env", "-u", "CLAUDE_CONFIG_DIR"] if of_capacity.is_default(d) else \
        ["env", f"CLAUDE_CONFIG_DIR={os.path.expanduser(d)}"]
    argv = env + ["claude", "--resume", sid]
    tool_argv, tool_settings = tools or ([], {})
    settings = of_spawn.session_settings(json.dumps({"autoMemoryDirectory": memory_dir}) if memory_dir else None, deny,
                                         tool_settings)
    if settings:
        argv += ["--settings", of_spawn.settings_file(settings, sid, state_dir)]
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
