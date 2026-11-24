# The laptop's live checkout

On a laptop, the code that runs outside any session (every Claude Code account's hooks, among them the guard, the
listener, the relay client, of-server, the health check and the `of` on PATH) runs from a live checkout of its own,
`~/.local/share/of/live`, apart from the working copy where sessions edit and commit. No session writes it, and it
moves only to a merged pull request's commit whose checks are green, the way the agent host's live checkout moves.

Why: on 2026-11-24 a patch applied into the shared working copy with `git apply --reject` landed the hunks that use a
name and not the one that defines it. Every account's hooks ran that copy's `bin/of-hook`, so the guard failed on every
tool call of every session on the laptop, both organizations', until the owner restored the file from a plain terminal
(of_server's message `01a20c9f-4a12`; hand-over `cb134c52`).

## Set it up (the owner, once, in a plain terminal)

    of live setup     # clones public main into ~/.local/share/of/live, lists it for of protected ff, writes the agent
    of live switch    # every hooked account's hooks, the units and ~/.local/bin/of now run from it

`of live setup` adds the live checkout to `protected_code` in `~/.config/of/orgs.yaml`, in the entry of the
organization that lists the checkout OF runs from now (`--org` names another): `of protected ff` moves only a path the
owner lists there with `ff` (docs/registry.md, Protected code), here the public repository's `main`, its `unit` check
green, `bin/of_protected.py` the owner's to change. It writes only when the file then reads as before with that key
more, and leaves an entry that has `protected_code` already for the owner to extend by hand.

`of live switch` runs `of hooks install` from the live checkout for each account whose settings already run OF's
hooks, with the flags it had (`--guard` where a `PreToolUse` hook runs, `--deny-secrets` where the prompt's mention
guard does) and `--replace` for the checkout its hooks ran from, so no hook of the working copy stays beside the
live one; then `of listen install`, `of relay install`, `of server install` and `of health install` from there, for
each of those units this machine runs already, so each launchd unit names the live checkout's `bin/of`. It stops at the first command that fails and leaves
`~/.local/bin/of` as it was. Both commands refuse in an agent's session.

## How it moves

    of live request   # anyone: ask for a move to public main's tip
    of live status    # where it is, and what the of on PATH runs

A request writes `~/.local/state/of/checkout-update.request`; its content is never read. The launchd agent
`dev.organic-framework.checkout-update` watches that file and runs the live checkout's own
`deploy/laptop/of-checkout-update.sh` (the macOS form of the agent host's update): it fetches public main privately
into a store inside the checkout's `.git`, refuses when the checkout's tracked files differ from its commit or untracked
files lie in `bin/` or `deploy/` (before anything else, a restart at the tip included), lets
`of protected ff` (organic-framework PR 511) move it to a merged pull request's commit with its checks green, and
restarts of-server, the relay and the listener with `launchctl kickstart`. Its log is
`~/.local/log/of/checkout-update.log`.

The guard's G40 (docs/guard.md) denies every session a write into the live checkout and into what runs it, so the
script changes only with a reviewed commit.

The working copy stays where development happens: its tests, its commits and the release gate. A change there runs
live only after its pull request merges and someone asks for a move.
