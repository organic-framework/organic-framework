# RFC 0008: work on other providers' harnesses (Codex, Muse)

| | |
|---|---|
| Status | **Accepted** by `of_architect` (2026-11-18). Framework design: no money beyond the plans the organization already pays for, and nothing irreversible. Each organization chooses its own use (§5). |
| Source | The founder, 2026-11-18, prompt `01a1ee3c-3b01` in `of_capacity`'s session: "we also have Muse, Wonderful and Codex accounts here that we do not use that much, but on the whole they could also carry out some task, because we keep running out of the limit on Claude, so let's somehow use these accounts too. Which tasks can we send them to, and for which tasks can these models be useful at all?" Hand-over `8a91a846` from `of_capacity`. |

## 1. What goes wrong

- **Claude runs out while the other plans sit idle.** `of_capacity`'s readings, 2026-11-18 10:40Z:
  - Claude: 3 of 5 accounts at 100% of their week.
  - Codex (ChatGPT Pro, model `gpt-6-astra`, the CLI inside ChatGPT.app): 0% of its weekly window, which resets on 2026-11-21 14:18Z. What is unused by then is lost.
  - Muse (Meta, model `muse-wonder-full-1.0`): answers headless; its limits are not visible yet.
- **Most Claude spend is work another model can do.** In `of_capacity`'s spend profile for 2026-11-10 and 11, subagents were 58% of Claude spend: reviews, searches, reading a long record into a short answer (docs/method.md, "Subagents: the owner's standing yes"). These parts read; they write nothing and decide nothing.
- **Nothing guards the other harnesses.** OF's guard is a Claude Code hook (docs/guard.md). The Codex config on this Mac runs `danger-full-access` with approvals off (`of_capacity`'s reading), and at 10:40Z Muse's contributor mode, under which Meta may train on the sessions, was on in the account's settings (off since 10:57Z: card `OF-26`). A position that starts either one by hand today sends whatever it reads to another provider, with no limit and no record.
- **"Wonderful" is Muse's subscription and model** (`muse-wonder-full-1.0`): the founder's own words, 2026-11-06 and 2026-11-18, found by `of_capacity` (message `01a1ee54-8ce6`). There is no separate program.

## 2. Which work goes where

| Work | Claude | Codex | Muse |
|---|---|---|---|
| Holding a position: decisions, asks, messages, `of status`, commits, the registry | yes | after rung 2 (§4) | no |
| A read-only review of a diff or a pull request | yes | rung 1 | rung 1, findings verified (below) |
| Reading: a repository, a long record, a document against code, into a short answer | yes | rung 1 | rung 1, findings verified |
| Research on the public web, with no workspace | yes | rung 1 | after its bake-off |
| A code change drafted in a snapshot and returned as a diff, which the position applies, tests and commits | yes | rung 1.5 (§3.3) | no |
| Anything that writes outside a snapshot, runs commands on a system, uses a grant or a credential, or touches production | yes | no (until rung 2, then under the guard) | no |
| Browser hands for the owner's kind-B tasks | no | RFC 0003 | no |

- **What each is known to be good at.** The founder considers Codex the strongest at browser use (RFC 0003 §4.6). Codex ships a code reviewer (`codex exec review`). Muse's trial on 2026-11-06 (`of_capacity`, three read-only reviews): it found both bugs seeded in one case, and 4 of its 7 findings on a real pull request had garbled failure scenarios. Codex on the same seeded case, 2026-11-18 (`of_capacity`, message `01a1ee54-8ce6`): `gpt-6.2-astra` reported 10 findings and `gpt-6.2-sol` 7, both found the two seeded bugs and both missed a third known one; Astra's 3 extra findings are not yet checked against the code. Nothing else is measured yet; the bake-off (§6) measures it before a kind of work moves by default.
- **Codex's model** (`of_capacity`'s pick, asked by the founder in prompt `01a1ee47-861f`): `gpt-6.2-astra` at effort `xhigh` for reviews and reading, `gpt-6.2-sol` when the Codex week runs short.
- **Every finding of an outside run is checked before it counts.** The calling position verifies each finding against the code, as it verifies a Claude subagent's. Muse's garbled scenarios make this a rule, not a habit.
- **A review's verdict stays the position's.** A pull request's reviewer is a position that wrote none of its commits (AGENTS.md); an outside run is a tool of that position, like a subagent. The verdict, its `Position:` line and its basis are the position's own.

