# From documents to computation

Status: **Proposal**, 2026-11-05, by `of_architect`.

The founder asked how all of this fits Organic Framework's architecture: the business case, the onboarding path, the method. How does it become computable? How do we know that writing Markdown will actually be used, computed and understood?

What the organization does is **ontological computation**: thinking, and creating working documents. Systems then follow the execution of those documents.

## The answer in one line

**A document becomes part of the system when it carries claims that a check can test and that events can confirm or refute.** Prose explains; typed claims compute.

## Three layers

| Layer | What it is | Where it lives | Example |
|---|---|---|---|
| **1. Framework model** | Concepts and their schemas: what a position, mandate, stakeholder, obligation, business-case area or onboarding run *is*, which fields it has and which rules hold | the framework repository: `docs/*.md` for meaning; schemas for shape | [`registry.md`](registry.md), [`events.md`](events.md) |
| **2. Organization model** | Instances: this organization's positions, systems, stakeholders, obligations, business case, onboarding runs. Each document is Markdown with a **typed header**; the body holds the reasoning | each organization's repository | `registry/positions.yaml`, `business-case.md`, `onboarding/organization-run.md` |
| **3. Event log** | What actually happens: prompts, asks, verdicts, messages, state changes, mandate changes, releases | each machine, then the sync system | `~/.local/state/of/events.jsonl` |

Computation runs **between** the layers. It checks layer 2 against layer 1, and it compares layer 2 with what layer 3 shows really happened.

## Four kinds of computation

1. **Checks: the model against the framework.** Deterministic rules over the organization model as a graph. For example:
   - every active position has a brief;
   - every system has a keeper;
   - every repository has an owner;
   - every interest of a confirmed stakeholder has a covering position;
   - every obligation has an owner and a next date;
   - every assumption has an invalidation signal.

   `of check` runs them; `of registry check` is the first. The rules come from the framework's documents, in the same way that every FPF pattern ends with a conformance checklist (`CC-…`).
2. **Projections: the event log into state.** States, the ask queue, onboarding levels, founder-load metrics and the desk are computed from events. They are never typed in by hand.
3. **Watchers: the model against reality.** A claim in the model has a signal in the events. For example:
   - an assumption's invalidation signal fires;
   - an obligation's date approaches without a filing event;
   - a mandate shows no done event by its date;
   - a position's work drifts from its brief.

   A watcher writes an event addressed to the claim's owner position. This is how "systems follow the execution of documents".
4. **Agent judgements: where rules cannot reach.** Some claims cannot be checked by a rule, for example "the brief still matches what the agent does" or "this stakeholder's interest is covered in substance". There an agent evaluates against stated criteria and records a verdict event, with its classifier or method version, `heuristic: true` and evidence references. The owner can overrule. A judgement is never silent.

## What a computable document looks like

Every organization-model document type has:
- a **schema** (the typed header);
- an **owner position**;
- a **maturity** (unknown → sketched → evidenced → kept current);
- **evidence references** (files, events, sources);
- a **review date**;
- **checks** (deterministic and agent);
- the **events** it emits or is watched by.

An example for one area of a business case:

```yaml
---
type: business-case.area
case: financial
area: cash-by-date
org: acme
owner: acme_finance           # the keeping position (a gap until it exists)
maturity: sketched
evidence: [reports/Founder load through throughput accounting 2026-11-05.md]
review: 2026-12-01
claims:
  - id: runway
    statement: "Cash covers operating expense for at least 6 months"
    value: unknown
    signal: { event: of.finance.cash_snapshot, rule: "months_of_oe < 6" }
---
Reasoning, sources and open questions in prose.
```

`of check` can see that the owner position does not exist yet, that the claim has no value, and when the review is due. A watcher fires when a cash snapshot contradicts the claim.

## The rule that keeps writing honest

Every framework concept should reach five things:
1. definition;
2. schema;
3. check;
4. projection or watcher;
5. a method for agents (a skill or brief template).

A conformance table in the framework lists each concept and which of the five it has. **A concept with no check and no consumer is dead prose**, and the table shows it. This answers the question "will this writing be used?" for every document.

Today:

| Concept | Definition | Schema | Check | Projection or watcher | Method for agents |
|---|---|---|---|---|---|
| Position (registry) | yes | yes | `of registry check` | `of who`, desk | briefs |
| Event | yes | yes (`events.md`) | — | metrics, asks, states | — |
| Onboarding run and level | yes | no | planned (`of onboard check`) | planned (`of onboard status`) | a structure keeper's run, by hand |
| Business case (six cases) | yes | no | no | no | no |
| Stakeholder and obligation | partly | no | no | no | no |
| Systems map | yes | partly (`systems:` in a user organization's registry) | no | no | no |

## How it fits the architecture

The framework parts in [`framework.md`](framework.md) map to the layers:
- **Model and format** holds the schemas;
- **Method** holds the agent methods;
- **Platform** holds the checks, projections and watchers (the `of` CLI now, a daemon later) and the event log and sync.

The organization model is a set of typed documents in the organization's repository. The platform indexes them into a queryable graph (SQLite first). Agents read and write them; the owner reads the maps.

## Proposed next step

Make one document type fully computable end to end, as the pattern for the rest: **the onboarding run**.
1. A schema for `organization-run.md` and per-subject levels.
2. `of onboard check` and `of onboard status` (`of_platform`).
3. The first user organization's structure keeper converts its run.
4. Then the business case areas (the maturity map) and the obligations calendar (the first watcher with deadlines).
