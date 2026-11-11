# RFC 0001: the Organic Framework platform

| | |
|---|---|
| Status | **Draft v3**, 2026-11-05 |
| v3 changes | Restructured around the organization's control loop (founder, 2026-11-05). New stages: measure, diagnose, generate and fix, from the state-of-the-art research (a private report, "Org structure tooling state of art"). New owner surface: Org Studio. v2's reviews and their resolution are kept at the end. |
| Author | `of_architect`, at the founder's request |
| Decides | the founder |
| Consolidates | [`framework.md`](../framework.md), [`storage.md`](../storage.md), [`events.md`](../events.md), [`registry.md`](../registry.md), [`server.md`](../server.md), [`onboarding.md`](../onboarding.md), [`computation.md`](../computation.md), [`lab.md`](../lab.md). Secrets: [RFC 0002](0002-secrets-in-traces.md). |

## 1. Summary

The Organic Framework platform runs an organization of AI agents and humans as **one control loop**.

```
 OBSERVE ─► MODEL ─► MEASURE ─► DIAGNOSE ─► GENERATE ─► FIX ─► STEER
 record     intended  owner      anti-      candidate    change   route work,
 everything + observed constraint patterns  diffs with   path,    mandates in
 per org    structure congruence → issues   predictions  review   context
    ▲                                                                │
    └──────── ORG STUDIO: the owner sees and decides every stage ◄───┘
               CONNECT: machines, colleagues, messages by position
```

In short:
- It **records** every agent session with the maximum of data, per organization.
- It **models** the organization twice: as intended (registry, systems, mandates, grants) and as observed (the log).
- It **measures** where the owner's attention and the agents' time go, and how well the structure fits the work.
- It **diagnoses** structural anti-patterns as ranked issues.
- It **generates** candidate changes with stated predictions.
- It **fixes** through a safe change path with a review date.
- It **steers** live work by routing it to the position that owns it.

**Org Studio** shows every stage to the owner and asks only for decisions. **Connect** joins machines, colleagues and messages, addressed by position.

The platform is a layer on top of agent runtimes (herdr first) and harnesses (Claude Code first). It runs in four deployment profiles, from one laptop to a managed cloud.

## 2. Motivation

The founder runs about 35 agent sessions across two organizations.
- He cannot see what is happening, or find an agent by name.
- Agents wait on him for hours, and some asks are never answered.
- No one owns cross-system releases, and work sprawls beyond its brief.
- History was being deleted.
- **Coordination with colleagues is unstable** (founder, 2026-11-05). Colleagues run their agents in their own unseen ways. Tasks are done unreliably, sometimes the wrong task is done, and quality varies.
- **He cannot delegate part of the organization structure** to unload himself properly (founder, 2026-11-05).

The baselines for all this are in the first user organization's repository (private; founder load, throughput accounting, flow and waste, 2026-11-05).

The research found that **no existing tool closes the structure loop**. HR design tools stop at "merge the scenario", network-analysis tools only describe, agent-observability tools see runs but not structure, and generators optimize per-task workflows. Organic Framework holds the intended structure and the observed log on the same keys, so it can close the loop.

The goals served are 1, 2 and 4 in [`mission.md`](../mission.md).

## 3. Goals and non-goals

**Goals:**
1. **Every owner's** attention is spent only on decisions, and each decision is quick to verify. An owner is not only the owner of the business. It is the owner of any part of the structure: a unit, a set of systems, a system. Colleagues are owners of their parts (founder, 2026-11-05). At the top, the founder's attention is the organization's constraint.
2. **An owner can delegate a part of the structure to another owner** and get it back, with a clear envelope (grants, budget, risk) and visible results.
3. **Work across owners is predictable.** Mandates, hand-overs and quality gates are explicit, whoever runs the agents and however they run them.
4. Every structural change states its prediction and is checked against it.
5. One canonical name per position everywhere.
6. Recording is complete, per organization and never slows an agent.
7. Agents reach each other across machines by position.
8. The platform is runtime- and harness-neutral, and starts on one laptop in 15 minutes.

**Non-goals:**
- A new multiplexer or IDE.
- Doing the agents' work.
- Surveillance of people.
- An "ideal org" template.
- A digital-twin simulator that claims to predict reorganizations. There is no evidence such simulators work.

## 4. Concepts

| Concept | Meaning in the platform |
|---|---|
| Organization, tenant | An organization (an app) has its own repository, database and routing. The tenant owns several organizations and is the only reader of raw content. |
| System | A node of the system breakdown, with **dependency edges** ([`systems.md`](../systems.md)) |
| Position, holder, mandate | Canonical name `<system>_<role>` (`<org>:<name>` across organizations). A holder is a session with its history. A mandate carries brief, summary, status, `throughput` and `serves`. |
| Grant | What a position may do, signed, attenuation-only (§5.2.3) |
| Event | A typed record over a raw tier ([`events.md`](../events.md)) |
| **Intended graph** | The structure as declared, at any time *t*: registry, systems map, mandates, grants |
| **Observed graph** | The structure as it acts, at any time *t*: who works on what, who asks and messages whom, who reads whose artifacts, who waits on whom |
| **Issue** | A diagnosed structural problem, with evidence, affected positions, candidate remedies and expected ΔT per owner minute |
| **Structural diff** | The unit of change: registry, grants, briefs, routing and desk edits, plus a hypothesis, predicted deltas, a reversal step and a review condition |
| **Structure policy** | The organization's declared limits (owner wait, plumbing share, interface ratio, span, congruence, context headroom), kept beside the registry |
| **Owner** | The position that owns a unit: its structure, its envelope (grants, budget, risk appetite) and its results. Owners nest. The founder owns the root; a colleague owns the unit delegated to them; an agent may own a lower unit. The accountable human of any position is the nearest human owner above it ([`ontology.md`](../ontology.md)). |
| **Delegation** | Handing a unit (a subtree of the structure) to another owner, with an envelope attenuated from the delegator's and a reporting line back (§5.8) |
| **Structure keeper** | The position that runs the loop for an organization: for example `acme_org` for Acme, `of_architect` for the build team |

## 5. The loop

### 5.1 Observe: record everything, per organization

**Recorder.**
- `of hook` runs in every harness configuration. It is passive and fail-open; typical latency is about 50 ms, with a hard limit of 200 ms.
- It writes the full raw payload and a typed event, and reports state to the runtime.
- Detached ingest after each turn captures transcripts, subagent transcripts, runtime events and snapshots. `of import` and `of archive` bring in history.

