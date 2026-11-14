#!/usr/bin/env bash
# lab-agent-host-v0 setup: provision the two organization users and the shared framework tree the B5
# rehearsal needs on one Linux VM. It stands up the OS-level pieces only; check.py builds the synthetic
# session, the bundle and the carry, and asserts A1..A6 + A3b.
#
# Two OS users stand in for the laptop and the host: alex (uid 1001) and of (uid 1002), different homes and
# uids, so of_carry's home-and-key-and-uid rewrite is exercised for real (/home/alex -> /home/of). The copy
# leg (A2) runs rsync over loopback SSH from alex to of with an explicit key, the owner's agent refused.
#
# Run inside the VM as a user with sudo:  sudo bash setup.sh
# A fresh framework tree is taken from /tmp/of-head.tar when present (git archive HEAD, copied in), else the
# existing /usr/local/of-cur is kept. Idempotent: re-running only fills what is missing.
set -euo pipefail

TREE=/usr/local/of-cur
TAR=/tmp/of-head.tar
ALEX=alex; OF=of
ALEX_UID=1001; OF_UID=1002

say() { printf '  %s\n' "$*"; }

if [ "$(id -u)" != 0 ]; then echo "setup.sh must run as root (sudo bash setup.sh)"; exit 2; fi

# 1. the shared framework tree, world-readable (both users run of_carry from it; the rewrite is keyed on the
#    session's homes, never on where the code lives, so one neutral copy serves both).
if [ -f "$TAR" ]; then
  rm -rf "$TREE"; mkdir -p "$TREE"; tar -xf "$TAR" -C "$TREE"
  chmod -R a+rX "$TREE"
  say "framework tree extracted to $TREE from $TAR"
elif [ -d "$TREE/bin" ]; then
  say "framework tree kept at $TREE (no fresh $TAR)"
else
  echo "no framework tree: put a git-archive at $TAR or populate $TREE/bin"; exit 3
fi
test -f "$TREE/bin/of_carry.py" || { echo "no of_carry.py under $TREE/bin"; exit 3; }

# 2. the two organization users.
for spec in "$ALEX:$ALEX_UID" "$OF:$OF_UID"; do
  u=${spec%:*}; uid=${spec#*:}
  if id "$u" >/dev/null 2>&1; then
    say "user $u exists (uid $(id -u "$u"))"
  else
    useradd -m -u "$uid" -s /bin/bash "$u"
    say "created user $u (uid $uid, home /home/$u)"
  fi
  install -d -m 700 -o "$u" -g "$u" "/home/$u/.ssh"
done

# 3. the copy leg's keys, both directions: each user holds its own private key, the other trusts its public
#    half, so a carry runs either way (forward alex->of and the rollback of->alex). Explicit key file, never
#    the owner's SSH agent (AGENTS.md); mode 600.
for src in "$ALEX" "$OF"; do
  KEY=/home/$src/.ssh/carry_ed25519
  if [ ! -f "$KEY" ]; then
    sudo -u "$src" ssh-keygen -t ed25519 -N '' -f "$KEY" -C "lab-carry-$src" >/dev/null
    say "generated copy-leg key $KEY"
  fi
  chmod 600 "$KEY"; chown "$src:$src" "$KEY" "$KEY.pub"
  for dst in "$ALEX" "$OF"; do
    [ "$dst" = "$src" ] && continue
    AUTH=/home/$dst/.ssh/authorized_keys
    touch "$AUTH"; chmod 600 "$AUTH"; chown "$dst:$dst" "$AUTH"
    if ! grep -qF "$(cat "$KEY.pub")" "$AUTH" 2>/dev/null; then
      cat "$KEY.pub" >> "$AUTH"
      say "installed $src's public key in $AUTH"
    fi
  done
  # loopback host key known to this user, so BatchMode ssh does not stop on the prompt.
  KNOWN=/home/$src/.ssh/known_hosts
  touch "$KNOWN"; chmod 600 "$KNOWN"; chown "$src:$src" "$KNOWN"
  for h in localhost 127.0.0.1; do
    sudo -u "$src" ssh-keygen -F "$h" -f "$KNOWN" >/dev/null 2>&1 || \
      ssh-keyscan -t ed25519 "$h" 2>/dev/null >> "$KNOWN" || true
  done
  chown "$src:$src" "$KNOWN"
done

systemctl is-active ssh >/dev/null 2>&1 || systemctl is-active sshd >/dev/null 2>&1 || \
  say "WARNING: no active ssh service; the copy leg (A2) needs sshd"

say "setup done: users $ALEX/$OF, tree $TREE, copy key $KEY"
