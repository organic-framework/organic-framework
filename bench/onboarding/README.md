# The onboarding package

Organic Framework's first Bench package ([`docs/framework.md`](../../docs/framework.md), "Installing from Bench"):
how a company starts on OF. Kept by `of_onboarding`. The documentation is [`docs/onboarding.md`](../../docs/onboarding.md);
the practice it follows, with every source, is [`docs/onboarding-practice.md`](../../docs/onboarding-practice.md).

`of init <org> --owner "<name>"` reads it and writes a new organization: the head's conductor as the first position,
then the onboarding unit and its run, with no agent started.

| File | What it is |
|---|---|
| `package.yaml` | the unit blueprint (eight roles, the conductor first) and the run (one work, eight stages), each role and stage with its sources |
| `briefs/conductor.md` | the head's one window; its core is [`docs/conductor.md`](../../docs/conductor.md) §8 |
| `briefs/sponsor.md`, `briefs/champion.md` | role cards for the two people |
| `briefs/onboarding-lead.md` | the bootstrap position: runs the eight steps and leaves |
| `briefs/business-case-analyst.md`, `briefs/system-architect.md`, `briefs/structure-keeper.md`, `briefs/flow-analyst.md` | the four lenses |
| `templates/charter.md` | step 1: scope, champion, people, what is read and what is not, systems of record, outside parties, how findings come back |
| `templates/role-design.md` | step 7: an owner per gap, one watcher per stakeholder, each role with its outside party, channel and access |
| `templates/recheck.md` | step 8: each item counted from the records by a position that did not build the structure |
| `environments/acme.md` | the dry run on the fictional Acme: persona, fixtures and each check's oracle; `of_lab` runs it as `lab-onboarding-v0` |

Placeholders filled by `of init`: `{org}` (the organization's prefix), `{Org}` (its display name), `{owner_id}` and
`{owner_name}` (the head). A role or step that changes names its source, or the run evidence that changed it, in
`package.yaml` and in the practice record's §8.
