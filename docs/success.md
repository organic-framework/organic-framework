# How we observe that Organic Framework is moving

Status: **Draft**, 2026-11-03.

The founder asked Claude to define this, using Organic Framework itself. The targets below are Claude's proposals, and the founder adjusts them freely.

## Method

This is dogfooding. We apply the metamethod ([`method.md`](method.md)) to the Organic Framework build team, which is app number two:

- step 1: stakeholders and interests;
- step 2: goals, as given in [`mission.md`](mission.md);
- step 6: observation, per result.

FPF C.39:4.1 asks "which observation would distinguish an adequate result". For each goal we name such observations:
- **leading** signals, which move within weeks;
- **lagging** signals, which confirm the goal.

A million organizations is a goal for years. In the first year the signals measure **proof**, not scale.

## Stakeholders of Organic Framework and their interests

| Stakeholder | Class | Main interests |
|---|---|---|
| The founder and the shareholders | internal | The mission; the founder's own throughput; the first user organization's success |
| Solo founders and small AI-first companies | external, adopter | Start in minutes; less plumbing; more throughput for less money |
| Larger companies | external, adopter | Governance and compliance; transforming an existing organization; bringing colleagues into the practice; replaceability |
| People working inside adopters | external | Their area of responsibility grows; clarity; learning the practice; not being surveilled or scored unfairly (EU AI Act Annex III) |
| Bench package authors (people, agents, vendors) | external | Reach, reputation, revenue share, safe feedback from installations |
| Open-source contributors (people and agents) | external | Influence, recognition, a fair process for changes |
| AI labs | external | Organization-level environments and benchmarks; outcome-linked trajectories with consent (goal 2) |
| Regulators (EU AI Act, UAE PDPL, DIFC) | external | Oversight evidence, logging, data protection |
| Model and harness vendors | external | Their tools stay first-class. Watch for platform dependence; partnership is possible. |
| The FPF author and community | external | Correct use of FPF and attribution |
| Incumbents and hyperscalers | adversarial | Bundling agent registries into their suites; forking an open core into their clouds |
| Attackers | adversarial | Raw traces and grants are a valuable target |
| Beneficiaries of humanity's hard problems | external | Problems actually solved (goal 3) |

## Observations per goal

### Goal 1: adoption at scale

**Leading signals (the first user organization first, then outside):**
- **The owner's attention is used for throughput.** This signal follows the theory of constraints and throughput accounting (TOC/TA).
  - **The drum.** In an AI-first organization the owner's attention is the constraint of the improvement process: agents produce changes faster than the owner can decide on them.
  - **What T is.** Throughput (T) is money in minus truly variable costs. Each organization defines it from its business case. Operating expense (OE) and investment (I) are recorded alongside it.
  - **Primary observations:**
    - **(a) Constraint time on throughput work:** the share of the owner's time spent on positions whose results move T.
    - **(b) Lost constraint time:** plumbing (routing, relaying, access, capacity, status polls) and needless asks. Targets: plumbing under 1 in 20 of the owner's messages; status polls near zero, because the owner's view answers them.
    - **(c) Decision inventory in front of the constraint:** open asks and proposals waiting on the owner, with their count, age and expected ΔT. The queue is ordered by expected ΔT per minute of the owner's time. An ask stays in the inventory until it is closed (answered, withdrawn, superseded or expired). Two metrics follow:
      - **dead-end asks:** asks with no answer for longer than N hours;
      - **agent-hours decisions waited on the owner:** p50, p90 and the sum.
    - **(d) ΔT released per owner decision:** expected at the ask, measured after deployment.
    - **(e) T with and without large events**, and net profit NP = T − OE once OE is recorded.
  - **Watch for the next constraint:** waiting on shared capacity (runners, account limits, budget pauses), in hours per cause. TOC says that once one constraint is relieved the next one appears; this metric should show it early.
  - **Secondary:** merged results per owner message, as a flow metric. Target above 1.5 at the first user organization. A merged result is inventory until it moves T.
  - **The first user organization** is measured first. Its definition of T and its baselines stay in its own repository. Methods: "How each observation is computed" below.
