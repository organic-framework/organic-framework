# Runbook: of-server for one organization (profile 2)

Owner of the code: `of_server`. Who deploys: the organization's cluster keeper (for Acme, `k8s_keeper`), **only with the founder's yes**. `of_server` never acts on an organization's cluster. Frame: [`../docs/server.md`](../docs/server.md).

What runs: one `of-server` pod (one replica, a disk queue on a volume), its ClickHouse databases, and an exposure through Cloudflare. Machines enrolled into it ship their records for this organization over HTTPS. The server masks secrets before anything reaches the organization's database; unmasked raw goes only to the vault, which only the tenant reads.

## 1. Decide: where ClickHouse runs

| | A. Databases and users on the organization's existing ClickHouse | B. A small dedicated ClickHouse next to `of-server` |
|---|---|---|
| Cost | none new | one pod, about 1 GB RAM, a 20–50 GB volume |
| Isolation | the existing cluster's administrators can read the vault, which holds unmasked secrets | only the tenant holds the admin credentials |
| Blast radius | a heavy query or a burst of ingest shares the production instance | separate |
| Operations | the keeper's existing backups and monitoring | one more thing to back up |

**Recommendation: B.** The vault holds every secret that ever reached a transcript, unmasked; on a shared instance its readers are whoever administers that instance. With A, keep the vault elsewhere (for example on the tenant's own machine) and put only the organization's database and the unassigned one on the shared instance.

For B, `deploy/clickhouse/clickhouse.yaml` is the reference: one pod, `Recreate`, a persistent volume (ClickHouse loses everything without one), 1–1.5 GiB of memory. The Lab ran it on k3s for `lab-profile2-v0` (2026-11-06).

Either way, the schema comes from `deploy/clickhouse/schema.py`:

```sh
python3 deploy/clickhouse/schema.py --org of_acme --vault of_acme_vault \
    --unassigned of_acme_unassigned --password-dir <dir with <user>.password files> > schema.sql
clickhouse-client --user <admin> --multiquery < schema.sql
```

It creates, per database, the tables `event`, `transcript_record`, `hook_record`, `runtime_record` (ReplacingMergeTree on the natural keys), an insert-only user `<db>_ingest`, a delete-only user `<db>_redact` for redaction, and for the organization's database the views `v_*` without raw content and a read-only user `<db>_read` for colleagues. Users get `sha256_hash` with a salt; no password is printed. The password directory must hold a `<user>.password` file for every one of these users (the `_ingest`, `_redact` and `_read` keys in section 2, seven for one organization); `schema.py` refuses and names the missing ones, rather than leaving a GRANT to fail halfway (found by the Lab's timed clean install, 2026-11-07).

## 2. Secrets

One Kubernetes Secret, made by the keeper from the secret manager (`existingSecret` in the values). **Make every value without a trailing newline** (`--from-literal=key="$(cat file)"`, or `printf %s`): `kubectl create secret --from-file` keeps the newline, of-server strips it, and ClickHouse then sees a different password than of-server sends (found by the Lab). Keys:

| Key | What | From |
|---|---|---|
| `secret.key` | the derived masking key (hex) | the tenant runs `of server export-secret-key <file>` on the tenant's machine and hands the file over through the secret manager |
| `of_acme_ingest.password` | | the secret manager |
| `of_acme_vault_ingest.password` | | |
| `of_acme_unassigned_ingest.password` | | |
| `of_acme_read.password` | Studio reads the organization's views (`v_*`, no raw content) as this user | the secret manager |
| `of_acme_redact.password`, `of_acme_vault_redact.password`, `of_acme_unassigned_redact.password` | delete-only, used only by redaction (`POST /v1/redactions`, docs/server.md); the `_ingest` users stay insert-only | the secret manager |

The server never receives the tenant's master key or the grant private key. Without the master it cannot accept legacy HMAC grants or forge any grant.

Grants: the tenant's grant **public** key goes into the values as `trustedGrantKeys` (pinned). The organization's `registry/grants.yaml` is read from a read-only checkout kept by the `git-sync` sidecar (`grants.gitSync`: a read-only deploy key and `known_hosts` in a Secret).

## 3. Image

