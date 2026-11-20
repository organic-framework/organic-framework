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
tools_mode: add                           # add (the default) | enforce
tools_excluded:                           # plugins no position starts with, in either mode
  - {plugin: slack@anthropic-plugin-directory, basis: "the owner's ask id"}
  - {url: "https://mcp.slack.com/*", basis: "the owner's ask id"}   # a claude.ai connector or a plugin's server
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
- **A tool names its credential by id** from the organization's credentials catalogue (`registry.md`, Credentials), with the variable the server reads: `env:` for the value, `file:` for the path of a temporary file that holds it. A credential still kept in a file is read by the server itself: the command or an `env` value names its path, and a leading `~`, `${HOME}` or `$HOME` in `env` is expanded at launch (Claude Code itself expands only the braced `${VAR}`). It never carries a vault reference or a value. The position that uses the tool must list that credential too; the broker checks this at launch.
- **`tools_mode`** says whether the catalogue is complete. With `add`, the default, a session gets its profile's portable servers on top of the account's own tools, and nothing is filtered. With `enforce`, the session sees exactly its profile (§4). The keeper sets `enforce` only when every position's tools are declared (2026-11-12: one catalogue entry, applied strictly, took Cloudflare and Grafana from two keepers moved to another account). Under `add`, a catalogue tool that is also defined on the account (a user-scope server in its `.claude.json`) appears twice in the session, once under each name; remove the account's copy when the catalogue entry lands.
- **`tools_excluded`** names plugins, or MCP server URLs, that no position of the organization starts with, whatever its account enables, each with its basis (the owner's ask id or a source). It works in both modes: every session's settings carry `enabledPlugins: {<plugin>: false}`, and under `add` only those entries, so the account's other plugins stay. An entry with `url` puts `deniedMcpServers: [{serverUrl: <url>}]` into every session's settings, in both modes; a trailing `*` matches any path. It hides a claude.ai connector, which `enabledPlugins` cannot reach, and a plugin's server alike (probed 2026-11-14, Claude Code 2.1.325: a connected claude.ai Slack connector was gone from the session). A catalogue entry for an excluded plugin is a problem for `of registry check`. It hides the tool from the agents only: the sign-in stays on the account until its owner signs out, and a live session keeps what it loaded until its next start (2026-11-14: a user organization's owner chose that no agent acts as him in Slack, while one of its accounts still enabled the Slack plugin, signed in as him).
- **`keeper`** owns the integration: its credential and rotation, its version, its scope.
- **`account_bound: true`** marks a tool whose sign-in lives in one Claude account. A `connector` is always account-bound.
- **`signin: <label>`** on an account-bound entry names which of the account's sign-ins it needs, when one account holds a sign-in with more rights than the plain entry (§5a).
- **`url`** of a plugin or connector is the server address the session allows. `of tools inventory` shows it per account.
- `of tools check` validates all of this; `of registry check` calls it once `of_platform` wires it in.

## 4. The launch

Every start of a position's session generates its configuration from the profile: `of spawn`, `of capacity move` and `resume`, the reboot restore, and a manual `of start`. They call `of_tools.launch_parts(position, session, config_dir)`, which returns:

- **`--mcp-config <file>`** (`~/.local/state/of/tools/<session>.mcp.json`, mode 600): the position's portable MCP servers. A server that needs a credential is started as `of secret run <credential> --file|--env NAME -- <server command>`. The broker gives the value to that one process. It is never in the file, never in the Claude Code process's environment, and so never visible to the session's shell commands.
- **The `--settings` keys**, with `tools_mode: enforce` (under `add`, only the `enabledPlugins` entries of `tools_excluded`), merged into the session's one settings file (`bin/of_spawn.py`, `session_settings`):
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

### 4a. One shared server per tool on a machine

A local tool server started per session costs one process per session. On 2026-11-14 the founder's laptop ran 83 Grafana tool servers for about 80 sessions, about 25 MB each (`footprint`), while its swap was full (of_architect's hand-over 75265244, organic-framework-build 4f8eb4e, R1). A tool whose server speaks MCP over HTTP runs instead as **one server for the machine**, and each session connects to it.

