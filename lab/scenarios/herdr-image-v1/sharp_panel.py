"""A working sketch for the founder's yes: the conductor panel as a picture that is sharp and scrolls in a herdr pane.

What herdr-image-v1 found, applied:
- the page is rendered at the pane's own device pixels (the cell size the terminal reports by CSI 16 t), so
  Ghostty shows it 1:1 and never scales it; of_platform's 1x picture was scaled up about 2x on a Retina screen;
- every image stays at most 16 MiB as RGBA, the cap of herdr 0.9.5's file path (native_graphics.rs MAX_FILE):
  a taller page is cut into tiles, each sent once; above the cap herdr falls back to ~22 MB of base64 per show;
- scrolling re-places the tiles with a source offset (kitty a=p with y= and h=) and sends no pixels again;
- the page is rendered again only when what it says changes, never because minutes pass.

Content comes from bin/of_conductor_panel.py unchanged (of_conductor's words, of_platform's page).

    python3 sharp_panel.py [--every 30]

Keys: wheel, arrows, j/k, PgUp/PgDn, g/G; q quits.
"""
import fcntl
import hashlib
import os
import re
import select
import shutil
import struct
import subprocess
import sys
import termios
import time
import tty
from base64 import standard_b64encode

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "bin")
sys.path.insert(0, os.path.abspath(BIN))
import of_conductor_panel as P  # noqa: E402
import of_events  # noqa: E402
import of_store  # noqa: E402

MAX_RGBA = 16 * 1024 * 1024
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
WORK = os.path.join(of_events.STATE_DIR, "herdr", "sharp-panel")
BASE_ID = 7100
out = sys.stdout


def w(s):
    out.write(s)
    out.flush()


def cell_px(fd):
    """(cell width, cell height) in device pixels, from CSI 16 t; the window ioctl as a fallback."""
    w("\033[16t")
    buf, end = "", time.time() + 1.0
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            buf += os.read(fd, 64).decode("ascii", "ignore")
            m = re.search(r"\033\[6;(\d+);(\d+)t", buf)
            if m:
                return int(m.group(2)), int(m.group(1))
    rows, cols, xp, yp = struct.unpack("HHHH", fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8))
    return (xp // cols if xp and cols else 18), (yp // rows if yp and rows else 36)


def chrome(args, timeout=60):
    return subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                           f"--user-data-dir={os.path.join(WORK, 'chrome')}", *args],
                          capture_output=True, text=True, timeout=timeout)


def page_html(ledger, conn, positions, cache, width_css):
    html = P.render_html(ledger, conn, positions=positions, cache=cache, width=width_css, height=0)
    return html.replace("height:0px;overflow:hidden;", "overflow:hidden;")


def page_height(html):
    """The page's height in CSS pixels, read back from Chrome's DOM."""
    src = os.path.join(WORK, "measure.html")
    probe = ("<script>addEventListener('load',()=>document.body.setAttribute('data-h',"
             "document.documentElement.scrollHeight))</script>")
    with open(src, "w") as fh:
        fh.write(html.replace("</body>", probe + "</body>"))
    r = chrome(["--virtual-time-budget=2000", "--dump-dom", f"file://{src}"])
    m = re.search(r'data-h="(\d+)"', r.stdout or "")
    return int(m.group(1)) if m else 1200


def render_tiles(html, width_css, dpr, tile_css):
    """PNG tiles of tile_css CSS pixels each, from the top of the page down; [(path, top_css)]."""
    total = page_height(html)
    tiles, top = [], 0
    while top < total:
        h = min(tile_css, total - top)
        src = os.path.join(WORK, f"tile-{len(tiles)}.html")
        png = os.path.join(WORK, f"tile-{len(tiles)}.png")
        with open(src, "w") as fh:
            fh.write(html.replace("</style>", f".wrap{{margin-top:-{top}px}}</style>", 1))
        if os.path.exists(png):
            os.unlink(png)
        chrome([f"--force-device-scale-factor={dpr}", f"--window-size={width_css},{h}",
                "--virtual-time-budget=2000", f"--screenshot={png}", f"file://{src}"])
        if not os.path.exists(png):
            break
        tiles.append((png, top))
        top += h
    return tiles, total


def send_png(path, image_id):
    data = standard_b64encode(open(path, "rb").read()).decode()
    chunks = [data[i:i + 4096] for i in range(0, len(data), 4096)] or [""]
    parts = []
    for k, ch in enumerate(chunks):
        more = 1 if k < len(chunks) - 1 else 0
        head = f"a=t,f=100,t=d,i={image_id},q=2,m={more}" if k == 0 else f"m={more},q=2"
        parts.append(f"\033_G{head};{ch}\033\\")
    w("".join(parts))


