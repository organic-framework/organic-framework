"""The secret_stores family's contract (docs/agent-secrets.md §13.3; hand-over 3512db81): every implementation writes,
finds, reads and lists by its own reference form, never puts a value in an error or on a command line, and says
whether it keeps positions apart. 1Password and the keychain run against stand-ins of `op` and `security` that keep
their items in memory (and, for 1Password, a token's vaults), so no real store is touched here."""

import json
import os
import shlex
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "bin"))
import of_family  # noqa: E402

FAMILY = "secret_stores"
VALUE = "s3cr3t value with spaces \"quotes\" and \\ backslash"


class R:
    def __init__(self, rc=0, out=""):
        self.returncode, self.stdout, self.stderr = rc, out, ""


class FakeOp:
    """`op` with service-account tokens that each reach some vaults (1Password keeps a token to its vaults)."""

    def __init__(self, tokens):
        self.tokens, self.items, self.argvs = tokens, {}, []

    def __call__(self, argv, env=None, input=None, **k):
        self.argvs.append(argv)
        vaults = self.tokens.get((env or {}).get("OP_SERVICE_ACCOUNT_TOKEN"))
        if vaults is None:
            return R(1)
        args = argv[1:]
        vault = args[args.index("--vault") + 1] if "--vault" in args else None
        if args[0] == "read":
            v, item, field = args[-1][len("op://"):].split("/")
            if v not in vaults or field not in self.items.get((v, item), {}):
                return R(1)
            return R(0, self.items[(v, item)][field])
        if vault not in vaults:
            return R(1)
        if args[:2] == ["item", "get"]:
            item = args[2]
            if (vault, item) not in self.items:
                return R(1)
            return R(0, json.dumps({"title": item, "fields": [{"id": f, "label": f, "value": v}
                                                              for f, v in self.items[(vault, item)].items()]}))
        if args[:2] == ["item", "edit"]:
            doc = json.loads(input)
            self.items[(vault, args[2])] = {f["label"]: f["value"] for f in doc["fields"]}
            return R(0, "")
        if args[:2] == ["item", "list"]:
            return R(0, json.dumps([{"title": i} for (v, i) in self.items if v == vault]))
        return R(1)


class FakeSecurity:
    """`security` with generic passwords in memory, and `-i` reading its commands from standard input."""

    def __init__(self):
        self.items, self.argvs = {}, []

    def __call__(self, argv, input=None, **k):
        self.argvs.append(argv)
        args = argv[1:]
        if args[:1] == ["-i"]:
            words = shlex.split(input.strip())
            if words[0] == "add-generic-password":
                s, a = words[words.index("-s") + 1], words[words.index("-a") + 1]
                self.items[(s, a)] = bytes.fromhex(words[words.index("-X") + 1]).decode()
                return R(0)
            return R(1)
        if args[0] == "find-generic-password":
            key = (args[args.index("-s") + 1], args[args.index("-a") + 1])
            if key not in self.items:
                return R(44)
            return R(0, self.items[key] + "\n" if "-w" in args else 'keychain: "x"\n')
        if args[0] == "dump-keychain":
            out = []
            for (s, a) in self.items:
                out += ['keychain: "/tmp/x.keychain-db"', 'class: "genp"', "attributes:",
                        f'    "acct"<blob>="{a}"', f'    "svce"<blob>="{s}"']
            return R(0, "\n".join(out) + "\n")
        return R(1)


def fixture(name, tmp):
    """(store, identity, a reference, its container, another position's identity or None)."""
    cls = of_family.implementation(FAMILY, name)
    if name == "envfile":
        return cls({"path": os.path.join(tmp, "lab.env")}), None, "envfile://GRAFANA_TOKEN", "lab.env", None
    if name == "keychain":
        store = cls({})
        store.runner = FakeSecurity()
        return store, None, "keychain://of-acme-obs/grafana-viewer", "of-acme-obs", None
    if name == "1password":
        store = cls({"op": "op"})
        store.runner = FakeOp({"tok-obs": {"acme-obs"}, "tok-k8s": {"acme-k8s"}})
        store.runner.items[("acme-obs", "grafana-viewer")] = {"credential": "old"}
        return store, "tok-obs", "op://acme-obs/grafana-viewer/credential", "acme-obs", "tok-k8s"
    raise AssertionError(f"no contract fixture for {FAMILY}/{name}: add one when you add the implementation")


