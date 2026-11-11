"""Contract tests of the plugin families (docs/plugins.md §2, §5 step 5; run by of_lab).

Every implementation of a family passes its family's contract. `contract.cases(family)` makes one TestCase per
implementation found (built in, shipped under bin/plugins/<family>/, or installed through the entry point group
organic_framework.<family>), checking what every family asks: each of the family's methods exists and is callable,
and `detect()`, when defined, answers a bool without touching the product. A family's own behaviour tests go in
tests/contracts/<family>/ and extend `contract.FamilyContract`.
"""
