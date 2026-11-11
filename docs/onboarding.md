# Onboarding in Organic Framework

Status: **v2**, 2026-11-11, by `of_onboarding`, the keeper of onboarding as a feature. v1 (2026-11-05, `of_architect`) was an organization path of eleven steps drawn from the first user organization's run. v2 replaces it with the eight steps of the practice professionals use to study a company ([`onboarding-practice.md`](onboarding-practice.md), "the record" below), and gives every person a conductor from the first step ([`conductor.md`](conductor.md)).

The founder's rules for it (2026-11-10):
- onboarding must be thought through;
- it starts no agent first; it starts the onboarding's own organization structure;
- the first version ships only what works today.

## Start a company on OF

```sh
of init <org> --owner "<the head's name>" --language <en|ru|…>   # the repository, the conductor, the unit; no agent started
of registry check                            # passes
cd ~/of/<org> && claude --dangerously-skip-permissions   # once: trust the folder, accept the bypass warning, /exit
of spawn <org>_conductor --config-dir ~/.claude   # the head's one window: the first agent started
```

`of spawn` stops when the account does not trust the new repository yet or has not accepted Claude Code's bypass-permissions warning, and files a task for the head; the third line answers both once. `of init` prints it only when one of them is missing.

Then the head talks to the conductor, and the onboarding runs through it. `<org>/onboarding/README.md` in the new repository lists the unit and the run.

**Without herdr** `of spawn` cannot open a pane. `of init` then reserves a session id for each agent of the unit in the registry (nothing is started) and prints the conductor's two lines: one declares its brief (`of event injected_prompt`), so it is not counted as the head's words, and one starts it in the repository (`claude --session-id <id> --name <org>_conductor "<brief>"`). Every agent's two lines are in `onboarding/README.md`, with when to start it.

`of init` writes, from the onboarding package (`bench/onboarding/`):
- the organization repository: `AGENTS.md`, `registry/positions.yaml`, `registry/briefs/`, `onboarding/`;
- in the registry: the head as a person (`people:`) holding the owner's position; **the owner's conductor as the first position**; the onboarding unit's positions; the organization as a creating system; the unit; and the run as one work with eight stages;
- a brief per position, and the charter, role-design and re-check templates under `onboarding/`;
- this machine's `orgs.yaml` entry, with the repository as the organization's first path, so `of spawn` starts its positions there;
- the recorder hooks and a local server, as before.

No agent is started. The agents that already run on the machine are listed as positions for the structure keeper to name later. `of registry check` passes with one warning until the charter is approved: the organization's keeper has no ask id under `confirmed:` yet.

Without `--owner`, the head's name is the machine's login name. `--language` is the language the agents use with the head, one setting kept in `~/.config/of/owner.yaml` (`of spawn` reads it too); without it `of init` asks in a terminal, keeps the machine's earlier setting, or uses English. It goes into the conductor's and the lead's briefs, the organization's `AGENTS.md` and the charter's section 7.

## Concepts

- **Onboarding path.** A method that takes one kind of subject from "outside Organic Framework" to "operating under it". It is an ordered list of steps.
  - Each step has a purpose, a done criterion, an owner, an approver if one is needed, its reversibility, and the evidence it leaves.
  - A path is a method description (FPF A.3.1). It is versioned and improves from runs (A.3.1.MR).
- **Onboarding run.** One pass of a path for one subject. It is work: it has a state, the current step, the evidence and the open questions. For an organization it is a work in the registry with one stage per step. Runs nest: an organization's run starts runs for its systems, positions and holders.
- **Onboarding package.** The organization path packaged for Bench ([`framework.md`](framework.md), "Installing from Bench"): a unit blueprint, a bootstrap position (the onboarding lead), a brief per role, the run, and its templates. It lives in `bench/onboarding/`.
- **Onboarding level.** How far a subject has come. The levels are the same for every subject kind, so the owner reads adoption at a glance, and the trace system can check it on any machine.

| Level | Name | Means | Checked by |
|---|---|---|---|
| L0 | unseen | The subject exists; Organic Framework does not know it | — |
| L1 | visible | It is inventoried. `of who` shows it, even without a position | the inventory |
| L2 | named | It has a registry entry: canonical name, system, kind, owner, aliases | the registry |
| L3 | governed | It has a brief or mandate, and its rules are delivered (AGENTS.md, Position trailer) | file checks |
| L4 | traced | Its work writes events with its position ([`events.md`](events.md)) | the event log |
| L5 | operating | It runs in the runtime under its name, and its continuation survives restarts. Peers reach it by name; its asks reach the owner's queue. | runtime and log |

