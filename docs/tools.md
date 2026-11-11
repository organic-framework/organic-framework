# Tools: what each position may use, bound to the position

Status: the framework's rule from 2026-11-11, replacing the proposal of [RFC 0004](rfc/0004-tool-profiles.md) where they differ (§7). Owner: `of_tools`. The registry fields are kept in [`registry.md`](registry.md) by `of_architect`.

An agent's tools are its MCP servers, its plugins and the claude.ai connectors it can call. Until now they belonged to the Claude account a session runs on. A move to another account dropped some of them, and every session on one account saw the same set. This document makes them part of the **position**: the registry declares them, and OF generates each session's configuration from that declaration, whatever account the session runs on.

## 1. The practice

| Principle | Source | What it means here |
|---|---|---|
| Permissions belong to roles, not to people or accounts | Sandhu, Coyne, Feinstein, Youman, "Role-Based Access Control Models", IEEE Computer 29(2), 1996; ANSI INCITS 359-2004 | The role is the position. The Claude account is only where a session runs, and grants nothing. |
| Least privilege | NIST SP 800-53 Rev. 5, AC-6 | A position gets the tools its mandate needs and no more. Read by default; a write scope needs a reason in the mandate. |
| Account management and review | NIST SP 800-53 Rev. 5, AC-2 and AC-2(j); ISO/IEC 27001:2022 A.5.15, A.5.18 | Access is granted with a hand-over, kept on a move, removed when a position closes, and reviewed every 90 days. |
| Inventory first | CIS Controls v8, Control 2 | `of tools inventory` lists every tool before anything is bound. |
| Declared, generated, reproducible | Kief Morris, "Infrastructure as Code", 2nd ed., O'Reilly, 2020 | The registry declares; the launcher generates. Nothing that the registry can declare is set by hand on an account. |

## 2. Deciding a position's tools

The structure keeper of the organization decides, from the position's brief, in this order:

1. **The work.** List the systems the mandate names and the cells the position owns (`owns`). A tool that reaches no such system is not granted.
2. **Read or write.** Grant the read form of a tool (`scope: read`) unless the mandate says the position changes that system. A write scope names the mandate line that needs it, in a comment next to the grant.
3. **Whose identity.** Every tool acts as someone (`acts_as`). Prefer the organization's own identity: a bot, a service account, a role with the least rights (RFC 0003 §1a). A tool that **acts as a person** (sends mail, posts as someone, pays, signs) has `scope: act-as-person` and needs the owner's yes on a card (`of ask`) before it is granted. The yes is cited by its ask id in a comment.
4. **Defaults.** What every position of the organization needs (a skills plugin, for example) is `default: true` in the catalogue. What a whole unit needs goes on the unit (`units.<unit>.tools`). A position adds to these, or removes one with `-<id>`.
5. **Portable before account-bound.** A tool OF can start itself (a local server, or a remote one with a token from the broker) works on every account. A tool whose sign-in sits in one Claude account (an OAuth plugin, a claude.ai connector) works only where that sign-in exists (§5). Prefer the portable form when a position depends on the tool.

**Review.** Every 90 days, and when a brief changes, the keeper compares each position's tools with its calls (`of tools inventory`, the usage table): a tool unused for 90 days is removed, unless the brief says why it is kept. A position that is parked or finished keeps no tools.

## 3. The registry

The catalogue is a top-level `tools:` mapping in the organization's registry. Positions and units name tools from it.

```yaml
tools:
  superpowers:
    kind: plugin                          # mcp-stdio | mcp-remote | plugin | connector
    plugin: superpowers@claude-plugins-official
    default: true                         # every position of the organization
    acts_as: "nobody: skills only, no server"
    scope: read
    keeper: acme_org
  grafana-read:
    kind: mcp-stdio
    command: ["~/.local/share/of/tools/mcp-grafana/1.6.1/mcp-grafana", "--disable-write"]
    env: {GRAFANA_URL: "https://grafana.example", GRAFANA_ORG_ID: "1"}
    credential: {id: grafana-viewer, file: GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE}   # or env: NAME
    acts_as: "Grafana service account agents-viewer, role Viewer"
    scope: read
    keeper: obs_keeper
  slack:
    kind: plugin
    plugin: slack@anthropic-plugin-directory
    url: https://mcp.slack.com/mcp        # the plugin's server; the session allows it by this URL
    account_bound: true                   # its OAuth sign-in stays in one Claude account
    acts_as: "the founder's Slack user"
    scope: [read, act-as-person]          # the owner's yes: ask <id>
    keeper: acme_dispatcher

units:
  obs: {tools: [grafana-read]}

positions:
  - name: acme_dispatcher
    tools: [slack, -superpowers]
```

