"""Every thread's stack on SIGUSR1: the watchdogs' dump before a restart, so a hang names where it waits. Owned by
of_server.

faulthandler.register(SIGUSR1, all_threads=True) walked the thread states from C inside the signal handler. On Python
3.15.2 it crashed the process when a thread was being freed at that moment (EXC_BAD_ACCESS in tstate_is_freed under
faulthandler_dump_traceback; of_conductor, msg 01a1f10f-bb50: a test of-server whose request threads had just ended,
and macOS put "Python quit unexpectedly" on the founder's screen), so the dump a hang needs killed the server instead.

A Python handler in the main thread, its first replacement, had two faults of its own (of_review on organic-framework
PR 368, Codex run 01a1f154-2ea7, both probed here):
- a main thread blocked writing stderr into a full pipe ran the handler inside that write, whose stderr.write raised
  "reentrant call" into the main thread: no dump, and the main thread's own code got the exception;
- a main thread inside a C call that never checks for signals (SQLite waiting out a lock for its busy timeout) ran the
  handler only when the call returned: a 6 s wait gave a 6 s late dump, after a watchdog has already restarted.

So the signal only wakes a thread of its own. Python's C-level handler writes the signal's number to the wakeup fd at
once, whatever the main thread is doing (signal.set_wakeup_fd); the dumper thread reads it and writes the dump with
os.write straight to the file's descriptor, never through the main thread's buffered stream and its lock. It reads
sys._current_frames(), which copies the frames under the interpreter's lock, so a thread that ends meanwhile is simply
missing, and it walks each frame by its code and line number without reading a source file. A thread the threading
module knows with no Python frame at that moment is listed as such; a thread started in C that Python never saw is
not, where faulthandler listed every thread state.

Not sigwait in a thread of its own: SIGUSR1 would have to be blocked in every other thread, and one that does not block
it (started before install(), or in C) takes the signal's default action, which ends the process; a blocked signal is
also inherited by every program the process starts. signal.signal() clears SA_RESTART, which faulthandler set, so a
native call in any thread could fail with EINTR: install() sets it again (signal.siginterrupt), since here no thread
needs the signal to break a wait.

Its limits: a thread whose C code holds the interpreter lock and never returns keeps the dumper from running, and the
watchdog restarts the process all the same. When something else already holds the process's one wakeup fd (an event
loop's), install() leaves it in place and dumps from a handler in the main thread instead, guarded the same way, with
Python's interrupting default kept so that handler runs soon; the main thread's frames then start at the frame it was
interrupted in, and a C call delays them as before.
"""
import os
import signal
import sys
import threading
import time

MAX_FRAMES = 200  # per thread; a deeper stack ends with how many frames were left out
_installed = {}   # signal number -> how it dumps ("thread" or "handler"), so a second install() changes nothing


def _render(skip=None, main_frame=None):
    names = {t.ident: t.name for t in threading.enumerate()}
    frames = sys._current_frames()
    if main_frame is not None:
        frames[threading.main_thread().ident] = main_frame
    frames.pop(skip, None)
    for ident in names:
        if ident is not None and ident != skip and ident not in frames:
            frames[ident] = None  # in C with no Python frame now, or ending
    lines = [f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} stack dump on SIGUSR1: {len(frames)} threads"]
    for ident, frame in sorted(frames.items()):
        lines.append(f'Thread 0x{ident:x} "{names.get(ident, "?")}" (most recent call first):')
        if frame is None:
            lines.append("  <no Python frame>")
            continue
        n, f = 0, frame
        while f is not None and n < MAX_FRAMES:
            code = f.f_code
            lines.append(f'  File "{code.co_filename}", line {f.f_lineno} in {code.co_name}')
            n, f = n + 1, f.f_back
        rest = 0
        while f is not None:
            rest, f = rest + 1, f.f_back
        if rest:
            lines.append(f"  ... {rest} more frames")
    return "\n" + "\n".join(lines) + "\n"  # its own line, after whatever partial line the stream holds


def _write(out, text):
    try:
        fd = out.fileno()
    except (AttributeError, OSError, ValueError):
        fd = None
    if fd is None:  # a stream with no descriptor (a test's StringIO): written from this thread
        out.write(text)
        out.flush()
        return
    data = memoryview(text.encode("utf-8", "backslashreplace"))
    while data:
        data = data[os.write(fd, data):]


