"""The systemd user services managed by `of listen` and `of relay`."""

import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import of_events
import of_init


def _word(arg):
    """Keep paths with spaces in one word and literal percent signs out of systemd's specifier expansion."""
    arg = arg.replace("%", "%%")
    if re.fullmatch(r"[\w./%$:=+-]+", arg):
        return arg
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r") + '"'


def service(command, python, of_bin, watchdog=False):
    """The timer starts its one-shot watchdog, while the user manager keeps the long-running services up."""
    python, of_bin = os.fspath(python or sys.executable), os.fspath(of_bin or of_init.OF)
    if "$" in python or "$" in of_bin:
        python = ":" + python  # suppress environment expansion in this ExecStart command
    name = command + ("-watchdog" if watchdog else "")
    # never in the state folder (hand-over 58681935): systemd opens the path itself, outside any sandbox; the relay's
    # and the server's too since their keeper's change (of_server, 2026-11-22)
    log = of_events.log_path(name + ".log").replace("%", "%%")
    policy = "Type=oneshot" if watchdog else "Restart=always\nRestartSec=10"
    relation = f"PartOf=of-{name}.timer" if watchdog else "After=of-server.service"
    path = _word("PATH=" + of_init.service_path())  # herdr can live in the installing user's ~/.local/bin
    unit = (f"[Unit]\nDescription=Organic Framework {name}\n{relation}\n\n[Service]\n"
            f"ExecStart={_word(python)} {_word(of_bin)} {command} {'watchdog' if watchdog else 'run'}\n"
            f"{policy}\nEnvironment=OF_AUTOMATION=1\nEnvironment=SSH_AUTH_SOCK=\n"
            f"Environment={path}\n"
            f"StandardOutput=append:{log}\nStandardError=append:{log}\n")
    return unit if watchdog else unit + "\n[Install]\nWantedBy=default.target\n"


def install(units, wanted, uninstall=False, run=None, units_dir=None):
    """Only enable the units wanted by a target. A timer's one-shot service stays idle between checks."""
    run = run or subprocess.run
    directory = Path(units_dir or "~/.config/systemd/user").expanduser()
    wanted = [name for name in wanted if name in units]

    def ctl(*args, check=True):
        return run(["systemctl", "--user", *args], capture_output=True, text=True, check=check)

    existing = {name for name in units if (directory / name).exists()}
    if uninstall:
        disable = [name for name in wanted if name in existing]
        if disable:
            ctl("disable", "--now", *disable)
        for name in existing:
            (directory / name).unlink(missing_ok=True)
        if existing:
            ctl("daemon-reload")
        return [f"removed {name}" for name in units]

    os.makedirs(of_events.STATE_DIR, mode=0o700, exist_ok=True)  # systemd opens the logs before ExecStart
    directory.mkdir(parents=True, exist_ok=True)
    changed = [name for name, text in units.items()
               if name not in existing or (directory / name).read_bytes() != text.encode()]
    active = {name for name in wanted if name in existing and ctl("is-active", name, check=False).returncode == 0}
    start = [name for name in wanted if name in changed or name not in active]
    if changed:
        with tempfile.TemporaryDirectory(dir=directory) as staging:
            for name in changed:
                (Path(staging) / name).write_bytes(units[name].encode())
            # enable --now leaves an active service on its old ExecStart and environment.
            stop = [name for name in changed if name in active]
            if stop:
                ctl("stop", *stop)
            for name in changed:
                os.replace(Path(staging) / name, directory / name)
    if changed or start:
        ctl("daemon-reload")
    if start:
        ctl("enable", "--now", *start)
    status = dict.fromkeys(units, "unchanged")
    status.update(dict.fromkeys(changed, "installed"))
    status.update(dict.fromkeys(start, "loaded"))
    return [f"{name}: {state}" for name, state in status.items()]


def main_pid(unit, run=None):
    """Use the manager's live MainPID so a stale pid file cannot select a process to signal."""
    run = run or subprocess.run
    try:
        result = run(["systemctl", "--user", "show", "-p", "MainPID", "--value", unit],
                     capture_output=True, text=True, timeout=10)
        if result.returncode:
            return None
        pid = int(result.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return pid if pid > 0 else None