Rules:
- **A tool names its credential by id** from the organization's credentials catalogue (`registry.md`, Credentials), with the variable the server reads: `env:` for the value, `file:` for the path of a temporary file that holds it. It never carries a vault reference or a value. The position that uses the tool must list that credential too; the broker checks this at launch.
- **`keeper`** owns the integration: its credential and rotation, its version, its scope.
- **`account_bound: true`** marks a tool whose sign-in lives in one Claude account. A `connector` is always account-bound.
- **`url`** of a plugin or connector is the server address the session allows. `of tools inventory` shows it per account.
- `of tools check` validates all of this; `of registry check` calls it once `of_platform` wires it in.

## 4. The launch

Every start of a position's session generates its configuration from the profile: `of spawn`, `of capacity move` and `resume`, the reboot restore, and a manual `of start`. They call `of_tools.launch_parts(position, session, config_dir)`, which returns:

- **`--mcp-config <file>`** (`~/.local/state/of/tools/<session>.mcp.json`, mode 600): the position's portable MCP servers. A server that needs a credential is started as `of secret run <credential> --file|--env NAME -- <server command>`. The broker gives the value to that one process. It is never in the file, never in the Claude Code process's environment, and so never visible to the session's shell commands.
- **The `--settings` keys**, merged into the session's one settings file (`bin/of_spawn.py`, `session_settings`):
  - `allowedMcpServers`: exactly the profile's servers. Portable ones pass by `serverName`, account-bound ones by `serverUrl`.
  - `enabledPlugins`: every plugin installed on the account, `true` for the profile's and `false` for the rest.

`of tools plan <position> [--config-dir D]` prints what a start would generate.

**What the harness does, checked** (Claude Code 2.1.322, 2026-11-11; probes are `claude -p --output-format stream-json`, reading the `init` event's `mcp_servers` and `plugins`):

| Fact | Documented | Probe result |
|---|---|---|
| `--mcp-config` loads servers from files or JSON strings | yes: "Load MCP servers from JSON files or strings (space-separated)" (code.claude.com/docs/en/cli-reference) | A stdio server from a generated file connected, with `${VAR}` placeholders in `env` expanded |
| `${VAR}` and `${VAR:-default}` expand in `command`, `args`, `env`, `url`, `headers` | yes (code.claude.com/docs/en/mcp) | confirmed for `env` under `--mcp-config` |
| `allowedMcpServers` in a session's `--settings` | listed as "Allowlist which MCP servers users can add", any settings file (code.claude.com/docs/en/settings) | It filters **every** source: user, plugin, claude.ai connector and `--mcp-config` servers. `serverName` matches user and `--mcp-config` servers; plugin and connector servers match only by `serverUrl` (a plugin's server name `plugin:slack:slack` did not match). An empty list allows none. |
| `enabledPlugins` in `--settings` | a `--settings` key "overrides the same key in your project and user settings files" (code.claude.com/docs/en/settings) | It merges **per entry**: naming one plugin `true` left the others enabled. So every installed plugin is named. |
| `--strict-mcp-config` | "uses only the MCP servers you pass with `--mcp-config`" (code.claude.com/docs/en/mcp) | It hides user, plugin and claude.ai connector servers; the plugins' skills still load. |
| OAuth sign-ins | "stores OAuth sign-ins per endpoint" (code.claude.com/docs/en/mcp) | A sign-in made through a plugin on one account did not carry to the same URL defined in `--mcp-config` on that account (`needs-auth`), nor to another account. |
| The MCP server's environment | not documented | A stdio server receives `CLAUDE_CODE_SESSION_ID`, so `of secret run` inside it identifies the session's position. |

