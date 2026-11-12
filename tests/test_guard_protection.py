"""What an organization's registry keeps from every agent session (docs/guard.md G20, G21), and the wider G11: a
keychain password printed with -g as with -w, and every item exported. No keychain, launchd or herdr is touched: the
protected names and folders are made up, the folders live in a temporary directory."""

import contextlib
import io
import json
import os
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_credentials  # noqa: E402
import of_events  # noqa: E402
import of_guard  # noqa: E402
import of_hook  # noqa: E402


def bash(cmd, cwd=None):
    hit = of_guard.critical_deny("Bash", {"command": cmd}, cwd)
    return hit[0] if hit else None


class KeychainTest(unittest.TestCase):
    def tearDown(self):
        of_guard.use_protection()

    def test_g11_a_password_printed_by_w_or_g_and_an_export(self):
        for cmd in ("security find-generic-password -s svc -w", "security find-generic-password -gs svc",
                    "security find-internet-password -s host -g", "security find-generic-password -a me -s svc -gw",
                    "/usr/bin/security find-generic-password -s svc -w", "security export -k login.keychain -o x.p12",
                    "security export", "security dump-keychain -d", "security dump-keychain -ad login.keychain"):
            self.assertIn("would print secrets", bash(cmd) or "", cmd)
        for cmd in ("security find-generic-password -s svc", "security find-generic-password -sw",  # -s takes "w"
                    "security find-internet-password -s host -a me", "security dump-keychain",
                    "security list-keychains", "security find-identity -v -p codesigning"):
            self.assertIsNone(bash(cmd), cmd)

    def test_g20_any_security_call_naming_a_protected_item(self):
        of_guard.use_protection(keychain=["acme-backup-r2-key-id", "acme-vault-*"])
        for cmd in ("security find-generic-password -s acme-backup-r2-key-id",
                    "security find-generic-password -sacme-backup-r2-key-id",
                    "security find-generic-password -l ACME-BACKUP-R2-KEY-ID login.keychain",
                    "security delete-generic-password -s acme-vault-restic",
                    "security add-generic-password -U -a me -s acme-vault-r2-secret -w x",
                    "security set-generic-password-partition-list -s acme-vault-x -S apple-tool:",
                    "/usr/bin/security find-generic-password -s acme-backup-r2-key-id -w",
                    "S=acme-vault-endpoint; security find-generic-password -s acme-vault-endpoint",
                    "python3 -c \"import subprocess; subprocess.run(['security', 'find-generic-password', '-s', "
                    "'acme-backup-r2-key-id'])\"",
                    "python3 - <<'EOF'\nimport keyring\nprint(keyring.get_password('acme-vault-r2', 'me'))\nEOF",
                    "osascript -e 'do shell script \"security find-generic-password -s acme-vault-x\"'"):
            got = bash(cmd) or ""
            self.assertTrue("keychain item" in got or "would print secrets" in got, (cmd, got))
        for cmd in ("security find-generic-password -s acme-other", "git commit -m 'rotate acme-vault-r2 keys'",
                    "grep -rn acme-backup-r2-key-id docs/", "echo acme-vault-x"):
            self.assertIsNone(bash(cmd), cmd)

    def test_without_a_catalogue_entry_metadata_stays_open(self):
        self.assertIsNone(bash("security find-generic-password -s acme-backup-r2-key-id"))


class ProtectedCodeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.bin = os.path.join(self.root, "Support", "acme-backup", "bin")
        self.agents = os.path.join(self.root, "LaunchAgents")
        os.makedirs(self.bin)
        os.makedirs(self.agents)
        of_guard.use_protection(code=[self.bin, os.path.join(self.agents, "com.acme.*")])

    def tearDown(self):
        of_guard.use_protection()
        self.tmp.cleanup()

    def test_g21_file_tools(self):
        for tool, path in (("Write", f"{self.bin}/backup.sh"), ("Edit", f"{self.agents}/com.acme.backup.plist"),
                           ("MultiEdit", f"{self.bin}/../bin/run.sh")):
            self.assertIn("protected_code", (of_guard.critical_deny(tool, {"file_path": path}, self.root) or [""])[0])
        self.assertIsNone(of_guard.critical_deny("Write", {"file_path": f"{self.agents}/other.plist"}, self.root))
        self.assertIsNone(of_guard.critical_deny("Read", {"file_path": f"{self.bin}/backup.sh"}, self.root))

    def test_g21_bash_writes_removals_moves_and_modes(self):
        b, a = self.bin, self.agents
        for cmd in (f"echo x > {b}/backup.sh", f"cp new.sh {b}/", f"chmod +x {b}/backup.sh",
                    f"sed -i '' s/a/b/ {b}/backup.sh", f"tee {a}/com.acme.backup.plist < p",
                    f"mv {a}/com.acme.backup.plist /tmp/x", f"rm -rf {os.path.dirname(b)}", f"rm -r {a}",
                    f"rmdir {b}", f"cd {b} && rm backup.sh", f"install -m 755 x {b}/backup.sh"):
            self.assertIn("protected_code", bash(cmd, self.root) or "", cmd)
        for cmd in (f"cat {b}/backup.sh", f"ls {a}", f"cp {b}/backup.sh /tmp/copy.sh", f"echo x > {a}/other.plist",
                    f"rm -rf {self.root}/elsewhere", f"bash {b}/backup.sh scan"):
            self.assertIsNone(bash(cmd, self.root), cmd)


