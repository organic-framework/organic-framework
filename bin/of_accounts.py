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
import shlex
import subprocess
import sys
from contextlib import contextmanager

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
    return os.fspath(home) if home else os.path.expanduser("~")


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


def _read(p):
    try:
        with open(p) as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def _document(p=None):
    text = _read(p or path())
    if text is None:
        return None
    import yaml
    doc = yaml.safe_load(text) or {}
    return doc if isinstance(doc, dict) else {}


def load(p=None):
    """The configured Claude accounts, or None while they are discovered by folder name."""
    doc = _document(p)
    if doc is None or ("codex_seats" in doc and "accounts" not in doc):
        return None
    out = []
    for e in doc.get("accounts") or []:
        if isinstance(e, dict) and e.get("name") and e.get("config_dir"):
            out.append({k: str(e[k]) for k in ("name", "config_dir", "label") if e.get(k)})
    return out


def _replace_list(text, key, entries):
    """Edit one top-level list, preserving the other sections and their comments verbatim."""
    import yaml
    root = yaml.compose(text)
    if root is not None and not isinstance(root, yaml.MappingNode):
        raise AccountError("accounts.yaml must contain a mapping of account lists")
    flow = bool(root and root.flow_style)
    body = yaml.safe_dump({key: entries}, sort_keys=False, allow_unicode=True, default_flow_style=bool(flow)).strip()
    if flow:
        body = body[1:-1]
    indent = " " * root.start_mark.column if root and not flow else ""
    body = body.replace("\n", "\n" + indent)
    pairs = root.value if root else []
    for i, (k, _) in enumerate(pairs):
        if k.value == key:
            # Only gaps between syntax tokens can hold comments. Keep them even when rewriting
            # this list. The block's end mark alone can reach the following section's comments.
            start = end = k.start_mark.index
            stop = pairs[i + 1][0].start_mark.index if i + 1 < len(pairs) else root.end_mark.index - int(flow)
            tokens = [t for t in yaml.scan(text) if start <= t.start_mark.index < t.end_mark.index <= stop]
            if flow and isinstance(tokens[-1], yaml.tokens.FlowEntryToken):
                tokens.pop()
            comments = []
            for token in tokens:
                left, right = token.start_mark.index, token.end_mark.index
                comments.extend(re.findall(r"#[^\n]*", text[end:left]))
                end = right
            replacement = ("\n" + indent).join([*comments, body])
            return text[:start] + replacement + text[end:]
    if flow:
        at = root.start_mark.index + 1
        return text[:at] + body + (", " if pairs else "") + text[at:]
    # Insert before a YAML document end marker, if present.
    at = root.end_mark.index if root else len(text)
    prefix = text[:at]
    return prefix + ("\n" if prefix and not prefix.endswith("\n") else "") + indent + body + "\n" + text[at:]


def save(entries=None, p=None, *, seats=None):
    p = p or path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    body = _read(p)
    if body is None:
        body = HEADER
    if entries is not None:
        body = _replace_list(body, "accounts", entries)
    if seats is not None:
        body = _replace_list(body, "codex_seats", seats)
    tmp = p + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(body)
    os.replace(tmp, p)


