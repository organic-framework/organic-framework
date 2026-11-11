# of-server for an organization on several machines: plan

Status: **plan**, 2026-11-10, by `of_server` for hand-over `55a18667` from `of_conductor`. The points marked **API** or **schema** go to `of_architect` before they are built (brief: announce API and schema changes first). The first organization's deployment waits for the founder's go, asked as one card with this plan (ask `9d4b56f4`). Frame: [`server.md`](server.md), [`../deploy/RUNBOOK.md`](../deploy/RUNBOOK.md), RFC 0001 §5.8 (Connect) and phase 4 of its rollout.

## 1. Target

An organization whose positions are held by agents and people on several machines works as it does today on the founder's Mac:
- a message to a position reaches its holder wherever it runs;
- an ask any agent files reaches the person it is for (the founder, or the owner of a delegated unit) on that person's machine;
- hand-overs, waits and nudges work between positions on different machines;
- `of who` shows every holder of the organization, with its machine and person;
- each machine ships its records to the organization's server, which masks them; no laptop needs inbound access.

## 2. What works today

| Piece | State | Evidence |
|---|---|---|
| Ingest, durable queue, masking at the boundary, vault | built, running on the Mac | 84,403 batches accepted by 2026-11-10 |
| Invites, enrollment with consent, revocation by machine or person | built | `POST /v1/invites`, `POST /v1/enroll`, `of enroll` |
| Relay and presence across machines | built; the relay polls every server named in `orgs.yaml` | `lab-profile2-v0` passed 2026-11-06 on k3s, a server and two clients: delivery, idempotency, durability, authorization, consent, invite and enroll, relay, presence |
| Packaging for Kubernetes | built | `deploy/`: Dockerfile, Helm chart, ClickHouse schema and users, RUNBOOK |
| Secrets gate before colleagues | passed | `lab-secrets-v0`: masker on profiles 1 and 2 (2026-11-06), guard battery 53/53 (2026-11-10) |
| Two machines end to end (S2→S3), `lab-routing-v0` | not run | docs/lab.md, phase 4 gate |

## 3. What breaks with a second machine

1. **Coordination is local.** Asks, declared states, hand-overs, nudges and `of who` are projections of each machine's own event store (`~/.local/state/of/of.db`). Their events are shipped to the server, but no machine reads another machine's events back. On a colleague's machine:
   - an agent's `of ask` never reaches the founder's popup;
   - a hand-over proposed to one of its positions is never offered there;
   - the founder's pulse cannot see that the agent waits, and cannot nudge it;
   - `of status waiting --on founder --ask <id>` there finds no ask filed elsewhere.
2. **Acting on a pane is local.** The pulse, the hand-over offer, the ask correction and the desk type into panes through this machine's herdr. A pane on another machine can be reached only by that machine.
3. **`of who` and `of apply` see only local herdr.** Holders on other machines exist only in the server's presence.
4. **People.** A position held by a person is bound to one machine by `holder.machine` in the registry, and its messages wait in `of inbox` there. A user organization's registry has one such holder, and it names the person in free text. A colleague who owns a delegated unit has no queue of asks on their own machine.
5. **Reachability.** The Mac's server listens on `127.0.0.1`. The first organization's server is packaged, not deployed (on hold since 2026-11-05). No image exists: it is to be built in CI, and this repository has no remote.
6. RUNBOOK section 5 still enrolls machines by hand; the invite flow replaced that.

## 4. Design

### 4.1 One server per organization

Unchanged (RFC 0001 v3 §5.1). Each organization's machines ship to and message through its server; `orgs.yaml` names it per organization. A machine in two organizations talks to both servers; it presents its positions in other organizations as proof of the sender for replies and granted messages, as today.

### 4.2 The server carries the organization's coordination (**API**)

- **Coordination events** are a fixed list in one module: the asks and their judging, answers, escalations and withdrawals; `of.state_declared`; `of.handover_*`; `of.holder_*` and `of.mandate_changed`; `of.nudge_sent`; `of.stall_detected`; `of.diff_*`. A test fails when a projection (`of_asks`, `of_pulse.project`, `of_handover`) reads an event name that is neither on the list nor marked local-only, so a new event cannot silently stay on one machine.
- **No new write path.** Machines already ship every event. When the writer has masked an accepted batch, it also copies its coordination events into a `coord` table in `server.db`, with a sequence number. The delay is the queue's drain time, seconds under live load.
- **`GET /v1/coord?org=<org>&after=<seq>&wait=<s ≤ 30>`** → `{"events": [...], "next": <seq>}`: masked coordination events of an organization the machine has joined, oldest first. A long-poll, like the relay's.
- **The relay pulls it.** The relay process already long-polls every server; it also follows each server's feed and inserts the events into the local store, skipping event ids it has (its own come back and are dropped). The local projections then work unchanged.
- **Who reads what.** Every machine that joined the organization reads its feed; inside one organization asks and states are visible to all its agents today, on the Mac. The tenant reads every organization's.
- **Retention.** 30 days in `server.db`; a new machine backfills that window. ClickHouse keeps everything, as now.

### 4.3 Only the machine that holds a pane acts on it

- The pulse, the hand-over offer, the ask correction and the desk act only on positions this machine holds (its own presence). For a position held elsewhere they type nothing: its own machine sees the same state through the feed and acts. A position has one holder, so exactly one machine nudges it.
- Whether a remote holder is idle comes from presence (`state`), which every relay already publishes.
- Test: two simulated machines, one wait between them, exactly one nudge, typed on the waited-on peer's machine.