So OF does **not** use `--strict-mcp-config`: it would hide the account-bound tools a position is granted. The allowlist gives the same exclusion and keeps them.

**A session started outside OF** (a bare `claude` in a pane) gets the account's own servers until the accounts are emptied (§6, step 5). The guard then flags its MCP calls (§6, step 4).

## 5. Account-bound tools and moves

An OAuth tool's sign-in, and a claude.ai connector, exist only on the accounts where someone signed in.
- `of capacity plan` and `move` read the position's account-bound tools (`launch_parts` lists them; `of tools plan` prints them). A move goes only to an account where they are connected. Otherwise the move stops, and the founder gets one task per account, listing each missing sign-in, before the move rather than after.
- Such a sign-in is the founder's (or the tool keeper's) work: one task per account, filed only when a position needs that tool there.
- A tool a position depends on moves to a portable form where the service allows it: a bot or service-account token held by the broker, or the tool's own OAuth sign-in through the bridge (§6, end).

## 6. The plan

1. **Inventory** (done 2026-11-11): `of tools inventory`; report in the build team's repository, `reports/framework/tool-inventory-2026-11-11.md`.
2. **The schema** in `registry.md`, agreed with `of_architect`; `of tools check` inside `of registry check` (`of_platform`).
3. **The launcher**: `of_tools.launch_parts`, called by `of spawn`, `of start`, `of capacity move|resume` and the reboot restore (`of_platform`, `of_capacity`). An organization without a `tools:` catalogue gets no change until its keeper declares one.
4. **The guard** (`of_server`): an MCP call to a server outside the position's profile is denied; a credential in an MCP config file (a value in `env` or `headers`, or a key in a URL) is a finding for the tool's keeper.
5. **The lab proof** (`of_lab`): two accounts; a position with a local tool and an account-bound tool moves from one to the other; both work on both sides where signed in; a second position on the same account does not see them.
6. **The catalogues**: the OF team's, then each user organization's, through its structure keeper. Then the user-scope servers in each account's `.claude.json` are removed, first their credentials, then the servers.
7. **Codex**: the same profile as `-c mcp_servers.<id>.…` overrides, or a generated `CODEX_HOME`, when `of_herdr` starts Codex through the launcher. Codex's documentation does not say yet whether its `-c` overrides reach `mcp_servers`; this is checked before it is built.

**A remote server with its own sign-in, made portable** (`bin/of_mcp_bridge.py`, 2026-11-11). Many services serve MCP over HTTP with OAuth (Slack, Notion, Linear, Cloudflare, a user organization's accounting service), or put a key in the URL. In Claude Code such a sign-in stays with one account (§4). The bridge makes it the tool's own instead:
- `of_mcp_bridge.py login --url-file F --oauth-store D`: one sign-in, in a plain terminal, by the person or the identity the tool acts as. It discovers the authorization server (RFC 9728, RFC 8414), registers its own client (RFC 7591), signs in with PKCE (RFC 7636) on a 127.0.0.1 callback, and writes the client and refresh token to `D/oauth.json`, mode 600. The folder is a credential in the organization's catalogue, kept by the tool's keeper.
- In the catalogue the tool is `kind: mcp-stdio` with the command `python3 <of>/bin/of_mcp_bridge.py --url-file F --oauth-store D`. The bridge refreshes the token under a lock, so every session of the position on every account uses it, and the session's shell never sees it.
- `of_mcp_bridge.py call … <tool> '<json>'` calls one tool from a shell, for a session started before its profile had the tool.
- A URL that carries a key is moved out of a config file with `of tools adopt-url <server> --to F`, which never prints it.

Not built yet: portable plugins (a plugin loaded with `--plugin-dir` from a folder OF keeps, instead of each account's cache).

## 7. Changes from RFC 0004

- A tool names a credential from the credentials catalogue by id; RFC 0004's inline `{vault: …}` is not used.
- The credential reaches the server's own process through `of secret run`, not the Claude Code process's environment.
- `--strict-mcp-config` is replaced by `allowedMcpServers` and `enabledPlugins` in the session's settings, which keep account-bound tools (§4).
- `scope` takes `read`, `write` and `act-as-person`; a tool with `default: true` is every position's default.
