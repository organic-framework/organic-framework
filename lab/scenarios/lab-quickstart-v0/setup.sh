#!/bin/sh
# lab-quickstart-v0 machine setup (docs/lab.md, section 12). Run as root in a fresh VM.
# The outsider already uses Claude Code: three agent sessions run in plain tmux
# sessions (arm "no-herdr") or in herdr panes (arm "herdr"). The agents are the stub
# harness installed as `claude`, so no credential ever enters the VM. Nothing of
# Organic Framework is installed; the repository is offered as a git remote, as if
# published, at file:///srv/organic-framework.git.
# Usage: setup.sh <framework-tarball> <arm: herdr|no-herdr>
set -eu
TAR="${1:?tarball}"; ARM="${2:?arm}"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends git tmux nftables curl ca-certificates python3 openssh-client >/dev/null
id dev >/dev/null 2>&1 || useradd -m -s /bin/bash dev
rm -f /etc/sudoers.d/dev

# The "published" repository: a bare git repo with one commit of the framework.
rm -rf /srv/src /srv/organic-framework.git
mkdir -p /srv/src && tar -xzf "$TAR" -C /srv/src
git -C /srv/src init -q -b main && git -C /srv/src add -A
git -C /srv/src -c user.name=of -c user.email=of@example.invalid commit -q -m "Organic Framework"
git clone -q --bare /srv/src /srv/organic-framework.git && rm -rf /srv/src
chmod -R a+rX /srv/organic-framework.git

# The outsider's existing setup: Claude Code (the stub) and three projects with agents.
su - dev -c 'mkdir -p ~/.local/bin ~/code/api ~/code/web ~/code/infra ~/.claude && for p in api web infra; do git -C ~/code/$p init -q; done'
install -m 755 /tmp/stub-claude /home/alex/.local/bin/claude
su - dev -c 'grep -q local/bin ~/.profile || echo "export PATH=\$HOME/.local/bin:\$PATH" >> ~/.profile'
if [ "$ARM" = herdr ]; then
  curl -fsSL -o /usr/local/bin/herdr "https://github.com/herdrdev/herdr/releases/download/v0.9.5/herdr-linux-aarch64"
  chmod 755 /usr/local/bin/herdr
fi
for p in api web infra; do
  su - dev -c "tmux new-session -d -s $p -c ~/code/$p 'PATH=\$HOME/.local/bin:\$PATH claude --name $p'"
done
echo "quickstart machine ready (arm: $ARM)"
