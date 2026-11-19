# Journeys: who uses OF, and the path each one walks

Status: **draft 2**, 2026-11-19, by `of_cjm`. Basis: the founder, 2026-11-19 13:55Z, prompt `01a1f417-1968`: design OF's screens "along the user's CJM"; card `OF-111`; hand-over `94a61cfe` from `of_architect`. A journey map is "a visualization of the process that a person goes through in order to accomplish a goal" (Nielsen Norman Group, "Journey Mapping 101").

This page is kept by `of_cjm`. `of_design` draws screens for the moments named here; the keepers of the systems take the gaps.

## 0. How to read this page

- **Where each reading is taken.** Every claim comes from OF's documents and code, or from the owner-key page (the owner-key matrix of 2026-11-19, published by `of_conductor`; cited below as *matrix*), or from read-only Codex runs (RFC 0008) over them. They say what a path is meant to be. They are not what a person did, so every step is a **proxy** for the person's experience. It equals the experience only where someone walked the step on a clean machine from these documents alone; no such run is cited yet, so no step is shown to hold.
- **Thoughts and feelings are "not observed".** No thread of `of_customer-success`, no Lab onboarding run and no correction of the founder is cited for any phase. The column states the expectation the matrix gives, and what to watch for; a guess is never written as a feeling.
- **A gap** is a step a person needs that no document names ("not documented"). It is a lead for the keeper of that system.
- Each step says its state: **today** (built, in the documents), **designed** (a design exists), **to build** (the matrix proposes it), **no** (impossible or unfit).

## 1. The five ways of working

| # | Way of working | Actor | Status here |
|---|---|---|---|
| 1 | One person, agents on the same computer | a solo owner | §4, short |
| 2 | Agents on a second computer, driven from a laptop; a Mac mini, a laptop, Claude Remote Control, no 1Password | a solo owner | **§2, first** |
| 3 | Agents on a Linux server, one contour per organization, driven from a Mac | the founder | **§3, first** |
| 4 | A person who reaches OF only through the Claude or ChatGPT apps | a person without a terminal | §5, short |
| 5 | A company of several people | an owner, unit heads, staff | §6, short |

Sources of the list: the matrix (rows 1 to 5); hand-over `94a61cfe`; cards `OF-109`, `OF-110`. Personas in the Lab: "the S0 solo founder, a cautious enterprise owner, a colleague joining (S2→S3)... uncalibrated until we have their traces" (docs/lab.md).

**What the owner key is today** (matrix, "How it works today"; Codex run `01a1f3d2-8cde-72ae`): one Ed25519 grant key, its public half pinned in `~/.config/of/grant_ed25519.pub`, its private half in the owner's 1Password on one Mac. Only a plain terminal on that Mac signs with it (Touch ID through 1Password's agent); the guard denies agents (docs/guard.md G13). The same key signs every grant and every passkey enrollment (`bin/of_grants.py`, `bin/of_passkey_owner.py`). Without 1Password only a plain private-key file remains, and `of passkey enroll` refuses it. The proposed scheme (`OF-109`, `OF-110`) is a list of owner keys, at least two per owner, with money, production, secrets, grants, structure and the key list signed on the approvals page by passkey or in a terminal; ordinary cards answered anywhere. It is **proposed**, not decided until the founder answers.

## 2. Journey A: a solo owner, a Mac mini, a laptop, no 1Password

**Actor.** One person; agents on a Mac mini that stays on; a laptop and a phone; a Claude subscription and the Claude app. No 1Password. **Expectation** (matrix): talk to the conductor each day through Claude Remote Control, answer ordinary cards in words, and sign consequential ones from a phone, without a terminal.

