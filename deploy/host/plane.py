#!/usr/bin/env python3
"""The agent host's OF plane, one per organization user (organic-framework-build reports/
agent-server-migration-plan-2026-11-11.md §3 B6, B2 and §9; of_agent-server's hand-over 5de421ee; owned by of_platform).
of_server's deploy/host/setup.py sets up the server side (the service user, of-server, ClickHouse, a relay per
organization user, the root watchdog); this script sets up what each organization user runs itself, under the same
rules for root (organic-framework PR 144): root follows no link and runs no code another user can change.

- What is the user's is written by the user: `sudo -u <user>` runs this file's user_write() for ~/.config/of/orgs.yaml
  (its own organizations only), ~/.local/bin/of (a wrapper of the release checkout's bin/of) and one marked block in
  ~/.zshrc (that PATH and the shell function), so a link the user planted leads only where the user may write. The
  user also installs the hooks, the guard and the deny rules in each of its Claude config dirs (`of hooks install`).
  PyYAML comes with Homebrew's python itself: setup.check_code refuses one without it (organic-framework PR 147).
- What is root's root writes through setup.py's Files (one component at a time, O_NOFOLLOW, O_EXCL): the
  LaunchDaemons with UserName at ProcessType Standard, written and not loaded (the user's own herdr server, the
  listener, `of health run` every minute), every daemon's log made beforehand in root's /var/log/of, and one root
  watchdog per user (`python -I`, of_listen.watchdog) that restarts a stuck listener, its state in root's own
  /var/db/of-plane. It reads that user's two beat files by stat, never opening them (of_listen.watchdog).
- `apply` and `start` refuse to run unless setup.check_code passes: the python, Homebrew and the release checkout
  belong to root (or an admin who is no organization user) and no organization user may write them. `deploy` first
  runs the same trust check over this script's folder and the python running it (setup.interpreter_roots: its
  prefix, its sys.path and its executable), and after the checkout over the checkout itself.
- `deploy --commit HASH --smoke-as USER --org-user ...` (root): the read-only release checkout /opt/organic-framework
  at HASH, a full commit hash the public main contains. Root fetches from the public repository itself, never from
  an organization user's checkout, whose main may hold commits no one reviewed (of_secrets-review on PR 148). The tree
  is made root's, the trust check runs, USER (never root) runs the smoke check (bin/of smoke), and only then does
  root compile bin/ with python -I from /; a failing step puts the commit before back. Root runs every program by
  its full path.
- `start` / `stop` (root) load and unload the plane's daemons; `check` runs, as each user, `of registry check`, the
  guard's probe in each config dir and a look at its orgs.yaml, and asks launchd for the daemons.
- `plan` (the default) prints what `apply` would do and changes nothing.

  sudo python3 deploy/host/plane.py plan  --org-user of:organic-framework-build=<of's home>/organic-framework-build
  sudo python3 deploy/host/plane.py apply --org-user of:organic-framework-build=<of's home>/organic-framework-build \\
                                          --org-user acme:acme=<acme's home>/acme-org --claude-dir acme:.claude-a
  sudo python3 deploy/host/plane.py deploy --commit <full hash> --smoke-as of --org-user of:... --org-user acme:...
  sudo python3 deploy/host/plane.py start|stop|check --org-user ...

The owner's remote view (B4: saved herdr machines `of@localhost` and `acme@localhost` under the admin user, for the
phone) is in docs/remote-access.md, "A separate agent host".
"""

import argparse
import json
import os
import pwd
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
# root runs programs by their full path, never as PATH finds them (of_review on PR 148)
GIT, SUDO, ENV, CHOWN, CHMOD, LAUNCHCTL, SH = ("/usr/bin/git", "/usr/bin/sudo", "/usr/bin/env", "/usr/sbin/chown",
                                              "/bin/chmod", "/bin/launchctl", "/bin/sh")
