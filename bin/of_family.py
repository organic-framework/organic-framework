"""The plugin mechanism shared by the client and of-server (docs/plugins.md §2; of_architect's hand-over aa628b92).

OF prefers no product. Every place where OF talks to an outside product goes through a family: a small interface
(its method names below) with any number of named implementations.

- **Where implementations come from**, all three the same way:
  - built in: classes OF registers in code (`register(family, name, cls)`), such as of-server's SQLite storage;
  - shipped: `bin/plugins/<family>/<name>.py`, a module whose `PLUGIN` is the class;
  - installed: a Python package's entry point in the group `organic_framework.<family>`.
- **Choosing.** A machine's choices are `~/.config/of/plugins.yaml` (`<family>: <name>`, or `<family>: {name: …,
  <settings>}`). An organization's choice is its registry's `plugins:` key, in the same shape; it wins over the
  machine's for that organization's data. A family with no choice and no default of OF's own is an error that names
  the options: never a silent fallback to a product the owner did not choose (§1).
- **Detecting.** An implementation may define a classmethod `detect()` (True when its product is on this machine);
  `of plugins detect` and `of init` propose from it, and the owner chooses.
- **Safety.** A plugin runs with OF's own rights. The guard denies agents writes to the code it loads
  (`code_paths()`): an agent cannot plant one (§2, with of_server).

A choice's settings reach the implementation as `settings` (a dict); how a family builds an instance is the
family's own (`Family.make`), so of-server's seams keep their constructors unchanged.
"""

import importlib
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SHIPPED = os.path.join(HERE, "plugins")
GROUP = "organic_framework."


class PluginError(Exception):
    pass


class Family:
    """One family: its name, its methods, who keeps it, and OF's own default when it has one (only for what is not
    an outside product: of-server's SQLite, disk, file system and machine keys)."""

    def __init__(self, name, methods, owner, doc, default=None):
        self.name, self.methods, self.owner, self.doc, self.default = name, tuple(methods), owner, doc, default

    def make(self, cls, settings, **kw):
        """An instance from a class and the choice's settings; families with other constructors pass their own
        arguments through `kw` (of-server's seams)."""
        if kw:
            return cls(**kw)
        try:
            return cls(settings or {})
        except TypeError:
            return cls()


FAMILIES = {}
_BUILTIN = {}


def define(name, methods, owner, doc, default=None):
    FAMILIES[name] = Family(name, methods, owner, doc, default)
    _BUILTIN.setdefault(name, {})
    return FAMILIES[name]


# docs/plugins.md §3. The methods are the outline the families' owners fill in as they convert their part.
# "secret_stores", not "secrets": a folder or package named secrets is a secret path for the guard (G1.1) and Claude
# Code's native deny rules, so no agent could read or edit the implementations (of_server, 2026-11-11). The old key
# `secrets:` in plugins.yaml or a registry's plugins: is read as this family (ALIASES).
define("secret_stores", ("read", "write", "exists", "list", "bootstrap"), "of_server",
       "where credentials live; the broker reads and writes through it")
define("runtime", ("agents", "read", "deliver", "label", "focus"), "of_herdr",
       "the panes agents run in: list, read, deliver text when idle, label, focus")
define("harness", ("start", "resume", "install_hooks", "transcripts", "accounts"), "of_platform",
       "the agent program: start and resume a session, its hooks, its transcripts, its accounts")
define("code_host", ("merged", "authors", "pr"), "of_platform",
       "work records: merged changes and their authors")
define("service", ("install", "start", "stop", "status"), "of_platform", "OF's background services")
define("notify", ("notify",), "of_asks", "reaching a person when something waits on them")
define("model", ("judge",), "of_platform", "the small judges: questions, mandate fit, the case, corrections")
define("storage", ("insert", "describe"), "of_server", "of-server's store", default="sqlite")
define("buffer", ("put", "get", "done", "dead", "revive"), "of_server", "of-server's queue", default="disk")
define("blobs", ("put", "get"), "of_server", "of-server's files", default="fs")
define("identity", ("verify",), "of_server", "of-server's machine identity", default="machine_keys")


