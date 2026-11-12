# lab-secrets-broker-v0

Proves on **two lab machines** what docs/agent-secrets.md §10 claims: a position's hand-over gives the new holder
access to its credential and the old holder none, with no step in 1Password. Domain and the broker: `of_server`
(built; of_server's hand-over d7a1354c). Harness: `of_lab`. Single-host unit version:
tests/test_secrets_broker.py (the FAKE_OP lives there; this reuses it across machines).

## Setup (three Lima VMs on the user-v2 net)

- **server** (`server-setup.sh`): a plain `of server run` (ClickHouse is optional) with the broker, the fake op
  (`fake-op` + `db.json`; enforces each token's vaults, logs every read to reads.log, never a value), the
  per-position service-account tokens under `tokens/t/<position>.token` **only here**, and the `t` org registry
  (`t-registry.yaml`: `obs_reader` holds the credential `grafana-viewer`, ref `op://t-obs/grafana-viewer/credential`,
  with `holder.session`).
- **client A** and **B** (`client-setup.sh <role> <session> <url>`): enrolled for `t`, each with a local `t`
  registry so `of secret run` resolves its session to `obs_reader`. `present.py` publishes a machine's presence for
  a position+session (the broker checks presence and the registry's holder.session at every use).

## Steps (each a pass/fail), 0–4; 5–6 wait for the next build and the founder's card

| # | Check | Result |
|---|---|---|
| 0 | 1Password's boundary holds without the broker: obs_reader's token cannot read a k8s vault | **PASS** |
| 1 | obs_reader held on A: `of secret run grafana-viewer` succeeds on A, refused on B | **PASS** |
| 2 | hand obs_reader to B (registry holder.session, B presents it): within ~10 s, refused on A and succeeds on B | **PASS** |
| 3 | no service-account token on A or B (files and environment) | **PASS** |
| 4 | server.db `secret_uses` shows A allowed, A refused after the hand-over, B allowed, and no value; the fake op's log shows only the broker's reads | **PASS** |

Run: bring up three VMs from lab/image/lab-net.yaml; on the server run `server-setup.sh` then
`of server enroll-machine` for A and B; on A and B run `client-setup.sh`; then `step0.sh` (server),
`present.py` + `of secret run` on A and B, the hand-over (edit the server registry's holder.session + B presents),
`step3.sh` (A and B), `step4.sh` (server).

## Notes

- Presence is published directly with `present.py` for the proof; in a live system the relay publishes it from a
  held position. The broker checks **both** presence and the registry's `holder.session`, so the old holder is
  refused the instant the registry names the new one.
- Steps 5 (a rotation filed for the keeper on a change of person) and 6 (a real 1Password test vault, the founder's
  hands once) wait for the next build and his card; they are out of this run.
