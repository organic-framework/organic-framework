# Environments for running Organic Framework

Status: **Proposal**, 2026-11-05, by `of_architect`.

Founder, 2026-11-05: start with an environment for running Organic Framework in different cases. Organic Framework must be tried in different situations, to see how it works and how it computes.

Related:
- "environment" is already a term (a reproducible scenario in which a unit runs and its result is observed; [`glossary.md`](glossary.md));
- Bench packages ship environments ([`framework.md`](framework.md));
- goal 2 needs the organization simulator as a reinforcement-learning environment ([`mission.md`](mission.md));
- computability: [`computation.md`](computation.md).

## What an environment is

An **environment** is one scenario plus a runner plus a score.
- **The scenario** is an organization at a stage ([`business-case.md`](business-case.md)). It has:
  - an initial state: the people and their roles, agents, systems and repositories, documents, a business-case fragment and the owner's goals;
  - a **script of pressures over time**: requests, incidents, a regulator's deadline, an account limit, a release that crosses systems, a colleague joining;
  - the **success observations** to compute ([`success.md`](success.md)).
- **The owner is simulated.** A persona plays the owner: goals, preferences, an attention budget, and how they answer a batch of questions. Without it, onboarding and attention cannot be tested. The persona is calibrated on the real founder's traces.
- **The runner** sets up the organization in a sandbox (a temporary organization repository, an isolated runtime session, mock systems) and lets agents work under the Organic Framework method for a number of steps.
- **The score** comes from the same computation as in real use: checks over the model, projections over the event log, the owner's attention spent, the time to structure, errors and missed obligations.

## Three levels of fidelity

| Level | What runs | What it tests | Cost |
|---|---|---|---|
| **1. Tabletop** | One or a few agents apply the method (onboarding path, systems breakdown, business case) to a written scenario and produce the organization model's documents. The owner persona answers. | The ontology and the method: are the documents complete, consistent and computable? Do the checks catch what they should? | minutes, cheap |
| **2. Replay** | Real history (a user organization's event log and transcripts) is replayed step by step. At each point: what would Organic Framework have shown, asked or done, and what actually happened? | Projections, watchers and metrics against reality; counterfactuals ("with the registry, incident 8 would not have happened") | cheap, once the import exists |
| **3. Live sandbox** | Real agents run in an isolated runtime session against mock systems: toy repositories, a fake CI, a fake market that emits T, and a regulator that sets deadlines. The owner persona plays the owner. | The whole loop: structure, coordination, desk, ask queue, releases, attention; the founder-load metrics under load | expensive; run selectively |

FPF OPS.11 warns that a simulation is evidence of a limited kind. Each result states its level, and level 1–3 results never replace the proving ground of a real organization.

## The scenario library (first set, our proposal)

| Scenario | Stage | What it stresses |
|---|---|---|
| A solo founder with an idea and 50 research agents | S0 | Structure from nothing, the strategic case, the attention budget |
| Organic Framework itself | S1 | Dogfooding: the management case, open-source positioning |
| A trading organization (anonymised from a real user organization's data) | S2 | Onboarding an existing fleet, the constraint on T, releases across systems |
| The same, plus a regulator's deadline and an account limit mid-release | S2 | Obligations calendar, holder swap with continuation, priorities |
| A colleague joins with their own agents | S2→S3 | The person path, the sync system, adoption on another machine |
| Two units under one owner | S3 | Recursion, delegation of risk, the competence library across units |
| Moving a position between machines (`lab-position-move-v0`, [`lab.md`](lab.md) section 11) | S2 | Holder swap across machines: resume, memory and paths, continuation, a single holder |
| An outsider's quickstart (`lab-quickstart-v0`, [`lab.md`](lab.md) section 12) | S0 | Profile 1: from nothing to named agents with recorded events in under 15 minutes |
| A team's self-hosted server (`lab-profile2-v0`, [`lab.md`](lab.md) section 13) | S2 | Profile 2: install, delivery, idempotency, durability, authorization, consent, relay |
| Secrets at the boundary (`lab-secrets-v0`, [`lab.md`](lab.md) section 14) | S2 | Canary secrets of every kind, injection that tries to get them printed, look-alikes; the gate for colleague access |

## What it gives the framework

- **Continuous integration for the framework itself.** Every change to the ontology, a schema, a check or the method runs the scenario suite at level 1, and selectively at levels 2 and 3. A change that makes the organizations worse is visible before it ships.
- **A forcing function for computability.** A scenario can only be scored through checks and projections, so every concept a scenario needs must become computable. This drives the conformance table in [`computation.md`](computation.md).
- **The seed of the products.** The same environments become Bench's environments (each package proves itself in them), the reinforcement-learning environments for goal 2, and an Org-Bench for AI labs.

## The lab: environments as isolated machines

Founder, 2026-11-05:
- Environments are a separate R&D part of Organic Framework, **the Lab**.
- They may lean towards reinforcement-learning environments.
- They are close to a virtual machine in which everything runs, so that we can test how our hooks and systems actually behave.

Our reading, to confirm:
- **An environment instance is a disposable machine**: a VM, isolated from the host. The founder chose to run Lab VMs on his own Mac (2026-11-05): Lima VMs with no mounts, no forwarding and a firewall to the host ([`lab.md`](lab.md)). It contains:
  - the runtime (herdr);
  - the agent harnesses (Claude Code, Codex);
  - the `of` CLI with its hooks and event store;
  - an organization repository built from the scenario;
  - mock systems: toy repositories, a fake CI, a fake market emitting T, a regulator with deadlines.
- **The founder can watch a lab run live.** herdr shows remote machines in the same window, so a lab VM is just another machine in the sidebar.
- **The interface is RL-shaped**, so the same environment serves testing, the framework's continuous integration and goal 2:
  - `reset(scenario, seed)` builds the machine and the organization;
  - `step()` advances the script of pressures and lets the agents work;
  - `observe()` returns the event store and the organization model;
  - `score()` runs the checks and projections into the success observations.
  - Every run is reproducible from the scenario, seed and versions (framework, harness, model).
- **First milestone: a hook testbed.** It brings up one machine and starts a scripted Claude Code session. It verifies that every hook kind writes the expected event and that the raw tier captures the transcript, then tears the machine down. This tests `of_platform`'s collector on a machine where nothing can be lost.
- **Open:**
  - Where the machines run: a user organization's server, a cloud VM, or the remote runners. This is the founder's call.
  - How agents in the lab authenticate: a separate account or API key with its own budget.

## Who builds it

The work set is large and separate: scenarios, the owner persona, mock systems, the runner and the scoring. It touches the design only through the schemas and the method. By the sizing rule in [`method.md`](method.md), it is a position of its own: **`of_lab`**, the keeper of the Lab. It works with `of_platform` (checks and projections) and `of_architect` (method and schemas).

## First step (proposal)

Level 1 with three scenarios:
- S0 solo founder;
- S1 Organic Framework;
- S2 like the first user organization.

The runner is one agent session per scenario. It applies the onboarding path v1 and the six-case business case, with an owner-persona subagent. The output is an organization repository per run, scored by `of check`. The first runs will show which concepts still cannot be computed. That list becomes `of_platform`'s backlog.
