# Methodology: how we analyze, design and build Organic Framework and Bench

Status: **Draft**, pending founder review. Date: 2026-10-29.

Basis: FPF Library (Anatoly Levenchuk), repository `ailev/FPF`, edition commit `c3b34de` (2026-10-29). FPF is published as "eternal alpha", so every PatternID below refers to that edition. Each section separates **Source** (what FPF says) from **Apply** (our decision or interpretation).

## 1. There is no single lifecycle; we compose methods from four parts of FPF

**Source.** FPF does not prescribe a fixed order of stages:

- A.1.STM:4.3 says of the system-thinking order: "This order is not a waterfall."
- OCE.Reference:4 describes `OCE.1 → OCE.2 → OCE.3` as result dependencies, not "a calendar sequence."
- Engineering.Reference:0.5 says to "Begin with the consequential question."

**Apply.** Our methodology combines four parts of FPF Library:

| Question | FPF part | Key patterns |
|---|---|---|
| Where do we start, and how do we keep the whole in view? | Core | C.39, A.1.STM, C.40.OD, C.39.QR / C.39.WA |
| How do we design the platform and the agent roles? | Engineering Suite, Systems Engineering (SYSE) | SYSE.1/2/5/6/24; platform engineering SYSE.12 and SYSE.25–41; agent work SYSE.42–48; Engineering.Reference:26 (guard and audit) |
| How do we assemble and change a human/agent organization? | Management Suite | OCE.1–14, ADM.8, ADM.13, GOALS.7, GOALS.3, OPS.4, OPS.5, STR.18, HRM.5 |
| How do we make Bench (about 90% packaged, 10% fitted)? | Customization and Productization (CUST), Semantic Integration Engineering (SIE) | CUST.1–8, SIE.1/4/11, SYSE.53, A.3.1.FT/MI, C.40.JD |

The Management Suite is, in effect, a specification of Organic Framework's domain logic. The product encodes OCE, ADM, GOALS and OPS distinctions as software.

## 2. Systems of interest (A.1.STM, SYSE.1)

**Source.** A.1.STM:0 says: "Agree what system must succeed for whom and in what use. [...] Repeat for the creators that must themselves be obtained."

**Apply.** We keep these systems separate and apply the mantra recursively across them.

> **Superseded in part (2026-11-03).** [`mission.md`](mission.md) defines three levels: target system ← creating organization ← Organic Framework. A client company is a creating organization (an app), not the target system. Its target system is what it builds and runs. The package definition in item 4 is superseded by [`framework.md`](framework.md): a package is a unit blueprint plus a bootstrap position.


1. **Target system.** A client's hybrid organization. The first client is the founder's own company. This is the system that must succeed, for example by deploying the bot reliably.
2. **Organic Framework.** The platform that creates and sustains the target system. It is an enabling system for practitioners: human leads and agent-held positions (SYSE.12).
3. **Bench.** A registry plus a repeatable offering family (CUST.3, SYSE.8).
4. **A Bench package.** A position description plus execution procedures plus an autonomy-budget template. It is installed into (1) through (2).
5. **Our own build team.** It is also a creator. It runs on Organic Framework as soon as possible (dogfooding).

## 3. Domain model grounded in FPF distinctions

> **Superseded (2026-11-03)** by [`ontology.md`](ontology.md) and [`ontology-a-core.md`](ontology-a-core.md). The table below is the first mapping and is kept for its FPF citations. The later decisions it misses:
> - the holder is an agent session with its history;
> - the accountable human is derived as the holder of the nearest human-held owner position;
> - the owner is a position.

