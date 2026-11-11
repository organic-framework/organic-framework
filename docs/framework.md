# What Organic Framework consists of

Status: **Draft**, 2026-11-03. The founder agreed to "Model and format". The other parts are a working proposal. Mission and goals: [`mission.md`](mission.md).

| Part | What it is | Why it exists |
|---|---|---|
| **Model and format** (agreed) | An open specification of the core objects: stakeholder, target system, position, holder, assignment, grant, budget, Workplace, Work record, rule. It includes an open format for Work records. | A shared language for people, agents and tools. The standard that AI-lab training builds on (goal 2). |
| **Method** (sketch: [`method.md`](method.md)) | Patterns for running the value cycle: stakeholder interests → mission → organization → target system → back. They cover how to design a position, restructure the organization and swap a holder, and both people and agents can read them. | This is what makes Organic Framework a framework rather than software. Its shape follows an FPF domain pattern framework (DPF). |
| **Platform** | Software that executes the model. At its heart is a sync layer that joins agents on any machine into one system. Around it: a position registry, a permission guard, Workplaces, a trace store, the lead's view, and adapters to any agent tool. | It makes the model work live, not only on paper. |
| **Bench** | Position packages, which carry their rules and runbooks, plus a registry and company fitting. | Reuse of positions and a network effect. |
| **Lab** (founder, 2026-11-05; was "Simulator") | A separate R&D part. It provides isolated environments, close to a virtual machine, in which the whole stack runs: the runtime, the agents, hooks, the event store and mock systems. Scenarios with a simulated owner run against them at three levels of fidelity ([`environments.md`](environments.md)), behind an interface shaped like a reinforcement-learning environment. | Test how Organic Framework, its hooks and systems behave before they reach a real organization. Continuous integration for the framework. Replay a reorganization before making it. The reinforcement-learning environments for goal 2 and an Org-Bench. |
| **Governance of Organic Framework itself** | Versioning, how changes are accepted, certification. | Organic Framework lives by its own cycle and survives a change of authors. |

**How documents become computation** (proposal, 2026-11-05): [`computation.md`](computation.md). Three layers: the framework model (schemas), the organization model (typed documents) and the event log. Four kinds of computation run between them: checks, projections, watchers and agent judgements.

## Recording and steering (founder's vision, 2026-11-05)

Founder, relayed by `of_platform`:
- Organic Framework switches on for certain sessions and steers them.
- It keeps them within their mandate, and splits a session into sub-positions when needed.
- It records everything into the database for analysis.
- It carries communication with agents on other machines.
- All of this is scoped per organization.

Our decomposition. Points 1 and 2 await the founder's confirmation.

1. **Two modes.**
   - **Recording** is always on, for every session (passive). See [`storage.md`](storage.md).
   - **Steering** applies only to **enrolled** sessions.
2. **The organization profile is the unit.** It holds the organization's registry, its database and its routing paths, plus its own harness settings where useful. It has no OS user of its own: the founder works on one screen (decision 6 in [`ontology.md`](ontology.md)). A session belongs to the organization its position serves; work across organizations follows decision 7.
3. **Enrollment.** `of start <position>` launches the holder in the runtime, under the canonical name, in the organization's profile, with its brief.
4. **Steering of enrolled sessions:**
   - **Mandate in context.** The brief and the current mandate are injected at session start and again after compaction.
   - **Route, don't block** (founder, 2026-11-05). An action outside the mandate is a signal about the structure. It is routed to the position that owns the work, or it goes to the organization's structure keeper, which recomputes the structure and proposes a split, merge or new position. Only critical actions are denied ([`rfc/0001-organic-framework-platform.md`](rfc/0001-organic-framework-platform.md), §5.7).
   - **Drift and sizing.** When the work leaves the mandate, or outgrows the sizing rule in [`method.md`](method.md), Organic Framework proposes sub-positions (a new session and a brief) for the owner to confirm.
