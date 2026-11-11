"""Runs the shared plugin contract (tests/contracts/) on every implementation found, of-server's seams first."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_plugins  # noqa: E402,F401 - registers of-server's built-in seams
from tests.contracts import contract  # noqa: E402

for _fam in sorted(contract.of_family.FAMILIES):
    globals().update(contract.cases(_fam))
