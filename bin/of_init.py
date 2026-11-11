"""of init and of hooks: from zero to "my agents in `of who`, with names and recorded events".

`of hooks install` adds the recorder hooks to a Claude Code config dir: additive only, shows
the diff, asks before writing, keeps a backup, and is idempotent. `of init` walks a new user
through prerequisites, a first organization, its structure from the onboarding package (the owner's
conductor first, then the onboarding unit and its run, with no agent started; bench/onboarding,
docs/onboarding.md), the agents already running, the hooks and a profile-1 server, in that order.
Target: under 15 minutes (docs/success.md, goal 1).
"""

import copy
import difflib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
OF = os.path.join(HERE, "of")
HOOK_EVENTS = ["SessionStart", "SessionEnd", "UserPromptSubmit", "Stop", "StopFailure", "PreCompact",
               "Notification", "PermissionRequest", "PermissionDenied"]
TOOL_HOOKS = {"PreToolUse": "AskUserQuestion", "PostToolUse": "*", "PostToolUseFailure": "*"}
MARK = " hook "  # how an of hook command is recognized: "<of> hook <Event>"


class NoTTY(Exception):
    """A confirmation was needed but stdin is not a terminal."""


def _ask(question, yes):
    if yes:
        return True
    if not sys.stdin.isatty():  # lab-quickstart Q2: a hidden prompt silently skipped the hooks
        raise NoTTY(f"{question} needs a yes, but stdin is not a terminal: run it in a terminal, or pass --yes")
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes", "д", "да")
    except EOFError:
        return False


# -------------------------------------------------------------------- the owner's language

# The language the agents use with the head: one explicit setting, asked at init (the founder, 2026-11-11: a new
# organization's conductor talked Russian, and nothing set its language). Kept in ~/.config/of/owner.yaml `language`,
# which of spawn reads for the owner's tasks; written into the conductor's brief, the lead's and the charter.
LANGUAGES = {"en": "English", "ru": "Russian", "uk": "Ukrainian", "de": "German", "fr": "French", "es": "Spanish",
             "it": "Italian", "pt": "Portuguese", "tr": "Turkish", "ar": "Arabic", "zh": "Chinese", "ja": "Japanese"}
_NAMES = dict({v.lower(): k for k, v in LANGUAGES.items()}, **{"русский": "ru", "english": "en"})


def language_code(text):
    """A language code for a code, an English name or the language's own name ("ru", "Russian" -> "ru"); an unknown
    one is kept as given."""
    s = str(text or "").strip()
    low = s.lower()
    return low if low in LANGUAGES else _NAMES.get(low, s)


def language_name(code):
    return LANGUAGES.get(code, code)


def owner_settings(path=None):
    import yaml
    path = path or _config_path("owner.yaml")
    try:
        with open(path) as fh:
            doc = yaml.safe_load(fh)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _config_path(name):
    import of_events
    return of_events.config_path(name)


def resolve_language(arg=None, yes=False, path=None, ask=None):
    """(code, changed): --language first, then this machine's owner.yaml, then a question in a terminal, else English.
    ask(prompt) -> answer; None reads stdin when it is a terminal and --yes was not given."""
    current = str(owner_settings(path).get("language") or "").strip()
    if arg:
        code = language_code(arg)
    elif current:
        code = current
    else:
        if ask is None and not yes and sys.stdin.isatty():
            ask = input
        try:
            answer = ask("Which language should your agents use with you? A code or a name (en, ru, …) [en] ") \
                if ask else ""
        except EOFError:
            answer = ""
        code = language_code(answer) or "en"
    return code, code != current


SURFACES = ("herdr", "remote")


def resolve_surface(arg=None, path=None):
    """(surface, changed): how the person reaches the agents (docs/remote.md §1). `herdr` (the default): at the
    machine, in herdr panes; `remote`: only through the conductor's chat (Remote Control, the desktop or mobile app),
    so every agent starts in tmux and the conductor with Remote Control on. --surface first, then owner.yaml."""
    current = str(owner_settings(path).get("surface") or "").strip()
    if arg and arg not in SURFACES:
        raise ValueError(f"--surface must be one of {', '.join(SURFACES)}, not {arg!r}")
    surface = arg or current or "herdr"
    return surface, bool(arg) and arg != current


def write_language(code, path=None):
    """Set `language` in owner.yaml, keeping its other keys."""
    write_owner_setting("language", code, path)


def write_owner_setting(key, value, path=None):
    """Set one key in owner.yaml (language, surface), keeping the others."""
    import yaml
    path = path or _config_path("owner.yaml")
    doc = owner_settings(path)
    doc[key] = value
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write("# The organization's owner on this machine (of init --language, --surface; docs/remote.md)\n")
        yaml.safe_dump(doc, fh, sort_keys=False, allow_unicode=True)


# -------------------------------------------------------------------- hooks

