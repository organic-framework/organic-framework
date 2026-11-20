"""Codex as a position's harness (hand-over 9ee1db9f; organic-framework-build reports/codex-main-harness-plan-2026-11-20.md):
the machine's managed requirements file, which holds the guard and the recorder as managed hooks (W3 of
reports/codex-guard-w2-w3-2026-11-20.md, of_server's decision).

Codex 0.170 runs a hook from the user's or a project's layer only when it is trusted, and `codex exec` skips an
untrusted one silently; hooks in the system requirements file run trusted by policy and no agent can change them.
On Linux and macOS that file is /etc/codex/requirements.toml (codex-rs/config/src/loader/mod.rs at rust-v0.170.0), so it
is written by the machine's owner once, with sudo. It holds every Codex run of every user on the machine, of subagent's
included, so only what holds for all of them is pinned there; the rest goes into each position's own layer
(NOT_PINNED says what and why)."""

import os

REQUIREMENTS_PATH = "/etc/codex/requirements.toml"   # codex-rs/config/src/loader/mod.rs, rust-v0.170.0
# The events OF records and guards; Codex 0.170 has no StopFailure, Notification, PermissionDenied or PostToolUseFailure
MANAGED_HOOK_EVENTS = ("SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop",
                       "PermissionRequest", "PreCompact")
# a PreToolUse hook that times out lets the call through (of_server's W2): the guard gets as long as Claude Code gives it
HOOK_TIMEOUT_S = 30
# the hosts a position's commands may reach (the Claude Code sandbox's of_spawn.SANDBOX_DOMAINS, W3 point 5)
DEFAULT_DOMAINS = ("github.com", "api.github.com", "codeload.github.com", "uploads.github.com",
                   "objects.githubusercontent.com", "raw.githubusercontent.com",
                   "api.openai.com", "chatgpt.com", "auth.openai.com")

NOT_PINNED = (
    ("allowed_web_search_modes", "of subagent's research runs use web_search=live on this machine; each position's "
                                 "layer sets web_search=disabled unless its tool profile names it (W3 point 7)"),
    ("the permission profile (filesystem deny list)", "it is the position's own (its folders, its credentials), so it "
                                                      "goes in the position's layer, in a CODEX_HOME the profile denies "
                                                      "to the session itself (W3 point 4)"),
    ("shell_environment_policy", "not a requirements key at rust-v0.170.0: each position's layer filters the "
                                 "commands' environment to its include_only list"),
    ("the hook's program", "each user's own ~/.local/bin/of: under contours every organization's user runs its own "
                           "OF; a user with none has every tool call refused"),
    ("the owner's own Codex", "this file governs every Codex run of every user on the machine, the owner's too: "
                              "approvals never, OF's hooks on every event, the network to the allowed domains only"),
    ("mcp_servers, apps, plugins", "per position, from of_tools' codex_layer (W7)"),
    ("the model", "per position, in its layer"),
)


