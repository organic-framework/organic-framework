"""`of herdr service install|uninstall|status`: this user's herdr server as a systemd user unit, on Linux.

herdr 0.9.5 runs `herdr server` in the foreground. On Linux it stays in whatever session started it: herdr's
`prepare_server_process` does nothing outside macOS (src/platform/mod.rs), and an attach that finds no server spawns
one detached from the client but still in the same session (src/server/autodetect.rs, `spawn_server_daemon`). On the
agent host, `of`'s server ran in an sshd session scope, and it would have ended with that login, together with every
pane and agent in it (of_architect's hand-over 763bfda4; of_agent-server, msgs 01a1faa3-4174 and 01a1fa98-86c9). This
unit runs it under the user's own service manager, which linger keeps up with no login session and starts at boot.

What the unit does, and why:
- OF_AUTOMATION=1, like every unit OF writes (AGENTS.md), and herdr's panes inherit it from the server
  (src/pane.rs). That is wanted: any agent of a herdr can type into any of its panes (`herdr pane send-text`), so an
  owner-only command (`of diff approve`, `of access`) must refuse in a herdr pane on a host. The owner runs them
  over plain SSH as the host's admin user (of_architect, msg 01a1fab3-ecf5; of_server, msg 01a1fab2-f750).
- No SSH agent (`SSH_AUTH_SOCK=`). herdr still gives a pane the agent of a client that attached with one
  (src/platform/ssh_agent.rs).
- A fixed PATH: herdr's folder, ~/.local/bin (where INSTALL.md puts `of` and Claude Code's installer puts
  `claude`), then the system's folders. Linux panes start a non-login shell (src/pane.rs,
  `shell_mode_uses_login_shell`), so they read the shell's rc file and never its profile. The PATH does not depend
  on the shell the install runs from, so a setup script that writes this text and a later install write the same
  file (one writer of one unit text: of_architect, msg 01a1fab3-ecf5).
- Slice=session.slice and OOMPolicy=continue (of_agent-server's hand-over e8a57731). The unit is one cgroup that
  holds every pane and agent. systemd-oomd kills a whole cgroup (systemd-oomd.service(8)), and a desktop distribution
  may mark app.slice, the default slice of a user's services, for it (ManagedOOMMemoryPressure=kill,
  ManagedOOMSwap=kill), as the agent host's does. On a host that marks only app.slice, session.slice takes the unit
  out of oomd's candidates. It does not where session.slice or one of its ancestors is marked, as Fedora's and
  Debian's oomd defaults do (of_review, PR 534); `oomctl` lists what a host monitors. The kernel's OOM killer takes
  one process, and under the default OOMPolicy=stop systemd would then stop the unit and every other pane with it
  (systemd.service(5)). continue keeps the unit, unless that process is herdr's server, the unit's main process. The
  slice applies at the unit's next start, so a server under the unit in another slice is not done yet.
- Its output appends to herdr-server.log in log_dir(), never in OF's state folder: systemd opens that path itself,
  outside any sandbox, and agents' sandboxed sessions write in the state folder.
- Restart=on-failure, but not on exit status 1. That is how herdr exits when another server holds its socket, and a
  restart could only fail again.
- A live handoff (`herdr update --handoff`) does not survive under the unit. It starts the new server as a child and
  ends the old one (src/server/handoff.rs). systemd takes the old one's exit for the service's end and stops what is
  left in its cgroup. So herdr is restarted with `systemctl --user restart of-herdr.service` instead.

Installing never stops a running server: that would end every pane. A server already running outside the unit keeps
running until it stops or the machine restarts, and the install says how to move it under the unit now.
"""

import getpass
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import of_events
import of_systemd

UNIT = "of-herdr.service"
SLICE = "session.slice"
UNITS_DIR = "~/.config/systemd/user"
RESTART = f"systemctl --user restart {UNIT}"


def log_dir():
    """Where the unit's output goes: a folder no sandboxed session writes. systemd opens StandardOutput=append: itself,
    as the user and outside any sandbox, so a link or a pipe planted in OF's state folder would act on herdr's start
    (of_server's Gaps entry; of_agent-server, msg 01a2046c-4053). of_events.LOG_DIR once of_platform's hand-over
    58681935 makes it, else ~/.local/log/of, the folder that hand-over names."""
    return getattr(of_events, "LOG_DIR", None) or os.path.join(os.path.expanduser("~"), ".local", "log", "of")


def unit_path(herdr, home=None):
    """The panes' PATH: herdr's folder, ~/.local/bin, then the system's, whatever the calling shell's PATH is."""
    home = home or os.path.expanduser("~")
    dirs = [os.path.dirname(herdr), os.path.join(home, ".local", "bin"), "/usr/local/bin", "/usr/bin", "/bin"]
    return ":".join(dict.fromkeys(dirs))


