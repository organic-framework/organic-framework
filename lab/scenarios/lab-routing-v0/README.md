# lab-routing-v0 — the relay regression suite

The relay types into every agent's pane, and on 2026-11-06–07 it broke four ways. This scenario pins one
case per fault so no later relay change can bring any of them back (of_architect, 2026-11-07). Owner: `of_lab`.

| # | Fault | Lives in | Layer that guards it |
|---|---|---|---|
| 1 | An `@` before a path is read by the receiver as a file mention and the file is pulled into the session (a Grafana token leaked, 2026-11-06). | `of_relay_client.neutralize_mentions` (`0719c7e`): every `@` not preceded by a word character → U+FF20. | unit (now) + real-CC VM (pending auth) |
| 2 | A tool's text delivered without a frame reads as the owner's own words. | `of_relay_client.frame`: prepend "Message from … (via …, authority: none)" unless already framed. | unit (now) |
| 3 | Typing into a non-empty input box appends to a blocked prompt and re-submits it (the block loop: 22 re-denies over ~10 h). | `of_relay_client.input_box_text` + `herdr_deliver` box check (`9858684`): a box with text defers the delivery; a dim suggestion counts as empty. | unit (now) + real-CC VM (pending auth) |
| 4 | A scheduled or relayed prompt recorded as the founder's own words (closed an ask, 2026-11-06). | `of_hook.prompt_origin` / `transcript_origin` / `herdr_deliver(human_channel=…)`. | unit (now) |

## Two layers

- **Unit layer (`tests/test_relay_regression.py`, runs today):** each fault guarded at the pure function the
  fix lives in. `python3 -m unittest tests.test_relay_regression`. No VM, no Claude login. 14 cases, green.
- **End-to-end layer (lab VM, pending the Lab's Claude login, `of asks e20d7876`):** faults 1 and 3 only
  reproduce against **real Claude Code** — the stub harness has no `@`-expansion and no input box. Those two,
  plus `of spawn`'s trust-dialog path (the next backlog item), need an authenticated Claude Code session in the
  VM driven over a pty / a herdr pane. Until then the unit guards above hold, and `of_lab` has pane-based
  canary/defer checks (verified 2026-11-07 in its own pane) as the interim end-to-end evidence.

## Status

- Unit layer: done (`tests/test_relay_regression.py`).
- End-to-end layer: blocked on VM Claude auth; scaffolding to follow.
