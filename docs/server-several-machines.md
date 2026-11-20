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

### 4.8 A second machine through a reverse forward (2026-11-19, `of_server`)

For a machine of the owner's own, such as the agent host, while the server stays on the laptop (organic-framework-build `reports/agent-host-continuation-plan-2026-11-18.md`, section 2b; one server for both organizations, `of_agent-server`'s message `01a1f018-a07d`). The laptop opens `ssh -N -R /srv/of-forward/server.sock:127.0.0.1:18201` to the host. That Unix socket on the host then reaches the laptop server's TLS listener, and no port opens on the laptop. The host's clients name `https://127.0.0.1:18200`, reach it through the socket and never over TCP, and trust only that listener's pinned certificate. A TCP port on the host's loopback could be reached or taken by any user of the host, another organization's included (`of_agent-server`, msg `01a1f098-02bb`; docs/contours.md §1); the socket's folder lets in the OF team's user alone, and the pin still keeps a machine's bearer, its batches and an invite code from anything but the server (`of_secrets-review` on organic-framework PR 350).

- **The TLS listener.** `of server tls-init [--tls-port 18201]` makes a P-256 key and a self-signed leaf certificate for `127.0.0.1`, which can sign no other, under the server's state folder (`tls/server.key`, mode 600, and `tls/server-cert.pem`), prints the certificate's SHA-256, and adds `tls: {port, cert, key}` to `~/.config/of/server.yaml`; the listener opens at the server's next start. It serves the same API on `127.0.0.1` at that port (another address only when `tls: host` names it), TLS 1.2 or later, with each handshake in its request's own thread. Its `/v1/health` answers `{ok, version}` alone. An existing pair is kept, never replaced, since every client pins it. On a host other users share, `tls: socket: <path>` puts the listener on that unix socket in place of the port (mode `0660`, or `tls: socket_mode`), and the server's top-level `socket: <path>` does the same for the plain listener (mode `0600`): the server then opens no TCP port, and only the socket folder's group reaches it, the forward's user among them. A start refuses a path where a server already listens or where any other file than a stale socket lies (`bin/of_server.py` `UnixListener`; 2026-11-19, `of_server`, for a server moved onto a host other users share). When the server runs on the host and the laptop is the client, the laptop's forward runs as a stdio bridge (`deploy/host/forward.py install --mode bridge`): its unit holds one ControlMaster connection and listens on a local socket (mode `0600`); each connection is one SSH session that the host's key line and `ForceCommand` turn into a stream into the server's socket (`socat`), with every kind of forwarding disabled there (`forward.py install --mode bridge --to … --dry-run` prints that sshd block and key line), since `ssh -L` to a socket cannot be confined on OpenSSH 10.6 (2026-11-20, `of_agent-server`'s test sshds).
- **The unit.** `python3 deploy/host/forward.py install --to of-forward@<host> [--port 18200] [--local-port 18201] [--dry-run]` writes and loads the user LaunchAgent `dev.organic-framework.forward`; `status` and `uninstall` too. `--dry-run` prints the unit, the host's sshd block and the key's `authorized_keys` line, and changes nothing. As every OF process, it runs with `OF_AUTOMATION=1`, an empty `SSH_AUTH_SOCK`, its own key, `IdentityAgent=none`, `IdentitiesOnly=yes`, `BatchMode=yes` and `ForwardAgent=no`. `ExitOnForwardFailure=yes` and keep-alives (`ServerAliveInterval=30`, three missed) end a forward that is refused or dead, and launchd starts it again.
- **The key** is the forward's alone, with no passphrase, since launchd starts it unattended: `python3 deploy/host/forward.py keygen` makes it in `~/.ssh/of-forward/` (mode 700) with an empty `SSH_AUTH_SOCK`, and prints only its public half, the host's `authorized_keys` line and its sshd block, so the private key's path never appears on a command line. `python3 deploy/host/forward.py pin-host --to of-forward@<host> --fingerprint SHA256:…` reads the host's ed25519 key and writes it to the forward's own `known_hosts` only when its fingerprint is the one given, read over another channel; the forward checks the host against that file alone (`StrictHostKeyChecking=yes`), never the person's own `known_hosts`.
- **The host's user and sshd** (sshd(8), "AUTHORIZED_KEYS FILE FORMAT"; sshd_config(5), `Match`). The forward logs in as a user of its own, `of-forward`, with the shell `/usr/sbin/nologin`, held to one remote forward twice:
  - an sshd block, `/etc/ssh/sshd_config.d/of-forward.conf`: `Match User of-forward` with `AllowTcpForwarding remote` and `PermitListen none` (OpenSSH 10.6 refuses a remote socket forward while TCP forwarding is `no`; `PermitListen none` keeps every remote TCP listen out), `AllowStreamLocalForwarding remote`, `StreamLocalBindUnlink yes`, `StreamLocalBindMask 0117` (the socket at 0660), `PermitOpen none`, `AllowAgentForwarding no`, `X11Forwarding no`, `GatewayPorts no`, `PermitTTY no`, `MaxSessions 0` and `ForceCommand /usr/bin/false`, closed by `Match all` so that the main `sshd_config`'s lines after its `Include` stay global (check both with `sshd -T -C user=…`). That leaves no local, dynamic or stdio forwarding (`-L`, `-D`, `-W`), no socket forwarding, and no session, so no command runs;
  - its line in that user's `authorized_keys`: `restrict,port-forwarding,command="/usr/bin/false" <public key>` (`permitlisten` names TCP listens alone);
  - the socket's folder, `/srv/of-forward/`, owned `of-forward:of` at mode 2750: `of-forward` writes there, the OF team's user reaches it, and no other user does. sshd limits no socket path, so a listen elsewhere is limited by the folders `of-forward` may write, and the pin makes it useless.

  Since the laptop runs every agent as one OS user, the key can be used by any of its sessions; on the host it opens only a socket listen, and the pinned certificate makes a listener anywhere useless to anyone but the server.
- **Enrolling the host** in both organizations, as the owner's own machine, once the forward is up (`curl -s --unix-socket /srv/of-forward/server.sock --cacert <the certificate> https://127.0.0.1:18200/v1/health` answers on the host, as the OF team's user):
  1. On the laptop, in a plain terminal: `of invite --server http://127.0.0.1:18200 --enroll-server https://127.0.0.1:18200 --org organic-framework-build --org <the other organization> --note "agent host"`, with no `--person`, since the machine is the owner's. It prints the `of enroll` command, with `--pin` and `--code -`, and the one-time code apart from it.
  2. Copy the certificate, `tls/server-cert.pem`, to the host. It is public, and its SHA-256 is the one `tls-init` printed: check it there with `openssl x509 -in <file> -outform DER | shasum -a 256`.
  3. On the host, in a plain terminal the owner opens, never in an agent's pane and never pasted into a chat: `of enroll https://127.0.0.1:18200 --pin <the certificate> --socket /srv/of-forward/server.sock --code - --org … --org …`. `--socket` writes `~/.config/of/servers/127.0.0.1_18200.socket`, and from then on no OF client of that user opens TCP to that address. `--pin` puts the certificate at `~/.config/of/servers/127.0.0.1_18200.pem` and, once the enrollment succeeded, makes `https://127.0.0.1:18200` ship.yaml's `server_url` when that default was its http form, `http://127.0.0.1:18200` (another default stays: every organization with no `server:` of its own ships there; of_review on PR 350); it then says to restart the relay, whose open connections predate the pin; from then on that user's clients trust that certificate alone for that address, and compare it byte for byte (the SHA-256 of its DER) on every connection. If the pin file goes, a connection fails rather than trusting anything else. Every OF client of that user that still names `http://127.0.0.1:18200` (an older default, an entry written by hand) speaks to `https://127.0.0.1:18200` instead (`of_client.pinned_url`, the scheme read without case), and so do the health checks, so nothing of OF sends a bearer or a batch in plain http to that port (`of_secrets-review` on PR 350). `--code -` asks for the code without echo, so it never shows in the process list other users of the host can read. It asks per organization whether the raw tier leaves the host; the typed tier always does (4.7) The raw tier leaves only for an organization the invite offers it for (`of invite --raw-allowed`): for any other, a yes is refused before the code is used, and `of enroll` enrols that organization with typed and says so. Raw is widened later only within the invite (docs/server.md, `PUT /v1/consent`), so a host whose invite offered none needs a new invite and enrolling again after the server's keeper revokes it (2026-11-19: the owner's two yeses aborted a host's enrolment on that refusal).
  4. Check: on the laptop, `of who` lists a position held on the host with the host's machine; a message to and from that position crosses; `of_lab` re-runs `lab-routing-v0`'s coordination check on this layout.