**Sync server (`of-server`).** Clients never write to a database directly, as in mature systems (PostHog, Langfuse v3, Sentry, SigNoz). The server's functions:
- authentication by machine key: Ed25519 with SSHSIG, short-lived tokens;
- schema validation and idempotency;
- routing per organization: `serves`, then position, then paths, then unassigned;
- the vault for raw content, which only the tenant reads. Organizations get typed and shape records, and raw only after masking;
- a durable disk queue before acknowledging;
- batch writers;
- receipts. Local deletion happens only after the server confirms;
- relay and grant verification ([`server.md`](../server.md)).

**Work across organizations.** Content follows its source, the builder's organization gets a shape copy, and results cross only by publication (decision 7 in [`ontology.md`](../ontology.md)).

**Deployment profiles.** The same client and API in all four; only the server plugins differ (storage, buffer, blobs, identity).

| | 1. Local | 2. Self-hosted, simple | 3. Self-hosted, scalable | 4. Organic Framework Cloud |
|---|---|---|---|---|
| Install | `brew` then `of init` | one VM: compose or a single binary, TLS | Helm | sign up, then `of enroll` |
| Store | SQLite by default; ClickHouse optional | ClickHouse, one node | ClickHouse cluster, S3 | managed |
| Buffer | disk | disk | Kafka or Redpanda | managed |
| Identity | local tenant | machine keys | machine keys and OIDC | machine keys, OIDC, tenants, billing |

**Where each organization runs** (founder, 2026-11-05):
- **The first user organization**: profile 2, packaged for Kubernetes, inside its own cluster. The founder and a colleague both connect to it.
- **The Organic Framework build team**: profile 1, a local setup.

A machine that takes part in both ships each organization's records to that organization's server. `orgs.yaml` holds a `server` endpoint per organization.

**Routing is done on the client; the server checks it again.** Only the machine holds all its registries, so routing and the reduction of copies across organizations happen on the client, through the shared `of_route` module:
- content goes only to the source organization's server;
- shape copies go to the builder organization's server;
- slim publication copies go to both servers.

Servers never exchange content with each other. Each server verifies again that the records it receives belong to its organization, and that shape and publication copies carry no content. This is defence in depth. Each server keeps its own vault for its organization's raw records, readable only by the tenant.

Profile 1 must take a newcomer from nothing to "my agents in `of who`, events recorded" in under 15 minutes.

**Events still missing**, needed by every later stage, in this order:
0. **Typed events derived from history.** Importing transcripts today stores raw records only. The derived events (prompts with their origin, asks and answers, agent messages, work records from PR links, compactions) must use the same classifiers and versions as the live collector. Without them no baseline can be reproduced.
1. `of.mandate_changed` and the holder lifecycle;
2. `work_record` with author position and touched paths;
3. `state_change` with `waiting_on_peer` and `blocked_on`;
4. the closed ask lifecycle;
5. **artifact reads** across positions. Reading another position's brief or code is coordination without a message. Reads are derived at ingest from the tool calls in transcripts (Read, Grep and Glob paths mapped to another position's system), not from a hook on the hottest path.
6. **runtime states with a cause.** The desk's subscription to herdr, or the hook's own state reports, writes `of.state_change` (working, waiting on the owner, on a peer, on capacity, suspended). Until then, flow efficiency comes only from asks and turn timing.

The log also exports to **OCEL 2.0**, so that object-centric process mining (PM4Py, OCPA) works without our own implementation.

**Status:**
- built: recorder, server v0, plugins, `of init`, routing, vault, shape, publication;
- **not deployed**: the hooks wait for the founder's yes, and history is not imported;
- not built: events derived from history, the missing events and the OCEL export.

### 5.1a The listener: an event-driven core (founder, 2026-11-06)

Founder, 2026-11-06: the architecture should be event-driven. A **listener** collects every new message from the agents' threads through the hooks. It analyses them, tracks questions and marks them answered or not. Later, more analyses attach to the same listener, for example tracking progress on large RFCs and projects.

Our reading:
- **Shape.** The listener is one local service, `of listen`, under launchd or systemd with `OF_AUTOMATION=1`. It follows the local event store (SQLite WAL) from a cursor. Every **processor** registered on it receives each new event, in order, at most once per cursor position.
- **Processors keep materialized views.** Each processor maintains a small table that the surfaces read instantly, instead of recomputing on every open:
  - **asks**: open, answered, moved on or orphaned, with their groups;
  - **waits and states**: who waits on whom, and stalls;
  - **hand-overs**;
  - **ask counts** pushed to herdr tokens;
  - later, **progress** per RFC or project: milestones from the documents and the PRs they link, done criteria, slippage.

  Processors also emit **derived events**, such as `of.agent_question_answered {via: listener}` and `of.stall_detected`.
- **Time-based rules run as timers inside the listener**, so `of pulse` becomes one of its processors: reminders, response windows, stall thresholds, reconciliation with live sessions. One process replaces the 5-minute launchd job.
- **Surfaces subscribe and render from the views.** This covers the asks popup, `of who`, the desk and Studio. They wake on a change notification from the listener, so the first frame needs no recomputation.
- **Analyses that need judgement** are processors too. Examples are "is this a question to the owner?", "did this message answer it?" and "how far is this RFC?". Each runs detached and on a budget, and writes a verdict with its method version (§5.4).
- **Rebuildable.** Every view can be rebuilt from the event log by replaying it from position zero. The views are caches; the log stays the source of truth. This is event sourcing with projections (CQRS).
- This replaces the earlier rule "no daemon". The CLI stays usable without the listener: it falls back to computing from the log.

### 5.2 Model: intended and observed structure at any time

#### 5.2.1 Three layers (after MOISE+)
- **Structural**: units, positions, kinds, holders and accountability links.
- **Functional**: systems and their breakdown and dependency edges; mandates, `throughput` and `serves`.
- **Normative**: grants, the single-writer rule, critical denies.

#### 5.2.2 Projection at time *t*
The model is rebuilt for any moment from the registry history and the log. It materializes three matrices:
- a **system DSM**: declared dependencies plus logical coupling (systems changed together in one work record);
- a **position × system DMM**: writers, keepers, touches;
- a **position DSM** of observed interaction: asks, messages and reads, weighted and time-decayed.

