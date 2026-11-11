"""A stand-in for Ghostty 1.4.0 as herdr's outer terminal, so a check can read what herdr sends without anyone's eyes.

It attaches a herdr client to a lab session inside a pseudo-terminal and answers the client the way Ghostty 1.4.0
does (src/terminal/kitty/graphics_image.zig at tag v1.4.0):
- every kitty graphics command is parsed, chunked transfers are joined, and a reply is sent when the command
  asks for one (an id and q=0, or q=1 for errors only);
- t=f (a regular file): the file is read and its size checked;
- t=t (a temporary file): accepted only in /tmp, /private/tmp (macOS) or $TMPDIR, with "tty-graphics-protocol"
  in its name, then read and deleted, as Ghostty does;
- t=d (inline): the base64 payload is counted;
- the usual start-up queries (primary device attributes, XTVERSION, cell and window size in pixels, colours).

The difference from herdr-image-v0's kclient.py: v0 answered only a=q, so herdr never got a reply to a file
transfer and fell back to inline after its 3 s timeout; v1 answers every transfer.

    python3 kterm.py SESSION OUT.json SECONDS [COLS ROWS CELL_W CELL_H]

Prints a summary and writes every event with its time to OUT.json.
"""
import base64
import json
import os
import pty
import re
import select
import sys
import time

session, out_path, seconds = sys.argv[1], sys.argv[2], float(sys.argv[3])
cols, rows = (int(sys.argv[4]), int(sys.argv[5])) if len(sys.argv) > 5 else (160, 50)
cell_w, cell_h = (int(sys.argv[6]), int(sys.argv[7])) if len(sys.argv) > 7 else (18, 36)  # Retina-like cells
TMPDIR = os.environ.get("TMPDIR", "/tmp")

env = {k: v for k, v in os.environ.items()
       if not k.startswith("HERDR_") and k not in ("SSH_CONNECTION", "SSH_TTY", "SSH_CLIENT", "TMUX", "STY")}
env.update(TERM="xterm-ghostty", TERM_PROGRAM="ghostty", TERM_PROGRAM_VERSION="1.4.0", COLORTERM="truecolor")
pid, fd = pty.fork()
if pid == 0:
    os.execvpe("herdr", ["herdr", "--session", session], env)

import fcntl  # noqa: E402
import struct  # noqa: E402
import termios  # noqa: E402

fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, cols * cell_w, rows * cell_h))

t0 = time.time()
events = []
total_in = 0
pending = {}  # chunked transfer key -> {"ctl": dict, "data": [str]}


def ev(kind, **kw):
    kw.update(kind=kind, t=round(time.time() - t0, 3))
    events.append(kw)


def reply(ctl, msg, error):
    q = ctl.get("q", "0")
    ident = ctl.get("i") or ctl.get("I")
    if not ident or q == "2" or (q == "1" and not error):
        return
    key = "i" if ctl.get("i") else "I"
    extra = f",p={ctl['p']}" if ctl.get("p") else ""
    os.write(fd, f"\x1b_G{key}={ident}{extra};{msg}\x1b\\".encode())
    ev("reply", id=ident, msg=msg)


def temp_ok(path):
    real = os.path.realpath(path)
    dirs = ["/tmp", "/dev/shm", "/private/tmp", TMPDIR, os.path.realpath(TMPDIR)]
    in_tmp = any(real == d.rstrip("/") or real.startswith(d.rstrip("/") + "/") for d in dirs)
    return in_tmp and "tty-graphics-protocol" in real


def handle(ctl, payload):
    a, t = ctl.get("a", "t"), ctl.get("t", "d")
    if a in ("t", "T", "q") and t in ("f", "t", "s"):
        path = base64.b64decode(payload or "").decode(errors="replace")
        size = None
        if t == "s":
            ev("tx", medium="shm", a=a, path=path, f=ctl.get("f"), s=ctl.get("s"), v=ctl.get("v"))
            reply(ctl, "EINVAL:shm not emulated", True)
            return
        if t == "t" and not temp_ok(path):
            ev("tx", medium="temp-rejected", a=a, path=path)
            reply(ctl, "EINVAL:temporary file not in temp dir", True)
            return
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as fh:
                fh.read()
            if t == "t":
                os.unlink(path)
        except OSError as e:
            ev("tx", medium={"f": "file", "t": "temp"}[t], a=a, path=path, error=str(e))
            reply(ctl, "EBADF:" + type(e).__name__, True)
            return
        ev("tx", medium={"f": "file", "t": "temp"}[t], a=a, path=path, bytes=size,
           f=ctl.get("f"), s=ctl.get("s"), v=ctl.get("v"), id=ctl.get("i"))
        reply(ctl, "OK", False)
        return
    if a in ("t", "T", "q"):
        ev("tx", medium="inline", a=a, bytes_b64=len(payload or ""), f=ctl.get("f"), s=ctl.get("s"),
           v=ctl.get("v"), id=ctl.get("i"), o=ctl.get("o"))
        reply(ctl, "OK", False)
        return
    if a == "p":
        ev("place", id=ctl.get("i"), c=ctl.get("c"), r=ctl.get("r"), x=ctl.get("x"), y=ctl.get("y"),
           w=ctl.get("w"), h=ctl.get("h"))
        reply(ctl, "OK", False)
        return
    if a == "d":
        ev("delete", d=ctl.get("d"), id=ctl.get("i"))
        return
    ev("other", a=a)


