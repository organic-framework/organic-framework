"""An env file: the secret_stores implementation for a lab (docs/plugins.md §3; hand-over 3512db81). Never for an
organization's real credentials: the file is plain text that every process of the OS user can read.

A reference is `envfile://<NAME>`; the file is the `path` setting, `NAME=value` lines (a `#` line is a comment; no
quoting, no expansion). There is no identity per position and no boundary between positions. A value is read into
the caller's process only; the file is written whole through a mode-600 temporary file and a rename.
"""

import os
import re
import tempfile

REF = re.compile(r"^envfile://([A-Za-z_][A-Za-z0-9_]*)$")


class StoreError(Exception):
    """A store refused or could not be reached; the message never holds a value."""


class EnvFile:
    SCHEME = "envfile"
    ENFORCES_BOUNDARY = False
    AUDIT = "none: a lab store"
    VALUE_COMMANDS = ()
    IDENTITY_VARIABLE = None
    CLI = None

    def __init__(self, settings=None):
        settings = settings or {}
        path = settings.get("path")
        if not path:
            raise StoreError("the env-file store needs its file as the `path` setting")
        self.path = os.path.expanduser(path)

    @classmethod
    def detect(cls):
        return False  # a lab store is chosen on purpose, never proposed

    @staticmethod
    def parse(ref):
        m = REF.match(ref or "")
        if not m:
            raise StoreError("not an env-file reference (envfile://<NAME>)")
        return m.group(1)

    def container(self, ref):
        self.parse(ref)
        return os.path.basename(self.path)

    def _lines(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                return fh.read().splitlines()
        except FileNotFoundError:
            return []
        except OSError as e:
            raise StoreError(f"the env file could not be read ({type(e).__name__})") from None

    def _values(self):
        out = {}
        for line in self._lines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v
        return out

    def read(self, ref, identity=None):
        """The value; for the caller to put into a process, never to print."""
        name = self.parse(ref)
        values = self._values()
        if name not in values:
            raise StoreError(f"no {name} in the env file")
        return values[name]

    def write(self, ref, value, identity=None):
        name = self.parse(ref)
        if "\n" in value or "\r" in value:
            raise StoreError("an env-file value is one line")
        lines, done = [], False
        for line in self._lines():
            if not done and "=" in line and line.split("=", 1)[0].strip() == name:
                line, done = f"{name}={value}", True
            lines.append(line)
        if not done:
            lines.append(f"{name}={value}")
        folder = os.path.dirname(self.path) or "."
        os.makedirs(folder, mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".envfile-", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except OSError as e:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise StoreError(f"the env file could not be written ({type(e).__name__})") from None

    def exists(self, ref, identity=None):
        return self.parse(ref) in self._values()

    def list(self, container, identity=None):
        """The names in the file: names only."""
        return sorted(self._values())

    def bootstrap(self, org, position, reads, writes, identity_dir):
        return {"identity_file": None,
                "steps": [f"nothing to create for {org}:{position}: a lab env file has no identity per position"]}


PLUGIN = EnvFile
