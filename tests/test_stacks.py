"""bin/of_stacks.py: every thread's stack on SIGUSR1, written by a thread of its own that the signal's wakeup fd wakes.

- of_conductor, msg 01a1f10f-bb50: faulthandler's C dump crashed a test of-server on Python 3.15.2 as its request
  threads ended, and put a crash dialog on the founder's screen;
- of_review on organic-framework PR 368 (Codex run 01a1f154-2ea7): a Python handler in the main thread raised
  "reentrant call" into a main thread blocked writing stderr into a full pipe, and ran only after a C call returned."""

import os
import select
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BIN = os.path.join(HERE, "..", "bin")

CHURN = textwrap.dedent("""\
    import os, signal, sys, threading, time
    sys.path.insert(0, sys.argv[1])
    import of_stacks
    print("mode", of_stacks.install(signal.SIGUSR1, sys.stdout), flush=True)
    stop = threading.Event()
    def churn():  # threads that start and end all the time, as an HTTP server's request threads do
        while not stop.is_set():
            ts = [threading.Thread(target=time.sleep, args=(0.001,), name="req") for _ in range(8)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
    threading.Thread(target=churn, name="churn", daemon=True).start()
    for _ in range(25):
        os.kill(os.getpid(), signal.SIGUSR1)
        time.sleep(0.02)
    stop.set()
    time.sleep(0.2)
    print("survived", flush=True)
""")

FULL_PIPE = textwrap.dedent("""\
    import signal, sys
    sys.path.insert(0, sys.argv[1])
    import of_stacks
    of_stacks.install(signal.SIGUSR1, sys.stderr)
    print("ready", flush=True)
    try:
        for _ in range(400):  # far more than a pipe holds: the main thread blocks inside this write
            sys.stderr.write("x" * 4096 + "\\n")
        sys.stderr.flush()
    except BaseException as e:
        print("main raised", type(e).__name__, e, flush=True)
        sys.exit(3)
    print("survived", flush=True)
""")

SQLITE_WAIT = textwrap.dedent("""\
    import signal, sqlite3, sys
    sys.path.insert(0, sys.argv[1])
    import of_stacks
    of_stacks.install(signal.SIGUSR1, sys.stderr)
    a = sqlite3.connect(sys.argv[2], isolation_level=None)
    a.execute("CREATE TABLE t (x)")
    a.execute("BEGIN EXCLUSIVE")
    b = sqlite3.connect(sys.argv[2], timeout=20, isolation_level=None)
    def waits_in_sqlite():  # SQLite's busy handler sleeps in C and never looks for a signal until it gives up
        try:
            b.execute("SELECT * FROM t").fetchall()
        except sqlite3.OperationalError:
            pass
    print("ready", flush=True)
    waits_in_sqlite()
""")

OTHER_WAKEUP_FD = textwrap.dedent("""\
    import os, signal, sys, time
    sys.path.insert(0, sys.argv[1])
    import of_stacks
    r, w = os.pipe()
    os.set_blocking(w, False)
    if sys.argv[2] == "before":  # an event loop's wakeup fd, set before install()
        signal.set_wakeup_fd(w)
    mode = of_stacks.install(signal.SIGUSR1, sys.stdout)
    if sys.argv[2] == "after":   # set after install(): it cuts the dumper thread off
        signal.set_wakeup_fd(w)
    print("mode", mode, flush=True)
    os.kill(os.getpid(), signal.SIGUSR1)
    time.sleep(0.3)
    print("theirs kept", signal.set_wakeup_fd(-1) == w, flush=True)
""")

NATIVE_READ = textwrap.dedent("""\
    import ctypes, os, signal, sys, threading, time
    sys.path.insert(0, sys.argv[1])
    import of_stacks
    if sys.argv[2] == "install":
        of_stacks.install(signal.SIGUSR1, sys.stderr)
    else:  # what a bare Python handler does: SA_RESTART cleared
        signal.signal(signal.SIGUSR1, lambda *a: None)
    libc = ctypes.CDLL(None, use_errno=True)
    r, w = os.pipe()
    got = []
    def native_read():  # a C library's read, which Python never retries
        buf = ctypes.create_string_buffer(1)
        n = libc.read(r, buf, 1)
        got.append((n, ctypes.get_errno()))
    t = threading.Thread(target=native_read, name="native")
    t.start()
    time.sleep(0.3)
    signal.pthread_kill(t.ident, signal.SIGUSR1)  # delivered in the thread that waits in read(2)
    time.sleep(0.3)
    os.write(w, b"x")
    t.join(5)
    print("read", got, flush=True)
""")

