#!/usr/bin/env python3
"""Set up the owner's admin login, organization users, credential keepers and channels on Linux.

Run from a root-owned checkout in the owner's terminal. Inspect the steps with plan
before applying them. The owner's admin login may be where agents once worked.
Without -I, a Python module planted beside this script could run as root.

  sudo python3 -I /opt/organic-framework/deploy/host/contours.py apply --admin owner --org-user of --org-user acme --keeper acme-vault --channel acme --no-tailscale-operator

passwd prompts directly when the admin needs a password. --admin-key-from selects
a public key by identity from SUDO_USER's authorized_keys. Agent-marked keys are
excluded. Keepers get no sudoers entry. --no-tailscale-operator inspects local prefs.

See step 2 of docs/contours.md section 4. deploy/host/plane.py creates a root-owned
checkout pinned to a reviewed commit.
"""

import argparse
import base64
import binascii
import fcntl
import grp
import json
import os
import pwd
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import sysconfig
from contextlib import contextmanager
from typing import NamedTuple


PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
NOLOGIN = "/usr/sbin/nologin"
NAME = re.compile(r"[a-z_][a-z0-9_-]{0,31}")
SUDO_ALL = re.compile(r"^[ \t]*\(ALL(?:[ \t]*:[ \t]*ALL)?\)[ \t]+(?:(?:NO)?(?:PASSWD|SETENV|EXEC):[ \t]*)*ALL[ \t]*$", re.MULTILINE)


class Refusal(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Refusal(message)


def trust_problems(paths, lstat=os.lstat):
    """Check each resolved absolute path and its parents through /."""
    problems = []
    seen = set()
    for path in paths:
        while path not in seen:
            seen.add(path)
            try:
                s = lstat(path)
            except OSError as e:
                problems.append(f"{path}: {e.strerror or e}")
            else:
                if s.st_uid != 0:
                    problems.append(f"{path} is owned by uid {s.st_uid}, expected root")
                if s.st_mode & 0o022:
                    problems.append(f"{path} is writable by group or others")
            path = os.path.dirname(path)
    return problems


def trusted_chain():
    """Report problems with the paths used to run this script."""
    paths = sysconfig.get_paths()
    roots = (__file__, sys.executable, paths["stdlib"], paths["platstdlib"])
    return trust_problems([os.path.realpath(p) for p in roots])


def arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "apply"))
    parser.add_argument("--admin")
    parser.add_argument("--admin-key-from", action="append", default=[], metavar="FILE")
    for option in ("org-user", "keeper", "channel"):
        parser.add_argument("--" + option, action="append", default=[])
    parser.add_argument("--no-tailscale-operator", action="store_true")
    a = parser.parse_args(argv)
    for attr in ("org_user", "keeper", "channel", "admin_key_from"):
        setattr(a, attr, list(dict.fromkeys(getattr(a, attr))))
    orgs = set(a.org_user) | ({"of", *a.channel} if a.channel else set())
    for n in [*orgs, *a.keeper] + ([a.admin] if a.admin is not None else []):
        require(NAME.fullmatch(n) and n != "root", f"invalid account name {n!r}")
    require(a.admin not in orgs | set(a.keeper), "admin has conflicting roles")
    require(not (set(a.keeper) & orgs), "keeper has conflicting roles")
    require("of" not in a.channel, "a channel needs two distinct users")
    require(all(NAME.fullmatch("ch-of-" + n) for n in a.channel), "channel group name too long")
    require(a.admin or not a.admin_key_from, "--admin-key-from needs --admin")
    return a


class User(NamedTuple):
    uid: int
    group: str
    home: str
    shell: str


class Directory(NamedTuple):
    owner: str
    group: str
    mode: int


def components(path):
    parts = path[1:].split("/")
    require(path.startswith("/") and all(p not in ("", ".", "..") for p in parts),
            f"expected an absolute, normalized path: {path!r}")
    return parts


