# Remote only: a person who reaches OF through the conductor's chat alone

Status: **design**, 2026-11-11, by `of_architect`; §6 added 2026-11-12. The founder, 18:18Z, working with a new user organization: "we are again sitting through Remote Control, there is no access to herdr, so the cards, the questions, the things to do are not fully usable. Part of it we cannot do through Remote Control at all. We need to design one more use case, where the work goes entirely through Remote Control, and build it too."

## 1. The case

The person sees only one thing: a chat with their conductor, through Claude Code's Remote Control, the desktop app or the mobile app, while the conductor's session runs on another machine. They have no herdr screen, no popup, no terminal and no `!` commands. Every surface OF shows through herdr today must then reach them through that chat, or say plainly that it waits until they are at the machine.

The person's machine records `surface: remote` in `~/.config/of/owner.yaml` (beside `language`); `herdr` is the default.

## 2. Today, with what already works

- **Questions.** The conductor runs `of asks --json` and presents each open card in the person's language, with its options and the asker's recommendation. When the person answers in the chat, the conductor sends the answer to the asking agent quoting the person verbatim, with the time of their message; the asker closes the card with `of status working --answered <id>` (AGENTS.md allows an answer that reached it this way). The conductor never answers for the person.
- **States and stuck threads.** `of who`, `of handover list` and `of conductor panel --once` print text; the conductor reads them and tells the person what waits on them.
- **Structure during onboarding.** A provisional position on the person's yes in the conductor's chat (docs/registry.md, Fields, `provisional`) starts work without a diff card.
- **Agents started without herdr** run in a terminal from the lines `of init` prints. Messages to them wait in `of inbox`; until delivery without herdr exists (§3, row 5), each such agent must read `of inbox --mine` at the start of every turn, and `of_onboarding` puts that line into the package's briefs today.
- **Waits for the machine:** approving a diff (`of diff approve` runs in a plain shell on the owner's machine, until the passkey approval of §6 ships), signing a grant, a task that makes a secret, Claude Code's folder-trust dialog. The conductor lists them and says they wait until the person is at the machine.

## 3. What to build

| # | What | Owner | Done when |
|---|---|---|---|
| 1 | **An answer recorded from the chat.** `of asks answer <id> --option <n> | --text "<words>" --prompt <event id>`: OF records the answer only after checking that the prompt is an `of.human_prompt` with origin human, in the session that holds the person's conductor, later than the card, and that it names the card or quotes its option. The person's own words are the authority; the conductor only points to them. | `of_asks` | an answer given in the conductor's chat closes the card with its basis, and the asker sees it |
| 2 | **A diff approved from the chat**, on the same evidence: `of diff approve <id> --prompt <event id>` accepted when the prompt quotes the diff's id and says yes, for tiers up to medium. High tier and grants stay at the machine. | `of_platform` with `of_server` (authority) | a medium-tier diff approved remotely applies; a high-tier one is refused with the reason |
| 3 | **The person's queue to the conductor.** With `surface: remote`, the pulse sends the person's conductor one message when a card or a task for the person opens, when a thread of theirs is stuck, and when a position waits on them, instead of a herdr notification or the popup. | `of_platform` | the conductor tells the person within its next turn, without being asked |
| 4 | **Tasks with a place.** A task for a person's hands says where it can be done: `where: phone` (a link, an app), `browser`, or `machine` (a terminal, a key, Touch ID). The conductor presents only what the person can do from where they are, and keeps the rest for the machine. | `of_asks` | the conductor shows a `machine` task as waiting, never as steps to type on a phone |
| 5 | **Delivery without herdr.** Agents that run without herdr run in tmux; the relay delivers into a tmux pane as it does into a herdr pane (idle, empty input box, declared injection). | `of_herdr` with `of_platform` | a message reaches an agent in tmux without anyone typing it |
| 6 | **The onboarding's remote path**: `of init --surface remote`, agents started in tmux, the conductor's brief with §2's steps. | `of_onboarding` | a new organization's head runs the onboarding from the conductor's chat alone |
| 7 | **The lab proof**, `lab-remote-v0`: a person reaches only the conductor's session; a card is answered, a medium diff approved, a `machine` task shown as waiting, a message delivered to an agent in tmux, a stuck thread surfaced; and, once row 8 ships, a high-tier diff approved with a virtual authenticator. | `of_lab` | the scenario passes on a clean VM |
| 8 | **A diff approved from the phone with the person's passkey**, any tier (§6): enrollment once per device at the machine, the approval page that shows the diff's exact text, the signed record, and `of diff apply` checking it. | `of_server` (of-server: enrollment, page, record) with `of_platform` (`of diff link`, `of diff apply`) | a high-tier diff approved from a phone applies; a forged or stale approval is refused with the reason |

The conductor finds the event id of the person's words with `of prompts --mine`: the recorded prompts of its own session, with their ids (`of_platform`, asked by `of_onboarding` for provisional positions). Rows 1 and 2 and provisional positions all use it.

## 4. What stays at the machine

- **Signing a grant**: the founder's key and Touch ID.
- **A task that makes a secret**: a value must never pass through a chat (AGENTS.md, tasks that make a secret).
- **Claude Code's folder-trust dialog** for a new folder.
- **A high-tier diff** (a delegation, a change of a unit's owner): the owner approves its exact text at the machine, or from the phone with their passkey once §6 ships.

The conductor keeps these in one list and gives it to the person when they say they are at the machine.

## 5. How far this is safe

A remote answer rests on the person's own words, recorded by the hook in the session that holds their conductor; the conductor points to them and never states a decision of its own (docs/conductor.md, rule 4). The limit (`of_asks`, 2026-11-11): the event store is a file that the agents' operating-system user can write, so a process running as that user could add a forged prompt. Until prompts are recorded by a component the agents cannot write to (of-server under its own operating-system user, the same fix as the independent secrets review's first finding, organic-framework-build `reports/agent-secrets-review-2026-11-11.md`):
- **answers to cards from the chat** are allowed only for cards that carry no `money`, `security_incident`, `prod_change` or `irreversible` tag;
- **diff approvals from the chat** (§3, row 2) are on hold: `of diff approve --prompt` refuses, and diffs are approved at the machine.

A component that records prompts outside the agents' reach would protect what is recorded, but it would not show that a prompt is the person's. Any process of the agents' operating-system user can submit a prompt to it as the hook does. So it does not lift the hold on diff approvals from the chat. §6 replaces the chat's yes with a proof only the person can give.

## 6. Approving a diff from the phone with a passkey (design, 2026-11-12)

The founder, 2026-11-12 ~11:38Z, after a user organization's head answered yes to a high-tier diff in the conductor's chat and the yes was recorded but not applied: if OF does not give a person who works through Remote Control this feature, it must be made to work for them (`of_conductor`'s session, prompt `01a1cf8d-8f27`; its message `01a1cf8e-9146`). The person approves with a passkey on their phone (WebAuthn: Face ID or Touch ID). The private key never leaves the phone's secure hardware, so no agent on any machine can make the signature.

1. **Enrollment, once per device, with the owner at the machine.** A browser on the agents' machine can hold a software authenticator, so the enrollment page alone would let an agent enroll a key of its own. So:
   - The owner opens one enrollment window from a plain terminal: `of passkey enroll <person>`, denied to agents' sessions as `of grant` is (docs/guard.md, G13). The window accepts exactly one credential and closes after it or when it expires.
   - The person scans the window's QR code with the phone and creates the passkey there (the platform's cross-device flow). Right away the phone makes a test assertion over a fresh challenge, and its page shows a short code derived from the new credential id and its public key.
   - The terminal shows the code of-server computes from the credential and public key it received. The owner checks the two codes are the same, then signs a record of the credential id, its public key, the person and the organization with the organization's grant key (`of grant`, Touch ID).
   - If an agent's authenticator took the window first, the phone's enrollment fails or the codes differ, and nothing is signed. of-server keeps the credential id and public key under the person's id in the identity store, never in the registry.
2. **Approval.** `of diff link <id>` prints a link for the conductor to give the person. The page, served by of-server, shows the diff's exact text and digest, with an explanation in the person's language. The digest is the full SHA-256 of the diff's canonical text, 64 hex characters, never a shortened one: the agent that writes a diff controls both texts, and a 64-bit prefix lets it find two diffs with the same prefix by a birthday search of about 2^32 hashes (`of_server`, 2026-11-12). People are shown its first 16 characters. The WebAuthn challenge is the SHA-256 of a preimage: the organization, the diff id, its digest, a nonce and an expiry. The person confirms with Face ID or Touch ID. of-server records `of.diff_approved {via: passkey, credential, preimage, authenticator_data, client_data_json, signature}`, so that anyone can check the approval again from the record alone.
3. **Apply.** `of diff apply` accepts a diff of any tier with a passkey approval only when every check of an assertion in W3C WebAuthn Level 2 §7.2 passes, and the chain behind it holds:
   - `client_data_json` has type `webauthn.get`, the challenge equal to the SHA-256 of the preimage, and the origin of the organization's of-server;
   - the `rpIdHash` in `authenticator_data` is the SHA-256 of of-server's relying-party id, and the flags show the user present and user verified;
   - the signature over `authenticator_data` followed by the SHA-256 of `client_data_json` verifies with the enrolled public key, and a non-zero signature counter is higher than the last one seen;
   - the enrollment record verifies against the organization's pinned grant public key;
   - the preimage names this organization, this diff and its current digest, and has not expired.
   - the enrolled person is an approver this diff needs: the person who holds the organization's owner position, or, for a `goal_change` node under a unit owner's goal, the person who holds that unit owner's position (docs/handover.md §3);
   - the credential is not revoked (step 6).

   Anyone can run these checks again on the record, so a forged approval is visible to anyone who looks.
4. **Where the page runs.** WebAuthn binds a passkey to a domain, so the phone must reach of-server over HTTPS at a stable name.
   - An organization whose of-server runs on its own servers has that already.
   - Where of-server runs only on the agents' machine, it needs a tunnel or a private network, and exposing it is that organization's owner's decision, on a card.
   - The page and its store must be where agents cannot change them: on another machine, or under of-server's own operating-system user, with its code under `protected_code` (docs/registry.md, Credentials; guard rule G21). The store holds the enrollment windows, each link's diff text and the enrollments. An agent that could write the page's script or its store could show one text while the challenge covers another, or defeat the phone's side of the enrollment code. Each side computes that code from the credential id and the public key, so a swapped key alone is caught (`of_secrets-review`'s review of the server half, 2026-11-12).
6. **Revoking one passkey**, for a lost or replaced phone, without a grant rekey. A revocation names the credential id, the person and the organization, and is signed in one of two ways:
   - by the owner with the organization's grant key at the machine (`of passkey revoke <person> <credential>`, denied to agents' sessions as `of grant` is);
   - or by another passkey enrolled for the same person, from any device, over a challenge that names the credential to revoke (planned: it comes through the coordination feed, after the grant-key path).

   A revocation only takes a power away, so either path is enough. of-server refuses assertions from a revoked credential at once. `of diff apply` refuses every approval made with it for a diff not yet applied.
5. **What does not change.** Grants and secrets stay at the machine (§4). A yes in the chat alone still approves no diff (§5).

