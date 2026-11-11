#!/bin/sh
# Provision a lab machine (run as root inside the VM, before any agent exists).
# Pinned versions: every run's manifest records them (docs/lab.md, section 1).
# Usage: provision.sh <framework-tarball>
set -eu

HERDR_VERSION="${HERDR_VERSION:-0.9.5}"
CLAUDE_VERSION="${CLAUDE_VERSION:-2.1.318}"
PYTHON_VERSION="${PYTHON_VERSION:-3.14}"
FRAMEWORK_TAR="${1:?framework tarball}"

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends \
  git sqlite3 nftables curl ca-certificates jq tmux zstd python3-yaml

# The lab user: agents run here, without sudo.
id lab >/dev/null 2>&1 || useradd -m -s /bin/bash lab
rm -f /etc/sudoers.d/lab

# herdr, pinned release binary. The GitHub release CDN 504s intermittently (2026-11-08); a pre-placed binary of the
# pinned version (e.g. copied from another lab VM, or staged at /tmp/herdr before provisioning) is used as is, so a
# flaky download does not fail the whole provision. Delete it to force a fresh download.
if [ "$(/usr/local/bin/herdr --version 2>/dev/null | awk '{print $2}')" != "${HERDR_VERSION}" ]; then
  if [ -x /tmp/herdr ]; then
    install -m755 /tmp/herdr /usr/local/bin/herdr
  else
    curl -fsSL --retry 3 --retry-delay 5 -o /usr/local/bin/herdr \
      "https://github.com/herdrdev/herdr/releases/download/v${HERDR_VERSION}/herdr-linux-aarch64"
    chmod 755 /usr/local/bin/herdr
  fi
fi

# Python for of (zstd needs 3.14+), through uv, for the lab user.
curl -fsSL https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
su - lab -c "uv python install ${PYTHON_VERSION} && uv venv --clear --python ${PYTHON_VERSION} ~/.venv-of && uv pip install -q --python ~/.venv-of/bin/python pyyaml"

# Claude Code, pinned, native installer, for the lab user; no autoupdate.
su - lab -c "curl -fsSL https://claude.ai/install.sh | bash -s ${CLAUDE_VERSION}"

# The framework at the run's commit.
su - lab -c "mkdir -p ~/organic-framework && tar -xzf '${FRAMEWORK_TAR}' -C ~/organic-framework"
su - lab -c "mkdir -p ~/.local/bin && ln -sf ~/organic-framework/bin/of ~/.local/bin/of"
# of runs under the lab venv's Python.
su - lab -c "sed -i '1s|.*|#!/home/lab/.venv-of/bin/python|' ~/organic-framework/bin/of"

# Versions for the manifest.
{
  echo "herdr: $(/usr/local/bin/herdr --version 2>/dev/null | awk '{print $2}')"
  echo "claude-code: $(su - lab -c '~/.local/bin/claude --version' 2>/dev/null | awk '{print $1}')"
  echo "python: $(su - lab -c '~/.venv-of/bin/python --version' | awk '{print $2}')"
  echo "os: $(. /etc/os-release && echo "$ID $VERSION_ID") $(uname -r)"
} > /etc/of-lab-versions
cat /etc/of-lab-versions
