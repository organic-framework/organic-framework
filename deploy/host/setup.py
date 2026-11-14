#!/usr/bin/env python3
"""Set up the agent host's server side on a clean macOS (organic-framework-build reports/
agent-server-migration-plan-2026-11-11.md §3 B2 and §9; of_agent-server's hand-over aba3d171; owned by of_server).

On the host each organization's agents run as their own OS user (`of`, `acme`, …). A hidden service user, `_ofserver`,
holds of-server, ClickHouse, their passwords and the tenant master key, none of which an agent user can read. This
script, run as root:

- refuses to go on when root or `_ofserver` would run code that an organization user or `_ofserver` can change: the
  python, everything under its install prefix (Homebrew's whole /opt/homebrew, whose libraries it loads), ClickHouse
  and the release checkout this script runs from (`trust_problems`);
- creates `_ofserver` (hidden, no login shell, its home /var/of-server mode 700) and checks each organization user
  exists, making its home 700;
- writes /etc/of: of-server's config (root-owned, readable by `_ofserver` only), ClickHouse's config and users, and the
  grant key's public half, pinned (root-owned: the host verifies grants and never signs them, of_architect, msg
  01a1d66a-cd41);
- makes ClickHouse's passwords (mode 600, `_ofserver`), never printing one: an admin user in its users.xml, and one
  per user deploy/clickhouse/schema.py creates (`<db>_ingest`, `_redact`, `_read`), which `schema` loads at S3;
- writes the LaunchDaemons, each with UserName, and does NOT load them: of-server, ClickHouse, a relay per
  organization user, and one root watchdog that restarts a hung server or relay (`of_server.py host-watchdog`). Their
  logs are in root's /var/log/of and the watchdog's state in root's /var/db/of-host-watchdog, never in a folder
  another user owns, since launchd may open a log as root.

Root never follows a link where another user could have put one (`Files`): `_ofserver` owns /var/of-server and an
organization user its home (of_secrets-review and of_review on organic-framework PR 144).

`plan` (the default) checks the code and prints what it would do, changing nothing; `apply` does it; `start` and
`stop` load and unload the daemons; `schema` loads the databases, tables and users into the running ClickHouse (S3 of
the plan). The tenant master key reaches /var/of-server/tenant.key by its own committed copy over SSH (hand-over item
3), never through this script. Run it with Apple's python from the release checkout, which the check covers:

  sudo /usr/bin/python3 /opt/organic-framework/deploy/host/setup.py plan  --org-user of --org-user acme --grant-pub F
  sudo /usr/bin/python3 /opt/organic-framework/deploy/host/setup.py apply --org-user of --org-user acme --grant-pub F
  sudo /usr/bin/python3 /opt/organic-framework/deploy/host/setup.py start|stop|schema --org-user of --org-user acme
"""

import argparse
import errno
import grp
import os
import pwd
import re
import secrets
import stat
import subprocess
import sys

SERVICE_USER = "_ofserver"
SERVICE_HOME = "/var/of-server"
ETC = "/etc/of"
LOGS = "/var/log/of"                    # root's: every daemon's launchd log, made before launchd opens it
WATCHDOG_STATE = "/var/db/of-host-watchdog"
DAEMONS = "/Library/LaunchDaemons"
OF_ROOT = "/opt/organic-framework"     # the read-only release checkout (B6, of_platform's deploy step)
HOMEBREW = "/opt/homebrew"            # all of it is checked: its python loads its libraries by absolute path
PYTHON = "/opt/homebrew/bin/python3"
CLICKHOUSE = "/opt/homebrew/bin/clickhouse"
PATH = "/opt/homebrew/bin:/usr/bin:/bin"
SERVER_LABEL = "dev.organic-framework.server"
CLICKHOUSE_LABEL = "dev.organic-framework.clickhouse"
WATCHDOG_LABEL = "dev.organic-framework.host-watchdog"
RELAY_LABEL = "dev.organic-framework.relay.{user}"
CH_HTTP, CH_TCP = 18123, 19000
ADMIN = "of_host_admin"
VAULT, UNASSIGNED = "of_host_vault", "of_host_unassigned"
USER_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,31}")


def relay_label(user):
    return RELAY_LABEL.format(user=user)


# ------------------------------------------------------------------ files


