# Environment: Acme, the onboarding package's dry run

Status: **v0**, 2026-11-11, by `of_onboarding` (the domain: persona, fixtures, oracles). `of_lab` builds and runs it
as the lab scenario `lab-onboarding-v0` (the harness: the VM, the stub, `check.py`, the report). Every package ships
environments that show how its agents work (`docs/framework.md`, "Installing from Bench"); this is the onboarding
package's first.

Acme is fictional: a small online retailer, the example company of OF's documents. No real company's names or data.

## 1. The company

- **The head:** Alex. Works on one machine, the lab VM's user.
- **Three agents already running**, one per repository in `~/code/`, each in its own tmux session: `storefront`
  (the shop and its pricing), `checkout`, `warehouse`.
- **What OF may read:** the three repositories' git history, and the sessions on this machine (the hooks).
- **The stage:** S2, operating.

## 2. Fixtures

**Git history on `main`**, seeded before the run. Day offsets are relative to the run's start; each commit at
10:00 UTC plus its index in minutes. Authors: `Alex <alex@example.com>` for the head's commits, `Acme agent
<agent@example.com>` for the agents'.

| Repository | Commits | Day offsets | With a `Position:` trailer |
|---|---|---|---|
| `~/code/storefront` | 18 | −14, −13, −13, −12, −11, −10, −10, −9, −8, −7, −7, −6, −5, −4, −3, −3, −2, −1 | the 12 by the agent: `Position: storefront_dev` |
| `~/code/checkout` | 10 | −14, −12, −11, −9, −8, −6, −5, −4, −2, −1 | the 6 by the agent: `Position: checkout_dev` |
| `~/code/warehouse` | 6 | −13, −10, −8, −5, −3, −1 | none (the head's own commits) |

Three of the storefront commits touch `pricing/discounts.py`, and three of the checkout commits touch
`discounts.py`, on the same days (−10, −7, −3): the discount rules live in two repositories, and nobody owns them.

## 3. The head's words

The stub types these, in order, into the conductor's pane once it is started.

1. "Hi. I want to start Organic Framework at Acme. Where do I begin?"
2. When the conductor asks how the onboarding should run: "Hands-off, please." The head runs no command; the
   conductor starts `acme_onboarding` itself.
3. The story: "Last week a customer's discount code worked on the storefront but failed at checkout. I asked who
   decides the discount rules. The storefront agent said pricing does; the checkout agent said the storefront owner
   does. Nobody knew. After two days I decided it myself, and I still don't know who owns the discount rules."

## 4. Checks and their oracles

### C1. `of init` writes the structure, and starts nothing

Run: `of init acme --owner Alex --path ~/code/storefront --path ~/code/checkout --path ~/code/warehouse --yes`.
- Exit 0.
- `~/of/acme/registry/positions.yaml`:
  - `positions`, in this order: `acme_conductor`, `acme_owner`, `acme_onboarding`, `acme_business-case`,
    `acme_system-architect`, `acme_org`, `acme_flow`; then the three running agents, `storefront_dev`,
    `checkout_dev` and `warehouse_dev`, each with `owner: acme_owner` and its `holder.session`.
  - With herdr on the VM, none of the first seven has a `holder.session`. Without herdr, each of the six agents has
    one reserved, with `workplace` saying it is started by hand, and `onboarding/README.md` has its two start lines;
    `of init` prints the conductor's. `acme_owner` has `holder: {runtime: human, person: alex}`;
    `acme_conductor` has `owner_chat: true` and `owner: acme_owner`.
  - Each of the first seven has `package: {name: onboarding, version: 1, role: <id>}`, the roles being
    `conductor`, `sponsor`, `lead`, `business-case`, `system-architect`, `structure-keeper`, `flow-analyst`.
  - `people: {alex: {name: Alex, machines: [], status: active}}`.
  - `systems: {acme: {name: Acme organization, kind: creating, keeper: acme_org}}`.
  - `units: {onboarding: {name: Onboarding, owner: acme_owner, systems: [acme]}}`.
  - `works: {acme-onboarding: …}` with eight stages, all `state: open`: `charter`, `problem`, `data`, `baselines`,
    `integrate`, `decide`, `increments`, `close`.
- Files: `registry/briefs/<name>.md` for each of the seven; `onboarding/README.md`, `charter.md`, `role-design.md`,
  `recheck.md`, `champion.md`. No file contains `{org}`, `{Org}`, `{owner_id}` or `{owner_name}`.
- `~/.config/of/orgs.yaml`: the `acme` entry's first path is `~/of/acme`.
- `of registry check`: exit 0, and its one warning names `keeper 'acme_org' has no owner's ask id` (the charter's
  approval confirms it later).