def config_dir(explicit=None):
    return os.path.expanduser(explicit or os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude")


def _is_of(cmd, of_path):
    """An of hook command, with or without the interpreter in front."""
    return isinstance(cmd, str) and (cmd.startswith(of_path + MARK) or f" {of_path}{MARK}" in cmd)


def hook_command(event, of_path=OF, deny_secrets=False):
    """The interpreter of runs under, by absolute path: a PATH whose python3 lacks PyYAML (or anything else)
    can never silently switch the recorder or the guard off. UserPromptSubmit carries --deny-secrets so the
    @mention prompt block (the whole secrets policy) rides the one flag, not an always-on default."""
    cmd = f"{sys.executable} {of_path}{MARK}{event}"
    if deny_secrets and event == "UserPromptSubmit":
        cmd += " --deny-secrets"
    return cmd


# RFC 0002 §4.1, prevention: secret files are not read by agents (Claude Code permission deny rules)
def _deny_secrets():
    import of_classify
    return of_classify.deny_rules()


DENY_SECRETS = _deny_secrets()  # one list with the guard: of_classify.SECRET_GLOBS
DENY_MARK = "of-deny-secrets"
GUARD_MATCHER = "Bash|Read|Grep|Glob|Edit|Write|MultiEdit|NotebookEdit"
GUARD_MCP_MATCHER = "mcp__.*"  # G16 (of_server, 2026-11-11): MCP connectors and browser tools reach outside parties


def with_hooks(settings, of_path=OF, deny_secrets=False, guard=False):
    new = copy.deepcopy(settings)
    hooks = new.setdefault("hooks", {})
    wanted = [(e, None) for e in HOOK_EVENTS] + list(TOOL_HOOKS.items())
    if guard:  # the enforced critical denies run before the tool (a PreToolUse for every risky tool)
        wanted.append(("PreToolUse", GUARD_MATCHER))
        wanted.append(("PreToolUse", GUARD_MCP_MATCHER))  # a second group: existing installs keep the first as is
    if deny_secrets:
        perms = new.setdefault("permissions", {})
        deny = perms.setdefault("deny", [])
        for rule in DENY_SECRETS:
            if rule not in deny:
                deny.append(rule)
        perms[f"_{DENY_MARK}"] = "rules added by of hooks install --deny-secrets; of hooks uninstall removes them"
    for event, matcher in wanted:
        groups = hooks.setdefault(event, [])
        mine = [h for g in groups if g.get("matcher") == matcher for h in g.get("hooks") or []
                if _is_of(h.get("command"), of_path)]
        if mine:
            for h in mine:  # an older install without the interpreter (or the flag) is upgraded in place
                h["command"] = hook_command(event, of_path, deny_secrets)
            continue
        g = {"hooks": [{"type": "command", "command": hook_command(event, of_path, deny_secrets), "timeout": 5}]}
        if matcher:
            g = {"matcher": matcher, **g}
        groups.append(g)
    return new


def without_hooks(settings, of_path=OF):
    new = copy.deepcopy(settings)
    for event, groups in list((new.get("hooks") or {}).items()):
        kept = []
        for g in groups:
            hs = [h for h in g.get("hooks") or [] if not _is_of(h.get("command"), of_path)]
            if hs:
                kept.append(dict(g, hooks=hs))
        if kept:
            new["hooks"][event] = kept
        else:
            del new["hooks"][event]
    perms = new.get("permissions") or {}
    if perms.pop(f"_{DENY_MARK}", None) is not None:
        perms["deny"] = [r for r in perms.get("deny") or [] if r not in DENY_SECRETS]
        if not perms["deny"]:
            perms.pop("deny")
        if not perms:
            new.pop("permissions", None)
    return new


def hooks(action, cfg_dir=None, yes=False, dry_run=False, of_path=OF, out=print, deny_secrets=False, guard=False):
    path = os.path.join(config_dir(cfg_dir), "settings.json")
    try:
        with open(path) as fh:
            text = fh.read()
        settings = json.loads(text) if text.strip() else {}
    except FileNotFoundError:
        text, settings = "", {}
    new = with_hooks(settings, of_path, deny_secrets, guard) if action == "install" else without_hooks(settings, of_path)
    if new == settings:
        out(f"{path}: nothing to change ({'already installed' if action == 'install' else 'not installed'})")
        return 0
    after = json.dumps(new, indent=2, ensure_ascii=False) + "\n"
    before = json.dumps(settings, indent=2, ensure_ascii=False) + "\n" if settings else ""
    out("".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), path, path + " (new)")))
    if dry_run:
        return 0
    if not _ask(f"Write these changes to {path}?", yes):
        out("not changed")
        return 1
    if os.path.exists(path):
        backup = f"{path}.bak-of-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copy2(path, backup)
        out(f"backup: {backup}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as fh:
        fh.write(after)
    os.replace(path + ".tmp", path)
    out(f"{action}ed: {path}")
    if action == "install" and guard:
        ok, detail = guard_works(new)
        out(f"guard self-test: {'ok, ' if ok else 'FAILED, '}{detail}")
        if not ok:
            out("the deny rules still hold (they need no interpreter); fix the guard command above")
            return 1
    return 0


# --------------------------------------------------------------------- init

def _name(text):
    n = re.sub(r"[^a-z0-9_-]+", "-", (text or "").lower()).strip("-_")
    if not n or not n[0].isalpha():
        n = "a" + n
    return n[:32]


def check_prereqs(out):
    ok = True
    v = sys.version_info
    out(f"  python {v.major}.{v.minor}: " + ("ok" if v >= (3, 11) else "need 3.11+"))
    ok &= v >= (3, 11)
    try:
        import yaml  # noqa: F401
        out("  PyYAML: ok")
    except ImportError:
        out("  PyYAML: missing; install with: python3 -m pip install --user pyyaml")
        ok = False
    for tool, need in (("claude", "the Claude Code CLI"), ("ssh-keygen", "OpenSSH (machine keys)"),
                       ("herdr", "herdr (optional: names, panes, the desk)")):
        out(f"  {tool}: " + ("ok" if shutil.which(tool) else f"not found ({need})"))
        if tool != "herdr":
            ok &= bool(shutil.which(tool))
    return ok


def live_agents():
    if not shutil.which("herdr"):
        return []
    try:
        r = subprocess.run(["herdr", "agent", "list"], capture_output=True, text=True, timeout=10)
        return json.loads(r.stdout)["result"]["agents"]
    except Exception:
        return []


def claude_sessions():
    """Live Claude Code sessions in any config dir (`claude agents --json`), shaped like herdr agents."""
    import glob
    out = []
    env = {k: v for k, v in os.environ.items() if not k.startswith("HERDR_")}
    for d in sorted(glob.glob(os.path.expanduser("~/.claude*"))):
        if not os.path.isdir(d):
            continue
        try:
            r = subprocess.run(["claude", "agents", "--json"], env=dict(env, CLAUDE_CONFIG_DIR=d),
                               capture_output=True, text=True, timeout=20)
            items = json.loads(r.stdout or "[]") if r.returncode == 0 else []
        except (OSError, subprocess.SubprocessError, ValueError):
            items = []
        for x in items:
            if x.get("sessionId") and x.get("kind") != "background":
                out.append({"agent": "claude-code", "agent_session": {"value": x["sessionId"]}, "cwd": x.get("cwd"),
                            "session_name": x.get("name")})
    return out


def propose_name(a, taken=()):
    """(name, system, role) for a live agent: a name it already has in the `<system>_<role>` form, else
    system from its working directory's basename and role `dev`: `api_dev`, `api_dev-2`, ..."""
    cwd = (a.get("cwd") or a.get("foreground_cwd") or "").rstrip("/")
    system = _name(os.path.basename(cwd) or "work")[:16].strip("-_") or "work"
    raw_hint = a.get("session_name") or a.get("name") or ""
    hint = _name(raw_hint) if raw_hint else ""
    if "_" in hint and hint not in taken and NAME_RE.match(hint):
        return hint, hint.split("_", 1)[0], hint.split("_", 1)[1]
    role = "dev"  # terminal titles and default names say nothing about the job; the owner renames
    base = f"{system}_{role}"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name, system, role


NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


# ------------------------------------------------- the onboarding package (bench/onboarding)

PACKAGE_DIR = os.path.join(os.path.dirname(HERE), "bench", "onboarding")
PREFIX_MAX = 14  # "<prefix>_system-architect" stays within the 32 characters of a name
SUMMARY_MAX = 40
TOKEN = re.compile(r"\{(org|Org|owner_id|owner_name|language)\}")


def display_name(org_arg):
    """How the organization is called in its documents: the name as typed, capitalised when typed all lowercase."""
    s = (org_arg or "").strip()
    return s[:1].upper() + s[1:] if s.islower() else s


def org_prefix(org):
    """The prefix of the organization's position names and its creating system's id."""
    return org[:PREFIX_MAX].rstrip("-_") or "org"


def fill(value, values):
    """Fill the package's placeholders ({org}, {Org}, {owner_id}, {owner_name}) in a string, list or mapping; any
    other brace stays as it is."""
    if isinstance(value, str):
        return TOKEN.sub(lambda m: values[m.group(1)], value)
    if isinstance(value, list):
        return [fill(v, values) for v in value]
    if isinstance(value, dict):
        return {k: fill(v, values) for k, v in value.items()}
    return value


def load_package(pkg_dir=PACKAGE_DIR):
    import yaml
    with open(os.path.join(pkg_dir, "package.yaml")) as fh:
        return yaml.safe_load(fh)


def build_structure(pkg, values, pkg_dir=PACKAGE_DIR):
    """The registry parts and the files the package gives a new organization, nothing written:
    {people, systems, units, positions (the conductor first), works, files {repo-relative path: text}}.
    values: {org (the prefix), Org, owner_id, owner_name, language (a name; English when absent)}."""
    values = dict({"language": "English"}, **values)

    def read(rel):
        with open(os.path.join(pkg_dir, rel)) as fh:
            return fill(fh.read(), values)
    sysd = fill(pkg["system"], values)
    unit = fill(pkg["unit"], values)
    positions, files = package_positions(pkg, values, pkg_dir)
    run = fill(pkg["run"], values)
    work = {"title": run["title"], "kind": run["kind"], "doc": "onboarding/charter.md", "systems": [values["org"]],
            "owner": run["owner"], "doers": run["doers"], "done_when": run["done_when"],
            "stages": [{"id": s["id"], "title": s["title"], "done_when": s["done_when"], "state": "open"}
                       for s in run["stages"]]}
    for key, rel in (pkg.get("templates") or {}).items():
        files[f"onboarding/{key}.md"] = read(rel)
    files["onboarding/README.md"] = run_readme(pkg, run, values)
    return {"people": {values["owner_id"]: {"name": values["owner_name"], "machines": [], "status": "active"}},
            "systems": {sysd["id"]: {k: v for k, v in sysd.items() if k != "id"}},
            "units": {unit["id"]: {k: v for k, v in unit.items() if k != "id"}},
            "positions": positions, "works": {run["id"]: work}, "files": files}


def package_positions(pkg, values, pkg_dir):
    """(positions, files): a package's roles as registry positions, each with its brief under registry/briefs/; a role
    with no position (the onboarding's champion) gives only its brief, under onboarding/. A package with no run, system
    or unit (bench/capacity) gives just these."""
    values = dict({"language": "English"}, **values)

    def read(rel):
        with open(os.path.join(pkg_dir, rel)) as fh:
            return fill(fh.read(), values)
    org = values["org"]
    positions, files = [], {}
    for r in pkg["roles"]:
        r = fill(r, values)
        if not r.get("position"):  # the champion: named by the charter, added by the structure keeper's diff
            files[f"onboarding/{r['id']}.md"] = read(r["brief"])
            continue
        name = r["position"]
        p = {"name": name, "system": org, "role": r["role"], "kind": r["kind"], "owner": r.get("owner") or name,
             "summary": (r.get("summary") or "")[:SUMMARY_MAX].rstrip(), "status": "active",
             "mandate": f"registry/briefs/{name}.md"}
        for key in ("throughput", "owner_chat", "mandate_functions"):
            if r.get(key) is not None:
                p[key] = r[key]
        if r.get("done_when"):
            p["mandate_exit"] = {"done_when": r["done_when"]}
        if r.get("held_by") == "person":
            p["holder"] = {"runtime": "human", "person": r.get("person") or values["owner_id"]}
            if values.get("owner_evidence"):  # docs/registry.md, People: a person-held claim carries evidence
                p["holder"]["evidence"] = values["owner_evidence"]
        p["package"] = {"name": pkg["package"], "version": pkg["version"], "role": r["id"]}  # docs/registry.md
        p["aliases"] = []
        positions.append(p)
        files[p["mandate"]] = read(r["brief"])
    return positions, files


# -------------------------------------------------------------------- the capacity package (docs/accounts.md B6)
# With two or more Claude accounts on the machine, the head decides once whether agents that stop on a usage limit
# move to an account with room, with one agent of theirs keeping watch ({org}_capacity, bench/capacity). The package
# and capacity.yaml are of_capacity's; this step only asks, adds the position and hands the settings to its writer.
CAPACITY_PACKAGE = os.path.join(os.path.dirname(PACKAGE_DIR), "capacity")
CAPACITY_QUESTION = ("This machine has {n} Claude accounts. When one reaches its usage limit, the agents on it stop "
                     "until it resets. Shall OF move them to an account that still has room, and add one agent of "
                     "yours, {position}, that keeps watch over the accounts and asks you only when your hands or "
                     "money are needed?")
CAPACITY_OPTIONS = ("yes, move them and add {position} (recommended: no agent waits on a spent account while "
                    "another has room)|no, not now: nothing is added, and agents on a spent account wait for it to "
                    "reset; tell your conductor if you change your mind")


def capacity_structure(values, pkg_dir=CAPACITY_PACKAGE):
    """The capacity package's parts for the head: its position and brief, merged like the onboarding's, and the
    settings for capacity.yaml. FileNotFoundError when the package is missing."""
    pkg = load_package(pkg_dir)
    positions, files = package_positions(pkg, values, pkg_dir)
    return {"people": {}, "systems": {}, "units": {}, "works": {}, "positions": positions, "files": files,
            "settings": fill(pkg.get("settings") or {}, values)}


def resolve_capacity(arg=None, yes=False, accounts=0, served=None, ask=None, position="a capacity keeper"):
    """'yes', 'no', or None when nobody here can answer (no terminal, or --yes): the head then gets one card.
    --capacity first; under two accounts, or with a capacity position already named in capacity.yaml, 'no' without a
    question; else one question in a terminal, no by default. ask(prompt) -> answer."""
    if arg:
        if arg not in ("yes", "no"):
            raise ValueError(f"--capacity must be yes or no, not {arg!r}")
        return arg
    if accounts < 2 or served:
        return "no"
    if ask is None and not yes and sys.stdin.isatty():
        ask = input
    if not ask:
        return None
    try:
        answer = ask(f"{CAPACITY_QUESTION} [y/N] ".format(n=accounts, position=position))
    except EOFError:
        answer = ""
    from of_locale import variants  # "yes" in each of OF's languages comes from the locale catalogue
    words = {"y"} | {w.lower() for w in variants("yes") + variants("y")}
    return "yes" if str(answer).strip().lower() in words else "no"


def write_capacity_settings(settings):
    """(written, kept), lists of keys: of_capacity's writer merges the package's settings into capacity.yaml, keeps its
    lines and comments, and never overwrites a key already set (of_capacity.write_defaults, 9b70593)."""
    import of_capacity
    return of_capacity.write_defaults(settings)


def file_capacity_card(n, position, lang=None, of_bin=None, run=subprocess.run):
    """One card to the head, asked as the caller (none in a plain terminal): (filed, output)."""
    from of_locale import tr
    of_bin = of_bin or OF
    try:
        r = run([of_bin, "ask", tr(CAPACITY_QUESTION, lang, n=n, position=position),
                 "--options", tr(CAPACITY_OPTIONS, lang, position=position)],
                capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return r.returncode == 0, ((r.stdout if r.returncode == 0 else r.stderr or r.stdout) or "").strip()


def run_readme(pkg, run, values, by_hand=None):
    """onboarding/README.md: the run as the head and the unit read it, from the package. by_hand: {position: [lines]}
    when the machine has no herdr, so the head starts the conductor and the lead in a terminal."""
    org, Org = values["org"], values["Org"]
    lines = [f"# {Org} onboarding", "",
             f"Written by `of init` from the onboarding package ({pkg['package']} v{pkg['version']}, "
             "`bench/onboarding/` in the organic-framework repository). The practice it follows is organic-framework "
             "`docs/onboarding-practice.md` (\"record\" below); the conductor is `docs/conductor.md`.", "",
             "## Start here", "",
             "No agent here asks you to run a command: you may talk to them through Remote Control or the Claude "
             "app, where a command typed into the chat does not run. They ask in words and run commands themselves. "
             "Only what needs your own hands is yours, described as a step with its place.", "",
             "0. Once, on the machine where OF runs: open Claude Code in this repository, choose \"Yes, I trust "
             "this folder\", accept the bypass-permissions warning, then leave it (`/exit`). In a terminal there: "
             "`claude --dangerously-skip-permissions` in this folder. No agent can answer these for you; without "
             "them `of spawn` stops and files a task for you.",
             f"1. Your conductor is started first, before any other agent, by you or by the agent that installed OF "
             f"(`of spawn {org}_conductor`). It is your one window: tell it what you want, and it finds the agent "
             "that owns it and follows the request to a result.",
             "2. Your conductor first asks how you want the onboarding to run: hands-off (the agents read what they "
             "can reach, decide the steps, show you results and ask only for decisions and access) or controlled "
             "(they ask before each source and step), or in your own words. Then it "
             f"starts the onboarding lead itself (`{org}_onboarding`). The agents first find out themselves who "
             "works on what and which repositories exist (who and what only, no content), then show you one draft "
             "to confirm or correct: the people and their likely roles, and a one-page agreement on what OF reads "
             "(`onboarding/charter.md`). You are never asked to write a list. No content is read before you agree.",
             "3. The other agents of the unit start later, in waves the lead sizes by capacity; the lead starts "
             "them.", "",
             "On a machine with no capacity snapshot yet, `of spawn` asks which Claude Code account to use: add "
             "`--config-dir` with your Claude Code folder (`~/.claude` unless you use another).", ""]
    if by_hand:
        when = {fill(r.get("position") or "", values): fill(r.get("starts") or "", values) for r in pkg["roles"]}
        lines += ["No herdr pane here (no herdr, or the person reaches the agents only through the conductor's "
                  "chat: `surface: remote`, organic-framework `docs/remote.md`). Each agent starts from the two lines "
                  "below, with the session id `of init` reserved for it. The first line declares the brief, so it is "
                  "not counted as your own words; the second starts the agent in a detached session (tmux, where "
                  "messages are delivered, else `screen`). The conductor or the lead runs both lines itself when its "
                  "time comes; the conductor's own lines are run by whoever installs OF, and with `surface: remote` "
                  "it starts with Remote Control on, so the person talks to it from the Claude app. To watch an agent "
                  "on this machine: `tmux attach -t <its name>` (or `screen -r <its name>`).", ""]
        for who, cmds in by_hand.items():
            lines += [f"`{who}`" + (f", {when[who]}" if when.get(who) else "") + ":", "", "```sh"] + cmds + ["```", ""]
    lines += ["## The unit", "", "| Role | Position | Starts | Sources |", "|---|---|---|---|"]
    for r in pkg["roles"]:
        r = fill(r, values)
        pos = f"`{r['position']}`" if r.get("position") else "named by the charter"
        lines.append(f"| {r['title']} ({'a person' if r.get('held_by') == 'person' else 'an agent'}) | {pos} | "
                     f"{r.get('starts') or '—'} | {'; '.join(r.get('sources') or [])} |")
    lines += ["", "Rules of the unit:", ""] + [f"- {x}" for x in pkg.get("rules") or []]
    lines += ["", f"## The run: work `{run['id']}` in the registry", "",
              "A stage is done only when its own evidence exists in the records.", "",
              "| # | Stage | Who | Done when | Sources |", "|---|---|---|---|---|"]
    for s in run["stages"]:
        lines.append(f"| {s['step']} | {s['title']} | {s['who']} | {s['done_when']} | {'; '.join(s['sources'])} |")
    return "\n".join(lines) + "\n"


def merge_structure(doc, built):
    """Add what the package gives to an organization's registry document, keeping everything already there: a
    person, system, unit, position or work that exists is never touched. The package's positions go first, the
    conductor first of all. Returns the names of what was added."""
    added = []
    for key in ("people", "systems", "units", "works"):
        have = doc.get(key) if isinstance(doc.get(key), dict) else {}
        for k, v in built[key].items():
            if k not in have:
                have[k] = v
                added.append(f"{key}: {k}")
        if have:
            doc[key] = have
    existing = doc.get("positions") or []
    names = {p.get("name") for p in existing}
    new = [p for p in built["positions"] if p["name"] not in names]
    added += [p["name"] for p in new]
    doc["positions"] = new + existing
    return added


def write_files(repo, files, out=print, state=None):
    """Write the package's files into the repository; a file that exists (a brief someone edited, a charter being
    filled) is never overwritten. state: {path: sha256} of what of init wrote (package_state), updated here."""
    for rel, text in files.items():
        path = os.path.join(repo, rel)
        if os.path.exists(path):
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)
        if state is not None:
            state[rel] = _sha(text)
        out(f"  + {rel}")


# What of init wrote, by content hash, so a re-run with --refresh can tell the package's untouched files from the
# organization's edits (of_architect's review of PR 3: a commit count errs both ways).
PACKAGE_STATE = "onboarding/.package-state.json"
REFRESH_IGNORE = ("onboarding/.before-refresh-*/", "*.package-new")


def _sha(text):
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


def load_state(repo):
    try:
        with open(os.path.join(repo, PACKAGE_STATE)) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(repo, state):
    path = os.path.join(repo, PACKAGE_STATE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(dict(sorted(state.items())), fh, indent=1)
        fh.write("\n")


def ignore_refresh_copies(repo):
    """Keep --refresh's copies out of the organization's commits: add them to its .gitignore."""
    path = os.path.join(repo, ".gitignore")
    have = open(path).read().splitlines() if os.path.exists(path) else []
    add = [x for x in REFRESH_IGNORE if x not in have]
    if add:
        with open(path, "a") as fh:
            fh.write(("\n" if have and have[-1] else "") + "# of init --refresh: copies for the structure keeper\n"
                     + "\n".join(add) + "\n")


def existing_owner(doc, prefix):
    """(person id, name) of the person holding <prefix>_owner in a registry already written, else None. A re-run of
    of init keeps the organization's owner, whatever --owner says (a different --owner would add a stray person)."""
    p = next((x for x in doc.get("positions") or [] if isinstance(x, dict) and x.get("name") == f"{prefix}_owner"), None)
    pid = ((p or {}).get("holder") or {}).get("person")
    if not pid:
        return None
    return pid, ((doc.get("people") or {}).get(pid) or {}).get("name") or pid


# --refresh: the package files a re-run may bring up to date (the founder, 2026-11-11: a user organization updated OF,
# but its agents kept the old briefs, since of init never overwrites). The working documents (the charter, the role
# design, the re-check) are never touched: they hold the organization's own words.
def refreshable(files):
    return ["AGENTS.md", "onboarding/README.md"] + sorted(r for r in files if r.startswith("registry/briefs/"))


def brief_history(of_repo, brief_rel, values, run=subprocess.run):
    """A function telling whether a text equals this brief as an earlier version of the package wrote it, filled with
    these values: for organizations set up before the package recorded its hashes (0.1.0). It reads the versions of
    bench/onboarding/<brief_rel> in this clone's git history; outside git it knows none."""
    path = f"bench/onboarding/{brief_rel}"
    shas = run(["git", "-C", of_repo, "log", "--format=%H", "--", path], capture_output=True, text=True).stdout.split()

    def known(text):
        for sha in shas:
            r = run(["git", "-C", of_repo, "show", f"{sha}:{path}"], capture_output=True, text=True)
            if r.returncode == 0 and fill(r.stdout, values) == text:
                return True
        return False
    return known


def refresh_files(repo, files, out=print, stamp=None, state=None, history=None):
    """Bring the package's files up to date. A file is the package's, untouched, when its content has the hash of init
    recorded (state), or, with no record, when history(rel, text) knows it as an earlier version of the package; then
    it is copied to onboarding/.before-refresh-<stamp>/ and overwritten, and its record updated. Any other is the
    organization's: kept, with the package's version beside it as <file>.package-new for its structure keeper to
    merge. Returns (refreshed, kept)."""
    stamp = stamp or time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    state = {} if state is None else state
    refreshed, kept = [], []
    for rel in refreshable(files):
        path = os.path.join(repo, rel)
        if rel not in files or not os.path.exists(path):
            continue
        with open(path) as fh:
            old = fh.read()
        if old == files[rel]:
            state[rel] = _sha(old)
            continue
        rec = state.get(rel)
        untouched = rec == _sha(old) if rec else bool(history and history(rel, old))
        if not untouched:
            with open(path + ".package-new", "w") as fh:
                fh.write(files[rel])
            kept.append(rel)
            out(f"  ~ {rel}: edited here, kept; the package's version is beside it ({rel}.package-new)")
            continue
        backup = os.path.join(repo, "onboarding", f".before-refresh-{stamp}", rel)
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        with open(backup, "w") as fh:
            fh.write(old)
        with open(path, "w") as fh:
            fh.write(files[rel])
        state[rel] = _sha(files[rel])
        refreshed.append(rel)
        out(f"  ↻ {rel}")
    return refreshed, kept


def owner_identity(owner=None):
    """(person id, name) of the organization's owner: the name given, else this machine's login name."""
    import getpass
    name = (owner or "").strip() or getpass.getuser()
    pid = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-_")[:32]
    if not pid or not pid[0].isalpha():
        pid = ("p" + pid)[:32]
    return pid, name


def owner_evidence(org, owner, owner_name, day=None):
    """Where the head's holding of the owner's position comes from (docs/registry.md, People: every person-held claim
    carries evidence; the founder, 2026-11-11): the command that named them, verbatim, with its date."""
    import datetime
    day = day or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    if owner:
        return f'of init {org} --owner "{owner}", run on this machine on {day}'
    return f"of init {org} without --owner, run on this machine on {day}: the login name {owner_name}"


def hand_start(doc, conductor, repo, of_cmd="of", agents=None, detach=False, remote_control=False, tmux=None):
    """Without herdr, `of spawn` cannot open a pane: reserve the position's session id in the registry (nothing is
    started) and return the two lines that start it: declare the brief, then run claude in the repository with that
    session id, in bypass mode as of spawn starts it (the guard still stops the critical calls). detach: the second
    line runs in a detached `screen` session, so an agent (the conductor, the lead) starts it itself, with no terminal
    of its own and nothing asked of the person (the founder, 2026-11-11: a head on Remote Control cannot run a
    command). Re-running reuses the reserved id. None when the position is not in doc or already has a holder that
    is not this reservation."""
    import shlex
    import uuid
    import of_spawn
    p = next((x for x in doc.get("positions") or [] if x.get("name") == conductor), None)
    if p is None:
        return None
    h = p.get("holder") or {}
    if h.get("session") and h.get("workplace") != HAND_START:
        return None
    sid = h.get("session") or str(uuid.uuid4())
    p["holder"] = {"runtime": "claude-code", "session": sid, "workplace": HAND_START}
    brief = of_spawn.brief_text(p, repo)
    start = (f"cd {shlex.quote(repo)} && claude --session-id {sid} --name {conductor} --dangerously-skip-permissions"
             + (f" --remote-control {conductor}" if remote_control else "")
             + "".join(f" --{k} {shlex.quote(str(v))}" for k, v in (agents or {}).items()
                       if k in ("model", "effort") and v)
             + f" {shlex.quote(brief)}")
    return [f"{of_cmd} event injected_prompt --injector of_init --session {sid} --position {conductor} "
            f"--text {shlex.quote(brief)}",
            detached(conductor, start, tmux) if detach else start]


HAND_START = "a terminal on the head's machine, started by hand (no herdr); of init reserved this session id"


def detached(name, line, tmux=None):
    """The line run in a detached session named after the position: tmux, where the relay delivers messages
    (docs/remote.md §3 row 5), else `screen` (macOS ships it; nothing is delivered into it). Whoever wants to watch
    the agent attaches on that machine: `tmux attach -t <name>` or `screen -r <name>`."""
    import shlex
    tmux = shutil.which("tmux") if tmux is None else tmux
    if tmux:
        return f"tmux new-session -d -s {name} {shlex.quote(line)}"
    return f"screen -U -dmS {name} sh -c {shlex.quote(line)}"  # -U: UTF-8, so an attached person reads it unbroken

# What a new organization's agents start on (the founder, 2026-11-11 ~17:35Z: "have of set opus xhigh at setup"). The
# alias follows the newest Opus. of init writes it on the organization's entry in orgs.yaml (`agents:`); every session
# OF starts uses it unless a position or the command names another.
AGENT_DEFAULTS = {"model": "opus", "effort": "xhigh"}
EFFORTS = ("low", "medium", "high", "xhigh", "max")


def agent_settings(model=None, effort=None, current=None):
    """The `agents:` entry for orgs.yaml: the organization's current one (else the defaults), with --model and
    --effort over it."""
    if effort and effort not in EFFORTS:
        raise ValueError(f"--effort must be one of {', '.join(EFFORTS)}, not {effort!r}")
    base = dict(AGENT_DEFAULTS, **(current if isinstance(current, dict) else {}))
    return dict(base, **{k: v for k, v in (("model", model), ("effort", effort)) if v})


def start_ready(acct, repo):
    """(trusted, accepted): whether the account trusts the new repository as Claude Code decides it, and whether it
    accepted the Bypass Permissions warning. of spawn stops on either (of_spawn.plan); False when it cannot tell."""
    import of_spawn
    try:
        import of_capacity
        trusted = bool(of_spawn.trusted_with_parents(acct, repo, of_capacity.trusted_key))
    except Exception:  # noqa: BLE001 - an unreadable account file means "not known to be trusted"
        trusted = False
    return trusted, of_spawn.bypass_accepted(acct)


def conductor_start(prefix, repo, acct, ready=(False, False)):
    """The lines that start the conductor with herdr. On a newcomer's machine the account does not trust the new
    repository yet and may not have accepted the Bypass Permissions warning; of spawn stops on both, so open Claude
    Code there once first (of_docs, 2026-11-11; README, Quickstart)."""
    import of_spawn
    lines = []
    if not all(ready):
        lines += ["# once, by the person on this machine (an agent cannot answer it for them; tell them in these words,",
                  f"# never as a command to paste): open Claude Code in {repo}, choose \"Yes, I trust this folder\",",
                  "# accept the bypass-permissions warning, then leave it with /exit. In a terminal here:",
                  of_spawn._claude_line({"config_dir": acct, "cwd": repo}, " --dangerously-skip-permissions")]
    lines.append(f"of spawn {prefix}_conductor --config-dir {acct}   # your one window: start it first")
    return lines


AGENTS_MD = """# {Org}: rules for agents

This repository holds the organization {Org} as an app of Organic Framework: its position registry
(`registry/positions.yaml`), the briefs of its positions (`registry/briefs/`) and its onboarding (`onboarding/`).

- **{owner_name}'s conductor comes first.** `{org}_conductor` is {owner_name}'s one window: agents reach
  {owner_name} through it, and it is the first agent started (organic-framework `docs/conductor.md`).
- **Decisions stay cards.** Ask {owner_name} only with `of ask`, written for a person without your context: the
  decision in one line, why now, the context, the options with their consequences, risks and money cost (never a
  time estimate) and a recommendation, then the evidence. The conductor explains a card and never answers it.
- **No time estimates.** Never tell {owner_name} how long something takes: their hands, preparation, a run or a
  build. A real deadline with its source stays: a legal due date, a booked window, a limit's reset, a session's
  expiry, a meeting they named.
- **Secrets.** Assume no password manager. When your work first needs a secret, tell the conductor, which asks
  {owner_name} once where they keep secrets; nothing mentions it before. A value never passes through a chat.
- **Messages.** When `of status` says messages wait for you, read them with `of inbox --mine` and take them with
  `of inbox --mine --take all`; on a machine without herdr nothing types them into your session.
- **Work that comes up is handled now.** During the onboarding too: when no position covers a request and
  {owner_name} says yes, the structure keeper adds a provisional position and the conductor starts it.
- **Find it, then ask to confirm.** Never ask {owner_name} for what an agent can find (names, roles, who works on
  what, which repositories exist): find it, draft it, and ask them to confirm or correct the draft in one sitting.
  No method term reaches them ("champion", "sponsor", "charter", "value stream", "baseline"); say it plainly.
- **Never ask {owner_name} to run a command.** They may reach you through Remote Control, the desktop or the
  mobile app, where a command typed into the chat does not run. Ask in words ("tell me when you are ready") and
  run commands yourself. What only they can do (log in, trust a folder, approve a key or a payment) is a step in
  plain words with its place (the machine, the app or page, what to choose), filed as a task, never a command to
  paste.
- **Language.** Agents talk to {owner_name} in {language}, whatever language the tools, the records or other agents
  use; they switch only when {owner_name} asks (`of init --language`, kept in `~/.config/of/owner.yaml`).
- **Who does what needs evidence.** A claim that a person holds a position, keeps a system or decides it carries
  `evidence:` beside it: the authorship it rests on, with its window, or an ask id. The people map from the
  repositories comes before any person's position, and {owner_name}'s words go in verbatim, never retold
  (organic-framework `docs/registry.md`, People).
- **What OF reads.** GitHub, the Claude Code sessions people choose to record, and the structure. Never, on an
  agent's own initiative, the head's mail, chat or meetings; a confidential source only with a permission recorded
  by an ask id (`onboarding/charter.md`). A source a person hands an agent with "read this" is read at once: their
  words are the permission, recorded in the charter with the time. If it cannot be opened, the agent says so and
  offers an export, a view link or a connector.
- **Agent names** are `<system>_<role>`, matching `[a-z][a-z0-9_-]{{0,31}}`.
- **Every agent commit** ends with a `Position: <name>` trailer naming the author's position.
- **At the end of every turn**, declare where you are: `of status working | waiting --on owner|<position> | idle | done`.
"""


def init(org, repo=None, paths=(), yes=False, cfg_dir=None, service=True, out=print, owner=None,
         pkg_dir=PACKAGE_DIR, language=None, model=None, effort=None, surface=None, plugins=(), refresh=False,
         capacity=None):
    import yaml
    display = display_name(org)
    org = _name(org)
    prefix = org_prefix(org)
    owner_id, owner_name = owner_identity(owner)
    kept_owner = None
    try:
        with open(os.path.join(os.path.expanduser(repo or f"~/of/{org}"), "registry", "positions.yaml")) as fh:
            kept_owner = existing_owner(yaml.safe_load(fh) or {}, prefix)
    except (OSError, ValueError):
        pass
    if kept_owner and kept_owner[0] != owner_id:
        if owner:
            out(f"  the organization's owner stays {kept_owner[1]} ({kept_owner[0]}), as first written; "
                f"--owner {owner!r} is not used (change the holder of {prefix}_owner by a diff instead)")
        owner_id, owner_name = kept_owner
    listed = next((o for o in (owner_settings(_config_path("orgs.yaml")).get("orgs") or [])
                   if isinstance(o, dict) and o.get("name") == _name(org)), {})
    start_on = agent_settings(model, effort, listed.get("agents"))
    lang, lang_changed = resolve_language(language, yes)
    surface, surface_changed = resolve_surface(surface)
    import of_accounts
    n_acc = len(of_accounts.dirs())
    served = str(owner_settings(_config_path("capacity.yaml")).get("position") or "").strip()
    cap = resolve_capacity(capacity, yes, n_acc, served, position=f"{prefix}_capacity")
    values = {"org": prefix, "Org": display, "owner_id": owner_id, "owner_name": owner_name,
              "owner_evidence": owner_evidence(org, owner, owner_name), "language": language_name(lang)}
    home = os.path.expanduser("~")
    out(f"Organic Framework: init for organization {org!r} (owner: {owner_name}; "
        f"agents talk to them in {language_name(lang)})\n")
    if lang_changed:
        write_language(lang)
        out(f"  language: {lang}, in {_config_path('owner.yaml')} (change it with of init --language)")
    if surface_changed:
        write_owner_setting("surface", surface)
        out(f"  surface: {surface}, in {_config_path('owner.yaml')} (docs/remote.md)")
    out("1. Prerequisites")
    if surface == "remote":
        out("  tmux: " + ("ok" if shutil.which("tmux") else
                          "not found (--surface remote starts every agent in tmux, where messages are delivered: "
                          "brew install tmux on macOS, your package manager on Linux)"))
    if not check_prereqs(out) or (surface == "remote" and not shutil.which("tmux")):
        out("\nFix the missing prerequisites above, then run `of init` again.")
        return 1

    out("\n2. The organization repository")
    repo = os.path.expanduser(repo or f"~/of/{org}")
    reg_dir = os.path.join(repo, "registry")
    os.makedirs(os.path.join(reg_dir, "briefs"), exist_ok=True)
    if not os.path.exists(os.path.join(repo, ".git")) and shutil.which("git"):
        subprocess.run(["git", "init", "-q", repo], check=False)
    agents_md = os.path.join(repo, "AGENTS.md")
    state = load_state(repo)
    if not os.path.exists(agents_md):
        with open(agents_md, "w") as fh:
            fh.write(AGENTS_MD.format(**values))
        state["AGENTS.md"] = _sha(AGENTS_MD.format(**values))
    out(f"  {repo}")

    # docs/conductor.md §5 and the onboarding package: the owner's conductor is the first position, then the unit
    out("\n3. The structure: the owner's conductor first, then the onboarding unit (no agent is started)")
    reg_path = os.path.join(reg_dir, "positions.yaml")
    doc = {}
    if os.path.exists(reg_path):
        with open(reg_path) as fh:
            doc = yaml.safe_load(fh) or {}
    before = copy.deepcopy(doc)
    doc.setdefault("organization", org)
    built = None
    try:
        built = build_structure(load_package(pkg_dir), values, pkg_dir)
    except FileNotFoundError:
        out(f"  the onboarding package is missing ({pkg_dir}): no conductor and no unit written. "
            "Install Organic Framework with its bench/ folder, then run `of init` again.")
    by_hand = None
    cap_built = None
    if built and (cap == "yes" or any(p.get("name") == f"{prefix}_capacity" for p in doc.get("positions") or [])):
        try:  # also on a re-run of an organization that has it, so --refresh brings its brief up to date
            cap_built = capacity_structure(values)
            built["positions"] += cap_built["positions"]
            built["files"].update(cap_built["files"])
        except FileNotFoundError:
            out(f"  the capacity package is missing ({CAPACITY_PACKAGE}): {prefix}_capacity is not added")
    if built:
        for name in merge_structure(doc, built):
            out(f"  + {name}")
        if surface == "remote" or not shutil.which("herdr"):  # no herdr pane: reserve each agent's session
            agents = [p["name"] for p in built["positions"] if (p.get("holder") or {}).get("runtime") != "human"]
            remote, cond = surface == "remote", f"{prefix}_conductor"
            by_hand = {q: c for q in agents for c in [hand_start(
                doc, q, repo, agents=start_on, detach=remote or q != cond, remote_control=remote and q == cond)] if c}
            run = fill(load_package(pkg_dir)["run"], values)
            built["files"]["onboarding/README.md"] = run_readme(load_package(pkg_dir), run, values, by_hand)
        if refresh:
            files = dict(built["files"], **{"AGENTS.md": AGENTS_MD.format(**values)})
            pkg = load_package(pkg_dir)
            briefs = {f"registry/briefs/{fill(r['position'], values)}.md": r["brief"]
                      for r in pkg["roles"] if r.get("position")}
            of_repo = os.path.dirname(os.path.abspath(pkg_dir.rstrip("/")))
            of_repo = os.path.dirname(of_repo) if os.path.basename(of_repo) == "bench" else of_repo
            hist = {rel: brief_history(of_repo, b, values) for rel, b in briefs.items()}
            refreshed, _ = refresh_files(repo, files, out, state=state,
                                         history=lambda rel, text: rel in hist and hist[rel](text))
            ignore_refresh_copies(repo)
            if refreshed:
                out("  running agents keep the brief they started with: their conductor tells each to read it again "
                    "(of send <position> \"your brief changed; read it again\"); the old files are under "
                    "onboarding/.before-refresh-*")
        write_files(repo, built["files"], out, state)
        save_state(repo, state)
        out(f"  {prefix}_conductor is the first position; nothing is running yet.")
        if by_hand:
            why = "surface remote" if surface == "remote" else "no herdr here"
            out(f"  {why}: the unit's {len(by_hand)} agents have session ids reserved, each started in a detached "
                "session by the conductor or the lead (onboarding/README.md)")

    out("\n4. Agents already running (a first look; the structure keeper names them in the onboarding)")
    positions = doc.setdefault("positions", [])
    known = {((p.get("holder") or {}).get("session")) for p in positions}
    added = 0
    taken = {p.get("name") for p in positions}
    agents = live_agents()
    source = "herdr"
    if not agents:  # agents in tmux or plain terminals: Claude Code's own list of live sessions
        agents, source = claude_sessions(), "claude"
    for a in agents:
        sid = (a.get("agent_session") or {}).get("value")
        if not sid or sid in known:
            continue
        name, system, role = propose_name(a, taken)
        taken.add(name)
        where = f"herdr pane {a['pane_id']}" if a.get("pane_id") else f"terminal, {a.get('cwd') or '?'}"
        positions.append({"name": name, "system": system, "role": role, "kind": "project",
                          "owner": f"{prefix}_owner" if built else "owner", "summary": "", "status": "active",
                          "holder": {"runtime": a.get("agent") or "claude-code", "session": sid,
                                     "workplace": where}, "aliases": []})
        known.add(sid)
        added += 1
        out(f"  + {name} ({where})")
    if not added:
        out("  no new live agents found (none running, or all registered already). "
            "Add positions by hand in registry/positions.yaml; see docs/registry.md.")
    elif source == "claude":
        out("  (found through Claude Code, not herdr: names and the desk need herdr; see README)")
    if doc != before:  # an unchanged registry is never rewritten (a hand-edited one keeps its comments)
        first = ("tenant", "organization", "people", "systems", "units", "positions", "works")
        ordered = {k: doc[k] for k in first if k in doc}
        ordered.update({k: v for k, v in doc.items() if k not in ordered})
        with open(reg_path, "w") as fh:
            fh.write("# Position registry (schema: docs/registry.md in the organic-framework repository).\n"
                     "# Written by `of init` from the onboarding package (bench/onboarding); the structure keeper "
                     "keeps it.\n# Run `of registry check` after every change.\n")
            yaml.safe_dump(ordered, fh, sort_keys=False, allow_unicode=True, width=120)

    out("\n5. This machine's list of organizations")
    import of_events
    orgs_path = of_events.config_path("orgs.yaml")
    os.makedirs(os.path.dirname(orgs_path), mode=0o700, exist_ok=True)
    doc = {}
    if os.path.exists(orgs_path):
        with open(orgs_path) as fh:
            doc = yaml.safe_load(fh) or {}
    doc.setdefault("desk", "desk")
    doc.setdefault("unassigned_database", "of_unassigned")
    lst = doc.setdefault("orgs", [])
    if not any(o.get("name") == org for o in lst):
        # the repository itself is the organization's first path: `of spawn` starts its positions there
        own = [repo] + [x for x in (os.path.expanduser(p) for p in paths) if x != repo]
        lst.append({"name": org, "path": repo.replace(home, "~", 1), "workspace": org,
                    "database": "of_" + org.replace("-", "_"),
                    "paths": [p.replace(home, "~", 1) for p in own], "agents": start_on})
    entry = next(o for o in lst if o.get("name") == org)
    if model or effort:  # an organization already listed changes only when asked
        entry["agents"] = start_on
    with open(orgs_path, "w") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False, allow_unicode=True)
    out(f"  {orgs_path}")
    if entry.get("agents"):
        out(f"  its agents start on {entry['agents'].get('model')}, effort {entry['agents'].get('effort')} "
            "(agents: in that file; of init --model/--effort changes it)")

    out("\n6. The recorder hooks (additive; your existing hooks stay)")
    failed = False
    try:
        hooks("install", cfg_dir, yes=yes, out=out)
    except NoTTY as e:
        out(f"  NOT installed: {e}")
        failed = True

    out("\n7. The server (profile 1: this machine, SQLite storage)")
    import of_client
    import of_events
    import of_server
    srv_cfg = of_events.config_path("server.yaml")
    if not os.path.exists(srv_cfg):
        with open(os.open(srv_cfg, os.O_WRONLY | os.O_CREAT, 0o600), "w") as fh:
            fh.write("# of-server plugins (docs/server.md, Profiles)\nplugins:\n  storage: sqlite\n  buffer: disk\n"
                     "  blobs: fs\n  identity: machine_keys\n")
    store = of_server.Store(of_server.load_config()["state_dir"])
    of_server.enroll(store, of_events.machine_id(), of_client.ensure_key(), [o["name"] for o in lst],
                     [o["name"] for o in lst], tenant=True)
    out(f"  this machine is enrolled as the tenant ({of_events.machine_id()})")
    if service:
        out("  " + install_service())
    else:
        out("  service not installed (--no-service); run `of server run` yourself")

    out("\n8. Message delivery and the pulse (the relay and the listener, as services)")
    if service:
        down = install_units(out=out)
        if down:
            failed = True
            out(f"  FAILED: {', '.join(down)} not running. Agents' messages to each other wait undelivered until the "
                "relay runs.")
    else:
        out("  not installed (--no-service): run `of relay run` and `of listen run` yourself, each kept running")

    out("\n9. Claude accounts: agents on a spent one move to one with room (docs/accounts.md)")
    position = f"{prefix}_capacity"
    if cap == "yes" and cap_built:
        try:
            settings = cap_built["settings"]
            written, kept = write_capacity_settings(settings)
            out(f"  + {position}: keeps watch over this machine's {n_acc} Claude account(s); it starts later, like "
                "the other agents")
            if written:
                out(f"  capacity.yaml: {', '.join(f'{k}: {settings[k]}' for k in written)}")
            if kept:
                out(f"  capacity.yaml keeps what was set there: {', '.join(kept)}")
            if "position" in kept and served and served != settings.get("position"):
                out(f"  this machine's accounts stay with {served}: one keeper per person, so {position} has "
                    "nothing to watch here")
            if n_acc < 2:
                out("  one account here: nothing to move until a second is added (of account add)")
        except Exception as e:  # noqa: BLE001 - said plainly; the position is in the registry already
            failed = True
            out(f"  capacity.yaml NOT written: {e}")
    elif cap == "yes":
        out(f"  {position} not added: the capacity package is missing")
    elif served and not capacity:
        out(f"  served by {served} (capacity.yaml position)")
    elif n_acc < 2 and not capacity:
        out(f"  Claude accounts here: {n_acc}; nothing to move between (of account add adds another)")
    elif cap == "no":
        out(f"  not now: nothing added, and no balancer runs, so no agent moves off a spent account. To add it "
            f"later: of init {org} --capacity yes")
    elif kept_owner is None:  # nobody here to answer: one card to the head, on the first run only
        filed, said = file_capacity_card(n_acc, position, lang)
        if filed:
            out(f"  {n_acc} accounts: one card to {owner_name}, relayed by their conductor; on yes it runs "
                f"of init {org} --capacity yes --yes ({said})")
        else:
            out(f"  {n_acc} accounts, and the card was not filed ({said}): ask {owner_name} in words, then "
                f"of init {org} --capacity yes|no")
    else:
        out(f"  {n_acc} accounts, undecided: ask {owner_name} in words, then of init {org} --capacity yes|no")

    out("\n10. Plugins: what this machine has (docs/plugins.md; OF assumes no product)")
    if plugins:
        try:
            chosen = write_plugin_choices(plugins)
            out(f"  chosen: {', '.join(f'{k}: {v}' for k, v in chosen.items())} ({_config_path('plugins.yaml')})")
        except Exception as e:  # noqa: BLE001 - a wrong family or name is the person's to fix, said plainly
            failed = True
            out(f"  NOT written: {e}")
    lines = plugin_proposals()
    for line in lines:
        out(f"  {line}")
    if not lines and not plugins:
        out("  nothing to choose now")

    out("\nDone. Next:")
    out("  of registry check     # validate the registry")
    if by_hand and f"{prefix}_conductor" in by_hand:
        acct = config_dir(cfg_dir)
        for line in conductor_start(prefix, repo, acct, start_ready(acct, repo))[:-1]:  # the trust step, if missing
            out(f"  {line}")  # a detached session would wait on the trust dialog with nobody to answer it
    if by_hand and f"{prefix}_conductor" in by_hand and surface == "remote":
        out(f"  # your one window, {prefix}_conductor, first: whoever installs runs both lines here; it starts in tmux")
        out("  # with Remote Control on. The person then opens it from the Claude app (Remote Control) and talks")
        out("  # to it there; nothing else needs a terminal, except what waits for the machine (docs/remote.md §4):")
        for line in by_hand[f"{prefix}_conductor"]:
            out(f"  {line}")
    elif by_hand and f"{prefix}_conductor" in by_hand:
        out(f"  # your one window, {prefix}_conductor: start it first, before any other agent, in a terminal here")
        out("  # (an installing agent with no terminal runs the second line detached: screen -U -dmS <name> sh -c '<line>'):")
        for line in by_hand[f"{prefix}_conductor"]:
            out(f"  {line}")
        out(f"  # then tell it what you want; the other agents' lines are in {repo}/onboarding/README.md")
    elif built:
        acct = config_dir(cfg_dir)
        for line in conductor_start(prefix, repo, acct, start_ready(acct, repo)):
            out(f"  {line}")
        out(f"                       # then tell it what you want; the onboarding starts there ({repo}/onboarding/README.md)")
    out("  of apply              # names and labels in herdr")
    out("  of who                # your agents, with who waits for you first")
    out("  of asks               # open questions from agents, once sessions run with the hooks")
    return 2 if failed else 0


def install_service():
    py, label = sys.executable, "dev.organic-framework.server"
    state = os.path.expanduser("~/.local/state/of/server")
    os.makedirs(state, mode=0o700, exist_ok=True)
    if platform.system() == "Darwin":
        plist = os.path.expanduser(f"~/Library/LaunchAgents/{label}.plist")
        if os.path.exists(plist):
            return f"service already installed: {plist}"
        with open(plist, "w") as fh:
            fh.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array><string>{py}</string><string>{OF}</string><string>server</string><string>run</string></array>
  <key>EnvironmentVariables</key><dict><key>OF_AUTOMATION</key><string>1</string><key>SSH_AUTH_SOCK</key><string></string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
  <key>Nice</key><integer>10</integer>
  <key>StandardOutPath</key><string>{state}/server.log</string>
  <key>StandardErrorPath</key><string>{state}/server.log</string>
</dict></plist>
""")
        subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", plist], check=False)
        return _started(f"service installed: {plist}")
    unit_dir = os.path.expanduser("~/.config/systemd/user")
    os.makedirs(unit_dir, exist_ok=True)
    unit = os.path.join(unit_dir, "of-server.service")
    with open(unit, "w") as fh:
        fh.write(f"[Unit]\nDescription=Organic Framework sync server\n\n[Service]\nExecStart={py} {OF} server run\n"
                 "Environment=OF_AUTOMATION=1\nEnvironment=SSH_AUTH_SOCK=\nRestart=on-failure\nNice=10\nIOSchedulingClass=idle\n\n"
                 "[Install]\nWantedBy=default.target\n")
    r = subprocess.run(["systemctl", "--user", "enable", "--now", "of-server.service"], capture_output=True, text=True)
    if r.returncode != 0:  # e.g. no systemd user session on a VM: "Failed to connect to bus"
        return (f"service file written ({unit}) but systemd could not start it: "
                f"{(r.stderr or r.stdout).strip()[:120]}. Run `of server start` instead (it detaches and waits for the server).")
    return _started(f"service installed: {unit}")


# The services that carry messages and the pulse (the founder, 2026-11-11 ~17:55Z: on a new organization's machine
# the relay never started, so the conductor's message to the onboarding lead never arrived). Same labels as the
# units `of relay on|off` and `of listen status|install` work with.
UNITS = (
    {"name": "relay", "label": "dev.organic-framework.relay", "unit": "of-relay.service",
     "what": "message delivery between agents (of relay)", "args": ["relay", "run"], "always": False},
    {"name": "listen", "label": "dev.organic-framework.listen", "unit": "of-listen.service",
     "what": "the listener and its 5-minute pulse: hand-over offers, nudges, the asks (of listen)",
     "args": ["listen", "run"], "always": True},
)


def service_path():
    """PATH for a service: the folders of herdr, claude and python3 as this shell finds them, then the usual ones.
    The relay types into panes through herdr; a launchd job gets no shell PATH of its own."""
    dirs = []
    for tool in ("herdr", "claude", "python3"):
        w = shutil.which(tool)
        if w:
            dirs.append(os.path.dirname(w))
    dirs += ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    return ":".join(dict.fromkeys(dirs))


def unit_text(u, system, py=None, of=None, log=None, path=None):
    """The launchd plist (Darwin) or the systemd user unit (else) that runs `of <args>` for a unit of UNITS."""
    py, of, path = py or sys.executable, of or OF, path or service_path()
    if system == "Darwin":
        args = "".join(f"<string>{a}</string>" for a in [py, of] + u["args"])
        keep = "<true/>" if u["always"] else "<dict><key>SuccessfulExit</key><false/></dict>"
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{u["label"]}</string>
  <key>ProgramArguments</key><array>{args}</array>
  <key>EnvironmentVariables</key><dict><key>OF_AUTOMATION</key><string>1</string><key>SSH_AUTH_SOCK</key><string></string>
    <key>PATH</key><string>{path}</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key>{keep}
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Background</string>
  <key>Nice</key><integer>5</integer>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
"""
    return (f"[Unit]\nDescription=Organic Framework: {u['what']}\nAfter=of-server.service\n\n[Service]\n"
            f"ExecStart={py} {of} {' '.join(u['args'])}\nEnvironment=OF_AUTOMATION=1\nEnvironment=SSH_AUTH_SOCK=\n"
            f"Environment=PATH={path}\nRestart={'always' if u['always'] else 'on-failure'}\nRestartSec=10\nNice=5\n\n"
            "[Install]\nWantedBy=default.target\n")


def unit_running(u, system, run=subprocess.run):
    if system == "Darwin":
        r = run(["launchctl", "print", f"gui/{os.getuid()}/{u['label']}"], capture_output=True, text=True)
        return r.returncode == 0 and "state = running" in r.stdout
    r = run(["systemctl", "--user", "is-active", u["unit"]], capture_output=True, text=True)
    return r.stdout.strip() == "active"


def install_units(out=print, system=None, run=subprocess.run, wait_s=8, sleep=time.sleep):
    """Install and start the relay and the listener as user services, then check each runs (twice, a moment apart, so a
    service that dies at start is caught). A unit file already there is kept, and started only when it does not run.
    Returns the names that do not run; init fails loudly on any."""
    import of_events
    system = system or platform.system()
    failed = []
    for u in UNITS:
        log = os.path.join(of_events.STATE_DIR, f"{u['name']}.log")
        if system == "Darwin":
            path = os.path.expanduser(f"~/Library/LaunchAgents/{u['label']}.plist")
            start = [["launchctl", "bootout", f"gui/{os.getuid()}/{u['label']}"],
                     ["launchctl", "bootstrap", f"gui/{os.getuid()}", path]]
        else:
            path = os.path.join(os.path.expanduser("~/.config/systemd/user"), u["unit"])
            start = [["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", u["unit"]]]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new = not os.path.exists(path)
        if new:  # a unit already there (a machine set up earlier, by hand or by of init) is kept as it is
            with open(path, "w") as fh:
                fh.write(unit_text(u, system, log=log))
        if new or not unit_running(u, system, run):
            for cmd in start:
                run(cmd, capture_output=True, text=True)
        ok, deadline = False, time.time() + wait_s
        while time.time() < deadline:
            if unit_running(u, system, run):
                sleep(2)
                ok = unit_running(u, system, run)
                break
            sleep(0.5)
        if ok:
            out(f"  {u['name']}: running ({u['what']}); log {log}")
        else:
            failed.append(u["name"])
            out(f"  {u['name']}: NOT running ({u['what']}). Its unit is {path}; see {log}. "
                f"`of {' '.join(u['args'])}` runs it in the foreground and shows why.")
    return failed


# Families whose choice waits until the work needs it (the founder, 2026-11-11 ~18:56Z: where secrets are kept is
# chosen as late as possible, when the first secret is needed; the conductor asks then).
PLUGINS_LATER = ("secret_stores",)  # of_family's name since b633781; `secrets` is its alias (of_family.canonical)


def _family(name):
    try:
        import of_family
        return of_family.canonical(name)
    except (ImportError, AttributeError):
        return name


def plugin_proposals(propose=None):
    """docs/plugins.md §1: what this machine has, per family without a choice (of_family.propose), less the
    families chosen later (PLUGINS_LATER)."""
    if propose is None:
        try:
            import of_family
            propose = of_family.propose
        except ImportError:
            return []
    return [line for line in propose() if _family(line.split(":", 1)[0].strip()) not in PLUGINS_LATER]


def write_plugin_choices(choices, path=None, check=None):
    """Write --plugin family=name choices to ~/.config/of/plugins.yaml, keeping its other keys. check(family, name)
    raises when the family or the implementation does not exist (of_family.implementation)."""
    import yaml
    path = path or _config_path("plugins.yaml")
    if check is None:
        import of_family
        check = of_family.implementation
    doc = {}
    if os.path.exists(path):
        with open(path) as fh:
            doc = yaml.safe_load(fh) or {}
    for item in choices:
        family, sep, name = item.partition("=")
        family, name = family.strip(), name.strip()
        if not sep or not family or not name:
            raise ValueError(f"--plugin takes family=name, not {item!r}")
        if _family(family) in PLUGINS_LATER:
            fam = _family(family)
            raise ValueError(f"{fam} is the organization's choice, made when it first needs one, on its owner's card, "
                             f"and recorded in its registry as plugins: {fam} with the ask id (docs/registry.md, "
                             "Plugins; docs/agent-secrets.md §13.2); of init does not choose it for this machine")
        check(family, name)
        doc[family] = name
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        yaml.safe_dump(doc, fh, sort_keys=False, allow_unicode=True)
    return doc


def _started(what, url="http://127.0.0.1:18200/v1/health", wait_s=8):
    """Say started only when the server answers (lab-quickstart Q3)."""
    import urllib.error
    import urllib.request
    deadline = time.time() + wait_s
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1).close()
            return f"{what}; of-server is up"
        except urllib.error.HTTPError:
            return f"{what}; of-server is up"
        except Exception:
            time.sleep(0.5)
    return f"{what}, but of-server does not answer on {url}: see its log, or run `of server start`"


