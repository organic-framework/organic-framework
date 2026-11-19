"""docs/guard.md G33 and G35: the shell's write forms into orgs.yaml and the asks outbox (of_review's probes on
organic-framework PR 395, organic-framework-build reports/review/probes/write_forms.py, msg 01a1f529-2656). Each form
is judged by critical_deny on a temporary HOME (tests/isolate.py); nothing runs."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_guard  # noqa: E402


class WriteForms(unittest.TestCase):

    def setUp(self):
        # a HOME of its own, whatever runs the file: run alone as a script, tests/isolate.py does not set one, and
        # setUp would write into the real outbox and orgs.yaml (of_review on organic-framework PR 399)
        self.saved_home = os.environ.get("HOME")
        os.environ["HOME"] = os.path.realpath(tempfile.mkdtemp())
        self.home = os.path.expanduser("~")
        assert self.home != self.saved_home
        self.box = os.path.join(self.home, ".local/state/of/asks-outbox")
        os.makedirs(self.box, exist_ok=True)
        open(os.path.join(self.box, "20991231T000000-abcd.json"), "w").write("{}")
        os.makedirs(os.path.join(self.home, ".config/of"), exist_ok=True)
        open(os.path.join(self.home, ".config/of/orgs.yaml"), "w").write("orgs: []\n")
        self.work = os.path.realpath(tempfile.mkdtemp())
        link = os.path.join(self.work, "ob")
        if not os.path.lexists(link):
            os.symlink(self.box, link)
        self.saved = os.environ.get("OF_STATE")

    def tearDown(self):
        if self.saved_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.saved_home
        if self.saved is None:
            os.environ.pop("OF_STATE", None)
        else:
            os.environ["OF_STATE"] = self.saved

    def deny(self, cmd):
        return of_guard.critical_deny("Bash", {"command": cmd}, cwd=self.work)

    def test_into_the_outbox(self):
        B, box = "~/.local/state/of/asks-outbox", self.box
        for cmd in (f"ln -s {B} /private/tmp/obx && echo x > /private/tmp/obx/a.json", f"echo x >| {B}/a.json",
                    f"exec 3> {B}/a.json", f"cp -t {B} a.json", f"curl -s -o {B}/a.json file:///tmp/x",
                    f"wget -qO {B}/a.json https://example.com/x", f"find . -name a.json -exec cp {{}} {B}/ ';'",
                    f"sed -n 'w {B}/a.json' a.json", f"awk '{{print > \"{box}/a.json\"}}' a.json",
                    f"python3 -c \"open('{box}/a.json','w').write('x')\"", f"perl -e 'open(F, \">{box}/a.json\"); print F 1'",
                    f"node -e \"require('fs').writeFileSync('{box}/a.json','x')\"", f"yq -i '.a=1' {B}/a.json",
                    f"git --work-tree={B} checkout HEAD -- a.json", f"rm {B}/2*.json", f"echo x > {B}/[2]*.json",
                    f"D={B}; echo x > $D/a.json"):
            self.assertTrue(self.deny(cmd), cmd)
        for cmd in (f"cat {B}/a.json", f"ls {B}", "echo x > ~/.local/state/of/notes.txt"):
            self.assertIsNone(self.deny(cmd), cmd)

    def test_into_orgs_yaml(self):
        O = "~/.config/of/orgs.yaml"
        for cmd in (f"echo x >| {O}", f"exec 3> {O}", f"F={O}; echo x > $F", "cp -t ~/.config/of orgs.yaml",
                    f"curl -s -o {O} file:///tmp/x", f"sed -n 'w {O}' a",
                    f"awk '{{print > \"{self.home}/.config/of/orgs.yaml\"}}' a", f"find . -name x -exec cp {{}} {O} ';'",
                    "ln -s ~/.config/of /private/tmp/cfx && echo x > /private/tmp/cfx/orgs.yaml"):
            self.assertTrue(self.deny(cmd), cmd)
        self.assertIsNone(self.deny(f"cat {O}"))

    def test_the_outbox_follows_of_state(self):
        alt = os.path.realpath(tempfile.mkdtemp())
        os.environ["OF_STATE"] = alt
        self.assertTrue(of_guard.critical_deny("Write", {"file_path": os.path.join(alt, "asks-outbox", "a.json"),
                                                         "content": "{}"}, cwd=self.work))
        self.assertTrue(self.deny(f"echo x > {alt}/asks-outbox/a.json"))


if __name__ == "__main__":
    unittest.main()
