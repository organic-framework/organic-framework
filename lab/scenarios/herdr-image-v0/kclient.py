"""Attach a herdr client to the oflab session inside a pseudo-terminal that plays a Ghostty-like outer terminal:
it answers the client's queries (kitty graphics a=q -> OK, primary device attributes, XTVERSION as ghostty) and
records every byte the client draws. Then reports whether any kitty graphics command reached the 'screen'."""
import os
import pty
import re
import select
import sys
import time

out_path = sys.argv[1]
seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 8
env = {k: v for k, v in os.environ.items() if not k.startswith("HERDR_")}
env.update(TERM="xterm-ghostty", TERM_PROGRAM="ghostty", COLORTERM="truecolor")
pid, fd = pty.fork()
if pid == 0:
    os.execvpe("herdr", ["herdr", "--session", "oflab"], env)
import fcntl
import struct
import termios
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 160, 1440, 900))
seen, end = b"", time.time() + seconds
while time.time() < end:
    r, _, _ = select.select([fd], [], [], 0.1)
    if not r:
        continue
    try:
        b = os.read(fd, 65536)
    except OSError:
        break
    seen += b
    for q in re.findall(rb"\x1b_G([^\x1b]*a=q[^\x1b;]*)", b):
        i = re.search(rb"i=(\d+)", q)
        os.write(fd, b"\x1b_Gi=" + (i.group(1) if i else b"1") + b";OK\x1b\\")
    if b"\x1b[c" in b or b"\x1b[0c" in b:
        os.write(fd, b"\x1b[?62;22;52c")
    if b"\x1b[>0q" in b or b"\x1b[>q" in b:
        os.write(fd, b"\x1bP>|ghostty 1.2.0\x1b\\")
    if b"\x1b[16t" in b:
        os.write(fd, b"\x1b[6;18;9t")
    if b"\x1b[14t" in b:
        os.write(fd, b"\x1b[4;900;1440t")
os.kill(pid, 9)
with open(out_path, "wb") as fh:
    fh.write(seen)
cmds = re.findall(rb"\x1b_G([^;\x1b]*)", seen)
print(f"bytes drawn: {len(seen)}; kitty commands: {len(cmds)}; {sorted(set(c[:40] for c in cmds))[:8]}")
print("placeholder cells (U+10EEEE):", seen.count("\U0010EEEE".encode()))
print("queries the client asked:", sorted(set(re.findall(rb"\x1b\[[>?]?[0-9;]*[a-zA-Z]", seen)) & {b"\x1b[c", b"\x1b[>q", b"\x1b[16t", b"\x1b[14t"}))