## Subjects and their paths

| Subject | Path | Owner of the run |
|---|---|---|
| **Organization** | the eight steps below | the onboarding lead (`<org>_onboarding`); the structure keeper keeps the result |
| **Person** (the head, a champion, a colleague) | a person in `people:`, a position they hold, and their conductor in the same diff ([`registry.md`](registry.md), People; [`conductor.md`](conductor.md) §5); `of invite` and `of enroll` for their machine | the structure keeper |
| **Machine** | install the runtime and its agent integration, the `of` CLI, `orgs.yaml`, hooks and `machine_id`; route each organization's records to its own database; join its organization's server | the platform, with the machine owner's yes |
| **System** (and its code repositories) | map entry with its evidence and keeper → dependency edges → repository rules (`AGENTS.md`, trailer, names) → release path | the system architect, then the system's keeper |
| **Position** | brief → registry entry by structural diff → grants → `of spawn` | the structure keeper; the owner approves new positions |
| **Holder** (a new session, or a holder swap) | resolve the position → name everywhere → rules → move continuation (crons, monitors, wake-ups, open asks) → announce to peers → verify by name | the platform, with the outgoing holder handing over |
| **Package** (from Bench) | the position path plus fitting (CUST.2, CUST.3:4.3); "installation is onboarding a professional" ([`framework.md`](framework.md)) | the package's bootstrap position |

## The onboarding unit

The roles follow the practice (the record §3). Each brief is in `bench/onboarding/briefs/`, and `package.yaml` gives each role its sources in full.

| Role | Held by | Does | Source |
|---|---|---|---|
| **Conductor** | an agent per person who holds or owns a position: `<org>_conductor` for the head, `<org>_conductor-<person>` for anyone else | the person's one window: routes each request to its owner by the registry, follows it to a result, brings back results, decisions and risks; presents the cards and never answers one; watches open requests | [`conductor.md`](conductor.md) §2–§3: a single window (UN/CEFACT Rec. 33), a single point of contact (ITIL 4), the chief of staff (Ciampa 2005), orchestrator-workers (Anthropic 2024); the record §8.3 (2), §8.4 (3) |
| **Sponsor** | the head, a person, the unit's owner | asks the question; approves the charter; decides on the findings | Block's client; TOGAF Phase A approval; Martin and Osterling's executive sponsor (the record §1.2–§1.4) |
| **Champion** | a person the charter names | owns the first value stream end to end; confirms the current-state map; owns the plan of increments | Martin and Osterling's value stream champion; Rother and Shook's value stream manager (the record §1.4) |
| **Onboarding lead** | an agent, kind project: the bootstrap position | runs the cycle: charter, data reading, integration, the one decision batch, the increments, the close; the unit's one batching point for the head's cards | Block's consultant; Martin and Osterling's facilitator, who must not work in the stream; due diligence's lead (the record §1.1–§1.4) |
| **Business-case analyst** | an agent, kind analyst | how the company makes money and for whom, against the norms: the six cases, the strategy's parts, the stakeholder set | commercial and financial due diligence; TOGAF Phase A; BIZBOK; ISO/IEC/IEEE 15288 §6.4.1; the record §8.1 |
| **System architect** | an agent, kind keeper | what the company makes and runs, and each record's system of record | technical due diligence; TOGAF baselines B–D; ISO/IEC/IEEE 15288 and 42010; Tornhill; [RFC 0007](rfc/0007-system-architect-and-cells.md); the record §8.2 (7) |
| **Structure keeper** | an agent, kind keeper | who does and decides what, people included; keeps the organization model afterwards | Weisbord; Nadler and Tushman; BIZBOK organization map; RAPID; socio-technical congruence; the lines of control (the record §8.1); the record §8.4 (1, 2) |
| **Flow analyst** | an agent, kind analyst | the value stream where the head's pain sits, measured before and after from what OF records only | Rother and Shook; Martin and Osterling's lead time; operational due diligence; process mining; the record §5, §8.2 (3) |

Rules of the unit:
- **The conductor is not the lead.** The conductor stays for as long as the person works in OF; the lead runs one cycle and leaves.
- **The lead is not the structure keeper.** The facilitator must not work in the stream, and the keeper stays after the run.
- In a small company one agent may hold several lens roles. The lead and the conductor stay separate.
- **The sponsor and the champion are people.**
- **One batching point per unit** files the head's cards; in the onboarding unit it is the lead.

## The organization path

Version 2. Each step is a stage of the work `<org>-onboarding` in the organization's registry, and a stage is done only when its own evidence exists in the records (the record §8.3 (3)). The column "v1" names the steps of the earlier path each one takes over.