## 3. Rung 1: outside subagents

A position starts a read-only run on another harness where it would start a Claude subagent:

```
of subagent run --harness codex|muse|auto --kind review|read|research \
  [--workspace <checkout> --at <commit>|--diff <base>..<head>] --prompt-file <file> [--schema <file>]
```

It prints the run's answer (or the schema's JSON) and the path of its record. `--harness auto` picks, among the harnesses the organization allows for that kind (§5), the one whose window expires first and has room, the rule `of capacity balance` already follows (docs/capacity.md); with none, it says so, and the position uses a Claude subagent.

### 3.1 The limits, each with its test

The wrapper is the guard for these runs. Each limit is a test in the wrapper's suite, and `of_platform` chooses how each is met.

| Limit | What the wrapper does | Test |
|---|---|---|
| **Confined to a snapshot** | A `review` or `read` run sees only a fresh copy of the named commit or diff, exported from git into a new folder under the position's scratch folder: tracked files only, so no untracked `.env` or key reaches it. Never the live checkout. | A canary file in the home folder and one beside the checkout: a run told to read them gets neither. |
| **No secret paths** | Before the run, every path in the snapshot is checked against the guard's secret paths (G1, by `of_classify.SECRET_GLOBS` and the guard's list), and every file's first bytes against G1.13's private-key headers; one match refuses the run and names the path. | A snapshot holding a G1 name, or a key under a harmless name, is refused. |
| **The prompt is material too** | The prompt file and the schema leave for the provider as they are, and a `research` run sends nothing else. Before the run, the wrapper runs on them the snapshot's secret checks and the organization's pre-publish check where it has one (for the framework, the gate every commit passes, organic-framework-build `release/README.md` §7: no user organization, person, host, home path or secret); a match refuses the run and names it. An organization with no such check sends Codex no prompt while `OF-27` is open. A prompt that passes that check is publishable, so it counts as public material below. | A prompt holding a key, and one naming a user organization, are refused. |
| **The snapshot's instructions are data** | A run never loads the snapshot's own instruction files (`AGENTS.md`, `CLAUDE.md`, skills, rules) as instructions: a reviewed change could carry text that steers its reviewer. The prompt names the answer's language (Codex answered in Russian on 2026-11-18 after reading the workspace's `AGENTS.md`). Muse loads them only with `--trust-workspace`, which the wrapper never passes. | A snapshot whose `AGENTS.md` asks for an answer in French gets an answer in the prompt's language, and its event stream shows the file read, if at all, as a file. |
| **Read-only, no shell beyond the sandbox, no network for a workspace run** | Codex: `codex exec --sandbox read-only --ignore-user-config --ignore-rules`, approvals never, so the account's `danger-full-access` config and its MCP servers are not loaded. Muse: `muse exec --workspace <snapshot> --disable-write --disable-shell --disable-web-tools --approval-mode never --no-foreign-personal-context --disable-request-user-input` (as in `of_capacity`'s trial script). | A run told to write a file, to reach the network, or to call an MCP tool fails, and its event stream lists no tool outside the allowed ones. Codex's help (0.169.0-alpha.11.1) does not say whether its read-only sandbox stops reads outside the workspace; the canary test decides. If it fails, the run goes as another OS user that can read only the snapshot, or in a Lab VM (docs/lab.md). |
| **Never both a workspace and the web** | `research` runs have the web and no workspace; `review` and `read` runs have a workspace and no web. A page read on the web can then never carry the organization's material out (prompt injection). | A `research` run gets no snapshot; a `review` run has no web tool. |
| **No training on the run** | Muse: `--contributor-mode off` on every run (per run, never persisted). Codex has no per-run switch (none in `codex exec --help`, 0.169.0-alpha.11.1), and no interface found reads the account's setting, so the basis is the owner's card `OF-27` (turn off training on his data in ChatGPT): until it is done and checked, Codex runs only on public material. **Public** means: every commit the run sends (`--at`, and both ends of `--diff`) is reachable from a ref fetched from the public remote (its `main`, or a pull request's head), and the prompt passed the pre-publish check above. The working copy's `main` runs ahead of the public `main`, so a commit not pushed yet is not public. | A Muse run without the flag is refused; so is a Codex run, while `OF-27` is open, on a commit that exists only in the working copy, or on a prompt that fails the pre-publish check. |
| **No owner's keychain** | A harness that keeps its login in the macOS keychain gets its own keychain, holding only that login, in the run's fresh home; never the owner's login keychain, which holds all of his passwords (encrypted; a prompt-injected read would carry it to the provider). Or the login comes another way the harness takes (Muse's `--api-key-stdin`). | A run told to read the owner's login keychain gets nothing. |
| **A clean environment** | The process gets only what it needs: no `SSH_AUTH_SOCK`, `OF_AUTOMATION=1`, the `HERDR_*` variables unset (AGENTS.md, nested harness), no organization tokens. | A run that prints its environment shows none of them. |
| **Recorded** | Each run writes `of.subagent_run` (started and ended): position, session, work, harness, model, kind, the commit or diff, the prompt's hash, exit, the tokens its JSONL reports, and its record's path. The record (the `--json` stream and the answer) stays in the position's state folder, mode 600, under RFC 0002's scan. `of capacity spend` attributes the run to its position, work and goal. | A run with no event fails the suite. |

