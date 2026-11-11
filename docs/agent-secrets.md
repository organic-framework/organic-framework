# Agents' secrets that follow the position

Status: **approved with changes** by `of_architect` (msg `01a1cab8`, 2026-11-11; the changes are in), by `of_server`. Hand-over `7ae99eac` from `of_architect`, from the founder's request through `of_conductor` (hand-over `df9d6584`). Points marked **schema**, **API** or **event** are agreed with `of_architect`; the schema paragraphs go into registry.md with the build. The founder's decisions are one card (§11). **Built** (2026-11-11): the broker (`bin/of_server_secrets.py`, `POST /v1/secrets/use`), `of secret run` and `of secret plan` (`bin/of_secret.py`), `ref` and `rotate_days` in the catalogue, the guard's deny of the token folder (guard.md G1.10); tests `tests/test_secrets_broker.py`. Next: break-glass grants, `of secret rotate|handover|review`, the `of.secret_*` events (with `of_platform`), then the lab proof (§10). **The store is the organization's choice** (founder, 2026-11-11, §13): sections 4 to 7 describe the 1Password store, one of several; the broker and the hand-over do not depend on the store. Building waits on the founder's decision on the broker (card `379274c4`).

Frame: the credentials catalogue ([`registry.md`](registry.md), Credentials), the guard ([`guard.md`](guard.md) G9, G14), tool profiles (RFC 0004 §3.1), the identity-store rules (RFC 0003 §1a), of-server on several machines ([`server-several-machines.md`](server-several-machines.md)), moves ([`capacity.md`](capacity.md)).

## 1. What was asked

For every organization:
1. vaults in 1Password by system and by organization;
2. a service account per position;
3. a position's secrets follow it when its agents move from one person or account to another, and the old holder loses access;
4. rotation, break-glass and audit;
5. reasoned with FPF, checked against named security practice, each 1Password limit taken from its documentation, built on the credentials catalogue.

## 2. Today

- **Secrets are files.** An organization's working credentials sit under `~/.config/<org>/` on the founder's Mac (the first user organization: 9,139 files under its root, 275 with a credential name, in the replay of 2026-11-11; guard.md G14). Every agent runs as the same OS user, so a file's mode keeps no position from another's credentials. The catalogue and the guard (G14, deny since 2026-11-11 for named files) stop accidents and use by the wrong position, not a process that wants the file.
- **One service account per organization.** The first user organization has one service account with access to one vault. Its token sits on the founder's Mac under the organization's `agent-identity` folder; only that organization's committed script reads it (RFC 0003 §1a; guard.md G9 denies a bare `op`).
- **Agents will leave the Mac.** A position may soon be held on a colleague's machine or on the always-on agent server (server-several-machines.md; `of_agent-server`). Today nothing moves a credential with a position, and nothing takes it away from the machine it leaves.
- **Moves between accounts keep the session.** `of capacity move` resumes the same session, in the same pane, on another Claude account of the same machine.

## 3. The reasoning (FPF)

FPF edition as in [`methodology.md`](methodology.md); pattern A.2.1, `U.RoleAssignment`.

1. **A position is a role in its organization's context; a holder is whoever enacts it now.** An assignment is `〈holder, role, context, window〉` (A.2.1:4.1). The role's meaning lives in its context (RA-1, Locality). So a credential belongs to a position of one organization, never to the person, machine or Claude account that holds it, and never crosses into another organization.
2. **An assignment confers authorization, not capability** (RA-5). A credential is the means of an authorization. It must therefore be granted through the assignment and end with it: the credential follows the role, and the holder only borrows it while the window is open.
3. **A hand-over closes one window and opens another** (A.2.1:4.8.1: "Close `A#Role@..t` and open `B#Role@t..`", history kept by closing windows, CC-WIN-1). The design keeps every holder's window in the record; it never erases who held what.
4. **Each use is an enactment, valid only while the assignment is enactable** (RE-5, A.2.1:4.8.2). Access is checked at every use, not once at hand-over. A suspended or revoked assignment (A.2.1:4.8.3) stops the next use, whatever the holder kept.
5. **An emergency bundle is activated only under a declared emergency** (A.2.1:4.8.4). Break-glass is a declared, time-boxed state with its own record, not a standing back door.

From 2 and 4: **the holder should never hold the long-lived key at all.** If the key sits on the holder's machine, closing the window depends on someone deleting a file on a machine that is no longer trusted. The design keeps the keys with a broker that checks the assignment at each use.

