"""docs/guard.md K6: OF's own code never prints or logs a secret value.

A tripwire, not a proof: every call to an output sink (print, write, log, logging) whose argument
names a secret-shaped variable (key, token, secret, password, credential, private, as a whole
`_`-part) must be listed below with the reason it is safe. A new one fails until it is reviewed.
"""
import ast
import glob
import os
import re
import unittest

BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin")
SECRET = re.compile(r"(?:^|_)(?:key|token|secret|password|passwd|pw|credentials?|private)(?:$|_)", re.I)
SINKS = {"print", "write", "info", "warning", "error", "debug", "exception", "log"}

# (file, the call's source line stripped): why it is safe
REVIEWED = {
    ("of_passkey_owner.py", 'out.write(f"Revoked the passkey {credential[:12]}… of {person} in {grants_path} (its '
                            'structure keeper commits it).\\n")'):
        "a passkey's credential id is a public identifier, sent to the phone in every request; no key or value",
    ("of_capacity.py", "log(f\"  memory {key}: {[x for x in merge_memory(mem_src, mem_dst, mem_base, '/nonexistent', "
                       "dry_run=True) if x[1] != 'same']}\")"): "key is a memory directory's name, not a credential",
    ("of_hook.py", 'write("mandate_check", {"verdict": "warn", "decision": "admit", "mode": "enforce",'):
        "the subject is passed through mask(); `token` is the matched command word",
    ("of_plugins.py", 'fh.write(f"{principal_id} namespaces=\\"{self.NAMESPACE}\\" {public_key}\\n")'):
        "a public key into ssh-keygen's allowed-signers file",
    ("of_server.py", 'fh.write(secret_key(dict(cfg, secret_key_file=None, tenant_key_create=False)).hex() + "\\n")'):
        "export-secret-key: into a new mode-600 file, never printed",
    ("of_server.py", 'fh.write(secrets.token_hex(32) + "\\n")'): "a new tenant key into a mode-600 file",
}


def findings():
    out = set()
    for path in sorted(glob.glob(os.path.join(BIN, "*.py"))):
        with open(path) as fh:
            src = fh.read()
        lines = src.splitlines()
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if name not in SINKS:
                continue
            for arg in node.args:
                words = [n.id if isinstance(n, ast.Name) else n.attr for n in ast.walk(arg)
                         if isinstance(n, (ast.Name, ast.Attribute))]
                if any(SECRET.search(w) for w in words):
                    out.add((os.path.basename(path), lines[node.lineno - 1].strip()))
    return out


class NoSecretOutputTest(unittest.TestCase):
    def test_every_secret_named_value_sent_to_output_is_reviewed(self):
        new = sorted(findings() - set(REVIEWED))
        self.assertEqual(new, [], "review each (docs/guard.md K6), then add it to REVIEWED with the reason")

    def test_the_reviewed_list_has_no_stale_entries(self):
        self.assertEqual(sorted(set(REVIEWED) - findings()), [])


if __name__ == "__main__":
    unittest.main()
