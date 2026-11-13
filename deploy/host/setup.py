#!/usr/bin/env python3
"""Set up the agent host's server side on a clean macOS (organic-framework-build reports/
agent-server-migration-plan-2026-11-11.md §3 B2 and §9; of_agent-server's hand-over aba3d171; owned by of_server).

On the host each organization's agents run as their own OS user (`of`, `acme`, …). A hidden service user, `_ofserver`,
holds of-server, ClickHouse, their passwords and the tenant master key, none of which an agent user can read. This
script, run as root:

- creates `_ofserver` (hidden, no login shell, its home /var/of-server mode 700) and checks each organization user
  exists, making its home 700;
- writes /etc/of: of-server's config (root-owned, readable by `_ofserver` only), ClickHouse's config and users, and the
  grant key's public half, pinned (root-owned: the host verifies grants and never signs them, of_architect, msg
  01a1d66a-cd41);
- makes ClickHouse's passwords (mode 600, `_ofserver`), never printing one: an admin user in its users.xml, and one
  per user deploy/clickhouse/schema.py creates (`<db>_ingest`, `_redact`, `_read`), which `schema` loads at S3;
- writes the LaunchDaemons, each with UserName, and does NOT load them: of-server, ClickHouse, a relay per
  organization user, and one root watchdog that restarts a hung server or relay (`of_server.py host-watchdog`).

`plan` (the default) prints what it would do and changes nothing; `apply` does it; `start` and `stop` load and unload
the daemons; `schema` loads the databases, tables and users into the running ClickHouse (S3 of the plan). The tenant master key reaches /var/of-server/tenant.key by its own committed copy over
SSH (hand-over item 3), never through this script.

  sudo python3 deploy/host/setup.py plan  --org-user of --org-user acme --grant-pub FILE
  sudo python3 deploy/host/setup.py apply --org-user of --org-user acme --grant-pub FILE
  sudo python3 deploy/host/setup.py start|stop|schema --org-user of --org-user acme
"""

import argparse
import grp
import os
import pwd
import secrets
import subprocess
import sys

SERVICE_USER = "_ofserver"
SERVICE_HOME = "/var/of-server"
ETC = "/etc/of"
DAEMONS = "/Library/LaunchDaemons"
OF_ROOT = "/opt/organic-framework"     # the read-only release checkout (B6, of_platform's deploy step)
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


# ------------------------------------------------------------------ the plan