def directory(path):
    """Pathname commands are safe only when root alone can replace each component."""
    here = "/"
    for part in components(path):
        here = os.path.join(here, part)
        try:
            s = os.lstat(here)
        except FileNotFoundError:
            return None
        require(stat.S_ISDIR(s.st_mode), f"{here} is a link or is not a directory")
        if here != path:
            require(s.st_uid == 0 and not s.st_mode & 0o022, f"unsafe parent {here}")
    return Directory(pwd.getpwuid(s.st_uid).pw_name, grp.getgrgid(s.st_gid).gr_name,
                     stat.S_IMODE(s.st_mode))


def run(argv, **kw):
    env = {**os.environ, "PATH": PATH, "LC_ALL": "C"}
    if argv[0] == "passwd" and len(argv) == 2:
        with open("/dev/tty", "rb") as tty:
            return subprocess.run(argv, check=True, env=env, stdin=tty)
    return subprocess.run(argv, check=True, env=env, **kw)


def password(name, invoke=run):
    fields = invoke(["passwd", "-S", name], capture_output=True, text=True).stdout.split()
    require(len(fields) >= 2 and fields[0] == name and fields[1] in ("P", "L", "NP"),
            f"unknown passwd -S status for {name}")
    return fields[1]


class Host:
    def __init__(self):
        self.groups = {g.gr_name: set(g.gr_mem) for g in grp.getgrall()}
        self.users = {u.pw_name: User(u.pw_uid, grp.getgrgid(u.pw_gid).gr_name, u.pw_dir, u.pw_shell)
                      for u in pwd.getpwall()}
        self.tailscale = shutil.which("tailscale", path=PATH)
        limits = {}
        with open("/etc/login.defs") as f:
            for line in f:
                fields = line.split()
                if fields and fields[0] in ("SYS_UID_MAX", "UID_MIN"):
                    limits[fields[0]] = int(fields[1])
        self.system_max = limits.get("SYS_UID_MAX", limits.get("UID_MIN", 1000) - 1)

    directory = staticmethod(directory)

    @staticmethod
    def password(name):
        return password(name) if os.geteuid() == 0 else None


