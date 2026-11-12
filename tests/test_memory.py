"""A position's memory directory (RFC 0006 §2.1): the scope from the registry, the directory, the launch flag."""

import importlib.machinery
import importlib.util
import json
import os
import shlex
import stat
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_memory  # noqa: E402
import of_spawn  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("of_cli", os.path.join(HERE, "..", "bin", "of"))
_spec = importlib.util.spec_from_loader("of_cli", _loader)
of = importlib.util.module_from_spec(_spec)
_loader.exec_module(of)

UNITS = {"shop": {"owner": "acme_throughput-owner"}, "platform": {"owner": "platform_owner", "memory": "unit"}}


def p(name="storefront_checkout", unit="shop", org_memory=None, **kw):
    d = {"name": name, "_org": "acme", "_orgid": "acme", "_unit": unit, "_units": UNITS,
         "_unit_owner": (UNITS.get(unit) or {}).get("owner"), "_memory_org": org_memory}
    d.update(kw)
    return d


class ScopeTest(unittest.TestCase):
    def test_the_nearest_setting_wins_and_the_default_is_the_position(self):
        self.assertEqual(of_memory.scope_id(p()), "storefront_checkout")
        self.assertEqual(of_memory.scope_id(p(org_memory="org")), "org")
        self.assertEqual(of_memory.scope_id(p(org_memory="unit")), "unit-shop")
        self.assertEqual(of_memory.scope_id(p("k8s_keeper", "platform", org_memory="org")), "unit-platform")  # unit
        self.assertEqual(of_memory.scope_id(p("k8s_keeper", "platform", memory="position")), "k8s_keeper")  # position
        self.assertEqual(of_memory.scope_id(p("acme_org", None, memory="unit")), "acme_org")  # no unit: its own
        self.assertEqual(of_memory.scope_id(p(memory="team")), "storefront_checkout")  # a bad value is the default
        self.assertTrue(of_memory.directory(p(), base="/m").endswith("/m/acme/storefront_checkout"))

    def test_the_shared_index_has_an_owner(self):
        positions = [p("acme_org", None, role="org"), p()]
        self.assertEqual(of_memory.owner(p(), positions), "storefront_checkout")
        self.assertEqual(of_memory.owner(p(org_memory="unit"), positions), "acme_throughput-owner")
        self.assertEqual(of_memory.owner(p(org_memory="org"), positions), "acme_org")

    def test_the_directory_is_private_and_named_in_the_launch_flag(self):
        with tempfile.TemporaryDirectory() as base:
            flag = of_memory.settings(p(), base=base)
            d = json.loads(flag)["autoMemoryDirectory"]
            self.assertEqual(d, os.path.join(base, "acme", "storefront_checkout"))
            for x in (d, os.path.dirname(d)):
                self.assertEqual(stat.S_IMODE(os.stat(x).st_mode), 0o700)
            cmd = of_spawn.launch_command("/w/store", "/u/.claude-e", "sid", "storefront_checkout", "/u/.claude",
                                          memory=flag, state_dir=base)
            argv = shlex.split(cmd)
            with open(argv[argv.index("--settings") + 1]) as fh:  # a settings file: the line stays short (of_spawn)
                self.assertEqual(json.load(fh), {"autoMemoryDirectory": d})
            self.assertNotIn("--settings", of_spawn.launch_command("/w", "/u/.c", "sid", "n", "/u/.claude"))

    def test_the_index_warns_before_the_cut(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(of_memory.index_size(d), (0, 0))
            with open(os.path.join(d, "MEMORY.md"), "w") as fh:
                fh.write("- line\n" * 180)
            self.assertFalse(of_memory.over(d))
            with open(os.path.join(d, "MEMORY.md"), "a") as fh:
                fh.write("- one more")
            self.assertEqual(of_memory.index_size(d)[0], 181)
            self.assertTrue(of_memory.over(d))
            with open(os.path.join(d, "MEMORY.md"), "w") as fh:
                fh.write("x" * 22001)
            self.assertTrue(of_memory.over(d))


class RegistryMemoryTest(unittest.TestCase):
    def test_the_check_flags_a_bad_scope_at_each_level(self):
        with tempfile.TemporaryDirectory() as root:
            def write(rel, text):
                path = os.path.join(root, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as fh:
                    fh.write(textwrap.dedent(text))
            write("acme/registry/positions.yaml", """
                organization: acme
                memory: team
                units:
                  store: {owner: acme_a, systems: [s], memory: everyone}
                positions:
                  - {name: acme_a, system: s, role: a, unit: store, memory: unit}
                  - {name: acme_b, system: s, role: b, memory: folder}
                """)
            write("conf/orgs.yaml", "orgs:\n  - {name: acme, path: %s/acme}\n" % root)
            positions, problems = of.load_positions(os.path.join(root, "conf/orgs.yaml"))
            text = "\n".join(problems)
            self.assertIn("acme:registry/positions.yaml: memory 'team' is not one of position, unit, org", text)
            self.assertIn("units: store: memory 'everyone'", text)
            self.assertIn("acme_b: memory 'folder'", text)
            self.assertNotIn("acme_a: memory", text)
            self.assertEqual(of_memory.scope_id(next(x for x in positions if x["name"] == "acme_a")), "unit-store")


if __name__ == "__main__":
    unittest.main()


class SessionStartWarningTest(unittest.TestCase):
    """RFC 0006 §2.3: an index past 180 lines or 22,000 bytes is named at session start, once a day per index."""

    def test_once_a_day_for_the_indexes_the_position_keeps(self):
        import of_hook
        with tempfile.TemporaryDirectory() as tmp:
            saved = of_hook.STATE, of_hook.OUTPUT, of_hook._runs_on_own_memory
            of_hook.STATE, of_hook.OUTPUT = tmp, None
            of_hook._runs_on_own_memory = lambda: True
            try:
                own, unit, small = (os.path.join(tmp, "m", x) for x in ("storefront_checkout", "unit-shop", "x"))
                for d, n in ((own, 190), (unit, 250), (small, 20)):
                    os.makedirs(d)
                    with open(os.path.join(d, "MEMORY.md"), "w") as fh:
                        fh.write("- a lesson\n" * n)
                index = {"sessions": {"S1": ["storefront_checkout", "acme"], "S2": ["acme_throughput-owner", "acme"]},
                         "memory": {"storefront_checkout": [[own, "acme_org"], [small, "acme_org"]],
                                    "acme_throughput-owner": [[unit, "acme_org"]]}}
                of_hook._memory_warning({"session_id": "S1", "source": "startup"}, index, now=1_800_000_000)
                ctx = of_hook.OUTPUT["hookSpecificOutput"]["additionalContext"]
                self.assertIn("Your memory index", ctx)
                self.assertIn("190 lines", ctx)
                self.assertIn("send the rules that should last to acme_org", ctx)
                self.assertNotIn("/x/", ctx)  # 20 lines: nothing to say
                of_hook.OUTPUT = None
                of_hook._memory_warning({"session_id": "S1", "source": "compact"}, index, now=1_800_000_000 + 3600)
                self.assertIsNone(of_hook.OUTPUT)  # the same day: once
                of_hook._memory_warning({"session_id": "S1", "source": "resume"}, index, now=1_800_000_000 + 86400)
                self.assertIn("190 lines", of_hook.OUTPUT["hookSpecificOutput"]["additionalContext"])  # next day
                of_hook.OUTPUT = {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "brief"}}
                of_hook._memory_warning({"session_id": "S2", "source": "startup"}, index, now=1_800_000_000)
                ctx = of_hook.OUTPUT["hookSpecificOutput"]["additionalContext"]
                self.assertTrue(ctx.startswith("brief\n\nMemory: The shared memory index you keep"))  # the owner's
                of_hook.OUTPUT = None
                of_hook._runs_on_own_memory = lambda: False  # still on the folder memory: it reads none of these
                of_hook._memory_warning({"session_id": "S1", "source": "startup"}, index, now=1_800_000_000 + 9 * 86400)
                self.assertIsNone(of_hook.OUTPUT)
            finally:
                of_hook.STATE, of_hook.OUTPUT, of_hook._runs_on_own_memory = saved
