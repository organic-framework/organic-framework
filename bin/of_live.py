"""The laptop's live checkout: the one every account's Claude Code hooks, the listener, the relay client, of-server,
the health check and the `of` on PATH run from, apart from the shared working copy sessions write (hand-over
cb134c52; of_server's message 01a20c9f-4a12: on 2026-11-24 a patch applied with --reject into ~/organic-framework left a
name undefined in the hook, and every session's tool calls on the laptop were refused for two hours, both
organizations', until the founder restored the file from a plain terminal).

It moves as the agent host's does (organic-framework-build tools/agent-host/of-checkout-update.sh): only on a request,
to public main's tip, through `of protected ff` (PR 511), a merged pull request's commit with its checks green, so no
working-copy commit and no working tree ever runs live. deploy/laptop/of-checkout-update.sh is its macOS form, started
by the launchd agent dev.organic-framework.checkout-update when ~/.local/state/of/checkout-update.request is written.

  of live setup     the owner's, once: clone public main into LIVE, write the update agent that runs its script
  of live switch    the owner's, once after setup: every account's hooks, the units and ~/.local/bin/of from LIVE
  of live request   anyone's: ask the agent to move LIVE to public main's tip (the request's content is never read)
  of live status    where LIVE is, and what runs from where
"""

import os
import plistlib
import re
import subprocess
import sys

URL = "https://github.com/organic-framework/organic-framework.git"
LIVE_DIR = "~/.local/share/of/live"   # of_guard.LIVE_DIR, which G40 keeps
LIVE = os.path.expanduser(LIVE_DIR)
SCRIPT = os.path.join("deploy", "laptop", "of-checkout-update.sh")   # run where it lies, in the live checkout
SITE = os.path.join(".git", "of-site")   # the update's only import folder beyond the checkout: PyYAML, which G40 keeps


def copy_pyyaml(live=LIVE, python=None, run=subprocess.run):
    """Copy the PyYAML package the owner's Python imports into LIVE's SITE: the update runs Python with -I, so the
    user site, a folder sessions may write, never loads (of_secrets-review on PR 629), while on a Mac PyYAML lives
    there. The copy is the owner's, at setup."""
    r = run([python or sys.executable, "-c", "import os, yaml; print(os.path.dirname(yaml.__file__))"],
            capture_output=True, text=True)
    src = (r.stdout or "").strip()
    if getattr(r, "returncode", 1) != 0 or not os.path.isfile(os.path.join(src, "__init__.py")):
        raise LiveError("the Python OF runs on has no PyYAML to copy")
    import shutil
    site = os.path.join(live, SITE)
    tmp = site + ".new"
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src, os.path.join(tmp, "yaml"), symlinks=False, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.rmtree(site, ignore_errors=True)
    os.replace(tmp, site)
    return site
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


