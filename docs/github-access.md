# GitHub from the agent host's sandbox, and where agents' credentials go next

Status: a design for the owner's card. Nothing here is built. Written by `of_server` for hand-over `4c7b6219`
(2026-11-22), at the founder's question: "we wanted to design the access system with the sandbox and so on, where is it
now?" (prompt `01a20450-ff3e`), and at his second: "How could a first version of such a system for gh look, given our
work, so as to go in its direction?" (prompt `01a2045f-a7d2`). Frame: [`access.md`](access.md) (keepers),
[`agent-secrets.md`](agent-secrets.md) (the 1Password-backed broker), [`guard.md`](guard.md) G18 and Gaps.

## 1. Today

- Every agent's `git` and `gh` act as the owner's GitHub account, and only the guard tells positions apart
  ([`guard.md`](guard.md) G18). A verdict's `Position:` line is text the reviewer writes.
- On the agent host a push goes through a unit that holds the keys: a session hands it a bundle and a request, and
  the unit checks the push and makes it.
- The host has no `gh` sign-in. The reviewers (`of_review`, `of_secrets-review`) post their verdicts as pull request
  comments, which they cannot do from there.
- Reading the public repository needs no token. But GitHub limits unauthenticated API reads per address, and every
  session on the host shares that one limit.

## 2. What a session must never hold

A session must never hold a token that can write. Where sessions share one OS user, any file that user owns is one gap
in the guard or the sandbox away from a session; only another OS user is a wall the kernel keeps
([`access.md`](access.md) §1).

## 3. The options for `gh`

- **A. A posting unit (recommended).** The token is held by a unit outside the sandbox, as its own OS user. A session
  hands it the text and gets the result back, and never sees the token. The unit can check the text before it is
  published.
- **B. A scoped token the session reads.** This is the simplest option. But the session can do whatever the token
  allows, with no check on the text, and it can publish the token itself, since the sandbox's network allowlist keeps
  GitHub open. Rejected.
- **C. The reviewers stay on the laptop.** This works today. But it keeps two positions off the host, outside any
  sandbox, against "first take the agents off this Mac" (card `379274c4`). It is a fallback only.
- **D. A credential-injecting egress proxy** (the isolation plan's layer 4). The session never sees the token. But the
  proxy adds it to every request it lets through, so it cannot tell a comment from any other write, and it cannot
  check the text. It is the right tool when several HTTP services need credentials, which is not the case yet. Nothing
  here claims that Claude Code's sandbox proxy, or Strands Box (a preview whose trial `of_lab` holds under hand-over
  `53ea8f33`), can inject a header; that has not been checked.
- **E. The broker's `of secret run`** ([`agent-secrets.md`](agent-secrets.md)). It puts the value into the holder's
  command, so the holder can print it (card `379274c4`, second flaw). Its place here is behind A, as the store A's
  token comes from (§5).

## 4. The first version: a posting unit

- **Who runs it.** A root-owned program, `/usr/local/libexec/of-gh-post`, run by root's units as its own OS user,
  `of-gh`. That user's home, mode 700, holds the token.
- **The door.** A session writes a request, then touches a signal file, and a path unit starts the service. The request
  names the repository, the pull request, the kind and a body file, and lies in a folder under OF's state folder. The
  service reads it as data: no link, regular files only, a size bound. It writes the result where the session reads
  it, in a folder only the service writes.
- **What it allows.** Two actions only: a comment on a pull request, and a review whose event is a comment (never an
  approval, which branch protection could count). Both only on OF's repositories, named in the program and never in
  the registry, since the registry follows agents' pushes (card `OF-266`). It refuses everything else.
- **The gate.** The body is publication, so it passes the export gate's checks. The unit runs them from a copy that the
  owner's sitting installs root-owned, pinned by its sha256. It never runs them from organic-framework-build's
  `release/`, which follows pushes.
- **Attribution.** The body is posted as written, followed by the id of the session that wrote the request. This
  attributes the text and does not authenticate it: every position is `of`, so a request names its position as text,
  as a verdict does today (card `379274c4`, third flaw). The unit does not make this worse, and it does not fix it.
- **Reads.** Reads take no token. A diff comes by fetching the pull request's refs over HTTPS, which the API limit
  does not count, and the rest comes through `gh`'s unauthenticated reads. If the shared limit bites, sessions get a
  second token that can only read public data: leaked, it gives away nothing but a rate limit.
- **The guard.** G18 keeps denying `gh`'s writes from a session, so the unit is the only writer.

**Whose name posts.**
- The owner's account, with a fine-grained token for that one repository, limited to what comments and reviews need.
  It takes one step in his browser and nothing new on GitHub. Every agent's comment still shows as his.
- A GitHub App installed on the repository. Comments then show as the app's bot, apart from the owner. The tokens it
  mints are short-lived, and the unit holds the app's private key instead of a token. It takes him more steps: create
  the app, install it, keep its key.
- Recommendation: start with the owner's token. Move to the App when agents' words and his should look apart on
  GitHub, or when the broker keeps the key.

## 5. Where the broker lives, and the order of the move

- **Where.** One OS user per organization holds its agents' credentials, outside the sandbox, with root-owned code. It
  is reached through request files, as the push unit is. A socket would be no stronger a door: the Linux sandbox opens
  every Unix socket or none ([`guard.md`](guard.md), Gaps). For now it is built only as single-purpose units such as
  §4's, never as a general keeper. The founder, on keepers serving from their lists ([`access.md`](access.md) step 4):
  "Don't build it for now, we'd only make it more complex" (prompt `01a2045f-a7d2`).
- **The order.**
  1. The posting unit, with its token in a file in its own user's home.
  2. That token moves into 1Password: OF's vault, read by the unit's user with a service account of its own
     ([`agent-secrets.md`](agent-secrets.md)). This is the broker's first use, and it runs as a user apart from the
     agents, which is card `379274c4`'s first fix.
  3. OF's other credentials on the host, as they come, each moved as a unit that acts, never as a value handed into a
     session.
  4. A user organization's positions, in its own contour, by its structure keeper.
- **Card `379274c4`.** The founder deferred this move on 2026-11-13: "first we need to move to the second Mac as the
  server and take the agents off this Mac, and then we'll see." That condition is being met now that OF's sessions run
  on the host. Steps 2 to 4 still wait for his yes.

## 6. The owner's hands, once

- In his browser: create the fine-grained token (the repository, and only the permissions comments and reviews need).
- In a plain terminal, in one sitting: create `of-gh`, install the program and its units, and read the token silently
  into its file. Never with `!` in an agent's session, and never pasted into a chat.

## 7. Open

- **Caller identity (above).** Telling positions apart needs either one OS user per position or an identity no session
  can forge. Neither is designed here.
- **The gate's copy** changes only in the owner's sitting, so the posting unit runs the gate as it stood at the last
  sitting.
