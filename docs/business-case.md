# The business case in Organic Framework

Status: **Proposal**, 2026-11-05, by `of_architect`. The founder asked whether the business case was described correctly and to which standard. It was not to a standard. The first list in [`onboarding.md`](onboarding.md) was assembled ad hoc and missed several parts. This document puts it on a standard footing.

The business case is the model of *why the organization is worth running and how it pays*. It is link 2 of the value cycle in [`mission.md`](mission.md). It is never written in one go. It grows by gradual onboarding, a slice per relieved pain.

## Standards compared

| Source | What it requires |
|---|---|
| **HM Treasury Green Book, Five Case Model** (UK, the most widely used business-case structure; the basis of "Better Business Cases" and close to PRINCE2) | Strategic case (the case for change), economic case (options and value), commercial case (how the value is obtained and captured), financial case (affordability and funding), management case (how it is delivered and governed) |
| **PRINCE2, Business Case** | Reasons, business options (including do nothing), expected benefits, expected dis-benefits, timescale, costs, investment appraisal, major risks |
| **ISO/IEC/IEEE 15288, Business or mission analysis** | Problem or opportunity space, stakeholders, classes of solution, preliminary life-cycle concepts (concept of operations), alternatives |
| **Business Model Canvas** (Osterwalder) | Customer segments, value propositions, channels, relationships, revenue streams, key resources, key activities, key partners, cost structure |
| **FPF**, edition `c3b34de` | STR.18 (question → worthwhile contribution → explained way → feasible contributions and means → justified choice and commitment → realization, use and renewed questions); STR.8 (capabilities, dependencies, business models, initiatives); STR.12 (signals to reconsider); A.1.STM (agree what must succeed for whom and in what use); OPS.14 (throughput, cash and operating consequences); FIN.2 (liquidity and funding by date); MA.1 (cost model); MKT.1 (market problem); CGOV.2 (shareholding and control) |

## What the first list missed

The first list had: stakeholders, mission, throughput accounting, market, target system, creating organization, resources and capital, rules and risk.

It missed:
- **the problem or opportunity and "why now"**;
- **the options considered**, including doing nothing;
- **benefits, dis-benefits and how they are measured**;
- **assumptions with invalidation signals**;
- **how value is captured and from whom** (for a trading company the payer is the market; see [`method.md`](method.md));
- **suppliers, partners and contracts**;
- **cash and liquidity by date**, runway and funding (for example a SAFE round);
- **ownership, control and decision rights**;
- **the concept of operations of the target system**;
- **the delivery plan with milestones**;
- **legal and regulatory constraints** as constraints, not only as stakeholders.

## Two levels: the organization's case and each initiative's case

Founder, 2026-11-05: for an organization that already runs, "why now" is not a question of founding. The first user organization already earns money, and the point is to earn more.

The Five Case Model was written for a proposal to change something. Organic Framework therefore uses it at two levels:
- **The organization's business case** is a *living* model of a going concern: where the organization stands (T, OE, cash), where the owner wants it to go (more T, at an accepted risk), the gap between the two, and the constraint that limits closing it. "Why now" becomes "what limits growth now".
- **Each initiative's case**: an RFC, a new position, a new system, or a decision in the owner's inventory. It is a small case with options (including doing nothing), expected ΔT, cost, risks and invalidation signals. The economic case lives mostly here. The decision inventory with expected ΔT ([`success.md`](success.md)) is the list of these small cases.

## Proposed structure: six cases on the Organic Framework ontology

The founder pointed out that regulation is often decisive and has no clear home in the Five Case Model. In the Green Book it is spread across the strategic case (constraints) and the commercial case (procurement law). Organic Framework makes it a **sixth case**:
- it is the **licence to operate**;
- it has deadlines and legal consequences;
- it is a working set of its own (jurisdictions, regulators, obligations), so it needs its own keeping position.

Each case is a set of model areas. Each area matures **unknown → sketched → evidenced → kept current**, and has a keeping position once it is kept current.

| Case | Areas | Ontology and FPF |
|---|---|---|
| **1. Strategic case: where we stand, where we go, what limits us** | stakeholders and their interests; current state (T, cash) and the owner's ambition; the gap and the current constraint; mission and goals; scope; external dependencies | group D (stakeholder, interest, coverage); STR.1–STR.3; A.1.STM; 15288 business analysis; TOC |
| **2. Economic case: which options, what value** | mostly per initiative: options, including doing nothing; expected benefits as ΔT and dis-benefits; assumptions with invalidation signals; risks; the chosen option and its reasons | STR.6, STR.9, STR.11, STR.12; the decision inventory ([`success.md`](success.md)) |
| **3. Commercial case: how value is captured** | who pays and how (customer, or the market); value proposition; market, competitors and alternatives; channels; suppliers, partners and contracts | MKT.1, MKT.13; STR.8; Business Model Canvas |
| **4. Financial case: does it pay and can we afford it** | throughput accounting (T, I, OE, NP); cash and liquidity by date, and runway; funding and capital (rounds, cost of capital); unit economics | OPS.14; FIN.2, FIN.5; MA.1; Goldratt's throughput accounting |
| **5. Management case: how it is built, run and governed** | target system breakdown and its concept of operations; creating organization (units, positions, people, where they work); ownership, control and decision rights; risk appetite; delivery plan and milestones; how benefits are measured | [`systems.md`](systems.md); groups A–B; CGOV.2–CGOV.4; [`onboarding.md`](onboarding.md) |
| **6. Regulatory case: the licence to operate** | jurisdictions and legal entities; regulators and licences (for example, a virtual-asset regulator); tax; AML and sanctions; data protection; AI-specific rules (EU AI Act); the **obligations calendar** (events addressed to a position, with consequence if missed); the status of each licence and filing | group D (statutory stakeholders, the obligations calendar in [`method.md`](method.md)); CGOV.8, CGOV.9; Bench stakeholder packages per jurisdiction |

