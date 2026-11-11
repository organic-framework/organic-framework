# Storage of traces and events

Status: **Accepted v1**, 2026-11-05 (`of_architect`); the central store runs on the founder's Mac (founder, 2026-11-05). Written by `of_platform` after two founder decisions: capture the maximum ("every byte may be useful later") and keep it in a database, not in JSONL files. The central store is ClickHouse, as the founder proposed; `of_architect` accepted the design. Frame: [`framework.md`](framework.md), "The trace and sync system" and "Capture the maximum". Typed layer: [`events.md`](events.md).

## Two tiers

1. **Raw tier: every byte.** Everything the harnesses and the runtime produce, kept as produced and compressed. Nothing is dropped and nothing is masked.
2. **Typed layer: events.** The event kinds of [`events.md`](events.md), as rows that reference raw records. Metrics, `of asks`, `of who` states and the desk read this layer.

The typed layer can be rebuilt from the raw tier at any time, for example after a classifier improves. The raw tier cannot be rebuilt from anything.

## What is captured

| Source | Content | How |
|---|---|---|
| Claude Code transcripts | Every record of every session in every config dir: prompts, assistant messages, thinking where present, tool calls with their inputs and outputs, token usage, model, timestamps, `pr-link` records, subagent transcripts | Ingested incrementally by file offset: a detached ingest started from the Stop and SessionEnd hooks, and `of import` for history |
| Codex rollouts | `~/.codex/sessions` | `of import` |
| Claude Code hooks | The full hook JSON of every hook the collector receives: permission dialogs, notifications, exact prompt submit times, the herdr pane | `of hook` |
| herdr | The event stream: agent states, focus, pane moves | The desk router's subscription |
| Instructions in force | `settings.json`, AGENTS.md and CLAUDE.md files, memory files, briefs, registries | Content-addressed snapshots when they change |
| Model traffic (later, opt-in) | Exact requests and responses, including system prompts and tool definitions that transcripts do not hold | A local proxy through `ANTHROPIC_BASE_URL` |

