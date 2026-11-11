#!/bin/bash
# lab-profile2-v0 server: profile 2 on Kubernetes, as Acme's keeper would install it
# (deploy/RUNBOOK.md, option B: a dedicated ClickHouse). Runs as root inside the server VM.
# Everything secret here is generated for this disposable cluster; none of it is real.
# Usage: [NETPOL=1 CLIENT_CIDR=…] server-setup.sh <framework-tarball>   (NETPOL=1: the chart's NetworkPolicy on)
set -euo pipefail
TAR="${1:?tarball}"
ORG=lab-p2; DB=of_lab_p2; NS=of-server
T0=$(date +%s)
log() { echo "[$(( $(date +%s) - T0 ))s] $*"; }
export DEBIAN_FRONTEND=noninteractive

log "packages"
apt-get update -q >/dev/null
apt-get install -y -q --no-install-recommends docker.io python3 python3-yaml curl ca-certificates openssh-client >/dev/null
rm -rf /opt/of && mkdir -p /opt/of && tar -xzf "$TAR" -C /opt/of

log "k3s (stable channel; version recorded below)"
curl -sfL https://get.k3s.io | INSTALL_K3S_CHANNEL=stable INSTALL_K3S_EXEC="--disable traefik --write-kubeconfig-mode 644" sh - >/dev/null
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
until kubectl get nodes 2>/dev/null | grep -q " Ready"; do sleep 2; done
log "helm"
curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash >/dev/null 2>&1

log "image: build from deploy/Dockerfile inside this VM, import into k3s"
docker build -q -t of-server:lab -f /opt/of/deploy/Dockerfile /opt/of >/dev/null
docker save of-server:lab | k3s ctr images import - >/dev/null

log "secrets for this cluster only"
S=/root/lab-secrets; mkdir -p -m 700 $S
[ -f $S/secret.key ] || for u in admin ${DB}_ingest ${DB}_vault_ingest ${DB}_unassigned_ingest ${DB}_redact ${DB}_vault_redact ${DB}_unassigned_redact ${DB}_read; do
  python3 -c 'import secrets; print(secrets.token_urlsafe(24))' > $S/$u.password
done
[ -f $S/secret.key ] || python3 -c 'import secrets; print(secrets.token_hex(32))' > $S/secret.key
[ -f $S/grant ] || ssh-keygen -q -t ed25519 -N "" -C lab-grant -f $S/grant >/dev/null
chmod 600 $S/*

log "ClickHouse (dedicated, option B)"
kubectl create namespace clickhouse --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl -n clickhouse create secret generic ch-admin --from-literal=password="$(cat $S/admin.password)" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl apply -f - >/dev/null <<YAML
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: clickhouse-data, namespace: clickhouse}
spec: {accessModes: [ReadWriteOnce], storageClassName: local-path, resources: {requests: {storage: 5Gi}}}
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: clickhouse, namespace: clickhouse}
spec:
  replicas: 1
  selector: {matchLabels: {app: clickhouse}}
  template:
    metadata: {labels: {app: clickhouse}}
    spec:
      containers:
      - name: clickhouse
        image: clickhouse/clickhouse-server:25.8
        env:
        - {name: CLICKHOUSE_USER, value: admin}
        - {name: CLICKHOUSE_PASSWORD, valueFrom: {secretKeyRef: {name: ch-admin, key: password}}}
        - {name: CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT, value: "1"}
        ports: [{containerPort: 8123}]
        resources: {limits: {memory: 1536Mi}}
        volumeMounts: [{name: data, mountPath: /var/lib/clickhouse}]
      volumes: [{name: data, persistentVolumeClaim: {claimName: clickhouse-data}}]
  strategy: {type: Recreate}
---
apiVersion: v1
kind: Service
metadata: {name: clickhouse, namespace: clickhouse}
spec: {selector: {app: clickhouse}, ports: [{port: 8123, targetPort: 8123}]}
YAML
kubectl -n clickhouse rollout status deploy/clickhouse --timeout=600s >/dev/null
until kubectl -n clickhouse exec deploy/clickhouse -- clickhouse-client --user admin --password "$(cat $S/admin.password)" -q "select 1" >/dev/null 2>&1; do sleep 2; done
python3 /opt/of/deploy/clickhouse/schema.py --org $DB --vault ${DB}_vault --unassigned ${DB}_unassigned --password-dir $S > $S/schema.sql
kubectl -n clickhouse exec -i deploy/clickhouse -- clickhouse-client --user admin --password "$(cat $S/admin.password)" --multiquery < $S/schema.sql

log "of-server from the chart"
python3 - "$S" "$DB" "$ORG" > $S/values.yaml <<'PY'
import sys, yaml, os
S, DB, ORG = sys.argv[1:4]
r = lambda n: open(os.path.join(S, n)).read().strip()
v = {"image": {"repository": "of-server", "tag": "lab", "pullPolicy": "Never"},
     "orgs": {ORG: DB}, "vaultDatabase": DB + "_vault", "unassignedDatabase": DB + "_unassigned",
     "tenant": "lab", "rawToOrg": False,
     "clickhouse": {"url": "http://clickhouse.clickhouse.svc:8123", "user": DB + "_ingest"},
     "trustedGrantKeys": [r("grant.pub")],
     "secretValues": {"secret.key": r("secret.key"),
                      **{f"{u}.password": r(f"{u}.password") for u in
                         (DB + "_ingest", DB + "_vault_ingest", DB + "_unassigned_ingest", DB + "_read")}},
     "service": {"type": "NodePort", "port": 18200, "nodePort": 31820},
     "networkPolicy": ({"enabled": True,
                        "ingressFrom": [{"ipBlock": {"cidr": os.environ.get("CLIENT_CIDR", "192.168.104.0/24")}}]}
                       if os.environ.get("NETPOL") == "1" else {"enabled": False})}
print(yaml.safe_dump(v))
PY
chmod 600 $S/values.yaml
helm upgrade --install of-server /opt/of/deploy/helm/of-server -n $NS --create-namespace -f $S/values.yaml >/dev/null
kubectl -n $NS rollout status deploy --timeout=300s >/dev/null
until curl -sf http://127.0.0.1:31820/v1/health >/dev/null; do sleep 2; done
log "ready: $(curl -s http://127.0.0.1:31820/v1/health)"
echo "versions: k3s $(k3s --version | head -1 | awk '{print $3}'), helm $(helm version --short), clickhouse 25.8, image of-server:lab"
echo "install_seconds: $(( $(date +%s) - T0 ))"
