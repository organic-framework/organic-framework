# {Org} onboarding charter

Status: **draft**, by `{org}_onboarding`. Approved by: _(the sponsor's ask id, once answered)_.

The agreement between {owner_name} and the onboarding on what OF reads and who takes part. The agents draft it
from what they found themselves (who works on what, which repositories exist; no content), and {owner_name}
confirms or corrects it in one sitting; they are never asked to write it (record §8.4 (11)). Sources: Block's entry and
contracting (*Flawless Consulting*); TOGAF Phase A's Statement of Architecture Work; Martin and Osterling's charter
(organic-framework `docs/onboarding-practice.md` §1.2–§1.4, step 1). The lead drafts it through the head's
conductor and asks only what the records cannot tell.

## 1. The question and the scope

- **Decides:** {owner_name} (`{org}_owner`).
- **The question the onboarding answers:** _(in the sponsor's words)_
- **In scope:** _(the organization, units, systems or value streams)_
- **Out of scope:** _(what the onboarding will not look at or change)_

## 2. Where it hurts now (step 2)

- **The head's last story of confusion or bureaucracy, verbatim:** _(what they could not find out, or whose
  decision they waited for)_
- **The first area of work to put on OF:** _(suggested by the agents from the story and the people map; confirmed)_
- **The stage of the company:** S0 idea, S1 started, S2 operating or S3 scaling (`docs/business-case.md`).

## 3. People

Each person here holds a position in the registry (`people:`), and each gets a conductor on joining
(`docs/conductor.md` §3 and §5).

Drafted by the agents from the people map (person, likely role, systems, evidence); confirmed or corrected by
{owner_name}.

| Person | Likely role, and the evidence | In the onboarding | Position | Their conductor |
|---|---|---|---|---|
| {owner_name} | the head | decides | `{org}_owner` | `{org}_conductor` |
| _(name)_ | _(found: role, systems, source)_ | owns the first area we put on OF (suggested by the agents) | _(`<area>_champion`)_ | _(`{org}_conductor-<person>`)_ |
| _(name)_ | _(found: role, systems, source)_ | _(may be asked about their part)_ | | |

## 4. What OF reads, and what it does not

| Source | Read? | Who reads it | Permission |
|---|---|---|---|
| GitHub: the repositories listed below | yes | system architect, flow analyst | this charter |
| Claude Code sessions people choose to record | yes, per person | structure keeper, flow analyst | each person's consent (`of consent`) |
| The structure: the registry and the briefs | yes | everyone in the unit | this charter |
| The head's mail, chat and meetings | **no** | — | — |
| _(a confidential source: the books, a bank, a provider's portal)_ | _(yes or no)_ | _(who)_ | _(an ask id)_ |

Repositories: _(list)_

This section limits what agents read on their own initiative, never what a person hands them. A source {owner_name},
the owner of the first area or the source's own owner gives an agent with "read this" is read at once, and recorded here
(`docs/onboarding-practice.md` §8.4 (9)):

| Source | Given by | Their words, verbatim | When (UTC) | Read by |
|---|---|---|---|---|

OF has no messages to people outside the organization, and it never interviews anyone but {owner_name} and the
owner of the first area unless this charter names them.

## 5. Where each record is kept (systems of record)

Name the system of record before calling a record missing (`docs/onboarding-practice.md` §8.2 (7)).

| Record | System of record | Kept by | Access for the onboarding |
|---|---|---|---|
| the books | _(e.g. an accounting service run by outside accountants)_ | _(who)_ | _(none, read-only, with an ask id)_ |
| bank accounts and payments | | | |
| contracts | | | |
| customers | | | |
| licences and filings | | | |
| people and payroll | | | |

## 6. Outside parties

Every role that works with an outside party names it, with who writes to it, the channel and the access; the head's
login tasks are listed here, so the channels open as soon as the roles exist (§8.2 (8), §8.3 (1)). Signing, moving
money, identity checks and strategy choices stay with people.

| Outside party | What the company needs from it | Writer (a position, or the head) | Channel | Access: read, then write | The head's login task |
|---|---|---|---|---|---|
| _(e.g. the outside accountants)_ | | | | | |

## 7. How findings come back

- **One decision batch** (step 6): the lead files the findings as one numbered card; the head's conductor presents
  it and explains each item; the head answers on the card.
- **One batching point per unit:** the lead for the onboarding unit. No other position of the unit files cards to
  the head one by one (§8.3 (2)).
- **Language:** the head is addressed in {language} (set at `of init --language`, kept in
  `~/.config/of/owner.yaml`); documents are written in _(language)_.
- **The measure:** the flow analyst's before report (step 4) and the re-measure (step 8), by one method (§5).

## 8. Approval

The sponsor approves this charter on one card. Its ask id goes in the status line at the top.