def plan(a, state):
    """Return (description, argv) pairs. Project earlier steps without altering the input state."""
    users = state.users.copy()
    groups = {g: set(members) for g, members in state.groups.items()}
    paths = {}
    steps = []

    def step(text, *argv):
        steps.append((text, list(argv)))

    def info(path):
        return paths[path] if path in paths else state.directory(path)

    def folder(path, owner, group, mode):
        desired = Directory(owner, group, mode)
        if info(path) == desired:
            step(f"already {path} {mode:04o} {owner}:{group}")
        else:
            step(f"set up {path}", "install", "-d", "-o", owner, "-g", group, "-m", f"{mode:04o}", path)
        paths[path] = desired

    def account(n, keeper=False):
        if n in users:
            require(users[n].uid != 0, f"{n} has uid zero")
            step(f"already have user {n}")
        else:
            home = ("/var/lib/of-keepers/" if keeper else "/home/") + n
            require(info(home) is None, f"{home} already exists for missing user {n}")
            require(n not in groups, f"group {n} already exists for missing user")
            flags = ["-r", "-M", "-s", NOLOGIN] if keeper else ["-m"]
            step(f"create user {n}", "useradd", *flags, "-U", "-d", home, n)
            users[n] = User(-1, n, home, NOLOGIN if keeper else "")
            groups[n] = set()
        return users[n]

    if a.admin:
        admin = account(a.admin)
        require("wheel" in groups, "missing wheel group")
        if admin.group == "wheel" or a.admin in groups["wheel"]:
            step(f"already in wheel: {a.admin}")
        else:
            step(f"add {a.admin} to wheel", "usermod", "-aG", "wheel", a.admin)
            groups["wheel"].add(a.admin)
        status = state.password(a.admin) if a.admin in state.users else "L"
        if status == "P":
            step(f"already have a password for {a.admin}")
        else:
            step(f"set {a.admin}'s password interactively if needed", "passwd", a.admin)
        if a.admin_key_from:
            step(f"copy selected owner keys to {a.admin} if missing", sys.executable, "-I",
                 os.path.abspath(__file__), "_keys", a.admin, *a.admin_key_from)

    for n in a.org_user:
        u = account(n)
        home = info(u.home)
        require(home is None or home.owner == n, f"{u.home} is not {n}'s home")
        folder(u.home, n, home.group if home else u.group, 0o700)

    for n in a.org_user:
        primary = users[n].group == "wheel"
        member = n in groups.get("wheel", ())
        if not primary and not member:
            step(f"already outside wheel: {n}")
            continue
        require(a.admin, f"removing {n} from wheel requires --admin with a password in wheel")
        if primary:
            require(n != "wheel", "cannot use wheel as replacement primary group")
            if n not in groups:
                step(f"create primary group for {n}", "groupadd", n)
                groups[n] = set()
            step(f"verify admin before changing {n}'s primary group", "sudo", "-l", "-U", a.admin)
            step(f"change {n}'s primary group", "usermod", "-g", n, n)
            users[n] = users[n]._replace(group=n)
        if member:
            step(f"verify admin before removing {n} from wheel", "sudo", "-l", "-U", a.admin)
            step(f"remove {n} from wheel", "gpasswd", "-d", n, "wheel")

    if a.keeper:
        for path in ("/var/lib/of-keepers", "/opt/of-keepers"):
            folder(path, "root", "root", 0o755)
    for n in a.keeper:
        u = account(n, keeper=True)
        home = "/var/lib/of-keepers/" + n
        require(u.uid <= state.system_max, f"{n} exists with a non-system uid")
        require(u.group != "wheel" and n not in groups.get("wheel", ()), f"keeper {n} is in wheel")
        if u.shell != NOLOGIN or u.home != home:
            step(f"set keeper login for {n}", "usermod", "-s", NOLOGIN, "-d", home, n)
        # Without -p, useradd creates a locked account.
        if n in state.users and state.password(n) != "L":
            step(f"lock password for {n} if needed", "usermod", "-L", n)
        else:
            step(f"already locked upon creation or inspection: {n}")
        folder(home, n, u.group, 0o700)
        folder("/opt/of-keepers/" + n, "root", "root", 0o755)

    if a.channel:
        folder("/srv/of-channel", "root", "root", 0o755)
    for n in a.channel:
        g = "ch-of-" + n
        pair = {"of", n}
        require(all(who in users and users[who].uid != 0 for who in pair), f"channel needs users of and {n}")
        if g not in groups:
            step(f"create channel group {g}", "groupadd", g)
            groups[g] = set()
        primary = {who for who, u in users.items() if u.group == g}
        require(primary <= pair, f"another user has primary group {g}")
        if groups[g] | primary != pair:
            step(f"set members of {g}", "gpasswd", "-M", "of," + n, g)
        else:
            step(f"already have channel members of and {n}")
        parent = "/srv/of-channel/" + n
        folder(parent, "root", "root", 0o755)
        folder(parent + "/to-of", n, g, 0o2750)
        folder(parent + "/to-" + n, "of", g, 0o2750)
    if a.no_tailscale_operator:
        if state.tailscale:
            step("check Tailscale OperatorUser", "tailscale", "debug", "prefs")
        else:
            step("already clear: tailscale is absent")
    return steps


# Read SSH paths one component at a time, including below user-writable homes.
DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@contextmanager
def key_folder(path, uid):
    fd = os.open("/", DIR)
    try:
        for part in components(path) if path != "/" else ():
            child = os.open(part, DIR, dir_fd=fd)
            os.close(fd)
            fd = child
            s = os.fstat(fd)
            sticky = s.st_uid == 0 and s.st_mode & stat.S_ISVTX
            require(s.st_uid in (0, uid) and (sticky or not s.st_mode & 0o022), f"unsafe SSH path {path!r}")
        yield fd
    finally:
        os.close(fd)


