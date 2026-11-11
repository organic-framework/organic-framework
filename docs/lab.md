# The Lab

Status: **Accepted as the basis**, 2026-11-05, by `of_architect`, with the amendments below folded in. Written by `of_lab`. The host is the founder's decision (section 9).

The Lab is the R&D part of Organic Framework that runs the whole stack in disposable, isolated machines ([`framework.md`](framework.md), parts table). This note designs it: the machine, the interface, the scenario format, the owner persona, mock systems, authentication and budget, watching a run, and teardown. It ends with the host options for the founder and the plan for the first milestone, the hook testbed.

Related:
- what an environment is and the three levels of fidelity: [`environments.md`](environments.md);
- how documents become computation (checks, projections, watchers, judgements): [`computation.md`](computation.md);
- the event log the Lab verifies: [`events.md`](events.md); "Capture the maximum" in [`framework.md`](framework.md);
- the stages and cases a scenario is set at: [`business-case.md`](business-case.md);
- the observations a run is scored on: [`success.md`](success.md).

## Principles

1. **Lab machines are VMs on the founder's Mac, by his decision** (2026-11-05; section 9). They are isolated from the Mac's data: no shared folders, no forwarded agent, no route to the Mac's services except the model gateway. Each VM has capped CPU and memory. Builds and heavy jobs outside the Lab still do not run on the Mac.
2. **Synthetic data only.** No real organization's data or credentials ever enter a lab machine. An Acme-like scenario is built from shapes and statistics, never from copied history. The rule holds for lab machines and for the `lab/` code in this repository. Replay of a real organization's history is the one exception, and it never touches either: see "In-tenant replay" below.
3. **One machine per run, thrown away after it.** A run never reuses a machine. Nothing in a lab machine is worth keeping except the run bundle that leaves it.
4. **Same code as production.** The lab runs the real `of` CLI, the real hooks and the real harnesses at pinned versions. Mocks replace the *world* (systems, the market, the owner), never Organic Framework itself.
5. **Everything is recorded.** A run's bundle is its evidence: manifest, event log, raw tier, model traffic, score. A result without its bundle does not count.
6. **Every result states its level** (tabletop, replay, live sandbox; [`environments.md`](environments.md)) and never replaces the Acme proving ground (FPF OPS.11).

## Parts

| Part | What it is | Where it runs |
|---|---|---|
| **Controller** | `of lab`: creates and destroys machines, drives the RL-shaped interface, collects bundles, runs the reaper. Light: API calls and `ssh`. | anywhere light; v0 from the founder's Mac or a lab position's pane |
| **Image** | The pinned software of a lab machine, built from a script in this repository | a host snapshot |
| **Machine** | One disposable VM built from the image, one per run | the chosen host |
| **Scenario** | A directory that defines an organization at a stage, its pressures, its owner persona and its success observations | this repository, `lab/scenarios/` |
| **Lab code** | The runner (`of lab`), the image build, the in-machine agent, the mocks and the scenarios. A framework part: it becomes open source with the framework | this repository, `lab/` |
| **Owner persona** | A simulated owner that answers agents the way the founder does | inside the machine, as a process outside the agents' sessions |
| **Mock systems** | The world around the organization: git, CI, market, regulator | inside the machine |
| **Model gateway** | The only path from a machine to model providers; holds the provider key and the budget, logs the traffic | outside the machine |
| **Run store** | Where run bundles are kept after teardown | outside the machine |

## 1. The environment machine

### What a machine is

A **VM, not a container on a shared host.** The hook testbed must see what a real Workplace sees: a real TTY, a herdr server with real panes, Claude Code's own process tree, its config directory and transcripts, `~/.local/state/of/` with real file modes and locks. A VM gives that with no special cases. Mock systems may run as containers *inside* the VM later; that keeps the blast radius at the VM.

### Image contents (v0)

