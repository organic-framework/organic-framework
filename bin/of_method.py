"""OF's reading of a package (docs/bench.md, "OF's part"; organic-framework-build reports/framework/metamethod,
10-metamethod-v2.md sections 3 to 6, and 11-first-slice.md parts 4 and 5).

Bench (organic-framework/bench, bin/bench.py) knows packages; this module knows what OF writes in them: a method's `main.lua` (modules,
settings, what may be extended), an organization's tuning (`config/settings/<package>.lua`) and its hooks
(`extensions/<package>/*.lua`). At install it reads them all in Bench's closed reader, refuses a setting of the wrong
type and a hook on a module that may not be extended, and gives Bench what the lock records. At work it gives a
package's settings as installed, and calls the organization's hooks before a module, each returning a decision.
"""

import hashlib
import os
import re

import of_benchpath

try:   # Bench's own checkout (organic-framework/bench), at the path OF's configuration names
    of_benchpath.ensure()
    import bench
    from bench import BenchError
    _MISSING = None
except ImportError as _e:   # OF runs without it; each function here then says where Bench was looked for
    bench = None
    _MISSING = str(_e)

    class BenchError(Exception):
        pass


def _need():
    if bench is None:
        raise BenchError(_MISSING)

# the words of a method's main.lua and its lib/ files
METHOD_WORDS = {"decl": ["roles", "subject", "setting", "module", "extendable", "event"],
                "value": ["skill", "tool", "command", "record", "schedule", "turn_end", "act", "role", "classifier"]}
# the words of an organization's hook file
WHENS = ("before", "after", "around", "instead")
HOOK_WORDS = {"decl": list(WHENS), "value": []}

SETTINGS_DIR = os.path.join("config", "settings")
EXTENSIONS_DIR = "extensions"

DURATION = re.compile(r"(?:\d+[smhdw])+")
LANGUAGE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")
UNIT_S = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
TYPES = ("string", "int", "number", "bool", "duration", "language", "enum")
DECISIONS = ("pass", "stop", "hand", "hold")


# ---- reading a method ----

def read_method(folder, spec):
    """A package's main.lua as {"settings": {name: {type, default, values}}, "modules": {name: body},
    "extendable": [...]}; a package with no main.lua has an empty method."""
    _need()
    main = spec.get("main") or "main.lua"
    if not os.path.isfile(os.path.join(folder, main)):
        return {"settings": {}, "modules": {}, "extendable": []}
    r = bench.evaluate(bench.lua_sources(folder), main, METHOD_WORDS, name=f"{spec['name']}/{main}")
    settings, modules, extendable = {}, {}, []
    for d in r["decls"]:
        w, name, body = d.get("word"), d.get("name"), d.get("body")
        if w == "setting":
            settings[name] = _setting_decl(spec["name"], name, body or {})
        elif w == "module":
            if not name:
                raise BenchError(f"{spec['name']}/{main}: a module needs a name")
            modules[name] = body or {}
        elif w == "extendable":
            extendable += [x for x in (body or []) if isinstance(x, str)]
    for m in extendable:
        if m not in modules:
            raise BenchError(f"{spec['name']}/{main}: extendable names \"{m}\", which is no module")
    return {"settings": settings, "modules": modules, "extendable": sorted(set(extendable))}


def _setting_decl(pkg, name, body):
    if not name:
        raise BenchError(f"{pkg}: a setting needs a name")
    t = body.get("type")
    if t not in TYPES:
        raise BenchError(f"{pkg}: setting \"{name}\" has type {t!r}; the types are {', '.join(TYPES)}")
    decl = {"type": t}
    if t == "enum":
        vals = body.get("values")
        if not (isinstance(vals, list) and vals and all(isinstance(v, str) for v in vals)):
            raise BenchError(f"{pkg}: setting \"{name}\" is an enum and needs values = {{ ... }}")
        decl["values"] = vals
    if "default" in body:
        problem = type_problem(decl, body["default"])
        if problem:
            raise BenchError(f"{pkg}: setting \"{name}\": its default {problem}")
        decl["default"] = body["default"]
    return decl


def type_problem(decl, v):
    """None when V is a value of the setting DECL, else what is wrong, in words."""
    t = decl["type"]
    if t == "string" and isinstance(v, str):
        return None
    if t == "int" and isinstance(v, int) and not isinstance(v, bool):
        return None
    if t == "number" and isinstance(v, (int, float)) and not isinstance(v, bool):
        return None
    if t == "bool" and isinstance(v, bool):
        return None
    if t == "duration" and isinstance(v, str) and DURATION.fullmatch(v):
        return None
    if t == "language" and isinstance(v, str) and LANGUAGE.fullmatch(v):
        return None
    if t == "enum" and v in decl.get("values", []):
        return None
    want = {"duration": 'a duration such as "30m", "2h" or "1d"', "language": 'a language code such as "en" or "ru"',
            "enum": "one of " + ", ".join(f'"{x}"' for x in decl.get("values", [])), "int": "a whole number",
            "number": "a number", "bool": "true or false", "string": "a text"}[t]
    return f"is {_show(v)}, not {want}"


