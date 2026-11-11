# Glossary

Status: **Draft**, 2026-11-03. This is the project's bounded context (A.1.1). Use the preferred term in project documents. Aliases are listed so that older documents and reports stay readable.

The full definitions are in [`ontology.md`](ontology.md) and [`ontology-a-core.md`](ontology-a-core.md).

| Preferred term | Aliases (avoid in new text) | Meaning in one line |
|---|---|---|
| **Position** | lane, role (when it means a place, not a kind), pane | A durable place in a unit with an expected contribution |
| **Position kind** | role type, "whale" | What a position answers for: owner, keeper, project, analyst, coordinator |
| **Holder** | session (for agents), incumbent | Who holds a position: a human, or an agent session with its history |
| **Assignment** | appointment | The dated relation of a holder to a position |
| **Owner** | lead, human lead, principal | The position that owns a unit: structure, value chain, capital, risk |
| **Accountable human** | named overseer | The holder of the nearest human-held owner position up the tree |
| **Keeper** | steward | The kind that keeps a system or a process running |
| **System agent** | system keeper, keeper (for a system), system owner (when it means the agent) | The agent that answers for one system; only it writes to that system in production (founder, 2026-11-05) |
| **System** | service (when it means the whole) | A node of the system breakdown, with its supersystem, subsystems, using, interacting and enabling systems; see [`systems.md`](systems.md) |
| **Enabling system** | infrastructure, platform (when it means support) | A system that supports another in a life-cycle stage without being part of it (ISO/IEC/IEEE 15288) |
| **Supersystem** | suprasystem, parent system | The system of which this one is a proper part; interaction or ownership alone does not make one |
| **Onboarding path** | migration, rollout, setup (when it means taking a subject under OF) | A method that takes one kind of subject (organization, machine, system, position, holder, package) from outside OF to operating under it; runs and levels L0–L5 in [`onboarding.md`](onboarding.md) |
| **Release** | rollout, deploy (when it means the decision) | Authorization to use a new configuration of one or more systems (FPF SYSE.14) |
| **Mandate** | brief, charge | What a position is charged with now |
| **Grant** | permission, approval, autonomy (when it means what a position may do) | Scope, tier, counter, expiry, provenance |
| **Rule** | convention, guardrail (a guardrail is a bundle, not one object) | How something must be done or is never done, with its origin |
| **Resource** | capacity | Something a position owns or shares; shared resources carry budgets |
| **Unit** | team, department | A creating subsystem; the root unit is the organization |
| **Organization** | app, company | The root unit of one creating organization |
| **Tenant** | — | The owner of data; it may run several organizations |
| **Workplace** | — | Where a holder runs: machine, account, triggers, continuation state |
| **Event** | trace record, Work record (one event type) | An append-only, dated record addressed to a position |
| **Ask** | question to the lead, approval request | An event: proposal → verdict → reason → outcome link |
| **Steer** | direction, correction | An event: the owner's direction to a position |
| **Package** | role package, Bench package | A unit blueprint plus a bootstrap position |
| **Environment** | test case, showcase, simulator scenario | A reproducible scenario in which a unit runs and its result is observed |