| Layer | Contents | Pinned by |
|---|---|---|
| OS | Debian 13 or Ubuntu 24.04 LTS, minimal; a non-root user `lab`; no inbound access except SSH from the controller's key | image id |
| Runtime | herdr (same version as the founder's, today 0.9.5) as a headless server with the Claude integration installed | version |
| Harnesses | Claude Code (native installer, a pinned version, autoupdate off); Codex CLI later | version |
| Organic Framework | this repository at a commit: `bin/of`, the hooks, `of_events`, `of_archive`; Python 3.14 (for `compression.zstd`) and PyYAML | git commit |
| Hooks | the global `settings.json` of the lab's Claude config directory, with `of hook <HookEvent>` for every kind in [`events.md`](events.md) | file hash |
| Lab agent | `of lab agent`: the in-machine side of the interface (pressure clock, persona driver, mock control, observe) | git commit |
| Mock systems | see section 5 | versions |
| Organization | an organization repository generated from the scenario, plus `~/.config/of/orgs.yaml` naming it. Each run is its own organization `lab-<run id>` of the separate tenant `lab` (`of_architect`, 2026-11-05) | scenario hash |

The image is built by one script (`lab/image/build.sh`, cloud-init friendly) on the host, never on the Mac, and saved as a snapshot. A run starts from the snapshot in about a minute; only the scenario layer is applied per run. The **image id** is part of every run's manifest.

## 2. The RL-shaped interface and reproducibility

The interface follows the Gymnasium shape, so the same environment serves testing, the framework's continuous integration and goal 2.

| Call | What it does | Returns |
|---|---|---|
| `reset(scenario, seed)` | create a machine from the image; apply the scenario (organization repository, registry, briefs, mocks, persona); start herdr and the initial agent sessions | `run_id`, the first observation |
| `step(action=None)` | advance the scenario clock to the next pressure or for a time slice; deliver due pressures; let agents and the persona work until the slice ends | observation, reward, `terminated`, `truncated`, info |
| `observe()` | read without advancing: the event log, the organization model (its repository), herdr state, mock state | observation |
| `score()` | run checks and projections over the event log and the model into the scenario's success observations ([`computation.md`](computation.md)) | a score record |
| `close()` | collect the run bundle, then tear down (section 8) | the bundle path |

Mapping to RL terms:
- **The environment** is the world: the scenario clock, the pressures, the mock systems and the owner persona.
- **The policy** is what is under test: Organic Framework plus the agents running under it. For framework CI the policy is fixed and the framework version varies; for goal 2 the agents are what learns.
- **A step is a scenario tick, not a token.** Agents run freely inside a step; the environment only decides when pressures arrive and when to look.
- **Reward** is a scalar derived from `score()`. For CI the full score record matters more than the scalar.
- **`terminated`** when the success observations are reached or the script is exhausted; **`truncated`** when the run hits its budget or wall-clock limit (section 6).
- `action` is reserved: in v0 it is always `None`. Later it lets an outside learner act as the owner, or inject a reorganization mid-run.

### Reproducibility

Every run writes a **manifest** before it starts:

```yaml
run_id: 2026-11-05T18-02-11Z-lab-hooks-v0-s42
scenario: {id: lab-hooks-v0, hash: sha256:…}
seed: 42
image: {host: <host>, id: <snapshot id>, build_commit: <git sha>}
framework: {commit: <git sha>, dirty: false}
harness: {claude-code: 2.1.318}
runtime: {herdr: 0.9.5}
models: {agents: claude-opus-5-5, persona: claude-sonnet-5-5}
persona: {id: founder-v0, calibration: <report ref>}
mocks: {git: <version>, ci: <version>, market: <version>}
limits: {usd: 5, wall_clock: 45m}
level: live-sandbox
```

Results are reported as **pass^k** (the share of seeds on which all k repeated runs succeed, as in tau-bench), so reliability is visible, not only the best case.

What the seed controls: pressure timing jitter, mock outcomes (which CI runs fail), persona sampling choices that the persona makes in code (latency, whether it batches). What it cannot control: model sampling. Two consequences:
- **A run is reproducible in its inputs, not its outputs.** Claims about outcomes are made over N runs per seed set, with spread reported.
- **A run is replayable exactly** from its bundle: the gateway records every model request and response, so a replay can serve recorded responses instead of calling the model. This also makes the replay level ([`environments.md`](environments.md)) cheap for lab runs.

## 3. The scenario format

A scenario is a directory. Its header is a typed document in the sense of [`computation.md`](computation.md), so `of check` can validate the scenario itself.

```
lab/scenarios/<id>/
  scenario.yaml      # header: id, version, stage, level, what it stresses, limits
  org/               # the initial organization repository: registry, briefs, AGENTS.md, docs
  agents.yaml        # which sessions start: position, harness, model, initial prompt
  systems.yaml       # mock systems and their seeded behaviour
  pressures.yaml     # the script of pressures over time
  persona.yaml       # the owner persona: card and calibration reference
  success.yaml       # observations: checks and projections with thresholds
  README.md          # the story of the scenario, for people
```

**Pressures** are timed or triggered. Each names a kind, a target and a payload; kinds are a small closed list that grows with the Lab:

```yaml
pressures:
  - at: 0m
    kind: owner_request            # the persona types a request to a position
    to: acme_platform
    text: "Add a health endpoint to the billing service and ship it today."
  - after: {event: of.agent_question, from: acme_platform}
    kind: owner_answer_policy      # how the persona treats this ask
    policy: answer_in_batch
  - at: 20m
    kind: incident                 # a mock system changes state
    system: ci
    effect: {fail_next: 2}
  - at: 30m
    kind: obligation               # a regulator sets a deadline
    stakeholder: regulator
    due: 50m
    consequence: "licence warning"
  - at: 35m
    kind: capacity                 # an account limit is hit
    effect: {suspend: acme_research, reason: budget}
```

**Success observations** reuse the computation kinds: a check over the final model, a projection over the event log, or an agent judgement with its criteria. Each has a threshold and says whether it is required.

```yaml
observations:
  - id: shipped
    projection: work_records(position=acme_platform, merged=true) >= 1
    required: true
  - id: owner_load
    projection: human_prompts(origin=human) <= 6
  - id: obligation_met
    watcher: obligation(regulator).filed_before_due
    required: true
  - id: no_dead_end_asks
    projection: asks(open_for > 15m) == 0
```

**Alignment with existing environment formats.** The layout borrows from three conventions so lab scenarios can later be published as an Org-Bench without a rewrite:
- **Harbor / Terminal-Bench**: a task is a directory with instructions, an environment definition and tests that write `reward.json`. Our `success.yaml` compiles to such tests; `score()` writes `reward.json` with one component per observation.
- **tau-bench / tau2-bench**: the simulated user is part of the environment (`user_scenario` with persona and instructions, `evaluation_criteria`). Our `persona.yaml` plays that role for the owner.
- **SWE-bench**: for code pressures on the mock git repositories, the grading contract is a pair of test lists (fail-to-pass, pass-to-pass) at a base commit.

Where scenarios live: in this repository (`lab/scenarios/`), with the rest of the lab code, and they become open source with the framework (`of_architect`, 2026-11-05). So every scenario is synthetic or anonymised **before** it enters `lab/`. The S2 "Acme-like" scenario is built with `acme_org` from anonymised shapes (the count of positions, the system breakdown's topology, the baseline's rates) and contains nothing that identifies the company: no names, code, data or text. Anything that cannot be anonymised does not become a scenario.

The first set is the library in [`environments.md`](environments.md); the hook testbed adds `lab-hooks-v0` (section 10).

## 4. The owner persona

The owner is the constraint ([`success.md`](success.md)); a scenario that cannot simulate him cannot test attention, asks or onboarding.

**What the persona is.** A program with a model behind it. It runs inside the machine as a process, outside every agent session, and talks to agents only the way the founder does: by typing into their panes (`herdr agent prompt`), answering their questions, and reading the desk. Every prompt it types is declared first through `of event injected_prompt` with `injector: owner_persona`, so the event log can always tell the persona from a real human.

**The persona card** (`persona.yaml`):
- goals and preferences for the scenario (what "done" means to this owner);
- hidden knowledge: facts only the owner knows, released only when asked (so needless and missing asks become measurable);
- the attention budget: how often the persona looks, how many messages per hour, batch size;
- response behaviour: latency distribution from ask to answer, the share of bare yeses, corrections, "over-engineered" strikes, how it treats a question it considers needless;
- language and style (the founder writes Russian, short, often dictated).

**Calibration.** `of_field-analyst` owns it, from the founder-load baseline (Acme org repo, `reports/Founder load baseline 2026-11-05.md`) and the flow-quality report:
1. Fit the card's numeric parameters to the founder's measured distributions: ask-to-verdict delay (p50, p90), bare-yes share, correction share, message length, plumbing share, batch behaviour.
2. Validate on a held-out period: run the persona through a replay of that period's asks and compare the distributions.
3. A discriminator test: `of_field-analyst`'s classifiers should not separate persona answers from the founder's better than chance on the measured features.
4. Version the result (`founder-v0`, …) with the report that justifies it. A run names its persona version.

**Data rule.** Only aggregate statistics and synthetic examples enter the lab. The founder's own prompts are real organization data and never go into a persona card or a lab machine.

**Personas beyond the founder.** The same card describes other owners for other scenarios: the S0 solo founder, a cautious enterprise owner, a colleague joining (S2→S3). They are uncalibrated until we have their traces, and the results say so.

## 5. Mock systems

Mock systems are the world the organization works on. They are small, seeded and observable; each writes its own state changes as events with `of.source: lab` so scoring can see the world, not only the agents.

**Every event of a run** (hooks, `of`, mocks, persona) also carries `of.lab.run_id`, and its `of.org` is `lab-<run id>`. Both attributes are an additive change to [`events.md`](events.md), agreed by `of_architect` on 2026-11-05 and added by `of_platform`.

| Mock | v0 form | Seeded behaviour |
|---|---|---|
| **Git** | bare repositories on the machine, served over SSH or a local Forgejo | initial content from the scenario |
| **CI** | a `post-receive` hook that runs a scripted check and posts a status; merge on green | which runs fail, how long they take |
| **Pull requests** | a minimal PR API over the git mock (open, review, merge), enough for `pr-link`-like work records | review delays |
| **Market** | a process that emits `of.t` events per lane on a schedule; a deployment can move T by a scripted ΔT | the T curve, the effect of each change |
| **Regulator** | pressures of kind `obligation` with deadlines and consequences | dates |
| **Capacity** | a governor that suspends a session (SIGSTOP) and declares it with `of event state_declared` | when limits hit |
| **Stub harness** | a fake agent that replays a recorded hook-payload sequence into `of hook`, at no model cost | the sequence |

The stub harness matters for cost: most hook and projection regressions can run without a model at all. Real harnesses are used where the question is how the real harness behaves.

## 6. Authentication and budget

**No real credentials enter a lab machine.** Specifically: none of the founder's Claude accounts, no Acme keys, no GitHub tokens, no SSH keys that open anything outside the lab.

**Model access goes through a gateway** outside the machine:
- The provider key lives only in the gateway. It belongs to a separate Anthropic Console workspace for the Lab, with its own monthly spend limit.
- Each run gets a **virtual key** that is valid only for that run, with a USD cap and an expiry. The machine sees `ANTHROPIC_BASE_URL` and that virtual key, nothing else. A leaked virtual key is worth at most one run's budget, and it dies at teardown.
- The gateway **logs every request and response**. This is the **first trial of the harness-side proxy** for exact model traffic that [`framework.md`](framework.md) lists as a later option (`of_architect`, 2026-11-05). It is tried here with synthetic data, and its log is written in the raw tier's format ([`storage.md`](storage.md), by `of_platform`), so the result comes back to `of_platform` as a candidate raw-tier source for real Workplaces.
  - Shape (agreed with `of_platform`, 2026-11-05): request and response bodies go to content-addressed zstd blobs; one row per request and response pair goes to a `model_traffic` table: `received_at`, `run_id`, `session_id` if known, `model`, `request_sha256`, `response_sha256`, `latency_ms`, input, output and cache tokens. `of_platform` adds the table after the first real sample, so its columns follow real data.
  - Lab data goes to its own tenant and database, never to the founder's (`of_tenant_acme`).
- Our proposal for v0 is in section 9 (which gateway, and a simpler fallback for the first run).

**Budget** has three levels:

| Level | Limit | On exceed |
|---|---|---|
| Run | USD cap (gateway), wall-clock cap (controller), host lifetime (reaper) | the run is `truncated`, the bundle is still collected |
| Suite | sum of run caps; the controller refuses to start a run over it | refuse |
| Month | the Console workspace spend limit and the host account's budget alert | the provider refuses; the founder is notified |

The persona's model calls go through the same gateway with their own virtual key, so the owner's cost and the agents' cost are separate in the score.

**Access to the machine** is by a fresh SSH key pair per run, generated by the controller and deleted at teardown. The founder's own SSH key is not installed.

## 7. How the founder watches a run

herdr shows remote machines in the same window (organic-framework-build `reports/framework/herdr as the runtime for Organic Framework.md`):
- At `reset`, the controller runs a herdr server on the machine and saves it as a herdr machine on the founder's side (`herdr machine add lab-<short run id>`). The lab machine appears in the sidebar with its workspace named after the scenario's organization.
- The controller drives the run through `herdr --machine lab-… agent prompt …`, the same path the persona uses inside.
- The founder can watch any pane live, and can type into one. A run the founder typed into is marked `interactive: true` in its manifest; its owner-load numbers then count him, not the persona.
- At `close`, the controller removes the machine profile.

Lab runs stay out of the founder's `of who` and desk with no special case: each run is an organization of the separate tenant `lab`, which the founder's `orgs.yaml` does not list. `of who --org lab-*` shows them on request (`of_architect`, 2026-11-05).

## 8. Teardown and data handling

**The run bundle** is collected before anything is destroyed:
- `manifest.yaml` (section 2);
- `events.jsonl` from the machine;
- the raw tier: every transcript and the raw hook payloads, zstd-compressed (`of archive`);
- the gateway's request and response log for the run's virtual keys;
- herdr's state and logs, the mock systems' event streams;
- `score.json`, and the hook testbed's report when it is that scenario.

**Teardown, in order:** collect and verify the bundle (checksums) → revoke the run's virtual keys → delete the machine through the host API → delete the SSH key pair → remove the herdr machine profile → write `lab_run_closed` with the bundle's hash.

**The reaper.** Every machine carries labels `of-lab=1`, `run=<id>`, `expires=<time>`. The controller runs a reaper on each call (and on a schedule once there is a lab position's continuation to hold it) that deletes any expired machine and revokes its keys. A forgotten machine costs at most its lifetime.

**Data.**
- Lab data is synthetic, so a bundle may be kept, shared and later published as part of an Org-Bench, unlike real raw data ([`framework.md`](framework.md)).
- Before `reset`, the controller scans the scenario directory with the event log's masker patterns ([`events.md`](events.md), Secrets) and refuses a scenario with a hit.
- Bundles go to the run store: v0 is a directory outside every git repository on the machine that ran the controller (`~/.local/state/of/lab/runs/<run_id>/`); an object store bucket once runs are frequent. Bundles never go into this repository.

## 9. Host options (for the founder's decision)

Prices are list prices without VAT, checked 2026-11-05 from public sources; "≈" marks figures we could not confirm on the provider's own page.

| | **A. Hetzner Cloud, one VM per run** (recommended) | **B. A managed sandbox service** (E2B, Morph Cloud) | **C. Acme's runners** |
|---|---|---|---|
| What it is | A real VM from our snapshot per run, in a separate Hetzner project `of-lab` with its own API token | Firecracker microVMs from a provider's API, snapshot and fork built in | Spare pods on Acme's backtest runners (w6, runner-0, w7–w16) |
| Machine cost | CX33 (4 vCPU, 8 GB) €0.0136/h, capped at €8.49/month; CPX32 €0.057/h if CX is not orderable. Hourly billing, rounded up | E2B: about $0.33/h for 4 vCPU and 8 GB, per second; runs over 1 h need the $150/month Pro plan. Morph: no public rates | about €0 marginal |
| A hook testbed run (≤ 1 h) | €0.02–0.06 for the machine | ≈ $0.35, plus the plan | €0 |
| Fixed cost | a small permanent `lab-gateway` VM for the model gateway, CX23 ≈ €5/month | the same gateway, elsewhere | the same gateway, elsewhere |
| herdr attach and live watching | yes: a plain SSH machine | unproven: no native SSH on E2B; Morph has SSH | yes |
| Fidelity | full: systemd, real TTY, real file system | high, with provider-specific limits on session length and idle | full |
| Scaling to tens of runs | fine to about 10 in parallel; beyond that a dedicated server with microVMs | built for it: fork N copies from one snapshot in under a second | competes with backtests |
| Isolation | strong: own VM, own project, no network path to anything real | strong | **weak**: shares the network with Acme systems; service tokens and cloud metadata are reachable unless locked down |
| Key risks | Hetzner raised prices twice in 2026; CX availability is patchy; no nested virtualization for microVMs later | vendor lock-in and opaque pricing (Morph); session limits; herdr over SSH must be proven first | breaks the "no real secrets" rule in practice; takes runner slots from backtests; needs `runners_coordinator` and `acme_org`, and the Acme herdr move is on hold |
| Who approves | the founder (a new Hetzner project and a payment method) | the founder (a new vendor) | the founder and Acme's runner keepers |

Model cost is separate from the host and the same for every option. It dominates the bill.

| Run | Machines | Models (estimate) | Cap |
|---|---|---|---|
| Hook testbed (two sessions on Sonnet 5.5, scripted, ≤ 1 h) | €0.02–0.06 | $1–3 | $5 |
| **Level-1 suite, 3 scenarios** (S0, S1, S2; one Opus 5.5 agent session per scenario applying the onboarding path and the six cases, plus a Sonnet 5.5 owner persona; about an hour each) | €0.05–0.20 (3 VM-hours) | about $7 per scenario, $4–15 range; **about $20–25 per suite** | $50 |
| The same suite with Sonnet 5.5 agents | the same | about $10–12 per suite | $25 |

The estimate assumes about 80 model calls per scenario at an average 80K-token context, 90% of it read from cache, and 2K output tokens per call, at list prices (Opus 5.5 $4 in, $20 out, $0.20 cache read per million tokens; Sonnet 5.5 $2, $10, $0.20). The first real runs replace it with measured numbers from the gateway.

**Decision (founder, 2026-11-05): lab machines run on the founder's Mac.** Asked to choose between A, B and C, he answered "run them on my Mac". This overrides our recommendation and the Mac rule for the Lab only. What it means and how it is kept safe is in "The Lab on the founder's Mac" below. A and B stay the options for when runs need to go parallel or the Mac cannot carry them.

Our recommendation before the decision was **A now, B later.** Hetzner gives the hook testbed a real machine the founder can watch in herdr, for cents a run. When scenario suites need tens of runs in parallel, we try B (Morph's branching fits RL best) or a dedicated Hetzner server running microVMs, and decide on evidence. C is listed because it is free, but it is the one option where a lab agent could reach something real.

A side question for the founder under A: bill the `of-lab` project to the Acme Hetzner account (projects keep resources and tokens apart) or open a separate account. The capacity budget is shared today ([`framework.md`](framework.md), "How we build it"), so either works; a separate account is cleaner once Organic Framework has its own entity.

### The Lab on the founder's Mac

The machine: Apple M4, 10 cores, 32 GB of memory, 117 GB of free disk (2026-11-05). Lima 2.2.1 is already installed.

**How machines run.** Each lab machine is a Lima VM using Apple's Virtualization framework (`vmType: vz`), started from a pinned Ubuntu 24.04 arm64 image and provisioned by `lab/image/` from this repository. The controller creates it at `reset` and deletes it at `close` (`limactl delete`). The reaper deletes any `of-lab-*` instance past its expiry.

**Isolation from the Mac.** The Mac holds real data and keys, so the default Lima settings are not acceptable:
- **No mounts.** Lima mounts the home directory by default; lab machines use `mounts: []`. Files go in by `limactl copy` and come out the same way.
- **No SSH agent forwarding, no port forwards to the Mac**, no Rosetta, no containerd.
- **Automation never touches the founder's 1Password SSH agent** (founder's rule, 2026-11-05). `oflab` unsets `SSH_AUTH_SOCK` and runs git with `GIT_CONFIG_GLOBAL=/dev/null`. Lima's ssh uses `-F /dev/null` and its own key, so it never reads `~/.ssh/config`. Lab sessions on the Mac run without an agent socket and with a lab gitconfig that does not sign commits. They also run without the parent pane's herdr variables (`HERDR_ENV`, `HERDR_PANE_ID`, `HERDR_SOCKET_PATH` and the tab and workspace ids) and without the parent Claude session's own variables. Otherwise herdr's integration rebinds the founder's pane to the test session (`of_platform`'s incident, 2026-11-05). The first testbed run broke this rule once, with a signed `git commit` in a toy repo, and that is why the rule is written here.
- **Default-deny egress during runs.** Once provisioning is done, and before any agent starts, root installs an nftables policy in the VM (`lab/image/lockdown.sh`). It drops everything leaving the VM except the model gateway on the Mac (one IP and port).

  That blocks:
  - the Mac's loopback services: ClickHouse on 18123 and 19000, `of-server`, `kubectl` port-forwards, the local SOCKS proxy;
  - the Mac's LAN;
  - **the Mac's VPN routes**: the Mac routes 10.x networks and a default route through a VPN tunnel, and NAT would otherwise carry a VM's traffic into Acme's infrastructure (`of_architect`'s condition, 2026-11-05);
  - DNS;
  - the public internet.

  Agents run as the user `lab` without sudo, so they cannot lift the policy. Mock systems run inside the VM, so a run needs nothing else.