class SecretStoreContract:
    """Mixed into one TestCase per implementation (below)."""
    name = None

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store, self.who, self.ref, self.container, self.other = fixture(self.name, self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_exists_read_list(self):
        self.store.write(self.ref, VALUE, self.who)
        self.assertTrue(self.store.exists(self.ref, self.who))
        self.assertEqual(self.store.read(self.ref, self.who), VALUE)
        self.assertEqual(self.store.container(self.ref), self.container)
        names = self.store.list(self.container, self.who)
        self.assertTrue(names and all(VALUE not in n for n in names))       # names only

    def test_a_value_is_never_on_a_command_line_or_in_an_error(self):
        self.store.write(self.ref, VALUE, self.who)
        for argv in getattr(self.store.runner, "argvs", []) if hasattr(self.store, "runner") else []:
            self.assertFalse(any(VALUE in str(a) or VALUE.encode().hex() in str(a) for a in argv), argv)
        bad = self.ref[:-1] + ("X" if not self.ref.endswith("X") else "Y")
        with self.assertRaises(Exception) as e:
            self.store.read(bad, self.who)
        self.assertNotIn(VALUE, str(e.exception))

    def test_a_foreign_reference_is_refused(self):
        with self.assertRaises(Exception):
            self.store.read("nostore://x/y", self.who)

    def test_the_boundary_is_what_it_claims(self):
        self.store.write(self.ref, VALUE, self.who)
        self.assertIsInstance(self.store.ENFORCES_BOUNDARY, bool)
        if self.store.ENFORCES_BOUNDARY:   # another position's identity does not reach this container
            with self.assertRaises(Exception):
                self.store.read(self.ref, self.other)

    def test_bootstrap_names_where_the_identity_goes(self):
        out = self.store.bootstrap("acme", "obs_keeper", [self.container], [], "/srv/of/identities")
        self.assertTrue(out["steps"])
        if self.store.ENFORCES_BOUNDARY:
            self.assertEqual(out["identity_file"], "/srv/of/identities/acme/obs_keeper.token")
            self.assertTrue(all(VALUE not in s for s in out["steps"]))

    def test_its_scheme_and_audit_are_stated(self):
        self.assertTrue(self.ref.startswith(self.store.SCHEME + "://"))
        self.assertTrue(self.store.AUDIT)


for _name in sorted(of_family.implementations(FAMILY)):
    _cls = f"SecretStore{_name.title().replace('_', '')}Contract"
    globals()[_cls] = type(_cls, (SecretStoreContract, unittest.TestCase), {"name": _name})


class GuardCoversTheStores(unittest.TestCase):
    """docs/agent-secrets.md §13.6, hand-over 3512db81: the guard names every store's commands. Each implementation
    declares its own command line (CLI) and the forms that print a stored value (VALUE_COMMANDS); the guard denies
    them on an agent's command line, so a store cannot ship without its rules."""

    def test_every_store_command_is_denied_to_agents(self):
        import of_guard
        for name in sorted(of_family.implementations(FAMILY)):
            cls = of_family.implementation(FAMILY, name)
            cmds = [f"{cls.CLI} item list"] if getattr(cls, "CLI", None) else []
            cmds += [" ".join(form) + " x" for form in cls.VALUE_COMMANDS]
            for cmd in cmds:
                self.assertTrue(of_guard.critical_deny("Bash", {"command": cmd}, "/tmp"), f"{name}: {cmd}")


class FamilyTest(unittest.TestCase):
    def test_the_three_ship(self):
        self.assertTrue({"1password", "keychain", "envfile"} <= set(of_family.implementations(FAMILY)))
        self.assertEqual(of_family.FAMILIES[FAMILY].owner, "of_server")


if __name__ == "__main__":
    unittest.main()
