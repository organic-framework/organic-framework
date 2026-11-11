# The conductor: every person's one window

Status: **decided**, 2026-11-11, by `of_architect`, hand-over `e404efbd` from `of_conductor`. The founder chose where the conductor lives: option A, a position in the person's home organization with grants into the others (ask `b7cb0925`, §4).

The founder, 2026-11-11 10:02Z: "теперь обязательно для всех, кто начинает пользоваться нашим фреймворком, вообще для всех, кто пользуется, через дирижер работать ... чтобы у него всегда было одно окно".

## 1. Why

- **A person who steers many agents types into many panes.** The founder typed into 7 to 26 different positions' panes a day on 2026-11-06..09.
- **Each pane costs his context and theirs.** A request typed into the wrong pane reaches the wrong owner, and nothing follows it to a result.
- **The OF team's conductor** (`of_conductor`, since 2026-11-10, card 3d4d31cc) took 41 of his 118 typed prompts on its first day. On 2026-11-11 up to 10:05Z it took 4 of 8, which went to 4 panes in all.
  - Source: `of.human_prompt` events with origin `human` in the event store.
- **Every person in an organization on OF has the same problem at a smaller scale.** That includes colleagues who join a company one by one, and the 80 people of a company with nothing centralized.

## 2. Source practice

- **A single window** (UN/CEFACT, Recommendation No. 33, "Establishing a Single Window", 2005). A party lodges everything once, at one entry point, and the entry point routes it to the agencies that own it.
- **A single point of contact** (ITIL 4, service desk practice, Axelos 2019). Users have one place to go, and the desk keeps each request until it is resolved, whoever resolves it.
- **The chief of staff** (Dan Ciampa, "The Case for a Chief of Staff", Harvard Business Review, May 2005). A chief of staff:
  - manages the flow of issues to one executive;
  - follows commitments to closure;
  - acts as an honest broker, not a decider.
- **Orchestrator-workers** (Anthropic, "Building effective agents", December 2024). A central agent splits a request, hands each part to the worker that owns it, and puts the results together.

## 3. The rule

1. **Every person who holds or owns a position has exactly one conductor.** That includes an organization's owner, a person who owns a unit, and a colleague who holds a position. An agent has no conductor. A person with no position needs none.
2. **It serves one person, across every organization that person works in.**
3. **Agents reach a person through that person's conductor.**
   - A message to a position a person holds goes to the person's conductor. The conductor brings it to the person in their language.
   - Today the relay keeps such messages in `of inbox`; changing that is a build step (§7).
4. **A decision stays a card.**
   - An ask to the person is still filed with `of ask`, and the person answers on the card.
   - The conductor explains the card and says which card to answer. It never answers for the person.
   - Money, secrets, production changes and irreversible acts reach the person only as the owner's ask.
5. **The person may still type into any pane.** The conductor is the default way in, not a gate. Its measure is the share of the person's prompts that go to it (§6).

## 4. Where it lives: option A, chosen by the founder (ask `b7cb0925`)

The options as he saw them are kept below for the record.

| Option | Shape | For | Against |
|---|---|---|---|
| **A. A position in the person's home organization, with grants into the others** (recommended; **chosen**) | `<org>_conductor` for the owner, `<org>_conductor-<person>` for anyone else, owned by the person's position. Its reach into another organization is a grant the founder signs, as for `of_conductor` today (g-2026-11-10-004). | No new kind of thing. Most people work in one organization, and a grant covers the rest. It is today's working shape. | A person in several organizations has the conductor's records in only one of them. |
| B. A personal organization per person | Each person has an organization of their own (a repository and a registry) that holds the conductor and any personal agents, with grants into each organization the person works in. | It is the cleanest across organizations. It suits people who run their own agents with nothing centralized. | One more repository and registry per person, and 80 of them in a company of 80. |
| C. One conductor per organization | A single conductor serves every person in the organization. | Fewer agents. | One window shared by many people mixes their contexts and breaks "one person, one window". |

## 5. When it is created, and its place in the structure

- **`of init <org>`** creates the owner's conductor as the organization's first position, before any agent is named. Its brief comes from the template below.
  - This follows the founder's rule for onboarding (2026-11-10): start with the organization's structure, not with agents. The conductor is the owner's window into that structure.
