# Model, owner measures and Org Studio v0

Status: **Draft v0**, 2026-11-05, `of_studio` (brief: `registry/briefs/of_studio.md` in the build team's repository). Frame: [RFC 0001](rfc/0001-organic-framework-platform.md) §5.2 (Model), §5.3 (Measure), §5.9 (Org Studio), §8 phase 2. Inputs: [`events.md`](events.md), [`registry.md`](registry.md), [`storage.md`](storage.md). Design owner: `of_architect`.

This document is the contract of Studio: one projection of the organization at time *t*, the measures of each owner's constraint computed from it, and the first web page that shows them. Decisions not yet agreed with a teammate are marked **(proposal)**.

## 1. One pipeline, three surfaces

```
 registry history ─┐
 (org repo + of.mandate_changed)        ┌─► of model …   (query, CLI; later a tool for agents)
                   ├─► PROJECT(t) ─► MODEL(t) ─► MEASURE(owner, window) ─┼─► of who      (terminal)
 event log ────────┘   tenant job    snapshot      derived numbers only  └─► Studio      (web, via of-server)
 (local store / vault)
```

- **One model, no duplicated logic.** `bin/of_model.py` builds the model; `bin/of_measures.py` computes the measures from it. The terminal, the query and the web call these two modules and nothing else. A number shown in Studio is the number `of model` prints.
- **Pure functions over rows.** Both modules take registry snapshots and event rows and return plain data. They call neither herdr nor the network, so the unit tests run them on fixtures ([`AGENTS.md`](../AGENTS.md): pure logic only).
- **Tenant-level job.** The projection runs where the vault is: on the founder's machine for profile 1, inside `of-server` for profile 2. It reads paths and masked text; it writes only derived numbers and masked labels to the organization's database and repository.

## 2. Model at time *t*

### 2.1 Inputs

| Input | Source now | Source later |
|---|---|---|
| Registry (positions, systems map, owners) at *t* | The organization's `registry/positions.yaml` at the last commit of its repository at or before *t*. Without a commit, the working file, marked `undated` | Plus `of.mandate_changed` events (backlog v1.1 item 6), which override the commit for the fields they carry |
| Asks, answers, prompts, peer messages, states | Typed events in the local store (`event` table), live and imported with the same kinds ([`events.md`](events.md)) | The same kinds in the organization's ClickHouse database |
| Work records, touched paths | `of.work_record`: merged PRs (`of import github`, `source: gh`) and their `pr-link` attributions; local commits for an organization that commits without PRs, such as the OF team (see below) | Plus deployments (backlog v1.1 item 2) |
| Artifact reads | Not available | Derived at ingest from Read/Grep/Glob paths (RFC 0001 §5.1, gap 5) |

**Local commits as work records** (2026-11-07; the OF team commits on `main` locally and never pushes, so it has no merged PRs). The model reads a commit as it reads a merged PR, given this payload: `source: git`, `unit: commit`, `repo` (the checkout's directory name, e.g. `organic-framework`), `sha`, `author`, `merged_at` (the commit time; the event's `ts` too), `title`, `position` (from the `Position:` line of the message; read the line anywhere in the message, since git's trailer parser misses it when a blank line precedes `Co-Authored-By`), and `paths` (the changed files, which the model maps through the systems' `repos:` and `paths:`), or `systems` when mapped at import. A commit is a smaller unit than a merged PR, so a rule that divides by merged PRs (`chatty`, `over-coupled`) can tell them apart by `unit`.

The model never reads transcript content. The one raw-tier input is the `transcript_record` index (session, type, timestamp; no content), for working concurrency. Imported events name their position from the registry at import time (`of.resolution: registry@<date>`) until dated resolution exists.

The model never parses transcripts. If a number needs something the event log lacks, the fix is a derived event kind in the importer (`of_platform`), not a parser here.

### 2.2 What the model holds

```
MODEL(t) = {
  at, org, registry_rev,             # the commit or "undated"
  positions:  name → {system, role, kind, owner, accountable, status, throughput, holder, owns}
  systems:    id → {kind, parent, keeper, governed_by, lifecycle, depends_on,
                    runs, money, confirmed, needs, paths, repos}       # RFC 0007 §2.2; paths and repos not in snapshots
  cells:      {methods, cells, gaps, overlaps, two_deciders, unknown}  # RFC 0007 §2.3, §3.4a
  units:      owner → [positions]     # the owner tree (§2.4)
  goals:      id → {title, parent, rank, measure, metric, target, by, owner, owner_from, state, decided, drives}
  conditions: id → {title, measure, level, metric, under, owner, state, decided}   # docs/registry.md, Goals (§5.5a)
  system_dsm: (a, b) → {declared: kind | null, coupling: n}
  dmm:        (position, system) → {home, keeper, writes, touches}
  position_dsm: (from, to) → {asks, messages, reads, weight}
}
```

- **System DSM.** A declared edge comes from `depends_on`. **Logical coupling** counts the work records in the window that touched both systems; paths map to systems through the systems map's `repos` and `paths`. Declared and observed are shown side by side; neither overwrites the other.
  - **Wide PRs** (2026-11-06, from a structure keeper's review of the first run on a user organization): a PR that touches more than *N* systems (*N* = 3 by default, chosen on the Structure tab) is a cross-system change, such as a migration window, not evidence of coupling. It is left out of the count; each pair keeps a histogram of PRs by the number of systems they touched, and the weight Σ 1/(k − 1) over all PRs is shown beside the count. Work records by bot authors (merge-queue copies, dependency bumps) carry no coupling.
  - **Basis of an edge** (proposal to `of_platform` for the systems map, additive): `depends_on[].observed_by` is `cochange`, `runtime` or `both`, with optional `evidence` (config references: endpoints, buckets, image registries, DSNs in configs and manifests). Co-change cannot see operational interfaces between systems in different repositories, so a declared edge with a runtime basis and no co-change is **runtime only**, not "declared, not observed".
- **Position × system DMM.** `home`: the position's `system`. `keeper`: the system's `keeper`. `writes`: work records by the position touching the system. `touches`: reads, later.
- **Position DSM.** Directed edges from asks (position → its owner), peer messages (`of.agent_message_out` with a resolved `to_position`, else `of.agent_message_in` by `from`) and reads. Each interaction carries weight `2^(−age / H)` with half-life **H = 72 h** (proposal), so the matrix at *t* shows the current pattern and old edges fade without a window cut-off.

### 2.3 Snapshots

- `of model snapshot [--org O] [--at T]` writes `snapshots/model/<YYYY-MM-DDTHHMMZ>.json` in the organization's repository: the model of §2.2, sorted keys, one stable layout, so that two moments diff with `git diff` or `jq`.
- A snapshot holds **names, ids, counts and weights only**: no prompt or message text, no paths beyond the systems map's own patterns. It is organization data and is committed like any other file in that repository (by its keeper, with the founder's yes).
- Snapshots are written on request and by a daily job; they are not the cache of the live projection.

### 2.4 Owners and units

**Declared units and delegations** (2026-11-06, on `of_platform`'s contract in [`registry.md`](registry.md) "Units" and "Delegations"; this replaces the inferred units below wherever a registry declares `units:`). A position belongs to the unit of its system. When a unit is delegated (its active delegation is the last record whose `to_owner` is the unit's owner), every position of the unit except the owner answers to the unit's owner: its asks reach the owner, not the founder, and the founder's Owner view shows the unit as one row, with the record's id and review date, the owner's L, W, open asks and dead ends, the unit's open issues, and any fired escalation. `of_diagnose.escalations()` evaluates the `escalate_when` conditions that are Studio's (`issue.severity >= X`; `budget_used` and `envelope breach` report `not_measured` until budgets are recorded); `of pulse` evaluates `ask.blocking` and `ask_unanswered`. `of model escalations --write` records each newly fired condition once as `of.escalation_due` {delegation, unit, condition, from_owner, to, evidence}, for `of_platform` to deliver to the delegator. A delegation takes effect when its diff was applied (`of.diff_applied`, kind `delegate`); asks before that stay the delegator's.

**The delegation review** (the Review view, 2026-11-06, the founder's first priority for Studio: predictions against facts on one screen). When the organization's repository has `reports/delegations/<delegation id>.yaml` (baseline, review window and predictions; `of_field-analyst`), the Review view shows, per prediction: the file's baseline, the baseline recomputed by the same code (`of_measures.delegation_series`; `delegation_review` is its point at the window's end), the predicted value, the baseline at the same point of its window, the value expected by now, the value now, and the pace. The measures are those of the record's positions plus its `to_owner`: `founder.*` and `unit_owner_side.*` (asks, answered, moved on, delays on owner-answered asks, agent-hours waited, the unit's share of the founder's asks, the founder's messages into the unit's sessions and their plumbing share, asks by asking position).

- **Pace.** For a measure that grows over the window (`DELEGATION_ADDITIVE`: asks, answered, moved on, agent-hours, messages), *expected by now* = the prediction × the share of the baseline window's total reached at the same offset into it (the windows are seven days apart, so the same weekday and hour); for a baseline of 0, the share of time elapsed. A growing measure already past its value is settled for the week before the window ends: *missed for the week* for `<=`, *met for the week* for `>=` (`of_field-analyst`, 2026-11-06). A `unit_owner_side` measure with a baseline of 0 takes its shape from the same measure on the founder's side in the baseline week (the unit's asks, same positions, same weekly rhythm). Shares and delays are compared as they stand (*met so far*). A prediction is *unmeasurable yet* when the file says `unmeasurable: <reason>`. An agent unit owner's answers count since `of_platform`'s asks v6 (1c1f01a): the asked position's reply message into the asker's session closes the ask. During the first day the pace is marked noisy; a `min_denominator` keeps a share *not judged yet*; a `hold` judges nothing. Once the window has ended the verdict is *met* or *missed* against the prediction itself.
- **Like with like.** A measure whose definition changes gets a new version in `of_measures.DEFINITIONS` (founder messages: `fm-v1`, the fa-2026-11-05 labels whatever the origin; `fm-v2` from 2026-11-06T20:00Z, d32f8de, only a corrected origin human or paste). The baseline is recomputed on the new definition, and the predictions file restates the prediction by its own rule, recording old and new in `restated`. The review row names the definition it reads and shows `restated`. The baseline cache is keyed by `REVIEW_VERSION` (5 since fm-v2), so a definition change must bump it. Platform, 2026-11-06: founder messages in the unit over the baseline week were 251 under fm-v1 and 227 under fm-v2, and the prediction was restated from 125 to 113.
- **Where the founder's messages into the unit go** (of_architect, for the platform review). The Review row of `founder.founder_messages_in_unit` splits the window's messages two ways, each with its weekly rate (count × 168 / hours elapsed) beside the prediction. By position: the unit owner, which is the intended shift, against its keepers. By topic, `inc-v0`, the leaked-credentials incident of 2026-11-06: the message names rotation, a reissue or revocation, leaks, secrets, tokens, passwords, access or signing keys, or the offboarding; or it answers an ask tagged `security_incident`, or one whose decision line (the first 200 characters) names them. A popup answer names its ask by the last 8 characters. The row then says plainly whether the non-incident rate is still above the prediction. Reported, never judged. A text OF's tools type into a pane (`of_asks.TOOL_FRAME`: a routed question, a relayed message) is never a founder message, even when it was recorded as human.
- **Escalations by tag, and changes inside the window** (of_field-analyst, 2026-11-07). Each side of the series counts its asks by the asker's first declared tag (`of ask --tags`, source declared), else `untagged`. Guessed tags do not count. An ask no agent tags (a blocking screen, a heuristic question, a permission prompt) reads `detected`, and an escalated copy reads its escalate_when rule (`prod_change`, `issue.severity`, `ask.blocking`, from the copy's own frame). Blocking screens (`blocked:*`, recorded from 2026-11-06T13:00Z) are left out of the founder's asks and their share, and are reported apart, so the review compares like with like. The Review shows this beside the founder-side asks verdict and in the reported table as `founder.escalation_tag`; it exists from 2026-11-06, so the baseline has none. The record's `tag_corrections` ({ask, tag, at, by, why}: an open ask cannot be retagged) win over the declared tag, and the view names the corrected asks. A record's `practice_changes` ({id, at, what}) inside the window are marked like applied diffs, and the founder-side tags show untagged asks before and after each. The series is also computed at the exact moment of each change. A sum of durations (`agent_hours_waited`) splits by cutting each wait there: the part before goes before. A structural diff named in the predictions file's caveats (`sd-…`) that applied inside the window is marked on every chart at its time, and every additive row shows its value before and after. The verdicts stay on the whole window.
- **One series, one pass.** `delegation_series` projects the asks once up to the window's end and reads every point from it: an ask counts from its opening and its closure from `closed_at`, since the lifecycle only closes. The view draws hourly cumulative curves per growing measure: the baseline week, the predicted path (the baseline scaled to the prediction), the review window so far, and the predicted value.
- **Who asks.** Asks by asking position: to the founder in the baseline week, to the founder and to the unit owner since the delegation, so the review sees whether the founder's asks are the unit owner's escalations or keepers going around it.
- **Rows.** The baseline window lies before the page's own rows: its rows are loaded for it, and its series is cached for an hour in `~/.local/state/of/studio/review-<id>.json`, keyed by the window, the predictions file's digest and `REVIEW_VERSION`. When each delegation took effect comes from every `of.diff_applied` in the store (`Data.delegations_effective`), never from rows cut at a window's end: an application just after the baseline window would otherwise read as "in effect from the start" and empty the founder's side of the baseline.
- **Closures without an organization.** `of` writes some closures with no organization and no cwd: reconcile answers, withdrawals, and `of asks answered` from a shell. `org_rows` keeps an `of.agent_question_answered` or `of.ask_withdrawn` of that kind when an ask it names (`ask_id`, `ask_ids`) is among the organization's rows. Without them the ask stayed open in Studio while the live asks view had it closed: 4 stale asks in the inbox, and in the platform baseline 2 founder answers missed (answered 36 → 38, open at the end 2 → 0, agent-hours 7.6 → 7.9; 2026-11-06, found by `of_field-analyst`).
- **Who answered, who escalated** (2026-11-06, with `of_field-analyst`). On the unit owner's side an ask counts as answered only by the unit owner's own act: its reply into the asker's session (asks v6) or its own `of asks answered`. A permission approved in the asker's pane, or the founder's answer to an escalated copy, is `closed_otherwise`; an owner side with no own answer shows its delays as *no data yet*. The delegator's copy of an escalated ask (`of.agent_question` `source: escalated`, written in the escalating session) reaches whom it names (`to`), not the position's accountable owner, and counts under its `from_position`; the headline splits the founder's asks into the unit owner's own, escalations (on the unit's conditions, or of keepers' asks) and keepers asking directly.
- **One filter.** `asks_of` passes rows through `of_asks.relevant(name, source, payload)`, the filter of the live asks view (`of_asks.load`, the listener): hook and import events, tools' declarations from any source, and a relay message the agent pulled itself. Over the whole store this gives the live view's 923 asks in the same states (2026-11-06). The temporary skip of `of.paste_sourced` is gone: it is not in `of_asks.READS`.
- **Kinds.** The page loads every kind the ask lifecycle reads (`of.ask_escalated`, `of.permission_resolved`, `of.question_labelled`, `of.injected_prompt`, …, and `of_asks.READS` once it exists); before 2026-11-06 it missed these and an escalated ask stayed open until a later message closed it, while the CLI (all kinds) had it right.
- **The unit owner's yes** (`sd-2026-11-06-platform-unit-owner-yes`, for the 2026-11-13 review; `of_architect`). When the delegation's `envelope.approvals` names the diff that set the rule, the Review view measures it from that diff's application (`of_measures.unit_owner_yes`, exposed as `unit_owner_yes.*` for the predictions file). The classifier is `uy-v1`, defined by `of_field-analyst` in the predictions file (block `unit_owner_yes`): a sentence of the unit owner's relayed message to a keeper with yes, approved (not "approved for/by"), go ahead, is fine, ok to merge, "my (routine) yes" or "a routine yes from me", that also names merge, apply, push or a PR #N, after an optional list marker; one per message. Not a yes: a sentence that gives the yes to the founder ("the founder said YES", "founder … approved", "standing founder yes"), a negation ("without a yes", "still needing a yes", "not covered"), a permission sentence, a yes carrying an ask id (8 hex characters: asks go to the founder, so it is the founder's answer), or an item of a numbered list under a lead naming the founder's answers; in a message about standing permissions only a yes naming a PR counts. On 13:10–13:47Z this gives 11 yeses of 62 messages, `of_field-analyst`'s hand count. `standing_yes`: messages with the unit owner's own "standing (routine) yes" (none naming the founder), counted apart. PR numbers in yes sentences are joined to work records; `reverts_of_yes_merges` counts those reverted within 14 days (`Revert "<title>"`, or a revert naming the number), beside `reverts_in_unit`. Also the keepers' requests to the owner (`rq-v0`), and the unit's spend this month against `envelope.budget.tokens_month` with the share of the month gone. All reported, never judged. PRs show as *not imported yet* while `of import github` has not reached the rule's start.
- The Owner view links to it from the unit's row.

Inferred units (before `units:` exist):

An owner is the position or person that a position's `owner` field names. Owners nest: the `owner` of an owner's own position is its parent. The **accountable human** of a position is the first human up that chain ([`ontology.md`](ontology.md)).

- The founder is the root. Every position whose chain ends at `founder` without passing another human is in the founder's own unit.
- A **delegated unit** is the set of positions whose chain reaches a colleague before the founder. In the founder's views it is one row, named after the colleague, with its measures rolled up; the colleague's views show its inside.
- No delegated unit exists in either registry today. Delegation itself is RFC 0001 phase 4; this projection only needs `owner: <colleague>` on the unit's top positions and an entry for the colleague (kind `owner`, holder a human). **(proposal to `of_architect`: a human holder is `holder: {runtime: human, person: <id>}`.)**

### 2.5 Who works with whom (`of model coupling`; 2026-11-12, for a user organization's structure design)

The input to a structure review: who actually works with whom over a window, how work couples units, and where load sits (`of_conductor`'s message `01a1d096-4ca3`). `of model coupling --org O [--days 14] [--at T] [--top 20] [--json]` prints it. `of_model.structure_input` computes it from the model at *t*, the window's rows, the works (`of_works`), the hand-overs (`of_handover.project`) and the asks with their recipients (`of_measures.asks_routed`, the lifecycle `asks_of` filters).

- **Messages, each counted once** (`of_model.peer_pairs`). A relay message (`of send`) is counted by its `msg_id`: from the sender's `of.agent_message_out` (source `of`), or, when it was sent from another machine, from the receiver's delivery log. A direct message (Claude Code's SendMessage, recorded by the hook or an import) goes to its `to_position`, else to the session that received the same text within 5 seconds. Undelivered messages, messages to another organization and in-process subagents are not counted. The relay records messages from 2026-11-06; before that only direct messages are seen.
- **Hand-overs** proposed in the window, from the proposer to the receiver. **Asks** raised in the window: one that waits on a delegated unit's owner is a pair; one that waits on the human owner is the asker's load, counted apart for `of ask` (declared).
- **Units.** A position's unit is the declared unit holding its system (registry `units:`, through the system's parents), else "(no unit)". A unit's owner sits in the unit of its own system, so a pair of a position and its accountable owner is marked **line**: the units' DSM shows how much of each cell is line, and the strongest cross-unit pairs are listed twice, peers and line.
- **Works across units**: the open works whose doers are in more than one unit, and those whose systems are; the open works by system.
- **Load per position**: turns (`of.turn_end`), messages out and in, distinct senders, other units sending to it, hand-overs out and in, asks to the owner (with `of ask`), asks routed to it as a unit's owner, open and active works, and works waiting on it now. **Most depended on**: positions ranked by what they received (messages, hand-overs and asks), with the part from other units. **Finished**: a status other than active; **no turn and no open work**; **turns but no open work on the map**.

## 3. Measures of the owner's constraint

Per owner *O* and window *[a, b)*. Every measure states its events, its classifier versions and the registry revision it used.

### 3.1 Asks and the queue

An **ask to O** is an `of.agent_question` or `of.permission_request` from a session whose position's accountable human is *O*. Its lifecycle (open, answered, denied, superseded, expired, withdrawn) is the existing projection of `bin/of_asks.py`, reused as is. Asks from sessions with no position are counted under `(unregistered)` and attributed to the founder.
A heuristic question the judge closed as `not_a_question` (`of.agent_question_judged`, from 2026-11-07T22:20Z; founder, ask `ddd6e3a3`) never reached the owner's queue, so no measure counts it (`of_studio`, 2026-11-09: 276 of the first 312 verdicts). A heuristic question not judged yet counts as before: history before the judge, and a verdict still pending. The judge's no shows up as a fall in the `detected` bucket of a delegation review, not as a change in the unit.

| Measure | Definition |
|---|---|
| **L**, decision inventory | Time-average number of open asks to *O* over the window: ∫ open(t) dt / (b − a). Also L at *b* (the inventory now) |
| **λ**, arrival rate | Asks to *O* that opened in the window ÷ window hours |
| **W**, wait | Mean open time of asks that opened in the window; an ask still open at *b* counts up to *b* (censored, flagged) |
| **Little residual** | L − λW, and (L − λW) / L, shown with the **carry-in**: the asks opened before the window and still open at its start, and the part of L they contribute (in W7 the residual is entirely carry-in). Near zero means the window is roughly stationary; a positive residual means asks from before the window are still open, a rising L with flat answering means the constraint is saturated |
| Answer delay p50 / p75 / p90 | Over asks the owner answered: answer time − ask time, minutes. A question is `answered` only when the owner's input closed it (a human prompt with origin human or paste, or an AskUserQuestion answer); a question the session closed by moving on (a turn started by a peer message, a message out, a work record) is `moved_on`: its time counts in L and W, not here, and it is not a dead end (`of_field-analyst`'s hand-check, 2026-11-06). |
| Agent-hours waited | Σ (close − open) over answered asks, leaving out gaps over 12 h (sleep), as in the baseline §2.6 |
| **Dead-end asks** | Asks open longer than `dead_end_hours` (24 h, `~/.config/of/asks.yaml`) at *b*, plus asks that expired (session ended) unanswered in the window |

### 3.2 Where the owner's messages go

| Measure | Definition |
|---|---|
| Owner messages | `of.human_prompt` with `origin: human` (or `paste`) from *O*, after the exclusions of the baseline §2.1 (briefs, interrupts, alert pastes, injected), deduplicated by event id. The origin is the corrected one: `of_model.load_events` loads every `of.prompt_origin` in the store with the prompts and applies `of_asks.apply_prompt_origins`, the one reader the asks projection also uses, so a cron or loop fire the hook recorded as `human` (175 since 11-01) counts as `scheduled`. An origin of `scheduled`, `system`, `injected` or `unknown` is never the owner's, whatever the baseline labels say; the same rule decides which prompts close a declared wait and which count as the owner's prompts on a lingering mandate. |
| **Inbound concentration** | The owner's in-degree share: asks to *O* ÷ (asks to *O* + peer requests). A peer request is a received `of.agent_message_in` labelled `rq-v0` (the first 300 characters after the wrapper ask for something: `?`, please, could you, or the same words in the people's language, …; `of_field-analyst`, 2026-11-06). Asks reach only humans, so asks alone would make it 1 with one owner. A user organization's baseline week: 399 asks, 510 peer requests, 0.439. Also: the share of asks from positions whose `throughput` is not `raise` or `sustain` |
| **Plumbing share** | Owner messages with a non-empty `intent` (status poll, routing, relay paste, access, capacity and tooling) ÷ owner messages. Status polls are reported separately |
| Constraint time on throughput, (a) | Owner messages to positions with `throughput` `raise` or `sustain` at the time of the message ÷ owner messages matched to a position |

