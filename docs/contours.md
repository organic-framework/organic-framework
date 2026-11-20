# Contours: one organization per OS user, one channel between them

Status: **design**, 2026-11-19, by `of_architect`, with `of_agent-server` (the agent host) and `of_server` (servers and the channel). Basis: the founder, 2026-11-18 21:22Z, prompt `01a1f08a-5490` in `of_conductor`'s session: "let's make a separate user for OF and a separate one for [each user organization], and also put [them] into different contours, so that communication goes through the customer success agent, for now without an outside server. This lets us divide duties and responsibilities between the systems more precisely." It supersedes, for the target state, the earlier ruling that both organizations share one server until the move.

## 1. A contour

A **contour** is everything one organization runs, closed to every other organization:
- **its own OS user** on each machine it runs on, with a home of mode 0700 that no other organization's user reads;
- **its own herdr server, services and OF install**, each listening on a Unix socket in its own home (mode 0700), never on a loopback TCP port, which every user of the machine can reach or take (`of_server`). For the listener, of-server and the relay this is the target of §4 step 3, not today's state: herdr already listens on a socket in the home, while of-server listens on `127.0.0.1:18200`, and on a TLS listener at 18201 once TLS is set up (`of server tls-init`, off by default), which is what the laptop's forward reaches. A contour holds:
  - its listener, its pulse, its of-server with its relay and its event store;
  - its own Claude Code accounts and their sign-ins, and its own Codex sign-in;
  - its credential roots and its secret store;
- **its own copy of OF's code.** The OF team runs its working copy. Any other organization runs a released version of the public repository, pinned, and updates it on its own decision, so no change of the OF team's reaches it unreviewed.

Two contours share nothing on the machine but the kernel, the keepers' root-owned programs, and the channel below. On the first agent host, the OS users are `of` for the OF team and one per user organization, named by its short name (`acme` for Acme). Keepers keep their own users, per organization, as before (docs/access.md §1).

**One origin per repository while an organization's positions run on more than one machine.** A position commits in the working copy on the machine that holds it. When one organization's positions run on two machines at once, during a move or for good, each repository they commit to has one origin. That origin is a bare repository in the organization's own contour, on the machine that stays. It is never on a person's laptop, which no agent on another machine may reach. Each working copy fetches from the origin before it commits and pushes its own commits there right after, fast-forward only. The origin refuses a push that is not a fast-forward, and any deletion. Whether positions push at all is the organization's own push rule (its `AGENTS.md`). For the OF team it is asked on the founder's card `OF-162` (2026-11-20, `of_architect`): the host's copy of the team's repository had no origin and was 80 commits behind the laptop's, so positions on the host read a stale registry, and their commits would have forked it (`of_agent-server`, message `01a1f89c-aac7`).

## 2. The one channel between contours

- **Between OF's `of_customer-success` and each organization's structure keeper `<org>_org`**, its architect. The organization may later put its own customer-success agent at its end instead.
- **What crosses:** framework matters only:
  - a bug in OF, a failed setup, a missing capability, a question about how to do something with OF;
  - OF's answers and links to documents;
  - a reproduction with the organization's names and data taken out.
- **What never crosses:**
  - the organization's work, tasks, cards, code and its review;
  - its data, credentials and secrets;
  - its incidents;
  - the OF team's own internal matters.
- **The transport, on the host, with no outside server for now:** a mailbox folder with one inbox per direction, for example `/srv/of-channel/<org>/to-of/` and `.../to-<org>/`.
  - **The folders** (`of_server`, message `01a1f08d-1f9a`). The parent `/srv/of-channel/<org>/` is root's, mode 0755, made by the setup script, so neither side can replace the other's inbox. Each inbox is owned by its sending user, with the pair's group, mode 2750: the owner writes, the other side reads, nobody else does anything.
  - **A message is one file**, `{from, to, thread, text, at}`. The sender writes it as `.<id>.tmp` at mode 0640 and renames it to `<id>.json` (a UUIDv7), so the receiver never reads half a file. The sender runs its own pre-publish check on the text first, and removes its own files after a retention.
  - **The receiver trusts the file system, not the text.** It opens each file with `O_NOFOLLOW` and takes only a regular file under a size limit, owned by the pair's other user (by `fstat`). It sets `from` to that contour, whatever the file says, and keeps a ledger of the ids it took; it can delete nothing there. Its listener takes the message in with authority none, and the receiving agent reads it as data, never as instructions.
- **Later, the same messages go through the public support API** (docs/support.md) when an outside server exists. Only the transport changes.

