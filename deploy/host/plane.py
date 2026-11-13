#!/usr/bin/env python3
"""The agent host's OF plane, one per organization user (organic-framework-build reports/
agent-server-migration-plan-2026-11-11.md §3 B6, B2 and §9; of_agent-server's hand-over 5de421ee; owned by of_platform).
of_server's deploy/host/setup.py sets up the server side (the service user, of-server, ClickHouse, a relay per
organization user, the root watchdog); this script sets up what each organization user runs itself.

- `deploy --from SRC --ref REF` (root): the read-only release checkout /opt/organic-framework at that commit, owned by
  root, readable by all; the smoke check (bin/of smoke) runs on it before it counts, and a failing commit is rolled
  back to the one before. A running plane picks it up on `start` (its daemons restart).
- `apply` (root): for each organization user, as that user:
  - ~/.config/of/orgs.yaml naming only its own organizations (its repositories in its own home);
  - ~/.local/bin/of, a wrapper that runs the release checkout's bin/of on Homebrew's Python;
  - in ~/.zshrc, one marked block: that PATH and the shell function (bin/of-shell.zsh, which resumes a session as
    it ran);
  - PyYAML for that Python (pip --user) when it is missing;
  - the recorder hooks, the guard and the secrets deny rules in each of its Claude config dirs (`of hooks install
    --guard --deny-secrets`);
  - LaunchDaemons with UserName, at ProcessType Standard, written and not loaded: its own herdr server (so no
    organization can type into another's panes), the listener and its pulse, and `of health run` every minute; and one
    root watchdog per user that restarts a stuck listener (`of listen watchdog` with that user's state).
- `start` / `stop` (root): load or unload the plane's daemons.
- `check`: as each user, `of registry check` passes, the guard answers deny in each config dir, the daemons are
  loaded, herdr's server answers, and orgs.yaml names no other user's organization.
- `plan` (the default) prints what `apply` would do and changes nothing.

  sudo python3 deploy/host/plane.py plan  --org-user of:organic-framework-build=<of's home>/organic-framework-build
  sudo python3 deploy/host/plane.py apply --org-user of:organic-framework-build=<of's home>/organic-framework-build \\
                                          --org-user acme:acme=<acme's home>/acme-org --claude-dir acme:.claude-a
  sudo python3 deploy/host/plane.py deploy --from <of's home>/organic-framework --ref <commit>
  sudo python3 deploy/host/plane.py start|stop|check --org-user ...

The owner's remote view (B4: saved herdr machines `of@localhost` and `acme@localhost` under the admin user, for the
phone) is in docs/remote-access.md, "A separate agent host".
"""

import argparse
import os
import subprocess
import sys

OF_ROOT = "/opt/organic-framework"
PYTHON = "/opt/homebrew/bin/python3"
HERDR = "/opt/homebrew/bin/herdr"
PATH = "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
DAEMONS = "/Library/LaunchDaemons"
WATCH_STATE = "/var/of-plane"  # the root watchdogs' own state, outside every user's home
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
        if not (user and org and repo and os.path.isabs(repo)):
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
    import pwd
    return pwd.getpwnam(user).pw_dir


# ------------------------------------------------------------------ the files

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
    """{label: plist} of one organization user's plane. Each runs as that user, except the watchdog (root, so it can
    restart the user's system daemon), which reads that user's state and keeps its own outside the home."""
    state = os.path.join(user_home, ".local", "state", "of")
    of = [PYTHON, f"{OF_ROOT}/bin/of"]
    return {
        label("herdr", user): plist(label("herdr", user), [HERDR, "server"], user, user_home, keep=True,
                                    log=os.path.join(state, "herdr-server.log")),
        label("listen", user): plist(label("listen", user), of + ["listen", "run"], user, user_home, keep=True,
                                     log=os.path.join(state, "listen.log")),
        label("health", user): plist(label("health", user), of + ["health", "run"], user, user_home, interval=60,
                                     log=os.path.join(state, "health.log")),
        label("listen-watchdog", user): plist(  # of_listen alone, which needs no PyYAML: root has none of its own
            label("listen-watchdog", user),
            [PYTHON, "-c", f"import sys; sys.path.insert(0, '{OF_ROOT}/bin'); import of_listen; "
                           "print(of_listen.watchdog())"], None, None, interval=60,
            env={"OF_STATE": state, "OF_LISTEN_TARGET": f"system/{label('listen', user)}",
                 "OF_LISTEN_WATCHDOG_STATE": os.path.join(WATCH_STATE, f"{user}-listen-watchdog.json")},
            log=os.path.join(WATCH_STATE, f"{user}-listen-watchdog.log")),
    }


# ------------------------------------------------------------------ the steps

def steps(org_users, claude_dirs, homes=None):
    """[(user, description, action)] of `apply`, in order. action: ("write", path, text, mode) | ("run", argv)."""
    homes = homes or {u: home(u) for u in org_users}
    out = []
    for user, orgs in org_users.items():
        h = homes[user]
        out.append((user, "orgs.yaml: its own organizations only",
                    ("write", os.path.join(h, ".config", "of", "orgs.yaml"), orgs_yaml(orgs, h), 0o600)))
        out.append((user, "the of wrapper", ("write", os.path.join(h, ".local", "bin", "of"), wrapper(), 0o755)))
        out.append((user, "~/.zshrc: PATH and the shell function", ("zshrc", os.path.join(h, ".zshrc"))))
        out.append((user, "PyYAML for Homebrew's Python, if it is missing",
                    ("run", ["sh", "-c", f"{PYTHON} -c 'import yaml' 2>/dev/null || "
                                         f"{PYTHON} -m pip install --user --break-system-packages pyyaml"])))
        for d in claude_dirs[user]:
            cfg = d if os.path.isabs(d) else os.path.join(h, d)
            out.append((user, f"hooks, guard and deny rules in {cfg}",
                        ("run", [PYTHON, f"{OF_ROOT}/bin/of", "hooks", "install", "--guard", "--deny-secrets",
                                 "--config-dir", cfg, "--yes"])))
        for lbl, text in daemons(user, h).items():
            out.append((None, f"LaunchDaemon {lbl} (written, not loaded)",
                        ("write", os.path.join(DAEMONS, f"{lbl}.plist"), text, 0o644)))
    return out


