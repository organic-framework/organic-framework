# of-server: the sync server

Status: **Contract v0 accepted** by `of_architect`, 2026-11-05. **Built and running** on the founder's Mac: authentication, batches, receipts, the durable queue and the writers, dead-batch alerts, grant verification, and masking at the boundary with findings (`bin/of_server.py`, `bin/of_mask.py`, launchd `dev.organic-framework.server`, `127.0.0.1:18200`); the shipper uses it. Also built: per-organization servers (see Routing), remote enrollment with consent (Machines and consent), packaging for Kubernetes (`deploy/`). Relay and presence and the OTLP/JSON logs receiver are built too. Not yet built: OTLP traces and protobuf. Written by `of_platform` after the founder's decision to put a server in front of ClickHouse (RFC 0001 v2, §5.2a). Keeper since 2026-11-05: `of_server`, the single writer of `bin/of_server.py`, `of_mask.py`, `of_route.py`, `of_plugins.py`, `of_grants.py`. This document fixes the **API contract first**; the implementation follows once the contract is accepted. Frame: [`storage.md`](storage.md), [`events.md`](events.md), [`registry.md`](registry.md) (grants).

## Why a server

Today every machine's shipper inserts into ClickHouse directly, holding the database credentials. That cannot reach colleagues' machines: they would need database credentials, routing and masking would run on machines the tenant does not control, and nothing would acknowledge durability. Systems that ingest agent and product telemetry at scale converge on one shape: **edge → durable buffer → batch writers → ClickHouse**. PostHog, Langfuse v3, Sentry and SigNoz all work this way. `of-server` is that shape, small enough for one Mac, with a contract that does not change when it moves to the cloud.

## Responsibilities

| | Today (shipper) | With `of-server` |
|---|---|---|
| Authentication | ClickHouse users on every machine | per-machine credentials, verified by the server; only the server holds `*_ingest` |
| Schema validation | none | per record type and schema version; the current and the previous version are accepted |
| Idempotency | ReplacingMergeTree only | batch ids plus record ids; a retried batch returns the same receipt |
| Routing to organizations | in the shipper | on the server, also checked against the organizations the machine has joined |
| Masking at the boundary | not built | on the server, before anything reaches an organization's database |
| Durability | none before the insert | a durable on-disk queue; the acknowledgement comes only after fsync |
| Writes to ClickHouse | many small inserts | batch writers per database and table |
| Relay and presence | not built | messages between machines; live holders per machine |
| Grant verification | not built | the server resolves and verifies grants for the relay and for relayed authority |

## API contract, v1

HTTP/1.1 with JSON bodies, base path `/v1`. Request bodies may be compressed (`Content-Encoding: zstd` or `gzip`). Every request carries `Authorization: Bearer <machine token>`, except `GET /v1/health`. Errors use `application/problem+json` (RFC 9457) with a stable `type`.

### Ingest

**`POST /v1/batches`**: the shipper's only write call.

```json
{
  "batch_id": "0193f0c2-…",            // idempotency key, UUIDv7, chosen by the machine
  "machine_id": "8316e52e-…",
  "schema": 1,                         // events.md schema version of the records
  "records": [
    {"type": "event", "data": { … one events.md record … }},
    {"type": "transcript_record", "data": {"session_id": "…", "record_uuid": "…", "ts": "…", "harness": "claude-code",
                                           "source_path": "…", "line_offset": 123, "sha256": "…", "raw": "…"}},
    {"type": "hook_record", "data": {"local_id": 42, "received_at": "…", "hook_event": "Stop", "session_id": "…",
                                     "sha256": "…", "raw": "…"}},
    {"type": "runtime_record", "data": {"received_at": "…", "runtime": "herdr", "type": "pane.agent_status_changed",
                                        "pane_id": "w3:p5", "raw": "…"}}
  ]
}
```

Responses:
- **`202 Accepted`** after the batch is fsynced to the durable queue:
  ```json
  {"batch_id": "…", "state": "queued", "accepted": 1998, "rejected": [{"index": 17, "reason": "schema: missing ts"}]}
  ```
  A record is rejected alone; the rest of the batch is kept. A retry with the same `batch_id` returns the original receipt and writes nothing again. A record the server has already accepted in any earlier batch (by its natural key, per organization) is skipped as well, and the receipt counts it in `duplicates`; the tenant may set `"replace": true` on a batch to re-send on purpose.
- `400`: the batch itself is malformed. `401` / `403`: the machine is unknown, revoked, or sends an organization it has not joined. `409`: the same `batch_id` with a different body. `413`: over 64 MB after decompression. `429` / `503`: back off, with `Retry-After`.
- Record identity is stable: `of.event.id` for events, `session_id + record_uuid` for transcript records, `machine_id + local_id` for hook records. ClickHouse tables stay ReplacingMergeTree on these keys, so even a lost receipt cannot duplicate.

**`GET /v1/receipts?after=<cursor>`**: the machine's receipts, in order. A batch moves from `queued` to `committed` once every one of its records is in ClickHouse, raw records included in the vault. Local raw is deleted only after `committed` (ship → confirm → delete). `of shipped` lists these receipts with record counts per organization and tier.

**`POST /v1/otlp/v1/logs`**: an OTLP/HTTP logs receiver, for harnesses and tools that already speak OpenTelemetry (Claude Code's and Codex's OTel export). Built 2026-11-05 (`bin/of_server_otlp.py`). **OTLP/JSON only** (`Content-Type: application/json`; protobuf answers `415`: set the exporter to `http/json`); machine bearer as for batches.
- A log record with `gen_ai.*` attributes, or an event name starting `gen_ai.`, `claude_code.`, `codex.` or `of.`, becomes an `event`: its name, `timestamp`, resource and record attributes are kept under their OTel names (events.md, principle 5), `source: otlp`; the session comes from `session.id`, `gen_ai.conversation.id` or `conversation.id`.
- Anything else is kept as a `runtime_record` with `runtime: "otlp"`, the whole log record and resource as its raw.
- The organization comes from the resource or record attribute `of.org`, else from routing; consent, masking and the vault apply as for any batch. The batch id is derived from the body, so a re-sent request adds nothing. The answer is `200 {}`, or `200 {"partialSuccess": {"rejectedLogRecords", "errorMessage"}}`.
- Traces (`/v1/otlp/v1/traces`) come later.

