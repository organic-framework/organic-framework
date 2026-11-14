#!/usr/bin/env python3
"""The agent host's OF plane, one per organization user (organic-framework-build reports/
agent-server-migration-plan-2026-11-11.md §3 B6, B2 and §9; of_agent-server's hand-over 5de421ee; owned by of_platform).
of_server's deploy/host/setup.py sets up the server side (the service user, of-server, ClickHouse, a relay per
organization user, the root watchdog); this script sets up what each organization user runs itself, under the same
rules for root (organic-framework PR 144): root follows no link and runs no code another user can change.

- What is the user's is written by the user: `sudo -u <user>` runs this file's user_write() for ~/.config/of/orgs.yaml
  (its own organizations only), ~/.local/bin/of (a wrapper of the release checkout's bin/of) and one marked block in
  ~/.zshrc (that PATH and the shell function), so a link the user planted leads only where the user may write. The
  user also installs PyYAML for itself when it is missing, and the hooks, the guard and the deny rules in each of its
  Claude config dirs (`of hooks install`).
- What is root's root writes through setup.py's Files (one component at a time, O_NOFOLLOW, O_EXCL): the
  LaunchDaemons with UserName at ProcessType Standard, written and not loaded (the user's own herdr server, the
  listener, `of health run` every minute), every daemon's log made beforehand in root's /var/log/of, and one root
  watchdog per user (`python -E -s`, of_listen.watchdog) that restarts a stuck listener, its state in root's own
  /var/db/of-plane. It reads that user's two beat files by stat, never opening them (of_listen.watchdog).
- `apply`, `start` and `deploy` refuse to run unless setup.check_code passes: the python, Homebrew and the release
  checkout belong to root (or an admin who is no organization user) and no organization user may write them.
- `deploy --from SRC --ref REF` (root): the read-only release checkout /opt/organic-framework at REF, a branch or
  tag of SRC. SRC's owner makes a git bundle of it (root runs no git in another user's repository); root copies the
  bundle into its own folder without following a link and fetches from that copy; the owner runs the smoke check
  (bin/of smoke) on the checkout, never root; a failing commit is put back to the one before. Root then compiles bin/
  (the users cannot write a read-only checkout's bytecode cache) and makes it root's, readable by all.
- `start` / `stop` (root) load and unload the plane's daemons; `check` runs, as each user, `of registry check`, the
  guard's probe in each config dir and a look at its orgs.yaml, and asks launchd for the daemons.
- `plan` (the default) prints what `apply` would do and changes nothing.

  sudo python3 deploy/host/plane.py plan  --org-user of:organic-framework-build=<of's home>/organic-framework-build
  sudo python3 deploy/host/plane.py apply --org-user of:organic-framework-build=<of's home>/organic-framework-build \\
                                          --org-user acme:acme=<acme's home>/acme-org --claude-dir acme:.claude-a
  sudo python3 deploy/host/plane.py deploy --from <of's home>/organic-framework --ref main
  sudo python3 deploy/host/plane.py start|stop|check --org-user ...

The owner's remote view (B4: saved herdr machines `of@localhost` and `acme@localhost` under the admin user, for the
phone) is in docs/remote-access.md, "A separate agent host".
"""

import argparse
import json
import os
import pwd
import stat
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import setup as host  # noqa: E402  (of_server's deploy/host/setup.py: Files, check_code, the paths)

OF_ROOT = host.OF_ROOT
PYTHON = host.PYTHON
HERDR = os.path.join(host.HOMEBREW, "bin", "herdr")
PATH = host.PATH
DAEMONS = host.DAEMONS
LOGS = host.LOGS
WATCH_STATE = "/var/db/of-plane"          # root's alone: each root watchdog's state
DEPLOY_DIR = "/var/db/of-plane-deploy"    # root's alone: the copied bundle
HANDOFF_DIR = "/var/db/of-plane-handoff"  # lent to the source's owner for its bundle, then taken back
MARK = "# >>> organic-framework plane (deploy/host/plane.py) >>>"
END = "# <<< organic-framework plane <<<"
SERVICES = ("herdr", "listen", "health", "listen-watchdog")


