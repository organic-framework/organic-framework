# RFC 0004: tool profiles, MCP access per position, independent of accounts

| | |
|---|---|
| Status | **Draft**, 2026-11-07 |
| Author | `of_architect` |
| Decides | the founder |
| Depends on | RFC 0001 (platform), RFC 0002 (secrets in traces), RFC 0003 §1a (the agents' own identity, the 1Password service account), `docs/capacity.md` (moves), `docs/registry.md` (grants) |

## 1. Summary

An agent's tools (MCP servers, plugins, connectors) belong today to the **Claude account** it runs on, not to its **position**. As a result:
- **A move breaks them.** When `of_capacity` moves a holder to another account, its tools stay behind.
- **Every agent on an account gets the same tools.** Nothing separates what a position may use.
- **Their setup and credentials are copied by hand into every account.** One Grafana token file serves every session, and a rotation breaks all of them at once.

This RFC makes tools part of the position:
- each organization declares a catalogue of tool services;
- each position names the ones it may use;
- OF starts every session with exactly those tools, from a config it generates, whatever account the session runs on;
- credentials come from the organization's vault at launch and are never written to a config file.

## 2. Facts (2026-11-07, the first user organization)

1. **A move dropped Slack.** Its dispatcher lost Slack when it moved to another account's config dir at 11:35Z (founder task `fe1e3bfd`). The Slack plugin and its OAuth were set up on the old account only, so it cannot see colleagues' answers until the founder reconnects it by hand.
2. **A move dropped Gmail.** No session on that account has the Gmail tools. The Gmail integration stayed on the account the organization's agents left in the morning.
3. **One Grafana token is shared by every session on every account.** Grafana MCP is configured by hand at user scope in all four config dirs and in Codex, each reading the same token file. The token's service account had the Admin role, and one such token leaked into another session on 10-28 (`platform_owner`'s ask `b1f5f4a6`). When it was rotated on 11-07, Grafana MCP in every session answered 401 until each config pointed to the new file.
3a. **The inventory confirms it** (`of_platform`, `reports/mcp-inventory-2026-11-07.md`).
   - **Configs differ by account.** Every config dir has Grafana (stdio, token from a file) and the Cloudflare plugin; Linear is enabled only in one config dir, and the Slack plugin only in another. So a move adds or drops Linear and Slack.
   - **Usage over 14 days:** Grafana 2,145 calls from 37 positions, the claude.ai Slack connector (account-bound) 338, the Slack plugin 221, Cloudflare 160, Linear 5.
   - **No project `.mcp.json` files.** Codex adds node_repl, computer-use, Grafana, Airflow and an accounting system.
   - **No credential in plain text** in any MCP entry or config file.
4. **Sessions wait on MCP authentication** with no owner to clear it (`blocked:mcp_auth`: `platform_owner` twice on 11-06, `aws_keeper` on 11-07).
5. **Claude Code can already take a session's MCP servers from outside the account.** `--mcp-config <file>` loads servers from a file, and `--strict-mcp-config` ignores every other MCP configuration. Codex takes MCP servers from its own config or from `-c mcp_servers.<id>…` overrides.

## 3. Design

### 3.1 The catalogue: `tools:` in the organization's registry

```yaml
tools:
  grafana-read:
    kind: mcp-stdio                 # mcp-stdio | mcp-remote | plugin | connector
    command: ["~/.codex/tools/grafana-mcp/1.6.1/mcp-grafana", "--disable-write"]
    env:
      GRAFANA_URL: https://grafana.example
      GRAFANA_SERVICE_ACCOUNT_TOKEN: {vault: "Acme Agents/grafana viewer"}   # resolved at launch, never written
    acts_as: "Grafana service account 'agents-viewer', role Viewer"
    scope: read
    keeper: obs_keeper              # owns the integration, its credential and its rotation
  slack-dispatch:
    kind: mcp-remote
    url: https://slack-mcp.example/mcp
    headers:
      Authorization: {vault: "Acme Agents/slack bot token", format: "Bearer {}"}
    acts_as: "Slack app 'Acme agents' (bot), not a person"
    scope: [read, post]
    keeper: acme_dispatcher
  gmail-founder:
    kind: connector                 # bound to one Claude account (claude.ai connector or per-account OAuth)
    account_bound: true
    acts_as: "the founder's mailbox"
    scope: [read, send]
    keeper: acme_org
```

