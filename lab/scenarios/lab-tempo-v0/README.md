# lab-tempo-v0: the booking judge

Scores `bin/of_tempo.py`: when a position books its own work for later, does it name what the work waits on? It
answers part 5 of hand-over 277de578 from `of_architect` (2026-11-17). The founder asked "Where does this dragging
out of time come from?" (prompt `01a1e9ab-a3ba`), after a day of pushing one organization's positions to start work
they had put into later slots.

## Sources (scored apart, as in lab-fit-v0)

- **Synthetic**: `cases.json` has 16 items of a fictional organization: crons, clock waits, messages up and asks.
  Eight are labelled `no_basis`: work put off with no named reason. A round deadline, "tomorrow" or a self-made slot
  in a plan count here. The other eight are labelled `passes`, meaning a basis or no booking at all. A basis here is
  an owner-booked window with its ask, a CI run, a lock held on the same host, data that lands later, a vendor's
  notice or a position that must act first. A recurring watch is no booking. These cases hold no organization's data.
- **Real**: `real_cases.json` names items of a past window in this machine's store by their key, each with its label
  and the label's source. It lives outside the public tree, under `lab-private/` (release README §2). The window
  is 12:00 to 14:00Z on 2026-11-17 at the user organization `of_conductor` wrote about (message `01a1e9ac-036e`).
  The cases flagged `no_basis` are the bookings it named; the founder moved each of them only after pushing. The
  passes are waits with a named basis: CI runs and a booked etcd window. `of_platform` labelled them from those
  sources; the founder did not label them himself. The run replays the window with `of_tempo.replay` and prints only
  scores.

## Running it

    python3 lab/scenarios/lab-tempo-v0/run.py synthetic|real|both

Each call is a real model call (Haiku), so a run is a paid check, not a unit test. The unit tests of the filter and
the judge's logic are in `tests/test_tempo.py`.

A flag is the verdict `no_basis`. The scores are the flag's precision and recall on the labelled items. The real run
also prints how many items the window held and how many were flagged in all.

## Results

| Version | Source | n | Precision | Recall | Cost |
|---|---|---|---|---|---|
| tb-v1 | synthetic | 16 | 0.86 | 0.75 | $0.005 |
| tb-v1 | real | 10 | 1.00 | 0.80 | $0.066 |
| tb-v2 | synthetic | 16 | 0.75 | 0.75 | $0.005 |
| tb-v2 | real | 30 | 0.75 | 1.00 | $0.071 |

The real window held 152 items. The judge flagged 58 of them; only 10 are labelled, so the precision over all 58 is
not known yet.

Misses on 2026-11-17:
- **Synthetic.** It read "Shall I rotate them tomorrow?" as waiting on the owner's answer. It read a finished
  report's deadline as no booking. It flagged "I start at 14:30Z if the rule is open by then", which waits on a
  position, because nothing named requires 14:30Z. That last flag is arguable.
- **Real.** It passed an alarm before a roll, because the roll waits on another position's decision at a fixed hour.

tb-v2 (2026-11-17) adds of_architect's rule (AGENTS.md and docs/method.md, "Work starts when it is ready"): a clock
time holds only when it is the basis's own time. It also names the basis's own times, a short lead before them and
the role's own cadence. The real cases grew to 30: 20 random flagged items were labelled from the rule's text, an
inference like the first 10. All five false flags of the real run are items where `of_platform` had read a short
lead before a booked slot, or a booked hour, as a basis. Read strictly, the rule's text does not say that. The
synthetic s11 and s16 wait on a lock or a position and then name a fixed hour, and were labelled before the rule;
the judge flags them by it.

tb-v3 (2026-11-17) adds of_architect's next rule line (AGENTS.md and docs/method.md; message `01a1ea20-1fa7`). A step before a booked slot runs as soon as it can, unless it names why it cannot run
earlier. A slot holds only when it has a source; a slot a position picks for itself is a booking. By that line the
five disputed real items are bookings, and they were relabelled `no_basis` after the tb-v2 verdicts had been seen.
Their labels say so. From tb-v3 the notes are live, and each note's outcome measures the precision: the position runs
the work, names a basis, or does neither.
