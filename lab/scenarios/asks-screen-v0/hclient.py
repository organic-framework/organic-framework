"""An outer terminal for a lab herdr client, driven by a script: keys and mouse wheel go in exactly as a terminal sends
them, so herdr's own client and server carry them to the program in the pane (asks-screen-v0).

    python3 hclient.py SESSION COLS ROWS SCRIPT

SCRIPT holds one action per line:
    sleep S            wait S seconds
    keys TEXT          send TEXT as typed; Python escapes allowed (\\x1b[Z is Shift+Tab, \\x1b[B is down)
    wheel up|down X Y  one wheel notch at cell X, Y (1-based), as SGR mouse (herdr's client enables it)
    read NAME          save the pane's visible text (pane $PANE) to $OUTDIR/NAME.txt
    run CMD            run a shell command (for example: add a card to the fixture store while the screen is open)
The client answers herdr's start-up queries as Ghostty 1.4.0 does (from herdr-image-v1's kterm.py).
"""
import os
import pty
import select
import struct
import sys
import termios
import time
import fcntl

session, cols, rows, script = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
cell_w, cell_h = 18, 36
QUERIES = [
    (b"\x1b[?1016$p", b"\x1b[?1016;2$y"), (b"\x1b[?u", b"\x1b[?0u"),
    (b"\x1b[c", b"\x1b[?62;22;52c"), (b"\x1b[0c", b"\x1b[?62;22;52c"),
    (b"\x1b[>q", b"\x1bP>|ghostty 1.4.0\x1b\\"), (b"\x1b[>0q", b"\x1bP>|ghostty 1.4.0\x1b\\"),
    (b"\x1b[16t", f"\x1b[6;{cell_h};{cell_w}t".encode()),
    (b"\x1b[14t", f"\x1b[4;{rows * cell_h};{cols * cell_w}t".encode()),
    (b"\x1b[18t", f"\x1b[8;{rows};{cols}t".encode()),
    (b"\x1b]10;?\x1b\\", b"\x1b]10;rgb:ffff/ffff/ffff\x1b\\"), (b"\x1b]11;?\x1b\\", b"\x1b]11;rgb:0000/0000/0000\x1b\\"),
    (b"\x1b]10;?\x07", b"\x1b]10;rgb:ffff/ffff/ffff\x07"), (b"\x1b]11;?\x07", b"\x1b]11;rgb:0000/0000/0000\x07"),
]

env = {k: v for k, v in os.environ.items() if not k.startswith("HERDR_") and k not in ("TMUX", "SSH_TTY")}
env.update(TERM_PROGRAM="ghostty", TERM="xterm-ghostty" if os.path.exists("/Applications/Ghostty.app") else "xterm-256color")
pid, fd = pty.fork()
if pid == 0:
    os.execvpe("herdr", ["herdr", "--session", session], env)
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, cols * cell_w, rows * cell_h))
t0 = time.time()


def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.05)
        if not r:
            continue
        try:
            b = os.read(fd, 1 << 20)
        except OSError:
            return False
        for q, ans in QUERIES:
            if q in b:
                os.write(fd, ans)
    return True


pump(2.0)  # the client's start-up
for line in open(script):
    line = line.rstrip("\n")
    if not line.strip() or line.startswith("#"):
        continue
    op, _, arg = line.partition(" ")
    if op == "sleep":
        pump(float(arg))
    elif op == "keys":
        os.write(fd, arg.encode().decode("unicode_escape").encode("latin-1"))
        pump(0.3)
    elif op == "wheel":
        way, x, y = arg.split()
        os.write(fd, f"\x1b[<{64 if way == 'up' else 65};{x};{y}M".encode())
        pump(0.3)
    elif op == "read":
        import subprocess
        pump(0.4)
        r = subprocess.run(["herdr", "--session", session, "pane", "read", os.environ["PANE"], "--source", "visible",
                            "--lines", str(rows)], capture_output=True, text=True, env=env, timeout=15)
        with open(os.path.join(os.environ["OUTDIR"], arg + ".txt"), "w") as fh:
            fh.write(r.stdout if r.returncode == 0 else f"[read failed: {r.stderr.strip()}]")
        print(f"{time.time() - t0:7.2f} read {arg}", flush=True)
    elif op == "run":
        import subprocess
        subprocess.run(["sh", "-c", arg], env=os.environ, timeout=60)
        pump(0.2)
os.kill(pid, 9)