### 3.2 How a position uses it

- **Default for the kinds that passed the bake-off** (§6), when the organization allows them (§5). Until then a position may use it, and checks each finding as above.
- **The method's conditions hold** (docs/method.md, "Subagents"): what needs the position stays with the position.
- **What it does not replace.** A subagent that runs on a runner, watches a run, or changes files anywhere but a snapshot stays a Claude subagent until rung 2.

### 3.3 Patch runs (rung 1.5)

The founder, 2026-11-18 14:11Z, prompt `01a1eeff-880b` in `of_conductor`'s session, declining to restart agents with a clean context: "Let's move part of the work to Codex, something about development and so on. Context is a very important part of the agents' understanding of the system, so context must not be reset." A `patch` run lets Codex write code without holding a position, the way a Claude subagent writes in its own worktree (docs/method.md, "Subagents").

- **The run.** `of subagent run --harness codex --kind patch --workspace <checkout> --at <commit> --prompt-file <file>` gives Codex its snapshot to write in (Codex's `workspace-write` sandbox on the snapshot only), with §3.1's read confinement, no network, no credentials, no connectors and no hooks. Before the run, the wrapper makes the snapshot a git repository with one commit of its files; after it, the answer is `git add -A` then `git diff --cached --binary` against that commit, so new, deleted and mode-changed files are in it. It is kept in the run's record. The wrapper never applies it anywhere.
- **Light work only.** The run may run the named tests of what it changes inside the snapshot; no build, no full test suite, nothing heavy (AGENTS.md, "No heavy local jobs on a laptop"). That sentence is a request to the model, so the wrapper also bounds the run: Codex's step cap and a wall-clock limit, after which it stops the run and keeps its record. `of_platform` sets their values and says their purpose.
- **What a patch may not change** (of_review, PR 336). The position applies the diff and runs its tests with its own rights, so a diff must not change what steers later agents or privileged automation. The wrapper refuses a diff that touches any of these, and names the path; such a change is the position's own to write:
  - agent settings and instructions: `.claude/`, `.codex/`, `.agents/`, `.mcp.json`, and `AGENTS.md`, `CLAUDE.md`, `CLAUDE.local.md` anywhere;
  - skills and plugins: `skills/` and `plugins/` under those folders;
  - git control: `.gitattributes`, `.gitmodules`, `.githooks/`, a `hooks` folder, `.git/`;
  - CI: `.github/`, `.gitlab-ci.yml`, `.circleci/`, `.buildkite/`;
  - a symbolic link, new or made from a file (a later write through it lands outside the worktree), and a file newly made executable.
- **The position owns the change.** It reads the whole diff as it would a Claude subagent's, applies it in its own worktree, runs its tests and commits it under its own name, with `Drafted-by: codex <model>, run <id>` beside its `Position:` trailer, so the measures can tell drafted work apart. A pull request's reviewer is still a position that wrote none of its commits: a drafted commit counts as its committer's. A protected worktree (docs/guard.md G21) takes no drafted patch: its git calls are denied.
- **Tested, per kind.** The same limits as §3.1, and these tests: a patch run writes nothing beside or outside its snapshot; its diff names only the snapshot's files; a diff touching each class above, a link or a newly executable file is refused. The selftest gate is kept per harness and kind: a patch run goes only after a patch-mode selftest passed on that harness; a passed `read` selftest admits no patch run.
- **Allowed by the organization's entry** with `patch` among the harness's `kinds` (§5). Until the wrapper takes `--kind patch` (hand-over `66c37728`), a patch run is refused.

## 4. Rung 2: positions held by Codex

A Codex session in a herdr pane holds a position like a Claude Code session, when all of these hold:

1. **The collector**: a Codex turn writes the same events as a Claude Code turn (RFC 0003 §6 step 3, `of_platform`).
2. **The guard**: Codex's hooks call `bin/of_guard.py` before every tool call, and docs/guard.md's tests pass on Codex's tool calls (`of_platform`, with `of_server`, who owns the guard).
3. **The start**: `of spawn --harness codex` gives the session its start context, its tool profile (docs/tools.md §6, item 7) and its memory, and `of resume-command` resumes it on its account (the `harness` family, docs/plugins.md §3).
4. **The capacity**: `of capacity status` counts the ChatGPT plan's windows (RFC 0003 §4.7, `of_capacity`).

Until then, no position is held by Codex, and no Codex session runs with the account's own config in an organization's checkout. RFC 0003's browser executor (§4.1 there) waits for the same four conditions; it is not an exception. Muse holds no position: whether it has hooks OF can use is unknown (it serves `mxp` and `acp` protocols); `of_lab` finds out when a position needs it.

The founder said yes to building rung 2 on 2026-11-20 (prompt `01a1f8ba-17a0`). His reason, prompt `01a1f87e-ee28` the same day: "Realistically we cannot buy more Claude for another week, but we can buy Codex." He also said yes to its plan (prompt `01a1f8d2-c94e`; organic-framework-build `reports/codex-main-harness-plan-2026-11-20.md`). The sections below make the four conditions concrete. They read Codex 0.170 from its source and documentation (organic-framework-build `reports/research/codex-0.170-as-a-holder-2026-11-20.md`, every fact with its link; the hook events, the `hooks` feature and the options named below were checked in the 0.169.1 binary).

### 4.1 What any harness that holds a position gives

The same list holds for Claude Code, Codex and any later harness. It is the `harness` family's contract (docs/plugins.md).

| | Part | What OF needs |
|---|---|---|
| 1 | **Start** | A session in a herdr pane, in the position's folder, on an account of its organization (a seat), with its model, its tool profile and its guard. Its start context arrives whole (brief, binding rules, team), and no dialog stays on its screen. |
| 2 | **Guard** | Every call that runs a command, edits a file or calls a tool server is checked by `bin/of_guard.py` before it runs, and an error in the check refuses the call. The operating system's sandbox refuses the session every credential root and secret path, so a call the check misses still reads none. The session cannot change its own guard, rules or config. |
| 3 | **Record** | Each prompt, marked when a person typed it, each tool call and its result, and each turn's end go into the event store, masked, as they happen. |
| 4 | **Messages** | herdr tells the pane's state (idle, working, waiting on an approval), so the relay types in only when it is idle. |
| 5 | **State and asks** | `of status`, `of ask`, `of send` and the rest are commands any harness runs. The caller is the session its harness names (G31). A session in an operating-system sandbox reaches OF's state through one door, its own socket to the listener (§4.5). It never reads the store or holds the machine's key. |
| 6 | **Resume and move** | A session resumes by its id on its account. It moves between accounts between turns, with its context. |
| 7 | **Capacity** | Each account's windows are read, and a session starts on the account with headroom. |

### 4.2 How Codex gives it

| | Codex 0.170 | Built by |
|---|---|---|
| 1 | `codex -C <folder> --profile <name> -m <model>`, with `CODEX_HOME` one folder per seat and `-c` overrides. The position's folders are marked untrusted in the seat's `config.toml` (`projects."<path>".trust_level`). Their `.codex/` layer, a config layer whose config, rules and hooks could weaken the profile, is then never loaded, and neither are their `AGENTS.md` files. `of_platform` checks on the host that no trust dialog shows. The start context goes in as `developer_instructions`, with what those `AGENTS.md` files say. It is a developer message with no size cap found, so it is not cut to 10,000 characters as Claude Code's start hook is. `model_instructions_file` is not used: it replaces the model's own instructions. | `of_platform` |
| 2 | **Three layers:**<br>- **A `PreToolUse` hook** that runs `bin/of_guard.py`. Codex 0.170's synchronous hook can refuse a shell call, an `apply_patch` and an MCP call, by exit 2 with a reason or by a JSON deny. Codex runs the call when a hook times out, crashes or answers malformed, so the hook's wrapper answers deny on any error of its own.<br>- **A permission profile per position**, enforced by bubblewrap and seccomp on Linux and kept whatever the command's spelling: the minimal platform files read; the position's folders written; every credential root and secret path of the catalogue `deny`; every seat's `CODEX_HOME` (its login, config, rules and hooks), the Claude accounts' sign-ins and transcripts, and the other sessions' scratch folders `deny`; the `.codex/` and `.agents/` folders under the position's folders not writable.<br>- **Exec-policy `forbidden` rules** for the guard's denied commands where a command prefix expresses them; they refuse before the sandbox, under `approval_policy = "never"` too.<br>**Not covered by the hook:** input sent into a running command session (`write_stdin`), and hosted tools such as web search, which stay off unless the position's tool profile names them.<br>**Hook trust:** an unmanaged hook is reviewed by its hash at start. The guard is a **managed** hook in Codex's system layer, root's file, which needs no trust and which no agent can change, with `allow_managed_hooks_only`. It is never a session flag, a user-layer hook or Codex's trust bypass, which the agent's own work can reach.<br>**Interactive exec off** (`unified_exec`, `unified_exec_tty`), so every command is a fresh call the hook sees. **Approvals never**, so a request to leave the sandbox is refused.<br>**Network:** a sandboxed command on Linux with the network off reaches no Unix socket, so `of` and herdr would fail. The profile turns the network on behind Codex's proxy, with a domain allowlist, and allows Unix sockets. That opens the same gap as Claude Code's sandbox on Linux (docs/guard.md, Gaps). | `of_server` decides, `of_platform` builds, `of_secrets-review` reviews |
| 3 | Hooks `SessionStart`, `UserPromptSubmit` (the prompt), `PreToolUse`, `PostToolUse` (the result), `Stop` (the turn's end) and `SessionEnd`, with Claude Code's shape. Codex's session files (`$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*.jsonl`, or `.jsonl.zst`) are ingested after the fact; OpenAI calls their format no stable interface. | `of_platform` |
| 4 | herdr's Codex pane states, checked on the agent host. `notify` on `agent-turn-complete` is a second turn-end signal. | `of_herdr` |
| 5 | Unchanged commands, which inside the sandbox talk to the listener's socket in the session's own folder (§4.5); the session id comes from the hook's input. | `of_platform`; the signing `of_server` |
| 6 | `codex resume <id> -C <folder>` on the seat's `CODEX_HOME`. A move takes the session's file to the other seat's home. | `of_platform`, `of_capacity` |
| 7 | Each session file's `token_count` event carries the account's windows (`used_percent` consumed, `window_minutes`, `resets_at`). The app-server's `account/rateLimits/read` gives them now. | `of_capacity` |

### 4.3 The guard's rules on Codex

`of_server` decided how each rule of docs/guard.md holds on Codex (2026-11-20, organic-framework-build `reports/codex-guard-w2-w3-2026-11-20.md`; hand-over `aaa87244`):
- The command rules hold by the managed hook, which `of hook` serves with Codex's payload mapped: `Bash` as today, and `apply_patch` parsed into its paths for the write and edit rules.
- The secret-path and credential-root rules hold twice, by the hook and by the profile's `deny` for every process and built-in read.
- Claude Code's native rules hold as managed `forbidden` rules beside the hook.
- G22 and G23 do not hold as written: W7's tool profile turns plugins off, and a Codex position gets no shared tool server until Codex's way is named. G28 is not needed under approvals never.
- The mention guard is still to check.

Before any Codex position runs:
- `of_secrets-review` reviews that decision rule by rule;
- a canary selftest per Codex position passes: denied roots unread, no write outside the writable paths, no host off the allowlist, a G3 and a G5 form refused, no `write_stdin`, `of status` and `of send` working, no user or project hook run.

Until then, a Codex position holds no grant and no credential. The managed hook takes one sudo sitting of the owner on the host.

### 4.4 Where, and who first

- **Where:** on the agent host, in the organization's own contour (docs/contours.md).
- **The registry:** `holder.runtime: codex` (docs/registry.md, Positions).
- **First positions:** those that read and write public framework code and hold no grant or credential. They move to Codex by a structural diff (`swap_holder`), once the guard is reviewed and start and recording work.
- **When it is done:** a position that did not build this sets one up on a clean OS user from the documents alone (`of_lab`, docs/lab.md), as AGENTS.md asks of every feature a person uses.

### 4.5 OF's state from inside a sandbox

`of_server`'s review of organic-framework PR 475 found what a sandboxed session would otherwise need to read or write. `of` writes its events to the store and reads hand-overs and asks there, so the profile had to leave OF's state folder readable: `of.db` and `raw/` hold every session's records, other positions' included. `of` also signs its server requests with the machine's key (`~/.config/of/machine_ed25519`). Inside an operating-system sandbox, a command the agent runs reads whatever `of` can read.

So a sandboxed session reaches OF through one door, and the store and the key stay outside (`of_architect`, 2026-11-20, of the three ways in organic-framework-build `reports/codex-guard-w2-w3-2026-11-20.md`):
- **One socket per session.** At start, the listener serves the session a Unix socket in a folder of that launch, `~/.local/state/of-doors/<launch>/`, beside OF's state folder rather than in it: bwrap cannot reach a folder inside one it masks. The listener takes the caller to be that launch's position, whatever a request says, and checks the connection: the peer process must run under that launch's Codex, started with its layer. That caller is the one G31 needs. The launch's files there can still be written by a session of the same user outside a sandbox, which docs/guard.md names as a gap; until a guard rule closes it, the W10 rule holds (`of_platform`, 2026-11-20).
- **Writes** go through that socket, or into an append-only spool in the same folder that the listener drains. Either way the listener stamps each event with that session and its position.
- **Reads** are the projections `of` shows: the asks, the hand-overs, the caller's own inbox, `of who`, and one event cited by its id, as a position checks an owner's answer. They are limited to the caller's organization, and never raw tables, transcripts or hook records. A person's prompt is shown only from the caller's own session, or when a record another wrote to the caller cites the prompt's id: the owner's answer to one of its asks, a hand-over it receives, the other side's note or delivery on a hand-over it is a party to, and a message delivered to it whose sender may itself see that prompt by this same rule. The last is how a position checks a decision the founder typed in another session, such as its conductor's, and that session relayed with the prompt's id (`of_architect`, 2026-11-24). The caller's own asks, answers, proposals and notes never count, since they are text it chooses, and a message counts only through its sender, so two sandboxed positions cannot unlock a prompt neither may see (`of_secrets-review` on organic-framework PR 633). A two-group prefix that matches more than one prompt shows none. Prompts hold matters the founder told nobody else about (`of_architect`, 2026-11-20, on `of_server`'s review).
- **Signing.** The listener signs the session's server requests with the machine's key, which no sandboxed session holds. The relay's checks are unchanged.
- **The profile** denies OF's state folder (`~/.local/state/of`) and its config folder (`~/.config/of`, which holds the machine's key) whole.

