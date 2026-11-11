# lab-drift-v0 — design note (for of_architect and of_studio)

Calibrate of_studio's anti-pattern detectors by scoring precision and recall on **synthetic, labelled** drift
with negatives (docs/diagnose.md §5.2; RFC 0001 §5.7). Owner: `of_lab`; verifier/detector owner: `of_studio`.
This is a pure-logic scenario (a direct function call, no VM): it runs as `python3 -m unittest` / a script, like
the relay-regression unit layer.

## Entry point (from of_studio, 2026-11-07)

Pure call, no store: `of_diagnose.diagnose(model, rows, a, b, cat=None, context=None)` where
`model = of_model.build(registry_doc, rows, at=b, org=NAME)`. `rows` are load_events-shape dicts
(`event_id, ts (ISO Z), name (of.*), session_id, position, org, cwd, source, payload`). `a`/`b` are epochs; the
window is `[a, b)` plus earlier rows for state. `context = {keeper_since: fn(system, keeper) -> epoch}` (a lambda
in the lab; fragmented-ownership and lingering-adjacent rules use it). Returns
`{issues: [{rule, part, affected, evidence, ...}], rules: [{rule, version, status: fired|quiet|blocked, missing}]}`.
A rule whose inputs are absent reports **blocked** — scored separately, never as a false negative. Fixture shape:
`tests/test_studio.py` (`DOC`, the `ev(...)` and `prompt(...)` helpers).

## Rules in v0 (five)

| Type | Rule id | Positive fixture (fires) | Negative fixture (must stay quiet) |
|---|---|---|---|
| router owner | `ap.owner-router@0` | an owner position with in-degree share > 0.25 and plumbing share > 0.05 across many positions, λ rising/flat | an owner with normal coordination below the shares; a coordinator doing the routing (excluded by kind) |
| over-coupled pair | `ap.over-coupled@1` | two keepers with interface ratio ≥ 3, reciprocity ≥ 0.5, ≥ 1 mutual wait, observed ≥ 3 days | a pair below the interface ratio, or with no mutual waits, or one-directional traffic |
| missing keeper | `ap.orphan-work@1` (part `keeperless`) | ≥ 5 merged-PR work records changing a system with **no keeper** (payload.systems), over the window | the same work on a system that **has** a keeper; < 5 records; work only under /tmp or scratchpad (excluded paths) |
| lingering keeper | `ap.lingering@0` | an **active** position with a registry `mandate_exit` (done_when + review date), now ≥ exit_met_hours (48) past the review, **no** mandate change since, and owner prompts to it after the due date | a position whose exit was met but with a mandate change since; or no owner prompts after the due date; or still within 48 h |
| chatty (threshold case) | `ap.chatty@0` | **v0 signal (of_architect + founder, 2026-11-07): turns started in the receiver by typed-in (injected) messages AND pulse nudges, per hour** — not only messages per work record. Positive: a receiver whose turn-starts/hour from injected messages + pulse nudges clears the threshold | a pair at a normal rate; **and** a coordinator-heavy pair (runners_coordinator-style, 453 msgs/1 PR) kept as a NEGATIVE — per §5.1 that over-fire is a wrong-threshold sign the threshold must learn to exclude |

> **chatty signal change (of_studio owns it).** The founder's complaint today is about turns *started in the receiver* by injected messages and pulse nudges, not raw message volume. That is a change to `ap.chatty@0` in `diagnose-v0.yaml`/`of_diagnose` (count receiver turn-starts from `of.injected_prompt` / injected `of.human_prompt` and pulse nudges per hour). of_studio makes the detector change; lab-drift-v0 exercises and scores the new signal. The msgs/wr input stays as a secondary.

Thresholds are read from `docs/diagnose-v0.yaml` (not hard-coded in the harness); a threshold change bumps the
rule version. of_studio moves each threshold to the precision/recall point the owner chooses.

## Hard-to-synthesize inputs (set directly, from §4 / of_studio)

- `payload.classifications`: `fa-2026-11-05` (founder / cats) and `rq-v0` on `of.agent_message_in` (owner-router
  and owner-relay read it);
- `of.work_record` attribution — `position` on the event or a `pr-link` — and `payload.systems`;
- for lingering: the registry `mandate_exit` (done_when + review) on the position;
- for inbound-backlog-adjacent needs (not in the v0 five, but if chatty merges into bottleneck): `of.inbound_queue`
  rows on ≥ 3 days. Other events are plain.

