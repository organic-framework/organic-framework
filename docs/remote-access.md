# Remote access to the owner's view (phone, tablet)

Status: **Guide**, 2026-11-06, by `of_architect`. It applies to profile 1 (one machine) and to a profile 2 server.

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