def read_keys(fd, name, uid):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(name, flags, dir_fd=fd), "rb") as f:
        s = os.fstat(f.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and s.st_uid in (0, uid)
                and not s.st_mode & 0o022, "unsafe SSH key file")
        data = f.read(8 * 1024 * 1024 + 1)
        require(len(data) <= 8 * 1024 * 1024, "SSH key file exceeds 8 MiB")
        return data


# An authorized_keys options field can contain quoted spaces. Match it as one field
# so a key mentioned inside a command or a trailing comment cannot select the line.
KEY = re.compile(rb'(?:(?P<options>(?:[^\s"\\]|\\(?!")|\\"|"(?:[^"\\]|\\(?!")|\\")*")+)[ \t]+)?'
                 rb'(?P<type>(?:ssh-|sk-|ecdsa-)[\w@.+-]+)[ \t]+(?P<blob>[A-Za-z0-9+/]+=*)'
                 rb'(?:[ \t]+(?P<comment>.*))?')
AGENT = re.compile(rb'(?<![a-z0-9])agents?(?![a-z0-9])', re.I)


def key_line(line):
    line = line.rstrip(b"\r").strip(b" \t")
    if line.startswith(b"#") or len(line) > 128 * 1024 or re.search(rb"[\x00-\x08\x0a-\x1f]", line):
        return None
    m = KEY.fullmatch(line)
    if not m:
        return None
    try:
        blob = base64.b64decode(m["blob"], validate=True)
    except binascii.Error:
        return None
    kind = m["type"]
    if not blob.startswith(len(kind).to_bytes(4, "big") + kind) or len(blob) <= 4 + len(kind):
        return None
    return blob, m["options"], bool(AGENT.search(m["comment"] or b""))


def selected_lines(public_files, authorized):
    pins = set()
    for data in public_files:
        key = key_line(data.strip())
        require(key and not key[1] and not key[2], "expected one owner's public key per --admin-key-from")
        pins.add(key[0])
    lines, found = [], set()
    for line in authorized.split(b"\n"):
        key = key_line(line)
        if key and key[0] in pins:
            require(not key[2], "selected public key is also marked as an agent key")
            # An agent-controlled source must not choose code to execute as the admin,
            # or turn the selected device key into a certificate authority.
            options = (key[1] or b"").lower()
            require(not re.search(rb"(?:^|,)(?:command=|environment=|cert-authority(?:,|$))", options),
                    "selected key has unsafe admin login options")
            lines.append(line)
            found.add(key[0])
    require(found == pins, "selected owner key missing from sudo user's authorized_keys")
    return list(dict.fromkeys(lines))


def import_keys(admin, filenames):
    require(os.geteuid() == 0 and NAME.fullmatch(admin), "key import requires root and a valid admin")
    caller = os.environ.get("SUDO_USER", "")
    require(NAME.fullmatch(caller), "--admin-key-from requires an invoking sudo user")
    src, dst = pwd.getpwnam(caller), pwd.getpwnam(admin)
    require(dst.pw_uid != 0, "admin has uid zero")
    public = []
    for filename in filenames:
        parent, name = os.path.split(os.path.abspath(filename))
        with key_folder(parent, src.pw_uid) as fd:
            public.append(read_keys(fd, name, src.pw_uid))
    with key_folder(src.pw_dir + "/.ssh", src.pw_uid) as fd:
        lines = selected_lines(public, read_keys(fd, "authorized_keys", src.pw_uid))

    # This helper is a separate process. Give up root before creating anything
    # in a directory the admin can move. Replace the final file atomically.
    os.setgroups([])
    os.setgid(dst.pw_gid)
    os.setuid(dst.pw_uid)
    with key_folder(dst.pw_dir, dst.pw_uid) as home:
        try:
            os.mkdir(".ssh", 0o700, dir_fd=home)
        except FileExistsError:
            pass
        fd = os.open(".ssh", DIR, dir_fd=home)
    try:
        s = os.fstat(fd)
        require(s.st_uid == dst.pw_uid, "admin does not own .ssh")
        if stat.S_IMODE(s.st_mode) != 0o700:
            os.fchmod(fd, 0o700)
        try:
            existing = read_keys(fd, "authorized_keys", dst.pw_uid)
        except FileNotFoundError:
            existing = b""
        present = set(existing.split(b"\n"))
        missing = [line for line in lines if line not in present]
        if not missing:
            print("already have the selected owner keys")
            return
        tmp = ".authorized_keys." + secrets.token_hex(16)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        new = os.open(tmp, flags, 0o600, dir_fd=fd)
        try:
            with os.fdopen(new, "wb") as f:
                f.write(existing)
                if existing and not existing.endswith(b"\n"):
                    f.write(b"\n")
                f.write(b"\n".join(missing) + b"\n")
            os.replace(tmp, "authorized_keys", src_dir_fd=fd, dst_dir_fd=fd)
        finally:
            try:
                os.unlink(tmp, dir_fd=fd)
            except FileNotFoundError:
                pass
    finally:
        os.close(fd)