| Founder's concept | FPF distinction | Consequence for the product |
|---|---|---|
| Role | Position (OCE.5); system-role kind (A.2) | OCE.5: "Replacing a person can appear to abolish a position" when title, position, holder and assignment are merged. Positions persist through vacancy and changes of holder. |
| Agent or human in a role | Holder plus dated assignment (A.2.1). A.13: "agent" "is not a fundamental FPF type." | Humans and agent systems are both holders. A different model is a different system and needs requalification (Library.Reference:9.3.4). |
| Role rights | Permission grant (ADM.8, A.2.8.PER); autonomy budget (E.16 `AutonomyBudgetDecl`) | ADM.8: "A mechanism can block or enable an action without constituting institutional permission." Limits are enforced "outside model-generated narrative" (SYSE.47), by a guard that returns admit, deny or missing (Engineering.Reference:26). |
| AGENTS.md | MethodDescription (A.3), structured per GOALS.7 | Each one contains: the receiving result; explicit permissions in the actual environment; a continuation mechanism; completion evidence the agent cannot alter. |
| Workplace | Not a Core term. Nearest: work-performance configuration (A.15.8), enabling relation with a state (OCE.6) | A Workplace is a set of supporting systems plus where the continuation state lives. Its relation states are proposed, decided-not-effective, effective, contradicted, expired, suspended and missing. "Laptop off" = suspended. Engineering patterns: SYSE.33, SYSE.38, SYSE.40, SYSE.41. |
| Audit, trajectories, prompts | Role–Method–Work alignment (A.15); Work vs record (A.3); ADM.13; OPS.4; SYSE.46:4.3 | Each trace records who performed which Work, under which assignment, enacting which Method. A.3: "a ticket, log row, record, or carrier is not the Work." |
| A human leading a group of agents | HRM.Reference:4.3; GOALS.7:4.2 | Performance, authority and responsibility are allocated separately. A human need not supervise every reversible operation. |
| Resource conflicts | GOALS.3, OPS.5, OPS.19, SYSE.44, C.32.MWA.PP | OPS.5 lists "the agent is free" among cues that do not by themselves justify a start; starting work needs a current admission decision. |
| Metrics | Capability under conditions (A.2.2); GOALS.1–4; OCE.13 | No single "change succeeded" score. |
| Refactoring the org structure | OCE.13/14, OCE.11, ADM.16, B.1.5.RS | Change the arrangement without losing open cases or continuing service. |
| Org simulation | No dedicated pattern. OPS.11, C.28, A.3.3 | OPS.11: "A simulation can compare its modeled alternatives; it does not establish that the actual operation has the same relations or effects." Simulation runs on the OCE.4 contribution-architecture model. |

## 4. Bench through CUST and SIE

- A package is a **CUST.3 offering family**: invariants, allowed variations (parameter, adapter, profile) and local residue. The "90%" is invariants plus allowed variations. The "10%" is local residue, recovered per company by CUST.2.
- Fitting recovers client deltas and places variations: **CUST.2** and **CUST.3:4.3**. Repeatable installation and service delivery follow **CUST.6** ("Establish Repeatable Deployment and Service Delivery"). Per CUST.6, a method described as independent "cannot depend on unrecognized rescue by its author." (Label corrected 2026-11-03 after the ontology review.)
- Fitting company terms and processes uses SIE.1 and SIE.4, plus method fit and transfer (A.3.1.FT, A.3.1.MI).
- Package upgrades for installed companies follow CUST.8 and SIE.11. Package dependency resolution follows SYSE.53.
- The claim "the package fits this company" is a reliance claim. It is tested per SYSE.46, on the company's representative tasks.
- **Order.** Bench is not built first. Installing a package is an OCE.6 assignment into an OCE.5 position, so it needs an organization model to install into. The first packages are extracted bottom-up (CUST.3:4.4 compares top-down and bottom-up families) from roles proven in Organic Framework, via CUST.5 (field learning to a productization candidate).
- **Our analogy, not in the source:** a package resembles a role-level DPF, and company fitting resembles an LPF (local framework).

## 5. First moves

These are not mandatory stages. Each result revises the earlier answers that depend on it.