The `intent` label is set **once**, by `bin/of_classify.py`, the classifier the collector and the importer share (`of_platform`; agreed 2026-11-05), with `intent_classifier_version`. The prompt exclusions (`exclusions`: brief, interrupt, alert_paste, injected) and the question heuristic live there too. This module reads the labels and never re-runs the regexes.

### 3.2a Status polls (`sp-v1`)

`of_field-analyst`'s definition (docs/diagnose.md §7.1): an owner message whose main act asks for the state of work in progress, or of the agent doing it. `of_measures.status_poll_label` is rule-based. It recognizes poll clauses (how things stand, what is left, what happened, whether a step is done, when it will be ready, what the agent waits for, impatience), then applies the test: remove them, and if an imperative other than report or gather is left, it is `instruction_with_poll`. A poll on limits, alerts or logs is `system_status`; a design or mechanism question is `question`. Pasted text and numbered answers are never polls.
`of_measures.status_polls` counts them over a window per organization and per position, with the share of the owner's messages (fm-v2). The owner view's tile and the polls-against-Studio-opens chart read it. The pinned `fa` phrase list stays for the founder-load gate only (`status_polls_fa` beside it).
`of model polls [--org O] [--from --to] [--exclude-unit U] [--labels FILE …]` prints it. With labels it reports the agreement on every labelled message, and the gate is the count within ±3. Labelled week 2026-10-30T13:40Z–2026-11-06T13:40Z, both label files: a user organization 47 against 46 labelled (all 46 found, 1 unlabelled), the OF team 4 against 4, no labelled negative flagged. The rules were written against that week, so the next labelled week is the real check. Outside the platform unit: 34 polls in 503 owner messages (the Works prediction's judged scope).

**Change predictions on the Review tab.** A `reports/predictions/*.yaml` in an organization's repository (a prediction for a change, such as `of_field-analyst`'s Works-screen prediction) is reviewed on that organization's Review tab with the same code on both windows: the baseline week recomputed beside the file's figures, then the review window, or before it opens the last 7 days for reference. The precondition comes first: below it the verdict is *not tested*, not *missed*. The Works view's precondition is the number of distinct UTC days with `of.studio_viewed {view: works}`; only the one-time link (`of studio`, `s` in the asks popup) records a view. A view whose link an agent requested (`requester: agent:<position>`) never counts, and the record may exclude others by event id with a reason (`excluded_views: [{event, why}]`). The file names its measure (`measure: {name: status-poll, version: sp-v1}`) and its scopes (`scopes: {<name>: {orgs: […], exclude_units: […] | only_units: […]}}`). Until it declares them, Studio reads the Works prediction's prose as *judged_scope*: both organizations outside unit platform, and *platform_unit*, and says so on the card. Results are cached hourly.