5. **A sync server in front of the databases** (founder, 2026-11-05). Clients never write to the database directly. `of-server` ingests, validates, masks, buffers durably and writes in batches; it also relays messages and verifies grants (RFC 0001, §5.2a).
6. **Messages across machines.** `of send <position> <text>` resolves the holder and its machine from the registry. A message to a local holder goes direct. A message to a remote holder goes through the runtime's machine link, later through the sync system. Every message is logged in its organization's database. A message between organizations needs an explicit grant.

**How it maps to the ontology** (our reading):

| Mechanism | Ontology |
|---|---|
| Enrollment | an assignment becoming *effective* (OCE.6: proposed → decided → effective) in an organization's Workplace |
| Organization profile | the organization's registry, database and routing; Workplaces stay on the founder's machine |
| Mandate injection | the mandate (group A) delivered to the holder, together with the rules that bind the assignment (group B) |
| Bounds | grants with their autonomy tier (group B), enforced at the action; a denial is an ask event |
| Sub-positions | the change operations *spawn* and *split* (group E), on the change path: propose → diff → owner's yes → apply → observe |
| Cross-machine messages | messages between positions (group C), addressed by canonical name |

Proposed order:
1. recording;
2. `of start` with mandate injection;
3. bounds and the ask queue;
4. `of send`;
5. drift detection and sub-positions (this needs data first).

The platform design is `docs/runtime.md`, by `of_platform`.

## The sync layer comes first

The founder decided this on 2026-11-03.

- **The heart of the platform is a sync layer.** It joins agents wherever they run, in the cloud, on the founder's machine and on colleagues' machines, into one shared system.
- **Hosting Workplaces in the cloud is secondary.** A Workplace may stay on a laptop. The defect is not the laptop itself. The defect is that its agents are invisible to everyone else and that their state dies with the machine.
- **Our reading (not yet confirmed by the founder).** The sync layer carries:
  - positions and assignments: who holds what, and where;
  - mandates, grants and rules, including their propagation (R7);
  - addressing and messages between positions across machines (R1). Today's cross-session messages reach only sessions on the same Mac;
  - states, including suspended (R3);
  - questions to the lead;
  - capacity budgets;
  - Work records.
- **The trace and sync system** (founder, 2026-11-05). One Organic Framework system:
  - **stores all trajectories**: ideally the full context and the tokens sent between each harness and its models, the interactions between agents, and the human's prompts with their times;
  - **collects in real time and imports history**;
  - **connects colleagues' agents and the founder's other machines**, so the whole organization shares one layer for communication between agents;
  - **carries agent-to-agent communication across machines**, which herdr does today on one machine, at a larger scale;
  - **lets us verify and steer the adoption and use of Organic Framework** on colleagues' machines and other participants' machines, not only the founder's.
- **Capture the maximum** (founder, 2026-11-05). Every byte may be useful later, and storage is a database, not loose files.
  - **Raw tier.** Every transcript line from every harness, the full raw hook payloads, the runtime's event stream and snapshots of instructions (settings, `AGENTS.md`, memory). It is compressed and content-addressed, so copies dedupe.
  - **Typed tier.** The events of [`events.md`](events.md), each referencing its raw rows.
  - **Storage.** Locally, a SQLite database per machine (no daemon, full-text search). Later a central database fed by the sync system's shipper.
  - **Masking moves to the boundary.** Raw data stays unmasked on its machine. Anything that leaves the machine is masked or opt-in.
  - **Retention.** The harness's own cleanup must not delete history before it is captured.
  - Storage design: `docs/storage.md`, by `of_platform`.
- **Our reading:** the event schema is the system's interface and aligns with the OpenTelemetry GenAI conventions. Capturing exact model traffic, beyond what transcripts hold, needs a harness-side proxy, which is a later option.
- **The sync layer also serves problem 2.** Once a colleague's agents join the shared system, the practice becomes visible and can spread. What syncs from a colleague's machine is under their control (consent; EU AI Act Annex III limits).
- **Two streams, not one** (founder, 2026-11-03):
  1. **Model state, synced both ways.** The Organic Framework ontology objects (positions, assignments, grants and the rest) between every machine and the shared system.
  2. **Raw data, captured as fully as possible.** Transcripts, tool calls, messages, prompts, costs, timings and outcomes. Their purpose is future reorganization design and an environment, possibly for RL, in which each company can test how a reorganization would go before doing it.
