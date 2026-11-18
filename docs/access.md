# Access through keepers: the map, a request, approval across machines, the weekly review

Status: **accepted** by the founder, 2026-11-18 21:01Z, prompt `01a1f077-09b7` in `of_conductor`'s session ("ok, access through keepers: approve"); designed 2026-11-19 by `of_architect`, with `of_server` for the guard and the keepers. Basis: the founder's yes, 2026-11-18 20:13Z, prompt `01a1f04a-a868` in `of_conductor`'s session ("okay, let's try"), to `of_conductor`'s four-point plan for growing an organization's structure and access once its agents live on an agent host behind keepers (message `01a1f04d-13d3`).

**Build state.** §1 to §5 are the design. `of_server` builds them step by step (§6), each in its own pull request; a step is in use once that pull request reaches the public main, and is marked here then.

## 1. Keepers

A **keeper** holds something an agent must use but never hold: a vault item, a signing key, a browser profile with its sign-ins. It runs as its own OS user on the agent host. Its home is closed to the agents' users, and its program and folder are owned by root (organic-framework-build `reports/agent-host-isolation-2026-11-18.md`, layer 1). An agent asks it through one narrow door: an asking script run by one sudoers line, or a socket. It does what its list allows for that caller and nothing else. Three kinds exist so far:

| Kind | Holds | An entry allows |
|---|---|---|
| `vault` | an organization's secret store access, such as its 1Password service account | named items, for a caller |
| `signer` | one money key | destinations, a per-call cap and a per-period cap |
| `browser` | a browser profile signed in to providers' account pages | sites, the actions on them, a download folder, a per-day count |

The keeper knows its caller from the process, not from anything the caller sends: the caller's process id from the socket or its sudo parent, up to the Claude Code session, and through OF's session record to the position (that report, "The caller's identity"; `of_server` designs it, `of_lab` tests it). Within one organization every position runs as the same OS user, so this rests on process ancestry, not on the OS. For a money keeper, what bounds a caller that is not who it seems is the grant's per-call and per-period caps (§4).

## 2. One access map per organization

`of access map [--org O] [--position P] [--json]` prints who reaches what, through which keeper, with which limit, approved how, and when each entry was last used. It is generated, never kept by hand, from:
- the registry's credentials catalogue: the credentials each position lists or keeps (docs/registry.md, Credentials);
- each keeper's list, `registry/access/<keeper>.yaml` in the organization's repository (below);
- the grants whose scope names a keeper (§4);
- the keepers' records of each request, served or refused (`of.access_used`, `of.access_refused`, §5).

Built so far, step 1 of §6 (2026-11-19, `of_server`; `bin/of_access.py`, `tests/test_access.py`): the map reads the catalogue and the grants, and from step 2 the keepers' lists. A credential is reached by each position that lists it and by its keeper. Its APPROVED column is the organization repository's commit that last changed that line, with the commit's `Position:` trailer, or `uncommitted`. LAST USED comes from the secrets broker's records on a machine that runs the server, for a credential the broker serves by its `ref`: `none here` when it never served it, `no broker here` on a machine without a server. A file's use reads `not recorded` until the keepers write `of.access_used` (step 4). A grant shows whether its signature verifies against this machine's pinned grant key. `--long` adds what each credential acts as and its paths or reference. The map never opens a credential file.

A keeper's list:

```yaml
keeper: acme-signer
kind: signer                      # vault | signer | browser
host: <the agent host>            # the machine the keeper runs on
entries:
  - position: acme_payer
    access: "dest:0x1234…"        # vault: "item:<item id>"; signer: "dest:<address>"; browser: "site:<domain>"
    actions: [send]               # browser: download, read, order…; signer: send; vault: read
    limit: {per_call: {amount: 0.5, unit: ETH}, per_period: {amount: 2, unit: ETH, period: P1D}}   # numbers, a unit, an ISO 8601 period
    tier: money                   # plain | secret | money | production (§3)
    decided: "01a1f000-0000-7000-8000-000000000123"   # card OF-123 (an example id): the full event id of the founder's card (AGENTS.md: a field a program reads keeps it), or the structure keeper's commit for a plain entry
    approval: "passkey:<record id>"   # secret and above (§4)
    grant: g-2026-11-19-004       # money and production (§4)
    added: {at: 2026-11-19T10:00:00Z, by: acme_org}
    expires: null                 # optional
```

