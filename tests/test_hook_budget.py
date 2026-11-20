"""Each Claude Code hook stays within a CPU and memory budget per call (of_conductor msg 01a1d663-4530, 2026-11-13: about
70 agents run the hooks on every tool call; organic-framework-build reports/of-log-readers-2026-11-14.md, 3.6).

Measured as a whole process on a scratch store, the way Claude Code runs it: bin/of-hook <Event>. On the founder's Mac
on 2026-11-14 a hook took 55 to 97 ms of CPU and 63 MB this way (through bin/of, which Python compiles on every run:
144 to 243 ms). The budget leaves room for a slower machine, and still fails on a store scan or a heavy import.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ENTRY = os.path.join(HERE, "..", "bin", "of-hook")
CPU_S = 0.6
RSS_MB = 150
EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop")
# Each hook runs under a child of its own, which prints its one child's CPU and peak memory: the test process's own
# RUSAGE_CHILDREN holds the largest child any earlier test waited for, which CI read as 157 to 237 MB while the hooks
# measured 25 to 73 MB (of_review on organic-framework PR 496, comment 7050471499)
MEASURE = """
import json, resource, subprocess, sys
r = subprocess.run(sys.argv[1:], input=sys.stdin.read(), capture_output=True, text=True, timeout=60)
u = resource.getrusage(resource.RUSAGE_CHILDREN)
print(json.dumps({"cpu": u.ru_utime + u.ru_stime, "rss": u.ru_maxrss, "traceback": "Traceback" in r.stderr,
                  "stderr": r.stderr[-2000:]}))
"""


class HookBudgetTest(unittest.TestCase):
    def test_each_hook_within_its_budget(self):
        # a hook may start the background ingest, which can still write into the scratch state as the test ends
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
            env = dict(os.environ, OF_STATE=t, OF_AUTOMATION="1", SSH_AUTH_SOCK="")
            for k in ("CLAUDE_CODE_SESSION_ID", "HERDR_PANE_ID", "HERDR_SOCKET_PATH"):
                env.pop(k, None)
            for ev in EVENTS:
                payload = {"hook_event_name": ev, "session_id": "budget", "cwd": t, "prompt": "hi",
                           "tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_response": {"stdout": "x"},
                           "transcript_path": os.path.join(t, "none.jsonl")}
                best, peak = None, 0
                for _ in range(3):  # the best of three: a busy machine's noise is not the hook's cost
                    r = subprocess.run([sys.executable, "-c", MEASURE, sys.executable, ENTRY, ev],
                                       input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=90)
                    got = json.loads(r.stdout.strip().splitlines()[-1])
                    self.assertFalse(got["traceback"], f"{ev}: {got['stderr']}")   # decided in the child, whole
                    best = got["cpu"] if best is None else min(best, got["cpu"])
                    peak = max(peak, got["rss"])
                self.assertLess(best, CPU_S, f"{ev}: {best:.3f} s of CPU")
                rss_mb = peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024  # bytes on macOS, KiB on Linux
                self.assertLess(rss_mb, RSS_MB, f"{ev}: {rss_mb:.1f} MB")

    def test_the_installer_writes_the_small_entry(self):
        sys.path.insert(0, os.path.join(HERE, "..", "bin"))
        import of_init
        cmd = of_init.hook_command("PostToolUse")
        self.assertTrue(cmd.endswith("/bin/of-hook PostToolUse"), cmd)
        self.assertTrue(of_init._is_of(cmd, of_init.OF))
        self.assertTrue(of_init._is_of(f"{sys.executable} {of_init.OF} hook Stop", of_init.OF))  # an older install
        s = of_init.with_hooks({"hooks": {"Stop": [{"hooks": [{"type": "command",
                                                              "command": f"{of_init.OF} hook Stop"}]}]}}, of_init.OF)
        self.assertEqual(s["hooks"]["Stop"][0]["hooks"][0]["command"], of_init.hook_command("Stop"))  # upgraded


if __name__ == "__main__":
    unittest.main()
