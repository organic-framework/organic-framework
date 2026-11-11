"""Does the terminal this runs in accept a kitty temporary file (t=t) from /tmp on macOS?

Writes a 1x1 RGBA file named tty-graphics-protocol-* in /tmp, sends a=q with t=t, and prints the terminal's
answer and whether the file was consumed (a terminal deletes a temporary file it accepted). Inside a herdr pane
this tests herdr's embedded libghostty-vt, not the outer terminal.
"""
import base64
import os
import select
import sys
import termios
import time
import tty

path = f"/tmp/tty-graphics-protocol-oflab-{os.getpid()}"
with open(path, "wb") as fh:
    fh.write(b"\xff\x00\x00\xff")
fd = sys.stdin.fileno()
old = termios.tcgetattr(fd)
tty.setraw(fd)
try:
    os.write(sys.stdout.fileno(), f"\033_Gi=41,s=1,v=1,a=q,t=t,f=32;{base64.b64encode(path.encode()).decode()}\033\\".encode())
    buf, end = b"", time.time() + 2
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            buf += os.read(fd, 1024)
finally:
    termios.tcsetattr(fd, termios.TCSADRAIN, old)
consumed = not os.path.exists(path)
if not consumed:
    os.unlink(path)
print(f"\nKTEMP answer={buf!r} consumed={consumed} realpath={os.path.realpath('/tmp')}")