def _show(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, (dict, list)):
        return "a table"
    return str(v)


def seconds(text):
    return sum(int(n) * UNIT_S[u] for n, u in re.findall(r"(\d+)([smhdw])", text))


# ---- the organization's tuning ----

def settings_file(project, package):
    return os.path.join(project, SETTINGS_DIR, f"{package}.lua")


def read_settings(project, package, method):
    """The organization's values for PACKAGE checked against its method's settings: ({name: value} with defaults
    filled in, the file's digest or None)."""
    _need()
    decls = method["settings"]
    values = {k: d["default"] for k, d in decls.items() if "default" in d}
    p = settings_file(project, package)
    if not os.path.isfile(p):
        return values, None
    rel = os.path.relpath(p, project)
    r = bench.read_file(p, bench.NO_WORDS, name=rel)
    got = r.get("returned")
    if got is None or got == {} or got == []:
        got = {}
    if not isinstance(got, dict):
        raise BenchError(f"{rel}: returns a table of settings, return {{ name = value }}")
    for k, v in sorted(got.items()):
        if k not in decls:
            known = ", ".join(sorted(decls)) or "none"
            raise BenchError(f"{rel}: \"{k}\" is no setting of {package} (its settings: {known})")
        problem = type_problem(decls[k], v)
        if problem:
            raise BenchError(f"{rel}: {k} {problem}")
        values[k] = v
    return values, bench.file_digest(p)


# ---- the organization's hooks ----

def hook_files(project, package):
    _need()
    d = os.path.join(project, EXTENSIONS_DIR, package)
    if not os.path.isdir(d):
        return []
    out = []
    for n in sorted(os.listdir(d)):
        p = os.path.join(d, n)
        if n.endswith(".lua") and os.path.isfile(p) and not os.path.islink(p):
            out.append(p)
        elif n.endswith(".lua"):
            raise BenchError(f"{os.path.relpath(p, project)}: a hook file is a regular file, not a link")
    return out


def read_hooks(project, package, method, version):
    """The organization's hooks on PACKAGE: [{file, digest, when, module, at}] in the order they run (file name,
    then place in the file). A hook on a module that is not extendable, or written for another major version, is
    refused."""
    _need()
    major = version.split(".")[0]
    out = []
    for p in hook_files(project, package):
        rel = os.path.relpath(p, project).replace(os.sep, "/")
        r = bench.evaluate({"hook.lua": bench._read_text(p)}, "hook.lua", HOOK_WORDS, name=rel)
        digest = bench.file_digest(p)
        for i, d in enumerate(r["decls"], 1):
            when, module, body = d.get("word"), d.get("name"), d.get("body") or {}
            if not module:
                raise BenchError(f"{rel}: {when} names the module it hooks, {when} \"file\" {{ ... }}")
            if module not in method["modules"]:
                raise BenchError(f"{rel}: {package} has no module \"{module}\"")
            if module not in method["extendable"]:
                raise BenchError(f"{rel}: {package}'s module \"{module}\" may not be extended (extendable: "
                                 f"{', '.join(method['extendable']) or 'none'})")
            run = body.get("run")
            if not (isinstance(run, dict) and run.get("@") == "function"):
                raise BenchError(f"{rel}: {when} \"{module}\" needs run = function(input, ctx) ... end")
            wf = body.get("written_for")
            if wf != f"{package} {major}":
                raise BenchError(f"{rel}: written_for is {_show(wf) if wf is not None else 'missing'}; this hook "
                                 f"runs on {package} {version}, so it says written_for = \"{package} {major}\"")
            out.append({"file": rel, "digest": digest, "when": when, "module": module, "at": run["path"]})
    return out


# ---- install and check ----

def on_install(project, name, folder, spec):
    """What the lock records for OF beside Bench's own: the settings as checked and the hooks."""
    _need()
    method = read_method(folder, spec)
    values, digest = read_settings(project, name, method)
    entry = {}
    if digest or values:
        # the names of the duration settings: at work only those are read as seconds (of_bench on PR 575: a text
        # setting set to "5m" read as 300)
        entry["settings"] = {"values": values,
                             "durations": sorted(k for k, d in method["settings"].items() if d["type"] == "duration")}
        if digest:   # as the lock reads back: no key without a value
            entry["settings"].update(file=f"{SETTINGS_DIR}/{name}.lua".replace(os.sep, "/"), digest=digest)
    hooks = read_hooks(project, name, method, spec["version"])
    if hooks:
        entry["hooks"] = hooks
    return entry


