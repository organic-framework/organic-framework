"""The macOS keychain: a secret_stores implementation for a solo owner on one Mac (docs/agent-secrets.md §13.4;
hand-over 3512db81).

A reference is `keychain://<service>/<account>`, a generic password. There is no identity per position: every
process of the OS user reaches the keychain, so the store keeps no position from another's items and the broker's own
check is the only boundary (ENFORCES_BOUNDARY is False; the owner's card says so). A value is read into the caller's
process only: `security … -w` prints it to this process's pipe, never to an agent; a value is written through
`security -i` on standard input, so it is never on a command line. Errors carry the exit code only.

Settings: `keychain`, a keychain file to use instead of the user's default one (the contract tests use a stand-in).
"""

import re
import shutil
import subprocess
import sys

REF = re.compile(r"^keychain://([^/\s]+)/([^/\s]+)$")


class StoreError(Exception):
    """A store refused or could not be reached; the message never holds a value."""


def _quote(word):
    """A word for `security -i`'s own command line: double quotes, with backslash and quote escaped."""
    return '"' + str(word).replace("\\", "\\\\").replace('"', '\\"') + '"'


class Keychain:
    SCHEME = "keychain"
    ENFORCES_BOUNDARY = False
    AUDIT = "none: the broker's record is the only read log"
    VALUE_COMMANDS = (("security", "find-generic-password", "-w"), ("security", "find-internet-password", "-w"),
                      ("security", "dump-keychain", "-d"))
    IDENTITY_VARIABLE = None
    CLI = None  # `security` is the OS's own tool; its value-printing forms are guarded (G6, G11)

    def __init__(self, settings=None):
        settings = settings or {}
        self.keychain = settings.get("keychain")
        self.timeout = float(settings.get("timeout") or 20)
        self.runner = subprocess.run

    @classmethod
    def detect(cls):
        return sys.platform == "darwin" and bool(shutil.which("security"))

    @staticmethod
    def parse(ref):
        m = REF.match(ref or "")
        if not m:
            raise StoreError("not a keychain reference (keychain://<service>/<account>)")
        return m.group(1), m.group(2)

    def container(self, ref):
        return self.parse(ref)[0]

    def _run(self, args, stdin=None, ok=(0,)):
        tail = [self.keychain] if self.keychain and stdin is None else []
        try:
            r = self.runner(["security", *args, *tail], capture_output=True, text=True, input=stdin,
                            timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise StoreError(f"the keychain could not be reached ({type(e).__name__})") from None
        if r.returncode not in ok:
            raise StoreError(f"the keychain refused (security {args[0] if args else '-i'}, exit {r.returncode})")
        return r

    def read(self, ref, identity=None):
        """The value; for the caller to put into a process, never to print."""
        service, account = self.parse(ref)
        out = self._run(["find-generic-password", "-s", service, "-a", account, "-w"]).stdout
        return out[:-1] if out.endswith("\n") else out

    def write(self, ref, value, identity=None):
        """Add or update the item; the value goes as hex through `security -i` on standard input."""
        service, account = self.parse(ref)
        words = ["add-generic-password", "-U", "-s", service, "-a", account, "-X", value.encode().hex()]
        if self.keychain:
            words.append(self.keychain)
        self._run(["-i"], stdin=" ".join(_quote(w) for w in words) + "\n")

    def exists(self, ref, identity=None):
        service, account = self.parse(ref)
        return self._run(["find-generic-password", "-s", service, "-a", account], ok=(0, 44)).returncode == 0

    def list(self, container, identity=None):
        """The accounts of the generic passwords whose service is CONTAINER: names only (dump-keychain without -d
        prints attributes, never values)."""
        out = self._run(["dump-keychain"]).stdout
        names, svc, acct = [], None, None
        for line in out.splitlines() + ["keychain:"]:
            if line.startswith("keychain:"):
                if svc == container and acct is not None:
                    names.append(acct)
                svc = acct = None
            m = re.search(r'"(svce|acct)"<blob>="(.*)"$', line.strip())
            if m:
                if m.group(1) == "svce":
                    svc = m.group(2)
                else:
                    acct = m.group(2)
        return sorted(names)

    def bootstrap(self, org, position, reads, writes, identity_dir):
        """Nothing to create: the keychain has no identity per position (ENFORCES_BOUNDARY is False)."""
        return {"identity_file": None,
                "steps": [f"nothing to create for {org}:{position}: the keychain has no identity per position; the "
                          "broker's own check is the only boundary between positions"]}


PLUGIN = Keychain