## 4. What 1Password allows (its documentation, read 2026-11-11)

| Fact | Source |
|---|---|
| At most **100 service accounts** per 1Password account | 1password.dev/service-accounts ("You can create up to 100 service accounts.") |
| **Vault access is fixed at creation**: "You can't add vault access to a generated service account after creation"; permissions per vault "can't be changed later"; to change them, create a new service account | 1password.dev/service-accounts/security.md; get-started.md; manage-service-accounts.md |
| Permissions per vault: `read_items`, `write_items` (needs read), `share_items` (needs read); **no item-level scope** | get-started.md; CLI reference `op service-account create --vault` |
| Never the built-in Personal, Private or Employee vault, or the default Shared vault; never a vault whose service-account access is turned off | get-started.md; security.md |
| A service account **can't create another** service account and can't manage users | security.md |
| Created by owners and administrators, or by groups or people they allow; "Anyone granted this permission can manage only the service accounts they created" | manage-service-accounts.md |
| **The token is shown once** (wizard and CLI); "It's up to you to save and protect the service account token"; owners and administrators can't view it | get-started.md; security.md |
| **Rotation** creates a new token with the same permissions; the old one expires when chosen: "now", "1 hour", "3 days"; **revoking** "immediately removes its access"; a deleted service account's token can't authenticate | manage-service-accounts.md; security.md |
| The CLI documents only `op service-account create` (with `--expires-in`, `--vault`, `--raw`) and `ratelimit`; **rotation and revocation are documented in the web interface only** | CLI reference, management-commands/service-account.md; manage-service-accounts.md |
| **Rate limits** per token per hour: Business 10,000 reads and 1,000 writes; Teams and Families 1,000 and 100. Per account per 24 hours, all service accounts together: Business 50,000, Teams 5,000, Families 1,000. Over a limit: HTTP 429 with a retry time | 1password.dev/service-accounts/rate-limits.md |
| **Audit**: each service account has an Item Usage Report (vaults and items it can access, the ones it used, when, and the action) | manage-service-accounts.md |
| A **secret reference** is `op://<vault>/<item>/[section/]<field>`; names may hold letters, digits, `-`, `_`, `.` and spaces, are case-insensitive, and a part with any other character must be named by its ID | 1password.dev/cli/secret-reference-syntax.md |
| The **Events API** reports sign-in attempts, item usage in shared vaults and audit events of a **Business** account; item usage arrives only when clients sync. Whether reads by service accounts appear there is **not documented** | 1password.dev/events-api.md; events-api/item-usage-actions.md |

Two of these shape the design:
- vault access is fixed at creation, so a holder change must not need a new service account;
- rotation and revocation are web-interface actions of a person, so a holder change must not need a rotation either.

## 5. The design

### 5.1 Vaults: by organization and system

- **One vault per system that has credentials**, named `<org>-<system>` in plain ASCII (for example `acme-storefront`, `acme-obs`). A system's id comes from the organization's systems map. Plain names keep secret references free of quoting and IDs (§4: other characters must be referred to by ID).
- **One organization vault**, `<org>-org`, for what belongs to no single system: the agent mailbox, a registrar, the organization's own service-account plan.
- **Split a system's vault when its readers differ.** 1Password scopes a service account to whole vaults, never to items (§4). When two credentials of one system are read by different positions, they go to two vaults (`acme-k8s-read`, `acme-k8s-admin`), so a service account never reaches an item its position does not list.
- **One break-glass vault**, `<org>-break-glass`, that no service account can open (§5.6).
- **Nothing in the founder's personal vaults**, which service accounts cannot reach anyway (§4).

**Schema** (agreed with `of_architect`): a credential in the catalogue names its item by 1Password's own secret reference, which names the field and which `op read` and `op run` take as it is, and its rotation period. It needs at least one of `ref` and `paths`: a credential may live only in the vault.

```yaml
credentials:
  grafana-viewer:
    ref: "op://acme-obs/grafana-viewer-token/credential"   # a secret reference; never a value
    paths: ["~/.config/acme/grafana/viewer.token"]   # only while the file still exists (migration)
    acts_as: "Grafana service account agents-viewer, role Viewer"
    keeper: obs_keeper
    rotate_days: 90
```

