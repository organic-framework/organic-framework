"""The laptop's live checkout: the one every account's Claude Code hooks, the listener, the relay client, of-server,
the health check and the `of` on PATH run from, apart from the shared working copy sessions write (hand-over
cb134c52; of_server's message 01a20c9f-4a12: on 2026-11-24 a patch applied with --reject into ~/organic-framework left a
name undefined in the hook, and every session's tool calls on the laptop were refused for two hours, both
organizations', until the founder restored the file from a plain terminal).

It moves as the agent host's does (organic-framework-build tools/agent-host/of-checkout-update.sh): only on a request,
to public main's tip, through `of protected ff` (PR 511), a merged pull request's commit with its checks green, so no
working-copy commit and no working tree ever runs live. deploy/laptop/of-checkout-update.sh is its macOS form, started
by the launchd agent dev.organic-framework.checkout-update when ~/.local/state/of/checkout-update.request is written.

  of live setup     the owner's, once: clone public main into LIVE, install the update script and its agent
  of live switch    the owner's, once after setup: every account's hooks, the units and ~/.local/bin/of from LIVE
  of live request   anyone's: ask the agent to move LIVE to public main's tip (the request's content is never read)
  of live status    where LIVE is, and what runs from where
"""

import os
import plistlib
import shutil
import subprocess
import sys

URL = "https://github.com/organic-framework/organic-framework.git"
LIVE = os.path.expanduser("~/.local/share/of/live")
LIBEXEC = os.path.expanduser("~/.local/libexec/of-checkout-update")
LABEL = "dev.organic-framework.checkout-update"
AGENTS = os.path.expanduser("~/Library/LaunchAgents")
REQUEST = os.path.expanduser("~/.local/state/of/checkout-update.request")
LINK = os.path.expanduser("~/.local/bin/of")
# the units whose installers write the path of the bin/of that runs them: run from LIVE, they run LIVE
UNITS = (("listen", "install"), ("relay", "install"), ("server", "install"), ("health", "install"))
SAFE_GIT = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "submodule.recurse=false"]


class LiveError(Exception):
    pass


def git_env():
    return dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0",
                SSH_AUTH_SOCK="", OF_AUTOMATION="1")


def agent_plist(python, script=LIBEXEC, request=REQUEST, live=LIVE):
    """The launchd agent that runs the update on a request: WatchPaths on the request file, the script and the Python
    named absolutely, OF_AUTOMATION set, no shell."""
    return plistlib.dumps({
        "Label": LABEL, "ProgramArguments": ["/bin/bash", script],
        "WatchPaths": [request], "RunAtLoad": False,
        "EnvironmentVariables": {"OF_PYTHON": python, "OF_AUTOMATION": "1", "PATH": "/usr/bin:/bin"},
        "StandardOutPath": os.path.expanduser("~/.local/log/of/checkout-update.log"),
        "StandardErrorPath": os.path.expanduser("~/.local/log/of/checkout-update.log"),
        "WorkingDirectory": os.path.expanduser("~"),
    })


def hooked_accounts(dirs=None):
    """[(config dir, flags)] for each Claude Code account whose settings run OF's hooks today, with the flags they
    were installed with (--guard where a PreToolUse guard runs, --deny-secrets where the prompt's mention guard does),
    so a switch writes each one as it was, only the path changed."""
    import json
    if dirs is None:
        import of_accounts
        dirs = of_accounts.dirs()
    out = []
    for d in dirs:
        d = os.path.expanduser(d)
        try:
            with open(os.path.join(d, "settings.json")) as fh:
                hooks = (json.load(fh) or {}).get("hooks") or {}
        except (OSError, ValueError):
            continue
        cmds = [h.get("command", "") for evs in hooks.values() for m in evs or [] for h in (m.get("hooks") or [])]
        if not any("of-hook" in c or " hook " in c for c in cmds):
            continue
        flags = []
        if any("PreToolUse" in c for c in cmds):
            flags.append("--guard")
        if any("--deny-secrets" in c for c in cmds):
            flags.append("--deny-secrets")
        out.append((d, flags))
    return out


