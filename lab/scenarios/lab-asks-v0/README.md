# lab-asks-v0: finding the founder's questions

Scores the two stages that find a question an agent leaves in its pane instead of filing it with `of ask`:
the detectors (`bin/of_classify.py`, q-v0 to q-v5), which pick closing messages as candidates, and the judge
(`bin/of_judge.py`, a small model), which decides whether the agent waits on its owner. A miss at either stage
loses the question. Kept by `of_asks`; started on 2026-11-10 after the founder's "There was a question and it did not reach
the panel" (ci_keeper; `reports/asks-losses-2026-11-07-10.md`).

## Cases

`real_cases.json` names closing messages in the tenant store by event id: an `of.agent_question` candidate, or an
`of.turn_end` that no detector picked. Each has a label (`owner_question` or `not_a_question`) and its source. The
file holds no text; `run.py` reads the store and the transcripts on the Mac (docs/lab.md, Level 2) and prints only
scores and event ids.

126 cases, 35 positive. 116 were drawn on 2026-11-10 (`run.py sample --since 2026-11-08 --n 60 --seed 1110`): every
candidate the judge called `owner_question` (41) or skipped (8) since 2026-11-08, 60 of its 465 `not_a_question`
at random, and 7 messages from the founder's reports (5 he named, 2 later notes repeating ci_keeper's questions). `asks-117` was added on 2026-11-11: of_capacity's
proposal that he reported lost (hand-over `cc2ed950`, `reports/asks-pane-questions-2026-11-11.md`).
`asks-118` to `asks-126` were added on 2026-11-17: pane cards for decisions another position already carried (j-v6
below). Positives are oversampled, so the scores
describe this set, not the daily stream; the results below were measured on the first 116.

| Source | Cases | What it is |
|---|---|---|
| founder | 6 | Questions he said were lost or answered from the pane (2026-11-07, 2026-11-10) |
| acme_org | 1 | Its report behind a953a7d: a reminder of its filed card, typed in as a new question |
| of_asks | 21 | An agent's reading where the recorded verdict and Opus disagreed (acme_org to check) |
| silver | 89 | The recorded verdict and Opus (the j-v3 prompt) agree; no one checked |
| a user organization's structure keeper | 6 | Pane cards for a decision its unit owner already carried to the founder, one of them its own (2026-11-17) |
| of_asks | 3 | Three more cards of the same keeper, unit owner and hour; not checked by the keeper |

Labelling rule: `owner_question` when the agent waits on the founder's answer to something not filed with
`of ask`, also when it only points at it ("a question about 1Password" in a WAITING ON line, a status note "awaiting
owner answers"); a reminder of an ask already filed, by any position, is `not_a_question`.

## Running it

    python3 lab/scenarios/lab-asks-v0/run.py detectors
    python3 lab/scenarios/lab-asks-v0/run.py judge [--model haiku] [--model-only] [--out F]
    python3 lab/scenarios/lab-asks-v0/run.py pipeline [--gate detectors|all] [--ids asks-117] [--runs 3]
    python3 lab/scenarios/lab-asks-v0/run.py cards [--since 2026-11-10T20:45:00]
    python3 lab/scenarios/lab-asks-v0/run.py sample | silver --ids F --model opus

`pipeline` asks whether the stages as built would put a case on the founder's list: the gate (a detector fires,
or every closing message), the judge's rules before the model, and the judge by a majority of `--runs` calls; it
exits 1 when a labelled question is lost. `cards` is the stop rule (of_architect, 2026-11-11): of the pane cards OF
filed for agents (`source: pane`, from 2026-11-10 20:45Z), the share that ended withdrawn or answered "not a question".
Above 1 in 5, the judge is tightened before judge.yaml `gate: all` opens the gate; it exits 1 then.

Run the paid commands with `HERDR_ENV`, `HERDR_PANE_ID` and `HERDR_SOCKET_PATH` unset (AGENTS.md: a nested
harness re-binds the pane). `judge` replays the judge's rules before the model with the queue as it stood at
each case's time.

## Results (2026-11-10)

Detectors, each alone (the candidate gate is any version, so its recall is 1.00 on a set drawn from candidates):

| Version | Precision | Recall |
|---|---|---|
| q-v0 | 0.88 | 0.41 |
| q-v1 | 0.42 | 0.71 |
| q-v2 | 0.28 | 0.56 |
| q-v3 | 0.89 | 0.47 |
| q-v4 | 0.83 | 0.56 |
| q-v5 | 0.71 | 0.71 |

The judge:

| Judge | Model | Rules | Precision | Recall | Cost |
|---|---|---|---|---|---|
| j-v3 | haiku | none (`--model-only`) | 0.68 | 0.76 | $0.067 |
| j-v3 | haiku | replayed | 0.81 | 0.74 | $0.023 |
| j-v4 | haiku | replayed, 117 cases | 0.82 | 0.91 | $0.052 |
| j-v5 | haiku | replayed, 117 cases, 2 runs | 0.82, 0.82 | 0.89, 0.94 | $0.052, $0.026 |
| j-v6 | haiku | replayed, 117 cases, the same 2 runs | 0.86, 0.87 | 0.89, 0.94 | the same calls |