| # | Step | Who | Done when | Source | v1 |
|---|---|---|---|---|---|
| 1 | **One window, then the contract.** `of init` writes the repository with the head's conductor as its first position, then the unit, with no agent started. The head starts the conductor first, then the lead. Through the conductor, the lead drafts the charter: scope, champion, people, what is read and what is not, each record's system of record, the outside parties with their writer, channel and access, how findings come back | the head starts; the lead drafts; the sponsor approves | `of registry check` passes; the head's conductor is live and the head's requests reach their owners through it; the charter is approved by an ask id | Block, entry and contracting; TOGAF Phase A, Statement of Architecture Work; Martin and Osterling's charter; [`conductor.md`](conductor.md) §3, §5; the record §8.2 (7), §8.3 (1), §8.4 (1) | 0 |
| 2 | **The presenting problem**: the head's last story of confusion or bureaucracy, told to the conductor | the head tells; the lead records | the story is recorded; the first value stream or decision area is chosen; the stage S0–S3 is named | Block, discovery; Watkins' situation diagnosis; [`business-case.md`](business-case.md) stages | the loop's "biggest pain" |
| 3 | **Data first**: people, agents and machines; git and GitHub; the sessions people choose to record; each confidential source only with its recorded permission; **the people map** (authors by system) confirmed by the head on one card before any person gets a position | system architect and structure keeper, started as one wave | every live agent, person and repository is visible; the people map is confirmed by an ask id; every person-held claim carries evidence; each source read has its permission | Process Mining Manifesto (discovery); Tornhill; the record §8.2 (7), §8.4 (1, 4, 7) | 1 |
| 4 | **Four baselines in parallel**: business case, systems, structure, flow; each against its norm; each measure's source checked; each mandate against its work | the four lenses; the business-case analyst and the flow analyst start as the second wave | each baseline exists, each fact names its evidence, and each norm part has a state | due diligence's workstreams; TOGAF baselines; BIZBOK; the six boxes; the record §8.1, §8.2 (1–4), §8.4 (2) | 2, 3, 4, 5 |
| 5 | **Integrate**: misfits and gaps ranked by consequence; transformational ones to the head, transactional ones to a keeper; gaps nobody raises go to the head as decisions | the lead | the findings list, each with its lens, evidence, consequence and decider | Nadler and Tushman's misfits; TOGAF gap analysis; due diligence's red flags; Burke and Litwin; the record §8.2 (5) | the ranking rules |
| 6 | **Feedback**: one numbered decision batch, filed by the lead; the head's conductor presents it | the lead files; the sponsor decides | the ask is answered; confirmed facts move to `confirmed:` | Block, feedback and the decision to act; the record §8.3 (2) | 6 |
| 7 | **Increments**: the future state as works with checkable ends, non-invasive first. New roles come from the role-design template, each with its outside party, channel and access; they start in waves sized by capacity; each unit gets one batching point; the conductor watches every open request | the lead with the keepers; the champion owns the plan; the sponsor approves each invasive batch | each increment is a work with `done_when` and a measure; the head's open items from the onboarding are counted per day | Martin and Osterling's future state; TOGAF transition architectures; Watkins' early wins; the record §8.2 (8), §8.3 (1, 2), §8.4 (2–4); [`rollout.md`](rollout.md) | the loop; 7, 8, 9 |
| 8 | **Re-measure, re-check, hand over, close.** A position that did not build the structure counts each item's state from the records | the flow analyst re-measures and re-checks; the lead closes | the re-measure against step 4; the re-check, item by item; the lead's position is finished | Block, extension, recycle or termination; Martin and Osterling's reviews; the record §8.3 (3), §8.4 (5) | 10 |

**What OF reads.** GitHub (`of import github`), the Claude Code sessions people choose to record, and the structure itself. It does not read the head's mail, chat or meetings. A confidential source (the books, a bank, a provider's portal) is read only with a permission recorded by an ask id. OF has no messages between agents and people outside the organization: an outside party is reached through the head, or through a channel the head allows, named in the charter. The charter says all of this (`onboarding/charter.md`, sections 4 to 6).

**Through every step:** run `of registry check` after each registry change.

**What runs v1's steps 7 to 9 now.** Hooks on each machine (v1 step 7), the rules in each repository (step 8) and moving holders into the runtime (step 9) are increments of step 7. Each is a work with its own `done_when`, and the sponsor approves each invasive batch.

## The measure before and after

