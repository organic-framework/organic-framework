# herdr in Organic Framework

Owner: `of_herdr`. Status: first version, 2026-11-11. So far it covers the version OF runs on, what herdr can
draw, agents without herdr (tmux), and the upstream issues OF depends on. The adapter, the full inventory of OF's calls and the contract tests
come next (brief, Mandate 1, items 2 and 3).

OF runs on [herdr](https://herdr.dev) (`herdrdev/herdr`, Apache-2.0), adopted on 2026-11-05. herdr is a terminal
workspace manager made for coding agents, and it supports many agent CLIs, not only Claude Code. It tracks each
agent's state (working, blocked, done, idle), lets agents be addressed by name, and exposes a CLI and a socket API
that agents and plugins drive. It knows terminals, not systems, roles or mandates. OF adds those on top: the
registry, pane metadata, the asks queue and the event log. So OF stays a layer over herdr, not a multiplexer of its
own, and herdr is its first runtime adapter. The full analysis is a private report of the team that builds OF.

## The version

- **herdr 0.9.5, stable channel** (`herdr channel show`), installed by Homebrew. The server and the founder's
  client run the same binary.
- herdr embeds Ghostty's terminal core, libghostty-vt `1.3.2-HEAD+44f2a44df`
  (`vendor/libghostty-vt.vendor.json`). Every pane is emulated by that copy, not by the outer terminal.
- **The founder's terminal is Ghostty 1.4.0.**
- The source OF reads is a shallow clone of the release tag in `~/.cache/of-herdr/herdr-v0.9.5`. Nothing is
  built from it on the Mac.

Upgrades go through `of_herdr`: the contract tests run on the new release first, on both the stable and preview
channels (to come).

## What can be drawn in a pane or a popup

A pane or a popup is a terminal. herdr has no HTML or web view in v0.9.5: plugin panes are terminal panes too.
That leaves two ways to draw:

1. **Text, drawn by Ghostty at its own resolution.** It is always sharp, scrolls, can be selected, and
   `herdr pane read` can read it. Panes get `COLORTERM=truecolor`.
2. **Pictures by the kitty graphics protocol.** The pane's emulator takes the image, and herdr's client re-sends
   it to the outer terminal in one of two ways:

| Path | When herdr takes it | What crosses the terminal |
|---|---|---|
| File ("direct") | Local client; `TERM_PROGRAM` ghostty, kitty or WezTerm; no SSH, tmux or screen; the terminal reports cell pixels (`CSI 16 t`) and answers mode 1016; the image is RGBA of **at most 16 MiB** | a path; the server's file under `/var/tmp/herdr-pane-graphics-<uid>/` |
| Inline | anything else, including every image over 16 MiB | the decoded RGBA as base64, uncompressed: about 5.4 bytes a pixel |

Rules for OF's screens drawn as pictures (`lab/scenarios/herdr-image-v1`):
- **Render at the pane's device pixels.** The cell size comes from `CSI 16 t`. A picture smaller than its cells
  is scaled up by Ghostty and looks soft.
- **Keep every image at most 16 MiB as RGBA**, for example 2560x1600. Cut a taller page into tiles of whole rows.
- **Scroll by placement, never by re-sending.** Re-place each tile with a source offset (`a=p` with `y=` and
  `h=`). Send pixels only when the content changes.
- **Never redraw because time passes.**
- herdr also drops any one graphics transaction over 30 MiB of encoded data (`MAX_GRAPHICS_FRAME_SIZE` minus
  `MAX_FRAME_SIZE`; herdr#3946), and a pane's emulator keeps at most 64 MiB of images.

The requests screen's picture failed on these rules (2026-11-10, conductor request R7):
- the first render was 0.6% over 16 MiB, so herdr sent 22.6 MB inline on every redraw;
- the second render was cut to 1x, and Ghostty scaled it up, so it looked soft;
- the panel did not handle scrolling.

Neither herdr nor Ghostty had a bug that blocked it. The full account is in the lab's README.

## The OF herdr plugin

The founder, 2026-11-11 (through `of_conductor`, msg `01a1cc40`): the naming that keeps the herdr sidebar readable
is part of OF's herdr plugin, so every organization gets it on install, and it applies by itself when a position
starts. The plugin is the folder `herdr-plugin/` (manifest `herdr-plugin.toml`, runner `run.sh`, hooks in
`bin/of_herdr_plugin.py`).

- **What it does.** herdr runs its hook once after the server restores its session (`[[startup]]`) and whenever it
  detects an agent in a pane (event `pane.agent_detected`). The hook waits for the session herdr reports a moment
  after detection (at most 30 s), then runs `of apply`:
  - the agent's herdr name becomes its canonical position name, and the pane label is `■ name ■`;
  - the tab is named by role and the workspace by organization, with one workspace per unit (`short·unit`);
  - the `system`, `role` and `summary` tokens are set;
  - misplaced panes are moved, unless a move would move the person's focus (`bin/of_focus.py`).

  So `of spawn`, capacity moves, resumes and a `claude` started by hand are all named without anyone running
  `of apply`.
- **Load.** `of apply` is idempotent and takes about a second. Applies are coalesced: one runs at a time, and
  everything detected meanwhile causes exactly one more run (at most five in a row). A name binding recorded by
  `of apply` has a fixed event id, so repeated runs keep it once. The log is `<state>/herdr-plugin.log`; herdr
  keeps its own (`herdr plugin log list --plugin organic-framework`).
- **Install and remove.** `of init` runs `of_herdr_plugin.install()`. By hand, run `bin/of_herdr_plugin.py install`
  with the Python that `of` runs on. It links the folder and writes two files into the plugin's config dir
  (`herdr plugin config-dir organic-framework`): `of_home` and `python`, the interpreter it ran under. It is
  idempotent, and it refuses an interpreter without PyYAML before it links anything. `herdr plugin unlink
  organic-framework` removes the plugin. herdr registers plugins for the user, in every session.
  The runner `run.sh` finds OF through `of_home`, else the folder above the plugin. It takes the first interpreter
  that imports PyYAML, in this order: `$OF_PYTHON`, the recorded `python`, `python3` on `PATH`, then
  `$OF_PY_FALLBACKS` (by default Homebrew's, `/usr/local`'s and the system's `python3`).
  On 2026-11-12 a user organization's Mac showed why the recorded interpreter comes first. OF ran from its own venv,
  there was no Homebrew, and the system `python3` had no PyYAML, so every hook failed. When none is found, the hook
  says what to run. After updating OF on such a machine, run `of init` again, or `install` as above.
  `tests/test_herdr_plugin.py` (CleanMachineTest) runs `run.sh` on such a machine.
- **The Agents sidebar: what needs you first** (the founder, ~21:25Z via of_conductor). The plugin installs a herdr
  Agents view (`agent.view.set`, label "OF: needs you first"). First come the agents with an open question to the
  owner (the `ask` pane token), then herdr's attention (a dialog or permission, a finished turn not yet seen,
  working, idle and seen), then the layout order. Every agent stays listed. herdr keeps a view only until its
  server exits, so the startup hook sets it again; `python3 bin/of_herdr_plugin.py view` sets it by hand. herdr
  refuses the view (`plugin_not_found`) when the plugin is not linked.
- **The questions screen as a popup.** The pane entrypoint `asks` (placement popup, 90% x 85%) runs `of asks
  --watch`. Any process, a key or a panel's button opens it over everything with `herdr plugin pane open --plugin
  organic-framework --entrypoint asks`. Leave out `--placement`, so the manifest's popup applies; `--env
  OF_ASKS_OF=<bin/of>` runs a trial build. herdr answers `ui_busy` while another popup, Settings or Copy mode is
  open. A click in a pane reaches its program as a mouse report when the program turned mouse reporting on, also
  when the pane is not focused: the first click lands (checked in `lab/scenarios/asks-screen-v0`, `click.script`).
- **A pinned panel that survives.** `of_herdr_plugin.py keep [--name NAME] -- COMMAND` runs a pinned pane's program
  again whenever it exits. It waits 2 s, doubling up to 60 s while the program keeps failing, back to 2 s after a
  run over a minute, and says so in the pane (in the owner's language); Ctrl-C stops it. The founder's questions
  panel runs under it since it died at 21:23Z on 2026-11-11, when the build it ran from was rebased. A popup's
  command must not use it: the popup would never close.
- **What a plugin cannot do in herdr 0.9.5.** It cannot bind keys or set sidebar rows; those stay in herdr's
  `config.toml` (prefix+a for the questions, the red per-workspace question counter).
  `of_herdr_plugin.configure()` (or `python3 bin/of_herdr_plugin.py configure`), which `of init` calls beside
  `install()`, adds both when they are missing. It is idempotent and leaves a person's own prefix+a binding and
  sidebar rows alone, saying so. It backs the file up first, runs `herdr config check` and puts the backup back if
  the check fails, then reloads a running server. Its CLI cannot open a
  plugin pane as a popup (`--placement` lacks `popup`), although the socket API and the manifest can.
- **Checked on 2026-11-11 in an isolated lab herdr** (its own `XDG_CONFIG_HOME`, so its own plugin registry,
  sessions and socket; its own OF config and state, with a lab organization of one position):
  - a Claude Code session started as `claude -n storefront_operator` was named within a second of detection, with
    its pane label, tokens, tab label (`operator`) and workspace label (`acme`);
  - after two server restarts, the startup hook ran each time, and the resumed session was named again by its
    session;
  - repeated runs recorded the name binding once.

  A session with no message yet cannot be resumed after a restart ("No conversation found"). A position bound only
  by its name loses that name at a restart until the keeper writes `holder.session`.

## Resuming OF's sessions after a herdr restart

herdr resumes an agent after its server restarts by typing a command into the pane's new shell. For Claude Code its
own command is a bare `claude --resume <id>`: no account, model, effort or permission mode. On 2026-11-10, 38 sessions
came back on one spent account that way. A shell function (`bin/of-shell.zsh`) used to rewrite it; OF no longer
needs one (docs/accounts.md A4, hand-over a9c118e8 from `of_capacity`).

- **How.** OF's Claude Code hook reports its own resume command with every state report (`pane.report_agent`,
  `resume_argv`; `resume_argv()` in `bin/of_hook.py`). herdr keeps a reported resume command with the pane, across
  restarts too, and prefers it to its own (herdr 0.9.5, `src/persist/restore.rs`). The command is
  `env <the hook's Python> <OF>/bin/of claude --resume <id>`, and `of claude --resume` runs the `of resume-command`
  line: the account that holds the session, its model, effort and permission mode (`bin/of_accounts.py`).
- **Why `env` and full paths.** herdr takes a plain command name as the first word and types the command into a new
  login shell. On macOS, `path_helper` puts `/usr/bin` first there, and `/usr/bin/python3` has no PyYAML. The lab
  first saw `of: PyYAML is missing`, then a resume on the wrong account until `of_capacity` ran `bin/of` under the same
  Python too (a3c44e9).
- **When it is not reported.** No session (a subagent's call), a side chat, or a path herdr would refuse (an
  apostrophe, a control character). herdr then uses its own command.
- **Checked on 2026-11-11** in a lab herdr whose panes ran a login `/bin/sh` (no OF shell function) with no
  `CLAUDE_CONFIG_DIR` in the pane. A Claude Code session was started on one account for its own process only, as
  `of spawn` does, and given one message; its hook reported the command. After a server restart, herdr typed it and
  the session came back with its conversation (its transcript exists only on that account) and its model
  (`claude --resume <id> --model claude-haiku-5-5`). Tests: `tests/test_herdr_plugin.py` `ResumeTest`.
- A session that never got a message cannot be resumed by any command ("No conversation found").

## Agents without herdr: tmux

A person who reaches OF only through the conductor's chat ([`remote.md`](remote.md)) runs the other agents in tmux.
OF delivers to them as it does to a herdr pane (`bin/of_tmux.py`, owner `of_herdr`):

- **How an agent is seen.** OF's Claude Code hook reports the agent's state for its tmux pane where it reports
  state to herdr: idle at session start, working when a prompt is submitted, blocked on a permission request or a
  question, idle when the turn ends, forgotten at session end. One small file per pane lives under
  `~/.local/state/of/tmux-panes/`. At session start the hook also notes the Claude Code process, so a pane whose
  agent has exited (a shell prompt again) is never typed into.
- **Its pane id** is `tmux:<socket name>:%<n>`, for example `tmux:default:%3`. `of who`, the relay and routed asks
  list such agents next to herdr's and bind them to positions by session. `of apply`, `of go`, views and the desk
  change herdr's panes only and leave tmux agents alone.
- **Delivery** follows herdr's rules: only when the reported state is idle (or working, for a message waiting over
  five minutes; never blocked), only into an empty input box (a dim suggestion counts as empty), never into a
  dialog, declared as an injection first. The text goes in as one bracketed paste through a tmux buffer, then
  Enter after 300 ms, as herdr 0.9.5's `agent prompt` does. Nothing selects a pane or moves a person's focus.
- **What it needs on the machine:** tmux, OF's hooks in the Claude Code settings (`of hooks install`), and the
  relay (`of relay run`). An agent started before the hook knew tmux appears at its next turn.
- **Limits.** tmux has no agent state of its own, so the hooks are the only source. A turn interrupted with Esc
  fires no Stop hook, so the pane reads working until its next turn ends. A tmux agent has no herdr name, label or
  sidebar.

Checked on 2026-11-11 with tmux 3.8 and Claude Code 2.1.322 in a private tmux server: the relay's delivery reached
an idle session, which answered it; the session recorded the text as injected by the relay; `of who` showed the
agent's state; a message to a box holding text was deferred; the report was removed when the session exited.
Tests: `tests/test_tmux.py`.

## Upstream issues OF depends on

| Issue | State | What it does to OF | Workaround in OF |
|---|---|---|---|
| [herdr#5656](https://github.com/herdrdev/herdr/issues/5656): after one placement change while a file transfer is in flight, every later image goes inline until the client reattaches (macOS, Ghostty) | open, p2 | A scrolling picture screen could switch the founder's client to inline images, so every pane lags | none yet; any picture screen is built with this risk named |
| [herdr#5824](https://github.com/herdrdev/herdr/issues/5824): keep PNG or compress (`o=z`) inline images | closed, not planned | Inline images stay about 5.4 bytes a pixel | keep images inside the file path's 16 MiB |
| [herdr#3946](https://github.com/herdrdev/herdr/issues/3946): images over the 32 MiB frame cap are dropped; some small ones are not painted | open, p2 | a large picture can vanish without an error | keep images inside 16 MiB |
| [herdr#5783](https://github.com/herdrdev/herdr/issues/5783): redrawn images keep GPU memory in herdr-app until restart | open, p1, Linux | not hit on macOS so far; redraw rarely anyway | redraw on content change only |
| [herdr#5594](https://github.com/herdrdev/herdr/issues/5594): plugin panes do not answer `CSI 16 t` and report 0x0 pixels | open | a plugin pane cannot size a sharp picture | draw pictures in normal panes |
| **not filed yet**: herdr's embedded libghostty-vt rejects a kitty temporary file (`t=t`) from `/tmp` on macOS (`EINVAL: temporary file not in temp dir`). Ghostty fixed it in `cd432ba5cb` (2026-10-12), and herdr 0.9.5 vendors an older copy. | candidate | none today: OF sends no temporary files. It breaks pane apps that do. | none needed |

Anything filed upstream needs the founder's yes first (brief, Limits).