## Stage of the organization

Founder, 2026-11-05: the case depends on the stage the company is at:
- an existing business;
- an idea that has barely begun;
- an idea not yet born.

Each case asks a different question at each stage, and the stage decides which cases come first in onboarding.

Our reading: four stages, plus the end of the life cycle.

| Stage | What exists | Main question | Essence Opportunity alpha | ISO 15288 stage |
|---|---|---|---|---|
| **S0 Idea** | A problem and a hunch; agents can already work on it, but there is no product | Is it worth trying, and is it allowed? | identified → solution needed | concept |
| **S1 Started** | Work has begun; no stable revenue (pre-fit) | Does it work, and for whom will they pay, before the money runs out? | value established → viable | development |
| **S2 Operating** | Revenue; a going concern (the first user organization) | What limits growth of T now? | addressed → benefit accrued | utilization and support |
| **S3 Scaling** | Several units, products or markets; outside capital | How do we grow without losing control and the licence to operate? | benefit accrued, repeated | utilization at scale |
| *Renewal or exit* | Decline, pivot, sale, closure | Continue, change or end? | — | retirement |

Lean startup's milestones mark the transitions: problem–solution fit (S0→S1), product–market fit (S1→S2), scalable growth (S2→S3).

## The six cases by stage

★ marks the case that leads at that stage. This is where gradual onboarding starts.

| Case | S0 Idea | S1 Started | S2 Operating | S3 Scaling |
|---|---|---|---|---|
| **1. Strategic** | ★ Whose problem, why it matters, why now (window, technology) | Which stakeholders confirmed the problem; scope of the first offer | ★ Current state, ambition, gap; **the constraint on T now** | Portfolio of units and markets; dependencies between them |
| **2. Economic** | Options, including not doing it; the cheapest test | Per experiment: hypothesis, cost, signal to stop or pivot | Per initiative: options, expected ΔT, risks; **the owner's decision inventory** | Capital allocation across initiatives and units (ΔT per unit of capital) |
| **3. Commercial** | Who would pay, and how; alternatives they use today | ★ Who actually pays; value proposition; first channel; key suppliers | Market share, competitors, contracts, partners; for a trading company the payer is the market | New markets and segments; partner and channel economics |
| **4. Financial** | Cost of the test | ★ **Burn and runway by date**; funding to reach fit | T, I, OE, NP; cash by date; unit economics | Funding rounds, cost of capital, capital structure |
| **5. Management** | One human and as many agents as the work needs: even 50–100 for research, prototypes and stakeholder study | First systems and first humans beside the agents; first rules from incidents | Dozens to hundreds of agents mixed with several humans; many systems; structure keepers; decision rights, risk appetite, benefit measurement | Units with their own owners (human or agent); many humans; governance (board, shareholders); delegation of risk |
| **5a. Competence** (part of the management case) | Methods installed from Bench; research captured as documents, not chat | Incident → rule; first methods recovered from traces | Methods recovered from the event log (A.3.1.MR); briefs and position histories; the organization's own Bench packages | A shared competence library across units; packages exported, published or sold |
| **6. Regulatory** | Is it legal at all, and where (jurisdiction choice) | Entity, licences needed **before** launch, tax registration | Obligations calendar, licence status, AML, data, AI rules | Each new jurisdiction; group structure; audits |

Founder, 2026-11-05, on the management case:
- **Agents appear at every stage**, and deploying them is almost unlimited. Even at the idea stage 50 or 100 agents can do the work. So the stages differ not in whether there is a workforce, but in how much there is to manage. As the company moves from idea to start to operation to scale, there are more agents, more humans mixed with agents, more systems, more money, more risk and more complexity.
- **Most important: experience accumulates inside the company.** It must be digitized as competence and become an asset of AI.

Our reading:
- Competence is the organization's capital that grows with every stage. It consists of methods, rules with their origins, briefs, position histories and packages, recovered from the event log.
- It links the management case to goal 2 (teaching AI to build systems, [`mission.md`](mission.md)) and to Bench, where an organization's own packages can be published or sold ([`framework.md`](framework.md)).

How to read it:
- **The leading case decides onboarding's first pains.** For the first user organization (S2) these are the constraint on T and the decision inventory, which is what this session already found.
- **The regulatory case never leads, but a missed deadline jumps every queue** at every stage.
- **The economic case moves from the organization to the initiatives** as the company grows. At S0 it is the whole decision; from S2 on it is many small cases.

## Gradual filling

The five cases are the *map*, not a form. Gradual onboarding ([`onboarding.md`](onboarding.md)) fills one slice per relieved pain. The owner sees the map with each area's maturity.

Two cases have deadlines and legal weight: the financial case (cash by date) and the regulatory case. Their gaps jump the queue.

## Open questions

- Is the six-case skeleton right, or does the founder prefer a lighter first view (for example the Business Model Canvas)?
- One business case per organization, or also one per unit, as the value cycle repeats at every level?
