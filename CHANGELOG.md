<!-- site:skip -->
<a href="README.md"><picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/readme/strip-changelog-dark.svg">
  <img alt="Organic Framework: Changelog" src=".github/readme/strip-changelog-light.svg" width="100%">
</picture></a>
<!-- /site:skip -->

# Changelog

Every notable change to Organic Framework, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). What each part can do today, and how far it has come, is in [`FEATURES.md`](FEATURES.md).

## [Unreleased]

## [0.2.0] - YYYY-MM-DD

OF on herdr as a plugin, one screen for the questions that wait on you, and an organization run from the conductor's chat alone.

### Added

**On herdr**

- **OF's herdr plugin** (`herdr-plugin/`, for herdr 0.9.5). Agents are named by their positions as soon as herdr sees them and after every herdr restart, with no `of apply`. The sidebar's Agents view lists the agents with a question for you first, then those that need attention, and keeps every agent listed. `prefix+a` opens the questions screen as a popup, and a red counter on each workspace shows how many questions wait there. herdr resumes OF's sessions on their own Claude account, model and permission mode (`of claude --resume`). `of init` links the plugin and adds the key and the counter after asking; `herdr plugin unlink organic-framework` takes the plugin out.
- **The questions screen** (`of asks --watch`): the queue and the open card side by side, in streams (holds work; money and production; for your hands; everything else), with a search and keys that wrap around. It opens on the most urgent stream that holds anything.
- **The questions panel** (`of asks --panel`): a small panel that stays in sight, with each stream's count, the longest wait, the first cards and a button that opens the questions screen. The more room it has, the more it shows.

**From the conductor's chat alone** ([`docs/remote.md`](docs/remote.md))

- `of init --surface remote`, for a person who reaches OF only through the chat with their conductor (Remote Control, the desktop or the mobile app): the conductor starts with Remote Control on, and every agent runs in tmux. The relay delivers messages into tmux and `screen` as it does into herdr.
- Questions answered in the chat. `of asks --chat` gives the conductor the person's queue to tell them, and `of asks answer --prompt` records an answer only on the person's own recorded words (`of prompts --mine`). A card about money, security, production or anything irreversible stays at the machine, and approving a diff from the chat is held back until a person's words are recorded where agents cannot write ([`docs/remote.md`](docs/remote.md) §5). A task says where it can be done (`of ask --where phone|browser|machine`), and the pulse tells the person's conductor when something waits on them.
- Provisional positions: work raised during the onboarding starts on the person's yes in their conductor's chat, as a project with no powers over money, secrets, production or delegation, until the structure step folds it in.

**Setting up**

- `INSTALL.md`: a guide for the coding agent that installs OF for a person. It gives the questions to ask, each step's command and check, the steps only the person can take, and a recommended setup on a machine that stays on.
- New steps in `of init`:
  - `--language`: the language agents use with the head, kept in `~/.config/of/owner.yaml`;
  - a default model and effort per organization, Opus at `xhigh` (`--model`, `--effort`), which `of spawn` (new `--effort`) and `of resume-command` use;
  - the relay and the listener installed as user services and checked running;
  - OF's herdr plugin, its questions key and its counter, after asking;
  - with two or more Claude accounts, `--capacity yes|no`: agents on a spent account move to one with room, and the person's capacity keeper `<org>_capacity` keeps watch;
  - the plugins the machine has for OF, and `--plugin family=name`;
  - `--refresh`: a new organization's package briefs, `AGENTS.md` and `onboarding/README.md` brought up to date after an update; a file the organization edited is kept, with the new version beside it as `<file>.package-new`. A re-run keeps the organization's owner.
- `of account` and `of claude`: the machine's Claude accounts as OF's own list (`~/.config/of/accounts.yaml`), with no shell aliases ([`docs/accounts.md`](docs/accounts.md)). `bench/capacity`: the capacity package, one keeper per person.

**Plugins**

- One plugin mechanism for the client and of-server ([`docs/plugins.md`](docs/plugins.md)): families with built-in, shipped or installed implementations. The first family is `secret_stores`, with 1Password, the macOS keychain and an env file for the lab. Where an organization keeps its secrets is its own choice, made when the first secret is needed.

**Running an organization**

- One locale layer: every string OF shows a person is English in the code and comes in their language from a catalogue, `bin/locale/<lang>.json`; a missing entry shows the English. Russian is the first catalogue.
- Hand-overs carry a basis: `of handover propose --basis` takes an ask id, a quoted source, evidence or `inference`, and a title or done-when that states an obligation is refused without one. `of handover note` adds a note to an open hand-over without changing its state.
- `of diff` move_position: a position moves between two units delegated alike, and each delegation's list follows it.
- `of model usage`: a delegated unit's month per position, day and model.
- `of grant rekey`: the grant-signing key moves into the owner's 1Password SSH agent, and grants are signed through it from then on.
- The diagnosis rule `unsourced-requirement`: a claim that drives work lost its basis on the way.

### Changed

