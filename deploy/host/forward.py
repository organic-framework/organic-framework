#!/usr/bin/env python3
"""The laptop's reverse forward to the agent host: the host reaches this machine's of-server at its own
127.0.0.1:<port> (organic-framework-build reports/agent-host-continuation-plan-2026-11-18.md, section 2b: one server for
both organizations, staying on the laptop at first). Owned by of_server.

    python3 deploy/host/forward.py install --to <user>@<host> --key <private key file> [--port 18200] [--dry-run]
    python3 deploy/host/forward.py status
    python3 deploy/host/forward.py uninstall

A user LaunchAgent (dev.organic-framework.forward) runs `ssh -N -R 127.0.0.1:<port>:127.0.0.1:<local port>` and launchd
keeps it running: ExitOnForwardFailure and keep-alives make a dead or refused forward end, and launchd starts it again.
The local port is the server's TLS listener (`of server tls-init`), so the host's clients trust only its pinned
certificate and a process that takes the port while the forward is down gets nothing (of_secrets-review on
organic-framework PR 350). As every OF process (AGENTS.md): OF_AUTOMATION=1, its own key, no SSH agent (SSH_AUTH_SOCK
empty, IdentityAgent=none, IdentitiesOnly), BatchMode, no agent forwarding.

On the host the forward logs in as a user of its own, held to this one remote forward twice: by an sshd Match block
(remote forwarding only, that one listen address, no local, dynamic or socket forwarding, no session, no command) and by
the key's authorized_keys line (`--dry-run` prints both; docs/server-several-machines.md 4.8). The host's key must be in
known_hosts first: BatchMode never asks.
"""
import argparse
import os
import plistlib
import subprocess
import sys

LABEL = "dev.organic-framework.forward"
PORT = 18200        # on the host: the address OF's clients use
LOCAL_PORT = 18201  # on the laptop: the server's TLS listener (of_server.TLS_PORT)
HOST_USER = "of-forward"


def agent_path(home=None):
    return os.path.join(home or os.path.expanduser("~"), "Library", "LaunchAgents", f"{LABEL}.plist")


def ssh_argv(to, key, port=PORT, local_port=LOCAL_PORT):
    """The forward's ssh call: its own key, never an SSH agent; ends on a failed forward or a dead link."""
    return ["/usr/bin/ssh", "-N", "-R", f"127.0.0.1:{port}:127.0.0.1:{local_port}",
            "-i", os.path.expanduser(key), "-o", "IdentityAgent=none", "-o", "IdentitiesOnly=yes",
            "-o", "BatchMode=yes", "-o", "ForwardAgent=no", "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3", to]


def unit(to, key, port=PORT, home=None, local_port=LOCAL_PORT):
    """The LaunchAgent's dictionary."""
    home = home or os.path.expanduser("~")
    log = os.path.join(home, ".local", "state", "of", "forward.log")
    return {"Label": LABEL, "ProgramArguments": ssh_argv(to, key, port, local_port), "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": 30, "EnvironmentVariables": {"OF_AUTOMATION": "1", "SSH_AUTH_SOCK": ""},
            "StandardOutPath": log, "StandardErrorPath": log, "ProcessType": "Background"}


def authorized_line(pubkey, port=PORT):
    """The host's authorized_keys line for the forward's key: no command, and a listen on that one address."""
    return f'restrict,port-forwarding,permitlisten="127.0.0.1:{port}",command="/usr/bin/false" {pubkey.strip()}'


def sshd_block(user=HOST_USER, port=PORT):
    """The host's sshd Match block for the forward's user (sshd_config(5)): remote forwarding to that one listen
    address and nothing else: no local or dynamic forwarding (-L, -D, -W), no socket forwarding, no session."""
    return (f"# /etc/ssh/sshd_config.d/of-forward.conf (docs/server-several-machines.md 4.8)\n"
            f"Match User {user}\n"
            f"    AllowTcpForwarding remote\n"
            f"    PermitListen 127.0.0.1:{port}\n"
            f"    PermitOpen none\n"
            f"    AllowStreamLocalForwarding no\n"
            f"    AllowAgentForwarding no\n"
            f"    X11Forwarding no\n"
            f"    GatewayPorts no\n"
            f"    PermitTTY no\n"
            f"    MaxSessions 0\n"
            f"    ForceCommand /usr/bin/false\n")


def install(to, key, port=PORT, dry_run=False, home=None, run=subprocess.run, out=print, local_port=LOCAL_PORT):
    key = os.path.expanduser(key)
    if not os.path.isfile(key):
        raise SystemExit(f"forward: no key file at {key}")
    if "@" not in to:
        raise SystemExit("forward: --to <user>@<host>")
    path = agent_path(home)
    doc = unit(to, key, port, home, local_port)
    if dry_run:
        out(plistlib.dumps(doc).decode())
        out("on the host, the sshd block:\n" + sshd_block(to.split("@", 1)[0], port))
        pub = key + ".pub"
        if os.path.isfile(pub):
            out(f"on the host, {to.split('@', 1)[0]}'s authorized_keys: " + authorized_line(open(pub).read(), port))
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
    out(f"forward installed: {LABEL} keeps ssh -N -R 127.0.0.1:{port}:127.0.0.1:{local_port} to {to} running")
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
    ap.add_argument("--port", type=int, default=PORT, help="the port on the host (default 18200)")
    ap.add_argument("--local-port", type=int, default=LOCAL_PORT,
                    help="the server's TLS listener on this machine (default 18201)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.action == "install":
        if not (a.to and a.key):
            ap.error("install needs --to and --key")
        install(a.to, a.key, a.port, a.dry_run, local_port=a.local_port)
        return 0
    if a.action == "uninstall":
        uninstall()
        return 0
    return status()


if __name__ == "__main__":
    sys.exit(main())