The projection is a **tenant-level job**: the paths it needs live in the vault, and it writes only derived numbers to the organization's database. Snapshots are organization data. They are committed as files in the organization's repository, so any two moments diff with ordinary tools. A **read-only query surface** (CLI now, a tool for agents later) gives keepers and analysts the same numbers the owner sees.

#### 5.2.3 Registry and grants
- Registries, briefs, grants and typed documents live in each organization's repository. `of registry check` validates them; `of apply` binds holders and never edits them.
- **Grants**: signed (HMAC under a tenant key), attenuation-only delegation, `effect: allow|deny`, `critical`. Matching order: a critical deny first, then the most specific scope, then the more restrictive tier ([`registry.md`](../registry.md)).
- **Single writer**: the registry and grants change only through a reviewed diff (§5.6).

**Status:**
- built: registry, check, apply, grants v0;
- not built: dependency edges in the systems map, the projection at *t*, the matrices, snapshots, the query surface.

### 5.3 Measure: start with the constraint, then fit

1. **The owner's constraint:**
   - the decision inventory L, the arrival rate λ (asks per hour) and the wait W over one window. Little's law (L = λW) is shown with its residual (L − λW) as a stationarity check, not assumed. A rising L with flat answering means the constraint is saturated;
   - the owner's inbound concentration, overall and from non-throughput mandates;
   - plumbing share; dead-end asks; agent-hours waiting on the owner ([`success.md`](../success.md)).
2. **Flow efficiency per position**: working time against waiting time, split by cause (owner, peer, shared capacity, suspended). The cause tells which kind of fix applies: the constraint, coupling, or the next constraint.
3. **Socio-technical congruence**: the coordination the work requires (CR = TA · TD · TAᵀ, from work records and system coupling) against the coordination that happens (messages, asks and artifact reads). Outputs:
   - **missing edges**, which predict integration failures;
   - **surplus edges**, which mean overhead or a hidden dependency;
   - per-position congruence.

   This is the best-supported structural diagnostic in the literature (Cataldo and Herbsleb; Sosa, Eppinger and Rowles).
4. **Cheap network metrics:**
   - ownership: writers per system, and whether one writer dominates;
   - betweenness: brokers and single points of failure;
   - reciprocity;
   - the **interface ratio**: messages between two positions over their own work, which is the measurable merge signal.
5. **Hypotheses, versioned:**
   - **working-set proxies** (compactions per mandate, rereads, open threads, peer share). No validated measure of cognitive load exists;
   - **MAST failure tags**, judged by a model, run detached and sampled under a budget.

Every speed metric is shown beside a quality metric (reverts) and an impact metric (the share of `raise` work).

### 5.4 Diagnose: issues, not alerts

A versioned catalogue of anti-patterns, each with a detection rule, evidence and a default remedy. Thresholds are calibrated in the Lab (`lab-drift-v0`) and on cases the owner labels in-tenant.

| Anti-pattern | Detected by | Default remedy |
|---|---|---|
| Owner as universal router | the owner's in-degree share; L rising while λ is flat; plumbing above 1 in 20 | standing grants for repeated approvals; a coordinator; batch asks |
| Owner relaying between positions | relays, `carries_owner_words` | a direct interface, or a merge |
| Coordination gap | a required edge with no interaction or read | an announce rule on the interface; or move the seam |
| Over-coupled split | mutual peer waits, a high interface ratio, sequential work | merge, if the working set fits |
| Context overload | rising compactions and rereads, forgotten-fact errors | a fresh holder (if history-bound), or a split at a seam (if the working set is too big) |
| Fragmented ownership | several writers to one system | hand writes to the system agent |
| Orphan work | `mandate_check: unowned` clusters | a new position, or an extended mandate |
| Bottleneck broker | top betweenness; peer waits spike when it pauses | a second path; split its interfaces |
| Missing verifier | reverted results; no review on the path | a verification step or position |
| Chatty coordination | many messages per unit of throughput; messages never used | a published interface |
| Unclear mandate | specification-failure tags; mandate flip-flops | rewrite the brief; split if two jobs share it |
| Management-function gap | an unanswered function in the [`method.md`](../method.md) check | a new position |
| Churn | frequent holder or mandate changes with throughput drops | freeze; lengthen review windows |
| Lingering collaboration | a temporary pairing past its exit criterion | close, merge or formalize |

Each **issue** carries:
- its evidence (the series and edges that triggered it);
- the affected positions;
- the candidate remedies;
- the **expected ΔT per minute of the owner's time** to decide it. Issues are ranked by this.

Metrics are computed on the whole organization and filtered only for display. Rework loops (mandate flip-flops, re-raised asks, reverts) are explicit rules, because process mining does not find them by default.

### 5.5 Generate: incremental first, greenfield second

**Incremental.** An issue becomes one or a few candidate diffs from the remedy templates of the sizing rule ([`method.md`](../method.md)): subagents, fresh holder, split by fork, merge, hand-over, new position, verifier. These map onto the Dynamic Reteaming patterns: grow-and-split, merging, switching, isolation. Most of the value is here.

**Greenfield.** For onboarding step 3 and for units from Bench packages, size-capped clustering of the system DSM (Thebeau or Leiden) proposes a whole structure. The formulation is a constrained multi-objective search over partitions:
- **Hard constraints:**
  - context fit: working set ≤ α × effective context, with α < 1;
  - one writer per system;
  - an accountability path to an owner;
  - a bounded owner span or attention budget;
  - each unit has its system agent.
- **Objectives:**
  - minimize cross-position coupling, owner escalations, the critical path across positions and tokens per result;
  - prefer a central validator.
- **Split rule:** split only if the working set exceeds the context budget, or if the work is parallelizable, loosely coupled and a single agent succeeds below about 45%. This is the result of the 180-configuration scaling study; sequential work loses 39–70% when split. Otherwise keep one position with subagents.

An LLM proposer may name positions, draft briefs, map stakeholders and run the management-functions check. Its output must pass the deterministic constraints and the numeric scorer before anyone sees it.

**Every candidate leaves as a diff with predicted signal deltas**, for example "B's interface ratio with A drops below 1; owner relays between them fall to zero". Without a stated prediction, the fix stage has nothing to check.

Architecture search over agents (MaAS, AFlow and similar) belongs **inside** a position, in how a project composes subagents, not at the level of the org chart.

### 5.6 Fix: a change path with a hypothesis, a canary and a review date