`of registry check` adds: `ref` is a secret reference whose vault is `<org>-…` of the credential's organization, in the characters a reference allows; one item is named by one credential; each credential has `ref` or `paths`.

### 5.2 Service accounts: one per position that uses credentials

- **A position that lists or keeps a credential gets one service account**, named `of-<org>-<position>`. A position without credentials gets none. This keeps the count under the cap of 100 (§4): the first user organization has 66 positions, 6 of which list credentials and 5 of which keep them (2026-11-11); the OF team has no credentials yet.
- **Its vaults are the vaults of its credentials**, computed from the catalogue: `read_items` for the credentials it lists; `write_items` as well for those it keeps, so the keeper can write a rotated value.
- **Created once per position**, and again only when its mandate adds a vault (vault access is fixed at creation, §4). A holder change never needs a new one.
- **Created by the founder**, or a person he allows in 1Password (§4: only people create service accounts), in a plain terminal. `of secret plan --org O` prints the exact `op service-account create … --vault … --raw` line for each, with its output going straight into the broker's token file. The founder runs it as a task for his hands; no value passes through a chat or a pane (AGENTS.md, tasks that make a secret).
- **The organization's existing service account is retired** once its positions have their own (§8).

### 5.3 The broker: keys stay with the organization's server

**API** (to `of_architect`): of-server gets a secrets endpoint; the `of` client gets `of secret`.

- **The broker is part of the organization's of-server** (on the founder's Mac today: launchd `dev.organic-framework.server`; later the organization's own server). It keeps every position's service-account token in its state folder, one file per position, mode 600, never in a repository and **never on a holder's machine**.
- **A holder asks for a credential at the moment of use**: `of secret run <credential> [--env NAME | --file NAME] -- <command…>`. The client asks `POST /v1/secrets/use`. The broker answers only when all of these hold:
  1. the machine's own credential (an enrolled machine, as the relay already requires);
  2. the machine presents the position now (presence);
  3. the calling session is the position's registered holder (`holder.session`), the same check that guards taking a message;
  4. the position lists or keeps the credential (the catalogue);
  5. no break-glass hold or suspension is on the position.
  Then it reads the item with **that position's own** service-account token, so 1Password itself enforces the vault boundary even if the broker's check had a bug.
- **The value goes into the command only.** The client puts it in the command's environment, or in a temporary file (mode 600) that it removes when the command ends. It never prints it, never writes it to a transcript, and the guard and masking rules stay as they are (guard.md G1–G8, K1–K4).
- **A short cache.** The broker keeps a value in memory for at most 5 minutes, and drops it at once on a holder change or a rotation. The load is small: the first user organization's agents used named credential files 465 times in 21 days (guard.md G14 replay), far under the hourly and daily limits of any plan (§4).
- **The broker decides who gets a value, not what is done with it.** `of secret run` hands the value to a command the holder writes, so the holder can print it or copy it. Masking (guard.md K) is the backstop for what reaches a transcript, and rotation on a change of person (§5.4) closes the gap a former holder leaves.
- **On the founder's Mac the broker shares the holders' OS user.** There every agent runs as the same OS user as the broker, so the token folder is readable by every session, and "never on a holder's machine" holds only on a separate broker host. Until then, the token folder is a credential root that the guard denies to every session as a critical rule (guard.md G14), and a suspected compromise of the host rotates every token it holds (§5.5).
- **What it does not stop.** On one machine every session runs as the same OS user, so a hostile process can impersonate the holder session there. The broker stops accidents, wrong positions and every former holder on another machine; the boundary between positions on one machine is a separate OS user per position, the agent server's design (`of_agent-server`), and the broker serves that design unchanged.

### 5.4 Hand-over: how a position's secrets follow it

The broker reads the holder at every use (§3, point 4), so a hand-over needs no step in 1Password:

| Case | What changes | Access | Values the old holder used |
|---|---|---|---|
| **Another Claude account on the same machine** (`of capacity move`) | nothing: the same session continues | unchanged | nothing to do |
| **A new session on the same machine** (a respawn) | `holder.session` | the broker serves the new session and refuses the old one from the next call | nothing to do: same person, same machine |
| **Another machine of the same person** | `holder.session`, the machine in presence | as above; the old machine is refused | nothing to do |
| **Another person, or the agent server** | `holder.session`, machine, person | as above | **rotated** by each credential's keeper (below) |
| **A person leaves** | the machine is revoked (`of server revoke`) | every position on it is refused | rotated, as above, for everything that machine's holders used in the last 90 days |

