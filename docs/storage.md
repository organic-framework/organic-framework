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

### Transcripts in their own file (proposed, 2026-11-16, `of_platform`)

*Proposed for review by `of_review`, `of_server` and `of_capacity` before any code (`of_architect`, message `01a1e3c3-9800`, accepted the direction).*

**Why.** One SQLite file has one write lock. The hooks write `event` and `hook_record` into `of.db`, and so does the transcript ingest, into `transcript_record` and `blob`. On 2026-11-15 and 11-16 `hooks.errors` failed twice (18% and 15% of hook calls spooled within 10 minutes). Two causes were fixed: the GitHub import rewrote every record through a search-table scan (organic-framework PR 234), and a moved session ran the fork check over all its records (2026-11-16, `of_platform`). The cause left is the size of the store: at 12.9 GB (3.8 million transcript records, 3.9 million blobs, 6.1 GB of compressed content) and 92% swap, one transcript insert takes about 0.7 s, measured by the ingest's own breakdown (8 records of one file in 5.6 s; the fork check and the commit 0.0 s). Each insert updates the blob key and five transcript indexes, two of them in random order, on pages no longer in memory. The 0.25 s transaction cap cannot split one insert, so the hooks' short lock wait runs out and they spool.

**The measure** is read at the hook: the spooled hook calls per hour in `hook-errors.log` (lines with ` spooled`), and `hooks.errors` (the spooled and failed share of hook calls in 10 minutes, docs/health.md). Before: 761, 348, 491 and 215 spooled calls on 2026-11-13, 14, 15 and 16 (to 10:00Z). The change works when the spooled calls caused by the ingest go to zero. Calls spooled for another writer, such as the listener, are counted apart.

**Layout.** Two files, each with its own WAL and write lock:
- `of.db` keeps everything the hooks, the relay and the listener write: `event` and its search tables, `hook_record`, `runtime_record`, `snapshot`, the views and cursors, and the blobs those rows name.
- `of-transcripts.db` takes `transcript_record`, `session_fork`, the `transcript_own` view, `ingest_cursor`, and the blobs of transcript lines.

`of_store.connect()` attaches `of-transcripts.db` as `tx`. A hook call that writes only events connects without it, so it opens one file as today. After the move, `main` has no transcript tables, so an unqualified `transcript_record` resolves to `tx` (SQLite searches `temp`, then `main`, then the attached files) and readers keep their queries. `blob` exists in both files; `get_blob` looks in `main`, then `tx`.

