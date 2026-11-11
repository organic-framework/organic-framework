# Closing a finished position: `of close`

Status: **approved with changes** by `of_architect` and reviewed by `acme_org`, 2026-11-10; written by `of_platform`. The founder asked for a closing procedure after `acme_org` found that
OF has none.

## The case

`acme_org`, 2026-11-10, on `catalogue_paths-cleanup` (a project position in market-data). Its own note met its exit
on 11-09 at 19:02Z, and then its session sat idle for a day. Closing it by hand took five manual steps:

1. **No command.** `acme_org` closed the pane with `herdr pane close`, after reading the pane to be sure it was the
   right one. `of who` then showed the position as `remote:idle` on machine "?", from stale presence, instead of
   offline.
2. **No check of the founder's yes.** AGENTS.md forbids closing another agent's session without it. `acme_org`
   found his answer in the store by hand: ask `b0e6186c`, "yes, close the session" (his words, in his language), asked by acme_throughput-owner.
3. **No check of what was left behind.** By hand it found four worktrees in the shared checkout (clean, three
   branches merged) and an open obsolete PR #2184. It routed them to catalogue_keeper.
4. **No registry step.** It set `status: finished` and wrote the closure into `holder.workplace` by hand. No field
   holds a closed session, and no event marks the closure.
5. **Works.** The position's work had the same `done_when` as the position, and `acme_org` set it to `done`. Studio
   still shows it as active, because `draft_state` overrides `state`; this went to `of_studio`.

## The procedure

    of close <position> --ask <id> [--park] [--successor <position>] [--dry-run] [--note "…"]
                        [--withdraw <ask id>]… [--hand <ask id>=<position>]…

The structure keeper runs it, or any position with the founder's yes for this closure. It has three phases. A
dry run prints all of them and changes nothing.

`--park` closes the session and frees its capacity, but keeps the position (acme_org: most keeper cases are a pause,
not an end). It sets `status: parked`, removes `holder` and writes no `closed`. Without `--park` the position is
finished. Neither mode ever deletes the memory directory, so a position that comes back starts from its brief and
its lessons.

### 1. Authority

- `--ask <id>` names an ask the founder answered, and its question must name the position. The command reads the
  answer from the store, so nobody retells the decision (of_architect):
  - with options, it reads the chosen option, which must be the closing one (a closure ask offers
    "close | keep", in the owner's language);
  - a free-text answer counts only when it is a plain yes;
  - a conditional answer ("yes, but first …") blocks, and the command prints his words for the keeper.
- When no such ask exists, the command stops and prints the `of ask` to file.
- A position held by a person is refused.
- **The pane is confirmed** (acme_org): when the live session in the position's pane differs from
  `holder.session`, the registry is stale, and the command refuses, so it never closes the wrong agent.
- **Keepers need a successor** (acme_org). The command blocks until `--successor` names one, unless `--park` is
  used, when the position is any of:
  - the keeper of a live system;
  - a unit owner;
  - a delegation's delegate;
  - the only decider of a cell in `owns:` (RFC 0007).

  The same close diff writes the successor into those fields.

### 2. Leftovers

The command reports what is left behind. It blocks only on the items marked *block*. The rest is handed on.

| Item | Source | When open |
|---|---|---|
| The exit | `mandate_exit.done_when` and the last `of status done` | **block** unless `--note` says why it closes before its exit |
| A running turn | the session's state | **block**: it closes between turns |
| Open asks and tasks to the founder | `view_asks` for the position | **block** (of_architect: a closure covers only what his words name). Each is withdrawn by name (`--withdraw <id>`) or handed to a named position (`--hand <id>=<position>`), and the command prints which |
| Hand-overs it gave or took that are still open | `of handover` | listed, with the counterpart to tell |
| Open PRs | `of.pr_state` (the author comes from the Position line, `06c2fb4`) | listed, with the repository's keeper to hand them to |
| Branches and worktrees | the organization's checkouts: worktrees and branches whose commits carry its `Position:` | **block** only on dirty or unpushed work (acme_org). Clean and merged ones are listed for removal; pushed but unmerged ones are listed with their PR, for the follow-up position |
| Credentials issued to it | per-position runner keys, k8s ServiceAccounts, database users, relay grants (acme_org) | listed, each for its issuer to revoke |
| Messages | queued relay messages to it, and peers that declared `waiting --on` it | those peers are told; a later `of send` to a finished position is refused and names the follow-up position |
| Crons, monitors, background tasks | the last `of.continuation` | listed; they die with the session |
| Works where it is a doer | `works:` | every one is listed (acme_org: it was a second doer in its case), and those whose `done_when` equals the position's exit are marked, for the work's owner |
| Lessons in its memory directory | RFC 0006 | named to the structure keeper, which decides where they go; the directory stays |

### 3. Closing

1. The session is closed between turns with `of_capacity.close`, and herdr's pane is released. With
   `--keep-session` the session stays and only the registry is changed.
2. Relay presence for the position is withdrawn, so `of who` shows it as `finished`, not as `remote:idle`.
3. A registry change is written through `of diff`, as the new kind `close`:
   - `status: finished`, or `parked` with `--park`;
   - `closed: {at, by, ask, session}`, where `session` is the session that held the position (not with
     `--park`);
   - the `holder` block is removed;
   - the successor goes into the fields the position held (keeper, unit owner, delegate, cell decider).

   The diff carries the ask id as its approval, so the founder never gets a second card for the same closure
   (of_architect). The structure keeper commits it.
4. The command records `of.position_closed {position, session, ask_id, by, leftovers: {...}}`.
5. The receivers named in phase 2 get one `--fyi` message each, and so do the position that asked for the
   founder's yes and the follow-up position (acme_org).

**Respawn** (of_architect). A spawn diff for a closed position clears `closed`. The event log keeps the history,
and the memory directory is still there for the new holder.

## Schema changes (to `of_architect` before code)

- The registry field `closed` on a position.
- The `of diff` kind `close`.
- The event `of.position_closed`.

## Not in v1

- Closing a work. Works close by the owner's decision (`state: done`), and Studio's `draft_state` is `of_studio`'s
  part.
- Merging or deleting branches. The command lists them; their keeper acts.
