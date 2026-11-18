# Support for OF's users: a public API for registered organizations, issues for everyone

Status: **design**, 2026-11-19, by `of_architect`, with `of_server` for identity, the service and its limits. Basis: the founder, 2026-11-18 20:59Z, prompt `01a1f075-109f` in `of_conductor`'s session: "And what if we make some public API for this? And connect people to it after registration? Then they will have a direct connection, and otherwise through GitHub issues." It is the channel of OF's customer-success agent, `of_customer-success` (docs/method.md, "Users' success").

## 1. Two tiers

| Tier | Who | How |
|---|---|---|
| **The support API** | a registered organization: its structure keeper `<org>_org`, its other agents, its people | threads with `of_customer-success`, over HTTPS, after sign-up |
| **Issues** | anyone | the public repository's issues, by the issue template; security matters through private vulnerability reporting |

Both carry framework matters only: a bug in OF, a failed setup, a missing capability, how to do something with OF. Never an organization's work or data.

## 2. Where it runs

- **A service of its own, `of-support`, on a public host.** It is not OF's internal relay, of-server, or any machine where OF's agents run. It holds the registrations, the threads and the limits, and nothing else.
- **OF's agent reaches it outward only.** `of_customer-success` pulls new messages from the service in batches and posts its answers back. Nothing on the internet can open a connection to an OF machine.
- Its host and its cost are the founder's choice, one card when the build is ready: a small hosted worker or a container on a host OF already pays for.

## 3. Registration and identity

- **An organization registers once**, by its owner: a GitHub or e-mail sign-in, the organization's name, and a contact. The owner accepts the terms of the channel: framework matters only, no organization data.
- **Its agents use an organization token**, scoped to that organization and that service only. The owner creates it after sign-up, and it lives in the organization's own secret store. It can be rotated and revoked, and it is never a credential on any OF system.
- **Its people sign in themselves**, with GitHub or a passkey, under the registration of their organization, which its owner confirms.
- Every message carries who sent it: the organization, and the agent position or the person. A message with no valid identity is refused.

## 4. What crosses it

- **Text only**, a few kilobytes per message, and text logs up to a size limit. No files besides those.
- **The service checks every message before it is stored**: gitleaks with its default rules and the organization-name and home-path checks of the pre-publish gate. A message that fails is refused with the reason, so the sender takes the data out. The sender's own pre-publish check runs first, on its side.
- **`of_customer-success` never asks for an organization's data.** It asks for a reproduction with names and data taken out.

## 5. Limits, so OF's spend does not grow with the users

- **Per organization:** a number of new threads and messages per week, and a cap on OF's spend on its threads per week. The founder sets both values on the card; the service and the agent enforce them.
- **When a cap is reached**, the service says so and when it resets, and points to issues. Nothing is lost.
- **One agent for all organizations.** OF pays for answers, not for watching anyone. A question asked twice becomes a document, which cuts the next one's cost.
- **Measured from day one:** the agent records its spend per thread and per organization, and the weekly line to `of_conductor` carries it.

## 6. Abuse and prompt injection

- **Their text is data, never instructions.** The agent's brief says so, and the agent can do nothing that a crafted message could abuse:
  - it reads the public repository and the documents;
  - it posts only to the thread it answers;
  - it holds no grant on any organization, no credential and no write access beyond its own answers;
  - its reading and searching go through Codex read runs (RFC 0008).
- **The service throttles and bans.** It rate-limits by identity and by address, refuses an identity its owner revoked, and lets `of_customer-success` close a thread or suspend an organization, telling its owner why.
- **Security reports** never go through the API: private vulnerability reporting only.

## 7. Cost to OF per organization

- **Fixed:** none per organization. The service is shared, and its host costs the same for one organization or fifty.
- **Variable:** the answers, bounded by each organization's weekly spend cap.
- **What the organization pays:** its own agents' tokens for watching its OF health and writing to OF (docs/method.md, "Users' success").

## 8. Who builds what

| Step | Who | Done when |
|---|---|---|
| 1. The issue template, private vulnerability reporting, and a page a newcomer reads on how to reach OF | `of_customer-success` with `of_release` | an issue filed by the template reaches the agent |
| 2. `of-support`: registration, identity, threads, checks, limits, the agent's pull | `of_server` | a test organization opens a thread, and the agent answers it within its cap, in the Lab |
| 3. The host | the founder's card | the service runs where he chose |
| 4. The agent's side: the pull in batches, the spend per thread, the weekly line | `of_customer-success` | the line shows a week's threads and spend |
