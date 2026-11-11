# Position registry

Status: **v0**, 2026-11-05. Schema agreed between `of_architect` and `of_platform`. Concepts: [`ontology.md`](ontology.md) and [`ontology-a-core.md`](ontology-a-core.md) (position, holder, mandate). Naming: [`systems.md`](systems.md).

The framework defines the schema. Every organization that uses Organic Framework keeps its own instance in its own repository, at `registry/positions.yaml`. This repository holds no organization's registry.

## Where registries live

- One repository per organization (an app of Organic Framework), with its registry at `registry/positions.yaml` and its briefs at `registry/briefs/<name>.md`.
- Each machine lists the organizations it takes part in at `~/.config/of/orgs.yaml`:

  ```yaml
  desk: desk                      # the desk workspace, laid out by `of desk`
  orgs:
    - name: organic-framework-build
      path: ~/organic-framework-build
      workspace: organic-framework  # the herdr workspace label of this organization
      database: of_build            # its own database in the trace store (docs/storage.md)
      paths: [~/organic-framework, ~/organic-framework-build]  # sessions working here belong to it
    - name: acme
      path: ~/acme/org
      workspace: acme
      short: ac                     # optional: the prefix of unit workspaces (ac·shop); default: workspace
      database: of_acme
      paths: [~/acme]
      hold: true                    # optional: `of apply` leaves this organization's panes alone
      drain_from: local             # optional, after a cutover to the organization's own server: local | <url> | [list]
      branches: {storefront: develop}  # optional: a repository's default branch, when OF cannot tell it
  ```

- `branches` (2026-11-11, `of_platform`; the founder: `of import git` read only `main`): `of import git` reads each checkout's default branch. It takes the branch named here for the repository (by its folder name or path), else the remote's default (`origin/HEAD`), else the one of `main`, `master`, `trunk` and `develop` that exists. When none resolves, or several exist with no `origin/HEAD`, it says so for that repository and reads nothing from it. `origin/HEAD` is set when a repository is cloned and does not follow a later change of the remote's default; `git remote set-head origin -a` refreshes it, or name the branch here.

- `drain_from` (2026-11-11, `of_server`, organic-framework 8d4ec61): after an organization's `server:` points at its own server, the relay still presents its holders to the old server named here and delivers the messages queued there, so none are stranded. Remove it by hand once the old queue is empty. It replaces a re-posting script, which would have needed the new server to accept messages on behalf of their senders ([`server-several-machines.md`](server-several-machines.md) §4.6).

- The `of` CLI reads every listed registry. A canonical name and every alias must be unique across all of them.

## Where the name shows in herdr

`of apply` puts a position's canonical name in four places on its live holder:

1. the herdr **agent name** (`herdr agent rename`), the address for `herdr agent focus` and `prompt`;
2. the **pane label** `■ <name> ■` (`herdr pane rename`), shown on the pane border. `of go` strips the markers;
3. the **tab label**, set to the role, when the tab holds that one agent and is not on the desk;
4. the pane **tokens** `system`, `role`, `summary` (`herdr pane report-metadata`, source `of:registry`).

**Units.** When an organization's registry has `units:` (below), its positions are laid out by unit: one workspace per unit, labelled `<short>·<unit>` (for example `ac·shop`), with one tab per system, labelled by the system id, and the system's positions as panes in splits. At most `max_per_tab` (4) panes go in one tab, in a balanced layout (1 full; 2 side by side; 3 one left and two right, or three columns; 4 an even 2×2 grid); more agents of a system go to `<system>·2`, `<system>·3` and so on, blocked and working agents first and parked ones last. A system's keeper (`systems.<id>.keeper`, its single writer) is pinned to the first slot (top left) of the system's first tab; when that tab is full, a parked or finished agent (else the last-ranked one) gives up its place and moves to the overflow. Other placement is sticky: an agent stays in its tab when its state changes, so `of apply` re-lays out a tab only when its set of agents or its shape is wrong. It moves panes, never restarts agents; names and labels stay. Settings: `~/.config/of/layout.yaml` (`max_per_tab`, `three: left+2 | columns`). The desk router sends an evicted unit agent back the same way. Positions whose system is in no unit stay in the organization's workspace as below.

**Workspaces are organizations, plus the desk.** A workspace whose agents all belong to one organization is labelled with that organization's `workspace` from `orgs.yaml`. The desk workspace holds agents of several organizations; `of desk` lays it out, and `of apply` never relabels it.

## File shape

```yaml
tenant: <owner of the tenant>
organization: <organization id>
positions:
  - name: of_platform
    system: organic-framework
    role: platform
    kind: project
    owner: founder
    summary: "step 0 + of CLI v0"
    status: active
    mandate: registry/briefs/of_platform.md
    holder:
      runtime: claude-code
      session: 4f9c2a10-5b3e-4d8a-9c71-2e6f0b8d1a35
      workplace: herdr local, workspace organic-framework, tab platform
    aliases: []
```

**A hash after a space ends an unquoted value** (2026-11-08, from a user organization: `RFC #2338.` in a plain value was read as a comment and the file still parsed). Quote a value that cites a PR or an issue number (`contribution: "Refunds work, RFC #2338."`), and put two spaces before the hash of a comment (`status: active  # parked until 11-13`). `of registry check` reports one space before the hash and a word after it as a problem (the value is cut), and one space with a space after it as a warning. It reads the parser's own end of each value, so a quoted value or a hash inside a word (`C#`) is never reported.

## Fields