Rules:
- **Every tool acts as a named identity**, preferably the organization's own (RFC 0003 §1a): a bot, a service account, a role with the least rights. A tool that acts as a person says so in `acts_as`, and its write scopes fall under acc-v0's row 10 (speaking outside) or row 6 (his own accounts).
- **Credentials live only in the vault.** `{vault: "<vault>/<item>"}` is resolved by a committed launcher at start and passed to that process alone. A token in a plain config file is a guard finding.
- **The keeper** owns the integration: the credential, its rotation, its scope. When a credential rotates, the keeper updates the vault item, and every session picks it up at its next start.
- **`account_bound: true`** marks what lives in one Claude account and cannot travel: claude.ai connectors, and plugins whose OAuth sits in the account. Avoid it for any tool a position depends on. Where it cannot be avoided, §3.4 applies.

### 3.2 The grant: `tools:` on the position

```yaml
- name: acme_dispatcher
  tools: [slack-dispatch]
- name: obs_keeper
  tools: [grafana-read, grafana-write]
```

- **A position gets only what its mandate needs.** A unit or role may give defaults (`units.<unit>.tools`), and a position adds to them or removes from them.
- **A write scope must be justified by the mandate.** The structure keeper adds or changes these lines like any registry change, and a write scope that acts as a person needs the owner's yes.
- **This is the first concrete form of the grants of `docs/registry.md`.** A later version folds it into `grants:`, with the same meaning.

### 3.3 Launch: one config per session, from the profile

Every start of a position's session generates `~/.local/state/of/tools/<position>.json` from its profile: `of spawn`, `of capacity move` and `resume`, the desk, and a manual start through `of start <position>`.
- **The config holds no secrets:** `${VAR}` placeholders only.
- **A committed launcher starts the harness:**
  - it resolves the vault references through the organization's service account and puts them into the environment of that process alone;
  - Claude Code gets `--mcp-config <file> --strict-mcp-config`;
  - Codex gets the same servers as `-c mcp_servers.<id>…` overrides.
- **Result:** the account changes nothing about a session's tools. A move keeps them because they are not stored in the account. Two positions on one account see different tools.
- **A start outside OF** (a bare `claude` in a pane) gets the account's own MCP config, which this RFC empties over time (§5, step 4). The guard flags MCP calls from a session that has no profile.

### 3.4 Account-bound tools and moves

- `of capacity plan` and `move` read each position's account-bound tools. A move goes only to an account that has them connected. Otherwise it stops, and the founder gets one task for each missing connection, batched per account, before the move rather than after.
- **When a new connector is added**, the keeper says on which accounts it must exist (all of them, if the position may move anywhere). `of capacity` checks this parity once a day.

### 3.5 Audit and the guard

- **Every MCP call is already a tool call in the hook** (`mcp__<server>__<tool>`). It is recorded with the position, the tool id and the scope.
- **The guard denies an MCP call whose server is not in the position's profile.** This is defence in depth: the strict config already hides such servers.
- **A credential found in an MCP config file** (`.claude.json`, `.mcp.json`, Codex `config.toml`) is a guard finding with an owner: the tool's keeper.

## 4. Who builds what

| Step | What | Owner |
|---|---|---|
| 1 | Inventory. Read-only, names only: the MCP servers, plugins and connectors per config dir, per project and in Codex; which positions called which `mcp__` tools in the last 14 days; where credentials sit in plain text | `of_platform` |
| 2 | Registry schema: `tools:` catalogue and position `tools:`, with `of registry check` | `of_architect` (schema), `of_platform` (check) |
| 3 | The launcher and the generated config, for Claude Code and Codex, used by `of spawn` and `of start` | `of_platform` |
| 4 | Vault resolution at launch, and the guard (calls outside the profile, credentials in config files) | `of_server` |
| 5 | Moves keep the profile; parity of account-bound tools before a move | `of_capacity` |
| 6 | Lab scenario: two accounts, a position with a stdio tool and a remote tool moves between them, and both tools work on both sides | `of_lab` |
| 7 | Each organization's catalogue and grants. For the first user organization: Slack and Gmail move to the organization's own identities where possible, and Grafana to a Viewer service account | each structure keeper with its keepers |

## 5. Order

1. Inventory (step 1) and schema (step 2), at once.
2. Launcher and vault resolution (steps 3 and 4), tested in the Lab (step 6).
3. Moves and parity (step 5).
4. Organizations declare their catalogues (step 7). The account-level MCP configs are then emptied of credentials, and finally of servers.