**Writers**, each changed to name `tx` explicitly:
- `of_store.ingest_file` and `_insert_capped`: transcript rows, their blobs, `ingest_cursor`;
- `of_store.detect_forks`, `of db forks` in `bin/of`: `session_fork` and the uuid index;
- `of_store.redact_rows` and `drop_unreferenced`, with `of redact`: transcript rows and their blobs, in the file each row lives in;
- `of_merge_delta` (a host move's store merge, offline): it merges each file into its peer.

**Readers** that read transcript rows, unchanged except where noted:
- `bin/of_capacity.py` (the spend view by `id` cursor, the usage sums over `transcript_own`), `bin/of_spend.py`;
- `bin/of_derive.py` (sessions, Codex rows), `bin/of_asks_answer.py` (the assistant's last blob), `bin/of_paste.py`;
- `bin/of_credentials.py` and `bin/of_guard_replay.py` (blobs of tool calls in a window), `bin/of_tools.py`;
- `bin/of_studio.py` and `bin/of_studio_cli.py` (assistant turns per session in a window);
- `bin/of_ship.py` and `bin/of_route.py` (shipping by `id`, raw-consent gating), `bin/of_server.py` (its own tables on the server, not this file);
- `bin/of` (`of db forks`, `of redact`, `of db stats`).

None of them joins an event with a transcript row in one SQL statement; each reads one side and matches in Python. So no query has to span both files.

**The move, without an offline job.** A 12 GB copy run at once would hold the disk and the lock for long and spool every hook, so the move goes in small steps and only while the machine is idle:
1. `of-transcripts.db` is created empty. From then on new ingest writes there. Its `id` sequence starts above `main`'s largest transcript `id`, so the `id` cursors (`ship_cursor`, the capacity spend view) read on without a gap or a repeat.
2. During the move every connection defines temp views `transcript_record`, `transcript_own` and `blob` as `main`'s rows `UNION ALL` `tx`'s. Temp is searched first, so every reader sees all rows.
3. A mover copies old rows into `tx` by `id` range: rows and their blobs, as stored, keeping their `id`. It copies at most 2,000 rows in a transaction capped at 0.25 s, and runs only while the machine is idle: no hook spooled in the last 10 minutes, and load below the number of cores. The pulse runs it. Its cursor is a row in `tx`. Each copied range is checked by count and by its first and last `id`, then deleted from `main` in transactions capped at 0.25 s.
4. When `main` has no transcript rows left, its empty transcript tables are dropped and the temp views go. From then on unqualified names resolve to `tx`.

**Copy, not re-ingest.** The direction was to re-ingest old rows from the raw tier. The raw tier cannot give them all back. `of archive` copied every transcript once, on 2026-11-05 (2.5 GB in `raw/files/`), and nothing since. For later sessions, the harness's own transcript files are the only copy outside the store, and a session moved or removed since then has no such file. Re-ingest would also give the rows new `id`s, which the shipping and spend cursors would read as new. Copying the rows as stored keeps both: same content, same `id`s, and no parsing or redaction to run again.

**Disk.** Deleted rows' pages go to `main`'s free list, and the file does not shrink. During and after the move, `of.db` stays at 12.9 GB while `of-transcripts.db` grows to about 12 GB: about 12 GB more on disk until `main` is vacuumed. Free disk was 94 GiB on 2026-11-16, against the card at 20 GiB. A `VACUUM` of `main` afterwards rewrites only what is left there (about 2 GB of events, search tables and hook records). It is needed only for the disk, never for correctness. It holds `main`'s write lock while it runs, so the hooks spool for that time. It runs once, at night when the machine is idle, from the pulse. The owner is told in advance. The pulse's free-disk check runs before it, and the `VACUUM` waits while free disk is below 30 GiB, since it needs room for its copy. Event growth reuses the free list meanwhile.

**What is not changed.** The event log, its search tables, the views and every `event` reader stay in `of.db`, so the listener and the hooks' path keep their queries. The central store, ClickHouse, keeps its tables. `of_server`'s own store is a separate file and is not touched.

**Open for the review.**
- The idle rule's numbers: 10 minutes without a spooled hook call, load below the number of cores.
- Whether `blob` rows that both a hook record and a transcript line name stay in both files. They are rare, and content addressing makes the copies harmless.
- The move's pace, in batches per idle tick.

### Merging another machine's store (2026-11-15)

When positions move to another machine (the agent host move, organic-framework-build reports/agent-server-migration-plan-2026-11-11.md §3, step B3), the target's store takes one organization's rows from the source's after a cutover point. On the host each organization runs as its own OS user with its own store, so a merge names its organization (`--org`) and another organization's rows never cross (`of_review` on organic-framework PR 195). Its positions come from the target's own registry (`--orgs`, the target's organizations list), never from the source's labels; a session is the organization's when the target knows it as one of those positions' sessions, or does not know it and the source names only those positions for it. Each row moves by the key that names it on every machine: events by `event_id`, the organization's own, naming one of its positions and sessions when they name any; transcript records of its sessions by `(session_id, record_uuid)`; session forks of those sessions; hook records of those sessions by their content, since their row ids are per machine (an event's `raw_ref` to one is rewritten to the target's id). The blobs those rows name come along. Machine-level rows that name no organization's session stay: runtime records, snapshots, the pulse's and the capacity probe's records, and the ingest and ship cursors.

```sh
python3 bin/of_merge_delta.py --source <copy of the source's of.db> --target ~/.local/state/of/of.db --org <id> [--orgs <the target's orgs.yaml>] --since <cutover ISO> [--person-acts <event id>,…] [--dry-run] [--json]
```

A re-run adds nothing. The asks and hand-overs from the delta appear in the projections at once, since they read the log; `of listen rebuild` re-derives the listener's views in time order after a large merge. Run it on the target machine against a copy of the source's file: selecting by time reads the source's tables whole. **ClickHouse needs no copy of its own.** The merged rows come after the target's ship cursor, so the target's own `of ship` sends them through its own of-server, which masks them with its key; ClickHouse keeps an event by `(org, name, ts, event_id)` and a transcript record by `(session_id, record_uuid)` once, and a merged hook record becomes the target's own row. An event the source had from the coordination feed is never shipped by the target (its own machine ships it). This holds while every row of the organization in the source's ClickHouse came from the source's own store: on 2026-11-15 the laptop's of-server had one machine enrolled, itself, for both organizations. A source server that also took batches from another machine of the organization needs that machine re-pointed to the target (its `server:`) before the cutover, and a redaction the source's server ran after the cutover is run again on the target's.

**A merge never holds the target's write lock for long**, since every hook of the target waits on that lock at most 0.15 s and then spools (`of_platform`, message `01a1dc95-cee1`: a test merge held a store's lock up to 154.7 s, a whole table in one transaction). It takes the lock just before a write, never while it reads the source or checks the target, commits at the first row past 0.25 s and then sleeps 20 ms, so a waiting hook gets the lock, as ingest does; the summary prints how many transactions it took and the longest. A merge cut short is finished by running it again. **The source is never the running machine's own live store** (`of_store.db_path()`): the merge refuses it, so its long reads never compete with that machine's hooks. Merge from a copy (`sqlite3 <of.db> ".backup <copy>"`, taken at the cutover).