def label(service, user):
    return f"dev.organic-framework.{service}.{user}"


def parse_org_users(items):
    """`user:org=repo` (repeatable) -> {user: [(org, repo), ...]}."""
    out = {}
    for item in items or []:
        user, _, rest = item.partition(":")
        org, _, repo = rest.partition("=")
        if not (user and host.USER_NAME.fullmatch(user) and org and repo and os.path.isabs(repo)):
            raise SystemExit(f"--org-user {item!r}: write user:org=/absolute/repo/path")
        out.setdefault(user, []).append((org, repo))
    return out


def parse_claude_dirs(items, users):
    """`user:dir` (repeatable; a relative dir is under the user's home) -> {user: [dir, ...]}; ~/.claude by default."""
    out = {u: [] for u in users}
    for item in items or []:
        user, _, d = item.partition(":")
        if user not in out or not d:
            raise SystemExit(f"--claude-dir {item!r}: write user:dir for a user given with --org-user")
        out[user].append(d)
    return {u: (ds or [".claude"]) for u, ds in out.items()}


def home(user):
    return pwd.getpwnam(user).pw_dir


# ------------------------------------------------------------------ what the user writes, as the user

def orgs_yaml(orgs, user_home):
    """Only the user's own organizations: another user's registry is not this plane's (§9, 5)."""
    lines = ["# written by deploy/host/plane.py: this user's organizations only", "orgs:"]
    for org, repo in orgs:
        if not os.path.abspath(repo).startswith(os.path.abspath(user_home) + os.sep):
            raise SystemExit(f"{org}: its repository {repo} is not in {user_home}; each organization's checkout lives "
                             "in its own user's home")
        lines += [f"  - name: {org}", f"    path: {repo}", f"    workspace: {org}"]
    return "\n".join(lines) + "\n"


def wrapper():
    return f'#!/bin/sh\n# written by deploy/host/plane.py\nexec {PYTHON} {OF_ROOT}/bin/of "$@"\n'


def zshrc(text):
    """The user's ~/.zshrc with the plane's one block, replaced in place when it is there already."""
    block = (f"{MARK}\nexport PATH=\"$HOME/.local/bin:/opt/homebrew/bin:$PATH\"\n"
             f"[ -r {OF_ROOT}/bin/of-shell.zsh ] && source {OF_ROOT}/bin/of-shell.zsh\n{END}\n")
    if MARK in text and END in text:
        a, b = text.index(MARK), text.index(END) + len(END)
        return text[:a] + block + text[b:].lstrip("\n")
    return text + ("" if not text or text.endswith("\n") else "\n") + block


def user_files(orgs, user_home):
    """[(path under the home, text, mode)] the user writes for itself."""
    return [(".config/of/orgs.yaml", orgs_yaml(orgs, user_home), 0o600), (".local/bin/of", wrapper(), 0o755)]


def user_write(spec, home_dir=None):
    """Run as the user (sudo -u <user> ... user_write): write SPEC's files under its home and the ~/.zshrc block."""
    h = home_dir or os.path.expanduser("~")
    for rel, text, mode in spec["files"]:
        path = os.path.join(h, rel)
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        with open(path + ".tmp", "w") as fh:
            fh.write(text)
        os.chmod(path + ".tmp", mode)
        os.replace(path + ".tmp", path)
    rc = os.path.join(h, ".zshrc")
    try:
        with open(rc) as fh:
            old = fh.read()
    except FileNotFoundError:
        old = ""
    new = zshrc(old)
    if new != old:
        with open(rc, "w") as fh:
            fh.write(new)
    return [rel for rel, _, _ in spec["files"]] + [".zshrc"]


def as_user(user, argv, run=subprocess.run, stdin=None, env=None):
    extra = [f"{k}={v}" for k, v in (env or {}).items()]
    return run(["sudo", "-u", user, "-H", "env", f"PATH={PATH}", "OF_AUTOMATION=1", "SSH_AUTH_SOCK=", *extra, *argv],
               capture_output=True, text=True, input=stdin)


