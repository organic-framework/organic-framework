"""The of-server image (deploy/Dockerfile) copies every module its modules import when they load.

CI imports every module in the built image before it pushes (.github/workflows/of-server-image.yml), but only after a
build; this test finds the same gap from the source, before a pull request (2026-11-12: of_plugins began to import
of_family, which the image lacked, and the image's import check failed). Imports inside a function run only on that
path; the server's own start runs some of those, so a second test starts it from the image's modules alone (of_review
on organic-framework PR 368: run() imported of_stacks, and App() of_server_health, neither of them in the image, and
the image's job only builds it). A third holds every other function-level import of a module the image lacks to a
written reason (of_review's note on the same PR: the secrets broker reached of_credentials._org_entry, which imports
of_protected)."""

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


def copied(text=None):
    """The of_* modules the Dockerfile's COPY instructions copy from the build context (a comment or another
    instruction that names a module copies nothing)."""
    if text is None:
        with open(os.path.join(ROOT, "deploy", "Dockerfile"), encoding="utf-8") as fh:
            text = fh.read()
    text = re.sub(r"\\\n", " ", text)  # one line per instruction
    out = set()
    for line in text.splitlines():
        words = line.split()
        if words[:1] == ["COPY"] and not any(w.startswith("--from=") for w in words):
            out |= set(re.findall(r"\bbin/(of_\w+)\.py\b", line))
    return out


# A module the image lacks, imported inside a function of one it holds: where that runs, and why the image does
# without it. "module.*": every function of that module. A new one fails the test until it is copied or listed here.
NOT_IN_IMAGE = {
    "of_client": {"of_ship._ship_server": "`of ship`'s client side; the server uses of_ship's batch code only"},
    "of_guard": {"of_credentials.report": "`of credentials`, on a machine",
                 "of_grants.shape_warnings": "`of registry check`, on a machine"},
    "of_hook": {f"{m}.{f}": "inside a try that goes on without the collector"
                for m, f in (("of_events", "card_prefix"), ("of_events", "record"), ("of_route", "_org_by_session"),
                             ("of_route", "_own_org_by_session"))},
    "of_keeper": {"of_access._approval_notes": "`of access`, on a machine; the server uses the limit checks only"},
    "of_pulse": {"of_access.send_digests": "the listener's access digest, on a machine"},
    "of_relay_client": {"of_server.host_watchdog": "the agent host's watchdog, run from its checkout, not the image"},
}
STUDIO = ("of_studio's pages: which of them the image serves is of_studio's decision (of_server's message of "
          "2026-11-19); until then such a page answers an error in the image")
for _m, _by in (("of_capacity", "of_studio"), ("of_loop", "of_model"), ("of_loops", "of_model of_studio"),
                ("of_router", "of_model"), ("of_spend", "of_studio"), ("of_studio_health", "of_studio"),
                ("of_studio_views", "of_studio"), ("of_works", "of_studio")):
    NOT_IN_IMAGE.setdefault(_m, {}).update({f"{b}.*": STUDIO for b in _by.split()})


def function_imports(module):
    """{(imported of_* module, "module.function")} for each import inside a function of MODULE."""
    with open(os.path.join(BIN, f"{module}.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    out = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Import):
                names = [a.name for a in sub.names]
            elif isinstance(sub, ast.ImportFrom) and sub.module and not sub.level:
                names = [sub.module]
            else:
                continue
            for name in names:
                name = name.split(".")[0]
                if name.startswith("of_") and os.path.exists(os.path.join(BIN, f"{name}.py")):
                    out.add((name, f"{module}.{fn.name}"))
    return out


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

    def test_only_copy_instructions_count(self):
        text = ("# bin/of_hook.py is the client's\nCOPY bin/of_a.py \\\n     bin/of_b.py ./\n"
                "COPY --from=keygen bin/of_c.py ./\nRUN cp bin/of_d.py /tmp\n")
        self.assertEqual(copied(text), {"of_a", "of_b"})
        self.assertIn("of_stacks", copied())

    def test_every_module_imported_inside_a_function_is_copied_or_has_its_reason(self):
        have = copied()
        unlisted, used = [], set()
        for module in sorted(have):
            for name, where in sorted(function_imports(module)):
                if name in have:
                    continue
                listed = NOT_IN_IMAGE.get(name, {})
                key = where if where in listed else f"{module}.*" if f"{module}.*" in listed else None
                if key is None:
                    unlisted.append(f"{name} <- {where}")
                else:
                    used.add((name, key))
        self.assertEqual(unlisted, [], "copy these in deploy/Dockerfile, or give NOT_IN_IMAGE the reason the image "
                                       "does without them (module <- where it is imported)")
        stale = sorted(f"{n} <- {k}" for n, ks in NOT_IN_IMAGE.items() for k in ks if (n, k) not in used)
        self.assertEqual(stale, [], "NOT_IN_IMAGE lists imports that are gone or now copied: remove them")

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