def plist(label, argv, user=None, env=None, keep=True, interval=None, log=None, workdir=None):
    """A LaunchDaemon: UserName when it runs as a user, Standard priority, OF_AUTOMATION=1 and no SSH agent."""
    from xml.sax.saxutils import escape
    envs = {"OF_AUTOMATION": "1", "SSH_AUTH_SOCK": "", "PATH": PATH, **(env or {})}
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">',
           '<plist version="1.0"><dict>', f"  <key>Label</key><string>{escape(label)}</string>",
           "  <key>ProgramArguments</key><array>" + "".join(f"<string>{escape(a)}</string>" for a in argv) + "</array>",
           "  <key>EnvironmentVariables</key><dict>" + "".join(
               f"<key>{escape(k)}</key><string>{escape(v)}</string>" for k, v in envs.items()) + "</dict>",
           "  <key>ProcessType</key><string>Standard</string>"]
    if user:
        out += [f"  <key>UserName</key><string>{escape(user)}</string>"]
    if interval:
        out += [f"  <key>StartInterval</key><integer>{int(interval)}</integer>"]
    else:
        out += ["  <key>RunAtLoad</key><true/>",
                "  <key>KeepAlive</key>" + ("<true/>" if keep is True else
                                            "<dict><key>SuccessfulExit</key><false/></dict>"),
                "  <key>ThrottleInterval</key><integer>10</integer>"]
    if workdir:
        out += [f"  <key>WorkingDirectory</key><string>{escape(workdir)}</string>"]
    if log:
        out += [f"  <key>StandardOutPath</key><string>{escape(log)}</string>",
                f"  <key>StandardErrorPath</key><string>{escape(log)}</string>"]
    return "\n".join(out + ["</dict></plist>", ""])


def split_org(o):
    """(name, database) from `name` or `name=database`: the database as the machines' orgs.yaml names it, so the
    delta merge (B3) finds the same database here."""
    name, _, database = str(o).partition("=")
    return name, database or f"of_{db(name)}"


def org_dbs(orgs):
    return [split_org(o)[1] for o in orgs]


