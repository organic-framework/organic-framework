"""The plugin mechanism (bin/of_family.py; docs/plugins.md §2; hand-over aa628b92)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "bin"))
import of_family  # noqa: E402


class MechanismTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = of_family.SHIPPED
        of_family.SHIPPED = self.tmp.name
        os.makedirs(os.path.join(self.tmp.name, "secret_stores"))
        with open(os.path.join(self.tmp.name, "secret_stores", "envfile.py"), "w") as fh:
            fh.write("class EnvFile:\n"
                     "    def __init__(self, settings): self.settings = settings\n"
                     "    @classmethod\n"
                     "    def detect(cls): return True\n"
                     "    def read(self, ref): return None\n"
                     "    def write(self, ref, value): pass\n"
                     "    def exists(self, ref): return False\n"
                     "    def list(self, scope): return []\n"
                     "    def bootstrap(self): pass\n"
                     "PLUGIN = EnvFile\n")

    def tearDown(self):
        of_family.SHIPPED = self.saved
        self.tmp.cleanup()

    def test_no_silent_fallback_the_owner_chooses(self):
        self.assertEqual(of_family.implementations("secret_stores"), {"envfile": "shipped"})
        with self.assertRaises(of_family.PluginError) as e:
            of_family.load("secret_stores", machine={})
        self.assertIn("no secret_stores plugin chosen", str(e.exception))
        self.assertIn("options: envfile", str(e.exception))
        p = of_family.load("secret_stores", machine={"secrets": {"name": "envfile", "path": "/x/.env"}})  # the old key
        self.assertEqual(p.settings, {"path": "/x/.env"})

    def test_the_organization_wins_for_its_data(self):
        org = {"plugins": {"secret_stores": "envfile"}}
        self.assertEqual(of_family.choice("secret_stores", org, machine={"secrets": "keychain"})[2], "organization")
        self.assertEqual(of_family.choice("secret_stores", None, machine={"secret_stores": "keychain"})[:1], ("keychain",))
        self.assertEqual(of_family.choice("storage", None, machine={}), ("sqlite", {}, "default"))  # OF's own

    def test_unknown_names_detection_and_the_org_check(self):
        with self.assertRaises(of_family.PluginError):
            of_family.implementation("secret_stores", "lastpass")
        with self.assertRaises(of_family.PluginError):
            of_family.register("nope", "x", object)
        self.assertEqual(of_family.detect("secret_stores"), ["envfile"])
        self.assertTrue(any(l.startswith("secret_stores: found envfile") for l in of_family.propose(machine={})))
        probs = of_family.check_org({"plugins": {"secret": "envfile", "secrets": "vault", "runtime": {}}})
        self.assertTrue(any("'secret' is no family" in p for p in probs))
        self.assertTrue(any(p.startswith("warning: plugins: secret_stores: 'vault' is not installed") for p in probs))
        self.assertTrue(any("runtime: names no implementation" in p for p in probs))
        self.assertIn(of_family.SHIPPED, of_family.code_paths())

    def test_server_builtins_without_importing_the_server(self):
        """of_onboarding, 2026-11-11: of init --plugin storage=sqlite found no sqlite."""
        self.assertIn("sqlite", of_family.implementations("storage"))
        self.assertTrue(of_family.implementation("storage", "sqlite"))


if __name__ == "__main__":
    unittest.main()