| Step | What happens |
|---|---|
| Package | The diff carries a hypothesis, predicted deltas, a reversal step and a review condition (for example, "after 20 tasks or 7 days") |
| Validity check | Deterministic: registry check, grant attenuation, single writer, accountability path, no critical grant widened |
| Objections | Affected holders may object. A **reviewer on a different model** checks the proposal; supervisors on the same model share blind spots. |
| Structural replay | Tier (a), exact and cheap: recompute routing, who would have been asked and queue order under the diff ([`lab.md`](../lab.md)) |
| Owner's yes | Batched with other proposals into one decision list |
| Apply | Where possible as a **canary or in shadow**: a forked position takes a share of new tasks, or drafts while the old one acts. Continuation moves through the outgoing holder. |
| Review | At the review condition, compare observed with predicted; keep, extend or revert |

**Every variant and its outcome go into an archive with lineage.** That archive is what exposed objective hacking in self-improving agent systems.

**Risk tiers:**

| Tier | Operations | Approval |
|---|---|---|
| Low | use subagents; a fresh holder for an agent; hand a task to the owning position | later: auto-apply under a standing `act_and_report` grant, with notification and an abort window |
| Medium | split or merge agent positions; a new agent position; a mandate rewrite | the owner's yes; a canary or shadow for N tasks; an automatic revert proposal on regression |
| High | anything touching a human holder; reassigning the accountable human; **moving a position between machines** or organizations; widening grants | always the owner's explicit yes; never automatic |

**Fixer SLOs:** acceptance rate, revert rate, prediction error per remedy type, override rate. These are the only evidence for raising a remedy's tier. Model and harness version changes are logged beside structural changes, so that their effects are not confused.

**Moving a position between machines** keeps the holder (the same session) and changes the Workplace.
- The procedure: quiesce → snapshot (a bundle with manifest and checksums, secrets by reference) → transfer → path mapping → resume → verify → retire the source.
- Checked on 2026-11-05: a transcript resumes by id from another path with its context. **Auto-memory is per path** and must be remapped.
- It is carried: transcript, subagents, memory, file history, worktree and branch with uncommitted changes, continuation, name and grants.
- `lab-position-move-v0` tests it.

**Redistribution between people** uses the same diffs. A colleague who takes over positions receives their entries, grants, briefs and holders **with the full content history and the session itself** (transcript and context), so they continue from the same point (founder, 2026-11-05). This is the same bundle as a move between machines, delivered to the colleague's machine. Content limits apply only when the organization's rules or a third party's data require them.

### 5.7 Steer: a reconciler, not a planner

1. **Mandate in context.** For enrolled positions (`enrolled: true`), the brief, the mandate, the rules and the team are injected at session start and after every compaction.
2. **Route, don't block** (founder, 2026-11-05). The mandate router runs at `PreToolUse`, fail-open, and writes `of.mandate_check`:
   - in mandate: proceed;
   - **owned by another position**: proceed, and add to the agent's turn "this belongs to X; hand it over";
   - **unowned**: the event goes to the structure keeper;
   - **critical**: deny, the only hard block. This covers money-moving production writes without a yes, printing secrets, and destructive actions on another organization's systems.
3. **The outer loop is a reconciler**, after the Kubernetes controller pattern:
   - the **desired state** is the structure policy;
   - the **observed state** is the measured model;
   - the **action** is the smallest proposal that closes the gap.

   **Hysteresis and cooldowns** per position prevent split → merge → split on noisy signals.
4. **Graded autonomy** (founder, 2026-11-05: whatever can be proven in the Lab can, over time, be automated). A remedy class moves from `propose` to `act_and_report` once two things hold:
   - it is **proven in the Lab** on its scenarios;
   - its **measured precision and revert rate in production** clear the owner's threshold.

   The first class is **hand-over**: work that belongs to another position is handed to that position without asking. Hand-over is routing *within* the existing structure; it changes no registry entry. Structural classes (split, merge, new position) follow later, by the same evidence. Changes that touch a human stay with the owner.
5. **The structure keeper cannot read or edit the detectors that judge it.** An optimizer with access to its evaluator games it.

### 5.8 Connect: machines, colleagues and messages

- **Machines and colleagues.**
  - Enrollment issues a machine key, per-organization ingest rights and a relay identity; leaving revokes them.
  - Consent per organization and tier is an event: typed always, raw opt-in.
  - `of shipped` shows what has left the machine.
- **Messages address positions, not agents** (founder, 2026-11-05).
  - Local delivery goes through `herdr agent prompt` with an injected-prompt declaration and a queue.
  - Remote delivery goes through the server's relay: outbound-only, store-and-forward, deduplicated.
  - A position held by a human receives messages on that person's surfaces: the ask queue, the desk, later a chat bridge.
- **Delegating a part of the structure** (founder, 2026-11-05). An owner hands a unit to another owner, usually a colleague. It is a high-tier structural diff (§5.6) and carries:
  - **the unit**: its systems, positions, holders and briefs;
  - **an envelope** attenuated from the delegator's: grants (no wider than the delegator's own), a token and money budget, a risk appetite, and the critical denies that stay in force;
  - **a reporting line**: the unit's measures (its owner's constraint, throughput classes, issues) roll up to the delegator's Studio as one row. The delegator steps in only on breached limits or issues above a threshold;
  - **a way back**: taking the unit back is the reverse diff.

  The delegator stops being asked about the unit's routine decisions. That is the unloading the founder needs.
- **Coordination with colleagues' agents** (founder, 2026-11-05). The platform makes a colleague's work as visible and predictable as the founder's own, without dictating how they run their agents:
  - their agents are enrolled positions, with canonical names, briefs and mandates that others can read;
  - work crosses owners only as asks and hand-overs, with an acceptance step and done criteria, never as a remark in chat;
  - **quality gates**: a verifier step or position on each hand-over (MAST: the missing verifier is a top failure);
  - per-owner measures in Studio: hand-overs accepted on time, rework and reverts, mandate drift, wrong-task rate (work that does not match the mandate it was asked under);
  - the method travels with the positions: a colleague's position carries the same brief, rules and checks as the founder's would. This is problem 2 of [`mission.md`](../mission.md), bringing colleagues into the practice.
- **Authority.** A claimed approval carries a grant id that the server verifies. Otherwise it is marked an unverified claim. Messages across organizations need a grant.
- **Status:** delivery today uses the harness. Relay and presence are being built.

### 5.9 Org Studio: the owner's surface

