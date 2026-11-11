# Plugins: OF works with whatever the environment has

Status: **design**, 2026-11-11, by `of_architect`. The founder, about 19:00Z: "we need a plugin system for such things, where different systems can be in the environment: LastPass, 1Password, AWS Key Management, Keychain on the Mac and so on. Everything should connect through plugins, without OF holding the view that we must use only 1Password."

## 1. The rule

- **OF prefers no product.** Every place where OF talks to an outside product goes through a **family**: a small interface with any number of implementations. OF ships some of them; anyone can add one without changing OF.
- **The owner chooses, OF does not assume.** `of init` detects what the machine has and proposes; the choice is written to configuration. Nothing falls back silently to a product the owner did not choose; a missing choice is a clear message that names the families' options.
- **One mechanism for the client and the server.** of-server's seams (`bin/of_plugins.py`: storage, buffer, blobs, identity, chosen in `server.yaml`) become this mechanism's first families.

## 2. The mechanism

- **A family** is a Python class with a few methods (§3), a registry of named implementations, and a contract test suite that every implementation passes (`tests/contracts/<family>/`, run by `of_lab`).
- **Choosing.** A machine's choices live in `~/.config/of/plugins.yaml` (`<family>: <name>` plus that implementation's settings). A choice that belongs to an organization, such as where its secrets live, sits in the organization's registry (`plugins:`), so every machine and colleague of that organization uses the same one. The organization's choice wins over the machine's for its own data.
- **Loading.** OF loads implementations it ships (`bin/plugins/<family>/<name>.py`) and, from installed Python packages, entry points in the group `organic_framework.<family>`. It never loads code from a folder an agent can write: the guard denies agents writes to installed plugin code, so a plugin cannot be planted by an agent. A plugin runs with OF's own rights; installing one is the owner's act.
- **Naming references.** Where a record names something inside a product, it uses the family's URI form: a credential's `ref` is `<backend>://<path>` (for example `op://acme-obs/grafana/credential`, `keychain://acme/grafana-viewer`, `vault://secret/acme/grafana#token`, `aws-sm://acme/grafana`), so a registry says which backend holds it and moves between backends by changing the ref.

## 3. The families

| Family | What it does for OF | Methods (outline) | Ships with | Owner |
|---|---|---|---|---|
| `secrets` | where credentials live; the broker reads and writes through it | `read(ref)` into a process only, `write(ref, value)`, `exists(ref)`, `list(scope)` names only, `bootstrap()` | 1Password service accounts; macOS Keychain; an env file for a lab. Candidates: Bitwarden, LastPass, HashiCorp Vault, AWS Secrets Manager and KMS, Google Secret Manager | `of_server` |
| `runtime` | the panes agents run in: list, read, deliver text when idle, label, focus | `agents()`, `read(pane)`, `deliver(pane, text)`, `label(pane, …)`, `focus(pane)` | herdr; tmux; none (a terminal, inbox only) | `of_herdr` |
| `harness` | the agent program: start and resume a session, its hooks, its transcripts, its accounts | `start(…)`, `resume(session)`, `install_hooks()`, `transcripts()`, `accounts()` | Claude Code. Candidate: Codex | `of_platform`, accounts with `of_capacity` |
| `code_host` | work records: merged changes and their authors | `merged(since)`, `authors(paths, since)`, `pr(id)` | git (local); GitHub. Candidates: GitLab, Bitbucket | `of_platform` |
| `service` | OF's background services | `install(unit)`, `start`, `stop`, `status` | launchd; systemd; foreground | `of_platform` |
| `notify` | reaching a person when something waits on them | `notify(person, item)` | herdr popup; the person's conductor (docs/remote.md); macOS notification | `of_asks` |
| `model` | the small judges (questions, mandate fit, the case, corrections) | `judge(prompt, schema)` | Claude through the harness. Candidates: another API, a local model | `of_platform` |
| `storage`, `buffer`, `blobs`, `identity` | of-server's store, queue, files and machine identity | as in `bin/of_plugins.py` today | SQLite, ClickHouse; disk; file system; machine keys | `of_server` |

Tools for agents (MCP servers, plugins, connectors such as Slack or Gmail) are already chosen per organization and position by the tools catalogue (docs/registry.md, Tools; docs/tools.md); they are not a family here.

## 4. Where OF assumes one product today (2026-11-11, `bin/`)

| Product | Files | Becomes |
|---|---|---|
| 1Password (`op`, service accounts) | 14, among them `of`, `of_capacity.py`, `of_credentials.py`, `of_grants.py`, `of_guard.py`, `of_hook.py`, `of_client.py` | `secrets`; the guard's rules about `op` become rules about the chosen backend's command |
| herdr | 35 | `runtime` (tmux already exists in `of_tmux.py`) |
| Claude Code (`claude`, `CLAUDE_CONFIG_DIR`, its hooks and transcripts) | 10, among them `of_hook.py`, `of_capacity.py`, `of_resume.py`, `of_init.py`, `of_judge.py` | `harness`; the small judges move to `model` |
| GitHub (`gh`) | 12, among them `of_github.py`, `of_coord.py`, `of_listen.py`, `of_measures.py`, `of_model.py` | `code_host` |
| launchd, systemd | 6 and 5 (`of`, `of_init.py`, `of_listen.py`, `of_server.py`, `of_capacity.py`) | `service` |
| macOS notifications | `of_guard.py` | `notify` |
| ClickHouse | 10 (`of_route.py`, `of_ship.py`, `of_server.py`, `of_redact.py` and others) | `storage` only; the client stops naming it |

Each owner lists the exact call sites in its part when it converts, and the contract tests prove the conversion.

## 5. Order

1. **The mechanism** (`of_platform`): the loader shared by client and server, `plugins.yaml`, the organization's `plugins:` key, entry points, the guard's deny on writes to installed plugin code (with `of_server`), and `of init` proposing choices from what it detects.
2. **`secrets` first** (`of_server`), with three implementations (1Password, macOS Keychain, an env file) and the `<backend>://` refs; the broker and `of_capacity`'s credential broker use it. This answers the founder's request directly.
3. **`runtime`** (`of_herdr`): herdr and tmux; the remote-only design's delivery into tmux (docs/remote.md row 5) is its first use.
4. **`notify`, `code_host`, `service`, `harness`, `model`**, each by its owner, in any order.
5. **Contract tests** per family (`of_lab`), so a third party's plugin can prove itself.