def unit_text(herdr, path=None, log=None):
    """The unit for `herdr` (an absolute path). `path` and `log` default to this user's."""
    w = of_systemd._word
    path = path or unit_path(herdr)
    log = (log or os.path.join(log_dir(), "herdr-server.log")).replace("%", "%%")
    return ("[Unit]\nDescription=Organic Framework: this user's herdr server\n\n[Service]\n"
            "WorkingDirectory=%h\n"
            # one cgroup holds every agent: out of the slice a desktop marks for systemd-oomd, which kills whole cgroups
            f"Slice={SLICE}\n"
            # the kernel's OOM killer takes one process; the default (stop) would then end every other agent. continue
            # cannot help when that process is herdr's server, the unit's main process
            "OOMPolicy=continue\n"
            f"ExecStart={w(herdr)} server\n"
            f"ExecStop={w(herdr)} server stop\n"
            "Restart=on-failure\nRestartSec=10\n"
            # exit status 1: another server holds the socket, so a restart could only fail again
            "RestartPreventExitStatus=1\n"
            "Environment=OF_AUTOMATION=1\nEnvironment=SSH_AUTH_SOCK=\n"
            f"Environment={w('PATH=' + path)}\n"
            f"StandardOutput=append:{log}\nStandardError=append:{log}\n"
            "\n[Install]\nWantedBy=default.target\n")


def _user_env(environ=None, uid=None, runtime_root="/run/user"):
    """The environment for `systemctl --user`. With no login session (a cron, `runuser`, a script), XDG_RUNTIME_DIR is
    unset and systemctl cannot find the user manager that linger keeps. This user's /run/user/<uid> then stands in
    (of_lab's Lab VM run, hand-over 6759dc6d)."""
    env = dict(os.environ if environ is None else environ)
    runtime = os.path.join(runtime_root, str(os.getuid() if uid is None else uid))
    if not env.get("XDG_RUNTIME_DIR") and os.path.isdir(runtime):
        env["XDG_RUNTIME_DIR"] = runtime
    return env


def _ctl(run, *args, check=False):
    return run(["systemctl", "--user", *args], capture_output=True, text=True, check=check, env=_user_env())