ROOT_ENV = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": "/var/root", "LANG": "C"}  # root's runs: a fixed PATH
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
    return run([SUDO, "-u", user, "-H", ENV, f"PATH={PATH}", "OF_AUTOMATION=1", "SSH_AUTH_SOCK=", *extra, *argv],
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
        # of_listen alone, which needs no PyYAML; -I: no env, no user site, no working folder
        label("listen-watchdog", user): (plist(
            label("listen-watchdog", user),
            [PYTHON, "-I", "-c", f"import sys; sys.path.insert(0, '{OF_ROOT}/bin'); import of_listen; "
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
            run([LAUNCHCTL, "bootout", f"system/{lbl}"], capture_output=True)
            if not unload:
                r = run([LAUNCHCTL, "bootstrap", "system", path], capture_output=True, text=True)
                log(f"{lbl}: {'loaded' if r.returncode == 0 else 'not loaded: ' + (r.stderr or '').strip()[:200]}")
            else:
                log(f"{lbl}: unloaded")


# ------------------------------------------------------------------ deploy

PUBLIC = "https://github.com/organic-framework/organic-framework.git"  # reviewed code only: what of_release merged


GIT_SAFE = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]  # root's git runs no hook, no monitor
GIT_ENV = {**ROOT_ENV, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0"}


def deploy(commit, smoke_as=None, org_users=(), run=subprocess.run, log=print, root=None, source=PUBLIC,
           trust=None, self_check=None, final_check=None):
    """The release checkout at COMMIT, a full hash the public main contains. Root never runs git in an existing
    checkout, whose .git may redirect the fetch (insteadOf) or run a hook as root (of_server on PR 148): each deploy
    is a new folder root makes, a fresh `git init`, a fetch of the public main and a checkout of exactly that hash,
    with hooks and the file system monitor off and no system or global git config. In order: what runs this script
    passes the trust check (its folder and setup.interpreter_roots); the new tree, made root's and read-only, passes
    it; an organization user (SMOKE_AS, never root) runs the smoke check on it; root compiles its bin/ (python -I, from
    /); the new tree is renamed into place, the old one kept as .prev; and setup.check_code, the rule apply and start
    use, passes on it, or the old tree is renamed back. A failing step deletes the new tree and runs no git in it; the
    tree in place is never touched by git. Programs by their full path, with a fixed PATH. Returns the commit."""
    import re
    import shutil
    root = root or OF_ROOT
    # first, what runs this: the script's folder and the python must be root's (or an admin's) and no organization
    # user's to write, before root does anything for it (of_review on PR 148)
    me = (self_check or (lambda: host.trust_problems(
        [os.path.dirname(os.path.realpath(__file__)), *host.interpreter_roots()],  # the python's prefix and path too
        [*org_users, host.SERVICE_USER], run=run)))()
    if me:
        raise SystemExit("deploy: refused: what runs this script is not root's alone: " + "; ".join(me)[:400])
    if not re.fullmatch(r"[0-9a-f]{40}", str(commit or "")):
        raise SystemExit("deploy: --commit takes a full commit hash (40 hex): root deploys a pinned commit")
    if not smoke_as:
        raise SystemExit("deploy: name the organization user who runs the smoke check (--smoke-as); root never does")
    new, prev = f"{root}.new-{commit[:12]}", f"{root}.prev"
    if os.path.lexists(new):
        shutil.rmtree(new)  # root's own leftover from a deploy that stopped; rmtree removes a link, never follows it
    os.mkdir(new, 0o755)

    def git(*a):
        r = run([GIT, *GIT_SAFE, "-C", new, *a], capture_output=True, text=True, cwd="/", env=GIT_ENV)
        if r.returncode != 0:
            fail(f"git {' '.join(a[:2])}: {(r.stderr or r.stdout).strip()[:300]}")
        return r.stdout.strip()

    def fail(why):
        shutil.rmtree(new, ignore_errors=True)  # no git in a tree that failed: it is removed
        raise SystemExit(f"deploy: {why} on {commit[:12]}; {root} is unchanged")
    git("init", "-q")
    git("fetch", "-q", source, "main")
    public = git("rev-parse", "FETCH_HEAD")
    if run([GIT, *GIT_SAFE, "-C", new, "merge-base", "--is-ancestor", commit, public], capture_output=True, cwd="/",
           env=GIT_ENV).returncode:
        fail(f"{commit[:12]} is not on the public main ({public[:12]}): only reviewed code is deployed")
    git("checkout", "-q", "--detach", commit)
    if git("rev-parse", "HEAD") != commit:
        fail("the checkout is not the pinned commit")
    run([CHOWN, "-R", "-P", "root:wheel", new], capture_output=True, cwd="/", env=ROOT_ENV)
    run([CHMOD, "-R", "u+rwX,go+rX,go-w", new], capture_output=True, cwd="/", env=ROOT_ENV)
    problems = (trust or (lambda: host.trust_problems([new], [*org_users, host.SERVICE_USER], run=run)))()
    if problems:
        fail("the trust check failed (" + "; ".join(problems)[:300] + ")")
    # the user runs the smoke check, never root; git in root's tree needs it named safe for that user
    r = as_user(smoke_as, [PYTHON, os.path.join(new, "bin", "of"), "smoke", "--repo", new], run,
                env={"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": new})
    if r.returncode != 0:
        fail("the smoke check failed (" + (r.stderr or r.stdout).strip()[-300:] + ")")
    # the users cannot write a read-only checkout's bytecode cache: compile it here, after the trust check, isolated
    run([PYTHON, "-I", "-m", "compileall", "-q", os.path.join(new, "bin")], capture_output=True, cwd="/",
        env=ROOT_ENV)
    run([CHOWN, "-R", "-P", "root:wheel", os.path.join(new, "bin")], capture_output=True, cwd="/", env=ROOT_ENV)
    if os.path.lexists(prev):
        shutil.rmtree(prev)
    had = os.path.lexists(root)
    if had:
        os.rename(root, prev)
    os.rename(new, root)
    final = (final_check or (lambda: host.check_code(list(org_users), run=run,
                                                     here=os.path.join(root, "deploy", "host", "plane.py"))))()
    if final:
        failed = f"{root}.failed-{commit[:12]}"
        os.rename(root, failed)
        if had:
            os.rename(prev, root)
        shutil.rmtree(failed, ignore_errors=True)
        raise SystemExit("deploy: setup.check_code failed on the new tree (" + "; ".join(final)[:300] + "); "
                         f"{'the tree before is back' if had else 'nothing is in place'}")
    log(f"deploy: {root} at {commit[:12]}, on the public main {public[:12]} (trust, smoke and code checks ok; the "
        f"tree before is {prev if had else 'none'})")
    return commit


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
            r = run([LAUNCHCTL, "print", f"system/{label(svc, user)}"], capture_output=True, text=True)
            ok &= r.returncode == 0
            log(f"{user}: {label(svc, user)}: {'loaded' if r.returncode == 0 else 'NOT loaded'}")
        r = as_user(user, [HERDR, "status", "server"], run)
        ok &= r.returncode == 0
        log(f"{user}: herdr server: {'answers' if r.returncode == 0 else 'NOT answering'}")
        r = as_user(user, [SH, "-c", "grep -h '^  - name:' \"$HOME/.config/of/orgs.yaml\""], run)
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
    ap.add_argument("--commit", help="deploy: the full hash of a commit on the public main")
    ap.add_argument("--smoke-as", help="deploy: the organization user who runs the smoke check")
    a = ap.parse_args(argv)
    if a.action == "deploy":
        if os.geteuid() != 0:
            raise SystemExit("deploy: run it as root (sudo)")
        deploy(a.commit, a.smoke_as, list(parse_org_users(a.org_user)))
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