The owner's attention is the constraint, so **Org Studio opens on the structure inbox, not on the chart**. The inbox holds:
- the diagnosed issues and pending diffs, ordered by expected ΔT per owner minute;
- for each item: its hypothesis, predicted deltas, tier and review date;
- one action per item: accept, edit or defer. Accepted items are batched into one decision.

Behind each item is the evidence that justifies it:

| Owner's question | View |
|---|---|
| Is every system owned exactly once? | system × position coverage matrix |
| Does the structure fit the work? | intended-vs-actual DSM overlay: required but absent, present but not required |
| Who is overloaded or a single point of failure? | interaction network sized by load, coloured by betweenness, owner pinned |
| Where does time go waiting? | wait heatmap (position × time, coloured by cause), flow-efficiency bars |
| Is attention going to throughput? | Sankey: owner minutes → throughput class → system → position |
| Is the hierarchy sane? | spans-and-layers histogram; cardinality per kind |
| What will this change do, and what did it do? | **one structure with the difference drawn explicitly**, plus a delta table of predicted against observed |

**Layout for 30–300 positions:**
- positions grouped inside system boxes, as tiles coloured by health;
- edges aggregated between systems;
- collapsed systems shown as glyphs with a count and their worst health;
- matrices by default above about 100 positions.

**Drill-down:** organization → system → position card (mandate, grants, holder history, asks, waits, issues) → session timeline (borrowed from OpenTelemetry-shaped trace viewers).

**Surfaces, one model, no duplicated logic:**
- **terminal**: `of who`, `of go`, the desk, `of asks` and a top-issues table;
- **web**: a local page served by `of-server` to the tenant only, on localhost, opened by a one-time link from `of studio`. It holds the inbox and the evidence views and is the owner's main screen. Colleagues later read through the `*_read` views;
- **query**: read-only access for agents.

**Status:** the terminal surface is built (the desk router is off). The web Studio and the query surface are not built.

### 5.10 Security and privacy

- Raw content lives only in the tenant's spool, archive and vault.
- Masking of secrets happens at `of-server`, before organization databases.
- Prevention, detection, review and rotation follow [RFC 0002](0002-secrets-in-traces.md).
- **Prompt injection between positions:**
  - delivered messages are framed as coming from a named position, with a verified or unverified authority mark;
  - no guard trusts text;
  - the Lab plants injection text in `lab-secrets-v0`.

### 5.11 Failure modes

| Failure | Degrades to |
|---|---|
| A hook crashes or runs slow | the event is dropped and logged; ingest recovers the content from the transcript |
| The registry is unreadable | events are recorded unattributed and routed by paths; the router fails open |
| The local store is locked or full | JSONL fallback; an alert |
| The server or store is down | the spool keeps everything; no deletion without a receipt; retries; dead letters raise an alert |
| Disk below 50 GB | an alert; shipping and confirmed deletion are prioritised |
| herdr is down | recording continues; `of who` shows registry data only; `of send` queues |
| Sessions started outside `of start` | recorded only; bound by name or session; steered once enrolled |
| **Silent stall** (seen on 2026-11-05: every agent idle, four waiting on the owner with questions visible only in their panes) | Agents declare their state themselves with `of ask` and `of status`; this works without global hooks. `of pulse`, a 5-minute watcher, detects stalls (an active mandate, idle, no declared reason), reminds the owner of old asks and nudges a peer that someone is waiting on. Studio's inbox opens on "waiting on you / stalled / waiting on others". |
| A metric or detector is wrong | the issue is labelled false by the owner; the rule's version is retired; the fixer SLOs show it |

## 6. Interfaces

| Interface | Stability |
|---|---|
| Registry and grants ([`registry.md`](../registry.md)) | v0; additive changes through `of_architect` |
| Events ([`events.md`](../events.md)) | v1; additions only; the previous version is accepted |
| Server API ([`server.md`](../server.md)) | v0 |
| Structural diff format | to define (step 5 of §8), shared by the Lab and production |
| Structure policy file | to define (step 9 of §8) |
| CLI | built: `init · apply · who · go · desk · asks · registry check · hook · hooks install · import · archive · ingest · grant · secrets review/mark · db stats`. Designed: `start · send · move · onboard · shipped · studio` |
| Exports | OpenTelemetry GenAI names; OCEL 2.0 (to build) |

## 7. Alternatives considered

- **HR design suites** (Orgvue, ChartHop): scenario diffs and approvals, but no observed structure, no check after the merge and no rollback. We take their scenario-diff interaction.
- **Fleet managers** (Gas Town, Agent Deck): role identities and a problems view, but tied to tmux and with no organization model.
- **Network-analysis tools** (Viva Insights, Kumu): they describe, but their metrics are not validated on outcomes. We take their metric definitions.
- **A digital-twin simulator**: no evidence that it predicts reorganizations. Tier (a) structural replay covers structural questions.
- **Org-level architecture search** (ADAS, AFlow, MASS): it needs an automatic scorer that the org level does not have. It is used inside positions only.
- Rejected earlier, with reasons in v2: our own multiplexer, one tenant database, small payloads, an observability backend as the store.

## 8. Rollout

| Phase | Loop content | State | Gate |
|---|---|---|---|
| 0 | Names, registry, `who · go`, desk, herdr pilot | done (the desk router is off) | — |
| 1 | **Observe**: recorder, server v0, plugins, `of init`, routing, vault, shape | built, not deployed | Lab hook testbed passes; the founder installs the hooks |
| 2 | **Observe gaps** (mandate and holder events, work records, waits by cause, closed asks, reads); **Model** at *t* with matrices and snapshots; **Measure** of the owner's constraint; **Studio v0** (terminal health table, a web page with inbox, coverage matrix, wait heatmap); grants issued; enrollment and mandate injection; mandate router in observe mode | next | the metrics reproduce the founder-load baseline within tolerance; the owner opens Studio instead of polling |
| 3 | **Profile 1 complete** (SQLite default, 15-minute quickstart); masking at the server (RFC 0002); secrets review and rotation | before colleagues | `lab-quickstart-v0` (median under 15 minutes) and `lab-secrets-v0` pass |
| 4 | **Connect**: profile 2; colleagues' machines; relay; `of send` across machines; **delegating a unit to a colleague-owner** (envelope, roll-up to the delegator's Studio, way back); **cross-owner hand-overs with acceptance and verifier gates**; per-owner Studio | after 3 | `lab-profile2-v0`, S2→S3 and `lab-routing-v0` pass |
| 5 | **Diagnose v0** (the catalogue, ranking by ΔT per owner minute); **Fix v0, propose only** (diff format with prediction, validity check, objections, batched yes, review scheduler, archive, fixer SLOs, explicit-difference view); scope and size drift signals | after 2–4 produce data | precision and recall per rule above the owner's threshold (`lab-drift-v0` and owner labels) |
| 6 | **Measure v1** (congruence, network metrics, OCEL export, MAST on a budget); **Generate** (remedy templates, then greenfield clustering); structural replay and canaries; moving positions between machines; redistribution | after 5 | generated diffs accepted at least as often as hand-written ones; replay calibrated; `lab-position-move-v0` passes |
| 7 | **Steer with graded autonomy** (structure policy, reconciler, low-tier `act_and_report`); profile 3, then **profile 4, Organic Framework Cloud** | goal 1 | revert rate below threshold over a minimum number of applied fixes |

