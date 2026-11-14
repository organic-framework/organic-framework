# RFC 0005: development loops

| | |
|---|---|
| Status | **Draft**, 2026-11-07 |
| Author | `of_architect`, from the founder's request through the first user organization's structure keeper (2026-11-07, about 14:30Z) |
| Decides | the founder |
| Depends on | [`works.md`](../works.md) (the work object), [`registry.md`](../registry.md) (goals, works), [`studio.md`](../studio.md), [`diagnose.md`](../diagnose.md), accountability acc-v0 (`reports/accountability-v0 2026-11-07.md`) |

## 1. Summary

Much of a company's work is a **development loop**: change something, evaluate it, keep or drop it, repeat, until it earns money, or until it moves a measure its owner chose (§4.2, 2026-11-14). OF already follows works, but not their loops, so a loop can run for days without anyone seeing:
- where it stands;
- what it costs;
- whether it still aims at money, or at the measure its owner chose.

This RFC makes the loop a first-class part of a work. It carries:
- a target priced in money, or a measure its owner decided, with money reported beside it (§4.2);
- a chain of measures that maps each evaluation to that target;
- an **evaluation ladder** whose rungs have a cost, a latency and an acceptance rule;
- a **hypothesis register** that chooses the next experiment by information per cost;
- a **decision log**.

OF derives the loop's metrics from events. Studio shows one loop card per work, and the pulse nudges on a plateau, on drift and on a missing production checkpoint.

## 2. Evidence: the first user organization's search work, 2026-11-02..07

From its structure keeper's analysis (a private report; work `acme-search-ladder`):
- **The gain came from a step back, not from tuning.** The day pass rose from 211.7 to 325.8 units of the bot's revenue (+25%) in about six hours, after the founder reset the frame. Three days of tuning that followed added nothing measurable.
- **The objective drifted silently** to milliseconds, with no price per millisecond in money.
- **The loop's constraint was access to evaluation.** 76 of 92 incoming messages were about runner slots, and 58 of 118 hours were gaps. The cheap offline harness, checking a change in minutes, came on day four.
- **The most expensive model ran everything,** runner babysitting included: $969 in the week, the highest of any position.
- **Nothing reached production in five days:** no shadow, no canary, no flag flip.
- **The founder could not see the loop.** Six of his roughly 30 prompts were "how is it going".
- **The measurement discipline itself was strong:** paired arms, oracle equivalence, every number tied to an evidence section. The loop lacked structure around that discipline, not rigour inside it.

## 3. Theory

| Source | What we take |
|---|---|
| PDSA (Deming) | Every cycle is a hypothesis with a predicted effect, a test, a study of the result and a decision |
| Product development flow (Reinertsen) | Cost of delay; small batches and fast feedback; WIP limits; choose the next experiment by the value of information per unit of cost |
| Theory of constraints (Goldratt), applied to the loop itself | Find the loop's bottleneck (usually evaluation), exploit it with cheap rungs first, subordinate the schedule to it, then elevate it |
| Double-loop learning (Argyris) | Single loops tune inside the frame; a plateau calls for questioning the frame, with an outside review |
| Goodhart's law | Each rung's metric must be money or a proxy proven against money; otherwise the loop optimizes the proxy. The warning is against a proxy an agent chose: when the owner decides the measure, the owner carries that risk, and money is reported beside it (§4.2) |

## 4. The object: `loop:` on a work

A loop belongs to a work (`docs/works.md`). It lives in the work's entry in the organization's registry (structure keeper, single writer) or in a `loop.yaml` beside the work's doc, kept by the work's owner.

