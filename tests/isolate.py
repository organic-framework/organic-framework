"""One place that keeps every test away from the founder's machine state (of_architect, 2026-11-07: tests that ran
'git commit' in temporary repositories signed through the global git config and called the founder's 1Password).

Imported by tests/__init__.py (a package run: python3 -m unittest discover -s tests -t .) and by
tests/test_0_isolation.py, which the loader imports first in any discover run, so the variables are set before any
test module is imported or run. Idempotent."""

import atexit
import os
import shutil
import tempfile

VARS = {
    "GIT_CONFIG_GLOBAL": os.devnull,   # no user git config: no commit signing, no credential helpers
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",        # git never asks for anything
    "SSH_AUTH_SOCK": "",               # never the SSH agent (1Password)
    "OF_AUTOMATION": "1",
}


def apply():
    if os.environ.get("OF_TEST_ISOLATED") == "1":
        return
    os.environ.update(VARS)
    import site
    # packages installed with --user (PyYAML) live under the real home: keep them for subprocesses
    os.environ.setdefault("PYTHONUSERBASE", site.getuserbase())
    # nothing reads or writes the founder's home config or state; realpath: /var is a link to /private/var
    home = os.path.realpath(tempfile.mkdtemp(prefix="of-test-home-"))
    os.environ["OF_TEST_REAL_HOME"] = os.environ.get("HOME", "")
    os.environ["HOME"] = home
    os.makedirs(os.path.join(home, ".config", "of"), exist_ok=True)  # a machine that lists no organization
    with open(os.path.join(home, ".config", "of", "orgs.yaml"), "w") as fh:
        fh.write("orgs: []\n")
    atexit.register(shutil.rmtree, home, True)
    os.environ["OF_TEST_ISOLATED"] = "1"


apply()