def server_status(herdr, run):
    """(running, socket) of this user's default herdr server, from `herdr status server`: its `status: running` and `socket:` lines."""
    try:
        r = run([herdr, "status", "server"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False, None
    lines = [line.strip() for line in (r.stdout or "").splitlines()]
    sock = next((line.partition(":")[2].strip() for line in lines if line.startswith("socket:")), None)
    return r.returncode == 0 and "status: running" in lines, sock


def socket_holder(sock, proc="/proc", uid=None):
    """(pid, cgroup) of this user's process listening on the Unix socket `sock`, or None. `herdr status server` names no
    pid, and a lab server with its own XDG folders is another herdr server of the same user, so the server is found by
    its socket: its inode in /proc/net/unix, then the process holding it."""
    uid = os.getuid() if uid is None else uid
    try:
        rows = Path(proc, "net", "unix").read_text().splitlines()[1:]
    except OSError:
        return None
    inodes = {f"socket:[{cols[6]}]" for cols in (row.split() for row in rows) if len(cols) >= 8 and cols[7] == sock}
    if not inodes:
        return None
    with os.scandir(proc) as entries:
        for entry in entries:
            if not entry.name.isdigit():
                continue
            try:
                if entry.stat().st_uid != uid:
                    continue
                with os.scandir(os.path.join(entry.path, "fd")) as fds:
                    held = any(os.readlink(fd.path) in inodes for fd in fds)
                cgroup = Path(entry.path, "cgroup").read_text() if held else ""
            except OSError:
                continue
            if held:
                line = next((ln for ln in cgroup.splitlines() if ln.startswith("0::")), cgroup.strip())
                return int(entry.name), line.partition("::")[2] or line
    return None


def under_unit(cgroup):
    return cgroup.rstrip("/").endswith("/" + UNIT)


def slice_of(cgroup):
    """The slice a unit's cgroup sits in: `.../user@1001.service/app.slice/of-herdr.service` gives app.slice."""
    parts = cgroup.rstrip("/").split("/")
    return parts[-2] if len(parts) > 1 else ""


def _stale_slice(cgroup):
    """"" when a server under the unit sits in the unit's slice, else what to say. The slice applies only at the unit's
    next start, so a server still in another slice runs the old text (of_review, PR 534)."""
    if slice_of(cgroup) == SLICE:
        return ""
    return (f", but in {slice_of(cgroup)}, not {SLICE}. The unit's text applies at its next restart: `{RESTART}` ends "
            "every pane")


def linger(run, user=None):
    """"yes", "no", or None when loginctl cannot tell."""
    try:
        r = run(["loginctl", "show-user", user or getpass.getuser(), "-p", "Linger", "--value"],
                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    value = (r.stdout or "").strip()
    return value if r.returncode == 0 and value in ("yes", "no") else None


def _linger_line(run):
    state = linger(run)
    if state == "yes":
        return "linger is on: the unit starts at boot and keeps running with no login session"
    who = getpass.getuser()
    head = "linger is off" if state == "no" else "linger could not be read"
    return (f"{head}: the unit starts only with a login and ends with the last one. The host's admin turns it on: "
            f"`sudo loginctl enable-linger {who}`")


def _herdr():
    herdr = shutil.which("herdr")
    if not herdr:
        raise SystemExit("of herdr service: herdr is not on PATH (INSTALL.md, the prerequisites)")
    return herdr


def _linux():
    if not sys.platform.startswith("linux"):
        raise SystemExit("of herdr service: Linux only. On macOS herdr keeps its server in the user's own launchd "
                         "context itself (src/platform/macos/bootstrap.rs)")


def default_server(herdr, run, proc="/proc"):
    """None when no default server answers, else (pid, cgroup) of the process on its socket, (None, None) when that
    process cannot be read."""
    running, sock = server_status(herdr, run)
    if not running:
        return None
    return (socket_holder(sock, proc) if sock else None) or (None, None)


def install(run=subprocess.run, units_dir=None, herdr=None, proc="/proc", wait_s=10, sleep=time.sleep):
    """Write the unit, enable it, and start it when no server runs. Returns (lines, ok)."""
    _linux()
    herdr = herdr or _herdr()
    directory = Path(units_dir or UNITS_DIR).expanduser()
    target = directory / UNIT
    text = unit_text(herdr)
    changed = not target.exists() or target.read_bytes() != text.encode()
    os.makedirs(log_dir(), mode=0o700, exist_ok=True)  # systemd opens the log before ExecStart
    lines = []
    if changed:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", dir=directory, delete=False) as fh:
            fh.write(text)
        os.replace(fh.name, target)
        _ctl(run, "daemon-reload", check=True)
        lines.append(f"wrote {target}")
    _ctl(run, "enable", UNIT, check=True)
    lines.append(f"{UNIT} enabled")
    lines.append(_linger_line(run))
    server = default_server(herdr, run, proc)
    if server is not None:
        pid, cg = server
        if cg is not None and under_unit(cg):
            lines.append(f"herdr's server runs under the unit (pid {pid})"
                         + (_stale_slice(cg) or (f". The new unit applies at its next restart: `{RESTART}` ends every "
                                                 "pane" if changed else "")))
        else:
            where = f"(pid {pid}) runs outside the unit, in {cg}," if pid else "runs, but its process could not be read,"
            lines.append(f"herdr's server {where} and ends with that session. The unit takes it over at the next "
                         f"boot, or now with `herdr server stop` and then `systemctl --user start {UNIT}`. Every "
                         "pane ends either way, as at a reboot")
        return lines, True
    _ctl(run, "start", UNIT)
    deadline = time.time() + wait_s
    while time.time() < deadline:
        server = default_server(herdr, run, proc)
        if server is not None:
            pid, cg = server
            if cg is not None and under_unit(cg):
                lines.append(f"herdr's server started under the unit (pid {pid})")
                return lines, True
            lines.append(f"a herdr server started outside the unit (pid {pid}, {cg}): an attach started it first")
            return lines, False
        sleep(0.5)
    lines.append(f"herdr's server did not start: `systemctl --user status {UNIT}` and "
                 f"{os.path.join(log_dir(), 'herdr-server.log')} say why")
    return lines, False


def uninstall(run=subprocess.run, units_dir=None):
    """Disable and remove the unit; a server running under it keeps running until it stops."""
    _linux()
    target = Path(units_dir or UNITS_DIR).expanduser() / UNIT
    if not target.exists():
        return [f"no {UNIT} here"], True
    _ctl(run, "disable", UNIT)
    target.unlink()
    _ctl(run, "daemon-reload")
    active = _ctl(run, "is-active", UNIT).stdout.strip() == "active"
    lines = [f"removed {target}"]
    if active:
        lines.append(f"herdr's server keeps running until it stops; nothing starts it at boot now. "
                     f"`systemctl --user stop {UNIT}` stops it now, with every pane")
    return lines, True


def status(run=subprocess.run, units_dir=None, herdr=None, proc="/proc"):
    """Exit 0 only when the unit is installed and the default server runs under it."""
    _linux()
    herdr = herdr or _herdr()
    target = Path(units_dir or UNITS_DIR).expanduser() / UNIT
    lines = [f"{UNIT}: " + ("installed" if target.exists() else "not installed")
             + f", {_ctl(run, 'is-enabled', UNIT).stdout.strip() or 'unknown'}"
             + f", {_ctl(run, 'is-active', UNIT).stdout.strip() or 'unknown'}"]
    lines.append(_linger_line(run))
    server = default_server(herdr, run, proc)
    if server is None:
        lines.append("no herdr server answers for this user")
        return lines, False
    pid, cg = server
    if cg is None or not under_unit(cg):
        lines.append(f"herdr's server pid {pid}: outside the unit, in {cg}")
        return lines, False
    stale = _stale_slice(cg)
    lines.append(f"herdr's server pid {pid}: under the unit{stale}")
    return lines, target.exists() and not stale


def main(action):
    fn = {"install": install, "uninstall": uninstall, "status": status}[action]
    try:
        lines, ok = fn()
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"of herdr service {action}: {' '.join(e.cmd)}: {(e.stderr or str(e)).strip()}") from e
    for line in lines:
        print(f"of herdr service: {line}")
    return 0 if ok else 1
