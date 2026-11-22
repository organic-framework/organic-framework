"""of-asks as a Bench package at work (the package: the repository organic-framework/of-asks; Bench and of_method:
organic-framework/bench, kept by of_bench): the organization's settings and hooks of the question flow, read only
where the organization installed the package. With no Benchfile.lock naming of-asks in the organization's folder,
nothing is imported from Bench, no Lua runs and asking works as before (organic-framework-build
reports/framework/metamethod/11-first-slice.md, parts 4 and 5)."""
import os

PACKAGE = "of-asks"
ESCALATE_AFTER_S = 2 * 3600  # the package's default for escalate_after (of-asks's main.lua)
LOCK = "Benchfile.lock"      # bench.LOCK, named here so a machine without Bench reads nothing from it


def _lock_names_it(project):
    """None when the organization did not install of-asks; else the lock's entry, or the error that kept it from
    being read (a lock that names the package but cannot be read: Bench or a Lua runtime missing, a broken file)."""
    if not project:
        return None
    p = os.path.join(project, LOCK)
    if not os.path.isfile(p):
        return None
    try:
        import bench
        e = bench.read_lock(project).get(PACKAGE)
    except Exception as err:  # noqa: BLE001 - unreadable: installed only if its text names the package
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                named = f'"{PACKAGE}"' in fh.read()
        except OSError:
            named = False
        return err if named else None
    return e


def installed(project):
    return _lock_names_it(project) is not None


def before_file(project, card):
    """The organization's hooks before the module "file", for CARD (plain data: question, options, tags, to, from,
    blocking, due, task). {"decision": "pass", "by": None} where of-asks is not installed or no hook stands there;
    else of_method.before's result. A lock or a hook that cannot be read stops the card: a hook before a module is
    a gate, and a gate that cannot be read stays closed (Bench's docs/bench.md)."""
    e = _lock_names_it(project)
    if e is None:
        return {"decision": "pass", "by": None}
    if isinstance(e, Exception):
        return {"decision": "stop", "by": f"{PACKAGE}'s lock", "reason": f"Benchfile.lock cannot be read: {e}"}
    import bench
    import of_method
    try:
        return of_method.before(project, PACKAGE, "file", card)
    except bench.BenchError as err:
        return {"decision": "stop", "by": f"{PACKAGE}'s hooks", "reason": str(err)}


def escalate_after(project, log=None):
    """Seconds after which an unanswered routed card rises on a bare `ask_unanswered` rule: the organization's
    escalate_after where it installed of-asks, else the package's default. A settings file changed since the install
    is refused by of_method.settings, so the default acts until `of bench install` checks it."""
    e = _lock_names_it(project)
    if e is None or isinstance(e, Exception):
        return ESCALATE_AFTER_S
    import bench
    import of_method
    try:
        v = of_method.settings(project, PACKAGE).get("escalate_after")
    except bench.BenchError as err:
        if log:
            log(f"of-asks escalate_after: {err}; the default {ESCALATE_AFTER_S // 3600}h acts")
        return ESCALATE_AFTER_S
    return v if isinstance(v, int) and v > 0 else ESCALATE_AFTER_S