INSTALL_FAILS = textwrap.dedent("""\
    import os, signal, sys, threading, time
    sys.path.insert(0, sys.argv[1])
    import of_stacks
    if sys.argv[2] == "no-pipe":  # no descriptor left: the main thread's handler dumps instead
        def no_pipe():
            raise OSError(24, "Too many open files")
        of_stacks.os.pipe = no_pipe
        print("mode", of_stacks.install(signal.SIGUSR1, sys.stdout), flush=True)
        os.kill(os.getpid(), signal.SIGUSR1)
        time.sleep(0.2)
    else:  # from a thread that is not the main one: signal.signal refuses, and install() says so
        got = []
        t = threading.Thread(target=lambda: got.append(of_stacks.install(signal.SIGUSR1, sys.stdout)))
        t.start()
        t.join()
        print("mode", got[0], flush=True)
    print("started", flush=True)
""")


LEFT_CLEAN = textwrap.dedent("""\
    import os, signal, sys, threading, time
    sys.path.insert(0, sys.argv[1])
    import of_stacks
    def fds():
        return set(os.listdir("/dev/fd"))
    calls = []
    if sys.argv[2] == "theirs-kept":  # every set_wakeup_fd call install() makes, after theirs is set
        r, w = os.pipe()
        os.set_blocking(w, False)
        signal.set_wakeup_fd(w)
        real = signal.set_wakeup_fd
        def spy(fd, **kw):
            calls.append("theirs" if fd == w else fd)
            return real(fd, **kw)
        signal.set_wakeup_fd = spy
    elif sys.argv[2] == "unrestorable":  # theirs was set, then made blocking: set_wakeup_fd refuses to put it back
        r, w = os.pipe()
        os.set_blocking(w, False)
        signal.set_wakeup_fd(w)
        os.set_blocking(w, True)
    else:  # no thread can start
        def no_thread(self):
            raise RuntimeError("can't start new thread")
        threading.Thread.start = no_thread
    before = fds()
    print("mode", of_stacks.install(signal.SIGUSR1, sys.stdout), flush=True)
    print("calls", [c if c in ("theirs", -1) else "ours" for c in calls], flush=True)
    if sys.argv[2] == "theirs-kept":
        signal.set_wakeup_fd = real
        print("theirs kept", signal.set_wakeup_fd(w) == w, flush=True)
    print("wakeup fd", signal.set_wakeup_fd(-1), flush=True)
    print("fds left", sorted(fds() - before), flush=True)
    os.kill(os.getpid(), signal.SIGUSR1)
    time.sleep(0.2)
""")


def run(code, *args, timeout=60):
    return subprocess.run([sys.executable, "-c", code, BIN, *args], capture_output=True, text=True, timeout=timeout)


