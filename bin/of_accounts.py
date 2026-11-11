"""The harness accounts on this machine (docs/accounts.md): the list OF chooses from, `of account add|list|remove|
adopt`, and `of claude`, which opens Claude Code on an account with no shell alias or shell function.

Owner: of_capacity. The list is `~/.config/of/accounts.yaml`, one entry per account: `name`, `config_dir`, optional
`label`. With no file, OF finds `~/.claude` and every `~/.claude-<name>` folder, as it always did, so a machine set up
by hand keeps working; `of account adopt` writes the file from what it finds. The founder, 2026-11-11 ~20:48Z, through
of_conductor (msg 01a1cc5f), translated: "I have now separated the Claude accounts by hand through the zsh config.
Properly it should be done more systematically, so that our colleagues can use it without customizing the zsh config."
"""

import glob
import json
import os
import re
import subprocess
import sys

import of_events

FILE = "accounts.yaml"
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
HEADER = ("# The Claude Code accounts on this machine (docs/accounts.md): name, config_dir, optional label.\n"
          "# `of account add|remove|adopt` writes it; OF starts, moves and resumes sessions only on these.\n")


class AccountError(Exception):
    pass


def path():
    return of_events.config_path(FILE)


def _home(home=None):
    return home or os.path.expanduser("~")


def expand(d, home=None):
    """A config folder as an absolute path; `~` is the home folder (or `home`, in tests)."""
    d = str(d)
    if d == "~" or d.startswith("~/"):
        d = _home(home) + d[1:]
    return os.path.normpath(d)


def tilde(d, home=None):
    h = _home(home)
    return "~" + d[len(h):] if d == h or d.startswith(h + os.sep) else d


def discovered(home=None):
    """`~/.claude` and every `~/.claude-<name>` directory, sorted. `~/.claude.json` is a file, not a dir. A name with
    a dot is not an account: Claude Code locks a config dir with a `<dir>.lock` directory, and on 2026-11-09 the
    probe took `~/.claude-d.lock` for an account and ran in it every 30 minutes, keeping the lock in place."""
    home = _home(home)
    out = [d for d in glob.glob(os.path.join(home, ".claude-*")) if os.path.isdir(d)
           and re.match(r"^\.claude-[A-Za-z0-9_-]+$", os.path.basename(d))]
    base = os.path.join(home, ".claude")
    if os.path.isdir(base):
        out.append(base)
    return sorted(out)


def load(p=None):
    """[{name, config_dir[, label]}] from accounts.yaml, or None when there is no file."""
    p = p or path()
    try:
        with open(p) as fh:
            text = fh.read()
    except FileNotFoundError:
        return None
    import yaml
    doc = yaml.safe_load(text) or {}
    out = []
    for e in (doc.get("accounts") or []) if isinstance(doc, dict) else []:
        if isinstance(e, dict) and e.get("name") and e.get("config_dir"):
            out.append({k: str(e[k]) for k in ("name", "config_dir", "label") if e.get(k)})
    return out


def save(entries, p=None):
    import yaml
    p = p or path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    body = yaml.safe_dump({"accounts": entries}, sort_keys=False, allow_unicode=True, default_flow_style=False)
    tmp = p + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(HEADER + body)
    os.replace(tmp, p)


def name_of(d, home=None):
    """"default" for `~/.claude`, "<name>" for `~/.claude-<name>`, else the folder's own name."""
    d = expand(d, home)
    if d == os.path.join(_home(home), ".claude"):
        return "default"
    base = os.path.basename(d)
    return base[len(".claude-"):] if base.startswith(".claude-") else base.lstrip(".")


def entries(home=None):
    """The accounts: the file's, or those found by folder name when there is no file."""
    listed = load()
    if listed is not None:
        return listed
    return [{"name": name_of(d, home), "config_dir": tilde(d, home)} for d in discovered(home)]


def dirs(home=None):
    """The config folders OF uses, sorted: the file's entries whose folder exists, else those found by name."""
    listed = load()
    if listed is None:
        return discovered(home)
    return sorted({expand(e["config_dir"], home) for e in listed if os.path.isdir(expand(e["config_dir"], home))})


def find(account, home=None):
    """The entry for a name or a config folder, or None."""
    for e in entries(home):
        if e["name"] == account or expand(e["config_dir"], home) == expand(account, home):
            return e
    return None


def _host():
    import platform
    return platform.node() or "this machine"


def login_task(name, d, lang=None):
    """(title, steps) of the owner's task to log in on a new account: one sitting, in a plain terminal."""
    from of_locale import tr
    kw = dict(name=name, dir=d, host=_host(), line=f"of claude --account {name} --dangerously-skip-permissions")
    return (tr("Log in to Claude Code in the new account {name}", lang, **kw),
            tr("OF added the Claude Code account {name} (folder {dir}) on {host}. Sessions start on it only after "
               "someone logs in there, and only the owner can.\n"
               "1. On {host}, in a plain terminal, not with \"!\" inside an agent's session: {line}\n"
               "2. Log in when Claude Code asks (or type /login), then accept the Bypass Permissions warning with "
               "\"Yes, I accept\": OF's agents run in that mode.\n"
               "3. Leave Claude Code and tell your conductor it is done; `of account list` then shows the account "
               "logged in.", lang, **kw))


