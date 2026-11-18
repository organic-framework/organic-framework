#!/usr/bin/env python3
"""The laptop's reverse forward to the agent host: the host's OF team reaches this machine's of-server through a Unix
socket, by the name https://127.0.0.1:<port> (organic-framework-build reports/agent-host-continuation-plan-2026-11-18.md, section 2b: one server for
both organizations, staying on the laptop at first). Owned by of_server.

    python3 deploy/host/forward.py keygen
    python3 deploy/host/forward.py pin-host --to <user>@<host> --fingerprint SHA256:<the host key's, read elsewhere>
    python3 deploy/host/forward.py install --to <user>@<host> [--key <private key file>] [--port 18200] [--dry-run]
    python3 deploy/host/forward.py status
    python3 deploy/host/forward.py uninstall

`keygen` makes the forward's own key in its own folder (~/.ssh/of-forward/, mode 700) and prints only its public half,
the host's authorized_keys line and sshd block, so the private key's path never appears on a command line. `pin-host`
reads the host's ed25519 key, accepts it only when its fingerprint is the one given (read over another channel), and
writes it to the forward's own known_hosts; the forward checks the host against that file alone
(StrictHostKeyChecking=yes), never the person's own known_hosts.

A user LaunchAgent (dev.organic-framework.forward) runs `ssh -N -R /srv/of-forward/server.sock:127.0.0.1:<local port>`
(the host's end a Unix socket only the OF team's user reaches, docs/contours.md §1) and launchd keeps it running: ExitOnForwardFailure and keep-alives make a dead or refused forward end, and launchd starts it again.
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
DIR = "~/.ssh/of-forward"
KEY = DIR + "/id_ed25519"
KNOWN = DIR + "/known_hosts"
PORT = 18200        # the address OF's clients name: https://127.0.0.1:18200, reached on the host through SOCKET
LOCAL_PORT = 18201  # on the laptop: the server's TLS listener (of_server.TLS_PORT)
HOST_USER = "of-forward"
SOCKET = "/srv/of-forward/server.sock"  # on the host: of-forward:of 2750, so of reaches it and no other user does


def agent_path(home=None):
    return os.path.join(home or os.path.expanduser("~"), "Library", "LaunchAgents", f"{LABEL}.plist")


def ssh_argv(to, key, port=PORT, local_port=LOCAL_PORT, known=KNOWN, socket=SOCKET):
    """The forward's ssh call: the host's end a Unix socket, never a TCP port another user of the host could reach
    or take (of_agent-server, msg 01a1f098-02bb; docs/contours.md §1); its own key, never an SSH agent, the host
    checked against the forward's own pinned known_hosts; ends on a failed forward or a dead link."""
    return ["/usr/bin/ssh", "-N", "-R", f"{socket}:127.0.0.1:{local_port}",
            "-i", os.path.expanduser(key), "-o", "IdentityAgent=none", "-o", "IdentitiesOnly=yes",
            "-o", f"UserKnownHostsFile={os.path.expanduser(known)}", "-o", "StrictHostKeyChecking=yes",
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
    """The host's authorized_keys line for the forward's key: forwarding and no command; the sshd block allows only a
    Unix-socket listen (permitlisten names TCP listens alone)."""
    return f'restrict,port-forwarding,command="/usr/bin/false" {pubkey.strip()}'


def sshd_block(user=HOST_USER, port=PORT):
    """The host's sshd Match block for the forward's user (sshd_config(5)): a remote Unix-socket listen and nothing
    else: no TCP forwarding (-L, -D, -W, -R to a port), no session; the socket at mode 0660 in its setgid folder, a
    stale one replaced. sshd limits no socket path, so the user's writable folders are the limit. A closing
    `Match all` ends it: a file under sshd_config.d is included near the top of sshd_config, and without it the main
    file's lines after the Include would apply to this user alone."""
    return (f"# /etc/ssh/sshd_config.d/of-forward.conf (docs/server-several-machines.md 4.8)\n"
            f"Match User {user}\n"
            f"    AllowTcpForwarding no\n"
            f"    AllowStreamLocalForwarding remote\n"
            f"    StreamLocalBindUnlink yes\n"
            f"    StreamLocalBindMask 0117\n"
            f"    PermitOpen none\n"
            f"    AllowAgentForwarding no\n"
            f"    X11Forwarding no\n"
            f"    GatewayPorts no\n"
            f"    PermitTTY no\n"
            f"    MaxSessions 0\n"
            f"    ForceCommand /usr/bin/false\n"
            f"Match all\n")  # ends the block, so the main config's lines after its Include are not under it


def _no_agent_env():
    env = {k: v for k, v in os.environ.items() if k != "SSH_AUTH_SOCK"}
    env["SSH_AUTH_SOCK"] = ""
    return env


def keygen(key=KEY, run=subprocess.run, out=print):
    """Make the forward's own key once (no passphrase: launchd starts it unattended) and print its public half."""
    key = os.path.expanduser(key)
    os.makedirs(os.path.dirname(key), mode=0o700, exist_ok=True)
    os.chmod(os.path.dirname(key), 0o700)
    if not os.path.exists(key):
        r = run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "of-forward", "-f", key], capture_output=True,
                text=True, env=_no_agent_env())
        if r.returncode != 0:
            raise SystemExit(f"forward: ssh-keygen failed: {(r.stderr or '').strip()[:200]}")
    with open(key + ".pub") as fh:
        pub = fh.read().strip()
    out(f"the forward's public key: {pub}")
    out("on the host, the forwarding user's authorized_keys line:\n" + authorized_line(pub))
    out("on the host, the sshd block:\n" + sshd_block())
    return pub


