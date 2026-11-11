"""Ask the terminal whether it speaks kitty graphics; print the answer as text (readable by herdr pane read or a file).

Three queries:
  1 a=q, direct placement (what the panel used)
  2 a=q with U=1, a virtual placement (Unicode placeholders), the way multiplexers pass images on
  3 a primary device attributes query (CSI c), so a silent terminal is told apart from a dead probe
"""
import os
import select
import sys
import termios
import time
import tty

out_file = sys.argv[1] if len(sys.argv) > 1 else None


def ask(seq, wait=1.5):
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    tty.setraw(fd)
    try:
        os.write(sys.stdout.fileno(), seq.encode())
        buf, end = b"", time.time() + wait
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.1)
            if r:
                buf += os.read(fd, 1024)
        return buf
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


one_px = "AAAA"  # one RGB pixel, base64
res = {
    "direct": ask(f"\033_Gi=31,s=1,v=1,a=q,t=d,f=24;{one_px}\033\\"),
    "virtual": ask(f"\033_Gi=32,s=1,v=1,a=q,t=d,f=24,U=1;{one_px}\033\\"),
    "device": ask("\033[c"),
}
lines = [f"TERM={os.environ.get('TERM')} TERM_PROGRAM={os.environ.get('TERM_PROGRAM')} HERDR={os.environ.get('HERDR_ENV')}"]
lines += [f"{k}: {v!r}" for k, v in res.items()]
text = "\n".join(lines)
print("\nPROBE\n" + text + "\nEND")
if out_file:
    with open(out_file, "w") as fh:
        fh.write(text + "\n")