def user_write_argv():
    here = os.path.dirname(os.path.abspath(__file__))
    return [PYTHON, "-c", f"import json, sys; sys.path.insert(0, {here!r}); import plane; "
                          "print(plane.user_write(json.load(sys.stdin)))"]


# ------------------------------------------------------------------ what root writes, through setup.Files

def plist(lbl, argv, user=None, user_home=None, env=None, keep=False, interval=None, log=None):
    from xml.sax.saxutils import escape
    envs = {"OF_AUTOMATION": "1", "SSH_AUTH_SOCK": "", "PATH": PATH, **({"HOME": user_home} if user_home else {}),
            **(env or {})}
    e = "".join(f"<key>{escape(k)}</key><string>{escape(v)}</string>" for k, v in envs.items())
    args = "".join(f"<string>{escape(a)}</string>" for a in argv)
    extra = ""
    if user:
        extra += f"  <key>UserName</key><string>{escape(user)}</string>\n"
    if keep:
        extra += "  <key>RunAtLoad</key><true/>\n  <key>KeepAlive</key><true/>\n  <key>ThrottleInterval</key><integer>10</integer>\n"
    if interval:
        extra += f"  <key>StartInterval</key><integer>{interval}</integer>\n"
    if user_home:
        extra += f"  <key>WorkingDirectory</key><string>{escape(user_home)}</string>\n"
    log = log or "/dev/null"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{escape(lbl)}</string>
  <key>ProgramArguments</key><array>{args}</array>
  <key>EnvironmentVariables</key><dict>{e}</dict>
  <key>ProcessType</key><string>Standard</string>
{extra}  <key>StandardOutPath</key><string>{escape(log)}</string>
  <key>StandardErrorPath</key><string>{escape(log)}</string>
