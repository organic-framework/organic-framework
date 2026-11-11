"""The scratch quota (docs/guard.md G15; a user organization's platform_owner, msg 01a1c8f3, and of_platform, msg
01a1c950, 2026-11-11): a session the pulse lists over its quota reads and deletes in its scratch folder, never writes."""
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import of_events  # noqa: E402
import of_guard  # noqa: E402
import of_hook  # noqa: E402
import of_scratch  # noqa: E402

S = "/private/tmp/claude-501/-Users-x-proj/S1"
SP = S + "/scratchpad"


def w(cmd, cwd="/Users/alex/proj"):
    return of_guard.scratch_write("Bash", {"command": cmd}, cwd, [S])


class ScratchWriteTest(unittest.TestCase):
    def test_writes_into_the_folder_are_found(self):
        for cmd in (f"git worktree add {SP}/wt main", f"git clone https://h/r {SP}/r", f"cp -r ~/data {SP}/",
                    f"echo x > {SP}/a.txt", f"python3 x.py >> {SP}/log", f"curl -s https://h/f -o {SP}/f.bin",
                    f"tar -xzf a.tgz -C {SP}/x", f"mkdir -p {SP}/new", f"python3 {SP}/run.py", f"sed -i '' s/a/b/ {SP}/f",
                    f"S={SP}; cat > $S/notes.md <<'EOF'\nhello\nEOF", f"cd {SP}/wt && cargo build",
                    f"git -C {SP}/wt pull", f"dd if=/dev/zero of={SP}/big bs=1m count=10", f"ls | tee {SP}/list",
                    f"gh run download 123 -D {SP}/art", f"gh repo clone o/r {SP}/r", f"kubectl cp ns/p:/data {SP}/d",
                    f"curl -sSL https://h/x --output-dir {SP} -O", f"wget -P {SP} https://h/x", f"tar czf {SP}/a.tgz src"):
            self.assertTrue(w(cmd), cmd)
        self.assertTrue(w("npm install", cwd=SP + "/app"))                      # run with its working folder there
        self.assertTrue(of_guard.scratch_write("Write", {"file_path": SP + "/x.py"}, "/w", [S]))
        self.assertTrue(of_guard.scratch_write("Edit", {"file_path": SP + "/x.py"}, "/w", [S]))

    def test_reads_and_deletions_stay_open_so_it_can_prune(self):
        for cmd in (f"du -sh {SP}/*", f"ls -la {SP}", f"rm -rf {SP}/wt", f"git worktree remove --force {SP}/wt",
                    f"git -C {SP}/wt status", f"cat {SP}/notes.md", f"grep -r x {SP}", f"find {SP} -name '*.log' -delete",
                    f"cp {SP}/result.json ~/proj/out.json", f"mv {SP}/keep.md ~/proj/", "git worktree prune",
                    f"sed -n 1,5p {SP}/f", f"of status working --note x", "echo done > /tmp/elsewhere",
                    f"ssh gw 'mkdir {SP}/x'", "cargo build", f". {SP}/env.sh && echo ok", f"source {SP}/env.sh",
                    f"gh pr create --title t --body-file {SP}/body.md", f"curl -X POST -d @{SP}/q.json https://h/api",
                    f"kubectl apply -f {SP}/m.yaml", f"tar czf /tmp/out.tgz -C {SP} wt", f"chmod +x {SP}/run.sh",
                    f"kubectl cp {SP}/x ns/p:/tmp/x", f"tar -tzf {SP}/a.tgz", "curl -s https://h/x"):
            self.assertIsNone(w(cmd), cmd)
        self.assertIsNone(w("rm -rf wt build", cwd=SP))
        self.assertIsNone(w("git status", cwd=SP + "/wt"))
        self.assertIsNone(of_guard.scratch_write("Read", {"file_path": SP + "/x.py"}, "/w", [S]))
        self.assertIsNone(of_guard.scratch_write("Write", {"file_path": "/Users/alex/proj/x.py"}, "/w", [S]))
        self.assertIsNone(w(f"echo x > {SP}/a.txt".replace(S, "/private/tmp/claude-501/-Users-x-proj/S2")))  # another


class ScratchHookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (of_events.STATE_DIR, of_hook.STATE, os.environ.get("HERDR_ENV"))
        of_events.STATE_DIR = of_hook.STATE = self.tmp.name
        os.environ.pop("HERDR_ENV", None)

    def tearDown(self):
        of_events.STATE_DIR, of_hook.STATE = self.saved[0], self.saved[1]
        if self.saved[2] is not None:
            os.environ["HERDR_ENV"] = self.saved[2]
        self.tmp.cleanup()

    def fire(self, sid, cmd):
        of_hook.OUTPUT = None
        old, sys.stdout = sys.stdout, io.StringIO()
        try:
            of_hook.handle("PreToolUse", {"hook_event_name": "PreToolUse", "session_id": sid, "cwd": "/Users/alex/proj",
                                          "tool_name": "Bash", "tool_input": {"command": cmd}})
            printed = sys.stdout.getvalue()
        finally:
            sys.stdout = old
        return (json.loads(printed) if printed.strip() else (of_hook.OUTPUT or {})).get("hookSpecificOutput") or {}

    def test_only_a_listed_session_is_refused_and_told_how_to_get_out(self):
        self.assertEqual(self.fire("S1", f"git worktree add {SP}/wt main"), {})       # no quota file: nobody listed
        with open(of_scratch.quota_path(), "w") as fh:
            json.dump({"sessions": {"S1": {"position": "of_x", "org": "o", "paths": [S], "bytes": 7 * of_scratch.GiB,
                                           "since": 0}}}, fh)
        out = self.fire("S1", f"git worktree add {SP}/wt main")
        self.assertEqual(out["permissionDecision"], "deny")
        for words in ("7.0 GiB", "over the 6.0 GiB quota", "under 3.0 GiB", "within 5 minutes", "G15"):
            self.assertIn(words, out["permissionDecisionReason"])
        self.assertNotIn("permissionDecision", self.fire("S1", f"rm -rf {SP}/wt"))   # pruning stays open
        self.assertNotIn("permissionDecision", self.fire("S2", f"git worktree add {SP}/wt main"))  # not listed


if __name__ == "__main__":
    unittest.main()