def _file_task(title, steps, of_bin=None, run=subprocess.run):
    """File the owner's task as the caller, as of spawn files its own (the calling session's position; none in a plain
    terminal, where the owner runs it): (filed, output). No fixed sender: a colleague's organization has no
    of_capacity (of_platform's review of PR 22)."""
    of_bin = of_bin or os.path.join(os.path.dirname(os.path.realpath(__file__)), "of")
    try:
        r = run([of_bin, "ask", title, "--task", steps], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return r.returncode == 0, ((r.stdout if r.returncode == 0 else r.stderr or r.stdout) or "").strip()


def add(name, config_dir=None, label=None, home=None, logged_in=None, ask=None, log=print):
    """Add an account: create its folder (mode 700), list it, and file one owner task to log in there unless it is
    logged in already. The accounts found by name are listed too, so adding the first one hides none of them.
    `ask(title, steps)` -> (filed, output); when the task cannot be filed, the steps are printed for whoever runs this
    (in a plain terminal, the owner) and AccountError says so."""
    if not NAME.match(name or ""):
        raise AccountError(f"an account name is lowercase letters, digits, - and _, at most 32 characters: {name!r}")
    d = config_dir or ("~/.claude" if name == "default" else f"~/.claude-{name}")
    full = expand(d, home)
    es = entries(home)
    for e in es:
        same = expand(e["config_dir"], home) == full
        if e["name"] == name and not same:
            raise AccountError(f"the account {name} already exists, with the folder {e['config_dir']}")
        if same and e["name"] != name:
            raise AccountError(f"the folder {e['config_dir']} is already the account {e['name']}")
    os.makedirs(full, mode=0o700, exist_ok=True)
    if not any(e["name"] == name for e in es):
        es.append({k: v for k, v in (("name", name), ("config_dir", tilde(full, home)), ("label", label)) if v})
    save(es)
    log(f"account {name}: {tilde(full, home)} listed in {tilde(path(), home)}")
    if logged_in is None:
        import of_capacity
        logged_in = bool(of_capacity.account_of(full, home))
    if logged_in:
        return None
    title, steps = login_task(name, tilde(full, home))
    try:
        filed, out = ask(title, steps) if ask else _file_task(title, steps)
    except Exception as e:  # noqa: BLE001 - a failed filing is reported below, never as filed
        filed, out = False, f"{type(e).__name__}: {e}"
    if filed:
        log(f"account {name}: not logged in yet; the owner's task to log in: {out}")
        return title
    log(f"account {name}: not logged in yet, and the task to log in could not be filed ({out[-200:]}).\n"
        f"The steps, for whoever logs in:\n{title}\n{steps}")
    raise AccountError("the task to log in was not filed; its steps are above")


def remove(name, home=None, log=print):
    """Stop using an account: OF no longer starts, moves or resumes sessions there. Its folder is never deleted."""
    es = entries(home)
    keep = [e for e in es if e["name"] != name]
    if len(keep) == len(es):
        raise AccountError(f"no account named {name} (of account list)")
    save(keep)
    gone = next(e for e in es if e["name"] == name)
    log(f"account {name}: no longer used; its folder {gone['config_dir']} is kept as it is")


def adopt(home=None, log=print):
    """Write accounts.yaml from the accounts found by folder name, when there is no file yet."""
    if load() is not None:
        log(f"{tilde(path(), home)} exists already; nothing changed")
        return entries(home)
    es = entries(home)
    save(es)
    log(f"{tilde(path(), home)}: {len(es)} account(s): {', '.join(e['name'] for e in es)}")
    return es


def rows(home=None, fresh=False):
    """One row per account for `of account list`: its email, login state and limits from of_capacity's last reading
    (`fresh` probes now), and its live sessions."""
    import of_capacity
    by_dir = {r["config_dir"]: r for r in of_capacity.status([], dirs=dirs(home), fresh=fresh, probe=fresh)}
    out = []
    for e in entries(home):
        d = expand(e["config_dir"], home)
        r = by_dir.get(d) or {}
        w = r.get("windows") or {}
        out.append({"name": e["name"], "config_dir": e["config_dir"], "label": e.get("label"),
                    "exists": os.path.isdir(d), "account": of_capacity.account_of(d, home),
                    "logged_in": r.get("logged_in"), "five_hour": (w.get("five_hour") or {}).get("utilization"),
                    "seven_day": (w.get("seven_day") or {}).get("utilization"),
                    "sessions": len(of_capacity.live_sessions(d)) if os.path.isdir(d) else 0})
    return out


def _pct(v):
    return "—" if v is None else f"{v:.0f}%"


def show(rs, out=print):
    if not rs:
        out("no accounts (of account add <name>)")
        return
    w = max(len(r["name"]) for r in rs)
    for r in rs:
        state = ("no folder" if not r["exists"] else "logged in" if r["logged_in"] else
                 "not logged in" if r["logged_in"] is False or not r["account"] else "no reading")
        out(f"{r['name']:<{w}}  {r['config_dir']:<28} {r['account'] or '-':<36} {state:<13} "
            f"5h {_pct(r['five_hour']):>4}  7d {_pct(r['seven_day']):>4}  sessions {r['sessions']}"
            + (f"  ({r['label']})" if r.get("label") else ""))


# ------------------------------------------------------------------ of claude

def resume_line(sid, of_bin=None, run=subprocess.run):
    """The line `of resume-command` gives for a session (its account, model, effort, permission mode), or None."""
    of_bin = of_bin or os.path.join(os.path.dirname(os.path.realpath(__file__)), "of")
    try:
        r = run([of_bin, "resume-command", sid], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = (r.stdout or "").strip()
    return line if r.returncode == 0 and line else None


def choose(cwd, home=None, status_rows=None):
    """The config folder `of claude` opens with no --account: the account with the most headroom among those that
    trust the folder, else the one with the most headroom (the choice of spawn)."""
    import of_capacity
    import of_spawn
    rs = status_rows if status_rows is not None else of_capacity.status([], dirs=dirs(home), probe=False)
    ok = [r for r in rs if of_spawn.trusted_with_parents(expand(r["config_dir"], home), cwd, of_capacity.trusted_key)]
    t = of_capacity.best_target(ok) or of_capacity.best_target(rs)
    return expand(t["config_dir"], home) if t else None


def launch(args, env=None, cwd=None, home=None, chooser=None, resumer=None):
    """(argv, env) that `of claude ARGS` runs. `--account NAME` picks the account; without it the exact form
    `--resume <id>` resumes the session as it ran (the account that holds it); otherwise an already set
    CLAUDE_CONFIG_DIR is kept, else the account is chosen as of spawn chooses."""
    import of_capacity
    env = dict(os.environ if env is None else env)
    args, account = list(args), None
    if args and args[0] == "--account" and len(args) > 1:  # ours only first: the rest is Claude Code's, untouched
        account, args = args[1], args[2:]
    elif args and args[0].startswith("--account="):
        account, args = args[0].split("=", 1)[1], args[1:]
    if account is None and len(args) == 2 and args[0] == "--resume":
        line = (resumer or resume_line)(args[1])
        if line:
            return ["/bin/sh", "-c", "exec " + line], env
    if account is not None:
        e = find(account, home)
        if not e:
            raise AccountError(f"no account named {account} (of account list)")
        d = expand(e["config_dir"], home)
    elif env.get("CLAUDE_CONFIG_DIR"):
        d = expand(env["CLAUDE_CONFIG_DIR"], home)
    else:
        d = (chooser or choose)(cwd or os.getcwd(), home)
        if not d:
            raise AccountError("no logged-in account with headroom (of account list); pass --account NAME")
    if of_capacity.is_default(d, home):
        env.pop("CLAUDE_CONFIG_DIR", None)
    else:
        env["CLAUDE_CONFIG_DIR"] = d
    return [of_capacity.claude_bin()] + args, env


def claude(argv):
    """`of claude [--account NAME] [claude arguments]`: replaces this process with Claude Code on that account."""
    try:
        cmd, env = launch(argv)
    except AccountError as e:
        print(f"of claude: {e}", file=sys.stderr)
        return 2
    if cmd[0] == "/bin/sh":
        print(f"of: {cmd[2][len('exec '):]}", file=sys.stderr)
    os.execvpe(cmd[0], cmd, env)


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(prog="of account", description="The Claude Code accounts OF uses on this machine "
                                                                "(docs/accounts.md)")
    sub = ap.add_subparsers(dest="action")
    ls = sub.add_parser("list", help="each account: email, login state, 5-hour and week use, live sessions")
    ls.add_argument("--fresh", action="store_true", help="probe every account now (else the last reading)")
    ls.add_argument("--json", action="store_true")
    a = sub.add_parser("add", help="create an account's folder, list it, and file the owner's task to log in there")
    a.add_argument("name")
    a.add_argument("--config-dir", dest="config_dir", help="default: ~/.claude-<name> (~/.claude for 'default')")
    a.add_argument("--label", help="a note shown in the list, for example whose subscription it is")
    r = sub.add_parser("remove", help="stop using an account; its folder is kept")
    r.add_argument("name")
    sub.add_parser("adopt", help="write accounts.yaml from the folders found by name (~/.claude, ~/.claude-<name>)")
    args = ap.parse_args(argv)
    try:
        if args.action in (None, "list"):
            rs = rows(fresh=getattr(args, "fresh", False))
            if getattr(args, "json", False):
                print(json.dumps(rs, indent=1))
            else:
                show(rs)
                if load() is None:
                    print(f"(found by folder name; `of account adopt` writes {tilde(path())})")
        elif args.action == "add":
            add(args.name, args.config_dir, args.label)
        elif args.action == "remove":
            remove(args.name)
        elif args.action == "adopt":
            adopt()
    except AccountError as e:
        print(f"of account: {e}", file=sys.stderr)
        return 2
    return 0