**Update, 2026-11-06.** The founder enabled the hooks on his account and the history import. The deployment for the first user organization is still on hold.

**Gates passed, 2026-11-06.**
- **Phase 2:** the founder-load gate passed 46/46 on imported history (`of_studio`).
- **Phase 3:** `lab-quickstart-v0` passed 7/7, median 45 s, against a target under 15 minutes. `lab-secrets-v0` passed from the recorder to the server on profile 1 and on k8s; its live-agent part passed too: with the deny rules and the enforcing guard (RFC 0002 §4.1, `of hooks install --deny-secrets --guard`), no canary reached a real agent and the agent refused the injection (`of_lab`). The guard's fail-open was closed (it decides before anything else, under an absolute interpreter path, and the install self-tests it; run 5 had no findings). On the founder's yes (2026-11-06, ask `c3909367`) both flags are installed on all four Claude accounts on his machine; each install's self-test answered deny, and a live read of a canary secret file was denied.
- **Phase 4:** `lab-profile2-v0` passed: invite, enrollment, relay and presence (`of_lab`, `of_server`).
- **The first user organization** moved to herdr (36 positions, onboarding step 9).
- The unit `platform` is delegated to `platform_owner`; review on 2026-11-13.

**Hold, founder, 2026-11-05.** We are still developing and are not deploying to the first user organization yet. Three things are on hold:
- the hooks on the founder's account;
- the history import;
- the first user organization's deployment.

Development continues on fixtures, the Lab and the build team's own sessions. The Lab uses the Claude Code or Codex CLI on the founder's Mac with a small token budget; it has no separate API key.

## 9. Success

**Goal 1 observations** ([`success.md`](../success.md)):
- the owner's attention on throughput rises;
- plumbing, the decision inventory, dead-end asks and waiting fall;
- name failures go to near zero.

**Loop health:**
- issues the owner accepts or labels true;
- the prediction error and revert rate of fixes;
- the congruence trend.

**Platform invariants, asserted by the Lab:**
- no hook blocks a session;
- no record reaches the wrong organization;
- no secret reaches a colleague-visible store;
- no authority is taken from message text.

## 10. Questions for the founder

Answered on 2026-11-05:
- **Studio** is a web page. It may become something more integrated; we iterate as we go.
- **Redistribution** carries the content history and the session itself, so a colleague continues from the same point (§5.6).
- **The trigger for the cloud** will be decided later.

- **The first autonomous fix**: hand-over to the owning position. More classes follow as the Lab and production evidence allows (§5.7, item 4).

## Review: of_platform (implementation), 2026-11-05

### Status lines against reality

- **§5.1 Recorder is "built", but not running and not complete.** The code and 40 tests exist, but hooks are not installed and the history import has not run, so the store holds two sessions. Missing pieces:
  - typed events derived from history: `of import` stores raw records only, with no `human_prompt`, asks or `work_record` from `pr-link`;
  - Codex rollouts are stored raw without parsed fields;
  - instruction snapshots: `add_snapshot` exists, but nothing calls it;
  - herdr runtime events: the desk subscribes, but writes no `runtime_record`;
  - live ingestion of subagent transcripts (they come in only by import).

  `of_lab` also found collector assumptions that contradict the hook docs: PermissionDenied fires in auto mode only, PostToolUseFailure carries `error` rather than `tool_response`, the SubagentStart/Stop and PostCompact hooks are not collected, and `permission_mode`, `prompt_id` and `effort` are not recorded. They are to be fixed in one pass after the testbed. "About 50 ms" was measured before the SQLite store; it has to be re-measured under load (lab item d). Suggested status: *built, not deployed; known gaps listed*.
- **§5.2 is built, with two gaps:**
  - raw content shipped to organization databases is **unmasked today** (two sessions). Until §5.9 masking exists, the shipper should send only typed and shape rows to organization databases and keep raw tenant-only. I can add that gate now;
  - local retention (ship → confirm → delete) and the free-space alert are not built.
- **§5.4:** the desk router has been tested in a scratch workspace and in a read-only live run, but it has never moved a pane on the real desk.
- **§6:** the built CLI also has `ship`, `event` (`injected_prompt`, `state_declared`, `published`) and `registry check`.
- **§8, phase 1:** "built; hook installation **and the history import** await the founder; lab fixes pending".

### §5.6 Messages: what does not work as written

1. **`of send` cannot use "the harness's own cross-session message".** SendMessage is a tool inside a session, not an API that a CLI can call, and Claude's messaging socket is internal and undocumented.
   - From outside, local delivery is `herdr agent prompt`, which types into the pane. The collector would then record the text as the founder's prompt, unless `of send` first writes `injected_prompt`; the wrapper already supports this.
   - Typing into a busy pane, or into one that shows a dialog, is unsafe; herdr's docs warn about it. Delivery needs `agent wait` until idle, plus a per-target queue.
2. **SSH is not a relay for colleagues.** herdr's machine link needs Remote Login and SSH keys *into* each machine. That is fine for the owner's own servers, but not for colleagues' laptops (inbound access, NAT, sleeping machines).
   - Colleagues need an outbound-only relay: each machine connects out, with store-and-forward, message ids, at-least-once delivery and dedupe at the receiver.
   - Presence needs the same channel. So my answer to question 3 is a small relay from the start for colleagues, and SSH only for the owner's own machines.
3. **Names are unique per organization, not globally.** Across organizations, an address needs the organization (`<org>:<name>`), and runtime ids are per server, so they must be keyed by (machine, pane).
4. **"The receiver verifies the grant" cannot be left to the receiving agent.** A model will believe the text. Verification has to happen in the platform: the delivery path or the UserPromptSubmit hook annotates the message as verified or not, against a grants store. That store does not exist yet; it belongs to phase 2 with bounds.