- **Coverage of the first user organization:**
  - the share of its active positions registered in Organic Framework reaches 100%;
  - holder swaps, such as account switches, lose no scheduled triggers or monitors.
- **A second human.** A colleague, for example from another team, runs their agents on Organic Framework, and their throughput rises (problem 2).
- **Time to a first working organization** for a new solo founder is under 15 minutes, measured on people who are not us.

**Milestone ladder (lagging):**
1. the first external organization;
2. 10 weekly-active organizations;
3. 100;
4. 1,000.

Every step also needs 4-week retention of organizations above 40%.

**North star (proposal):** weekly-active organizations in which Organic Framework measurably reduced the owner's load per result. This counts adoption and value together. It avoids counting sign-ups.

#### How each observation is computed

Goal 1's founder-load observations are computed from the event log of the trace and sync system ([`framework.md`](framework.md)). Until that log exists, they are computed from imported transcripts with keyword classifiers; the first baseline was measured that way in the first user organization (2026-11-05). Every derived label stores its classifier version, so that a baseline can be recomputed under a new method and compared.

| Observation | Computed as | Events and fields it needs |
|---|---|---|
| **(a) Constraint time on throughput work** | Owner messages (later: owner `focus` time) on positions whose mandate is meant to move T, divided by the total. Each position carries a throughput class (raise T, operate T, OE and infrastructure, measurement, coordination) | `human_prompt` with target position; `focus` (opt-in); the position's throughput class in the registry |
| **(c) Decision inventory** | Open asks and proposals waiting on the owner at a moment: count, age distribution, and the sum of their expected ΔT. Dead-end asks: open longer than N hours. Agent-hours waited: per closed ask, answer time minus ask time; p50, p90 and the sum | `ask` with `expected_dT` and its evidence ref; ask lifecycle (open → answered / withdrawn / superseded / expired) with the ask id on the answer; the age from `ask.ts` |
| **Waiting on shared capacity** | Hours positions spend blocked, per cause (runner, CI, account limit, budget pause) | `state_change` with `blocked_on` and its end |
| **(d) ΔT per owner decision** | Expected ΔT on the verdict; measured ΔT after the deployment that carries the decision | `verdict` → `work_record` → `deployment` (release with its PRs and positions) → measured ΔT from the organization's T source |
| **(e) T and NP** | T per window, with and without large events; NP = T − OE | The organization's T and OE sources, through an app adapter (not the framework core) |
| **Founder messages** | Count of human prompts to positions in a window. Excludes prompts injected by agents, launch briefs, scheduled prompts and interrupts | `human_prompt`: id, ts, human, **target position**, holder session, machine, channel (terminal, herdr, phone), input mode (typed / dictated / pasted, with paste source). Separate `agent_injected_prompt` with the injecting position |
| **Merged results per founder message** | Merged pull requests (later: accepted results of any kind) in the window ÷ founder messages | `work_record` for each accepted result with its **author position** and mandate |
| **Plumbing share** | Founder messages whose intent is routing, relaying, access, capacity or tooling, or a status poll ÷ all founder messages | Plumbing acts as first-class events where possible: `route` (from, to), `relay` (source message), `access_grant` (resource, scope), `capacity_change` (account, plan, limit), `status_request`. Otherwise `human_prompt.intent` from a versioned classifier |
| **Status polls** | Count of `status_request` events, and how many came after the lead's view was opened | `status_request` (to a position or broadcast); `lead_view_opened` (which positions were shown) |
| **Approvals and relayed approvals** | Verdicts on asks, split by kind: answered with a bare yes; asked though a standing grant covered it (needless); demanded in formal wording; and grants that reached the acting position through a relay | `ask` (id, from position, kind, blocking); `verdict` (ask id, decision, reason, grant id); `grant` (issuer, scope, tier, expiry, provenance: first-hand / standing / relayed with the message-id chain); `of.mandate_check` (admit / deny / missing) |
| **Time from an agent's question to the founder's answer** | `verdict.ts − ask.ts` per ask: p50 / p75 / p90, count over 60 min, waiting hours | The same ask id on both events; position state `waiting_on_owner` with start and end |
| **Concurrent positions** | Positions in state *working* per 5-minute bucket: mean, p90, peak; also distinct active positions per day, and positions waiting on the owner | `state_change` (position, holder, state: working / waiting on owner / waiting on peer / suspended / idle, reason); `session_lifecycle` (spawn with spawner and brief, resume, holder swap, compaction, close) |
| **Names the founder had to look up** | Failed or ambiguous name resolutions, by humans and by positions; founder prompts that route by a name rather than through the view | `name_resolution` (requester, name given, alias matched, result: resolved / none / ambiguous); `message.delivery` (delivered / held / expired / unresolved) |
| **Founder time** (proposed addition) | Time with each position's pane or view in front; notifications shown and acted on | `focus` events from the terminal or herdr; opt-in desktop events for time outside it (browser, password manager, chat) |

