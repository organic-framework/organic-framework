# Diagnose: the anti-pattern catalogue v0

Status: **v0, proposal**, 2026-11-06, by `of_field-analyst` (mandate: phase 5, from `of_architect`). Source: RFC 0001 v3 §5.4 ([`rfc/0001-organic-framework-platform.md`](rfc/0001-organic-framework-platform.md)).
Inputs:
- the events of [`events.md`](events.md);
- the registry and systems map of [`registry.md`](registry.md);
- the owner measures of [`studio.md`](studio.md) §3.
Calibration ran on the history of the first user organization, from 2026-11-06; its report stays in that organization's own repository.

Every threshold below is a **hypothesis, to be confirmed in the Lab (`lab-drift-v0`, [`lab.md`](lab.md)) and by owner labels**.

**Thresholds, priors and inputs are data in [`diagnose-v0.yaml`](diagnose-v0.yaml), the single source.** The numbers in this document are its version-0 values. A threshold change bumps the rule's version in the YAML, with no code change. The rules in `bin/of_diagnose_rules.py` (`of_field-analyst`) read the file; `bin/of_diagnose.py` (`of_studio`) runs them. The implementation in v0 is described in §6.

## 1. What an issue is

The catalogue turns measures into **issues, not alerts** (RFC §5.4).

| Part of an issue | What it is |
|---|---|
| `issue_id` | A stable hash of the rule id and the affected positions or systems. The same problem on the next day updates the same issue. |
| `rule`, `rule_version` | e.g. `ap.owner-router` and `ap.owner-router@0`. The catalogue as a whole is `diagnose-v0`. |
| `window` | Rolling 7 days, evaluated daily; trend rules compare with the previous 7 days |
| `evidence` | The series, edges, events and work records that triggered the rule, with event ids |
| `affected` | Positions, systems and edges |
| `remedies` | The default remedy first, then alternatives |
| `score` | Expected ΔT per minute of the owner's time to decide it (§3). Issues are ranked by it |
| `severity` | `low`, `medium`, `high` or `critical`, per rule or part ([`diagnose-v0.yaml`](diagnose-v0.yaml); default `medium`). Escalations take `high` and above; `critical` also bypasses the ranking. Chronic organization-wide conditions stay `medium` and are ranked, not pushed |
| `state` | open → acknowledged → remedied (a change with a review date, RFC §5.6) or dismissed (with an owner label). An issue that stops firing for two windows closes as `resolved` |

**Owner labels.** The owner, or an analyst the owner delegates, labels issues. A label is one event, `of.issue_labelled`, with:
- `issue_id`, `rule_version`;
- `label`: `true_positive`, `false_positive`, `true_but_not_now` or `wrong_remedy`;
- an optional note.

Labels are the in-tenant calibration set.

**Labels and versions.** A label belongs to a rule version.
- A version bump that changes **detection** starts with no labels: an earlier `false_positive` does not hide the new version's issue.
- A bump that changes only priors, remedies or deciders, that only adds parts, or that only narrows what fires (each issue it still raises has the same id and evidence as before) lists the earlier versions in `labels_carry_from` (YAML). Their labels still apply, so the owner is not asked twice about the same finding. Labels attach to issue ids, so a new part, with new ids, starts with none. A rule whose false-positive share over its last 20 labels exceeds 0.3 is demoted to "shown on request" until its threshold is revised.

**Observed positions.** A position counts as observed when it shows hook or import activity on at least `observed_days_gte` (3) distinct days of the window. Events written by the `of` CLI itself, such as registry bookkeeping, do not count. Silence is evidence only for observed positions. Rules that reason from absent or few messages consider observed positions only: coordination gap, over-coupled, bottleneck broker and chatty.

**Trend notation.** "Rising" means the least-squares slope over the window's daily values is positive and the last 3 days are above the window's median. "Flat" means the slope is within ±20% of the window mean per week.

## 2. The catalogue

Notation:
- *O*, the owner;
- *p*, *q*, positions;
- *s*, a system;
- WR, merged work records;
- msgs, peer messages (`of.agent_message_out`/`_in`).

Measures named in *italics* are defined in [`studio.md`](studio.md) §3.

### 2.1 Owner as universal router — `ap.owner-router@0`

- **Signals:**
  - *plumbing share* (`of.human_prompt` intent);
  - the owner's in-degree share of requests: asks to *O* ÷ (asks to *O* + peer requests).
    - *Asks to O* come from the ask lifecycle, in every state.
    - *Peer requests* are delivered peer messages that ask something: `of.agent_message_in` labelled `rq-v0`, which matches `(\?|прошу|просьба|можешь|можете|нужн[оаы]|please|could you|can you|request|ask)` (case-insensitive) on the first 300 characters of the body after the message header.
    - The receiving side counts each delivered message once. Sends that never landed, broadcasts and acknowledgements are left out.
    - The first user organization's week 7 gives about 0.44 (399 asks against about 510 peer requests), or 0.37 with the older proxy of waiting statements;
  - *L* and *λ* per day (§3.1).
- **Rule.** It fires when any of these holds:
  - plumbing share > 0.05;
  - in-degree share > 0.25;
  - L is rising while λ is flat.
- **Default remedy.**
  - A standing grant for the repeated approval classes (the asks answered with a bare yes, grouped by topic).
  - A coordinator for one cluster of routing.
  - Batched asks with expected ΔT, which the owner answers in one sitting.
- **ΔT basis:** freed owner minutes (§3, *v_c* · Δm). Prior *p* = 0.6. *m_decide* = 5 min.

### 2.2 Owner relaying between positions — `ap.owner-relay@1`

*v1 (2026-11-06), calibrated on the first user organization's history: the owner-words half uses the narrower `relayed` test and a share of the pair's traffic; coordinators are not counted as senders. v0 fired on 12 pairs, mostly busy pairs and mentions of other people. v1 fires on 4.*

