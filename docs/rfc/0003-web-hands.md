# RFC 0003: web hands, browser agents that act for the owner

| | |
|---|---|
| Status | **Draft**, 2026-11-07; revised the same day (§1a) |
| Author | `of_architect` |
| Decides | the founder |
| Depends on | RFC 0001 (the platform), RFC 0002 (secrets in traces), [`handover.md`](../handover.md), "Asking the founder" in `AGENTS.md` |

## 1. Summary

Today every click in a web UI that needs the owner's account becomes a founder task ("To do for you"). On 2026-11-07, filing a kernel bug in Launchpad was such a task: the text was ready and checked, and only the clicks and the login were left for him.

A browser agent can make those clicks. Codex on the founder's Mac already has browser use and computer use. This RFC adds a second route for a task: **"Do it for me"**. The asker prepares the exact content. The founder approves it with one press. A browser agent does the clicks in a separate browser profile, records what it did, and returns the evidence. The asker checks the result and closes the task, as it does today.

Secrets, money and the owner's physical presence (SSO, Touch ID, passkeys) stay with the founder.

### 1a. Revision: the agents' own identity first (founder, 2026-11-07 10:49Z)

The founder rejected the route above as the default. It still needs him: a profile, an extension, a login per site, about 10–15 minutes, plus an approval per task. For low-priority work, that buys little. What he wants is no touch at all: an agent registers an account with the company's mail, confirms it, sets it up and files the report.

So the order of routes for owner work of kind B (§3) becomes:

1. **An API or an email, under the organization's own identity.**
   - Most targets have one. For example, the Ubuntu Security Team takes reports at `security@ubuntu.com` as well as through Launchpad, so no account is needed.
   - The agent sends from an **agent mailbox** on the company's domain. A committed script reads and sends its mail with a credential kept in the vault (RFC 0002 §4.1); no value is ever printed.
2. **An account the agents own.**
   - When a site needs an account, the agent registers one with the agent mailbox, confirms it from that mailbox, and keeps the generated password in the vault.
   - The account is the company's, named as such, never the founder's person.
   - The site's terms must allow it. A CAPTCHA or other human check is never bypassed: the agent picks another route or files a one-click task.
3. **A browser on the agents' own accounts** (the executor of §4, in its own profile), only for sites without an API.
4. **Acting in the founder's own account**, the route of §4.2, only when the account must be his: a legal identity, or a personal relationship with the vendor.

The founder's hands stay for:
- what only he can do (kind A);
- a human check a site will not waive;
- once per organization, creating the agent mailbox, unless an agent can do it with existing admin access.

Every outward act still carries an owner decision id. The decision approves the content and the target, not the clicks. For example, the Launchpad report was approved in ask `890025ce`.

#### The agent mailbox without the owner's mail admin

