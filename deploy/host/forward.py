#!/usr/bin/env python3
"""The laptop's reverse forward to the agent host: the host reaches this machine's of-server at its own
127.0.0.1:<port> (organic-framework-build reports/agent-host-continuation-plan-2026-11-18.md, section 2b: one server for
both organizations, staying on the laptop at first). Owned by of_server.

    python3 deploy/host/forward.py install --to <user>@<host> --key <private key file> [--port 18200] [--dry-run]
    python3 deploy/host/forward.py status
    python3 deploy/host/forward.py uninstall

A user LaunchAgent (dev.organic-framework.forward) runs `ssh -N -R 127.0.0.1:<port>:127.0.0.1:<port>` and launchd keeps it
running: ExitOnForwardFailure and keep-alives make a dead or refused forward end, and launchd starts it again. As every
OF process (AGENTS.md): OF_AUTOMATION=1, its own key, no SSH agent (SSH_AUTH_SOCK empty, IdentityAgent=none,
IdentitiesOnly), BatchMode, no agent forwarding. On the host, the key's authorized_keys line limits it to this one
forward: restrict,port-forwarding,permitlisten="127.0.0.1:<port>" (docs/server-several-machines.md, "A second machine
through a reverse forward"). The host's key must be in known_hosts first: BatchMode never asks.
"""
import argparse
import os
import plistlib
import subprocess
import sys

LABEL = "dev.organic-framework.forward"
PORT = 18200


def agent_path(home=None):
    return os.path.join(home or os.path.expanduser("~"), "Library", "LaunchAgents", f"{LABEL}.plist")


def ssh_argv(to, key, port=PORT):
    """The forward's ssh call: its own key, never an SSH agent; ends on a failed forward or a dead link."""
    return ["/usr/bin/ssh", "-N", "-R", f"127.0.0.1:{port}:127.0.0.1:{port}",
            "-i", os.path.expanduser(key), "-o", "IdentityAgent=none", "-o", "IdentitiesOnly=yes",
            "-o", "BatchMode=yes", "-o", "ForwardAgent=no", "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3", to]


def unit(to, key, port=PORT, home=None):
    """The LaunchAgent's dictionary."""
    home = home or os.path.expanduser("~")
    log = os.path.join(home, ".local", "state", "of", "forward.log")
    return {"Label": LABEL, "ProgramArguments": ssh_argv(to, key, port), "RunAtLoad": True, "KeepAlive": True,
            "ThrottleInterval": 30, "EnvironmentVariables": {"OF_AUTOMATION": "1", "SSH_AUTH_SOCK": ""},
            "StandardOutPath": log, "StandardErrorPath": log, "ProcessType": "Background"}


def authorized_line(pubkey, port=PORT):
    """The host's authorized_keys line for the forward's key: this one forward and nothing else."""
    return f'restrict,port-forwarding,permitlisten="127.0.0.1:{port}" {pubkey.strip()}'


def install(to, key, port=PORT, dry_run=False, home=None, run=subprocess.run, out=print):
    key = os.path.expanduser(key)
    if not os.path.isfile(key):
        raise SystemExit(f"forward: no key file at {key}")
    if "@" not in to:
        raise SystemExit("forward: --to <user>@<host>")
    path = agent_path(home)
    doc = unit(to, key, port, home)
    if dry_run:
        out(plistlib.dumps(doc).decode())
        pub = key + ".pub"
        if os.path.isfile(pub):
            out("on the host, authorized_keys: " + authorized_line(open(pub).read(), port))
        return doc
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.makedirs(os.path.dirname(doc["StandardOutPath"]), exist_ok=True)
    with open(path, "wb") as fh:
        plistlib.dump(doc, fh)
    os.chmod(path, 0o644)
    uid = os.getuid()
    run(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], capture_output=True)
    r = run(["launchctl", "bootstrap", f"gui/{uid}", path], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"forward: launchctl bootstrap failed: {(r.stderr or '').strip()[:200]}")
    out(f"forward installed: {LABEL} keeps ssh -N -R 127.0.0.1:{port} to {to} running")
    return doc


def uninstall(home=None, run=subprocess.run, out=print):
    run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
    path = agent_path(home)
    if os.path.exists(path):
        os.remove(path)
    out(f"forward removed: {LABEL}")


def status(run=subprocess.run, out=print):
    r = run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], capture_output=True, text=True)
    if r.returncode != 0:
        out(f"{LABEL}: not loaded")
        return 1
    state = next((ln.strip() for ln in r.stdout.splitlines() if ln.strip().startswith("state =")), "state = ?")
    out(f"{LABEL}: {state}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="The laptop's reverse forward to the agent host (deploy/host/forward.py).")
    ap.add_argument("action", choices=["install", "uninstall", "status"])
    ap.add_argument("--to", help="<user>@<host>")
    ap.add_argument("--key", help="the forward's own private key file")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.action == "install":
        if not (a.to and a.key):
            ap.error("install needs --to and --key")
        install(a.to, a.key, a.port, a.dry_run)
        return 0
    if a.action == "uninstall":
        uninstall()
        return 0
    return status()


if __name__ == "__main__":
    sys.exit(main())
