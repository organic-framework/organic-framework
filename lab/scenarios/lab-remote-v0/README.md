# lab-remote-v0

The lab proof of `docs/remote.md` §3 row 7: a person who reaches Organic Framework **only through the conductor's
session** can still run the organization. Hand-over `7a39af30` from `of_architect` (verifier). The features it exercises
are rows 1-5 of `docs/remote.md` §3, owned by `of_asks` (`f4d4b89f`), `of_platform` with `of_server` (`c3a23fae`),
`of_herdr` (`4e17fb58`) and `of_onboarding` (`bb3bc0dd`); this scenario is the harness and the proof, not the features.

## The setup (a clean VM, no herdr, no popup)

One disposable lab VM stands in for the person's machine:

- `~/.config/of/owner.yaml` has `surface: remote` (so the queue reaches the person through the conductor, not a herdr
  notification or the popup).
- The organization is created remote: `of init <org> --surface remote` - every agent starts in tmux and the conductor
  with Remote Control on (`docs/remote.md` §2).
- Only the conductor runs as a driven session; the other agents run in tmux panes (`bin/of_tmux.py`, owner `of_herdr`),
  started from the lines `of init` prints. No herdr server, no popup process.
- The person acts only by prompts recorded in the conductor's session (`of.human_prompt`, origin human) - the hook
  records them; `of prompts --mine` lists them with their event ids. The conductor points to those words; it never
  states a decision of its own.

## Checks (docs/remote.md §3 row 7)

Each check is one leg of the row-7 "done when". The commands are those `docs/remote.md` names; the interfaces are all
present at main `3962757` ("Remote answers and tasks with a place") - the run confirms each end to end.

| # | Row | What the person does from the chat | How it is checked |
|---|-----|------------------------------------|-------------------|
| C1 | 1 | **A card answered.** `of asks answer <id> --option N\|--text "<words>" --prompt <event id>` | the card closes `by: owner`, `via: conductor`, carrying the prompt's id and words; the asker sees the answer. OF records it only when the prompt is an `of.human_prompt`, origin human, in the conductor's session, later than the card, naming the card or its option. |
| C2 | 2 | **A medium diff approved, a high one refused.** `of diff approve <id> --prompt <event id>`, the prompt quoting the diff id and saying yes | the medium-tier diff applies (`of.diff_approved`); the high-tier one is refused with its reason (a high diff and grants stay at the machine). |
| C3 | 4 | **A `machine` task shown as waiting.** `of ask --task "…" --where machine` | `of asks --chat` / `of asks --json` presents it as `waiting` (it waits until the person is at the machine), never as steps to type on a phone. |
| C4 | 5 | **A message delivered to a tmux agent.** a peer `of send`s the tmux agent | the relay delivers it into the agent's tmux pane (idle, empty input, declared injection, `bin/of_tmux.py`) with no one typing it. |
| C5 | 3 | **A stuck thread surfaced.** a thread of the person's is left stuck | with `surface: remote`, the pulse's `remote_notes` sends the conductor one message about it, which the conductor passes to the person within its next turn. |

### Negative cases (authority): the evidence must be the decision owner's own

C1 and C2 accept an answer or an approval only as the decision owner's own words, recorded in the session that holds
**their** conductor (`docs/remote.md` §5). Two forgeries must be **refused**:

- **C1n / C2n-a — a colleague's conductor.** A conductor that is not the decision owner's (another org's, or one that
  does not own the card or diff) giving `yes <id>` is refused: the prompt must be in the owner's own conductor session.
- **C1n / C2n-b — a forged session id.** A session that only sets `CLAUDE_CODE_SESSION_ID` to the conductor's id, without
  genuinely holding the position (`holder.session` + live presence), is refused: claiming the env var is not holding the
  session.

**Both pass today** (the answer/approval is wrongly accepted); `of_asks` is fixing them after of_architect's review of
`3962757`. So each negative check asserts a refusal and **fails until the fix lands**, then guards the regression.

## Run (on a clean lab VM)

```
OFLAB_MAX_VMS=1 lab/bin/oflab up remote1
#   as the lab user inside the VM:
bash setup.sh            # owner.yaml surface: remote; of init --surface remote; tmux agents; the conductor session
python3 check.py         # one line per check (C1..C5); exit 1 on any fail
```

Heavy work stays in the VM (`AGENTS.md`: nothing heavy on the Mac). The run is recorded in
`organic-framework-build reports/framework/`.

## Status

- Harness: setup.sh and check.py to build (this README is the spec, sent to `of_architect` for the scenario's shape).
- Features: rows 1, 3, 4, 5 and row 2's `--prompt` are present at main `3962757`; the checks are wired to them
  directly. Exact outputs are read from each owner's module (`of_asks_chat.py`, `of_diff.py`, `of_pulse.remote_notes`,
  `of_tmux.py`); questions about a specific flag or output go to that owner, not through `of_architect`.
- Known authority gaps (of_architect's review of `3962757`): a colleague's conductor and a session that only forges
  `CLAUDE_CODE_SESSION_ID` are both wrongly accepted today; `of_asks` is fixing them. The two negative cases above
  assert the refusal, so they fail until the fix and then guard it.
- Blocked only on a free lab VM for the run (Mac load over the lab ceiling); the harness is built meanwhile.