- Only `of access grant` and `of access revoke` write a list, and the structure keeper commits it.
- A keeper reads its list from the committed tip of the organization's default branch, as `of subagent run` reads its entry (docs/registry.md, Subagent harnesses). An uncommitted edit allows nothing. Any session of that organization that can commit there could still add a line; for `secret` and up the signatures in §4 are what holds, and no keeper kind takes a `plain` line so far.
- A `limit` is machine-read: an amount, a unit and an ISO 8601 period, in the line and in the grant alike, so the keeper compares them as numbers.
- A keeper's own policy file, such as the signer's destination list and caps, is generated from its list. The two never drift apart by hand.
- **The keeper's kind sets the tier's floor** (`of_secrets-review` on PR 349). A line's own `tier` decides which approvals the keeper checks, and the structure keeper writes it, so a lower tier must not lower the checks. A `signer` line is `money` at least, and a `vault` or `browser` line is `secret` at least. A browser site that moves money, such as a payment or banking page, is `money` by the keeper's own site list, which the host's setup script installs in the keeper's root-owned folder. The floors live in the keeper's code and that folder, never in the list: the keeper refuses a line whose tier is below its floor, and `of registry check` names it.
- `of registry check` validates every list: a known keeper and position of that organization, a tier, a `decided` that resolves, and an `approval` and a `grant` where the tier needs them.

## 3. A request for access

1. **The position asks its structure keeper**: `of access request --keeper <keeper> --access <what> [--actions …] --limit <…> --why "<the work it is for>"`. The request goes to the organization's structure keeper (`acme_org` for Acme, `of_architect` for the OF team) as a routed ask. It is never a message to the founder.
2. **The structure keeper checks the mandate.** It runs `of delegate "<the work>"`. When the work belongs to another position, or to none, it says so and the request stops there. It then sets the tier:
   - `plain`: nothing secret, no money, no production. No keeper kind so far has this floor (§2); it is for a future kind that holds nothing secret;
   - `secret`: a vault item, or a signed-in provider page;
   - `money`: a signing key, a payment or a banking page;
   - `production`: anything that changes a production system.
3. **A plain entry** is the structure keeper's decision. It runs `of access grant`, and its commit is the record.
4. **A secret, money or production entry is one founder card**, filed by the structure keeper and written for him (AGENTS.md, "Asking the founder"): "Give `<position>` `<access>` with `<limit>`?". It names the work, the keeper, the limit and the expiry, with the tag `security`, `money` or `prod_change`, and the options yes or no. One card per entry; several entries for one work are numbered in one card.
5. **On his yes**, the structure keeper runs `of access grant <keeper> <position> <access> --limit … --decided <card> [--approval …] [--grant …]`. That adds one line, and the keeper refuses the line until the approvals in §4 verify.
6. **Removal is one line**: `of access revoke <keeper> <position> <access> --why …`. It needs nobody's yes, since it only narrows. The keeper stops serving the entry at its next read of the list, and the weekly digest names it (§5).

Built so far, step 2 of §6 (2026-11-19, `of_server`; `bin/of_access.py`, `tests/test_access.py`):
- `of access request --keeper K --access A [--actions a,b] [--limit JSON] --why "…"` checks the access, actions and limit against the keeper's kind, then files the ask to the structure keeper with the keeper's floor and these steps in it.
- `of access grant <keeper> <position> <access> --tier T --decided <card|commit> [--actions …] [--limit JSON] [--approval passkey:<id>] [--grant <id>] [--expires <UTC time>]` adds the line after the same checks as `of registry check`, with its time and writer in `added`. `--kind` and `--host` start a keeper's list. In an agent's session only the organization's structure keeper runs it; the owner runs it from a plain terminal with `--org`. `--limit` is JSON: `{"per_call": {"amount": 0.5, "unit": "ETH"}, "per_period": {"amount": 2, "unit": "ETH", "period": "P1D"}}`.
- `of access revoke <keeper> <position> <access> --why "…"` removes the line, under the same rule for who runs it.
- `of registry check` checks every list: the file's name against its `keeper`, the kind and the host, and each entry's position, access form, actions, tier against the kind's floor, and limit (numbers, a unit, an ISO 8601 period; a signer needs both caps, a browser a per-period count). It also checks that `decided` is a card on this machine (secret and up) or a commit of the organization's repository (plain), that secret and up has a `passkey:` approval, that money and production name a grant in `registry/grants.yaml` (its signature and scope are step 3), and the `added` and `expires` fields.
- The map shows each line with its keeper, its limit and its `decided`, and reads `uncommitted: serves nothing` until the line is committed.