**Rules for the log.**

1. Every event carries a position name (canonical, from the registry) as well as a session id, because session names drift.
2. Imported history and live events share one schema. Imported events keep their source path and offset.
3. Events have stable ids so that copies of a transcript in several places count once.
4. Prompts are masked for secrets at write.
5. Human focus and time events are opt-in (EU AI Act Annex III).

**Not computable from transcripts today:** the target position of a prompt; whether a prompt was typed, dictated, pasted or injected by an agent; the pairing of an answer with its question; grants and their provenance; idle versus waiting versus suspended; the founder's focus and time.

### Goal 2: teaching AI to build systems

**Leading signals:**
- **Linked Work records.** At the first user organization, at least 80% of Work records link to a result, and at least 80% of asks carry a verdict and a reason as fields, not prose.
- **The reward chain.** Owner decision → PR → deployment → measured ΔT of throughput is recorded end to end. This chain is the reward signal for reinforcement learning ([`mission.md`](mission.md), goal 2).
- **Backtest calibration.** A backtest reproduces the last month of the first user organization from the event log within a stated error before any reorganization forecast is trusted (OPS.11's caution).
- **Package environments.** Bench packages ship environments: the first package has one, then most packages do.

**Lagging signals:**
1. an open Org-Bench is published;
2. a lab runs it;
3. a lab trains or evaluates on it.

### Goal 3: humanity's hard problems

**Leading signals:**
- the list of 10–20 problems is written and published;
- each problem has a named stakeholder map: who suffers, who could solve it.

**Lagging signal:** an organization working on a listed problem runs on Organic Framework.

### Health of the framework itself

- **Bench:** packages that have at least one successful installation outside their author's organization, by installation analysis.
- **Contributions:** external contributors (people and agents) and the time to a decision on their changes.
- **Ontology stability:** schema versions released, and changes that break existing apps. The target is none without a migration.

## Who watches what

These are analyst positions in the build team. The owner, who is the founder, sets the targets and the budgets.

| Analyst position | Watches |
|---|---|
| Proving-ground analyst | The first user organization's signals of goals 1 and 2 |
| Adoption analyst | External organizations, retention, time to a first organization |
| Ecosystem analyst | Bench and contributions |

Goal 3 is the owner's own.

## Horizons (proposal)

| Months | Focus |
|---|---|
| 0–3 | The first user organization runs on Organic Framework. Founder load per result improves. A second human joins. Work records link to results. |
| 3–6 | First external organizations. First package with an environment. Backtest calibrated in the first user organization. First design partner. |
| 6–12 | 100 weekly-active organizations. Org-Bench published. |