### §5.7 Steering: what does not work as written

1. **Enrollment should not require relaunching.** Hooks are already global. Enrollment can be a registry flag (for example, `steer: true` on the bound position), which the steering hooks check. `of start` then becomes a convenience for new sessions, not the only path for the 35 that already run.
2. **Bounds on Bash are brittle.** A shell command is not a system: `ssh host kubectl …`, scripts and aliases hide the target. Grants need a mapping of repositories, hosts and clusters to systems. The checks will start as heuristics, so observe mode is essential, and false-positive rates will be high at first. (PreToolUse hooks run, and can deny, even in bypass mode, which most sessions use.)
3. **"Topic: an agent judgement every N turns" cannot run inside a hook** (latency, cost). It runs detached after Stop: a cheap model reads the last N turns against the brief, under a per-session budget, and the result is a heuristic event. On question 2, I agree: start with scope and size, and add the model judge once those are measured.
4. **The platform cannot "move the continuation" of a split by itself.** Crons, monitors and wake-ups are created inside the session, through its own tools; no outside API lists or moves them. Context does not transfer either. The old agent must execute its half of the split: write the handoff, cancel its crons and send open threads. The new session then recreates its own. The platform orchestrates this by message and checks it through events.
   - For the same reason, "act and report" for splits is risky as a default. On question 1, *propose* is the right default.
5. **The size and merge signals are measurable** from the store once it is filled: compactions, context tokens, the breadth of distinct repositories and paths, and the ratio of messages to work between positions. "Many parallel threads" has no reliable signal yet.

### Smaller points

- **§5.9:** state plainly that until masking exists, nothing raw is shipped to organization databases (the gate above), and that the HMAC key is tenant-only (`~/.config/of/tenant.key`, purpose-separated keys).
- **§9 "no hook ever blocks a session"** needs a measure: hook p99 latency and the hook error rate from `hook-errors.log`, both in `of who` or a health command.

## Review: of_lab (testability), 2026-11-05