def _q(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def requirements_toml(of_bin=None, domains=DEFAULT_DOMAINS):
    """The text of /etc/codex/requirements.toml: managed hooks only, OF's hooks on every event it records (the guard
    on every tool call, no matcher), interactive exec off (write_stdin skips the hook), approvals never, no
    full-access sandbox, the network through Codex's proxy with every Unix socket reachable (of, herdr, the relay) and
    only the allowed domains, the account's browser, computer use, appshots and remote control off."""
    lines = [
        "# Written by `of codex requirements` (Organic Framework); W3 of the Codex positions plan. The machine's owner",
        "# installs it once with sudo; it holds every Codex run of every user here, the owner's own too. Each user's",
        "# hooks run that user's own ~/.local/bin/of; a user with none has every tool call refused.",
        "allow_managed_hooks_only = true",
        'allowed_approval_policies = ["never"]',
        'allowed_sandbox_modes = ["read-only", "workspace-write"]',
        "allow_browser_and_computer_use = false",
        "allow_appshots = false",
        "allow_remote_control = false",
        "",
        "[features]",
        "hooks = true",
        "unified_exec = false",
        "unified_exec_tty = false",
        "network_proxy = true",
        "",
        "[network_proxy]",
        "enabled = true",
        "dangerously_allow_all_unix_sockets = true",
        "managed_allowed_domains_only = true",
        "",
        "[network_proxy.domains]",
    ]
    lines += [f"{_q(d)} = \"allow\"" for d in domains]
    for ev in MANAGED_HOOK_EVENTS:
        lines += ["", f"[[hooks.{ev}]]", "", f"[[hooks.{ev}.hooks]]", 'type = "command"',
                  f"command = {_q(hook_command(ev, of_bin))}", f"timeout = {HOOK_TIMEOUT_S}"]
    return "\n".join(lines) + "\n"


# The events whose hook decides: a failure there refuses the call or the prompt (exit 2 is Codex's deny or block for
# each), where Codex itself would let it through (of_server's W2). The other events only record, and a failure passes.
GUARD_EVENTS = ("PreToolUse", "PermissionRequest", "UserPromptSubmit")
# the prompt hook carries the mention guard, as of init installs Claude Code's (bin/of_init.py hook_command; of_review
# on organic-framework PR 475: allow_managed_hooks_only lets no position add it)
EVENT_FLAGS = {"UserPromptSubmit": " --deny-secrets"}
USER_OF = "$HOME/.local/bin/of"


def hook_command(event, of_bin=None):
    """The shell line Codex runs for EVENT ($SHELL -lc, codex-rs hooks/src/engine/command_runner.rs at rust-v0.170.0).
    It runs the calling user's own of (of_review on organic-framework PR 475: the file holds every user's Codex runs,
    and under contours each organization's user has its own home, mode 0700, and its own OF install; one user's path
    would be a hook other contours cannot start, so their calls would run unguarded). of_bin names one program instead,
    for a test or a machine with one OF install. On a guard event, an of that is missing, cannot start, or ends other than allowing (0) or denying (2)
    refuses the call, with the reason on stderr."""
    import shlex
    if of_bin is not None and not os.path.isabs(of_bin):
        raise ValueError(f"the hooks' program must be an absolute path, not {of_bin!r}: a relative one resolves "
                         "against each session's folder, and a hook that cannot start lets the call through")
    exe = shlex.quote(of_bin) if of_bin else f'"{USER_OF}"'
    shown = of_bin or "~/.local/bin/of"
    run = f'"$o" hook {event}{EVENT_FLAGS.get(event, "")}'
    if event not in GUARD_EVENTS:
        return f'o={exe}; [ -x "$o" ] || exit 0; exec {run}'
    why = ("OF: the guard did not run here ({}), so this is refused. This user's of is "
           + shown.replace("{", "{{").replace("}", "}}") + " (organic-framework docs/guard.md, Codex).")
    return (f'o={exe}; if [ ! -x "$o" ]; then echo {shlex.quote(why.format("no of there"))} >&2; exit 2; fi; '
            f'{run}; r=$?; if [ "$r" -ne 0 ] && [ "$r" -ne 2 ]; then '
            f'echo {shlex.quote(why.format("it ended with status STATUS")).replace("STATUS", "'\"$r\"'")} >&2; '
            f'exit 2; fi; exit "$r"')


def default_of_bin():
    """The `of` the hooks run, by its real path in the checkout: the user's link resolved, else this checkout's. Not the
    link itself: inside an of subagent run's bwrap, where /home is empty, the checkout's bin folder is bound in read-only
    (of_subagent.hook_dirs), and a bound link would find no modules beside it (of_server, msg 01a1f982-178c)."""
    link = os.path.expanduser("~/.local/bin/of")
    real = os.path.realpath(link)
    if os.path.islink(link) and os.path.isfile(os.path.join(os.path.dirname(real), "of_hook.py")):
        return real
    # a plain file there is no checkout: its folder holds the user's other programs, which a run must not get bound
    # in (of_review on organic-framework PR 475); this checkout's of instead
    return os.path.join(os.path.dirname(os.path.realpath(__file__)), "of")


