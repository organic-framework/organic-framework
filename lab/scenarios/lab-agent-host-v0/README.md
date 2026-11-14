# lab-agent-host-v0

The lab rehearsal (**B5**) of the agent-host move: it proves **B1** (a session carried to another machine and into
another OS user's home resumes there, both directions) and **B3** (another machine's event and record delta merges
by id) in the lab, before the host's **S2** stage. Hand-over `7ebc98c6` from `of_agent-server` (verifier), basis the
founder's ask `f3e55128`. The move's plan is organic-framework-build `reports/agent-server-migration-plan-2026-11-11.md`
§3 (B1, B3) and §9 (one OS user per organization). Findings go to `of_capacity` (B1) and `of_server` (B3), not through
`of_architect`. This scenario is the harness and the proof, not the features.

B1 is `of capacity carry` (`bin/of_carry.py`, `of_capacity`, 2026-11-13; PR through `of_release`). B3 is `of_server`'s
delta merge by id (pending). On the real host each organization runs as its own OS user, so a session changes both
machine and home (for example `/Users/alex` to `/Users/of`); the carry rewrites the home, Claude Code's folder keys
and the agent tmp root's uid, one pass, and never rewrites a record's `message` (its thinking blocks are signed).

## The setup (two lab machines, non-production, synthetic)

Two lab machines stand in for the laptop and the host, each an organization's OS user with its own home and uid:

- **Two Lima VMs** when the Mac's load allows a second (the preflight ceiling gates a new VM); otherwise **two OS
  users on one running VM** (`alex` and `of`), which still differ in home and uid, so the path rewrite is exercised in
  full. The copy leg (rsync over SSH) runs between the two VMs, or over loopback SSH between the two users.
- A non-production session is created under user `alex` (a scripted Claude Code session, as in `lab-quickstart-v0`,
  with the folder-trust and bypass stub so `of capacity carry restore` can resume it), on a signed-in account (stub),
  in a trusted folder under `alex`'s home.
- The target user `of` has a registry that lists the carried position in its organization, so the restore (which needs
  `--org` and restores only positions the target registry has in that organization) has an entry to resume.
- No real organization data or credentials: the session, its account and its folders are synthetic.

**Lab OS and fidelity.** The lab machines are Linux VMs (Lima), and the move's host is macOS; the founder's Mac
cannot host a macOS VM (`AGENTS.md`: nothing heavy on it). So this rehearsal proves the OS-agnostic parts — B1's carry
mechanism (the rewrite across homes and uids, and the resume selection, both directions) and B3's delta merge — while
three things are **out of lab scope** and are validated at the host's S2 instead:

- the host setup script `deploy/host/setup.py` (`of_server`, B2: `dscl`, `launchctl`/`/Library/LaunchDaemons`,
  Homebrew, `/Users` homes), and the `/private/tmp/claude-<uid>` scratch root and `/Users` paths that `of_carry` and
  `of_capacity.agent_tmp_root` assume (on Linux the home-and-key rewrite still runs; a scratch folder is carried
  through the home rule, a tmp root under the home, since the macOS `/private/tmp` uid substring never appears);
- **the actual Claude Code boot on resume** and the `of.capacity_carry_restored` / `of.holder_changed` events it
  emits. The boot needs a real signed-in account and the API, which the lab does not have and must not use. So the
  lab drives the resume up to the launch boundary: `of_carry.restore` reaches the boot through
  `of_capacity.reboot_restore`, and `check.py` injects a `restore_fn` in its place, asserting which sessions are
  selected and the rewritten, pane-cleared local plan handed to it. The boot and its events are validated at S2.

The findings state what the Linux rehearsal covers and what only the host can, and carry two defects the rehearsal
surfaced in `of_carry` (to `of_capacity`): the copy wrapper `taskpolicy -b` is macOS-only, and the content of
non-transcript session state (file-history, session-env, todos, tasks, scratch) is not rewritten on a carry.

## Checks

B1's "done when" (plan §3) is that a non-production session resumes on the other machine, under its organization's
user, with its context, memory, account, model and mode, in both directions. `of_capacity` has already checked the
rewrite and install into a scratch home with no resume (docs/capacity.md, "Carrying sessions to another machine",
2026-11-13); **this rehearsal's new leg is the resume, both directions.**

