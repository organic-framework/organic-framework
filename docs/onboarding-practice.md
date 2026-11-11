# Onboarding practice: how professionals study a company, and the onboarding unit it gives Organic Framework

Status: **Proposal**, 2026-11-10, by `of_onboarding-research`; reviewed by `of_architect`. It is the source of the onboarding package (`bench/onboarding/`): every role and step of the package cites this document. The onboarding keeper, `of_onboarding` from 2026-11-11, keeps it current from runs.

It was written after the founder rejected an onboarding design whose roles had no source (ask `a4fb46ee`): "find SotA practices for studying the company, its business case and its systems, and set up agents by that practice, not the ones you made up".

## Summary

- **No single method covers a company's business case, systems and organization at once.** The professions that must understand a company fast and then act in it each cover a part:
  - investors run due diligence;
  - organization-development consultants run a diagnosis;
  - enterprise architects describe the baseline architecture;
  - lean practitioners map a value stream;
  - systems engineers describe the system of interest and its stakeholders.
- **They share one skeleton**, and that skeleton is the practice onboarding should follow:
  1. contract with a sponsor;
  2. start from the presenting problem;
  3. read the data before interviewing;
  4. build a baseline per lens, in parallel, each by its specialist;
  5. integrate the findings as misfits ranked by consequence;
  6. feed back once, and let the sponsor decide;
  7. plan the future state in increments;
  8. re-measure, then hand over or close.