## Two fixture sources (of_architect, 2026-11-07)

Synthetic fixtures alone only test the detectors against *our own* idea of drift, so v0 scores two sources:

1. **Synthetic, labelled** fixtures in the repo (the table above): positives + negatives per rule, VM-free and
   CI-safe.
2. **Real labelled cases from this week's tenant store** — e.g. the five Acme positions parked while still
   working, the lost doers, `platform_owner` routing ~60 asks, `returns_keeper`'s 28 identical nudges. Labels
   come from `of issues label` or from of_studio, per the §5 "owner labels in-tenant" protocol.

The real-case pass reads the real store and therefore runs **on the Mac, never inside a lab VM** (docs/lab.md,
"Level 2 ... does not run in the Lab's machines"); no real org data is copied into a disposable VM. The synthetic
pass is the VM-free/CI part. Each pass reports precision/recall per rule; the summary keeps them separate and also
combined, so a detector is judged on both our constructed drift and real labelled drift.

## Harness and scoring

- `lab/scenarios/lab-drift-v0/fixtures/*.py` (or one module): each scenario returns `(registry_doc, events, labels)`
  where `labels = [{rule, affected: [names], label: "positive"|"negative"}]`.
- `run.py`: for each scenario, `build` the model, `diagnose` over `[a, b)`, then **match** each returned issue to a
  label by `rule` id and `affected` set (set-equality on the affected names). Accumulate per rule: TP (positive
  matched by a fired issue), FP (a fired issue with no positive label, or matching a negative), FN (a positive with
  no fired issue). Report **precision = TP/(TP+FP)** and **recall = TP/(TP+FN)** per rule, plus the count of
  **blocked** rules (scored apart). Output a table and a JSON summary.
- A small unittest (`tests/test_drift_v0.py`) runs the harness and asserts each rule's P and R clear a floor, so a
  catalogue or model change that regresses a detector fails CI. The floor starts loose; of_studio tightens it to
  the owner's chosen point.

## Open questions for of_studio (before building)

1. For over-coupled and chatty, `reciprocity_without_waits_gte` / `waits_recorded: per_pair` and the λ inputs —
   which events carry the waits (`of.state_declared` waiting_on, or a dedicated event)? I'll synthesize whatever
   they read.
2. owner-router's `l_rising_lambda_flat` (λ): what event stream feeds λ, and over what sub-windows, so a positive
   actually trips it?
3. Is matching an issue to a label by `rule` + exact `affected` set right, or do some rules report `affected` as a
   single primary name (then I match on membership, not set-equality)?

## Build spec — of_studio's verification and fixture corrections (2026-11-07)

Scope approved (of_architect). of_studio confirmed the design matches the code and gave the exact inputs; the
fixtures are corrected as below. Matching is by exact, sorted `affected` set.

**ap.owner-router@0** — runs only for a HUMAN owner (a unit owner held by a human, else `founder`/`of_model.HUMAN`),
so a coordinator-routing negative is trivially quiet and tests nothing; a real negative keeps ALL THREE signals off,
a positive trips any one. affected `[owner]`.
- plumbing share > 0.05 = `of.human_prompt` (origin human, `classifications.fa-2026-11-05 = {kind: founder,
  extracted: true, cats: [...]}`) with a plumbing cat (routing, relay_paste, access, capacity_tooling, …) over all
  founder messages;
- in-degree share > 0.25 = asks to the owner / (asks + received peer requests); a peer request is
  `of.agent_message_in` source hook/import with `classifications.rq-v0` true. **Include rq-v0 messages or the share
  is 1.0 and the rule always fires** (so a negative must add rq-v0 peer traffic);
- λ: stream = asks accountable to the owner (`of.agent_question` detected_by declared, from_position, to founder; a
  hook heuristic question; `of.permission_request`) via `of_asks.project`; per UTC day, `little()` gives L and λ;
  fires when L rises (positive LS slope, last 3 days above median) while λ is flat (abs(slope×7) ≤ 0.2×mean).
  Positive: same arrivals/day, closing later each day. **7-day window** (≤3 days never trips).

