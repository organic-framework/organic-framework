# Diagnose: the anti-pattern catalogue v0

Status: **v0, proposal**, 2026-11-06, by `of_field-analyst` (mandate: phase 5, from `of_architect`). Source: RFC 0001 v3 §5.4 ([`rfc/0001-organic-framework-platform.md`](rfc/0001-organic-framework-platform.md)).
Inputs:
- the events of [`events.md`](events.md);
- the registry and systems map of [`registry.md`](registry.md);
- the owner measures of [`studio.md`](studio.md) §3.
Calibration ran on the history of the first user organization, from 2026-11-06; its report stays in that organization's own repository.
Framework changes are cited by their commit on the public `main`. A change made before 2026-11-11 is cited by its date and position: such changes entered the public history together, in its first commit (organic-framework 74cbd41).

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

**Owner labels.** The owner, or an analyst the owner delegates, labels issues. The run counts a label only from the owner, the organization's structure keeper or the position the issue routes to, never from a position the issue names (docs/studio.md, Labels over all time). A label is one event, `of.issue_labelled`, with:
- `issue_id`, `rule_version`;
- `label`: `true_positive`, `false_positive`, `true_but_not_now` or `wrong_remedy`;
- an optional note.

Labels are the in-tenant calibration set.

**Labels and versions.** A label belongs to a rule version.
- A version bump that changes **detection** starts with no labels: an earlier `false_positive` does not hide the new version's issue.
- A bump that changes only priors, remedies or deciders, that only adds parts, or that only narrows what fires (each issue it still raises has the same id and evidence as before) lists the earlier versions in `labels_carry_from` (YAML). Their labels still apply, so the owner is not asked twice about the same finding. Labels attach to issue ids, so a new part, with new ids, starts with none. A rule whose false-positive share over its last 20 labels exceeds 0.3 is demoted to "shown on request" until its threshold is revised.

