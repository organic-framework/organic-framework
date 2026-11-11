# Capacity: model limits as a budgeted resource

Status: **v0**, 2026-11-06. Owner: `of_capacity`. Design owner: `of_architect`. Concepts: [`ontology.md`](ontology.md) (shared resources, group B), RFC 0001 v3 §5.6 (a Workplace change with the same holder). Events: [`events.md`](events.md). Code: `bin/of_capacity.py`.

## The resource

An agent runs on an **account**: a harness login with its own model limits. In Claude Code one account is one config dir (`~/.claude`, or `~/.claude-<name>` chosen with `CLAUDE_CONFIG_DIR`). Each account has two windows, a **5-hour** and a **7-day** one, each with a utilization and a reset time. When a window is full, every session on that account stops until it resets. Overage may be disabled at the organization level, so there is no paying one's way out.

Capacity is therefore a budgeted resource shared by every position on the account, like the owner's attention ([`events.md`](events.md), `of.attention_routed`). Organic Framework treats it the same way: measure it, record it, and route around it with a visible decision rather than a stall.

An owner with several accounts holds a pool. Moving a position from a full account to one with headroom is a **Workplace change**: the holder (the session, its context, memory and name) stays the same; only where it runs changes. It is the same family as moving a position to another machine.

## Measuring: `of capacity status`

```
of capacity status [--fresh | --no-probe] [--json]
```

One row per config dir: the account (from `<dir>/.claude.json`, `~/.claude.json` for the default dir), logged in or not, the utilization and local reset time of each window, and the positions running there. Positions come from the registries: a position's `holder.session` is matched against the harness's live session files, `<dir>/sessions/<pid>.json` (status `busy`, `idle`, `shell` or `waiting`; a file whose pid is gone is ignored). Live sessions no position claims are counted as "other".

