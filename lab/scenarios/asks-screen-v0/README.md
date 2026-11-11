# asks-screen-v0: the owner's questions screen, driven through a real herdr client

Built on 2026-11-12 by `of_herdr` for layout A of the questions screen (of_asks's `bin/of_asks_inbox.py`, the design
the founder chose with of_conductor). It checks the screen as herdr carries it: keys and the mouse wheel go in as a
terminal sends them, through herdr's own client and server, and the pane is read back with `herdr pane read`.

## Pieces

- `fixture.py OUT`: two lab organizations and 60 open cards (8 blocking, 6 money or production, 5 tasks, 41 others),
  numbered `Q01`..`Q60`, each with its own words (of asks merges questions whose words overlap by 60%).
- `addcard.py N [AGE_S [WORDS]]`: one more blocking card while the screen is open; an age older than the selected
  card sorts it above the cursor; WORDS makes it longer than the screen.
- `hclient.py SESSION COLS ROWS SCRIPT`: an outer terminal for a lab herdr client, answering herdr's start-up
  queries as Ghostty 1.4.0 does; the script sends keys and wheel notches and saves pane reads.
- `*.script`: the steps below.

The lab herdr is isolated: `XDG_CONFIG_HOME` and `XDG_STATE_HOME` under `~/.cache/of-herdr/pl` (its own plugins,
sessions, socket and `config.toml` with `onboarding = false`, else the first-run screen takes every key), and
`OF_CONFIG_DIR`, `OF_STATE` point at the fixture. Answers in the screen are real, so they land in the fixture store.

## Results (herdr 0.9.5, build 4777cbd, 2026-11-12)

At 160x45:
- ↑ on the first card wraps to the last, ↓ comes back; `j` five times reaches the sixth; `G` and `g` go last and
  first.
- Tab moves to "Money and production"; Shift+Tab comes back (`KEY_BTAB` arrives through herdr).
- `?` opens the help; Esc closes it.
- The wheel over the queue moves the selection by one card. Over a card longer than the screen, three notches down
  scroll it and one notch up scrolls back; the selection stays.
- A card that arrives keeps the selection on the same card. **Finding:** a card that sorts above the cursor moves
  the selected row down one screen line (line 8 to 9) when the whole queue fits on the screen.
- A money card: `1` asks for a second press ("press 1 again (or y)"); another key says "cancelled" and records
  nothing. A production card is answered with one press, as designed (the confirmation is for money and
  irreversible only).
- The trial popup key (`prefix+shift+a`, 90% x 85%) opens the build; Esc closes it.

At 96x40 (a 77-column pane): layout B, one card per screen with "‹ 1 / 11 ›"; → pages on; ← from the first wraps to
the last. **Finding:** the streams line is cut in the middle of a label (the fourth stream shows only its dot), and
the keys line loses Tab and `?`.

## Rerun on build 5f83ad0 (2026-11-12)

From a fresh fixture, every step above again, plus `money3.script`:
- Both findings are fixed. A card that sorts above the cursor leaves the selected row on its screen line (line 8),
  and the divider shows ▲ at its top. At 77 columns the other streams show as dot and count
  (`● Holds work 11   ● 6   ● 5   ● 41`), and the keys line keeps Tab, `?` and Esc.
- Unchanged and passing: the arrows wrap; Tab and Shift+Tab; `g`, `G`, `?` and Esc; the wheel over the queue and over
  a long card; layout B paging and wrap; the trial popup opens and Esc closes it; the money card asks for a second
  press, and another key cancels with nothing recorded.
