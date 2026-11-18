# How to reach OF

Status: **step 1 of [`support.md`](support.md) §8**, 2026-11-19. Basis: the founder, 2026-11-18 20:57Z, prompt `01a1f073-4cfc`: "on OF's side there must also be a customer success agent, who talks with the user company's agents or with its people."

OF has one agent for its users, `of_customer-success`. It answers **framework matters only**: a bug in OF, a failed setup, a missing capability, how to do something with OF. It never takes part in your organization's work, and it never asks for your data.

## Where to write

| You are | Channel | Available |
|---|---|---|
| anyone | an issue on the public repository, filed with the **Framework matter** template | now |
| a security reporter | [private vulnerability reporting](https://github.com/organic-framework/organic-framework/security/advisories/new) of the repository; never an issue | now, once `of_release` has turned it on for the repository |
| a registered organization | threads with the agent over the support API ([`support.md`](support.md)) | later: designed, not built |
| an organization on a host shared with the OF team | its structure keeper `<org>_org` writes to the agent over OF's relay under a grant ([`contours.md`](contours.md)) | on a shared host |

## What an issue holds

The template asks for five things: the OF version (the commit of your checkout, `git -C <checkout> rev-parse --short HEAD`, or the release), the command or screen, what happened, what you expected, and a confirmation that nothing private is in it. A reproduction helps most.

**Take out every name before you file**: your organization, its people, hosts, home paths, secrets and data. The issue is public. If a reproduction needs them, replace them with placeholders (`my-org`, `host-1`) and check that it still fails.

## What happens next

**Today** there is no automatic reader. `of_customer-success` reads new issues by hand when its session is running, answers with the document that covers the matter, and hands a defect to the keeper of the OF system involved with its evidence. An issue can wait until that session runs.

**To come** (the batch reader and the spend recorded per issue and per organization: [`support.md`](support.md) §8 step 4): new issues read in batches, the answer followed until you see it, and a question asked twice turned into a document or a fix.

When a matter belongs to your organization (your tasks, code, secrets, incidents), the agent says so and stops.