def ch_schema():
    """deploy/clickhouse/schema.py, beside this script."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "clickhouse", "schema.py")
    spec = importlib.util.spec_from_file_location("of_ch_schema", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def server_yaml(orgs, tenant="default"):
    """of-server's config: storage in the local ClickHouse, the master key with the service user, grants verified by
    the pinned public key only (no private key on the host), loopback only until the S3 tailnet bind."""
    lines = ["# of-server on the agent host (deploy/host/setup.py). Owned by root, readable by _ofserver only.",
             "host: 127.0.0.1", "port: 18200", f"state_dir: {SERVICE_HOME}/state",
             "plugins: {storage: clickhouse, buffer: disk, blobs: fs, identity: machine_keys}",
             f"tenant_key_file: {SERVICE_HOME}/tenant.key   # copied by its own script over SSH, never printed",
             "tenant_key_create: false", f"trusted_grant_keys_file: {ETC}/grant_ed25519.pub",
             "grant_legacy_hmac: false", "health_detail: true", f"tenant: {tenant}",
             f"vault_database: {VAULT}", f"unassigned_database: {UNASSIGNED}", "raw_to_org: false", "orgs:"]
    lines += [f"  {n}: {d}" for n, d in map(split_org, orgs)]
    # each database's own <db>_ingest password beside this one is used for it (of_route.ClickHouse.auth_for)
    lines += ["clickhouse:", f"  url: http://127.0.0.1:{CH_HTTP}", f"  user: {UNASSIGNED}_ingest",
              f"  password_file: {SERVICE_HOME}/secrets/{UNASSIGNED}_ingest.password", ""]
    return "\n".join(lines)


def db(org):
    return "".join(c if c.isalnum() else "_" for c in org)


def clickhouse_config():
    return "\n".join([
        "<clickhouse>",
        "  <!-- the agent host's ClickHouse (deploy/host/setup.py): loopback only, data with _ofserver -->",
        "  <listen_host>127.0.0.1</listen_host>",
        f"  <http_port>{CH_HTTP}</http_port>", f"  <tcp_port>{CH_TCP}</tcp_port>",
        f"  <path>{SERVICE_HOME}/clickhouse/</path>", f"  <tmp_path>{SERVICE_HOME}/clickhouse/tmp/</tmp_path>",
        f"  <user_files_path>{SERVICE_HOME}/clickhouse/user_files/</user_files_path>",
        f"  <users_config>{ETC}/clickhouse/users.xml</users_config>",
        "  <logger><level>warning</level>"
        f"<log>{SERVICE_HOME}/clickhouse/log/clickhouse.log</log>"
        f"<errorlog>{SERVICE_HOME}/clickhouse/log/clickhouse.err.log</errorlog></logger>",
        "</clickhouse>", ""])


def users_xml(admin_hash):
    """The admin user only, by its password's SHA-256, from loopback, with access management: it creates the others
    (deploy/clickhouse/schema.py, `setup.py schema`). No default user."""
    return ("<clickhouse>\n  <!-- deploy/host/setup.py: no default user; passwords live in "
            f"{SERVICE_HOME}/secrets, mode 600 -->\n  <users><{ADMIN}><password_sha256_hex>{admin_hash}"
            "</password_sha256_hex><networks><ip>127.0.0.1</ip></networks><profile>default</profile>"
            f"<quota>default</quota><access_management>1</access_management></{ADMIN}></users>\n"
            "  <profiles><default/></profiles><quotas><default/></quotas>\n</clickhouse>\n")


# ------------------------------------------------------------------ what root writes, never through a link

DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class Files:
    """Root's file work, safe where another user owns the folder (organic-framework PR 144, of_secrets-review and
    of_review): a folder is opened one component at a time with O_NOFOLLOW, a file is made with O_EXCL|O_NOFOLLOW
    after its old name is removed, and modes and owners are set through the descriptor. A link, a hard link, a FIFO
    or a non-folder where a folder belongs is refused. Only a path's first component is resolved: the system's own
    links (/var, /etc → /private/…) sit in /, which root alone writes. ROOT is a test tree instead of / (nothing is
    chowned there, and the running user stands for every owner)."""

    def __init__(self, root=""):
        self.root = root.rstrip("/") or ""

    def p(self, path):
        return self.root + path

    def uid(self, name):
        return os.geteuid() if self.root else pwd.getpwnam(name).pw_uid

    def own(self, fd, owner, group):
        if not self.root:
            os.fchown(fd, pwd.getpwnam(owner).pw_uid, grp.getgrnam(group).gr_gid)

    def owned_by(self, fd, path, owners):
        uids = {0, os.geteuid()} | {self.uid(o) for o in owners}
        if os.fstat(fd).st_uid not in uids:
            raise SystemExit(f"refused: {path} belongs to uid {os.fstat(fd).st_uid}, not to {' or '.join(owners)}")

    def folder(self, path, create=False):
        """A descriptor of folder PATH; with CREATE, missing folders are made (700; the caller sets mode and owner)."""
        parts = [p for p in path.split("/") if p]
        first = os.path.join(self.root or "/", parts[0])
        if create and self.root:
            os.makedirs(first, exist_ok=True)
        fd = os.open(os.path.realpath(first), os.O_RDONLY | os.O_DIRECTORY)
        for part in parts[1:]:
            try:
                try:
                    nfd = os.open(part, DIR, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    os.mkdir(part, 0o700, dir_fd=fd)
                    nfd = os.open(part, DIR, dir_fd=fd)
            except FileNotFoundError:
                os.close(fd)
                raise
            except OSError as e:
                os.close(fd)
                raise SystemExit(f"refused: {path}: {part} is a link or no folder ({e.strerror}); root follows no "
                                 "link here") from None
            os.close(fd)
            fd = nfd
        return fd

    def unlink(self, dfd, name, path):
        try:
            os.unlink(name, dir_fd=dfd)
        except FileNotFoundError:
            pass
        except OSError as e:
            raise SystemExit(f"refused: {path}: {e.strerror}") from None

    def mkdir_now(self, path, mode, owner="root", group="wheel"):
        parent, name = os.path.split(path)
        pfd = self.folder(parent, create=True)
        try:
            try:
                os.mkdir(name, mode, dir_fd=pfd)
            except FileExistsError:
                pass
            try:
                fd = os.open(name, DIR, dir_fd=pfd)
            except OSError as e:
                raise SystemExit(f"refused: {path} is a link or no folder ({e.strerror}); root follows no link "
                                 "here") from None
        finally:
            os.close(pfd)
        try:
            self.owned_by(fd, path, (owner,))
            os.fchmod(fd, mode)
            self.own(fd, owner, group)
        finally:
            os.close(fd)

    def write_now(self, path, text, mode, owner="root", group="wheel"):
        """PATH replaced whole: a new file made beside it, its mode and owner set, then renamed over it."""
        parent, name = os.path.split(path)
        pfd = self.folder(parent, create=True)
        tmp = name + ".tmp"
        try:
            self.unlink(pfd, tmp, path + ".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=pfd)
            try:
                os.fchmod(fd, mode)
                self.own(fd, owner, group)
                _write_all(fd, text.encode())
            finally:
                os.close(fd)
            os.rename(tmp, name, src_dir_fd=pfd, dst_dir_fd=pfd)
        finally:
            os.close(pfd)

    def create(self, folder, name, data, mode, owner, group):
        """NAME made in FOLDER with DATA; False when the name is taken (kept: a re-run keeps every password)."""
        dfd = self.folder(folder)
        try:
            try:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=dfd)
            except FileExistsError:
                return False
        finally:
            os.close(dfd)
        try:
            os.fchmod(fd, mode)
            self.own(fd, owner, group)
            _write_all(fd, data.encode())
        finally:
            os.close(fd)
        return True

    def read(self, folder, name, owners, cap=4096):
        """The bytes of NAME in FOLDER, or None when absent: a regular file with one link, owned by root or one of
        OWNERS, at most CAP bytes. A link, a hard link, a FIFO or anything larger is refused."""
        dfd = self.folder(folder)
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
        except FileNotFoundError:
            return None
        except OSError as e:
            raise SystemExit(f"refused: {folder}/{name} is a link ({e.strerror}); root reads no link here") from None
        finally:
            os.close(dfd)
        try:
            st = os.fstat(fd)
            uids = {0, os.geteuid()} | {self.uid(o) for o in owners}
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid not in uids or st.st_size > cap:
                raise SystemExit(f"refused: {folder}/{name} is not a plain file of {' or '.join(owners)} (a hard "
                                 f"link, a FIFO, another owner's, or over {cap} bytes)")
            return os.read(fd, cap)
        finally:
            os.close(fd)

    def log_now(self, path, owner):
        """A daemon's log, made in root's LOGS before launchd opens it, perhaps as root: appended to, never truncated,
        600 and its daemon's user's."""
        folder, name = os.path.split(path)
        dfd = self.folder(folder)
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600,
                         dir_fd=dfd)
        except OSError as e:
            raise SystemExit(f"refused: {path} is a link or no file ({e.strerror})") from None
        finally:
            os.close(dfd)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise SystemExit(f"refused: {path} is not a plain file with one link")
            os.fchmod(fd, 0o600)
            self.own(fd, owner, "wheel")
        finally:
            os.close(fd)


def _write_all(fd, data):
    while data:
        data = data[os.write(fd, data):]


# ------------------------------------------------------------------ the code root and _ofserver run

# A user's write access, as the system answers it for that user (its groups and ACLs included): Apple's own shell,
# run as the user, prints each path on its input it may write. Nothing the user can change runs in it.
WRITABLE = 'while IFS= read -r p; do if [ -w "$p" ]; then printf "%s\\n" "$p"; fi; done; exit 0'


def chain(path, hops=40):
    """(folders, real): every folder PATH passes through, following its links one at a time (so the folder each link
    sits in counts), and the real path it names. Whoever may write one of those folders may change what PATH runs."""
    folders, cur, parts = ["/"], "/", [p for p in path.split("/") if p not in ("", ".")]
    while parts:
        part = parts.pop(0)
        if part == "..":
            cur = os.path.dirname(cur)
            continue
        nxt = os.path.join(cur, part)
        if os.path.islink(nxt):
            hops -= 1
            if hops < 0:
                raise OSError(errno.ELOOP, "too many links", path)
            target = os.readlink(nxt)
            cur = "/" if target.startswith("/") else cur
            parts = [p for p in target.split("/") if p not in ("", ".")] + parts
            continue
        cur = nxt
        if parts:
            folders.append(cur)
    return list(dict.fromkeys(folders)), cur


def tree_of(real):
    """The folder whose whole content a program at REAL may load: a folder is its own; for a file, the topmost folder
    above it that root does not own (Homebrew's /opt/homebrew, whose libraries its python loads by absolute path),
    else the folder two above it (a python's prefix, above its bin/)."""
    if os.path.isdir(real):
        return real
    top, cur = None, os.path.dirname(real)
    while cur not in ("/", ""):
        if os.lstat(cur).st_uid != 0:
            top = cur
        cur = os.path.dirname(cur)
    return top or os.path.dirname(os.path.dirname(real))


def _pth_paths(path, cap=65536):
    """The folders a .pth file in a site-packages folder adds to sys.path (its `import` lines are code in a file
    the walk already checks)."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return []
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return []
        text = os.read(fd, cap).decode("utf-8", "replace")
    finally:
        os.close(fd)
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not re.match(r"import[ \t]", line):
            out.append(os.path.normpath(os.path.join(os.path.dirname(path), line)))
    return out


def _inside(path, tree):
    return path == tree or path.startswith(tree.rstrip("/") + "/")


def owner_rule(untrusted):
    """A path's owner may be root, or an admin who is none of UNTRUSTED (of_secrets-review on PR 144)."""
    try:
        admin = grp.getgrnam("admin").gr_gid
    except KeyError:
        admin = None
    seen = {}

    def ok(uid):
        if uid == 0:
            return True
        if uid not in seen:
            try:
                pw = pwd.getpwuid(uid)
                seen[uid] = (admin is not None and pw.pw_name not in untrusted
                             and admin in os.getgrouplist(pw.pw_name, pw.pw_gid))
            except KeyError:
                seen[uid] = False
        return seen[uid]
    return ok


def trust_problems(roots, untrusted, run=subprocess.run, owner_ok=None, limit=10):
    """What is wrong with the code root and `_ofserver` run, as lines (empty: nothing). For each of ROOTS: every folder
    it passes through, link by link, and all of its tree (`tree_of`), each link in a tree checked as a path, and the
    folders a site-packages .pth adds. A path is refused when OWNER_OK refuses its owner (default `owner_rule`) or
    when one of UNTRUSTED (each organization user, and `_ofserver`) may write it, asked as that user (`WRITABLE`); a
    user that does not exist yet (`_ofserver` before `apply`) is skipped."""
    owner_ok = owner_ok or owner_rule(untrusted)
    problems, seen, trees, pending = [], {}, set(), list(roots)

    def add(p):
        if p in seen:
            return
        try:
            seen[p] = os.lstat(p)
        except OSError as e:
            seen[p] = None
            problems.append(f"{p}: {e.strerror}")

    def add_chain(p):
        try:
            folders, real = chain(p)
        except OSError as e:
            problems.append(f"{p}: {e.strerror}")
            return None
        for f in folders:
            add(f)
        add(real)
        return real

    while pending:
        real = add_chain(pending.pop(0))
        if real is None or not os.path.exists(real) or any(_inside(real, t) for t in trees):
            continue
        tree = tree_of(real)
        if any(_inside(tree, t) for t in trees):
            continue
        trees.add(tree)
        add_chain(tree)
        for top, dirs, files in os.walk(tree, onerror=lambda e: problems.append(f"{e.filename}: {e.strerror}")):
            for n in dirs + files:
                full = os.path.join(top, n)
                add(full)
                st = seen.get(full)
                if st is not None and stat.S_ISLNK(st.st_mode):
                    add_chain(full)  # its target, as a path; a folder it names outside the trees is not walked
                elif n.endswith(".pth") and os.path.basename(top) == "site-packages":
                    pending += [p for p in _pth_paths(full) if os.path.exists(p)]
    plain = sorted(p for p, st in seen.items() if st is not None and not stat.S_ISLNK(st.st_mode))
    owners = [p for p in plain if not owner_ok(seen[p].st_uid)]
    if owners:
        problems.append(f"{len(owners)} path(s) owned by neither root nor an admin outside the organization users: "
                        + ", ".join(f"{p} (uid {seen[p].st_uid})" for p in owners[:limit]))
    odd = [p for p in plain if "\n" in p]
    if odd:
        problems.append(f"{len(odd)} path(s) with a newline in the name: " + ", ".join(map(repr, odd[:limit])))
    listing = "".join(p + "\n" for p in plain if "\n" not in p)
    for user in untrusted:
        try:
            pwd.getpwnam(user)
        except KeyError:
            if user != SERVICE_USER:
                problems.append(f"no user {user}: the founder creates the organization users first (S0)")
            continue
        r = run(["sudo", "-n", "-u", user, "--", "/bin/sh", "-c", WRITABLE], input=listing, capture_output=True,
                text=True, cwd="/")
        if r.returncode != 0:
            problems.append(f"could not ask as {user} what it may write (exit {r.returncode}): "
                            f"{(r.stderr or '').strip()[:200]}")
            continue
        hits = [line for line in (r.stdout or "").splitlines() if line]
        if hits:
            problems.append(f"{user} may write {len(hits)} of them: " + ", ".join(hits[:limit]))
    return problems


def code_roots():
    """What root and `_ofserver` run: Homebrew when the python or ClickHouse is its, the python (and its install
    prefix), ClickHouse and the release checkout."""
    brew = [HOMEBREW] if any(p.startswith(HOMEBREW + "/") for p in (PYTHON, CLICKHOUSE)) else []
    return brew + [PYTHON, CLICKHOUSE, OF_ROOT]


def check_code(org_users, run=subprocess.run, here=None):
    """trust_problems over code_roots(), and this script must be the release checkout's, the one the check covers."""
    here = os.path.realpath(here or __file__)
    problems = []
    if not here.startswith(os.path.realpath(OF_ROOT) + os.sep):
        problems.append(f"this script runs from {here}, not from the release checkout {OF_ROOT} the check covers")
    return problems + trust_problems(code_roots(), [*org_users, SERVICE_USER], run=run)


# ------------------------------------------------------------------ the plan


class Plan(Files):
    """The steps, each (description, function). `plan` prints them; `apply` runs them."""

    def __init__(self, org_users, grant_pub, orgs, root="/", run=subprocess.run, out=print, tenant="default"):
        super().__init__(root)
        self.org_users, self.grant_pub, self.orgs, self.tenant = org_users, grant_pub, orgs, tenant
        self.run, self.out, self.steps = run, out, []

    def step(self, what, fn=None):
        self.steps.append((what, fn))

    def write(self, path, text, mode, owner="root", group="wheel"):
        self.step(f"write {path} ({oct(mode)[2:]} {owner}:{group})", lambda: self.write_now(path, text, mode, owner, group))

    def mkdir(self, path, mode, owner="root", group="wheel"):
        self.step(f"mkdir {path} ({oct(mode)[2:]} {owner}:{group})", lambda: self.mkdir_now(path, mode, owner, group))

    def log(self, path, owner):
        self.step(f"log {path} (600 {owner}, in root's {LOGS})", lambda: self.log_now(path, owner))

    def cmd(self, argv, check=True):
        def go():
            r = self.run(argv, capture_output=True, text=True)
            if check and r.returncode != 0:
                raise SystemExit(f"{' '.join(argv)}: exit {r.returncode}: {(r.stderr or '').strip()[:200]}")
        self.step("$ " + " ".join(argv), go)

    # -------------------------------------------------------------- the content

    def build(self):
        self.service_user()
        for u in self.org_users:
            self.step(f"check user {u} exists; chmod 700 /Users/{u}", self._org_user(u))
        self.mkdir(ETC, 0o755)
        self.mkdir(f"{ETC}/clickhouse", 0o750, "root", SERVICE_USER)
        for d in ("state", "secrets", "clickhouse", "clickhouse/tmp", "clickhouse/user_files", "clickhouse/log"):
            self.mkdir(f"{SERVICE_HOME}/{d}", 0o700, SERVICE_USER, SERVICE_USER)
        self.mkdir(LOGS, 0o755)
        self.mkdir(WATCHDOG_STATE, 0o700)
        self.write(f"{ETC}/server.yaml", server_yaml(self.orgs, self.tenant), 0o640, "root", SERVICE_USER)
        self.write(f"{ETC}/clickhouse/config.xml", clickhouse_config(), 0o640, "root", SERVICE_USER)
        self.step(f"ClickHouse passwords in {SERVICE_HOME}/secrets (600, {SERVICE_USER}), never printed: {ADMIN} "
                  f"(users.xml, by its hash) and the {len(self.db_users())} users schema.py creates", self._passwords)
        if self.grant_pub:
            self.step(f"pin the grant key's public half: {ETC}/grant_ed25519.pub (644 root:wheel)", self._pin)
        else:
            self.step(f"NO --grant-pub: {ETC}/grant_ed25519.pub stays absent and every grant fails to verify")
        self.daemons()

    def service_user(self):
        self.step(f"create the hidden service user {SERVICE_USER} (no login shell, home {SERVICE_HOME})",
                  self._service_user)
        self.mkdir(SERVICE_HOME, 0o700, SERVICE_USER, SERVICE_USER)

    def daemons(self):
        py, of_bin, server_py = PYTHON, f"{OF_ROOT}/bin/of", f"{OF_ROOT}/bin/of_server.py"
        # what runs as root or _ofserver reads no PYTHON* variable and no user site (-I would also drop the script's
        # own folder from sys.path, where of_server.py finds its modules)
        priv = [py, "-E", "-s"]
        self.log(f"{LOGS}/server.log", SERVICE_USER)
        self.write(f"{DAEMONS}/{SERVER_LABEL}.plist", plist(
            SERVER_LABEL, priv + [server_py, "--config", f"{ETC}/server.yaml", "run"], user=SERVICE_USER,
            env={"HOME": SERVICE_HOME}, keep="failure", log=f"{LOGS}/server.log",
            workdir=f"{SERVICE_HOME}/state"), 0o644)
        self.log(f"{LOGS}/clickhouse.log", SERVICE_USER)
        self.write(f"{DAEMONS}/{CLICKHOUSE_LABEL}.plist", plist(
            CLICKHOUSE_LABEL, [CLICKHOUSE, "server", "--config-file", f"{ETC}/clickhouse/config.xml"],
            user=SERVICE_USER, env={"HOME": SERVICE_HOME}, log=f"{LOGS}/clickhouse.log",
            workdir=f"{SERVICE_HOME}/clickhouse"), 0o644)
        for u in self.org_users:
            home = f"/Users/{u}"
            self.log(f"{LOGS}/relay-{u}.log", u)
            self.write(f"{DAEMONS}/{relay_label(u)}.plist", plist(
                relay_label(u), [py, of_bin, "relay", "run"], user=u, env={"HOME": home}, keep="failure",
                log=f"{LOGS}/relay-{u}.log", workdir=home), 0o644)
        argv = priv + [server_py, "--config", f"{ETC}/server.yaml", "host-watchdog", "--domain", "system",
                       "--state-dir", WATCHDOG_STATE]
        for u in self.org_users:
            argv += ["--relay", f"{u}:{relay_label(u)}:/Users/{u}/.local/state/of"]
        self.log(f"{LOGS}/host-watchdog.log", "root")
        self.write(f"{DAEMONS}/{WATCHDOG_LABEL}.plist", plist(
            WATCHDOG_LABEL, argv, env={"HOME": "/var/root"}, interval=60, log=f"{LOGS}/host-watchdog.log",
            workdir=WATCHDOG_STATE), 0o644)
        self.step("the daemons are written, NOT loaded: `setup.py start` loads them at S3")

    # -------------------------------------------------------------- the steps' bodies

    def _service_user(self):
        if self.root:
            return
        try:
            pwd.getpwnam(SERVICE_USER)
            return
        except KeyError:
            pass
        uid = next(i for i in range(400, 500) if not _taken(i))
        for argv in (["dscl", ".", "-create", f"/Groups/{SERVICE_USER}"],
                     ["dscl", ".", "-create", f"/Groups/{SERVICE_USER}", "PrimaryGroupID", str(uid)],
                     ["dscl", ".", "-create", f"/Users/{SERVICE_USER}"],
                     ["dscl", ".", "-create", f"/Users/{SERVICE_USER}", "UniqueID", str(uid)],
                     ["dscl", ".", "-create", f"/Users/{SERVICE_USER}", "PrimaryGroupID", str(uid)],
                     ["dscl", ".", "-create", f"/Users/{SERVICE_USER}", "UserShell", "/usr/bin/false"],
                     ["dscl", ".", "-create", f"/Users/{SERVICE_USER}", "NFSHomeDirectory", SERVICE_HOME],
                     ["dscl", ".", "-create", f"/Users/{SERVICE_USER}", "RealName", "OF server"],
                     ["dscl", ".", "-create", f"/Users/{SERVICE_USER}", "IsHidden", "1"]):
            r = self.run(argv, capture_output=True, text=True)
            if r.returncode != 0:
                raise SystemExit(f"{' '.join(argv)}: exit {r.returncode}")

    def _org_user(self, u):
        def go():
            if self.root:
                os.makedirs(self.p(f"/Users/{u}"), exist_ok=True)
            else:
                try:
                    pwd.getpwnam(u)
                except KeyError:
                    raise SystemExit(f"no user {u}: the founder creates the organization users first (S0)")
            fd = self.folder(f"/Users/{u}")
            try:
                self.owned_by(fd, f"/Users/{u}", (u,))
                os.fchmod(fd, 0o700)  # no other organization's user reads this one's home (§9)
            finally:
                os.close(fd)
        return go

    def db_users(self):
        return list(ch_schema().users(org_dbs(self.orgs), VAULT, UNASSIGNED))

    def _passwords(self):
        import hashlib
        folder = f"{SERVICE_HOME}/secrets"
        for name in [ADMIN] + self.db_users():  # an existing password is kept: a re-run changes nothing
            self.create(folder, f"{name}.password", secrets.token_urlsafe(32), 0o600, SERVICE_USER, SERVICE_USER)
        admin = self.read(folder, f"{ADMIN}.password", (SERVICE_USER,))
        self.write_now(f"{ETC}/clickhouse/users.xml", users_xml(hashlib.sha256(admin).hexdigest()), 0o640, "root",
                       SERVICE_USER)

    def _pin(self):
        with open(self.grant_pub) as fh:
            line = fh.read().strip()
        if not line.startswith("ssh-ed25519 ") or "PRIVATE" in line:
            raise SystemExit(f"{self.grant_pub}: not an OpenSSH ed25519 public key line")
        self.write_now(f"{ETC}/grant_ed25519.pub", line + "\n", 0o644, "root", "wheel")

    def apply(self):
        for what, fn in self.steps:
            self.out(what)
            if fn:
                fn()

    def show(self):
        for what, _ in self.steps:
            self.out(what)


def _taken(i):
    try:
        pwd.getpwuid(i)
        return True
    except KeyError:
        pass
    try:
        grp.getgrgid(i)
        return True
    except KeyError:
        return False


def labels(org_users):
    return [CLICKHOUSE_LABEL, SERVER_LABEL] + [relay_label(u) for u in org_users] + [WATCHDOG_LABEL]


def start_stop(action, org_users, run=subprocess.run, out=print):
    """Load (in order: storage, server, relays, watchdog) or unload them in the system domain."""
    order = labels(org_users) if action == "start" else list(reversed(labels(org_users)))
    for label in order:
        argv = (["launchctl", "bootstrap", "system", f"{DAEMONS}/{label}.plist"] if action == "start"
                else ["launchctl", "bootout", f"system/{label}"])
        r = run(argv, capture_output=True, text=True)
        out(f"{label}: {'ok' if r.returncode == 0 else 'exit ' + str(r.returncode)}")


def load_schema(orgs, run=subprocess.run, out=print, root=""):
    """S3, once ClickHouse runs: deploy/clickhouse/schema.py's SQL, its users' passwords hashed from the secrets folder,
    through clickhouse client as the admin. The admin's password goes in a client config file (600, _ofserver) that
    is made new (whatever had its name is removed first, never written through) and removed after, never on a command
    line."""
    f = Files(root)
    folder = f"{SERVICE_HOME}/secrets"

    def password(user):
        return f.read(folder, f"{user}.password", (SERVICE_USER,))

    sql = ch_schema().schema(org_dbs(orgs), VAULT, UNASSIGNED, read=password)
    pw = password(ADMIN)
    if pw is None:
        raise SystemExit(f"no {ADMIN} password in {folder}: run `apply` first")
    from xml.sax.saxutils import escape
    config = (f"<config><user>{ADMIN}</user><password>{escape(pw.decode().strip())}</password><host>127.0.0.1</host>"
              f"<port>{CH_TCP}</port></config>\n")
    dfd = f.folder(folder)
    try:
        f.unlink(dfd, "client.xml", f"{folder}/client.xml")
        fd = os.open("client.xml", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dfd)
        try:
            os.fchmod(fd, 0o600)
            f.own(fd, SERVICE_USER, SERVICE_USER)
            _write_all(fd, config.encode())
        finally:
            os.close(fd)
        try:
            r = run(["sudo", "-u", SERVICE_USER, CLICKHOUSE, "client", "--config-file", f.p(f"{folder}/client.xml"),
                     "--multiquery"], input=sql, capture_output=True, text=True)
        finally:
            f.unlink(dfd, "client.xml", f"{folder}/client.xml")
    finally:
        os.close(dfd)
    out(f"schema: {'loaded' if r.returncode == 0 else 'exit ' + str(r.returncode)}"
        + ("" if r.returncode == 0 else f": {(r.stderr or '').strip()[:300]}"))
    return r.returncode


def main(argv=None, run=subprocess.run):
    ap = argparse.ArgumentParser(description="the agent host's server side (deploy/host/setup.py)")
    ap.add_argument("action", choices=["plan", "apply", "start", "stop", "schema"], nargs="?", default="plan")
    ap.add_argument("--tenant", default="default", help="the tenant's name, as the machines' ship.yaml has it")
    ap.add_argument("--org-user", action="append", default=[], help="an organization's OS user (repeat)")
    ap.add_argument("--org", action="append", default=[], help="an organization of-server serves, as NAME or "
                    "NAME=DATABASE (orgs.yaml's database); repeat; default: the org users' names")
    ap.add_argument("--grant-pub", help="the grant key's public half (OpenSSH line), pinned root-owned")
    ap.add_argument("--root", default="/", help=argparse.SUPPRESS)  # tests: a tree instead of /
    a = ap.parse_args(argv)
    if not a.org_user:
        ap.error("give --org-user for each organization's OS user")
    bad = [u for u in a.org_user if not USER_NAME.fullmatch(u)]
    if bad:
        ap.error(f"not a user name: {', '.join(bad)}")
    if a.root == "/" and a.action != "stop":
        if os.geteuid() != 0:
            ap.error(f"{a.action} runs as root (sudo): it checks, as each organization user, what they may write")
        problems = check_code(a.org_user, run=run)
        if problems:
            print("refused: root or _ofserver would run code an organization user or _ofserver can change; "
                  "nothing was done:")
            for p in problems:
                print("  " + p)
            return 2
    if a.action in ("start", "stop"):
        return start_stop(a.action, a.org_user) or 0
    if a.action == "schema":
        return load_schema(a.org or a.org_user, root="" if a.root == "/" else a.root)
    p = Plan(a.org_user, a.grant_pub, a.org or a.org_user, root=a.root, tenant=a.tenant)
    p.build()
    (p.apply if a.action == "apply" else p.show)()
    return 0


if __name__ == "__main__":
    sys.exit(main())
