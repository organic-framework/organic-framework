#!/bin/bash
# lab-profile2-v0 client: a machine of the owner or a colleague, shipping to the team's
# of-server. Runs as `lab` inside a client VM. Prints "MACHINE <id> <pubkey line>".
# Usage: client-setup.sh <owner|colleague> <server_url>
set -euo pipefail
ROLE="${1:?role}"; URL="${2:?server url}"
unset SSH_AUTH_SOCK; export OF_AUTOMATION=1 PATH="$HOME/.local/bin:$PATH"
OF="$HOME/organic-framework/bin/of"
rm -rf "$HOME/.config/of" "$HOME/.local/state/of" "$HOME/of" "$HOME/.claude" "$HOME/work"
mkdir -p "$HOME/.config/of" "$HOME/of/lab-p2/registry" "$HOME/of/lab-p2-other/registry" "$HOME/work" "$HOME/other" "$HOME/.claude"
echo '{}' > "$HOME/.claude/settings.json"
install -m 755 "$HOME/organic-framework/lab/stubs/claude" "$HOME/.local/bin/claude"
sed -i "1s|.*|#!$HOME/.venv-of/bin/python|" "$HOME/.local/bin/claude"
# The shared registry: one position per person. Session ids are fixed so routing is exact.
cat > "$HOME/of/lab-p2/registry/positions.yaml" <<'EOF'
tenant: lab
organization: lab-p2
positions:
  - {name: lab_owner, system: lab, role: owner, kind: project, throughput: raise, owner: founder, summary: "owner's agent", status: active, holder: {runtime: claude-code, session: "11111111-1111-4111-8111-111111111111"}, aliases: []}
  - {name: lab_colleague, system: lab, role: colleague, kind: project, throughput: raise, owner: founder, summary: "colleague's agent", status: active, holder: {runtime: claude-code, session: "22222222-2222-4222-8222-222222222222"}, aliases: []}
EOF
# An organization this machine did not join on the server (the authorization check).
cat > "$HOME/of/lab-p2-other/registry/positions.yaml" <<'EOF'
tenant: lab
organization: lab-p2-other
positions:
  - {name: other_agent, system: other, role: agent, kind: project, throughput: raise, owner: founder, summary: "not joined", status: active, holder: {runtime: claude-code, session: "33333333-3333-4333-8333-333333333333"}, aliases: []}
EOF
cat > "$HOME/.config/of/orgs.yaml" <<EOF
orgs:
  - name: lab-p2
    path: ~/of/lab-p2
    database: of_lab_p2
    server: $URL
    paths: [~/work]
  - name: lab-p2-other
    path: ~/of/lab-p2-other
    database: of_lab_p2_other
    server: $URL
    paths: [~/other]
EOF
printf 'mode: server\nserver_url: %s\ntenant: lab\n' "$URL" > "$HOME/.config/of/ship.yaml"
ssh-keygen -q -t ed25519 -N "" -C "lab-$ROLE" -f "$HOME/.config/of/machine_ed25519" <<< y >/dev/null 2>&1 || true
"$OF" hooks install --config-dir "$HOME/.claude" --yes >/dev/null
MID=$(cd "$HOME/organic-framework/bin" && "$HOME/.venv-of/bin/python" -c 'import of_events; print(of_events.machine_id())')
echo "MACHINE $MID $(cat "$HOME/.config/of/machine_ed25519.pub")"
