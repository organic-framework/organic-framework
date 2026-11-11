# Rollout pacing: safe steps, and a pause sized by the measure, not the calendar

Status: **accepted** by `of_architect`, 2026-11-10. Hand-over `a515e37a` from `of_conductor`.

**The case** (the founder, 2026-11-10 in `of_conductor`'s pane). A cache of base images for CI was ready, and its rollout to the remaining runners was put off until the next day. The users are the organization's own people, and they would gain from it at once. "Everything is ready, and we wait for tomorrow to roll it out." He asked for an algorithm with two properties:
- it keeps time between steps, to see whether a regression appears;
- it never waits a day to roll out a simple improvement.

## 1. The rule in one line

A rollout moves in steps of growing blast radius. After each step it waits **until the change's guard measure could show a regression**, then goes on at once. A calendar day is never a reason to wait. A human yes is needed only when the change's risk class asks for one, and that yes covers the whole plan, not each step.

## 2. Inputs

Every rollout plan states these inputs before its first step. The plan is a line in the system's calendar or in its work's stages.

| Input | Values | Why it matters |
|---|---|---|
| **Rollback** | exercised (run at least once on this system, the same way) · written only · none (irreversible) | A written rollback that was never run is not a rollback (a user organization's platform unit, 2026-11-07: a kube-proxy window cut its single sign-on for 14 minutes). |
| **Blast radius** of the next step | one unit (a runner, a node, a host, a site, a share of traffic) · a pool · everything | It sets how many steps the rollout needs. |
| **Money path** | yes or no: funds, trading, payments, accounting rows | Money changes need the owner of the money unit. |
| **Data or state change** | none · additive and reversible · migration or deletion | Migrations and deletions are usually irreversible. |
| **Guard measure** | the metric that would show the regression, with its threshold and its minimum sample | It sets the pause (§4). |
| **Who must say yes** | none (standard) · the unit owner · the organization's owner | This comes from the unit's envelope (docs/registry.md, Delegations). |

## 3. Risk classes

| Class | When | Yes needed | Source practice |
|---|---|---|---|
| **Standard** | The rollback is exercised, each step's blast radius is limited, there is no money path, and the state change is none or additive. Examples: a CI cache, a runner image, an internal tool, a dashboard, an alert. | **None.** The plan is pre-authorized by the unit's envelope. The keeper runs it and reports. | ITIL 4 change enablement: a standard change is low-risk, well understood and pre-authorized (Axelos, *ITIL 4: Change Enablement*, 2019). External approval of such changes adds delay and no stability (Forsgren, Humble and Kim, *Accelerate*, IT Revolution 2018, ch. 7: change approval boards). |
| **Normal** | A money path, a shared control plane (Kubernetes, network, databases on the money path), or a rollback that is written but not exercised. | The unit owner, or the owner where the envelope keeps it, for the **whole plan** at once. | ITIL 4: a normal change is authorized by its change authority. *Site Reliability Engineering* (O'Reilly 2016), ch. 27, "Reliable Product Launches at Scale": gradual and staged rollouts. |
| **Irreversible** | Deletion, a migration with no way down, key rotation, moving funds. | The organization's owner, as an ask, with the evidence from a rehearsal in the lab or on a copy first. | ITIL 4; RFC 0002 §4.1 and the guard for keys; the envelope's own rules. |

A step that would move the rollout into a higher class stops for that class's yes. Examples: a step that reaches the money path, or one that finds the rollback does not work.

## 4. The pause after a step

The pause is the time **the guard measure needs to be able to show a regression of the size that matters**. It is never "until tomorrow" and never "let it soak" without a measure.

- **Choose the guard measure** before the step. It should react to what can go wrong:
  - a CI cache: job failures, pull errors and job duration on the runners that changed;
  - a bot release: sends, errors, latency per trigger and revenue per race;
  - a database change: error rate, latency and replication lag.
- **The pause ends at the first of these:**
  1. **The sample is enough.** The changed units have produced the minimum sample for the measure, for example 5 CI jobs through the new cache, or N races, or one full cycle of a periodic job. With no regression past the threshold, the next step starts **at once**.
  2. **The measure regressed.** Roll back now with the exercised rollback, and stop the plan.
  3. **A known slow effect.** Some regressions show only later: memory growth, a nightly job, a weekly cron, a surge. The pause then lasts until that effect could show once, for example one run of the nightly job. It is sized by that effect and named in the plan.
- **Waiting for traffic is allowed; waiting for a date is not.** If the sample cannot accumulate because nothing passes through the changed units (quiet hours, no jobs), the plan says "until N events", and the next step starts when they arrive.
- **The next step may follow at once, with no pause:**
  - when the step is purely additive and nothing consumes it yet, for example a new image in the cache that no job uses;
  - when the same change already passed the same measure on an identical unit in this rollout, and the next step's units are of the same kind and load. A runner pool is the typical case: after the first runner passes, the rest follow in one step.

Source practice:
- Canary length is set by the time the canary metrics need to tell a good release from a bad one (Beyer et al., *The Site Reliability Workbook*, O'Reilly 2018, ch. 16, "Canarying Releases").
- Automated canary analysis compares canary metrics with a baseline statistically (Netflix Technology Blog, "Automated Canary Analysis at Netflix with Kayenta", 2018).
- Rollout controllers express a plan as steps of weight, pause and analysis (Argo Rollouts, canary strategy).

## 5. When people must be present

- **A rollback that needs a person** needs that person during the step and its pause. That is the only time-of-day constraint. With an automated, exercised rollback there is none.
- **The owner's quiet hours** limit only what needs his yes during a step. A standard change never does.
- **A freeze beside a money-path window.** A step may not run on a shared system while a money-path window is open on it, or while a surge is on. This is the only legitimate reason to give a change a later slot. Source: ITIL 4's change schedule and blackout periods.
- **Reviews have no working day.** A user organization's root cause, 2026-11-10: a single unit reviewer said "I review it tomorrow morning". When the reviewer is an agent, it answers a review request once it is idle. A review that waits for a person states which person and why. Keepers review in parallel, not in turn. Source: Google's engineering practices, "Speed of Code Reviews": a reviewer who is not in the middle of a focused task reviews shortly after the request arrives.
- **A standard change needs no calendar slot.** Calendars hold windows that need a yes, a person or a freeze, and standard changes run when they are ready.

## 6. The algorithm

```
plan(change):
  inputs  = rollback, blast radius per step, money path, state change, guard measure (threshold, minimum sample, slow effects)
  class   = standard | normal | irreversible           # §3
  yes     = none | unit owner | owner                  # from the class and the envelope; one yes for the whole plan
  steps   = smallest unit -> same-kind remainder -> other kinds -> all   # fewer steps when units are identical
  record the plan in the system's calendar or the work's stages

for step in steps:
  if step raises the class: stop; ask that class's yes
  if the rollback needs a person: that person is present
  apply step
  wait until: sample >= minimum (or the named slow effect has run once) OR measure regressed
  if regressed: roll back; stop; report
  record the step's start, measure, sample and result
  continue at once
report: done, with each step's measure
```

## 7. Anti-patterns

- **Calendar deferral.** "Tomorrow" for a ready standard change. The fix is the pause rule of §4.
- **A soak without a measure.** A pause that names no metric, threshold or sample. Nobody can say when it ends or what it showed.
- **One yes per step.** It brings the owner the same decision again and again. One yes covers the plan; only a raise in class asks again.
- **Batching ready changes into one window.** It makes the blast radius larger and the cause of a regression harder to find. Small batches deploy more often with fewer failures (*Accelerate*, ch. 2 and 5).
- **Treating every change as normal.** It sends standard changes to people, and the delay buys no stability (*Accelerate*, ch. 7).

## 8. Who applies it

- Each organization's keepers apply it in their rollout plans. Unit owners apply it when they say yes to a plan.
- Each unit's envelope names which of its changes are standard (docs/registry.md, Delegations).
- `of_field-analyst` may add calendar deferral and soaks without a measure to the anti-pattern catalogue (docs/diagnose.md), from the plans and calendars it reads.
