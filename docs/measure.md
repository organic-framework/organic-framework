# Measuring an organization before and after Organic Framework

Status: **method and first result**, 2026-11-11, by `of_architect`. The founder asked for the open-source release to include the before-and-after measure (2026-11-10). The first result is from one user organization, told without its name.

## 1. What it answers

Does an organization that steers many agents get more done for less of its owner's attention after it adopts Organic Framework? Each measure below compares a window before adoption with a window after it, by one method in both windows.

## 2. Windows

- **Before** is imported history: the agents' transcripts and the repositories' merged pull requests from the days before adoption (`of import`, `of import github`).
- **After** starts when the recorder hooks are installed (`of hooks install`) and the organization's registry exists. Both should start on the same day; record the hour.
- **Mark partial days and outages** (a restart of the owner's machine, a capacity stop) and leave them out of averages, or state that they are in.
- Compare weekdays with weekdays. Weekends behave differently: in the first result, most of the gain in merged work came from weekends.

## 3. The measures

| Measure | Events | How | Why it matters |
|---|---|---|---|
| Agents working at once | `of.turn_end` | the mean count of distinct sessions with a turn in each 5-minute bucket, 07:00–21:00 UTC | parallel work the owner does not have to drive |
| Merged pull requests per day | `of.work_record` from GitHub | by merge date, over the organization's repositories | output |
| Merged pull requests per owner prompt | `of.work_record`, `of.human_prompt` (origin `human`) | the day's merged PRs divided by the owner's typed prompts in the organization's sessions | output per word of the owner's |
| The owner's queue | `of.question` (declared asks), `view_asks` | declared asks per day; the median time to an answer; the share of open items that are really the owner's | whether questions reach the owner as decisions, not as status |
| Escalations from unit owners | declared asks of unit-owner positions, `escalated_from` | the share escalated to the owner | whether delegation holds |
| Structure changes by diff | `of.diff_proposed`, `of.diff_applied` | diffs per kind; the median time from proposal to apply | how fast the structure changes, and under whose yes |
| Guard denies | `of.mandate_check` with verdict `critical` | critical denies per day, by rule | secrets kept out of transcripts, and whether agents learn the rules |
| Capacity stops and moves | `of.holder_changed`, capacity snapshots | moves per day; the median length of a usage-limit stop | whether the owner still moves sessions by hand |
| Development loops | `of.loop_*` | hypotheses, runs and decisions per work | whether work runs as a loop rather than as tuning |
| Messages between agents | `of.agent_message_out`, `of.nudge_sent` | per day | the cost of coordination |
| The owner's corrections | `of.founder_correction` | corrections per position and week; the share that repeat an earlier one | whether rules stick (RFC 0006) |
| Output with an author position | `of.work_record` with a position | the share of merged PRs whose commit or PR names a position | without it, output per position cannot be measured |

## 4. Comparability

- **One method in both windows.** A series whose source changes at adoption (for example, questions found in panes before and declared asks after) is a change of mechanism, not a measured gain. Say so beside it.
- **Absolute values depend on the method.** A narrower baseline, such as one counting only some repositories, gives different numbers. Compare a series only with itself.
- **Name the source and the filter** of every number: the event types, the organization, the window.

## 5. First result: a user organization, 2026-11-05..10

The organization runs trading software with about 40 agent positions on five Claude accounts and one owner. "Before" is 2026-10-26..11-04 from imported history; "after" is from the hooks' start on the evening of 2026-11-05. 2026-11-10 is a partial day with a restart of the owner's machine.

| Measure | Before | After | Reading |
|---|---|---|---|
| Agents working at once (mean) | 2.1 | 8.5 (11-06..09) | About four times as many agents work in parallel. |
| Merged PRs per day | 148 | 184 | +25%. Weekdays are about level (~190). Weekends rose about fourfold: 48 and 37 before, 175 and 164 after. |
| Merged PRs per owner prompt | 1.36 | 2.15 (11-07..09) | Output per word of the owner's rose by 60%. His prompts fell from 109 to 85 a day. |
| The owner's queue | questions in panes | declared asks: 163 on 11-06, then 60, 29, 47, and 9 on 11-10 | The median time to an answer is 0.2–1.0 h (7.1 h on a Sunday). By 11-08, 73% of the items left were his. |
| Question judge (a small model, from 11-07) | every in-pane text that looked like a question reached him | 93% judged not a question | Status lines no longer reach the owner. |
| Unit owners | — | 115 declared asks, 17 (15%) escalated to the owner | Delegation holds: 85% are settled without him. |
| Structure by diff | positions created by hand | 19 diffs, 16 applied; median 12 minutes from proposal to apply, his yes included | Active positions rose from 31–35 to 40. |
| Guard | a token leaked through a file mention | 91 critical denies over about 11,000 checked tool calls a day | Denies fell from 52 a day to 6–14 as agents learned the rules. |
| Capacity across five accounts | 5 account switches by hand in 9 days | 170 moves; usage-limit stops last 0–10 minutes (median) | The owner no longer moves sessions. |
| Restart of the owner's machine | every session lost | 47 of 47 sessions resumed | — |
| Development loops | — | one search work: 58 hypotheses, 43 runs, 20 decisions in 3 days | Search runs as a loop of hypothesis, run and decision. |
| Messages between agents | — | 4,490 → 2,644 a day; nudges 303 → 128 a day | Less traffic, but still a lot. |
| The owner's corrections (from 11-08) | — | 27 corrections, 3 repeats | Too early for a trend. |

What did not work yet:
1. **Work went to the wrong role, and the systems map was wrong.** The mandate-fit judge reached 0.44 precision on real cases. RFC 0007 answers with a system architect, a map on evidence and ownership cells.
2. **Most output had no author position.** Only 9–43% of merged PRs a day named one, so output and value per position could not be measured.
3. **Usage-limit stops persisted:** 12–21 a day after 11-08.
4. **The owner's weekday prompts fell little:** 129 a day before, 109 on a Monday after. What changed is what he answers (asks rather than status) and how much ships per word.

## 6. Reproduce it on your organization

There is no `of` command for this measure yet. Each number above is a read-only query over the event store (`~/.local/state/of/of.db`, table `event`, and the view `view_asks`), filtered to one organization and one window, using the events of §3. Rerun it with the same windows at each structure review.