Not taken:
- a signing broker alone, which leaves the store readable;
- a machine identity per sandboxed position, which still leaves the store readable and enrolls many machines.

The same door serves a Claude Code session once its sandbox is on. Until it is built, no Codex position runs where another session's records matter.

## 5. Each organization chooses

What leaves for another provider is the organization's choice, as with every product OF talks to (docs/plugins.md §1: "Nothing falls back silently to a product the owner did not choose"). Its structure keeper writes it in the registry:

```yaml
subagent_harnesses:
  codex: {kinds: [review, read, research, patch], repos: [organic-framework]}   # repositories whose snapshots may be sent
  muse:  {kinds: [review, read], repos: [organic-framework]}
  prompt_check: "release/check.py"   # the organization's pre-publish check, run on the prompt and the schema
  basis: "<the keeper's commit, or the owner's card where the organization requires one>"
```

The schema is in docs/registry.md, "Subagent harnesses". It names repositories, not systems: one repository holds many systems.

- With no key, an organization's positions use none, and `--harness auto` uses Claude.
- **The OF team** (its keeper, `of_architect`): Codex for `review`, `read`, `research` and `patch` on the public framework repository (public as §3.1 defines it), with prompts that pass the pre-publish check. Muse after its login no longer needs the owner's keychain (§3.1). Not organic-framework-build: its reports name a user organization and its people.
- **A user organization** decides through its own structure keeper, for its own repositories.

