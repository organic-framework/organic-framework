# Installing Organic Framework: a guide for the agent doing it

You are a coding agent (Claude Code, for example), and a person asked you to install Organic Framework (OF) from this repository. This page tells you what to ask, what to run, how to check each step, and where to stop for the person. The README's quickstart is the same install, written for a person doing it by hand.

**Rules for you while you install:**
- Ask before you change anything outside the new folders: the person's Claude Code settings, their shell profile, a service on their machine.
- Some steps only the person can do: logging in to Claude Code, answering Claude Code's trust dialog, and installing software that asks for their password. Stop there, say exactly what to do, and wait.
- Never ask for, read or print a password, token or key.
- Do not tell the person how long anything will take.
- Run each check below. If a check fails, stop, show the output, and say what you think is wrong.

## 1. Ask the person, in this order

1. **Their name**, as it should appear in the organization's registry (`--owner`).
2. **A short name for the organization**: lowercase letters, digits and dashes, for example `acme`. It becomes the folder `~/of/<org>` and the prefix of every position's name.
3. **The folders with the code their agents work on.** Each becomes a `--path`.
4. **Whether they have a machine that stays on**: a home server, a second computer, a mini PC. If they do, recommend section 2 before you go on. If they do not, install on the machine you are on.
5. **Which Claude Code account the agents run on**: its folder is `~/.claude` unless they set `CLAUDE_CONFIG_DIR`.

## 2. If they have a machine that stays on (recommended)

Agents keep working while the laptop sleeps or travels, and the person reaches them from the laptop or a phone. Recommend this setup, and let the person decide:
- **Install OF and herdr on that machine,** not on the laptop: run sections 3 to 7 there. The agents, the registry and of-server live there, and so does the Claude Code login the agents use.
- **Reach it over Tailscale,** a private network of the person's own devices. Install Tailscale on that machine, the laptop and the phone, signed in to the same account. Open no port to the internet.
- **SSH with keys only,** no passwords. From the laptop: `ssh <user>@<machine's Tailscale name>`, then `herdr`. herdr attaches to the session already running there, with every agent in it. `Ctrl+b, q` detaches, and the agents keep running.
- **Keep the machine awake on power.** On macOS: System Settings, Energy, prevent sleep when the display is off.

The details and a security checklist are in [`docs/remote-access.md`](docs/remote-access.md). The person installs Tailscale and turns on SSH: both need their password or a login in a browser.

## 3. Check the prerequisites

| Check | Passes when | If it fails |
|---|---|---|
| `python3 --version` | 3.11 or newer | Install a newer Python: Homebrew `brew install python` on macOS, the system's package manager on Linux. |
| `python3 -c "import yaml"` | no output | Homebrew's or a recent Linux Python refuses a plain `pip install --user`. On macOS: `python3 -m pip install --user --break-system-packages pyyaml`. On Debian or Ubuntu: `sudo apt install python3-yaml` (the person types the password). |
| `git --version` | prints a version | Install git. |
| `ssh -V` | prints a version | Install OpenSSH. |
| `claude --version` | prints a version | Install Claude Code (docs.claude.com). The person logs in: run `claude` once in a terminal and follow its login. |
| `command -v herdr` | prints a path | Recommended: `brew install herdr` on macOS; other systems: herdr.dev. Without herdr, OF records sessions and keeps the registry, but agents get no names, no desk and no `of spawn`. |

## 4. Install the `of` command

```bash
git clone https://github.com/organic-framework/organic-framework ~/organic-framework
mkdir -p ~/.local/bin && ln -s ~/organic-framework/bin/of ~/.local/bin/of
of --help
```

**Check:** `of --help` prints the list of commands. If the shell says `of: command not found`, `~/.local/bin` is not on the PATH. Ask the person before you add `export PATH="$HOME/.local/bin:$PATH"` to their shell profile (`~/.zshrc` or `~/.bashrc`), then open a new shell.

## 5. Create the organization

```bash
of init <org> --owner "<their name>" --path <folder> [--path <another folder>]
```

`of init` asks before it adds OF's recorder hooks to the Claude Code settings. If you run it without a terminal, it stops there and installs nothing. Explain the change to the person first: the hooks are added next to theirs, with a backup and a diff. Then either the person runs the command in a terminal, or you add `--yes` once they agree.

It creates the organization's repository at `~/of/<org>`. In it are the registry, with the person's conductor as the first position and the onboarding unit, and `AGENTS.md`. It starts no agent. It also starts `of-server`, the local store, as a user service.

**Check:**
- `of registry check` passes. One warning about the structure keeper's confirmation is expected: the onboarding answers it.
- `of server status` answers. If `of init` said that no user service could start (some Linux machines and containers), run `of server start`.

## 6. Trust the new folder: the person's step

Claude Code asks whether you trust a folder the first time it runs there, and warns once per account before it runs without asking permission. `of spawn` answers neither for the person. If one of them is missing, `of init` prints this line, and the person runs it in a terminal:

```bash
cd ~/of/<org> && claude --dangerously-skip-permissions
```

They choose "Yes, I trust this folder", accept the bypass-permissions warning, and type `/exit`. Agents started by `of spawn` then run tools without asking each time, and OF's guard still stops the critical ones ([`docs/guard.md`](docs/guard.md)). If the person wants agents to ask before each tool, they run plain `claude` here instead, and you add `--no-bypass` to `of spawn` below.

## 7. Start the person's conductor

With herdr:

```bash
of spawn <org>_conductor --config-dir ~/.claude
```

Use the account folder from step 1.5. Without herdr, `of init` printed two lines instead, which declare the conductor's brief and start it in a terminal. They are also in `~/of/<org>/onboarding/README.md`. The person runs them.

**Check:** `of who` lists `<org>_conductor` with a live pane, if herdr runs.

## 8. Hand over to the person

Tell them:
- From now on they talk to their conductor, `<org>_conductor`. It is their one window to every agent ([`docs/conductor.md`](docs/conductor.md)). Its first job is the onboarding: it asks them to start the onboarding lead, which drafts the organization's charter with them ([`docs/onboarding.md`](docs/onboarding.md)).
- Questions from agents come to them as cards: `of asks` lists them, and herdr's popup shows them.
- `~/of/<org>` is a git repository with their organization's structure. Keep it private: in their own git hosting, never in a public repository.
- What works today, and how far each part has come: [`FEATURES.md`](FEATURES.md). Everyday commands: [`docs/guide.md`](docs/guide.md).

Every Claude Code session is recorded from its next turn on. `of import` brings in the sessions from before.
