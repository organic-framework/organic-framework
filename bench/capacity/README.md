# The capacity package

A Bench package ([`docs/framework.md`](../../docs/framework.md), "Installing from Bench"): one person's Claude Code
accounts, measured and balanced, so that no session stands on a usage limit while another account has room. Kept by
`of_capacity`. The design is [`docs/accounts.md`](../../docs/accounts.md), part B; the balancer's rules are
[`docs/capacity.md`](../../docs/capacity.md).

| File | What it is |
|---|---|
| `package.yaml` | the role (one per person, in their home organization), the settings written to `capacity.yaml`, the services and the rules, each with its source |
| `briefs/capacity.md` | the position's brief |

Before it: `of account add <name>` lists each account on the machine and files the person's task to log in there;
`of claude [--account NAME]` opens Claude Code on one, with no shell alias. The package adds the position that keeps
them working; the services run without it once `capacity.yaml` says `auto: true`.

Placeholders filled by `of init`: `{org}` (the organization's prefix), `{org_name}` (its full name, as in an address),
`{Org}` (its display name), `{owner_id}` and `{owner_name}` (the person the position serves).
