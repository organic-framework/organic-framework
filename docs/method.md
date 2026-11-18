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

### Subagents: the owner's standing yes (2026-11-16, `of_architect`)

An organization's owner may give every position a standing yes to run the independent parts of its work as subagents, in parallel, rather than one after another in its own turns. The founder gave it for his organizations. He said this about a user organization's search decider, which had named its own hours as its only limit although its approved process names its workers as its subagents: "Again, the only limit is [its] own time. It can simply launch subagents. For some reason it does not" (2026-11-16, prompt `01a1e393-ded4` in `of_conductor`'s session; relayed in message `01a1e394-376d`). A minute later he widened it: "we need, for everyone where work has to be parallelised, done fast, attacked, iterated toward a goal and so on, to allow launching subagents" (prompt `01a1e395-8d97`, its last word corrected to "subagents" in `01a1e395-b062`).

- **Why it must be said.** Claude Code's description of its Agent tool tells an agent not to spawn subagents unless the user asks, and to use the tool only when the user explicitly says so (as read on 2026-11-16). A position's user is its owner, and no brief or start context said that the owner asks. So the yes is stated where every session reads it, in its owner's own words: the organization's AGENTS.md, the start context of every enrolled position, and the brief `of spawn` types (below).
- **What runs as a subagent.** Parts that need no durable context and do not wait on each other:
  - a loop's variants and hypotheses, one per subagent (RFC 0005 §4.5);
  - searches over code, documents or the web;
  - a review of a draft by a fresh context that has none of its author's;
  - reading a long record into a short answer;
  - watching a runner: a babysitter on the loop's `babysit_model` ([`capacity.md`](capacity.md)).
- **The conditions are the rules already in force.**
  - A subagent that changes files works in its own git worktree, so parallel changes never meet in one checkout.
  - Heavy jobs run on runners or in CI, never on a laptop.
  - A loop's variants in flight are arms: the work's `wip_arms` is set to the number it runs at once (RFC 0005 §4.4).
  - A subagent's model follows its task: the position's own model for reasoning, a smaller one for watching and reading.
- **A subagent is not a position** ([`ontology.md`](ontology.md)). What needs the position stays with the position:
  - decisions on its cells and its loops;
  - asks to the owner;
  - messages to other positions;
  - `of status` and the registry;
  - commits to a shared branch.

  A subagent reports to the position that launched it.
- **It holds where the organization's owner said yes.** The organization's registry records that yes in a top-level `subagents:`, with the owner's words and the ids that resolve to them (docs/registry.md, "Subagents"). Only then do the start context and the spawn brief state it. OF never tells one organization's positions that another's owner asked (organic-framework PR 241 review).
- **Where it is said:**
  - the framework's own AGENTS.md (2026-11-16), for the OF team;
  - each organization's AGENTS.md, by its structure keeper;
  - every enrolled position's start context, and one sentence in `of spawn`'s brief, where the organization's registry records the owner's yes (`of_platform`, to build).

### Work starts when it is ready (2026-11-17, `of_architect`)

The founder, 2026-11-17 (prompt `01a1e9ab-a3ba`): "I'm tired of chasing the whole org structure myself. Where does this dragging out of time come from?" `of_conductor` (message `01a1e9ac-036e`) found five patterns that day in a user organization. Each moved only after he pushed:
- ready work booked into later slots: four items set at 13:18Z for 14:25Z to 18:45Z; a release cadence planned from a week later, then from the next day, and "from now" only at his third push; a backup fix planned "by Wednesday 12:00Z" until he said now;
- one change at a time per keeper, and queues ordered by the keepers' own preference, with no check that the changes conflict;
- limits the positions had set themselves (a memory line, a count of sends, a freeze) held as hard until he questioned them;
- owners passing plans up without challenging their dates;
- "tomorrow 12:00Z" as a default deadline, with no reason.

**Where the delay comes from** (inference, unconfirmed):
- **Human calendars, copied.** Agents plan like the human teams in the text they learned from: slots, business-hours deadlines, one change at a time. Those habits protect people's attention and working hours. An agent has neither limit. What waits is the owner's attention, and every slot spends it.
- **A guess loses its author.** A limit written into a plan without its basis reads, a turn later, as a rule. Nobody can tell that it was a guess, so nobody drops it.
- **Relays check readings, not dates.** The relay rule (AGENTS.md, "A conclusion about an event rests on readings taken where the event happens") made positions check where a reading was taken. Nothing asked them to check a date.
- **Waits are invisible.** A session cron or a background wait loop is seen by its own agent alone. Of that organization's 21 positions with a turn end between 12:00Z and 13:25Z that day, 16 held a session cron or a background task at their last turn end in that span, one of them 30 (`of.continuation`, which lists both at each turn end; a background task there waits on the clock or on a condition such as a CI run, so not all of them are bookings). Product development's queues are invisible in general (Reinertsen, *The Principles of Product Development Flow*, 2009, the principle of invisible inventory); an agent's are invisible even to its manager.

**The rule** (AGENTS.md, "Work starts when it is ready"): work runs as soon as what it needs exists. A later start, an order between items, a limit and a deadline each carry their basis, or they do not hold.
- **A wait that names its basis holds.** The forms OF has: `of status waiting --on <position|founder>`, an owner's card, and a loop's `of loop wait --on runner|window|release|holdout|ask|position|calendar` ([`diagnose.md`](diagnose.md) §2.29, where a wait on the calendar holds nothing). A booked time with none of these is not a wait. A clock time holds only when it is the basis's own time: a window's start, a booked slot, a limit's reset, a run's expected end. A wait on a position, a decision or a run waits for that event, not for a time picked ahead of it. On 2026-11-17 an alarm at 16:10Z waited on another position's decision before a change booked for 16:40Z; the decision was a basis for waiting on it, not for either time (`of_platform`'s replay for hand-over `277de578`, message `01a1e9ec-00d2`).
  A step before a booked slot (a preparation, a kit, a check) runs as soon as it can, not at a lead picked before the slot, unless it names why it cannot run earlier: what it makes expires or goes stale (a credential's lifetime, a snapshot that must be fresh), or the people it needs are there only from then. Run early, it finds its problems while there is still time before the slot. A slot holds when it has a source (a release calendar, a window the owner or a third party booked); a slot a position picks for itself is a booking (`of_architect`, 2026-11-17, on the five disputed items of the same replay, message `01a1ea1a-e261`).
- **Parallel is the default.** Two items run at once unless they touch the same system, host, file or resource. A position runs its independent parts as subagents where its owner said yes (above).
- **A limit you set is a proposal.** It states its purpose, and it is dropped or put to the owner when it stops work. The owner's limit (its ask id) and a measured one hold.
- **A deadline is real or absent.** It has a source: a legal date, a booked window, a limit's reset, a meeting the owner named. No default.
- **A relay checks dates.** A manager, a conductor or a structure keeper checks each date and order of a plan before passing it up, and sends one without a basis back to the doer. When the owner has to say "now", the doer checks every other date of that plan in the same turn, not only the one he named.

**What OF watches** (hand-over `277de578` to `of_platform`, 2026-11-17, to build):
- At each turn end, a position's booked starts (its session crons, its background waits, and the planned times, orders, limits and deadlines in its last message) pass a cheap filter, then a small model judges each for a basis. One without a basis is typed back into the position's pane at once: run it now, or name what it waits on.
- The owner's push on tempo ("now", "why later", "don't wait") becomes a kind of founder correction (`of corrections`), counted per position in the structure keeper's daily digest.

**The measure** is the owner's pushes on tempo per organization per day, which should fall. The booked starts without a basis, per position, lead it.

### Users' success: a service other people use is watched after they start (2026-11-19, `of_architect`)

On 2026-11-18 two services were found broken by the people using them before any agent knew: a user organization's own service, whose users found an outage first, and OF's questions panel, which another organization's agent could not set up. The founder asked for customer-success roles for services people use (relayed by `of_conductor`, message `01a1f05e-4aba`).

- **What it is** (the practice of customer success management; sources below). It is the ongoing work of making sure the people who started using a service keep getting the result they came for. Onboarding gets them to the first useful use. Support answers when they report a problem. Success comes between and after the two: it reads their health, and reaches them before they report.
- **What a success role does after onboarding:**
  - **keeps a health line per user** (an organization, a team or a person), red, yellow or green, from evidence: use over time, failures, open issues, progress toward the goal agreed at the start;
  - **watches use and failures, and acts before a report.** A failed setup or upgrade, a check that went red, a feature that stopped being used: it tells the user what it saw, with the machine and version, and offers the fix;
  - **reviews adoption with the user** at the agreed points: actual use against the goal, the next step;
  - **closes the loop.** It hands a symptom with its impact to support, a defect with its evidence to engineering (the system's keeper), and the workflow and obstacle to product, and it follows each to an answer the user can see.
- **The signals it reads without asking the user** come from the service's own records. For OF these are each machine's health (docs/health.md), failed `of init` and `of spawn` runs, guard denials that stop a feature, the user organization's framework matters through its structure keeper, and its people's questions about OF. A stale signal is a gap, named as unknown, never as healthy.
- **Through whom.** For OF the success role is `of_customer-success`, which an organization's agents and people may write to on the channel below (the founder, 2026-11-18 20:57Z, prompt `01a1f073-4cfc`; AGENTS.md, "The OF team organizes; each organization operates"). OF reaches an organization back only on that channel, or through its structure keeper. An organization's own success role reaches its users through the channels its owner allows.
- **One role can carry it** while users are few: an agreed goal per user, the health line, one place for incoming issues.
- **For OF's own users, the organization pays for its own success** (the founder, 2026-11-18 20:54Z, prompt `01a1f070-e3c9`: "spending our tokens on scaling other organizations is a non-scaling method, we simply will not have enough tokens. But making a mechanism for talking to a customer success agent for every organization is a good thing. So an organization's minimal structure should also have of_architect, and some public access to talk to a customer success agent"; and 20:57Z, prompt `01a1f073-4cfc`: "And of_architect must be in every minimal org structure"):
  - **Each organization watches its own OF health on its own tokens.** Its of_architect is its own structure keeper, `<org>_org`, which `of init` writes from the onboarding package and which stays after the run, beside its system architect. That reading was `of_architect`'s, and the founder approved it on card `OF-90` (2026-11-18 21:41Z), whose text named `<org>_org` as the architect of every organization. The structure keeper reads its organization's OF signals above and brings each framework matter to OF; it fixes what is its own.
  - **OF has one customer-success agent of its own** (the founder, 20:57Z, prompt `01a1f073-4cfc`: "on OF's side there must also be a customer success agent, who talks with the user company's agents or with its people"): `of_customer-success`, which talks with any organization's agents and people about framework matters.
  - **The channel, in two tiers** ([`support.md`](support.md); the founder, 20:59Z, prompt `01a1f075-109f`): a public support API for a registered organization's agents and people, and issues on the public framework repository for everyone else. **To build:** the issue template (OF version, command, what happened, what was expected, no organization's names or data) and the repository's private vulnerability reporting for security matters (`of_customer-success` with `of_release`, hand-over `df1fd1ff`); the API (`of_server`, hand-over `4cb3ed69`). The asker runs its own pre-publish check on its text first. An organization OF's relay reaches may also write from its `<org>_org` under a grant.
  - **OF pays only for the answers** (to build, `of_customer-success`, hand-over `df1fd1ff`). It reads new issues in batches, through Codex read runs first (RFC 0008), answers framework matters only, with the document that answers them, hands a defect to the keeper of the OF system involved, turns a question asked twice into a document or a fix, and never does an organization's work. It records its spend per issue, so the channel's cost to OF per organization stays visible.
- **Sources** (gathered by a Codex research run on 2026-11-18, `of subagent run`, record `01a1f05e-fc3c`; not re-read here): B. Hochstein et al., "Customer success management, customer health, and retention in B2B industries", International Journal of Research in Marketing, 2023, doi 10.1016/j.ijresmar.2023.09.002; GitLab Handbook, "CSM Responsibilities and Services" and "Customer Health Scoring", 2026; First Round Review, "The Founder's Guide to Building a V1 of Customer Success", 2025; Replicated documentation, "About instance and event data", for telemetry from installations customers run themselves.

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
