# Harness accounts as an OF feature, and the capacity package

Status: **plan**, 2026-11-11, by `of_capacity`. Done: A1–A3 (`bin/of_accounts.py`, `tests/test_accounts.py`). Next: A4 with `of_herdr`.

Basis: the founder, through `of_conductor` (msg 01a1cc5f), about 20:48Z, translated:

> "We also need an agent to move into Bench: of_capacity, with all its services, launchd and so on, with its position, so that it runs at our colleagues who use Organic Framework too. And it is a must: I have now separated the Claude accounts by hand through the zsh config. Properly it should be done more systematically, so that our colleagues, the users, can use it normally, without customizing the zsh config."

## 1. Today

- **Accounts are found by their folder names.** OF takes `~/.claude` and every `~/.claude-<name>` folder (`of_capacity.config_dirs`). `of spawn`, the moves, `of resume-command`, the reboot restore and the balancer choose among them and run `claude` with `CLAUDE_CONFIG_DIR` set (left unset for `~/.claude`).
- **Done by hand on the founder's Mac:**
  - he created each folder and logged in there;
  - one shell alias per account opens Claude Code on it;
  - his shell config sources `bin/of-shell.zsh`. In a herdr pane, that file turns herdr's bare `claude --resume <id>` after a herdr restart into the `of resume-command` line: the session's account, model, effort and permission mode. Without it, 38 sessions came back on one spent account on 2026-11-10.
- **Services.**
  - The probe and the balancer run inside the listener's capacity processor and inside the pulse. `of init` installs both as one service: launchd on macOS, systemd on Linux, with `OF_AUTOMATION=1`.
  - Settings live in `~/.config/of/capacity.yaml`.
  - Some code is still tied to one installation. The stall invariant sends its work to the fixed address `organic-framework-build:of_capacity`, and the events name `of_capacity` as their position.

## 2. Part A: accounts without shell customization

1. **`~/.config/of/accounts.yaml`** lists the machine's accounts: name, config folder and an optional label.
   - Every reader of accounts goes through `config_dirs()`, and `config_dirs()` reads this file.
   - With no file, discovery works as today, so an existing machine keeps working unchanged.
   - `of account adopt` writes the file from what it finds.
2. **`of account add <name>`** creates the folder (mode 700) and adds the entry. It then files one owner task: in a plain terminal, open Claude Code on that account, log in, and accept the Bypass Permissions warning. Only the owner does this.
   - **`of account list`** shows each account's email, login state, week and 5-hour use, and the positions on it. The login check is the probe.
   - **`of account remove <name>`** takes the account out of spawn and the balancer and never deletes the folder.
3. **`of claude [--account NAME] [claude arguments]`** is the launcher that replaces the aliases.
   - It runs Claude Code with that account's folder.
   - Without `--account`, it takes the account with the most headroom among those that trust the current folder, the same choice `of spawn` makes.
   - `of claude --resume <id>` runs the `of resume-command` line.
4. **herdr's resume after a herdr restart without the zsh function** (with `of_herdr`, who chooses one):
   - (a) herdr restores an agent through a command OF configures, if its restore or `herdr agent start` accepts one;
   - (b) OF's herdr plugin resumes OF's sessions from its startup hook before herdr types the bare line;
   - (c) herdr panes get OF's launcher folder first on their PATH through herdr's own settings, with no user shell file involved.

   `bin/of-shell.zsh` stays as the fallback until the chosen way is proven. Then it is removed, and `of init` stops mentioning it.
5. **The `harness` family** (docs/plugins.md §3), with `of_platform`: `accounts()`, start and resume take an account. Claude Code's implementation is items 1–4; another harness (Codex) fills the same methods.

## 3. Part B: the capacity package in Bench

1. **`bench/capacity/`**, kept like `bench/onboarding/`:
   - `package.yaml`: one role, `{org}_capacity`, held by an agent, with its sources;
   - the brief and the mandate, generalized from `of_capacity`'s own, with nothing about one person's accounts;
   - the registry entry.

   `of init` installs it when the organization wants it, and an existing organization can add it later.
2. **Services: no new unit.** On every machine with two or more accounts, the listener's capacity processor and the pulse run the probe and the balancer.
   - `of init` writes the defaults to `capacity.yaml`:
     - rescue on, so a session stopped on its limit moves to an account with room;
     - fill and relief off until the owner says yes on one card.
   - The listener's installer already provides launchd or systemd with `OF_AUTOMATION=1`.
3. **Generalize the code.**
   - The invariant's work goes to the capacity position that the machine's registries name, or to the owner's conductor when there is none.
   - The events carry that position's name.
   - Nothing in the code names one person's accounts or organizations.
4. **Machine and organization.** Accounts belong to a person's machine, and one machine may serve several organizations; the founder's serves two.
   - The balancer is one per machine and moves only the positions this machine holds (docs/server-several-machines.md §4.3).
   - The services need no position.
   - Proposal: the position is optional, one per person, registered in that person's home organization. This is the open decision in §5.
5. **The credential broker** (setup tokens, the vault) stays out of the package until `secret_stores` lands (docs/plugins.md §5, step 2). Then it moves onto that family.
6. **The `of init` step**, agreed with `of_onboarding`: after the listener is installed, `of init` sees N accounts and asks the head on one card whether to balance between them.

## 4. Order

1. **A1–A3** with tests. Discovery stays as the fallback, so nothing changes on a machine that has no `accounts.yaml`.
2. **A4** with `of_herdr`.
3. **B3**, then **B1**, **B2** and **B6** with `of_onboarding`. `of_lab` gives a dry run: a fresh machine with two accounts, no shell file.
4. Retire `bin/of-shell.zsh`. The founder's aliases are his own file: he keeps them or drops them.

Each step is committed on `main` and named to `of_release` with a reviewer, so it ships as a pull request (release README §7). None of it moves a live session.

## 5. Open

- **Where the optional position lives** (B4): one per person, in their home organization (proposed), or one per organization.
- **Which of A4's three ways** (`of_herdr`).