- **Limits.**
  - Only the OF team's user of the host reaches the socket. Without a credential, it gets from the listener only `{ok, version}`, `/v1/terms`, `/v1/schema`, and enrollment with a valid invite code.
  - Every forwarded client reaches the server from `127.0.0.1`, so they share one limit on bad invite codes, with the laptop's own clients too: a client that tries wrong codes can hold up an enrollment for the limit's window, and gets no other client's credential from it.
  - While the laptop sleeps or the forward is down, the host's shipper and relay find no server and retry, and its records wait in its local spool.
- **Later**, when the server moves to the host before the money positions (that plan), the store, the organization's database and each machine's enrollment move at one cutover, and this forward is removed.

### 4.9 The owner's own machine in several organizations' servers (2026-11-19, `of_asks`)

Under contours each organization runs on its own server, in its own contour, and the owner's laptop holds none of their positions ([`contours.md`](contours.md) §3, the questions panel row; the founder's card `OF-99`). The laptop joins each organization's server as his own machine. Its relay reads each organization's coordination feed into the laptop's own store (4.2), so one questions panel shows every organization's open cards. Each card carries its own card id, whose prefix names its organization (`OF-99`, `ACM-3`). No record of one organization reaches another's server: the panel's records ship only to their card's organization's server (`of_asks`, 2026-11-19, hand-over `830275c0`).

