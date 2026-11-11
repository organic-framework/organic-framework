# Mission of Organic Framework

Status: **Agreed** with the founder, 2026-11-03. The wording will be refined as the framework evolves.

## Mission

Most value in the world is created by building and operating systems. Organic Framework enables people to build and run successful systems together with AI agents and, where needed, other people.

Organic Framework does not build the target system itself. It organizes the **creating organization**: the organization that creates and operates the target system.

## Problems Organic Framework solves

The founder stated these on 2026-11-03, from his experience of running his own company with agents.

1. **Systematic design of the creating organization.**
   - Today, positions grow lane by lane, incident by incident.
   - With a deliberate structure of positions derived from the business case and the target system, agents would reach a successful system much sooner.
   - This is a hypothesis to test on the first user organization, not yet evidence.
2. **Bringing colleagues into the practice.**
   - The way of working with agents that one person has built does not transfer to colleagues by itself.
   - Colleagues without it work at a very different productivity.
   - The framework must make the practice teachable and adoptable by other people, not only by its author.
3. **Replaceability and growth.**
   - Humans and agents can replace each other in positions.
   - A person's area of responsibility grows over time through reorganization, as people build more and more agent pipelines.

## The value cycle

The path from mission to result is a cycle, not a line:

1. **Stakeholders have interests**, expressed or latent.
2. Those interests **justify the company's mission and business case**.
3. The mission and business case **shape the creating organization**.
4. The organization **creates and operates the target system**.
5. The target system **serves the stakeholders**. They pay for the result, and serving their interests is the point of the work. Their interests change as a result.
6. The organization **observes the stakeholders and the target system and changes its own structure** in response. Reorganization is a routine operation, not a yearly project.

FPF's strategy mantra closes the same loop (STR.18): "Strategic question → worthwhile contribution → explained way → feasible contributions and means → justified choice and commitment → realization, use and renewed questions."

## The organization Organic Framework builds

- **AI-first.** By default an agent may hold a position. A human holds a position where a human is actually needed.
- **Positions with substance.** Every position has explicit permissions, a Workplace and a full trace of work.
- **Always current.** The structure keeps refactoring itself to stay current with the world. Large companies of the traditional kind find reorganization slow and painful; this organization must not.
- **Fully digitalized** (founder, 2026-11-03). Everything that belongs to the company's activity is recorded, on every machine, including colleagues' machines:
  - everything their models do, that is all trajectories;
  - the code and other artifacts people create.
  What does not belong to the company's activity is not recorded.
- **Experience becomes AI's competence** (founder, 2026-11-05). Experience accumulates inside the company as it works. It must be digitized as competence and become an asset of AI: methods, rules with their origins, briefs, position histories and packages recovered from the trace. This competence grows from stage to stage and is the organization's capital ([`business-case.md`](business-case.md)).
- **Robust to change.** It survives changes of people, models and tools, and it can be restructured without collapsing.

## The human's role

Founder, 2026-11-03. The key role of a human in an Organic Framework organization is to **own the nested structure of units and positions**:
- design it as a system;
- watch how it works and tune it;
- take care of it and keep it in order;
- make sure the value chain works as it must, generating value for the stakeholders and for the shareholders.

Two more parts of the same role (founder, 2026-11-03):
- **Watch and steer.** Watch how everything works, including the traces and trajectories. From time to time, steer units in a more effective direction, especially project units, or point out problems.
- **Allocate capital and set the risk appetite.** The higher a person's area of responsibility, the more of the questions are about allocation: capital, hardware, money for advertising, token budgets. Equally, the person decides what level of risk the organization accepts.
  - Allocation itself can be automated. In proprietary-trading firms, much of the allocation of capital and risk already is.
  - Top-level decisions still come from people, because people ultimately provide the capital and answer for the risk.
- **Answer for the result.** This is the part that stays human: a person is accountable for something. Agents may perform the work and decide within their envelopes; humans answer for the outcome. (FPF separates performance, authority and responsibility; see HRM.Reference:4.3 in the methodology.)

**Change the structure with great care** (founder, 2026-11-03). Splitting, merging and closing positions must not be casual operations.

**The founder's situation today.** He watches the organization through Zellij: three tabs with many panes. Switching between them is inconvenient, yet he still needs to see everything.

**Our reading (not yet confirmed).**
- The human's work shifts from doing and approving individual steps toward designing and maintaining the structure. In the eight-day retrospective, much of the founder's attention went to approvals, routing, status polls and plumbing. The friction report puts plumbing alone at about 1 in 6 of his messages.
- A share of the human's time spent on structure and value, rather than on plumbing, is a candidate measure for goal 1.
- Agents may hold analyst and coordinator positions inside a tree. Ownership of the tree stays with its human, which is terminal oversight.
- As agents take over more processes, each human owns a larger tree.
- The platform's main surfaces follow from this role:
  - the lead's view of state;
  - a view of the value chain;
  - structure editing: spawning, splitting, merging, closing and installing from Bench;
  - backtesting a reorganization before making it.

## Three levels

target system ← creating organization ← Organic Framework

The team that builds Organic Framework is itself a creating organization and runs on Organic Framework (dogfooding). That makes dogfooding a test of the mission, not an optional practice.

## Goals of the framework itself

Agreed with the founder on 2026-11-03.

1. **Adoption at scale.** Companies from small to large use Organic Framework, and at least one million creating organizations are built with it.
   - Under AI-first, a "small company" may be one founder and a dozen agents. Organic Framework must therefore be light to start with.
2. **Teaching AI to build systems.** Through Organic Framework, AI labs can train models with reinforcement learning to build successful systems, not only to write code. The founder sees this as the next step in the development of AI models. Consequences for the design:
   - The full trace of work is training data, not only an audit record. Each Work record must link to outcomes: did the target system succeed for its stakeholders? That link is the reward signal.
   - Record formats are open and standard.
   - Data rules are part of the core from day one: what a company owns, what may be used for training, on what terms, and with what share for the company.
   - The organization simulator becomes a reinforcement-learning environment.
   - The success of a system shows months later. Models therefore need intermediate, verifiable signals, such as the observation of success per position and per result (C.39).
3. **Humanity's hardest problems.** The founder will list 10–20 major problems of humanity that he wants solved, by anyone. Organic Framework should take part in solving some of them. The list lives in a separate document. It gives direction; it is not a set of requirements.

## Still open

- ~~How we observe the success of each goal.~~ Drafted in [`success.md`](success.md), 2026-11-03, pending founder review.
- What the framework consists of.
- How we build it.
