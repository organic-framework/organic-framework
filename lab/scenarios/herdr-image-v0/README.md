# herdr-image-v0: does a pane image reach the outer terminal?

**Superseded by [`herdr-image-v1`](../herdr-image-v1/README.md) (2026-11-11).** This stand-in never answered the SGR pixel mouse query, so herdr never offered it the file path. Its conclusion, "herdr re-sends a pane's image as raw RGBA", holds only for the inline path and for images over 16 MiB.

A check that does not rely on anyone's eyes. It was built on 2026-11-11 after the conductor panel's picture
showed nothing in the founder's pane.

- `kprobe.py`: asks the terminal it runs in for kitty graphics support, with a direct placement and with a
  virtual one (`a=q`), and prints the answers. Inside a herdr pane both answer `OK`: herdr's emulator takes
  images.
- `kshow.py direct|virtual`: shows a 4x4 PNG in its pane, either directly or by Unicode placeholders.
- `kclient.py OUT SECONDS`: attaches a herdr client to the `oflab` session inside a pseudo-terminal. The
  pseudo-terminal plays a Ghostty outer terminal: `TERM_PROGRAM=ghostty`, and it answers the client's queries. It
  records every byte the client draws and counts the kitty transmit (`a=t`) and placement (`a=p`) commands.

Run it in a separate herdr session, never in the founder's. Close any workspace that shows something else, so
the image pane is the one on screen:

    env -u HERDR_ENV -u HERDR_PANE_ID -u HERDR_SOCKET_PATH herdr --session oflab server &
    herdr --session oflab pane run <pane> "python3 kshow.py direct"
    python3 kclient.py out.bin 10

Findings on 2026-11-11 (herdr 0.9.5):

| Image | Transmit | Placement |
|---|---|---|
| 4x4 PNG, direct | yes | yes |
| 4x4 PNG, virtual | yes | yes |
| the panel at 2392x1764 (a 2x render) | about 10 MB still streaming after 12 s | none |
| the panel at 960x706 | 3.6 MB | yes |

herdr re-sends a pane's image to the outer terminal as raw RGBA: 4 bytes a pixel, base64-encoded. The 2x render
came to about 17 MB a show and never got placed. The fault was the panel's image size, not herdr or Ghostty.
