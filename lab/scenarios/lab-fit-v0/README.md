# lab-fit-v0: the mandate-fit judge

Scores `bin/of_fit.py`: does the work a position hands out belong to the position that gets it? It answers
RFC 0001 §5.7 (drift and wrong-task rate). Requested by the founder on 2026-11-10, after acme_throughput-owner gave
storefront_pricing the price of 1 ms of pending-race lead. Pending speed is storefront_search's mandate.

## Sources (scored apart, as in lab-drift-v0)

- **Synthetic**: `roster.json` is a fictional organization with eight positions. `cases.json` holds 48 messages
  from its manager, 12 per verdict (`in_mandate`, `other_position` with its owner, `no_position`, `not_work`).
  `generate.py` wrote them with Sonnet once; they are committed so that a score can be reproduced. They are
  VM-free and hold no organization's data.
- **Real**: `real_cases.json` names messages in the tenant store by event id, each with the label a person gave
  and the source of that label. It reads the real store and runs on the Mac only (docs/lab.md, Level 2), and it
  prints only scores.

## Running it

    python3 lab/scenarios/lab-fit-v0/run.py synthetic|real|both [--jobs 6] [--out verdicts.jsonl]

Each case is a real model call (Haiku, about $0.001 to $0.002), so a run is a paid check, not a unit test. The
unit tests of the judge's logic are in `tests/test_fit.py`.

A flag is `other_position` or `no_position` on a message that hands out work, with confidence of at least 0.6.
The scores are the flag's precision and recall, and the owner accuracy over flagged `other_position` cases.

## Results

| Version | Source | n | Precision | Recall | Owner | Cost |
|---|---|---|---|---|---|---|
| mf-v0 | synthetic | 48 | 1.00 | 0.54 | 0.75 | $0.030 |
| mf-v1 | synthetic | 48 | 1.00 | 0.88 | 0.80 | $0.031 |
| mf-v1 | real | 1 | 1.00 | 1.00 | 1.00 | $0.001 |

Routing by model (the mf-v1 prompt on the synthetic set; routing is the share of the 24 work cases with an owner
where the right doer was named):

| Model | Precision | Recall | Routing | Cost |
|---|---|---|---|---|
| haiku | 1.00 | 0.83 | 0.88 | $0.018 |
| sonnet | 1.00 | 0.79 | 0.83 | $0.453 |
| claude-opus-5-5 | 1.00 | 0.88 | 0.83 | $1.068 |
| claude-fable-5-2 | 1.00 | 0.83 | 0.88 | $1.769 |

On the real case, Fable 5.2 named storefront_checkout with confidence 0.55, below the flag; that mandate also lists
mempool-replay calibration, so the two mandates overlap. A larger model does not route better on this set. The
miss on 2026-11-10 was a missing step, not a weak model.

mf-v0 read work that only touched a position's data as that position's mandate: a tax export from the ledger, or
a hire for infra. mf-v1 judges the purpose of the work instead. Its three misses are arguable labels: deploying a
change, drafting a spread model for a new venue, and measuring gaps in market data.

### Real labels (acme_org, 2026-11-10)

acme_org labelled the 40 messages mf-v1 flagged in acme_throughput-owner's dispatches since 2026-11-07: 5 yes,
33 no, 2 unsure. They are in `real_cases.json` with the founder's own case, 38 in all with 5 positives. The
negatives are hard ones, since mf-v1 flagged all of them. acme_org named three faults:
(a) mandates read as of today, not as of the message;
(b) steps on the receiver's own PR, such as queueing it after a yes;
(c) replies that name another doer.

| Version | Change | Precision | Recall | Cost |
|---|---|---|---|---|
| mf-v1 | (the flags as labelled) | 0.13 | 0.80 | $0.024 |
| mf-v2 | registry as of the message (its git history); own-deliverable steps and relays are no new work | 0.27 | 0.80 | $0.052 |
| mf-v3 | the judge is told when the message replies to the receiver | 0.36 | 0.80 | $0.055 |
| mf-v4 | plus the thread, the PRs named (branch, author) and the receiver's last turn | 0.20 | 0.20 | $0.021 |
| mf-v5 | without the receiver's last turn | 0.44 | 0.80 | $0.053 |
| mf-v5 on sonnet | | 0.60 | 0.60 | $0.879 |
| mf-v5 on claude-fable-5-2 | flags almost nothing | 0.00 | 0.00 | $3.543 |

The receiver's last turn is circular evidence: a misrouted task is exactly what the receiver is doing, so the
judge read it as the receiver's own work. With 5 positives these numbers move by 0.2 from one run to the next.
The judge is not precise enough to alert anyone yet. More labels come first.

`of fit replay` gave the baseline on acme_throughput-owner's 326 dispatches since 2026-11-07. The existing
detectors found nothing there: the mandate router read only paths (`unscoped`), and the diagnose rules raised
only `ap.chatty`.

## Next

- More real labels: the founder or acme_org confirms or rejects each message the replay flags.
- The listener's processor, which records `of.mandate_fit` and sends one note per case to the structure keeper.
  It waits until real precision holds on more than one label.