- **Idempotence:** the same `of init` again leaves `registry/positions.yaml` byte for byte the same and writes no
  file.

### C2. The conductor is the first agent started

- Before: no acme position has a live holder (`of who --json`).
- `of spawn acme_conductor` exits 0, and the first acme session to start, by its first event, is acme_conductor's
  (its declared brief, `of.injected_prompt`, names `acme_conductor`).
- The conductor's first reply to the head's word 1 asks how the onboarding should run, offers hands-off first,
  and asks the head to run no command (real-agent part).
- After word 2, `acme_onboarding` is started by the conductor (its declared brief names the injector), and the
  head typed no command.

### C3. The story reaches the lead

- **Deterministic (pass/fail):** `of delegate "<the story, word 3>" --from acme_conductor` names `acme_onboarding`
  among the positions that should take it.
- **Real-agent part (optional):** after word 3, `acme_conductor` sends `acme_onboarding` one message that quotes the
  story verbatim; `acme_onboarding` is the second acme agent started.

### C4. The lead files the charter as one ask (real-agent part)

- `~/of/acme/onboarding/charter.md` has word 2 verbatim in section 1 ("How the onboarding runs") and the story,
  verbatim, in section 2.
- The ask carries the people map as a draft to confirm, from the fixtures' commit authors; no ask or message to
  the head asks them to write a list or names a method term ("champion", "sponsor", "charter").
- Exactly one open ask from `acme_onboarding`, linked to the work `acme-onboarding` (`of asks --all --json`).
- No other acme position has filed an ask to the head.

### C5. The before measure runs, and names its gaps

- `of import git --org acme`: 34 `of.work_record` events with `source: git` (18 + 10 + 6); 18 of them carry a
  position from their `Position:` line (12 `storefront_dev`, 6 `checkout_dev`).
- `of model measures --org acme --from <run start − 15 days> --to <now> --json`: exit 0, and under `flow`
  (method `fl-v0`, `docs/studio.md` §3.4b):
  - `commits_from_git` is 34: the commits are a count beside the series, never pull requests;
  - `merged_prs_per_day`, `pr_lead_time_h` and `merged_prs_per_prompt` have `value: null` and a `gap` that names
    `no of.work_record from GitHub (of import github)`: there is no GitHub in this environment;
  - every other series has exactly one of `value` and `gap`, and a gap names its missing input (for example
    `no of.turn_end` when the window recorded no turn), never an estimate;
  - `never_collected` lists three measures: process time, %C&A, activity ratio and rolled %C&A; work outside
    GitHub and git; the share of people who record their sessions.

### C6. The head's open items from the onboarding, per day

- Counted from `of asks --all --json`: the open asks and tasks filed by positions of the unit `onboarding`, per
  UTC day.
- Expected: 0 after C1 to C3; 1 after C4 (the charter's ask).

### C7. A message reaches an agent in a screen session (no herdr, no tmux)

- On a machine with neither herdr nor tmux, `of init acme --owner Alex --yes` writes, for every agent but the
  conductor, a second line that starts it with `screen -U -dmS <its name>`.
- Real-agent part: the conductor runs the lead's two lines itself; `of who` shows `acme_onboarding` as
  `screen:<pid>.acme_onboarding:0`; the conductor's first message reaches the lead with nobody typing it (the relay
  delivers into screen windows, organic-framework 9012a74), and the lead answers it within its next turn.

### C8. The remote surface (`of init --surface remote`)

- Deterministic: with tmux on the PATH, `of init acme --owner Alex --surface remote --yes` writes
  `surface: remote` to `~/.config/of/owner.yaml`; every agent's second line in `onboarding/README.md` starts with
  `tmux new-session -d -s <its name>`, and the conductor's carries `--remote-control acme_conductor`. Without tmux,
  `of init` stops at step 1 and names tmux.
- Real-agent part: the head's words reach the conductor only through its session (no pane is typed into by the
  stub); every card is presented in the chat and answered there, relayed verbatim to its asker
  (`docs/remote.md` §2).
