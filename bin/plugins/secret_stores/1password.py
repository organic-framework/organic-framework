"""1Password through its service accounts: the secret_stores family's first implementation (docs/agent-secrets.md
§4, §13; hand-over 3512db81).

A reference is 1Password's own secret reference, `op://<vault>/<item>/[<section>/]<field>`, which `op read` takes as
it is. A position's identity is its service account's token; 1Password itself keeps a token to the vaults it was
created with (§4), so this store enforces the boundary between positions. Values are read into the caller's process
only: nothing here prints or logs one, and an error carries `op`'s exit code, never its message.
"""

import json
import os
import re
import shutil
import subprocess

REF = re.compile(r"^op://([A-Za-z0-9_.-][A-Za-z0-9_. -]*)/([A-Za-z0-9_. -]+)/(?:([A-Za-z0-9_. -]+)/)?"
                 r"([A-Za-z0-9_. -]+)$")


class StoreError(Exception):
    """A store refused or could not be reached; the message never holds a value."""


class OnePassword:
    SCHEME = "op"
    ENFORCES_BOUNDARY = True
    AUDIT = "each service account's Item Usage Report; the Events API on a Business account (docs/agent-secrets.md §4)"
    VALUE_COMMANDS = (("op", "read"), ("op", "item", "get"), ("op", "inject"), ("op", "run"))
    IDENTITY_VARIABLE = "OP_SERVICE_ACCOUNT_TOKEN"
    CLI = "op"  # its own command line: only the broker and committed scripts run it (docs/guard.md G9)

    def __init__(self, settings=None):
        settings = settings or {}
        self.op = settings.get("op") or shutil.which("op") or "op"
        self.timeout = float(settings.get("timeout") or 20)
        self.runner = subprocess.run

    @classmethod
    def detect(cls):
        return bool(shutil.which("op"))

    @staticmethod
    def parse(ref):
        """(vault, item, section or None, field) of a reference, or StoreError."""
        m = REF.match(ref or "")
        if not m:
            raise StoreError("not a 1Password secret reference (op://<vault>/<item>/[<section>/]<field>)")
        return m.group(1), m.group(2), m.group(3), m.group(4)

    def container(self, ref):
        return self.parse(ref)[0]

    def _run(self, args, identity, stdin=None):
        if not identity:
            raise StoreError("1Password needs the position's service-account token as its identity")
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/opt/homebrew/bin"), "HOME": os.environ.get("HOME", "/"),
               self.IDENTITY_VARIABLE: identity, "OP_BIOMETRIC_UNLOCK_ENABLED": "false", "OF_AUTOMATION": "1"}
        try:
            r = self.runner([self.op, *args], env=env, capture_output=True, text=True, input=stdin,
                            timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise StoreError(f"1Password could not be reached ({type(e).__name__})") from None
        if r.returncode != 0:
            raise StoreError(f"1Password refused (op {args[0]}, exit {r.returncode})")  # its message is not passed on
        return r.stdout

    def read(self, ref, identity=None):
        """The value, read as the position's service account; for the caller to put into a process, never to print."""
        self.parse(ref)
        return self._run(["read", "--no-newline", ref], identity)

    def write(self, ref, value, identity=None):
        """Set the field to VALUE through a template on standard input, so the value is never on a command line."""
        vault, item, section, field = self.parse(ref)
        doc = json.loads(self._run(["item", "get", item, "--vault", vault, "--format", "json"], identity) or "{}")
        hit = next((f for f in doc.get("fields") or [] if (f.get("label") or f.get("id")) == field and
                    (section is None or ((f.get("section") or {}).get("label") or "") == section)), None)
        if hit is None:
            raise StoreError(f"no field {field} in item {item} of vault {vault}")
        hit["value"] = value
        self._run(["item", "edit", item, "--vault", vault, "--template", "/dev/stdin"], identity,
                  stdin=json.dumps(doc))

    def exists(self, ref, identity=None):
        vault, item, section, field = self.parse(ref)
        try:
            doc = json.loads(self._run(["item", "get", item, "--vault", vault, "--format", "json"], identity) or "{}")
        except StoreError:
            return False
        return any((f.get("label") or f.get("id")) == field for f in doc.get("fields") or [])

    def list(self, container, identity=None):
        """The item titles of a vault: names only."""
        out = self._run(["item", "list", "--vault", container, "--format", "json"], identity)
        return sorted(str(i.get("title") or "") for i in json.loads(out or "[]"))

    def bootstrap(self, org, position, reads, writes, identity_dir):
        """The one line a person runs in a plain terminal to create the position's service account with exactly
        these vaults, its token going straight into the broker's identity file (docs/agent-secrets.md §5.2)."""
        vaults = {v: "read_items" for v in reads}
        vaults.update({v: "read_items,write_items" for v in writes})
        flags = " ".join(f"--vault {v}:{how}" for v, how in sorted(vaults.items()))
        target = os.path.join(identity_dir, org, f"{position}.token")
        return {"identity_file": target,
                "steps": [f"umask 077; mkdir -p {os.path.dirname(target)} && op service-account create "
                          f"of-{org}-{position} {flags} --raw > {target}"]}


PLUGIN = OnePassword