- **Why rotate when the person changes.** Closing the window ends access; it does not make a value someone has already seen secret again. NIST SP 800-53 Rev. 5 AC-2(k) asks for "a process for changing shared or group account authenticators … when individuals are removed from the group", and PS-5 asks to "modify access authorization as needed" on a transfer. A position's credentials are exactly such shared authenticators across its successive holders.
- **Only what was used.** The broker's record (§5.7) lists the credentials the old holder actually read in its window. `of secret handover <position>` files one hand-over per such credential to its keeper, who rotates it with its system's own procedure (§5.5). A credential nobody read in the window needs no rotation.
- **Moves stay with `of_capacity`.** What a move must do about secrets reaches `of_capacity` as a hand-over: never copy a token or a secret file with a session, and record a holder change only through the registry and presence, which the broker reads.

### 5.5 Rotation

- **Values, by their keeper.** Each credential has `rotate_days` (default 90) and is also rotated on a trigger: a person change (§5.4), a break-glass use (§5.6), an exposure found by the guard or masking (guard.md K), a leaver, or a suspected compromise of the broker's host. The keeper changes the value in its system with its own committed script, then writes the new value to the item through the broker (`of secret rotate <credential>`, its service account has `write_items`). The broker drops its cached value. OWASP's Secrets Management Cheat Sheet: "regularly rotate secrets so that any stolen credentials will only work for a short time" (§2.7.2) and "create secrets to expire after a defined time where possible" (§2.7.4).
- **Service-account tokens, by a person, rarely.** A token never leaves the broker's host, so it is rotated every 90 days, and **every token the broker holds** is rotated when that host is suspected. Rotation is a web-interface action (§4): the founder, or the person he allows, rotates with "Expire existing token: now" and writes the new token into the broker's file with a silent read (`umask 077; read -rs T; printf %s "$T" > <file>`), as a task for his hands. With one service account per position that uses credentials (about 11 today), that is a few minutes a quarter.
- **The broker warns** when a credential passes its `rotate_days`, once to its keeper, and lists overdue ones in the review (§5.7).

### 5.6 Break-glass

Three levels, each declared and recorded (§3, point 5):

1. **A position needs a credential it does not list, urgently.** The founder's signed grant, issued in a plain terminal like every grant, names exactly one credential for exactly one position for at most 24 hours (**schema**, agreed: grants get `scope: {credentials: [id]}` with one id and a required `expires` at most 24 hours after issue, as for `contacts`). The broker serves it, files a founder card at each use, and files the credential's rotation for its keeper when the grant ends.
2. **The broker or its host is down.** Agents wait; a person acts. The founder reads the item in his own 1Password app and does the work himself, or restores the broker. No agent gets a token as a workaround.
3. **1Password or the founder's own account is out of reach.** The `<org> · break-glass` vault holds the few root credentials (cloud root, domain registrar, the 1Password account's own recovery). No service account can open it. Two named people can, from devices that are not agents' machines. Following Microsoft's guidance on emergency access accounts: at least two people, no dependency on the normal path, an alert on every use, a review after each use, and a drill at least every 90 days. OWASP §2.9: break-glass credentials are backed up in a second system and tested routinely.

### 5.7 Audit and review

- **Every request to the broker is recorded**, allowed or refused (**event** `of.secret_used`, agreed with `of_architect`; its fields are agreed with `of_platform` for docs/events.md): organization, position, holder session, machine, person, credential id, vault, decision and reason. Never the value. It ships to the organization's server like every typed record. OWASP §2.6 asks for who requested a secret, when, and authorization errors, in a log that resists tampering; the server's store is outside the holder's machine.
- **Rotations and break-glass are recorded too** (`of.secret_rotated`, `of.secret_breakglass`).
- **1Password's own view.** Each position's service account has its Item Usage Report (§4). A read there that the broker did not record means the token was used outside the broker: an incident. On a Business plan, the Events API may make this check automatic; whether it lists service-account reads is not documented and is checked in the lab (§10).
- **A review every 90 days**: `of secret review --org O` lists each position, its credentials, the last use of each, overdue rotations and break-glass uses. Access nobody used in 90 days is proposed for removal to the structure keeper (NIST SP 800-53 AC-2(j), reviews at a set frequency).

## 6. Checked against security practice