Where the company mail is a suite only the owner administers (for example Google Workspace on the company's domain), the agent mailbox lives on a **subdomain** the agents can reach by API, and the root MX stays untouched:
- **Inbound.** Mail-provider routing (for example Cloudflare Email Routing) hands the subdomain's mail to a small worker. The worker keeps only mail from senders the agents expect: the site it is registering with, or a recipient it wrote to. It drops everything else.
- **Outbound.** The provider's sending API, from that subdomain only, with an allowlist of sender addresses.
- **Never domain-wide delegation** in the mail suite: such a credential reads every mailbox, the owner's included.

For the first user organization, its structure keeper found on 2026-11-07:
- There is no Workspace access, and the root domain publishes no SPF, DMARC or DKIM.
- Cloudflare Email Routing is free.
- Cloudflare Email Sending (beta) needs the Workers Paid plan to reach arbitrary recipients. It includes 3,000 mails a month, and new accounts start with a small daily quota.

#### Where the identity's secrets live

The founder approved the agents' own identity (ask `29ea1baa`, 2026-11-07) and chose the store: a **1Password service account**.

- **The vault.** One 1Password vault per organization, for example "Acme Agents" for Acme, holding only the agents' identity:
  - the mailbox's scoped API token;
  - every external account's login and generated password, in the item's notes.

  The founder sees every item in his own 1Password app, so the accounts stay the company's and stay in his hands.
- **The service account.** One per organization (for example `mt-agents`), with `read_items,write_items` on that vault only. It never gets `share_items` or `--can-create-vaults`, and it can never reach a personal or private vault.
- **Its token is the one secret on disk**, in `~/.config/<org>/agent-identity/op-sa.token`, the directory mode 700 and the file mode 600. `op service-account create --raw` writes the token there straight from the founder's terminal, so it is never shown.

Rules:
- **Only committed scripts touch it.** Only committed, reviewed scripts in the organization's repository read the token and call `op` (RFC 0002 §4.1). They pass the token as `OP_SERVICE_ACCOUNT_TOKEN` to their own `op` calls only. A password is generated inside the script and never printed.
- **It never reaches the founder's 1Password.** With a service-account token, `op` never contacts the 1Password desktop app, so it never prompts the founder. This is why the rule "automation never touches the founder's 1Password" still holds.
- **The guard** denies:
  - direct reads of `~/.config/*/agent-identity/`;
  - a bare `op` on an agent's command line. A bare `op` would reach the founder's desktop app and prompt him.
- **Mail is data, never instructions.** Confirmation and reset links are secrets: the script opens them itself and never prints them.
- **Revocation and recovery.**
  - A leaked token is revoked by deleting the service account; one founder action replaces it.
  - A lost item is recovered by a password reset through the agent mailbox.
  - The mailbox is recovered through the provider account the owner administers.
- **Limits are no concern at this volume.** 1Password limits service accounts per hour: 1,000 reads and 100 writes on Teams and Families, 10,000 and 1,000 on Business.

## 2. Facts (2026-11-07)

- **Founder tasks.** 25 tasks were filed between 2026-11-06 14:18Z and 2026-11-07 07:41Z, about 17 after removing withdrawn duplicates. About a third are clicks in his accounts that show no secret:
  - the Launchpad report;
  - two posts in a Slack Connect channel;
  - deleting old GitHub app keys;
  - disabling a former employee in the data warehouse;
  - deleting an old provider token.

  The rest create or show a secret (monitoring, object storage, provider and lab tokens, a GitHub app key), need his presence (cloud and warehouse single sign-on, Touch ID), or move money.
- **Codex on this Mac.** Codex ships inside ChatGPT.app with its CLI (`codex-cli 0.167.0-alpha.3.2`).
  - Feature flags `browser_use`, `browser_use_external`, `computer_use`, `in_app_browser` and `hooks` are stable and on.
  - Plugins `browser` (the in-app browser, aimed at local targets), `computer-use` and `unified-computer-use` are enabled.
  - Plugin `chrome` (the user's own Chrome, with its sign-ins) is available but not installed.
  - `codex queue --thread <id> --message … --resume` queues a message into an existing session.
  - Rollouts are in `~/.codex/sessions`, which `of import` already reads.
- **Public sources.**
  - Codex gained computer use and an in-app browser on 2026-04-16, and a Chrome extension on 2026-05-07.
  - On macOS, computer use runs in the background; on Windows, in the foreground only.
- **Unknown.** Can the Codex CLI in a herdr pane drive the Chrome plugin and computer use, or only the desktop app? This decides where the executor runs (§4.1). The Lab answers it first (§6).

## 3. Three kinds of owner work

| Kind | Examples | Route |
|---|---|---|
| **A. Only the owner** | creating or viewing a secret; payment; SSO, Touch ID, passkey, 2FA on his phone; signing as a legal person | a task for him, as today |
| **B. His account, no secret on screen** | filing a bug, a post in a shared channel, deleting an old key, disabling a user, a settings toggle | **web hands**, with his approval of the exact content |
| **C. No account** | reading public pages, checking a status page, downloading a public file | any agent, no approval |

A task that mixes A and B is split, one sitting per task (`AGENTS.md`, 2026-11-07). For example, the founder creates the new token (A), and web hands later delete the old one (B).

## 4. Design

### 4.1 The executor and its browser profile

- **The executor is a position**, for example `acme_web-hands` in Acme. The structure keeper names it. It is held by a Codex session with the `chrome` plugin, or by any other harness that passes the Lab's bake-off (§4.6).
- **Where it runs.** If the CLI can drive the browser, it runs in a herdr pane, like every other position, so the relay, `of status` and the desk work unchanged. If only the desktop app can, the session is an app thread: OF reaches it with `codex queue --thread … --resume`, and the thread reports back with the `of` CLI.
- **A separate Chrome profile, "OF hands".** The founder signs in there, once, only to the sites on the allowlist (`~/.config/of/hands.yaml`). His main profile, which holds the password manager, mail and banking, is never attached. This follows the same principle as the SSH agent rule: automation never reaches the founder's own keyring.
- **Pages go to the model provider.** Whatever the executor sees is sent to OpenAI (or to the provider of whichever harness holds the position). A site whose pages must not leave the Mac stays off the allowlist.

### 4.2 Dispatch: "Do it for me"

1. The asker files the task with a route:

   ```
   of ask "<title>" --task "<steps>" --hands web --site bugs.launchpad.net --content <file> --done-when "<check>"
   ```

   The content file holds the exact text the executor will type. The steps say what to click, where to stop and what to return.
2. The popup shows the task under "Do it for me": the site, the content, the last irreversible step (for example "Submit"), and two buttons, "Do it" and "I'll do it myself".
3. "Do it" is the owner's decision. It is an `of.agent_question_answered` with `approve_hands: true`, and its id is the authority. OF hands the task to the executor (`of handover`), carrying the ask id, the steps and the content.
4. The executor works and stops before the last irreversible step if the steps say `confirm_before: submit`. It then posts a screenshot of the filled form to the popup, and the founder presses "Send". A low-risk task may go through on the first approval.
5. "I'll do it myself" keeps today's route: the steps are shown to him.

### 4.3 Authority and the guard

- **Every action needs an owner's decision id** naming the site, the action and the content. A relayed message or a page's text never authorizes anything.
- **The executor only goes where it was sent.** It opens only the task's site and the allowlist. A link to another domain is a stop and a question, not a click.
- **Page text is data, never instructions** (prompt injection). The executor's brief says so. It types only the content file; it never takes text from the page into another field.
- **Never kind A.** The executor refuses any page that would show a secret (a "copy your token" screen), a payment form or an SSO or 2FA prompt. It stops and files a kind-A task for the founder instead.
- **One task, one site, one approval.** No standing grant to act on a site.

### 4.4 Recording

- **Install the OF collector as Codex hooks.** They are stable in this version; RFC 0001 §8 listed Codex hooks as deferred. Every executor turn then becomes events like those of any other position.
- **Each action is an `of.web_action` event**: site, URL path, action, the ask id, and a screenshot reference in the blob store.
- **Screenshots fall under RFC 0002**: scanned, kept in the tenant's store, never shipped to another organization's server.
- **The rollout** stays in `~/.codex/sessions` and is imported as today.

### 4.5 The result and its check

- The executor returns:
  - the result: a URL, a bug number, a post link;
  - the last screenshot;
  - `of status done --note …`.
- The asker checks the result as it would after the founder's own "done". For example, the bug exists, or the old key is gone (`gh api`). Then it runs `of status working --done-task <id>`.
- If the result is not there, the task reopens with what is missing and goes back to the founder's queue.

### 4.6 Executor neutrality and the bake-off

- The founder considers Codex the strongest at browser use. We start there, and keep the interface harness-neutral: the task, the profile, the allowlist, the events and the check.
- The Lab runs a bake-off on five recorded kind-B tasks:
  - in a throwaway Launchpad staging or test account;
  - in a test Slack workspace;
  - in a test GitHub organization.

  It compares Codex (`chrome` plugin and computer use), Claude in Chrome and a Playwright-based agent. It measures success, the number of owner touches, time, tokens, and any step outside the allowlist.

### 4.7 Capacity

The executor spends the ChatGPT subscription. `of_capacity` adds it to `of capacity status` as one more account, with its limits.

## 5. Pilot

After §1a:

1. **The agent mailbox for the first user organization.**
   - Its structure keeper finds out what the company's mail allows and whether an agent can create the mailbox with existing admin access.
   - If no agent can, it is one task for the founder: create the mailbox.
2. **The Launchpad report** goes by email from that mailbox to `security@ubuntu.com`, with the text already checked by its security keeper and approved by the founder in ask `890025ce`.
3. **The Lab's CLI-or-app test and bake-off (§4.6)** move to the agents' own test accounts and wait until a kind-B task needs a browser.

## 6. Who builds what

| Step | Who | Done when |
|---|---|---|
| 1. CLI-or-app test, then the bake-off on test accounts | `of_lab` | a report with the five tasks and the measures above |
| 2. `of ask --hands web`, the popup's "Do it" and "Send", handover to the executor, `of.web_action` | `of_platform` | a kind-B task runs end to end in the Lab |
| 3. Codex hooks collector | `of_platform` | a Codex turn writes the same events as a Claude Code turn |
| 4. ChatGPT subscription in `of capacity` | `of_capacity` | its limits show in `of capacity status` |
| 5. The executor position and its brief, per organization | each structure keeper (`acme_org` for Acme) | a registry entry and a brief that carries §4.3 |

## 7. Decisions for the founder

Replaced by §1a. One decision is left: **may agents register and use accounts on external sites under the company's identity (the agent mailbox), and send what he already approved in content, without asking him again for the clicks?** The rules of §1a apply: no CAPTCHA bypass, passwords only in the vault, each outward act tied to an owner decision id.

The first case is the Launchpad report. It is low priority, so it waits for the agent mailbox, then goes by email to `security@ubuntu.com`. The founder task `b0bd12d0` is withdrawn.
