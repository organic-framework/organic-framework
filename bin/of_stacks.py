"""Every thread's stack, written on SIGUSR1 by a Python signal handler: the watchdogs' dump before a restart, so a hang
names where it waits. Owned by of_server.

faulthandler.register(SIGUSR1, all_threads=True) walked the thread states from C inside the signal handler. On Python
3.15.2 it crashed the process when a thread was being freed at that moment (EXC_BAD_ACCESS in tstate_is_freed under
faulthandler_dump_traceback; of_conductor, msg 01a1f10f-bb50: a test of-server whose request threads had just ended,
and macOS put "Python quit unexpectedly" on the founder's screen), so the dump a hang needs killed the server instead.
This handler runs in the main thread between bytecodes, as every Python signal handler does: a main thread blocked in
select, a lock or a sleep is woken for it. It reads sys._current_frames(), which holds the interpreter's lock while it
copies the frames, so a thread that ends meanwhile is simply missing. Its limit: a process whose lock is held by
C code that never returns writes nothing, and its watchdog restarts it all the same.
"""
import signal
import sys
import threading
import time
import traceback


def dump(file=None):
    out = file or sys.stderr
    names = {t.ident: t.name for t in threading.enumerate()}
    frames = sys._current_frames()
    lines = [f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} stack dump on SIGUSR1: {len(frames)} threads"]
    for ident, frame in sorted(frames.items()):
        lines.append(f'Thread 0x{ident:x} "{names.get(ident, "?")}" (most recent call first):')
        for fs in reversed(traceback.extract_stack(frame)):
            lines.append(f'  File "{fs.filename}", line {fs.lineno} in {fs.name}')
    try:
        out.write("\n".join(lines) + "\n")
        out.flush()
    except (OSError, ValueError):
        pass  # a closed log: the dump is lost, never the process


def install(sig=signal.SIGUSR1, file=None):
    """Write every thread's stack to FILE (stderr) on SIG. Call it from the main thread."""
    signal.signal(sig, lambda signum, frame: dump(file))
