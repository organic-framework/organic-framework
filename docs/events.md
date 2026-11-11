# Event log

Status: **Accepted v1**, 2026-11-05 (`of_architect`). Written by `of_platform`. Frame: [`framework.md`](framework.md), "The trace and sync system". Concepts: event with provenance ([`ontology.md`](ontology.md)). Registry: [`registry.md`](registry.md).

The event log is the first collector of the trace and sync system. Each machine writes its own log locally, and the sync system ships it later. The schema below is therefore an interface. Projections such as `of asks`, `of who` states, founder-load metrics and the desk router read it; future sync and export read it too. Change it only through `of_architect`, and keep it backward compatible: add fields, never repurpose them.

## Principles

1. **Append-only and immutable.** Events are never edited. A correction is a new event.
2. **Capture raw; type a layer above.** Every byte the harness produces is kept in the raw tier ([`storage.md`](storage.md)): transcripts, hook payloads, runtime events. Events are a typed layer above it. `of.transcript` references the raw record, and an event's payload stays small.
3. **Mask at the boundary.** The raw tier keeps content unmasked on the machine. Typed text fields are masked when written, and everything that leaves the machine (sync, export, an agent reading through `of`) is masked or explicitly opted in (see Secrets).
4. **Never get in the agent's way.** Hooks always exit 0, finish within about 200 ms, and drop the event rather than block or fail the session.
5. **OpenTelemetry names where they exist.** Attributes use OpenTelemetry GenAI and resource semantic conventions (`gen_ai.*`, `host.*`, `event.name`). Organic Framework attributes use the `of.*` namespace. One line converts to one OTel log record without renaming.
6. **Runtime-neutral.** Claude Code is the first harness. Codex and others map their hooks onto the same kinds.

## Where it lives

- `~/.local/state/of/events.jsonl` on each machine. Directory mode 700, file mode 600. Outside every git repository.
- One JSON object per line, UTF-8. Writers append under an exclusive `flock`.
- `~/.local/state/of/machine_id` holds a random UUID, created once per machine. It is not a hardware id.
- Rotation and shipping belong to the sync system. v0 does neither.

## Record

```json
{
  "of.schema": 1,
  "of.event.id": "0193f0c2-5b7e-7c1a-9d2e-4b1f3a6c8d10",
  "timestamp": "2026-11-05T15:42:07.123Z",
  "event.name": "of.human_prompt",
  "host.id": "<machine_id>",
  "host.name": "alex-mbp",
  "of.harness.name": "claude-code",
  "of.harness.version": "2.1.318",
  "of.harness.config_dir": "~/.claude-b",
  "gen_ai.provider.name": "anthropic",
  "gen_ai.request.model": "claude-opus-5-5",
  "gen_ai.conversation.id": "4f9c2a10-5b3e-4d8a-9c71-2e6f0b8d1a35",
  "gen_ai.agent.name": "of_platform",
  "of.org": "organic-framework-build",
  "of.workplace": {"runtime": "herdr", "pane": "w3:p5", "workspace": "w3"},
  "of.cwd": "~/organic-framework",
  "of.source": "hook",
  "of.transcript": {"path": "~/.claude-b/projects/-Users-alex-organic-framework/4f9c2a10-….jsonl", "offset": 1048576},
  "of.payload": {"origin": "human", "text": "…", "chars": 212}
}
```

| Attribute | Required | Meaning |
|---|---|---|
| `of.schema` | yes | Schema version, an integer. This document is version 1. |
| `of.event.id` | yes | UUIDv7 (time-ordered). Imported events use UUIDv5 over `session id + transcript record uuid + kind`, so a re-import is idempotent. |
| `timestamp` | yes | UTC, ISO 8601 with milliseconds. The time the hook fired, or the transcript record's time on import. |
| `event.name` | yes | The kind, namespaced: `of.<kind>` (see Kinds). |
| `host.id`, `host.name` | yes | The machine (OTel resource attributes). `host.id` is the `machine_id` above. |
| `of.harness.name`, `of.harness.version` | yes | `claude-code` and its version. |
| `of.harness.config_dir` | yes | The harness config directory, with `~` for the home directory. It identifies the account without recording an email address. |
| `gen_ai.provider.name` | yes | `anthropic` for Claude Code. |
| `gen_ai.request.model` | no | The model, when the hook knows it (`session_start`), else the last seen one. |
| `gen_ai.conversation.id` | yes | The harness session id. |
| `gen_ai.agent.name` | no | The position's canonical name, resolved from the registries through `holder.session`. `null` when the session holds no position. |
| `of.org` | no | The organization id of that position. |
| `of.serves` | no | The organization the position's current mandate serves (registry `serves`), when it differs from `of.org`. Routing to an organization's database follows `of.serves` first. |
| `of.shape` | no | `true` on the shape copy of an event: what the builder's organization receives of a session that served another organization. Its payload keeps only allow-listed process fields ([`storage.md`](storage.md), cross-organization work). |
| `of.workplace` | no | Where the holder runs: for herdr, its pane, tab and workspace ids from `HERDR_PANE_ID`, `HERDR_TAB_ID`, `HERDR_WORKSPACE_ID`. |
| `of.cwd` | no | The working directory, with `~` for the home directory. |
| `of.source` | yes | `hook` (live), `import` (from history), `of` (written by the `of` CLI) or `lab` (written by lab mocks and the owner persona inside a lab machine). Events with source `of` or `lab` carry no harness attributes. |
| `of.lab.run_id` | no | Set on every event of a lab run, from the environment variable `OF_LAB_RUN_ID`. A lab run is an organization `lab-<run id>` of the separate tenant `lab` ([`lab.md`](lab.md)). |
| `of.transcript` | no | `{path, offset}` of the harness transcript: the file size in bytes when the event fired. The matching record is the next one written after that offset. On import: `{path, uuid}` of the record. |
| `of.payload` | yes | Small and kind-specific (see Kinds). At most 8 KB. Text over the limit is cut, with `"truncated": true` added. |

## Kinds (v0)

