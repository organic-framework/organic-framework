"""RFC 0008 §3.1: the limits of an outside run (bin/of_subagent.py), each with its test. The harness is a stand-in
here; the live checks that need the real Codex and Muse (the canaries, the network, the tools) are `of subagent
selftest`, run on the machine that has them."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_events  # noqa: E402
import of_subagent  # noqa: E402

REG = {"systems": {"organic-framework": {"repos": ["fw"]}},
       "subagent_harnesses": {"codex": {"kinds": ["review", "read", "research"], "systems": ["organic-framework"]},
                              "muse": {"kinds": ["review", "read"], "systems": ["organic-framework"]}}}
G = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]


class Fake:
    """A stand-in harness: records what it was started with, and writes the stream of a short run."""

    def __init__(self, harness, tools=()):
        self.harness, self.tools, self.calls = harness, tools, []

    def __call__(self, argv, stdin=None, stdout=None, stderr=None, env=None, cwd=None):
        self.calls.append({"argv": argv, "env": env, "cwd": cwd, "files": sorted(
            os.path.relpath(os.path.join(d, f), cwd) for d, _, fs in os.walk(cwd) for f in fs),
            "prompt": stdin.read().decode() if stdin else ""})
        if self.harness == "codex":
            lines = [{"type": "item.started", "item": {"type": t}} for t in self.tools or ["command_execution"]]
            lines.append({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 40,
                                                              "output_tokens": 7, "reasoning_output_tokens": 3}})
            with open(argv[argv.index("-o") + 1], "w") as fh:
                fh.write("no findings")
        else:
            lines = [{"payload_type": "task.lifecycle.proposed", "payload": {"event": {"task_kind": "tool." + t}}}
                     for t in self.tools or ["read_file"]]
            lines += [{"payload_type": "run.usage", "payload": {"models": [{"model": "m", "input_tokens": 50,
                                                                            "cached_tokens": 5, "output_tokens": 2}]}},
                      {"payload_type": "run.terminal.completed", "payload": {"text": "no findings"}}]
        stdout.write(("\n".join(json.dumps(x) for x in lines) + "\n").encode())
        return subprocess.CompletedProcess(argv, 0)


class SubagentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.saved = of_events.STATE_DIR
        of_events.STATE_DIR = os.path.join(t, "state")
        self.events = []
        self.patches = [mock.patch.object(of_events, "emit", lambda k, p, **kw: self.events.append((k, p))),
                        mock.patch.object(of_subagent, "scratch_root", lambda s: os.path.join(t, "scratch")),
                        mock.patch.object(of_subagent, "muse_binary", lambda *a: "/bin/false")]
        for p in self.patches:
            p.start()
        self.repo = os.path.join(t, "fw")
        os.makedirs(self.repo)
        git = lambda *a: subprocess.run(["git", "-C", self.repo, *G, *a], check=True, capture_output=True)  # noqa: E731
        git("init", "-q")
        with open(os.path.join(self.repo, "a.py"), "w") as fh:
            fh.write("print(1)\n")
        git("add", "a.py")
        git("commit", "-qm", "one")
        with open(os.path.join(self.repo, "a.py"), "a") as fh:
            fh.write("print(2)\n")
        git("commit", "-qam", "two")
        with open(os.path.join(self.repo, ".env"), "w") as fh:     # untracked, beside the checkout's files
            fh.write("TOKEN=x\n")
        self.git = git
        self.prompt = os.path.join(t, "prompt.md")
        with open(self.prompt, "w") as fh:
            fh.write("Review this.")

    def publish(self):
        """HEAD on the branch of a GitHub remote, as a pushed commit of a public repository is."""
        if "origin" not in self.git("remote").stdout.decode().split():
            self.git("remote", "add", "origin", "https://github.com/acme/fw.git")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")

    def tearDown(self):
        for p in self.patches:
            p.stop()
        of_events.STATE_DIR = self.saved
        self.tmp.cleanup()

    def go(self, harness="muse", kind="review", **kw):
        fake = kw.pop("fake", None) or Fake(harness)
        kw.setdefault("checkout", self.repo if kind != "research" else None)
        if kind != "research" and "diff" not in kw:
            kw.setdefault("at", "HEAD")
        if kw.get("visibility") and harness == "codex" and kind != "research":
            self.publish()
        got = of_subagent.run(harness, kind, self.prompt, "of_doer", session="S1", registry=kw.pop("registry", REG),
                              conn=None, popen=fake, visibility=kw.pop("visibility", lambda r: False),
                              log=lambda m: None, **kw)
        return got, fake.calls[-1] if fake.calls else None

    def test_confined_to_a_snapshot_of_tracked_files(self):
        got, call = self.go(diff="HEAD~1..HEAD")
        self.assertEqual(call["files"], [".of-review.diff", "a.py"])       # no .env: tracked files only
        self.assertNotEqual(os.path.realpath(call["cwd"]), os.path.realpath(self.repo))   # never the live checkout
        self.assertFalse(os.path.exists(call["cwd"]))                      # the copy is gone after the run
        self.assertEqual(got["answer"], "no findings")

    def test_a_secret_path_or_a_key_under_a_harmless_name_refuses_the_run(self):
        for name, body in ((".env.production", "A=1\n"), ("notes.txt", "-----BEGIN OPENSSH PRIVATE KEY-----\nx\n")):
            with self.subTest(name=name):
                with open(os.path.join(self.repo, name), "w") as fh:
                    fh.write(body)
                self.git("add", "-f", name)
                self.git("commit", "-qm", "add " + name)
                fake = Fake("muse")
                with self.assertRaises(of_subagent.SubagentError) as e:
                    self.go(fake=fake)
                self.assertIn(name, str(e.exception))
                self.assertEqual(fake.calls, [])                           # nothing started
                self.git("rm", "-q", name)
                self.git("commit", "-qm", "drop " + name)

    def test_read_only_flags_no_web_with_a_workspace_and_no_workspace_with_the_web(self):
        _, c = self.go("codex", visibility=lambda r: True)
        a = c["argv"]
        self.assertIn("--ignore-user-config", a)
        self.assertIn('web_search="disabled"', a)
        for f in ("apps", "plugins", "browser_use", "computer_use", "hooks", "multi_agent"):
            self.assertIn(f, [a[i + 1] for i, x in enumerate(a) if x == "--disable"])
        fs = next(x for x in a if x.startswith("permissions.of_snapshot.filesystem="))
        self.assertIn('"/Users"="none"', fs)
        self.assertIn('"/private/tmp"="none"', fs)
        self.assertNotIn("sandbox-exec", a[0])                             # Codex applies its own sandbox
        _, m = self.go("muse")
        self.assertEqual(m["argv"][:3], ["/usr/bin/sandbox-exec", "-f", m["argv"][2]])
        for f in ("--disable-write", "--disable-shell", "--disable-web-tools", "--no-foreign-personal-context",
                  "--disable-request-user-input"):
            self.assertIn(f, m["argv"])
        _, r = self.go("codex", kind="research")
        self.assertEqual(r["files"], [])                                   # research: an empty folder, no snapshot
        self.assertIn('web_search="live"', r["argv"])
        with self.assertRaises(of_subagent.SubagentError):
            of_subagent.run("codex", "research", self.prompt, "of_doer", checkout=self.repo, at="HEAD", registry=REG)

    def test_the_outer_sandbox_denies_reads_outside_the_run(self):
        prof = of_subagent.sandbox_profile("/private/tmp/run1", reads=["/Applications"], writes=[])
        self.assertIn('(deny file-read-data (subpath "/Users") (subpath "/private/tmp")', prof)
        self.assertIn('(allow file-read-data (subpath "/private/tmp/run1")', prof)
        self.assertLess(prof.index("(deny file-read-data"), prof.index("(allow file-read-data"))   # the later wins

    def test_no_training_on_the_run(self):
        _, m = self.go("muse")
        self.assertEqual(m["argv"][m["argv"].index("--contributor-mode") + 1], "off")
        with self.assertRaises(of_subagent.SubagentError) as e:            # not public, no training reading
            self.go("codex")
        self.assertIn("training reading is missing", str(e.exception))
        self.assertIsNone(of_subagent.training_gate("codex", "review", "off", "private"))
        self.assertIn("training reading is on", of_subagent.training_gate("codex", "review", "on", "private"))
        self.assertIsNone(of_subagent.training_gate("codex", "research", None, "private"))

    def test_public_material_is_each_commit_on_a_public_remote_branch(self):
        self.git("remote", "add", "origin", "https://github.com/acme/fw.git")
        head = self.git("rev-parse", "HEAD").stdout.decode().strip()
        self.assertIn("is on no branch", of_subagent.public_material(self.repo, [head], lambda r: True))
        self.git("update-ref", "refs/remotes/origin/main", head)
        self.assertIsNone(of_subagent.public_material(self.repo, [head], lambda r: r == "acme/fw"))
        self.assertIn("no public GitHub remote", of_subagent.public_material(self.repo, [head], lambda r: False))
        got, _ = self.go("codex", visibility=lambda r: True)
        self.assertEqual(got["exit"], 0)

    def test_a_clean_environment(self):
        with mock.patch.dict(os.environ, {"SSH_AUTH_SOCK": "/tmp/agent", "HERDR_PANE_ID": "p1", "GITHUB_TOKEN": "t",
                                          "OP_SERVICE_ACCOUNT_TOKEN": "o"}):
            for h in ("codex", "muse"):
                _, c = self.go(h, visibility=lambda r: True)
                env = c["env"]
                self.assertFalse({"SSH_AUTH_SOCK", "HERDR_PANE_ID", "GITHUB_TOKEN", "OP_SERVICE_ACCOUNT_TOKEN"} & set(env))
                self.assertEqual(env["OF_AUTOMATION"], "1")
                self.assertNotEqual(env["HOME"], os.path.expanduser("~"))    # a fresh home: no owner's skills
                self.assertTrue(env["HOME"].startswith(os.path.join(self.tmp.name, "scratch")))

    def test_recorded_with_its_spend_and_its_tools(self):
        got, _ = self.go("codex", fake=Fake("codex", ["command_execution", "web_search"]), visibility=lambda r: True,
                         work="w1")
        started, ended = [p for k, p in self.events if k == "subagent_run"]
        self.assertEqual((started["phase"], ended["phase"]), ("started", "ended"))
        self.assertEqual((ended["position"], ended["session"], ended["work"], ended["harness"], ended["kind"]),
                         ("of_doer", "S1", "w1", "codex", "review"))
        self.assertEqual(ended["usage"], {"input": 100, "cached": 40, "output": 7, "reasoning": 3})
        self.assertEqual(ended["unexpected_tools"], ["web_search"])        # the web, on a review: named
        self.assertEqual(len(ended["prompt_sha256"]), 64)
        rec = ended["record"]
        self.assertEqual(oct(os.stat(rec).st_mode & 0o777), "0o700")
        for f in ("prompt.md", "stream.jsonl", "answer.txt"):
            self.assertEqual(oct(os.stat(os.path.join(rec, f)).st_mode & 0o777), "0o600")
        with open(os.path.join(rec, "prompt.md")) as fh:
            self.assertEqual(fh.read(), "Review this.")                   # the position's prompt, as given

    def test_the_organization_chooses(self):
        with self.assertRaises(of_subagent.SubagentError) as e:
            self.go(registry={"systems": REG["systems"]})
        self.assertIn("allows no outside harness", str(e.exception))
        self.assertIn("not research", of_subagent.allowance(REG, "muse", "research"))
        self.assertIn("not 'other'", of_subagent.allowance(REG, "codex", "read", "other"))
        reg = dict(REG, systems={"organic-framework": {"repos": ["elsewhere"]}})
        with self.assertRaises(of_subagent.SubagentError) as e:
            self.go(registry=reg)
        self.assertIn("the repository of no system", str(e.exception))
        self.assertEqual(of_subagent.pick(REG, "review", "organic-framework"), "codex")
        self.assertEqual(of_subagent.pick(REG, "review", "organic-framework", {"codex": (100, 1.0)}), "muse")
        self.assertIsNone(of_subagent.pick({}, "review"))


if __name__ == "__main__":
    unittest.main()
