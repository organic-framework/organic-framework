# RFC 0006: a position's memory, and the founder's corrections as records

Status: **accepted** by `of_architect` (2026-11-09). It is a framework fix: reversible (a launch setting), no money, no structure change. Source: a finding of the first user organization's structure keeper (a private report). The founder asked why one of its positions repeated mistakes after he corrected it.

## 1. What goes wrong

Claude Code's auto-memory lives in the account's config dir, under the working folder: `<config dir>/projects/<folder>/memory/`. Under OF this has three effects.

1. **Positions that share a folder share one memory.** Every position working in one repository folder on one account writes into one `MEMORY.md` index.
2. **The index is cut.**
   - Claude Code 2.1.319 loads only the index's first 200 lines and 25,000 bytes (`ZN=200`, `Qee=25000` in the CLI).
   - New entries go at the end, so the newest lessons are the ones cut.
   - On 2026-11-09 that folder's index had 215 to 257 lines (27.9 to 35.2 KB) on all five accounts. One position's five lessons from the founder's corrections were on lines 232 to 257. After each compaction and each move it started without them.
   - The OF team's folder index had 20 to 29 lines.
3. **Each account keeps its own copy.**
   - A move merges the folder's memory into the target (`of capacity move`, a union of lines). The copies still differ, and the merged index grows.
   - Which lessons a position has depends on which account it runs on.

## 2. Decision

### 2.1 Memory belongs to the position

- Each position's auto-memory lives in **`~/.local/state/of/memory/<org>/<position>/`**:
  - mode 700;
  - never committed: memory can hold what a repository must not;
  - outside every account.
- `of spawn` and every resume that `of capacity` starts (move, resume) launch Claude Code with `--settings '{"autoMemoryDirectory": "<that dir>"}'`. The CLI describes the setting as ignored only when it comes from a checked-in project file. Step 1 below checks that the flag is honored.
- Effects:
  - every account uses the same directory, so a move neither carries nor loses a lesson;
  - two positions in one folder no longer share an index;
  - a new holder of the position inherits the lessons, because they belong to the role. This is also the beginning of a role package's lessons in Bench.
- A session without a position, such as the founder's own, keeps the folder memory.
- **The scope is configurable** (founder, 2026-11-09). The registry key `memory:` takes `position` (the default), `unit` or `org`. It can be set on the organization, on a unit or on a position; the nearest setting wins.
  - The directory is `~/.local/state/of/memory/<org>/<scope id>/`, where the scope id is the position's name, `unit-<unit>` or `org`.
  - A shared index fills faster: a folder with 30-odd positions passed 200 lines in about ten days. A shared scope is therefore kept under the cut by its owner: the unit's owner for `unit`, the structure keeper for `org`. The warning of §2.3 goes to that owner.
  - Knowledge every role of a unit needs, such as a reporting standard or security practice, belongs in a practice file or a Bench package that the brief loads. Shared memory is not the place for it, because it is never cut and it is reviewed.
- **The setting is applied at each position's next spawn or move.** No running session is restarted for it: restarting another agent's session needs the founder's yes.

### 2.2 Migration

- **Seeding.** A position's directory is seeded from the folder memories of every account whose `originSessionId` is one of its sessions (`holder.session` and `of.holder_changed`). Where copies differ, the files are merged as `of capacity move` merges them today: three-way, never dropping a line.
- **Unattributed files** stay in the folder memory. The structure keeper reads them once and moves what is a rule into `AGENTS.md` or a rules file.
- **The index.** Each position's index is rebuilt from its own files. It is a list of one line per file, newest first. Agents append later entries at the end, so what keeps them inside the cut is the warning of §2.3.
- Nobody trims the shared folder index by hand: the migration replaces it.

### 2.3 A warning before the cut

- **When it fires:** a position's index passes 180 lines or 22,000 bytes, below the cut.
- **What happens:**
  - the position is told at its next session start, at most once a day;
  - Studio lists it.
- **What the text tells the position:** merge related entries, generalize them into the kind of mistake, and send durable rules to the structure keeper for the brief.

### 2.4 The founder's corrections become records

A lesson that matters to the role belongs in the role's records, the brief or a rules file, which the structure keeper reviews and every new holder reads. The agent's memory is a working cache.

- **Detection.**
  - The founder's prompts in a position's pane (`of.human_prompt`, origin human) are judged by the same small-model judge as in-pane questions: is this a correction, and what is the rule in one line.
  - A correction is recorded as `of.founder_correction` with the position, the rule, a kind and `repeat_of`. `repeat_of` is the earlier correction of the same position with the same rule, when the judge finds one.
- **Hand-over.** Once a day, each organization's structure keeper gets one `--fyi` digest of its positions' corrections, not one message per correction. The keeper decides where each rule goes: the position's brief, a rules file such as `registry/rules/money-reads.md`, or nowhere.
- **Measure.** The **repeat-correction rate** per position and week is the corrections with `repeat_of` divided by all corrections, shown in Studio. It is the test the founder set for splitting an organization's rules by role (2026-11-09): splitting works if the rate falls.

## 3. Who builds what

| # | What | Owner |
|---|---|---|
| 1 | `of spawn` passes `autoMemoryDirectory`, resolved from `memory:` by one function that `of capacity` also uses. First a lab session checks three things: the flag is honored, the system prompt names the directory, and no trust prompt appears | `of_platform` (lab check with `of_lab`) |
| 2 | `of capacity move` and `resume` pass the same flag; the memory merge skips a position that has its own directory; the migration of §2.2 | `of_capacity` |
| 3 | The warning of §2.3 | `of_platform` |
| 4 | Detecting corrections and recording `of.founder_correction`; the keepers' daily digest | `of_platform` |
| 5 | The repeat-correction rate in Studio | `of_studio` |

- **Critical path: 1, then 2.** The lessons come back once a position starts with its own directory.
- Steps 3 to 5 are independent of each other.
- The event name `of.founder_correction` is added to [`docs/events.md`](../events.md) with step 4.

## 4. Limits

- A cut can still happen inside one position's index. The warning and the newest-first order make it rare and push the lesson into the records.
- The judge can misread a correction, or miss one. The digest goes to a keeper, who decides; nothing is written into a brief automatically.
- The memory directory is per machine. A position that moves to another machine starts with an empty directory until memory sync is designed.
