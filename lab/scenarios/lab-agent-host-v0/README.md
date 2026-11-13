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
mechanism (the rewrite across homes and uids, and the resume, both directions) and B3's delta merge — while two
macOS-specific parts are **out of lab scope** and are validated at the host's S2 instead: the host setup script
`deploy/host/setup.py` (`of_server`, B2: `dscl`, `launchctl`/`/Library/LaunchDaemons`, Homebrew, `/Users` homes), and
the `/private/tmp/claude-<uid>` scratch root and `/Users` paths that `of_carry` and `of_capacity.agent_tmp_root`
assume (on Linux the home-and-key rewrite and the resume still run; the macOS scratch-root leg does not). The findings
state what the Linux rehearsal covers and what only the host can.

## Checks

B1's "done when" (plan §3) is that a non-production session resumes on the other machine, under its organization's
user, with its context, memory, account, model and mode, in both directions. `of_capacity` has already checked the
rewrite and install into a scratch home with no resume (docs/capacity.md, "Carrying sessions to another machine",
2026-11-13); **this rehearsal's new leg is the resume, both directions.**

| # | Leg | What the harness does | How it is checked |
|---|-----|-----------------------|-------------------|
| A1 | prepare | `of capacity carry prepare --only <org> --bundle DIR` as `alex` | `plan.json` names `alex`'s machine, home, uid and the session's account; `files.txt` lists the session's state, memory and scratch; a scratch or task file over 64 MB is left behind and named in the plan. |
| A2 | copy | `of capacity carry copy --bundle DIR --to of@host:DIR --key FILE` | the bundle reaches the target by rsync over SSH with that key only (`SSH_AUTH_SOCK` empty, `IdentityAgent=none`, background priority, bandwidth limit); the owner's SSH agent is never used; `--from` pulls the same way. |
| A3 | restore rewrite | `of capacity carry restore --bundle DIR --org <org> --home <of-home>` as `of` | the restore refuses a bundle for another organization than `--org`; it restores only the positions the target registry has in that organization; a project's folder memory is not carried (the position's own memory directory is). Every path moves from `alex`'s home and uid to `of`'s (home, folder keys `-Users-alex-…` to `-Users-of-…`, tmp root uid), one pass; each record's `message` stays byte for byte; files land only under `of`'s home and tmp root; a different file already there is kept aside in `kept/`. |
| A3b | org guard | restore the bundle with the wrong `--org`, and restore when the target registry lacks the carried position | the mismatched `--org` is refused; a position the target registry does not have in the organization is not restored. |
| A4 | **resume (forward)** | the restore resumes the session as `of` | the session resumes under `of`'s user with its context, memory, account, model and mode, in a pane placed by its workspace and tab (never by another machine's pane id); `of.capacity_carry_restored` and `of.holder_changed` (`evidence: of capacity carry restore`) are written. |
| A5 | **resume (reverse)** | carry the session back to `alex` and resume | the rollback direction resumes under `alex` the same way; the copy that stayed running on the source is never stopped by the carry. |
| A6 | account guard | restore a session whose target config dir is signed in to another account | the session is **not** resumed and the restore names it (the owner signs in or chooses the account first). |
| B3-1 | delta merge | merge the source's post-cutover events and records into the target store and ClickHouse by id | a re-run adds nothing (idempotent on the UUIDv7 id / `ReplacingMergeTree` record id). |
| B3-2 | delta projections | after the merge | the delta's asks and hand-overs appear in the target's projections. |

A folder-trust note: a rewritten folder must be trusted in the target config dir (the owner's call); the stub grants
the trust in the lab, as the quickstart rehearsal does. A sign-in note: until the target config dir is signed in to
the account the session ran on, restore does not resume it (A6 asserts the guard).

## Run (on the lab VM(s))

```
# as each organization user inside the VM:
bash setup.sh            # create the alex and of users (or two VMs), a scripted session under alex, stubs
python3 check.py         # A1..A6 now; B3-1..B3-2 once of_server's delta merge lands; exit 1 on any fail
```

Heavy work stays in the VM (`AGENTS.md`: nothing heavy on the Mac). The run is recorded in
organic-framework-build `reports/framework/`.

## Status

- Harness: `setup.sh` and `check.py` to build; this README is the spec.
- **B1 ready:** `of capacity carry prepare|copy|restore` is in (`of_capacity`, 2026-11-13; `restore` gained a required
  `--org <organization>` and restores only positions the target registry has in that organization, folder memory not
  carried, `of_capacity` 2026-11-14). `of_capacity` verified
  the rewrite and install into a scratch home (539 of 540 conversations byte for byte, 226 working folders moved, no
  resume). The resume across machines in both directions (A4, A5) and the account guard (A6) are this rehearsal's
  part.
- **B3 pending:** `of_server`'s event and record delta merge by id; B3-1 and B3-2 wait for it. Asked `of_server` for
  the ref and how it is invoked.
- Blocked on a free VM pair only by the Mac's load (over the new-VM ceiling); B5 runs on the up VM with two
  organization users meanwhile, and a second VM when load permits.
- **macOS host script (B2) is out of lab scope:** `deploy/host/setup.py` (`of_server`, 2026-11-14) is macOS-only and
  gets its first real run at the host's S0/S2, not here (see "Lab OS and fidelity"). Offered `of_server` a static
  review and a `plan` run.
