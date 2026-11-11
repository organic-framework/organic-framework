"""Loaded first by discover (sorted names): sets the isolation if the package init did not run, and fails loudly
if anything a test could reach the founder's 1Password or home through is still set."""

import os
import unittest

try:
    import isolate  # noqa: F401  (discover -s tests: the tests folder is on sys.path)
except ImportError:
    from tests import isolate  # noqa: F401  (discover -s tests -t .)


class IsolationTest(unittest.TestCase):
    def test_no_user_git_config_agent_or_home(self):
        self.assertEqual(os.environ.get("GIT_CONFIG_GLOBAL"), os.devnull)
        self.assertEqual(os.environ.get("GIT_CONFIG_NOSYSTEM"), "1")
        self.assertEqual(os.environ.get("GIT_TERMINAL_PROMPT"), "0")
        self.assertEqual(os.environ.get("SSH_AUTH_SOCK"), "")
        self.assertEqual(os.environ.get("OF_AUTOMATION"), "1")
        self.assertNotEqual(os.environ.get("HOME"), os.environ.get("OF_TEST_REAL_HOME"))
        self.assertTrue(os.path.basename(os.environ["HOME"]).startswith("of-test-home-"))

    def test_git_sees_no_signing(self):
        import subprocess
        r = subprocess.run(["git", "config", "--get", "commit.gpgsign"], capture_output=True, text=True,
                           cwd=os.environ["HOME"])
        self.assertNotEqual(r.stdout.strip(), "true")


if __name__ == "__main__":
    unittest.main()
