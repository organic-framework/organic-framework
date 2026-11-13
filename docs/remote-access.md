# Remote access to the owner's view (phone, tablet)

Status: **Guide**, 2026-11-06, by `of_architect`; a separate agent host added 2026-11-14 by `of_platform` (hand-over 5de421ee). It applies to profile 1 (one machine), to a profile 2 server and to an agent host with one user per organization.

The owner works with the organization from wherever he is. herdr is built for this: its TUI adapts to narrow screens, and an SSH client on a phone attaches to the same herdr server as the desktop. The agents, the desk, the asks popup (`prefix+a`) and the side chat all work there.

## Principle

**No port is opened to the internet.** The machine is reached only over a private network of the owner's own devices (Tailscale, WireGuard-based), with SSH keys only. Passwords are not accepted. Agents keep running on the machine; the phone is only a screen and a keyboard.

## Steps (macOS host, about 10 minutes)

1. **Private network.**
   - Install Tailscale on the Mac (`brew install --cask tailscale`, or the App Store) and on the phone or iPad, and sign in with the same account on both.
   - Note the Mac's Tailscale name, for example `alexs-mac`.
2. **SSH on the Mac, keys only, and only on Tailscale.**
   - System Settings → General → Sharing → **Remote Login: on**, and allow access only for your user. Admin password required.
   - Harden `sshd` with a drop-in file `/etc/ssh/sshd_config.d/100-of.conf`:
     ```
     PasswordAuthentication no
     KbdInteractiveAuthentication no
     PermitRootLogin no
     AllowUsers alex
     ```
   - Optionally, limit SSH to the Tailscale interface with the macOS firewall, or with Tailscale ACLs (`tailscale ssh` is an alternative to `sshd`).
3. **A key on the phone or tablet.**
   - In the client, generate an Ed25519 key on the device; the private key never leaves the device.
   - Add its public half to `~/.ssh/authorized_keys` on the Mac, one line, with a comment naming the device.
   - Removing that line revokes the device.
4. **The client.**
   - iPhone: **Moshi**, which herdr's own docs recommend.
   - iPad mini: **Blink Shell**.
   - Both support **mosh**, so the session survives network changes and the phone sleeping. Install mosh on the Mac: `brew install mosh`.
5. **Attach.** From the client: `ssh alex@alexs-mac`, then `herdr`. It attaches to the running herdr server, the same one as on the desktop.
   - `Ctrl+b, a`: the asks popup.
   - `Ctrl+b, w`: the workspaces.
   - `Ctrl+b, q`: detach. The agents keep running.
6. **Keep the Mac awake** while away: System Settings → Energy, prevent sleep on power, or `caffeinate -dims` in a pane.

## A separate agent host (one user per organization)

When the agents move to their own machine (organic-framework-build reports/agent-server-migration-plan-2026-11-11.md §9), each organization's agents run as their own macOS user, for example `of` and `acme`, and the owner's admin user, `alex` here, holds no agent. Each organization user has **its own herdr server**, so no organization can type into another's panes, and its own OF plane: listener, pulse, asks popup and health, set up by `deploy/host/plane.py` (`of_platform`) beside the server side of `deploy/host/setup.py` (`of_server`). Each user's popup shows only its own organization's asks, and each organization has its own desk.

1. **SSH for three users, keys only.** In the drop-in file of step 2 above: `AllowUsers alex of acme`. Each organization user's `~/.ssh/authorized_keys` holds the owner's device keys, one line per device, as in step 3.
2. **From the laptop: two saved machines in one herdr window.**
   ```
   herdr machine add of@agents-host --label of
   herdr machine add acme@agents-host --label acme
   ```
   Both servers then show in the laptop's herdr sidebar, each with its workspaces, agents and popup. `herdr machine status` checks them without asking for a password.
3. **From the phone: one attach that shows both.** The phone attaches to `alex@agents-host` and runs `herdr`. Under `alex`, the two organizations are saved machines on the same host:
   ```
   herdr machine add of@localhost --label of
   herdr machine add acme@localhost --label acme
   ```
   They log in with a key of `alex`'s on the host, made for this alone (`ssh-keygen -t ed25519 -f ~/.ssh/agents-host-local`), whose public half is in `of`'s and `acme`'s `authorized_keys` with the comment `alex on agents-host, for herdr saved machines`. Deleting those lines cuts the phone's path to the agents without touching the laptop's.
4. **Mosh** for both paths: `brew install mosh` on the host, as for one machine.

To check on the host before the first agents move (the plan's S2, `of_platform` with the owner): the asks popup (`Ctrl+b, a`), its notifications and the desk reach the owner through a saved machine, from the laptop and from the phone; an ask filed by an agent of `of` appears in `of`'s popup and not in `acme`'s; `sudo python3 deploy/host/plane.py check --org-user …` passes.

## Studio from the phone

Studio is a web page served by `of-server` on `127.0.0.1`. To open it from the phone over the private network, there are two options:
- forward the port through SSH: `ssh -L 18200:127.0.0.1:18200 alex@alexs-mac`, then open the one-time link from `of studio`;
- or let `of-server` listen on the Tailscale interface as well, with the one-time link as the only entry. This one is to be added.

Answering asks from the web page is on the roadmap. Today, answers go through the herdr popup.

## Security checklist

- [ ] No port forwarding on the router and no public SSH: Tailscale only.
- [ ] `PasswordAuthentication no` is in force: `sudo sshd -T | grep -i passwordauthentication`.
- [ ] One key per device, each with a comment; revoke a lost device by deleting its line.
- [ ] FileVault is on.
- [ ] The phone has a passcode and biometric lock; the client app locks too.
- [ ] Agents never use the owner's SSH keys (`AGENTS.md`); remote access is for the owner only.
