# lab-onboarding-v0

The onboarding package's dry run on the fictional company **Acme** (`bench/onboarding/environments/acme.md`). Every
package ships environments that show how its agents work; this is the onboarding package's first. Domain, persona,
git fixtures and the oracle of each check: `of_onboarding` (acme.md). The harness — the VM, the stub, `setup.sh`,
`check.py`, the report: `of_lab`.

## Checks (acme.md §4)

| # | Check | Kind | Status |
|---|---|---|---|
| C1 | `of init acme --owner Alex` writes the structure (conductor first, the onboarding unit and its eight-stage run) and starts nothing | deterministic | **passing** (19/19) |
| C2 | `of spawn acme_conductor` is the first agent started | deterministic | **passing** (4/4) |
| C3 | the story reaches the lead (`of delegate` names `acme_onboarding`) | deterministic + real-agent | C3 deterministic to build; real part of_onboarding's |
| C4 | the lead files the charter as one ask | real-agent | optional, of_onboarding's |
| C5 | the before measure runs and names its gaps (`of import git`, `of model measures --org acme`) | deterministic | to build (needs the git-history fixtures of §2) |
| C6 | the head's open onboarding items, per day | deterministic | to build |

C1 and C2 pass on a clean lab VM (2026-11-11, 23/23 assertions), checking the install path before a real adopter tries
it.

## Run (on a clean lab VM)

```
OFLAB_MAX_VMS=1 lab/bin/oflab up onboard1          # the VM packs bench/ (oflab bc50ecf)
#   as the lab user inside the VM:
bash setup.sh                                       # stub claude, ~/code repos + 3 running agents, herdr, trust
python3 check.py --only c1,c2                        # one line per assertion; exit 1 on any fail
```

## Harness notes (for the real install)

- **herdr on the VM.** `of init` reads the live agents from herdr; with herdr present none of the seven onboarding
  positions reserves a holder (the head starts them), and the three repo agents are detected as `<repo>_dev`. herdr is
  started in a detached tmux PTY; its session state is reset first so no earlier pane restores (never `pkill herdr`).
- **`of spawn` needs `--config-dir`** in the VM, because there is no `of_capacity` account probe; a real install has a
  logged-in account.
- **Folder trust is the head's act.** `of spawn` stops and files a task if the account does not trust `~/of/acme`
  (as designed); the setup pre-trusts it to stand in for the head having trusted the folder.
- **Real-agent parts** (C2's conductor reply, C3, C4) run a real Claude session in a lab pane; they are `of_onboarding`'s
  to drive once the deterministic harness is in.
