"""Carrying sessions between machines (bin/of_carry.py; agent host plan §3 B1): the path rewrite, the bundle, the
copy's commands and the restore, between two homes in a temporary folder and back."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import of_capacity  # noqa: E402
import of_carry  # noqa: E402

SID = "5e551011-0000-4000-8000-000000000001"
POSITIONS = [{"name": "acme_a", "_org": "acme"}]  # the target's registry


def read(p):
    with open(p) as fh:
        return fh.read()


def line(rec):
    return json.dumps(rec, ensure_ascii=False, separators=(",", ":"))


class Paths(unittest.TestCase):
    def test_the_home_its_folder_keys_and_the_tmp_root(self):
        rw = of_carry.Rewriter("/Users/alex", "/Users/lab", 501, 502)
        self.assertEqual(of_carry.key_of("/Users/alex/x/.claude/worktrees/y"), "-Users-alex-x--claude-worktrees-y")
        self.assertEqual(rw.path("/Users/alex/.claude-a/projects/-Users-alex-org/s.jsonl"),
                         "/Users/lab/.claude-a/projects/-Users-lab-org/s.jsonl")
        self.assertEqual(rw.path("/private/tmp/claude-501/-Users-alex-org/s/scratchpad"),
                         "/private/tmp/claude-502/-Users-lab-org/s/scratchpad")
        self.assertEqual(rw.path("-private-tmp-claude-501--Users-alex-org"), "-private-tmp-claude-502--Users-lab-org")
        self.assertEqual(rw.text("cd /Users/alex && ls '/Users/alex'"), "cd /Users/lab && ls '/Users/lab'")
        for same in ("/Users/alexander", "-Users-alexander", "/home/runner/x", "x-Users-alex-y"):
            self.assertEqual(rw.path(same), same)

    def test_a_replacement_is_never_rewritten_again(self):
        """A rehearsal home inside the tmp root holds the old key in its own path: one pass keeps it whole."""
        new = "/private/tmp/claude-501/-Users-alex-org/s1/scratchpad/home-of"
        rw = of_carry.Rewriter("/Users/alex", new, 501, 501)
        self.assertEqual(rw.path("/Users/alex/.claude-a/projects/-Users-alex-org/s.jsonl"),
                         f"{new}/.claude-a/projects/{of_carry.key_of(new)}-org/s.jsonl")

    def test_the_conversation_is_never_rewritten(self):
        rw = of_carry.Rewriter("/Users/alex", "/Users/lab", 501, 501)
        rec = {"type": "assistant", "cwd": "/Users/alex/org",
               "message": {"content": [{"type": "thinking", "thinking": "in /Users/alex/org", "signature": "sig"},
                                       {"type": "text", "text": "/Users/alex/org/a.py"}]},
               "toolUseResult": {"filePath": "/Users/alex/org/a.py"},
               "snapshot": {"trackedFileBackups": {"/Users/alex/org/a.py": {"v": 1}}}}
        out = rw.record(rec)
        self.assertEqual(out["message"], rec["message"])  # signed thinking: an edit there breaks the session
        self.assertEqual(out["cwd"], "/Users/lab/org")
        self.assertEqual(out["toolUseResult"]["filePath"], "/Users/lab/org/a.py")
        self.assertIn("/Users/lab/org/a.py", out["snapshot"]["trackedFileBackups"])
        untouched = line({"type": "user", "cwd": "/srv/x", "message": {"content": "hi"}}).encode()
        self.assertEqual(rw.transcript(untouched), untouched)  # a line it does not touch stays byte for byte

    def test_a_string_cut_inside_an_emoji_is_written_escaped(self):
        """of_review on PR 116: JSON.stringify writes a lone surrogate as \\ud83d; written back it raised."""
        rw = of_carry.Rewriter("/Users/alex", "/Users/lab", 501, 501)
        raw = b'{"cwd":"/Users/alex/org","toolUseResult":{"stdout":"cut \\ud83d"},"message":{"content":"x"}}'
        out = json.loads(rw.transcript(raw))
        self.assertEqual((out["cwd"], out["toolUseResult"]["stdout"]), ("/Users/lab/org", "cut \ud83d"))


class CopyCommands(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.key = os.path.join(self.d, "k")
        with open(self.key, "w") as fh:
            fh.write("x")
        os.chmod(self.key, 0o600)

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_push_and_pull_run_in_the_background_with_only_the_key(self):
        push = of_carry.copy_commands("/b", self.key, to="of@host:/c/b", bwlimit=1000)
        self.assertEqual([c[:3] for c in push], [["taskpolicy", "-b", "rsync"]] * 2)
        ssh = push[0][push[0].index("-e") + 1]
        for opt in ("-i " + self.key, "IdentityAgent=none", "IdentitiesOnly=yes", "BatchMode=yes"):
            self.assertIn(opt, ssh)
        self.assertTrue(all("--chmod=go-rwx" in c for c in push))  # nothing readable by another user on the host
        spaced = os.path.join(self.d, "a key")
        shutil.copy2(self.key, spaced)
        c = of_carry.copy_commands("/b", spaced, to="h:/c")[0]
        self.assertIn("-i '" + spaced + "'", c[c.index("-e") + 1])  # a path with a space stays one word
        self.assertIn("--bwlimit=1000", push[1])
        self.assertEqual(push[1][-3:], ["--files-from=/b/files.txt", "/", "of@host:/c/b/files/"])
        pull = of_carry.copy_commands("/b", self.key, frm="of@host:/c/b")
        self.assertEqual(pull[-1][-3:], ["--files-from=/b/files.txt", "of@host:/", "/b/files/"])
        with self.assertRaises(of_carry.CarryError):
            of_carry.copy_commands("/b", self.key, to="a:/x", frm="b:/y")

    def test_no_agent_and_no_loose_key(self):
        envs = []

        def run(c, env=None):
            envs.append(env)
            return mock.Mock(returncode=0)
        of_carry.copy(os.path.join(self.d, "b"), self.key, to="of@host:/c/b", run=run, log=lambda *_: None)
        self.assertTrue(envs and all(e["SSH_AUTH_SOCK"] == "" for e in envs))
        with self.assertRaises(of_carry.CarryError):
            of_carry.copy(os.path.join(self.d, "b"), None, to="of@host:/c/b", run=run, log=lambda *_: None)
        os.chmod(self.key, 0o644)
        with self.assertRaises(of_carry.CarryError):
            of_carry.copy(os.path.join(self.d, "b"), self.key, to="of@host:/c/b", run=run, log=lambda *_: None)


class Carry(unittest.TestCase):
    """A session prepared in home A, copied, restored in home B, and carried back to A."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.a, self.b = os.path.join(self.root, "A"), os.path.join(self.root, "B")
        self.proj = os.path.join(self.a, "org")
        os.makedirs(self.proj)
        os.makedirs(os.path.join(self.b, "org"))
        self.cfg = os.path.join(self.a, ".claude-x")
        key = of_carry.key_of(self.proj)
        pdir = os.path.join(self.cfg, "projects", key)
        os.makedirs(os.path.join(pdir, SID, "subagents"))
        os.makedirs(os.path.join(pdir, "memory"))
        os.makedirs(os.path.join(self.cfg, "file-history", SID))
        self.lines = [
            line({"type": "user", "cwd": self.proj, "sessionId": SID, "message": {"content": "start"}}),
            line({"type": "assistant", "cwd": self.proj, "message": {"content": [
                {"type": "thinking", "thinking": f"look in {self.proj}", "signature": "s1"}]}}),
            line({"type": "summary", "summary": "no path here"})]
        self.transcript = os.path.join(pdir, f"{SID}.jsonl")
        with open(self.transcript, "w") as fh:
            fh.write("\n".join(self.lines) + "\n")
        with open(os.path.join(pdir, SID, "subagents", "agent-1.jsonl"), "w") as fh:
            fh.write(line({"type": "user", "cwd": self.proj, "message": {"content": "sub"}}) + "\n")
        with open(os.path.join(pdir, "memory", "MEMORY.md"), "w") as fh:
            fh.write("- folder memory\n")
        with open(os.path.join(self.cfg, "file-history", SID, "abc@v1"), "w") as fh:
            fh.write("old file\n")
        self.mem = os.path.join(self.a, ".local", "state", "of", "memory", "acme", "acme_a")
        os.makedirs(self.mem)
        with open(os.path.join(self.mem, "MEMORY.md"), "w") as fh:
            fh.write("- position memory\n")
        self.tmp = os.path.join(self.a, "tmp")
        os.makedirs(os.path.join(self.tmp, key, SID, "scratchpad"))
        with open(os.path.join(self.tmp, key, SID, "scratchpad", "notes.txt"), "w") as fh:
            fh.write("scratch\n")
        for home in (self.a, self.b):
            os.makedirs(os.path.join(home, ".claude-x"), exist_ok=True)
            with open(os.path.join(home, ".claude-x", ".claude.json"), "w") as fh:
                json.dump({"oauthAccount": {"emailAddress": "acct@x"}}, fh)
        self.entry = {"name": "acme_a", "position": "acme_a", "org": "acme", "session": SID, "config_dir": self.cfg,
                      "cwd": self.proj, "pane": "w3:p9", "workspace": "acme", "tab": "a", "model": "claude-opus-5-5",
                      "effort": "high", "bypass": True, "memory_dir": self.mem, "crons": [], "tasks": [],
                      "running": [], "hold": False, "status": "idle"}
        self.other = dict(self.entry, name="acme_b", position="acme_b", org="other", session="x")

    def tearDown(self):
        shutil.rmtree(self.root)

    def prepare(self, home, entries, bundle):
        with mock.patch.object(of_carry.os.path, "expanduser", lambda p: home if p == "~" else p):
            return of_carry.prepare([], {"acme"}, bundle, log=lambda *_: None, entries=entries, tmp_root=self.tmp
                                    if home == self.a else os.path.join(self.b, "tmp"))

    def ship(self, src_bundle, dst_bundle):
        """What `copy` does over SSH: the plan, the list, and every listed file under files/<its path>."""
        os.makedirs(os.path.join(dst_bundle, "files"), exist_ok=True)
        for f in ("plan.json", "files.txt"):
            shutil.copy2(os.path.join(src_bundle, f), dst_bundle)
        for p in read(os.path.join(src_bundle, "files.txt")).split():
            t = os.path.join(dst_bundle, "files", p.lstrip("/"))
            os.makedirs(os.path.dirname(t), exist_ok=True)
            shutil.copy2(p, t)

    def restore(self, bundle, home, only=None):
        calls = []

        def restore_fn(positions, only=None, plan_path=None, **k):
            calls.append((only, plan_path))
            return [(n, "resumed", {}) for n in sorted(only)]

        def account(d):
            try:
                return json.loads(read(os.path.join(d, ".claude.json")))["oauthAccount"]["emailAddress"]
            except OSError:
                return None
        done = of_carry.restore(bundle, POSITIONS, "acme", only=only, log=lambda *_: None, home=home, uid=os.getuid(),
                                account_of=account, restore_fn=restore_fn)
        return done, calls

    def test_there_and_back(self):
        with mock.patch.object(of_capacity, "account_of", lambda d, home=None: "acct@x"):
            plan = self.prepare(self.a, [self.entry, self.other], os.path.join(self.root, "out"))
        self.assertEqual([e["name"] for e in plan["entries"]], ["acme_a"])  # one organization per bundle
        listed = read(os.path.join(self.root, "out", "files.txt")).split()
        for f in (self.transcript, os.path.join(self.mem, "MEMORY.md")):
            self.assertIn(f, listed)
        self.assertTrue(any(p.endswith("scratchpad/notes.txt") for p in listed))
        self.assertTrue(any(p.endswith("subagents/agent-1.jsonl") for p in listed))
        self.assertFalse(any("/projects/" in p and "/memory/" in p for p in listed))  # a folder's memory stays

        there = os.path.join(self.root, "on-b")
        self.ship(os.path.join(self.root, "out"), there)
        newkey = of_carry.key_of(os.path.join(self.b, "org"))
        clash = os.path.join(self.b, ".local", "state", "of", "memory", "acme", "acme_a", "MEMORY.md")
        os.makedirs(os.path.dirname(clash))
        with open(clash, "w") as fh:
            fh.write("- B's own\n")
        done, calls = self.restore(there, self.b)
        self.assertEqual(done, [("acme_a", "resumed", {})])
        moved = os.path.join(self.b, ".claude-x", "projects", newkey, f"{SID}.jsonl")
        recs = [json.loads(x) for x in read(moved).split("\n") if x.strip()]
        self.assertEqual(recs[0]["cwd"], os.path.join(self.b, "org"))
        self.assertEqual(recs[1]["message"]["content"][0]["thinking"], f"look in {self.proj}")  # the old home, kept
        self.assertTrue(os.path.exists(os.path.join(self.b, ".local", "state", "of", "memory", "acme", "acme_a",
                                                    "MEMORY.md")))
        self.assertTrue(os.path.exists(os.path.join(there, "kept", clash.lstrip("/"))))  # B's file kept aside
        lp = json.loads(read(calls[0][1]))
        e = lp["entries"][0]
        self.assertEqual((e["config_dir"], e["cwd"], e["pane"]), (os.path.join(self.b, ".claude-x"),
                                                                   os.path.join(self.b, "org"), None))
        self.assertEqual((lp["kind"], lp["target_home"], calls[0][0]), ("carry", self.b, {"acme_a"}))

        # and back: what B prepares from the restored session comes back to A as it was
        os.remove(self.transcript)
        e_b = dict(e, account="acct@x")
        with mock.patch.object(of_capacity, "account_of", lambda d, home=None: "acct@x"):
            self.prepare(self.b, [e_b], os.path.join(self.root, "out-b"))
        back = os.path.join(self.root, "on-a")
        self.ship(os.path.join(self.root, "out-b"), back)
        self.restore(back, self.a)
        self.assertEqual(read(self.transcript).split("\n")[:3], self.lines)

    def test_secret_paths_stay_behind_unread_and_the_bundle_is_private(self):
        """of_server on PR 116: a kubeconfig or an .env in a scratch folder never travels as a side effect."""
        scratch = os.path.join(self.tmp, of_carry.key_of(self.proj), SID, "scratchpad")
        planted = [os.path.join(scratch, "kubeconfig"), os.path.join(scratch, "app", ".env")]
        for p in planted:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write("planted\n")
        out = os.path.join(self.root, "out")
        with mock.patch.object(of_capacity, "account_of", lambda d, home=None: "acct@x"):
            plan = self.prepare(self.a, [self.entry], out)
        listed = read(os.path.join(out, "files.txt")).split()
        for p in planted:
            self.assertNotIn(p, listed)
            self.assertIn({"path": p, "reason": "secret path"}, plan["skipped"])
        self.assertIn(self.transcript, listed)
        for f in ("plan.json", "files.txt"):
            self.assertEqual(os.stat(os.path.join(out, f)).st_mode & 0o777, 0o600)
        there = os.path.join(self.root, "on-b")
        self.ship(out, there)
        link = os.path.join(there, "files", self.b.lstrip("/"), "org", "linked")
        os.makedirs(os.path.dirname(link), exist_ok=True)
        os.symlink("/etc/hosts", link)
        self.restore(there, self.b)
        self.assertFalse(os.path.lexists(os.path.join(self.b, "org", "linked")))  # a link in a bundle is not followed
        moved = os.path.join(self.b, ".claude-x", "projects", of_carry.key_of(os.path.join(self.b, "org")), f"{SID}.jsonl")
        self.assertEqual(os.stat(moved).st_mode & 0o077, 0)

    def test_only_what_the_plan_accounts_for_is_put_in_place(self):
        """of_review on PR 116: a bundle's shell profile, LaunchAgent, Claude settings and guard cache all landed."""
        with mock.patch.object(of_capacity, "account_of", lambda d, home=None: "acct@x"):
            self.prepare(self.a, [self.entry], os.path.join(self.root, "out"))
        there = os.path.join(self.root, "on-b")
        self.ship(os.path.join(self.root, "out"), there)
        strays = [".zshrc", "Library/LaunchAgents/x.plist", ".claude-x/settings.json",
                  ".local/state/of/guard-credentials.json", ".claude-x/projects/-k/other.jsonl"]
        for rel in strays:
            p = os.path.join(there, "files", self.a.lstrip("/"), rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as fh:
                fh.write("stray\n")
        logged = []
        of_carry.restore(there, POSITIONS, "acme", log=logged.append, home=self.b, uid=os.getuid(),
                         account_of=lambda d: "acct@x", restore_fn=lambda *a, **k: [])
        for rel in strays:
            self.assertFalse(os.path.exists(os.path.join(self.b, rel)), rel)
        self.assertTrue(any("NOT put in place" in x for x in logged))
        newkey = of_carry.key_of(os.path.join(self.b, "org"))
        self.assertTrue(os.path.exists(os.path.join(self.b, ".claude-x", "projects", newkey, f"{SID}.jsonl")))
        self.assertTrue(os.path.exists(os.path.join(self.b, ".local", "state", "of", "memory", "acme", "acme_a",
                                                    "MEMORY.md")))

    def test_a_crafted_entry_writes_no_other_memory(self):
        """of_review on PR 116 (comment 6864023728): one crafted entry wrote MEMORY.md into another organization's
        position memory and into another project's folder memory."""
        with mock.patch.object(of_capacity, "account_of", lambda d, home=None: "acct@x"):
            self.prepare(self.a, [self.entry], os.path.join(self.root, "out"))
        there = os.path.join(self.root, "on-b")
        self.ship(os.path.join(self.root, "out"), there)
        plan = json.loads(read(os.path.join(there, "plan.json")))
        sid2 = "5e551011-0000-4000-8000-000000000002"
        other_mem = os.path.join(self.a, ".local", "state", "of", "memory", "theirs", "their_conductor")
        other_key = of_carry.key_of(os.path.join(self.a, "their-checkout"))
        plan["entries"].append(dict(self.entry, name="acme_a", session=sid2, memory_dir=other_mem))
        with open(os.path.join(there, "plan.json"), "w") as fh:
            json.dump(plan, fh)
        files = {os.path.join(other_mem, "MEMORY.md"): "- planted\n",
                 os.path.join(self.cfg, "projects", other_key, f"{sid2}.jsonl"): line({"type": "user"}) + "\n",
                 os.path.join(self.cfg, "projects", other_key, "memory", "MEMORY.md"): "- planted\n"}
        for p, text in files.items():
            t = os.path.join(there, "files", p.lstrip("/"))
            os.makedirs(os.path.dirname(t), exist_ok=True)
            with open(t, "w") as fh:
                fh.write(text)
        self.restore(there, self.b)
        self.assertFalse(os.path.exists(os.path.join(self.b, ".local", "state", "of", "memory", "theirs")))
        self.assertFalse(os.path.exists(os.path.join(self.b, ".claude-x", "projects",
                                                     of_carry.key_of(os.path.join(self.b, "their-checkout")), "memory")))
        with self.assertRaises(of_carry.CarryError):  # a bundle of another organization than the one named
            of_carry.restore(there, POSITIONS, "theirs", log=lambda *_: None, home=self.b, uid=os.getuid(),
                             account_of=lambda d: "acct@x", restore_fn=lambda *a, **k: [])

    def test_one_organization_per_bundle(self):
        with mock.patch.object(of_capacity, "account_of", lambda d, home=None: "acct@x"), \
                self.assertRaises(of_carry.CarryError):
            of_carry.prepare([], {"acme_a", "acme_b"}, os.path.join(self.root, "out"), log=lambda *_: None,
                             entries=[self.entry, self.other], tmp_root=self.tmp)

    def test_restore_only_puts_in_place_the_named_sessions(self):
        with mock.patch.object(of_capacity, "account_of", lambda d, home=None: "acct@x"):
            self.prepare(self.a, [self.entry], os.path.join(self.root, "out"))
        there = os.path.join(self.root, "on-b")
        self.ship(os.path.join(self.root, "out"), there)
        of_carry.restore(there, POSITIONS, "acme", only={"someone_else"}, log=lambda *_: None, home=self.b,
                         uid=os.getuid(),
                         account_of=lambda d: "acct@x", restore_fn=lambda *a, **k: [])
        self.assertFalse(os.path.exists(os.path.join(self.b, ".claude-x", "projects")))

    def test_another_account_is_not_resumed_and_nothing_lands_outside_the_home(self):
        with mock.patch.object(of_capacity, "account_of", lambda d, home=None: "acct@x"):
            self.prepare(self.a, [self.entry], os.path.join(self.root, "out"))
        there = os.path.join(self.root, "on-b")
        self.ship(os.path.join(self.root, "out"), there)
        stray = os.path.join(there, "files", "etc", "stray.conf")
        os.makedirs(os.path.dirname(stray))
        with open(stray, "w") as fh:
            fh.write("x")
        with open(os.path.join(self.b, ".claude-x", ".claude.json"), "w") as fh:
            json.dump({"oauthAccount": {"emailAddress": "someone-else@x"}}, fh)
        done, calls = self.restore(there, self.b)
        self.assertEqual(done, [("acme_a", "failed", "another account")])
        self.assertEqual(calls, [])
        self.assertFalse(os.path.exists("/etc/stray.conf"))


class CarryNeverStopsALiveSession(unittest.TestCase):
    def test_a_live_copy_of_a_carried_session_is_left_alone(self):
        d = tempfile.mkdtemp()
        try:
            path = os.path.join(d, "plan.local.json")
            with open(path, "w") as fh:
                json.dump({"kind": "carry", "prepared_at": "2026-11-13T19:00:00Z", "entries": [
                    {"name": "acme_a", "position": "acme_a", "session": SID, "config_dir": "/c/x"}]}, fh)
            kills = []
            with mock.patch.object(of_capacity, "config_dirs", lambda *a: ["/c/other"]), \
                    mock.patch.object(of_capacity, "live_sessions",
                                      lambda d, *a: [{"session": SID, "pid": 7, "started": 0}]), \
                    mock.patch.object(of_capacity.os, "kill", lambda *a: kills.append(a)), \
                    mock.patch.object(of_capacity.of_events, "emit", lambda *a, **k: None):
                done = of_capacity.reboot_restore([], plan_path=path, log=lambda *_: None)
            self.assertEqual(done, [("acme_a", "running", None)])
            self.assertEqual(kills, [])
            self.assertTrue(os.path.exists(path))  # a carry bundle is not the restart's own plan: never moved
        finally:
            shutil.rmtree(d)


class CLI(unittest.TestCase):
    def test_carry_with_no_step_prints_its_usage(self):
        import subprocess
        of = os.path.join(os.path.dirname(__file__), "..", "bin", "of")
        r = subprocess.run([sys.executable, of, "capacity", "carry"], capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("usage: of capacity carry prepare --only", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