- No agent asks the organization's head to run a command. The head may talk to agents through Remote Control, the desktop or the mobile app, where a command typed into the chat does not run. The conductor asks in words and starts the onboarding lead itself; what only the head can do (a login, trusting the folder) is a step in words with its place. Without herdr, every agent but the conductor starts in a detached session, tmux or else `screen`. `INSTALL.md` tells the installing agent the same.
- The conductor's and the onboarding lead's briefs: do everything they can themselves, and say at each step in plain words what they do and why.
- The relay types what is queued for an agent as one prompt as soon as the agent is idle; the 15-minute window per receiver is gone.
- The README follows the OF design: the photograph and the type of the design, light and dark; "install it with your agent" comes first.

### Fixed

- On a new machine the relay never started, so agents' messages did not arrive. `of init` now installs it, `of status` and `of send` warn when no relay runs on the machine, and `of relay on` installs the service when it is missing.
- The relay could type one message more than once while a hook record of its delivery was still in the spool.
- `of import git` and the flow measure read each repository's default branch, not `main`.
- Capacity: a probe that got no reading (the network down) no longer moves sessions ahead of a stop, and `of capacity balance` makes no move while the API does not answer.
- The quickstart's PyYAML step: Homebrew's Python and recent Linux Pythons refuse a plain `pip install --user`.

### Security

- Fixes from the review of 2026-11-11 of the commits pushed after 0.1.0, each through a reviewed pull request:
  - a diff is approved only in the owner's popup or terminal, never by an answer recorded through the conductor's chat (finding 1, high);
  - a card's own words hold it at the machine, whatever tags were declared (finding 2, high);
  - a remote approval passes the same checks as an answer from the chat, and a digest counts only where it is named as one (findings 3 and 7);
  - a remote person is told of an item only once its delivery is confirmed, and no queue reaches another organization's conductor (findings 4 and 5);
  - delivery into `screen` never pastes the previous message (finding 8), and the chat reads a merged card as the check does (finding 9);
  - grant signing reaches the owner's SSH agent only from the owner's own terminal (finding 10); a full ask id is accepted as a hand-over's basis (finding 6).
- The guard: the release gate's hooks are not skipped or moved (G17); only the release position writes to the public repository (G18); agents do not write the code OF loads as plugins, herdr's plugin folders included (G19); every secret store's commands that print a value are denied to agents; `executors*.txt` and file names with the misspelling `secrtets` are secret files.
- Grants signed with the old HMAC signature are no longer trusted.

## 0.1.0 - 2026-11-11

The first public release.

### Added

- **The organization model.** A registry of positions in each organization's own repository (`registry/positions.yaml`), held by people and by agent sessions alike: canonical names `<system>_<role>`, systems with keepers, units, delegations, works and goals, a credentials catalogue. `of registry check` validates it. Signed grants (`of grant`) say what a position may do beyond its organization or the guard.
- **Starting an organization.** `of init` creates the organization's repository with the owner's conductor as its first position and the onboarding unit from the onboarding package (`bench/onboarding/`), installs the recorder hooks after asking, and starts a local of-server. `of spawn` starts a position's agent in its place, on the Claude account with the most headroom, with its brief.
- **Seeing and steering agents on herdr.** `of who`, `of apply`, `of go`, `of view` and the desk (`of desk`), which keeps the agents of the owner's current case in view. The pulse (`of pulse`, inside the listener `of listen`) finds who waits on whom and nudges idle peers.
- **Questions, messages and hand-overs.** Questions to a person as cards with options and a recommendation (`of ask`, `of asks`, the popup); declared state at the end of each turn (`of status`); messages between positions, typed in when the receiver is idle (`of send`, `of inbox`, the relay); hand-overs with a checkable end (`of handover`); structure changes applied only after the owner approves their exact text (`of diff`); `of delegate`, which names the position whose mandate covers a task.
- **Recording.** Every Claude Code session recorded by hooks (`of hooks install`); history brought in by `of import`; typed events derived from it (`of derive`); merged pull requests and commits as work records (`of import github`, `of import git`); shipping to the organization's server with consent per organization (`of ship`, `of consent`, `of shipped`).
- **The server.** of-server with SQLite on one machine (profile 1); enrolling machines and people (`of invite`, `of enroll`); positions held on several machines, tested in the lab; a self-hosted server on a VM (profile 2, `deploy/`), tested in the lab.
- **Guard and secrets.** The guard denies critical actions before a tool runs (secret files, printing secrets, memory reads, file mentions); an organization's own signed deny rules; secrets masked in traces and removable from every stored copy (`of redact`); a broker that serves a credential to the position's holder at the moment of use (`of secret run`).
- **Capacity.** Limits per Claude account (`of capacity status`); moving a session to another account between turns (`of capacity move`, `of capacity balance`); every session back on its account after a restart (`of capacity reboot`); resuming a session on its own account and mode (`of resume-command`).
- **Model, measures and diagnosis.** The organization model and its measures (`of model`, `of studio`, `of works`); anti-patterns found and labelled by the owner (`of issues`, the catalogue in `docs/diagnose.md`); development loops (`of loop`); the owner's corrections as records (`of corrections`); the before-and-after measure of adopting OF (`docs/measure.md`).
- **The lab.** Isolated environments and scenarios that test OF itself (`lab/`).
- **The framework's documents** in `docs/`: mission, ontology, method, systems, the registry and event schemas, the RFCs.
- **License:** Apache License 2.0.

[Unreleased]: https://github.com/organic-framework/organic-framework/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/organic-framework/organic-framework/releases/tag/v0.2.0
