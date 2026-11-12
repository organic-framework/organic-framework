# Works: following the work, not the agents

Status: **Proposal**, 2026-11-06, by `of_architect`, at the founder's request: "a project screen in Studio by RFCs: who is doing which project; follow the works, not the agents".

## 1. Why

The owner's surfaces so far are about positions:
- `of who`, the desk and the asks popup show who waits, who is stalled and who asks;
- Studio's Inbox, Waits and Owner views do the same in numbers.

An owner, though, thinks in works: "the spam-trigger RFC", "storefront core v2", "moving the journals to ClickHouse". One work often spans several positions, and one keeper carries several works. Today the owner has to rebuild the picture of the works from what the agents say. A **work** makes that picture a projection.

## 2. The object

A work is a unit of intended change with a checkable end. It covers:
- an RFC and its stages;
- a project position's mandate;
- an incident, a window, or a migration.

| Field | Meaning |
|---|---|
| `id` | Stable, e.g. `acme-spam-triggers`, `of-rfc-0001` |
| `title` | One line, in the owner's words |
| `kind` | `rfc`, `project`, `incident`, `window`, `migration` |
| `doc` | The RFC or brief: a repository and path |
| `systems` | The systems it changes (registry ids) |
| `owner` | The accountable position. For a work inside a delegated unit, it is the unit owner |
| `doers` | The positions doing it. For a work of kind `project`, this is usually its project position |
| `stages` | Optional: the RFC's stages or phases, each with `done_when` |
| `done_when` | A checkable end, as for hand-overs (`docs/handover.md` §1.1) |
| `due`, `review` | Dates |
| `match` | How evidence attaches to the work, all optional: branch globs, PR title patterns, the RFC's path, ask tags |

Works live in the organization's registry, under a top-level `works:` map. The structure keeper is their single writer, as for positions. A position of kind `project` is one work's doer: the work carries the end and the stages, and the position is who does them.

## 3. State is derived, not reported

No agent writes "status: 60%". The work's row is computed from events that already exist:

| Shown | From |
|---|---|
| **Stage** | The last stage whose `done_when` evidence is present: a merged PR matching the stage, or a stage note written by the structure keeper |
| **Moving** | Merged PRs, commits and active turns of the doers on the matched branches or paths: the last progress and a 7-day count. Progress counts commits on matched branches and open-PR activity (pushes, reviews), not only merged PRs, so works by external doers and long-running branches show as moving (`of_architect`, 2026-11-06) |
| **Waiting on** | The doers' declared waits and their open asks tagged or matched to the work, for example "waits on you for 2 h: Wazuh window go" |
| **Hand-overs** | Open hand-overs whose `scope` matches the work |
| **Cost** | The doers' spend on the work's sessions (`of_capacity`, by `message.id`), this week and to date |
| **Risk** | Diagnose issues on the work's systems or doers. Past `due` or `review` with no later progress shows as `lingering` |

The state of a work:
- `proposed`: the doc exists and no stage has started;
- `active`: there was progress in the last 3 days;
- `waiting`: the doers' waits block it;
- `quiet`: no progress for 3 or more days and no declared wait;
- `review`: the last stage is done, and the owner has not closed it;
- `done` or `parked`: set by the structure keeper.

## 4. Bootstrapping without new work for agents

- **RFCs:**
  - the structure keeper lists the organization's RFC files (in the first user organization, about 50 in its main repository, plus the other repositories);
  - each becomes a work proposal, taking `title` from the H1 and the stages from the `Status:` line and the phase headings;
  - the keeper confirms the list with the owner in one pass, keeping the live ones and parking the rest.
- **Project positions:** each active position of kind `project` (`<system>_<task>`) becomes a work. Its brief's mandate gives `done_when` and its review gives `review`.
- **OF itself:** RFC 0001's phases, RFC 0002, and each accepted design (`handover.md`, inbox `--mine`, finance facts) are works of `organic-framework-build`.
- **Matching:**
  - a branch `claude/<x>-rfc` or a PR that touches `docs/rfc/<x>.md` attaches to the work whose `doc` is that file;
  - `of ask --work <id>` and `of status --work <id>` make the link explicit when matching is ambiguous.

## 5. The Studio view: Works

- **One row per work**, grouped by unit, then by system. The row shows: the title, the stage (for example "2 of 5"), the state, the doers, "waiting on whom" with its age, the last progress, the cost this week, and the `review` date.
- **On top:** the works that wait on the owner, then the quiet ones past their review.
- **A row opens a work card:**
  - its stages with evidence;
  - its PR timeline;
  - its asks and hand-overs;
  - its doers' recent turns, in one line each.
- **In herdr:** `of works` prints the same rows in the terminal, and the asks popup shows each ask's work.

## 6. Order and owners

| Step | What | Owner |
|---|---|---|
| 1 | The `works:` schema in `docs/registry.md`, `of registry check` for it, and `--work` on `of ask` and `of status` | `of_platform` (schema reviewed by `of_architect`) |
| 2 | The first user organization's works bootstrapped from its RFCs and project positions, confirmed with its owner | its structure keeper |
| 3 | The Works view and the work card in Studio, and `of works` | `of_studio` |
| 4 | Matching PRs and branches to works in `of import github` (done 2026-11-06: `works` on `of.work_record`, and `of.pr_state` for open PRs; the live ingest's pr-links attach through their PR number) | `of_platform` |
| 5 | Works in the diagnose rules: `lingering` on works, and orphan work as "PRs that match no work" | `of_studio`, `of_field-analyst` |