The reading is a **probe**: one minimal `claude -p` call on the cheapest model (`haiku`, one word in, one word out) from a neutral working directory (`~/.local/state/of/capacity/probe`), with `--output-format stream-json --verbose`, all hooks off (`--settings '{"disableAllHooks": true}'`: the probe is no position's session, so the recorder must not see it) and no MCP servers (`--strict-mcp-config`). The stream carries a `rate_limit_event` with both windows and a `result` with the call's token cost. A probe runs at most once per 30 minutes per account; `--fresh` probes now, `--no-probe` shows the last reading. Each fresh reading is one `of.capacity_snapshot` event, with the probe's cost, so the cost of measuring is itself on record. The cache is `~/.local/state/of/capacity.json`.

What the probe showed on 2026-11-06:
- The CLI reports utilization as a fraction (`0.99`), not whole percents as in October; `of` normalizes to percents.
- An account in `allowed_warning` reports only the window in warning (the 7-day one); the 5-hour window is then unknown, shown as `—`.
- A probe costs about $0.002 at list price without hooks and MCP servers (about 12k cached-read and 9k cache-write tokens), $0.004 with them (the plugins' SessionStart context). It leaves a one-turn transcript under the probe directory's project key in the account's config dir.
- An account that is not logged in answers `Not logged in · Please run /login` and costs nothing.
- An account on a plan without a 5-hour window reports only `seven_day`.

**The default dir is probed with `CLAUDE_CONFIG_DIR` unset.** Setting it to `~/.claude` gives the harness a different identity (`~/.claude/.claude.json` instead of `~/.claude.json`) that is not logged in; the 2026-11-06 morning reading "not logged in" for `~/.claude` was this artifact, and the account had 0% of its week used. A resume onto the default dir likewise uses `env -u CLAUDE_CONFIG_DIR`.

**A failed probe is no reading, not a logout.** Only the harness's own "Not logged in" marks an account logged out; a missing binary or a timeout shows as "no reading" and is retried on the next run. Jobs under launchd have a short `PATH` and may lack `USER`: the probe looks for `claude` in `~/.local/bin` and the Homebrew prefixes, and sets `USER` and `LOGNAME`, because the harness finds its login in the keychain by user name (without them every account reads "Not logged in"; this happened on 2026-11-06 at 10:57).

Every probe runs in the automation environment ([`AGENTS.md`](../AGENTS.md)): `OF_AUTOMATION=1`, an empty `SSH_AUTH_SOCK`, no `HERDR_*` variables (a nested harness run from a herdr pane would re-bind the pane), and `OF_PROBE=1`.

## Thresholds

A position's account past a threshold is a capacity problem the owner should see before the account stops:

| Window | Threshold |
|---|---|
| 7-day | 90% |
| 5-hour | 95% |

`of capacity status` prints a line per account past a threshold with positions on it, naming the logged-in account with the most **headroom** (the free share of its tightest window).

`of pulse` (every 5 minutes) reads capacity the same way, so each account is probed at most every 30 minutes, and asks the owner with `of ask` (from `of_capacity`, options "move them now" or "wait for the reset") once per account, window and reset period: the account, the window past its threshold, the reset time, the positions there by organization, and the target with the most headroom. The keys already asked are kept in the capacity cache (`_proposed`).

`of capacity plan [--no-probe] [--json]` lists, for every account past a threshold, the positions to move, the target and the order: production first (registry `throughput: raise`, then `sustain`, `coordinate`, `measure`, `expense`), then parked, then finished positions; holders that are busy at the moment come after the rest, since they move between turns. Each line is one `of capacity move`.

## The move: a Workplace change with the same holder

```
of capacity move <position> --to <config-dir> [--dry-run] [--allow-shells]
of capacity move <position> --model <model> [--to <config-dir>] [--reason "<why>"] [--dry-run]
```

`--model` restarts the session on another model by the same steps, on its own account unless `--to` names another: same session id, pane, name, context and memory; the effort the process runs at (its `--effort`) and its permission mode are kept. The note is a "Model change" note and says the reason (`--reason`); `of.holder_changed` has `previous_model` and `reason`. Later moves, restores after a restart and `of resume-command` keep the new model, since each takes the session's last one. First use: acme_throughput-owner to `claude-fable-5-2` for the founder's 3-day trial (ask 8ee7f1bc, 2026-11-10). A model switch drops the prompt cache: the first turn writes the whole context again at the new model's price. Fable 5.2 costs twice Opus 5.5 per input, output and cache-write token, and the same for a cache read ($0.20 per million, from a probe's cost): about 1.3 times Opus for a long-context manager.

One session at a time. Every check runs before anything irreversible; when one fails, the old session keeps running untouched.

1. **Only between turns.** The session file's `status` must not be `busy` (a turn or subagents running), and herdr must say `idle` or `done`. A `shell` status means background shells are running; exiting stops them, so the move refuses unless `--allow-shells` (agree with the holder first; when the holder cannot answer, judge what the shells do and say so in the report).
2. **No draft in the input box.** The last `❯` line of the pane is the input box; plain text there is a draft (the owner may be typing) and the move refuses. Dim text is the harness's suggestion, not a draft.
3. **Exit, by the screen** (`exit_session`). `/exit` typed into the pane through herdr, declared first as `of.injected_prompt`. A session with scheduled tasks or background shells gets Claude Code's "Background work is running" dialog (1. Exit and stop tasks, 2. Move to background and exit, 3. Stay). The move answers it only once it is on screen, and only with "Exit and stop tasks": it checks the cursor and moves it up if needed; "Move to background" would leave the position running with no pane, where no message reaches it, so it is never picked. A `/exit` still in the input box is submitted once. Without `--allow-shells` the dialog is cancelled. **On any failure the pane is put back** (Esc closes the dialog; the session stays as it was), and a pane that cannot be put back is `stuck`: the balancer gives the owner a task at once (`of go`, Esc or "3. Stay", never "Move to background") and stops its run. The move then waits for the pane's shell to be back in the foreground. (aws_keeper, 2026-11-07: a blind Enter 20 s after `/exit` missed the dialog; the move failed and left it open for 2.5 h; two messages the relay typed into it were lost; then "Move to background and exit" was picked there and the position ran as a background copy, on the account at 99%.)
4. **Carry the session** from the old config dir to the new one, after the exit so the transcript is final: `projects/<key>/<sid>.jsonl`, `projects/<key>/<sid>/` (subagents), `file-history/<sid>/`, `session-env/<sid>/`, `tasks/<sid>/`. Anything already at the target is first saved under the move's work dir.
5. **Merge memory three-way.** Memory is per project directory and per config dir (`projects/<key>/memory/`). The base is the version last carried, kept under `~/.local/state/of/capacity/memory-base/`. A file only one side changed takes that side; `MEMORY.md` changed on both sides takes the union of lines; any other file changed on both sides keeps the target and leaves the source's copy in the work dir as a conflict to merge by hand. With no base (the first carry into a dir that holds an older copy, for example an account the organization left weeks ago), a target file older than the source's is replaced and the old one is kept in the work dir (`*.older-target`); a newer target is a conflict. Nothing is overwritten blindly: a blind `rsync --update` once lost a `MEMORY.md` edit. Moved and not-yet-moved sessions write to different dirs, so every move re-runs the merge.
   **A position's own memory (RFC 0006).** With `position_memory: true` in `capacity.yaml` (a move: also when the session already runs on its directory, as `of spawn` starts it), a move and a resume start the session with `--settings '{"autoMemoryDirectory": "~/.local/state/of/memory/<org>/<scope id>/"}'` and skip the folder merge: every account reads the same directory. The scope id is the position's name, `unit-<unit>` or `org`, from the registry key `memory:` on the position, its unit or its organization, the nearest winning (`bin/of_memory.py`, `of_platform`'s one resolver for `of spawn` and `of capacity`). Before the resume, the move folds in what the session wrote to its folder before it ran on its own directory. `of capacity memory [<position>] [--dry-run]` seeds the directories: each folder memory file whose `originSessionId` is one of a position's sessions (from the event store's sessions and positions, `of.holder_changed`, and the registry's `holder.session`, aliases resolved), oldest copy first, each against the copy last taken from that account and folder (`memory-base/positions/`). Two copies with no common base are joined line by line, both sides kept where they differ. The index is rebuilt one line per file, newest first, keeping each file's own index line. With `--from-transcripts`, a file with no `originSessionId` (or one of a session no position held) goes to the position of the last session that wrote it: a Write or Edit, or a shell write (`>`, `>>`, `tee`, `sed -i`; three in four memory writes were heredocs), read from the largest copy of each transcript; bulk copies (`cp`, `rsync`) are not authorship. Files still of no known position stay in the folder, and `--report` lists them per organization for its structure keeper in `~/.local/state/of/memory/<org>/_unattributed.md`, one line per file name, feedback first, with the writing session's folder and first task as a hint. Directories are mode 700 and never committed. Seeded on 2026-11-09: 930 of about 1,860 folder copies by `originSessionId`, then 295 more by their writers; 632 copies (137 file names, 133 of Acme and 4 of the OF team) went to the keepers, most of them written by task sessions from before positions existed.

   **Credential deny rules.** A move, a resume and a restore after a reboot start the session with the same deny rules `of spawn` writes from the credentials catalogue (`of_spawn.deny_rules`; docs/registry.md, Credentials), joined with the memory directory in the one `--settings` value (`deny_for`, 2026-11-11). A broken catalogue gives no rules and a line in the log; the guard still applies.
6. **Resume in the same pane** under the canonical name, from the session's working directory: `CLAUDE_CONFIG_DIR=<dir> claude --resume <sid> -n <name> --model <model> --effort <effort> --dangerously-skip-permissions '<prompt>'`. The model is the session's last one and the effort the one its process runs at (its `--effort`), else the old config dir's setting for the model, so the holder runs as before even when the target's defaults differ (on 2026-11-10, 40 of 49 sessions ran at `xhigh` on accounts set to `high`; until then a move took the setting and would have lowered them). The prompt points to a hand-over note in the work dir (`~/.local/state/of/capacity/moves/<name>-<time>/note.md`): the move, the crons, monitors and background tasks from the session's last `of.continuation` event (they died with the old process), and a check question. `--resume` keeps the session id. **The prompt is declared** (`of.injected_prompt`) just before the start, and the hook takes a declaration for 10 minutes; a note that arrives later is recorded as the owner's words (platform_owner's, on 2026-11-06: the folder trust dialog held its resume from 09:27Z to 12:30Z). So a start that is not up in 5 minutes is not left to land later: when the trust dialog holds it, the move closes the dialog with "No, exit" (the process exits, the note is never typed) and fails; with no dialog on screen the start may be slow, and the move declares the same text once more and fails, saying so (`start_session`; `OF_CAPACITY_START_WAIT_S` overrides the 5 minutes, for the lab).
   After the resume, the relay server may answer `403 not_holder` to the moved holder's `of inbox --mine` or `of send` until it sees the new process. warehouse_keeper saw it for a few minutes on 2026-11-07 (reported by `platform_owner` through `acme_org`). Since of_server's 363dbaa the relay re-reads its holders every 10 seconds and publishes a change at once, so the window is about 10 seconds; the hand-over note says to retry in 10 to 20 seconds.
7. **Record and rebind**: `of.holder_changed` (same session, new Workplace: config dir, account, runtime and pane), then the herdr name: herdr reports the resumed process as a new agent in the pane, without the old one's name (platform_owner lost its name on 2026-11-06), so the move waits until herdr shows the session in the pane and names the agent itself (`rename_agent`), then runs `of apply` for the tokens. **`of capacity` never edits an organization's registry** (single writer: its structure keeper). The account is runtime state, not structure (both registries dropped it from `holder.workplace` on 2026-11-06): an account-only move implies no registry change. A move to another runtime or pane writes the implied change of `holder.workplace` as a unified diff in the move's work dir (`registry.diff`, also named in the event) for the keeper to apply and commit: `of_architect` for the OF team, `acme_org` for Acme. A direct edit once dropped a closing quote and made a whole registry unreadable.
8. **Verify** by reading the pane once the holder is idle: the agent states what it was working on and its next step. `of who` shows it bound; `of apply --dry-run` proposes nothing for it.

A session with a very large context is expensive to resume: the first call re-reads the whole transcript and can eat a fresh account's 5-hour window in minutes. For such a holder, prefer a fresh session briefed from the position's progress (brief, last report, memory) over a resume.

Plugins, MCP servers and hooks are per config dir. Before the first move to an account, prepare it: the herdr Claude integration (`CLAUDE_CONFIG_DIR=<dir> herdr integration install claude`; `herdr integration status` shows it), the same plugins and MCP servers where the positions need them, and the recorder hooks (`of hooks install --config-dir <dir>`, with the owner's yes; without them, moved sessions stop recording).

**When the old account is already exhausted**, the holder cannot take a turn: a message to it ends in the harness's limit dialog ("Stop and wait for limit to reset", "Wait here", "Switch to usage credits"). Choose the first option (never the paid one), then move it; the move itself needs no turn on the old account.

### From another terminal multiplexer

A holder running in a Zellij pane (`ZELLIJ_SESSION_NAME` and `ZELLIJ_PANE_ID` in its process environment) moves the same way, into herdr: the draft check reads `zellij action dump-screen --pane-id`, `/exit` goes through `write-chars` and `send-keys`, and the session resumes in a new herdr pane in its unit's workspace (`<org short>·<unit>`) and its system's tab, created when missing (`--into WORKSPACE/TAB` overrides). The Zellij pane is left at its shell. The launch flags follow the old process (`--dangerously-skip-permissions` only when it had it), and `--note` / `--note-file` append the organization's continuation text to the hand-over note. An account move and a runtime move are one restart.

### Finishing a move: `of capacity resume`

`of capacity resume <position> --session SID --to <config-dir> --pane <pane> [--note-file F] [--dry-run]` finishes a move whose resume never started: it starts the session from the config dir that holds it, in that herdr pane, with a declared hand-over note and the check question, then names the agent, records `of.holder_changed` (evidence `of capacity resume`) and runs `of apply`. Model and effort come from the transcript and the config dir, the permission mode from the transcript. A claude process sitting on the folder trust dialog in the pane is closed with "No, exit" first; any other claude process there, a session live in any config dir, or a folder the target does not trust refuses. **Nothing is typed into a pane undeclared** (of_platform, 2026-11-07): runners_keeper's relaunch at 12:41Z on 2026-11-06 was typed by hand, before this command existed, and was recorded as the owner's words.

### Restarting the Mac: `of capacity reboot`

A restart of the Mac (an OS update, founder, 2026-11-10) stops every agent session, and macOS clears `/private/tmp`, where the sessions keep their scratchpads and their background tasks' output (30 GB, 227,000 files that day). The platform's services come back by themselves at login (launchd `RunAtLoad`: ClickHouse, of-server, the listener with the pulse and the balancer, the relay); the event store, asks, relay queues, registries, memory directories and transcripts are on disk. The sessions are not: 48 that day, 26 of them with background watchers or timers that die with the process.

- `of capacity reboot prepare [--dry-run]`, just before the restart: records each live session (position, account, pane with its workspace and tab, the folder it started in, model, effort, bypass, memory directory, its last record of crons and background tasks, and its background processes from `ps`) in `~/.local/state/of/capacity/reboot/current/plan.json`, and keeps `/private/tmp/claude-<uid>` by hard links beside it (the same volume: no copy and no space, about four seconds; a file still written to in place stays current). It touches no session; run it again to refresh, a later run replaces the earlier one. It also sets herdr's `[session] resume_agents_on_restore = false` (`~/.config/herdr/config.toml`): on 2026-11-10 herdr resumed 38 agents itself after the restart as a bare `claude --resume <id>`, with no account (so `~/.claude`, at 100% of its week, and the stale transcript copies earlier moves left there) and no bypass, model or effort; none of them got a turn through.
- After the restart, log in, start herdr, and in a herdr pane: `of capacity reboot restore [--only a,b] [--dry-run]`. It moves the kept files back where nothing is there now, then resumes every recorded session that is not running, one after another, production (`hold`) first: on its account, with its model, effort, mode and memory directory, and a declared note that lists what died with the old process (crons, monitors, background tasks, processes) to re-create, and says a turn was cut off when the session was busy at `prepare`. The pane: its own when herdr brought it back with the same id and only a shell runs there; else a pane herdr brought back with other ids (no agent, the same workspace and tab, the same folder); else a new pane in the position's workspace (its tab there, or its system's), as `of spawn` places it. Never the pane `restore` runs in. A recorded session already live on another account or without bypass, started after `prepare`, is herdr's own resume: `restore` stops it (SIGTERM) and resumes the session as it ran; one started before `prepare` is a move the plan does not know, and is left alone. When every session is back, herdr's agent resume is set back to what it was. A session that fails (an untrusted folder: the owner decides) is reported and the rest go on; run it again for the failed ones. At the end `of apply`, and the plan moves to `reboot/done-<time>/`.
- `of capacity reboot status`: when the plan was made, how many of its sessions run now, and which are down.
- Events: `of.capacity_reboot_prepared`, `of.capacity_reboot_restored`, and `of.holder_changed` per session (`evidence: of capacity reboot restore`).

Checked on 2026-11-10: a dry run over all 48 sessions, and a live restore of a throwaway session into a new pane (resumed with its context, read its note).

## Closing a session

`of capacity close <position> --session SID --reason TEXT [--dry-run]` ends a live session between turns with no resume: the harness's limit dialog is answered with "stop and wait", a draft refuses, then `/exit` through the same screen-driven exit as a move, answering the background-work dialog with "Exit and stop tasks" (a closure ends the session's scheduled tasks too; a held Zellij pane drops to its shell with Esc), or `claude stop` for a background session. **A closure is confirmed only by the processes**, never by the registry (a registry "closed" is a decision, not a fact): no live session file, no `claude agents --json` entry in any config dir while that dir's daemon runs, no process whose arguments name the session. It writes `of.holder_changed` with `new_session: null`, the reason and what was checked. A session waiting on an owner's question is not closed blindly: the question is saved and put to the owner first.

## Counting tokens (canonical)

One API response is one `message.id`. Claude Code writes a transcript record per content block of a response, each carrying the same `usage`, so summing records counts a response several times (on the Acme platform sessions, 286M by records against 141M by responses). The count:

- **per response, once**, by `message.id`, subagent transcripts counted to their session;
- **fresh tokens** = input + cache writes (5-minute and 1-hour) + output: what the provider processes anew;
- **cache reads** separately: they are most of the tokens (6.3B against 141M fresh in that week) and priced at 0.05× input;
- **cost at list** as the common weight of all four (Opus 5.5: $4 in, $20 out, $8 1-hour write, $5 5-minute write, $0.20 cache read per MTok). Limits move with this weight more than with any single count.

`of_capacity.usage(dirs, since, until, sessions)` returns this per session (a full read, for reports). The balancer's spend comes from the listener (RFC 0001 §5.1a): the `capacity` processor (`of_capacity.ListenerProcessor`, every 5 minutes) reads `transcript_record` by its own cursor (rows carry `message_id` and the 5-minute/1-hour cache writes since `of_store` schema 2) into `view_capacity_spend`, one row per response, kept 6 hours, and starts a detached `of capacity balance` (one at a time, a lock). Until the store's backfill gives every usage row of the last 6 hours a message id, the view is incomplete and the balancer reads the transcripts by a byte cursor instead (`spend_index`); when it completes, the view is rebuilt once and becomes the source. While no listener runs the processor, `of pulse` runs the balancer.

Two corrections found on the first day of the view (2026-11-06): (1) a move copies the transcript (and its subagents) to the new account, so a count by file credits the copied history to the new account; the transcript cursor now counts a copied file only from its copy time (`mark_copied`, from each move and back-filled from the day's `of.holder_changed`), and the store, which keeps each record's original `source_path`, was right all along. (2) The live ingest carried no subagent transcripts (only the import did, up to 06:27Z), and subagents were about a third of `~/.claude`'s spend. of_platform fixed it the same day (1fd9473); a subagent's records reach the store at its parent's next Stop, SessionEnd or PreCompact, so during a long turn they lag. The same holds for a session's own records, which reach the store at its own Stop: a session in one long turn (of_capacity's own turn ran from 10:01Z to past 12:45Z) showed $0/h while it was spending. The balancer therefore adds to the view every response the store does not have yet (by message id, from the transcript cursor), which also covers config dirs without the OF hooks: no double count, and no lag. Where both have a response, the store's record wins. (3) A side question asked in a running session forks it: the fork's transcript repeats the session's records under the fork's own id, and the fork stops first while the session is in a long turn. A response therefore belongs to the session that had it first: in the cursor, the session whose transcript file was born first; in the view, the store's `transcript_own` (of_platform, from 2026-11-06: a fork is marked in `session_fork` in the transaction of its first rows, and the view leaves its copies of its parent's records out), with the older rule (the session whose earliest response in the view is older) only for a store without it. The fork keeps only its own answer. Processor `capacity@2` reads `transcript_own` and rebuilt the view once.

## Balancing: use the capacity that expires first

**Decision (founder, 2026-11-06):** maximize the use of the tokens: spend first the capacity that resets first, plan the load so that a 5-hour window is not interrupted, and switch a holder's account when it would be.

Why: **budget left in a window at its reset is lost, so spend first what resets first.** An account's weekly budget is several 5-hour windows (on `~/.claude-e`, 1% of the week was about 3.7% of a 5-hour window, so a week is about 3.7 full windows), and the 5-hour window caps how fast it can be spent. Hours before a weekly reset, most of an unused week can no longer be spent: only the rest of the current window and the windows that fit before the reset.

`of capacity balance [--dry-run]` runs this rule once; `of pulse` runs it every 5 minutes when `~/.config/of/capacity.yaml` says `auto: true`. Each run does three things in order: rescue, then relief and fill, then a check of the invariant.

### The invariant: no session stands on a limit while another account accepts work (design, 2026-11-11)

**Founder, 2026-11-10 ~20:10Z:** «у меня сессия в 5-часовой лимит уперлась. Почему ее никто не ребалансирует на другой аккаунт?» (hand-over 2759a172 from of_conductor).

**The class.** A session stopped on a usage limit stays stopped while another account could run it. This happened five times in four days, and each time a rule or a parameter was fixed and the class remained:
- 11-07: ten Acme sessions stood on `~/.claude` at 100%, because relief took only idle holders;
- 11-08: the four production keepers stood on an exhausted ~/.claude-e until the founder said to move them by hand;
- 11-08: of_capacity sat on its own limit screen from 11:42Z to 14:34Z, because it was held;
- 11-10, 20:08Z: of_conductor and acme_code-auditor stood on mail@acme.example's 5-hour limit, until the founder asked, while ~/.claude-b and ~/.claude-d had 35 and 19 points of their week left.

**The cause in the design**, not in the parameters:
1. **One gate for two different decisions.** Relief and fill moved a session only when a prediction said the target had room for it: spend rates from the last 30 minutes, calibrations, tier priors, horizons, resume cost. Every unknown counted as no room (after the 11-07 Pro overflow). For fill, which is optional, that is right: a wrong "no" costs nothing. For a session that has already stopped it is wrong, because the two errors cost different things:
   - a wrong "no" is a certain stall that lasts until the founder notices;
   - a wrong "yes" costs little and corrects itself: the target fills up, its stopped sessions are rescued at the next run, and an account that does not accept work is never a target.

   The design treated the two errors as if they cost the same.
2. **The trigger was a prediction, not an observation.** Relief started from a probe's utilization (cached for up to 30 minutes) and from projections, not from the stop itself. The stop is in the session's transcript the moment it happens. On 11-10, acme_code-auditor stopped at 20:08:38Z, and the probe saw the account rejected at 20:10:20Z.
3. **Nobody checked the invariant.** "Nothing to move" counted as success, so the founder was the monitor.

**The parameters were not the cause.** Hours earlier, 62d6e1b fixed the two parameters behind the 11-10 stall: the 5-hour prior for accounts that report no 5-hour window, and the 24-hour horizon. Replayed on the inputs of 20:10Z (lab scenario `capacity-stall-v0`), 62d6e1b still leaves both sessions stopped. The 30-minute spend on mail@acme.example was $68/h, $57 of it acme_code-auditor's, so the projection had its week running out in 4.5 hours, and the target again had to last 24 hours. The live dry run at 20:16Z moved sessions only because the two had already been moved by hand.

**The change.**
1. **Rescue first, from observations** (`rescue_plan`). A session is *stopped* when the last main-thread assistant record in its transcript is the harness's usage-limit message (`isApiErrorMessage`, `apiError: usage_limit_reached`; `limit_stop`). An account *accepts work* when no window of its last probe stops it now and no session on it stopped after that probe. A window whose reset has passed stops nothing. Each stopped holder between turns, on an account that does not accept work, goes to an account that does:
   - accounts not about to stop come first, then the most capacity left: the observed free percent times the account's measured dollars per 1% of its week (else its plan's), so a fresh Pro does not outrank an Ultra with a third of its week left;
   - each move counts `rescue_step` (2) points against its target, so a batch spreads;
   - production (`hold`) goes first, then the priority tiers, then the oldest stop;
   - up to `rescue_moves` (6) move in a run, and the rest go at the next run, 5 minutes later.
   - **ahead of the stop** (2026-11-11): a holder between turns on an account its probe saw refuse work for `rescue_ahead_min` (60) minutes or longer yet goes too, after every stopped one, since it stops on its next turn. On 11-11 ~/.claude-d's week ran out at about 06:20Z with storefront-hosts_keeper, a production keeper, idle there and a check due at 06:48Z. Within the hour a window reopens before most idle holders get work, and each move costs a resume.

   No prediction vetoes a rescue. The move's own checks still apply: between turns, no draft in the input box, folder trust. A session whose move failed is skipped for 30 minutes, except one whose limit wait the move cancelled (Esc on "continuing automatically at …"): it no longer continues by itself when its window reopens, so it is tried again on the next run. After that Esc the move reads the screen up to five times, 2 s apart, for the input box (2026-11-11: a single read missed it and left a watcher position with its wait cancelled, idle after its window reopened, until a move by hand).
2. **Relief and fill stay predictive and fail-closed.** They are optimizations: moving sessions off an account that is about to stall, and spending what expires first.
3. **The invariant is checked after every run** (`stall_report`, `invariant_step`; of_architect's review, 2026-11-11). The check lists every holder still stopped while another account accepts work, and why: mid-turn, no position in any registry, a failed move, or over the cap. The founder must never be the monitor again:
   - `of.capacity_stall` is recorded whenever the set of stalled holders changes, including to the empty set when the last stall clears, so the duration of each stall can be measured. The event names the accounts that accepted work at that moment, so it shows on its own that the invariant was broken.
   - A stall that survives two runs goes to of_capacity's own inbox as work, once.
   - **The pool, not a move.** A session rescued twice within one 5-hour window means the pool itself is running out, not that a move went wrong. Holders standing on a limit while no account accepts work mean the pool is spent. Either way, the rescues go on by themselves, and at most once per 5-hour window of_capacity's own session gets the founder's capacity question as work, with the numbers: each account's week and 5-hour use, when a stopped one reopens, its sessions and their spend, and what one more Max 20x account holds a week. It is his money call, and of_capacity files it itself with `of ask --tags money --due …` unless an ask of its own on it is open. A money decision reaches him only as its owner's ask, never through of_conductor (of_conductor, 2026-11-11). Nothing else about stalls reaches him. On 11-11 the question went out ahead of the stall it forecast (ask 28205a42): ~/.claude-d's week ran out at about 06:20Z, and the rest of the pool was due to run out hours before the next account reopened at 21:00Z.

**Risks accepted.**
- A rescue may put a session on an account that then stops sooner than predicted. The pool is finite, and the stopped session works in the meantime instead of standing.
- A large context costs its resume on the target, at most a few dollars.
- A session that stopped before its own account reopened (a probe after the stop says allowed) stays where it is. It continues there, or waits for its next prompt.

**Verification.** `python3 lab/scenarios/capacity-stall-v0/run.py [--rev REV]` replays the real 20:10Z inputs and three cases of the class, plus two negative cases:
- *probe lag*: the probe 24 minutes old says 92%;
- *long reopen*: 4.5 hours until the source reopens, with the targets' predicted room short;
- *uncalibrated target*;
- negatives: *nothing accepts*, and *reset and busy*.

Before 62d6e1b, the scenario fails the real case and all three class cases. At 62d6e1b it fails the same four. With the rescue it passes all six.

The rules below are the predictive part, relief and fill:

- **Fill.** The *sink* is the logged-in account, not about to stall, whose week resets first. Its 5-hour use at the window's reset is projected from what its sessions spend now; idle holders move to it from accounts that reset later, the biggest spenders first, while the projection stays under `fill_to` (85%). Spend is measured from the transcripts at list price: the higher of the last 30 minutes and the last 2 hours, since a holder idle now but busy all morning will be busy again. The window is calibrated in dollars per percent from what the account spent since the window opened (about $12 per 1% of a 5-hour window on `~/.claude-e`).
- **A move holds.** Every move, by the balancer or by hand (`of capacity move`), holds the session on its new account for `cooldown_h` (`hold_after_move`), so fill and relief do not undo it; rescue still moves it if it stops (2026-11-11: `of_docs` was moved by hand to the account that can read an artifact shared to it, and fill prefers the account whose week resets first).
- **Relief.** An account about to stall (5-hour window at `relief_at`, 95%, or its week at `week_max`, 97%) sheds idle holders to the account with the most headroom.
- **Folder trust** (of_architect, 2026-11-06). Claude Code asks once per folder whether to trust it (`projects[<path>].hasTrustDialogAccepted` in the config's `.claude.json`); a git worktree takes its main repository's trust. A resumed session in a folder the target does not trust sits on that dialog (runners_keeper did, the same day, for 3 h 24 min; once the folder was trusted on the target, closing the stale dialog with "No, exit", which grants nothing, and resuming again finished the move; `of capacity resume` does this now). Before any irreversible step a move checks the target; when only the source trusts the folder, it carries the flag (the owner's earlier decision) and records it in `of.holder_changed` (`trust: {state: carried, path, from}`); when no config trusts it, it does not move, and the balancer asks the owner once with the list of folders. The tool never presses the trust dialog.
- **Relief checks the target and waits out a window that reopens soon** (2026-11-07, 17:00 to 17:30Z). ~/.claude-e reached 97% of a 5-hour window that reopened at 17:30Z, and relief moved 14 sessions onto the new Pro account. Relief had no capacity check, so the Pro account overflowed at once (104%) and held them until 19:30Z. Now:
  - an account stalled only by a 5-hour window that reopens within `relief_wait_min` (90 minutes), with its week below `week_max`, is waited out, not relieved;
  - relief puts a session on a target only if that target's room (the same 5-hour, week and tier sizing as fill) takes its rate, and its resume costs at most 10% of the target's 5-hour window;
  - a missing connector still does not stop relief.
- **Relief ahead of a week running out** (2026-11-08). ~/.claude-e reached 95% of its week with 39 sessions on it, while ~/.claude-d, fresh at 0%, stood empty: relief only started at `week_max` (97%), and fill puts load on the account whose week resets first (mail). Now:
  - an account whose week reaches `week_max` within `relief_ahead_h` (6) hours at its current load, and before it resets, counts as about to stall;
  - relief may move `relief_moves` (6) sessions a run;
  - a relief target must hold what it takes for `relief_horizon_h` (24) hours, not its whole week (otherwise a fresh account looks too small, and the source stalls); when only the source's 5-hour window stops it, only until that window reopens (at least an hour);
  - an account that reports no 5-hour window but has a measured week is sized by its week alone. On 2026-11-10 two idle sessions stood on the Max 5x account's 5-hour limit for about 20 minutes, until the founder asked, while the Max 20x accounts had 35 and 19 points of their week left: those report no 5-hour window (none in 225 probes each since 11-03, at $100-115/h), the tier prior from the 5x account gave them $376 a window, and the target had to last a day;
  - waiting out a 5-hour window applies only when that window is the cause and the week is not running out.
- **A new or small account** (2026-11-07: mail@acme.example on Claude Pro, about 1/20 of an Ultra). Its week reset first, so fill chose it as the sink; with no calibration yet its room counted as infinite, and the next run would have moved two sessions of 480k and 708k tokens of context at $8 to $10 an hour onto it. Now:
  - An account with no calibration of its own is sized from the others' by plan tier (`organizationRateLimitTier`: Pro 1, Max 5x 5, Max 20x 20); with neither, it takes nothing. The profile in `.claude.json` refreshes only when Claude Code fetches it again, so after a plan change `capacity.yaml` `tiers: {<config dir>: <tier>}` overrides it (2026-11-08: mail@acme.example, Pro to Max 5x).
  - A 5-hour window that is not open yet counts as empty, with five hours ahead.
  - What fill puts on a sink must also last until its week resets: the sink's remaining week, less its own load.
  - A session whose resume (its context written to the sink's 1-hour cache, at $8 per million tokens) would cost more than 10% of the sink's 5-hour window does not go there.
- **An exit dialog never stays open** (2026-11-08). A relief move on 11-07 at 17:23Z typed `/exit` into accounting_keeper. The move closed the dialog with Esc and recorded the pane as restored, but the dialog was open again and stayed so for 21 hours; then ~/.claude-e's weekly limit came up under it. Every later relief refused the pane ("herdr says blocked"), and nobody was told. Now:
  - after Esc, the exit step checks again at 2, 5 and 10 seconds, pressing Esc again if the dialog is back; only one that stays makes the pane `stuck` (the founder's task);
  - a move that finds an exit dialog open, left by anyone, closes it with Esc ("Stay", always safe) before anything else, so a missed one heals at the next run.
- **herdr's integration travels with a move** (2026-11-08). herdr learns which session runs in a pane from its own SessionStart hook (`hooks/herdr-agent-state.sh`), installed per config dir. `~/.claude-d` had hooks, the OF and mac-guard ones, but not herdr's. So seven sessions moved there were unbound in herdr: no name, no relay delivery, and `of inbox` answered 403 (reported by `of_lab`). A move or resume now runs `herdr integration install claude` on a target that lacks it. Sessions already running were re-bound by replaying the hook's report for each pane, followed by `of apply`.
- **Settings travel with a move.** A new account's config dir has none of the owner's settings. Its resume stopped at the Bypass Permissions warning, and its sessions would have run without the OF hooks, the collector and the guard. A move now carries the missing `hooks`, `skipDangerousModePermissionPrompt`, `permissions`, `modelSettings`, auto-compact and clean-up settings from the source. This is the owner's earlier decision, as with folder trust: no dialog is pressed, nothing existing is overwritten, and `of.holder_changed` lists what was carried. A start held by the Bypass warning is closed with "No, exit" and named as such.
- **The week is calibrated by its growth** (2026-11-09). The spend index holds the last 6 hours, but the week's dollars per percent were computed as that spend over the whole week's percent since it opened: an Ultra came out at about $5 per 1% instead of about $26, so the balancer saw weeks running out (~/.claude-b was about an hour from a premature relief). Now each probe keeps the week's reading for 7 hours (capacity cache, `_week_hist`), and the calibration divides the spend since the oldest reading inside the index by the week's growth since then (at least 3 points); with no such reading it is unknown, not small. Measured that day: ~/.claude-d $25, ~/.claude-b $29 and mail@acme.example (Max 5x) $19 per 1% of the week, so a Max 5x week is about three quarters of an Ultra's, not a quarter; the tier scale stays only a fallback for an account with no reading.
- **Move work dirs are pruned** (of_platform, 2026-11-09: 193 dirs held 7.2 GB in two days, with the Mac's disk at 60 GiB free). Each balance run deletes a move dir's `backup/` (the target's older copy of the session's files, saved before the copy, needed only to undo a move gone wrong) once the move is 24 hours old and its position runs in a live session. `note.md`, `conflicts/` and `registry.diff` stay.
- **Agent sessions stay in Bypass Permissions mode** (founder, 2026-11-08). warehouse_keeper, resumed on 11-07 onto the new mail@acme.example account (whose settings then lacked `skipDangerousModePermissionPrompt`), ran a day in auto mode, Claude Code's default since 2.1.x, with `--dangerously-skip-permissions` still on its command line. Now:
  - a resume starts with the flag when the session ever ran in bypass, not only when its last prompt did;
  - after every start (move, resume, `of spawn`) the footer is read; a mode other than bypass is turned back with shift+tab, one press at a time, re-reading the footer after each, only with the input box drawn and empty, and stopping when a press changes nothing;
  - every balance run does the same for each idle pane whose process carries the flag and whose footer names another mode (`of.permission_mode_restored`). A footer that names no mode is left alone, since it may simply not be drawn.
- **Connectors.** A claude.ai connector (Slack, for example) belongs to the claude.ai account, not to the config dir, so it does not travel with a moved session (2026-11-07: `acme_dispatcher` and `obs_keeper` lost Slack on the move to alex@example.com). The balancer reads which connectors each session called (its transcript) and which ones any session on each account called in the last 14 days (the store). Fill never moves a session onto an account without a connector it uses. Relief still moves it, since it stands anyway, and every move's note names the missing connector and tells the holder to give the founder a task to connect it.
- **Plugins.** A plugin (Cloudflare, Slack, Linear) is installed per config dir, and its MCP server's login (Cloudflare's OAuth) is kept per config dir too (2026-11-11: mail@acme.example had no plugins at all, and `aws_keeper` lost Cloudflare in the 06:28Z relief move there, with OFF-013's R2 token rotation waiting on it; acme_org's report). The balancer reads the plugins each session called (`mcp__plugin_<name>_…` in its transcript tail, `tools_used`) and the plugins installed and not disabled on each account (`plugins/installed_plugins.json`, `settings.json` `enabledPlugins`; `plugins_seen`). Fill and relief never move a session onto an account without a plugin it used. Rescue prefers an account that has them and moves the session anyway when none that accepts work does, since a stopped session cannot work at all. Every move's note names a missing plugin and tells the holder to tell of_capacity what waits on it. An installed plugin whose MCP server is not connected on that account (`claude mcp list` says "Needs authentication" or failed; `plugins_ready`, from the same list and 6-hour cache as the connectors) counts as missing (2026-11-11: `aws_keeper`, relieved to ~/.claude-d, found Cloudflare installed but signed out, with a token deletion waiting on it).
- **Tool profiles** ([`tools.md`](tools.md) §4-§5; hand-over 9843194a from `of_tools`). For a position whose organization has a `tools:` catalogue, a move, `of capacity resume` and the reboot restore start the session with its profile, as `of spawn` does: `of_tools.launch_parts` gives its `--mcp-config` and its `allowedMcpServers` and `enabledPlugins`, merged into the session's one settings file with its memory directory and credential deny rules (`tools_for`, `launch_command`). Its account-bound tools (an OAuth plugin, a claude.ai connector) count only where `claude mcp list` shows their URL connected (`missing_signins`). Fill, relief, `of capacity plan` and a move by hand never pick an account without them. A move by hand stops before anything irreversible, and the founder gets one task for that account listing the missing sign-ins (`file_signin_task`, at most once a day per account and tool). Rescue prefers an account that has them and moves anyway when none with room does, because a stopped session cannot work at all; that move's note names what is missing, and the founder gets the same task. An organization without a catalogue sees no change.
- **Priority** (founder, 2026-11-07, ask 4576f634, goal g-throughput). `capacity.yaml` `priority: {first: [rules], last: [rules]}`; a rule names any of `org`, `unit`, `throughput`, `name` (a list means any). Relief and fill take tier 0 (first) before tier 1 (the rest) before tier 2 (last), and the faster holder first within a tier; `of capacity plan` orders the same way. Today: first, the Acme money side (throughput `raise` or `measure`); last, the Acme platform unit, in keep mode.
- **Guards.** Fill moves only `idle` holders. Relief, off an account about to stall, also takes a holder with background shells (`shell`) and one already stopped on the limit dialog (`waiting`, herdr `blocked`): it stands anyway, its shells stop and are listed in its note, and the limit dialog is answered only with "Stop and wait" (2026-11-07: ten Acme sessions stood on `~/.claude` at 100% because relief took idle holders only, and a move refused a `blocked` pane). Never a `busy` holder, mid-turn. Scheduled tasks stop and are carried in the hand-over note in full. Positions in `capacity.yaml` `hold`, production (empty by default; Acme's are `k8s_keeper`, `storefront-hosts_keeper`, `forecasting_keeper` and `storefront_operator`), move only by relief, off an account exhausted or about to be, and go first there; fill never takes them (founder, ask 44a3b1f9, 2026-11-08: on 11-08 all four stood on an exhausted ~/.claude-e until he said move them by hand). `of_capacity` itself is not held since 2026-11-08: the balancer runs as its own process, so relief moves of_capacity's session between turns like any other, which it could not do while held (it sat on its own limit screen from 11:42Z to 14:34Z). Holders under `min_rate` ($5/h) are not worth a move: a resume writes the whole context to the new account's cache, $1 to $7. At most `max_moves` (2) per run; a moved session rests 2 hours, a refused one 30 minutes. Each move is an `of.capacity_balanced` event and an `of.holder_changed`; balancer moves write no registry diff (the account is a runtime fact, recorded in the events and in `of capacity status`).

After a reset the rule turns by itself: when `~/.claude-e`'s week resets, `~/.claude` becomes the account that expires first and load flows back to it.

## Loops: cost per cycle and babysitting (RFC 0005 §8 step 5; design, 2026-11-07)

`of_capacity`'s part of [RFC 0005](rfc/0005-development-loops.md): the cost side of a development loop, and routing runner babysitting to the loop's `babysit_model`.

### Evidence: Acme's search work, 2026-10-31..11-06

The doer is `storefront_search`, with its 40 subagents. Every response is counted once, by `message.id`, at list price:

| | Cost | Share |
|---|---|---|
| The week | $982 (Fable 5.1 $913, Opus 5.5 $67, Sonnet $2) | 100% |
| **Babysitting**: runner-only messages, or turns woken by a monitor | **$584** | **59%** |
| of which turns woken by a monitor | $489, 2,365 responses | 50% |
| of which runner-only messages | $253 | 26% |

There were 1,408 runner commands (`kubectl` and the organization's own wrappers and namespaces, listed in `capacity.yaml` `runner_commands` as regex fragments) and 310 monitor calls. The babysitting cost is mostly the context: cache reads $317, output $138, cache writes $128. The median babysitting response re-read **456k tokens** of the doer's context, and the 90th percentile 745k.

**So the lever is the context, not only the model.** Caches are per model, so the same session cannot switch models for its babysitting turns: the switch rewrites the whole context's cache. Two estimates on Sonnet:
- the same responses with the same context: $308;
- a **separate babysitter with its own small context** (30k tokens): **about $45**, so about $540 a week saved on this one position.

### Routing: a babysitter, not a model switch

- **The babysitter.** Runner commands and monitors go to a **babysitter subagent** running on the loop's `babysit_model`, in the background. The doer launches it with the run's job, pod and acceptance rule. The babysitter:
  - watches with its own monitors, so their events wake it, not the doer;
  - reports to the doer only on a terminal state or an anomaly;
  - never edits code and never decides.
  
  The doer's large context then wakes once per run, not on every event.
- **Where its definition lives.** It is one agent file, `.claude/agents/runner-babysitter.md` (its `model:` from `babysit_model`; tools: Bash, Monitor, TaskStop, read-only files). `of_capacity` ships the template in this repository. Each organization installs it in its own repository, by the boundary rule; for Acme, through `acme_org`.
- **Monitors that emit less are a second lever,** in the same file: only terminal states and errors, and no progress ticks.

### Measures (derived, as RFC 0005 §5 requires)

- **Babysitting cost per session and day:** its share of the session's spend, its model, and the estimate on `babysit_model` in a small context. It feeds:
  - the **Babysitting model** rule (RFC 0005 §6; `of_field-analyst`'s anti-pattern), which fires when babysitting runs on a model above `babysit_model`;
  - the loop card.
- **Cost per cycle:**
  - Per `of.loop_run`: the tokens of the loop's sessions (its doers and their subagents) from the run's start (the hypothesis entering the rung) to its result, plus the runner hours from the runner's own record where one exists (cbx).
  - Per kept cycle: the loop's cost over a period, divided by the cycles kept in it.
  - Concurrent work in the same session is not split by hypothesis; the card says so.

### Build order

1. `of capacity babysit <position|session> [--since --until] [--json]`: the measure above, from the fullest copy of the session's transcript and its subagents. **Built 2026-11-07**; it reproduces the evidence table exactly.
2. The `runner-babysitter` template, `templates/agents/runner-babysitter.md`: **written 2026-11-07**. It is `model: sonnet` with read-only tools and one monitor filtered to terminal lines and anomalies, and it reports once. It never ends its turn between monitor events: Claude Code notifies the doer each time a subagent stops while its own background work runs, and every notification wakes the doer's large context. With that rule, the search doer's wakes per gate-and-day batch fell from 6 to 8 down to 1 or 2 (`acme_org`'s feedback after 8 runs, 2026-11-07). An agent type is loaded only at session start, so a live session uses `general-purpose` with `model: sonnet` and the template's text as its prompt. The doer starts it in the background with the job, the check commands, `done when`, the anomalies and a deadline. Each organization's structure keeper copies it to its repository's `.claude/agents/` and sets `model:` from the loop's `babysit_model`; `acme_org` first.
3. Cost per cycle: **built 2026-11-07**. For each `of.loop_run` an hour after it ends (a session's records reach the store at its Stop), the capacity processor writes one `of.loop_cost`: `{work, run, rung, hypothesis, session, from, to, usd, fresh_tokens, responses, by_model}`. It holds the recording session's spend from `started_at` (else `queued_at`) to `ended_at`, each response once by `message.id`, subagents included, at list price per model. A run with no start gets `usd: null`: unknown, never zero. Runner hours stay the run's own `runner_h` (cbx derivation, RFC 0005 step 2).
4. The measure exposed to the rule and to Studio's loop card (steps 3 and 4: `of_studio`, `of_field-analyst`).

## Credential broker (design, 2026-11-07)

**Status: design for review** (`of_architect`'s request, from the founder in chat at about 11:25Z, 2026-11-07). Nothing is built until it is read.

**Boundary** (founder, about 11:45Z; AGENTS.md, "The OF team organizes; each organization operates"):
- **The broker issues tokens; it never writes into an organization's systems.** A sink inside an organization, such as Acme's k8s Secret for the Catalogue workers, is installed and switched by that organization's own keeper, who calls the broker.
- **Contact with an organization goes through its structure keeper** (`acme_org` for Acme).
- `of_capacity` owns the broker, the vault and the inventory; the consumer's owner owns the sink, the install script and the restart. The founder's hands are needed only for (1) one terminal line that creates the vault and its service account, (2) one browser "Authorize" per account and year, in one sitting, and (3) the decisions in "Terms" below.

### Why

Accounts and limits run out in one place or another, and every consumer outside a herdr session gets its token by a founder task: lab VMs (`of_lab`'s withdrawn task `08333956`) and Acme's Catalogue tax workers (the k8s Secret `claude-subscription-token`). Nobody else sees those tokens. The tax workers spent about 47.8M tokens of `~/.claude` in the week to 11-07, with no session to show for it (catalogue_keeper's ask `6ed93736`; most likely `~/.claude`, the only account whose week resets on 11-11, our reading). That account reached 100% on 11-07, and ten sessions stopped. A broker issues the tokens, picks the account by the balancing rule, and makes every consumer visible.

### Terms: what the plans allow headless (checked 2026-11-07)

All four accounts are personal subscriptions on the top tier, **Ultra** (`organizationType: claude_max`, `planDisplayName: Ultra`, rate tier `max_20x`, billed by Stripe), so the **Consumer Terms** apply, not the Commercial Terms.

- **Automated use.** The Consumer Terms (§3) forbid access "through automated or non-human means, whether through a bot, script, or otherwise", except through an Anthropic API key or "where we otherwise explicitly permit it". Claude Code's authentication page permits one case explicitly: "For CI pipelines, scripts, or other environments where interactive browser login isn't available, generate a one-year OAuth token with `claude setup-token`". The token "can only make model requests" and needs a Pro, Max, Ultra, Team or Enterprise plan.
- **Ordinary, individual use, and several accounts per person.**
  - Claude Code's legal page: "Advertised usage limits for Pro, Max, and Ultra plans assume ordinary, individual usage of Claude Code and the Agent SDK". OAuth is "designed to support ordinary use of Claude Code".
  - Several Max accounts held by one person are not against the terms when each is that person's own and unshared. That is a statement by Thariq Shihipar of the Claude Code team, 2026-02-18, which we have only second-hand (`of_architect`'s search; the original post was not found). Sharing an account, reselling access and using an account to evade a ban are against the terms.
  - Reported with that statement: phone verification allows at most three accounts per number; in mid-February some multi-account users were banned by abuse detection; many developers publicly run three to five Max subscriptions with tools that switch between them.
  - **The founder confirms that all four accounts are his own, registered to him and unshared** (in chat, about 12:16Z, 2026-11-07, through `of_architect`). The founder's reading of individual use: each person uses Claude Code on their own plan for their own work, other engineers will do the same on theirs, and their agents only exchange messages with the company's agents.
  - The residual risk is a false positive from abuse detection. That argues for a second provider (vendor independence: Codex below), not against the accounts.
- **Products and services need API keys.** "Developers building products or services that interact with Claude's capabilities … should use API key authentication through Claude Console or a supported cloud provider." API keys may live "in a development environment, secrets manager, or machine image for use by the customer's own authorized users", billed to the key owner.
- **Credits, not a separate meter** (checked 2026-11-07: Anthropic support article 17154008 "Monthly API credits for Max, Ultra, and Team plans"; "Use the Claude Agent SDK with your Claude plan"; the release notes of 2026-10-07).
  - **No separate Agent SDK credit.** An Agent SDK credit was announced for 2026-06-15. It was paused and never started (Anthropic: "That credit isn't available"); the API credits of 2026-10-07 replace it.
  - **A plan sign-in draws on the plan's limits.** With a plan sign-in, a setup-token included, "`claude -p` and Agent SDK usage still draw from your plan's usage limits". So headless consumers on a setup-token spend the same 5-hour and weekly windows as the sessions; the tax workers' 47.8M tokens came out of `~/.claude`'s week. The probes do too, a few cents a day. The balancer's headroom needs no change, provided leased consumers are counted on their account.
  - **The 2026-10-07 API credits.** Every Max, Ultra and Team plan has monthly API credits: $100 on Max, **$200 on Ultra**, and $20 or $100 a Team seat, pooled and capped at $500. They are usable only through an API key from the Console organization linked to that Claude account, and they cover the API, Managed Agents, and the Agent SDK or `claude -p` run with such a key. They "don't change your usage limits". **Our four Ultra accounts carry $800 a month of API credits, unused today.** That is lane B's first money.
- **Codex** (checked 2026-11-07 on learn.chatgpt.com and developers.openai.com; help.openai.com and the Terms of Use pages refused our fetch with HTTP 403).
  - **The default is an API key.** "The right way to authenticate automation is with an API key." A ChatGPT-plan sign-in on a runner is documented as an "advanced workflow for enterprise and other trusted private automation". Its rules:
    - never in public or open-source jobs;
    - never one `auth.json` shared across concurrent jobs or machines;
    - never stored where others read it.
  - **Headless sign-in.** Device-code login (beta) is enabled in the personal account's security settings. The fallback is to copy `~/.codex/auth.json` from a machine with a browser. The session refreshes itself during use and goes stale after about 8 days, so each run must write the refreshed file back.
  - **Workspace access tokens** for scripts and CI exist on Business and Enterprise; the pages disagree on which. No page states their billing.
  - **API credits.** ChatGPT Pro gets $100, $200 or $500 a month by tier, and Business $20 or $100 a seat, capped at $500; Plus and Enterprise get none. They are only for the API Platform: "Codex usage through API keys is not supported by this benefit". They expire monthly and need the same email on the ChatGPT plan and the API organization.
  - **Limits.** Pro has no 5-hour limit. Plus counts messages per 5 hours. Past a limit, extra credits can be bought.
  - **Lane A′: Codex on a ChatGPT plan, on a trusted private runner.** OpenAI's "Maintain Codex account auth in CI/CD (advanced)" (learn.chatgpt.com/docs/auth/ci-cd-auth) documents ChatGPT-managed auth there, while saying "The right way to authenticate automation is with an API key." Its conditions:
    - treat `~/.codex/auth.json` like a password;
    - never in public or open-source repositories;
    - **never the same file across concurrent jobs or machines, so one sign-in per concurrently running worker**;
    - reseed from a trusted machine if the refresh breaks.
    
    It names no plan and says nothing about limits; third-party guides say such runs draw on the plan's own windows. We found no OpenAI terms on automated use of a plan. A Pro or Business plan's API credits could pay a direct OpenAI API backend, not Codex.
So the design has **three lanes**, and which consumer goes where is decided by its owner (the founder; for Acme, through `acme_org`):

| Lane | Credential | For | Terms |
|---|---|---|---|
| **A. Owner's agents** | `claude setup-token` of one of his Max accounts | Lab VMs and other scripts that run Claude Code for the owner's own work | Explicitly permitted for scripts; individual use |
| **A′. Owner's agents, second provider** | A ChatGPT-plan Codex sign-in (`auth.json`), one per concurrently running worker | The same, on a trusted private runner, for vendor independence | Documented by OpenAI as an advanced path; an API key is preferred |
| **B. Services** | An API key from a Console organization (Commercial Terms), billed per token or paid by a plan's included credit | Acme's production workers (the tax workers decide on live money for a company) | Clean: keys are made for this, and they can sit in a secrets manager |

**Decided so far** (2026-11-07):
- **Individual use is the normal case**, and several accounts per person are allowed when each is the owner's own and unshared. All four are the founder's.
- **Services go to lane B** (the tax workers). That is Acme's decision, asked through `acme_org`. Lane B can run on a plan's own credit:
  1. link a Console organization to one of the founder's accounts;
  2. claim the Ultra credit, $200 a month;
  3. create an API key whose spend limit equals the credit;
  4. put the key in the vault.
  
  The founder's three clicks are `of_capacity`'s task, because accounts are tenant level; `acme_org` decides the switch, and its keeper installs the key through the broker.
- **Does one account's credit cover the tax workers?** They count in Sonnet input-token equivalents: 47.8M a week, about $96 a week at Sonnet 5.5's $2 per million, or about $410 a month.
  - Shop (live money): 0.86M a week, about $7 a month, so the credit covers it many times over.
  - Marketplace (a dry-run bot): 46.9M a week, about twice the credit at its current rate. It is moving to Codex anyway.
  - **First choice for both: a Claude API key on an included credit** (`of_architect`, 2026-11-07). It needs no code change (the workers already have an `anthropic-api` backend) and uses money already paid for. Marketplace at its current rate needs about two accounts' credits, or a lower budget.
  - Codex (lane A′) is a vendor-independence option, not this week's fix. The workers decide on live money, so a move needs a shadow comparison. Acme's memory says a Codex backend exists in `tax_policy` since 10-12 and that Marketplace is planned on it (founder's yes `625b3390`); the choice is `acme_org`'s.
- For scale: the fleet spends about $9,100 a week at list price (10-31..11-06: Acme $8,100, the OF team $900). One Ultra holds about $2,600 a week (about $26 per 1% of the week, measured 2026-11-07 on ~/.claude-e), so the four accounts are about the fleet's size.

The lab stays on lane A: it is the owner's own agents. A whole-fleet move to Team or Enterprise is not proposed; the numbers do not call for it. Codex follows the same split: a ChatGPT plan for the owner's executor (RFC 0003 §4.7), and an API key for anything that serves a product.

### Inventory: `of capacity creds`

One table, from events, never values. Each row is:
- a **login**, a config dir signed in by `/login`; or
- a **token**: `setup-token`, an API key, or a Codex `auth.json`.

The columns:
- kind;
- account, as an email;
- plan (`claude_max` and so on, read from the config's `oauthAccount`);
- minted at and expires at (a setup-token lives one year);
- fingerprint, the first 12 hex digits of the token's SHA-256;
- vault item id;
- the consumers leasing it now.

A token minted before the broker is **adopted** by its owner's keeper: `of capacity creds --adopt NAME --kind setup_token|api_key|codex_auth --account EMAIL (--from-stdin | --fingerprint HEX) [--expires DATE] [--consumer C]`. The token comes in on stdin, piped from the owner's own store, and is hashed and dropped; or the owner gives the fingerprint itself. Each adoption is an `of.credential_adopted` event. **Built 2026-11-07** (step 2): `of capacity creds` lists the logins from each config dir's `oauthAccount` (account, plan, rate tier) and the tokens from events. Only the fingerprint is kept, plus the vault copy if the owner wants it there. Today's known rows:
- four logins: `~/.claude` alex@acme.example, `~/.claude-e` alex@example.com, `~/.claude-b` founder@example.com, `~/.claude-d` (Apple relay address);
- one token: the tax workers' Secret, minting account unconfirmed;
- the lab VM tokens, if any are still alive after `08333956`.

### Store: a 1Password service account

As decided for agents' secrets (founder, ask `29ea1baa`; AGENTS.md; RFC 0003 §1a):
- **The vault** is **"OF Capacity"**, in the 1Password account that `capacity.yaml` `op_account` names (its sign-in address; the owner's business account, never his personal one), with one item per token: the token as a concealed field, and the account, kind, minted and expiry dates and fingerprint as plain fields.
- **The service account** is **`of-capacity`**, with `read_items,write_items` on that vault only: no `share_items`, no new vaults.
- **Its token** is the only secret on disk, in `~/.config/of/capacity/op-sa.token` (directory 700, file 600). `op service-account create --raw` writes it there from the founder's terminal, in `of_capacity`'s own one-line task (build step 3).
- **Access.** Only committed scripts in this repository read that token and call `op` with `OP_SERVICE_ACCOUNT_TOKEN` set. The guard denies direct reads of `~/.config/of/capacity/` and a bare `op`.

### Minting: `of capacity mint <account> [--all-due]`

1. Run `claude setup-token` with that account's config dir (`env -u CLAUDE_CONFIG_DIR` for `~/.claude`), under a pseudo-terminal, so the browser opens for the OAuth.
2. **The founder's one act:** check that the page shows the expected email, and click Authorize. `--all-due` runs the accounts one after another, so this is one sitting.
3. Read the printed token from the pseudo-terminal, match it by its known prefix, and never echo it. The script's output shows only the fingerprint.
4. **Identity check.** A setup-token cannot tell which account it belongs to, and the browser may be signed in to another one. So run a minimal probe with `CLAUDE_CODE_OAUTH_TOKEN` set: its 7-day and 5-hour reset times must equal those of the config dir's own probe. The same account means the same windows. On a mismatch, the script stores nothing, and the founder's task names the email to switch to.
5. Write the token to the vault. Emit `of.credential_minted` (account, kind, fingerprint, expiry, vault item), never the value.
6. **Expiry.** A week before any token expires, the pulse raises one batched task: "Authorize N accounts". Retired tokens are listed by mint date, for revocation at claude.ai/settings/claude-code. The page shows labels, not fingerprints, so the date is the match.

### Leasing: `of capacity lease <consumer>`

Consumers are declared in `~/.config/of/capacity-consumers.yaml`. Each entry gives:
- the name;
- the owning position and organization;
- the lane;
- the sink;
- a weekly token cap.

The broker:
1. **Picks the account** by the balancing rule. That is the soonest-expiring capacity that is not about to stall, after the priority tiers (the consumer's organization and throughput, founder ask `4576f634`). A consumer never lands on an account at `week_max` or `relief_at`.
2. **Delivers without printing, into the owner's own install script.** The consumer's keeper runs `of capacity lease <consumer>`. The consumer's entry names a committed install script in the owner's repository, for example Acme's script that applies its one Secret. The broker runs that script with the token on its stdin, straight from `op read`. The token is never printed and never passes through an agent's command output. The broker writes nothing into the organization's systems itself; the script, its credentials (a kubeconfig, a VM shell) and the restart that follows belong to the owner:
   - **a lab VM** (`of_lab`'s script): `limactl shell <vm> -- sh -c 'umask 077; cat > ~/.config/claude/oauth-token'`;
   - **a k8s Secret** (the organization's script, for example Acme's, run by its keeper): `kubectl apply` of one Secret, built with `--from-file=…=/dev/stdin --dry-run=client -o yaml`.
3. **Asks for rotation, by its own probes.** The broker knows every account's utilization. When a leased account passes `relief_at` or `week_max`, it marks the lease due (`of.lease_rotation_due`) and tells the organization's structure keeper (`acme_org`), or the keeper named in the consumer's entry with that keeper's consent. That keeper runs `of capacity lease <consumer> --renew`, and the broker picks the next account. The consumer itself need not report anything.
4. **Each step is an event:** `of.lease_granted`, `of.lease_rotated`, `of.lease_released` (consumer, account, fingerprint, sink, reason). `of capacity status` lists leases under their account, so a hidden consumer becomes a visible one. Its spend, which no transcript shows, appears as utilization the sessions do not explain. The balancer subtracts the consumer's cap from that account's headroom.
5. **Lane B** leases an API key the same way. It does not rotate between accounts: a key is billed per token, and its cap is the Console's spend limit.

### What stays with people

| Act | Who | How often |
|---|---|---|
| Create the vault and `of-capacity` | founder | once, in `of_architect`'s one terminal line |
| Authorize a setup-token in the browser | founder | once per account and year, batched |
| Create Acme's Console API key and its spend limit (lane B) | founder | once, if he chooses lane B |
| The install script and its credentials for a sink inside an organization | that organization's keeper, through its structure keeper (Acme: `acme_org`, then `k8s_keeper`, with `platform_owner`'s yes) | once per consumer |
| Run `of capacity lease … --renew` when a rotation is due, and restart the consumer | the consumer's keeper | per rotation, unless the consumer reads the Secret live |

### Build order

1. **Decisions:**
   - The four accounts are the founder's own: confirmed 2026-11-07.
   - **Keep the four accounts as they are; no written question to Anthropic** (the founder, in `of_capacity`'s session, 2026-11-07, about 12:34Z: «такой вопрос, конечно, задавать не нужно. Мы уже разобрались эту всю историю»). The balancer keeps spreading the fleet over them.
   - The tax workers' lane is Acme's, asked by `acme_org`.
2. **Inventory** (`of capacity creds`, `adopt`): events only, no secrets. It can ship before the vault.
3. **Vault and service account** (code built; the founder's sitting waits until a consumer needs a token, since none does on 2026-11-08): the founder runs `of capacity vault-setup` himself, in a normal terminal tab. It is a committed command, so no task text has to carry the token file's path. It uses his own `op` session (Touch ID), creates the vault and `of-capacity`, and writes the token straight to its mode-600 file. It refuses to run from automation or an agent's pane, and never overwrites an existing token. The step is `of_capacity`'s own founder task. It is filed once step 4's code exists, so that the vault line, the first Authorize clicks and, if `acme_org` chooses lane B, the Console key make one sitting. Acme's 1Password line is `acme_org`'s own task (`569478d4`), by the boundary rule.
4. **Mint** (`of capacity mint`): pseudo-terminal capture, identity check by probe windows, vault write, events. First sitting: up to four Authorize clicks. **Built 2026-11-07**, together with step 3's code: `of capacity vault` checks that the service account sees exactly "OF Capacity", and `of capacity mint <config-dir> | --all-due` mints, checks and stores. Both refuse until the founder's vault line has run. The live test is the first sitting.
5. **Lease through the owner's install script**, with rotation notices: first the lab VMs, with `of_lab`'s script and VM bring-up.
6. **Acme's consumers**, through `acme_org`. **Superseded on 2026-11-07:** both tax workers left the Claude subscription for Codex, Marketplace at 14:56Z and Shop at 22:34Z on the founder's own word. So no Console key is needed, and the founder's task `06ccf5a6` was withdrawn. Cleaning up the cluster's unused setup-token Secret is `catalogue_keeper`'s. The earlier plan follows for the record. The founder decided (ask `dbc59ec7`, 2026-11-07): only the Shop tax worker moves to lane B; Marketplace stays on the subscription until Codex. The key comes from the Console organization linked to alex@acme.example, Ultra since 2026-01, which carries the $200 monthly credit; with no card and no auto-reload, requests stop when the credit runs out. The founder stores it with `of capacity creds --store NAME --kind api_key --account EMAIL`, in his own terminal: a hidden prompt, straight to the vault, only the fingerprint recorded. `catalogue_keeper` installs it with its own script.
7. **Expiry watch** and the revocation list.
8. **Codex**, after RFC 0003's executor exists. Device-code login, or an `auth.json` refreshed by each run and written back to the vault. Lane split as above, after OpenAI's terms are read.

## Order and the owner's say

Within a team, move the positions whose loss costs least first and the structure keeper last, announced, so it is idle when its turn comes. Production positions go last, on the owner's explicit go, and never while busy. Between organizations the owner decides: the mover asks with `of ask`, with the order proposed.

## First use (2026-11-06)

The OF team (seven positions) moved from `~/.claude-b` to `~/.claude-e` in about eight minutes, one at a time, with `of capacity move`. Every holder answered the check question correctly; `--resume` kept every session id. Contexts were 105k to 819k tokens. The first turn on the new account writes the whole context to its 1-hour cache: measured from the transcripts, $0.79 to $6.80 at list price per holder, $26.91 for the team, about 1.1% of a week at the October rate of about $24 per point. `~/.claude-b` reached its weekly limit in the middle of the move; the last two holders were moved after it, without a turn on the old account.

## Roadmap (mandate 1 §5)

- `of capacity move --all <from-dir>`: the plan, executed one holder at a time, stopping at the first failed check.
- Tests on fixtures (`tests/test_capacity.py`) and a Lab scenario with `of_lab`: two config dirs in a VM, a move between them, verification by the check question.