| `event.name` | Claude Code hook | Payload | Used by |
|---|---|---|---|
| `of.session_start` | SessionStart | `source` (startup, resume, clear, compact), `title` | holder binding, `of who` |
| `of.session_end` | SessionEnd | `reason` | holder binding, `of who` |
| `of.human_prompt` | UserPromptSubmit | `origin` (`human`, `injected`, `scheduled`, `system`, `paste`, `unknown`), `channel`, `input_mode`, `reply_to`, `text` (masked), `chars`, `prompt_id` | founder load, relays, prompt history |
| `of.loop_hypothesis` | `of loop hypothesis`, and `of loop run`/`decision` for status updates | `work`, `id` (8-hex; also as `hypothesis`), `change`, `predict`, `cost` {`rung`, `runner_h`, `text`}, `voi` (low, med, high), `status` (open at creation). A later event with the same `id` carries only the new `status` and `status_at`: testing at its first run, kept, dropped or parked at a decision (RFC 0005 §4.1; docs/studio.md 5.6) | loops |
| `of.loop_run` | `of loop run` | `work`, `run` (id), `rung` (on the work's ladder), `hypothesis`, `metric` (default the target's), `value` (a number in the metric's unit), `accepted` (the rung's accept met), `result`, `evidence`, `queued_at`, `started_at`, `ended_at` (default now), `runner_h`, `tokens`, `session`, `model` (the session's), `source` (manual, or the runner's when derived: `of loop run --source cbx --run <job name>`; a `run` already recorded for the work is skipped, so a runner's sync can repeat); the fields docs/diagnose.md §4.1 reads | loops |
| `of.loop_decision` | `of loop decision` | `work`, `decision` (keep, drop, park, step_up, step_back, change_objective), `hypothesis`, `rung`, `metric` and `to_money` (both required for change_objective: the new metric and how it becomes money; `to_money` is also written as `money`, Studio's name), `note`. A hypothesis's status (open, testing, kept, dropped, parked) is derived from runs and decisions | loops |
| `of.prompt_origin` | `of db prompt-origins` | `event_id` (an `of.human_prompt`), `origin`, `evidence`: a correction from the transcript's `turnOrigin`; the asks projection applies it | asks |
| `of.agent_question` (task) | `of ask --task` | `task: true`, `steps`, `command`, plus the usual ask fields | work for the owner's own hands (a key, a login, a payment): an answer is his claim (`claimed_at`), never a closure; the asker closes it with `of.state_declared {done_task}` after checking the result, or reopens it with `{reopen_task, note}` |
| `of.agent_message_in` | UserPromptSubmit when the prompt is a peer message | `from` (name or address), `text` (masked, first 1 KB), `chars`; relayed: `via`, `authority_class`, `authority_detail`, `reply_to` | agent interactions, relays; a message from the position an ask was addressed to answers that ask (its `reply_to` names one, else the only one open to that sender) |
| `of.agent_message_in` (relay) | `of relay` on delivery, `of inbox --mine --take` | `from`, `msg_id`, `authority`, `delivery` (`delivered` into the pane, `pulled` by the agent itself, `deferred`, `failed`), `via: relay`, `chars` | delivery paths; a pulled message is never typed in afterwards (dedup by `msg_id`) |
| `of.inbound_queue` | `of relay`, when a position's queue depth or its oldest message changes (from the `PUT /v1/presence` answer, every 60 s) | `org`, `position`, `depth`, `oldest_created_at` | inbound load per position: depth and age (of_studio's measure) |
| `of.agent_message_out` | PostToolUse, tool `SendMessage` | `to` as given, `to_position` (resolved), `delivery` (delivered, held, expired, unresolved), `carries_owner_words`, `text` (masked, first 1 KB), `chars` | agent interactions, addressing failures |
| `of.injected_prompt` | written by the injecting tool through `of event` before it types into a pane | `injector` (position or tool), `target_session`, `text_sha256`, `reason` | tells injected prompts from the founder's |
| `of.state_declared` | written by a supervisor through `of event` | `state` (`suspended`, `resumed`), `reason` (budget, account move, …) | states; a SIGSTOP shows as suspended |
| `of.compaction` | PreCompact | `trigger` (auto, manual) | session lifecycle |
| `of.attention_routed` | written by `of desk` | `action` (`bring_in`, `evict`, `focus`, `notify`), `pane`, `status`, `replaced` or `by` or `previous`; from 2026-11-09 `case` (the case title), `score` and `why` on `bring_in` and `evict` (docs/desk.md) | the owner's attention as a budgeted resource; focus metrics |
| `of.founder_correction` | `of corrections judge` (`bin/of_corrections.py`), started by the listener after the founder types into a position's pane (RFC 0006 §2.4) | `position`, `prompt_id` (the `of.human_prompt` event id: one correction per prompt), `rule` (one general English line), `kind` (`output`, `method`, `scope`, `fact`, `process`, `other`), `repeat_of` (the event id of the position's earlier correction of the same rule, else null), `quote` (his words, first 300 characters), `reason`, `judge` (`fc-v1`), `model`, `ms`, `cost_usd`; the row's time is his prompt's | the structure keeper's daily digest; the repeat-correction rate in Studio (`of_measures.repeat_corrections`) |
| `of.case_inferred` | `of desk case --infer` (`bin/of_case.py`), started by the listener after the founder types into a pane (docs/desk.md) | `cases` (up to 3, the current first: `title` and `summary` in Russian, `members` [{`position`, `weight` 0..1, `why`}], up to 8), `version` (`c-v1`), `model`, `account`, `ms`, `cost_usd`, `trigger` (`prompt`, `refresh`, `cli`), `window_s`, `prompts`, `last_prompt`, `focal`, `digest_chars` | the desk router fills the desk from the latest one |
| `of.published` | written by `of event published` | `from_org`, `to_org`, `artifact` (path and revision), `grant`, `note` | results crossing an organization boundary (decision 7); shipped to the databases of both organizations (see the publication rule) |
| `of.ask_withdrawn` | written by `of asks withdraw` | `ask_id`, `reason`, `by` (`owner` from the founder's terminal, else the calling position) | ask lifecycle: the owner withdraws a dead-end ask, or the asker one that is no longer needed |
| `of.mandate_check` | the mandate router, enrolled positions only (observe mode runs on PostToolUse, so it adds no latency before a tool; enforcement will move to PreToolUse) | `verdict` (`in_mandate`, `owned_by:<position>`, `in_handover:<id>`, `unowned` (a file path no system claims), `unscoped` (a Bash call whose working directory names no system), `outside` (a path under no organization's root: scratchpads, the harness's memory), `critical`; from the guard also `warn` (a rule in warn mode: the call ran, with a note to the agent) and `org_rule` (an organization's critical deny grant denied the call; docs/guard.md O2), both with `grant_id` when an organization's grant decided; `ask_dialog` (a session holding a position called AskUserQuestion, denied: it asks its owner with `of ask`, 2026-11-10), and `grant_notes` when a kept deny is missing or altered in its file); paths inside git worktrees (`<repo>/.claude/worktrees/<name>/…`, `.worktrees/<repo>-<name>/…`) are read as the repository's, `decision` (`admit`, `route`, `escalate`, `deny`), `grant_id`, `tool_name`, `path`, `system`, `system_org`, `keeper`, `non_live:<lifecycle>` (a keeper-less system that is unsupported or retired; rt-v5), `mode` (`observe`, `enforce`), `router_version` (`rt-v5`; bumped on every rule change), `command_class` (`runner`: a job on a runner such as `cargo cbx` or runners; `monitor`: polling or following, such as a sleep loop, `until`, `watch`, `tail -f`, `logs -f`, `gh run watch`; `other`; RFC 0005 rule 2.20) | steering; routing work to its owner. Only a critical deny grant denies; everything else is admitted, with context added for `owned_by`, and `unowned` goes to the organization's structure keeper. Replaces the earlier name `guard_decision` |
| `of.machine_enrolled` | written by `of server enroll-machine` | `machine_id`, `orgs`, `raw_orgs` (organizations whose raw tier the person consented to), `tenant` | machines and consent ([`server.md`](server.md)) |
| `of.server_alert` | of-server | `kind` (`dead_batch`, …), `batch_id`, `attempts`, `error` | data loss and server health, raised to the owner |
| `of.consent` | of-server, at enrollment and on `PUT /v1/consent` | `machine_id`, `person`, `tiers` (`typed`, `raw`), `terms` (version), `via` (`enroll`, `update`), `withdrawn` (tiers narrowed), `effect_on_shipped` | consent per organization and machine ([`server.md`](server.md), Machines and consent) |
| `of.machine_revoked` | of-server, `DELETE /v1/machines/…` | `machine_id`, `person`, `by` | withdrawing a machine or a colleague |
| `of.secret_detected` | of-server, the boundary masker; tenant-only store (the vault), never an organization's database | `kind`, `hmac` (8 hex), `org`, `count`, `batches`, `first_seen`, `last_seen`, `tables`, `last_location` (a record identity); never the value | secret review and rotation (RFC 0002) |
| `of.message_sent` | of-server relay | `msg_id`, `to`, `from` (`<org>:<position>`), `authority`, `reply_to`, `sender_machine`, `sender_person`, `chars`, `secrets_masked`, `priority` (`normal`, `fyi`, `urgent`), `text` (masked; the recipient's organization only, the sender's gets a slim copy without it) | messages between positions ([`server.md`](server.md), Relay and presence) |
| `of.message_delivered`, `of.message_failed`, `of.message_expired` | of-server relay | `msg_id`, `to`, `machine_id`, `reason`, `failures`; on a take (`of inbox --mine`) also `via: "taken"` and `session_id` | delivery of relayed messages |
| `of.studio_viewed` | of-server, the `/studio/` mount: one per page shown (HTML, status 200; not the stylesheet, not 401/403/404/503) | `viewer` (id), `tenant`, `scope` (`org`, `unit`), `owner` (the unit's owner or null), `view` (`inbox`, `works`, `owner`, `coverage`, `structure`, `waits`, `review`), `org` (the `org` parameter, or `all`), `requester` (`agent:<position>`, `agent:session:<sid>` or `agent:invalid` when an agent's process minted the link, given in `POST /v1/studio/link`; null for the owner's own opens); no other query, no paths, no content | the owner's use of Studio against status polls to agents (success.md, goal 1). In the organization shown; a view of all organizations goes to the tenant's own (`tenant_org`), else the tenant-only unassigned database |
| `of.continuation` | Stop, when the manifest changed | `session_crons` (CronCreate, ScheduleWakeup, `/loop`: what will wake the session), `background_tasks` (work in flight), `crons`, `tasks` (counts); text masked | holder moves re-create continuation on the target machine (RFC 0001 §5.8, lesson R3); `of who` can show sessions that are paused, not done |
| `of.secret_reviewed` | written by `of leaks mark` (alias `of secrets`) | `hmac` (the secret's tag under the tenant key), `rule`, `kind`, `decision` (`rotate`, `false_positive`, `rotated`), `note`, `orgs`; never the value | the rotation list (RFC 0002); tenant only |
| `of.state_declared` | written by `of status` (and by supervisors, `suspended`/`resumed`) | `state` (working, waiting, idle, done, suspended, resumed), `waiting_on` (founder, a position, external:<who>, resource:<what>), `note` | who waits on whom; never closes a declared ask (only a heuristic one moves on) |
| `of.stall_detected` | `of pulse`, once when a position becomes stalled, or when relayed messages wait for it over 30 minutes (`cause: inbound_queue`, `depth`, `oldest_min`) | `position`, `idle_since`, `evidence` | stalls over time |
| `of.nudge_sent` | `of pulse`, when it nudges an idle peer | `from_position`, `to_position`, `about`, `pane` | who nudged whom, about what |
| `of.pulse` | written by `of pulse` each run | `observed_at`, `positions: [{position, kind (waiting_owner, waiting_peer, stalled), on, since, ask_ids, evidence}]` | the attention projection over time (Studio) |
| `of.mandate_changed` | `of apply` and `of pulse`, when a position's registry entry differs from its last record (first run: a baseline per position) | `position`, `mandate: {path, revision}` (sha256 prefix of the brief), `summary`, `status`, `throughput`, `serves`, `changed[]`, `first`, `issued_by`, `reason` | the model at time t (RFC 0001 v3 §5.2) |
| `of.holder_bound` / `of.holder_unbound` | `of apply` and `of pulse`, from the live bindings | `position`, `session`, `pane`, `previous_session` | holder lifecycle |
| `of.handover_proposed`, `_offered`, `_accepted`, `_declined`, `_blocked`, `_delivered`, `_verified`, `_rejected`, `_closed`, `_returned`, `_withdrawn`, `_expired` | `of handover …` by the actor the transition allows ([`handover.md`](handover.md) §1.2); `_offered` and `_expired` by `of pulse` | proposed: `id` (= the event id), `from`, `to`, `title`, `done_when`, `verify`, `due`, `priority` (`blocking`, `normal`), `mandate`, `scope` {`systems`, `paths`}, `context`; the others: `id`, `by`, `note`, `result` (delivered), `on` (blocked); every id after the proposal is deterministic (hand-over id + kind + repeat), so a relay mirror writes the same event | work between positions and owners: response windows, first-pass verification, rejections, returns; a blocking hand-over is the requester's `waiting_peer`; an accepted hand-over's scope turns the router's `owned_by` into `in_handover:<id>` |
| `of.issue_labelled` | `of issues label <id> <label> [--note]`: the owner (a shell that is no agent's session, `by: owner`) or a position the owner delegates (`--from`) | `issue_id`, `rule`, `rule_version`, `label` (`true_positive`, `false_positive`, `true_but_not_now`, `wrong_remedy`), `note`, `by` | the only stored kind of the anti-pattern catalogue ([`diagnose.md`](diagnose.md)): issue state in Studio, calibration per rule version |
| `of.diff_proposed`, `of.diff_approved`, `of.diff_applied` | `of diff propose` (with an ask to the owner), `of diff approve` (the owner, in a terminal; an agent session or a script is refused) or the owner's `approve` answer to that ask in the popup, `of diff apply` | proposed: `id`, `kind`, `tier`, `org`, `author`, `digest` (of the diff's content), `diff`, `ask_id`; approved: `id`, `digest`, `by: owner`, `machine`; applied: `id`, `kind`, `tier`, `org`, `digest`, `files`, `review_due`, `predicted`, `continuation`, `diff` | structural changes as one format ([`handover.md`](handover.md) §3): an approval holds only for the exact content it saw; the review compares `predicted` with what happens. `of.mandate_changed` and holder events follow from the applied registry |
| `of.ask_escalated` | `of pulse`, for an ask routed to a delegated unit's owner; `of ask` itself when a rule fires at filing (`ask.blocking`, a declared tag), so the frame typed to the unit owner says it is escalated; `of ask --escalates <id>` with `rule: by_owner` | `ask_id` (the routed ask), `from_owner`, `to: founder`, `rule` (the `escalate_when` that fired, or `by_owner`), `delegation`; a new `of.agent_question` {`source: escalated`, `escalated_from`, `delegation`, `to: founder`} follows, except for `by_owner` | the delegator is asked only on the delegation's conditions; the routed ask's state becomes `escalated`. The unit owner is told once (in the routed frame, or by `of pulse`'s redelivery pass for a later escalation) not to file it again: its rewrite for the founder, `of ask "…" --escalates <id>`, carries `escalated_from` and supersedes OF's copy, which is in the asker's words (acme_org, 2026-11-08: platform_owner refiled net_keeper's blocking 878829d1 as 2dfeedbf, and the founder held two cards for one decision). The founder's answer to the copy or the rewrite reaches both the asker and the unit owner |
| `of.escalation_due` | `of model escalations --org O --write` (Studio, run by `of pulse` for organizations with a delegation) | `delegation`, `unit`, `condition` (issue severity, budget, envelope), `from_owner` (the unit owner), `to` (the delegator), `evidence` (issue ids) | a delegated unit's condition fired; `of pulse` adds the founder's copy of the question (`of.agent_question` {`source: escalated`, `escalated_from`}) once |
| `of.agent_question_judged` | `of asks judge` (`bin/of_judge.py`), started by the Stop hook and by the pulse | `ask_id` (the heuristic `of.agent_question`), `verdict` (`owner_question`, `not_a_question`, `skipped`, `error`), `question` (one Russian line), `options`, `reason`, `model`, `account`, `ms`, `cost_usd`, `judge` (`j-v1`), `origin` (who started the turn), `error` | opens or closes a heuristic candidate in the asks projection |
| `of.question_labelled` | the asks popup, key `n` on a heuristic question | `ask_id`, `ask_ids`, `label: not_a_question`, `kind`, `text` (first 600 characters), `by: owner`; the ask closes with `of.agent_question_answered {via: asks-ui, verdict: not_a_question}` (state `not_a_question`) | the owner's labels calibrate the heuristic; its version (`q-v2` now) goes up after a re-calibration |
| `of.agent_question` with `detected_by: blocked` | the listener (`BlockedProcessor`, `bin/of_blocked.py`), from the screens of agents that are not working, Claude Code's waiting state and herdr `blocked` | `blocked_type` (`trust`, `mcp_auth`, `press_enter`, `limit`, `login`, `exit`, `permission` (Codex only; Claude Code's come from the collector), `menu`), `pane`, `agent`, `fingerprint`, `snippet` (the last 15 lines, masked), `options` [{`key`, `label`}], `folder` (trust), `to` (founder; `of_capacity` for limits and login when it is live), `since`, `delivery` (`fyi` from 2026-11-09 for a limit routed to `of_capacity`: one `of send --fyi` from `of_platform` to its inbox, never typed into its pane; a login stays typed) | no session sits blocked unseen. The ask's kind is `blocked:<type>`. An answer in the popup sends the option's keys to that pane only while the fingerprint still matches (`agent_question_answered {via: asks-ui, keys}`); folder trust is never confirmed automatically; the ask closes when the dialog is gone (`via: reconcile, reason: dialog_closed`) |
| `of.artifact_read` | the ingest worker (live) and `of derive` (history), from transcript tool calls | `reader`, `path`, `system`, `system_org`, `keeper`, `tool` (Read, Grep, Glob), `tool_use_id`; only when the path's system is kept by another position | coordination without a message: socio-technical congruence (RFC 0001 v3 §5.3) |
| `of.name_resolution` | written by `of go` and later by message routing | `query`, `matched` (name or alias), `result` (resolved, none, ambiguous), `candidates` | addressing failures |
| `of.agent_question` | PreToolUse, tool `AskUserQuestion`; Stop, when the last message asks a question; `of ask` (`detected_by: declared`, with `to`, `blocking`, `options`, `topic_key` for deduplication; from a delegated unit, `asked_to: founder` when `--to founder` went to the unit owner, or `escalate_rule` and `delegation` when a declared tag sent it to the founder at once); closed by `of.agent_question_answered` (also written by `of asks answered`, with `ask_ids`), by an `of.human_prompt` with origin human into the asker's session only when it is the popup's answer (it closes the ask it names); from 2026-11-07 the owner's free text closes no declared ask, it marks the session's open asks seen (`seen_at`, `seen_by` in the projection) and the UserPromptSubmit hook lists them to the agent, which closes the one the words answered with `of status working --answered <id>` (before that date free text closed the session's declared ask when exactly one was open), or by `of.ask_withdrawn`. **A status declaration (`of.state_declared`) never closes a declared ask** (decision 2026-11-06: agents declare `of status` at the end of every turn, so this closure dropped unanswered questions); only heuristic asks close on a declared status | `detected_by` (`tool` or `heuristic` with `classifier_version`), `tool_use_id`, `questions`: header, question text (masked) and option labels for each | ask queue, waits |
| `of.permission_resolved` | the collector's PostToolUse / PostToolUseFailure fast path, when the session had an open permission request | `reply_to` (the request), `tool_name`, `outcome` (`ran`, `failed`) | closes the permission ask: the asked-for tool ran |
| `of.agent_question_answered` | PostToolUse, tool `AskUserQuestion`; `of asks answered`; the asks popup (`of asks --watch`) | `tool_use_id`, `answers` (masked); or `ask_id`, `ask_ids` (the group it closes), `by: owner`, `answer` (masked) and `via: asks-ui` when answered in the popup; `via: reconcile`, `reason` (`orphaned`: the session has no process; `resolved_elsewhere`: it no longer waits) when `of pulse` or the popup finds the session moved on (`bin/of_asks_live.py`, from `claude agents --json` per config dir and herdr `blocked`) | ask queue, waits. A popup answer is also typed into the pane of every asker in the group, declared as the owner's own words: its `of.human_prompt` has `origin: human`, `channel: asks-ui`. That prompt is the delivery's record: an answer that has none is typed into the asker's pane when it is idle, for 24 h, and a question `of ask` routed to a unit owner the same way (`of_asks_answer.redeliver_pass`; acme_org, 2026-11-08: 5 of 61 answers in a day never arrived). It is typed when the asker ends its turn (the listener sees `of.turn_end` and runs `of asks deliver <position>`), and `of pulse` retries before any nudge of its own; one prompt carries all the founder's answers due to that pane, oldest first (acme_org, 2026-11-09: four answers given at 10:35-10:37Z while platform_owner was busy reached it at 10:56 and 11:01). Only an `of.human_prompt` with origin human closes a position's asks; relayed and injected text does not |
| `of.permission_request` | PermissionRequest | `tool_name`, `summary` (masked, 200 characters: the command, file path or URL) | ask queue, grants |
| `of.permission_denied` | PermissionDenied | `tool_name`, `summary` | ask queue, grants |
| `of.attention` | Notification, except `permission_prompt` | `notification_type` (`idle_prompt`, `elicitation_dialog`, …), `message` (masked) | desk router, states |
| `of.turn_end` | Stop | `last_message` (masked, first 1 KB), `last_message_chars` | states, desk router, ask queue, waits |
| `of.turn_error` | StopFailure | `error` | states |
| `of.holder_changed` | SessionStart, written by `of`; `of capacity move` (a Workplace change: same session, another account) | `position`, `previous_session`, `new_session`, `evidence`; a move adds `workplace` and `previous_workplace` {`config_dir`, `account`, `pane`}, `model`, `effort`, `files`, `memory` | registry upkeep (the owner confirms) |
| `of.capacity_balanced` | `of capacity balance` (and `of pulse` with `auto: true`), one per move | `position`, `session`, `from`, `to`, `rate` ($/h at list), `reason` (`rescue`, `relief` or `fill`), `done`, `error`, `pane` | which capacity was spent where, and why ([`capacity.md`](capacity.md)) |
| `of.capacity_stall` | `of capacity balance`, when the set of stalled holders changes, the empty set included | `stalled`: [{`session`, `position`, `account`, `since`, `why`}], `accepting`: [config dirs] | holders still stopped on a usage limit after a run while those accounts accepted work: the invariant broken, and why; an empty `stalled` ends a stall, so its duration is measurable ([`capacity.md`](capacity.md), "The invariant") |
| `of.disk_usage` | `of pulse`, when it sized at least one live session's scratch folder (each at most every 30 minutes; `bin/of_scratch.py`) | `free_bytes`, `base`, `scratch: [{position, org, session, bytes}]` | disk as a resource: whose scratch grows, how fast free space falls ([`storage.md`](storage.md), "Disk and retention") |
| `of.capacity_snapshot` | `of capacity status`, once per fresh probe of a config dir (at most every 30 minutes) | `config_dir`, `account`, `logged_in`, `status` (`allowed`, `allowed_warning`, `rejected`), `overage`, `windows` {`five_hour`, `seven_day`: `utilization` (percent), `status`, `resets_at`}, `probe` {`model`, `cost_usd`, `tokens`, `duration_ms`}, `error`, `positions`, `others`, `sessions`, `busy` | capacity as a budgeted resource ([`capacity.md`](capacity.md)): utilization over time, the cost of measuring it, who ran where |
| `of.capacity_reboot_prepared`, `of.capacity_reboot_restored` | `of capacity reboot prepare` before a restart of the Mac, `of capacity reboot restore` after it | prepared: `sessions`, `tmp_kept` {`files`, `bytes`, `failed`}; restored: `resumed`, `failed` (names) | a restart's cost in sessions and how many came back ([`capacity.md`](capacity.md)) |
| `of.grant_key_rotated` | `of grant rekey` (`of_server`; hand-over 4c7ac78f): the tenant's grant-signing key moved into 1Password's SSH agent and every grant re-signed with it | `grants` (how many were re-signed), `files` (how many grant files changed), `fingerprint` (the new key's SHA256 fingerprint, the public part only) | when the signing key changed, and which fingerprint `of registry check` should now see on every grant |
| `of.secret_used` | `of secret run`, each time a position asks its broker for a credential (`bin/of_secret.py`, `of_server`; [`agent-secrets.md`](agent-secrets.md)) | `credential` (the catalogue id), `vault` (the vault's name, never an item reference), `decision` (`allow` or `deny`), `reason`, `person` (when a person holds the position). The envelope carries the organization, the position, the session and the machine (`host_id`). Never a value or a full reference | who used which credential where, and every refusal |
| `of.secret_rotated` | `of secret rotate` | `credential`, `by` (the position or person who rotated it), `trigger` (`schedule`, `person_change`, `breakglass`, `exposure`, `leaver`, `host`) | rotation history per credential |
| `of.secret_breakglass` | a break-glass grant to a credential (`of secret`, `of_server`) | `credential`, `grant_id`, `expires` (ISO time), `reason`; the position is the envelope's | break-glass uses, each followed by a rotation (`trigger: breakglass`) |
| `of.finance_fact` | written by `of event finance_fact`, a finance-unit position under a grant with `data: [finance]`; sent straight to the organization's server, the local spool keeps a stub | `period`, `kind` (`T`, `OE`, `I`), `category`, `product`, `amount`, `currency`, `amount_usd`, `fx`, `basis`, `source`, `supersedes`, `grant_id` | owner-only finance data: NP, T/OE, ROI and the value of an owner minute (see Finance facts) |

Notes:
- **Permissions come from the PermissionRequest and PermissionDenied hooks**, not from Notification(`permission_prompt`). These dedicated hooks carry `tool_name` and `tool_input`. Notification feeds only `of.attention`.
- **`of.subagent`** (record field, and `subagent` in the payload since 2026-11-06): `{id, type}` from Claude Code's `agent_id` and `agent_type`, set on every hook event a subagent's call produces (PreToolUse included, so a guard deny names it). The hook's `session_id` is the PARENT session's: a subagent never appears as a session of its own. Its transcript is `<config>/projects/<key>/<parent session>/subagents/agent-<id>.jsonl`, which live ingest reads with the parent's. A subagent's `of.agent_question` (Claude Code's prompt-suggestion side queries) is not an ask (`of_asks.relevant`); its `of.permission_request` is.
- **`origin` of a prompt**: `human` when typed by a person; `peer` prompts go to `of.agent_message_in`. Further values are `system` (task notifications, scheduled wakeups, hook-injected prompts) and `paste` (the prompt is mostly pasted content). The classification rules are fixed when the collector is coded, from real prompt shapes, and listed here.
- **Prompt fields**: `channel` is `herdr` (from `HERDR_ENV`), `terminal` or `remote` (when the harness reports a remote trigger). `input_mode` is `typed` or `pasted` (from the transcript's paste markers). Dictation cannot be told from typing in v0. `reply_to` is the `of.event.id` of the session's open ask, if one is open.
- **Asks are mostly free text.** Most agent questions are a final message ending in a question, and most sessions skip permission dialogs, so approvals are typed in chat (`of_field-analyst`, baseline). On Stop, when any question classifier (`q-v0`..`q-v4`) fires on the last message, the collector writes `of.agent_question` with `detected_by: heuristic` and a `classifier_version`; the next human prompt of that session gets it as `reply_to`. The event keeps the last 600 characters (`question`, cut from the end) and the options it lists as (a)/(b), in Latin or Cyrillic letters (`options`). **From 2026-11-07 (founder, ask `ddd6e3a3`) such a question is only a candidate**: the asks projection gives it state `candidate`, shown to nobody, and the judge (`bin/of_judge.py`, `of asks judge`) decides. The hook queues the closing message (masked, mode 600 under `~/.local/state/of/judge/pending`) and starts the judge in the background; a small model (Haiku through `claude -p` with no tools, hooks, MCP servers or session file, on the agent's account unless of_capacity's last reading says it is spent) answers whether the agent waits on its owner, with the decision in one Russian line. The verdict is `of.agent_question_judged`: `owner_question` opens the candidate (`judged`, its text the judge's line, its options), `not_a_question` or `skipped` (the agent filed an `of ask` in that turn) closes it as `not_a_question`, `error` leaves it for the pulse (up to 3 tries, within 24 h). When the agent waits on the owner without an ask, OF types a correction into its pane (`of.nudge_sent {kind: ask_correction, ask_id}`): at once after a turn a peer or a tool started, after 10 minutes when the founder started it, at most once per session in 30 minutes, never after the session went on. A judged question still open after 30 minutes is shown in the popup's own section below the asks, without counters, tokens or notifications. A wait on the founder needs an ask: `of status waiting --on founder` is refused unless the position has an open ask or task to him, or names the open ask it waits on with `--ask <id>` (recorded as `ask`).
- **Publication rule** (decision 7): `of.published` goes to the databases of both organizations. The source's copy is complete. The receiving organization's copy carries only the artifact reference, the grant and the note, cleaned the same way as the published document; references into the source's traces (session, transcript record, tool call) stay in the source's copy only.
- **Injected and scheduled prompts**: the account mover and the budget governor type into panes, and their prompts look like the founder's (22–31 a week in the baseline). Each injecting tool calls `of event injected_prompt` first. Agents send to panes through `of send`, a thin wrapper over `herdr agent prompt` that records the sender position the same way. The collector marks a prompt `origin: injected` when the session and text hash match an injection from the last 60 seconds. Prompts the harness marks as scheduled or meta (cron, wakeups, task notifications) get `origin: scheduled`. Live, the prompt's own transcript record (with `turnOrigin`) is written just after UserPromptSubmit, so the collector reads the transcript's tail back to the previous turn: a `system/scheduled_task_fire` record holding the same prompt makes it `scheduled`; a transcript it cannot read makes it `unknown` (2026-11-06: a cron prompt recorded as `human` closed the founder's ask c54997b8). `of db prompt-origins [--since] [--apply]` checks the recorded prompts against their transcript records and writes `of.prompt_origin` corrections. Only `human` and `paste` prompts answer an ask; `scheduled`, `system`, `injected` and `unknown` close nothing. A raw `herdr agent prompt` without the wrapper cannot be told from typing in v0.
- **Relayed authority**: `of.agent_message_out` carries a classified label `carries_owner_words` (the message quotes the owner or claims his yes), with `classifier_version`. The owner's name, besides "founder" and "owner", and an organization's own words in the classifiers (names of its sessions' work, its runner commands) come from the machine's `~/.config/of/classify.json` (`name_refs`, `capacity_words`, `owner_names`, `runner_commands`, regex fragments; `bin/of_classify.py` `TERMS`), so the published code names no organization. It is JSON because the guard's hook imports the classifiers under interpreters without PyYAML. The relay chain and the grant reference come later.
- **An open ask** is an `agent_question` or `permission_request` that no later event of the same session has followed. A `human_prompt`, `agent_question_answered`, `permission_denied` or a tool event closes it. The ask queue and the waits are projections; they are not stored.
- **`holder_changed`**: a new session starts in a herdr pane whose tokens name a position, but the registry holds another session for it. `of` records the evidence and leaves the registry alone. The owner confirms the change.
- **Not collected in v0**: every tool call, token counts and costs. They are in the transcripts and come in by import when needed. Exact model traffic needs a harness-side proxy, which is a later option ([`framework.md`](framework.md)).

## Hook state

`of hook` keeps one small file per session, `~/.local/state/of/sessions/<session id>.json`, with the open ask and the last model. It is a cache that can be rebuilt from the log, not a source of truth. It lets the collector fill `reply_to` and close asks without reading the log.

## Requirements from founder-load metrics

`of_field-analyst` is the first customer (2026-11-05). The founder-load baseline is in the Acme org repo, `reports/Founder load baseline 2026-11-05.md`. Its requirements map to this schema as follows.

| Requirement | v0 | Later |
|---|---|---|
| Position on every event, not only the session | `gen_ai.agent.name` | Resolved at import from the registry history too |
| One schema for live and import; stable ids; copies of a session in several config trees | Deterministic import ids from the transcript record uuid, so copies collapse | |
| Human prompt: target position, channel, input mode, length, reply to an ask | `of.human_prompt` | Dictation as an input mode |
| Prompts injected by tools, told apart from the founder's | `of.injected_prompt`, `origin: injected` | |
| Ask: from, kind (approval, decision, information, access), blocking, options | `agent_question` (decision), `permission_request` (approval); options recorded | A classified `kind` for free-text asks |
| Verdict: decision, reason, grant used | `agent_question_answered`, `permission_denied`; approval is the tool running after the request | Grants and `of.mandate_check` with the grant subsystem |
| Message between positions: resolved target, delivery status | `of.agent_message_out` | Relay chain, `carries_authority`, grant reference |
| State change, including suspension by the budget governor | Hook kinds plus `of.state_declared` | `waiting_on_peer` from the message graph |
| Session lifecycle: spawn, resume, holder swap, compaction, close | `session_start` (source), `session_end`, `compaction`, `holder_changed` | Spawner position and brief reference |
| Work record: merged PR with its author position and mandate | | `of import` turns the transcripts' `pr-link` records (PR number, repository, session, time) into `of.work_record` with the session's position; GitHub `mergedAt` adds the merge. The `Position:` trailer covers the future |
| PR state: an open PR's activity, or its close without a merge | | `of import github` (every 30 minutes from the listener) writes `of.pr_state` once per change of an open PR: repo, number, head_ref, author, `state` open or closed, draft, updated_at, `last_review_at` (the newest submitted review), `last_push_at` (the newest commit date on the head; GitHub's list API has no push time), paths, systems, `works`. The id is deterministic over the PR and its `updated_at`, so an unchanged PR writes nothing. Merged PRs stay `of.work_record`, which also carries `body` (the PR's description, masked, at most 2,000 bytes) and `reviews` (the number of submitted reviews; absent when GitHub did not say), and from `of import git` `message` (the commit message below its subject, masked and clipped the same way), for `ap.unsourced-requirement` (of_field-analyst, 2026-11-11), and `works`: the ids of the organization's works the PR matches (docs/registry.md, Works) |
| Plumbing (route, relay, access grant, capacity change, status request, lead view) | | Typed events where a tool exists; otherwise a classified intent on `human_prompt` |
| Name resolution | `of.name_resolution` | Message routing writes it too |
| Focus: which pane is in front, notifications shown and acted on | | From the desk router and herdr focus changes; opt-in |
| Derived labels carry `classifier_version` | Rule for every classified field | |
| Mask secrets at write; focus and time events opt-in | Masker; focus is not collected in v0 | |

The metrics the baseline defines are projections of this log: merged results per founder message, plumbing share, status polls, bare-yes and needless asks, ask-to-verdict delay, concurrent working positions and name-resolution failures.

## Secrets

The masker replaces matches with `[masked:<kind>]` in every text field of the payload before writing. Patterns in v0:

- private key blocks (`-----BEGIN … PRIVATE KEY-----`);
- 32-byte hex keys (`0x` followed by 64 hex characters, and bare 64-hex strings next to the words key, private, secret or seed). Acme handles chain keys;
- provider tokens: Anthropic and OpenAI (`sk-ant-…`, `sk-…`), GitHub (`ghp_`, `gho_`, `ghs_`, `github_pat_`), Slack (`xox[abpr]-`), AWS access keys (`AKIA…`, `ASIA…`), Google API keys (`AIza…`), JWTs (`eyJ….eyJ….…`);
- `password=`, `secret:`, `token=`, `api_key=` and similar assignments: the value is masked;
- URLs with credentials (`scheme://user:pass@host`): the password is masked.

Not covered: seed phrases and other free-form secrets. Masking lowers the risk; it does not remove it. The log stays on the machine with mode 600, and what leaves the machine is the sync system's decision, under the colleague's control ([`framework.md`](framework.md)).

## Finance facts

Proposal by `of_field-analyst`, accepted by `of_architect` on 2026-11-06. The organization's finance unit records **throughput (T), operating expense (OE) and investment (I) month by month**, so that net profit (NP = T − OE), T/OE, ROI (NP / I) and the value of an owner minute (*v_c*, [`diagnose.md`](diagnose.md) §3) can be computed.

**An event, not a registry file.**
- The registry holds structure, which changes by reviewed diffs.
- Finance facts are a monthly series, plus daily balances. They are appended and corrected, and they come from documents (invoices, statements, the report bot), not from structure decisions.
- A registry file would put the amounts into a git repository that every position of the organization reads.

**Payload of `of.finance_fact`:**

| Field | Meaning |
|---|---|
| `period` | `YYYY-MM`. For `B`, the day `YYYY-MM-DD` whose end the balance is at, so a query by the prefix `YYYY-MM` still finds a month |
| `kind` | `T`, `OE` or `I`, flows for a month; or `B`, a balance: the amount held at the end of one day, a stock, not a flow (of_architect, 2026-11-11, for a treasury). NP and ROI read only `T`, `OE` and `I` |
| `account` | `B` only: the treasury's own name for the account or wallet. Never an account number or an address |
| `category` | Absent for `B`. `OE`: `claude_accounts`, `servers`, `node_providers`, `saas`, `people`, `other`. `T`: the system id from the registry. `I`: the asset id from the registry if there is one, else a free string |
| `product` | `T` only, when one system has several products |
| `amount` | A decimal string. No floats |
| `currency` | An ISO 4217 code, or a token symbol |
| `amount_usd`, `fx` | `fx` is `{rate, source, date}`. Both are required when `currency` is not `USD` |
| `basis` | `actual` or `estimate` |
| `source` | A reference to the source document: invoice number, statement, report id. Never the document itself |
| `supersedes` | Optional: the event id of the fact this one corrects. It must be a fact of the same (`org`, `period`, `kind`, `category`, `product`), or for `B` of the same (`org`, `period`, `account`); the validator refuses anything else |
| `grant_id` | The writer's grant. The server refuses a fact whose writer holds no valid grant with `data: [finance]` and a write tier ([`registry.md`](registry.md), grants) |

**Current value.** The current value of (`org`, `period`, `kind`, `category`, `product`) is its latest fact that no other fact supersedes. For `B` the key is (`org`, `period`, `account`): a later balance for the same account and day supersedes the earlier one.

**`people` is one aggregate per period.** Amounts per person are never recorded.

**Who may read.** Finance facts are owner-only data. Their readers are:
- the organization's owner;
- positions holding a grant whose scope includes `data: [finance]`. The `observe` tier reads, and an `act_*` tier writes.

The finance unit's positions get the grant when the unit is made. An analyst gets it only when the owner names them.

**Where the data lives.**
- **The local spool keeps only a stub.** `of event finance_fact` sends the event straight to the organization's server and keeps only its event id and the sha256 of its payload in `of.db`, in the table `finance_stub`, never in `event`. If the server is unreachable, the command fails and nothing is queued locally; the writer retries.
  - The client (`bin/of_finance.py`, `of_platform`) posts `{record}` to `POST /v1/finance_facts`. It sends only when the server's `/v1/terms` lists the capability `finance_facts`; until then it refuses and keeps nothing. The endpoint is `of_server`'s (proposed 2026-11-10).
- **No full-text index.** `of.finance_fact` is never put into `event_fts`.
- **Nothing leaves the organization.**
  - The kind is outside the shape allow-list and never part of a publication copy. It is never shipped to another organization's server.
  - of-server refuses it in any reduced copy (defence in depth).
  - In the organization's ClickHouse, the colleagues' view `v_event` leaves it out (their read user reads only the views, which run as their definer, so the filter is in the view rather than a row policy; `of_server`, 2026-11-10). Studio shows it only in owner-only views.
- **Derived measures cross a boundary only as an owner-approved `of.published` document.** These are NP, T/OE, ROI and *v_c*, computed inside the organization.

**Honest limit for v0.** Every agent on the founder's Mac runs as his user. The stub keeps the amounts out of the `event` table and `event_fts`, but not out of two places:
- **The writing session's transcript.** Live ingest copies every transcript line into `of.db` within seconds of a turn (`transcript_record` and its blobs), and `of archive` and `of import` copy it too. The amounts a finance session types into its `of event` calls reach `of.db` that way.
- **The finance unit's working files**, if it keeps any.

Whoever can read these can read the amounts. Two remedies are planned for v1, to be designed when the finance unit exists:
- an importer that reads the amounts from the source documents, so that no agent types them;
- or a per-position ingest exclusion.

**Implementation owners:**
- `of_platform`: the event kind, `of event finance_fact` with the stub, and the exclusion from the shape allow-list and from `event_fts`;
- `of_server`: server-only write, the grant check, the refusal in reduced copies and the ClickHouse row policy;
- `of_studio`: the owner-only views and the measures NP, T/OE, ROI and *v_c*.

## Import of history

`of import` reads past harness transcripts (Claude Code first, then Codex rollouts in `~/.codex/sessions`, where `turn_trigger: remote_ios` gives `channel: phone`) and writes the same kinds, plus `of.work_record` from `pr-link` records, with `of.source: import`, `of.transcript.uuid` and deterministic event ids. For each session it imports only records older than that session's first `hook` event, so live and imported events do not overlap.

## Installation (Claude Code)

- One command, `of hook <HookEvent>`, reads the hook JSON on stdin, writes the event and exits 0. With `HERDR_ENV=1` it also reports state to herdr through `herdr pane report-agent`: `working` on UserPromptSubmit, `blocked` on PermissionRequest and on PreToolUse AskUserQuestion, `idle` on Stop. After `blocked`, the next PreToolUse or PostToolUse of the same session (including PostToolUse AskUserQuestion) returns it to `working`, so an answered agent does not stay blocked. herdr then stops guessing the state from the screen.
- Hooks go into the global `settings.json` of each Claude config directory, so every session there is recorded. v0 installs only into `~/.claude-b`, and only after the founder has seen the exact change.

## Backlog v1.1 (after `of hook`)

From `of_field-analyst`, approved by `of_architect` (2026-11-05). Goal 1 in [`success.md`](success.md) is now framed by throughput accounting: the owner's attention is the constraint, and the reward chain is decision → PR → deploy → measured ΔT.

Core:
1. `expected_dT` and `evidence_ref` (a backtest or analysis id) on asks, with an open or closed state, so the decision inventory (count, age, Σ expected ΔT) is a projection.
2. `of.deployment`: a release with the PRs, positions and mandates it contains. It closes the chain verdict → work record → deployment.
3. `measured_dT` on a deployment, or a later outcome event that references it, filled from the organization's T source.
4. A throughput class per position, needed for "constraint time on throughput work": decided as the registry field `throughput` ([`registry.md`](registry.md)), 2026-11-05.
5. Focus events become the preferred input for constraint time; message counts stay the fallback.
6. `of.mandate_changed`: position, mandate (brief path and revision), summary, throughput, status (active, parked, superseded, done), issued_by, reason. `of apply` writes it when a position's registry entry differs from its last such event; later the issuer of a mandate writes it. The registry stays the current snapshot and the log holds the history, so observations can use the class a position had at the time.

Flow quality and waste (`of_field-analyst`, 2026-11-05; Acme org repo, `reports/Flow quality and waste 2026-11-05.md`):

7. **Blocked with a cause.** `of.state_changed` with `state` (working, waiting, idle, suspended), `blocked_on` (`owner`, `peer:<position>`, `resource:<runner|ci|limit|governor>`, `external:<who>`) and the end of each wait. v1 already gives owner waits as open asks and suspensions as `state_declared`; runner and CI waits need a source (long-running tool calls, CI hooks). The registry's `waiting_on` would gain the `resource:` form, which is `of_architect`'s call.
8. **Ask lifecycle.** An ask is `open` until it is `answered` (a reply or verdict that names it), `superseded` (a new ask in the same session before an answer), `withdrawn` (the agent says so; classified) or `expired` (its session ended, or no answer within a set age). These are projection rules over v1 events, so dead ends can show live in the owner's view (`of asks`).
9. **Correction.** `of.correction`: an agent corrects its own earlier claim or work, with a reference to the corrected event or work record and `kind` (`fact`, `method`, `code`). Classified on `turn_end`, with `heuristic: true` and `classifier_version`. They are labelled negative examples for goal 2.
10. **Rework relations.** `reverts`, `fixes` and `supersedes` between work records, from revert commits, PR references and the `Position:` trailer history.
11. **Verdict reason.** `reason_class` (`accept`, `correct`, `over_engineered`, `wrong_priority`) on the human prompt that answers an ask, classified, so the owner's corrections and over-engineering strikes are counted exactly.

App adapters (each organization defines T and produces it; the core only needs typed `of.t` and `of.oe` events to reference):
12. Acme T: daily T per lane and host plus missed market, from the storefront reports bot (money after gas, with a chain reconciliation flag).
13. Acme OE: subscriptions, plan changes, servers, node providers and SaaS as monthly amounts, from the founder or from bills. Not available yet.

## Classifications

Classified labels live in one map per event, keyed by version: `payload.classifications = {"<version>": {labels}}` (`bin/of_classify.py`, shared by the live collector and `of derive`).
- `fa-2026-11-05`: `of_field-analyst`'s baseline rules, pinned verbatim. On `human_prompt`: `kind` (founder, interrupt, injected, brief, alert_paste), `cats` (status_poll, routing, relay_paste, access, capacity_tooling), `approval` (short, explicit, null). On `agent_message_in`: `relayed`.
- `of-v1`: on `human_prompt`: `origin`, `interrupt`, `alert_paste`. In history, origin comes from the harness's metadata (`origin.kind`, `turnOrigin`, `promptSource`).
- `q-v0` / `q-v1` on `agent_question`: one event is written when either fires; consumers filter by version.

Codex rollouts (`~/.codex/sessions/**/rollout-*.jsonl[.zst]`, zstd read whole) are ingested with `harness: codex` and the session id taken from the file name. `of derive` writes their human prompts (the baseline's exclusions: `<environment_context`, `<user_instructions`, `# AGENTS`, `<permissions`; duplicates across `user_message` and `message`/user merged by minute plus the first 60 characters; `channel: phone` when `turn_trigger` is `remote_ios`), turn ends and heuristic questions, with `of.harness.name: codex`.

Imported events carry `of.source: import`, `of.transcript: {path, uuid}`, `of.resolution: registry@<date>` and deterministic ids (UUIDv5 over session, record and kind).

## Decisions (2026-11-05, `of_architect`)

0. Classified fields (the heuristic `agent_question`, `carries_owner_words`, any classified `origin` or intent) carry `classifier_version` and `heuristic: true`, and never block anything automatically.

1. A human prompt's text is recorded in full, up to 8 KB. The founder wants his prompts recorded.
2. `turn_end` records `last_message`, masked, first 1 KB, so the ask queue and the desk can show what the agent is waiting on.
3. Every agent commit and pull request carries a `Position: <name>` trailer, so work records can name their author position. The rule is in the AGENTS.md of each repository.
