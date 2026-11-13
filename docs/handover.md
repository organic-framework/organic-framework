# Hand-overs, delegation and the structural diff

Status: **Proposal**, 2026-11-06, by `of_architect`.

This document specifies three things RFC 0001 v3 needs before colleagues join (phase 4) and before the fixer exists (phase 5):
- work between owners (§5.8);
- delegating a unit (§5.8);
- the structural diff (§5.6).

They answer two of the founder's pains:
- "Colleagues run their agents in unseen ways: tasks are done unreliably, sometimes the wrong task, with varying quality."
- "I cannot delegate part of the structure to unload myself."

## 1. Hand-over: work between positions and owners

A **hand-over** is a piece of work that one position asks another position to do. It applies within one owner's unit and, more importantly, across owners. A chat message does not count as a hand-over; a hand-over is an object with a lifecycle recorded as events.

### 1.1 The object

| Field | Meaning |
|---|---|
| `id` | stable id (UUIDv7) |
| `from`, `to` | positions, `<org>:<name>` when across organizations |
| `title` | one line |
| `mandate` | the requester's mandate the work serves (the reason it exists) |
| `done_when` | **checkable done criteria**: tests pass, a PR merged, a metric below a value, a file exists. Required. |
| `verify` | who checks the result: `requester` (the default) or a named verifier position. `auto` with a command comes later; it needs a grant and a remote runner. |
| `due` | optional date or "before <event>" |
| `priority` | `blocking` (the requester waits), `normal` |
| `expected_dT` | optional: why it matters, in throughput terms (feeds the owner's ranking) |
| `context` | references, not copies: briefs, files, PRs, earlier hand-overs |
| `basis` | **why the work is owed**: `ask:<id>` (an owner's decision), `source:<law, contract, standard or document> "<the quote>"`, `evidence:<a commit, metric, log or report>`, or `inference` (the requester's own reasoning, unconfirmed). Required when the title or `done_when` states an obligation (must, required, requirement, compliance, mandated); the receiver sees it in the offer before it accepts (2026-11-11, from a user organization: a watcher's unconfirmed inference travelled as a must and merged as a "compliance requirement"). |
| `scope` | optional `{systems: [...], paths: [...]}`. While the hand-over is accepted, the mandate router gives work in this scope the verdict `in_handover:<id>` rather than `owned_by:X`. This is what makes the wrong-task rate computable. |

**A basis never grows on the way.** Whoever passes work on (a further hand-over, a PR, a commit message, a brief, a registry line) carries its `basis` unchanged and cites it. An `inference` stays an inference until an owner's ask or a cited source confirms it, and text built on it says "unconfirmed"; it is never written as a requirement, a rule or a decision. A receiver declines or returns work stated as an obligation without a basis. The same rule holds for claims about people (docs/registry.md, People: evidence).

### 1.2 Lifecycle (events)

```
proposed ──► accepted ──► delivered ──► verified ──► closed
    │            │             │             │
    ├► declined  ├► blocked    └► rejected ──┘ (back to accepted, with the reason)
    └► expired   └► returned (the receiver gives it back)
```

| Event | Written by | Rule |
|---|---|---|
| `of.handover_proposed` | requester (`of handover propose`) | `done_when` required; `basis` required for an obligation (§1.1) |
| `of.handover_accepted` / `declined` | receiver | **must answer within the receiver's response window**. The window is counted from delivery into the receiver's pane. The default is 30 minutes; a position can override it with the registry field `response_window`. `of pulse` nudges once the window has passed. |
| `of.handover_blocked` | receiver | names what it waits on (owner, peer, external) |
| `of.handover_delivered` | receiver | links the result (PR, file, report) |
| `of.handover_verified` / `rejected` | verifier | rejected carries the reason; the work returns to accepted |
| `of.handover_closed` | requester | closes the requester's wait |
| `of.handover_returned` | receiver | gives the work back with a reason (wrong owner, too big, unclear) |
| `of.handover_withdrawn` | requester | cancels the hand-over before it is closed |
| `of.handover_expired` | `of pulse` | `due` has passed without acceptance, or four response windows have passed with no answer |
| `of.handover_noted` | requester or receiver (`of handover note <id> --note "…"`) | a note on an open hand-over, never a state change: a correction to its basis ("precaution, unconfirmed"), a narrowed scope. `of handover list` and the offer show every note (2026-11-11, from a user organization) |

Hand-overs are delivered through the relay as messages in the fixed frame. The **record** is the events, not the text.

### 1.3 What this fixes

- **Wrong tasks.** The receiver accepts against explicit `done_when` and its own mandate. A hand-over outside its mandate is declined or returned, and that is visible. The mandate router reports work done outside any accepted hand-over or mandate.
- **Unstable quality.** Every hand-over has a verifier, which is the remedy for MAST's "missing verifier". Rejections are counted per receiver.
- **Silent waits.** A `blocking` hand-over is a declared wait of the requester. `of pulse` and Studio see it.

### 1.4 Measures per owner (Studio)

For each owner:
- hand-overs accepted within the response window;
- delivered by `due`;
- first-pass verification rate;
- rejections and their reasons;
- returns;
- **wrong-task rate**: work outside an accepted hand-over or the mandate, from `of.mandate_check`.

These are shown per owner, not per person's private habits. The platform does not dictate how a colleague runs their agents; it makes the work between owners predictable.

## 2. Delegating a unit

### 2.1 The delegation record

A delegation is a registry change, applied as a high-tier structural diff (§3).

```yaml
delegations:
  - id: del-2026-11-xx-discovery
    unit: discovery                 # a unit: its systems and positions
    systems: [discovery]            # systems that move into the unit
    positions: [catalogue_feed-fix, catalogue_paths-cleanup]
    from_owner: acme_owner          # the delegator's owner position
    to_owner: acme_catalogue-owner  # held by the colleague (person: <id>)
    envelope:
      grants: [g-…]                 # attenuated from the delegator's; no wider
      budget: {tokens_month: …, usd_month: …}
      risk_appetite: low            # bounds the grants the new owner may issue inside
      critical_denies: inherited    # the delegator's critical denies stay in force
    reporting:
      rollup: [owner_constraint, throughput_share, issues_open, handovers]
      escalate_when:                # the delegator is asked only on these
        - ask.blocking
        - ask_unanswered > 2h
        - budget_used > 80%
        - issue.severity >= high
        - envelope_breach
        - prod_change{affects:[storefront]}   # tag predicates: asks carry tags (of ask --tags, or guessed)
        - money
        - security_incident
        - irreversible                      # also from a destructive command seen by the mandate router
        - window_go{systems:[k8s]}
    review: 2026-12-01              # the delegation's own review date
    way_back: reverse-diff          # taking the unit back
```

### 2.2 What changes for the delegator

- Asks from inside the unit go to the new owner, not to the delegator. The router uses the unit's owner chain.
- In the delegator's Studio the unit is **one row**:
  - its owner constraint;
  - its throughput share;
  - its open issues and hand-overs across the boundary;
  - a red mark only on `escalate_when`.
- The accountable human for positions in the unit is the colleague, the nearest human owner. The delegator remains accountable for the envelope.

### 2.3 Taking it back

Taking a unit back is the reverse diff:
- the positions, grants and asks return;
- the unit's history stays with the organization;
- the colleague's content rights follow the organization's rules.

## 3. The structural diff

The single format for every structural change. The Lab applies it to a lab organization; `of` applies it for real.

```yaml
diff:
  id: sd-2026-11-xx-…
  kind: split | merge | spawn | close | park | swap_holder | reassign_owner | delegate | move_workplace | handover_rule | mandate_rewrite | grant_change | goal_change
  tier: low | medium | high                 # §5.6 of RFC 0001
  author: acme_org                          # the structure keeper proposing it
  issue: iss-…                              # the diagnosed issue it answers (optional)
  hypothesis: "Splitting storefront_operator into operator and releases cuts the owner's relays between them to zero"
  predicted:                                # signal deltas the review checks
    - metric: owner_relays{a=storefront_operator,b=storefront_releases}
      from: 7/week
      to: 0
    - metric: interface_ratio{storefront_operator,storefront_releases}
      to: "< 1"
  changes:                                  # machine-applicable edits
    registry: [ {op: add, path: positions[...], value: {...}}, ... ]
    goals:    [ {op: add | rewrite | close, id: g-…, value: {...}}, ... ]   # goal_change; conditions: the same
    grants:   [ ... ]
    briefs:   [ {op: write, path: registry/briefs/storefront_releases.md, from: fork-of-brief} ]
    routing:  [ ... ]
    desk:     [ ... ]
  continuation:                             # what the outgoing holder moves
    - crons: [...]
    - open_asks: [...]
  reversal: "reverse of changes; the forked holder hands its threads back"
  review: {after: "20 tasks or 7 days", compare: predicted}
  canary: {share_of_new_tasks: 0.3}         # optional, medium tier
  approvals: [owner]                        # owner's yes; objections from affected holders are recorded
```

**Rules:**
- A diff without `predicted` and `review` cannot be applied, because the fixer has nothing to check.
- `of diff check` runs the validity checks: the registry, grant attenuation, the single writer, the accountability path, no widened critical grant.
- `of diff apply` writes the events (`of.diff_applied`, plus `of.mandate_changed`, holder and grant events) and schedules the review.
- **An approval binds the diff's full SHA-256 digest** (2026-11-12, `of_server`'s finding): 64 hex characters over the canonical text, in every path (`of diff approve`, the card, a passkey). A shortened digest would let the agent that writes a diff find a second diff with the same digest and have it applied in place of the one approved. Cards and the terminal show the first 16 characters to people. A diff proposed with a shortened digest is proposed again before it is approved.
- `of diff review` compares observed signals with `predicted` and proposes keep, extend or revert. Every diff and its review go to the archive.
- **`goal_change`** (2026-11-12, `of_architect`; `of_conductor`'s message `01a1cf8f-1d75`) adds, rewrites or closes goals and conditions (docs/registry.md, Goals). One diff may carry a whole tree, so its approvers approve the exact text once. Its tier is medium at least. Each node it touches is approved by the owner of that node's parent (docs/registry.md, Goals, "Who decides"):
  - a root goal, or a condition held for the whole organization, by the organization's owner (`approvals: [owner]`);
  - a node under a unit owner's goal, or a condition held `under` it, by that unit owner (`approvals: [<the unit owner's position>]`), who approves it as the organization's owner does: `of diff approve` on a machine enrolled for the person who holds that position (docs/registry.md, People, `machines`), or that person's passkey (docs/remote.md §6);
  - **only a person's yes approves.** When an agent holds the unit owner's position, the organization's owner approves those nodes; an agent's yes never approves a diff (docs/remote.md §5 and §6). A position in `approvals` resolves to its holder's person when the diff is applied, not when it is proposed, so a change of holder in between carries no approval over;
  - a diff that touches both kinds names every approver it needs, and applies only when all have approved.
  Until `of diff approve` accepts a unit owner (`of_platform`), the organization's owner approves those nodes too. `of diff apply` writes each approval's full id into the `decided` of the nodes it covers. Its `predicted` names what the tree should change, for example the open works on no goal or condition going to zero, or a measure per sub-goal.

## 4. Order of implementation

| Step | What | Owner |
|---|---|---|
| 1 | Hand-over events and `of handover propose/accept/deliver/verify/close`; `of pulse` response windows; Studio measures | `of_platform` (client and events), `of_studio` (measures), `of_server` (relay delivery) |
| 2 | The diff format, with `of diff check/apply` for the low and medium kinds the build team needs first (spawn, mandate_rewrite, swap_holder) | `of_platform` |
| 3 | Delegation as a diff kind; per-owner roll-up and `escalate_when` in Studio | `of_platform`, `of_studio` |
| 4 | Review scheduler and archive; the explicit-difference view in Studio | `of_studio` |

## 5. Implementation decisions (2026-11-06, `of_architect` with `of_platform`)

- **State.** Hand-over state is a read-only projection over the events (`bin/of_handover.py`). Only the events are stored.
- **Who may write which event:**
  - accept, decline, blocked, deliver and return come from `to`;
  - verify and reject come from the verifier;
  - close and withdraw come from `from`.
- **Across machines,** the propose and answer messages travel through the relay in the fixed frame, tagged `handover:<id>`. The receiving client writes mirror events with the same deterministic ids.
- **A blocking hand-over** counts as the requester's wait on a peer in `of who`, on the desk and in Studio.
- **`of diff apply`** makes narrow text edits to the hand-written registries, keeping comments, in the repository of the organization's structure keeper. The keeper commits them as the single writer.
- **Diff v2 operations** (2026-11-06), in any kind, covered by the approved digest like every change:
  - `changes.registry: [{op: amend_delegation, id: <delegation id>, under: envelope | reporting (or none), set: {key: value}}]` edits one existing delegation record in place. An existing key's lines are replaced (left alone when the value is already the same), a new key goes after the mapping's last line, and comments stay. The id must appear exactly once. The fields that define a delegation (`id`, `unit`, `from_owner`, `to_owner`, `diff`) are never amended; a new delegation is `op: delegate`. `systems` and `positions` may only grow (the new list must keep every old item): widening a delegated unit is the delegator's decision, carried by a high-tier diff; narrowing it is a reverse diff. `critical_denies` stays `inherited`.
  - `changes.registry: [{op: move_system, system: <id>, from_unit: <unit>, to_unit: <unit>}]` (2026-11-11, from a user organization's move of a system between units) moves a system from one unit's `systems` list to another's, in the style each list is written, comments kept. A delegation of either unit gets the same change in its own `systems` list (out of the old unit's, into the new one's), so a move that touches a delegation is a high-tier diff. The keeper's own `unit` changes in the same diff by `op: set`; a delegation's `positions` list is not changed by this op.
  - `changes.registry: [{op: move_position, position: <name>, from_unit: <unit>, to_unit: <unit>}]` (2026-11-11, from a user organization: a position moved between two units delegated to the same owner, and still counted in the old unit's budget) sets the position's `unit` and moves it from the old unit's delegation `positions` list to the new one's, comments kept. When both units are delegated by the same delegator to the same delegatee, no authority changes hands, so the move is one diff of medium tier: the owner approves its exact text, because the position now falls under the other delegation's envelope and budget. When the delegatees differ, it narrows one delegation and widens another: a reverse diff for the first and a high-tier `delegate` or `amend_delegation` for the second.
  - `changes.registry: [{op: rename_position, from: <name>, to: <name>}]` (2026-11-13, `of_architect`; asked by a user organization's structure keeper when its owner merged two keepers and asked for a name that says what the merged position keeps) gives a position a new canonical name. The old name goes to the end of its `aliases` (a merged position's old name too), and every reference in the registry follows the new name: systems' `keeper`, units' and delegations' `positions`, works' doers and owners, goals' `owner`, the credentials' and tools' `keeper`. The brief file moves to `registry/briefs/<to>.md`, and the position's memory directory moves with it, leaving a link at the old path for the live session until its next start. After the apply, `of apply` renames the live holder's herdr agent and pane from `holder.session`, without a restart; the owner's yes on the diff is the yes this rename of a session needs. The relay resolves aliases, so messages to the old name still arrive. **A grant does not follow an alias**: `of_grants` matches a grant's `subject` exactly, and the new name gets a new grant from its issuer, because an alias the structure keeper writes must not move authority someone else signed. Tier: low on its own; with a mandate change, that change's tier.
  - `changes.registry: [{op: add_unit, id, name, owner, systems: [...]}]` (2026-11-11, from a user organization's company and compliance units) appends a unit to `units:`. The owner is a position, or one the same diff adds (a spawn that needs its own unit). Handing a unit to an agent owner with an envelope stays a delegation (`op: delegate`). A system sits in one unit: one already in another unit comes over by `move_system` in the same diff. The registry after the change passes `of registry check` (units, owners, each position's `unit:`).
  - `mandate_rewrite` may set `owns`, a position's cells `[{system, method, decides}]` (RFC 0007), with a known system and kind of work. The edited registry may not have a cell with two deciders that involves a position the diff sets: the others become doers in the same diff.
  - `changes.agents_md: [{op: insert_line, after: <the start of one line>, text: <the line>}]` inserts a rule into the organization's `AGENTS.md` after the anchor's whole bullet. The anchor must match exactly one line (checked by `of diff check`), and a line already present is not inserted again.
- **`of diff propose --ask "<text>"`** gives the owner's question in his language, standing on its own (AGENTS.md, Asking the founder); a medium or high tier diff refuses to propose without it.
- **Approval** is `of.diff_approved`, given through `of diff approve <id>` or by answering an ask. It is accepted only from the owner's machine and identity.
- **Metric names in `predicted`** are checked against `of_studio`'s catalogue of measures.
