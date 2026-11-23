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
import glob
import json
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
    instruction that names a module copies nothing). As Docker reads it: comment lines dropped first, also inside a
    continued instruction; instructions in any case; a glob or the bin folder itself read against this checkout."""
    if text is None:
        with open(os.path.join(ROOT, "deploy", "Dockerfile"), encoding="utf-8") as fh:
            text = fh.read()
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    text = re.sub(r"\\\n", " ", text)  # one line per instruction
    out = set()
    for line in text.splitlines():
        words = line.split()
        if not words or words[0].upper() != "COPY" or any(w.startswith("--from=") for w in words):
            continue
        rest = line.strip()[len(words[0]):].strip()
        while rest.startswith("--"):  # the flags (--chown, --chmod, --link) come first
            rest = rest.split(None, 1)[1] if len(rest.split(None, 1)) > 1 else ""
        srcs = (json.loads(rest) if rest.startswith("[") else rest.split())[:-1]  # the last one is the destination
        for src in srcs:
            src = src.rstrip("/")
            if src == "bin" or glob.has_magic(src):
                paths = glob.glob(os.path.join(ROOT, src, "*.py") if src == "bin" else os.path.join(ROOT, src))
                out |= {m.group(1) for m in (re.fullmatch(r"(of_\w+)\.py", os.path.basename(x)) for x in paths) if m}
            else:
                m = re.fullmatch(r"bin/(of_\w+)\.py", src)
                if m:
                    out.add(m.group(1))
    return out


# A module the image lacks, imported inside a function of one it holds: where that runs, and why the image does
# without it. "module.*": every function of that module. A new one fails the test until it is copied or listed here.
NOT_IN_IMAGE = {
    "of_systemd": {"of_server.install_systemd": "`of server install` on a Linux host; the image runs under compose or "
                                                "Kubernetes, never under the user's systemd",
                   "of_server.systemd_units": "the same: written only by `of server install` on a Linux host"},
    "of_client": {"of_ship._ship_server": "`of ship`'s client side; the server uses of_ship's batch code only",
                  "of_decisions._default_post": "a machine sends its proposals to its organization's server; the image "
                                                "accepts decisions and never proposes one",
                  "of_events.card_ref": "a machine asks its organization's server for a card id; in the image no "
                                        "orgs.yaml names a server, so the import is never reached, and it sits in a "
                                        "try that files the ask without an id"},
    "of_guard": {"of_credentials.report": "`of credentials`, on a machine",
                 "of_grants.shape_warnings": "`of registry check`, on a machine"},
    "of_hook": {f"{m}.{f}": "inside a try that goes on without the collector"
                for m, f in (("of_events", "card_prefix"), ("of_events", "record"), ("of_route", "_org_by_session"),
                             ("of_route", "_own_org_by_session"))},
    "of_keeper": {"of_access._approval_notes": "`of registry check`, on a machine; the server uses the limit checks "
                                                "only"},
    "of_pulse": {"of_access.send_digests": "the listener's access digest, on a machine"},
    "of_relay_client": {"of_server.host_watchdog": "the agent host's watchdog, run from its checkout, not the image"},
}
# Studio's pages (of_studio's decision which of them the image serves: of_server's message 01a1f369-887d).
STUDIO_FAILS = ("imported outside a try, so a page that reaches it answers 500 in the image whenever it is built: every "
                "page built on Data reaches of_loops (Data.loop_rows), so a Data page answers 500 on a cache miss, "
                "and always with a server (ClickHouse) source or for a unit's viewer, which bypass the cache; a "
                "cached page and the static files answer 200 (of_review's probes on organic-framework PRs 370, 373 "
                "and 376; the image before PR 370 failed the same way)")
STUDIO_DEGRADES = "imported inside a guard that goes on without it: cached usage, no spend, no fold view, a page that says so"
for _m, _by, _why in (("of_loop", "of_model", STUDIO_FAILS), ("of_loops", "of_model of_studio", STUDIO_FAILS),
                      ("of_router", "of_model", STUDIO_FAILS), ("of_works", "of_studio", STUDIO_FAILS),
                      ("of_capacity", "of_studio", STUDIO_DEGRADES), ("of_spend", "of_studio", STUDIO_DEGRADES),
                      ("of_studio_health", "of_studio", STUDIO_DEGRADES),
                      ("of_studio_views", "of_studio", STUDIO_DEGRADES)):
    NOT_IN_IMAGE.setdefault(_m, {}).update({f"{b}.*": _why for b in _by.split()})


def _imported(node):
    """The of_* modules of this repository that one AST node imports: `import`, `from … import`, or a call of
    importlib.import_module or __import__ with a literal name."""
    if isinstance(node, ast.Import):
        names = [a.name for a in node.names]
    elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
        names = [node.module]
    elif (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)
          and isinstance(node.args[0].value, str)
          and ast.unparse(node.func) in ("importlib.import_module", "import_module", "__import__")):
        names = [node.args[0].value]
    else:
        return set()
    names = {n.split(".")[0] for n in names}
    return {n for n in names if n.startswith("of_") and os.path.exists(os.path.join(BIN, f"{n}.py"))}


def _parse(module):
    with open(os.path.join(BIN, f"{module}.py"), encoding="utf-8") as fh:
        return ast.parse(fh.read())


FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def function_imports(module):
    """{(imported of_* module, "module.qualname")} for each import that runs only when a function of MODULE runs,
    under the innermost function that holds it ("Class.method" for a method)."""
    out = set()

    def visit(node, scope, in_fn):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, FUNCTIONS):
                visit(child, scope + [getattr(child, "name", "<lambda>")], True)
            elif isinstance(child, ast.ClassDef):
                visit(child, scope + [child.name], in_fn)
            else:
                if in_fn:
                    out.update((name, f"{module}.{'.'.join(scope)}") for name in _imported(child))
                visit(child, scope, in_fn)

    visit(_parse(module), [], False)
    return out


def load_imports(module):
    """The of_* modules of this repository that MODULE imports when it loads: every import outside a function, at
    module level, in a try, an if, a with or a class body there."""
    out = set()

    def visit(node):
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, FUNCTIONS):
                out.update(_imported(child))
                visit(child)

    visit(_parse(module))
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

    def test_the_import_scans_see_what_runs_where(self):
        """of_review's note 4 on organic-framework PR 370: importlib and __import__, a class body, a nested function,
        two classes' methods of one name."""
        src = ("import importlib\nclass K:\n    import of_events\n    def run(self):\n        import of_mask\n"
               "class L:\n    def run(self):\n        import of_route\n"
               "def outer():\n    def inner():\n        importlib.import_module('of_store')\n    __import__('of_ship')\n")
        tmp = tempfile.mkdtemp(prefix="of-scan-")
        self.addCleanup(shutil.rmtree, tmp, True)
        with open(os.path.join(tmp, "of_scan_probe.py"), "w") as fh:
            fh.write(src)
        global BIN
        real, BIN = BIN, tmp
        try:
            for m in ("of_events", "of_mask", "of_route", "of_store", "of_ship"):
                open(os.path.join(tmp, f"{m}.py"), "w").close()
            fns, loads = function_imports("of_scan_probe"), load_imports("of_scan_probe")
        finally:
            BIN = real
        self.assertEqual(fns, {("of_mask", "of_scan_probe.K.run"), ("of_route", "of_scan_probe.L.run"),
                               ("of_store", "of_scan_probe.outer.inner"), ("of_ship", "of_scan_probe.outer")})
        self.assertEqual(loads, {"of_events"})

    def test_only_copy_instructions_count(self):
        text = ("# bin/of_hook.py is the client's\nCOPY bin/of_a.py \\\n     bin/of_b.py ./\n"
                "COPY --from=keygen bin/of_c.py ./\nRUN cp bin/of_d.py /tmp\n")
        self.assertEqual(copied(text), {"of_a", "of_b"})
        self.assertIn("of_stacks", copied())

    def test_copy_is_read_as_docker_reads_it(self):
        """of_review's note 3 on organic-framework PR 370: a comment inside a continued COPY, a lowercase copy, a glob."""
        self.assertEqual(copied("COPY bin/of_a.py \\\n# bin/of_y.py\n     bin/of_x.py ./\n"), {"of_a", "of_x"})
        self.assertEqual(copied("copy bin/of_a.py ./\n"), {"of_a"})
        self.assertEqual(copied("COPY bin/of_stack*.py ./\n"), {"of_stacks"})
        self.assertEqual(copied('COPY --chown=of ["bin/of_a.py", "bin/of_b.py", "./"]\n'), {"of_a", "of_b"})
        self.assertEqual(copied("COPY bin/of_a.py /app/bin/of_z.py\n"), {"of_a"})  # a destination copies nothing
        self.assertIn("of_server", copied("COPY bin/ ./\n"))
        self.assertEqual(copied("COPY\n"), set())

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