## 4. Approval across machines

The founder decides on his laptop or his phone. The agents and the keepers run on the agent host. His keys never go there: only signatures and public keys do.

- **Plain:** the structure keeper's commit.
- **Secret:** his yes on the card, given on the approval page that shows the entry's exact line, with his passkey (the phone or the laptop; `of diff link`, docs/remote.md §6). The passkey signs the line without its `approval`, `grant` and `added` fields, in canonical JSON (keys sorted), so recording the approval or the time it was added never breaks it. The signed record is stored with the entry as `approval`.
- **Money and production:** the same passkey approval, and also a grant he signs with his own key on the laptop, through Touch ID: `of grant issue --scope keepers=<keeper>:<access> --limit … --expires …`. He runs it in a plain terminal, and no agent's session can run it (docs/guard.md G13; AGENTS.md, "Automation never touches the owner's SSH agent"). The grant lands in the organization's `registry/grants.yaml` (docs/registry.md, Grants). The card names the expiry he chooses, and the grant carries it.
- **What the keeper verifies before it serves a new line.** The host's setup script, which he runs once, pins the organization's grant public key and his passkey enrollment records in the keeper's root-owned folder. An agent cannot write there. Before it serves a line, the keeper checks:
  - the passkey record's signature covers the exact line, and the enrollment is signed by the grant key (as `of diff apply` checks today);
  - for money and production, the grant's SSHSIG verifies against the pinned public key, its scope names this keeper, this access and a limit at least as wide as the line's in every part (amount, unit, period): the grant bounds the line, never the other way, so a line wider than its grant is refused; and it has not expired or been revoked.

  A line that fails any check is not served, and the keeper tells the structure keeper why.
- **Revocation fails closed** (`of_secrets-review` on PR 349: today's `of.grant_revoked` is an unsigned event in the agents' own store). A keeper treats a grant as revoked as soon as any source says so, and never takes a revocation back:
  - his signed revocation (`of grant revoke`, his key on the laptop) in `registry/grants.yaml` at the committed tip, or sent to the keeper directly;
  - an `of.grant_revoked` event, signed or not, and a grant missing from the committed file. An unsigned revocation can only narrow, so it is taken as it is;
  - the keeper keeps every revocation it has seen in an append-only log in its own folder. Restoring an older copy of the grants file, which still carries the grant's valid signature, brings nothing back;
  - it reads the grants file only from a commit that descends from the last one it read;
  - when it cannot read its sources, it serves no `money` or `production` line.

  A grant's `expires` bounds what is left.

Built so far, step 3 of §6 (2026-11-19, `of_server`; `bin/of_keeper.py`, `tests/test_keeper.py`):
- `of_keeper.line_problem` is the check every keeper kind calls before it serves a line, with what it read from the committed tip and its own folder: `pins/grant.pub`, `pins/passkeys/*.json` (`of_keeper.load_pins`) and `revocations.jsonl` (`of_keeper.RevocationLog`, append-only: a grant gone from the committed file, a `revoked` field or an `of.grant_revoked` event revokes it, and nothing takes that back). `of_keeper.descends` checks that a new commit descends from the last one read. The keeper runs with its own `OF_ORGS` and state folder, so `of_grants.passkey_revoked` reads its own record of revoked passkeys.
- What the passkey signs is `of_keeper.line_payload`: `{id: access:<keeper>:<position>:<access>, org, keeper, line}`, the line without `approval`, `grant` and `added`, times in UTC with a Z; its digest is `of_diff.digest`'s. So the approval page and `of_passkey.approval_problem` serve a line as they serve a diff, and of-server needs nothing new.
- `of access link <keeper> <position> <access>` gives the structure keeper that page's address for the founder's card. `of access record <keeper> <position> <access>` takes his approval from the organization's feed once it reaches this machine, checks it, saves it as `registry/access/approvals/<event id>.json` and names it on the line as `approval: passkey:<event id>`. The structure keeper commits both together.
- `of registry check` names a line of secret and up whose record is missing or does not approve the line as it stands.
- After `of_secrets-review`'s review of PR 357: a kind that is not `vault`, `signer` or `browser` serves nothing; a browser keeper serves nothing until its folder pins `pins/money_sites.txt`, the payment and banking domains, one per line, and a line for one of them, or a subdomain, is `money` at least; a revocation log that cannot be read whole (a line that is no record, or fewer lines than its count file says it held) makes the keeper's sources unreadable, so it serves no money or production line; `of_keeper.ApprovalLog` withdraws for good the approval of a line gone from the committed list, by the nonce the owner's passkey signed (never the record's file name, which the list's writer chooses), so a line removed and added again needs the owner's new yes; and amounts are exact decimals (`of_access.amount`): an integer, a decimal string, or a finite float, never NaN or an infinity. Write large amounts, such as wei, as integers or strings.
- A grant for a line: `scope: {keepers: ["<keeper>:<access>"], limit: {…}}`, exactly one line, the tenant's allow to one position, with `expires`; `of_access.limit_within` holds the line within it in every part. A narrower limit attenuates, and a wider or an open one does not.