**Who decides.** Each rule names its decider in the catalogue: `owner`, `structure_keeper`, `unit_owner`, `doer` or `work_owner` (§2.16), or `unit_owner_or_structure_keeper`. The last goes to the unit owner when the issue lies wholly inside one delegated unit (`route_to_unit`, the run's), and to the organization's structure keeper otherwise. A label judges whether the diagnosis is right; the remedy is a structure change, which still reaches the owner as a diff for his yes. Since 2026-11-15 these rules have this decider: function-gap (2.12), inbound-backlog (2.15), owner-router (2.1), coordination-gap (2.3), orphan-work's `cluster` part (2.7) and unclear-mandate (2.11). Their issues used to wait for the owner's label, 24 of them that day across two organizations, and no card about labels goes to him (`of_architect`, relay message `01a1dc76-ed6e`, on this position's proposal). chatty and over-coupled moved to the structure keeper on 2026-11-10 (§2.10, §2.4). Each move is a version bump that changes only the decider, so earlier labels carry over.

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

### 2.1 Owner as universal router — `ap.owner-router@1`

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

### 2.3 Coordination gap — `ap.coordination-gap@2`

*v1 (2026-11-06): an edge is judged unmet only when both keepers are observed (§1).*

- **Signal.** Socio-technical congruence (RFC §5.3.3), on observed keepers. A **required edge** between the keepers of *s* and *t* exists when either:
  - the systems map declares `depends_on`;
  - ≥ 3 WR in the window changed both *s* and *t* (logical coupling).

  An edge is **unmet** when its two positions exchanged no message, ask or artifact read in the window.
- **Rule.** A required edge that is unmet for 7 days while both keepers are observed. Severity rises with the co-changed WR count.
- **Default remedy.** An announce rule on the interface: the writer of a change to *s* that touches *t*'s interface notifies *t*'s keeper. Or move the seam so that one position owns both sides.
- **ΔT basis:** expected integration failures avoided. The incident rate per unmet edge is taken from the organization's history; until then use 0.05 per edge per week × the mean T loss of an incident. *p* = 0.5. *m_decide* = 3 min.

### 2.4 Over-coupled split — `ap.over-coupled@5`

*v1 (2026-11-06): waits count as recorded per pair, not organization-wide. Under v0, one day of declared states, from the first `of status` declarations on 2026-11-05, switched every pair to the mutual-wait branch. The rule then fell silent on the operator–hosts pair (207 messages, ratio 4.7, reciprocity 0.97).*
*v2 (2026-11-10): no pair with a unit owner (kind `owner`) or the structure keeper. When of import github began reading the `Position:` line (74% of the first user organization's PRs attributed, up from 26%), the rule ran there for the first time and fired on 26 pairs, 20 of them a unit owner with a position of its unit: the delegation working, not a split to merge, as §2.2 and §2.8 already treat them. v2 fires on 6, among them two positions of the main bot (253 messages, ratio 4.3) and the pair of the bot's operator and its hosts' keeper. Labels carry over: v2 only narrows what fires.*
*v3 (2026-11-10): decided by the structure keeper, as §2.10 and the structure-coverage family (§2.23–2.26), through `of_platform`'s daily keeper digest (`of_architect`'s decision; its remedies, a merge or a published interface, are structure diffs the owner approves). Labels carry over: only the decider changed.*
*v4 (2026-11-15), the relay-era recalibration (this position's mandate 5). From 2026-11-06 the relay records every `of send`, about six times the messages the transcripts showed, and the interface ratio of 3 had been set on transcript-era counts. In the first user organization, in the week to 2026-11-15, 8 pairs fire at 3, at ratios 3.1 to 5.3, and every one merged at least 29 PRs. The keeper's labels are too few to move the value: one, `true_but_not_now` on a project and the keeper of its own system at 4.1 (2026-11-12: "coupling with the keeper is the project's nature"). It bounds the ratio from above at 4.1, and no false-positive label bounds it from below. So the ratio stays 3. New is the floor that chatty@3 gave its pair part: a pair is measured only when it merged ≥ 3 PRs in the window (`pair_merged_prs_gte`), since below that its messages per merged PR measure something else. That is an inference from the chatty labels on the same measure (§2.10). It silences no pair that fires today; with mutual waits it would have raised pairs with no merged PR at ratios up to 16. In the OF team the rule raises one pair, `of_release` with `of_secrets-review` at 3.5, for its structure keeper to label.*

- **Signals:**
  - the **interface ratio**: msgs between *p* and *q* ÷ WR of *p* and *q*;
  - reciprocity: the smaller direction ÷ the larger;
  - mutual waits: `blocked_on: peer:q` on *p* and `peer:p` on *q* in the same window.
- **Rule.** All three hold:
  - interface ratio ≥ 3, over ≥ 3 merged PRs of the pair (v4);
  - reciprocity ≥ 0.5;
  - at least one mutual wait, or a reciprocity ≥ 0.8 if waits are not recorded for the pair.
    - Waits are **recorded for a pair** when both positions declared a state (`of.state_declared`) on ≥ `observed_days_gte` distinct days of the window.

  Pairs where one side is a coordinator of shared capacity go to §2.10 instead.
- **Default remedy.** Merge *p* and *q* if the combined working set fits one holder (§2.5 signals below their thresholds). Otherwise publish the interface.
- **ΔT basis:** waiting hours removed on *raise*/*sustain* positions × their T rate. *p* = 0.5. *m_decide* = 15 min.

### 2.5 Context overload — `ap.context-overload@1`

*Built 2026-11-14 (`of_field-analyst`). It was specified on `of.correction`, an agent's correction of itself (`events.md`, backlog item 9), which was never recorded. It reads the owner's corrections instead, `of.founder_correction` (`of_platform`'s judge, RFC 0006 §2.4).*

- **Signals:**
  - compactions per active day (`of.compaction`); a day is active when the position's sessions recorded a hook or import event on it;
  - rereads of the same artifact (not read yet);
  - forgotten-fact errors: the owner's corrections of the position within 2 hours after one of its compactions, either of kind `fact` or repeating an earlier correction of the same rule (`repeat_of`: he explains again what it held before). One correction counts per prompt (`prompt_id`), as in 2.9.
- **Rule.** Over 7 days:
  - compactions per active day ≥ max(4, 3 × the organization's median, over the positions active in the window);
  - and ≥ 2 forgotten-fact errors after compactions.
- **Decider:** the structure keeper. It sets both remedies: where the position's reading runs, in its brief, and a split, a structure change as for chatty@3.
- **Evidence:** the compactions, the active days and the rate, the organization's median and the floor it sets, and each correction with its kind, rule, `repeat_of` and the minutes since the compaction before it.
- **Default remedy.** The position keeps its context: no fresh holder and no cleared context (the founder, 2026-11-18, prompt `01a1eeff-880b`: "Context is a very important part of the agents' understanding of the system, and so context must not be wiped"; [`method.md`](method.md), "Sizing positions").
  - If the load is history-bound (one long-lived session whose working set is small): move its reading, searches and reviews to subagents and to other harnesses (RFC 0008), so fewer of its own turns fill the context.
  - A split at a seam, if the working set itself is too big (many systems, many inbound edges: see §2.8).
  - v1 (2026-11-23) changes nothing in what fires. It marks these remedies, which replaced a fresh holder under @0 (organic-framework PR 377), and keeps @0's labels (`labels_carry_from: [0]`; `of_review` on PR 377, message `01a1f3e1-fa47`).
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

### 2.7 Orphan work — `ap.orphan-work@5`

*v1 (2026-11-06): three parts, each with its own prior, decision time, remedies and decider.*
*v2 (2026-11-07, from the first user organization through `of_architect`): `keeperless` fires only on a system that is live and kept by nobody, by the registry fields `of_architect` added on 2026-11-07. Labels carry over: v2 only narrows what fires.*
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

- **Input rule** (2026-11-07, no version bump: it fixes the input, not the rule): `cluster` counts `unowned` verdicts only from mandate router `rt-v4` on (`of_studio`, 2026-11-07). `rt-v2` and `rt-v3` judged paths in linked worktrees outside the repository unowned: `<repo>-wt/<name>`, `<repo>-wt-<name>`, `.worktrees/<other name>`. Those false clusters were most of the first user organization's 7 cluster issues. `rt-v4` resolves a linked worktree through its `.git` file.
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

### 2.9 Missing verifier — `ap.missing-verifier@2`

*Built 2026-11-14 (`of_field-analyst`) on the owner's corrections, `of.founder_correction`, for the reason given in 2.5.*
*v1 (2026-11-14, this position's mandate 5): part `answers`, for a position whose results are its answers rather than merged work. v0 could only note such a position: it had nothing to divide by. `of_architect` chose the denominator: the owner's own prompts in the position's sessions (`of.human_prompt`, origin human). A prompt is recorded where it happens. "Answers that relay a conclusion" would need a classifier's label, itself a proxy. v0's rule is part `results`, unchanged, and its labels carry over.*
*v2 (2026-11-15): part `results` reads verdict comments as review evidence. Since that day each merged PR's work record carries the `Verdict: approve|changes` comments of its last 100, with the reviewer's `Position:` line (`verdicts`; `of_platform`, at this position's request after the PR 176 review). This framework's own pull requests are reviewed so, never by a GitHub review. Part `answers` is unchanged; its labels carry over.*

- **Signals:**
  - rework on a position's results: `reverts`/`fixes` relations on its WR (not recorded yet, `events.md` backlog item 10), and the owner's corrections of its results, those of kind `output` or `fact` (`correction_kinds`). Corrections of its method, scope or process are not rework on a result;
  - review evidence on the path: a merged change of the position's in the window with a review. That is a submitted review (`reviews` on its work record), or from v2 a verdict comment by another position (`verdicts`), whatever its verdict; a verdict with no `Position:` line counts too. A PR whose verdicts were not read (imported before 2026-11-15, until its re-import) in a repository in which no merged pull request of the window has a submitted review says nothing either way (PR 176 review).
- **Rule.** Over 7 days, with ≥ 3 corrections of the position's results, one part holds:

  | Part | The position | Condition |
  |---|---|---|
  | `results` | merged work in the window | (corrections + reverts) ÷ WR ≥ 0.2, and no review evidence on its path |
  | `answers` | merged no work in the window | corrections ÷ the owner's prompts in its sessions ≥ 0.05 (`answers_corrections_per_prompt_gte`). No review of an answer is recorded, so the part cannot read review evidence, and its summary says so. "Merged no work" is what the records show: a position whose repository is not imported, or whose PRs are not attributed, reads the same, and the summary says "no merged work of it is recorded". The part keys its issue on `answers:<position>`, so a label on it never decides the position's `results` issue (PR 179 review) |

  Part `answers` needs the work records too, to know that the position merged none. Its threshold is a hypothesis from the first week. It is set below the two positions v0 could only note (7% and 8%), so that their structure keepers label them, and those labels confirm or move it.
- **One correction per prompt.** A prompt the judge read twice counts once (`prompt_id`), as in Studio's repeat-correction rate (PR 176 review).
- **What it cannot tell yet is a note, not an issue.** A position with ≥ 3 corrections of its results and no merged work has nothing to divide by while the owner's prompts are not recorded (part `answers` blocked). A position none of whose merged changes can show a review cannot show the third condition. Local commits carry neither a review count nor verdicts, nor do PRs imported before organic-framework 11aee30; PRs imported before 2026-11-15 carry no verdicts until their re-import. When only some changes can show one, the issue counts only those and says how many.
- **Decider:** the structure keeper. A review step and a verifier position are structure changes, as for chatty@3.
- **Default remedy.** A verification step: a cross-model review before results go to the owner or to production. Or a verifier position for the class of result. For part `answers`: a check before an answer reaches the owner, in which a second model reads it against its sources and against where each reading in it was taken (AGENTS.md, a conclusion rests on readings taken where its event happens). Or a verifier position for the class of answers.
- **ΔT basis:** rework avoided, plus decisions made on wrong facts avoided. *p* = 0.6. *m_decide* = 5 min.

### 2.10 Chatty coordination — `ap.chatty@5`

*v1 (2026-11-07), at the founder's request through `of_architect` and `of_lab`: the receiver's side. v0 saw chatter only as messages per merged PR, from the sender's side. v1 adds two parts: a position whose turns are mostly started by typed-in text, and a waiter whose wait is pushed again and again by nudges. Built by `of_studio` (2026-11-07). The pair and organization part keep their issue ids, so owner labels on @0 still apply to them.*
*v2 (2026-11-10): decided by the structure keeper, not the owner, through `of_platform`'s daily keeper digest (`of_architect`'s decision). Its remedies, a published interface, a queue or a hand-over, are structure diffs the owner approves. With PRs attributed it raised 56 issues in the first user organization that would otherwise have reached its owner. Labels carry over: only the decider changed.*
*v3 (2026-11-14), the relay-era recalibration: the pair part measures a pair per merged PR only when the pair merged ≥ 3 PRs in the window (`pair_merged_prs_gte`). Its basis is the first user organization's chatty labels. On 2026-11-12 its structure keeper labelled 12 issues. Ten were pair issues, all false positives. By the keeper's notes, the work of each of those pairs is not merged code: the structure keeper's registry and brief commits in the organization's repository; compliance and legal registers and questions to counsel; a treasury keeper's wallet and signer policy; an auditor's findings handed to their owners by message. In the week to 2026-11-12 each of those pairs had at most one merged PR. The other two were `receiver_flood` issues on a unit owner and a keeper, each the single interface of its unit, labelled true but not now with the split deferred. v3 does not change that part, and both are still raised, snoozed by their label. The only true-positive pair labels are older: two from 2026-11-06, under chatty@0, each on a coordinator and a code position. In the week to 2026-11-12 those pairs merged 29 and 21 PRs, at 10.7 and 36.9 messages per PR, and v3 still raises both. So the threshold stays at 10 per PR on relay-era counts, and only the floor is new. Below the floor, v3 is also silent on a pair of code positions that exchanges many messages and merges nothing. So the rule's notes name every pair below the floor with its messages and merged PRs, and such a pair stays in sight (PR 176 review). On 2026-11-14 v3 takes the organization's open pair issues from 43 to 19. Labels carry over: v3 only narrows what fires.*
*v4 (2026-11-15), from the first user organization's labels of that day (its structure keeper, 37 issues). 13 pair issues between a unit owner and a position of its own unit were false positives: "the delegation's dispatch channel ... count decisions or leave owner-to-doer pairs inside a delegation out". v4 leaves such a pair out (`exclude_unit_owner_with_own_unit`, the unit followed through parents), and keeps a unit owner's pair with another unit's position. One `repeat_nudges` issue had of pulse's own notices as its waiter, and v4 leaves out a waiter no registry names. 13 pair and nudge issues with the runner-slot coordinator were `wrong_remedy`: "the remedy is a booking tool or queue, not a boundary change". So a pair or a wait with a position of kind `coordinator` carries the remedies under `coordinator`: a booking tool or queue, and no notices copied to it unless it must act. *p* becomes 0.91 from the labels (§5.2). Labels carry over: v4 only narrows what fires, and changes a remedy.*

*Counting rule (no version bump: it fixes the input, not the rule): only days that have ended and whose merged PRs are imported count, for the parts that read WR and for §2.13. Otherwise, today's missing PRs inflate msgs per WR.*

*Input rule (2026-11-10, no version bump): the organization part compares like with like. From 2026-11-06 the relay logs every `of send` (`of.agent_message_out` with source `of`), where earlier windows saw only messages found in transcripts (`import`, `hook`): in the first user organization about 3,300 a day against about 500, and the part fired "3.4 to 25.4 per PR". When the recorders that wrote at least 10% of each window's messages differ, the part is not compared and says so in the rule's notes. From 2026-11-13 both windows are relay-era. The pair part's threshold (10 per PR) was set on transcript-era counts; v3 recalibrated it on the first relay-era week, with labels.*

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
  | pair | one pair has ≥ 10 msgs per WR over ≥ 3 merged PRs (v3), typically booking or status traffic with a coordinator |
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

### 2.11 Unclear mandate — `ap.unclear-mandate@4`

*v1 (2026-11-06): only substantive changes count, and a position settles first.*
*v2 (2026-11-15): an organization-wide brief edit is no position's mandate change. In the first user organization's week to 2026-11-15 the rule raised 33 issues from 203 brief revisions, with no summary changed. Most were rules pointed from many briefs at once: from 15 to 55 briefs in one commit, such as the rule against time estimates or the rule on readings taken where the event happens. A brief edit made on ≥ 5 positions within an hour (`org_wide_briefs_gte`, `org_wide_within_min`) no longer counts. The same snapshot's other fields, such as a moved `done_when`, still do. On the same week the rule raises 9 issues; the OF team's 1 stays. There are no labels yet: the 10 go to their deciders. Labels carry from v1: v2 only narrows what fires.*
*v3 (2026-11-15): no brief revision counts. `of_architect`, the decider, judged the OF team's remaining issue (`2ff510d619de`) a false positive (relay message `01a1dc76-ed6e`): of_conductor's three edits in the window added the founder's corrections as rules, and its summary, contribution, exit and ownership did not change. A brief gains rules while the mandate stays, and a revision hash cannot tell the two apart. So v3 reads only the fields that state the mandate: `throughput`, `mandate_exit.done_when` and a reopened status (`summary` alone stays out, as v1 found). On the same week it raises 2 issues in the first user organization, each a done_when moved twice, and none in the OF team. It supersedes v2's partial fix. Labels carry from v1 and v2: v3 only narrows what fires.*
- *The v0 issue.* v0 fired on 4 positions, from the registry build-out of 2026-11-05/06.
  - The schema gained `mandate_exit` and `mandate_functions`, so every position "changed".
  - Summaries were reworded ("prod" → "production").

- **Signals:**
  - `of.mandate_changed` events with a **substantive** change:
    - `throughput` changes;
    - the brief (`mandate`) changed up to v2 (from v2 not when the edit reached ≥ 5 positions' briefs within an hour); from v3 a brief revision does not count;
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

### 2.12 Management-function gap — `ap.function-gap@2`

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

### 2.15 Inbound backlog — `ap.inbound-backlog@3`

*v3 (2026-11-22): a parked position whose successor has a live holder gets part `successor`, of severity `medium`, in place of `unattended`. A user organization's structure keeper reported that one backlog reached its owner three times as a delegated unit's escalation (`issue.severity >= high`) on two parked positions, each with an active, held successor. One queue held its own successor's messages, sent by name as docs/guard.md R11 allowed until `of_server`'s change of 2026-11-22 (organic-framework PR 519) refused them. The keeper asked that neither reaches the owner and that the successor or its unit owner acts (the keeper's message `01a2034b-0114`, relayed by `of_platform` in `01a20350-75b9`). A successor cannot read another position's queue: its unit owner or the structure keeper moves the queue to it (`of inbox move`), and that needs no decision of the owner's. The keeper also asked that an empty queue not escalate. The rule cannot tell one: the relay records depth 0 both when a queue is emptied and when nobody presents the position any more, because its holder's session ended (`of_review` on organic-framework PR 518). The change adds a part and narrows `unattended` by it, so earlier labels carry over.*

*v2 (2026-11-15): a queue kept under a position's older name (registry `aliases`) is that position's, as the relay delivers it. Queues of one position under several names add up, day by day, under the position's own issue, and the evidence names the aliases. The first user organization's structure keeper labelled two `unattended` issues false positive that day: two aliases of one keeper read as "not in the registry".*

*v0 (2026-11-06), defined on the relay's `of.inbound_queue` events (`of_studio`, 2026-11-06). Queue data starts on 2026-11-06 at 12:47Z, so the rule is blocked until queues are observed on 3 days, and its thresholds are uncalibrated.*

A position receives more than it takes: peer messages wait in its queue on the server, and their senders wait with them.

- **Signals** (*inbound queues*, `of_measures.inbound_queues`, per position and day):
  - the time-weighted average depth;
  - backlog minutes: minutes when the oldest waiting message was older than 30 min (`of pulse`'s stall threshold);
  - whether the position has a live holder. In v0 this comes from the registry: status `active` and a holder session, or a person. Presence is not in the model.

  Until 2026-11-06 12:53Z (`of_platform`) the relay recorded only a change of depth, so earlier ages are upper bounds. From then on it also records a change of the oldest message at the same depth, and ages are exact.
- **Rule.** Over 7 days, the queue was observed on ≥ 3 days, and one of two parts holds.

  A day counts as observed when the organization had any queue change that day. A day with no messages does not count, so the unblock can slip by quiet days. This can only delay the rule, never make it fire. A relay heartbeat is not needed for v0.

  - **Overload.** The position has a live holder, has backlog ≥ 60 min on ≥ 3 days, and has an average depth ≥ 2 over the window.
  - **Unattended.** The position has no live holder (parked, finished, or no session), and messages for it waited ≥ 60 min on ≥ 2 days. Senders write to a position nobody holds.
  - **Successor** (v3). The same, for a parked position whose `successor` (registry) has a live holder. Its unit owner or the structure keeper moves the queue to that successor. A queue's last recorded depth does not say whether messages still wait: the relay records 0 also when nobody presents the position any more.

  Exclusions and overlaps:
  - positions of kind `owner` are excluded: the owner's inbound load is §2.1's case;
  - when §2.8 fires on the same position, the backlog is attached to that issue as evidence, and no second issue is raised.
- **Default remedy.**
  - *Overload:*
    - a deputy, or a published interface for the most frequent request (as in §2.8);
    - for a unit owner, keepers decide routine within their grants instead of asking (the delegation's `escalate_when`).
  - *Unattended:* route the senders to the successor or to the unit owner, or give the position a holder.
  - *Successor:* the unit owner or the structure keeper moves the queue to the successor (`of inbox move`); a stale message is dropped (`of inbox drop`).
- **Severity.** Overload is `medium`. Unattended is `high`, because work waits on nobody. Successor is `medium`: the unit owner or the structure keeper moves the queue to a named, held successor with its own command, so a delegated unit's `issue.severity >= high` does not take it to the owner.
- **ΔT basis:** sender waiting hours removed: the backlog minutes × the senders waiting, on *raise*/*sustain* positions. Owner minutes are 0 unless the owner relays around the queue (§2.2). *p* = 0.5. *m_decide* = 5 min.

### 2.16–2.22 and 2.28 Development-loop rules (RFC 0005 §6)

*v0 (2026-11-07), built: `bin/of_diagnose_rules.py`, on `of_studio`'s projection (`of_model.loops`) and `of_platform`'s loop events, both of that day.* [RFC 0005](rfc/0005-development-loops.md) makes a work's development loop an object:
- a target in money;
- the measures that map each rung's metric to money;
- an evaluation ladder, cheapest rung first;
- a hypothesis register and a decision log.

These rules watch it: seven from 2026-11-07, and 2.28 from 2026-11-14. Each is per work, with `affected: [<work>]`.

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
| 2.18 | `ap.loop-plateau@2` | 3 consecutive runs on the target metric each improve the best earlier result by < 1% of it | work_owner | medium |
| 2.19 | `ap.loop-wip@1` | More than its limit of hypotheses in `testing` in one group for ≥ 12 h: a cell when the loop declares `cells`, a rung when `wip_arms` is a map by rung, else the work | doer | low |
| 2.20 | `ap.loop-babysit-model@0` | ≥ 100 runner commands and monitors by the loop's doers on a model ranked above `babysit_model` (default `sonnet`) | doer, with `of_capacity` | low |
| 2.21 | `ap.loop-objective-drift@2` | `unpriced`: ≥ 3 runs, and ≥ 30% of the loop's runs, measure a metric that is neither the target's nor a measure with `to_money`. `untied`: the same on a target the owner decided (`decided`), where a measure with `to_target` is tied too, or any change of objective recorded there. `context`: a run, a hypothesis or a change of objective names a part outside the target's segment | work_owner | medium |
| 2.22 | `ap.loop-eval-bottleneck@1` | On one rung with ≥ 3 runs that carry a queue time, the wait share (queued to started, over queued to ended) is > 40% | work_owner | medium |
| 2.28 | `ap.loop-observed-elsewhere@1` | `decision`: a keep or a drop rests only on runs that decide nothing: on a proxy not shown to hold (`held` is `not_held` or `unknown`), or, from v1, unlocated. `accepted`: such a run carries `accepted`. One is enough | doer | `decision` medium, `accepted` low |

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
  - A cycle is a run whose metric is the target's, read where its event happens. From v1 (2026-11-14) a run on a proxy not shown to hold (2.28) is no cycle: it decides nothing, and the loop card keeps it out of the curve and the plateau (§4.1). From v2 (2026-11-15) neither is an unlocated run.
  - The gain is its value over the best earlier value, relative to that best. The direction comes from `target.better` (`higher`, the default, or `lower`).
  - Remedy: a step back (double loop). The owner asks a second position for a reframe review: by default the position the unit owner names; for the OF team, `of_architect` (RFC §9 q2).
  - The first user organization's case: the day pass stayed at the same value from 11-05 to 11-07.
- **2.19 WIP limit.**
  - Arms in flight are hypotheses whose last status is `testing`.
  - From v1 (2026-11-23) they are counted per group, as `wip_arms` counts them (§4.1, `metrics.wip_groups`): per cell when the loop declares `cells`, and per rung when `wip_arms` is a map by rung, a hypothesis's rung being that of its latest run, else the one its cost names, else the ladder's first. Each group stands against its own limit; a rung the map does not name has none. A loop with a whole number and no cells is one group, the work, as in v0, with the same evidence. Basis: `of_architect`'s hand-over `c4ef8758`, after a user organization's six hypotheses in testing over four cells, none above its limit of three, were flagged as six against three (`of_review` on organic-framework PR 577).
  - Evidence: `hours_over` and `limit` of the group longest over, the work's hypotheses in testing, and with cells or a map, `groups`: each group over, with its `cell`, `rung`, `limit`, `hours_over` and `most`, the most it held while over.
  - Nudge: "Finish or park one arm" (with groups, in the cell or rung named).
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
  - **Part `untied`: the owner's measure** (v2, 2026-11-14; hand-over 95bf514e from `of_architect`, basis: [`registry.md`](registry.md), Works, "The owner's measure", and `of_architect`'s decision on `of_conductor`'s message `01a1d95e-4a81`). A loop target may carry `decided`, the yes of the person who owns the work's goal on that measure. The measure itself is then what the work's changes are for, and its money is a reading. Goodhart's warning is against a proxy an agent chose; when the owner chose the measure, the owner carries that risk.
    - Only on a target whose `decided` resolves (below). A loop without it is judged as before, part `unpriced`: there a `to_target` chain prices nothing, and a change of objective needs its money.
    - A run is tied when its metric is the target's, or a measure with `to_target` or `to_money`. The counts are part `unpriced`'s: ≥ 3 untied runs and ≥ 30% of the loop's runs in the window.
    - Any `change_objective` decision on such a loop fires at once, whether it names a yes, money or neither. The measure changes only in the registry: on the owner's yes the structure keeper rewrites the target and its `decided`, a doer records no change of objective there, and `of loop decision` refuses one ([`registry.md`](registry.md), Works, "The measure changes only by the owner's yes, in the registry", from the organic-framework PR 168 review). So such an event was written past that check, and the summary says where the measure changes.
    - Nudge (RFC 0005 §6): "Tie the metric to the target, or get the owner's yes on a new measure." It never asks for a price, and nothing in the issue speaks of money. The plateau rule (2.18) still asks the owner about their own measure.
    - Remedies: tie the metric to the target (`measures.to_target`) or return to the target metric; get the owner's yes on a new measure, which the structure keeper writes into the target and its `decided` in the registry.
    - The evidence names `decided` and counts `untied` runs and `change_objective_on_owners_measure`, in place of `unpriced` and `change_objective_without_money`.
    - Part `context` is unchanged on such a target and still wins the part; its evidence then carries `untied`. The issue id is the work's in every part, so a loop that the owner's yes moves from `unpriced` to `untied` keeps its issue.
    - `decided` counts only when it resolves in the log as a person's yes: an ask filed for the work with its yes option, which they chose, or their own prompt in the session that holds their conductor ([`registry.md`](registry.md), Works, "Where it is read, and how it is checked", from the organic-framework PR 168 review). The rule does not look it up: it reads the projection (§4.1), which gives `target.decided` only for a resolved id, through `of_platform`'s resolver. An id that does not resolve leaves the loop one without `decided`, so it is judged as part `unpriced`, and the issue names the id and why: `decided_unresolved` and `decided_unresolved_why` (`not_found`, `unread`, `not_looked_up`) in the evidence, and the reason in the summary.
  - **Part `context`** (v1, 2026-11-12; hand-over 9e2140b6 from `of_architect`, basis: `of_conductor`'s relay message `01a1cf7d-c056` quoting the founder's correction of that day, "parts outside the segment are reported as context, never a target or a lever"). A loop's `target.segment` names the part of the chain the organization controls, and its `context` lists the parts outside it ([`registry.md`](registry.md), Works).
    - Fires when, in the window, a run's metric, a live hypothesis's `predict` or `change`, or a `change_objective` decision's metric matches a context part. A hypothesis counts while it is `open` or `testing`, or when it was recorded in the window. One is enough.
    - A context part is a text or `{name, what}`. Matching is [`registry.md`](registry.md)'s ("How a check matches a context part"), with `of registry check`'s own matcher (`context_matches` in `bin/of_loop.py`, `of_platform`), read through `of_model`'s rule surface, so the check and the rule agree:
      - a metric that is a name (`[a-z][a-z0-9_]{0,63}`) matches only a part with the same `name`: `rival_ordering` matches `{name: rival_ordering, what: …}`, and `rival_ordering_rank` or `network_after_send` match nothing;
      - prose (a hypothesis's `predict` or `change`, a metric written as prose) matches when every content word of the part's text, or of its `what`, is among the field's words. Case and punctuation are ignored, and so are short function words ("the", "after", "of") and one-letter pieces (the "s" of "party's"); a plural counts as its singular ("rules" as "rule"). "The bundle leaves 3 ms after our node sees the block" matches neither example part.
    - **Where the word match stops.** A part whose words also describe work inside the segment matches that work. With RFC 0005's part "rivals' routes", the hypothesis "pick routes that beat rivals' routes", which is about the search inside the segment, fires. Write a part's text with the words that set it apart from the organization's own work, and give a part that runs or measures can carry a `name`. A small model judging the match, as `of fit` does, may replace the word match ([`registry.md`](registry.md)).
    - Nudge (RFC 0005 §6): "`<The part>` is outside the segment: report it, and act inside."
    - Remedies: report the part beside the target and move the work inside the segment, onto a time or a rule between its start and end; restate a hypothesis about the part to the owner as an inference, never a finding.
    - One issue per work, as before; when both parts fire, `context` names it and the unpriced runs stay in its evidence. Issue ids do not change.
    - Without a declared segment the part is quiet. On 2026-11-12 no loop of either organization declares one (`of registry check` warns on each).
- **2.22 Evaluation bottleneck.**
  - The wait share is per rung: (started − queued) summed over (ended − queued) summed, over the rung's runs that ended in the window.
  - Remedy: elevate the rung (more runners for it), or move hypotheses to a cheaper rung. It goes to the owner, since it costs capacity.
  - v0 reads `of_studio`'s wait share as the projection gives it, over all the rung's runs so far, and fires only for a loop that ran in the window. A week-windowed share waits for the projection to give one.
  - v1 (2026-11-23): the projection reads the share only from runs with a queue time (`queued_at`), and gives `wait_runs`, the runs it is read from (§4.1). The gate of 3 runs counts those, not all the rung's runs, so a share read from one or two runs raises nothing. A rung whose runs carry no queue time reads not observed and raises nothing. Basis: `of_architect`'s hand-over `0985ab73`, after a user organization's loop, whose runs carried no queue time, showed 82 to 94% waiting: the analysis, reviews and pull requests between its runs (`of_studio`, message `01a205ca-7cbf`).
  - The first user organization's case: 76 of 92 relayed messages to the doer were about runner slots, and 58 of 118 hours were gaps.
- **2.28 Observed elsewhere** (v0, 2026-11-14; hand-over e640ea57 from `of_architect`, basis: the founder's prompt `01a1da18-f250` of 2026-11-14, "The cause must be found and this behaviour fixed systemically"; spec: [`registry.md`](registry.md), Works, "Where a reading is taken", and RFC 0005 §4.3 and §6). A reading taken anywhere else than where its event happens is a proxy. Its measure, or the run itself, names `proxy_for`: where the event happens (`at`) and the condition under which the reading equals the one there (`valid_when`). The run records whether that condition held in its data (`held`, `not_held`, `unknown`). A proxy not shown to hold, whose `held` is `not_held` or `unknown`, decides nothing. `of loop` refuses a keep or a drop that rests only on such runs, and writes no `accepted` on them. The rule finds what got past that check: decisions written before it existed, runs synced from a runner, and events written by other tools.
  - **v1 (2026-11-15; hand-over 7b8db7d4 from `of_architect`; spec: [`registry.md`](registry.md), Works, "A run names where its reading was taken" and "An unlocated run decides nothing").** An unlocated run decides nothing too. Such a run names no reading point: its metric is neither the target's nor a declared measure's that names one, and it states none. It counts as a proxy not shown to hold, with `held` read as `not located`. So a loop whose runs name no reading point is judged instead of reporting blocked. `of loop locate` places a run later, and the run counts as located only from that time: a decision made before it is judged on what the run held when recorded (`as_recorded`, `located_at`, §4.1). The basis is this position's run of the loop rules on the first user organization's 18 loops (2026-11-15). On three of them, the founder's case of 2026-11-14 included, no run named a declared measure, so nothing could be refused.
  - **Part `decision`.** A `keep` or `drop` decision in the window on a hypothesis whose every run before the decision decides nothing, as known at the decision's time. A hypothesis with no run is not this rule's case. One run on the data where the condition held (`held`), or one run taken where its event happens (no proxy), is enough for the decision to stand.
  - **Part `accepted`.** A run that ended in the window and decided nothing when it was recorded carries `accepted`, true or false. A rung's accept is never judged on it.
  - One hit fires. One issue per work. When both parts fire, `decision` names it and the accepted runs stay in its evidence.
  - A measure read where its event happens whose chain (`to_money`, `to_target`) rests on a reading elsewhere carries `chain_proxy_for`, not `proxy_for` ([`registry.md`](registry.md), Works, "A direct reading with a proxy chain"). Its runs are direct readings, and the rule leaves them alone. A price or a ranking taken from that chain is an inference, which the money line's label shows.
  - Nudge (RFC 0005 §6), after what fired: "`<metric>` was read at `<observed_at>`, and the event happens at `<at>`: report it as not observed there, or run it on the data where `<valid_when>` held." For an unlocated run: "nobody named where `<metric>` was read: name it with `of loop locate <work> --run <id> --observed-at <where>` and `--direct` or `--proxy-for`, or report it as not located."
  - Remedies. For `decision`: reopen the hypothesis and report its reading as not observed where the event happens, labelled as a proxy; run it where the event happens, or on the data where the condition held, before a keep or a drop. For `accepted`: record no accept on a proxy not shown to hold, and rerun it on the data where the condition held, which counts as taken there.
  - The evidence names `observed_at`, `proxy_for` (the proxy's `for`), `valid_when` and `held` of the last such run, the `decisions` with their runs, and the `accepted_runs`.
  - **Inputs.** It reads `runs[].proxy` and `runs[].observed_at` (§4.1): `of_platform`'s run fields from 2026-11-14 (hand-over 52b9d314), and `of_studio`'s projection of them, which takes both from the run's measure when the event names none, with `held` then `unknown`. A measure that the structure keeper marks with `proxy_for` therefore makes its earlier runs proxies of unknown condition. Up to v0, while no run of a declared loop said where it was read, the rule reported `blocked: of.loop_run.observed_at`. From v1 it reads an unlocated run as one that decides nothing, and it also reads `runs[].located_at` and `runs[].as_recorded`.
  - The first user organization's case: on 2026-11-14 an analysis took the time a rival's transaction reached the organization's own nodes as the time it reached the block's builder, and that reading had already carried two decisions that day. On 2026-11-15, v1 raises its loop. Eight keeps and drops in the week rest only on runs that nobody located: their metrics are prose that names no declared measure, although two of its measures carry `proxy_for` at the block's builder.

**Calibration.** The thresholds are RFC 0005 §6's, unconfirmed. Each rule gets synthetic fixtures in `lab-drift-v0` (a positive and a negative), and the search week as its first real case. A rule that fires on every loop, or on none, has a wrong threshold (§5).

### 2.29 Stalled loop — `ap.loop-stalled@0`

*v0 (2026-11-16), built: `bin/of_diagnose_rules.py` (`loop_stall`, `rule_loop_stalled`). Basis: the founder's prompt `01a1e116-e3de` of 2026-11-15, in English: "Well, not even 2 days; one should understand much more often what the problem is. Two days is also long." He answered `of_conductor`'s proposal of a rule at 2 days without a run; `of_conductor` relayed the ask in message `01a1e117-3301` with the hours below, which are its proposal, unconfirmed.* A user organization's bidding loop ran all its runs on 11-10 and 11-11. For four and a half days after that, it had no run and only bookkeeping decisions, and a report line every two hours saying that no rung had moved. Nobody caught it: not OF, not the conductor, not the work's owner.

**Fires when** a loop has at least one hypothesis in `testing`, and for `stall_hours_gte` (4) hours it has shown no progress and no stated wait. Its work must not be `done` or `parked`. One issue per work, with `affected: [<work>]`.
- **Progress** is a run that ended or a decision, and nothing else. A report, a status note, `of loop show` or an owner's check that finds the loop unchanged never counts. A keep, a drop or a park that leaves the hypothesis where its last decision put it, with no run in between, changes nothing and does not count either. On the bidding loop, a hypothesis parked on 11-10 was parked again on 11-12.
- **A stated wait** (`of.loop_wait`: `on`, `ref`, `expect_at`; schema in [`registry.md`](registry.md), Works, "A stated wait", `of_architect`, 2026-11-16) declared after the last progress holds the clock until its `expect_at`. `on` is one of `runner`, `window` (such as a discovery window), `release` (a release slot), `holdout` (held-out data that does not exist yet), `ask` (an owner's card, `ref` its id), `position` (`ref` its name) or `calendar`. `of loop wait` requires `--until`, and `ref` on every kind but `calendar`. An event with no `expect_at` can come only from another tool, and it holds nothing past its own time.
- **Of several stated waits, the earliest current promise holds the clock.** A wait that ended before the latest wait was stated is answered by that statement. Of the others, the one that ends first decides, so one long wait cannot hide a short one that slipped. On 2026-11-16 a user organization's structure keeper asked for this through `of_platform` (message `01a1e319-de65`): its speed loop had stated a release wait until 13:00Z beside later ones, and the latest end held the clock. A wait that ends long after it is stated is still told to the owner when it is stated (below).
- **A wait on an `ask` also ends when that ask is answered or withdrawn**, if that comes before its `expect_at`. The clock then counts again from the answer (`of_architect`'s decision, message `01a1e12a-e502`). The projection gives that end as the wait's `ended_at` (§4.1).
- **A wait on the calendar holds nothing.** These are the kinds in `calendar_kinds`: `calendar` (a date the work's design fixes, such as a locked week or a test of 14 days) and `holdout`. With nothing else in flight, the work has no next step, and that is the finding. When something else is in flight, its runs and its other waits hold the clock themselves. A held-out gate booked with another position is a wait on that `position`, and it holds.
- **A wait re-declared on the same thing** (the same `on` and `ref`) with no run or decision in between counts once. Two checks of an unchanged loop are no progress. A re-declaration with an earlier end replaces the counted one, so a wait can be shortened, never lengthened. On 2026-11-16 a user organization's keeper moved a runner slot two days earlier; the restated wait on the same runner counted for nothing, and the first, later end kept holding the clock (`of_platform`, message `01a1e356-c25c`). A wait that ends early needs no withdrawal either: the next run or decision ends it.

**Parts.**
- `silent`: nothing was stated after the last progress.
- `wait_over`: the stated wait's `expect_at` passed.
- `calendar`: the only wait stated is on the calendar.

**Levels and delivery.** `loop_stall(card, now, thresholds)` is the one clock, and it returns the level:
- `owner` from `stall_hours_gte` (4 h). The pulse (`of_platform`) tells the work's owner and doers.
- `conductor` from `escalate_hours_gte` (8 h). The pulse types it into the organization's conductor's pane, and the note tells the conductor to tell the founder. `of_conductor` had asked for "the founder's digest" too, but OF has none: the conductor is the founder's window (`of_platform`, message `01a1e12c-f3ad`).

The pulse calls this function every 5 minutes with the catalogue's thresholds, and it keys an episode by the returned `since`, the time the clock started, so each level is told once per episode. A wait whose `expect_at` lies more than `escalate_hours_gte` after it was stated reaches the work's owner once, when it is stated, through the pulse. Otherwise a long wait would hold the clock unseen and hide the stall the rule exists to show. It is not a cap: the wait holds as stated ([`registry.md`](registry.md), Works, "A stated wait"). Studio (`of_studio`) shows the same clock per work through `loop_clock(card, now, thresholds)`, which gives the reading at any time (asked by `of_studio`, message `01a1e130-0a14`). While a stated wait holds the clock, part is `held`, `since` is the wait's end and `hours` is negative, the hours left. Below `stall_hours_gte`, `level` is null. `loop_stall` is `loop_clock` from that threshold on. The daily diagnosis lists the issue for the work owner, as for every loop rule. The pulse is what makes the rule live within hours.

**Nudge** (`stall_text`, one line for the issue, the pulse and Studio), for example: "`<work>`: no run, decision or stated wait for 21.4 h (last: run `<id>` at 2026-11-15 00:07Z); in testing: `<ids>`: run it, decide it, or state what it waits on and by when (`of loop wait <work> --on <what> --until <time>`)". The last two parts add that the stated wait passed, or that the work waits only on the calendar.

**Remedies.**
- `silent`: run the hypothesis in testing, decide it, or state the wait.
- `wait_over`: run, decide, or state the new wait and why the first one slipped.
- `calendar`: start a hypothesis on a cheaper rung meanwhile, or have the owner park the work until the date.

**Inputs.** The rule reads `hypotheses[].status`, `runs[].ended_at` and `decisions[]` (the card's `decisions_all`, §4.1), and two fields the projection does not carry yet: `waits[]` and `state` (§4.1, both asked of `of_studio`). It also needs `of loop wait`, the event `of.loop_wait`, asked of `of_platform`, with its schema for `of_architect`. Until those land, nothing can hold the clock: the rule notes this per issue, and a loop that legitimately waits still shows as stalled.

**The first real case** (replay every 30 minutes over 2026-11-08..15 on the first user organization's loops, at 4 h and 8 h, with no waits stated, since none could be): 18 episodes on 5 works, 10 of which reached 8 h.
- The bidding loop had 5 episodes, 3 of which reached 8 h; the longest was 61 h. The rule would first have fired at 11:53Z on 11-11, four hours after its last run.
- Another loop had 8 episodes, 3 of which reached 8 h.
- A third has stood for 21 h, at 2026-11-15 21:26Z, with three hypotheses in testing.
- The OF team records no loop events.

**Calibration.** The lab gets a positive and a negative fixture for each part. The hours are the conductor's numbers until the owner's labels move them (§5).

### 2.23–2.26 Structure coverage

*v0 (2026-11-10), built. Decided by `of_architect` the same day (relay messages 01a1c62b and 01a1c62e), after the founder's root-cause review of why the first user organization's structure gaps raised no issue (`of_platform`): every rule until then diagnosed behaviour in a 7-day window against the registry, and none checked the registry's own coverage. A gap was seen only when work happened to pass through it.*

The decision:
- **The catalogue reads structure as well as behaviour.** §2.12 already reads the registry. Studio already shows the flags below; what was missing was the issue, so nobody was asked.
- **One computation.** These rules read Studio's own coverage (`of_model.coverage`, [`studio.md`](studio.md) §3.4) and cells (`of_model.cells_of`, [RFC 0007](rfc/0007-system-architect-and-cells.md) §2.3), never a second computation, so Studio and the issue never disagree.
- **Routing.** Every issue of the family goes to the organization's structure keeper, who brings a diff. Never straight to the owner: the owner approves the diff.
- **Systems kept outside stand as decided.** `governed_by: external:<who>` counts as kept (§2.7 v2). RFC 0007 §2.2 now makes such a fact count only with the owner's ask id under `confirmed:`, and `of registry check` warns on it, so a false claim is caught there.

**2.23 A live system nobody keeps — `ap.unkept-system@2`.** Two parts. *v1 (2026-11-15): in part `unit`, a subsystem with no unit of its own lies in its nearest parent's unit. The first user organization's structure keeper labelled 6 issues false positive the same day: "A subsystem is in its parent's unit ... The rule should follow parent."*

| Part | Fires when | Remedy |
|---|---|---|
| `keeper` | Studio flags the live system `no_keeper`, `keeper_unknown` or `keeper_<status>` (a parked or finished keeper), with or without work. Kept, as Studio computes it: a keeper, `governed_by: external:<who>`, a lifecycle other than `live`, or a supersystem whose every part is kept | Name a keeper, a new keeper position, an external governor with the owner's ask id, or a lifecycle if it no longer runs |
| `unit` | In an organization with units, a live system in no unit, not kept outside, and not a supersystem whose parts are all in units | Put it in the unit whose owner answers for it, or a new unit |

- Studio's `orphan` flag (no position touches the system) is evidence on the `keeper` part (`untouched`), not an issue. Alone, it marks only a system kept outside that none of the organization's positions works on, which is by design.
- The evidence carries the system's merged PRs in the window, its writers and its unit.
- *p* = 0.6 (`keeper`), 0.5 (`unit`); *m_decide* = 5 and 3 min. Severity medium and low.
- **Calibration** (2026-11-10): in the first user organization it fires once, on a delivery system, live and kept by nobody with 4 merged PRs (under §2.7 v2's 5). Three other systems are kept outside, and a fourth is the supersystem of systems that are all kept and all in units. On the OF team it is quiet.

**2.24 Work off the map — `ap.off-map-work@2`.** A position whose file edits mostly land in systems that other positions keep. *v1 (2026-11-15): an edit in the position's own system, or in a subsystem of it, counts as its own even when another position keeps that system. The first user organization's structure keeper labelled a project working in its own system, kept by its keeper, a false positive: "the rule should count a project's own system as its map".*
- **Signal:** the mandate router's verdicts (`of.mandate_check`, from `rt-v4`) on file edits (`Edit`, `Write`, `MultiEdit`, `NotebookEdit`) in the window: `in_mandate` against `owned_by:<keeper>`.
- **Left out:**
  - reads, since reading others' code is normal;
  - `Bash`, since a shell at a repository root is `unscoped`;
  - paths outside the organization;
  - paths no system claims: those are the map's gap, §2.7's `cluster`, and appear here only as evidence. They are keyed by area as `cluster` keys them, so an edit in a linked worktree counts under its repository, never as `~/<org>/.worktrees` (2026-11-12, `of_studio`'s `area_of`; that label made a structure keeper read an issue as a worktree bug).
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

**2.25 A needed cell nobody decides — `ap.cell-gap@1`.** RFC 0007 step 5, grown from §2.12: a cell is a system or subsystem crossed with a kind of work. The system architect declares `needs:` on systems, and the structure keeper declares `owns:` on positions ([`registry.md`](registry.md)).

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

### 2.27 Unsourced or strengthened requirement — `ap.unsourced-requirement@0`

*v0 (2026-11-11), built. Asked by `of_architect` (relay message 01a1cb93) after two incidents the founder raised that day, in which a claim lost its basis as it moved and grew stronger at each step. The rule it checks is [`handover.md`](handover.md) §1.1 (organic-framework 20b2f02): every claim that drives work carries its basis (an owner's ask, a source with the quote, evidence, or an inference), passed on unchanged.*

The first user organization's case (2026-11-10):
1. A compliance watcher read two laws and marked its reading "[confirm with counsel]" in a hand-over's context.
2. The hand-over asked a bot's position to screen live operations against a sanctions list, as plain work to do.
3. The PR that did it called it a "compliance requirement" and was merged with no review, so live operations were gated on a reading nobody had confirmed.

| Part | Fires when | Remedy |
|---|---|---|
| `strengthened` | A hand-over's own basis is an inference, and its title and done_when do not say so. Before the hand-over `basis` field (organic-framework 3fe957e), the inference is the requester's own word in the context ("[confirm with counsel]", "unconfirmed", "inference"). With the field, the offer already shows `basis: inference` as unconfirmed, so only a title or done_when that calls the work owed grows it. One issue per hand-over; the merged PRs and commits that carry it on are its evidence. A work record also fires on its own when it cites an inference-based hand-over that kept its hedge, and states the work as owed | Restate the basis as an inference in the hand-over and in the work built on it. Confirm it with an owner's ask or a quoted source before the work counts as required. The receiver returns the hand-over until then |
| `unsourced` | A hand-over or a work record states the work as owed, with no basis at all | Add the basis, or return the work to its requester |

- **What counts as stating an obligation:** the work is owed to an authority. Examples: "compliance requirement", "legal obligation", "required by …", "the law requires", "requirement REG-02".
  - A plain "must" or "required" does not count. In a spec it describes behaviour ("a required TOML key"), and in the first calibration week every such match was of that kind.
  - `of handover propose` refuses a broader word list (must, required, compliance, …) without a basis. That is the gate at the source; this rule checks what passed it and what grew after it.
- **What counts as a basis**, in this order:
  - the hand-over's `basis` field;
  - else an ask id, a quoted source, or the requester's hedge in the context;
  - else any context, such as a pointer to a register or a report, which v0 accepts as a basis.
- **How work is linked to a hand-over**, in this order:
  - its text cites the hand-over id;
  - the hand-over's delivered result names the PR or the commit;
  - it is the receiver's work, merged after the hand-over, and it cites a register token the hand-over cites (such as `REG-02`).

  Merge-queue copies are left out.
- **Left out:**
  - hand-overs declined, returned, withdrawn or expired, since the receiver did what §1.1 asks;
  - questions (a title ending in "?").
- **Decider:** the structure keeper, through the daily keeper digest. The organization's structure keeper fixes its instance (`of_architect`, relay message 01a1cb93).
- *p* = 0.6 (`strengthened`), 0.4 (`unsourced`); *m_decide* = 5 and 3 min. Severity medium and low.
- **Inputs.** It needs `of.handover_proposed`. It also reads hand-over states and work records when present (`reads` in the YAML); the run's loaders take `reads` kinds since organic-framework 12ba7dc (`of_studio`), and they never block a rule.
  - Since organic-framework 11aee30 (2026-11-11, `of_platform`), a merged PR's work record keeps its body (masked, at most 2,000 bytes) and its count of submitted reviews, and a commit's keeps its message body. The rule reads them with the title. Records imported earlier get them on their next re-import.
- **Calibration** (2026-11-11, the 7 days to that evening):
  - The first user organization: 2 issues, both from the same watcher's hand-overs with "[confirm with counsel]" in the context.
    - One is the case above. On titles alone, the register token linked its PR. With bodies, the PR's body cites the hand-over id, and its record shows 0 reviews. The body was restated the next day as "Precaution, unconfirmed".
    - In the other, an unconfirmed duty became a row of the organization's legal obligations calendar.
  - The OF team: quiet.
  - Rerun on 2026-11-12, the 7 days to 00:45Z, with the new fields: the same 2 issues and no new ones. Of the work records read (the 9 days before the run), 675 of the first user organization's 2,002 carry a PR body, and 442 of the OF team's 1,404 carry a commit message body. The OF team stays quiet.
  - Two calibration notes:
    - "Must" and "required" matched the titles of 4 merged PRs (each also as a merge-queue copy), all describing behaviour.
    - "Assume" as a hedge word matched 3 CI PRs ("assume the publisher role"), so v0 does not use it.
- **Not covered here:** a claim about people in a registry, such as "a colleague keeps the X bots", the day's first incident. [`registry.md`](registry.md), People, requires `evidence:` beside such a claim, and `of registry check` warns without it (`of_platform`).

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
| 2.3, 2.4, 2.6, 2.9, 2.10 (pair, organization) | WR attributed to positions; systems per WR | Both exist. Systems by paths come from `of import github`. Positions come from transcript PR links joined onto the merged PR, and, since 2026-11-10 (`of_platform`), from the `Position:` line of a PR body. On 2026-11-10 only 90 of the first user organization's 820 merged PRs in the window were attributed, so these rules were blocked |
| 2.4, 2.8 | `blocked_on` with an end | backlog v1.1 item 7 |
| 2.5 | `of.compaction` (v1); rereads; the owner's corrections | Compactions and `of.founder_correction` (RFC 0006 §2.4) exist, and the rule reads both from 2026-11-14. Reread detection: new |
| 2.6 runtime | runtime writes as work records (deploys, host actions, settings) | `of.deployment` (backlog item 2); host actions: new |
| 2.9 | `reverts`/`fixes` relations; review evidence of local commits and of answers | Relations: backlog item 10. Review evidence on merged PRs exists: submitted reviews (2.27's input) and verdict comments (`verdicts`, `of_platform`, 2026-11-15). Reviews of local commits and of answers: new |
| 2.10 | reply and reference tracking on messages | new |
| 2.11 | `of.mandate_changed` (v1.1); `reason_class` (item 11); MAST sampling | backlog; RFC §5.3.5 |
| 2.12 | `mandate_functions` on positions | registry (`of_platform`, 2026-11-05); the stakeholder map as data is still new |
| 2.14 | `mandate_exit` on positions | registry (`of_platform`, 2026-11-05) |
| 2.15 | `of.inbound_queue` on ≥ 3 days; presence for the live-holder test | Queue events exist, with exact ages since 2026-11-06 12:53Z (`of_platform`). Presence: new; v0 uses the registry |
| §3 | T and OE events per organization | app adapters |
| 2.16–2.22 | The loop projection of §4.1 | RFC 0005 §8 steps 1–3 |
| 2.28 | `observed_at` and `proxy` on runs, and `proxy_for` on measures; from v1 `located_at` and `as_recorded` | `of_platform`'s `of loop run` and `of registry check` from 2026-11-14 (hand-over 52b9d314); `of loop locate` and the projection's `located_at` and `as_recorded`, 2026-11-15 (`of_platform`, hand-over ba0485aa); each loop's measures in its organization's registry |
| 2.25, 2.26 | `needs:` on systems and `owns:` on positions | RFC 0007 steps 1 and 3: the schema exists; each organization's system architect fills it |
| 2.27 | A merged PR's body and review count on its work record | Exist since organic-framework 11aee30 (2026-11-11); records imported earlier get them on their next re-import |

### 4.1 What the loop rules read

The loop rules read one projection per work, `model["loops"][<work>]`, through `of_model`'s rule surface (`of_studio`). The derived metrics are RFC 0005 §5's, computed once by `of_studio` for both the loop card and the rules. A rule never recomputes them.

| Field | Meaning | Source | Owner | Read by |
|---|---|---|---|---|
| `work`, `owner`, `doers` | The work id, its owner (`work_owner`), the positions doing it | registry `works:` | `of_architect` (schema) | all |
| `target` | `metric`, `money`, `stake`, `better` (`higher` default, `lower`), **`segment`** (`start`, `end`, `context`: a list of parts, each a text or `{name, what}`; `segment` is null when the target names none), **`decided`** (the owner's yes on the measure, docs/registry.md Works, "The owner's measure", only when it resolves in this machine's log as a person's yes; else null, and the target is priced in money), **`decided_words`** (what the person said: the ask's question and chosen option, or the prompt's first words), **`decided_unresolved`** and **`decided_unresolved_why`** (`not_found`, `unread`, `not_looked_up`; present only when an id is written and does not resolve), **`observed_at`** (where its reading is taken; null when not named), **`chain_proxy_for`** (`{at, valid_when}` when the target's money line stands for an event elsewhere, such as live profit for a backtest; null otherwise; 2026-11-15, `of_studio`) | registry `works.<w>.loop.target` | `of_architect`; `segment` in `of_studio`'s projection since organic-framework PR 60, `decided` since `of_studio`'s change of 2026-11-14 | 2.17, 2.18, 2.21 |
| `measures[]` | `name`, `to_money` (empty means not priced), **`to_target`** (how it moves a target the owner decided; null when not given; 2.21 counts it only on such a target), and as the registry writes them **`observed_at`** and **`proxy_for`** {`at`, `valid_when`} ([`registry.md`](registry.md), Works, "Where a reading is taken") | `loop.measures` | `of_architect` | 2.21 |
| `ladder[]` | Cheapest first: `rung`, `cost`, `latency`, `accept`, **`production`** (true for shadow and higher), **`since`** (when the rung became available; default the loop's start) | `loop.ladder` | `of_architect`; `production` and `since` are new | 2.16, 2.17, 2.22 |
| `checkpoint_days`, `wip_arms`, `babysit_model` | Per work; defaults 3, 2, `sonnet` | `loop` | `of_architect` | 2.17, 2.19, 2.20 |
| `hypotheses[]` | `id`, `ts`, `status` and `status_at`, the last of `open`, `testing`, `kept`, `dropped`, `parked`; **`predict`** and **`change`** (texts, null when the event gave none) | `of.loop_hypothesis` | `of_platform`; `predict` and `change` in `of_studio`'s projection since organic-framework PR 60 | 2.16, 2.19, 2.21, 2.29 |
| `runs[]` | `id`, `rung`, `hypothesis` (may be empty), `metric`, `on_target`, **`value`** (a number in the metric's unit), **`accepted`** (the rung's `accept` met; `of loop run` writes none on a proxy not shown to hold, so one there is 2.28's part `accepted`), **`observed_at`** (where the reading was taken) and **`proxy`** (`{for, valid_when, held}`, `held` one of `held`, `not_held`, `unknown`; null when the reading is taken where its event happens), each from the event, a part it lacks from its metric's measure (`observed_at`, and `proxy_for`, which counts only when it names `at`), with `held` then `unknown`; any other `held` reads `unknown`, `result`, **`queued_at`**, `started_at`, `ended_at`, `position`, `session`, `model`. A run on a proxy not shown to hold is not observed: it stays out of `curve`, `plateau`, `trend` and the gains. From 2026-11-15: **`direct`** (the run stated that its reading is taken where its event happens), **`located_at`** (when `of loop locate` placed it, else null) and **`as_recorded`** (`observed_at`, `direct` and `proxy` as recorded, before the first locate). A run with no `observed_at` is unlocated and counts the same | `of.loop_run`, or derived from a runner's records | `of_platform`; `value`, `accepted` and `queued_at` are new beside RFC §4.1's `result` text; `observed_at` and `proxy` in `of_studio`'s projection since its change of 2026-11-14 | 2.16–2.18, 2.21, 2.22, 2.28 |
| `decisions[]` | `ts`, `kind` (`keep`, `drop`, `park`, `reopen`, `step_up`, `step_back`, `change_objective`), `hypothesis`, and for `change_objective` the new `metric` and its `to_money`. **`decided`** is always null: only the registry cites the owner's yes, and `of loop` refuses a change of objective on a decided loop. An event that names one keeps it in **`decided_unresolved`**, with `decided_unresolved_why` `decision` | `of.loop_decision` | `of_platform` | 2.21, 2.28, 2.29 |
| `metrics.rungs.<rung>` | `runs`, `cycle_time`, **`wait_share`** (from runs with `queued_at` only; null when none has one), **`wait_runs`** (the runs it is read from) and **`wait_unobserved`** (the runs without a queue time), from 2026-11-23 (`of_architect`'s hand-over `0985ab73`) | RFC §5 | `of_studio` | 2.22 |
| **`waits[]`** | Stated waits, in time order: `ts`, `on` (`runner`, `window`, `release`, `holdout`, `ask`, `position`, `calendar`), `ref`, **`expect_at`** (epoch seconds; `of loop wait` requires it), **`ended_at`** (a wait on an `ask`: when that ask was answered or withdrawn, if before `expect_at`; null otherwise), `hypothesis` (may be empty, meaning the whole work), `by`, `note`. Absent until projected | `of.loop_wait` (`of loop wait`) | `of_platform` (the event), `of_studio` (the projection); asked 2026-11-16 | 2.29 |
| **`state`** | The work's `state` as the structure keeper sets it (`done`, `parked`; null otherwise) | registry `works.<w>.state` | `of_studio`; asked 2026-11-16 | 2.29 |
| `metrics.wip` | Hypotheses in `testing` over time, as `[(ts, n)]`: the work's total | RFC §5 | `of_studio` | 2.19 |
| **`metrics.wip_groups`** | The same per group that `wip_arms` counts by: `{cell, rung, limit, series}`, `cell` when the loop declares cells, `rung` when `wip_arms` is a map (the rung of the hypothesis's latest run, else its cost's, else the ladder's first), `limit` that group's (null for a rung the map does not name), `series` as `[(ts, n)]`; one group with both null for a plain number. `wip_arms` reads as declared, a number or a map. From 2026-11-23 (`of_architect`'s hand-overs `c4ef8758` and `0985ab73`) | RFC §5 | `of_studio` | 2.19 |
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
| owner-router@1 | fired 1 | fired 1 | P/R 1.0 | 1 tp, fires | — |
| owner-relay@1 | fired 3 | quiet | P/R 1.0, both halves; negatives: a busy pair (6 of 80), a unit owner's own unit | 3 tp, fire | — |
| coordination-gap@2 | fired 4 | blocked (no work records, no `depends_on`) | P/R 1.0; reports blocked when a keeper is unobserved | 3 tp, fire | — |
| over-coupled@4 | blocked (`wr.position`); 2026-11-10 with PRs attributed: 26 under v1, 6 under v2; 2026-11-15: 8 (ratios 3.1–5.3, each ≥ 29 PRs) | 2026-11-15: 1 (`of_release` and `of_secrets-review`, 3.5) | P/R 1.0; negatives: one-directional, a unit owner, a pair with no merged PR | 1 true_but_not_now (2026-11-12, at 4.1, snoozed): too few to move the ratio | the 8 unlabelled issues (7 in U1, 1 in OF) to label with their structure keepers |
| context-overload@0 | 2026-11-14: quiet | 2026-11-14: quiet | P/R 1.0; negatives: every position compacting as often, an outlier corrected only on output and scope or long after a compaction; reports blocked without the owner's corrections | — | a real case; rereads |
| fragmented-ownership@1 | blocked (`of.deployment`, `wr.position`) | blocked | code P/R 1.0; negatives: keeper named inside the windows, one window only | — | runtime part not built (no `of.deployment`) |
| orphan-work@4 | fired 3 (cluster 1, keeperless 2) | quiet | cluster and unmapped P/R 1.0; negatives: a scratchpad, verdicts before rt-v4, 1 of 31 unmapped | 2 true_but_not_now, 1 wrong_remedy (keeperless on an unsupported system, fixed in v2) | keeperless moved to unkept-system (v3) |
| bottleneck-broker@1 | quiet | quiet | P/R 1.0; negatives: a coordinator hub, a mesh | — | — |
| missing-verifier@2 | 2026-11-14: fired 1 (`answers`: a position that answers the owner, 7 corrections of its results in 99 of his prompts) | 2026-11-14: fired 1 (`answers`: `of_conductor`, 18 in 226) | both parts P/R 1.0. `results` negatives: one reviewed PR, corrections of method and process, 3 against 20 PRs, PRs that do not say whether they were reviewed, a repository whose verdicts were not read, a PR approved by another position's verdict comment. `answers` negatives: 3 in 100 prompts, corrections of method only. Reports blocked without the owner's corrections | — | the two `answers` issues to label with the structure keepers; reverts |
| chatty@4 | fired 31 (pair 19, flood 5, nudges 7); 2026-11-14 open pair issues 43 under v2, 19 under v3 | fired 2 (flood) | all parts P/R 1.0 (pair, organization, flood, nudges); pair negatives: 6 per PR, 12 messages over 1 PR | 2 tp (pair, 2026-11-06), fire under v3; 10 fp (pair, 2026-11-12), all below v3's floor; 2 true_but_not_now (flood, 2026-11-12), still raised | 19 pair issues to review |
| unclear-mandate@4 | quiet; 2026-11-15: 33 under v1, 9 under v2, 2 under v3 | 2026-11-15: 1 under v2 (judged a false positive by its decider), 0 under v3 | P/R 1.0; negatives: schema adoption with reworded summaries, onboarding, a rule pointed from five briefs, a position's own brief gaining rules | 1 false positive in its decider's message (`01a1dc76-ed6e`), not yet recorded as a label: moved v3 | that label; the 2 issues to label |
| function-gap@1 | fired 7 | fired 12 | P/R 1.0 (a function covered only by a parked position) | — | 12 on OF to review |
| churn@0 | quiet | blocked (no work records) | both parts P/R 1.0; negative: holder changes with no dip | — | — |
| lingering@0 | quiet | quiet | P/R 1.0 | — | — |
| inbound-backlog@2 | blocked (queues on 2 of 3 days) | blocked | both parts P/R 1.0; negative: a queue under an alias | 5 tp, 2 fp on aliases (fixed in v2) | — |
| loop-cheapest-rung@0 | 2026-11-15, 18 declared loops: fired 5, quiet 1, blocked 12 (no run in the window) | no declared loop | P/R 1.0 | — | the 5 issues to label with their doers |
| loop-checkpoint@0 | 2026-11-15, 18 loops: fired 1, quiet 5, blocked 12 | no declared loop | P/R 1.0 | — | the issue to label with its work owner |
| loop-plateau@2 | 2026-11-15, 18 loops: quiet 6, blocked 12 | no declared loop | P/R 1.0 | — | a real case: quiet on the 6 loops with runs |
| loop-wip@0 | 2026-11-15, 18 loops: fired 2, quiet 16 | no declared loop | P/R 1.0 | — | the 2 issues to label with their doers |
| loop-babysit-model@0 | 2026-11-15, 18 loops: quiet 18 | no declared loop | P/R 1.0 | — | a real case |
| loop-objective-drift@2 | 2026-11-15, 18 loops: fired 4, quiet 2, blocked 12; partly from runs whose metric names no declared measure | no declared loop | P/R 1.0 (6 tp: unpriced, untied runs, a change of objective on the owner's measure, a `decided` that does not resolve, context by a hypothesis, context by a named run; negatives: priced, a measure tied to the owner's target, a change of objective with its money on a priced loop, a lever inside the segment, a name near the part's name, no segment) | — | the 4 issues to label with their work owners; runs that name their measure |
| loop-eval-bottleneck@0 | 2026-11-15, 18 loops: fired 2, quiet 4, blocked 12 | no declared loop | P/R 1.0 | — | the 2 issues to label with their work owners |
| loop-observed-elsewhere@1 | 2026-11-15, 18 loops: fired 4, quiet 14 (under v0: quiet 3, blocked 15). The 4 are keeps and drops, or accepts, resting only on runs nobody located, the founder's case of 2026-11-14 among them | no declared loop | P/R 1.0 (4 tp: a drop on proxies not shown to hold, an accept on such a proxy, a drop on unlocated runs, a drop on a run located only after it; negatives: a run on the data where the condition held, a keep that also rests on runs on the target, a proxy reported with no accept, a direct reading whose money chain rests on a proxy, a run located before the drop) | — | the 4 issues to label with their doers |
| unkept-system@1 | fired 1 (delivery), 2026-11-10 | quiet | both parts P/R 1.0; negatives: external keeper, unsupported, a supersystem (keeper and unit) | — | — |
| off-map-work@1 | fired 7, 2026-11-10 | quiet | P/R 1.0; negative: reads and shells | — | the 7 U1 issues to label with its structure keeper |
| cell-gap@0 | blocked (no `needs` yet) | blocked | all three parts P/R 1.0; reports blocked without cells | — | U1's cells |
| cell-overlap@0 | blocked (no `owns` yet) | blocked | P/R 1.0; reports blocked without cells | — | U1's cells |
| unsourced-requirement@0 | fired 2, 2026-11-11 | quiet | both parts P/R 1.0; negatives: a quoted source, the hedge kept in the done_when, a returned hand-over, spec words; reports blocked without hand-overs | — | the 2 U1 issues to label with its structure keeper |

The loop rules (2.16–2.22) were added and built on 2026-11-07, after the live run above. On 2026-11-15 each ran on each of the first user organization's 18 declared loops alone (this position's mandate 5); its results per loop are in that organization's records. Twelve loops had no run ending in the window. On three loops no run's metric names a declared measure or the target's metric. Those runs inherit neither `observed_at` nor `proxy` from their measure, and 2.21 reads them as unpriced or untied. Among them is the loop of the founder's prompt of 2026-11-14 (01a1da18-f250), whose measures carry `proxy_for` at the block's builder. `of_architect` decided the same day that a run names where its reading was taken, and an unlocated run decides nothing ([`registry.md`](registry.md), Works); 2.28 v1 reads that, and raises those loops.

`of.correction` was never recorded. context-overload and missing-verifier read the owner's corrections, `of.founder_correction`, instead, and were built on 2026-11-14. With them every rule of the catalogue has a result in `lab-drift-v0`.

### 5.2 Priors from labels (2026-11-15, re-tallied 2026-11-23)

This position's mandate 5 sets each rule's *p* and *m_decide* from its deciders' labels, or names the rule as having too few. The rules:
- **The labels.** Each issue counts by its latest label that counts in the run: the owner's, or the structure keeper's or the position's the issue routes to, unless the issue names it (organic-framework PR 630, `of_diagnose.may_label`), so a position's label on an issue about itself never moves a *p*. Issues the rule's current version no longer raises are left out (the classes fixed by unclear-mandate@3, chatty@3 and @4, unkept-system@1 and off-map-work@1).
- ***p*** is the share of the labelled judgements that are not `false_positive`: `true_positive`, `true_but_not_now` and `wrong_remedy` (a true diagnosis with the wrong remedy) all count as true. It is held at 0.95 or under.
- **Distinct judgements.** A decider often labels many issues of one cause with one note, such as 34 cell-gap issues on systems whose cells nobody has mapped yet. So the count is of distinct judgements (organization, label, note), and *p* is set only from 5 of them.
- ***m_decide*** stays as it was for every rule: a label records no decision time.
- **The tally is a framework tool**, `python3 bin/of_priors.py --org <organization>` (2026-11-23), so an organization runs it on its side, where its labels stay ([`contours.md`](contours.md) §2; `of_architect`, message `01a205f4-c040`). It applies the rules above and prints, per rule at its current version, only counts and *p*: the labelled issues, those on a version the current one does not carry, those no label of which counts by its writer, those whose replay was blocked, those it no longer raises, those counted, their distinct judgements and false positives, *p* (or "too few"), and the catalogue's *p*. It prints no issue text, id, note or affected position.
  - The owner's label given on the current version counts as it is: that version raised the issue it was given on, since a version changes whenever what it raises does. A label on a version the current one carries, or on none, counts only when the current rules still raise its issue in a replay over the 7 days before it, as Studio's page runs them; an issue merged into another counts as raised. A label from another writer than the owner, whatever its version, is judged in such a replay too, as the run judges it, since who may label an issue is the issue's: the positions it names and the one it routes to (an issue merged into another by its own rule's routing), the structure keeper of the label's own time, and the writer the store records, a session's by its holder in that run; when no label of its issue counts, it counts apart (`other writer`). A later label that no replay can judge does not replace the owner's, which counts on any issue. A label whose rule was blocked or failed in that replay, or had a part blocked while its issue is not raised (a label names no part), is not judged and counts apart (`replay blocked`), so that part is only as complete as the store holds each window's events: it runs on the machine that holds the organization's history. A label the tally cannot read (its label, rule, version, time or writer not text) is left out and counted in the footer.
  - Its cost: one replay per sitting of the labels that need one (labels less than an hour apart), running only the rules labelled in it, over rows read once, of the kinds Studio's page and those rules read, through a 9-day window slid forward from sitting to sitting, so memory holds one window. Its progress goes to stderr; the footer gives the CPU seconds and the peak memory. The first version replayed every rule over 16 days of every kind per labelled hour: a user organization stopped it at its cap of 60 CPU seconds, past 3 GiB, with nothing printed (`of_architect`, message `01a205fe-0a3d`). On this organization's agent host, over 25 labelled issues and a small store, the first version took 2.5 CPU seconds and 112 MiB with 8 replays, this one 0.5 and 63 MiB with 4, and each replay found the same issues of its rules as a full one. The first user organization then ran it under the same cap: 11 replays over 288 labelled issues, 45 CPU seconds and 1.86 GiB at its peak, with no replay blocked. Its memory is one 9-day window of the model's kinds and one model, as a Studio page of that organization holds: it grows with the organization's events in 9 days, not with how far back its labels go. The writer rule (2026-11-24) adds the sittings of the labels from other writers on current versions: on this organization's agent host, where every label is the structure keeper's, it ran the same 7 replays as without it, at 0.7 CPU seconds and 70 MiB, and its one label on a current version whose replay was blocked moved from counted to `replay blocked`; with the keeper of each label's time, writers by their sessions and a blocked part read as blocked (of_review on PR 632), the same 7 replays took 0.7 CPU seconds and 69 MiB, and orphan-work's 4 labels moved from `not raised now` to `replay blocked`.
- **The 2026-11-23 re-tally** adds the table the first user organization's structure keeper ran on its side with the tool of organic-framework PR 581 and returned whole through `of_architect` (message `01a20606-59b6`), to this organization's, run on its agent host. Distinct judgements add up across the two, since the organization is part of each. This organization's agent host holds little before 2026-11-15, so 7 of its 25 labelled issues read `replay blocked` (chatty 3, inbound-backlog 3, owner-router 1) and are not judged; neither table names a note, so no cause of a false positive is given here.

| Rule (2026-11-23) | Labelled issues | Counted | Distinct judgements (false positives) | *p* |
|---|---|---|---|---|
| cell-gap@1 | 47 | 47 | 14 (0) | system 0.6, nobody 0.6, no_decider 0.5 → 0.95 |
| chatty@4 | 107 | 81 | 29 (3) | 0.91 → 0.90 (chatty@5, 2026-11-24); this organization's 3 true positives whose replay was blocked are not judged |
| function-gap@2 | 9 | 9 | 5 (1) | 0.7 → 0.8 |
| inbound-backlog@3 | 29 | 23 | 8 (0) | 0.95, unchanged |
| off-map-work@2 | 16 | 15 | 13 (4) | 0.8 → 0.69 |
| orphan-work@5 | 21 | 18 | 10 (4) | cluster 0.6, unmapped 0.8 → 0.6 |
| over-coupled@5 | 35 | 35 | 26 (0) | 0.5 → 0.95 |
| unkept-system@2 | 20 | 13 | 12 (0) | keeper 0.6, unit 0.5 → 0.95 |
| coordination-gap@2, unsourced-requirement@0, loop-objective-drift@2, missing-verifier@2, unclear-mandate@4, owner-relay@1, owner-router@1, loop-checkpoint@0, loop-eval-bottleneck@1 | 1 to 6 each | | under 5 each | too few labels: unchanged |
| the other rules | none | | — | too few labels: unchanged |

- A label names no part, so a rule whose *p* was set per part takes one *p* for all its parts. Each rule whose *p* changed is a version that changes only priors, with `labels_carry_from` listing the earlier ones. *m_decide* is unchanged.

The 2026-11-15 tally:

| Rule | Labelled issues | Distinct judgements (false positives) | *p* |
|---|---|---|---|
| chatty@4 | 37 | 11 (1) | 0.6 → 0.91 |
| inbound-backlog@2 | 5 | 5 (0) | 0.5 → 0.95 |
| off-map-work@1 | 9 | 5 (1) | 0.5 → 0.8 |
| cell-gap@0, over-coupled@4, function-gap@1, coordination-gap@2, orphan-work@4, unkept-system@1, missing-verifier@2, owner-router@1, owner-relay@1, unsourced-requirement@0, loop-objective-drift@2, loop-checkpoint@0, loop-eval-bottleneck@0 | 1 to 34 each | under 5 each | too few labels: unchanged |
| the other rules | none | — | too few labels: unchanged |

Two false positives point at the registry, not the rule: function-gap's "goals" (`of_architect`'s brief proposes the team's goals; the function belongs in its `mandate_functions`), and off-map-work's alert rules (`obs_keeper`'s mandate covers every platform system's alerts, which no registry field says). One points at a rule of the organization: loop-objective-drift's `unpriced` part on a latency measure of a speed work, which the founder's rule in that organization says needs no price (a speed cut is justified by the latency it buys). It is one judgement, so it moves nothing yet.

## 6. Implementation v0

Agreed with `of_studio`, 2026-11-06. Split on 2026-11-07 (`of_studio`, hand-over e42c1acf):

- **Modules.**
  - The rules are `bin/of_diagnose_rules.py` (`of_field-analyst`), with their tests in `tests/test_diagnose_rules.py`. A rule reads the model and the rows only through `of_model.RULE_SURFACE`, the query surface `of_studio` keeps; a reading the surface lacks is added there by `of_studio` first.
  - The run is `bin/of_diagnose.py` (`of_studio`). It checks each rule's inputs, applies the owner's labels, routes issues to units, merges, ranks and escalates.
  - `diagnose(model, rows, a, b)` returns `{"issues": [...], "rules": [{rule, version, status: fired | quiet | blocked | not_built | failed, missing: [...]}]}`; a rule that raises reads `failed` with its `error` and no issues, and the other rules run on (2026-11-23, `of_studio`: `wip_arms` may now be a map by rung, which a rule comparing a count with one number cannot read; such a rule had stopped the organization's whole diagnosis, its escalations with it);
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

**Definition.** A status poll is an owner message whose main act is to ask for the state of work in progress, or of the agent doing it. The examples in this section are the owner's messages from the labelled week below, [2026-10-30T13:40Z, 2026-11-06T13:40Z), translated from Russian; the classifiers match the original wording. Examples:
- progress, or how things stand: "how are things here?", "what's the progress on …?";
- what is left: "what do we have left?";
- what happened while the owner was away: "how did the night go?", "what happened while I was away?";
- whether a step is done: "have you merged the PR yet?", "how did the database migration go?";
- when it will be ready: "when do we migrate monitoring?", "how long is the wait in the queue?";
- what the agent waits for, or needs from the owner: "what are you waiting for my yes on?", "what questions do you have for me?";
- impatience with progress: "why is it taking so long?".

**What still counts as a poll:**
- An imperative whose only object is to report or gather that state: "tell me what happened", "go through the sessions and tell me what questions there are for me", "find out from session X whether everything is ready".
- A greeting, a return note ("I'm back from the gym") or an acknowledgement ("okay", "great") in the same message.

**What is not a poll:**

| Label | The message… | Example |
|---|---|---|
| `instruction_with_poll` | also directs new work, decides, or gives new information besides the poll. It is an instruction, and the poll is secondary | "now we need to finish setting up …, what do we have left here", "do the next step, why are you waiting for me?" |
| `system_status` | asks for the state of a production system or a resource, not of work: alerts, limits, lost logs. A dashboard answers it, not the Works screen. Reported apart | "what's the status on the limits now?", "are we still losing logs or not?" |
| `question` | asks about design, mechanism or facts | "what does <a tool> do?", "how do you see the org structure …?" |

**The test for a question inside an instruction.** Remove the question. If what is left still asks the agent to do, decide or know something new, the message is an instruction. If only a greeting, an acknowledgement or a request to report is left, it is a poll.

**Unit.** Status polls per week (7 days), per organization and per position. Two figures:
- the count;
- the share of the owner's messages in the same scope.

The share is what moves when the owner's volume changes; the count is what the owner spends.

**Classifier.** `fa-2026-11-05`'s `status_poll` (a phrase list) is pinned for the founder-load baseline and stays as it is.
- On the hand-labelled week below it has precision 0.88 (38 of 43) and recall 0.75 (38 of 51).
- It misses polls without its phrases: "what's the status on the fixes?", "what about it?", "have you merged yet?", "what's left in the end …", "what is stopping us now from applying the PR?", "what's the forecast for the migrations?".
- It takes instructions that contain a poll phrase.

`sp-v1` is this definition. Its implementation (`of_studio`) must reproduce the hand labels within ±3 on the labelled week before it replaces `fa`'s count in the Works prediction. The hand labels are app material, kept as event ids with labels in each organization's own repository.

**Baseline.** The week before the Works screen, [2026-10-30T13:40Z, 2026-11-06T13:40Z), hand-labelled:

| Organization | Owner messages | Status polls | Share |
|---|---|---|---|
| The first user organization | 547 | 47 | 8.6% |
| The OF team | 178 | 4 | 2.2% |
| Both | 725 | 51 | 7.0% |

The labels come from a scan by wording of all the week's messages, plus two polls found in `of_studio`'s sample and one found by its implementation (2026-11-06), so 51 is a lower bound. Owner messages are counted after `of_platform`'s origin corrections of 2026-11-06 20:12Z, which turned 15 briefs and capacity notes into injected text, and without the brief and move-note templates (`of_studio`, 2026-11-06). `of model polls` reproduces the labels: 47 of 46 + 1 and 4 of 4 before that label was added, fitted on the same week, so this is a fit and not a test.
- The top position is the k8s keeper with 15. Next come the main bot's search agent (6), its operator (5) and the dispatcher (5).
- 4 polls came from sessions with no position.

### 7.2 What the owner does in a message — `oa-v1`

*Defined 2026-11-07, at `of_architect`'s request. Drawn from `of_studio`'s ad hoc breakdown of the k8s keeper's owner messages (instructions, answers to asks, approvals, pasted text, corrections) and its 45 sample ids.*

**What the breakdown mixed.** It put three different things on one axis:
- what the owner does in the message: instruct, approve;
- how the message arrived: pasted;
- what it answers: an ask.

A pasted message can be an approval or a relay. An answer to an ask can be an instruction. So `oa-v1` gives every owner message **one act** and two attributes.

**Acts.** When a message holds several acts, the first in this table wins. The examples are the owner's messages from the sample, translated from Russian:

| Act | The owner… | Examples |
|---|---|---|
| `relay` | passes on another agent's output or request: pasted, or retold. Most of the text is that agent's, not the owner's. Plumbing, read by §2.2 | "The CVE session asked to move two watchers …"; a pasted "Four yeses needed: …" |
| `correction` | says that the agent did, proposes or assumes something wrong, or challenges it. Read by §2.9 | "why do we need runners with that many gigabytes?", "not like that", "fix …" |
| `instruction` | directs new work, or changes the direction of work: a goal, a requirement, a plan, a routing order | "make the proxy fault-tolerant", "consult the session that …" |
| `decision` | chooses among options the agent offered, or answers its questions with content other than yes | "1. Okay 2. Well, that would be good to do 3. we put it in the cluster" |
| `approval` | consents to an action the agent proposed, possibly with a condition, and adds no new work. An imperative that only carries out the proposal is part of the approval | "let's apply it", "I confirm <the bot>", "Yes, agreed. Record this decision" |
| `status_poll` | §7.1 | |
| `system_status` | §7.1 | |
| `question` | asks about design, mechanism or facts | "what is stickiness?", "does it really work?" |
| `information` | gives the agent facts it did not have: a person's feedback, an alert, a log | "Feedback from a colleague: …"; a pasted alert |

**Attributes:**
- **`pasted`**: the corrected origin is `paste`, or the text holds a `<pasted_content>` block.
  - With `paste_source` (ps-v0) at ≥ 0.5, the text is another session's output, and the act is `relay`.
  - Without a source, it comes from outside this machine (a person, a person's agent, an alert), and the act is read from the owner's own words around it.
  - The hook misses some pastes typed as `human`, such as an agent's report copied in. Read them as pastes: agent prose in the third person, formal lists, "the agent did not find …".
- **`answers_ask`**: the message closes an ask (`closed_by` in the ask lifecycle, or `reply_to`). It is not an act: an answer can be a decision, an approval, an instruction or a correction.

**Unit.** As in §7.1: per week, per organization and position, count and share of owner messages, for each act and attribute.

**Calibration.** The 45 sample messages, labelled by act, are kept in that organization's own repository, with `of_studio`'s sketch label beside each. Of the 45, 24 agree once `pasted_text` is read as relay or information. The rest move mainly in three ways:
- "approval" that adds new work becomes `instruction`;
- "answer" becomes `decision`, `instruction` or `relay` by its content;
- pastes that the hook missed become `relay`.

`oa-v1` is a definition with one labelled sample of one position, not a calibrated classifier. Before it is implemented, a second sample of at least 100 messages across positions is labelled.
