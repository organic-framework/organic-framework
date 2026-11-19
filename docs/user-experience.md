# User experience: the criterion every design is scored on

Status: **in use**, 2026-11-19, by `of_design`, who keeps it. Basis: the founder, 2026-11-19 13:55Z, prompt `01a1f417-1968` (relayed by `of_conductor`, message `01a1f417-a96d`), on the owner-key design: "let's make a new user-experience parameter, and on user experience we will try to keep to simplicity, nativeness and so on." Simplicity and nativeness are his words. Reach, clarity, recovery and attention are `of_design`'s reading of the lessons named beside each, and they stay proposals until a journey in `docs/journeys.md` (`of_cjm`) or the founder confirms or changes them.

A design is a set of options for a moment in a person's journey: how they set up a key, where they answer a card, how they learn that something waits on them. This page says how each option is scored, so that two designers, or a designer and a keeper, reach the same score for the same reason.

## 1. Six measures

Each option gets 0, 1 or 2 on each measure, with its reason in one line.

| Measure | What it asks | 2 | 1 | 0 |
|---|---|---|---|---|
| **Simplicity** (founder's word) | How many steps, decisions and new things does the person meet? A step is an act of their hands: a command typed, a tap, a scan, a Face ID. A decision is a choice they make. A new thing is an app to install, an account to open or an idea to learn. | No new thing, and no more steps than the platform's own equivalent (saving a passkey on a website) | One new thing, or steps beyond the platform's own | Several new things, a step only a technical person can do, or a choice the person cannot judge |
| **Nativeness** (founder's word) | Does it use the platform's own mechanism: Touch ID, Face ID, Windows Hello, passkeys, the system's dialogs and notifications, the apps the person already uses? | The platform's own mechanism, or an app the person already uses | A well-known app the person installs for this | A mechanism of OF's own where the platform has one, or a way around the platform (a key in a plain file, a code pasted between windows) |
| **Reach** | Can the person act from the device in their hand when the moment comes? Lesson: the founder's Remote Control case, docs/remote.md §1. | Any device the person carries or sits at | Some of their devices | Only one machine, or only the agents' machine |
| **Clarity** | Does the person see what they decide, in their language, with each option's consequence, and is what they confirm exactly what they were shown? Lesson: AGENTS.md, "Asking the founder". | The exact decision on the screen where they confirm it | The decision one step away, behind a link or a retelling | They confirm what they cannot read: a digest, a code alone, a system dialog that names only a site |
| **Recovery** | When a device is lost, a name changes or a hand slips, is the way back short, and does it need nobody else? Lesson: of_cjm's journeys end in recovery after a lost device (its brief). | The person recovers alone, from another device of theirs, and nothing they approved is lost | Recovery needs the agents' machine, or a second sitting | Recovery means starting over (every grant reissued, every device enrolled again), or needs someone else |
| **Attention** | Does OF interrupt the person only for what only they can do, once, where they are now, at the strength the decision needs, and does nothing wait on them unseen? Lessons: AGENTS.md, "Nobody waits unseen", and the 83 of 252 asks withdrawn or replaced before an answer (2026-11-15, message `01a1dba9-6182`). | One notice per decision, on the channel they use now | They must go and look, or get the same notice twice | It waits unseen, or it interrupts for what an agent may decide |

A measure that does not tell a moment's options apart is shown as `—` and not counted.

## 2. Scoring a design

- **Score an option at a moment of a journey, never in general.** A terminal command scores 2 on Simplicity for a person at their own terminal and 0 for a person who has only the Claude app. The moments come from `of_cjm`'s journeys (`docs/journeys.md`). Until a journey exists, a design names the case it scores for and says it is provisional.
- **Give the counts** behind Simplicity: steps, decisions, new things. A count read from a mockup is a proxy for what the person does on a real machine. It holds once the built screen is checked on a clean machine (§4).
- **The total ranks options within one moment.** Ties go to Simplicity, then Nativeness, the two the founder named. A total never compares options of different moments.
- **Every 0 is named** in the design, with what would lift it.
- **Gates come before scores.** An option that fails a gate is shown as ruled out, with its source, and is not scored:
  - **Security.** The signing rules and their security are `of_server`'s and `of_secrets-review`'s. A design shows them and does not change them. For example, a word in a chat never approves money (docs/remote.md §5).
  - **A machine it was not built on.** A feature a person uses is done only when its command sets it up, the documents a newcomer reads name it, and a position that did not build it set it up from those documents on a clean machine (AGENTS.md).
  - **Language.** Every string a person sees goes through the locale layer (AGENTS.md, Language).

## 3. Rules that follow

Each rule names the measure it serves.

1. **One gesture per decision** (Simplicity). An important decision costs one Face ID, Touch ID or Windows Hello on a page that shows it. When the signing rules need two signatures for one decision, the design asks `of_server` for one gesture over both, or for the reason it cannot be.
2. **The decision on the confirming screen** (Clarity). A system's passkey dialog names only the site. The page above it carries the decision: its text, the amount and the limit, the expiry, the organization, in the person's language.
3. **The decision sets the strength, not the channel** (Simplicity, Clarity). An ordinary card can be answered anywhere. An important one is always the same page and the same gesture, wherever the person found its link. They learn one rule.
4. **Channels bring links; the page carries the authority** (Clarity, Reach). A chat, a notification or a connector shows the card and opens the page. It never approves anything important itself.
5. **The platform first** (Nativeness). Passkeys and biometrics over key files; the system's notifications; the person's own apps. Nothing is installed that the platform already has.
6. **Two ways in that do not fail together** (Recovery). Every owner has at least two keys that share no single failure. Two passkeys in one iCloud Keychain share one: the account. A passkey is bound to the approval page's name; an SSH key or a hardware key is not.
7. **The page's name is chosen once** (Recovery). The approval page's HTTPS name is set at setup and kept. A new name ends every passkey enrolled for the old one, and the setup says so at the moment the name is chosen.
8. **Say where it waits** (Attention, Reach). What the person cannot do from the device in their hand is shown as waiting for the device that can, never as steps to type on a phone (docs/remote.md §3, row 4).
9. **Every change of keys is told on every other key** (Recovery, Attention). A key added or removed is a notice on the person's other devices, with one tap to undo it.
10. **Nothing learned twice** (Simplicity). A card has the same words, the same order and the same marks on every surface: the popup, the questions panel, the conductor's chat, the approval page and any connector.
11. **Ask only what the person can judge** (Simplicity). Setup proposes one choice from what it detects, and puts the others behind "another way". A person is never asked to pick among key kinds they have not heard of.

## 4. From design to a built screen

1. `of_design` draws the screens for each moment of a journey and scores every option. The founder sees the design as one page, in his language, through `of_conductor`.
2. The system's keeper builds it. `of_design` hands each keeper its part with `of handover`, with the scores and the rules above as the done-when's basis.
3. `of_design` checks the built screen against its design on a clean machine or OS user (a Lab VM, docs/lab.md): the steps counted again, each difference named. Only then is it called done.

## 5. Designs scored so far

| Design | Date | Where | State |
|---|---|---|---|
| The owner's keys and approvals (cards `OF-109`, `OF-110`) | 2026-11-19 | the founder's page through `of_conductor`; the scores in §6 | provisional until `of_cjm`'s journeys for its two first cases exist |

## 6. The owner's keys and approvals: scores (2026-11-19)

The platform facts behind the scores come from a Codex research run of 2026-11-19 (run `01a1f433-c9de`, record in `of_design`'s machine state), checked against the sources it cites: W3C WebAuthn Level 3 (a passkey is bound to the RP ID, a domain; the system dialog shows the site and the account, never page text), Apple's and Microsoft's passkey guides, Tailscale's HTTPS, Serve and Services pages, Anthropic's Remote Control and connector pages, and the macOS `sc_auth(8)` and `ssh-keychain(8)` manuals. Today's OF screens come from a Codex read of the working copy on 2026-11-19 (run `01a1f434-632a`).

The options are those of `of_architect`'s matrix of 2026-11-19 (cards `OF-109`, `OF-110`). Two cases, from the brief: **A**, one person with agents on a second computer, driving it from a laptop and a phone, with no 1Password; **B**, an owner with agents on a Linux server, one contour per organization, driving it from a Mac with 1Password. The scores are for case A unless a line says otherwise. S, N, R, C, B and A stand for Simplicity, Nativeness, Reach, Clarity, Recovery and Attention.

### 6.1 The owner's key: what signs

| Option | S | N | R | C | B | A | Total | Reason |
|---|---|---|---|---|---|---|---|---|
| Passkey on the phone (synced by iCloud Keychain or Google Password Manager) | 2 | 2 | 2 | — | 1 | 2 | 9/10 | Saved like any website's passkey; one Face ID; on the phone always at hand and synced to the person's other devices. Bound to the page's name, so a renamed page ends it (rule 7). |
| Passkey in a computer's browser, Windows Hello | 2 | 2 | 1 | — | 0 | 2 | 7/10 | Native on Windows, but a passkey saved to the device stays on that computer: a lost or reinstalled computer ends it, and so does a renamed page. Edge from version 142 can save it to Microsoft Password Manager instead, which syncs. |
| SSH key in the Mac's Secure Enclave, `sc_auth`, no install | 1 | 2 | 0 | — | 1 | 2 | 6/10 | Platform hardware and Touch ID, but made with a terminal command few people know (`sc_auth create-ctk-identity … -k p-256-ne -t bio`, then `ssh-keygen -K` with `SSH_SK_PROVIDER=/usr/lib/ssh-keychain.dylib`), usable only on that Mac, and its Touch ID prompt names a system component, not OF. Not bound to a name, so it survives a renamed page: the spare key. |
| SSH key in 1Password | 1 | 1 | 1 | — | 2 | 2 | 7/10 | For case B, S 2: he already has it. For case A it is an app and a subscription. Synced across the person's 1Password devices and bound to no name. |
| USB security key (FIDO) | 0 | 1 | 1 | — | 2 | 1 | 5/10 | Bought, carried and touched; on a Mac it also needs OpenSSH from Homebrew or a provider library. The best spare kept in a drawer: bound to no name and to no device. |
| Secretive (macOS app) | 1 | 1 | 0 | — | 1 | 2 | 5/10 | An install for what `sc_auth` gives without one. |
| TPM key on Linux (`ssh-tpm-agent`) or Windows (nCryptAgent) | 0 | 1 | 0 | — | 1 | 2 | 4/10 | An agent to install and configure; usable only on that computer. |
| Windows Hello as an SSH key | ruled out | | | | | | | Signing fails (Win32-OpenSSH issues 2408 and 2040). |
| A private key in a plain file | ruled out | | | | | | | Any program of the user can read it; OF refuses to enroll a passkey with it (docs/remote.md §6). |

Reading: the passkey on the phone is the main key, everywhere. The SSH key earns its place only on Recovery, so it is the spare, never the daily key. This is the founder's "passkey for everything" for daily use, and `of_architect`'s "a name-free recovery root" (message `01a1f419-dcea`) for the day the page's name changes.

### 6.2 Answering an ordinary card

| Option | S | N | R | C | B | A | Total | Reason |
|---|---|---|---|---|---|---|---|---|
| Words in the conductor's chat, through Remote Control (works today) | 2 | 2 | 2 | 1 | — | 1 | 8/10 | The person just says it, in an app they use. The conductor retells the card, so the person reads its rendering, not the card. They learn of it only when they open the chat. |
| Buttons in Claude through the "OF approvals" connector (card `OF-110`) | 1 | 2 | 2 | 2 | — | 1 | 8/10 | The real card, tapped in the conversation on any Claude app. One connector sign-in to set up, and a public address (a security trade-off, on its own card). |
| The same connector in Claude Desktop only, as a local server | 1 | 2 | 1 | 2 | — | 1 | 7/10 | No public address: Claude Desktop runs a local MCP server itself. Not on the phone. |
| The questions panel on the laptop, over several servers | 1 | 1 | 1 | 2 | — | 2 | 7/10 | One place for every organization's cards, count in sight. Needs OF on the laptop and the laptop joined to each server. |
| The herdr popup on the agents' machine (works today) | 2 | 1 | 0 | 2 | — | 1 | 6/10 | One key at that machine; nowhere else. |
| A notification with buttons (ntfy, a Telegram bot) | 1 | 1 | 2 | 1 | — | 2 | 7/10 | Reaches the phone at once; an app or a bot to set up; the text is short. Telegram is N 2 for someone who uses it, and carries the card's text through Telegram's servers, which is `of_server`'s gate. |
| Codex CLI with OF's tools | 2 | 2 | 0 | 2 | — | 1 | 7/10 | For a person who works in Codex at their machine: no public address needed. |
| ChatGPT with OF as a custom connector | 1 | 2 | 1 | 2 | — | 1 | 7/10 | Web only, and a public address or OpenAI's tunnel. |
| The terminal (`of asks`) | 1 | 1 | 0 | 2 | — | 0 | 4/10 | Exact, but only at the machine and only when the person looks. |

Reading: the chat already scores as high as the connector. The connector lifts Clarity and costs a setup step and a public address: a second step, as `of_architect` recommends on `OF-110`, not a need.

### 6.3 Approving an important decision: money, production, secrets, grants, structure, the list of keys

| Option | S | N | R | C | B | A | Total | Reason |
|---|---|---|---|---|---|---|---|---|
| The approval page with a passkey, opened from a link on any device | 1 | 2 | 2 | 2 | 2 | 2 | 11/12 | One tap and one Face ID over the exact text. S 1, not 2: with the page in the tailnet, every device that approves needs the Tailscale app on and signed in (6.5). |
| The terminal with an SSH key (`of diff approve`, `of grant issue`) | 1 | 1 | 0 | 1 | 2 | 1 | 6/12 | For a person at their own terminal; the text scrolls past in a long diff. |
| An SSH signature from a laptop without OF: the page shows a command, the person pastes the signature back | 0 | 0 | 1 | 1 | 2 | 1 | 5/12 | Copying between a page and a terminal is the kind of workaround rule 5 rules against. Keep it for recovery only. |
| A word in the chat, or a button in a connector | ruled out | | | | | | | docs/remote.md §5; the connector only brings the link (`OF-110`). |

### 6.4 Learning that something waits

| Option | S | N | R | C | B | A | Total | Reason |
|---|---|---|---|---|---|---|---|---|
| The conductor says it in the chat (works today, `surface: remote`) | 2 | 2 | 2 | 1 | — | 1 | 8/10 | Already there. The Claude app can push it to the phone ("Push when Claude decides" in Claude Code's `/config`), but Claude decides when to push. |
| A system notification on the laptop (macOS, Linux, Windows) with one action that opens the card or the page | 1 | 2 | 1 | 1 | — | 2 | 7/10 | The platform's own; on macOS a notification that opens a link needs a small signed app, so S 1. |
| A push to the phone (ntfy, or a Telegram bot) | 1 | 1 | 2 | 1 | — | 2 | 7/10 | At once, where the person is; an app or a bot to set up. |
| The herdr notification and popup | 2 | 1 | 0 | 1 | — | 1 | 5/10 | Only at the agents' machine. |

### 6.5 The approval page's address

| Option | S | N | R | C | B | A | Total | Reason |
|---|---|---|---|---|---|---|---|---|
| A name in the tailnet (Tailscale Serve) | 1 | 1 | 1 | — | 1 | — | 4/8 | One command on the agents' machine (`tailscale serve --bg <port>`), HTTPS enabled once per tailnet, and the Tailscale app on every device that approves. A borrowed computer cannot reach it. The name follows the machine's name in the tailnet, and enabling HTTPS publishes it in the public certificate logs. |
| The organization's own domain behind Cloudflare Tunnel with an access policy | 0 | 1 | 2 | — | 2 | — | 5/8 | A domain, a tunnel and a policy to set up, once; then any device reaches it, and the name outlives a move to another machine, so passkeys outlive it too. A public address: the owner's card. |
| Tailscale Funnel | 1 | 1 | 2 | — | 1 | — | 5/8 | Public with one command; the name is still the machine's. A public address: the owner's card. |
| Case B, two contours on one host: **a name per contour** (Tailscale Services) | 1 | — | — | 2 | 1 | — | 4/6 | The system's passkey dialog names the organization's own site, so the owner always sees which organization he approves for. One passkey per organization to enroll. |
| Case B: **one name, a port per contour, one passkey for both** | 2 | — | — | 0 | 1 | — | 3/6 | A passkey is bound to the name, not the port, so one passkey would serve both pages. But the account name the dialog shows is set when the passkey is made, so it would name one organization while the owner approves for the other. |
| Case B: **one name, a port per contour, a passkey per organization** | 1 | — | — | 1 | 1 | — | 3/6 | The dialog names the same site for both; only its account line tells the organization. |

### 6.6 First setup: how the first key is chosen

| Option | S | N | R | C | B | A | Total | Reason |
|---|---|---|---|---|---|---|---|---|
| `of init` detects what is there and proposes one key, the others behind "another way" | 2 | 2 | — | 2 | — | — | 6/6 | No decision in the common case: 1Password found, use it; else the phone's passkey. |
| `of init` asks which of four key kinds | 0 | 1 | — | 1 | — | — | 2/6 | A choice the person cannot judge (rule 11). |

### 6.7 What the scores ask of the keepers

Named here as design requests. Each keeper decides within its own rules, and nothing is built before the founder's answer on `OF-109`.

- **`of_onboarding`**: `of init` sets up the page's name before the first passkey (a passkey cannot be enrolled before the name exists), proposes one key from what it detects, and puts the Tailscale app on the phone in the same sitting, with the reason.
- **`of_server`**: one gesture for a keeper's money line, which today needs a passkey approval and a separate grant (docs/access.md §4); a name per contour, for the dialog to name the organization; a notice on every other key when a key is added or removed, with one tap to undo; the approval page in the person's language, showing the decision's fields drawn from the signed text itself (who asks, the amount, the limit, the expiry), with the exact signed text one tap below. Today the page is in English only and shows the decision as JSON (`bin/of_server_passkey.py`, read 2026-11-19), which scores Clarity 1 for a person who does not read English or JSON; the list of keys names where each passkey lives (a synced passkey is on all the person's devices of that provider), not only the device it was made on.
- **`of_asks`**: the same card on every surface (rule 10), with an important card marked by what it needs ("signature on the page") and a link that opens the page; a machine task shown as waiting for the machine.