This replaces the inference in `of_conductor`'s point 3. Grants already verify with the public key alone (docs/guard.md G13), and passkey approvals already verify through the enrollment's signature by the grant key (docs/remote.md §6). New here are a grant scope that names a keeper, and the pinned copies in each keeper's folder.

## 5. The weekly review

- **Every request is recorded, served or refused** (as docs/agent-secrets.md §5.7 records every request to the broker). A keeper writes `of.access_used {keeper, position, access, at}` for a call it serves and `of.access_refused {keeper, position, access, reason, at}` for one it refuses, as events in its organization's store through its own writer. Values are never recorded: an item's id, never its value; a destination and an amount, never a key.
- **Once a week**, on Monday from 05:00 UTC like the corrections digest, the pulse sends each structure keeper one `--fyi` digest:
  - the entries unused for 30 days, each with its `of access revoke` line ready. The organization may set `access_review: {unused_days: N}` in its registry; 30 is a proposal, so that an access nobody uses does not stand for months;
  - the grants that expire within 14 days;
  - the lines a keeper refused, with the reason;
  - what was granted and removed that week.
- **The structure keeper decides**, removing with `of access revoke`. A money or production entry it keeps past 30 days unused stays on the next digest.
- **The founder sees one line a week per organization**, in his conductor's report: the number of entries by tier, what changed, and what waits on him.

Built so far, step 5 of §6 (2026-11-19, `of_server`; `bin/of_access.py`, the listener's `AccessDigestProcessor`, `tests/test_access.py` DigestTest): from 05:00 UTC on Mondays the listener starts `of access digest`, which sends each organization's structure keeper its week's review once per ISO week, as an `--fyi` message from that keeper's own position, so it never crosses organizations. It lists the committed lines unused for `access_review: {unused_days}` days (30 when unset), counted from their last `of.access_used` or, never used, from their `added` time, each with its `of access revoke` line; the grants that expire within 14 days; the lines a keeper refused this week (`of.access_refused`); and the lines committed and removed since a week ago. An organization with nothing in it gets nothing. `of access digest --dry-run --force` shows it now. The founder's weekly line in his conductor's report is `of_conductor`'s.

## 6. Who builds what

The first step stands on what exists today, so it starts now.

| Step | Who | Done when |
|---|---|---|
| 1. `of access map` from the credentials catalogue and grants alone, with no keeper lists yet | `of_server` | it prints the OF team's and a user organization's current reach |
| 2. Keeper lists in docs/registry.md, `of access request`, `grant` and `revoke`, their checks in `of registry check` | `of_architect` (schema), `of_server` (code) | a plain entry goes from request to the list on a test organization |
| 3. A grant scope that names a keeper; the keeper's verification of the passkey record and the grant against pinned keys | `of_server` | a line without a valid signature is not served, in the Lab |
| 4. Each keeper serves from its list and records every call, `of.access_used` or `of.access_refused`: the vault asking script, the signer's policy, the browser keeper's sites and actions | `of_server`; each organization's keepers through its structure keeper | the map shows last use for each kind |
| 5. The weekly digest in the pulse | `of_server` | a test organization gets it on a Monday |
| 6. Each organization's lists | its structure keeper (for example `acme_org`, once step 2's format exists) | its map has no hand-kept line |