0. **Project canon.**
   - Pin the FPF edition and reference it from the project AGENTS.md.
   - Maintain an English glossary as the project's bounded context (A.1.1).
   - FPF is not the only reference. Map the canon to established standards and bodies of knowledge (Enterprise Architecture and adjacent fields) so that concepts can be cross-checked and communicated (founder decision, 2026-10-29). Survey: a private research report, "Standards for hybrid agent organizations" (2026-10-29; adoption choices pending founder review).
1. **One result and its observation of success** (C.39:4.1, OCE.1).
   - Decided scope (founder, 2026-10-29): one position that owns the first client's bot in operation. It deploys the bot, watches its metrics, reports problems and resolves the subset of them its autonomy budget (E.16) allows.
   - Observed success means: it survives the laptop going offline; it has explicit permissions; it has a full audit trace; it has a human lead; its holder can be replaced; detected problems reach the human lead with evidence; self-resolved problems stay within the declared autonomy budget.
2. **Current arrangement** (OCE.2).
   - Inventory the first client: who runs which agents, with which access, and where they conflict.
   - Classify each claim as formal, observed, contradicted or unknown.
   - Done for the founder's own agent organization (2026-11-03): a private retrospective, eight days of an owner-run agent organization. Its lessons (requirements R1–R11, model changes M1–M7) are not yet folded into this document.
3. **Concepts** (SYSE.2, OCE.3, SYSE.54 EventStorming on the task flow).
   - Link use concepts to system concepts.
   - Develop several materially different organization concepts.
4. **Domain model** from section 3, cross-checked against the standards surveyed in step 0. This becomes the core data model of Organic Framework.
5. **Architecture alternatives** (SYSE.5, SYSE.6, SYSE.24).
   - Compare build, buy and reuse for the agent runtime, Workplace hosting, the permission guard and trajectory storage.
   - Record reopen conditions for each choice.
   - Design principle (founder, 2026-10-29): Organic Framework is a framework, not a fixed toolset. Teams choose their own agent runtimes, models and tools locally, and these choices change over time. The core model (positions, assignments, permissions, Workplaces, Work records) must stay stable while tool adapters are added, replaced and retired (SYSE.28 interface evolution, SIE for meaning across tools).
6. **Thin slice** (OCE.9, SYSE.47, SYSE.46).
   - Take one position end to end, on a server Workplace.
   - Compare it against the current laptop baseline on representative tasks.
7. **Observe and revise** (OCE.13, OCE.14, GOALS.4).
   - Add the second position.
   - Extract the first position into the first Bench package.

Mantras to keep in view:

- A.1.STM for engineering.
- STR.18 for mission and business case: "Strategic question → worthwhile contribution → explained way → feasible contributions and means → justified choice and commitment → realization, use and renewed questions."
- OPS.23 for runtime: "Accepted result → route and readiness → limiting conditions → release and feasible order → service, money and human conditions → observed result and changed conditions."

## 6. Traps FPF names explicitly

- **C.40.OD:2.** "every box has an owner, but the required work and its conditions do not connect." Organic Framework must not become an org-chart editor with agent boxes.
- **GOALS.7 anti-pattern.** "Persistence by assertion: use an effective continuation mechanism and its actual limits." The current laptop-hosted agent is an example.
- **SYSE.24.** The "label AI neither closes an unsupported operation nor grants acceptance authority."
- **OCE.8.** A hybrid arrangement chosen on presumed superiority can perform "worse than the best applicable solo configuration."
- **LDR.** "Do not freeze the old arrangement and merely insert a drafting assistant."

## 7. Where FPF does not help (open gaps)

- "Workplace" is not an FPF term. Our object is constructed from A.15.8, OCE.6 and SYSE.33/38/40/41.
- There is no pattern for simulating organizations. This is our own development area under C.40.OD.
- Control Architecture Design (CAD) and Maintenance Engineering (MNT) are out of scope for agent oversight and software workplaces. CAD covers sampled linear control. MNT covers physical and cyber-physical equipment.
