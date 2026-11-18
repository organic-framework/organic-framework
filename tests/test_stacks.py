"""bin/of_stacks.py: every thread's stack on SIGUSR1 by a Python handler, while threads start and end around it
(of_conductor, msg 01a1f10f-bb50: faulthandler's C dump crashed a test of-server on Python 3.15.2 as its request
threads ended, and put a crash dialog on the founder's screen)."""

import os
import subprocess
import sys
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

CHILD = textwrap.dedent("""\
    import os, signal, sys, threading, time
    sys.path.insert(0, sys.argv[1])
    import of_stacks
    of_stacks.install(signal.SIGUSR1, sys.stdout)
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
    print("survived")
""")


class StacksTest(unittest.TestCase):

    def test_a_dump_among_threads_that_end_never_kills_the_process(self):
        r = subprocess.run([sys.executable, "-c", CHILD, os.path.join(HERE, "..", "bin")], capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        self.assertIn("survived", r.stdout)
        self.assertGreaterEqual(r.stdout.count("stack dump on SIGUSR1"), 1)
        self.assertIn('"MainThread" (most recent call first):', r.stdout)
        self.assertIn('"churn" (most recent call first):', r.stdout)


if __name__ == "__main__":
    unittest.main()