def check(project):
    """The organization's settings and hook files that differ from the lock: [(package, path, how)]."""
    _need()
    project = os.path.abspath(project)
    out = []
    for name, e in sorted(bench.read_lock(project).items()):
        p = settings_file(project, name)
        rel = os.path.relpath(p, project)
        want = (e.get("settings") or {}).get("digest")
        have = bench.file_digest(p) if os.path.isfile(p) else None
        if want != have:
            out.append((name, rel, "added since the install" if not want else "removed since the install"
                        if not have else "changed since the install"))
        locked = {h["file"]: h["digest"] for h in e.get("hooks") or []}
        now = {os.path.relpath(f, project).replace(os.sep, "/"): bench.file_digest(f)
               for f in hook_files(project, name)}
        for rel, how in bench.changed(locked, now):
            out.append((name, rel, how + " since the install"))
    return out


# ---- at work ----

def _entry(project, package):
    e = bench.read_lock(project).get(package)
    if e is None:
        raise BenchError(f"{package} is not installed in {project}: run bench install")
    return e


def settings(project, package, parsed=True):
    """PACKAGE's settings as the organization set them at install, defaults filled in; a duration in seconds when
    PARSED. Refused when the settings file changed since the install, so a value nobody checked never acts."""
    _need()
    project = os.path.abspath(project)
    e = _entry(project, package)
    s = e.get("settings") or {}
    p = settings_file(project, package)
    have = bench.file_digest(p) if os.path.isfile(p) else None
    if have != s.get("digest"):
        raise BenchError(f"{os.path.relpath(p, project)} changed since the install: run bench install, which "
                         f"checks it")
    values = dict(s.get("values") or {})
    if parsed:
        for k in s.get("durations") or []:
            v = values.get(k)
            if isinstance(v, str) and DURATION.fullmatch(v):
                values[k] = seconds(v)
    return values


def decision(value, hook):
    """A hook's return value as {decision, by, reason, to, until}; anything it cannot mean stops the module (a hook
    before a module is a gate, and a gate that cannot be read is closed)."""
    by = f"{hook['file']}: {hook['when']} \"{hook['module']}\""
    if value is None or value == "pass":
        return {"decision": "pass", "by": by}
    if isinstance(value, str) and value in DECISIONS:
        value = {"decision": value}
    if isinstance(value, dict) and value.get("decision") in DECISIONS:
        out = {"decision": value["decision"], "by": by}
        for k in ("reason", "to", "until"):
            if isinstance(value.get(k), str):
                out[k] = value[k]
        if out["decision"] == "hand" and "to" not in out:
            return {"decision": "stop", "by": by, "reason": "the hook handed the input on without naming to whom"}
        if out["decision"] == "hold" and "until" not in out:
            return {"decision": "stop", "by": by, "reason": "the hook held the input without naming until what"}
        return out
    return {"decision": "stop", "by": by, "reason": f"the hook returned {_show(value)}, which is no decision "
                                                     f"({', '.join(DECISIONS)})"}


def before(project, package, module, value, ctx=None):
    """Call the organization's hooks before PACKAGE's MODULE with VALUE (the module's input, plain data), in order;
    the first that does not pass decides. Returns {decision, by, reason, to, until} (`by` names the hook file and
    the hook); {"decision": "pass", "by": None} when no hook stands before the module. A hook whose file changed
    since the install, or that the reader stopped, stops the module, with the reason."""
    _need()
    return _run_hooks(project, package, "before", module, value, ctx)


def _run_hooks(project, package, when, module, value, ctx):
    project = os.path.abspath(project)
    e = _entry(project, package)
    hooks = [h for h in e.get("hooks") or [] if h.get("when") == when and h.get("module") == module]
    if not hooks:
        return {"decision": "pass", "by": None}
    ctx = dict(ctx or {})
    ctx.setdefault("package", package)
    ctx.setdefault("settings", (e.get("settings") or {}).get("values") or {})
    for h in hooks:
        p = os.path.join(project, h["file"])
        by = f"{h['file']}: {when} \"{module}\""
        # read once: the text whose digest is checked is the text evaluated (of_bench on PR 575)
        try:
            text = bench._read_text(p)
        except (OSError, BenchError):
            text = None
        if text is None or "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest() != h["digest"]:
            return {"decision": "stop", "by": by, "reason": f"{h['file']} changed since the install; run of bench "
                                                            f"install, which checks it"}
        try:
            r = bench.evaluate({"hook.lua": text}, "hook.lua", HOOK_WORDS, mode="call",
                               path=h["at"], args=(value, ctx), name=h["file"])
        except BenchError as err:
            return {"decision": "stop", "by": by, "reason": str(err)}
        d = decision(r.get("value"), h)
        if d["decision"] != "pass":
            return d
    return {"decision": "pass", "by": None}