- **Provisioning** (packages, herdr, Claude Code, Python) happens before the lockdown, while no agent exists.

**Limits** (`of_architect`'s condition, 2026-11-05: production agents come first).
- 2 vCPU, 4 GB of memory and a 20 GB disk per VM.
- **One VM by default.** Three only for `lab-profile2-v0`, `lab-secrets-v0` and `lab-routing-v0`, and only when there is headroom.
- **Preflight before every start** (`lab/bin/oflab preflight`): free memory at least 30%, load below 70% of the cores, and the number of running lab VMs under the limit. Otherwise the start is refused.
- **Under memory pressure the VMs are paused** (`oflab stop-all`, which is `limactl stop`); they do not compete with production agents. The resource guard on the Mac may also lower a busy VM's priority.

**The model gateway on the Mac.** v0 is a small stdlib proxy, `lab/gateway/oflab_gateway.py`, not LiteLLM: LiteLLM's virtual keys need a Postgres, and the only Postgres on the Mac holds real data. The proxy does the same job: per-run keys with a USD cap and an expiry, the real key swapped in from the Keychain, every request and response recorded as zstd blobs plus a `model_traffic` row, and error injection for the testbed. It listens on the Mac's loopback only and is reachable from the VMs through the one port the firewall leaves open. It holds the Lab's Anthropic key, which the founder creates once in a separate Console workspace `of-lab` with a monthly spend limit. The key lives in the Mac's Keychain and never enters a VM. VMs get per-run virtual keys as designed in section 6.

**Watching.** Each VM runs a herdr server; the controller adds it as a herdr machine over Lima's SSH, so it shows in the founder's sidebar as designed in section 7.

**Egress profiles** (`lab/image/`), chosen per scenario:

| Profile | Allows | Used by |
|---|---|---|
| `lockdown.sh` (none) | only the model gateway on the Mac | the hook testbed, `capacity-move-v0`, `lab-secrets-v0` on profile 1 |
| `lockdown-internet.sh` | the public internet | `lab-quickstart-v0`: the subject downloads things |
| `lockdown-labnet.sh` | the other lab VMs on the lab subnet, and the public internet (packages, images) | `lab-profile2-v0`, `lab-secrets-v0` on profile 2 |

Every profile drops:
- the Mac (the host gateway, except DNS) and its LAN;
- the other private ranges;
- link-local;
- the public addresses the Mac routes through its VPN tunnel.

`oflab vpn-routes` lists the VPN-routed addresses, normalised to CIDR: macOS prints short forms such as `172.64.128/20`, which nft rejects. `lockdown-labnet.sh` replaces only its own nft table, so k3s keeps its rules, and it repeats the drops in the forward chain for pod traffic.

**Lessons from the first runs (2026-11-05 and 06):**
- **VM-to-VM networking needs Lima's `user-v2` network** (`lab/image/lab-net.yaml`); macOS 27's vmnet shared mode (`vzNAT`) isolates the VMs from each other.
- **Apply the lockdown detached, never from the session you keep open** (of_server, 2026-11-07). `lockdown-labnet` is egress-only with `ct state established,related accept` first, so it does not cut an already-established connection — but conntrack only starts tracking when the `oflab` table is created. A control session opened *before* the table exists (the `limactl shell` / `ssh` running the script) is not yet tracked, so its next outgoing packet — sshd sending the command's exit status back to the host gateway — is seen as NEW and dropped by `ip daddr <host_gateway> drop`, and so is every retransmit; the shell then hangs indefinitely (ten hours once, twenty hours once). Redirecting the output does not help. Run it detached, e.g. `limactl shell VM sudo systemd-run --unit oflock sh -c 'sh /tmp/lockdown.sh ARGS > /tmp/lockdown.log 2>&1'`, and wrap every later `limactl shell` to that VM in `timeout`. The node itself stays manageable after the policy (a fresh `limactl shell` returns 0), so no management allowlist is added; verification from the client VMs is still the better evidence.
- **Right after a restart, `user-v2` may have no internet** for about a minute; checks retry before failing.
- **Before the lockdown, a VM could reach** both the Mac's ClickHouse and the Mac's VPN networks. The isolation probes (`lab/testbed/isolation.sh`) run before anything else.

**Models for lab runs.** The founder decided on 2026-11-05 and 06:
- there is no separate lab API key;
- lab personas use the founder's example account, headless and isolated: no user settings or hooks (`--setting-sources project`), no MCP servers, no saved session, a budget cap per run, and the run directory as the working directory;
- the account's credential never enters a lab VM. A persona acts on a VM only through a wrapper that runs commands there.

The gateway below stays the design for when the Lab gets its own key.

### The model gateway (our proposal)

- **v0: LiteLLM proxy on the `lab-gateway` VM.** It holds the Lab's Anthropic key (a separate Console workspace with a monthly spend limit), mints a virtual key per run with `max_budget` and an expiry, and stores every request and response in its own database. Claude Code reaches it through `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` (documented in Claude Code's LLM gateway page). The same VM can later host the controller and the run store.
- **Fallback: Cloudflare AI Gateway.** Managed, holds the key (BYOK), has dollar spend limits and request logs. Fewer moving parts, but per-run keys and full-body retention are weaker, and the traffic log lives with Cloudflare.
- Not a subscription token (`claude setup-token`): it is the founder's account, its limits are the organization's capacity, and it is a real credential.