def switch_plan(python, accounts, live=LIVE):
    """The commands a switch runs, each from LIVE's own bin/of, so each writes LIVE's path: every hooked account's
    hooks, then the units."""
    of_bin = os.path.join(live, "bin", "of")
    cmds = [[python, of_bin, "hooks", "install", "--config-dir", d, "--yes", *flags] for d, flags in accounts]
    cmds += [[python, of_bin, *u] for u in UNITS]
    return cmds


def check_live(live=LIVE):
    """Refuse a LIVE that is no plain checkout of public main (as the update script checks it)."""
    if os.path.islink(live) or not os.path.isdir(os.path.join(live, ".git")) or os.path.islink(os.path.join(live, ".git")):
        raise LiveError(f"{live} is no plain checkout")
    r = subprocess.run(["git", *SAFE_GIT, "-C", live, "remote", "get-url", "origin"], capture_output=True, text=True,
                       env=git_env())
    if r.stdout.strip() != URL:
        raise LiveError(f"{live}'s origin is not public main ({URL})")


def setup(python=None, run=subprocess.run, live=LIVE, log=print):
    """Clone public main into LIVE (when it is not there), copy the update script beside OF's own, write and load the
    agent. The owner's, in a plain terminal."""
    python = python or sys.executable
    if not os.path.exists(live):
        os.makedirs(os.path.dirname(live), mode=0o755, exist_ok=True)
        r = run(["git", *SAFE_GIT, "clone", "--quiet", "--no-tags", "--branch", "main", URL, live], env=git_env())
        if getattr(r, "returncode", 1) != 0:
            raise LiveError(f"git clone {URL} failed")
        log(f"  cloned public main into {live}")
    check_live(live)
    os.makedirs(os.path.dirname(LIBEXEC), mode=0o755, exist_ok=True)
    src = os.path.join(live, "deploy", "laptop", "of-checkout-update.sh")
    if not os.path.isfile(src):
        raise LiveError(f"{src} is not in the live checkout yet: public main does not carry it")
    shutil.copyfile(src, LIBEXEC + ".new")
    os.chmod(LIBEXEC + ".new", 0o755)
    os.replace(LIBEXEC + ".new", LIBEXEC)
    os.makedirs(os.path.dirname(REQUEST), exist_ok=True)
    os.makedirs(os.path.expanduser("~/.local/log/of"), exist_ok=True)
    path = os.path.join(AGENTS, LABEL + ".plist")
    with open(path + ".new", "wb") as fh:
        fh.write(agent_plist(python))
    os.replace(path + ".new", path)
    dom = f"gui/{os.getuid()}"
    run(["launchctl", "bootout", f"{dom}/{LABEL}"], capture_output=True)
    run(["launchctl", "bootstrap", dom, path], capture_output=True)
    log(f"  the update agent {LABEL} watches {REQUEST}")
    return live


def switch(python=None, run=subprocess.run, live=LIVE, accounts=None, log=print):
    """Every hooked account's hooks, the units and ~/.local/bin/of from LIVE. The owner's, in a plain terminal."""
    python = python or sys.executable
    check_live(live)
    for cmd in switch_plan(python, hooked_accounts() if accounts is None else accounts, live):
        r = run(cmd)
        log(f"  {' '.join(cmd[2:5])}: {'done' if getattr(r, 'returncode', 1) == 0 else 'FAILED'}")
    tmp = LINK + ".new"
    if os.path.lexists(tmp):
        os.unlink(tmp)
    os.symlink(os.path.join(live, "bin", "of"), tmp)
    os.replace(tmp, LINK)
    log(f"  {LINK} -> {os.path.join(live, 'bin', 'of')}")


def request():
    """Ask the agent for a move to public main's tip."""
    os.makedirs(os.path.dirname(REQUEST), exist_ok=True)
    with open(REQUEST, "a"):
        os.utime(REQUEST, None)
    return REQUEST


def status(live=LIVE):
    """{live, head, link, hooks} for `of live status`."""
    head = None
    if os.path.isdir(os.path.join(live, ".git")):
        r = subprocess.run(["git", *SAFE_GIT, "-C", live, "rev-parse", "HEAD"], capture_output=True, text=True,
                           env=git_env())
        head = r.stdout.strip() or None
    link = os.path.realpath(LINK) if os.path.lexists(LINK) else None
    return {"live": live, "head": head, "link": link,
            "accounts": [(d, flags) for d, flags in hooked_accounts()]}