The flow analyst's, by the method of the record §5: it counts only what OF records about the organization, uses one method for both windows, and lists the metrics it cannot compute as gaps, never estimated. It adds the head's open items from the onboarding, per day (the record §8.3 (2)). The measures are `of_studio`'s (`of model measures`, `of model queues`).

## Gradual onboarding: pains first, the business case grows behind them

The founder, 2026-11-05: at some point the organization needs a whole business case. It covers throughput accounting, the market, the current organization structure, and the stakeholders of the organization, of the target system and of the nested systems. Doing it all at once would overload the person being onboarded. Onboarding must be gradual: it **solves the person's current pains, and little by little closes the rest**.

**Two axes.**
- **Adoption**: subjects under Organic Framework, measured in levels L0–L5 (above).
- **The organization model**: what Organic Framework knows about the organization. Its areas make up the business case, structured as six cases in [`business-case.md`](business-case.md). Each area has a maturity: **unknown → sketched → evidenced → kept current**. "Sketched" is inferred and not confirmed. "Evidenced" is confirmed by the owner or by data. "Kept current" means a position keeps it.

**The improvement loop is the TOC loop** (identify the constraint, exploit it, subordinate to it, elevate it, repeat). Steps 2 to 7 are its first turn; after the close, the keepers run it on:
1. Find the owner's current biggest pain, from the traces and from one question: "what hurts most now?". Rank by lost constraint time or by expected ΔT ([`success.md`](success.md)).
2. Choose the **smallest Organic Framework increment that relieves it**.
3. Capture **only the slice of the model that this increment needs**. The model grows as a by-product of relief, never as a questionnaire.
4. Measure whether the pain dropped. Then repeat with the next pain.

**Rules for the model's growth:**
- **Infer first, ask second.** Read the traces, documents and repositories. Propose a "sketched" answer and ask the owner only to confirm or correct it.
- **An attention budget for onboarding**: one batch at a time, each question with a proposed answer, filed by the unit's batching point.
- **Show completeness without pressure.** The owner sees the model's maturity per area as a map, with the uncovered areas visible. A gap is a fact, not a demand.
- **Gaps with consequences come first.** An uncovered area that carries a deadline or a legal risk (tax, licences) jumps the queue. The stakeholder obligations calendar in [`method.md`](method.md) feeds this.

**An operating company, as an example** (stage S2; the order its pains usually come in):

| # | Pain | Increment | Model slice it fills |
|---|---|---|---|
| 1 | "I don't see what happens; I can't find agents; I don't know who waits for me" | the conductor, inventory, registry, names, the desk | creating organization (agents), target system (first pass) |
| 2 | "Releases jam between systems" | dependency edges in the systems map, a release coordinator | target system breakdown with interfaces |
| 3 | "Which of my decisions matter most" | a decision inventory with expected ΔT | throughput accounting |
| 4 | "The company as a system has no one" (books, tax, licences) | an obligations calendar; positions for the company's own functions, from the role-design template | stakeholders, rules and risk |
| 5 | "Colleagues don't work this way" | each colleague's position and conductor | creating organization (people) |
| 6 | "Where is the market going" | a market analyst's mandate | market |

## Design rules: comfortable and effective

1. **One window from the start.** The head's conductor is the first agent; the onboarding reaches the head through it, and decisions stay cards ([`conductor.md`](conductor.md)).
2. **Value before invasion.** The first steps give the owner a picture (who's who, who waits for them, the baseline) without touching a single running agent.
3. **One batch of questions per stage.** The owner's attention is the organization's constraint ([`success.md`](success.md)). Onboarding must not become a new source of plumbing.
4. **Each step is idempotent and reversible.** A run can stop at any level and resume. Re-running a done step changes nothing; `of init` run twice adds nothing and overwrites no file.
5. **Done criteria are checked from the records.** Each step's `done_when` is a stage of the run's work; a position that did not build the structure counts them at the close. Manual ticks are the exception.
6. **Owners act on what they own.** The structure keeper does not edit the system agents' repositories. It issues each system agent a step and checks the result. Rules reach repositories through their own agents.
7. **Continuation travels with the holder.** A holder swap moves crons, monitors, wake-ups and open asks with it; nothing silently dies.
8. **Every run improves the path.** Friction in a run becomes evidence, and the record (§8) and the package change with it.

## Open questions

- Should levels be recorded per subject in the registry (`onboarding: L3`), or always computed from checks? Our reading: computed, with the registry holding only an override.
- Without herdr, the agents are started by hand from `onboarding/README.md`, and nothing places them on a desk or names their panes. A no-herdr mode of `of spawn` itself would serve positions added later.