GUARD_PROBE = {"hook_event_name": "PreToolUse", "session_id": "of-guard-selftest", "tool_name": "Bash",
               "tool_input": {"command": "printenv"}, "cwd": "/tmp"}


def guard_works(settings, timeout=5):  # Claude Code kills a hook at 5 s (of_server, 2026-11-10): flag what it would
    """Run the installed guard command on a harmless probe (printenv) the way Claude Code would, with an
    empty PATH-independent environment, and check it answers deny. (ok, detail)."""
    import tempfile
    cmds = [h.get("command") for g in (settings.get("hooks") or {}).get("PreToolUse") or []
            if g.get("matcher") == GUARD_MATCHER for h in g.get("hooks") or []]
    if not cmds:
        return None, "no guard installed"
    with tempfile.TemporaryDirectory() as tmp:  # its own state dir: the probe leaves no event behind
        env = {"PATH": "/usr/bin:/bin", "HOME": os.path.expanduser("~"), "OF_STATE": tmp, "OF_AUTOMATION": "1"}
        try:
            r = subprocess.run(cmds[0], shell=True, input=json.dumps(GUARD_PROBE), capture_output=True, text=True,
                               timeout=timeout, env=env)
        except (subprocess.SubprocessError, OSError) as e:
            return False, f"{type(e).__name__}: {e}"
    ok = '"permissionDecision": "deny"' in (r.stdout or "")
    return ok, ("answers deny" if ok else f"exit {r.returncode}: {(r.stderr or r.stdout).strip()[:200]}")