### 3.2b Repeat corrections (`rc-v0`; RFC 0006 §2.4, step 5)

The founder's corrections to a position are `of.founder_correction` events, from the judge that reads his prompts in its pane (`of_platform`, RFC 0006 step 4). `repeat_of` names an earlier correction of the same position with the same rule. The **repeat-correction rate** of a position in an ISO week (Monday 00:00 UTC) is the corrections with `repeat_of` ÷ all its corrections that week. It is the founder's test for splitting an organization's rules by role: the split works if the rate falls.

- **Reads:** `position` (an alias counts as its canonical name; with none, the session's position), `repeat_of`, `rule` (the "repeated most" column) and `prompt_id`. One correction per `prompt_id` counts, so a retried judge counts once.
- **Whole weeks:** every correction the organization has is loaded, whatever the page's window (`Data.history`), so a week's rate is never cut at the window's edge.
- **Where it shows:** `of_measures.repeat_corrections(model, rows, b, weeks)` computes it. The Owner page shows the last 6 weeks, one row per position plus `all`, as "rate repeats/n". A unit owner sees its own positions. `of model corrections [--org O] [--weeks N] [--at T] [--json]` prints the same table for a structure keeper.

### 3.3 Waiting time by cause

Agent-minutes each position spent waiting in the window, by cause (`of_measures.waits`, built 2026-11-06):

| Cause (colour) | From |
|---|---|
| `owner` | open asks (§3.1); herdr-blocked episodes (`of.attention` `herdr_blocked` from `of pulse`, until the position's next event); `of.pulse` snapshots of `waiting_owner` |
| `peer:<position>` | `of.state_declared` `waiting` with `waiting_on: <position>`; `of.state_changed` `blocked_on: peer:…`; pulse snapshots of `waiting_peer` |
| `capacity:<runner, ci, limit, …>` | `waiting_on` or `blocked_on` `resource:…` |
| `suspended` | `of.state_declared` `suspended` → the next state (the budget governor) |
| `external:<who>` | `waiting_on: external:…` |
| `stalled` | `of.stall_detected` from its `idle_since` until the position's next event: idle with no cause given |

Sources overlap (an ask and a pulse snapshot often describe the same wait), so every minute of a position counts **once**, for the first cause in the order above. A declared state lasts until the position's next declared state. No dedicated `state_change` kind is needed: `of.state_declared`, the pulse events and the asks carry every cause. The heatmap buckets the minutes by position × hour; a stacked bar per position shows the window's total by cause.

**Inbound queues** (`of_measures.inbound_queues`, 2026-11-06, at `of_architect`'s request): the other side of a wait, messages waiting for a position to take them. The relay writes `of.inbound_queue` {`org`, `position`, `depth`, `oldest_created_at`} whenever a position's queue on the server changes depth (0 included; it checks every 60 s). The depth is a step function; its last value before the window carries in. Per position: depth and age of the oldest message now, the most and the time-weighted average over the observed part of the window, and the hours the oldest message was older than 30 minutes (`QUEUE_OLD_S`, the threshold of `of pulse`'s `stall_detected` with `cause: inbound_queue`). A position that receives more than it takes shows a growing average and backlog hours. Until `of_platform`'s f888177 (2026-11-06T12:53Z) a change was recorded only when the depth changed, so an oldest message replaced at the same depth (one taken, one arrived) showed at the next change and earlier ages are upper bounds; since then the pair (depth, oldest) is compared and ages are exact. A position never seen with a queue has no row: the server lists non-empty queues only, and the relay writes depth 0 when a queue it saw empties. The table sits under the heatmap on the waits view; `of model queues` prints it.

### 3.4 Coverage matrix

System × position, from the model at *b*:

| Cell | Meaning |
|---|---|
| **K** | the system's keeper (its single writer) |
| **W**_n_ | the position wrote *n* work records touching the system in the window |
| **h** | the system is the position's home (`system` field) without being keeper |
| **m** | the position edited the system's files (Edit, Write; from `of.mandate_check`) without a work record |
| **r** | the position read the system's files (Read, Grep, Glob; from `of.mandate_check`) |

Flags per system: no keeper (a supersystem whose every part is kept counts as kept, and so does a system with `governed_by: external:<who>`, shown as "kept by <who> (external)"; 2026-11-07); keeper not a position of the organization; more than one writer (fragmented ownership); writes by a non-keeper; **orphan work** (calls the mandate router judged `unowned` in a system without keeper); no position at all (orphan system). Flags per position: no home system in the map. A system whose `lifecycle` is not `live` (registry.md: `unsupported`, `retired`) carries no flags: its writers are shown and its lifecycle stands next to its name. The model projects `lifecycle` on every system (default `live`) for the rules to read.

**Work outside mandates** (built 2026-11-06), from `of.mandate_check` (`bin/of_router.py`, observe mode; payload `verdict`, `tool_name`, `path`, `system`, `keeper`), per position over the window: calls in mandate, in another position's system (by its keeper), unowned, `unscoped` (a shell whose working directory is a repository root: no system named, not a finding) and critical, and the share outside. Verdicts count from router version `rt-v2` (`router_version` in the payload; absent means `rt-v1`, which judged worktree paths and a shell's cwd `unowned` by mistake; `MIN_ROUTER` in `bin/of_model.py`). A verdict on a system whose `lifecycle` is not `live` counts as `non_live` (the router's `non_live:<lifecycle>` from `rt-v5`; an older `unowned` verdict on such a system is read the same way, by the registry at *t*): it is shown in the position's counts and is never orphan work. **Orphan work** groups the unowned calls by area: a system without keeper, or the first directories of a path no system claims. The remedy is a new position or an extended mandate (RFC 0001 §5.4). Reads across positions also become `reads` edges of the position DSM (gap 5 of RFC 0001 §5.1). Snapshots keep the counts and fold unclaimed paths into one area, so no path leaves in them.

### 3.4a Systems on evidence and ownership cells (RFC 0007 §2.2–2.3, step 4; built 2026-11-10)

**Each system's evidence** is `of_model.evidence(model, activity, ask_ids)`:
- **Runtime**: `runs: {where, evidence}` as the system architect wrote it, from the platform keepers. A bare string is read as `where`.
- **Code**, derived, `of_model.code_activity(model, rows, a, b)` (`ca-v0`), over the last **30 days** (`CODE_DAYS`):
  - `merged`: the work records that touched the system, mapped by changed paths as the DSM maps them. These are merged PRs (`gh`) or commits on `main` (`git`). Bots' records are left out.
  - `open`: PRs whose latest `of.pr_state` in the window is open.
  - `writers`: positions by record, attributed as in the DMM: the session's holder, the payload's `position`, or a transcript pr-link. The rest count as `unattributed`, with their authors.
  - `last`: the newest merged record.
  - `co_change`: system pairs in one record, with `prs` (records touching at most *N* systems), `weighted` (Σ 1/(k − 1)) and `wide`.
  - With `areas_depth`, the same per changed directory: `areas` (`<repo>/<first N directories>` → records, writers, the system that claims it) and `area_co_change`. Records touching more than 20 areas count as `wide`. This is the co-change DSM that lab-systems-v0 clusters for its baseline (RFC 0007 §2.4). It needs no systems map.
- **Money**: `money:` as written: a P&L line, a cost line, or the goal the system serves.
- **Governance**: `of_model.governance(system)` lists the facts and their ask ids under `confirmed:`. A keeper, a non-`self` `governed_by` and a non-default `lifecycle` are required. `boundary` is listed once confirmed.
  - A fact's state is `confirmed` when its ask id names a stored ask (`of.agent_question` or `of.permission_request`, by the whole id or either 8-character form, in any organization), `ask_not_found` when it does not, and `unconfirmed` without an id.
  - A comment such as "(founder, date)" confirms nothing.
  - Where no store can be read, ask ids are not checked, and the page says so.

**Cells** are `model["cells"]` = `of_model.cells_of(positions, systems, methods)`. A cell is (system, kind of work), where a kind is a framework default (`of_model.METHODS`) or the organization's `methods:`. There is one row per cell that a system `needs` or a position `owns`:
- `needed`, `live`;
- `deciders`, `doers` (every active position on the cell, the decider too) and `inactive` (parked or finished holders, who own nothing);
- `gap`: for a needed cell of a live system with no decider, `nobody` or `no_decider`;
- `overlap`: 3 or more doers (`OVERLAP_DOERS`).

The model also lists `gaps`, `overlaps`, `two_deciders` and `unknown` (an `owns` entry naming an unknown system or kind of work, or a kind in `needs` that is not defined).

A cell on a parent system does not cover its subsystems' cells. Each is its own row.

A unit's owner sees the cells on its systems and those its positions hold. The other positions' names are masked as "+n outside the unit", and the same goes for the writers on the Systems page.

These are the inputs to `of_field-analyst`'s cell-gap and cell-overlap rules (RFC 0007 step 5). The rules read `model["cells"]`, and `context["code_activity"]()` for the 30-day activity, which is computed on first call.

### 3.4b The flow measure before and after onboarding (`fl-v0`; 2026-11-11)

`of model measures` ends with the flow analyst's series of `docs/onboarding-practice.md` §5, over the same window and the same organization's events (`of_measures.flow`). Each series prints its value, or `gap: <the missing input>` when the window holds none of what it reads (`FLOW_NEEDS`, checked as the gate checks `NEEDS`, with `present()`). A gap is never filled with an estimate. The JSON carries them under `flow`. This is the before report's first output for a new organization and the oracle of `lab-onboarding-v0`'s check C5.

| Series | Reads | Method |
|---|---|---|
| merged pull requests per day | `of.work_record` with `source: gh` | by `merged_at`, one per (repo, number); pr-link records and commits never count; bots' pull requests (`BOT_AUTHOR`, a merge queue's own) are counted apart |
| pull request lead time | the same | `created_at` to `merged_at`, the median (the baseline's floor(p·n) element) |
| agents working at once | `of.turn_end`, `of.human_prompt` | sessions in a turn per 5-minute bucket; a turn runs from the session's first prompt after its last turn end to its turn end (a turn end with no recorded prompt counts in its own bucket; a turn over 12 h lost its start). The mean over working buckets, those with at least one session in a turn (no calendar is assumed), and over all buckets, and the peak |
| the head's prompts per day; merged pull requests per prompt | `of.human_prompt` | the owner's messages of §3.2 |
| the head's asks per day; the median answer | `of.agent_question` | §3.1's arrivals and answer delay |
| asks settled below the head | asks to a unit owner | asks whose accountable is a unit owner below the head, and the share with no escalated copy to the head |
| waits on the head and on a peer | `of.state_declared` | §3.3's position-hours, classes `owner` and `peer` |

Commits from `of import git` are reported as a count beside the series, never as pull requests: one method for both windows. The window marks its partial days and the days with no event of the organization (a restart or a day off). Three measures are always listed as never collected: process time and %C&A (§1.4), work outside GitHub and git, and the share of people who record their sessions.

### 3.5 Incrementality

The measures are sums over events, so they are kept as **hourly partial aggregates** per (owner, position, cause) with the last event row id seen. A new run reads only rows after that id and the asks still open. Percentiles are recomputed from the per-ask durations, which are small (hundreds a week). Nothing reads the whole log twice; nothing runs heavy on the Mac.

## 4. The gate

These measures reproduce `of_field-analyst`'s baseline of 2026-11-05 on the first user organization (windows W8 = [10-26, 11-03) and W7 = [10-29, 11-05), UTC, half-open; the baseline report and the throughput-accounting report live in that organization's repository). Tolerance and definitions **agreed with `of_field-analyst` on 2026-11-05**:

| Observation | Gate definition | Target W8 / W7 | Tolerance |
|---|---|---|---|
| Founder messages, status polls, short approvals, bare-yes answers, relayed peer messages | The baseline's classifiers, pinned (below) | baseline §2.1–2.5 | ±3% or ±3, whichever is larger |
| Plumbing share | Pinned classifier | 21.5% / 24.8% | ±1.0 percentage point |
| Answer delay p50 / p75 / p90, minutes | Baseline ask only: `?` in the last 250 characters of the last assistant text block, or an `AskUserQuestion` call; paired with the next founder message of the session | 2.2 / 9.0 / 34.6 and 2.4 / 11.4 / 43.8 | ±10% or ±0.5 min, whichever is larger |
| Agent-hours waited | Same asks; gaps over 12 h left out | 64.3 / 50.0 | ±10% |
| Concurrent working sessions mean / p90 / peak | A session works in a 5-minute bucket if it wrote an assistant entry in it | 2.3 / 6 / 27 and 2.7 / 7 / 27 | ±0.3 / ±1 / ±2 |
| Constraint time on throughput (a), **undated** | Classes from the registry snapshot sha256 `9c387e5916f3225a…` (the organization's `registry/positions.yaml`, 2026-11-05 18:34 local) | 39.5% / 41.2% | ±2 percentage points |
| Merged PRs per founder message, Claude only | PRs by the baseline's `scope.agent_authors` (the logins the agents merge under) merged in the window ÷ founder messages without Codex prompts | 578/806 = 0.717 / 491/617 = 0.796 | ±0.03 |

Rules of comparison:
- **Pinned classifiers.** The gate runs the baseline's own rules (`CATS`, `APPROVE`, `EXPLICIT`, `RELAYED`, `INJECTED`, `BRIEF` in the baseline's own `compute_metrics.py`, kept in the organization's repository), ported into the shared classifier module under their own `classifier_version`, so the only difference is extraction. The current classifier's results are reported beside them, ungated.
- **Scope.** The organization only: sessions whose working directory is not under the baseline's `scope.exclude_cwd` (there, the framework's own checkout). Copies of a session across the four config trees count once. For history, injected prompts are found by the `INJECTED` patterns, since `origin: injected` exists only for live events.
- **Reported, not gated:** the dated (a) (44.4% / 42.0%; it contains a heuristic override until `of.mandate_changed` exists); the merged-PR ratio with Codex prompts (0.70 / 0.77); the waste report's statement-based wait episodes (277 / 189 agent-hours) and dead ends, which use another definition; asks found by the newer question heuristic; and the new measures (L, λ, W, the Little residual, inbound concentration, dead-end asks, waits by cause), which `of_field-analyst` checks by hand on a sample.
- **Per-message diff.** A failed row is diffed against the baseline's run data (kept by `of_field-analyst`, git-ignored, in the organization's repository), joined on the timestamp truncated to the minute and the first 60 characters of text. Texts from it never go into a report.

`of model gate --org acme` prints each row with the target, the projection and pass or fail, and exits 1 on any failure. The targets live in the organization's repository as data, `reports/baselines/founder-load-2026-11-05.yaml` (written by `of_field-analyst`; the pass rule is |projected − target| ≤ max(rel · target, abs)), not in code. The gate refuses to count a row as passed when its pins do not hold:
- **Classifier pin.** The baseline file names the sha256 of its rules; `bin/of_classify.py` must carry the same pin.
- **Registry pin.** The pinned registry is kept beside the baseline as `positions-<sha256 prefix>.yaml` (recovered from the staged blob on 2026-11-05; the structure keeper confirmed that no position field changed after it, only the `systems:` map). Without that file, (a) is reported, not gated.
- **Labels the gate reads** (`fa-2026-11-05`, events.md "Classifications"): `kind`, `cats`, `approval` (`short`, `explicit`, `both`), `extracted` (the entry the baseline's extractor would emit; only those count) on `human_prompt`; `relayed` on `agent_message_in`; `q-v0` on `agent_question`.

**Rows without inputs are `blocked`, not failed.** Each observation names the inputs it reads (`NEEDS` in `bin/of_measures.py`); when one is empty in the window after the scope, the row says which, and the gate exits 1 without counting it as a failure.

**Status: passed, 46 of 46, 2026-11-06.** After `of_platform` imported and derived the history (Claude Code and Codex), the gate ran on the full windows. The report, with the output and how the last rows were closed, lives in the organization's repository. Two lessons for every later baseline:
- **Pairing follows the method, not the event link.** A founder message answers an ask only if the agent took no other turn between them (`TURN_SLACK_S` in `bin/of_measures.py`); `reply_to` alone links answers to asks that were already superseded by other work.
- **A pinned classifier includes its extractor.** Which records the baseline's extractor skipped (projects under `-private-tmp`, automation turns in Codex) and how it cut the text it matched (`clean(body, 300)`) are part of the pinned version, as much as the regexes.

## 5. Studio v0

A web page for the owner. It is the main screen; the terminal stays for quick looks. The founder iterates on it: ship small, show early.

### 5.1 Serving and access

- `bin/of_studio.py` renders server-side HTML (no build step, inline SVG, no scripts: of-server sends CSP `default-src 'self'` and `X-Frame-Options: DENY`) through one entry point, `handle(method, path, query, viewer) → (status, content_type, body)`, for everything under `/studio/`.
- `of-server` mounts it and owns access (**agreed with `of_server`**, 2026-11-05; the mount answers 404 while `bin/of_studio.py` is absent):
  - `POST /v1/studio/link`, tenant or local machine only, mints a one-time token (single use, 5 minutes); an optional body `{"next": "/studio/works?org=…&w=…"}` is kept with the token as the page to land on, accepted only as a path under `/studio/` (no scheme, host, `//`, backslash, `..` segment or control character, also once percent-decoded; not `/studio/open`), else `/studio/`; an optional `"requester"` (`agent:<position>` or `agent:session:<sid>`, set by the client only when an agent's process asks; a value starting with `agent:` but over 80 characters or with other characters is stored as `agent:invalid`, any other value as none) stays with the session and goes into every `of.studio_viewed` it produces;
  - `GET /studio/open?t=<token>` exchanges it for the cookie `of_studio` (HttpOnly, SameSite=Strict, Path `/studio/`, Secure behind https, 12 h) and answers 303 to the token's `next` (default `/studio/`); that page is the first one recorded as `of.studio_viewed`. The token is single use, 5 minutes, stored hashed; the link's base comes from the request's Host or `public_url`, so it works behind an organization's ingress;
  - any other `/studio/` request without that cookie gets 401.
- `of studio` asks for a link and opens the browser. Profile 1: `127.0.0.1:18200`. A server deployment (profile 2): the server's ingress.
- **Viewer and scope.** The viewer is `{id, tenant, scope}`: today `id` is the minting machine and `scope` is `org`. A colleague who owns a delegated unit is `{scope: "unit", owner: <the owner's name in the registry>, org}` (built 2026-11-05; of-server issues it when delegation exists): the measures are computed on the whole organization and filtered for display (RFC 0001 §5.4), so the colleague sees the unit's positions, the systems they touch, the asks that reach the colleague and the unit's waits, never the rest and never "all organizations". The founder's Owner view shows each delegated unit as one row: positions, open asks, L, W, dead ends, and how many inside wait on its owner or have stalled. The tenant sees everything, including masked ask text. A colleague who owns a delegated unit sees that unit only, and only derived numbers and masked text.

### 5.1a The page cache (2026-11-07)

A page takes 12 to 30 s to compute: the model, the measures and diagnose over a week of events for every organization (on 2026-11-07, 77 000 rows for a user organization and 14 000 for the OF team; about 330 MB while it runs). The owner read a 28-second first page as "it does not open". So of-server serves pages computed beforehand.

- `of studio --render-cache` computes every organization once, in its own process, and writes each default page under `~/.local/state/of/studio/pages/<org>/<view>.html` (mode 600, directories 700): every view, Coverage with `all=1` too, and the tenant's inbox across organizations (`all/inbox.html`). It takes about 14 s for both organizations.
- `of_studio.handle()` serves a cached page in a few milliseconds. When the page is older than `PAGE_STALE_S` (5 min), it starts one refresh in the background and serves the cached page meanwhile, with its age in the footer: "computed 3 h ago, a newer one is being computed: reload in a minute".
- One render at a time (2026-11-11). On 2026-11-10 two renders ran at once, one for more than 11 minutes, at a load average of about 50; renders had grown to 4 to 30 minutes, longer than the old lock's 10.
  - A render holds an `flock` on `.refresh.lock` for its whole life. The kernel releases the lock when the process exits, however it ends.
  - A second render, and a background start while one runs, return at once.
  - The `.refresh` file now only spaces starts at least 10 minutes apart.
  - A render runs at `nice 10` and stops itself after 20 minutes (`REFRESH_MAX_S`).
  - Without the 72-second re-parse of a large registry's history (§5.3, the keeper history), a render takes about a minute.
- What the cache does not hold is computed as before: a unit owner's view, a work card (`w`), a structure parameter (`n`), and profile 2's reader. A cold cache computes the page asked for, keeps it, and refreshes the rest in the background.
- The cache lives in files, so a server restart (CodeWatch restarts it on any change under `bin/`) keeps it warm. Freshness without a request comes from the listener running `of studio --render-cache` on a timer.

### 5.2 Views

| View | Shows | Reads |
|---|---|---|
| **Inbox** | Three blocks, at the very top (founder, 2026-11-05, after agents stood idle unseen): **waiting on you**, **stalled** (active mandate, idle, no declared reason), **waiting on someone else** (who → whom). One row per position, deduplicated across sources; for the tenant it spans every organization. Later: issues and pending diffs ranked by expected ΔT per owner minute | §5.3 |
| **Review** | Each delegation's predictions against facts on one screen: the pace table, cumulative curves against the baseline week, who asks whom, caveats from the predictions file. A unit viewer sees its own delegation | §2.4 |
| **Goals** | The goal tree in rank order, each goal with its measure, target, owner, works, doers, open asks and progress; the conditions beside it, a breach above everything; the works with no goal apart | §5.5a |
| **Works** | One row per work (docs/works.md §5): title, state, stage or end, doers, waiting on whom and how long, last progress, progress in 7 days, open issues, review date; works waiting on the owner on top, then those past review, then by unit and system; RFCs nobody holds and finished projects folded. A row opens the work card: facts, waits, merged PRs, commits to the RFC file, issues, the doers' last turns. v0 derives the works (`bin/of_works.py`, below) | §5.5 |
| **Coverage** | The system × position matrix of §3.4, systems grouped by kind and parent, with the flags | §3.4 |
| **Structure** | Declared vs observed system DSM (RFC 0001 §5.9, the owner's question "does the structure fit the work?"): a system × system matrix; each cell shows the edge declared from the row's system (`u` uses, `e` enabled by, `i` interacts) and the number of merged PRs in the window that changed both. Green: declared and observed. Blue: declared with a runtime basis (`observed_by`), no co-change expected. Grey: declared, no co-change and no runtime basis. Red: observed, not declared (a missing edge or a path-claim problem, for the structure keeper). PRs wider than *N* systems are left out (*N* chosen on the tab). Lists of both follow. Built 2026-11-06 at the founder's default (no objection) | §2.2, `of_model.overlay` |
| **Systems** | Each system's evidence (RFC 0007 §2.2): its plain name, where it runs and the evidence, code over 30 days (merged, open, last, writers, and how many records have no position), money, and each governance fact as ✓ with its ask id, "unconfirmed" or "ask not found", then its needs. Tiles count the live systems with runtime and money evidence and the unconfirmed facts. Below: the systems changed together over 30 days | §3.4a |
| **Cells** | The cells matrix: systems × the kinds of work in use. A cell shows `D` when it has a decider, then its number of doers. Red: needed with no decider. Amber: 3 or more doers, or two deciders. Green: needed and decided. Grey: held, not needed. Lists follow: needed cells with no owner, cells with 3 or more doers, two deciders, and unknown entries in `owns` or `needs` | §3.4a |
| **Waits** | The heatmap: positions × hours of the window, cells coloured by the dominant cause, intensity by agent-minutes waited | §3.3 |
| **Owner** | The owner's header: L, λ, W and the Little residual, answer delay percentiles, dead-end asks, inbound concentration, plumbing share, (a). For the founder, a delegated unit is one row with its rolled-up numbers. Then the repeat-correction rate per position and week | §3.1, §3.2, §3.2b |

The page opens on the inbox (RFC 0001 §5.9).

### 5.3 Issues in the inbox (diagnose v0)

`bin/of_diagnose.py` runs the anti-pattern catalogue of [`diagnose.md`](diagnose.md) as projections over the model at *t* and the event rows; thresholds, priors and inputs come only from `docs/diagnose-v0.yaml`. **The rules are `of_field-analyst`'s, the run is `of_studio`'s** (2026-11-07, organic-framework-build diff `sd-2026-11-07-of-team-roles`, the founder's ask `909d03a2`): the detection rules are `bin/of_diagnose_rules.py` with their tests in `tests/test_diagnose_rules.py`; `bin/of_diagnose.py` checks each rule's inputs, applies the owner's labels, routes issues to units, merges and ranks them. The rules read the model and the rows only through `of_model.RULE_SURFACE`, the stable query surface: the model's readings (sessions to positions, message senders, the overlay, PR attribution, the structure keeper, the area of a path no system claims: `area_of`, given the model's `map_repos`, names a linked worktree's path by its repository; and `context_matches` with `CONTEXT_NAME`, `bin/of_loop.py`'s matcher of a loop target's context parts, re-exported so `of registry check` and the rules match alike), the measures a rule needs (`founder_messages`, `message_categories`, `asks_of`, `little`, `owner_measures`, `inbound_queues`) and the issue record (`issue`, `issue_id`, `spec_part`), the contract between a rule and the run. `tests/test_studio.py` pins the names and parameters and checks that the rules import nothing else of the platform; a reading the surface lacks is added there by `of_studio` first. Each rule reports `fired`, `quiet`, `blocked` (an input it needs is empty in the window: it raises nothing) or `not built`. Critical issues sit above "waiting on you"; the rest are the inbox's fourth block in two groups (diagnose.md §3): issues **ranked by score** (a known gain in owner minutes per minute deciding), then **risks without a ΔT yet**, by severity and then the age of the evidence, so a high-severity gap is not buried under a small gain. Issues wholly inside a delegated unit are its owner's to decide; the delegator sees one line for them and gets them only through the unit's `escalate_when`. The rules' status is underneath, so the owner sees what is not yet measured and why. A unit's `budget_used` is the fresh tokens (input, cache writes, output) of its positions this calendar month, from the transcript index (`tok-v0`: assistant records, deduplicated by session, usage and minute). The canonical count is `of_capacity`'s (docs/capacity.md, "Counting tokens"): one API response once, by `message.id`, subagent transcripts included; `of_capacity.usage()` gives it, with cost at list price, but it is a full read, too heavy for every page. Each delegated unit's canonical usage this month (fresh tokens and cost at list, cache reads included, counted as `of_capacity.usage()` counts) is cached in `~/.local/state/of/studio/usage.json` and refreshed at most every 15 minutes by `of model escalations` (which `of pulse` runs, one copy, killed at 60 s); pages only read the cache.
- The refresh is incremental (2026-11-11). Before it, a full read of a user organization's units took 50 s and 5.2 GB.
  - `~/.local/state/of/studio/usage-index.json` keeps each transcript's offset, this month's message ids and per-session sums: the month's, per UTC day, and fresh tokens per model (index v2, 2026-11-11; a new version reads the month again from the start, about 11 GB for a user organization's units, in three refreshes).
  - A refresh streams only the lines appended since the last one, for at most the time `of model escalations` has left of its 45 s.
  - A unit's numbers change only once a refresh has read every file; an interrupted one goes on from its offsets next time.
- **Use per position** (`of model usage`, 2026-11-11, asked by a user organization's structure keeper for its unit owner): each delegated unit's month per position, with each position's sessions, fresh tokens, share of the unit, dollars and model mix, then the unit per UTC day (`--per-day`: each position per day; `--json`: everything). It reads the same index and count as the unit's budget row, so its unit totals are the escalation's numbers: a unit owner can answer a `budget_used` escalation with a plan (models, pauses, the limit the unit needs) instead of leaving the question with the delegator. Dollars are `of_capacity.usage`'s, at Opus 5.5 list prices for every model, so a move to a smaller model shows in the token mix, not yet in the dollars. A position in two delegated units counts in both and is marked. While the index has not read every file, the command prints a gap, never partial numbers; `--refresh` reads on for one budget, as the pulse does. Any position may run it on the machine that holds the units' transcripts.
- The registry's keeper history, which `keeper_since` reads for `ap.fragmented-ownership`, is kept on disk by commit id (`~/.local/state/of/studio/keepers/`). A run parses only new revisions, and only their `systems:` block: a user organization's 179 revisions had cost 72 s per run.
- `of model escalations` for a user organization with 26 systems takes about 9 s. `budget_used > N%` fires when either `tokens_month` or `usd_month` passes N%, and the unit row shows the month's cost even without a dollar budget. `tok-v0` approximates the canonical count closely (platform unit, 2026-10-30..11-07: 144M against 140.8M) and is replaced by a `message_id` grouping once the transcript index carries it (`of_platform`).

Silence is evidence only where there is observation: a rule that concludes "no interaction" (coordination gap) counts a position as observed only when its own sessions recorded hook or import activity on at least `observed_days_gte` days of the window (default 3). Bookkeeping written by `of` (`of.mandate_changed`, `of.holder_changed`) is not activity.

Rules in parts (needs keyed by part, as `orphan-work@1` and `fragmented-ownership`) run the parts whose inputs are present; the rest report blocked. Each issue names its **decider** (`owner` or `structure_keeper`, from the yaml). The structure keeper is the keeper of the organization's creating system (`acme_org` for Acme). Issues it decides leave the owner's block and appear as one routed line; the keeper reads them with `of model issues --decider structure_keeper`.

Labels come from `of issues label <issue> <label>` (`of_platform`), which writes `of.issue_labelled`; a label applies to the rule version it names and to the versions in the rule's `labels_carry_from`. `of issues` lists and labels the issues of every organization in `orgs.yaml` (`of model issues --org all`), so an id from any keeper digest works without `--org`; the label carries the issue's organization, which the owner's own label (no position) could not be resolved from. An id open in two organizations (a rule on the founder, such as `ap.owner-router`) needs `--org`.

Built (2026-11-06): `unclear-mandate` (≥ 2 changes of summary or throughput in `of.mandate_changed` snapshots), `churn` (holder or mandate changes with merged PRs at most half the trailing 7-day median on those days; only days that ended and whose PRs were imported count), `chatty` (messages per merged PR rising ≥ 50% against the previous window, or an observed pair with ≥ 10 per PR; and, from 2026-11-07 at the founder's request, the receiver's side: `of_diagnose_rules.injected_turns` counts the turns a receiver's session started on text typed into its pane, a relayed message (`of.agent_message_in` from the hook), a pulse nudge (the hook's prompt in of pulse's frame) or another tool's note (corrected origin injected). Part *receiver_flood*: hours with ≥ `injected_turns_per_hour_gte` (20) such turns on ≥ `flood_hours_gte` (3) hours. Part *repeat_nudges*: one waiter nudging the same peer ≥ `nudges_per_pair_gte` (10) times. Each part keys its issue on its own key, so a pair's per-PR issue and its labels keep their id. First run on a user organization, 7 days: 5 floods, with platform_owner at 787 turns and 130 in one hour, and 6 nudge pairs, the busiest at 28), `bottleneck-broker` (inbound share and normalized betweenness both above threshold, until waits during its absence are recorded), `owner-router` (plumbing share, in-degree share, or L rising while λ is flat), `owner-relay` (per ordered pair: the owner's routing or relay-paste prompts to *q* that name *p*, and messages *p* → *q* carrying the owner's words; prompts to the structure keeper are its job, not relays), `over-coupled` (observed pairs: messages ÷ their merged PRs, reciprocity, mutual declared waits; coordinators excluded), `fragmented-ownership` code part (≥ 3 writers and the keeper's share under 30% in consecutive windows; PRs attributed through pr-links), `function-gap` (a management function in no active mandate's `mandate_functions`; ranked, critical once a stakeholder map exists as data), `lingering` (an active mandate 48 h past its `mandate_exit.review` with no later `of.mandate_changed`; `done_when` is prose, carried as evidence), `orphan-work` (keeper-less systems with ≥ 5 merged PRs, PRs no path claims ≥ 5%, unowned router verdicts ≥ 3 per area) `coordination-gap` (required edges between observed keepers with no message, ask or read), and `inbound-backlog` (2026-11-06; per position and day from §3.3's inbound queues: *overload* for a live holder with backlog ≥ 60 min on ≥ 3 days and an average depth ≥ 2, *unattended* for a position that is parked, finished, unregistered or without a holder session, with backlog on ≥ 2 days; owners excluded; blocked until the organization's queues were seen on 3 days, a day counting when the relay wrote any change for it). A rule's `thresholds.merge_into` turns its issue on the same positions as an open issue of the named rule into `evidence.merged` on that issue (`of_diagnose.merge_into`, after every rule ran). First run on a user organization: five orphan-work issues (one system with 88 PRs, `data` 57, two more with 18 and 8, without a keeper; 11% of PRs unmapped); coordination gaps blocked until the keepers' sessions are recorded.

### 5.5 Works v0 (derived)

The founder's next screen (2026-11-06; docs/works.md, step 3): follow the works, not the agents. Until the registry has `works:` (step 1, `of_platform`) and the structure keeper has confirmed the organization's (step 2), Studio derives the list itself (`of_works.derive`, `WORKS_VERSION` wk-v0):

- **RFC works:** every `docs/rfc/*.md` (not `README.md`, not translations `*.xx.md`) in a main checkout under the organization's `paths` (a git worktree, whose `.git` is a file, is a copy and is skipped). Title from the H1, the stage from the `Status:` line, commits to the file from `git log` over 60 days (cached per minute).
- **Project works:** every position of kind `project`: title from its summary, end and review from its mandate, done or parked from its status.
- **Links:** a brief that names `docs/rfc/<x>.md` outside a reading section ("Read first", references) makes its position a doer of that RFC's work; a project whose brief names one RFC is that work, one row.
- **Evidence and state** (docs/works.md §3): a merged PR matches a work when a doer merged it (event position or pr-link attribution) or its branch or title contains the RFC's slug (8 characters or more). Progress is the latest of the last matched PR, the last commit to the RFC file and a doer's last turn. Waits are the doers' (`of_measures.attention`); a work waits on the owner when a doer is `waiting_owner` on the founder (a wait on a unit owner shows as a wait on that position). State: `done`/`parked` from the registry, else `waiting`, `active` (progress in 3 days), `proposed` (an RFC nobody holds whose status says proposal or draft, with no PR), `quiet`. Not yet: stages with evidence, cost, hand-overs per work, and `review` (the last stage done).
- **The registry's list wins.** When the organization has confirmed works (`works:` in `registry/positions.yaml`, else `registry/works.yaml`, else a `registry/works.draft.yaml` whose `draft.status` says confirmed: the first user organization's list of 2026-11-06 had 77 works), Studio shows exactly those (`of_works.registered`). It keeps their `doc` (`repo:path`), doers, systems, owner, `done_when`, `review`, `why` and the declared state beside the computed one; `done` and `parked` are taken as declared. A PR belongs to a work by the `works` its record carries (`of_github.match_works` at import, 7630cc6: PR numbers in the doc's repository, branch globs in the work's repositories, paths, a change to the doc; the rules of docs/registry.md), so Studio and the import read one rule; a record without that field goes through the same function, and a derived work is matched here by its `match` or the RFC's slug; a doer's PR that no match claims goes to the doer's work only when the doer has exactly one open work, otherwise it stays unmatched and the card says so (a keeper with several works would otherwise inflate each of them). Progress also counts repository evidence from the main checkouts (`of_works.attach_docs`): the last commit on each fetched remote branch that `match.branches` names (an open PR's pushes, an external doer's work), and commits on the default branch over 14 days touching `match.paths` (`<repo>:<glob>`). Open PRs come from `of.pr_state` (one event per change; the newest per PR, over every one in the store, is its state; a PR closed unmerged drops out), matched the same way: the card lists them, their branches and titles name stages as moving, and the newer of a PR's last push and last review counts as progress (`last_progress`, and one in `progress_7d` when within the week).
- **Stages** (the founder's choice for the next step, 2026-11-06, ask ff188ea5). A work's `stages:` in the registry win (a structure keeper's first list kept stages on all 27 works, {id, title, done_when, state, note}, ids being the doers' branch and PR tokens; done or open as listed, moving when a matched PR or branch names the id; a range id such as `t2-t5` names each of t2…t5, a numeric id names nothing); otherwise `st-v0` reads them from the RFC as on the remote default branch (`origin/HEAD`), or on one of its three newest matched branches when the RFC is still in its PR (one `git cat-file --batch` per repository): stage-like headings (Stage, Phase, Part, Step, Wave, Milestone N; P1, T7, S1, E2, W3) and hypothesis rows of a table (`| H4a … |`; other table rows are not stages, so a problems table P1–P5 is not read). A stage is *done* when its heading carries a verdict word (closed, done, built, shipped, merged, implemented, refuted, confirmed, completed, rolled out), its row a bold verdict (`**Closed …`), or the Status line says "phases 0 and 1 are built"; it is *moving* when a matched PR's branch or title, or a matched branch, names it (`p2`, `h4a`, `phase-2`). The row shows "3 of 14 done · moving: H1, H4a"; the card lists the stages with their PRs and branches. Repository reads are cached for 10 minutes (`GIT_CACHE_S`). Not read yet: a stage note by the structure keeper (docs/works.md §3).
- `of works [--org O] [--all] [--json]` (`of_platform`'s 04677a8; the same as `of model works`) prints the same rows, after the goal tree when the organization has goals or conditions (§5.5a); `of works --goals [--json]` prints only the tree and the conditions.

### 5.5a Goals (docs/registry.md, Goals; built 2026-11-12, hand-over `3ed608c0` from `of_architect`)

The founder asked on 2026-11-12 for a goal broken into sub-goals, and those into sub-goals, with the agents on each, and the tree managed (`of_conductor`'s message `01a1cf84`). The Goals tab shows that tree, and `of works` prints it (`--goals` alone, `--json` as data). Goals and conditions come from the registry at *t* (`of_model.goals_of`, `conditions_of`: `model["goals"]`, `model["conditions"]`); the tree is `of_works.goal_tree` over the page's works.

- **Rank order.** Root goals, then each goal's sub-goals, by `rank` (1 first), then id: `of_goals.tree`, the order `of registry check` and `of works` use. An achieved or dropped goal keeps its place, dimmed. A goal whose parents form a cycle is shown as a root, with a note. A dangling `parent` makes a root; `of registry check` names both.
- **Each goal row** shows its rank, title, id, state, `decided` (flagged when absent), `drives`, its `measure`, `target` (flagged when a sub-goal has none), `by` and `metric`. Its owner is its own, else its nearest ancestor's (shown as "from <goal>"), else the organization's owner.
- **What a goal adds up.** The works of its whole branch: open works of all, their doers (derived from the works, never written), open asks, progress marks in 7 days (as the Works list counts them), the last progress, and the works by state. A work's open asks are its doers' waits from the attention projection, and an ask declared with `--work` belongs to the work it names, whoever asked (`of_works.evidence`; this also corrects the Works list's "Waiting on"). The works that name a goal directly are listed under it.
- **Notes on a goal**: works on a goal that has sub-goals ("each belongs on the sub-goal it moves"), an active goal under an achieved or dropped one, and more than 7 active sub-goals (more than 7 active root goals is a note above the tree).
- **Conditions** sit beside the tree (below it on a narrow screen), each with its measure, level, current reading, owner (its own, else the owner of the goal it is held `under`, else the organization's owner), and the same rollup of the works that keep it. **The reading**: a `metric` that is a dotted name of the owner's measures for the page's window (for example `agent_hours_waited` or `answer_delay_min.p50`) is read; prose names no Studio measure and shows as such. **A breach**: when the level is a comparison (`<= 1`, `>= 99.5%`) and there is a reading that fails it, the condition is listed again at the top of the tab, above the tiles and the tree (docs/registry.md: a breach comes before progress). A level in words is shown, never judged.
- **Works with no goal** are listed apart: the open works that name neither a goal nor a condition, a question for the owner. A work naming a goal or condition the registry lacks is named in a note.
- **A unit's owner** sees the branches with a work of the unit's positions or a goal one of them owns, with their ancestors, and the conditions they own or keep.
- No goals and no conditions: the tab says so, and what the structure keeper writes.

### 5.6 Development loops (RFC 0005 §5, §7; built 2026-11-07)

`bin/of_loops.py` derives a work's loop metrics from events; no agent reports them. The loop's spec is the work's `loop:` in the registry (RFC 0005 §4: `target`, `measures`, `ladder`, `checkpoint_days`, `wip_arms`). The register and the decision log are events. Studio reads every one the organization has, not just the page's window, since a loop runs longer than a week. **The fields read**, for `of loop` (RFC 0005 §8 step 2, `of_platform`):

| Event | Fields |
|---|---|
| `of.loop_hypothesis` | `work`, `id`, `change`, `predict`, `cost: {rung, runner_h}`, `voi` (`low`, `med`, `high`), `status` (`open`, `testing`, `kept`, `dropped`, `parked`). A later event with the same `id` updates the hypothesis |
| `of.loop_run` | `work`, `hypothesis`, `rung`, `result` (text), `value` (a number in `metric`'s units), `metric` (default: the target's), `queued_at` (when it entered the rung), `started_at`, `ended_at` (default: the event's ts), `runner_h`, `tokens`, `evidence` |
| `of.loop_decision` | `work`, `hypothesis`, `decision` (`keep`, `drop`, `step_up`, `step_back`, `change_objective`), `rung`, `metric` and `money` (for a change of objective), `note` |
| `of.loop_cost` | `work`, `run`, `session`, `from`, `to`, `usd` (null: unknown), `fresh_tokens`, `responses`, `by_model` (`of_capacity`) |

A run starts an open hypothesis (`testing`); `keep` and `drop` end it (`kept`, `dropped`).

**The metrics:**
- **Cycle time per rung** is the median, over the rung's runs, of the time from entering the rung to the result. A run enters at `queued_at`, else at the end of the same hypothesis' previous run, else when the hypothesis was recorded.
- **Wait share** is the time from entering to `started_at`, over the time from entering to the result. It covers runs that give `started_at`.
- **Cycles a day** are the rung's runs this week, divided by 7.
- **Highest rung reached** is the highest by ladder order, with the first time it was reached. A rung the ladder does not name is shown, and ranks lowest.
- **Production rungs** are the ones marked `production: true`, else `shadow` and every rung after it.
  - The production checkpoint is overdue past `checkpoint_days` without such a run.
  - **Time to first money** runs from the loop's first event to its first production run.
- **Hit rate** is kept over decided.
- **The curve** is the runs that measure the target (`metric` absent or equal to the target's) and give a numeric `value`. **Gain per cycle** is the mean step between them; **gain per kept cycle** counts the steps whose hypothesis was kept. The plateau window is the last three steps, flat when each is under 1% (RFC 0005 §6; the rule itself is `of_field-analyst`'s).
- **Objective drift** is marked when each of the latest three runs measures a metric that is not the target and that `measures` gives no `to_money`.
- **Cost this week** is in dollars from `of.loop_cost` (`of_capacity`, step 5, d168f6d): the recording session's spend in the run's window, at list price, written about an hour after the run ends and joined to the run by `run`. It also counts runner hours from the runs, and fresh tokens from the cost, else from the run. A run without a cost yet, or without a start (`usd` null), is counted apart as without a cost, never as zero. Cost per kept cycle is per hypothesis kept this week. Babysitting dollars per doer are `of capacity babysit <position> --json`; the card shows the calls by model.
- **WIP** is the hypotheses in test, against `wip_arms`.
- **The register** shows the hypotheses in test, then the open ones by value of information per runner-hour (`voi` 1, 2 or 3 over the cost's runner hours, at least 0.1).

**One projection for the card and the rules.** `of_model.build` computes `model["loops"][<work>]` once, for every registry work with a `loop:` or with loop events. Studio feeds it each loop event the organization has. The loop rules read it through `of_model.loops(model)` on the rule surface, in the shape of [`diagnose.md`](diagnose.md) §4.1:
- `owner` and `doers`; `target` (with `better`, `higher` by default, and `segment` as the registry writes it, `{start, end, context}` with `context` always a list of its parts, each a text or a mapping `{what, name}`, or `null` when the target names none; docs/registry.md Works) and `measures`;
- `ladder_spec` (with `production` and `since`); `checkpoint_days`, `wip_arms` and `babysit_model` (default `sonnet`);
- `hypotheses` (each with `id`, `ts`, `status`, `status_at`, `change` and `predict`), `runs` and `decisions_all`;
- `metrics`: `rungs.<rung>` {runs, cycle_time, wait_share}, `wip` as `[(ts, n)]`, `production` {last_at, days_since}, and `babysit`: per doer, the tool calls whose `of.mandate_check` `command_class` is `runner` or `monitor`, by model, this week.

A unit owner's view keeps the loops whose owner or doers are in the unit. In the run, `present()` counts `registry.works.loop` when a projected loop is declared, and `of.loop_run` when a run ended in the window. Two more deciders route a loop rule's issue: `doer`, the work's doers, whom the pulse nudges and whom the inbox lists apart; and `work_owner`, the work's owner, which leaves the founder's list when that owner owns a delegated unit. Each comes from the issue's `work` or `evidence.work`.

**The card** sits on the work's card in Works: the target, its controlled segment (start → end) with the parts outside it as context beside it, each by its text, or its `what` and `name`, money and stake, with a red line on drift; the ladder with the highest rung marked; the checkpoint; the curve with the plateau window; the register; the cost; the decision log. The Works list opens with one row per work with a loop. `of model loops [--org O] [--json]` prints the same.

**A standing work** (`standing: true`; docs/registry.md, `of_architect` bd1d454, 2026-11-10) has no final end: its loop is its end condition.
- **Never derived as done.** Nothing in Studio derives done for any work; a standing work ends only when the keeper sets `state: done` or `parked`, on the owner's decision.
- **Its progress is the target's trend by checkpoint, not stages.** `of_loops.checkpoint_trend` cuts the loop's time, from its first event, into periods of `checkpoint_days`. Each period gets the best on-target run value that ended in it (by `better`; none shows as –). The change compares the last two periods with a value, signed towards better, and in percent of the earlier one. The Works row shows "standing" by the state and, in place of the stage summary, "day pass: 325.8 → 326.4 by 3-day checkpoint (+0.2%)", the last four checkpoints. The work card leaves out the stages, and its loop card adds the table of checkpoints (the last eight).
- **Past review** only when its last loop decision, else the loop's first event, is older than `checkpoint_days`. A standing work with no loop events at all falls back to its `review` date, as any work. The card's Review line shows the review date, the checkpoint period and the age of the last loop decision.
- The projection's `trend` (`checkpoints`, `last`, `previous`, `change`, `pct`) sits in `model["loops"]`, so a rule can read it. A plateau over checkpoints is `of_field-analyst`'s loop-plateau rule.
- A work listed outside `positions.yaml` (`registry/works.yaml`) keeps its own `loop:` even when the model has seen its events without that spec.

### 5.4 The inbox projection

`of_measures.attention(model, rows, now)` returns one row per position, `{position, kind, on, since, sources, text}`, with `kind` one of `waiting_owner`, `stalled`, `waiting_peer`, `waiting_external`, ranked in that order and oldest first. `of who` and `of status` can call the same function. Sources, merged per position (kind names set by `of_architect`, 2026-11-05):

| Source | Events |
|---|---|
| `of ask` | `of.agent_question` with `source: declared` and `topic_key` (one row per position and topic); closed by `of.agent_question_answered` or `of.ask_withdrawn` naming the ask or its topic |
| `of status` | `of.state_declared` with `state` `working`, `waiting`, `done`, `idle` and `waiting_on`; the latest per position counts. A declared reason clears a stall. The governor's `suspended` and `resumed` are waits by cause, not inbox rows |
| hooks and history | open asks of §3.1 |
| `of pulse` | `of.stall_detected` (position, idle since), until the position shows any later event; `of.nudge_sent` counts the nudges since; `of.pulse` marks that a pulse ran |
| registry | `waiting_on` of active positions, shown as `declared` without an age |

## 6. Query surface

```
of model [--org O] [--at T] [--json] summary | positions | systems | dsm | dmm | coverage | position-dsm
of model measures [--org O] [--owner X] [--from A] [--to B] [--json]   # the owner's queue, then the flow series (§3.4b)
of model snapshot [--org O] [--at T]
of model gate --org O [--baseline FILE]
of model issues [--org O|all] [--decider owner|structure_keeper] [--json]
of model escalations [--org O] [--write] [--json]
of model usage [--org O] [--unit U]… [--per-day] [--refresh] [--json]   # each delegated unit's month per position, day and model
of model senders [--org O] [--json]      # received messages whose sender resolves to no position: aliases to add
of model queues [--org O] [--from A] [--to B] [--json]   # inbound load: relayed messages waiting per position
of model works [--org O] [--all] [--goals] [--json]   # the works (docs/works.md), v0 derived; --goals: the goal tree (§5.5a)
of model loops [--org O] [--json]          # development loops (RFC 0005 §5): the loop card's data
of model corrections [--org O] [--weeks N] [--json]   # the repeat-correction rate per position and week (RFC 0006 §2.4)
of model evidence [--org O] [--days 30] [--json]       # each system's evidence (RFC 0007 §2.2): the Systems page
of model activity [--org O] [--days 30] [--areas DEPTH] [--json]   # code activity and co-change per system, per directory
of model cells [--org O] [--json]                      # ownership cells: gaps, overlaps, two deciders (RFC 0007 §2.3)
of model coupling [--org O] [--days 14] [--top 20] [--json]   # who works with whom, by position and unit; load (§2.5)
of studio [--org O]
```

All read-only except `snapshot`, which writes one file into the organization's repository. The code is `bin/of_studio_cli.py` (`main(argv)`); `bin/of` dispatches `model` and `studio` to it. `of who` will take its `waiting_on` column from the same projection once asks exist in the log (today it is the registry's declared field).

## 7. Who does what

| Part | Writer |
|---|---|
| `bin/of_model.py`, `bin/of_measures.py`, `bin/of_studio.py`, `bin/of_diagnose.py` (the run), their tests, this document | `of_studio` |
| The detection rules: `bin/of_diagnose_rules.py`, `tests/test_diagnose_rules.py`, `docs/diagnose.md`, `docs/diagnose-v0.yaml` | `of_field-analyst` |
| `of model` and `of studio` subcommands in `bin/of` | `of_studio` drafts, `of_platform` (single writer of the CLI) merges |
| Derived event kinds from history, the shared classifier, `of.mandate_changed`, `of.state_changed` | `of_platform` |
| The `/studio/` mount, the one-time link, the cookie, the ingress | `of_server` |
| Metric definitions, the baseline values, the tolerance | `of_field-analyst` |
| An organization's systems map, owners and units in its registry | its structure keeper (`acme_org` for Acme) |

## 8. Order of work

1. This document; the requests to teammates. **Done 2026-11-05.**
2. The model from the registry alone: positions, systems, declared DSM, the coverage matrix from keepers and homes. **Done 2026-11-05**; Studio's coverage view runs on it before any events.
3. The measures over fixtures shaped like `of derive`'s events; `of model measures` and `of model gate`. **Done 2026-11-05** (`tests/test_studio.py`).
4. Studio v0 with the four views: `of studio --file` **done 2026-11-05**; served by `of-server` on `127.0.0.1:18200` through the one-time link from `of studio` **done 2026-11-05** (mount by `of_server`). An organization's ingress comes with its profile 2 deployment.
5. The gate on the first user organization, as soon as the full history import runs (it waits on the founder's yes) and `of import github` lands (`of_platform`).