def dump(file=None, *, skip=None, main_frame=None):
    """Write every thread's stack to FILE (stderr) now; never raises. SKIP: a thread left out (the dumper itself);
    MAIN_FRAME: the frame the main thread was interrupted in, in place of the handler's own frames on top of it."""
    try:
        _write(file or sys.stderr, _render(skip, main_frame))
    except Exception:  # noqa: BLE001: a closed log or a stream in use: the dump is lost, never the process
        pass


def _dumper(rfd, sig, file):
    me = threading.get_ident()
    while True:
        try:
            got = os.read(rfd, 512)
        except OSError:
            return
        if not got:
            return
        if sig in got:  # one byte per signal Python handled, each its number; the others are not ours
            dump(file, skip=me)


def _handler(file, wfd=None):
    """The Python handler, run in the main thread when it next checks for signals. With the dumper thread (WFD, the
    wakeup fd it reads) it only looks that the wakeup fd is still that one: code that set another one later has cut
    the dumper off, and this handler dumps instead."""
    def handle(signum, frame):
        if wfd is not None:
            try:
                cur = signal.set_wakeup_fd(-1)
                signal.set_wakeup_fd(cur)
            except (ValueError, OSError):
                cur = None
            if cur == wfd:
                return  # the dumper thread has written it
        dump(file, main_frame=frame)
    return handle


def _release(rfd, wfd):
    """Close the dumper's pipe, unregistering its write end first while it is still the wakeup fd: a closed fd left
    registered would take the next signal's byte, or a later pipe's number (of_review on organic-framework PR 370)."""
    try:
        cur = signal.set_wakeup_fd(-1)
        if cur not in (-1, wfd):
            signal.set_wakeup_fd(cur)  # someone else's: back in place
    except (ValueError, OSError):
        pass
    for fd in (rfd, wfd):
        try:
            os.close(fd)
        except OSError:
            pass


def _install_thread(sig, file):
    """The dumper thread and its wakeup fd: "thread", or None when another wakeup fd is in place (kept). On an error
    the pipe is closed and no longer the wakeup fd, and the error goes on to install()."""
    rfd, wfd = os.pipe()  # not inherited by child processes (PEP 446)
    try:
        os.set_blocking(wfd, False)  # set_wakeup_fd requires it
        signal.signal(sig, _handler(file, wfd))  # Python's C handler is the one that writes the wakeup byte
        prev = signal.set_wakeup_fd(wfd, warn_on_full_buffer=False)
        if prev != -1:  # someone else's wakeup fd (an event loop's): keep theirs, dump from the main thread
            signal.set_wakeup_fd(prev)  # refused when theirs was closed or made blocking: the except unregisters ours
    except BaseException:
        _release(rfd, wfd)
        raise
    if prev != -1:  # theirs is back and ours was never left registered: close the pipe, never touch theirs again
        os.close(rfd)  # (_release would set -1 and put theirs back: a signal in between would miss it; of_review, PR 373)
        os.close(wfd)
        return None
    try:
        if hasattr(signal, "siginterrupt"):
            signal.siginterrupt(sig, False)  # SA_RESTART again: a native call in any thread goes on after the signal
        threading.Thread(target=_dumper, args=(rfd, sig, file), name="of-stacks", daemon=True).start()
    except BaseException:
        _release(rfd, wfd)
        raise
    return "thread"


def install(sig=signal.SIGUSR1, file=None):
    """Write every thread's stack to FILE (stderr) on SIG. Call it from the main thread; a second call changes
    nothing. Returns how it dumps: "thread" (the dumper thread, woken by the wakeup fd), "handler" (from the main
    thread), or "off" when neither could be set up. It never raises: a service starts without its stack dump rather
    than not at all (of_review on organic-framework PR 368)."""
    sig = int(sig)
    if sig in _installed:
        return _installed[sig]
    try:
        mode = _install_thread(sig, file)
    except Exception:  # noqa: BLE001: no pipe, no thread: the main thread's handler still dumps
        mode = None
    if mode is None:
        try:
            signal.signal(sig, _handler(file))
            mode = "handler"
        except Exception as e:  # noqa: BLE001: not the main thread, or no such signal
            try:
                os.write(2, f"of_stacks: no stack dump on signal {sig}: {type(e).__name__}: {e}\n".encode())
            except OSError:
                pass
            return "off"
    _installed[sig] = mode
    return mode