| Phase | Actions | State | Source |
|---|---|---|---|
| Hearing of OF | The README's first action is to give a coding agent the repository link. The README suggests agents on an always-on computer "reached from your laptop over Tailscale". | today | README.md |
| Setup | On the Mini: OF, herdr, Claude Code login, Tailscale (both computers, one account), keys-only SSH, `of init`; the owner logs in, trusts the folder, talks to the conductor, who starts the onboarding lead on "ready". The first owner key is made by `of init` (matrix scheme: it asks which kind, shows a QR code; the owner enrolls a passkey on the phone, the codes match on both screens). A second key on the laptop: Secure Enclave with no install (`sc_auth create-ctk-identity ... -t bio`) or Secretive. The approvals page gets a tailnet name by one `tailscale serve`. | `of init`, Claude login, SSH: today. Owner-key list at `of init`, laptop key, approvals page for grants: to build. Passkey enrollment today needs the grant key not held as a plain file. | INSTALL.md §2; docs/guide.md; docs/remote.md §6; matrix, second-computer case, steps 1–3 |
| Daily work | Remote Control to the conductor from the Claude app, browser or Desktop; ordinary cards answered in words in the chat, recorded in the owner's name. A card for money, production or a grant: the conductor sends a link; the owner opens it on the phone, sees the exact text, confirms with Face ID. In herdr the owner no longer goes. | chat and Remote Control: today. Link page for structure: today. For grants and keepers' access: to build. | docs/remote.md §§2, 5–6; matrix step 4–5; run `01a1f3d2-888b-7124` (Remote Control needs the machine awake, a Pro/Max/Team account) |
| Recovery after a lost device | Lost phone: the laptop's key removes the old passkey, a new one is enrolled; grants stay valid (matrix). Today: `of passkey revoke <person> <credential>` needs the grant key; revocation by a second passkey is "planned". Remote login: delete the device's line in `authorized_keys`; revoke the machine at of-server. | scheme: to build. Today: only with the grant key. | docs/remote.md §6; docs/remote-access.md; deploy/RUNBOOK.md |
| Growing into a company | See §6; one more colleague joins by `of invite` and `of enroll`, and the structure keeper adds their position and conductor in one diff. | today | docs/onboarding.md; docs/server-several-machines.md |

**Thoughts and feelings.** Not observed. Watch for: the first time a card needs more than a chat word; the first lost phone; the moment the owner learns the approvals page name cannot change without re-enrolling passkeys (matrix: "a passkey is bound to the site name").

**Opportunities and gaps (for `of_design`: the moments that matter).**

1. *Setup, the first key.* The owner without 1Password has no signing path today at all (matrix; `bin/of_passkey_owner.py` refuses a plain file). The first-key screen is the entry to everything after it.
2. *Setup, the approvals page name.* A worked example for the Mini's tailnet name and a check that it is reachable only from the owner's network: not documented.
3. *Daily work, which card needs what.* The card's tag, not the owner's guess, decides chat answer or link. A card that cannot be answered in chat must say why in one line and carry its link.
4. *Recovery, a solo owner with no second person.* The emergency vault assumes "at least two people" (docs/agent-secrets.md §5.6); losing every key at once: not documented. The "two keys, one an SSH key" rule of `OF-109` is the only guard, and it is not built.

## 3. Journey B: the founder, agents on a Linux server, driven from a Mac

**Actor.** An owner of several organizations; agents on a Linux server, one contour (own OS user, server and conductor window) per organization; the Mac, the phone, a key in 1Password. **Expectation** (matrix): one questions panel on the Mac for both contours; the 1Password key stays as the first key; "nothing breaks".

| Phase | Actions | State | Source |
|---|---|---|---|
| Hearing of OF | As A. | today | README.md |
| Setup | Tailscale on host, Mac and phone; keys-only SSH. A script makes the owner's admin login, organization users, credential keepers and channels. Per contour: sign-ins, trust the folder, start the conductor. Join the Mac to each contour: an invite with no `--person` (the machine is the owner's), `of enroll` in a plain terminal, a consent choice for raw records. | today; the contours are being set up; private sockets are a target | INSTALL.md; deploy/host/contours.py; docs/contours.md; docs/server-several-machines.md |
| Daily work | One questions panel on the Mac shows every organization's cards (prefixed ids); an answer goes only to that card's organization. Consequential changes: the `of diff link` page with Face ID or Touch ID; money and production access: also a grant signed in a terminal on the Mac. A stalled hand-over: `systemctl --user is-active of-relay.service`. | panel and diff page: today (panel designed, waiting for contours); approvals page per contour: to build | docs/server-several-machines.md; docs/remote.md; docs/access.md; INSTALL.md |
| Recovery after a lost device | Lost phone: delete its `authorized_keys` line; `of passkey revoke <person> <credential> --org ORG`; enroll the new phone at the machine. Lost Mac: revoke the machine at each organization's server (`of_server.py revoke --machine-id`). The scheme adds a backup key in Secure Enclave and a phone passkey, each signed by the 1Password key, entered in every organization's list. | revocation: today, one step per contour; backup keys: to build | docs/remote-access.md; deploy/RUNBOOK.md; matrix "founder's case" |
| Growing into a company | The structure keeper adds each person with their conductor; a single-use invite per person and organization; the colleague chooses whether raw records leave their machine (default no). | today | docs/onboarding-practice.md; deploy/RUNBOOK.md |

