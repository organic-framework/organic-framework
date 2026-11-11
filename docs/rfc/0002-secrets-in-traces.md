# RFC 0002: secrets in agent traces

| | |
|---|---|
| Status | **Draft**, 2026-11-05 |
| Author | `of_architect`; facts and tooling by `of_platform` |
| Decides | the founder |
| Depends on | RFC 0001 (the platform), §5.2 (vault), §5.2a (`of-server`), §5.9 (security) |

## 1. Summary

Agent transcripts hold secrets: private keys, passwords, tokens. This happens because agents read files, print environments and echo commands. Organic Framework now captures every byte of these transcripts.

This RFC sets out four things:
- how secrets are found;
- how they are kept out of every store that anyone other than the tenant can read;
- how they stop entering agents' context in the first place;
- how found secrets are reviewed and rotated.

## 2. Facts (2026-11-05)

`of_platform` scanned every transcript on the founder's Mac: four Claude config directories and Codex. It used gitleaks 8.30.1 with redaction. Each value was identified only by an HMAC under a tenant-only key, so no value was printed or stored. Organizations were assigned by the platform's routing.

| Kind | Unique values | Where |
|---|---|---|
| Private keys (PEM blocks), all high entropy | **12** | user organization 8, unassigned 4 |
| `user:password` in curl commands | **19** | user organization |
| Authorization headers in curl commands | **6** | user organization |
| AWS access keys (some may be documentation examples) | **6** | unassigned 4, user organization 2 |
| Generic API keys | 1,897 unique; **73** with entropy ≥ 4.0 bits | user organization 1,730, unassigned 184, OF build 12 |
| **EVM private keys** (64 hex characters next to key words; contextual rule) | **6** (26 appearances) | user organization |
| Passwords in URLs (`scheme://user:password@host`; contextual rule) | **50** (some are placeholders) | user organization 47, OF build 3, unassigned 1 |

That gives **99 high-signal unique secrets** plus up to about 73 plausible generic ones.

**The six EVM keys were checked on-chain** (2026-11-05, at the founder's request):
- The addresses were derived in memory; keys were never printed or stored.
- Public RPCs of four EVM chains were queried.
- All six have **zero balance, zero transactions and zero balance in the main tokens**, and none is a known test-framework default key.

They are therefore test or example keys, or **off-chain signing keys**: builder or relay authentication, or bundle signing. An off-chain key holds no money, but it carries identity and reputation, so it is still rotated if it is in use. Not checked yet: Solana, other chains, keystore files and seed phrases.

**Exposure.** Everything in a transcript has already been sent to the model provider as context. Transcripts also sat in plaintext under `~/.claude*`, readable by every agent running as the same user. In the eight-day retrospective (incident 16), an agent recovered an administrator credential from an old transcript.

## 3. Principles

1. **Values never enter context.** The best secret in a transcript is one that was never there.
2. **Unmasked secrets exist only where the tenant alone reads them.** These places are the local spool and archive, and the vault.
3. **Masking happens at the boundary, centrally.** `of-server` masks before anything reaches an organization's database. No client can skip it.
4. **A found secret is an incident until the owner says otherwise.** Detection leads to review and rotation, not only to masking.
5. **No tool ever prints a secret value**, including the tools that find secrets.

## 4. Design

### 4.1 Prevent: keep values out of context (steering, RFC 0001 §5.7)

- **Deny rules** in the harness settings for files with secrets (`.env*`, `*.pem`, `*.key`, keystores, `executors.txt` and similar, extensible per organization). They are installed by `of hooks install` and reviewed by the owner.
- **A critical grant with `effect: deny`** for printing environments and secret files (`printenv`, `env`, `cat .env`, `echo $…KEY`). This is one of the few hard blocks the mandate router keeps (RFC 0001 §5.7).
- **Secrets by reference.** Commands receive secrets through injection: 1Password `op run`, environment variables set outside the agent, or a credential helper. The value never appears in a prompt or a tool output.
- **An agent rule in every organization's `AGENTS.md`.** Never paste, echo or log a secret value. Refer to a secret by its name in the secret manager.

#### The guard's Bash check (`of_guard.critical_deny`, 2026-11-06; in `bin/of_hook.py` until 2026-11-07)

The critical deny parses a Bash command once, with quotes tracked, and denies what will **execute or be read as data** by the command — never prose it only carries (an `of send` message, a `git commit -m`, a heredoc written to a file). A secret **value** must never reach the transcript (stdout, stderr, the command text), and a secret **file** must not leave its place; names are not secrets, and a credential a program reads without showing is not a leak. It denies: environment dumps (`printenv`, `env`, `set`, `export -p`, `env | grep <value>`, `printenv <secret-name>`, `ps e`/`eww`, and the same inside `docker`/`kubectl exec` and `ssh`); printing a secret-shaped variable (`KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL(S)|PRIVATE` as a whole `_`-part) when it reaches the agent; reads, copies, archives, encodings and uploads of a secret file (`cat`, `tar`, `zip`, `rsync`, `scp` source, `openssl`, `gpg`, `base64`, `xxd`, `curl -F/-d/-T @file`, `wget --post-file`); cluster and secret-manager reads that print stored values (`kubectl` on a Secret kind — every `-o` except `name`/default, plus `-w`, `edit`, `apply view-last-applied`; `kubectl get pod -o yaml|json`; `helm get values|all|manifest`; `kubeseal --raw`; `sops -d`; `vault kv get`; `aws secretsmanager get-secret-value`; `gcloud secrets versions access`; `op read`/`op item get`); and reads of `/proc/<pid>/environ`, `/etc/wireguard/**`, `/etc/amnezia/**`, `wg/awg showconf`. Allowed: names-only env output (`cut -d= -f1`, `grep -o '^P[A-Z_]*'`, `grep -c/-q/-l`, `wc`); credential files as values of `curl --cert/--key/--cacert/-K` and `ssh -i`; a secret value piped into a program's stdin (`… | ssh gw 'curl -K -'`); `kubectl get secret -o json | jq '.data | keys'`; writing a secret to a file (a `>` redirect, the owner's to grant); `describe secret` (sizes only). Over ~70k distinct Bash commands from three days of local transcripts the check removed 589 old false denies and added 285 real ones (cluster Secret/pod `-o` 177, `docker inspect` 62, `ps e` 14, `/proc` 10, `helm get` 8, the rest 14).

