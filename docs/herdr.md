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
- **Install and remove.** `of init` runs `of_herdr_plugin.install()`. By hand, run `python3 bin/of_herdr_plugin.py install`
  with the Python that `of` runs on (the file has no `#!` line). It links the folder and writes two files into the plugin's config dir
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
- **The questions panel, on any machine** (the founder, 2026-11-18 ~18:16Z via of_conductor, msg 01a1efa8-8574: on a
  machine other than the one it was built on, nobody found how to turn it on). `of asks --panel` is a
  full-screen program: the count in each stream, the longest wait, the first card, and a button that opens the
  questions popup. It shows the open questions to the person that this machine's OF knows of, and "Nothing waits on
  you" when there are none. `of asks --watch` is a different program: the full questions screen the popup shows,
  with stream tabs across the top, a list and a card. Put in a pane, it gives no button and lays the space out
  differently (the founder, ~18:22Z: a conductor had put that one in its pane). `ps -ax -o
  pid,args | grep 'of asks --'` tells which one runs. It needs:
  - OF from 2026-11-12 or later (`of asks --help` lists `--panel`) and `of init` run on the machine. Its labels follow
    `language` in `~/.config/of/owner.yaml`, English by default.
  - A terminal pane. An agent's own shell (Claude Code's Bash tool) has no terminal, so the program cannot draw there.
    An agent opens it beside its pane without moving the person's focus: `herdr pane split "$HERDR_PANE_ID"
    --direction right --no-focus` prints the new pane's id, then `herdr pane run <that id> "of asks --panel"`.
  - For the button and `prefix+a`: OF's herdr plugin linked and the key in herdr's `config.toml`. `of init` does both
    after the person's yes; by hand the person runs `python3 bin/of_herdr_plugin.py install`, then
    `python3 bin/of_herdr_plugin.py configure`, in a plain terminal, with the Python that `of` runs on. Linking a plugin is the person's act, and the guard refuses `herdr plugin link` from an agent
    (docs/guard.md G19).

  `of init` pins it beside the person's conductor when they work in herdr, with the same yes as the plugin and the
  key. The listener opens it within a minute of the conductor starting. On a machine set up earlier, pin it beside
  the conductor yourself: `of view add <org> --workspace <the workspace the conductor is in> --tab conductor
  --positions <org>_conductor --panel questions`, then `of view apply <org>`. The workspace is the label of the one that
  holds the conductor's pane (herdr's Spaces sidebar, or `herdr workspace list`). For an organization with units it
  is the unit's (`<short>·<unit>`), not the organization's, and a view in another workspace would take the
  conductor out of its own. Name the view after the organization,
  as `of init` does: `of view add` refuses a name already in use unless given `--replace`, and `--dry-run`
  prints the view without writing it. `--panel questions` writes the
  command (the panel under `keep`, with the Python and the `bin/` that ran `of view add`) and places `panel:questions`
  after the positions. `--panel` takes only built-in panels, and `of view apply` (the listener too, each minute, as the
  person) runs only those, with the command the code gives their name: a command stored in `views.yaml` is never
  run, and a panel of another name is refused and named. A panel opens beside the view's first
  agent with a live pane, so a view whose agents are all down starts no panel (`panel:questions: no live pane; left
  out`). A view of panels alone, for a machine that holds no position (the owner's laptop under contours,
  `docs/contours.md` §3), is `of view add questions --workspace questions --tab questions --panel
  questions` with no `--positions`: `of view apply questions` starts the panel in the view's own tab, and makes the
  tab and its workspace when they are missing, without moving the person's focus (hand-over 830275c0; checked in an
  isolated lab herdr, 2026-11-19). The panel is split at the layout's share (60% stays with the agent in `main-left-60`), since herdr 0.9.5
  resizes no pane moved within its own tab. When the view's tab does not exist yet and the first agent's own tab in
  the view's workspace holds nothing but the view's panes, `of view apply` gives that tab the view's label instead of
  moving the agent to a new tab. A move would be held back while the person works in that pane, and then it would
  empty and close the tab they had been in (checked in an isolated lab herdr, 2026-11-18). Before 2026-11-18,
  `of view add` had no `--panel`. `--positions …,panel:questions` was refused as an unknown position, and the panel
  could be pinned only by writing it into `views.yaml` by hand.
- **What a plugin cannot do in herdr 0.9.5.** It cannot bind keys or set sidebar rows; those stay in herdr's
  `config.toml` (prefix+a for the questions; the red per-workspace question counter it also added until 2026-11-17
  is gone: the founder had it and the tabs' `❓N` removed, and herdr now shows a question only on its asker).
  `of_herdr_plugin.configure()` (or `python3 bin/of_herdr_plugin.py configure`), which `of init` calls beside
  `install()`, adds the key when it is missing. It is idempotent and leaves a person's own prefix+a binding alone,
  saying so. It backs the file up first, runs `herdr config check`, writes the text it read back byte for byte if
  the check fails, then reloads a running server. The popup's log (`asks-popup.err`) and the backups live in
  `of-state` beside `config.toml`, never in OF's state folder: agents' sandboxed sessions write there, and
  `configure` and the plugin's hooks run outside the sandbox, so a link planted there would make them write where it
  points (`of_agent-server`, 2026-11-22). A machine configured earlier gets the move by running `configure` again
  (`python3 bin/of_herdr_plugin.py configure`, or `of init`): it rewrites only the log path in OF's own binding. Its
  CLI cannot open a
  plugin pane as a popup (`--placement` lacks `popup`), although the socket API and the manifest can.
- **Keys and popups with a saved machine** (`herdr machine add`; read in herdr 0.9.5's source, not run: hand-over
  830275c0). One herdr window shows one server's tab at a time: the laptop's own or one saved machine's. The sidebar
  lists them all. A custom command, such as prefix+a, is the command of the server on screen, taken from its own
  `config.toml` and run there (`src/client/shell/config.rs`, `apply_snapshot_keybindings`;
  `src/client/shell/actions.rs`). With a saved machine's pane focused, prefix+a opens that machine's popup, or nothing
  when it binds no such key. A popup belongs to the server that opened it (`src/protocol/wire.rs`,
  `PaneSurfaceFrame.popup`), so the laptop's popup, from its key or a panel's button, shows only while one of the
  laptop's own workspaces is on screen. herdr 0.9.5 has no way to bind a key to the laptop's server whatever is
  focused; that would be a request upstream.
- **Copying text out of another machine's herdr** (herdr 0.9.5's source; the OSC 52 switch checked in an isolated
  lab herdr, 2026-11-20). A copy, a mouse selection or a program's own OSC 52 in a pane, is written by the herdr
  *client*, on the machine the client runs on (`src/selection.rs`, `write_osc52_bytes`).
  - **A saved machine** (`herdr machine add`) **or `herdr --remote`** runs the client on the laptop. The selection
    is read from the other machine and written on the laptop, and so is a program's OSC 52 from the machine on
    screen (`src/client/shell/actions.rs`, `SelectionCopy`; `src/client/endpoint/message_policy.rs`). The copy lands
    on the laptop's clipboard. This is the way to attach to the agent host: with `herdr --remote` from the laptop,
    copies landed there (the founder, on the agent host, 2026-11-20; `of_agent-server`, message `01a1f5f7-2380`).
  - **`ssh host`, then `herdr` there** runs the client on the host. It sends the copy to the terminal as OSC 52 only
    when `SSH_CONNECTION` or `SSH_TTY` is set, or when the host's own clipboard tools fail. Otherwise it writes the
    host's clipboard: on Linux, `wl-copy` with `WAYLAND_DISPLAY`, or `xclip` or `xsel` with `DISPLAY`
    (`src/platform/linux.rs`). `sudo -i` drops `SSH_CONNECTION` and `SSH_TTY` unless `env_keep` keeps them
    (sudoers(5)). So after `sudo -iu of herdr` the client takes itself for local, and a copy reaches the laptop only
    when the host's clipboard tools fail, for example when the host has no display. Pass the variable through:
    `sudo -iu of env SSH_TTY="$SSH_TTY" herdr`. In the lab, a client with `SSH_TTY` set sent the copy as OSC 52 and
    one without it called the clipboard tool.
  - Either way, OSC 52 reaches the laptop's clipboard only when the laptop's terminal accepts OSC 52 writes.
  - **A long line copied with the outer terminal's own selection breaks at every screen row** (herdr 0.9.5's
    source, 2026-11-20, after a user organization's sign-in URL failed this way).
    - herdr's own copy joins a pane's soft-wrapped rows: it reads the selection with unwrap on
      (`src/pane/terminal.rs`, `ghostty_extract_selection`).
    - The outer terminal sees only herdr's frame. herdr draws it run by run with cursor moves
      (`src/protocol/render_ansi.rs`, `write_cursor_position`), so the terminal's own selection ends every row
      with a line break. A browser then reads each break as a space, and the URL fails.
    - Copy with herdr's selection from a client that writes the laptop's clipboard (above), or with the program's
      own copy key. In Claude Code's sign-in screen that key is c: the line above the URL ends "(c to copy)", and
      the copy goes out as OSC 52 or through the host's clipboard tool.
    - herdr passes a program's OSC 8 links on to the outer terminal (`render_ansi.rs`), and Claude Code marks its
      sign-in URL as one. So a terminal that opens OSC 8 links can open the whole URL. Not tried.
- **Checked on 2026-11-11 in an isolated lab herdr** (its own `XDG_CONFIG_HOME`, so its own plugin registry,
  sessions and socket; its own OF config and state, with a lab organization of one position):
  - a Claude Code session started as `claude -n storefront_operator` was named within a second of detection, with
    its pane label, tokens, tab label (`operator`) and workspace label (`acme`);
  - after two server restarts, the startup hook ran each time, and the resumed session was named again by its
    session;
  - repeated runs recorded the name binding once.

  A session with no message yet cannot be resumed after a restart ("No conversation found"). A position bound only
  by its name loses that name at a restart until the keeper writes `holder.session`.

## herdr's server on Linux: a user unit

- **Why.** On Linux, herdr 0.9.5 keeps its server in the session that started it. `prepare_server_process` does
  nothing outside macOS (`src/platform/mod.rs`), and an attach that finds no server spawns one detached from the
  client but in the same session (`src/server/autodetect.rs`, `spawn_server_daemon`). A server started from an ssh
  login ends with that login's session scope, and every pane and agent ends with it. On the agent host, `of`'s
  server ran in an sshd session scope, and so did a user organization's (`of_agent-server`, msgs `01a1faa3-4174` and
  `01a1fa98-86c9`; hand-over `763bfda4`).
- **`of herdr service install`**, run as the contour's user, before anything starts herdr there (INSTALL.md, §2):
  - It writes `~/.config/systemd/user/of-herdr.service` and enables it. When no server runs, it starts it.
  - It never stops a running server, because every pane would end. A server already running outside the unit keeps
    running until it stops or the machine restarts. To move it under the unit now: `herdr server stop`, then
    `systemctl --user start of-herdr.service`. Every pane ends either way, as at a reboot, and OF's sessions come
    back as below.
  - `of herdr service status` exits 0 only when the default server runs under the unit. It finds the server by the
    process that holds the socket `herdr status server` names, so a lab server with its own XDG folders does not
    count. `uninstall` disables and removes the unit, and never stops it.
  - **Linger**, once per user, by the host's admin: `sudo loginctl enable-linger <user>`. Without it the unit starts
    only with a login and ends with the last one. The install and the status say which.
- **What the unit sets, and why:**
  - **`ExecStart=herdr server`, `ExecStop=herdr server stop`, and `OF_AUTOMATION=1`,** like every unit OF writes.
    herdr's panes inherit it from the server (`src/pane.rs`), and that is wanted. Any agent of a herdr can type into
    any of its panes (`herdr pane send-text`), so an owner-only command (`of diff approve`, `of access`) refuses in a
    herdr pane on a host. The owner runs those over plain SSH as the host's admin user, as in the setup sittings
    (`of_architect`, msg `01a1fab3-ecf5`; `of_server`, msg `01a1fab2-f750`).
  - **`SSH_AUTH_SOCK=` empty.** herdr still gives a pane the agent of a client that attached with one
    (`src/platform/ssh_agent.rs`).
  - **A fixed `PATH`:** herdr's folder, then `~/.local/bin` (where INSTALL.md puts `of` and Claude Code's installer
    puts `claude`), then `/usr/local/bin:/usr/bin:/bin`. Linux panes start a non-login shell (`src/pane.rs`,
    `shell_mode_uses_login_shell`): they read the shell's rc file and never its profile. The `PATH` does not depend
    on the shell the install runs from. So a host's setup script that writes the same text and a later install
    write the same file: one writer of one unit text (`of_architect`, msg `01a1fab3-ecf5`).
  - **Its output appends to `~/.local/log/of/herdr-server.log`,** never to OF's state folder. systemd opens that
    path itself, as the user and outside any sandbox, and agents' sandboxed sessions write in the state folder, so a
    link or a pipe planted there would act on herdr's start (`of_server`, 2026-11-22). The folder is the one
    `of_platform`'s hand-over `58681935` names for every log OF writes from outside the sandbox.
  - **`Restart=on-failure`, except on exit status 1.** herdr exits with 1 when another server holds its socket, and
    a restart could only fail again.
  - **`Slice=session.slice` and `OOMPolicy=continue`, so that one out-of-memory event does not end every agent of
    the user** (`of_agent-server`, hand-over `e8a57731`). The unit is one cgroup, and it holds every pane and agent
    of that herdr. There are two ways to lose all of them at once:
    - systemd-oomd kills a whole cgroup (`systemd-oomd.service(8)`). A desktop distribution may mark `app.slice`,
      the default slice of a user's services, for it (`ManagedOOMMemoryPressure=kill`, `ManagedOOMSwap=kill`), as
      the agent host's does. On a host that marks only `app.slice`, `session.slice` takes the unit out of oomd's
      candidates. It does not where `session.slice` or one of its ancestors is marked, as Fedora's and Debian's
      oomd defaults do (`of_review`, PR 534): under a marked ancestor, a unit left at `auto` "can still be a
      candidate" (`systemd.resource-control(5)`). `oomctl` lists what a host monitors. The slice applies at the
      unit's next start. Until then `of herdr service status` names the restart and exits 1.
    - The kernel's OOM killer takes one process. Under the default `OOMPolicy=stop`, systemd then stops the unit,
      and every other pane with it (`systemd.service(5)`). `continue` logs the kill and keeps the unit, unless that
      process is herdr's server. The server is the unit's main process, so its end is the service's end, and the
      default `KillMode=control-group` ends every pane (`systemd.kill(5)`).

    The cost, on a host that marks only `app.slice`: oomd no longer has an agent to take. When an agent runs away
    with memory, oomd may take OF's other services in `app.slice` instead (of-server, of-clickhouse, of-relay,
    of-listen), and the agent meets only the kernel's killer. With swap on zram, which is memory too, that comes
    late. The unit sets no memory bound, because its size depends on the host's memory and on how many contours
    share it. A host's admin sets one per contour user with `systemctl --user set-property of-herdr.service
    MemoryMax=<size> MemorySwapMax=<size>`. At the bound, the kernel takes the largest process in the unit, and the
    unit stays unless that is herdr's server. `MemorySwapMax` matters because, with `MemoryMax` alone, a cgroup at
    its bound keeps growing into swap. `set-property` keeps its values beside the unit, under
    `~/.config/systemd/user.control/`, and `of herdr service install` never writes there.
- **Upgrades restart it.** A live handoff (`herdr update --handoff`) does not survive under the unit. It starts the
  new server as a child and ends the old one (`src/server/handoff.rs`). systemd takes that exit for the end of the
  service and stops what is left in its cgroup. After herdr's package is upgraded, `systemctl --user restart
  of-herdr.service`.
- **A desktop is another case.** The user manager's environment may lack a desktop session's variables (`DISPLAY`,
  `WAYLAND_DISPLAY`), and a unit's panes inherit it. The unit is for a machine's contour users, who have no desktop.
- **Checked on 2026-11-21 on the agent host, as `of`,** with the unit text the install writes there, under another
  name and with its own XDG folders, so the live server was not touched:
  - the text was the same from a plain and from a login shell;
  - the server started in `user@1001.service/app.slice/<unit>` (the slice before hand-over `e8a57731`);
  - a pane had `OF_AUTOMATION=1`, `SSH_AUTH_SOCK` empty, no `SSH_CONNECTION`, `LANG`, and `PATH` with
    `~/.local/bin`, where it found `of` and `claude`;
  - `systemctl --user stop` ended it through `ExecStop`.

  `of herdr service status` on the same host found the live server in `session-161.scope`, outside any unit.
- **Checked on 2026-11-21 on a Linux Lab VM by `of_lab`,** a position that did not build it, from INSTALL.md §2
  and this section alone (hand-over `6759dc6d`; organic-framework-build report `lab-herdr-service-v0`):
  - install with no server running, then status exited 0;
  - after a reboot with nobody logged in, the server ran under the unit and status exited 0;
  - a server started from an ssh login was named as outside the unit, and the two commands the install printed
    moved it under the unit.

  Status with no login session needs `XDG_RUNTIME_DIR` to reach the user manager. A cron or `runuser` has none,
  so the command sets it to `/run/user/<uid>` when that folder exists.
- **Checked on 2026-11-22 on a Linux Lab VM by `of_lab`,** for `Slice=` and `OOMPolicy=` (hand-overs `376e51e8`
  and `e8a57731`). The agent host's `app.slice` drop-in and oomd settings were reproduced, the unit was bound to
  256 MB with no swap, and a process in one pane allocated until the kernel killed it:
  - With the old text (`app.slice`, `OOMPolicy=stop`), the kernel took that process and then herdr's server, and
    every pane ended. Why it took the server as a second victim is not established. `Restart=on-failure` started
    the server again 10 s later: a restart after a crash.
  - With the new text written by `install` over the running unit, and no restart, `OOMPolicy=continue` was in
    force from the install's daemon-reload. The next kill took only the allocating process. The unit and the other
    pane survived, still in `app.slice`.
  - After `systemctl --user restart`, the unit's cgroup was `session.slice/of-herdr.service`, and `oomctl` listed
    `app.slice` and not `session.slice`. A kill left the unit with the same main process and the other pane alive.
  - A bound set with `systemctl --user set-property` stayed under `~/.config/systemd/user.control/` across a
    second install.

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
| [herdr#5888](https://github.com/herdrdev/herdr/issues/5888): agents' start screens read as `idle`, so `agent prompt` types into them. Two Codex 0.170 screens with it: "Hooks need review" (and its review list), and "Background server has incompatible feature settings" (OF's reproduction, comment of 2026-11-20, on the founder's yes, card OF-221) | open, p2 | the relay types into a Codex position's start screen. In a lab, one letter trusted a hook and an Enter restarted Codex's background server (organic-framework-build `reports/codex-pane-states-w8-2026-11-20.md`) | a local override of herdr's codex manifest: the 2026.10.11.1 rules plus two that read those screens as `blocked`. It sits in `~/.config/herdr/agent-detection/codex.toml` on the agent host under `of` since 2026-11-20 and is loaded with `herdr server reload-agent-manifests`. The reload resets every pane's detection: a `done` pane reads `idle` again after it. The override shadows the catalogue's codex manifest, so it goes when herdr's own manifest reads these screens as `blocked`. It was checked in a lab herdr, with and without the override, on the screens saved on the host. |
| **not filed yet**: herdr's embedded libghostty-vt rejects a kitty temporary file (`t=t`) from `/tmp` on macOS (`EINVAL: temporary file not in temp dir`). Ghostty fixed it in `cd432ba5cb` (2026-10-12), and herdr 0.9.5 vendors an older copy. | candidate | none today: OF sends no temporary files. It breaks pane apps that do. | none needed |

Anything filed upstream needs the founder's yes first (brief, Limits).