def place(tiles, offset_px, view_rows, cols, cell_h, dpr):
    """Show the page from offset_px (device pixels) down over view_rows rows: each tile's visible slice, by its
    source offset, with no pixels sent."""
    view_px = view_rows * cell_h
    seq = []
    for n, (_, top_css, h_css) in enumerate(tiles):
        top, bottom = top_css * dpr, (top_css + h_css) * dpr
        a, b = max(top, offset_px), min(bottom, offset_px + view_px)
        image_id = BASE_ID + n
        if b <= a:
            seq.append(f"\033_Ga=d,d=i,i={image_id},q=2\033\\")
            continue
        row = (a - offset_px) // cell_h
        rows_n = max(1, (b - a) // cell_h)
        seq.append(f"\033[{row + 1};1H\033_Ga=p,i={image_id},p=1,x=0,y={a - top},"
                   f"h={rows_n * cell_h},c={cols},r={rows_n},C=1,q=2\033\\")
    w("".join(seq))


def main():
    every = int(sys.argv[sys.argv.index("--every") + 1]) if "--every" in sys.argv else 30
    os.makedirs(WORK, exist_ok=True)
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    w("\033[?1049h\033[?25l\033[?1000h\033[?1006h\033[H\033[2J")
    positions, loaded, cache = [], 0, P.load_cache()
    tiles, total_px, shown, offset_rows, fetched, geom, placed = [], 0, None, 0, 0, None, None
    try:
        while True:
            cols, rows = shutil.get_terminal_size((100, 40))
            cw, ch = cell_px(fd) if geom is None or geom[:2] != (cols, rows) else geom[2:]
            dpr = 2 if ch >= 28 else 1
            view_rows = rows - 1
            if time.time() - fetched >= every or geom is None or geom[:2] != (cols, rows):
                if time.time() - loaded > 600:
                    try:
                        positions, loaded = P._positions(), time.time()
                    except Exception:  # noqa: BLE001
                        loaded = time.time()
                conn = of_store.connect()
                try:
                    ledger = P.load_ledger()
                    cache = P.load_cache()  # read only: the live panel asks the model and fills it
                    words = P.render(ledger, conn, positions=positions, cache=cache, color=False)
                    width_css = cols * cw // dpr
                    html = page_html(ledger, conn, positions, cache, width_css)
                finally:
                    conn.close()
                steady = "\n".join(x for x in words.split("\n")[1:] if "движение" not in x)
                key = hashlib.sha1(f"{steady}{cols}x{rows}x{cw}x{ch}".encode()).hexdigest()
                fetched, geom = time.time(), (cols, rows, cw, ch)
                if key != shown:
                    w(f"\033[{rows};1H\033[2K\033[38;2;142;142;147m  обновляю…\033[0m")
                    # a tile is a whole number of rows and at most MAX_RGBA as RGBA
                    max_rows = MAX_RGBA // (cols * cw * 4 * ch)
                    tile_css = max(1, max_rows) * ch // dpr
                    pngs, total_css = render_tiles(html, width_css, dpr, tile_css)
                    w("\033_Ga=d,d=A,q=2\033\\\033[H\033[2J")
                    tiles = []
                    for n, (png, top_css) in enumerate(pngs):
                        send_png(png, BASE_ID + n)
                        nxt = pngs[n + 1][1] if n + 1 < len(pngs) else total_css
                        tiles.append((png, top_css, nxt - top_css))
                    total_px = total_css * dpr
                    shown = key
                    placed = None
            max_off = max(0, -(-total_px // ch) - view_rows)
            offset_rows = max(0, min(offset_rows, max_off))
            frame = (offset_rows, cols, rows, shown)
            if tiles and frame != placed:
                place(tiles, offset_rows * ch, view_rows, cols, ch, dpr)
                hint = (f"  колесо · ↑↓ · PgUp/PgDn   строки {offset_rows + 1}–{min(offset_rows + view_rows, max_off + view_rows)}"
                        f" из {max_off + view_rows}   ·   набросок of_herdr: картинка в пикселях панели, {len(tiles)} част."
                        if max_off else "  набросок of_herdr: картинка в пикселях панели")
                w(f"\033[{rows};1H\033[2K\033[38;2;99;99;102m{hint[:cols - 1]}\033[0m")
                placed = frame
            r, _, _ = select.select([fd], [], [], 1.0)
            if not r:
                continue
            data = os.read(fd, 256).decode("utf-8", "ignore")
            if "q" in data:
                return 0
            step = 0
            step -= 3 * data.count("\033[<64;") + data.count("\033[A") + data.count("k")
            step += 3 * data.count("\033[<65;") + data.count("\033[B") + data.count("j")
            step -= view_rows * data.count("\033[5~")
            step += view_rows * data.count("\033[6~")
            if "g" in data:
                offset_rows, step = 0, 0
            if "G" in data:
                offset_rows, step = max_off, 0
            offset_rows += step
    finally:
        w("\033_Ga=d,d=A,q=2\033\\\033[?1000l\033[?1006l\033[?25h\033[?1049l")
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


if __name__ == "__main__":
    sys.exit(main())
