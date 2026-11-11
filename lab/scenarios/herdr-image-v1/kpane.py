"""Show one PNG of a given pixel size in this pane, sent the way the conductor panel sends its picture
(bin/of_conductor_panel.py kitty_image: a=T, f=100, t=d, base64 in 4096-byte chunks, placed over c x r cells),
then wait. Prints KPANE-READY with the sizes so a check can read the pane.

    python3 kpane.py WIDTH HEIGHT [COLS ROWS]
"""
import base64
import struct
import sys
import time
import zlib

w, h = int(sys.argv[1]), int(sys.argv[2])
cols = int(sys.argv[3]) if len(sys.argv) > 3 else 100
rows = int(sys.argv[4]) if len(sys.argv) > 4 else 40


def png(w, h):
    # Horizontal stripes of two greys: compresses like a calm UI page, not like noise.
    row_a = b"\x00" + b"\x1c\x1c\x1e" * w
    row_b = b"\x00" + b"\xf2\xf2\xf7" * w
    raw = b"".join(row_a if (y // 8) % 2 else row_b for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


p = png(w, h)
data = base64.standard_b64encode(p).decode()
chunks = [data[i:i + 4096] for i in range(0, len(data), 4096)] or [""]
out = ["\033[H\033[2J\033[?25l", "\033_Ga=d,d=A,q=2\033\\"]
for i, ch in enumerate(chunks):
    more = 1 if i < len(chunks) - 1 else 0
    head = f"a=T,f=100,t=d,c={cols},r={rows},q=2,m={more}" if i == 0 else f"m={more},q=2"
    out.append(f"\033_G{head};{ch}\033\\")
sys.stdout.write("".join(out))
sys.stdout.write(f"\033[{rows + 1};1HKPANE-READY {w}x{h} png={len(p)} rgba={w * h * 4}\n")
sys.stdout.flush()
time.sleep(3600)