```yaml
tools:
  grafana:
    kind: mcp-stdio
    command: [~/tools/mcp-grafana, --disable-write]
    env: {GRAFANA_URL: "https://grafana.example", GRAFANA_SERVICE_ACCOUNT_TOKEN_FILE: "~/.config/acme/grafana.token"}
    credential: {id: grafana-viewer}
    shared:
      port: 18310                        # unique on the machine; of tools check reports a clash
      args: [--transport, streamable-http, --address, "127.0.0.1:{port}", --session-idle-timeout-minutes, "0"]
      path: /mcp                         # the default
      token_env: MCP_GRAFANA_SERVER_TOKEN  # required: where the server takes its access token
```

- **The server.** `of tools shared install` writes one launchd unit per shared tool (`dev.organic-framework.tool.<org>.<tool>`, `OF_AUTOMATION=1`, kept alive), which runs `of tools serve <org>/<tool>`: the entry's command, its credential given as for a session's own server, its `env`, and `shared.args`. It listens on 127.0.0.1 only. `of tools shared status` says whether each port answers.
- **The access token** is OF's own random value, made at the server's first start, in a mode-600 file under `~/.local/state/of/tools/shared/`. It is never an outside credential, and it is never written into a session's MCP config.
- **The session.** When the port answers at launch, the session's `--mcp-config` names the tool as `{type: http, url: http://127.0.0.1:<port>/mcp, headersHelper: "python3 <of bin>/of_tools.py header <org>/<tool>"}` (configs written before 2026-11-18 name `of tools header`, which works the same, only slower). Claude Code runs the helper as its own child when it connects. The helper takes nothing from its caller's word: it walks up to the nearest parent started with `--mcp-config <state>/tools/<session>.mcp.json` and `--session-id <session>` (or `--resume <session>`, as a move, a restart or `of resume-command` relaunches it), the file OF wrote at that session's launch from its position's profile, and prints the `Authorization` header only when that file names this tool's URL. So the profile still decides, and an agent's own shell command, whose nearest such parent is its own session, gets the token only when its own profile has the tool (probed with Claude Code 2.1.325: a session launched this way connected and saw 27 Grafana tools; a call from a session OF did not launch this way was refused). Without the token the server answers 401 (probed, mcp-grafana 1.6.1), and Claude Code then takes the server for an OAuth one and fails with "Dynamic Client Registration rejected (HTTP 404)": the sign of a session the helper refused (2026-11-14, moved sessions before the helper accepted `--resume`), or of a helper that ran out of time. Claude Code waits on the helper only so long and does not retry the server: in Claude Code 2.1.326's own MCP logs, helpers that took up to 10.6 s were accepted and those that took 11.6 s and 12.3 s were not (2026-11-17: three sessions, one of them started by `of spawn` with a correct config, while the machine was loaded). On 2026-11-18 two sessions restarted for that reason failed the same way, with the machine's load average near 100 on 10 cores. So the helper does as little as it can: it runs `of_tools.py` itself rather than `of`, which compiles its whole file on every run, and it reads the server's URL from a cache beside the tokens (`<state>/tools/shared/urls.json`, mode 600), keyed by the registry's path and the hash of what the catalogue was read from: its `tools:` block when that parses alone (4 KB of a 517 KB registry: 0.011 s against 0.62 s), else the whole file, as when the block's alias names an anchor outside it (`of_server` and `of_review`, PR 329). The helper then takes about 0.13 s of CPU, Python's start included. The cache is no trust root, since an agent can write it: a cached URL is used only as `http://127.0.0.1:<port>/<path>` and only when its HMAC-SHA256 under the tool's own token checks over the organization, the tool, the registry's key and the URL; otherwise the helper reads the registry (`of_server`, PR 322 review). A session left without its shared server gets it back with `/mcp` (reconnect) or a restart. A shared entry without `token_env` is refused by `of tools check` and never served. Claude Code runs a `headersHelper` only in a trusted folder, and `of spawn` starts sessions only in trusted folders.
- **Fallback.** When the port does not answer at launch, the session starts its own server, as before, and `of tools plan` notes it.
- **Limits.** Every session runs as the same operating-system user, so a determined agent could still reach the token: read its file, or start a nested Claude Code with another session's config file. Both are visible commands for the guard (its trust review, `of_server`); the binding stops a position from using the tool by accident or by naming another position. The server acts for one identity, the tool's credential, for every session that uses it; Grafana's audit shows the service account, not the position. One session's heavy query slows the others. A server that restarts drops its sessions' MCP sessions; whether Claude Code reconnects at the next call is checked in the rollout (§4a is being rolled out with Grafana first). `--session-idle-timeout-minutes 0` keeps an idle session's MCP session from being reaped.
- **The account's own copy.** A user-scope server of the same tool in an account's `.claude.json` still starts in every session on that account. Remove it from each account when the shared server runs (`claude mcp remove <name> -s user` with the account's config folder), or nothing is saved.

### 4b. A position held by Codex

Status: proposed 2026-11-20 by `of_tools` (hand-over `68dfbcca`, item W7 of organic-framework-build `reports/codex-main-harness-plan-2026-11-20.md`). The kinds are in [`registry.md`](registry.md) (`of_architect`); the layer's form is to be agreed with `of_platform`, who builds `of spawn` for Codex (W4).

A position whose holder runs on Codex gets its tools from the same profile as on Claude Code: the same catalogue, the same `tools:` on its unit and on itself, the same review (§2). Only the form differs. Codex reads its tools from its configuration, and it has tools of its own that Claude Code does not.

**Where the configuration goes.** A Codex seat is one `CODEX_HOME` with one sign-in (W9), shared by every position on it, as a Claude account's folder is. So nothing of one position goes into the seat's `config.toml`.
- At each start, `of spawn` writes a layer for the session, `$CODEX_HOME/of-<session>.config.toml` (mode 600), and starts `codex -p of-<session>`. The interactive `codex` has no `--ignore-user-config`; only `codex exec` has it. So the seat's own file still loads under the layer.
- `of_tools.codex_layer(position, session, codex_home)` returns the layer's tool tables: `features`, `mcp_servers`, `apps` and `plugins`. It also writes `<state>/tools/<session>.mcp.json`, as for Claude Code, for the shared-server helper. `of spawn` merges these tables with its own (the model, W3's permission profile, W5's instructions) into the one layer file. `of tools plan <position> --codex` prints them.
- OF writes the seat's own `config.toml` and keeps tools out of it. `of tools check` reports a seat whose file defines `mcp_servers`, `plugins` or `apps`.

What this rests on (Codex CLI 0.169.1, probed 2026-11-20 in a fresh `CODEX_HOME`; the source read at the tags `rust-v0.169.0` and `rust-v0.170.0` of github.com/openai/codex, Codex run `01a1f8d6-69a0`):

| Fact | Source | Probe |
|---|---|---|
| `-p NAME` layers `$CODEX_HOME/NAME.config.toml` over `config.toml`. It takes the ordinary top-level keys, `mcp_servers`, `features`, `apps` and `plugins` among them. The old `[profiles.NAME]` tables are refused. | CLI help: "Layer $CODEX_HOME/<name>.config.toml on top of the base user config"; developers.openai.com/codex/config-advanced, "Profiles" | `codex -p of-s1 mcp list`: the layer's `enabled = false` turned off a server of the seat's file, and its own server was listed |
| `-c mcp_servers.<id>.…` adds or overrides one server for one run | developers.openai.com/codex/config-advanced, "One-off overrides from the CLI" (`mcp_servers.context7.enabled=false`) | `codex mcp list -c mcp_servers.t1.command=…` listed a server defined nowhere else |
| A server is `command`/`args`/`env`/`cwd` (stdio) or `url` with `http_headers`, `env_http_headers`, `bearer_token_env_var` or `http_headers_helper` (HTTP). It also takes `enabled`, `required`, `enabled_tools`, `disabled_tools` and the timeouts. An inline `bearer_token` is refused. | `codex-rs/config/src/mcp_types.rs`; `--strict-config` refuses an unknown field of a server | yes |
| Stdio servers and header helpers run outside the sandbox of the model's commands | CLI trust help: "MCP commands outside the sandbox"; `codex-rs/codex-mcp/src/runtime.rs`: "HTTP headers helpers can only run in the local environment" | not probed |
| `http_headers_helper` is one string, run as `sh -c <string>`. It runs with a cleared environment: `HOME`, `PATH`, `USER`, `SHELL`, `LANG`, `TMPDIR` and a few more. It is run once per connection and once more on a rejected request. It must answer within **10 s** with a JSON object of headers. | `codex-rs/rmcp-client/src/http_headers.rs`; binary strings "headers helper timed out after 10 seconds", "headers helper output exceeds 64 KiB" | an array or a table is refused: "expected a string" |
| The account's apps are one built-in server, `codex_apps`. It is on when `features.apps` is on and the seat signed in with ChatGPT. `apps._default.enabled = false` turns every app off, and `apps."<id>".enabled` turns one back on. | `codex-rs/features/src/lib.rs`, `codex-rs/connectors/src/app_tool_policy.rs`; config reference | `--strict-config` accepts `apps._default.enabled`; a fresh seat has `apps` on |
| A plugin can bring skills, MCP servers, apps and hooks. It is installed under `$CODEX_HOME/plugins/cache/`. `plugins._default.enabled = false` turns every plugin off, and `plugins."<name>@<marketplace>".enabled = true` turns one back on. | `codex-rs/config/src/types.rs`; `codex-rs/core-plugin-common/src/installed.rs` | a fresh seat has `plugins` and `remote_plugin` on |
| `browser_use`, `browser_use_external`, `browser_use_full_cdp_access`, `in_app_browser`, `computer_use` and `in_app_local_automation` are the desktop app's controls ("Requirements-only gate"). No documented CLI tool comes from them. | `codex-rs/features/src/lib.rs`; developers.openai.com/codex/enterprise/managed-configuration | a fresh seat has them on |

**What each catalogue kind becomes on Codex.**

| Kind | In the layer |
|---|---|
| `mcp-stdio` | `[mcp_servers.<id>]` with `command`, `args` and `env`. `of secret run` comes in front when the entry names a credential, as in §4; the layer holds paths and names, never a value. |
| `mcp-stdio` with `shared:` (§4a) | `url = "http://127.0.0.1:<port>/mcp"` and `http_headers_helper = "python3 <of bin>/of_tools.py header <org>/<id>"`. Codex starts the helper through `sh`, so `sh` is its parent and the session's `codex -p of-<session>` above it. The helper accepts the nearest ancestor that names `-p`/`--profile of-<session>` when `<state>/tools/<session>.mcp.json` names this tool's URL, as it accepts a Claude Code session by its `--mcp-config` file. Codex waits 10 s on it, against about 11 s on Claude Code, so the cache of §4a matters as much here. |
| `mcp-remote`, portable | `url`, with its headers as `http_headers`. A header that needs a credential goes through the bridge (§6), never into the layer. |
| `plugin`, `connector` | Nothing: they are Claude Code's, and their sign-ins live in a Claude account. `of tools plan` names each one it skips. A position that needs the same service on Codex gets its portable form (the bridge, §6) or a Codex entry below. |
| `codex-app` | One app of the seat's ChatGPT sign-in, by its connector id (`app: <id>`): `apps."<id>".enabled = true`. It is account-bound to the seat, as a connector is to a Claude account (§5): the seat's sign-in must have it connected, and placement reads it. |
| `codex-plugin` | `plugin: <name>@<marketplace>`, in the layer `plugins."<name>@<marketplace>".enabled = true`. It is installed on the seat, and its MCP servers, apps and skills come with it. |
| `codex-feature` | One of Codex's own tools that reaches outside the session's files (`feature: <name>`): `image_generation` (`features.image_generation = true`), `web_search` (`web_search = "live"`), or, once a CLI session can use them, `browser_use` or `computer_use` (`features.<name> = true`). |

**Off unless the profile names it**, in both modes (`add` and `enforce`), so a Codex position does not start with whatever the seat's sign-in carries:
- the account's apps: `features.apps = false`. When the profile names a `codex-app`, `apps` stays on, with `apps._default.enabled = false` and the named apps `true`;
- plugins: `features.plugins = false` and `remote_plugin = false`. When the profile names a `codex-plugin`, `plugins` stays on, with `plugins._default.enabled = false` and the named plugins `true`;
- browser and computer use: the six desktop controls above are `false`;
- image generation: `features.image_generation = false`;
- web search: `web_search = "disabled"`. On Codex no hook sees a hosted search, so W3 cannot guard it (RFC 0008 §4.2; `of_server`, W3); a position searches the web only when its profile names `web_search`;
- what installs or suggests tools during a session: `features.tool_suggest = false`, `recommended_plugins = false` and `skill_mcp_dependency_install = false`.

Computer use and the browser act as the person whose desktop and signed-in browser they drive. So their `codex-feature` entries carry `scope: act-as-person` and need the owner's yes on a card (§2, step 3).

**Under `enforce`**, every server in the seat's own file that the profile does not name gets `enabled = false`, as `allowedMcpServers` hides an account's own servers on Claude Code. Under `add` they stay, as an account's do.

**Not the profile's.** `of spawn` sets these from the other work items, not from the tool profile:
- Codex's sandbox and rules (W3, `of_server`) and its hooks (W2, W3);
- its subagents (`agents.enabled`, `features.multi_agent_v2`), off for now: `of_architect`'s call for W1 (message `01a1f8ea-b0ce`), until W3's canary selftest shows the managed hook refusing a subagent's call;
- its memories (`features.memories`, off by default; W5);
- its skills (W5): Codex finds them in `.agents/skills` of the working folder, `$HOME/.agents/skills` and the seat's plugins.

**Who checks.** `of tools plan --codex` prints the layer's tool tables; it and `codex_layer` are built by `of_tools` once this form is agreed. For W3's canary selftest of a Codex position, `of_tools` proposes one more check to `of_server`: list the session's tools and fail on one that the profile does not name.

## 5. Account-bound tools and moves

An OAuth tool's sign-in, and a claude.ai connector, exist only on the accounts where someone signed in.
- `of capacity plan` and `move` read the position's account-bound tools (`launch_parts` lists them; `of tools plan` prints them). A move goes only to an account where they are connected. Otherwise the move stops, and the founder gets one task per account, listing each missing sign-in, before the move rather than after.
- Such a sign-in is the founder's (or the tool keeper's) work: one task per account, filed only when a position needs that tool there.
- A tool a position depends on moves to a portable form where the service allows it: a bot or service-account token held by the broker, or the tool's own OAuth sign-in through the bridge (§6, end).