class Plan:
    """The steps, each (description, function). `plan` prints them; `apply` runs them."""

    def __init__(self, org_users, grant_pub, orgs, root="/", run=subprocess.run, out=print, tenant="default"):
        self.org_users, self.grant_pub, self.orgs, self.tenant = org_users, grant_pub, orgs, tenant
        self.root, self.run, self.out, self.steps = root.rstrip("/") or "", run, out, []

    def p(self, path):
        return self.root + path

    def step(self, what, fn=None):
        self.steps.append((what, fn))

    def write(self, path, text, mode, owner="root", group="wheel"):
        self.step(f"write {path} ({oct(mode)[2:]} {owner}:{group})", lambda: self.write_now(path, text, mode, owner, group))

    def write_now(self, path, text, mode, owner="root", group="wheel"):
        full = self.p(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        fd = os.open(full + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        os.replace(full + ".tmp", full)
        self.chown(full, owner, group)

    def mkdir(self, path, mode, owner="root", group="wheel"):
        def go():
            full = self.p(path)
            os.makedirs(full, exist_ok=True)
            os.chmod(full, mode)
            self.chown(full, owner, group)
        self.step(f"mkdir {path} ({oct(mode)[2:]} {owner}:{group})", go)

    def chown(self, full, owner, group):
        if self.root:
            return  # a test tree: no users to own it
        os.chown(full, pwd.getpwnam(owner).pw_uid, grp.getgrnam(group).gr_gid)

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
        self.write(f"{DAEMONS}/{SERVER_LABEL}.plist", plist(
            SERVER_LABEL, [py, server_py, "--config", f"{ETC}/server.yaml", "run"], user=SERVICE_USER,
            env={"HOME": SERVICE_HOME}, keep="failure", log=f"{SERVICE_HOME}/state/server.log",
            workdir=f"{SERVICE_HOME}/state"), 0o644)
        self.write(f"{DAEMONS}/{CLICKHOUSE_LABEL}.plist", plist(
            CLICKHOUSE_LABEL, [CLICKHOUSE, "server", "--config-file", f"{ETC}/clickhouse/config.xml"],
            user=SERVICE_USER, env={"HOME": SERVICE_HOME}, log=f"{SERVICE_HOME}/clickhouse/log/launchd.log",
            workdir=f"{SERVICE_HOME}/clickhouse"), 0o644)
        for u in self.org_users:
            home = f"/Users/{u}"
            self.write(f"{DAEMONS}/{relay_label(u)}.plist", plist(
                relay_label(u), [py, of_bin, "relay", "run"], user=u, env={"HOME": home}, keep="failure",
                log=f"{home}/.local/state/of/relay.log", workdir=home), 0o644)
        argv = [py, server_py, "--config", f"{ETC}/server.yaml", "host-watchdog", "--domain", "system"]
        for u in self.org_users:
            argv += ["--relay", f"{u}:{relay_label(u)}:/Users/{u}/.local/state/of"]
        self.write(f"{DAEMONS}/{WATCHDOG_LABEL}.plist", plist(
            WATCHDOG_LABEL, argv, env={"HOME": "/var/root"}, interval=60,
            log=f"{SERVICE_HOME}/state/watchdog.log"), 0o644)
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
                os.chmod(self.p(f"/Users/{u}"), 0o700)
                return
            try:
                pwd.getpwnam(u)
            except KeyError:
                raise SystemExit(f"no user {u}: the founder creates the organization users first (S0)")
            os.chmod(f"/Users/{u}", 0o700)  # no other organization's user reads this one's home (§9)
        return go

    def db_users(self):
        return list(ch_schema().users(org_dbs(self.orgs), VAULT, UNASSIGNED))

    def _passwords(self):
        import hashlib
        for name in [ADMIN] + self.db_users():
            path = self.p(f"{SERVICE_HOME}/secrets/{name}.password")
            if not os.path.exists(path):  # an existing password is kept: a re-run changes nothing
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as fh:
                    fh.write(secrets.token_urlsafe(32))
                self.chown(path, SERVICE_USER, SERVICE_USER)
        with open(self.p(f"{SERVICE_HOME}/secrets/{ADMIN}.password")) as fh:
            admin = hashlib.sha256(fh.read().encode()).hexdigest()
        self.write_now(f"{ETC}/clickhouse/users.xml", users_xml(admin), 0o640, "root", SERVICE_USER)

    def _pin(self):
        with open(self.grant_pub) as fh:
            line = fh.read().strip()
        if not line.startswith("ssh-ed25519 ") or "PRIVATE" in line:
            raise SystemExit(f"{self.grant_pub}: not an OpenSSH ed25519 public key line")
        full = self.p(f"{ETC}/grant_ed25519.pub")
        with open(full + ".tmp", "w") as fh:
            fh.write(line + "\n")
        os.chmod(full + ".tmp", 0o644)
        os.replace(full + ".tmp", full)
        self.chown(full, "root", "wheel")

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


def load_schema(orgs, run=subprocess.run, out=print):
    """S3, once ClickHouse runs: deploy/clickhouse/schema.py's SQL, its users' passwords hashed from the secrets folder,
    through clickhouse client as the admin. The admin's password goes in a client config file (600, _ofserver) that
    is removed after, never on a command line."""
    sql = ch_schema().schema(org_dbs(orgs), VAULT, UNASSIGNED, password_dir=f"{SERVICE_HOME}/secrets")
    cfg = f"{SERVICE_HOME}/secrets/client.xml"
    with open(f"{SERVICE_HOME}/secrets/{ADMIN}.password") as fh:
        pw = fh.read().strip()
    from xml.sax.saxutils import escape
    fd = os.open(cfg, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(f"<config><user>{ADMIN}</user><password>{escape(pw)}</password><host>127.0.0.1</host>"
                 f"<port>{CH_TCP}</port></config>\n")
    try:
        os.chown(cfg, pwd.getpwnam(SERVICE_USER).pw_uid, grp.getgrnam(SERVICE_USER).gr_gid)
        r = run(["sudo", "-u", SERVICE_USER, CLICKHOUSE, "client", "--config-file", cfg, "--multiquery"],
                input=sql, capture_output=True, text=True)
    finally:
        os.remove(cfg)
    out(f"schema: {'loaded' if r.returncode == 0 else 'exit ' + str(r.returncode)}"
        + ("" if r.returncode == 0 else f": {(r.stderr or '').strip()[:300]}"))
    return r.returncode


def main(argv=None):
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
    if a.action in ("start", "stop"):
        return start_stop(a.action, a.org_user) or 0
    if a.action == "schema":
        return load_schema(a.org or a.org_user)
    if a.action == "apply" and a.root == "/" and os.geteuid() != 0:
        ap.error("apply runs as root (sudo)")
    p = Plan(a.org_user, a.grant_pub, a.org or a.org_user, root=a.root, tenant=a.tenant)
    p.build()
    (p.apply if a.action == "apply" else p.show)()
    return 0


if __name__ == "__main__":
    sys.exit(main())