- **Our reading (not yet confirmed).**
  - Keep the raw stream append-only and immutable, and treat model state as a projection of it (event sourcing). A reorganization can then be replayed against past events: a "backtest" of the organization, the way a trading company backtests its bots.
  - An environment must first reproduce the company's own recent past from the raw data before its forecasts are trusted. OPS.11 says a simulation compares its modeled alternatives; it does not establish that the actual operation has the same relations or effects.
  - The retrospective's metrics are candidate scoring functions: founder load per result, waits, polls and needless asks.
  - Raw data is the most sensitive data we hold. It stays in the company's own tenant. Secrets are masked at write. Anything leaving the company, for example to AI labs, is a separate opt-in. Data captured from colleagues' machines touches EU AI Act Annex III.
  - Scale: one founder produced about 3.2 GB of transcripts in eight days.

## Framework and apps

The founder decided this on 2026-11-03. Organic Framework is separated from the organizations built on it, in the way Ruby on Rails is separated from a Rails app:

- **The framework** is public and open source. It holds the model and format, the method, the platform, Bench and the simulator.
- **An app** is one creating organization built on the framework. It holds that organization's positions, rules, Workplaces, fitted packages, and everything derived from its own data. An app is private unless its owner publishes it.
- The first apps are the founder's own company and **the Organic Framework build team** itself.
- **Material derived from an app's data stays in that app.** One example is the retrospective of the first user organization's agents. Opening the framework repository must not require cleaning its history.
- **One repository per organization** (founder, 2026-11-05). The framework repository holds only the framework and no organization's data, not even the build team's.
  - Each app has its own repository with its registry, briefs, reports and `AGENTS.md`:
    - for example `~/acme/org` for Acme, kept by its structure keeper `acme_org`;
    - `~/organic-framework-build` for the build team, kept by `of_architect`.
  - Each machine lists the organizations it takes part in at `~/.config/of/orgs.yaml`. This matches the sync system: a machine joins organizations.
  - The registry schema lives in the framework, in [`registry.md`](registry.md). Organizations hold only instances.
  - Framework documents cite app evidence by repository and path, not by link.
  - Framework documents tell their examples of Acme, a fictional online retailer, and their lessons of "a user organization". The check before each public export keeps it so.
- **Convention over configuration.** This is our takeaway from the Rails analogy, not a founder decision yet. Sensible defaults let a position be created in under a minute (retrospective lesson R9) and keep the start light (goal 1).

## How we build it

