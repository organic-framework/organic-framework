# {Org} role design

Status: **draft**, by `{org}_onboarding` with `{org}_org`. Used at step 7 to close the gaps the decision batch kept.

Sources: the record (organic-framework `docs/onboarding-practice.md`) §8.2 (8) and §8.3 (1, 2); ISO 37301:2021
§4.5 (an obligations register per stakeholder, each with an owner); Kim et al. 2025 (arXiv:2512.08296: parallel
watchers under one coordinator); `docs/rollout.md` (waves sized by capacity). Every new role names its source,
its outside party, its channel and its access; a role with no source is not proposed.

## 1. Rules

1. **An owner per gap.** Each gap the head kept in the decision batch gets one owning position, an existing one
   whose mandate grows or a new one. Run `of delegate "<the gap>"` first: it names the positions whose mandates
   already cover it.
2. **One watcher per stakeholder that sets its own requirements** (a tax authority, a licensing body, an AML or
   data-protection regulator, another jurisdiction). Each watcher keeps that stakeholder's obligations register
   (ISO 37301 §4.5): each obligation with its source, its date, its consequence and its owner.
3. **One coordinating owner** over the watchers. It is the unit's batching point: it files the head's cards as one
   batch, and the watchers send it their questions.
4. **Each role tied to its outside party.** An agent does the work with the outside party, with read-only access
   first and per-party permission to write. Signing, moving money, identity checks and strategy choices stay with
   people.
5. **Channels open with the roles.** For each outside party: who writes, through which channel, and the head's
   login task, filed when the role is created, not after (§8.3 (1)).
6. **Waves.** New positions start in waves sized by the accounts' capacity (`of capacity status`); between waves,
   wait until the guard measure could show a regression, never for a calendar day (`docs/rollout.md`).
7. **Lines of control.** A role that watches a risk sits apart from the role that takes it (IIA Three Lines Model;
   §8.1).

## 2. The roles

| Gap (from the decision batch, item) | Role | Position | Kind | Unit · batching point | Source | Outside party | Channel | Access (read → write) | The head's login task | Measure |
|---|---|---|---|---|---|---|---|---|---|---|
| _(item n)_ | | `<system>_<role>` | | | | | | | | |

## 3. Waves

| Wave | Positions | Capacity check (`of capacity status`) | Guard measure to wait on before the next |
|---|---|---|---|
| 1 | | | |

## 4. The head's open items

The new roles must not flood the head's queue. The flow analyst counts the head's open items from the onboarding
per day (§8.3 (2)); a rise after a wave is a finding for the wave's batching point.