**Memory** (2026-11-07, from a user organization's rule): kernel and process memory hold keys, so the check also denies on the command line, including inside `ssh`, `sudo`, `docker`/`kubectl exec`: reads of `/proc/kcore`, `/dev/mem`, `/dev/kmem`, `/proc/<pid|self>/mem`, `/proc/vmcore` and crash dumps (`/var/crash/**`, `vmcore*`), core files (`core`, `core.<pid>`, `/cores/**`), and process memory dumps (`gcore`, `gdb`/`lldb` attached to a pid). Only the sanctioned script pattern below reads them, prints no memory contents, and keeps them on the host.

**Known limits** (write-ups, not yet closed): the check reasons about the command it is given, so it misses a secret hidden behind an **unknown wrapper** (`$W "…"`, a project's `vsb.sh '…'`) or **indirection** (`cat $(echo .env)`, a variable holding the path); a heredoc **written to a file through a doubly-nested `kubectl exec` → `ssh`** can be re-split and mis-flagged (an accepted false positive); **interpreter code** is matched by its string literals, so a test string naming a secret path or an env dump is denied even when it is only edited text (accepted per the "a false positive over a miss" rule — the agent writes such code to a file first). OF's own holder and presence detection runs `ps eww -o command=` through a subprocess, not as an agent's tool call, so the guard never sees it and needs no exemption (`of_architect`, 2026-11-06).

**The sanctioned pattern for reading a secret on purpose** (`of_architect`, 2026-11-06, for rotations). The guard judges the command line an agent runs, not the inside of a script file. A secret that must be read, so that it can be written straight back (a rotation) or fingerprinted, is read inside a **committed, reviewed script**, never inline on the command line. The guard cannot see inside the script, so the review is the control. The script:
- prints only key names, sizes or SHA-256 fingerprints, never values or their base64;
- uses no shell tracing (`set -x`), no `curl -v`/`--trace`, and no debug flag that echoes requests;
- prints a fixed message on error, never the object or the payload, and reads with `-o json` and parses it, so that a failed output template cannot fall back to printing the object;
- keeps no temporary files with values, or only mode-600 files removed on exit;
- is re-reviewed after any change before it runs against production again.

#### The @file-mention vector (`of_architect`/`of_platform`, 2026-11-06)

A text another session reads that places `@` directly before a path is read by Claude Code as a **file mention**: the file's content is loaded into the receiving session. This is how a Grafana token leaked on 2026-11-06 — a relayed note carried `@<path>` and the token entered the reader's context. `of_platform` confirmed the mechanism first-hand: `herdr agent prompt` delivers a relayed prompt as a **paste**; Claude Code expands an `@path` that arrives by paste; the expansion is its own transcript record of type `attachment` carrying the file content; and it is **not a tool call**, so no `PreToolUse` hook fires for it and the native `Read(...)` deny rules do not catch it.

So the control for **injected (relayed) text** is neutralizing at the relay: `herdr_deliver` turns a `@` before a path-like token into U+FF20 in everything OF types into a pane (commit `0719c7e`), and `AGENTS.md` forbids an `@` before a path in any text another session will read (`of send`, `of ask`, `of handover`, status notes). The guard adds a second layer aimed at **typed text** — a path a person types `@`-mentioned into their own session, which the relay never sees: a `UserPromptSubmit` block (`of_guard.prompt_secret_mention`, with `of_hook._mention_guard`, gated on `--deny-secrets`) rejects a prompt that names a secret path as an `@mention` and records it as `of.mandate_check {deny, critical, enforce}`.

Whether that `UserPromptSubmit` block fires **before** Claude Code expands the mention was tested by `of_lab` on 2026-11-06 at 19:16Z in its own trusted pane, with a canary file at a dot-env path and the raw `@` path pasted through `herdr agent prompt` (bypassing the relay's neutralizer). **Result: the block covers typed and pasted text.** The prompt was denied, and the session's transcript holds no `attachment` record of the file, then or in the 22 later re-submissions; the canary never reached the session (checked by `of_architect` on 2026-11-07 from the transcript, by record type, without reading the canary).

**A follow-on fault found by the same run:** a blocked prompt goes back into the input box, and every later delivery into that pane (relay messages, pulse nudges) was appended to it and re-submitted with the same `@` path, so it was denied again, 22 times in ten hours. The agent was deaf until the input was cleared by hand. The fix is in the relay: `herdr_deliver` never types into a pane whose input box is not empty (it defers, and after repeated defers it reports the pane); this also keeps a person's half-typed prompt from being appended to.

### 4.2 Mask at the boundary (`of-server`, phase 3)

- **The detector** is the gitleaks ruleset plus contextual rules:
  - EVM private keys: 0x plus 64 hex characters, next to key words;
  - passwords in URLs (`scheme://user:pass@`);
  - seed phrases: twelve or twenty-four BIP-39 words in a row.
- **Replacement**: `[secret:<kind>:<hmac8>]`. The HMAC uses the tenant key with the label `of/secret/v1`. The same secret correlates across records but cannot be guessed, even when it is weak.
- **Where it runs**: in `of-server`, before records reach organization databases. Raw, unmasked records go only to the vault.
- **The gate holds until then.** `raw_to_org=false` stays in force until the Lab passes `lab-secrets-v0` (canary secrets of every kind, plus injection text that tries to get secrets printed).
- **False positives in the masker are acceptable. False negatives are not.** The masker errs towards masking.

### 4.3 Detect and act

- **Findings.** `of.secret_detected` (kind, location, HMAC, first and last seen, organization) is written to the tenant-only store.
- **`of secrets review`** is for the owner only. It shows:
  - each unique finding with its kind;
  - for private keys, their type: SSH, TLS, EVM wallet, other;
  - its location and its masked context, never the value.

  The owner marks it as **rotate**, **false positive** or **already rotated**. Each mark is written as `of.secret_reviewed`.
- **Wallet keys are classified by on-chain state, using addresses only**: funded / used / never used. A funded key is rotated at once, by moving the funds first. A used key is rotated. A never-used key is checked for off-chain use (signing, authentication) before it is marked a false positive.
- **Rotation priority:**
  1. wallet keys and anything that moves money;
  2. cloud administrator and AWS keys;
  3. database and service passwords;
  4. API tokens.
- **Every rotation is done by the position that owns the system**, with the owner's yes. The structure keeper routes each item to that position. A secret that reached the model provider counts as exposed, regardless of the provider's retention terms.
- **Continuous scanning.** The shipper scans new records. A new high-signal finding raises an alert to the owner within minutes.

### 4.4 History

- The archive and the vault keep the unmasked history; only the tenant reads them.
- Organization databases receive the masked version once masking is live.
- **Rewriting history is not done.** Harness transcripts under `~/.claude*` stay as they are. Their protection is the operating system (FileVault, file modes) plus prevention from now on.
- The owner may later ask to scrub specific secrets from the local harness transcripts. This is a separate, explicit operation with a backup.

## 5. Rollout

| Step | What | Owner | State |
|---|---|---|---|
| 1 | Scan history (gitleaks plus contextual rules); totals without values | `of_platform` | done |
| 2 | `of secrets review`; the owner marks findings; a rotation list per system | `of_platform` builds; **owner reviews** | being built |
| 3 | Rotation of private keys and AWS keys first, by the owning positions | system agents, with the owner's yes | waits for step 2 |
| 4 | Prevention: deny rules, the critical deny grant, secrets by reference, the `AGENTS.md` rule | `of_platform`, each organization's structure keeper | with phase 2 of RFC 0001 |
| 5 | Masking in `of-server`; `lab-secrets-v0` passes; then the gate may open | `of_platform`, `of_lab` | phase 3 of RFC 0001 |
| 6 | Continuous scanning and alerts | `of_platform` | after step 5 |

## 6. Open questions for the founder

1. When do you want to go through the review (step 2)? It is the only way to know whether the 12 private keys include wallet keys.
2. Secrets by reference: is 1Password (`op run`) the standard for every organization, or is there another secret manager?
3. Should old transcripts under `~/.claude*` be scrubbed of confirmed secrets after rotation, or left as they are (protected by FileVault)?