- **Onboarding step 1, the contract with the sponsor** (docs/onboarding-practice.md): the sponsor's conductor exists from the start.
  - The onboarding lead talks to the sponsor through it.
  - The champion gets a conductor when they join.
- **A colleague who joins** (`of invite`, `of enroll`): their conductor is created with their position and runs on their machine. Only the machine that holds a position acts on it (docs/server-several-machines.md §4.3).
- **A person who leaves** (`status: left`): their conductor closes with their positions.
- **The structure keeper** creates each conductor by diff, as any position, and keeps the template. The conductor never edits the registry.
- **Unit owners** own their units' operations. The conductor routes operational matters to them. In a delegated unit, decisions go to the unit owner, not to the person, unless the person owns that unit.

## 6. Measures

| Measure | Baseline | Target |
|---|---|---|
| The share of a person's typed prompts that go to their conductor | the founder: 0 before 2026-11-10; 41 of 118 on its first day | most, and he types elsewhere only when he chooses to |
| Different panes a person types into in a day | the founder: 7 to 26 (2026-11-06..09) | a few |
| Requests closed with a result or a reason | the conductor's ledger | every request |

## 7. Build steps

| Step | Owner | Done when |
|---|---|---|
| This design, with the founder's choice of §4 | `of_architect` | the card answered: option A (`b7cb0925`) |
| The onboarding package uses it: step 1 creates the sponsor's conductor | `of_onboarding-research` (hand-over b4e0a61b) | the rebuilt onboarding diff names it: v5 (organic-framework-build commits 20f6715, 40d1518) |
| `of init` creates the owner's conductor | the onboarding subsystem's keeper: `of_onboarding` if the founder starts it (ask `d6f5c5af`), `of_platform` until then | a fresh `of init` lists `<org>_conductor` |
| A colleague's conductor position is added with their position | the organization's structure keeper, in the same diff (`acme_org` for Acme's colleagues) | the colleague's position and `<org>_conductor-<person>` are both in the registry, and the conductor runs on the colleague's machine |
| Enrollment creates the colleague's conductor, so no structure keeper has to add it by hand | `of_server` (enrollment) | an enrolled colleague has a live conductor |
| A message to a position a person holds goes to the person's conductor | `of_server` (the relay) | the message reaches the conductor and no longer waits in `of inbox` |
| The conductor's panel and ledger work for any person, not only the founder | `of_platform` (`bin/of_conductor_panel.py`) | a second person's conductor shows its own requests |

Order. The design, the onboarding package and the structure keeper's step need nothing new: a position, a brief, `of spawn` on the person's machine, and the founder's grants. The `of init` step changes existing code (`bin/of_init.py`, which today names positions after the live agents). The last three steps are new building. They start after the goal of 2026-11-11 (organic-framework-build `reports/goal-2026-11-11.md`) has its agents' parts done, because its owners carry that goal's steps. Until the relay step lands, a message to a person's position still waits in `of inbox`, and an ask still reaches the person on their own machine.

## 8. Template brief

Each organization's structure keeper writes a conductor's brief from this template. It is generalized from `of_conductor`'s brief (organic-framework-build `registry/briefs/of_conductor.md`) and the corrections it received.

> **Brief: `<org>_conductor[-<person>]`.** Role conductor, kind coordinator, owner: `<the person's position>`.
>
> You are `<person>`'s one window to the agents of `<organizations>`.
> - **Route.** For each request, find its owner in the registry: the systems map's keepers, the units' owners, the structure keeper for structure. Send it in one message that stands on its own, with the person's words quoted verbatim. Split a request with several owners, and say who got which part.
> - **Follow up** until a result exists: a commit, an answer, a delivered hand-over, or a reason it cannot be done. Read the records, the owner's state and, when it stays silent, its pane, read only.
> - **Bring back only** results, the person's decisions, and risks. Write in their language, in plain, complete sentences. Name cards and tasks by their titles, as the person sees them.
> - **Keep a ledger of the person's open requests,** and before you go idle, give the state of each one.
> - **Check a result before you report it done.** Look at a delivered file or screen yourself.
> - **Authority:**
>   - What you pass on is a request, not an approval.
>   - Decisions travel as ask ids.
>   - Never retell the person's words as a decision.
>   - Money, secrets, production changes and irreversible acts reach the person only as their owner's ask.
> - **You do not** do any organization's work, edit the registry, or type into or stop another agent's session.
> - **Reach into another organization** only under a grant.
