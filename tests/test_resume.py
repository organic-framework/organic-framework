"""of resume-command: herdr's bare `claude --resume <id>` becomes the session's own account and mode (2026-11-10)."""

import json
import os
import shlex
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_capacity  # noqa: E402
import of_resume  # noqa: E402

SID = "00000000-0000-4000-8000-00000000a001"  # made up: no real session id in a published tree


class ResumeCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def copy(self, name, lines, age=0):
        d = os.path.join(self.home, name)
        os.makedirs(os.path.join(d, "projects", "-w"), exist_ok=True)
        t = os.path.join(d, "projects", "-w", f"{SID}.jsonl")
        with open(t, "w") as fh:
            fh.write("\n".join(json.dumps(x) for x in lines) + "\n")
        os.utime(t, (time.time() - age, time.time() - age))
        return d

    def test_the_newest_copy_its_mode_and_the_planned_effort(self):
        reply = {"type": "assistant", "message": {"model": "claude-opus-5-5", "content": []}}
        stale = self.copy(".claude", [reply], age=3600)  # left by a move: older
        own = self.copy(".claude-d", [{"permissionMode": "bypassPermissions"}, reply])
        plans = os.path.join(self.home, "reboot")
        os.makedirs(os.path.join(plans, "done-1"))
        with open(os.path.join(plans, "done-1", "plan.json"), "w") as fh:
            json.dump({"entries": [{"session": SID, "model": "claude-opus-5-5", "effort": "xhigh", "bypass": True}]}, fh)
        self.assertEqual(of_resume.newest_copy(SID, [stale, own])[0], own)
        deny = ["Read(//h/.config/acme/x.cfg)", "Edit(//h/.config/acme/x.cfg)"]
        argv = shlex.split(of_resume.command(SID, [stale, own], memory_dir="/m/acme_org", plan_root=plans, deny=deny,
                                             state_dir=self.home))
        self.assertEqual(argv[:5], ["env", f"CLAUDE_CONFIG_DIR={own}", "claude", "--resume", SID])
        with open(argv[argv.index("--settings") + 1]) as fh:  # a settings file, as of spawn writes it
            self.assertEqual(json.load(fh), {"autoMemoryDirectory": "/m/acme_org", "permissions": {"deny": deny}})
        self.assertEqual((argv[argv.index("--model") + 1], argv[argv.index("--effort") + 1]),
                         ("claude-opus-5-5", "xhigh"))
        self.assertEqual(argv[-1], of_capacity.BYPASS_FLAG)

    def test_the_organization_default_only_when_the_session_names_none(self):
        own = self.copy(".claude-d", [{"type": "user"}])  # a transcript with no model in it, and no plan
        argv = shlex.split(of_resume.command(SID, [own], plan_root=os.path.join(self.home, "none"),
                                             defaults={"model": "opus", "effort": "xhigh"}))
        self.assertEqual((argv[argv.index("--model") + 1], argv[argv.index("--effort") + 1]), ("opus", "xhigh"))

    def test_the_default_account_unsets_the_variable_and_an_unknown_session_gives_nothing(self):
        base = self.copy(".claude", [{"type": "user"}])
        orig = of_capacity.is_default
        of_capacity.is_default = lambda d, home=None: d == base
        try:
            argv = shlex.split(of_resume.command(SID, [base], plan_root=os.path.join(self.home, "none")))
        finally:
            of_capacity.is_default = orig
        self.assertEqual(argv, ["env", "-u", "CLAUDE_CONFIG_DIR", "claude", "--resume", SID])  # no mode: default
        self.assertIsNone(of_resume.command("00000000-0000-4000-8000-000000000000", [base]))


if __name__ == "__main__":
    unittest.main()
