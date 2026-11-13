# OF's own health: checks, one page, one card

Status: **design**, 2026-11-14, `of_architect`.

**Basis.**
- The founder asked for a top-level look at where OF's tools fail next (2026-11-13, through `of_conductor`).
- The review that followed found that the team learns of failures from his screenshots. It was the OF team's private review of 2026-11-14 (S4 and R9).
- On that review he chose to keep his machine on the working copy, where the team tests. The checks below therefore also catch a broken commit quickly (R3).

## 1. What it is

A small job checks OF itself every minute on each machine, and of-server checks itself. The result is:
- one current state per check;
- an event when a check changes state;
- one page that shows it all;
- a card to the owner only when his own work is hit.

The checks never run on the listener's loop, because the listener is one of the things they watch.

## 2. The checks

| Check | What it measures | Degraded | Failed | Runs in | Owner |
|---|---|---|---|---|---|
| `server.health` | of-server's answer time on `/v1/health` | over 2 s | over 20 s, or no answer | `of health run` | `of_server` |
| `relay.oldest` | the age of the oldest message queued for an idle receiver | over 10 min | over 60 min | of-server | `of_server` |
| `listener.lag.<processor>` | events behind, and seconds since the processor's last tick | over 2 min | over 10 min | `of health run` | `of_platform` |
| `view.age.<view>` | the age of each view a screen or the CLI reads | over the view's declared lag | over 5 times it | `of health run` | `of_platform`, with the view's owner |
| `hooks.errors` | hook calls that failed or spooled, out of all calls, in the last 10 min | over 1% | over 10% | `of health run` | `of_platform` |
| `hooks.latency` | the hooks' p99 time per event kind, in the last 10 min | over 1 s | over 5 s | `of health run` | `of_platform` |
| `machine.memory` | swap used, out of swap | over 80% | over 95% | `of health run` | `of_platform` |
| `machine.disk` | free disk | under 25 GiB | under 20 GiB (the pulse's card) | `of health run` | `of_platform` |
| `commit.smoke` | the working copy's newest commit passes the smoke check (§4) | — | it does not | the commit's hook, and `of health run` for the newest commit | `of_platform` |

A limit here is the starting value. Its owner tunes it in this table with the evidence, never in the code alone.

## 3. Where the result goes

- **The state.** `of health run` writes the current state of every check to `~/.local/state/of/health.json`: the state (`ok`, `degraded` or `failed`), the value, the limit, and since when. of-server keeps its own checks and serves them to its machines.
- **The event.** One `of.health_changed` event `{check, state, value, limit, since, owner}` is written only when a check changes state. The log does not grow by one event a minute.
- **The command.** `of health` prints the table.
- **The page.** Studio's Health tab shows the table, with each failed check's owner and the time since it failed (`of_studio`).
- **The owner of a check** gets one `--fyi` when it fails, and one when it is back.
- **The person.** A card goes to the organization's owner only when his own work is hit, after 5 minutes failed:
  - `server.health`;
  - `relay.oldest`;
  - the age of the view his questions screen reads;
  - `hooks.errors`;
  - `commit.smoke`.

  It names the check, its owner and what he sees meanwhile. It closes itself when the check is back. The other checks go to their owners only.

## 4. A broken commit, caught quickly (R3)

The founder's machine runs the working copy's `main`. So a commit that breaks a shared module must be caught before it runs, or within a minute after.

- **The smoke check** runs in the repository's pre-commit hook, in a few seconds:
  - every module in `bin/` imports;
  - `of hook` takes a synthetic payload of each event kind and writes to a scratch store;
  - `of registry check` passes on the registries the commit touches;
  - `of status`, `of asks` and `of who` answer.

  It is not the test suite, which runs in CI.
- **A commit that passes and still breaks** at runtime shows within a minute as `hooks.errors` or a `view.age`.
- **One commit can be rolled back.** `of rollback <commit>` reverts exactly that commit's own paths, in a temporary worktree so that the hooks run. It never commits from the shared index, and it records the commit author's position in the message. The commit's author runs it, or, when the author is not live, `of_platform` does.

## 5. Build

| Part | Owner |
|---|---|
| `of health run`, as a launchd job every minute at Standard priority, installed by `of health install`; the machine checks; the state file; the events; `of health`; the smoke check; `of rollback` | `of_platform` |
| The server's checks: `server.health` as answered, and `relay.oldest`; serving them to the machines | `of_server` |
| Studio's Health tab, and the card when his work is hit | `of_studio` |
