"""docs/guard.md G33: OF's own configuration is the owner's (of_review on organic-framework PR 345, through of_herdr:
views.yaml's panels ran as the person; the gap "OF's own configuration is written by any agent": orgs.yaml decides which
registries the guard reads). Nothing here writes either file: the guard judges a path by name."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_guard  # noqa: E402

HOME = os.path.realpath(os.path.expanduser("~"))


def bash(cmd):
    return of_guard.critical_deny("Bash", {"command": cmd}, cwd=HOME)


class OwnerConfigTest(unittest.TestCase):

    def test_it_denies_and_names_the_commands(self):
        self.assertEqual(of_guard.OWNER_CONFIG_MODE, "deny")
        got = bash("sed -i '' 's/hold: true/hold: false/' ~/.config/of/orgs.yaml")  # the replay's live flow
        self.assertTrue(got and "G33" in got[0])
        for named in ("of org set <org> hold on|off", "of view add|remove", "of init", "Edit tool"):
            self.assertIn(named, got[0])

    def test_the_remedies_it_names_pass(self):
        self.assertIsNone(bash("of org set acme hold off"))
        self.assertIsNone(bash("of org show acme"))
        self.assertIsNone(of_guard.critical_deny(  # a help text that names the file, changed with the Edit tool
            "Edit", {"file_path": os.path.join(HOME, "organic-framework", "bin", "of"), "old_string": "a",
                     "new_string": "listed in ~/.config/of/orgs.yaml"}, cwd=HOME))
        self.assertIsNone(bash("mkdir -p /tmp/t1/.config/of && printf 'orgs: []\\n' > /tmp/t1/.config/of/orgs.yaml"))

    def test_writing_it_is_denied(self):
        for name in ("orgs.yaml", "views.yaml"):
            p = f"~/.config/of/{name}"
            for cmd in (f"printf 'x' >> {p}", f"echo x > {p}", f"cp /tmp/x {p}", f"mv /tmp/x {p}", f"rm {p}",
                        f"ln -sf /tmp/x {p}", f"tee {p} < /tmp/x", f"sed -i '' s/a/b/ {p}",
                        f'printf x >> "$HOME/.config/of/{name}"'):
                got = bash(cmd)
                self.assertTrue(got and "G33" in got[0], cmd)
            for tool in ("Write", "Edit"):
                got = of_guard.critical_deny(tool, {"file_path": os.path.join(HOME, ".config", "of", name),
                                                    "content": "x", "old_string": "a", "new_string": "b"}, cwd=HOME)
                self.assertTrue(got and "G33" in got[0], (tool, name))

    def test_reading_it_and_ofs_own_commands_pass(self):
        for cmd in ("cat ~/.config/of/views.yaml", "grep -n panels ~/.config/of/views.yaml",
                    "of view add main --workspace w --tab t --positions a,b --panel questions", "of view apply main",
                    "printf x > ~/.config/of/notes.txt", "ssh host 'echo x > ~/.config/of/views.yaml'"):
            self.assertIsNone(bash(cmd), cmd)


class OwnerConfigFormsTest(unittest.TestCase):
    """G33's forms from PR 387's reviews (of_secrets-review's comment 7017458194, point 2; of_review's comment
    7017518350): a bare redirection, a copy into the folder, a body run after cd, find -delete, yq -i, and writes from
    interpreter code. Nothing runs: the guard judges the text."""

    def test_the_writes_are_denied(self):
        o = "~/.config/of/orgs.yaml"
        for cmd in (f">> {o}", "cp /tmp/orgs.yaml ~/.config/of/", "cd ~/.config/of && sh -c 'true >> views.yaml'",
                    "find ~/.config/of/views.yaml -delete", f"yq -i '.orgs[0].hold = true' {o}",
                    "python3 -c \"import os; open(os.path.expanduser('~/.config/of/orgs.yaml'),'a').write('x')\"",
                    "python3 - <<'PY'\nimport os\nopen(os.path.expanduser('~/.config/of/views.yaml'), 'w').write('x')\nPY",
                    "ruby -e \"File.write(File.expand_path('~/.config/of/orgs.yaml'), 'x')\"",
                    "node -e \"require('fs').writeFileSync(require('os').homedir()+'/.config/of/orgs.yaml','x')\"",
                    # a path in a line variable (of_secrets-review on hand-over 726902df, found on PR 395)
                    'd=$HOME/.config/of; cp /tmp/o.yaml "$d/orgs.yaml"', "d=~/.config/of; echo x > $d/views.yaml"):
            got = bash(cmd)
            self.assertTrue(got and "G33" in got[0], cmd)

    def test_reads_still_run(self):
        o = "~/.config/of/orgs.yaml"
        for cmd in (f"cat {o}", f"grep hold {o}", f"yq '.orgs' {o}", f"cat {o} > /tmp/orgs-copy.yaml",
                    'd=/tmp; cp /tmp/o.yaml "$d/orgs.yaml"',
                    "python3 -c \"import yaml,os; print(yaml.safe_load(open(os.path.expanduser('~/.config/of/orgs.yaml'))))\"",
                    "find ~/.config/of -name '*.yaml' -exec grep -l hold {} \\;"):
            self.assertIsNone(bash(cmd), cmd)


if __name__ == "__main__":
    unittest.main()