def kitty(body):
    """One chunked transfer at a time, as the protocol requires: continuation chunks carry only m (and q),
    and the first chunk's keys apply to the whole transfer."""
    head, _, payload = body.partition(";")
    ctl = dict(kv.split("=", 1) for kv in head.split(",") if "=" in kv)
    if "cur" in pending:
        pending["cur"]["data"].append(payload)
        if ctl.get("m", "0") == "0":
            first = pending.pop("cur")
            handle(first["ctl"], "".join(first["data"]))
        return
    if ctl.get("m") == "1":
        pending["cur"] = {"ctl": ctl, "data": [payload]}
        return
    handle(ctl, payload)


PIXEL_MOUSE = os.environ.get("KTERM_PIXEL_MOUSE", "1") == "1"  # Ghostty 1.4.0 knows mode 1016 (SGR pixel mouse)
QUERIES = [
    # herdr's client asks these together at start (src/client/terminal_setup.rs FULL_QUERY); it takes the
    # server-file ("direct") image path only when the terminal reports mode 1016 and exact cell pixels.
    (b"\x1b[?1016$p", b"\x1b[?1016;2$y" if PIXEL_MOUSE else b"\x1b[?1016;0$y"), (b"\x1b[?u", b"\x1b[?0u"),
    (b"\x1b[c", b"\x1b[?62;22;52c"), (b"\x1b[0c", b"\x1b[?62;22;52c"),
    (b"\x1b[>q", b"\x1bP>|ghostty 1.4.0\x1b\\"), (b"\x1b[>0q", b"\x1bP>|ghostty 1.4.0\x1b\\"),
    (b"\x1b[16t", f"\x1b[6;{cell_h};{cell_w}t".encode()),
    (b"\x1b[14t", f"\x1b[4;{rows * cell_h};{cols * cell_w}t".encode()),
    (b"\x1b[18t", f"\x1b[8;{rows};{cols}t".encode()),
    (b"\x1b]10;?\x1b\\", b"\x1b]10;rgb:ffff/ffff/ffff\x1b\\"), (b"\x1b]11;?\x1b\\", b"\x1b]11;rgb:0000/0000/0000\x1b\\"),
    (b"\x1b]10;?\x07", b"\x1b]10;rgb:ffff/ffff/ffff\x07"), (b"\x1b]11;?\x07", b"\x1b]11;rgb:0000/0000/0000\x07"),
]
APC = re.compile(rb"\x1b_G(.*?)\x1b\\", re.S)
buf = b""
end = t0 + seconds
while time.time() < end:
    r, _, _ = select.select([fd], [], [], 0.05)
    if not r:
        continue
    try:
        b = os.read(fd, 1 << 20)
    except OSError:
        break
    total_in += len(b)
    for q, ans in QUERIES:
        if q in b:
            os.write(fd, ans)
    buf += b
    last = 0
    for m in APC.finditer(buf):
        kitty(m.group(1).decode(errors="replace"))
        last = m.end()
    buf = buf[last:]
    k = buf.rfind(b"\x1b_G")
    buf = buf[k:] if k >= 0 else buf[-2:]  # keep only an unfinished command

os.kill(pid, 9)
summary = {
    "seconds": seconds, "bytes_from_client": total_in,
    "transfers": [e for e in events if e["kind"] == "tx"],
    "placements": sum(1 for e in events if e["kind"] == "place"),
    "first_placement_s": next((e["t"] for e in events if e["kind"] == "place"), None),
    "replies": sum(1 for e in events if e["kind"] == "reply"),
}
with open(out_path, "w") as fh:
    json.dump({"summary": summary, "events": events}, fh, indent=1)
print(json.dumps(summary, indent=1))