**`GET /v1/schema`**: the accepted schema versions and the record types with their required fields.

### Relay and presence

Built 2026-11-05 by `of_server` (`bin/of_server_relay.py`), accepted by `of_architect` with amendments. Client: `of send`, `of relay run`, `of inbox` (`of_platform`, `bin/of_relay_client.py`).

**`PUT /v1/presence`** `{"holders": [{"org", "position", "session_id", "state", "pane", "kind": "agent" | "human"}]}` replaces the machine's presence; TTL 120 s. The relay re-reads its holders every 10 s and publishes at once when who holds what changed (a position, session, pane or kind; an account move, 2026-11-07), else every 60 s; → `{"holders": <n>, "ttl", "queued"}` (see below). Holders of organizations served here must be joined by the machine. A position of an organization not served here is accepted only as proof that this machine holds the sender's position for messages across organizations; it is never listed.
**`GET /v1/presence?org=<org>`** → `{"holders": [{…, "machine_id", "person", "age_s"}]}`; only organizations the machine has joined (the tenant sees all).

**`POST /v1/messages`** `{"msg_id", "to": "<org>:<position>", "from": "<org>:<position>", "text", "reply_to", "grant_id"?, "grants"?: [grant, …its issuer chain]}` → `202 {"msg_id", "state": "queued", "authority", "authority_reason"}`. A `to` position that breaks the name rule `[a-z][a-z0-9_-]{0,31}` (AGENTS.md, agent names) is refused with `400 bad_position`, since no position could ever take it; whether the position exists in the registry is left to the client. An optional `"priority"`: `normal` (the default), `fyi` or `urgent` (anything else is `400`); it is stored, echoed in the response and returned with the message everywhere. **An `fyi` message is never offered to an agent's relay** (the long-poll below leaves it out, so it is never leased and never typed into a pane): the agent sees it counted in its presence `queued` and reads it with `of inbox --mine` (`queued`, `take`); a person's relay still gets it for `of inbox`. Unread, it expires like any other. `urgent` is for the client: its relay sends it past the 15-minute batching.
- Posted to the server of the `to` organization. A same `msg_id` with the same body returns the same answer; with another body, `409`. Text is at most 64 KB.
- **Masking**: the text is masked before it is stored or delivered (RFC 0002, principle 1); findings go to the vault.
- **Authority**: `verified` only when (a) the grant chain verifies (pinned Ed25519 keys; inline grants make this work across servers), its subject is the `from` position and its scope admits the receiver: `to_positions` (`<org>:<position>` patterns; checked for every message, in the sender's own organization too) and, across organizations, `to_org`, and (b) the sending machine currently presents the `from` position, and, where the server has that organization's registry (`positions_files`), with the position's registered holder session. Otherwise `unverified_claim` with the reason (`sender not holder: …`), or `none` without a grant.
- A message across organizations without `verified` authority is rejected with `403`, unless it is a **reply**. A reply goes through when all of these hold:
  - its `reply_to` names a message from the receiving position that was delivered to the sending position at most 7 days before (`done_at`, else its creation);
  - the sending machine joined the sender's organization;
  - the machine holds the sending position (presence, and the registered holder where the registry is known).

  The reply goes only back to that sender. It carries no authority: `authority: none`, the reason "a reply to <id> (no grant needed; carries no authority)", and no `grant_id`, even when one was sent. The rule is `of_architect`'s, on `of_server`'s proposal (docs/registry.md, Grants, "Where they live"; `2eeabd5`, 2026-11-09). Within one organization the sender's organization must be joined by the machine.

**`GET /v1/messages?wait=<s ≤ 30>`** (outbound long-poll) → `{"messages": [{"msg_id", "to", "from", "text", "reply_to", "authority", "authority_reason", "grant_id", "sender_machine", "sender_person", "created_at", "attempt", "kind", "priority", "reply_to_from"}]}`: messages for the positions this machine presents now (for an agent's position, not `fyi` ones), each leased to it for 60 s. The server returns structured fields, never ready-made text: the client frames them (`Message from <org>:<position> (via relay, authority: …; machine/person …)`) and never presents them as the owner's words (RFC 0001 §5.9). `kind: human` goes to the person's `of inbox`, `agent` into the pane (`herdr agent prompt` after idle, preceded by `injected_prompt`). **A receiver's queue goes as a whole** (2026-11-09, from a user organization): when one message for a position is due, its other queued messages that only wait out a deferral (no failure counted) are leased with it, oldest first; one that failed keeps its back-off. Before this, each deferred message had its own retry time a fraction of a second apart; the first one due was typed alone and the client's 15-minute window held the rest for another 15 minutes. That is how a project position's GO, sent at 11:05:39Z, reached `k8s_keeper` at 11:29:19Z, after the step's 11:10Z start, and how the 10:59:15Z batch skipped two older messages. An `urgent` message now takes the receiver's queued ones with it. `reply_to_from` (2026-11-11) names the sender of the message a reply answers, so a client can tell a reply to the receiver's own message. The client has no window since 2026-11-11 (docs/guard.md R9): it types whatever is leased for an idle receiver as one prompt.
**`POST /v1/messages/{msg_id}/ack`** `{"status": "delivered" | "failed" | "deferred", "reason", "retry_after"}`: only the machine holding the lease (`409` otherwise). `deferred` means the receiver is busy (the agent is not idle): the message is offered again after `retry_after` seconds (default 30, 5 to 600) and no failure is counted, so a client checks idleness briefly and defers instead of holding a lease for minutes. A failed delivery is offered again after a back-off (30 s, doubling, at most 10 minutes); after 5 failures it is `dead`, and `of_server.py relay-requeue` offers dead messages again once the receiver is fixed. The server host drops a queued message nobody can take (its target typed wrong) with `of_server.py relay-drop --msg-id M --reason "…"`: it becomes `expired` with reason `dropped: …` and `of.message_expired` carries that reason; `relay-requeue --state expired --msg-id M` undoes it. Delivery is at least once; receivers deduplicate by `msg_id`.

**An agent takes its own messages** (`of inbox --mine`; accepted by `of_architect` 2026-11-06, built). Instead of waiting for the relay to inject a message into its pane, an agent lists the messages queued for the position it holds and takes them itself:
- **`GET /v1/messages/queued?to=<org>:<position>&session=<session_id>`** → `{"messages": [{…the fields of the long-poll…, "state": "queued" | "leased", "lease_s"}]}`, oldest first, at most 100. Nothing is leased by listing. Allowed when this machine presents that position now with that `session_id` (its `PUT /v1/presence`), as an agent (a person's position, `kind: human`, keeps its local `of inbox`), and, where the server has the organization's registry (`positions_files`), that session is the position's registered holder: the same test as a sender's (`sender not holder…`); `403 not_holder` otherwise.
- **`POST /v1/messages/{msg_id}/take`** `{"to"?: "<org>:<position>", "session": "<session_id>"}` → the message's fields (as listed) with `"state": "delivered", "already": false`: the same holder test, for the message's `to` (`400` when `to` is given and differs). A queued message (waiting, deferred or backing off after a failure) or one whose lease ran out becomes `delivered` at once, so the relay never offers it again and never injects it; `of.message_delivered` is written with `via: "taken"` and the session. A message under a live lease (the relay is delivering it right now) is refused with `409 leased` and `retry_after` (the lease's remaining seconds): the relay injects it, or defers it and frees it within seconds. A message already delivered (or dead, or expired) answers `200` with its fields, its state and `"already": true`, so a take is idempotent. A lease held by this same machine's relay is refused too: the relay may be typing it.
- **Queued counts in `PUT /v1/presence`**: its response carries `"queued": {"<org>:<position>": {"n", "oldest"}}`, messages not yet delivered (`queued` or `leased`) for each position just presented that has any (`oldest`: the oldest `created_at`); `{}` when none. The client's Stop-hook line reads it.
- **What a sender sent that has not landed**, in the same response (2026-11-09, from a user organization: a GO waited 24 minutes and its sender could not see it): `"unsent": {"<org>:<position>": {"n", "items": [{"msg_id", "to", "created_at", "priority", "state", "reason", "next_at", "present"}]}}`, the messages each position just presented sent that are not delivered yet (`fyi` left out), the 10 oldest; `reason` is the last deferral's, `next_at` when it is offered again, `present` whether its receiver has a live holder. `of status` prints one line for the messages between 5 minutes and a day old that were not delivered here since, per receiver, oldest first: "3 messages you sent are not delivered yet: 2 to acme:k8s_keeper (oldest 8m; batched, next in 1m); to acme:catalogue_keeper (7m; being typed now)", and, when one waits for the receiver's 15-minute window, that an `--urgent` message to a receiver takes its queued ones with it. Past a day the note is no longer about timing, so it is not repeated at every turn end.
- On `409 leased` the client tells the agent the message is arriving in its pane now, not an error.
- **The client takes only what the agent was shown** (2026-11-08, from a user organization: `of inbox --take all >/dev/null` took a reply that arrived after the agent's listing, and its text was lost; at least ~100 such messages in two days). A plain `of inbox --mine` records, per session, the messages it listed (`relay-shown.json`). `--take all` takes only those; a message that arrived since is printed as "new since your last listing" and stays queued, because the output of a take may be thrown away. `--take <id>` takes what it names. Every taken message is kept here with its text, the server's masked copy (`relay-taken.jsonl`, mode 600, 14 days), and `of inbox --mine --taken` shows it again.
- The guard is the machine's credential plus the session: every process on an enrolled machine holds the credential, so the session check keeps agents on one machine from taking each other's messages by mistake; it is not a secret.

### Coordination across machines

Built 2026-11-10 (`bin/of_coord.py`, `bin/of_server_coord.py`; plan [`server-several-machines.md`](server-several-machines.md) §4.2, accepted by `of_architect`). Asks, declared states, hand-overs and holder changes are projections of each machine's own event store; this feed lets every machine of an organization see them.
- **What is shared**: the events in `of_coord.EVENTS` and the `of.handover_*` and `of.diff_*` families. Everything else a projection reads is in `of_coord.LOCAL_ONLY` (prompts, turns, pane-level facts): only the machine that holds a pane acts on it. `tests/test_server_coord.py` fails when a projection reads an event name that is on neither list.
- **No new write path.** When the writer has written a batch's events into an organization's database, it copies that organization's coordination events (masked: the rows the organization got; shape copies of other organizations' events left out) into `server.db` table `coord`, idempotent by event id. The delay is the queue's drain time. Kept 30 days.
- **`GET /v1/coord?org=<org>&after=<seq>&wait=<s ≤ 30>`** → `{"events": [{"seq", "event_id", "name", "ts", "host_id", "record"}], "next", "last"}`, oldest first, at most 500: only for a machine that joined the organization (the tenant reads all; `403` otherwise). `last` is the newest sequence number held: a cursor past it means the server started over, and the client reads again from 0.
- **The relay pulls it**: one thread per server reads each of its organizations every 10 s (at once again after events came), stores the events other machines wrote as they are, with `raw_ref` `coord:<org>:<seq>`, and keeps its cursor in `coord-cursor.json`. The store skips an event id it holds. `of ship` never sends a pulled event back.

**Store and forward**: a message to a position with no live holder waits up to 14 days, then expires.

**Events** in the recipient organization: `of.message_sent` (masked text, authority, sender machine and person), `of.message_delivered`, `of.message_failed`, `of.message_expired`. When the sender's organization is also served by this server and differs, it gets a slim `of.message_sent` without the text.

### Secrets broker (2026-11-11, `of_server`; [`agent-secrets.md`](agent-secrets.md) §5.3)

**`POST /v1/secrets/use`** `{"org", "position", "session_id", "credential"}` (machine bearer) → `200 {"value", "ref_vault", "cached"}`. The value goes only into the caller's command (`of secret run`); the server answers only when:
- the machine joined the organization;
- it presents the position now with that session, and the session is the position's registered holder (`holder.session`) in the registry the server reads (`positions_files`, else `orgs.yaml`): a former holder is refused from its next call (`403 not_holder`);
- the position lists or keeps the credential (`403 not_listed`; `404 unknown_credential`), which has a secret reference (`409 no_ref`);
- the position's service-account token is in the broker's folder, `secrets.tokens_dir` (`~/.config/of/service-accounts/<org>/<position>.token`; `409 no_service_account`).

It reads the item with `op read` as that position's service account (`secrets.op`, the token in op's environment only), keeps the value in memory for 5 minutes keyed by session, and records every decision in server.db `secret_uses` (never a value). 1Password's refusal is `502 vault_refused`, without its message.

### Finance facts

Built 2026-11-10 (`bin/of_server_finance.py`), for `of event finance_fact` (`of_platform`'s client); the rules are [`events.md`](events.md), Finance facts. A finance fact is written only here, never in a batch.

**`POST /v1/finance_facts`** `{"record": <the of.finance_fact event record>}` (machine bearer) → `202 {"event_id", "accepted": true, "state", "batch_id"}`; the same fact again → `200` with `"already": true`; the same event id with another body → `409`. The server checks, in order:
- the payload: `period`, `kind`, `category`, `product`, `account`, `amount`, `currency`, `amount_usd`, `fx`, `basis`, `source`, `supersedes`, `grant_id`, and no other field (amounts per person are never recorded): `400 invalid_fact` with every problem. A balance (`kind: B`, 2026-11-11) has the day `YYYY-MM-DD` for its period and an `account` instead of a category: the treasury's own name, refused when it looks like an account number or an address;
- the organization: served here and joined by the machine (`403`);
- the grant `grant_id`: it verifies, its subject is the writing position (the record's `gen_ai.agent.name`), it is an allow with `data: [finance]` in its scope and an `act_*` tier (`observe` only reads), and no valid deny with `data: [finance]` covers the position (`403 no_finance_grant`, with the reason). An omitted `data` grants none, unlike the other scope dimensions;
- the writer: this machine presents the position now with the record's session, the registered holder where the server has the registry (`403 not_holder`);
- `supersedes`: a fact of the same (`org`, `period`, `kind`, `category`, `product`), for a balance of the same (`org`, `period`, `account`), that no other fact supersedes yet (`400`, or `409 superseded` naming the fact to supersede instead).

The fact is then queued like a one-record batch (`finance-<event id>`): durable before the answer, masked, written to the organization's database only, never a shape or publication copy, never the coordination feed. `server.db` keeps its key and the hash of its record for the next check, never its amounts. A batch, an OTLP log or a copy carrying `of.finance_fact` is refused record by record. `GET /v1/terms` lists `"capabilities": ["coord", "finance_facts"]`, so a client refuses to send a fact to a server without it. In ClickHouse the colleagues' read user (`<db>_read`) sees the event table only through `v_event`, which leaves finance facts out (`deploy/clickhouse/schema.py`, `CREATE OR REPLACE`, so an existing installation gets it too); a row policy would not reach it, since the views run as their definer. The tenant reads the table.

### Redaction

Accepted by `of_architect` 2026-11-06 for `of redact <sha|session> --reason` (`of_platform`'s client). Built (server side, `bin/of_server.py` `create_redaction`, `Writer._redact`; storage `delete` in `bin/of_plugins.py`); the client follows. A machine that finds a secret in rows it has already shipped asks the server to replace them with a masked copy everywhere this server wrote them.

**`POST /v1/redactions`** `{"redaction_id", "table": "transcript_record" | "hook_record" | "event", "ids": [...], "session", "sha_old", "sha_new", "reason", "records": [{"type", "data"}, …]}` → `202 {"redaction_id", "state": "queued"}`.
- `ids`: the rows' natural keys in that table: for `transcript_record`, `record_uuid` with `session`, or `[session_id, record_uuid]` pairs (many sessions in one request, `session` omitted); `local_id` for `hook_record` (with this machine's `host_id`); `event_id` for `event`. At most 10,000 per request. The writer deletes them a thousand keys per statement (ClickHouse's `max_query_size`), so a sweep of thousands of rows is a few mutations per database, not one per session.
- `records`: the replacement rows, as in a batch, made by the client from the redacted source (`sha_new`). The server masks them again, routes them like any batch and writes them past the duplicate check (they reuse the old keys). Empty `records`: the rows are only deleted. A replacement in a table with content (`transcript_record`, `hook_record`) must carry its `raw` (`400` otherwise): an empty one would empty the vault's copy, which is a delete. The client sends the copies instead of re-shipping, so no cursor or seen-set has to be rewound.
- **Who**: the tenant's machine for any row; another machine only for rows it shipped itself (`host_id` = its machine id), `403` otherwise. A pure delete (empty `records`) needs the tenant: deleting without a replacement is the one way to hide history. `of.redacted` rows are never redactable (the delete excludes them). `redaction_id` makes a retry idempotent (`200` with the current state).
- **Order**: the redaction joins the same disk queue as batches and runs after every batch this server received before it. The writer then, in every database this server writes (each organization's, the unassigned one, the vault): `ALTER TABLE <db>.<table> DELETE WHERE <key> IN (…)` with `mutations_sync = 1`, a mutation that rewrites the parts (a lightweight `DELETE` would only hide the rows until the next merge); then it inserts the replacement.
- **A replacement goes where its row was**: before deleting, the writer finds which organization's database holds each named row and sets the replacement's `org_id` to it (saved in the redaction's own queue file, so a retry keeps it); a replacement whose row is in no organization's database keeps its own routing. (On 2026-11-06 replacements routed afresh landed in the unassigned database.)
- **The disk too**: committed batches leave the disk at commit, and every batch received before the redaction is committed when it runs (the writer keeps order); a later batch cannot hold the old rows (the duplicate check drops them). A batch in `dead/` may, and would write them back on `replay-dead`: the redaction rewrites those files without the rows before it reports `done`. Another redaction's file is never touched (it holds replacements). `sha_old`'s blob is dropped from the server's blob store if it is there (v0 ingest stores none).
- **Event**: `of.redacted` {`redaction_id`, `table`, `ids`, `sha_old`, `sha_new`, `reason`, `machine_id`, `counts`: {database: rows deleted}}, never content, into the vault and each organization database that held the rows.
- **`GET /v1/redactions/{redaction_id}`** → `{"state": "queued" | "done" | "failed" | "cancelled", "counts", "error"}`; a failed one is retried like a batch and becomes `failed` after 8 attempts, with a `server_alert`. The tenant's keeper may cancel a queued redaction by hand (batch and redaction state `cancelled`, its file kept in `dead/`); health, `replay-dead` and the writer leave it alone.
- **ClickHouse**: `<db>_ingest` stays insert-only, so a stolen machine credential, or the ingest path itself, can never delete history: the store stays an audit trail. A separate role `<db>_redact` has `ALTER DELETE` on the four tables only (`deploy/clickhouse/schema.py`, its password in the server's Secret); only the server's redaction worker uses it, after the authorization check above. A server without it fails the redaction with that reason. ClickHouse backups and replicas outside this server keep the old rows: the keeper redacts those. Profile 1 with SQLite storage deletes by key.

### Grants

**`POST /v1/grants/verify`**: `{"grant_id": "…", "subject": "<org>:<position>", "action": {…optional…}}` returns `{"valid": true, "tier": "propose", "reason": "ok", "decision": "admit"}`. The server reads the organizations' `registry/grants.yaml` and verifies signatures, expiry, revocation, counters and delegation chains ([`registry.md`](registry.md), Grants). The tenant-only master key never leaves the server.

### Authentication

Each machine has an Ed25519 key, made with `ssh-keygen` at enrollment (`~/.config/of/machine_ed25519`); the private key never leaves the machine. Signatures use OpenSSH's `ssh-keygen -Y sign` format, namespace `of-server`, so no third-party crypto library is needed on either side.

**`POST /v1/auth/challenge`** `{"machine_id"}` → `{"nonce", "namespace"}`, valid for 60 s.
**`POST /v1/auth/token`** `{"machine_id", "nonce", "signature"}` → `{"token", "expires"}`. The token is valid for about one hour; the server stores only its hash. Revocation removes the machine, and its tokens stop working at once; others expire on their own. mTLS can come later, with TLS for colleagues.

### Machines and consent

Built 2026-11-05 by `of_server`; the invite flow replaces the tenant-only `POST /v1/machines/enroll` of contract v0 (accepted by `of_architect`, with amendments). A colleague's machine joins without anyone touching the server host.

**`POST /v1/invites`** (tenant machines only): `{"orgs": [...], "raw_allowed": [...], "person": "<id>", "ttl_s": 259200, "note": "…"}` → `{"code", "expires", "expires_at", "orgs", "raw_allowed", "person"}` (`expires`: epoch seconds; `expires_at`: the same for people, `YYYY-MM-DD HH:MM UTC`).
- The code is 20 random bytes in base32, single use; the server stores only its hash. It expires after 72 hours by default, 7 days at most.
- `person` binds the machine to a person: the colleague's id in the organization's registry, which positions held by humans point to. A machine without a person is a machine of the tenant. Per-owner Studio views and delegated units rely on it.
- On the server host the keeper can do the same: `of_server.py invite --org X [--raw-allowed X] [--person P]`.

**`POST /v1/enroll`** (no bearer; the code is the credential; over TLS): `{"code", "machine_id", "pubkey": "ssh-ed25519 …", "consent": {"<org>": {"tiers": ["typed"] | ["typed", "raw"], "terms": "<version>"}}}` → `{"machine_id", "orgs", "raw_orgs", "person"}`.
- The consent names exactly the invite's organizations. `typed` is the required minimum; `raw` only for organizations in the invite's `raw_allowed`; the terms version must be one the server offers.
- Errors: `400` malformed or bad consent, `403` raw not offered, `404` unknown code, `410` used or expired, `409` the machine is already enrolled, `429` after 10 bad codes from one address in 10 minutes.
- Each organization gets an `of.consent` event (`machine_id`, `person`, `tiers`, `terms`, `via: enroll`) and an `of.machine_enrolled` event, through the durable queue.

**`PUT /v1/consent`** (machine bearer): `{"<org>": {"tiers", "terms"}}` → the machine's consent. Narrowing is always allowed; widening `raw` only within the invite. It applies to batches accepted from then on. Rows already shipped follow the terms' `effect_on_shipped` (RFC 0001 §5.5); the `of.consent` event records `withdrawn` and `effect_on_shipped`. Only `remain` (rows stay under the terms they were shipped under) is carried out today; the server refuses to start with terms that promise anything else.

**`GET /v1/terms`** (no bearer): `{"current", "tiers", "required": ["typed"], "effect_on_shipped", "url"}`. Terms live in `server.yaml` (`terms`, `terms_current`).

**`DELETE /v1/machines/{machine_id}`** and **`DELETE /v1/machines?person=<id>`** (tenant only) → `{"revoked": [...]}`. The key and its tokens stop working at once; each organization the machine had joined gets `of.machine_revoked`. Withdrawing a colleague revokes every machine of theirs in one call. On the server host: `of_server.py revoke --machine-id X | --person P`.

**Receipts** carry `tiers: {"<org>": [...]}` as consented when the batch was accepted, for `of shipped`.

### Health

**Its own checks** (docs/health.md §2, §5; `of_architect`'s hand-over `154a0cbf`): `server.health`, the longest time in the last three minutes from accepting a `/v1/health` connection to its reply written, degraded over 2 s and failed over 20 s; and `relay.oldest`, how long the oldest message, other than an fyi, has been queued for an agent that is live and idle (or done), counted from the first check that saw it so, with the messages the person holds (a dialog open, text in the input box) left out, degraded over 10 minutes and failed over 60. The server judges them every minute, keeps one state each with since when, writes `of.health_changed` only on a change, and serves them to its machines at **`GET /v1/health/checks`** (machine token) for `of health`.

**`GET /v1/health`**: liveness, no authentication. Locally it also reports the queue: `queued`, `queue_bytes`, `drain_rate_bps` (the writer's moving average) and `drain_eta_s`, so a backlog that drains after a large import reads apart from a stall (`writer_error`, or batches waiting with no rate); on a public ingress only `{ok, version}`. **`GET /v1/metrics`**: queue depth and age, writer lag per database, rejects by reason, receipts not yet committed. Tenant only.

## Processing on the server

1. **Authenticate** the machine and load its joined organizations and consented tiers.
2. **Validate** each record against its type and schema version (the current and the previous).
3. **Route**: decide each record's organization by the rules in [`storage.md`](storage.md) (`of.serves`, the session's position, working-directory paths, else unassigned). A record whose organization the machine has not joined is rejected, never written elsewhere.
4. **Fan out**:
   - raw records go unmasked to the vault, if the machine's consent includes the raw tier for that organization;
   - the organization's database gets typed events, shape copies, publication copies, and raw records **masked** (`[secret:<kind>:<hmac8>]`, RFC 0002);
   - every secret detected becomes an `of.secret_detected` event in a tenant-only table.
5. **Queue durably**: append to segment files on disk, fsync, then answer `202`.
6. **Write in batches**: one writer per database and table, flushing every 1 s or 10,000 rows or 16 MB, with retries and backoff; poison records go to a dead-letter segment. When a batch's records are all written, its receipt becomes `committed`.

## Masking at the boundary

Built 2026-11-05 (RFC 0002 §4.2; `bin/of_mask.py`). It runs only on the server, in the writers, so no client can skip it.

**What is masked.** Every row bound for an organization's database (or the unassigned one): an event's `payload` and `record`, and the `raw` of transcript, hook and runtime records. Shape copies and publication copies are made from the masked row. The vault gets raw unmasked; nothing else does.

**The detector.**
- The **gitleaks 8.30.1 default ruleset**, vendored as `bin/rules/gitleaks-8.30.1.toml` (MIT; the same version as the history scan in RFC 0002 §2). Rule regex, keywords, `secretGroup`, entropy, and the global and per-rule allowlists (regexes and stopwords) apply as in gitleaks; path allowlists and path-only rules do not, since a record has no path. Go RE2 syntax is translated to Python's `re`. Two rules run without their vendor keyword, because their patterns are distinctive and a pasted token rarely names its vendor: `airtable-personnal-access-token`, `facebook-access-token`.
- **Contextual rules**:
  - `evm-private-key`: 64 hex characters, with or without `0x`, whose 80-character window before it holds a key phrase (`private … key`, `signer key`, …) or ends in an identifier containing `key`, `pk`, `priv`, `secret`, `seed`, `mnemonic` or `signer` (`pk=`, `"wallet_pk": "`, `PRIVATE_KEY=`, `key 0x…`), unless the identifier names a hash (`sha`, `hash`, `digest`, `code`, `request`, `tx`, `block`, `root`, `public`, `address`, …);
  - `url-password`: the password in `scheme://user:password@host`;
  - `seed-phrase`: 12 to 24 consecutive BIP-39 English words, with any spacing or numbering.
- **Look-alikes survive**: a bare 64-hex string (transaction and block hashes), git SHAs, UUIDs and addresses are not masked. A bare private key with no key word next to it is therefore an honest miss, reported by `lab-secrets-v0`.
- A JSON record is scanned twice: as serialized text, for contexts that span JSON structure, and as its decoded strings, for content that escaping hides. The result stays valid JSON. Overlapping findings merge into one span: the masker errs towards masking.

**The tag** is `[secret:<kind>:<hmac8>]`: `<kind>` is the gitleaks rule id or the contextual kind, `<hmac8>` the first 8 hex digits of HMAC-SHA256 under the tenant purpose key `of/secret/v1`. One secret keeps one tag however it is written (an EVM key with or without `0x`, a seed phrase however spaced).

**Findings.** Each secret found becomes one `of.secret_detected` event per (kind, HMAC, organization) in the vault's `event` table, which only the tenant reads. It is never written to an organization's database. Its payload holds `kind`, `hmac`, `org`, `count`, `batches`, `first_seen`, `last_seen`, `tables` and `last_location` (a record identity such as `transcript_record:<session>/<record uuid>`), never the value. The server keeps sightings per batch in `server.db`, so a retried batch adds nothing; the event's id and `ts` are stable, so ReplacingMergeTree keeps one row per secret with its latest totals.

**The gate.** `raw_to_org` stays `false` until `lab-secrets-v0` passes. While it is closed, organizations get typed events (masked) and raw-tier records without their content; the masker still scans the raw, so findings exist from day one. Without a masker (the shipper's direct mode) raw never reaches an organization, whatever `raw_to_org` says.

**Cost.** About 3.2 MB/s on one core of the founder's Mac, measured on 76 MB of real transcripts (20,521 records): about 5 minutes of CPU per GB (2 MB/s before the one-scan prefilters of 2026-11-06: the keywords through one prefix-trie regex, reused for the decoded pass when no `\u` or `\/` escape could hide one, and the BIP-39 check only inside stretches of twelve or more joined words; output identical on that sample). The writer therefore runs as a child process of the server (`of_server.py writer`, supervised and restarted if it exits), so masking, which holds the interpreter lock during long regular-expression matches, never slows the HTTP side; it reports through `<state_dir>/writer.json`, which `/v1/health` reads.

**Under load** (2026-11-06, a 5.5 GB backlog): the writer drains about 1 MB/s of queued batch bytes, CPU-bound in regular expressions; text outside ASCII (a quarter of the founder's records: Cyrillic, emoji) is slower than ASCII in Python's `re`. Live load is far below that, so only a one-off backlog feels it. Two candidates, not taken: (1) compile the gitleaks rules with `re.ASCII`, which is closer to RE2, whose `\w` is ASCII; about 20% faster on `generic-api-key`, and its spans differ on 5 of 6,000 texts, so it is taken only with a `lab-secrets-v0` re-run that shows no missed secret; (2) a multi-process writer, only where a pod has more than one CPU and backlogs recur. One writer otherwise.

## Routing with one server per organization

Decided by `of_architect`, 2026-11-05 (RFC 0001 v3 §5.1). A user organization runs its own `of-server` in its cluster, and the build team keeps a local one, so a machine ships each organization's records to that organization's server (`server:` per organization in `orgs.yaml`; absent means the local server).
- **The client routes.** Only the machine has the registries and paths. It runs `route_org` (`bin/of_route.py`) and sends each record with `org_id` to that organization's server.
  An event whose session, organization and directory name no organization but whose payload names an ask (`ask_id`, `ask_ids`: an ask's closure written by a reconcile or a withdrawal) follows that ask: `route_org` looks the ask up in the machine's event store and routes it the same way (2026-11-06; before that such closures went to the unassigned database, which an organization's Studio does not read, and its asks stayed open there). Events already shipped are not moved.
- **Copies across organizations are reduced on the client**: builder shape copies and the receiving side of `of.published`, with the reduction functions in `of_route.py`. There is no server-to-server exchange.
- **The server checks again (defence in depth)**: it rejects records of organizations it does not serve or the machine has not joined; it rejects shape and publication copies that carry anything outside the content-free allow-list; it masks, keeps its own tenant-only vault for its organizations' raw, and writes its own findings.
- **Grants** are pulled by the server from the organization's `registry/grants.yaml` with a read-only deploy key. Grant signatures move to an asymmetric tenant key (Ed25519, `ssh-keygen -Y sign`, namespace `of-grant`): the private key stays on the tenant's machine and servers hold only the public key, so a server can verify a grant but never forge one. HMAC signatures stay valid as legacy until everything is re-signed. The masking key `of/secret/v1` is separate; it grants no authority.

The local profile-1 server keeps routing records that arrive without `org_id`.

## Server settings

`~/.config/of/server.yaml` (mode 600). Keys and defaults:

| Key | Default | |
|---|---|---|
| `host`, `port` | `127.0.0.1`, `18200` | |
| `request_timeout` | `60` | seconds a connection may sit idle in one read or write before the server closes it (2026-11-13) |
| `state_dir` | `~/.local/state/of/server` | `server.db`, `queue/`, `dead/`, `storage/`, `blobs/` |
| `plugins` | `storage: sqlite`, `buffer: disk`, `blobs: fs`, `identity: machine_keys` | see Profiles |
| `tenant_key_file` | `~/.config/of/tenant.key` | the master key; the masking key is derived from it |
| `tenant_key_create` | `true` | profile 1 creates the key on first start; Kubernetes sets `false` and mounts it |
| `tenant` | from `ship.yaml`, else `default` | |
| `vault_database`, `unassigned_database` | `of_vault`, `of_unassigned` | |
| `raw_to_org` | `false` | the gate above |
| `token_ttl`, `challenge_ttl`, `max_body`, `flush_every`, `max_attempts` | 3600, 60, 64 MB, 1 s, 8 | |

The routing keys fall back to the client's `ship.yaml` while the founder's Mac still keeps them there.

## Profiles

The client and the API contract are the same in every profile; the server differs only by the plugins chosen in `~/.config/of/server.yaml` (`bin/of_plugins.py`). Adding a profile adds plugin implementations, never a fork of the server (RFC 0001 §5.2a).

| Seam | Profile 1: one machine | Profile 2: self-hosted simple | Profile 3: self-hosted scalable | Profile 4: Organic Framework Cloud |
|---|---|---|---|---|
| storage | **SQLite** (default), or ClickHouse | ClickHouse | ClickHouse cluster | managed ClickHouse |
| buffer | disk queue | disk queue | Kafka / Redpanda | managed stream |
| blobs | file system | file system | S3-compatible | object storage |
| identity | machine keys | machine keys, TLS | machine keys + OIDC for people | machine keys + OIDC/SSO |
| runs as | launchd / systemd user service | one VM: compose or a single binary | Helm chart | hosted |

Built today: storage `sqlite` and `clickhouse`, buffer `disk`, blobs `fs`, identity `machine_keys`. An unknown plugin name stops the server at start, rather than falling back silently.

**The founder's Mac** runs profile 1 with ClickHouse storage:
- a launchd agent `dev.organic-framework.server` on `127.0.0.1:18200`, with its queue in `~/.local/state/of/server/`;
- the server holds the ClickHouse `*_ingest` credentials and the tenant master key;
- the shipper posts batches to it.

**On a Mac it survives a sleep, a wake and a lost network** (`of_conductor`, msg `01a1d63f-d434`, 2026-11-13). After a day asleep with no network, the Mac woke swapping (load 180 to 266, 13.2 of 14 GB of swap), and the server answered `/v1/health` only after 25 s until a person restarted it. Its launchd agent, as `of init` wrote it, was a `Background` job with `Nice 10` and low-priority I/O (its systemd unit had `Nice=10` and idle I/O): that class gets the least CPU and the slowest page-ins, so every answer came after its client had given up (198 replies to gone clients in that run's log, no deadlock). A day later, still swapping, health took 13 s. Now:
- `of server install` (and `of init`) writes and loads two launchd agents: the server at `ProcessType Standard`, with no `Nice` and no low-priority I/O, since agents wait on its answers; and `dev.organic-framework.server-watchdog`, one check a minute (`of server watchdog`). Agents already as they should be are left running. The writer child lowers itself (`nice 10`): the drain to storage can wait. `of server uninstall` removes both. The systemd unit `of init` writes no longer lowers the server's priority either; the watchdog is launchd's only so far.
- The watchdog restarts a server process that is alive but has not answered `/v1/health` (20 s timeout) through 3 minutes of checks, at most once in 15 minutes. First it sends `SIGUSR1`, which writes every thread's stack to `server.log`, so the next hang names where it waited; it records `of.server_watchdog` and logs to `<state_dir>/watchdog.log`. A server with no live process is left alone: `of server stop` meant it, and launchd's `KeepAlive` restarts a crash. A gap of more than two minutes between checks (a sleep) starts the count again, and the restart's time is kept before `launchctl` is called, so a call that hangs under load still waits 15 minutes (`of_review` on organic-framework PR 112).
- A connection idle in one read or write for `request_timeout` (60 s) is closed, so a client that went away in a sleep holds no thread.
- `server.log` lines carry their UTC time; replies to clients that had gone (broken pipe, reset, timeout) are counted instead of a traceback each, the count written with the next such reply a minute or more after the last line.
- The writer tells an unreachable storage (refused, reset, timed out, no route) from an error answer: the batch waits for the network, doubling the pause up to a minute, and spends no attempt. Before, a batch was dead after 8 failures one second apart, so a lost network killed every batch in about 8 seconds. A storage unreachable for 30 minutes raises one `of.server_alert` (`storage_unreachable`) to the owner.
- The relay is watched the same way (`of relay install|uninstall|watchdog`): its launchd agent runs at `Standard`, every loop (presence, each server's poll and coordination feed) stamps a heartbeat file, and a watchdog restarts the relay launchd runs (its pid from launchd, never from the heartbeat) when its loops have not moved for 15 minutes, above the longest bounded call to a server, through 3 minutes of checks, after a stack dump to `relay.log`.
- `tests/test_server_resilience.py` freezes a real server process as a sleep does while its clients time out, wakes it and expects an answer within 3 s; it takes the storage away and gives it back, and the batch drains.

**The agent host** (organic-framework-build reports/agent-server-migration-plan-2026-11-11.md §3 B2 and §9; `of_agent-server`'s hand-over `aba3d171`): each organization's agents run as their own OS user, and a hidden service user `_ofserver` holds of-server, ClickHouse, their passwords and `tenant.key`, none readable by an agent user. `deploy/host/setup.py` (as root) creates that user (home `/var/of-server`, 700), makes each organization user's home 700, writes `/etc/of` (the server's config readable by `_ofserver` only; ClickHouse on loopback, its admin in `users.xml` by hash; the grant key's public half pinned root-owned, since the host verifies grants and never signs them, `of_architect`, msg `01a1d66a-cd41`), makes every ClickHouse password (600, never printed), and writes the LaunchDaemons with `UserName`, not loaded: of-server, ClickHouse, a relay per organization user, and one root watchdog (`of_server.py host-watchdog`) that restarts a hung server or relay in the `system` domain. `plan` shows it, `apply` does it, `start`/`stop` load the daemons and `schema` loads `deploy/clickhouse/schema.py` at S3. `--org NAME=DATABASE` keeps each organization's database as the machines' `orgs.yaml` names it.

No organization user, and not `_ofserver`, reaches what root holds through the host setup (of_secrets-review and of_review on organic-framework PR 144):
- **The code root and `_ofserver` run.** Every action but `stop` runs as root and first checks the python with all of its Homebrew prefix (its libraries load from there by absolute path), ClickHouse and the release checkout `/opt/organic-framework`, which the script itself must be run from. That covers every folder they pass through, link by link, and the folders a site-packages `.pth` adds. Each path must be owned by root or an admin who is no organization user, and writable by no organization user and not by `_ofserver`. Writability is asked as each of them, groups and ACLs included. Otherwise the script refuses and does nothing. The privileged daemons run python with `-E -s`. A later change of owners or modes, such as an admin's `brew upgrade`, is not watched: re-run `plan` after changing that software.
- **What root writes.** Folders are opened one component at a time without following a link. Files are made new with `O_EXCL|O_NOFOLLOW`, after their old name is removed, and modes and owners are set on the descriptor. A link, a hard link or a FIFO where root reads a password is refused. So a re-run, or `schema`'s client file, never acts through a link that `_ofserver` put in `/var/of-server`.
- **What launchd and the watchdog open as root.** Every daemon's log is in root's `/var/log/of`, made beforehand and owned by the daemon's user (600), since launchd may open it before it drops to `UserName`. The watchdog keeps its state in root's `/var/db/of-host-watchdog` and takes every pid from launchd, of-server's included. It reads each relay's heartbeat without following a link or blocking: a regular file of that user's, at most 64 KiB, with any field of the wrong type read as no heartbeat. One check that fails never stops the others. It records no event, because root's own store is read by nobody: its log, `/var/log/of/host-watchdog.log`, is the record, and the machines see a restart in of-server's own health checks (`server.health`).

**A new user** gets profile 1 from `of init` in minutes, with SQLite storage and no other services ([`../README.md`](../README.md), quickstart).

**A code change restarts the server as a whole** (2026-11-07). Every 2 s the server checks the modules it has loaded from its own directory (`bin/`). When one of their files changed, has not been written for 2 s, and every changed file compiles, the server stops accepting, lets the requests in flight answer, stops its writer and re-executes itself: same pid, arguments and environment, so launchd, systemd and `of server start` all keep it. A file caught mid-edit or broken keeps the old code serving until it is fixed. A module not loaded yet is not watched: it is imported fresh when first needed. So a deploy of `bin/` never leaves the server with an old module beside a new one. That happened on 2026-11-07: `of_asks`, imported fresh, called `of_classify.q_v3`, which the server's old `of_classify` lacked, and Studio answered 500. This replaces Studio's own reload of its modules. A restart cuts long-polls and keep-alive connections, which the clients retry.

**Without a service manager** (a VM or a container shell without a systemd user session): `of server start` (or `of server run --daemon`) detaches the server in its own session with `OF_AUTOMATION=1` and no SSH agent, appends its output to `~/.local/state/of/server.log` (`--log` to change), and returns once `/v1/health` answers; it does nothing when a server already answers on the configured port. Every `run` writes its pid to `<state_dir>/server.pid` and removes it on exit; `of server stop` sends that pid SIGTERM (requests in flight finish, the writer stops, the queue stays on disk). Where the launchd agent `dev.organic-framework.server` is installed (a Mac after `of init`), `of server start` kicks that job instead of detaching a copy, so the server keeps its launchd log (`<state_dir>/server.log`) and comes back after a crash or a reboot; `stop` ends it with a clean exit, which the agent's `KeepAlive: {SuccessfulExit: false}` does not restart. A systemd unit with `Restart=always` comes back after `stop`: stop it through systemd.

## Open questions

1. Is a machine token enough for colleagues, or should we use mTLS from the start?
2. Long-poll or WebSocket for the relay? This draft uses long-poll: simpler, proxy-friendly and good enough at our message rate.
3. Masking may also be wanted on the machine side before upload, for colleagues who do not want raw to leave their machine even to the vault. Their consent already covers that: the raw tier is opt-in, so without it raw never leaves.