1. **OF and herdr on the laptop**, as [`INSTALL.md`](../INSTALL.md) §3 and §4 install them, herdr's Linux release included. The laptop has no organization of its own, so it needs no `of init`. The popup's key and the panel's button need OF's herdr plugin and its key: `python3 bin/of_herdr_plugin.py install`, then `python3 bin/of_herdr_plugin.py configure`, in a plain terminal ([`herdr.md`](herdr.md), "The questions panel, on any machine"). The screens follow `language` in `~/.config/of/owner.yaml`, English by default: `of owner set language <code>` writes it, for example `of owner set language ru`.
2. **Join each organization's server**, once per organization, each in a plain terminal the owner opens, never in an agent's pane:
   - in that organization's contour: `of invite --server <its server> --org <the organization> --note "owner's laptop"`, with no `--person`, since the machine is the owner's. Add `--enroll-server` when the laptop reaches the server at another address, through a forward;
   - on the laptop: `of enroll <the server as the laptop reaches it> --code - --org <the organization>`. For a server behind a pinned forward, also give `--pin <its certificate> --socket <the forward's socket>`, as in 4.8 step 3. It asks whether the raw tier leaves the laptop for that organization; with no terminal, `--yes` answers no, and `--raw <the organization> --yes` answers yes. Each organization's server has its own local address, so each has its own pin and socket file under `~/.config/of/servers/`.

   Each enrollment writes the organization with its `server:` into the laptop's `orgs.yaml`. The shipper routes each organization's records by that entry.

   Each organization's server is its contour's own, set up there ([`contours.md`](contours.md) §4 step 3). Two contours on one machine each need their own server address, with that contour's clients pointed at it. `of init <org> --server-port <port>` gives one its own port, for example `of init acme --server-port 18300`: it writes the port to `server.yaml` before the server starts, and the address to the organization's `orgs.yaml` entry, which routes its records; `ship.yaml` keeps the default for every organization without a `server:` of its own. Without it every organization's server takes `127.0.0.1:18200`, and a second contour on the machine reaches the first one's server and is refused (`401`; `of_lab`'s walkthrough, organic-framework-build reports/framework/questions-panel-two-contours-walkthrough-2026-11-19.md, finding 1).
3. **The relay and the listener as services**: `of relay install` and `of listen install`. Where the user has no session of the system's service manager (a shell opened with `sudo -u`, for one), they do not start: run `of relay run` and `of listen run` yourself, each kept running. The relay pulls every enrolled server's feed. The listener keeps the queue's view and lays out the panel's view.
4. **The panel**: `of view add questions --workspace questions --tab questions --panel questions`, then `of view apply questions` ([`herdr.md`](herdr.md), "The questions panel, on any machine"). One herdr window shows one server's tab at a time. To keep the panel in sight beside a contour's window (a saved machine), give it a second terminal window with a herdr client of the laptop's own server, left on the `questions` workspace. With a contour's pane in focus, prefix+a opens that contour's own popup, which shows only its organization's cards ([`herdr.md`](herdr.md), "Keys and popups with a saved machine").
5. **What leaves the laptop, and where it goes.** His answer, a "not a question", or a card he hands over or puts off ships at once: the panel starts `of ship` in the background, since no agent's hook on the laptop starts the ingest worker that ships (`of_asks_answer.ship_soon`). It goes only to the server of that card's organization. That server's feed brings it to the machine that holds the asker, which types the answer or the handed card into the pane there (`of_asks_answer.pending`). A card's own record, pulled from a feed, is never shipped back.
6. **Check.** `of asks` on the laptop lists the open cards of every organization. After an answer to one card, it shows as answered in its own contour's `of asks --all`, and the asker's pane gets it. `of shipped` on the laptop lists its receipts by server and organization, and the answer's batch is on its card's organization's server alone. `tests/test_owner_panel.py` runs the same with two organizations' servers and the host's store.

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
