# RFC 0007: the system architect, a systems map on evidence, and ownership cells

| | |
|---|---|
| Status | **Accepted** by `of_architect` (2026-11-10). Framework design: no money and nothing irreversible. A new position in an organization still goes through that organization's diff and the founder's yes. |
| Source | `of_platform`'s note from a session with the founder (2026-11-10), kept in OF's state directory (`fit/proposal-structure-2026-11-10.md`), with its first coverage draft of the first user organization. The founder asked for each step. |

## 1. What went wrong

- **A task went to the wrong role.**
  - `acme_throughput-owner` handed `storefront_pricing` "what is 1 ms of our lead worth on pending races". That is `storefront_search`'s work, and the founder caught it by hand.
  - `of fit`, the judge of whether handed-out work belongs to its receiver, reaches only 0.44 precision on the real cases. Mandates are one line long, and stronger models did no better.
- **The storefront has 7 or 8 owners per kind of work.** They separate only by subsystem, and no field names the subsystems. The "1 ms" task fell on the seam between three of them.
- **The systems map is wrong and opaque.**
  - The founder cannot tell from the names what `delivery`, `bot-core`, `research` or `trading` are.
  - At least one fact is false: "a colleague keeps the marketplace bots" was written as "(founder, 2026-11-06)" with no ask id, and someone else keeps them.
  - The `confirm` fields were never put to him.

**Root cause, in the framework.** Defining systems needs three kinds of knowledge: what runs in production and where, which code is alive and what ships from it, and where the money comes from. Onboarding step 3 gives the systems map to the structure keeper ([`onboarding.md`](../onboarding.md)), which holds none of the three.

FPF separates the architect of the creating system (the organization) from the architect of the target system. OF had only the first.

## 2. Decisions

### 2.1 Two architects

- **The system architect** is a role in every organization. It owns the target system's breakdown:
  - the supersystem, its systems and subsystems, and the relations of [`systems.md`](../systems.md);
  - the interfaces between them;
  - the open grain questions;
  - the evidence on each system (§2.2);
  - which kinds of work each system needs (§2.3).
- **The structure keeper** keeps the organization: positions, units, delegations and envelopes. It maps positions onto the architect's cells, and asks the architect when a system is unclear.
- **Onboarding step 3 changes owner** to the system architect, and step 6's decision batch includes its unconfirmed facts.
- **One position may hold both roles** in a small organization. The OF team does this: `of_architect` keeps the team and the framework's map. The evidence rules apply either way.
- **For the first user organization**, its structure keeper proposes the position by a spawn diff, with the founder's yes. It needs read access to:
  - runtime, from the platform keepers;
  - code activity, from Studio;
  - money, from `accounting_keeper`.

### 2.2 A systems map on evidence

Each live system carries four kinds of evidence:

| Evidence | Field | Source |
|---|---|---|
| Runtime | `runs:` — where it runs (cluster and namespace, hosts, or `nowhere`), with `evidence` as on `depends_on` | the platform keepers |
| Code | none: derived | Studio, from `of.work_record` and `of.pr_state`: activity per system over 30 days, and the writers |
| Money | `money:` — a P&L line, a cost line for an enabling system, or the goal it serves | the money owner (for example `accounting_keeper`) |
| Governance | `confirmed:` — a map from each fact to the owner's ask id: `keeper`, `governed_by`, `lifecycle`, `boundary` | the owner, through one batched ask |

The rules for governance facts:
- **A retold date in a comment confirms nothing.** A comment such as "(founder, 2026-11-06)" counts as unconfirmed; only an ask id does.
- **`of registry check` warns** on a live system whose `keeper`, a non-`self` `governed_by` or a non-default `lifecycle` has no ask id under `confirmed:`.
  - It warns rather than fails, so today's registries keep passing.
  - Studio marks each such fact "unconfirmed".
- **The facts go to the owner in one numbered `of ask`.** The system architect files it, through the structure keeper.
- The existing `confirm:` list stays the open-questions list. A confirmed fact moves from `confirm:` to `confirmed:`.
- **The name is for the owner.** Each system's `name` says in plain words what it is and what it does for the business, so the owner can tell it without the id. The owner's understanding is part of the test (§2.4).

### 2.3 Ownership cells