## 10. The first milestone: the hook testbed

Goal: run the real `of hook` collector under a real Claude Code session on one disposable machine, check every event kind of [`events.md`](events.md) and the raw tier, tear down, and report what differs from what `of_platform` assumed.

### Steps

1. `reset(lab-hooks-v0, seed)`: one machine; herdr server; the lab Claude config directory with the hooks installed; a two-position organization (`lab_alpha`, `lab_beta`) in the registry, with `holder.session` filled at start so positions resolve.
2. Two Claude Code sessions in herdr panes, `lab_alpha` and `lab_beta`, interactive (not `-p`), so the hooks fire as on a real Workplace.
3. The controller drives a fixed script through `herdr --machine … agent prompt` and pane input (section "Script" below).
4. `observe()`: pull `events.jsonl`, `hook-errors.log`, the session caches, the transcripts and the raw tier.
5. `score()`: run the expectations table below.
6. `close()`: bundle, teardown, report.

### Script and expected events

| # | Driver action | Expected events |
|---|---|---|
| 1 | start both sessions | `session_start` (`source: startup`, model, title) ×2, position and org resolved, `of.workplace` filled |
| 2 | prompt `lab_alpha` with a plain request | `human_prompt` (`origin: human`, `channel: herdr`) then `turn_end` with `last_message` |
| 3 | the driver declares an injection, then types | `injected_prompt`, then `human_prompt` with `origin: injected` |
| 4 | a prompt containing a fake token (`sk-ant-test…`) | `human_prompt.text` shows `[masked:anthropic_key]` |
| 5 | ask `lab_alpha` to use AskUserQuestion; the driver answers | `agent_question` (`detected_by: tool`), herdr state `blocked`, then `agent_question_answered`, state `working` |
| 6 | a turn whose last message asks a question | `turn_end` plus `agent_question` (`heuristic`, `q-v1`); the next prompt carries `reply_to` |
| 7 | a session with permission prompts on: a tool call the driver approves, one it denies; then the same in auto mode | `permission_request` ×2, herdr `blocked` then back; `permission_denied` only from the auto-mode denial (see below) |
| 8 | `lab_alpha` sends a message to `lab_beta` | `agent_message_out` (`to_position: lab_beta`, `delivery`) and `agent_message_in` on `lab_beta` |
| 9 | leave a session idle | `attention` (`idle_prompt`) |
| 10 | `/compact` | `compaction` (`manual`) |
| 11 | provoke a model error (gateway returns an error for one request) | `turn_error` |
| 12 | start a new session in `lab_beta`'s pane | `holder_changed` with evidence |
| 13 | a subagent call | events carry `of.subagent`; herdr state not changed by the subagent |
| 14 | `of desk once`, `of go lab_beta`, `of go nobody` | `attention_routed`, `name_resolution` (resolved, none) |
| 15 | the capacity mock suspends `lab_beta` | `state_declared` (`suspended`, then `resumed`) |
| 16 | end both sessions | `session_end` ×2 |
| 17 | `of archive` | `raw_archived`; every transcript is in the raw tier, and every event's `of.transcript` offset points into it |

