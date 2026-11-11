#!/bin/bash
# lab-secrets-broker-v0 server (docs/agent-secrets.md §10). Runs as the lab user on the server VM: the fake op
# (stands for 1Password; enforces each token's vaults, logs every read, never a value), the per-position
# service-account tokens (ONLY here, never on A or B), the test org `t` registry, and a plain of-server with the
# broker. Usage: server-setup.sh   (reads fake-op, db.json, t-registry.yaml from the same dir)
set -eu; H=/home/lab; D="$(cd "$(dirname "$0")" && pwd)"
export PATH="$H/.local/bin:$PATH" OF_AUTOMATION=1
mkdir -p "$H/op" "$H/tokens/t" "$H/server-state" "$H/.config/of"
install -m755 "$D/fake-op" "$H/op/op"; cp "$D/db.json" "$H/op/db.json"; cp "$D/t-registry.yaml" "$H/t-registry.yaml"
printf tok-reader > "$H/tokens/t/obs_reader.token"
printf tok-keeper > "$H/tokens/t/obs_keeper.token"
printf tok-k8s   > "$H/tokens/t/k8s_keeper.token"
chmod 600 "$H"/tokens/t/*.token
cat > "$H/.config/of/server.yaml" <<YAML
state_dir: /home/lab/server-state
host: 0.0.0.0
port: 18200
orgs: {t: db_t}
positions_files: {t: /home/lab/t-registry.yaml}
secrets: {tokens_dir: /home/lab/tokens, op: /home/lab/op/op}
tenant: x
YAML
export OF_SERVER_CONFIG="$H/.config/of/server.yaml"
setsid "$H/organic-framework/bin/of" server run </dev/null >"$H/server.log" 2>&1 &
sleep 6; curl -s --max-time 4 http://127.0.0.1:18200/v1/health | head -c 40; echo " (of-server up)"