A **cell** is a system or subsystem crossed with a kind of work.

**The vocabulary.** The framework's default kinds of work, from `of_platform`'s draft:

| Kind | What it covers |
|---|---|
| `research` | opportunities, losses, hypotheses and studies |
| `model` | quantitative models: pricing, bidding, prediction, the money value of an effect |
| `build` | code and design |
| `verify` | backtests, tests, validation before a change runs |
| `operate` | releases, configuration, deploys, on-call, incidents |
| `measure` | timing, metrics, monitoring |
| `account` | ledger, P&L, costs, attribution |
| `secure` | security, secrets, risk limits, compliance |
| `data` | capture, storage and serving |

- An organization may add kinds under a top-level `methods:` map, each with a one-line definition.
- These kinds are not the management functions (`mandate_functions`): those are organization-wide, and cells are per system.

**What each system needs.** The system architect writes it on the system: `needs: [build, verify, operate, …]`. A model may draft it, and the architect decides. A cell no system needs is never a gap.

**Who owns a cell.** A position's entry lists its cells. Several positions may be doers on a cell, but at most one decides.

```yaml
owns:
  - {system: storefront.search, method: build, decides: true}
  - {system: storefront.search, method: verify}
```

**Subsystems are systems** with `parent` set and their own `paths`, as the map already allows. the storefront's are named first, because its owners separate only by subsystem.

**Checks and uses:**
- `of registry check` warns on two deciders on one cell, and on an `owns` entry naming an unknown system or kind of work.
- Studio shows the needed cells of live systems that have no owner, and the cells with three or more doers.
- `ap.function-gap` grows into cell-gap and cell-overlap issues. They go to the structure keeper, never straight to the owner.
- `of fit` and `of delegate` classify a task into a cell and take its owner from the table, instead of reading one-line mandates.

### 2.4 A test of the breakdown: lab-systems-v0

- **Variants to compare.** Breakdowns are built from different inputs, prompts and models:
  - inputs: paths, co-change history, runtime, money, the owner's description;
  - prompts: code-first, value streams, FPF;
  - models: Sonnet, Opus 5.5, Fable 5.2.

  The baseline is clustering of the co-change DSM, as RFC 0001 planned.
- **Scores:**
  - understandability: the owner's labels and a blind name test, in one sitting of his;
  - the share of work inside one system;
  - money attribution;
  - one keeper per system;
  - consistent levels;
  - stability across variants.
- **What it is for.** It is a tool for the system architect, not a replacement for it. The owner's confirmed map is the reference.
- **It runs after the facts are confirmed** (§2.2), since the reference needs them.

## 3. Who builds what

| # | What | Owner |
|---|---|---|
| 1 | The schema: `runs`, `money`, `confirmed`, `needs`, `owns`, `methods`, in [`registry.md`](../registry.md); the role in [`onboarding.md`](../onboarding.md) step 3 | `of_architect` |
| 2 | The checks: unconfirmed governance facts, two deciders on one cell, unknown systems and kinds in `owns` | `of_platform` |
| 3 | The first user organization's system architect position, by a diff with the founder's yes; then its map on evidence, its main bot's subsystems first, and one batched ask of the unconfirmed facts | its structure keeper, then the new position |
| 4 | Studio: each system's evidence (runtime, code activity, money, confirmations), and the cells view with gaps and overlaps; the per-system code activity and co-change data | `of_studio` |
| 5 | Cell-gap and cell-overlap issues, grown from `ap.function-gap` | `of_field-analyst` |
| 6 | `of fit` and `of delegate` on cells | `of_platform` |
| 7 | lab-systems-v0, on `of_studio`'s data | `of_lab` |

- **Critical path:** 1, then 3; then 2 and 4 in parallel, then 5 and 6. Step 7 comes after the first user organization's confirmed facts.
- **Mandates.** Its mandates move into `owns` as positions are next rewritten, the main bot's first. Nobody rewrites them all at once.

## 4. Limits

- **The vocabulary is a first cut.** `data` and `measure` overlap at the edges. An organization's own kinds of work, and the founder's practice packages for Bench, will show what is missing.
- **Cells do not replace judgment.** A task can span cells. The judge then names the cell that decides, and the others are doers.