</dict></plist>
"""


def daemons(user, user_home):
    """{label: (plist, the log's owner)} of one organization user's plane. Each runs as that user, except the watchdog
    (root, so it can restart the user's system daemon), which reads that user's beat files and keeps its state in
    root's folder. Every log is in root's LOGS, made before launchd opens it."""
    state = os.path.join(user_home, ".local", "state", "of")
    of = [PYTHON, f"{OF_ROOT}/bin/of"]
    lg = lambda svc: os.path.join(LOGS, f"plane-{svc}-{user}.log")  # noqa: E731
    return {
        label("herdr", user): (plist(label("herdr", user), [HERDR, "server"], user, user_home, keep=True,
                                     log=lg("herdr")), user),
        label("listen", user): (plist(label("listen", user), of + ["listen", "run"], user, user_home, keep=True,
                                      log=lg("listen")), user),
        label("health", user): (plist(label("health", user), of + ["health", "run"], user, user_home, interval=60,
                                      log=lg("health")), user),
        label("listen-watchdog", user): (plist(  # of_listen alone, which needs no PyYAML; -E -s: no env, no user site
            label("listen-watchdog", user),
            [PYTHON, "-E", "-s", "-c", f"import sys; sys.path.insert(0, '{OF_ROOT}/bin'); import of_listen; "
                                       "print(of_listen.watchdog())"], None, None, interval=60,
            env={"OF_STATE": state, "OF_LISTEN_TARGET": f"system/{label('listen', user)}",
                 "OF_LISTEN_WATCHDOG_STATE": os.path.join(WATCH_STATE, f"{user}-listen-watchdog.json")},
            log=lg("listen-watchdog")), "root"),
    }


# ------------------------------------------------------------------ apply, start, stop

def steps(org_users, claude_dirs, homes=None):
    """[(user or None for root, description)] of `apply`, in order: what `plan` prints."""
    homes = homes or {u: home(u) for u in org_users}
    out = [(None, "check: the python, Homebrew and the release checkout are root's and no organization user's"),
           (None, f"mkdir {WATCH_STATE} (700 root:wheel)")]
    for user, orgs in org_users.items():
        h = homes[user]
        out.append((user, "as the user: " + ", ".join(rel for rel, _, _ in user_files(orgs, h)) + ", ~/.zshrc block"))
        out.append((user, "as the user: PyYAML for Homebrew's Python, if it is missing"))
        for d in claude_dirs[user]:
            cfg = d if os.path.isabs(d) else os.path.join(h, d)
            out.append((user, f"as the user: hooks, guard and deny rules in {cfg}"))
        for lbl, (_, owner) in daemons(user, h).items():
            out.append((None, f"log {LOGS}/…{lbl.rsplit('.', 2)[-2]}-{user}.log (600 {owner}); "
                              f"LaunchDaemon {lbl} (written, not loaded)"))
    return out


def trust(org_users, run=subprocess.run):
    problems = host.check_code(list(org_users), run=run, here=__file__)
    if problems:
        raise SystemExit("refused: the code root would run is not root's alone:\n  " + "\n  ".join(problems))


def apply(org_users, claude_dirs, run=subprocess.run, log=print, homes=None, files=None, checked=False):
    homes = homes or {u: home(u) for u in org_users}
    if not checked:
        trust(org_users, run)
    files = files or host.Files()
    files.mkdir_now(LOGS, 0o755)  # as setup.py makes it
    files.mkdir_now(WATCH_STATE, 0o700)
    for user, orgs in org_users.items():
        h = homes[user]
        spec = {"files": user_files(orgs, h)}
        r = as_user(user, user_write_argv(), run, stdin=json.dumps(spec))
        if r.returncode != 0:
            raise SystemExit(f"{user}: its files: {(r.stderr or r.stdout).strip()[-400:]}")
        log(f"{user}: {', '.join(rel for rel, _, _ in spec['files'])}, ~/.zshrc block")
        r = as_user(user, ["sh", "-c", f"{PYTHON} -c 'import yaml' 2>/dev/null || "
                                        f"{PYTHON} -m pip install --user --break-system-packages pyyaml"], run)
        if r.returncode != 0:
            raise SystemExit(f"{user}: PyYAML: {(r.stderr or r.stdout).strip()[-400:]}")
        for d in claude_dirs[user]:
            cfg = d if os.path.isabs(d) else os.path.join(h, d)
            r = as_user(user, [PYTHON, f"{OF_ROOT}/bin/of", "hooks", "install", "--guard", "--deny-secrets",
                               "--config-dir", cfg, "--yes"], run)
            if r.returncode != 0:
                raise SystemExit(f"{user}: hooks in {cfg}: {(r.stderr or r.stdout).strip()[-400:]}")
            log(f"{user}: hooks, guard and deny rules in {cfg}")
        for lbl, (text, owner) in daemons(user, h).items():
            log_path = plist_log(text)
            files.log_now(log_path, owner)
            files.write_now(os.path.join(DAEMONS, f"{lbl}.plist"), text, 0o644)
            log(f"root: {lbl} (written, not loaded; log {log_path})")


def plist_log(text):
    import plistlib
    return plistlib.loads(text.encode())["StandardOutPath"]


def load(org_users, unload=False, run=subprocess.run, log=print):
    for user in org_users:
        for svc in SERVICES:
            lbl = label(svc, user)
            path = os.path.join(DAEMONS, f"{lbl}.plist")
            run(["launchctl", "bootout", f"system/{lbl}"], capture_output=True)
            if not unload:
                r = run(["launchctl", "bootstrap", "system", path], capture_output=True, text=True)
                log(f"{lbl}: {'loaded' if r.returncode == 0 else 'not loaded: ' + (r.stderr or '').strip()[:200]}")
            else:
                log(f"{lbl}: unloaded")


# ------------------------------------------------------------------ deploy

def _take_back(path, files):
    """The handoff folder made root's alone again, by its descriptor, before root reads what the user left there."""
    fd = files.folder(path)
    try:
        if not files.root:
            os.fchown(fd, 0, 0)
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)


def _copy_bundle(src_dir, name, dst, files, owner):
    """Root's own copy of the user's bundle: read with O_NOFOLLOW|O_NONBLOCK, a plain file of the owner with one link."""
    dfd = files.folder(src_dir)
    try:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
        except OSError as e:
            raise SystemExit(f"refused: {src_dir}/{name} is a link or missing ({e.strerror})") from None
    finally:
        os.close(dfd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid not in {0, os.geteuid(), files.uid(owner)}:
            raise SystemExit(f"refused: {src_dir}/{name} is not a plain file of {owner} with one link")
        data = b""
        while True:
            chunk = os.read(fd, 1 << 20)
            if not chunk:
                break
            data += chunk
    finally:
        os.close(fd)
    _write_bytes(files, dst, data)


def _write_bytes(files, path, data):
    parent, name = os.path.split(path)
    pfd = files.folder(parent, create=True)
    try:
        files.unlink(pfd, name, path)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=pfd)
        try:
            host._write_all(fd, data)
        finally:
            os.close(fd)
    finally:
        os.close(pfd)


def deploy(src, ref, run=subprocess.run, log=print, root=None, files=None, owner=None, checked=False):
    """The release checkout at REF (a branch or tag of SRC); see the module's docstring. Returns the commit."""
    root = root or OF_ROOT
    files = files or host.Files()
    owner = owner or pwd.getpwuid(os.stat(src).st_uid).pw_name
    handoff = files.p(HANDOFF_DIR) if files.root else HANDOFF_DIR
    deploy_dir = files.p(DEPLOY_DIR) if files.root else DEPLOY_DIR
    files.mkdir_now(HANDOFF_DIR, 0o700)
    fd = files.folder(HANDOFF_DIR)  # lent to the owner for its bundle
    try:
        files.own(fd, owner, "staff")
    finally:
        os.close(fd)
    r = as_user(owner, ["git", "-C", src, "bundle", "create", os.path.join(handoff, "release.bundle"), ref], run)
    _take_back(HANDOFF_DIR, files)
    if r.returncode != 0:
        raise SystemExit(f"deploy: {owner} could not bundle {ref} of {src}: {(r.stderr or r.stdout).strip()[-300:]}")
    files.mkdir_now(DEPLOY_DIR, 0o700)
    copy = os.path.join(deploy_dir, "release.bundle")
    _copy_bundle(HANDOFF_DIR, "release.bundle", DEPLOY_DIR + "/release.bundle", files, owner)

    def git(*a, check=True):
        r = run(["git", "-C", root, *a], capture_output=True, text=True)
        if check and r.returncode != 0:
            raise SystemExit(f"deploy: git {' '.join(a[:2])}: {(r.stderr or r.stdout).strip()[:300]}")
        return r.stdout.strip()
    if not os.path.isdir(os.path.join(root, ".git")):
        r = run(["git", "clone", "-q", "--no-checkout", copy, root], capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"deploy: git clone: {(r.stderr or r.stdout).strip()[:300]}")
        before = None
    else:
        before = git("rev-parse", "HEAD", check=False) or None
    git("fetch", "-q", copy, ref)
    git("checkout", "-q", "--detach", "FETCH_HEAD")
    now = git("rev-parse", "HEAD")
    # the owner runs the smoke check, never root; git in root's checkout needs it named safe for that user
    r = as_user(owner, [PYTHON, os.path.join(root, "bin", "of"), "smoke", "--repo", root], run,
                env={"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": root})
    if r.returncode != 0:
        if before:
            git("checkout", "-q", "--detach", before)
        raise SystemExit(f"deploy: the smoke check failed on {now[:12]}; "
                         f"{'back on ' + before[:12] if before else 'nothing was checked out before'}: "
                         f"{(r.stderr or r.stdout).strip()[-300:]}")
    # the users cannot write a read-only checkout's bytecode cache: compile it here (compiling runs no code)
    run([PYTHON, "-E", "-s", "-m", "compileall", "-q", os.path.join(root, "bin")], capture_output=True)
    run(["chown", "-R", "-P", "root:wheel", root], capture_output=True)
    run(["chmod", "-R", "u+rwX,go+rX,go-w", root], capture_output=True)
    log(f"deploy: {root} at {now[:12]} (smoke check ok, by {owner})")
    return now


# ------------------------------------------------------------------ check

def check(org_users, claude_dirs, run=subprocess.run, log=print, homes=None):
    """True when every user's plane is whole; one line per finding. What is in a user's home is read as the user."""
    homes = homes or {u: home(u) for u in org_users}
    ok = True
    others = {org for u, orgs in org_users.items() for org, _ in orgs}
    for user, orgs in org_users.items():
        h = homes[user]
        mine = {org for org, _ in orgs}
        r = as_user(user, [PYTHON, f"{OF_ROOT}/bin/of", "registry", "check"], run)
        ok &= r.returncode == 0
        log(f"{user}: of registry check: {'ok' if r.returncode == 0 else 'FAILED'}")
        for d in claude_dirs[user]:
            cfg = d if os.path.isabs(d) else os.path.join(h, d)
            code = (f"import json,sys; sys.path.insert(0,'{OF_ROOT}/bin'); import of_init; "
                    f"s=json.load(open('{cfg}/settings.json')); print(of_init.guard_works(s))")
            r = as_user(user, [PYTHON, "-c", code], run)
            good = r.returncode == 0 and "True" in (r.stdout or "")
            ok &= good
            log(f"{user}: the guard in {cfg}: {'answers deny' if good else 'NOT working: ' + (r.stdout or r.stderr)[-200:]}")
        for svc in SERVICES:
            r = run(["launchctl", "print", f"system/{label(svc, user)}"], capture_output=True, text=True)
            ok &= r.returncode == 0
            log(f"{user}: {label(svc, user)}: {'loaded' if r.returncode == 0 else 'NOT loaded'}")
        r = as_user(user, [HERDR, "status", "server"], run)
        ok &= r.returncode == 0
        log(f"{user}: herdr server: {'answers' if r.returncode == 0 else 'NOT answering'}")
        r = as_user(user, ["sh", "-c", "grep -h '^  - name:' \"$HOME/.config/of/orgs.yaml\""], run)
        listed = {ln.split(":", 1)[1].strip() for ln in (r.stdout or "").splitlines() if ":" in ln}
        foreign = listed & (others - mine)
        ok &= not foreign
        log(f"{user}: orgs.yaml: {'only its own' if not foreign else 'ALSO ' + ', '.join(sorted(foreign))}")
    return ok


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("action", nargs="?", default="plan", choices=["plan", "apply", "start", "stop", "check", "deploy"])
    ap.add_argument("--org-user", action="append", default=[], help="user:org=/absolute/repo (repeatable)")
    ap.add_argument("--claude-dir", action="append", default=[], help="user:dir (repeatable; ~/.claude by default)")
    ap.add_argument("--from", dest="src", help="deploy: the repository to take the release from")
    ap.add_argument("--ref", help="deploy: a branch or tag of it")
    a = ap.parse_args(argv)
    if a.action == "deploy":
        if not (a.src and a.ref):
            raise SystemExit("deploy needs --from <repository> and --ref <branch or tag>")
        if os.geteuid() != 0:
            raise SystemExit("deploy: run it as root (sudo)")
        deploy(a.src, a.ref)
        trust(parse_org_users(a.org_user) or {}, subprocess.run)  # the checkout it made: root's alone
        return 0
    org_users = parse_org_users(a.org_user)
    if not org_users:
        raise SystemExit("name each organization user: --org-user user:org=/absolute/repo")
    claude = parse_claude_dirs(a.claude_dir, org_users)
    if a.action == "plan":
        for user, what in steps(org_users, claude):
            print(f"{user or 'root'}: {what}")
        return 0
    if a.action == "check":
        return 0 if check(org_users, claude) else 1
    if os.geteuid() != 0:
        raise SystemExit(f"{a.action}: run it as root (sudo)")
    if a.action == "stop":
        load(org_users, unload=True)
        return 0
    trust(org_users)
    if a.action == "apply":
        apply(org_users, claude, checked=True)
    else:
        load(org_users)
    return 0


if __name__ == "__main__":
    sys.exit(main())