Lab terms are from [`lab.md`](../lab.md) and [`environments.md`](../environments.md). L1 = tabletop, L2 = replay, L3 = live sandbox. Scenarios already in the library: `lab-hooks-v0`, S0 (a solo founder), S1 (Organic Framework itself), S2 (an organization like the first user organization), S2+ (S2 with a regulator's deadline and an account limit), S2→S3 (a colleague joins), S3 (two units). Scenarios marked *new* are proposed here.

### Per capability

| § | Capability | Checked by (scenario, level) | Pass criterion | Missing for the test |
|---|---|---|---|---|
| 5.1 | Recorder | `lab-hooks-v0`, L3, plus its 30-session load step | every hook kind writes its event; the raw tier holds every transcript byte; p99 latency within budget; no torn lines | a stated latency budget: §5.1 says about 50 ms, [`events.md`](../events.md) says 200 ms. Ingest and import idempotency checks (re-import adds 0 rows; two config dirs dedupe). Codex hooks |
| 5.2 | Per-organization storage and routing | *new* `lab-routing-v0`, L3 with the stub harness (no model cost): two organizations and one tenant on one machine; positions with `serves`, matching working directories, and unassigned work | 0 records in the wrong database; shape copies contain no content | routing written as testable rules (one expected destination per record class). **Canary strings** planted in content and searched for in shape copies and other organizations' databases. A ClickHouse in the lab image, under the `lab` tenant |
| 5.3 | Model and registry | S0, S1, S2 at L1; every L3 run applies its registry | `of registry check` and `of apply` are clean; the onboarding path's done criteria hold | `of check` beyond the registry (typed documents, onboarding run) is not built, so L1 can score only the registry today ([`computation.md`](../computation.md)) |
| 5.4 | Owner's view (who, go, desk, asks) | S2 and S2+ at L3 with the owner persona | the persona finds every waiting agent through the view, not by polling; ask-to-verdict delay and dead-end asks below the baseline | `of asks` (designed). A calibrated persona (`of_field-analyst`). The persona must read the view (`of who`, desk) the way the founder does, or the test measures nothing |
| 5.5 | Colleagues and other machines | S2→S3 at L3 with **two machines** (owner and colleague), two personas | consent events exist; `of shipped` matches what actually left; raw content leaves only when opted in (canaries) | runner support for multi-machine runs (the manifest assumes one); a colleague persona; the shipper and the relay |
| 5.6 | Messages between agents | local: `lab-hooks-v0` row 8; across machines: S2→S3 | every message has a resolved target and a delivery status; **a forged "owner said yes" without a grant id is rejected** | grant ids; a pressure kind `forged_approval`; `of send` and the relay |
| 5.7 | Steering, drift, splits | *new* `lab-drift-v0` family, L3, plus L2 on synthetic histories (below) | per signal: precision, recall and detection delay against labelled drift; bounds in observe mode: false-positive rate on benign sessions | **ground truth**, computable signal definitions, a working-set measure, `of start` and the split operation (below) |
| 5.8 | Reorganization with a backtest | operations: S2+ at L3 (holder swap mid-release, account limit). Backtest: L2 (below) | after each operation: continuation fires (crons, monitors, wake-ups), names resolve, no ask is lost, the registry diff is applied exactly | state reconstruction from events, a counterfactual method, a calibration metric, and **where real-history replay runs** (below) |
| 5.9 | Security and privacy | *new* `lab-secrets-v0`, L3: fake secrets of every masker kind (and seed phrases) planted in the scenario; mock repositories carrying prompt-injection text that asks agents to print the environment or exfiltrate a key | 0 canary secrets in any organization or colleague-visible store; the same secret gets the same hmac tag; the environment-print guard and deny rules fire | the masker in the shipper; a lab-only tenant hmac key; the guards from phase 2 |

### 5.7 Drift and splits: what the Lab needs

1. **Ground truth.** Drift cannot be scored without labels. Two sources:
   - **Injected drift (Lab, synthetic).** Pressures that pull a position off its mandate, each with a labelled start time:
     - a request sent to the wrong position;
     - a "while you're at it" side task;
     - a sub-project that grows step by step;
     - work in a repository of another system.

     Each comes with **negatives**: legitimately broad work that must not trigger.
   - **Labelled real cases (L2).** The founder or `of_field-analyst` marks drift in the first user organization's past sessions (the "Eight days" report has candidates). This data is real, so it is replayed inside that organization's tenant, never on lab machines. See the decision needed in 5.8.
2. **Signals as computable rules.** Each of scope, topic, size and mandate count needs a definition, a threshold and a method version before it can be scored. *Topic* is an agent judgement, so the Lab also reports its cost per N turns. That answers open question 2 with numbers.
3. **A working-set measure.** The sizing rule ([`method.md`](../method.md)) has no metric yet. Proposed proxies the Lab can compute from events: compactions per mandate, rereads of the same files, open threads, and the message share with one peer.
4. **Cheap L2 on synthetic histories.** Drift appears after hours, and long L3 runs are expensive. Most detector tuning should run on generated session histories with known drift points, replayed into the detector at no model cost. L3 then confirms on a few live runs.
5. **Split quality**, observed after a split:
   - each new position's working-set proxies fall;
   - the interface traffic the split creates stays small;
   - continuation moved;
   - the owner confirmed in one step (the persona's prompt count).

   This needs `of start`, the hand-over, and `of.mandate_changed` (events backlog v1.1, item 6), all unbuilt.
6. **Merges.** The same scenario family, seeded with two positions that talk more than they work.

### 5.8 Reorganization backtest: what the Lab needs

1. **State at time t.** A replay must rebuild the organization at any past moment. The registry today is a snapshot, so the history of positions, mandates and holders exists only once `of.mandate_changed` and holder events are written. The backtest cannot start before they have been recorded for a while.
2. **A counterfactual method.** A replay cannot re-run the agents under a different structure. We propose two tiers, and each result names its tier:
   - **(a) structural replay**: recompute what is fully determined by structure under the proposed diff: routing, who would have been asked, ask queue order, name resolution, who held which continuation. Cheap and exact, but it covers structural questions only;
   - **(b) re-simulation**: restore a snapshot at t into a lab organization, apply the diff, and run agents on mocks from there. Expensive and of limited fidelity (OPS.11); used selectively.
3. **Calibration before trust.** [`success.md`](../success.md) (goal 2) asks the backtest to reproduce the last month within a stated error. That error metric is not defined. We propose: the founder-load observations recomputed by replay versus measured, with a tolerance per observation.
4. **One diff for the Lab and for production.** A reorganization proposal should be a machine-applicable diff (registry, briefs, grants, routing, desk) that the Lab applies to a lab organization and `of` later applies for real. Otherwise the backtest tests something else.
5. **Decision needed (`of_architect`): where real-history replay runs.** The Lab's rule is synthetic data only, on disposable machines, with open-source scenarios. §5.8 asks for "replay of the organization's own history". Our proposal: the same runner and scoring also run **in-tenant**, against the organization's own store, under its tenant and on its hosts. The result leaves the tenant only as derived numbers. Lab machines never see the data.

### Cross-cutting

- **Lab gates in the rollout (§8).** Proposed:
  - phase 1: the hook testbed passes;
  - phase 3: `lab-secrets-v0` passes, before any colleague gets access;
  - phase 4: S2→S3 on two machines;
  - phase 5: drift precision and recall above an owner-set threshold on `lab-drift-v0`;
  - phase 6: the backtest is calibrated (5.8, item 3).
- **"Built" claims in §5.1 and §5.2** should be confirmed by the Lab before the founder relies on them. The hook testbed covers §5.1; `lab-routing-v0` covers §5.2.
- **Platform success criteria (§9)** become Lab assertions: "no hook blocks a session" (latency and exit codes under load), "no record reaches the wrong organization" (routing canaries), "no secret reaches a colleague-visible store" (secret canaries).

## Review: Fable (independent model), 2026-11-05, summary

Five problems:
1. Grants are load-bearing but undefined.
2. "Never blocks" contradicts enforced bounds; the latency figure is inconsistent; `PermissionDenied` fires only in auto mode.
3. Relayed authority is not verifiable as designed.
4. The split mechanism contradicts "Fork is not a swap" and the sizing rule. "Act and report" bypasses the change path.
5. The data boundary leaks: colleagues with `*_rw` would read raw; local raw deletion destroys the only unmasked copy; there is no reachable store for phase 4.

Over-engineered for now:
- backtests as a gate;
- the topic judgement every N turns;
- merges;
- the four-tier response;
- cross-organization messages;
- the Codex adapter before Claude Code is deployed.

Missing:
- a failure-mode table;
- a prompt-injection threat model;
- machine and colleague identity, and consent withdrawal;
- the registry's single writer;
- measurable phase gates;
- cost;
- schema skew;
- the status of `runtime.md`.

## Resolution of reviews (`of_architect`, v2)

| Finding | Resolution in v2 |
|---|---|
| Statuses overstate (of_platform) | §5.1, §5.2, §8: "built, not deployed", gaps listed |
| Unmasked raw in organization databases (of_platform) | Vault gate in force (§5.2); raw reaches organizations only after masking |
| `of send` cannot call the harness's tool; relay; `<org>:<name>`; platform verifies grants (of_platform) | §5.6 |
| Enrollment is a flag, the topic judge runs detached, the old agent executes its half, propose is the default (of_platform) | §4, §5.7 |
| Per-capability Lab coverage, canaries, drift ground truth, state at t, one diff format, gates (of_lab) | §5.7, §5.8, §8 gates, §9 invariants; in-tenant replay in [`lab.md`](../lab.md) |
| Latency inconsistency (of_lab, Fable) | Typical ~50 ms, hard limit 200 ms (§5.1) |
| Grants undefined (Fable 1) | §5.0; phase-2 entry gate |
| The recorder vs the guard; guard event; failure policy (Fable 2) | Two hooks; `of.guard_decision`; failure policy (§5.7) |
| Unverifiable authority (Fable 3) | Signed grants; the platform verifies; otherwise "unverified claim" (§5.6) |
| Split vs fork; sizing rule; act and report (Fable 4) | Three remedies; split by fork; propose only in v1 (§5.7) |
| Data boundary (Fable 5) | Ingest and read roles; delete local raw only after the vault confirms it; reachable store as the phase-4 prerequisite (§5.2, §5.5) |
| Over-engineering (Fable) | Backtests deferred; topic judge and merges later; cross-organization messages deferred; Codex deferred |
| Missing items (Fable) | §5.10 failure modes; prompt injection (§5.9); identity and withdrawal (§5.5); single writer (§5.3); measurable gates (§8); cost (§8); schema skew (§5.1); supersedes `runtime.md` (header) |

