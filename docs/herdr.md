# herdr in Organic Framework

Owner: `of_herdr`. Status: first version, 2026-11-11. So far it covers the version OF runs on, what herdr can
draw, and the upstream issues OF depends on. The adapter, the full inventory of OF's calls and the contract tests
come next (brief, Mandate 1, items 2 and 3).

OF runs on [herdr](https://herdr.dev) (`herdrdev/herdr`, Apache-2.0), adopted on 2026-11-05. herdr is a terminal
workspace manager made for coding agents, and it supports many agent CLIs, not only Claude Code. It tracks each
agent's state (working, blocked, done, idle), lets agents be addressed by name, and exposes a CLI and a socket API
that agents and plugins drive. It knows terminals, not systems, roles or mandates. OF adds those on top: the
registry, pane metadata, the asks queue and the event log. So OF stays a layer over herdr, not a multiplexer of its
own, and herdr is its first runtime adapter. The full analysis is a private report of the team that builds OF.

## The version

- **herdr 0.9.5, stable channel** (`herdr channel show`), installed by Homebrew. The server and the founder's
  client run the same binary.
- herdr embeds Ghostty's terminal core, libghostty-vt `1.3.2-HEAD+44f2a44df`
  (`vendor/libghostty-vt.vendor.json`). Every pane is emulated by that copy, not by the outer terminal.
- **The founder's terminal is Ghostty 1.4.0.**
- The source OF reads is a shallow clone of the release tag in `~/.cache/of-herdr/herdr-v0.9.5`. Nothing is
  built from it on the Mac.

Upgrades go through `of_herdr`: the contract tests run on the new release first, on both the stable and preview
channels (to come).

## What can be drawn in a pane or a popup

A pane or a popup is a terminal. herdr has no HTML or web view in v0.9.5: plugin panes are terminal panes too.
That leaves two ways to draw:

1. **Text, drawn by Ghostty at its own resolution.** It is always sharp, scrolls, can be selected, and
   `herdr pane read` can read it. Panes get `COLORTERM=truecolor`.
2. **Pictures by the kitty graphics protocol.** The pane's emulator takes the image, and herdr's client re-sends
   it to the outer terminal in one of two ways:

| Path | When herdr takes it | What crosses the terminal |
|---|---|---|
| File ("direct") | Local client; `TERM_PROGRAM` ghostty, kitty or WezTerm; no SSH, tmux or screen; the terminal reports cell pixels (`CSI 16 t`) and answers mode 1016; the image is RGBA of **at most 16 MiB** | a path; the server's file under `/var/tmp/herdr-pane-graphics-<uid>/` |
| Inline | anything else, including every image over 16 MiB | the decoded RGBA as base64, uncompressed: about 5.4 bytes a pixel |

Rules for OF's screens drawn as pictures (`lab/scenarios/herdr-image-v1`):
- **Render at the pane's device pixels.** The cell size comes from `CSI 16 t`. A picture smaller than its cells
  is scaled up by Ghostty and looks soft.
- **Keep every image at most 16 MiB as RGBA**, for example 2560x1600. Cut a taller page into tiles of whole rows.
- **Scroll by placement, never by re-sending.** Re-place each tile with a source offset (`a=p` with `y=` and
  `h=`). Send pixels only when the content changes.
- **Never redraw because time passes.**
- herdr also drops any one graphics transaction over 30 MiB of encoded data (`MAX_GRAPHICS_FRAME_SIZE` minus
  `MAX_FRAME_SIZE`; herdr#3946), and a pane's emulator keeps at most 64 MiB of images.

The requests screen's picture failed on these rules (2026-11-10, conductor request R7):
- the first render was 0.6% over 16 MiB, so herdr sent 22.6 MB inline on every redraw;
- the second render was cut to 1x, and Ghostty scaled it up, so it looked soft;
- the panel did not handle scrolling.

Neither herdr nor Ghostty had a bug that blocked it. The full account is in the lab's README.

## Upstream issues OF depends on

| Issue | State | What it does to OF | Workaround in OF |
|---|---|---|---|
| [herdr#5656](https://github.com/herdrdev/herdr/issues/5656): after one placement change while a file transfer is in flight, every later image goes inline until the client reattaches (macOS, Ghostty) | open, p2 | A scrolling picture screen could switch the founder's client to inline images, so every pane lags | none yet; any picture screen is built with this risk named |
| [herdr#5824](https://github.com/herdrdev/herdr/issues/5824): keep PNG or compress (`o=z`) inline images | closed, not planned | Inline images stay about 5.4 bytes a pixel | keep images inside the file path's 16 MiB |
| [herdr#3946](https://github.com/herdrdev/herdr/issues/3946): images over the 32 MiB frame cap are dropped; some small ones are not painted | open, p2 | a large picture can vanish without an error | keep images inside 16 MiB |
| [herdr#5783](https://github.com/herdrdev/herdr/issues/5783): redrawn images keep GPU memory in herdr-app until restart | open, p1, Linux | not hit on macOS so far; redraw rarely anyway | redraw on content change only |
| [herdr#5594](https://github.com/herdrdev/herdr/issues/5594): plugin panes do not answer `CSI 16 t` and report 0x0 pixels | open | a plugin pane cannot size a sharp picture | draw pictures in normal panes |
| **not filed yet**: herdr's embedded libghostty-vt rejects a kitty temporary file (`t=t`) from `/tmp` on macOS (`EINVAL: temporary file not in temp dir`). Ghostty fixed it in `cd432ba5cb` (2026-10-12), and herdr 0.9.5 vendors an older copy. | candidate | none today: OF sends no temporary files. It breaks pane apps that do. | none needed |

Anything filed upstream needs the founder's yes first (brief, Limits).
