#!/bin/bash
# $1 = role (a|b), $2 = obs_reader session, $3 = server url. Explicit /home/lab paths (no ~ expansion).
set -eu
ROLE="$1"; SESS="$2"; URL="$3"; H=/home/lab
export PATH="$H/.local/bin:$PATH"; unset SSH_AUTH_SOCK
rm -rf "$H/.config/of" "$H/.local/state/of" "$H/of"
mkdir -p "$H/.config/of" "$H/of/t/registry" "$H/work"
cat > "$H/.config/of/orgs.yaml" <<YAML
orgs:
  - name: t
    path: ~/of/t
    database: db_t
    server: $URL
    paths: [~/work]
YAML
printf 'mode: server\nserver_url: %s\ntenant: x\n' "$URL" > "$H/.config/of/ship.yaml"
cat > "$H/of/t/registry/positions.yaml" <<YAML
organization: t
credentials:
  grafana-viewer: {ref: "op://t-obs/grafana-viewer/credential", keeper: obs_keeper}
  kube-admin: {ref: "op://t-k8s/kube-admin/credential", keeper: k8s_keeper}
positions:
  - {name: obs_reader, credentials: [grafana-viewer], holder: {session: "$SESS"}}
  - {name: obs_keeper, holder: {session: "S-keeper"}}
  - {name: k8s_keeper, holder: {session: "S-k8s"}}
YAML
ssh-keygen -q -t ed25519 -N "" -C "t-$ROLE" -f "$H/.config/of/machine_ed25519" <<< y >/dev/null 2>&1 || true
MID=$(cd "$H/organic-framework/bin" && "$H/.venv-of/bin/python" -c "import of_events; print(of_events.machine_id())")
echo "MACHINE $MID $(cat "$H/.config/of/machine_ed25519.pub")"