- **Signals:**
  - **Owner relays:** owner prompts that pass text from *p* to *q*'s session. Either:
    - a paste (`relay_paste`) whose source is *p*;
    - or a routing prompt (intent `routing`) that names *p* by its name or an alias (at least 4 characters, word-bounded).
    - Prompts to the structure keeper are its job and do not count.
    - A pair with an owner-kind position on either side does not count. Talking to a unit owner about its unit's positions is delegation, and naming the owner's own position is not a relay (the first user organization, 2026-11-06: four such pairs right after its platform delegation).
  - **Owner words:** messages *p* → *q* that pass on the owner's decision or authority.
    - The count is over messages *q* received (`agent_message_in`), so each delivered message counts once; the pair's received messages are the denominator.
    - The sender is resolved by the matching `agent_message_out` (same text prefix, within 5 s, from *p*'s session). Failing that, by the `from-name` through registry names and aliases. The test is the `relayed` label (fa-2026-11-05: `RELAYED` on the first 300 characters of the body after the header). About 9 in 10 sampled matches carry the owner's yes or order.
  - Both are counted per ordered pair (*p* → *q*).
- **Rule.** For one pair over 7 days, either:
  - owner relays ≥ 3;
  - messages with owner words ≥ 5 **and** ≥ 10% of the pair's messages, sent by a position that is not a coordinator.
    - A coordinator carrying the owner's decisions to lanes is doing its mandate.
    - In a busy pair (about 100 messages a week), 5 such messages are status reporting, not the owner's channel.
- **Paste sources (`ps-v0`, of_platform, 2026-11-06).** `of.paste_sourced` names the session whose assistant text was pasted. It matches 8 probes of 40 letters and digits against other sessions' full texts of the previous 24 h, writes no source below a score of 0.5, and lets the best score win.
  - **On history:** 4 of 62 owner pastes found a source, all correct on inspection (scores 0.875–1.0). Only one is a pair of registered positions (the CI keeper → the bot hosts' keeper).
  - **Why so few:** most pasted text appears in no transcript on this machine. It comes from web chats, cloud runs or other machines, and such a paste is not a relay between two positions. Low recall here is mostly true negatives.
  - **Status of the relay half:** in the first user organization no pair reaches 3, so the half mostly counts routing prompts. Re-check after a week of live data: pastes copied from herdr panes on this machine should resolve.
- **Default remedy.**
  - A direct interface between *p* and *q*: a message channel and a grant for the class of decision that was relayed.
  - If the pair also trips §2.4, merge.
- **ΔT basis:** freed owner minutes, plus faster decisions (the relay's added wait). *p* = 0.7. *m_decide* = 5 min.

### 2.3 Coordination gap — `ap.coordination-gap@1`

*v1 (2026-11-06): an edge is judged unmet only when both keepers are observed (§1).*

- **Signal.** Socio-technical congruence (RFC §5.3.3), on observed keepers. A **required edge** between the keepers of *s* and *t* exists when either:
  - the systems map declares `depends_on`;
  - ≥ 3 WR in the window changed both *s* and *t* (logical coupling).

  An edge is **unmet** when its two positions exchanged no message, ask or artifact read in the window.
- **Rule.** A required edge that is unmet for 7 days while both keepers are observed. Severity rises with the co-changed WR count.
- **Default remedy.** An announce rule on the interface: the writer of a change to *s* that touches *t*'s interface notifies *t*'s keeper. Or move the seam so that one position owns both sides.
- **ΔT basis:** expected integration failures avoided. The incident rate per unmet edge is taken from the organization's history; until then use 0.05 per edge per week × the mean T loss of an incident. *p* = 0.5. *m_decide* = 3 min.

### 2.4 Over-coupled split — `ap.over-coupled@3`

*v1 (2026-11-06): waits count as recorded per pair, not organization-wide. Under v0, one day of declared states, from the first `of status` declarations on 2026-11-05, switched every pair to the mutual-wait branch. The rule then fell silent on the operator–hosts pair (207 messages, ratio 4.7, reciprocity 0.97).*
*v2 (2026-11-10): no pair with a unit owner (kind `owner`) or the structure keeper. When of import github began reading the `Position:` line (74% of the first user organization's PRs attributed, up from 26%), the rule ran there for the first time and fired on 26 pairs, 20 of them a unit owner with a position of its unit: the delegation working, not a split to merge, as §2.2 and §2.8 already treat them. v2 fires on 6, among them two positions of the main bot (253 messages, ratio 4.3) and the pair of the bot's operator and its hosts' keeper. Labels carry over: v2 only narrows what fires.*
*v3 (2026-11-10): decided by the structure keeper, as §2.10 and the structure-coverage family (§2.23–2.26), through `of_platform`'s daily keeper digest (`of_architect`'s decision; its remedies, a merge or a published interface, are structure diffs the owner approves). Labels carry over: only the decider changed.*

- **Signals:**
  - the **interface ratio**: msgs between *p* and *q* ÷ WR of *p* and *q*;
  - reciprocity: the smaller direction ÷ the larger;
  - mutual waits: `blocked_on: peer:q` on *p* and `peer:p` on *q* in the same window.
- **Rule.** All three hold:
  - interface ratio ≥ 3;
  - reciprocity ≥ 0.5;
  - at least one mutual wait, or a reciprocity ≥ 0.8 if waits are not recorded for the pair.
    - Waits are **recorded for a pair** when both positions declared a state (`of.state_declared`) on ≥ `observed_days_gte` distinct days of the window.

  Pairs where one side is a coordinator of shared capacity go to §2.10 instead.
- **Default remedy.** Merge *p* and *q* if the combined working set fits one holder (§2.5 signals below their thresholds). Otherwise publish the interface.
- **ΔT basis:** waiting hours removed on *raise*/*sustain* positions × their T rate. *p* = 0.5. *m_decide* = 15 min.

### 2.5 Context overload — `ap.context-overload@0`

- **Signals:**
  - compactions per active day (`of.compaction`);
  - rereads of the same artifact;
  - forgotten-fact errors: `of.correction` with kind `fact`, and owner prompts classified as re-explaining, within 2 hours after a compaction.
- **Rule.** Over 7 days:
  - compactions per active day ≥ max(4, 3 × the organization's median);
  - and ≥ 2 forgotten-fact errors after compactions.
- **Default remedy.**
  - A fresh holder with a written handover, if the load is history-bound (one long-lived session).
  - A split at a seam, if the working set itself is too big (many systems, many inbound edges: see §2.8).
- **ΔT basis:** corrections avoided × their mean cost, plus owner re-explain minutes freed. *p* = 0.5. *m_decide* = 10 min.

### 2.6 Fragmented ownership — `ap.fragmented-ownership@1`

*v1 (2026-11-06): the code part counts only windows in which the keeper already held the system. Under v0, it fired on two systems whose keepers were named on 2026-11-05. Both windows measured writes made before those keepers existed.*

- **Signals:**
  - writers per system and the keeper's share of writes (*coverage matrix*, studio §3.4);
  - **runtime writes**: deploys, host actions and settings changes on *s*, attributed to a position.
- **Rule.**
  - **(runtime)** Any runtime write to *s* by a position other than its keeper. This is **critical**: it bypasses ranking and goes straight to the owner.
  - **(code)** *s* has ≥ 3 writers, and the keeper's share of its WR is < 0.3, for two consecutive windows.
    - A window counts only if the same position was *s*'s keeper at its start (`registry_at`, the registry's history).
    - Before that, the part reports `quiet: keeper since <date>`. The earliest it can fire is two full windows after the keeper was named.
- **Default remedy.**
  - Hand writes to the system agent: others request the change from the keeper.
  - For code, keep the writers, but route their changes through the keeper's review.
- **ΔT basis:** incident risk (runtime); review cost against rework (code). *p* = 0.7 (runtime), 0.4 (code). *m_decide* = 3 min.

### 2.7 Orphan work — `ap.orphan-work@3`

*v1 (2026-11-06): three parts, each with its own prior, decision time, remedies and decider.*
*v2 (2026-11-07, from the first user organization through `of_architect`): `keeperless` fires only on a system that is live and kept by nobody, by the registry fields of organic-framework commit 5523cb6. Labels carry over: v2 only narrows what fires.*
*v3 (2026-11-10): the `keeperless` part moves to §2.23 `unkept-system`, which flags a live system nobody keeps whether or not work passes through it, with its merged PRs as evidence, and goes to the structure keeper. Two rules on one finding would have reached two deciders. A `cluster` on a system nobody keeps is evidence on that system's §2.23 issue (`merge_into`). Labels carry over: v3 only narrows what fires.*

- **Signals:**
  - `of.mandate_check` with verdict `unowned`, clustered by system or topic;
  - WR on systems without a keeper;
  - WR that map to no system.
- **Rule.** Over 7 days, any of these holds:
  - ≥ 3 `unowned` verdicts on one cluster;
    - temporary and scratch paths are not work areas and form no cluster: `/tmp`, `/private/tmp`, `$TMPDIR`, any `…/scratchpad/…`;
  - ≥ 5% of WR with no system.
  - Until v3: ≥ 5 WR on a live system without a keeper (`keeperless`), now §2.23. A system with `governed_by: external:<who>` was kept, and an `unsupported` or `retired` system's changes were shown, not flagged; §2.23 keeps both.
- **Parts and remedies.**

  | Part | Signal | Remedy | Decider | *p* | *m_decide* |
  |---|---|---|---|---|---|
  | `cluster` | `unowned` verdicts | A new position with a brief draft, or the nearest mandate extended | owner | 0.6 | 10 min |
  | `unmapped` | WR with no system | Extend the systems map (paths or a new system) | the structure keeper | 0.8 | 3 min |

- **Input rule** (2026-11-07, no version bump: it fixes the input, not the rule): `cluster` counts `unowned` verdicts only from mandate router `rt-v4` on (`of_studio` 13b781f). `rt-v2` and `rt-v3` judged paths in linked worktrees outside the repository unowned: `<repo>-wt/<name>`, `<repo>-wt-<name>`, `.worktrees/<other name>`. Those false clusters were most of the first user organization's 7 cluster issues. `rt-v4` resolves a linked worktree through its `.git` file.
- **Calibration of v2.** Two `keeperless` issues of the first user organization reached the owner as high, and its money-side owner labelled them on 2026-11-07:
  - a research system, kept by an external colleague: `true_but_not_now`;
  - a second bot, unsupported and not running: `wrong_remedy`.

  Both are the v2 negatives in `lab-drift-v0`, with a live keeperless system as the positive.
- **ΔT basis:** risk of unowned change, plus the value the cluster already produces.

### 2.8 Bottleneck broker — `ap.bottleneck-broker@1`

*v1 (2026-11-06), calibrated on the first user organization's event log: betweenness on strong ties only, a lower inbound share, and coordinators and the structure keeper excluded.*
- *Strong ties.* On every-message edges the graph is nearly complete (29 positions, degrees 21–25), and betweenness separates nobody.
- *Inbound share.* With senders resolved, the busiest position receives 18% of peer messages, not the 29% of the transcript-based first pass.

- **Signals:**
  - a position's inbound share of peer messages;
  - normalized betweenness on the interaction graph of **strong ties**: undirected edges between positions that exchanged ≥ 5 messages in the window;
  - peer waits naming it, compared when it is working and when it is suspended or blocked.
- **Rule.** Two conditions hold:
  - inbound share ≥ 0.15 (about 4–5 times an even share in an organization of 30 positions), or betweenness ≥ 0.2;
  - and peer-wait hours on it per hour of its unavailability are ≥ 2 × its baseline. If waits are not yet recorded, require both structural signals.

  Coordinators and the structure keeper broker by mandate and are not judged here: their traffic is §2.10's case.
  - On the first user organization, the rule fires on its main bot's operator in both windows: W7 (inbound 0.18, betweenness 0.25) and the 7 days to 2026-11-06 (0.16, 0.22).
  - The k8s keeper is just below the inbound threshold (0.14 in both windows), with betweenness 0.22 and 0.40. It is the platform delegation's hub.
  - Without recorded waits, both structural signals are required, so it does not fire. Expect it to fire once waits on it are recorded.
- **Default remedy.** A second path: a deputy, or a published interface for its most frequent request. Or split its interfaces among positions.
- **ΔT basis:** waiting hours removed on *raise*/*sustain* positions, plus the single-point-of-failure risk. *p* = 0.5. *m_decide* = 15 min.

### 2.9 Missing verifier — `ap.missing-verifier@0`

- **Signals:**
  - rework on a position's results: `reverts`/`fixes` relations on its WR, and `of.correction` on its claims;
  - review evidence on the path: a review by another position or model, recorded as a work-record relation or a correction source.
- **Rule.** Over 7 days, all of:
  - (corrections + reverts) ÷ WR ≥ 0.2;
  - with ≥ 3 corrections;
  - and no review evidence on the position's path.
- **Default remedy.** A verification step: a cross-model review before results go to the owner or to production. Or a verifier position for the class of result.
- **ΔT basis:** rework avoided, plus decisions made on wrong facts avoided. *p* = 0.6. *m_decide* = 5 min.

### 2.10 Chatty coordination — `ap.chatty@2`

*v1 (2026-11-07), at the founder's request through `of_architect` and `of_lab`: the receiver's side. v0 saw chatter only as messages per merged PR, from the sender's side. v1 adds two parts: a position whose turns are mostly started by typed-in text, and a waiter whose wait is pushed again and again by nudges. Built by `of_studio` (b8242dd). The pair and organization part keep their issue ids, so owner labels on @0 still apply to them.*
*v2 (2026-11-10): decided by the structure keeper, not the owner, through `of_platform`'s daily keeper digest (`of_architect`'s decision). Its remedies, a published interface, a queue or a hand-over, are structure diffs the owner approves. With PRs attributed it raised 56 issues in the first user organization that would otherwise have reached its owner. Labels carry over: only the decider changed.*

*Counting rule (no version bump: it fixes the input, not the rule): only days that have ended and whose merged PRs are imported count, for the parts that read WR and for §2.13. Otherwise, today's missing PRs inflate msgs per WR.*

*Input rule (2026-11-10, no version bump): the organization part compares like with like. From 2026-11-06 the relay logs every `of send` (`of.agent_message_out` with source `of`), where earlier windows saw only messages found in transcripts (`import`, `hook`): in the first user organization about 3,300 a day against about 500, and the part fired "3.4 to 25.4 per PR". When the recorders that wrote at least 10% of each window's messages differ, the part is not compared and says so in the rule's notes. From 2026-11-13 both windows are relay-era. The pair part's threshold (10 per PR) was set on transcript-era counts; it is recalibrated on the first relay-era week, with labels.*

*Input rule (2026-11-10, no version bump): the organization and pair parts divide by **attributed** merged PRs, so they need `wr.position` (at least half of the merged PRs attributed to a position), as §2.4 does. On the first user organization on 2026-11-10, 90 of 820 merged PRs were attributed: 133 pair issues fired, 68 of them on pairs with no attributed PR. Until PRs are attributed, these two parts report blocked.*

- **Signals:**
  - msgs per WR, organization-wide and per pair;
  - messages never used: no reply, no reference and no action within 24 h. This needs reference tracking; until then, use msgs with no reply;
  - **typed-in turns** per receiver and hour: turns started in a session by text typed into its pane by someone other than its owner. These are relayed messages (`of.agent_message_in` from the hook), `of pulse` nudges (a prompt in the pulse's frame) and other tools' notes (a prompt whose corrected origin is `injected`);
  - nudges per (waiter, nudged) pair.
- **Rule.** Over 7 days, one of the parts holds:

  | Part | Condition |
  |---|---|
  | organization | msgs per WR rises ≥ 50% against the previous window |
  | pair | one pair has ≥ 10 msgs per WR, typically booking or status traffic with a coordinator |
  | `receiver_flood` | a position had ≥ 20 typed-in turns in an hour, on ≥ 3 hours |
  | `repeat_nudges` | `of pulse` nudged the same position ≥ 10 times for the same waiter |

  Coordinators and the structure keeper are judged here, not excluded: their traffic is this rule's case (§2.8).
- **Default remedy.**
  - Organization and pair parts: a published interface, such as a booking tool, a status surface, or a queue the coordinator reads, instead of messages.
  - `receiver_flood`:
    - deliver in batches (a digest every N minutes) or into a queue the receiver reads between tasks;
    - give its most frequent senders a published interface;
    - when one position receives for a whole unit, split its interfaces (§2.8).
  - `repeat_nudges`: the wait becomes a hand-over instead of nudges, or goes to the owner of both. No due date: the founder ruled out invented deadlines (ask `53628150`). A nudge that repeats ten times is not being answered, and §2.15 shows whether the nudged position's queue is the cause.
- **Calibration** in the first user organization, the 7 days to 2026-11-07:
  - `receiver_flood` fires on 5 positions: the platform unit's owner (787 turns, peak 130 in an hour), the structure keeper, the main bot's operator, the CI runners' coordinator and the k8s keeper. All of them are hubs: a unit owner, the structure keeper, the broker of §2.8, a coordinator and the delegation's hub.
  - `repeat_nudges` fires on 6 pairs; the largest is a project position → the main bot's operator, 28 nudges.

  The thresholds are first guesses. Labels on these 11 issues set them.
- **ΔT basis:** holder time and context freed on *raise* positions: typed-in turns removed × the cost of a turn. *p* = 0.6. *m_decide* = 5 min.

### 2.11 Unclear mandate — `ap.unclear-mandate@1`

*v1 (2026-11-06): only substantive changes count, and a position settles first.*
- *The v0 issue.* v0 fired on 4 positions, from the registry build-out of 2026-11-05/06.
  - The schema gained `mandate_exit` and `mandate_functions`, so every position "changed".
  - Summaries were reworded ("prod" → "production").

- **Signals:**
  - `of.mandate_changed` events with a **substantive** change:
    - `throughput` changes;
    - the brief (`mandate`) changes;
    - `mandate_exit.done_when` changes from a non-empty value;
    - `status` goes from `done` back to `active`.
  - What does not count:
    - adding a field the previous snapshot did not have (schema adoption);
    - an edit to `summary` alone;
    - any change within `settle_days` (3) of the position's first snapshot (onboarding);
  - owner verdicts with `reason_class` `wrong_priority` or `over_engineered`;
  - MAST specification-failure tags (sampled, judged by a model, RFC §5.3.5).
- **Rule.** For one position over 7 days, any of these holds:
  - ≥ 2 mandate changes (flip-flops);
  - ≥ 3 such verdicts;
  - specification-failure tags on ≥ 20% of its sampled turns.
- **Default remedy.** Rewrite the brief with done criteria. Split it if two jobs share one position.
- **ΔT basis:** rework and owner corrections avoided. *p* = 0.5. *m_decide* = 10 min.

### 2.12 Management-function gap — `ap.function-gap@0`

- **Signal.** The management-functions check of [`method.md`](method.md): for each function, does an active position's `mandate_functions` ([`registry.md`](registry.md)) cover it? It runs weekly against the registry, and when the stakeholder map changes.
- **Rule.** A function with no covering position. When the organization's stakeholder map exists as data and lists a right-to-operate interest that depends on the function, the issue is critical. When the map lists any other interest that depends on it, the severity is `high`. Without such evidence it is `medium`. Otherwise the issue is ranked like any other. An organization that does not need a function (for example marketing) dismisses it with a `false_positive` label.
- **Default remedy.** A new position (often a keeper of a process), or an external organization with an interface (for example counsel).
- **ΔT basis:** the consequence of the uncovered interest from the stakeholder map: risk-weighted loss, or the right to operate. *p* = 0.7. *m_decide* = 20 min.

### 2.13 Churn — `ap.churn@0`

- **Signals:**
  - `of.holder_changed` and `of.mandate_changed` per position;
  - Workplace moves affecting many positions at once (account switches, `of.state_declared` suspended → resumed with an account reason);
  - WR per day.
- **Rule.** Either:
  - one position has ≥ 2 holder or mandate changes in 7 days;
  - or a change hits ≥ 50% of active positions on one day;

  and in either case WR fall ≥ 50% against the trailing 7-day median on the days of change.
- **Default remedy.** Freeze structure changes. Lengthen review windows. Make the swap a platform operation that carries continuation state.
- **ΔT basis:** the measured throughput dip × its expected recurrence. *p* = 0.6. *m_decide* = 5 min.

### 2.14 Lingering collaboration — `ap.lingering@0`

- **Signals:**
  - a temporary position or pairing with a declared exit criterion (a date, or an event such as "until X is back" or "until the first Y");
  - the event or date that meets it;
  - activity after it.
- **Rule.** The exit criterion has been met for ≥ 48 h, the position or pair is still active, and there is no `of.mandate_changed` that extends it.
- **Default remedy.** Close it, merge it into the permanent owner, or formalize it as a position with a new mandate.
- **ΔT basis:** holder and owner time freed, plus ambiguity of ownership removed. *p* = 0.6. *m_decide* = 3 min.
- **Note.** This rule reads the position's `mandate_exit` (`done_when`, `review`; [`registry.md`](registry.md)). The exit is met when `done_when` is observed or the `review` date has passed. Without the field, the rule is blocked, and history calibrates it only by owner labels.

### 2.15 Inbound backlog — `ap.inbound-backlog@0`

*v0 (2026-11-06), defined on the relay's `of.inbound_queue` events (from 2a5ad71). Queue data starts on 2026-11-06 at 12:47Z, so the rule is blocked until queues are observed on 3 days, and its thresholds are uncalibrated.*

A position receives more than it takes: peer messages wait in its queue on the server, and their senders wait with them.

- **Signals** (*inbound queues*, `of_measures.inbound_queues`, per position and day):
  - the time-weighted average depth;
  - backlog minutes: minutes when the oldest waiting message was older than 30 min (`of pulse`'s stall threshold);
  - whether the position has a live holder. In v0 this comes from the registry: status `active` and a holder session, or a person. Presence is not in the model.

  Until 2026-11-06 12:53Z (of_platform's f888177) the relay recorded only a change of depth, so earlier ages are upper bounds. From then on it also records a change of the oldest message at the same depth, and ages are exact.
- **Rule.** Over 7 days, the queue was observed on ≥ 3 days, and one of two parts holds.

  A day counts as observed when the organization had any queue change that day. A day with no messages does not count, so the unblock can slip by quiet days. This can only delay the rule, never make it fire. A relay heartbeat is not needed for v0.

  - **Overload.** The position has a live holder, has backlog ≥ 60 min on ≥ 3 days, and has an average depth ≥ 2 over the window.
  - **Unattended.** The position has no live holder (parked, finished, or no session), and messages for it waited ≥ 60 min on ≥ 2 days. Senders write to a position nobody holds.

  Exclusions and overlaps:
  - positions of kind `owner` are excluded: the owner's inbound load is §2.1's case;
  - when §2.8 fires on the same position, the backlog is attached to that issue as evidence, and no second issue is raised.
- **Default remedy.**
  - *Overload:*
    - a deputy, or a published interface for the most frequent request (as in §2.8);
    - for a unit owner, keepers decide routine within their grants instead of asking (the delegation's `escalate_when`).
  - *Unattended:* route the senders to the successor or to the unit owner, or give the position a holder.
- **Severity.** Overload is `medium`. Unattended is `high`, because work waits on nobody.
- **ΔT basis:** sender waiting hours removed: the backlog minutes × the senders waiting, on *raise*/*sustain* positions. Owner minutes are 0 unless the owner relays around the queue (§2.2). *p* = 0.5. *m_decide* = 5 min.

### 2.16–2.22 Development-loop rules (RFC 0005 §6)

*v0 (2026-11-07), built: `bin/of_diagnose_rules.py`, on `of_studio`'s projection (`of_model.loops`, ce4d547) and `of_platform`'s loop events (3a9a476, 1b22476).* [RFC 0005](rfc/0005-development-loops.md) makes a work's development loop an object:
- a target in money;
- the measures that map each rung's metric to money;
- an evaluation ladder, cheapest rung first;
- a hypothesis register and a decision log.

These seven rules watch it. Each is per work, with `affected: [<work>]`.

**Inputs and status.** They read the loop projection of §4.1, which does not exist yet:
- the schema is `of_architect`'s (RFC §8 step 1);
- the events are `of_platform`'s (step 2);
- the loop metrics are `of_studio`'s (step 3).

A rule reports `blocked` while the organization has no declared loop or no run in the window. Their first positive cases are the first user organization's search week, 2026-11-02..07, checked once that week's loop events are derived from the runner records.

**Remedies.** The first remedy of every loop rule is a pulse nudge to the work's doer, with the rule's evidence in one line. The issue reaches the owner only for a decision that is the owner's under the accountability rules of 2026-11-07 (acc-v0):
- shadow and one-site rungs are the unit owner's;
- all sites is a `prod_change`;
- the plateau review is the work owner's.

**Deciders.** Two new decider values (`of_studio`'s routing in the run):
- `doer`: the positions doing the work;
- `work_owner`: the work's `owner` in the registry, or the unit owner when the work names none.

| § | Rule | Fires when, over 7 days | Decider | Severity |
|---|---|---|---|---|
| 2.16 | `ap.loop-cheapest-rung@0` | ≥ 2 runs on a rung for a hypothesis that has no accepted run on a cheaper rung that already existed (`since` ≤ the run) | doer | low |
| 2.17 | `ap.loop-checkpoint@0` | The loop ran in the last `checkpoint_days` (default 3), but no run reached a production rung in that time, counted from the loop's start or its last production run | work_owner | medium |
| 2.18 | `ap.loop-plateau@0` | 3 consecutive runs on the target metric each improve the best earlier result by < 1% of it | work_owner | medium |
| 2.19 | `ap.loop-wip@0` | More than `wip_arms` hypotheses in `testing` for ≥ 12 h | doer | low |
| 2.20 | `ap.loop-babysit-model@0` | ≥ 100 runner commands and monitors by the loop's doers on a model ranked above `babysit_model` (default `sonnet`) | doer, with `of_capacity` | low |
| 2.21 | `ap.loop-objective-drift@0` | ≥ 3 runs, and ≥ 30% of the loop's runs, measure a metric that is neither the target's nor a measure with `to_money` | work_owner | medium |
| 2.22 | `ap.loop-eval-bottleneck@0` | On one rung with ≥ 3 runs, the wait share (queued to started, over queued to ended) is > 40% | work_owner | medium |

Per rule:

- **2.16 Cheapest rung first.**
  - A rung is cheaper when it comes earlier in the ladder, which is listed cheapest first.
  - Only rungs that existed at the time count, through the rung's `since`: the first user organization's offline harness came on day four, and runs before it skipped nothing.
  - Runs without a hypothesis cannot be judged. Their count is evidence, and they do not fire.
  - Nudge: "Run it on `<cheaper rung>` first: `<its latency>` instead of `<this rung's latency>`."
  - ΔT basis: the runner hours of the skipped rung's cost difference; owner minutes 0.
- **2.17 Production checkpoint.**
  - Production rungs are the rungs marked `production: true` in the ladder (shadow and higher).
  - Nudge: "No production signal for `<n>` days: what goes to shadow next?"
  - The issue goes to the work owner, who approves the shadow rung.
  - ΔT basis: the stake per day (`target.stake`) × the days without a production signal, as cost of delay.
- **2.18 Plateau.**
  - A cycle is a run whose metric is the target's.
  - The gain is its value over the best earlier value, relative to that best. The direction comes from `target.better` (`higher`, the default, or `lower`).
  - Remedy: a step back (double loop). The owner asks a second position for a reframe review: by default the position the unit owner names; for the OF team, `of_architect` (RFC §9 q2).
  - The first user organization's case: the day pass stayed at the same value from 11-05 to 11-07.
- **2.19 WIP limit.**
  - Arms in flight are hypotheses whose last status is `testing`.
  - Nudge: "Finish or park one arm."
- **2.20 Babysitting model.**
  - Runner commands and monitors are tool calls classed `runner` or `monitor` (§4.1).
  - The model rank is `haiku` < `sonnet` < `opus` < `fable`, by the model id's family.
  - Remedy: route runner commands and monitors to a subagent or session on `babysit_model`. The routing is `of_capacity`'s (RFC §8 step 5).
  - The first user organization's case: 1,449 runner commands and 297 monitors, all on Fable 5.1; $969 in the week.
  - ΔT basis: the cost difference between the two models on those calls; owner minutes 0.
- **2.21 Objective drift (Goodhart).**
  - A run's metric must be the target's, or a measure whose `to_money` is set.
  - Nudge: "Price `<metric>` in `<target money>` before more work on it."
  - A `change_objective` decision without a money metric fires at once, whatever the counts.
  - The first user organization's case: from 11-05 the runs measured p50 milliseconds, with no money per millisecond.
- **2.22 Evaluation bottleneck.**
  - The wait share is per rung: (started − queued) summed over (ended − queued) summed, over the rung's runs that ended in the window.
  - Remedy: elevate the rung (more runners for it), or move hypotheses to a cheaper rung. It goes to the owner, since it costs capacity.
  - v0 reads `of_studio`'s wait share as the projection gives it, over all the rung's runs so far, and fires only for a loop that ran in the window. A week-windowed share is v1, once the projection gives one.
  - The first user organization's case: 76 of 92 relayed messages to the doer were about runner slots, and 58 of 118 hours were gaps.

**Calibration.** The thresholds are RFC 0005 §6's, unconfirmed. Each rule gets synthetic fixtures in `lab-drift-v0` (a positive and a negative), and the search week as its first real case. A rule that fires on every loop, or on none, has a wrong threshold (§5).

### 2.23–2.26 Structure coverage

*v0 (2026-11-10), built. Decided by `of_architect` the same day (relay messages 01a1c62b and 01a1c62e), after the founder's root-cause review of why the first user organization's structure gaps raised no issue (`of_platform`): every rule until then diagnosed behaviour in a 7-day window against the registry, and none checked the registry's own coverage. A gap was seen only when work happened to pass through it.*

The decision:
- **The catalogue reads structure as well as behaviour.** §2.12 already reads the registry. Studio already shows the flags below; what was missing was the issue, so nobody was asked.
- **One computation.** These rules read Studio's own coverage (`of_model.coverage`, [`studio.md`](studio.md) §3.4) and cells (`of_model.cells_of`, [RFC 0007](rfc/0007-system-architect-and-cells.md) §2.3), never a second computation, so Studio and the issue never disagree.
- **Routing.** Every issue of the family goes to the organization's structure keeper, who brings a diff. Never straight to the owner: the owner approves the diff.
- **Systems kept outside stand as decided.** `governed_by: external:<who>` counts as kept (§2.7 v2). RFC 0007 §2.2 now makes such a fact count only with the owner's ask id under `confirmed:`, and `of registry check` warns on it, so a false claim is caught there.

**2.23 A live system nobody keeps — `ap.unkept-system@0`.** Two parts:

| Part | Fires when | Remedy |
|---|---|---|
| `keeper` | Studio flags the live system `no_keeper`, `keeper_unknown` or `keeper_<status>` (a parked or finished keeper), with or without work. Kept, as Studio computes it: a keeper, `governed_by: external:<who>`, a lifecycle other than `live`, or a supersystem whose every part is kept | Name a keeper, a new keeper position, an external governor with the owner's ask id, or a lifecycle if it no longer runs |
| `unit` | In an organization with units, a live system in no unit, not kept outside, and not a supersystem whose parts are all in units | Put it in the unit whose owner answers for it, or a new unit |

- Studio's `orphan` flag (no position touches the system) is evidence on the `keeper` part (`untouched`), not an issue. Alone, it marks only a system kept outside that none of the organization's positions works on, which is by design.
- The evidence carries the system's merged PRs in the window, its writers and its unit.
- *p* = 0.6 (`keeper`), 0.5 (`unit`); *m_decide* = 5 and 3 min. Severity medium and low.
- **Calibration** (2026-11-10): in the first user organization it fires once, on a delivery system, live and kept by nobody with 4 merged PRs (under §2.7 v2's 5). Three other systems are kept outside, and a fourth is the supersystem of systems that are all kept and all in units. On the OF team it is quiet.

**2.24 Work off the map — `ap.off-map-work@0`.** A position whose file edits mostly land in systems that other positions keep.
- **Signal:** the mandate router's verdicts (`of.mandate_check`, from `rt-v4`) on file edits (`Edit`, `Write`, `MultiEdit`, `NotebookEdit`) in the window: `in_mandate` against `owned_by:<keeper>`.
- **Left out:**
  - reads, since reading others' code is normal;
  - `Bash`, since a shell at a repository root is `unscoped`;
  - paths outside the organization;
  - paths no system claims: those are the map's gap, §2.7's `cluster`, and appear here only as evidence.
- **Rule:** at least 10 such edits, and an `in_mandate` share under 0.5. One issue per position.
- **Parts by remedy**, same issue id:
  - `no_own_paths`: none of its edits falls in its own system, so its mandate maps to no paths. The fix is in the registry: name its subsystem with paths (a system with `parent` and `paths`) and set the position's system to it.
  - `mostly_elsewhere`: list the cells it works in under `owns`, name the subsystem, or hand the work to the keeper of the system it changes.
- `of fit`'s mandate-fit verdicts join the evidence once `of fit` records them.
- *p* = 0.5, *m_decide* = 10 min.
- **Calibration** (2026-11-10, the first user organization): 7 positions. Four are one symptom: the main bot's positions are separated only by subsystems no registry field names, and much of their code lives in a shared core its operator keeps:
  - two of the bot's positions (34 of 38 edits in the core; 23 of 42);
  - two positions of other systems whose code lives in the core (29 of 29; 22 of 22).

  The other three: a price-feed position editing a market-data system, a clean-up project editing the data platform, and the observability keeper editing a model-training system.

  Counting unclaimed paths as "outside" fired on all six OF positions: the OF map claims no path of its own repository, and §2.7's clusters already say so.

**2.25 A needed cell nobody decides — `ap.cell-gap@0`.** RFC 0007 step 5, grown from §2.12: a cell is a system or subsystem crossed with a kind of work. The system architect declares `needs:` on systems, and the structure keeper declares `owns:` on positions ([`registry.md`](registry.md)).

| Part | Fires when | Remedy |
|---|---|---|
| `nobody` | A needed cell of a live system has no active owner | Extend a position's `owns`, a new position, or drop the kind from `needs` |
| `no_decider` | Doers, and none decides | Mark one doer `decides: true` |
| `system` | The system declares `needs`, and no active position owns any of its cells yet. One issue for the system, not one per cell: its positions are not mapped yet | Map the system's positions onto its cells |

- It is blocked while no system declares `needs`. The issues start where the cells exist.
- A `no_decider` gap on an overlapping cell is evidence on the §2.26 issue (`merge_into`).
- *p* = 0.6 (`nobody`, `system`), 0.5 (`no_decider`).

**2.26 Too many owners on one cell — `ap.cell-overlap@0`.** A cell with 3 or more active doers (`of_model.OVERLAP_DOERS`, as Studio shows it).
- The remedy: name subsystems so the positions' cells are distinct, keep one decider and make the rest doers, or merge positions that share every cell.
- It is blocked while no position declares `owns`. Two deciders on one cell is a registry error, and `of registry check` warns on it; it is not an issue.
- *p* = 0.5, *m_decide* = 10 min.

What stays without a rule: a task given to the wrong role. A task message carries no kind-of-work label yet. Once tasks are classified into cells (RFC 0007 step 6, `of fit` and `of delegate`), an assignment to a position that does not own the task's cell becomes checkable; that is RFC 0001 §5.7.

## 3. Ranking: expected ΔT per minute of the owner's time

score = *p* · (ΔT_direct + *v_c* · Δ*m_c* − *C_r*) / *m_decide*

| Term | Definition | Default until calibrated |
|---|---|---|
| *p* | Probability that the default remedy removes the issue. A prior per rule (§2), updated from remedied issues and owner labels (the share of `remedied` issues that resolve within two windows) | per rule |
| ΔT_direct | Expected T gained per week by the remedy on the affected positions: waiting hours removed on *raise*/*sustain* positions × their T rate; or expected incident loss avoided | From the organization's T source (app adapter, [`events.md`](events.md) backlog); 0 if T is not recorded |
| *v_c* | Value of one owner minute: T over the trailing 4 weeks ÷ the owner's minutes on throughput work in those weeks, i.e. constraint time × share (a) of [`success.md`](success.md). Without T, rank by Δ*m_c* only | — |
| Δ*m_c* | Owner minutes per week the remedy frees: plumbing prompts, relays, needless asks or re-explanations removed × minutes per prompt | 1.5 min per prompt until `focus` events give real minutes |
| *C_r* | Weekly cost of the remedy in the same units: a new position's OE, review time | 0 for grants and rules |
| *m_decide* | Owner minutes to decide the issue | per rule (§2) |

- **Critical issues bypass the ranking:** runtime writes by non-keepers (§2.6), and function gaps that touch the right to operate (§2.12).
- **Risks without ΔT yet.** Several rules have a ΔT basis that is not recorded yet: incident loss for coordination gaps, unowned change, an uncovered function. Their score is 0, and no ΔT is invented for them. The inbox therefore shows three groups, in this order:
  1. critical issues;
  2. issues with a score above 0, ranked by score;
  3. **risks without ΔT yet**, ordered by severity, then by the age of the oldest evidence.

  A risk moves into the ranked group once its ΔT basis is measured.
- **Ties** are broken by the age of the oldest evidence.

## 4. What the rules need that is not recorded yet

| Rule | Needs | Where it is planned |
|---|---|---|
| 2.1, 2.2 | `rq-v0` (is a request) on `agent_message_in`; **`paste_source`** on `relay_paste` prompts, the session whose output was pasted, found at derive by matching the pasted text against other sessions' full assistant texts of the previous 24 h | `rq-v0`: of_classify (asked 2026-11-06), of_measures applies it meanwhile; `paste_source`: new, derive |
| 2.3, 2.4, 2.6, 2.9, 2.10 (pair, organization) | WR attributed to positions; systems per WR | Both exist. Systems by paths come from `of import github`. Positions come from transcript PR links joined onto the merged PR, and from organic-framework 39332c3 (2026-11-10) the `Position:` line of a PR body. On 2026-11-10 only 90 of the first user organization's 820 merged PRs in the window were attributed, so these rules were blocked |
| 2.4, 2.8 | `blocked_on` with an end | backlog v1.1 item 7 |
| 2.5 | `of.compaction` (v1); rereads; `of.correction` kind `fact` | v1; reread detection new; backlog item 9 |
| 2.6 runtime | runtime writes as work records (deploys, host actions, settings) | `of.deployment` (backlog item 2); host actions: new |
| 2.9 | `reverts`/`fixes` relations; review evidence | backlog item 10; review relation new |
| 2.10 | reply and reference tracking on messages | new |
| 2.11 | `of.mandate_changed` (v1.1); `reason_class` (item 11); MAST sampling | backlog; RFC §5.3.5 |
| 2.12 | `mandate_functions` on positions | registry (e152e3d); the stakeholder map as data is still new |
| 2.14 | `mandate_exit` on positions | registry (e152e3d) |
| 2.15 | `of.inbound_queue` on ≥ 3 days; presence for the live-holder test | Queue events exist, with exact ages since f888177 (2026-11-06 12:53Z). Presence: new; v0 uses the registry |
| §3 | T and OE events per organization | app adapters |
| 2.16–2.22 | The loop projection of §4.1 | RFC 0005 §8 steps 1–3 |
| 2.25, 2.26 | `needs:` on systems and `owns:` on positions | RFC 0007 steps 1 and 3: the schema exists; each organization's system architect fills it |

### 4.1 What the loop rules read

The loop rules read one projection per work, `model["loops"][<work>]`, through `of_model`'s rule surface (`of_studio`). The derived metrics are RFC 0005 §5's, computed once by `of_studio` for both the loop card and the rules. A rule never recomputes them.

| Field | Meaning | Source | Owner | Read by |
|---|---|---|---|---|
| `work`, `owner`, `doers` | The work id, its owner (`work_owner`), the positions doing it | registry `works:` | `of_architect` (schema) | all |
| `target` | `metric`, `money`, `stake`, `better` (`higher` default, `lower`) | registry `works.<w>.loop.target` | `of_architect` | 2.17, 2.18, 2.21 |
| `measures[]` | `name`, `to_money` (empty means not priced) | `loop.measures` | `of_architect` | 2.21 |
| `ladder[]` | Cheapest first: `rung`, `cost`, `latency`, `accept`, **`production`** (true for shadow and higher), **`since`** (when the rung became available; default the loop's start) | `loop.ladder` | `of_architect`; `production` and `since` are new | 2.16, 2.17, 2.22 |
| `checkpoint_days`, `wip_arms`, `babysit_model` | Per work; defaults 3, 2, `sonnet` | `loop` | `of_architect` | 2.17, 2.19, 2.20 |
| `hypotheses[]` | `id`, `ts`, `status` and `status_at`, the last of `open`, `testing`, `kept`, `dropped`, `parked` | `of.loop_hypothesis` | `of_platform` | 2.16, 2.19 |
| `runs[]` | `id`, `rung`, `hypothesis` (may be empty), `metric`, **`value`** (a number in the metric's unit), **`accepted`** (the rung's `accept` met), **`queued_at`**, `started_at`, `ended_at`, `position`, `session`, `model` | `of.loop_run`, or derived from runner records (cbx) | `of_platform`; `value`, `accepted` and `queued_at` are new beside RFC §4.1's `result` text | 2.16–2.18, 2.21, 2.22 |
| `decisions[]` | `ts`, `kind` (`keep`, `drop`, `step_up`, `step_back`, `change_objective`), `hypothesis`, and for `change_objective` the new `metric` and its `to_money` | `of.loop_decision` | `of_platform` | 2.21 |
| `metrics.rungs.<rung>` | `runs`, `cycle_time`, **`wait_share`** | RFC §5 | `of_studio` | 2.22 |
| `metrics.wip` | Hypotheses in `testing` over time, as `[(ts, n)]` | RFC §5 | `of_studio` | 2.19 |
| `metrics.production` | `last_at` (the last run on a production rung), `days_since` | RFC §5 | `of_studio` | 2.17 |
| `metrics.babysit` | Per doer position: tool calls classed `runner` or `monitor`, by model | `of.mandate_check` with a new **`command_class`** (`runner`, `monitor`, `other`) and the event's `model` | `of_platform` (the class), `of_studio` (the count) | 2.20 |

`present()` in the run counts `registry.works.loop` as present when the projection holds a loop, and `of.loop_run` when a run ended in the window. A loop rule's `needs` name them, so until then it reports `blocked`.

## 5. Calibration protocol

1. **History.** Compute every rule on the organization's imported history and list which ones fire. A rule that fires on every position, or on none, has a wrong threshold.
2. **Lab.** `lab-drift-v0` injects labelled drift with negatives: a router owner, an over-coupled pair, a missing keeper, a lingering keeper. It reports precision and recall per rule. Thresholds are moved to the precision/recall point the owner chooses.
3. **Owner labels.** In-tenant labels (§1) update each rule's false-positive share and *p*. A threshold change bumps the rule version (`@1`), and both versions run side by side for one window.
4. **Implementation.** The rules run as projections over the model at *t* and the event rows (§6).

### 5.1 Where each rule stands (2026-11-07)

Since 2026-11-07 one position owns each rule end to end: the entry here, the detection rule in `bin/of_diagnose_rules.py` with its tests in `tests/test_diagnose_rules.py`, its fixtures with `of_lab`, its drift-harness result and its owner labels (`of_field-analyst`; the OF team's decision of 2026-11-07). Every rule needs a drift-harness result by 2026-11-14:
- a synthetic positive and negative in `lab-drift-v0`;
- or, for a rule whose input is not recorded, the harness checks that the rule reports `blocked` and raises nothing.

The columns:
- **Live** is the run status on 2026-11-07 ~16:30Z (7-day window), for the first user organization (U1) and the OF team (OF).
- **Synthetic** is `lab-drift-v0` `fixtures.py` as of 2026-11-10 (first run 2026-11-07). Each negative is a benign look-alike, the calibration's false positive where there was one, and each was checked to fire once its guard is removed.
- **Labelled** is the owner's labels on the 2026-11-06T12:00Z card (`real_cases.py`).

| Rule | Live U1 | Live OF | Synthetic | Labelled | Gap |
|---|---|---|---|---|---|
| owner-router@0 | fired 1 | fired 1 | P/R 1.0 | 1 tp, fires | — |
| owner-relay@1 | fired 3 | quiet | P/R 1.0, both halves; negatives: a busy pair (6 of 80), a unit owner's own unit | 3 tp, fire | — |
| coordination-gap@1 | fired 4 | blocked (no work records, no `depends_on`) | P/R 1.0; reports blocked when a keeper is unobserved | 3 tp, fire | — |
| over-coupled@3 | blocked (`wr.position`); 2026-11-10 with PRs attributed: 26 under v1, 6 under v2 | blocked | P/R 1.0; negatives: one-directional, a unit owner | — | — |
| context-overload@0 | blocked (`of.correction`) | blocked | reports blocked (no `of.correction`) | — | not built |
| fragmented-ownership@1 | blocked (`of.deployment`, `wr.position`) | blocked | code P/R 1.0; negatives: keeper named inside the windows, one window only | — | runtime part not built (no `of.deployment`) |
| orphan-work@3 | fired 3 (cluster 1, keeperless 2) | quiet | cluster and unmapped P/R 1.0; negatives: a scratchpad, verdicts before rt-v4, 1 of 31 unmapped | 2 true_but_not_now, 1 wrong_remedy (keeperless on an unsupported system, fixed in v2) | keeperless moved to unkept-system (v3) |
| bottleneck-broker@1 | quiet | quiet | P/R 1.0; negatives: a coordinator hub, a mesh | — | — |
| missing-verifier@0 | blocked (`of.correction`, `wr.position`) | blocked | reports blocked (no `of.correction`) | — | not built |
| chatty@2 | fired 31 (pair 19, flood 5, nudges 7) | fired 2 (flood) | all parts P/R 1.0 (pair, organization, flood, nudges) | 2 tp (pair), fire | 19 pair issues to review |
| unclear-mandate@1 | quiet | quiet | P/R 1.0; negatives: schema adoption with reworded summaries, onboarding | — | — |
| function-gap@0 | fired 7 | fired 12 | P/R 1.0 (a function covered only by a parked position) | — | 12 on OF to review |
| churn@0 | quiet | blocked (no work records) | both parts P/R 1.0; negative: holder changes with no dip | — | — |
| lingering@0 | quiet | quiet | P/R 1.0 | — | — |
| inbound-backlog@0 | blocked (queues on 2 of 3 days) | blocked | both parts P/R 1.0 | — | — |
| loop-cheapest-rung@0 | blocked (no declared loop) | blocked | P/R 1.0 | — | the search week, once its runs are derived |
| loop-checkpoint@0 | blocked (no declared loop) | blocked | P/R 1.0 | — | the search week, once its runs are derived |
| loop-plateau@0 | blocked (no declared loop) | blocked | P/R 1.0 | — | the search week, once its runs are derived |
| loop-wip@0 | blocked (no declared loop) | blocked | P/R 1.0 | — | a real case |
| loop-babysit-model@0 | blocked (no declared loop) | blocked | P/R 1.0 | — | the search week, once its runs are derived |
| loop-objective-drift@0 | blocked (no declared loop) | blocked | P/R 1.0 | — | the search week, once its runs are derived |
| loop-eval-bottleneck@0 | blocked (no declared loop) | blocked | P/R 1.0 | — | the search week, once its runs are derived |
| unkept-system@0 | fired 1 (delivery), 2026-11-10 | quiet | both parts P/R 1.0; negatives: external keeper, unsupported, a supersystem (keeper and unit) | — | — |
| off-map-work@0 | fired 7, 2026-11-10 | quiet | P/R 1.0; negative: reads and shells | — | the 7 U1 issues to label with its structure keeper |
| cell-gap@0 | blocked (no `needs` yet) | blocked | all three parts P/R 1.0; reports blocked without cells | — | U1's cells |
| cell-overlap@0 | blocked (no `owns` yet) | blocked | P/R 1.0; reports blocked without cells | — | U1's cells |

The loop rules (2.16–2.22) were added and built on 2026-11-07, after the live run above. Their real cases wait for the loop events of the search week (§4.1), derived from the runner records.

`of.correction` is not recorded. Its likely source is the `correction` act of `oa-v1` (§7.2) once that is calibrated. Until then, context-overload and missing-verifier stay unbuilt.

## 6. Implementation v0

Agreed with `of_studio`, 2026-11-06. Split on 2026-11-07 (organic-framework commit 113dffc, hand-over e42c1acf):

- **Modules.**
  - The rules are `bin/of_diagnose_rules.py` (`of_field-analyst`), with their tests in `tests/test_diagnose_rules.py`. A rule reads the model and the rows only through `of_model.RULE_SURFACE`, the query surface `of_studio` keeps; a reading the surface lacks is added there by `of_studio` first.
  - The run is `bin/of_diagnose.py` (`of_studio`). It checks each rule's inputs, applies the owner's labels, routes issues to units, merges, ranks and escalates.
  - `diagnose(model, rows, a, b)` returns `{"issues": [...], "rules": [{rule, version, status: fired | quiet | blocked | not_built, missing: [...]}]}`;
  - rules are pure functions; issues are a projection; the only stored kind is the owner's label, `of.issue_labelled` (`of_platform`).
- **Blocked, not fired.** Each rule declares its inputs (`needs` in the YAML). If an input is empty in the window, the rule reports `blocked: no <input>` and raises nothing. The page lists blocked rules below the issues, so the owner sees what an import or a new event kind would unlock.
- **Attribution.** Rules that need authors run on attributed work records only, report the attributed share in their evidence, and are blocked below `attributed_share_min`. Those rules are:
  - fragmented ownership (code);
  - the interface ratio of over-coupled;
  - missing verifier;
  - chatty's pair and organization parts (from 2026-11-10, §2.10).

  Rules that need only systems per work record run on all non-bot work records. Attribution itself comes from the importer (transcript PR links, the `Position:` trailer), never from the rules.
- **Placement.** Critical issues go above "waiting on you", at the very top of the inbox. All others form the fourth inbox block, ranked by score.
- **Score in v0.** Without T events, ΔT_direct = 0 and *v_c* is unknown. So score = *p* · Δ*m_c* / *m_decide*: owner minutes saved per minute of deciding. Each rule returns its Δ*m_c* from its evidence (`delta_m` in the YAML), and the page shows the terms.
- **State in v0.** An issue is `open` while it fires. An owner label changes it:
  - `false_positive` hides it for that rule version;
  - `true_but_not_now` hides it for 7 days;
  - `wrong_remedy` keeps it, marked.

  `resolved` means quiet at both *b* and *b* − 7 days. `acknowledged` and `remedied` wait for the Fix stage (change-path events, RFC §5.6).
- **Tests** use framework fixtures. Organization data stays in its app; the app-side calibration script is the check on real history.

## 7. Owner-message measures

The rules above read the owner's messages through classifiers. This section defines what each kind of owner message is, as a measure in its own right. A message is what the owner typed or pasted into an agent's session:
- an `of.human_prompt` whose corrected origin is `human` or `paste` (`fm-v2`: `of_asks.apply_prompt_origins`);
- deduplicated on (minute, first 60 characters).

Cron fires, injected notes and relay deliveries are not the owner's messages.

### 7.1 Status poll — `sp-v1`

*Defined 2026-11-06, at `of_architect`'s request: a status poll is the system failing to show the owner what he wanted. It is what the Works screen (Studio, 2026-11-06T13:39Z) and the inbox exist to remove.*

**Definition.** A status poll is an owner message whose main act is to ask for the state of work in progress, or of the agent doing it. Examples:
- progress, or how things stand: «как тут дела?», «какой прогресс по …?»;
- what is left: «что у нас осталось?»;
- what happened while the owner was away: «как прошла ночь?», «что случилось, пока меня не было?»;
- whether a step is done: «ты уже смержил PR?», «как прошла миграция базы?»;
- when it will be ready: «когда мы будем мигрировать мон?», «сколько ждать очередь?»;
- what the agent waits for, or needs from the owner: «на что ты ждёшь моё да?», «какие ко мне вопросы?»;
- impatience with progress: «чего так долго?».

**What still counts as a poll:**
- An imperative whose only object is to report or gather that state: «расскажи, что случилось», «проанализируй сессии и скажи, какие ко мне вопросы», «узнай у сессии X, всё ли готово».
- A greeting, a return note («я вернулся со спортзала») or an acknowledgement («окей», «отлично») in the same message.

**What is not a poll:**

| Label | The message… | Example |
|---|---|---|
| `instruction_with_poll` | also directs new work, decides, or gives new information besides the poll. It is an instruction, and the poll is secondary | «надо теперь донастроить …, что у нас тут осталось», «делай следующий шаг, ты зачем меня ждёшь?» |
| `system_status` | asks for the state of a production system or a resource, not of work: alerts, limits, lost logs. A dashboard answers it, not the Works screen. Reported apart | «какой сейчас статус по лимитам?», «мы продолжаем терять логи или нет?» |
| `question` | asks about design, mechanism or facts | «что делает DEFENDER?», «как ты видишь орг-структуру …?» |

**The test for a question inside an instruction.** Remove the question. If what is left still asks the agent to do, decide or know something new, the message is an instruction. If only a greeting, an acknowledgement or a request to report is left, it is a poll.

**Unit.** Status polls per week (7 days), per organization and per position. Two figures:
- the count;
- the share of the owner's messages in the same scope.

The share is what moves when the owner's volume changes; the count is what the owner spends.

**Classifier.** `fa-2026-11-05`'s `status_poll` (a phrase list) is pinned for the founder-load baseline and stays as it is.
- On the hand-labelled week below it has precision 0.88 (38 of 43) and recall 0.75 (38 of 51).
- It misses polls without its phrases: «какой статус по правкам?», «что с ней?», «ты уже смержил?», «что в итоге осталось …», «что нас сейчас останавливает, чтобы применить PR?», «какой прогноз по миграциям?».
- It takes instructions that contain a poll phrase.

`sp-v1` is this definition. Its implementation (`of_studio`) must reproduce the hand labels within ±3 on the labelled week before it replaces `fa`'s count in the Works prediction. The hand labels are app material, kept as event ids with labels in each organization's own repository.

**Baseline.** The week before the Works screen, [2026-10-30T13:40Z, 2026-11-06T13:40Z), hand-labelled:

| Organization | Owner messages | Status polls | Share |
|---|---|---|---|
| The first user organization | 547 | 47 | 8.6% |
| The OF team | 178 | 4 | 2.2% |
| Both | 725 | 51 | 7.0% |

The labels come from a scan by wording of all the week's messages, plus two polls found in `of_studio`'s sample and one found by its implementation (90a4413), so 51 is a lower bound. Owner messages are counted after `of_platform`'s origin corrections of 2026-11-06 20:12Z, which turned 15 briefs and capacity notes into injected text, and without the brief and move-note templates (of_studio dc35391). `of model polls` reproduces the labels: 47 of 46 + 1 and 4 of 4 before that label was added, fitted on the same week, so this is a fit and not a test.
- The top position is the k8s keeper with 15. Next come the main bot's search agent (6), its operator (5) and the dispatcher (5).
- 4 polls came from sessions with no position.

### 7.2 What the owner does in a message — `oa-v1`

*Defined 2026-11-07, at `of_architect`'s request. Drawn from `of_studio`'s ad hoc breakdown of the k8s keeper's owner messages (instructions, answers to asks, approvals, pasted text, corrections) and its 45 sample ids.*

**What the breakdown mixed.** It put three different things on one axis:
- what the owner does in the message: instruct, approve;
- how the message arrived: pasted;
- what it answers: an ask.

A pasted message can be an approval or a relay. An answer to an ask can be an instruction. So `oa-v1` gives every owner message **one act** and two attributes.

**Acts.** When a message holds several acts, the first in this table wins:

| Act | The owner… | Examples |
|---|---|---|
| `relay` | passes on another agent's output or request: pasted, or retold. Most of the text is that agent's, not the owner's. Plumbing, read by §2.2 | «Сессия CVE попросила перевести два вотчера …»; a pasted «Нужны четыре «да»: …» |
| `correction` | says that the agent did, proposes or assumes something wrong, or challenges it. Read by §2.9 | «а зачем нам раннеры на столько гигабайт?», «не так», «исправь …» |
| `instruction` | directs new work, or changes the direction of work: a goal, a requirement, a plan, a routing order | «сделай прокси отказоустойчивым», «посоветуйся с сессией, которая …» |
| `decision` | chooses among options the agent offered, or answers its questions with content other than yes | «1. Окей 2. Ну это было бы хорошо сделать 3. размещаем в кластере» |
| `approval` | consents to an action the agent proposed, possibly with a condition, and adds no new work. An imperative that only carries out the proposal is part of the approval | «давай применять», «подтверждаю evm-arb», «Да, согласен. Запиши это решение» |
| `status_poll` | §7.1 | |
| `system_status` | §7.1 | |
| `question` | asks about design, mechanism or facts | «что такое липкость?», «а он реально работает?» |
| `information` | gives the agent facts it did not have: a person's feedback, an alert, a log | «От Вовы обратная связь: …»; a pasted alert |

**Attributes:**
- **`pasted`**: the corrected origin is `paste`, or the text holds a `<pasted_content>` block.
  - With `paste_source` (ps-v0) at ≥ 0.5, the text is another session's output, and the act is `relay`.
  - Without a source, it comes from outside this machine (a person, a person's agent, an alert), and the act is read from the owner's own words around it.
  - The hook misses some pastes typed as `human`, such as an agent's report copied in. Read them as pastes: agent prose in the third person, formal lists, «агент не нашёл …».
- **`answers_ask`**: the message closes an ask (`closed_by` in the ask lifecycle, or `reply_to`). It is not an act: an answer can be a decision, an approval, an instruction or a correction.

**Unit.** As in §7.1: per week, per organization and position, count and share of owner messages, for each act and attribute.

**Calibration.** The 45 sample messages, labelled by act, are kept in that organization's own repository, with `of_studio`'s sketch label beside each. Of the 45, 24 agree once `pasted_text` is read as relay or information. The rest move mainly in three ways:
- "approval" that adds new work becomes `instruction`;
- "answer" becomes `decision`, `instruction` or `relay` by its content;
- pastes that the hook missed become `relay`.

`oa-v1` is a definition with one labelled sample of one position, not a calibrated classifier. Before it is implemented, a second sample of at least 100 messages across positions is labelled.