def installed_of_bin(path=REQUIREMENTS_PATH):
    """The one program the installed requirements file names for the guard, or None when it runs each user's own
    (hook_command) or no file is installed."""
    import shlex
    import tomllib
    try:
        with open(path, "rb") as fh:
            cmd = tomllib.load(fh)["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        first = cmd.split(";", 1)[0]
        if first.startswith("o="):
            prog = shlex.split(first[2:])[0]
            return None if "$HOME" in prog else prog
    except Exception:  # noqa: BLE001
        return None
    return None


# ---------- a Codex session's layer: $CODEX_HOME/of-<launch id>.config.toml (W4 of hand-over 9ee1db9f) ----------

# The environment a Codex position's commands get: Codex's inherit "all" then include_only filters it, so only these
# names reach them (W3, of_secrets-review's review via of_server, msg 01a1f8e9-aa9c). No SSH agent, no token, no
# account variable: a command gets nothing it does not need to run of, git and herdr.
ENV_INCLUDE = ("PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_*", "TERM", "TZ", "TMPDIR",
               "HERDR_ENV", "HERDR_PANE_ID", "HERDR_SOCKET_PATH", "OF_POSITION", "OF_ORGS", "OF_STATE",
               "GIT_CEILING_DIRECTORIES")
PROFILE = "of_position"


def seat_homes():
    """Every Codex seat's CODEX_HOME on this machine (of_accounts.codex_seats), ~/.codex, and $CODEX_HOME when set."""
    try:
        import of_accounts
        homes = [os.path.expanduser(str(s.get("home") or s.get("codex_home") or "")) for s in of_accounts.codex_seats()]
    except Exception:  # noqa: BLE001
        homes = []
    homes += [os.path.expanduser("~/.codex"), os.environ.get("CODEX_HOME") or ""]
    return sorted({os.path.abspath(h) for h in homes if h})


def _real(p):
    """A path as the sandbox sees it: resolved when it exists, else as given."""
    return os.path.realpath(p) if os.path.exists(p) else os.path.abspath(p)


def server_socket_dirs(servers_dir=None):
    """The folders of the Unix sockets OF's clients reach their servers by (each ~/.config/of/servers/<host>.socket
    names one)."""
    import glob
    out = []
    for f in glob.glob(os.path.join(servers_dir or os.path.expanduser("~/.config/of/servers"), "*.socket")):
        try:
            with open(f) as fh:
                sock = fh.read().strip()
        except OSError:
            continue
        if sock.startswith("/"):
            out.append(os.path.dirname(sock))
    return sorted(set(out))


def org_repos(orgs_file=None):
    """The repositories this machine's organizations name in orgs.yaml (path:, paths:)."""
    try:
        import yaml
        path = orgs_file or os.environ.get("OF_ORGS") or os.path.expanduser("~/.config/of/orgs.yaml")
        with open(path) as fh:
            doc = yaml.safe_load(fh) or {}
        base = os.path.dirname(os.path.abspath(path))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for o in doc.get("orgs") or []:
        if isinstance(o, dict):
            for p in [o.get("path")] + list(o.get("paths") or []):
                if p:
                    out.append(os.path.join(base, os.path.expanduser(str(p))))
    return out


def _outermost(paths):
    """The denied paths Codex's bwrap can mask: those that exist, and none inside another denied one (tried on the agent
    host, 2026-11-20: a key file inside the denied server folder made bwrap fail, "Can't mkdir parents ... Read-only
    file system", and the session ran no command)."""
    out = []
    for p in sorted({os.path.abspath(x) for x in paths if x and os.path.lexists(x)}):
        if not any(p == q or p.startswith(q.rstrip("/") + "/") for q in out):
            out.append(p)
    return out


# under every folder a session writes: a project layer and project skills a later session that trusts the folder would
# load (W3 as of_server took of_secrets-review's change, organic-framework-build b0040a5 and f78bfbb)
PROTECTED = (".codex", ".agents")
# the guard's inputs in OF's state folder, written by the hook outside the session's sandbox, read only for its
# commands: index.json maps a session to its position, and with it the position's credential rules (of_secrets-review
# on organic-framework PR 475)
GUARD_INPUTS = ("index.json",)


def filesystem(cwd, deny_rules=None, checkout_bin=None, codex_home=None):
    """The position's permission profile's filesystem map (W3 point 4, of_server's decision): the platform's minimal
    files read, its working folder and OF's state folder written, the live checkout's bin/ read only (so no command or
    patch of the session can break its hook: of_server, msg 01a1f989-0322), and denied (read and write) every path the
    Claude Code sandbox denies (of_spawn.sandbox_settings: the catalogue's credential paths, sign-ins, keys,
    transcripts, OF's server and ClickHouse files, Muse's home), every Codex seat's CODEX_HOME, its own included,
    and the Claude temp root, where Claude Code sessions keep their scratch; CODEX_HOME, the session's own seat home,
    is denied whether or not it is a listed seat. Under each folder it writes, .codex and .agents are made and read
    only, and the guard's index is read only. Without of_spawn's deny list it raises: never a map without it."""
    import of_scratch
    import of_spawn
    box = of_spawn.sandbox_settings(deny_rules or [], platform="linux") or {}
    denied = set((box.get("filesystem") or {}).get("denyRead") or [])
    denied |= set(seat_homes())
    if codex_home:
        denied.add(os.path.abspath(os.path.expanduser(codex_home)))
    denied.add(of_scratch.base_dir())
    fs = {":minimal": "read"}
    fs.update({p: "deny" for p in _outermost(denied)})
    state = os.path.expanduser("~/.local/state/of")
    writes = [_real(cwd), state]   # the state folder's server folder, more specific, stays denied
    for w in writes:
        fs[w] = "write"
        for name in PROTECTED:   # made, so the sandbox can bind it read only (bwrap binds only what exists)
            p = os.path.join(w, name)
            if os.path.isdir(w) and not os.path.lexists(p):
                os.mkdir(p, 0o755)
            if os.path.islink(p) or os.path.isfile(p):
                fs[p] = "deny"   # a link or a file there: hidden, never followed
            else:
                fs[p] = "read"
    for name in GUARD_INPUTS:
        p = os.path.join(state, name)
        if os.path.isfile(p) and not os.path.islink(p):
            fs[p] = "read"
    bin_dir = checkout_bin or os.path.dirname(default_of_bin())
    fs[_real(bin_dir)] = "read"
    # what of, herdr and git need to read: OF's configuration (its keys inside stay denied, the more specific path
    # wins), the user's bin folder with the of and herdr links, each organization's repository (its registry and
    # briefs), and the folders of the servers' sockets (~/.config/of/servers/*.socket): without the socket the client
    # falls back to TCP on loopback, which the proxy blocks and which holds ClickHouse (tried on the agent host)
    for p in (os.path.expanduser("~/.config/of"), os.path.expanduser("~/.local/bin")) + tuple(server_socket_dirs()) + \
            tuple(org_repos()):
        if os.path.exists(p) and _real(p) not in fs:
            fs[_real(p)] = "read"
    # Codex's own sandbox on Linux runs the Codex program again inside: its program files must be readable, each one
    # alone (of_subagent.codex_parts; tried on the agent host, 2026-11-20: "bwrap: execvp …/bin/codex: No such file")
    try:
        import of_subagent
        for part in of_subagent.codex_parts(of_subagent.codex_bin()):
            fs[part] = "read"
    except Exception:  # noqa: BLE001 - no Codex on this machine: nothing to read
        pass
    return fs


def layer(position, cwd, model=None, instructions=None, tool_tables=None, deny_rules=None, domains=DEFAULT_DOMAINS,
          checkout_bin=None, codex_home=None):
    """The session layer's tables: the model, approvals never, workspace-write under the position's permission
    profile, its network through the proxy with the allowed domains and every Unix socket, interactive exec off, the
    command environment filtered to ENV_INCLUDE, the start context whole as developer_instructions (W5), and of_tools'
    tool tables (features, web_search, mcp_servers, apps, plugins; W7) merged in, W3's features last so no tool table
    turns them back on."""
    tables = dict(tool_tables or {})
    feats = dict(tables.pop("features", None) or {})
    feats.update({"hooks": True, "unified_exec": False, "unified_exec_tty": False, "network_proxy": True,
                  "multi_agent_v2": False, "memories": False})
    doc = {}
    if model:
        doc["model"] = model
    doc.update({"approval_policy": "never", "sandbox_mode": "workspace-write", "default_permissions": PROFILE,
                "project_doc_max_bytes": 0})
    if instructions:
        doc["developer_instructions"] = instructions
    doc["web_search"] = tables.pop("web_search", "disabled")
    doc["features"] = feats
    doc["shell_environment_policy"] = {"inherit": "all", "include_only": list(ENV_INCLUDE)}
    doc["permissions"] = {PROFILE: {
        "filesystem": filesystem(cwd, deny_rules, checkout_bin, codex_home or os.environ.get("CODEX_HOME")),
        "network": {"enabled": True, "dangerously_allow_all_unix_sockets": True,
                    "domains": {d: "allow" for d in domains}}}}
    doc.update(tables)
    return doc


def _key(k):
    import re
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else _q(k)


def _val(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        if "\n" in v:
            return '"""' + v.replace("\\", "\\\\").replace('"""', '\\"\\"\\"') + '"""'
        return _q(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_val(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{_key(k)} = {_val(x)}" for k, x in v.items()) + "}"
    raise ValueError(f"no TOML for {type(v).__name__}")


def to_toml(doc, prefix=()):
    """A small TOML writer for the layer: plain values first, then each table under its dotted, quoted name."""
    plain = [(k, v) for k, v in doc.items() if not isinstance(v, dict)]
    tables = [(k, v) for k, v in doc.items() if isinstance(v, dict)]
    out = []
    if prefix and (plain or not tables):
        out.append("[" + ".".join(_key(k) for k in prefix) + "]")
    out += [f"{_key(k)} = {_val(v)}" for k, v in plain]
    for k, v in tables:
        sub = to_toml(v, prefix + (k,))
        if sub:
            out += ([""] if out else []) + [sub]
    return "\n".join(out)


def layer_path(codex_home, launch_id):
    return os.path.join(codex_home, f"of-{launch_id}.config.toml")


def write_layer(codex_home, launch_id, doc):
    """Write the layer, mode 600, whole or not at all, through a new file of its own in the same folder (mkstemp: never a
    link someone left at a guessable name; of_secrets-review on organic-framework PR 475)."""
    import tempfile
    path = layer_path(codex_home, launch_id)
    fd, tmp = tempfile.mkstemp(prefix=".of-layer-", dir=codex_home)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(to_toml(doc) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path
