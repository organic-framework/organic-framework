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
| Anything that writes, runs commands on a system, uses a grant or a credential, or touches production | yes | no (until rung 2, then under the guard) | no |
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
- **What it does not replace.** A subagent that changes files, runs on a runner, or watches a run stays a Claude subagent until rung 2.

## 4. Rung 2: positions held by Codex

A Codex session in a herdr pane holds a position like a Claude Code session, when all of these hold:

1. **The collector**: a Codex turn writes the same events as a Claude Code turn (RFC 0003 §6 step 3, `of_platform`).
2. **The guard**: Codex's hooks call `bin/of_guard.py` before every tool call, and docs/guard.md's tests pass on Codex's tool calls (`of_platform`, with `of_server`, who owns the guard).
3. **The start**: `of spawn --harness codex` gives the session its start context, its tool profile (docs/tools.md §6, item 7) and its memory, and `of resume-command` resumes it on its account (the `harness` family, docs/plugins.md §3).
4. **The capacity**: `of capacity status` counts the ChatGPT plan's windows (RFC 0003 §4.7, `of_capacity`).

Until then, no position is held by Codex, and no Codex session runs with the account's own config in an organization's checkout. RFC 0003's browser executor (§4.1 there) waits for the same four conditions; it is not an exception. Muse holds no position: whether it has hooks OF can use is unknown (it serves `mxp` and `acp` protocols); `of_lab` finds out when a position needs it.

## 5. Each organization chooses

What leaves for another provider is the organization's choice, as with every product OF talks to (docs/plugins.md §1: "Nothing falls back silently to a product the owner did not choose"). Its structure keeper writes it in the registry:

```yaml
subagent_harnesses:
  codex: {kinds: [review, read, research], repos: [organic-framework]}   # repositories whose snapshots may be sent
  muse:  {kinds: [review, read], repos: [organic-framework]}
  prompt_check: "release/check.py"   # the organization's pre-publish check, run on the prompt and the schema
  basis: "<the keeper's commit, or the owner's card where the organization requires one>"
```

The schema is in docs/registry.md, "Subagent harnesses". It names repositories, not systems: one repository holds many systems.

- With no key, an organization's positions use none, and `--harness auto` uses Claude.
- **The OF team** (its keeper, `of_architect`): Codex for `review`, `read` and `research` on the public framework repository (public as §3.1 defines it), with prompts that pass the pre-publish check. Muse after its login no longer needs the owner's keychain (§3.1). Not organic-framework-build: its reports name a user organization and its people.
- **A user organization** decides through its own structure keeper, for its own repositories.

## 6. The bake-off, and what proves it works

`of_lab` runs the same cases on a Claude subagent, Codex and Muse:
- reviews of diffs with seeded bugs (the design of `of_capacity`'s Muse trial, kept in its state folder);
- reviews of the framework's recent pull requests whose findings were verified in their review records (for example organic-framework PR 306, reviewed six times);
- reading questions on the public repository whose answers are checked.

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
