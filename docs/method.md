# Method part of Organic Framework

Status: **Sketch**, 2026-11-03. This is a first pass of the "Method" part in [`framework.md`](framework.md). The founder asked to work it out here and not to go deep yet. Terms: [`glossary.md`](glossary.md).

## Three levels of method

Domain methods differ for everyone (founder, 2026-11-03). Operational management, Kubernetes keeping and building a trading bot share no content. Organic Framework therefore separates three levels, following FPF's own split into Core, DPF and LPF:

| Level | What it is | Where it lives |
|---|---|---|
| **Metamethod** | One method for every domain: how to take a unit through the value cycle, and how to recover, adopt, fit and introduce any other method | The framework |
| **Domain methods** | Kubernetes keeping, building a trading bot, operational management, tax compliance in a given country | Bench packages, as practices |
| **Local methods** | A company's runbooks, its AGENTS.md and its rules | The app (organization) |

Organic Framework itself carries only the metamethod and the **method model**: one way to describe, compose, fit and transfer any method. Methods for Kubernetes and for trading are then described in the same shape, whatever their content.

## Method model

- **Method, description and work are separate** (FPF A.3, A.3.1).
  - A **method** is a reusable way of doing, with its applicability, preconditions and intended effect.
  - A **method description** is a carrier: AGENTS.md, a skill (SKILL.md), a runbook.
  - **Work** is a run, recorded as events.
  - A.3.1 warns against reading "a diagram … for work authorization" or "a program for proof of operational success".
- **A practice is the composable unit.** Here we follow OMG Essence: methods are composed from practices on top of a small domain-independent kernel. A position's way of working is a composition of practices from the metamethod, its packages and local rules. FPF cites Essence 1.2 and treats the Essence 2.0 beta (March 2026) as neighboring method engineering. Situational Method Engineering contributes composition for a given situation.
- **The life of a method in an organization:**
  1. Recover the method from observed work: A.3.1.MR, applied to the event log.
  2. Describe it.
  3. Qualify its fit and transfer it: A.3.1.FT, for example when installing a package.
  4. Introduce it into practice and decide what to keep or change: A.3.1.MI, for example when bringing in a colleague.
  5. Observe Work against it: A.15, role–method–work alignment.
  6. Revise it.
- **Calibrate to agent economics** (friction F1). Validation lengths, estimates and the threshold for asking the owner come from the organization's actual capacity and reversibility, not from human norms.

## The metamethod: one pass of the value cycle for a unit

The metamethod repeats at every level of the unit tree.

1. **Stakeholders and interests.** Discover the stakeholders, study them, collect evidence and keep their interests current (see below). This step never ends.
2. **Mission and business case.** State them for the unit from the interests it serves.
3. **Structure.** Derive units and positions from interests. Each interest that matters gets a position that serves or protects it.
4. **Staffing.** Establish the positions from briefs or install them from Bench, then assign holders.
5. **Running.** Positions work within grants, budgets and rules.
6. **Observation.** Analysts observe the result for each stakeholder and the use of the owner's attention.
7. **Change.** Propose → diff and backtest → owner's yes → apply → observe.

### Structure grows organically

Founder, 2026-11-03.

- Organic Framework **proposes** an organization structure, studies it and supplements it as work goes on. It does not design the whole structure up front.
- At the start, AI agents do not have the company's full context. The structure therefore grows organically as context is discovered, and the method must make that growth efficient.
- Every proposal goes through the change path: propose → diff and backtest → owner's yes → apply → observe.
- This is where the name "Organic" comes from.

### Sizing positions: ontological load against context

Founder, 2026-11-05: the key parameter for whether to split a position is whether the objects it reasons about fit into its holder's context. Adding positions raises communication complexity, because agents must then tell each other things. Many positions pay off for large reasoning work, such as a system breakdown, where several participants each know their own subject well. For a project whose path is already clear, one position is better: it drives the work with subagents and other tools inside itself.