def pin_host(to, fingerprint, known=KNOWN, run=subprocess.run, out=print):
    """Pin the host's ed25519 key in the forward's own known_hosts, only when its fingerprint is FINGERPRINT."""
    import tempfile
    host = to.split("@", 1)[-1]
    if not fingerprint.startswith("SHA256:"):
        raise SystemExit("forward: --fingerprint SHA256:<base64>, as ssh-keygen -lf prints it")
    r = run(["ssh-keyscan", "-q", "-t", "ed25519", host], capture_output=True, text=True, timeout=30,
            env=_no_agent_env())
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip() and not ln.startswith("#")]
    if not lines:
        raise SystemExit(f"forward: {host} gave no ed25519 host key")
    with tempfile.NamedTemporaryFile("w", suffix=".pub", delete=False) as fh:
        fh.write(lines[0] + "\n")
    try:
        got = run(["ssh-keygen", "-lf", fh.name], capture_output=True, text=True, env=_no_agent_env()).stdout.split()
    finally:
        os.remove(fh.name)
    if len(got) < 2 or got[1] != fingerprint:
        raise SystemExit(f"forward: {host}'s key is {got[1] if len(got) > 1 else '?'}, not {fingerprint}: not pinned")
    known = os.path.expanduser(known)
    os.makedirs(os.path.dirname(known), mode=0o700, exist_ok=True)
    with open(known, "w") as fh:
        fh.write(lines[0] + "\n")
    os.chmod(known, 0o600)
    out(f"pinned {host}'s host key {fingerprint} in the forward's own known_hosts")
    return lines[0]


def install(to, key=KEY, port=PORT, dry_run=False, home=None, run=subprocess.run, out=print, local_port=LOCAL_PORT):
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
    out(f"forward installed: {LABEL} keeps ssh -N -R {SOCKET}:127.0.0.1:{local_port} to {to} running")
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
    ap.add_argument("action", choices=["keygen", "pin-host", "install", "uninstall", "status"])
    ap.add_argument("--to", help="<user>@<host>")
    ap.add_argument("--key", default=KEY, help="the forward's own private key file (default: keygen's)")
    ap.add_argument("--fingerprint", help="pin-host: the host key's SHA256 fingerprint, read over another channel")
    ap.add_argument("--port", type=int, default=PORT, help="the port on the host (default 18200)")
    ap.add_argument("--local-port", type=int, default=LOCAL_PORT,
                    help="the server's TLS listener on this machine (default 18201)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.action == "keygen":
        keygen(a.key)
        return 0
    if a.action == "pin-host":
        if not (a.to and a.fingerprint):
            ap.error("pin-host needs --to and --fingerprint")
        pin_host(a.to, a.fingerprint)
        return 0
    if a.action == "install":
        if not a.to:
            ap.error("install needs --to")
        install(a.to, a.key, a.port, a.dry_run, local_port=a.local_port)
        return 0
    if a.action == "uninstall":
        uninstall()
        return 0
    return status()


if __name__ == "__main__":
    sys.exit(main())