| # | Leg | What the harness does | How it is checked |
|---|-----|-----------------------|-------------------|
| A1 | prepare | `of capacity carry prepare --only <org> --bundle DIR` as `alex` | `plan.json` names `alex`'s machine, home, uid and the session's account; `files.txt` lists the session's state, memory and scratch; a scratch or task file over 64 MB is left behind and named in the plan. |
| A2 | copy | `of capacity carry copy --bundle DIR --to of@host:DIR --key FILE` | `copy_commands` builds rsync over SSH with that key only (`SSH_AUTH_SOCK` empty, `IdentityAgent=none`, `--chmod=go-rwx`, bandwidth limit), and the bundle reaches the target over loopback SSH; the owner's SSH agent is never used. The macOS wrapper `taskpolicy -b` the command prepends does not exist on Linux, so the lab asserts the command's contract and runs the equivalent rsync without it (a finding for `of_capacity`). |
| A3 | restore rewrite | `of capacity carry restore --bundle DIR --org <org> --home <of-home>` as `of` | the restore refuses a bundle for another organization than `--org`; it restores only the positions the target registry has in that organization; a project's folder memory is not carried (the position's own memory directory is). Every path moves from `alex`'s home and uid to `of`'s (home, folder keys `-Users-alex-…` to `-Users-of-…`, tmp root uid), one pass; each record's `message` stays byte for byte; files land only under `of`'s home and tmp root; a different file already there is kept aside in `kept/`. |
| A3b | org guard | restore the bundle with the wrong `--org`, and restore when the target registry lacks the carried position | the mismatched `--org` is refused; a position the target registry does not have in the organization is not restored. |
| A4 | **resume (forward)** | the restore selects the session for resume as `of` | `restore` selects the carried session (account matched), and writes `plan.local.json` with the entry's `config_dir`, `cwd` and `memory_dir` rewritten to `of`'s home and its `pane` cleared (never another machine's pane id). The actual boot and the `of.capacity_carry_restored` / `of.holder_changed` events are the host's S2 part (see "Lab OS and fidelity"); the lab injects a `restore_fn` and asserts the selection and this local plan. |
| A5 | **resume (reverse)** | carry the session back to `alex` and restore | the rollback direction selects and rewrites under `alex` the same way; the copy that stayed running on the source is never stopped by the carry. |
| A6 | account guard | restore a session whose target config dir is signed in to another account | the session is **not** resumed and the restore names it (the owner signs in or chooses the account first). |
| B3-1 | delta merge | merge the source's post-cutover events and records into the target store and ClickHouse by id | a re-run adds nothing (idempotent on the UUIDv7 id / `ReplacingMergeTree` record id). |
| B3-2 | delta projections | after the merge | the delta's asks and hand-overs appear in the target's projections. |

A sign-in note: until the target config dir is signed in to the account the session ran on, restore does not resume
it (A6 asserts the guard). A folder-trust note, for the S2 boot: a rewritten folder must be trusted in the target
config dir (the owner's call); `check.py` writes the trust marker as the quickstart rehearsal does, for when the boot
runs with a real account.

## Run (on the lab VM)

```
sudo bash setup.sh       # provision users alex (1001) and of (1002), the shared framework tree, the copy keys
python3 check.py         # forward (alex->of) and reverse (of->alex); A1..A6 + A3b; exit 1 on any failure
```

`setup.sh` stands up the OS-level pieces (two organization users, a world-readable framework tree, an SSH key per
user for the copy leg); `check.py` builds the synthetic session, then prepares, copies over loopback SSH, restores
with the rewrite, and drives the guards and the resume selection, both directions. A `git archive HEAD` of the
framework placed at `/tmp/of-head.tar` gives `setup.sh` a fresh tree. Heavy work stays in the VM (`AGENTS.md`:
nothing heavy on the Mac). The run is recorded in organic-framework-build `reports/framework/`.

## Status

- **Harness built and green (2026-11-15, VM `of-lab-e2e1`):** `setup.sh` + `check.py` pass A1, A2, A3, A3b, A4, A5,
  A6 forward (alex→of) and reverse (of→alex), exit 0. The resume boot and its events are the S2 part (see "Lab OS and
  fidelity"); B3-1/B3-2 wait on `of_server`. Run report in organic-framework-build `reports/framework/`.
- **B1 ready:** `of capacity carry prepare|copy|restore` is in (`of_capacity`, 2026-11-13; `restore` gained a required
  `--org <organization>` and restores only positions the target registry has in that organization, folder memory not
  carried, `of_capacity` 2026-11-14). `of_capacity` verified the rewrite and install into a scratch home (539 of 540
  conversations byte for byte, 226 working folders moved, no resume); this rehearsal adds the cross-user rewrite with
  two real homes and uids, the guards, and the resume selection in both directions.
- **Two `of_carry` findings, fixed and re-verified (2026-11-15):** `of_capacity` fixed both in 4a295eb — the copy
  wrapper is now `background()` (per platform: `taskpolicy -b`, else `nice`/`ionice`), and `install` rewrites the
  content of `session-env`, `tasks` and `todos` to the new home; `file-history` stays byte for byte on purpose (its
  files are edited-file snapshots `/rewind` writes back, keyed by the already-rewritten transcript records), and
  scratch travels as the session's own work. A rerun on 4a295eb is green, with A2 taking the Linux wrapper and a new
  `A3-state-text` check asserting the three state folders follow the home while `file-history` is retained.
- **B3 pending:** `of_server`'s event and record delta merge by id; B3-1 and B3-2 wait for it. Asked `of_server` for
  the ref and how it is invoked.
- **macOS host script (B2) is out of lab scope:** `deploy/host/setup.py` (`of_server`, 2026-11-14) is macOS-only and
  gets its first real run at the host's S0/S2, not here (see "Lab OS and fidelity"). Offered `of_server` a static
  review and a `plan` run.
