# OF's own health: checks, one page, one card

Status: **design**, 2026-11-14, `of_architect`; **built in part** the same day: each machine's checks, the state file, the events, `of health`, the smoke check and `of rollback` (`of_platform`, `bin/of_health.py`), and of-server's own checks (`of_server`). Studio's Health tab and the person's card are built (`of_studio`, `bin/of_studio_health.py`, docs/studio.md §5.2a); the card starts once `of health run` calls it.

**Basis.**
- The founder, 2026-11-13 20:12Z (prompt `01a1d68a-5ab4`, in `of_conductor`'s session): "Let's look once more at the top-level architecture of Organic Framework's tools: where else something may fire, because we keep having an unstable problem."
- The OF team's review that followed (private, 2026-11-14, S4 and R9) found that the team learns of failures from his screenshots.
- The founder, 2026-11-13 20:32Z, on that review's question whether his machine should run only released versions (prompt `01a1d69c-a002`): "No, we test right on my machine." His machine stays on the working copy's `main`, so the checks below also catch a broken commit quickly (R3).

## 1. What it is

A small job checks OF itself every minute on each machine, and of-server checks itself. The result is:
- one current state per check;
- an event when a check changes state;
- one page that shows it all;
- a card to the owner only when his own work is hit and the check's owner cannot be told.

The checks never run on the listener's loop, because the listener is one of the things they watch.

## 2. The checks

| Check | What it measures | Degraded | Failed | Runs in | Owner |
|---|---|---|---|---|---|
| `server.health` | of-server's answer time on `/v1/health` | over 2 s | over 20 s, or no answer | `of health run` | `of_server` |
| `relay.oldest` | how long the oldest message has been queued for a live, idle receiver, from the first check that saw it so (a wait on a busy agent counts nothing; a message the person holds, with a dialog open or text in the input box, does not count) | over 10 min | over 60 min | of-server | `of_server` |
| `listener.lag.<processor>` | events behind, and seconds since the processor's last tick | over 2 min | over 10 min | `of health run` | `of_platform` |
| `view.age.<view>` | the age of each view a screen or the CLI reads | over the view's declared lag | over 5 times it | `of health run` | `of_platform`, with the view's owner |
| `hooks.errors` | hook calls that failed or spooled, out of all calls, in the last 10 min | over 1% | over 10% | `of health run` | `of_platform` |
| `hooks.latency` | the hooks' p99 time per event kind, in the last 10 min | over 1 s | over 5 s | `of health run` | `of_platform` |
| `machine.memory` | swap used, out of swap | over 80% | over 95% | `of health run` | `of_platform` |
| `machine.disk` | free disk | under 25 GiB | under 20 GiB (the pulse's card) | `of health run` | `of_platform` |
| `machine.home_git` | a `.git` in the home folder itself | – | present (every git call under home outside a repository of its own walks the whole home folder; 2026-11-15) | `of health run` | `of_platform` |
| `commit.smoke` | the working copy's newest commit passes the smoke check (§4) | — | it does not | the commit's hook, and `of health run` for the newest commit | `of_platform` |

A limit here is the starting value. Its owner tunes it in this table with the evidence, never in the code alone.

## 3. Where the result goes

- **The state.** `of health run` writes the current state of every check to `~/.local/state/of/health.json`: the state (`ok`, `degraded` or `failed`), the value, the limit, and since when. of-server keeps its own checks and serves them to its machines.
- **The event.** One `of.health_changed` event `{check, state, value, limit, since, owner}` is written only when a check changes state. The log does not grow by one event a minute. Each change is recorded once: `server.health` by each machine's `of health run`, which measures the server's answer from there and so also sees a server that does not answer, and `relay.oldest` by of-server; the server keeps and serves its own `server.health` reading without recording its changes (2026-11-17, `of_architect`'s hand-over `09042907`: both sides had recorded them).
- **The command.** `of health` prints the table.
- **The page.** Studio's Health tab shows the table, with each failed check's owner and the time since it failed (`of_studio`).
- **The owner of a check** gets one `--fyi` once it has failed for 5 minutes, and one when that failure is over; a check that fails and recovers within 5 minutes tells nobody (2026-11-14: swap around its limit failed and recovered every few minutes, one `--fyi` each time). What one run has to tell an owner, notes and daily reminders alike, goes as one message listing every check (2026-11-17: a stopped listener failed 22 of one owner's checks, and the owner got 22 notes).
- **A failure that lasts is told again** (2026-11-17, `of_architect`, on `of_platform`'s proposal, message `01a1e82f-f81e`). While a check stays failed, its owner is told once a day. The first reminder, a day after the failure began, is typed into the owner's pane, not `--fyi`. One `--fyi` is easy to miss among health notes: on 2026-11-14 a check failed, its owner missed the one `--fyi`, and the check stayed failed for three days until a hand-over check found it on the Health tab. It was a stale cursor, not a real lag. The same holds for of-server's own checks: of-server tells the owner of `relay.oldest` in the same way, through its own relay, from `of_server` (`bin/of_server_health.py`; hand-over `09042907`: before it, a failing `relay.oldest` was never told, since `of health run` leaves the server's checks to the server). Two limits (`of_review` on organic-framework PR 263): when the relay itself is what fails, the typed reminder waits in the queue it reports on, while the `--fyi` still shows in the owner's `of status` count and the person's card below does not go through the relay; and the server tells only in the OF team's organization (`OWNER_ORG`), as `of health run` does, so a server that does not serve it tells nobody.
- **The person.** These checks hit the organization's owner's own work, and the Health tab marks them:
  - `server.health`;
  - `relay.oldest`;
  - the age of the view his questions screen reads;
  - `hooks.errors`;
  - `commit.smoke`.

  A card goes to him only for `server.health` and `relay.oldest`, after 5 minutes failed. Their owner hears of a failure through of-server's relay, the thing that fails, so the card is the one path left (the limits above). It names the check, its owner and what he sees meanwhile. It closes itself when the check is back. Every other check, his work's included, goes to its owner only: an operations matter with an owner, and no decision only he can make (`of_conductor`, 2026-11-17, message `01a1ea82-ce80`; 14 such cards reached him from 2026-11-15 to 2026-11-17, 11 of them on the 17th, none for those two).

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
| The server's checks: `server.health` as answered, and `relay.oldest`; serving them to the machines; recording `relay.oldest`'s changes and telling its owner | `of_server` |
| Studio's Health tab, and the card when his work is hit | `of_studio` |