def in_wheel(name):
    try:
        user, wheel = pwd.getpwnam(name), grp.getgrnam("wheel")
    except KeyError:
        return False
    return user.pw_gid == wheel.gr_gid or name in wheel.gr_mem


def check_paths(argv):
    if argv[0] == "install":
        directory(argv[-1])
    elif argv[0] == "useradd":
        home = argv[argv.index("-d") + 1]
        require(directory(home) is None, f"home appeared before useradd: {home}")


def execute(a, steps, invoke=run, member=in_wheel, guard=check_paths, out=print):
    completed = []
    for label, argv in steps:
        out(f"{label}  {shlex.join(argv)}" if argv else label)
    for label, argv in steps:
        if not argv:
            continue
        cmd = argv[0]
        if a.action == "plan" and cmd != "tailscale":
            continue
        try:
            if cmd == "tailscale":
                data = json.loads(invoke(argv, capture_output=True, text=True).stdout)
                require(isinstance(data, dict) and isinstance(data.get("OperatorUser", ""), str),
                        "unrecognized tailscale preferences")
                orgs = set(a.org_user) | ({"of", *a.channel} if a.channel else set())
                operator = data.get("OperatorUser", "")
                require(operator not in orgs, f"organization user {operator} is the Tailscale operator")
                out("already clear of organization operators")
            elif argv[:3] == ["sudo", "-l", "-U"]:
                name = argv[-1]
                require(member(name) and password(name, invoke) == "P",
                        f"admin {name} must exist, belong to wheel and have password status P")
                listing = invoke(argv, capture_output=True, text=True).stdout
                # A rule that runs as ALL but lists only some commands does not make root (of_server, PR 353).
                require(SUDO_ALL.search(listing), f"sudo -l lists no rule that runs ALL commands as ALL for admin {name}")
            else:
                guard(argv)
                invoke(argv)
            completed.append(label)
        except (OSError, KeyError, ValueError, subprocess.CalledProcessError) as e:
            raise Refusal(f"step '{label}' failed: {e}. Completed: {', '.join(completed) or 'none'}") from None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        if argv[:1] == ["_keys"] and len(argv) >= 3:
            # The helper runs as root too: the same isolated, root-owned chain as apply.
            problems = trusted_chain() + ([] if sys.flags.isolated else ["the key helper requires isolated Python (python3 -I)"])
            require(not problems, "\n".join(problems))
            import_keys(argv[1], argv[2:])
        else:
            a = arguments(argv)
            problems = trusted_chain()
            if not sys.flags.isolated:
                problems.append("apply requires isolated Python (use python3 -I)")
            if a.action == "apply":
                if os.geteuid() != 0:
                    problems.append("apply requires root (use sudo)")
                require(not problems, "\n".join(problems))
            elif problems:
                print("warning: " + "\n".join(problems), file=sys.stderr)
            # Serialize owner sittings, including their snapshots, without leaving a lock file.
            fd = os.open("/etc", DIR)
            try:
                if a.action == "apply":
                    fcntl.flock(fd, fcntl.LOCK_EX)
                execute(a, plan(a, Host()))
            finally:
                os.close(fd)
    except (OSError, KeyError, ValueError, subprocess.CalledProcessError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