### 4.4 Who holds what

- `of who` adds the holders from `GET /v1/presence?org=` that are not on this machine, with machine, person and age. The endpoint exists.
- `of apply` and `of spawn` stay local: they label and start panes on this machine only. Whoever has hands on a machine spawns its positions there.

### 4.5 People (decided by `of_architect`, docs/registry.md "People", `c2e24e8`, `829f404`)

- Each organization's registry lists its people once: `people:` with `id`, `name`, `machines` and `status` (`active` or `left`). No personal data beyond a name: e-mails and handles stay in the identity store.
- A position held by a person points to the id: `holder: {runtime: human, person: <id>}`, with `machine` only when the position is bound to one.
- An invite's `person` must be one of these ids; the server checks this where it has the registry.
- An agent working for a person has that person's position as its owner, and its asks go to that person. On the person's machine, `of asks` shows them, from the feed.
- Messages to a person wait in `of inbox` on their machine, as today; a chat bridge comes later.

### 4.6 The first organization's server

- Profile 2 in the organization's cluster, deployed by its `k8s_keeper` through its structure keeper by the RUNBOOK: a dedicated ClickHouse next to the server (RUNBOOK option B; the vault holds unmasked secrets, so it stays off the shared instance), exposure through a Cloudflare tunnel behind Access with a service token per machine.
- **The image is built off the Mac**, by one of two routes (the founder decides):
  - a private GitHub repository for this code, whose CI builds the image and pushes it to the organization's registry: this needs a push, which needs the founder's yes;
  - a build on a remote build machine from a tarball of a commit; its `aws_keeper` takes the image into its registry.
- **Cutover on the founder's Mac**: `orgs.yaml` gets `server:` for the organization; the relay already polls both servers. Messages still queued for its positions on the Mac's server are drained, not left to expire: the organization's `orgs.yaml` entry gets `drain_from: local`, and the relay presents its holders to the old server too, so those messages are leased and delivered as before (2026-11-11, instead of a re-posting script: re-posting would need the new server to accept messages on behalf of their senders). When the old server holds nothing for the organization, `drain_from` is removed; the relay stops at its next round.
- **History**: the organization's records shipped since 2026-11-05 stay in the Mac's ClickHouse (its database there) unless the founder wants them copied; nothing depends on it.
- **Grants**: the server pins the grant public key. If ask `0fd223f2` moves the signing key, the new key is pinned; deploying after that answer avoids a second pin.

### 4.7 Security (the rules stay)

- A colleague's machine ships the typed tier; raw only with that person's consent, and `raw_to_org` stays `false`.
- Taking a message needs the machine's credential, its presence and the registered holder session: one machine cannot take another's messages.
- The coordination feed carries masked text only, to machines of that organization.
- No secret value in a log, a ticket or a message; the masking key and the cluster passwords travel through the secret manager.

## 5. Build steps

| # | Step | Owner | Done when |
|---|---|---|---|
| 1 | Coordination feed: the event list and its test, the `coord` table, `GET /v1/coord`, the relay's pull into the local store | `of_server` | unit tests; two relays on one test server see each other's asks and states |
| 2 | Acting only on held panes: pulse, hand-over offer, ask correction, desk | `of_server` (coordination across machines, organic-framework-build `809e9c4`), in agreement with `of_platform` and `of_asks`, whose files these are | the two-machine nudge test in 4.3 |
| 3 | `of who` with remote holders | `of_server`, in agreement with `of_platform` | a holder on a second machine is listed with machine and person |
| 4 | People in the registry (schema decided); invites checked against them | `of_server` (server check), each structure keeper (its people) | `of registry check` validates `people:`; an invite for an unknown person is refused |
| 5 | RUNBOOK update (invite flow), the organization's values file, the relay's drain of the old server (`drain_from`) | `of_server` | reviewed by its structure keeper |
| 6 | Lab: S2→S3 on two machines, `lab-routing-v0` | `of_lab`, on lab VMs | both pass: 0 records in the wrong organization; an ask, a hand-over and a nudge cross machines |
| 7 | The first organization: image, deployment, the Mac enrolled and cut over, then colleagues' machines one by one | its `k8s_keeper` and `aws_keeper` through its structure keeper; `of_server` writes the scripts | after the founder's go; `of shipped` commits on the new server; a message to and from one of its positions crosses it |

Steps 1 to 6 run in this repository and on lab VMs, never on the organization's systems. Step 7 starts only with the founder's go.

## 6. What the founder decides or does

1. **The go for the first organization's server**: given (ask `9d4b56f4`, 2026-11-10): deploy, the image built by CI in a private GitHub repository. Installed after step 6.
2. **Each colleague, when there is one**: who, which machine, and whether raw leaves it. One invite per person, filed as its own ask then.
3. **Later, not blocking**: whether the organization's history on the Mac is copied to its server.
4. Ask `0fd223f2` (the grant key), already open: it changes only which public key the server pins.

## 7. Reviewed by `of_architect` (2026-11-10, msg `01a1c734`)

- `GET /v1/coord` with the test on projection event names: yes.
- "Only the machine that holds a pane acts on it": yes; it goes into AGENTS.md's platform section when it ships.
- People: decided in docs/registry.md (4.5).
- The server version end to end is `of_server`'s (organic-framework-build `809e9c4`): the central store, enrollment of machines and people on both sides (the dispatcher entry stays `of_platform`'s), coordination across machines, packaging and deploy.
