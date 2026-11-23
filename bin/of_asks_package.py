"""of-asks as a Bench package at work (the package: the repository organic-framework/of-asks; Bench:
organic-framework/bench, kept by of_bench, found through bin/of_benchpath.py): the organization's settings and hooks
of the question flow, read only where the organization installed the package. With no Benchfile.lock naming of-asks
in the organization's folder, nothing is imported from Bench, no Lua runs and asking works as before
(organic-framework-build reports/framework/metamethod/11-first-slice.md, parts 4 and 5).

An organization's Lua is evaluated only inside Bench's OS sandbox, which never starts inside an agent's session: a
hook asked for from a Claude Code session is evaluated by the listener, through its hooks door (bin/of_hookdoor.py;
of_server's ruling, message 01a20566-e23c). The pulse, which reads escalate_after, runs in the listener."""
import os

PACKAGE = "of-asks"
ESCALATE_AFTER_S = 2 * 3600  # the package's default for escalate_after (of-asks's main.lua)
LOCK = "Benchfile.lock"      # bench.LOCK, named here so a session or a machine without Bench reads nothing from it
CARD_KEYS = ("question", "options", "tags", "tags_guessed", "to", "from", "blocking", "task", "due")  # the door's own


def _lock_text_names_it(project):
    """Does the organization's Benchfile.lock name of-asks? Read as text, with no Bench and no Lua, and only as a
    regular file reached without a link, opened without waiting, read up to of_context.read_in's limit: the folder holds
    what is pushed to the organization's repository, so a link or a FIFO committed under that name must never make
    every `of ask`, the pulse or the listener wait on it or read what it names (of_review on PR 574). Bench's own
    read_lock reads it the same way. Anything else under that name reads as no lock."""
    if not project:
        return False
    import of_context
    try:
        text = of_context.read_in(project, LOCK)
    except OSError:
        return False
    return text is not None and f'"{PACKAGE}"' in text


def _lock_entry(project):
    """In the listener or a terminal: None when the organization did not install of-asks; else the lock's entry, or
    the error that kept it from being read (Bench or a Lua runtime missing, a broken file)."""
    if not _lock_text_names_it(project):
        return None
    try:
        import of_benchpath
        of_benchpath.ensure()
        import bench
        e = bench.read_lock(project).get(PACKAGE)
    except Exception as err:  # noqa: BLE001 - it names the package but cannot be read
        return err
    return e if e is not None else RuntimeError(f"{LOCK} names {PACKAGE} but holds no entry for it")


def installed(project):
    return _lock_text_names_it(project)


def in_session(env=None):
    """A Claude Code session, whose hook decisions come from the listener. A Codex session's `of ask` already runs
    in the listener through its door (OF_DOOR_CALLER set), and decides in-process as a plain terminal does."""
    env = os.environ if env is None else env
    return bool(env.get("CLAUDE_CODE_SESSION_ID")) and not env.get("OF_DOOR_CALLER")


def before_file(project, card, org=None, ask=None):
    """The organization's hooks before the module "file", for CARD (plain data, the keys of CARD_KEYS).
    {"decision": "pass", "by": None} where of-asks is not installed; else the hooks' decision: from the listener
    through its hooks door in a Claude Code session, else of_method.before in this process. Anything that cannot be
    read or reached stops the card, with the reason: a hook before a module is a gate, and a gate that cannot be read
    stays closed (Bench's docs/bench.md)."""
    if not _lock_text_names_it(project):
        return {"decision": "pass", "by": None}
    card = {k: card[k] for k in CARD_KEYS if k in card}
    if in_session():
        if ask is None:
            import of_hookdoor
            ask = of_hookdoor.ask
        try:
            return ask(org, PACKAGE, "file", card)
        except Exception as err:  # noqa: BLE001 - never a traceback in `of ask`
            return {"decision": "stop", "by": "the hooks door", "reason": f"{type(err).__name__}: {err}"}
    e = _lock_entry(project)
    if isinstance(e, Exception):
        return {"decision": "stop", "by": f"{PACKAGE}'s lock", "reason": f"{LOCK} cannot be read: {e}"}
    try:  # any failure, Bench or of_method missing included, closes the gate: never a traceback in `of ask`
        import of_method
        return of_method.before(project, PACKAGE, "file", card)
    except Exception as err:  # noqa: BLE001
        return {"decision": "stop", "by": f"{PACKAGE}'s hooks", "reason": f"{type(err).__name__}: {err}"}


def escalate_after(project, log=None):
    """Seconds after which an unanswered routed card rises on a bare `ask_unanswered` rule: the organization's
    escalate_after where it installed of-asks, else the package's default. Read by the pulse, in the listener. Any
    failure (a settings file changed since the install, Bench missing) keeps the default and logs it, so the pulse's
    escalation never ends on it (of_platform on PR 574)."""
    e = _lock_entry(project)
    if e is None:
        return ESCALATE_AFTER_S
    try:
        if isinstance(e, Exception):
            raise e
        import of_method
        v = of_method.settings(project, PACKAGE).get("escalate_after")
    except Exception as err:  # noqa: BLE001
        if log:
            log(f"of-asks escalate_after: {type(err).__name__}: {err}; the default {ESCALATE_AFTER_S // 3600}h acts")
        return ESCALATE_AFTER_S
    return v if isinstance(v, int) and v > 0 else ESCALATE_AFTER_S
