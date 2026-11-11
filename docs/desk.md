# The desk: the founder's case on one screen

Owner: `of_platform`. Code: `bin/of_desk.py` (the router), `bin/of_case.py` (the case), `bin/of_listen.py` (when to infer).

## Why it changed (2026-11-09)

The first desk (2026-11-05) followed agent states. An agent off the desk that turned `blocked` or `done` came in, in place of the desk agent idle longest. With fifty agents this failed the founder:

- On 2026-11-09 the desk moved 27 times. Only one of the agents it brought in had answered him. Sixteen had finished a turn a peer's message started, and five a turn a background task started.
- It evicted the sessions he was working with. He asked `acme_throughput-owner` to arrange the desk (13:26Z). At 13:38Z the router put `sec_patch` back in place of `accounting_keeper`: he had removed that agent, but its open ask counted as `blocked`. At 13:47Z it took `acme_throughput-owner` itself, five minutes after it had answered him: "idle" meant only that he was still reading.
- It knew nothing of what he placed by hand. Only `of desk pin` protected a pane, and nobody used it.

The founder (2026-11-09): the desk should show the case he is working on, gathered by a small model that reads the chats.

## What the desk is

The desk is the founder's working table. It shows two things:

1. **The panes he put there.** He can put them himself (herdr), have an agent do it at his request, or use `of desk put`, or `of desk pin`. They stay until he removes them.
2. **The agents of his current case.** A case is the matter he works on now: an incident he investigates, a decision he prepares. Its agents are the ones he talks to about it, the ones he names, and the ones doing the work for them.

The desk is not an alert board. Questions reach him through the asks popup, and an agent stuck in a dialog gets a notification. A finished turn, by itself, is nobody's business.

## The case

`bin/of_case.py` infers the case from the event log:

1. **The digest.** It covers his own typed prompts of the last 90 minutes, each with the agent he typed it to and the start of that agent's answer. Popup answers are left out, since answering a card is not working on a case. It also covers the messages those agents exchanged with others: the text of every batch the relay typed in, and counts for the rest. Last come the roster (name and summary of every position) and the cases named last time.
2. **The model.** Haiku through `claude -p` reads the digest, the same way the asks judge does: no tools, no hooks, no session file, on the account with the most headroom. It names up to 3 cases, the current one first. A case is a whole investigation, not each question in it. Each case has a short Russian title and summary, and up to 8 members with a weight (1.0 for the agent he talks to, 0.7–0.9 for the core work, 0.4–0.6 for a supporting part) and a reason.
3. **The record.** `of.case_inferred` ([`events.md`](events.md)). Names not in the roster are dropped.

On the day's data, one call takes about 17 s and $0.005. It named the incident ("Всплеск #4: откуда деньги и почему мы их не взяли") with `acme_throughput-owner`, `accounting_keeper`, `storefront_checkout`, `storefront_search`, `storefront_timing`, `storefront_promo-triggers` and `storefront_operator`. It left out `forecasting_keeper`, whose traffic was routine. It also put a later conversation with `acme_org` first, once he had moved on to it.

**When it runs.** The listener starts `of desk case --infer` in the background:

- after he types into any agent's pane, once 30 s pass without another prompt, and at most every 2 minutes;
- every 10 minutes while his last prompt is less than 30 minutes old.

Without prompts in the window nothing runs, and the last case stays.

## The router

On every herdr event batch and every 30 s, the router fills the desk from the latest case. Each agent gets a score: its weight in the current case, its weight × 0.6 in the second case and × 0.4 in the third, whichever is highest.

1. **What it never moves**, on the desk or off it:
   - his placements and pins;
   - the focused pane;
   - an agent he typed to in the last 15 minutes (he may be typing there right now);
   - an agent the router itself placed less than 10 minutes ago;
   - the positions of pinned views (`of view`), which are in sight already.
2. **What it brings:** case members with a score of at least 0.3 that have a live pane, highest first. They fill free slots first (`slots`, default 6).
3. **What it replaces:** only an agent the router placed itself. The newcomer's score must beat that agent's by at least 0.2, so the desk does not flicker between near equals.
4. **What it does not bring back:** an agent the router placed and he removed. It stays off while the case keeps its title, and for at least 2 hours.
5. **Never:**
   - taking focus;
   - bringing in an agent because it finished a turn, went idle or has an open ask.
6. **Notifications:** an agent off the desk that waits in a herdr dialog gets one notification per 10 minutes. The old summary "N agents finished" is gone.

The desk tab is labelled with the current case's title.

A pane counts as his when it is on the desk and the router did not place it. The router keeps its own placements in `~/.local/state/of/desk.json` (`placed`), so a pane moved by hand needs no command. An agent the router placed that leaves the desk without the router has been removed by him (`removed`).

On its first run, the case router takes as its own only what the old router brought in during the last 2 hours. The rest of the desk is his: he placed it or kept it there. `platform_owner`, for example, was brought in on 2026-11-06 and kept for three days.

## Commands

- `of desk` shows the slots: each agent, its state, why it is there (his placement, a pin, or the case with its reason), the current case with its members, and the agents waiting in dialogs.
- `of desk case` shows the latest case. `of desk case --infer` infers it now and records it. `--dry-run` prints the result without recording it.
- `of desk put <name> [slot]` puts an agent on the desk as his own placement. `of desk pin|unpin <name>` sets a pin that survives removal.
- `of desk on|off|run|once`: the router, as before.

Settings: `~/.config/of/desk.yaml` (`slots`, `case_min`, `case_margin`, `touch_s`, `min_stay_s`, `case_decay`).