@contextmanager
def _editing():
    """Serialize account and seat changes across CLI processes."""
    import fcntl
    os.makedirs(os.path.dirname(path()), exist_ok=True)
    with open(path() + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def codex_seats(home=None):
    """Configured Codex sign-ins in list order, with their homes expanded."""
    import of_capacity
    doc = _document() or {}
    if "codex_seats" not in doc:
        return [{"name": "main", "home": of_capacity.codex_home(home)}]
    return [{"name": e["name"], "home": expand(e["home"], home)} for e in doc["codex_seats"] or []
            if isinstance(e, dict) and isinstance(e.get("name"), str) and NAME.fullmatch(e["name"]) and e.get("home")]


def codex_seat(name=None):
    """A named seat, defaulting to main or the first configured seat."""
    seats = codex_seats()
    default = seats[0] if name is None and seats else None
    return next((s for s in seats if s["name"] == (name or "main")), default)


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


def login_task(name, d, lang=None, codex=False):
    """(title, steps) of the owner's task to log in on a new account: one sitting, in a plain terminal."""
    from of_locale import tr
    if codex:
        folder = "~" if d == "~" else shlex.quote(d)
        if d.startswith("~/"):
            folder = "~/" + shlex.quote(d[2:])
        return (tr("Sign in to {names} in the account {acct}", lang, names="Codex", acct=name),
                tr("On {host}, open a plain terminal tab. Never run this command with \"!\" inside an agent's "
                   "session. Run the command and follow the device sign-in instructions.\n{line}", lang, host=_host(),
                   line=f"CODEX_HOME={folder} codex login --device-auth"))
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
        r = run([sys.executable, of_bin, "ask", title, "--task", steps], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return r.returncode == 0, ((r.stdout if r.returncode == 0 else r.stderr or r.stdout) or "").strip()


def add(name, config_dir=None, label=None, home=None, logged_in=None, ask=None, log=print, codex=False):
    """Add an account: create its folder (mode 700), list it, and file one owner task to log in there unless it is
    logged in already. The accounts found by name are listed too, so adding the first one hides none of them.
    `ask(title, steps)` -> (filed, output); when the task cannot be filed, the steps are printed for whoever runs this
    (in a plain terminal, the owner) and AccountError says so."""
    import of_capacity
    if not NAME.fullmatch(name or ""):
        raise AccountError(f"an account name is lowercase letters, digits, - and _, at most 32 characters: {name!r}")
    default = f"~/.codex-{name}" if codex else ("~/.claude" if name == "default" else f"~/.claude-{name}")
    d = config_dir or default
    full = expand(d, home)
    key = "home" if codex else "config_dir"
    with _editing():
        if codex:
            full = os.path.abspath(full)
            es = [dict(e, home=tilde(e["home"], home)) for e in codex_seats(home)]
        else:
            es = entries(home)
        for e in es:
            same = expand(e[key], home) == full
            if e["name"] == name and not same:
                raise AccountError(f"the account {name} already exists, with the folder {e[key]}")
            if same and e["name"] != name:
                raise AccountError(f"the folder {e[key]} is already the account {e['name']}")
        os.makedirs(full, mode=0o700, exist_ok=True)
        if not any(e["name"] == name for e in es):
            es.append({k: v for k, v in (("name", name), (key, tilde(full, home)), ("label", label)) if v})
        if codex:
            save(seats=es)
        else:
            save(es)
            try:
                of_capacity.ensure_statusline(full, log=log)
            except (OSError, ValueError) as e:
                log(f"account {name}: could not configure statusLine: {e}")
    log(f"account {name}: {tilde(full, home)} listed in {tilde(path(), home)}")
    if logged_in is None:
        logged_in = os.path.isfile(os.path.join(full, "auth.json")) if codex else bool(of_capacity.account_of(full, home))
    if logged_in:
        return None
    title, steps = login_task(name, tilde(full, home), codex=codex)
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
    with _editing():
        es = entries(home)
        seats = codex_seats(home)
        keep = [e for e in es if e["name"] != name]
        keep_seats = [dict(e, home=tilde(e["home"], home)) for e in seats if e["name"] != name]
        gone = [e for e in es + seats if e["name"] == name]
        if not gone:
            raise AccountError(f"no account named {name} (of account list)")
        save(keep if len(keep) != len(es) else None, seats=keep_seats if len(keep_seats) != len(seats) else None)
    for e in gone:
        log(f"account {name}: no longer used; its folder {e.get('config_dir') or e['home']} is kept as it is")


def adopt(home=None, log=print):
    """Write accounts.yaml from the accounts found by folder name, when there is no file yet."""
    with _editing():
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

RESUME_TIMEOUT_S = 60
RESUME_TRIES = 2   # one more try before refusing: a loaded machine can pass the timeout (2026-11-22: load 190 on 10 cores)


def resume_line(sid, of_bin=None, run=subprocess.run, why=None):
    """The line `of resume-command` gives for a session (its account, model, effort, permission mode), or None; `why`,
    a list, gets the reason there is none."""
    of_bin = of_bin or os.path.join(os.path.dirname(os.path.realpath(__file__)), "of")
    note = why.append if why is not None else (lambda _: None)
    try:
        # this Python, not PATH's first python3: in a login shell /usr/bin comes first and its python3 has no PyYAML,
        # so bin/of failed and the resume fell to another account (of_herdr's lab, 2026-11-11, hand-over a9c118e8)
        r = run([sys.executable, of_bin, "resume-command", sid], capture_output=True, text=True,
                timeout=RESUME_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        note(f"timed out after {RESUME_TIMEOUT_S} s")
        return None
    except OSError as e:
        note(f"did not run ({e})")
        return None
    line = (r.stdout or "").strip()
    if r.returncode == 0 and line:
        return line
    tail = ((r.stderr or "").strip().splitlines() or [""])[-1][:200]
    note(f"exited {r.returncode}" + (f": {tail}" if tail else "") if r.returncode else "printed no line")
    return None


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
    `--resume <id>` resumes the session as it ran, by the line `of resume-command` gives, or not at all; otherwise an
    already set CLAUDE_CONFIG_DIR is kept, else the account is chosen as of spawn chooses.

    With no line after RESUME_TRIES tries it raises AccountError, never starting Claude Code bare: a bare resume ran
    the session without its deny rules, its tool profile and, on Linux, its sandbox, with nothing said, and herdr's
    restore of an agent pane comes through here (hand-over af34d473 from of_architect, 2026-11-22)."""
    import of_capacity
    env = dict(os.environ if env is None else env)
    args, account = list(args), None
    if args and args[0] == "--account" and len(args) > 1:  # ours only first: the rest is Claude Code's, untouched
        account, args = args[1], args[2:]
    elif args and args[0].startswith("--account="):
        account, args = args[0].split("=", 1)[1], args[1:]
    if account is None and len(args) == 2 and args[0] == "--resume":
        why = []
        get = resumer or (lambda sid: resume_line(sid, why=why))
        for _ in range(RESUME_TRIES):
            line = get(args[1])
            if line:
                return ["/bin/sh", "-c", "exec " + line], env
        raise AccountError(f"no resume line for session {args[1]}: `of resume-command` "
                           f"{why[-1] if why else 'gave none'} ({RESUME_TRIES} tries). A bare start would run it "
                           f"without its deny rules, tool profile and sandbox, so it does not start. Run "
                           f"`of resume-command {args[1]}` to see why, then `of claude --resume {args[1]}` again.")
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
    ap = argparse.ArgumentParser(prog="of account", description="The Claude Code accounts and Codex seats on this machine "
                                                                "(docs/accounts.md)")
    sub = ap.add_subparsers(dest="action")
    ls = sub.add_parser("list", help="each account: email, login state, 5-hour and week use, live sessions")
    ls.add_argument("--fresh", action="store_true", help="probe every account now (else the last reading)")
    ls.add_argument("--json", action="store_true")
    a = sub.add_parser("add", help="create an account's folder, list it, and file the owner's task to log in there")
    a.add_argument("name")
    a.add_argument("--codex", action="store_true", help="add a Codex seat")
    a.add_argument("--home", help="Codex seat folder (default: ~/.codex-<name>)")
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
                for seat in codex_seats():
                    print(f"codex:{seat['name']}  {tilde(seat['home'])}")
        elif args.action == "add":
            if args.codex and (args.config_dir or args.label):
                ap.error("--config-dir and --label apply to Claude accounts")
            if args.home and not args.codex:
                ap.error("--home requires --codex")
            add(args.name, args.home if args.codex else args.config_dir, args.label, codex=args.codex)
        elif args.action == "remove":
            remove(args.name)
        elif args.action == "adopt":
            adopt()
    except AccountError as e:
        print(f"of account: {e}", file=sys.stderr)
        return 2
    return 0