```yaml
works:
  acme-search-ladder:
    goal: g-throughput
    loop:
      target:
        metric: "day pass, revenue per day, replay day 1003"     # what success is measured in
        money: "bot revenue per day"                              # how it becomes money
        stake: "about 65 a day over the route table"         # what is at stake, roughly
        throughput_class: raise
        segment:                                                  # what the organization controls (2026-11-12; docs/registry.md, Works)
          start: "a recorded block of replay day 1003 reaches the search"
          end: "the search's chosen route leaves it"
          context: ["the live market on the day", "rivals' routes"]   # held fixed by the replay; reported, never a target or a lever
      measures:                                                   # the chain from each rung to money
        - name: latency_p50_ms
          to_money: "revenue per ms from the race arithmetic"        # required before a rung may optimize it
      ladder:                                                     # cheapest first
        - {rung: oracle, cost: "0", latency: "1 min", accept: "0 differing rows"}
        - {rung: offline, cost: "local CPU", latency: "5 min", accept: "no candidate lost on 81 blocks"}
        - {rung: gate, cost: "1 runner-h", latency: "40 min", accept: "det2 >= baseline"}
        - {rung: day_pass, cost: "6 runner-h", latency: "4 h", accept: "> baseline + 1%"}
        - {rung: shadow, cost: "1 site", latency: "1 day", accept: "no loss path; decisions logged"}
        - {rung: one_site, cost: "1 site", latency: "2 days", accept: "revenue >= control site"}
        - {rung: all_sites, cost: "prod_change", latency: "—", accept: "—"}
      checkpoint_days: 3          # a production rung (shadow or higher) at least this often
      wip_arms: 2                 # at most this many arms in flight
      babysit_model: sonnet       # the model for runner commands and monitors
```

### 4.1 The hypothesis register and the decision log

- **A hypothesis is recorded before its test:**

  ```
  of loop hypothesis <work> "<change>" --predict "<effect on the target>" --cost "<rung, runner-hours>" --voi <low|med|high>
  ```

  It is one `of.loop_hypothesis` event, with the status `open`, `testing`, `kept`, `dropped` or `parked`.
- **A run on a rung is recorded when it ends:**

  ```
  of loop run <work> --rung day_pass --hypothesis <id> --result "325.8" --evidence "RFC §S2aq"
  ```

  It is one `of.loop_run` event, with the cost from `of_capacity`'s spend and the runner's own record. Where the runner already writes events (cbx runs), `of loop` derives them instead of asking the doer.
- **A decision is one `of.loop_decision` event:** keep, drop, park, reopen, step up a rung, step back (reframe), or change the objective. `reopen` puts a parked, dropped or kept hypothesis back to `open`, with a note that names what changed or whose word reopened it (`of loop` and its register, `of_platform`, organic-framework PR 168; the loop card, `of_studio`, to build). Why: on 2026-11-14 a user organization's owner revived a parked hypothesis, and it was recorded as `keep` (loop decision `01a1d96b-3945`), which reads as a hypothesis that won and counts in the hit rate. A change of objective must name its money metric (§6), or, on a target the owner decided, the owner's yes on the new measure (§4.2).
- **The next experiment is chosen by value of information per cost.** The register shows the open hypotheses in that order, so the doer and the owner see why the next one is next. The cost is the rung's runner-hours, the evaluation's time; it is never money or tokens.

### 4.2 The owner's measure (2026-11-14)

The person who owns a work's goal may decide that the loop's target is itself what its changes are for (`target.decided`, the id of their yes; docs/registry.md, Works, "The owner's measure"). Then `money` is a reading, not a requirement; each measure chains to the target (`to_target`) or to money; `--voi` is optional and judged against the target; a change of objective names the owner's new yes instead of a chain to money; and no position asks the owner to price or prove that measure in money. The plateau question and every gate on an act (§6, decision rights) stay as they are. This came from a user organization whose owner chose a speed objective and accepted that the spend on it may bring no money gain.

## 5. Loop metrics: derived, never reported

As for works (`docs/works.md` §3), no agent writes "60%". From the events:

| Metric | Meaning |
|---|---|
| Cycle time per rung | From a hypothesis entering a rung to its result |
| Cycles a day | Runs per day, per rung |
| Hit rate | The share of hypotheses kept |
| Gain per cycle | Change in the target per kept cycle, in the target's units, and in money when the target is priced |
| Cost per cycle | Tokens (`of_capacity`, by session) and runner hours |
| Wait share | Time waiting for a rung (a runner slot, a review, a window) over elapsed time |
| Time to first money | From the loop's start to its first production rung |
| WIP | Arms and hypotheses in flight |
| Highest rung reached | And when |