Built in CI from `deploy/Dockerfile` (Python 3.14 slim, Debian's `ssh-keygen` for signature checks, PyYAML, no pip; runs as uid 10001 with a read-only root filesystem), by `.github/workflows/of-server-image.yml` in the framework repository, on every change to `main`; a pull request builds and checks it without pushing. Never built on the founder's Mac. CI imports every module the image holds and checks that pip is gone, that the copied OpenSSH files are the ones the build stage's dpkg lists (package, version, the MD5 in its file list, the SHA-256 in the SBOM) and that no OpenSSH package or other OpenSSH program is in the image, and that Trivy takes the SBOM's packages as packages of the image's operating system, so Debian's advisories are matched to `openssh-client` at its version; the build itself stops unless `ssh-keygen` signs and verifies. Then it pushes; a separate read-only job scans the pushed digest with Trivy and puts the digest and the table of high and critical findings in the run's summary, the scan result to hand to an organization that copies the image.

The image holds `ssh-keygen` alone, not the OpenSSH package (2026-11-12): the server only verifies signatures, so the build copies `ssh-keygen` and the libraries it loads that the runtime image lacks from a build stage where `openssh-client` is installed, and writes `/usr/share/of-server/sbom/ssh-keygen.cdx.json` (CycloneDX) from that stage's dpkg records with `deploy/copied_sbom.py`, never by hand. A scanner that reads SBOMs in an image (Trivy does) still sees `openssh-client` and its version, so later OpenSSH findings match it; a scanner that reads only the package list does not. A user organization's security keeper accepted this on two conditions, the SBOM from dpkg's records and the CI check of the copies' hashes (msg 01a1cf70; dpkg's file list holds MD5, accepted as the anchor in msg 01a1cf80); without them the package stays. CVE-2026-123158 (on CISA's KEV list) is in `sshd`'s password check on musl libc without PAM, code this image never held. Debian's security updates are applied at each build.

- **The framework's registry**: `ghcr.io/organic-framework/of-server`, built by the framework repository's CI on every change to `main`, one tag per build (the commit's first 12 characters), plus the version and `main`. Each image carries the label `org.opencontainers.image.revision` with the full commit, for an organization that tags its copy by it.
- **The organization's own registry** (recommended): the organization's own CI copies the image by digest into its registry, or builds it from a pinned commit of the framework repository. The framework's CI names no organization's account: in a public repository a workflow step's environment, repository variables included, is printed in public logs. While the framework's package is private, pulling it from outside GitHub needs a person's classic token with `read:packages` (GitHub Docs, "Working with the Container registry"), so a cluster should not pull from it directly.
- Pin the **digest** in the values: the chart writes `<image.repository>:<image.tag>`, so `image.tag: "<tag>@sha256:…"` pins it.

## 4. Install

```sh
helm upgrade --install of-server deploy/helm/of-server -n of-server --create-namespace \
  -f values-acme.yaml
```

`values-acme.yaml` sets at least: `image.repository`, `image.tag`, `orgs`, `tenant`, `vaultDatabase`, `unassignedDatabase`, `clickhouse.url`, `existingSecret`, `trustedGrantKeys`, `grants.gitSync.*`, `networkPolicy.*` (the tunnel connector's namespace and pod labels, ClickHouse's, the cluster's DNS; §4.1).

**Grants and the registry** reach the server one of two ways: `grants.gitSync.*` checks the organization's repository out read-only, or, for a repository without a remote, `grants.configMap: {name, keys: {<org>: {grants: <key>, positions: <key>}}}` mounts a ConfigMap that the organization's keeper fills from `registry/grants.yaml` and `registry/positions.yaml` with a committed script, rerun when they change. Never both. Without either, the server refuses finance facts and new messages that need a grant.

Exposure: point the Cloudflare tunnel at `http://of-server-of-server.of-server.svc:18200`, path prefix `/v1/`, behind Cloudflare Access if the organization uses it for machines (a service token per machine, which the machine names in its `orgs.yaml` as `server_access`, docs/registry.md), or enable the chart's Ingress. `/v1/health` answers only `{ok, version}` here.

Sizing: requests 100m CPU and 256 Mi, limits 1 CPU and 1 Gi. Masking costs about 5 CPU-minutes per GB of transcripts.

### 4.1 Isolation: no other pod reaches the server or ClickHouse

The founder, 2026-11-14 (prompt 01a1d97e-d730): "We need a plan for running the server in our cluster now, but safely, so that other containers cannot reach the DB and the server."

- **The server** (`networkPolicy` in the values, on by default; keep it on). In: only port 18200, only from `ingressFrom`, the tunnel connector's or the ingress controller's pods, named by namespace and pod labels in one entry; the chart refuses an entry that names a namespace alone, which would admit every pod in it. Out: only `clickhouseTo` on 8123, `dnsTo` on 53 and, with git-sync, `gitSyncTo` on 22 and 443 (by default any address outside the private and link-local ranges, so no pod, node or cloud metadata service). A policy covers the whole pod: with git-sync the server's own container may open 22 and 443 to the same addresses; `grants.configMap` avoids that.
- **ClickHouse** (`deploy/clickhouse/clickhouse.yaml`): `default-deny` on every pod of its namespace, and `clickhouse`: in only from of-server's pods on 8123 (the manifest names the namespace `of-server`; edit it if yours differs), out only to the cluster's DNS. Its other ports (9000, 9004, 9005, 9009) take nothing. A keeper who keeps these policies in their own manifests keeps the same effect; the check below tests the effect, not the names.
- **DNS**: by default the `kube-dns` pods in `kube-system`. A cluster that resolves through a node-local cache on a link-local address adds that address to `dnsTo` as an `ipBlock` (and to ClickHouse's egress).
- **The cluster's network plugin must enforce NetworkPolicy.** A plugin that does not still accepts the objects and then ignores them, so nothing above holds: flannel alone, for example, or a managed cluster where policy enforcement is not switched on. The check proves enforcement in the cluster itself.
- **The kubelet's readiness probes** come from the node; the common plugins admit them. If a pod goes unready once the policies apply, the plugin blocks them: admit the nodes' addresses.

**Prove it** after every install, and after every change to a policy, the network plugin or the namespaces:

```sh
python3 deploy/netcheck.py --namespace of-server --release of-server --clickhouse-namespace clickhouse \
    --connector-namespace <the tunnel's namespace> --deployer <Kind:name of whoever deploys>
```

It makes a scratch namespace and removes it after. There, a probe pod must reach an open target and be refused by a target behind a deny-all policy: a cluster that does not enforce NetworkPolicy fails here. The same probe, a stranger pod in the server's namespace and one in the connector's namespace must be refused by the server (its Service and pods) and by ClickHouse (its Service, and its pods on every port). From inside the server's container, ClickHouse must answer and the scratch target and the API server must not. The probes run the server's own image (python3); `--image` names another the cluster can pull. `--clickhouse-service` and `--clickhouse-selector` name a ClickHouse that is not the reference manifest's; `--clickhouse-namespace none` skips it when it runs outside the cluster. Exit 0 only when every line passes; nothing secret is read.

**Secrets: only the deployer reads them.** In the server's and ClickHouse's namespaces, reading a Secret includes three rights: get, list or watch on Secrets; creating a pod or any workload (a pod mounts any Secret of its namespace); exec or attach into a pod (the server's pod holds its Secret mounted). Grant none of them to anyone but the deployer, and check the cluster-wide bindings as well: a ClusterRoleBinding to `edit` or `admin` reaches every namespace. The server's pod has no API token (`automountServiceAccountToken: false`), and its ServiceAccount needs no binding. The same check lists everyone RBAC gives one of the three rights, outside the API server's bootstrap bindings, and fails unless each is named with `--deployer` or `--accept`. It does not follow impersonation or role escalation (`impersonate`, `bind`, `escalate`): grant those to nobody here.

## 5. Enroll machines

**The tenant's machine first, once.** The founder's machine is the only one enrolled by hand, as the tenant (it may invite and revoke). Its public key is public; the person sends it:

```sh
kubectl -n of-server exec deploy/of-server-of-server -c of-server -- python3 /app/of_server.py \
  --config /etc/of/server.yaml enroll --tenant --machine-id <id> --pubkey '<ssh-ed25519 AAAA…>' --org acme
```

On that machine, `orgs.yaml` gets `server: https://<host>` for the organization.

**Every other machine by invitation** (docs/server.md, Machines and consent). Nobody touches the server host:

1. The tenant's machine makes a single-use code for one person and organization: `of invite --server https://<host> --org acme --person <id> [--raw-allowed acme]`. `--person` is the colleague's id in the organization's registry; without it the code enrolls another machine of the tenant. The code expires after 72 hours (`--ttl`, 7 days at most).
2. The code reaches the person through the secret manager or a direct message, never a ticket or a shared channel.
3. On the new machine: `of enroll https://<host> --code <code> --org acme`. It asks whether raw leaves the machine (default no; `--raw acme` only within the invite's `--raw-allowed`) and records the consent as `of.consent`.

Revoke a machine or every machine of a person: `of_server.py revoke --machine-id <id>` or `--person <id>` on the server host, or `DELETE /v1/machines…` from the tenant's machine. Their tokens stop working at once.

## 6. Verify

- `kubectl -n of-server get pods`: ready.
- `python3 deploy/netcheck.py …` (§4.1): every line passes.
- From an enrolled machine: `of shipped` shows receipts moving from `queued` to `committed`.
- In ClickHouse (tenant): rows in `of_acme.event`; no `[secret:` value missing where a test secret was sent; one `of.secret_detected` per test secret in `of_acme_vault.event` and none in `of_acme.event`.

## 7. Operate

- **Upgrade**: `helm upgrade` with a new tag. The strategy is Recreate: the pod stops, the queue stays on the volume, the new pod drains it. Clients retry; nothing is lost.
- **ClickHouse down**: batches stay `queued` on the volume and are retried in order. A batch that fails 8 times becomes `dead`: it is kept in `/data/server/dead/` and an `server_alert` event is written. Once the cause is fixed, queue them again: `kubectl -n of-server exec deploy/of-server-of-server -c of-server -- python3 /app/of_server.py --config /etc/of/server.yaml replay-dead [--batch-id ID]`.
- **Backups**: ClickHouse as the keeper backs up the instance. The volume holds `server.db` (machines, receipts, sightings) and the queue; snapshot it with the cluster's volume snapshots.
- **Uninstall**: `helm uninstall` keeps the volume (`helm.sh/resource-policy: keep`); delete it by hand only once its queue is empty.

## 8. Never

- Never set `rawToOrg: true` before `lab-secrets-v0` passes and the founder agrees.
- Never put the tenant master key or the grant private key in the cluster.
- Never print a secret value in a log, a ticket or a chat.