ALIASES = {"secrets": "secret_stores"}


def canonical(family):
    return ALIASES.get(family, family)


def register(family, name, cls):
    """A built-in implementation, registered by OF's own code."""
    if family not in FAMILIES:
        raise PluginError(f"no family {family!r}; families: {', '.join(sorted(FAMILIES))}")
    _BUILTIN[family][name] = cls


def _server_builtins(family):
    """of-server's seams register their built-ins when bin/of_plugins.py loads; load it on first need, so the client
    (of init --plugin storage=sqlite, of plugins) sees them too (of_onboarding, 2026-11-11)."""
    if family in SERVER_FAMILIES and not _BUILTIN.get(family):
        try:
            import of_plugins  # noqa: F401
        except Exception:  # noqa: BLE001 - a server module that cannot load leaves the family empty
            pass


def _shipped(family):
    out = {}
    d = os.path.join(SHIPPED, family)
    try:
        names = sorted(f[:-3] for f in os.listdir(d) if f.endswith(".py") and not f.startswith("_"))
    except OSError:
        return out
    for name in names:
        out[name] = ("shipped", os.path.join(d, f"{name}.py"))
    return out


def _entry_points(family):
    try:
        from importlib.metadata import entry_points
        eps = entry_points()
        found = eps.select(group=GROUP + family) if hasattr(eps, "select") else eps.get(GROUP + family, [])
    except Exception:  # noqa: BLE001 - no metadata: no installed plugins
        return {}
    return {ep.name: ("installed", ep) for ep in found}


def implementations(family):
    """{name: source} for a family: "builtin", "shipped" or "installed". A later source does not shadow an earlier
    one: OF's own built-ins first, then what it ships, then installed packages."""
    family = canonical(family)
    if family not in FAMILIES:
        raise PluginError(f"no family {family!r}; families: {', '.join(sorted(FAMILIES))}")
    _server_builtins(family)
    out = {name: "builtin" for name in _BUILTIN.get(family, {})}
    for name, (src, _) in list(_shipped(family).items()) + list(_entry_points(family).items()):
        out.setdefault(name, src)
    return out


def implementation(family, name):
    """The class of one implementation, or PluginError naming the options."""
    family = canonical(family)
    _server_builtins(family)
    if name in _BUILTIN.get(family, {}):
        return _BUILTIN[family][name]
    shipped = _shipped(family).get(name)
    if shipped:
        spec = importlib.util.spec_from_file_location(f"of_plugin_{family}_{name}", shipped[1])
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        cls = getattr(mod, "PLUGIN", None)
        if cls is None:
            raise PluginError(f"{shipped[1]} defines no PLUGIN")
        return cls
    ep = _entry_points(family).get(name)
    if ep:
        return ep[1].load()
    opts = ", ".join(sorted(implementations(family))) or "none installed"
    raise PluginError(f"no {family} plugin {name!r}; options: {opts}")


SERVER_FAMILIES = ("storage", "buffer", "blobs", "identity")


def machine_choices(path=None, server_path=None):
    """~/.config/of/plugins.yaml: {family: name or {name, settings…}}; of-server's four seams keep their choice in
    server.yaml's `plugins:` (unchanged), which counts as the machine's when plugins.yaml names none."""
    import of_events

    def read(p):
        try:
            import yaml
            with open(p) as fh:
                d = yaml.safe_load(fh) or {}
        except (OSError, ImportError, ValueError):
            return {}
        return d if isinstance(d, dict) else {}
    doc = read(path or of_events.config_path("plugins.yaml"))
    server = read(server_path or of_events.config_path("server.yaml")).get("plugins") or {}
    for fam in SERVER_FAMILIES:
        if fam not in doc and isinstance(server, dict) and server.get(fam):
            doc[fam] = server[fam]
    return doc


def _split(v):
    if isinstance(v, dict):
        return v.get("name"), {k: x for k, x in v.items() if k != "name"}
    return (str(v) if v else None), {}


