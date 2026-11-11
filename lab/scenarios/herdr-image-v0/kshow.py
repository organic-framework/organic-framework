"""Show a tiny red PNG in this terminal by kitty graphics, in one of two ways, then wait.
  direct:  a=T with c/r (what the conductor panel did)
  virtual: a=T,U=1 then Unicode placeholder cells (the way images cross multiplexers)
"""
import base64
import struct
import sys
import time
import zlib

mode = sys.argv[1]


def png(w=4, h=4):
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + \
        chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


data = base64.standard_b64encode(png()).decode()
sys.stdout.write("\033[2J\033[H")
if mode == "direct":
    sys.stdout.write(f"\033_Ga=T,f=100,t=d,c=4,r=2,q=2;{data}\033\\")
else:
    sys.stdout.write(f"\033_Ga=T,f=100,t=d,i=77,U=1,c=4,r=2,q=2;{data}\033\\")
    diac = ["̅", "̍", "̎", "̐"]  # row/column diacritics 0..3 from kitty's table
    sys.stdout.write("\033[38;5;77m")  # the image id in the foreground colour
    for r in range(2):
        sys.stdout.write("".join("\U0010EEEE" + diac[r] + diac[c] for c in range(4)) + "\n")
    sys.stdout.write("\033[39m")
sys.stdout.write("KSHOW-" + mode + "\n")
sys.stdout.flush()
time.sleep(600)
