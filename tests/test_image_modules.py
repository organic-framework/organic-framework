"""The of-server image (deploy/Dockerfile) copies every module its modules import when they load.

CI imports every module in the built image before it pushes (.github/workflows/of-server-image.yml), but only after a
build; this test finds the same gap from the source, before a pull request (2026-11-12: of_plugins began to import
of_family, which the image lacked, and the image's import check failed). Imports inside a function run only on that
path and are left out, as the image leaves out the client modules they name."""

import ast
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")


def copied():
    """The of_* modules the Dockerfile copies into /app."""
    with open(os.path.join(ROOT, "deploy", "Dockerfile"), encoding="utf-8") as fh:
        return set(re.findall(r"\bbin/(of_\w+)\.py\b", fh.read()))


def load_imports(module):
    """The of_* modules of this repository that MODULE imports at load: at module level, in a try or an if there."""
    with open(os.path.join(BIN, f"{module}.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    out = set()
    for node in tree.body:
        nodes = [node] if isinstance(node, (ast.Import, ast.ImportFrom)) else (
            list(ast.walk(node)) if isinstance(node, (ast.Try, ast.If)) else [])
        for sub in nodes:
            if isinstance(sub, ast.Import):
                names = [a.name for a in sub.names]
            elif isinstance(sub, ast.ImportFrom) and sub.module and not sub.level:
                names = [sub.module]
            else:
                continue
            for name in names:
                name = name.split(".")[0]
                if name.startswith("of_") and os.path.exists(os.path.join(BIN, f"{name}.py")):
                    out.add(name)
    return out


class ImageModulesTest(unittest.TestCase):
    def test_every_module_imported_at_load_is_copied(self):
        have = copied()
        self.assertIn("of_server", have)
        missing = {}
        for module in sorted(have):
            self.assertTrue(os.path.exists(os.path.join(BIN, f"{module}.py")), f"the Dockerfile copies {module}")
            for name in load_imports(module) - have:
                missing.setdefault(name, []).append(module)
        self.assertEqual(missing, {}, "add these to deploy/Dockerfile (module: imported by)")

    def test_a_function_level_import_is_not_a_load_import(self):
        self.assertNotIn("of_hook", load_imports("of_events"))   # imported inside a function only


if __name__ == "__main__":
    unittest.main()