def choice(family, org_doc=None, machine=None):
    """(name, settings, source) for a family: the organization's `plugins:` first (its own data), then the machine's
    plugins.yaml, then OF's own default; (None, {}, None) when nothing chooses."""
    family = canonical(family)
    keys = [family] + [a for a, c in ALIASES.items() if c == family]
    org = (org_doc or {}).get("plugins") if isinstance((org_doc or {}).get("plugins"), dict) else {}
    hit = next((k for k in keys if org.get(k)), None)
    if hit:
        name, settings = _split(org[hit])
        return name, settings, "organization"
    machine = machine_choices() if machine is None else machine
    hit = next((k for k in keys if machine.get(k)), None)
    if hit:
        name, settings = _split(machine[hit])
        return name, settings, "machine"
    d = FAMILIES[family].default if family in FAMILIES else None
    return (d, {}, "default") if d else (None, {}, None)


def load(family, org_doc=None, machine=None, **kw):
    """An instance of the chosen implementation. No choice: PluginError naming the options and where to choose."""
    family = canonical(family)
    name, settings, _ = choice(family, org_doc, machine)
    if not name:
        opts = ", ".join(sorted(implementations(family))) or "none installed yet"
        raise PluginError(f"no {family} plugin chosen ({FAMILIES[family].doc}); options: {opts}. Choose one in "
                          f"~/.config/of/plugins.yaml ({family}: <name>) or the organization's registry (plugins:)")
    return FAMILIES[family].make(implementation(family, name), settings, **kw)


def detect(family):
    """The implementations whose product is on this machine (their classmethod detect() says so)."""
    family = canonical(family)
    out = []
    for name in implementations(family):
        try:
            cls = implementation(family, name)
            if getattr(cls, "detect", None) and cls.detect():
                out.append(name)
        except Exception:  # noqa: BLE001 - a plugin that cannot load is not detected
            continue
    return out


def propose(machine=None):
    """Lines for `of init` and `of plugins detect`: per family without a choice, what this machine has."""
    machine = machine_choices() if machine is None else machine
    lines = []
    for fam in sorted(FAMILIES):
        name, _, src = choice(fam, None, machine)
        found = detect(fam)
        if src in ("machine", "organization"):
            continue
        if found:
            lines.append(f"{fam}: found {', '.join(found)}; choose with `{fam}: <name>` in ~/.config/of/plugins.yaml")
    return lines


def code_paths():
    """Where the code OF loads as plugins lives: the shipped folder and each installed plugin's package. The guard
    denies agents writes there (docs/plugins.md §2)."""
    out = [SHIPPED]
    try:
        from importlib.metadata import entry_points
        eps = entry_points()
        groups = [g for g in (eps.groups if hasattr(eps, "groups") else eps.keys()) if g.startswith(GROUP)]
        for g in groups:
            for ep in (eps.select(group=g) if hasattr(eps, "select") else eps[g]):
                mod = importlib.util.find_spec(ep.value.split(":", 1)[0].split(".", 1)[0])
                if mod and mod.origin:
                    out.append(os.path.dirname(mod.origin))
    except Exception:  # noqa: BLE001
        pass
    return sorted(set(out))


def check_org(doc):
    """`of registry check` on an organization's `plugins:` key: a mapping of known families to a name or
    {name, settings}. Problems as strings; an implementation not installed here is a warning, since another machine
    of the organization may have it."""
    pl = doc.get("plugins")
    if pl is None:
        return []
    if not isinstance(pl, dict):
        return ["plugins: must map a family to an implementation name or {name: …, settings}"]
    out = []
    for fam, v in pl.items():
        fam = canonical(fam)
        if fam not in FAMILIES:
            out.append(f"plugins: {fam!r} is no family; families: {', '.join(sorted(FAMILIES))}")
            continue
        name, _ = _split(v)
        if not name:
            out.append(f"plugins: {fam}: names no implementation")
        elif name not in implementations(fam):
            out.append(f"warning: plugins: {fam}: {name!r} is not installed on this machine "
                       f"(here: {', '.join(sorted(implementations(fam))) or 'none'})")
    return out
