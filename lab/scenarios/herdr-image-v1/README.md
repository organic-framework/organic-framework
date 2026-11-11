# herdr-image-v1: which way a pane image reaches Ghostty, and why the conductor panel's picture failed

Built on 2026-11-11 by `of_herdr` for the founder's question about the requests screen (conductor request R7,
20:30Z): "Это ошибка в herdr или в Ghostty?" It replaces the conclusion of `herdr-image-v0`.

## Why v0's conclusion was wrong

v0's stand-in terminal (`kclient.py`) answered only kitty `a=q` queries. herdr 0.9.5 has two ways to send a pane
image to the outer terminal, and v0 could only see the slow one:

1. **The file path ("direct").** The herdr server writes the decoded RGBA to a file under
   `/var/tmp/herdr-pane-graphics-<uid>/` and the client sends Ghostty `a=t,f=32,t=f;<path>`. Only a few hundred
   bytes cross the terminal. herdr takes this path only when:
   - the client is local, with `TERM_PROGRAM=ghostty` (or kitty or WezTerm), and no SSH, tmux or screen;
   - the terminal reports exact cell pixels (`CSI 16 t`) and answers the SGR pixel mouse query (`CSI ? 1016 $ p`)
     (`src/client/handshake.rs`, `src/server/headless.rs` `client_supports_direct_graphics`);
   - the image is RGBA of at most **16 MiB** (`src/server/headless/native_graphics.rs`, `MAX_FILE`).
2. **Inline.** Otherwise the client sends the decoded RGBA as base64 through the terminal (`t=d,f=32`, 3072-byte
   chunks), never the source PNG and never compressed. That is about 5.4 bytes per pixel.

v0's stand-in never answered mode 1016, so herdr never offered it the file path, and v0 measured only the inline
path. It concluded that "herdr re-sends a pane's image as raw RGBA". The panel was then cut to 1x, which is why the
founder saw it soft.

## What v1 measures

- `kterm.py`: a stand-in for Ghostty 1.4.0 as the outer terminal. It answers every kitty transfer as Ghostty does,
  including `t=f` and `t=t` files with Ghostty 1.4.0's temp-directory rule. It also answers the start-up queries,
  mode 1016 among them (`KTERM_PIXEL_MOUSE=0` turns that answer off). It records every transfer and placement
  with its time.
- `kpane.py W H`: shows a W x H PNG in its pane the way `bin/of_conductor_panel.py` sent its picture.
- `ktemp.py`: asks the terminal it runs in to take a kitty temporary file from `/tmp` (macOS: `/private/tmp`).
- `sharp_panel.py`: the working sketch for the founder (below).
- `lab.sh`: herdr against the lab session only, with the pane variables dropped.

Run it in the lab session, never the founder's:

    ./lab.sh server &                       # herdr --session oflab server
    ./lab.sh pane run w2:p1 "python3 $PWD/kpane.py 1800 1440 100 40"
    python3 kterm.py oflab runs/out.json 20 # 160x50 cells of 18x36 px, like a Retina Ghostty
    ./lab.sh server stop

## Results (herdr 0.9.5, 2026-11-11)

| Image in the pane | RGBA size | How herdr sent it | Bytes through the terminal | Placed after |
|---|---|---|---|---|
| 960x706 (the 1x panel), stand-in without mode 1016 | 2.7 MB | inline | 3.6 MB | 3.8 s |
| 960x706, stand-in with mode 1016 | 2.7 MB | file | 26 KB | 0.3 s |
| 1800x1440 (2x of 100x40 cells) | 10.4 MB | file | 26 KB | 0.2 s |
| 2392x1764 (the panel's first, 2x render) | 16.1 MiB, 0.6% over the cap | inline | 22.6 MB | 4.5 s |

The same 22.6 MB went again on every redraw. Before 2026-11-11 00:47 +04 the panel redrew whenever a minute
passed (fixed in `d8ec081`).

The founder's own herdr server (pid 1495) created its file-export directory at 00:43 +04, when the 1x picture went
in. During the 2x picture's run (from 20:08Z) it never did. In his setup the file path works, and the 2x picture
never used it.

`ktemp.py` in a herdr pane got the answer `EINVAL: temporary file not in temp dir`: herdr 0.9.5's embedded
libghostty-vt (`1.3.2-HEAD+44f2a44df`) predates Ghostty's fix `cd432ba5cb` (2026-10-12, "accept /private/tmp for
temporary files on macOS"). The founder's Ghostty 1.4.0 has that fix, and the conductor panel never sent
temporary files, so this did not cause the failure. It is a separate herdr bug (docs/herdr.md).

## The cause, layer by layer

- **OF's drawing (the fault).**
  - The first picture was 0.6% over herdr's 16 MiB file-path cap, so herdr sent 22.6 MB per show.
  - The second was cut to 1x to fit, and Ghostty scaled it up about 2x on a Retina screen: soft.
  - A picture scrolls only if the panel handles the wheel and moves it, and the panel did not.
- **herdr (weak spots, no bug that blocked this).**
  - Above 16 MiB it falls back to inline uncompressed RGBA without a word at the default log level.
  - Upstream declined compressing that inline path (herdr#5824, closed as not planned on 2026-11-05).
  - After one failed or late file transfer it sends every later image inline until the client reattaches
    (herdr#5656, open, p2, macOS and Ghostty).
- **Ghostty: no fault found.** 1.4.0 reads herdr's files and temporary files.

## The working sketch

`sharp_panel.py` draws the conductor panel's page (`render_html`, unchanged content) as a picture that is sharp and
scrolls:
- it renders at the pane's device pixels from `CSI 16 t`, so there is no scaling;
- it cuts the page into tiles of whole rows, each at most 16 MiB as RGBA, so every tile takes the file path;
- a scroll re-places the tiles with a source offset (`a=p,y=,h=`), so no pixels are sent again;
- it renders again only when the words change.

In the lab: one 2412x1728 tile went by file and was placed in 0.26 s. A one-row scroll sent only placements, plus
the next tile once by file when it came into view. The whole session sent 27 KB through the terminal.

It ran in the founder's session in a tab of its own ("набросок панели", made with `--no-focus`). There his pane
gave 5408x748-pixel tiles (16.2 MB each).

Limits of the sketch:
- each content change costs about 10–20 s of headless Chrome, cold-started per tile;
- the text is not readable by `herdr pane read`;
- herdr#5656 applies: a scroll that changes placements while a tile is in flight could push every later image
  inline until the client reattaches.

## Decision

The founder paused the requests screen's redesign on 2026-11-11 (ask `08936933`). Before that he asked whether herdr has React or Next.js plugins (ask `d0099cfe`). It has none that draw web pages: plugin panes are terminal panes, and non-terminal plugin UI is not part of plugin v1 (herdr docs, plugins.mdx). React can draw in a terminal through Ink or OpenTUI, as text. The sketch was stopped and its tab closed. The panel stays as text cards (`49394a5`).