**First assertions, before any event check: isolation.** From inside the VM as the user `lab`, each of these must fail:
- connecting to the Mac's ClickHouse (18123 and 19000) and other loopback ports through the host gateway;
- connecting to the Mac's LAN gateway and to an address on each of the Mac's VPN routes (10.x);
- DNS resolution;
- an HTTPS request to the public internet;
- `sudo`.

The model gateway port must succeed. A run whose isolation check fails stops there.

Every event is also checked against the record schema: required attributes present, `of.schema: 1`, UUIDv7 ids in time order, payload ≤ 8 KB, file modes 700/600, no line torn under concurrent writes, and hook latency (time from the harness's hook start to the append): every hook under the 200 ms hard limit, with the typical value (about 50 ms expected) reported.

### What the documentation already contradicts

A first pass over the Claude Code hooks reference (code.claude.com/docs/en/hooks, read 2026-11-05) disagrees with the collector in places. The testbed confirms each on the real harness before `of_platform` changes anything:

- **PermissionDenied fires only in auto mode**, not when a person denies a dialog. A manual denial leaves no `permission_denied`, so the open ask is not closed by it; [`events.md`](events.md) assumes it is. Row 7 above then expects `permission_request` ×2 and no `permission_denied`.
- **PostToolUseFailure has no `tool_response`**; it carries `error` and `is_interrupt`. A failed `SendMessage` loses its error text in the collector today.
- **PermissionRequest has no `tool_use_id`**, so a request cannot be paired with its later tool call by id.
- **More values than the collector knows:** SessionStart `source` can be `fork`; Notification types include `agent_completed`; StopFailure adds `error_details`.
- **Hooks the collector does not use:** SubagentStart, SubagentStop (with the subagent's transcript path), PostCompact (with the summary). Common fields `permission_mode`, `prompt_id` and `effort` are on every hook and are not recorded.
- **`idle_prompt` fires about 60 seconds** after a stop with no input; row 9 waits for it.
- **Headless mode is not a substitute.** In `claude -p`, several hooks are undocumented (Stop, PreCompact, Notification), AskUserQuestion needs a permission host, and `--bare` disables hooks entirely. This is why the testbed drives interactive sessions through herdr. The Agent SDK runs the same `settings.json` hooks and can act as a scripted user; it is the fallback driver if herdr input proves unreliable.
- **OpenTelemetry export does not carry hook events**, so it cannot verify the collector; it is a useful second record of prompts, tool decisions and API requests to compare against.

### Assumptions to confirm or refute

These are read from `bin/of_hook.py` and [`events.md`](events.md); the report states each as confirmed, refuted (with what the harness actually does) or untestable:

- `of.harness.version` is the basename of `CLAUDE_CODE_EXECPATH` (true for the native installer on the Mac; unknown elsewhere);
- SessionStart carries `model` and `session_title`;
- Stop carries `last_assistant_message`;
- PermissionRequest and PermissionDenied fire, and how they behave under each permission mode;
- Notification carries `notification_type`, and `idle_prompt` fires in an interactive session;
- AskUserQuestion's PostToolUse carries the answers under `tool_response.answers`;
- a peer message arrives as a prompt starting with `<cross-session-message` with `from-name`;
- a `SendMessage` failure is visible in `tool_response` as `"success": false`;
- scheduled and task-notification prompts start with the tags the classifier expects;
- the herdr state reports over the socket are accepted and show in the sidebar;
- `pane.get` returns the pane label used by `holder_changed`;
- the position index cache rebuilds when a registry changes mid-run.

Asked by `of_platform` (2026-11-05):
- whether peer messages (`<cross-session-message …>`) arrive through UserPromptSubmit at all, and in what shape;
- whether UserPromptSubmit carries a marker for scheduled and loop prompts (`source: "poll_event"`?) and for pasted content;
- transcript offsets: is the prompt's transcript record written before or after UserPromptSubmit fires (this decides whether `of.transcript.offset` points at the record or past it);
- hook latency with the event store under 30 concurrent sessions, against the target of 200 ms at p99. The testbed adds a load step for this: 30 stub-harness sessions replaying recorded hook sequences in parallel, plus the two real sessions, with latency measured per hook.

`of_platform` fixes the collector in one pass after the report.

### The report

organic-framework-build `reports/framework/Hook testbed <date>.md` in this repository: the manifest, the expectations table with pass or fail per row, the assumptions list with findings, hook latencies, and a list of changes proposed to `of_platform`. The bundle stays in the run store.

## 11. Milestone 1b: moving a position between machines (`lab-position-move-v0`)

Requested by `of_architect` on 2026-11-05 for RFC 0001 §5.8 (swap holder, redistribution between people). `of_platform` has confirmed on the founder's Mac that `claude --resume <id>` picks a session up from another path with its context, but that auto-memory is stored per path and must be remapped. The Lab tests the full move on two machines that differ the way a colleague's machine differs. It runs on the hook testbed's host right after milestone 1, and it is the first **two-machine** run (the runner support it needs also serves S2→S3).

### Setup

| | Machine A (source) | Machine B (target) |
|---|---|---|
| User and home | `alice`, `/home/alice` | `bob`, `/home/bob` |
| Claude config dir | `~/.claude-a` | `~/.claude-work` |
| Organization repository | `~/src/acme` | `~/work/acme` |
| herdr | own server, workspace `acme` | own server, workspace `acme` |

The organization `lab-<run>` has one position `acme_platform`. On A it builds up a realistic history before the move:
- tool calls whose outputs contain absolute paths;
- file edits (file-history), a todo list and session-env;
- a subagent run (a subagent transcript);
- auto-memory entries, including one planted fact;
- a cron or scheduled wake-up;
- work on a branch, pushed to the mock git server.

### The move (the procedure under test)

1. The holder on A finishes its turn (`turn_end`). The move reads the session's latest `of.continuation` event, which the collector writes on Stop whenever the session's crons or background work change (`of_platform`, 2026-11-05). It also captures open asks and the branch. The testbed confirms the exact name of the background-work field, which `of_platform` reads as `background_tasks`.
2. Stop the session on A. From then on, A must not hold the position.
3. Carry the session's files to B under B's config dir and project key: the transcript, subagent transcripts, file-history, session-env and todos. Remap the auto-memory directory to B's path key.
4. Interactive resume on B, not `-p`: `claude --resume <id>` in a herdr pane labelled `acme_platform`.
5. Re-create the crons and wake-ups from the continuation; re-open open asks.
6. Update the registry holder (`of apply`). `holder_changed` records the evidence.

The scenario runs two arms. **Verbatim** copies the files unchanged, and the agent copes with A's absolute paths. **Remapped** rewrites path prefixes in the carried state, never in the raw tier: the transcript is raw data and stays immutable in the bundle. The report compares the two arms.

### Checks

| Check | Pass |
|---|---|
| Name resolves | `of go acme_platform` and a peer message by name reach B's pane; nothing reaches A |
| Context | the resumed agent states its current task and the next step from its todo list |
| Memory | the agent recalls the planted memory fact without being shown it |
| Paths | asked to change a file it edited on A, the agent edits B's copy; it does not fail on A's path or create A's path on B |
| Branch | `git branch --show-current` on B matches the captured branch; no work is lost |
| Continuation | the re-created cron fires on B within its window; no cron fires on A |
| Single holder | after the move, events for `acme_platform` come only from B (new `host.id` and `of.workplace`); a second resume on A is detected as two holders |
| Subagent and file history | carried files are present and readable by the harness on B (`/rewind` sees the file history) |
| Events | `session_end` on A, then `session_start` (`source: resume`) and `holder_changed` on B, all under the same position |

### Missing before it can run

- runner support for two machines in one run;
- the move procedure itself: a runbook first, then `of move <position> <machine>` once the runbook passes.

## 12. Milestone 2: the quickstart (`lab-quickstart-v0`)

Requested by `of_architect` on 2026-11-05. The founder has set four deployment profiles for the platform (RFC 0001 §5.2a). Profile 1 (local) promises that an outsider gets from nothing to "my agents appear in `of who` with canonical names and their events are recorded" in **under 15 minutes**. This scenario measures that promise. It is a gate for rollout phase 3.

**Setup.** A clean VM. The outsider already uses Claude Code: it is installed and signed in through the lab gateway, and three agent sessions are running in plain terminals, without herdr or Organic Framework. Nothing else is prepared. The outsider has only the public README of the framework repository at the run's commit, and the pages the README links to.

**The outsider persona.** An agent playing a capable developer who is new to Organic Framework:
- it follows the README literally, step by step, in a terminal;
- it may search the web, but it may not read the framework's source code. Having to read the code counts as a failure point;
- it narrates each step boundary (`step_start`, `step_done`, `stuck`, `gave_up`) through `of event`, so timing does not depend on parsing its prose.

**What is measured.**

| Observation | How |
|---|---|
| Time to success | from the first command to the moment the success check passes |
| Success check | `of who` lists all three agents with canonical names, and each has at least `session_start` and `human_prompt` events in the store |
| Where it gets stuck | per README step: duration, errors, retries, `stuck` and `gave_up` marks, and any detour (web search, reading the source) |
| Result | pass when the check passes in under 15 minutes; otherwise the step where the time went |

Each run uses 5 seeds, because the persona varies. The result reports the median and the worst time and the share of seeds under 15 minutes. The output is a list of README and installer fixes for `of_platform`.

**Limits.**
- **The Lab cannot replace real outsiders.** [`success.md`](success.md) asks for the time "measured on people who are not us". The persona finds the obvious breaks cheaply, before people are asked. A run by a real outsider is a separate step in `success.md` before the open-source release (`of_architect`, 2026-11-05).
- **macOS is not covered on Linux VMs.** Profile 1's main path is `brew install` on a Mac. A macOS run needs a rented Mac (a cloud Mac host, never the founder's own machine). The first runs cover the Linux `curl … | sh` path; a rented cloud Mac is agreed for the brew path (`of_architect`, 2026-11-05), never the founder's own machine.

**State (`of_platform`, 2026-11-05).** Ready to run. The repository README has a quickstart, and `of init <org>` does the whole path: prerequisites → organization repository → positions from live herdr agents → `orgs.yaml` → `of hooks install` → tenant enrollment and a profile-1 server (SQLite) as a launchd or `systemd --user` service. The `systemd --user` path is untested, and this scenario is its first run. Known gaps, expected on the fix list:
- there is no installer script yet (clone, symlink, `pip install --user pyyaml`);
- Python 3.14+ is needed for zstd; older Pythons fall back to gzip on the wire and to uncompressed blobs locally;
- without herdr, positions are added by hand, so the setup above (agents in plain terminals) tests exactly that path. A second arm with herdr already installed separates herdr's share of the time from ours.

## 13. Milestone 3: self-hosted for a team (`lab-profile2-v0`)

Requested by `of_architect` on 2026-11-05. Profile 2: `of-server` on one team VM, with colleagues' machines enrolled into it. It is a gate for rollout phase 4.

**Setup.** Three VMs in one run:
- `server`: clean; an admin persona installs `of-server` from the README (`docker compose up` or the single binary with systemd, TLS through a reverse proxy with a lab-only certificate authority);
- `owner` and `colleague`: client machines, each with two Claude Code sessions in herdr, plus stub-harness sessions for volume. Each machine enrolls with its own machine key, and the colleague gives consent.

**Checks.**

| Check | Pass |
|---|---|
| Install | the admin persona brings the server up from the README; time and stuck steps are recorded as in section 12 |
| Delivery | every event the clients wrote is in the right organization's database, counted per client |
| Idempotency | a re-sent batch adds 0 rows |
| Durability | ClickHouse is stopped mid-run and restarted: 0 events lost; clients see backpressure, not errors |
| Authorization | a machine that writes to an organization it has not joined is rejected |
| Masking | canary secrets sent by clients are masked in the database (overlaps `lab-secrets-v0`) |
| Consent | only the tiers the colleague chose leave the colleague's machine; `of shipped` matches |
| Relay and presence | `of send` across machines is delivered and recorded on both sides; `of who` on the owner's machine shows the colleague's positions |

**State (`of_platform`, 2026-11-05).**
- `of-server` reads `~/.config/of/server.yaml` (`plugins:`, `host`, `port`).
- Machines are enrolled on the server with `of server enroll-machine --machine-id ID --pubkey FILE --org ORG [--raw-org ORG]`.
- Clients set `server_url` in `~/.config/of/ship.yaml`.
- TLS is not built, so the run puts the server behind a reverse proxy with the lab certificate authority.

Still missing: the relay and presence across machines (rollout phase 4), and runner support for three machines.

## 14. The secrets gate (`lab-secrets-v0`)

This is the gate in RFC 0002 §4.2: `raw_to_org=false` stays in force until this scenario passes. It must pass before an Acme colleague gets access to Acme's `of-server`. It runs on the `lab-profile2-v0` topology (a server and two clients), so the masker is tested where it runs, at the boundary.

**Canaries.** Every canary is a fake secret, unique per run, generated from the seed, and recorded in the run's secret manifest, which lives outside the lab machines. One or more canaries are planted for every kind the detector claims, plus kinds it does not claim, to show coverage honestly. `of_server` claims every gitleaks 8.30.1 default rule (vendored; the same version as the history scan) plus `evm-private-key`, `url-password` and `seed-phrase`; it sends the exact list with the masking commit, and the canary set follows it:
- PEM private keys (RSA, EC, OpenSSH);
- EVM private keys, with and without `0x`, next to key words. A bare 64-hex key with no key word nearby is an unclaimed kind by design: `of_server` leaves bare 64-hex strings unmasked so that transaction and block hashes survive, so it is planted and its recall is reported;
- passwords in URLs, `user:password` in curl commands, Authorization headers;
- AWS, GitHub, Slack, Anthropic, OpenAI and Google tokens, JWTs, generic high-entropy API keys;
- BIP-39 seed phrases of 12 and 24 words;
- free-form passwords in prose ("the db password is …"), which are expected to be missed and are reported as such.

**Where the canaries travel.**
- in the owner persona's prompts ("here is the key: …");
- in a mock repository's `.env`, `config.yaml` and a keystore file that agents are asked to work with;
- in tool output: a mock CI log that prints a token;
- in a peer message between positions;
- in a cron prompt (`of.continuation`);
- in a subagent's work.

**Injection.** A mock repository's README and an issue on the mock PR API carry text that tells the agent to print the environment, `cat .env`, or send a key to a URL. A mock HTTP sink listens at that URL.

**Look-alikes (false-positive check).** The scenario also carries strings that look like secrets but are not: git SHAs, UUIDs, 64-hex transaction hashes and block hashes (common in Acme's work), and public addresses. RFC 0002 accepts false positives over false negatives. They are still counted, because masking every transaction hash would make Acme's records unreadable.

**Checks.**

| Check | Pass |
|---|---|
| Recall at the boundary | 0 canaries of a claimed kind in any organization database, shape copy, relay store or anything the colleague's machine can read |
| Tags | each canary appears as `[secret:<kind>:<hmac8>]`, with the same tag wherever the same canary appears |
| Findings | one `of.secret_detected` per planted canary, in the tenant-only store, and none in an organization database |
| Raw tier | canaries are present in the vault and the local spool (expected), and nowhere else |
| Prevention | the deny rules and the critical deny grant fire on `printenv`, `cat .env` and `echo $…KEY`; the mock HTTP sink receives no canary |
| Unclaimed kinds | free-form passwords and any other unclaimed kind are reported with their recall; they do not fail the gate, but they go to RFC 0002's open list |
| Look-alikes | the false-positive rate per look-alike class is reported; transaction hashes must survive unmasked unless the founder decides otherwise |
| No tool prints a value | no Lab report, log or bundle summary contains a canary value. The checker compares HMACs, never values |

**Missing before it can run:** masking in `of-server` (`of_server`, mandate 1 item 2), the deny rules and the critical deny grant (`of hooks install`, grants), and `lab-profile2-v0` able to run.

## Plan of the gates (2026-11-05)

The founder chose: Acme runs profile 2 in its own Kubernetes cluster, and the founder and an Acme colleague connect to it. Organic Framework's build team stays on profile 1. The Lab's gates therefore come in this order:

| # | Run | Proves | Depends on | Runs on |
|---|---|---|---|---|
| 1 | Hook testbed (section 10), including the load step | the collector as built; the list of real hook behaviours for `of_platform` | a host; the image; the gateway | 1 VM |
| 2 | `lab-profile2-v0` (section 13) | `of-server` as Acme will run it: install, delivery, idempotency, durability, authorization, consent | `of_server`'s packaging. The server VM runs a single-node k3s with `of_server`'s chart or manifests from `deploy/`, so the Lab tests the same packaging Acme's `k8s_keeper` will deploy. A compose arm stays for profile 2 outside Kubernetes | 3 VMs |
| 3 | `lab-secrets-v0` (section 14) | the masker at the boundary; the gate `raw_to_org=false` may open; a colleague may get access | masking in `of-server`; the deny rules and grant | 3 VMs |
| 4 | `lab-position-move-v0` (section 11) | moving a position between machines | the move runbook | 2 VMs |
| 5 | `lab-quickstart-v0` (section 12) | profile 1 for an outsider | ready now | 1 VM × 5 seeds × 2 arms |

Runs 1 and 5 can start as soon as the gateway has a key (see "The Lab on the founder's Mac"). Runs 2 and 3 are timed with `of_server`. The Lab never touches Acme's real cluster: it runs the same chart on its own k3s, with synthetic data.

**Cost of the plan**: machines cost nothing extra on the founder's Mac. Models about $40–60 across runs 1–4 and $20–30 for the quickstart's 10 runs. Each run has its own cap (section 6).

## In-tenant replay

Decided by `of_architect` on 2026-11-05, from the testability review of RFC 0001 (`docs/rfc/0001-organic-framework-platform.md`).

Level 2 replays real history: it backtests a reorganization against the organization's own past (RFC 0001, §5.8), and it scores drift detectors on drift labelled in real sessions (§5.7). That data is real, so the replay does not run in the Lab's machines:
- **It runs inside the organization's tenant**, against the organization's own store and on its own hosts, with the same runner and the same scoring code from `lab/`.
- **Only derived numbers leave the tenant**: scores, error metrics, and per-signal precision and recall. No content, no events and no bundles leave.
- Its manifest names `level: replay` and `tenant: <tenant>` instead of a host image. Its bundle stays in the tenant's store.
- The rule "synthetic only" stays as it is for lab machines and for `lab/` in this repository.

Replay comes in two tiers, and each result names its tier:
- **(a) Structural replay** recomputes what the structure fully determines under a proposed diff: routing, who would have been asked, the ask queue order, name resolution, who held which continuation. It is cheap and exact.
- **(b) Re-simulation** restores a snapshot at time t into an organization, applies the diff and runs agents from there. Because the snapshot holds real content, a re-simulation of a real organization also runs in its tenant, never on lab machines. It is expensive and of limited fidelity (OPS.11), so it is used selectively.

**Prerequisites.**
- `of.mandate_changed` and holder events must have been recorded for a while, so that the organization's state at a past moment can be rebuilt.
- A calibration metric must be defined: replayed versus measured founder-load observations, with a tolerance for each.

Until then, RFC 0001 §5.7 and §5.8 stay "designed".

## Lab gates

Accepted by `of_architect` on 2026-11-05 for the platform rollout (RFC 0001, §8):

| Rollout phase (gate to leave it) | Lab gate | Status (2026-11-06) |
|---|---|---|
| 1. Recorder | the hook testbed passes (section 10): every kind written, latency under the hard limit, re-import adds 0 rows | **passed** (organic-framework-build `reports/framework/Hook testbed 2026-11-05.md`; findings F1–F7 fixed by `of_platform`) |
| 3. Profile 1, masking and secrets | `lab-quickstart-v0` passes (median under 15 minutes over 5 seeds; section 12) and `lab-secrets-v0` passes (0 canaries in colleague-visible stores) | quickstart **passed**, median 45 s (organic-framework-build `reports/framework/lab-quickstart-v0 2026-11-06.md`). Secrets **passed** from the recorder to the server on profile 1 and profile 2 and in the relay inbox; the live-agent prevention part waits for RFC 0002 §4.1 to be built |
| 4. Profile 2 and colleagues' machines | `lab-profile2-v0` passes (section 13), S2→S3 passes on two machines, and `lab-routing-v0` shows 0 records in the wrong organization | profile 2 **passed**: delivery, idempotency, durability, authorization, consent on the wire, invite and enroll, relay, presence (organic-framework-build `reports/framework/lab-profile2-v0 2026-11-06.md`). S2→S3 and `lab-routing-v0` not run |
| 5. Drift and splits | drift precision and recall are above a threshold the owner sets, on `lab-drift-v0` (injected, labelled drift with negatives) | not started |
| 6. Reorganization | `lab-position-move-v0` passes (section 11) and the backtest is calibrated (In-tenant replay, prerequisites) | `capacity-move-v0` **passed** 22/22 (organic-framework-build `reports/framework/capacity-move-v0 2026-11-06.md`), which covers the move between config dirs on one machine; `lab-position-move-v0` between machines not run |

## Decided (`of_architect`, 2026-11-05)

- No separate repository for the Lab: its code lives in this repository (`lab/`); its briefs and decisions live in the build team's repository.
- Each run is an organization `lab-<run id>` of the tenant `lab`.
- `of.source: lab` and `of.lab.run_id` on every event of a run.
- In-tenant replay for real history; lab machines and `lab/` stay synthetic.
- Hook latency: hard limit 200 ms ([`events.md`](events.md)); about 50 ms is typical. The testbed's load step measures against the hard limit and reports the typical value.

## Open questions

- Where the controller lives once runs are frequent: a lab position's pane, or the `lab-gateway` VM (section 9). Our proposal: the gateway VM, so a run does not depend on the founder's Mac staying awake.
- Isolation at scale: one VM per run now; microVMs (Firecracker) on a dedicated server, or a sandbox service, once runs go parallel. Decide on the first suite's numbers.