## 3. Today's paths between the organizations, under contours

Each path today crosses organizations directly. Under contours each needs a new shape, and the founder chooses where there is a trade-off.

| Path today | Under contours | Trade-off |
|---|---|---|
| **`of_conductor`**, the founder's one window, messages the user organization's positions directly | **Chosen: A** (the founder, card `OF-99`, 2026-11-18 21:40Z). **A.** The organization gets a conductor of its own (`<org>_conductor`, its own tokens) in its own contour. The founder has one window per organization, both shown side by side on his laptop. **B.** One window that belongs to neither organization: the founder's own conductor, held in his own contour (his user), which each organization's relay accepts as their owner's conductor. | A keeps the contours strict, and he talks to two windows. B keeps one window, and his own contour becomes a third one that reaches both. |
| **The questions panel**, showing both organizations' cards | His laptop enrolls as his machine in each organization's server and shows both queues in one panel, each card marked by its organization. His own queue is his, so nothing crosses between the organizations. | None: it reads two servers instead of one. |
| **`of_capacity` moves the user organization's sessions between accounts** | **The founder's choice** (card `OF-102`, its third option): every Claude account is signed in under both contours, one shared pool as today, knowing that the organizations' limits are not split. Each contour's balancer moves only its own sessions; the probe reads each account's own windows, so both see the account's total use. Nothing about capacity crosses between the contours: an account's threshold card to the founder comes only from the OF team's `of_capacity`, and the organization's balancer files cards about its own sessions only. | Two balancers can fill one account in the same run; the next run moves the overflow back. |
| **Structure diffs** | Unchanged: each organization's diffs are its own, applied in its contour, and he approves them on his queue. `of_architect` gives method only through the channel, when the organization's keeper asks. | None. |
| **The access map** | Unchanged: one map per organization, in its contour (docs/access.md). | None. |
| **Grants between the organizations, and cross-organization replies** | None remain once the channel exists. They are revoked after the move. | Messages between contours go only through the channel. |

**Between one organization's server cut and the channel.** A message between the two organizations lives only on its receiver's server, so a reply with no grant goes to a server that never saw the original, and is refused. This is by design, and nothing new is built around it (`of_architect`, 2026-11-20, on `of_server`'s message `01a1f8d0-9089`):
- no new grant between the organizations;
- no copy of sent messages kept on the sender's server for replies to be checked against.

Both would rebuild the direct paths that the channel replaces, and the founder chose a conductor of each organization's own (card `OF-99`). Until then:
- **An organization's matters raised in an OF session.** The OF position hands them to that organization's structure keeper, whose grant lets it answer (AGENTS.md, "The OF team organizes; each organization operates"). Once the organization's own conductor runs, the founder raises them there.
- **What an organization's position has for the founder.** It goes to its own organization's queue (`of ask`) or its conductor, never as a reply across servers.
- **Framework matters.** They go between the OF team and the organization's structure keeper, under their grants, until `of_customer-success`'s channel exists.

## 4. Order, and what waits on the founder

1. **His yes on the target state**, in one card from `of_agent-server` (`OF-44`, amended): the OS users, the contours, the channel, and his choice of window, A or B.
2. **The setup script, in his one sudo sitting** (`of_agent-server`, reviewed by `of_server`): the users and their homes, the keepers' users (layers 1 to 3 of the host plan), the channel folder and its groups, and the SSH change of `OF-38`.
3. **Each contour's services** (`of_server`): its herdr, listener, of-server with relay and store, on Unix sockets in the user's own home; `of_client` and of-server speak HTTP over such a socket. The laptop's TLS forward serves only the transition.
4. **His sign-ins, one sitting per contour:** the Claude accounts he gives it, Codex for the OF team, and the organization's secret store's service account (`of_capacity` and the organization's own keeper list what each needs).
5. **The move of the sessions** (`of capacity carry`, paths rewritten): the OF team into `of`, the organization into its own user, each from its own folders. Its credential roots go by the copy script, with its structure keeper's yes (docs/registry.md, Credentials).
6. **The channel replaces the direct paths** (§3). Grants between the organizations are revoked.

**The transition.** On the laptop, until the move, both organizations keep one server, as `of_server` ruled. The OF team moves in stages: its positions on the host reach the laptop's server through a forward until the last one has moved, and then its server moves into its own user. A user organization goes straight into its own contour, with its own server from its first session on the host and its records exported from the laptop's store. Nothing moves to the host before step 1.
