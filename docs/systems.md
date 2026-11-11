# System breakdown in Organic Framework

Status: **Draft**, 2026-11-05. It follows the founder's request to put a system breakdown in front of everything else: the system, its subsystems and its supersystem, "by all the standards". Terms: [`glossary.md`](glossary.md). Its first application was a user organization's system breakdown (app material, private).

## Why first

Founder, 2026-11-05: there are now too many agents, each responsible for its own system. Interaction between them has become complex, releases cannot be pushed through, and there are many dependencies between the systems and services. Organic Framework must be finished and introduced.

Our reading: the agents were created lane by lane, so the map of agents does not follow the map of systems. Several agents act on one system, and dependencies between systems are known only to whoever remembers them. A system breakdown comes first because every agent, release and dependency is then placed on it.

## Naming (founder, 2026-11-05)

- What we have called a keeper position is called a **system agent**: the agent that answers for one system.
- **An agent is named by its system and its role** (founder, 2026-11-05), for example `storefront.operator` or `k8s-platform.keeper`. A project is named `<system>/<task>`, for example `storefront/send-check`. Every agent has exactly one canonical name: the pane title, the messaging handle and the name used in memory and in agents' messages. Older names stay only as aliases in the registry.
- **First platform step** (agreed 2026-11-05): an **address book of agents**, built from the live panes, session transcripts and messaging handles. On top of it come `of go <any name>` (jump to the agent's pane), `of who` (one table of agents, systems and states) and renaming panes to canonical names. Next: states from transcripts, one queue of asks to the owner, then agents bound to systems and a release coordinator.
- **Runtime** (founder, 2026-11-05): Zellij is not required. Organic Framework does not build its own terminal manager; its job is the system breakdown, integration between systems, roles and positions. herdr is the candidate runtime: it supports many agent CLIs, not only Claude Code. herdr was adopted on 2026-11-05 ([`herdr.md`](herdr.md)), so the canonical name takes herdr's agent-name form `[a-z][a-z0-9_-]{0,31}` (no dots), for example `storefront_operator` (our proposal).
- **Our reading, to confirm:**
  - "Position" stays as the general framework term underneath (ontology group A). A system agent is a position whose subject is a system.
  - Every other kind of work is placed relative to a system: a project is a change to a system, an analyst observes a system for its stakeholders, a coordinator works across systems.
  - The organization is itself a system: the creating system. Its owner is the system agent of that system.

## Standards used

| Source | What we take |
|---|---|
| ISO/IEC/IEEE 15288 (system life cycle processes) | **System of interest**; **system elements**, decomposed recursively until each element can be made, bought or reused; **enabling systems**, which support the system of interest in a life-cycle stage without being part of it; **interoperating systems** in its operational environment |
| ISO/IEC/IEEE 42010 (architecture description) | Stakeholders and their concerns; views, each answering named concerns |
| ISO/IEC/IEEE 21839 and 21841 (systems of systems) | Constituent systems that are **independently owned and governed**; SoS types: directed, acknowledged, collaborative, virtual |
| FPF A.1 (holons), A.1.STM | Part–whole by holons. "Repeat for the creators": the creating system is a system of its own. "A factory is not a constituent of its product" |
| FPF SYSE.Reference:2 | One system appears in several structures: part of a containing system, user, neighbour, bearer of functions, product of realization. Describe the environment through **containing, using, neighbouring and interacting** systems. **"Call a System a suprasystem only when the engineered System under discussion is its proper part."** Ownership does not prove parthood; interaction does not prove membership |
| FPF SYSE.Reference:4, SYSE.3 | Recursive realization: builder and enabling systems may themselves need platforms and engineering |
| FPF SYSE.12 | The engineering platform is an engineered system when it is deliberately developed to enable named work |
| FPF SYSE.18 | Coordinate commitments, interfaces and configurations with independently governed constituents, respecting the limits of control |
| FPF SYSE.11, SYSE.14, SYSE.20 | Obtaining an increment is not authorizing its use (release). Actual dependencies and shared resources decide what work can overlap |

## The breakdown for one system

Every system in the map is described by the same seven relations. The first three are parthood; the rest are not.

| Relation | Question | Example kind |
|---|---|---|
| **Supersystem** (containing system) | Of which system is this one a proper part? | The target system for a bot |
| **Subsystems** (elements) | Which proper parts does it have, down to what one agent can answer for? | Collectors, state, search, executors |
| **Using systems** | Which systems use its outputs? | Accounting uses execution records |
| **Interacting and neighbouring systems** | Which systems does it exchange with at run time without containing them? | Blockchain nodes, venues, other searchers |
| **Enabling systems** | What does it need to be built, tested, released, run and observed? | CI, cluster, observability, secrets |
| **Creating system** | Which part of the organization creates and operates it? | Its system agent and the projects on it |
| **Governance** | Who owns it, and is it independently governed? | Another person's system is an SoS constituent |

Rules (our reading):
1. **One system, one system agent.** Only it writes to the system in production. This is the single-writer rule the first user organization arrived at after an incident on 2026-10-30.
2. **The system agent's grain follows the breakdown.** A system gets its own agent when it has its own release, its own production state or its own owner; otherwise it is a subsystem inside a larger agent's system.
3. **Projects hang under a system.** A project is a change to one system, reported to that system's agent. A change that touches several systems is a cross-system change and goes to a coordinator.
4. **Dependencies are edges of the map, not memory.** "Uses", "interacts with" and "enabled by" are recorded with their interfaces. A release follows those edges: it is planned from the graph, not from what an agent remembers.
5. **Independently governed systems keep their owner.** An agent works with them through an interface and a time-boxed grant, as the AI Factory keeper did.

## Three chains of systems

The three levels in [`mission.md`](mission.md) are three applications of the same breakdown:

target system ← creating system (the organization) ← Organic Framework

The organization's own system (books, tax, licences, records) is a system in this map too. That is where the management functions check in [`method.md`](method.md) applies.

## What this changes in the ontology (to confirm)

- **Group A** gains a **system** concept with the seven relations above. A position of the system-agent kind points to one system.
- **Group C** gains a **release**: an authorization to use a new configuration of one or more systems (SYSE.14), planned from the dependency edges.
- **Group E**: changing the breakdown (split, merge or re-parent a system) goes through the same change path as changing positions.