| Field | Required | Meaning |
|---|---|---|
| `name` | yes | Canonical name `<system>_<role>` (a project: `<system>_<task>`), matching `[a-z][a-z0-9_-]{0,31}`. Unique across all registries on the machine. Holders change; the name stays. |
| `system` | yes | The system the position answers for or works on. Set as the herdr pane token `system`. |
| `role` | yes | Set as the herdr pane token `role`; the tab label when the tab holds one agent. |
| `kind` | yes | owner, keeper, project, analyst or coordinator ([`ontology.md`](ontology.md), position kinds). |
| `owner` | yes | The accountable owner: who may create, park or close the position (a structure decision, acc-v0 row 9). In a delegated unit it stays the organization's owner (for example `founder`); the position reports day to day to the unit's current owner, derived from `units.<unit>.owner`, so a delegation and its reversal never rewrite the positions (2026-11-08). |
| `summary` | no | One line of at most 40 characters: the current mandate in short. The herdr pane token `summary` and a column in `of who`. |
| `status` | no | `active`, `parked` or `finished`: the state of the position's mandate ([`ontology-a-core.md`](ontology-a-core.md): active, parked, done; finished = done). A plain string. `of who` lists parked and finished positions last. Waiting is not parked: a position waiting on an answer stays `active` and declares `of status waiting --on founder|<position>`; `parked` means its mandate is paused on purpose, and only whoever parked it resumes it. `of pulse` flags a parked position whose holder declared working, or showed working in herdr for a run of 10 minutes or more that did not end in a declared idle, waiting or done (a short reply to a peer is not work on a paused mandate), in the last hour: a note in the relay frame to the organization's structure keeper (its active position of role `org`, else `architect`), typed when the keeper is idle, at most once a day per position, never to the founder (2026-11-06: five positions of a user organization were parked while waiting on asks and never unparked, so the Works check showed their works as lost). |
| `waiting_on` | no | `owner`, a position name (a peer), or `external:<who>` (an outside party, for example `external:kernel-upstream`). Declared runtime state, set by hand for now; the event log ([`events.md`](events.md)) replaces it later. `of who` lists owner first, next to herdr `blocked`, then peer, then external. |
| `note` | no | One line, display only. |
| `throughput` | no | The throughput class of the current mandate, after throughput accounting (Goldratt's TOC: throughput T, investment I, operating expense OE) and goal 1 (a) in [`success.md`](success.md): `raise` (raises the organization's T), `sustain` (keeps the current T: operating production), `expense` (OE or infrastructure), `measure` (measurement and analysis), `coordinate` (distribution and coordination). It describes the mandate, not the position forever, and changes with it. `of who --long` shows it. |
| `serves` | no | The organization whose material the current mandate works on, when it is not the position's own ([`ontology.md`](ontology.md), decision 7: work across organizations is an engagement). The traces of the position's sessions go to that organization's database; this takes priority over the position's own organization. Results cross back only by publication (`of.published`, [`events.md`](events.md)). One session serves one organization. |
| `enrolled` | no | `true`: the position is under steering. Its sessions get the mandate in context at SessionStart (brief, current mandate, the organization's binding rules, the team); later the mandate router (observe first) applies to it. Recording applies to every session regardless. |
| `owner_chat` | no | `true`: the owner converses with this position in its own pane (of_conductor, the founder's one window, 2026-11-10). A question it leaves there for him is read where it is asked, so the asks judge (`bin/of_judge.py`) skips its candidates before the model (`of.agent_question_judged` verdict `skipped`, rule `owner-chat`): no correction typed into its pane, no card in his popup. Only the organization's structure keeper sets the flag, on the owner's word. A position with it still files every decision that is the owner's with `of ask`, and the decisions it relays reach him as asks filed by their owners. |
| `mandate` | no | Path of the brief, relative to the organization's repository. |
| `mandate_exit` | no | The current mandate's exit criteria: `{done_when: "<checkable criteria>", review: YYYY-MM-DD}` (`review` optional). The pulse can close a mandate on it, and the diagnosis uses it for lingering collaboration and unclear mandates ([`diagnose.md`](diagnose.md)). Part of the mandate: a change writes `of.mandate_changed`. |
| `mandate_functions` | no | The management functions the current mandate covers ([`method.md`](method.md), management functions check): a list of `strategy`, `goals`, `operations`, `administration`, `corporate_governance`, `corporate_finance`, `management_accounting`, `marketing`, `hr`, `leadership`, `org_change`, `asset_management`, `community`, `productization`. For the management-function gap. |
| `response_window` | no | Minutes within which the position answers a hand-over offered into its pane ([`handover.md`](handover.md)); default 30. `of pulse` nudges after it and expires the hand-over after four. |
| `contribution` | no | What the position contributes, in a few lines. |
| `holder.session` | for binding | The live holder's agent session id. `of apply` binds the position to the live agent with this session. |
| `holder.runtime`, `holder.workplace` | no | The harness and where the holder runs. |
| `aliases` | no | Older names: pane titles, session names, memory role names. `of go` accepts them. |
| `package` | no | The package and role a position was installed from: `{name: onboarding, version: 1, role: conductor}`. `of init` writes it on every position it takes from the onboarding package (bench/onboarding/package.yaml), so Bench can tell which positions came from a package and which version. Hand-written positions have none. The check does not read it yet. |
| `provisional` | no | A position the structure keeper added before the structure was finished, so that work could start (2026-11-11, from an onboarding: work raised in the head's first hour had no position): `{asked: "<the person's words, verbatim>", at: <UTC>, by: <their conductor>, event: <the of.human_prompt event id>}`. Allowed only for kind `project`, owned by the person's own position, on a yes the person typed in their own conductor's pane; that recorded prompt is its basis and stands in for a diff approval. It holds no credentials, tools beyond the defaults, grants, delegation or unit envelope, and does nothing that is money, secrets, production or irreversible. The structure keeper folds it into the structure by a diff (kept, merged or finished) at the onboarding's structure step, or within the run; `of registry check` refuses one without `asked`, `at` and `event`, or with any of those powers, and warns on one still open after its run's work is closed (`of_platform`). |
| `memory` | no | The scope of the position's auto-memory ([RFC 0006](rfc/0006-position-memory.md) §2.1): `position` (the default), `unit` or `org`. It may also be set on a unit (`units.<id>.memory`) and at the top of the registry, for the whole organization; the nearest setting wins. The directory is `~/.local/state/of/memory/<org>/<position, unit-<unit> or org>/` (mode 700, never committed). `of spawn` launches the position with it (`--settings '{"autoMemoryDirectory": …}'`, `bin/of_memory.py`), and so does `of capacity` on a move or resume. A unit scope for a position in no unit falls back to the position. `of registry check` validates the value at each level. |

Any other field passes through untouched. Organizations may add their own (the first user organization uses `close` and `brief`; its `confirm` on systems is now in the systems map below). A field that several organizations need moves into this table.

## Changing the schema

The schema is an interface other positions read. Propose a change to `of_architect` before making it, then update this document, the `of` CLI and its tests together.

## Units (2026-11-06, `of_architect`)

A unit is a group of systems with one owner: the part of the structure that can be delegated ([`handover.md`](handover.md) §2). Top-level in the organization's registry:

```yaml
units:
  shop:
    name: Shop
    owner: acme_owner            # the unit's owner position (later: the delegatee)
    systems: [storefront, storefront-hosts, payments, forecasting]
```

- A position belongs to the unit of its `system`.
- `of registry check`: a unit id matches the system id rule; `owner` is a position of the organization; `systems` is a non-empty list of systems from the systems map; every system is in at most one unit.
- `of apply` lays out herdr by unit (above, "Where the name shows in herdr"); `of who` shows a `UNIT` column when any position has one.

## Delegations (2026-11-06)

A unit can be delegated to another owner: a position held by an agent or by a person ([`handover.md`](handover.md) §2). The change is a `delegate` structural diff (high tier, the owner's approval), which sets `units.<unit>.owner` and appends a record:

```yaml
delegations:
  - id: del-2026-11-06-platform
    unit: platform
    systems: [k8s, data, obs]          # copied from the unit at delegation time
    positions: [k8s_keeper, obs_keeper] # the positions of those systems at delegation time
    from_owner: acme_owner
    to_owner: platform_owner            # any position: an agent or a person (acme_org's draft)
    envelope:
      grants: [g-…]                     # attenuated from the delegator's; no wider
      budget: {tokens_month: …, usd_month: …}
      risk_appetite: low                # ordered: low < medium < high; a grant's is never above its issuer's
      critical_denies: inherited        # always: the delegator's critical denies stay in force
    reporting:
      rollup: [owner_constraint, throughput_share, issues_open, handovers]
      escalate_when: [ask.blocking, ask_unanswered > 2h, budget_used > 80%, issue.severity >= high, envelope_breach,
                      "prod_change{affects:[storefront, storefront-hosts]}", money, security_incident, irreversible,
                      "window_go{systems:[k8s]}"]
    review: 2026-12-01
    way_back: reverse-diff
    diff: sd-2026-11-06-…               # the diff that made it
```

- **Budget.** `tokens_month` counts fresh tokens (input + cache writes + output, each response once by `message.id`); `usd_month` is the cost at list prices, cache reads included, which is closer to what moves the account limits. Both are optional; `budget_used > N%` fires when either passes N% of its value this month. Counting as in `docs/capacity.md` ("Counting tokens"); added 2026-11-06 on `of_capacity`'s finding that cache reads were more than half of the platform unit's cost.
- The active delegation of a unit is the last record whose `to_owner` is the unit's current owner.
- `of ask` from a position in a delegated unit goes to the unit owner (`to: <owner>`), typed into its pane; it is not in the founder's queue. `of ask --to founder` from such a position goes to the unit owner too, and `of ask` says so in one line (`asked_to: founder` on the event); it goes to the founder at once only when a declared `--tags` matches the delegation's `escalate_when` (`money`, `irreversible`, `security_incident`, a `prod_change` on a listed system), since the unit owner cannot decide those (`escalate_rule` and `delegation` on the event). A guessed tag never skips the unit owner. Nothing is refused (`of_architect`, 2026-11-07). `of pulse` escalates it to the founder on `ask.blocking` or `ask_unanswered > N h|m` (`of.ask_escalated` plus the founder's copy of the question). The other conditions are evaluated by Studio.
- **Escalation tags.** Besides the ask and budget conditions, `escalate_when` takes tag predicates: `prod_change{affects:[systems]}`, `money`, `security_incident`, `irreversible`, `window_go{systems:[...]}`. An ask carries `tags`: set by the agent (`of ask --tags prod_change:storefront+storefront-hosts,money`) or, when it sets none, guessed from the text by the classifier (each guessed tag carries `source: guessed`, the method version and, from `of ask`, the asker's system as its `systems`; an escalation it fires is marked as a guess in `of.ask_escalated`, the founder's copy and Studio; from `tg-v1`, 2026-11-09, a mention negated within three words before it, such as "no production change", is no tag); heuristic asks get guessed tags, and an `of.mandate_check` on a destructive Bash command (`rm -rf`, `DROP TABLE`, `kubectl delete`, a force push, `terraform destroy`) gets `irreversible`. A routed ask escalates as soon as a tag predicate matches (for `{affects|systems:[...]}`, when the tag's systems meet the list, or a declared tag names none; a guessed tag without systems never meets a list). A unit owner that settles an escalated ask itself closes the founder's copy with `of asks answered <the routed ask's id> "what it did"`. Studio shows the condition that fired.
- **`standard_changes`** (2026-11-11, [`rollout.md`](rollout.md) §3) lists the kinds of change in the unit that are standard. A standard change has an exercised rollback, a limited blast radius per step, no money path and no destructive state change. It needs no yes and no calendar slot, and the keeper runs it paced by its guard measure. Every kind of change not listed is normal or irreversible. The list is free text per kind, for example `"CI runner image and cache changes"`, and the unit's owner agrees it with the delegator through the delegation's diff.
- **`risk_appetite`** is an ordered enum `low | medium | high` (anything else, such as `low-medium`, is rejected); grant attenuation compares it by that order.
- `of registry check` validates each record: the unit exists, both owners are positions, the envelope is a mapping with a valid `risk_appetite`, `escalate_when` is set and every entry is understood.

**A unit owner's place.** A position may name `unit: <id>`. The unit's owner (or any position whose system is not in the unit) is then laid out by `of apply` in the unit's workspace, in a tab `owner`. The check requires the unit to exist in `units:`.

## People (2026-11-11, `of_architect`; from `of_server`'s plan for several machines, docs/server-several-machines.md §4.5)

People are part of the organization. A position is a role, and a person can hold it as an agent can.

```yaml
people:
  alex:                       # the id: [a-z][a-z0-9_-]{0,31}, stable; never an email
    name: "Alex"              # how the organization calls the person
    machines: [m-0a1b2c3d]      # machine ids enrolled for this person (of enroll); may be empty
    status: active            # active | left
positions:
  - name: research_keeper
    holder: {runtime: human, person: alex}   # machine: <id> only when the position is bound to one machine
  - name: research_replay
    owner: research_keeper    # an agent working for a person: its owner is that person's position
    holder: {runtime: claude-code, session: …}
```

Rules:
- **The registry holds no personal data beyond a name.** Emails, phone numbers and chat handles stay in the organization's server (identity) and the secret manager. The registry is committed, and it is read by every agent.
- **A person's position follows the same naming** (`<system>_<role>`), mandate, `owns` cells and envelopes as an agent's. The decider of a cell may be a person.
- **An agent that works for a person** names that person's position as its `owner`. Its asks go to that person, through the person's position, and climb to the organization's owner only by the person's envelope or the unit's escalation rule.
- **Invites and enrollment** name a person by id. The server refuses an invite for an id that the registry does not list (`of_server`).
- **A person who leaves** gets `status: left`. Each of their positions gets a new holder or is closed (`swap_holder` or `close` diffs). Nothing about them is deleted from the history.
- **People outside the organization** who are not on OF stay `external:<name>` (doers of works, `governed_by`).
- **Their traces:** a person's machine ships the typed tier. Raw traces go only with that person's consent (docs/server-several-machines.md §4.7).
- **Who does what needs evidence** (the founder, 2026-11-11 15:01Z: "where did this information come from at all? Look at the commits and you will see he is not the one doing it"; a user organization's registry had paraphrased his words "everything except Solana goes into our structure" as "a colleague keeps the Solana bots"). Every claim that a person holds a position, keeps or decides a system, or is a work's doer (`holder.person`, `keeper`, `governed_by: external:<who>`, `doers: [external:…]`) carries `evidence:` beside it: the authorship it rests on (commits or merged PRs in the system's repositories, with the window, for example `"git authors 2026-10-01..11-10: 41 of 47 commits"`), or the person's own word or the owner's word as an ask id. The owner's words are quoted verbatim, never paraphrased.
- **A flag on a registry fact is tracked until it is closed** (2026-11-11: a user organization's system architect had flagged the same false claim among 25 unconfirmed facts, and the flag stayed in a report). Whoever finds a fact in a registry false or without evidence (a reviewer, an analyst, the check) files it as a hand-over to the organization's structure keeper, `of handover propose <keeper> "Registry fact: <the fact>" --done-when "fixed with its evidence, or the owner's answer recorded"`, never only in a report. The pulse then follows it like any hand-over.
- **The people map comes before any person's position.** The structure keeper builds it from the repositories (authors by system, by `of import git` and `of import github`) and has the owner confirm it on one card before a diff gives a person a position.
- `of registry check` validates ids, `status`, that every `holder.person` names a listed person, and that a person's machine ids are enrolled (`of_platform`). It warns on a person-held claim without `evidence:` (`of_platform`).

## Works (2026-11-06, docs/works.md step 1)

A top-level `works:` map in the organization's registry: one entry per unit of intended change with a checkable end (an RFC and its stages, a project position's mandate, an incident, a window, a migration). The structure keeper is the single writer. Studio derives each work's state from events (docs/works.md §3); nobody reports progress by hand.

```yaml
works:
  acme-checkout-retries:                     # the id: [a-z0-9][a-z0-9._-]{1,63}, stable
    title: "Order numbers and payment retries for checkout"   # one line, in the owner's words
    kind: rfc                                # rfc | project | incident | window | migration
    doc: "storefront:docs/rfc/checkout-retries.md"   # repository:path
    systems: [storefront]                    # registry system ids
    owner: acme_owner                        # optional; absent = the unit owner of its systems
    doers: [storefront_checkout, external:dana]   # positions, or people outside the registry as external:<name>
    done_when: "part 1 merged and flipped on US then FRA"
    due: 2026-11-20                          # optional dates
    review: 2026-11-13
    state: parked                            # optional: only done | parked, set by the keeper; else derived
    stages:                                  # optional
      - id: part-1                           # the token PRs and branches use (t7, p2, part-1)
        title: "Order number claimed before payment"
        done_when: "PR #2683 merged through Mergify; the flag on US then FRA"
        state: open                          # done | open
    match:                                   # optional: how evidence attaches
      prs: [2218, 2683]                      # PR numbers in the doc's repository
      branches: ["claude/retry-*", "deploy:claude/*checkout-v2*"]   # head branch globs: bare in the work's repositories, <repo>:<glob> in that one
      paths: ["storefront:docs/rfc/checkout-*.md"]   # <repo>:<glob>
      doc: "storefront:docs/rfc/checkout-retries.md"
      tags: [payments]                        # ask tags
```

**A development loop** (RFC 0005 §4, 2026-11-07) is a work's optional `loop:` with `target` (`metric` and `money` required, `stake`, `throughput_class`), `measures` (each with a `name` and its `to_money` chain), `ladder` (rungs, cheapest first, each a unique `rung` id with `cost`, `latency` and an `accept` criterion; `production: true` on shadow and higher rungs, which the production-checkpoint rule counts; `since`, the date the rung became available, so runs before it never count as skipping it), `checkpoint_days`, `wip_arms` and `babysit_model`. `target.better` is `higher` (the default) or `lower`. A work with a loop needs `owner` and `doers`, since the loop rules route to them (`work_owner`, `doer`; of_field-analyst's input contract, docs/diagnose.md §4.1). It may live instead in a `loop.yaml` beside the work's doc, never in both. `of registry check` validates it (a rung without `accept` and an unknown key are warnings). The hypotheses, runs and decisions are events (`of loop`; docs/events.md).

**A standing work** (2026-11-10, `of_architect`; from a user organization: the founder wants some roles to improve without end) is a work with `standing: true`. It has no final end, so its end condition is its loop.
- It needs a `loop:` with a `target` and `checkpoint_days`. `done_when` may be left out.
- `review` is the next checkpoint. Each checkpoint records the target's trend as a loop decision.
- It is never derived as done. It ends only by the owner's decision: the keeper sets `state: done` or `parked` and records the ask id.
- It is not a goal. The goal stays the owner's outcome (for example `g-throughput`); the standing work is the lever one position keeps working on for it.
- A position's mandate still ends: its `mandate_exit` stays checkable, and the standing work carries on under the next mandate.
- A plateau over checkpoints is the loop-plateau rule's question to the owner: keep, change the objective, or stop.

Other keys (notes such as `why`, `last_progress`) are allowed and ignored by the checks. `of registry check` validates the id, `kind`, the systems, `owner` and `doers` (a position here or `external:<name>`), `state`, the dates, the stages (unique ids, `done_when`, `state`) and the `match` types. When a work's systems span more than one unit, `owner` is required (the unit owner would be ambiguous). A doer whose position is parked or finished is a warning, not a problem: the work has lost its doer, and Studio shows it. `of import github` names the works each PR matches (`works` on `of.work_record` and `of.pr_state`): a number in `prs` within the doc's repository; a `branches` glob, `<repo>:<glob>` in that repository or a bare glob in the doc's repository or one where the work's systems live; a merge queue's batch PR (head `mergify/merge-queue/…` or `gh-readonly-queue/…`) matches no work, since it copies PRs already counted; a `paths` glob, `<repo>:<glob>` over the PR's changed paths or a plain glob over `<repo dir>/<path>` as systems' paths are written; or a change to the `doc` file itself. `of ask --work <id>` and `of status --work <id>` link an ask or a declared state to a work explicitly (the id must exist in the organization's `works:`); otherwise evidence attaches by `match`. Studio shows `external:` doers as people, not positions.

## Goals (2026-11-07, `of_architect`; implemented once an owner sets goals)

A top-level `goals:` map: the few outcomes the organization's owner wants, above the works. A work names the goal it serves, so the owner sees what the organization works toward and what matters first, not a flat list of works. Goals are the owner's: an agent proposes them, and they enter the registry only with the owner's yes, whose ask id each goal records.

```yaml
goals:
  g-example:                                  # the id, same rule as a work's (illustration only)
    title: "<the outcome, one line, in the owner's words>"
    rank: 1                                   # unique within the organization: 1 matters most
    measure: "<how success is seen>"          # free text
    metric: null                              # optional: a Studio measure name when one exists
    target: "<the level that counts as success>"   # optional
    by: 2026-12-31                            # optional date
    owner: acme_owner                         # who answers for it; usually the organization's owner
    state: active                             # active | achieved | dropped
    decided: <ask id>                         # the owner's yes on this text; required
works:
  mt-nonces-wallets:
    goal: g-example                           # optional: the one goal the work mainly serves
```

Rules:
- Few: more than 7 active goals is a warning; a goal list is a choice, not an inventory.
- One goal per work. A work that serves two names the one it serves most; a work with no goal is shown apart in Studio, "no goal", which is a question for the owner, not an error.
- An agent never adds, ranks or rewrites a goal without the owner's yes on that exact text (an ask); the structure keeper writes it and records `decided`.
- Asks and declared states reach a goal through their work (`--work`). Studio groups works, their open asks and their progress under each goal, in rank order.
- `of registry check` validates the ids, `rank` (unique integers), `state`, `decided` (present), the dates, and each work's `goal` (an existing goal).

## Tools (2026-11-11, `of_tools`; method in [`tools.md`](tools.md))

A top-level `tools:` catalogue names the tools the organization's agents may use: MCP servers, plugins and claude.ai connectors. Positions and units name tools from it. OF generates each session's tool configuration from the position's list at every start, whatever Claude account the session runs on (`of_tools.launch_parts`; tools.md §4).

```yaml
tools:
  <id>:                       # [a-z][a-z0-9_-]{0,47}
    kind: mcp-stdio           # mcp-stdio | mcp-remote | plugin | connector
    command: [program, args…] # mcp-stdio
    env: {NAME: value}        # mcp-stdio; names and plain settings only, never a secret value
    url: https://…            # mcp-remote and connector; a plugin's own server, to allow it by URL
    headers: {NAME: value}    # mcp-remote; names and plain settings only, never a secret value
    plugin: name@marketplace  # plugin
    credential: {id: <credential id>, env: NAME | file: NAME}   # from the credentials catalogue below
    account_bound: true       # its sign-in lives in one Claude account (always true for a connector)
    default: true             # every position of the organization has it
    acts_as: "…"              # the identity the tool acts as
    scope: read               # read | write | act-as-person, or a list of them
    keeper: <position>        # owns the integration, its credential and its rotation

units:
  <unit>: {tools: [<id>, …]}  # the unit's defaults

positions:
  - name: <position>
    tools: [<id>, -<id>]      # adds to the defaults; -<id> removes one
```

- A position's tools are the catalogue's `default: true` tools, then its unit's, then its own; `-<id>` removes one given earlier.
- **A tool names its credential by id** from the Credentials catalogue (below), with how the server takes it: `env:` the value, `file:` the path of a temporary file. A credential with a `ref` reaches only the server's own process through `of secret run`; one still kept in a file is read by the server itself, and the command names its path. A tool never carries a vault reference or a value. The position must list the credential too.
- **`scope: write`** names the mandate line that needs it, in a comment. **`act-as-person`** (a tool that sends, posts, pays or signs as a person) needs the owner's yes, cited by its ask id in a comment.
- **`account_bound: true`**: the tool works only on the Claude accounts where it is signed in. A move goes only to such an account (`of capacity`).
- `of tools check` (inside `of registry check`) validates: ids, `kind` and the fields it needs, `scope` values, `acts_as`, `keeper` (a position of the organization), each `credential.id` (in the Credentials catalogue), a file credential's path in the command, no secret-shaped value in `env` or `headers`, no `vault` key, an OAuth `mcp-remote` marked `account_bound`, and every tool a position or unit names.

## Plugins (2026-11-11, `of_architect`; docs/plugins.md)

An organization's choices of the outside products that hold its data, so every machine and colleague of the organization uses the same one. A machine's own choices live in `~/.config/of/plugins.yaml`; the organization's win for its data.

```yaml
plugins:
  secret_stores:
    name: 1password          # 1password | bitwarden | vault | aws-sm | gcp-sm | sops | keychain (docs/agent-secrets.md §13.4)
    chosen: "29ea1baa"       # the owner's ask id; required
    settings: {}             # the implementation's own settings, never a secret value
```

- **Absent means no product chosen.** For `secret_stores` that means files: credentials under the credential roots, named by `paths` (Credentials, below), until the owner chooses at the first need (docs/agent-secrets.md §13.2).
- **A credential's `ref` names its store** in the store's own form (`op://`, `bws://`, `vault://`, `aws-sm://`, `gcp-sm://`, `sops://`, `keychain://`); `of registry check` refuses a `ref` whose store is not the organization's chosen one.
- `chosen` is required: the store is the owner's decision, never a default.

## Credentials (2026-11-11, `of_architect`; from a user organization's structure keeper through `of_server`)

Every agent runs as one OS user, so a file's mode keeps no position from another position's credentials. The guard denied credential files by name and missed some names (`*.header`, `*.password`, until 2026-11-11). A credentials catalogue lets the guard deny by default instead.

```yaml
credentials:
  grafana-viewer:
    paths: ["~/.config/acme/grafana/viewer.token"]   # files on the machine; never a value
    acts_as: "Grafana service account agents-viewer, role Viewer"
    keeper: obs_keeper            # owns its scope and its rotation
positions:
  - name: obs_keeper
    credentials: [grafana-viewer]
```

Rules:
- **Default deny under the organization's credential roots.** The root is `~/.config/<org>/` unless the registry names its roots in a top-level `credential_roots:` list. An organization whose id is `acme`, for example, may keep its files under `~/.config/acme-inc/`.
  - A root holds credentials only. When a folder also holds working files (worktrees, environments, logs), the registry names its credential subfolders as roots, not the whole folder, or the organization moves the working files out.
  - `~/.config/of/` is the platform's configuration, not an organization's root. The guard's name rules cover it (docs/guard.md G1).
  - A position may read or use a file there only when it lists a credential whose `paths` match the file. The credential's keeper may too.
  - Using a file includes passing its path to a command, for example `--kubeconfig`, `ssh -i` or `curl -H @file`.
  - A file under a root that no credential names is denied to every position. The guard reports it to the structure keeper as an undeclared credential.
- **Paths outside the roots**, for example an SSH key under `~/.ssh`, are protected only where a credential names them. Each is denied to the positions that do not list it.
- **A credential is listed only by positions of its own organization.** Another organization's roots are denied the same way.
- **Names, paths and references only.** The catalogue never holds a value. A credential kept in 1Password is named by its secret reference (below); one still kept in a file, by its `paths`.
- **Secret references** (2026-11-11, `of_server`; [`agent-secrets.md`](agent-secrets.md), agreed by `of_architect`, msg `01a1cab8`):
  - `ref: "op://<vault>/<item>/<field>"`, 1Password's own form, which `op read` and `op run` take as it is. The vault is `<org>-<system>` in plain ASCII (`acme-obs`, `acme-k8s-read`): a reference allows letters, digits, `-`, `_`, `.` and spaces, and any other character needs an ID.
  - `rotate_days`: how often its keeper rotates the value; 90 when absent.
  - A credential needs `ref` or `paths`, or both while its file is being retired.
  - The organization's broker serves a `ref` to the holder of a position that lists or keeps the credential (`of secret run`; docs/server.md, `POST /v1/secrets/use`), read with that position's own service account.
- **What it is and is not.**
  - The guard stops accidents and use by the wrong position. It does not stop a process that wants the file, because every session runs as the same user.
  - `of spawn` writes the same lists into the session's own Claude Code deny rules (`permissions.deny`, `Read` and `Edit` of each path; `bin/of_spawn.py` `deny_rules`), in a settings file under the state folder passed with `--settings` (`spawn/<session>.settings.json`, mode 600). A file, because a fresh pane's shell takes at most 1,024 bytes a line and 26 paths made a 4.9 KB command line. Each layer switches with the guard's own (G14), so the two deny together:
    - once `of_credentials.MODE_NAMED` is `deny`, every credential the position neither lists nor keeps;
    - once `of_credentials.MODE_ROOT` is `deny`, the whole of each root the position uses nothing under: another organization's roots, and its own when it lists nothing there.
    - While a switch warns, its layer writes nothing (the first user organization's root is also its working folder).
    - Claude Code checks a deny before the permission mode, so the rules hold under `--dangerously-skip-permissions` too.
    - They also cover the file commands Claude Code recognizes in Bash (`cat`, `head`, `sed`, redirections). They do not cover a script that opens the file itself.
    - A deny beats an allow in Claude Code, so a root the position uses a file under cannot be denied with that file carved out. That part stays with the guard (G14).
    - Moves and resumes (`of capacity`) do not write these rules yet.
  - The real boundary is a separate OS user per organization or per position. That is the always-on agent server's design (`of_agent-server`).
- **Source practice:** least privilege and fail-safe defaults (Saltzer and Schroeder, "The Protection of Information in Computer Systems", Proc. IEEE, 1975; NIST SP 800-53 Rev. 5, AC-3 and AC-6).
- **`of registry check` validates the catalogue:**
  - credential ids;
  - each credential's `ref` or `paths`; a `ref` is a secret reference in the allowed characters, its vault is the organization's own (`<org>-…`), and no two credentials name one item; `rotate_days` is a whole number;
  - that each keeper and each position that lists a credential is a position of the organization;
  - that no two credentials claim one path;
  - that `credential_roots` is a list of paths under the home directory.

  `of_server` builds the check and the guard rule (docs/guard.md), with a replay of recent sessions before the deny goes live.

## Grants (proposal, 2026-11-05)

Status: **v0, accepted** by `of_architect` (2026-11-05), after RFC 0001 §5.0. Code: `bin/of_grants.py`, tests `tests/test_grants.py`. Issue and revoke with `of grant issue|revoke --org …` (tenant only: it signs with the tenant key and refuses a grant whose delegation chain does not verify); `of registry check` verifies every grant and its chain; of-server answers `POST /v1/grants/verify`. Grants are what a position may do, at which autonomy tier. The guard (bounds), the relay (relayed authority), cross-organization messages and automatic changes all read them from here. Authority is never read from message text.

### Where they live

- `registry/grants.yaml` in the repository of the **subject's** organization. A grant across organizations (for example, a position allowed to message another organization) lives with the subject and names `to_org`. The receiving organization's guard reads the issuing organization's grants through the same `orgs.yaml` list.
- **A reply needs no grant** (2026-11-09, `of_architect`, on `of_server`'s proposal). A reply goes through without one when all of these hold:
  - its `reply_to` names a message from the other organization that was delivered to the replier at most 7 days before;
  - it goes only to that message's sender.

  Such a reply carries no authority: the relay marks it as a reply and resolves no grant in it. It cannot start a new thread with any other position.
- Grants are append-only in practice. A grant is revoked by adding `revoked`, never by deleting the entry, so the history stays in the file and in the event log.

### Shape

```yaml
grants:
  - id: g-2026-11-05-001             # unique within the organization; referenced by events and messages
    subject: of_platform             # the position the grant is for
    issuer: tenant                   # tenant, or a position that holds a grant to issue grants of this scope
    tier: act_and_report             # observe | propose | act_with_approval | act_and_report
    scope:                           # every listed dimension must match; an omitted one is unrestricted
      tools: [Bash, Edit, Write]
      repos: [~/organic-framework, ~/organic-framework-build]
      paths: ["~/organic-framework/**"]
      hosts: []                      # ssh / kubectl targets; empty = none
      systems: [organic-framework]   # resolved through the system map
      commands: ["git *", "python3 *", "herdr *"]   # shell patterns, heuristic (RFC 0001 §5.7)
      to_org: null                   # set for grants that cross an organization boundary
      to_positions: null             # optional, with to_org: the positions it may write to, '<org>:<position>' patterns
                                     # (fnmatch), e.g. [organic-framework-build:of_platform]; absent = any position in to_org.
                                     # The relay checks it (of_server, 6635d94); `of send` picks such a grant only for a
                                     # matching target (2026-11-06, the founder's 22ed05e1)
      data: [finance]                # owner-only data classes: observe reads, act_* writes (events.md, Finance facts); omitted = none
    expires: 2026-12-05T00:00:00Z    # optional; absent = standing until revoked
    counter: null                    # optional: the number of uses allowed (ontology E.16)
    effect: allow                    # allow | deny
    critical: false                  # true: the guard fails closed for this grant; on a deny, it is decisive
    provenance:
      kind: standing                 # standing | per_step | relayed
      chain: []                      # for relayed: the event ids of each hop, back to the issuer
      source: "founder in chat, 2026-11-05"
    note: "build and test the of CLI"
    revoked: null                    # or {at, by, reason}
    signature: "ssh-ed25519:U1NIU0lH…"   # SSHSIG over the canonical JSON of every field except `signature`
```

**Contacts** (2026-11-11, `of_server`; docs/guard.md G16). `scope: {contacts: ["<one e-mail address or one host>"]}` on an allow grant the tenant issues to one position lets that position contact that one regulator or authority, which the guard otherwise denies. Exactly one contact per grant, never `subject: "*"`, never on a deny; another address at the same authority needs its own grant. Issued after the founder's yes on an `of ask`, which the grant's `note` names.

**Credentials, for break-glass** (2026-11-11, agreed by `of_architect`, msg `01a1cab8`; [`agent-secrets.md`](agent-secrets.md) §5.6). `scope: {credentials: [<one credential id>]}` on an allow grant the tenant issues to one position, with `expires` at most 24 hours after it is issued, lets that position use that one credential through the broker although it does not list it. Exactly one credential and one position per grant, never `subject: "*"`, never on a deny. **Not built yet**: until it is, the broker refuses such a use.

**Authorities** (2026-11-11, `of_architect`; docs/guard.md G16). An organization lists the regulators and authorities it answers to (where it is licensed, its tax office) in a top-level `authorities:` list of its registry, reviewed by its structure keeper like the rest of its structure:

```yaml
authorities:
  - name: Free-zone licensing authority
    contacts: [licensing.example, officer@mail.example]   # hosts (a host covers its subdomains) or e-mail addresses
    evidence: "trade licence 123"                     # the licence or record that makes it this organization's
```

G16 treats every listed contact as an authority for every session on the machine, beside government domains and the framework's own list (`bin/rules/authorities.txt`, `of_server`'s data). `of registry check` validates the shape: a name, a non-empty list of hosts or e-mail addresses, and evidence.

### Rules

- **Signature.** An Ed25519 signature in OpenSSH's SSHSIG format (`ssh-keygen -Y sign`, namespace `of-grant`) over the canonical JSON (sorted keys, no whitespace) of the entry without `signature`. The private key is the tenant's grant key. Since `of grant rekey` (the founder, ask `0fd223f2`, 2026-11-11) it lives only in the owner's 1Password, and `of grant issue|revoke` signs through 1Password's SSH agent, which asks for Touch ID (`ssh-keygen -Y sign -f <pinned public key>`, the private half held by the agent); `of grant` refuses to sign from an agent's session or a service. Until then it was a file (`~/.config/of/grant_ed25519`), which `rekey` removes after re-signing every grant. The legacy HMAC signature is no longer trusted. Verifiers trust only **pinned** public keys: the tenant's own `.pub` on the client, `trusted_grant_keys` in each server's `server.yaml`. A public key listed in an organization's repository is never a trust anchor, because anyone who can write to the repository could add one. A server can therefore verify a grant but never forge one. **Legacy:** `hmac-sha256:` signatures (HMAC under `HMAC(master, "of/grant/v1")`) stay valid until re-signed, but only where the master key is present, i.e. on the tenant's machine; servers outside it hold no master. The secret tags of RFC 0002 use a separate key, `HMAC(master, "of/secret/v1")`, which grants no authority; a server outside the tenant's machine gets only that derived key (`of server export-secret-key`).
- **Validity.** A grant counts only if its signature verifies, it has not expired, it is not revoked, and its counter is not exhausted. `of registry check` reports invalid grants; an unsigned or tampered entry is never authority.
- **Effect.** A grant allows by default; `effect: deny` makes it a denial at its scope. A non-critical deny takes part in the ordinary order: the more specific grant wins, and at equal specificity a deny is more restrictive than any allow. Only `critical: true` on a deny overrides everything it applies to.
- **Matching** (not "the most permissive wins", which is creeping privilege). For an action, among the subject's valid grants that apply:
  1. a critical deny always wins;
  2. otherwise the **most specific** scope wins: the longest literal match by repository, path, command and the other dimensions, as in routing and firewall rules;
  3. between equally specific grants, the **more restrictive** wins (deny, then observe, propose, act with approval, act and report).
  No applicable grant means "missing grant" (`of.mandate_check` with `decision: escalate`; the mandate router routes, it does not block, RFC 0001 §5.7). A broad grant therefore never overrides a narrow restrictive one.
- **Delegation is attenuation only** (as in capability systems: macaroons, UCAN). A position may issue grants only under a grant whose scope includes `grants: {depth: N}`, the right to issue. A derived grant:
  - stays within the issuer's scope in every dimension the issuer limits;
  - has a tier no higher than the issuer's;
  - expires no later than the issuer's grant;
  - carries provenance as a chain back to the tenant.
  The issue right itself is delegated only while `depth` allows it, 1 unless stated otherwise. In ontology terms, a unit's owner, possibly an agent, divides the envelope it received from above, within the unit's risk appetite. `of registry check` verifies each chain.
- **Relayed authority.** A message that claims authority carries a grant id. The platform resolves it and annotates the message as verified or as an unverified claim before the receiving agent sees it.
- **Events.** `of.grant_issued` and `of.grant_revoked` carry the grant id, subject, issuer, tier, scope digest and expiry. Uses of counted grants are `of.mandate_check` events with the grant id.

### Organization rules (2026-11-09, `of_architect`)

An organization's own policy on what its positions may run ([`guard.md`](guard.md) §O) is a set of **deny grants** in its `registry/grants.yaml`. It is never a separate unsigned file: a rule that the positions it limits can edit is not a rule. A user organization asked first (its structure keeper through `of_server`, 2026-11-08).

```yaml
  - id: g-2026-11-09-001
    subject: "*"                       # every position of the organization, and its sessions without a position
    except: [runners_keeper, runners_coordinator]   # signed with the grant, so an exception cannot be added by hand
    issuer: tenant
    tier: observe
    effect: deny
    critical: false                    # false: the guard warns; true: it denies
    scope:
      kubectl: {verbs: [exec, cp, attach, debug, port-forward], namespaces: [runners-jobs]}   # the O1 matcher's keys
    note: "Runner work goes only through the cbx gate (founder, ask ac3b43dc, 2026-11-06)"   # shown to the agent
```

- **Subject set.** `subject: "*"` with `except:` applies to every position of the organization except those listed, and to its sessions without a position (they get no exception).
- **Scope `kubectl`.** It takes the O1 matcher's keys: `verbs`, `pods`, `namespaces`, `admin`. Every key given must match. `allow_exec` (2026-11-11) lists the execs that stay out of the rule: `[{argv: [/usr/sbin/sshd, -i], pods: [runner-0]}]`, the exact command after `--`, into a pod matching `pods` when given.
- **Mode.** A non-critical deny warns: the agent sees `note` and the call runs, recorded as `of.mandate_check`. A critical deny denies. A rule starts in warn. It moves to deny with a new grant and the revocation of the old one, once the replay shows that the live flows have moved.
- **Only the tenant issues and revokes them, in a plain terminal.** Today `of grant` says "tenant only" but does not check who runs it. The guard therefore denies, from every agent session, as a critical framework rule:
  - `of grant issue` and `of grant revoke`;
  - any tool write to a `registry/grants.yaml`.

  Rules are structure (acc-v0 row 9). The structure keeper proposes the exact `of grant issue` command. After the owner's yes, it files the command as a task for the owner's hands.
- **A deny ends only by a signed revocation.**
  - The guard keeps the deny grants it has verified. It keeps applying one that disappeared from the file without a `revoked` entry, and reports it.
  - `of registry check` reports a grant issued (`of.grant_issued`) and not revoked that is missing from the file.
  
  Deleting the entry therefore does not lift the rule.

## Systems map (proposal, 2026-11-05, `of_architect`)

Each organization's registry may carry a top-level `systems:` map. It is the functional layer of the model (RFC 0001 §5.2) and the source of the system DSM.

```yaml
systems:
  storefront:
    name: Storefront, the online shop
    kind: target            # target | enabling | creating
    parent: shop            # the supersystem, if the system is a proper part of one
    keeper: storefront_operator
    repos: [storefront]     # repositories and the paths in them, when one repo holds several systems
    paths: ["storefront/services/web/**", "storefront/services/checkout/**"]
    governed_by: self       # self | external:<who> (an independently governed system)
    lifecycle: live         # live (default) | unsupported | retired
    runs: {where: "k8s shop/storefront; hosts us-1, fra-1", evidence: "deploy/k8s/storefront/values.yaml"}   # or {where: nowhere}
    money: "P&L line: storefront sales revenue"   # a P&L line, a cost line for an enabling system, or the goal it serves
    confirmed: {keeper: "<ask id>"}   # fact -> the owner's ask id (keeper, governed_by, lifecycle, boundary)
    needs: [research, model, build, verify, operate, measure, secure]   # kinds of work the system needs (RFC 0007 §2.3)
    # personal_data: {whose: "customers, staff", where: "PostgreSQL in the cluster; Hetzner, Germany", evidence: "record of processing, row 3"}
    #   only on a system that holds personal data; absent means none known
    depends_on:
      - system: catalogue
        interface: product catalogue (Catalogue PG)
        kind: uses           # uses | interacts | enabled_by
      - system: k8s
        kind: enabled_by
        observed_by: runtime # optional: cochange | runtime | both, the basis on which the edge is known
        evidence:            # optional: where the runtime basis is seen (a string or a list of strings)
          - "deploy/k8s/storefront/values.yaml: cluster runtime"
```

Rules:
- `keeper` names one position: the single writer.
- **Evidence and cells** (2026-11-10, [RFC 0007](rfc/0007-system-architect-and-cells.md)): the system architect owns the map.
  - `runs` says where the system runs, with evidence from the platform keepers.
  - `money` says how the system meets the money.
  - `confirmed` gives the owner's ask id for each governance fact. A comment such as "(founder, date)" confirms nothing. `of registry check` warns on a live system whose `keeper`, non-`self` `governed_by` or non-default `lifecycle` has no ask id.
  - `needs` lists the kinds of work the system needs, from the default vocabulary or the organization's `methods:` map: `research`, `model`, `build`, `verify`, `operate`, `measure`, `account`, `secure`, `data`.
  - A position's `owns` lists its cells as `{system, method, decides}`. At most one position decides a cell.
  - `personal_data` (2026-11-11, proposed by a user organization's system architect through its structure keeper) marks a system that holds personal data, so the organization's privacy watcher finds such systems in the map.
    - `whose` names categories of people, for example "contractors, shareholders", never a person or their data. The registry still holds no personal data beyond a name (People).
    - `where` says where the data sits, with the provider and its country or region. Transfers across borders are among the first things data-protection law asks about.
    - `evidence` is a string or a list of strings: the row of the organization's record of processing that covers the system (GDPR Art. 30, or the local law's equivalent).
    - The map only marks the system. The categories of data, purposes, retention and recipients stay in the record of processing, kept by the position that keeps it, and are not copied here.
    - Absent means none known. A suspicion not yet checked says "(not checked)" in `whose`.
    - The system architect writes it, as it writes `runs`. When it marks a new system, it tells the privacy watcher in one message. `of registry check` validates the shape.
- `depends_on` edges are declared by the structure keeper. Logical coupling (systems changed together in one work record) is measured, not declared, and is shown beside the declared edges.
- `observed_by` says how an edge is known. Co-change in merged PRs cannot see operational interfaces between systems in different repositories (RPC endpoints, buckets, image registries, DSNs), so an edge with `observed_by: runtime` and no co-change is "runtime only" in Studio, not "declared, not observed". `evidence` names where the runtime basis is seen, such as configuration references.
- `paths` resolve the case where one repository holds several systems. Every path belongs to at most one system.
- `governed_by: external:<who>` (2026-11-07, from a user organization): the system is kept outside the organization by the named person or party. It counts as kept: the keeperless rule never asks the organization for a keeper of it, and Studio shows it as "kept by <who> (external)". `keeper` stays empty or names the position that talks to the outside keeper.
- `lifecycle` (2026-11-07, from a user organization): `live` (the default) runs and is kept; `unsupported` exists but nobody maintains it and it does not run (code may still be built in a shared workspace); `retired` is gone or archived. The structure keeper sets it on the owner's word, with the date and ask id in a comment. Rules that ask for a keeper or an owner apply only to `live` systems; changes that touch a non-live system are shown, not flagged. `of registry check` validates the value.
- `confirm` (2026-11-06, from a user organization): what about the system still awaits the owner's confirmation. A list of `keeper` (who keeps it), `boundary` (its grain and what belongs to it: split, merge, paths) and `live` (whether it runs at all); `true` stays valid and means unspecified. `doubt` is the free-text question beside it. Studio marks issues on such a system with what is unconfirmed (for example "keeper unconfirmed"); the owner's confirm pass removes the value once answered.
- `of registry check` validates the map (built): system ids; `kind`; that each `parent` exists, with no cycles; that each `keeper` is a position of the organization; `governed_by`; that every edge's target exists, with a valid kind, a valid `observed_by`, `evidence` as a string or a list of strings, and no self-edge; that no path is claimed by two systems; and that no repository is claimed whole by two systems without `paths`. Paths are globs (`**` crosses directories; `*` and `?` stay within one). When the repository is local (under the organization's `paths` in `orgs.yaml`), the check expands each glob against the files on the repository's GitHub default branch (`gh api …/git/trees`, read-only; `--ref local` or no network: the local `git ls-files`, since a local clone may be days old): it flags any file claimed by two systems and any pattern that matches no tracked file. Otherwise it flags two patterns that can match the same path.
