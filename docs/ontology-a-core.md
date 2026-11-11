# Ontology, group A: Core (the organization at rest)

Status: **Draft for founder review**, 2026-11-03. It was rebuilt after an ontology review (a private report, 2026-11-03) and replaces the earlier `ontology-2-organization.md`. Map and kinds: [`ontology.md`](ontology.md). Terms: [`glossary.md`](glossary.md).

The FPF grounding is the OCE suite of edition `c3b34de`. The examples come from the eight-day retrospective of the first user organization.

## Unit

A **creating subsystem**.

- Units nest recursively. The **root unit is the organization**: the app, in the Rails analogy.
- Every unit has:
  - an owner position;
  - a contribution;
  - its stakeholders, internal or external;
  - its risk appetite (group B).
- Every unit may contain positions of every kind.
- Data belongs to the **tenant**, not to the unit (group F). One tenant can run several organizations: today the founder's tenant runs his own company and the build team.
- Units are parts of the creating organization, not of the target system. A.1.STM: "a factory is not a constituent of its product."

FPF grounding:
- A.1.STM: "Repeat for the creators that must themselves be obtained."
- OCE.4: contribution architecture.

*The first user organization (our reading, not a decision):*
- the root unit, the company;
- inside it, the trading bot unit (the pilot operator, the host baseline, RFC lanes, analysts of the bot's results);
- the platform unit (the cluster-migration project and the lanes it coordinates);
- the CI unit.

## Position

A durable institutional place in a unit through which an expected contribution is made, whoever holds it.

**Identity** follows OCE.5:4.1 and OCE.5:4.2:
- an owning unit;
- an establishment basis: who established it, when, and by which decision;
- identity-bearing criteria: the expected contribution and the eligible holder kinds;
- a continuation condition;
- a scope and an interval.

"Holder or description change alone preserves identity unless the applicable basis says otherwise" (OCE.5:4.1). A position therefore survives vacancy, a change of holder, a change of title and a revised description.

A **position description** is not a separate object. It is a rendering of the expected contribution and the current mandate.

**Attributes:**
- kind: owner, keeper, project, analyst or coordinator (optional);
- lifetime class;
- current mandate;
- the resources it owns, its grants and the rules that bind it (group B).

**Addressing.** Messages, grants, asks and Work records address a position, never a session or a pane (R1). The platform resolves the position to its current holder at delivery time.

**Cheap establishment** (R9; the founder spawns lanes in one sentence). A position is established either from a brief or by installing a Bench package:
- it takes under a minute;
- its defaults come from the spawner, the unit and the organization ("convention over configuration");
- a coordinator or an owner may establish positions within its grant;
- the establishment basis is always recorded as an event.

*The first user organization:* "production operator of the trading bot" is a position, and one agent session is its current holder. The position persisted through four account switches.

## Lifetime class

The class is independent of the kind.

| Class | Ends when | Example from the first user organization |
|---|---|---|
| Durable | It is abolished by a decision | the trading bot's production operator, CI owner |
| Bounded | Its mandate is accepted as done, or it is abandoned | An RFC lane through to turnkey delivery |
| Ephemeral | Its single result is returned or a timeout passes; it closes itself | `archive-nodes-k8s`, closed after six minutes |

## Holder

The **one who holds a position**: a human, or an agent **session with its history** (founder decision, 2026-11-03).

- **Holders are systems in a role.** A.13: "agent" "is not a fundamental FPF type". Holding a position is a role that a system plays.
- **Why the session.** The history carries the context needed to play the role. This deliberately deviates from Library.Reference:9.3.4 (see the grounding notes in [`ontology.md`](ontology.md)).
- **What keeps the holder.** Resume, compaction and an account switch keep the holder. An account switch is a Workplace change that suspends continuation (retrospective M5).
- **Model and harness are attributes of the holder.** A change of model is recorded as a requalification event for the same holder.
- **The full history lives in the event log**, which is synced. The session's working context is a lossy view of it; the pilot had 60 compactions. The history survives both compaction and the loss of a machine.
- **Capability is separate from assignment.** OCE.6:4.1: "Assignment remains usable as a relation claim even when capability fit fails." Capability under conditions is A.2.2. For an agent holder, its history is a large part of that capability.

## Assignment

A **dated relation** that places a holder in a position (A.2.1, OCE.6).

**States** (OCE.6:4.1): proposed, decided-not-effective, effective, contradicted, expired, suspended, missing.
- **Suspended** always carries a reason, who suspended it and a resume condition. It is visible to the owner and to peers (R3). In the retrospective, a budget governor froze 9 lanes for about 14 hours without anyone seeing it.
- **Missing** means the position expects a holder but none is reachable. In the retrospective, the AI Factory keeper was unreachable by name.

**Holder swap.** One assignment ends and another starts. The position, its mandate and its continuation state stay; the history does not.
- The new holder's context is built from the continuation state, the mandate and the rules, and from the predecessor's history where the owner allows it.
- Friction F3, re-explaining after resumes and switches, is the cost of not doing this.
- A **bulk swap**, for example on an account switch, moves N assignments at once as one platform operation (R2).

**Fork is not a swap.** Forking a session creates a second holder with the same history. It is the natural way to **split** an overloaded position without losing context (the pilot split, retrospective §7.3). The fork takes the new position, and the original holder keeps the old one.

**An assignment grants nothing by itself.** Permission, access, resources and authority are established separately, each by its own owner (OCE.6:4.1 step 7; group B).

## Owner

The **position that owns a unit** (founder decision, 2026-11-03). Its expected contribution:
- design the unit's structure as a system;
- watch how it works and tune it; keep it in order;
- steer positions, especially projects, and point out problems;
- allocate capital and budgets (money, hardware, tokens, advertising);
- set the risk appetite;
- make sure the value chain pays the stakeholders and the shareholders.

Properties:
- **Envelopes.** The owner sets the envelopes (structure, budgets, risk, grants) that every other position in the unit works within.
- **The root owner is human.** Allocation of capital and risk may be automated, as in proprietary-trading firms. Top-level allocation decisions and accountability stay with people, who provide the capital and answer for the risk.
- **Lower owners may be agents** (founder, 2026-11-03). Such an owner divides the envelope it received from above among its unit's positions. Example: the bootstrap agent of an installed Bench package owns the unit it sets up.
- **Agents may perform and decide; humans answer.** Accountability is the part of the owner's role that never leaves people.
- **Accountable human.** Every agent-held position resolves to exactly one: the holder of the nearest human-held owner position up the unit tree. In exports, for example under EU AI Act Art. 14/26, this derived value is materialized per position as the named natural-person overseer the standards survey requires.
- **Replaceable.** Like any position, the owner position can change holders. A person's area of responsibility is the set of units whose owner positions they hold (group E).
- **Not a bare relation.** "Lead" in older documents means this position.

*The first user organization:* the founder holds the owner position of its root unit, and of the build team.

## Coordinates within grant

A relation: a position, which may be agent-held, allocates work, capacity or new positions to other positions within an envelope granted by its owner (retrospective M1).

- It is distinct from the owner's terminal decisions. Without the distinction, every allocation returns to the founder, which is the bottleneck he removed.
- A **coordinator** is a position whose expected contribution is mostly this relation. Any kind may also coordinate a little.

*The first user organization:*
- a dispatcher project spawned 13 lanes;
- the runner coordinator grants slots;
- the cluster-migration project coordinates a GitOps lane;
- the pilot coordinates a journals lane.

## Mandate

What a position is charged with **now**. A position has **one active mandate**; others are parked (founder decision, 2026-11-03).

**Fields:**
- goal;
- done criteria;
- asks: the points where the owner's yes is needed. They reference **ask** events in group C;
- the coordinator, where there is one, as a reference.

What to read first and the stages belong to the **method** (group C). Rules are referenced, not copied (group B). The field set is distilled from the dispatcher's 19 briefs.

**States:** active, parked, superseded, done (friction F6: duplicate RFCs, abandoned lanes).

**Mandate versus expected contribution:**
- the **expected contribution** is stable and identity-bearing (OCE.5);
- the **mandate** changes freely without changing the position's identity.

This resolves the scope drift the retrospective saw: a dispatcher project went from RFC lane to dispatcher to policy broadcaster. Drift of the mandate is cheap. Drift of the expected contribution means either a recorded re-establishment or a new position, decided in one step by the owner or by a coordinator within its grant.

## Open questions

1. Which units does the first user organization actually have? The list above is a reading of the retrospective, not a decision.