def agent_plist(python, script=None, request=REQUEST, live=LIVE):
    """The launchd agent that runs the update on a request: WatchPaths on the request file, the script and the Python
    named absolutely, OF_AUTOMATION set, no shell. The script is the live checkout's own, which G40 keeps."""
    script = script or os.path.join(live, SCRIPT)
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
    were installed with (--guard where a PreToolUse guard runs, --deny-secrets where the prompt's mention guard does)
    and a `--replace <bin/of>` for each other checkout they run from, so a switch writes each one as it was, only the
    path changed, and no hook of the working copy stays beside the live one (of_secrets-review on PR 629)."""
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
        for c in cmds:   # the checkouts its hooks run from today: `<python> <checkout>/bin/of-hook <Event>`
            for w in c.split():
                if w.endswith("/bin/of-hook") or w.endswith("/bin/of"):
                    old = os.path.join(os.path.dirname(w), "of")
                    if old not in flags and os.path.realpath(old) != os.path.realpath(os.path.join(LIVE, "bin", "of")):
                        flags += ["--replace", old]
        import of_init
        if any(m.get("matcher") == of_init.GUARD_MATCHER for m in hooks.get("PreToolUse") or []):
            flags.append("--guard")   # the guard's own group, never a recorder's PreToolUse (of_review on PR 628)
        if any("--deny-secrets" in c for c in cmds):
            flags.append("--deny-secrets")
        out.append((d, flags))
    return out


def installed_units(agents=AGENTS):
    """The units of UNITS this machine runs now (their launchd agent is there): a switch moves those, never adds one."""
    return [u for u in UNITS if os.path.exists(os.path.join(agents, f"dev.organic-framework.{u[0]}.plist"))]


def switch_plan(python, accounts, live=LIVE, units=None):
    """The commands a switch runs, each from LIVE's own bin/of, so each writes LIVE's path: every hooked account's
    hooks, then the units this machine runs (of_review on PR 628)."""
    of_bin = os.path.join(live, "bin", "of")
    cmds = [[python, of_bin, "hooks", "install", "--config-dir", d, "--yes", *flags] for d, flags in accounts]
    cmds += [[python, of_bin, *u] for u in (installed_units() if units is None else units)]
    return cmds


def check_live(live=LIVE):
    """Refuse a LIVE that is no plain checkout of public main (as the update script checks it)."""
    if os.path.islink(live) or not os.path.isdir(os.path.join(live, ".git")) or os.path.islink(os.path.join(live, ".git")):
        raise LiveError(f"{live} is no plain checkout")
    r = subprocess.run(["git", *SAFE_GIT, "-C", live, "remote", "get-url", "origin"], capture_output=True, text=True,
                       env=git_env())
    if r.stdout.strip() != URL:
        raise LiveError(f"{live}'s origin is not public main ({URL})")


def checkout_org(orgs_file, checkout=None):
    """The organization whose orgs.yaml entry lists the checkout OF runs from now (its `path` or one of its `paths`): the
    one the live checkout's protected_code entry goes to."""
    import yaml
    checkout = os.path.realpath(checkout or os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
    try:
        with open(orgs_file) as fh:
            doc = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return None
    for o in doc.get("orgs") or []:
        if isinstance(o, dict):
            ps = [o.get("path")] + list(o.get("paths") or [])
            if any(p and os.path.realpath(os.path.expanduser(str(p))) == checkout for p in ps):
                return o.get("name")
    return None


def setup(python=None, run=subprocess.run, live=LIVE, log=print, orgs_file=None, org=None):
    """Clone public main into LIVE (when it is not there), list it as the owner's protected_code entry with ff in
    orgs.yaml (of protected ff moves only such an entry; of_review on PR 628), write and load the agent. The owner's,
    in a plain terminal."""
    python = python or sys.executable
    orgs_file = orgs_file or os.path.expanduser("~/.config/of/orgs.yaml")
    if not os.path.exists(live):
        os.makedirs(os.path.dirname(live), mode=0o755, exist_ok=True)
        r = run(["git", *SAFE_GIT, "clone", "--quiet", "--no-tags", "--branch", "main", URL, live], env=git_env())
        if getattr(r, "returncode", 1) != 0:
            raise LiveError(f"git clone {URL} failed")
        log(f"  cloned public main into {live}")
    check_live(live)
    src = os.path.join(live, SCRIPT)
    if not os.path.isfile(src):
        raise LiveError(f"{src} is not in the live checkout yet: public main does not carry it")
    log(f"  copied PyYAML into {copy_pyyaml(live, python, run)} for the update, which runs Python with -I")
    if ff_entry(orgs_file, live) is None:
        org = org or checkout_org(orgs_file)
        if not org:
            raise LiveError("no organization in orgs.yaml lists the checkout OF runs from; name one with --org")
        add_ff_entry(orgs_file, org, live)
        log(f"  listed {live} in {org}'s protected_code in {orgs_file}, moved only by of protected ff")
    os.makedirs(os.path.dirname(REQUEST), exist_ok=True)
    os.makedirs(os.path.expanduser("~/.local/log/of"), exist_ok=True)
    path = os.path.join(AGENTS, LABEL + ".plist")
    with open(path + ".new", "wb") as fh:
        fh.write(agent_plist(python, live=live))
    os.replace(path + ".new", path)
    dom = f"gui/{os.getuid()}"
    run(["launchctl", "bootout", f"{dom}/{LABEL}"], capture_output=True)
    run(["launchctl", "bootstrap", dom, path], capture_output=True)
    log(f"  the update agent {LABEL} watches {REQUEST}")
    return live


FF = {"repo": "organic-framework/organic-framework", "branch": "main", "checks": ["unit"],
      "human": ["bin/of_protected.py"]}   # as the agent host's entry for its live checkout


def ff_entry(orgs_file, live=LIVE):
    """The owner's protected_code entry with ff for LIVE in orgs.yaml (of protected ff moves only such an entry), or
    None."""
    import yaml
    try:
        with open(orgs_file) as fh:
            doc = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return None
    for o in doc.get("orgs") or []:
        for e in (o.get("protected_code") or []) if isinstance(o, dict) else []:
            if isinstance(e, dict) and e.get("ff") and \
                    os.path.realpath(os.path.expanduser(str(e.get("path")))) == os.path.realpath(live):
                return e
    return None


def add_ff_entry(orgs_file, org, live=LIVE):
    """Add LIVE's protected_code entry with ff to ORG's entry in orgs.yaml, the owner's file (docs/guard.md G33): a
    block appended to the entry, the rest of the file kept as written, and written only when the file then reads as it
    did with that one key more. ORG's entry must have no protected_code yet; otherwise the owner adds the item."""
    import tempfile
    import yaml
    import of_init
    with open(orgs_file) as fh:
        text = fh.read()
    doc = yaml.safe_load(text) or {}
    orgs = doc.get("orgs") or []
    at = [k for k, o in enumerate(orgs) if isinstance(o, dict) and of_init.org_entry_name(o) == org]
    if len(at) != 1:
        raise LiveError(f"no single organization {org!r} in {orgs_file}")
    if "protected_code" in orgs[at[0]]:
        raise LiveError(f"{org}'s entry in {orgs_file} has protected_code already: add the live checkout's item to it")
    home = os.path.expanduser("~")
    shown = "~" + live[len(home):] if live.startswith(home + "/") else live
    item = {"path": shown, "ff": dict(FF)}
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    dash_col = None
    for i, ln in enumerate(lines):
        if re.match(r"^orgs\s*:", ln):
            for j in range(i + 1, len(lines)):
                m = re.match(r"^( *)- ", lines[j])
                if m:
                    dash_col = len(m.group(1))
                    break
            break
    if dash_col is None:
        raise LiveError(f"{orgs_file}: its orgs: list is not in block style; add the entry by hand")
    items = [i for i, ln in enumerate(lines) if re.match(r"^ {%d}- " % dash_col, ln)]
    if len(items) != len(orgs):
        raise LiveError(f"{orgs_file}: its orgs: list is not in plain block style; add the entry by hand")
    b = items[at[0]]
    e = next((i for i in items if i > b), None)
    if e is None:
        e = next((i for i in range(b + 1, len(lines)) if lines[i].strip() and not lines[i].startswith(" ")), len(lines))
    while e > b + 1 and not lines[e - 1].strip():
        e -= 1
    col = dash_col + 2
    block = yaml.safe_dump({"protected_code": [item]}, default_flow_style=None, sort_keys=False)
    block = "".join(" " * col + ln + "\n" for ln in block.splitlines())
    new = "".join(lines[:e]) + block + "".join(lines[e:])
    want = dict(doc)
    want["orgs"] = [dict(o, protected_code=[item]) if k == at[0] else o for k, o in enumerate(orgs)]
    if yaml.safe_load(new) != want:
        raise LiveError(f"{orgs_file} would read differently after the edit; add the entry by hand:\n{block}")
    real = os.path.realpath(orgs_file)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(real), prefix=".orgs.yaml.")
    with os.fdopen(fd, "w") as fh:
        fh.write(new)
    os.chmod(tmp, os.stat(real).st_mode & 0o777)
    os.replace(tmp, real)
    return item


def switch(python=None, run=subprocess.run, live=LIVE, accounts=None, log=print, units=None):
    """Every hooked account's hooks, the units and ~/.local/bin/of from LIVE. The owner's, in a plain terminal."""
    python = python or sys.executable
    check_live(live)
    for cmd in switch_plan(python, hooked_accounts() if accounts is None else accounts, live, units):
        r = run(cmd)
        if getattr(r, "returncode", 1) != 0:   # the first failure stops the switch: ~/.local/bin/of stays as it was
            raise LiveError(f"{' '.join(cmd[2:])} failed; nothing after it ran. Fix it and run of live switch again")
        log(f"  {' '.join(cmd[2:5])}: done")
    tmp = LINK + ".new"
    if os.path.lexists(tmp):
        os.unlink(tmp)
    os.symlink(os.path.join(live, "bin", "of"), tmp)
    os.replace(tmp, LINK)
    log(f"  {LINK} -> {os.path.join(live, 'bin', 'of')}")


def request(path=REQUEST):
    """Ask the agent for a move to public main's tip: the request file touched, never through a link, and never a
    FIFO or anything else waited on (of_review on PR 628)."""
    import stat
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    except OSError as e:
        raise LiveError(f"{path} cannot be written as a plain file ({e.strerror})")
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise LiveError(f"{path} is no plain file")
        os.utime(fd)
    finally:
        os.close(fd)
    return path


def status(live=LIVE):
    """{live, head, link, hooks} for `of live status`."""
    head = None
    if os.path.isdir(os.path.join(live, ".git")):
        r = subprocess.run(["git", *SAFE_GIT, "-C", live, "rev-parse", "HEAD"], capture_output=True, text=True,
                           env=git_env())
        head = r.stdout.strip() or None
    import shutil
    found = shutil.which("of")   # the of a shell runs, which need not be ~/.local/bin/of (of_review on PR 628)
    link = os.path.realpath(found) if found else None
    return {"live": live, "head": head, "link": link,
            "accounts": [(d, flags) for d, flags in hooked_accounts()]}