**Thoughts and feelings.** Not observed. Prompt-level signals to collect: the founder's corrections in `of_platform`'s digest about approvals and panels.

**Opportunities and gaps.**

1. *Setup.* The approvals page "over HTTPS at a stable name", Tailscale only, with proof it is unreachable from outside: not documented (docs/remote.md §6 asks for it; the matrix proposes Tailscale Serve).
2. *Setup, the owner's laptop and passkeys.* Enrolling a machine with no `--person` does not give it the server's tenant bit, which passkey enrollment requires (Codex run `01a1f3d2-8cde-72ae`, §6: `bin/of_server.py:304`, `bin/of_server_passkey.py:313`; read in the code by the run, not re-checked by this position). Whether the founder's Mac can enroll a phone against a server contour: unclear; for `of_server`.
3. *Daily work, two contours, one phone.* An approvals link belongs to one organization; the card must name it first. This is the screen `of_design` draws first for the founder.
4. *Recovery.* Removing a lost device from Tailscale and regaining owner access with the Mac and the enrolled phone both gone: not documented. A lost Mac is one revoke per contour.
5. *Agent hosts.* No owner key lives on the agents' server by design (matrix), so the owner's hands are always elsewhere; every consequential step crosses devices.

## 4. Journey C: one person, agents on the same computer (short)

Actor: the founder's own earlier case ("how you work now", matrix). Today: key in 1Password with Touch ID; approvals in the herdr questions window, the pop-up or a terminal; channel: herdr panes. To build: Secure Enclave without install (macOS), Secretive, a FIDO USB key; on Linux any SSH agent, FIDO or TPM; on Windows only through WSL2 (OF does not run natively: Unix `fcntl`, file modes and sockets; run `01a1f3d2-8cde-72ae` §5), the install not yet walked in the Lab. Journey map for this way: **not yet drawn**; needs one clean-machine run on each OS (docs/lab.md).

## 5. Journey D: a person who reaches OF only through the apps (short)

Actor: someone who does not open a terminal; phone, claude.ai, ChatGPT. **No** way around one setup by someone with a terminal: "OF and the first key are installed once in a terminal" (matrix). Today: answers in words in the conductor's chat through Remote Control. To build: a passkey in the browser; cards with buttons in the Claude conversation (an interactive connector needing a public HTTPS address on 443, which waits on card `OF-110`); notifications by ntfy or Telegram with a link; Codex tools in Codex CLI; ChatGPT only with a Mac or Windows host and its own limits. An artifact cannot approve anything important: Claude withholds "tools that require approval on every action" from artifacts (runs `01a1f3d2-888b-7124` and the matrix). Journey map: **not yet drawn**; evidence needed from a person who first meets OF in an app, none is cited.

## 6. Journey E: a company of several people (short)

Actors: the owner, unit heads, staff. Today: the key is one per organization and the owner holds it; passkeys are per person; the one who holds the role approves (owner or unit head); only the owner issues grants; each person has their own card queue (matrix, row 5). To build: each person's keys in the organization's list, their own conductor for those who work through apps. Gap: a staff member who loses a device while the owner is away: not documented. Journey map: **not yet drawn**.

## 7. What the next version needs

- Evidence for the thoughts and feelings column, in this order: `of_customer-success` threads, the founder's corrections in `of_platform`'s daily digest, the Lab's onboarding runs (`of_onboarding`, `of_lab`).
- One clean-machine run per journey, to turn a proxy step into a reading.
- The founder's answers on `OF-109` and `OF-110`, since §2 and §3 describe a proposed scheme where they say "to build".

## 8. Method

Documents read by read-only Codex runs: `01a1f433-5dc7-71a2-a3a8-a214267eb5d7` and `01a1f433-5dc7-716c-897a-fa4390be0dad` (the documents, first draft), and `01a1f3d2-8cde-72ae-911a-589b8fe4a61b`, `01a1f3d2-888b-7124-a2f5-df4b60c84380`, `01a1f3d2-888b-73e0-a87d-8b134a63a87c` (the present system, remote channels and app limits, keys per OS; run by `of_conductor`). Quotes from the documents were checked against the files; claims taken from the runs or the matrix are cited to them and not re-checked against the code unless said so.
