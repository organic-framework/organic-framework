"""The shared contract every plugin passes (see tests/contracts/__init__.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "bin"))
import of_family  # noqa: E402


class FamilyContract(unittest.TestCase):
    family = None
    name = None

    def impl(self):
        return of_family.implementation(self.family, self.name)

    def test_has_every_method_of_its_family(self):
        cls = self.impl()
        missing = [m for m in of_family.FAMILIES[self.family].methods if not callable(getattr(cls, m, None))]
        self.assertEqual(missing, [], f"{self.family}/{self.name} lacks {missing}")

    def test_detect_is_a_bool_when_defined(self):
        cls = self.impl()
        if getattr(cls, "detect", None):
            self.assertIsInstance(cls.detect(), bool)


def cases(family, base=FamilyContract):
    """{test class name: TestCase} for every implementation of `family`, to put in a test module's globals()."""
    out = {}
    for name in sorted(of_family.implementations(family)):
        cname = f"{family.title().replace('_', '')}{name.title().replace('_', '').replace('-', '')}Contract"
        out[cname] = type(cname, (base,), {"family": family, "name": name})
    return out
