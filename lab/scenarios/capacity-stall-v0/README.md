# capacity-stall-v0

Can the balancer leave a session stopped on a usage limit while another account accepts work? The invariant, the
cause and the design are in [`docs/capacity.md`](../../../docs/capacity.md), "The invariant" (hand-over 2759a172 from
of_conductor; owner of the code: `of_capacity`; lab owner: `of_lab`).

Pure logic, no VM: `run.py` loads `bin/of_capacity.py` from a git revision (default: the working tree), plans each
case with it (`decide` when the revision has it, else its `balance_plan`, as its balancer ran), and checks the
invariant with its own oracle, from each case's ground truth of which accounts accept work:
- every holder stopped on a limit, between turns, on an account that does not accept work, is moved;
- every move goes to an account that accepts work;
- no mid-turn session moves.

| Case | What it is | Kind |
|---|---|---|
| `real-2026-11-10` | the probe rows of 20:10:20Z, the five mail@acme.example sessions with their rates and contexts as the balancer read them, the two stops of 20:08Z; the other accounts' sessions anonymous with their real counts and total rates | the incident |
| `probe-lag` | the source's probe, 24 minutes old, says 92% and allowed; its session stopped 5 minutes ago | class |
| `long-reopen-short-room` | the source reopens in 4.5 hours; both targets accept work but their predicted week room over 4.5 hours is negative | class |
| `uncalibrated-target` | the only account that accepts work has no calibration and no known plan | class |
| `hold-production-stalled` | a production (`hold`) position stopped behind a stale probe that still says allowed; only the rescue moves it, and `hold` is never spared from the rescue (of_lab, 2026-11-11) | class |
| `cheap-stalled` | a stopped session spending below `min_rate` must still be moved — a balancing threshold never gates the rescue (of_lab, 2026-11-11) | guard |
| `nothing-accepts` | every account is stopped: no move may go anywhere | negative |
| `reset-and-busy` | a stop older than a probe that says allowed (it goes on where it is), and a mid-turn session | negative |

```
python3 lab/scenarios/capacity-stall-v0/run.py --rev 3b09797   # before 62d6e1b: 3 of 8
python3 lab/scenarios/capacity-stall-v0/run.py --rev 62d6e1b   # the two parameters: 3 of 8
python3 lab/scenarios/capacity-stall-v0/run.py                 # the rescue: 8 of 8
```

Exit 1 on any failure; `--json` for the moves of each case.