def as_user(user, argv, run=subprocess.run):
    return run(["sudo", "-u", user, "-H", "env", f"PATH={PATH}", "OF_AUTOMATION=1", "SSH_AUTH_SOCK=", *argv],
               capture_output=True, text=True)


def apply(org_users, claude_dirs, run=subprocess.run, log=print, homes=None, chown=None):
    import pwd
    for user, what, act in steps(org_users, claude_dirs, homes):
        if act[0] == "write":
            _, path, text, mode = act
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".tmp", "w") as fh:
                fh.write(text)
            os.chmod(path + ".tmp", mode)
            if user:
                pw = pwd.getpwnam(user)
                (chown or os.chown)(path + ".tmp", pw.pw_uid, pw.pw_gid)
                (chown or os.chown)(os.path.dirname(path), pw.pw_uid, pw.pw_gid)
            os.replace(path + ".tmp", path)
        elif act[0] == "zshrc":
            path = act[1]
            try:
                with open(path) as fh:
                    text = fh.read()
            except FileNotFoundError:
                text = ""
            new = zshrc(text)
            if new != text:
                with open(path, "w") as fh:
                    fh.write(new)
                pw = pwd.getpwnam(user)
                (chown or os.chown)(path, pw.pw_uid, pw.pw_gid)
        else:
            r = as_user(user, act[1], run)
            if r.returncode != 0:
                raise SystemExit(f"{user}: {what}: {(r.stderr or r.stdout).strip()[-400:]}")
        log(f"{user or 'root'}: {what}")
    os.makedirs(WATCH_STATE, mode=0o700, exist_ok=True)


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


def deploy(src, ref, run=subprocess.run, log=print, root=OF_ROOT):
    """The release checkout at `ref`, root-owned and read-only for everyone else; the smoke check first, and the
    commit before it back when the check fails. Returns the commit now checked out."""
    def git(*a, check=True):
        r = run(["git", "-C", root, *a], capture_output=True, text=True)
        if check and r.returncode != 0:
            raise SystemExit(f"deploy: git {' '.join(a[:2])}: {(r.stderr or r.stdout).strip()[:300]}")
        return r.stdout.strip()
    if not os.path.isdir(os.path.join(root, ".git")):
        r = run(["git", "clone", "--no-checkout", src, root], capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"deploy: git clone: {(r.stderr or r.stdout).strip()[:300]}")
        before = None
    else:
        before = git("rev-parse", "HEAD", check=False) or None
    git("fetch", "-q", src, ref)
    git("checkout", "-q", "--detach", "FETCH_HEAD")
    now = git("rev-parse", "HEAD")
    r = run([PYTHON, os.path.join(root, "bin", "of"), "smoke", "--repo", root], capture_output=True, text=True)
    if r.returncode != 0:
        if before:
            git("checkout", "-q", "--detach", before)
        raise SystemExit(f"deploy: the smoke check failed on {now[:12]}; "
                         f"{'back on ' + before[:12] if before else 'nothing was checked out before'}: "
                         f"{(r.stderr or r.stdout).strip()[-300:]}")
    # the users cannot write a read-only checkout's bytecode cache: compile it here, or every hook and command compiles
    # its modules again on each run (bin/of-hook's docstring)
    run([PYTHON, "-m", "compileall", "-q", os.path.join(root, "bin")], capture_output=True)
    run(["chown", "-R", "root:wheel", root], capture_output=True)
    run(["chmod", "-R", "u+rwX,go+rX,go-w", root], capture_output=True)
    log(f"deploy: {OF_ROOT} at {now[:12]} (smoke check ok)")
    return now


def check(org_users, claude_dirs, run=subprocess.run, log=print, homes=None):
    """True when every user's plane is whole; one line per finding."""
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
        try:
            with open(os.path.join(h, ".config", "of", "orgs.yaml")) as fh:
                listed = {ln.split(":", 1)[1].strip() for ln in fh if ln.strip().startswith("- name:")}
        except OSError:
            listed = set()
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
    ap.add_argument("--ref", help="deploy: the commit")
    a = ap.parse_args(argv)
    if a.action == "deploy":
        if not (a.src and a.ref):
            raise SystemExit("deploy needs --from <repository> and --ref <commit>")
        deploy(a.src, a.ref)
        return 0
    org_users = parse_org_users(a.org_user)
    if not org_users:
        raise SystemExit("name each organization user: --org-user user:org=/absolute/repo")
    claude = parse_claude_dirs(a.claude_dir, org_users)
    if a.action == "plan":
        for user, what, _ in steps(org_users, claude):
            print(f"{user or 'root'}: {what}")
        return 0
    if a.action == "check":
        return 0 if check(org_users, claude) else 1
    if os.geteuid() != 0:
        raise SystemExit(f"{a.action}: run it as root (sudo)")
    if a.action == "apply":
        apply(org_users, claude)
    else:
        load(org_users, unload=a.action == "stop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