- **Today.** The founder builds Organic Framework himself, with agents.
- **Later.** Once it is open source, other people join. Agents are contributors too, so the contribution process must work for agent contributors, not only for people.
- **Two proving grounds.** The build team is itself an app on Organic Framework (dogfooding), next to the founder's own company.
- **Capacity budget.** For now, the build team shares its capacity budget (tokens, compute, the founder's attention) with the founder's own company, and the budget is sufficient. Revisit when it becomes a constraint.
- **Open.** When the framework repository becomes public.

## The first user organization is the proving ground

The founder decided this on 2026-11-03. Every part is tried on his own company first: the method, the model and format, and the software. The first Bench packages are extracted from its own work.

**Bench direction (founder, 2026-11-03).** The first packages are system keepers for widely used systems: a Kubernetes cluster, ClickHouse, PostgreSQL, deployments. Industry best practice already provides most of the 90% for them. The retrospective's first candidate, the merge-queue keeper (the report calls it "steward"), is of the same kind: Mergify is a widely used system.

### Installing from Bench is onboarding a professional

The founder described this on 2026-11-03.

- **Why install from Bench.** Some positions are taken from Bench rather than written from a brief. A package can describe an ideal security officer or an ideal Kubernetes cluster manager, with its processes. Then no company has to reinvent a role that industry best practice already describes.
- **The analogy.** Imagine hiring a top DevOps engineer who nearly wrote Kubernetes. You still have to explain the company's goals, hand over the work and set the tasks. Installing a package must do the same.

What installation does (our structure for the founder's description; the FPF grounding is CUST.2 and CUST.3:4.3 for fitting, CUST.6 for repeatable installation, and OCE.6 for assignment; corrected after the ontology review):

1. **Introduce the company.** The package learns the context it lands in:
   - the target system, the unit and its stakeholders;
   - the neighboring positions;
   - the systems it will keep;
   - local conventions.
   This is the local residue, recovered by CUST.2.
2. **Set the mandate and the goals.** What this position is charged with here.
3. **Set guardrails.**
   - Grants, an autonomy budget and a capacity budget.
   - The lead (accountable human) and the coordinator.
   - The organization's rules, merged with the package's rules. Conflicts are surfaced, not silently resolved.
4. **Hand over.** If a holder already does this work, its context and open work move to the new position. Example: Kubernetes keeping moves out of a cluster-migration project's position.
5. **Trial.** The claim "the package fits this company" is tested on the company's representative tasks (SYSE.46). Autonomy is promoted on the lead's word or on evidence (retrospective lesson on promotion).

**A package bootstraps a unit, not a single agent** (founder, 2026-11-03).
- A Bench position is an agent with instructions. After installation it sets up its own creating system, which then ensures the success of its target. For the Kubernetes package, that target is the cluster: upgrades, monitoring, scaling for the needs of other sessions, redistributing capacity, and more.
- **Why not one prompt.** Such a role cannot be one-shotted from a single prompt. "Agent, you now manage Kubernetes" does not work. A package already describes, by industry best practice, what the role must do and how to build the system that keeps the cluster successful.
- **Agreed with the founder (2026-11-03).** A package is a unit blueprint plus a bootstrap position. The installed agent runs Organic Framework's cycle one level down:
  - its stakeholders are the other units that need the cluster;
  - its mission is the cluster's success for them;
  - it builds its own unit of keepers, a capacity coordinator, an analyst of cluster health and cost, and projects for migrations;
  - it observes success through SLOs.
  So the framework's method must be executable by agents, not only by humans.
- **No session history in a package.** A package carries instructions and best practice. Our suggestion: experience travels in a safe form, as rules with provenance learned in earlier installations (retrospective M7), never as another company's raw history.

**Stakeholder packages** (founder, 2026-11-03). Besides position packages, Bench may carry packages for classes of stakeholders, for example tax authorities of different countries: knowledge of the stakeholder plus practices for serving it. See [`method.md`](method.md).

**Bench analyzes every installed unit** (founder's idea, 2026-11-03).
- The package manager does not only distribute packages. It examines in detail what each installed unit does:
  - how it fitted;
  - what it did;
  - where it failed;
  - which local rules were added;
  - what success it reached.
- **Our reading.** This is the analyst kind at the Bench level. Bench is itself a creating organization: its products are packages, and the success of installed units is its value signal. The analysis feeds:
  - package improvement and upgrades (CUST.5 field learning, CUST.8);
  - rules learned across installations;
  - evidence that a package fits a kind of company.
- **Diagnostics are opt-in, like Apple's sharing with app developers** (founder, 2026-11-03).
  - Inside the company, the unit's activity is analyzed in full: it is the company's own data.
  - Reporting to Bench works like Apple's option to share diagnostics with app developers: off unless the company turns it on.
  - No private data leaves the company. By default not even public data leaves it: anything sent can be breached ("все могут хакнуть").
  - Our reading: diagnostics carry minimal derived signals only, such as failure signatures, fit results and rule-change counts, never content.

**Every package ships environments: test cases that show how its agents work** (founder, 2026-11-03).
- An environment is a reproducible scenario in which the package's unit runs and its result is observed. For the Kubernetes package, it is a test cluster with injected failures, an upgrade and a capacity crunch.
- **Our reading.** The same environments serve several purposes:
  - a demonstration for a company choosing a package;
  - regression tests that gate each package release;
  - evidence for certified packages (the trust tier);
  - representative tasks for the fit claim (SYSE.46).
- They are also the kind of environment AI labs train on (goal 2). Bench environments become the first concrete environments of the simulator part.
- Environments let package authors improve a package without customer data. This matches the strict diagnostics boundary above.

Evidence so far: a private retrospective of eight days of an owner-run agent organization.
