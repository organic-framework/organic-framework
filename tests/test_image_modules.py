"""The of-server image (deploy/Dockerfile) copies every module its modules import when they load.

CI imports every module in the built image before it pushes (.github/workflows/of-server-image.yml), but only after a
build; this test finds the same gap from the source, before a pull request (2026-11-12: of_plugins began to import
of_family, which the image lacked, and the image's import check failed). Imports inside a function run only on that
path and are left out, as the image leaves out the client modules they name; the server's own start runs some of
those, so a second test starts it from the image's modules alone (of_review on organic-framework PR 368: run()
imported of_stacks, and App() of_server_health, neither of them in the image, and the image's job only builds it)."""

import ast
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

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

    def test_the_server_starts_and_dumps_its_stacks_from_the_image_modules_alone(self):
        """`of_server.py run` as the image's entry point runs it, from a folder that holds only what the Dockerfile
        copies into /app: it answers its health route, writes every thread's stack on SIGUSR1 and stops on SIGTERM."""
        tmp = tempfile.mkdtemp(prefix="of-image-")
        self.addCleanup(shutil.rmtree, tmp, True)
        app = os.path.join(tmp, "app")
        os.makedirs(os.path.join(app, "docs"))
        for module in copied():
            shutil.copy(os.path.join(BIN, f"{module}.py"), app)
        shutil.copy(os.path.join(BIN, "of_studio.css"), app)
        shutil.copytree(os.path.join(BIN, "rules"), os.path.join(app, "rules"))
        shutil.copy(os.path.join(ROOT, "docs", "diagnose-v0.yaml"), os.path.join(app, "docs"))
        cfg = os.path.join(tmp, "server.yaml")
        with open(cfg, "w") as fh:
            fh.write(f"host: 127.0.0.1\nport: 0\nstate_dir: {tmp}/state\ntenant_key_file: {tmp}/tenant.key\n"
                     f"trusted_grant_keys_file: {tmp}/none.pub\n")
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "OF_SERVER_CONFIG")}
        env.update(HOME=tmp, OF_AUTOMATION="1", PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1",
                   OF_DIAGNOSE=os.path.join(app, "docs", "diagnose-v0.yaml"))
        proc = subprocess.Popen([sys.executable, os.path.join(app, "of_server.py"), "--config", cfg, "run"],
                                cwd=tmp, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        out, err = [], []

        def drain(stream, into):
            for line in stream:
                into.append(line)

        readers = [threading.Thread(target=drain, args=(proc.stdout, out), daemon=True),
                   threading.Thread(target=drain, args=(proc.stderr, err), daemon=True)]
        for t in readers:
            t.start()

        def wait_for(cond, what, timeout=30):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if cond():
                    return
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
            self.fail(f"{what}; exit {proc.poll()}; stdout {''.join(out)[-800:]!r}; stderr {''.join(err)[-1500:]!r}")

        try:
            port = []
            wait_for(lambda: port or [port.append(int(m.group(1))) for m in
                                      [re.search(r"listening on [0-9.]+:(\d+)", "".join(out))] if m],
                     "the server never said it listens")
            with urllib.request.urlopen(f"http://127.0.0.1:{port[0]}/v1/health", timeout=10) as r:
                self.assertEqual(r.status, 200)
            proc.send_signal(signal.SIGUSR1)
            wait_for(lambda: "stack dump on SIGUSR1" in "".join(err), "no stack dump on SIGUSR1")
            self.assertIsNone(proc.poll(), "the server died on SIGUSR1")
            proc.send_signal(signal.SIGTERM)
            self.assertEqual(proc.wait(timeout=30), 0, "".join(err)[-1500:])
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
            for t in readers:
                t.join(timeout=5)
            proc.stdout.close()
            proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
