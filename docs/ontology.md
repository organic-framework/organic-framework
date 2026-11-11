# Ontology of Organic Framework

Status: **Structure agreed** with the founder, 2026-11-03. It was rebuilt after an ontology review (a private report, 2026-11-03).

Definitions written so far:
- group A: [`ontology-a-core.md`](ontology-a-core.md).
- the system breakdown (system, system agent, release; founder, 2026-11-05): [`systems.md`](systems.md).

The other groups are listed here with their concepts and still need definitions. Terms and their aliases are in [`glossary.md`](glossary.md).

Sources:
- the mission, goals, problems and the human's role in [`mission.md`](mission.md);
- the framework parts in [`framework.md`](framework.md);
- the first user organization's retrospective (lessons R1–R11, M1–M7) and the founder-friction report;
- FPF edition `c3b34de`.

## Principle: trace first

**The event log is the substrate.** The model state at time t is a projection of that log. The founder put the sync layer first and asked for full digitalization, so events, messages, machines and continuation state belong in the core, not only in simulation.

## Minimal core

These twelve concepts are what a solo founder with a dozen agents needs to start, and what the first proving ground needs first.

| # | Concept | One line |
|---|---|---|
| 1 | **Unit** | A creating subsystem. Units nest, and the root unit is the organization. |
| 2 | **Position** | A durable place in a unit with an expected contribution. Attributes: kind, lifetime class, current mandate. |
| 3 | **Holder** | A human, or an agent session with its history. Model and harness are attributes. |
| 4 | **Assignment** | A dated holder-in-position relation, with the OCE.6 states. |
| 5 | **Owner** | The position that owns a unit: its structure, value chain, capital and risk appetite. Human-held at the top. |
| 6 | **Mandate** | What a position is charged with now: goal, done criteria, asks. |
| 7 | **Grant** | Permission: scope, autonomy tier, optional counter, expiry and provenance. |
| 8 | **Rule** | How something must be done or is never done, with its origin and rationale. |
| 9 | **Resource** | Something a position owns or shares. Shared resources carry budgets and priorities. |
| 10 | **Workplace** | Where a holder runs: machine, runtime account, scheduled triggers, continuation state. |
| 11 | **Event** | An append-only, dated record addressed to a position. Work records, messages, asks, steering and state changes are all events. |
| 12 | **Target system** | What the organization builds and runs. |

The owner's **risk appetite** is an attribute of a unit, not a separate core concept.

## Groups

| Group | Concepts | Definitions |
|---|---|---|
| **A. Core**: the organization at rest | unit (root = organization); position (expected contribution, kind, lifetime class); holder; assignment; owner; mandate; *coordinates-within-grant* | [`ontology-a-core.md`](ontology-a-core.md) |
| **B. Authority and resources** | **Grant**:<br>• scope;<br>• tier (observe / propose / act-with-approval / act-and-report);<br>• optional counting-down counter (E.16);<br>• expiry;<br>• provenance (standing / per-step / relayed, with a verifiable relay chain).<br><br>**Rule**: origin, scope, rationale; binds assignments and is re-delivered when it changes.<br><br>**Resource**: owned or shared. Shared resources carry budgets, priorities and admission; capital, hardware, tokens, money and the lead's attention are all budgeted resources. Secrets are a resource attribute, masked at write.<br><br>**Risk appetite**: set by the owner per unit; it bounds the grants inside the unit. | to write |
| **C. Execution**: the organization in motion | **Workplace**.<br><br>**Machine**: identity and trust.<br><br>**Method**: AGENTS.md, runbooks.<br><br>**Event**, schema-versioned. Event types:<br>• Work record, with artifact references;<br>• message between positions;<br>• ask: proposal → verdict → reason → outcome link;<br>• steer: the owner's direction to a position;<br>• state change.<br><br>**Position runtime state**: working / waiting on the owner / waiting on a peer / suspended / idle.<br><br>**Continuation state**. | to write |
| **D. Purpose and evaluation** | stakeholder (internal or external, adversarial included, shareholders included; discovered and studied continuously, see [`method.md`](method.md)); interest (expressed or latent); **coverage**: which position owns each interest, and in which form (serve / observe / protect / relate); stakeholder attributes and states as learned from the first map (see [`method.md`](method.md)); mission; business case; target system; result; observation of success (per result, C.39; per position is our extension); founder load per result | to write |
| **E. Evolution** | structure (the designed unit tree, OCE.4); change operations (spawn, close, swap holder, fork, split, merge, promote, park, supersede); reorganization increment (OCE.9); environment; backtest; reward signal; verdicts as labelled data; a person's area of responsibility; practice; bringing a colleague into the practice (FPF: OCE.18 and the Human Capability Engineering suite) | to write |
| **F. Ecosystem** | **onboarding path** (a method per subject kind), **onboarding run** and **onboarding level** L0–L5, see [`onboarding.md`](onboarding.md); tenant; external organization and its interface; package (unit blueprint + bootstrap position); manifest; installation (onboarding); fitting (CUST.2, CUST.3:4.3); local residue; learned rules; installation analysis; opt-in diagnostics; version and upgrade; data ownership, consent and the company's share; schema version | to write |

**Changing the structure is done with great care** (founder, 2026-11-03). Every change operation in group E follows the same path:

propose → diff and backtest → owner's yes → apply → observe

Every change can be reversed. Agents propose changes and observe their effects; the owner decides and owns the result.

## Position kinds

Kind is an optional attribute of a position. It names what the position answers for.

| Kind | Answers for | Examples from the first user organization |
|---|---|---|
| **Owner** | The unit itself: its structure, its value chain, capital and risk appetite. The owner sets the envelopes that the other kinds work within. | The founder, owner of his company and of the build team |
| **Keeper** | Keeping something running. A **system keeper** owns one software system (bots, databases, cluster). A **process keeper** runs a business process or function across systems (security, CVE patching, accounting). | the trading bot's operator, its hosts, CI, the forecasting system's keeper; a kernel CVE patching campaign, an accountant, a competitor census |
| **Project** | A structured change: research, an RFC, implementation, improvement | RFC lanes, turnkey lanes |
| **Analyst** | Whether the target system succeeds for its stakeholders, and what the market and competitors do. It proposes direction and decides within its grant. The founder: "like directors", the management layer. | Top-down analysis (#1841), expectation gaps (#2272), market-share decomposition, Spy, management reports |
| **Coordinator** | Distributing work and capacity within the owner's envelope. A position is a coordinator when its expected contribution is mostly the *coordinates-within-grant* relation; any kind may also coordinate a little. | The dispatcher, the runner coordinator, the budget governor |

**The owner is the creator one level up.** The other four kinds work on the target system. The owner works on the unit itself: for the owner, the unit is its system. It designs the unit, keeps it in order, changes it, and checks that its value chain pays.

The owner of the root unit is a human. Allocation of capital and risk may be automated at any level. Top-level allocation decisions and accountability for risk stay with people, because people provide the capital and answer for the risk.

An owner further down may be an agent, which divides the envelope it received from above (founder, 2026-11-03). Example: the bootstrap agent of an installed Bench package owns the unit it sets up.

The human's irreducible part is accountability: **agents may perform and decide; humans answer.**

**Hypothesis.** Each link of the value cycle has an owning kind:

| Link of the cycle | Owning kind |
|---|---|
| Stakeholders and results → renewed mission | Analyst |
| Mission → organization | Coordinator |
| Organization → change of the target system | Project |
| Target system → service to stakeholders | Keeper |

The owner owns the whole cycle of its unit. This is not proven; the founder wants to test it in the environment.

**Not a position.** Subagents are helpers inside one position's work. They need no assignment of their own.

## Decisions on the review questions

Agreed with the founder on 2026-11-03:

1. **The owner (lead) is a position.** It is durable and human-held at the top. Its expected contribution is the structure, the value chain, capital and risk.
2. **A tenant is not an organization.** The founder's tenant runs two apps today: his own company and the build team.
3. **The autonomy tier is part of a grant.** It is not a separate object.
4. **One active mandate per position.** Other mandates are parked.
5. **A partner's agents.** They are an external organization with an interface until they join the sync layer. After that, they become positions in the organization.
6. **Each app is a business, and its data is separate** (founder, 2026-11-05). There is one trace database per organization. The point is **clean data, not security on the founder's Mac**. When colleagues join an organization, they see only that organization's traces, not the founder's other projects ([`storage.md`](storage.md)).
   - The founder works with everything on one screen. There are **no separate OS users or machines per organization** (founder's correction, 2026-11-05).
   - Routing keeps the data clean. Each record goes by its position to that position's organization, else by working-directory paths, else to an unassigned store that only the tenant sees. Nothing reaches an organization's database by default.
7. **Work across organizations: content follows its source, process follows the builder** (founder, 2026-11-05, refining our first reading). The case is a position of one organization working on another organization's material, for example `of_field-analyst`. It builds Organic Framework while working on a user organization's data, and Organic Framework must keep a trace of its own building too. The boundary goes by **what a record holds**, not by the session:
   1. **Content follows its source.** The full trace (text, tool output, paths) goes to the organization the mandate serves. The registry carries `serves: <org>`.
   2. **Process follows the builder.** The position's own organization gets a **shape** copy of the same sessions:
      - who worked, under which mandate, when;
      - the model and the tools used;
      - tokens;
      - the timing from ask to answer;
      - the lengths of messages and outputs.

      It contains no text, no tool output and no paths. An allow-list per record type decides this, and the records carry `of.shape: true` ([`storage.md`](storage.md), [`events.md`](events.md)).
   3. **Results cross the boundary only by publication.** This is a document cleaned of the source's specifics, plus an `of.published` event. The source keeps the full event; the receiver keeps a slim copy.
   4. **A plainly double mandate is split.** One session serves one organization; the steering layer will enforce it.

## Grounding notes

- **Holder = session with its history.** This deliberately deviates from Library.Reference:9.3.4, which treats the configured agent system (model, instructions, context, memory, tools) as the operative participant. The reason is the founder's: the history carries the context of the role. 9.3.4 itself lists preserving task state and an external memory as interventions that change capability.
- **Observation of success.** C.39 supports observation of success per result. Per position is our own extension.
- **Units are not parts of the target system.** A.1.STM warns: "a factory is not a constituent of its product". Units are parts of the creating organization, not of the target system.