j-v4 (2026-11-11, hand-over `cc2ed950`): a proposal that waits on the owner's yes is a question; other positions'
asks the message names are listed; the filed-ids rule clears a message only when it asks nothing else, also not
when an item with no id stands beside the ids ("a question about 1Password"); a wait on another position's "yes" is no
wait on the owner. A first run, before the last two changes, gave 0.81 and 0.86. Most of the seven false positives
are the of_asks readings that acme_org has not checked yet.
`pipeline --ids asks-117`: lost at the gate with the detectors; with `--gate all`, on the list in 5 runs of 5.

j-v5 (2026-11-16): a message that names only structural diffs already proposed (`sd-…`) and asks the owner nothing
else is cleared before the model (rule `proposed-diff`), and a diff's open card is listed among the asks the message
names. A diff is decided on its own card, and an answer to any other card approves nothing; a user organization's
structure keeper reported the duplicate (message `01a1e0f6-0eab`): a pane card asking the founder to approve a diff
he had approved on its card 74 s before. The model's prompt is j-v4's, and none of the 117 cases names a diff id, so
j-v4's scores stand for j-v5. Replayed on the 70 pane cards filed to 2026-11-16, the rule clears one: that duplicate.

j-v6 (2026-11-17): a turn in which the agent declared `of status waiting --on` the very position its card would go to,
its unit owner, is skipped before the model (rule `waits-on-receiver`): that position already sees the wait, and the
pulse nudges it. A user organization's structure keeper reported (message `01a1e998-1041`, through `of_server`) five
pane cards from one keeper's closing messages in a delegated unit, all for decisions its unit owner was already
bringing to the founder, and a founder card from its own closing line that it withdrew as a duplicate; with three more
cards of the same hour they are `asks-118` to `asks-126`. On j-v5's model calls (two runs, the same inputs), the rule
clears three labelled non-questions (`asks-041`, `asks-054`, `asks-094`) and no question; of the nine new cases, j-v5
filed 8 and 7, j-v6 files 6 and 6. A wait declared on another position, or a message to the receiver in the turn, is
no such rule: questions for the founder stood beside them (`asks-034`, `asks-076`; `asks-113`, which he reported
lost). The card the rule skips would also have escalated to the founder by the delegation's
`escalate_when` (`ask_unanswered > 2h`, a tag), and a declared wait does not (`of_review` on organic-framework PR
287). That is accepted on the field's readings: of the 68 pane cards filed for unit owners from 2026-11-10 to
2026-11-17, none escalated, and the rule would have skipped five. Four were closed by the unit owner within 1.3
minutes, by its answer or its reply to the asker, and the fifth was withdrawn by its asker as no question after 0.4
minutes. The reading replays `waits_on_receiver` on each judged candidate with a filed card, with the turn as
`of_judge.turn_of` reads it (the session's last prompt from a person or delivered message before the candidate),
and takes each card's closing from the asks view (`closed_by`), where a unit owner's reply to the asker closes its
card too (asks view version 6). A turn bounded by `of.continuation` events starts elsewhere and finds four
(`of_review`, PR 299).

Two prompt changes were measured and dropped. With the turn's last `of status`, the open founder asks of the unit
owner and of the waited-on position, an item "a decision another position already carries", and a line saying where
the card goes ("its unit owner, who decides its questions or brings them to the founder"): precision 0.89 and 0.90,
recall 0.71 and 0.80: the model read a delegated unit's questions as questions to another position, and missed ten and
seven, `asks-113` among them. Without that line: precision 0.91 and 0.83, recall 0.91 and 0.86, within Haiku's noise,
`asks-014` lost in both runs, and six of the nine new cases still filed. The cards left are a keeper's "the scripts
wait for your yes" in turns that declared no wait; the decision travelled through the unit owner's messages and the
founder's conductor, not through an ask, so nothing the judge reads shows it carried.

Haiku's verdicts vary between runs by a few cases; compare versions on more than one run.

## What the misses show

1. **The filed-ids rule clears new questions.** A message that names a filed ask and also asks something new
   without "?" is cleared as "names only asks already filed": acme_org's "a question about 1Password" (never filed)
   beside its card ids, three times; of_platform's "write down who runs the Solana bots"
   beside 8ee7f1bc.
2. **A pointer to an unfiled question reads as a reminder.** ci_keeper's notes "registry cache #1063 awaiting
   owner answers" (15:37Z, 15:52Z, 16:07Z) were judged status: the model assumes the questions were filed.
3. **Reminders of another position's ask.** The prompt lists only the agent's own filed asks, so "your 'yes' on
   e0e53b7f" (filed by another position) reads as new when the message has other content.

Next (j-v4): list to the model every open founder ask the message names, by any position; tell it that a question
it waits on that is in no list and has no id was never filed; and run the filed-ids rule only when the message
says nothing else to the owner. Then measure here, and add a sample of turns no detector picked, to measure the
detectors' recall on the stream.