| Practice | What it asks | Here |
|---|---|---|
| NIST SP 800-53 Rev. 5 **AC-6**, least privilege | only the accesses "necessary to accomplish assigned organizational tasks" | per-position service accounts, vaults split by reader set, item-level check in the broker (§5.1–5.3) |
| **AC-2(k)**, shared authenticators | change shared authenticators when individuals leave the group | rotation on a person change, only of what was used (§5.4) |
| **AC-2(j)**, account reviews | review accounts at a set frequency | the 90-day review (§5.7) |
| **PS-5**, personnel transfer | review the need for access on reassignment and "modify access authorization as needed" | hand-over by holder change; access ends at the next call (§5.4) |
| **AC-2(2)**, emergency accounts | automated temporary and emergency account management | break-glass grants expire within 24 hours (§5.6) |
| OWASP Secrets Management Cheat Sheet **§2.2, §2.3** | centralize; engineers do not have access to all secrets | one store per organization, one broker, per-position access |
| OWASP **§2.4**, automation | "limit or remove the human interaction with the actual secrets" | values go from the broker into the command; nobody pastes them |
| OWASP **§2.6**, audit | who requested which secret, when, authorization errors; tamper-resistant | `of.secret_used` on the organization's server; 1Password's report as a second view (§5.7) |
| OWASP **§2.7**, lifecycle | rotation, revocation, expiry | §5.5; break-glass grants expire |
| OWASP **§2.9** and Microsoft Entra emergency access guidance | break-glass backed up and tested; two accounts, alerts on every use, a drill at least every 90 days | §5.6 |
| FPF A.2.1 | assignment authorizes; enactment checked at each use; hand-over closes and opens windows | §3, §5.3, §5.4 |

## 7. Fit to 1Password's limits

| Limit (§4) | How the design stays inside it |
|---|---|
| 100 service accounts | only positions that use credentials get one (about 11 today); a new organization adds its own few |
| Vault access fixed at creation | a holder change never needs a new account; only a mandate change that adds a vault does |
| Rotation and revocation only in the web interface | a holder change needs neither; tokens rotate quarterly, by a person |
| No item-level scope | vaults split by reader set; the broker checks items |
| Rate limits per token and per account | a 5-minute cache; today's use is about 22 named-credential reads a day |
| Events API: Business only, service-account reads not documented | the broker's own record is the primary audit; 1Password's report is the cross-check |

## 8. Migration

1. **Catalogue.** Each structure keeper adds `vault:` and `rotate_days` to its credentials (after the schema review). `of registry check` validates them.
2. **Vaults and items.** Each credential's keeper moves the value into its system's vault (the founder's hands for vault creation, once per vault).
3. **Service accounts.** `of secret plan` prints the creation lines; the founder runs them in a plain terminal.
4. **Broker.** of-server serves `of secret run` on the Mac. Positions switch from files to `of secret run`, credential by credential. When a credential's file is no longer used, its keeper deletes it and drops `paths`. The guard G14 keeps protecting the files that remain.
5. **Retire** the organization's single service account and its token file.
6. **Several machines.** The same broker serves remote holders through the organization's server (server-several-machines.md).

## 9. Who does what

| Who | What |
|---|---|
| `of_server` | the broker, `of secret`, the events, the review, this document |
| `of_architect` | reviews this design; the catalogue and grant schema |
| `of_platform` | `of.secret_*` events in docs/events.md |
| `of_capacity` | moves: never copy a token or a secret file; holder changes only through the registry and presence |
| `of_lab` | the lab proof (§10) |
| `of_agent-server` | one OS user per position on the agent server; the broker client runs per user |
| Each structure keeper | `vault:` and `rotate_days` in its catalogue; asks its keepers to move values |
| Each credential's keeper | moves its value into the vault; rotates it on schedule and on triggers |
| The founder's hands | create vaults and service accounts (one line each), rotate tokens quarterly, hold the break-glass vault with a second person |

## 10. The lab proof (with `of_lab`)

**Claim:** a position's hand-over gives the new holder access and the old holder none, without any step in 1Password.

**Setup:** two lab machines A and B enrolled for one test organization, its of-server with the broker, and a stub of 1Password that enforces a token's vaults and logs every read. Optionally, a second leg on a real test vault with one test service account (the founder's hands, once).