- **The roles follow from it.** The practice calls for two people and five agent roles:
  - people: a sponsor (the company's head) and a champion (a person in the company who owns the first value stream);
  - agents: an onboarding lead who runs the cycle and has no stake in the result, and four lens specialists: a business-case analyst, a system architect, a structure keeper and a flow analyst.
- **Against the rejected v1:** v1 named five roles without a source: owner, curator, structure keeper, system architect, analyst. The practice confirms four of them with sources. It turns the analyst into a flow analyst, and it adds two roles that v1 lacked: the onboarding lead (the facilitator) and the business-case analyst (the founder named the business case on ask `a4fb46ee`).
- **What changes when the company runs agents:**
  - its traces (git history, recorded sessions, the event log) replace much of the interviewing;
  - the agents people run on their own are the informal organization that diagnosis models already look for;
  - the sponsor and the champion stay people.
- **The first trial run** (§8): a desk study of steps 3–5 on an operating company. It added the norms that step 4 checks against (the strategy, the stakeholder set, the lines of control) and eight lessons; a re-check after the structure was applied added three more (§8.3), and the package added ten from the same runs (§8.4). The roles and steps above are unchanged, except that each person now has a conductor from step 1 ([`conductor.md`](conductor.md)).
- **The package** that applies this practice is `bench/onboarding/`: the unit blueprint, a brief per role, the run as one work, and the charter, role-design and re-check templates. `of init` writes a new organization from it ([`onboarding.md`](onboarding.md)).

## 1. The practices

### 1.1 Due diligence: commercial, operational, technical

- **What it is.** A buyer's study of a company before a deal. It is organized as **workstreams, one per lens**, each run by a specialist with its own checklist:
  - financial, legal, commercial;
  - human resources, environmental;
  - IT and production technology.

  This is Howson's chapter structure ([Howson, *Due Diligence: The Critical Stage in Mergers and Acquisitions*, Gower 2003; Routledge 2017](https://www.routledge.com/Due-Diligence-The-Critical-Stage-in-Mergers-and-Acquisitions/Howson/p/book/9781138246522)). The author directs a commercial due-diligence firm.
- **What each lens asks** (practitioner guides; weak evidence, vendor material):
  - commercial: will the market keep producing the revenue the plan assumes;
  - operational: can the company deliver the plan with the machinery it has, and what fixing it costs;
  - technical: architecture, security, dependence on vendors and end-of-life technology.

  Sources: [Papermark, operational due diligence](https://www.papermark.com/blog/operational-due-diligence), [BD Emerson](https://www.bdemerson.com/service/commercial-operational-technology-due-diligence).
- **What it produces:**
  - findings with red flags graded by severity;
  - the lower grades become items of a value-creation or 100-day plan; the higher ones reprice or stop the deal.

  The same guides; the grading scales differ by vendor.
- **What OF takes:**
  - parallel workstreams per lens, each with its specialist;
  - findings graded by consequence;
  - findings turned into a plan of increments.

### 1.2 Organizational diagnosis and the consulting cycle

- **Weisbord's six boxes** ([Weisbord 1976, *Group & Organization Studies* 1(4): 430–447, doi:10.1177/105960117600100405](https://doi.org/10.1177/105960117600100405)):
  - six places to look: purposes, structure, relationships, rewards, leadership and helpful mechanisms, inside an environment;
  - each box is diagnosed in its formal and its informal form;
  - "process" trouble shows up as blocked work, freed by working on one or more boxes.

  It is a practice theory, not a validated model ([Weisbord PDF copy](https://leeds-faculty.colorado.edu/Rosse/Courses/4003/Readings/Weisbord.pdf)).
- **Nadler and Tushman's congruence model** ("A model for diagnosing organizational behavior: Applying a congruence perspective", *Organizational Dynamics*, Autumn 1980, pp. 35 ff.):
  - an organization turns inputs (environment, resources, history, strategy) into outputs through four components: the work, the people, the formal organization and the informal organization;
  - performance follows the **fit** between them, so a diagnosis looks for misfits ([summary](https://thinkinsights.net/strategy/nadler-tushman-congruence-framework)).
- **The Burke-Litwin model** ([Burke and Litwin 1992, *Journal of Management* 18(3): 523–545, doi:10.1177/014920639201800306](https://doi.org/10.1177/014920639201800306)):
  - twelve factors with hypothesized causal links;
  - **transformational** factors (the external environment, mission and strategy, leadership, culture) change only through the top;
  - **transactional** factors (structure, management practices, systems and policies, climate, task and skills, needs and values, motivation) change in daily management.
- **Block's consulting cycle** ([Block, *Flawless Consulting*, 4th ed., Wiley 2023](https://www.wristers.nl/boek/9781394177301)):
  1. entry and contracting: wants, responsibilities and concerns are agreed with the client before any data is gathered;
  2. discovery and dialogue: the presenting problem is the start, not the answer;
  3. feedback and the decision to act: the consultant turns the data into a few findings, and the client decides;
  4. engagement and implementation;
  5. extension, recycle or termination.

  Phase names are from [secondary summaries](https://www.supersummary.com/flawless-consulting/summary).
- **What OF takes:**
  - Block's cycle as the run's backbone;
  - the six boxes and the congruence model as the organization lens, formal against informal;
  - Burke-Litwin's split to route each finding: transformational ones to the head, transactional ones to the keepers.

  OF does not take Burke-Litwin's survey instrument: it reads no surveys and has none to give.

### 1.3 Enterprise architecture discovery

- **TOGAF ADM Phase A, Architecture Vision** (The Open Group, TOGAF Standard 10th ed.; the text is behind sign-in at [pubs.opengroup.org](https://pubs.opengroup.org/togaf-standard/adm/chap03.html); the lists below follow [Visual Paradigm's guide](https://togaf.visual-paradigm.com/2025/01/20/phase-a-architecture-vision-in-togaf-adm/)):
  - **steps:**
    - identify the stakeholders, their concerns and business requirements;
    - confirm the business goals, drivers and constraints;
    - evaluate the current capabilities;
    - assess readiness for transformation;
    - define the scope with its exclusions;
  - **outputs:**
    - an approved **Statement of Architecture Work**;
    - the Architecture Vision;
    - a Capability Assessment;
    - a Communications Plan.
- **Phases B–D**: a **baseline** and a target per domain (business, data, application, technology), and a **gap analysis** between them. The Architecture Definition Document spans all four ([Visual Paradigm ADM guide-through](https://circle.visual-paradigm.com/docs/togaf-adm-guide-through/phase-b-business-architecture/how-to-develop-architecture-definition-document/)).
  - The full ADM is too heavy for a founder-led organization of people and agents (our judgment). What OF takes from TOGAF is Phase A's contract and stakeholder steps, and the baseline-then-gap pattern.
- **BIZBOK** (Business Architecture Guild, [v14 Part 1](https://cdn.ymaws.com/www.businessarchitectureguild.org/resource/resmgr/bizbok_14/v14_Part1.pdf)):
  - four core domains: **capabilities, value streams, organization and information**;
  - stakeholder mapping is an extended domain;
  - the organization map is the units and their decomposition ([Bizzdesign](https://bizzdesign.com/blog/the-business-architects-toolbox-organization-mapping)).
  - Capability maps are automation-agnostic, so they hold whether a human or an agent does the work.
- **ISO/IEC/IEEE 42010:2022**:
  - stakeholders, their concerns, and views that each answer named concerns;
  - correspondences between descriptions ([sample](https://cdn.standards.iteh.ai/samples/74393/fc7b7f103d8446a4b87a3261e31370d3/ISO-IEC-IEEE-42010-2022.pdf)).
- **ISO/IEC/IEEE 15288:2023**:
  - the business or mission analysis process (6.4.1) and the stakeholder needs and requirements definition process (6.4.2) ([iTeh catalogue](https://standards.iteh.ai/catalog/standards/iso/4109ef6c-8070-4ebe-89d8-c45ae8413981/iso-iec-ieee-15288-2023));
  - the system of interest, its elements and its enabling systems, already used in [`docs/systems.md`](systems.md);
  - FPF's holons and SYSE.Reference, cited there.
- **Architecture evaluation**: ATAM checks an architecture against stakeholders' quality-attribute scenarios ([Kazman, Klein, Clements 2000, CMU/SEI-2000-TR-004](https://en.wikipedia.org/wiki/Architecture_tradeoff_analysis_method)). It belongs after onboarding, when a change is weighed, not in the first study.
- **What OF takes:**
  - the contract (Statement of Architecture Work);
  - stakeholders and concerns;
  - a baseline per lens, then the gaps;
  - value streams and an organization map;
  - the system breakdown OF already uses.

### 1.4 Value stream mapping

- **Rother and Shook** ([*Learning to See*, Lean Enterprise Institute 1999](https://www.lean.org/store/book/learning-to-see/)):
  - choose a product family;
  - a **value stream manager** answers for the current-state map, the future-state map and the plan between them;
  - draw the current state, looking for every source of waste;
  - draw the future state;
  - plan the implementation in segments.
- **Martin and Osterling** (*Value Stream Mapping: How to Visualize Work and Align Leadership for Organizational Transformation*, McGraw-Hill 2014; [notes](https://danlebrero.com/2021/03/10/value-stream-mapping-summary/)):
  - **roles:**
    - an **executive sponsor**, mandatory, who "oversees the entire value stream" and is accountable for results;
    - a **value stream champion**, who owns the transformation plan;
    - a **facilitator**, mandatory, who "should not oversee or work in the value stream";
    - a mapping team of five to seven people;
  - **steps:**
    - a **charter**: scope, problems, target condition, accountable parties and team;
    - the current state, walking the stream;
    - the future state;
    - a transformation plan;
    - regular reviews;
  - **metrics:** process time, lead time, percent complete and accurate (%C&A), activity ratio and rolled %C&A;
  - **knowledge work** needs narrow scoping: "most office and service value streams will require more than one value stream map".
- **What OF takes:**
  - the sponsor, champion and facilitator split;
  - the charter;
  - one value stream at a time, chosen from the head's pain;
  - lead time and waits as the measure before and after, taken only from what OF records (§5). Process time, %C&A and activity ratio need data OF does not collect from a company of people, so they stay gaps.

### 1.5 Reading the traces before asking: process mining and code history

- **Process mining** ([IEEE Task Force, Process Mining Manifesto, BPM 2011 Workshops, LNBIP 99, doi:10.1007/978-3-642-28108-2_19](https://doi.org/10.1007/978-3-642-28108-2_19)): three uses of an event log:
  - **discovery** builds a process model from the log with no prior model;
  - **conformance** compares a model with the log;
  - **enhancement** extends a model from the log.

  OCEL 2.0 is its object-centric log format ([ocel-standard.org](https://www.ocel-standard.org/)).
- **Behavioural code analysis** ([Tornhill, *Your Code as a Crime Scene*, Pragmatic Bookshelf 2015](https://reflectoring.io/book-review-your-code-as-a-crime-scene/)), all from version-control history:
  - hotspots;
  - temporal coupling, meaning files that change together;
  - authors per area, and knowledge loss.
- **Socio-technical congruence** compares the coordination the work requires (from co-change) with the coordination that happens. It is the best-validated structural diagnostic ([Cataldo, Herbsleb, Carley, ESEM 2008](https://researchr.org/publication/CataldoHC08)).
- **What OF takes:** the data-first order. OF already writes the event log (hooks), imports git and GitHub, and computes co-change. That is the basis of [`docs/onboarding.md`](onboarding.md)'s "infer first, ask second".

### 1.6 Decision rights

- **RAPID** ([Rogers and Blenko, "Who Has the D?", *Harvard Business Review*, January 2006](https://store.hbr.org/product/who-has-the-d-how-clear-decision-roles-enhance-organizational-performance/R0601D); [Bain](https://www.bain.com/insights/who-has-the-d/)):
  - five decision roles: recommend, agree, perform, input, decide;
  - for each important decision, exactly one role "has the D";
  - the authors present clear decision roles as the way to unclog decision bottlenecks.
- **Why it matters for the first adopter:** the friend's company's head is worn out by confusion and bureaucracy. That is the presenting problem this lens addresses.
- **What OF takes:**
  - a decision list with its single decider;
  - RFC 0007's `owns: {decides: true}` per cell, and delegations with envelopes, are OF's form of "who has the D".

### 1.7 A new leader's first months

- **Watkins** (*The First 90 Days*, Harvard Business Review Press 2003, updated 2013; [summary](https://www.supersummary.com/the-first-90-days/part-2-summary)):
  - diagnose the situation first: start-up, turnaround, accelerated growth, realignment or sustaining success (STARS);
  - make a learning plan: who keeps the knowledge, which sources to read first;
  - secure early wins.
- **What OF takes:**
  - the situation decides what to study first; [`docs/business-case.md`](business-case.md) already does this with its stages S0–S3;
  - early wins, which [`docs/onboarding.md`](onboarding.md) calls "value before invasion".

### 1.8 How strong the evidence is

- **Validated:**
  - socio-technical congruence predicts failures and resolution time in software organizations;
  - process mining is a mature, standardized method.
- **Practice theories:** Weisbord, Nadler-Tushman, Block, VSM and RAPID. They are widely used and taught, and only weakly validated. Later studies that test Burke-Litwin are cited in summaries, and I could not check them.
- **Professional consensus:** due diligence's workstream structure and TOGAF are documented in books and standards, not tested by experiment.
- **None of them has been tested on organizations of people and agents.** OF's own runs are the test: every run feeds back into the path, as [`docs/onboarding.md`](onboarding.md) rule 7 already says.

## 2. The skeleton they share

| # | Step | Due diligence | Consulting and diagnosis | Enterprise architecture | Value stream mapping |
|---|---|---|---|---|---|
| 1 | **Contract** with a sponsor: question, scope, access, exclusions | the engagement and its scope | Block 1: entry and contracting | Phase A: Statement of Architecture Work, approved | the charter |
| 2 | **The presenting problem** and the situation | the deal thesis | Block 2: the presenting problem; Watkins: STARS | business goals, drivers, constraints | the problems and target condition in the charter |
| 3 | **Data before interviews** | the data room and its questions (the order varies by deal) | discovery | the existing descriptions | walking the stream; process mining (§1.5) |
| 4 | **A baseline per lens**, in parallel, by specialists | one workstream per lens | the six boxes; congruence components | baselines B, C, D | the current-state map |
| 5 | **Integrate**: misfits and gaps, ranked by consequence | red flags by severity | congruence misfits; Burke-Litwin routing | gap analysis | waste and the metrics |
| 6 | **Feed back once**; the sponsor decides | the investment decision | Block 3: feedback and the decision to act | approval of the vision | the future state agreed |
| 7 | **The future state in increments** | the value-creation or 100-day plan | Block 4: engagement | transition architectures | the transformation plan |
| 8 | **Re-measure; hand over or close** | — | Block 5: extension, recycle or termination | the next cycle | reviews against the metrics |

## 3. The roles the practice calls for

| Role | Held by | Does | Source | OF basis | Evidence it produces |
|---|---|---|---|---|---|
| **Sponsor** | the company's head, the unit's `owner` | asks the question; approves the charter; decides on the findings | Block's client; TOGAF Phase A approval of the Statement of Architecture Work; Martin and Osterling's executive sponsor | the organization's owner | the charter approved by an ask id; the answered decision batch |
| **Champion** (v1's "curator") | a person in the company | owns the first value stream end to end; opens its data and people; confirms the current-state map; owns the plan of increments | Martin and Osterling's value stream champion; Rother and Shook's value stream manager | a position held by a person | named in the charter; confirms the current-state map; signs off each increment |
| **Onboarding lead** | an agent, kind `project`: the package's bootstrap position | runs the cycle: drafts the charter, plans the data reading, integrates the four baselines into findings, files the one decision batch, drafts the increments, closes the run | Block's consultant across the five phases; Martin and Osterling's facilitator, who must not work in the stream; the lead who coordinates due diligence's workstreams (Howson) | [`framework.md`](framework.md): a Bench package's bootstrap position; [`onboarding.md`](onboarding.md): the organization run | the charter draft; the findings list; the decision-batch ask; the plan; the closing note |
| **Business-case analyst** | an agent, kind `analyst` | the baseline of how the company makes money and for whom: stakeholders and concerns, value streams, revenue and cost lines, the current constraint | commercial and financial due diligence (Howson); TOGAF Phase A stakeholders, goals, drivers and constraints; BIZBOK value streams and stakeholder map; ISO/IEC/IEEE 15288 6.4.1, business or mission analysis | [`business-case.md`](business-case.md): six cases, each area's maturity | the business-case map with each area's maturity; stakeholders and concerns; the money line per system, for the architect |
| **System architect** | an agent, kind `keeper` | the baseline of what the company makes and runs | technical due diligence (Howson: IT and production technology); TOGAF baselines B–D and gap analysis; ISO/IEC/IEEE 15288 and 42010; Tornhill's code-history analysis | [RFC 0007](rfc/0007-system-architect-and-cells.md) §2.1–2.2; [`systems.md`](systems.md) | the systems map on evidence (`runs`, code, `money`, `confirmed`); repository → system → keeper; open grain questions; unconfirmed facts for the batch |
| **Structure keeper** | an agent, kind `keeper` | the baseline of who does and decides what; keeps the organization model afterwards | Weisbord's six boxes and Nadler-Tushman's congruence, formal against informal organization; BIZBOK organization map; RAPID; socio-technical congruence; HR due diligence (Howson) | [`onboarding.md`](onboarding.md) steps 0, 4 and 5; the registry; RFC 0007 cells with `decides` | people, agents and positions in the registry (L2); briefs with current mandates; the list of key decisions, each with its one decider; misfits between formal and informal |
| **Flow analyst** | an agent, kind `analyst` | the current state of the value stream where the head's pain sits, and the measure before and after, from what OF records only | Rother and Shook's current-state map; Martin and Osterling's lead time; operational due diligence; process mining discovery and conformance | [`onboarding.md`](onboarding.md) step 2, "measure before"; [`success.md`](success.md); `of_studio`'s measures; §5 below | PR lead time and throughput from GitHub; asks, turns and waits from recorded sessions; the metrics it cannot compute (process time, %C&A, activity ratio) listed as gaps; the "before" report; the re-measure |

Notes:
- **Each person has a conductor, and it is not the lead.** Every person who holds or owns a position has one conductor, their one window ([`conductor.md`](conductor.md)). It stays as long as the person works in OF; the lead runs one cycle and leaves. Agents reach the sponsor and the champion through their conductors, and decisions stay cards.
- **The lead is not the structure keeper.** Martin and Osterling's facilitator must not work in the stream, and Block's consultant leaves at termination. The structure keeper keeps the organization after onboarding ends, so it has a stake in the findings. In a small company one agent may still hold several lens roles, as RFC 0007 allows the architect and the keeper to be one position. The lead stays separate.
- **People keep the sponsor and champion roles.** Every source puts the decision with a sponsor who answers for the result.
- **The flow analyst measures only what OF records** (§5). Martin and Osterling's process time, %C&A and activity ratio need each person's time on each item and the receiving step's judgement of what it got. OF does not collect either from a company of people. Those metrics are listed as gaps, never estimated.
- **Lenses the practice has and OF's first version folds in:**
  - legal and regulatory due diligence: the business-case analyst lists it as a gap, and a gap with a deadline jumps the queue (business-case.md, the sixth case);
  - environmental due diligence;
  - financial due diligence on the books: no books are read unless the head grants them.

## 4. The steps, mapped onto OF's onboarding

The organization path in [`docs/onboarding.md`](onboarding.md) has steps 0 to 10, and step 3 moved to the system architect under RFC 0007. The practice keeps most of them and reorders two things:
- the contract comes before anything else;
- the four baselines run in parallel and meet in one integration step before the decision batch.

| # | Step | Source | OF path steps | Who | Done when (evidence) |
|---|---|---|---|---|---|
| 1 | **One window, then the contract.** `of init` creates the organization repository with the owner's conductor as its first position, then the onboarding unit, with no agent started. The sponsor's conductor is the first agent started; the lead talks to the sponsor through it, and the champion gets one on joining ([`conductor.md`](conductor.md) §5). The lead's charter is approved | Block 1; TOGAF Phase A, Statement of Architecture Work; Martin and Osterling's charter | 0 | lead drafts; sponsor approves | `of registry check` passes; the charter names the scope, the champion, what is read (GitHub, sessions people choose to record, the structure) and what is not (mail, chat, meetings); approved by an ask id |
| 2 | **The presenting problem.** The head's last story of confusion or bureaucracy: what he could not find out, or whose decision he waited for | Block 2; Watkins' STARS; business-case.md stages | the onboarding loop, "find the current biggest pain" | sponsor tells; lead records | the story recorded; the first value stream or decision area chosen; the stage (S0–S3) named |
| 3 | **Data first.** Inventory; git and GitHub imported; recorded sessions ingested | Process Mining Manifesto (discovery); Tornhill; onboarding.md's "infer first, ask second" | 1 | system architect and structure keeper | every live agent and repository is visible (L1); the imports exist |
| 4 | **Four baselines, in parallel** | due diligence's workstreams; TOGAF baselines B–D; BIZBOK domains; the six boxes | 2, 3, 4, 5 | business-case analyst, system architect, structure keeper, flow analyst | each baseline exists, each fact sketched or evidenced, and each fact names its evidence |
| 5 | **Integrate.** Misfits and gaps, ranked by consequence; each routed: transformational to the head, transactional to a keeper | Nadler-Tushman; TOGAF gap analysis; due diligence's red flags; Burke-Litwin | the ranking rules in onboarding.md; Studio's diagnosed issues | lead | the findings list, each finding with its lens, evidence, consequence and decider |
| 6 | **Feedback and decision.** One numbered ask | Block 3; TOGAF Phase A approval | 6 | lead files; sponsor decides | the ask is answered; confirmed facts move to `confirmed:` (RFC 0007 §2.2) |
| 7 | **Increments.** The future state as works with checkable ends, non-invasive first | Martin and Osterling's future state and transformation plan; TOGAF transition architectures; the 100-day plan; Watkins' early wins | the TOC loop; 7, 8, 9 | lead with the keepers; champion owns the plan; sponsor approves each invasive batch | each increment is a work with `done_when` and a measure; no dates |
| 8 | **Re-measure; hand over; close** | Block 5; Martin and Osterling's reviews | 10 | flow analyst; lead | the re-measure against step 4's baseline; the keepers keep their maps; the lead's position is finished |

## 5. The measure before and after

The flow analyst's measure. It counts only what OF records about the organization, and lists the rest as gaps.

**Method.**
- **Source:** the organization's event store, filtered to that organization only.
- **Before** is the history imported when the organization joins: `of import` for the sessions people choose to record, and `of import github` for merged pull requests.
- **After** starts when the hooks are installed on the machines of the people who record their sessions.
- **One method for both windows**: the same series and the same filters. A value from another method is never compared with these.
- **Each window marks** its partial days and outages, such as a machine restart.

**Series.**

| Measure | Read from | Events |
|---|---|---|
| Throughput: merged pull requests per day | GitHub | `of.work_record` from GitHub, by `merged_at` |
| Lead time of a pull request: opened to merged, the median | GitHub | `of.work_record`, `created_at` to `merged_at` |
| Agents working at once: the mean over 5-minute buckets in working hours | recorded sessions | `of.turn_end` |
| The head's prompts per day, and merged pull requests per prompt | recorded sessions | `of.human_prompt` with origin `human` |
| The head's queue: declared asks per day, and the median time to an answer | the owner's queue | `of.agent_question`, `of.agent_question_answered` |
| Asks settled below the head: the share of unit owners' asks not escalated | the owner's queue | asks and their escalations |
| Waits: time positions wait on the head or on a peer | declared states | `of.state_declared`; the listener's waits view |

**How OF computes them** (`of model measures`, method `fl-v0`, `docs/studio.md` §3.4b, by `of_studio`, 2026-11-11): each series prints its value, or `gap: <the missing input>` when the window holds none of what it reads.
- **Working hours** are the 5-minute buckets with at least one session in a turn. No calendar is assumed.
- **Bots' pull requests** (a merge queue's own) are counted apart from the throughput.
- **Commits** from `of import git` (an organization that never pushes to GitHub) are a count beside the series, never pull requests.

**Gaps, never estimated.**
- Process time, %C&A, activity ratio and rolled %C&A (§1.4). They need each person's time on each item and the receiving step's judgement of what it got. OF does not collect either from a company of people.
- Work outside GitHub, and the sessions people do not record. The before report says what share of people record their sessions, so the reader knows what the numbers cover.

## 6. What a company of people and agents changes

- **Traces replace much of the interviewing.**
  - Process mining builds a model from the log before anyone draws one, and code-history analysis finds owners and coupling before anyone is asked.
  - In an OF company, the event log, git history and recorded sessions are that log. The head is asked only to confirm or correct a sketch, in one batch.
- **People's own agents are the informal organization.**
  - Weisbord and Nadler-Tushman diagnose the formal and the informal organization side by side. In the friend's company people run their own agents and nothing is centralized.
  - The structure keeper's inventory is the informal organization, and the registry is the formal one. The misfits between them are findings.
- **What OF cannot read.** OF reads GitHub, the sessions people choose to record, and the structure. It does not read the head's mail, chat or meetings. The charter says so (step 1), and the business-case analyst and the structure keeper mark what stays unknown as gaps, not as guesses.
- **Lens roles can be agents; the sponsor and the champion cannot.** Each source puts the decision with an accountable sponsor, and the champion with the people who do the work.

## 7. What OF's first version leaves out

- TOGAF's full ADM, ATAM, Burke-Litwin's survey instrument, and target architectures beyond the first value stream: too heavy for the first study (§1.3, §1.2).
- Legal, environmental and book-level financial due diligence: listed as gaps until the head grants access or names a deadline (§3).
- Interviews outside the head and the champion: the friend's company is reached only through the founder.

## 8. Lessons from trial runs

### 8.1 The norms step 4 checks against

The trial showed that a baseline built from traces finds only what agents touch. Each lens therefore starts from a published norm and records, part by part, what exists, its maturity (unknown, sketched, evidenced, kept current) and its evidence.

**The business case.** The six cases in [`business-case.md`](business-case.md): the Green Book's five, plus the regulatory case.

**The strategy.** A goal is not a strategy. The business-case analyst checks each part:

| Part | What it is | Source |
|---|---|---|
| Objective | one specific, measurable, time-bound end | Collis and Rukstad; Lafley and Martin's winning aspiration |
| Scope | where the company plays, and what it will not do | Collis and Rukstad; Lafley and Martin's where to play; Hambrick and Fredrickson's arenas |
| Advantage | why it wins there, against whom | Collis and Rukstad; Lafley and Martin's how to win; Hambrick and Fredrickson's differentiators |
| Diagnosis | the critical challenge; in OF, the constraint | Rumelt's kernel; Goldratt |
| Guiding policy | the overall approach to the challenge | Rumelt's kernel |
| Coherent actions | coordinated steps with resources, in an order | Rumelt's kernel; Hambrick and Fredrickson's vehicles and staging |
| Economic logic | how returns exceed costs | Hambrick and Fredrickson |
| Capabilities and management systems | what the company must be able to do, and how it steers | Lafley and Martin |
| Risk appetite and limits | the risk the strategy accepts, as quantitative limits with roles | COSO ERM 2017; FSB 2013 |
| Measures | how the strategy is followed | Kaplan and Norton |

**The stakeholder set.** The business-case analyst checks:
- **who counts as a stakeholder:** anyone who can affect or is affected by the company's objectives (Freeman);
- **a checklist of classes:** owners and the financial community, employees, customers, suppliers, competitors, governments and regulators, communities (Freeman; ISO 26000 §5.3);
- **primary and secondary stakeholders:** without the primary ones the company cannot go on (Clarkson);
- **salience:** power, legitimacy and urgency (Mitchell, Agle and Wood);
- **coverage per interest:** who serves, observes, protects against or relates to it.

**The lines of control.** The structure keeper checks the management functions in [`method.md`](method.md), and then the lines:
- a governing body;
- a first line that delivers and owns its risk;
- a second line that monitors and challenges risk apart from the first;
- a third line of independent assurance (IIA Three Lines Model).

In a trading business, trading is kept apart from its risk control and its books. Barings fell when one trader ran both trading and settlement (Board of Banking Supervision 1995). Mintzberg's five parts (strategic apex, middle line, operating core, technostructure, support staff) show where a missing function would sit.

### 8.2 The first trial: a desk study of an operating company (2026-11-10)

Steps 3 to 5 were run as a desk study on an operating company (stage S2) that OF had onboarded by its earlier path. The company's record stays in its own repository. Lessons:

1. **Norm before traces.** Every gap the norms of §8.1 found was absent from the traces. Agents work on the product. The company as a legal and financial entity (books, tax, licences, investors, treasury) leaves no trace until something goes wrong.
2. **The strategy needs its own baseline.** The company had a ranked goal with a measure, a named constraint and an ordered list of works. It had no target number, no written scope (nor what it will not do), no advantage claim, no risk appetite with limits, and no capital allocation. The goal and the constraint looked like a strategy until they were checked part by part.
3. **Check the measure before the "before" report.** A business measure the company relied on was wrong by an order of magnitude in one class. It was found only when a doer decomposed it item by item. The flow analyst is independent of the lead (§3) for the same reason. In multi-agent systems, failures of verification are about a fifth of all failures, and a separate verification step improved results (MAST, Cemri et al.). That independence extends to the company's own business measures: the baseline names each measure's source, and whether and how anyone has checked it.
4. **Check the lines, not only the functions.** A function can have a position and still lack independence. In the trial, the keeper of the main measure sat in the unit that measure judges, and no position watched exposure. The function list did not show this; the second-line question did.
5. **Gaps that nobody raises stay.** An earlier study had ranked the company-side gaps first. A week later they had not moved: no agent works there, so nothing in the daily flow raises them. Step 5 files such gaps as decisions with their consequences, apart from the improvement findings, and step 8 checks them again.
6. **Reuse earlier baselines by their dates.** With an earlier stakeholder map and system breakdown, the trial was a delta: read each baseline's date, check what changed since, and mark each row changed or the same. A baseline nobody keeps goes stale. The stakeholder map needs a keeper, as the systems map has one.
7. **Metadata can mislead; ask where each record is kept.** Dates and counts in a document store suggested the books were dormant. They were not: outsourced accountants kept them in an accounting service, and the store was a local draft system. Before a baseline calls a record missing, it names the system of record for it (the accounting service, the bank, a provider's portal) and checks there, and the charter (step 1) lists those systems and the access to each. Metadata is a first pass, never the finding.
8. **External parties are part of the role, not a reason to stop.** The first design marked items that need a bank, an adviser or an authority as the owner's. The owner's correction: an agent does the work with the external party (a person or a service), with read-only access first and per-party permission to write; only signing, moving money, identity checks and strategy choices stay with people. Each role therefore names its external party, its channel and its access. Where several stakeholders each set their own requirements (tax, licensing, AML, a virtual-asset regulator, another jurisdiction), the practice keeps one obligations register per stakeholder with an owner (ISO 37301 §4.5), and the watchers run in parallel under one coordinator (Kim et al.).

### 8.3 The first re-check after a structure was applied (2026-11-11)

The same operating company applied the designed structure in one evening. A re-check a few hours later, counted from the records rather than from status notes, taught three things:

1. **Positions produce findings within hours; closure waits on the outside.** The new watchers and keepers had registers, a calendar and several findings the company had not seen within hours. None of their questions to an external party had an answer, because no agent could reach one: every channel still needed the owner's permission for that party and a login task. The charter (step 1) therefore sets up the external channels, each party with its writer, its channel and the login task, as soon as the roles exist, not after.
2. **New roles flood the owner's queue unless batching is designed into every unit.** The unit whose coordinator batched its findings sent one batch. The roles that reported to the owner directly sent cards one by one, more than twenty in the first hours. Each unit gets a batching point from the start, and the owner's open items from the new structure are measured per day (§5 measures the queue).
3. **The re-check counts the records.** It counts register rows with a source, dated duties present in the calendar, questions answered by their external party, gaps routed, and live holders. Each item is closed only when its own `done_when` evidence exists. The coordinator's own rollup agreed with the count. When a rollup and the count disagree, that disagreement is itself a finding.

### 8.4 What the package adds from the same runs (2026-11-11, `of_onboarding`)

The onboarding package (`bench/onboarding/`) cites these besides §8.1–§8.3. Each comes from the same operating company's run or from the OF team's own practice, and each names where it changed the package.

1. **People are positions.** The registry lists people, a person holds a position as an agent does, and an agent that works for a person names that person's position as its owner ([`registry.md`](registry.md), People, 2026-11-11). It was added when the operating company's colleagues began to join. Package: the sponsor is a position held by a person from `of init` on; the structure keeper adds each person the charter names, with their conductor (steps 1 and 3).
2. **Each mandate against its work.** In the operating company, work went to positions whose mandates did not cover it, and nobody checked before handing it out. A mandate audit compared each brief with the work its position did, and a manager now runs `of delegate` before giving a position new work. Package: the structure baseline checks each mandate against its work (step 4), and every hand-out in step 7 starts with `of delegate`.
3. **A watch over open requests.** The OF team's conductor checked each of the founder's open requests on a short cycle and nudged a stalled owner, overnight from 2026-11-10. Without it, a request that reached its owner could still stop there unseen. Package: the conductor keeps the watch (step 7, and through the run).
4. **Waves sized by capacity.** The operating company started its new positions at most four first runs at once, because the accounts' weekly limits were already more than half spent. Between steps it waited until the guard measure could show a regression, not for a calendar day ([`rollout.md`](rollout.md)). Package: the data wave and the lens wave (steps 3 and 4) and every wave of new roles (step 7) are sized by `of capacity status`.
5. **Honest green.** The owner asked that nothing be shown as done unless it is. An item closes only with its own `done_when` evidence, counted from the records by a position that did not build the structure: the builder's rollup is checked, never trusted (MAST, Cemri et al.: verification failures are about a fifth of multi-agent failures). Package: the re-check template; the flow analyst or the business-case analyst counts it, never the lead or the structure keeper (step 8).
6. **The conductor's practice.** The OF team's conductor received corrections on its first two days, each from a slip: a person who talks in the conductor's pane needs `owner_chat`, or every question asked there becomes a card; the ledger is a structured file the panel reads, never committed; `of delegate` before routing found overlaps and a gap no mandate covered; a nudge sent while its owner was committing, so the watch reads the pane first, nudges at most once per owner per thread per 30 minutes, and never wakes the person at night; a merged fix was reported as live, so each state is named by its evidence (planned, merged, deployed where, verified live); a card answered in chat travels verbatim, with its time, to the card's owner, who closes it; a flooded queue is grouped by the person's goals and by risk, and batched at the source. Package: the conductor's brief, "Your practice".
7. **Who does what, from the records first.** In the operating company, the registry put a colleague on systems the commits showed they did not keep: the owner's words had been retold as a claim (2026-11-11). Before any person gets a position, the structure keeper builds the people map from the repositories (authors by system, from `of import git` and `of import github`, each with its window), and the head confirms it on one card. Every person-held claim then carries `evidence:`: the authorship with its window, or the head's or the person's word as an ask id, quoted verbatim ([`registry.md`](registry.md), People). Package: step 3 builds the map and its card before any person's position; `of init` records the command that named the head as the evidence of the sponsor's holder.
8. **The head may not be at a terminal.** A user organization's head reached their conductor through Claude's Remote Control, from another computer, while OF ran on a separate always-on machine; a command the conductor asked them to type into the chat did not run (2026-11-11). Agents never ask a person to run a command: they ask in words ("tell me when you are ready to start the onboarding") and run commands themselves. What only the person can do (log in, trust a folder, accept a warning, approve a key) is a step in plain words with its place (the machine, the app or page, what to choose), filed as a task. Package: the conductor starts the lead itself when the head says they are ready, and the lead starts the waves; without herdr, each agent starts in a detached `screen` session from the lines in `onboarding/README.md`; the rule is in every brief that talks to the head and in the organization's `AGENTS.md`.
9. **What a person hands over is read.** In the same organization a person gave the onboarding a link to a whiteboard and said to read it, and the onboarding answered that it could not read anything the charter did not list (2026-11-11). The charter limits what agents read on their own initiative, never what a person hands them: the head's, the sponsor's, the champion's or the source owner's "read this" is the permission, recorded in the charter's section 4 with their words and the time, and the source is read at once. When it cannot be opened (a board behind a sign-in, which a web fetch cannot open), the agent says so plainly and offers the easiest way: an export, a view link anyone can open, or a connector bound to the position ([`tools.md`](tools.md)), signed in to once by the person. Package: the conductor's, the lead's, the system architect's, the business-case analyst's, the sponsor's and the champion's briefs, the charter's section 4, the organization's `AGENTS.md`.
10. **The messages must arrive.** In the same organization the relay, the service that delivers messages between agents, never started at install, so the conductor's first message to the onboarding lead never arrived, and nothing said so (2026-11-11). `of init` installs the relay and the listener as services and checks that each runs, failing loudly when one does not; the conductor checks that its messages arrive and starts the relay itself when they do not. Package: `of init` step 8; the conductor's brief.

## Sources

- Block, P. *Flawless Consulting: A Guide to Getting Your Expertise Used*, 4th ed. Wiley, 2023.
- Board of Banking Supervision. *Report of the Board of Banking Supervision Inquiry into the Circumstances of the Collapse of Barings.* HMSO, July 1995.
- Burke, W. W., Litwin, G. H. "A causal model of organizational performance and change." *Journal of Management* 18(3): 523–545, 1992. doi:10.1177/014920639201800306.
- Business Architecture Guild. *BIZBOK Guide* v14, Part 1. https://cdn.ymaws.com/www.businessarchitectureguild.org/resource/resmgr/bizbok_14/v14_Part1.pdf
- Cataldo, M., Herbsleb, J., Carley, K. "Socio-technical congruence." ESEM 2008. https://researchr.org/publication/CataldoHC08
- Cemri, M., et al. "Why Do Multi-Agent LLM Systems Fail?" NeurIPS 2025 Datasets and Benchmarks. arXiv:2503.13657.
- Clarkson, M. B. E. "A stakeholder framework for analyzing and evaluating corporate social performance." *Academy of Management Review* 20(1): 92–117, 1995.
- Collis, D. J., Rukstad, M. G. "Can You Say What Your Strategy Is?" *Harvard Business Review*, April 2008.
- COSO. *Enterprise Risk Management: Integrating with Strategy and Performance.* 2017.
- Financial Stability Board. *Principles for an Effective Risk Appetite Framework.* 2013. https://www.fsb.org/uploads/r_131118.pdf
- Freeman, R. E. *Strategic Management: A Stakeholder Approach.* Pitman, 1984.
- Hambrick, D. C., Fredrickson, J. W. "Are you sure you have a strategy?" *Academy of Management Executive* 15(4), 2001; reprinted 19(4): 51–62, 2005.
- Howson, P. *Due Diligence: The Critical Stage in Mergers and Acquisitions.* Gower 2003; Routledge 2017.
- IEEE Task Force on Process Mining. "Process Mining Manifesto." BPM 2011 Workshops, LNBIP 99, 2012. doi:10.1007/978-3-642-28108-2_19.
- ISO 26000:2010, Guidance on social responsibility, §5.3.
- ISO 37301:2021, Compliance management systems — Requirements with guidance for use, §4.5.
- ISO/IEC/IEEE 15288:2023, System life cycle processes; ISO/IEC/IEEE 42010:2022, Architecture description.
- Kaplan, R. S., Norton, D. P. "The Balanced Scorecard: Measures that Drive Performance." *Harvard Business Review*, January–February 1992.
- Kazman, R., Klein, M., Clements, P. *ATAM: Method for Architecture Evaluation.* CMU/SEI-2000-TR-004, 2000.
- Kim et al. 2025. A controlled study of 180 multi-agent configurations. arXiv:2512.08296.
- Lafley, A. G., Martin, R. L. *Playing to Win: How Strategy Really Works.* Harvard Business Review Press, 2013.
- Martin, K., Osterling, M. *Value Stream Mapping.* McGraw-Hill, 2014.
- Mintzberg, H. *The Structuring of Organizations.* Prentice-Hall, 1979.
- Mitchell, R. K., Agle, B. R., Wood, D. J. "Toward a theory of stakeholder identification and salience." *Academy of Management Review* 22(4): 853–886, 1997. doi:10.5465/amr.1997.9711022105.
- Nadler, D. A., Tushman, M. L. "A model for diagnosing organizational behavior: Applying a congruence perspective." *Organizational Dynamics*, Autumn 1980.
- Rogers, P., Blenko, M. "Who Has the D? How Clear Decision Roles Enhance Organizational Performance." *Harvard Business Review*, January 2006.
- Rother, M., Shook, J. *Learning to See.* Lean Enterprise Institute, 1999.
- Rumelt, R. P. *Good Strategy Bad Strategy.* Crown Business, 2011.
- The Institute of Internal Auditors. *The IIA's Three Lines Model: An update of the Three Lines of Defense.* July 2020.
- The Open Group. *The TOGAF Standard*, 10th ed., ADM Phases A–D.
- Tornhill, A. *Your Code as a Crime Scene.* Pragmatic Bookshelf, 2015.
- Watkins, M. *The First 90 Days.* Harvard Business Review Press, 2003; updated 2013.
- Weisbord, M. R. "Organizational diagnosis: Six places to look for trouble with or without a theory." *Group & Organization Studies* 1(4): 430–447, 1976. doi:10.1177/105960117600100405.