Harnesses delete their own history (Claude Code's default `cleanupPeriodDays` is 30 days). On 2026-11-05 the founder set it to 36500 in every config dir, and `of archive` copied all transcripts into the raw tier.

## Local store: SQLite

One file per machine: `~/.local/state/of/of.db`, in WAL mode.

- **Why SQLite.** No daemon; it ships with Python; many hook processes can write at once (WAL, `busy_timeout`, tiny transactions); FTS5 gives full-text search over prompts and messages; JSON functions query payloads. DuckDB can attach the file read-only for heavy analysis.
- **Why hooks do not write to ClickHouse directly**, although it runs on the same Mac: tiny concurrent inserts from dozens of hook processes are an anti-pattern for it, and the hooks must keep working when it is down. DuckDB and chDB allow one writer process only.
- **The local store is the spool.** A shipper sends batches to the central store when there is one. The machine keeps working offline.

Tables (v1):

| Table | Rows | Key columns |
|---|---|---|
| `blob` | Content-addressed raw content, zstd-compressed | `sha256` (primary), `size`, `zdata` |
| `transcript_record` | One row per transcript line | `source_path`, `line_offset`, `blob_sha256`, `session_id`, `record_uuid`, `parent_uuid`, `type`, `timestamp`, `model`, `tool_name`, `input_tokens`, `output_tokens`; unique on `session_id` + `record_uuid` |
| `hook_record` | One row per hook call, raw JSON in `blob` | `received_at`, `hook_event`, `session_id`, `blob_sha256` |
| `runtime_record` | herdr events | `received_at`, `type`, `pane_id`, `blob_sha256` |
| `snapshot` | Instruction files when they change | `path`, `taken_at`, `blob_sha256` |
| `event` | The typed layer: one row per event of [`events.md`](events.md) | the attributes as columns; `payload` JSON; `raw_ref` |
| `event_fts` | FTS5 over masked text fields of `event` | |
| `ingest_cursor` | How far each source file has been read | `source_path`, `inode`, `offset` |
| `ship_cursor` | What the central store has confirmed | `table`, `last_rowid` |

Content addressing removes duplicates. A session copied into another config dir by an account move produces the same records, and they are stored once.

## Central store: ClickHouse

- **Why ClickHouse.** The data is append-only, wide and large (about 1 GB a day of raw transcripts from one founder). The questions are aggregations over time, positions and organizations. ClickHouse's columnar compression and `MergeTree` fit this. `ReplacingMergeTree` keyed on the record id makes re-shipping idempotent.
- **Tables** mirror the local ones, with `tenant_id`, `org_id` and `host_id` on every row. Raw content is stored in a `ZSTD`-compressed column, not as files.
- **The shipper** sends batches (thousands of rows, not single inserts) over HTTPS, records what ClickHouse confirmed, and retries. Moving the store to another host changes only the shipper's target.

## Tenancy and placement

- The store belongs to the **tenant** (the founder), not to an organization ([`ontology.md`](ontology.md): tenant ≠ organization). It holds the data of all his organizations: the OF build team, Acme and, later, colleagues' organizations.
- **Each machine names its tenant** in `~/.config/of/ship.yaml` (`tenant:`); the code's default is `default`, which a real tenant overrides, because shipped rows carry it as `tenant_id`.
- **One database per organization** (founder, 2026-11-05: "every app is a business"). The purpose is **clean data, not isolation on the founder's machine**: when colleagues join an organization, they see that organization's traces and nothing of the founder's other projects. The founder works with everything on one screen, under one macOS user, and as the tenant sees all of it by design.
  - Each organization's `orgs.yaml` entry names its `database`; each database has its own ClickHouse user, which can read and write that database only. Today: `of_build` and `of_acme`, each with its `_ingest` and `_read` users (see Roles). The tenant's user `of` administers all of them. A colleague gets the `_read` user of the organizations they joined.
- **Routing is what keeps the data clean.** The shipper puts each row into the database of its organization: from the session's position in the registry, else from the working directory (`paths` in `orgs.yaml`; for transcripts, Claude Code's project directory, scratchpads included). Rows of no known organization go to `of_unassigned`, which only the tenant reads. Nothing reaches an organization's database by default. On 2026-11-05, routing by path assigned 8.61 of 8.62 GB of transcripts; the rest were scratchpads, now covered by a rule.
- **Roles** (RFC 0001 §5.2). Each database has an insert-only user `<database>_ingest`, which the shipper uses. Organization databases also have a read-only user `<database>_read` that sees only views without raw content: `v_event`, `v_transcript_record`, `v_hook_record`. Colleagues get `_read`. Only the tenant's user `of` reads tables, the vault and `of_unassigned`. Credentials live in `~/.config/of/clickhouse/<user>.password` (mode 600).
- **The vault.** Until boundary masking exists (RFC 0002), unmasked raw records (transcript lines, hook payloads) are shipped only to `of_vault`, which only the tenant reads. Organization databases receive the same records without their content, plus typed events, which are masked at write. When masking is built, organizations receive masked raw.
- **Cross-organization work** (decision 7, founder 2026-11-05). A position that builds one organization while working on another's material (for example, `of_field-analyst` builds Organic Framework from Acme traces) does two kinds of work at once. The boundary is drawn by what a record holds, not by the session:
  1. **Content follows its source.** The position's registry entry says `serves: <org>`; the full traces of its sessions (texts, tool outputs, prompts, raw records) go to that organization's database.
  2. **Process follows the builder.** The position's own organization gets the **shape** of the same sessions: who worked, under which mandate, when, with which model and tools, how many tokens, who asked whom and how long the answer took, message lengths. No text, no tool output, no paths. Fields pass by an allow-list per record type (`of.shape: true`).
  3. **Results cross by publication**: a cleaned document plus `of.published` ([`events.md`](events.md), publication rule).
  4. **One session serves one organization.** Where a mandate is plainly double, the position is split; the steering layer enforces it later.
  Verified on 2026-11-05 with one `of_field-analyst` session: 1,228 records with 6.45 MB of content in `of_acme`; the same 1,228 records as shape, with zero content, in `of_build`.
- **Physically**, the founder chose his Mac (2026-11-05), with disk freed by the agents that own the space. Setup:
  - ClickHouse 26.10 from Homebrew, run by the launchd agent `dev.organic-framework.clickhouse` (background process type, low-priority IO, nice 10, restarted on failure);
  - localhost only, HTTP port 18123, native port 19000; config in `~/.config/of/clickhouse/` (mode 700), data in `~/.local/state/of/clickhouse/`;
  - capped for a laptop: 3 GB server memory, 2 GB per query, small background pools, uncompressed cache off, no system log tables;
  - the tenant's user `of` with a random password in `~/.config/of/clickhouse/of.password` (mode 600); one database and one user per organization, as above.
  Moving it to a server later changes only the shipper's target.
- **A colleague's machine** (a requirement for the sync system, not for v0). What syncs from a colleague's machine is under their control ([`framework.md`](framework.md): consent; EU AI Act Annex III):
  1. the shipper sends only the organizations the colleague has joined (their `orgs.yaml`), and only the tiers they agreed to: the typed layer always, the raw tier by opt-in;
  2. consent is recorded as an event (`of.consent`) that carries the version of the terms;
  3. the colleague sees what has left their machine (`of shipped`).

## Threat model

The raw tier concentrates every secret that has ever passed through an agent session in one place. Acme's retrospective has an incident in which a credential was recovered from a transcript.