## 6. Rules the platform watches

Each rule is a diagnose anti-pattern with an owner and a test, `of_field-analyst`'s catalogue. Each fires as an issue with its evidence, and its first remedy is a pulse nudge to the work's doer:

| Rule | Fires when | Nudge |
|---|---|---|
| **Cheapest rung first** | A run on a rung while a cheaper rung exists and was skipped for that hypothesis | "Run it on `offline` first: 5 min instead of 4 h" |
| **Production checkpoint** | No shadow or higher rung for `checkpoint_days` | "No production signal for 5 days: what goes to shadow next?" |
| **Plateau** | 3 cycles in a row gain under 1% of the target | A step back and an outside review. The owner is asked for a reframe review by a second position (double loop) |
| **WIP limit** | More than `wip_arms` arms in flight | "Finish or park one arm" |
| **Babysitting model** | Runner commands and monitors run on a model above `babysit_model` | To `of_capacity` and the doer: route babysitting to the cheaper model |
| **Objective drift** | A run's metric is not the target and has no `to_money` in `measures` (on a target the owner decided, no `to_target` either; §4.2); or a run's metric or a hypothesis's predicted effect is a context part of the target's segment (2026-11-12) | "Price a ms in revenue before more speed work"; on a target the owner decided, "Tie this metric to the target, or get the owner's yes on a new measure", never a price; for a context part, "The live market on the day is outside the segment: report it, and act inside" |
| **Evaluation bottleneck** | Wait share over 40% on one rung | To the owner: elevate the rung (more runners, a cheaper rung) |

Decision rights follow acc-v0:
- the doer moves between offline rungs;
- the unit owner approves shadow and one-site rungs, inside its envelope;
- all sites is a `prod_change` and goes to the founder unless delegated;
- the plateau review is the owner's call.

## 7. Studio: the loop card

One card per work with a loop. Its reader is the work's owner (for the search work: `acme_throughput-owner`) and the founder. It shows:
- **The target, the stake and the money metric**, or, on a target the owner decided, the id of that yes, with money as a reading (§4.2). A red mark appears when the current runs measure something else.
- **The ladder:** each rung with its cycle time, last result and wait share. The highest rung reached is highlighted, with the days since the last production checkpoint.
- **The register:** open hypotheses by value of information per cost, and the last kept and dropped ones.
- **The curve of the target per cycle,** with the plateau rule's window marked.
- **Cost this week:** tokens and runner hours, and cost per kept cycle.
- **The decision log.**

The card answers "how is it going" without a prompt.

## 8. Who builds what

| Step | What | Owner |
|---|---|---|
| 1 | Schema: `loop:` on a work, `loop.yaml`, and the checks | `of_architect` (schema), `of_platform` (check) |
| 2 | `of loop hypothesis/run/decision`, the events, and derivation from runner records (cbx) where they exist | `of_platform` |
| 3 | Loop metrics, and the loop card in Studio | `of_studio` |
| 4 | The seven rules as diagnose anti-patterns, with fixtures | `of_field-analyst`, with `of_lab` for the drift harness |
| 5 | Cost per cycle and babysitting-model routing | `of_capacity` |
| 6 | The first loop: `acme-search-ladder`, its ladder and target from the structure keeper's analysis; reader `acme_throughput-owner` | `acme_org` with the search work's doer |

## 9. Open questions

1. Where the loop lives: the registry (structure keeper, single writer) or a `loop.yaml` beside the work's doc (the work's owner). Proposal: the target and the ladder in the registry, since they are structure. The register and the log are events.
2. The plateau review: who is the "outside" reviewer by default? Proposal: the unit owner names a second position, and for the OF team it is `of_architect`.
3. Price of latency: a generic `to_money` helper per throughput class, or a per-work formula. Proposal: per work first.
