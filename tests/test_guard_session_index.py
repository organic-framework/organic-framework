"""G36: the guard's session index is the collector's (docs/guard.md G36).

Hand-over a18341af (of_server), from of_secrets-review, msg 01a1f9a7-b29c: _guard_index trusts
~/.local/state/of/index.json for a session's position, and with it the position's credential rules; that folder is
writable, so a session that rewrites its entry could take another position's rules."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_guard  # noqa: E402

IDX = "~/.local/state/of/index.json"
TMP = "~/.local/state/of/index.json.4242.1795190000000000000.tmp"


def bash(cmd, cwd=None):
    hit = of_guard.critical_deny("Bash", {"command": cmd}, cwd)
    return hit[0] if hit else None


class SessionIndexTest(unittest.TestCase):

    def test_the_file_tools_never_write_it(self):
        home = os.path.expanduser("~")
        for tool in ("Write", "Edit", "MultiEdit"):
            for path in (f"{home}/.local/state/of/index.json", f"{home}/.LOCAL/state/of/INDEX.json",
                         os.path.expanduser(TMP)):
                with self.subTest(tool=tool, path=path):
                    hit = of_guard.critical_deny(tool, {"file_path": path})
                    self.assertEqual(hit and hit[0], of_guard.SESSION_INDEX_DENY)
        hit = of_guard.critical_deny("Write", {"file_path": "index.json"}, f"{home}/.local/state/of")
        self.assertEqual(hit and hit[0], of_guard.SESSION_INDEX_DENY)               # a relative path, by its cwd
        self.assertIsNone(of_guard.critical_deny("Read", {"file_path": f"{home}/.local/state/of/index.json"}))
        self.assertIsNone(of_guard.critical_deny("Write", {"file_path": f"{home}/.local/state/of/index.json.md"}))
        self.assertIsNone(of_guard.critical_deny("Write", {"file_path": f"{home}/w/docs/index.json"}))

    def test_a_shell_write_move_copy_link_or_removal_is_refused(self):
        for cmd in (f"echo '{{}}' > {IDX}", f"echo x >> {IDX}", f"printf x | tee {IDX}", f"printf x | tee -a {IDX}",
                    f"cp /tmp/forged.json {IDX}", f"mv /tmp/forged.json {IDX}", f"ln -sf /tmp/forged.json {IDX}",
                    f"rm {IDX}", f"rm -f {TMP}", f"truncate -s 0 {IDX}", f"sed -i 's/a/b/' {IDX}",
                    f"cp /tmp/forged.json {TMP}", f"mv {TMP} /tmp/x", "cp /tmp/index.json ~/.local/state/of/",
                    f"echo x > ~/.local/state/of/index.json.*.tmp", "rm ~/.local/state/of/index.js*",
                    f"d=~/.local/state/of; echo x > $d/index.json"):
            with self.subTest(cmd=cmd):
                self.assertEqual(bash(cmd), of_guard.SESSION_INDEX_DENY)

    def test_interpreter_code_naming_it_beside_a_write(self):
        for cmd in (f"python3 -c \"open('{os.path.expanduser(IDX)}', 'w').write('{{}}')\"",
                    "python3 - <<'EOF'\nimport json, os\np = os.path.expanduser('~/.local/state/of/index.json')\n"
                    "json.dump({}, open(p, 'w'))\nEOF",
                    f"perl -e 'open(F, \">{os.path.expanduser(IDX)}\"); print F 1'"):
            with self.subTest(cmd=cmd[:40]):
                self.assertEqual(bash(cmd), of_guard.SESSION_INDEX_DENY)

    def test_reading_it_stays_open(self):
        for cmd in (f"cat {IDX}", f"python3 -c \"import json; print(json.load(open('{os.path.expanduser(IDX)}')))\"",
                    f"jq . {IDX}", f"cp {IDX} /tmp/index-copy.json", f"ls -l ~/.local/state/of/",
                    "echo x > ~/.local/state/of/notes.txt", "cp /tmp/forged.json ~/.local/state/of/", "grep -c sessions ~/.local/state/of/index.json"):
            with self.subTest(cmd=cmd):
                self.assertIsNone(bash(cmd))

    def test_under_of_state_too(self):
        import tempfile
        from unittest import mock
        state = tempfile.mkdtemp(prefix="of-state-")
        with mock.patch.dict(os.environ, {"OF_STATE": state}):
            self.assertEqual(bash(f"echo x > {state}/index.json"), of_guard.SESSION_INDEX_DENY)
            hit = of_guard.critical_deny("Write", {"file_path": f"{state}/index.json"})
            self.assertEqual(hit and hit[0], of_guard.SESSION_INDEX_DENY)


if __name__ == "__main__":
    unittest.main()