### 5a. A labelled sign-in

One account may hold a sign-in with more rights than the tool's plain entry: a person signed in to a plugin as a user who can create API keys, on an account that many positions share. Without more, every session that starts there gets it, in `add` mode or with no catalogue at all.
- **The machine labels the sign-in.** `capacity.yaml` (of_capacity's) keeps `signins: {<config dir>: {<plugin id or server url>: <label>}}`, each with its basis in a comment, for example `{~/.claude-x: {cloudflare@anthropic-plugin-directory: keys}}`.
- **The catalogue names the label.** An entry with the same plugin (or url) and `signin: <label>` is the tool for that sign-in. Positions get it like any tool; `of tools check` refuses `signin` on an entry that is not account-bound.
- **The launch.** On an account whose sign-in for plugin P (or url U) carries label L, a session whose profile names no entry for P (or U) with `signin: L` starts with `enabledPlugins: {P: false}` and `deniedMcpServers` for that url, and for the url of every catalogue entry of P. This holds in both modes, for an organization with no catalogue, and for a profile that names the plain entry. A profile that names the labelled entry keeps the plugin.
- **Fails closed.** When `capacity.yaml` is absent, cannot be read or has no `signins` key, every plugin or url that some listed catalogue labels is off for every profile that names no labelled entry for it, on every account (of_server, organic-framework PR 243: a dropped key must not hand the sign-in to every session again). An account not listed under an existing `signins` key holds no labelled sign-in.
- **Placement** (of_capacity): an entry with `signin: L` counts as signed in only on an account whose label for it is L, and a plain entry only where that plugin carries no label.
- As with `tools_excluded`, this hides the sign-in only from the sessions OF starts (`of spawn`, `of capacity move`, resume) after it; a bare `claude` a person starts on that account still has it. `capacity.yaml` is writable by every session under the owner's user, so the label is a guard against accident, not against an agent that edits it. A live session keeps what it loaded until its next start (2026-11-16, a user organization: a sign-in that could create API keys reached two dozen sessions on one account, while three positions needed it).

## 6. The plan

1. **Inventory** (done 2026-11-11): `of tools inventory`; report in the build team's repository, `reports/framework/tool-inventory-2026-11-11.md`.
2. **The schema** in `registry.md`, agreed with `of_architect`; `of tools check` inside `of registry check` (`of_platform`).
3. **The launcher**: `of_tools.launch_parts`, called by `of spawn`, `of start`, `of capacity move|resume` and the reboot restore (`of_platform`, `of_capacity`). An organization without a `tools:` catalogue gets no change until its keeper declares one.
4. **The guard** (`of_server`): an MCP call to a server outside the position's profile is denied; a credential in an MCP config file (a value in `env` or `headers`, or a key in a URL) is a finding for the tool's keeper.
5. **The lab proof** (`of_lab`): two accounts; a position with a local tool and an account-bound tool moves from one to the other; both work on both sides where signed in; a second position on the same account does not see them.
6. **The catalogues**: the OF team's, then each user organization's, through its structure keeper. Then the user-scope servers in each account's `.claude.json` are removed, first their credentials, then the servers.
7. **Codex**: the same profile, as a layer file that `of spawn` passes with `-p` (§4b).

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