**ap.over-coupled@1** — waits are `of.state_declared {position, state: waiting, waiting_on: <position>}` (what
`of status waiting --on X` writes; source of ok). `waits_recorded: per_pair` → waits count only when BOTH positions
have `of.state_declared` on ≥3 distinct days, then a mutual wait (each waiting on the other ≥once) is needed; else
reciprocity ≥0.8 substitutes. messages = `of.agent_message_out` with `payload.to_position`; work = non-bot
`of.work_record` attributed by event position or pr-link; ratio = messages / pair combined merged PRs; both observed
≥3 days (source hook/import, e.g. `of.turn_end`); coordinator kind skipped. **BLOCKED unless ≥50% of window work
records are attributed** — score apart. affected = the two positions, sorted.

**ap.chatty@0** (current code; the receiver-turns signal is a pending of_studio change) — same messages/work/observed
rules; fires with ≥10 messages AND ≥10 per merged PR; does NOT skip coordinators, so the runners_coordinator-style
case fires today — label it **negative and expect it as an FP** (the measured false positive). Also an org-level
issue, affected `["(organization)"]`, when msgs/merged-PR rise ≥50% vs the previous equal window — **give that its own
label** per scenario.

**ap.orphan-work@1 (keeperless)** — ≥5 non-bot `of.work_record` whose `payload.systems` includes a registry system
with no keeper; affected = the **system id**, not a position. The `/tmp` case belongs to the cluster part
(`of.mandate_check` paths), not keeperless — move or drop it.

**ap.lingering@0** — owner prompts do NOT gate it (they only size `delta_m`); so a "no owner prompts" negative WILL
fire. A valid negative is one of: exit not yet 48 h past the end of the review day; a mandate change after the due
date; the position not active; or no `mandate_exit`. Registry field `mandate_exit {done_when, review}` on the
position; due = review + 24 h. affected `[position]`.

Build proceeds now (priority 3 is done).

## of_studio update (2026-11-07, b8242dd): chatty built, runners is positive, real cases

**chatty receiver signal is built.** `of_diagnose.injected_turns(model, rows, a, b) -> [(ts, receiver, kind, sender)]`
for each turn a receiver started on text typed into its pane. Row shapes to synthesize (no `of.injected_prompt`
needed): **relay** = `of.agent_message_in` source hook, sender from the frame `Message from org:pos (via relay` or
`payload.from_position`; **nudge** = `of.human_prompt` whose text starts `Message from <waiter> (via of pulse`;
**tool** = `of.human_prompt` whose corrected origin is injected. Receiver = event position or holder session. Two new
parts of `ap.chatty`:
- **receiver_flood**: ≥ `injected_turns_per_hour_gte` such turns in an hour, on ≥ `flood_hours_gte` hours → affected
  `[receiver]`;
- **repeat_nudges**: one waiter nudging the same peer ≥ `nudges_per_pair_gte` times → affected `[waiter, receiver]`
  sorted.
Thresholds are code defaults until of_field-analyst sets them in the catalogue. Acme 7-day reference: 5 floods
(platform_owner 787 turns, peak 130/hr) and 6 nudge pairs (returns_keeper → storefront_operator 28).

**Correction — runners is a positive.** The founder labelled `a6c80536a491` (storefront_search + runners_coordinator,
453 messages / 1 PR) **true_positive** (ask `ac3b43dc`: runs outside the cbx gate made every chain a slot
negotiation). So the coordinator-heavy chatty case is a POSITIVE fixture, not the false positive I first planned.

**Real labelled cases (source 2), 10 `of.issue_labelled` in the store** (read from of.db; payload issue_id,
rule_version, label, note):
- `ap.chatty@0`: a6c80536a491, 297e9671f2d3 — true_positive;
- `ap.owner-router@0`: 6081a150e82b — true_positive;
- `ap.owner-relay@1`: 65eb65de3efe, a1a9ab96da37, e44d9d7dc005 — true_positive;
- `ap.coordination-gap@1`: be3bddf4ef14, 6f354eb8dd17, c1860b045c49 — true_positive (cause: a wrong boundary);
- `ap.orphan-work@1`: 146ea0207403 — true_but_not_now (fires, but deferred).
The real-case pass runs diagnose over the relevant store window on the Mac (never a VM) and checks each labelled
issue fires with its affected set. Of of_architect's named cases, only returns_keeper's nudges (repeat_nudges
`3ddaff3eeed5`, unlabelled) is a diagnose issue; parked-while-working (of pulse), platform_owner's ~60 asks (no rule
covers an agent unit owner), and the lost doers (a Works finding) are not — ask of_architect/acme_org for labels on the
ones that are issues.
