# Installing Organic Framework: a guide for the agent doing it

You are a coding agent (Claude Code, for example), and a person asked you to install Organic Framework (OF) from this repository. This page tells you what to ask, what to run, how to check each step, and where to stop for the person. The README's quickstart is the same install, written for a person doing it by hand.

**First of all: do it yourself, and explain it simply** (the founder, 2026-11-11: "in setup, let the agent do as much as it can itself rather than pull the person in, and explain everything very plainly"). Do everything you can yourself, and ask the person only for what truly needs them: a login, a password prompt, Claude Code's trust dialog, a decision. At each step, say in plain words what you are doing and why, so a person with no technical background follows it: no command names, ids or jargon unless they ask.

**Rules for you while you install:**
- **The person may be remote.** They may drive you through Claude's Remote Control, the desktop or the mobile app, or over SSH from another computer, and a command typed into such a chat does not run. Never ask them to type or paste a command: run every command yourself.
- Ask before you change anything outside the new folders: the person's Claude Code settings, their shell profile, a service on their machine.
- Some steps only the person can do: logging in to Claude Code, answering Claude Code's trust dialog, and installing software that asks for their password. Stop there, say in words what to do and where (which machine, which app, what to choose there), and wait. Show a command only to a person who says they sit at a terminal on that machine, in front of it or in an SSH session to it, and only for that terminal.
- Never ask for, read or print a password, token or key.
- Do not tell the person how long anything will take.
- Run each check below. If a check fails, stop, show the output, and say what you think is wrong.

## 1. Ask the person, in this order

1. **Their name**, as it should appear in the organization's registry (`--owner`).
2. **A short name for the organization**: lowercase letters, digits and dashes, for example `acme`. It becomes the folder `~/of/<org>` and the prefix of every position's name.
3. **The folders with the code their agents work on.** Each becomes a `--path`.
4. **Whether they have a machine that stays on**: a home server, a second computer, a mini PC. If they do, recommend section 2 before you go on. If they do not, install on the machine you are on.
5. **Which Claude Code account the agents run on**: its folder is `~/.claude` unless they set `CLAUDE_CONFIG_DIR`.
6. **The language their agents should use with them** (`--language`, a code or a name: `en`, `ru`, …). Usually it is the one they talk to you in.
7. **How they will reach their agents**: at the machine where OF runs, in herdr (the default), or only through the chat with their conductor, from another computer or a phone: Claude's Remote Control, the desktop or the mobile app. For the chat alone, use `--surface remote` at step 5 ([`docs/remote.md`](docs/remote.md)).

## 2. If they have a machine that stays on (recommended)

Agents keep working while the laptop sleeps or travels, and the person reaches them from the laptop or a phone. Recommend this setup, and let the person decide:
- **Install OF and herdr on that machine,** not on the laptop: run sections 3 to 7 there. The agents, the registry and of-server live there, and so does the Claude Code login the agents use.
- **Reach it over Tailscale,** a private network of the person's own devices. Install Tailscale on that machine, the laptop and the phone, signed in to the same account. Open no port to the internet.
- **SSH with keys only,** no passwords. From the laptop: `ssh <user>@<machine's Tailscale name>`, then `herdr`. herdr attaches to the session already running there, with every agent in it. `Ctrl+b, q` detaches, and the agents keep running.
- **Or the chat alone.** If the person will talk only to their conductor, from the Claude app, install with `--surface remote` (step 1.7): the conductor starts with Remote Control on, and every agent runs in tmux on that machine.
- **Keep the machine awake on power.** On macOS: System Settings, Energy, prevent sleep when the display is off.

The details and a security checklist are in [`docs/remote-access.md`](docs/remote-access.md). The person installs Tailscale and turns on SSH: both need their password or a login in a browser.

## 3. Check the prerequisites

| Check | Passes when | If it fails |
|---|---|---|
| `python3 --version` | 3.11 or newer | Install a newer Python: Homebrew `brew install python` on macOS, the system's package manager on Linux. |
| `python3 -c "import yaml"` | no output | Homebrew's or a recent Linux Python refuses a plain `pip install --user`. Run `python3 -m pip install --user --break-system-packages pyyaml`. On Linux without pip (`python3 -m pip --version` fails), the system package (`python3-yaml` on Debian or Ubuntu) needs the person's password: their step, on this machine. |
| `git --version` | prints a version | Install git. |
| `ssh -V` | prints a version | Install OpenSSH. |
| `claude --version` | prints a version | Install Claude Code (docs.claude.com). Logging in is the person's step: on this machine, they start Claude Code in a terminal and follow its login. If you run in Claude Code on this machine, on the account from step 1.5, the login is done. |
| `command -v herdr` | prints a path | Recommended: `brew install herdr` on macOS; other systems: herdr.dev. Without herdr, OF records sessions and keeps the registry, but agents get no names, no desk and no `of spawn`. |
| `command -v tmux` | prints a path | Needed with `--surface remote`, and used without herdr: `brew install tmux` on macOS, the package manager on Linux. Without it, agents without herdr start in `screen`. |

## 4. Install the `of` command

```bash
git clone https://github.com/organic-framework/organic-framework ~/organic-framework
mkdir -p ~/.local/bin && ln -s ~/organic-framework/bin/of ~/.local/bin/of
of --help
```