| Threat | Control |
|---|---|
| Another local user or process reads the store | Directory mode 700, files 600; the store lives outside every repository |
| A stolen or lost laptop | FileVault, checked on 2026-11-05: on |
| An agent reads raw secrets | Agents never read the raw tier directly. They go through `of`, which masks its output (the patterns in [`events.md`](events.md), Secrets) |
| A secret leaves the machine | Mask at the boundary: shipping and export carry masked text unless the tenant opts a source in |
| An over-broad reader on the central store | Per-organization RBAC; read access to raw content only for the tenant |
| Theft of the server's secrets: the tenant master key (`~/.config/of/tenant.key`: grant signatures, secret tags) and the ClickHouse `*_ingest` credentials (`~/.config/of/clickhouse/`) | Mode 600 in a mode-700 directory, outside every repository; read only by of-server and `of` running as the tenant; never sent to machines (they hold only their own Ed25519 key and short-lived tokens). A leaked master key means re-signing all grants and re-tagging secrets under a new key |
| Loss of data between machine and store | Durable queue with fsync before 202; receipts; local raw is deleted only after `committed`. A batch that fails for good (`dead`) raises an owner notification and an `of.server_alert` event, and shows in `of who` and `/v1/health` |
| A backup or copy of the store | Backups of the raw tier are encrypted and stay in the tenant's control |

Masking is a safety net, not a guarantee. Seed phrases and free-form secrets are not detected.

## Disk and retention

Measured on 2026-11-05:
- Claude transcripts: 2,084 files, 8.6 GB. Codex sessions: 1.3 GB. About 1.1 GB written in the last 24 hours.
- zstd level 9 compresses transcripts 6.5×. Of the 3.66 GB of top-level session files, 2.1 GB are copies of the same sessions in several config dirs.
- Disk on the founder's Mac: 91 GB free (90 % used).

Rules:
- Until the central store exists, the raw tier stays on the machine, at about 150 MB a day after compression. `of` warns when free space falls below 50 GB: `of pulse` notifies the owner once per episode and again once a day while it stays low (the episode ends above 55 GB), and `of import` stops. `of.db` sets a 64 MiB `journal_size_limit`, since SQLite otherwise keeps the WAL file at its largest size (2026-11-08: 2.9 GB with 651 frames in use).
- **Session scratch folders** (2026-11-11, Acme through `acme_org`): free disk fell from 46 to 28 GiB in about seven hours, while `of.db` grew 25 MB. The space went to live sessions' scratch folders (`/private/tmp/claude-<uid>/<project>/<session>`): 49 GB, mostly full repository worktrees of 1.5 GB each and copies of data. `of pulse` (`bin/of_scratch.py`):
  - sizes each live session's folder at most every 30 minutes, within 45 seconds a pulse (`du -skx` at low priority; all 71 live folders took 6.6 s), and records the sizes with free disk as `of.disk_usage`;
  - tells a position once, in its own pane when it is idle, when its folder passes 3 GiB; it tells it again only after the folder fell under the cap. It re-sizes a folder over the cap every pulse;
  - past a 6 GiB quota (Acme's `platform_owner`: "a quota, not only a warning"; six positions freed about 17 GB only after being told), lists the session in `scratch-quota.json` in the state folder (`of_scratch.read_quota()`: session, position, paths, bytes, since) and tells the position. The guard reads that list to refuse new writes into the folder, while reads and deletions stay allowed, until the folder is back under 3 GiB (`of_server`'s rule, docs/guard.md);
  - files one card to the founder when free disk falls under 20 GiB, naming the largest folders, and closes the card itself once free disk is above 25 GiB again;
  - never deletes anything, because a scratch folder is its session's own work.
- Once the central store exists, local raw data follows **ship → confirm → delete**: a record is deleted locally only after the central store confirms it. The typed layer stays locally for 90 days for the local views.
- Harness transcripts themselves are no longer deleted by the harness (`cleanupPeriodDays: 36500`). Pruning them becomes safe once they are confirmed in the central store.

## Rollout

1. Done (2026-11-05): stop harness deletion; `of archive` copies all transcripts into `~/.local/state/of/raw/files/` (zstd, low priority, re-runnable).
2. `of.db` with the tables above; `of hook` writes to it instead of `events.jsonl`, keeping each hook within about 200 ms.
3. Transcript ingestion: detached, started from Stop and SessionEnd; `of import` for the archive and Codex.
4. `of` read commands (`asks`, `who`, search) on the typed layer, with masked output.
5. Central ClickHouse, the shipper, then retention.

### Forked sessions (2026-11-06)

A fork (a side question, a resume with `--fork-session`) copies its parent's records into a new transcript under its own session id. `transcript_record` keeps both copies (its key is `session_id` plus `record_uuid`), so a sum per session counts the copies twice. `session_fork (session_id, parent_session_id, shared, method, detected_at)` records each fork: of two sessions sharing records, the one whose transcript file was born later is the fork (`method: fv-0:birthtime`; `fv-0:first_record` when a file is gone). The view `transcript_own` is `transcript_record` without a fork's copies of its parent's records; `of derive` reads it. `of db forks` builds the `transcript_uuid` index once (it is never built on connect, so hooks never wait for it) and finds every fork; after that the ingest worker checks each new transcript.