The source is a store every process of its machine's one OS user can write, so the merge takes no row's word for itself (`of_secrets-review` and `of_review` on organic-framework PR 195):
- **A blob is hashed before it is stored.** One whose bytes are not its address is refused, with the row that names it, and counted; it never takes the address of the real one.
- **Every field of a source row is its writer's word.** On B1's reverse the source is a store an organization's OS user writes, so no label, position or host id in it decides anything.
- **An event that changes a record by its id, or files itself under one** (an ask's answer, withdrawal, snooze or amend; a hand-over step; a diff's approval or application, which closes its card; a loop's step, or an ask or record filed for a work) crosses only when that record is the organization's: an ask, hand-over or diff on the target or in the same delta, a work in its registry. Any other is held and listed. **An id the target holds is decided by the target's own row**, judged by what the target records alone (its label, its position, a session the target records only with the organization's positions), never by a delta row that claims it; an id the target lacks is the organization's only when no other row of the delta also makes it. A delta proposal that reuses an id that is not the organization's is held too (`of_review` on organic-framework PR 195: a forged proposal reusing another organization's ask or diff id let its answer or application close that organization's card).
- **The acts of a person cross only by their ids.** The owner's answers, withdrawals, snoozes, hand-offs and labels, human prompts, prompt-origin corrections, diff approvals, hand-over accepts and verifies, and consents are what the trust paths read as a person's own (G24 names them). Every one in the delta is listed by name, id, position and host, and held unless `--person-acts` names its event id: run `--dry-run` first, check each, and name the ones that are the owner's. A host id is the source's own claim. Every other event name crosses: it is the history the projections, Studio and the analyses read. Grants carry their own signature, which every reader checks again.
- **The copy travels and lies as a secret does**: it holds every transcript of the source. Copy it over SSH with the agent key file (`SSH_AUTH_SOCK` empty), into a mode-600 file outside any repository, and delete it once the merge is done.
- An event with a fixed id (`of_events.stable_id`: an escalation, an expiry) that the target would write later for the same fact is kept once, the source's copy first; both machines mean the same fact by that id.

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
  - tells an organization's structure keeper once, in its pane when it is idle, when its own organization's live sessions in one project folder together pass 12 GiB, listing only those sessions with their positions and sizes (a keeper acts on its own organization's positions, never on another's sharing the folder), and again only after they fell under it; a project is named without the home folder's part, and the home folder itself as `~` (2026-11-14, a user organization's structure keeper: 24 GiB in one project folder, every session under its own quota);
  - sizes the worktrees inside each organization's checkouts (Claude Code's `<path>/.claude/worktrees/*` and the agents' own `<path>/.worktrees/*`, each also one level down, from `orgs.yaml` `paths`; on 2026-11-15 they held 46.0 and 53.5 GiB on the founder's Mac) on the same cadence and budget, and tells the organization's structure keeper once when its worktrees pass 12 GiB together, listing only its own (2026-11-14, `of_capacity`: ten such worktrees of about 1 GiB each took free disk from 35.8 to 18.4 GB in five hours, while every scratch folder stayed flat);
  - files one card to the founder when free disk falls under 20 GiB, naming what takes the space, largest first: swap (macOS's swap files share the disk; on 2026-11-14 they held 14 GiB and swung free disk by about 9 GiB as memory pressure rose and fell), OF's event store, the live sessions' temporary folders with their largest projects (an ended session's folder stays on disk and is not counted), and the worktrees inside the checkouts by repository; it closes the card itself once free disk is above 25 GiB again;
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
