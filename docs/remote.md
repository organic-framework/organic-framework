# Remote only: a person who reaches OF through the conductor's chat alone

Status: **design**, 2026-11-11, by `of_architect`. The founder, 18:18Z, working with a new user organization: "we are again sitting through Remote Control, there is no access to herdr, so the cards, the questions, the things to do are not fully usable. Part of it we cannot do through Remote Control at all. We need to design one more use case, where the work goes entirely through Remote Control, and build it too."

## 1. The case

The person sees only one thing: a chat with their conductor, through Claude Code's Remote Control, the desktop app or the mobile app, while the conductor's session runs on another machine. They have no herdr screen, no popup, no terminal and no `!` commands. Every surface OF shows through herdr today must then reach them through that chat, or say plainly that it waits until they are at the machine.

The person's machine records `surface: remote` in `~/.config/of/owner.yaml` (beside `language`); `herdr` is the default.

## 2. Today, with what already works

- **Questions.** The conductor runs `of asks --json` and presents each open card in the person's language, with its options and the asker's recommendation. When the person answers in the chat, the conductor sends the answer to the asking agent quoting the person verbatim, with the time of their message; the asker closes the card with `of status working --answered <id>` (AGENTS.md allows an answer that reached it this way). The conductor never answers for the person.
- **States and stuck threads.** `of who`, `of handover list` and `of conductor panel --once` print text; the conductor reads them and tells the person what waits on them.
- **Structure during onboarding.** A provisional position on the person's yes in the conductor's chat (docs/registry.md, Fields, `provisional`) starts work without a diff card.
- **Agents started without herdr** run in a terminal from the lines `of init` prints. Messages to them wait in `of inbox`; until delivery without herdr exists (§3, row 5), each such agent must read `of inbox --mine` at the start of every turn, and `of_onboarding` puts that line into the package's briefs today.
- **Waits for the machine:** approving a diff (`of diff approve` runs in a plain shell on the owner's machine), signing a grant, a task that makes a secret, Claude Code's folder-trust dialog. The conductor lists them and says they wait until the person is at the machine.

## 3. What to build

| # | What | Owner | Done when |
|---|---|---|---|
| 1 | **An answer recorded from the chat.** `of asks answer <id> --option <n> | --text "<words>" --prompt <event id>`: OF records the answer only after checking that the prompt is an `of.human_prompt` with origin human, in the session that holds the person's conductor, later than the card, and that it names the card or quotes its option. The person's own words are the authority; the conductor only points to them. | `of_asks` | an answer given in the conductor's chat closes the card with its basis, and the asker sees it |
| 2 | **A diff approved from the chat**, on the same evidence: `of diff approve <id> --prompt <event id>` accepted when the prompt quotes the diff's id and says yes, for tiers up to medium. High tier and grants stay at the machine. | `of_platform` with `of_server` (authority) | a medium-tier diff approved remotely applies; a high-tier one is refused with the reason |
| 3 | **The person's queue to the conductor.** With `surface: remote`, the pulse sends the person's conductor one message when a card or a task for the person opens, when a thread of theirs is stuck, and when a position waits on them, instead of a herdr notification or the popup. | `of_platform` | the conductor tells the person within its next turn, without being asked |
| 4 | **Tasks with a place.** A task for a person's hands says where it can be done: `where: phone` (a link, an app), `browser`, or `machine` (a terminal, a key, Touch ID). The conductor presents only what the person can do from where they are, and keeps the rest for the machine. | `of_asks` | the conductor shows a `machine` task as waiting, never as steps to type on a phone |
| 5 | **Delivery without herdr.** Agents that run without herdr run in tmux; the relay delivers into a tmux pane as it does into a herdr pane (idle, empty input box, declared injection). | `of_herdr` with `of_platform` | a message reaches an agent in tmux without anyone typing it |
| 6 | **The onboarding's remote path**: `of init --surface remote`, agents started in tmux, the conductor's brief with §2's steps. | `of_onboarding` | a new organization's head runs the onboarding from the conductor's chat alone |
| 7 | **The lab proof**, `lab-remote-v0`: a person reaches only the conductor's session; a card is answered, a medium diff approved, a `machine` task shown as waiting, a message delivered to an agent in tmux, a stuck thread surfaced. | `of_lab` | the scenario passes on a clean VM |

The conductor finds the event id of the person's words with `of prompts --mine`: the recorded prompts of its own session, with their ids (`of_platform`, asked by `of_onboarding` for provisional positions). Rows 1 and 2 and provisional positions all use it.

## 4. What stays at the machine

- **Signing a grant**: the founder's key and Touch ID.
- **A task that makes a secret**: a value must never pass through a chat (AGENTS.md, tasks that make a secret).
- **Claude Code's folder-trust dialog** for a new folder.
- **A high-tier diff** (a delegation, a change of a unit's owner): the owner approves its exact text at the machine.

The conductor keeps these in one list and gives it to the person when they say they are at the machine.

## 5. How far this is safe

A remote answer rests on the person's own words, recorded by the hook in the session that holds their conductor; the conductor points to them and never states a decision of its own (docs/conductor.md, rule 4). The limit (`of_asks`, 2026-11-11): the event store is a file that the agents' operating-system user can write, so a process running as that user could add a forged prompt. Until prompts are recorded by a component the agents cannot write to (of-server under its own operating-system user, the same fix as the independent secrets review's first finding, organic-framework-build `reports/agent-secrets-review-2026-11-11.md`):
- **answers to cards from the chat** are allowed only for cards that carry no `money`, `security_incident`, `prod_change` or `irreversible` tag;
- **diff approvals from the chat** (§3, row 2) are on hold: `of diff approve --prompt` refuses, and diffs are approved at the machine.