**Check:** `of --help` prints the list of commands. If the shell says `of: command not found`, `~/.local/bin` is not on the PATH. Ask the person before you add `export PATH="$HOME/.local/bin:$PATH"` to their shell profile (`~/.zshrc` or `~/.bashrc`), then open a new shell.

## 5. Create the organization

```bash
of init <org> --owner "<their name>" --language <code> --path <folder> [--path <another folder>] [--surface remote]
```

The language is kept in `~/.config/of/owner.yaml`; without `--language`, `of init` asks in a terminal, or uses English. A new organization's agents start on Opus at effort `xhigh`: `of init` writes `agents: {model: opus, effort: xhigh}` on the organization's entry in `~/.config/of/orgs.yaml`, and `of spawn` reads it. If the person wants another model or effort, add `--model` and `--effort`.

`of init` asks before it adds OF's recorder hooks to the Claude Code settings. If you run it without a terminal, it stops there and installs nothing. Explain the change to the person first, in words: the hooks are added next to theirs, with a backup and a diff. When they agree, run it with `--yes`.

It creates the organization's repository at `~/of/<org>`. In it are the registry, with the person's conductor as the first position and the onboarding unit, and `AGENTS.md`. It starts no agent. It also starts three user services: `of-server`, the local store; the relay, which delivers messages between agents; and the listener, whose pulse offers hand-overs and nudges agents that wait.

**Check:**
- `of init` printed `relay: running` and `listen: running`. If one says `NOT running`, `of init` exits with code 2: show the person, in plain words, that agents cannot pass messages (the conductor's first message to the onboarding lead would never arrive), read the log it names, and run the command it gives to see why.
- `of registry check` passes. One warning about the structure keeper's confirmation is expected: the onboarding answers it.
- `of server status` answers. If `of init` said that no user service could start (some Linux machines and containers), run `of server start`.

## 6. Trust the new folder: the person's step

Claude Code asks whether you trust a folder the first time it runs there, and warns once per account before it runs without asking permission. No agent can answer either for the person, and `of spawn` stops without them. If one of them is missing, `of init` says so.

Tell the person in words what to do and where, then wait: on this machine (in front of it, or in an SSH session to it from another computer), open a terminal, start Claude Code in the folder `~/of/<org>` in bypass mode, choose "Yes, I trust this folder", accept the bypass-permissions warning, and leave it with `/exit`. The line for that terminal, for a person who sits at it and never to be pasted into your chat:

```bash
cd ~/of/<org> && claude --dangerously-skip-permissions
```

Agents started by `of spawn` then run tools without asking each time, and OF's guard still stops the critical ones ([`docs/guard.md`](docs/guard.md)). If the person wants agents to ask before each tool, they start plain `claude` there instead, and you add `--no-bypass` to `of spawn` below.

## 7. Start the person's conductor

With herdr:

```bash
of spawn <org>_conductor --config-dir ~/.claude
```

Use the account folder from step 1.5. With `--surface remote`, or without herdr, `of init` printed two lines instead: the first declares the conductor's brief, the second starts it. They are also in `~/of/<org>/onboarding/README.md`. You run them:
- **With `--surface remote`**, run both as printed: the second already starts the conductor in tmux with Remote Control on. The person then opens it from the Claude app and talks to it there.
- **Without herdr**, run the first as it is and the second detached, with the line quoted for the shell: `tmux new-session -d -s <org>_conductor '<the second line>'`, or where there is no tmux, `screen -U -dmS <org>_conductor sh -c '<the second line>'` (macOS ships `screen`). If the person sits at a terminal on this machine, they may run the second line there instead.

**Check:** with herdr, `of who` lists `<org>_conductor` with a live pane. Otherwise `tmux ls` (or `screen -ls`) lists `<org>_conductor`; on this machine, `tmux attach -t <org>_conductor` (or `screen -r <org>_conductor`) shows it.

**Check that messages between agents can travel.** The conductor's first message goes to the onboarding lead, and only the relay delivers it. The relay's service runs when `launchctl list dev.organic-framework.relay` shows a `PID` (macOS) or `systemctl --user is-active of-relay.service` says `active` (Linux). Later, `of status` in the conductor's session may print `1 message you sent is not delivered yet: to <org>_onboarding (8m; batched, next in 2m)`. `batched` is normal: the relay types at most one prompt per receiver per 15 minutes. `no live holder` means the lead is not running. When no relay runs on this machine, `of status` and `of send` say so: `no relay runs on this machine: …`. `of relay on` then installs the service if it is missing and starts it; check it again as above.

## 8. Hand over to the person

Tell them:
- From now on they talk to their conductor, `<org>_conductor`. It is their one window to every agent ([`docs/conductor.md`](docs/conductor.md)). Its first job is the onboarding: it asks them in words to say when they are ready to start it, then starts the onboarding lead itself, which drafts the organization's charter with them ([`docs/onboarding.md`](docs/onboarding.md)).
- Questions from agents come to them as cards: their conductor brings them, herdr's popup shows them, and `of asks` lists them in a terminal on this machine.
- Colleagues join the same structure as people holding positions, beside the agents ([`docs/registry.md`](docs/registry.md), People); their machines join with `of invite` and `of enroll`.
- `~/of/<org>` is a git repository with their organization's structure. Keep it private: in their own git hosting, never in a public repository.
- What works today, and how far each part has come: [`FEATURES.md`](FEATURES.md). Everyday commands: [`docs/guide.md`](docs/guide.md).

Every Claude Code session is recorded from its next turn on. `of import` brings in the sessions from before.