## 6. The bake-off, and what proves it works

`of_lab` runs the same cases on a Claude subagent, Codex and Muse:
- reviews of diffs with seeded bugs (the design of `of_capacity`'s Muse trial, kept in its state folder);
- reviews of the framework's recent pull requests whose findings were verified in their review records (for example organic-framework PR 306, reviewed six times);
- reading questions on the public repository whose answers are checked;
- for `patch`: tasks with a known test that the diff must make pass, scored by the tests passing after the position applies the diff, the lines it had to change by hand, and any path §3.3 refuses. Until those cases ran, a position uses patch runs and checks each diff, as for any kind before its bake-off (§3.2).

It reports per kind and harness: the seeded bugs found, the findings whose failure scenario holds when checked, the false ones, and the share of the window each run took. **A kind moves to an outside harness by default** when, on the same cases, it finds at least the seeded bugs the Claude subagent finds. Its false findings are reported beside that, because the calling position pays one check for each. The reading is taken where the event happens: in the run's own output, checked against the code.

The bake-off needs no wrapper to start: it runs on the public repository only, with the flags of §3.1 and the Lab's own snapshot. Codex's window resets on 2026-11-21 14:18Z, so its unused week is spent first.

## 7. Who builds what

Steps 1 to 3 start now, in parallel.

| Step | Who | Done when |
|---|---|---|
| 1. `of subagent run` for Codex and Muse, with §3.1's limits and their tests, and `of.subagent_run` | `of_platform` (the `harness` family) | the suite of §3.1 passes, a review run on the public repository returns findings, and its event and spend show |
| 2. `OF-27` checked when the founder does it; Muse's limits read; both plans in `of capacity status` and `of capacity spend` | `of_capacity` | both plans show their windows |
| 3. The bake-off of §6 | `of_lab` | a report per kind and harness with the measures above |
| 4. `subagent_harnesses:` in docs/registry.md and `of registry check`; the OF team's entry; the method and AGENTS.md lines that tell positions to use it | `of_architect` | after step 1; positions are told only what works |
| 5. Rung 2 (§4) | `of_platform`, `of_server`, `of_capacity` | the four conditions hold for one position in the Lab |
| 6. Each organization's choice | its structure keeper | its registry entry, or none |