class GuardStateTest(unittest.TestCase):
    def test_the_caches_these_rules_read_are_not_an_agents_to_change(self):
        """of_review's probe on organic-framework PR 65: rm, an overwrite, a forged cache."""
        for cmd in ("rm ~/.local/state/of/guard-credentials.json", "echo '{}' > ~/.local/state/of/guard-credentials.json",
                    "cp forged.json ~/.local/state/of/guard-credentials.json",
                    "mv ~/.local/state/of/passkey-revoked.json /tmp/x", "truncate -s 0 ~/.local/state/of/passkey-revoked.json"):
            self.assertIn("guard's own cache", bash(cmd, os.path.expanduser("~")) or "", cmd)
        for tool in ("Write", "Edit"):
            hit = of_guard.critical_deny(tool, {"file_path": os.path.expanduser("~/.local/state/of/guard-credentials.json")})
            self.assertIn("guard's own cache", (hit or [""])[0], tool)
        self.assertIsNone(bash("cat ~/.local/state/of/guard-credentials.json"))
        self.assertIsNone(bash("ls -la ~/.local/state/of/"))


class CatalogueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        r = self.tmp.name
        os.makedirs(os.path.join(r, "org", "registry"))
        self.reg = os.path.join(r, "org", "registry", "positions.yaml")
        with open(self.reg, "w") as fh:
            fh.write(textwrap.dedent("""
                organization: acme
                protected_code:
                  - ~/Library/Application Support/acme-backup/bin
                  - ~/Library/LaunchAgents/com.acme.*
                  - /tmp
                credentials:
                  backup-keys:
                    keychain: [acme-backup-r2-key-id, acme-backup-*]
                    keeper: acme_legal
                  bad:
                    keychain: ["two words", "ab*"]
                    keeper: acme_legal
                positions:
                  - {name: acme_legal, system: acme, role: legal, holder: {session: S1}}
                  - {name: acme_docs, system: acme, role: docs, holder: {session: S2}}
                """))
        self.orgs = os.path.join(r, "orgs.yaml")
        with open(self.orgs, "w") as fh:
            fh.write("orgs:\n  - {name: acme, path: org}\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_entries_and_their_checks(self):
        cat = of_credentials.load(self.orgs)
        keychain, code = of_credentials.protection(cat)
        self.assertEqual(keychain, ["acme-backup-*", "acme-backup-r2-key-id"])
        home = os.path.expanduser("~")
        self.assertEqual(code, [f"{home}/Library/Application Support/acme-backup/bin",
                                f"{home}/Library/LaunchAgents/com.acme.*"])
        probs = "\n".join(of_credentials.check(self.orgs))
        self.assertIn("protected_code: '/tmp' is not a path", probs)
        self.assertIn("keychain: 'two words' is not a service name", probs)
        self.assertIn("keychain: 'ab*' is not a service name", probs)
        self.assertNotIn("credential backup-keys: needs ref", probs)

    def test_the_hook_reads_the_cache_and_denies(self):
        state = os.path.join(self.tmp.name, "state")
        os.makedirs(state)
        self.assertIsNone(of_credentials.cached_protection(state))  # no cache yet
        self.assertIn("acme-backup-r2-key-id", of_credentials.guard_protection(state, self.orgs)[0])  # built first
        os.remove(os.path.join(state, of_credentials.CACHE))
        of_credentials.cached(state, self.orgs)
        self.assertIn("acme-backup-r2-key-id", of_credentials.cached_protection(state)[0])
        path = os.path.join(state, of_credentials.CACHE)
        with open(path) as fh:
            c = json.load(fh)
        c["version"] = 1
        with open(path, "w") as fh:
            json.dump(c, fh)
        self.assertIsNone(of_credentials.cached_protection(state))  # an old cache is rebuilt, not read
        self.assertEqual(of_credentials.cached(state, self.orgs)["acme"]["credentials"]["backup-keys"]["keychain"],
                         ["acme-backup-r2-key-id", "acme-backup-*"])
        os.remove(path)  # a removed cache: the hook rebuilds it before the critical denies
        saved = (of_events.STATE_DIR, of_hook.STATE, os.environ.get("OF_ORGS"))
        of_events.STATE_DIR = of_hook.STATE = state
        os.environ["OF_ORGS"] = self.orgs
        try:
            of_hook.OUTPUT = None
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                of_hook.handle("PreToolUse", {"hook_event_name": "PreToolUse", "session_id": "S1", "cwd": self.tmp.name,
                                              "tool_name": "Bash",
                                              "tool_input": {"command": "security find-generic-password -s "
                                                                        "acme-backup-restic"}})
            printed = out.getvalue()
            got = (json.loads(printed) if printed.strip() else (of_hook.OUTPUT or {})).get("hookSpecificOutput") or {}
            self.assertEqual(got.get("permissionDecision"), "deny")
            self.assertIn("G20", got.get("permissionDecisionReason", ""))
        finally:
            of_events.STATE_DIR, of_hook.STATE = saved[0], saved[1]
            if saved[2] is None:
                os.environ.pop("OF_ORGS", None)
            else:
                os.environ["OF_ORGS"] = saved[2]
            of_hook.OUTPUT = None
            of_guard.use_protection()


if __name__ == "__main__":
    unittest.main()