**Steps and pass criteria:**
0. **1Password's boundary holds without the broker.** With the stub, position P's token cannot read a vault of position Q, so a bug in the broker's own check would still not reach another position's items.
1. Position P is held on A. On A, `of secret run cred -- <check>` succeeds; on B it is refused.
2. P is handed over to B (registry holder change, B's presence). Within 10 seconds, the same call on A is refused and on B succeeds.
3. No service-account token exists on A or B (a scan of both machines' files and environments).
4. The broker's record shows A allowed, A refused after the hand-over, B allowed; the stub's log shows only the broker's reads.
5. The hand-over was to another person, so `of secret handover P` filed one rotation for each credential A used, and none for the rest.
6. Real leg: the test service account's token is rotated with "Expire existing token: now"; the broker works with the new token and the old one is refused by 1Password; the Item Usage Report lists exactly the broker's reads; on a Business plan, the Events API is checked for service-account reads.

## 11. The founder's card

One card, numbered, each point with a default he can accept as is:
1. **The 1Password plan.** A fact on his account's billing page. Default: Teams's limits until he says Business; on Business the Events API cross-check is switched on (§5.7).
2. **Who besides him may create and rotate service accounts.** Default: only he.
3. **Rotation on a change of person.** Default: yes, every credential the former holder used is rotated (NIST SP 800-53 AC-2(k)); the question is only which credentials, if any, are exempt.
4. **The second person for the break-glass vault.** Default: none yet; level 3 of break-glass (§5.6) waits until he names one.

**His answers** (ask `9cec60bb`, 2026-11-11): (1) the company account is **Business**, so the Business rate limits apply and the Events API cross-check of §5.7 is planned, its coverage of service-account reads to be checked in lab step 6; (2) **only he** for now, and he asked whether someone else should have access; (3) and (4) the defaults. A person 1Password lets create service accounts manages only the ones that person created (§4), so a deputy who can rotate the tokens of existing accounts must be a 1Password administrator; that choice is his next card.

## 12. Alternatives not chosen

- **The token travels with the holder.** On a hand-over the founder rotates the token with "Expire existing token: now" and the new holder gets the new one. Rejected: a click by a person at every hand-over (rotation is web-only, §4), and a long-lived key on every holder's machine, readable by every process of that user.
- **One service account per organization** (today). Rejected: any holder of any position reaches every vault of the organization.
- **A service account per holder** (per machine or per person). Rejected: vault access is fixed at creation and only people create service accounts (§4), so every move would need the founder's hands.
- **1Password Connect**, a self-hosted server that 1Password compares with service accounts (1password.dev/secrets-automation.md). Not evaluated here: the broker gives per-holder control on the server OF already runs, without another service.

## 13. The store is the organization's choice

### 13.1 What was asked

The founder, 2026-11-11 ~18:56Z, through `of_conductor` (msg `01a1cbf2`): "far from everyone uses 1Password as their secrets store, so at some stage we have to let them choose what the secrets store will be; and probably the later that is done, the better." `of_conductor`'s frame: a store interface with 1Password as one option and the stores companies commonly run; chosen at the first moment a position actually needs a secret, not at install; the guard names the chosen store's commands, not only `op`.

### 13.2 When it is chosen: at the first need

- **Not at install.** `of init` asks nothing about secrets. An organization starts with **files**: credentials under its credential root, the catalogue's `paths`, and the guard's G14, as today (§2).
- **The first need is the moment.** It is the first of these:
  1. a structure keeper adds a credential with a store reference (`ref`) to the catalogue;
  2. a credential's keeper asks to move a file credential into a store (`of secret plan --org O`);
  3. a position runs `of secret run` in an organization that has no store yet.
  At that moment OF files **one card** for the organization's owner, once (§13.7). Until the answer, `of secret run` refuses with "this organization has not chosen its secrets store yet; card <id>", and the files keep working.
- **An organization that already chose keeps its choice.** The first user organization chose 1Password for its agents' secrets (ask `29ea1baa`, 2026-11-07; AGENTS.md, a service account per organization). It is recorded as its store when the schema lands, with no new card.

### 13.3 What the broker needs from a store

The broker (§5.3), the hand-over (§5.4), the record (§5.7) and the review do not change with the store. They call the **`secret_stores` plugin family** ([`plugins.md`](plugins.md) §3) through the shared loader (`bin/of_family.py`): one implementation per store under `bin/plugins/secret_stores/<name>.py`, or an installed `organic_framework.secret_stores` entry point. The name is `secret_stores` everywhere, folder, entry-point group and registry key: a folder named `secrets` is a secret path for the guard (G1.1) and for Claude Code's native deny rules (N1), so no agent could read the code in it (`of_architect` and `of_platform`, 2026-11-11, b633781). The family's methods are `read`, `write`, `exists`, `list` and `bootstrap` (`of_family.FAMILIES`); the broker needs from each implementation:

| Operation | What it does |
|---|---|
| `ref` form | the catalogue's reference for this store, validated by `of registry check` (below) |
| `container(ref)` | the unit the store scopes access by: a vault, a project, a path, a name prefix, a file |
| `read(ref, identity)` | the value, read with **the position's own identity** in this store; never logged |
| `exists(ref)`, `list(container)` | whether an item is there, and the items of a container (names only), for `of registry check` and the review |
| `write(ref, value, identity)` | a keeper's rotation, with an identity that may write that container |
| `bootstrap(position, read, write)` | the steps a person runs once to create the position's identity with exactly those containers, and where its secret goes (the broker's identity file, with a silent read); `of secret plan` prints them |
| `enforces_boundary` | whether the store itself stops one position's identity from reading another's containers (§5.3, "1Password itself enforces the vault boundary") |
| `audit` | where the store's own record of reads is, for the cross-check of §5.7 |
| `value_commands` | the command forms that print a stored value, for the guard (§13.6) |

The `ref` form, `container`, `enforces_boundary`, `audit` and `value_commands` are attributes of an implementation beyond the family's five methods; they are proposed to `of_platform` for the family.

### 13.4 The stores

Each row is checked against the store's own documentation before its implementation is built, as §4 did for 1Password; the cells below are the design's assumptions, not yet that check.

| Store | `ref` | Position identity | Boundary the store enforces | Who creates the identity | The store's own audit |
|---|---|---|---|---|---|
| **1Password** (service accounts) | `op://<org>-<system>/<item>/<field>` | a service account per position | vaults; fixed at creation (§4) | an owner or administrator (§4) | Item Usage Report; Events API on Business (§4) |
| **Bitwarden Secrets Manager** | `bws://<project>/<secret id>` | a machine account per position, with an access token | projects, read or read-write per project | an organization administrator | the organization's event logs (to check which plans) |
| **HashiCorp Vault** (or OpenBao) | `vault://<mount>/<org>/<system>/<name>#<field>` | an AppRole per position, with a policy over its paths | policies; editable later, so a mandate change needs no new identity | a Vault administrator | audit devices |
| **AWS Secrets Manager** | `aws-sm://<region>/of/<org>/<system>/<name>#<key>` | an IAM role per position, assumed by the broker host | IAM policies on the secrets' names or tags; editable later | an AWS administrator, best in the organization's infrastructure code | CloudTrail (`GetSecretValue`) |
| **GCP Secret Manager** | `gcp-sm://<project>/<secret>[@<version>]` | a service account per position | IAM on each secret (or on labels, with conditions); editable later | a project administrator | Cloud Audit Logs, data access (switched on per project) |
| **sops with age** (encrypted files in the organization's repository) | `sops://<file>#<key path>` | an age key per position, held by the broker | the file's recipients: a position's key decrypts only the files it is a recipient of | the structure keeper edits the recipients; `sops updatekeys` re-encrypts | none beyond git history (who changed a file); the broker's record is the only read log |
| **The OS keychain** (macOS keychain, Linux secret service) | `keychain://<service>/<account>` | none: the OS user | none between positions; the broker's check only | nobody | none; the broker's record only |
| **Files** (no store chosen) | (none: `paths`) | none | none; the guard's G14 stops accidents, not a process | nobody | none |

What follows from the table:
- **A store that enforces the boundary is preferred** (1Password, Bitwarden, Vault, AWS, GCP, sops). With the keychain or files, a bug in the broker's own check reaches every position's credentials; that is acceptable only for a solo owner's single machine, and the card says so.
- **Only 1Password fixes access at creation.** With Vault, AWS or GCP a mandate change edits a policy instead of creating an identity, and an administrator's script (never the broker) can rotate a position's identity, so §5.5's "by a person, in the web interface" is 1Password's case only.
- **The names keep the design's units.** "One vault per system" (§5.1) becomes one container per system in any store: a vault, a project, a path, a name prefix, a file. The rule "split a container when its readers differ" stays wherever the boundary is per container.

### 13.5 What changes in the design

| Section | With another store |
|---|---|
| §4, §7 (1Password's facts and limits) | one such table per store, from its documentation, before its implementation is built |
| §5.1 vaults | containers per system, named in the store's form |
| §5.2 service accounts | a position identity in the store, only for positions that use credentials; `of secret plan` prints the store's own steps |
| §5.3 broker | unchanged; `read` goes through the family; the identity file holds the store's secret (a token, an AppRole secret, an age key) |
| §5.4 hand-over | unchanged: the broker reads the holder at each use |
| §5.5 rotation | values unchanged; the identity's rotation as the store allows (above) |
| §5.6 break-glass | level 3 lives wherever the company keeps its root credentials; OF records its use only |
| §5.7 audit | the store's own audit column above, as the cross-check |

### 13.6 The guard

- **Values printed** (G6) already covers `vault kv get` and `vault read`, `aws secretsmanager get-secret-value`, `gcloud secrets versions access`, `sops -d` to stdout, `op read` and `op item get`, and the keychain with `-w` (G11). **To add**, each after the usual 21-day replay: Bitwarden (`bws secret get` and `list`, `bw get` and `bw list items`), `secret-tool lookup`, `age -d` to stdout, `aws secretsmanager batch-get-secret-value`, and `aws ssm get-parameter(s) --with-decryption`.
- **The chosen store's own command line** (G9 today names `op`) becomes one rule per store, read from the organization's registry: the store's command line runs only from the broker and the organization's committed scripts, with the organization's identity variable set (`OP_SERVICE_ACCOUNT_TOKEN`, `BWS_ACCESS_TOKEN`, `VAULT_TOKEN` or an AppRole login, the broker's `AWS_PROFILE`, a position's `GOOGLE_APPLICATION_CREDENTIALS`, `SOPS_AGE_KEY_FILE`). Without it, the command would act as the person signed in on the machine, as a bare `op` reaches the founder's own 1Password (G9's reason).
- **The identity files** stay a credential root that the guard denies to every session (G1.10, G14), whatever the store.

### 13.7 The owner's card at the first need

One card for the organization's owner, in the owner's language through the locale layer, standing on its own (AGENTS.md):
1. **The decision:** where the organization keeps its agents' secrets.
2. **Why now:** the first credential needs moving (which one, for which position); until the answer it stays a file.
3. **The options**, ranked by what the organization already runs: a store CLI found on its machines, the cloud of its systems in the systems map, a store named in its existing references. Each option gives its consequence: whether the store keeps positions apart by itself, who must create identities and how often, and its cost if it is a paid product. Recommended: the store the company already runs and audits; with none, sops with age in the organization's repository (no service to run, a boundary per file); the keychain only for a solo owner on one machine. For the keychain the card also says that an item another program created can make macOS ask the owner when it is read, since its access list names that program (`of_architect`, review of PR 20).
4. **What happens next:** the structure keeper records the answer (**schema** below) and `of secret plan` prints the first identity's steps (`bootstrap`) as a task for the owner's hands.

**Schema** (agreed with `of_architect`, a0a1407; [`registry.md`](registry.md), Plugins): an organization's choices live under `plugins:`, so its store is `plugins: {secret_stores: {name: 1password|bitwarden|vault|aws-sm|gcp-sm|sops|keychain, chosen: <ask id>, settings: {…}}}`, with settings such as a Vault address, a region or a project. Absent means files; `chosen` is required. `of registry check` refuses a `ref` whose store is not the chosen one, and a container that is not the organization's (§5.1, for any store).

### 13.8 Build order

Hand-over `3512db81` from `of_architect` builds the family now: its five methods, the 1Password, macOS Keychain and lab env-file implementations, the `<backend>://<path>` references and their contract tests. Building it moves no position from files to the broker. The rest waits on the founder's decision on the broker (card `379274c4`):
1. the `secret_stores` family's files implementation and the refusal of §13.2;
2. the broker and `of_capacity`'s credential broker reading through the family, today's `_op_read` moved behind the 1Password implementation (`bin/plugins/secret_stores/1password.py`), unchanged in behaviour;
3. the first-need card, `plugins.secret_stores` in the registry, and `of registry check`;
4. the guard rules of §13.6 for the chosen stores, each after its replay;
5. other implementations when an organization chooses that store, each with its facts table, a stub in the lab proof (§10, step 0 against the stub) and, where possible, a real leg.