Our reading, to confirm:
- **The working set of a position.** These are the objects the position must hold together to reason correctly: systems, concepts, decisions, interfaces, stakeholders and live state. It is measured in tokens of the material that must be present, against the holder's *effective* context. Quality falls well before the nominal window, and compaction loses facts.
- **The working set is not the history.** A holder's context also fills with its own past turns. A position that is full of history but has a small working set does not need a split. It needs a fresh holder that reads the position's documents (a holder swap).
- **Coupling decides where to cut.** A split creates interface objects (contracts, schemas, messages) that both sides must now hold. Cut only along a seam where the interface is small compared with what each side stops holding. Highly coupled reasoning, such as one design model, stays in one head (compare Cognition's "Don't build multi-agents").
- **Three answers to growing work:**
  1. **One position with subagents.** The work is clear, decomposes into independent tasks, and needs no durable context. Subagents are disposable context; they are not positions.
  2. **Split into positions.** The working set exceeds the effective context and a low-coupling seam exists. Each new position carries a durable working set.
  3. **Merge positions.** Two positions exchange much more than they work alone, and their combined working set fits.
- **Throughput is not a reason to split.** Rate limits and budgets are addressed by parallel subagents or by more capacity, not by new positions, unless the working set also grows.
- **Signals:**
  - the holder rereads the same material;
  - errors come from forgotten facts;
  - repeated compaction on one mandate;
  - large interface traffic between two positions;
  - an owner who must relay between positions.
- **Grounding.** Team Topologies uses cognitive load as the primary sizing rule for teams. Parnas (information hiding) and the design structure matrix cut modules where coupling is low. Conway's law ties the communication structure to the structure of the result.

### One team: rails for working together

Founder, 2026-11-05:
- The founder works directly with any position, not only through one of them.
- Agents should in the end work together as members of one team.
- Organic Framework must give the rails for that.

Our reading, the first rails (to confirm):
- **One name everywhere**, resolved through the registry. Any member can find any other.
- **A readable position record** for each member: contribution, current mandate, interfaces and done criteria. Teammates read it before they ask.
- **Mandates are files, not chat**: `registry/briefs/<name>.md`. A new holder of the same position starts from the brief.
- **Asks between members are events.** Request → accepted → result with a link. Peer messages are the transport, not the record.
- **Work that touches another member's working set is announced to that member.** For example, a schema change is announced to everyone who reads the schema. Decisions that change the design go to the design owner.
- **The owner sees the team in one place**: names, mandates, states, and who waits for whom.

### Management functions check

The stakeholder map of the first user organization showed that agents' attention goes to the target system, while **the company as a system** (books, tax, licences, investors, records) has no positions.

The metamethod therefore checks a unit against the management functions of FPF's Management DPF Suite:
- strategy;
- goals;
- operations;
- administration;
- corporate governance;
- corporate finance;
- management accounting;
- marketing;
- human resources;
- leadership;
- organization change;
- engineering asset management;
- community;
- customization and productization.

Each function gets one of three answers:
- it has a position;
- it is deliberately not needed;
- it is a gap.

The check is domain-independent, so it belongs to the framework. Its domain content, for example UAE free-zone accounting, comes from Bench packages.

## Stakeholder work

The founder (2026-11-03) calls this a key element of Organic Framework: **interests come from stakeholders, and units of systems come out of interests.**

- **The gap today.** At the first user organization many stakeholders' interests are not covered by any current position's work. Examples: accounting, regulators, hackers, and many more ("millions").
- **Discovery and study.** Organic Framework identifies stakeholders and, ideally, studies them right away: it collects evidence and runs analytics on each.
- **Stakeholder packages in Bench.** Bench may carry packages for classes of stakeholders, for example tax authorities of different countries. Such a package bundles knowledge of the stakeholder and practices for serving it.
- **Our reading (not yet confirmed):**
  - **Coverage.** The owner's value-chain view shows a matrix of stakeholders, their interests and the positions that own each interest. An uncovered interest is a visible gap, not a surprise.
  - **Adversarial stakeholders** belong in the model too. Hackers and competitors have interests opposed to ours, and positions exist to protect against them. A competitor census is such work.
  - **Stakeholders are internal or external.** For the platform unit, the bot units are stakeholders.
  - **Lifecycle states.** Essence's Stakeholders and Opportunity alphas give a ready state model to check. Stakeholders: recognized → represented → involved → in agreement → satisfied for deployment → satisfied in use.
- **Grounding:**
  - FPF A.1.STM: "Agree what system must succeed for whom and in what use."
  - PSDA.12: recover participants, concerns and affected systems.
  - CGOV.6: conflicts and related-party interests.
  - D.2: ethical concerns across levels.
  - ISO/IEC/IEEE 42010: stakeholders and concerns (standards survey).

### Lessons from the first stakeholder map (the first user organization, 2026-11-03)

Source: the organization's own stakeholder map (app material, private). The map found 33 stakeholders: 4 covered, 17 partial, 12 not covered.

- **Coverage follows the agents' work, and the agents work only on the target system.** The company as a legal and financial entity had no position at all: shareholders and investors, tax, licensing, the virtual-asset regulator, AML, the bank.
- **Trace-first discovery is blind to stakeholders the organization ignores.** Every uncovered stakeholder was found through non-agent sources (a dormant data room, public research). The stakeholder step therefore needs:
  - checklists per organization type and jurisdiction. These are the first Bench stakeholder packages;
  - scans of non-agent sources: documents, bank and bill inboxes.
- **The stakeholder object needs more than an Essence-style state:**
  - class;
  - relation type (contractual / statutory / voluntary / opposed / market);
  - jurisdiction;
  - a calendar of obligations, as events addressed to a position;
  - a relationship owner and a coverage form (serve / observe / protect / relate);
  - the consequence if uncovered;
  - a confirmation state (hypothesized → confirmed by an expert);
  - the last signal (staleness);
  - data sensitivity.
- **Adversaries need a threat state** (observed → modelled → mitigated → monitored), not "satisfied". Covering an opposed interest means protecting against it.
- **The payer may be the market, not a customer.** At the first user organization, a trading company, it is the market. Step 5 of the value cycle must not assume a customer.

## Growing methods bottom-up

Full digitalization makes A.3.1.MR practical: methods can be recovered from the event log rather than written from scratch. Methods already visible in the first user organization's retrospective:
- **turnkey change:** RFC → review by a different model → staged delivery;
- **the reporting method**, written after "I don't like your reports";
- **incident → rule:** a correction becomes a rule with its origin and rationale within minutes;
- **keeper of an absent owner's system**, with an explicit hand-back.

Recovered methods also serve problem 2: a colleague is shown the method recovered from the founder's work instead of a retelling.

## To study next

- OMG Essence 1.2 and the 2.0 beta: kernel, alphas, practices.
- Situational Method Engineering and ISO/IEC 24744.
- The FPF A.3.1 family (A.3.1.MR, A.3.1.FT, A.3.1.MI) and A.15.
- Practice libraries as Bench material: SRE, ITIL 4 practices, DORA capabilities.