class StacksTest(unittest.TestCase):

    def test_a_dump_among_threads_that_end_never_kills_the_process(self):
        r = run(CHURN)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        self.assertIn("mode thread", r.stdout)
        self.assertIn("survived", r.stdout)
        self.assertGreaterEqual(r.stdout.count("stack dump on SIGUSR1"), 1)
        self.assertIn('"MainThread" (most recent call first):', r.stdout)
        self.assertIn('"churn" (most recent call first):', r.stdout)
        self.assertNotIn('"of-stacks"', r.stdout)  # the dumper leaves itself out

    def test_a_main_thread_blocked_on_a_full_stderr_pipe_gets_no_exception(self):
        p = subprocess.Popen([sys.executable, "-c", FULL_PIPE, BIN], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            p.stdout.readline()
            time.sleep(0.5)  # nobody reads stderr: the pipe is full and the main thread waits inside its write
            os.kill(p.pid, signal.SIGUSR1)
            time.sleep(0.3)
            out, err = p.communicate(timeout=60)
        finally:
            if p.poll() is None:
                p.kill()
                p.communicate()
        self.assertEqual(p.returncode, 0, out.decode())
        self.assertIn(b"survived", out)
        self.assertNotIn(b"reentrant", out + err)
        self.assertIn(b"stack dump on SIGUSR1", err)

    def test_the_dump_comes_while_the_main_thread_waits_inside_a_c_call(self):
        db = os.path.join(tempfile.mkdtemp(prefix="of-stacks-"), "x.db")
        p = subprocess.Popen([sys.executable, "-c", SQLITE_WAIT, BIN, db], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        try:
            p.stdout.readline()
            time.sleep(0.3)
            sent = time.monotonic()
            os.kill(p.pid, signal.SIGUSR1)
            got = b""
            while b"in waits_in_sqlite" not in got and time.monotonic() - sent < 10:
                ready, _, _ = select.select([p.stderr], [], [], 0.1)
                if ready:
                    got += os.read(p.stderr.fileno(), 65536)
            waited = time.monotonic() - sent
        finally:
            p.kill()
            p.communicate(timeout=30)
        self.assertIn(b"stack dump on SIGUSR1", got)
        self.assertIn(b"in waits_in_sqlite", got)
        self.assertLess(waited, 10, "the dump waited for SQLite's busy timeout")   # the timeout is 20 s
        self.assertNotIn(b"of_stacks.py", got)  # no handler frames on top of the main thread's stack
        self.assertEqual(got.count(b"stack dump on SIGUSR1"), 1)

    def test_someone_elses_wakeup_fd_is_kept_and_the_main_thread_dumps(self):
        r = run(OTHER_WAKEUP_FD, "before")
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        self.assertIn("mode handler", r.stdout)
        self.assertIn("theirs kept True", r.stdout)
        self.assertEqual(r.stdout.count("stack dump on SIGUSR1"), 1)
        self.assertIn('"MainThread" (most recent call first):', r.stdout)
        self.assertNotIn("of_stacks.py", r.stdout)  # the main thread's stack starts where the signal found it

    def test_a_wakeup_fd_set_after_install_leaves_the_dump_to_the_main_thread(self):
        r = run(OTHER_WAKEUP_FD, "after")
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        self.assertIn("mode thread", r.stdout)
        self.assertEqual(r.stdout.count("stack dump on SIGUSR1"), 1)

    def test_a_native_call_in_another_thread_goes_on_after_the_signal(self):
        r = run(NATIVE_READ, "install")
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        self.assertIn("read [(1, ", r.stdout)                 # restarted, then read the byte
        self.assertIn("stack dump on SIGUSR1", r.stderr)
        self.assertIn('"native" (most recent call first):', r.stderr)
        bare = run(NATIVE_READ, "bare")                         # the control: without SA_RESTART it fails
        self.assertIn(f"read [(-1, {__import__('errno').EINTR})]", bare.stdout)

    def test_install_never_stops_the_service_it_starts_in(self):
        r = run(INSTALL_FAILS, "no-pipe")
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        self.assertIn("mode handler", r.stdout)
        self.assertEqual(r.stdout.count("stack dump on SIGUSR1"), 1)
        r = run(INSTALL_FAILS, "not-main")
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        self.assertIn("mode off", r.stdout)
        self.assertIn("started", r.stdout)
        self.assertIn("of_stacks: no stack dump on signal", r.stderr)

    def test_a_failed_install_leaves_no_wakeup_fd_and_no_open_pipe(self):
        """of_review on organic-framework PR 370: when the earlier owner's wakeup fd could not be put back, the new
        pipe stayed the wakeup fd, both ends open, with no dumper."""
        for case in ("unrestorable", "no-thread"):
            with self.subTest(case=case):
                r = run(LEFT_CLEAN, case)
                self.assertEqual(r.returncode, 0, r.stderr[-500:])
                self.assertIn("mode handler", r.stdout)
                self.assertIn("wakeup fd -1", r.stdout)
                self.assertIn("fds left []", r.stdout)
                self.assertEqual(r.stdout.count("stack dump on SIGUSR1"), 1)

    def test_someone_elses_wakeup_fd_is_never_taken_off_while_install_puts_it_back(self):
        """of_review on organic-framework PR 373: the kept branch went through _release, which set -1 and then theirs
        again, so a signal in between found no wakeup fd."""
        r = run(LEFT_CLEAN, "theirs-kept")
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        self.assertIn("mode handler", r.stdout)
        self.assertIn("calls ['ours', 'theirs']", r.stdout)
        self.assertIn("theirs kept True", r.stdout)
        self.assertIn("fds left []", r.stdout)

    def test_a_closed_stream_loses_the_dump_never_the_process(self):
        sys.path.insert(0, BIN)
        try:
            import of_stacks
        finally:
            sys.path.remove(BIN)
        fh = open(os.devnull, "w")
        fh.close()
        of_stacks.dump(fh)  # no exception


if __name__ == "__main__":
    unittest.main()
